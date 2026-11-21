"""Real loopback TCP archive/resource/worker-restart verification. Synthetic data only."""

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Queue

import httpx
import psutil

from filemind.infrastructure.file_analysis import Cancelled
from filemind.services.archive import ArchiveClient
from filemind.services.cloud import CloudClient
from filemind.services.devicelink import DeviceLinkClient
from filemind_server.accounts import create_account
from filemind_server.ai import AISettings
from filemind_server.ai_control import save_config
from filemind_server.archives import ArchivePreferences, ArchiveService, ArchiveSettings
from filemind_server.config import Settings
from filemind_server.database import CentralDatabase
from filemind_server.models import Registration


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser-qa", action="store_true")
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    root = project / ".artifacts" / ("archive-live-" + uuid.uuid4().hex[:8])
    root.mkdir(parents=True)
    db = CentralDatabase(root / "central" / "central.sqlite3")
    db.migrate()
    with db.transaction(True) as conn:
        user = create_account(
            conn,
            Registration(
                username="archive_qa",
                display_name="归档联调用户",
                password="Synthetic-archive-QA-2026",
            ),
        )
        create_account(
            conn,
            Registration(
                username="archive_admin",
                display_name="归档联调管理员",
                password="Synthetic-archive-QA-2026",
            ),
            role="system_admin",
        )
    save_config(db, "ai_v2_2", AISettings(provider="mock"))
    save_config(db, "archive_v2_2", ArchiveSettings(concurrency=2))
    service = ArchiveService(db, Settings(data_dir=root / "central"), None)
    service.set_preferences(user["id"], ArchivePreferences(auto_archive=True, ai_mode="metadata"))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        "FILEMIND_SERVER_DATA": str(root / "central"),
        "FILEMIND_ALLOW_PAID_AI": "false",
        "FILEMIND_DEEPSEEK_API_KEY": "",
        "FILEMIND_ALLOWED_ORIGINS": url,
    }
    process = None
    log = (root / "server.log").open("w", encoding="utf-8")

    def start():
        child = subprocess.Popen(
            [sys.executable, "-m", "filemind_server", "serve", "--port", str(port)],
            cwd=project,
            env=env,
            stdout=log,
            stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        with httpx.Client(trust_env=False, timeout=1) as client:
            for _ in range(100):
                if child.poll() is not None:
                    raise RuntimeError("QA server exited")
                try:
                    if client.get(url + "/api/health").is_success:
                        return child
                except httpx.HTTPError:
                    pass
                time.sleep(0.1)
        raise TimeoutError("QA server readiness")

    def stop(child):
        if child and child.poll() is None:
            owned = psutil.Process(child.pid)
            members = owned.children(recursive=True)
            for member in reversed(members):
                member.terminate()
            psutil.wait_procs(members, timeout=10)
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=10)

    def wait_complete(client, job_id):
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            row = client.request("GET", f"/archive/jobs/{job_id}")
            if row["state"] == "COMPLETED":
                return row
            if row["state"] in {"FAILED", "PLAN_REVIEW_REQUIRED", "EXPIRED"}:
                raise RuntimeError("QA job failed: " + row["error"])
            time.sleep(0.1)
        raise TimeoutError("Archive completion")

    try:
        process = start()
        account = CloudClient(url)
        account.token = account.login("archive_qa", "Synthetic-archive-QA-2026")["access_token"]
        archive = ArchiveClient(url, account.token)
        source = root / "synthetic-notes.txt"
        with source.open("wb") as stream:
            for _ in range(32):
                stream.write(b"Synthetic Python study notes.\n".ljust(262144, b" "))
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        job = account.request(
            "POST",
            "/archive/jobs",
            {
                "filename": source.name,
                "size_bytes": source.stat().st_size,
                "sha256": digest,
                "idempotency_key": str(uuid.uuid4()),
            },
        )
        lease = account.request("POST", f"/archive/jobs/{job['id']}/claim")
        with httpx.Client(trust_env=False) as client, source.open("rb") as stream:
            response = client.put(
                url + f"/api/archive/jobs/{job['id']}/chunks/0",
                headers={
                    "Authorization": "Bearer " + account.token,
                    "X-Archive-Lease": lease["lease_token"],
                },
                content=stream.read(262144),
            )
            assert response.is_success
        stop(process)
        process = start()  # actual independent Uvicorn/Python process restart
        resume = account.request("POST", f"/archive/jobs/{job['id']}/claim")
        assert resume["offset"] == 262144
        child = psutil.Process(process.pid)
        stop_sampling = threading.Event()
        samples = []

        def resources():
            tree = [child, *child.children(recursive=True)]
            return (
                sum(p.memory_info().rss for p in tree),
                sum(p.cpu_times().user + p.cpu_times().system for p in tree),
            )

        initial_rss, initial_cpu = resources()

        def sample():
            while not stop_sampling.wait(0.02):
                try:
                    rss, cpu = resources()
                    staging = sum(
                        p.stat().st_size for p in (root / "central" / "staging").glob("*.part")
                    )
                    temporary = sum(
                        p.stat().st_size
                        for p in (root / "central" / "storage" / ".uploads").glob("*")
                    )
                    with db.transaction() as conn:
                        active_workers = conn.execute(
                            "SELECT count(*) FROM archive_jobs WHERE worker_expires_at>?",
                            (time.time(),),
                        ).fetchone()[0]
                        active_uploads = conn.execute(
                            "SELECT count(*) FROM archive_jobs WHERE state='UPLOADING' "
                            "AND lease_expires_at>?",
                            (time.time(),),
                        ).fetchone()[0]
                    samples.append((rss, cpu, staging + temporary, active_workers, active_uploads))
                except (OSError, psutil.Error):
                    pass

        sampler = threading.Thread(target=sample, daemon=True)
        sampler.start()
        started = time.monotonic()
        # Two simultaneous uploads/background archives, then a small queued batch.
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(archive.upload, job, source)
            second = pool.submit(archive.create_upload, source)
            staged = [first.result(), second.result()]
        completed = [wait_complete(account, j["id"]) for j in staged]
        small = root / "queue.txt"
        small.write_text("Synthetic queue notes", encoding="utf-8")
        queued = [archive.create_upload(small) for _ in range(4)]
        completed.extend(wait_complete(account, j["id"]) for j in queued)
        # Independent actual DeviceLink delivery, then receiver-provided archive copy.
        tx_registration, rx_registration = (
            account.register_device("QA sender"),
            account.register_device("QA receiver"),
        )
        tx = DeviceLinkClient(url, tx_registration["device_token"])
        rx = DeviceLinkClient(url, rx_registration["device_token"])
        rx.poll()
        created = Queue(maxsize=1)
        target = root / "received-queue.txt"
        with ThreadPoolExecutor(max_workers=1) as pool:
            sending = pool.submit(
                tx.send, small, rx_registration["device"]["id"], None, None, created.put
            )
            tid = created.get(timeout=10)
            delivered = rx.receive(tid, target)
            assert sending.result(30)["state"] == delivered["state"] == "COMPLETED"
        receiver_archive = ArchiveClient(url, rx_registration["device_token"])
        result = receiver_archive.auto_from_transfer(delivered, target)
        completed.append(wait_complete(account, result["id"]))
        large_cancel = root / "cancel-128MiB.bin"
        with large_cancel.open("wb") as stream:
            stream.truncate(128 * 1024 * 1024)
        cancellation = threading.Event()
        try:
            archive.create_upload(
                large_cancel,
                cancellation,
                lambda done, total: cancellation.set() if done >= 262144 else None,
            )
            raise AssertionError("Large upload should pause after its first acknowledged chunk")
        except Cancelled:
            pass
        paused = next(j for j in archive.jobs() if j["filename"] == large_cancel.name)
        assert paused["uploaded_bytes"] == 262144 and paused["state"] == "UPLOADING"
        cancelled = account.request("POST", f"/archive/jobs/{paused['id']}/cancel")
        assert cancelled["state"] == "CANCELLED"
        elapsed = time.monotonic() - started
        stop_sampling.set()
        sampler.join(timeout=2)
        _, end_cpu = resources()
        with db.transaction() as conn:
            ai_rows = [
                dict(r)
                for r in conn.execute(
                    "SELECT simulated,input_tokens,output_tokens,created_at,finished_at "
                    "FROM ai_usage_records"
                )
            ]
            failures = conn.execute(
                "SELECT count(*) FROM archive_jobs WHERE state IN ('FAILED','RETRY_WAIT')"
            ).fetchone()[0]
            pending_temp = conn.execute(
                "SELECT count(*) FROM ingest_sessions WHERE state!='CLEANED'"
            ).fetchone()[0]
        catalog = account.request("GET", "/library")
        assert len(catalog) == 7 and all(row["simulated"] for row in ai_rows)
        assert pending_temp == 0
        report = {
            "scope": "Windows loopback real TCP, synthetic files, Mock AI, real local storage",
            "completed": len(completed),
            "queued_small_files": 4,
            "concurrent_archive_setting": 2,
            "peak_sampled_active_workers": max([0, *[s[3] for s in samples]]),
            "peak_sampled_active_uploads": max([0, *[s[4] for s in samples]]),
            "real_server_restart": True,
            "persisted_resume_offset": resume["offset"],
            "devicelink_then_receiver_archive": "COMPLETED",
            "catalog_files": len(catalog),
            "elapsed_seconds": round(elapsed, 3),
            "server_initial_rss_bytes": initial_rss,
            "server_peak_rss_bytes": max([initial_rss, *[s[0] for s in samples]]),
            "server_cpu_seconds": round(end_cpu - initial_cpu, 3),
            "peak_sampled_temporary_disk_bytes": max([0, *[s[2] for s in samples]]),
            "pending_temporary_objects": pending_temp,
            "failure_count": failures,
            "large_upload_cancel": {
                "source_bytes": 128 * 1024 * 1024,
                "acknowledged_bytes": 262144,
                "final_state": "CANCELLED",
            },
            "mock_ai_seconds": [round(r["finished_at"] - r["created_at"], 4) for r in ai_rows],
            "real_deepseek_called": False,
            "baidu_connected": False,
        }
        (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report), flush=True)
        print("QA_READY " + url + " " + str(root), flush=True)
        if args.browser_qa:
            deadline = time.monotonic() + 900
            while time.monotonic() < deadline and not (root / "stop-qa").exists():
                time.sleep(0.5)
    finally:
        stop(process)
        log.close()


if __name__ == "__main__":
    main()
