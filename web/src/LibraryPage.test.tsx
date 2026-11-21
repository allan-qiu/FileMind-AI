import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { ApiClient } from "./api";
import { LibraryPage } from "./LibraryPage";

it("renders authorized database results, details and a real rename request", async () => {
  const api = new ApiClient();
  const file = {
    file_id: "f1",
    filename: "notes.txt",
    category: "学习资料",
    directory: "学习",
    favorite: 0,
    deleted_at: null,
    revision: 1,
    version_id: "v1",
    version: 1,
    size_bytes: 12,
    summary: "Synthetic",
    updated_at: 1,
    archived_at: 1,
    provider: "local",
    storage_directory: "学习",
    sha256: "a".repeat(64),
  };
  const request = vi
    .spyOn(api, "request")
    .mockImplementation(async (path, method) => {
      if (path.startsWith("/library/snapshot"))
        return {
          items: [file],
          revision: 1,
          acl: "a",
          has_more: false,
          next_offset: 1,
        } as never;
      if (path.startsWith("/library/changes"))
        return { items: [], revision: 1, acl: "a", has_more: false } as never;
      if (path.startsWith("/library/search"))
        return { items: [file], total: 1 } as never;
      if (path === "/library/folders")
        return {
          items: [{ path: "学习", count: 1 }],
          categories: ["学习资料"],
        } as never;
      if (path === "/library/conflicts") return [] as never;
      if (path === "/library/f1" && method === "PATCH") return file as never;
      if (path === "/library/f1")
        return {
          ...file,
          versions: [
            {
              id: "v1",
              version: 1,
              filename: "notes.txt",
              size_bytes: 12,
              created_at: 1,
              sha256: file.sha256,
            },
          ],
          sources: [],
          history: [],
        } as never;
      throw new Error("Unexpected request " + path);
    });
  render(<LibraryPage api={api} />);
  await userEvent.click(
    await screen.findByRole("button", { name: "notes.txt" }),
  );
  await screen.findByText("文件详情 · notes.txt");
  await userEvent.clear(screen.getByLabelText("名称", { exact: true }));
  await userEvent.type(
    screen.getByLabelText("名称", { exact: true }),
    "renamed.txt",
  );
  await userEvent.click(
    screen.getByRole("button", { name: "保存名称 / 分类 / 目录" }),
  );
  await waitFor(() =>
    expect(request).toHaveBeenCalledWith(
      "/library/f1",
      "PATCH",
      expect.objectContaining({
        filename: "renamed.txt",
        expected_revision: 1,
      }),
    ),
  );
});
