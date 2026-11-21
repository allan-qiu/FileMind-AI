"""Resumable offset/chunks protocol matching archives; isolated temporary namespace."""

import os
import shutil
import time
import uuid

from fastapi import HTTPException

from filemind.services.classification import valid_name
from filemind_server.share_config import config
from filemind_server.shares import event
from filemind_server.storage import LocalStorageProvider


class TemporaryShareStorage:
    def __init__(self, db, settings):
        self.db, self.settings = db, settings
        self.root = settings.data_dir / "share-temporary"
        self.storage = None

    def initialize(self):
        self.storage = LocalStorageProvider(self.root)

    def begin(self, uid, data):
        cfg, now, upload = config(self.db), time.time(), str(uuid.uuid4())
        if not cfg.allow_temporary:
            raise HTTPException(403, "系统已关闭临时分享")
        try:
            valid_name(data["filename"])
        except ValueError:
            raise HTTPException(422, "文件名称无效") from None
        if data["size_bytes"] > cfg.max_file_bytes:
            raise HTTPException(413, "超过单文件上限")
        if data["size_bytes"] > max(0, shutil.disk_usage(self.root).free - 16 * 1024**2):
            raise HTTPException(507, "本地临时存储磁盘余量不足")
        with self.db.transaction(True) as conn:

            def used(owner=None):
                a = conn.execute(
                    "SELECT coalesce(sum(file_size),0) FROM temporary_share_objects "
                    "WHERE deletion_status!='DELETED'" + (" AND owner_user_id=?" if owner else ""),
                    (owner,) if owner else (),
                ).fetchone()[0]
                b = conn.execute(
                    "SELECT coalesce(sum(file_size),0) FROM share_uploads WHERE status='UPLOADING'"
                    + (" AND owner_user_id=?" if owner else ""),
                    (owner,) if owner else (),
                ).fetchone()[0]
                return a + b

            if (
                used(uid) + data["size_bytes"] > cfg.user_temp_bytes
                or used() + data["size_bytes"] > cfg.total_temp_bytes
            ):
                raise HTTPException(507, "临时存储配额不足")
            pending = conn.execute(
                "SELECT count(*) FROM share_uploads WHERE owner_user_id=? AND status='UPLOADING'",
                (uid,),
            ).fetchone()[0]
            if pending >= 10:
                raise HTTPException(429, "同时上传任务过多")
            storage_id = self.storage.begin_upload()
            conn.execute(
                "INSERT INTO "
                "share_uploads(upload_id,owner_user_id,storage_id,filename,file_size,sha256,status,expires_at,created_at)"
                " VALUES (?,?,?,?,?,?,'UPLOADING',?,?)",
                (
                    upload,
                    uid,
                    storage_id,
                    data["filename"],
                    data["size_bytes"],
                    data["sha256"],
                    now + cfg.upload_ttl,
                    now,
                ),
            )
            return {
                "upload_id": upload,
                "offset": 0,
                "chunk_bytes": self.settings.transfer_chunk_bytes,
                "expires_at": now + cfg.upload_ttl,
            }

    def upload(self, conn, uid, upload):
        row = conn.execute(
            "SELECT * FROM share_uploads WHERE upload_id=? AND owner_user_id=?", (upload, uid)
        ).fetchone()
        if not row:
            raise HTTPException(404, "上传不存在或未授权")
        return dict(row)

    def chunk(self, uid, upload, offset, block):
        with self.db.transaction(True) as conn:
            row = self.upload(conn, uid, upload)
            if row["status"] != "UPLOADING" or row["expires_at"] <= time.time():
                raise HTTPException(410, "上传已结束或过期")
            if (
                not block
                or len(block) > self.settings.transfer_chunk_bytes
                or offset + len(block) > row["file_size"]
            ):
                raise HTTPException(413, "上传块超出范围")
            path = self.storage.pending / row["storage_id"]
            from filemind.infrastructure.file_analysis import reject_links

            reject_links(path)
            with path.open("r+b") as stream:
                # Crash between fsync and DB commit: discard only uncommitted upload tail.
                stream.truncate(row["offset"])
                if offset < row["offset"]:
                    stream.seek(offset)
                    if offset + len(block) <= row["offset"] and stream.read(len(block)) == block:
                        return {"offset": row["offset"]}
                    raise HTTPException(409, "重试内容与已上传数据不同")
                if offset != row["offset"]:
                    raise HTTPException(409, "上传偏移不匹配")
                stream.seek(offset)
                stream.write(block)
                stream.flush()
                os.fsync(stream.fileno())
            conn.execute(
                "UPDATE share_uploads SET offset=? WHERE upload_id=?", (offset + len(block), upload)
            )
            return {"offset": offset + len(block)}

    def complete(self, uid, upload):
        cfg = config(self.db)
        with self.db.transaction(True) as conn:
            row = self.upload(conn, uid, upload)
            if row["status"] == "COMPLETED":
                return {"object_id": row["object_id"]}
            if (
                row["status"] != "UPLOADING"
                or row["expires_at"] <= time.time()
                or row["offset"] != row["file_size"]
            ):
                raise HTTPException(409, "上传未完成或已过期")
            # Deterministic destination also permits retry after a publish/DB-commit crash.
            oid, key = row["upload_id"], "objects/" + row["upload_id"]
            try:
                if self.storage.exists(key):
                    meta = self.storage.metadata(key)
                    if (meta["size_bytes"], meta["sha256"]) != (row["file_size"], row["sha256"]):
                        raise ValueError("INTEGRITY_MISMATCH")
                else:
                    meta = self.storage.complete_upload(
                        row["storage_id"], key, row["file_size"], row["sha256"]
                    )
            except (OSError, ValueError):
                raise HTTPException(409, "上传内容缺失或哈希不匹配") from None
            now = time.time()
            conn.execute(
                "INSERT INTO temporary_share_objects VALUES (?,?,?,?,?,?,?,?,'LIVE','')",
                (
                    oid,
                    uid,
                    key,
                    row["filename"],
                    row["file_size"],
                    row["sha256"],
                    now,
                    now + cfg.default_ttl,
                ),
            )
            from filemind_server.downloads import remember, signature

            with self.storage.read(key) as stream:
                if meta.get("verification_signature") == signature(stream):
                    remember(conn, stream, "temporary:" + key, row["sha256"])
            conn.execute(
                "UPDATE share_uploads SET status='COMPLETED',object_id=? WHERE upload_id=?",
                (oid, upload),
            )
            event(conn, None, "TEMPORARY_UPLOADED", uid)
            return {"object_id": oid, "size_bytes": row["file_size"], "sha256": row["sha256"]}

    def cancel(self, uid, upload):
        with self.db.transaction(True) as conn:
            row = self.upload(conn, uid, upload)
            if row["status"] == "COMPLETED":
                raise HTTPException(409, "已完成上传不能取消")
            conn.execute("UPDATE share_uploads SET status='CANCELLED' WHERE upload_id=?", (upload,))
        return {"status": "CANCELLED"}

    def objects(self, uid):
        with self.db.transaction() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT t.*,(SELECT count(*) FROM share_links s WHERE "
                    "s.temporary_object_id=t.object_id AND s.status='ACTIVE' AND "
                    "s.expires_at>?) active_references FROM temporary_share_objects t "
                    "WHERE owner_user_id=? ORDER BY created_at DESC LIMIT 200",
                    (time.time(), uid),
                )
            ]
