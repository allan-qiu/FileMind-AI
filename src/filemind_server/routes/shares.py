"""Authenticated management, header credentials for guests and separate admin scope."""

from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from filemind_server.dependencies import AdminPrincipal, UserPrincipal
from filemind_server.share_config import ShareSettings, config, save

router = APIRouter()


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Create(Strict):
    file_version_id: str | None = Field(None, max_length=100)
    temporary_object_id: str | None = Field(None, max_length=100)
    max_downloads: int | None = Field(None, ge=1, le=10000)
    ttl_seconds: int | None = Field(None, ge=60, le=2592000)
    password: str | None = Field(None, max_length=128)


class Update(Strict):
    max_downloads: int | None = Field(None, ge=1, le=10000)
    ttl_seconds: int | None = Field(None, ge=60, le=2592000)
    password: str | None = Field(None, max_length=128)


class Upload(Strict):
    filename: str = Field(min_length=1, max_length=180)
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class Verify(Strict):
    token: str = Field(min_length=1, max_length=100)
    password: str | None = Field(None, max_length=128)


class Reservation(Strict):
    grant: str = Field(min_length=1, max_length=100)
    request_key: str = Field(min_length=8, max_length=100)
    session_secret: str = Field(min_length=32, max_length=100)


class Confirmation(Strict):
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


def manager(request):
    return request.app.state.shares


def credential(authorization):
    if not authorization.startswith("Bearer ") or len(authorization) > 110:
        raise HTTPException(404, "下载凭据无效")
    return authorization[7:]


@router.get("/shares/settings")
def user_settings(request: Request, p: UserPrincipal):
    return config(request.app.state.db)


@router.post("/shares/temporary/uploads", status_code=201)
def begin(data: Upload, request: Request, p: UserPrincipal):
    return request.app.state.share_temporary.begin(p.user["id"], data.model_dump())


@router.get("/shares/temporary/uploads/{upload_id}")
def upload_status(upload_id: str, request: Request, p: UserPrincipal):
    temp = request.app.state.share_temporary
    with temp.db.transaction() as conn:
        row = temp.upload(conn, p.user["id"], upload_id)
        return {
            k: row[k]
            for k in ("upload_id", "status", "offset", "file_size", "expires_at", "object_id")
        }


@router.put("/shares/temporary/uploads/{upload_id}/chunks/{offset}")
async def chunk(upload_id: str, offset: int, request: Request, p: UserPrincipal):
    # Never accumulate more than the existing archive chunk limit, even if headers lie.
    data = bytearray()
    async for block in request.stream():
        data.extend(block)
        if len(data) > request.app.state.settings.transfer_chunk_bytes:
            raise HTTPException(413, "上传块超出上限")
    import asyncio

    return await asyncio.to_thread(
        request.app.state.share_temporary.chunk, p.user["id"], upload_id, offset, bytes(data)
    )


@router.post("/shares/temporary/uploads/{upload_id}/complete")
def finish(upload_id: str, request: Request, p: UserPrincipal):
    return request.app.state.share_temporary.complete(p.user["id"], upload_id)


@router.post("/shares/temporary/uploads/{upload_id}/cancel")
def cancel_upload(upload_id: str, request: Request, p: UserPrincipal):
    return request.app.state.share_temporary.cancel(p.user["id"], upload_id)


@router.get("/shares/temporary/objects")
def objects(request: Request, p: UserPrincipal):
    return request.app.state.share_temporary.objects(p.user["id"])


@router.post("/shares", status_code=201)
def create(data: Create, request: Request, p: UserPrincipal):
    return manager(request).create(p, data.model_dump(exclude_none=True))


@router.get("/shares")
def listing(
    request: Request,
    p: UserPrincipal,
    q: str = Query("", max_length=180),
    status: Literal["", "ACTIVE", "EXPIRED", "REVOKED"] = "",
    offset: int = Query(0, ge=0),
    after: float | None = Query(None, ge=0),
    before: float | None = Query(None, ge=0),
):
    return manager(request).listing(
        p, q=q, status=status, offset=offset, after=after, before=before
    )


@router.get("/shares/{share_id}")
def detail(share_id: str, request: Request, p: UserPrincipal):
    m = manager(request)
    with m.db.transaction() as conn:
        return m.view(conn, m.owned(conn, p.user["id"], share_id))


@router.patch("/shares/{share_id}")
def update(share_id: str, data: Update, request: Request, p: UserPrincipal):
    values = data.model_dump(exclude_unset=True)
    if values.get("max_downloads") is None:
        values.pop("max_downloads", None)
    if values.get("ttl_seconds") is None:
        values.pop("ttl_seconds", None)
    return manager(request).update(p, share_id, values)


@router.post("/shares/{share_id}/revoke")
def revoke(share_id: str, request: Request, p: UserPrincipal):
    return manager(request).revoke(p, share_id)


@router.get("/shares/{share_id}/downloads")
def history(share_id: str, request: Request, p: UserPrincipal):
    return manager(request).history(p, share_id)


@router.post("/public/shares/verify")
def verify(data: Verify, request: Request):
    return manager(request).verify(
        data.token, data.password, request.client.host if request.client else "local"
    )


@router.post("/public/shares/downloads", status_code=201)
def reserve(data: Reservation, request: Request):
    return request.app.state.share_downloads.reserve(
        data.grant, data.request_key, data.session_secret
    )


@router.get("/public/downloads/{session_id}")
def session_state(session_id: str, request: Request, authorization: str = Header("")):
    return request.app.state.share_downloads.state(session_id, credential(authorization))


@router.get("/public/downloads/{session_id}/content")
def content(session_id: str, request: Request, authorization: str = Header("")):
    return request.app.state.share_downloads.prepare(
        session_id, credential(authorization), request.headers.get("range")
    )


@router.post("/public/downloads/{session_id}/content")
async def native_content(session_id: str, request: Request):
    # Native browser Save download supports bounded form credentials without URL secrets.
    import asyncio
    from urllib.parse import parse_qs

    raw = await request.body()
    if len(raw) > 512:
        raise HTTPException(413, "下载请求过长")
    fields = parse_qs(raw.decode("ascii", errors="ignore"))
    secret = fields.get("session_secret", [""])[0]
    if not 32 <= len(secret) <= 100:
        raise HTTPException(404, "下载凭据无效")
    return await asyncio.to_thread(
        request.app.state.share_downloads.prepare, session_id, secret, None
    )


@router.post("/public/downloads/{session_id}/complete")
def complete(
    session_id: str, data: Confirmation, request: Request, authorization: str = Header("")
):
    return request.app.state.share_downloads.confirm(
        session_id, credential(authorization), data.sha256
    )


@router.post("/public/downloads/{session_id}/cancel")
def cancel(session_id: str, request: Request, authorization: str = Header("")):
    return request.app.state.share_downloads.cancel(session_id, credential(authorization))


@router.get("/admin/shares/settings")
def admin_settings(request: Request, p: AdminPrincipal):
    return config(request.app.state.db)


@router.put("/admin/shares/settings")
def configure(data: ShareSettings, request: Request, p: AdminPrincipal):
    save(request.app.state.db, data)
    from filemind_server.accounts import audit

    with request.app.state.db.transaction(True) as conn:
        audit(conn, p.user["id"], "share.settings", "share", data.model_dump())
    return data


@router.get("/admin/shares")
def admin_list(
    request: Request,
    p: AdminPrincipal,
    q: str = Query("", max_length=180),
    owner: str = Query("", max_length=100),
    status: Literal["", "ACTIVE", "EXPIRED", "REVOKED"] = "",
    offset: int = Query(0, ge=0),
    after: float | None = Query(None, ge=0),
    before: float | None = Query(None, ge=0),
):
    return manager(request).listing(p, True, q, status, owner, offset, after, before)


@router.post("/admin/shares/{share_id}/revoke")
def admin_revoke(share_id: str, request: Request, p: AdminPrincipal):
    return manager(request).revoke(p, share_id, True)


@router.get("/admin/shares/{share_id}/downloads")
def admin_history(share_id: str, request: Request, p: AdminPrincipal):
    return manager(request).history(p, share_id, True)


@router.get("/admin/share-statistics")
def statistics(request: Request, p: AdminPrincipal):
    import time

    now = time.time()
    start = int(now // 86400) * 86400
    with request.app.state.db.transaction() as conn:
        aggregate = conn.execute(
            "SELECT coalesce(sum(status='ACTIVE' AND expires_at>?),0) active,"
            "coalesce(sum(created_at>=?),0) "
            "created_today,coalesce(sum(source_type='temporary'),0) temporary,"
            "coalesce(sum(source_type='library'),0) library,"
            "coalesce(sum(status='EXPIRED' OR status='ACTIVE' AND "
            "expires_at<=?),0) expired,"
            "coalesce(sum(status='REVOKED'),0) revoked FROM share_links",
            (now, start, now),
        ).fetchone()
        counts = dict(aggregate)
        counts["worker_errors"] = request.app.state.share_worker_errors
        counts["completed_today"] = conn.execute(
            "SELECT count(*) FROM share_download_sessions WHERE "
            "status='COMPLETED' AND completed_at>=?",
            (start,),
        ).fetchone()[0]
        counts["active_sessions"] = conn.execute(
            "SELECT count(*) FROM share_download_sessions WHERE reserved_quota=1"
            " AND reservation_expires_at>?",
            (now,),
        ).fetchone()[0]
        counts["wire_attempt_bytes"] = conn.execute(
            "SELECT coalesce(sum(bytes_sent),0) FROM share_events"
        ).fetchone()[0]
        counts["delivered_prefix_bytes"] = conn.execute(
            "SELECT coalesce(sum(transferred_bytes),0) FROM share_download_sessions"
        ).fetchone()[0]
        counts["temporary_bytes"] = conn.execute(
            "SELECT coalesce(sum(file_size),0) FROM temporary_share_objects "
            "WHERE deletion_status!='DELETED'"
        ).fetchone()[0]
        counts["pending_upload_bytes"] = conn.execute(
            "SELECT coalesce(sum(file_size),0) FROM share_uploads WHERE status='UPLOADING'"
        ).fetchone()[0]
        counts["cleanup_failures"] = conn.execute(
            "SELECT count(*) FROM temporary_share_objects WHERE deletion_status='FAILED'"
        ).fetchone()[0]
        counts["sessions"] = [
            dict(r)
            for r in conn.execute(
                "SELECT "
                "session_id,share_id,status,reserved_quota,transferred_bytes,expected_bytes,created_at,reservation_expires_at,error"
                " FROM share_download_sessions ORDER BY created_at DESC LIMIT 200"
            )
        ]
        counts["temporary_objects"] = [
            dict(r)
            for r in conn.execute(
                "SELECT "
                "object_id,owner_user_id,file_size,expires_at,deletion_status,error "
                "FROM temporary_share_objects ORDER BY created_at DESC LIMIT 200"
            )
        ]
        counts["time_basis"] = (
            "UTC day; wire attempts conservatively include failed sends; "
            "traffic retains configured audit window"
        )
        return counts


@router.post("/admin/shares/cleanup/run")
def cleanup(request: Request, p: AdminPrincipal):
    request.app.state.share_expiry.sweep()
    return {"status": "RECONCILED"}
