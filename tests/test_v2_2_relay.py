import hashlib
from concurrent.futures import ThreadPoolExecutor

from test_transfers import create, relay  # noqa: F401


def test_relay_rejects_forged_receiver_hash(relay):  # noqa: F811
    client, app, devices, _ = relay
    sender, receiver = devices[:2]
    transfer, body = create(client, sender, receiver, b"a")
    tid = transfer["id"]
    client.post(f"/api/transfers/{tid}/accept", headers=receiver["headers"])
    with ThreadPoolExecutor(max_workers=1) as pool:
        upload = pool.submit(
            client.post, f"/api/transfers/{tid}/chunks/0", content=b"b", headers=sender["headers"]
        )
        assert (
            client.get(f"/api/transfers/{tid}/chunks/0", headers=receiver["headers"]).content
            == b"b"
        )
        assert upload.result(5).is_success
    response = client.post(
        f"/api/transfers/{tid}/ack",
        headers=receiver["headers"],
        json={"sha256": body["sha256"], "confirmation": "written_file"},
    )
    assert response.status_code == 422
    assert app.state.transfers.row(tid)["state"] == "FAILED"


def test_browser_limit_rejected_before_sending(relay):  # noqa: F811
    client, app, devices, _ = relay
    app.state.settings.transfer_max_bytes = 256 * 1024 * 1024
    response = client.post(
        "/api/transfers",
        headers=devices[0]["headers"],
        json={
            "receiver_id": devices[1]["device"]["id"],
            "filename": "large.bin",
            "size_bytes": 128 * 1024 * 1024,
            "sha256": hashlib.sha256(b"").hexdigest(),
            "request_key": "browser-limit-test",
        },
    )
    assert response.status_code == 413
