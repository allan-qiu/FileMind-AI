from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from filemind.ui.main_window import MainWindow


def test_real_folder_import_edit_execute_history_and_undo(qtbot, service, tmp_path, monkeypatch):
    source = tmp_path / "real-input"
    source.mkdir()
    original = source / "原文件.txt"
    original.write_text("真实文件读写与撤销验收", encoding="utf-8")
    output = tmp_path / "real-output"
    window = MainWindow(service)
    qtbot.addWidget(window)
    window.show()
    window.navigate(1)
    page = window.organize
    monkeypatch.setattr(
        "filemind.ui.pages.organize.QFileDialog.getExistingDirectory", lambda *args: str(source)
    )
    monkeypatch.setattr(
        "filemind.ui.pages.organize.QMessageBox.question",
        lambda *args: QMessageBox.StandardButton.Yes,
    )
    qtbot.mouseClick(page.folder_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: bool(page.files) and not page.controller.busy, timeout=10000)
    assert page.files[0].path == str(original)
    page.destination.setText(str(output))
    qtbot.mouseClick(page.generate_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: page.plan is not None and not page.controller.busy)
    page.table.item(0, 4).setText("已整理.txt")
    qtbot.mouseClick(page.execute_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not original.exists() and not page.controller.busy)
    target = output / "文档" / "已整理.txt"
    assert target.read_text(encoding="utf-8") == "真实文件读写与撤销验收"
    window.navigate(5)
    assert window.history.table.rowCount() == 1
    assert window.history.table.item(0, 3).text() == "COMPLETED"
    qtbot.mouseClick(window.history.undo_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: original.exists() and not window.history.controller.busy)
    assert not target.exists()
    assert original.read_text(encoding="utf-8") == "真实文件读写与撤销验收"
    assert "已撤销" in window.history.task_combo.currentText()
    assert service.local.repository.tasks()[0]["status"] == "COMPLETED"
    assert service.local.repository.tasks()[0]["undone_count"] == 1


def test_failed_ai_configuration_does_not_enable_ai():
    import pytest

    from filemind.services.classification import AIProvider

    with pytest.raises(ValueError, match="授权"):
        AIProvider("https://api.example.invalid/v1/chat/completions", "key", "model", False)
