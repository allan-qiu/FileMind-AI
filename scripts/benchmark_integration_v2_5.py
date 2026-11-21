"""Six-size, loopback-only, real-file archive/library/share benchmark. No cloud access."""

import hashlib
import json
import os
import platform
import socket
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import psutil

from filemind.services.archive import ArchiveClient
from filemind.services.cloud import CloudClient
from filemind_server.accounts import create_account
from filemind_server.ai import AISettings
from filemind_server.ai_control import save_config
from filemind_server.archives import ArchivePreferences, ArchiveService
from filemind_server.config import Settings
from filemind_server.database import CentralDatabase
from filemind_server.models import Registration
from filemind_server.share_config import ShareSettings, save

ROOT = Path(__file__).resolve().parents[1]


def main():
    root = ROOT / ".artifacts" / ("integration-benchmark-" + uuid.uuid4().hex[:8])
    root.mkdir(parents=True)
    data_dir = root / "central"
    db = CentralDatabase(data_dir / "central.sqlite3")
    db.migrate()
    password = "Synthetic-V25-benchmark"
    with db.transaction(True) as conn:
        user = create_account(
            conn, Registration(username="v25_bench", display_name="Synthetic", password=password)
        )
    save_config(db, "ai_v2_2", AISettings(provider="mock"))
    save(db, ShareSettings(bytes_per_second=64 * 1024**2))
    ArchiveService(db, Settings(data_dir=data_dir), None).set_preferences(
        user["id"], ArchivePreferences(ai_mode="metadata")
    )
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        "FILEMIND_SERVER_DATA": str(data_dir),
        "FILEMIND_ALLOW_PAID_AI": "false",
        "FILEMIND_DEEPSEEK_API_KEY": "",
        "FILEMIND_BAIDU_REMOTE_ENABLED": "false",
        "FILEMIND_BAIDU_WRITES_ENABLED": "false",
    }
    samples, stop = [], threading.Event()
    report = {
        "date": "2026-10-31",
        "os": platform.platform(),
        "python": platform.python_version(),
        "cpu_logical": psutil.cpu_count(),
        "host_ram_bytes": psutil.virtual_memory().total,
        "network": "127.0.0.1 TCP only; no TLS",
        "physical_devices": 1,
        "provider": "real local filesystem",
        "ai": "Mock metadata only",
        "production": False,
        "sampling_interval_seconds": 0.05,
        "rows": [],
    }
    with (root / "server.log").open("w", encoding="utf-8") as log:
        child = subprocess.Popen(
            [sys.executable, "-m", "filemind_server", "serve", "--port", str(port)],
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )

        def sample():
            while not stop.wait(0.05):
                try:
                    tree = [psutil.Process(child.pid), *psutil.Process(child.pid).children(True)]
                    rss = sum(p.memory_info().rss for p in tree)
                    cpu = sum(p.cpu_times().user + p.cpu_times().system for p in tree)
                    temporary = sum(
                        p.stat().st_size
                        for folder in ("staging", "read-cache")
                        for p in (data_dir / folder).rglob("*")
                        if p.is_file()
                    )
                    samples.append((time.perf_counter(), rss, cpu, temporary))
                except (psutil.Error, OSError):
                    pass

        thread = threading.Thread(target=sample, daemon=True)
        try:
            with httpx.Client(timeout=120, trust_env=False) as http:
                for _ in range(100):
                    if child.poll() is not None:
                        raise RuntimeError("Benchmark service exited")
                    try:
                        if http.get(url + "/api/health").is_success:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.1)
                else:
                    raise TimeoutError("Benchmark startup")
                account = CloudClient(url)
                account.token = account.login("v25_bench", password)["access_token"]
                headers = {"Authorization": "Bearer " + account.token}
                archive = ArchiveClient(url, account.token)
                thread.start()
                for size in (
                    1024,
                    1024**2,
                    10 * 1024**2,
                    64 * 1024**2,
                    128 * 1024**2,
                    256 * 1024**2,
                ):
                    source = root / f"synthetic-{size}.bin"
                    digest = hashlib.sha256()
                    block = bytes(range(256)) * 1024
                    with source.open("xb") as stream:
                        remaining = size
                        while remaining:
                            part = block[: min(len(block), remaining)]
                            stream.write(part)
                            digest.update(part)
                            remaining -= len(part)
                    begin_index, started = len(samples), time.perf_counter()
                    job = archive.create_upload(source)
                    upload_seconds = time.perf_counter() - started
                    deadline = time.monotonic() + 180
                    while time.monotonic() < deadline:
                        job = account.request("GET", "/archive/jobs/" + job["id"])
                        if job["state"] == "COMPLETED":
                            break
                        if job["state"] in {"FAILED", "PLAN_REVIEW_REQUIRED"}:
                            raise AssertionError("Unexpected archive failure: " + job["state"])
                        time.sleep(0.1)
                    else:
                        raise TimeoutError("Archive completion")
                    archive_seconds = time.perf_counter() - started
                    row = {
                        "size_bytes": size,
                        "upload_seconds": round(upload_seconds, 4),
                        "archive_total_seconds": round(archive_seconds, 4),
                    }

                    def download(endpoint, request_headers, size=size, digest=digest):
                        begin, first, got, total = time.perf_counter(), None, hashlib.sha256(), 0
                        with http.stream(
                            "GET", url + endpoint, headers=request_headers
                        ) as response:
                            response.raise_for_status()
                            for data in response.iter_bytes(262144):
                                if first is None:
                                    first = time.perf_counter() - begin
                                got.update(data)
                                total += len(data)
                        elapsed = time.perf_counter() - begin
                        assert total == size and got.hexdigest() == digest.hexdigest()
                        return {
                            "seconds": round(elapsed, 4),
                            "first_byte_seconds": round(first, 4),
                            "mib_per_second": round(size / 1024**2 / elapsed, 3),
                            "sha256_matches": True,
                        }

                    row["library"] = download("/api/library/" + job["id"] + "/content", headers)
                    link = account.request(
                        "POST", "/shares", {"file_version_id": job["id"], "max_downloads": 1}
                    )
                    grant = http.post(
                        url + "/api/public/shares/verify",
                        json={"token": link["share_url"].split("#")[1]},
                    ).json()["grant"]
                    secret = uuid.uuid4().hex + uuid.uuid4().hex
                    reserved = http.post(
                        url + "/api/public/shares/downloads",
                        json={
                            "grant": grant,
                            "request_key": str(uuid.uuid4()),
                            "session_secret": secret,
                        },
                    )
                    reserved.raise_for_status()
                    row["share"] = download(
                        "/api/public/downloads/" + reserved.json()["session_id"] + "/content",
                        {"Authorization": "Bearer " + secret},
                    )
                    deadline = time.monotonic() + 3
                    while time.monotonic() < deadline:
                        count = account.request("GET", "/shares/" + link["share_id"])
                        if count["completed_downloads"] == 1:
                            break
                        time.sleep(0.01)
                    assert count["completed_downloads"] == 1
                    interval = samples[begin_index:]
                    row["peak_rss_bytes"] = max(s[1] for s in interval)
                    row["peak_temp_bytes"] = max(s[3] for s in interval)
                    row["cpu_seconds"] = round(interval[-1][2] - interval[0][2], 4)
                    row["cpu_percent_one_core"] = round(
                        row["cpu_seconds"] / max(0.001, interval[-1][0] - interval[0][0]) * 100, 2
                    )
                    row["sqlite_read_transaction_ms"] = []
                    for _ in range(5):
                        began = time.perf_counter()
                        with db.transaction() as conn:
                            conn.execute("SELECT count(*) FROM archive_jobs").fetchone()
                        row["sqlite_read_transaction_ms"].append(
                            round((time.perf_counter() - began) * 1000, 3)
                        )
                    report["rows"].append(row)
                    print(
                        json.dumps(
                            {"size_bytes": size, "passed": True, "archive_seconds": archive_seconds}
                        ),
                        flush=True,
                    )

                # Same DB, genuinely competing write connections; no production workload.
                def write(index):
                    began = time.perf_counter()
                    with db.transaction(True) as conn:
                        conn.execute(
                            "INSERT OR REPLACE INTO platform_settings VALUES (?,?)",
                            ("benchmark_" + str(index), "synthetic"),
                        )
                    return (time.perf_counter() - began) * 1000

                with ThreadPoolExecutor(max_workers=8) as pool:
                    times = list(pool.map(write, range(64)))
                report["sqlite_competing_writes"] = {
                    "threads": 8,
                    "writes": 64,
                    "lock_failures": 0,
                    "max_ms": round(max(times), 3),
                    "mean_ms": round(sum(times) / len(times), 3),
                }
                with db.transaction() as conn:
                    report["archive_successes"] = conn.execute(
                        "SELECT count(*) FROM archive_jobs WHERE state='COMPLETED'"
                    ).fetchone()[0]
                    report["archive_retries"] = conn.execute(
                        "SELECT sum(retry_count) FROM archive_jobs"
                    ).fetchone()[0]
                    report["mock_ai_response_seconds"] = [
                        round(r[0], 6)
                        for r in conn.execute("SELECT finished_at-created_at FROM ai_usage_records")
                    ]
                    report["database_integrity"] = conn.execute(
                        "PRAGMA integrity_check"
                    ).fetchone()[0]
                report["all_passed"] = True
                (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
                print(str(root), flush=True)
        finally:
            stop.set()
            if thread.is_alive():
                thread.join(timeout=2)
            child.terminate()
            child.wait(timeout=15)


if __name__ == "__main__":
    main()
