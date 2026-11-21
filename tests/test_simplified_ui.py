"""Exercise the reduced workflow and session lifetime, without external requests."""

from filemind.services.cloud import CloudClient, CloudError
from filemind.ui.cloud_dialog import CloudDialog
from filemind.ui.main_window import MainWindow


def test_simple_start_and_advanced_navigation(qtbot, service):
    window = MainWindow(service)
    qtbot.addWidget(window)
    window.show()
    assert window.stack.currentWidget() is window.organize
    assert window.nav_buttons[1].isVisible()
    assert window.server_nav.isVisible()
    assert not window.nav_buttons[2].isVisible()
    assert not window.organize.options.isVisible()
    window.organize.options_button.click()
    assert window.organize.custom_rules.isVisible()
    window.navigate(2)
    assert window.nav_buttons[2].isVisible()
    assert window.stack.currentWidget() is window.monitoring


def test_return_preserves_session_but_app_exit_clears_it(qtbot, service, monkeypatch):
    window = MainWindow(service)
    qtbot.addWidget(window)
    monkeypatch.setattr(CloudDialog, "exec", lambda self: 0)
    window.open_cloud()
    dialog = window.cloud_dialog
    dialog.token = "synthetic-session"
    dialog.binding = ("synthetic-device", "synthetic-device-token")
    dialog.update_controls()
    dialog.show()
    assert not dialog.login_panel.isVisible()
    assert dialog.library_button.isVisible()
    assert not dialog.advanced_panel.isVisible()
    dialog.reject()
    assert dialog.token == "synthetic-session"
    window.open_cloud()
    assert window.cloud_dialog is dialog
    assert dialog.binding is not None
    assert window.close()
    assert not dialog.token and not dialog.binding


def test_send_registers_once_and_failure_does_not_open_transfer(qtbot, monkeypatch):
    dialog = CloudDialog()
    qtbot.addWidget(dialog)
    dialog.token = "synthetic-session"
    dialog.connected_server = "http://127.0.0.1:8765"
    opened, registered = [], []
    class TransferWindow:
        def __init__(self, *args):
            pass

        def exec(self):
            opened.append(True)

    monkeypatch.setattr("filemind.ui.cloud_dialog.DeviceLinkDialog", TransferWindow)

    def register(*args):
        registered.append(True)
        return {"device": {"id": "synthetic-device"}, "device_token": "synthetic-token"}

    monkeypatch.setattr(CloudClient, "register_device", register)
    dialog.open_link()
    qtbot.waitUntil(lambda: len(opened) == 1 and not dialog.busy)
    dialog.open_link()
    assert len(registered) == 1 and len(opened) == 2
    dialog.timer.stop()
    dialog.binding = None

    def fail(*args):
        raise CloudError("测试：连接不可用")

    monkeypatch.setattr(CloudClient, "register_device", fail)
    dialog.open_link()
    qtbot.waitUntil(lambda: not dialog.busy)
    assert len(opened) == 2
    assert dialog._next_action is None
    assert "连接不可用" in dialog.feedback.text()
