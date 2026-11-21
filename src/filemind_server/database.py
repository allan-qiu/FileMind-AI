import hashlib
import sqlite3
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path

MIGRATIONS = Path(__file__).parent / "migrations"


class CentralDatabase:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def transaction(self, write: bool = False) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.path, timeout=5)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    def migrate(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise ValueError("中央数据库不能是符号链接。")
        scripts = sorted(MIGRATIONS.glob("*.sql"))
        with closing(sqlite3.connect(self.path)) as conn:
            current = conn.execute("PRAGMA user_version").fetchone()[0]
            if current > len(scripts):
                raise ValueError("中央数据库版本过高，拒绝降级。")
            if current:
                for script in scripts[:current]:
                    expected = hashlib.sha256(script.read_bytes()).hexdigest()
                    row = conn.execute(
                        "SELECT checksum FROM schema_migrations WHERE version=?",
                        (int(script.name[:3]),),
                    ).fetchone()
                    if not row or row[0] != expected:
                        raise ValueError("已应用迁移的校验和不匹配，保留数据库并停止启动。")
            if current < len(scripts) and current:
                backup_dir = self.path.parent / "backups"
                backup_dir.mkdir(exist_ok=True)
                target = backup_dir / f"before-v{current + 1}-{time.time_ns()}.sqlite3"
                with closing(sqlite3.connect(target)) as backup:
                    conn.backup(backup)
            conn.execute("PRAGMA journal_mode=WAL")
        with self.transaction(write=True) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version INTEGER PRIMARY KEY, checksum TEXT NOT NULL, applied_at INTEGER NOT NULL)"
            )
            for script in scripts[current:]:
                for statement in script.read_text(encoding="utf-8").split(";"):
                    if statement.strip():
                        conn.execute(statement)
                version = int(script.name[:3])
                conn.execute(
                    "INSERT INTO schema_migrations VALUES (?,?,?)",
                    (version, hashlib.sha256(script.read_bytes()).hexdigest(), int(time.time())),
                )
                conn.execute(f"PRAGMA user_version={version}")
