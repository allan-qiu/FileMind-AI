import { sha256 } from "@noble/hashes/sha2.js";
import { ApiClient } from "./api";

export interface ShareLink {
  share_id: string;
  display_name: string;
  source_type: string;
  status: string;
  max_downloads: number;
  completed_downloads: number;
  reserved_downloads: number;
  remaining_downloads: number;
  expires_at: number;
  created_at: number;
  share_url?: string;
  file_version_id?: string;
  temporary_object_id?: string;
  size_bytes: number | null;
}
export interface TemporaryObject {
  object_id: string;
  original_filename: string;
  file_size: number;
  deletion_status: string;
  active_references: number;
}
export const hex = (bytes: Uint8Array) =>
  Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
export const secret = () => hex(crypto.getRandomValues(new Uint8Array(32)));
export async function hashFile(
  file: File,
  signal: AbortSignal,
  progress: (n: number) => void,
) {
  const hash = sha256.create();
  for (let offset = 0; offset < file.size; offset += 262144) {
    signal.throwIfAborted();
    hash.update(
      new Uint8Array(await file.slice(offset, offset + 262144).arrayBuffer()),
    );
    progress(Math.min(file.size, offset + 262144));
  }
  return hex(hash.digest());
}
export async function temporaryUpload(
  api: ApiClient,
  file: File,
  signal: AbortSignal,
  progress: (n: number) => void,
) {
  const sha = await hashFile(file, signal, () => {});
  const up = await api.request<{ upload_id: string; chunk_bytes: number }>(
    "/shares/temporary/uploads",
    "POST",
    { filename: file.name, size_bytes: file.size, sha256: sha },
  );
  try {
    for (let offset = 0; offset < file.size; offset += up.chunk_bytes) {
      signal.throwIfAborted();
      const r = await fetch(
        `/api/shares/temporary/uploads/${up.upload_id}/chunks/${offset}`,
        {
          method: "PUT",
          credentials: "same-origin",
          headers: {
            "X-CSRF-Token": api.csrf,
            "Content-Type": "application/octet-stream",
          },
          body: file.slice(offset, offset + up.chunk_bytes),
          signal,
        },
      );
      if (!r.ok) throw new Error((await r.json()).detail || "上传失败");
      progress(Math.min(file.size, offset + up.chunk_bytes));
    }
    return await api.request<{ object_id: string }>(
      `/shares/temporary/uploads/${up.upload_id}/complete`,
      "POST",
    );
  } catch (error) {
    await api
      .request(`/shares/temporary/uploads/${up.upload_id}/cancel`, "POST")
      .catch(() => {});
    throw error;
  }
}
