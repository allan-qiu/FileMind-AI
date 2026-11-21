import { useEffect, useRef, useState } from "react";
import { sha256 } from "@noble/hashes/sha2.js";
import { ApiClient, ApiError, errorMessage } from "./api";
import { bytes, when } from "./types";
import { hex, secret } from "./share";
import "./share.css";

type Info = {
  password_required: boolean;
  grant?: string;
  display_name?: string;
  size_bytes?: number;
  sha256?: string;
  expires_at?: number;
  remaining_downloads?: number;
  delivery_semantics?: string;
};
type Session = {
  session_id: string;
  status: string;
  transferred_bytes: number;
  expected_bytes: number;
};
export function PublicShare() {
  const [token, setToken] = useState(() => location.hash.slice(1));
  const [info, setInfo] = useState<Info | null>(null),
    [password, setPassword] = useState(""),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [busy, setBusy] = useState(false),
    [progress, setProgress] = useState(0);
  const [dark, setDark] = useState(
    () => matchMedia("(prefers-color-scheme: dark)").matches,
  );
  const current = useRef<{ id: string; secret: string } | null>(null),
    controller = useRef<AbortController | null>(null),
    poller = useRef<ReturnType<typeof setInterval> | null>(null);
  const api = useRef(new ApiClient()).current;
  const activeToken = useRef(token);
  useEffect(() => {
    activeToken.current = token;
    document.documentElement.dataset.theme = dark ? "dark" : "light";
  }, [dark]);
  useEffect(() => {
    setInfo(null);
    setError("");
    setNotice("");
    setProgress(0);
    setBusy(false);
    void verify();
  }, [token]);
  useEffect(() => {
    const changed = () => {
      controller.current?.abort();
      if (poller.current) clearInterval(poller.current);
      const old = current.current;
      current.current = null;
      if (old)
        void fetch(`/api/public/downloads/${old.id}/cancel`, {
          method: "POST",
          headers: { Authorization: "Bearer " + old.secret },
        }).catch(() => {});
      activeToken.current = location.hash.slice(1);
      setPassword("");
      setToken(activeToken.current);
    };
    window.addEventListener("hashchange", changed);
    return () => {
      window.removeEventListener("hashchange", changed);
      controller.current?.abort();
      if (poller.current) clearInterval(poller.current);
    };
  }, []);
  async function verify(supplied?: string) {
    setError("");
    try {
      const value = await api.request<Info>("/public/shares/verify", "POST", {
        token,
        ...(supplied !== undefined ? { password: supplied } : {}),
      });
      if (activeToken.current !== token) return;
      setInfo(value);
    } catch (e) {
      if (activeToken.current !== token) return;
      setInfo((current) =>
        current?.password_required ||
        (e instanceof ApiError && e.status === 429)
          ? current
          : null,
      );
      setError(errorMessage(e));
    }
  }
  async function state() {
    const c = current.current;
    if (!c) return null;
    const r = await fetch(`/api/public/downloads/${c.id}`, {
      headers: { Authorization: "Bearer " + c.secret },
      cache: "no-store",
    });
    if (!r.ok) throw new Error((await r.json()).detail || "会话已结束");
    return (await r.json()) as Session;
  }
  async function start(native = false) {
    if (busy || !info?.grant) return;
    setBusy(true);
    setError("");
    setNotice("");
    setProgress(0);
    controller.current = new AbortController();
    try {
      const credential = secret();
      const session = await api.request<Session>(
        "/public/shares/downloads",
        "POST",
        {
          grant: info.grant,
          request_key: crypto.randomUUID(),
          session_secret: credential,
        },
      );
      current.current = { id: session.session_id, secret: credential };
      if (native) {
        const frame = document.createElement("iframe");
        frame.hidden = true;
        frame.name = "filemind-save-" + crypto.randomUUID();
        document.body.append(frame);
        const form = document.createElement("form");
        form.method = "POST";
        form.action = `/api/public/downloads/${session.session_id}/content`;
        form.target = frame.name;
        const input = document.createElement("input");
        input.type = "hidden";
        input.name = "session_secret";
        input.value = credential;
        form.append(input);
        document.body.append(form);
        form.submit();
        form.remove();
        setNotice(
          "浏览器正在接收文件。下方进度为服务端交付进度；保存结果由浏览器管理。",
        );
        poller.current = setInterval(() => {
          void state()
            .then((s) => {
              if (!s) return;
              setProgress(s.transferred_bytes);
              if (s.status === "COMPLETED") {
                if (poller.current) clearInterval(poller.current);
                setBusy(false);
                setNotice(
                  "服务端完整交付已计一次；请在浏览器下载任务中确认保存结果。",
                );
                void verify(password || undefined);
                frame.remove();
              } else if (
                ["FAILED", "EXPIRED", "CANCELLED"].includes(s.status)
              ) {
                throw new Error("下载已结束：" + s.status);
              }
            })
            .catch((e) => {
              if (poller.current) clearInterval(poller.current);
              setError(errorMessage(e));
              setBusy(false);
              frame.remove();
            });
        }, 1000);
        return;
      }
      if ((info.size_bytes || 0) > 32 * 1024 ** 2)
        throw new Error("超过页面校验模式 32 MiB 上限，请使用流式保存");
      const r = await fetch(
        `/api/public/downloads/${session.session_id}/content`,
        {
          headers: { Authorization: "Bearer " + credential },
          signal: controller.current.signal,
        },
      );
      if (!r.ok || !r.body)
        throw new Error((await r.json()).detail || "下载失败");
      const reader = r.body.getReader(),
        hash = sha256.create(),
        parts: BlobPart[] = [];
      let done = 0;
      while (true) {
        const block = await reader.read();
        if (block.done) break;
        hash.update(block.value);
        parts.push(block.value.slice().buffer);
        done += block.value.length;
        if (done > 32 * 1024 ** 2) throw new Error("页面下载内存上限");
        setProgress(done);
      }
      const digest = hex(hash.digest());
      if (done !== info.size_bytes || digest !== info.sha256)
        throw new Error("文件大小或 SHA-256 不一致");
      // The ASGI completion transaction can trail receipt of the last network byte briefly.
      let s = await state();
      for (let i = 0; i < 20 && s?.status !== "COMPLETED"; i++) {
        await new Promise((resolve) => setTimeout(resolve, 100));
        s = await state();
      }
      const ack = await fetch(
        `/api/public/downloads/${session.session_id}/complete`,
        {
          method: "POST",
          headers: {
            Authorization: "Bearer " + credential,
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ sha256: digest }),
        },
      );
      if (!ack.ok) throw new Error("交付记录确认失败，请查询会话状态");
      const url = URL.createObjectURL(new Blob(parts)),
        a = document.createElement("a");
      a.href = url;
      a.download = info.display_name || "download";
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 30000);
      setNotice(
        "SHA-256 校验一致，服务端已计一次，已请求浏览器保存。保存到磁盘的结果无法由页面证明。",
      );
      current.current = null;
      setBusy(false);
      await verify(password || undefined);
    } catch (e) {
      controller.current?.abort();
      const failed = current.current;
      current.current = null;
      if (failed)
        await fetch(`/api/public/downloads/${failed.id}/cancel`, {
          method: "POST",
          headers: { Authorization: "Bearer " + failed.secret },
        }).catch(() => {});
      setError(errorMessage(e));
      setBusy(false);
    }
  }
  async function cancel() {
    controller.current?.abort();
    if (poller.current) clearInterval(poller.current);
    const c = current.current;
    if (c) {
      await fetch(`/api/public/downloads/${c.id}/cancel`, {
        method: "POST",
        headers: { Authorization: "Bearer " + c.secret },
      }).catch(() => {});
    }
    current.current = null;
    setBusy(false);
    setNotice("已请求取消；已完成交付的记录不会撤销。");
  }
  return (
    <main className="public-share">
      <div className="row">
        <strong>FileMind Share</strong>
        <button onClick={() => setDark(!dark)}>
          切换{dark ? "浅色" : "深色"}
        </button>
      </div>
      <h1>与你分享的文件</h1>
      <p className="muted">
        无需注册即可下载。链接的有效期与下载名额由服务器确认。
      </p>
      {error && (
        <div role="alert" className="feedback error">
          {error}
        </div>
      )}
      {info?.password_required && (
        <form
          className="card public-password"
          onSubmit={(e) => {
            e.preventDefault();
            void verify(password);
          }}
        >
          <label>
            访问密码
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </label>
          <button className="primary">验证访问密码</button>
        </form>
      )}
      {info?.grant && (
        <section className="card">
          <h2 className="share-file-title">{info.display_name}</h2>
          <dl>
            <dt>文件类型</dt>
            <dd>
              {info.display_name?.split(".").pop()?.toUpperCase() || "文件"}
            </dd>
            <dt>文件大小</dt>
            <dd>{bytes(info.size_bytes || 0)}</dd>
            <dt>有效期至</dt>
            <dd>{when(info.expires_at || 0)}</dd>
            <dt>剩余下载名额</dt>
            <dd>{info.remaining_downloads}</dd>
          </dl>
          <div className="actions">
            {(info.size_bytes || 0) <= 32 * 1024 ** 2 && (
              <button
                className="primary"
                disabled={busy || !info.remaining_downloads}
                onClick={() => void start()}
              >
                校验并下载
              </button>
            )}
            <button
              disabled={busy || !info.remaining_downloads}
              onClick={() => void start(true)}
            >
              流式保存到浏览器
            </button>
            <button
              disabled={busy}
              onClick={() => void verify(password || undefined)}
            >
              刷新名额
            </button>
            {busy && (
              <button onClick={() => void cancel()}>取消下载会话</button>
            )}
          </div>
          {!info.remaining_downloads && (
            <p role="status">下载名额已耗尽或正在被占用。</p>
          )}
          <progress
            value={progress}
            max={info.size_bytes || 1}
            aria-label="服务端交付进度"
          />
          <p role="status">
            {bytes(progress)} / {bytes(info.size_bytes || 0)} · {notice}
          </p>
          <p className="muted">{info.delivery_semantics}</p>
        </section>
      )}
      {!info && !error && <p role="status">正在验证分享…</p>}
    </main>
  );
}
