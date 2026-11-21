import hashlib
import shutil

from filemind_server import database
from filemind_server.accounts import create_account
from filemind_server.database import CentralDatabase
from filemind_server.models import Registration


def test_v24_upgrade_preserves_old_rows_and_binds_existing_objects_to_local(tmp_path, monkeypatch):
    previous = tmp_path / "v24"
    previous.mkdir()
    for script in sorted(database.MIGRATIONS.glob("*.sql"))[:10]:
        shutil.copyfile(script, previous / script.name)
    db = CentralDatabase(tmp_path / "central.sqlite3")
    with monkeypatch.context() as context:
        context.setattr(database, "MIGRATIONS", previous)
        db.migrate()
    with db.transaction(True) as conn:
        user = create_account(
            conn,
            Registration(
                username="migrate_v25", display_name="Synthetic", password="Synthetic-migration-v25"
            ),
        )
        conn.execute(
            "INSERT INTO archive_jobs(id,user_id,idempotency_key,filename,size_bytes,"
            "sha256,state,storage_key,created_at,updated_at,expires_at) VALUES "
            "('old',?,'old-object','old.txt',3,?,'RETRY_WAIT','u/old.txt',1,1,9999999999)",
            (user["id"], hashlib.sha256(b"abc").hexdigest()),
        )
        old = tuple(conn.execute("SELECT * FROM archive_jobs").fetchone())
        checks = [tuple(r) for r in conn.execute("SELECT version,checksum FROM schema_migrations")]
    db.migrate()
    db.migrate()
    with db.transaction() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 11
        assert tuple(conn.execute("SELECT * FROM archive_jobs").fetchone()) == old
        assert tuple(conn.execute("SELECT * FROM archive_storage_bindings").fetchone()) == (
            "old",
            "local",
        )
        assert [
            tuple(r)
            for r in conn.execute(
                "SELECT version,checksum FROM schema_migrations WHERE version<=10"
            )
        ] == checks
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
    assert len(list((tmp_path / "backups").glob("before-v11-*.sqlite3"))) == 1
