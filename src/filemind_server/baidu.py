"""Official XPan/OAuth protocol adapter. Disabled until separately authorized.

Protocol references: baidu-netdisk/mcp's official generated SDK and upload example.
No cookies/private endpoints. Real permissions and quotas require the app console.
"""

import hashlib
import io
import json
import os
import secrets
import threading
import time
from contextlib import contextmanager
from urllib.parse import urlencode, urlsplit

import httpx

from filemind.infrastructure.file_analysis import reject_links
from filemind.services.classification import valid_name
from filemind_server.key_store import protect


class BaiduError(ValueError):
    """Only fixed codes may escape the adapter; URLs/tokens/bodies stay private."""


class BaiduCredentials:
    def __init__(self, root):
        self.path = root / "private" / "baidu.credentials"
        self.lock = threading.RLock()

    def get(self):
        with self.lock:
            reject_links(self.path)
            if not self.path.exists():
                return {}
            try:
                return json.loads(protect(self.path.read_bytes(), True))
            except (ValueError, OSError):
                raise BaiduError("CREDENTIAL_STORE_UNAVAILABLE") from None

    def set(self, data):
        with self.lock:
            reject_links(self.path)
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            temp = self.path.with_name("baidu-" + secrets.token_hex(8) + ".new")
            try:
                with temp.open("xb") as stream:
                    if os.name != "nt":
                        os.fchmod(stream.fileno(), 0o600)
                    stream.write(protect(json.dumps(data).encode()))
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp, self.path)
            finally:
                temp.unlink(missing_ok=True)


class BaiduNetdiskProvider:
    name = "baidu"
    capabilities = {
        "read": True,
        "streaming": True,
        "range": False,
        "delete": True,
        "list": True,
        "multipart": True,
        "sha256": False,
        "verification": "full_sha256_readback_required",
        "live_verified": False,
        "connected": False,
        "reason": "OFFICIAL_AUTHORIZATION_REQUIRED",
    }
    chunk_bytes = 4 * 1024 * 1024

    def __init__(
        self,
        credentials=None,
        *,
        app_root="",
        client_id="",
        client_secret="",
        formal_authorized=False,
        remote_enabled=False,
        writes_enabled=False,
        transport=None,
        sleeper=time.sleep,
    ):
        self.credentials = credentials
        self.app_root = app_root.rstrip("/")
        self.client_id, self.client_secret = client_id, client_secret
        self.formal_authorized, self.remote_enabled = formal_authorized, remote_enabled
        self.writes_enabled, self.transport, self.sleeper = writes_enabled, transport, sleeper
        self.last_error = ""
        self.lock = threading.RLock()
        if self.app_root:
            parts = self.app_root.split("/")
            if len(parts) != 3 or parts[:2] != ["", "apps"]:
                raise BaiduError("APP_ROOT_MUST_BE_REGISTERED_APPS_DIRECTORY")
            valid_name(parts[2])

    def guard(self, write=False):
        if not self.formal_authorized or not self.remote_enabled:
            # Preserve the old fail-closed exception contract for unconfigured instances.
            raise NotImplementedError("OFFICIAL_AUTHORIZATION_REQUIRED")
        if not self.credentials or not self.app_root:
            raise BaiduError("BAIDU_NOT_CONFIGURED")
        if write and not self.writes_enabled:
            raise BaiduError("CLOUD_WRITE_NOT_AUTHORIZED")

    def status(self):
        value = self.credentials.get() if self.credentials else {}
        return {
            "provider": self.name,
            "configured": bool(value.get("access_token")),
            "formal_authorization_declared": self.formal_authorized,
            "remote_enabled": self.remote_enabled,
            "writes_enabled": self.writes_enabled,
            "expired": value.get("expires_at", 0) <= time.time(),
            "refresh_available": bool(value.get("refresh_token")),
            "status": "没有正式授权"
            if not self.formal_authorized
            else ("已配置，真实连接未验证" if value.get("access_token") else "未配置"),
            "last_error": self.last_error,
            "live_verified": False,
            "production_verified": False,
            "capabilities": self.capabilities,
        }

    def path(self, key):
        parts = key.split("/")
        for part in parts:
            valid_name(part)
        path = self.app_root + "/" + "/".join(parts)
        if not self.app_root or len(path.encode()) > 1000:
            raise BaiduError("INVALID_REMOTE_PATH")
        return path

    def _json(self, method, url, *, params=None, data=None, files=None, retry=False):
        try:
            with httpx.Client(
                transport=self.transport, timeout=30, trust_env=False, follow_redirects=False
            ) as client:
                for attempt in range(3 if retry else 1):
                    with client.stream(method, url, params=params, data=data, files=files) as r:
                        raw = bytearray()
                        for block in r.iter_bytes(16384):
                            if len(raw) + len(block) > 2 * 1024 * 1024:
                                raise BaiduError("BAIDU_RESPONSE_TOO_LARGE")
                            raw.extend(block)
                        if r.status_code == 429 and retry and attempt < 2:
                            self.sleeper(min(2**attempt, 4))
                            continue
                        if not r.is_success:
                            raise BaiduError("BAIDU_HTTP_" + str(r.status_code))
                        value = json.loads(raw)
                        if not isinstance(value, dict):
                            raise BaiduError("BAIDU_INVALID_RESPONSE")
                        code = value.get("errno", value.get("error_code", 0))
                        if value.get("error") or code:
                            if code in (31034, 31326) and retry and attempt < 2:
                                self.sleeper(min(2**attempt, 4))
                                continue
                            label = (
                                "BAIDU_TOKEN_EXPIRED"
                                if code in (110, 111)
                                else (
                                    "BAIDU_RATE_LIMIT"
                                    if code in (31034, 31326)
                                    else "BAIDU_REMOTE_ERROR"
                                )
                            )
                            raise BaiduError(label)
                        return value
        except BaiduError as exc:
            self.last_error = str(exc)
            raise
        except (httpx.HTTPError, ValueError, TypeError):
            self.last_error = "BAIDU_NETWORK_OR_PROTOCOL_ERROR"
            raise BaiduError(self.last_error) from None
        raise BaiduError("BAIDU_RETRY_EXHAUSTED")

    def authorization_url(self, redirect_uri):
        self.guard()
        if not self.client_id or not redirect_uri.startswith("https://"):
            raise BaiduError("OAUTH_CONFIGURATION_REQUIRED")
        state = secrets.token_urlsafe(32)
        with self.credentials.lock:
            value = self.credentials.get()
            value["pending"] = {
                "hash": hashlib.sha256(state.encode()).hexdigest(),
                "expires": time.time() + 600,
                "redirect": redirect_uri,
            }
            self.credentials.set(value)
        return "https://openapi.baidu.com/oauth/2.0/authorize?" + urlencode(
            {
                "response_type": "code",
                "client_id": self.client_id,
                "redirect_uri": redirect_uri,
                "scope": "basic,netdisk",
                "state": state,
            }
        )

    def exchange(self, code, state):
        self.guard()
        with self.credentials.lock:
            value = self.credentials.get()
            pending = value.pop("pending", {})
            self.credentials.set(value)  # consume before network; never replay a code
            if pending.get("expires", 0) <= time.time() or not secrets.compare_digest(
                pending.get("hash", ""), hashlib.sha256(state.encode()).hexdigest()
            ):
                raise BaiduError("OAUTH_STATE_INVALID")
            result = self._json(
                "POST",
                "https://openapi.baidu.com/oauth/2.0/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "redirect_uri": pending["redirect"],
                },
            )
            self._save_tokens(result)

    def _save_tokens(self, value, prior=None):
        if (
            not isinstance(value.get("access_token"), str)
            or not value["access_token"]
            or type(value.get("expires_in")) is not int
            or value["expires_in"] <= 0
        ):
            raise BaiduError("OAUTH_INVALID_TOKEN_RESPONSE")
        refresh = value.get("refresh_token") or (prior or {}).get("refresh_token")
        self.credentials.set(
            {
                "access_token": value["access_token"],
                "refresh_token": refresh,
                "expires_at": time.time() + value["expires_in"],
            }
        )

    def token(self):
        self.guard()
        with self.credentials.lock:
            value = self.credentials.get()
            if value.get("expires_at", 0) > time.time() + 60 and value.get("access_token"):
                return value["access_token"]
            if not value.get("refresh_token") or not self.client_id or not self.client_secret:
                raise BaiduError("REAUTHORIZATION_REQUIRED")
            result = self._json(
                "POST",
                "https://openapi.baidu.com/oauth/2.0/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": value["refresh_token"],
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
            )
            self._save_tokens(result, value)
            return result["access_token"]

    def disconnect(self):
        # Local deletion does not claim remote revocation. User must unlink in Baidu console.
        if self.credentials:
            self.credentials.set({})
        return {"disconnected": True, "remote_revocation_verified": False}

    def api(self, method, endpoint, operation, *, data=None, params=None, write=False):
        self.guard(write)
        return self._json(
            method,
            "https://pan.baidu.com" + endpoint,
            params={"method": operation, "access_token": self.token(), **(params or {})},
            data=data,
            retry=method == "GET",
        )

    def quota(self):
        self.guard()
        result = self._json(
            "GET",
            "https://pan.baidu.com/api/quota",
            params={"access_token": self.token(), "checkfree": 1},
            retry=True,
        )
        if any(type(result.get(k)) is not int or result[k] < 0 for k in ("total", "used")):
            raise BaiduError("INVALID_QUOTA_RESPONSE")
        return {k: result[k] for k in ("total", "used")}

    def list(self, prefix=""):
        self.guard()
        remote = self.path(prefix) if prefix else self.app_root
        items = []
        for start in range(0, 100000, 1000):
            result = self.api(
                "GET",
                "/rest/2.0/xpan/file",
                "list",
                params={"dir": remote, "start": start, "limit": 1000},
            )
            page = result.get("list")
            if not isinstance(page, list):
                raise BaiduError("BAIDU_INVALID_LIST")
            for row in page:
                path = row.get("path", "")
                if not path.startswith(self.app_root + "/"):
                    raise BaiduError("BAIDU_PATH_OUTSIDE_APP")
                key = path[len(self.app_root) + 1 :]
                self.path(key)
                items.append(
                    {
                        "key": key,
                        "size_bytes": row.get("size"),
                        "md5": row.get("md5"),
                        "fs_id": row.get("fs_id"),
                        "isdir": row.get("isdir", 0),
                        "provider": self.name,
                        "verification_level": "size_md5_metadata",
                        "sha256": None,
                    }
                )
            if len(page) < 1000:
                return items
        raise BaiduError("LIST_PAGE_LIMIT_REACHED")

    def metadata(self, key):
        self.path(key)
        for row in self.list(key.rpartition("/")[0]):
            if row["key"] == key and not row["isdir"]:
                if type(row["size_bytes"]) is not int or row["size_bytes"] < 0:
                    raise BaiduError("BAIDU_INVALID_METADATA")
                return row
        raise FileNotFoundError("BAIDU_OBJECT_NOT_FOUND")

    def exists(self, key):
        try:
            self.metadata(key)
            return True
        except FileNotFoundError:
            return False

    @contextmanager
    def read(self, key):
        meta = self.metadata(key)
        result = self.api(
            "GET",
            "/rest/2.0/xpan/multimedia",
            "filemetas",
            params={"fsids": json.dumps([meta["fs_id"]]), "dlink": 1},
        )
        rows = result.get("list", [])
        if len(rows) != 1 or rows[0].get("fs_id") != meta["fs_id"]:
            raise BaiduError("BAIDU_INVALID_DOWNLOAD_METADATA")
        url = rows[0].get("dlink", "")
        parsed = urlsplit(url)
        # Never follow arbitrary redirects or send credentials to untrusted hosts.
        host = parsed.hostname or ""
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
            or not (host.endswith(".baidu.com") or host.endswith(".baidupcs.com"))
        ):
            raise BaiduError("UNSUPPORTED_DOWNLOAD_HOST_OR_REDIRECT")
        try:
            with httpx.Client(
                transport=self.transport, timeout=30, trust_env=False, follow_redirects=False
            ) as client:
                with client.stream(
                    "GET",
                    url,
                    params={"access_token": self.token()},
                    headers={"User-Agent": "pan.baidu.com"},
                ) as response:
                    if response.status_code != 200:
                        raise BaiduError("BAIDU_DOWNLOAD_HTTP_" + str(response.status_code))
                    yield BoundedHTTPRead(response)
        except httpx.HTTPError:
            raise BaiduError("BAIDU_DOWNLOAD_INTERRUPTED") from None

    def verify(self, key, expected_size, expected_hash):
        meta = self.metadata(key)
        if meta["size_bytes"] != expected_size:
            raise BaiduError("STORAGE_SIZE_MISMATCH")
        digest, count = hashlib.sha256(), 0
        with self.read(key) as stream:
            while block := stream.read(262144):
                count += len(block)
                if count > expected_size:
                    raise BaiduError("STORAGE_SIZE_MISMATCH")
                digest.update(block)
        if count != expected_size or digest.hexdigest() != expected_hash:
            raise BaiduError("STORAGE_INTEGRITY_MISMATCH")
        return {**meta, "sha256": digest.hexdigest(), "verification_level": "full_sha256_readback"}

    def mkdir(self, key):
        return self.api(
            "POST",
            "/rest/2.0/xpan/file",
            "create",
            write=True,
            data={"path": self.path(key), "isdir": 1, "rtype": 0},
        )

    def upload(self, source, key):
        self.guard(True)
        from filemind_server.storage import file_digest

        expected_size, expected_hash = file_digest(source)
        if expected_size > 256 * 1024**2 or not expected_size:
            raise BaiduError("UNSUPPORTED_UPLOAD_SIZE_1_TO_256_MIB")
        # XPan precreate does not promise to create missing ancestors. Create only
        # the registered app's required parents, without replacing existing entries.
        parts = key.split("/")
        self.path(key)
        for depth in range(1, len(parts)):
            parent = "/".join(parts[: depth - 1])
            directory = "/".join(parts[:depth])
            entry = next((item for item in self.list(parent) if item["key"] == directory), None)
            if entry and not entry["isdir"]:
                raise BaiduError("REMOTE_PARENT_IS_FILE")
            if not entry:
                result = self.mkdir(directory)
                if result.get("path") != self.path(directory) or result.get("isdir") != 1:
                    raise BaiduError("BAIDU_INVALID_CREATED_DIRECTORY")
        if self.exists(key):
            return self.verify(key, expected_size, expected_hash)  # immutable no-overwrite
        block_list = []
        with source.open("rb") as stream:
            while block := stream.read(self.chunk_bytes):
                block_list.append(hashlib.md5(block, usedforsecurity=False).hexdigest())
        base = {
            "path": self.path(key),
            "size": expected_size,
            "isdir": 0,
            "block_list": json.dumps(block_list),
            "rtype": 0,
        }
        result = self.api(
            "POST", "/rest/2.0/xpan/file", "precreate", write=True, data={**base, "autoinit": 1}
        )
        uploadid = result.get("uploadid")
        if not isinstance(uploadid, str) or not uploadid:
            raise BaiduError("BAIDU_INVALID_UPLOAD_ID")
        requested = result.get("block_list")
        if not isinstance(requested, list) or any(
            type(i) is not int or i < 0 or i >= len(block_list) for i in requested
        ):
            raise BaiduError("BAIDU_INVALID_BLOCK_LIST")
        with source.open("rb") as stream:
            for part in requested:
                stream.seek(part * self.chunk_bytes)
                block = stream.read(self.chunk_bytes)
                result = self._json(
                    "POST",
                    "https://d.pcs.baidu.com/rest/2.0/pcs/superfile2",
                    params={
                        "method": "upload",
                        "access_token": self.token(),
                        "type": "tmpfile",
                        "path": self.path(key),
                        "uploadid": uploadid,
                        "partseq": str(part),
                    },
                    files={"file": ("block", io.BytesIO(block))},
                )
                if result.get("md5") != block_list[part]:
                    raise BaiduError("BAIDU_PART_HASH_MISMATCH")
        if file_digest(source) != (expected_size, expected_hash):
            raise BaiduError("SOURCE_CHANGED_DURING_UPLOAD")
        result = self.api(
            "POST", "/rest/2.0/xpan/file", "create", write=True, data={**base, "uploadid": uploadid}
        )
        if result.get("path") != self.path(key):
            raise BaiduError("BAIDU_UNEXPECTED_CREATED_PATH")
        return self.verify(key, expected_size, expected_hash)

    def delete(self, key):
        return self.api(
            "POST",
            "/rest/2.0/xpan/file",
            "filemanager",
            write=True,
            params={"opera": "delete"},
            data={"async": 0, "filelist": json.dumps([self.path(key)])},
        )

    def download(self, key, destination):
        with self.read(key) as source, destination.open("xb") as target:
            while block := source.read(262144):
                target.write(block)


class BoundedHTTPRead:
    def __init__(self, response):
        self.blocks, self.pending = response.iter_bytes(262144), bytearray()

    def read(self, size):
        if not 1 <= size <= 4 * 1024 * 1024:
            raise BaiduError("BOUNDED_READ_SIZE_REQUIRED")
        while len(self.pending) < size:
            block = next(self.blocks, b"")
            if not block:
                break
            self.pending.extend(block)
        result = bytes(self.pending[:size])
        del self.pending[:size]
        return result
