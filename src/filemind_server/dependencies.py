import hmac
import time
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from filemind_server.security import token_hash


@dataclass(frozen=True)
class Principal:
    user: dict
    session: dict


def authenticate(request: Request, admin: bool = False) -> Principal:
    authorization = request.headers.get("Authorization", "")
    bearer = authorization.startswith("Bearer ")
    cookie_name = "fm_admin" if admin else "fm_user"
    token = authorization[7:] if bearer else request.cookies.get(cookie_name, "")
    if not token or len(token) > 256:
        raise HTTPException(401, "请先登录。")
    with request.app.state.db.transaction() as conn:
        session = conn.execute(
            "SELECT * FROM auth_sessions WHERE token_hash=? AND expires_at>?",
            (token_hash(token), int(time.time())),
        ).fetchone()
        if not session:
            raise HTTPException(401, "登录已过期或授权已撤销。")
        user = conn.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
        if not user or not user["enabled"]:
            raise HTTPException(401, "账号不可用。")
        if admin and (user["role"] != "system_admin" or session["scope"] != "admin"):
            raise HTTPException(403, "需要独立的系统管理员登录。")
        if not admin and session["scope"] == "admin":
            raise HTTPException(403, "请使用用户端登录。")
        if session["device_id"]:
            device = conn.execute(
                "SELECT revoked_at FROM devices WHERE id=? AND user_id=?",
                (session["device_id"], user["id"]),
            ).fetchone()
            if not device or device["revoked_at"] is not None:
                raise HTTPException(401, "设备授权已撤销。")
    if not bearer and request.method not in ("GET", "HEAD", "OPTIONS"):
        supplied = request.headers.get("X-CSRF-Token", "")
        if not hmac.compare_digest(supplied, session["csrf_token"]):
            raise HTTPException(403, "请求校验失败，请刷新后重试。")
    return Principal(dict(user), dict(session))


def current_user(request: Request) -> Principal:
    return authenticate(request)


def current_admin(request: Request) -> Principal:
    return authenticate(request, admin=True)


def account_user(request: Request) -> Principal:
    principal = authenticate(request)
    if principal.session["scope"] != "user":
        raise HTTPException(403, "此操作需要账户登录，不接受设备凭据。")
    return principal


UserPrincipal = Annotated[Principal, Depends(current_user)]
AdminPrincipal = Annotated[Principal, Depends(current_admin)]
AccountPrincipal = Annotated[Principal, Depends(account_user)]


def current_device(request: Request) -> Principal:
    principal = authenticate(request)
    if principal.session["scope"] != "device" or not principal.session["device_id"]:
        raise HTTPException(403, "文件传输需要有效的独立设备凭据。")
    return principal


DevicePrincipal = Annotated[Principal, Depends(current_device)]
