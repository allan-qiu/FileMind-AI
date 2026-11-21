import hashlib
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from filemind_server.app import create_app
from filemind_server.config import Settings


@pytest.fixture
def relay(tmp_path):
    app = create_app(
        Settings(
            data_dir=tmp_path / "central",
            transfer_chunk_bytes=4096,
            transfer_max_bytes=1024 * 1024,
            transfer_timeout=1,
        )
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        credentials = {"username": "relay_test", "password": "relay-test-password"}
        assert (
            client.post(
                "/api/auth/register", json={**credentials, "display_name": "Relay"}
            ).status_code
            == 201
        )
        session = client.post("/api/auth/login", json=credentials).json()
        account_headers = {"Authorization": "Bearer " + session["access_token"]}
        devices = []
        for index in range(4):
            response = client.post(
                "/api/devices",
                json={
                    "name": f"logical-device-{index}",
                    "platform": "windows" if index % 2 == 0 else "browser",
                },
                headers=account_headers,
            )
            assert response.status_code == 201, response.text
            device = response.json()
            device["headers"] = {"Authorization": "Bearer " + device["device_token"]}
            devices.append(device)
            assert client.get("/api/transfers/inbox", headers=device["headers"]).status_code == 200
        yield client, app, devices, account_headers


def create(client, sender, receiver, data=b"payload", **changes):
    body = {
        "receiver_id": receiver["device"]["id"],
        "filename": "payload.bin",
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "request_key": str(uuid.uuid4()),
        **changes,
    }
    response = client.post("/api/transfers", json=body, headers=sender["headers"])
    assert response.status_code == 201, response.text
    return response.json(), body


@pytest.mark.parametrize("size", [0, 17, 12000])
def test_transparent_bytes_and_receiver_confirmation_independent_of_archive(relay, size):
    client, app, devices, _ = relay
    sender, receiver = devices[:2]
    data = bytes(index % 251 for index in range(size))
    transfer, body = create(client, sender, receiver, data)
    tid = transfer["id"]
    assert (
        client.post(f"/api/transfers/{tid}/accept", headers=receiver["headers"]).status_code == 200
    )
    assert (
        client.post(
            f"/api/transfers/{tid}/ack",
            headers=receiver["headers"],
            json={"sha256": body["sha256"], "confirmation": "browser_received"},
        ).status_code
        == 409
    )
    received = bytearray()
    with ThreadPoolExecutor(max_workers=1) as executor:
        for seq in range(max(1, (len(data) + 4095) // 4096)):
            chunk = data[seq * 4096 : (seq + 1) * 4096]
            future = executor.submit(
                client.post,
                f"/api/transfers/{tid}/chunks/{seq}",
                content=chunk,
                headers={**sender["headers"], "Content-Type": "application/octet-stream"},
            )
            response = client.get(f"/api/transfers/{tid}/chunks/{seq}", headers=receiver["headers"])
            assert response.status_code == 200, response.text
            received.extend(response.content)
            assert future.result(timeout=5).status_code == 200
    assert received == data
    row = client.get(f"/api/transfers/{tid}", headers=sender["headers"]).json()
    assert row["state"] == "VERIFYING" and row["bytes_forwarded"] == len(data)
    ack = {"sha256": hashlib.sha256(received).hexdigest(), "confirmation": "browser_received"}
    assert (
        client.post(f"/api/transfers/{tid}/ack", headers=receiver["headers"], json=ack).json()[
            "state"
        ]
        == "COMPLETED"
    )
    assert (
        client.post(f"/api/transfers/{tid}/ack", headers=receiver["headers"], json=ack).status_code
        == 200
    )
    with app.state.db.transaction() as conn:
        handoff = conn.execute(
            "SELECT * FROM transfer_archive_handoff WHERE transfer_id=?", (tid,)
        ).fetchall()
        assert len(handoff) == 1 and handoff[0]["state"] in {"PENDING", "CREATED"}
    assert app.state.transfers.peak_buffer_bytes <= 2 * 4096
    assert app.state.transfers.buffered_bytes() == 0


def test_reject_cancel_timeout_hash_failure_and_other_transfer_survives(relay):
    client, app, devices, _ = relay
    sender, receiver = devices[:2]
    transfer, _ = create(client, sender, receiver)
    tid = transfer["id"]
    assert (
        client.post(f"/api/transfers/{tid}/reject", headers=receiver["headers"]).json()["state"]
        == "REJECTED"
    )
    assert (
        client.post(f"/api/transfers/{tid}/accept", headers=receiver["headers"]).status_code == 409
    )
    transfer, _ = create(client, sender, receiver)
    tid = transfer["id"]
    assert (
        client.post(f"/api/transfers/{tid}/cancel", headers=sender["headers"]).json()["state"]
        == "CANCELLED"
    )
    transfer, _ = create(client, sender, receiver, b"x")
    tid = transfer["id"]
    client.post(f"/api/transfers/{tid}/accept", headers=receiver["headers"])
    response = client.post(
        f"/api/transfers/{tid}/chunks/0", content=b"x", headers=sender["headers"]
    )
    assert response.status_code == 408
    assert (
        client.get(f"/api/transfers/{tid}", headers=sender["headers"]).json()["state"] == "FAILED"
    )
    other, _ = create(client, devices[2], devices[3], b"y")
    tid = other["id"]
    assert (
        client.post(f"/api/transfers/{tid}/accept", headers=devices[3]["headers"]).status_code
        == 200
    )
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            client.post,
            f"/api/transfers/{tid}/chunks/0",
            content=b"y",
            headers=devices[2]["headers"],
        )
        assert (
            client.get(f"/api/transfers/{tid}/chunks/0", headers=devices[3]["headers"]).content
            == b"y"
        )
        assert future.result(5).status_code == 200
    response = client.post(
        f"/api/transfers/{tid}/ack",
        json={"sha256": "0" * 64, "confirmation": "written_file"},
        headers=devices[3]["headers"],
    )
    assert response.status_code == 422
    assert (
        client.get(f"/api/transfers/{tid}", headers=devices[3]["headers"]).json()["state"]
        == "FAILED"
    )


def test_devices_permissions_limits_expiry_and_idempotency(relay):
    client, app, devices, account = relay
    sender, receiver = devices[:2]
    transfer, body = create(client, sender, receiver)
    tid = transfer["id"]
    assert client.post("/api/transfers", json=body, headers=sender["headers"]).json()["id"] == tid
    assert client.post("/api/transfers", json=body, headers=account).status_code == 403
    assert client.get(f"/api/transfers/{tid}", headers=devices[2]["headers"]).status_code == 404
    assert client.post(f"/api/transfers/{tid}/accept", headers=sender["headers"]).status_code == 403
    assert (
        client.post(
            "/api/transfers",
            json={**body, "size_bytes": 2 * 1024 * 1024},
            headers=sender["headers"],
        ).status_code
        == 413
    )
    client.post(f"/api/transfers/{tid}/accept", headers=receiver["headers"])
    second, _ = create(client, devices[2], devices[3])
    client.post(f"/api/transfers/{second['id']}/accept", headers=devices[3]["headers"])
    third, _ = create(client, sender, receiver)
    assert (
        client.post(f"/api/transfers/{third['id']}/accept", headers=receiver["headers"]).status_code
        == 429
    )
    with app.state.db.transaction(True) as conn:
        conn.execute(
            "UPDATE transfer_sessions SET expires_at=? WHERE id=?", (time.time() - 1, third["id"])
        )
    assert (
        client.get(f"/api/transfers/{third['id']}", headers=sender["headers"]).json()["state"]
        == "EXPIRED"
    )
    client.delete(f"/api/devices/{sender['device']['id']}", headers=account)
    assert client.post("/api/transfers", json=body, headers=sender["headers"]).status_code == 401
    assert (
        client.get(f"/api/transfers/{tid}", headers=receiver["headers"]).json()["state"] == "FAILED"
    )


def test_two_active_relays_bound_memory_and_apply_backpressure(relay):
    client, app, devices, _ = relay
    app.state.settings.transfer_timeout = 5
    tasks = []
    for sender, receiver in (devices[:2], devices[2:]):
        transfer, _ = create(client, sender, receiver, b"a" * 4096)
        tid = transfer["id"]
        assert (
            client.post(f"/api/transfers/{tid}/accept", headers=receiver["headers"]).status_code
            == 200
        )
        tasks.append((tid, sender, receiver))
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(
                client.post,
                f"/api/transfers/{tid}/chunks/0",
                content=b"a" * 4096,
                headers=sender["headers"],
            )
            for tid, sender, _ in tasks
        ]
        deadline = time.monotonic() + 3
        while app.state.transfers.buffered_bytes() < 8192 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert app.state.transfers.buffered_bytes() == 8192
        assert not any(future.done() for future in futures)
        for tid, _, receiver in tasks:
            assert (
                client.get(f"/api/transfers/{tid}/chunks/0", headers=receiver["headers"]).content
                == b"a" * 4096
            )
        assert all(future.result(5).status_code == 200 for future in futures)
    assert app.state.transfers.peak_buffer_bytes <= 2 * 4096
    assert app.state.transfers.buffered_bytes() == 0


def test_actual_forwarding_obeys_configured_aggregate_pacing(relay):
    client, app, devices, _ = relay
    app.state.settings.transfer_bytes_per_second = 8192
    app.state.settings.transfer_timeout = 5
    sender, receiver = devices[:2]
    transfer, _ = create(client, sender, receiver, b"a" * 12288)
    tid = transfer["id"]
    client.post(f"/api/transfers/{tid}/accept", headers=receiver["headers"])
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=1) as pool:
        for seq in range(3):
            future = pool.submit(
                client.post,
                f"/api/transfers/{tid}/chunks/{seq}",
                content=b"a" * 4096,
                headers=sender["headers"],
            )
            assert (
                len(
                    client.get(
                        f"/api/transfers/{tid}/chunks/{seq}", headers=receiver["headers"]
                    ).content
                )
                == 4096
            )
            assert future.result(5).status_code == 200
    assert time.monotonic() - started >= 0.9
