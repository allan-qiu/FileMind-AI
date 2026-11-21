"""Explicit FileMind SQLite backup; refuses overwrite and never stops services."""

import argparse
import hashlib
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source, target = args.database.resolve(), args.output.absolute()
    if not source.is_file() or target.exists() or target.is_symlink():
        raise SystemExit("Existing source and NEW backup destination required")
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as conn:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"archive_jobs", "transfer_sessions", "share_links", "schema_migrations"} <= names:
            raise SystemExit("Source is not an identified FileMind central database")
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with target.open("xb") as placeholder:
            if os.name != "nt":
                os.fchmod(placeholder.fileno(), 0o600)
        with closing(sqlite3.connect(target)) as dest:
            conn.backup(dest)
            assert dest.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    with target.open("rb") as saved:
        checksum = hashlib.file_digest(saved, "sha256").hexdigest()
    print(
        json.dumps(
            {
                "backup": str(target),
                "sha256": checksum,
                "note": "Private backup: exclude from delivery ZIPs",
            }
        )
    )


if __name__ == "__main__":
    main()
