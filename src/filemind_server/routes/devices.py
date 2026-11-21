import time
import uuid

from fastapi import APIRouter, HTTPException, Request

from filemind_server.accounts import audit
from filemind_server.dependencies import AccountPrincipal, UserPrincipal
from filemind_server.models import DeviceCreate
from filemind_server.security import new_token, token_hash

router = APIRouter()


def device_view(row) -> dict:
    value = dict(row)
    value["online"] = value["revoked_at"] is None and (value["last_seen"] or 0) > time.time() - 90
    return value


@router.get("/devices")
def devices(request: Request, principal: AccountPrincipal):
    with request.app.state.db.transaction() as conn:
        rows = conn.execute(
            "SELECT * FROM devices WHERE user_id=? ORDER BY created_at DESC,id",
            (principal.user["id"],),
        ).fetchall()
    return [device_view(row) for row in rows]


@router.post("/devices", status_code=201)
def register_device(data: DeviceCreate, request: Request, principal: AccountPrincipal):
    device_id, token, now = str(uuid.uuid4()), new_token(), int(time.time())
    with request.app.state.db.transaction(write=True) as conn:
        previous = None
        if data.installation_id:
            previous = conn.execute(
                "SELECT d.* FROM device_installations i JOIN devices d ON "
                "d.id=i.device_id WHERE i.user_id=? AND i.installation_id=? AND "
                "d.revoked_at IS NULL",
                (principal.user["id"], data.installation_id),
            ).fetchone()
            if previous:
                device_id = previous["id"]
        count = conn.execute(
            "SELECT count(*) FROM devices WHERE user_id=? AND revoked_at IS NULL",
            (principal.user["id"],),
        ).fetchone()[0]
        if count >= 20 and not previous:
            raise HTTPException(409, "有效设备已达 20 台，请先撤销不再使用的设备。")
        if previous:
            conn.execute(
                "UPDATE devices SET name=?,platform=?,last_seen=? WHERE id=?",
                (data.name, data.platform, now, device_id),
            )
        else:
            conn.execute(
                "INSERT INTO devices VALUES (?,?,?,?,?,?,NULL)",
                (device_id, principal.user["id"], data.name, data.platform, now, now),
            )
        if data.installation_id:
            conn.execute(
                "INSERT INTO device_installations VALUES (?,?,?) ON "
                "CONFLICT(user_id,installation_id) DO UPDATE SET "
                "device_id=excluded.device_id",
                (principal.user["id"], data.installation_id, device_id),
            )
        conn.execute(
            "INSERT INTO auth_sessions VALUES (?,?,?,?,?,?,?)",
            (
                token_hash(token),
                principal.user["id"],
                "device",
                device_id,
                new_token(),
                now,
                now + request.app.state.settings.session_seconds,
            ),
        )
        audit(
            conn, principal.user["id"], "device.registered", device_id, {"platform": data.platform}
        )
        row = conn.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone()
    return {
        "device": device_view(row),
        "device_token": token,
        "expires_at": now + request.app.state.settings.session_seconds,
    }


@router.post("/devices/{device_id}/heartbeat")
def heartbeat(device_id: str, request: Request, principal: UserPrincipal):
    if principal.session["device_id"] != device_id:
        raise HTTPException(403, "心跳必须使用此设备的专属凭据。")
    with request.app.state.db.transaction(write=True) as conn:
        updated = conn.execute(
            "UPDATE devices SET last_seen=? WHERE id=? AND revoked_at IS NULL",
            (int(time.time()), device_id),
        )
        if not updated.rowcount:
            raise HTTPException(401, "设备已撤销。")
    return {"ok": True}


@router.delete("/devices/{device_id}")
def revoke_device(device_id: str, request: Request, principal: AccountPrincipal):
    with request.app.state.db.transaction(write=True) as conn:
        row = conn.execute(
            "SELECT id FROM devices WHERE id=? AND user_id=?", (device_id, principal.user["id"])
        ).fetchone()
        if not row:
            raise HTTPException(404, "设备不存在。")
        conn.execute("UPDATE devices SET revoked_at=? WHERE id=?", (int(time.time()), device_id))
        conn.execute("DELETE FROM auth_sessions WHERE device_id=?", (device_id,))
        audit(conn, principal.user["id"], "device.revoked", device_id, {})
    return {"ok": True}
