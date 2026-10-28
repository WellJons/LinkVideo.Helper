from __future__ import annotations

"""Billing workbench for Belarus and Kazakhstan.

Renders live records only from an explicitly configured BillingGateway. The
default adapter is fail-closed; no guesswork around CRM endpoints or cookies.
"""

import threading
from datetime import date

from PySide6.QtCore import QDate, QSettings, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QCalendarWidget, QCheckBox, QComboBox, QDateEdit,
    QDialog, QFormLayout, QMenu, QGridLayout, QHeaderView, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QScrollArea,
    QSpinBox, QTabWidget, QTableWidget, QTableWidgetItem, QTextEdit,
    QVBoxLayout, QWidget,
)

from linkvideo_vpn_helper.services.billing_catalog import TariffReferenceCatalog
from linkvideo_vpn_helper.services.billing_search_parser import BillingSearchHit
from linkvideo_vpn_helper.services.billing_tariff_filter import (
    archive_days_from_name, bitrate_mbps_from_name, select_tariffs,
)
from linkvideo_vpn_helper.services.billing_editable_gateway import BillingEditableGateway, TariffChangeReceipt
from linkvideo_vpn_helper.services.billing_token_transport import BillingTokenTransport, BillingWriteOutcomeUnknown
from linkvideo_vpn_helper.services.billing_gateway import (
    BillingClient, BillingGateway, BillingNotConfigured, BillingPermissionDenied,
    BillingService, BillingValidationError, TariffChange, TariffOption,
    UnconfiguredBillingGateway, validate_client, validate_login,
    validate_tariff_change,
)
from linkvideo_vpn_helper.ui.components import (
    Card, PageHeader, StatusPill, TaskStatus, build_page_scaffold,
)
from linkvideo_vpn_helper.ui.dialogs import ConfirmDialog
from linkvideo_vpn_helper.ui.billing_login_panel import BillingLoginPanel
from linkvideo_vpn_helper.ui.billing_all_tariffs import BillingAllTariffsWidget


BILLING_LOGIN_URL = "https://crm.linkvideo.ru/login"


def _value(text: str) -> str:
    return str(text or "").strip() or "—"


class ChangeTariffDialog(QDialog):
    """One service only; tariff options must come from the authenticated API."""

    def __init__(self, service: BillingService, options: tuple[TariffOption, ...], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Сменить тариф")
        self.setMinimumWidth(450)
        self.setObjectName("ChangeTariffDialog")
        root = QVBoxLayout(self)
        root.setContentsMargins(25, 22, 25, 22)
        root.setSpacing(13)
        heading = QLabel("Сменить тариф")
        heading.setObjectName("SectionTitle")
        root.addWidget(heading)
        camera = QLabel(service.camera_name or service.camera_id)
        camera.setObjectName("Muted")
        camera.setWordWrap(True)
        root.addWidget(camera)
        form = QFormLayout()
        form.setSpacing(11)
        self.tariff = QComboBox()
        self.tariff.setObjectName("TariffSelect")
        for option in options:
            self.tariff.addItem(option.name, option.id)
            if option.name == service.tariff_name:
                self.tariff.setCurrentIndex(self.tariff.count() - 1)
        form.addRow("Тариф", self.tariff)
        self.effective_date = QDateEdit()
        self.effective_date.setObjectName("TariffEffectiveDate")
        self.effective_date.setCalendarPopup(True)
        self.effective_date.setDisplayFormat("dd.MM.yyyy")
        self.effective_date.setMinimumDate(QDate.currentDate())
        self.effective_date.setDate(QDate.currentDate())
        form.addRow("Дата", self.effective_date)
        self.comment = QTextEdit()
        self.comment.setObjectName("TariffComment")
        self.comment.setPlaceholderText("Необязательно")
        self.comment.setMaximumHeight(90)
        form.addRow("Комментарий", self.comment)
        root.addLayout(form)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        self.apply = QPushButton("Применить")
        self.apply.setProperty("role", "primary")
        self.apply.setObjectName("ApplyTariffButton")
        self.apply.setEnabled(bool(options))
        self.apply.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(self.apply)
        root.addLayout(row)

    def selection(self) -> tuple[TariffOption, date, str]:
        current = self.tariff.currentIndex()
        if current < 0:
            raise BillingValidationError("Не выбран тариф.")
        option = TariffOption(str(self.tariff.currentData()), self.tariff.currentText())
        day = self.effective_date.date().toPython()
        return option, day, self.comment.toPlainText().strip()


class TariffCatalogDialog(QDialog):
    """A read-only CRM catalog, never a list of approved service mutations."""

    def __init__(self, snapshot: TariffReferenceCatalog, account_type: str,
                 parent=None, *, service: BillingService | None = None,
                 selected_count: int = 1):
        super().__init__(parent)
        self.setObjectName("TariffCatalogDialog")
        self.setWindowTitle("Подбор тарифа" if service else "Справочник тарифов")
        self.resize(850, 630 if service else 600)
        self.setMinimumSize(700, 455)
        self.service = service
        self._chosen_date = QDate.currentDate()
        self._visible_tariffs = ()
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 19, 22, 19)
        root.setSpacing(11)

        title = QLabel(
            f"Изменить тариф для {selected_count} камер"
            if service and selected_count > 1 else
            "Подобрать тариф для камеры" if service else "Каталог тарифов"
        )
        title.setObjectName("SectionTitle")
        root.addWidget(title)
        if service:
            selected_camera = QLabel(
                f"Камера: {service.camera_name or service.camera_id}  ·  "
                f"Текущий тариф: {service.tariff_name or '—'}"
            )
            selected_camera.setObjectName("Value")
            selected_camera.setWordWrap(True)
            root.addWidget(selected_camera)
        note = QLabel(
            f"{snapshot.country.name} · {account_type}  ·  "
            "Срок хранения и скорость в названии тарифа. "
            "Проверьте цену перед применением."
            if service else
            f"{snapshot.country.name} · {account_type} · Справочник тарифов"
        )
        note.setObjectName("Muted")
        note.setWordWrap(True)
        root.addWidget(note)

        tabs = QTabWidget()
        tabs.setObjectName("SettingsTabs")
        tabs.setDocumentMode(True)
        main = QWidget()
        main_l = QVBoxLayout(main)
        main_l.setContentsMargins(8, 13, 8, 10)
        main_l.setSpacing(10)
        self.filter = QLineEdit()
        self.filter.setObjectName("TariffCatalogFilter")
        self.filter.setPlaceholderText("Поиск по названию, описанию или ID тарифа")
        filter_row = QHBoxLayout()
        filter_row.addWidget(self.filter, 1)
        filter_row.addWidget(QLabel("Дней хранения:"))
        self.archive_days = QSpinBox()
        self.archive_days.setObjectName("TariffCatalogArchiveDays")
        self.archive_days.setRange(0, 3660)
        self.archive_days.setSpecialValueText("Любой срок")
        self.archive_days.setToolTip("Введите точное число дней; 0 — любой срок")
        filter_row.addWidget(self.archive_days)
        main_l.addLayout(filter_row)
        quick_row = QHBoxLayout()
        quick_row.setSpacing(6)
        quick_row.addWidget(QLabel("Архив:"))
        self.quick_days = []
        for days in (0, 3, 7, 14, 30, 90):
            button = QPushButton("Все" if days == 0 else f"{days} дн.")
            button.setObjectName(f"TariffQuickDays{days}")
            button.setCheckable(True)
            button.clicked.connect(
                lambda checked=False, value=days: self.archive_days.setValue(value)
            )
            quick_row.addWidget(button)
            self.quick_days.append((days, button))
        quick_row.addStretch(1)
        quick_row.addWidget(QLabel("Скорость:"))
        self.bitrate_filter = QComboBox()
        self.bitrate_filter.setObjectName("TariffBitrateFilter")
        for text, value in (("Любая", 0), ("0,5 Мбит/с", 0.5),
                            ("1 Мбит/с", 1), ("2 Мбит/с", 2),
                            ("4 Мбит/с", 4), ("8 Мбит/с", 8)):
            self.bitrate_filter.addItem(text, value)
        self.bitrate_filter.currentIndexChanged.connect(self._filter_rows)
        quick_row.addWidget(self.bitrate_filter)
        main_l.addLayout(quick_row)

        self.table = QTableWidget(0, 6)
        self.table.setObjectName("TariffCatalogTable")
        self.table.setHorizontalHeaderLabels(
            ["Тариф", "Архив", "Стоимость", "Категория", "Тест, дней", "ID"]
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().hide()
        self.table.setWordWrap(True)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, 6):
            self.table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        self._tariffs = snapshot.tariffs
        self._country = snapshot.country.name
        self._account_type = account_type
        self._currency = snapshot.country.currency_code
        self.filter.textChanged.connect(self._filter_rows)
        self.archive_days.valueChanged.connect(self._filter_rows)
        main_l.addWidget(self.table)
        self.tariff_cards = QListWidget()
        self.tariff_cards.setObjectName("TariffPickerResults")
        self.tariff_cards.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tariff_cards.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.tariff_cards.verticalScrollBar().setSingleStep(26)
        dark = self.palette().color(self.backgroundRole()).lightness() < 128
        regular = "#332e35" if dark else "#ffffff"
        hover = "#473730" if dark else "#fff1e7"
        selected_bg = "#63412f" if dark else "#ffe4d2"
        foreground = "#f7f4f2" if dark else "#243042"
        border = "#635a60" if dark else "#d9e0e9"
        self.tariff_cards.setStyleSheet(
            "QListWidget#TariffPickerResults { background: transparent; border: none;"
            " outline: none; }"
            f"QListWidget#TariffPickerResults::item {{ background: {regular};"
            f" color: {foreground}; border: 1px solid {border};"
            " border-radius: 10px; margin: 4px 2px; padding: 12px 15px; }"
            f"QListWidget#TariffPickerResults::item:hover {{ background: {hover};"
            " border: 1px solid #dd6b22; }"
            f"QListWidget#TariffPickerResults::item:selected {{ background: {selected_bg};"
            " border: 2px solid #d65a10; }"
            f"QListWidget#TariffPickerResults::item:selected:hover {{ background: {selected_bg}; }}"
        )
        self.tariff_cards.currentRowChanged.connect(self._on_tariff_picked)
        self.tariff_cards.setVisible(bool(service))
        self.table.setVisible(not service)
        main_l.addWidget(self.tariff_cards, 1)
        self._fill_rows(self._tariffs)
        tabs.addTab(main, "Тарифы")
        if service:
            tabs.tabBar().hide()

        archive = QWidget()
        archive_l = QVBoxLayout(archive)
        archive_l.setContentsMargins(8, 13, 8, 10)
        archive_note = QLabel(
            "Справочные периоды архива. Их допустимость для отдельного тарифа "
            "в ответе API не указана."
        )
        archive_note.setObjectName("TinyMuted")
        archive_note.setWordWrap(True)
        archive_l.addWidget(archive_note)
        self.period_table = QTableWidget(0, 3)
        self.period_table.setObjectName("BillingArchivePeriodsTable")
        self.period_table.setHorizontalHeaderLabels(["Название", "Дней", "ID"])
        self.period_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.period_table.verticalHeader().hide()
        self.period_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2):
            self.period_table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        for period in sorted(snapshot.archive_periods, key=lambda item: item.days):
            row = self.period_table.rowCount()
            self.period_table.insertRow(row)
            for column, value in enumerate((period.name, str(period.days), period.id)):
                self.period_table.setItem(row, column, QTableWidgetItem(value))
        archive_l.addWidget(self.period_table)
        tabs.addTab(archive, "Глубина архива")
        if service:
            tabs.setTabVisible(1, False)

        root.addWidget(tabs, 1)
        if service is not None:
            note = QLabel(
                "Изменение будет отправлено в биллинг после отдельного подтверждения."
            )
            note.setObjectName("TinyMuted")
            note.setWordWrap(True)
            root.addWidget(note)
            preview_row = QHBoxLayout()
            self.pick_date = QPushButton(self._chosen_date.toString("dd.MM.yyyy"))
            self.pick_date.setObjectName("TariffPreviewDateButton")
            self.pick_date.setToolTip("Открыть календарь в отдельном окне")
            self.pick_date.clicked.connect(self._open_date_calendar)
            preview_row.addWidget(QLabel("Дата:"))
            preview_row.addWidget(self.pick_date)
            preview_row.addStretch(1)
            self.copy_selection = QPushButton("Скопировать выбор")
            self.copy_selection.setObjectName("CopyServiceTariffSelection")
            self.copy_selection.clicked.connect(self._copy_selection)
            preview_row.addWidget(self.copy_selection)
            root.addLayout(preview_row)
        close = QPushButton("Закрыть")
        close.setObjectName("TariffCatalogClose")
        close.clicked.connect(self.reject)
        if service:
            actions = QHBoxLayout()
            actions.addStretch(1)
            actions.addWidget(close)
            self.apply_tariff = QPushButton("Применить тариф")
            self.apply_tariff.setObjectName("ApplyServiceTariff")
            self.apply_tariff.setProperty("role", "primary")
            self.apply_tariff.setMinimumHeight(42)
            self.apply_tariff.setEnabled(False)
            self.apply_tariff.clicked.connect(self.accept)
            actions.addWidget(self.apply_tariff)
            root.addLayout(actions)
        else:
            root.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)

    def _fill_rows(self, tariffs):
        selected = select_tariffs(
            tariffs, country=self._country,
            # CRM itself lists ЮЛ, Monitor and start plans on a physical
            # account; don't silently exclude them by account_type_name.
            days=self.archive_days.value(),
            search=self.filter.text(),
            bitrate_mbps=float(self.bitrate_filter.currentData() or 0),
        )
        self._visible_tariffs = selected
        self.table.setRowCount(len(selected))
        self.tariff_cards.clear()
        for days, button in self.quick_days:
            button.setChecked(self.archive_days.value() == days)
        for row, tariff in enumerate(selected):
            retention = archive_days_from_name(tariff.name)
            fields = (
                tariff.name,
                f"{retention} дн." if retention is not None else "Не указан",
                f"{tariff.price} {self._currency}",
                "Monitor" if tariff.is_monitor else "Видеонаблюдение",
                str(tariff.test_days) if tariff.test_days is not None else "—",
                tariff.id,
            )
            for column, value in enumerate(fields):
                item = QTableWidgetItem(value)
                item.setToolTip(tariff.description or tariff.name)
                self.table.setItem(row, column, item)
            self.table.setRowHeight(row, 42)
            bit = bitrate_mbps_from_name(tariff.name)
            label = (
                f"{retention} дней" if retention is not None else "Срок не указан"
            )
            speed = f"{bit:g} Мбит/с" if bit else "Скорость не указана"
            option = QListWidgetItem(
                f"{tariff.name}\n"
                f"{label}  •  {speed}  •  {tariff.price} {self._currency}"
                f"  •  {tariff.account_type}"
            )
            option.setToolTip(tariff.description or tariff.name)
            self.tariff_cards.addItem(option)
        if self.service is not None and hasattr(self, "apply_tariff"):
            self.apply_tariff.setEnabled(False)

    def _on_tariff_picked(self, index: int):
        if 0 <= index < self.table.rowCount():
            self.table.selectRow(index)
        if hasattr(self, "apply_tariff"):
            self.apply_tariff.setEnabled(index >= 0)

    def _filter_rows(self, *_):
        self._fill_rows(self._tariffs)

    def _open_date_calendar(self):
        """Standalone calendar, never clipped by a popup/scroll area."""
        picker = QDialog(self)
        picker.setObjectName("TariffStandaloneCalendar")
        picker.setWindowTitle("Дата изменения тарифа")
        picker.setMinimumSize(410, 350)
        layout = QVBoxLayout(picker)
        calendar = QCalendarWidget(picker)
        calendar.setObjectName("TariffCalendarWidget")
        calendar.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
        calendar.setGridVisible(True)
        calendar.setMinimumDate(QDate.currentDate())
        calendar.setSelectedDate(
            self._chosen_date if self._chosen_date >= QDate.currentDate()
            else QDate.currentDate()
        )
        layout.addWidget(calendar)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(picker.reject)
        confirm = QPushButton("Выбрать дату")
        confirm.clicked.connect(picker.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(confirm)
        layout.addLayout(buttons)
        if picker.exec() == QDialog.DialogCode.Accepted:
            self._chosen_date = calendar.selectedDate()
            self.pick_date.setText(self._chosen_date.toString("dd.MM.yyyy"))

    def selection_message(self) -> str:
        if self.service is None:
            raise ValueError("Камера не выбрана")
        index = self.tariff_cards.currentRow()
        if index < 0:
            index = self.table.currentRow()
        if not 0 <= index < len(self._visible_tariffs):
            raise ValueError("Выберите строку тарифа в таблице")
        tariff = self._visible_tariffs[index]
        retention = archive_days_from_name(tariff.name)
        return (
            f"Камера: {self.service.camera_name or self.service.camera_id}\n"
            f"Текущий тариф: {self.service.tariff_name or '—'}\n"
            f"Выбранный тариф: {tariff.name}\n"
            f"Архив: {str(retention) + ' дн.' if retention is not None else 'не указан'}\n"
            f"Дата: {self._chosen_date.toString('dd.MM.yyyy')}\n\n"
            "Подбор для ручного применения в биллинге. Тариф ещё не изменён."
        )

    def selection_change(self, client: BillingClient) -> TariffChange:
        if self.service is None:
            raise BillingValidationError("Камера не выбрана")
        row = self.tariff_cards.currentRow()
        if not 0 <= row < len(self._visible_tariffs):
            raise BillingValidationError("Выберите тариф из списка.")
        tariff = self._visible_tariffs[row]
        return validate_tariff_change(
            client, self.service, TariffOption(tariff.id, tariff.name),
            self._chosen_date.toPython(), "",
        )

    def _copy_selection(self):
        try:
            message = self.selection_message()
        except ValueError:
            self.copy_selection.setText("Сначала выберите тариф")
            return
        QApplication.clipboard().setText(message)
        self.copy_selection.setText("✓ Скопировано")


class TariffsPage(QWidget):
    searchReady = Signal(int, object, object)
    operationReady = Signal(int, str, object, object)

    def __init__(self, parent=None, *, gateway: BillingGateway | None = None,
                 settings: QSettings | None = None):
        super().__init__(parent)
        self.setObjectName("TariffsPage")
        self.settings = settings if settings is not None else QSettings("LinkVideo", "LinkVideo.Helper")
        self.gateway = gateway if gateway is not None else UnconfiguredBillingGateway()
        self._owned_transport: BillingTokenTransport | None = None
        self._request_id = 0
        self._busy = False
        self._uncertain_tariff_changes: set[tuple[str, str, str, date]] = set()
        self.client: BillingClient | None = None
        self._build()
        self.searchReady.connect(self._on_search)
        self.operationReady.connect(self._on_operation)
        self.billing_login.sessionChanged.connect(self._on_billing_session_changed)

    def _build(self):
        self.page_scroll, self.page_canvas, root = build_page_scaffold(
            self, max_width=1380, min_width=760, margins=22, spacing=12,
        )
        self.page_layout = root
        header = QHBoxLayout()
        header.addWidget(PageHeader(
            "Клиенты и тарифы",
            "Поиск, камеры и управление тарифами.",
        ), 1)
        self.auth_btn = QPushButton("Войти в CRM")
        self.auth_btn.setObjectName("BillingShowLogin")
        self.auth_btn.setMinimumHeight(40)
        self.auth_btn.setProperty("role", "soft")
        self.auth_btn.clicked.connect(self._open_login)
        header.addWidget(self.auth_btn, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)
        # The CRM is a separate token-based service. B2O credentials can be
        # entered into the same styled form, but an unrelated B2O token cannot
        # simply be reused against the CRM host.
        self.billing_dialog = QDialog(self)
        self.billing_dialog.setObjectName("BillingAuthenticationDialog")
        self.billing_dialog.setWindowTitle("Вход в биллинг LinkVideo")
        self.billing_dialog.setMinimumWidth(660)
        login_layout = QVBoxLayout(self.billing_dialog)
        login_layout.setContentsMargins(14, 14, 14, 14)
        self.billing_login = BillingLoginPanel(self.settings, self.billing_dialog)
        login_layout.addWidget(self.billing_login)
        self.billing_login.sessionChanged.connect(
            lambda ok: self.billing_dialog.accept() if ok else None
        )

        self.workspace_tabs = QTabWidget()
        self.workspace_tabs.setObjectName("SettingsTabs")
        self.workspace_tabs.setDocumentMode(True)
        self.clients_tab = QWidget()
        clients_layout = QVBoxLayout(self.clients_tab)
        clients_layout.setContentsMargins(12, 12, 12, 12)
        clients_layout.setSpacing(12)
        self.workspace_tabs.addTab(self.clients_tab, "Поиск клиента")
        self.create_clients_tab = self._make_client_creation()
        self.workspace_tabs.addTab(self.create_clients_tab, "Создать клиента")
        self.all_tariffs_page = BillingAllTariffsWidget(self.gateway, self)
        self.workspace_tabs.addTab(self.all_tariffs_page, "Все тарифы")
        root.addWidget(self.workspace_tabs, 1)
        root = clients_layout

        self.search_card = Card(kind="hero")
        search = QVBoxLayout(self.search_card)
        search.setContentsMargins(16, 12, 16, 12)
        search.setSpacing(7)
        title_row = QHBoxLayout()
        title = QLabel("Поиск клиента")
        title.setObjectName("SectionTitle")
        self.api_status = StatusPill("API не подключён", "warning")
        title_row.addWidget(title)
        title_row.addStretch(1)
        title_row.addWidget(self.api_status)
        search.addLayout(title_row)
        self.auth_hint = QLabel("Беларусь и Казахстан · Вход в CRM через окно авторизации")
        self.auth_hint.setObjectName("TinyMuted")
        search.addWidget(self.auth_hint)

        find_row = QHBoxLayout()
        find_row.setSpacing(8)
        self.login = QLineEdit()
        self.login.setObjectName("TariffLoginSearch")
        self.login.setPlaceholderText("Введите логин клиента, например lkz_...")
        self.login.setMinimumHeight(44)
        self.login.returnPressed.connect(self._find_client)
        self.search_btn = QPushButton("Найти клиента")
        self.search_btn.setObjectName("TariffSearchButton")
        self.search_btn.setProperty("role", "primary")
        self.search_btn.setMinimumHeight(44)
        self.search_btn.clicked.connect(self._find_client)
        self.open_billing = QPushButton("Открыть биллинг")
        self.open_billing.setObjectName("OpenBillingButton")
        self.open_billing.setMinimumHeight(44)
        self.open_billing.clicked.connect(self._open_billing)
        find_row.addWidget(self.login, 1)
        find_row.addWidget(self.search_btn)
        find_row.addWidget(self.open_billing)
        search.addLayout(find_row)
        root.addWidget(self.search_card)

        self.task = TaskStatus()
        self.task.hide()
        root.addWidget(self.task)

        self.empty_card = Card()
        empty = QVBoxLayout(self.empty_card)
        empty.setContentsMargins(22, 38, 22, 38)
        empty.setSpacing(9)
        empty_title = QLabel("Найдите клиента")
        empty_title.setObjectName("SectionTitle")
        empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_hint = QLabel(
            "Введите логин клиента или номер учётной записи в строке поиска."
        )
        self.empty_hint.setObjectName("Muted")
        self.empty_hint.setWordWrap(True)
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty.addWidget(empty_title)
        empty.addWidget(self.empty_hint)
        root.addWidget(self.empty_card)

        self.result_host = QWidget()
        self.result_host.setObjectName("TariffResultHost")
        result = QVBoxLayout(self.result_host)
        result.setContentsMargins(0, 0, 0, 0)
        result.setSpacing(12)

        self.summary_card = Card()
        summary = QVBoxLayout(self.summary_card)
        summary.setContentsMargins(16, 13, 16, 13)
        summary.setSpacing(6)
        header = QHBoxLayout()
        self.client_heading = QLabel("")
        self.client_heading.setObjectName("SectionTitle")
        self.client_status = StatusPill("—", "neutral")
        header.addWidget(self.client_heading)
        header.addWidget(self.client_status)
        header.addStretch(1)
        summary.addLayout(header)
        self.client_type = QLabel("")
        self.client_type.setObjectName("Muted")
        summary.addWidget(self.client_type)
        self.metrics = QGridLayout()
        self.metrics.setSpacing(12)
        self.metric_values = {}
        for idx, (key, label) in enumerate((
            ("login", "Логин"), ("contract", "Договор"),
            ("services", "Услуги"), ("balance", "Баланс"),
        )):
            cell = Card(subtle=True)
            cl = QVBoxLayout(cell)
            cl.setContentsMargins(12, 9, 12, 9)
            cl.setSpacing(4)
            name = QLabel(label)
            name.setObjectName("TinyMuted")
            value = QLabel("—")
            value.setObjectName("Value")
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            cl.addWidget(name)
            cl.addWidget(value)
            self.metrics.addWidget(cell, idx // 4, idx % 4)
            self.metric_values[key] = value
            # Keep the values for diagnostics but avoid a second bulky
            # four-column data card repeating the client header.
            cell.hide()
        summary.addLayout(self.metrics)
        result.addWidget(self.summary_card)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("SettingsTabs")
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self._make_information(), "Общая информация")
        self.tabs.addTab(self._make_services(), "Услуги")
        self.tabs.addTab(self._make_unavailable("Финансовые операции будут доступны после подключения API."), "Финансы")
        self.tabs.addTab(self._make_unavailable("Документы пока не загружаются из биллинга."), "Документы")
        self.tabs.setTabVisible(2, False)
        self.tabs.setTabVisible(3, False)
        self.tabs.setCurrentIndex(1)
        result.addWidget(self.tabs)
        self.result_host.hide()
        root.addWidget(self.result_host)
        root.addStretch(1)
        # A short wheel motion must not jump the entire long page.
        self.page_scroll.verticalScrollBar().setSingleStep(24)
        self.page_scroll.verticalScrollBar().setPageStep(400)
        self.api_status.set_status(
            "API настроен" if self.gateway.configured else "API не подключён",
            "success" if self.gateway.configured else "warning",
        )
        self.search_btn.setEnabled(bool(self.gateway.configured))
        if not self.gateway.configured:
            self.empty_hint.setText(
                "Войдите в корпоративный биллинг, чтобы искать клиентов."
            )

    def _make_client_creation(self) -> QWidget:
        # Customer records are created in B2O operator administration, not in
        # the new billing CRM and not through the MikroTik VPN-account form.
        from linkvideo_vpn_helper.ui.b2o_client_create import B2OClientCreationWidget
        self.b2o_creator = B2OClientCreationWidget(self.settings, self)
        return self.b2o_creator

    def _on_billing_session_changed(self, logged_in: bool):
        """Bind CRM login to the existing verified read-only API workflows."""
        self._request_id += 1  # Ignore any in-flight request from former account.
        self.client = None
        self.result_host.hide()
        self.empty_card.show()
        self.services_table.setRowCount(0)
        self._set_busy(False)
        self.task.hide()
        old = self._owned_transport
        self._owned_transport = None
        if old is not None:
            # Do not block Qt waiting for an old HTTP request to finish.
            old.token = ""
            threading.Thread(
                target=old.close, name="crm-close-session", daemon=True
            ).start()

        if logged_in and self.billing_login.session_token:
            transport = BillingTokenTransport(
                self.billing_login.session_token, header_mode="auto"
            )
            self._owned_transport = transport
            self.gateway = BillingEditableGateway(transport)
            self.api_status.set_status("Авторизован", "success")
            self.auth_btn.setText("Сменить вход")
            self.auth_hint.setText("CRM подключён · Беларусь и Казахстан")
            self.empty_hint.setText(
                "Введите логин клиента или номер учётной записи для поиска в CRM."
            )
        else:
            self.gateway = UnconfiguredBillingGateway()
            self.api_status.set_status("API не подключён", "warning")
            self.auth_btn.setText("Войти в CRM")
            self.auth_hint.setText("Для поиска и смены тарифа выполните вход в CRM")
            self.empty_hint.setText(
                "Войдите в корпоративный биллинг, чтобы искать клиентов."
            )
        self.all_tariffs_page.set_gateway(self.gateway)
        self.search_btn.setEnabled(bool(self.gateway.configured))
        self.catalog_btn.setEnabled(False)

    def _make_information(self) -> QWidget:
        container = Card()
        layout = QGridLayout(container)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)
        self.info_values = {}
        fields = (
            ("login", "Логин"), ("contract", "Договор №"),
            ("email", "Email"), ("phone", "Телефон"),
            ("manager", "Менеджер"), ("address", "Адрес"),
            ("registered", "Дата регистрации"), ("country", "Страна"),
        )
        for index, (key, title) in enumerate(fields):
            cell = Card(subtle=True)
            block = QVBoxLayout(cell)
            block.setContentsMargins(13, 11, 13, 11)
            label = QLabel(title)
            label.setObjectName("TinyMuted")
            value = QLabel("—")
            value.setObjectName("Value")
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            block.addWidget(label)
            block.addWidget(value)
            layout.addWidget(cell, index // 2, index % 2)
            self.info_values[key] = value
        return container

    def _make_services(self) -> QWidget:
        container = Card()
        root = QVBoxLayout(container)
        root.setContentsMargins(17, 16, 17, 16)
        root.setSpacing(11)
        header_row = QHBoxLayout()
        h = QLabel("Услуги видеонаблюдения")
        h.setObjectName("SectionTitle")
        header_row.addWidget(h)
        header_row.addStretch(1)
        self.catalog_btn = QPushButton("Каталог тарифов")
        self.catalog_btn.setObjectName("TariffCatalogButton")
        self.catalog_btn.setProperty("role", "soft")
        self.catalog_btn.setEnabled(False)
        self.catalog_btn.setToolTip(
            "Справочник тарифов выбранной страны и типа клиента без изменения услуг"
        )
        self.catalog_btn.clicked.connect(self._request_catalog)
        self.bulk_btn = QPushButton("Изменить выбранным (0)")
        self.bulk_btn.setObjectName("BulkTariffChange")
        self.bulk_btn.setProperty("role", "primary")
        self.bulk_btn.setMinimumHeight(40)
        self.bulk_btn.setEnabled(False)
        self.bulk_btn.clicked.connect(self._request_bulk_catalog)
        header_row.addWidget(self.bulk_btn)
        header_row.addWidget(self.catalog_btn)
        root.addLayout(header_row)
        self.services_table = QTableWidget(0, 7)
        self.services_table.setObjectName("TariffServicesTable")
        self.services_table.setHorizontalHeaderLabels(
            ["Камера", "Тариф", "Цена", "Скидка", "Итого", "Статус", "Управление"]
        )
        self.services_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.services_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.services_table.setAlternatingRowColors(False)
        self.services_table.verticalHeader().hide()
        self.services_table.setWordWrap(True)
        self.services_table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        head = self.services_table.horizontalHeader()
        head.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        head.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        # ResizeToContents on a column made entirely of cell widgets can
        # calculate a width too small for even the first button. Keep an
        # explicit minimum width for the action column and let the table
        # show its own horizontal scrollbar when space is limited.
        for idx, width in ((2, 80), (3, 85), (4, 80), (5, 112), (6, 184)):
            head.setSectionResizeMode(idx, QHeaderView.ResizeMode.Fixed)
            self.services_table.setColumnWidth(idx, width)
        self.services_table.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.services_table.setMinimumHeight(320)
        # Keep the legacy table model available to diagnostics/tests, but the
        # customer-facing control is a compact scrollable stack of camera cards.
        self.services_table.hide()
        root.addWidget(self.services_table)
        self.camera_scroll = QScrollArea()
        self.camera_scroll.setObjectName("BillingCameraCardsScroll")
        self.camera_scroll.setWidgetResizable(True)
        self.camera_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.camera_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.camera_scroll.verticalScrollBar().setSingleStep(26)
        self.camera_scroll.setMaximumHeight(490)
        self.camera_scroll.setMinimumHeight(90)
        self.camera_cards_host = QWidget()
        self.camera_checks = {}
        self.camera_cards = QVBoxLayout(self.camera_cards_host)
        self.camera_cards.setContentsMargins(2, 2, 2, 2)
        self.camera_cards.setSpacing(9)
        self.camera_scroll.setWidget(self.camera_cards_host)
        root.addWidget(self.camera_scroll)
        self.services_hint = QLabel(
            "Выберите камеру и тариф. Изменение выполняется после подтверждения."
        )
        self.services_hint.setObjectName("TinyMuted")
        self.services_hint.setWordWrap(True)
        root.addWidget(self.services_hint)
        return container

    @staticmethod
    def _make_unavailable(message: str) -> QWidget:
        card = Card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 24, 20, 24)
        label = QLabel(message)
        label.setObjectName("Muted")
        label.setWordWrap(True)
        layout.addWidget(label)
        return card

    def _set_busy(self, busy: bool):
        self._busy = busy
        self.login.setEnabled(not busy)
        self.search_btn.setEnabled(not busy)
        self.services_table.setEnabled(not busy)
        self.camera_scroll.setEnabled(not busy)
        if hasattr(self, "bulk_btn"):
            self.bulk_btn.setEnabled(
                not busy and bool(self.gateway.can_change_tariffs)
                and any(x.isChecked() for x in self.camera_checks.values())
            )
        self.catalog_btn.setEnabled(
            not busy and self.client is not None and self.gateway.configured
            and callable(getattr(self.gateway, "reference_catalog", None))
        )

    def _find_client(self):
        if self._busy:
            return
        try:
            login = validate_login(self.login.text())
        except BillingValidationError as exc:
            self.task.warning("Проверьте логин", str(exc))
            return
        if not self.gateway.configured:
            self.task.warning(
                "Вход в биллинг",
                "Нажмите «Войти в CRM», затем повторите поиск.",
            )
            return
        self._request_id += 1
        request_id = self._request_id
        # Old client data cannot remain operable while another login is
        # being searched. Never mistake stale results for this new query.
        self.client = None
        self.result_host.hide()
        self.empty_card.show()
        self._set_busy(True)
        self.task.busy_inline("Поиск клиента", "Запрашиваю сведения из нового биллинга…")

        def work():
            try:
                resolver = getattr(self.gateway, "find_client_or_choices", None)
                result = resolver(login) if callable(resolver) else self.gateway.find_client(login)
                if isinstance(result, BillingClient):
                    validate_client(result)
                self.searchReady.emit(request_id, result, None)
            except Exception as exc:
                self.searchReady.emit(request_id, None, exc)

        threading.Thread(target=work, name="billing-client-search", daemon=True).start()

    def _on_search(self, request_id: int, client, error):
        if request_id != self._request_id:
            return
        self._set_busy(False)
        if error is not None:
            self.task.error("Не удалось получить клиента", self._safe_error(error))
            return
        if client is None:
            self.task.warning("Клиент не найден", "Проверьте логин и наличие клиента в новом биллинге.")
            self.result_host.hide()
            self.empty_card.show()
            return
        if isinstance(client, tuple) and client and all(
            isinstance(hit, BillingSearchHit) for hit in client
        ):
            chosen = self._choose_billing_account(client)
            if chosen is None:
                self.task.warning("Выбор отменён", "Нужно выбрать страну и аккаунт.")
                return
            self._load_selected_account(chosen)
            return
        if not isinstance(client, BillingClient):
            self.task.error("Некорректный ответ CRM", "Не удалось определить аккаунт.")
            return
        self.client = client
        self.task.hide()
        self._render_client(client)

    def _choose_billing_account(self, hits: tuple[BillingSearchHit, ...]):
        """Always require explicit selection when one login has several accounts."""
        dialog = QDialog(self)
        dialog.setObjectName("BillingChooseAccountDialog")
        dialog.setWindowTitle("Выберите аккаунт и страну")
        dialog.setMinimumSize(560, 320)
        layout = QVBoxLayout(dialog)
        note = QLabel(
            "Найдено несколько аккаунтов. Выберите нужную страну и договор "
            "перед открытием камер или сменой тарифа."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        choices = QListWidget()
        choices.setObjectName("BillingDuplicateAccountList")
        choices.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        choices.setStyleSheet(
            "QListWidget::item { padding: 11px; margin: 3px; }"
            "QListWidget::item:hover { background: palette(alternate-base); }"
        )
        for hit in hits:
            display = (
                f"{hit.country}   •   ID {hit.id}   •   {hit.account_type or 'Тип не указан'}\n"
                f"{hit.login}   {hit.account_name if hit.account_name != hit.login else ''}"
            )
            choices.addItem(QListWidgetItem(display))
        layout.addWidget(choices)
        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(dialog.reject)
        select = QPushButton("Открыть аккаунт")
        select.setProperty("role", "primary")
        select.setEnabled(False)
        select.setObjectName("BillingSelectAccount")
        choices.currentRowChanged.connect(lambda row: select.setEnabled(row >= 0))
        choices.itemDoubleClicked.connect(lambda item: dialog.accept())
        select.clicked.connect(dialog.accept)
        actions.addWidget(cancel)
        actions.addWidget(select)
        layout.addLayout(actions)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        row = choices.currentRow()
        return hits[row] if 0 <= row < len(hits) else None

    def _load_selected_account(self, hit: BillingSearchHit):
        if self._busy or not callable(getattr(self.gateway, "find_client_by_hit", None)):
            self.task.error("Нет доступа", "Не удалось открыть выбранный аккаунт.")
            return
        self._request_id += 1
        request_id = self._request_id
        self._set_busy(True)
        self.task.busy_inline("Открытие аккаунта", f"{hit.country} · ID {hit.id}")

        def work():
            try:
                client = self.gateway.find_client_by_hit(hit)
                validate_client(client)
                if client.id != hit.id or client.country != hit.country:
                    raise BillingValidationError("Ответ относится к другому аккаунту.")
                self.searchReady.emit(request_id, client, None)
            except Exception as exc:
                self.searchReady.emit(request_id, None, exc)

        threading.Thread(
            target=work, name="billing-selected-account", daemon=True
        ).start()

    def _render_client(self, client: BillingClient):
        validate_client(client)
        self.empty_card.hide()
        self.result_host.show()
        self.client_heading.setText(client.display_name or client.login)
        positive = client.status.strip().casefold() in {"активный", "активен", "active"}
        self.client_status.set_status(client.status or "Неизвестно", "success" if positive else "neutral")
        self.client_type.setText(
            "   ·   ".join(x for x in (
                client.legal_type, client.country,
                f"Договор № {client.contract_number}" if client.contract_number else "",
                f"Услуг: {len(client.services)}" if client.services_loaded else "",
            ) if x)
        )
        for key, text in (
            ("login", client.login),
            ("contract", client.contract_number),
            ("services", client.services_status or
             (str(len(client.services)) if client.services_loaded or client.services else "—")),
            ("balance", client.balance_text),
        ):
            self.metric_values[key].setText(_value(text))
        fields = {
            "login": client.login, "contract": client.contract_number,
            "email": client.email, "phone": client.phone, "manager": client.manager,
            "address": client.address, "registered": client.registered_at,
            "country": client.country,
        }
        for key, value in fields.items():
            self.info_values[key].setText(_value(value))
        self.catalog_btn.setEnabled(
            self.gateway.configured and
            callable(getattr(self.gateway, "reference_catalog", None))
            and not self._busy
        )
        self.services_table.setRowCount(len(client.services))
        self.services_hint.setText(
            "Список камер ещё не загружен из CRM."
            if not client.services_loaded and not client.services else
            f"Камер: {len(client.services)} · смена тарифа с подтверждением"
        )
        self.camera_checks.clear()
        while self.camera_cards.count():
            entry = self.camera_cards.takeAt(0)
            if entry.widget():
                entry.widget().deleteLater()
        for row, service in enumerate(client.services):
            cells = (
                service.camera_name + ("\n" + service.camera_id if service.camera_id else ""),
                service.tariff_name, service.price_text, service.discount_text,
                service.total_text, service.status,
            )
            for col, value in enumerate(cells):
                item = QTableWidgetItem(_value(value))
                item.setToolTip(_value(value))
                self.services_table.setItem(row, col, item)
            actions = QWidget()
            actions_row = QHBoxLayout(actions)
            actions_row.setContentsMargins(3, 3, 3, 3)
            actions_row.setSpacing(5)
            action = QPushButton("Тариф")
            action.setProperty("role", "soft")
            action.setMinimumWidth(100)
            action.setMinimumHeight(36)
            action.setObjectName("TariffChangeButton")
            can_preview = (
                bool(self.gateway.configured)
                and callable(getattr(self.gateway, "reference_catalog", None))
            )
            action.setEnabled(can_preview or bool(self.gateway.can_change_tariffs))
            action.setToolTip(
                "Подобрать тариф по стране и глубине архива для этой камеры"
                if can_preview else "Нет подтверждённого API справочника тарифов"
            )
            action.clicked.connect(
                lambda _checked=False, srv=service: self._request_service_catalog(srv)
                if callable(getattr(self.gateway, "reference_catalog", None))
                else self._request_options(srv)
            )
            secondary = QPushButton("⋯")
            secondary.setObjectName("TariffOtherActionsButton")
            secondary.setProperty("role", "soft")
            secondary.setFixedWidth(46)
            secondary.setMinimumHeight(36)
            secondary.setToolTip("Другие действия с услугой")
            menu = QMenu(secondary)
            # Operations pictured in the billing UI are not enabled until
            # their endpoints, server-side policies and audit are confirmed.
            for name in ("Скидка", "Включить / отключить", "Перенести", "История изменений"):
                item = menu.addAction(name)
                item.setEnabled(False)
                item.setToolTip("Метод API пока не подтверждён")
            secondary.setMenu(menu)
            actions_row.addWidget(action, 1)
            actions_row.addWidget(secondary)
            self.services_table.setCellWidget(row, 6, actions)
            self.services_table.setRowHeight(row, 58)
            # New presentation: full-width readable camera card with one
            # primary action. No clipped 2nd button or grid-like columns.
            cam_card = Card(subtle=True)
            cam_card.setObjectName("BillingCameraCard")
            cam_row = QHBoxLayout(cam_card)
            cam_row.setContentsMargins(13, 12, 13, 12)
            cam_row.setSpacing(12)
            check = QCheckBox()
            check.setObjectName("BillingCameraSelect")
            check.setToolTip(f"Выбрать {service.camera_name} для смены тарифа")
            check.toggled.connect(self._update_bulk_button)
            self.camera_checks[service.id] = check
            cam_row.addWidget(check)
            thumb = QLabel("Нет\nпревью")
            thumb.setObjectName("BillingCameraPreview")
            thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            thumb.setFixedSize(70, 58)
            thumb.setToolTip(
                "Превью через B2O появится после подтверждения соответствия "
                "id_camera_vc и ID камеры оператора. Не подставляем произвольное видео."
            )
            thumb.setStyleSheet(
                "QLabel#BillingCameraPreview { background: #e9edf2; "
                "color: #7b8591; border: 1px solid #dce1e7; border-radius: 7px; }"
            )
            cam_row.addWidget(thumb)
            details = QVBoxLayout()
            details.setSpacing(5)
            title_row = QHBoxLayout()
            cam_name = QLabel(service.camera_name or f"Камера {service.camera_id}")
            cam_name.setObjectName("Value")
            title_row.addWidget(cam_name)
            cam_status = StatusPill(service.status, "success" if service.status == "Активный" else "neutral")
            title_row.addWidget(cam_status)
            title_row.addStretch(1)
            details.addLayout(title_row)
            tariff_label = QLabel(
                f"Тариф: {service.tariff_name or '—'}"
                f"   ·   ID камеры: {service.camera_id}"
            )
            tariff_label.setObjectName("Muted")
            tariff_label.setWordWrap(True)
            details.addWidget(tariff_label)
            if service.total_text:
                total_label = QLabel(f"Итого: {service.total_text}")
                total_label.setObjectName("TinyMuted")
                details.addWidget(total_label)
            cam_row.addLayout(details, 1)
            change_button = QPushButton("Изменить тариф")
            change_button.setProperty("role", "soft")
            change_button.setObjectName("BillingCameraChangeTariff")
            change_button.setMinimumWidth(162)
            change_button.setMinimumHeight(42)
            change_button.setEnabled(
                bool(self.gateway.configured)
                and callable(getattr(self.gateway, "reference_catalog", None))
                and not self._busy
            )
            change_button.clicked.connect(
                lambda _checked=False, srv=service: self._request_service_catalog(srv)
            )
            cam_row.addWidget(change_button)
            other = QPushButton("⋯")
            other.setObjectName("BillingCameraOtherActions")
            other.setToolTip("Отключить, передать, история изменений")
            other.setProperty("role", "soft")
            other.setFixedWidth(42)
            other.setMinimumHeight(42)
            popup = QMenu(other)
            for label in ("Включить / отключить", "Передать камеру", "История изменений", "Скидка"):
                operation = popup.addAction(label)
                operation.triggered.connect(
                    lambda checked=False, name=label, srv=service:
                    self._billing_external_action(name, srv)
                )
            other.setMenu(popup)
            cam_row.addWidget(other)
            self.camera_cards.addWidget(cam_card)
        # Avoid nested scrolling for a short camera list. For larger lists
        # only the card stack scrolls, in small predictable pixel increments.
        self.camera_scroll.setFixedHeight(
            min(490, max(95, len(client.services) * 99 + 16))
        )
        self._update_bulk_button()
        self.tabs.setCurrentIndex(1)

    def _billing_external_action(self, action: str, service: BillingService):
        # There is no verified CRM API for disable/transfer/history yet.
        # Do not simulate writes or invent endpoints; offer original billing UI.
        choice = ConfirmDialog(
            action,
            f"Камера: {service.camera_name} (ID {service.id}).\n"
            "API этого действия пока не подтверждён. "
            "Открыть оригинальный биллинг для выполнения операции?",
            "Открыть биллинг", False, self,
        )
        if choice.exec() == QDialog.DialogCode.Accepted:
            self._open_billing()

    def _selected_camera_services(self):
        if self.client is None:
            return ()
        return tuple(
            service for service in self.client.services
            if service.id in self.camera_checks
            and self.camera_checks[service.id].isChecked()
        )

    def _update_bulk_button(self, *_):
        selected = self._selected_camera_services()
        self.bulk_btn.setText(f"Изменить выбранным ({len(selected)})")
        self.bulk_btn.setEnabled(
            bool(selected) and bool(self.gateway.can_change_tariffs) and not self._busy
        )

    def _request_bulk_catalog(self):
        selected = self._selected_camera_services()
        if not selected or self._busy or self.client is None:
            return
        self._request_id += 1
        request_id = self._request_id
        client = self.client
        self._set_busy(True)
        self.task.busy_inline(
            "Выбор тарифа", f"Получаю тарифы для {len(selected)} камер…"
        )
        def work():
            try:
                catalog = self.gateway.reference_catalog(client)
                self.operationReady.emit(
                    request_id, "bulk_catalog", (selected, catalog), None
                )
            except Exception as exc:
                self.operationReady.emit(request_id, "bulk_catalog", None, exc)
        threading.Thread(
            target=work, name="billing-multi-camera-catalog", daemon=True
        ).start()

    def _request_catalog(self):
        if (
            self._busy or self.client is None or not self.gateway.configured
            or not callable(getattr(self.gateway, "reference_catalog", None))
        ):
            return
        self._request_id += 1
        request_id = self._request_id
        client = self.client
        self._set_busy(True)
        self.task.busy_inline("Справочник тарифов", "Загружаю данные CRM без изменения услуг…")

        def work():
            try:
                snapshot = self.gateway.reference_catalog(client)
                self.operationReady.emit(request_id, "catalog", snapshot, None)
            except Exception as exc:
                self.operationReady.emit(request_id, "catalog", None, exc)

        threading.Thread(target=work, name="billing-tariff-catalog", daemon=True).start()

    def _request_service_catalog(self, service: BillingService):
        """Read-only tariff selection for one camera; no update POST."""
        if (
            self._busy or self.client is None
            or service not in self.client.services
            or not self.gateway.configured
            or not callable(getattr(self.gateway, "reference_catalog", None))
        ):
            return
        self._request_id += 1
        request_id = self._request_id
        client = self.client
        self._set_busy(True)
        self.task.busy_inline(
            "Подбор тарифа",
            "Загружаю справочник для страны и типа выбранного клиента…",
        )

        def work():
            try:
                snapshot = self.gateway.reference_catalog(client)
                self.operationReady.emit(
                    request_id, "service_catalog", (service, snapshot), None,
                )
            except Exception as exc:
                self.operationReady.emit(request_id, "service_catalog", None, exc)

        threading.Thread(
            target=work, name="billing-service-tariff-preview", daemon=True
        ).start()

    def _request_options(self, service: BillingService):
        if self._busy or self.client is None or not self.gateway.can_change_tariffs:
            return
        self._request_id += 1
        request_id = self._request_id
        self._set_busy(True)
        self.task.busy_inline("Загружаю тарифы", "Получаю доступные тарифы выбранной камеры…")

        def work():
            try:
                choices = self.gateway.tariff_options(self.client.id, service.id)
                self.operationReady.emit(request_id, "options", (service, choices), None)
            except Exception as exc:
                self.operationReady.emit(request_id, "options", None, exc)

        threading.Thread(target=work, name="billing-tariff-options", daemon=True).start()

    def _on_operation(self, request_id: int, operation: str, result, error):
        if request_id != self._request_id:
            return
        self._set_busy(False)
        if operation == "change_unknown":
            self._uncertain_tariff_changes.add(result)
            self.task.warning("Проверьте биллинг", self._safe_error(error))
            return
        if error is not None:
            self.task.error("Операция биллинга не выполнена", self._safe_error(error))
            return
        if operation == "catalog":
            if self.client is None:
                return
            self.task.hide()
            dialog = TariffCatalogDialog(result, self.client.legal_type, self)
            dialog.exec()
        elif operation == "service_catalog":
            if self.client is None:
                return
            service, snapshot = result
            if service not in self.client.services:
                return
            self.task.hide()
            dialog = TariffCatalogDialog(
                snapshot, self.client.legal_type, self, service=service,
            )
            if dialog.exec() == QDialog.DialogCode.Accepted:
                try:
                    change = dialog.selection_change(self.client)
                except BillingValidationError as exc:
                    self.task.warning("Проверьте тариф", str(exc))
                    return
                self._apply_service_tariff(service, change, dialog._visible_tariffs)
        elif operation == "bulk_catalog":
            if self.client is None:
                return
            selected, snapshot = result
            if not all(s in self.client.services for s in selected):
                return
            self.task.hide()
            dialog = TariffCatalogDialog(
                snapshot, self.client.legal_type, self,
                service=selected[0], selected_count=len(selected)
            )
            if dialog.exec() == QDialog.DialogCode.Accepted:
                try:
                    change = dialog.selection_change(self.client)
                except BillingValidationError as exc:
                    self.task.warning("Проверьте тариф", str(exc))
                    return
                self._apply_bulk_tariff(selected, change, dialog._visible_tariffs)
        elif operation == "bulk_change":
            outcomes, refreshed = result
            if self.client is not None and refreshed is not None and refreshed.id == self.client.id:
                self.client = refreshed
                self._render_client(refreshed)
            good = sum(1 for _, status, _ in outcomes if status == "ok")
            summary = "; ".join(f"{camera}: {message}" for camera, _, message in outcomes)
            if good == len(outcomes):
                self.task.done("Тарифы назначены", f"Подтверждено камер: {good}. {summary[:900]}")
            else:
                self.task.warning(
                    "Частичный результат",
                    f"Успешно: {good}/{len(outcomes)}. {summary[:900]}"
                )
        elif operation == "options":
            service, choices = result
            self.task.hide()
            self._show_change_dialog(service, tuple(choices))
        elif operation == "change":
            if not isinstance(result, TariffChangeReceipt):
                self.task.error(
                    "Некорректный ответ биллинга",
                    "Проверьте изменение в CRM перед повтором.",
                )
                return
            if result.updated_client is not None and self.client is not None:
                if result.updated_client.id == self.client.id:
                    self.client = result.updated_client
                    self._render_client(result.updated_client)
            detail = result.message
            if result.remain_pay:
                detail += f" · Доплата по ответу CRM: {result.remain_pay}"
            self.task.done("Изменение тарифа подтверждено", detail)

    def _apply_bulk_tariff(self, services, example: TariffChange, visible_tariffs):
        client = self.client
        if not client or self._busy or not self.gateway.can_change_tariffs:
            return
        selected = tuple(dict((service.id, service) for service in services).values())
        if not selected or not all(service in client.services for service in selected):
            self.task.warning("Выбор изменился", "Повторите выбор камер.")
            return
        tariff = next((t for t in visible_tariffs
                       if t.id == example.tariff_id and t.country == client.country), None)
        if tariff is None:
            self.task.warning("Неверный тариф", "Тариф другой страны.")
            return
        changes = tuple(
            validate_tariff_change(
                client, service, TariffOption(tariff.id, tariff.name),
                example.effective_date, ""
            )
            for service in selected
        )
        if any((client.id, c.service_id, c.tariff_id, c.effective_date)
               in self._uncertain_tariff_changes for c in changes):
            self.task.warning(
                "Проверьте биллинг",
                "Некоторые выбранные камеры имеют неподтверждённую предыдущую запись."
            )
            return
        names = ", ".join(s.camera_name or s.id for s in selected[:8])
        if len(selected) > 8:
            names += f" и ещё {len(selected) - 8}"
        dialog = ConfirmDialog(
            "Массовая смена тарифа",
            f"Клиент: {client.login} ({client.country})\n"
            f"Камер: {len(selected)} — {names}\n"
            f"Тариф: {tariff.name}\n"
            f"Дата: {example.effective_date:%d.%m.%Y}\n\n"
            "Биллинг не предоставляет подтверждённого пакетного API. "
            "Программа отправит отдельный PUT для каждой камеры. "
            "Уже успешные операции не откатываются при ошибке следующей.",
            "Применить к выбранным", False, self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._request_id += 1
        request_id = self._request_id
        self._set_busy(True)
        self.task.busy_inline("Массовая смена", f"Обработка {len(changes)} камер…")

        def work():
            outcomes = []
            for service, change in zip(selected, changes):
                fingerprint = (client.id, change.service_id, change.tariff_id, change.effective_date)
                try:
                    result = self.gateway.change_tariff(change, client=client)
                    outcomes.append((service.camera_name, "ok", result.message))
                except BillingWriteOutcomeUnknown:
                    self._uncertain_tariff_changes.add(fingerprint)
                    outcomes.append((
                        service.camera_name, "unknown",
                        "Исход запроса неизвестен; дальнейшие изменения остановлены"
                    ))
                    break
                except Exception:
                    outcomes.append((
                        service.camera_name, "error",
                        "Биллинг отклонил запрос или не удалось подтвердить его результат"
                    ))
                    break
            try:
                refreshed = self.gateway.find_client_by_id(client)  # optional
            except Exception:
                refreshed = None
            self.operationReady.emit(
                request_id, "bulk_change", (tuple(outcomes), refreshed), None
            )
        threading.Thread(
            target=work, name="billing-multi-camera-put", daemon=True
        ).start()

    def _apply_service_tariff(self, service: BillingService, change: TariffChange,
                              visible_tariffs):
        """One deliberate PUT per confirmed selection. Never retry uncertain writes."""
        client = self.client
        if client is None or not self.gateway.can_change_tariffs or self._busy:
            self.task.warning("Нет доступа", "Требуется подтверждённая сессия CRM.")
            return
        if service not in client.services or change.client_id != client.id or change.service_id != service.id:
            self.task.warning("Камера изменилась", "Повторно откройте клиента.")
            return
        choice = next((t for t in visible_tariffs if t.id == change.tariff_id), None)
        if choice is None or choice.country != client.country:
            self.task.warning("Неверный тариф", "Тариф должен принадлежать стране клиента.")
            return
        fingerprint = (client.id, service.id, change.tariff_id, change.effective_date)
        if fingerprint in self._uncertain_tariff_changes:
            self.task.warning(
                "Проверьте биллинг",
                "Этот запрос уже отправлялся, но результат был неизвестен. "
                "Проверьте изменение в CRM перед повтором.",
            )
            return
        confirm = ConfirmDialog(
            "Изменить тариф камеры?",
            f"Камера: {service.camera_name} (ID {service.id})\n"
            f"Клиент: {client.login} · {client.country}\n"
            f"Было: {service.tariff_name}\n"
            f"Будет: {choice.name}\n"
            f"Стоимость в каталоге: {choice.price}\n"
            f"Тип тарифа по CRM: {choice.account_type}\n"
            f"Дата применения: {change.effective_date:%d.%m.%Y}\n\n"
            "Биллинг покажет итоговое решение о допустимости тарифа. "
            "Изменение будет отправлено после подтверждения.",
            "Применить тариф",
            False,
            self,
        )
        if confirm.exec() != QDialog.DialogCode.Accepted:
            return
        self._request_id += 1
        request_id = self._request_id
        self._set_busy(True)
        self.task.busy_inline("Смена тарифа", "Отправляю подтверждённый PUT в биллинг…")

        def work():
            try:
                receipt = self.gateway.change_tariff(change, client=client)
                self.operationReady.emit(request_id, "change", receipt, None)
            except BillingWriteOutcomeUnknown as exc:
                self.operationReady.emit(request_id, "change_unknown", fingerprint, exc)
            except Exception as exc:
                self.operationReady.emit(request_id, "change_error", None, exc)

        threading.Thread(
            target=work, name="billing-confirmed-tariff-put", daemon=True
        ).start()

    def _show_change_dialog(self, service: BillingService, choices: tuple[TariffOption, ...]):
        if not choices or self.client is None:
            self.task.warning("Нет доступных тарифов", "Биллинг не вернул тарифы для выбранной услуги.")
            return
        dialog = ChangeTariffDialog(service, choices, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            option, effective_date, comment = dialog.selection()
            change = validate_tariff_change(self.client, service, option, effective_date, comment)
        except BillingValidationError as exc:
            self.task.warning("Проверьте параметры тарифа", str(exc))
            return
        confirmed = ConfirmDialog(
            "Подтвердить смену тарифа",
            f"Услуга: {service.camera_name or service.camera_id}\n"
            f"Новый тариф: {option.name}\n"
            f"Дата: {effective_date:%d.%m.%Y}\n"
            "Изменение будет отправлено в корпоративный биллинг.",
            "Подтвердить",
            False,
            self,
        )
        if confirmed.exec() != QDialog.DialogCode.Accepted:
            return
        self._request_id += 1
        request_id = self._request_id
        self._set_busy(True)
        self.task.busy_inline("Изменение тарифа", "Ожидаю подтверждения нового биллинга…")

        def work():
            try:
                updated = self.gateway.change_tariff(change)
                self.operationReady.emit(request_id, "change", updated, None)
            except Exception as exc:
                self.operationReady.emit(request_id, "change", None, exc)

        threading.Thread(target=work, name="billing-tariff-change", daemon=True).start()

    @staticmethod
    def _safe_error(error: Exception) -> str:
        # Do not display raw network exceptions: some libraries include
        # full URLs with tokens or HTTP authorization information in errors.
        if isinstance(error, (
            BillingNotConfigured, BillingPermissionDenied,
            BillingValidationError, BillingWriteOutcomeUnknown,
        )):
            return str(error)
        return "Нет ответа от сервера или произошла ошибка API. Проверьте соединение и повторите запрос."

    def _open_login(self):
        # Reuse the existing login panel and DPAPI-stored credentials.
        if self.billing_login.login_verified:
            self.billing_login._logout()
        self.billing_dialog.open()

    def _open_billing(self):
        # The OS browser owns the corporate session. Helper never reuses
        # cookies or RouterOS credentials for an unverified CRM endpoint.
        if not QDesktopServices.openUrl(QUrl(BILLING_LOGIN_URL)):
            self.task.error(
                "Не удалось открыть биллинг",
                "Проверьте браузер по умолчанию и доступность crm.linkvideo.ru.",
            )
