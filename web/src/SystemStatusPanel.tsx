import { Fragment, useEffect, useState } from "react";
import { ApiClient } from "./api";
import { Card } from "./components";
import { bytes } from "./types";

interface SystemStatus {
  ai: {
    provider: string;
    model: string;
    configuration_status: string;
    connection_status: string;
    selected_mode: string;
    paid_calls_enabled: boolean;
    validation?: {
      states: Record<string, string>;
      last_live_success_at: number | null;
      live_archives: number;
      counts: {
        provider_attempts: number;
        cache_reuses: number;
        errors: number;
      };
      scope: string;
    };
    usage: {
      simulated: number;
      currency: string;
      requests: number;
      input_tokens: number;
      output_tokens: number;
      estimated_cost: number;
    }[];
  };
  baidu: { status: string; last_error: string; writes_enabled: boolean };
  storage: { write_provider: string; read_cache_reserved_bytes: number };
  archive_jobs: Record<string, number>;
  archive_failure_rate: number;
  counts: Record<string, number>;
  database: { journal_mode: string; schema: number; read_query: string };
  acceptance: Record<string, string>;
}

export function SystemStatusPanel({ api }: { api: ApiClient }) {
  const [data, setData] = useState<SystemStatus | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const load = async () => {
    setLoading(true);
    setError("");
    try {
      setData(await api.request<SystemStatus>("/system-status"));
    } catch (e) {
      setData(null);
      setError(e instanceof Error ? e.message : "系统状态读取失败");
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    void load();
  }, [api]);
  return (
    <Card title="集成状态与验收边界" className="integration-status">
      <button disabled={loading} onClick={() => void load()}>
        {loading ? "正在读取系统状态…" : "刷新集成状态"}
      </button>
      {error && <p role="alert">{error}</p>}
      {data && (
        <>
          <dl className="detail-list">
            <dt>AI Provider / 模型</dt>
            <dd>
              {data.ai.provider} / {data.ai.model}
            </dd>
            <dt>AI 密钥</dt>
            <dd>{data.ai.configuration_status}</dd>
            <dt>AI 运行模式</dt>
            <dd>{data.ai.selected_mode}</dd>
            <dt>真实 AI 连通性</dt>
            <dd>{data.ai.connection_status}</dd>
            <dt>收费调用开关</dt>
            <dd>
              {data.ai.paid_calls_enabled
                ? "已开启，仍受授权和预算限制"
                : "关闭"}
            </dd>
            <dt>百度网盘</dt>
            <dd>{data.baidu.status}</dd>
            <dt>网盘写入授权开关</dt>
            <dd>{data.baidu.writes_enabled ? "已开启" : "关闭"}</dd>
            <dt>最近存储错误</dt>
            <dd>{data.baidu.last_error || "无记录（不代表连接成功）"}</dd>
            <dt>归档存储</dt>
            <dd>{data.storage.write_provider}</dd>
            <dt>下载校验缓存</dt>
            <dd>{bytes(data.storage.read_cache_reserved_bytes)}</dd>
            <dt>归档失败比例</dt>
            <dd>{(data.archive_failure_rate * 100).toFixed(1)}%</dd>
            <dt>数据库</dt>
            <dd>
              schema {data.database.schema} · {data.database.journal_mode} ·{" "}
              {data.database.read_query}
            </dd>
            {Object.entries(data.archive_jobs).map(([state, count]) => (
              <Fragment key={state}>
                <dt>归档 {state}</dt>
                <dd>{count}</dd>
              </Fragment>
            ))}
          </dl>
          {data.ai.validation && (
            <section aria-label="DeepSeek 验收记录">
              <h3>DeepSeek 验收记录</h3>
              <dl className="detail-list">
                {Object.entries(data.ai.validation.states).map(
                  ([name, state]) => (
                    <Fragment key={name}>
                      <dt>
                        {(
                          {
                            API_KEY_CONFIGURED: "密钥配置",
                            API_MODEL_AVAILABLE: "账户模型列表",
                            API_AUTHENTICATION_VERIFIED: "模型列表鉴权",
                            LIVE_CLASSIFICATION_TESTED: "真实分类历史",
                            LIVE_ARCHIVE_TESTED: "真实归档历史",
                          } as Record<string, string>
                        )[name] ?? name}
                      </dt>
                      <dd>{state}</dd>
                    </Fragment>
                  ),
                )}
              </dl>
              <p>
                Provider 尝试 {data.ai.validation.counts.provider_attempts} 次 ·
                缓存复用 {data.ai.validation.counts.cache_reuses} 次 ·
                失败或结果未知 {data.ai.validation.counts.errors} 次 ·
                真实归档历史 {data.ai.validation.live_archives} 次
              </p>
              <p>
                最近真实分类成功：
                {data.ai.validation.last_live_success_at
                  ? new Date(
                      data.ai.validation.last_live_success_at * 1000,
                    ).toLocaleString()
                  : "尚无记录"}
              </p>
              <p className="muted">{data.ai.validation.scope}</p>
            </section>
          )}
          <h3>Token 与估算费用</h3>
          {!data.ai.usage.length ? (
            <p>尚无调用记录。</p>
          ) : (
            data.ai.usage.map((u) => (
              <p key={`${u.currency}-${u.simulated}`}>
                {u.simulated ? "Mock" : "真实 API"} · {u.requests} 次 · 输入{" "}
                {u.input_tokens} / 输出 {u.output_tokens} Token ·{" "}
                {u.estimated_cost.toFixed(6)} {u.currency}（估算）
              </p>
            ))
          )}
          <h3>文件库与分享</h3>
          <p>
            {data.counts.library_files} 个文件 · {data.counts.library_versions}{" "}
            个版本 · {data.counts.share_links} 条分享 ·{" "}
            {data.counts.completed_share_downloads} 次完成下载
          </p>
          <p>
            分享尝试流量 {bytes(data.counts.share_wire_bytes)} · 归档临时预留{" "}
            {bytes(data.counts.temporary_reserved_bytes)} · 临时分享{" "}
            {bytes(data.counts.temporary_share_bytes)}
          </p>
          <h3>外部验收</h3>
          <ul>
            {Object.entries(data.acceptance).map(([name, state]) => (
              <li key={name}>
                {name}：{state}
              </li>
            ))}
          </ul>
          <p className="muted">
            配置和开关不证明生产可用。刷新此页只读取中央服务状态，不会调用收费
            AI 或写入网盘。
          </p>
        </>
      )}
    </Card>
  );
}
