from __future__ import annotations

from PySide6.QtCore import (
    Property, QEasingCurve, QEvent, QLocale, QPoint, QPropertyAnimation, QRectF, Qt, QTimer, Signal
)
from PySide6.QtGui import QPalette, QColor, QIntValidator, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication, QCalendarWidget, QDialog, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel,
    QMenu, QProgressBar, QPushButton, QScrollArea, QSpinBox, QTimeEdit, QVBoxLayout, QWidget, QLineEdit, QSizePolicy, QGraphicsDropShadowEffect
)

from linkvideo_vpn_helper.services.server_registry import ServerRegistry


def button_feedback(button: QPushButton, text: str = "✓ Скопировано", timeout_ms: int = 1200):
    """Short visual acknowledgement for copy/success actions."""
    if button is None:
        return
    original = str(button.property("feedback_original_text") or button.text())
    button.setProperty("feedback_original_text", original)
    button.setText(text)
    button.setProperty("feedback", "success")
    button.style().unpolish(button)
    button.style().polish(button)
    button.setEnabled(False)

    def restore():
        try:
            button.setText(original)
            button.setProperty("feedback", "")
            button.style().unpolish(button)
            button.style().polish(button)
            button.setEnabled(True)
        except RuntimeError:
            pass

    QTimer.singleShot(max(350, int(timeout_ms)), restore)


class Card(QFrame):
    def __init__(self, parent=None, subtle: bool = False, kind: str | None = None):
        super().__init__(parent)
        if kind == "hero":
            self.setObjectName("HeroCard")
        elif kind == "accent":
            self.setObjectName("AccentCard")
        elif kind == "danger":
            self.setObjectName("DangerCard")
        elif kind == "success":
            self.setObjectName("SuccessCard")
        else:
            self.setObjectName("SubtleCard" if subtle else "Card")
        # A very light desktop elevation makes the surface hierarchy read like a
        # modern web app instead of a stack of bordered group boxes.
        if not subtle and kind not in {"accent", "danger", "success"}:
            try:
                shadow = QGraphicsDropShadowEffect(self)
                shadow.setBlurRadius(22)
                shadow.setOffset(0, 4)
                shadow.setColor(QColor(17, 24, 39, 18))
                self.setGraphicsEffect(shadow)
            except Exception:
                pass


class PageHeader(QWidget):
    def __init__(self, title: str, subtitle: str = "", parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)
        self.title = QLabel(title)
        self.title.setObjectName("PageTitle")
        root.addWidget(self.title)
        self.subtitle = QLabel(subtitle)
        self.subtitle.setObjectName("PageSubtitle")
        self.subtitle.setWordWrap(True)
        self.subtitle.setVisible(bool(subtitle))
        root.addWidget(self.subtitle)


class SectionHeader(QWidget):
    def __init__(self, title: str, subtitle: str = "", parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(2)
        t = QLabel(title)
        t.setObjectName("SectionTitle")
        root.addWidget(t)
        if subtitle:
            s = QLabel(subtitle)
            s.setObjectName("Muted")
            s.setWordWrap(True)
            root.addWidget(s)


class StatusPill(QLabel):
    def __init__(self, text: str = "", kind: str = "neutral", parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setContentsMargins(10, 4, 10, 4)
        self.setMinimumHeight(25)
        self.set_status(text, kind)

    def set_status(self, text: str, kind: str = "neutral"):
        self.setText(text)
        self.setProperty("pill", kind)
        self.style().unpolish(self)
        self.style().polish(self)


class CounterControl(QWidget):
    valueChanged = Signal(int)

    def __init__(self, value: int = 1, minimum: int = 1, maximum: int = 14, parent=None):
        super().__init__(parent)
        self._value = max(minimum, min(maximum, int(value)))
        self.minimum = int(minimum)
        self.maximum = int(maximum)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.minus = QPushButton("−")
        self.minus.setProperty("role", "icon")
        self.minus.setFixedSize(42, 42)
        self.plus = QPushButton("+")
        self.plus.setProperty("role", "icon")
        self.plus.setFixedSize(42, 42)
        self.label = QLabel()
        self.label.setObjectName("BigValue")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setMinimumWidth(56)
        self.minus.clicked.connect(lambda: self.setValue(self._value - 1))
        self.plus.clicked.connect(lambda: self.setValue(self._value + 1))
        layout.addWidget(self.minus)
        layout.addWidget(self.label)
        layout.addWidget(self.plus)
        layout.addStretch(1)
        self._sync()

    def value(self) -> int:
        return self._value

    def setValue(self, value: int):
        value = max(self.minimum, min(self.maximum, int(value)))
        if value == self._value:
            return
        self._value = value
        self._sync()
        self.valueChanged.emit(value)

    def _sync(self):
        self.label.setText(str(self._value))
        self.minus.setEnabled(self._value > self.minimum)
        self.plus.setEnabled(self._value < self.maximum)


class SegmentedControl(QWidget):
    changed = Signal(str)

    def __init__(self, items: list[tuple[str, str]], current: str | None = None, parent=None):
        super().__init__(parent)
        self._buttons: dict[str, QPushButton] = {}
        self._current = current or (items[0][0] if items else "")
        self.setObjectName("SegmentHost")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(3)
        for key, text in items:
            btn = QPushButton(text)
            btn.setProperty("segment", "true")
            btn.clicked.connect(lambda checked=False, k=key: self.setCurrent(k))
            self._buttons[key] = btn
            lay.addWidget(btn)
        self._sync()

    def current(self) -> str:
        return self._current

    def setCurrent(self, key: str):
        if key not in self._buttons:
            return
        changed = key != self._current
        self._current = key
        self._sync()
        if changed:
            self.changed.emit(key)

    def _sync(self):
        for key, btn in self._buttons.items():
            btn.setProperty("active", "true" if key == self._current else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)


class ServerPicker(QWidget):
    changed = Signal(str)
    addRequested = Signal()

    AUTO = "__AUTO__"

    def __init__(self, registry: ServerRegistry, allow_auto: bool = False, parent=None, auto_text: str = "Автоматически подобрать сервер"):
        super().__init__(parent)
        self.registry = registry
        self.allow_auto = bool(allow_auto)
        self.auto_text = str(auto_text or "Автоматически подобрать сервер")
        self._host = self.AUTO if allow_auto else ""

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.frame = QFrame()
        self.frame.setObjectName("ServerPickerFrame")
        self.frame.setMinimumHeight(46)
        self.frame.setCursor(Qt.CursorShape.PointingHandCursor)
        fl = QHBoxLayout(self.frame)
        fl.setContentsMargins(14, 0, 14, 0)
        fl.setSpacing(12)
        self.host_label = QLabel()
        self.host_label.setObjectName("Value")
        self.country_label = QLabel()
        self.country_label.setObjectName("Muted")
        self.country_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        fl.addWidget(self.host_label, 1)
        fl.addWidget(self.country_label, 0)
        layout.addWidget(self.frame, 1)

        def click(event):
            if event.button() == Qt.MouseButton.LeftButton:
                self._show_menu()
        self.frame.mousePressEvent = click
        self.host_label.mousePressEvent = click
        self.country_label.mousePressEvent = click

        servers = self.registry.all(include_disabled=False)
        if not self._host and servers:
            self._host = servers[0].host
        self._sync()

    def host(self) -> str:
        return "" if self._host == self.AUTO else self._host

    def is_auto(self) -> bool:
        return self._host == self.AUTO

    def setAutomatic(self):
        if not self.allow_auto:
            return
        self._host = self.AUTO
        self._sync()
        self.changed.emit("")

    def setHost(self, host: str):
        if self.allow_auto and (not host or host == self.AUTO):
            self.setAutomatic()
            return
        item = self.registry.get(host)
        self._host = item.host
        self._sync()
        self.changed.emit(self._host)

    def refresh(self):
        if self._host != self.AUTO and self._host not in self.registry.hosts():
            servers = self.registry.all(include_disabled=False)
            self._host = servers[0].host if servers else (self.AUTO if self.allow_auto else "")
        self._sync()

    def _sync(self):
        # No decorative arrows/tooltips: the whole field is visibly clickable.
        if self._host == self.AUTO:
            self.host_label.setText(self.auto_text)
            self.country_label.setText("")
        elif self._host:
            item = self.registry.get(self._host)
            self.host_label.setText(item.host)
            self.country_label.setText(item.country)
        else:
            self.host_label.setText("VPN-сервер не выбран")
            self.country_label.setText("")

    def _show_menu(self):
        menu = QMenu(self)
        if self.allow_auto:
            action = menu.addAction("Автоматически подобрать сервер")
            action.triggered.connect(self.setAutomatic)
            menu.addSeparator()

        groups = (("Россия", []), ("Беларусь", []), ("Казахстан", []), ("Другие", []))
        group_map = {name: bucket for name, bucket in groups}
        for server in self.registry.all(include_disabled=False):
            group_map.get(server.country, group_map["Другие"]).append(server)

        for country, items in groups:
            if not items:
                continue
            header = menu.addAction(country)
            header.setEnabled(False)
            for server in items:
                marker = "✓  " if server.host == self._host else "    "
                action = menu.addAction(f"{marker}{server.host}        {server.country}")
                action.triggered.connect(lambda checked=False, h=server.host: self.setHost(h))
            menu.addSeparator()

        add_action = menu.addAction("＋  Добавить VPN-сервер")
        add_action.triggered.connect(self.addRequested.emit)
        menu.exec(self.frame.mapToGlobal(self.frame.rect().bottomLeft()))


def build_page_scaffold(parent: QWidget, *, max_width: int = 1440, min_width: int = 820, margins: int = 28, spacing: int = 16):
    """Create the common centered/responsive page surface used by Helper 2.2.

    Pages no longer stretch every card across ultra-wide screens. The viewport
    stays fluid, the useful work area is capped, and narrow windows get a
    horizontal scrollbar only when they truly cannot fit the minimum desktop
    layout.
    """
    host = QVBoxLayout(parent)
    host.setContentsMargins(0, 0, 0, 0)
    host.setSpacing(0)

    scroll = QScrollArea(parent)
    scroll.setObjectName("PageScroll")
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

    viewport = QWidget()
    viewport.setObjectName("PageViewport")
    center = QHBoxLayout(viewport)
    center.setContentsMargins(0, 0, 0, 0)
    center.setSpacing(0)

    canvas = QWidget()
    canvas.setObjectName("PageCanvas")
    canvas.setMinimumWidth(int(min_width))
    canvas.setMaximumWidth(int(max_width))
    canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    layout = QVBoxLayout(canvas)
    layout.setContentsMargins(int(margins), int(margins), int(margins), int(margins))
    layout.setSpacing(int(spacing))

    center.addStretch(1)
    center.addWidget(canvas, 20)
    center.addStretch(1)
    scroll.setWidget(viewport)
    host.addWidget(scroll)
    return scroll, canvas, layout


class Spinner(QWidget):
    def __init__(self, parent=None, size: int = 28):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.setInterval(24)
        self._timer.timeout.connect(self._tick)

    def start(self):
        self._timer.start()
        self.show()

    def stop(self):
        self._timer.stop()
        self.hide()

    def _tick(self):
        self._angle = (self._angle + 11) % 360
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(4, 4, self.width() - 8, self.height() - 8)
        pen = QPen(self.palette().color(QPalette.ColorRole.Highlight), 3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawArc(rect, int((90 - self._angle) * 16), int(-245 * 16))




class BusyDialog(QDialog):
    """Неблокирующее модальное окно ожидания поверх рабочего интерфейса.

    Длинные операции больше не встраиваются карточкой между содержимым страницы.
    Диалог остаётся поверх родительского окна, а worker-поток продолжает работу.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("BusyDialog")
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMinimumWidth(430)
        self.setMaximumWidth(520)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        card = QFrame()
        card.setObjectName("BusyDialogCard")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(22, 20, 22, 20)
        lay.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(12)
        self.spinner = Spinner(size=30)
        self.spinner.start()
        self.title = QLabel("Выполняю операцию")
        self.title.setObjectName("SectionTitle")
        head.addWidget(self.spinner)
        head.addWidget(self.title, 1)
        lay.addLayout(head)

        self.detail = QLabel("")
        self.detail.setObjectName("Muted")
        self.detail.setWordWrap(True)
        lay.addWidget(self.detail)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        self.progress.setMinimumHeight(8)
        self.progress.setMaximumHeight(8)
        self.progress.hide()
        lay.addWidget(self.progress)
        self.progress_text = QLabel("")
        self.progress_text.setObjectName("TinyMuted")
        self.progress_text.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.progress_text.hide()
        lay.addWidget(self.progress_text)

        hint = QLabel("Не закрывайте Helper до завершения операции.")
        hint.setObjectName("TinyMuted")
        lay.addWidget(hint)
        outer.addWidget(card)

    def reject(self):
        # Операция управляется worker-потоком страницы. Случайный Esc не должен
        # скрывать ожидание и создавать впечатление, что задача завершилась.
        return

    def update_busy(self, title: str, detail: str = "", progress: int | None = None, progress_text: str = ""):
        self.title.setText(str(title or "Выполняю операцию"))
        self.detail.setText(str(detail or ""))
        if progress is None:
            self.progress.hide()
            self.progress_text.hide()
        else:
            value = max(0, min(100, int(progress)))
            self.progress.setValue(value)
            self.progress.show()
            self.progress_text.setText(progress_text or f"{value}%")
            self.progress_text.show()
        self.adjustSize()

    def show_centered(self):
        self.adjustSize()
        parent = self.parentWidget()
        if parent is not None:
            frame = parent.frameGeometry()
            geo = self.frameGeometry()
            geo.moveCenter(frame.center())
            self.move(geo.topLeft())
        self.show()
        self.raise_()


class TaskStatus(Card):
    """Понятное состояние фоновой операции без вывода технического лога."""
    retryRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent, subtle=True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(9)
        row = QHBoxLayout()
        self.spinner = Spinner(size=28)
        self.icon = QLabel("")
        self.icon.setObjectName("BigValue")
        self.icon.setFixedWidth(28)
        self.title = QLabel("Готово к работе")
        self.title.setObjectName("Value")
        row.addWidget(self.spinner)
        row.addWidget(self.icon)
        row.addWidget(self.title, 1)
        layout.addLayout(row)
        self.detail = QLabel("")
        self.detail.setObjectName("Muted")
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        # Процент не рисуется внутри полосы высотой несколько пикселей: на
        # Windows текст обрезался и визуально ломал индикатор. Процент выводим
        # отдельной подписью ниже.
        self.progress.setTextVisible(False)
        self.progress.setMinimumHeight(10)
        self.progress.setMaximumHeight(10)
        self.progress.hide()
        layout.addWidget(self.progress)
        self.progress_text = QLabel("")
        self.progress_text.setObjectName("TinyMuted")
        self.progress_text.hide()
        layout.addWidget(self.progress_text)
        self.retry = QPushButton("Повторить")
        self.retry.setProperty("role", "soft")
        self.retry.clicked.connect(self.retryRequested.emit)
        self.retry.hide()
        layout.addWidget(self.retry, 0, Qt.AlignmentFlag.AlignLeft)
        self.spinner.hide()
        self.icon.hide()
        self._busy_dialog = None

    def _ensure_busy_dialog(self):
        parent = self.window()
        if self._busy_dialog is None or self._busy_dialog.parentWidget() is not parent:
            try:
                if self._busy_dialog is not None:
                    self._busy_dialog.close()
            except RuntimeError:
                pass
            self._busy_dialog = BusyDialog(parent)
        return self._busy_dialog

    def _close_busy_dialog(self):
        dlg = self._busy_dialog
        if dlg is not None:
            try:
                dlg.spinner.stop()
                dlg.hide()
            except RuntimeError:
                pass

    def busy(self, title: str, detail: str = "", progress: int | None = None, progress_text: str = ""):
        # Ожидание теперь показывается поверх окна, а не вставляется между
        # рабочими карточками страницы. Сам TaskStatus остаётся только для
        # результата/ошибки после завершения.
        self.hide()
        dlg = self._ensure_busy_dialog()
        dlg.spinner.start()
        dlg.update_busy(title, detail, progress, progress_text)
        dlg.show_centered()

    def done(self, title: str, detail: str = ""):
        self._close_busy_dialog()
        self.show()
        self._reset_title()
        self.spinner.stop()
        self.progress.hide()
        self.progress_text.hide()
        self.retry.hide()
        self.icon.setText("✓")
        self.icon.setObjectName("SuccessText")
        self.icon.show()
        self.title.setText(title)

        self.detail.setText(detail)
        self._refresh_icon()

    def warning(self, title: str, detail: str = ""):
        self._close_busy_dialog()
        self.show()
        self._reset_title()
        self.spinner.stop()
        self.progress.hide()
        self.progress_text.hide()
        self.retry.hide()
        self.icon.setText("!")
        self.icon.setObjectName("WarningText")
        self.icon.show()
        self.title.setText(title)
        self.detail.setText(detail)
        self._refresh_icon()

    def error(self, title: str, detail: str = "", retry: bool = False):
        self._close_busy_dialog()
        self.show()
        self.spinner.stop()
        self.progress.hide()
        self.progress_text.hide()
        self.title.setText(title)
        self.title.setObjectName("DangerText")
        self.detail.setText(detail)
        self.icon.setText("×")
        self.icon.setObjectName("DangerText")
        self.icon.show()
        self.retry.setVisible(bool(retry))
        self._refresh_icon()
        self.title.style().unpolish(self.title)
        self.title.style().polish(self.title)

    def _reset_title(self):
        self.title.setObjectName("Value")
        self.title.style().unpolish(self.title)
        self.title.style().polish(self.title)

    def _refresh_icon(self):
        self.icon.style().unpolish(self.icon)
        self.icon.style().polish(self.icon)


class MetricCard(Card):
    def __init__(self, label: str, value: str = "—", hint: str = "", parent=None):
        super().__init__(parent, subtle=True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(13, 11, 13, 11)
        lay.setSpacing(3)
        self.label = QLabel(label)
        self.label.setObjectName("TinyMuted")
        self.value = QLabel(value)
        self.value.setObjectName("BigValue")
        lay.addWidget(self.label)
        lay.addWidget(self.value)
        self.hint = QLabel(hint)
        self.hint.setObjectName("TinyMuted")
        self.hint.setWordWrap(True)
        self.hint.setVisible(bool(hint))
        lay.addWidget(self.hint)

    def setValue(self, value: str, hint: str | None = None):
        self.value.setText(str(value))
        if hint is not None:
            self.hint.setText(hint)
            self.hint.setVisible(bool(hint))


class EmptyState(Card):
    def __init__(self, title: str, text: str, symbol: str = "◇", parent=None):
        super().__init__(parent, subtle=True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 28, 28, 28)
        lay.setSpacing(8)
        lay.addStretch(1)
        icon = QLabel(symbol)
        icon.setObjectName("BigValue")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label = QLabel(title)
        title_label.setObjectName("SectionTitle")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        text_label = QLabel(text)
        text_label.setObjectName("Muted")
        text_label.setWordWrap(True)
        text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(icon)
        lay.addWidget(title_label)
        lay.addWidget(text_label)
        lay.addStretch(1)


class Switch(QWidget):
    toggled = Signal(bool)

    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self._checked = bool(checked)
        self._offset = 24.0 if self._checked else 4.0
        self.setFixedSize(48, 26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._anim = QPropertyAnimation(self, b"offset", self)
        self._anim.setDuration(150)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, value: bool):
        value = bool(value)
        if value == self._checked:
            return
        self._checked = value
        self._anim.stop()
        self._anim.setStartValue(self._offset)
        self._anim.setEndValue(24.0 if value else 4.0)
        self._anim.start()
        self.toggled.emit(value)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self.setChecked(not self._checked)
        super().mousePressEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = self.palette().color(QPalette.ColorRole.Highlight if self._checked else QPalette.ColorRole.Mid)
        if not self.isEnabled():
            track.setAlpha(100)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, 1, 48, 24), 12, 12)
        p.setBrush(self.palette().color(QPalette.ColorRole.Base))
        p.drawEllipse(QRectF(self._offset, 4, 18, 18))

    def getOffset(self) -> float:
        return self._offset

    def setOffset(self, value: float):
        self._offset = float(value)
        self.update()

    offset = Property(float, getOffset, setOffset)


class AnimatedStack(QWidget):
    """Лёгкий контейнер страниц с коротким fade без тяжёлого QStackedWidget."""
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
    """Компактный выбор даты/времени без нативного Windows time spinbox.

    Открывается как popover под полем. Время вводится тремя обычными полями,
    поэтому никаких системных стрелок рядом со значениями нет.
    """

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
        from PySide6.QtGui import QGuiApplication

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
        for b in (now_btn, zero_btn, end_btn):
            b.setProperty("role", "ghost")
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

        def _popup_destroyed(*_):
            if self._popup is popup:
                self._popup = None
            app2 = QApplication.instance()
            if app2 is not None:
                try:
                    app2.removeEventFilter(self)
                except Exception:
                    pass

        popup.destroyed.connect(_popup_destroyed)
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

