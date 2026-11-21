from PySide6.QtCore import Qt
from test_devicelink_http import devices

from filemind.ui.devicelink_dialog import DeviceLinkDialog

pytest_plugins = ["test_devicelink_http"]


def test_two_native_qt_windows_transfer_real_file_over_http(
    qtbot, real_server, tmp_path, monkeypatch
):
    server, _ = real_server
    (sender, sender_id), (receiver, receiver_id) = devices(server, ("windows", "windows"))
    source = tmp_path / "qt-source.txt"
    source.write_text("两个 Qt 窗口经真实中央 HTTP 中继", encoding="utf-8")
    target = tmp_path / "qt-received.txt"
    rx = DeviceLinkDialog(server, receiver_id, receiver.control.token, "receiver")
    tx = DeviceLinkDialog(server, sender_id, sender.control.token, "sender")
    qtbot.addWidget(rx)
    qtbot.addWidget(tx)
    rx.show()
    tx.show()
    qtbot.waitUntil(lambda: tx.peers.count() > 0, timeout=10000)
    monkeypatch.setattr(
        "filemind.ui.devicelink_dialog.QFileDialog.getOpenFileName", lambda *args: (str(source), "")
    )
    monkeypatch.setattr(
        "filemind.ui.devicelink_dialog.QFileDialog.getSaveFileName", lambda *args: (str(target), "")
    )
    qtbot.mouseClick(tx.choose_button, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(tx.send_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(
        lambda: any(row["state"] == "WAITING_RECEIVER" for row in rx.rows), timeout=10000
    )
    index = next(i for i, row in enumerate(rx.rows) if row["state"] == "WAITING_RECEIVER")
    rx.table.selectRow(index)
    qtbot.mouseClick(rx.receive_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(
        lambda: target.exists() and not rx.data_job.busy and not tx.data_job.busy, timeout=20000
    )
    assert target.read_bytes() == source.read_bytes()
    assert "COMPLETED" in rx.feedback.text() or any(row["state"] == "COMPLETED" for row in rx.rows)
    for dialog in (tx, rx):
        dialog.timer.stop()
        dialog.done(0)
        qtbot.waitUntil(
            lambda d=dialog: (
                not any(c.busy for c in (d.poller, d.data_job, d.cancel_job, d.archive_job))
            ),
            timeout=10000,
        )
