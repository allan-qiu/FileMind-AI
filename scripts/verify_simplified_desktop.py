"""Render the actual Qt widgets using isolated local data; no network or credentials."""

import argparse
import json
from pathlib import Path

from PySide6.QtWidgets import QApplication

from filemind.core.paths import WorkspacePaths
from filemind.domain.models import Theme
from filemind.services.application import ApplicationService
from filemind.ui.main_window import MainWindow


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    opts = parser.parse_args()
    output = opts.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    app = QApplication([])
    service = ApplicationService.create(WorkspacePaths(output / "isolated-profile"))
    window = MainWindow(service)
    window.show()
    for theme in (Theme.LIGHT, Theme.DARK):
        window.set_theme(theme)
        for width, height in ((1080, 850), (880, 620)):
            window.resize(width, height)
            app.processEvents()
            assert window.stack.currentWidget() is window.organize
            assert window.server_nav.isVisible()
            assert not window.organize.options.isVisible()
            assert window.grab().save(str(output / f"desktop-{theme.value}-{width}.png"))
    assert window.close()
    (output / "report.json").write_text(
        json.dumps(
            {"qt_widget_render": "PASS", "sizes": [[1080, 850], [880, 620]],
             "themes": ["light", "dark"], "external_api": "NOT_EXECUTED"},
            indent=2,
        ), encoding="utf-8",
    )


if __name__ == "__main__":
    main()
