import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { ArchivePage } from "./ArchivePage";
import { ArchiveAdmin } from "./ArchiveAdmin";
import { ApiClient } from "./api";

it("shows real archive states, requires explicit consent, and saves selected mode", async () => {
  const api = new ApiClient();
  const call = vi
    .spyOn(api, "request")
    .mockImplementation(async (path, method, body) => {
      if (path === "/archive/preferences")
        return method === "PUT"
          ? body
          : { auto_archive: false, ai_mode: "off", rules: "" };
      if (path === "/library") return [];
      if (path === "/archive/jobs")
        return [
          {
            id: "job-one",
            filename: "notes.txt",
            size_bytes: 8,
            state: "AWAITING_AI_CONSENT",
            uploaded_bytes: 8,
            source_transfer_id: null,
            plan: null,
            error: "",
            created_at: 1,
          },
        ];
      throw new Error("unexpected route");
    });
  render(<ArchivePage api={api} binding={null} />);
  await screen.findByText("notes.txt");
  expect(screen.getByLabelText("允许发送给云端 AI 的信息")).toHaveValue("off");
  expect(
    screen.getByText("等待 AI 授权", { selector: "span" }),
  ).toBeInTheDocument();
  await userEvent.selectOptions(
    screen.getByLabelText("允许发送给云端 AI 的信息"),
    "metadata",
  );
  await userEvent.click(screen.getByRole("button", { name: "保存授权设置" }));
  await waitFor(() =>
    expect(call).toHaveBeenCalledWith("/archive/preferences", "PUT", {
      auto_archive: false,
      ai_mode: "metadata",
      rules: "",
    }),
  );
  expect(screen.getByRole("progressbar")).toHaveAttribute("value", "0");
});

it("shows validated plan and submits explicit human confirmation", async () => {
  const api = new ApiClient();
  const plan = {
    category: "学习资料",
    subcategory: "Python",
    suggested_filename: "Python.txt",
    target_directory: "学习资料/Python",
    summary: "notes",
    reason: "主题不明确",
    keywords: [],
    related_file_ids: [],
    needs_user_review: true,
  };
  const call = vi
    .spyOn(api, "request")
    .mockImplementation(async (path, method) => {
      if (path === "/archive/preferences")
        return { auto_archive: false, ai_mode: "metadata", rules: "" };
      if (path === "/library") return [];
      if (method === "PUT") return {};
      return [
        {
          id: "job-review",
          filename: "old.txt",
          size_bytes: 8,
          state: "PLAN_REVIEW_REQUIRED",
          uploaded_bytes: 8,
          source_transfer_id: null,
          plan,
          error: "",
          created_at: 1,
          model: "mock-v2.2",
        },
      ];
    });
  render(<ArchivePage api={api} binding={null} />);
  await userEvent.click(
    await screen.findByRole("button", { name: "查看 / 处理" }),
  );
  expect(screen.getByLabelText("建议文件名")).toHaveValue("Python.txt");
  expect(screen.getByLabelText("需要确认的原因")).toHaveValue("主题不明确");
  await userEvent.click(
    screen.getByRole("button", { name: "确认并执行此计划" }),
  );
  await waitFor(() =>
    expect(call).toHaveBeenCalledWith("/archive/jobs/job-review/plan", "PUT", {
      ...plan,
      needs_user_review: false,
    }),
  );
});

it("admin reports local storage and unknown AI request without fabricating success", async () => {
  const api = new ApiClient(true);
  vi.spyOn(api, "request").mockImplementation(async (path) => {
    if (path === "/ai")
      return {
        provider: "deepseek",
        model: "deepseek-flash",
        key_configured: false,
        real_api_enabled: false,
        timeout: 30,
        retries: 1,
        concurrency: 1,
        max_tokens: 1024,
        monthly_budget: 0,
        request_limit: 0,
        input_per_million: 0,
        output_per_million: 0,
        price_version: "unconfigured",
        currency: "CNY",
        categories: ["其他"],
      };
    if (path === "/archive/settings")
      return {
        enabled: true,
        provider: "local",
        concurrency: 1,
        upload_concurrency: 2,
        cache_bytes: 100,
        max_file_bytes: 100,
        ttl_seconds: 60,
        lease_seconds: 60,
        backup_grace_seconds: 30,
        max_retries: 3,
      };
    if (path === "/archive/jobs") return [];
    if (path === "/ai/usage")
      return {
        items: [
          {
            id: "unknown-job",
            model: "deepseek-flash",
            status: "UNKNOWN",
            simulated: false,
            error: "NETWORK_OUTCOME_UNKNOWN",
            currency: "CNY",
            estimated_cost: 0,
            price_version: "test",
          },
        ],
        totals: [],
      };
    return {
      status: "本地存储适配器运行中，百度网盘未正式连接",
      files: 0,
      used_bytes: 0,
      temporary_objects: 0,
      reserved_bytes: 0,
      jobs: [],
      last_reconciliation: null,
    };
  });
  render(<ArchiveAdmin api={api} />);
  await screen.findByText("本地存储适配器运行中，百度网盘未正式连接");
  expect(screen.getByLabelText("模型 ID")).toHaveValue("deepseek-flash");
  expect(screen.getByLabelText("更新 API 密钥")).toHaveValue("");
  expect(screen.getByText(/NETWORK_OUTCOME_UNKNOWN/)).toBeInTheDocument();
});
