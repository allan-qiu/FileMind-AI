import hashlib
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from test_server import bearer, login, register

from filemind_server.ai import AISettings
from filemind_server.ai_control import save_config
from filemind_server.app import create_app
from filemind_server.archives import ArchiveSettings
from filemind_server.config import Settings
from filemind_server.storage import BaiduNetdiskProvider, LocalStorageProvider


@pytest.fixture
def archive(tmp_path):
    app = create_app(Settings(data_dir=tmp_path / "central", archive_worker_enabled=False))
    with TestClient(app, base_url="http://127.0.0.1") as client:
        user = register(client)
        headers = bearer(login(client))
        save_config(app.state.db, "ai_v2_2", AISettings(provider="mock"))
        yield client, app, headers, user


def create_upload(
    archive,
    payload=b"Python synthetic notes",
    filename="notes.txt",
    key="synthetic-upload-key",
    finish=True,
):
    client, app, headers, _ = archive
    response = client.post(
        "/api/archive/jobs",
        headers=headers,
        json={
            "filename": filename,
            "size_bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "idempotency_key": key,
        },
    )
    assert response.status_code == 201, response.text
    job = response.json()
    lease = client.post(f"/api/archive/jobs/{job['id']}/claim", headers=headers)
    assert lease.is_success, lease.text
    upload_headers = {
        **headers,
        "X-Archive-Lease": lease.json()["lease_token"],
        "Content-Type": "application/octet-stream",
    }
    if payload:
        response = client.put(
            f"/api/archive/jobs/{job['id']}/chunks/0", headers=upload_headers, content=payload
        )
        assert response.is_success, response.text
    if finish:
        response = client.post(f"/api/archive/jobs/{job['id']}/finish", headers=upload_headers)
        assert response.is_success, response.text
    return job, upload_headers


def consent(archive, mode="metadata"):
    client, _, headers, _ = archive
    assert client.put(
        "/api/archive/preferences",
        headers=headers,
        json={"auto_archive": True, "ai_mode": mode, "rules": ""},
    ).is_success


@pytest.mark.parametrize(
    "payload,filename",
    [
        (b"Python notes", "Python.txt"),
        (b"Python notes", "untitled.txt"),
        (b"Python notes", "invoice.txt"),
        ("Python 编程 notes".encode(), "mixed.md"),
        (b"unextractable", "image.png"),
        (b"", "empty.txt"),
    ],
)
def test_real_storage_pipeline_and_manual_fallback(archive, payload, filename):
    client, app, headers, _ = archive
    consent(archive, "content")
    job, _ = create_upload(archive, payload, filename)
    tid = job["id"]
    assert app.state.archives.process_one(tid)
    row = client.get(f"/api/archive/jobs/{tid}", headers=headers).json()
    if row["state"] == "PLAN_REVIEW_REQUIRED":
        plan = {**row["plan"], "needs_user_review": False}
        assert client.put(f"/api/archive/jobs/{tid}/plan", json=plan, headers=headers).is_success
        app.state.archives.process_one(tid)
    row = client.get(f"/api/archive/jobs/{tid}", headers=headers).json()
    assert row["state"] == "COMPLETED", row
    catalog = client.get("/api/library", headers=headers).json()
    assert len(catalog) == 1 and catalog[0]["filename"] == filename and catalog[0]["version"] == 1
    assert client.get(f"/api/library/{tid}/content", headers=headers).content == payload
    assert (
        app.state.archives.storage.metadata(row["storage_key"])["sha256"]
        == hashlib.sha256(payload).hexdigest()
    )
    assert not app.state.archives.temp_path(tid).exists()
    with app.state.db.transaction() as conn:
        events = [
            r[0]
            for r in conn.execute(
                "SELECT state FROM archive_events WHERE job_id=? ORDER BY id", (tid,)
            )
        ]
        assert (
            conn.execute("SELECT state FROM ingest_sessions WHERE job_id=?", (tid,)).fetchone()[0]
            == "CLEANED"
        )
    assert (
        events.index("AI_PLANNING")
        < events.index("ARCHIVING")
        < events.index("VERIFYING_STORAGE")
        < events.index("COMMITTING_METADATA")
        < events.index("COMPLETED")
    )


def test_no_consent_does_not_call_ai_or_starve_other_jobs(archive):
    client, app, headers, user = archive
    job, _ = create_upload(archive)
    app.state.archives.process_one(job["id"])
    assert app.state.archives.row(job["id"])["state"] == "AWAITING_AI_CONSENT"
    assert not app.state.archives.process_one()
    with app.state.db.transaction() as conn:
        assert conn.execute("SELECT count(*) FROM ai_usage_records").fetchone()[0] == 0
    consent(archive)
    assert app.state.archives.process_one()
    assert app.state.archives.row(job["id"])["state"] == "COMPLETED"


def test_upload_resume_duplicate_chunks_and_cancel_cleanup(archive):
    client, app, headers, _ = archive
    job, upload_headers = create_upload(archive, finish=False)
    tid = job["id"]
    assert client.put(
        f"/api/archive/jobs/{tid}/chunks/0",
        headers=upload_headers,
        content=b"Python synthetic notes",
    ).json()["duplicate"]
    assert (
        client.put(
            f"/api/archive/jobs/{tid}/chunks/0", headers=upload_headers, content=b"wrong"
        ).status_code
        == 409
    )
    with app.state.db.transaction(True) as conn:
        conn.execute("UPDATE archive_jobs SET lease_expires_at=0 WHERE id=?", (tid,))
    claim = client.post(f"/api/archive/jobs/{tid}/claim", headers=headers).json()
    assert claim["offset"] == len(b"Python synthetic notes")
    assert claim["lease_token"] != upload_headers["X-Archive-Lease"]
    assert client.post(f"/api/archive/jobs/{tid}/finish", headers=upload_headers).status_code == 409
    assert client.post(f"/api/archive/jobs/{tid}/cancel", headers=headers).is_success
    assert not app.state.archives.temp_path(tid).exists()
    assert not app.state.archives.storage.list()


def test_cache_full_and_no_cross_user_access(archive):
    client, app, headers, _ = archive
    save_config(app.state.db, "archive_v2_2", ArchiveSettings(cache_bytes=1))
    data = {
        "filename": "test.txt",
        "size_bytes": 2,
        "sha256": "a" * 64,
        "idempotency_key": "cache-full-key",
    }
    job = client.post("/api/archive/jobs", headers=headers, json=data).json()
    assert client.post(f"/api/archive/jobs/{job['id']}/claim", headers=headers).status_code == 507
    register(client, "other_test")
    other = bearer(login(client, "other_test"))
    assert client.get(f"/api/archive/jobs/{job['id']}", headers=other).status_code == 404
    assert client.post(f"/api/archive/jobs/{job['id']}/claim", headers=other).status_code == 404
    assert client.get("/api/archive/jobs", headers=other).json() == []
    assert client.get("/api/admin/archive/jobs", headers=headers).status_code == 403


def test_formal_object_survives_database_failure_and_retry_no_reupload(archive, monkeypatch):
    client, app, headers, _ = archive
    consent(archive)
    job, _ = create_upload(archive)
    tid, service = job["id"], app.state.archives
    calls = []
    original_upload = service.storage.upload

    def upload(*args):
        calls.append(1)
        return original_upload(*args)

    monkeypatch.setattr(service.storage, "upload", upload)
    service.before_metadata_commit = lambda: (_ for _ in ()).throw(
        RuntimeError("synthetic DB failure")
    )
    service.process_one(tid)
    assert service.row(tid)["state"] == "RETRY_WAIT"
    assert client.get("/api/library", headers=headers).json() == []
    assert len(service.storage.list()) == 1
    service.before_metadata_commit = None
    assert client.post(f"/api/archive/jobs/{tid}/retry", headers=headers).is_success
    service.process_one(tid)
    assert service.row(tid)["state"] == "COMPLETED" and len(calls) == 1
    with app.state.db.transaction() as conn:
        assert conn.execute("SELECT count(*) FROM ai_usage_records").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM file_versions").fetchone()[0] == 1
    assert service.reconcile()["failures"] == []


def test_worker_claim_fencing_and_restart_after_plan(archive):
    client, app, headers, _ = archive
    consent(archive)
    job, _ = create_upload(archive)
    service, tid = app.state.archives, job["id"]
    claimed = service.claim_work(tid)
    assert claimed and not service.claim_work(tid)
    with app.state.db.transaction(True) as conn:
        conn.execute("UPDATE archive_jobs SET worker_expires_at=0 WHERE id=?", (tid,))
    with pytest.raises(RuntimeError, match="LEASE_LOST"):
        service.set_state(tid, claimed[1], "ARCHIVING")
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: service.process_one(tid), range(2)))
    assert sorted(outcomes) == [False, True]
    assert service.row(tid)["state"] == "COMPLETED"


def test_storage_failure_missing_stage_expiry_and_reconcile(archive, monkeypatch):
    client, app, headers, _ = archive
    consent(archive)
    job, _ = create_upload(archive)
    service, tid = app.state.archives, job["id"]
    with monkeypatch.context() as m:
        m.setattr(service.storage, "upload", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
        service.process_one(tid)
    assert service.row(tid)["state"] == "RETRY_WAIT"
    assert client.get("/api/library", headers=headers).json() == []
    service.retry(tid)
    service.process_one(tid)
    key = service.row(tid)["storage_key"]
    assert service.storage.exists(key)
    service.sweep()
    assert service.storage.exists(key)  # cleanup only operates on own staging namespace
    service.storage.delete(key)
    assert service.reconcile()["failures"][0]["reason"] == "MISSING_OR_UNREADABLE"
    second, _ = create_upload(archive, key="second-upload-key")
    service.temp_path(second["id"]).unlink()
    service.process_one(second["id"])
    assert service.row(second["id"])["state"] == "RETRY_WAIT"
    with app.state.db.transaction(True) as conn:
        conn.execute("UPDATE archive_jobs SET expires_at=0 WHERE id=?", (second["id"],))
    service.sweep()
    assert service.row(second["id"])["state"] == "EXPIRED"


def test_storage_real_multipart_and_failclosed_baidu(tmp_path):
    storage = LocalStorageProvider(tmp_path / "objects")
    identifier = storage.begin_upload()
    storage.upload_part(identifier, 0, b"abc")
    with pytest.raises(ValueError):
        storage.upload_part(identifier, 0, b"abc")
    meta = storage.complete_upload(
        identifier, "学习/notes.txt", 3, hashlib.sha256(b"abc").hexdigest()
    )
    assert meta["verification_level"] == "full_sha256_readback"
    storage.download("学习/notes.txt", tmp_path / "downloaded.txt")
    assert (tmp_path / "downloaded.txt").read_bytes() == b"abc"
    with pytest.raises(ValueError):
        storage.path("../escape")
    with pytest.raises(NotImplementedError):
        BaiduNetdiskProvider().upload(tmp_path / "downloaded.txt", "remote")
