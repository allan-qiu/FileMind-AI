import { ApiClient, ApiError } from "./api";

export interface LibraryFile {
  file_id: string;
  user_id: string;
  filename: string;
  category: string;
  directory: string;
  favorite: number;
  deleted_at: number | null;
  workspace_id: string | null;
  revision: number;
  version_id: string;
  version: number;
  size_bytes: number;
  sha256: string;
  summary: string;
  updated_at: number;
  archived_at: number;
  storage_directory: string;
  provider: string;
}
export interface Version {
  id: string;
  version: number;
  filename: string;
  size_bytes: number;
  sha256: string;
  created_at: number;
  device_id: string | null;
  actor_id: string | null;
  base_version_id: string | null;
}
export interface Detail extends LibraryFile {
  versions: Version[];
  sources: Record<string, unknown>[];
  history: {
    id: string;
    kind: string;
    before_json: string;
    created_at: number;
    user_id: string;
  }[];
}
export interface Conflict {
  id: string;
  file_id: string;
  filename: string;
  base_version_id: string;
  current_version_id: string;
  candidate_version_id: string;
  candidate_size: number;
  candidate_time: number;
  device_id: string | null;
  actor_id: string;
  reason: string;
  state: string;
}
type Change = {
  revision: number;
  kind: string;
  file?: LibraryFile;
  file_id?: string;
  folder?: { path: string };
};
type Page = {
  receipt?: string;
  items: (LibraryFile | Change)[];
  revision: number;
  acl: string;
  has_more: boolean;
  next_offset: number;
};

export class CatalogSession {
  clientId = "web:" + crypto.randomUUID();
  revision = 0;
  acl = "";
  rows = new Map<string, LibraryFile>();
  clear() {
    this.rows.clear();
    this.revision = 0;
    this.acl = "";
  }
  async sync(api: ApiClient) {
    let snapshot = !this.acl,
      offset = 0,
      at: number | undefined;
    let revision = this.revision,
      acl = this.acl;
    let pending: (LibraryFile | Change)[] = [];
    try {
      for (let i = 0; i < 10000; i++) {
        const query = new URLSearchParams({
          limit: "200",
          client_id: this.clientId,
        });
        if (snapshot) {
          query.set("offset", String(offset));
          if (at !== undefined) query.set("at", String(at));
        } else {
          query.set("after", String(revision));
          query.set("acl", acl);
        }
        let page: Page;
        try {
          page = await api.request<Page>(
            `/library/${snapshot ? "snapshot" : "changes"}?${query}`,
          );
        } catch (error) {
          if (error instanceof ApiError && error.status === 409) {
            snapshot = true;
            pending = [];
            offset = 0;
            at = undefined;
            continue;
          }
          throw error;
        }
        if (snapshot && at !== undefined && page.acl !== acl) {
          pending = [];
          offset = 0;
          at = undefined;
          continue;
        }
        pending.push(...page.items);
        revision = page.revision;
        acl = page.acl;
        offset = page.next_offset;
        at = revision;
        if (page.has_more) continue;
        if (snapshot) this.rows.clear();
        for (const item of pending) {
          if ("kind" in item) {
            if (item.kind === "REMOVED" && item.file_id)
              this.rows.delete(item.file_id);
            if (item.file) this.apply(item.file);
          } else this.apply(item);
        }
        const changed = revision !== this.revision || acl !== this.acl;
        this.revision = revision;
        this.acl = acl;
        if (page.receipt)
          await api.request("/library/sync/ack", "POST", {
            client_id: this.clientId,
            receipt: page.receipt,
          });
        return changed;
      }
      throw new Error("目录持续变化，请稍后重试");
    } catch (error) {
      if (error instanceof ApiError && [401, 403].includes(error.status))
        this.clear();
      throw error;
    }
  }
  apply(file: LibraryFile) {
    if ((this.rows.get(file.file_id)?.revision ?? -1) <= file.revision)
      this.rows.set(file.file_id, file);
  }
}

export const operation = (
  file: LibraryFile,
  values: Record<string, unknown> = {},
) => ({
  expected_revision: file.revision,
  idempotency_key: crypto.randomUUID(),
  ...values,
});

export async function verifiedDownload(
  version: Version,
  signal: AbortSignal,
  progress: (n: number) => void,
) {
  if (version.size_bytes > 32 * 1024 ** 2)
    throw new Error("超过页面内校验上限，请使用浏览器原生下载");
  const response = await fetch(`/api/library/versions/${version.id}/content`, {
    credentials: "same-origin",
    signal,
  });
  if (!response.ok || !response.body)
    throw new Error(`下载失败（${response.status}）`);
  const reader = response.body.getReader(),
    chunks: Uint8Array<ArrayBuffer>[] = [];
  let size = 0;
  try {
    while (true) {
      signal.throwIfAborted();
      const { value, done } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > version.size_bytes) throw new Error("下载超过声明大小");
      chunks.push(new Uint8Array(value));
      progress(size);
    }
    const blob = new Blob(chunks);
    const digest = Array.from(
      new Uint8Array(
        await crypto.subtle.digest("SHA-256", await blob.arrayBuffer()),
      ),
      (b) => b.toString(16).padStart(2, "0"),
    ).join("");
    signal.throwIfAborted();
    if (size !== version.size_bytes || digest !== version.sha256)
      throw new Error("下载完整性校验失败");
    return blob;
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
