import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { DeviceLinkPage } from "./DeviceLinkPage";
import { ApiClient } from "./api";

vi.mock("./devicelink", async (original) => {
  const real = await original<typeof import("./devicelink")>();
  return {
    ...real,
    sendFile: vi.fn(async (_token, _file, _receiver, signal, created) => {
      created("cancel-test");
      return await new Promise((_resolve, reject) => {
        signal.addEventListener("abort", () => reject(new Error("aborted")), {
          once: true,
        });
      });
    }),
  };
});

it("cancels the active request without racing a failure report or showing completion", async () => {
  const requests: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    requests.push(path);
    if (path.endsWith("/peers"))
      return new Response(
        JSON.stringify([{ id: "peer", name: "接收设备", platform: "windows" }]),
      );
    return new Response(path.endsWith("/inbox") ? "[]" : "{}");
  });
  render(
    <DeviceLinkPage
      api={new ApiClient()}
      binding={{ id: "sender", token: "test-device-token" }}
      bind={vi.fn()}
    />,
  );
  await screen.findByRole("option", { name: "接收设备 (windows)" });
  await userEvent.selectOptions(
    screen.getByLabelText("在线可接收设备"),
    "peer",
  );
  await userEvent.upload(
    screen.getByLabelText("选择发送文件"),
    new File(["fixture"], "test.txt"),
  );
  await userEvent.click(screen.getByRole("button", { name: "发起发送" }));
  await screen.findByText("请求已发出，等待接收端同意…");
  await userEvent.click(screen.getByRole("button", { name: "取消当前传输" }));
  await waitFor(() =>
    expect(requests.some((path) => path.endsWith("/cancel"))).toBe(true),
  );
  expect(requests.some((path) => path.endsWith("/fail"))).toBe(false);
  expect(screen.queryByText(/传输已完成/)).not.toBeInTheDocument();
});
