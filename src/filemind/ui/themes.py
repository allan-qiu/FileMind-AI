from dataclasses import dataclass

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from filemind.domain.models import Theme


@dataclass(frozen=True)
class Colors:
    canvas: str
    surface: str
    sidebar: str
    text: str
    muted: str
    border: str
    accent: str
    on_accent: str
    tint: str
    disabled: str


PALETTES = {
    Theme.LIGHT: Colors(
        "#F5F7FB",
        "#FFFFFF",
        "#FFFFFF",
        "#17243B",
        "#53627A",
        "#D6DFEC",
        "#245CDD",
        "#FFFFFF",
        "#EAF0FF",
        "#66748A",
    ),
    Theme.DARK: Colors(
        "#111824",
        "#1B2535",
        "#16202E",
        "#ECF1FA",
        "#ABBAD0",
        "#415069",
        "#99B8FF",
        "#11234A",
        "#253857",
        "#9CAAC0",
    ),
}


def apply_theme(app: QApplication, theme: Theme) -> None:
    c = PALETTES[theme]
    palette = QPalette()
    for role, color in (
        (QPalette.ColorRole.Window, c.canvas),
        (QPalette.ColorRole.WindowText, c.text),
        (QPalette.ColorRole.Base, c.surface),
        (QPalette.ColorRole.AlternateBase, c.canvas),
        (QPalette.ColorRole.Text, c.text),
        (QPalette.ColorRole.Button, c.surface),
        (QPalette.ColorRole.ButtonText, c.text),
        (QPalette.ColorRole.Highlight, c.accent),
        (QPalette.ColorRole.HighlightedText, c.on_accent),
        (QPalette.ColorRole.ToolTipBase, c.surface),
        (QPalette.ColorRole.ToolTipText, c.text),
        (QPalette.ColorRole.PlaceholderText, c.muted),
    ):
        palette.setColor(role, QColor(color))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(c.disabled))
    palette.setColor(
        QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(c.disabled)
    )
    app.setPalette(palette)
    app.setStyleSheet(f"""
        QWidget {{ color: {c.text}; font-family: 'Microsoft YaHei UI', 'Segoe UI';
                   font-size: 13px; }}
        QMainWindow, QWidget#Shell, QWidget#PageBody {{ background: {c.canvas}; }}
        QFrame#Sidebar, QFrame#Toolbar {{ background: {c.sidebar}; }}
        QFrame#Sidebar {{ border-right: 1px solid {c.border}; }}
        QFrame#Toolbar {{ border-bottom: 1px solid {c.border}; }}
        QFrame#Card {{ background: {c.surface}; border: 1px solid {c.border};
                       border-radius: 12px; }}
        QFrame#Hero {{ background: {c.tint}; border: 1px solid {c.border};
                       border-radius: 14px; }}
        QLabel {{ background: transparent; border: none; }}
        QLabel#Brand {{ font-size: 21px; font-weight: 700; }}
        QLabel#Logo {{ color: {c.on_accent}; background: {c.accent}; border-radius: 10px;
                       font-size: 24px; font-weight: 700; }}
        QLabel#PageTitle {{ font-size: 27px; font-weight: 700; }}
        QLabel#HeroTitle {{ font-size: 28px; font-weight: 700; }}
        QLabel#SectionTitle {{ font-size: 16px; font-weight: 600; }}
        QLabel#Metric {{ font-size: 30px; font-weight: 700; }}
        QLabel#Muted {{ color: {c.muted}; }}
        QLabel#Eyebrow {{ color: {c.accent}; font-size: 12px; font-weight: 600; }}
        QLabel#Badge {{ background: {c.tint}; color: {c.accent}; border-radius: 6px;
                       padding: 5px 10px; font-size: 12px; font-weight: 600; }}
        QLabel#Safety {{ background: {c.tint}; color: {c.text}; border-radius: 8px;
                        padding: 10px 14px; }}
        QPushButton {{ background: {c.surface}; border: 1px solid {c.border};
                       border-radius: 7px; padding: 8px 14px; min-height: 22px; }}
        QPushButton:hover {{ background: {c.tint}; border-color: {c.accent}; }}
        QPushButton:pressed {{ background: {c.tint}; }}
        QPushButton:focus, QLineEdit:focus, QComboBox:focus, QTableWidget:focus {{
            border: 2px solid {c.accent}; }}
        QPushButton:disabled {{ background: {c.canvas}; color: {c.disabled};
                                border-color: {c.border}; }}
        QPushButton#Primary {{ background: {c.accent}; color: {c.on_accent};
                               border-color: {c.accent}; font-weight: 600; }}
        QPushButton#Primary:hover {{ border: 2px solid {c.text}; }}
        QPushButton#Primary:disabled {{ background: {c.canvas}; color: {c.disabled};
                                       border-color: {c.border}; }}
        QPushButton#Nav {{ text-align: left; border: 1px solid transparent;
                           padding: 10px 14px; background: transparent; min-height: 24px; }}
        QPushButton#Nav:hover {{ background: {c.canvas}; }}
        QPushButton#Nav:checked {{ background: {c.tint}; color: {c.accent}; font-weight: 600;
                                   border-color: {c.border}; }}
        QPushButton#Nav:focus {{ border-color: {c.accent}; }}
        QLineEdit, QComboBox {{ background: {c.surface}; border: 1px solid {c.border};
                              border-radius: 7px; padding: 9px 12px; min-height: 20px; }}
        QComboBox QAbstractItemView {{ background: {c.surface}; color: {c.text};
            selection-background-color: {c.tint}; selection-color: {c.text}; }}
        QTableWidget {{ background: {c.surface}; alternate-background-color: {c.canvas};
                        border: 1px solid {c.border}; border-radius: 8px;
                        gridline-color: {c.border}; selection-background-color: {c.tint};
                        selection-color: {c.text}; }}
        QTableWidget::item {{ padding: 8px; border: none; }}
        QHeaderView::section {{ background: {c.canvas}; color: {c.muted}; border: none;
                               border-bottom: 1px solid {c.border}; padding: 10px;
                               text-align: left; }}
        QScrollArea {{ border: none; background: {c.canvas}; }}
        QScrollBar:vertical {{ background: {c.canvas}; width: 10px; margin: 0; }}
        QScrollBar::handle:vertical {{ background: {c.border}; border-radius: 5px;
                                      min-height: 28px; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        QStatusBar {{ background: {c.surface}; color: {c.muted};
                      border-top: 1px solid {c.border}; }}
        QToolTip {{ background: {c.surface}; color: {c.text}; border: 1px solid {c.border}; }}
    """)
