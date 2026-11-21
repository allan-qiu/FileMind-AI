import shutil

from filemind_server import database
from filemind_server.accounts import create_account
from filemind_server.database import CentralDatabase
from filemind_server.models import Registration


def test_v21_database_upgrades_with_backup_and_original_checksums(tmp_path, monkeypatch):
    old_scripts = tmp_path / "v21-migrations"
    old_scripts.mkdir()
    current = database.MIGRATIONS
    for script in sorted(current.glob("*.sql"))[:2]:
        shutil.copyfile(script, old_scripts / script.name)
    db = CentralDatabase(tmp_path / "central.sqlite3")
    with monkeypatch.context() as m:
        m.setattr(database, "MIGRATIONS", old_scripts)
        db.migrate()
    with db.transaction(True) as conn:
        user = create_account(
            conn,
            Registration(
                username="upgrade_user",
                display_name="Synthetic upgrade",
                password="synthetic-migration-password",
            ),
        )
        checks = [tuple(r) for r in conn.execute("SELECT version,checksum FROM schema_migrations")]
    db.migrate()
    with db.transaction() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == len(list(current.glob("*.sql")))
        assert conn.execute("SELECT id FROM users").fetchone()[0] == user["id"]
        assert [
            tuple(r)
            for r in conn.execute("SELECT version,checksum FROM schema_migrations WHERE version<=2")
        ] == checks
        assert conn.execute("SELECT count(*) FROM archive_jobs").fetchone()[0] == 0
    assert list((tmp_path / "backups").glob("before-v3-*.sqlite3"))
