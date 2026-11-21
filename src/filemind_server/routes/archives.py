import asyncio
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from filemind_server.accounts import audit
from filemind_server.ai import OrganizePlan
from filemind_server.ai_control import save_config
from filemind_server.archives import ArchivePreferences, ArchiveSettings
from filemind_server.dependencies import AccountPrincipal, AdminPrincipal, UserPrincipal

router = APIRouter()


class ArchiveCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str = Field(min_length=1, max_length=180)
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    idempotency_key: str = Field(min_length=8, max_length=100)


@router.get("/archive/preferences")
def preferences(request: Request, p: UserPrincipal):
    return request.app.state.archives.preferences(p.user["id"])


@router.put("/archive/preferences")
def update_preferences(data: ArchivePreferences, request: Request, p: AccountPrincipal):
    request.app.state.archives.set_preferences(p.user["id"], data)
    with request.app.state.db.transaction(True) as conn:
        audit(
            conn,
            p.user["id"],
            "archive.consent_updated",
            p.user["id"],
            {"ai_mode": data.ai_mode, "auto_archive": data.auto_archive},
        )
    return data


@router.get("/archive/jobs")
def jobs(request: Request, p: UserPrincipal, state: str = "", offset: int = 0):
    service = request.app.state.archives
    service.ingest_handoffs()
    with service.db.transaction() as conn:
        rows = conn.execute(
            "SELECT * FROM archive_jobs WHERE user_id=? AND (?='' OR state=?) "
            "ORDER BY created_at DESC LIMIT 200 OFFSET ?",
            (p.user["id"], state, state, max(0, offset)),
        ).fetchall()
    return [service.public(dict(row)) for row in rows]


@router.post("/archive/jobs", status_code=201)
def create(data: ArchiveCreate, request: Request, p: UserPrincipal):
    s = request.app.state.archives
    try:
        return s.public(
            s.create(
                p.user["id"], data.filename, data.size_bytes, data.sha256, data.idempotency_key
            )
        )
    except ValueError:
        raise HTTPException(422, "文件名称不合法") from None


@router.get("/archive/jobs/{job_id}")
def detail(job_id: str, request: Request, p: UserPrincipal):
    s = request.app.state.archives
    return s.public(s.owned(job_id, p))


@router.post("/archive/transfers/{transfer_id}", status_code=201)
def manual_transfer_archive(transfer_id: str, request: Request, p: UserPrincipal):
    s = request.app.state.archives
    with s.db.transaction() as conn:
        row = conn.execute(
            "SELECT * FROM transfer_sessions WHERE id=? AND user_id=? AND state='COMPLETED'",
            (transfer_id, p.user["id"]),
        ).fetchone()
    if not row:
        raise HTTPException(404, "已完成传输不存在")
    return s.public(
        s.create(
            row["user_id"],
            row["filename"],
            row["size_bytes"],
            row["expected_hash"],
            "transfer:" + transfer_id,
            dict(row),
        )
    )


@router.get("/archive/jobs/{job_id}/events")
def events(job_id: str, request: Request, p: UserPrincipal):
    s = request.app.state.archives
    s.owned(job_id, p)
    with s.db.transaction() as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT state,message,created_at FROM archive_events "
                "WHERE job_id=? ORDER BY id DESC LIMIT 200",
                (job_id,),
            )
        ]


@router.post("/archive/jobs/{job_id}/claim")
def claim(job_id: str, request: Request, p: UserPrincipal):
    return request.app.state.archives.claim(job_id, p)


@router.put("/archive/jobs/{job_id}/chunks/{offset}")
async def chunk(job_id: str, offset: int, request: Request, p: UserPrincipal):
    s = request.app.state.archives
    s.owned(job_id, p)
    token = request.headers.get("X-Archive-Lease", "")
    size = request.app.state.settings.transfer_chunk_bytes
    block = bytearray()
    try:
        async with asyncio.timeout(request.app.state.settings.transfer_timeout):
            async for part in request.stream():
                if len(block) + len(part) > size:
                    raise HTTPException(413, "归档块超过上限")
                block.extend(part)
    except TimeoutError:
        raise HTTPException(408, "归档块读取超时，已确认偏移保留") from None
    return await asyncio.to_thread(s.upload_chunk, job_id, offset, block, p, token)


@router.post("/archive/jobs/{job_id}/finish")
def finish(job_id: str, request: Request, p: UserPrincipal):
    return request.app.state.archives.finish_upload(
        job_id, p, request.headers.get("X-Archive-Lease", "")
    )


@router.post("/archive/jobs/{job_id}/retry")
def retry(job_id: str, request: Request, p: UserPrincipal):
    s = request.app.state.archives
    s.owned(job_id, p)
    s.retry(job_id)
    return s.public(s.row(job_id))


@router.post("/archive/jobs/{job_id}/cancel")
def cancel(job_id: str, request: Request, p: UserPrincipal):
    s = request.app.state.archives
    s.cancel(job_id, p)
    return s.public(s.row(job_id))


@router.put("/archive/jobs/{job_id}/plan")
def review(job_id: str, data: OrganizePlan, request: Request, p: UserPrincipal):
    s = request.app.state.archives
    try:
        s.review(job_id, p, data.model_dump())
    except ValueError:
        raise HTTPException(422, "整理计划未通过分类/路径/关联权限校验") from None
    return s.public(s.row(job_id))


@router.get("/admin/archive/jobs")
def admin_jobs(
    request: Request, p: AdminPrincipal, state: str = "", user_id: str = "", offset: int = 0
):
    s = request.app.state.archives
    with s.db.transaction() as conn:
        rows = conn.execute(
            "SELECT j.*,u.username FROM archive_jobs j JOIN users u ON u.id=j.user_id "
            "WHERE (?='' OR state=?) AND (?='' OR user_id=?) ORDER BY created_at DESC "
            "LIMIT 200 OFFSET ?",
            (state, state, user_id, user_id, max(0, offset)),
        ).fetchall()
    return [s.admin_public(dict(r)) for r in rows]


@router.post("/admin/archive/jobs/{job_id}/retry")
def admin_retry(job_id: str, request: Request, p: AdminPrincipal):
    s = request.app.state.archives
    s.retry(job_id, extend_expired=True)
    with s.db.transaction(True) as conn:
        audit(conn, p.user["id"], "archive.admin_retry", job_id, {})
    return s.admin_public(s.row(job_id))


@router.get("/admin/archive/settings")
def settings(request: Request, p: AdminPrincipal):
    return request.app.state.archives.config()


@router.put("/admin/archive/settings")
def configure(data: ArchiveSettings, request: Request, p: AdminPrincipal):
    if data.provider != "local":
        provider = request.app.state.archives.provider(data.provider)
        try:
            provider.guard(True)
        except (ValueError, NotImplementedError):
            raise HTTPException(409, "百度网盘未配置或未取得正式访问及写入授权") from None
    save_config(request.app.state.db, "archive_v2_2", data)
    with request.app.state.db.transaction(True) as conn:
        audit(conn, p.user["id"], "archive.settings_updated", None, data.model_dump())
    return data


@router.get("/admin/storage")
def storage(request: Request, p: AdminPrincipal):
    s = request.app.state.archives
    with s.db.transaction() as conn:
        totals = dict(
            conn.execute(
                "SELECT count(*) files,coalesce(sum(size_bytes),0) used_bytes FROM storage_objects"
            ).fetchone()
        )
        temp = dict(
            conn.execute(
                "SELECT count(*) temporary_objects,coalesce(sum(reserved_bytes),0) reserved_bytes "
                "FROM ingest_sessions WHERE state!='CLEANED'"
            ).fetchone()
        )
        counts = [
            dict(r)
            for r in conn.execute("SELECT state,count(*) count FROM archive_jobs GROUP BY state")
        ]
        last = conn.execute("SELECT * FROM reconciliation_jobs ORDER BY id DESC LIMIT 1").fetchone()
    return {
        "provider": "local",
        "status": "本地存储适配器运行中，百度网盘未正式连接",
        "capabilities": s.storage.capabilities,
        **totals,
        **temp,
        "jobs": counts,
        "last_reconciliation": dict(last) if last else None,
        "checked_at": time.time(),
        "worker_errors": request.app.state.archive_worker_errors,
    }


@router.post("/admin/storage/reconcile")
def reconcile(request: Request, p: AdminPrincipal):
    return request.app.state.archives.reconcile()
