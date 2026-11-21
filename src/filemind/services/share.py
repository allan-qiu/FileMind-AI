"""Qt-free HTTP client. All hashing and transfer calls belong on background workers."""

from pathlib import Path

import httpx

from filemind.infrastructure.file_analysis import check_cancel, content_hash, reject_links
from filemind.services.cloud import CloudClient, CloudError


class ShareClient:
    def __init__(self, server, token, transport=None):
        self.control = CloudClient(server, token, transport)

    def request(self, method, path, body=None):
        return self.control.request(method, path, body)

    def upload(self, path: Path, cancel=None, progress=None):
        reject_links(path.absolute())
        size = path.stat().st_size
        digest = content_hash(path, cancel)
        up = self.request(
            "POST",
            "/shares/temporary/uploads",
            {"filename": path.name, "size_bytes": size, "sha256": digest},
        )
        upload_id = up["upload_id"]
        try:
            with (
                path.open("rb") as source,
                httpx.Client(
                    timeout=30, trust_env=False, transport=self.control.transport
                ) as client,
            ):
                offset = 0
                while block := source.read(up["chunk_bytes"]):
                    check_cancel(cancel)
                    response = client.put(
                        self.control.server
                        + f"/api/shares/temporary/uploads/{upload_id}/chunks/{offset}",
                        headers={
                            "Authorization": "Bearer " + self.control.token,
                            "Content-Type": "application/octet-stream",
                        },
                        content=block,
                    )
                    if not response.is_success:
                        raise CloudError("临时上传失败", response.status_code)
                    offset = response.json()["offset"]
                    if progress:
                        progress(offset, size)
            check_cancel(cancel)
            return self.request("POST", f"/shares/temporary/uploads/{upload_id}/complete")
        except BaseException:
            try:
                self.request("POST", f"/shares/temporary/uploads/{upload_id}/cancel")
            except CloudError:
                pass
            raise

    def create(self, source, maximum, ttl, password=""):
        return self.request(
            "POST",
            "/shares",
            {**source, "max_downloads": maximum, "ttl_seconds": ttl, "password": password or None},
        )
