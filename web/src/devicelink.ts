import { sha256 } from "@noble/hashes/sha2.js";
import { bytesToHex } from "@noble/hashes/utils.js";

export interface Transfer {
  id: string;
  sender_id: string;
  receiver_id: string;
  filename: string;
  size_bytes: number;
  expected_hash: string;
  state: string;
  bytes_forwarded: number;
  bytes_received: number;
  created_at: number;
  completed_at: number | null;
  sender_name: string;
  receiver_name: string;
  confirmation: string | null;
  error: string;
  username?: string;
}
export interface Peer {
  id: string;
  name: string;
  platform: string;
}
export interface LinkConfig {
  chunk_bytes: number;
  max_bytes: number;
  ttl: number;
  resumable: boolean;
}
export const terminal = new Set([
  "COMPLETED",
  "FAILED",
  "CANCELLED",
  "REJECTED",
  "EXPIRED",
]);
export const stateNames: Record<string, string> = {
  CREATED: "已创建",
  WAITING_RECEIVER: "等待接收",
  ACCEPTED: "已接受",
  CONNECTING: "正在连接",
  TRANSFERRING: "正在传输",
  VERIFYING: "等待校验确认",
  COMPLETED: "已完成",
  REJECTED: "已拒绝",
  CANCELLED: "已取消",
  FAILED: "失败",
  EXPIRED: "已过期",
};
export const byteText = (n: number) =>
  n < 1024
    ? `${n} B`
    : n < 1048576
      ? `${(n / 1024).toFixed(1)} KiB`
      : `${(n / 1048576).toFixed(2)} MiB`;

export async function deviceApi<T>(
  token: string,
  path: string,
  method = "GET",
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetch("/api/transfers" + path, {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: signal
      ? AbortSignal.any([signal, AbortSignal.timeout(45000)])
      : AbortSignal.timeout(10000),
  });
  const value = await response.json().catch(() => ({}));
  if (!response.ok)
    throw new Error(
      typeof value.detail === "string"
        ? value.detail
        : `请求失败 ${response.status}`,
    );
  return value as T;
}

export async function hashFile(file: File, signal?: AbortSignal) {
  const hash = sha256.create();
  for (let offset = 0; offset < file.size; offset += 262144) {
    signal?.throwIfAborted();
    hash.update(
      new Uint8Array(await file.slice(offset, offset + 262144).arrayBuffer()),
    );
  }
  signal?.throwIfAborted();
  return bytesToHex(hash.digest());
}

export async function sendFile(
  token: string,
  file: File,
  receiverId: string,
  signal: AbortSignal,
  created: (id: string) => void,
  progress: (done: number, total: number) => void,
) {
  const config = await deviceApi<LinkConfig>(token, "/config");
  if (file.size > config.max_bytes) throw new Error("文件超过服务器大小限制。");
  const digest = await hashFile(file, signal);
  let row = await deviceApi<Transfer>(
    token,
    "",
    "POST",
    {
      receiver_id: receiverId,
      filename: file.name,
      size_bytes: file.size,
      sha256: digest,
      request_key: crypto.randomUUID(),
    },
    signal,
  );
  created(row.id);
  const deadline = Date.now() + config.ttl * 1000;
  while (row.state === "WAITING_RECEIVER") {
    signal.throwIfAborted();
    if (Date.now() >= deadline) throw new Error("等待接收方超时。");
    await new Promise((resolve) => setTimeout(resolve, 200));
    row = await deviceApi<Transfer>(
      token,
      `/${row.id}`,
      "GET",
      undefined,
      signal,
    );
  }
  if (!["ACCEPTED", "CONNECTING"].includes(row.state))
    throw new Error(`传输${stateNames[row.state] ?? row.state}`);
  for (
    let seq = 0;
    seq < Math.max(1, Math.ceil(file.size / config.chunk_bytes));
    seq++
  ) {
    signal.throwIfAborted();
    const block = await file
      .slice(seq * config.chunk_bytes, (seq + 1) * config.chunk_bytes)
      .arrayBuffer();
    const response = await fetch(`/api/transfers/${row.id}/chunks/${seq}`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/octet-stream",
      },
      body: block,
      signal: AbortSignal.any([signal, AbortSignal.timeout(45000)]),
    });
    if (!response.ok)
      throw new Error(
        (await response.json().catch(() => ({}))).detail || "文件块发送失败。",
      );
    progress(Math.min(file.size, (seq + 1) * config.chunk_bytes), file.size);
  }
  while (!terminal.has(row.state)) {
    signal.throwIfAborted();
    if (Date.now() >= deadline) throw new Error("接收端确认超时。");
    row = await deviceApi<Transfer>(
      token,
      `/${row.id}`,
      "GET",
      undefined,
      signal,
    );
    if (!terminal.has(row.state))
      await new Promise((resolve) => setTimeout(resolve, 200));
  }
  if (row.state !== "COMPLETED")
    throw new Error(`${stateNames[row.state]}：${row.error}`);
  return row;
}

export async function receiveFile(
  token: string,
  row: Transfer,
  signal: AbortSignal,
  progress: (done: number, total: number) => void,
) {
  if (row.size_bytes > 32 * 1024 * 1024)
    throw new Error(
      "本浏览器接收上限为 32 MiB，请使用 Windows 客户端或减小文件。",
    );
  const config = await deviceApi<LinkConfig>(token, "/config");
  await deviceApi(token, `/${row.id}/accept`, "POST", undefined, signal);
  const buffers: ArrayBuffer[] = [],
    hash = sha256.create();
  let received = 0;
  for (
    let seq = 0;
    seq < Math.max(1, Math.ceil(row.size_bytes / config.chunk_bytes));
    seq++
  ) {
    signal.throwIfAborted();
    const response = await fetch(`/api/transfers/${row.id}/chunks/${seq}`, {
      headers: { Authorization: `Bearer ${token}` },
      signal: AbortSignal.any([signal, AbortSignal.timeout(45000)]),
    });
    if (!response.ok)
      throw new Error(
        (await response.json().catch(() => ({}))).detail || "文件块接收失败。",
      );
    const expected = Math.min(
      config.chunk_bytes,
      Math.max(0, row.size_bytes - seq * config.chunk_bytes),
    );
    const reader = response.body?.getReader();
    if (!reader) throw new Error("浏览器不支持响应流。");
    let count = 0;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      signal.throwIfAborted();
      count += value.byteLength;
      if (count > expected) {
        await reader.cancel();
        throw new Error("文件块超过声明长度。");
      }
      const copy = new Uint8Array(value.byteLength);
      copy.set(value);
      buffers.push(copy.buffer);
      hash.update(copy);
      received += value.byteLength;
      progress(received, row.size_bytes);
    }
    if (count !== expected) throw new Error("文件块长度不一致。");
  }
  const digest = bytesToHex(hash.digest());
  if (received !== row.size_bytes || digest !== row.expected_hash)
    throw new Error("接收 SHA-256 或文件大小不匹配。");
  return {
    id: row.id,
    filename: row.filename,
    blob: new Blob(buffers, { type: "application/octet-stream" }),
    sha256: digest,
  };
}
