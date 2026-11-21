"Session-only SQLite metadata cache and bounded, verified on-demand disk downloads."

import hashlib
import json
import os
import sqlite3
import threading
import uuid
from pathlib import Path
from urllib.parse import urlencode

import httpx

from filemind.infrastructure.file_analysis import check_cancel, content_hash, reject_links
from filemind.services.archive import ArchiveClient
from filemind.services.cloud import CloudClient, CloudError


class CatalogCache:
    "No credentials or bytes, no disk persistence; offline shared-space data is hidden."

    def __init__(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.execute("CREATE TABLE entries(id TEXT PRIMARY KEY,revision INTEGER,payload TEXT)")
        self.lock = threading.RLock()
        self.revision, self.acl, self.account = 0, "", ""

    def clear(self):
        with self.lock, self.conn:
            self.conn.execute("DELETE FROM entries")
            self.revision, self.acl, self.account = 0, "", ""

    def apply(self, items, revision, acl, snapshot=False):
        with self.lock, self.conn:
            if snapshot:
                self.conn.execute("DELETE FROM entries")
            for event in items:
                if event.get("kind") == "REMOVED":
                    self.conn.execute("DELETE FROM entries WHERE id=?", (event["file_id"],))
                    continue
                row = event.get("file", event)
                if "file_id" not in row:
                    continue
                self.conn.execute(
                    (
                        "INSERT INTO entries VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET revisio"
                        "n=excluded.revision,payload=excluded.payload WHERE excluded.revision>=en"
                        "tries.revision"
                    ),
                    (row["file_id"], row["revision"], json.dumps(row, ensure_ascii=False)),
                )
            self.revision, self.acl = revision, acl

    def rows(self, offline=False):
        with self.lock:
            rows = [
                json.loads(r[0])
                for r in self.conn.execute("SELECT payload FROM entries ORDER BY id")
            ]
        return [r for r in rows if not offline or not r.get("workspace_id")]


class LibraryClient:
    def __init__(self, server, token, transport=None):
        self.control = CloudClient(server, token, transport)
        self.cache = CatalogCache()
        from filemind.services.sync_identity import installation_id

        self.client_id = "windows:" + installation_id()

    def close(self):
        self.cache.clear()
        self.control.token = ""

    def request(self, method, path, body=None):
        try:
            return self.control.request(method, path, body)
        except CloudError as exc:
            if exc.status in {401, 403}:
                self.cache.clear()
            raise

    def sync(self, cancel=None):
        try:
            check_cancel(cancel)
            session = self.request("GET", "/auth/session")
            user = session["user"]
            client_id = (
                "device:" + session["device_id"] if session.get("device_id") else self.client_id
            )
            account = self.control.server + ":" + user["id"]
            if self.cache.account != account:
                self.cache.clear()
                self.cache.account = account
            collected, snapshot, offset, at = [], not bool(self.cache.acl), 0, None
            cursor, acl = self.cache.revision, self.cache.acl
            for _ in range(10000):
                check_cancel(cancel)
                query = {"client_id": client_id, "limit": 200}
                if snapshot:
                    query["offset"] = offset
                    if at is not None:
                        query["at"] = at
                    path = "/library/snapshot?" + urlencode(query)
                else:
                    query.update(after=cursor, acl=acl)
                    path = "/library/changes?" + urlencode(query)
                try:
                    page = self.request("GET", path)
                except CloudError as exc:
                    if exc.status == 409:
                        snapshot, collected, offset, at = True, [], 0, None
                        continue
                    raise
                collected.extend(page["items"])
                if snapshot and at is not None and acl != page["acl"]:
                    collected, offset, at = [], 0, None
                    continue
                cursor, acl = page["revision"], page["acl"]
                if snapshot:
                    at, offset = cursor, page["next_offset"]
                if not page["has_more"]:
                    self.cache.apply(collected, cursor, acl, snapshot)
                    if page.get("receipt"):
                        self.request(
                            "POST",
                            "/library/sync/ack",
                            {"client_id": client_id, "receipt": page["receipt"]},
                        )
                    return {"state": "SYNCED", "revision": cursor, "items": self.cache.rows()}
            raise CloudError("目录持续变化，请稍后重新同步")
        except CloudError as exc:
            if exc.status in {401, 403}:
                self.cache.clear()
                raise
            if exc.status is None and self.cache.acl:
                return {
                    "state": "OFFLINE_PRIVATE_CACHE",
                    "revision": self.cache.revision,
                    "items": self.cache.rows(offline=True),
                }
            raise

    def search(self, **query):
        return self.request(
            "GET", "/library/search?" + urlencode({k: v for k, v in query.items() if v is not None})
        )

    def detail(self, file_id):
        return self.request("GET", f"/library/{file_id}")

    def operate(self, file, changes=None, kind="PATCH"):
        path = f"/library/{file['file_id']}"
        method = kind
        if kind == "RESTORE":
            path, method = path + "/restore", "POST"
        return self.request(
            method,
            path,
            {
                "expected_revision": file["revision"],
                "idempotency_key": str(uuid.uuid4()),
                **(changes or {}),
            },
        )

    def upload_version(self, file, path, cancel=None, progress=None):
        reject_links(path.absolute())
        job = self.request(
            "POST",
            f"/library/{file['file_id']}/versions",
            {
                "filename": path.name,
                "size_bytes": path.stat().st_size,
                "sha256": content_hash(path, cancel),
                "idempotency_key": str(uuid.uuid4()),
                "base_version_id": file["version_id"],
            },
        )
        archive = ArchiveClient(self.control.server, self.control.token)
        return archive.upload(job, path, cancel, progress)

    def download(self, version, destination: Path, cancel=None, progress=None):
        reject_links(destination.absolute())
        if destination.exists():
            raise CloudError("目标文件已存在，请选择新名称")
        temporary = destination.with_name(".filemind-download-" + str(uuid.uuid4()) + ".part")
        digest, done, task = hashlib.sha256(), 0, ""
        try:
            with (
                temporary.open("xb") as target,
                httpx.Client(
                    timeout=40, trust_env=False, transport=self.control.transport
                ) as client,
            ):
                with client.stream(
                    "GET",
                    self.control.server + f"/api/library/versions/{version['id']}/content",
                    headers={"Authorization": "Bearer " + self.control.token},
                ) as response:
                    if not response.is_success:
                        if response.status_code in {401, 403}:
                            self.cache.clear()
                        raise CloudError("下载请求失败", response.status_code)
                    task = response.headers.get("x-download-task", "")
                    for block in response.iter_bytes(262144):
                        check_cancel(cancel)
                        if done + len(block) > version["size_bytes"]:
                            raise CloudError("下载内容超过声明大小")
                        target.write(block)
                        digest.update(block)
                        done += len(block)
                        if progress:
                            progress(done, version["size_bytes"])
                check_cancel(cancel)
                if done != version["size_bytes"] or digest.hexdigest() != version["sha256"]:
                    raise CloudError("下载 SHA-256 或大小不一致")
                target.flush()
                os.fsync(target.fileno())
            os.link(temporary, destination)  # never overwrite a user file
            return {
                "state": "VERIFIED_SAVED",
                "path": str(destination),
                "sha256": digest.hexdigest(),
                "task_id": task,
            }
        except (OSError, httpx.HTTPError) as exc:
            raise CloudError("下载中断或磁盘写入失败；未覆盖目标文件") from exc
        finally:
            temporary.unlink(missing_ok=True)
