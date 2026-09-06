from __future__ import annotations

import random
import re
import threading
import time

from PySide6.QtCore import QEasingCurve, QPointF, QPropertyAnimation, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QBoxLayout, QDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from linkvideo_vpn_helper.services.errors import classify_exception
from linkvideo_vpn_helper.services.search_service import FastSearchService
from linkvideo_vpn_helper.services.server_registry import ServerRegistry
from linkvideo_vpn_helper.services.vpn_service import ClientRecord, SessionCredentials, VPNService
from linkvideo_vpn_helper.ui.components import (
    Card, CounterControl, EmptyState, MetricCard, PageHeader, SegmentedControl,
    ServerPicker, StatusPill, TaskStatus, button_feedback, build_page_scaffold,
)
from linkvideo_vpn_helper.ui.dialogs import AddServerDialog, ConfirmDialog, PasswordDialog


class TrafficGraph(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rx: list[float] = []
        self.tx: list[float] = []
        self.setMinimumHeight(250)

    def add(self, rx: float, tx: float):
        self.rx.append(max(0.0, float(rx or 0)))
        self.tx.append(max(0.0, float(tx or 0)))
        self.rx = self.rx[-80:]
        self.tx = self.tx[-80:]
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(12, 16, -12, -28)
        if rect.width() < 80 or rect.height() < 80:
            return
        pal = self.palette()
        grid = pal.color(QPalette.ColorRole.Mid)
        muted = pal.color(QPalette.ColorRole.PlaceholderText)
        painter.setPen(QPen(grid, 1))
        painter.drawRoundedRect(rect, 12, 12)
        for i in range(1, 4):
            y = rect.top() + rect.height() * i / 4
            painter.drawLine(rect.left(), int(y), rect.right(), int(y))
        values = self.rx + self.tx
        top = max(values) if values else 1.0
        top = max(0.1, top * 1.2)

        def draw(vals, color):
            if len(vals) < 2:
                return
            painter.setPen(QPen(QColor(color), 2.5))
            step = rect.width() / max(1, len(vals) - 1)
            points = [QPointF(rect.left() + i * step, rect.bottom() - (v / top) * rect.height()) for i, v in enumerate(vals)]
            for a, b in zip(points, points[1:]):
                painter.drawLine(a, b)

        draw(self.rx, pal.color(QPalette.ColorRole.Link).name())
        draw(self.tx, pal.color(QPalette.ColorRole.Highlight).name())
        painter.setPen(muted)
        painter.drawText(rect.left(), rect.bottom() + 20, "Получение")
        painter.setPen(pal.color(QPalette.ColorRole.Highlight))
        painter.drawText(rect.left() + 95, rect.bottom() + 20, "Отправка")


class TrafficDialog(QDialog):
    """Мониторинг общей VPN-сессии.

    Трафик по отдельным NAT-портам намеренно не показывается: RouterOS conntrack
    на используемых серверах не давал достаточно стабильных данных для такого
    статуса. В Helper отображаются только подтверждённые RX/TX самой VPN-сессии.
    """

    sampleReady = Signal(object, object)

    def __init__(self, service: VPNService, credentials: SessionCredentials, client: ClientRecord, parent=None):
        super().__init__(parent)
        self.service = service
        self.credentials = credentials
        self.client = client
        self._busy = False
        self._last = None
        self.setWindowTitle(f"Мониторинг · {client.login}")
        self.resize(800, 560)
        self.setMinimumSize(660, 480)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(14)

        top = QHBoxLayout()
        title = QLabel(client.login)
        title.setObjectName("PageTitle")
        self.status = StatusPill("VPN онлайн" if client.is_online else "VPN офлайн", "success" if client.is_online else "neutral")
        top.addWidget(title)
        top.addStretch(1)
        top.addWidget(self.status)
        root.addLayout(top)
        self.server_label = QLabel(f"{client.server}    {client.remote_address}")
        self.server_label.setObjectName("Muted")
        root.addWidget(self.server_label)

        metrics = QHBoxLayout()
        self.rx_metric = MetricCard("Получение", "0 Mbps")
        self.tx_metric = MetricCard("Отправка", "0 Mbps")
        self.total_metric = MetricCard("Всего трафика", "—")
        self.uptime_metric = MetricCard("VPN uptime", client.uptime or "—")
        for w in (self.rx_metric, self.tx_metric, self.total_metric, self.uptime_metric):
            metrics.addWidget(w, 1)
        root.addLayout(metrics)

        self.graph = TrafficGraph()
        root.addWidget(self.graph, 1)

        note = QLabel("График показывает общий трафик VPN-подключения клиента. Статус трафика отдельных NAT-портов не используется.")
        note.setObjectName("TinyMuted")
        note.setWordWrap(True)
        root.addWidget(note)

        row = QHBoxLayout()
        row.addStretch(1)
        close = QPushButton("Закрыть")
        close.clicked.connect(self.close)
        row.addWidget(close)
        root.addLayout(row)

        self.sampleReady.connect(self._on_sample)
        self.timer = QTimer(self)
        self.timer.setInterval(2500)
        self.timer.timeout.connect(self._poll)
        self.timer.start()
        self._poll()

    def _poll(self):
        if self._busy:
            return
        self._busy = True
        server = self.client.server
        login = self.client.login

        def worker():
            try:
                self.sampleReady.emit(self.service.get_client(server, self.credentials, login), None)
            except Exception as exc:
                self.sampleReady.emit(None, classify_exception(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _on_sample(self, client, error):
        self._busy = False
        if error or client is None:
            detail = getattr(error, "message", None) or "Клиент не найден"
            self.status.set_status("Ошибка обновления", "danger")
            self.server_label.setText(detail)
            return
        now = time.time()
        rx = int(client.rx_bytes or 0)
        tx = int(client.tx_bytes or 0)
        rx_rate = tx_rate = 0.0
        if self._last:
            old_rx, old_tx, old_t = self._last
            dt = max(0.2, now - old_t)
            rx_rate = max(0, rx - old_rx) * 8 / dt / 1_000_000
            tx_rate = max(0, tx - old_tx) * 8 / dt / 1_000_000
        elif client.rx_rate or client.tx_rate:
            # На части RouterOS current rate приходит сразу с интерфейса.
            rx_rate = max(0.0, float(client.rx_rate or 0) / 1_000_000)
            tx_rate = max(0.0, float(client.tx_rate or 0) / 1_000_000)
        self._last = (rx, tx, now)
        self.client = client
        self.graph.add(rx_rate, tx_rate)
        self.status.set_status("VPN онлайн" if client.is_online else "VPN офлайн", "success" if client.is_online else "neutral")
        self.server_label.setText(f"{client.server}    {client.remote_address}")
        self.rx_metric.setValue(f"{rx_rate:.2f} Mbps", self._fmt(rx))
        self.tx_metric.setValue(f"{tx_rate:.2f} Mbps", self._fmt(tx))
        self.total_metric.setValue(self._fmt(rx + tx), f"RX {self._fmt(rx)} · TX {self._fmt(tx)}")
        self.uptime_metric.setValue(client.uptime or "—")

    @staticmethod
    def _fmt(n):
        n = float(n or 0)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if n < 1024 or unit == "TB":
                return f"{n:.1f} {unit}"
            n /= 1024

    def closeEvent(self, event):
        self.timer.stop()
        super().closeEvent(event)


class ClientCardDialog(QDialog):
    """Отдельное рабочее окно карточки клиента.

    Поиск остаётся компактным в основной странице. Управление открывается только
    после выбора конкретной учётной записи и не растягивает страницу вниз.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Карточка клиента · LinkVideo.Helper")
        self.setModal(False)
        self.resize(980, 760)
        self.setMinimumSize(760, 600)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        self.task = TaskStatus()
        self.task.hide()
        root.addWidget(self.task)

        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.host = QWidget()
        self.content_layout = QVBoxLayout(self.host)
        self.content_layout.setContentsMargins(2, 2, 2, 2)
        self.content_layout.setSpacing(10)
        self.scroll.setWidget(self.host)
        root.addWidget(self.scroll, 1)


class SearchManagePage(QWidget):
    searchReady = Signal(object)
    progressReady = Signal(int, int, str)
    actionReady = Signal(object, object, str)
    liveReady = Signal(object)

    MAX_CONTENT_WIDTH = 1280

    def __init__(self, service: VPNService, search: FastSearchService, credentials: SessionCredentials, registry: ServerRegistry, parent=None):
        super().__init__(parent)
        self.service = service
        self.search = search
        self.credentials = credentials
        self.registry = registry
        self.current: ClientRecord | None = None
        self._deleted_current = None
        self._deleted_lookup_query = ""
        self._deleted_lookup_pending = False
        self._active_match_count = 0
        self._search_had_errors = False
        self._mode = "login"
        self._selected_port: int | None = None
        self._cancel_event = None
        self._action_busy = False
        self._recent_new_ports: set[int] = set()
        self._pending_old_ports: set[int] = set()
        self.searchReady.connect(self._on_search)
        self.progressReady.connect(self._on_progress)
        self.actionReady.connect(self._on_action)
        self.liveReady.connect(self._on_live_refresh)
        self._live_timer = QTimer(self)
        self._live_timer.setInterval(15000)
        self._live_timer.timeout.connect(self._silent_refresh)
        self._build()

    def _build(self):
        # 3.0.0: поиск и карточка живут внутри одной страницы. При выборе клиента
        # панель результатов плавно сжимается в левую колонку, а карточка занимает
        # освободившееся место. Отдельное окно больше не создаётся.
        self.page_scroll, self.page_canvas, self.page_layout = build_page_scaffold(
            self, max_width=1360, min_width=760, margins=22, spacing=12
        )

        self.workspace = QWidget()
        workspace_l = QHBoxLayout(self.workspace)
        workspace_l.setContentsMargins(0, 0, 0, 0)
        workspace_l.setSpacing(12)

        self.left_card = Card(kind="hero")
        self.left_card.setMinimumWidth(320)
        ll = QVBoxLayout(self.left_card)
        ll.setContentsMargins(18, 16, 18, 16)
        ll.setSpacing(10)

        self.search_form = QWidget()
        sfl = QVBoxLayout(self.search_form)
        sfl.setContentsMargins(0, 0, 0, 0)
        sfl.setSpacing(10)
        title = QLabel("Поиск клиента")
        title.setObjectName("SectionTitle")
        hint = QLabel("Введите логин или внешний порт. Выберите найденную учётную запись — карточка откроется здесь же, без нового окна.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        sfl.addWidget(title)
        sfl.addWidget(hint)

        self.mode = SegmentedControl([("login", "По логину"), ("port", "По порту")], "login")
        self.mode.changed.connect(self._set_mode)
        sfl.addWidget(self.mode)

        self.server_picker = ServerPicker(self.registry, allow_auto=False)
        self.server_picker.addRequested.connect(self._add_server)
        self.server_picker.refresh()
        self.server_picker.hide()
        sfl.addWidget(self.server_picker)

        self.query = QLineEdit()
        self.query.setPlaceholderText("Например: 890000001")
        self.query.returnPressed.connect(self._search)
        sfl.addWidget(self.query)

        search_actions = QHBoxLayout()
        search_actions.setSpacing(8)
        self.btn_search = QPushButton("Найти")
        self.btn_search.setProperty("role", "primary")
        self.btn_search.setMinimumHeight(44)
        self.btn_search.clicked.connect(self._search)
        self.btn_refresh = QPushButton("Обновить результаты")
        self.btn_refresh.clicked.connect(self._refresh_search)
        search_actions.addWidget(self.btn_search, 1)
        search_actions.addWidget(self.btn_refresh, 1)
        sfl.addLayout(search_actions)

        self.task = TaskStatus()
        self.task.hide()
        sfl.addWidget(self.task)
        ll.addWidget(self.search_form)

        results_row = QHBoxLayout()
        self.results_title = QLabel("Результаты")
        self.results_title.setObjectName("CardTitle")
        self.open_hint = QLabel("Клик — открыть карточку")
        self.open_hint.setObjectName("TinyMuted")
        results_row.addWidget(self.results_title)
        results_row.addStretch(1)
        results_row.addWidget(self.open_hint)
        ll.addLayout(results_row)

        self.results = QListWidget()
        self.results.setObjectName("SearchResultsList")
        self.results.setSpacing(0)
        self.results.itemClicked.connect(self._select_result)
        self.results.setMinimumHeight(360)
        self.results.setMaximumHeight(720)
        ll.addWidget(self.results, 1)
        self.search_note = QLabel("")
        self.search_note.setObjectName("TinyMuted")
        self.search_note.setWordWrap(True)
        ll.addWidget(self.search_note)

        workspace_l.addWidget(self.left_card, 4)

        self.detail_panel = Card()
        self.detail_panel.setObjectName("ClientWorkspaceCard")
        self.detail_panel.setMinimumWidth(590)
        detail_outer = QVBoxLayout(self.detail_panel)
        detail_outer.setContentsMargins(14, 14, 14, 14)
        detail_outer.setSpacing(10)

        detail_nav = QHBoxLayout()
        self.back_to_results = QPushButton("← Назад к поиску")
        self.back_to_results.setProperty("role", "ghost")
        self.back_to_results.clicked.connect(self._close_client_view)
        self.detail_route_title = QLabel("Карточка клиента")
        self.detail_route_title.setObjectName("CardTitle")
        detail_nav.addWidget(self.back_to_results)
        detail_nav.addWidget(self.detail_route_title)
        detail_nav.addStretch(1)
        detail_outer.addLayout(detail_nav)

        self.client_task = TaskStatus()
        self.client_task.hide()
        detail_outer.addWidget(self.client_task)

        self.detail_scroll = QScrollArea()
        self.detail_scroll.setWidgetResizable(True)
        self.detail_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.detail_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.detail_host = QWidget()
        self.detail_l = QVBoxLayout(self.detail_host)
        self.detail_l.setContentsMargins(2, 2, 2, 2)
        self.detail_l.setSpacing(10)
        self.detail_scroll.setWidget(self.detail_host)
        detail_outer.addWidget(self.detail_scroll, 1)
        self.detail_panel.hide()
        workspace_l.addWidget(self.detail_panel, 8)

        self.workspace.setMinimumHeight(640)
        self.page_layout.addWidget(self.workspace, 1)
        self._panel_anim = None

    def _open_client_view(self):
        if not self.current:
            return
        self.detail_route_title.setText(f"Клиент {self.current.login}")
        self.client_task.hide()
        self.detail_panel.show()
        self.search_form.hide()
        self.open_hint.setText("Выберите другую запись")
        self.results_title.setText("Найденные клиенты")
        self._animate_search_panel(compact=True)
        self._render_client()
        QTimer.singleShot(0, lambda: self.detail_scroll.verticalScrollBar().setValue(0))

    def _close_client_view(self, checked=False, immediate: bool = False):
        if immediate:
            self._deleted_current = None
            self.detail_panel.hide()
            self.search_form.show()
            self.left_card.setMaximumWidth(16777215)
            self.results_title.setText("Результаты")
            self.open_hint.setText("Клик — открыть карточку")
            return
        if not self.detail_panel.isVisible():
            return
        self.detail_panel.hide()
        self.search_form.show()
        self.results_title.setText("Результаты")
        self.open_hint.setText("Клик — открыть карточку")
        self._animate_search_panel(compact=False)

    def _animate_search_panel(self, compact: bool):
        # Мягкое сжатие/раскрытие левой панели — визуально похоже на переход
        # внутри web-приложения, но остаётся обычным QWidget без отдельного окна.
        if self._panel_anim is not None:
            self._panel_anim.stop()
        start = max(270, self.left_card.width())
        end = 300 if compact else max(760, self.workspace.width() - 24)
        anim = QPropertyAnimation(self.left_card, b"maximumWidth", self)
        anim.setDuration(230)
        anim.setStartValue(start)
        anim.setEndValue(end)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        if not compact:
            anim.finished.connect(lambda: self.left_card.setMaximumWidth(16777215))
        self._panel_anim = anim
        anim.start()

    def resizeEvent(self, event):
        super().resizeEvent(event)

    def _sync_responsive_layout(self):
        # Компоновка поиска теперь одноколоночная и не требует переноса карточки.
        return

    def cancel_current_action(self) -> bool:
        event = self._cancel_event
        if event is None or event.is_set():
            return False
        event.set()
        self._cancel_event = None
        self.btn_search.setEnabled(True)
        self.task.show()
        self.task.warning("Поиск остановлен", "Операция отменена клавишей Esc.")
        return True

    def _set_mode(self, mode: str):
        self._mode = mode
        self.server_picker.setVisible(mode == "port")
        if mode == "login":
            self.query.setPlaceholderText("Например: 890000001")
        else:
            self.query.setPlaceholderText("Внешний порт, например 11313")

    def _add_server(self):
        dialog = AddServerDialog(self.registry, self)
        if dialog.exec() and dialog.server:
            self.server_picker.refresh()
            self.server_picker.setHost(dialog.server.host)

    def _refresh_search(self):
        if self.query.text().strip():
            self._search()
        elif self.current:
            self._refresh()

    def _search(self):
        if self._cancel_event is not None and not self._cancel_event.is_set():
            self.task.show()
            self.task.warning("Поиск уже выполняется", "Дождитесь завершения текущего поиска или нажмите Esc.")
            return
        q = self.query.text().strip()
        if not q:
            self.task.show()
            self.task.warning("Введите значение", "Укажите логин клиента или внешний порт.")
            return
        servers = self.registry.hosts()
        if not servers:
            self.task.show()
            self.task.warning("Нет активных VPN-серверов", "Включите хотя бы один сервер в настройках.")
            return
        if self._mode == "port":
            try:
                port_value = int(q)
            except Exception:
                port_value = 0
            if not (1 <= port_value <= 65535):
                self.task.show()
                self.task.warning("Некорректный порт", "Введите номер внешнего порта от 1 до 65535.")
                return
            if not self.server_picker.host():
                self.task.show()
                self.task.warning("Выберите сервер", "Поиск по порту выполняется на конкретном VPN-сервере.")
                return

        cancel_event = threading.Event()
        self._cancel_event = cancel_event
        self.btn_search.setEnabled(False)
        self.results.clear()
        self.search_note.clear()
        self.current = None
        self._deleted_current = None
        self._deleted_lookup_query = q
        self._deleted_lookup_pending = False
        self._active_match_count = 0
        self._search_had_errors = False
        self._selected_port = None
        self._recent_new_ports.clear()
        self._close_client_view(immediate=True)
        self.task.show()

        if self._mode == "login":
            self.task.busy("Ищу клиента", f"Проверено 0 из {len(servers)} серверов", 0)
        else:
            self.task.busy("Ищу порт", f"Проверяю {self.server_picker.host()}…")

        def worker():
            try:
                if self._mode == "login":
                    report = self.search.search_login_all(
                        servers, self.credentials, q,
                        lambda a, b, srv: (not cancel_event.is_set()) and self.progressReady.emit(a, b, srv),
                        cancel_event=cancel_event,
                    )
                else:
                    if cancel_event.is_set():
                        return
                    report = self.search.search_port(self.server_picker.host(), self.credentials, int(q))
            except Exception as exc:
                # Never leave the modal busy overlay alive because a background search
                # raised outside the normal per-server error handling.
                from linkvideo_vpn_helper.services.search_service import SearchReport, ServerSearchError
                report = SearchReport(total=len(servers), checked=0)
                report.errors.append(ServerSearchError("Поиск", classify_exception(exc)))
            if not cancel_event.is_set() and self._cancel_event is cancel_event:
                self.searchReady.emit(report)

        threading.Thread(target=worker, daemon=True).start()

    def _on_progress(self, checked: int, total: int, server: str):
        if self._cancel_event is None:
            return
        # 100% is reserved for the actual completion signal.  Discovery may have
        # finished while matching client cards are still being hydrated.
        pct = min(95, int(checked * 100 / total)) if total else 0
        detail = f"Проверено {checked} из {total} · {server}"
        if total and checked >= total:
            detail = f"Проверены все {total} серверов · формирую результаты"
        self.task.busy("Ищу клиента", detail, pct)

    def _on_search(self, report):
        self._cancel_event = None
        self.btn_search.setEnabled(True)
        failed_servers = {str(x.server).lower() for x in report.errors}
        successful = max(0, int(report.checked) - len(failed_servers))
        self._active_match_count = len(report.matches)
        self._search_had_errors = bool(report.errors)

        for client in report.matches:
            self._add_result(client)

        archive_lookup = False
        if self._mode == "login" and self._deleted_lookup_query == self.query.text().strip():
            try:
                from linkvideo_vpn_helper.ui import vpn_sheets_sync_integration as integration
                coordinator = getattr(integration, "_COORDINATOR", None)
                if coordinator is not None and coordinator.is_configured():
                    archive_lookup = True
                    self._deleted_lookup_pending = True
                    coordinator.search_deleted_async(self, self._deleted_lookup_query)
            except Exception:
                archive_lookup = False

        if report.matches:
            self.task.hide()
            self.search_note.clear()
        elif report.errors:
            self.task.warning(
                "Клиент не найден на всех доступных серверах",
                f"Успешно проверено {successful}/{report.total}. {len(report.errors)} сервер(ов) проверить не удалось.",
            )
            self.search_note.setText(
                "Не удалось проверить:\n" + "\n".join(f"• {e.server}: {e.error.message}" for e in report.errors[:10])
            )
        elif archive_lookup:
            self.task.busy("Проверяю удалённые учётки", "Ищу логин в резервной базе Google Sheets…")
            self.search_note.clear()
        else:
            self.task.done("Клиент не найден", f"Проверено серверов: {successful}/{report.total}")
            self.search_note.clear()

        if self.results.count():
            self.results.setCurrentRow(-1)
            self.open_hint.setText("Клик по записи — открыть карточку")
        elif archive_lookup:
            self.open_hint.setText("Проверяю архив удалённых…")
        else:
            self.open_hint.setText("Совпадений нет")

    def _on_deleted_search(self, query: str, hits, error):
        if self._mode != "login" or str(query or "").strip() != self.query.text().strip():
            return
        self._deleted_lookup_pending = False

        if error is not None:
            if self._active_match_count == 0 and not self._search_had_errors:
                self.task.warning(
                    "Активный клиент не найден",
                    "Архив удалённых учёток сейчас проверить не удалось.",
                )
            self.search_note.setText(
                (self.search_note.text() + "\n" if self.search_note.text() else "")
                + f"Архив удалённых: {str(error)[:220]}"
            )
            return

        existing_keys: set[tuple[str, str]] = set()
        for index in range(self.results.count()):
            item = self.results.item(index)
            value = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
            server = str(getattr(value, "server", "") or "").strip().lower()
            login = str(getattr(value, "login", "") or "").strip()
            if server and login:
                existing_keys.add((server, login))

        added = 0
        for record in list(hits or []):
            key = (str(record.server or "").strip().lower(), str(record.login or "").strip())
            if key in existing_keys:
                continue
            self._add_deleted_result(record)
            existing_keys.add(key)
            added += 1

        if self.results.count():
            self.task.hide()
            self.open_hint.setText("Клик по записи — открыть карточку")
            if added and self._active_match_count:
                self.search_note.setText(f"Также найдено удалённых записей: {added}")
            elif added:
                self.search_note.setText("Найдена удалённая учётная запись — её можно восстановить.")
        elif not self._search_had_errors:
            self.task.done("Клиент не найден", "Нет ни активной, ни удалённой учётной записи с таким логином.")
            self.open_hint.setText("Совпадений нет")

    def _add_deleted_result(self, record):
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, record)
        widget = Card(subtle=True)
        widget.setObjectName("ResultCard")
        widget.setMinimumHeight(72)
        widget.setProperty("selected", "false")
        widget.setProperty("deleted", "true")
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)

        row = QHBoxLayout()
        name = QLabel(record.login)
        name.setObjectName("Value")
        state = QLabel("Удалён · можно восстановить" if record.password_saved else "Удалён · нет сохранённого пароля")
        state.setObjectName("WarningText" if record.password_saved else "DangerText")
        row.addWidget(name)
        row.addStretch(1)
        row.addWidget(state)
        layout.addLayout(row)

        detail = QLabel(
            f"{record.server} · удалена: {record.deleted_at or 'дата неизвестна'} · "
            f"Remote IP: {record.remote_address or '—'}"
        )
        detail.setObjectName("TinyMuted")
        layout.addWidget(detail)
        item.setSizeHint(QSize(0, 76))
        self.results.addItem(item)
        self.results.setItemWidget(item, widget)

    def _add_result(self, client: ClientRecord):
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, client)
        widget = Card(subtle=True)
        widget.setObjectName("ResultCard")
        widget.setMinimumHeight(66)
        widget.setProperty("selected", "false")
        l = QVBoxLayout(widget)
        l.setContentsMargins(12, 10, 12, 10)
        l.setSpacing(4)
        row = QHBoxLayout()
        name = QLabel(client.login)
        name.setObjectName("Value")
        conflict_count = sum(len(v) for v in (client.port_conflicts or {}).values())
        if conflict_count:
            state = QLabel(f"⚠ Конфликтов: {len(client.port_conflicts)}")
            state.setObjectName("WarningText")
            state.setToolTip("На сервере найдены другие TCP NAT-правила с теми же внешними портами")
        else:
            state = QLabel("Онлайн" if client.is_online else "Офлайн")
            state.setObjectName("SuccessText" if client.is_online else "Muted")
        row.addWidget(name)
        row.addStretch(1)
        row.addWidget(state)
        l.addLayout(row)
        country = self.registry.get(client.server).country
        desc = QLabel(f"{client.server} · {country} · портов: {len(client.ports)}")
        desc.setObjectName("TinyMuted")
        desc.setMinimumHeight(18)
        l.addWidget(desc)
        item.setSizeHint(QSize(0, 70))
        self.results.addItem(item)
        self.results.setItemWidget(item, widget)

    def _highlight_result(self, selected_item):
        for index in range(self.results.count()):
            item = self.results.item(index)
            widget = self.results.itemWidget(item)
            if widget is None:
                continue
            widget.setProperty("selected", "true" if item is selected_item else "false")
            widget.style().unpolish(widget)
            widget.style().polish(widget)

    def _select_result(self, item):
        if item is None:
            return
        client = item.data(Qt.ItemDataRole.UserRole)
        if not client:
            return

        from linkvideo_vpn_helper.services.vpn_restore_service import DeletedVPNClient
        self._highlight_result(item)
        if isinstance(client, DeletedVPNClient):
            self.current = None
            self._deleted_current = client
            self._open_deleted_view()
            return

        self._deleted_current = None
        changed_client = not self.current or self.current.server != client.server or self.current.login != client.login
        self.current = client
        if changed_client:
            self._recent_new_ports.clear()
        self._selected_port = client.ports[0] if client.ports else None
        self._open_client_view()

    def _open_deleted_view(self):
        record = self._deleted_current
        if record is None:
            return
        self.detail_route_title.setText(f"Удалённый клиент {record.login}")
        self.client_task.hide()
        self.detail_panel.show()
        self.search_form.hide()
        self.open_hint.setText("Выберите другую запись")
        self.results_title.setText("Найденные клиенты")
        self._animate_search_panel(compact=True)
        self._render_deleted_client()
        QTimer.singleShot(0, lambda: self.detail_scroll.verticalScrollBar().setValue(0))

    def _render_deleted_client(self):
        record = self._deleted_current
        if record is None:
            return
        self._clear_detail()

        header_row = QHBoxLayout()
        title = QLabel(f"Клиент {record.login}")
        title.setObjectName("SectionTitle")
        header_row.addWidget(title)
        header_row.addStretch(1)
        header_row.addWidget(StatusPill("Удалён", "warning"))
        self.detail_l.addLayout(header_row)

        info = Card(subtle=True)
        grid = QGridLayout(info)
        grid.setContentsMargins(14, 12, 14, 12)
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(8)
        values = (
            ("VPN-сервер", record.server),
            ("Удалён", record.deleted_at or "—"),
            ("Remote Address", record.remote_address or "—"),
            ("Profile", record.profile or "—"),
            ("NAT / Порты", record.ports or "—"),
            ("Пароль в резервной базе", "Сохранён" if record.password_saved else "НЕ СОХРАНЁН"),
        )
        for index, (label_text, value_text) in enumerate(values):
            label = QLabel(label_text)
            label.setObjectName("TinyMuted")
            value = QLabel(str(value_text))
            value.setObjectName("Value")
            value.setWordWrap(True)
            grid.addWidget(label, index, 0)
            grid.addWidget(value, index, 1)
        self.detail_l.addWidget(info)

        note = QLabel(
            "Helper восстановит тот же PPP Secret, пароль, профиль и Remote Address. "
            "Свободные старые внешние порты будут возвращены. Если старый порт уже "
            "занят другим NAT-правилом, Helper не заберёт его: подберёт новый свободный "
            "внешний порт, сохранив прежний внутренний порт назначения."
            if record.password_saved else
            "Старый пароль не был сохранён. Автоматическое восстановление заблокировано, "
            "чтобы случайно не создать клиенту другой пароль."
        )
        note.setObjectName("Muted" if record.password_saved else "DangerText")
        note.setWordWrap(True)
        self.detail_l.addWidget(note)

        actions = QHBoxLayout()
        actions.addStretch(1)
        restore = QPushButton("Восстановить клиента")
        restore.setProperty("role", "primary")
        restore.setEnabled(bool(record.password_saved) and not self._action_busy)
        restore.clicked.connect(self._restore_deleted)
        actions.addWidget(restore)
        self._deleted_restore_btn = restore
        self.detail_l.addLayout(actions)
        self.detail_l.addStretch(1)

    def _restore_deleted(self):
        record = self._deleted_current
        if record is None or self._action_busy or not record.password_saved:
            return
        dialog = ConfirmDialog(
            "Восстановить удалённого клиента?",
            f"{record.server}\nЛогин: {record.login}\nRemote Address: {record.remote_address or '—'}\n"
            f"Старые порты: {record.ports or '—'}\n\n"
            "Занятые внешние порты не будут перезаписаны — для них Helper подберёт новые свободные.",
            confirm_text="Восстановить",
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        try:
            from linkvideo_vpn_helper.ui import vpn_sheets_sync_integration as integration
            coordinator = getattr(integration, "_COORDINATOR", None)
            if coordinator is None or not coordinator.is_configured():
                raise RuntimeError("Google Sheets не настроен")
        except Exception as exc:
            self.client_task.show()
            self.client_task.error("Восстановление недоступно", str(exc))
            return

        self._action_busy = True
        if hasattr(self, "_deleted_restore_btn"):
            self._deleted_restore_btn.setEnabled(False)
        self.client_task.show()
        self.client_task.busy("Восстанавливаю клиента", f"{record.login} · {record.server}")
        coordinator.restore_deleted_async(self, record)

    def _on_deleted_restore(self, result, error):
        self._action_busy = False
        if error is not None:
            self.client_task.show()
            self.client_task.error("Восстановление не выполнено", str(error))
            if hasattr(self, "_deleted_restore_btn"):
                self._deleted_restore_btn.setEnabled(True)
            return

        self.client_task.show()
        replacements = dict(getattr(result, "port_replacements", {}) or {})
        if replacements:
            replacement_text = ", ".join(
                f"{old} → {new}" for old, new in sorted(replacements.items())
            )
            self.client_task.done(
                "Клиент восстановлен · порты заменены",
                f"{result.login} · {result.server} · {replacement_text}",
            )
        else:
            self.client_task.done(
                "Клиент восстановлен",
                f"{result.login} · {result.server} · старые порты свободны и восстановлены",
            )
        try:
            from linkvideo_vpn_helper.ui import vpn_sheets_sync_integration as integration
            coordinator = getattr(integration, "_COORDINATOR", None)
            if coordinator is not None:
                coordinator.notify_mutation(
                    result.server,
                    "восстановление клиента из резервной базы",
                    result.login,
                )
        except Exception as exc:
            self.search_note.setText(
                "Клиент восстановлен, но автоматическую синхронизацию базы "
                f"не удалось запустить: {str(exc)[:180]}"
            )

        self._deleted_current = None
        QTimer.singleShot(
            3000 if dict(getattr(result, "port_replacements", {}) or {}) else 1200,
            lambda: (
                self._close_client_view(immediate=True),
                self._search(),
            ),
        )

    def _clear_detail(self):
        while self.detail_l.count():
            item = self.detail_l.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
            elif item.layout():
                self._clear_nested(item.layout())

    def _clear_nested(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
            elif item.layout():
                self._clear_nested(item.layout())

    def _render_empty(self):
        self._clear_detail()
        self.detail_l.addWidget(EmptyState("Клиент не выбран", "Вернитесь к результатам поиска и выберите учётную запись.", "⌕"), 1)

    def _render_client(self):
        c = self.current
        if not c:
            self._render_empty()
            return
        self._clear_detail()

        # Карточка намеренно повторяет компоновку 1.1.2: заголовок, две
        # информационные карточки в одной строке, отдельный список портов и
        # плотный блок управления ниже. Визуальный язык остаётся новым.
        header_row = QHBoxLayout()
        header = QLabel(f"Клиент {c.login}")
        header.setObjectName("SectionTitle")
        header_row.addWidget(header)
        header_row.addStretch(1)
        live = StatusPill("В сети" if c.is_online else "Не в сети", "success" if c.is_online else "neutral")
        header_row.addWidget(live)
        self.detail_l.addLayout(header_row)

        if self._recent_new_ports:
            banner = Card(kind="success")
            bl = QHBoxLayout(banner)
            bl.setContentsMargins(12, 9, 12, 9)
            ports_text = ", ".join(str(x) for x in sorted(self._recent_new_ports))
            text = QLabel(f"Новые порты: {ports_text}")
            text.setObjectName("SuccessText")
            copy_new = QPushButton("Копировать новые порты")
            copy_new.clicked.connect(lambda _=False, value=ports_text, btn=copy_new: self._copy_new_ports(value, btn))
            bl.addWidget(text, 1)
            bl.addWidget(copy_new)
            self.detail_l.addWidget(banner)

        top = QHBoxLayout()
        top.setSpacing(10)

        data = Card()
        dl = QVBoxLayout(data)
        dl.setContentsMargins(12, 12, 12, 12)
        dl.setSpacing(8)
        dt = QLabel("Данные клиента")
        dt.setObjectName("SectionTitle")
        main_info = QLabel(
            f"Сервер: {c.server}\n"
            f"Логин: {c.login}\n"
            f"Пароль: {c.password}\n"
            f"Remote Address: {c.remote_address or '—'}\n"
            f"Порты: {', '.join(str(x) for x in c.ports) if c.ports else '—'}"
        )
        main_info.setObjectName("Value")
        main_info.setWordWrap(True)
        main_info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        main_info.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        dl.addWidget(dt)
        dl.addWidget(main_info, 1)

        vpn = Card()
        vl = QVBoxLayout(vpn)
        vl.setContentsMargins(12, 12, 12, 12)
        vl.setSpacing(8)
        vt = QLabel("Состояние VPN")
        vt.setObjectName("SectionTitle")
        lines = [f"Статус VPN: {'Онлайн' if c.is_online else 'Не в сети'}"]
        if not c.is_enabled:
            lines.append("Учётка выключена")
        if c.is_online:
            if c.tx_rate:
                lines.append(f"Отправлено: {self._fmt_rate(c.tx_rate)}")
            if c.rx_rate:
                lines.append(f"Получено: {self._fmt_rate(c.rx_rate)}")
            if c.uptime:
                lines.append(f"Uptime: {c.uptime}")
            lines.extend(["", f"Всего отправлено: {self._fmt_bytes(c.tx_bytes)}", f"Всего получено: {self._fmt_bytes(c.rx_bytes)}"])
        else:
            lines.extend(["", f"Последнее отключение VPN: {self._fmt_router_datetime(c.last_logged_out)}"])
        diag = QLabel("\n".join(lines))
        diag.setObjectName("Value")
        diag.setWordWrap(True)
        diag.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        diag.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        vl.addWidget(vt)
        vl.addWidget(diag, 1)

        data.setMinimumWidth(340)
        vpn.setMinimumWidth(250)
        top.addWidget(data, 4)
        top.addWidget(vpn, 2)
        self.detail_l.addLayout(top)

        if c.port_conflicts:
            conflict_card = Card()
            conflict_card.setObjectName("ConflictCard")
            conflict_layout = QVBoxLayout(conflict_card)
            conflict_layout.setContentsMargins(12, 10, 12, 10)
            conflict_layout.setSpacing(5)
            conflict_title = QLabel("⚠ Конфликт внешних NAT-портов")
            conflict_title.setObjectName("WarningText")
            conflict_layout.addWidget(conflict_title)
            lines = []
            for port in sorted(c.port_conflicts):
                owners = []
                for conflict in c.port_conflicts.get(port, []):
                    owner = conflict.owner_text()
                    suffix = " (правило отключено)" if conflict.disabled else ""
                    label = owner + suffix
                    if label not in owners:
                        owners.append(label)
                lines.append(f"Порт {port} также используется: {', '.join(owners) or 'другим NAT-правилом'}")
            conflict_text = QLabel("\n".join(lines))
            conflict_text.setWordWrap(True)
            conflict_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            conflict_layout.addWidget(conflict_text)
            conflict_note = QLabel(
                "На одном VPN-сервере внешний TCP-порт должен принадлежать одной учётке. "
                "При одинаковом dst-port порядок NAT-правил может направлять трафик не тому клиенту."
            )
            conflict_note.setObjectName("TinyMuted")
            conflict_note.setWordWrap(True)
            conflict_layout.addWidget(conflict_note)
            self.detail_l.addWidget(conflict_card)

        ports = Card()
        pl = QVBoxLayout(ports)
        pl.setContentsMargins(12, 12, 12, 12)
        pl.setSpacing(8)
        port_head = QHBoxLayout()
        ptitle = QLabel("Порты клиента")
        ptitle.setObjectName("SectionTitle")
        self.selected_port_pill = StatusPill("Порт не выбран", "neutral")
        port_head.addWidget(ptitle)
        port_head.addStretch(1)
        port_head.addWidget(self.selected_port_pill)
        pl.addLayout(port_head)
        self.port_list = QListWidget()
        self.port_list.setObjectName("PortList")
        self.port_list.setMinimumHeight(160)
        self.port_list.setMaximumHeight(230)
        self.port_list.setSpacing(4)
        self.port_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.port_list.currentItemChanged.connect(self._port_selected)
        if c.ports:
            for port in c.ports:
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, int(port))
                conflict_rows = (c.port_conflicts or {}).get(int(port), [])
                conflict_owners = []
                for conflict in conflict_rows:
                    owner = conflict.owner_text()
                    if owner not in conflict_owners:
                        conflict_owners.append(owner)
                if conflict_rows:
                    owner_text = ", ".join(conflict_owners[:2])
                    if len(conflict_owners) > 2:
                        owner_text += f" +{len(conflict_owners) - 2}"
                    item.setText(f"⚠ Порт {port} — также: {owner_text or 'другое правило'}")
                    item.setToolTip("Конфликт NAT: этот внешний TCP-порт встречается в другой учётке на том же сервере")
                    item.setForeground(QColor("#D97706"))
                else:
                    item.setText(f"Порт {port}" + ("    • новый" if int(port) in self._recent_new_ports else ""))
                item.setSizeHint(QSize(0, 42))
                self.port_list.addItem(item)
            desired = self._selected_port if self._selected_port in c.ports else c.ports[0]
            for i in range(self.port_list.count()):
                if int(self.port_list.item(i).data(Qt.ItemDataRole.UserRole)) == int(desired):
                    self.port_list.setCurrentRow(i)
                    break
        else:
            item = QListWidgetItem("NAT-порты не найдены")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            item.setSizeHint(QSize(0, 42))
            self.port_list.addItem(item)
        pl.addWidget(self.port_list, 1)
        self.detail_l.addWidget(ports)

        control = Card()
        cl = QVBoxLayout(control)
        cl.setContentsMargins(12, 12, 12, 12)
        cl.setSpacing(8)
        ct = QLabel("Управление")
        ct.setObjectName("SectionTitle")
        cl.addWidget(ct)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)

        self.copy_data_button = QPushButton("Копировать данные")
        self.copy_data_button.setProperty("role", "primary")
        self.copy_data_button.clicked.connect(self._copy)
        refresh = QPushButton("Обновить статус")
        refresh.clicked.connect(self._refresh)
        monitor = QPushButton("Открыть мониторинг")
        monitor.clicked.connect(self._monitor)
        grid.addWidget(self.copy_data_button, 0, 0)
        grid.addWidget(refresh, 0, 1)
        grid.addWidget(monitor, 0, 2)

        self.add_count = CounterControl(1, 1, 14)
        add = QPushButton("Добавить порты")
        add.setProperty("role", "primary")
        add.clicked.connect(self._add_ports)
        grid.addWidget(self.add_count, 1, 0)
        grid.addWidget(add, 1, 1, 1, 2)

        self.toggle_port_button = QPushButton("Выберите порт")
        self.toggle_port_button.clicked.connect(self._toggle_selected_port)
        self.remove_port_button = QPushButton("Удалить порт")
        self.remove_port_button.clicked.connect(self._remove_selected_port)
        grid.addWidget(self.toggle_port_button, 2, 0, 1, 2)
        grid.addWidget(self.remove_port_button, 2, 2)

        self.password_edit = QLineEdit()
        self.password_edit.setPlaceholderText("Новый пароль или пусто для генерации")
        change = QPushButton("Сменить пароль")
        change.clicked.connect(self._password_inline)
        grid.addWidget(self.password_edit, 3, 0, 1, 2)
        grid.addWidget(change, 3, 2)

        account_toggle = QPushButton("Вкл./выкл. учётку")
        account_toggle.clicked.connect(self._toggle_account)
        delete = QPushButton("Удалить клиента")
        delete.setProperty("role", "danger")
        delete.clicked.connect(self._delete)
        grid.addWidget(account_toggle, 4, 0, 1, 2)
        grid.addWidget(delete, 4, 2)

        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)
        cl.addLayout(grid)
        self.detail_l.addWidget(control)
        self._sync_selected_port_controls()

    def _plain_data_line(self, name: str, value: str, suffix: str = "") -> QWidget:
        w = QWidget()
        l = QHBoxLayout(w)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(8)
        label = QLabel(f"{name}:")
        label.setObjectName("Muted")
        val = QLabel(value or "—")
        val.setObjectName("Value")
        val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        l.addWidget(label)
        l.addWidget(val, 1)
        if suffix:
            country = QLabel(suffix)
            country.setObjectName("Muted")
            l.addWidget(country)
        return w

    def _port_selected(self, current, previous):
        if current is None:
            self._selected_port = None
            self._sync_selected_port_controls()
            return
        value = current.data(Qt.ItemDataRole.UserRole)
        self._selected_port = int(value) if value is not None else None
        self._sync_selected_port_controls()

    def _selected_port_text(self) -> str:
        return f"Выбран порт {self._selected_port}" if self._selected_port else "Порт не выбран"

    def _sync_selected_port_controls(self):
        port = self._selected_port
        if hasattr(self, "selected_port_pill"):
            has_conflict = bool(port and self.current and (self.current.port_conflicts or {}).get(int(port)))
            if has_conflict:
                self.selected_port_pill.set_status(f"⚠ Конфликт порта {port}", "warning")
            else:
                self.selected_port_pill.set_status(self._selected_port_text(), "info" if port else "neutral")
        if hasattr(self, "remove_port_button"):
            self.remove_port_button.setEnabled(bool(port))
            self.remove_port_button.setText(f"Удалить порт {port}" if port else "Удалить порт")
        if hasattr(self, "toggle_port_button"):
            self.toggle_port_button.setEnabled(bool(port))
            if port and self.current:
                enabled = port not in set(int(x) for x in self.current.disabled_ports)
                self.toggle_port_button.setText(("Отключить порт " if enabled else "Включить порт ") + str(port))
            else:
                self.toggle_port_button.setText("Выберите порт")

    def _add_ports(self):
        if not self.current:
            return
        count = self.add_count.value() if hasattr(self, "add_count") else 1
        c = self.current
        self._pending_old_ports = set(int(x) for x in c.ports)
        self._run_action("add_ports", lambda: self.service.add_ports(c.server, self.credentials, c.login, count))

    def _password_inline(self):
        if not self.current:
            return
        value = self.password_edit.text().strip() if hasattr(self, "password_edit") else ""
        if not value:
            alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
            value = "".join(random.choice(alphabet) for _ in range(8))
        c = self.current
        password = value
        self._run_action("password", lambda: self.service.set_password(c.server, self.credentials, c.login, password))

    def _toggle_selected_port(self):
        c = self.current
        port = self._selected_port
        if not c or not port:
            self.task.show()
            self.task.warning("Порт не выбран", "Выберите порт в списке выше.")
            return
        enabled = port not in c.disabled_ports
        self._run_action("port_toggle", lambda: self.service.set_port_enabled(c.server, self.credentials, c.login, port, not enabled))

    def _remove_selected_port(self):
        c = self.current
        port = self._selected_port
        if not c or not port:
            self.task.show()
            self.task.warning("Порт не выбран", "Выберите порт в списке выше.")
            return
        dialog = ConfirmDialog("Удалить порт", f"Удалить NAT-порт {port} у клиента {c.login}?", "Удалить", True, self)
        if dialog.exec():
            self._run_action("remove_port", lambda: self.service.remove_port(c.server, self.credentials, c.login, port))

    def _toggle_account(self):
        c = self.current
        if not c:
            return
        self._run_action("secret", lambda: self.service.set_secret_enabled(c.server, self.credentials, c.login, not c.is_enabled))

    def _disconnect(self):
        c = self.current
        if not c:
            return
        dialog = ConfirmDialog("Отключить VPN-сессию", f"Принудительно завершить текущую VPN-сессию {c.login}?", "Отключить", False, self)
        if not dialog.exec():
            return

        def operation():
            self.service.disconnect_client_session(c.server, self.credentials, c.login)
            time.sleep(0.4)
            return self.service.get_client(c.server, self.credentials, c.login)

        self._run_action("disconnect", operation)

    def _delete(self):
        c = self.current
        if not c:
            return
        dialog = ConfirmDialog(
            "Удалить клиента",
            f"Будут удалены PPP Secret, профиль и NAT-правила клиента {c.login}. Это действие нельзя отменить.",
            "Удалить клиента",
            True,
            self,
        )
        if not dialog.exec():
            return

        def operation():
            self.service.delete_client(c.server, self.credentials, c.login)
            return None

        self._run_action("delete", operation)

    def _refresh(self):
        c = self.current
        if c:
            self._run_action("refresh", lambda: self.service.get_client(c.server, self.credentials, c.login, include_port_conflicts=True))

    def _copy(self):
        if self.current:
            QGuiApplication.clipboard().setText(self.current.copy_text())
            if hasattr(self, "copy_data_button"):
                button_feedback(self.copy_data_button, "✓ Скопировано")

    def _monitor(self):
        if self.current:
            TrafficDialog(self.service, self.credentials, self.current, self).exec()

    def _run_action(self, name: str, fn):
        if not self.current:
            return
        self._action_busy = True
        self.btn_search.setEnabled(False)
        action_task = self.client_task if hasattr(self, "client_task") else self.task
        action_task.show()
        action_task.busy("Выполняю действие", self.current.login)

        def worker():
            try:
                self.actionReady.emit(fn(), None, name)
            except Exception as exc:
                self.actionReady.emit(None, classify_exception(exc), name)

        threading.Thread(target=worker, daemon=True).start()

    def _on_action(self, result, error, name: str):
        self._action_busy = False
        self.btn_search.setEnabled(True)
        action_task = self.client_task if hasattr(self, "client_task") else self.task
        if error:
            action_task.show()
            action_task.error("Операция не выполнена", getattr(error, "message", None) or str(error))
            return
        if name == "delete":
            deleted = self.current
            current_row = self.results.currentRow()
            remove_row = -1
            for index in range(self.results.count()):
                item = self.results.item(index)
                client = item.data(Qt.ItemDataRole.UserRole)
                if client and deleted and client.server == deleted.server and client.login == deleted.login:
                    remove_row = index
                    break
            if remove_row >= 0:
                self.results.takeItem(remove_row)
            # Остальные найденные результаты остаются на месте; карточка удалённой
            # учётки просто закрывается, новый клиент автоматически не открывается.
            self.current = None
            self._selected_port = None
            action_task.show()
            action_task.done("Клиент удалён", "PPP-учётная запись и связанные NAT-правила удалены.")
            self._close_client_view(immediate=True)
            self.results.setCurrentRow(-1)
            self._highlight_result(None)
            return
        if isinstance(result, ClientRecord):
            if name == "add_ports":
                self._recent_new_ports = set(int(x) for x in result.ports) - set(self._pending_old_ports)
                if self._recent_new_ports:
                    self._selected_port = sorted(self._recent_new_ports)[0]
            elif name == "remove_port":
                self._recent_new_ports.intersection_update(int(x) for x in result.ports)
            self.current = result
            if self._selected_port not in self.current.ports:
                self._selected_port = self.current.ports[0] if self.current.ports else None
            self._sync_result_record(self.current)
        messages = {
            "add_ports": "Порты добавлены",
            "password": "Пароль изменён",
            "port_toggle": "Состояние порта изменено",
            "remove_port": "Порт удалён",
            "secret": "Состояние учётной записи изменено",
            "disconnect": "VPN-сессия отключена",
            "refresh": "Состояние клиента обновлено",
        }
        action_task.show()
        action_task.done("Готово", messages.get(name, "Состояние клиента обновлено"))
        self._render_client()
        # RouterOS может обновить динамические поля с небольшой задержкой.
        # Дополнительная тихая синхронизация убирает необходимость вручную
        # нажимать «Обновить» после добавления/удаления/переключения портов.
        QTimer.singleShot(700, self._silent_refresh)

    def _copy_new_ports(self, value: str, button: QPushButton):
        QGuiApplication.clipboard().setText(value)
        button_feedback(button, "✓ Новые порты скопированы")

    def _sync_result_record(self, client: ClientRecord):
        """Keep search results and the detail card in sync after mutations."""
        for index in range(self.results.count()):
            item = self.results.item(index)
            old = item.data(Qt.ItemDataRole.UserRole)
            if old and old.server == client.server and old.login == client.login:
                item.setData(Qt.ItemDataRole.UserRole, client)
                widget = self.results.itemWidget(item)
                if widget is not None:
                    labels = widget.findChildren(QLabel)
                    # Result card has login, state and description labels in that order.
                    for label in labels:
                        if label.objectName() in {"SuccessText", "Muted"} and label.text() in {"Онлайн", "Офлайн"}:
                            label.setText("Онлайн" if client.is_online else "Офлайн")
                            label.setObjectName("SuccessText" if client.is_online else "Muted")
                            label.style().unpolish(label); label.style().polish(label)
                    if labels:
                        country = self.registry.get(client.server).country
                        desc_text = f"{client.server} · {country} · портов: {len(client.ports)}"
                        for label in labels:
                            if label.objectName() == "TinyMuted":
                                label.setText(desc_text)
                                break
                break

    @staticmethod
    def _signature(client: ClientRecord | None):
        if client is None:
            return None
        return (
            client.server, client.login, bool(client.is_online), bool(client.is_enabled),
            tuple(int(x) for x in client.ports), tuple(int(x) for x in client.disabled_ports),
            str(client.last_logged_out or ""), int(client.rx_bytes or 0), int(client.tx_bytes or 0),
            tuple((int(port), tuple(x.owner_text() for x in rows)) for port, rows in sorted((client.port_conflicts or {}).items())),
        )

    def _silent_refresh(self):
        c = self.current
        if not c or self._action_busy or self._cancel_event is not None or not self.isVisible():
            return
        server, login = c.server, c.login
        before = self._signature(c)
        def worker():
            try:
                fresh = self.service.get_client(server, self.credentials, login)
            except Exception:
                return
            if fresh is not None:
                self.liveReady.emit((fresh, before))
        threading.Thread(target=worker, daemon=True).start()

    def _on_live_refresh(self, payload):
        if not payload:
            return
        fresh, before = payload
        if not self.current or fresh.server != self.current.server or fresh.login != self.current.login:
            return
        # Тихое обновление каждые 15 секунд остаётся лёгким и не сканирует NAT.
        # Последняя подтверждённая информация о конфликтах сохраняется до ручного
        # обновления карточки или повторного поиска.
        if not fresh.port_conflicts and self.current.port_conflicts:
            fresh.port_conflicts = self.current.port_conflicts
        if self._signature(fresh) == before:
            return
        self.current = fresh
        self._recent_new_ports.intersection_update(int(x) for x in fresh.ports)
        if self._selected_port not in fresh.ports:
            self._selected_port = fresh.ports[0] if fresh.ports else None
        self._sync_result_record(fresh)
        if hasattr(self, "detail_panel") and self.detail_panel.isVisible():
            self._render_client()

    def onActivated(self):
        self._live_timer.start()
        QTimer.singleShot(250, self._silent_refresh)

    def onDeactivated(self):
        self._live_timer.stop()
        self._close_client_view(immediate=True)

    def refresh_servers(self):
        self.server_picker.refresh()

    @staticmethod
    def _fmt_router_datetime(value: str) -> str:
        raw = str(value or "").strip()
        if not raw or raw.lower() in {"never", "none", "—", "jan/01/1970 00:00:00", "1970-01-01 00:00:00"}:
            return "Нет данных"
        months = {
            "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
            "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
        }
        m = re.match(r"^([A-Za-z]{3})/(\d{1,2})/(\d{4})\s+(\d{1,2}:\d{2}:\d{2})$", raw)
        if m and m.group(1).lower() in months:
            return f"{int(m.group(2)):02d}/{months[m.group(1).lower()]:02d}/{int(m.group(3)) % 100:02d} {m.group(4)}"
        m = re.match(r"^(\d{4})-(\d{2})-(\d{2})[T\s]+(\d{2}:\d{2}:\d{2})", raw)
        if m:
            if m.group(1) == "1970":
                return "Нет данных"
            return f"{m.group(3)}/{m.group(2)}/{int(m.group(1)) % 100:02d} {m.group(4)}"
        m = re.match(r"^(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})\s+(\d{1,2}:\d{2}:\d{2})$", raw)
        if m:
            return f"{int(m.group(1)):02d}/{int(m.group(2)):02d}/{int(m.group(3)) % 100:02d} {m.group(4)}"
        return raw

    @staticmethod
    def _fmt_bytes(value: int) -> str:
        n = float(value or 0)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if n < 1024 or unit == "TB":
                return f"{n:.1f} {unit}"
            n /= 1024

    @staticmethod
    def _fmt_rate(value: float | int) -> str:
        bps = max(0.0, float(value or 0))
        if bps >= 1_000_000:
            return f"{bps / 1_000_000:.1f} Mbps"
        if bps >= 1_000:
            return f"{bps / 1_000:.1f} kbps"
        return f"{bps:.0f} bps"
