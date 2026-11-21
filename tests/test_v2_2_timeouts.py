import hashlib
import socket
from urllib.parse import urlsplit

from test_devicelink_http import devices, real_server  # noqa: F401


def test_slow_chunk_body_returns_408_and_releases_relay(real_server):  # noqa: F811
    url, app = real_server
    app.state.settings.transfer_timeout = 1
    (sender, _), (receiver, receiver_id) = devices(url, ("windows", "windows"))
    receiver.poll()
    transfer = sender.request(
        "POST",
        "",
        {
            "receiver_id": receiver_id,
            "filename": "slow.bin",
            "size_bytes": 16384,
            "sha256": hashlib.sha256(b"x" * 16384).hexdigest(),
            "request_key": "slow-body-test",
        },
    )
    tid = transfer["id"]
    receiver.request("POST", f"/{tid}/accept")
    address = urlsplit(url)
    with socket.create_connection((address.hostname, address.port), timeout=4) as sock:
        sock.sendall(
            (
                f"POST /api/transfers/{tid}/chunks/0 HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                f"Authorization: Bearer {sender.control.token}\r\nContent-Length: 16384\r\n\r\nx"
            ).encode()
        )
        response = sock.recv(4096)
        assert b" 408 " in response
    assert sender.request("GET", f"/{tid}")["state"] == "FAILED"
    assert app.state.transfers.buffered_bytes() == 0
