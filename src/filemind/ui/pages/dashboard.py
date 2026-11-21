from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout

from filemind.services.application import ApplicationService
from filemind.ui.widgets import Card, Page, button, label


class DashboardPage(Page):
    organize_requested = Signal()
    real_requested = Signal()

    def __init__(self, service: ApplicationService):
        super().__init__("工作台", "从这里开始，让文件管理更有条理。")
        hero = Card(hero=True)
        hero.body.addWidget(label("FILEMIND / YOUR LOCAL WORKSPACE", "Eyebrow"))
        hero.body.addWidget(label("让每一份文件，各归其位。", "HeroTitle"))
        hero.body.addWidget(label("真实枚举，离线规则或授权 AI，先编辑计划再确认执行。", "Muted"))
        action_row = QHBoxLayout()
        start = button("整理我的文件  →", primary=True)
        start.clicked.connect(self.real_requested.emit)
        action_row.addWidget(start)
        self.preview_button = button("查看独立样例")
        self.preview_button.clicked.connect(self.organize_requested.emit)
        action_row.addWidget(self.preview_button)
        action_row.addStretch()
        action_row.addWidget(label("离线可用", "Badge", wrap=False))
        hero.body.addLayout(action_row)
        self.body.addWidget(hero)

        metrics = QHBoxLayout()
        metrics.setSpacing(16)
        self.metric_labels = {}
        for name, value, note in (
            ("内置样例", str(len(service.demo.preview())), "仅用于界面演示"),
            ("真实操作记录", str(service.history_count()), "详情与条件撤销见操作历史"),
            ("运行中的监控", "0", "在文件监控页面明确启用"),
        ):
            card = Card()
            card.body.addWidget(label(name, "Muted"))
            value_label = label(value, "Metric")
            self.metric_labels[name] = value_label
            card.body.addWidget(value_label)
            card.body.addWidget(label(note, "Muted"))
            metrics.addWidget(card, 1)
        self.body.addLayout(metrics)
        self.live_label = label("", "Muted")
        self.body.addWidget(self.live_label)

        panels = QHBoxLayout()
        panels.setSpacing(16)
        workflow = Card("一个清晰、安全的整理流程")
        for number, title, description in (
            ("01", "导入真实文件", "后台读取与哈希；支持取消、排除与多个目录"),
            ("02", "编辑分类计划", "离线规则 / 明确授权的真实 AI，来源可见"),
            ("03", "确认后执行", "默认不覆盖，操作历史支持条件撤销"),
        ):
            workflow.body.addWidget(label(f"{number}   {title}", "SectionTitle"))
            workflow.body.addWidget(label(description, "Muted"))
        panels.addWidget(workflow, 3)
        scope = Card("本地工作空间")
        scope.body.addWidget(label("整理文件、服务器文件；其他操作见“更多功能”"))
        scope.body.addWidget(label("浅色 / 深色主题，随时切换"))
        scope.body.addWidget(label("安装版数据保存在当前 Windows 用户的应用数据目录"))
        scope.body.addStretch()
        scope.body.addWidget(label("监控自动移动须独立启用；云端账户与本地整理相互独立。", "Muted"))
        panels.addWidget(scope, 2)
        self.body.addLayout(panels)
        self.finish()

    def refresh(self, service):
        active = sum(row["running"] for row in service.monitor.rules())
        self.metric_labels["真实操作记录"].setText(str(service.history_count()))
        self.metric_labels["运行中的监控"].setText(str(active))
        self.live_label.setText(
            f"当前真实操作记录 {service.history_count()} 条；运行中监控 {active} 个。"
        )
