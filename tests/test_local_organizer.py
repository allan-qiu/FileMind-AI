import errno
import json
import os
import sqlite3
import time
from pathlib import Path
from threading import Event

import httpx
import pytest

from filemind.infrastructure.database import Database
from filemind.services.classification import AIProvider, valid_name
from filemind.services.local_organizer import target_path
from filemind.services.monitoring import MonitorManager


def plan_for(service, tmp_path, names=("note.txt",)):
    source = tmp_path / "inbox"
    source.mkdir(exist_ok=True)
    for name in names:
        (source / name).write_text("真实文件内容 " + name, encoding="utf-8")
    result = service.scanner.scan_many([source])
    assert len(result.files) == len(names)
    return service.classification.plan(result.files, tmp_path / "sorted")


def test_scanner_excluded_root_and_progress(service, tmp_path):
    skipped = tmp_path / "skip"
    kept = tmp_path / "kept"
    skipped.mkdir()
    kept.mkdir()
    (skipped / "excluded.txt").write_bytes(b"secret")
    (kept / "a.txt").write_bytes(b"a")
    (kept / "b.txt").write_bytes(b"bb")
    progress = []
    result = service.scanner.scan_many(
        [skipped, kept], exclude_dirs=["skip"], progress=lambda n, b: progress.append((n, b))
    )
    assert len(result.files) == 2
    assert progress == [(1, 1), (2, 3)]


def test_real_readonly_source_is_preserved(service, tmp_path):
    import stat

    plan = plan_for(service, tmp_path)
    source = Path(plan.entries[0].file.path)
    source.chmod(stat.S_IREAD)
    try:
        report = service.local.execute(plan, confirmed=True)
        assert report["results"][0]["state"] == "FAILED"
        assert source.exists() and not target_path(plan.entries[0]).exists()
    finally:
        source.chmod(stat.S_IREAD | stat.S_IWRITE)


@pytest.mark.skipif(os.name != "nt", reason="Requires Windows sharing locks")
def test_real_windows_exclusive_file_lock_preserves_source(service, tmp_path):
    import ctypes
    from ctypes import wintypes

    plan = plan_for(service, tmp_path)
    source = Path(plan.entries[0].file.path)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    handle = create(str(source), 0x80000000, 0, None, 3, 0, None)
    assert handle != wintypes.HANDLE(-1).value
    try:
        report = service.local.execute(plan, confirmed=True)
        assert report["results"][0]["state"] == "FAILED"
        assert source.exists() and not target_path(plan.entries[0]).exists()
    finally:
        assert close(handle)


def test_real_scan_categories_summary_and_move_undo(service, tmp_path):
    plan = plan_for(service, tmp_path, ("note.txt", "code.py", "photo.jpg", "unknown.xyz"))
    assert plan.entries[0].file.sha256
    assert {e.category for e in plan.entries} == {"文档", "代码", "图片", "其他"}
    assert any(e.file.summary for e in plan.entries)
    with pytest.raises(ValueError, match="确认"):
        service.local.execute(plan)
    report = service.local.execute(plan, confirmed=True)
    assert report["status"] == "COMPLETED"
    assert all(target_path(e).exists() and not Path(e.file.path).exists() for e in plan.entries)
    assert service.history_count() == 4
    assert all(row["state"] == "UNDONE" for row in service.local.undo(plan.id, confirmed=True))
    assert all(Path(e.file.path).exists() for e in plan.entries)


def test_conflicts_never_overwrite_and_numbering_is_explicit(service, tmp_path):
    plan = plan_for(service, tmp_path)
    target = target_path(plan.entries[0])
    target.parent.mkdir(parents=True)
    target.write_bytes(b"existing-target")
    report = service.local.execute(plan, confirmed=True)
    assert report["results"][0]["state"] == "FAILED"
    assert target.read_bytes() == b"existing-target"
    assert Path(plan.entries[0].file.path).exists()
    numbered = service.classification.plan(
        [plan.entries[0].file], tmp_path / "sorted", numbered=True
    )
    assert numbered.entries[0].target_name == "note_2.txt"
    assert service.local.execute(numbered, confirmed=True)["status"] == "COMPLETED"


@pytest.mark.parametrize("bad", ["CON.txt", "LPT1", "../x", "a/b", "x.", "x ", "", "a:b"])
def test_windows_names_are_checked(bad):
    with pytest.raises(ValueError):
        valid_name(bad)


def test_changed_missing_invalid_and_unselected_entries(service, tmp_path):
    plan = plan_for(service, tmp_path, ("changed.txt", "missing.txt", "invalid.txt", "skip.txt"))
    by_name = {entry.file.name: entry for entry in plan.entries}
    Path(by_name["changed.txt"].file.path).write_text("modified", encoding="utf-8")
    Path(by_name["missing.txt"].file.path).unlink()
    by_name["invalid.txt"].target_name = "CON.txt"
    by_name["skip.txt"].selected = False
    assert service.local.execute(plan, confirmed=True)["status"] == "PARTIAL"
    assert Path(by_name["skip.txt"].file.path).exists()
    assert service.history_count() == 0


def test_cancel_and_io_failure_continue_other_entries(service, tmp_path, monkeypatch):
    import filemind.services.local_organizer as module

    plan = plan_for(service, tmp_path, ("fail.txt", "ok.txt"))
    original = module.commit_without_replace

    def fail_one(source, target):
        if source.name == "fail.txt":
            raise PermissionError("injected locked-file/write-permission failure")
        return original(source, target)

    monkeypatch.setattr(module, "commit_without_replace", fail_one)
    results = service.local.execute(plan, confirmed=True)["results"]
    assert {r["state"] for r in results} == {"FAILED", "COMPLETED"}
    cancel_root = tmp_path / "cancel-case"
    cancel_root.mkdir()
    cancelled = plan_for(service, cancel_root, ("cancel.txt",))
    event = Event()
    event.set()
    service.local.execute(cancelled, confirmed=True, cancel=event)
    assert Path(cancelled.entries[0].file.path).exists()


def test_cross_volume_copy_verifies_before_removing_source(service, tmp_path, monkeypatch):
    import filemind.services.local_organizer as module

    plan = plan_for(service, tmp_path)
    original = module.commit_without_replace

    def cross_device(source, target):
        if source.parent.name == "inbox":
            raise OSError(errno.EXDEV, "cross-device fixture")
        return original(source, target)

    monkeypatch.setattr(module, "commit_without_replace", cross_device)
    assert service.local.execute(plan, confirmed=True)["status"] == "COMPLETED"
    assert target_path(plan.entries[0]).read_bytes().decode("utf-8").startswith("真实文件内容")
    assert not list((tmp_path / "sorted").rglob("*.part"))


@pytest.mark.parametrize("conflict", ["occupied", "modified"])
def test_undo_refuses_new_original_or_modified_destination(service, tmp_path, conflict):
    plan = plan_for(service, tmp_path)
    service.local.execute(plan, confirmed=True)
    entry = plan.entries[0]
    if conflict == "occupied":
        Path(entry.file.path).write_bytes(b"new unrelated file")
    else:
        target_path(entry).write_bytes(b"edited after move")
    assert service.local.undo(plan.id, confirmed=True)[0]["state"] == "UNDO_BLOCKED"
    assert target_path(entry).exists()


def test_prepared_journal_recovers_after_file_move_before_db_commit(service, tmp_path, monkeypatch):
    plan = plan_for(service, tmp_path)
    repository = service.local.repository
    original = repository.state

    def crash(op_id, state, error=""):
        if state == "COMPLETED":
            raise sqlite3.OperationalError("simulated DB failure after atomic move")
        original(op_id, state, error)

    monkeypatch.setattr(repository, "state", crash)
    with pytest.raises(sqlite3.OperationalError):
        service.local.execute(plan, confirmed=True)
    assert repository.operations(plan.id)[0]["state"] == "PREPARED"
    monkeypatch.setattr(repository, "state", original)
    assert service.local.reconcile()[0]["state"] == "COMPLETED"
    assert service.history_count() == 1


def test_scan_cancel_exclusions_and_unparseable_document(service, tmp_path):
    (tmp_path / "bad.pdf").write_bytes(b"not a pdf")
    (tmp_path / "skip.tmp").write_bytes(b"excluded")
    excluded = tmp_path / "excluded"
    excluded.mkdir()
    (excluded / "private.txt").write_bytes(b"excluded directory")
    scan = service.scanner.scan_many([tmp_path], exclude_dirs=["excluded"], exclude_types=["tmp"])
    assert [file.name for file in scan.files] == ["bad.pdf"]
    assert scan.files[0].summary == ""
    cancelled = Event()
    cancelled.set()
    assert service.scanner.scan_many([tmp_path], cancel=cancelled).cancelled


def test_ai_unavailable_does_not_silently_classify_as_ai(service, tmp_path):
    plan = plan_for(service, tmp_path)
    transport = httpx.MockTransport(lambda request: httpx.Response(503, text="unavailable"))
    provider = AIProvider("http://127.0.0.1/ai", "test-key", "fixture", True, transport)
    with pytest.raises(ValueError, match="AI 调用失败"):
        service.classification.plan([plan.entries[0].file], tmp_path / "out", provider=provider)
    good = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "category": "资料",
                                    "name": "标题.txt",
                                    "reason": "fixture response",
                                }
                            )
                        }
                    }
                ]
            },
        )
    )
    provider = AIProvider("http://127.0.0.1/ai", "test-key", "fixture", True, good)
    actual = service.classification.plan(
        [plan.entries[0].file], tmp_path / "out", provider=provider
    )
    assert actual.entries[0].origin == "ai:fixture"
    assert "test-key" not in actual.model_dump_json()


def test_watchdog_debounces_stable_file_and_avoids_output_loop(service, tmp_path):
    source, destination = tmp_path / "watch", tmp_path / "watch" / "output"
    source.mkdir()
    manager = MonitorManager(service.local.repository, service.local, stable_seconds=0.4)
    rule = manager.add(source, destination, auto_execute=True)
    manager.start(rule)
    try:
        file = source / "watched.txt"
        with file.open("w", encoding="utf-8") as stream:
            for index in range(4):
                stream.write(f"part {index}\n")
                stream.flush()
                os.fsync(stream.fileno())
                time.sleep(0.15)
                assert file.exists()
        target = destination / "文档" / file.name
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and not target.exists():
            time.sleep(0.1)
        assert target.exists()
        time.sleep(0.7)
        assert service.history_count() == 1
        assert any(e["state"] == "COMPLETED" for e in manager.events())
    finally:
        manager.stop()
    assert not manager.observers


def test_v1_upgrade_preserves_history_and_creates_backup(tmp_path):
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE app_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        conn.execute(
            "CREATE TABLE operation_history(id INTEGER PRIMARY KEY,action "
            "TEXT,source_path TEXT,target_path TEXT,created_at TEXT,status TEXT)"
        )
        conn.execute(
            "INSERT INTO operation_history VALUES(1,'move','old','new','time','COMPLETED')"
        )
        conn.execute("PRAGMA user_version=1")
    Database(path).initialize()
    assert Database(path).history_count() == 1
    assert list((tmp_path / "backups").glob("before-v2-*.sqlite3"))
