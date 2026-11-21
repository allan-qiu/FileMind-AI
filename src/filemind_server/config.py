import os
from pathlib import Path

from dotenv import dotenv_values
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseModel):
    data_dir: Path = PROJECT_ROOT / ".filemind-server"
    session_seconds: int = Field(default=28800, ge=60, le=604800)
    secure_cookies: bool = False
    registration_open: bool = True
    allowed_origins: tuple[str, ...] = (
        "http://127.0.0.1:8765",
        "http://localhost:8765",
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    )
    login_limit: int = Field(default=10, ge=1, le=100)
    relay_enabled: bool = True
    transfer_max_bytes: int = Field(default=256 * 1024 * 1024, ge=1, le=1024 * 1024 * 1024)
    transfer_chunk_bytes: int = Field(default=256 * 1024, ge=4096, le=1024 * 1024)
    transfer_concurrency: int = Field(default=2, ge=1, le=8)
    transfer_pending_limit: int = Field(default=100, ge=1, le=1000)
    transfer_timeout: float = Field(default=30, ge=1, le=300)
    transfer_ttl: int = Field(default=900, ge=5, le=3600)
    transfer_bytes_per_second: int = Field(default=4 * 1024 * 1024, ge=1024, le=128 * 1024 * 1024)
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost")
    web_dist: Path = PROJECT_ROOT / "web" / "dist"
    allow_paid_ai: bool = False
    archive_worker_enabled: bool = True
    baidu_formal_authorized: bool = False
    baidu_remote_enabled: bool = False
    baidu_writes_enabled: bool = False
    baidu_app_root: str = ""
    verified_read_cache_bytes: int = Field(default=512 * 1024**2, ge=1024**2, le=4 * 1024**3)

    @classmethod
    def from_env(cls) -> "Settings":
        values = {**dotenv_values(PROJECT_ROOT / ".env"), **os.environ}
        root = Path(values.get("FILEMIND_SERVER_DATA_ROOT", str(PROJECT_ROOT)))
        if not root.is_absolute() or root.resolve() == Path(root.anchor):
            raise ValueError("显式服务数据根目录必须是独立绝对路径，不能是卷根目录。")
        root = root.resolve()
        data_dir = (root / values.get("FILEMIND_SERVER_DATA", ".filemind-server")).absolute()
        if (
            not data_dir.resolve().is_relative_to(root)
            or data_dir.resolve() == root
            or data_dir.resolve() != data_dir
        ):
            raise ValueError("服务端数据必须位于指定根目录的独立、非链接子目录。")
        return cls(
            relay_enabled=values.get("FILEMIND_RELAY_ENABLED", "true").lower() == "true",
            allow_paid_ai=values.get("FILEMIND_ALLOW_PAID_AI", "false").lower() == "true",
            archive_worker_enabled=values.get("FILEMIND_ARCHIVE_WORKER", "true").lower() == "true",
            baidu_formal_authorized=values.get("FILEMIND_BAIDU_FORMAL_AUTHORIZED", "false").lower()
            == "true",
            baidu_remote_enabled=values.get("FILEMIND_BAIDU_REMOTE_ENABLED", "false").lower()
            == "true",
            baidu_writes_enabled=values.get("FILEMIND_BAIDU_WRITES_ENABLED", "false").lower()
            == "true",
            baidu_app_root=values.get("FILEMIND_BAIDU_APP_ROOT", ""),
            verified_read_cache_bytes=int(
                values.get("FILEMIND_READ_CACHE_BYTES", str(512 * 1024**2))
            ),
            data_dir=data_dir,
            secure_cookies=values.get("FILEMIND_SECURE_COOKIES", "false").lower() == "true",
            registration_open=values.get("FILEMIND_REGISTRATION_OPEN", "true").lower() == "true",
            session_seconds=int(values.get("FILEMIND_SESSION_SECONDS", "28800")),
            transfer_max_bytes=int(
                values.get("FILEMIND_TRANSFER_MAX_BYTES", str(256 * 1024 * 1024))
            ),
            transfer_chunk_bytes=int(values.get("FILEMIND_TRANSFER_CHUNK_BYTES", str(256 * 1024))),
            transfer_concurrency=int(values.get("FILEMIND_TRANSFER_CONCURRENCY", "2")),
            transfer_pending_limit=int(values.get("FILEMIND_TRANSFER_PENDING_LIMIT", "100")),
            transfer_bytes_per_second=int(
                values.get("FILEMIND_TRANSFER_BYTES_PER_SECOND", str(4 * 1024 * 1024))
            ),
            transfer_timeout=float(values.get("FILEMIND_TRANSFER_TIMEOUT", "30")),
            transfer_ttl=int(values.get("FILEMIND_TRANSFER_TTL", "900")),
            allowed_hosts=tuple(
                values.get("FILEMIND_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")
            ),
            allowed_origins=tuple(
                values.get("FILEMIND_ALLOWED_ORIGINS", ",".join(cls().allowed_origins)).split(",")
            ),
        )
