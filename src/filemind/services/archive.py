"""Windows archive HTTP client. Uploads resume from the server's durable offset."""

import uuid
from pathlib import Path

import httpx

from filemind.infrastructure.file_analysis import check_cancel, content_hash, reject_links
from filemind.services.cloud import CloudClient, CloudError
from filemind.services.devicelink import DeviceLinkClient


class ArchiveClient:
    def __init__(self, server, token):
        self.control = CloudClient(server, token)

    def jobs(self):
        return self.control.request("GET", "/archive/jobs")

    def preferences(self):
        return self.control.request("GET", "/archive/preferences")

    def create_upload(self, path: Path, cancel=None, progress=None):
        reject_links(path.absolute())
        digest = content_hash(path, cancel)
        row = self.control.request(
            "POST",
            "/archive/jobs",
            {
                "filename": path.name,
                "size_bytes": path.stat().st_size,
                "sha256": digest,
                "idempotency_key": str(uuid.uuid4()),
            },
        )
        return self.upload(row, path, cancel, progress)

    def upload(self, job: dict, path: Path, cancel=None, progress=None):
        reject_links(path.absolute())
        if path.stat().st_size != job["size_bytes"] or content_hash(path, cancel) != job["sha256"]:
            raise CloudError("归档来源与已交付文件哈希/大小不同，拒绝上传")
        job_id = job["id"]
        lease = self.control.request("POST", f"/archive/jobs/{job_id}/claim")
        headers = {
            "Authorization": "Bearer " + self.control.token,
            "X-Archive-Lease": lease["lease_token"],
            "Content-Type": "application/octet-stream",
        }
        offset = lease["offset"]
        if progress:
            progress(offset, job["size_bytes"])
        with httpx.Client(timeout=40, trust_env=False) as client, path.open("rb") as source:
            source.seek(offset)
            while block := source.read(lease["chunk_bytes"]):
                check_cancel(cancel)
                response = client.put(
                    self.control.server + f"/api/archive/jobs/{job_id}/chunks/{offset}",
                    headers=headers,
                    content=block,
                )
                DeviceLinkClient.check_response(response)
                offset = response.json()["offset"]
                if progress:
                    progress(offset, job["size_bytes"])
            check_cancel(cancel)
            response = client.post(
                self.control.server + f"/api/archive/jobs/{job_id}/finish", headers=headers
            )
            DeviceLinkClient.check_response(response)
            return response.json()

    def auto_from_transfer(self, transfer, path: Path, cancel=None, progress=None):
        if not self.preferences()["auto_archive"]:
            return {"state": "AUTO_ARCHIVE_OFF"}
        job = next((r for r in self.jobs() if r["source_transfer_id"] == transfer["id"]), None)
        if not job:
            raise CloudError("独立归档任务尚未创建，请在归档列表中重试")
        if job["state"] not in {"WAITING_SOURCE", "WAITING_UPLOAD", "UPLOADING"}:
            return job
        return self.upload(job, path, cancel, progress)
