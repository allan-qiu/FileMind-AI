import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest

from filemind.core.config import ConfigError
from filemind.domain.models import Theme
from filemind.ui.main_window import MainWindow


@pytest.fixture
def window(qtbot, service):
    widget = MainWindow(service)
    qtbot.addWidget(widget)
    widget.show()
    qtbot.waitExposed(widget)
    return widget


def test_all_seven_pages_are_reachable(window, qtbot):
    assert window.stack.count() == 7
    window.more_button.setChecked(True)
    for index, nav in enumerate(window.nav_buttons):
        qtbot.mouseClick(nav, Qt.MouseButton.LeftButton)
        assert window.stack.currentIndex() == index
        assert nav.isChecked()
        assert nav.text() in window.breadcrumb.text()


def test_preview_filter_and_disabled_operations(window, qtbot, service):
    window.navigate(0)
    qtbot.mouseClick(window.dashboard.preview_button, Qt.MouseButton.LeftButton)
    assert window.stack.currentIndex() == 1
    assert window.organize.table.rowCount() == 6
    window.organize.search.setText("xlsx")
    assert window.organize.table.rowCount() == 1
    window.organize.search.setText("absent")
    assert window.organize.table.rowCount() == 0
    assert window.organize.import_button.isEnabled()
    assert window.monitoring.add_button.isEnabled()
    assert window.plans.create_button.isEnabled()
    for control in (
        window.organize.execute_button,
        window.history.undo_button,
    ):
        assert not control.isEnabled()
    assert service.history_count() == 0


def test_theme_sync_and_persistence(window, qtbot, service):
    qtbot.mouseClick(window.theme_button, Qt.MouseButton.LeftButton)
    assert window.theme == Theme.DARK
    assert window.settings.theme_combo.currentData() == Theme.DARK
    window.settings.theme_combo.setCurrentIndex(0)
    assert window.theme == Theme.LIGHT
    window.resize(1040, 720)
    qtbot.waitUntil(lambda: window.width() == 1040)
    window.navigate(6)
    qtbot.mouseClick(window.settings.save_button, Qt.MouseButton.LeftButton)
    stored = service.config_store.load()
    assert stored.theme == Theme.LIGHT
    assert (stored.window_width, stored.window_height) == (1040, 720)


def test_resize_keeps_navigation_and_content_available(window, qtbot):
    window.resize(880, 620)
    qtbot.waitUntil(lambda: window.width() == 880)
    window.more_button.setChecked(True)
    for index in range(7):
        window.navigate(index)
        assert window.nav_buttons[index].isVisible()
        page = window.stack.currentWidget()
        assert page.viewport().width() > 500
        assert page.widget().width() == page.viewport().width()


def test_assistant_demo_and_keyboard_navigation(window, qtbot):
    window.activateWindow()
    window.setFocus()
    QTest.keySequence(window, QKeySequence("Ctrl+4"))
    qtbot.waitUntil(lambda: window.stack.currentIndex() == 3)
    window.assistant.question.setText("如何归档？")
    qtbot.mouseClick(window.assistant.send_button, Qt.MouseButton.LeftButton)
    assert "本地固定演示回答" in window.assistant.answer.text()
    assert "未调用 AI" in window.assistant.answer.text()


def test_close_restores_theme_and_size(qtbot, service):
    first = MainWindow(service)
    qtbot.addWidget(first)
    first.show()
    first.resize(1000, 700)
    first.set_theme(Theme.DARK)
    assert first.close()
    service.config = service.config_store.load()
    second = MainWindow(service)
    qtbot.addWidget(second)
    assert second.theme == Theme.DARK
    assert (second.width(), second.height()) == (1000, 700)


def test_save_failure_is_visible_and_preserves_config(window, monkeypatch, service):
    messages = []

    def fail(config):
        raise ConfigError("test: read-only directory")

    monkeypatch.setattr(service.config_store, "save", fail)
    monkeypatch.setattr(
        "filemind.ui.main_window.QMessageBox.warning", lambda *args: messages.append(args[2])
    )
    assert window.save_preferences() is False
    assert "保存失败" in window.settings.feedback.text()
    assert "read-only" in messages[0]
    assert window.close() is False
