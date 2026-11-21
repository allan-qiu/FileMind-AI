from pathlib import Path

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QCheckBox, QFileDialog, QHBoxLayout, QLineEdit, QMessageBox

from filemind.ui.tasks import TaskController
from filemind.ui.widgets import Card, DataTable, Page, button, label


class MonitoringPage(Page):
    import_requested = Signal(object)

    def __init__(self, service):
        super().__init__(
            "文件监控",
            "watchdog 真实事件；先等待文件稳定。关闭客户端即停止监控，没有独立后台服务。",
        )
        self.service, self.rows, self.event_rows = service, [], []
        self.controller = TaskController(self)
        self.controller.result.connect(lambda value: self.refresh())
        self.controller.error.connect(lambda value: self.feedback.setText("失败：" + value))
        self.controller.finished.connect(self.controls)
        card = Card("持久化监控策略")
        self.source, self.destination = QLineEdit(), QLineEdit()
        for title, field in (("监控源目录", self.source), ("监控输出目录", self.destination)):
            row = QHBoxLayout()
            field.setPlaceholderText(title)
            field.setAccessibleName(title)
            choose = button("选择目录")
            choose.clicked.connect(lambda checked=False, field=field: self.choose(field))
            row.addWidget(field, 1)
            row.addWidget(choose)
            card.body.addLayout(row)
        self.rules = QLineEdit()
        self.rules.setPlaceholderText("可选规则：*.pdf=资料;*.jpg=图片")
        self.rules.setAccessibleName("监控分类规则")
        card.body.addWidget(self.rules)
        self.auto = QCheckBox("明确启用自动移动（默认仅生成待确认事件）")
        card.body.addWidget(self.auto)
        row = QHBoxLayout()
        self.add_button, self.pause_button, self.resume_button, self.remove_button = (
            button("添加并启动"),
            button("暂停"),
            button("恢复"),
            button("删除策略"),
        )
        self.add_button.clicked.connect(self.add)
        self.pause_button.clicked.connect(lambda: self.action("pause"))
        self.resume_button.clicked.connect(lambda: self.action("start"))
        self.remove_button.clicked.connect(lambda: self.action("remove"))
        for widget in (self.add_button, self.pause_button, self.resume_button, self.remove_button):
            row.addWidget(widget)
        card.body.addLayout(row)
        self.table = DataTable(("源目录", "输出目录", "策略", "运行状态"))
        card.body.addWidget(self.table)
        self.body.addWidget(card)
        events = Card("真实触发记录")
        self.events = DataTable(("文件", "状态", "说明"))
        events.body.addWidget(self.events)
        self.import_button = button("将选中事件载入智能整理")
        self.import_button.clicked.connect(self.import_event)
        events.body.addWidget(self.import_button)
        self.feedback = label(
            "删除策略只移除配置，不删除监控文件。输出目录事件被排除以避免循环。", "Muted"
        )
        events.body.addWidget(self.feedback)
        self.body.addWidget(events)
        self.timer = QTimer(self)
        self.timer.setInterval(1500)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh()
        self.finish()

    def choose(self, field):
        path = QFileDialog.getExistingDirectory(self, "选择目录")
        if path:
            field.setText(path)

    def controls(self):
        for widget in (self.add_button, self.pause_button, self.resume_button, self.remove_button):
            widget.setEnabled(not self.controller.busy)

    def add(self):
        try:
            rules = [
                tuple(value.strip().split("=", 1))
                for value in self.rules.text().split(";")
                if value.strip()
            ]
            if any(len(rule) != 2 for rule in rules):
                raise ValueError("规则格式应为：模式=分类。")
            if not self.source.text() or not self.destination.text():
                raise ValueError("请选择监控源目录与输出目录。")
            if (
                self.auto.isChecked()
                and QMessageBox.question(
                    self,
                    "启用自动文件移动",
                    "此策略运行期间会自动移动稳定文件，不再逐批弹窗。\n输出目录将被排除，同名不覆盖，操作写入历史。\n明确启用？",
                )
                != QMessageBox.StandardButton.Yes
            ):
                return
            rule_id = self.service.monitor.add(
                Path(self.source.text()),
                Path(self.destination.text()),
                self.auto.isChecked(),
                rules,
            )
            self.controller.submit(lambda cancel, progress: self.service.monitor.start(rule_id))
            self.controls()
        except ValueError as exc:
            self.feedback.setText(str(exc))

    def action(self, operation):
        index = self.table.currentRow()
        if index >= 0:
            rule_id = self.rows[index]["id"]
            self.controller.submit(
                lambda cancel, progress: getattr(self.service.monitor, operation)(rule_id)
            )
            self.controls()

    def refresh(self):
        current = self.table.currentRow()
        self.rows = self.service.monitor.rules()
        self.table.set_rows(
            [
                (
                    row["source"],
                    row["destination"],
                    "自动移动" if row["auto_execute"] else "等待确认",
                    "运行中" if row["running"] else "已暂停 / 客户端未运行",
                )
                for row in self.rows
            ]
        )
        if 0 <= current < len(self.rows):
            self.table.selectRow(current)
        self.event_rows = self.service.monitor.events()
        selected = self.events.currentRow()
        self.events.set_rows(
            [(row["path"], row["state"], row["message"]) for row in self.event_rows]
        )
        if 0 <= selected < len(self.event_rows):
            self.events.selectRow(selected)
        self.controls()

    def import_event(self):
        row = self.events.currentRow()
        if row >= 0:
            self.import_requested.emit([self.event_rows[row]["path"]])
