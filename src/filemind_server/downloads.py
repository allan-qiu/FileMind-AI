"""Bounded reads with persisted verification of the exact local file identity."""

import hashlib
import json
import os
import time


def signature(stream):
    stat = os.fstat(stream.fileno())
    return json.dumps([stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns])


def remember(conn, stream, key, sha256):
    conn.execute(
        "INSERT OR REPLACE INTO storage_read_checks VALUES (?,?,?,?)",
        (key, signature(stream), sha256, time.time()),
    )


def verify_open_file(db, stream, key, size, sha256):
    before = signature(stream)
    if os.fstat(stream.fileno()).st_size != size:
        raise ValueError("STORAGE_SIZE_MISMATCH")
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT * FROM storage_read_checks WHERE object_key=?", (key,)
        ).fetchone()
    if row and row["signature"] == before and row["sha256"] == sha256:
        return "trusted_unchanged_local_object"
    digest = hashlib.sha256()
    while block := stream.read(262144):
        digest.update(block)
    if digest.hexdigest() != sha256 or before != signature(stream):
        raise ValueError("STORAGE_INTEGRITY_MISMATCH")
    with db.transaction(True) as conn:
        remember(conn, stream, key, sha256)
    stream.seek(0)
    return "full_sha256_readback"
