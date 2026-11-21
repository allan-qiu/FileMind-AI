"""V2.4 isolated real TCP/process-restart/streaming and optional browser acceptance."""

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

import httpx
import psutil

from filemind.infrastructure.file_analysis import content_hash
from filemind.services.archive import ArchiveClient
from filemind.services.cloud import CloudClient
from filemind.services.library import LibraryClient
from filemind.services.share import ShareClient
from filemind_server.accounts import create_account
from filemind_server.ai import AISettings
from filemind_server.ai_control import save_config
from filemind_server.archives import ArchivePreferences, ArchiveService
from filemind_server.config import Settings
from filemind_server.database import CentralDatabase
from filemind_server.models import Registration
from filemind_server.security import new_token
from filemind_server.share_config import ShareSettings, save


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser-qa", action="store_true")
    parser.add_argument("--node-modules", type=Path)
    parser.add_argument("--megabytes", type=int, default=64)
    args = parser.parse_args()
    if not 1 <= args.megabytes <= 128:
        raise ValueError("Local synthetic file size 1–128 MiB")
    project = Path(__file__).resolve().parents[1]
    root = project / ".artifacts" / ("share-live-" + uuid.uuid4().hex[:8])
    root.mkdir(parents=True)
    data = root / "central"
    db = CentralDatabase(data / "central.sqlite3")
    db.migrate()
    password = "Synthetic-share-QA-2026"
    with db.transaction(True) as conn:
        user = create_account(
            conn, Registration(username="share_qa", display_name="分享联调", password=password)
        )
        create_account(
            conn,
            Registration(username="share_admin", display_name="分享管理员", password=password),
            "system_admin",
        )
    save_config(db, "ai_v2_2", AISettings(provider="mock"))
    save(
        db,
        ShareSettings(
            max_concurrency=8,
            user_concurrency=8,
            share_concurrency=8,
            bytes_per_second=16 * 1024 * 1024,
        ),
    )
    ArchiveService(db, Settings(data_dir=data), None).set_preferences(
        user["id"], ArchivePreferences(auto_archive=False, ai_mode="metadata")
    )
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        "FILEMIND_SERVER_DATA": str(data),
        "FILEMIND_ALLOW_PAID_AI": "false",
        "FILEMIND_DEEPSEEK_API_KEY": "",
        "FILEMIND_ALLOWED_ORIGINS": url,
    }
    log = (root / "server.log").open("w", encoding="utf-8")
    process = None
    stop_sample = threading.Event()
    samples = []

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
                    raise RuntimeError("QA service exited")
                try:
                    if client.get(url + "/api/health").is_success:
                        return child
                except httpx.HTTPError:
                    pass
                time.sleep(0.1)
        raise TimeoutError("QA startup")

    def stop(child):
        if child and child.poll() is None:
            parent = psutil.Process(child.pid)
            family = [parent, *parent.children(recursive=True)]
            for p in reversed(family):
                try:
                    p.terminate()
                except psutil.NoSuchProcess:
                    pass
            _, alive = psutil.wait_procs(family, timeout=5)
            for p in alive:
                try:
                    p.kill()
                except psutil.NoSuchProcess:
                    pass
            child.wait(timeout=5)

    def sample():
        while not stop_sample.wait(0.05):
            try:
                p = psutil.Process(process.pid)
                samples.append(sum(x.memory_info().rss for x in [p, *p.children(recursive=True)]))
            except (psutil.Error, AttributeError):
                pass

    report = {
        "version": "2.4.0",
        "transport": "real loopback TCP",
        "storage": "real local bytes",
        "ai": "MockProvider for archive regression only",
        "real_deepseek": False,
        "real_baidu": False,
        "physical_devices": False,
        "production": False,
    }
    try:
        process = start()
        threading.Thread(target=sample, daemon=True).start()
        account = CloudClient(url)
        account.token = account.login("share_qa", password)["access_token"]
        owner = ShareClient(url, account.token)
        with httpx.Client(base_url=url, trust_env=False, timeout=40) as client:

            def verify(link):
                r = client.post(
                    "/api/public/shares/verify", json={"token": link["share_url"].split("#")[1]}
                )
                assert r.is_success, r.text
                return r.json()

            def reserve(grant):
                secret = new_token()
                r = client.post(
                    "/api/public/shares/downloads",
                    json={
                        "grant": grant,
                        "request_key": str(uuid.uuid4()),
                        "session_secret": secret,
                    },
                )
                return r, {"Authorization": "Bearer " + secret}

            def wait_completed(sid, headers):
                for _ in range(60):
                    r = client.get("/api/public/downloads/" + sid, headers=headers).json()
                    if r["status"] == "COMPLETED":
                        return r
                    time.sleep(0.05)
                raise AssertionError("Delivery not committed")

            def download(link):
                info = verify(link)
                r, headers = reserve(info["grant"])
                assert r.status_code == 201, r.text
                sid = r.json()["session_id"]
                res = client.get("/api/public/downloads/" + sid + "/content", headers=headers)
                assert res.is_success, res.text[:80]
                wait_completed(sid, headers)
                digest = hashlib.sha256(res.content).hexdigest()
                assert digest == info["sha256"]
                for _ in range(2):
                    assert client.post(
                        "/api/public/downloads/" + sid + "/complete",
                        headers=headers,
                        json={"sha256": digest},
                    ).is_success
                assert (
                    client.get(
                        "/api/public/downloads/" + sid + "/content", headers=headers
                    ).status_code
                    == 410
                )
                return res.content

            small = root / "temporary.txt"
            small.write_bytes(b"FileMind Share end-to-end synthetic content\n" * 100)
            obj = owner.upload(small)
            link = owner.create({"temporary_object_id": obj["object_id"]}, 1, 3600)
            assert verify(link)["remaining_downloads"] == 1
            assert download(link) == small.read_bytes()
            assert owner.request("GET", "/shares/" + link["share_id"])["completed_downloads"] == 1
            with db.transaction(True) as conn:
                conn.execute(
                    "UPDATE share_links SET expires_at=? WHERE share_id=?",
                    (time.time() - 1, link["share_id"]),
                )
            admin_session = CloudClient(url).request(
                "POST", "/admin/auth/login", {"username": "share_admin", "password": password}
            )
            ah = {"Authorization": "Bearer " + admin_session["access_token"]}
            assert client.post("/api/admin/shares/cleanup/run", headers=ah).is_success
            with db.transaction() as conn:
                temp = conn.execute(
                    "SELECT * FROM temporary_share_objects WHERE object_id=?", (obj["object_id"],)
                ).fetchone()
                assert temp["deletion_status"] == "DELETED"
                assert not (data / "share-temporary" / temp["storage_location"]).exists()
            report["loop_A"] = (
                "PASS: upload, independent temp storage, guest, quota, hash, complete, "
                "forced synthetic expiry, cleanup"
            )

            source = root / "Python-history.txt"
            source.write_bytes(b"Python immutable Version 1")
            job = ArchiveClient(url, account.token).create_upload(source)

            def wait_job(jid):
                for _ in range(300):
                    state = owner.request("GET", "/archive/jobs/" + jid)["state"]
                    if state == "COMPLETED":
                        return
                    if state in ("FAILED", "PLAN_REVIEW_REQUIRED"):
                        raise AssertionError(state)
                    time.sleep(0.1)
                raise TimeoutError("Mock archive")

            wait_job(job["id"])
            library = LibraryClient(url, account.token)
            file = library.detail(job["id"])
            vid = file["version_id"]
            links = [owner.create({"file_version_id": vid}, n, 3600) for n in (1, 3)]
            source2 = root / "Python-history-v2.txt"
            source2.write_bytes(b"Python immutable Version 2")
            v2 = library.upload_version(file, source2)
            wait_job(v2["id"])
            for x in links:
                assert download(x) == source.read_bytes()
            owner.request("POST", "/shares/" + links[0]["share_id"] + "/revoke")
            assert download(links[1]) == source.read_bytes()
            assert (
                owner.request("GET", "/shares/" + links[1]["share_id"])["completed_downloads"] == 2
            )
            report["loop_B"] = (
                "PASS: pinned Version 1 after Version 2, A/B independent, hashes, "
                "revoke A retains B"
            )

            race = owner.create({"file_version_id": vid}, 1, 3600)
            grant = verify(race)["grant"]
            with ThreadPoolExecutor(max_workers=10) as pool:
                results = list(pool.map(lambda _: reserve(grant), range(10)))
            winners = [(r, h) for r, h in results if r.status_code == 201]
            assert len(winners) == 1
            r, h = winners[0]
            sid = r.json()["session_id"]
            assert (
                client.get("/api/public/downloads/" + sid + "/content", headers=h).content
                == source.read_bytes()
            )
            wait_completed(sid, h)
            assert owner.request("GET", "/shares/" + race["share_id"])["completed_downloads"] == 1
            report["loop_C"] = {
                "clients": 10,
                "accepted": 1,
                "completed": 1,
                "database_count_verified": True,
            }

            large = root / "large.bin"
            block = (b"FileMind synthetic large stream\n" * 40000)[: 1024**2]
            block += b"x" * (1024**2 - len(block))
            with large.open("xb") as stream:
                for _ in range(args.megabytes):
                    stream.write(block)
            obj = owner.upload(large)
            large_link = owner.create({"temporary_object_id": obj["object_id"]}, 3, 3600)
            grant = verify(large_link)["grant"]
            r, h = reserve(grant)
            sid = r.json()["session_id"]
            digest = hashlib.sha256()
            done = 0
            began = time.perf_counter()
            first = None
            with client.stream(
                "GET", "/api/public/downloads/" + sid + "/content", headers=h
            ) as response:
                assert response.is_success
                for block in response.iter_bytes(262144):
                    if first is None:
                        first = time.perf_counter() - began
                    digest.update(block)
                    done += len(block)
            elapsed = time.perf_counter() - began
            wait_completed(sid, h)
            assert digest.hexdigest() == content_hash(large)
            report["large_stream"] = {
                "bytes": done,
                "first_byte_seconds": round(first, 4),
                "seconds": round(elapsed, 3),
                "sha256_matches": True,
                "whole_file_memory_buffer": False,
            }

            pending = owner.create({"file_version_id": vid}, 1, 3600)
            grant = verify(pending)["grant"]
            r, h = reserve(grant)
            sid = r.json()["session_id"]
            stop(process)
            process = start()
            assert (
                client.get("/api/public/downloads/" + sid, headers=h).json()["status"] == "RESERVED"
            )
            assert (
                client.get("/api/public/downloads/" + sid + "/content", headers=h).content
                == source.read_bytes()
            )
            wait_completed(sid, h)
            report["process_restart_quota"] = (
                "PASS: credentials, reservation and committed count survive "
                "separate process restart"
            )
            with db.transaction() as conn:
                assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                assert not conn.execute("PRAGMA foreign_key_check").fetchall()
                assert (
                    conn.execute(
                        "SELECT count(*) FROM share_links WHERE completed_downloads>max_downloads"
                    ).fetchone()[0]
                    == 0
                )
            report["database_integrity"] = "ok; foreign_key_check empty; zero quota overruns"
            if args.browser_qa:
                node_env = {**os.environ}
                if args.node_modules:
                    node_env["NODE_PATH"] = str(args.node_modules.resolve())
                subprocess.run(
                    ["node", str(project / "scripts/verify_share_browser.cjs"), url, str(root)],
                    cwd=project,
                    env=node_env,
                    check=True,
                    timeout=240,
                )
                report["browser"] = json.loads(
                    (root / "browser-report.json").read_text(encoding="utf-8")
                )
        report["peak_process_family_rss_bytes"] = max(samples, default=0)
        (root / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            json.dumps(
                {"artifact_directory": str(root), "report": report}, ensure_ascii=False, indent=2
            )
        )
    finally:
        stop_sample.set()
        stop(process)
        log.close()


if __name__ == "__main__":
    main()
