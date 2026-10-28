from __future__ import annotations

"""Billing workbench for Belarus and Kazakhstan.

Renders live records only from an explicitly configured BillingGateway. The
default adapter is fail-closed; no guesswork around CRM endpoints or cookies.
"""

import threading
from datetime import date

from PySide6.QtCore import QDate, QSettings, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDateEdit, QDialog, QFormLayout, QMenu,
    QGridLayout, QHeaderView, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTabWidget, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

from linkvideo_vpn_helper.services.billing_catalog import TariffReferenceCatalog
from linkvideo_vpn_helper.services.billing_readonly_gateway import BillingReadOnlyGateway
from linkvideo_vpn_helper.services.billing_token_transport import BillingTokenTransport
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

    def __init__(self, snapshot: TariffReferenceCatalog, account_type: str, parent=None):
        super().__init__(parent)
        self.setObjectName("TariffCatalogDialog")
        self.setWindowTitle("Справочник тарифов")
        self.resize(920, 600)
        self.setMinimumSize(670, 440)
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 19, 22, 19)
        root.setSpacing(11)

        title = QLabel("Каталог тарифов")
        title.setObjectName("SectionTitle")
        root.addWidget(title)
        note = QLabel(
            f"{snapshot.country.name} · {account_type} · "
            f"{len(snapshot.tariffs)} записей. Каталог не означает, "
            "что тариф разрешён для конкретной услуги."
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
        main_l.addWidget(self.filter)

        self.table = QTableWidget(0, 5)
        self.table.setObjectName("TariffCatalogTable")
        self.table.setHorizontalHeaderLabels(
            ["Тариф", "Стоимость", "Категория", "Тест, дней", "ID"]
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().hide()
        self.table.setWordWrap(True)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, 5):
            self.table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        self._tariffs = snapshot.tariffs
        self._currency = snapshot.country.currency_code
        self._fill_rows(self._tariffs)
        self.filter.textChanged.connect(self._filter_rows)
        main_l.addWidget(self.table)
        tabs.addTab(main, "Тарифы")

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
        for period in sorted(snapshot.archive_periods, key=lambda item: item.days, reverse=True):
            row = self.period_table.rowCount()
            self.period_table.insertRow(row)
            for column, value in enumerate((period.name, str(period.days), period.id)):
                self.period_table.setItem(row, column, QTableWidgetItem(value))
        archive_l.addWidget(self.period_table)
        tabs.addTab(archive, "Глубина архива")

        root.addWidget(tabs, 1)
        close = QPushButton("Закрыть")
        close.setObjectName("TariffCatalogClose")
        close.clicked.connect(self.accept)
        root.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)

    def _fill_rows(self, tariffs):
        self.table.setRowCount(len(tariffs))
        for row, tariff in enumerate(tariffs):
            fields = (
                tariff.name,
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

    def _filter_rows(self, query: str):
        needle = query.strip().casefold()
        for row, tariff in enumerate(self._tariffs):
            matches = not needle or needle in (
                tariff.name + " " + tariff.description + " " + tariff.id
            ).casefold()
            self.table.setRowHidden(row, not matches)


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
        root.addWidget(PageHeader(
            "Тарифы",
            "Поиск клиентов Беларуси и Казахстана, просмотр услуг и управление тарифами.",
        ))
        self.billing_login = BillingLoginPanel(self.settings, self)
        root.addWidget(self.billing_login)

        self.workspace_tabs = QTabWidget()
        self.workspace_tabs.setObjectName("SettingsTabs")
        self.workspace_tabs.setDocumentMode(True)
        self.clients_tab = QWidget()
        clients_layout = QVBoxLayout(self.clients_tab)
        clients_layout.setContentsMargins(12, 12, 12, 12)
        clients_layout.setSpacing(12)
        self.workspace_tabs.addTab(self.clients_tab, "Поиск клиента")
        self.all_tariffs_page = BillingAllTariffsWidget(self.gateway, self)
        self.workspace_tabs.addTab(self.all_tariffs_page, "Все тарифы")
        root.addWidget(self.workspace_tabs, 1)
        root = clients_layout

        self.search_card = Card(kind="hero")
        search = QVBoxLayout(self.search_card)
        search.setContentsMargins(19, 16, 19, 16)
        search.setSpacing(10)
        title_row = QHBoxLayout()
        title = QLabel("Новый биллинг LinkVideo")
        title.setObjectName("SectionTitle")
        self.api_status = StatusPill("API не подключён", "warning")
        title_row.addWidget(title)
        title_row.addStretch(1)
        title_row.addWidget(self.api_status)
        search.addLayout(title_row)
        hint = QLabel("На первом этапе поддерживаются только Беларусь и Казахстан.")
        hint.setObjectName("TinyMuted")
        search.addWidget(hint)

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
            "Введите логин клиента в строке поиска. "
            "Для получения сведений из CRM необходимо подключить согласованный API."
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
        summary.setContentsMargins(20, 17, 20, 18)
        summary.setSpacing(11)
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
        summary.addLayout(self.metrics)
        result.addWidget(self.summary_card)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("SettingsTabs")
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self._make_information(), "Общая информация")
        self.tabs.addTab(self._make_services(), "Услуги")
        self.tabs.addTab(self._make_unavailable("Финансовые операции будут доступны после подключения API."), "Финансы")
        self.tabs.addTab(self._make_unavailable("Документы пока не загружаются из биллинга."), "Документы")
        self.tabs.setCurrentIndex(1)
        result.addWidget(self.tabs)
        self.result_host.hide()
        root.addWidget(self.result_host)
        root.addStretch(1)
        self.api_status.set_status(
            "API настроен" if self.gateway.configured else "API не подключён",
            "success" if self.gateway.configured else "warning",
        )
        self.search_btn.setEnabled(bool(self.gateway.configured))
        if not self.gateway.configured:
            self.empty_hint.setText(
                "Войдите в корпоративный биллинг, чтобы искать клиентов."
            )

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
            self.gateway = BillingReadOnlyGateway(transport.get_json)
            self.api_status.set_status("Авторизован", "success")
            self.empty_hint.setText(
                "Введите логин клиента или номер учётной записи для поиска в CRM."
            )
        else:
            self.gateway = UnconfiguredBillingGateway()
            self.api_status.set_status("API не подключён", "warning")
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
        root.addWidget(self.services_table)
        self.services_hint = QLabel(
            "Смена тарифов выполняется по каждой услуге отдельно. "
            "Без подтверждённого API изменение отключено."
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
                "Выполните вход в CRM выше, затем повторите поиск.",
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
                result = self.gateway.find_client(login)
                if result is not None:
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
        self.client = client
        self.task.hide()
        self._render_client(client)

    def _render_client(self, client: BillingClient):
        validate_client(client)
        self.empty_card.hide()
        self.result_host.show()
        self.client_heading.setText(client.display_name or client.login)
        positive = client.status.strip().casefold() in {"активный", "активен", "active"}
        self.client_status.set_status(client.status or "Неизвестно", "success" if positive else "neutral")
        self.client_type.setText(" · ".join(x for x in (client.legal_type, client.country) if x))
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
            "Смена тарифов выполняется по каждой услуге отдельно. "
            "Без подтверждённого API изменение отключено."
        )
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
            action.setEnabled(bool(self.gateway.can_change_tariffs))
            action.setToolTip(
                "Сменить тариф у этой камеры"
                if self.gateway.can_change_tariffs
                else "Нужен подтверждённый API и право на изменение тарифов"
            )
            action.clicked.connect(lambda _checked=False, srv=service: self._request_options(srv))
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
        self.tabs.setCurrentIndex(1)

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
        if error is not None:
            self.task.error("Операция биллинга не выполнена", self._safe_error(error))
            return
        if operation == "catalog":
            if self.client is None:
                return
            self.task.hide()
            dialog = TariffCatalogDialog(result, self.client.legal_type, self)
            dialog.exec()
        elif operation == "options":
            service, choices = result
            self.task.hide()
            self._show_change_dialog(service, tuple(choices))
        elif operation == "change":
            try:
                validate_client(result)
            except Exception as exc:
                self.task.error("Некорректный ответ биллинга", str(exc))
                return
            self.client = result
            self._render_client(result)
            self.task.done("Тариф обновлён", "Изменение подтверждено ответом биллинга.")

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
        if isinstance(error, (BillingNotConfigured, BillingPermissionDenied, BillingValidationError)):
            return str(error)
        return "Нет ответа от сервера или произошла ошибка API. Проверьте соединение и повторите запрос."

    def _open_billing(self):
        # The OS browser owns the corporate session. Helper never reuses
        # cookies or RouterOS credentials for an unverified CRM endpoint.
        if not QDesktopServices.openUrl(QUrl(BILLING_LOGIN_URL)):
            self.task.error(
                "Не удалось открыть биллинг",
                "Проверьте браузер по умолчанию и доступность crm.linkvideo.ru.",
            )
