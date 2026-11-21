import pytest


def test_preview_is_explicit_demo_and_does_not_record_operations(service):
    proposals = service.demo.preview()
    assert len(proposals) == 6
    assert all(p.is_demo and p.source.path.startswith("demo://") for p in proposals)
    assert service.database.history_count() == 0
    assert len(service.demo.preview("xlsx")) == 1
    assert service.demo.preview("not-a-file") == ()


def test_real_scan_is_readonly_and_demo_cannot_be_executed(service, tmp_path):
    real_fixture = tmp_path / "important.txt"
    real_fixture.write_bytes(b"keep this file")
    before = real_fixture.read_bytes()
    assert any(file.name == "important.txt" for file in service.scanner.scan(tmp_path))
    assert service.ai is None
    with pytest.raises(ValueError, match="确认"):
        service.executor.execute(service.demo.preview())
    service.watcher.stop()
    assert real_fixture.read_bytes() == before
    assert service.database.history_count() == 0


def test_assistant_is_clearly_labeled(service):
    assert "未调用 AI" in service.demo.assistant_reply("如何整理文件？")
