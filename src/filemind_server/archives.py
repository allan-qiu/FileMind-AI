"""Durable archive orchestration, fenced leases, resumable staging, AI-before-storage."""

import hashlib
import json
import os
import secrets
import time
import uuid
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from filemind.infrastructure.file_analysis import reject_links
from filemind.services.classification import valid_name
from filemind_server.ai import AIError, validate_plan
from filemind_server.ai_control import load_config
from filemind_server.extraction import extract_excerpt
from filemind_server.key_store import server_secret
from filemind_server.storage import LocalStorageProvider, file_digest, io_path

FINAL = {"COMPLETED", "FAILED", "CANCELLED", "EXPIRED"}
PROCESSABLE = {
    "STAGED",
    "AWAITING_AI_CONSENT",
    "READY_TO_ARCHIVE",
    "ARCHIVING",
    "VERIFYING_STORAGE",
    "COMMITTING_METADATA",
    "RETRY_WAIT",
    "AI_PLANNING",
}


class ArchiveSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    concurrency: int = Field(default=1, ge=1, le=2)
    upload_concurrency: int = Field(default=2, ge=1, le=4)
    cache_bytes: int = Field(default=512 * 1024 * 1024, ge=1, le=4 * 1024**3)
    max_file_bytes: int = Field(default=256 * 1024 * 1024, ge=1, le=1024**3)
    ttl_seconds: int = Field(default=86400, ge=60, le=604800)
    lease_seconds: int = Field(default=60, ge=5, le=600)
    backup_grace_seconds: int = Field(default=30, ge=0, le=600)
    max_retries: int = Field(default=3, ge=0, le=10)
    provider: Literal["local", "baidu"] = "local"


class ArchivePreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    auto_archive: bool = False
    ai_mode: Literal["off", "metadata", "summary", "content"] = "off"
    rules: str = Field(default="", max_length=2000)


class ArchiveService:
    def __init__(self, db, settings, ai):
        self.db, self.settings, self.ai = db, settings, ai
        self.spool = io_path(settings.data_dir / "staging")
        self.storage = None
        self.before_metadata_commit = None  # fault-injection seam; never an API setting

    def initialize(self):
        reject_links(self.spool.absolute())
        self.spool.mkdir(parents=True, exist_ok=True)
        self.storage = LocalStorageProvider(self.settings.data_dir / "storage")
        from filemind_server.storage_reads import VerifiedStorageReads

        self.providers = {"local": self.storage}
        self.reads = VerifiedStorageReads(
            self.db, self.settings.data_dir / "read-cache", self.settings.verified_read_cache_bytes
        )
        from filemind_server.baidu import BaiduCredentials, BaiduNetdiskProvider

        self.providers["baidu"] = BaiduNetdiskProvider(
            BaiduCredentials(self.settings.data_dir),
            app_root=self.settings.baidu_app_root,
            client_id=server_secret("FILEMIND_BAIDU_CLIENT_ID"),
            client_secret=server_secret("FILEMIND_BAIDU_CLIENT_SECRET"),
            formal_authorized=self.settings.baidu_formal_authorized,
            remote_enabled=self.settings.baidu_remote_enabled,
            writes_enabled=self.settings.baidu_writes_enabled,
        )

    def provider(self, name):
        # Persisted provider identity, never the currently selected write provider.
        if name == "local":
            return self.storage
        try:
            return self.providers[name]
        except KeyError:
            raise ValueError("STORAGE_PROVIDER_UNAVAILABLE") from None

    def job_storage(self, row):
        with self.db.transaction(True) as conn:
            binding = conn.execute(
                "SELECT provider FROM archive_storage_bindings WHERE job_id=?", (row["id"],)
            ).fetchone()
            if not binding:
                name = "local" if row["storage_key"] else self.config().provider
                conn.execute("INSERT INTO archive_storage_bindings VALUES (?,?)", (row["id"], name))
            else:
                name = binding[0]
        return self.provider(name)

    def recover(self):
        # ServiceLock guarantees previous process is gone; preserve source-upload leases.
        with self.db.transaction(True) as conn:
            conn.execute("UPDATE archive_jobs SET worker_token=NULL,worker_expires_at=NULL")

    def config(self):
        return load_config(self.db, "archive_v2_2", ArchiveSettings)

    def preferences(self, user_id):
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM archive_preferences WHERE user_id=?", (user_id,)
            ).fetchone()
        return (
            ArchivePreferences(
                auto_archive=bool(row["auto_archive"]), ai_mode=row["ai_mode"], rules=row["rules"]
            )
            if row
            else ArchivePreferences()
        )

    def set_preferences(self, user_id, prefs):
        with self.db.transaction(True) as conn:
            conn.execute(
                "INSERT INTO archive_preferences VALUES (?,?,?,?,?) ON CONFLICT(user_id) "
                "DO UPDATE SET auto_archive=excluded.auto_archive,ai_mode=excluded.ai_mode,"
                "rules=excluded.rules,updated_at=excluded.updated_at",
                (user_id, int(prefs.auto_archive), prefs.ai_mode, prefs.rules, time.time()),
            )

    def row(self, job_id):
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM archive_jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise HTTPException(404, "归档任务不存在")
        return dict(row)

    def owned(self, job_id, principal):
        row = self.row(job_id)
        if row["user_id"] != principal.user["id"]:
            raise HTTPException(404, "归档任务不存在")
        return row

    def public(self, row):
        result = {
            k: v
            for k, v in row.items()
            if k
            not in {
                "lease_token",
                "worker_token",
                "worker_expires_at",
                "plan_json",
                "idempotency_key",
            }
        }
        result["plan"] = json.loads(row["plan_json"]) if row["plan_json"] else None
        with self.db.transaction() as conn:
            ingest = conn.execute(
                "SELECT offset,state FROM ingest_sessions WHERE job_id=?", (row["id"],)
            ).fetchone()
        result["uploaded_bytes"] = ingest["offset"] if ingest else 0
        return result

    def admin_public(self, row):
        """Operational metadata only; content-derived AI fields require owner access."""
        result = self.public(row)
        if result["plan"]:
            result["plan"] = {
                k: v
                for k, v in result["plan"].items()
                if k
                in {
                    "category",
                    "subcategory",
                    "suggested_filename",
                    "target_directory",
                    "needs_user_review",
                }
            }
        # Unexpected exception messages may include text excerpts. Keep the fixed code/type.
        result["error"] = result["error"].partition(":")[0]
        return result

    @staticmethod
    def event(conn, job_id, state, message=""):
        conn.execute(
            "INSERT INTO archive_events(job_id,state,message,created_at) VALUES (?,?,?,?)",
            (job_id, state, message, time.time()),
        )

    def create(self, user_id, filename, size, digest, key, transfer=None, associate=None):
        valid_name(filename)
        config = self.config()
        if not config.enabled:
            raise HTTPException(409, "归档已由管理员暂停")
        if size > config.max_file_bytes:
            raise HTTPException(413, "超过单文件归档上限")
        now = time.time()
        with self.db.transaction(True) as conn:
            previous = conn.execute(
                "SELECT * FROM archive_jobs WHERE user_id=? AND idempotency_key=?", (user_id, key)
            ).fetchone()
            if previous:
                if (previous["filename"], previous["size_bytes"], previous["sha256"]) != (
                    filename,
                    size,
                    digest,
                ):
                    raise HTTPException(409, "归档幂等键内容不匹配")
                if associate:
                    associate(conn, previous["id"])
                return dict(previous)
            # Bound persistent queues independently from transfer pending sessions.
            count = conn.execute(
                "SELECT count(*) FROM archive_jobs WHERE state NOT IN "
                "('COMPLETED','CANCELLED','EXPIRED','FAILED')"
            ).fetchone()[0]
            if count >= 1000:
                raise HTTPException(429, "归档队列已满")
            used = conn.execute(
                "SELECT coalesce(sum(size_bytes),0) FROM archive_jobs WHERE user_id=? "
                "AND state NOT IN ('CANCELLED','EXPIRED','FAILED')",
                (user_id,),
            ).fetchone()[0]
            quota = conn.execute("SELECT quota_bytes FROM users WHERE id=?", (user_id,)).fetchone()[
                0
            ]
            if used + size > quota:
                raise HTTPException(413, "归档任务与已归档文件超过账户配额")
            job_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO "
                "archive_jobs(id,user_id,source_transfer_id,idempotency_key,filename,"
                "size_bytes,sha256,state,primary_owner,backup_owner,created_at,updated_"
                "at,expires_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    user_id,
                    transfer["id"] if transfer else None,
                    key,
                    filename,
                    size,
                    digest,
                    "WAITING_SOURCE" if transfer else "WAITING_UPLOAD",
                    transfer["receiver_id"] if transfer else None,
                    transfer["sender_id"] if transfer else None,
                    now,
                    now,
                    now + config.ttl_seconds,
                ),
            )
            self.event(conn, job_id, "CREATED")
            self.event(conn, job_id, "WAITING_SOURCE" if transfer else "WAITING_UPLOAD")
            if associate:
                associate(conn, job_id)
        return self.row(job_id)

    def ingest_handoffs(self):
        if not self.config().enabled:
            return
        with self.db.transaction() as conn:
            rows = conn.execute(
                "SELECT t.*,h.created_at handoff_created_at FROM transfer_archive_handoff h "
                "JOIN transfer_sessions t "
                "ON t.id=h.transfer_id "
                "WHERE h.state IN ('PENDING','NOT_IMPLEMENTED') AND "
                "t.state='COMPLETED' LIMIT 50"
            ).fetchall()
        for row in rows:
            if not self.preferences(row["user_id"]).auto_archive:
                with self.db.transaction(True) as conn:
                    conn.execute(
                        "UPDATE transfer_archive_handoff SET state='AUTO_ARCHIVE_OFF' "
                        "WHERE transfer_id=?",
                        (row["id"],),
                    )
                continue
            if row["handoff_created_at"] + self.config().ttl_seconds <= time.time():
                with self.db.transaction(True) as conn:
                    conn.execute(
                        "UPDATE transfer_archive_handoff SET state='EXPIRED' WHERE transfer_id=?",
                        (row["id"],),
                    )
                continue
            try:
                self.create(
                    row["user_id"],
                    row["filename"],
                    row["size_bytes"],
                    row["expected_hash"],
                    "transfer:" + row["id"],
                    dict(row),
                )
            except HTTPException as exc:
                if exc.status_code in {413, 429}:
                    # Oversized transfer may remain delivered but cannot enter archive.
                    with self.db.transaction(True) as conn:
                        conn.execute(
                            "UPDATE transfer_archive_handoff SET state=? WHERE transfer_id=?",
                            (
                                "ARCHIVE_LIMIT_EXCEEDED" if exc.status_code == 413 else "PENDING",
                                row["id"],
                            ),
                        )
                continue
            with self.db.transaction(True) as conn:
                conn.execute(
                    "UPDATE transfer_archive_handoff SET state='CREATED' WHERE transfer_id=?",
                    (row["id"],),
                )

    def temp_path(self, job_id):
        if str(uuid.UUID(job_id)) != job_id:
            raise ValueError("任务ID不合法")
        path = self.spool / (job_id + ".part")
        reject_links(path.absolute())
        return path

    def claim(self, job_id, principal):
        row = self.owned(job_id, principal)
        config, now = self.config(), time.time()
        if not config.enabled:
            raise HTTPException(409, "归档暂停")
        owner = principal.session["device_id"] or ("account:" + principal.user["id"])
        if row["source_transfer_id"]:
            if owner not in {row["primary_owner"], row["backup_owner"]}:
                raise HTTPException(403, "只有原接收或发送设备可以提供该归档副本")
            if (
                owner == row["backup_owner"]
                and now < row["created_at"] + config.backup_grace_seconds
            ):
                with self.db.transaction() as conn:
                    primary = conn.execute(
                        "SELECT revoked_at FROM devices WHERE id=?", (row["primary_owner"],)
                    ).fetchone()
                if primary and primary["revoked_at"] is None:
                    raise HTTPException(409, "仍在等待主要归档来源")
        with self.db.transaction(True) as conn:
            row = dict(conn.execute("SELECT * FROM archive_jobs WHERE id=?", (job_id,)).fetchone())
            if (
                row["state"] not in {"WAITING_SOURCE", "WAITING_UPLOAD", "UPLOADING"}
                or row["expires_at"] <= now
            ):
                raise HTTPException(409, "任务不处于可上传状态")
            if row["lease_expires_at"] and row["lease_expires_at"] > now:
                if row["upload_owner"] != owner:
                    raise HTTPException(409, "归档副本已经被另一来源领取")
                token = row["lease_token"]
            else:
                active = conn.execute(
                    "SELECT count(*) FROM archive_jobs WHERE state='UPLOADING' AND "
                    "lease_expires_at>?",
                    (now,),
                ).fetchone()[0]
                if active >= config.upload_concurrency:
                    raise HTTPException(429, "归档上传并发已满")
                token = secrets.token_hex(24)
            ingest = conn.execute(
                "SELECT * FROM ingest_sessions WHERE job_id=?", (job_id,)
            ).fetchone()
            if not ingest:
                used = conn.execute(
                    "SELECT coalesce(sum(reserved_bytes),0) FROM ingest_sessions WHERE "
                    "state!='CLEANED'"
                ).fetchone()[0]
                if used + row["size_bytes"] > config.cache_bytes:
                    raise HTTPException(507, "临时缓存配额不足")
                conn.execute(
                    "INSERT INTO ingest_sessions(job_id,reserved_bytes,expires_at) VALUES (?,?,?)",
                    (job_id, row["size_bytes"], row["expires_at"]),
                )
            conn.execute(
                "UPDATE archive_jobs SET "
                "state='UPLOADING',upload_owner=?,lease_token=?,lease_expires_at=?,upda"
                "ted_at=? WHERE id=?",
                (owner, token, now + config.lease_seconds, now, job_id),
            )
            self.event(conn, job_id, "UPLOADING")
        with self.db.transaction() as conn:
            offset = conn.execute(
                "SELECT offset FROM ingest_sessions WHERE job_id=?", (job_id,)
            ).fetchone()[0]
        return {
            "lease_token": token,
            "offset": offset,
            "chunk_bytes": self.settings.transfer_chunk_bytes,
            "lease_expires_at": now + config.lease_seconds,
        }

    def check_upload(self, conn, job_id, principal, token):
        row = dict(conn.execute("SELECT * FROM archive_jobs WHERE id=?", (job_id,)).fetchone())
        owner = principal.session["device_id"] or ("account:" + principal.user["id"])
        if (
            row["user_id"] != principal.user["id"]
            or row["upload_owner"] != owner
            or not row["lease_token"]
            or not secrets.compare_digest(row["lease_token"], token)
            or row["lease_expires_at"] <= time.time()
            or row["expires_at"] <= time.time()
            or row["state"] != "UPLOADING"
        ):
            raise HTTPException(409, "归档上传租约已过期或无效")
        return row

    def upload_chunk(self, job_id, offset, block, principal, token):
        self.owned(job_id, principal)
        if not 0 < len(block) <= self.settings.transfer_chunk_bytes or offset < 0:
            raise HTTPException(422, "归档块长度/偏移不合法")
        digest = hashlib.sha256(block).hexdigest()
        with self.db.transaction(True) as conn:
            row = self.check_upload(conn, job_id, principal, token)
            ingest = conn.execute(
                "SELECT offset FROM ingest_sessions WHERE job_id=?", (job_id,)
            ).fetchone()
            previous = conn.execute(
                "SELECT * FROM ingest_chunks WHERE job_id=? AND offset=?", (job_id, offset)
            ).fetchone()
            if previous:
                if (previous["size_bytes"], previous["sha256"]) != (len(block), digest):
                    raise HTTPException(409, "重复块内容不匹配")
                return {"offset": ingest["offset"], "duplicate": True}
            if offset != ingest["offset"] or offset + len(block) > row["size_bytes"]:
                raise HTTPException(409, "归档偏移不匹配")
            path = self.temp_path(job_id)
            if offset and (not path.exists() or path.stat().st_size < offset):
                raise HTTPException(409, "临时副本丢失，需要人工恢复")
            with path.open("r+b" if path.exists() else "xb") as stream:
                stream.truncate(offset)  # discard only bytes not durably acknowledged in SQLite
                stream.seek(offset)
                stream.write(block)
                stream.flush()
                os.fsync(stream.fileno())
            conn.execute(
                "INSERT INTO ingest_chunks VALUES (?,?,?,?)", (job_id, offset, len(block), digest)
            )
            conn.execute(
                "UPDATE ingest_sessions SET offset=? WHERE job_id=?", (offset + len(block), job_id)
            )
            conn.execute(
                "UPDATE archive_jobs SET lease_expires_at=?,updated_at=? WHERE id=?",
                (time.time() + self.config().lease_seconds, time.time(), job_id),
            )
        return {"offset": offset + len(block), "duplicate": False}

    def finish_upload(self, job_id, principal, token):
        row = self.owned(job_id, principal)
        if row["state"] == "STAGED":
            return self.public(row)
        with self.db.transaction(True) as conn:
            row = self.check_upload(conn, job_id, principal, token)
            offset = conn.execute(
                "SELECT offset FROM ingest_sessions WHERE job_id=?", (job_id,)
            ).fetchone()[0]
            if offset != row["size_bytes"]:
                raise HTTPException(409, "文件尚未上传完整")
            path = self.temp_path(job_id)
            if not path.exists() and row["size_bytes"] == 0:
                path.touch(exist_ok=False)
            if not path.exists() or file_digest(path) != (row["size_bytes"], row["sha256"]):
                raise HTTPException(422, "暂存完整性校验失败")
            conn.execute("UPDATE ingest_sessions SET state='STAGED' WHERE job_id=?", (job_id,))
            conn.execute(
                "UPDATE archive_jobs SET "
                "state='STAGED',lease_token=NULL,lease_expires_at=NULL,updated_at=? "
                "WHERE id=?",
                (time.time(), job_id),
            )
            self.event(conn, job_id, "STAGED")
        return self.public(self.row(job_id))

    def set_state(self, job_id, token, state, **changes):
        now = time.time()
        with self.db.transaction(True) as conn:
            row = conn.execute(
                "SELECT * FROM archive_jobs WHERE id=? AND worker_token=? AND worker_expires_at>?",
                (job_id, token, now),
            ).fetchone()
            if not row or row["state"] in FINAL:
                raise RuntimeError("WORKER_LEASE_LOST")
            fields = {"state": state, "updated_at": now, **changes}
            # All keys come from internal call sites, never raw HTTP JSON.
            conn.execute(
                "UPDATE archive_jobs SET " + ",".join(f"{k}=?" for k in fields) + " WHERE id=?",
                (*fields.values(), job_id),
            )
            self.event(conn, job_id, state, str(changes.get("error", "")))

    def claim_work(self, job_id=None):
        config, now = self.config(), time.time()
        if not config.enabled:
            return None
        with self.db.transaction(True) as conn:
            count = conn.execute(
                "SELECT count(*) FROM archive_jobs WHERE worker_expires_at>?", (now,)
            ).fetchone()[0]
            if count >= config.concurrency:
                return None
            states = tuple(PROCESSABLE)
            query = (
                "SELECT * FROM archive_jobs WHERE state IN (" + ",".join("?" for _ in states) + ") "
            )
            query += (
                "AND (worker_expires_at IS NULL OR worker_expires_at<=?) "
                "AND retry_at<=? AND expires_at>? "
            )
            query += (
                "AND (state!='AWAITING_AI_CONSENT' OR EXISTS (SELECT 1 FROM archive_preferences p "
            )
            query += "WHERE p.user_id=archive_jobs.user_id AND p.ai_mode!='off')) "
            params = (*states, now, now, now)
            if job_id:
                query += "AND id=? "
                params += (job_id,)
            query += "ORDER BY created_at LIMIT 1"
            row = conn.execute(query, params).fetchone()
            if not row:
                return None
            token = secrets.token_hex(24)
            conn.execute(
                "UPDATE archive_jobs SET worker_token=?,worker_expires_at=? WHERE id=?",
                (token, now + 600, row["id"]),
            )
            return row["id"], token

    def process_one(self, job_id=None):
        claimed = self.claim_work(job_id)
        if not claimed:
            return False
        job_id, token = claimed
        try:
            row = self.row(job_id)
            if not row["plan_json"]:
                prefs = self.preferences(row["user_id"])
                if prefs.ai_mode == "off":
                    self.set_state(job_id, token, "AWAITING_AI_CONSENT")
                    return True
                path = self.temp_path(job_id)
                if not path.exists() or file_digest(path) != (row["size_bytes"], row["sha256"]):
                    raise FileNotFoundError("STAGED_COPY_MISSING_OR_CHANGED")
                excerpt, extraction = extract_excerpt(path, row["filename"], prefs.ai_mode)
                # Check consent immediately before constructing the third-party request.
                if self.preferences(row["user_id"]).ai_mode != prefs.ai_mode:
                    self.set_state(job_id, token, "AWAITING_AI_CONSENT")
                    return True
                with self.db.transaction() as conn:
                    directories = [
                        r[0]
                        for r in conn.execute(
                            "SELECT DISTINCT v.target_directory FROM file_versions v "
                            "JOIN global_files f ON f.id=v.file_id "
                            "WHERE f.user_id=? LIMIT 100",
                            (row["user_id"],),
                        )
                    ]
                self.set_state(job_id, token, "AI_PLANNING")
                plan = self.ai.plan(
                    job_id,
                    row["user_id"],
                    {
                        "filename": row["filename"],
                        "size_bytes": row["size_bytes"],
                        "sha256": row["sha256"],
                        "original_category": row["original_category"],
                        "excerpt": excerpt,
                        "extraction": extraction,
                        "consent_mode": prefs.ai_mode,
                        "current_directories": directories,
                        "user_rules": prefs.rules,
                    },
                )
                if "fallback" in extraction or (prefs.ai_mode != "metadata" and not excerpt):
                    plan.needs_user_review = True
                    plan.reason = (
                        "正文提取不可用，使用文件名/元数据规划，需人工确认。" + plan.reason
                    )
                with self.db.transaction() as conn:
                    model = conn.execute(
                        "SELECT model FROM ai_usage_records WHERE id=?", (job_id,)
                    ).fetchone()[0]
                self.set_state(
                    job_id,
                    token,
                    "PLAN_REVIEW_REQUIRED" if plan.needs_user_review else "READY_TO_ARCHIVE",
                    plan_json=plan.model_dump_json(),
                    model=model,
                    error="",
                )
                if plan.needs_user_review:
                    return True
                row = self.row(job_id)
            plan = validate_plan(row["plan_json"], row["filename"], self.ai.config().categories)
            storage = self.job_storage(row)
            if not row["storage_key"]:
                object_key = (
                    f"{row['user_id']}/{plan.target_directory}/{job_id}/{plan.suggested_filename}"
                )
                if len(str(storage.path(object_key))) > 1024:
                    plan.needs_user_review = True
                    plan.reason = "正式存储路径过长，请缩短目录和文件名。"
                    self.set_state(
                        job_id, token, "PLAN_REVIEW_REQUIRED", plan_json=plan.model_dump_json()
                    )
                    return True
                self.set_state(job_id, token, "READY_TO_ARCHIVE", storage_key=object_key)
                row = self.row(job_id)
            # Metadata commit/publish is resumed without another storage upload.
            with self.db.transaction() as conn:
                committed = conn.execute(
                    "SELECT id FROM file_versions WHERE id=?", (job_id,)
                ).fetchone()
            if not committed:
                self.set_state(job_id, token, "ARCHIVING")
                key, path = row["storage_key"], self.temp_path(job_id)
                if not storage.exists(key):
                    storage.upload(path, key)
                self.set_state(job_id, token, "VERIFYING_STORAGE")
                meta = (
                    storage.metadata(key)
                    if isinstance(storage, LocalStorageProvider)
                    else storage.verify(key, row["size_bytes"], row["sha256"])
                )
                if (meta["size_bytes"], meta["sha256"]) != (row["size_bytes"], row["sha256"]):
                    raise ValueError("STORAGE_VERIFICATION_FAILED")
                self.set_state(job_id, token, "COMMITTING_METADATA")
                if self.before_metadata_commit:
                    self.before_metadata_commit()
                self.commit_metadata(row, plan, meta, token)
            self.publish(job_id, token)
            self.cleanup_job(job_id)
        except AIError as exc:
            if exc.code == "AI_BUSY":
                self.set_state(
                    job_id, token, "RETRY_WAIT", retry_at=time.time() + 2, error=exc.code
                )
            else:
                self.set_state(job_id, token, "PLAN_REVIEW_REQUIRED", error=exc.code)
        except Exception as exc:
            row = self.row(job_id)
            if row["worker_token"] == token and row["state"] not in FINAL:
                retries = row["retry_count"] + 1
                self.set_state(
                    job_id,
                    token,
                    "FAILED" if retries > self.config().max_retries else "RETRY_WAIT",
                    retry_count=retries,
                    retry_at=time.time() + min(2**retries, 300),
                    error=type(exc).__name__ + ":" + str(exc)[:160]
                    if not isinstance(exc, OSError)
                    else type(exc).__name__,
                )
        finally:
            with self.db.transaction(True) as conn:
                conn.execute(
                    "UPDATE archive_jobs SET worker_token=NULL,worker_expires_at=NULL "
                    "WHERE id=? AND worker_token=?",
                    (job_id, token),
                )
        return True

    def commit_metadata(self, row, plan, meta, token):
        job_id, now = row["id"], time.time()
        with self.db.transaction(True) as conn:
            current = conn.execute("SELECT * FROM archive_jobs WHERE id=?", (job_id,)).fetchone()
            if (
                current["worker_token"] != token
                or current["worker_expires_at"] <= now
                or current["state"] in FINAL
            ):
                raise RuntimeError("WORKER_LEASE_LOST")
            conn.execute(
                "INSERT OR IGNORE INTO storage_objects VALUES (?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    job_id,
                    meta["provider"],
                    meta["key"],
                    meta["size_bytes"],
                    meta["sha256"],
                    meta["verification_level"],
                    now,
                ),
            )
            from filemind_server.downloads import remember, signature

            if meta["provider"] == "local":
                with self.storage.read(meta["key"]) as verified:
                    if meta.get("verification_signature") == signature(verified):
                        remember(conn, verified, meta["key"], meta["sha256"])
            request = conn.execute(
                "SELECT * FROM library_version_requests WHERE job_id=?", (job_id,)
            ).fetchone()
            if request:
                from filemind_server.library import LibraryService

                LibraryService(self.db, self).commit_version(conn, request, row, plan, now)
                return
            conn.execute(
                "INSERT OR IGNORE INTO global_files VALUES (?,?,'private',?,?)",
                (job_id, row["user_id"], now, now),
            )
            conn.execute(
                "INSERT OR IGNORE INTO file_versions VALUES (?,?,1,?,?,?,?,?,?,?)",
                (
                    job_id,
                    job_id,
                    job_id,
                    plan.suggested_filename,
                    plan.category,
                    plan.target_directory,
                    plan.summary,
                    row["sha256"],
                    now,
                ),
            )
            conn.execute(
                "INSERT OR IGNORE INTO file_sources VALUES (?,?,?,?)",
                (job_id, row["source_transfer_id"], row["primary_owner"], row["backup_owner"]),
            )
            conn.execute(
                "INSERT OR IGNORE INTO outbox_events VALUES (?,?,'CATALOG_PUBLISH',NULL,?)",
                (job_id, job_id, now),
            )

    def publish(self, job_id, token):
        now = time.time()
        with self.db.transaction(True) as conn:
            row = conn.execute("SELECT * FROM archive_jobs WHERE id=?", (job_id,)).fetchone()
            if (
                row["worker_token"] != token
                or row["worker_expires_at"] <= now
                or row["state"] in FINAL
            ):
                raise RuntimeError("WORKER_LEASE_LOST")
            conn.execute(
                "INSERT OR IGNORE INTO catalog_revisions(user_id,job_id,created_at) VALUES (?,?,?)",
                (row["user_id"], job_id, now),
            )
            revision = conn.execute(
                "SELECT id FROM catalog_revisions WHERE job_id=?", (job_id,)
            ).fetchone()[0]
            if not conn.execute(
                "SELECT 1 FROM library_version_requests WHERE job_id=?", (job_id,)
            ).fetchone():
                conn.execute(
                    "INSERT OR IGNORE INTO catalog_entries VALUES (?,?,?)",
                    (job_id, job_id, revision),
                )
            from filemind_server.library import LibraryService

            LibraryService(self.db, self).published(conn, job_id)
            conn.execute("UPDATE outbox_events SET published_at=? WHERE id=?", (now, job_id))
            conn.execute(
                "UPDATE archive_jobs SET "
                "state='COMPLETED',error='',completed_at=?,updated_at=? WHERE id=?",
                (now, now, job_id),
            )
            self.event(conn, job_id, "COMPLETED")

    def review(self, job_id, principal, plan):
        row = self.owned(job_id, principal)
        plan = validate_plan(plan, row["filename"], self.ai.config().categories)
        if plan.needs_user_review:
            raise HTTPException(422, "确认计划需要将needs_user_review设为false")
        with self.db.transaction(True) as conn:
            row = conn.execute("SELECT * FROM archive_jobs WHERE id=?", (job_id,)).fetchone()
            if (
                row["state"] != "PLAN_REVIEW_REQUIRED"
                or row["storage_key"]
                or (row["worker_expires_at"] or 0) > time.time()
            ):
                raise HTTPException(409, "任务不允许修改计划")
            # Human plan is explicit, never falsely labelled successful AI.
            conn.execute(
                "UPDATE archive_jobs SET "
                "plan_json=?,state='READY_TO_ARCHIVE',error='',model=coalesce(model,'ma"
                "nual-review'),updated_at=? WHERE id=?",
                (plan.model_dump_json(), time.time(), job_id),
            )
            self.event(conn, job_id, "READY_TO_ARCHIVE", "用户明确确认计划")

    def retry(self, job_id, extend_expired=False):
        with self.db.transaction(True) as conn:
            row = conn.execute("SELECT * FROM archive_jobs WHERE id=?", (job_id,)).fetchone()
            if (
                not row
                or row["state"] not in {"FAILED", "RETRY_WAIT"}
                or (
                    row["expires_at"] <= time.time() and not (extend_expired and row["storage_key"])
                )
            ):
                raise HTTPException(409, "任务不可重试；过期或AI不确定请求需要人工处理")
            conn.execute(
                "UPDATE archive_jobs SET "
                "state='RETRY_WAIT',retry_count=0,retry_at=0,error='' WHERE id=?",
                (job_id,),
            )
            if extend_expired and row["expires_at"] <= time.time():
                conn.execute(
                    "UPDATE archive_jobs SET expires_at=? WHERE id=?",
                    (time.time() + self.config().ttl_seconds, job_id),
                )
                self.event(conn, job_id, "RETRY_WAIT", "管理员延长已有正式对象的恢复期限")
            self.event(conn, job_id, "RETRY_WAIT", "受控重试；复用已有计划和正式对象")

    def cancel(self, job_id, principal):
        self.owned(job_id, principal)
        with self.db.transaction(True) as conn:
            row = conn.execute("SELECT * FROM archive_jobs WHERE id=?", (job_id,)).fetchone()
            if row["state"] in FINAL:
                return
            if (row["worker_expires_at"] or 0) > time.time() or row["storage_key"]:
                raise HTTPException(409, "执行中或已建立正式对象，不能中途删除；请等待或对账")
            conn.execute(
                "UPDATE archive_jobs SET "
                "state='CANCELLED',lease_token=NULL,updated_at=? WHERE id=?",
                (time.time(), job_id),
            )
            self.event(conn, job_id, "CANCELLED")
        self.cleanup_job(job_id)

    def cleanup_job(self, job_id):
        row = self.row(job_id)
        if row["state"] not in {"COMPLETED", "CANCELLED", "EXPIRED"}:
            return
        self.temp_path(job_id).unlink(missing_ok=True)
        with self.db.transaction(True) as conn:
            conn.execute(
                "UPDATE ingest_sessions SET state='CLEANED',reserved_bytes=0 WHERE job_id=?",
                (job_id,),
            )

    def sweep(self):
        now = time.time()
        self.ingest_handoffs()
        with self.db.transaction(True) as conn:
            rows = conn.execute(
                "SELECT * FROM archive_jobs WHERE expires_at<=? AND state NOT IN "
                "('COMPLETED','CANCELLED','EXPIRED') "
                "AND (worker_expires_at IS NULL OR worker_expires_at<=?)",
                (now, now),
            ).fetchall()
            for row in rows:
                # Never discard recovery context for already-published formal objects.
                if row["storage_key"]:
                    conn.execute(
                        "UPDATE archive_jobs SET "
                        "state='FAILED',error='EXPIRED_WITH_STORAGE_REQUIRES_RECONCILIA"
                        "TION' WHERE id=?",
                        (row["id"],),
                    )
                else:
                    conn.execute(
                        "UPDATE archive_jobs SET state='EXPIRED',lease_token=NULL WHERE id=?",
                        (row["id"],),
                    )
                    self.event(conn, row["id"], "EXPIRED")
            cleanup = [
                r[0]
                for r in conn.execute(
                    "SELECT j.id FROM archive_jobs j JOIN ingest_sessions i ON i.job_id=j.id "
                    "WHERE j.state IN ('COMPLETED','CANCELLED','EXPIRED') AND "
                    "i.state!='CLEANED' LIMIT 100"
                )
            ]
        for job_id in cleanup:
            self.cleanup_job(job_id)

    def catalog(self, user_id):
        with self.db.transaction() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT f.id "
                    "file_id,f.visibility,v.*,s.provider,s.object_key,s.size_bytes,s.ve"
                    "rification_level,c.revision_id "
                    "FROM catalog_entries c JOIN global_files f ON f.id=c.file_id "
                    "JOIN file_versions v ON v.id=c.version_id JOIN storage_objects s "
                    "ON s.id=v.object_id "
                    "WHERE f.user_id=? ORDER BY v.created_at DESC LIMIT 200",
                    (user_id,),
                )
            ]

    def reconcile(self):
        with self.db.transaction() as conn:
            objects = [dict(r) for r in conn.execute("SELECT * FROM storage_objects")]
            unresolved = [
                dict(r)
                for r in conn.execute(
                    "SELECT id,storage_key,state FROM archive_jobs "
                    "WHERE storage_key IS NOT NULL AND state!='COMPLETED'"
                )
            ]
        failures = []
        for obj in objects:
            try:
                provider = self.provider(obj["provider"])
                meta = (
                    provider.metadata(obj["object_key"])
                    if isinstance(provider, LocalStorageProvider)
                    else provider.verify(obj["object_key"], obj["size_bytes"], obj["sha256"])
                )
                if (meta["size_bytes"], meta["sha256"]) != (obj["size_bytes"], obj["sha256"]):
                    failures.append({"job_id": obj["job_id"], "reason": "CONTENT_MISMATCH"})
            except (OSError, ValueError, NotImplementedError):
                failures.append({"job_id": obj["job_id"], "reason": "MISSING_OR_UNREADABLE"})
        report = {
            "checked": len(objects),
            "failures": failures,
            "unresolved": unresolved,
            "action": "read-only; no formal objects deleted",
        }
        with self.db.transaction(True) as conn:
            conn.execute(
                "INSERT INTO reconciliation_jobs(created_at,result_json) VALUES (?,?)",
                (time.time(), json.dumps(report)),
            )
        return report
