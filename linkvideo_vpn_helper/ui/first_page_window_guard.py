from __future__ import annotations

"""Prevent orphan Qt widgets from flashing as small LinkVideo windows.

This is a scoped *construction* guard, not a global hide-all-windows patch.
Explicit dialogs, menus and popups are never affected.
"""

import logging

from PySide6.QtCore import QObject, QEvent, Qt
from PySide6.QtWidgets import QDialog, QMainWindow, QWidget


class FirstPageWindowGuard(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._orphans: list[QWidget] = []

    def eventFilter(self, obj, event):
        if event.type() != QEvent.Type.Show or not isinstance(obj, QWidget):
            return False
        if isinstance(obj, (QDialog, QMainWindow)):
            return False
        if (not obj.isWindow() or obj.parentWidget() is not None or
                obj.windowType() != Qt.WindowType.Window):
            return False
        if obj not in self._orphans:
            # Show events occur before the native window is mapped. Keep the
            # widget logically visible so normal parenting/layout can proceed.
            obj.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
            self._orphans.append(obj)
            logging.getLogger("linkvideo.helper").info(
                "WINDOW | suppressed orphan during page construction | %s",
                type(obj).__name__[:60],
            )
        return False

    def release(self):
        for widget in self._orphans:
            try:
                if widget.parentWidget() is None:
                    # An orphan with no parent at the end of construction is
                    # a stray window, not a legitimate part of the page.
                    widget.hide()
                widget.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, False)
            except RuntimeError:
                pass
        self._orphans.clear()
