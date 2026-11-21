"Transparent, bounded HTTP relay. No file content analysis or server-side file cache."

import asyncio
import hashlib
import time
import uuid

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

from filemind.services.classification import valid_name
from filemind_server.accounts import audit

TERMINAL = {"COMPLETED", "REJECTED", "CANCELLED", "FAILED", "EXPIRED"}
ACTIVE = {"ACCEPTED", "CONNECTING", "TRANSFERRING", "VERIFYING"}
STEPS = {
    "WAITING_RECEIVER": {"ACCEPTED", "REJECTED", "CANCELLED", "FAILED", "EXPIRED"},
    "ACCEPTED": {"CONNECTING", "TRANSFERRING", "CANCELLED", "FAILED", "EXPIRED"},
    "CONNECTING": {"TRANSFERRING", "CANCELLED", "FAILED", "EXPIRED"},
    "TRANSFERRING": {"VERIFYING", "CANCELLED", "FAILED", "EXPIRED"},
    "VERIFYING": {"COMPLETED", "CANCELLED", "FAILED", "EXPIRED"},
}


class Relay:
    def __init__(self):
        self.condition = asyncio.Condition()
        self.delivered = asyncio.Event()
        self.sender_lock = asyncio.Lock()
        self.receiver_busy = False
        self.slot = None
        self.inflight = 0
        self.sha256 = hashlib.sha256()


class TransferService:
    def __init__(self, database, settings):
        self.db, self.settings = database, settings
        self.relays = {}
        self.peak_buffer_bytes = 0
        self.rate_lock = asyncio.Lock()
        self.next_send_time = 0.0

    async def pace(self, size):
        async with self.rate_lock:
            now = time.monotonic()
            due = max(now, self.next_send_time)
            self.next_send_time = due + size / self.settings.transfer_bytes_per_second
        await asyncio.sleep(max(0, due - time.monotonic()))

    def row(self, transfer_id):
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM transfer_sessions WHERE id=?", (transfer_id,)
            ).fetchone()
        if not row:
            raise HTTPException(404, "传输会话不存在。")
        return dict(row)

    def participant(self, transfer_id, principal, side=None):
        row = self.row(transfer_id)
        device_id = principal.session["device_id"]
        if row["user_id"] != principal.user["id"] or device_id not in (
            row["sender_id"],
            row["receiver_id"],
        ):
            raise HTTPException(404, "传输不存在或设备无权限。")
        if side and row[side + "_id"] != device_id:
            raise HTTPException(403, "设备不具备此传输角色。")
        return row

    def transition(self, transfer_id, state, message=""):
        with self.db.transaction(True) as conn:
            row = conn.execute(
                "SELECT * FROM transfer_sessions WHERE id=?", (transfer_id,)
            ).fetchone()
            if row["state"] == state:
                return
            if state not in STEPS.get(row["state"], set()):
                raise HTTPException(409, f"不允许从 {row['state']} 转为 {state}。")
            conn.execute(
                (
                    "UPDATE transfer_sessions SET "
                    "state=?,error=?,updated_at=?,completed_at=? WHERE id=?"
                ),
                (
                    state,
                    message,
                    time.time(),
                    time.time() if state == "COMPLETED" else None,
                    transfer_id,
                ),
            )
            conn.execute(
                "INSERT INTO transfer_events(transfer_id,state,message,created_at) "
                "VALUES (?,?,?,?)",
                (transfer_id, state, message, time.time()),
            )

    async def wake(self, transfer_id):
        relay = self.relays.get(transfer_id)
        if relay:
            relay.slot = None
            relay.delivered.set()
            async with relay.condition:
                relay.condition.notify_all()

    async def terminate(self, transfer_id, state, message=""):
        row = self.row(transfer_id)
        if row["state"] not in TERMINAL:
            self.transition(transfer_id, state, message)
        await self.wake(transfer_id)
        return self.row(transfer_id)

    def create(self, data, principal):
        if not self.settings.relay_enabled:
            raise HTTPException(503, "文件中继已关闭；本地整理与文件库仍可使用。")
        valid_name(data.filename)
        if data.size_bytes > self.settings.transfer_max_bytes:
            raise HTTPException(413, "文件超过本服务配置的大小限制。")
        sender, now = principal.session["device_id"], time.time()
        with self.db.transaction(True) as conn:
            previous = conn.execute(
                "SELECT * FROM transfer_sessions WHERE sender_id=? AND request_key=?",
                (sender, data.request_key),
            ).fetchone()
            if previous:
                if (
                    previous["receiver_id"],
                    previous["filename"],
                    previous["size_bytes"],
                    previous["expected_hash"],
                ) != (data.receiver_id, data.filename, data.size_bytes, data.sha256):
                    raise HTTPException(409, "幂等键已被不同传输使用。")
                return dict(previous)
            receiver = conn.execute(
                "SELECT d.id,d.platform FROM devices d JOIN device_receivers r ON r.device_id=d.id "
                "WHERE d.id=? AND d.user_id=? AND d.revoked_at IS NULL AND r.ready_at>?",
                (data.receiver_id, principal.user["id"], now - 10),
            ).fetchone()
            if not receiver or data.receiver_id == sender:
                raise HTTPException(409, "接收设备不可用、离线或未经授权。")
            if receiver["platform"] != "windows" and data.size_bytes > 32 * 1024 * 1024:
                raise HTTPException(413, "浏览器/移动端接收仍采用受限 Blob，最大32 MiB。")
            count = conn.execute(
                "SELECT count(*) FROM transfer_sessions WHERE state NOT IN "
                "('COMPLETED','REJECTED','CANCELLED','FAILED','EXPIRED')"
            ).fetchone()[0]
            if count >= self.settings.transfer_pending_limit:
                raise HTTPException(429, "等待中传输已达限制，请稍后再试。")
            transfer_id = str(uuid.uuid4())
            conn.execute(
                (
                    "INSERT INTO "
                    "transfer_sessions(id,user_id,sender_id,receiver_id,filename,size_bytes,"
                    "expected_hash,request_key,state,created_at,updated_at,expires_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
                ),
                (
                    transfer_id,
                    principal.user["id"],
                    sender,
                    data.receiver_id,
                    data.filename,
                    data.size_bytes,
                    data.sha256,
                    data.request_key,
                    "CREATED",
                    now,
                    now,
                    now + self.settings.transfer_ttl,
                ),
            )
            for state in ("CREATED", "WAITING_RECEIVER"):
                conn.execute(
                    "INSERT INTO transfer_events(transfer_id,state,message,created_at) "
                    "VALUES (?,?,?,?)",
                    (transfer_id, state, "", now),
                )
            conn.execute(
                "UPDATE transfer_sessions SET state='WAITING_RECEIVER' WHERE id=?", (transfer_id,)
            )
            audit(
                conn,
                principal.user["id"],
                "transfer.created",
                transfer_id,
                {"size": data.size_bytes},
            )
        return self.row(transfer_id)

    def accept(self, transfer_id):
        if len(self.relays) >= self.settings.transfer_concurrency:
            raise HTTPException(429, "中继仍在释放先前连接，请稍后接受。")
        with self.db.transaction(True) as conn:
            row = conn.execute(
                "SELECT state,expires_at FROM transfer_sessions WHERE id=?", (transfer_id,)
            ).fetchone()
            if row["state"] != "WAITING_RECEIVER" or row["expires_at"] <= time.time():
                raise HTTPException(409, "传输已被处理或已过期。")
            count = conn.execute(
                "SELECT count(*) FROM transfer_sessions WHERE state IN "
                "('ACCEPTED','CONNECTING','TRANSFERRING','VERIFYING')"
            ).fetchone()[0]
            if count >= self.settings.transfer_concurrency:
                raise HTTPException(429, "正在传输的会话达到并发限制。")
            conn.execute(
                "UPDATE transfer_sessions SET state='ACCEPTED',updated_at=? WHERE id=?",
                (time.time(), transfer_id),
            )
            conn.execute(
                (
                    "INSERT INTO transfer_events(transfer_id,state,message,created_at) "
                    "VALUES (?,'ACCEPTED','',?)"
                ),
                (transfer_id, time.time()),
            )
        self.relays[transfer_id] = Relay()
        return self.row(transfer_id)

    def live(self, transfer_id):
        row = self.row(transfer_id)
        if row["state"] in TERMINAL:
            raise HTTPException(409, f"传输已结束：{row['state']} {row['error']}")
        if row["expires_at"] <= time.time():
            raise HTTPException(409, "传输已过期。")
        if transfer_id not in self.relays:
            raise HTTPException(409, "接收方尚未同意，或会话需重新发送。")
        return row, self.relays[transfer_id]

    async def wait(self, request, predicate, transfer_id):
        deadline = time.monotonic() + self.settings.transfer_timeout
        while not predicate():
            self.live(transfer_id)
            if time.monotonic() >= deadline:
                await self.terminate(
                    transfer_id, "FAILED", "等待文件块超时；不支持断点续传，请重新发送。"
                )
                raise HTTPException(408, "传输等待超时。")
            if await request.is_disconnected():
                await self.terminate(transfer_id, "FAILED", "客户端连接断开。")
                raise HTTPException(409, "客户端已断线。")
            # Wake as soon as a slot/delivery changes. Periodic bounded checks still
            # detect disconnects and deadlines even when the peer never responds.
            relay = self.relays[transfer_id]
            async with relay.condition:
                if not predicate():
                    try:
                        await asyncio.wait_for(relay.condition.wait(), timeout=0.1)
                    except TimeoutError:
                        pass
        if self.row(transfer_id)["state"] != "COMPLETED":
            self.live(transfer_id)

    def expected_chunk(self, row, seq):
        count = max(
            1,
            (row["size_bytes"] + self.settings.transfer_chunk_bytes - 1)
            // self.settings.transfer_chunk_bytes,
        )
        if seq < 0 or seq >= count or seq != row["next_seq"]:
            raise HTTPException(409, "块序号无效；不支持重复块或断点续传。")
        return min(
            self.settings.transfer_chunk_bytes,
            max(0, row["size_bytes"] - seq * self.settings.transfer_chunk_bytes),
        )

    async def upload(self, transfer_id, seq, request: Request):
        row, relay = self.live(transfer_id)
        expected = self.expected_chunk(row, seq)
        if relay.sender_lock.locked():
            raise HTTPException(409, "一个会话只允许一个发送请求。")
        async with relay.sender_lock:
            body = bytearray()
            try:
                async with asyncio.timeout(self.settings.transfer_timeout):
                    async for block in request.stream():
                        if len(body) + len(block) > expected:
                            raise HTTPException(413, "文件块超过声明长度。")
                        body.extend(block)
                if len(body) != expected:
                    raise HTTPException(422, "文件块长度不正确。")
                row, _ = self.live(transfer_id)
                if row["state"] in {"ACCEPTED", "CONNECTING"}:
                    self.transition(transfer_id, "TRANSFERRING")
                with self.db.transaction(True) as conn:
                    conn.execute(
                        (
                            "UPDATE transfer_sessions SET "
                            "bytes_received=bytes_received+?,updated_at=? WHERE id=?"
                        ),
                        (len(body), time.time(), transfer_id),
                    )
                relay.delivered.clear()
                relay.sha256.update(body)
                relay.slot = (seq, bytes(body))
                async with relay.condition:
                    relay.condition.notify_all()
                self.peak_buffer_bytes = max(self.peak_buffer_bytes, self.buffered_bytes())
                await self.wait(request, relay.delivered.is_set, transfer_id)
                return {
                    "forwarded_bytes": self.row(transfer_id)["bytes_forwarded"],
                    "next_seq": seq + 1,
                }
            except TimeoutError:
                await self.terminate(transfer_id, "FAILED", "文件块请求体读取超时。")
                raise HTTPException(408, "文件块请求体读取超时。") from None
            except BaseException as exc:
                if self.row(transfer_id)["state"] not in TERMINAL:
                    await self.terminate(
                        transfer_id, "FAILED", "发送失败或连接中断：" + type(exc).__name__
                    )
                raise

    async def download(self, transfer_id, seq, request):
        row, relay = self.live(transfer_id)
        self.expected_chunk(row, seq)
        if relay.receiver_busy:
            raise HTTPException(409, "一个会话只允许一个接收请求。")
        relay.receiver_busy = True
        if row["state"] == "ACCEPTED":
            self.transition(transfer_id, "CONNECTING")
        try:
            await self.wait(request, lambda: relay.slot is not None, transfer_id)
            actual_seq, body = relay.slot
            if actual_seq != seq:
                raise HTTPException(409, "接收块序号不匹配。")
            relay.slot = None
            relay.inflight = len(body)
        except BaseException:
            relay.receiver_busy = False
            raise

        async def stream():
            delivered = False
            try:
                await self.pace(len(body))
                yield body
                current, _ = self.live(transfer_id)
                with self.db.transaction(True) as conn:
                    conn.execute(
                        (
                            "UPDATE transfer_sessions SET "
                            "bytes_forwarded=bytes_forwarded+?,next_seq=next_seq+1,updated_at=? "
                            "WHERE id=?"
                        ),
                        (len(body), time.time(), transfer_id),
                    )
                if current["bytes_forwarded"] + len(body) == current["size_bytes"]:
                    self.transition(transfer_id, "VERIFYING")
                delivered = True
                relay.delivered.set()
                async with relay.condition:
                    relay.condition.notify_all()
            finally:
                relay.inflight = 0
                relay.receiver_busy = False
                if not delivered and self.row(transfer_id)["state"] not in TERMINAL:
                    await self.terminate(transfer_id, "FAILED", "接收连接断开或转发失败。")

        return StreamingResponse(
            stream(),
            media_type="application/octet-stream",
            headers={"Content-Length": str(len(body))},
        )

    async def acknowledge(self, transfer_id, data):
        row = self.row(transfer_id)
        if row["state"] == "COMPLETED":
            if data.sha256 != row["expected_hash"]:
                raise HTTPException(409, "重复确认的哈希不匹配。")
            return row
        if row["state"] != "VERIFYING" or row["bytes_forwarded"] != row["size_bytes"]:
            raise HTTPException(409, "尚未完整转发，不允许完成确认。")
        if data.sha256 != row["expected_hash"]:
            await self.terminate(transfer_id, "FAILED", "接收端 SHA-256 不匹配。")
            raise HTTPException(422, "文件完整性校验失败。")
        relay = self.relays.get(transfer_id)
        if not relay or relay.sha256.hexdigest() != row["expected_hash"]:
            await self.terminate(transfer_id, "FAILED", "中继 SHA-256 不匹配。")
            raise HTTPException(422, "中继字节完整性校验失败。")
        with self.db.transaction(True) as conn:
            current = conn.execute(
                "SELECT state FROM transfer_sessions WHERE id=?", (transfer_id,)
            ).fetchone()[0]
            if current != "VERIFYING":
                raise HTTPException(409, "传输状态已变化。")
            conn.execute(
                (
                    "UPDATE transfer_sessions SET "
                    "state='COMPLETED',confirmation=?,completed_at=?,updated_at=? WHERE "
                    "id=?"
                ),
                (data.confirmation, time.time(), time.time(), transfer_id),
            )
            conn.execute(
                (
                    "INSERT INTO transfer_events(transfer_id,state,message,created_at) "
                    "VALUES (?,'COMPLETED',?,?)"
                ),
                (transfer_id, data.confirmation, time.time()),
            )
            conn.execute(
                "INSERT OR IGNORE INTO transfer_archive_handoff VALUES (?,?,?,'PENDING',?)",
                (transfer_id, row["receiver_id"], row["sender_id"], time.time()),
            )
        await self.wake(transfer_id)
        self.relays.pop(transfer_id, None)
        return self.row(transfer_id)

    def buffered_bytes(self):
        return sum((len(r.slot[1]) if r.slot else 0) + r.inflight for r in self.relays.values())

    async def sweep(self):
        with self.db.transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM transfer_sessions WHERE state NOT IN "
                "('COMPLETED','REJECTED','CANCELLED','FAILED','EXPIRED')"
            ).fetchall()
        for row in rows:
            if row["expires_at"] <= time.time():
                await self.terminate(row["id"], "EXPIRED", "会话期限已到。")
            else:
                with self.db.transaction() as conn:
                    valid = conn.execute(
                        "SELECT count(DISTINCT d.id) FROM devices d JOIN users u ON u.id=d.user_id "
                        "JOIN auth_sessions s ON s.device_id=d.id WHERE d.id IN (?,?) "
                        "AND d.revoked_at IS NULL AND u.enabled=1 AND s.expires_at>?",
                        (row["sender_id"], row["receiver_id"], int(time.time())),
                    ).fetchone()[0]
                if valid != 2:
                    await self.terminate(row["id"], "FAILED", "参与设备已撤销、停用或授权过期。")
        for transfer_id in list(self.relays):
            if self.row(transfer_id)["state"] in TERMINAL:
                await self.wake(transfer_id)
                relay = self.relays[transfer_id]
                if not relay.receiver_busy and not relay.sender_lock.locked():
                    self.relays.pop(transfer_id, None)

    def recover(self):
        with self.db.transaction(True) as conn:
            rows = conn.execute(
                "SELECT id FROM transfer_sessions WHERE state NOT IN "
                "('COMPLETED','REJECTED','CANCELLED','FAILED','EXPIRED')"
            ).fetchall()
            for row in rows:
                conn.execute(
                    (
                        "UPDATE transfer_sessions SET "
                        "state='FAILED',error='服务重启；无断点续传，请重新发送',"
                        "updated_at=? WHERE id=?"
                    ),
                    (time.time(), row["id"]),
                )
                conn.execute(
                    (
                        "INSERT INTO transfer_events(transfer_id,state,message,created_at) "
                        "VALUES (?,'FAILED','服务重启',?)"
                    ),
                    (row["id"], time.time()),
                )
