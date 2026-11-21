import { useEffect, useState } from "react";
import { ApiClient, errorMessage } from "./api";
import { Card, Metric } from "./components";
import { bytes, when } from "./types";
import type { LibraryFile } from "./library";

interface Overview {
  files: number;
  versions: number;
  objects: number;
  events: number;
  trash: number;
  conflicts: number;
  downloads: number;
  bytes_sent: number;
  failed_downloads: number;
  revision: number;
  users: {
    user_id: string;
    username: string;
    files: number;
    versions: number;
    referenced_bytes: number;
  }[];
  items: LibraryFile[];
  sync: {
    user_id: string;
    client_id: string;
    revision: number;
    updated_at: number;
  }[];
  download_tasks: {
    id: string;
    version_id: string;
    state: string;
    bytes_sent: number;
    error: string;
    updated_at: number;
  }[];
}
export function LibraryAdmin({ api }: { api: ApiClient }) {
  const [data, setData] = useState<Overview | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [offset, setOffset] = useState(0),
    [report, setReport] = useState("");
  const load = async () => {
    setBusy(true);
    try {
      setData(await api.request<Overview>(`/library?offset=${offset}`));
      setError("");
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => {
    void load();
  }, [api, offset]);
  return (
    <>
      <h1>文件库运营</h1>
      <p className="muted">
        元数据与诊断。内容读取仍需文件所属用户或空间成员授权。
      </p>
      {error && <p role="alert">{error}</p>}
      <div className="actions">
        <button disabled={busy} onClick={() => void load()}>
          刷新真实统计
        </button>
        <button
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            try {
              setReport(
                JSON.stringify(
                  await api.request("/storage/reconcile", "POST"),
                  null,
                  2,
                ),
              );
            } catch (e) {
              setError(errorMessage(e));
            } finally {
              setBusy(false);
            }
          }}
        >
          只读存储对账
        </button>
      </div>
      {data && (
        <>
          <div className="metrics">
            {[
              ["逻辑文件", data.files],
              ["文件版本", data.versions],
              ["存储对象", data.objects],
              ["同步事件", data.events],
              ["回收站", data.trash],
              ["未解决冲突", data.conflicts],
              ["下载任务", data.downloads],
              ["失败 / 中断下载", data.failed_downloads],
            ].map(([title, value]) => (
              <Metric
                key={title}
                title={String(title)}
                value={value}
                note="数据库实时统计"
              />
            ))}
          </div>
          <p>已发送响应字节 {bytes(data.bytes_sent)}（不代表客户端落盘确认）</p>
          <Card title="用户容量（按版本引用计，复用对象可能重复计数）">
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>用户</th>
                    <th>文件</th>
                    <th>版本</th>
                    <th>引用容量</th>
                  </tr>
                </thead>
                <tbody>
                  {data.users.map((u) => (
                    <tr key={u.user_id}>
                      <td>{u.username}</td>
                      <td>{u.files}</td>
                      <td>{u.versions}</td>
                      <td>{bytes(u.referenced_bytes)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
          <Card title="文件元数据">
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>文件</th>
                    <th>用户</th>
                    <th>当前版本</th>
                    <th>存储提供商</th>
                    <th>校验 / 状态</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((f) => (
                    <tr key={f.file_id}>
                      <td>{f.filename}</td>
                      <td>{f.user_id}</td>
                      <td>v{f.version}</td>
                      <td>{f.provider}</td>
                      <td>{f.deleted_at ? "已删除" : "已归档"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="actions">
              <button
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - 100))}
              >
                上一页
              </button>
              <button
                disabled={data.items.length < 100}
                onClick={() => setOffset(offset + 100)}
              >
                下一页
              </button>
            </div>
          </Card>
          <Card title="目录同步诊断">
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>用户 / 会话</th>
                    <th>已提交游标</th>
                    <th>修订差距</th>
                    <th>最后请求</th>
                  </tr>
                </thead>
                <tbody>
                  {data.sync.map((s) => (
                    <tr key={s.user_id + s.client_id}>
                      <td>
                        {s.user_id} / {s.client_id}
                      </td>
                      <td>{s.revision}</td>
                      <td>{Math.max(0, data.revision - s.revision)}</td>
                      <td>{when(s.updated_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="muted">
              修订差距含其他作用域事件，供诊断参考；不是实际秒数延迟。失败下载由用户重新请求，目录
              409 重取快照。
            </p>
          </Card>
          <Card title="下载任务">
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>任务 / 版本</th>
                    <th>状态</th>
                    <th>发送字节</th>
                    <th>错误</th>
                  </tr>
                </thead>
                <tbody>
                  {data.download_tasks.map((t) => (
                    <tr key={t.id}>
                      <td>
                        {t.id}
                        <small>{t.version_id}</small>
                      </td>
                      <td>{t.state}</td>
                      <td>{t.bytes_sent}</td>
                      <td>{t.error || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}
      {report && (
        <Card title="只读对账结果">
          <pre className="library-hash">{report}</pre>
        </Card>
      )}
    </>
  );
}
