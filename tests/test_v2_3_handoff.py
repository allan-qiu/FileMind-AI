from concurrent.futures import ThreadPoolExecutor

import pytest
from test_transfers import create, relay  # noqa: F401


@pytest.mark.parametrize("auto", [False, True])
def test_auto_archive_switch_and_explicit_manual_archive(relay, auto):  # noqa: F811
    client, app, devices, account = relay
    sender, receiver = devices[:2]
    client.put(
        "/api/archive/preferences", headers=account, json={"auto_archive": auto, "ai_mode": "off"}
    )
    data = b"synthetic switch regression"
    transfer, body = create(client, sender, receiver, data)
    tid = transfer["id"]
    client.post(f"/api/transfers/{tid}/accept", headers=receiver["headers"])
    with ThreadPoolExecutor(max_workers=1) as pool:
        sent = pool.submit(
            client.post, f"/api/transfers/{tid}/chunks/0", content=data, headers=sender["headers"]
        )
        assert (
            client.get(f"/api/transfers/{tid}/chunks/0", headers=receiver["headers"]).content
            == data
        )
        assert sent.result(5).is_success
    assert client.post(
        f"/api/transfers/{tid}/ack",
        headers=receiver["headers"],
        json={"sha256": body["sha256"], "confirmation": "written_file"},
    ).is_success
    jobs = client.get("/api/archive/jobs", headers=account).json()
    assert len(jobs) == int(auto)
    manual = client.post(f"/api/archive/transfers/{tid}", headers=account)
    assert manual.status_code == 201
    assert (
        client.post(f"/api/archive/transfers/{tid}", headers=account).json()["id"]
        == manual.json()["id"]
    )
    assert len(client.get("/api/archive/jobs", headers=account).json()) == 1
    assert app.state.transfers.row(tid)["state"] == "COMPLETED"
