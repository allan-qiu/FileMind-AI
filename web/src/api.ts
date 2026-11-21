export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export class ApiClient {
  csrf = "";
  constructor(public admin = false) {}
  async request<T>(path: string, method = "GET", body?: unknown): Promise<T> {
    const prefix = this.admin ? "/api/admin" : "/api";
    const response = await fetch(prefix + path, {
      method,
      credentials: "same-origin",
      headers: {
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
        ...(method !== "GET" ? { "X-CSRF-Token": this.csrf } : {}),
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: AbortSignal.timeout(10000),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = Array.isArray(data.detail)
        ? data.detail.map((item: { msg: string }) => item.msg).join("；")
        : data.detail;
      throw new ApiError(
        response.status,
        detail || `请求失败（${response.status}）`,
      );
    }
    return data as T;
  }
}

export const errorMessage = (error: unknown) =>
  error instanceof Error ? error.message : "请求失败，请重试。";
