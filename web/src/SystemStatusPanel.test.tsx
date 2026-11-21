import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { ApiClient } from "./api";
import { SystemStatusPanel } from "./SystemStatusPanel";

it("separates configured state from live acceptance and clears stale facts after an error", async () => {
  const api = new ApiClient(true);
  const request = vi.spyOn(api, "request").mockResolvedValue({
    ai: {
      provider: "mock",
      model: "deepseek-flash",
      configuration_status: "已配置",
      connection_status: "未验证",
      selected_mode: "Mock 模拟",
      paid_calls_enabled: false,
      validation: {
        states: { API_KEY_CONFIGURED: "CONFIGURED", LIVE_CLASSIFICATION_TESTED: "PENDING" },
        last_live_success_at: null,
        live_archives: 0,
        counts: { provider_attempts: 0, cache_reuses: 0, errors: 0 },
        scope: "本数据库所选模型的历史记录",
      },
      usage: [],
    },
    baidu: { status: "没有正式授权", last_error: "", writes_enabled: false },
    storage: { write_provider: "local", read_cache_reserved_bytes: 0 },
    archive_jobs: { COMPLETED: 1 },
    archive_failure_rate: 0,
    counts: {
      library_files: 1,
      library_versions: 1,
      share_links: 0,
      completed_share_downloads: 0,
      share_wire_bytes: 0,
      temporary_reserved_bytes: 0,
      temporary_share_bytes: 0,
    },
    database: { schema: 11, journal_mode: "wal", read_query: "passed" },
    acceptance: { production: "未验证" },
  });
  render(<SystemStatusPanel api={api} />);
  await screen.findByText("没有正式授权");
  expect(screen.getByText("production：未验证")).toBeInTheDocument();
  expect(screen.getByRole("region", { name: "DeepSeek 验收记录" })).toHaveTextContent("PENDING");
  expect(screen.getByText("CONFIGURED")).toBeInTheDocument();
  request.mockRejectedValueOnce(new Error("中央服务断开"));
  await userEvent.click(screen.getByRole("button", { name: "刷新集成状态" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("中央服务断开");
  expect(screen.queryByText("已配置")).not.toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "DeepSeek 验收记录" })).not.toBeInTheDocument();
});
