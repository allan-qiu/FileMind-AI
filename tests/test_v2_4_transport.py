# ruff: noqa: F811
import asyncio
import hashlib
import multiprocessing
import sqlite3
import time
import uuid
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor

import httpx
import pytest
from test_devicelink_http import real_server  # noqa: F401
from test_server import PASSWORD, bearer, login
from test_v2_2_archives import archive  # noqa: F401
from test_v2_4_shares import access, count, reservation, share, temporary

from filemind.services.cloud import CloudClient
from filemind.services.share import ShareClient
from filemind_server.database import CentralDatabase
from filemind_server.security import new_token
from filemind_server.share_downloads import ShareDownloadService
from filemind_server.shares import ShareManager


def process_reserve(path, grant):
    db = CentralDatabase(__import__("pathlib").Path(path))
    service = ShareDownloadService(ShareManager(db, None), None)
    try:
        return service.reserve(grant, str(uuid.uuid4()), new_token())["session_id"]
    except __import__("fastapi").HTTPException as exc:
        return exc.status_code


def test_real_multiple_processes_last_slot_and_database_lock_wait(archive):
    c, app, h, _ = archive
    link = share(archive, oid=temporary(archive), maximum=1)
    grant = access(c, link)["grant"]
    with ProcessPoolExecutor(
        max_workers=4, mp_context=multiprocessing.get_context("spawn")
    ) as pool:
        jobs = [pool.submit(process_reserve, str(app.state.db.path), grant) for _ in range(10)]
        results = [j.result(timeout=30) for j in jobs]
    assert sum(isinstance(r, str) for r in results) == 1
    assert count(archive, link["share_id"]) == (0, 1)
    second = share(archive, oid=link["temporary_object_id"], maximum=1)
    grant = access(c, second)["grant"]
    with sqlite3.connect(app.state.db.path) as blocker, ThreadPoolExecutor(max_workers=1) as pool:
        blocker.execute("BEGIN IMMEDIATE")
        future = pool.submit(reservation, c, grant)
        time.sleep(0.15)
        assert not future.done()
        blocker.commit()
        r, _ = future.result(timeout=8)
        assert r.status_code == 201, r.text
    assert count(archive, second["share_id"]) == (0, 1)


def test_asgi_interrupted_prefix_resumes_rejects_parallel_and_revocation(archive):
    c, app, h, _ = archive
    data = b"0123456789abcdef" * 65536
    link = share(archive, oid=temporary(archive, data), maximum=1)
    r, dh = reservation(c, access(c, link)["grant"])
    sid, secret = r.json()["session_id"], dh["Authorization"][7:]
    response = app.state.share_downloads.prepare(sid, secret, None)
    assert c.get("/api/public/downloads/" + sid + "/content", headers=dh).status_code == 409

    async def receive():
        await asyncio.Event().wait()

    chunks = []

    async def send(message):
        if message["type"] == "http.response.body" and message.get("body"):
            if chunks:
                raise OSError("synthetic socket disconnected")
            chunks.append(message["body"])

    with pytest.raises(OSError, match="socket disconnected"):
        asyncio.run(response({}, receive, send))
    state = c.get("/api/public/downloads/" + sid, headers=dh).json()
    assert state["status"] == "RESERVED" and state["transferred_bytes"] == 262144
    assert count(archive, link["share_id"]) == (0, 1)
    remaining = c.get(
        "/api/public/downloads/" + sid + "/content", headers={**dh, "Range": "bytes=262144-"}
    )
    assert b"".join(chunks) + remaining.content == data
    assert count(archive, link["share_id"]) == (1, 0)
    second = share(archive, oid=link["temporary_object_id"])
    r, dh = reservation(c, access(c, second)["grant"])
    sid, secret = r.json()["session_id"], dh["Authorization"][7:]
    response = app.state.share_downloads.prepare(sid, secret, None)
    chunks = []

    async def revoke_after_first(message):
        if message["type"] == "http.response.body" and message.get("body"):
            chunks.append(message["body"])
            c.post("/api/shares/" + second["share_id"] + "/revoke", headers=h)

    with pytest.raises(__import__("fastapi").HTTPException):
        asyncio.run(response({}, receive, revoke_after_first))
    assert len(chunks) == 1
    assert count(archive, second["share_id"]) == (0, 0)


def test_upload_retry_hash_expiration_cleaner_never_deletes_library(archive):
    c, app, h, _ = archive
    from test_v2_3_library import archived

    file = archived(archive)
    official = app.state.archives.storage.path(file["object_key"])
    data = b"abcd"
    body = {"filename": "x.bin", "size_bytes": 4, "sha256": hashlib.sha256(data).hexdigest()}
    up = c.post("/api/shares/temporary/uploads", headers=h, json=body).json()
    path = "/api/shares/temporary/uploads/" + up["upload_id"]
    assert c.put(path + "/chunks/0", headers=h, content=b"ab").json()["offset"] == 2
    assert c.put(path + "/chunks/0", headers=h, content=b"ab").json()["offset"] == 2
    assert c.put(path + "/chunks/0", headers=h, content=b"XX").status_code == 409
    assert c.post(path + "/complete", headers=h).status_code == 409
    with app.state.db.transaction(True) as conn:
        conn.execute(
            "UPDATE share_uploads SET expires_at=? WHERE upload_id=?",
            (time.time() - 1, up["upload_id"]),
        )
    assert c.put(path + "/chunks/2", headers=h, content=b"cd").status_code == 410
    app.state.share_expiry.sweep()
    assert official.is_file()
    with app.state.db.transaction() as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()


def test_real_tcp_stream_hash_native_form_and_disconnect(real_server, tmp_path):
    url, app = real_server
    account = CloudClient(url)
    account.request(
        "POST",
        "/auth/register",
        {"username": "share_tcp", "display_name": "TCP", "password": PASSWORD},
    )
    account.token = account.login("share_tcp", PASSWORD)["access_token"]
    source = tmp_path / "large.bin"
    # 8 MiB real file; browser tests cover native downloads separately.
    source.write_bytes(b"FileMind real TCP bytes\n" * (8 * 1024 * 1024 // 23))
    owner = ShareClient(url, account.token)
    obj = owner.upload(source)
    link = owner.create({"temporary_object_id": obj["object_id"]}, 3, 3600)
    with httpx.Client(base_url=url, trust_env=False, timeout=30) as c:
        info = access(c, link)
        r, dh = reservation(c, info["grant"])
        sid = r.json()["session_id"]
        began = time.perf_counter()
        with c.stream("GET", "/api/public/downloads/" + sid + "/content", headers=dh) as response:
            first = next(response.iter_bytes(16384))
            assert first
            ttfb = time.perf_counter() - began
        assert ttfb < 5
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            state = c.get("/api/public/downloads/" + sid, headers=dh).json()
            if state["status"] != "TRANSFERRING":
                break
            time.sleep(0.05)
        assert state["status"] in ("RESERVED", "COMPLETED"), state
        if state["status"] == "RESERVED":
            assert state["transferred_bytes"] < source.stat().st_size
            assert c.post("/api/public/downloads/" + sid + "/cancel", headers=dh).is_success
        r, dh = reservation(c, info["grant"])
        sid = r.json()["session_id"]
        response = c.post(
            "/api/public/downloads/" + sid + "/content",
            data={"session_secret": dh["Authorization"][7:]},
            headers={"Origin": "null"},
        )
        assert response.is_success, response.text[:80]
        assert (
            hashlib.sha256(response.content).hexdigest()
            == hashlib.sha256(source.read_bytes()).hexdigest()
        )
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            state = c.get("/api/public/downloads/" + sid, headers=dh).json()
            if state["status"] == "COMPLETED":
                break
            time.sleep(0.05)
        assert state["status"] == "COMPLETED"
        assert c.get("/api/public/downloads/" + sid + "/content", headers=dh).status_code == 410
        assert (
            c.post(
                "/api/shares",
                headers={"Origin": "null", "Authorization": "Bearer " + account.token},
                json={"temporary_object_id": obj["object_id"]},
            ).status_code
            == 403
        )


def test_admin_real_statistics_no_content_grant_and_settings(archive):
    c, app, h, user = archive
    oid = temporary(archive)
    link = share(archive, oid=oid)
    from filemind_server.accounts import create_account
    from filemind_server.models import Registration

    with app.state.db.transaction(True) as conn:
        create_account(
            conn,
            Registration(username="admin_share", password=PASSWORD, display_name="Admin"),
            "system_admin",
        )
    admin = bearer(login(c, "admin_share", True))
    assert c.get("/api/admin/share-statistics", headers=admin).json()["active"] == 1
    settings = c.get("/api/admin/shares/settings", headers=admin).json()
    settings["max_downloads"] = 2
    settings["default_downloads"] = 2
    assert c.put("/api/admin/shares/settings", headers=admin, json=settings).is_success
    assert (
        c.post(
            "/api/shares", headers=h, json={"temporary_object_id": oid, "max_downloads": 3}
        ).status_code
        == 422
    )
    listed = c.get("/api/admin/shares", headers=admin).json()[0]
    assert (
        "token_hash" not in listed and "password_hash" not in listed and "share_url" not in listed
    )
    assert c.get("/api/shares/" + link["share_id"], headers=admin).status_code == 403
    assert c.post("/api/admin/shares/" + link["share_id"] + "/revoke", headers=admin).is_success
    assert c.get("/api/admin/share-statistics", headers=admin).json()["revoked"] == 1
