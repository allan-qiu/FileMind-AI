import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path

import pytest


@pytest.fixture
def release():
    path = Path(__file__).resolve().parents[1] / "scripts" / "release.py"
    spec = importlib.util.spec_from_file_location("review_release", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_release_allowlist_excludes_runtime_secrets_and_dependencies(
    release, tmp_path, monkeypatch
):
    monkeypatch.setattr(release, "ROOT", tmp_path)
    for name in (
        "src/filemind/app.py",
        "tests/test_app.py",
        "web/src/main.tsx",
        "README.md",
        ".env",
        ".filemind/user.sqlite3",
        ".artifacts/private.txt",
        "web/node_modules/x.js",
        ".venv/private.py",
        "src/filemind/__pycache__/secret.pyc",
    ):
        file = tmp_path / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("fixture", encoding="utf-8")
    actual = {p.relative_to(tmp_path).as_posix() for p in release.source_files()}
    assert actual == {"src/filemind/app.py", "tests/test_app.py", "web/src/main.tsx", "README.md"}


def test_release_manifest_and_zip_are_independently_verifiable(release, tmp_path, monkeypatch):
    monkeypatch.setattr(release, "ROOT", tmp_path)
    config = tmp_path / "pyproject.toml"
    config.write_text('[project]\nversion="2.1.0"\n', encoding="utf-8")
    source = tmp_path / "README.md"
    source.write_text("reproducible fixture", encoding="utf-8")
    target = tmp_path / "review.zip"
    release.pack(target, [config, source], "E")
    with zipfile.ZipFile(target) as archive:
        assert archive.testzip() is None
        manifest = json.loads(archive.read("DELIVERY_MANIFEST.json"))
        assert manifest["version"] == "2.1.0"
        for name, digest in manifest["files"].items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == digest


def test_release_refuses_private_key_material(release, tmp_path, monkeypatch):
    monkeypatch.setattr(release, "ROOT", tmp_path)
    config = tmp_path / "pyproject.toml"
    config.write_text('[project]\nversion="2.1.0"\n', encoding="utf-8")
    key = tmp_path / "README.md"
    key.write_text("-----BEGIN PRIVATE KEY-----\nfixture\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Private key"):
        release.pack(tmp_path / "refused.zip", [key], "E")
