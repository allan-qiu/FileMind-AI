import hashlib
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_server import bearer, login, register
from test_v2_2_archives import archive, consent, create_upload  # noqa: F401


def archived(fixture, payload=b"Python version one", key="library-initial-upload"):
    consent(fixture)
    job, _ = create_upload(fixture, payload=payload, key=key)
    fixture[1].state.archives.process_one(job["id"])
    assert fixture[1].state.archives.row(job["id"])["state"] == "COMPLETED"
    return fixture[0].get(f"/api/library/{job['id']}", headers=fixture[2]).json()


def operation(file, **changes):
    return {"expected_revision": file["revision"], "idempotency_key": str(uuid.uuid4()), **changes}


def version(fixture, file, payload, base=None, process=True):
    client, app, headers, _ = fixture
    response = client.post(
        f"/api/library/{file['file_id']}/versions",
        headers=headers,
        json={
            "filename": "Python-notes.txt",
            "size_bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "idempotency_key": str(uuid.uuid4()),
            "base_version_id": base or file["version_id"],
        },
    )
    assert response.status_code == 201, response.text
    job = response.json()
    lease = client.post(f"/api/archive/jobs/{job['id']}/claim", headers=headers).json()
    upload = {**headers, "X-Archive-Lease": lease["lease_token"]}
    for offset in range(0, len(payload), lease["chunk_bytes"]):
        assert client.put(
            f"/api/archive/jobs/{job['id']}/chunks/{offset}",
            headers=upload,
            content=payload[offset : offset + lease["chunk_bytes"]],
        ).is_success
    assert client.post(f"/api/archive/jobs/{job['id']}/finish", headers=upload).is_success
    if process:
        app.state.archives.process_one(job["id"])
        assert app.state.archives.row(job["id"])["state"] == "COMPLETED", app.state.archives.row(
            job["id"]
        )
    return client.get(f"/api/library/{file['file_id']}", headers=headers).json(), job


def test_versions_conflict_preservation_resolution_and_range(archive):  # noqa: F811
    client, app, headers, _ = archive
    first = archived(archive)
    second, job2 = version(archive, first, b"Python version two")
    third, job3 = version(archive, second, b"Python version three")
    assert third["version"] == 3 and len(third["versions"]) == 3
    page = client.get(
        f"/api/library/{first['file_id']}/versions?limit=1&offset=1", headers=headers
    ).json()
    assert len(page) == 1 and page[0]["version"] == 2
    for vid, data in [
        (first["version_id"], b"Python version one"),
        (job2["id"], b"Python version two"),
        (job3["id"], b"Python version three"),
    ]:
        res = client.get(f"/api/library/versions/{vid}/content", headers=headers)
        assert res.content == data
        assert res.headers["x-content-sha256"] == hashlib.sha256(data).hexdigest()
        partial = client.get(
            f"/api/library/{vid}/content", headers={**headers, "Range": "bytes=2-7"}
        )
        assert partial.status_code == 206 and partial.content == data[2:8]
    conflict_file, candidate = version(
        archive, third, b"Python conflicting branch", first["version_id"]
    )
    assert conflict_file["version_id"] == job3["id"]
    conflicts = client.get("/api/library/conflicts", headers=headers).json()
    assert conflicts[0]["candidate_version_id"] == candidate["id"]
    body = operation(conflict_file, action="candidate")
    res = client.post(
        f"/api/library/conflicts/{candidate['id']}/resolve", headers=headers, json=body
    )
    assert res.is_success, res.text
    assert res.json()["version_id"] == candidate["id"]
    assert (
        client.post(
            f"/api/library/conflicts/{candidate['id']}/resolve", headers=headers, json=body
        ).json()
        == res.json()
    )
    assert len(client.get(f"/api/library/{first['file_id']}/versions", headers=headers).json()) == 4


def test_sync_search_operations_tombstones_and_idempotency(archive):  # noqa: F811
    client, app, headers, _ = archive
    file = archived(archive)
    snap = client.get("/api/library/snapshot", headers=headers).json()
    assert snap["items"][0]["file_id"] == file["file_id"]
    body = operation(
        file, filename="中文笔记.txt", directory="学习/笔记", category="学习资料", favorite=True
    )
    rename = client.patch(f"/api/library/{file['file_id']}", headers=headers, json=body)
    assert rename.is_success, rename.text
    file = rename.json()
    assert (
        client.patch(f"/api/library/{file['file_id']}", headers=headers, json=body).json() == file
    )
    assert (
        client.patch(
            f"/api/library/{file['file_id']}",
            headers=headers,
            json={**body, "filename": "changed.txt"},
        ).status_code
        == 409
    )
    result = client.get(
        (
            "/api/library/search?q=中文&extension=txt&category=学习&min_size=1&max_size=1"
            "00&sort=name&favorite=true&limit=1"
        ),
        headers=headers,
    ).json()
    assert result["total"] == 1 and result["items"][0]["directory"] == "学习/笔记"
    assert client.get("/api/library/search?q=absent", headers=headers).json()["total"] == 0
    change = client.get(
        f"/api/library/changes?after={snap['revision']}&acl={snap['acl']}", headers=headers
    ).json()
    assert change["items"][-1]["file"]["filename"] == "中文笔记.txt"
    assert (
        client.get(
            f"/api/library/changes?after={snap['revision']}&acl={snap['acl']}", headers=headers
        ).json()
        == change
    )
    deleted = client.request(
        "DELETE", f"/api/library/{file['file_id']}", headers=headers, json=operation(file)
    ).json()
    assert client.get("/api/library/search", headers=headers).json()["total"] == 0
    assert client.get("/api/library/search?trash=true", headers=headers).json()["total"] == 1
    restored = client.post(
        f"/api/library/{file['file_id']}/restore", headers=headers, json=operation(deleted)
    )
    assert restored.is_success
    assert client.get("/api/library/search", headers=headers).json()["total"] == 1
    assert (
        client.get("/api/library/changes?after=0&acl=invalid", headers=headers).status_code == 409
    )
    with ThreadPoolExecutor(max_workers=3) as pool:
        replies = list(
            pool.map(
                lambda _: client.get("/api/library/snapshot", headers=headers).json(), range(6)
            )
        )
    assert all(r["revision"] == replies[0]["revision"] for r in replies)


def test_private_isolation_workspace_and_permission_reset(archive):  # noqa: F811
    client, app, headers, _ = archive
    file = archived(archive)
    register(client, "other_test")
    other = bearer(login(client, "other_test"))
    assert client.get("/api/library/search", headers=other).json()["total"] == 0
    assert client.get(f"/api/library/{file['file_id']}", headers=other).status_code == 404
    assert (
        client.get(f"/api/library/{file['version_id']}/content", headers=other).status_code == 404
    )
    space = client.post("/api/workspaces", headers=headers, json={"name": "Synthetic team"}).json()
    assert client.post(
        f"/api/workspaces/{space['id']}/members",
        headers=headers,
        json={"username": "other_test", "permission": "viewer"},
    ).is_success
    snap = client.get("/api/library/snapshot", headers=other).json()
    file = client.patch(
        f"/api/library/{file['file_id']}",
        headers=headers,
        json=operation(file, workspace_id=space["id"]),
    ).json()
    assert (
        client.get(
            f"/api/library/changes?after={snap['revision']}&acl={snap['acl']}", headers=other
        ).status_code
        == 409
    )
    assert client.get("/api/library/search", headers=other).json()["total"] == 1
    assert (
        client.patch(
            f"/api/library/{file['file_id']}",
            headers=other,
            json=operation(file, filename="forbidden.txt"),
        ).status_code
        == 403
    )
    other_id = client.get("/api/auth/session", headers=other).json()["user"]["id"]
    assert client.delete(
        f"/api/workspaces/{space['id']}/members/{other_id}", headers=headers
    ).is_success
    assert client.get("/api/library/search", headers=other).json()["total"] == 0


def test_download_storage_failure_and_invalid_range(archive):  # noqa: F811
    client, app, headers, _ = archive
    file = archived(archive)
    for value in ["bytes=999-", "bytes=2-1", "bytes=1-2,4-5", "bytes=-0", "invalid"]:
        assert (
            client.get(
                f"/api/library/{file['version_id']}/content", headers={**headers, "Range": value}
            ).status_code
            == 416
        )
    app.state.archives.storage.path(file["object_key"]).write_bytes(b"synthetic corruption")
    res = client.get(f"/api/library/{file['version_id']}/content", headers=headers)
    assert res.status_code == 409
    assert (
        client.get(
            f"/api/library/downloads/{res.headers['x-download-task']}", headers=headers
        ).json()["state"]
        == "FAILED"
    )


def test_simultaneous_versions_keep_both_candidates_and_save_as_reuses_object(archive):  # noqa: F811
    from filemind_server.ai import AISettings
    from filemind_server.ai_control import save_config
    from filemind_server.archives import ArchiveSettings

    client, app, headers, _ = archive
    file = archived(archive)
    jobs = [
        version(archive, file, b"Python branch " + bytes([i]), process=False)[1] for i in range(2)
    ]
    save_config(app.state.db, "archive_v2_2", ArchiveSettings(concurrency=2))
    save_config(app.state.db, "ai_v2_2", AISettings(provider="mock", concurrency=2))
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert all(pool.map(lambda job: app.state.archives.process_one(job["id"]), jobs))
    conflicts = client.get("/api/library/conflicts", headers=headers).json()
    assert len(conflicts) == 1
    updated = client.get(f"/api/library/{file['file_id']}", headers=headers).json()
    assert len(updated["versions"]) == 3
    saved = client.post(
        f"/api/library/conflicts/{conflicts[0]['id']}/resolve",
        headers=headers,
        json=operation(updated, action="save_as"),
    )
    assert saved.is_success, saved.text
    assert saved.json()["file_id"] != file["file_id"]
    with app.state.db.transaction() as conn:
        assert conn.execute("SELECT count(*) FROM storage_objects").fetchone()[0] == 3
        assert conn.execute("SELECT count(*) FROM file_versions").fetchone()[0] == 4
    # Soft-deleting one reference never deletes underlying bytes used by the other.
    assert client.request(
        "DELETE",
        f"/api/library/{saved.json()['file_id']}",
        headers=headers,
        json=operation(saved.json()),
    ).is_success
    assert client.get(
        f"/api/library/{conflicts[0]['candidate_version_id']}/content", headers=headers
    ).is_success


def test_version_restore_and_conflict_after_deleted_during_upload(archive):  # noqa: F811
    client, app, headers, _ = archive
    file = archived(archive)
    current, _ = version(archive, file, b"Python new bytes")
    response = client.post(
        f"/api/library/{file['file_id']}/current-version",
        headers=headers,
        json=operation(current, version_id=file["version_id"]),
    )
    assert response.is_success, response.text
    assert response.json()["version_id"] == file["version_id"]
    _, pending = version(archive, response.json(), b"Python offline modification", process=False)
    deleted = client.request(
        "DELETE",
        f"/api/library/{file['file_id']}",
        headers=headers,
        json=operation(response.json()),
    ).json()
    app.state.archives.process_one(pending["id"])
    assert deleted["deleted_at"]
    conflict = client.get("/api/library/conflicts", headers=headers).json()[0]
    assert conflict["reason"] == "DELETED_DURING_UPLOAD"
    assert client.get(f"/api/library/{pending['id']}/content", headers=headers).status_code == 409
    assert client.post(
        f"/api/library/{file['file_id']}/restore",
        headers=headers,
        json=operation(client.get(f"/api/library/{file['file_id']}", headers=headers).json()),
    ).is_success


def test_operation_failure_rolls_back_and_expired_cursor_resets(archive):  # noqa: F811
    import sqlite3

    client, app, headers, _ = archive
    file = archived(archive)
    with app.state.db.transaction(True) as conn:
        conn.execute(
            "CREATE TRIGGER synthetic_event_failure BEFORE INSERT ON file_operation_e"
            "vents BEGIN SELECT RAISE(FAIL,'synthetic failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        client.patch(
            f"/api/library/{file['file_id']}",
            headers=headers,
            json=operation(file, filename="must-rollback.txt"),
        )
    unchanged = client.get(f"/api/library/{file['file_id']}", headers=headers).json()
    assert unchanged["filename"] == file["filename"] and unchanged["revision"] == file["revision"]
    with app.state.db.transaction(True) as conn:
        conn.execute("DROP TRIGGER synthetic_event_failure")
    # Cursor ahead of server / inconsistent paginated snapshot always resets.
    snap = client.get("/api/library/snapshot", headers=headers).json()
    assert (
        client.get(
            f"/api/library/changes?after={snap['revision'] + 1}&acl={snap['acl']}", headers=headers
        ).status_code
        == 409
    )
    assert (
        client.get(f"/api/library/snapshot?at={snap['revision'] + 1}", headers=headers).status_code
        == 409
    )
    assert client.post(
        "/api/library/folders", headers=headers, json={"path": "空目录/子目录"}
    ).is_success
    assert (
        client.get("/api/library/search?directory=空目录/子目录", headers=headers).json()["total"]
        == 0
    )
    assert client.get("/api/library/folders", headers=headers).json()["items"]


def test_large_disk_object_download_and_suffix_range(archive):  # noqa: F811
    client, app, headers, _ = archive
    file = archived(archive)
    data = b"Python large synthetic bytes\n" * 100000
    current, job = version(archive, file, data)
    with client.stream("GET", f"/api/library/{job['id']}/content", headers=headers) as response:
        digest = hashlib.sha256()
        for chunk in response.iter_bytes(262144):
            digest.update(chunk)
    assert digest.hexdigest() == current["sha256"]
    response = client.get(
        f"/api/library/{job['id']}/content", headers={**headers, "Range": "bytes=-31"}
    )
    assert response.content == data[-31:]
