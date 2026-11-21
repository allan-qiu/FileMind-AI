"""Acceptance tooling and deployment boundaries, using isolated data only."""

import importlib.util
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from test_server import central  # noqa: F401

from filemind_server import config
from filemind_server.config import Settings
from filemind_server.extraction import extract_excerpt

ROOT = Path(__file__).parents[1]


def test_synthetic_office_and_pdf_are_extractable(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "fixtures_v25", ROOT / "scripts" / "synthetic_acceptance_files.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fixtures = module.create(tmp_path / "fixtures")
    assert len(fixtures) == 8
    for path, _ in fixtures:
        text, method = extract_excerpt(path, path.name, "summary")
        if path.suffix == ".png":
            assert not text and "fallback" in method
        else:
            assert text and "fallback" not in method


def test_live_script_never_calls_without_both_explicit_flags_and_new_key():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/verify_deepseek_live_v2_5.py")],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0 and "No API request sent" in result.stdout


def test_explicit_external_data_root_keeps_default_containment(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path / "code")
    monkeypatch.delenv("FILEMIND_SERVER_DATA_ROOT", raising=False)
    monkeypatch.setenv("FILEMIND_SERVER_DATA", str(tmp_path / "outside"))
    with pytest.raises(ValueError):
        Settings.from_env()
    monkeypatch.setenv("FILEMIND_SERVER_DATA_ROOT", str(tmp_path / "persistent"))
    monkeypatch.setenv("FILEMIND_SERVER_DATA", "state")
    assert Settings.from_env().data_dir == tmp_path / "persistent" / "state"
    monkeypatch.setenv("FILEMIND_SERVER_DATA", "../escape")
    with pytest.raises(ValueError):
        Settings.from_env()
    monkeypatch.setenv("FILEMIND_SERVER_DATA_ROOT", str(tmp_path.anchor))
    with pytest.raises(ValueError):
        Settings.from_env()


def test_backup_only_identified_filemind_db_and_never_overwrites(central, tmp_path):  # noqa: F811
    _, app, _ = central
    script = ROOT / "scripts/deploy-v2_5/backup.py"
    backup = tmp_path / "backup.sqlite3"
    first = subprocess.run(
        [
            sys.executable,
            str(script),
            "--database",
            str(app.state.db.path),
            "--output",
            str(backup),
        ],
        capture_output=True,
        timeout=15,
    )
    assert first.returncode == 0, first.stderr
    with sqlite3.connect(backup) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    original = backup.read_bytes()
    repeat = subprocess.run(
        [
            sys.executable,
            str(script),
            "--database",
            str(app.state.db.path),
            "--output",
            str(backup),
        ],
        capture_output=True,
        timeout=15,
    )
    assert repeat.returncode and backup.read_bytes() == original
    unrelated = tmp_path / "unrelated.sqlite3"
    with sqlite3.connect(unrelated) as conn:
        conn.execute("CREATE TABLE question_bank(id INTEGER)")
    refused = subprocess.run(
        [
            sys.executable,
            str(script),
            "--database",
            str(unrelated),
            "--output",
            str(tmp_path / "refused.sqlite3"),
        ],
        capture_output=True,
        timeout=15,
    )
    assert refused.returncode and not (tmp_path / "refused.sqlite3").exists()
