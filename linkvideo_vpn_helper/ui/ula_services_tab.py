from __future__ import annotations

"""Read-only Russian Ula account and service probe within billing workspace."""

import re
import threading
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFormLayout,
    QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from linkvideo_vpn_helper.services.ula_connection import (
    UlaConnectionDetails, create_ula_gateway, read_ula_ini,
)
from linkvideo_vpn_helper.services.ula_oracle_gateway import UlaReadError
from linkvideo_vpn_helper.ui.components import Card, StatusPill


def _safe_error(error: Exception) -> str:
    # Never put password, raw connection descriptors or filesystem paths
    # from third-party errors into screenshots, UI, logs or support reports.
    if isinstance(error, (ValueError, TypeError, UlaReadError)):
        return str(error)[:170]
    codes = re.findall(r"\b(?:ORA|DPY|DPI)-\d{4,5}\b", str(error))
    if codes:
        return "Ошибка Oracle " + ", ".join(list(dict.fromkeys(codes))[:3])
    return "Не удалось выполнить запрос Oracle. Проверьте подключение и права пользователя."


class UlaServicesTab(QWidget):
    operationFinished = Signal(int, str, object, object)

    def __init__(self, parent=None, *, gateway_factory=None):
        super().__init__(parent)
        self.setObjectName("UlaServicesTab")
        self._gateway_factory = gateway_factory or create_ula_gateway
        self._generation = 0
        self._busy = False
        self._hits = ()
        self._build()
        self.operationFinished.connect(self._finished)

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(12)

        heading = Card(kind="hero")
        head_layout = QVBoxLayout(heading)
        h = QHBoxLayout()
        label = QLabel("Россия · Ula")
        label.setObjectName("SectionTitle")
        self.state = StatusPill("Тестирование", "warning")
        h.addWidget(label)
        h.addStretch(1)
        h.addWidget(self.state)
        head_layout.addLayout(h)
        note = QLabel(
            "Проверка текущих услуг и стоимости из Ula. Только чтение: "
            "тарифы, договоры и счета здесь не изменяются. "
            "Значение COSTS сверяем с Ula до внедрения в рабочий поиск."
        )
        note.setWordWrap(True)
        note.setObjectName("Muted")
        head_layout.addWidget(note)
        root.addWidget(heading)

        connection = Card()
        setup = QVBoxLayout(connection)
        title = QLabel("Подключение к Oracle Ula")
        title.setObjectName("SectionTitle")
        setup.addWidget(title)

        row = QHBoxLayout()
        self.import_button = QPushButton("Импортировать Ula.ini")
        self.import_button.setObjectName("UlaImportIni")
        self.import_button.clicked.connect(self._import_ini)
        row.addWidget(self.import_button)
        row.addStretch(1)
        setup.addLayout(row)

        grid = QGridLayout()
        grid.setSpacing(8)
        self.host = QLineEdit()
        self.host.setObjectName("UlaOracleHost")
        self.host.setPlaceholderText("Server из Ula.ini")
        self.port = QLineEdit("1521")
        self.port.setObjectName("UlaOraclePort")
        self.port.setValidator(QIntValidator(1, 65535, self))
        self.port.setMaximumWidth(105)
        self.sid = QLineEdit()
        self.sid.setObjectName("UlaOracleSid")
        self.sid.setPlaceholderText("SID")
        self.username = QLineEdit()
        self.username.setObjectName("UlaOracleUsername")
        self.username.setPlaceholderText("UserName из Ula.ini")
        self.password = QLineEdit()
        self.password.setObjectName("UlaOraclePassword")
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("Пароль учётной записи Oracle")
        self.password.setToolTip("Пароль не сохраняется и не отправляется в логи.")
        self.password.returnPressed.connect(self._test_connection)

        grid.addWidget(QLabel("Сервер"), 0, 0)
        grid.addWidget(self.host, 0, 1)
        grid.addWidget(QLabel("Порт"), 0, 2)
        grid.addWidget(self.port, 0, 3)
        grid.addWidget(QLabel("SID"), 1, 0)
        grid.addWidget(self.sid, 1, 1)
        grid.addWidget(QLabel("Пользователь"), 1, 2)
        grid.addWidget(self.username, 1, 3)
        grid.addWidget(QLabel("Пароль"), 2, 0)
        grid.addWidget(self.password, 2, 1, 1, 3)
        grid.setColumnStretch(1, 2)
        grid.setColumnStretch(3, 1)
        setup.addLayout(grid)

        self.thick_client = QLineEdit()
        self.thick_client.setObjectName("UlaOracleInstantClient")
        self.thick_client.setPlaceholderText("Необязательно: папка 64-bit Oracle Instant Client (для старых БД)")
        self.thick_client.setToolTip("Oracle Thin не требует клиентских DLL; Thick — только при необходимости.")
        setup.addWidget(self.thick_client)

        self.test_button = QPushButton("Проверить соединение")
        self.test_button.setObjectName("UlaTestConnection")
        self.test_button.setProperty("role", "soft")
        self.test_button.clicked.connect(self._test_connection)
        setup.addWidget(self.test_button)
        root.addWidget(connection)

        find_card = Card()
        f = QVBoxLayout(find_card)
        f.setSpacing(8)
        title = QLabel("Поиск клиента Ula")
        title.setObjectName("SectionTitle")
        f.addWidget(title)
        line = QHBoxLayout()
        self.search_text = QLineEdit()
        self.search_text.setObjectName("UlaClientSearch")
        self.search_text.setPlaceholderText("Имя или идентификатор клиента")
        self.search_text.returnPressed.connect(self._find)
        self.search_type = QComboBox()
        self.search_type.setObjectName("UlaSearchType")
        for mode in (0, 1, 2):
            self.search_type.addItem(f"Режим {mode}", mode)
        self.search_type.setToolTip("Режим поиска экспериментальный, требуется сверка Ula.")
        self.find_button = QPushButton("Найти")
        self.find_button.setObjectName("UlaFindClient")
        self.find_button.setProperty("role", "primary")
        self.find_button.clicked.connect(self._find)
        line.addWidget(self.search_text, 1)
        line.addWidget(self.search_type)
        line.addWidget(self.find_button)
        f.addLayout(line)
        self.hits_table = QTableWidget(0, 3)
        self.hits_table.setObjectName("UlaSearchResults")
        self.hits_table.setHorizontalHeaderLabels(["ID клиента", "Название клиента", "Тип"])
        self.hits_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.hits_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.hits_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.hits_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.hits_table.setMaximumHeight(185)
        self.hits_table.setMinimumHeight(75)
        self.hits_table.itemSelectionChanged.connect(self._use_selected)
        f.addWidget(self.hits_table)

        line2 = QHBoxLayout()
        self.client_id = QLineEdit()
        self.client_id.setObjectName("UlaClientId")
        self.client_id.setPlaceholderText("ID_CLIENT (можно ввести вручную)")
        self.client_id.setValidator(QIntValidator(1, 2147483647, self))
        self.client_id.returnPressed.connect(self._load_services)
        self.include_inactive = QCheckBox("Включая неактивные")
        self.include_inactive.setObjectName("UlaIncludeInactive")
        self.group_client = QCheckBox("Групповой клиент")
        self.group_client.setObjectName("UlaGroupClient")
        self.fetch_button = QPushButton("Показать услуги и стоимость")
        self.fetch_button.setObjectName("UlaLoadServices")
        self.fetch_button.setProperty("role", "primary")
        self.fetch_button.clicked.connect(self._load_services)
        line2.addWidget(self.client_id, 1)
        line2.addWidget(self.include_inactive)
        line2.addWidget(self.group_client)
        line2.addWidget(self.fetch_button)
        f.addLayout(line2)
        root.addWidget(find_card)

        results = Card()
        r = QVBoxLayout(results)
        r.setSpacing(8)
        label = QLabel("Действующие услуги и тарифы")
        label.setObjectName("SectionTitle")
        r.addWidget(label)
        self.services = QTableWidget(0, 6)
        self.services.setObjectName("UlaServicesTable")
        self.services.setHorizontalHeaderLabels([
            "Услуга", "Тариф", "COSTS", "Скидка", "Статус", "Договор / ID услуги"
        ])
        self.services.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.services.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.services.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.services.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.services.setMinimumHeight(245)
        r.addWidget(self.services)
        root.addWidget(results)
        self.message = QLabel("Подключение не выполняется автоматически. Выберите Ula.ini и введите пароль.")
        self.message.setObjectName("TinyMuted")
        self.message.setWordWrap(True)
        root.addWidget(self.message)
        root.addStretch(1)

    def _import_ini(self):
        file, _ = QFileDialog.getOpenFileName(
            self, "Выберите Ula.ini", str(Path.home()), "Ula.ini (Ula.ini);;INI (*.ini)"
        )
        if not file:
            return
        try:
            settings = read_ula_ini(file)
        except ValueError as exc:
            self.message.setText(str(exc))
            return
        self.host.setText(settings.host)
        self.port.setText(str(settings.port))
        self.sid.setText(settings.sid)
        self.username.setText(settings.username)
        self.message.setText("Параметры из Ula.ini загружены. Пароль необходимо ввести отдельно.")

    def _details(self):
        return UlaConnectionDetails(
            self.host.text().strip(), int(self.port.text() or "0"),
            self.sid.text().strip(), self.username.text().strip()
        ).validated()

    def _run(self, action, fn):
        if self._busy:
            return
        self._busy = True
        self._generation += 1
        number = self._generation
        self.state.set_status("Запрос…", "warning")
        self.message.setText("Обращение к Oracle выполняется в фоновом потоке.")
        for b in (self.import_button, self.test_button, self.find_button, self.fetch_button):
            b.setEnabled(False)

        def worker():
            try:
                result = fn()
                self.operationFinished.emit(number, action, result, None)
            except Exception as exc:
                self.operationFinished.emit(number, action, None, _safe_error(exc))
        threading.Thread(target=worker, daemon=True, name="ula-read-only").start()

    def _make_gateway(self):
        return self._gateway_factory(
            self._details(), self.password.text(),
            thick_client_dir=self.thick_client.text().strip()
        )

    def _test_connection(self):
        try:
            gateway = self._make_gateway()
        except Exception as exc:
            self.message.setText(_safe_error(exc))
            return
        def work():
            connection = gateway._connect()
            try:
                return True
            finally:
                connection.close()
        self._run("connect", work)

    def _find(self):
        try:
            gateway = self._make_gateway()
            query = self.search_text.text().strip()
            mode = int(self.search_type.currentData())
        except Exception as exc:
            self.message.setText(_safe_error(exc))
            return
        self._run("find", lambda: gateway.search_clients(query, search_type=mode))

    def _load_services(self):
        try:
            gateway = self._make_gateway()
            ident = self.client_id.text().strip()
            inactive = bool(self.include_inactive.isChecked())
            grouped = bool(self.group_client.isChecked())
        except Exception as exc:
            self.message.setText(_safe_error(exc))
            return
        self._run("services", lambda: gateway.list_services(
            ident, include_non_active=inactive, group_client=grouped
        ))

    def _use_selected(self):
        items = self.hits_table.selectedItems()
        if items:
            ident = self.hits_table.item(items[0].row(), 0)
            if ident:
                self.client_id.setText(ident.text())

    def _finished(self, generation, action, result, error):
        if generation != self._generation:
            return
        self._busy = False
        for b in (self.import_button, self.test_button, self.find_button, self.fetch_button):
            b.setEnabled(True)
        if error:
            self.state.set_status("Ошибка", "warning")
            self.message.setText(str(error))
            return
        self.state.set_status("Подключено", "success")
        if action == "connect":
            self.message.setText("Oracle авторизовала подключение. Теперь можно искать клиента.")
        elif action == "find":
            self.hits_table.setRowCount(len(result))
            for i, hit in enumerate(result):
                for j, value in enumerate((hit.client_id, hit.name, hit.client_type)):
                    self.hits_table.setItem(i, j, QTableWidgetItem(value))
            self.message.setText(f"Результатов поиска: {len(result)}. Выберите клиента и запросите услуги.")
        elif action == "services":
            self.services.setRowCount(len(result))
            for i, service in enumerate(result):
                values = (
                    service.service_name, service.tariff_name, service.costs_text,
                    service.discount_text, service.status_text or service.active_flag,
                    " / ".join(x for x in (service.contract_number, service.contract_service_id) if x),
                )
                for j, value in enumerate(values):
                    self.services.setItem(i, j, QTableWidgetItem(value))
            self.message.setText(
                f"Получено услуг: {len(result)}. COSTS — сумма из Oracle; "
                "период оплаты и учёт скидок ещё не подтверждены."
            )
