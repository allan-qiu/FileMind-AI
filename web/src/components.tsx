import type { ReactNode } from "react";
import { ArrowUpRight, FolderOpen } from "lucide-react";

export function Card({
  title,
  children,
  className = "",
}: {
  title?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`card ${className}`}>
      {title && <h2>{title}</h2>}
      {children}
    </section>
  );
}
export function Empty({ text }: { text: string }) {
  return (
    <div className="empty">
      <FolderOpen size={28} aria-hidden="true" />
      <p>{text}</p>
    </div>
  );
}
export function Metric({
  title,
  value,
  note,
}: {
  title: string;
  value: ReactNode;
  note: string;
}) {
  return (
    <div className="metric">
      <p>{title}</p>
      <strong>{value}</strong>
      <span>{note}</span>
    </div>
  );
}
export function Status({
  children,
  active = false,
}: {
  children: ReactNode;
  active?: boolean;
}) {
  return <span className={`badge ${active ? "active" : ""}`}>{children}</span>;
}
export function Roadmap() {
  return (
    <Card title="连接设备、整理归档与文件分享">
      <div className="roadmap">
        {[
          ["03", "DeviceLink", "中央流式中继，接收后确认"],
          ["04", "AI 云端归档", "结构化计划，校验后正式存储"],
          ["05", "全局文件库", "同步目录，按需下载文件"],
          ["06", "FileMind Share", "独立链接与原子下载名额"],
        ].map(([step, name, desc]) => (
          <div key={step}>
            <span className="step">{step}</span>
            <div>
              <h3>{name}</h3>
              <p>{desc}</p>
            </div>
            <Status active>已开放</Status>
          </div>
        ))}
      </div>
      <p className="muted footnote">
        <ArrowUpRight size={15} aria-hidden="true" />
        当前已开放账户、设备、空间和 DeviceLink、AI归档、全局文件库及 FileMind
        Share。
      </p>
    </Card>
  );
}
export function Pager({
  offset,
  size,
  count,
  change,
}: {
  offset: number;
  size: number;
  count: number;
  change: (n: number) => void;
}) {
  return (
    <div className="row pager">
      <span className="muted">
        第 {Math.floor(offset / size) + 1} 页 · 本页 {count} 条
      </span>
      <button
        disabled={offset === 0}
        onClick={() => change(Math.max(0, offset - size))}
      >
        上一页
      </button>
      <button disabled={count < size} onClick={() => change(offset + size)}>
        下一页
      </button>
    </div>
  );
}
