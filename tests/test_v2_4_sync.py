# ruff: noqa: F811
import os
import uuid

from test_v2_2_archives import archive  # noqa: F401
from test_v2_3_library import archived, operation


def test_sent_and_applied_receipt_retry_disconnect_no_regression(archive):
    client, app, headers, _ = archive
    file = archived(archive)
    snap = client.get("/api/library/snapshot?client_id=device-one", headers=headers).json()
    with app.state.db.transaction() as conn:
        cursor = conn.execute("SELECT * FROM catalog_sync_cursors").fetchone()
        assert cursor["revision"] == 0 and cursor["sent_revision"] == snap["revision"]
    body = {"client_id": "device-one", "receipt": snap["receipt"]}
    assert (
        client.post("/api/library/sync/ack", headers=headers, json=body).json()["revision"]
        == snap["revision"]
    )
    assert client.post("/api/library/sync/ack", headers=headers, json=body).is_success
    client.patch(
        "/api/library/" + file["file_id"], headers=headers, json=operation(file, filename="new.txt")
    )
    path = f"/api/library/changes?client_id=device-one&after={snap['revision']}&acl={snap['acl']}"
    changes = client.get(path, headers=headers).json()
    again = client.get(path, headers=headers).json()
    assert again["items"] == changes["items"]
    with app.state.db.transaction() as conn:
        assert (
            conn.execute("SELECT revision FROM catalog_sync_cursors").fetchone()[0]
            == snap["revision"]
        )
    assert (
        client.post(
            "/api/library/sync/ack", headers=headers, json={**body, "receipt": changes["receipt"]}
        ).json()["revision"]
        == changes["revision"]
    )
    assert (
        client.post("/api/library/sync/ack", headers=headers, json=body).json()["revision"]
        == changes["revision"]
    )
    assert (
        client.post(
            "/api/library/sync/ack", headers=headers, json={**body, "client_id": "other"}
        ).status_code
        == 409
    )


def test_device_identity_isolated_and_revoked(archive):
    client, app, headers, _ = archive
    archived(archive)
    devices = [
        client.post(
            "/api/devices", headers=headers, json={"name": str(uuid.uuid4()), "platform": "windows"}
        ).json()
        for _ in range(2)
    ]
    for d in devices:
        h = {"Authorization": "Bearer " + d["device_token"]}
        snap = client.get("/api/library/snapshot?client_id=ignored", headers=h).json()
        assert snap["client_id"] == "device:" + d["device"]["id"]
        assert client.post(
            "/api/library/sync/ack",
            headers=h,
            json={"client_id": "ignored", "receipt": snap["receipt"]},
        ).is_success
    with app.state.db.transaction() as conn:
        assert (
            conn.execute("SELECT count(DISTINCT client_id) FROM catalog_sync_cursors").fetchone()[0]
            == 2
        )
    assert client.delete("/api/devices/" + devices[0]["device"]["id"], headers=headers).is_success
    assert (
        client.get(
            "/api/library/snapshot",
            headers={"Authorization": "Bearer " + devices[0]["device_token"]},
        ).status_code
        == 401
    )


def test_installation_rebind_login_and_explicit_revocation(archive):
    from test_server import bearer, login, register

    client, app, headers, _ = archive
    installation = str(uuid.uuid4())
    body = {"name": "Windows A", "platform": "windows", "installation_id": installation}
    first = client.post("/api/devices", headers=headers, json=body).json()
    again = client.post(
        "/api/devices", headers=bearer(login(client)), json={**body, "name": "Renamed Windows A"}
    ).json()
    assert first["device"]["id"] == again["device"]["id"]
    register(client, "second_account")
    separate = client.post(
        "/api/devices", headers=bearer(login(client, "second_account")), json=body
    ).json()
    assert separate["device"]["id"] != first["device"]["id"]
    assert client.delete("/api/devices/" + first["device"]["id"], headers=headers).is_success
    assert (
        client.get(
            "/api/library/snapshot", headers={"Authorization": "Bearer " + again["device_token"]}
        ).status_code
        == 401
    )
    rebound = client.post("/api/devices", headers=headers, json=body).json()
    assert rebound["device"]["id"] != first["device"]["id"]
    assert client.get(
        "/api/library/snapshot", headers={"Authorization": "Bearer " + rebound["device_token"]}
    ).is_success


def test_download_reuses_verified_object_and_invalidates_on_mutation(archive, monkeypatch):
    client, app, headers, _ = archive
    file = archived(archive)
    from filemind_server import downloads

    original = downloads.hashlib.sha256
    calls = []

    def count(*args, **kwargs):
        if not args:
            calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(downloads.hashlib, "sha256", count)
    assert (
        client.get("/api/library/" + file["version_id"] + "/content", headers=headers).content
        == b"Python version one"
    )
    assert not calls
    path = app.state.archives.storage.path(file["object_key"])
    path.write_bytes(b"X" * path.stat().st_size)
    os.utime(path, None)
    assert (
        client.get("/api/library/" + file["version_id"] + "/content", headers=headers).status_code
        == 409
    )
    assert calls
