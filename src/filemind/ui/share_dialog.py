import json
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QProgressBar,
    QScrollArea,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from filemind.services.share import ShareClient
from filemind.ui.tasks import TaskController
from filemind.ui.widgets import DataTable, button, label


class ShareDialog(QDialog):
    def __init__(self, server, token, parent=None, version=None):
        super().__init__(parent)
        self.setWindowTitle("FileMind Share — 文件分享")
        self.resize(1050, 850)
        self.client = ShareClient(server, token)
        self.rows, self.local_path, self.closing = [], None, False
        self.worker = TaskController(self)
        self.worker.result.connect(self.completed)
        self.worker.error.connect(lambda error: self.status.setText("操作失败：" + error))
        self.worker.finished.connect(self.controls)
        self.worker.progress.connect(
            lambda n, total: self.progress.setValue(int(100 * n / max(total, 1)))
        )
        outer = QVBoxLayout(self)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        content = QWidget()
        body = QVBoxLayout(content)
        scroll.setWidget(content)
        outer.addWidget(scroll)
        body.addWidget(label("FileMind Share · 文件分享", "PageTitle"))
        body.addWidget(label("点击创建后上传临时文件；正式库分享固定到所选版本。", "Muted"))
        self.status = label("正在读取我的分享…", "Muted")
        self.status.setWordWrap(True)
        body.addWidget(self.status)
        form = QFormLayout()
        self.source = QComboBox()
        self.source.addItems(["本地临时文件", "文件库指定版本", "已有临时文件"])
        self.path = QLineEdit()
        self.path.setReadOnly(True)
        self.choose_local = button("选择本地文件（创建后上传）")
        self.choose_local.clicked.connect(self.pick)
        self.files, self.versions, self.objects = QComboBox(), QComboBox(), QComboBox()
        self.load_library = button("加载文件库 / 临时对象")
        self.load_library.clicked.connect(self.sources)
        self.files.activated.connect(self.choose_file)
        self.maximum = QSpinBox()
        self.maximum.setRange(1, 10000)
        self.maximum.setValue(3)
        self.ttl = QComboBox()
        for title, seconds in [
            ("1 小时", 3600),
            ("24 小时", 86400),
            ("7 天", 604800),
            ("30 天", 2592000),
            ("自定义秒数", 0),
        ]:
            self.ttl.addItem(title, seconds)
        self.ttl.setCurrentIndex(1)
        self.custom_ttl = QSpinBox()
        self.custom_ttl.setRange(60, 2592000)
        self.custom_ttl.setValue(7200)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setMaxLength(128)
        for title, widget in [
            ("文件来源", self.source),
            ("本地文件", self.path),
            ("", self.choose_local),
            ("", self.load_library),
            ("文件库文件", self.files),
            ("固定文件版本", self.versions),
            ("临时文件对象", self.objects),
            ("下载次数", self.maximum),
            ("有效期", self.ttl),
            ("自定义有效秒数", self.custom_ttl),
            ("访问密码（可选）", self.password),
        ]:
            widget.setAccessibleName(title or widget.text())
            form.addRow(title, widget)
        body.addLayout(form)
        self.create_button = button("创建分享链接", primary=True)
        self.create_button.clicked.connect(self.create)
        body.addWidget(self.create_button)
        self.url = QLineEdit()
        self.url.setReadOnly(True)
        self.url.setAccessibleName("新分享链接")
        body.addWidget(self.url)
        self.copy_button = button("复制分享链接")
        self.copy_button.clicked.connect(lambda: QApplication.clipboard().setText(self.url.text()))
        body.addWidget(self.copy_button)
        self.table = DataTable(
            ("文件 / Share ID", "上限", "已完成", "预留", "剩余", "状态", "到期时间")
        )
        self.table.setMinimumHeight(180)
        body.addWidget(self.table)
        actions = QHBoxLayout()
        self.buttons = []
        for title, callback in [
            ("刷新我的分享", self.refresh),
            ("保存选中分享配置", self.update),
            ("撤销选中分享", self.revoke),
            ("下载记录", self.history),
            ("取消当前上传", self.worker.cancel),
        ]:
            w = button(title)
            w.clicked.connect(callback)
            actions.addWidget(w)
            self.buttons.append(w)
        body.addLayout(actions)
        self.change_password = QCheckBox("修改时更新密码（留空移除）；未勾选则保留")
        self.change_expiry = QCheckBox("修改时使用上方有效期（从现在起计时）")
        body.addWidget(self.change_password)
        body.addWidget(self.change_expiry)
        self.records = QTextEdit()
        self.records.setReadOnly(True)
        self.records.setMaximumHeight(160)
        body.addWidget(self.records)
        self.progress = QProgressBar()
        body.addWidget(self.progress)
        body.addWidget(
            label("服务端完整交付计一次；浏览器实际保存与真实设备覆盖另行验证。", "Muted")
        )
        if version:
            self.source.setCurrentIndex(1)
            self.versions.addItem(
                "Version " + str(version["version"]) + " · " + version["filename"], version["id"]
            )
        self.refresh()

    def controls(self):
        if self.closing and not self.worker.busy:
            self.client.control.token = ""
        for w in [self.create_button, self.choose_local, self.load_library, *self.buttons[:-1]]:
            w.setEnabled(not self.worker.busy)

    def submit(self, action):
        if not self.closing and self.worker.submit(action):
            self.controls()

    def refresh(self):
        self.submit(lambda c, p: {"kind": "list", "items": self.client.request("GET", "/shares")})

    def sources(self):
        self.submit(
            lambda c, p: {
                "kind": "sources",
                "files": self.client.request("GET", "/library/search?limit=200")["items"],
                "objects": self.client.request("GET", "/shares/temporary/objects"),
            }
        )

    def choose_file(self):
        fid = self.files.currentData()
        if fid:
            self.submit(
                lambda c, p: {
                    "kind": "versions",
                    "items": self.client.request("GET", f"/library/{fid}/versions"),
                }
            )

    def pick(self):
        name, _ = QFileDialog.getOpenFileName(self, "选择临时分享文件（点击创建才上传）")
        if name:
            self.local_path = Path(name)
            self.path.setText(name)
            self.source.setCurrentIndex(0)

    def values(self):
        return (
            self.maximum.value(),
            self.ttl.currentData() or self.custom_ttl.value(),
            self.password.text(),
        )

    def create(self):
        maximum, ttl, password = self.values()
        source = self.source.currentIndex()
        local, vid, oid = self.local_path, self.versions.currentData(), self.objects.currentData()
        if (source == 0 and not local) or (source == 1 and not vid) or (source == 2 and not oid):
            self.status.setText("请先选择文件来源")
            return

        def work(cancel, progress):
            selected = (
                {"file_version_id": vid}
                if source == 1
                else {"temporary_object_id": oid}
                if source == 2
                else {
                    "temporary_object_id": self.client.upload(local, cancel, progress)["object_id"]
                }
            )
            return {"kind": "created", "link": self.client.create(selected, maximum, ttl, password)}

        self.submit(work)

    def selected(self):
        i = self.table.currentRow()
        return self.rows[i] if 0 <= i < len(self.rows) else None

    def update(self):
        row = self.selected()
        if row:
            maximum, ttl, password = self.values()
            changes = {"max_downloads": maximum}
            if self.change_expiry.isChecked():
                changes["ttl_seconds"] = ttl
            if self.change_password.isChecked():
                changes["password"] = password or None
            self.submit(
                lambda c, p: {
                    "kind": "changed",
                    "link": self.client.request("PATCH", "/shares/" + row["share_id"], changes),
                }
            )

    def revoke(self):
        row = self.selected()
        if row:
            self.submit(
                lambda c, p: {
                    "kind": "changed",
                    "link": self.client.request("POST", "/shares/" + row["share_id"] + "/revoke"),
                }
            )

    def history(self):
        row = self.selected()
        if row:
            self.submit(
                lambda c, p: {
                    "kind": "history",
                    "data": self.client.request("GET", "/shares/" + row["share_id"] + "/downloads"),
                }
            )

    def completed(self, result):
        if self.closing:
            return
        kind = result["kind"]
        if kind == "list":
            from datetime import datetime

            self.rows = result["items"]
            self.table.set_rows(
                [
                    (
                        r["display_name"] + " / " + r["share_id"],
                        str(r["max_downloads"]),
                        str(r["completed_downloads"]),
                        str(r["reserved_downloads"]),
                        str(r["remaining_downloads"]),
                        r["status"],
                        datetime.fromtimestamp(r["expires_at"]).strftime("%Y-%m-%d %H:%M"),
                    )
                    for r in self.rows
                ]
            )
            self.status.setText(f"已读取 {len(self.rows)} 条真实分享记录（最多显示 200 条）")
        elif kind == "sources":
            self.files.clear()
            self.objects.clear()
            for f in result["files"]:
                self.files.addItem(f["filename"], f["file_id"])
            for o in result["objects"]:
                if o["deletion_status"] == "LIVE":
                    self.objects.addItem(o["original_filename"], o["object_id"])
            self.status.setText("已加载来源；选择文件库文件后加载历史版本。")
        elif kind == "versions":
            self.versions.clear()
            for v in result["items"]:
                self.versions.addItem(f"Version {v['version']} · {v['filename']}", v["id"])
        elif kind == "created":
            self.url.setText(self.client.control.server + result["link"]["share_url"])
            self.password.clear()
            self.status.setText("分享已创建。明文链接仅本次显示，请复制保存。")
            from PySide6.QtCore import QTimer

            QTimer.singleShot(100, self.refresh)
        elif kind == "history":
            self.records.setPlainText(json.dumps(result["data"], ensure_ascii=False, indent=2))
        else:
            self.status.setText("服务端配置已更新")
            from PySide6.QtCore import QTimer

            QTimer.singleShot(100, self.refresh)

    def done(self, result):
        self.closing = True
        self.worker.cancel()
        self.url.clear()
        self.password.clear()
        if not self.worker.busy:
            self.client.control.token = ""
        super().done(result)
