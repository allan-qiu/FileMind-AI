"""Bounded, loopback-only synthetic relay resource check. No production access."""

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Queue

import httpx
import psutil

from filemind.services.cloud import CloudClient
from filemind.services.devicelink import DeviceLinkClient


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mib", type=int, choices=[1, 8, 10, 64, 128, 256], default=8)
    parser.add_argument("--pairs", type=int, choices=[1, 2], default=2)
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    root = project / ".artifacts" / ("relay-benchmark-" + uuid.uuid4().hex[:8])
    root.mkdir(parents=True)
    with socket.socket() as socket_probe:
        socket_probe.bind(("127.0.0.1", 0))
        port = socket_probe.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        "FILEMIND_SERVER_DATA": str(root / "central"),
        "FILEMIND_TRANSFER_MAX_BYTES": str(256 * 1024 * 1024),
        "FILEMIND_TRANSFER_TTL": "900",
        "FILEMIND_TRANSFER_BYTES_PER_SECOND": str(32 * 1024 * 1024),
        "FILEMIND_ALLOW_PAID_AI": "false",
        "FILEMIND_DEEPSEEK_API_KEY": "",
        "FILEMIND_BAIDU_REMOTE_ENABLED": "false",
        "FILEMIND_BAIDU_WRITES_ENABLED": "false",
    }
    with (root / "server.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "filemind_server", "serve", "--port", str(port)],
            cwd=project,
            env=env,
            stdout=log,
            stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            with httpx.Client(trust_env=False, timeout=1) as http:
                for _ in range(100):
                    if process.poll() is not None:
                        raise RuntimeError("Benchmark child exited before readiness")
                    try:
                        if http.get(url + "/api/health").is_success:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.1)
                else:
                    raise TimeoutError("Local benchmark readiness timeout")
            account = CloudClient(url)
            credentials = {"username": "resource_test", "password": "isolated-benchmark-only"}
            account.request(
                "POST", "/auth/register", {**credentials, "display_name": "Resource QA"}
            )
            account.token = account.login(**credentials)["access_token"]
            devices = [account.register_device(f"resource-{i}") for i in range(4)]
            source = root / "source.bin"
            with source.open("wb") as stream:
                for _ in range(args.mib * 4):
                    stream.write(bytes(range(256)) * 1024)
            child = psutil.Process(process.pid)

            def resources():
                # Windows venv python.exe may be a launcher with the real interpreter child.
                tree = [child, *child.children(recursive=True)]
                rss = sum(p.memory_info().rss for p in tree)
                cpu = sum(p.cpu_times().user + p.cpu_times().system for p in tree)
                return rss, cpu

            initial_rss, start_cpu = resources()
            peak_rss = initial_rss
            started = time.monotonic()

            def pair(index):
                sender = DeviceLinkClient(url, devices[index * 2]["device_token"])
                receiver = DeviceLinkClient(url, devices[index * 2 + 1]["device_token"])
                receiver.poll()
                created = Queue(maxsize=1)
                with ThreadPoolExecutor(max_workers=1) as sending:
                    future = sending.submit(
                        sender.send,
                        source,
                        devices[index * 2 + 1]["device"]["id"],
                        None,
                        None,
                        created.put,
                    )
                    tid = created.get(timeout=8)
                    incoming = receiver.receive(tid, root / f"received-{index}.bin")
                    outgoing = future.result(timeout=900)
                    assert incoming["state"] == outgoing["state"] == "COMPLETED"
                    return outgoing

            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(pair, i) for i in range(args.pairs)]
                while not all(future.done() for future in futures):
                    peak_rss = max(peak_rss, resources()[0])
                    time.sleep(0.05)
                rows = [future.result() for future in futures]
            elapsed = time.monotonic() - started
            _, end_cpu = resources()
            report = {
                "scope": f"Windows loopback, {args.pairs} x {args.mib} MiB, server process tree",
                "forwarded_bytes": sum(row["bytes_forwarded"] for row in rows),
                "seconds": round(elapsed, 3),
                "server_initial_rss_bytes": initial_rss,
                "server_peak_rss_bytes": peak_rss,
                "server_cpu_seconds": round(end_cpu - start_cpu, 3),
                "central_disk_bytes": sum(
                    p.stat().st_size for p in (root / "central").rglob("*") if p.is_file()
                ),
                "config": {"concurrency": 2, "chunk_bytes": 262144, "bytes_per_second": 33554432},
                "completed": len(rows),
            }
            assert report["forwarded_bytes"] == args.mib * args.pairs * 1024 * 1024
            assert report["central_disk_bytes"] < 2 * 1024 * 1024
            (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(json.dumps(report))
            print(root)
        finally:
            # This is only the child created above, never an unrelated listener or service.
            owned = psutil.Process(process.pid)
            descendants = owned.children(recursive=True)
            for member in reversed(descendants):
                member.terminate()
            psutil.wait_procs(descendants, timeout=10)
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=10)


if __name__ == "__main__":
    main()
