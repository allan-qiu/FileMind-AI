import "./library.css";
import { useEffect, useRef, useState } from "react";
import {
  FileText,
  Folder,
  RefreshCw,
  Star,
  Trash2,
  Download,
  GitBranch,
} from "lucide-react";
import { ApiClient, ApiError, errorMessage } from "./api";
import { Card, Empty } from "./components";
import { bytes, when } from "./types";
import {
  CatalogSession,
  operation,
  verifiedDownload,
  type LibraryFile,
  type Detail,
  type Version,
  type Conflict,
} from "./library";
import { uploadArchiveFile, type ArchiveJob } from "./archive";
import { hashFile } from "./devicelink";
import { ShareCreate } from "./ShareCenter";

export function LibraryPage({ api }: { api: ApiClient }) {
  const [shareVersion, setShareVersion] = useState("");
  const catalog = useRef(new CatalogSession()),
    loading = useRef(false),
    alive = useRef(true);
  const pendingForce = useRef(false);
  const [files, setFiles] = useState<LibraryFile[]>([]),
    [total, setTotal] = useState(0);
  const [folders, setFolders] = useState<{ path: string; count: number }[]>([]),
    [categories, setCategories] = useState<string[]>([]);
  const [conflicts, setConflicts] = useState<Conflict[]>([]),
    [detail, setDetail] = useState<Detail | null>(null);
  const [query, setQuery] = useState(""),
    [draft, setDraft] = useState("");
  const [folder, setFolder] = useState<string | null>(null),
    [category, setCategory] = useState("");
  const [extension, setExtension] = useState(""),
    [sort, setSort] = useState("recent");
  const [minSize, setMinSize] = useState(""),
    [maxSize, setMaxSize] = useState("");
  const [after, setAfter] = useState(""),
    [before, setBefore] = useState("");
  const [mode, setMode] = useState("all"),
    [offset, setOffset] = useState(0);
  const [status, setStatus] = useState("正在同步目录…"),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false),
    [progress, setProgress] = useState(0);
  const [name, setName] = useState(""),
    [directory, setDirectory] = useState(""),
    [editCategory, setEditCategory] = useState("");
  const [versionId, setVersionId] = useState(""),
    [newContent, setNewContent] = useState<File | null>(null);
  const abort = useRef<AbortController | null>(null),
    busyRef = useRef(false);
  const filters = {
    query,
    folder,
    category,
    extension,
    minSize,
    maxSize,
    after,
    before,
    sort,
    mode,
    offset,
  };
  const currentFilters = useRef(filters);
  currentFilters.current = filters;

  const load = async (force = false) => {
    if (loading.current) {
      pendingForce.current ||= force;
      return;
    }
    loading.current = true;
    try {
      const changed = await catalog.current.sync(api);
      if (!alive.current) return;
      setStatus(
        `已同步 · 修订 ${catalog.current.revision} · ${new Date().toLocaleTimeString()}`,
      );
      if (!changed && !force) return;
      const f = currentFilters.current;
      const params = new URLSearchParams({
        q: f.query,
        category: f.category,
        extension: f.extension,
        sort: f.sort,
        trash: String(f.mode === "trash"),
        favorite: String(f.mode === "favorite"),
        limit: "50",
        offset: String(f.offset),
      });
      if (f.folder !== null) params.set("directory", f.folder);
      if (f.minSize) params.set("min_size", f.minSize);
      if (f.maxSize) params.set("max_size", f.maxSize);
      if (f.after)
        params.set("after", String(new Date(f.after).getTime() / 1000));
      if (f.before)
        params.set(
          "before",
          String(new Date(f.before).getTime() / 1000 + 86399),
        );
      const [result, tree, conflicts] = await Promise.all([
        api.request<{ items: LibraryFile[]; total: number }>(
          `/library/search?${params}`,
        ),
        api.request<{
          items: { path: string; count: number }[];
          categories: string[];
        }>("/library/folders"),
        api.request<Conflict[]>("/library/conflicts"),
      ]);
      if (!alive.current) return;
      setFiles(result.items);
      setTotal(result.total);
      setFolders(tree.items);
      setCategories(tree.categories);
      setConflicts(conflicts);
      setError("");
    } catch (failure) {
      if (!alive.current) return;
      setStatus("未同步 · 请检查连接或登录授权");
      setError(errorMessage(failure));
      if (failure instanceof ApiError && [401, 403].includes(failure.status)) {
        setFiles([]);
        setDetail(null);
        setConflicts([]);
        setFolders([]);
        setCategories([]);
        catalog.current.clear();
      }
    } finally {
      loading.current = false;
      if (pendingForce.current && alive.current) {
        pendingForce.current = false;
        queueMicrotask(() => void load(true));
      }
    }
  };
  useEffect(() => {
    alive.current = true;
    void load(true);
    const timer = window.setInterval(() => {
      if (!busyRef.current) void load();
    }, 5000);
    return () => {
      alive.current = false;
      window.clearInterval(timer);
      abort.current?.abort();
      catalog.current.clear();
    };
  }, [api]);
  useEffect(() => {
    void load(true);
  }, [
    query,
    folder,
    category,
    extension,
    minSize,
    maxSize,
    after,
    before,
    sort,
    mode,
    offset,
  ]);
  const run = async (action: () => Promise<void>, message = "操作已完成") => {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await action();
      if (alive.current) {
        setNotice(message);
        await load(true);
      }
    } catch (failure) {
      if (alive.current) {
        setError(errorMessage(failure));
        if (
          failure instanceof ApiError &&
          [401, 403].includes(failure.status)
        ) {
          setDetail(null);
          setFiles([]);
          setConflicts([]);
          setFolders([]);
          setCategories([]);
          catalog.current.clear();
        }
      }
    } finally {
      busyRef.current = false;
      if (alive.current) setBusy(false);
    }
  };
  const choose = async (file: LibraryFile) => {
    const d = await api.request<Detail>(`/library/${file.file_id}`);
    if (!alive.current) return;
    setDetail(d);
    setName(d.filename);
    setDirectory(d.directory);
    setEditCategory(d.category);
    setVersionId(d.version_id);
  };
  const mutate = async (
    method: string,
    path: string,
    values: Record<string, unknown> = {},
  ) => {
    if (!detail) return;
    await api.request(path, method, operation(detail, values));
    setDetail(null);
  };
  const selectedVersion = detail?.versions.find((v) => v.id === versionId);
  const download = async (v: Version) => {
    abort.current = new AbortController();
    setProgress(0);
    const blob = await verifiedDownload(v, abort.current.signal, (n) =>
      setProgress((n / Math.max(1, v.size_bytes)) * 100),
    );
    const url = URL.createObjectURL(blob),
      link = document.createElement("a");
    link.href = url;
    link.download = v.filename;
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 60000);
  };
  const resolve = async (conflict: Conflict, action: string) => {
    const file = await api.request<Detail>(`/library/${conflict.file_id}`);
    await api.request(
      `/library/conflicts/${conflict.id}/resolve`,
      "POST",
      operation(file, { action }),
    );
    setDetail(null);
  };

  return (
    <>
      <div className="library-heading">
        <div>
          <h2>文件目录与版本</h2>
          <p className="muted">目录随设备同步，内容按需下载。</p>
        </div>
        <button disabled={busy} onClick={() => void load(true)}>
          <RefreshCw size={16} aria-hidden="true" />
          同步目录
        </button>
      </div>
      <p role="status" aria-live="polite">
        {status}
      </p>
      {error && (
        <div role="alert" className="alert error">
          {error}
        </div>
      )}
      {notice && <p role="status">{notice}</p>}
      <div className="library-layout">
        <Card title="浏览目录" className="library-tree">
          <nav aria-label="文件库视图">
            {[
              ["all", "全部文件"],
              ["recent", "最近文件"],
              ["favorite", "收藏"],
              ["trash", "回收站"],
              ["conflicts", "冲突中心"],
            ].map(([key, title]) => (
              <button
                key={key}
                aria-current={mode === key ? "page" : undefined}
                onClick={() => {
                  setMode(key);
                  setFolder(null);
                  setOffset(0);
                  setDetail(null);
                }}
              >
                {key === "trash" ? (
                  <Trash2 size={16} />
                ) : key === "conflicts" ? (
                  <GitBranch size={16} />
                ) : (
                  <Folder size={16} />
                )}{" "}
                {title}
              </button>
            ))}
          </nav>
          <label>
            逻辑目录
            <select
              value={folder ?? "*"}
              onChange={(e) => {
                setFolder(e.target.value === "*" ? null : e.target.value);
                setOffset(0);
              }}
            >
              <option value="*">全部目录</option>
              {folders.map((f) => (
                <option key={f.path} value={f.path}>
                  {f.path || "根目录"} ({f.count})
                </option>
              ))}
            </select>
          </label>
          <label>
            新建空目录
            <input
              aria-label="新建目录路径"
              placeholder="学习/笔记"
              id="new-library-folder"
            />
          </label>
          <button
            disabled={busy}
            onClick={() =>
              void run(async () => {
                const input = document.getElementById(
                  "new-library-folder",
                ) as HTMLInputElement;
                await api.request("/library/folders", "POST", {
                  path: input.value,
                });
                input.value = "";
              })
            }
          >
            新建逻辑目录
          </button>
        </Card>
        <div className="library-main">
          <Card
            title={
              mode === "conflicts"
                ? "冲突中心"
                : mode === "trash"
                  ? "回收站"
                  : "文件目录"
            }
          >
            <form
              className="inline-form"
              onSubmit={(e) => {
                e.preventDefault();
                setQuery(draft);
                setOffset(0);
              }}
            >
              <label>
                文件名
                <input
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  placeholder="搜索文件名"
                />
              </label>
              <button className="primary">搜索</button>
            </form>
            <details>
              <summary>筛选与排序</summary>
              <div className="library-filters">
                <label>
                  分类
                  <select
                    value={category}
                    onChange={(e) => {
                      setCategory(e.target.value);
                      setOffset(0);
                    }}
                  >
                    <option value="">全部分类</option>
                    {categories.map((c) => (
                      <option key={c}>{c}</option>
                    ))}
                  </select>
                </label>
                <label>
                  类型
                  <input
                    value={extension}
                    onChange={(e) => {
                      setExtension(e.target.value);
                      setOffset(0);
                    }}
                    placeholder="pdf / txt"
                  />
                </label>
                <label>
                  最小字节
                  <input
                    type="number"
                    min="0"
                    value={minSize}
                    onChange={(e) => {
                      setMinSize(e.target.value);
                      setOffset(0);
                    }}
                  />
                </label>
                <label>
                  最大字节
                  <input
                    type="number"
                    min="0"
                    value={maxSize}
                    onChange={(e) => {
                      setMaxSize(e.target.value);
                      setOffset(0);
                    }}
                  />
                </label>
                <label>
                  修改日期起
                  <input
                    type="date"
                    value={after}
                    onChange={(e) => {
                      setAfter(e.target.value);
                      setOffset(0);
                    }}
                  />
                </label>
                <label>
                  修改日期止
                  <input
                    type="date"
                    value={before}
                    onChange={(e) => {
                      setBefore(e.target.value);
                      setOffset(0);
                    }}
                  />
                </label>
                <label>
                  排序
                  <select
                    value={sort}
                    onChange={(e) => {
                      setSort(e.target.value);
                      setOffset(0);
                    }}
                  >
                    <option value="recent">最近修改</option>
                    <option value="name">名称（降序）</option>
                    <option value="size">大小（降序）</option>
                    <option value="archived">最近归档</option>
                  </select>
                </label>
              </div>
            </details>
            {mode === "conflicts" ? (
              conflicts.length ? (
                <div className="library-conflicts">
                  {conflicts.map((c) => (
                    <article key={c.id}>
                      <h3>
                        {c.filename} ·{" "}
                        {c.state === "OPEN" ? "待解决" : "已解决"}
                      </h3>
                      <p>
                        {c.reason} · {bytes(c.candidate_size)} ·{" "}
                        {when(c.candidate_time)}
                      </p>
                      <dl>
                        <dt>基础版本</dt>
                        <dd>{c.base_version_id}</dd>
                        <dt>当前版本</dt>
                        <dd>{c.current_version_id}</dd>
                        <dt>候选版本</dt>
                        <dd>{c.candidate_version_id}</dd>
                        <dt>来源设备 / 账户</dt>
                        <dd>{c.device_id || c.actor_id}</dd>
                      </dl>
                      <div className="actions">
                        {[
                          ["current", "保留当前"],
                          ["candidate", "选择候选"],
                          ["save_as", "候选另存"],
                        ].map(([a, t]) => (
                          <button
                            key={a}
                            disabled={busy || c.state !== "OPEN"}
                            onClick={() => void run(() => resolve(c, a))}
                          >
                            {t}
                          </button>
                        ))}
                      </div>
                    </article>
                  ))}
                </div>
              ) : (
                <Empty text="没有版本冲突" />
              )
            ) : (
              <>
                {files.length ? (
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>文件</th>
                          <th>分类 / 目录</th>
                          <th>大小</th>
                          <th>版本</th>
                          <th>修改时间</th>
                          <th>状态</th>
                        </tr>
                      </thead>
                      <tbody>
                        {files.map((f) => (
                          <tr key={f.file_id}>
                            <td>
                              <button
                                className="library-file"
                                aria-label={f.filename}
                                disabled={busy}
                                onClick={() =>
                                  void run(() => choose(f), "详情已加载")
                                }
                              >
                                <FileText size={17} aria-hidden="true" />
                                {f.filename}
                                {f.favorite ? (
                                  <Star size={14} aria-label="已收藏" />
                                ) : null}
                              </button>
                            </td>
                            <td>
                              {f.category}
                              <small>{f.directory || "根目录"}</small>
                            </td>
                            <td>{bytes(f.size_bytes)}</td>
                            <td>v{f.version}</td>
                            <td>{when(f.updated_at)}</td>
                            <td>{f.deleted_at ? "已删除" : "正常"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <Empty text="当前条件下没有文件" />
                )}
                <div className="actions">
                  <button
                    disabled={!offset || busy}
                    onClick={() => setOffset(Math.max(0, offset - 50))}
                  >
                    上一页
                  </button>
                  <span>
                    共 {total} 个 · 第 {Math.floor(offset / 50) + 1} 页
                  </span>
                  <button
                    disabled={offset + 50 >= total || busy}
                    onClick={() => setOffset(offset + 50)}
                  >
                    下一页
                  </button>
                </div>
              </>
            )}
          </Card>
          {detail && (
            <Card title={`文件详情 · ${detail.filename}`}>
              <p>{detail.summary}</p>
              <dl className="library-metadata">
                <dt>文件 ID</dt>
                <dd>{detail.file_id}</dd>
                <dt>归档时间</dt>
                <dd>{when(detail.archived_at)}</dd>
                <dt>实际存储</dt>
                <dd>
                  {detail.provider} / {detail.storage_directory}
                </dd>
                <dt>文件来源</dt>
                <dd>
                  {detail.sources.length
                    ? JSON.stringify(detail.sources)
                    : "旧来源未记录"}
                </dd>
              </dl>
              <div className="library-filters">
                <label>
                  名称
                  <input
                    maxLength={180}
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                  />
                </label>
                <label>
                  逻辑分类
                  <input
                    value={editCategory}
                    onChange={(e) => setEditCategory(e.target.value)}
                  />
                </label>
                <label>
                  逻辑目录
                  <input
                    value={directory}
                    onChange={(e) => setDirectory(e.target.value)}
                  />
                </label>
              </div>
              <div className="actions">
                <button
                  disabled={busy || !!detail.deleted_at}
                  onClick={() =>
                    void run(() =>
                      mutate("PATCH", `/library/${detail.file_id}`, {
                        filename: name,
                        category: editCategory,
                        directory,
                      }),
                    )
                  }
                >
                  保存名称 / 分类 / 目录
                </button>
                <button
                  disabled={busy || !!detail.deleted_at}
                  onClick={() =>
                    void run(() =>
                      mutate("PATCH", `/library/${detail.file_id}`, {
                        favorite: !detail.favorite,
                      }),
                    )
                  }
                >
                  {detail.favorite ? "取消收藏" : "收藏"}
                </button>
                {detail.deleted_at ? (
                  <button
                    disabled={busy}
                    onClick={() =>
                      void run(() =>
                        mutate("POST", `/library/${detail.file_id}/restore`),
                      )
                    }
                  >
                    恢复文件
                  </button>
                ) : (
                  <button
                    disabled={busy}
                    onClick={() =>
                      void run(() =>
                        mutate("DELETE", `/library/${detail.file_id}`),
                      )
                    }
                  >
                    移入回收站
                  </button>
                )}
              </div>
              <label>
                历史版本
                <select
                  value={versionId}
                  onChange={(e) => setVersionId(e.target.value)}
                >
                  {detail.versions.map((v) => (
                    <option key={v.id} value={v.id}>
                      Version {v.version} · {v.filename} · {bytes(v.size_bytes)}{" "}
                      · {when(v.created_at)}
                    </option>
                  ))}
                </select>
              </label>
              {detail.versions.length >= 200 && (
                <button
                  disabled={busy}
                  onClick={() =>
                    void run(async () => {
                      const page = await api.request<Version[]>(
                        `/library/${detail.file_id}/versions?offset=${detail.versions.length}&limit=200`,
                      );
                      setDetail({
                        ...detail,
                        versions: [...detail.versions, ...page],
                      });
                    }, "已加载更多历史版本（如无新增则已显示全部）")
                  }
                >
                  加载更多历史版本
                </button>
              )}
              {selectedVersion && (
                <>
                  <button
                    disabled={!!detail.deleted_at}
                    onClick={() => setShareVersion(selectedVersion.id)}
                  >
                    分享此版本
                  </button>
                  {shareVersion && (
                    <>
                      <ShareCreate
                        key={shareVersion}
                        api={api}
                        versionId={shareVersion}
                      />
                      <button onClick={() => setShareVersion("")}>
                        关闭版本分享
                      </button>
                    </>
                  )}
                  <p className="library-hash">
                    SHA-256 {selectedVersion.sha256}
                    <br />
                    来源：
                    {selectedVersion.device_id ||
                      selectedVersion.actor_id ||
                      "未记录"}
                  </p>
                  <div className="actions">
                    {selectedVersion.size_bytes <= 32 * 1024 ** 2 ? (
                      <button
                        disabled={busy || !!detail.deleted_at}
                        onClick={() =>
                          void run(
                            () => download(selectedVersion),
                            "SHA-256 校验通过，已请求浏览器保存",
                          )
                        }
                      >
                        <Download size={16} />
                        校验并下载版本
                      </button>
                    ) : (
                      <a
                        className="button"
                        href={`/api/library/versions/${selectedVersion.id}/content`}
                      >
                        浏览器原生下载
                      </a>
                    )}
                    <button
                      disabled={
                        busy ||
                        !!detail.deleted_at ||
                        versionId === detail.version_id
                      }
                      onClick={() =>
                        void run(() =>
                          mutate(
                            "POST",
                            `/library/${detail.file_id}/current-version`,
                            { version_id: versionId },
                          ),
                        )
                      }
                    >
                      设为当前版本
                    </button>
                  </div>
                </>
              )}
              <p className="muted">
                页面内校验限 32
                MiB。大文件用浏览器原生流式保存，页面无法确认实际落盘或保存后的哈希；取消请在浏览器下载任务中操作。
              </p>
              <label>
                提交新版本
                <input
                  type="file"
                  disabled={busy || !!detail.deleted_at}
                  onChange={(e) => setNewContent(e.target.files?.[0] ?? null)}
                />
              </label>
              <button
                disabled={busy || !newContent || !!detail.deleted_at}
                onClick={() =>
                  void run(async () => {
                    if (!newContent) return;
                    abort.current = new AbortController();
                    const sha256 = await hashFile(
                      newContent,
                      abort.current.signal,
                    );
                    const job = await api.request<ArchiveJob>(
                      `/library/${detail.file_id}/versions`,
                      "POST",
                      {
                        filename: newContent.name,
                        size_bytes: newContent.size,
                        sha256,
                        base_version_id: detail.version_id,
                        idempotency_key: crypto.randomUUID(),
                      },
                    );
                    await uploadArchiveFile(
                      api,
                      newContent,
                      abort.current.signal,
                      (n, total) => setProgress((n / Math.max(1, total)) * 100),
                      job,
                    );
                    setNewContent(null);
                    setDetail(null);
                  }, "新版本已暂存，AI 授权/计划与归档进度请在归档任务中查看")
                }
              >
                上传新版本
              </button>
              <details>
                <summary>操作历史与撤销</summary>
                {detail.history.length ? (
                  detail.history.map((h) => (
                    <p key={h.id}>
                      {when(h.created_at)} · {h.kind}{" "}
                      {h.kind === "PATCH" && (
                        <button
                          disabled={busy}
                          onClick={() =>
                            void run(async () => {
                              const old = JSON.parse(h.before_json);
                              await mutate(
                                "PATCH",
                                `/library/${detail.file_id}`,
                                {
                                  filename: old.filename,
                                  category: old.category,
                                  directory: old.directory,
                                  favorite: !!old.favorite,
                                },
                              );
                            })
                          }
                        >
                          恢复此次编辑前的名称 / 目录
                        </button>
                      )}
                    </p>
                  ))
                ) : (
                  <p>暂无手动操作记录</p>
                )}
              </details>
            </Card>
          )}
        </div>
      </div>
      {busy && (
        <div className="library-progress" role="status">
          <progress value={progress} max={100} />
          <button onClick={() => abort.current?.abort()}>
            取消本次上传 / 页面内下载
          </button>
        </div>
      )}
    </>
  );
}
