import json
import os
import shutil
import time

import psutil
from fastapi import APIRouter, Depends, HTTPException, Request

from filemind_server.accounts import PUBLIC_USER, audit
from filemind_server.dependencies import AdminPrincipal, current_admin
from filemind_server.models import PlatformPatch, UserPatch
from filemind_server.routes.auth import platform_values
from filemind_server.routes.devices import device_view

router = APIRouter(prefix="/admin", dependencies=[Depends(current_admin)])


@router.get("/overview")
def overview(request: Request):
    with request.app.state.db.transaction() as conn:
        users = conn.execute("SELECT count(*) FROM users").fetchone()[0]
        enabled = conn.execute("SELECT count(*) FROM users WHERE enabled=1").fetchone()[0]
        devices = conn.execute("SELECT count(*) FROM devices WHERE revoked_at IS NULL").fetchone()[
            0
        ]
        online = conn.execute(
            "SELECT count(*) FROM devices WHERE revoked_at IS NULL AND last_seen>?",
            (int(time.time()) - 90,),
        ).fetchone()[0]
        audit_count = conn.execute("SELECT count(*) FROM audit_logs").fetchone()[0]
    disk = shutil.disk_usage(request.app.state.settings.data_dir)
    process = psutil.Process(os.getpid())
    return {
        "users": users,
        "enabled_users": enabled,
        "devices": devices,
        "online_devices": online,
        "audit_entries": audit_count,
        "process_rss_bytes": process.memory_info().rss,
        "process_threads": process.num_threads(),
        "process_cpu_seconds": process.cpu_times().user + process.cpu_times().system,
        "host_available_memory_bytes": psutil.virtual_memory().available,
        "disk_free_bytes": disk.free,
        "database_bytes": request.app.state.db.path.stat().st_size,
        "uptime_seconds": max(0, int(time.time() - request.app.state.started_at)),
        "services": {
            "accounts": "running",
            "devices": "running",
            "devicelink": "running" if request.app.state.settings.relay_enabled else "disabled",
            "archive": "running"
            if request.app.state.settings.archive_worker_enabled
            else "worker_paused",
            "library": "running",
            "share": "running",
        },
    }


@router.get("/system-status")
def status(request: Request):
    from filemind_server.system_status import system_status

    return system_status(request.app)


@router.get("/storage/baidu")
def baidu_status(request: Request):
    return request.app.state.archives.provider("baidu").status()


@router.post("/storage/baidu/probe")
def baidu_probe(request: Request):
    try:
        quota = request.app.state.archives.provider("baidu").quota()
    except (ValueError, OSError, NotImplementedError):
        raise HTTPException(409, "百度网盘连接未授权、不可用或需重新授权") from None
    return {
        "connection_status": "连接成功",
        "quota": quota,
        "production_verified": False,
        "note": "仅本次配额只读请求，不代表上传下载验收",
    }


@router.post("/storage/baidu/disconnect")
def baidu_disconnect(request: Request, p: AdminPrincipal):
    result = request.app.state.archives.provider("baidu").disconnect()
    with request.app.state.db.transaction(True) as conn:
        audit(conn, p.user["id"], "storage.baidu.disconnected", None, {})
    return result


@router.get("/users")
def users(request: Request, query: str = "", offset: int = 0, limit: int = 50):
    limit, offset = max(1, min(limit, 100)), max(0, offset)
    with request.app.state.db.transaction() as conn:
        # LIKE is parameterized, and output is restricted to operational metadata.
        return [
            dict(row)
            for row in conn.execute(
                f"SELECT {PUBLIC_USER} FROM users WHERE username LIKE ? OR display_name LIKE ? "
                "ORDER BY created_at,id LIMIT ? OFFSET ?",
                (f"%{query[:64]}%", f"%{query[:64]}%", limit, offset),
            ).fetchall()
        ]


@router.patch("/users/{user_id}")
def update_user(user_id: str, data: UserPatch, request: Request, principal: AdminPrincipal):
    changes = data.model_dump(exclude_unset=True, exclude_none=True)
    if not changes:
        raise HTTPException(422, "没有可修改的字段。")
    with request.app.state.db.transaction(write=True) as conn:
        row = conn.execute(f"SELECT {PUBLIC_USER} FROM users WHERE id=?", (user_id,)).fetchone()
        if not row:
            raise HTTPException(404, "用户不存在。")
        after = {**dict(row), **changes}
        if (
            row["role"] == "system_admin"
            and row["enabled"]
            and (after["role"] != "system_admin" or not after["enabled"])
        ):
            count = conn.execute(
                "SELECT count(*) FROM users WHERE role='system_admin' AND enabled=1"
            ).fetchone()[0]
            if count <= 1:
                raise HTTPException(409, "必须保留至少一个有效系统管理员。")
        # Field names originate only from the validated model, not request strings.
        conn.execute(
            "UPDATE users SET " + ",".join(f"{key}=?" for key in changes) + " WHERE id=?",
            (*changes.values(), user_id),
        )
        if "role" in changes or changes.get("enabled") is False:
            conn.execute("DELETE FROM auth_sessions WHERE user_id=?", (user_id,))
        audit(conn, principal.user["id"], "admin.user.updated", user_id, changes)
    return {"ok": True}


@router.get("/devices")
def all_devices(request: Request, offset: int = 0):
    with request.app.state.db.transaction() as conn:
        return [
            device_view(row)
            for row in conn.execute(
                "SELECT d.*,u.username FROM devices d JOIN users u ON u.id=d.user_id "
                "ORDER BY d.created_at DESC,d.id LIMIT 100 OFFSET ?",
                (max(0, offset),),
            ).fetchall()
        ]


@router.delete("/devices/{device_id}")
def revoke_device(device_id: str, request: Request, p: AdminPrincipal):
    with request.app.state.db.transaction(write=True) as conn:
        if not conn.execute("SELECT id FROM devices WHERE id=?", (device_id,)).fetchone():
            raise HTTPException(404, "设备不存在。")
        conn.execute("UPDATE devices SET revoked_at=? WHERE id=?", (int(time.time()), device_id))
        conn.execute("DELETE FROM auth_sessions WHERE device_id=?", (device_id,))
        audit(conn, p.user["id"], "admin.device.revoked", device_id, {})
    return {"ok": True}


@router.get("/audit")
def logs(request: Request, offset: int = 0):
    with request.app.state.db.transaction() as conn:
        rows = conn.execute(
            "SELECT a.*,u.username actor FROM audit_logs a LEFT JOIN users u ON u.id=a.actor_id "
            "ORDER BY a.id DESC LIMIT 100 OFFSET ?",
            (max(0, offset),),
        ).fetchall()
    return [{**dict(row), "detail": json.loads(row["detail"])} for row in rows]


@router.get("/settings")
def get_settings(request: Request):
    return platform_values(request)


@router.put("/settings")
def set_settings(data: PlatformPatch, request: Request, p: AdminPrincipal):
    with request.app.state.db.transaction(write=True) as conn:
        for key, value in {
            "registration_open": str(data.registration_open).lower(),
            "announcement": data.announcement,
        }.items():
            conn.execute(
                "INSERT INTO platform_settings VALUES (?,?) ON CONFLICT(key) "
                "DO UPDATE SET value=excluded.value",
                (key, value),
            )
        audit(conn, p.user["id"], "admin.settings.updated", None, data.model_dump())
    return {"ok": True}
