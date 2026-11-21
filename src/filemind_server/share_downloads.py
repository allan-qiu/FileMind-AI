"""Atomic quota reservations; sequential, single-connection resumable ASGI delivery."""

import asyncio
import time
import uuid
from urllib.parse import quote

from fastapi import HTTPException
from starlette.responses import Response

from filemind_server.downloads import signature
from filemind_server.security import token_hash
from filemind_server.share_config import config
from filemind_server.shares import event


class ShareDownloadService:
    def __init__(self, manager, temporary):
        self.manager, self.temporary, self.db = manager, temporary, manager.db

    def reserve(self, grant, key, secret):
        cfg, now = config(self.db), time.time()
        with self.db.transaction(True) as conn:
            access = conn.execute(
                "SELECT * FROM share_access_grants WHERE grant_hash=? AND expires_at>?",
                (token_hash(grant), now),
            ).fetchone()
            if not access:
                raise HTTPException(404, "访问凭据无效或过期")
            row = self.manager.owned(conn, "", access["share_id"], True)
            self.manager.active(row)
            if not (cfg.allow_library if row["source_type"] == "library" else cfg.allow_temporary):
                raise HTTPException(403, "此分享来源已被系统关闭")
            src = self.manager.source(conn, row)
            conn.execute(
                "UPDATE share_download_sessions SET "
                "status='EXPIRED',reserved_quota=0,failed_at=? WHERE "
                "reserved_quota=1 AND reservation_expires_at<=?",
                (now, now),
            )
            prior = conn.execute(
                "SELECT * FROM share_download_sessions WHERE share_id=? AND request_key=?",
                (row["share_id"], key),
            ).fetchone()
            if prior:
                if prior["session_token_hash"] != token_hash(secret):
                    raise HTTPException(409, "重复请求必须使用原会话凭据")
                return self.public(prior)
            quota = conn.execute(
                "SELECT count(*) FROM share_download_sessions WHERE share_id=? AND "
                "reserved_quota=1",
                (row["share_id"],),
            ).fetchone()[0]
            if row["completed_downloads"] + quota >= row["max_downloads"]:
                raise HTTPException(409, "下载次数已耗尽或名额正在使用")
            if quota >= cfg.share_concurrency:
                raise HTTPException(429, "此链接并发名额已满")
            total, owner = conn.execute(
                "SELECT count(*),coalesce(sum(s.owner_user_id=?),0) FROM "
                "share_download_sessions d JOIN share_links s ON "
                "s.share_id=d.share_id WHERE d.reserved_quota=1",
                (row["owner_user_id"],),
            ).fetchone()
            if total >= cfg.max_concurrency or owner >= cfg.user_concurrency:
                raise HTTPException(429, "系统或用户并发下载已满")
            sid = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO "
                "share_download_sessions(session_id,share_id,session_token_hash,reserved_quota,expected_bytes,reservation_expires_at,status,created_at,request_key)"
                " VALUES (?,?,?,1,?,?,'RESERVED',?,?)",
                (
                    sid,
                    row["share_id"],
                    token_hash(secret),
                    src["size"],
                    min(now + cfg.session_ttl, row["expires_at"]),
                    now,
                    key,
                ),
            )
            event(conn, row["share_id"], "QUOTA_RESERVED", session=sid)
            return self.public(
                conn.execute(
                    "SELECT * FROM share_download_sessions WHERE session_id=?", (sid,)
                ).fetchone()
            )

    @staticmethod
    def public(row):
        return {
            k: row[k]
            for k in (
                "session_id",
                "share_id",
                "status",
                "transferred_bytes",
                "expected_bytes",
                "reservation_expires_at",
                "client_received_at",
            )
        }

    def checked(self, conn, sid, secret, allow_completed=False):
        row = conn.execute(
            "SELECT * FROM share_download_sessions WHERE session_id=? AND session_token_hash=?",
            (sid, token_hash(secret)),
        ).fetchone()
        if not row:
            raise HTTPException(404, "下载会话无效")
        row = dict(row)
        if allow_completed and row["status"] == "COMPLETED":
            return row, None
        if (
            row["status"] not in {"RESERVED", "TRANSFERRING"}
            or row["reservation_expires_at"] <= time.time()
        ):
            raise HTTPException(410, "下载会话已结束或过期")
        link = self.manager.owned(conn, "", row["share_id"], True)
        self.manager.active(link)
        self.manager.source(conn, link)
        return row, link

    def state(self, sid, secret):
        with self.db.transaction() as conn:
            row, _ = self.checked(conn, sid, secret, True)
            return self.public(row)

    def cancel(self, sid, secret):
        with self.db.transaction(True) as conn:
            row, _ = self.checked(conn, sid, secret, True)
            if row["status"] != "COMPLETED":
                conn.execute(
                    "UPDATE share_download_sessions SET "
                    "status='CANCELLED',reserved_quota=0,failed_at=? WHERE session_id=?",
                    (time.time(), sid),
                )
                event(conn, row["share_id"], "CANCELLED", session=sid)
            return {"status": "COMPLETED" if row["status"] == "COMPLETED" else "CANCELLED"}

    def confirm(self, sid, secret, sha256):
        # A client can annotate delivery only; it cannot control whether quota is charged.
        with self.db.transaction(True) as conn:
            row, _ = self.checked(conn, sid, secret, True)
            if row["status"] != "COMPLETED":
                raise HTTPException(409, "服务端尚未完成交付")
            link = self.manager.owned(conn, "", row["share_id"], True)
            if link["source_type"] == "temporary":
                expected = conn.execute(
                    "SELECT content_sha256 FROM temporary_share_objects WHERE object_id=?",
                    (link["temporary_object_id"],),
                ).fetchone()[0]
            else:
                expected = conn.execute(
                    "SELECT sha256 FROM file_versions WHERE id=?", (link["file_version_id"],)
                ).fetchone()[0]
            if sha256 != expected:
                raise HTTPException(409, "客户端哈希不匹配")
            conn.execute(
                "UPDATE share_download_sessions SET "
                "client_received_at=coalesce(client_received_at,?) WHERE "
                "session_id=?",
                (time.time(), sid),
            )
            return {"status": "COMPLETED", "client_received": True, "disk_save_proven": False}

    def prepare(self, sid, secret, range_header):
        cfg = config(self.db)
        with self.db.transaction(True) as conn:
            row, link = self.checked(conn, sid, secret)
            if row["status"] == "TRANSFERRING":
                raise HTTPException(409, "此会话已有传输连接")
            if not (cfg.allow_library if link["source_type"] == "library" else cfg.allow_temporary):
                raise HTTPException(403, "此来源分享已关闭")
            start, end, size = (
                row["transferred_bytes"],
                row["expected_bytes"] - 1,
                row["expected_bytes"],
            )
            if range_header:
                import re

                match = re.fullmatch(r"bytes=(\d+)-(\d*)", range_header)
                if not match or int(match[1]) != start or size == 0:
                    raise HTTPException(
                        416,
                        "仅支持从服务器已交付偏移顺序续传",
                        headers={"Content-Range": f"bytes */{size}"},
                    )
                end = min(int(match[2]), end) if match[2] else end
                if end < start:
                    raise HTTPException(416, "Range 无效")
            elif start:
                raise HTTPException(416, "续传需要 Range 及正确偏移")
            src = self.manager.source(conn, link)
            conn.execute(
                "UPDATE share_download_sessions SET "
                "status='TRANSFERRING',started_at=coalesce(started_at,?) WHERE "
                "session_id=?",
                (time.time(), sid),
            )
        stream = None
        try:
            storage = (
                self.temporary.storage
                if src["provider"] == "temporary"
                else self.manager.archives.provider(src["provider"])
            )
            stream = self.manager.archives.reads.open(
                storage,
                src["key"],
                size,
                src["sha256"],
                namespace="temporary:" if src["provider"] == "temporary" else "",
            )
            stream.seek(start)
        except (OSError, ValueError, NotImplementedError):
            if stream:
                stream.close()
            self.failed(sid, "CONTENT_UNAVAILABLE", terminal=True)
            raise HTTPException(409, "文件缺失、读取失败或完整性不匹配") from None
        headers = {
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote(link["display_name"]),
            "Content-Length": str(max(0, end - start + 1)),
            "Accept-Ranges": "bytes",
            "X-Content-SHA256": src["sha256"],
            "Cache-Control": "no-store",
        }
        if range_header:
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        return ShareContentResponse(
            self, sid, secret, stream, start, end, size, cfg, headers, bool(range_header)
        )

    def account_wire(self, sid, secret, size):
        cfg, now = config(self.db), time.time()
        with self.db.transaction(True) as conn:
            row, link = self.checked(conn, sid, secret)
            if row["status"] != "TRANSFERRING":
                raise HTTPException(410, "下载已终止")
            day = int(now // 86400)
            usage = conn.execute(
                "SELECT wire_attempt_bytes FROM share_daily_traffic WHERE "
                "owner_user_id=? AND utc_day=?",
                (link["owner_user_id"], day),
            ).fetchone()
            used = usage[0] if usage else 0
            if used + size > cfg.user_daily_bytes:
                raise HTTPException(429, "用户今日传输流量上限")
            session_wire = conn.execute(
                "SELECT coalesce(sum(bytes_sent),0) FROM share_events WHERE "
                "share_id=? AND session_id=?",
                (row["share_id"], sid),
            ).fetchone()[0]
            if session_wire + size > row["expected_bytes"] + 2 * 262144:
                raise HTTPException(429, "会话重试流量已达上限，请取消或等待过期")
            conn.execute(
                "INSERT INTO share_daily_traffic VALUES (?,?,?) ON "
                "CONFLICT(owner_user_id,utc_day) DO UPDATE SET "
                "wire_attempt_bytes=wire_attempt_bytes+excluded.wire_attempt_bytes",
                (link["owner_user_id"], day, size),
            )
            # Conservative traffic accounting before send also bounds failed/aborted traffic.
            event(conn, row["share_id"], "WIRE_ATTEMPT", session=sid, size=size)

    def checkpoint(self, sid, secret, position, finish=False):
        with self.db.transaction(True) as conn:
            row, _ = self.checked(conn, sid, secret)
            if (
                row["status"] != "TRANSFERRING"
                or position < row["transferred_bytes"]
                or position > row["expected_bytes"]
            ):
                raise RuntimeError("INVALID_TRANSFER_CHECKPOINT")
            completed = finish and position == row["expected_bytes"]
            conn.execute(
                "UPDATE share_download_sessions SET "
                "transferred_bytes=?,status=?,reserved_quota=?,completed_at=? WHERE "
                "session_id=?",
                (
                    position,
                    "COMPLETED" if completed else "RESERVED" if finish else "TRANSFERRING",
                    0 if completed else 1,
                    time.time() if completed else None,
                    sid,
                ),
            )
            if completed:
                conn.execute(
                    "UPDATE share_links SET "
                    "completed_downloads=completed_downloads+1,updated_at=? WHERE "
                    "share_id=?",
                    (time.time(), row["share_id"]),
                )
                event(conn, row["share_id"], "COMPLETED", session=sid)

    def failed(self, sid, error, terminal=False):
        with self.db.transaction(True) as conn:
            row = conn.execute(
                "SELECT * FROM share_download_sessions WHERE session_id=?", (sid,)
            ).fetchone()
            if row and row["status"] == "TRANSFERRING":
                release = terminal or row["transferred_bytes"] == 0
                conn.execute(
                    "UPDATE share_download_sessions SET "
                    "status=?,reserved_quota=?,failed_at=?,error=? WHERE session_id=?",
                    (
                        "FAILED" if release else "RESERVED",
                        0 if release else 1,
                        time.time(),
                        error,
                        sid,
                    ),
                )
                event(
                    conn,
                    row["share_id"],
                    "FAILED" if release else "TRANSFER_INTERRUPTED",
                    session=sid,
                )

    def recover(self):
        # Exclusive service lock is held by app startup. Prefix checkpoints survive restart.
        with self.db.transaction(True) as conn:
            conn.execute(
                "UPDATE share_download_sessions SET "
                "status='RESERVED',error='SERVER_RESTART_PREFIX_RECOVERED' WHERE "
                "status='TRANSFERRING'"
            )


class ShareContentResponse(Response):
    def __init__(self, service, sid, secret, stream, start, end, size, cfg, headers, partial):
        super().__init__(
            content=None,
            status_code=206 if partial else 200,
            headers=headers,
            media_type="application/octet-stream",
        )
        self.service, self.sid, self.secret, self.stream = service, sid, secret, stream
        self.start, self.end, self.size, self.cfg = start, end, size, cfg
        self.signature = signature(stream)
        self.finalizing = False

    async def __call__(self, scope, receive, send):
        async def disconnect():
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return

        delivery = asyncio.create_task(self.deliver(send))
        listener = asyncio.create_task(disconnect())
        try:
            done, _ = await asyncio.wait({delivery, listener}, return_when=asyncio.FIRST_COMPLETED)
            if listener in done and not delivery.done() and not self.finalizing:
                delivery.cancel()
            try:
                await delivery
            except asyncio.CancelledError:
                if listener not in done:
                    raise
        finally:
            listener.cancel()
            if not delivery.done():
                delivery.cancel()
            await asyncio.gather(listener, delivery, return_exceptions=True)

    async def deliver(self, send):
        position = self.start
        started = time.monotonic()
        try:
            await asyncio.wait_for(
                send(
                    {
                        "type": "http.response.start",
                        "status": self.status_code,
                        "headers": self.raw_headers,
                    }
                ),
                30,
            )
            while position <= self.end:
                delay = (position - self.start) / self.cfg.bytes_per_second - (
                    time.monotonic() - started
                )
                if delay > 0:
                    await asyncio.sleep(min(delay, 1))
                block = await asyncio.to_thread(
                    self.stream.read, min(262144, self.end - position + 1)
                )
                if not block:
                    raise OSError("SHORT_READ")
                await asyncio.to_thread(
                    self.service.account_wire, self.sid, self.secret, len(block)
                )
                last_frame = position + len(block) > self.end
                if last_frame:
                    if signature(self.stream) != self.signature:
                        raise OSError("OBJECT_CHANGED_DURING_DELIVERY")
                    self.finalizing = True
                await asyncio.wait_for(
                    send(
                        {"type": "http.response.body", "body": block, "more_body": not last_frame}
                    ),
                    30,
                )
                position += len(block)
                # Last checkpoint is committed only after the final ASGI body send returns.
                if not last_frame:
                    await asyncio.to_thread(
                        self.service.checkpoint, self.sid, self.secret, position
                    )
            if signature(self.stream) != self.signature:
                raise OSError("OBJECT_CHANGED_DURING_DELIVERY")
            if self.size == 0:
                self.finalizing = True
                await asyncio.wait_for(
                    send({"type": "http.response.body", "body": b"", "more_body": False}), 30
                )
            await asyncio.to_thread(self.service.checkpoint, self.sid, self.secret, position, True)
        except BaseException:
            await asyncio.shield(
                asyncio.to_thread(self.service.failed, self.sid, "STREAM_INTERRUPTED")
            )
            raise
        finally:
            await asyncio.to_thread(self.stream.close)
        if self.background:
            await self.background()
