from __future__ import annotations

"""Restore mature desktop components after the archive inline-progress addition.

This module deliberately patches the shared components module before pages are
imported. It keeps the proven Russian date/time popover, page fade stack and
toast implementation while allowing TaskStatus.busy_inline to live in the base
module.
"""

from PySide6.QtCore import QEvent, QLocale, QPoint, QPropertyAnimation, Qt, QTimer, Signal, QEasingCurve
from PySide6.QtGui import QGuiApplication, QIntValidator
from PySide6.QtWidgets import (
    QApplication, QCalendarWidget, QFrame, QGraphicsOpacityEffect, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from linkvideo_vpn_helper.ui.components import Card


class AnimatedStack(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._pages: dict[str, QWidget] = {}
        self._current: str | None = None
        self._animation = None

    def addPage(self, key: str, widget: QWidget):
        widget.hide()
        self._pages[key] = widget
        self._layout.addWidget(widget)

    def page(self, key: str) -> QWidget | None:
        return self._pages.get(key)

    def setCurrent(self, key: str):
        if key not in self._pages or key == self._current:
            return
        if self._current and self._current in self._pages:
            self._pages[self._current].hide()
        new = self._pages[key]
        new.show()
        effect = QGraphicsOpacityEffect(new)
        effect.setOpacity(0.0)
        new.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b"opacity", new)
        animation.setDuration(170)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        def clear_effect():
            try:
                new.setGraphicsEffect(None)
            except Exception:
                pass

        animation.finished.connect(clear_effect)
        new._page_fade_animation = animation
        animation.start()
        self._animation = animation
        self._current = key


class DateTimePickerButton(QWidget):
    changed = Signal()
    MONTHS = (
        "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
        "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
    )

    def __init__(self, value=None, parent=None):
        super().__init__(parent)
        from PySide6.QtCore import QDateTime
        self._value = value or QDateTime.currentDateTime()
        self._popup = None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.button = QPushButton()
        self.button.setObjectName("DateTimeField")
        self.button.setMinimumHeight(44)
        self.button.clicked.connect(self._open)
        lay.addWidget(self.button)
        self._sync()

    def value(self):
        return self._value

    def setValue(self, value):
        self._value = value
        self._sync()
        self.changed.emit()

    def _sync(self):
        self.button.setText(self._value.toString("dd.MM.yyyy    HH:mm:ss"))

    @staticmethod
    def _two(value: int) -> str:
        return f"{int(value):02d}"

    def _close_popup(self):
        popup = self._popup
        if popup is None:
            return
        app = QApplication.instance()
        if app is not None:
            try:
                app.removeEventFilter(self)
            except Exception:
                pass
        self._popup = None
        try:
            popup.close()
        except RuntimeError:
            pass

    def eventFilter(self, watched, event):
        popup = self._popup
        if popup is not None and popup.isVisible():
            if event.type() == QEvent.Type.MouseButtonPress:
                widget = QApplication.widgetAt(event.globalPosition().toPoint()) if hasattr(event, "globalPosition") else None
                inside_popup = widget is not None and (widget is popup or popup.isAncestorOf(widget))
                inside_button = widget is not None and (widget is self.button or self.button.isAncestorOf(widget))
                if not inside_popup and not inside_button:
                    self._close_popup()
            elif event.type() in (QEvent.Type.ApplicationDeactivate, QEvent.Type.WindowDeactivate):
                self._close_popup()
        return super().eventFilter(watched, event)

    def _open(self):
        from PySide6.QtCore import QDateTime, QTime

        if self._popup is not None:
            self._close_popup()

        popup = QFrame(None, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        popup.setObjectName("DateTimePopup")
        popup.setMinimumWidth(380)
        popup.setMaximumWidth(430)
        root = QVBoxLayout(popup)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        month_row = QHBoxLayout()
        prev_btn = QPushButton("‹")
        prev_btn.setProperty("role", "icon")
        prev_btn.setFixedSize(38, 36)
        month_label = QLabel()
        month_label.setObjectName("SectionTitle")
        month_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        next_btn = QPushButton("›")
        next_btn.setProperty("role", "icon")
        next_btn.setFixedSize(38, 36)
        month_row.addWidget(prev_btn)
        month_row.addWidget(month_label, 1)
        month_row.addWidget(next_btn)
        root.addLayout(month_row)

        cal = QCalendarWidget()
        cal.setObjectName("CompactCalendar")
        cal.setNavigationBarVisible(False)
        cal.setGridVisible(False)
        cal.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        cal.setHorizontalHeaderFormat(QCalendarWidget.HorizontalHeaderFormat.ShortDayNames)
        cal.setLocale(QLocale("ru_RU"))
        cal.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
        cal.setSelectedDate(self._value.date())
        cal.setMinimumHeight(270)
        cal.setMaximumHeight(300)
        root.addWidget(cal)

        def update_month(year: int, month: int):
            month_label.setText(f"{self.MONTHS[max(1, min(12, month)) - 1]}  {year}")

        update_month(cal.yearShown(), cal.monthShown())
        cal.currentPageChanged.connect(update_month)
        prev_btn.clicked.connect(cal.showPreviousMonth)
        next_btn.clicked.connect(cal.showNextMonth)

        time_caption = QLabel("Время")
        time_caption.setObjectName("CardTitle")
        root.addWidget(time_caption)

        time_row = QHBoxLayout()
        time_row.setSpacing(7)
        parts = []
        current_time = self._value.time()
        for initial, max_value in ((current_time.hour(), 23), (current_time.minute(), 59), (current_time.second(), 59)):
            edit = QLineEdit(self._two(initial))
            edit.setObjectName("TimePart")
            edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
            edit.setMaxLength(2)
            edit.setValidator(QIntValidator(0, max_value, edit))
            edit.setFixedWidth(58)
            edit.setMinimumHeight(42)
            parts.append(edit)
        time_row.addStretch(1)
        time_row.addWidget(parts[0])
        colon1 = QLabel(":")
        colon1.setObjectName("BigValue")
        time_row.addWidget(colon1)
        time_row.addWidget(parts[1])
        colon2 = QLabel(":")
        colon2.setObjectName("BigValue")
        time_row.addWidget(colon2)
        time_row.addWidget(parts[2])
        time_row.addStretch(1)
        root.addLayout(time_row)

        quick = QHBoxLayout()
        quick.setSpacing(6)
        now_btn = QPushButton("Сейчас")
        zero_btn = QPushButton("00:00:00")
        end_btn = QPushButton("23:59:59")
        for button in (now_btn, zero_btn, end_btn):
            button.setProperty("role", "ghost")
        quick.addWidget(now_btn)
        quick.addWidget(zero_btn)
        quick.addWidget(end_btn)
        quick.addStretch(1)
        root.addLayout(quick)

        def set_time(qtime):
            parts[0].setText(self._two(qtime.hour()))
            parts[1].setText(self._two(qtime.minute()))
            parts[2].setText(self._two(qtime.second()))

        def set_now_minute():
            t = QTime.currentTime()
            set_time(QTime(t.hour(), t.minute(), 0))

        now_btn.clicked.connect(set_now_minute)
        zero_btn.clicked.connect(lambda: set_time(QTime(0, 0, 0)))
        end_btn.clicked.connect(lambda: set_time(QTime(23, 59, 59)))

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Отмена")
        ok = QPushButton("Готово")
        ok.setProperty("role", "primary")
        buttons.addWidget(cancel)
        buttons.addWidget(ok)
        root.addLayout(buttons)
        cancel.clicked.connect(self._close_popup)

        def accept_value():
            values = []
            maxima = (23, 59, 59)
            for edit, maximum in zip(parts, maxima):
                raw = edit.text().strip()
                if not raw:
                    edit.setFocus()
                    return
                value = int(raw)
                if not 0 <= value <= maximum:
                    edit.setFocus()
                    edit.selectAll()
                    return
                values.append(value)
            qtime = QTime(values[0], values[1], values[2])
            self.setValue(QDateTime(cal.selectedDate(), qtime))
            self._close_popup()

        ok.clicked.connect(accept_value)
        for edit in parts:
            edit.returnPressed.connect(accept_value)

        popup.adjustSize()
        pos = self.button.mapToGlobal(QPoint(0, self.button.height() + 5))
        screen = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
        if screen:
            area = screen.availableGeometry()
            popup.adjustSize()
            x = min(max(area.left() + 8, pos.x()), area.right() - popup.width() - 8)
            y = pos.y()
            if y + popup.height() > area.bottom() - 8:
                y = self.button.mapToGlobal(QPoint(0, -popup.height() - 5)).y()
            pos = QPoint(x, max(area.top() + 8, y))
        popup.move(pos)
        self._popup = popup
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

        def popup_destroyed(*_):
            if self._popup is popup:
                self._popup = None
            app2 = QApplication.instance()
            if app2 is not None:
                try:
                    app2.removeEventFilter(self)
                except Exception:
                    pass

        popup.destroyed.connect(popup_destroyed)
        popup.show()
        popup.raise_()
        popup.activateWindow()


class Toast(Card):
    def __init__(self, parent=None):
        super().__init__(parent, kind="hero")
        self.setVisible(False)
        self.setMinimumWidth(330)
        self.setMaximumWidth(520)
        self._opacity = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity)
        self._fade = QPropertyAnimation(self._opacity, b"opacity", self)
        self._fade.setDuration(160)
        self._fade.finished.connect(self._fade_finished)
        self._hiding = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hideAnimated)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(15, 12, 15, 12)
        lay.setSpacing(3)
        self.title = QLabel("")
        self.title.setObjectName("Value")
        self.text = QLabel("")
        self.text.setObjectName("Muted")
        self.text.setWordWrap(True)
        lay.addWidget(self.title)
        lay.addWidget(self.text)

    def showMessage(self, title: str, text: str = "", timeout_ms: int = 3500):
        self._hiding = False
        self.title.setText(title)
        self.text.setText(text)
        self.adjustSize()
        self.setVisible(True)
        self.raise_()
        self._fade.stop()
        self._opacity.setOpacity(0.0)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()
        self._timer.start(max(500, int(timeout_ms)))

    def hideAnimated(self):
        if not self.isVisible():
            return
        self._hiding = True
        self._fade.stop()
        self._fade.setStartValue(self._opacity.opacity())
        self._fade.setEndValue(0.0)
        self._fade.start()

    def _fade_finished(self):
        if self._hiding:
            self._hiding = False
            self.setVisible(False)


def install_components_compat() -> None:
    import linkvideo_vpn_helper.ui.components as components

    components.AnimatedStack = AnimatedStack
    components.DateTimePickerButton = DateTimePickerButton
    components.Toast = Toast
