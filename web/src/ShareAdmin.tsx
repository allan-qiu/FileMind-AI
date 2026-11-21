import { useEffect, useState } from "react";
import { ApiClient, errorMessage } from "./api";
import { Card } from "./components";
import { ShareCenter } from "./ShareCenter";
import "./share.css";

export function ShareAdmin({ api }: { api: ApiClient }) {
  const [stats, setStats] = useState<Record<string, unknown> | null>(null),
    [settings, setSettings] = useState<Record<string, number | boolean> | null>(
      null,
    ),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [busy, setBusy] = useState(false);
  async function load() {
    try {
      const [s, c] = await Promise.all([
        api.request<Record<string, unknown>>("/share-statistics"),
        api.request<Record<string, number | boolean>>("/shares/settings"),
      ]);
      setStats(s);
      setSettings(c);
    } catch (e) {
      setError(errorMessage(e));
    }
  }
  useEffect(() => {
    void load();
  }, [api]);
  const labels: Record<string, string> = {
    active: "有效分享",
    created_today: "今日创建",
    temporary: "临时分享",
    library: "正式库分享",
    expired: "已过期",
    revoked: "已撤销",
    completed_today: "今日完成下载",
    active_sessions: "活跃下载名额",
    wire_attempt_bytes: "传输尝试字节",
    temporary_bytes: "临时对象字节",
    pending_upload_bytes: "上传预留字节",
    cleanup_failures: "清理失败",
  };
  const settingLabels: Record<string, string> = {
    allow_temporary: "允许临时分享",
    allow_library: "允许文件库分享",
    default_ttl: "默认有效期（秒）",
    max_ttl: "最大有效期（秒）",
    default_downloads: "默认下载次数",
    max_downloads: "最大下载次数",
    max_file_bytes: "单文件上限（字节）",
    user_temp_bytes: "单用户临时空间（字节）",
    total_temp_bytes: "总临时空间（字节）",
    max_concurrency: "全局并发名额",
    user_concurrency: "用户并发名额",
    share_concurrency: "链接并发名额",
    session_ttl: "下载会话时限（秒）",
    cleanup_interval: "清理间隔（秒）",
    upload_ttl: "上传时限（秒）",
    bytes_per_second: "单连接每秒字节",
    user_daily_bytes: "用户每日流量字节",
    event_retention_days: "访问审计保留天数",
  };
  async function act(action: () => Promise<unknown>) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await action();
      setNotice("服务端配置或对账已完成");
      await load();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="share-stack">
      {error && (
        <p role="alert" className="feedback error">
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      <Card title="分享运行概览">
        <button onClick={() => void load()}>刷新真实统计</button>
        <div className="share-metrics">
          {Object.entries(labels).map(([key, label]) => (
            <div className="share-metric" key={key}>
              {label}
              <strong>{stats ? String(stats[key]) : "加载中"}</strong>
            </div>
          ))}
        </div>
        <p>今日按 UTC 统计。传输尝试含失败发送；统计窗口受审计保留期限制。</p>
      </Card>
      <ShareCenter api={api} admin />
      <Card title="分享系统配置">
        <fieldset disabled={busy} className="share-form">
          {settings &&
            Object.entries(settings).map(([k, v]) => (
              <label key={k}>
                {settingLabels[k] || k}
                {typeof v === "boolean" ? (
                  <input
                    type="checkbox"
                    checked={v}
                    onChange={(e) =>
                      setSettings({ ...settings, [k]: e.target.checked })
                    }
                  />
                ) : (
                  <input
                    type="number"
                    min={1}
                    value={v}
                    onChange={(e) =>
                      setSettings({ ...settings, [k]: Number(e.target.value) })
                    }
                  />
                )}
              </label>
            ))}
        </fieldset>
        <div className="actions">
          <button
            disabled={busy || !settings}
            onClick={() =>
              void act(() => api.request("/shares/settings", "PUT", settings))
            }
          >
            保存全局分享设置
          </button>
          <button
            disabled={busy}
            onClick={() =>
              void act(() => api.request("/shares/cleanup/run", "POST"))
            }
          >
            立即运行临时存储对账
          </button>
        </div>
      </Card>
      <Card title="下载会话与失败任务">
        <pre className="share-history">
          {stats ? JSON.stringify(stats.sessions, null, 2) : "正在加载"}
        </pre>
      </Card>
      <Card title="临时存储与清理状态">
        <pre className="share-history">
          {stats
            ? JSON.stringify(stats.temporary_objects, null, 2)
            : "正在加载"}
        </pre>
      </Card>
    </div>
  );
}
