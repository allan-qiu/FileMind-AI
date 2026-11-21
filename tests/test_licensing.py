import json
import tomllib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from filemind import __version__
from filemind.licensing import DEFAULT_SOURCE_URL, SPDX_LICENSE
from filemind_server.app import create_app
from filemind_server.config import Settings


def test_public_source_offer_without_account(tmp_path):
    url = "https://example.org/releases/modified-source.zip"
    app = create_app(Settings(data_dir=tmp_path, archive_worker_enabled=False, source_url=url))
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.get("/api/public/license")
        assert response.status_code == 200
        assert response.json() == {
            "version": __version__,
            "license": "AGPL-3.0-only",
            "source_url": url,
        }
        assert client.get("/api/library").status_code == 401


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "https://user:secret@example.org/source.zip",
        "https://example.org/source.zip?token=private",
        "http://example.org/source.zip",
        "file:///private/source",
    ],
)
def test_source_configuration_rejects_credential_or_active_urls(url):
    with pytest.raises(ValidationError):
        Settings(source_url=url)


def test_license_metadata_and_exact_release_archive_agree():
    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf8"))["project"]
    web = json.loads((root / "web/package.json").read_text(encoding="utf8"))
    assert project["license"] == web["license"] == SPDX_LICENSE == "AGPL-3.0-only"
    assert project["version"] == web["version"] == __version__
    assert DEFAULT_SOURCE_URL.endswith(f"/v{__version__}.zip")
    assert (root / "LICENSE").read_bytes() == (root / "licenses/AGPL-3.0.txt").read_bytes()
