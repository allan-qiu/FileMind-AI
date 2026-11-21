"Authoritative logical catalog. SQLite write transactions fence versions and operations."

import hashlib
import json
import time
import uuid

from fastapi import HTTPException

from filemind.services.classification import valid_name
from filemind_server.security import new_token, token_hash


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fingerprint(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def directory(value):
    if not value:
        return ""
    if len(value) > 400:
        raise HTTPException(422, "目录过长")
    try:
        for part in value.split("/"):
            valid_name(part)
    except ValueError:
        raise HTTPException(422, "逻辑目录不合法") from None
    return value


SELECT = """SELECT f.id file_id,f.user_id,f.visibility,f.created_at archived_at,f.updated_at,
 m.filename,m.category,m.directory,m.favorite,m.deleted_at,m.workspace_id,m.revision,
 v.id version_id,v.version,v.sha256,v.summary,v.created_at version_created_at,
 s.size_bytes,s.provider,s.object_key,s.verification_level,v.target_directory storage_directory
 FROM global_files f JOIN file_metadata m ON m.file_id=f.id
 JOIN catalog_entries c ON c.file_id=f.id JOIN file_versions v ON v.id=c.version_id
 JOIN storage_objects s ON s.id=v.object_id """
ACCESS = (
    "(m.workspace_id IS NULL AND f.user_id=? OR EXISTS (SELECT 1 FROM workspa"
    "ce_members w WHERE w.workspace_id=m.workspace_id AND w.user_id=?))"
)


class LibraryService:
    def __init__(self, db, archives):
        self.db, self.archives = db, archives

    @staticmethod
    def scope(row):
        return (
            "workspace:" + row["workspace_id"] if row["workspace_id"] else "user:" + row["user_id"]
        )

    def owned(self, conn, file_id, p, write=False):
        row = conn.execute(
            SELECT + " WHERE f.id=? AND " + ACCESS, (file_id, p.user["id"], p.user["id"])
        ).fetchone()
        if not row:
            raise HTTPException(404, "文件不存在或未授权")
        row = dict(row)
        if write and row["workspace_id"]:
            membership = conn.execute(
                "SELECT permission FROM workspace_members WHERE workspace_id=? AND user_id=?",
                (row["workspace_id"], p.user["id"]),
            ).fetchone()
            if not membership or membership[0] not in {"editor", "admin"}:
                raise HTTPException(403, "共享空间只读权限")
        return row

    def raw(self, conn, file_id):
        return dict(conn.execute(SELECT + " WHERE f.id=?", (file_id,)).fetchone())

    def emit(self, conn, file_id, kind, old_scope=None):
        if kind == "DELETE" or (old_scope and old_scope != self.scope(self.raw(conn, file_id))):
            from filemind_server.shares import event

            for link in conn.execute(
                "SELECT share_id FROM share_links WHERE status='ACTIVE' AND "
                "file_version_id IN (SELECT id FROM file_versions WHERE file_id=?)",
                (file_id,),
            ):
                event(conn, link["share_id"], "SOURCE_REVOKED")
            conn.execute(
                "UPDATE share_links SET status='REVOKED',revoked_at=?,updated_at=? "
                "WHERE status='ACTIVE' AND file_version_id IN (SELECT id FROM "
                "file_versions WHERE file_id=?)",
                (time.time(), time.time(), file_id),
            )
            conn.execute(
                "UPDATE share_download_sessions SET "
                "status='CANCELLED',reserved_quota=0,error='SOURCE_AUTHORIZATION_ENDED'"
                " WHERE reserved_quota=1 AND share_id IN (SELECT share_id FROM "
                "share_links WHERE status='REVOKED')"
            )
        conn.execute("UPDATE global_files SET updated_at=? WHERE id=?", (time.time(), file_id))
        row = self.raw(conn, file_id)
        scopes = {self.scope(row)}
        if old_scope:
            scopes.add(old_scope)
        last = 0
        for scope in sorted(scopes):
            last = conn.execute(
                (
                    "INSERT INTO catalog_events(scope,file_id,kind,payload,created_at) VALUES"
                    " (?,?,?,'{}',?)"
                ),
                (scope, file_id, kind, time.time()),
            ).lastrowid
            row["revision"] = last
            conn.execute("UPDATE catalog_events SET payload=? WHERE id=?", (encoded(row), last))
        conn.execute("UPDATE file_metadata SET revision=? WHERE file_id=?", (last, file_id))
        conn.execute("UPDATE global_files SET updated_at=? WHERE id=?", (time.time(), file_id))
        return self.raw(conn, file_id)

    def published(self, conn, job_id):
        request = conn.execute(
            "SELECT * FROM library_version_requests WHERE job_id=?", (job_id,)
        ).fetchone()
        if request:
            file_id = request["file_id"]
            return self.emit(
                conn,
                file_id,
                "CONFLICT"
                if conn.execute(
                    "SELECT 1 FROM file_conflicts WHERE candidate_version_id=?", (job_id,)
                ).fetchone()
                else "VERSION",
            )
        version = conn.execute("SELECT * FROM file_versions WHERE id=?", (job_id,)).fetchone()
        conn.execute(
            (
                "INSERT OR IGNORE INTO file_metadata(file_id,filename,category,directory)"
                " VALUES (?,?,?,?)"
            ),
            (job_id, version["filename"], version["category"], version["target_directory"]),
        )
        return self.emit(conn, job_id, "CREATED")

    def list_files(self, p, query):
        where, params = [ACCESS], [p.user["id"], p.user["id"]]
        where.append("m.deleted_at IS " + ("NOT NULL" if query.get("trash") else "NULL"))
        for key, column in (("q", "m.filename"), ("category", "m.category")):
            if query.get(key):
                where.append(f"instr(lower({column}),lower(?))>0")
                params.append(query[key])
        if query.get("directory") is not None:
            where.append("m.directory=?")
            params.append(query["directory"])
        if query.get("extension"):
            where.append("lower(substr(m.filename,-length(?)))=lower(?)")
            ext = "." + query["extension"].lstrip(".")
            params.extend([ext, ext])
        if query.get("favorite"):
            where.append("m.favorite=1")
        for key, column, sign in (
            ("min_size", "s.size_bytes", ">="),
            ("max_size", "s.size_bytes", "<="),
            ("after", "f.updated_at", ">="),
            ("before", "f.updated_at", "<="),
        ):
            if query.get(key) is not None:
                where.append(f"{column}{sign}?")
                params.append(query[key])
        order = {
            "name": "m.filename COLLATE NOCASE",
            "size": "s.size_bytes",
            "recent": "f.updated_at",
            "archived": "f.created_at",
        }[query.get("sort", "recent")]
        clause = " WHERE " + " AND ".join(where)
        with self.db.transaction() as conn:
            count = conn.execute(
                "SELECT count(*) FROM (" + SELECT + clause + ")", params
            ).fetchone()[0]
            rows = conn.execute(
                SELECT
                + clause
                + " ORDER BY "
                + order
                + (" DESC" if query.get("descending", True) else " ASC")
                + ",f.id LIMIT ? OFFSET ?",
                (*params, query.get("limit", 50), query.get("offset", 0)),
            ).fetchall()
        return {"items": [dict(r) for r in rows], "total": count}

    def versions(self, conn, file_id, limit=200, offset=0):
        return [
            dict(r)
            for r in conn.execute(
                (
                    "SELECT v.*,s.size_bytes,s.provider,x.actor_id,x.device_id,x.base_version"
                    "_id FROM file_versions v JOIN storage_objects s ON s.id=v.object_id LEFT"
                    " JOIN file_version_sources x ON x.version_id=v.id WHERE v.file_id=? ORDE"
                    "R BY v.version DESC LIMIT ? OFFSET ?"
                ),
                (file_id, limit, offset),
            )
        ]

    def detail(self, p, file_id):
        with self.db.transaction() as conn:
            row = self.owned(conn, file_id, p)
            row["versions"] = self.versions(conn, file_id)
            row["sources"] = [
                dict(r)
                for r in conn.execute(
                    (
                        "SELECT x.* FROM file_sources x JOIN file_versions v ON v.id=x.version_id"
                        " WHERE v.file_id=? LIMIT 200"
                    ),
                    (file_id,),
                )
            ]
            row["history"] = [
                dict(r)
                for r in conn.execute(
                    (
                        "SELECT id,user_id,kind,created_at,before_json FROM file_operation_events"
                        " WHERE file_id=? ORDER BY created_at DESC LIMIT 100"
                    ),
                    (file_id,),
                )
            ]
            return row

    def acl(self, conn, p):
        members = [
            tuple(r)
            for r in conn.execute(
                (
                    "SELECT workspace_id,permission FROM workspace_members WHERE user_id=? OR"
                    "DER BY workspace_id"
                ),
                (p.user["id"],),
            )
        ]
        return fingerprint(
            [
                p.user["id"],
                conn.execute("SELECT epoch FROM library_acl_epoch").fetchone()[0],
                members,
            ]
        )

    @staticmethod
    def revision(conn):
        return conn.execute("SELECT coalesce(max(id),0) FROM catalog_events").fetchone()[0]

    def sync(self, p, after=None, acl="", limit=200, offset=0, at=None, client_id=""):
        client_id = self.sync_identity(p, client_id)
        with self.db.transaction(True) as conn:
            current, permission = self.revision(conn), self.acl(conn, p)
            if after is not None:
                first = conn.execute("SELECT coalesce(min(id),0) FROM catalog_events").fetchone()[0]
                if acl != permission or after > current or (first and after < first - 1):
                    raise HTTPException(409, "SNAPSHOT_REQUIRED")
                scopes = ["user:" + p.user["id"]] + [
                    "workspace:" + r[0]
                    for r in conn.execute(
                        "SELECT workspace_id FROM workspace_members WHERE user_id=?",
                        (p.user["id"],),
                    )
                ]
                rows = conn.execute(
                    "SELECT * FROM catalog_events WHERE id>? AND scope IN ("
                    + ",".join("?" for _ in scopes)
                    + ") ORDER BY id LIMIT ?",
                    (after, *scopes, limit + 1),
                ).fetchall()
                more, rows = len(rows) > limit, rows[:limit]
                items = []
                for row in rows:
                    if row["file_id"] is None:
                        items.append(
                            {
                                "revision": row["id"],
                                "kind": row["kind"],
                                "folder": json.loads(row["payload"]),
                            }
                        )
                        continue
                    try:
                        visible = self.owned(conn, row["file_id"], p)
                        payload = json.loads(row["payload"])
                        if not payload:
                            payload = visible
                        items.append({"revision": row["id"], "kind": row["kind"], "file": payload})
                    except HTTPException:
                        items.append(
                            {"revision": row["id"], "kind": "REMOVED", "file_id": row["file_id"]}
                        )
                cursor = rows[-1]["id"] if more else current
            else:
                if at is not None and at != current:
                    raise HTTPException(409, "SNAPSHOT_REQUIRED")
                rows = conn.execute(
                    SELECT + " WHERE " + ACCESS + " ORDER BY f.id LIMIT ? OFFSET ?",
                    (p.user["id"], p.user["id"], limit + 1, offset),
                ).fetchall()
                more, items, cursor = len(rows) > limit, [dict(r) for r in rows[:limit]], current
            receipt = ""
            if client_id:
                receipt = new_token()
                conn.execute(
                    "DELETE FROM catalog_sync_deliveries WHERE expires_at<?", (time.time(),)
                )
                conn.execute(
                    "INSERT INTO catalog_sync_deliveries VALUES (?,?,?,?,?,?)",
                    (
                        token_hash(receipt),
                        p.user["id"],
                        client_id,
                        cursor,
                        permission,
                        time.time() + 3600,
                    ),
                )
                conn.execute(
                    (
                        "INSERT INTO "
                        "catalog_sync_cursors(user_id,client_id,revision,acl,updated_at,sent_revision)"
                        " "
                        "VALUES (?,?,0,?,?,?) ON CONFLICT(user_id,client_id) DO UPDATE SET "
                        "sent_revision=max(sent_revision,excluded.sent_revision),updated_at=excluded.updated_at"
                    ),
                    (
                        p.user["id"],
                        client_id,
                        permission,
                        time.time(),
                        cursor,
                    ),
                )
            return {
                **({"receipt": receipt, "client_id": client_id} if receipt else {}),
                "items": items,
                "revision": cursor,
                "acl": permission,
                "has_more": more,
                "next_offset": offset + len(items),
            }

    @staticmethod
    def sync_identity(p, client_id):
        return "device:" + p.session["device_id"] if p.session.get("device_id") else client_id

    def acknowledge(self, p, client_id, receipt):
        client_id = self.sync_identity(p, client_id)
        with self.db.transaction(True) as conn:
            sent = conn.execute(
                "SELECT * FROM catalog_sync_deliveries WHERE receipt_hash=? "
                "AND user_id=? AND client_id=? AND expires_at>?",
                (token_hash(receipt), p.user["id"], client_id, time.time()),
            ).fetchone()
            if not sent or sent["acl"] != self.acl(conn, p):
                raise HTTPException(409, "同步回执无效，请重新同步")
            conn.execute(
                "UPDATE catalog_sync_cursors SET revision=max(revision,?),acl=?,updated_at=? "
                "WHERE user_id=? AND client_id=?",
                (sent["revision"], sent["acl"], time.time(), p.user["id"], client_id),
            )
            return {
                "revision": conn.execute(
                    "SELECT revision FROM catalog_sync_cursors WHERE user_id=? AND client_id=?",
                    (p.user["id"], client_id),
                ).fetchone()[0]
            }

    def operation(self, p, file_id, data, kind="PATCH"):
        uid, key, now = p.user["id"], data["idempotency_key"], time.time()
        digest = fingerprint([file_id, kind, data])
        with self.db.transaction(True) as conn:
            row = self.owned(conn, file_id, p, True)
            previous = conn.execute(
                "SELECT * FROM file_operation_events WHERE user_id=? AND idempotency_key=?",
                (uid, key),
            ).fetchone()
            if previous:
                if previous["request_hash"] != digest:
                    raise HTTPException(409, "幂等键内容不同")
                return json.loads(previous["result_json"])
            if data["expected_revision"] != row["revision"]:
                raise HTTPException(409, "目录已变化，请刷新后重试")
            before = encoded(row)
            if kind == "DELETE":
                conn.execute(
                    "UPDATE file_metadata SET deleted_at=?,deleted_by=? WHERE file_id=?",
                    (now, uid, file_id),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO file_tombstones VALUES (?,?,?,?,?)",
                    (file_id, uid, now, row["directory"], row["version_id"]),
                )
            elif kind == "RESTORE":
                conn.execute(
                    "UPDATE file_metadata SET deleted_at=NULL,deleted_by=NULL WHERE file_id=?",
                    (file_id,),
                )
                conn.execute("DELETE FROM file_tombstones WHERE file_id=?", (file_id,))
                conn.execute(
                    "INSERT INTO file_restore_records VALUES (?,?,?,?,?,?)",
                    (str(uuid.uuid4()), file_id, uid, kind, row["version_id"], now),
                )
            elif kind == "VERSION_RESTORE":
                if row["deleted_at"]:
                    raise HTTPException(409, "请先恢复文件")
                if not conn.execute(
                    "SELECT 1 FROM file_versions WHERE id=? AND file_id=?",
                    (data["version_id"], file_id),
                ).fetchone():
                    raise HTTPException(404, "版本不存在")
                conn.execute(
                    "UPDATE catalog_entries SET version_id=? WHERE file_id=?",
                    (data["version_id"], file_id),
                )
                conn.execute(
                    "INSERT INTO file_restore_records VALUES (?,?,?,?,?,?)",
                    (str(uuid.uuid4()), file_id, uid, kind, data["version_id"], now),
                )
            else:
                if row["deleted_at"]:
                    raise HTTPException(409, "请先恢复文件")
                updates = {
                    k: v
                    for k, v in data.items()
                    if k in {"filename", "category", "directory", "favorite", "workspace_id"}
                }
                for k in ("filename", "category"):
                    if k in updates:
                        try:
                            valid_name(updates[k])
                        except ValueError:
                            raise HTTPException(422, "名称或分类不合法") from None
                if "directory" in updates:
                    directory(updates["directory"])
                if "workspace_id" in updates:
                    if row["user_id"] != uid:
                        raise HTTPException(403, "只有文件所有者可调整共享范围")
                    if updates["workspace_id"]:
                        member = conn.execute(
                            "SELECT permission FROM workspace_members "
                            "WHERE workspace_id=? AND user_id=?",
                            (updates["workspace_id"], uid),
                        ).fetchone()
                        if not member or member[0] != "admin":
                            raise HTTPException(403, "需要目标空间管理员权限")
                    conn.execute("UPDATE library_acl_epoch SET epoch=epoch+1")
                if updates:
                    conn.execute(
                        "UPDATE file_metadata SET "
                        + ",".join(k + "=?" for k in updates)
                        + " WHERE file_id=?",
                        (*updates.values(), file_id),
                    )
            result = self.emit(conn, file_id, kind, self.scope(row))
            conn.execute(
                "INSERT INTO file_operation_events VALUES (?,?,?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), uid, file_id, key, digest, kind, before, encoded(result), now),
            )
            return result

    def commit_version(self, conn, request, row, plan, now):
        fid, job = request["file_id"], row["id"]
        current = self.raw(conn, fid)
        # Permission is rechecked after asynchronous upload and AI planning.
        from filemind_server.dependencies import Principal

        actor = Principal({"id": request["actor_id"]}, {})
        self.owned(conn, fid, actor, True)
        if not conn.execute(
            "SELECT 1 FROM users WHERE id=? AND enabled=1", (request["actor_id"],)
        ).fetchone():
            raise HTTPException(403, "上传账户已停用")
        if (
            request["device_id"]
            and not conn.execute(
                "SELECT 1 FROM devices WHERE id=? AND revoked_at IS NULL", (request["device_id"],)
            ).fetchone()
        ):
            raise HTTPException(403, "上传设备已撤销")
        number = conn.execute(
            "SELECT coalesce(max(version),0)+1 FROM file_versions WHERE file_id=?", (fid,)
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO file_versions VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                job,
                fid,
                number,
                job,
                plan.suggested_filename,
                plan.category,
                plan.target_directory,
                plan.summary,
                row["sha256"],
                now,
            ),
        )
        conn.execute(
            "INSERT INTO file_version_sources VALUES (?,?,?,?,?)",
            (job, request["actor_id"], request["device_id"], request["base_version_id"], job),
        )
        if current["version_id"] != request["base_version_id"] or current["deleted_at"]:
            conn.execute(
                (
                    "INSERT INTO file_conflicts(id,file_id,base_version_id,current_version_id"
                    ",candidate_version_id,actor_id,device_id,reason,created_at) VALUES (?,?,"
                    "?,?,?,?,?,?,?)"
                ),
                (
                    job,
                    fid,
                    request["base_version_id"],
                    current["version_id"],
                    job,
                    request["actor_id"],
                    request["device_id"],
                    "DELETED_DURING_UPLOAD" if current["deleted_at"] else "STALE_BASE_VERSION",
                    now,
                ),
            )
        else:
            conn.execute("UPDATE catalog_entries SET version_id=? WHERE file_id=?", (job, fid))

    def resolve(self, p, conflict_id, data):
        with self.db.transaction(True) as conn:
            conflict = conn.execute(
                "SELECT * FROM file_conflicts WHERE id=?", (conflict_id,)
            ).fetchone()
            if not conflict:
                raise HTTPException(404, "冲突不存在")
            row = self.owned(conn, conflict["file_id"], p, True)
            digest = fingerprint([conflict_id, data])
            previous = conn.execute(
                (
                    "SELECT request_hash,result_json FROM file_operation_events WHERE user_id"
                    "=? AND idempotency_key=?"
                ),
                (p.user["id"], data["idempotency_key"]),
            ).fetchone()
            if previous:
                if previous[0] != digest:
                    raise HTTPException(409, "幂等键内容不同")
                return json.loads(previous[1])
            if (
                conflict["state"] != "OPEN"
                or row["revision"] != data["expected_revision"]
                or row["deleted_at"]
            ):
                raise HTTPException(409, "冲突或文件已变化，请刷新（回收站文件需先恢复）")
            action, fid, now = data["action"], row["file_id"], time.time()
            if action == "candidate":
                conn.execute(
                    "UPDATE catalog_entries SET version_id=? WHERE file_id=?",
                    (conflict["candidate_version_id"], fid),
                )
            elif action == "save_as":
                fid = str(uuid.uuid4())
                old = conn.execute(
                    "SELECT * FROM file_versions WHERE id=?", (conflict["candidate_version_id"],)
                ).fetchone()
                vid = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO global_files VALUES (?,?,'private',?,?)",
                    (fid, p.user["id"], now, now),
                )
                conn.execute(
                    "INSERT INTO file_versions VALUES (?,?,1,?,?,?,?,?,?,?)",
                    (
                        vid,
                        fid,
                        old["object_id"],
                        old["filename"],
                        old["category"],
                        old["target_directory"],
                        old["summary"],
                        old["sha256"],
                        now,
                    ),
                )
                legacy_revision = conn.execute(
                    "SELECT revision_id FROM catalog_entries WHERE file_id=?", (row["file_id"],)
                ).fetchone()[0]
                conn.execute(
                    "INSERT INTO catalog_entries VALUES (?,?,?)", (fid, vid, legacy_revision)
                )
                conn.execute(
                    "INSERT INTO file_metadata(file_id,filename,category,directory) "
                    "VALUES (?,?,?,?)",
                    (fid, old["filename"], old["category"], row["directory"]),
                )
                conn.execute(
                    "INSERT INTO file_version_sources VALUES (?,?,?,?,?)",
                    (
                        vid,
                        p.user["id"],
                        p.session.get("device_id"),
                        conflict["base_version_id"],
                        None,
                    ),
                )
                self.emit(conn, fid, "CREATED")
            conn.execute(
                "UPDATE file_conflicts SET state='RESOLVED',resolved_at=?,resolution=? WHERE id=?",
                (now, action, conflict_id),
            )
            self.emit(conn, row["file_id"], "CONFLICT_RESOLVED")
            result = self.raw(conn, fid)
            conn.execute(
                "INSERT INTO file_operation_events VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    str(uuid.uuid4()),
                    p.user["id"],
                    row["file_id"],
                    data["idempotency_key"],
                    digest,
                    "CONFLICT_RESOLVED",
                    encoded(row),
                    encoded(result),
                    now,
                ),
            )
            return result
