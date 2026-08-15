from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from PySide6.QtCore import Qt, QUrl, Signal, QTimer
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QGridLayout, QHeaderView, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget, QAbstractItemView,
)

from linkvideo_vpn_helper.services.errors import classify_exception
from linkvideo_vpn_helper.services.server_registry import ServerRegistry
from linkvideo_vpn_helper.services.vpn_automation_service import (
    AutomationStatus, SeedResult, VPNAutomationService, LV_AUTOMATION_VERSION,
)
from linkvideo_vpn_helper.services.vpn_backup_service import VPNBackupBatchResult, VPNBackupService, VPNServerBackupResult
from linkvideo_vpn_helper.services.vpn_service import (
    SessionCredentials, VPNService, VPN_L2TP_HARD_LIMIT, VPN_L2TP_SOFT_LIMIT,
    VPN_L2TP_WARNING_LIMIT,
)
from linkvideo_vpn_helper.ui.components import Card, StatusPill, TaskStatus, MetricCard, EmptyState, build_page_scaffold
from linkvideo_vpn_helper.ui.dialogs import ConfirmDialog


class ServerTableWidget(QTableWidget):
    """Таблица со своей прокруткой, не передающая колесо внешней странице."""
    def wheelEvent(self, event):
        bar = self.verticalScrollBar()
        delta = event.angleDelta().y()
        if delta:
            steps = max(1, abs(delta) // 120)
            direction = -1 if delta > 0 else 1
            amount = max(36, bar.singleStep() * 3) * steps
            bar.setValue(bar.value() + direction * amount)
        event.accept()


class VPNServersPage(QWidget):
    statsReady = Signal(object)
    backupReady = Signal(object, object)
    backupProgress = Signal(int, int, str)
    actionReady = Signal(str, object, object)
    actionProgress = Signal(int, int, str)

    def __init__(self, service: VPNService, credentials: SessionCredentials, registry: ServerRegistry, parent=None):
        super().__init__(parent)
        self.service = service
        self.credentials = credentials
        self.registry = registry
        self.backup_service = VPNBackupService()
        self.automation = VPNAutomationService()
        self._busy = False
        self._cancel_event: threading.Event | None = None
        self._last_backup_folder = None
        self._automation_by_host: dict[str, AutomationStatus] = {}
        self._stats_by_host: dict[str, object] = {}
        self._selected_host = ""
        self._refresh_silent_mode = False
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(20000)
        self._refresh_timer.timeout.connect(lambda: self.refresh(silent=True))
        self.statsReady.connect(self._on_stats)
        self.backupReady.connect(self._on_backup)
        self.backupProgress.connect(self._on_backup_progress)
        self.actionReady.connect(self._on_action)
        self.actionProgress.connect(self._on_action_progress)
        self._build()

    def _build(self):
        self.page_scroll, self.page_canvas, root = build_page_scaffold(
            self, max_width=1420, min_width=820, margins=22, spacing=12
        )
        self.page_layout = root

        header = Card(kind="hero")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(18, 16, 18, 16)
        hl.setSpacing(16)
        text = QVBoxLayout()
        text.setSpacing(4)
        title = QLabel("VPN-серверы")
        title.setObjectName("SectionTitle")
        hint = QLabel(
            "Состояние vpn*.linkvideo.ru, резервные копии и управление автономной LV-автоматикой. "
            f"Новые клиенты автоматически не назначаются на серверы с {VPN_L2TP_SOFT_LIMIT}+ активными L2TP."
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        text.addWidget(title)
        text.addWidget(hint)
        self.refresh_btn = QPushButton("Обновить данные")
        self.refresh_btn.setProperty("role", "primary")
        self.refresh_btn.clicked.connect(self.refresh)
        hl.addLayout(text, 1)
        hl.addWidget(self.refresh_btn, 0, Qt.AlignmentFlag.AlignTop)
        root.addWidget(header)

        self.task = TaskStatus()
        self.task.hide()
        root.addWidget(self.task)

        metrics = QHBoxLayout()
        metrics.setSpacing(10)
        self.m_available = MetricCard("Доступно", "—")
        self.m_active = MetricCard("Активные L2TP", "—")
        self.m_installed = MetricCard("LV установлено", "—")
        self.m_quarantine = MetricCard("Карантин / архив", "—")
        for item in (self.m_available, self.m_active, self.m_installed, self.m_quarantine):
            metrics.addWidget(item, 1)
        root.addLayout(metrics)

        actions_card = Card(subtle=True)
        al = QVBoxLayout(actions_card)
        al.setContentsMargins(16, 13, 16, 13)
        al.setSpacing(9)
        at = QHBoxLayout()
        label = QLabel("Действия со всеми серверами")
        label.setObjectName("Value")
        self.policy = StatusPill(
            f"Распределение: < {VPN_L2TP_SOFT_LIMIT} · тревога {VPN_L2TP_WARNING_LIMIT} · предел {VPN_L2TP_HARD_LIMIT}",
            "neutral",
        )
        at.addWidget(label)
        at.addStretch(1)
        at.addWidget(self.policy)
        al.addLayout(at)
        action_grid = QGridLayout()
        action_grid.setHorizontalSpacing(8)
        action_grid.setVerticalSpacing(8)
        self.backup_btn = QPushButton("Выгрузить все серверы")
        self.backup_btn.clicked.connect(self._backup_all)
        self.install_all_btn = QPushButton("Установить/обновить LV на всех")
        self.install_all_btn.clicked.connect(self._install_all)
        self.start_all_btn = QPushButton("Запустить LV на всех")
        self.start_all_btn.clicked.connect(lambda: self._set_all_automation(True))
        self.stop_all_btn = QPushButton("Остановить LV на всех")
        self.stop_all_btn.setProperty("role", "danger")
        self.stop_all_btn.clicked.connect(lambda: self._set_all_automation(False))
        action_grid.addWidget(self.backup_btn, 0, 0)
        action_grid.addWidget(self.install_all_btn, 0, 1)
        action_grid.addWidget(self.start_all_btn, 1, 0)
        action_grid.addWidget(self.stop_all_btn, 1, 1)
        action_grid.setColumnStretch(0, 1)
        action_grid.setColumnStretch(1, 1)
        al.addLayout(action_grid)
        root.addWidget(actions_card)

        legend = Card(subtle=True)
        ll = QVBoxLayout(legend)
        ll.setContentsMargins(16, 12, 16, 12)
        ll.setSpacing(7)
        legend_title = QLabel("Памятка состояний VPN-клиентов")
        legend_title.setObjectName("Value")
        ll.addWidget(legend_title)
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(5)
        items = [
            ("Активная", "активность менее 30 дней"),
            ("Спящая — 30+ дней", "30–89 дней без связи"),
            ("Карантин — 90+ дней", "90–364 дня; может восстановиться автоматически"),
            ("Кандидат в архив — 365+ дней", "365+ дней; автоматически не удаляется"),
            ("Отключена вручную", "автовосстановление запрещено"),
            ("Активность неизвестна", "автоматика не отключает такую учётку"),
        ]
        for i, (name, desc) in enumerate(items):
            w = QLabel(f"<b>{name}</b> — {desc}")
            w.setObjectName("TinyMuted")
            w.setWordWrap(True)
            grid.addWidget(w, i // 3, i % 3)
        for c in range(3):
            grid.setColumnStretch(c, 1)
        ll.addLayout(grid)
        root.addWidget(legend)

        table_card = Card()
        tl = QVBoxLayout(table_card)
        tl.setContentsMargins(14, 14, 14, 14)
        tl.setSpacing(10)
        table_head = QHBoxLayout()
        table_title = QLabel("Серверы")
        table_title.setObjectName("SectionTitle")
        self.summary = QLabel("Нажмите «Обновить данные», чтобы получить состояние серверов.")
        self.summary.setObjectName("TinyMuted")
        table_head.addWidget(table_title)
        table_head.addStretch(1)
        table_head.addWidget(self.summary)
        tl.addLayout(table_head)

        self.table = ServerTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels([
            "VPN-сервер", "Страна", "Активные L2TP", "Учётки", "CPU", "RAM", "NAT", "LV-автоматика"
        ])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(False)
        self.table.verticalHeader().setVisible(False)
        self.table.itemSelectionChanged.connect(self._table_selection_changed)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for i in range(1, 8):
            hh.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        # Серверы прокручиваются внутри фиксированной области. Колесо мыши
        # над таблицей больше не двигает всю страницу VPN-серверов.
        self.table.setFixedHeight(360)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        tl.addWidget(self.table)
        root.addWidget(table_card)

        self.detail_card = Card()
        self.detail_layout = QVBoxLayout(self.detail_card)
        self.detail_layout.setContentsMargins(16, 14, 16, 14)
        self.detail_layout.setSpacing(10)
        self._render_server_detail("")
        root.addWidget(self.detail_card)

        foot = QLabel(
            "Безопасный порядок: резервная копия → установка LV → инициализация активности → наблюдение → "
            "включение карантина только после проверки. Полная резервная копия содержит PPP-пароли."
        )
        foot.setObjectName("TinyMuted")
        foot.setWordWrap(True)
        root.addWidget(foot)
        root.addStretch(1)

    def showEvent(self, event):
        super().showEvent(event)
        if self.table.rowCount() == 0 and not self._busy:
            self.refresh()

    def onActivated(self):
        self._refresh_timer.start()
        if self.table.rowCount() == 0 and not self._busy:
            self.refresh()

    def onDeactivated(self):
        self._refresh_timer.stop()

    def refresh_servers(self):
        if self.isVisible() and not self._busy:
            self.refresh()

    def cancel_current_action(self) -> bool:
        event = self._cancel_event
        if event is None or event.is_set():
            return False
        event.set()
        self._cancel_event = None
        self._set_busy(False)
        self.task.show()
        self.task.warning("Операция остановлена", "Операция отменена клавишей Esc.")
        return True

    def _set_busy(self, value: bool):
        self._busy = value
        for button in (self.refresh_btn, self.backup_btn, self.install_all_btn, self.start_all_btn, self.stop_all_btn):
            button.setEnabled(not value)

    def refresh(self, silent: bool = False):
        if self._busy:
            return
        servers = self.registry.hosts()
        if not servers:
            self.task.show(); self.task.warning("Нет VPN-серверов", "Включите серверы в настройках.")
            return
        self._set_busy(True)
        self._refresh_silent_mode = bool(silent)
        if not silent:
            self.task.show(); self.task.busy("Проверяю VPN-серверы", f"Серверов: {len(servers)}")

        def one(host: str):
            stat = self.service.analyze_server_quick(host, self.credentials)
            auto = self.automation.get_status(host, self.credentials)
            return stat, auto

        def worker():
            rows = []
            with ThreadPoolExecutor(max_workers=min(8, len(servers)), thread_name_prefix="vpn-dashboard") as pool:
                futures = {pool.submit(one, host): host for host in servers}
                for future in as_completed(futures):
                    host = futures[future]
                    try:
                        stat, auto = future.result()
                        rows.append((host, stat, auto, None))
                    except Exception as exc:
                        rows.append((host, None, None, classify_exception(exc).message))
            self.statsReady.emit(rows)
        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def _state(active: int) -> tuple[str, str]:
        if active >= VPN_L2TP_HARD_LIMIT:
            return "ПРЕДЕЛ ПРЕВЫШЕН", "danger"
        if active >= VPN_L2TP_WARNING_LIMIT:
            return "КРИТИЧЕСКАЯ НАГРУЗКА", "danger"
        if active >= VPN_L2TP_SOFT_LIMIT:
            return "ВЫСОКАЯ НАГРУЗКА", "warning"
        if active >= 400:
            return "ПОВЫШЕННАЯ НАГРУЗКА", "warning"
        return "НОРМА", "success"

    def _on_stats(self, rows):
        self._set_busy(False)
        order = {host: i for i, host in enumerate(self.registry.hosts())}
        rows = sorted(rows, key=lambda item: order.get(item[0], 9999))
        previous = self._selected_host
        self.table.setRowCount(len(rows))
        self._automation_by_host.clear()
        self._stats_by_host.clear()
        active_total = 0
        ok_count = 0
        quarantine_total = 0
        installed = 0
        for r, (host, stat, auto, error) in enumerate(rows):
            country = self.registry.get(host).country
            self._stats_by_host[host] = stat
            if stat is None:
                values = [host, country, "—", "—", "—", "—", "—", "Ошибка"]
            else:
                active = int(stat.clients_online)
                active_total += active
                ok_count += 1
                if isinstance(auto, AutomationStatus):
                    self._automation_by_host[host] = auto
                    installed += int(auto.installed)
                    quarantine_total += auto.quarantine + auto.archive
                    auto_text = auto.state_text
                    if auto.installed and auto.aging_enabled and not auto.paused:
                        auto_text += " · карантин включён"
                else:
                    auto_text = "—"
                values = [
                    host, country, f"{active} / {VPN_L2TP_HARD_LIMIT}", str(stat.clients_total),
                    "—" if stat.cpu_load is None else f"{stat.cpu_load}%",
                    "—" if stat.memory_usage_percent is None else f"{stat.memory_usage_percent}%",
                    str(stat.ports_total), auto_text,
                ]
            for c, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, host)
                if c in (2, 3, 4, 5, 6):
                    item.setTextAlignment(int(Qt.AlignmentFlag.AlignCenter))
                if error and c == 0:
                    item.setToolTip(error)
                self.table.setItem(r, c, item)

        self.m_available.setValue(f"{ok_count} / {len(rows)}")
        self.m_active.setValue(str(active_total))
        self.m_installed.setValue(f"{installed} / {ok_count}")
        self.m_quarantine.setValue(str(quarantine_total))
        self.summary.setText(f"Обновлено серверов: {ok_count}/{len(rows)}")

        target = previous if previous in self._stats_by_host else (rows[0][0] if rows else "")
        if target:
            for r in range(self.table.rowCount()):
                item = self.table.item(r, 0)
                if item and item.text() == target:
                    self.table.selectRow(r)
                    break
            self._selected_host = target
            self._render_server_detail(target)
        else:
            self._selected_host = ""
            self._render_server_detail("")

        errors = [x for x in rows if x[3]]
        if errors and not self._refresh_silent_mode:
            self.task.show(); self.task.warning("Статистика получена частично", f"Не ответили: {len(errors)} сервер(а/ов).")
        elif not self._refresh_silent_mode:
            self.task.show(); self.task.done("VPN-серверы обновлены", f"Получена статистика {ok_count} серверов.")
        self._refresh_silent_mode = False

    def _table_selection_changed(self):
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return
        item = self.table.item(rows[0].row(), 0)
        if not item:
            return
        self._selected_host = item.text()
        self._render_server_detail(self._selected_host)

    def _clear_detail(self):
        while self.detail_layout.count():
            item = self.detail_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
            elif item.layout():
                while item.layout().count():
                    sub = item.layout().takeAt(0)
                    if sub.widget():
                        sub.widget().deleteLater()

    def _render_server_detail(self, host: str):
        self._clear_detail()
        if not host:
            empty = EmptyState("VPN-сервер не выбран", "Выберите сервер в таблице выше.", "▦")
            empty.setMaximumHeight(180)
            self.detail_layout.addWidget(empty)
            return
        stat = self._stats_by_host.get(host)
        auto = self._automation_by_host.get(host)
        top = QHBoxLayout()
        name = QLabel(host)
        name.setObjectName("SectionTitle")
        top.addWidget(name)
        top.addStretch(1)
        if stat is not None:
            state, kind = self._state(int(stat.clients_online))
            top.addWidget(StatusPill(state, kind))
        top.addWidget(StatusPill(auto.state_text if auto else "LV: нет данных", "success" if auto and auto.installed and not auto.paused else "neutral"))
        self.detail_layout.addLayout(top)

        counts = QHBoxLayout()
        counts.setSpacing(8)
        values = [
            ("Активные", auto.active if auto else 0),
            ("Спящие", auto.sleeping if auto else 0),
            ("Карантин", auto.quarantine if auto else 0),
            ("Архив", auto.archive if auto else 0),
            ("Вручную", auto.manual if auto else 0),
            ("Неизвестно", auto.unknown if auto else 0),
        ]
        for label, value in values:
            counts.addWidget(MetricCard(label, str(value)), 1)
        self.detail_layout.addLayout(counts)

        if auto and auto.installed:
            runtime_info = QLabel(
                f"Запуски Scheduler: активность {auto.activity_run_count} · автовосстановление {auto.restore_run_count} · карантин {auto.aging_run_count}"
            )
            runtime_info.setObjectName("TinyMuted")
            self.detail_layout.addWidget(runtime_info)

        actions = QGridLayout()
        actions.setHorizontalSpacing(8)
        actions.setVerticalSpacing(8)
        backup_one = QPushButton("Выгрузить этот сервер")
        backup_one.clicked.connect(lambda _=False, h=host: self._backup_one(h))
        install = QPushButton("Обновить LV" if auto and auto.installed else "Установить LV")
        install.clicked.connect(lambda _=False, h=host: self._install_one(h))
        seed = QPushButton("Инициализировать активность")
        seed.setEnabled(bool(auto and auto.installed))
        seed.clicked.connect(lambda _=False, h=host: self._seed_one(h))
        runtime = QPushButton("Запустить LV" if auto and auto.paused else "Остановить LV")
        # «Запустить LV» is also a repair action for older partial installs:
        # set_automation_enabled() recreates missing schedulers/logging before start.
        runtime.setEnabled(bool(auto and (auto.installed or auto.scripts_ready)))
        if auto and auto.installed and not auto.paused:
            runtime.setProperty("role", "danger")
        runtime.clicked.connect(lambda _=False, h=host: self._toggle_automation(h))
        quarantine = QPushButton("Выключить карантин" if auto and auto.aging_enabled else "Включить карантин")
        quarantine.setEnabled(bool(auto and auto.installed and not auto.paused))
        quarantine.clicked.connect(lambda _=False, h=host: self._toggle_quarantine(h))
        actions.addWidget(backup_one, 0, 0, 1, 2)
        actions.addWidget(install, 1, 0)
        actions.addWidget(seed, 1, 1)
        actions.addWidget(runtime, 2, 0)
        actions.addWidget(quarantine, 2, 1)
        actions.setColumnStretch(0, 1)
        actions.setColumnStretch(1, 1)
        self.detail_layout.addLayout(actions)

    def _set_action_widget(self, row: int, host: str, auto: AutomationStatus | None):
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(5)
        install = QPushButton("Обновить LV" if auto and auto.installed else "Установить LV")
        install.setToolTip("Перед изменением автоматически создаётся полная резервная копия этого VPN-сервера.")
        install.clicked.connect(lambda _=False, h=host: self._install_one(h))

        seed = QPushButton("Инициализировать активность")
        seed.setToolTip("Заполнить состояние и дату последней активности без отключения клиентов.")
        seed.setEnabled(bool(auto and auto.installed))
        seed.clicked.connect(lambda _=False, h=host: self._seed_one(h))

        runtime = QPushButton("Запустить LV" if auto and auto.paused else "Остановить LV")
        runtime.setToolTip(
            "Запускает/останавливает все служебные задачи LV. При остановке скрипты остаются установленными, "
            "учётные записи клиентов не изменяются."
        )
        # «Запустить LV» is also a repair action for older partial installs:
        # set_automation_enabled() recreates missing schedulers/logging before start.
        runtime.setEnabled(bool(auto and (auto.installed or auto.scripts_ready)))
        runtime.setProperty("role", "default" if auto and auto.paused else "danger")
        runtime.clicked.connect(lambda _=False, h=host: self._toggle_automation(h))

        quarantine = QPushButton("Выключить карантин" if auto and auto.aging_enabled else "Включить карантин")
        quarantine.setEnabled(bool(auto and auto.installed and not auto.paused))
        quarantine.setProperty("role", "danger" if auto and auto.aging_enabled else "default")
        quarantine.clicked.connect(lambda _=False, h=host: self._toggle_quarantine(h))

        lay.addWidget(install)
        lay.addWidget(seed)
        lay.addWidget(runtime)
        lay.addWidget(quarantine)
        self.table.setCellWidget(row, 10, box)

    def _backup_one(self, host: str):
        dialog = ConfirmDialog(
            "Выгрузить конфигурацию VPN-сервера?",
            f"Будет создана полная резервная копия {host}: PPP Secret вместе с паролями, профили, NAT/firewall, IP-пулы, маршруты и служебные скрипты.\n\n"
            "Копия сохраняется локально в Documents\\LinkVideo.Helper\\VPN_Backups и содержит конфиденциальные данные.",
            "Выгрузить сервер",
            False,
            self,
        )
        if not dialog.exec():
            return
        self._run_mutation(
            "backup_one", host,
            lambda h: self.backup_service.backup_one(h, self.credentials, prefix="MANUAL"),
        )

    def _install_one(self, host: str):
        dialog = ConfirmDialog(
            "Установить LV-автоматику?",
            f"Перед изменением будет создана полная резервная копия {host}. Затем Helper установит/обновит "
            f"LV-Activity, LV-AutoRestore и LV-Aging {LV_AUTOMATION_VERSION}.\n\n"
            "Автоматический карантин НЕ будет включён автоматически.",
            "Создать копию и установить",
            False,
            self,
        )
        if not dialog.exec():
            return
        self._run_mutation("install", host, self._install_with_backup)

    def _install_with_backup(self, host: str):
        backup = self.backup_service.backup_one(host, self.credentials)
        if not backup.ok:
            raise RuntimeError("Полная резервная копия перед установкой LV не создана")
        status = self.automation.install_or_update(host, self.credentials)
        return {"status": status, "backup": str(backup.json_path.parent if backup.json_path else "")}

    def _seed_one(self, host: str):
        dialog = ConfirmDialog(
            "Инициализировать последнюю активность?",
            f"Helper проставит служебные состояния PPP Secret на {host}, используя текущие активные сессии и "
            "доступный last-logged-out. Учётки НЕ будут включаться или отключаться.\n\n"
            "Существующие вручную отключённые учётки будут отмечены как «Отключена вручную» и не смогут восстановиться автоматически.",
            "Заполнить активность",
            False,
            self,
        )
        if not dialog.exec():
            return
        self._run_mutation("seed", host, lambda h: self.automation.seed_lifecycle(h, self.credentials))

    def _toggle_automation(self, host: str):
        auto = self._automation_by_host.get(host)
        if not auto or not (auto.installed or auto.scripts_ready):
            self.task.show(); self.task.warning("LV-автоматика не установлена", "Сначала установите LV на сервер.")
            return
        # Partial legacy installs are treated as stopped. The service will repair
        # missing Scheduler/logging components before enabling runtime.
        target = auto.paused if auto.installed else True
        if target:
            title = "Запустить LV-автоматику"
            text = (
                f"Запустить служебные задачи LV на {host}?\n\n"
                "Будут включены сбор активности и автовосстановление. Состояние автокарантина, "
                "которое было до остановки, будет восстановлено."
            )
            button, danger = "Запустить", False
        else:
            title = "Остановить LV-автоматику"
            text = (
                f"Остановить все служебные задачи LV на {host}?\n\n"
                "Будут отключены планировщики сбора активности, автокарантина и автовосстановления, "
                "а также служебное логирование. Скрипты останутся установленными. PPP Secret и их текущие "
                "состояния НЕ изменятся. После запуска прежнее состояние карантина восстановится."
            )
            button, danger = "Остановить", True
        dialog = ConfirmDialog(title, text, button, danger, self)
        if not dialog.exec():
            return
        self._run_mutation(
            "automation_on" if target else "automation_off",
            host,
            lambda h: self.automation.set_automation_enabled(h, self.credentials, target),
        )

    def _toggle_quarantine(self, host: str):
        auto = self._automation_by_host.get(host)
        if not auto or not auto.installed:
            self.task.show(); self.task.warning("LV-автоматика не установлена", "Сначала установите LV на сервер.")
            return
        target = not auto.aging_enabled
        if target and auto.initialized <= 0:
            self.task.show(); self.task.warning(
                "Последняя активность не инициализирована",
                "Сначала нажмите «Инициализировать активность». Карантин нельзя включать без служебных состояний.",
            )
            return
        if target:
            text = (
                f"Включить ежедневный LV-Aging на {host}?\n\n"
                f"Сейчас: активные {auto.active}, спящие {auto.sleeping}, карантин {auto.quarantine}, "
                f"архив {auto.archive}, отключены вручную {auto.manual}, активность неизвестна {auto.unknown}.\n\n"
                "После следующего запуска учётки в состояниях «Карантин» и «Кандидат в архив» будут отключены. "
                "Ручные отключения и записи с неизвестной активностью автоматика не изменяет."
            )
            title, button, danger = "Включить карантин", "Включить", True
        else:
            text = (
                f"Выключить автоматический карантин на {host}? Уже отключённые учётки в состояниях «Карантин» "
                "и «Кандидат в архив» останутся отключёнными, но новые учётки автоматически отключаться не будут."
            )
            title, button, danger = "Выключить карантин", "Остановить", False
        dialog = ConfirmDialog(title, text, button, danger, self)
        if not dialog.exec():
            return
        self._run_mutation(
            "quarantine_on" if target else "quarantine_off",
            host,
            lambda h: self.automation.set_quarantine_enabled(h, self.credentials, target),
        )

    def _install_all(self):
        servers = self.registry.hosts()
        if not servers:
            return
        dialog = ConfirmDialog(
            "Установить LV-автоматику на всех VPN?",
            f"Для каждого из {len(servers)} серверов Helper сначала создаст отдельную полную резервную копию, "
            "а затем установит/обновит LV-автоматику.\n\n"
            "Автоматический карантин нигде сам не включается. Операция может занять несколько минут.",
            "Создать копии и установить",
            False,
            self,
        )
        if not dialog.exec():
            return
        cancel = threading.Event()
        self._cancel_event = cancel
        self._set_busy(True)
        self._bulk_action_title = "Устанавливаю LV-автоматику"
        self.task.show(); self.task.busy(self._bulk_action_title, f"0 из {len(servers)}", 0)

        def worker():
            ok = []
            errors = []
            for idx, host in enumerate(servers, 1):
                if cancel.is_set():
                    return
                self.actionProgress.emit(idx - 1, len(servers), host)
                try:
                    self._install_with_backup(host)
                    ok.append(host)
                except Exception as exc:
                    errors.append((host, str(exc)))
                self.actionProgress.emit(idx, len(servers), host)
            if not cancel.is_set():
                self.actionReady.emit("install_all", {"ok": ok, "errors": errors}, None)
        threading.Thread(target=worker, daemon=True).start()

    def _set_all_automation(self, enabled: bool):
        servers = [host for host in self.registry.hosts() if self._automation_by_host.get(host) and self._automation_by_host[host].installed]
        if not servers:
            self.task.show(); self.task.warning("Нет установленной LV-автоматики", "Сначала установите LV хотя бы на один VPN-сервер.")
            return
        if enabled:
            title = "Запустить LV на всех серверах?"
            description = (
                f"Будут запущены служебные задачи на {len(servers)} сервер(ах). Для каждого сервера восстановится "
                "состояние автокарантина, которое было до остановки."
            )
            button, danger = "Запустить на всех", False
        else:
            title = "Остановить LV на всех серверах?"
            description = (
                f"Будут остановлены служебные задачи LV на {len(servers)} сервер(ах). Скрипты останутся установленными, "
                "PPP Secret и состояния клиентов не изменятся."
            )
            button, danger = "Остановить на всех", True
        dialog = ConfirmDialog(title, description, button, danger, self)
        if not dialog.exec():
            return
        cancel = threading.Event()
        self._cancel_event = cancel
        self._set_busy(True)
        self._bulk_action_title = "Запускаю LV-автоматику" if enabled else "Останавливаю LV-автоматику"
        self.task.show(); self.task.busy(self._bulk_action_title, f"0 из {len(servers)}", 0)

        def worker():
            ok, errors = [], []
            for idx, host in enumerate(servers, 1):
                if cancel.is_set():
                    return
                self.actionProgress.emit(idx - 1, len(servers), host)
                try:
                    self.automation.set_automation_enabled(host, self.credentials, enabled)
                    ok.append(host)
                except Exception as exc:
                    errors.append((host, str(exc)))
                self.actionProgress.emit(idx, len(servers), host)
            if not cancel.is_set():
                self.actionReady.emit("automation_on_all" if enabled else "automation_off_all", {"ok": ok, "errors": errors}, None)

        threading.Thread(target=worker, daemon=True).start()

    def _run_mutation(self, name: str, host: str, fn):
        self._set_busy(True)
        self.task.show(); self.task.busy("Изменяю VPN-сервер", host)

        def worker():
            try:
                self.actionReady.emit(name, {"host": host, "result": fn(host)}, None)
            except Exception as exc:
                self.actionReady.emit(name, {"host": host}, classify_exception(exc))
        threading.Thread(target=worker, daemon=True).start()

    def _on_action_progress(self, done: int, total: int, host: str):
        pct = int(done / max(1, total) * 100)
        self.task.busy(getattr(self, "_bulk_action_title", "Изменяю LV-автоматику"), f"{done} из {total} · {host}", pct)

    def _on_action(self, name: str, payload, error):
        self._cancel_event = None
        self._set_busy(False)
        if error:
            self.task.error("Операция не выполнена", getattr(error, "message", None) or str(error))
            return
        # Одиночные операции возвращают уже повторно проверенный AutomationStatus.
        # Обновляем выбранный сервер сразу, не ждём следующего фонового refresh.
        if isinstance(payload, dict):
            host = str(payload.get("host") or "")
            result = payload.get("result")
            if host and isinstance(result, AutomationStatus):
                self._automation_by_host[host] = result
                if host == self._selected_host:
                    self._render_server_detail(host)

        if name in {"install_all", "automation_on_all", "automation_off_all"}:
            ok = list((payload or {}).get("ok") or [])
            errors = list((payload or {}).get("errors") or [])
            if name == "install_all":
                title = "LV-автоматика установлена"
                detail = f"Обновлены {len(ok)} серверов. Автокарантин не включался автоматически."
            elif name == "automation_on_all":
                title = "LV-автоматика запущена"
                detail = f"Запущено серверов: {len(ok)}."
            else:
                title = "LV-автоматика остановлена"
                detail = f"Остановлено серверов: {len(ok)}. Учётные записи клиентов не изменялись."
            if errors:
                self.task.warning(title + " частично", f"Успешно {len(ok)}, ошибок {len(errors)}.")
            else:
                self.task.done(title, detail)
        elif name == "backup_one":
            result = (payload or {}).get("result")
            if isinstance(result, VPNServerBackupResult) and result.ok:
                folder = result.json_path.parent if result.json_path else None
                self._last_backup_folder = folder
                self.task.done("Резервная копия сервера создана", f"{result.server}: {folder}")
                if folder:
                    try:
                        os.startfile(str(folder))
                    except Exception:
                        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
            else:
                self.task.warning("Резервная копия создана частично", "Проверьте ошибки выгрузки выбранного сервера.")
        elif name == "seed":
            result = (payload or {}).get("result")
            if isinstance(result, SeedResult):
                self.task.done(
                    "Последняя активность инициализирована",
                    f"{result.server}: изменено {result.changed}/{result.total}; активные {result.active}, спящие {result.sleeping}, "
                    f"карантин {result.quarantine}, архив {result.archive}, вручную {result.manual}, неизвестно {result.unknown}. Никто не отключён.",
                )
        elif name.startswith("quarantine"):
            self.task.done("Политика обновлена", "Автокарантин включён." if name.endswith("on") else "Автокарантин выключен.")
        elif name.startswith("automation_"):
            self.task.done("LV-автоматика запущена" if name.endswith("on") else "LV-автоматика остановлена",
                           "Служебные задачи работают." if name.endswith("on") else "Скрипты сохранены на сервере, но Scheduler-задачи остановлены.")
        else:
            result = (payload or {}).get("result") or {}
            backup = result.get("backup") if isinstance(result, dict) else ""
            self.task.done("LV-автоматика установлена", f"Автокарантин не включён автоматически. Резервная копия: {backup}" if backup else "Автокарантин не включён автоматически.")
        self.refresh()

    def _backup_all(self):
        servers = self.registry.hosts()
        if not servers:
            return
        dialog = ConfirmDialog(
            "Создать полную резервную копию всех VPN?",
            "Будут выгружены PPP Secret ВМЕСТЕ С ПАРОЛЯМИ, профили, NAT/firewall, IP-пулы, адреса, маршруты и служебные скрипты каждого сервера.\n\n"
            "Файлы сохранятся локально в Documents\\LinkVideo.Helper\\VPN_Backups. Папка содержит конфиденциальные данные.",
            "Создать резервный снимок",
            False,
            self,
        )
        if not dialog.exec():
            return
        cancel_event = threading.Event()
        self._cancel_event = cancel_event
        self._set_busy(True)
        self.task.show(); self.task.busy("Выгружаю конфигурации VPN", f"0 из {len(servers)}", 0)

        def worker():
            try:
                result = self.backup_service.backup_all(
                    servers, self.credentials,
                    lambda done, total, host: self.backupProgress.emit(done, total, host),
                    cancel_event,
                )
                if not cancel_event.is_set():
                    self.backupReady.emit(result, None)
            except Exception as exc:
                if not cancel_event.is_set():
                    self.backupReady.emit(None, exc)
        threading.Thread(target=worker, daemon=True).start()

    def _on_backup_progress(self, done: int, total: int, host: str):
        pct = int(done / max(1, total) * 100)
        self.task.busy("Выгружаю конфигурации VPN", f"{done} из {total} · {host}", pct)

    def _on_backup(self, result, error):
        self._cancel_event = None
        self._set_busy(False)
        if error:
            self.task.error("Резервный снимок не создан", str(error))
            return
        if not isinstance(result, VPNBackupBatchResult):
            return
        self._last_backup_folder = result.folder
        if result.failure_count:
            self.task.warning(
                "Резервная копия создана частично",
                f"Сохранено {result.success_count}/{len(result.servers)} серверов. Папка: {result.folder}",
            )
        else:
            self.task.done("Полная резервная копия создана", f"Сохранено {result.success_count} серверов. Папка: {result.folder}")
        try:
            os.startfile(str(result.folder))
        except Exception:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(result.folder)))
