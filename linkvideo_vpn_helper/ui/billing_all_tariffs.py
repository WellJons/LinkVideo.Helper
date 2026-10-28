from __future__ import annotations

"""Read-only all-tariffs catalog, independent of the selected customer."""

import threading

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from linkvideo_vpn_helper.services.billing_catalog import AllTariffsSnapshot
from linkvideo_vpn_helper.services.billing_tariff_filter import (
    archive_days_from_name, select_tariffs,
)
from linkvideo_vpn_helper.ui.components import Card, TaskStatus


class _CatalogEvents(QObject):
    complete = Signal(int, object, object)


class BillingAllTariffsWidget(QWidget):
    def __init__(self, gateway, parent=None):
        super().__init__(parent)
        self.setObjectName("BillingAllTariffsWidget")
        self.gateway = gateway
        self._query = 0
        self._rows = ()
        self._currencies = {}
        self._events = _CatalogEvents(self)
        self._events.complete.connect(self._on_result)
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(11)
        bar = QHBoxLayout()
        heading = QLabel("Все тарифы")
        heading.setObjectName("SectionTitle")
        bar.addWidget(heading)
        bar.addStretch(1)
        self.refresh = QPushButton("Обновить из биллинга")
        self.refresh.setObjectName("AllTariffsRefresh")
        self.refresh.setProperty("role", "primary")
        self.refresh.setEnabled(
            bool(self.gateway.configured and callable(getattr(self.gateway, "all_tariffs", None)))
        )
        self.refresh.clicked.connect(self.reload)
        bar.addWidget(self.refresh)
        root.addLayout(bar)

        hint = QLabel(
            "Полный справочник тарифов CRM. Новые тарифы появятся при обновлении "
            "списка. Просмотр не меняет настройки клиентов."
        )
        hint.setObjectName("TinyMuted")
        hint.setWordWrap(True)
        root.addWidget(hint)

        filter_row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setObjectName("AllTariffsSearch")
        self.search.setPlaceholderText("Название, ID или описание")
        self.search.textChanged.connect(self._filter)
        self.country = QComboBox()
        self.country.setObjectName("AllTariffsCountry")
        self.country.addItem("Все страны", "")
        self.country.currentIndexChanged.connect(self._filter)
        self.archive_days = QSpinBox()
        self.archive_days.setObjectName("AllTariffsArchiveDays")
        self.archive_days.setRange(0, 3660)
        self.archive_days.setSpecialValueText("Все сроки")
        self.archive_days.setSuffix(" дн.")
        self.archive_days.setToolTip(
            "Введите точное количество дней хранения; 0 — все сроки"
        )
        self.archive_days.valueChanged.connect(self._filter)
        filter_row.addWidget(self.search, 1)
        filter_row.addWidget(self.country)
        filter_row.addWidget(QLabel("Глубина архива:"))
        filter_row.addWidget(self.archive_days)
        root.addLayout(filter_row)

        self.status = TaskStatus()
        self.status.hide()
        root.addWidget(self.status)
        self.table = QTableWidget(0, 8)
        self.table.setObjectName("AllTariffsTable")
        self.table.setHorizontalHeaderLabels(
            ["ID", "Название", "Архив", "Страна", "Тип клиента", "Стоимость", "Пробный", "Камер"]
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setMinimumHeight(360)
        root.addWidget(self.table)
        self.empty = QLabel("API не подключён. Список тарифов пока не загружен.")
        self.empty.setObjectName("Muted")
        self.empty.setWordWrap(True)
        root.addWidget(self.empty)

    def set_gateway(self, gateway):
        """Switch active CRM identity, invalidating old background results."""
        self._query += 1
        self.gateway = gateway
        self._rows = ()
        self._currencies = {}
        self.table.setRowCount(0)
        self.country.blockSignals(True)
        self.country.clear()
        self.country.addItem("Все страны", "")
        self.country.blockSignals(False)
        self.search.clear()
        self.archive_days.setValue(0)
        connected = bool(
            gateway.configured and callable(getattr(gateway, "all_tariffs", None))
        )
        self.refresh.setEnabled(connected)
        self.empty.setText(
            "Нажмите «Обновить из биллинга» для загрузки каталога."
            if connected else "Войдите в CRM для просмотра тарифов."
        )
        self.status.hide()

    def reload(self):
        if not self.refresh.isEnabled():
            return
        self._query += 1
        request_id = self._query
        self.refresh.setEnabled(False)
        self.status.busy_inline("Загрузка каталога", "Получаю актуальный список тарифов…")

        def worker():
            try:
                snapshot = self.gateway.all_tariffs()
                self._events.complete.emit(request_id, snapshot, None)
            except Exception:
                self._events.complete.emit(request_id, None, "Не удалось загрузить справочник тарифов.")
        threading.Thread(target=worker, daemon=True, name="crm-all-tariffs").start()

    def _on_result(self, request_id: int, snapshot: AllTariffsSnapshot | None, error):
        if request_id != self._query:
            return
        self.refresh.setEnabled(
            bool(self.gateway.configured and callable(getattr(self.gateway, "all_tariffs", None)))
        )
        if error is not None:
            self.status.error("Ошибка обновления", error)
            return
        if not isinstance(snapshot, AllTariffsSnapshot):
            self.status.error("Ошибка каталога", "Неожиданный формат ответа CRM.")
            return
        self._rows = snapshot.tariffs
        self._currencies = {country.name: country.currency_code for country in snapshot.countries}
        previous_country = self.country.currentData()
        self.country.blockSignals(True)
        self.country.clear()
        self.country.addItem("Все страны", "")
        for country in snapshot.countries:
            if any(row.country == country.name for row in snapshot.tariffs):
                self.country.addItem(country.name, country.name)
        for idx in range(self.country.count()):
            if self.country.itemData(idx) == previous_country:
                self.country.setCurrentIndex(idx)
                break
        self.country.blockSignals(False)
        self._filter()
        self.status.done("Каталог обновлён", f"Загружено тарифов: {len(self._rows)}")

    def _filter(self, *_):
        country = str(self.country.currentData() or "")
        selected = select_tariffs(
            self._rows, country=country,
            days=self.archive_days.value(),
            search=self.search.text(),
        )
        self.table.setRowCount(len(selected))
        for index, row in enumerate(selected):
            depth = archive_days_from_name(row.name)
            values = (
                row.id, row.name,
                f"{depth} дн." if depth is not None else "Не указан",
                row.country, row.account_type,
                f"{row.price} {self._currencies.get(row.country, '')}".strip(),
                f"{row.test_days} дн." if row.test_days is not None else "—",
                str(row.count_cameras) if row.count_cameras is not None else "—",
            )
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setToolTip(row.description or row.name)
                self.table.setItem(index, column, cell)
            self.table.setRowHeight(index, 38)
        if not selected:
            self.empty.setText(
                "Нет тарифов по выбранной стране, валюте и глубине архива. "
                "Попробуйте другой срок или очистите фильтры."
                if self._rows else "Каталог пуст."
            )
        else:
            self.empty.setText("")
