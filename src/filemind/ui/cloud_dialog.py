import sys
from collections.abc import Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from filemind.services.cloud import CloudClient, CloudError, validate_server_url
from filemind.ui.archive_dialog import ArchiveDialog
from filemind.ui.devicelink_dialog import DeviceLinkDialog
from filemind.ui.library_dialog import LibraryDialog
from filemind.ui.share_dialog import ShareDialog
from filemind.ui.widgets import DataTable, button, label


class Results(QObject):
    done = Signal(object)
    failed = Signal(str)
    finished = Signal()


class NetworkTask(QRunnable):
    def __init__(self, operation: Callable):
        super().__init__()
        self.operation = operation
        self.results = Results()

    @Slot()
    def run(self):
        try:
            self.results.done.emit(self.operation())
        except Exception as exc:
            self.results.failed.emit(str(exc))
        finally:
            self.results.finished.emit()


class CloudDialog(QDialog):
    """Optional M1/M2 account connection; existing local pages remain offline."""

    def __init__(self, parent=None, *, retain_session=False):
        super().__init__(parent)
        self.setWindowTitle("FileMind · 服务器文件")
        self.resize(720, 540)
        self.retain_session = retain_session
        self._next_action = None
        self.token = ""
        self.connected_server = ""
        self.binding: tuple[str, str] | None = None
        self.busy = False
        self._task: NetworkTask | None = None
        self._completed: Callable | None = None
        self._closed = False
        self.device_rows: list[dict] = []
        outer = QVBoxLayout(self)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        content = QWidget()
        scroll.setWidget(content)
        outer.addWidget(scroll)
        body = QVBoxLayout(content)
        body.setContentsMargins(24, 24, 24, 24)
        body.setSpacing(14)
        body.addWidget(label("服务器文件", "PageTitle"))
        body.addWidget(label("登录后查看文件、下载或分享。", "Muted"))
        self.login_panel = QWidget()
        login_body = QVBoxLayout(self.login_panel)
        login_body.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout()
        port = 18765 if getattr(sys, "frozen", False) else 8765
        self.server = QLineEdit(f"http://127.0.0.1:{port}")
        self.username, self.password = QLineEdit(), QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.username.setMaxLength(32)
        self.password.setMaxLength(128)
        self.server.setAccessibleName("中央服务地址")
        self.username.setAccessibleName("云端用户名")
        self.password.setAccessibleName("云端密码")
        form.addRow("用户名", self.username)
        form.addRow("密码", self.password)
        login_body.addLayout(form)
        self.connection_button = button("连接设置 ▸")
        self.connection_button.setCheckable(True)
        self.connection_settings = QWidget()
        connection_form = QFormLayout(self.connection_settings)
        connection_form.addRow("服务地址", self.server)
        login_body.addWidget(self.connection_button)
        login_body.addWidget(self.connection_settings)
        self.connection_settings.hide()
        self.connection_button.toggled.connect(self.connection_settings.setVisible)
        actions = QHBoxLayout()
        self.login_button = button("登录", primary=True)
        self.logout_button = button("退出账户")
        self.web_button, self.admin_button = button("打开 Web 用户端"), button("打开 Admin")
        login_body.addWidget(self.login_button)
        body.addWidget(self.login_panel)
        self.workspace_panel = QWidget()
        workspace_body = QVBoxLayout(self.workspace_panel)
        workspace_body.setContentsMargins(0, 0, 0, 0)
        self.link_button = button("发送 / 接收文件")
        self.link_button.setToolTip("首次使用时自动登记本机，再打开设备互传。")
        self.link_button.clicked.connect(self.open_link)
        self.archive_button = button("上传与归档记录")
        self.archive_button.clicked.connect(self.open_archives)
        self.library_button = button("查看服务器文件", primary=True)
        self.library_button.clicked.connect(self.open_library)
        self.share_button = button("分享文件")
        self.share_button.clicked.connect(
            lambda: ShareDialog(self.connected_server, self.token, self).exec()
        )
        for control in (
            self.library_button,
            self.share_button,
            self.link_button,
            self.archive_button,
        ):
            workspace_body.addWidget(control)
        self.advanced_button = button("设备与管理设置 ▸")
        self.advanced_button.setCheckable(True)
        workspace_body.addWidget(self.advanced_button)
        self.advanced_panel = QWidget()
        advanced_body = QVBoxLayout(self.advanced_panel)
        advanced_body.setContentsMargins(0, 0, 0, 0)
        self.advanced_button.toggled.connect(self.advanced_panel.setVisible)
        for control in (self.web_button, self.admin_button):
            actions.addWidget(control)
        advanced_body.addLayout(actions)
        self.feedback = label("尚未连接。没有账号时可在 Web 用户端注册。", "Muted")
        body.addWidget(self.feedback)
        advanced_body.addWidget(label("我的设备", "SectionTitle"))
        self.table = DataTable(("设备", "平台", "状态"))
        self.table.setMinimumHeight(150)
        advanced_body.addWidget(self.table, 1)
        device_actions = QHBoxLayout()
        self.device_name = QLineEdit("我的 Windows 客户端")
        self.device_name.setMaxLength(64)
        self.device_name.setAccessibleName("登记设备名称")
        self.bind_button, self.refresh_button, self.revoke_button = (
            button("登记本机"),
            button("刷新"),
            button("撤销选中设备"),
        )
        device_actions.addWidget(self.device_name, 1)
        for control in (self.bind_button, self.refresh_button, self.revoke_button):
            device_actions.addWidget(control)
        advanced_body.addLayout(device_actions)
        workspace_body.addWidget(self.advanced_panel)
        self.advanced_panel.hide()
        workspace_body.addWidget(self.logout_button)
        body.addWidget(self.workspace_panel)
        self.back_button = button("返回本地整理")
        self.back_button.clicked.connect(self.reject)
        body.addWidget(self.back_button)
        body.addWidget(label("账户仅在本次程序运行期间保留，退出程序后需重新登录。", "Muted"))
        self.login_button.clicked.connect(self.login)
        self.password.returnPressed.connect(self.login)
        self.logout_button.clicked.connect(self.logout)
        self.refresh_button.clicked.connect(self.refresh)
        self.bind_button.clicked.connect(self.bind_device)
        self.revoke_button.clicked.connect(self.revoke)
        self.web_button.clicked.connect(lambda: self.open_web("/app/"))
        self.admin_button.clicked.connect(lambda: self.open_web("/admin/"))
        self.timer = QTimer(self)
        self.timer.setInterval(30000)
        self.timer.timeout.connect(self.heartbeat)
        self.update_controls()

    def update_controls(self):
        self.login_panel.setVisible(not bool(self.token))
        self.workspace_panel.setVisible(bool(self.token))
        for control in (self.server, self.username, self.password, self.login_button):
            control.setEnabled(not self.busy and not self.token)
        for control in (
            self.bind_button,
            self.refresh_button,
            self.revoke_button,
            self.logout_button,
        ):
            control.setEnabled(bool(self.token) and not self.busy)
        self.link_button.setEnabled(bool(self.token) and not self.busy)
        self.archive_button.setEnabled(bool(self.token) and not self.busy)
        self.library_button.setEnabled(bool(self.token) and not self.busy)
        self.share_button.setEnabled(bool(self.token) and not self.busy)

    def open_library(self):
        LibraryDialog(
            self.connected_server, self.binding[1] if self.binding else self.token, self
        ).exec()

    def open_archives(self):
        ArchiveDialog(
            self.connected_server, self.token, self.binding[1] if self.binding else "", self
        ).exec()

    def open_link(self):
        if self.binding:
            DeviceLinkDialog(
                self.connected_server, self.binding[0], self.binding[1], self.username.text(), self
            ).exec()
        else:
            self._next_action = self.open_link
            self.bind_device()

    def submit(self, operation: Callable, completed: Callable):
        if self.busy:
            return
        self.busy = True
        self.update_controls()
        self.feedback.setText("正在连接中央服务…")
        task = NetworkTask(operation)
        self._task = task
        self._completed = completed
        task.results.done.connect(self.handle_result)
        task.results.failed.connect(self.show_error)
        task.results.finished.connect(self.task_finished)
        QThreadPool.globalInstance().start(task)

    @Slot(str)
    def show_error(self, message: str):
        self._next_action = None
        if not self._closed:
            self.feedback.setText("请求失败：" + message)

    @Slot(object)
    def handle_result(self, data):
        if not self._closed and self._completed:
            self._completed(data)

    @Slot()
    def task_finished(self):
        self.busy = False
        self._task = None
        self._completed = None
        if self._closed:
            self.deleteLater()
            return
        self.update_controls()
        if self._next_action:
            action, self._next_action = self._next_action, None
            QTimer.singleShot(0, action)

    def client(self) -> CloudClient:
        return CloudClient(self.connected_server, self.token)

    def login(self):
        try:
            server = validate_server_url(self.server.text())
        except CloudError as exc:
            self.show_error(str(exc))
            return
        username, password = self.username.text(), self.password.text()
        self.password.clear()
        self.connected_server = server
        self.submit(lambda: CloudClient(server).login(username, password), self.logged_in)

    @Slot(object)
    def logged_in(self, data):
        self.token = data["access_token"]
        self.feedback.setText(f"已连接真实账户：{data['user']['display_name']}。请选择要做的事。")

    def refresh(self):
        self.submit(self.client().devices, self.render_devices)

    @Slot(object)
    def render_devices(self, devices):
        self.device_rows = devices
        self.table.set_rows(
            [
                (
                    d["name"],
                    d["platform"],
                    "已撤销" if d["revoked_at"] else "在线" if d["online"] else "离线",
                )
                for d in devices
            ]
        )
        self.feedback.setText(f"已读取 {len(devices)} 条真实设备记录。")

    def bind_device(self):
        name = self.device_name.text().strip()
        if not name:
            self.show_error("请输入设备名称。")
            return
        from filemind.services.sync_identity import installation_id

        identity = installation_id()
        self.submit(lambda: self.client().register_device(name, identity), self.bound)

    @Slot(object)
    def bound(self, data):
        self.binding = (data["device"]["id"], data["device_token"])
        self.timer.start()
        self.feedback.setText("本机设备已登记，心跳已启动。点击刷新查看最新记录。")

    def heartbeat(self):
        if self.binding and not self.busy:
            device_id, token = self.binding
            self.submit(
                lambda: CloudClient(self.connected_server, token).request(
                    "POST", f"/devices/{device_id}/heartbeat"
                ),
                self.heartbeat_received,
            )

    @Slot(object)
    def heartbeat_received(self, data):
        self.feedback.setText("设备心跳已确认。")

    def revoke(self):
        selected = self.table.currentRow()
        if selected < 0:
            self.show_error("请先选中一台设备。")
            return
        device_id = self.device_rows[selected]["id"]
        self._revoking_id = device_id
        self.submit(lambda: self.client().revoke_device(device_id), self.revoked)

    @Slot(object)
    def revoked(self, data):
        if self.binding and self.binding[0] == self._revoking_id:
            self.timer.stop()
            self.binding = None
        self.feedback.setText("选中设备授权已撤销，原凭据立即失效。点击刷新更新列表。")

    def logout(self):
        self.submit(self.client().logout, self.logged_out)

    @Slot(object)
    def logged_out(self, data):
        self.token = ""
        self.binding = None
        self.timer.stop()
        self.device_rows = []
        self.table.set_rows([])
        self.feedback.setText("已退出，服务端会话已撤销。")

    def open_web(self, path):
        try:
            url = validate_server_url(self.server.text())
            QDesktopServices.openUrl(QUrl(url + path))
        except CloudError as exc:
            self.show_error(str(exc))

    def done(self, result):
        if self.retain_session:
            self._next_action = None
            self.password.clear()
            super().done(result)
            return
        self.dispose_session()
        super().done(result)
        if not self.busy:
            self.deleteLater()

    def dispose_session(self):
        self._closed = True
        self.timer.stop()
        self.token = ""
        self.binding = None
        self._next_action = None
        self.password.clear()
