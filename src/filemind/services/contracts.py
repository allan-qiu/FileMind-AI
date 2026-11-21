"""Business ports for V2.1 adapters; no Qt dependencies or disk operations here."""

from collections.abc import Sequence
from pathlib import Path
from threading import Event
from typing import Protocol

from filemind.domain.local import ClassificationResult, LocalFile, LocalPlan, ScanResult


class FileScanner(Protocol):
    def scan_many(
        self,
        paths: Sequence[Path],
        cancel: Event | None = None,
        exclude_dirs=(),
        exclude_types=(),
        progress=None,
    ) -> ScanResult:
        """Read-only, bounded analysis performed outside the UI thread."""
        ...


class AIClassifier(Protocol):
    def classify(self, file: LocalFile) -> ClassificationResult:
        """Explicit consent, timeout and validated structured response."""
        ...


class FileWatcher(Protocol):
    def start_enabled(self) -> None: ...
    def pause(self, rule_id: str) -> None: ...
    def start(self, rule_id: str) -> None: ...
    def stop(self) -> None: ...


class OperationExecutor(Protocol):
    def execute(
        self, plan: LocalPlan, confirmed: bool = False, cancel: Event | None = None, progress=None
    ) -> dict: ...

    def undo(self, task_id: str, confirmed: bool = False, cancel: Event | None = None): ...

    def reconcile(self): ...
