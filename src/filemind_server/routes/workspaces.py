import time
import uuid

from fastapi import APIRouter, HTTPException, Request

from filemind_server.accounts import audit
from filemind_server.dependencies import AccountPrincipal, Principal
from filemind_server.models import MemberCreate, WorkspaceCreate

router = APIRouter()


@router.get("/workspaces")
def workspaces(request: Request, principal: AccountPrincipal):
    with request.app.state.db.transaction() as conn:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT w.*,m.permission FROM workspaces w JOIN workspace_members m "
                "ON w.id=m.workspace_id WHERE m.user_id=? ORDER BY w.created_at,w.id",
                (principal.user["id"],),
            ).fetchall()
        ]


@router.post("/workspaces", status_code=201)
def create_workspace(data: WorkspaceCreate, request: Request, p: AccountPrincipal):
    workspace_id = str(uuid.uuid4())
    with request.app.state.db.transaction(write=True) as conn:
        count = conn.execute(
            "SELECT count(*) FROM workspaces WHERE owner_id=?", (p.user["id"],)
        ).fetchone()[0]
        if count >= 20:
            raise HTTPException(409, "空间数量已达上限。")
        conn.execute(
            "INSERT INTO workspaces VALUES (?,?,?,?)",
            (workspace_id, data.name, p.user["id"], int(time.time())),
        )
        conn.execute(
            "INSERT INTO workspace_members VALUES (?,?,?)", (workspace_id, p.user["id"], "admin")
        )
        audit(conn, p.user["id"], "workspace.created", workspace_id, {})
    return {"id": workspace_id, "name": data.name}


def require_member(conn, workspace_id: str, p: Principal, manage: bool = False):
    member = conn.execute(
        "SELECT w.owner_id,m.permission FROM workspaces w JOIN workspace_members m "
        "ON w.id=m.workspace_id WHERE w.id=? AND m.user_id=?",
        (workspace_id, p.user["id"]),
    ).fetchone()
    if not member:
        # System admins do not bypass workspace privacy.
        raise HTTPException(404, "空间不存在或未授权。")
    if manage and not (
        member["owner_id"] == p.user["id"]
        or (
            p.user["role"] in ("workspace_admin", "system_admin")
            and member["permission"] == "admin"
        )
    ):
        raise HTTPException(403, "需要此空间的成员管理权限。")
    return member


@router.get("/workspaces/{workspace_id}/members")
def members(workspace_id: str, request: Request, p: AccountPrincipal):
    with request.app.state.db.transaction() as conn:
        require_member(conn, workspace_id, p)
        return [
            dict(row)
            for row in conn.execute(
                "SELECT u.id,u.username,u.display_name,m.permission FROM workspace_members m "
                "JOIN users u ON u.id=m.user_id WHERE m.workspace_id=?",
                (workspace_id,),
            ).fetchall()
        ]


@router.post("/workspaces/{workspace_id}/members")
def add_member(workspace_id: str, data: MemberCreate, request: Request, p: AccountPrincipal):
    with request.app.state.db.transaction(write=True) as conn:
        require_member(conn, workspace_id, p, manage=True)
        user = conn.execute(
            "SELECT id FROM users WHERE username=?", (data.username.lower(),)
        ).fetchone()
        if not user:
            raise HTTPException(404, "用户不存在。")
        owner = conn.execute(
            "SELECT owner_id FROM workspaces WHERE id=?", (workspace_id,)
        ).fetchone()[0]
        if owner == user["id"]:
            raise HTTPException(409, "不能修改空间所有者的权限。")
        conn.execute(
            "INSERT INTO workspace_members VALUES (?,?,?) ON "
            "CONFLICT(workspace_id,user_id) "
            "DO UPDATE SET permission=excluded.permission",
            (workspace_id, user["id"], data.permission),
        )
        if data.permission == "viewer":
            revoke_member_shares(conn, workspace_id, user["id"])
        audit(
            conn,
            p.user["id"],
            "workspace.member.updated",
            workspace_id,
            {"member_id": user["id"], "permission": data.permission},
        )
    return {"ok": True}


@router.delete("/workspaces/{workspace_id}/members/{user_id}")
def remove_member(workspace_id: str, user_id: str, request: Request, p: AccountPrincipal):
    with request.app.state.db.transaction(write=True) as conn:
        membership = require_member(conn, workspace_id, p, manage=True)
        if membership["owner_id"] == user_id:
            raise HTTPException(409, "不能移除空间所有者。")
        updated = conn.execute(
            "DELETE FROM workspace_members WHERE workspace_id=? AND user_id=?",
            (workspace_id, user_id),
        )
        if not updated.rowcount:
            raise HTTPException(404, "成员不存在。")
        revoke_member_shares(conn, workspace_id, user_id)
        audit(conn, p.user["id"], "workspace.member.removed", workspace_id, {"member_id": user_id})
    return {"ok": True}


def revoke_member_shares(conn, workspace_id, user_id):
    now = time.time()
    from filemind_server.shares import event

    for link in conn.execute(
        "SELECT share_id FROM share_links WHERE status='ACTIVE' AND workspace_id=? "
        "AND owner_user_id=?",
        (workspace_id, user_id),
    ):
        event(conn, link["share_id"], "WORKSPACE_REVOKED", user_id)
    conn.execute(
        "UPDATE share_links SET status='REVOKED',revoked_at=?,updated_at=? "
        "WHERE workspace_id=? AND owner_user_id=? AND status='ACTIVE'",
        (now, now, workspace_id, user_id),
    )
    conn.execute(
        "UPDATE share_download_sessions SET "
        "status='CANCELLED',reserved_quota=0,error='WORKSPACE_ACCESS_ENDED' "
        "WHERE reserved_quota=1 AND share_id IN (SELECT share_id FROM "
        "share_links WHERE workspace_id=? AND owner_user_id=? AND "
        "status='REVOKED')",
        (workspace_id, user_id),
    )
