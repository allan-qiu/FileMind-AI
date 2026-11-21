"""Native Qt render review, using only an isolated project-local workspace."""

import json
from pathlib import Path

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from filemind.core.paths import WorkspacePaths
from filemind.domain.models import Theme
from filemind.services.application import ApplicationService
from filemind.ui.main_window import PAGE_NAMES, MainWindow


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    output = root / ".artifacts" / "screenshots"
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    app.setStyle("Fusion")
    service = ApplicationService.create(WorkspacePaths(root / ".artifacts" / "render-workspace"))
    window = MainWindow(service)
    window.showNormal()
    window.organize.load_demo()
    results = []
    for theme in Theme:
        window.set_theme(theme)
        for width, height in ((1280, 840), (880, 620)):
            window.resize(width, height)
            for index, name in enumerate(PAGE_NAMES):
                window.navigate(index)
                window.stack.currentWidget().verticalScrollBar().setValue(0)
                QTest.qWait(30)
                app.processEvents()
                page = window.stack.currentWidget()
                assert page.widget().width() == page.viewport().width(), name
                filename = f"{theme.value}-{width}-{index + 1}.png"
                assert window.grab().save(str(output / filename))
                results.append({"theme": theme.value, "size": [width, height], "page": name})
    assert service.history_count() == 0
    assert window.close()
    (output / "review.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Rendered {len(results)} native Qt views. Output: {output}")


if __name__ == "__main__":
    main()
