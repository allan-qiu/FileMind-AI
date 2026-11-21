# ruff: noqa: F811
import threading

from test_devicelink_http import real_server  # noqa: F401
from test_server import PASSWORD

from filemind.services.cloud import CloudClient
from filemind.services.share import ShareClient
from filemind.ui.share_dialog import ShareDialog


def test_qt_share_upload_manage_background_real_http(qtbot, real_server, tmp_path, monkeypatch):
    url, app = real_server
    account = CloudClient(url)
    account.request(
        "POST",
        "/auth/register",
        {"username": "share_qt", "display_name": "Qt Share", "password": PASSWORD},
    )
    account.token = account.login("share_qt", PASSWORD)["access_token"]
    original = ShareClient.upload
    threads = []

    def upload(self, *args, **kwargs):
        threads.append(threading.get_ident())
        return original(self, *args, **kwargs)

    monkeypatch.setattr(ShareClient, "upload", upload)
    dialog = ShareDialog(url, account.token)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitUntil(lambda: not dialog.worker.busy, timeout=10000)
    source = tmp_path / "share-qt.txt"
    source.write_bytes(b"Qt real HTTP share" * 5000)
    dialog.local_path = source
    dialog.path.setText(str(source))
    dialog.maximum.setValue(1)
    dialog.create()
    qtbot.waitUntil(lambda: bool(dialog.url.text()) and not dialog.worker.busy, timeout=10000)
    assert threads and all(t != threading.get_ident() for t in threads)
    dialog.copy_button.click()
    from PySide6.QtWidgets import QApplication

    assert QApplication.clipboard().text() == dialog.url.text()
    qtbot.waitUntil(lambda: len(dialog.rows) == 1 and not dialog.worker.busy, timeout=10000)
    dialog.table.selectRow(0)
    dialog.maximum.setValue(3)
    dialog.update()
    qtbot.waitUntil(
        lambda: (
            len(dialog.rows) == 1
            and dialog.rows[0]["max_downloads"] == 3
            and not dialog.worker.busy
        ),
        timeout=10000,
    )
    dialog.table.selectRow(0)
    dialog.history()
    qtbot.waitUntil(
        lambda: "CREATED" in dialog.records.toPlainText() and not dialog.worker.busy, timeout=10000
    )
    # No share secret is captured in evidence or persisted to files.
    dialog.url.setText("[share link redacted]")
    assert dialog.grab().save(str(tmp_path / "share-qt-redacted.png"))
    dialog.table.selectRow(0)
    dialog.revoke()
    qtbot.waitUntil(
        lambda: dialog.rows and dialog.rows[0]["status"] == "REVOKED" and not dialog.worker.busy,
        timeout=10000,
    )
    dialog.done(0)
    assert not dialog.client.control.token
