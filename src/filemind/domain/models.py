from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Theme(StrEnum):
    LIGHT = "light"
    DARK = "dark"


class FileRecord(BaseModel):
    """Metadata supplied by a future read-only scanner."""

    model_config = ConfigDict(frozen=True)
    path: str
    name: str
    extension: str
    size_bytes: int = Field(ge=0)


class OrganizationProposal(BaseModel):
    """A proposal is data only; it never executes a filesystem action."""

    model_config = ConfigDict(frozen=True)
    source: FileRecord
    category: str
    suggested_name: str
    target_folder: str
    reason: str
    is_demo: bool = True


class FileEvent(BaseModel):
    model_config = ConfigDict(frozen=True)
    path: str
    kind: str
