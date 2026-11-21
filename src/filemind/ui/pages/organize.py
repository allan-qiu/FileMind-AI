from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from filemind.domain.local import LocalPlan
from filemind.services.application import ApplicationService
from filemind.services.classification import valid_name
from filemind.ui.tasks import TaskController
from filemind.ui.widgets import Card, DataTable, Page, button, label


class OrganizePage(Page):
    operation_done = Signal()
    plan_ready = Signal(object)

    def __init__(self, service: ApplicationService):
        super().__init__("整理文件", "① 选择文件　② 选择保存位置并预览　③ 确认整理")
        self.service, self.files, self.roots, self.plan = service, [], [], None
        self.body.setContentsMargins(20, 16, 20, 16)
        self.body.setSpacing(12)
        self.loaded, self.demo_mode = False, False
        self.controller = TaskController(self)
        self.controller.result.connect(self.completed)
        self.controller.error.connect(lambda message: self.result_label.setText("失败：" + message))
        self.controller.finished.connect(self.controls)
        self.controller.progress.connect(
            lambda done, total: self.result_label.setText(
                f"正在处理：{done} / {total}（扫描第二项为累计字节）"
            )
        )
        card = Card("1. 选择要整理的文件")
        card.body.setContentsMargins(16, 12, 16, 12)
        card.body.setSpacing(8)
        row = QHBoxLayout()
        self.import_button, self.folder_button = (
            button("选择文件", primary=True),
            button("选择文件夹"),
        )
        self.clear_button, self.load_button = button("清空待处理列表"), button("查看独立样例")
        self.import_button.clicked.connect(self.pick_files)
        self.folder_button.clicked.connect(self.pick_folder)
        self.clear_button.clicked.connect(self.clear)
        self.load_button.clicked.connect(self.load_demo)
        for control in (
            self.import_button,
            self.folder_button,
            self.clear_button,
        ):
            row.addWidget(control)
        card.body.addLayout(row)
        self.options_button = button("更多选项 ▸")
        self.options_button.setCheckable(True)
        self.options = QWidget()
        options_body = QVBoxLayout(self.options)
        options_body.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.exclude_dirs, self.exclude_types = QLineEdit(), QLineEdit()
        self.exclude_dirs.setPlaceholderText("排除目录：node_modules;*缓存*")
        self.exclude_types.setPlaceholderText("排除类型：.tmp;.exe")
        self.exclude_dirs.setAccessibleName("排除目录")
        self.exclude_types.setAccessibleName("排除文件类型")
        row.addWidget(self.exclude_dirs)
        row.addWidget(self.exclude_types)
        options_body.addLayout(row)
        row = QHBoxLayout()
        self.destination = QLineEdit()
        self.destination.setPlaceholderText("请选择或输入整理输出根目录")
        self.destination.setAccessibleName("整理输出目录")
        self.destination_button = button("选择保存位置")
        self.destination_button.clicked.connect(self.pick_destination)
        row.addWidget(self.destination, 1)
        row.addWidget(self.destination_button)
        card.body.addLayout(row)
        row = QHBoxLayout()
        self.mode = QComboBox()
        self.mode.addItems(["离线规则", "真实 AI API"])
        self.mode.setAccessibleName("分类模式")
        self.custom_rules = QLineEdit()
        self.custom_rules.setPlaceholderText("自定义：*.pdf=资料;*发票*=财务")
        self.custom_rules.setAccessibleName("自定义分类规则")
        self.numbered = QCheckBox("冲突编号")
        self.generate_button = button("预览整理结果", primary=True)
        self.generate_button.clicked.connect(self.generate)
        for control in (self.mode, self.custom_rules, self.numbered):
            row.addWidget(control)
        options_body.addLayout(row)
        options_body.addWidget(self.load_button)
        self.mode_hint = label("分类方式：离线规则（不调用付费 AI）", "Muted")
        self.mode.currentTextChanged.connect(
            lambda text: self.mode_hint.setText(f"分类方式：{text}")
        )
        card.body.addWidget(self.mode_hint)
        actions = QHBoxLayout()
        actions.addWidget(self.generate_button)
        actions.addWidget(self.options_button)
        actions.addStretch()
        card.body.addLayout(actions)
        card.body.addWidget(self.options)
        self.options.hide()
        self.options_button.toggled.connect(self.toggle_options)
        self.body.addWidget(card)
        preview = Card("2. 查看结果，再确认整理")
        preview.body.setContentsMargins(16, 12, 16, 12)
        preview.body.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索名称、分类或目录（筛选不改变勾选状态）")
        self.search.setAccessibleName("搜索文件")
        self.search.textChanged.connect(self.filter_rows)
        preview.body.addWidget(self.search)
        self.table = DataTable(
            ("选择", "原文件", "分类", "目标目录", "目标文件名", "来源", "冲突 / 状态")
        )
        self.table.setMinimumHeight(240)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        preview.body.addWidget(self.table)
        actions = QHBoxLayout()
        self.all_button, self.none_button = button("全选"), button("全不选")
        self.all_button.clicked.connect(lambda: self.select_all(True))
        self.none_button.clicked.connect(lambda: self.select_all(False))
        self.execute_button = button("确认整理", primary=True)
        self.execute_button.clicked.connect(self.execute)
        self.cancel_button = button("取消当前任务")
        self.cancel_button.clicked.connect(self.controller.cancel)
        for control in (self.all_button, self.none_button, self.execute_button, self.cancel_button):
            actions.addWidget(control)
        preview.body.insertLayout(1, actions)
        self.result_label = label("先选择文件。预览不会移动文件。", "Muted")
        preview.body.addWidget(self.result_label)
        self.body.addWidget(preview)
        self.finish()
        self.controls()

    def toggle_options(self, expanded):
        self.options.setVisible(expanded)
        self.options_button.setText("收起更多选项 ▾" if expanded else "更多选项 ▸")

    def controls(self):
        busy = self.controller.busy
        for control in (
            self.import_button,
            self.folder_button,
            self.clear_button,
            self.load_button,
            self.destination_button,
            self.destination,
            self.mode,
            self.custom_rules,
            self.exclude_dirs,
            self.exclude_types,
            self.numbered,
        ):
            control.setEnabled(not busy)
        self.table.setEnabled(not busy)
        self.generate_button.setEnabled(bool(self.files) and not busy)
        self.execute_button.setEnabled(self.plan is not None and not busy)
        self.cancel_button.setEnabled(busy)

    def launch(self, kind, operation):
        self.job_kind = kind
        self.result_label.setText(
            {
                "scan": "正在读取真实文件…",
                "plan": "正在生成分类，尚未移动文件…",
                "execute": "正在整理…",
            }[kind]
        )
        self.controller.submit(operation)
        self.controls()

    def pick_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "选择一个或多个待整理文件")
        if paths:
            self.scan_paths(paths)

    def pick_folder(self):
        path = QFileDialog.getExistingDirectory(self, "添加待整理目录（可重复添加）")
        if path:
            self.scan_paths([path])

    def pick_destination(self):
        path = QFileDialog.getExistingDirectory(self, "选择输出根目录")
        if path:
            self.destination.setText(path)

    def scan_paths(self, paths):
        self.roots = list(dict.fromkeys([*self.roots, *map(str, paths)]))
        excludes = [v.strip() for v in self.exclude_dirs.text().split(";") if v.strip()]
        types = [v.strip() for v in self.exclude_types.text().split(";") if v.strip()]
        roots = self.roots.copy()
        self.launch(
            "scan",
            lambda cancel, progress: self.service.scanner.scan_many(
                roots, cancel, exclude_dirs=excludes, exclude_types=types, progress=progress
            ),
        )

    def generate(self):
        if not self.destination.text().strip():
            self.result_label.setText("请选择整理输出根目录。")
            return
        provider = self.service.ai if self.mode.currentIndex() == 1 else None
        if self.mode.currentIndex() == 1 and provider is None:
            self.result_label.setText("AI 未配置/授权；请在设置中配置，或明确切换离线规则模式。")
            return
        try:
            rules = [
                tuple(v.strip() for v in value.split("=", 1))
                for value in self.custom_rules.text().split(";")
                if value.strip()
            ]
            if any(len(rule) != 2 for rule in rules):
                raise ValueError("规则格式应为：模式=分类。")
            for _, category in rules:
                valid_name(category)
        except ValueError as exc:
            self.result_label.setText(str(exc))
            return
        destination, files, numbered = (
            Path(self.destination.text()),
            self.files.copy(),
            self.numbered.isChecked(),
        )
        self.launch(
            "plan",
            lambda cancel, progress: self.service.classification.plan(
                files, destination, provider, rules, cancel, numbered
            ),
        )

    def edited_plan(self):
        entries = []
        for row, entry in enumerate(self.plan.entries):
            directory, name, category = (
                self.table.item(row, col).text().strip() for col in (3, 4, 2)
            )
            if category != entry.category and directory == entry.target_dir:
                directory = str(Path(directory).parent / valid_name(category))
            changed = (directory, name, category) != (
                entry.target_dir,
                entry.target_name,
                entry.category,
            )
            entries.append(
                entry.model_copy(
                    update={
                        "target_dir": directory,
                        "target_name": name,
                        "category": category,
                        "selected": self.table.item(row, 0).checkState() == Qt.CheckState.Checked,
                        "origin": "manual" if changed else entry.origin,
                    }
                )
            )
        return LocalPlan(id=self.plan.id, entries=entries, mode=self.plan.mode)

    def execute(self):
        if self.plan is None:
            return
        try:
            plan = self.edited_plan()
            count = sum(entry.selected for entry in plan.entries)
            if not count:
                self.result_label.setText("请勾选至少一条计划。")
                return
            for entry in plan.entries:
                if entry.selected:
                    valid_name(entry.target_name)
        except ValueError as exc:
            self.result_label.setText(str(exc))
            return
        if (
            QMessageBox.question(
                self,
                "确认真实整理",
                f"将按预览移动或重命名 {count} 个真实文件。\n"
                "不覆盖同名文件；记录历史，符合条件可撤销。\n确认执行？",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self.launch(
            "execute",
            lambda cancel, progress: self.service.local.execute(plan, True, cancel, progress),
        )

    def completed(self, result):
        self.loaded, self.demo_mode = True, False
        if self.job_kind == "scan":
            self.files, self.plan = result.files, None
            self.table.set_rows(
                [["", f.path, f.kind, "", f.name, "真实元数据", "待分类"] for f in self.files]
            )
            self.result_label.setText(
                f"{'导入已取消，保留已读部分' if result.cancelled else '已读取'}："
                f"{len(self.files)} 个文件，{result.total_bytes:,} 字节。\n"
                + "\n".join(result.issues[:10])
            )
        elif self.job_kind == "plan":
            self.plan = result
            self.plan_ready.emit(result)
            self.table.set_rows(
                [
                    [
                        "",
                        e.file.path,
                        e.category,
                        e.target_dir,
                        e.target_name,
                        e.origin,
                        "冲突：不会覆盖" if e.conflict else "等待确认",
                    ]
                    for e in result.entries
                ]
            )
            self.result_label.setText(
                f"{len(result.entries)} 条计划等待确认；双击可改分类、目录与文件名。"
            )
        else:
            for row, entry in enumerate(self.plan.entries):
                state = next(
                    (v["state"] for v in result["results"] if v["id"] == entry.id), "未执行"
                )
                self.table.item(row, 6).setText(state)
            self.result_label.setText(
                f"整理报告：{result['status']}；批次 {result['task_id']}，详情与错误见操作历史。"
            )
            self.plan = None
            self.operation_done.emit()
        for row in range(self.table.rowCount()):
            for col in range(self.table.columnCount()):
                item = self.table.item(row, col)
                if item:
                    item.setFlags(
                        Qt.ItemFlag.ItemIsEnabled
                        | Qt.ItemFlag.ItemIsSelectable
                        | (
                            Qt.ItemFlag.ItemIsEditable
                            if self.plan and col in (2, 3, 4)
                            else Qt.ItemFlag.NoItemFlags
                        )
                    )
            if self.plan:
                self.table.item(row, 0).setFlags(
                    Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
                )
                self.table.item(row, 0).setCheckState(Qt.CheckState.Checked)
        self.filter_rows()

    def select_all(self, checked):
        if self.plan:
            for row in range(self.table.rowCount()):
                self.table.item(row, 0).setCheckState(
                    Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
                )

    def filter_rows(self):
        query = self.search.text().casefold()
        if self.demo_mode:
            self.table.set_rows(
                [
                    [
                        "样例",
                        p.source.name,
                        p.category,
                        p.target_folder,
                        p.suggested_name,
                        "固定演示",
                        "不可执行",
                    ]
                    for p in self.service.demo.preview(query)
                ]
            )
            return
        for row in range(self.table.rowCount()):
            text = " ".join(
                self.table.item(row, col).text()
                for col in range(self.table.columnCount())
                if self.table.item(row, col)
            )
            self.table.setRowHidden(row, query not in text.casefold())

    def refresh(self):
        self.filter_rows()

    def clear(self):
        self.files, self.roots, self.plan = [], [], None
        self.table.setRowCount(0)
        self.result_label.setText("已清空待处理列表，未更改文件。")
        self.controls()

    def load_demo(self):
        self.files, self.roots, self.plan = [], [], None
        self.loaded, self.demo_mode = True, True
        self.table.set_rows(
            [
                [
                    "样例",
                    p.source.name,
                    p.category,
                    p.target_folder,
                    p.suggested_name,
                    "固定演示",
                    "不可执行",
                ]
                for p in self.service.demo.preview()
            ]
        )
        self.result_label.setText("6 份内置样例；不会读取、执行或记入真实历史。")
        self.filter_rows()
        self.controls()
