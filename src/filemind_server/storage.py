"""Real bounded local object storage. Baidu is deliberately fail-closed until authorized."""

import hashlib
import os
import uuid
from pathlib import Path
from typing import Protocol

from filemind.infrastructure.file_analysis import reject_links
from filemind.services.classification import valid_name


def io_path(path: Path):
    """Use Windows extended absolute paths for internal storage, retaining path checks."""
    path = path.absolute()
    raw = str(path)
    if os.name == "nt" and not raw.startswith("\\\\?\\"):
        raw = "\\\\?\\UNC\\" + raw[2:] if raw.startswith("\\\\") else "\\\\?\\" + raw
        return Path(raw)
    return path


def file_digest(path):
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while block := stream.read(262144):
            digest.update(block)
            size += len(block)
    return size, digest.hexdigest()


class StorageProvider(Protocol):
    def upload(self, source: Path, key: str) -> dict: ...
    def metadata(self, key: str) -> dict: ...
    def read(self, key: str): ...
    def delete(self, key: str): ...
    def list(self, prefix: str = "") -> list[dict]: ...
    def mkdir(self, key: str): ...


class LocalStorageProvider:
    capabilities = {
        "read": True,
        "range": True,
        "streaming": True,
        "delete": True,
        "list": True,
        "sha256": True,
        "multipart": True,
        "verification": "full_sha256_readback",
    }

    def __init__(self, root: Path):
        self.root = io_path(root)
        reject_links(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.pending = self.root / ".uploads"
        self.pending.mkdir(exist_ok=True)
        reject_links(self.pending)

    def path(self, key: str):
        parts = key.split("/")
        if not parts or parts[0] == ".uploads":
            raise ValueError("保留存储目录")
        for part in parts:
            valid_name(part)
        result = self.root.joinpath(*parts)
        if not result.resolve().is_relative_to(self.root.resolve()):
            raise ValueError("对象越出存储目录")
        reject_links(result)
        return result

    def mkdir(self, key):
        self.path(key).mkdir(parents=True, exist_ok=True)

    def exists(self, key):
        return self.path(key).is_file()

    def metadata(self, key):
        path = self.path(key)
        from filemind_server.downloads import signature

        digest, size = hashlib.sha256(), 0
        with path.open("rb") as stream:
            verified_signature = signature(stream)
            while block := stream.read(262144):
                size += len(block)
                digest.update(block)
            if signature(stream) != verified_signature:
                raise ValueError("OBJECT_CHANGED_DURING_VERIFICATION")
        return {
            "key": key,
            "size_bytes": size,
            "sha256": digest.hexdigest(),
            "verification_signature": verified_signature,
            "provider": "local",
            "verification_level": "full_sha256_readback",
        }

    def read(self, key):
        return self.path(key).open("rb")

    def download(self, key, destination: Path):
        with self.read(key) as source, destination.open("xb") as target:
            while block := source.read(262144):
                target.write(block)

    def begin_upload(self):
        identifier = str(uuid.uuid4())
        (self.pending / identifier).touch(exist_ok=False)
        return identifier

    def upload_part(self, identifier, offset, block):
        if str(uuid.UUID(identifier)) != identifier or len(block) > 1024 * 1024:
            raise ValueError("上传块无效")
        path = self.pending / identifier
        reject_links(path)
        with path.open("r+b") as stream:
            stream.seek(0, 2)
            if stream.tell() != offset:
                raise ValueError("偏移不匹配")
            stream.write(block)
            stream.flush()
            os.fsync(stream.fileno())

    def complete_upload(self, identifier, key, expected_size, expected_hash):
        if str(uuid.UUID(identifier)) != identifier:
            raise ValueError("上传ID无效")
        path = self.pending / identifier
        if file_digest(path) != (expected_size, expected_hash):
            raise ValueError("存储上传完整性不匹配")
        target = self.path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(path, target)  # atomic no-replace publication on the same filesystem
        except FileExistsError:
            if file_digest(target) != (expected_size, expected_hash):
                raise FileExistsError("正式对象冲突，拒绝覆盖") from None
        path.unlink()
        return self.metadata(key)

    def upload(self, source, key):
        expected_size, expected_hash = file_digest(source)
        if self.exists(key):
            meta = self.metadata(key)
            if (meta["size_bytes"], meta["sha256"]) != (expected_size, expected_hash):
                raise FileExistsError("正式对象冲突，拒绝覆盖")
            return meta
        identifier = self.begin_upload()
        try:
            with source.open("rb") as stream:
                offset = 0
                while block := stream.read(262144):
                    self.upload_part(identifier, offset, block)
                    offset += len(block)
            return self.complete_upload(identifier, key, expected_size, expected_hash)
        finally:
            (self.pending / identifier).unlink(missing_ok=True)

    def delete(self, key):
        self.path(key).unlink(missing_ok=True)

    def list(self, prefix=""):
        directory = self.path(prefix) if prefix else self.root
        return [
            self.metadata(p.relative_to(self.root).as_posix())
            for p in directory.rglob("*")
            if p.is_file() and not p.is_relative_to(self.pending) and not p.is_symlink()
        ]


from filemind_server.baidu import BaiduNetdiskProvider as BaiduNetdiskProvider  # noqa: E402
