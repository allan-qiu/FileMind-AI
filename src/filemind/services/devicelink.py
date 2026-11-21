"Device-token HTTP relay client. Files stay local; only bounded chunks enter the server."

import hashlib
import time
import uuid
from pathlib import Path
from threading import Event

import httpx

from filemind.infrastructure.file_analysis import (
    Cancelled,
    check_cancel,
    content_hash,
    reject_links,
)
from filemind.services.classification import valid_name
from filemind.services.cloud import CloudClient, CloudError
from filemind.services.local_organizer import commit_without_replace

TERMINAL = {"COMPLETED", "FAILED", "REJECTED", "CANCELLED", "EXPIRED"}


class DeviceLinkClient:
    def __init__(self, server: str, token: str):
        self.control = CloudClient(server, token)

    def request(self, method, path, body=None):
        return self.control.request(method, "/transfers" + path, body)

    def poll(self):
        return {"tasks": self.request("GET", "/inbox"), "peers": self.request("GET", "/peers")}

    def cancel(self, transfer_id):
        return self.request("POST", f"/{transfer_id}/cancel")

    def fail(self, transfer_id, message):
        try:
            return self.request("POST", f"/{transfer_id}/fail", {"reason": str(message)[:300]})
        except Exception:
            return None

    @staticmethod
    def check_response(response):
        if not response.is_success:
            try:
                detail = response.json().get("detail", "传输请求失败。")
            except ValueError:
                detail = f"传输 HTTP {response.status_code}"
            raise CloudError(str(detail))

    def send(
        self, path: Path, receiver_id: str, cancel: Event | None = None, progress=None, created=None
    ):
        reject_links(path.absolute())
        valid_name(path.name)
        before = path.stat()
        config = self.request("GET", "/config")
        if before.st_size > config["max_bytes"]:
            raise CloudError("文件超过当前服务器大小限制。")
        digest = content_hash(path, cancel)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise CloudError("发送前文件正在变化，请稍后重试。")
        transfer = self.request(
            "POST",
            "",
            {
                "receiver_id": receiver_id,
                "filename": path.name,
                "size_bytes": before.st_size,
                "sha256": digest,
                "request_key": str(uuid.uuid4()),
            },
        )
        tid = transfer["id"]
        if created:
            created(tid)
        deadline = time.monotonic() + config["ttl"]
        try:
            while transfer["state"] == "WAITING_RECEIVER":
                check_cancel(cancel)
                if time.monotonic() >= deadline:
                    raise CloudError("等待接收方超时。")
                time.sleep(0.15)
                transfer = self.request("GET", f"/{tid}")
            if transfer["state"] not in {"ACCEPTED", "CONNECTING"}:
                raise CloudError("接收未成功：" + transfer["state"])
            headers = {
                "Authorization": "Bearer " + self.control.token,
                "Content-Type": "application/octet-stream",
            }
            chunk_size = config["chunk_bytes"]
            with (
                httpx.Client(timeout=httpx.Timeout(45, connect=5), trust_env=False) as client,
                path.open("rb") as source,
            ):
                sent = 0
                for seq in range(max(1, (before.st_size + chunk_size - 1) // chunk_size)):
                    check_cancel(cancel)
                    block = source.read(chunk_size)
                    response = client.post(
                        f"{self.control.server}/api/transfers/{tid}/chunks/{seq}",
                        headers=headers,
                        content=block,
                    )
                    self.check_response(response)
                    sent += len(block)
                    if progress:
                        progress(sent, before.st_size)
            while True:
                check_cancel(cancel)
                transfer = self.request("GET", f"/{tid}")
                if transfer["state"] in TERMINAL:
                    if transfer["state"] != "COMPLETED":
                        raise CloudError(f"传输 {transfer['state']}：{transfer['error']}")
                    return transfer
                if time.monotonic() >= deadline:
                    raise CloudError("接收端尚未确认，已到期限。")
                time.sleep(0.15)
        except Cancelled:
            self.cancel(tid)
            raise
        except Exception as exc:
            self.fail(tid, type(exc).__name__ if isinstance(exc, OSError) else str(exc))
            raise

    def receive(
        self, transfer_id: str, destination: Path, cancel: Event | None = None, progress=None
    ):
        valid_name(destination.name)
        reject_links(destination.absolute())
        temporary = destination.parent / f".filemind-receive-{transfer_id}.part"
        opened, published = False, False
        try:
            if destination.exists():
                raise FileExistsError("保存位置已存在，拒绝覆盖。")
            destination.parent.mkdir(parents=True, exist_ok=True)
            transfer = self.request("GET", f"/{transfer_id}")
            config = self.request("GET", "/config")
            digest, received = hashlib.sha256(), 0
            with temporary.open("xb") as output:
                opened = True
                self.request("POST", f"/{transfer_id}/accept")
                headers = {"Authorization": "Bearer " + self.control.token}
                chunk_size = config["chunk_bytes"]
                with httpx.Client(timeout=httpx.Timeout(45, connect=5), trust_env=False) as client:
                    for seq in range(
                        max(1, (transfer["size_bytes"] + chunk_size - 1) // chunk_size)
                    ):
                        check_cancel(cancel)
                        count = 0
                        expected = min(
                            chunk_size, max(0, transfer["size_bytes"] - seq * chunk_size)
                        )
                        with client.stream(
                            "GET",
                            f"{self.control.server}/api/transfers/{transfer_id}/chunks/{seq}",
                            headers=headers,
                        ) as response:
                            if not response.is_success:
                                response.read()
                                self.check_response(response)
                            for block in response.iter_bytes(65536):
                                check_cancel(cancel)
                                count += len(block)
                                if count > expected:
                                    raise CloudError("接收块超过声明长度。")
                                output.write(block)
                                digest.update(block)
                                received += len(block)
                                if progress:
                                    progress(received, transfer["size_bytes"])
                        if count != expected:
                            raise CloudError("接收块长度不一致。")
                output.flush()
                import os

                os.fsync(output.fileno())
            if (
                received != transfer["size_bytes"]
                or digest.hexdigest() != transfer["expected_hash"]
            ):
                raise CloudError("接收文件 SHA-256 或大小不匹配。")
            check_cancel(cancel)
            commit_without_replace(temporary, destination)
            published = True
            return self.request(
                "POST",
                f"/{transfer_id}/ack",
                {"sha256": digest.hexdigest(), "confirmation": "written_file"},
            )
        except Cancelled:
            self.cancel(transfer_id)
            raise
        except Exception as exc:
            row = self.fail(
                transfer_id, type(exc).__name__ if isinstance(exc, OSError) else str(exc)
            )
            if published:
                if row and row["state"] == "COMPLETED":
                    return row
                raise CloudError(
                    "文件已保存，但服务端确认失败；请核对传输历史，勿覆盖重传。"
                ) from None
            raise
        finally:
            if opened and temporary.exists():
                temporary.unlink()
