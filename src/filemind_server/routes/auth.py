import time

from fastapi import APIRouter, HTTPException, Request, Response

from filemind_server.accounts import PUBLIC_USER, audit, create_account
from filemind_server.dependencies import AdminPrincipal, Principal, UserPrincipal
from filemind_server.models import Credentials, Registration
from filemind_server.security import check_password, new_token, password_hash, token_hash

router = APIRouter()
# Comparable password work for unknown users, generated once per process, never a login credential.
DUMMY_HASH = password_hash(new_token())


def platform_values(request: Request) -> dict:
    with request.app.state.db.transaction() as conn:
        values = dict(conn.execute("SELECT key,value FROM platform_settings").fetchall())
    return {
        "registration_open": values.get(
            "registration_open", str(request.app.state.settings.registration_open).lower()
        )
        == "true",
        "announcement": values.get("announcement", ""),
    }


def rate_limit(request: Request, username: str) -> str:
    key = token_hash(f"{request.client.host if request.client else 'local'}:{username.lower()}")
    now = int(time.time())
    with request.app.state.db.transaction(write=True) as conn:
        conn.execute("DELETE FROM login_attempts WHERE started_at<?", (now - 900,))
        row = conn.execute("SELECT count FROM login_attempts WHERE key=?", (key,)).fetchone()
        if row and row[0] >= request.app.state.settings.login_limit:
            raise HTTPException(429, "尝试次数过多，请在 15 分钟后重试。")
        # Per-IP aggregate also limits attempts made with many different usernames.
        ip_key = token_hash(f"ip:{request.client.host if request.client else 'local'}")
        total = conn.execute("SELECT count FROM login_attempts WHERE key=?", (ip_key,)).fetchone()
        if total and total[0] >= request.app.state.settings.login_limit * 5:
            raise HTTPException(429, "当前来源请求过多，请稍后再试。")
        for target in (key, ip_key):
            conn.execute(
                "INSERT INTO login_attempts VALUES (?,1,?) ON CONFLICT(key) "
                "DO UPDATE SET count=count+1",
                (target, now),
            )
    return key


def public_user(user: dict) -> dict:
    return {field.strip(): user[field.strip()] for field in PUBLIC_USER.split(",")}


@router.get("/public/config")
def public_config(request: Request):
    return {
        **platform_values(request),
        "milestones": {
            "M1": "available",
            "M2": "available",
            "M3": "available",
            "M4": "local_available",
            "M5": "local_available",
            "M6": "local_available",
        },
    }


@router.post("/auth/register", status_code=201)
def register(data: Registration, request: Request):
    rate_limit(request, data.username)
    if not platform_values(request)["registration_open"]:
        raise HTTPException(403, "当前未开放注册。")
    with request.app.state.db.transaction(write=True) as conn:
        return create_account(conn, data)


def login(data: Credentials, request: Request, response: Response, admin: bool) -> dict:
    key = rate_limit(request, data.username)
    with request.app.state.db.transaction() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username=?", (data.username.lower(),)
        ).fetchone()
    valid = check_password(data.password, row["password_hash"] if row else DUMMY_HASH)
    if not valid or not row or not row["enabled"]:
        raise HTTPException(401, "用户名或密码错误，或账号已停用。")
    if admin and row["role"] != "system_admin":
        raise HTTPException(403, "该账号没有系统管理员权限。")
    token, csrf = new_token(), new_token()
    now = int(time.time())
    with request.app.state.db.transaction(write=True) as conn:
        # Recheck account state while serializing with admin updates.
        user = conn.execute("SELECT * FROM users WHERE id=?", (row["id"],)).fetchone()
        if not user["enabled"] or (admin and user["role"] != "system_admin"):
            raise HTTPException(403, "账号权限已变更，请重新登录。")
        conn.execute("DELETE FROM auth_sessions WHERE expires_at<=?", (now,))
        conn.execute(
            "INSERT INTO auth_sessions VALUES (?,?,?,?,?,?,?)",
            (
                token_hash(token),
                user["id"],
                "admin" if admin else "user",
                None,
                csrf,
                now,
                now + request.app.state.settings.session_seconds,
            ),
        )
        conn.execute("DELETE FROM login_attempts WHERE key=?", (key,))
        audit(conn, user["id"], "admin.login" if admin else "account.login", user["id"], {})
    response.set_cookie(
        "fm_admin" if admin else "fm_user",
        token,
        httponly=True,
        samesite="strict",
        secure=request.app.state.settings.secure_cookies,
        max_age=request.app.state.settings.session_seconds,
        path="/api/admin" if admin else "/api",
    )
    return {
        "user": public_user(dict(user)),
        "csrf_token": csrf,
        "access_token": token,
        "expires_at": now + request.app.state.settings.session_seconds,
    }


@router.post("/auth/login")
def user_login(data: Credentials, request: Request, response: Response):
    return login(data, request, response, False)


@router.post("/admin/auth/login")
def admin_login(data: Credentials, request: Request, response: Response):
    return login(data, request, response, True)


@router.get("/auth/session")
def session(principal: UserPrincipal):
    return {
        "user": public_user(principal.user),
        "csrf_token": principal.session["csrf_token"],
        "device_id": principal.session["device_id"],
    }


@router.get("/admin/auth/session")
def admin_session(principal: AdminPrincipal):
    return {"user": public_user(principal.user), "csrf_token": principal.session["csrf_token"]}


def logout(request: Request, response: Response, principal: Principal, admin: bool):
    with request.app.state.db.transaction(write=True) as conn:
        conn.execute(
            "DELETE FROM auth_sessions WHERE token_hash=?", (principal.session["token_hash"],)
        )
        audit(conn, principal.user["id"], "session.logout", principal.user["id"], {})
    response.delete_cookie(
        "fm_admin" if admin else "fm_user", path="/api/admin" if admin else "/api"
    )
    return {"ok": True}


@router.post("/auth/logout")
def user_logout(request: Request, response: Response, principal: UserPrincipal):
    return logout(request, response, principal, False)


@router.post("/admin/auth/logout")
def admin_logout(request: Request, response: Response, principal: AdminPrincipal):
    return logout(request, response, principal, True)
