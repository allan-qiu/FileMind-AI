from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QProgressBar,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from filemind.services.archive import ArchiveClient
from filemind.ui.tasks import TaskController
from filemind.ui.widgets import DataTable, button, label


class ArchiveDialog(QDialog):
    def __init__(self, server, account_token, device_token="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("FileMind · 独立归档与 AI 授权")
        self.resize(1120, 850)
        self.account = ArchiveClient(server, account_token)
        self.device = ArchiveClient(server, device_token) if device_token else None
        self.rows, self.selected, self.current_plan = [], None, None
        self.closing, self.preferences_loaded = False, False
        self.poller, self.worker = TaskController(self), TaskController(self)
        self.poller.result.connect(self.loaded)
        self.poller.error.connect(self.error)
        self.worker.result.connect(self.completed)
        self.worker.error.connect(self.error)
        self.worker.progress.connect(self.progress)
        for controller in (self.poller, self.worker):
            controller.finished.connect(self.controls)
        outer = QVBoxLayout(self)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        content = QWidget()
        body = QVBoxLayout(content)
        scroll.setWidget(content)
        outer.addWidget(scroll)
        body.setContentsMargins(24, 24, 24, 24)
        body.addWidget(label("归档任务与整理结果", "PageTitle"))
        body.addWidget(
            label("本地存储适配器运行中，百度网盘未正式连接。即时交付与归档状态独立。", "Muted")
        )
        settings = QHBoxLayout()
        self.auto = QCheckBox("交付后自动提供归档副本")
        self.mode = QComboBox()
        for title, mode in (
            ("尚未授权", "off"),
            ("仅文件名与元数据", "metadata"),
            ("文本摘录最多4000字符", "summary"),
            ("正文最多16000字符", "content"),
        ):
            self.mode.addItem(title, mode)
        self.mode.setAccessibleName("云端AI处理授权模式")
        self.save_settings = button("保存授权设置")
        self.save_settings.clicked.connect(self.save_preferences)
        settings.addWidget(self.auto)
        settings.addWidget(self.mode, 1)
        settings.addWidget(self.save_settings)
        body.addLayout(settings)
        self.rules = QLineEdit()
        self.rules.setMaxLength(2000)
        self.rules.setPlaceholderText("个人分类规则（仅在明确授权的AI请求中使用）")
        self.rules.setAccessibleName("个人分类规则")
        body.addWidget(self.rules)
        self.feedback = label("正在读取归档任务…", "Muted")
        body.addWidget(self.feedback)
        self.table = DataTable(("原文件", "大小", "真实阶段", "上传字节", "模型", "重试 / 原因"))
        self.table.setMinimumHeight(180)
        self.table.itemSelectionChanged.connect(self.select_job)
        body.addWidget(self.table, 1)
        actions = QGridLayout()
        self.upload_button, self.resume_button = (
            button("新建归档并上传"),
            button("为选中任务提供副本 / 恢复"),
        )
        self.retry_button, self.refresh_button = button("重试选中失败任务"), button("刷新")
        self.pause_button = button("暂停本次上传")
        self.upload_button.clicked.connect(self.new_upload)
        self.resume_button.clicked.connect(self.resume_upload)
        self.retry_button.clicked.connect(self.retry)
        self.refresh_button.clicked.connect(self.poll)
        self.pause_button.clicked.connect(self.worker.cancel)
        for index, widget in enumerate(
            (
                self.upload_button,
                self.resume_button,
                self.retry_button,
                self.refresh_button,
                self.pause_button,
            )
        ):
            actions.addWidget(widget, index // 3, index % 3)
        body.addLayout(actions)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        body.addWidget(self.bar)
        form = QFormLayout()
        self.plan_fields = {}
        for key, title in (
            ("category", "AI二次分类"),
            ("subcategory", "子分类"),
            ("suggested_filename", "建议文件名"),
            ("target_directory", "正式目录"),
            ("summary", "摘要"),
            ("reason", "确认原因"),
        ):
            field = QLineEdit()
            field.setAccessibleName(title)
            field.setMaxLength(1000 if key in {"summary", "reason"} else 240)
            self.plan_fields[key] = field
            form.addRow(title, field)
        body.addLayout(form)
        self.confirm_button = button("明确确认选中整理计划", primary=True)
        self.confirm_button.clicked.connect(self.confirm_plan)
        body.addWidget(self.confirm_button)
        self.library = DataTable(("已归档名称", "分类", "版本", "真实存储对象"))
        self.library.setMinimumHeight(130)
        body.addWidget(self.library)
        body.addWidget(
            label(
                "AI阶段显示真实状态，无固定时间进度。关闭窗口暂停上传，恢复需重新选择相同文件。Mock模型不代表真实分析。",
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
        busy = self.worker.busy or self.closing
        for widget in (
            self.upload_button,
            self.resume_button,
            self.retry_button,
            self.save_settings,
        ):
            widget.setEnabled(not busy)
        self.confirm_button.setEnabled(
            not busy
            and bool(self.current_plan)
            and bool(self.selected and self.selected["state"] == "PLAN_REVIEW_REQUIRED")
        )
        self.pause_button.setEnabled(self.worker.busy and not self.closing)
        if self.closing and not self.worker.busy and not self.poller.busy:
            self.account.control.token = ""
            if self.device:
                self.device.control.token = ""
            super().done(0)
            self.deleteLater()

    def poll(self):
        if not self.closing and not self.poller.busy:
            self.poller.submit(
                lambda cancel, progress: {
                    "jobs": self.account.jobs(),
                    "preferences": self.account.preferences(),
                    "files": self.account.control.request("GET", "/library"),
                }
            )

    def loaded(self, data):
        if self.closing:
            return
        selected_id = self.selected["id"] if self.selected else None
        self.rows = data["jobs"]
        self.table.blockSignals(True)
        self.table.set_rows(
            [
                (
                    j["filename"],
                    f"{j['size_bytes']:,}",
                    j["state"],
                    f"{j['uploaded_bytes']:,}",
                    j["model"] or "尚未调用",
                    f"{j['retry_count']} / {j['error']}",
                )
                for j in self.rows
            ]
        )
        for i, row in enumerate(self.rows):
            if row["id"] == selected_id:
                self.table.selectRow(i)
                self.selected = row
        self.table.blockSignals(False)
        self.library.set_rows(
            [
                (f["filename"], f["category"], str(f["version"]), f["object_key"])
                for f in data["files"]
            ]
        )
        if not self.preferences_loaded:
            prefs = data["preferences"]
            self.auto.setChecked(prefs["auto_archive"])
            self.mode.setCurrentIndex(self.mode.findData(prefs["ai_mode"]))
            self.rules.setText(prefs["rules"])
            self.preferences_loaded = True
        self.controls()

    def select_job(self):
        index = self.table.currentRow()
        self.selected = self.rows[index] if 0 <= index < len(self.rows) else None
        self.current_plan = self.selected["plan"] if self.selected else None
        if (
            self.selected
            and self.selected["state"] == "PLAN_REVIEW_REQUIRED"
            and not self.current_plan
        ):
            self.current_plan = {
                "category": "其他",
                "subcategory": "",
                "suggested_filename": self.selected["filename"],
                "target_directory": "其他",
                "summary": "人工确认",
                "keywords": [],
                "related_file_ids": [],
                "needs_user_review": True,
                "reason": "AI未提供可执行计划，使用明确人工方案。",
            }
        for key, field in self.plan_fields.items():
            field.setText(str(self.current_plan.get(key, "")) if self.current_plan else "")
            field.setReadOnly(not self.selected or self.selected["state"] != "PLAN_REVIEW_REQUIRED")
        if self.selected:
            self.feedback.setText(
                f"原文件：{self.selected['filename']} · 原分类：未分类 · "
                f"{self.selected['state']} · {self.selected['error']}"
            )
        self.controls()

    def submit(self, operation):
        if self.worker.submit(operation):
            self.controls()

    def save_preferences(self):
        data = {
            "auto_archive": self.auto.isChecked(),
            "ai_mode": self.mode.currentData(),
            "rules": self.rules.text(),
        }
        self.submit(
            lambda cancel, progress: self.account.control.request(
                "PUT", "/archive/preferences", data
            )
        )

    def new_upload(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择归档文件")
        if path:
            self.submit(
                lambda cancel, progress: self.account.create_upload(Path(path), cancel, progress)
            )

    def resume_upload(self):
        if not self.selected:
            self.error("先选择归档任务")
            return
        row = dict(self.selected)
        client = self.device if row["source_transfer_id"] else self.account
        if not client:
            self.error("DeviceLink副本需要原设备凭据，请在原设备窗口操作")
            return
        path, _ = QFileDialog.getOpenFileName(self, "重新选择同大小及SHA-256的归档副本")
        if path:
            self.submit(lambda cancel, progress: client.upload(row, Path(path), cancel, progress))

    def retry(self):
        if self.selected:
            job_id = self.selected["id"]
            self.submit(
                lambda cancel, progress: self.account.control.request(
                    "POST", f"/archive/jobs/{job_id}/retry"
                )
            )

    def confirm_plan(self):
        if self.selected and self.current_plan:
            job_id = self.selected["id"]
            plan = {
                **self.current_plan,
                **{key: field.text() for key, field in self.plan_fields.items()},
                "needs_user_review": False,
            }
            self.submit(
                lambda cancel, progress: self.account.control.request(
                    "PUT", f"/archive/jobs/{job_id}/plan", plan
                )
            )

    def completed(self, data):
        if not self.closing:
            self.feedback.setText("操作已提交：" + data.get("state", "授权设置已保存"))
            self.poll()

    def progress(self, done, total):
        if not self.closing:
            self.bar.setValue(round(done * 100 / total) if total else 100)
            self.feedback.setText(
                f"已确认上传 {done:,} / {total:,} 字节；后台AI和存储阶段独立显示。"
            )

    def error(self, text):
        if not self.closing:
            self.feedback.setText("归档失败 / 暂停：" + text)

    def done(self, result):
        self.closing = True
        self.timer.stop()
        self.worker.cancel()
        self.controls()

    def closeEvent(self, event):
        event.ignore()
        self.done(0)
