import time

from fastapi import APIRouter, HTTPException, Request

from filemind_server.dependencies import AdminPrincipal, DevicePrincipal
from filemind_server.models import TransferAck, TransferCreate, TransferFailure

router = APIRouter()


def listings(request, principal):
    with request.app.state.db.transaction() as conn:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT t.*,s.name sender_name,r.name receiver_name FROM transfer_sessions t "
                "JOIN devices s ON t.sender_id=s.id JOIN devices r ON t.receiver_id=r.id "
                "WHERE t.user_id=? AND (t.sender_id=? OR t.receiver_id=?) "
                "ORDER BY t.created_at DESC LIMIT 200",
                (
                    principal.user["id"],
                    principal.session["device_id"],
                    principal.session["device_id"],
                ),
            ).fetchall()
        ]


@router.get("/transfers/config")
async def config(request: Request, p: DevicePrincipal):
    settings = request.app.state.settings
    return {
        "enabled": settings.relay_enabled,
        "chunk_bytes": settings.transfer_chunk_bytes,
        "max_bytes": settings.transfer_max_bytes,
        "concurrency": settings.transfer_concurrency,
        "ttl": settings.transfer_ttl,
        "resumable": False,
        "archive": "INDEPENDENT_JOB",
    }


@router.get("/transfers/peers")
async def peers(request: Request, p: DevicePrincipal):
    with request.app.state.db.transaction() as conn:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT d.id,d.name,d.platform,r.ready_at FROM devices d JOIN device_receivers r "
                "ON d.id=r.device_id WHERE d.user_id=? AND d.id<>? AND d.revoked_at IS NULL "
                "AND r.ready_at>? AND EXISTS(SELECT 1 FROM auth_sessions a "
                "WHERE a.device_id=d.id AND a.expires_at>?)",
                (p.user["id"], p.session["device_id"], time.time() - 10, int(time.time())),
            ).fetchall()
        ]


@router.get("/transfers/inbox")
async def inbox(request: Request, p: DevicePrincipal):
    now = time.time()
    with request.app.state.db.transaction(True) as conn:
        conn.execute(
            (
                "INSERT INTO device_receivers VALUES (?,?) ON CONFLICT(device_id) "
                "DO UPDATE SET ready_at=excluded.ready_at"
            ),
            (p.session["device_id"], now),
        )
        conn.execute(
            "UPDATE devices SET last_seen=? WHERE id=? AND revoked_at IS NULL",
            (int(now), p.session["device_id"]),
        )
    await request.app.state.transfers.sweep()
    return listings(request, p)


@router.get("/transfers")
async def transfers(request: Request, p: DevicePrincipal):
    await request.app.state.transfers.sweep()
    return listings(request, p)


@router.post("/transfers", status_code=201)
async def create(data: TransferCreate, request: Request, p: DevicePrincipal):
    await request.app.state.transfers.sweep()
    return request.app.state.transfers.create(data, p)


@router.get("/transfers/{transfer_id}")
async def get_transfer(transfer_id: str, request: Request, p: DevicePrincipal):
    await request.app.state.transfers.sweep()
    return request.app.state.transfers.participant(transfer_id, p)


@router.post("/transfers/{transfer_id}/accept")
async def accept(transfer_id: str, request: Request, p: DevicePrincipal):
    request.app.state.transfers.participant(transfer_id, p, "receiver")
    return request.app.state.transfers.accept(transfer_id)


@router.post("/transfers/{transfer_id}/reject")
async def reject(transfer_id: str, request: Request, p: DevicePrincipal):
    row = request.app.state.transfers.participant(transfer_id, p, "receiver")
    if row["state"] != "WAITING_RECEIVER":
        raise HTTPException(409, "只能拒绝尚未接受的会话。")
    return await request.app.state.transfers.terminate(transfer_id, "REJECTED", "接收端拒绝。")


@router.post("/transfers/{transfer_id}/cancel")
async def cancel(transfer_id: str, request: Request, p: DevicePrincipal):
    request.app.state.transfers.participant(transfer_id, p)
    return await request.app.state.transfers.terminate(transfer_id, "CANCELLED", "参与者主动取消。")


@router.post("/transfers/{transfer_id}/fail")
async def fail(transfer_id: str, data: TransferFailure, request: Request, p: DevicePrincipal):
    request.app.state.transfers.participant(transfer_id, p)
    return await request.app.state.transfers.terminate(transfer_id, "FAILED", data.reason)


@router.post("/transfers/{transfer_id}/chunks/{seq}")
async def upload(transfer_id: str, seq: int, request: Request, p: DevicePrincipal):
    request.app.state.transfers.participant(transfer_id, p, "sender")
    return await request.app.state.transfers.upload(transfer_id, seq, request)


@router.get("/transfers/{transfer_id}/chunks/{seq}")
async def download(transfer_id: str, seq: int, request: Request, p: DevicePrincipal):
    request.app.state.transfers.participant(transfer_id, p, "receiver")
    return await request.app.state.transfers.download(transfer_id, seq, request)


@router.post("/transfers/{transfer_id}/ack")
async def acknowledge(transfer_id: str, data: TransferAck, request: Request, p: DevicePrincipal):
    request.app.state.transfers.participant(transfer_id, p, "receiver")
    return await request.app.state.transfers.acknowledge(transfer_id, data)


@router.get("/transfers/{transfer_id}/events")
async def events(transfer_id: str, request: Request, p: DevicePrincipal):
    request.app.state.transfers.participant(transfer_id, p)
    with request.app.state.db.transaction() as conn:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM transfer_events WHERE transfer_id=? ORDER BY id", (transfer_id,)
            )
        ]


@router.get("/admin/transfers")
async def admin_transfers(request: Request, p: AdminPrincipal, offset: int = 0):
    await request.app.state.transfers.sweep()
    with request.app.state.db.transaction() as conn:
        rows = conn.execute(
            "SELECT t.*,s.name sender_name,r.name receiver_name,u.username "
            "FROM transfer_sessions t "
            "JOIN devices s ON t.sender_id=s.id JOIN devices r ON t.receiver_id=r.id "
            "JOIN users u ON t.user_id=u.id ORDER BY t.created_at DESC LIMIT 100 OFFSET ?",
            (max(0, offset),),
        ).fetchall()
        totals = dict(
            conn.execute(
                "SELECT count(*) total,coalesce(sum(bytes_forwarded),0) forwarded_bytes,"
                "sum(CASE WHEN state='COMPLETED' THEN 1 ELSE 0 END) completed,"
                "sum(CASE WHEN state IN ('FAILED','EXPIRED') THEN 1 ELSE 0 END) failed,"
                "sum(CASE WHEN state IN ('ACCEPTED','CONNECTING','TRANSFERRING','VERIFYING') "
                "THEN 1 ELSE 0 END) active "
                "FROM transfer_sessions"
            ).fetchone()
        )
    return {
        "items": [dict(row) for row in rows],
        "totals": totals,
        "buffered_bytes": request.app.state.transfers.buffered_bytes(),
        "peak_buffer_bytes": request.app.state.transfers.peak_buffer_bytes,
    }
