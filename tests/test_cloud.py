import httpx
import pytest
from PySide6.QtCore import Qt

from filemind.services.cloud import CloudClient, CloudError, validate_server_url
from filemind.ui.cloud_dialog import CloudDialog


@pytest.mark.parametrize(
    "value",
    [
        "file:///tmp",
        "http://example.com",
        "http://user:pass@localhost",
        "http://localhost/path",
        "https://example.com?key=secret",
    ],
)
def test_cloud_endpoint_validation(value):
    with pytest.raises(CloudError):
        validate_server_url(value)


def test_cloud_client_keeps_credentials_in_request_headers():
    def handle(request):
        assert request.headers["Authorization"] == "Bearer memory-token"
        assert request.url.path == "/api/devices"
        return httpx.Response(200, json=[])

    client = CloudClient("http://127.0.0.1:8765", "memory-token", httpx.MockTransport(handle))
    assert client.devices() == []


def test_incompatible_cloud_response_is_rejected_without_exposing_token():
    client = CloudClient(
        "http://127.0.0.1:8765",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"access_token": "private-response-token"})
        ),
    )
    with pytest.raises(CloudError, match="不兼容") as failure:
        client.login("test_user", "example-password")
    assert "private-response-token" not in str(failure.value)


def test_close_during_network_request_ignores_late_credentials(qtbot, monkeypatch):
    from threading import Event

    release = Event()

    def delayed_login(*args):
        assert release.wait(timeout=3)
        return {"access_token": "late-token", "user": {"display_name": "late"}}

    monkeypatch.setattr(CloudClient, "login", delayed_login)
    dialog = CloudDialog()
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.mouseClick(dialog.login_button, Qt.MouseButton.LeftButton)
    assert dialog.busy
    dialog.close()
    release.set()
    qtbot.waitUntil(lambda: not dialog.busy)
    assert not dialog.token


def test_cloud_dialog_login_device_and_logout(qtbot, monkeypatch):
    dialog = CloudDialog()
    qtbot.addWidget(dialog)
    monkeypatch.setattr(
        CloudClient,
        "login",
        lambda *args: {
            "access_token": "temporary-test-token",
            "user": {"display_name": "测试账户"},
        },
    )
    monkeypatch.setattr(
        CloudClient,
        "devices",
        lambda *args: [
            {
                "id": "test-device",
                "name": "电脑",
                "platform": "windows",
                "revoked_at": None,
                "online": True,
            }
        ],
    )
    monkeypatch.setattr(CloudClient, "logout", lambda *args: {"ok": True})
    dialog.show()
    dialog.username.setText("test_user")
    dialog.password.setText("test-password")
    qtbot.mouseClick(dialog.login_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: bool(dialog.token) and not dialog.busy)
    assert dialog.password.text() == ""
    qtbot.mouseClick(dialog.refresh_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: dialog.table.rowCount() == 1 and not dialog.busy)
    assert dialog.table.item(0, 0).text() == "电脑"
    qtbot.mouseClick(dialog.logout_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not dialog.token and not dialog.busy)
    assert dialog.table.rowCount() == 0


def test_cloud_connection_failure_keeps_offline_features_usable(qtbot, service, monkeypatch):
    from filemind.ui.main_window import MainWindow

    def unavailable(*args):
        raise CloudError("本地服务未启动")

    monkeypatch.setattr(CloudClient, "login", unavailable)
    window = MainWindow(service)
    qtbot.addWidget(window)
    window.show()
    dialog = CloudDialog(window)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.mouseClick(dialog.login_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "本地服务未启动" in dialog.feedback.text())
    assert not dialog.token
    window.open_demo()
    assert window.organize.table.rowCount() == 6
