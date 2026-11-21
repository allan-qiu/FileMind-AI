import pytest
from test_v2_2_archives import archive, consent, create_upload  # noqa: F401

from filemind_server.ai import AIError, AIResponse, AISettings, MockAIProvider
from filemind_server.ai_control import save_config


def test_publish_failure_recovers_without_storage_or_ai_repeat(archive, monkeypatch):  # noqa: F811
    client, app, headers, _ = archive
    consent(archive)
    job, _ = create_upload(archive)
    service, tid = app.state.archives, job["id"]
    with monkeypatch.context() as m:
        m.setattr(
            service,
            "publish",
            lambda *a: (_ for _ in ()).throw(RuntimeError("publication interrupted")),
        )
        service.process_one(tid)
    assert service.row(tid)["state"] == "RETRY_WAIT"
    assert client.get("/api/library", headers=headers).json() == []
    with app.state.db.transaction() as conn:
        assert conn.execute("SELECT count(*) FROM file_versions").fetchone()[0] == 1
        assert conn.execute("SELECT published_at FROM outbox_events").fetchone()[0] is None
    monkeypatch.setattr(service.storage, "upload", lambda *a: pytest.fail("must not upload again"))
    service.retry(tid)
    service.process_one(tid)
    assert service.row(tid)["state"] == "COMPLETED"
    assert len(client.get("/api/library", headers=headers).json()) == 1


def test_uncommitted_upload_tail_is_discarded_and_hash_checked(archive):  # noqa: F811
    client, app, headers, _ = archive
    data = b"abc123"
    job, upload_headers = create_upload(archive, payload=data, finish=False)
    tid, service = job["id"], app.state.archives
    # Model a process crash after file fsync but before SQLite ACK commit.
    with app.state.db.transaction(True) as conn:
        conn.execute("DELETE FROM ingest_chunks WHERE job_id=?", (tid,))
        conn.execute("UPDATE ingest_sessions SET offset=0 WHERE job_id=?", (tid,))
    service.temp_path(tid).write_bytes(b"uncommitted trailing garbage")
    assert client.put(
        f"/api/archive/jobs/{tid}/chunks/0", headers=upload_headers, content=data
    ).is_success
    assert service.temp_path(tid).read_bytes() == data
    assert client.post(f"/api/archive/jobs/{tid}/finish", headers=upload_headers).is_success


def test_ai_restart_unknown_does_not_repeat_charge(archive):  # noqa: F811
    client, app, headers, user = archive
    ai = app.state.ai
    ai.plan("uncertain-id", user["id"], {"filename": "notes.txt"})
    with app.state.db.transaction(True) as conn:
        conn.execute(
            "UPDATE ai_usage_records SET status='STARTED',result_json=NULL WHERE id='uncertain-id'"
        )
    ai.recover()
    with pytest.raises(AIError, match="PREVIOUS_REQUEST_REQUIRES_REVIEW"):
        ai.plan("uncertain-id", user["id"], {"filename": "notes.txt"})
    with app.state.db.transaction() as conn:
        assert conn.execute("SELECT status FROM ai_usage_records").fetchone()[0] == "UNKNOWN"
        assert conn.execute("SELECT count(*) FROM ai_usage_records").fetchone()[0] == 1


def test_budget_reservation_and_estimated_cost_with_synthetic_provider(archive):  # noqa: F811
    client, app, headers, user = archive
    ai = app.state.ai
    app.state.settings.allow_paid_ai = True  # test fixture only; no network provider is used
    ai.keys.set("synthetic-not-a-real-api-key")
    cfg = AISettings(
        provider="deepseek",
        input_per_million=1,
        output_per_million=2,
        price_version="synthetic-price-v1",
        request_limit=0.1,
        monthly_budget=0.1,
    )
    save_config(app.state.db, "ai_v2_2", cfg)
    ai.provider_override = MockAIProvider()
    ai.plan("synthetic-budget", user["id"], {"filename": "notes.txt"})
    with app.state.db.transaction() as conn:
        row = conn.execute("SELECT * FROM ai_usage_records").fetchone()
    assert row["estimated_cost"] == pytest.approx(0.00026)
    assert row["price_version"] == "synthetic-price-v1"
    cfg.monthly_budget = 0
    save_config(app.state.db, "ai_v2_2", cfg)
    with pytest.raises(AIError, match="MONTHLY_BUDGET_EXCEEDED"):
        ai.plan("blocked-budget", user["id"], {"filename": "notes.txt"})


def test_ai_invalid_result_requires_explicit_human_plan(archive):  # noqa: F811
    client, app, headers, _ = archive
    consent(archive)
    job, _ = create_upload(archive)

    class InvalidProvider:
        def complete(self, payload, settings):
            return AIResponse('{"category":"unknown"}', 100, 10, "synthetic-bad", True)

    app.state.ai.provider_override = InvalidProvider()
    app.state.archives.process_one(job["id"])
    row = app.state.archives.row(job["id"])
    assert row["state"] == "PLAN_REVIEW_REQUIRED" and not row["storage_key"]
    assert not app.state.archives.storage.list()


def test_same_names_different_bytes_and_same_bytes_different_names(archive):  # noqa: F811
    client, app, headers, _ = archive
    consent(archive)
    jobs = [
        create_upload(archive, payload=data, filename=name, key=f"identity-test-{i}")[0]
        for i, (name, data) in enumerate(
            [("same.txt", b"a"), ("same.txt", b"b"), ("renamed.txt", b"a")]
        )
    ]
    for job in jobs:
        app.state.archives.process_one(job["id"])
    items = client.get("/api/library", headers=headers).json()
    assert len(items) == 3 and len({item["object_key"] for item in items}) == 3
    assert len({item["sha256"] for item in items}) == 2


def test_actual_sqlite_insert_failure_rolls_back_metadata_and_reuses_object(archive):  # noqa: F811
    client, app, headers, _ = archive
    consent(archive)
    job, _ = create_upload(archive)
    service, tid = app.state.archives, job["id"]
    with app.state.db.transaction(True) as conn:
        conn.execute(
            "CREATE TRIGGER synthetic_insert_failure BEFORE INSERT ON storage_objects "
            "BEGIN SELECT RAISE(FAIL, 'synthetic metadata insert failure'); END"
        )
    service.process_one(tid)
    row = service.row(tid)
    assert row["state"] == "RETRY_WAIT" and "IntegrityError" in row["error"]
    assert service.storage.exists(row["storage_key"])
    with app.state.db.transaction(True) as conn:
        assert conn.execute("SELECT count(*) FROM global_files").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM storage_objects").fetchone()[0] == 0
        conn.execute("DROP TRIGGER synthetic_insert_failure")
    service.retry(tid)
    service.process_one(tid)
    assert service.row(tid)["state"] == "COMPLETED"
    assert len(client.get("/api/library", headers=headers).json()) == 1
