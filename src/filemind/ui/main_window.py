from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QCloseEvent, QKeySequence, QResizeEvent, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from filemind import __version__
from filemind.core.config import ConfigError
from filemind.domain.models import Theme
from filemind.services.application import ApplicationService
from filemind.ui.cloud_dialog import CloudDialog
from filemind.ui.icons import navigation_icon
from filemind.ui.pages.assistant import AssistantPage
from filemind.ui.pages.dashboard import DashboardPage
from filemind.ui.pages.history import HistoryPage
from filemind.ui.pages.monitoring import MonitoringPage
from filemind.ui.pages.organize import OrganizePage
from filemind.ui.pages.plans import PlansPage
from filemind.ui.pages.settings import SettingsPage
from filemind.ui.tasks import TaskController
from filemind.ui.themes import PALETTES, apply_theme
from filemind.ui.widgets import button, label

PAGE_NAMES = ("使用帮助", "整理文件", "自动整理", "AI 助手", "整理计划", "操作记录", "设置")


class MainWindow(QMainWindow):
    def __init__(self, service: ApplicationService):
        super().__init__()
        self.service = service
        self.theme = service.config.theme
        self.setWindowTitle("FileMind AI · 文件整理与服务器文件")
        self.cloud_dialog = None
        self.setMinimumSize(880, 620)
        self.resize(service.config.window_width, service.config.window_height)

        shell = QWidget()
        shell.setObjectName("Shell")
        root = QHBoxLayout(shell)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.setCentralWidget(shell)

        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(208)
        sidebar_body = QVBoxLayout(sidebar)
        sidebar_body.setContentsMargins(16, 26, 16, 20)
        sidebar_body.setSpacing(8)
        brand = QHBoxLayout()
        mark = label("F", "Logo", wrap=False)
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setFixedSize(40, 40)
        brand.addWidget(mark)
        brand.addWidget(label("FileMind", "Brand", wrap=False))
        sidebar_body.addLayout(brand)
        self.sidebar_tagline = label("AI 文件管理助手", "Muted")
        sidebar_body.addWidget(self.sidebar_tagline)
        sidebar_body.addSpacing(28)
        sidebar_body.addWidget(label("常用功能", "Muted"))

        self.nav_group = QButtonGroup(self)
        self.nav_buttons = []
        for index, title in enumerate(PAGE_NAMES):
            nav = button(title)
            nav.setObjectName("Nav")
            nav.setCheckable(True)
            nav.setIconSize(QSize(20, 20))
            nav.setToolTip(f"{title}  ·  Ctrl+{index + 1}")
            nav.setAccessibleName(title)
            self.nav_group.addButton(nav, index)
            self.nav_buttons.append(nav)
            sidebar_body.addWidget(nav)
            shortcut = QShortcut(QKeySequence(f"Ctrl+{index + 1}"), self)
            shortcut.activated.connect(lambda index=index: self.navigate(index))
        self.nav_group.idClicked.connect(self.navigate)
        self.server_nav = button("服务器文件", primary=True)
        self.server_nav.clicked.connect(self.open_cloud)
        sidebar_body.insertWidget(sidebar_body.indexOf(self.nav_buttons[5]), self.server_nav)
        self.more_button = button("更多功能 ▸")
        self.more_button.setCheckable(True)
        self.more_button.toggled.connect(self.show_more)
        sidebar_body.addWidget(self.more_button)
        self.show_more(False)
        sidebar_body.addStretch()
        sidebar_body.addWidget(label("确认后执行 · 默认不覆盖", "Badge"))
        self.sidebar_details = label("离线规则 / 可选 AI\n有条件撤销 · 真实监控", "Muted")
        sidebar_body.addWidget(self.sidebar_details)
        sidebar_body.addSpacing(10)
        self.sidebar_version = label(f"VERSION {__version__}  /  LOCAL", "Muted")
        sidebar_body.addWidget(self.sidebar_version)
        root.addWidget(sidebar)

        workspace = QVBoxLayout()
        workspace.setContentsMargins(0, 0, 0, 0)
        workspace.setSpacing(0)
        toolbar = QFrame()
        toolbar.setObjectName("Toolbar")
        toolbar_body = QHBoxLayout(toolbar)
        toolbar_body.setContentsMargins(28, 14, 28, 14)
        self.breadcrumb = label("工作空间  /  工作台", "Muted", wrap=False)
        toolbar_body.addWidget(self.breadcrumb, 1)
        toolbar_body.addWidget(label(f"本地整理 · V{__version__}", "Badge", wrap=False))
        self.cloud_button = button("服务器文件")
        self.cloud_button.clicked.connect(self.open_cloud)
        toolbar_body.addWidget(self.cloud_button)
        self.theme_button = button("切换深色")
        self.theme_button.setAccessibleName("切换浅色或深色主题")
        self.theme_button.setToolTip("切换主题  ·  Ctrl+Shift+L")
        self.theme_button.clicked.connect(self.toggle_theme)
        toolbar_body.addWidget(self.theme_button)
        workspace.addWidget(toolbar)

        safety = label(
            "选择文件 → 预览整理结果 → 确认整理。需要找回文件时，打开“操作记录”。", "Safety"
        )
        safety.setContentsMargins(20, 12, 20, 0)
        workspace.addWidget(safety)
        self.stack = QStackedWidget()
        self.dashboard = DashboardPage(service)
        self.organize = OrganizePage(service)
        self.monitoring = MonitoringPage(service)
        self.assistant = AssistantPage(service)
        self.plans = PlansPage()
        self.history = HistoryPage(service)
        self.settings = SettingsPage(service)
        for page in (
            self.dashboard,
            self.organize,
            self.monitoring,
            self.assistant,
            self.plans,
            self.history,
            self.settings,
        ):
            self.stack.addWidget(page)
        workspace.addWidget(self.stack, 1)
        root.addLayout(workspace, 1)
        self.statusBar().showMessage("就绪 · 本地整理可离线运行")
        self.statusBar().addPermanentWidget(label(f"V{__version__}", "Muted", wrap=False))

        self.dashboard.organize_requested.connect(self.open_demo)
        self.dashboard.real_requested.connect(lambda: self.navigate(1))
        self.organize.plan_ready.connect(self.plans.show_plan)
        self.plans.open_requested.connect(lambda: self.navigate(1))
        self.organize.operation_done.connect(self.history.refresh)
        self.monitoring.import_requested.connect(self.import_monitored)
        self.settings.theme_requested.connect(self.set_theme)
        self.settings.save_requested.connect(self.save_preferences)
        theme_shortcut = QShortcut(QKeySequence("Ctrl+Shift+L"), self)
        theme_shortcut.activated.connect(self.toggle_theme)
        self.set_theme(self.theme)
        self.navigate(1)
        self.startup_controller = TaskController(self)
        self.startup_controller.error.connect(lambda text: self.statusBar().showMessage(text))
        self.startup_controller.finished.connect(self.history.refresh)
        pending = any(
            row["state"] in {"PREPARED", "UNDO_PREPARED"}
            for row in service.local.repository.operations()
        )
        if pending or any(rule["enabled"] for rule in service.monitor.rules()):
            self.startup_controller.submit(
                lambda cancel, progress: (
                    service.local.reconcile(),
                    service.monitor.start_enabled(),
                )
            )
        if service.config.maximized:
            self.setWindowState(self.windowState() | Qt.WindowState.WindowMaximized)

    def show_more(self, expanded: bool) -> None:
        for index in (0, 2, 3, 4, 6):
            self.nav_buttons[index].setVisible(expanded)
        self.more_button.setText("收起更多功能 ▾" if expanded else "更多功能 ▸")

    def navigate(self, index: int) -> None:
        if not 0 <= index < self.stack.count():
            return
        if index in (0, 2, 3, 4, 6):
            self.more_button.setChecked(True)
        self.stack.setCurrentIndex(index)
        self.nav_buttons[index].setChecked(True)
        self.breadcrumb.setText(f"工作空间  /  {PAGE_NAMES[index]}")
        self.stack.currentWidget().heading.setFocus(Qt.FocusReason.OtherFocusReason)
        if index == 5:
            self.history.refresh()
        if index == 0:
            self.dashboard.refresh(self.service)

    def open_demo(self) -> None:
        self.organize.load_demo()
        self.navigate(1)

    def open_cloud(self) -> None:
        if self.cloud_dialog is None:
            self.cloud_dialog = CloudDialog(self, retain_session=True)
        self.cloud_dialog.exec()

    def import_monitored(self, paths):
        self.navigate(1)
        self.organize.scan_paths(paths)

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        app = QApplication.instance()
        assert isinstance(app, QApplication)
        apply_theme(app, theme)
        for index, nav in enumerate(self.nav_buttons):
            nav.setIcon(navigation_icon(index, PALETTES[theme].accent))
        self.setWindowIcon(navigation_icon(1, PALETTES[theme].accent))
        self.theme_button.setText("切换深色" if theme == Theme.LIGHT else "切换浅色")
        self.settings.sync_theme(theme)

    def toggle_theme(self) -> None:
        self.set_theme(Theme.DARK if self.theme == Theme.LIGHT else Theme.LIGHT)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if hasattr(self, "sidebar_tagline"):
            for widget in (self.sidebar_tagline, self.sidebar_details, self.sidebar_version):
                widget.setVisible(self.height() >= 740)

    def save_preferences(self) -> bool:
        normal = self.normalGeometry()
        width = max(880, min(normal.width(), 7680))
        height = max(620, min(normal.height(), 4320))
        try:
            self.service.save_preferences(self.theme, width, height, self.isMaximized())
        except ConfigError as exc:
            self.settings.feedback.setText("保存失败，原配置已保留。")
            QMessageBox.warning(self, "界面偏好未保存", str(exc))
            return False
        self.settings.feedback.setText("已保存界面偏好。下次启动将恢复主题与窗口大小。")
        self.statusBar().showMessage("已保存界面偏好", 5000)
        return True

    def closeEvent(self, event: QCloseEvent) -> None:
        controllers = (
            self.organize.controller,
            self.history.controller,
            self.monitoring.controller,
            self.startup_controller,
            *([self.cloud_dialog] if self.cloud_dialog else []),
        )
        if any(controller.busy for controller in controllers):
            for controller in controllers:
                if hasattr(controller, "cancel"):
                    controller.cancel()
            self.statusBar().showMessage("正在取消后台任务，请待当前步骤完成后再关闭。")
            event.ignore()
            return
        if self.save_preferences():
            self.service.watcher.stop()
            if self.cloud_dialog:
                self.cloud_dialog.dispose_session()
            event.accept()
        else:
            # A visible save failure keeps the window open so the user can retry.
            event.ignore()
