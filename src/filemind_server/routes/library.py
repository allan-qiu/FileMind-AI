import re
import time
import uuid
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from filemind_server.dependencies import AdminPrincipal, UserPrincipal, authenticate
from filemind_server.library import ACCESS, SELECT, LibraryService, directory, fingerprint
from filemind_server.routes.archives import ArchiveCreate

router = APIRouter()


class SyncAck(BaseModel):
    client_id: str = Field(max_length=100)
    receipt: str = Field(min_length=1, max_length=100)


@router.post("/library/sync/ack")
def sync_ack(data: SyncAck, request: Request, p: UserPrincipal):
    return service(request).acknowledge(p, data.client_id, data.receipt)


def service(request):
    return LibraryService(request.app.state.db, request.app.state.archives)


class Operation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: str = Field(min_length=8, max_length=100)
    expected_revision: int = Field(ge=0)


class FilePatch(Operation):
    filename: str = Field(default="", min_length=1, max_length=180)
    category: str = Field(default="", min_length=1, max_length=80)
    directory: str = Field(default="", max_length=400)
    favorite: bool = False
    workspace_id: str | None = Field(default=None, max_length=100)


class VersionCreate(ArchiveCreate):
    base_version_id: str = Field(min_length=1, max_length=100)


class Resolution(Operation):
    action: Literal["current", "candidate", "save_as"]


class VersionRestore(Operation):
    version_id: str = Field(min_length=1, max_length=100)


class FolderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=400)


@router.get("/library/search")
def search(
    request: Request,
    p: UserPrincipal,
    q: str = Query("", max_length=180),
    category: str = Query("", max_length=80),
    directory: str | None = Query(None, max_length=400),
    extension: str = Query("", max_length=30),
    min_size: int | None = Query(None, ge=0),
    max_size: int | None = Query(None, ge=0),
    after: float | None = Query(None, ge=0),
    before: float | None = Query(None, ge=0),
    sort: Literal["name", "size", "recent", "archived"] = "recent",
    descending: bool = True,
    trash: bool = False,
    favorite: bool = False,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return service(request).list_files(
        p, {k: v for k, v in locals().items() if k not in {"request", "p"}}
    )


@router.get("/library")
def legacy_catalog(request: Request, p: UserPrincipal):
    rows = service(request).list_files(p, {"limit": 200})["items"]
    return [{**r, "id": r["version_id"], "target_directory": r["directory"]} for r in rows]


@router.get("/library/folders")
def folders(request: Request, p: UserPrincipal):
    s = service(request)
    with s.db.transaction() as conn:
        rows = conn.execute(
            "SELECT m.directory,m.category,count(*) count FROM global_files f "
            "JOIN file_metadata m ON m.file_id=f.id WHERE "
            + ACCESS
            + " AND m.deleted_at IS NULL GROUP BY m.directory,m.category",
            (p.user["id"], p.user["id"]),
        ).fetchall()
        paths = {r["directory"] for r in rows}
        paths.update(
            r[0]
            for r in conn.execute(
                "SELECT path FROM library_folders WHERE scope=?", ("user:" + p.user["id"],)
            )
        )
        # Include intermediate logical folders; no physical storage moves occur.
        for path in list(paths):
            paths.update("/".join(path.split("/")[:i]) for i in range(1, len(path.split("/"))))
        return {
            "items": [
                {"path": path, "count": sum(r["count"] for r in rows if r["directory"] == path)}
                for path in sorted(paths)
            ],
            "categories": sorted({r["category"] for r in rows}),
        }


@router.post("/library/folders", status_code=201)
def create_folder(data: FolderCreate, request: Request, p: UserPrincipal):
    path = directory(data.path)
    with request.app.state.db.transaction(True) as conn:
        scope = "user:" + p.user["id"]
        created = conn.execute(
            "INSERT OR IGNORE INTO library_folders VALUES (?,?,?)", (scope, path, time.time())
        )
        if created.rowcount:
            from filemind_server.library import encoded

            conn.execute(
                (
                    "INSERT INTO catalog_events(scope,kind,payload,created_at) VALUES (?,'FOL"
                    "DER_CREATED',?,?)"
                ),
                (scope, encoded({"path": path}), time.time()),
            )
    return {"path": path}


@router.get("/library/revision")
def revision(request: Request, p: UserPrincipal):
    s = service(request)
    with s.db.transaction() as conn:
        return {"revision": s.revision(conn), "acl": s.acl(conn, p)}


@router.get("/library/snapshot")
def snapshot(
    request: Request,
    p: UserPrincipal,
    limit: int = Query(200, ge=1, le=200),
    offset: int = Query(0, ge=0),
    at: int | None = Query(None, ge=0),
    client_id: str = Query("", max_length=100),
):
    return service(request).sync(p, limit=limit, offset=offset, at=at, client_id=client_id)


@router.get("/library/changes")
def changes(
    request: Request,
    p: UserPrincipal,
    after: int = Query(ge=0),
    acl: str = Query(max_length=64),
    limit: int = Query(200, ge=1, le=200),
    client_id: str = Query("", max_length=100),
):
    return service(request).sync(p, after=after, acl=acl, limit=limit, client_id=client_id)


@router.get("/library/conflicts")
def conflicts(request: Request, p: UserPrincipal, offset: int = Query(0, ge=0)):
    s = service(request)
    with s.db.transaction() as conn:
        return [
            dict(r)
            for r in conn.execute(
                (
                    "SELECT x.*,m.filename,v.filename candidate_filename,s.size_bytes candida"
                    "te_size,v.created_at candidate_time FROM file_conflicts x JOIN global_fi"
                    "les f ON f.id=x.file_id JOIN file_metadata m ON m.file_id=f.id JOIN file"
                    "_versions v ON v.id=x.candidate_version_id JOIN storage_objects s ON s.i"
                    "d=v.object_id WHERE "
                )
                + ACCESS
                + " ORDER BY x.created_at DESC LIMIT 100 OFFSET ?",
                (p.user["id"], p.user["id"], offset),
            )
        ]


@router.post("/library/conflicts/{conflict_id}/resolve")
def resolve(conflict_id: str, data: Resolution, request: Request, p: UserPrincipal):
    return service(request).resolve(p, conflict_id, data.model_dump())


@router.get("/library/downloads/{task_id}")
def download_task(task_id: str, request: Request, p: UserPrincipal):
    with request.app.state.db.transaction() as conn:
        row = conn.execute(
            "SELECT * FROM library_download_tasks WHERE id=? AND user_id=?", (task_id, p.user["id"])
        ).fetchone()
    if not row:
        raise HTTPException(404, "下载任务不存在")
    return dict(row)


@router.get("/library/versions/{version_id}")
def version_detail(version_id: str, request: Request, p: UserPrincipal):
    s = service(request)
    with s.db.transaction() as conn:
        row = conn.execute(
            (
                "SELECT v.*,s.size_bytes,s.provider FROM file_versions v JOIN storage_obj"
                "ects s ON s.id=v.object_id WHERE v.id=?"
            ),
            (version_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "版本不存在")
        s.owned(conn, row["file_id"], p)
        return dict(row)


@router.get("/library/versions/{version_id}/content")
@router.get("/library/{version_id}/content")
def content(version_id: str, request: Request, p: UserPrincipal):
    s = service(request)
    with s.db.transaction() as conn:
        row = conn.execute(
            (
                "SELECT v.file_id,v.filename,v.sha256,s.object_key,s.size_bytes,s.provide"
                "r FROM file_versions v JOIN storage_objects s ON s.id=v.object_id WHERE "
                "v.id=?"
            ),
            (version_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "版本不存在")
        file = s.owned(conn, row["file_id"], p)
        if file["deleted_at"]:
            raise HTTPException(409, "请先恢复回收站文件")
    size, start, end = row["size_bytes"], 0, row["size_bytes"] - 1
    partial = request.headers.get("range")
    if partial:
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", partial)
        if not match or not any(match.groups()) or size == 0:
            raise HTTPException(416, "不支持此 Range", headers={"Content-Range": f"bytes */{size}"})
        a, b = match.groups()
        if a:
            start, end = int(a), min(int(b), end) if b else end
        else:
            start = max(0, size - int(b))
        if start > end or start >= size:
            raise HTTPException(416, "Range 超出文件", headers={"Content-Range": f"bytes */{size}"})
    task, now = str(uuid.uuid4()), time.time()
    with s.db.transaction(True) as conn:
        active = conn.execute(
            "SELECT count(*) FROM library_download_tasks WHERE user_id=? "
            "AND state IN ('VERIFYING','STREAMING') AND updated_at>?",
            (p.user["id"], now - 120),
        ).fetchone()[0]
        if active >= 4:
            raise HTTPException(429, "同时下载上限 4")
        global_active = conn.execute(
            "SELECT count(*) FROM library_download_tasks "
            "WHERE state IN ('VERIFYING','STREAMING') AND updated_at>?",
            (now - 120,),
        ).fetchone()[0]
        if global_active >= 8:
            raise HTTPException(429, "全局同时下载上限 8")
        conn.execute(
            (
                "INSERT INTO library_download_tasks(id,user_id,device_id,version_id,state"
                ",requested_bytes,created_at,updated_at) VALUES (?,?,?,?,'VERIFYING',?,?,"
                "?)"
            ),
            (
                task,
                p.user["id"],
                p.session["device_id"],
                version_id,
                max(0, end - start + 1),
                now,
                now,
            ),
        )

    def status(state, sent=0, error=""):
        with s.db.transaction(True) as conn:
            conn.execute(
                (
                    "UPDATE library_download_tasks SET state=?,bytes_sent=?,error=?,updated_a"
                    "t=? WHERE id=?"
                ),
                (state, sent, error, time.time(), task),
            )

    source = None
    try:
        source = s.archives.reads.open(
            s.archives.provider(row["provider"]), row["object_key"], size, row["sha256"]
        )
        from filemind_server.downloads import signature

        initial_signature = signature(source)
        source.seek(start)
    except (OSError, ValueError, NotImplementedError) as exc:
        if source:
            source.close()
        status("FAILED", error=type(exc).__name__)
        raise HTTPException(
            409, "存储对象缺失或完整性校验失败", headers={"X-Download-Task": task}
        ) from None

    def stream():
        sent, remaining = 0, max(0, end - start + 1)
        try:
            status("STREAMING")
            while remaining:
                authenticate(request)
                with s.db.transaction() as conn:
                    current = s.owned(conn, row["file_id"], p)
                    if current["deleted_at"]:
                        raise RuntimeError("FILE_DELETED")
                block = source.read(min(262144, remaining))
                if not block:
                    raise OSError("SHORT_READ")
                remaining -= len(block)
                sent += len(block)
                yield block
            if signature(source) != initial_signature:
                raise OSError("OBJECT_CHANGED_DURING_STREAM")
            status("STREAMED", sent)
        except BaseException:
            status("INTERRUPTED", sent, "STREAM_INTERRUPTED")
            raise
        finally:
            source.close()

    headers = {
        "Content-Disposition": "attachment; filename*=UTF-8''" + quote(row["filename"]),
        "Content-Length": str(max(0, end - start + 1)),
        "Accept-Ranges": "bytes",
        "ETag": '"' + row["sha256"] + '"',
        "X-Content-SHA256": row["sha256"],
        "X-Download-Task": task,
    }
    if partial:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(
        stream(),
        status_code=206 if partial else 200,
        headers=headers,
        media_type="application/octet-stream",
    )


@router.get("/library/{file_id}/versions")
def versions(
    file_id: str,
    request: Request,
    p: UserPrincipal,
    limit: int = Query(200, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    s = service(request)
    with s.db.transaction() as conn:
        s.owned(conn, file_id, p)
        return s.versions(conn, file_id, limit, offset)


@router.post("/library/{file_id}/current-version")
def restore_version(file_id: str, data: VersionRestore, request: Request, p: UserPrincipal):
    return service(request).operation(p, file_id, data.model_dump(), "VERSION_RESTORE")


@router.post("/library/{file_id}/versions", status_code=201)
def new_version(file_id: str, data: VersionCreate, request: Request, p: UserPrincipal):
    s = service(request)
    digest = fingerprint([file_id, data.model_dump()])
    with s.db.transaction() as conn:
        file = s.owned(conn, file_id, p, True)
        if file["deleted_at"]:
            raise HTTPException(409, "文件在回收站")
        if not conn.execute(
            "SELECT 1 FROM file_versions WHERE id=? AND file_id=?", (data.base_version_id, file_id)
        ).fetchone():
            raise HTTPException(422, "基础版本不属于此文件")

    # Archive creation and association are serialized atomically on the same DB connection.
    # create() supports a connection hook so a worker never sees an unassociated upload.
    def associate(conn, job_id):
        previous = conn.execute(
            "SELECT request_hash FROM library_version_requests WHERE job_id=?", (job_id,)
        ).fetchone()
        if previous and previous[0] != digest:
            raise HTTPException(409, "版本幂等键内容不同")
        conn.execute(
            "INSERT OR IGNORE INTO library_version_requests VALUES (?,?,?,?,?,?)",
            (job_id, file_id, data.base_version_id, p.user["id"], p.session["device_id"], digest),
        )

    try:
        job = s.archives.create(
            p.user["id"],
            data.filename,
            data.size_bytes,
            data.sha256,
            "version:" + data.idempotency_key,
            associate=associate,
        )
    except ValueError:
        raise HTTPException(422, "文件名称不合法") from None
    return s.archives.public(job)


@router.get("/library/{file_id}")
def detail(file_id: str, request: Request, p: UserPrincipal):
    return service(request).detail(p, file_id)


@router.patch("/library/{file_id}")
def patch(file_id: str, data: FilePatch, request: Request, p: UserPrincipal):
    return service(request).operation(p, file_id, data.model_dump(exclude_unset=True))


@router.delete("/library/{file_id}")
def delete(file_id: str, data: Operation, request: Request, p: UserPrincipal):
    return service(request).operation(p, file_id, data.model_dump(), "DELETE")


@router.post("/library/{file_id}/restore")
def restore(file_id: str, data: Operation, request: Request, p: UserPrincipal):
    return service(request).operation(p, file_id, data.model_dump(), "RESTORE")


@router.get("/admin/library")
def admin_library(request: Request, p: AdminPrincipal, offset: int = Query(0, ge=0)):
    s = service(request)
    with s.db.transaction() as conn:
        counts = {
            name: conn.execute(sql).fetchone()[0]
            for name, sql in {
                "files": "SELECT count(*) FROM global_files",
                "versions": "SELECT count(*) FROM file_versions",
                "objects": "SELECT count(*) FROM storage_objects",
                "events": "SELECT count(*) FROM catalog_events",
                "trash": "SELECT count(*) FROM file_metadata WHERE deleted_at IS NOT NULL",
                "conflicts": "SELECT count(*) FROM file_conflicts WHERE state='OPEN'",
                "downloads": "SELECT count(*) FROM library_download_tasks",
                "bytes_sent": "SELECT coalesce(sum(bytes_sent),0) FROM library_download_tasks",
                "failed_downloads": (
                    "SELECT count(*) FROM library_download_tasks WHERE state IN ('FAILED','IN"
                    "TERRUPTED')"
                ),
            }.items()
        }
        counts["users"] = [
            dict(r)
            for r in conn.execute(
                "SELECT f.user_id,u.username,count(DISTINCT f.id) files,count(v.id) versi"
                "ons,coalesce(sum(s.size_bytes),0) referenced_bytes FROM global_files f J"
                "OIN users u ON u.id=f.user_id JOIN file_versions v ON v.file_id=f.id JOI"
                "N storage_objects s ON s.id=v.object_id GROUP BY f.user_id LIMIT 200"
            )
        ]
        counts["items"] = [
            {k: r[k] for k in r.keys() if k not in {"summary", "object_key", "storage_directory"}}
            for r in conn.execute(
                SELECT + " ORDER BY f.updated_at DESC LIMIT 100 OFFSET ?", (offset,)
            )
        ]
        counts["sync"] = [
            dict(r)
            for r in conn.execute(
                "SELECT user_id,client_id,revision,updated_at FROM catalog_sync_cursors O"
                "RDER BY updated_at DESC LIMIT 100"
            )
        ]
        counts["download_tasks"] = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM library_download_tasks ORDER BY created_at DESC LIMIT 100"
            )
        ]
        counts["revision"] = s.revision(conn)
        return counts
