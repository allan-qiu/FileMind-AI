from PySide6.QtCore import Signal

from filemind.ui.widgets import Card, DataTable, Page, button, label


class PlansPage(Page):
    open_requested = Signal()

    def __init__(self):
        super().__init__("整理计划", "当前会话的真实计划草稿；编辑与执行统一在智能整理页完成。")
        card = Card("待确认的真实计划")
        self.info = label("暂无草稿。请导入真实文件并生成计划。", "Muted")
        card.body.addWidget(self.info)
        self.table = DataTable(("原文件", "分类", "建议名称", "目标目录", "来源"))
        card.body.addWidget(self.table)
        self.create_button = button("前往智能整理：创建或编辑计划", primary=True)
        self.create_button.clicked.connect(self.open_requested.emit)
        card.body.addWidget(self.create_button)
        card.body.addWidget(
            label(
                "此处显示生成时快照，不会自动执行；以智能整理页最终编辑和勾选为准。已执行记录见操作历史。",
                "Muted",
            )
        )
        self.body.addWidget(card)
        self.finish()

    def show_plan(self, plan):
        self.info.setText(f"批次 {plan.id}，{len(plan.entries)} 条；模式：{plan.mode}")
        self.table.set_rows(
            [(e.file.path, e.category, e.target_name, e.target_dir, e.origin) for e in plan.entries]
        )
