import json
import sqlite3
import time
import uuid

from fastapi import HTTPException

from filemind_server.models import Registration
from filemind_server.security import password_hash

PUBLIC_USER = "id, username, display_name, role, enabled, created_at, quota_bytes"


def audit(conn, actor_id: str | None, action: str, target: str | None, detail: dict) -> None:
    conn.execute(
        ("INSERT INTO audit_logs(actor_id,action,target_id,detail,created_at) VALUES (?,?,?,?,?)"),
        (actor_id, action, target, json.dumps(detail, ensure_ascii=False), int(time.time())),
    )


def create_account(conn, data: Registration, role: str = "user") -> dict:
    user_id, workspace_id = str(uuid.uuid4()), str(uuid.uuid4())
    now = int(time.time())
    try:
        conn.execute(
            "INSERT INTO users(id,username,display_name,password_hash,role,created_at) "
            "VALUES (?,?,?,?,?,?)",
            (
                user_id,
                data.username.lower(),
                data.display_name,
                password_hash(data.password),
                role,
                now,
            ),
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "用户名已存在。") from exc
    conn.execute(
        "INSERT INTO workspaces VALUES (?,?,?,?)", (workspace_id, "我的空间", user_id, now)
    )
    conn.execute("INSERT INTO workspace_members VALUES (?,?,?)", (workspace_id, user_id, "admin"))
    audit(conn, user_id, "account.created", user_id, {"role": role})
    return dict(conn.execute(f"SELECT {PUBLIC_USER} FROM users WHERE id=?", (user_id,)).fetchone())
