import errno
import os
import shutil
import stat
from pathlib import Path
from threading import Event, RLock

from filemind.domain.local import LocalFile, LocalPlan, PlanEntry
from filemind.infrastructure.file_analysis import (
    Cancelled,
    check_cancel,
    content_hash,
    reject_links,
)
from filemind.infrastructure.local_repository import LocalRepository
from filemind.services.classification import valid_name


def matches(path: Path, file: LocalFile, cancel=None) -> bool:
    reject_links(path)
    current = path.stat()
    return (
        current.st_size == file.size_bytes
        and current.st_mtime_ns == file.modified_ns
        and content_hash(path, cancel) == file.sha256
    )


def target_path(entry: PlanEntry) -> Path:
    valid_name(entry.target_name)
    target = Path(entry.target_dir) / entry.target_name
    if not target.is_absolute() or len(str(target)) > 240:
        raise ValueError("目标必须是绝对路径，且保守限制为 240 个字符以内。")
    reject_links(target)
    return target


def commit_without_replace(source: Path, target: Path):
    if os.name == "nt":
        os.rename(source, target)  # Windows rename refuses an existing destination.
    else:
        os.link(
            source, target
        )  # POSIX rename could overwrite, so use exclusive hard-link creation.
        source.unlink()


def move_without_replace(
    source: Path, target: Path, expected: LocalFile, temporary: Path, cancel: Event | None = None
):
    reject_links(source)
    reject_links(target)
    if not matches(source, expected, cancel):
        raise ValueError("源文件已变化，需重新生成计划。")
    if not source.stat().st_mode & stat.S_IWRITE:
        raise PermissionError("源文件为只读，不执行移动。")
    if str(source).casefold() == str(target).casefold():
        raise ValueError("目标与原路径相同（含大小写差异），未执行。")
    if target.exists():
        raise FileExistsError("目标已存在，拒绝覆盖。")
    target.parent.mkdir(parents=True, exist_ok=True)
    reject_links(target)
    check_cancel(cancel)
    try:
        commit_without_replace(source, target)
        return
    except OSError as exc:
        if exc.errno != errno.EXDEV and getattr(exc, "winerror", None) != 17:
            raise
    # Cross-volume fallback: exclusive temporary copy, fsync + hash, then publish without replace.
    created = False
    try:
        with source.open("rb") as src, temporary.open("xb") as out:
            created = True
            while block := src.read(1024 * 1024):
                check_cancel(cancel)
                out.write(block)
            out.flush()
            os.fsync(out.fileno())
        if content_hash(temporary, cancel) != expected.sha256 or not matches(
            source, expected, cancel
        ):
            raise ValueError("复制校验失败或源文件变化；保留原文件。")
        shutil.copystat(source, temporary)
        check_cancel(cancel)
        commit_without_replace(temporary, target)
        # Verify again before removing the original. Failure leaves both copies for review.
        if not matches(source, expected, cancel):
            raise ValueError("发布副本后源文件变化，原文件保留，需对账。")
        source.unlink()
    finally:
        if created and temporary.exists():
            # Only this operation's exclusively-created, journaled temporary.
            temporary.unlink()


class LocalOrganizer:
    def __init__(self, repository: LocalRepository):
        self.repository = repository
        self.lock = RLock()

    def execute(
        self, plan: LocalPlan, confirmed: bool = False, cancel: Event | None = None, progress=None
    ):
        if not confirmed:
            raise ValueError("必须明确确认整理计划后才能操作真实文件。")
        with self.lock:
            self.repository.begin_task(plan)
            results = []
            for index, entry in enumerate(plan.entries):
                if not entry.selected:
                    continue
                temporary = Path(entry.target_dir) / f".filemind-{entry.id}.part"
                if not self.repository.prepare(plan.id, entry, str(temporary)):
                    continue  # Idempotency: a submitted entry is never executed twice.
                try:
                    target = target_path(entry)
                    check_cancel(cancel)
                    move_without_replace(
                        Path(entry.file.path), target, entry.file, temporary, cancel
                    )
                except Exception as exc:
                    state = "CANCELLED" if isinstance(exc, Cancelled) else "FAILED"
                    self.repository.state(entry.id, state, str(exc))
                else:
                    # If this DB commit fails, PREPARED remains available for reconciliation.
                    self.repository.state(entry.id, "COMPLETED")
                    state = "COMPLETED"
                results.append({"id": entry.id, "state": state})
                if progress:
                    progress(index + 1, len(plan.entries))
            status = "COMPLETED" if all(r["state"] == "COMPLETED" for r in results) else "PARTIAL"
            self.repository.finish_task(plan.id, status)
            return {"task_id": plan.id, "status": status, "results": results}

    def undo(self, task_id: str, confirmed: bool = False, cancel: Event | None = None):
        if not confirmed:
            raise ValueError("请确认撤销。")
        results = []
        with self.lock:
            for row in reversed(self.repository.operations(task_id)):
                if row["state"] != "COMPLETED":
                    continue
                entry = PlanEntry.model_validate_json(row["payload"])
                current, original = target_path(entry), Path(entry.file.path)
                try:
                    check_cancel(cancel)
                    if not matches(current, entry.file, cancel):
                        raise ValueError("整理后文件被修改，拒绝自动撤销。")
                    if original.exists():
                        raise FileExistsError("原路径已被占用，拒绝覆盖。")
                    self.repository.state(entry.id, "UNDO_PREPARED")
                    move_without_replace(
                        current,
                        original,
                        entry.file,
                        original.parent / f".filemind-undo-{entry.id}.part",
                        cancel,
                    )
                except Exception as exc:
                    # Preserve the fact the original move completed, and record why undo is blocked.
                    self.repository.state(entry.id, "COMPLETED", f"撤销受阻：{exc}")
                    results.append({"id": entry.id, "state": "UNDO_BLOCKED", "error": str(exc)})
                else:
                    # A journal commit failure must retain UNDO_PREPARED for reconciliation.
                    self.repository.state(entry.id, "UNDONE")
                    results.append({"id": entry.id, "state": "UNDONE"})
        return results

    def reconcile(self):
        results = []
        with self.lock:
            for row in self.repository.operations():
                if row["state"] not in {"PREPARED", "UNDO_PREPARED"}:
                    continue
                entry = PlanEntry.model_validate_json(row["payload"])
                source, target = Path(entry.file.path), target_path(entry)
                if row["state"] == "UNDO_PREPARED":
                    source, target = target, source
                try:
                    moved = not source.exists() and target.is_file() and matches(target, entry.file)
                    state = (
                        ("UNDONE" if row["state"] == "UNDO_PREPARED" else "COMPLETED")
                        if moved
                        else "NEEDS_REVIEW"
                    )
                    self.repository.state(
                        row["id"], state, "异常退出后检查；不自动删除或覆盖任何文件。"
                    )
                except (OSError, ValueError) as exc:
                    state = "NEEDS_REVIEW"
                    self.repository.state(row["id"], state, str(exc))
                results.append({"id": row["id"], "state": state})
        return results
