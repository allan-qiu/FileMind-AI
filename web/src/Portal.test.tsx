import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { Portal } from "./Portal";
import { ApiClient, ApiError } from "./api";

const user = {
  id: "test",
  username: "alice_test",
  display_name: "爱丽丝",
  role: "user",
  enabled: true,
  created_at: 1700000000,
  quota_bytes: 1048576,
};
const config = { registration_open: true, announcement: "", milestones: {} };
function mockApi(loggedIn = false) {
  return vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(async (input, init) => {
      const path = String(input);
      if (path.endsWith("/public/config"))
        return new Response(JSON.stringify(config));
      if (path.endsWith("/auth/session"))
        return new Response(
          JSON.stringify(
            loggedIn
              ? { user, csrf_token: "test-csrf" }
              : { detail: "请先登录。" },
          ),
          { status: loggedIn ? 200 : 401 },
        );
      if (path.endsWith("/auth/login"))
        return new Response(JSON.stringify({ user, csrf_token: "test-csrf" }));
      if (path.endsWith("/devices") || path.endsWith("/workspaces"))
        return new Response("[]");
      return new Response(
        JSON.stringify({ detail: "需要独立的系统管理员登录。" }),
        { status: 403 },
      );
    });
}
describe("FileMind portals", () => {
  it("renders registration and preserves visible field labels", async () => {
    mockApi();
    render(<Portal />);
    await screen.findByRole("heading", { name: "登录工作空间" });
    await userEvent.click(
      await screen.findByRole("button", { name: "创建账户" }),
    );
    expect(screen.getByLabelText("显示名称")).toBeRequired();
    expect(screen.getByLabelText("密码")).toHaveAttribute("minLength", "10");
  });
  it("keeps admin entry separate with no public registration", async () => {
    mockApi();
    render(<Portal admin />);
    await screen.findByRole("heading", { name: "管理员登录" });
    expect(
      screen.queryByRole("button", { name: "创建账户" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /用户工作空间/ })).toHaveAttribute(
      "href",
      "/app/",
    );
  });
  it("shows available services and loads real empty devices", async () => {
    mockApi(true);
    render(<Portal />);
    await screen.findByText("已授权空间");
    expect(screen.queryByText("待开发")).not.toBeInTheDocument();
    expect(screen.getAllByText("已开放")).toHaveLength(4);
    await userEvent.click(screen.getByRole("button", { name: "我的设备" }));
    await screen.findByText("还没有登记设备。");
    expect(screen.queryByText("模拟设备")).not.toBeInTheDocument();
  });
  it("shows a real backend rejection without claiming success", async () => {
    const fetchMock = mockApi();
    render(<Portal admin />);
    await screen.findByRole("heading", { name: "管理员登录" });
    fetchMock.mockImplementationOnce(
      async () =>
        new Response(JSON.stringify({ detail: "没有系统管理员权限。" }), {
          status: 403,
        }),
    );
    await userEvent.type(screen.getByLabelText("用户名"), "alice_test");
    await userEvent.type(screen.getByLabelText("密码"), "incorrect-password");
    await userEvent.click(screen.getByRole("button", { name: "登录" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "没有系统管理员权限。",
    );
  });
  it("only stores visual preferences in localStorage", async () => {
    mockApi();
    render(<Portal />);
    await screen.findByRole("heading", { name: "登录工作空间" });
    await userEvent.click(screen.getByRole("button", { name: "切换主题" }));
    expect(window.localStorage.length).toBe(1);
    expect(window.localStorage.key(0)).toBe("filemind-theme");
  });
});
describe("API boundary", () => {
  it("sends csrf and cookie credentials and surfaces structured validation", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        new Response(
          JSON.stringify({ detail: [{ msg: "密码至少需要 10 个字符。" }] }),
          { status: 422 },
        ),
      );
    const api = new ApiClient(true);
    api.csrf = "csrf-for-test";
    await expect(api.request("/settings", "PUT", {})).rejects.toBeInstanceOf(
      ApiError,
    );
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/admin/settings",
      expect.objectContaining({
        credentials: "same-origin",
        headers: expect.objectContaining({ "X-CSRF-Token": "csrf-for-test" }),
      }),
    );
  });
});
