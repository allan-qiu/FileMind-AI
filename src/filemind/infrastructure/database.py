import sqlite3
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path

SCHEMA_VERSION = 2


class DatabaseError(RuntimeError):
    pass


class Database:
    """Short-lived connections; no open SQLite handles survive a method call."""

    def __init__(self, path: Path):
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with closing(sqlite3.connect(self.path, timeout=5)) as connection:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                if version > SCHEMA_VERSION:
                    raise DatabaseError("数据库版本高于当前应用支持的版本，请使用相应版本的应用。")
                if version in (1, SCHEMA_VERSION):
                    # Fail visibly for damaged/mismatched databases, instead of resetting them.
                    connection.execute("SELECT key, value FROM app_meta LIMIT 0")
                    connection.execute(
                        "SELECT id, action, source_path FROM operation_history LIMIT 0"
                    )
                    if version == SCHEMA_VERSION:
                        connection.execute("SELECT id FROM local_tasks LIMIT 0")
                        return
                    backup_dir = self.path.parent / "backups"
                    backup_dir.mkdir(exist_ok=True)
                    with closing(
                        sqlite3.connect(backup_dir / f"before-v2-{time.time_ns()}.sqlite3")
                    ) as backup:
                        connection.backup(backup)
                with connection:
                    connection.execute("BEGIN")
                    connection.execute(
                        "CREATE TABLE IF NOT EXISTS app_meta "
                        "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
                    )
                    connection.execute(
                        "CREATE TABLE IF NOT EXISTS operation_history ("
                        "id INTEGER PRIMARY KEY, action TEXT NOT NULL, "
                        "source_path TEXT NOT NULL, target_path TEXT, "
                        "created_at TEXT NOT NULL, status TEXT NOT NULL)"
                    )
                    connection.execute(
                        "INSERT OR IGNORE INTO app_meta (key, value) VALUES (?, ?)",
                        ("stage", "phase_1_prototype"),
                    )
                    for statement in (
                        (
                            "CREATE TABLE IF NOT EXISTS local_tasks (id TEXT PRIMARY KEY, "
                            "created_at REAL NOT NULL, status TEXT NOT NULL, mode TEXT NOT NULL)"
                        ),
                        (
                            "CREATE TABLE IF NOT EXISTS local_operations (id TEXT PRIMARY KEY, "
                            "task_id TEXT NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL, "
                            "error TEXT NOT NULL DEFAULT '', temp_path TEXT, history_id INTEGER, "
                            "updated_at REAL NOT NULL)"
                        ),
                        (
                            "CREATE TABLE IF NOT EXISTS local_monitor_rules (id TEXT PRIMARY "
                            "KEY, source TEXT NOT NULL, destination TEXT NOT NULL, enabled "
                            "INTEGER NOT NULL, auto_execute INTEGER NOT NULL, rules TEXT NOT "
                            "NULL)"
                        ),
                        (
                            "CREATE TABLE IF NOT EXISTS local_monitor_events (id INTEGER PRIMARY "
                            "KEY, rule_id TEXT NOT NULL, path TEXT NOT NULL, state TEXT NOT "
                            "NULL, message TEXT NOT NULL, created_at REAL NOT NULL)"
                        ),
                    ):
                        connection.execute(statement)
                    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        except sqlite3.Error as exc:
            raise DatabaseError(f"数据库初始化失败：{exc}") from exc

    @contextmanager
    def transaction(self, write: bool = False) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.path, timeout=5)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def history_count(self) -> int:
        try:
            with closing(sqlite3.connect(self.path, timeout=5)) as connection:
                return connection.execute("SELECT COUNT(*) FROM operation_history").fetchone()[0]
        except sqlite3.Error as exc:
            raise DatabaseError(f"无法读取操作历史：{exc}") from exc
