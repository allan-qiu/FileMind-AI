import { useEffect, useState } from "react";
import { Laptop, RefreshCw, Plus, Check } from "lucide-react";
import { ApiClient } from "./api";
import { Card, Empty, Metric, Roadmap, Status } from "./components";
import { DeviceLinkPage } from "./DeviceLinkPage";
import { ArchivePage } from "./ArchivePage";
import { LibraryPage } from "./LibraryPage";
import { ShareCenter } from "./ShareCenter";
import {
  type User,
  type Device,
  type Workspace,
  type Member,
  bytes,
  roleNames,
  when,
} from "./types";

export type Run = (
  action: () => Promise<void>,
  message?: string,
) => Promise<void>;

export function UserPages({
  page,
  api,
  user,
  run,
}: {
  page: string;
  api: ApiClient;
  user: User;
  run: Run;
}) {
  const [devices, setDevices] = useState<Device[]>([]);
  const [spaces, setSpaces] = useState<Workspace[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [deviceName, setDeviceName] = useState("");
  const [platform, setPlatform] = useState("browser");
  const [binding, setBinding] = useState<{ id: string; token: string } | null>(
    null,
  );
  const [spaceName, setSpaceName] = useState("");
  const [selected, setSelected] = useState("");
  const [members, setMembers] = useState<Member[] | null>(null);
  const [memberName, setMemberName] = useState("");
  const [permission, setPermission] = useState("viewer");
  const load = async () => {
    const [d, w] = await Promise.all([
      api.request<Device[]>("/devices"),
      api.request<Workspace[]>("/workspaces"),
    ]);
    setDevices(d);
    setSpaces(w);
    setLoaded(true);
  };
  useEffect(() => {
    void run(load);
  }, [api]);
  useEffect(() => {
    if (!binding || page === "link") return;
    const heartbeat = async () => {
      const response = await fetch(`/api/devices/${binding.id}/heartbeat`, {
        method: "POST",
        headers: { Authorization: `Bearer ${binding.token}` },
        signal: AbortSignal.timeout(5000),
      });
      if (!response.ok) {
        setBinding(null);
        throw new Error("设备凭据已失效，请重新绑定。");
      }
      await load();
    };
    const timer = window.setInterval(() => {
      void run(heartbeat);
    }, 30000);
    return () => window.clearInterval(timer);
  }, [binding, api, page]);
  const chooseSpace = (id: string) => {
    setSelected(id);
    setMembers(null);
    if (id)
      void run(async () =>
        setMembers(await api.request<Member[]>(`/workspaces/${id}/members`)),
      );
  };
  const space = spaces.find((item) => item.id === selected);
  const canManage =
    space &&
    (space.owner_id === user.id ||
      (user.role !== "user" && space.permission === "admin"));

  if (!loaded)
    return (
      <Card title="连接工作空间">
        <p>正在加载真实账户数据…</p>
        <button onClick={() => void run(load)}>重试加载</button>
      </Card>
    );
  if (page === "link")
    return <DeviceLinkPage api={api} binding={binding} bind={setBinding} />;
  if (page === "archives") return <ArchivePage api={api} binding={binding} />;
  if (page === "library") return <LibraryPage api={api} />;
  if (page === "shares") return <ShareCenter api={api} />;
  if (page === "devices")
    return (
      <>
        <Card title="添加一台设备">
          <p className="muted">
            为设备签发可撤销的独立凭据。Web
            绑定仅保存在本页内存，刷新或关闭后心跳停止；旧设备可在列表撤销。
          </p>
          <form
            className="inline-form"
            onSubmit={(event) => {
              event.preventDefault();
              void run(async () => {
                const result = await api.request<{
                  device: Device;
                  device_token: string;
                }>("/devices", "POST", { name: deviceName, platform });
                setBinding({
                  id: result.device.id,
                  token: result.device_token,
                });
                setDeviceName("");
                await load();
              }, "设备已登记，独立凭据已签发。");
            }}
          >
            <label>
              设备名称
              <input
                required
                maxLength={64}
                value={deviceName}
                onChange={(e) => setDeviceName(e.target.value)}
                placeholder="例如：我的笔记本"
              />
            </label>
            <label>
              平台
              <select
                value={platform}
                onChange={(e) => setPlatform(e.target.value)}
              >
                <option value="browser">浏览器</option>
                <option value="windows">Windows</option>
                <option value="android">Android</option>
                <option value="ios">iOS</option>
              </select>
            </label>
            <button className="primary" type="submit">
              <Plus size={16} aria-hidden="true" />
              登记设备
            </button>
          </form>
          {binding && (
            <p className="success">
              <Check size={16} aria-hidden="true" />
              本页正在为已绑定设备维持心跳。
            </p>
          )}
        </Card>
        <Card title="我的设备">
          <div className="section-tools">
            <span className="muted">在线状态基于最近 90 秒内的心跳。</span>
            <button onClick={() => void run(load)}>
              <RefreshCw size={15} aria-hidden="true" />
              刷新
            </button>
          </div>
          {!devices.length ? (
            <Empty text="还没有登记设备。" />
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>设备</th>
                    <th>平台</th>
                    <th>状态</th>
                    <th>最近连接</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {devices.map((d) => (
                    <tr key={d.id}>
                      <td>
                        <strong>{d.name}</strong>
                      </td>
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
                              if (
                                confirm(
                                  `撤销“${d.name}”的授权？原设备凭据将立即失效。`,
                                )
                              )
                                void run(async () => {
                                  await api.request(
                                    `/devices/${d.id}`,
                                    "DELETE",
                                  );
                                  if (binding?.id === d.id) setBinding(null);
                                  await load();
                                }, "设备授权已撤销。");
                            }}
                          >
                            撤销授权
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </>
    );

  if (page === "spaces")
    return (
      <>
        <Card title="建立共享空间">
          <p className="muted">
            先管理成员权限。文件内容与全局目录将在 M5
            接入；系统管理员不会自动获得私人空间访问权。
          </p>
          <form
            className="inline-form"
            onSubmit={(e) => {
              e.preventDefault();
              void run(async () => {
                await api.request("/workspaces", "POST", { name: spaceName });
                setSpaceName("");
                await load();
              }, "空间已创建。");
            }}
          >
            <label>
              空间名称
              <input
                required
                maxLength={64}
                value={spaceName}
                onChange={(e) => setSpaceName(e.target.value)}
                placeholder="例如：产品团队"
              />
            </label>
            <button className="primary">创建空间</button>
          </form>
        </Card>
        <div className="space-grid">
          {spaces.map((s) => (
            <button
              key={s.id}
              className={`space-card ${selected === s.id ? "chosen" : ""}`}
              onClick={() => chooseSpace(s.id)}
            >
              <Laptop size={22} aria-hidden="true" />
              <strong>{s.name}</strong>
              <span>
                {s.owner_id === user.id ? "我创建的空间" : "已授权空间"} ·{" "}
                {s.permission}
              </span>
            </button>
          ))}
        </div>
        {selected && (
          <Card title={`${space?.name ?? "空间"} · 成员`}>
            {!members ? (
              <p>正在加载成员…</p>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>成员</th>
                      <th>账户</th>
                      <th>权限</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {members.map((m) => (
                      <tr key={m.id}>
                        <td>{m.display_name}</td>
                        <td>{m.username}</td>
                        <td>{m.permission}</td>
                        <td>
                          {canManage && m.id !== space?.owner_id && (
                            <button
                              className="danger subtle"
                              onClick={() => {
                                if (confirm(`移除成员 ${m.username}？`))
                                  void run(async () => {
                                    await api.request(
                                      `/workspaces/${selected}/members/${m.id}`,
                                      "DELETE",
                                    );
                                    setMembers(
                                      await api.request<Member[]>(
                                        `/workspaces/${selected}/members`,
                                      ),
                                    );
                                  }, "成员已移除。");
                              }}
                            >
                              移除
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {canManage && (
              <form
                className="inline-form"
                onSubmit={(e) => {
                  e.preventDefault();
                  void run(async () => {
                    await api.request(
                      `/workspaces/${selected}/members`,
                      "POST",
                      { username: memberName, permission },
                    );
                    setMemberName("");
                    setMembers(
                      await api.request<Member[]>(
                        `/workspaces/${selected}/members`,
                      ),
                    );
                  }, "成员权限已保存。");
                }}
              >
                <label>
                  已有账号用户名
                  <input
                    required
                    pattern="[A-Za-z0-9_]{3,32}"
                    value={memberName}
                    onChange={(e) => setMemberName(e.target.value)}
                  />
                </label>
                <label>
                  空间权限
                  <select
                    value={permission}
                    onChange={(e) => setPermission(e.target.value)}
                  >
                    <option value="viewer">查看</option>
                    <option value="editor">编辑</option>
                    <option value="admin">空间管理</option>
                  </select>
                </label>
                <button className="primary">添加 / 更新成员</button>
              </form>
            )}
          </Card>
        )}
      </>
    );

  if (page === "profile")
    return (
      <Card title="我的账户">
        <dl className="detail-list">
          <dt>显示名称</dt>
          <dd>{user.display_name}</dd>
          <dt>用户名</dt>
          <dd>{user.username}</dd>
          <dt>角色</dt>
          <dd>{roleNames[user.role]}</dd>
          <dt>登记容量配额</dt>
          <dd>{bytes(user.quota_bytes)}（永久文件库尚未开放）</dd>
          <dt>注册时间</dt>
          <dd>{when(user.created_at)}</dd>
          <dt>凭据保存</dt>
          <dd>HttpOnly Cookie；设备凭据仅存当前页内存。</dd>
        </dl>
        <p className="muted">
          退出登录会撤销本次账户会话；设备授权可在“我的设备”单独撤销。
        </p>
      </Card>
    );

  return (
    <>
      <section className="hero">
        <div>
          <p className="eyebrow">FILEMIND / CONNECTED WORKSPACE</p>
          <h2>
            连接你的设备，
            <br />
            从一个有序的空间开始。
          </h2>
          <p>账户与设备已连接中央服务。桌面真实离线整理仍可独立使用。</p>
        </div>
        <div className="hero-art" aria-hidden="true">
          <div className="art-folder">
            <span>F</span>
          </div>
          <div className="art-line" />
          <div className="art-device">
            <Laptop size={45} />
          </div>
        </div>
      </section>
      <div className="metrics">
        <Metric
          title="有效设备"
          value={devices.filter((d) => !d.revoked_at).length}
          note={`${devices.filter((d) => d.online).length} 台最近在线`}
        />
        <Metric
          title="已授权空间"
          value={spaces.length}
          note="仅显示你有权访问的空间"
        />
        <Metric
          title="账户身份"
          value={<span className="metric-word">{roleNames[user.role]}</span>}
          note="权限由服务器逐次验证"
        />
      </div>
      <Roadmap />
    </>
  );
}
