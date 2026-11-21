from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


def label(text: str, role: str = "", wrap: bool = True) -> QLabel:
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(wrap)
    if role:
        widget.setObjectName(role)
    return widget


def button(text: str, primary: bool = False, unavailable: str = "") -> QPushButton:
    widget = QPushButton(text)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    if primary:
        widget.setObjectName("Primary")
    if unavailable:
        widget.setEnabled(False)
        widget.setToolTip(unavailable)
        widget.setAccessibleDescription(unavailable)
    return widget


class Card(QFrame):
    def __init__(self, title: str = "", hero: bool = False):
        super().__init__()
        self.setObjectName("Hero" if hero else "Card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(22, 20, 22, 20)
        self.body.setSpacing(12)
        if title:
            self.body.addWidget(label(title, "SectionTitle"))


class Page(QScrollArea):
    def __init__(self, title: str, subtitle: str):
        super().__init__()
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("PageBody")
        self.body = QVBoxLayout(body)
        self.body.setContentsMargins(28, 24, 28, 28)
        self.body.setSpacing(20)
        self.heading = label(title, "PageTitle")
        self.heading.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.heading.setAccessibleName(title)
        heading_group = QVBoxLayout()
        heading_group.setSpacing(6)
        heading_group.addWidget(self.heading)
        heading_group.addWidget(label(subtitle, "Muted"))
        self.body.addLayout(heading_group)
        self.setWidget(body)

    def finish(self) -> None:
        self.body.addStretch()


class DataTable(QTableWidget):
    def __init__(self, headers: Sequence[str], rows: Sequence[Sequence[str]] = ()):
        super().__init__(0, len(headers))
        self.setHorizontalHeaderLabels(headers)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.horizontalHeader().setMinimumSectionSize(90)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(49)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setAlternatingRowColors(True)
        self.setShowGrid(False)
        self.setMinimumHeight(240)
        self.setAccessibleName("只读数据表")
        self.set_rows(rows)

    def set_rows(self, rows: Sequence[Sequence[str]]) -> None:
        self.setRowCount(len(rows))
        for row, values in enumerate(rows):
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                self.setItem(row, col, item)
