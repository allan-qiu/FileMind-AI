import os
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from filemind.domain.models import Theme


class AppConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: int = Field(default=1, ge=1, le=1)
    theme: Theme = Theme.LIGHT
    window_width: int = Field(default=1280, ge=880, le=7680)
    window_height: int = Field(default=840, ge=620, le=4320)
    maximized: bool = False


class ConfigError(RuntimeError):
    pass


class ConfigStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> AppConfig:
        if not self.path.exists():
            return AppConfig()
        try:
            return AppConfig.model_validate_json(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ConfigError(f"无法读取配置 {self.path}：{exc}。原文件已保留。") from exc

    def save(self, config: AppConfig) -> None:
        temporary: Path | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Only replace the application's own config, never imported files.
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent, suffix=".tmp", delete=False
            ) as stream:
                temporary = Path(stream.name)
                stream.write(config.model_dump_json(indent=2))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except OSError as exc:
            raise ConfigError(f"无法保存配置：{exc}") from exc
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
