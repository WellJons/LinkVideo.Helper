from __future__ import annotations

"""Route mouse-wheel input to the nearest hovered scrollable field.

Qt can propagate an unhandled wheel event from a list/table/text editor to the
page QScrollArea, especially at the edge of the inner scrollbar. For Helper this
feels like the whole page jumps while the pointer is still over a field. The
application filter below always consumes the event in the nearest
QAbstractScrollArea, including at its top/bottom edge.
"""

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QApplication, QAbstractScrollArea, QWidget


_INSTALLED = False
_FILTER = None


class _NestedScrollFilter(QObject):
    @staticmethod
    def _nearest_area(widget) -> QAbstractScrollArea | None:
        current = widget if isinstance(widget, QWidget) else None
        while current is not None:
            if isinstance(current, QAbstractScrollArea):
                return current
            current = current.parentWidget()
        return None

    def eventFilter(self, watched, event):
        if event.type() != QEvent.Type.Wheel:
            return False
        area = self._nearest_area(watched)
        if area is None or not area.isEnabled():
            return False

        vertical = area.verticalScrollBar()
        horizontal = area.horizontalScrollBar()
        angle = event.angleDelta()
        pixel = event.pixelDelta()
        use_horizontal = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if vertical.maximum() <= vertical.minimum() and horizontal.maximum() > horizontal.minimum():
            use_horizontal = True

        bar = horizontal if use_horizontal else vertical
        delta = angle.x() if use_horizontal else angle.y()
        pixel_delta = pixel.x() if use_horizontal else pixel.y()
        if pixel_delta:
            amount = int(pixel_delta)
        elif delta:
            steps = max(1, abs(int(delta)) // 120)
            amount = (1 if delta > 0 else -1) * max(36, int(bar.singleStep()) * 3) * steps
        else:
            event.accept()
            return True

        bar.setValue(bar.value() - amount)
        event.accept()
        return True


def install_nested_scroll_guard() -> None:
    global _INSTALLED, _FILTER
    if _INSTALLED:
        return
    app = QApplication.instance()
    if app is None:
        return
    _FILTER = _NestedScrollFilter(app)
    app.installEventFilter(_FILTER)
    _INSTALLED = True
