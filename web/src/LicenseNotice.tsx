import { useEffect, useState } from "react";

export const RELEASE_VERSION = "2.6.3";
export const DEFAULT_SOURCE_URL = `https://github.com/allan-qiu/FileMind-AI/archive/refs/tags/v${RELEASE_VERSION}.zip`;

function publicSource(value: unknown): string | null {
  if (typeof value !== "string") return null;
  try {
    const url = new URL(value);
    const local = ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
    if (
      !["https:", "http:"].includes(url.protocol) ||
      (url.protocol === "http:" && !local) ||
      url.username ||
      url.password ||
      url.search ||
      url.hash
    )
      return null;
    return url.href;
  } catch {
    return null;
  }
}

export function LicenseNotice() {
  const [source, setSource] = useState(DEFAULT_SOURCE_URL);
  useEffect(() => {
    const controller = new AbortController();
    void fetch("/api/public/license", { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) return;
        const info = await response.json();
        const url = publicSource(info.source_url);
        if (info.license === "AGPL-3.0-only" && url) setSource(url);
      })
      .catch(() => {});
    return () => controller.abort();
  }, []);
  return (
    <span className="license-notice">
      AGPL-3.0 ·{" "}
      <a href={source} target="_blank" rel="noreferrer">
        获取对应版本源码
      </a>
    </span>
  );
}
