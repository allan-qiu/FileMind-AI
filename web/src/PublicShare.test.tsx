import {
  render,
  screen,
  fireEvent,
  waitFor,
  act,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { PublicShare } from "./PublicShare";

afterEach(() => vi.unstubAllGlobals());
it("retains password retry after rejection and does not reserve a slot on page open", async () => {
  history.replaceState(null, "", "/s/#synthetic-public-token");
  vi.stubGlobal("matchMedia", () => ({ matches: false }));
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ password_required: true })),
    )
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: "分享或访问密码无效" }), {
        status: 404,
      }),
    )
    .mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          password_required: false,
          grant: "synthetic-grant",
          display_name: "hello.txt",
          size_bytes: 3,
          remaining_downloads: 1,
          expires_at: 200,
        }),
      ),
    );
  vi.stubGlobal("fetch", fetchMock);
  render(<PublicShare />);
  await screen.findByLabelText("访问密码");
  fireEvent.change(screen.getByLabelText("访问密码"), {
    target: { value: "wrong" },
  });
  fireEvent.click(screen.getByRole("button", { name: "验证访问密码" }));
  await screen.findByRole("alert");
  expect(screen.getByLabelText("访问密码")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("访问密码"), {
    target: { value: "correct" },
  });
  fireEvent.click(screen.getByRole("button", { name: "验证访问密码" }));
  await screen.findByRole("heading", { name: "hello.txt" });
  expect(
    fetchMock.mock.calls.every(([url]) => url === "/api/public/shares/verify"),
  ).toBe(true);
  expect(location.hash).toBe("#synthetic-public-token");
});

it("switches links in one tab and ignores a stale verification response", async () => {
  history.replaceState(null, "", "/s/#first-token");
  vi.stubGlobal("matchMedia", () => ({ matches: false }));
  let finishFirst!: (value: Response) => void;
  const first = new Promise<Response>((resolve) => {
    finishFirst = resolve;
  });
  const fetchMock = vi
    .fn()
    .mockReturnValueOnce(first)
    .mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          password_required: false,
          grant: "second-grant",
          display_name: "second.txt",
          size_bytes: 2,
          remaining_downloads: 1,
          expires_at: 200,
        }),
      ),
    );
  vi.stubGlobal("fetch", fetchMock);
  render(<PublicShare />);
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  act(() => {
    history.replaceState(null, "", "/s/#second-token");
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  });
  await screen.findByRole("heading", { name: "second.txt" });
  await act(async () =>
    finishFirst(
      new Response(
        JSON.stringify({
          password_required: false,
          grant: "first-grant",
          display_name: "first.txt",
          size_bytes: 1,
          remaining_downloads: 1,
          expires_at: 200,
        }),
      ),
    ),
  );
  expect(
    screen.getByRole("heading", { name: "second.txt" }),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole("heading", { name: "first.txt" }),
  ).not.toBeInTheDocument();
});
