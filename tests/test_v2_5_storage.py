# ruff: noqa: F811
"""Synthetic protocol fixtures, never real Baidu requests or credentials."""

import hashlib
import io
import json
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from email.parser import BytesParser
from email.policy import default
from urllib.parse import parse_qs

import httpx
import pytest
from test_server import PASSWORD, bearer, login
from test_v2_2_archives import archive, consent, create_upload  # noqa: F401
from test_v2_3_library import archived
from test_v2_4_shares import access, count, reservation, share

from filemind_server.ai import AIError
from filemind_server.ai_control import save_config
from filemind_server.archives import ArchiveSettings
from filemind_server.baidu import BaiduCredentials, BaiduError, BaiduNetdiskProvider


class FakeXPan:
    def __init__(self, data=b"synthetic remote object"):
        self.data, self.calls = data, []
        self.created = False
        self.blocks = []
        self.directories = {"/apps/FileMind/u"}

    def __call__(self, request):
        self.calls.append((request.method, request.url.path, dict(request.url.params)))
        operation = request.url.params.get("method")
        body = (
            parse_qs(request.content.decode())
            if request.headers.get("content-type", "").startswith(
                "application/x-www-form-urlencoded"
            )
            else {}
        )
        path = "/apps/FileMind/u/notes.txt"
        row = {
            "path": path,
            "size": len(self.data),
            "fs_id": 42,
            "isdir": 0,
            "md5": hashlib.md5(self.data, usedforsecurity=False).hexdigest(),
        }
        value = {"errno": 0}
        if request.url.path == "/oauth/2.0/token":
            value = {
                "access_token": "synthetic-rotated",
                "refresh_token": "synthetic-refresh-2",
                "expires_in": 3600,
            }
        elif request.url.path == "/api/quota":
            value = {"errno": 0, "total": 10**9, "used": len(self.data)}
        elif operation == "list":
            directory = request.url.params["dir"]
            value["list"] = [
                {"path": p, "isdir": 1, "size": 0, "fs_id": 10}
                for p in self.directories
                if p.rpartition("/")[0] == directory
            ]
            if self.created and directory == "/apps/FileMind/u":
                value["list"].append(row)
        elif operation == "precreate":
            assert body["rtype"] == ["0"]
            self.blocks = json.loads(body["block_list"][0])
            value.update(uploadid="synthetic-upload", block_list=list(range(len(self.blocks))))
        elif operation == "upload":
            message = BytesParser(policy=default).parsebytes(
                b"Content-Type: "
                + request.headers["content-type"].encode()
                + b"\r\n\r\n"
                + request.content
            )
            part = next(message.iter_parts()).get_payload(decode=True)
            index = int(request.url.params["partseq"])
            assert part == self.data[index * 4 * 1024**2 : (index + 1) * 4 * 1024**2]
            value["md5"] = hashlib.md5(part, usedforsecurity=False).hexdigest()
        elif operation == "create":
            assert body["rtype"] == ["0"]
            if body["isdir"] == ["1"]:
                self.directories.add(body["path"][0])
                value.update(path=body["path"][0], isdir=1)
            else:
                self.created = True
                value.update(path=path, fs_id=42)
        elif operation == "filemetas":
            value["list"] = [{**row, "dlink": "https://d.pcs.baidu.com/download/synthetic"}]
        elif request.url.path == "/download/synthetic":
            return httpx.Response(200, content=self.data)
        return httpx.Response(200, json=value)


def provider(tmp_path, handler=None, **kwargs):
    vault = BaiduCredentials(tmp_path)
    vault.set(
        {
            "access_token": "synthetic-access",
            "refresh_token": "synthetic-refresh",
            "expires_at": time.time() + 3600,
        }
    )
    return BaiduNetdiskProvider(
        vault,
        app_root="/apps/FileMind",
        client_id="synthetic-app",
        client_secret="synthetic-secret",
        formal_authorized=True,
        remote_enabled=True,
        writes_enabled=True,
        transport=httpx.MockTransport(handler or FakeXPan()),
        sleeper=lambda _: None,
        **kwargs,
    )


def test_baidu_default_never_network_and_write_requires_independent_gate(tmp_path):
    fake = FakeXPan()
    p = BaiduNetdiskProvider(transport=httpx.MockTransport(fake))
    assert p.status()["status"] == "没有正式授权"
    with pytest.raises(NotImplementedError):
        p.quota()
    assert not fake.calls
    p = provider(tmp_path, fake)
    p.writes_enabled = False
    with pytest.raises(BaiduError, match="WRITE_NOT_AUTHORIZED"):
        p.mkdir("folder")
    assert not fake.calls


def test_baidu_real_protocol_multipart_readback_and_no_overwrite(tmp_path):
    data = b"x" * (4 * 1024**2 + 19)
    fake = FakeXPan(data)
    p = provider(tmp_path, fake)
    source = tmp_path / "source.txt"
    source.write_bytes(data)
    meta = p.upload(source, "u/notes.txt")
    assert meta["sha256"] == hashlib.sha256(data).hexdigest()
    assert meta["provider"] == "baidu" and meta["verification_level"] == "full_sha256_readback"
    assert len([r for r in fake.calls if r[2].get("method") == "upload"]) == 2
    initial = len([r for r in fake.calls if r[0] == "POST"])
    p.upload(source, "u/notes.txt")
    assert len([r for r in fake.calls if r[0] == "POST"]) == initial
    assert p.quota()["used"] == len(data)
    p.delete("u/notes.txt")
    assert fake.calls[-1][2]["opera"] == "delete"


def test_baidu_creates_missing_parent_before_precreate(tmp_path):
    fake = FakeXPan()
    fake.directories.clear()
    p = provider(tmp_path, fake)
    source = tmp_path / "source.txt"
    source.write_bytes(fake.data)
    p.upload(source, "u/notes.txt")
    assert "/apps/FileMind/u" in fake.directories


def test_baidu_oauth_state_refresh_rotation_disconnect_and_masking(tmp_path):
    fake = FakeXPan()
    p = provider(tmp_path, fake)
    url = p.authorization_url("https://filemind.example/oauth/baidu")
    state = parse_qs(url.split("?", 1)[1])["state"][0]
    with pytest.raises(BaiduError, match="STATE_INVALID"):
        p.exchange("synthetic-code", "wrong-state")
    url = p.authorization_url("https://filemind.example/oauth/baidu")
    state = parse_qs(url.split("?", 1)[1])["state"][0]
    p.exchange("synthetic-code", state)
    assert p.credentials.get()["refresh_token"] == "synthetic-refresh-2"
    value = p.credentials.get()
    value["expires_at"] = 0
    p.credentials.set(value)
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(lambda _: p.token(), range(4))) == ["synthetic-rotated"] * 4
    assert len([r for r in fake.calls if r[1] == "/oauth/2.0/token"]) == 2
    assert "synthetic-rotated" not in json.dumps(p.status())
    assert not p.disconnect()["remote_revocation_verified"]
    with pytest.raises(BaiduError, match="REAUTHORIZATION"):
        p.token()


@pytest.mark.parametrize("key", ["../x", "/root", "u/../../x", "u/CON.txt", "u\\other"])
def test_baidu_paths_stay_in_registered_app(tmp_path, key):
    with pytest.raises(ValueError):
        provider(tmp_path).path(key)


def test_baidu_retry_is_bounded_and_errors_do_not_echo_tokens(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, json={"error": "synthetic-access"})

    p = provider(tmp_path, handler)
    with pytest.raises(BaiduError) as error:
        p.quota()
    assert len(calls) == 3 and "synthetic-access" not in str(error.value)
    calls.clear()
    with pytest.raises(BaiduError):
        p.mkdir("folder")
    assert len(calls) == 1  # no blind write retries after an uncertain result


@pytest.mark.parametrize(
    "host",
    ["https://evil.example/x", "http://d.pcs.baidu.com/x", "https://baidu.com.evil.example/x"],
)
def test_baidu_never_sends_download_credentials_to_untrusted_hosts(tmp_path, host):
    fake = FakeXPan()
    fake.created = True

    def handler(request):
        if request.url.params.get("method") == "filemetas":
            return httpx.Response(200, json={"errno": 0, "list": [{"fs_id": 42, "dlink": host}]})
        return fake(request)

    p = provider(tmp_path, handler)
    with pytest.raises(BaiduError, match="UNSUPPORTED_DOWNLOAD"):
        with p.read("u/notes.txt"):
            pass
    assert all(r[1] != "/x" for r in fake.calls)


class RemoteBytes:
    capabilities = {"read": True}

    def __init__(self, data, broken=False):
        self.data, self.broken, self.closed = data, broken, False

    @contextmanager
    def read(self, key):
        try:
            if self.broken:
                raise OSError("SYNTHETIC_UPSTREAM_DISCONNECT")
            yield io.BytesIO(self.data)
        finally:
            self.closed = True


def remote_version(archive, remote):
    _, app, _, _ = archive
    item = archived(archive)
    app.state.archives.providers["remote-test"] = remote
    with app.state.db.transaction(True) as conn:
        conn.execute(
            "UPDATE storage_objects SET provider='remote-test' WHERE id=?", (item["version_id"],)
        )
    return item


def test_library_and_share_reuse_remote_reader_ranges_and_independent_limits(archive):
    c, app, h, _ = archive
    data = b"Python version one"
    remote = RemoteBytes(data)
    item = remote_version(archive, remote)
    assert c.get(f"/api/library/{item['version_id']}/content", headers=h).content == data
    ranged = c.get(
        f"/api/library/{item['version_id']}/content", headers={**h, "Range": "bytes=3-7"}
    )
    assert ranged.status_code == 206 and ranged.content == data[3:8]
    for maximum in (1, 3, 10):
        link = share(archive, vid=item["version_id"], maximum=maximum)
        grant = access(c, link)["grant"]
        for _ in range(maximum):
            result, dh = reservation(c, grant)
            assert result.status_code == 201
            response = c.get(
                "/api/public/downloads/" + result.json()["session_id"] + "/content", headers=dh
            )
            assert response.content == data
        assert reservation(c, grant)[0].status_code == 409
        assert count(archive, link["share_id"]) == (maximum, 0)
    assert remote.closed and app.state.archives.reads.reserved == 0
    assert not list(app.state.archives.reads.cache_dir.iterdir())
    with app.state.db.transaction() as conn:
        assert conn.execute("SELECT count(*) FROM storage_objects").fetchone()[0] == 1


@pytest.mark.parametrize("failure", ["hash", "short", "long", "network", "capacity"])
def test_remote_read_failure_releases_cache_and_download_reservation(archive, failure):
    c, app, _, _ = archive
    data = b"Python version one"
    values = {"hash": b"X" * len(data), "short": b"x", "long": data + b"extra"}
    remote = RemoteBytes(values.get(failure, data), broken=failure == "network")
    item = remote_version(archive, remote)
    if failure == "capacity":
        app.state.archives.reads.budget = 1
    link = share(archive, vid=item["version_id"], maximum=1)
    result, headers = reservation(c, access(c, link)["grant"])
    response = c.get(
        "/api/public/downloads/" + result.json()["session_id"] + "/content", headers=headers
    )
    assert response.status_code == 409 and count(archive, link["share_id"]) == (0, 0)
    assert app.state.archives.reads.reserved == 0


def test_storage_binding_survives_settings_change_and_publication_retry(archive):
    _, app, _, _ = archive
    consent(archive)
    job, _ = create_upload(archive)
    service = app.state.archives
    service.before_metadata_commit = lambda: (_ for _ in ()).throw(RuntimeError("SYNTHETIC_DB"))
    service.process_one(job["id"])
    assert service.row(job["id"])["state"] == "RETRY_WAIT"
    save_config(app.state.db, "archive_v2_2", ArchiveSettings(provider="baidu"))
    service.before_metadata_commit = None
    with app.state.db.transaction(True) as conn:
        conn.execute("UPDATE archive_jobs SET retry_at=0 WHERE id=?", (job["id"],))
    service.process_one(job["id"])
    assert service.row(job["id"])["state"] == "COMPLETED"
    with app.state.db.transaction() as conn:
        assert conn.execute("SELECT provider FROM storage_objects").fetchone()[0] == "local"


def test_ai_duplicate_inputs_have_no_extra_provider_call_and_reject_id_reuse(archive):
    _, app, _, user = archive
    ai = app.state.ai
    first = ai.plan("synthetic-one", user["id"], {"filename": "notes.txt"})
    assert ai.plan("synthetic-two", user["id"], {"filename": "notes.txt"}) == first
    with app.state.db.transaction() as conn:
        assert (
            conn.execute(
                "SELECT cache_source_id FROM ai_request_inputs WHERE usage_id=?", ("synthetic-two",)
            ).fetchone()[0]
            == "synthetic-one"
        )
        row = conn.execute("SELECT * FROM ai_usage_records WHERE id='synthetic-two'").fetchone()
        assert row["input_tokens"] == 0 and row["estimated_cost"] == 0
    with pytest.raises(AIError, match="INPUT_MISMATCH"):
        ai.plan("synthetic-one", user["id"], {"filename": "other.txt"})


def test_system_status_authentication_and_no_fabricated_connections(archive):
    c, app, h, _ = archive
    assert c.get("/api/admin/system-status", headers=h).status_code == 403
    from filemind_server.accounts import create_account
    from filemind_server.models import Registration

    with app.state.db.transaction(True) as conn:
        create_account(
            conn,
            Registration(username="status_admin", display_name="Synthetic", password=PASSWORD),
            "system_admin",
        )
    admin = bearer(login(c, "status_admin", admin=True))
    result = c.get("/api/admin/system-status", headers=admin)
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["baidu"]["status"] == "没有正式授权"
    assert data["acceptance"]["production"] == "未验证"
    assert not data["ai"]["paid_calls_enabled"]
    assert c.post("/api/admin/storage/baidu/probe", headers=admin).status_code == 409
