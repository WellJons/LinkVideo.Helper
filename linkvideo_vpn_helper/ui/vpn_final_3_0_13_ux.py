from __future__ import annotations

"""Final 3.0.13 VPN UX and event-driven sync adjustments.

This module is intentionally installed last, after the older compatibility
layers. It removes the remaining modal search overlay bug, simplifies the two
VPN-facing screens and replaces the five-minute full RouterOS polling loop with
change-triggered reconciliation.
"""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from linkvideo_vpn_helper.services.routeros_change_listener import RouterOSChangeListener
from linkvideo_vpn_helper.ui.components import Card, EmptyState, MetricCard, StatusPill, TaskStatus, build_page_scaffold


_INSTALLED = False


def _install_search_fixes() -> None:
    from linkvideo_vpn_helper.ui.pages.search_manage_page import SearchManagePage

    if getattr(SearchManagePage, "_lv_3013_final_search", False):
        return

    original_deleted_search = SearchManagePage._on_deleted_search

    def deleted_search(self, query: str, hits, error):
        # ``TaskStatus.busy`` is a floating BusyDialog. ``self.task.hide()`` only
        # hides the inline card and therefore used to leave that dialog forever
        # above a successfully found archived account.
        if self._mode == "login" and str(query or "").strip() == self.query.text().strip():
            close_busy = getattr(self.task, "_close_busy_dialog", None)
            if callable(close_busy):
                close_busy()
        return original_deleted_search(self, query, hits, error)

    def render_deleted_client(self):
        record = self._deleted_current
        if record is None:
            return
        self._clear_detail()

        header_row = QHBoxLayout()
        title = QLabel(record.login)
        title.setObjectName("SectionTitle")
        header_row.addWidget(title)
        header_row.addStretch(1)
        header_row.addWidget(StatusPill("Удалён", "warning"))
        self.detail_l.addLayout(header_row)

        info = Card(subtle=True)
        grid = QGridLayout(info)
        grid.setContentsMargins(14, 12, 14, 12)
        grid.setHorizontalSpacing(22)
        grid.setVerticalSpacing(9)

        values = [
            ("VPN-сервер", record.server or "—"),
            ("Remote IP", record.remote_address or "—"),
            ("Порты", record.ports or "—"),
            ("Удалён", record.deleted_at or "—"),
        ]
        for index, (label_text, value_text) in enumerate(values):
            label = QLabel(label_text)
            label.setObjectName("TinyMuted")
            value = QLabel(str(value_text))
            value.setObjectName("Value")
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(label, index, 0)
            grid.addWidget(value, index, 1)
        self.detail_l.addWidget(info)

        if not record.password_saved:
            warning = QLabel("В резервной копии нет пароля — автоматическое восстановление недоступно.")
            warning.setObjectName("DangerText")
            warning.setWordWrap(True)
            self.detail_l.addWidget(warning)

        actions = QHBoxLayout()
        actions.addStretch(1)
        restore = QPushButton("Восстановить")
        restore.setProperty("role", "primary")
        restore.setMinimumWidth(150)
        restore.setEnabled(bool(record.password_saved) and not self._action_busy)
        restore.clicked.connect(self._restore_deleted)
        actions.addWidget(restore)
        self._deleted_restore_btn = restore
        self.detail_l.addLayout(actions)
        self.detail_l.addStretch(1)

    SearchManagePage._on_deleted_search = deleted_search
    SearchManagePage._render_deleted_client = render_deleted_client
    SearchManagePage._lv_3013_final_search = True


def _install_clean_vpn_servers_layout() -> None:
    from linkvideo_vpn_helper.services.vpn_service import VPN_L2TP_HARD_LIMIT
    from linkvideo_vpn_helper.ui.pages.vpn_servers_page import ServerTableWidget, VPNServersPage

    if getattr(VPNServersPage, "_lv_3013_clean_layout", False):
        return

    def clean_build(self):
        self.page_scroll, self.page_canvas, root = build_page_scaffold(
            self, max_width=1420, min_width=820, margins=22, spacing=12
        )
        self.page_layout = root

        header = Card(kind="hero")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(18, 16, 18, 16)
        hl.setSpacing(14)
        text = QVBoxLayout()
        text.setSpacing(3)
        title = QLabel("VPN-серверы")
        title.setObjectName("SectionTitle")
        hint = QLabel("Состояние серверов и LV-автоматики. Выберите сервер в таблице — действия появятся ниже.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        text.addWidget(title)
        text.addWidget(hint)
        self.refresh_btn = QPushButton("Обновить")
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
        self.m_installed = MetricCard("LV работает", "—")
        self.m_quarantine = MetricCard("Карантин", "—")
        for item in (self.m_available, self.m_active, self.m_installed, self.m_quarantine):
            metrics.addWidget(item, 1)
        root.addLayout(metrics)

        table_card = Card()
        tl = QVBoxLayout(table_card)
        tl.setContentsMargins(14, 14, 14, 14)
        tl.setSpacing(9)
        table_head = QHBoxLayout()
        table_title = QLabel("Серверы")
        table_title.setObjectName("SectionTitle")
        self.summary = QLabel("Получаю состояние серверов…")
        self.summary.setObjectName("TinyMuted")
        table_head.addWidget(table_title)
        table_head.addStretch(1)
        table_head.addWidget(self.summary)
        tl.addLayout(table_head)

        self.table = ServerTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels([
            "VPN-сервер", "Страна", "Онлайн", "Учётки", "CPU", "RAM", "NAT", "LV"
        ])
        self.table.setEditTriggers(self.table.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(self.table.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(False)
        self.table.verticalHeader().setVisible(False)
        self.table.itemSelectionChanged.connect(self._table_selection_changed)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for index in range(1, 8):
            hh.setSectionResizeMode(index, QHeaderView.ResizeMode.ResizeToContents)
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

        # Potentially destructive fleet-wide actions are intentionally below the
        # selected-server controls instead of dominating the top of the page.
        batch = Card(subtle=True)
        bl = QVBoxLayout(batch)
        bl.setContentsMargins(16, 12, 16, 12)
        bl.setSpacing(8)
        batch_title = QLabel("Массовые действия")
        batch_title.setObjectName("Value")
        batch_hint = QLabel("Используйте только когда изменение действительно нужно применить ко всем VPN-серверам.")
        batch_hint.setObjectName("TinyMuted")
        batch_hint.setWordWrap(True)
        bl.addWidget(batch_title)
        bl.addWidget(batch_hint)
        actions = QGridLayout()
        actions.setHorizontalSpacing(8)
        actions.setVerticalSpacing(8)
        self.backup_btn = QPushButton("Резервная копия всех")
        self.backup_btn.clicked.connect(self._backup_all)
        self.install_all_btn = QPushButton("Обновить LV на всех")
        self.install_all_btn.clicked.connect(self._install_all)
        self.start_all_btn = QPushButton("Запустить LV на всех")
        self.start_all_btn.clicked.connect(lambda: self._set_all_automation(True))
        self.stop_all_btn = QPushButton("Остановить LV на всех")
        self.stop_all_btn.setProperty("role", "danger")
        self.stop_all_btn.clicked.connect(lambda: self._set_all_automation(False))
        actions.addWidget(self.backup_btn, 0, 0)
        actions.addWidget(self.install_all_btn, 0, 1)
        actions.addWidget(self.start_all_btn, 1, 0)
        actions.addWidget(self.stop_all_btn, 1, 1)
        actions.setColumnStretch(0, 1)
        actions.setColumnStretch(1, 1)
        bl.addLayout(actions)
        root.addWidget(batch)

        # Kept for compatibility with older methods that update this pill.
        self.policy = StatusPill(f"Предел L2TP: {VPN_L2TP_HARD_LIMIT}", "neutral")
        self.policy.hide()
        root.addWidget(self.policy)
        root.addStretch(1)

    VPNServersPage._build = clean_build
    VPNServersPage._lv_3013_clean_layout = True


def _install_event_driven_sheets() -> None:
    import linkvideo_vpn_helper.ui.vpn_sheets_sync_integration as integration

    coordinator_cls = integration.VPNSyncCoordinator
    if getattr(coordinator_cls, "_lv_3013_event_sync", False):
        return

    original_init = coordinator_cls.__init__
    original_sync_all = coordinator_cls.sync_all

    def _ensure_listeners(self):
        if not self.is_configured():
            return
        listeners = getattr(self, "_lv_change_listeners", None)
        if listeners is None:
            listeners = {}
            self._lv_change_listeners = listeners

        wanted = {str(host or "").strip() for host in self.registry.hosts() if str(host or "").strip()}
        for host in list(listeners):
            if host in wanted:
                continue
            listeners.pop(host).stop()

        def changed(server: str, path: str, values: dict[str, str]):
            login = str(values.get("name", "") or "").strip()
            self.mutationRequested.emit(server, f"событие RouterOS: {path.rsplit('/', 1)[-1]}", login)

        for host in sorted(wanted):
            if host in listeners:
                continue
            listener = RouterOSChangeListener(host, self.credentials, changed)
            listeners[host] = listener
            listener.start()

    def patched_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        # No more full 12-server polling every five minutes. RouterOS listen
        # events and Helper mutation callbacks trigger reconciliation instead.
        try:
            self._periodic.stop()
        except Exception:
            pass
        self._lv_change_listeners = {}
        QTimer.singleShot(1500, lambda: _ensure_listeners(self))
        # One startup baseline is still necessary after an application upgrade or
        # a period when Helper was not running. It is not a recurring poll.
        QTimer.singleShot(12_000, lambda: self.sync_all(manual=False) if self.is_configured() else None)

    def no_periodic_sync(self):
        return

    def sync_all(self, manual: bool = True):
        _ensure_listeners(self)
        return original_sync_all(self, manual=manual)

    coordinator_cls.__init__ = patched_init
    coordinator_cls._periodic_sync = no_periodic_sync
    coordinator_cls.sync_all = sync_all
    coordinator_cls._lv_ensure_event_listeners = _ensure_listeners
    coordinator_cls._lv_3013_event_sync = True

    # Replace the old database card before attach_vpn_sheets_sync() invokes the
    # hook. The card now describes the actual event-driven behaviour and keeps
    # progress out of the button label.
    def patch_vpn_servers_page() -> None:
        if integration._PATCHED_PAGE:
            return
        from linkvideo_vpn_helper.ui.pages.vpn_servers_page import VPNServersPage

        original_build = VPNServersPage._build

        def patched_build(self):
            original_build(self)
            coordinator = integration._COORDINATOR
            if coordinator is None:
                return

            card = Card(subtle=True)
            layout = QHBoxLayout(card)
            layout.setContentsMargins(16, 12, 16, 12)
            layout.setSpacing(12)
            text = QVBoxLayout()
            text.setSpacing(3)
            title = QLabel("VPN-база")
            title.setObjectName("Value")
            self.sheets_sync_status = QLabel()
            self.sheets_sync_status.setObjectName("TinyMuted")
            self.sheets_sync_status.setWordWrap(True)
            if coordinator.is_configured():
                self.sheets_sync_status.setText("Изменения RouterOS и Helper сохраняются по событиям. Полная сверка — вручную.")
            else:
                self.sheets_sync_status.setText("Google Sheets не настроен")
            text.addWidget(title)
            text.addWidget(self.sheets_sync_status)

            self.sheets_restore_btn = QPushButton("Восстановить")
            self.sheets_restore_btn.setEnabled(coordinator.is_configured())
            self.sheets_sync_btn = QPushButton("Сверить сейчас")
            self.sheets_sync_btn.setProperty("role", "primary")
            self.sheets_sync_btn.clicked.connect(lambda: coordinator.sync_all(manual=True))

            def open_restore_dialog():
                if not coordinator.is_configured() or coordinator.backend is None:
                    self.sheets_sync_status.setText(coordinator.config_hint())
                    return
                from linkvideo_vpn_helper.services.vpn_restore_service import VPNRestoreService
                from linkvideo_vpn_helper.ui.vpn_restore_dialog import VPNRestoreDialog

                dialog = VPNRestoreDialog(
                    VPNRestoreService(coordinator.vpn_service, coordinator.backend),
                    coordinator.credentials,
                    list(coordinator.registry.hosts()),
                    on_restored=lambda server, login: coordinator.notify_mutation(
                        server, "восстановление клиента из резервной базы", login
                    ),
                    parent=self,
                )
                dialog.exec()

            self.sheets_restore_btn.clicked.connect(open_restore_dialog)
            layout.addLayout(text, 1)
            layout.addWidget(self.sheets_restore_btn, 0, Qt.AlignmentFlag.AlignVCenter)
            layout.addWidget(self.sheets_sync_btn, 0, Qt.AlignmentFlag.AlignVCenter)
            self.page_layout.insertWidget(2, card)

            def started(total: int):
                self.sheets_sync_btn.setEnabled(False)
                self.sheets_sync_btn.setText("Сверяю…")
                self.sheets_sync_status.setText(f"Полная сверка · 0 из {total}")

            def progress(done: int, total: int, host: str, detail: str):
                self.sheets_sync_status.setText(f"Полная сверка · {done} из {total} · {host}")

            def finished(ok: int, failed: int):
                self.sheets_sync_btn.setEnabled(True)
                self.sheets_sync_btn.setText("Сверить сейчас")
                if failed:
                    self.sheets_sync_status.setText(f"Сверка завершена частично · успешно {ok} · ошибок {failed}")
                else:
                    self.sheets_sync_status.setText("База актуальна · дальнейшие изменения сохраняются по событиям")

            def missing(message: str):
                self.sheets_sync_btn.setEnabled(True)
                self.sheets_sync_btn.setText("Сверить сейчас")
                self.sheets_restore_btn.setEnabled(False)
                self.sheets_sync_status.setText(message)

            coordinator.syncStarted.connect(started)
            coordinator.syncProgress.connect(progress)
            coordinator.syncFinished.connect(finished)
            coordinator.syncConfigMissing.connect(missing)

        VPNServersPage._build = patched_build
        integration._PATCHED_PAGE = True

    integration._patch_vpn_servers_page = patch_vpn_servers_page


def install_vpn_final_3_0_13_ux() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _install_search_fixes()
    _install_clean_vpn_servers_layout()
    _install_event_driven_sheets()
    _INSTALLED = True
