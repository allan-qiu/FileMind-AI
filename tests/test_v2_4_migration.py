import hashlib
import shutil

from filemind_server import database
from filemind_server.accounts import create_account
from filemind_server.database import CentralDatabase
from filemind_server.models import Registration


def test_v23_six_migrations_preserve_records_and_split_legacy_sync_cursor(tmp_path, monkeypatch):
    scripts = tmp_path / "v23"
    scripts.mkdir()
    for script in sorted(database.MIGRATIONS.glob("*.sql"))[:6]:
        shutil.copyfile(script, scripts / script.name)
    db = CentralDatabase(tmp_path / "central.sqlite3")
    with monkeypatch.context() as m:
        m.setattr(database, "MIGRATIONS", scripts)
        db.migrate()
    with db.transaction(True) as conn:
        user = create_account(
            conn,
            Registration(
                username="migration_share",
                display_name="Migration",
                password="Synthetic-migration-2026",
            ),
        )
        conn.execute(
            "INSERT INTO "
            "archive_jobs(id,user_id,idempotency_key,filename,size_bytes,sha256,state,created_at,updated_at,expires_at)"
            " VALUES "
            "('j',?,'legacy-key','keep.txt',3,?,'COMPLETED',1,1,9999999999)",
            (user["id"], hashlib.sha256(b"abc").hexdigest()),
        )
        sha = hashlib.sha256(b"abc").hexdigest()
        conn.execute(
            "INSERT INTO storage_objects VALUES "
            "('o','j','local','formal/keep',3,?,'full_sha256_readback',1)",
            (sha,),
        )
        conn.execute("INSERT INTO global_files VALUES ('f',?,'private',1,1)", (user["id"],))
        conn.execute(
            "INSERT INTO file_versions VALUES "
            "('v','f',1,'o','keep.txt','other','folder','summary',?,1)",
            (sha,),
        )
        conn.execute(
            "INSERT INTO catalog_revisions(user_id,job_id,created_at) VALUES (?,'j',1)",
            (user["id"],),
        )
        conn.execute("INSERT INTO catalog_entries VALUES ('f','v',1)")
        conn.execute(
            "INSERT INTO "
            "file_metadata(file_id,filename,category,directory,revision) VALUES "
            "('f','keep.txt','other','folder',1)"
        )
        conn.execute(
            "INSERT INTO catalog_sync_cursors VALUES (?,'windows-session',17,'legacy',1)",
            (user["id"],),
        )
        names = [
            "users",
            "devices",
            "workspaces",
            "archive_jobs",
            "global_files",
            "file_versions",
            "storage_objects",
            "catalog_entries",
            "catalog_revisions",
            "file_metadata",
        ]
        original = {
            name: [tuple(r) for r in conn.execute("SELECT * FROM " + name)] for name in names
        }
    db.migrate()
    db.migrate()
    with db.transaction() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == len(
            list(database.MIGRATIONS.glob("*.sql"))
        )
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
        for name, rows in original.items():
            assert [tuple(r) for r in conn.execute("SELECT * FROM " + name)] == rows
        cursor = conn.execute("SELECT * FROM catalog_sync_cursors").fetchone()
        assert cursor["revision"] == 0 and cursor["sent_revision"] == 17
    backups = list((tmp_path / "backups").glob("before-v7-*.sqlite3"))
    assert len(backups) == 1
    restored = CentralDatabase(backups[0])
    with restored.transaction() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 6
        assert conn.execute("SELECT filename FROM file_versions").fetchone()[0] == "keep.txt"
