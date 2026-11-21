"""V2.6 extraction review: fresh dependencies and isolated local-only acceptance."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from verify_delivery import verify


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("--extract", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--browsers", action="store_true")
    args = parser.parse_args()
    artifact = verify(args.archive.resolve(), args.extract.resolve())
    root = args.extract.resolve()
    (root / ".artifacts").mkdir(exist_ok=True)
    logdir = root.parent / (root.name + "-logs")
    logdir.mkdir(exist_ok=False)
    env = {k: v for k, v in os.environ.items() if not k.startswith("FILEMIND_")}
    env.update(
        {
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "FILEMIND_ALLOW_PAID_AI": "false",
            "FILEMIND_DEEPSEEK_API_KEY": "",
            "FILEMIND_BAIDU_FORMAL_AUTHORIZED": "false",
            "FILEMIND_BAIDU_REMOTE_ENABLED": "false",
            "FILEMIND_BAIDU_WRITES_ENABLED": "false",
        }
    )
    env.pop("PYTHONPATH", None)
    if args.browsers:
        env["FILEMIND_TEST_WEBKIT"] = "1"
    python = (
        root / ".venv" / "Scripts" / "python.exe"
        if os.name == "nt"
        else root / ".venv" / "bin" / "python"
    )
    report = {
        "archive": args.archive.name,
        "archive_validation": artifact,
        "extracted": str(root),
        "isolation": "new extraction, new Python venv, new node_modules via npm ci",
        "production": False,
        "paid_api": False,
        "physical_mobile": False,
        "checks": [],
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)

    def persist():
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    def run(name, command, cwd=root, timeout=300):
        began = time.perf_counter()
        path = logdir / (name + ".txt")
        with path.open("w", encoding="utf-8") as stream:
            result = subprocess.run(
                [str(x) for x in command],
                cwd=cwd,
                env=env,
                stdout=stream,
                stderr=subprocess.STDOUT,
                timeout=timeout,
            )
        record = {
            "check": name,
            "command": [str(x) for x in command],
            "returncode": result.returncode,
            "seconds": round(time.perf_counter() - began, 3),
            "log": str(path),
            "log_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        report["checks"].append(record)
        persist()
        print(
            json.dumps(
                {"check": name, "returncode": result.returncode, "seconds": record["seconds"]}
            ),
            flush=True,
        )
        if result.returncode:
            raise RuntimeError("Independent check failed: " + name)

    persist()
    run("create-venv", [sys.executable, "-m", "venv", root / ".venv"])
    uv = shutil.which("uv")
    if uv:
        run(
            "install-lock",
            [uv, "pip", "install", "--python", python, "-r", root / "requirements.lock"],
        )
        run("install-source", [uv, "pip", "install", "--python", python, "--no-deps", "-e", root])
    else:
        run("install-lock", [python, "-m", "pip", "install", "-r", root / "requirements.lock"])
        run("install-source", [python, "-m", "pip", "install", "--no-deps", "-e", root])
    run("dependency-check", [python, "-m", "pip", "check"])
    run(
        "import-isolation",
        [
            python,
            "-c",
            "import sys,filemind,filemind_server;from pathlib import Path;"
            "r=Path(sys.argv[1]).resolve();"
            "assert Path(filemind.__file__).resolve().is_relative_to(r);"
            "assert Path(filemind_server.__file__).resolve().is_relative_to(r);"
            "print('PASS: imports resolve inside fresh extraction; version='+filemind.__version__)",
            root,
        ],
    )
    run(
        "python-tests",
        [python, "-m", "pytest", "-q", "--basetemp=.artifacts/independent-pytest"],
        timeout=420,
    )
    run("ruff", [python, "-m", "ruff", "check", "src", "tests", "scripts"])
    run("format", [python, "-m", "ruff", "format", "--check", "src", "tests", "scripts"])
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm:
        raise RuntimeError("npm required for independent web acceptance")
    run("npm-ci", [npm, "ci", "--no-audit", "--no-fund"], cwd=root / "web")
    run("web-tests", [npm, "test", "--", "--reporter=verbose"], cwd=root / "web")
    run("web-build", [npm, "run", "build"], cwd=root / "web")
    run("windows-launch", [python, "scripts/verify_launch.py"])
    run("archive-live", [python, "scripts/verify_archive_live.py"], timeout=300)
    run("paid-script-default-blocked", [python, "scripts/verify_deepseek_live_v2_6.py"])
    flags = ["--browser-qa", "--node-modules", "web/node_modules"] if args.browsers else []
    run(
        "share-live",
        [python, "scripts/verify_share_live.py", "--megabytes", "64", *flags],
        timeout=300,
    )
    run(
        "library-regression-live",
        [python, "scripts/verify_library_live.py", "--megabytes", "16", *flags],
        timeout=300,
    )
    if args.browsers:
        run("devicelink-browser-native", [python, "scripts/verify_devicelink_v2_5.py"], timeout=240)
    run("e2e-mock", [python, "scripts/verify_e2e_v2_6.py"], timeout=180)
    if args.browsers:
        run("archive-ui", [python, "scripts/verify_archive_ui_v2_6.py"], timeout=240)
    report["all_checks_passed"] = True
    report["python_version"] = subprocess.check_output(
        [str(python), "--version"], env=env, text=True
    ).strip()
    report["node_version"] = subprocess.check_output(
        ["node", "--version"], env=env, text=True
    ).strip()
    persist()
    print(json.dumps({"report": str(args.report), "all_checks_passed": True}), flush=True)


if __name__ == "__main__":
    main()
