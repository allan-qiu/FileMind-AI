import hashlib
from threading import Event

import httpx
import pytest

from filemind.services.cloud import CloudError
from filemind.services.library import CatalogCache, LibraryClient


def test_cache_replays_older_events_without_regression_and_hides_offline_shared():
    cache = CatalogCache()
    cache.apply(
        [
            {"file_id": "a", "revision": 2, "filename": "new", "workspace_id": None},
            {"file_id": "b", "revision": 3, "workspace_id": "shared"},
        ],
        3,
        "acl",
        True,
    )
    cache.apply([{"file": {"file_id": "a", "revision": 1, "filename": "old"}}], 3, "acl")
    assert cache.rows()[0]["filename"] == "new"
    assert len(cache.rows(offline=True)) == 1
    cache.clear()
    assert cache.rows() == [] and not cache.acl


@pytest.mark.parametrize("corrupt,cancelled", [(False, False), (True, False), (False, True)])
def test_disk_download_verifies_bytes_cancellation_and_cleanup(tmp_path, corrupt, cancelled):
    data = b"synthetic download" * 20000
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, content=b"bad" if corrupt else data)
    )
    client = LibraryClient("http://127.0.0.1", "synthetic", transport)
    version = {"id": "v1", "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    event, target = Event(), tmp_path / "download.bin"
    if cancelled:
        event.set()
    if corrupt or cancelled:
        with pytest.raises((CloudError, RuntimeError)):
            client.download(version, target, event)
        assert not target.exists()
    else:
        assert client.download(version, target, event)["state"] == "VERIFIED_SAVED"
        assert target.read_bytes() == data
        with pytest.raises(CloudError):
            client.download(version, target)
    assert not list(tmp_path.glob("*.part"))


def test_sync_network_offline_auth_revocation_and_account_switch():
    client = LibraryClient("http://127.0.0.1", "synthetic")
    account, state = ["alice"], ["online"]

    def request(method, path, body=None):
        if state[0] == "revoked":
            raise CloudError("revoked", 401)
        if state[0] == "offline":
            raise CloudError("network")
        if path == "/auth/session":
            return {"user": {"id": account[0]}}
        return {
            "items": [{"file_id": account[0], "revision": 1}],
            "revision": 1,
            "acl": account[0],
            "has_more": False,
            "next_offset": 1,
        }

    client.control.request = request
    assert client.sync()["items"][0]["file_id"] == "alice"
    state[0] = "offline"
    assert client.sync()["state"] == "OFFLINE_PRIVATE_CACHE"
    state[0] = "online"
    account[0] = "bob"
    assert client.sync()["items"][0]["file_id"] == "bob"
    state[0] = "revoked"
    with pytest.raises(CloudError):
        client.sync()
    assert client.cache.rows() == []


def test_download_interruption_and_disk_failure_leave_no_output(tmp_path, monkeypatch):
    class Interrupted(httpx.SyncByteStream):
        def __iter__(self):
            yield b"some synthetic bytes"
            raise httpx.ReadError("synthetic interruption")

    version = {"id": "v", "size_bytes": 100, "sha256": "a" * 64}
    client = LibraryClient(
        "http://127.0.0.1",
        "synthetic",
        httpx.MockTransport(lambda _: httpx.Response(200, stream=Interrupted())),
    )
    target = tmp_path / "interrupted.txt"
    with pytest.raises(CloudError):
        client.download(version, target)
    assert not target.exists() and not list(tmp_path.glob("*.part"))
    from pathlib import Path

    original = Path.open

    def denied(path, *args, **kwargs):
        if path.name.startswith(".filemind-download-"):
            raise PermissionError("synthetic disk denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", denied)
    with pytest.raises(CloudError):
        client.download(version, target)
    assert not target.exists()


def test_download_revocation_clears_session_cache(tmp_path):
    client = LibraryClient(
        "http://127.0.0.1", "synthetic", httpx.MockTransport(lambda _: httpx.Response(401))
    )
    client.cache.apply([{"file_id": "f", "revision": 1}], 1, "a", True)
    with pytest.raises(CloudError):
        client.download(
            {"id": "v", "size_bytes": 0, "sha256": hashlib.sha256(b"").hexdigest()},
            tmp_path / "denied.txt",
        )
    assert client.cache.rows() == []
