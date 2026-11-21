"""Native HTTP client ↔ actual Edge/Chrome UI, on one isolated Windows host."""

import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import httpx

from filemind.services.cloud import CloudClient
from filemind.services.devicelink import DeviceLinkClient


def main():
    root = Path(__file__).resolve().parents[1]
    output = root / ".artifacts" / ("devicelink-v25-" + uuid.uuid4().hex[:8])
    output.mkdir(parents=True)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        "FILEMIND_SERVER_DATA": str(output / "central"),
        "FILEMIND_ALLOW_PAID_AI": "false",
        "FILEMIND_DEEPSEEK_API_KEY": "",
        "FILEMIND_BAIDU_REMOTE_ENABLED": "false",
        "FILEMIND_BAIDU_WRITES_ENABLED": "false",
        "FILEMIND_ALLOWED_ORIGINS": url,
    }
    with (output / "server.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "filemind_server", "serve", "--port", str(port)],
            cwd=root,
            env=env,
            stdout=log,
            stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            with httpx.Client(trust_env=False, timeout=1) as client:
                for _ in range(100):
                    try:
                        if client.get(url + "/api/health").is_success:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.1)
                else:
                    raise TimeoutError("Browser test startup")
            account = CloudClient(url)
            account.request(
                "POST",
                "/auth/register",
                {
                    "username": "v25_browser",
                    "display_name": "Synthetic",
                    "password": "Synthetic-v25-browser",
                },
            )
            account.token = account.login("v25_browser", "Synthetic-v25-browser")["access_token"]
            device = account.register_device("Native-Windows")
            link = DeviceLinkClient(url, device["device_token"])
            source = output / "windows-to-browser.bin"
            source.write_bytes(b"Synthetic Windows native return\n" * 12000)
            stop = Event()

            def native():
                deadline = time.monotonic() + 150
                while time.monotonic() < deadline:
                    if stop.is_set():
                        return
                    task = next(
                        (
                            r
                            for r in link.poll()["tasks"]
                            if r["receiver_id"] == device["device"]["id"]
                            and r["state"] == "WAITING_RECEIVER"
                        ),
                        None,
                    )
                    if task:
                        break
                    time.sleep(0.2)
                else:
                    raise TimeoutError("Browser did not send to Windows")
                saved = output / "windows-received.bin"
                received = link.receive(task["id"], saved)
                assert (
                    hashlib.sha256(saved.read_bytes()).digest()
                    == hashlib.sha256(b"Synthetic cross-browser transport\n" * 18000).digest()
                )
                sent = link.send(source, task["sender_id"])
                assert received["state"] == sent["state"] == "COMPLETED"

            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(native)
                node_env = {**os.environ, "NODE_PATH": str(root / "web/node_modules")}
                result = subprocess.run(
                    ["node", "scripts/verify_devicelink_browser_v2_5.cjs", url, str(output)],
                    env=node_env,
                    cwd=root,
                    capture_output=True,
                    text=True,
                    timeout=180,
                )
                if result.returncode:
                    stop.set()
                    raise RuntimeError(result.stderr[-2000:])
                future.result(timeout=15)
            assert not account.request("GET", "/archive/jobs")
            report = json.loads((output / "browser-report.json").read_text())
            report["auto_archive_disabled_no_jobs"] = True
            (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(json.dumps({"output": str(output), "report": report}, ensure_ascii=False))
        finally:
            process.terminate()
            process.wait(timeout=15)


if __name__ == "__main__":
    main()
