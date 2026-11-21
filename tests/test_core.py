import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from filemind.core.config import AppConfig, ConfigError, ConfigStore
from filemind.domain.models import Theme
from filemind.infrastructure.database import Database, DatabaseError


def test_config_defaults_and_atomic_roundtrip(tmp_path):
    store = ConfigStore(tmp_path / "data" / "config.json")
    assert store.load() == AppConfig()
    store.save(AppConfig(theme=Theme.DARK, window_width=1024))
    assert store.load().theme == Theme.DARK
    assert store.load().window_width == 1024
    assert list(store.path.parent.glob("*.tmp")) == []


@pytest.mark.parametrize(
    "values", [{"window_width": 200}, {"theme": "other"}, {"schema_version": 2}]
)
def test_invalid_config_rejected(values):
    with pytest.raises(ValidationError):
        AppConfig(**values)


def test_corrupt_config_is_preserved(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ConfigError, match="原文件已保留"):
        ConfigStore(path).load()
    assert path.read_text(encoding="utf-8") == "{broken"


def test_failed_atomic_save_keeps_previous_config(tmp_path, monkeypatch):
    store = ConfigStore(tmp_path / "config.json")
    store.save(AppConfig(theme=Theme.LIGHT))

    def blocked_replace(source, destination):
        raise PermissionError("configuration is locked")

    monkeypatch.setattr("filemind.core.config.os.replace", blocked_replace)
    with pytest.raises(ConfigError, match="无法保存"):
        store.save(AppConfig(theme=Theme.DARK))
    assert store.load().theme == Theme.LIGHT
    assert list(tmp_path.glob("*.tmp")) == []


def test_database_init_is_idempotent_and_history_empty(tmp_path):
    db = Database(tmp_path / "db.sqlite3")
    db.initialize()
    db.initialize()
    assert db.history_count() == 0
    # Windows also verifies that initialization does not leave a locked connection.
    moved = tmp_path / "renamed.sqlite3"
    db.path.rename(moved)
    assert moved.exists()


def test_future_database_version_is_not_overwritten(tmp_path):
    path = tmp_path / "future.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version = 999")
    with pytest.raises(DatabaseError, match="数据库版本"):
        Database(path).initialize()
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 999


def test_corrupt_database_is_preserved(tmp_path):
    path = tmp_path / "corrupt.sqlite3"
    original = b"this is not a SQLite database"
    path.write_bytes(original)
    with pytest.raises(DatabaseError, match="初始化失败"):
        Database(path).initialize()
    assert path.read_bytes() == original


def test_failed_schema_initialization_rolls_back(tmp_path):
    path = tmp_path / "incompatible.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE app_meta (incompatible TEXT)")
    with pytest.raises(DatabaseError, match="初始化失败"):
        Database(path).initialize()
    with sqlite3.connect(path) as connection:
        tables = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        assert tables == [("app_meta",)]
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0


def test_storage_stays_in_workspace(service):
    assert service.paths.config_file.parent.parent == service.paths.root
    assert service.paths.database_file.parent.parent == service.paths.root


def test_no_qt_imports_in_business_layers():
    root = Path(__file__).resolve().parents[1] / "src" / "filemind"
    for package in ("core", "domain", "services", "infrastructure"):
        for file in (root / package).glob("*.py"):
            assert "PySide6" not in file.read_text(encoding="utf-8"), file
