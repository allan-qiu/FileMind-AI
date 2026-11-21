import { describe, expect, it, vi } from "vitest";
import { ApiClient, ApiError } from "./api";
import { CatalogSession, type LibraryFile } from "./library";

const file = { file_id: "f", filename: "one.txt", revision: 1 } as LibraryFile;
describe("catalog session", () => {
  it("applies snapshot, repeated changes and tombstones without duplicate rows", async () => {
    const api = new ApiClient(),
      cache = new CatalogSession();
    vi.spyOn(api, "request")
      .mockResolvedValueOnce({
        items: [file],
        revision: 1,
        acl: "a",
        has_more: false,
        next_offset: 1,
      })
      .mockResolvedValueOnce({
        items: [
          {
            kind: "PATCH",
            file: { ...file, revision: 2, filename: "two.txt" },
          },
          { kind: "PATCH", file },
        ],
        revision: 2,
        acl: "a",
        has_more: false,
      })
      .mockResolvedValueOnce({
        items: [{ kind: "REMOVED", file_id: "f" }],
        revision: 3,
        acl: "a",
        has_more: false,
      });
    await cache.sync(api);
    await cache.sync(api);
    expect(cache.rows.size).toBe(1);
    expect(cache.rows.get("f")?.filename).toBe("two.txt");
    await cache.sync(api);
    expect(cache.rows.size).toBe(0);
  });
  it("restarts expired cursors and clears metadata on revoked authorization", async () => {
    const api = new ApiClient(),
      cache = new CatalogSession();
    cache.acl = "old";
    cache.revision = 9;
    vi.spyOn(api, "request")
      .mockRejectedValueOnce(new ApiError(409, "SNAPSHOT_REQUIRED"))
      .mockResolvedValueOnce({
        items: [file],
        revision: 10,
        acl: "new",
        has_more: false,
        next_offset: 1,
      })
      .mockRejectedValueOnce(new ApiError(401, "revoked"));
    await cache.sync(api);
    expect(cache.acl).toBe("new");
    await expect(cache.sync(api)).rejects.toThrow("revoked");
    expect(cache.rows.size).toBe(0);
    expect(cache.acl).toBe("");
  });
});
