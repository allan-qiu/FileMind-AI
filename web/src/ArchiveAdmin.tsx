import { useEffect, useState } from "react";
import { ApiClient, errorMessage } from "./api";
import { Card, Empty, Metric, Status } from "./components";
import { archiveStates, type ArchiveJob } from "./archive";
import { byteText } from "./devicelink";

type AIConfig = {
  provider: string;
  model: string;
  timeout: number;
  retries: number;
  concurrency: number;
  max_tokens: number;
  monthly_budget: number;
  request_limit: number;
  input_per_million: number;
  output_per_million: number;
  price_version: string;
  currency: string;
  categories: string[];
  key_configured: boolean;
  real_api_enabled: boolean;
};
type ArchiveConfig = {
  enabled: boolean;
  concurrency: number;
  upload_concurrency: number;
  cache_bytes: number;
  max_file_bytes: number;
  ttl_seconds: number;
  lease_seconds: number;
  backup_grace_seconds: number;
  max_retries: number;
  provider: string;
};
type Usage = {
  items: {
    id: string;
    model: string;
    provider: string;
    status: string;
    input_tokens: number;
    output_tokens: number;
    estimated_cost: number;
    currency: string;
    price_version: string;
    simulated: boolean;
    error: string;
  }[];
  totals: {
    currency: string;
    simulated: boolean;
    requests: number;
    provider_attempts: number;
    cache_reuses: number;
    input_tokens: number;
    output_tokens: number;
    estimated_cost: number;
    failures: number;
  }[];
};
type Storage = {
  status: string;
  files: number;
  used_bytes: number;
  temporary_objects: number;
  reserved_bytes: number;
  jobs: { state: string; count: number }[];
  last_reconciliation: { created_at: number; result_json: string } | null;
};

export function ArchiveAdmin({ api }: { api: ApiClient }) {
  const [ai, setAi] = useState<AIConfig | null>(null),
    [config, setConfig] = useState<ArchiveConfig | null>(null);
  const [jobs, setJobs] = useState<ArchiveJob[]>([]),
    [usage, setUsage] = useState<Usage | null>(null),
    [storage, setStorage] = useState<Storage | null>(null);
  const [key, setKey] = useState(""),
    [filter, setFilter] = useState(""),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [busy, setBusy] = useState(false);
  const load = async () => {
    const [a, c, j, u, s] = await Promise.all([
      api.request<AIConfig>("/ai"),
      api.request<ArchiveConfig>("/archive/settings"),
      api.request<ArchiveJob[]>("/archive/jobs"),
      api.request<Usage>("/ai/usage"),
      api.request<Storage>("/storage"),
    ]);
    setAi(a);
    setConfig(c);
    setJobs(j);
    setUsage(u);
    setStorage(s);
  };
  useEffect(() => {
    void load().catch((e) => setError(errorMessage(e)));
  }, [api]);
  const run = async (action: () => Promise<void>) => {
    if (busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await action();
      await load();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
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
      <div className="section-tools">
        <p className="muted">真实任务与估算用量；Mock 和真实 API 分开标识。</p>
        <button disabled={busy} onClick={() => void run(async () => {})}>
          刷新管理数据
        </button>
      </div>
      {storage && (
        <>
          <div className="metrics">
            <Metric
              title="已归档文件"
              value={storage.files}
              note={byteText(storage.used_bytes)}
            />
            <Metric
              title="临时处理对象"
              value={storage.temporary_objects}
              note={`${byteText(storage.reserved_bytes)} 已预留`}
            />
            <Metric
              title="归档任务"
              value={jobs.length}
              note="最近 200 条任务"
            />
          </div>
          <Card title="存储管理">
            <p role="status">{storage.status}</p>
            <p className="muted">
              正式对象以完整 SHA-256 回读校验；不提供虚构的百度网盘容量。
            </p>
            <div className="row">
              {storage.jobs.map((s) => (
                <Status key={s.state}>
                  {archiveStates[s.state] ?? s.state} {s.count}
                </Status>
              ))}
            </div>
            <p>
              上次对账：
              {storage.last_reconciliation
                ? new Date(
                    storage.last_reconciliation.created_at * 1000,
                  ).toLocaleString()
                : "尚未执行"}
            </p>
            <button
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  const result = await api.request<{
                    checked: number;
                    failures: unknown[];
                  }>("/storage/reconcile", "POST");
                  setNotice(
                    `只读对账完成：${result.checked} 个对象，${result.failures.length} 项不一致。`,
                  );
                })
              }
            >
              执行只读存储对账
            </button>
          </Card>
        </>
      )}
      {ai && (
        <Card title="AI 管理中心">
          <p>
            默认服务：DeepSeek · 官方地址 https://api.deepseek.com · 密钥：
            {ai.key_configured ? "已配置（不回传）" : "未配置"} · 服务器真实
            API：{ai.real_api_enabled ? "已允许" : "未授权"}
          </p>
          <form
            className="archive-grid"
            onSubmit={(e) => {
              e.preventDefault();
              void run(async () => {
                const {
                  key_configured: _key,
                  real_api_enabled: _gate,
                  ...settings
                } = ai;
                await api.request("/ai", "PUT", {
                  settings,
                  ...(key ? { api_key: key } : {}),
                });
                setKey("");
                setNotice("AI 配置已保存。真实调用仍由服务器授权开关控制。");
              });
            }}
          >
            <label>
              提供商
              <select
                value={ai.provider}
                onChange={(e) => setAi({ ...ai, provider: e.target.value })}
              >
                <option value="deepseek">DeepSeek · 需要正式授权</option>
                <option value="mock">Mock · 仅合成联调</option>
              </select>
            </label>
            <label>
              模型 ID
              <input
                required
                value={ai.model}
                onChange={(e) => setAi({ ...ai, model: e.target.value })}
              />
            </label>
            <label>
              更新 API 密钥
              <input
                autoComplete="new-password"
                type="password"
                value={key}
                onChange={(e) => setKey(e.target.value)}
                placeholder="留空保留现有密钥"
              />
            </label>
            {(
              [
                ["timeout", "请求超时（秒）"],
                ["retries", "限流重试次数"],
                ["concurrency", "AI 最大并发"],
                ["max_tokens", "最大输出 Token"],
                ["monthly_budget", "月度预算"],
                ["request_limit", "单次调用预算"],
                ["input_per_million", "每百万输入 Token 估价"],
                ["output_per_million", "每百万输出 Token 估价"],
              ] as const
            ).map(([name, title]) => (
              <label key={name}>
                {title}
                <input
                  type="number"
                  min={0}
                  step={
                    name.includes("million") ||
                    name.includes("budget") ||
                    name === "request_limit"
                      ? 0.0001
                      : 1
                  }
                  value={ai[name]}
                  onChange={(e) =>
                    setAi({ ...ai, [name]: Number(e.target.value) })
                  }
                />
              </label>
            ))}
            <label>
              价格规则版本
              <input
                required
                value={ai.price_version}
                onChange={(e) =>
                  setAi({ ...ai, price_version: e.target.value })
                }
              />
            </label>
            <label>
              计价币种
              <input
                maxLength={3}
                value={ai.currency}
                onChange={(e) =>
                  setAi({ ...ai, currency: e.target.value.toUpperCase() })
                }
              />
            </label>
            <label>
              允许的顶层分类（逗号分隔）
              <input
                value={ai.categories.join(",")}
                onChange={(e) =>
                  setAi({ ...ai, categories: e.target.value.split(/[,，]/) })
                }
              />
            </label>
            <div className="row">
              <button className="primary" disabled={busy}>
                保存 AI 配置
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() =>
                  void run(async () => {
                    const r = await api.request<{ available: boolean }>(
                      "/ai/test",
                      "POST",
                    );
                    setNotice(
                      r.available
                        ? "模型列表连接成功；尚非分类调用验收。"
                        : "连接成功，但账户未列出所选模型。",
                    );
                  })
                }
              >
                测试模型列表连接
              </button>
            </div>
          </form>
          <p className="muted">
            价格需要按官方和账户更新，费用是估算值。网络结果未知时保留预算并人工处理，避免自动重复扣费。
          </p>
        </Card>
      )}
      {usage && (
        <Card title="AI 使用量与估算费用">
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>环境 / 币种</th>
                  <th>任务 / Provider 尝试 / 缓存复用</th>
                  <th>输入 / 输出 Token</th>
                  <th>估算费用</th>
                  <th>失败 / 结果未知</th>
                </tr>
              </thead>
              <tbody>
                {usage.totals.map((t) => (
                  <tr key={`${t.simulated}-${t.currency}`}>
                    <td>
                      {t.simulated ? "Mock" : "真实 Provider"} / {t.currency}
                    </td>
                    <td>
                      {t.requests} / {t.provider_attempts} / {t.cache_reuses}
                    </td>
                    <td>
                      {t.input_tokens} / {t.output_tokens}
                    </td>
                    <td>{t.estimated_cost.toFixed(6)}</td>
                    <td>{t.failures}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <details>
            <summary>最近调用与失败原因</summary>
            {usage.items.map((u) => (
              <p className="break" key={u.id}>
                {u.model} · {u.simulated ? "Mock" : "真实 Provider"} ·{" "}
                {u.status} · {u.error} · {u.currency}{" "}
                {u.estimated_cost.toFixed(6)} · 价格 {u.price_version}
              </p>
            ))}
          </details>
        </Card>
      )}
      {config && (
        <Card title="归档系统配置">
          <form
            className="archive-grid"
            onSubmit={(e) => {
              e.preventDefault();
              void run(async () => {
                await api.request("/archive/settings", "PUT", config);
                setNotice("归档配置已保存。");
              });
            }}
          >
            <label className="check">
              <input
                type="checkbox"
                checked={config.enabled}
                onChange={(e) =>
                  setConfig({ ...config, enabled: e.target.checked })
                }
              />
              启用归档工作器与副本领取
            </label>
            <label>
              正式存储提供商
              <select
                value={config.provider}
                onChange={(e) =>
                  setConfig({ ...config, provider: e.target.value })
                }
              >
                <option value="local">本地存储适配器</option>
                <option value="baidu" disabled>
                  百度网盘 · 尚未连接
                </option>
              </select>
            </label>
            {(
              [
                ["concurrency", "归档最大并发"],
                ["upload_concurrency", "归档上传并发"],
                ["cache_bytes", "临时缓存上限（字节）"],
                ["max_file_bytes", "单文件上限（字节）"],
                ["ttl_seconds", "暂存与等待期限（秒）"],
                ["lease_seconds", "上传租约（秒）"],
                ["backup_grace_seconds", "主要来源优先期（秒）"],
                ["max_retries", "自动失败重试次数"],
              ] as const
            ).map(([name, title]) => (
              <label key={name}>
                {title}
                <input
                  type="number"
                  min={0}
                  value={config[name]}
                  onChange={(e) =>
                    setConfig({ ...config, [name]: Number(e.target.value) })
                  }
                />
              </label>
            ))}
            <button className="primary" disabled={busy}>
              保存归档配置
            </button>
          </form>
        </Card>
      )}
      <Card title="归档任务中心">
        <label>
          阶段筛选
          <select value={filter} onChange={(e) => setFilter(e.target.value)}>
            <option value="">全部</option>
            {Object.entries(archiveStates).map(([k, n]) => (
              <option key={k} value={k}>
                {n}
              </option>
            ))}
          </select>
        </label>
        {!jobs.length ? (
          <Empty text="暂无真实归档任务" />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>任务 / 用户 / 来源</th>
                  <th>文件 / 大小</th>
                  <th>当前阶段 / 模型</th>
                  <th>目标目录 / 时间</th>
                  <th>失败 / 重试</th>
                </tr>
              </thead>
              <tbody>
                {jobs
                  .filter((j) => !filter || j.state === filter)
                  .map((j) => (
                    <tr key={j.id}>
                      <td className="break">
                        {j.id}
                        <small>{j.username}</small>
                        <small>
                          {j.upload_owner ?? j.primary_owner ?? "用户上传"}
                        </small>
                      </td>
                      <td>
                        {j.filename}
                        <small>{byteText(j.size_bytes)}</small>
                      </td>
                      <td>
                        <Status active={j.state === "COMPLETED"}>
                          {archiveStates[j.state] ?? j.state}
                        </Status>
                        <small>{j.model ?? "未调用"}</small>
                      </td>
                      <td>
                        {j.plan?.target_directory ?? "未规划"}
                        <small>
                          {new Date(j.created_at * 1000).toLocaleString()}
                        </small>
                        <small>
                          {j.completed_at
                            ? new Date(j.completed_at * 1000).toLocaleString()
                            : "尚未完成"}
                        </small>
                      </td>
                      <td className="break">
                        {j.error}
                        <small>重试 {j.retry_count} 次</small>
                        {["FAILED", "RETRY_WAIT"].includes(j.state) && (
                          <button
                            disabled={busy}
                            onClick={() =>
                              void run(async () => {
                                await api.request(
                                  `/archive/jobs/${j.id}/retry`,
                                  "POST",
                                );
                              })
                            }
                          >
                            受控重试
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}
