import { useEffect, useState } from "react";
import { RefreshCw, Search } from "lucide-react";
import { ApiClient } from "./api";
import { Card, Empty, Metric, Pager, Status } from "./components";
import {
  type User,
  type Device,
  type Stats,
  type AuditEntry,
  type PublicConfig,
  type Role,
  bytes,
  roleNames,
  when,
} from "./types";
import type { Run } from "./UserPages";
import { SystemStatusPanel } from "./SystemStatusPanel";

function UserRow({
  user,
  api,
  run,
  reload,
}: {
  user: User;
  api: ApiClient;
  run: Run;
  reload: () => Promise<void>;
}) {
  const [quota, setQuota] = useState(String(user.quota_bytes / 1048576));
  const update = async (patch: object) => {
    await api.request(`/users/${user.id}`, "PATCH", patch);
    await reload();
  };
  return (
    <tr>
      <td>
        <strong>{user.display_name}</strong>
        <small>{user.username}</small>
      </td>
      <td>
        <select
          aria-label={`${user.username}的角色`}
          value={user.role}
          onChange={(e) => {
            const role = e.target.value;
            if (
              confirm(
                `将 ${user.username} 的角色更改为 ${roleNames[role as Role]}？现有会话将撤销。`,
              )
            )
              void run(() => update({ role }), "角色已更新。");
          }}
        >
          {Object.entries(roleNames).map(([value, name]) => (
            <option key={value} value={value}>
              {name}
            </option>
          ))}
        </select>
      </td>
      <td>
        <Status active={!!user.enabled}>
          {user.enabled ? "正常" : "已停用"}
        </Status>
      </td>
      <td>
        <form
          className="quota"
          onSubmit={(e) => {
            e.preventDefault();
            void run(
              () => update({ quota_bytes: Number(quota) * 1048576 }),
              "容量配额已保存。",
            );
          }}
        >
          <input
            aria-label={`${user.username}的容量配额MB`}
            type="number"
            min={0}
            max={1048576}
            step={1}
            required
            value={quota}
            onChange={(e) => setQuota(e.target.value)}
          />
          <span>MB</span>
          <button>保存</button>
        </form>
      </td>
      <td>
        <button
          className={user.enabled ? "danger subtle" : "subtle"}
          onClick={() => {
            if (
              confirm(
                `${user.enabled ? "停用" : "启用"}账号 ${user.username}？`,
              )
            )
              void run(
                () => update({ enabled: !user.enabled }),
                "账号状态已更新。",
              );
          }}
        >
          {user.enabled ? "停用" : "启用"}
        </button>
      </td>
    </tr>
  );
}

export function AdminPages({
  page,
  api,
  run,
}: {
  page: string;
  api: ApiClient;
  run: Run;
}) {
  const [stats, setStats] = useState<Stats | null>(null);
  const [users, setUsers] = useState<User[]>([]);
  const [devices, setDevices] = useState<Device[]>([]);
  const [logs, setLogs] = useState<AuditEntry[]>([]);
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [open, setOpen] = useState(true);
  const [announcement, setAnnouncement] = useState("");
  const [loaded, setLoaded] = useState(false);
  const load = async (start = offset) => {
    if (page === "overview") setStats(await api.request<Stats>("/overview"));
    if (page === "users")
      setUsers(
        await api.request<User[]>(
          `/users?query=${encodeURIComponent(query)}&offset=${start}`,
        ),
      );
    if (page === "devices")
      setDevices(await api.request<Device[]>(`/devices?offset=${start}`));
    if (page === "audit")
      setLogs(await api.request<AuditEntry[]>(`/audit?offset=${start}`));
    if (page === "settings") {
      const data = await api.request<PublicConfig>("/settings");
      setOpen(data.registration_open);
      setAnnouncement(data.announcement);
    }
    setLoaded(true);
  };
  useEffect(() => {
    setLoaded(false);
    setOffset(0);
    void run(() => load(0));
  }, [page, api]);
  const changeOffset = (value: number) => {
    setOffset(value);
    void run(() => load(value));
  };
  const refresh = (
    <button onClick={() => void run(() => load())}>
      <RefreshCw size={15} aria-hidden="true" />
      刷新
    </button>
  );
  if (!loaded)
    return (
      <Card title="加载管理数据">
        <p>正在从中央服务读取…</p>
        <button onClick={() => void run(() => load())}>重试加载</button>
      </Card>
    );
  if (page === "users")
    return (
      <Card title="用户管理">
        <form
          className="section-tools"
          onSubmit={(e) => {
            e.preventDefault();
            setOffset(0);
            void run(() => load(0));
          }}
        >
          <label className="search">
            <Search size={16} aria-hidden="true" />
            <input
              aria-label="搜索用户"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="搜索用户名或显示名称"
              maxLength={64}
            />
          </label>
          <button>搜索</button>
          {refresh}
        </form>
        <p className="muted">
          角色变更与停用会撤销该用户所有会话。平台始终保留至少一个有效管理员。
        </p>
        {!users.length ? (
          <Empty text="没有匹配的用户。" />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>用户</th>
                  <th>角色</th>
                  <th>状态</th>
                  <th>登记容量配额</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {users.map((u) => (
                  <UserRow
                    key={u.id}
                    user={u}
                    api={api}
                    run={run}
                    reload={load}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
        <Pager
          offset={offset}
          count={users.length}
          size={50}
          change={changeOffset}
        />
      </Card>
    );
  if (page === "devices")
    return (
      <Card title="平台设备">
        <div className="section-tools">
          <p className="muted">运营元数据；不授予用户私人内容读取权限。</p>
          {refresh}
        </div>
        {!devices.length ? (
          <Empty text="当前没有设备登记。" />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>设备</th>
                  <th>所属账户</th>
                  <th>平台</th>
                  <th>状态</th>
                  <th>最近连接</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {devices.map((d) => (
                  <tr key={d.id}>
                    <td>{d.name}</td>
                    <td>{d.username}</td>
                    <td>{d.platform}</td>
                    <td>
                      <Status active={d.online}>
                        {d.revoked_at ? "已撤销" : d.online ? "在线" : "离线"}
                      </Status>
                    </td>
                    <td>{when(d.last_seen)}</td>
                    <td>
                      {!d.revoked_at && (
                        <button
                          className="danger subtle"
                          onClick={() => {
                            if (confirm(`撤销设备 ${d.name}？`))
                              void run(async () => {
                                await api.request(`/devices/${d.id}`, "DELETE");
                                await load();
                              }, "设备授权已撤销并写入审计。");
                          }}
                        >
                          撤销
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <Pager
          offset={offset}
          count={devices.length}
          size={100}
          change={changeOffset}
        />
      </Card>
    );
  if (page === "audit")
    return (
      <Card title="管理员及账户操作记录">
        <div className="section-tools">
          <p className="muted">按最新事件排序；不记录密码或访问令牌。</p>
          {refresh}
        </div>
        {!logs.length ? (
          <Empty text="暂无审计记录。" />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>时间</th>
                  <th>操作者</th>
                  <th>动作</th>
                  <th>元数据</th>
                </tr>
              </thead>
              <tbody>
                {logs.map((log) => (
                  <tr key={log.id}>
                    <td>{when(log.created_at)}</td>
                    <td>{log.actor || "系统"}</td>
                    <td>
                      <code>{log.action}</code>
                    </td>
                    <td className="break">{JSON.stringify(log.detail)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <Pager
          offset={offset}
          count={logs.length}
          size={100}
          change={changeOffset}
        />
      </Card>
    );
  if (page === "settings")
    return (
      <Card title="平台基础设置">
        <form
          className="settings-form"
          onSubmit={(e) => {
            e.preventDefault();
            void run(async () => {
              await api.request("/settings", "PUT", {
                registration_open: open,
                announcement,
              });
              await load();
            }, "平台设置已保存并记录审计。");
          }}
        >
          <label className="check-label">
            <input
              type="checkbox"
              checked={open}
              onChange={(e) => setOpen(e.target.checked)}
            />
            允许新用户注册
          </label>
          <label>
            系统公告
            <textarea
              maxLength={1000}
              rows={5}
              value={announcement}
              onChange={(e) => setAnnouncement(e.target.value)}
              placeholder="向用户发布本平台的说明"
            />
          </label>
          <p className="muted">
            设置由服务端存储。AI、归档和传输参数在对应管理页面配置。
          </p>
          <button className="primary">保存设置</button>
        </form>
      </Card>
    );
  if (!stats) return null;
  return (
    <>
      <section className="admin-heading">
        <div>
          <p className="eyebrow">FILEMIND ADMIN / LIVE METADATA</p>
          <h2>系统运行，一目了然。</h2>
          <p>真实账户、设备与服务进程指标，私人文件权限独立校验。</p>
        </div>
        {refresh}
      </section>
      <div className="metrics">
        <Metric
          title="注册用户"
          value={stats.users}
          note={`${stats.enabled_users} 个正常账户`}
        />
        <Metric
          title="有效设备"
          value={stats.devices}
          note={`${stats.online_devices} 台最近在线`}
        />
        <Metric
          title="服务进程内存"
          value={bytes(stats.process_rss_bytes)}
          note={`已运行 ${stats.uptime_seconds} 秒`}
        />
      </div>
      <div className="two-columns">
        <Card title="服务状态">
          <ul className="service-list">
            {Object.entries(stats.services).map(([name, status]) => (
              <li key={name}>
                <span>{name}</span>
                <Status active={status === "running"}>
                  {status === "running"
                    ? "已运行"
                    : status === "worker_paused"
                      ? "工作器已暂停"
                      : status}
                </Status>
              </li>
            ))}
          </ul>
        </Card>
        <Card title="本地运行信息">
          <dl className="detail-list">
            <dt>审计记录</dt>
            <dd>{stats.audit_entries}</dd>
            <dt>主数据库文件</dt>
            <dd>{bytes(stats.database_bytes)}（不含 WAL）</dd>
            <dt>进程累计 CPU 时间</dt>
            <dd>{stats.process_cpu_seconds?.toFixed(2) ?? "未读取"} 秒</dd>
            <dt>主机可用内存</dt>
            <dd>
              {stats.host_available_memory_bytes === undefined
                ? "未读取"
                : bytes(stats.host_available_memory_bytes)}
            </dd>
            <dt>所在磁盘剩余</dt>
            <dd>{bytes(stats.disk_free_bytes)}</dd>
            <dt>部署状态</dt>
            <dd>本地隔离开发；未检查生产服务器</dd>
          </dl>
          <p className="muted">
            指标来自当前服务主机。归档、文件库和分享详情见对应管理页面。
          </p>
        </Card>
      </div>
      <SystemStatusPanel api={api} />
    </>
  );
}
