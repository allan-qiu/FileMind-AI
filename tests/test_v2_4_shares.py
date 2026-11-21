# ruff: noqa: F811
import hashlib
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_server import bearer, login, register
from test_v2_2_archives import archive  # noqa: F401
from test_v2_3_library import archived, operation, version

from filemind_server.security import new_token
from filemind_server.share_config import ShareSettings, save


def temporary(fixture, data=b"synthetic share content"):
    c, app, h, _ = fixture
    r = c.post(
        "/api/shares/temporary/uploads",
        headers=h,
        json={
            "filename": "共享.txt",
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        },
    )
    assert r.status_code == 201, r.text
    up = r.json()
    for offset in range(0, len(data), up["chunk_bytes"]):
        r = c.put(
            f"/api/shares/temporary/uploads/{up['upload_id']}/chunks/{offset}",
            headers=h,
            content=data[offset : offset + up["chunk_bytes"]],
        )
        assert r.is_success, r.text
    r = c.post(f"/api/shares/temporary/uploads/{up['upload_id']}/complete", headers=h)
    assert r.is_success, r.text
    return r.json()["object_id"]


def share(fixture, oid=None, vid=None, maximum=3, password=None):
    c, _, h, _ = fixture
    r = c.post(
        "/api/shares",
        headers=h,
        json={
            "temporary_object_id": oid,
            "file_version_id": vid,
            "max_downloads": maximum,
            "ttl_seconds": 3600,
            "password": password,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def access(c, link, password=None):
    r = c.post(
        "/api/public/shares/verify",
        json={"token": link["share_url"].split("#")[1], "password": password},
    )
    assert r.is_success, r.text
    return r.json()


def reservation(c, grant, key=None, secret=None):
    secret = secret or new_token()
    r = c.post(
        "/api/public/shares/downloads",
        json={"grant": grant, "request_key": key or str(uuid.uuid4()), "session_secret": secret},
    )
    return r, {"Authorization": "Bearer " + secret}


def count(fixture, sid):
    with fixture[1].state.db.transaction() as conn:
        link = dict(conn.execute("SELECT * FROM share_links WHERE share_id=?", (sid,)).fetchone())
        reserved = conn.execute(
            "SELECT count(*) FROM share_download_sessions WHERE share_id=? AND "
            "reserved_quota=1 AND reservation_expires_at>?",
            (sid, time.time()),
        ).fetchone()[0]
        assert link["completed_downloads"] + reserved <= link["max_downloads"]
        return link["completed_downloads"], reserved


def test_temporary_disk_hash_links_independence_and_revocation(archive):
    c, app, h, _ = archive
    data = b"hello temporary" * 50000
    oid = temporary(archive, data)
    obj = c.get("/api/shares/temporary/objects", headers=h).json()[0]
    path = app.state.share_temporary.storage.path(obj["storage_location"])
    assert path.read_bytes() == data
    assert obj["content_sha256"] == hashlib.sha256(data).hexdigest()
    links = [share(archive, oid=oid, maximum=n) for n in (1, 3, 20)]
    assert len({x["share_url"] for x in links}) == 3
    assert len(list((app.state.share_temporary.storage.root / "objects").iterdir())) == 1
    for link in links:
        info = access(c, link)
        assert count(archive, link["share_id"]) == (0, 0)
        r, dh = reservation(c, info["grant"])
        assert r.status_code == 201, r.text
        sid = r.json()["session_id"]
        res = c.get("/api/public/downloads/" + sid + "/content", headers=dh)
        assert res.content == data
        assert count(archive, link["share_id"]) == (1, 0)
        for _ in range(2):
            ack = c.post(
                "/api/public/downloads/" + sid + "/complete",
                headers=dh,
                json={"sha256": hashlib.sha256(data).hexdigest()},
            )
            assert ack.is_success, ack.text
        assert c.get("/api/public/downloads/" + sid + "/content", headers=dh).status_code == 410
    c.post("/api/shares/" + links[0]["share_id"] + "/revoke", headers=h)
    app.state.share_expiry.sweep()
    assert path.exists()
    assert access(c, links[1])["remaining_downloads"] == 2


def test_library_history_fixed_version_delete_restore_stays_revoked(archive):
    c, app, h, _ = archive
    first = archived(archive)
    second, _ = version(archive, first, b"Python second version")
    links = [share(archive, vid=first["version_id"], maximum=n) for n in (1, 3)]
    renamed = c.patch(
        "/api/library/" + first["file_id"],
        headers=h,
        json=operation(second, filename="renamed.txt"),
    ).json()
    for link in links:
        r, dh = reservation(c, access(c, link)["grant"])
        assert (
            c.get(
                "/api/public/downloads/" + r.json()["session_id"] + "/content", headers=dh
            ).content
            == b"Python version one"
        )
    c.post("/api/shares/" + links[0]["share_id"] + "/revoke", headers=h)
    assert access(c, links[1])["remaining_downloads"] == 2
    deleted = c.request(
        "DELETE", "/api/library/" + first["file_id"], headers=h, json=operation(renamed)
    ).json()
    c.post("/api/library/" + first["file_id"] + "/restore", headers=h, json=operation(deleted))
    assert c.get("/api/shares/" + links[1]["share_id"], headers=h).json()["status"] == "REVOKED"
    assert (
        c.get("/api/library/" + first["version_id"] + "/content", headers=h).content
        == b"Python version one"
    )


@pytest.mark.parametrize("maximum,clients", [(1, 2), (1, 10), (3, 10)])
def test_atomic_concurrent_last_slots_real_transactions(archive, maximum, clients):
    c, app, h, _ = archive
    save(app.state.db, ShareSettings(max_concurrency=8, user_concurrency=8, share_concurrency=8))
    link = share(archive, oid=temporary(archive), maximum=maximum)
    grant = access(c, link)["grant"]
    with ThreadPoolExecutor(max_workers=clients) as pool:
        results = list(pool.map(lambda _: reservation(c, grant), range(clients)))
    accepted = [(r, dh) for r, dh in results if r.status_code == 201]
    assert len(accepted) == maximum
    assert count(archive, link["share_id"]) == (0, maximum)
    for r, dh in accepted:
        assert c.get(
            "/api/public/downloads/" + r.json()["session_id"] + "/content", headers=dh
        ).is_success
    assert count(archive, link["share_id"]) == (maximum, 0)
    assert reservation(c, grant)[0].status_code == 409


def test_sequential_ranges_no_overlap_parallel_reuse_and_quota_update(archive):
    c, app, h, _ = archive
    data = b"0123456789" * 50000
    link = share(archive, oid=temporary(archive, data), maximum=3)
    grant = access(c, link)["grant"]
    key, secret = str(uuid.uuid4()), new_token()
    r, dh = reservation(c, grant, key, secret)
    sid = r.json()["session_id"]
    assert reservation(c, grant, key, secret)[0].json()["session_id"] == sid
    assert count(archive, link["share_id"]) == (0, 1)
    base = "/api/public/downloads/" + sid
    assert (
        c.get(base + "/content", headers={**dh, "Range": "bytes=0-199999"}).content == data[:200000]
    )
    assert c.get(base + "/content", headers={**dh, "Range": "bytes=0-"}).status_code == 416
    assert c.get(base + "/content", headers=dh).status_code == 416
    assert (
        c.get(base + "/content", headers={**dh, "Range": "bytes=200000-"}).content == data[200000:]
    )
    assert count(archive, link["share_id"]) == (1, 0)
    r2, dh2 = reservation(c, grant)
    assert (
        c.patch("/api/shares/" + link["share_id"], headers=h, json={"max_downloads": 1}).status_code
        == 409
    )
    assert c.post(
        "/api/public/downloads/" + r2.json()["session_id"] + "/cancel", headers=dh2
    ).is_success
    assert count(archive, link["share_id"]) == (1, 0)
    assert c.patch(
        "/api/shares/" + link["share_id"], headers=h, json={"max_downloads": 1}
    ).is_success


def test_password_rate_limit_invalid_and_no_token_in_database(archive):
    c, app, h, _ = archive
    link = share(archive, oid=temporary(archive), password="synthetic-pass")
    token = link["share_url"].split("#")[1]
    assert access(c, link)["password_required"]
    assert (
        c.post("/api/public/shares/verify", json={"token": token, "password": "wrong"}).status_code
        == 404
    )
    assert (
        c.post(
            "/api/public/shares/verify", json={"token": "invalid", "password": "wrong"}
        ).status_code
        == 404
    )
    assert access(c, link, "synthetic-pass")["grant"]
    with app.state.db.transaction() as conn:
        text = "".join(conn.iterdump())
        assert token not in text and "synthetic-pass" not in text
    for _ in range(20):
        response = c.post("/api/public/shares/verify", json={"token": token, "password": "wrong"})
    assert response.status_code == 429


def test_permissions_owner_workspace_admin_separation(archive):
    c, app, h, _ = archive
    file = archived(archive)
    oid = temporary(archive)
    link = share(archive, oid=oid)
    register(c, "other_test")
    other = bearer(login(c, "other_test"))
    for body in ({"file_version_id": file["version_id"]}, {"temporary_object_id": oid}):
        assert c.post("/api/shares", headers=other, json=body).status_code in (403, 404)
    for method, path, body in [
        ("GET", "/api/shares/" + link["share_id"], None),
        ("POST", "/api/shares/" + link["share_id"] + "/revoke", {}),
        ("PATCH", "/api/shares/" + link["share_id"], {"max_downloads": 5}),
    ]:
        assert c.request(method, path, headers=other, json=body).status_code == 404
        assert (
            c.request(
                method,
                path,
                headers={"Authorization": "Bearer " + link["share_url"].split("#")[1]},
                json=body,
            ).status_code
            == 401
        )
    assert c.get("/api/admin/share-statistics", headers=h).status_code == 403
    space = c.post("/api/workspaces", headers=h, json={"name": "share team"}).json()
    c.post(
        "/api/workspaces/" + space["id"] + "/members",
        headers=h,
        json={"username": "other_test", "permission": "viewer"},
    )
    c.patch(
        "/api/library/" + file["file_id"], headers=h, json=operation(file, workspace_id=space["id"])
    )
    assert (
        c.post(
            "/api/shares", headers=other, json={"file_version_id": file["version_id"]}
        ).status_code
        == 403
    )


def test_missing_file_releases_quota_and_expired_reservation_restart(archive):
    c, app, h, _ = archive
    oid = temporary(archive)
    link = share(archive, oid=oid, maximum=1)
    grant = access(c, link)["grant"]
    r, dh = reservation(c, grant)
    sid = r.json()["session_id"]
    with app.state.db.transaction(True) as conn:
        conn.execute(
            "UPDATE share_download_sessions SET status='TRANSFERRING' WHERE session_id=?", (sid,)
        )
    app.state.share_downloads.recover()
    assert c.get("/api/public/downloads/" + sid, headers=dh).json()["status"] == "RESERVED"
    with app.state.db.transaction(True) as conn:
        conn.execute(
            "UPDATE share_download_sessions SET reservation_expires_at=? WHERE session_id=?",
            (time.time() - 1, sid),
        )
    app.state.share_expiry.sweep()
    assert count(archive, link["share_id"]) == (0, 0)
    r, dh = reservation(c, grant)
    obj = c.get("/api/shares/temporary/objects", headers=h).json()[0]
    app.state.share_temporary.storage.delete(obj["storage_location"])
    assert (
        c.get(
            "/api/public/downloads/" + r.json()["session_id"] + "/content", headers=dh
        ).status_code
        == 409
    )
    assert count(archive, link["share_id"]) == (0, 0)


def test_cleanup_references_failed_retry_upload_cancel_expiry_and_quotas(archive, monkeypatch):
    c, app, h, _ = archive
    oid = temporary(archive)
    links = [share(archive, oid=oid) for _ in range(2)]
    obj = c.get("/api/shares/temporary/objects", headers=h).json()[0]
    path = app.state.share_temporary.storage.path(obj["storage_location"])
    with app.state.db.transaction(True) as conn:
        conn.execute(
            "UPDATE share_links SET expires_at=? WHERE share_id=?",
            (time.time() - 1, links[0]["share_id"]),
        )
    app.state.share_expiry.sweep()
    assert path.exists()
    c.post("/api/shares/" + links[1]["share_id"] + "/revoke", headers=h)
    original = app.state.share_temporary.storage.delete
    with monkeypatch.context() as m:
        m.setattr(
            app.state.share_temporary.storage,
            "delete",
            lambda _: (_ for _ in ()).throw(OSError("synthetic locked file")),
        )
        app.state.share_expiry.sweep()
        assert (
            c.get("/api/shares/temporary/objects", headers=h).json()[0]["deletion_status"]
            == "FAILED"
        )
    app.state.share_expiry.sweep()
    assert not path.exists()
    assert original
    save(app.state.db, ShareSettings(max_file_bytes=5, user_temp_bytes=5, total_temp_bytes=5))
    body = {"filename": "x", "size_bytes": 6, "sha256": "a" * 64}
    assert c.post("/api/shares/temporary/uploads", headers=h, json=body).status_code == 413
    body["size_bytes"] = 4
    up = c.post("/api/shares/temporary/uploads", headers=h, json=body).json()
    assert c.post("/api/shares/temporary/uploads", headers=h, json=body).status_code == 507
    c.put(
        "/api/shares/temporary/uploads/" + up["upload_id"] + "/chunks/0", headers=h, content=b"ab"
    )
    assert c.post(
        "/api/shares/temporary/uploads/" + up["upload_id"] + "/cancel", headers=h
    ).is_success
    app.state.share_expiry.sweep()
    with app.state.db.transaction() as conn:
        assert (
            conn.execute(
                "SELECT status FROM share_uploads WHERE upload_id=?", (up["upload_id"],)
            ).fetchone()[0]
            == "CLEANED"
        )


def test_server_enforced_disable_lifetime_and_traffic(archive):
    c, app, h, _ = archive
    oid = temporary(archive)
    link = share(archive, oid=oid)
    grant = access(c, link)["grant"]
    save(app.state.db, ShareSettings(allow_temporary=False))
    assert c.post("/api/shares", headers=h, json={"temporary_object_id": oid}).status_code == 403
    assert reservation(c, grant)[0].status_code == 403
    save(app.state.db, ShareSettings(user_daily_bytes=1))
    r, dh = reservation(c, grant)
    with pytest.raises(RuntimeError, match="response already started"):
        c.get("/api/public/downloads/" + r.json()["session_id"] + "/content", headers=dh)
    assert count(archive, link["share_id"]) == (0, 0)
    with app.state.db.transaction(True) as conn:
        conn.execute(
            "UPDATE share_links SET expires_at=? WHERE share_id=?",
            (time.time() - 1, link["share_id"]),
        )
    assert reservation(c, grant)[0].status_code == 410
    assert (
        c.post(
            "/api/public/shares/verify", json={"token": link["share_url"].split("#")[1]}
        ).status_code
        == 410
    )


@pytest.mark.parametrize("data,maximum", [(b"", 1), (b"custom quota bytes", 7)])
def test_empty_file_and_custom_exhaustion(archive, data, maximum):
    c, app, h, _ = archive
    link = share(archive, oid=temporary(archive, data), maximum=maximum)
    grant = access(c, link)["grant"]
    for n in range(maximum):
        r, dh = reservation(c, grant)
        assert r.status_code == 201
        sid = r.json()["session_id"]
        assert c.get("/api/public/downloads/" + sid + "/content", headers=dh).content == data
        assert count(archive, link["share_id"]) == (n + 1, 0)
    assert reservation(c, grant)[0].status_code == 409


def test_multiple_links_multiple_users_concurrent_independent(archive):
    c, app, h, user = archive
    save(app.state.db, ShareSettings(max_concurrency=8, user_concurrency=8, share_concurrency=8))
    oid = temporary(archive)
    a = [share(archive, oid=oid, maximum=1) for _ in range(2)]
    other = register(c, "parallel_owner")
    other_headers = bearer(login(c, "parallel_owner"))
    second = (c, app, other_headers, other)
    oid2 = temporary(second)
    b = [share(second, oid=oid2, maximum=1) for _ in range(2)]
    grants = [access(c, link)["grant"] for link in a + b]

    def download(grant):
        r, dh = reservation(c, grant)
        assert r.status_code == 201, r.text
        return c.get(
            "/api/public/downloads/" + r.json()["session_id"] + "/content", headers=dh
        ).content

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert all(data == b"synthetic share content" for data in pool.map(download, grants))
    for link in a + b:
        assert count(archive, link["share_id"]) == (1, 0)
