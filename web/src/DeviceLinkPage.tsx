import { useEffect, useRef, useState } from "react";
import { ArrowRightLeft, RefreshCw, Link2 } from "lucide-react";
import { ApiClient, errorMessage } from "./api";
import { Card, Empty, Status } from "./components";
import {
  archiveRequest,
  uploadArchiveFile,
  type ArchiveJob,
  type Preferences,
} from "./archive";
import {
  deviceApi,
  sendFile,
  receiveFile,
  byteText,
  stateNames,
  terminal,
  type Peer,
  type Transfer,
} from "./devicelink";

type Binding = { id: string; token: string };
type Received = {
  id: string;
  filename: string;
  blob: Blob;
  sha256: string;
  confirmed?: boolean;
};

export function DeviceLinkPage({
  api,
  binding,
  bind,
}: {
  api: ApiClient;
  binding: Binding | null;
  bind: (value: Binding) => void;
}) {
  const [deviceName, setDeviceName] = useState("我的浏览器");
  const [peers, setPeers] = useState<Peer[]>([]),
    [tasks, setTasks] = useState<Transfer[]>([]);
  const [receiver, setReceiver] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const [progress, setProgress] = useState({ done: 0, total: 0, speed: 0 });
  const [received, setReceived] = useState<Received | null>(null),
    [download, setDownload] = useState("");
  const active = useRef<string | null>(null),
    abort = useRef<AbortController | null>(null);
  const lastSample = useRef({ stamp: 0, done: 0 });
  const archiveSource = useRef<{ id: string; file: File } | null>(null);
  const archiving = useRef(false),
    archiveAbort = useRef<AbortController | null>(null);
  const provideArchive = async () => {
    if (!binding || !archiveSource.current || archiving.current) return;
    archiving.current = true;
    try {
      const prefs = await archiveRequest<Preferences>(
        api,
        "/archive/preferences",
        "GET",
        undefined,
        binding.token,
      );
      if (!prefs.auto_archive) {
        archiveSource.current = null;
        return;
      }
      const jobs = await archiveRequest<ArchiveJob[]>(
        api,
        "/archive/jobs",
        "GET",
        undefined,
        binding.token,
      );
      const source = archiveSource.current;
      if (!source) return;
      const job = jobs.find((j) => j.source_transfer_id === source.id);
      if (!job) return;
      if (
        !["WAITING_SOURCE", "WAITING_UPLOAD", "UPLOADING"].includes(job.state)
      ) {
        archiveSource.current = null;
        return;
      }
      if (
        job.lease_expires_at &&
        job.lease_expires_at > Date.now() / 1000 &&
        job.upload_owner !== binding.id
      )
        return;
      if (
        job.backup_owner === binding.id &&
        Date.now() / 1000 < job.created_at + 30
      )
        return;
      archiveAbort.current = new AbortController();
      await uploadArchiveFile(
        api,
        source.file,
        archiveAbort.current.signal,
        () => {},
        job,
        binding.token,
      );
      archiveSource.current = null;
      if (alive.current)
        setNotice("即时交付已完成；独立归档副本已上传并校验。");
    } catch (failure) {
      if (alive.current)
        setError(
          "交付状态不受影响；归档副本暂未上传：" + errorMessage(failure),
        );
    } finally {
      archiving.current = false;
    }
  };
  const alive = useRef(true),
    polling = useRef(false);
  const poll = async () => {
    if (!binding || polling.current) return;
    polling.current = true;
    try {
      const rows = await deviceApi<Transfer[]>(binding.token, "/inbox");
      const online = await deviceApi<Peer[]>(binding.token, "/peers");
      if (alive.current) {
        setTasks(rows);
        setPeers(online);
        void provideArchive();
      }
    } catch (failure) {
      if (alive.current) setError(errorMessage(failure));
    } finally {
      polling.current = false;
    }
  };
  useEffect(() => {
    alive.current = true;
    if (!binding) return;
    void poll();
    const timer = window.setInterval(() => void poll(), 2000);
    const leave = () => {
      abort.current?.abort();
      archiveAbort.current?.abort();
      if (active.current)
        void fetch(`/api/transfers/${active.current}/cancel`, {
          method: "POST",
          headers: { Authorization: `Bearer ${binding.token}` },
          keepalive: true,
        }).catch(() => {});
    };
    window.addEventListener("pagehide", leave);
    return () => {
      alive.current = false;
      window.clearInterval(timer);
      window.removeEventListener("pagehide", leave);
      abort.current?.abort();
      archiveAbort.current?.abort();
      if (active.current)
        void fetch(`/api/transfers/${active.current}/cancel`, {
          method: "POST",
          headers: { Authorization: `Bearer ${binding.token}` },
          keepalive: true,
        }).catch(() => {});
    };
  }, [binding]);
  useEffect(() => {
    if (!received) {
      setDownload("");
      return;
    }
    const url = URL.createObjectURL(received.blob);
    setDownload(url);
    return () => URL.revokeObjectURL(url);
  }, [received]);
  const showProgress = (done: number, total: number) => {
    const now = performance.now(),
      previous = lastSample.current;
    const speed =
      ((done - previous.done) * 1000) / Math.max(1, now - previous.stamp);
    lastSample.current = { done, stamp: now };
    if (alive.current) setProgress({ done, total, speed });
  };
  const cancel = async () => {
    abort.current?.abort();
    if (binding && active.current) {
      try {
        await deviceApi(binding.token, `/${active.current}/cancel`, "POST");
        setNotice("已请求取消；不支持续传，请重新发送。");
      } catch (failure) {
        setError(errorMessage(failure));
      }
    }
    active.current = null;
    setReceived(null);
    void poll();
  };
  const perform = async (operation: (signal: AbortSignal) => Promise<void>) => {
    if (busy || !binding) return;
    setBusy(true);
    setError("");
    setNotice("");
    abort.current = new AbortController();
    lastSample.current = { stamp: performance.now(), done: 0 };
    setProgress({ done: 0, total: 0, speed: 0 });
    try {
      await operation(abort.current.signal);
    } catch (failure) {
      const cancelled = abort.current?.signal.aborted;
      if (alive.current) {
        if (cancelled) setNotice("传输已取消；重新发送需要重新选择文件。");
        else setError(errorMessage(failure));
      }
      if (active.current) {
        const endpoint = cancelled ? "cancel" : "fail";
        await deviceApi(
          binding.token,
          `/${active.current}/${endpoint}`,
          "POST",
          {
            reason: "浏览器报告传输失败或中断",
          },
        ).catch(() => {});
      }
    } finally {
      if (alive.current) {
        setBusy(false);
        void poll();
      }
    }
  };
  const createBinding = async () => {
    setBusy(true);
    setError("");
    try {
      const data = await api.request<{
        device: { id: string };
        device_token: string;
      }>("/devices", "POST", { name: deviceName, platform: "browser" });
      bind({ id: data.device.id, token: data.device_token });
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <Card title="DeviceLink · 真实中央中继">
        <p className="muted">
          一次发送或接收一个文件。浏览器接收上限 32
          MiB；分块校验、不支持续传。切换页面、刷新或关闭会中断本页未完成传输，设备凭据仅保留内存。
        </p>
        {!binding ? (
          <form
            className="inline-form"
            onSubmit={(e) => {
              e.preventDefault();
              void createBinding();
            }}
          >
            <label>
              当前浏览器设备名称
              <input
                required
                maxLength={64}
                value={deviceName}
                onChange={(e) => setDeviceName(e.target.value)}
              />
            </label>
            <button disabled={busy} className="primary">
              <Link2 size={16} aria-hidden="true" />
              绑定并开启接收
            </button>
          </form>
        ) : (
          <p className="muted">
            当前绑定：{binding.id} · 每 2 秒监听收件箱，10
            秒无监听即不可选择接收。
          </p>
        )}
        {error && (
          <div role="alert" className="feedback error">
            {error}
          </div>
        )}
        {notice && (
          <div role="status" className="feedback success">
            {notice}
          </div>
        )}
      </Card>
      {binding && (
        <>
          <Card title="发送文件">
            <form
              className="inline-form"
              onSubmit={(e) => {
                e.preventDefault();
                if (file && receiver)
                  void perform(async (signal) => {
                    const result = await sendFile(
                      binding.token,
                      file,
                      receiver,
                      signal,
                      (id) => {
                        active.current = id;
                        setNotice("请求已发出，等待接收端同意…");
                      },
                      showProgress,
                    );
                    active.current = null;
                    setNotice(
                      `传输${stateNames[result.state]}，接收确认：${result.confirmation}。独立归档任务可在文件目录查看。`,
                    );
                    archiveSource.current = { id: result.id, file };
                    void provideArchive();
                  });
              }}
            >
              <label>
                在线可接收设备
                <select
                  required
                  value={receiver}
                  onChange={(e) => setReceiver(e.target.value)}
                  disabled={busy}
                >
                  <option value="">请选择在线设备</option>
                  {peers.map((peer) => (
                    <option key={peer.id} value={peer.id}>
                      {peer.name} ({peer.platform})
                    </option>
                  ))}
                </select>
              </label>
              <label>
                发送文件
                <input
                  aria-label="选择发送文件"
                  type="file"
                  disabled={busy}
                  onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                />
              </label>
              <button
                className="primary"
                disabled={
                  busy ||
                  !file ||
                  !receiver ||
                  !!(received && !received.confirmed)
                }
              >
                <ArrowRightLeft size={16} aria-hidden="true" />
                发起发送
              </button>
            </form>
            <div className="transfer-progress">
              <progress
                max={progress.total || 1}
                value={progress.total ? progress.done : busy ? 0 : 1}
              />
              <span>
                {byteText(progress.done)} / {byteText(progress.total)} ·{" "}
                {byteText(progress.speed)}/s
              </span>
              <button
                disabled={!busy && !active.current}
                onClick={() => void cancel()}
              >
                取消当前传输
              </button>
            </div>
          </Card>
          {received && (
            <Card
              title={
                received.confirmed
                  ? "已确认字节；请检查保存副本"
                  : "接收字节已校验，尚未确认"
              }
            >
              <p>
                文件：{received.filename} · {byteText(received.blob.size)} ·
                SHA-256：<code className="break">{received.sha256}</code>
              </p>
              <p className="muted">
                “确认字节接收”只向服务器报告浏览器已核对完整字节，不能证明磁盘保存。请使用“保存副本”，并自行检查浏览器下载结果；离开本页将丢失内存副本。
              </p>
              <div className="row">
                <a
                  className="save-link"
                  href={download}
                  download={received.filename}
                >
                  保存副本
                </a>
                <button
                  className="primary"
                  disabled={received.confirmed}
                  onClick={async () => {
                    try {
                      await deviceApi(
                        binding.token,
                        `/${received.id}/ack`,
                        "POST",
                        {
                          sha256: received.sha256,
                          confirmation: "browser_received",
                        },
                      );
                      active.current = null;
                      setReceived({ ...received, confirmed: true });
                      archiveSource.current = {
                        id: received.id,
                        file: new File([received.blob], received.filename),
                      };
                      setNotice(
                        "已确认浏览器完整字节接收；磁盘保存由你检查。独立归档根据授权设置执行。",
                      );
                      void provideArchive();
                      void poll();
                    } catch (failure) {
                      setError(errorMessage(failure));
                    }
                  }}
                >
                  确认字节接收
                </button>
                <button
                  onClick={() => {
                    if (
                      confirm(
                        "仅释放本页内存副本，服务器没有保存文件。确认已保存或不再需要？",
                      )
                    ) {
                      if (!received.confirmed) void cancel();
                      else setReceived(null);
                    }
                  }}
                >
                  释放内存副本
                </button>
              </div>
            </Card>
          )}
          <Card title="本设备传输记录">
            <div className="section-tools">
              <p className="muted">
                完成状态以接收端确认确定；历史记录不代表服务器保存了文件。
              </p>
              <button onClick={() => void poll()}>
                <RefreshCw size={15} aria-hidden="true" />
                刷新
              </button>
            </div>
            {!tasks.length ? (
              <Empty text="还没有传输任务。" />
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>文件</th>
                      <th>发送 → 接收</th>
                      <th>状态</th>
                      <th>应用转发字节</th>
                      <th>操作 / 确认</th>
                    </tr>
                  </thead>
                  <tbody>
                    {tasks.map((task) => (
                      <tr key={task.id}>
                        <td>
                          <strong>{task.filename}</strong>
                          <small>{byteText(task.size_bytes)}</small>
                        </td>
                        <td>
                          {task.sender_name} → {task.receiver_name}
                        </td>
                        <td>
                          <Status active={task.state === "COMPLETED"}>
                            {stateNames[task.state] ?? task.state}
                          </Status>
                          <small className="break">{task.error}</small>
                        </td>
                        <td>
                          {task.bytes_forwarded.toLocaleString()} /{" "}
                          {task.size_bytes.toLocaleString()}
                        </td>
                        <td>
                          {task.state === "WAITING_RECEIVER" &&
                            task.receiver_id === binding.id && (
                              <div className="row">
                                <button
                                  disabled={busy || !!received}
                                  onClick={() =>
                                    void perform(async (signal) => {
                                      active.current = task.id;
                                      const result = await receiveFile(
                                        binding.token,
                                        task,
                                        signal,
                                        showProgress,
                                      );
                                      setReceived(result);
                                      setNotice(
                                        "字节校验通过，请保存副本并明确确认接收。",
                                      );
                                    })
                                  }
                                >
                                  接受并接收
                                </button>
                                <button
                                  disabled={busy}
                                  onClick={() =>
                                    void deviceApi(
                                      binding.token,
                                      `/${task.id}/reject`,
                                      "POST",
                                    )
                                      .then(poll)
                                      .catch((failure) =>
                                        setError(errorMessage(failure)),
                                      )
                                  }
                                >
                                  拒绝
                                </button>
                              </div>
                            )}
                          {task.state === "COMPLETED" && (
                            <button
                              disabled={busy}
                              onClick={() => {
                                void archiveRequest<ArchiveJob>(
                                  api,
                                  `/archive/transfers/${task.id}`,
                                  "POST",
                                  undefined,
                                  binding.token,
                                )
                                  .then(() =>
                                    setNotice(
                                      "已手动创建归档任务，请在归档页面为任务提供文件副本。",
                                    ),
                                  )
                                  .catch((failure) =>
                                    setError(errorMessage(failure)),
                                  );
                              }}
                            >
                              手动归档
                            </button>
                          )}
                          {task.confirmation === "browser_received"
                            ? "浏览器字节已确认，磁盘保存未证明"
                            : task.confirmation === "written_file"
                              ? "接收端报告已写入文件"
                              : !terminal.has(task.state)
                                ? "等待接收端确认"
                                : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </>
      )}
    </>
  );
}
