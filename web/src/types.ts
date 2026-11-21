export type Role = "user" | "workspace_admin" | "system_admin";
export interface User {
  id: string;
  username: string;
  display_name: string;
  role: Role;
  enabled: boolean;
  created_at: number;
  quota_bytes: number;
}
export interface Device {
  id: string;
  user_id: string;
  name: string;
  platform: string;
  created_at: number;
  last_seen: number | null;
  revoked_at: number | null;
  online: boolean;
  username?: string;
}
export interface Workspace {
  id: string;
  name: string;
  owner_id: string;
  permission: string;
}
export interface Member {
  id: string;
  username: string;
  display_name: string;
  permission: string;
}
export interface PublicConfig {
  registration_open: boolean;
  announcement: string;
  milestones: Record<string, string>;
}
export interface Session {
  user: User;
  csrf_token: string;
  access_token?: string;
}
export interface Stats {
  users: number;
  enabled_users: number;
  devices: number;
  online_devices: number;
  audit_entries: number;
  process_rss_bytes: number;
  process_cpu_seconds?: number;
  host_available_memory_bytes?: number;
  database_bytes: number;
  disk_free_bytes: number;
  uptime_seconds: number;
  services: Record<string, string>;
}
export interface AuditEntry {
  id: number;
  actor: string;
  action: string;
  target_id: string | null;
  detail: Record<string, unknown>;
  created_at: number;
}
export const roleNames: Record<Role, string> = {
  user: "普通用户",
  workspace_admin: "空间管理员",
  system_admin: "系统管理员",
};
export const when = (stamp: number | null) =>
  stamp
    ? new Date(stamp * 1000).toLocaleString("zh-CN", { hour12: false })
    : "尚未连接";
export const bytes = (value: number) =>
  value >= 1073741824
    ? `${(value / 1073741824).toFixed(1)} GB`
    : value >= 1048576
      ? `${(value / 1048576).toFixed(1)} MB`
      : value >= 1024
        ? `${(value / 1024).toFixed(1)} KB`
        : `${value} B`;
