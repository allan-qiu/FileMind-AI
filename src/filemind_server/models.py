from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Role = Literal["user", "workspace_admin", "system_admin"]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(pattern=r"^[A-Za-z0-9_]{3,32}$")
    password: str = Field(min_length=1, max_length=128)


class Registration(Credentials):
    display_name: str = Field(min_length=1, max_length=64)

    @field_validator("password")
    @classmethod
    def strong_enough(cls, value: str) -> str:
        if len(value) < 10:
            raise ValueError("密码至少需要 10 个字符。")
        return value


class DeviceCreate(Input):
    installation_id: str | None = Field(
        default=None, pattern=r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$"
    )
    name: str = Field(min_length=1, max_length=64)
    platform: Literal["windows", "browser", "android", "ios"] = "browser"


class UserPatch(Input):
    enabled: bool | None = None
    role: Role | None = None
    quota_bytes: int | None = Field(default=None, ge=0, le=1099511627776)


class WorkspaceCreate(Input):
    name: str = Field(min_length=1, max_length=64)


class MemberCreate(Input):
    username: str = Field(pattern=r"^[A-Za-z0-9_]{3,32}$")
    permission: Literal["admin", "editor", "viewer"] = "viewer"


class PlatformPatch(Input):
    registration_open: bool
    announcement: str = Field(default="", max_length=1000)


class TransferCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    receiver_id: str = Field(min_length=1, max_length=64)
    filename: str = Field(min_length=1, max_length=180)
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_key: str = Field(min_length=8, max_length=64)

    @field_validator("filename")
    @classmethod
    def filename_valid(cls, value):
        from filemind.services.classification import valid_name

        return valid_name(value)


class TransferAck(Input):
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmation: Literal["written_file", "browser_received"]


class TransferFailure(Input):
    reason: str = Field(default="用户端报告传输失败", max_length=300)
