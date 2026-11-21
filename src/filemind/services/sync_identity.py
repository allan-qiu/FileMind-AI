"""Stable installation identity only; never stores tokens or account credentials."""

import os
import uuid
from pathlib import Path


def installation_id():
    folder = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "FileMindAI"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "sync-installation-id"
    try:
        with path.open("x", encoding="ascii") as stream:
            stream.write(str(uuid.uuid4()))
    except FileExistsError:
        pass
    return str(uuid.UUID(path.read_text(encoding="ascii").strip()))
