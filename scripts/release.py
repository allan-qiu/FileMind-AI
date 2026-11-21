"Reproducible, allowlisted review ZIPs. Runtime data is never included in releases."

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
import time
import tomllib
import zipfile
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def source_files():
    files = []
    for folder in (
        "src",
        "tests",
        "scripts",
        "docs",
        "web/src",
        "web/app",
        "web/admin",
        "web/share",
        ".github",
        "licenses",
    ):
        for path in (ROOT / folder).rglob("*"):
            if (
                path.is_file()
                and not any(
                    part.endswith(".egg-info")
                    or part in {"__pycache__", "private", ".artifacts", "node_modules", ".venv"}
                    for part in path.relative_to(ROOT).parts
                )
                and path.suffix
                in {
                    ".py",
                    ".sql",
                    ".md",
                    ".sh",
                    ".ps1",
                    ".tsx",
                    ".ts",
                    ".css",
                    ".html",
                    ".cjs",
                    ".service",
                    ".conf",
                    ".yml",
                    ".yaml",
                    ".txt",
                }
            ):
                files.append(path)
    for name in (
        "README.md",
        "LICENSE",
        "THIRD_PARTY_NOTICES.md",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "CHANGELOG.md",
        "AGENTS.md",
        "pyproject.toml",
        "requirements.lock",
        ".env.example",
        ".gitignore",
        ".gitattributes",
        "run.py",
        "launch.cmd",
        "launch-server.cmd",
        "setup.ps1",
        "setup-v2.ps1",
        "web/package.json",
        "web/package-lock.json",
        "web/tsconfig.json",
        "web/vite.config.ts",
        "scripts/deploy-v2_6/filemind.env.example",
        "scripts/deploy-v2_6/initial_limits.json",
        "docs/evidence/V2_2_INDEPENDENT_REVIEW.json",
        "docs/evidence/V2_3_INDEPENDENT_REVIEW.json",
        "docs/evidence/V2_3_LIVE_REPORT.json",
        "docs/evidence/V2_3_BROWSER_REPORT.json",
    ):
        if (ROOT / name).is_file():
            files.append(ROOT / name)
    for path in (ROOT / "docs" / "evidence").glob("V2_[456]_*"):
        if path.is_file() and path.suffix in {".json", ".txt", ".png"}:
            files.append(path)
    return sorted(set(files))


def pack(path: Path, files, stage: str):
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]

    def git(*args):
        result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
        return result.stdout.strip() if result.returncode == 0 else None

    manifest = {
        "version": version,
        "target_version": version,
        "stage": stage,
        "files": {},
        "git": {
            "branch": git("branch", "--show-current"),
            "commit": git("rev-parse", "--verify", "HEAD"),
            "state": "working files; no user changes cleaned or auto-committed",
        },
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for file in files:
            relative = file.relative_to(ROOT).as_posix()
            raw = file.read_bytes()
            if re.search(rb"(?m)^-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----\r?$", raw):
                raise ValueError(f"Private key material refused: {relative}")
            if re.search(rb"sk-[A-Za-z0-9]{24,}", raw):
                raise ValueError(f"Credential-shaped material refused: {relative}")
            manifest["files"][relative] = hashlib.sha256(raw).hexdigest()
            archive.writestr(relative, raw)
        sums = "".join(f"{digest}  {name}\n" for name, digest in manifest["files"].items()).encode()
        manifest["files"]["SHA256SUMS.txt"] = hashlib.sha256(sums).hexdigest()
        archive.writestr("SHA256SUMS.txt", sums)
        archive.writestr(
            "DELIVERY_MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2)
        )
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        assert set(manifest["files"]).issubset(archive.namelist())
        for name, digest in manifest["files"].items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == digest
    return manifest


def baseline():
    target = ROOT / ".artifacts" / f"v2_1-baseline-{time.time_ns()}"
    target.mkdir(parents=True)
    pack(target / "source.zip", source_files(), "V2.0-before-changes")
    from filemind_server.config import Settings

    for name, folder in (
        ("desktop", ROOT / ".filemind"),
        ("central", Settings.from_env().data_dir),
    ):
        for database in folder.glob("*.sqlite3"):
            with closing(sqlite3.connect(database)) as source:
                with closing(sqlite3.connect(target / f"{name}-{database.name}")) as destination:
                    source.backup(destination)
        config = folder / "config.json"
        if config.exists():
            shutil.copyfile(config, target / f"{name}-config.json")
    if (ROOT / ".env").exists():
        shutil.copyfile(ROOT / ".env", target / "private.env.backup")
    print(target)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", default="M6")
    parser.add_argument("--series", default="2.4")
    parser.add_argument("--baseline", action="store_true")
    parser.add_argument("--final-names", action="store_true", help="Omit stage in final ZIP names")
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Refresh summary after review without changing the reviewed source ZIP",
    )
    args = parser.parse_args()
    if args.baseline:
        baseline()
        return
    output = ROOT / "deliveries" / ("V" + args.series) / args.stage
    output.mkdir(parents=True, exist_ok=True)
    files = source_files()
    required = {
        "src/filemind/app.py",
        "src/filemind_server/app.py",
        "web/src/Portal.tsx",
        "web/app/index.html",
        "web/admin/index.html",
        "requirements.lock",
        "web/package-lock.json",
    }
    assert required.issubset({p.relative_to(ROOT).as_posix() for p in files})
    hashes = []
    for kind, selection in (
        ("源码复核包", files),
        (
            "阶段总结",
            [p for p in files if p.suffix == ".md" or p.parent == ROOT / "docs" / "evidence"],
        ),
    ):
        prefix = f"FileMind_AI_V{args.series}" + ("" if args.final_names else f"_{args.stage}")
        archive = output / f"{prefix}_{kind}.zip"
        if args.summary_only and kind == "源码复核包":
            if not archive.is_file():
                raise ValueError("Reviewed source ZIP must already exist")
            hashes.append(f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}")
            continue
        manifest = pack(archive, selection, args.stage)
        (output / f"{kind}_文件清单.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        hashes.append(f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}")
    (output / "SHA256SUMS.txt").write_text("\n".join(hashes) + "\n", encoding="utf-8")
    print(output)
    print("\n".join(hashes))


if __name__ == "__main__":
    main()
