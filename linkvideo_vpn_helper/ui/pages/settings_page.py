from __future__ import annotations

import re
import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QBoxLayout, QCheckBox, QGridLayout, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QPushButton, QTabWidget, QVBoxLayout, QWidget,
)

from linkvideo_vpn_helper.services.errors import classify_exception
from linkvideo_vpn_helper.services.archive_service_core import B2OService
from linkvideo_vpn_helper.release_notes import RELEASE_HISTORY, release_notes
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
        self.archive_b2o = B2OService(settings)
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
            "Внешний вид, VPN-серверы, архивные кластеры и история изменений программы.",
        )
        root.addWidget(self.header)

        self.settings_tabs = QTabWidget()
        self.settings_tabs.setObjectName("SettingsTabs")
        self.settings_tabs.setDocumentMode(True)

        self.appearance_tab = QWidget()
        self.appearance_layout = QVBoxLayout(self.appearance_tab)
        self.appearance_layout.setContentsMargins(0, 14, 0, 0)
        self.appearance_layout.setSpacing(12)

        self.archive_tab = QWidget()
        self.archive_layout = QVBoxLayout(self.archive_tab)
        self.archive_layout.setContentsMargins(0, 14, 0, 0)
        self.archive_layout.setSpacing(12)

        # The active VPNSync integration injects its functional backend form
        # into this tab. Preserve test/save signals and Windows DPAPI storage.
        self.cloud_tab = QWidget()
        self.cloud_layout = QVBoxLayout(self.cloud_tab)
        self.cloud_layout.setContentsMargins(0, 14, 0, 0)
        self.cloud_layout.setSpacing(12)

        self.servers_tab = QWidget()
        self.servers_layout = QVBoxLayout(self.servers_tab)
        self.servers_layout.setContentsMargins(0, 14, 0, 0)
        self.servers_layout.setSpacing(12)

        self.about_tab = QWidget()
        self.about_layout = QVBoxLayout(self.about_tab)
        self.about_layout.setContentsMargins(0, 14, 0, 0)
        self.about_layout.setSpacing(12)

        self.settings_tabs.addTab(self.appearance_tab, "Внешний вид")
        self.settings_tabs.addTab(self.cloud_tab, "Облачный сервер")
        self.settings_tabs.addTab(self.archive_tab, "Архив")
        self.settings_tabs.addTab(self.servers_tab, "VPN-серверы")
        self.settings_tabs.addTab(self.about_tab, "О программе")
        root.addWidget(self.settings_tabs)

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
        self.animations_enabled = QCheckBox("Плавные анимации интерфейса")
        self.animations_enabled.setToolTip("Плавное появление страниц и короткая анимация при неверном пароле.")
        self.animations_enabled.setChecked(bool(self.settings.value("ui/animations_enabled", True, bool)))
        self.animations_enabled.toggled.connect(
            lambda enabled: self.settings.setValue("ui/animations_enabled", enabled)
        )
        al.addWidget(self.animations_enabled)
        al.addStretch(1)
        self.appearance_card = appearance

        archive = Card()
        ar = QVBoxLayout(archive)
        ar.setContentsMargins(20, 18, 20, 18)
        ar.setSpacing(11)
        at = QLabel("Архивные кластеры")
        at.setObjectName("SectionTitle")
        ah = QLabel(
            "Для поиска архива Helper использует профиль оператора B2O, "
            "данные камеры и HLS-плейлисты. К существующим кластерам можно "
            "добавить отдельные DVR/vcore-серверы для резервной проверки."
        )
        ah.setObjectName("Muted")
        ah.setWordWrap(True)
        ar.addWidget(at)
        ar.addWidget(ah)

        self.archive_host_rows: dict[int, QVBoxLayout] = {}
        for operator_id, country, prefix, cluster in (
            (241, "Россия", "linkvideo_", "linkvideo"),
            (1721, "Казахстан", "linkvideokz_", "linkvideokz"),
            (1741, "Беларусь", "linkvideoby_", "linkvideoby"),
        ):
            section = Card(subtle=True)
            group = QVBoxLayout(section)
            group.setContentsMargins(13, 11, 13, 11)
            group.setSpacing(6)
            country_row = QHBoxLayout()
            country_title = QLabel(f"{country} · {cluster}")
            country_title.setObjectName("Value")
            country_row.addWidget(country_title, 1)
            add_host = QPushButton("＋ Сервер")
            add_host.setProperty("role", "soft")
            add_host.setToolTip("Добавить DVR/vcore в резервный поиск этого кластера")
            add_host.clicked.connect(
                lambda checked=False, op=operator_id: self._add_archive_host(op))
            country_row.addWidget(add_host)
            group.addLayout(country_row)
            camera_prefix = QLabel(f"Камеры: {prefix}*")
            camera_prefix.setObjectName("TinyMuted")
            group.addWidget(camera_prefix)
            items = QVBoxLayout()
            items.setSpacing(5)
            group.addLayout(items)
            self.archive_host_rows[operator_id] = items
            self._render_archive_hosts(operator_id)
            ar.addWidget(section)

        extra_note = QLabel(
            "Новый кластер B2O с отдельным ID оператора — не просто сервер. "
            "Для него требуется подтверждённый адрес API, префикс камер и "
            "права текущего аккаунта. Ссылки с токенами сюда вставлять не нужно."
        )
        extra_note.setObjectName("TinyMuted")
        extra_note.setWordWrap(True)
        ar.addWidget(extra_note)

        archive_note = QLabel("Время запроса архива вводится во времени камеры. Переезды reserve-transfers сопоставляются через UTC+7 автоматически.")
        archive_note.setObjectName("TinyMuted")
        archive_note.setWordWrap(True)
        ar.addWidget(archive_note)
        ar.addStretch(1)
        self.archive_card = archive

        # Theme selection and a realistic mini-preview share one tab.
        preview = Card()
        pv = QVBoxLayout(preview)
        pv.setContentsMargins(20, 18, 20, 18)
        pv.setSpacing(11)
        pv_title = QLabel("Предпросмотр")
        pv_title.setObjectName("SectionTitle")
        pv.addWidget(pv_title)
        pv_hint = QLabel("Навигация, обычный пункт и основная кнопка")
        pv_hint.setObjectName("TinyMuted")
        pv.addWidget(pv_hint)
        preview_side = Card(subtle=True)
        preview_side.setObjectName("ThemePreviewSidebar")
        ps = QVBoxLayout(preview_side)
        ps.setContentsMargins(16, 15, 16, 15)
        ps.setSpacing(9)
        demo_logo = QLabel("▣  LinkVideo.Helper")
        demo_logo.setObjectName("PreviewBrand")
        demo_on = QLabel("  ◉   Активный раздел")
        demo_on.setObjectName("PreviewActive")
        demo_off = QLabel("  ◇   Другой раздел")
        demo_off.setObjectName("PreviewInactive")
        for preview_label in (demo_logo, demo_on, demo_off):
            preview_label.setMinimumHeight(33)
            ps.addWidget(preview_label)
        pv.addWidget(preview_side)
        main_action = QLabel("Основная кнопка")
        main_action.setObjectName("PreviewButton")
        main_action.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_action.setMinimumHeight(42)
        pv.addWidget(main_action)
        pv.addStretch(1)
        appearance_row = QHBoxLayout()
        appearance_row.setSpacing(12)
        appearance_row.addWidget(appearance, 6)
        appearance_row.addWidget(preview, 4)
        self.appearance_layout.addLayout(appearance_row)
        self.appearance_layout.addStretch(1)
        self.archive_layout.addWidget(archive)
        self.archive_layout.addStretch(1)


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
        self.servers_layout.addWidget(servers)
        self.servers_layout.addStretch(1)

        about = Card(subtle=True)
        bl = QHBoxLayout(about)
        bl.setContentsMargins(20, 16, 20, 16)
        about_text = QVBoxLayout()
        bt = QLabel("LinkVideo.Helper")
        bt.setObjectName("SectionTitle")
        ver = QLabel(f"Установленная версия: {APP_VERSION}")
        ver.setObjectName("Muted")
        about_text.addWidget(bt)
        about_text.addWidget(ver)
        upd = QPushButton("Проверить обновления")
        upd.clicked.connect(self.updateRequested.emit)
        bl.addLayout(about_text, 1)
        bl.addWidget(upd)
        self.about_layout.addWidget(about)

        current = Card()
        current_layout = QVBoxLayout(current)
        current_layout.setContentsMargins(20, 17, 20, 18)
        current_layout.setSpacing(10)
        current_title = QLabel(f"Что нового в {APP_VERSION}")
        current_title.setObjectName("SectionTitle")
        current_layout.addWidget(current_title)
        for line in release_notes(APP_VERSION):
            entry = QLabel("•  " + line)
            entry.setWordWrap(True)
            entry.setObjectName("Muted")
            current_layout.addWidget(entry)
        self.about_layout.addWidget(current)

        history_title = QLabel("История изменений")
        history_title.setObjectName("SectionTitle")
        self.about_layout.addWidget(history_title)
        for number, notes in RELEASE_HISTORY:
            if number == APP_VERSION:
                continue
            previous = Card(subtle=True)
            previous_layout = QVBoxLayout(previous)
            previous_layout.setContentsMargins(18, 13, 18, 13)
            previous_layout.setSpacing(6)
            heading = QLabel(f"Версия {number}")
            heading.setObjectName("Value")
            previous_layout.addWidget(heading)
            for note in notes:
                item = QLabel("•  " + note)
                item.setWordWrap(True)
                item.setObjectName("TinyMuted")
                previous_layout.addWidget(item)
            self.about_layout.addWidget(previous)
        self.about_layout.addStretch(1)

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

    def _render_archive_hosts(self, operator_id: int) -> None:
        """Show operator-scoped additions; never reveal account tokens."""
        rows = self.archive_host_rows[operator_id]
        self._clear(rows)
        hosts = self.archive_b2o.custom_archive_servers(operator_id)
        if not hosts:
            hint = QLabel("Дополнительных серверов пока нет")
            hint.setObjectName("TinyMuted")
            rows.addWidget(hint)
            return
        for host in hosts:
            line = QHBoxLayout()
            label = QLabel(host)
            label.setObjectName("Muted")
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            label.setWordWrap(True)
            line.addWidget(label, 1)
            remove = QPushButton("×")
            remove.setProperty("role", "icon")
            remove.setToolTip(f"Убрать сервер {host} из резервного поиска")
            remove.clicked.connect(
                lambda checked=False, op=operator_id, h=host:
                self._remove_archive_host(op, h))
            line.addWidget(remove)
            rows.addLayout(line)

    def _add_archive_host(self, operator_id: int) -> None:
        country = self.archive_b2o.operator_country(operator_id)
        value, accepted = QInputDialog.getText(
            self, f"Добавить DVR-сервер · {country}",
            "Домен сервера (без ссылки и токена):",
            QLineEdit.EchoMode.Normal, "")
        if not accepted or not value.strip():
            return
        try:
            clean = self.archive_b2o.normalize_vcore_host(value)
            if not re.fullmatch(r"[a-z0-9][a-z0-9.-]*\.video\.goodline\.info", clean):
                raise ValueError(
                    "Нужен адрес DVR/vcore в домене video.goodline.info, "
                    "без путей, параметров и токенов.")
            self.archive_b2o.add_custom_archive_server(operator_id, clean)
        except ValueError as exc:
            self.task.show()
            self.task.warning("Сервер не добавлен", str(exc))
            return
        self._render_archive_hosts(operator_id)
        self.task.show()
        self.task.done("Сервер сохранён", "Он будет учитываться при резервном поиске архива.")

    def _remove_archive_host(self, operator_id: int, host: str) -> None:
        dialog = ConfirmDialog(
            "Убрать архивный сервер",
            f"Убрать {host} из пользовательского списка? "
            "Встроенные серверы и история найденных записей не изменятся.",
            "Убрать", True, self)
        if not dialog.exec():
            return
        self.archive_b2o.remove_custom_archive_server(operator_id, host)
        self._render_archive_hosts(operator_id)

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
