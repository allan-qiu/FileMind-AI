import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  LayoutDashboard,
  Laptop,
  Users,
  Settings,
  LogOut,
  Moon,
  Sun,
  Menu,
  ChevronRight,
  Activity,
  Layers,
  UserRound,
} from "lucide-react";
import { ApiClient, ApiError, errorMessage } from "./api";
import { UserPages } from "./UserPages";
import { AdminPages } from "./AdminPages";
import { TransferAdmin } from "./TransferAdmin";
import { ArchiveAdmin } from "./ArchiveAdmin";
import { LibraryAdmin } from "./LibraryAdmin";
import { ShareAdmin } from "./ShareAdmin";
import { Status } from "./components";
import { LicenseNotice, RELEASE_VERSION } from "./LicenseNotice";
import { type Session, type User, type PublicConfig, roleNames } from "./types";

export function Portal({ admin = false }: { admin?: boolean }) {
  const api = useMemo(() => new ApiClient(admin), [admin]);
  const [user, setUser] = useState<User | null>(null);
  const [starting, setStarting] = useState(true);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [page, setPage] = useState("overview");
  const [mobileOpen, setMobileOpen] = useState(false);
  const [isMobile, setIsMobile] = useState(() => window.innerWidth <= 760);
  useEffect(() => {
    if (!window.matchMedia) return;
    const media = window.matchMedia("(max-width:760px)");
    const update = () => setIsMobile(media.matches);
    update();
    media.addEventListener("change", update);
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMobileOpen(false);
    };
    document.addEventListener("keydown", escape);
    return () => {
      media.removeEventListener("change", update);
      document.removeEventListener("keydown", escape);
    };
  }, []);
  const [register, setRegister] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [config, setConfig] = useState<PublicConfig | null>(null);
  const [dark, setDark] = useState(() => {
    try {
      return window.localStorage.getItem("filemind-theme") === "dark";
    } catch {
      return false;
    }
  });
  useEffect(() => {
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    try {
      window.localStorage.setItem("filemind-theme", dark ? "dark" : "light");
    } catch {
      /* Visual preferences are optional when browser storage is unavailable. */
    }
  }, [dark]);
  const run = useCallback(
    async (action: () => Promise<void>, success?: string) => {
      if (busyRef.current) return;
      busyRef.current = true;
      setBusy(true);
      setError("");
      setNotice("");
      try {
        await action();
        if (success) setNotice(success);
      } catch (failure) {
        setError(errorMessage(failure));
        if (failure instanceof ApiError && failure.status === 401)
          setUser(null);
      } finally {
        busyRef.current = false;
        setBusy(false);
      }
    },
    [],
  );
  useEffect(() => {
    const boot = async () => {
      try {
        const session = await api.request<Session>("/auth/session");
        api.csrf = session.csrf_token;
        setUser(session.user);
      } catch (failure) {
        if (!(failure instanceof ApiError && failure.status === 401))
          setError(errorMessage(failure));
      } finally {
        setStarting(false);
      }
    };
    void boot();
    void new ApiClient()
      .request<PublicConfig>("/public/config")
      .then(setConfig)
      .catch(() => {});
  }, [api]);
  const entries = admin
    ? ([
        ["overview", "系统总览", LayoutDashboard],
        ["users", "用户管理", Users],
        ["devices", "设备管理", Laptop],
        ["transfers", "传输管理", Activity],
        ["archives", "AI 与归档", Layers],
        ["library", "文件库运营", Layers],
        ["shares", "分享管理", Layers],
        ["audit", "运行审计", Activity],
        ["settings", "平台设置", Settings],
      ] as const)
    : ([
        ["overview", "工作台", LayoutDashboard],
        ["devices", "我的设备", Laptop],
        ["link", "DeviceLink 互传", Activity],
        ["archives", "归档与文件目录", Layers],
        ["library", "全局文件库", Layers],
        ["shares", "分享中心", Layers],
        ["spaces", "共享空间", Layers],
        ["profile", "个人中心", UserRound],
      ] as const);
  const pageTitle = entries.find((item) => item[0] === page)?.[1] ?? "工作台";
  const themeButton = (
    <button
      className="icon-button"
      aria-label="切换主题"
      onClick={() => setDark(!dark)}
    >
      {dark ? <Sun size={19} /> : <Moon size={19} />}
    </button>
  );
  const brand = (
    <div className="brand">
      <span className="brand-mark">F</span>
      <div>
        <strong>FileMind{admin ? " Admin" : " AI"}</strong>
        <small>{admin ? "平台管理控制台" : "连接设备，有序管理"}</small>
      </div>
    </div>
  );
  const feedback = (
    <>
      {error && (
        <div className="feedback error" role="alert">
          {error}
        </div>
      )}
      {notice && (
        <div className="feedback success" role="status">
          {notice}
        </div>
      )}
    </>
  );
  if (starting)
    return (
      <div className="starting" role="status">
        正在连接 FileMind 中央服务…
        <LicenseNotice />
      </div>
    );
  if (!user)
    return (
      <div className={`auth-layout ${admin ? "admin-auth" : ""}`}>
        <aside className="auth-story">
          {brand}
          <div>
            <p className="eyebrow">
              {admin ? "CONTROL WITH CLARITY" : "ONE SPACE. YOUR DEVICES."}
            </p>
            <h1>
              {admin ? (
                <>
                  让管理清晰，
                  <br />
                  让权限有界。
                </>
              ) : (
                <>
                  每一台设备，
                  <br />
                  同一个工作空间。
                </>
              )}
            </h1>
            <p>
              {admin
                ? "独立管理员会话，服务端权限校验。运营管理与用户私人内容访问始终分离。"
                : "从账户和设备开始，逐步连接文件互传、云端归档与分享。"}
            </p>
            <div className="auth-note">
              <span className="dot" />
              中央服务 · V{RELEASE_VERSION}
            </div>
          </div>
          <span className="muted">
            FILEMIND {RELEASE_VERSION} / {admin ? "ADMIN" : "WORKSPACE"}
          </span>
        </aside>
        <main className="auth-main">
          <div className="auth-top">
            {themeButton}
            <a href={admin ? "/app/" : "/admin/"}>
              {admin ? "用户工作空间" : "管理员入口"} <ChevronRight size={15} />
            </a>
          </div>
          <div className="auth-card">
            <p className="eyebrow">
              {admin ? "FILEMIND ADMIN" : "欢迎来到 FILEMIND"}
            </p>
            <h2>
              {admin
                ? "管理员登录"
                : register
                  ? "创建你的账户"
                  : "登录工作空间"}
            </h2>
            <p className="muted">
              {admin
                ? "请使用通过本地命令创建的系统管理员账号。"
                : "账号和数据来自当前中央服务，不包含预置演示账户。"}
            </p>
            {feedback}
            <form
              onSubmit={(event) => {
                event.preventDefault();
                void run(async () => {
                  if (register)
                    await api.request("/auth/register", "POST", {
                      username,
                      password,
                      display_name: displayName,
                    });
                  const session = await api.request<Session>(
                    "/auth/login",
                    "POST",
                    { username, password },
                  );
                  api.csrf = session.csrf_token;
                  setUser(session.user);
                  setPage("overview");
                  setPassword("");
                });
              }}
            >
              <label>
                用户名
                <input
                  required
                  autoComplete="username"
                  pattern="[A-Za-z0-9_]{3,32}"
                  minLength={3}
                  maxLength={32}
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  placeholder="3–32 位字母、数字或下划线"
                />
              </label>
              {register && (
                <label>
                  显示名称
                  <input
                    required
                    maxLength={64}
                    value={displayName}
                    onChange={(e) => setDisplayName(e.target.value)}
                    autoComplete="nickname"
                  />
                </label>
              )}
              <label>
                密码
                <input
                  required
                  type="password"
                  autoComplete={register ? "new-password" : "current-password"}
                  minLength={register ? 10 : 1}
                  maxLength={128}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder={register ? "至少 10 个字符" : "输入你的密码"}
                />
              </label>
              <button disabled={busy} className="primary full">
                {busy ? "正在连接…" : register ? "注册并登录" : "登录"}
              </button>
            </form>
            {!admin && config?.registration_open && (
              <p className="switch-auth">
                {register ? "已有账户？" : "还没有账户？"}
                <button
                  className="link"
                  onClick={() => {
                    setRegister(!register);
                    setError("");
                  }}
                >
                  {register ? "返回登录" : "创建账户"}
                </button>
              </p>
            )}
            {config?.announcement && (
              <div className="announcement">{config.announcement}</div>
            )}
            <p className="footnote muted">
              {admin
                ? "无默认管理员密码。用户端登录不会获得管理接口授权。"
                : "DeviceLink、独立归档和基础文件目录已开放；本地整理可离线运行。"}
            </p>
          </div>
          <footer>
            <LicenseNotice />
          </footer>
        </main>
      </div>
    );
  return (
    <div className={`shell ${admin ? "admin-shell" : ""}`}>
      <aside
        inert={isMobile && !mobileOpen}
        aria-hidden={isMobile && !mobileOpen}
        className={`sidebar ${mobileOpen ? "open" : ""}`}
      >
        {brand}
        <p className="nav-label">{admin ? "平台运营" : "工作空间"}</p>
        <nav>
          {entries.map(([key, title, Icon]) => (
            <button
              key={key}
              disabled={busy}
              aria-current={page === key ? "page" : undefined}
              className={page === key ? "selected" : ""}
              onClick={() => {
                setPage(key);
                setMobileOpen(false);
                setError("");
                setNotice("");
              }}
            >
              <Icon size={19} aria-hidden="true" />
              {title}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <Status active>中央服务已连接</Status>
          <p>
            账户与设备管理已开放
            <br />
            文件服务按里程碑逐步接入
          </p>
          <a href={admin ? "/app/" : "/admin/"}>
            {admin ? "打开用户端" : "打开管理控制台"} <ChevronRight size={14} />
          </a>
          <small>VERSION {RELEASE_VERSION} · AGPL-3.0</small>
        </div>
      </aside>
      {mobileOpen && (
        <button
          className="scrim"
          aria-label="关闭导航"
          onClick={() => setMobileOpen(false)}
        />
      )}
      <div className="workspace">
        <header className="topbar">
          <div className="row">
            <button
              className="icon-button menu-toggle"
              aria-label="切换导航"
              aria-expanded={mobileOpen}
              onClick={() => setMobileOpen(!mobileOpen)}
            >
              <Menu size={21} />
            </button>
            <span className="breadcrumb">
              {admin ? "管理控制台" : "工作空间"} <ChevronRight size={14} />{" "}
              {pageTitle}
            </span>
          </div>
          <div className="row">
            {themeButton}
            <span className="avatar" aria-hidden="true">
              {user.display_name.slice(0, 1)}
            </span>
            <span className="account-name">{user.display_name}</span>
            <button
              className="icon-button"
              aria-label="退出登录"
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  await api.request("/auth/logout", "POST");
                  api.csrf = "";
                  setUser(null);
                })
              }
            >
              <LogOut size={18} />
            </button>
          </div>
        </header>
        <main className="content">
          <div className="page-heading">
            <div>
              <p className="eyebrow">
                {admin ? "PLATFORM OPERATIONS" : "YOUR PERSONAL WORKSPACE"}
              </p>
              <h1>{pageTitle}</h1>
            </div>
            <Status>{roleNames[user.role]}</Status>
          </div>
          {config?.announcement && (
            <div className="announcement">{config.announcement}</div>
          )}
          {feedback}
          <div className="busy-line" role="status" aria-live="polite">
            {busy ? "正在处理请求…" : ""}
          </div>
          <fieldset className="page-content" disabled={busy}>
            {admin ? (
              page === "transfers" ? (
                <TransferAdmin api={api} />
              ) : page === "archives" ? (
                <ArchiveAdmin api={api} />
              ) : page === "library" ? (
                <LibraryAdmin api={api} />
              ) : page === "shares" ? (
                <ShareAdmin api={api} />
              ) : (
                <AdminPages page={page} api={api} run={run} />
              )
            ) : (
              <UserPages page={page} api={api} user={user} run={run} />
            )}
          </fieldset>
          <footer>
            FileMind AI · <LicenseNotice />
          </footer>
        </main>
      </div>
    </div>
  );
}
