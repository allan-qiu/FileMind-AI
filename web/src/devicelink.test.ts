import { File as NativeFile } from "node:buffer";
import { createHash } from "node:crypto";
import { describe, expect, it, vi } from "vitest";
import { hashFile, receiveFile, sendFile, type Transfer } from "./devicelink";

const digest = (value: Uint8Array) =>
  createHash("sha256").update(value).digest("hex");
const config = {
  chunk_bytes: 4096,
  max_bytes: 1048576,
  ttl: 30,
  resumable: false,
};
const json = (value: unknown) => new Response(JSON.stringify(value));

describe("DeviceLink bounded byte protocol", () => {
  it("hashes actual standard File slices incrementally including empty files", async () => {
    const data = new Uint8Array(700000).fill(42);
    const file = new NativeFile([data], "payload.bin") as unknown as File;
    const slice = vi.spyOn(file, "slice");
    expect(await hashFile(file)).toBe(digest(data));
    expect(slice).toHaveBeenCalledTimes(3);
    expect(
      await hashFile(new NativeFile([], "empty.bin") as unknown as File),
    ).toBe(digest(new Uint8Array()));
  });
  it("receives and verifies bytes but does not forge a browser-save acknowledgement", async () => {
    const data = new Uint8Array([1, 2, 3]);
    const calls: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const path = String(input);
      calls.push(path);
      if (path.endsWith("/config")) return json(config);
      if (path.endsWith("/accept")) return json({ state: "ACCEPTED" });
      return new Response(data);
    });
    const row = {
      id: "id",
      filename: "file.bin",
      size_bytes: 3,
      expected_hash: digest(data),
    } as Transfer;
    const received = await receiveFile(
      "test-only-token",
      row,
      new AbortController().signal,
      () => {},
    );
    expect(received.sha256).toBe(row.expected_hash);
    expect(received.blob.size).toBe(3);
    expect(calls.some((path) => path.endsWith("/ack"))).toBe(false);
  });
  it("rejects corrupted bytes before allowing confirmation", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) =>
      String(input).endsWith("/config")
        ? json(config)
        : String(input).endsWith("/accept")
          ? json({})
          : new Response(new Uint8Array([4, 5, 6])),
    );
    const row = {
      id: "id",
      filename: "file.bin",
      size_bytes: 3,
      expected_hash: digest(new Uint8Array([1, 2, 3])),
    } as Transfer;
    await expect(
      receiveFile("test", row, new AbortController().signal, () => {}),
    ).rejects.toThrow("SHA-256");
  });
  it("sends actual bytes with device credentials and waits for receiver completion", async () => {
    const payload = new Uint8Array([7, 8, 9]);
    let transmitted: ArrayBuffer | null = null;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const path = String(input);
      if (path.endsWith("/config")) return json(config);
      if (path.endsWith("/chunks/0")) {
        transmitted = init?.body as ArrayBuffer;
        return json({ next_seq: 1 });
      }
      if (init?.method === "POST")
        return json({ id: "test-id", state: "ACCEPTED" });
      return json({
        id: "test-id",
        state: "COMPLETED",
        confirmation: "browser_received",
      });
    });
    const row = await sendFile(
      "test",
      new NativeFile([payload], "file.bin") as unknown as File,
      "receiver",
      new AbortController().signal,
      () => {},
      () => {},
    );
    expect(row.state).toBe("COMPLETED");
    expect(new Uint8Array(transmitted!)).toEqual(payload);
  });
});
