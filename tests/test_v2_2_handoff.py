import hashlib
from concurrent.futures import ThreadPoolExecutor

from test_transfers import create, relay  # noqa: F401

from filemind_server.ai import AISettings
from filemind_server.ai_control import save_config
from filemind_server.archives import ArchiveSettings


def test_transfer_handoff_owner_backup_and_single_archive(relay):  # noqa: F811
    client, app, devices, account = relay
    sender, receiver = devices[:2]
    client.put(
        "/api/archive/preferences", headers=account, json={"auto_archive": True, "ai_mode": "off"}
    )
    data = b"synthetic source"
    transfer, body = create(client, sender, receiver, data)
    tid = transfer["id"]
    assert client.get("/api/archive/jobs", headers=account).json() == []
    client.post(f"/api/transfers/{tid}/accept", headers=receiver["headers"])
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            client.post, f"/api/transfers/{tid}/chunks/0", content=data, headers=sender["headers"]
        )
        assert (
            client.get(f"/api/transfers/{tid}/chunks/0", headers=receiver["headers"]).content
            == data
        )
        assert future.result(5).is_success
    assert client.post(
        f"/api/transfers/{tid}/ack",
        headers=receiver["headers"],
        json={"sha256": body["sha256"], "confirmation": "written_file"},
    ).is_success
    jobs = client.get("/api/archive/jobs", headers=account).json()
    assert len(jobs) == 1
    job, s = jobs[0], app.state.archives
    assert (
        job["primary_owner"] == receiver["device"]["id"]
        and job["backup_owner"] == sender["device"]["id"]
    )
    jid = job["id"]
    assert (
        client.post(f"/api/archive/jobs/{jid}/claim", headers=sender["headers"]).status_code == 409
    )
    primary = client.post(f"/api/archive/jobs/{jid}/claim", headers=receiver["headers"])
    assert primary.is_success
    assert (
        client.post(f"/api/archive/jobs/{jid}/claim", headers=devices[2]["headers"]).status_code
        == 403
    )
    save_config(app.state.db, "archive_v2_2", ArchiveSettings(backup_grace_seconds=0))
    assert (
        client.post(f"/api/archive/jobs/{jid}/claim", headers=sender["headers"]).status_code == 409
    )
    with app.state.db.transaction(True) as conn:
        conn.execute("UPDATE archive_jobs SET lease_expires_at=0 WHERE id=?", (jid,))
    backup = client.post(f"/api/archive/jobs/{jid}/claim", headers=sender["headers"]).json()
    upload_headers = {**sender["headers"], "X-Archive-Lease": backup["lease_token"]}
    assert client.put(
        f"/api/archive/jobs/{jid}/chunks/0", headers=upload_headers, content=data
    ).is_success
    assert client.post(f"/api/archive/jobs/{jid}/finish", headers=upload_headers).is_success
    # Handoff remains independently COMPLETED even with AI consent absent.
    s.process_one(jid)
    assert s.row(jid)["state"] == "AWAITING_AI_CONSENT"
    assert app.state.transfers.row(tid)["state"] == "COMPLETED"
    assert client.put(
        "/api/archive/preferences",
        headers=account,
        json={"ai_mode": "metadata", "auto_archive": True},
    ).is_success
    save_config(app.state.db, "ai_v2_2", AISettings(provider="mock"))
    # Background worker may also acquire this job; protocol is fenced either way.
    s.process_one(jid)
    import time

    deadline = time.monotonic() + 5
    while s.row(jid)["state"] != "COMPLETED" and time.monotonic() < deadline:
        time.sleep(0.02)
    assert s.row(jid)["state"] == "COMPLETED"
    files = client.get("/api/library", headers=account).json()
    assert len(files) == 1 and files[0]["sha256"] == hashlib.sha256(data).hexdigest()
    assert len(client.get("/api/archive/jobs", headers=account).json()) == 1
