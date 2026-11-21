"""Real Qt→HTTP integration against the separately seeded, loopback-only QA server."""

from pathlib import Path

from PySide6.QtCore import QEventLoop, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from filemind.core.paths import WorkspacePaths
from filemind.services.application import ApplicationService
from filemind.ui.cloud_dialog import CloudDialog
from filemind.ui.main_window import MainWindow


def wait_for(app, condition, timeout=12):
    # A real Qt event loop releases the Python GIL for network workers.
    # Repeated QTest.qWait/processEvents can starve a Python QRunnable.
    loop = QEventLoop()
    poll, deadline = QTimer(), QTimer()
    poll.setInterval(20)
    deadline.setSingleShot(True)
    poll.timeout.connect(lambda: loop.quit() if condition() else None)
    deadline.timeout.connect(loop.quit)
    poll.start()
    deadline.start(timeout * 1000)
    loop.exec()
    poll.stop()
    deadline.stop()
    assert condition(), "Qt/HTTP operation did not reach its expected state."


def main():
    root = Path(__file__).resolve().parents[1]
    app = QApplication([])
    app.setStyle("Fusion")
    service = ApplicationService.create(WorkspacePaths(root / ".artifacts" / "desktop-cloud-qa"))
    window = MainWindow(service)
    window.show()
    dialog = CloudDialog(window)
    dialog.show()
    dialog.username.setText("qa_user")
    dialog.password.setText("Browser-QA-only-2026!")
    QTest.mouseClick(dialog.login_button, Qt.MouseButton.LeftButton)
    wait_for(app, lambda: bool(dialog.token) and not dialog.busy)
    dialog.device_name.setText("Windows 原生联调设备")
    QTest.mouseClick(dialog.bind_button, Qt.MouseButton.LeftButton)
    wait_for(app, lambda: dialog.binding is not None and not dialog.busy)
    device_id = dialog.binding[0]
    dialog.heartbeat()
    wait_for(app, lambda: "心跳已确认" in dialog.feedback.text() and not dialog.busy)
    QTest.mouseClick(dialog.refresh_button, Qt.MouseButton.LeftButton)
    wait_for(app, lambda: any(d["id"] == device_id for d in dialog.device_rows) and not dialog.busy)
    output = root / ".artifacts" / "v2-desktop"
    output.mkdir(parents=True, exist_ok=True)
    assert dialog.grab().save(str(output / "cloud-connected.png"))
    index = next(i for i, d in enumerate(dialog.device_rows) if d["id"] == device_id)
    dialog.table.selectRow(index)
    QTest.mouseClick(dialog.revoke_button, Qt.MouseButton.LeftButton)
    wait_for(app, lambda: "授权已撤销" in dialog.feedback.text() and not dialog.busy)
    assert dialog.binding is None
    QTest.mouseClick(dialog.logout_button, Qt.MouseButton.LeftButton)
    wait_for(app, lambda: not dialog.token and not dialog.busy)
    dialog.close()
    window.close()
    print("PASS: real Qt login → Windows device registration → heartbeat → list → revoke → logout.")


if __name__ == "__main__":
    main()
