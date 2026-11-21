import { act, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DEFAULT_SOURCE_URL, LicenseNotice } from "./LicenseNotice";

describe("public corresponding source offer", () => {
  it("shows an exact-release source offer before authentication", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("offline"));
    render(<LicenseNotice />);
    expect(
      screen.getByRole("link", { name: "获取对应版本源码" }),
    ).toHaveAttribute("href", DEFAULT_SOURCE_URL);
    await waitFor(() => expect(fetch).toHaveBeenCalled());
  });
  it("uses the deployed modified version's public source URL", async () => {
    const source = "https://example.org/modified/source.zip";
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          license: "AGPL-3.0-only",
          source_url: source,
        }),
      ),
    );
    render(<LicenseNotice />);
    await waitFor(() =>
      expect(
        screen.getByRole("link", { name: "获取对应版本源码" }),
      ).toHaveAttribute("href", source),
    );
  });
  it.each(["javascript:alert(1)", "https://user:secret@example.org/src.zip"])(
    "rejects unsafe metadata %s",
    async (source) => {
      vi.spyOn(globalThis, "fetch").mockResolvedValue(
        new Response(
          JSON.stringify({
            license: "AGPL-3.0-only",
            source_url: source,
          }),
        ),
      );
      await act(async () => {
        render(<LicenseNotice />);
      });
      expect(
        screen.getByRole("link", { name: "获取对应版本源码" }),
      ).toHaveAttribute("href", DEFAULT_SOURCE_URL);
    },
  );
});
