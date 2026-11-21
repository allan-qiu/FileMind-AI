import { useEffect, useRef, useState } from "react";
import { ApiClient, errorMessage } from "./api";
import { Card, Empty } from "./components";
import { bytes, when } from "./types";
import { Detail, LibraryFile } from "./library";
import { ShareLink, TemporaryObject, temporaryUpload } from "./share";
import "./share.css";

export function ShareCreate({
  api,
  versionId,
  onCreated,
}: {
  api: ApiClient;
  versionId?: string;
  onCreated?: (link: ShareLink) => void;
}) {
  const [source, setSource] = useState(versionId ? "library" : "temporary");
  const [files, setFiles] = useState<LibraryFile[]>([]),
    [versions, setVersions] = useState<Detail["versions"]>([]);
  const [objects, setObjects] = useState<TemporaryObject[]>([]);
  const [vid, setVid] = useState(versionId || ""),
    [oid, setOid] = useState("");
  const [local, setLocal] = useState<File | null>(null),
    [maximum, setMaximum] = useState(3),
    [ttl, setTtl] = useState(86400);
  const [password, setPassword] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [url, setUrl] = useState(""),
    [notice, setNotice] = useState("");
  const [progress, setProgress] = useState(0),
    [query, setQuery] = useState("");
  const abort = useRef<AbortController | null>(null);
  useEffect(() => () => abort.current?.abort(), []);
  useEffect(() => {
    void api
      .request<TemporaryObject[]>("/shares/temporary/objects")
      .then(setObjects)
      .catch((e) => setError(errorMessage(e)));
  }, [api, url]);
  async function search() {
    try {
      setFiles(
        (
          await api.request<{ items: LibraryFile[] }>(
            "/library/search?q=" + encodeURIComponent(query),
          )
        ).items,
      );
    } catch (e) {
      setError(errorMessage(e));
    }
  }
  async function choose(fid: string) {
    try {
      const d = await api.request<Detail>("/library/" + fid);
      setVersions(d.versions);
      setVid(d.versions[0]?.id || "");
    } catch (e) {
      setError(errorMessage(e));
    }
  }
  async function create() {
    if (busy) return;
    setBusy(true);
    setError("");
    setUrl("");
    setNotice("");
    abort.current = new AbortController();
    try {
      let object = oid;
      if (source === "temporary" && local) {
        object = (
          await temporaryUpload(api, local, abort.current.signal, setProgress)
        ).object_id;
        setOid(object);
        setLocal(null);
      }
      if (!(source === "library" ? vid : object))
        throw new Error("请选择文件来源或上传文件");
      const link = await api.request<ShareLink>("/shares", "POST", {
        ...(source === "library"
          ? { file_version_id: vid }
          : { temporary_object_id: object }),
        max_downloads: maximum,
        ttl_seconds: ttl,
        password: password || null,
      });
      setPassword("");
      setUrl(location.origin + link.share_url);
      onCreated?.(link);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Card title="创建分享">
      <p>一次完整服务端交付计一次。每条链接独立设置，固定引用所选版本。</p>
      <fieldset disabled={busy} className="share-form">
        <label>
          文件来源
          <select value={source} onChange={(e) => setSource(e.target.value)}>
            <option value="temporary">临时文件</option>
            <option value="library">全局文件库指定版本</option>
          </select>
        </label>
        {source === "temporary" ? (
          <>
            <label>
              选择本地文件（点击创建后上传）
              <input
                type="file"
                onChange={(e) => {
                  setLocal(e.target.files?.[0] || null);
                  setOid("");
                }}
              />
            </label>
            <label>
              或复用已有临时文件
              <select
                value={oid}
                onChange={(e) => {
                  setOid(e.target.value);
                  setLocal(null);
                }}
              >
                <option value="">请选择</option>
                {objects
                  .filter((o) => o.deletion_status === "LIVE")
                  .map((o) => (
                    <option key={o.object_id} value={o.object_id}>
                      {o.original_filename} · {bytes(o.file_size)}
                    </option>
                  ))}
              </select>
            </label>
          </>
        ) : (
          <>
            {versionId ? (
              <p>已选择版本：{versionId}</p>
            ) : (
              <>
                <label>
                  查找文件
                  <input
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                  />
                </label>
                <button type="button" onClick={() => void search()}>
                  查询文件库
                </button>
                <label>
                  文件
                  <select
                    defaultValue=""
                    onChange={(e) => void choose(e.target.value)}
                  >
                    <option value="">请选择</option>
                    {files.map((f) => (
                      <option key={f.file_id} value={f.file_id}>
                        {f.filename}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  固定版本
                  <select value={vid} onChange={(e) => setVid(e.target.value)}>
                    <option value="">请选择</option>
                    {versions.map((v) => (
                      <option key={v.id} value={v.id}>
                        Version {v.version} · {v.filename}
                      </option>
                    ))}
                  </select>
                </label>
              </>
            )}
          </>
        )}
        <label>
          最多下载次数
          <input
            type="number"
            list="share-quota-presets"
            min={1}
            max={10000}
            value={maximum}
            onChange={(e) => setMaximum(Number(e.target.value))}
          />
          <datalist id="share-quota-presets">
            {[1, 3, 5, 10, 20].map((n) => (
              <option key={n} value={n} />
            ))}
          </datalist>
        </label>
        <label>
          有效期
          <select
            value={
              [3600, 86400, 604800, 2592000].includes(ttl) ? ttl : "custom"
            }
            onChange={(e) =>
              setTtl(
                e.target.value === "custom" ? 7200 : Number(e.target.value),
              )
            }
          >
            <option value={3600}>1 小时</option>
            <option value={86400}>24 小时</option>
            <option value={604800}>7 天</option>
            <option value={2592000}>30 天</option>
            <option value="custom">自定义</option>
          </select>
        </label>
        <label>
          有效期秒数
          <input
            type="number"
            min={60}
            max={2592000}
            value={ttl}
            onChange={(e) => setTtl(Number(e.target.value))}
          />
        </label>
        <label>
          访问密码（可选）
          <input
            type="password"
            autoComplete="new-password"
            maxLength={128}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        <p>
          预计规则：最多 {maximum} 次，{Math.round((ttl / 3600) * 10) / 10}{" "}
          小时，{password ? "需要访问密码" : "无需密码"}
          。系统限制以服务端校验为准。
        </p>
        <button type="button" className="primary" onClick={() => void create()}>
          创建分享链接
        </button>
      </fieldset>
      {busy && (
        <>
          <p role="status">正在校验、上传或创建… 已上传 {bytes(progress)}</p>
          <button onClick={() => abort.current?.abort()}>取消上传</button>
        </>
      )}
      {error && (
        <p role="alert" className="feedback error">
          {error}
        </p>
      )}
      {url && (
        <div className="share-result">
          <label>
            新分享链接（仅此时显示，请保存）
            <input readOnly value={url} />
          </label>
          <button
            onClick={() =>
              void navigator.clipboard
                .writeText(url)
                .then(() => setNotice("链接已复制"))
                .catch((e) => setError(errorMessage(e)))
            }
          >
            复制分享链接
          </button>
        </div>
      )}
      {notice && <p role="status">{notice}</p>}
    </Card>
  );
}

export function ShareCenter({
  api,
  admin = false,
}: {
  api: ApiClient;
  admin?: boolean;
}) {
  const [rows, setRows] = useState<ShareLink[]>([]),
    [q, setQ] = useState(""),
    [status, setStatus] = useState(""),
    [owner, setOwner] = useState("");
  const [error, setError] = useState(""),
    [history, setHistory] = useState<unknown>(null),
    [busy, setBusy] = useState(false),
    [editing, setEditing] = useState<ShareLink | null>(null);
  const [limit, setLimit] = useState(3),
    [ttl, setTtl] = useState(86400),
    [password, setPassword] = useState(""),
    [changePassword, setChangePassword] = useState(false),
    [changeExpiry, setChangeExpiry] = useState(false);
  const [offset, setOffset] = useState(0);
  const [after, setAfter] = useState(""),
    [before, setBefore] = useState("");
  async function load(pageOffset = offset) {
    try {
      setRows(
        await api.request<ShareLink[]>(
          `/shares?q=${encodeURIComponent(q)}&status=${status}&owner=${encodeURIComponent(admin ? owner : "")}&offset=${pageOffset}${after ? "&after=" + new Date(after).getTime() / 1000 : ""}${before ? "&before=" + new Date(before).getTime() / 1000 : ""}`,
        ),
      );
    } catch (e) {
      setError(errorMessage(e));
    }
  }
  useEffect(() => {
    void load();
  }, [api, status, offset]);
  async function run(action: () => Promise<unknown>) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await action();
      await load();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="share-stack">
      {!admin && <ShareCreate api={api} onCreated={() => void load()} />}
      <Card title={admin ? "分享管理中心" : "我的分享"}>
        <div className="share-filters">
          <label>
            创建时间起
            <input
              type="datetime-local"
              value={after}
              onChange={(e) => setAfter(e.target.value)}
            />
          </label>
          <label>
            创建时间止
            <input
              type="datetime-local"
              value={before}
              onChange={(e) => setBefore(e.target.value)}
            />
          </label>
          <label>
            文件名称
            <input value={q} onChange={(e) => setQ(e.target.value)} />
          </label>
          {admin && (
            <label>
              用户 ID
              <input value={owner} onChange={(e) => setOwner(e.target.value)} />
            </label>
          )}
          <label>
            状态
            <select
              value={status}
              onChange={(e) => {
                setStatus(e.target.value);
                setOffset(0);
              }}
            >
              <option value="">全部</option>
              <option value="ACTIVE">有效分享</option>
              <option value="EXPIRED">已过期</option>
              <option value="REVOKED">已撤销</option>
            </select>
          </label>
          <button disabled={busy} onClick={() => void load()}>
            查询 / 刷新
          </button>
        </div>
        {rows.length === 0 ? (
          <Empty text="暂无分享。创建的每条独立链接将在此显示。" />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  {[
                    "文件 / Share ID",
                    "创建 / 到期",
                    "上限 / 完成 / 预留 / 剩余",
                    "状态",
                    "操作",
                  ].map((x) => (
                    <th key={x}>{x}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.share_id}>
                    <td>
                      {r.display_name}
                      <small className="share-id">{r.share_id}</small>
                      {r.size_bytes !== null
                        ? bytes(r.size_bytes)
                        : "内容不可用"}
                    </td>
                    <td>
                      {when(r.created_at)}
                      <br />
                      {when(r.expires_at)}
                    </td>
                    <td>
                      {r.max_downloads} / {r.completed_downloads} /{" "}
                      {r.reserved_downloads} / {r.remaining_downloads}
                    </td>
                    <td>{r.status}</td>
                    <td>
                      <div className="actions">
                        <button
                          disabled={busy}
                          onClick={() =>
                            void run(async () =>
                              setHistory(
                                await api.request(
                                  `/shares/${r.share_id}/downloads`,
                                ),
                              ),
                            )
                          }
                        >
                          下载记录
                        </button>
                        {!admin && r.status === "ACTIVE" && (
                          <button
                            onClick={() => {
                              setEditing(r);
                              setLimit(r.max_downloads);
                              setPassword("");
                              setChangePassword(false);
                              setChangeExpiry(false);
                            }}
                          >
                            修改配置
                          </button>
                        )}
                        <button
                          disabled={busy || r.status !== "ACTIVE"}
                          onClick={() =>
                            void run(() =>
                              api.request(
                                `/shares/${r.share_id}/revoke`,
                                "POST",
                              ),
                            )
                          }
                        >
                          撤销链接
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="actions">
          <button
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - 200))}
          >
            上一页
          </button>
          <button
            disabled={rows.length < 200}
            onClick={() => setOffset(offset + 200)}
          >
            下一页
          </button>
        </div>
        {editing && (
          <section className="share-form" aria-label="修改分享配置">
            <h3>{editing.display_name}</h3>
            <label>
              新下载上限
              <input
                type="number"
                min={1}
                value={limit}
                onChange={(e) => setLimit(Number(e.target.value))}
              />
            </label>
            <label>
              <input
                type="checkbox"
                checked={changeExpiry}
                onChange={(e) => setChangeExpiry(e.target.checked)}
              />
              更新有效期
            </label>
            {changeExpiry && (
              <label>
                从现在起有效秒数
                <input
                  type="number"
                  min={60}
                  value={ttl}
                  onChange={(e) => setTtl(Number(e.target.value))}
                />
              </label>
            )}
            <label>
              <input
                type="checkbox"
                checked={changePassword}
                onChange={(e) => setChangePassword(e.target.checked)}
              />
              更新密码（留空移除）
            </label>
            {changePassword && (
              <label>
                新访问密码
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </label>
            )}
            <button
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  await api.request(`/shares/${editing.share_id}`, "PATCH", {
                    max_downloads: limit,
                    ...(changeExpiry ? { ttl_seconds: ttl } : {}),
                    ...(changePassword ? { password: password || null } : {}),
                  });
                  setEditing(null);
                })
              }
            >
              保存分享配置
            </button>
            <button onClick={() => setEditing(null)}>取消修改</button>
          </section>
        )}
      </Card>
      {error && (
        <p role="alert" className="feedback error">
          {error}
        </p>
      )}
      {history !== null && (
        <Card title="下载历史与访问记录">
          <button onClick={() => setHistory(null)}>关闭记录</button>
          <pre className="share-history">
            {JSON.stringify(history, null, 2)}
          </pre>
        </Card>
      )}
    </div>
  );
}
