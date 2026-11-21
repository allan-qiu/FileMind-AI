"""Own the isolated archive QA server lifecycle, then exercise actual Edge UI."""

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    output = ROOT / ".artifacts" / ("v26-archive-ui-" + uuid.uuid4().hex[:8])
    output.mkdir(parents=True)
    env = {**os.environ, "NODE_PATH": str(ROOT / "web/node_modules"), "PYTHONIOENCODING": "utf-8"}
    ready, qa_root = None, None
    with (output / "orchestrator.txt").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, "scripts/verify_archive_live.py", "--browser-qa"],
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            for _ in range(1200):
                text = (output / "orchestrator.txt").read_text(encoding="utf-8")
                ready = next(
                    (line for line in text.splitlines() if line.startswith("QA_READY ")), None
                )
                if ready:
                    _, url, folder = ready.split(" ", 2)
                    qa_root = Path(folder).resolve()
                    if not qa_root.is_relative_to(ROOT / ".artifacts"):
                        raise ValueError("Unexpected QA root")
                    break
                if process.poll() is not None:
                    raise RuntimeError("QA server exited before readiness; see isolated log")
                time.sleep(0.1)
            else:
                raise TimeoutError("QA readiness")
            with (output / "browser.txt").open("w", encoding="utf-8") as browser_log:
                subprocess.run(
                    ["node", "scripts/verify_archive_browser.cjs", url, str(qa_root)],
                    cwd=ROOT,
                    env=env,
                    stdout=browser_log,
                    stderr=subprocess.STDOUT,
                    check=True,
                    timeout=180,
                )
            print(
                json.dumps(
                    {
                        "output": str(qa_root),
                        "orchestrator": str(output),
                        "real_edge_ui": True,
                        "mock_ai": True,
                        "production": False,
                    }
                )
            )
        finally:
            if qa_root:
                (qa_root / "stop-qa").touch()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                import psutil

                for child in psutil.Process(process.pid).children(recursive=True):
                    child.terminate()
                process.terminate()
                process.wait(timeout=10)


if __name__ == "__main__":
    main()
