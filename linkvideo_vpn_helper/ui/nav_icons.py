from __future__ import annotations

"""Tiny consistent navigation pictograms drawn by Qt, without web assets."""

from PySide6.QtCore import QByteArray
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

# Path drawings use the same 24 × 24 stroke grid for every section.
_DRAWINGS = {
    "create": '<path d="M12 4v16M4 12h16"/>',
    "search": '<circle cx="10.8" cy="10.8" r="6.4"/><path d="m16 16 5 5"/>',
    "archive": '<path d="M12 3v13m-5-5 5 5 5-5M4 19v2h16v-2"/>',
    "diagnostics": '<path d="M2 12h5l3-8 4 16 3-8h5"/>',
    "inactive": '<path d="M7 3h10M7 21h10M8 4c0 5 8 5 8 8s-8 3-8 8m8-16c0 5-8 5-8 8s8 3 8 8"/>',
    "vpn_servers": '<rect x="3" y="4" width="18" height="7" rx="1.5"/><rect x="3" y="14" width="18" height="7" rx="1.5"/><path d="M7 7.5h.1M7 17.5h.1M11 7.5h7M11 17.5h7"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M10 2h4l.7 2.6 2 1.1 2.6-.7 2 3.4-1.9 1.9v2.2l1.9 1.9-2 3.4-2.6-.7-2 1.1L14 22h-4l-.7-2.6-2-1.1-2.6.7-2-3.4 1.9-1.9v-2.2L2.7 9.6l2-3.4 2.6.7 2-1.1z"/>',
}


def navigation_icon(key: str, *, color: str = "#D8E0E8", pixels: int = 20) -> QIcon:
    """Rasterise one local vector icon for a DPR-independent QWidget button."""
    drawing = _DRAWINGS.get(key)
    if not drawing:
        return QIcon()
    size = max(16, int(pixels))
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        f'fill="none" stroke="{color}" stroke-width="2" '
        f'stroke-linecap="round" stroke-linejoin="round">{drawing}</svg>'
    )
    image = QPixmap(size, size)
    image.fill(__import__("PySide6.QtCore", fromlist=["Qt"]).Qt.GlobalColor.transparent)
    painter = QPainter(image)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    renderer.render(painter)
    painter.end()
    return QIcon(image)
