from __future__ import annotations

import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QBoxLayout, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from linkvideo_vpn_helper.services.errors import classify_exception
from linkvideo_vpn_helper.services.server_registry import ServerRegistry
from linkvideo_vpn_helper.services.vpn_service import SessionCredentials, VPNService
from linkvideo_vpn_helper.ui.components import (
    Card, PageHeader, StatusPill, Switch, TaskStatus, build_page_scaffold,
)
from linkvideo_vpn_helper.ui.dialogs import AddServerDialog, ConfirmDialog
from linkvideo_vpn_helper.version import APP_VERSION
from linkvideo_vpn_helper.theme import theme_names, normalize_theme


class SettingsPage(QWidget):
    themeChanged = Signal(str)
    updateRequested = Signal()
    serversChanged = Signal()
    testReady = Signal(str, object, object)

    def __init__(
        self,
        settings,
        registry: ServerRegistry,
        service: VPNService | None = None,
        credentials: SessionCredentials | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.settings = settings
        self.registry = registry
        self.service = service
        self.credentials = credentials
        self.testReady.connect(self._on_test_ready)
        self._testing_host = ""
        self._build()

    def _build(self):
        self.page_scroll, canvas, root = build_page_scaffold(
            self, max_width=1360, min_width=760, margins=22, spacing=12
        )
        self.page_canvas = canvas
        self.page_layout = root


        # Настройки теперь используют ту же desktop-workbench компоновку, что и
        # остальные страницы Helper: не узкая колонка по центру, а широкая
        # рабочая область с умеренными полями по краям.
        self.header = PageHeader(
            "Настройки",
            "Внешний вид, список VPN-серверов и параметры архива. Пользовательские серверы сохраняются после обновления Helper.",
        )
        root.addWidget(self.header)

        self.top_settings = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.top_settings.setSpacing(12)

        appearance = Card()
        al = QVBoxLayout(appearance)
        al.setContentsMargins(20, 18, 20, 18)
        al.setSpacing(11)
        title = QLabel("Внешний вид")
        title.setObjectName("SectionTitle")
        hint = QLabel("Все темы используют один современный интерфейс LinkVideo, но отличаются собственными акцентами, поверхностями и характером. Семантика успеха, предупреждений и ошибок остаётся одинаковой.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        al.addWidget(title)
        al.addWidget(hint)

        current = normalize_theme(str(self.settings.value("ui/theme_v2", "ocean_blue", str) or "ocean_blue"))
        self._theme_buttons = {}
        self.theme_grid = QGridLayout()
        self.theme_grid.setHorizontalSpacing(8)
        self.theme_grid.setVerticalSpacing(8)
        for key, name in theme_names():
            btn = QPushButton(name)
            btn.setProperty("themeChoice", "true")
            btn.setProperty("active", "true" if key == current else "false")
            btn.clicked.connect(lambda checked=False, k=key: self._theme_changed(k))
            self._theme_buttons[key] = btn
        al.addLayout(self.theme_grid)
        al.addStretch(1)
        self.appearance_card = appearance

        archive = Card()
        ar = QVBoxLayout(archive)
        ar.setContentsMargins(20, 18, 20, 18)
        ar.setSpacing(11)
        at = QLabel("Архив")
        at.setObjectName("SectionTitle")
        ah = QLabel("Для архива достаточно выбрать страну. Внутренние ID B2O Helper использует сам и в интерфейсе не показывает.")
        ah.setObjectName("Muted")
        ah.setWordWrap(True)
        ar.addWidget(at)
        ar.addWidget(ah)

        for country, prefix, cluster in (
            ("Россия", "linkvideo_", "linkvideo"),
            ("Казахстан", "linkvideokz_", "linkvideokz"),
            ("Беларусь", "linkvideoby_", "linkvideoby"),
        ):
            row = QHBoxLayout()
            row.setSpacing(12)
            left = QLabel(country)
            left.setObjectName("Value")
            middle = QLabel(f"Камеры {prefix}*")
            middle.setObjectName("Muted")
            right = QLabel(f"Кластер {cluster}")
            right.setObjectName("TinyMuted")
            right.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            row.addWidget(left)
            row.addWidget(middle)
            row.addStretch(1)
            row.addWidget(right)
            ar.addLayout(row)

        archive_note = QLabel("Время запроса архива вводится во времени камеры. Переезды reserve-transfers сопоставляются через UTC+7 автоматически.")
        archive_note.setObjectName("TinyMuted")
        archive_note.setWordWrap(True)
        ar.addWidget(archive_note)
        ar.addStretch(1)
        self.archive_card = archive

        self.top_settings.addWidget(appearance, 3)
        self.top_settings.addWidget(archive, 2)
        root.addLayout(self.top_settings)

        # Список внутренних DVR/vcore намеренно не показываем сотруднику.
        # В 3.0.3 он обновляется фоново из B2O только как аварийный fallback;
        # основное определение архива идёт по player playlist выбранного периода.

        servers = Card(kind="hero")
        sl = QVBoxLayout(servers)
        sl.setContentsMargins(20, 18, 20, 18)
        sl.setSpacing(11)
        top = QHBoxLayout()
        left = QVBoxLayout()
        st = QLabel("VPN-серверы")
        st.setObjectName("SectionTitle")
        sh = QLabel("Встроены vpn01–vpn10, rb-vpn01 и kz-vpn01. Беларусь и Казахстан доступны для ручного выбора; автоматический подбор нагрузки использует только российские серверы.")
        sh.setObjectName("Muted")
        sh.setWordWrap(True)
        left.addWidget(st)
        left.addWidget(sh)
        add = QPushButton("＋ Добавить сервер")
        add.setProperty("role", "primary")
        add.clicked.connect(self._add)
        top.addLayout(left, 1)
        top.addWidget(add, 0, Qt.AlignmentFlag.AlignTop)
        sl.addLayout(top)

        self.server_rows = QVBoxLayout()
        self.server_rows.setSpacing(7)
        sl.addLayout(self.server_rows)
        self._render_servers()
        self.servers_card = servers
        root.addWidget(servers)

        about = Card(subtle=True)
        bl = QHBoxLayout(about)
        bl.setContentsMargins(20, 16, 20, 16)
        about_text = QVBoxLayout()
        bt = QLabel("LinkVideo.Helper")
        bt.setObjectName("SectionTitle")
        ver = QLabel(f"Версия {APP_VERSION}")
        ver.setObjectName("Muted")
        about_text.addWidget(bt)
        about_text.addWidget(ver)
        upd = QPushButton("Проверить обновления")
        upd.clicked.connect(self.updateRequested.emit)
        bl.addLayout(about_text, 1)
        bl.addWidget(upd)
        root.addWidget(about)

        self.task = TaskStatus()
        self.task.hide()
        root.addWidget(self.task)
        root.addStretch(1)

        self._sync_responsive_layout()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_responsive_layout()

    def _sync_responsive_layout(self):
        if not hasattr(self, "page_layout"):
            return
        width = self.width()
        compact = width < 1050
        self.top_settings.setDirection(QBoxLayout.Direction.TopToBottom if compact else QBoxLayout.Direction.LeftToRight)
        columns = 4 if width >= 1450 else (3 if width >= 1080 else 2)
        self._relayout_themes(columns)

    def _relayout_themes(self, columns: int):
        if not hasattr(self, "theme_grid"):
            return
        if getattr(self, "_theme_columns", None) == columns and self.theme_grid.count():
            return
        self._theme_columns = columns
        while self.theme_grid.count():
            self.theme_grid.takeAt(0)
        buttons = list(self._theme_buttons.values())
        for index, button in enumerate(buttons):
            self.theme_grid.addWidget(button, index // columns, index % columns)
        for column in range(columns):
            self.theme_grid.setColumnStretch(column, 1)

    def _theme_changed(self, key: str):
        key = normalize_theme(key)
        self.settings.setValue("ui/theme_v2", key)
        for theme_key, button in getattr(self, "_theme_buttons", {}).items():
            button.setProperty("active", "true" if theme_key == key else "false")
            button.style().unpolish(button)
            button.style().polish(button)
        self.themeChanged.emit(key)

    def _clear(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
            elif item.layout():
                self._clear(item.layout())

    def _render_servers(self):
        """Перерисовать список VPN-серверов в настройках.

        Метод был случайно потерян при рефакторинге тем 3.0.4/3.0.5, хотя
        вызовы из _build/_add/_remove/_set_enabled остались.
        """
        self._clear(self.server_rows)
        for server in self.registry.all(include_disabled=True):
            card = Card(subtle=True)
            row = QHBoxLayout(card)
            row.setContentsMargins(13, 9, 10, 9)
            row.setSpacing(9)

            labels = QVBoxLayout()
            labels.setSpacing(1)
            name = QLabel(server.host)
            name.setObjectName("Value")
            meta = QLabel("Встроенный сервер" if server.builtin else "Добавлен вручную")
            meta.setObjectName("TinyMuted")
            labels.addWidget(name)
            labels.addWidget(meta)

            country = StatusPill(
                server.country,
                "info" if server.country != "Россия" else "neutral",
            )
            state = StatusPill(
                "Используется" if server.enabled else "Отключён",
                "success" if server.enabled else "neutral",
            )

            toggle = Switch(server.enabled)
            toggle.setToolTip("Использовать сервер в поиске и создании клиентов")
            toggle.toggled.connect(
                lambda enabled, h=server.host: self._set_enabled(h, enabled)
            )

            test = QPushButton("Проверить")
            test.setProperty("role", "ghost")
            test.clicked.connect(
                lambda checked=False, h=server.host: self._test_server(h)
            )

            row.addLayout(labels, 1)
            row.addWidget(country)
            row.addWidget(state)
            row.addWidget(test)
            row.addWidget(toggle)

            if not server.builtin:
                delete = QPushButton("×")
                delete.setProperty("role", "icon")
                delete.setToolTip("Удалить пользовательский сервер")
                delete.clicked.connect(
                    lambda checked=False, h=server.host: self._remove(h)
                )
                row.addWidget(delete)

            self.server_rows.addWidget(card)

    def _add(self):
        dialog = AddServerDialog(self.registry, self)
        if dialog.exec():
            self._render_servers()
            self.serversChanged.emit()

    def _remove(self, host: str):
        dialog = ConfirmDialog(
            "Удалить VPN-сервер",
            f"Удалить {host} из пользовательского списка? Встроенные серверы удалить нельзя.",
            "Удалить",
            True,
            self,
        )
        if not dialog.exec():
            return
        self.registry.remove(host)
        self._render_servers()
        self.serversChanged.emit()

    def _set_enabled(self, host: str, enabled: bool):
        self.registry.set_enabled(host, enabled)
        self._render_servers()
        self.serversChanged.emit()

    def _test_server(self, host: str):
        if not self.service or not self.credentials:
            return
        if self._testing_host:
            return
        self._testing_host = host
        self.task.show()
        self.task.busy("Проверяю VPN-сервер", host)

        def worker():
            try:
                stat = self.service.analyze_server_quick(host, self.credentials)
                self.testReady.emit(host, stat, None)
            except Exception as exc:
                self.testReady.emit(host, None, classify_exception(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _on_test_ready(self, host: str, stat, error):
        self._testing_host = ""
        if error:
            self.task.error(f"{host} недоступен", getattr(error, "message", None) or str(error))
            return
        cpu = "нет данных" if getattr(stat, "cpu_load", None) is None else f"CPU {stat.cpu_load}%"
        mem = "нет данных" if getattr(stat, "memory_usage_percent", None) is None else f"RAM {stat.memory_usage_percent}%"
        self.task.done("VPN-сервер отвечает", f"{host} · {cpu} · {mem} · активных VPN: {stat.clients_online}")
