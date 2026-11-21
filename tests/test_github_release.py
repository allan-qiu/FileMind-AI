import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def publisher():
    path = Path(__file__).resolve().parents[1] / "scripts/prepare_github_release.py"
    spec = importlib.util.spec_from_file_location("public_release_check", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_public_export_refuses_existing_directory(publisher, tmp_path):
    marker = tmp_path / "user-work.txt"
    marker.write_text("preserve", encoding="utf8")
    with pytest.raises(ValueError, match="not be overwritten"):
        publisher.export_public(tmp_path)
    assert marker.read_text() == "preserve"


def test_public_check_reports_rules_without_secret_values(publisher, tmp_path):
    secret = "sk-" + "x" * 32
    (tmp_path / "settings.txt").write_text(secret, encoding="utf8")
    (tmp_path / ".env").write_text("PRIVATE=value", encoding="utf8")
    report = publisher.check_tree(tmp_path)
    assert report["status"] == "FAIL"
    assert any(row["rule"] == "provider-key" for row in report["findings"])
    assert any(row["rule"] == "excluded-runtime-or-credential-path" for row in report["findings"])
    assert secret not in str(report)


def test_empty_env_example_is_public(publisher):
    assert publisher.validate_file(".env.example", b"FILEMIND_DEEPSEEK_API_KEY=\n") == []
