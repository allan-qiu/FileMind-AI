import { useEffect, useState } from "react";
import { ApiClient, errorMessage } from "./api";
import { Card, Empty, Metric, Pager, Status } from "./components";
import { byteText, stateNames, type Transfer } from "./devicelink";
import { when } from "./types";

interface AdminData {
  items: Transfer[];
  totals: {
    total: number;
    completed: number | null;
    failed: number | null;
    active: number | null;
    forwarded_bytes: number;
  };
  buffered_bytes: number;
  peak_buffer_bytes: number;
}

export function TransferAdmin({ api }: { api: ApiClient }) {
  const [data, setData] = useState<AdminData | null>(null),
    [error, setError] = useState("");
  const [offset, setOffset] = useState(0),
    [filter, setFilter] = useState("all");
  const load = async () => {
    try {
      setData(await api.request<AdminData>(`/transfers?offset=${offset}`));
      setError("");
    } catch (failure) {
      setError(errorMessage(failure));
    }
  };
  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 3000);
    return () => window.clearInterval(timer);
  }, [api, offset]);
  const rows =
    data?.items.filter((row) => filter === "all" || row.state === filter) ?? [];
  return (
    <>
      {error && (
        <div className="feedback error" role="alert">
          {error}
        </div>
      )}
      {data && (
        <div className="metrics">
          <Metric
            title="活跃传输"
            value={data.totals.active ?? 0}
            note={`历史任务 ${data.totals.total}`}
          />
          <Metric
            title="完成 / 失败或过期"
            value={`${data.totals.completed ?? 0} / ${data.totals.failed ?? 0}`}
            note="完成须接收端确认"
          />
          <Metric
            title="累计应用转发量"
            value={byteText(data.totals.forwarded_bytes)}
            note={`${data.totals.forwarded_bytes.toLocaleString()} 字节，含失败已转发部分`}
          />
        </div>
      )}
      <Card title="中央中继传输管理">
        <div className="section-tools">
          <label>
            本页状态筛选
            <select value={filter} onChange={(e) => setFilter(e.target.value)}>
              <option value="all">全部</option>
              {Object.entries(stateNames).map(([value, name]) => (
                <option key={value} value={value}>
                  {name}
                </option>
              ))}
            </select>
          </label>
          <button onClick={() => void load()}>刷新真实数据</button>
        </div>
        <p className="muted">
          只显示运营元数据，不提供私人文件内容读取。每 3
          秒更新；计数为完整块交给响应发送的应用字节，不含协议开销或磁盘保存保证。
        </p>
        {data && (
          <p className="muted">
            当前中继管理缓冲 {data.buffered_bytes.toLocaleString()}{" "}
            字节；本进程峰值 {data.peak_buffer_bytes.toLocaleString()} 字节。
          </p>
        )}
        {!data ? (
          <p role="status">正在加载传输记录…</p>
        ) : !rows.length ? (
          <Empty text="当前筛选没有真实传输任务。" />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>文件 / 账户</th>
                  <th>来源 → 接收设备</th>
                  <th>状态 / 确认</th>
                  <th>声明 / 转发字节</th>
                  <th>创建 / 完成时间</th>
                  <th>失败原因</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.id}>
                    <td>
                      <strong>{row.filename}</strong>
                      <small>{row.username}</small>
                    </td>
                    <td>
                      {row.sender_name} → {row.receiver_name}
                    </td>
                    <td>
                      <Status active={row.state === "COMPLETED"}>
                        {stateNames[row.state]}
                      </Status>
                      <small>{row.confirmation || "未确认"}</small>
                    </td>
                    <td>
                      {row.size_bytes.toLocaleString()} /{" "}
                      {row.bytes_forwarded.toLocaleString()}
                    </td>
                    <td>
                      {when(row.created_at)}
                      <small>{when(row.completed_at)}</small>
                    </td>
                    <td className="break">{row.error || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {data && (
          <Pager
            offset={offset}
            size={100}
            count={data.items.length}
            change={setOffset}
          />
        )}
      </Card>
    </>
  );
}
