import uuid

from pydantic import BaseModel, ConfigDict, Field

from filemind.domain.models import FileRecord


class LocalFile(FileRecord):
    modified_ns: int
    created_at: float | None = None
    sha256: str
    device: int
    inode: int
    kind: str
    summary: str = ""


class ClassificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=180)
    reason: str = Field(default="", max_length=500)
    origin: str = "rule"


class PlanEntry(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    file: LocalFile
    target_dir: str
    target_name: str
    category: str
    origin: str
    reason: str = ""
    selected: bool = True
    conflict: bool = False
    requires_confirmation: bool = True
    status: str = "WAITING_CONFIRMATION"
    action: str = "move"


class LocalPlan(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    entries: list[PlanEntry]
    mode: str = "rule"


class ScanResult(BaseModel):
    files: list[LocalFile]
    issues: list[str] = Field(default_factory=list)
    cancelled: bool = False

    @property
    def total_bytes(self) -> int:
        return sum(file.size_bytes for file in self.files)
