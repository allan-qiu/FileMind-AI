"""Loopback-only companion for manual browser QA; uses isolated QA fixtures only."""

import hashlib
import json
import time
from pathlib import Path
from threading import Event, Thread

from filemind.services.cloud import CloudClient
from filemind.services.devicelink import DeviceLinkClient


def main():
    root = Path(__file__).resolve().parents[1] / ".artifacts" / "devicelink-qa"
    root.mkdir(parents=True, exist_ok=True)
    account = CloudClient("http://127.0.0.1:8765")
    account.token = account.login("qa_user", "Browser-QA-only-2026!")["access_token"]
    registration = account.register_device("QA Windows HTTP 客户端")
    link = DeviceLinkClient(account.server, registration["device_token"])
    stop = Event()

    def polling():
        while not stop.wait(1):
            link.poll()

    worker = Thread(target=polling, daemon=True)
    worker.start()
    deadline = time.monotonic() + 600
    try:
        link.poll()
        print("Windows QA receiver ready", flush=True)
        while time.monotonic() < deadline:
            tasks = link.poll()["tasks"]
            incoming = next((t for t in tasks if t["state"] == "WAITING_RECEIVER"), None)
            if incoming:
                break
            time.sleep(0.3)
        else:
            raise TimeoutError("No browser transfer within 600 seconds")
        target = root / f"windows-received-{incoming['id']}.bin"
        received = link.receive(incoming["id"], target)
        source = root / "windows-source.bin"
        source.write_bytes(bytes(range(251)) * 3000)
        outgoing = link.send(source, incoming["sender_id"])
        report = {
            "browser_to_windows": received,
            "windows_to_browser": outgoing,
            "saved_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            "sent_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "scope": "one Windows host, real browser and Python client, loopback HTTP",
        }
        (root / "browser-windows-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("Both directions COMPLETED; isolated JSON report saved", flush=True)
    finally:
        stop.set()
        worker.join(timeout=6)


if __name__ == "__main__":
    main()
