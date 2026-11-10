from __future__ import annotations

import re
import sys
import threading
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QBoxLayout, QCheckBox, QComboBox, QDialog, QFileDialog, QGridLayout, QHBoxLayout, QInputDialog, QLabel,
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
    backgroundReady = Signal(bool, bool, str)
    backgroundVerificationReady = Signal(bool, str)

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
        self.backgroundReady.connect(self._background_operation_complete)
        self.backgroundVerificationReady.connect(self._background_verify_done)
        self._testing_host = ""
        self._background_busy = False
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
        hint = QLabel(
            "Terminal // Code — тёмный интерфейс в стиле IDE и консоли: "
            "панели, таблицы, формы и окна входа. Выбор темы меняет "
            "только внешний вид, а не работу Helper."
        )
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

        extra_title = QLabel("Дополнительные кластеры B2O")
        extra_title.setObjectName("SectionTitle")
        ar.addWidget(extra_title)
        explanation = QLabel(
            "Новые профили выключены по умолчанию. Включите нужный оператор, "
            "после чего он появится в «Скачать архив» и «Диагностика архива». "
            "Авторизация остаётся общей через подключённую «Админку»."
        )
        explanation.setObjectName("Muted")
        explanation.setWordWrap(True)
        ar.addWidget(explanation)

        self.extra_cluster_switches: dict[str, Switch] = {}
        for cluster_key, display_name, operator_id in self.archive_b2o.EXTRA_CLUSTERS:
            cluster_card = Card(subtle=True)
            cluster_row = QHBoxLayout(cluster_card)
            cluster_row.setContentsMargins(13, 11, 13, 11)
            cluster_row.setSpacing(12)
            label_column = QVBoxLayout()
            label_column.setSpacing(3)
            headline = QLabel(f"{display_name} · оператор {operator_id}")
            headline.setObjectName("Value")
            label_column.addWidget(headline)
            desc = QLabel(
                f"B2O API: /operators/{operator_id}/cameras"
                + (" · Общий оператор с другим названием" if operator_id == 1442 else "")
            )
            desc.setObjectName("TinyMuted")
            desc.setWordWrap(True)
            label_column.addWidget(desc)
            cluster_row.addLayout(label_column, 1)
            state = QLabel()
            state.setObjectName("TinyMuted")
            cluster_row.addWidget(state)
            toggle = Switch(self.archive_b2o.extra_cluster_enabled(cluster_key))
            toggle.setToolTip(f"Разрешить выбор кластера {display_name} в архиве")
            self.extra_cluster_switches[cluster_key] = toggle

            def state_changed(enabled, *, key=cluster_key, status=state):
                self.archive_b2o.set_extra_cluster_enabled(key, enabled)
                status.setText("Включён" if enabled else "Выключен")

            toggle.toggled.connect(state_changed)
            state.setText("Включён" if toggle.isChecked() else "Выключен")
            cluster_row.addWidget(toggle)
            ar.addWidget(cluster_card)

        extra_note = QLabel(
            "Важно: «Здравсервис» и Goodline имеют одинаковый operator ID 1442 "
            "и открывают один и тот же список камер. Для разных данных потребуется "
            "ещё один идентификатор оператора или другое различие в API."
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
        self._build_background_mode()
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

    def _build_background_mode(self):
        """Optional single-computer recovery mirror, never enabled by install."""
        from linkvideo_vpn_helper.services.vpn_background_service import task_registered

        card = Card()
        form = QVBoxLayout(card)
        form.setContentsMargins(20, 18, 20, 18)
        form.setSpacing(10)
        name = QLabel("Этот компьютер — сервер синхронизации VPN")
        name.setObjectName("SectionTitle")
        form.addWidget(name)

        info = QLabel(
            "После включения Windows будет самостоятельно сверять выбранные VPN-серверы "
            "с выбранным интервалом и обновлять существующую Google-таблицу. "
            "LinkVideo.Helper можно закрыть — задача работает независимо от окна."
        )
        info.setWordWrap(True)
        info.setObjectName("Muted")
        form.addWidget(info)

        warning = QLabel(
            "Внимание: для аварийного восстановления в Google Sheets сохраняются "
            "полные данные клиентов, включая пароли PPP, NAT-порты и снимки RouterOS. "
            "Редакторы этой таблицы смогут прочитать пароли. Используйте только "
            "ограниченную служебную таблицу с доступом уполномоченных администраторов."
        )
        warning.setObjectName("WarningText")
        warning.setWordWrap(True)
        form.addWidget(warning)

        key_row = QHBoxLayout()
        self.bg_key_name = QLabel("Google Service Account: не выбран")
        self.bg_key_name.setObjectName("TinyMuted")
        self.bg_key_name.setWordWrap(True)
        self.bg_key_name.setMinimumWidth(0)
        key_row.addWidget(self.bg_key_name, 1)
        self.bg_key_button = QPushButton("Выбрать JSON")
        self.bg_key_button.clicked.connect(self._background_pick_key)
        key_row.addWidget(self.bg_key_button)
        form.addLayout(key_row)

        self.bg_targets = QLabel("")
        self.bg_targets.setObjectName("TinyMuted")
        self.bg_targets.setWordWrap(True)
        form.addWidget(self.bg_targets)

        period_row = QHBoxLayout()
        period_row.setSpacing(8)
        period_label = QLabel("Интервал резервирования")
        period_label.setObjectName("Muted")
        period_row.addWidget(period_label)
        self.bg_interval = QComboBox()
        self.bg_interval.setObjectName("VPNBackgroundInterval")
        self.bg_interval.addItem("Каждые 6 часов", 6)
        self.bg_interval.addItem("Каждые 12 часов", 12)
        self.bg_interval.addItem("Раз в сутки", 24)
        configured = int(self.settings.value("vpn_background/interval_hours", 12, int) or 12)
        index = self.bg_interval.findData(configured if configured in (6, 12, 24) else 12)
        self.bg_interval.setCurrentIndex(index)
        self.bg_interval.currentIndexChanged.connect(self._background_interval_changed)
        period_row.addWidget(self.bg_interval)
        self.bg_apply_interval = QPushButton("Применить интервал")
        self.bg_apply_interval.setProperty("role", "ghost")
        self.bg_apply_interval.clicked.connect(lambda: self._background_toggle(True))
        period_row.addWidget(self.bg_apply_interval)
        period_row.addStretch(1)
        form.addLayout(period_row)

        controls = QHBoxLayout()
        self.bg_enabled = QCheckBox("Включить этот компьютер как сервер VPN")
        self.bg_enabled.setObjectName("VPNBackgroundEnabled")
        self.bg_enabled.setChecked(False)
        self.bg_enabled.toggled.connect(self._background_toggle)
        controls.addWidget(self.bg_enabled)
        controls.addStretch(1)
        self.bg_check = QPushButton("Проверить состояние")
        self.bg_check.clicked.connect(self._background_refresh_status)
        controls.addWidget(self.bg_check)
        self.bg_admin_check = QPushButton("Проверить с правами администратора")
        self.bg_admin_check.setProperty("role", "ghost")
        self.bg_admin_check.clicked.connect(self._background_verify_admin)
        controls.addWidget(self.bg_admin_check)
        form.addLayout(controls)

        self.bg_status = QLabel("")
        self.bg_status.setObjectName("TinyMuted")
        self.bg_status.setWordWrap(True)
        form.addWidget(self.bg_status)
        self.servers_layout.addWidget(card)

        self._background_refresh_key()
        self._background_refresh_targets()
        # The task, not QSettings, is authoritative. Default is OFF.
        active = task_registered()
        self.bg_enabled.blockSignals(True)
        self.bg_enabled.setChecked(active)
        self.bg_enabled.blockSignals(False)
        self._background_refresh_status()

    def _background_interval_changed(self, _index: int):
        if self._background_busy:
            return
        interval = self.bg_interval.currentData()
        if interval not in (6, 12, 24):
            return
        self.settings.setValue("vpn_background/interval_hours", interval)
        self.settings.sync()
        if self.bg_enabled.isChecked():
            self.bg_status.setText(
                "Интервал выбран. Нажмите «Применить интервал», чтобы обновить задание Windows."
            )

    def _background_refresh_key(self):
        saved = str(self.settings.value("vpn_background/google_key_file", "", str) or "").strip()
        if not saved:
            saved = str(self.settings.value("sheets/service_account_file", "", str) or "").strip()
        self.bg_key_name.setText(
            f"Google Service Account: {Path(saved).name}"
            if saved and Path(saved).is_file()
            else "Google Service Account: ключ не выбран"
        )

    def _background_refresh_targets(self):
        # Standalone UI smoke tests may pass a tiny registry fixture.
        hosts = getattr(self.registry, "hosts", None)
        servers = list(hosts()) if callable(hosts) else [
            item.host for item in self.registry.all()
            if getattr(item, "enabled", True)
        ]
        self.bg_targets.setText(
            f"Серверов для синхронизации: {len(servers)}. "
            "Будут использованы все включённые VPN-серверы из списка выше."
        )

    def _background_pick_key(self):
        source, _ = QFileDialog.getOpenFileName(
            self, "Google Service Account для фонового резерва",
            "", "JSON (*.json)"
        )
        if not source:
            return
        try:
            from linkvideo_vpn_helper.ui.vpn_sheets_key_ui_compat import _valid_service_account
            _valid_service_account(Path(source))
        except Exception as exc:
            self.bg_status.setText(f"Неверный файл Google Service Account: {exc}")
            return
        self.settings.setValue("vpn_background/google_key_file", source)
        self.settings.sync()
        self._background_refresh_key()
        self.bg_status.setText("Ключ выбран. Фоновый режим пока не изменён.")

    def _background_revert_toggle(self, enabled: bool):
        self.bg_enabled.blockSignals(True)
        self.bg_enabled.setChecked(enabled)
        self.bg_enabled.blockSignals(False)

    def _background_toggle(self, enable: bool):
        if self._background_busy:
            return
        from linkvideo_vpn_helper.services.vpn_background_service import task_registered
        original_enabled = task_registered()
        if enable:
            if not sys.platform.startswith("win") or not getattr(sys, "frozen", False):
                self.bg_status.setText(
                    "Фоновый режим можно включить в установленной Windows-версии LinkVideo.Helper."
                )
                self._background_revert_toggle(original_enabled)
                return
            if self.credentials is None or not self.credentials.username or not self.credentials.password:
                self.bg_status.setText("Нет подтверждённой авторизации RouterOS.")
                self._background_revert_toggle(original_enabled)
                return
            servers = self.registry.hosts()
            if not servers:
                self.bg_status.setText("Сначала включите хотя бы один VPN-сервер.")
                self._background_revert_toggle(original_enabled)
                return
            key_path = str(
                self.settings.value("vpn_background/google_key_file", "", str)
                or self.settings.value("sheets/service_account_file", "", str) or ""
            ).strip()
            try:
                from linkvideo_vpn_helper.services.vpn_sheets_sync import (
                    GoogleSheetsBackend, SPREADSHEET_ID,
                )
                from linkvideo_vpn_helper.ui.vpn_sheets_key_ui_compat import _valid_service_account
                if key_path and Path(key_path).is_file():
                    google = _valid_service_account(Path(key_path))
                else:
                    backend = GoogleSheetsBackend.from_settings(self.settings)
                    if backend is None:
                        raise ValueError(
                            "Выберите Google Service Account JSON с доступом к таблице."
                        )
                    google = backend.service_account_info
                spreadsheet_id = SPREADSHEET_ID
            except Exception as exc:
                self.bg_status.setText(str(exc))
                self._background_revert_toggle(original_enabled)
                return

            interval_hours = int(self.bg_interval.currentData())
            confirm = ConfirmDialog(
                "Включить полную синхронизацию?" if not original_enabled else "Изменить расписание синхронизации?",
                f"Компьютер будет каждые {interval_hours} ч резервировать {len(servers)} VPN-серверов "
                "в Google Sheets, даже после закрытия Helper.\n\n"
                "Будут передаваться PPP-пароли и полные RouterOS-снимки клиентов. "
                "Любой редактор этой таблицы сможет видеть пароли. "
                "Включайте только при ограниченном доступе к Google Sheets. "
                "Windows запросит права администратора для планировщика.",
                confirm_text="Включить с полным резервом", parent=self
            )
            if confirm.exec() != QDialog.DialogCode.Accepted:
                self._background_revert_toggle(original_enabled)
                return
            try:
                from linkvideo_vpn_helper.services.vpn_background_service import stage_configuration
                staged = stage_configuration(
                    self.credentials, google, servers, spreadsheet_id,
                    interval_hours=interval_hours,
                )
            except Exception as exc:
                self.bg_status.setText(f"Не удалось подготовить защищённую конфигурацию: {exc}")
                self._background_revert_toggle(original_enabled)
                return
        else:
            confirm = ConfirmDialog(
                "Выключить фоновую синхронизацию?",
                "Windows перестанет автоматически отправлять резервные данные VPN "
                "в Google Sheets. Уже сохранённая информация в таблице не удалится.",
                confirm_text="Отключить", parent=self
            )
            if confirm.exec() != QDialog.DialogCode.Accepted:
                self._background_revert_toggle(True)
                return
            staged = None

        self._background_busy = True
        self.bg_enabled.setEnabled(False)
        self.bg_check.setEnabled(False)
        self.bg_admin_check.setEnabled(False)
        self.bg_key_button.setEnabled(False)
        self.bg_interval.setEnabled(False)
        self.bg_apply_interval.setEnabled(False)
        self.bg_status.setText(
            "Настраиваю планировщик Windows… подтвердите запрос UAC."
            if enable else "Отключаю фоновую задачу Windows…"
        )

        def worker():
            try:
                from linkvideo_vpn_helper.services.vpn_background_service import enable_task, disable_task
                if enable:
                    enable_task(staged)
                else:
                    disable_task()
                self.backgroundReady.emit(enable, True, "")
            except Exception as exc:
                self.backgroundReady.emit(enable, False, str(exc))
            finally:
                if staged is not None:
                    staged.unlink(missing_ok=True)

        threading.Thread(target=worker, daemon=True, name="vpn-background-task").start()

    def _background_operation_complete(self, enabled: bool, success: bool, message: str):
        self._background_busy = False
        self.bg_enabled.setEnabled(True)
        self.bg_check.setEnabled(True)
        self.bg_admin_check.setEnabled(True)
        self.bg_key_button.setEnabled(True)
        self.bg_interval.setEnabled(True)
        self.bg_apply_interval.setEnabled(True)
        if success:
            self.settings.setValue("vpn_background/enabled", bool(enabled))
            self.settings.sync()
            self._background_revert_toggle(enabled)
            self.bg_status.setText(
                f"Фоновая синхронизация включена. Резерв каждые {self.bg_interval.currentData()} ч."
                if enabled else "Фоновая задача отключена. Сохранённые в Google Sheets резервные данные не удалены."
            )
        else:
            from linkvideo_vpn_helper.services.vpn_background_service import task_registered
            self._background_revert_toggle(task_registered())
            self.bg_status.setText("Настройка не выполнена: " + message)

    def _background_verify_admin(self):
        if self._background_busy:
            return
        self._background_busy = True
        self.bg_admin_check.setEnabled(False)
        self.bg_check.setEnabled(False)
        self.bg_status.setText(
            "Проверяю наличие задания с правами администратора… подтвердите UAC."
        )

        def worker():
            try:
                from linkvideo_vpn_helper.services.vpn_background_service import verify_task_as_administrator
                verify_task_as_administrator()
                self.backgroundVerificationReady.emit(True, "")
            except Exception as exc:
                self.backgroundVerificationReady.emit(False, str(exc))

        threading.Thread(target=worker, daemon=True, name="vpn-task-verify").start()

    def _background_verify_done(self, verified: bool, message: str):
        self._background_busy = False
        self.bg_admin_check.setEnabled(True)
        self.bg_check.setEnabled(True)
        self._background_refresh_status()
        if not verified:
            self.bg_status.setText(
                "Не удалось проверить задание с правами администратора: " + message
            )

    def _background_refresh_status(self):
        from linkvideo_vpn_helper.services.vpn_background_service import (
            read_status, task_inspection, read_task_receipt,
        )
        inspection = task_inspection()
        actual = bool(inspection["installed"])
        self._background_revert_toggle(actual)
        self.bg_apply_interval.setEnabled(actual and not self._background_busy)
        self.bg_admin_check.setVisible(not inspection["verified"])
        if not actual:
            self.bg_status.setText(
                "Задание не найдено или скрыто правами Windows. "
                "Нажмите «Проверить с правами администратора», чтобы различить эти случаи."
            )
            return
        status = read_status()
        checked = status.get("checked_utc", "ещё не запускалась")
        successful = status.get("successful", 0)
        failed = status.get("failed", 0)
        state = status.get("state", "")
        if state == "ok":
            message = f"Последняя сверка: {checked} · успешно {successful} · ошибок {failed}."
        elif state == "partial":
            message = f"Частичная сверка: {checked} · успешно {successful} · ошибок {failed}. Проверьте доступ RouterOS/Google."
        elif state == "running":
            message = f"Сверка началась: {checked}."
        else:
            message = "Задание зарегистрировано, но успешная выгрузка в Google Sheets пока не подтверждена."
        receipt = read_task_receipt()
        interval = receipt.get("interval_hours", self.bg_interval.currentData())
        if inspection["verified"]:
            prefix = "Задание найдено в планировщике Windows."
        else:
            prefix = (
                "Регистрация подтверждена администратором, "
                "но текущему пользователю недоступна прямая проверка планировщика."
            )
        self.bg_status.setText(f"{prefix} Интервал: {interval} ч. {message}")

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
