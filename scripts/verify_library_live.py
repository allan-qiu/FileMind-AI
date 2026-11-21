"""V2.3 isolated TCP, real bytes, process restart, resources and optional browser QA."""

import argparse
import json
import os
import socket
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import httpx
import psutil

from filemind.infrastructure.file_analysis import Cancelled, content_hash
from filemind.services.archive import ArchiveClient
from filemind.services.cloud import CloudClient, CloudError
from filemind.services.library import LibraryClient
from filemind_server.accounts import create_account
from filemind_server.ai import AISettings
from filemind_server.ai_control import save_config
from filemind_server.archives import ArchivePreferences, ArchiveService
from filemind_server.config import Settings
from filemind_server.database import CentralDatabase
from filemind_server.models import Registration


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser-qa", action="store_true")
    parser.add_argument("--node-modules", type=Path)
    parser.add_argument("--megabytes", type=int, default=64)
    args = parser.parse_args()
    if not 1 <= args.megabytes <= 128:
        raise ValueError("Synthetic local resource bound: 1–128 MiB")
    project = Path(__file__).resolve().parents[1]
    root = project / ".artifacts" / ("library-live-" + uuid.uuid4().hex[:8])
    root.mkdir(parents=True)
    db = CentralDatabase(root / "central" / "central.sqlite3")
    db.migrate()
    password = "Synthetic-library-QA-2026"
    with db.transaction(True) as conn:
        user = create_account(
            conn, Registration(username="library_qa", display_name="文件库联调", password=password)
        )
        create_account(
            conn,
            Registration(username="library_admin", display_name="文件库管理员", password=password),
            "system_admin",
        )
    save_config(db, "ai_v2_2", AISettings(provider="mock"))
    ArchiveService(db, Settings(data_dir=root / "central"), None).set_preferences(
        user["id"], ArchivePreferences(auto_archive=False, ai_mode="metadata")
    )
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    environment = {
        **os.environ,
        "FILEMIND_SERVER_DATA": str(root / "central"),
        "FILEMIND_ALLOW_PAID_AI": "false",
        "FILEMIND_DEEPSEEK_API_KEY": "",
        "FILEMIND_ALLOWED_ORIGINS": url,
    }
    log = (root / "server.log").open("w", encoding="utf-8")
    process, stop_sampling = None, threading.Event()
    samples = []

    def start():
        child = subprocess.Popen(
            [sys.executable, "-m", "filemind_server", "serve", "--port", str(port)],
            cwd=project,
            env=environment,
            stdout=log,
            stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        with httpx.Client(trust_env=False, timeout=1) as client:
            for _ in range(100):
                if child.poll() is not None:
                    raise RuntimeError("QA service exited")
                try:
                    if client.get(url + "/api/health").is_success:
                        return child
                except httpx.HTTPError:
                    pass
                time.sleep(0.1)
        raise TimeoutError("QA readiness")

    def stop(child):
        if child and child.poll() is None:
            parent = psutil.Process(child.pid)
            children = parent.children(recursive=True)
            for descendant in children:
                descendant.terminate()
            child.terminate()
            psutil.wait_procs([parent, *children], timeout=8)
            for target in [parent, *children]:
                if target.is_running():
                    target.kill()
            child.wait(timeout=8)

    def sample():
        while not stop_sampling.wait(0.05):
            try:
                parent = psutil.Process(process.pid)
                samples.append(
                    sum(p.memory_info().rss for p in [parent, *parent.children(recursive=True)])
                )
            except psutil.Error:
                pass

    def wait_job(jid):
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            job = account.request("GET", f"/archive/jobs/{jid}")
            if job["state"] == "COMPLETED":
                return job
            if job["state"] in {"FAILED", "PLAN_REVIEW_REQUIRED"}:
                raise RuntimeError(str({k: job[k] for k in ["id", "state", "error"]}))
            time.sleep(0.1)
        raise TimeoutError("Archive completion")

    report = {
        "version": "2.3.0",
        "transport": "real loopback TCP",
        "ai": "MockProvider",
        "storage": "real local bytes",
        "real_deepseek": False,
        "real_baidu": False,
        "physical_devices": False,
        "production": False,
    }
    try:
        process = start()
        threading.Thread(target=sample, daemon=True).start()
        account = CloudClient(url)
        account.token = account.login("library_qa", password)["access_token"]
        devices = [account.register_device("Library-" + name) for name in ["A", "B"]]
        a, b = [LibraryClient(url, d["device_token"]) for d in devices]
        assert a.sync()["items"] == [] and b.sync()["items"] == []
        source = root / "Python-synthetic-large.txt"
        block = (b"Python synthetic global library bytes\n" * 30000)[: 1024**2]
        block += b"x" * (1024**2 - len(block))
        with source.open("xb") as stream:
            for _ in range(args.megabytes):
                stream.write(block)
        begin = time.perf_counter()
        job = ArchiveClient(url, devices[0]["device_token"]).create_upload(source)
        wait_job(job["id"])
        report["upload_and_archive_seconds"] = round(time.perf_counter() - begin, 3)
        files = b.sync()["items"]
        assert len(files) == 1
        file = b.detail(files[0]["file_id"])
        begin = time.perf_counter()
        saved = b.download(file["versions"][0], root / "B-demand-download.txt")
        report["download_seconds"] = round(time.perf_counter() - begin, 3)
        assert saved["sha256"] == content_hash(source)
        report.update(download_bytes=source.stat().st_size, sha256=saved["sha256"])
        renamed = a.operate(
            a.detail(file["file_id"]),
            {"filename": "跨设备学习笔记.txt", "directory": "学习/笔记", "favorite": True},
        )
        assert b.sync()["items"][0]["filename"] == renamed["filename"]
        deleted = a.operate(renamed, kind="DELETE")
        assert b.sync()["items"][0]["deleted_at"]
        a.operate(deleted, kind="RESTORE")
        assert not b.sync()["items"][0]["deleted_at"]
        before = b.detail(file["file_id"])
        update = root / "Python-version-two.txt"
        update.write_text("Python revised synthetic content\n" * 100, encoding="utf-8")
        v2 = a.upload_version(a.detail(file["file_id"]), update)
        wait_job(v2["id"])
        conflict_source = root / "Python-conflict.txt"
        conflict_source.write_text("Python candidate based on old version", encoding="utf-8")
        candidate = b.upload_version(before, conflict_source)
        wait_job(candidate["id"])
        conflicts = account.request("GET", "/library/conflicts")
        assert len(conflicts) == 1
        current = a.detail(file["file_id"])
        account.request(
            "POST",
            f"/library/conflicts/{conflicts[0]['id']}/resolve",
            {
                "action": "candidate",
                "expected_revision": current["revision"],
                "idempotency_key": str(uuid.uuid4()),
            },
        )
        old = next(v for v in b.detail(file["file_id"])["versions"] if v["version"] == 1)
        cancelled = threading.Event()
        try:
            b.download(
                old,
                root / "cancelled-must-not-exist.txt",
                cancelled,
                lambda n, total: cancelled.set(),
            )
            raise AssertionError("Cancellation did not interrupt")
        except Cancelled:
            pass
        assert not (root / "cancelled-must-not-exist.txt").exists()
        account.revoke_device(devices[0]["device"]["id"])
        try:
            a.sync()
            raise AssertionError("Revoked device accepted")
        except CloudError as exc:
            assert exc.status == 401 and a.cache.rows() == []
        stop(process)
        process = start()
        after_restart = b.sync()["items"]
        assert after_restart[0]["version_id"] == candidate["id"]
        report.update(
            logical_devices=2,
            rename_delete_restore=True,
            versions=3,
            conflict_resolved=True,
            cancellation=True,
            revoked_device_rejected=True,
            process_restart=True,
            peak_server_process_tree_rss_bytes=max(samples),
            sampled_interval_ms=50,
        )
        (root / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if args.browser_qa:
            env = {**os.environ}
            if args.node_modules:
                env["NODE_PATH"] = str(args.node_modules)
            subprocess.run(
                ["node", str(project / "scripts/verify_library_browser.cjs"), url, str(root)],
                cwd=project,
                env=env,
                check=True,
                timeout=180,
            )
            report["browser"] = json.loads((root / "browser-report.json").read_text())
        (root / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps({"output": str(root), **report}, ensure_ascii=False))
    finally:
        stop_sampling.set()
        stop(process)
        log.close()


if __name__ == "__main__":
    main()
