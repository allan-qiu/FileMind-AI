"Legacy V1 rejection adapters retained for compatibility; NOT wired by ApplicationService V2.1."

from collections.abc import Callable, Sequence
from pathlib import Path

from filemind.domain.models import FileEvent, FileRecord, OrganizationProposal


class FeatureUnavailableError(RuntimeError):
    pass


class PendingScanner:
    def scan(self, directory: Path) -> Sequence[FileRecord]:
        raise FeatureUnavailableError("第一阶段未启用真实文件扫描。")


class PendingAIClassifier:
    async def classify(self, files: Sequence[FileRecord]) -> Sequence[OrganizationProposal]:
        raise FeatureUnavailableError("第一阶段未连接 AI 服务，不会发送文件信息。")


class PendingWatcher:
    def start(self, directory: Path, on_event: Callable[[FileEvent], None]) -> None:
        raise FeatureUnavailableError("第一阶段未启用文件监控。")

    def stop(self) -> None:
        # Idempotent lifecycle hook. There is no observer to stop yet.
        return None


class DisabledOperationExecutor:
    def execute(self, proposals: Sequence[OrganizationProposal]) -> None:
        raise FeatureUnavailableError("安全限制：第一阶段禁止执行文件移动、重命名、覆盖或删除。")
