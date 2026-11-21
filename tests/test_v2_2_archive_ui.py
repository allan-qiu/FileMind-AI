from PySide6.QtCore import Qt
from test_devicelink_http import real_server  # noqa: F401

from filemind.services.cloud import CloudClient
from filemind.ui.archive_dialog import ArchiveDialog
from filemind_server.ai import AISettings
from filemind_server.ai_control import save_config


def test_native_archive_upload_consent_and_library_over_real_http(
    qtbot,
    real_server,  # noqa: F811
    tmp_path,
    monkeypatch,  # noqa: F811
):
    server, app = real_server
    account = CloudClient(server)
    account.request(
        "POST",
        "/auth/register",
        {
            "username": "qt_archive",
            "display_name": "Qt archive",
            "password": "synthetic-qt-password",
        },
    )
    account.token = account.login("qt_archive", "synthetic-qt-password")["access_token"]
    save_config(app.state.db, "ai_v2_2", AISettings(provider="mock"))
    source = tmp_path / "synthetic.txt"
    source.write_text("Synthetic Python learning notes", encoding="utf-8")
    dialog = ArchiveDialog(server, account.token)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitUntil(lambda: dialog.preferences_loaded, timeout=10000)
    assert dialog.mode.currentData() == "off"
    dialog.mode.setCurrentIndex(dialog.mode.findData("metadata"))
    qtbot.mouseClick(dialog.save_settings, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not dialog.worker.busy, timeout=10000)
    monkeypatch.setattr(
        "filemind.ui.archive_dialog.QFileDialog.getOpenFileName", lambda *a: (str(source), "")
    )
    qtbot.mouseClick(dialog.upload_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not dialog.worker.busy, timeout=10000)
    qtbot.waitUntil(lambda: dialog.library.rowCount() == 1, timeout=10000)
    assert dialog.bar.value() == 100
    assert dialog.library.item(0, 0).text() == source.name
    assert dialog.rows[0]["state"] == "COMPLETED"
    dialog.table.selectRow(0)
    assert dialog.plan_fields["suggested_filename"].text() == source.name
    screenshot = tmp_path / "archive-dialog.png"
    assert dialog.grab().save(str(screenshot))
    dialog.done(0)
    qtbot.waitUntil(lambda: not dialog.poller.busy and not dialog.worker.busy, timeout=10000)
