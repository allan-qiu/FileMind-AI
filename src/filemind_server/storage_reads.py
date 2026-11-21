"""Provider-neutral verified reads, with a bounded compatibility cache for remote objects.

Local immutable object reads retain the existing signature cache. Remote providers without
an independently trusted SHA-256 identity are fully verified into a private temporary file
before any HTTP bytes are sent. This deliberately trades disk/TTFB for integrity.
"""

import hashlib
import shutil
import tempfile
import threading

from filemind.infrastructure.file_analysis import reject_links
from filemind_server.downloads import verify_open_file
from filemind_server.storage import LocalStorageProvider


class StorageReadError(ValueError):
    pass


class ReservedRead:
    def __init__(self, stream, release):
        self.stream, self.release = stream, release

    def __getattr__(self, name):
        return getattr(self.stream, name)

    def close(self):
        try:
            self.stream.close()
        finally:
            if self.release:
                release, self.release = self.release, None
                release()


class VerifiedStorageReads:
    def __init__(self, db, cache_dir, budget=512 * 1024 * 1024):
        self.db, self.cache_dir, self.budget = db, cache_dir, budget
        self.lock, self.reserved = threading.Lock(), 0

    def open(self, provider, key, size, sha256, namespace=""):
        if isinstance(provider, LocalStorageProvider):
            stream = provider.read(key)
            try:
                verify_open_file(self.db, stream, namespace + key, size, sha256)
                return stream
            except BaseException:
                stream.close()
                raise
        if not provider.capabilities.get("read"):
            raise StorageReadError("PROVIDER_READ_UNSUPPORTED")
        reject_links(self.cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.lock:
            free = shutil.disk_usage(self.cache_dir).free
            if size < 0 or self.reserved + size > self.budget or free < size + 16 * 1024**2:
                raise StorageReadError("VERIFIED_READ_CACHE_CAPACITY")
            self.reserved += size

        def release():
            with self.lock:
                self.reserved -= size

        cached = None
        try:
            cached = tempfile.TemporaryFile(dir=self.cache_dir)
            digest, received = hashlib.sha256(), 0
            with provider.read(key) as upstream:
                while True:
                    block = upstream.read(min(262144, size - received + 1))
                    if not block:
                        break
                    received += len(block)
                    if received > size:
                        raise StorageReadError("STORAGE_SIZE_MISMATCH")
                    digest.update(block)
                    cached.write(block)
            if received != size or digest.hexdigest() != sha256:
                raise StorageReadError("STORAGE_INTEGRITY_MISMATCH")
            cached.flush()
            cached.seek(0)
            return ReservedRead(cached, release)
        except BaseException:
            if cached:
                cached.close()
            release()
            raise
