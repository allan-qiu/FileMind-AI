import time
from pathlib import Path

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QProgressBar,
    QVBoxLayout,
)

from filemind.services.archive import ArchiveClient
from filemind.services.devicelink import DeviceLinkClient
from filemind.ui.tasks import TaskController
from filemind.ui.widgets import DataTable, button, label


class DeviceLinkDialog(QDialog):
    transfer_created = Signal(str)

    def __init__(self, server, device_id, token, username, parent=None):
        super().__init__(parent)
        self.setWindowTitle("FileMind DeviceLink · 中央中继互传")
        self.resize(1020, 740)
        self.client = DeviceLinkClient(server, token)
        self.device_id, self.rows, self.current_id = device_id, [], None
        self.closing = False
        self.archive_sources = {}
        self.archive_job = TaskController(self)
        self.archive_job.result.connect(self.archived)
        self.archive_job.error.connect(
            lambda text: self.feedback.setText("即时交付已完成；后台归档等待重试：" + text)
        )
        self.archive_job.finished.connect(self.controls)
        self.poller, self.data_job, self.cancel_job = (TaskController(self) for _ in range(3))
        self.poller.result.connect(self.polled)
        self.poller.error.connect(self.error)
        self.data_job.result.connect(self.completed)
        self.data_job.error.connect(self.error)
        self.data_job.progress.connect(self.progress)
        self.cancel_job.result.connect(
            lambda row: self.feedback.setText("取消状态：" + row["state"])
        )
        self.cancel_job.error.connect(self.error)
        for controller in (self.poller, self.data_job, self.cancel_job):
            controller.finished.connect(self.controls)
        self.transfer_created.connect(self.created)
        body = QVBoxLayout(self)
        body.setContentsMargins(24, 24, 24, 24)
        body.setSpacing(14)
        body.addWidget(label("DeviceLink 跨设备互传", "PageTitle"))
        body.addWidget(label(f"账户：{username}  /  当前绑定设备：{device_id}", "Muted"))
        body.addWidget(
            label(
                "发送设备 → 中央透明分块中继 → 接收设备。"
                "接收端校验后确认才完成；DeviceLink不支持续传，后台归档独立执行。",
                "Safety",
            )
        )
        self.peers = QComboBox()
        self.peers.setAccessibleName("在线可接收设备")
        body.addWidget(label("选择最近 10 秒内在线监听的接收设备"))
        body.addWidget(self.peers)
        row = QHBoxLayout()
        self.source = QLineEdit()
        self.source.setAccessibleName("发送文件路径")
        self.source.setPlaceholderText("选择一个真实文件（发送只读，不移动源文件）")
        self.choose_button = button("选择文件")
        self.choose_button.clicked.connect(self.choose)
        self.send_button = button("发送请求", primary=True)
        self.send_button.clicked.connect(self.send)
        row.addWidget(self.source, 1)
        row.addWidget(self.choose_button)
        row.addWidget(self.send_button)
        body.addLayout(row)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        body.addWidget(self.bar)
        self.feedback = label("正在监听本设备收件箱…", "Muted")
        body.addWidget(self.feedback)
        self.table = DataTable(
            ("文件", "发送设备", "接收设备", "状态", "字节进度", "完成确认 / 错误")
        )
        self.table.setMinimumHeight(300)
        body.addWidget(self.table, 1)
        actions = QHBoxLayout()
        self.receive_button, self.reject_button = (
            button("接收选中任务并选择保存位置"),
            button("拒绝选中任务"),
        )
        self.cancel_button, self.refresh_button = button("取消当前传输"), button("刷新")
        self.receive_button.clicked.connect(self.receive)
        self.reject_button.clicked.connect(self.reject)
        self.cancel_button.clicked.connect(self.cancel)
        self.refresh_button.clicked.connect(self.poll)
        for widget in (
            self.receive_button,
            self.reject_button,
            self.cancel_button,
            self.refresh_button,
        ):
            actions.addWidget(widget)
        body.addLayout(actions)
        body.addWidget(
            label(
                "一次发送或接收一个文件；历史无服务器文件副本下载。关闭窗口会取消未完成的本机任务。",
                "Muted",
            )
        )
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.poll)
        self.timer.start()
        self.poll()
        self.controls()

    def controls(self):
        busy = self.data_job.busy
        for widget in (self.send_button, self.choose_button, self.source, self.receive_button):
            widget.setEnabled(not busy)
        self.cancel_button.setEnabled(busy or bool(self.current_id))
        if self.closing and not any(
            c.busy for c in (self.poller, self.data_job, self.cancel_job, self.archive_job)
        ):
            self.client.control.token = ""
            super().done(0)
            self.deleteLater()

    def poll(self):
        if not self.closing and not self.poller.busy:
            self.poller.submit(lambda cancel, progress: self.client.poll())

    def polled(self, result):
        selected = self.peers.currentData()
        self.peers.clear()
        for peer in result["peers"]:
            self.peers.addItem(f"{peer['name']} ({peer['platform']})", peer["id"])
        index = self.peers.findData(selected)
        if index >= 0:
            self.peers.setCurrentIndex(index)
        current = self.table.currentRow()
        self.rows = result["tasks"]
        self.table.set_rows(
            [
                (
                    r["filename"],
                    r["sender_name"],
                    r["receiver_name"],
                    r["state"],
                    f"{r['bytes_forwarded']:,} / {r['size_bytes']:,}",
                    r["confirmation"] or r["error"],
                )
                for r in self.rows
            ]
        )
        if 0 <= current < len(self.rows):
            self.table.selectRow(current)
        self.auto_archive()

    def choose(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择发送文件")
        if path:
            self.source.setText(path)

    def send(self):
        receiver = self.peers.currentData()
        path = Path(self.source.text())
        if not receiver or not path.is_file():
            self.feedback.setText("请选择真实文件和正在监听的接收设备。")
            return
        self.started = time.monotonic()
        self.archive_source = path
        self.feedback.setText("正在哈希并发起请求，等待接收端同意…")
        self.data_job.submit(
            lambda cancel, progress: self.client.send(
                path, receiver, cancel, progress, self.transfer_created.emit
            )
        )
        self.controls()

    def selected_transfer(self):
        index = self.table.currentRow()
        if index < 0:
            return None
        return self.rows[index]

    def receive(self):
        task = self.selected_transfer()
        if not task or task["receiver_id"] != self.device_id or task["state"] != "WAITING_RECEIVER":
            self.feedback.setText("请选择发给本设备且等待接收的任务。")
            return
        destination, _ = QFileDialog.getSaveFileName(
            self, "选择保存位置（现有文件不会覆盖）", task["filename"]
        )
        if not destination:
            return
        self.started = time.monotonic()
        self.archive_source = Path(destination)
        self.current_id = task["id"]
        self.feedback.setText("正在接收并分块校验…")
        self.data_job.submit(
            lambda cancel, progress: self.client.receive(
                task["id"], Path(destination), cancel, progress
            )
        )
        self.controls()

    def reject(self):
        task = self.selected_transfer()
        if task and not self.cancel_job.busy:
            self.cancel_job.submit(
                lambda cancel, progress: self.client.request("POST", f"/{task['id']}/reject")
            )

    def cancel(self):
        self.data_job.cancel()
        if self.current_id and not self.cancel_job.busy:
            tid = self.current_id
            self.cancel_job.submit(lambda cancel, progress: self.client.cancel(tid))

    def created(self, transfer_id):
        self.current_id = transfer_id

    def progress(self, done, total):
        speed = done / max(0.001, time.monotonic() - self.started)
        self.bar.setValue(round(done * 100 / total) if total else 100)
        self.feedback.setText(
            f"{done:,} / {total:,} 字节 · 平均 {speed / 1024:.1f} KiB/s；尚需接收端确认。"
        )

    def completed(self, task):
        self.feedback.setText(
            f"{task['state']} · {task['confirmation']}；独立后台归档不影响即时交付。"
        )
        if task["state"] == "COMPLETED":
            self.archive_sources[task["id"]] = (dict(task), self.archive_source, 0)
        self.current_id = None
        self.poll()

    def auto_archive(self):
        if self.closing or self.archive_job.busy:
            return
        for tid, (task, path, due) in list(self.archive_sources.items()):
            if time.monotonic() < due:
                continue
            self.archive_sources[tid] = (task, path, time.monotonic() + 10)
            client = ArchiveClient(self.client.control.server, self.client.control.token)
            self.archive_job.submit(
                lambda cancel, progress, t=task, p=path, i=tid, c=client: {
                    "transfer_id": i,
                    "result": c.auto_from_transfer(t, p, cancel, progress),
                }
            )
            break

    def archived(self, data):
        result = data["result"]
        if result["state"] not in {
            "AUTO_ARCHIVE_OFF",
            "WAITING_SOURCE",
            "WAITING_UPLOAD",
            "UPLOADING",
        }:
            self.archive_sources.pop(data["transfer_id"], None)
            self.feedback.setText("即时交付已完成；独立归档副本状态：" + result["state"])

    def error(self, text):
        self.feedback.setText("失败 / 中断：" + text)

    def done(self, result):
        self.closing = True
        self.timer.stop()
        self.cancel()
        self.archive_job.cancel()
        self.controls()

    def closeEvent(self, event):
        # Keep the dialog and its signals alive until HTTP workers have drained.
        event.ignore()
        self.done(0)
