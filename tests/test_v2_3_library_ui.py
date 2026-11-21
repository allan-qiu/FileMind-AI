import hashlib
import threading

from test_devicelink_http import real_server  # noqa: F401

from filemind.services.archive import ArchiveClient
from filemind.services.cloud import CloudClient
from filemind.ui.library_dialog import LibraryDialog
from filemind_server.ai import AISettings
from filemind_server.ai_control import save_config


def test_qt_library_real_http_search_rename_download_and_session_cleanup(
    qtbot,
    real_server,  # noqa: F811
    tmp_path,
    monkeypatch,  # noqa: F811
):  # noqa: F811
    server, app = real_server
    account = CloudClient(server)
    account.request(
        "POST",
        "/auth/register",
        {
            "username": "qt_library",
            "display_name": "Qt Library",
            "password": "synthetic-password-123",
        },
    )
    account.token = account.login("qt_library", "synthetic-password-123")["access_token"]
    account.request("PUT", "/archive/preferences", {"auto_archive": False, "ai_mode": "metadata"})
    save_config(app.state.db, "ai_v2_2", AISettings(provider="mock"))
    source = tmp_path / "Python.txt"
    source.write_bytes(b"Python native library synthetic bytes")
    job = ArchiveClient(server, account.token).create_upload(source)
    qtbot.waitUntil(
        lambda: app.state.archives.row(job["id"])["state"] == "COMPLETED", timeout=10000
    )
    from filemind.services.library import LibraryClient

    original, threads = LibraryClient.sync, []

    def sync(self, cancel=None):
        threads.append(threading.get_ident())
        return original(self, cancel)

    monkeypatch.setattr(LibraryClient, "sync", sync)
    dialog = LibraryDialog(server, account.token)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitUntil(lambda: dialog.table.rowCount() == 1 and not dialog.worker.busy, timeout=10000)
    assert threads and all(t != threading.get_ident() for t in threads)
    dialog.table.selectRow(0)
    qtbot.waitUntil(lambda: dialog.selected is not None and not dialog.worker.busy, timeout=10000)
    assert dialog.versions.count() == 1
    assert "SHA-256" in dialog.version_label.text()
    target = tmp_path / "downloaded.txt"
    monkeypatch.setattr(
        "filemind.ui.library_dialog.QFileDialog.getSaveFileName", lambda *a: (str(target), "")
    )
    dialog.download()
    qtbot.waitUntil(lambda: target.exists() and not dialog.worker.busy, timeout=10000)
    assert (
        hashlib.sha256(target.read_bytes()).digest() == hashlib.sha256(source.read_bytes()).digest()
    )
    qtbot.waitUntil(lambda: not dialog.worker.busy, timeout=10000)
    dialog.table.clearSelection()
    dialog.table.selectRow(0)
    qtbot.waitUntil(lambda: dialog.selected is not None and not dialog.worker.busy, timeout=10000)
    dialog.name_edit.setText("重命名笔记.txt")
    dialog.save()
    qtbot.waitUntil(lambda: not dialog.worker.busy, timeout=10000)
    dialog.load()
    qtbot.waitUntil(
        lambda: (
            dialog.rows
            and dialog.rows[0]["filename"] == "重命名笔记.txt"
            and not dialog.worker.busy
        ),
        timeout=10000,
    )
    assert dialog.grab().save(str(tmp_path / "library-qt.png"))
    dialog.done(0)
    qtbot.waitUntil(lambda: not dialog.worker.busy, timeout=10000)
    assert dialog.client.cache.rows() == [] and not dialog.client.control.token
