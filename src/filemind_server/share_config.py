"""Service-side limits stored through the existing platform settings mechanism."""

from pydantic import BaseModel, ConfigDict, Field

from filemind_server.ai_control import load_config, save_config


class ShareSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allow_temporary: bool = True
    allow_library: bool = True
    default_ttl: int = Field(86400, ge=60, le=2592000)
    max_ttl: int = Field(2592000, ge=60, le=2592000)
    default_downloads: int = Field(3, ge=1, le=10000)
    max_downloads: int = Field(100, ge=1, le=10000)
    max_file_bytes: int = Field(256 * 1024 * 1024, ge=1, le=4 * 1024**3)
    user_temp_bytes: int = Field(512 * 1024 * 1024, ge=1, le=10 * 1024**3)
    total_temp_bytes: int = Field(2 * 1024**3, ge=1, le=20 * 1024**3)
    max_concurrency: int = Field(2, ge=1, le=8)
    user_concurrency: int = Field(2, ge=1, le=8)
    share_concurrency: int = Field(2, ge=1, le=8)
    session_ttl: int = Field(900, ge=5, le=3600)
    cleanup_interval: int = Field(60, ge=1, le=3600)
    upload_ttl: int = Field(3600, ge=5, le=86400)
    bytes_per_second: int = Field(4 * 1024 * 1024, ge=1024, le=128 * 1024 * 1024)
    user_daily_bytes: int = Field(2 * 1024**3, ge=1, le=50 * 1024**3)
    event_retention_days: int = Field(30, ge=1, le=365)


def config(db):
    return load_config(db, "share_v2_4", ShareSettings)


def save(db, value):
    if value.default_ttl > value.max_ttl or value.default_downloads > value.max_downloads:
        from fastapi import HTTPException

        raise HTTPException(422, "默认配置超过上限")
    save_config(db, "share_v2_4", value)
