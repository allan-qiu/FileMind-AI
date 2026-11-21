import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from filemind import __version__
from filemind.core.config import ConfigError
from filemind.core.paths import project_paths
from filemind.infrastructure.database import DatabaseError
from filemind.services.application import ApplicationService
from filemind.ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("FileMind AI")
    app.setApplicationVersion(__version__)
    app.setStyle("Fusion")
    try:
        service = ApplicationService.create(project_paths())
        window = MainWindow(service)
    except (ConfigError, DatabaseError, OSError, ValueError) as exc:
        QMessageBox.critical(
            None,
            "FileMind AI 无法启动",
            f"{exc}\n\n请检查数据目录的配置和写入权限。应用未执行文件整理。",
        )
        return 1
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
