"""Transactional expiry/reference reconciliation and retryable temporary-only deletion."""

import time

from fastapi import HTTPException

from filemind_server.share_config import config
from filemind_server.shares import event


class ShareExpiryWorker:
    def __init__(self, manager, temporary):
        self.manager, self.temporary, self.db = manager, temporary, manager.db

    def sweep(self):
        cfg, now = config(self.db), time.time()
        with self.db.transaction(True) as conn:
            for row in conn.execute("SELECT * FROM share_links WHERE status='ACTIVE'"):
                state = "EXPIRED" if row["expires_at"] <= now else None
                if not state and row["source_type"] == "library":
                    try:
                        self.manager.source(conn, row)
                    except HTTPException:
                        state = "REVOKED"
                if state:
                    conn.execute(
                        "UPDATE share_links SET status=?,updated_at=? WHERE share_id=?",
                        (state, now, row["share_id"]),
                    )
                    event(conn, row["share_id"], state)
            for session in conn.execute(
                "SELECT session_id,share_id FROM share_download_sessions WHERE "
                "reserved_quota=1 AND (reservation_expires_at<=? OR share_id IN "
                "(SELECT share_id FROM share_links WHERE status!='ACTIVE'))",
                (now,),
            ):
                event(conn, session["share_id"], "SESSION_EXPIRED", session=session["session_id"])
            conn.execute(
                "UPDATE share_download_sessions SET "
                "status='EXPIRED',reserved_quota=0,failed_at=?,error='DEADLINE_OR_SHARE_ENDED'"
                " WHERE reserved_quota=1 AND (reservation_expires_at<=? OR share_id "
                "IN (SELECT share_id FROM share_links WHERE status!='ACTIVE'))",
                (now, now),
            )
            conn.execute("DELETE FROM share_access_grants WHERE expires_at<=?", (now,))
            conn.execute("DELETE FROM share_rate_limits WHERE expires_at<=?", (now,))
            # Deleted objects retain records; cleanup retries are safe across workers/restarts.
            for row in conn.execute(
                "SELECT * FROM temporary_share_objects WHERE deletion_status IN ('LIVE','FAILED')"
            ):
                references = conn.execute(
                    "SELECT 1 FROM share_links WHERE temporary_object_id=? AND "
                    "status='ACTIVE' AND expires_at>?",
                    (row["object_id"], now),
                ).fetchone()
                active = conn.execute(
                    "SELECT 1 FROM share_download_sessions d JOIN share_links s ON "
                    "s.share_id=d.share_id WHERE s.temporary_object_id=? AND "
                    "d.reserved_quota=1 AND d.reservation_expires_at>?",
                    (row["object_id"], now),
                ).fetchone()
                had_links = conn.execute(
                    "SELECT 1 FROM share_links WHERE temporary_object_id=?", (row["object_id"],)
                ).fetchone()
                if references or active or (not had_links and row["expires_at"] > now):
                    continue
                try:
                    if not row["storage_location"].startswith("objects/"):
                        raise ValueError("INVALID_TEMPORARY_NAMESPACE")
                    self.temporary.storage.delete(row["storage_location"])
                    conn.execute(
                        "UPDATE temporary_share_objects SET "
                        "deletion_status='DELETED',error='' WHERE object_id=?",
                        (row["object_id"],),
                    )
                    event(conn, None, "TEMPORARY_CLEANED", row["owner_user_id"])
                except (OSError, ValueError):
                    conn.execute(
                        "UPDATE temporary_share_objects SET "
                        "deletion_status='FAILED',error='DELETE_RETRY_REQUIRED' WHERE "
                        "object_id=?",
                        (row["object_id"],),
                    )
            for row in conn.execute(
                "SELECT * FROM share_uploads WHERE (status='UPLOADING' AND "
                "expires_at<=?) OR status IN ('CANCELLED','EXPIRED')",
                (now,),
            ):
                try:
                    from filemind.infrastructure.file_analysis import reject_links

                    path = self.temporary.storage.pending / row["storage_id"]
                    reject_links(path)
                    path.unlink(missing_ok=True)
                    # A completed filesystem publish with no committed DB object is orphaned.
                    key = "objects/" + row["upload_id"]
                    if not conn.execute(
                        "SELECT 1 FROM temporary_share_objects WHERE storage_location=?", (key,)
                    ).fetchone():
                        self.temporary.storage.delete(key)
                    conn.execute(
                        "UPDATE share_uploads SET status='CLEANED' WHERE upload_id=?",
                        (row["upload_id"],),
                    )
                except (OSError, ValueError):
                    conn.execute(
                        "UPDATE share_uploads SET status='EXPIRED' WHERE upload_id=?",
                        (row["upload_id"],),
                    )
            known = {
                r[0]
                for r in conn.execute(
                    "SELECT storage_id FROM share_uploads WHERE status='UPLOADING'"
                )
            }
            for path in self.temporary.storage.pending.iterdir():
                if (
                    path.name not in known
                    and path.is_file()
                    and not path.is_symlink()
                    and path.stat().st_mtime < now - cfg.upload_ttl
                ):
                    path.unlink(missing_ok=True)
            conn.execute(
                "DELETE FROM share_events WHERE created_at<?",
                (now - cfg.event_retention_days * 86400,),
            )
