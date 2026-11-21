"""Independently verify a delivery's inventory and hashes before optional local extraction."""

import argparse
import hashlib
import json
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = {".venv", "node_modules", ".filemind", ".filemind-server", ".artifacts", "__pycache__"}


def verify(archive_path: Path, extract: Path | None = None):
    if extract:
        resolved = extract.resolve()
        if not resolved.is_relative_to(ROOT / ".artifacts") or resolved.exists():
            raise ValueError("Extraction requires a NEW directory under project .artifacts")
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    checksums = archive_path.parent / "SHA256SUMS.txt"
    expected = next(
        line.split("  ", 1)[0]
        for line in checksums.read_text(encoding="utf-8").splitlines()
        if line.endswith("  " + archive_path.name)
    )
    assert digest == expected, "Archive checksum mismatch"
    with zipfile.ZipFile(archive_path) as archive:
        assert archive.testzip() is None
        manifest = json.loads(archive.read("DELIVERY_MANIFEST.json"))
        assert set(archive.namelist()) == {*manifest["files"], "DELIVERY_MANIFEST.json"}
        for name, checksum in manifest["files"].items():
            path = PurePosixPath(name)
            assert not path.is_absolute() and ".." not in path.parts
            assert ":" not in name and "\\" not in name
            assert not FORBIDDEN.intersection(path.parts)
            assert path.name != ".env" and path.suffix not in {".sqlite3", ".db", ".pem", ".key"}
            assert hashlib.sha256(archive.read(name)).hexdigest() == checksum, name
        if extract:
            archive.extractall(extract)
    return {"sha256": digest, "version": manifest["version"], "files": len(manifest["files"])}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("--extract", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.archive, args.extract), ensure_ascii=False))
