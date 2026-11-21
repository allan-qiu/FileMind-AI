"""Durable ShareManager, access grants, independent links and owner/admin views."""

import time
import uuid

from fastapi import HTTPException

from filemind_server.dependencies import Principal
from filemind_server.library import LibraryService
from filemind_server.security import check_password, new_token, password_hash, token_hash
from filemind_server.share_config import config


def event(conn, share_id, kind, actor=None, session=None, size=0):
    conn.execute(
        "INSERT INTO "
        "share_events(share_id,kind,actor_id,session_id,bytes_sent,created_at)"
        " VALUES (?,?,?,?,?,?)",
        (share_id, kind, actor, session, size, time.time()),
    )


class ShareManager:
    def __init__(self, db, archives):
        self.db, self.archives = db, archives
        self.library = LibraryService(db, archives)

    def source(self, conn, row):
        user = conn.execute(
            "SELECT * FROM users WHERE id=? AND enabled=1", (row["owner_user_id"],)
        ).fetchone()
        if not user:
            raise HTTPException(410, "分享不可用")
        if row["source_type"] == "temporary":
            obj = conn.execute(
                "SELECT * FROM temporary_share_objects WHERE object_id=? AND "
                "deletion_status='LIVE'",
                (row["temporary_object_id"],),
            ).fetchone()
            if not obj or obj["expires_at"] <= time.time():
                raise HTTPException(410, "分享内容不可用")
            return {
                "size": obj["file_size"],
                "sha256": obj["content_sha256"],
                "key": obj["storage_location"],
                "provider": "temporary",
            }
        version = conn.execute(
            "SELECT v.*,s.object_key,s.size_bytes,s.provider FROM file_versions "
            "v JOIN storage_objects s ON s.id=v.object_id WHERE v.id=?",
            (row["file_version_id"],),
        ).fetchone()
        if not version:
            raise HTTPException(410, "分享内容不可用")
        try:
            file = self.library.owned(conn, version["file_id"], Principal(dict(user), {}), True)
            if (
                file["user_id"] != user["id"]
                or file["deleted_at"]
                or file["workspace_id"] != row["workspace_id"]
            ):
                raise HTTPException(410, "文件授权发生变化")
        except HTTPException:
            raise HTTPException(410, "分享内容授权已结束") from None
        return {
            "size": version["size_bytes"],
            "sha256": version["sha256"],
            "key": version["object_key"],
            "provider": version["provider"],
        }

    @staticmethod
    def active(row):
        if row["status"] != "ACTIVE" or row["expires_at"] <= time.time():
            raise HTTPException(410, "分享已到期或撤销")

    @staticmethod
    def owned(conn, uid, sid, admin=False):
        row = conn.execute(
            "SELECT * FROM share_links WHERE share_id=?"
            + ("" if admin else " AND owner_user_id=?"),
            (sid,) if admin else (sid, uid),
        ).fetchone()
        if not row:
            raise HTTPException(404, "分享不存在或未授权")
        return dict(row)

    def view(self, conn, row):
        data = dict(row)
        for key in ("token_hash", "password_hash"):
            data.pop(key, None)
        data["password_required"] = bool(row["password_hash"])
        data["status"] = (
            "EXPIRED"
            if row["status"] == "ACTIVE" and row["expires_at"] <= time.time()
            else row["status"]
        )
        data["reserved_downloads"] = conn.execute(
            "SELECT count(*) FROM share_download_sessions WHERE share_id=? AND "
            "reserved_quota=1 AND reservation_expires_at>?",
            (row["share_id"], time.time()),
        ).fetchone()[0]
        data["remaining_downloads"] = (
            row["max_downloads"] - row["completed_downloads"] - data["reserved_downloads"]
        )
        try:
            data["size_bytes"] = self.source(conn, row)["size"]
        except HTTPException:
            data["size_bytes"] = None
        return data

    def create(self, p, data):
        cfg, now, sid, token = config(self.db), time.time(), str(uuid.uuid4()), new_token()
        ttl = data.get("ttl_seconds") or cfg.default_ttl
        maximum = data.get("max_downloads") or cfg.default_downloads
        if ttl > cfg.max_ttl or maximum > cfg.max_downloads:
            raise HTTPException(422, "分享配置超过系统上限")
        hashed = password_hash(data["password"]) if data.get("password") else None
        with self.db.transaction(True) as conn:
            vid, oid = data.get("file_version_id"), data.get("temporary_object_id")
            if bool(vid) == bool(oid):
                raise HTTPException(422, "必须且只能选择一种文件来源")
            workspace = None
            if vid:
                if not cfg.allow_library:
                    raise HTTPException(403, "系统已关闭文件库分享")
                v = conn.execute("SELECT * FROM file_versions WHERE id=?", (vid,)).fetchone()
                if not v:
                    raise HTTPException(404, "版本不存在或未授权")
                file = self.library.owned(conn, v["file_id"], p, True)
                if file["user_id"] != p.user["id"] or file["deleted_at"]:
                    raise HTTPException(403, "仅文件所有者可分享有效文件")
                name, workspace = v["filename"], file["workspace_id"]
            else:
                if not cfg.allow_temporary:
                    raise HTTPException(403, "系统已关闭临时分享")
                obj = conn.execute(
                    "SELECT * FROM temporary_share_objects WHERE object_id=? AND "
                    "owner_user_id=? AND deletion_status='LIVE' AND expires_at>?",
                    (oid, p.user["id"], now),
                ).fetchone()
                if not obj:
                    raise HTTPException(404, "临时对象不存在或未授权")
                name = obj["original_filename"]
                conn.execute(
                    "UPDATE temporary_share_objects SET expires_at=max(expires_at,?) "
                    "WHERE object_id=?",
                    (now + ttl, oid),
                )
            conn.execute(
                "INSERT INTO "
                "share_links(share_id,owner_user_id,workspace_id,source_type,file_version_id,temporary_object_id,token_hash,display_name,max_downloads,expires_at,password_hash,status,created_at,updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,'ACTIVE',?,?)",
                (
                    sid,
                    p.user["id"],
                    workspace,
                    "library" if vid else "temporary",
                    vid,
                    oid,
                    token_hash(token),
                    name,
                    maximum,
                    now + ttl,
                    hashed,
                    now,
                    now,
                ),
            )
            event(conn, sid, "CREATED", p.user["id"])
            row = self.owned(conn, p.user["id"], sid)
            self.source(conn, row)
            return {**self.view(conn, row), "share_url": "/s/#" + token}

    def update(self, p, sid, data):
        cfg, now = config(self.db), time.time()
        hashed = password_hash(data["password"]) if data.get("password") else None
        with self.db.transaction(True) as conn:
            row = self.owned(conn, p.user["id"], sid)
            self.active(row)
            view = self.view(conn, row)
            maximum = data.get("max_downloads", row["max_downloads"])
            expires = now + data["ttl_seconds"] if "ttl_seconds" in data else row["expires_at"]
            if (
                maximum < row["completed_downloads"] + view["reserved_downloads"]
                or maximum > cfg.max_downloads
                or expires > now + cfg.max_ttl
            ):
                raise HTTPException(409, "新额度小于已完成与预留数量，或配置超过上限")
            conn.execute(
                "UPDATE share_links SET "
                "max_downloads=?,expires_at=?,password_hash=?,updated_at=? WHERE "
                "share_id=?",
                (
                    maximum,
                    expires,
                    hashed if "password" in data else row["password_hash"],
                    now,
                    sid,
                ),
            )
            if "password" in data:
                conn.execute("DELETE FROM share_access_grants WHERE share_id=?", (sid,))
            if row["temporary_object_id"]:
                conn.execute(
                    "UPDATE temporary_share_objects SET expires_at=max(expires_at,?) "
                    "WHERE object_id=?",
                    (expires, row["temporary_object_id"]),
                )
            event(conn, sid, "UPDATED", p.user["id"])
            return self.view(conn, self.owned(conn, p.user["id"], sid))

    def revoke(self, p, sid, admin=False):
        with self.db.transaction(True) as conn:
            row = self.owned(conn, p.user["id"], sid, admin)
            if row["status"] != "REVOKED":
                conn.execute(
                    "UPDATE share_links SET status='REVOKED',revoked_at=?,updated_at=? "
                    "WHERE share_id=?",
                    (time.time(), time.time(), sid),
                )
                conn.execute(
                    "UPDATE share_download_sessions SET "
                    "status='CANCELLED',reserved_quota=0,failed_at=?,error='SHARE_REVOKED'"
                    " WHERE share_id=? AND reserved_quota=1",
                    (time.time(), sid),
                )
                conn.execute("DELETE FROM share_access_grants WHERE share_id=?", (sid,))
                event(conn, sid, "REVOKED", p.user["id"])
            return self.view(conn, self.owned(conn, p.user["id"], sid, admin))

    def listing(self, p, admin=False, q="", status="", owner="", offset=0, after=None, before=None):
        with self.db.transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM share_links WHERE (? OR owner_user_id=?) AND (?='' OR"
                " owner_user_id=?) AND instr(lower(display_name),lower(?))>0 "
                "AND (?='' OR CASE WHEN status='ACTIVE' AND expires_at<=? THEN "
                "'EXPIRED' ELSE status END=?) "
                "AND (? IS NULL OR created_at>=?) AND (? IS NULL OR created_at<=?) "
                "ORDER BY created_at DESC LIMIT 200 OFFSET ?",
                (
                    admin,
                    p.user["id"],
                    owner,
                    owner,
                    q,
                    status,
                    time.time(),
                    status,
                    after,
                    after,
                    before,
                    before,
                    offset,
                ),
            ).fetchall()
            return [self.view(conn, r) for r in rows]

    def rate(self, ip, kind="request", limit=120):
        key, now = token_hash("share-" + kind + ":" + ip), time.time()
        with self.db.transaction(True) as conn:
            conn.execute("DELETE FROM share_rate_limits WHERE expires_at<=?", (now,))
            row = conn.execute("SELECT count FROM share_rate_limits WHERE key=?", (key,)).fetchone()
            if row and row[0] >= limit:
                raise HTTPException(429, "访问验证过于频繁，请在一分钟后重试")
            conn.execute(
                "INSERT INTO share_rate_limits VALUES (?,1,?) ON CONFLICT(key) DO "
                "UPDATE SET count=count+1",
                (key, now + 60),
            )

    def verify(self, token, password, ip):
        self.rate(ip)
        with self.db.transaction() as conn:
            found = conn.execute(
                "SELECT * FROM share_links WHERE token_hash=?", (token_hash(token),)
            ).fetchone()
            row = dict(found) if found else None
        if not row:
            self.rate(ip, "credentials", 20)
            raise HTTPException(404, "分享或访问密码无效")
        self.active(row)
        if row["password_hash"] and password is None:
            return {"password_required": True}
        if row["password_hash"]:
            self.rate(ip, "credentials", 20)
            self.rate("share:" + token_hash(token), "credentials", 60)
        if row["password_hash"] and not check_password(password or "", row["password_hash"]):
            with self.db.transaction(True) as conn:
                event(conn, row["share_id"], "PASSWORD_FAILED")
            raise HTTPException(404, "分享或访问密码无效")
        grant = new_token()
        with self.db.transaction(True) as conn:
            row = self.owned(conn, "", row["share_id"], True)
            self.active(row)
            src = self.source(conn, row)
            conn.execute(
                "INSERT INTO share_access_grants VALUES (?,?,?)",
                (token_hash(grant), row["share_id"], min(row["expires_at"], time.time() + 900)),
            )
            event(conn, row["share_id"], "ACCESS_VERIFIED")
            view = self.view(conn, row)
            return {
                "grant": grant,
                "display_name": row["display_name"],
                "size_bytes": src["size"],
                "sha256": src["sha256"],
                "expires_at": row["expires_at"],
                "remaining_downloads": view["remaining_downloads"],
                "password_required": False,
                "delivery_semantics": (
                    "服务端完整传输结束计一次；客户端接收校验单独记录，不能证明磁盘保存。"
                ),
            }

    def history(self, p, sid, admin=False):
        with self.db.transaction() as conn:
            self.owned(conn, p.user["id"], sid, admin)
            return {
                "sessions": [
                    dict(r)
                    for r in conn.execute(
                        "SELECT "
                        "session_id,status,transferred_bytes,expected_bytes,reserved_quota,created_at,completed_at,client_received_at,error"
                        " FROM share_download_sessions WHERE share_id=? ORDER BY created_at "
                        "DESC LIMIT 200",
                        (sid,),
                    )
                ],
                "events": [
                    dict(r)
                    for r in conn.execute(
                        "SELECT * FROM share_events WHERE share_id=? "
                        "ORDER BY event_id DESC LIMIT 200",
                        (sid,),
                    )
                ],
            }
