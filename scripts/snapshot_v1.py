"""Create a non-destructive source/data baseline inside the project."""

import hashlib
import json
import sqlite3
import zipfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    target = root / ".artifacts" / f"v1-baseline-{stamp}"
    target.mkdir(parents=True)
    files = [
        file
        for folder in ("src", "tests", "docs", "scripts")
        for file in (root / folder).rglob("*")
        if file.is_file() and "__pycache__" not in file.parts
    ]
    files += [
        root / name
        for name in (
            "README.md",
            "pyproject.toml",
            "requirements.lock",
            "setup.ps1",
            "run.py",
            "launch.cmd",
            ".gitignore",
        )
    ]
    manifest = {}
    with zipfile.ZipFile(target / "source.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for file in files:
            relative = file.relative_to(root).as_posix()
            manifest[relative] = hashlib.sha256(file.read_bytes()).hexdigest()
            archive.write(file, relative)
    data = root / ".filemind"
    if (data / "config.json").exists():
        (target / "config.json").write_bytes((data / "config.json").read_bytes())
    if (data / "filemind.sqlite3").exists():
        with closing(sqlite3.connect(data / "filemind.sqlite3")) as source:
            with closing(sqlite3.connect(target / "filemind.sqlite3")) as destination:
                source.backup(destination)
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(target)


if __name__ == "__main__":
    main()
