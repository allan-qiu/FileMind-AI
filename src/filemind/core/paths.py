import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WorkspacePaths:
    """Keep application-owned writes beneath the workspace's data root."""

    root: Path

    @property
    def data_dir(self) -> Path:
        return self.root.resolve() / ".filemind"

    @property
    def config_file(self) -> Path:
        return self.data_dir / "config.json"

    @property
    def database_file(self) -> Path:
        return self.data_dir / "filemind.sqlite3"

    def ensure(self) -> None:
        # Reject a redirected data directory before writing anything.
        if self.data_dir.resolve().parent != self.root.resolve():
            raise ValueError("应用数据目录必须位于项目目录内，不能指向外部位置。")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        for file in (self.config_file, self.database_file):
            if file.is_symlink() or file.resolve().parent != self.data_dir.resolve():
                raise ValueError("应用数据文件不能链接到项目目录之外。")


def project_paths() -> WorkspacePaths:
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        local_app_data = Path(os.getenv("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return WorkspacePaths(local_app_data / "FileMind AI" / "Desktop")
    return WorkspacePaths(Path(__file__).resolve().parents[3])
