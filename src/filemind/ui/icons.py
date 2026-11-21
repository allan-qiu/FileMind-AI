from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

PATHS = (
    '<rect x="3" y="3" width="7" height="7" rx="1.5"/>'
    '<rect x="14" y="3" width="7" height="7" rx="1.5"/>'
    '<rect x="3" y="14" width="7" height="7" rx="1.5"/>'
    '<rect x="14" y="14" width="7" height="7" rx="1.5"/>',
    '<path d="M3 7V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5'
    'a2 2 0 0 1-2-2V7Z"/><path d="m9 13 2 2 4-4"/>',
    '<rect x="3" y="4" width="18" height="13" rx="2"/>'
    '<path d="M8 21h8m-4-4v4M6 11h3l2-4 3 7 2-3h2"/>',
    '<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5Z"/>',
    '<rect x="4" y="5" width="16" height="16" rx="2"/>'
    '<path d="M8 3v4m8-4v4M4 11h16m-12 4h3m-3 3h7"/>',
    '<path d="M3 11a9 9 0 1 1 2 7M3 4v7h7m2-4v5l3 2"/>',
    '<path d="m9 3-1 3-3 1-2 4 2 2v4l4 3 3-1 3 1 4-3v-4l2-2-2-4-3-1-1-3Z"/>'
    '<circle cx="12" cy="12" r="3"/>',
)


def navigation_icon(index: int, color: str) -> QIcon:
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        f'viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="1.7" '
        f'stroke-linecap="round" stroke-linejoin="round">{PATHS[index]}</svg>'
    )
    pixmap = QPixmap(48, 48)
    pixmap.setDevicePixelRatio(2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    # QPixmap's logical painting bounds are 24x24 at DPR 2, not its 48x48 pixels.
    QSvgRenderer(QByteArray(svg.encode())).render(painter, QRectF(0, 0, 24, 24))
    painter.end()
    return QIcon(pixmap)
