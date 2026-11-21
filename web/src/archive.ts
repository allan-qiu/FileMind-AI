import { ApiClient } from "./api";
import { hashFile } from "./devicelink";

export interface Plan {
  category: string;
  subcategory: string;
  suggested_filename: string;
  target_directory: string;
  summary: string;
  keywords: string[];
  related_file_ids: string[];
  needs_user_review: boolean;
  reason: string;
}
export interface ArchiveJob {
  id: string;
  user_id: string;
  filename: string;
  size_bytes: number;
  sha256: string;
  state: string;
  uploaded_bytes: number;
  source_transfer_id: string | null;
  primary_owner: string | null;
  backup_owner: string | null;
  upload_owner: string | null;
  lease_expires_at: number | null;
  plan: Plan | null;
  model: string | null;
  storage_key: string | null;
  error: string;
  retry_count: number;
  created_at: number;
  completed_at: number | null;
  username?: string;
}
export interface Preferences {
  auto_archive: boolean;
  ai_mode: string;
  rules: string;
}
export const archiveStates: Record<string, string> = {
  CREATED: "已创建",
  WAITING_SOURCE: "等待设备副本",
  WAITING_UPLOAD: "等待上传",
  UPLOADING: "正在上传",
  STAGED: "暂存已校验",
  AWAITING_AI_CONSENT: "等待 AI 授权",
  AI_PLANNING: "AI 正在整理",
  PLAN_REVIEW_REQUIRED: "需要人工确认",
  READY_TO_ARCHIVE: "计划已确认",
  ARCHIVING: "写入正式存储",
  VERIFYING_STORAGE: "核对正式文件",
  COMMITTING_METADATA: "发布文件目录",
  COMPLETED: "已归档",
  RETRY_WAIT: "等待重试",
  FAILED: "失败",
  CANCELLED: "已取消",
  EXPIRED: "已过期",
};

export async function archiveRequest<T>(
  api: ApiClient,
  path: string,
  method = "GET",
  body?: unknown,
  token?: string,
) {
  if (!token) return api.request<T>(path, method, body);
  const response = await fetch("/api" + path, {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(15000),
  });
  const value = await response.json();
  if (!response.ok)
    throw new Error(
      typeof value.detail === "string" ? value.detail : "归档请求失败",
    );
  return value as T;
}

export async function uploadArchiveFile(
  api: ApiClient,
  file: File,
  signal: AbortSignal,
  progress: (done: number, total: number) => void,
  job?: ArchiveJob,
  token?: string,
) {
  const digest = await hashFile(file, signal);
  if (job && (job.sha256 !== digest || job.size_bytes !== file.size))
    throw new Error("副本与已交付文件不一致。");
  const row =
    job ??
    (await archiveRequest<ArchiveJob>(
      api,
      "/archive/jobs",
      "POST",
      {
        filename: file.name,
        size_bytes: file.size,
        sha256: digest,
        idempotency_key: crypto.randomUUID(),
      },
      token,
    ));
  signal.throwIfAborted();
  const lease = await archiveRequest<{
    lease_token: string;
    offset: number;
    chunk_bytes: number;
  }>(api, `/archive/jobs/${row.id}/claim`, "POST", undefined, token);
  let offset = lease.offset;
  progress(offset, file.size);
  const headers = {
    "X-Archive-Lease": lease.lease_token,
    "X-CSRF-Token": api.csrf,
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
  while (offset < file.size) {
    signal.throwIfAborted();
    const response = await fetch(
      `/api/archive/jobs/${row.id}/chunks/${offset}`,
      {
        method: "PUT",
        credentials: "same-origin",
        headers: { ...headers, "Content-Type": "application/octet-stream" },
        body: file.slice(offset, offset + lease.chunk_bytes),
        signal: AbortSignal.any([signal, AbortSignal.timeout(45000)]),
      },
    );
    const value = await response.json();
    if (!response.ok) throw new Error(value.detail || "归档块上传失败");
    if (value.offset <= offset) throw new Error("服务器未确认上传偏移");
    offset = value.offset;
    progress(offset, file.size);
  }
  signal.throwIfAborted();
  const finish = await fetch(`/api/archive/jobs/${row.id}/finish`, {
    method: "POST",
    credentials: "same-origin",
    headers,
    signal: AbortSignal.any([signal, AbortSignal.timeout(45000)]),
  });
  const result = await finish.json();
  if (!finish.ok) throw new Error(result.detail || "暂存校验失败");
  return result as ArchiveJob;
}
