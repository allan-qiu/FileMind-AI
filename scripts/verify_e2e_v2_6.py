"""One-file native-client → relay → archive → library → anonymous share, real TCP.

Default: Mock AI. --approved-live uses the SAME bounded V2.6 paid ledger and lock.
No tokens, URLs with secrets, private files, or databases are exported in the report.
"""

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from pathlib import Path
from queue import Queue

import httpx
import psutil

from filemind.core.paths import WorkspacePaths
from filemind.services.application import ApplicationService
from filemind.services.archive import ArchiveClient
from filemind.services.cloud import CloudClient
from filemind.services.devicelink import DeviceLinkClient
from filemind.services.library import LibraryClient
from filemind.services.local_organizer import target_path
from filemind.services.share import ShareClient
from filemind_server.accounts import create_account
from filemind_server.ai import AISettings
from filemind_server.ai_control import save_config
from filemind_server.config import Settings
from filemind_server.database import CentralDatabase
from filemind_server.key_store import KeyStore
from filemind_server.models import Registration
from filemind_server.service_lock import ServiceLock

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--approved-live", action="store_true")
    args = parser.parse_args()
    root = ROOT / ".artifacts" / ("v26-e2e-" + uuid.uuid4().hex[:8])
    root.mkdir(parents=True)
    paid_root = ROOT / ".artifacts/v26-deepseek-live"
    if args.approved_live:
        if not (paid_root / "central/central.sqlite3").is_file():
            raise SystemExit("Run the explicitly approved eight-case acceptance first.")
        key = KeyStore(Settings.from_env().data_dir).get()
        if not key:
            raise SystemExit("NOT_CONFIGURED")
    else:
        key = ""
    with ServiceLock(paid_root / "acceptance.lock") if args.approved_live else nullcontext():
        data = paid_root / "central" if args.approved_live else root / "central"
        db = CentralDatabase(data / "central.sqlite3")
        db.migrate()
        with db.transaction() as conn:
            attempts = conn.execute("SELECT count(*) FROM ai_usage_records").fetchone()[0]
        if args.approved_live and attempts >= 20:
            raise SystemExit("Approval request cap exhausted; no API call sent.")
        username, password = "e2e_" + uuid.uuid4().hex[:10], "Synthetic-E2E-V26-only"
        with db.transaction(True) as conn:
            create_account(
                conn,
                Registration(username=username, display_name="Synthetic E2E", password=password),
            )
        save_config(
            db,
            "ai_v2_2",
            AISettings(
                provider="deepseek" if args.approved_live else "mock",
                retries=0,
                monthly_budget=4 if args.approved_live else 0,
                request_limit=0.2 if args.approved_live else 0,
                input_per_million=2,
                output_per_million=8,
                price_version="official-2026-10-31-peak-upper",
            ),
        )
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        url = f"http://127.0.0.1:{port}"
        env = {
            **os.environ,
            "FILEMIND_SERVER_DATA_ROOT": str(ROOT),
            "FILEMIND_SERVER_DATA": str(data),
            "FILEMIND_RELAY_ENABLED": "true",
            "FILEMIND_ALLOW_PAID_AI": str(args.approved_live).lower(),
            "FILEMIND_ARCHIVE_WORKER": "true",
            "FILEMIND_DEEPSEEK_API_KEY": key,
            "FILEMIND_BAIDU_FORMAL_AUTHORIZED": "false",
            "FILEMIND_BAIDU_REMOTE_ENABLED": "false",
            "FILEMIND_BAIDU_WRITES_ENABLED": "false",
            "FILEMIND_ALLOWED_ORIGINS": url,
        }
        report = {
            "scope": "Windows native client services; one physical host; real loopback TCP",
            "ai": "DeepSeek live" if args.approved_live else "Mock",
            "storage": "LocalStorageProvider real files",
            "production": False,
            "real_baidu": False,
            "physical_mobile": False,
            "steps": {},
            "checked_at": time.time(),
        }
        with (root / "server.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                [sys.executable, "-m", "filemind_server", "serve", "--port", str(port)],
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            try:
                with httpx.Client(timeout=30, trust_env=False) as http:
                    for _ in range(100):
                        if process.poll() is not None:
                            raise RuntimeError("Isolated server exited")
                        try:
                            if http.get(url + "/api/health").is_success:
                                break
                        except httpx.HTTPError:
                            pass
                        time.sleep(0.1)
                    else:
                        raise TimeoutError("Isolated server readiness")
                    account = CloudClient(url)
                    account.token = account.login(username, password)["access_token"]
                    account.request(
                        "PUT",
                        "/archive/preferences",
                        {"auto_archive": True, "ai_mode": "summary", "rules": ""},
                    )
                    devices = [
                        account.register_device("Windows logical " + str(i)) for i in range(3)
                    ]
                    source = root / "Python-course-notes-V26.md"
                    source.write_text(
                        "# Python course notes\nFunctions, classes, pytest, "
                        "type hints, generators, decorators and unit tests.\n",
                        encoding="utf-8",
                    )
                    digest = hashlib.sha256(source.read_bytes()).hexdigest()
                    desktop = ApplicationService.create(WorkspacePaths(root / "desktop"))
                    scan = desktop.scanner.scan_many([source])
                    plan = desktop.classification.plan(scan.files, root / "organized")
                    result = desktop.local.execute(plan, confirmed=True)
                    assert result["status"] == "COMPLETED"
                    source = target_path(plan.entries[0])
                    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
                    report["steps"]["local_organize"] = "CONFIRMED_MOVE_HASH_MATCH"
                    tx, rx = (DeviceLinkClient(url, d["device_token"]) for d in devices[:2])
                    rx.poll()
                    created = Queue(maxsize=1)
                    received = root / "received" / source.name
                    received.parent.mkdir()
                    with ThreadPoolExecutor(max_workers=1) as pool:
                        sent = pool.submit(
                            tx.send, source, devices[1]["device"]["id"], None, None, created.put
                        )
                        tid = created.get(timeout=10)
                        incoming = rx.receive(tid, received)
                        assert sent.result(30)["state"] == incoming["state"] == "COMPLETED"
                    assert hashlib.sha256(received.read_bytes()).hexdigest() == digest
                    report["steps"]["devicelink"] = "COMPLETED_SHA256_MATCH"
                    archiver = ArchiveClient(url, devices[1]["device_token"])
                    job = archiver.auto_from_transfer(incoming, received)
                    for _ in range(120):
                        job = account.request("GET", "/archive/jobs/" + job["id"])
                        if job["state"] in ("COMPLETED", "PLAN_REVIEW_REQUIRED", "FAILED"):
                            break
                        time.sleep(0.5)
                    report["steps"]["archive"] = job["state"]
                    assert job["state"] == "COMPLETED", "Live plan may require manual review"
                    library = LibraryClient(url, devices[2]["device_token"])
                    synced = library.sync()
                    item = next(r for r in synced["items"] if r["version_id"] == job["id"])
                    assert library.search(q="Python")["items"]
                    downloaded = root / "on-demand.md"
                    version = next(
                        v
                        for v in library.detail(item["file_id"])["versions"]
                        if v["id"] == item["version_id"]
                    )
                    library.download(version, downloaded)
                    assert hashlib.sha256(downloaded.read_bytes()).hexdigest() == digest
                    report["steps"]["library_sync_download"] = "SYNCED_SHA256_MATCH"
                    links = [
                        ShareClient(url, account.token).create(
                            {"file_version_id": item["version_id"]}, 2, 3600
                        )
                        for _ in range(2)
                    ]
                    verify = http.post(
                        url + "/api/public/shares/verify",
                        json={"token": links[0]["share_url"].split("#")[1]},
                    )
                    verify.raise_for_status()
                    grant = verify.json()["grant"]

                    def reserve(_):
                        secret = uuid.uuid4().hex + uuid.uuid4().hex
                        r = http.post(
                            url + "/api/public/shares/downloads",
                            json={
                                "grant": grant,
                                "request_key": str(uuid.uuid4()),
                                "session_secret": secret,
                            },
                        )
                        return r, secret

                    with ThreadPoolExecutor(max_workers=8) as pool:
                        attempts = list(pool.map(reserve, range(8)))
                    winners = [(r.json(), s) for r, s in attempts if r.status_code == 201]
                    assert len(winners) == 2
                    assert all(r.status_code in (201, 409, 429) for r, _ in attempts)
                    for r, secret in winners:
                        res = http.get(
                            url + "/api/public/downloads/" + r["session_id"] + "/content",
                            headers={"Authorization": "Bearer " + secret},
                        )
                        res.raise_for_status()
                        assert hashlib.sha256(res.content).hexdigest() == digest
                    for _ in range(30):
                        stats = account.request("GET", "/shares/" + links[0]["share_id"])
                        if stats["completed_downloads"] == 2:
                            break
                        time.sleep(0.1)
                    assert stats["completed_downloads"] == 2
                    other = account.request("GET", "/shares/" + links[1]["share_id"])
                    assert other["completed_downloads"] == 0
                    report["steps"]["public_share"] = (
                        "2_OF_8_RESERVED_2_HASH_MATCH_INDEPENDENT_LINK_ZERO"
                    )
                    assert all(
                        r["state"] == "UNDONE" for r in desktop.local.undo(plan.id, confirmed=True)
                    )
                    report["steps"]["local_undo"] = "UNDONE_ORIGINAL_HASH_MATCH"
                    assert (
                        hashlib.sha256(Path(plan.entries[0].file.path).read_bytes()).hexdigest()
                        == digest
                    )
                    assert tx.request("GET", "/" + tid)["state"] == "COMPLETED"
                    with db.transaction() as conn:
                        report["database_integrity"] = conn.execute(
                            "PRAGMA integrity_check"
                        ).fetchone()[0]
                        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
                        report["ledger"] = dict(
                            conn.execute(
                                "SELECT count(*) attempts,sum(input_tokens) input_tokens,"
                                "sum(output_tokens) output_tokens,"
                                "sum(estimated_cost) estimated_cost "
                                "FROM ai_usage_records WHERE simulated=0"
                            ).fetchone()
                        )
                    report["all_passed"] = True
            finally:
                try:
                    children = psutil.Process(process.pid).children(recursive=True)
                    for p in reversed(children):
                        p.terminate()
                    psutil.wait_procs(children, timeout=10)
                except psutil.NoSuchProcess:
                    pass
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)
                (root / "report.json").write_text(
                    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
                )
        print(json.dumps({"report": str(root / "report.json"), **report}, ensure_ascii=False))


if __name__ == "__main__":
    main()
