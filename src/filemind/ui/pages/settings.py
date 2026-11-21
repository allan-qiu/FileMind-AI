from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QCheckBox, QComboBox, QFormLayout, QLineEdit

from filemind.domain.models import Theme
from filemind.licensing import DEFAULT_SOURCE_URL, SPDX_LICENSE
from filemind.services.application import ApplicationService
from filemind.services.classification import AIProvider
from filemind.ui.widgets import Card, Page, button, label


class SettingsPage(Page):
    theme_requested = Signal(object)
    save_requested = Signal()

    def __init__(self, service: ApplicationService):
        super().__init__("设置中心", "让工作空间符合你的使用习惯。仅保存本应用的界面偏好。")
        appearance = Card("外观")
        appearance.body.addWidget(label("界面主题"))
        self.theme_combo = QComboBox()
        self.theme_combo.setAccessibleName("界面主题")
        self.theme_combo.addItem("浅色 · 清晰明亮", Theme.LIGHT)
        self.theme_combo.addItem("深色 · 专注舒适", Theme.DARK)
        self.theme_combo.setCurrentIndex(0 if service.config.theme == Theme.LIGHT else 1)
        self.theme_combo.currentIndexChanged.connect(
            lambda: self.theme_requested.emit(self.theme_combo.currentData())
        )
        appearance.body.addWidget(self.theme_combo)
        appearance.body.addWidget(
            label("主题即时生效。点击保存或正常关闭窗口后，下次启动会恢复偏好。", "Muted")
        )
        self.save_button = button("保存界面偏好", primary=True)
        self.save_button.clicked.connect(self.save_requested.emit)
        appearance.body.addWidget(self.save_button)
        self.feedback = label("", "Muted")
        appearance.body.addWidget(self.feedback)
        self.body.addWidget(appearance)
        licensing = Card("许可与源码")
        licensing.body.addWidget(label("FileMind AI · " + SPDX_LICENSE))
        source = button("获取对应版本源码")
        source.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(DEFAULT_SOURCE_URL)))
        licensing.body.addWidget(source)
        self.body.addWidget(licensing)
        storage = Card("本地存储")
        for name, path in (
            ("配置文件", service.paths.config_file),
            ("SQLite 数据库", service.paths.database_file),
        ):
            storage.body.addWidget(label(name, "Muted"))
            path_field = QLineEdit(str(path))
            path_field.setReadOnly(True)
            path_field.setAccessibleName(name + "位置")
            storage.body.addWidget(path_field)
        storage.body.addWidget(
            label("安装版使用当前用户的应用数据目录；源码版使用项目数据目录。", "Muted")
        )
        self.body.addWidget(storage)
        ai = Card("本地整理与云端边界")
        ai.body.addWidget(label("本地规则离线可用；监控按策略启用；文件执行须确认，默认不覆盖。"))
        ai.body.addWidget(
            label(
                "本地整理保持样例预览，不收集 AI 密钥或上传真实文件。"
                "云端账户与设备可通过独立窗口连接中央服务。",
                "Muted",
            )
        )
        self.body.addWidget(ai)
        config = Card("真实 AI 分类配置 · 仅保留在当前进程")
        form = QFormLayout()
        self.ai_endpoint, self.ai_key, self.ai_model = QLineEdit(), QLineEdit(), QLineEdit()
        self.ai_endpoint.setPlaceholderText("https://提供商/v1/chat/completions")
        self.ai_key.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("兼容 API 完整地址", self.ai_endpoint)
        form.addRow("API 密钥", self.ai_key)
        form.addRow("模型名称", self.ai_model)
        config.body.addLayout(form)
        self.ai_authorized = QCheckBox("同意发送文件摘要（可能产生费用）")
        config.body.addWidget(self.ai_authorized)
        config.body.addWidget(
            label("发送内容包括文件名、元数据和有界摘要，费用按服务商规则计算。", "Muted")
        )
        configure = button("启用本次会话 AI 配置")
        configure.clicked.connect(lambda: self.configure_ai(service))
        disable = button("停用 AI 配置")
        disable.clicked.connect(lambda: self.disable_ai(service))
        config.body.addWidget(configure)
        config.body.addWidget(disable)
        self.ai_feedback = label("未配置 AI。离线规则始终可用，不会把规则结果标为 AI。", "Muted")
        config.body.addWidget(self.ai_feedback)
        self.body.addWidget(config)
        self.finish()

    def configure_ai(self, service):
        try:
            service.ai = AIProvider(
                self.ai_endpoint.text().strip(),
                self.ai_key.text(),
                self.ai_model.text().strip(),
                self.ai_authorized.isChecked(),
            )
            self.ai_key.clear()
            self.ai_feedback.setText(
                "已配置；仅在智能整理选择 AI 并生成计划时调用。密钥不写配置或交付包。"
            )
        except ValueError as exc:
            self.ai_feedback.setText(str(exc))

    def disable_ai(self, service):
        service.ai = None
        self.ai_key.clear()
        self.ai_feedback.setText("AI 已停用。")

    def sync_theme(self, theme: Theme) -> None:
        self.theme_combo.blockSignals(True)
        self.theme_combo.setCurrentIndex(0 if theme == Theme.LIGHT else 1)
        self.theme_combo.blockSignals(False)
        self.feedback.clear()
