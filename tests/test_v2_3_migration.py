import shutil

from filemind_server import database
from filemind_server.accounts import create_account
from filemind_server.database import CentralDatabase
from filemind_server.models import Registration


def test_v22_catalog_and_objects_survive_upgrade_and_restore_backup(tmp_path, monkeypatch):
    old = tmp_path / "old"
    old.mkdir()
    for script in sorted(database.MIGRATIONS.glob("*.sql"))[:4]:
        shutil.copyfile(script, old / script.name)
    db = CentralDatabase(tmp_path / "central.sqlite3")
    with monkeypatch.context() as scoped:
        scoped.setattr(database, "MIGRATIONS", old)
        db.migrate()
    with db.transaction(True) as conn:
        user = create_account(
            conn,
            Registration(
                username="migration_test",
                display_name="Synthetic",
                password="synthetic-password-123",
            ),
        )
        conn.execute(
            (
                "INSERT INTO archive_jobs(id,user_id,idempotency_key,filename,size_bytes,"
                "sha256,state,created_at,updated_at,expires_at) VALUES ('j',?,'legacy-key"
                "','旧文件.txt',3,?,'COMPLETED',1,1,9999999999)"
            ),
            (user["id"], "a" * 64),
        )
        conn.execute(
            (
                "INSERT INTO storage_objects VALUES ('o','j','local','synthetic/key',3,?,"
                "'full_sha256_readback',1)"
            ),
            ("a" * 64,),
        )
        conn.execute("INSERT INTO global_files VALUES ('f',?,'private',1,1)", (user["id"],))
        conn.execute(
            (
                "INSERT INTO file_versions VALUES "
                "('v','f',1,'o','旧文件.txt','学习资料','学习/原目录','summary',?,1)"
            ),
            ("a" * 64,),
        )
        conn.execute(
            "INSERT INTO catalog_revisions(user_id,job_id,created_at) VALUES (?,'j',1)",
            (user["id"],),
        )
        conn.execute("INSERT INTO catalog_entries VALUES ('f','v',1)")
        original = {
            name: [tuple(r) for r in conn.execute("SELECT * FROM " + name)]
            for name in [
                "users",
                "global_files",
                "file_versions",
                "catalog_entries",
                "storage_objects",
                "schema_migrations",
            ]
        }
    db.migrate()
    db.migrate()
    with db.transaction() as conn:
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert (
            dict(conn.execute("SELECT * FROM file_metadata").fetchone())["filename"] == "旧文件.txt"
        )
        for table, rows in original.items():
            actual = [tuple(r) for r in conn.execute("SELECT * FROM " + table)]
            assert actual[: len(rows)] == rows
    backups = list((tmp_path / "backups").glob("before-v5-*.sqlite3"))
    assert len(backups) == 1
    restored = CentralDatabase(tmp_path / "restored.sqlite3")
    shutil.copyfile(backups[0], restored.path)
    with restored.transaction() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
        assert conn.execute("SELECT filename FROM file_versions").fetchone()[0] == "旧文件.txt"
