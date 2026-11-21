from pathlib import Path

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QMessageBox

from filemind.domain.local import PlanEntry
from filemind.ui.tasks import TaskController
from filemind.ui.widgets import Card, DataTable, Page, button, label


class HistoryPage(Page):
    def __init__(self, service):
        super().__init__(
            "操作历史", "真实批次日志与条件撤销；修改过的文件和已占用原路径不会被覆盖。"
        )
        self.service = service
        self.controller = TaskController(self)
        self.controller.result.connect(self.completed)
        self.controller.error.connect(lambda value: self.feedback.setText("失败：" + value))
        self.controller.finished.connect(self.controls)
        card = Card("真实整理批次")
        self.count_label = label("", "Badge")
        card.body.addWidget(self.count_label)
        row = QHBoxLayout()
        self.task_combo = QComboBox()
        self.task_combo.setAccessibleName("历史整理批次")
        self.task_combo.currentIndexChanged.connect(self.details)
        self.refresh_button, self.reconcile_button = button("刷新"), button("异常对账")
        self.undo_button = button("撤销选中批次")
        self.refresh_button.clicked.connect(self.refresh)
        self.reconcile_button.clicked.connect(self.reconcile)
        self.undo_button.clicked.connect(self.undo)
        row.addWidget(self.task_combo, 1)
        for control in (self.refresh_button, self.reconcile_button, self.undo_button):
            row.addWidget(control)
        card.body.addLayout(row)
        self.table = DataTable(("原路径", "目标路径", "动作", "状态", "执行 / 撤销错误"))
        self.table.setMinimumHeight(340)
        card.body.addWidget(self.table)
        self.feedback = label("无记录时显示空列表；对账只核对文件，不自动删除任何副本。", "Muted")
        card.body.addWidget(self.feedback)
        self.body.addWidget(card)
        self.finish()
        self.refresh()

    def controls(self):
        for widget in (self.refresh_button, self.reconcile_button, self.task_combo):
            widget.setEnabled(not self.controller.busy)
        self.undo_button.setEnabled(
            not self.controller.busy and bool(self.task_combo.currentData())
        )

    def refresh(self):
        previous = self.task_combo.currentData()
        self.task_combo.blockSignals(True)
        self.task_combo.clear()
        for task in self.service.local.repository.tasks():
            undone = task.get("undone_count", 0)
            status = (
                "已撤销"
                if undone and undone == task["count"]
                else "部分已撤销"
                if undone
                else task["status"]
            )
            self.task_combo.addItem(f"{task['id'][:8]} · {status} · {task['count']} 项", task["id"])
        if previous:
            index = self.task_combo.findData(previous)
            if index >= 0:
                self.task_combo.setCurrentIndex(index)
        self.task_combo.blockSignals(False)
        self.count_label.setText(f"真实文件操作记录：{self.service.history_count()}")
        self.details()
        self.controls()

    def details(self):
        task = self.task_combo.currentData()
        rows = []
        if task:
            for record in self.service.local.repository.operations(task):
                entry = PlanEntry.model_validate_json(record["payload"])
                rows.append(
                    (
                        entry.file.path,
                        str(Path(entry.target_dir) / entry.target_name),
                        entry.action,
                        record["state"],
                        record["error"],
                    )
                )
        self.table.set_rows(rows)

    def undo(self):
        task = self.task_combo.currentData()
        if (
            task
            and QMessageBox.question(
                self,
                "确认撤销",
                "将检查原路径、修改时间和 SHA-256。\n仅恢复符合条件的文件，不覆盖现有内容。继续？",
            )
            == QMessageBox.StandardButton.Yes
        ):
            self.feedback.setText("正在检查并撤销…")
            self.controller.submit(
                lambda cancel, progress: self.service.local.undo(task, True, cancel)
            )
            self.controls()

    def reconcile(self):
        self.feedback.setText("正在核对异常中断日志…")
        self.controller.submit(lambda cancel, progress: self.service.local.reconcile())
        self.controls()

    def completed(self, report):
        self.feedback.setText(
            "结果："
            + "; ".join(f"{row['id'][:8]} {row['state']} {row.get('error', '')}" for row in report)
        )
        self.refresh()
