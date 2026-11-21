import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { ApiClient } from "./api";
import { ShareCreate, ShareCenter } from "./ShareCenter";

it("creates a pinned version with independent quota/password and a copyable link", async () => {
  const api = new ApiClient();
  const request = vi
    .spyOn(api, "request")
    .mockImplementation(async (path, method, body) => {
      if (path === "/shares/temporary/objects") return [];
      if (path === "/shares" && method === "POST")
        return {
          share_id: "s1",
          share_url: "/s/#synthetic-test-token",
          ...(body as object),
        };
      throw new Error("Unexpected request " + path);
    });
  render(<ShareCreate api={api} versionId="version-one" />);
  fireEvent.change(screen.getByLabelText("最多下载次数"), {
    target: { value: "1" },
  });
  fireEvent.change(screen.getByLabelText("访问密码（可选）"), {
    target: { value: "synthetic-pass" },
  });
  fireEvent.click(screen.getByRole("button", { name: "创建分享链接" }));
  await screen.findByLabelText("新分享链接（仅此时显示，请保存）");
  expect(request).toHaveBeenCalledWith(
    "/shares",
    "POST",
    expect.objectContaining({
      file_version_id: "version-one",
      max_downloads: 1,
      password: "synthetic-pass",
    }),
  );
  expect(screen.getByLabelText("访问密码（可选）")).toHaveValue("");
});

it("shows independent link rows and revokes only the selected share", async () => {
  const api = new ApiClient(true);
  const rows = ["a", "b"].map((id, i) => ({
    share_id: id,
    display_name: "same.txt",
    status: "ACTIVE",
    max_downloads: i + 1,
    completed_downloads: 0,
    reserved_downloads: 0,
    remaining_downloads: i + 1,
    created_at: 100,
    expires_at: 200,
    size_bytes: 10,
  }));
  const request = vi
    .spyOn(api, "request")
    .mockImplementation(async (path, method) => {
      if (path.startsWith("/shares?")) return rows;
      if (path === "/shares/a/revoke" && method === "POST") {
        rows[0].status = "REVOKED";
        return rows[0];
      }
      throw new Error(path);
    });
  render(<ShareCenter api={api} admin />);
  await waitFor(() =>
    expect(screen.getAllByRole("button", { name: "撤销链接" })).toHaveLength(2),
  );
  fireEvent.click(screen.getAllByRole("button", { name: "撤销链接" })[0]);
  await waitFor(() =>
    expect(request).toHaveBeenCalledWith("/shares/a/revoke", "POST"),
  );
  expect(request).not.toHaveBeenCalledWith("/shares/b/revoke", "POST");
});
