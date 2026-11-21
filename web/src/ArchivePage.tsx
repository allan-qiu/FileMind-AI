import { useEffect, useRef, useState } from "react";
import { Archive, RefreshCw, Upload, Check } from "lucide-react";
import { ApiClient, errorMessage } from "./api";
import { Card, Empty, Status } from "./components";
import { byteText } from "./devicelink";
import {
  archiveStates,
  uploadArchiveFile,
  type ArchiveJob,
  type Plan,
  type Preferences,
} from "./archive";

interface LibraryFile {
  id: string;
  filename: string;
  category: string;
  target_directory: string;
  version: number;
  size_bytes: number;
  sha256: string;
  object_key: string;
  verification_level: string;
  summary: string;
  revision_id: number;
}

export function ArchivePage({
  api,
  binding,
}: {
  api: ApiClient;
  binding: { id: string; token: string } | null;
}) {
  const [jobs, setJobs] = useState<ArchiveJob[]>([]),
    [files, setFiles] = useState<LibraryFile[]>([]);
  const [prefs, setPrefs] = useState<Preferences>({
    auto_archive: false,
    ai_mode: "off",
    rules: "",
  });
  const [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [busy, setBusy] = useState(false);
  const [filter, setFilter] = useState(""),
    [selected, setSelected] = useState<ArchiveJob | null>(null);
  const [plan, setPlan] = useState<Plan | null>(null),
    [file, setFile] = useState<File | null>(null);
  const [progress, setProgress] = useState({ done: 0, total: 0 });
  const abort = useRef<AbortController | null>(null),
    loading = useRef(false),
    mounted = useRef(true),
    editingPrefs = useRef(false);
  const load = async () => {
    if (loading.current) return;
    loading.current = true;
    try {
      const [j, f, p] = await Promise.all([
        api.request<ArchiveJob[]>("/archive/jobs"),
        api.request<LibraryFile[]>("/library"),
        api.request<Preferences>("/archive/preferences"),
      ]);
      if (mounted.current) {
        setJobs(j);
        setFiles(f);
        if (!editingPrefs.current) setPrefs(p);
      }
    } catch (e) {
      if (mounted.current) setError(errorMessage(e));
    } finally {
      loading.current = false;
    }
  };
  useEffect(() => {
    mounted.current = true;
    void load();
    const timer = setInterval(() => void load(), 2000);
    return () => {
      mounted.current = false;
      clearInterval(timer);
      abort.current?.abort();
    };
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
  const pick = (job: ArchiveJob) => {
    setSelected(job);
    setPlan(
      job.plan
        ? { ...job.plan }
        : job.state === "PLAN_REVIEW_REQUIRED"
          ? {
              category: "其他",
              subcategory: "",
              suggested_filename: job.filename,
              target_directory: "其他",
              summary: "人工确认",
              keywords: [],
              related_file_ids: [],
              needs_user_review: true,
              reason: "AI未提供可执行计划，使用明确人工方案。",
            }
          : null,
    );
    setFile(null);
    setNotice("");
  };
  return (
    <>
      <Card title="云端归档与 AI 授权">
        <p className="muted">
          DeviceLink
          交付与归档分别记录。当前正式文件使用本地存储适配器；百度网盘未正式连接。AI
          使用 Mock 时会显示 mock-v2.2，不能代表真实模型分析。
        </p>
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
        <form
          className="archive-grid"
          onSubmit={(e) => {
            e.preventDefault();
            void run(async () => {
              await api.request("/archive/preferences", "PUT", prefs);
              editingPrefs.current = false;
              setNotice(
                "授权设置已保存。已发出的请求不能撤回；后续任务使用新设置。",
              );
            });
          }}
        >
          <label className="check">
            <input
              type="checkbox"
              checked={prefs.auto_archive}
              onChange={(e) => {
                editingPrefs.current = true;
                setPrefs({ ...prefs, auto_archive: e.target.checked });
              }}
            />
            交付后由当前设备自动提供归档副本
          </label>
          <label>
            允许发送给云端 AI 的信息
            <select
              value={prefs.ai_mode}
              onChange={(e) => {
                editingPrefs.current = true;
                setPrefs({ ...prefs, ai_mode: e.target.value });
              }}
            >
              <option value="off">尚未授权 · 等待人工处理</option>
              <option value="metadata">仅文件名与元数据</option>
              <option value="summary">文本摘录 · 最多 4000 字符</option>
              <option value="content">提取正文 · 最多 16000 字符</option>
            </select>
          </label>
          <label>
            个人分类规则
            <textarea
              maxLength={2000}
              value={prefs.rules}
              onChange={(e) => {
                editingPrefs.current = true;
                setPrefs({ ...prefs, rules: e.target.value });
              }}
            />
          </label>
          <button className="primary" disabled={busy}>
            <Check size={16} aria-hidden="true" />
            保存授权设置
          </button>
        </form>
        <p className="muted">
          不支持的正文类型会明确降为元数据规划并等待确认。当前不发送图片原始内容。关闭页面会暂停本页归档上传，恢复时需重新选择同一文件。
        </p>
      </Card>
      <Card
        title={
          selected
            ? `提供归档副本 · ${selected.filename}`
            : "上传文件并创建独立归档"
        }
      >
        <div className="inline-form">
          <label>
            选择文件
            <input
              aria-label="选择归档文件"
              type="file"
              disabled={busy}
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
          </label>
          <button
            className="primary"
            disabled={busy || !file}
            onClick={() =>
              void run(async () => {
                if (!file) return;
                if (selected?.source_transfer_id && !binding)
                  throw new Error(
                    "原传输需要原设备凭据。请保持 DeviceLink 绑定，或回到原设备提供副本。",
                  );
                abort.current = new AbortController();
                await uploadArchiveFile(
                  api,
                  file,
                  abort.current.signal,
                  (done, total) => setProgress({ done, total }),
                  selected ?? undefined,
                  selected?.source_transfer_id ? binding?.token : undefined,
                );
                setNotice("归档副本已暂存并通过哈希校验；后台继续处理。");
                setSelected(null);
                setFile(null);
              })
            }
          >
            <Upload size={16} aria-hidden="true" />
            {busy ? "正在上传…" : "上传归档副本"}
          </button>
          <button disabled={!busy} onClick={() => abort.current?.abort()}>
            暂停上传
          </button>
          <button
            disabled={busy}
            onClick={() => {
              setSelected(null);
              setPlan(null);
              setFile(null);
            }}
          >
            创建新归档
          </button>
        </div>
        <div className="transfer-progress">
          <progress
            aria-label="归档上传进度"
            max={progress.total || 1}
            value={progress.done}
          />
          <span>
            {byteText(progress.done)} / {byteText(progress.total)} ·
            服务器已确认字节
          </span>
        </div>
      </Card>
      <Card title="我的归档任务">
        <div className="section-tools">
          <label>
            阶段筛选
            <select value={filter} onChange={(e) => setFilter(e.target.value)}>
              <option value="">全部阶段</option>
              {Object.entries(archiveStates).map(([key, name]) => (
                <option key={key} value={key}>
                  {name}
                </option>
              ))}
            </select>
          </label>
          <button onClick={() => void load()}>
            <RefreshCw size={16} aria-hidden="true" />
            刷新
          </button>
        </div>
        {!jobs.length ? (
          <Empty text="暂无归档任务。上传文件或在 DeviceLink 成功交付后查看独立任务。" />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>原文件 / 来源</th>
                  <th>真实状态</th>
                  <th>模型 / 目标目录</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {jobs
                  .filter((j) => !filter || j.state === filter)
                  .map((j) => (
                    <tr key={j.id}>
                      <td>
                        <strong>{j.filename}</strong>
                        <small>
                          {byteText(j.size_bytes)} ·{" "}
                          {j.source_transfer_id ? "DeviceLink" : "独立上传"}
                        </small>
                        <small className="break">{j.id}</small>
                      </td>
                      <td>
                        <Status active={j.state === "COMPLETED"}>
                          {archiveStates[j.state] ?? j.state}
                        </Status>
                        <small>{byteText(j.uploaded_bytes)} 已上传</small>
                        <small className="break">{j.error}</small>
                      </td>
                      <td>
                        {j.model ?? "尚未调用"}
                        <small>{j.plan?.target_directory ?? "等待规划"}</small>
                      </td>
                      <td>
                        <div className="row">
                          <button disabled={busy} onClick={() => pick(j)}>
                            查看 / 处理
                          </button>
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
                              重试
                            </button>
                          )}
                          {[
                            "WAITING_SOURCE",
                            "WAITING_UPLOAD",
                            "UPLOADING",
                            "STAGED",
                            "AWAITING_AI_CONSENT",
                            "PLAN_REVIEW_REQUIRED",
                          ].includes(j.state) && (
                            <button
                              disabled={busy}
                              onClick={() =>
                                void run(async () => {
                                  await api.request(
                                    `/archive/jobs/${j.id}/cancel`,
                                    "POST",
                                  );
                                })
                              }
                            >
                              取消归档
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      {selected && (
        <Card title="整理结果与人工确认">
          <p>
            原文件：{selected.filename} · 原分类：未分类 · 模型：
            {selected.model ?? "尚未调用"}
          </p>
          {plan ? (
            <form
              className="archive-grid"
              onSubmit={(e) => {
                e.preventDefault();
                void run(async () => {
                  await api.request(
                    `/archive/jobs/${selected.id}/plan`,
                    "PUT",
                    { ...plan, needs_user_review: false },
                  );
                  setNotice("计划已明确确认，后台将验证并归档。");
                  setSelected(null);
                });
              }}
            >
              {(
                [
                  ["category", "二次分类"],
                  ["subcategory", "子分类"],
                  ["suggested_filename", "建议文件名"],
                  ["target_directory", "正式目标目录"],
                  ["summary", "文件摘要"],
                  ["reason", "需要确认的原因"],
                ] as const
              ).map(([key, title]) => (
                <label key={key}>
                  {title}
                  <input
                    value={plan[key]}
                    disabled={selected.state !== "PLAN_REVIEW_REQUIRED" || busy}
                    onChange={(e) =>
                      setPlan({ ...plan, [key]: e.target.value })
                    }
                  />
                </label>
              ))}
              {selected.state === "PLAN_REVIEW_REQUIRED" && (
                <button className="primary" disabled={busy}>
                  确认并执行此计划
                </button>
              )}
            </form>
          ) : (
            <p className="muted">
              尚无有效 AI 计划：
              {selected.error || archiveStates[selected.state]}
              。请先提供副本及授权，真实 API 未授权时可由管理员选择明确的 Mock
              联调。
            </p>
          )}
          {selected.storage_key && (
            <p className="break">实际存储对象：{selected.storage_key}</p>
          )}
        </Card>
      )}
      <Card title="已归档文件 · 基础文件目录">
        {!files.length ? (
          <Empty text="尚无正式文件。校验、提交和目录发布完成后显示。" />
        ) : (
          <div className="archive-grid">
            {files.map((f) => (
              <article className="archive-file" key={f.id}>
                <Archive size={20} aria-hidden="true" />
                <h3>{f.filename}</h3>
                <p>
                  {f.category} · v{f.version} · {byteText(f.size_bytes)}
                </p>
                <p>{f.summary}</p>
                <p className="muted break">{f.target_directory}</p>
                <details>
                  <summary>存储与完整性</summary>
                  <p className="break">对象：{f.object_key}</p>
                  <p className="break">SHA-256：{f.sha256}</p>
                  <p>
                    {f.verification_level} · 目录版本 {f.revision_id}
                  </p>
                </details>
                <a className="save-link" href={`/api/library/${f.id}/content`}>
                  读取原始文件
                </a>
              </article>
            ))}
          </div>
        )}
      </Card>
    </>
  );
}
