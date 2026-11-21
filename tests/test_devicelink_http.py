import hashlib
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

import httpx
import pytest
import uvicorn

from filemind.services.cloud import CloudClient, CloudError
from filemind.services.devicelink import DeviceLinkClient
from filemind_server.app import create_app
from filemind_server.config import Settings


@pytest.fixture
def real_server(tmp_path):
    app = create_app(
        Settings(
            data_dir=tmp_path / "central",
            transfer_timeout=2,
            transfer_chunk_bytes=16384,
            transfer_max_bytes=8 * 1024 * 1024,
        )
    )
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, access_log=False, log_level="error", ws="none"))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    deadline = time.monotonic() + 8
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started
    try:
        yield f"http://127.0.0.1:{port}", app
    finally:
        server.should_exit = True
        thread.join(timeout=8)
        sock.close()
        assert not thread.is_alive()


def devices(server, platforms):
    with httpx.Client(base_url=server, trust_env=False) as client:
        data = {
            "username": "http_test",
            "password": "local-http-password",
            "display_name": "HTTP test",
        }
        assert client.post("/api/auth/register", json=data).status_code == 201
    session = CloudClient(server).login(data["username"], data["password"])
    account = CloudClient(server, session["access_token"])
    result = []
    for index, platform in enumerate(platforms):
        device = account.request(
            "POST", "/devices", {"name": f"HTTP-{index}", "platform": platform}
        )
        result.append((DeviceLinkClient(server, device["device_token"]), device["device"]["id"]))
    return result


@pytest.mark.parametrize(
    "platforms",
    [
        ("windows", "windows"),
        ("windows", "browser"),
        ("browser", "windows"),
        ("browser", "browser"),
    ],
)
@pytest.mark.parametrize("empty", [False, True])
def test_two_logical_devices_relay_real_file_over_tcp(real_server, tmp_path, platforms, empty):
    server, app = real_server
    (sender, _), (receiver, receiver_id) = devices(server, platforms)
    receiver.poll()
    source = tmp_path / "source.bin"
    payload = b"" if empty else bytes(range(251)) * 1000
    source.write_bytes(payload)
    target = tmp_path / "received.bin"
    observed = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        outgoing = pool.submit(
            sender.send, source, receiver_id, None, lambda a, b: observed.append((a, b))
        )
        deadline = time.monotonic() + 8
        transfer = None
        while time.monotonic() < deadline:
            tasks = receiver.poll()["tasks"]
            transfer = next((t for t in tasks if t["state"] == "WAITING_RECEIVER"), None)
            if transfer:
                break
            time.sleep(0.05)
        assert transfer
        incoming = pool.submit(receiver.receive, transfer["id"], target)
        assert incoming.result(timeout=20)["state"] == "COMPLETED"
        assert outgoing.result(timeout=20)["state"] == "COMPLETED"
    assert target.read_bytes() == source.read_bytes() == payload
    assert hashlib.sha256(target.read_bytes()).hexdigest() == transfer["expected_hash"]
    assert observed[-1] == (len(payload), len(payload))
    assert app.state.transfers.peak_buffer_bytes <= 2 * 16384
    assert app.state.transfers.buffered_bytes() == 0


@pytest.mark.parametrize("side", ["sender", "receiver"])
def test_real_socket_disconnect_ends_only_its_transfer(real_server, side):
    server, _ = real_server
    (sender, _), (receiver, receiver_id) = devices(server, ("windows", "browser"))
    receiver.poll()
    payload = b"x" * 16384
    transfer = sender.request(
        "POST",
        "",
        {
            "receiver_id": receiver_id,
            "filename": "disconnect.bin",
            "size_bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "request_key": str(time.time_ns()),
        },
    )
    tid = transfer["id"]
    receiver.request("POST", f"/{tid}/accept")
    address = urlsplit(server)
    sock = socket.create_connection((address.hostname, address.port))
    if side == "sender":
        header = (
            f"POST /api/transfers/{tid}/chunks/0 HTTP/1.1\r\nHost: 127.0.0.1\r\n"
            f"Authorization: Bearer {sender.control.token}\r\nContent-Length: 16384\r\n\r\n"
        )
        sock.sendall(header.encode() + b"incomplete")
    else:
        header = (
            f"GET /api/transfers/{tid}/chunks/0 HTTP/1.1\r\nHost: 127.0.0.1\r\n"
            f"Authorization: Bearer {receiver.control.token}\r\n\r\n"
        )
        sock.sendall(header.encode())
    time.sleep(0.1)
    sock.close()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        row = receiver.request("GET", f"/{tid}")
        if row["state"] == "FAILED":
            break
        time.sleep(0.05)
    assert row["state"] == "FAILED"


def test_cancel_wakes_blocked_sender_and_releases_buffer(real_server, tmp_path):
    server, app = real_server
    (sender, _), (receiver, receiver_id) = devices(server, ("windows", "browser"))
    receiver.poll()
    source = tmp_path / "cancel.bin"
    source.write_bytes(b"cancel me" * 3000)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(sender.send, source, receiver_id)
        deadline = time.monotonic() + 5
        tasks = []
        while not tasks and time.monotonic() < deadline:
            tasks = receiver.poll()["tasks"]
        tid = tasks[0]["id"]
        receiver.request("POST", f"/{tid}/accept")
        while time.monotonic() < deadline and not app.state.transfers.buffered_bytes():
            time.sleep(0.02)
        assert sender.cancel(tid)["state"] == "CANCELLED"
        with pytest.raises(CloudError):
            future.result(timeout=5)
    assert app.state.transfers.buffered_bytes() == 0
    assert source.exists()
