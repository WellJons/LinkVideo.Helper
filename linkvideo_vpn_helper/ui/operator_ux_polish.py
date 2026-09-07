from __future__ import annotations

"""Operator-facing polish for archive recovery, audit and VPN infrastructure.

The PostgreSQL recovery/audit mechanics remain unchanged. This layer only keeps
technical implementation detail and high-frequency system telemetry out of the
default desktop workflow.
"""

import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QGridLayout, QHeaderView, QHBoxLayout, QLabel, QPushButton, QTableWidgetItem

from linkvideo_vpn_helper.ui.components import Card, StatusPill


_INSTALLED = False
_BUSINESS_TZ = timezone(timedelta(hours=7))


def _time_text(value: Any, *, seconds: bool = False) -> str:
    text = str(value or "").strip()
    if not text:
        return "—"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_BUSINESS_TZ)
        fmt = "%d.%m.%Y %H:%M:%S" if seconds else "%d.%m.%Y %H:%M"
        return parsed.astimezone(_BUSINESS_TZ).strftime(fmt)
    except Exception:
        return text


def _parent_card(widget):
    current = widget
    while current is not None:
        if isinstance(current, Card):
            return current
        current = current.parentWidget()
    return None


def _patch_search_archive() -> None:
    from linkvideo_vpn_helper.ui.pages.search_manage_page import SearchManagePage

    previous_cancel = SearchManagePage.cancel_current_action

    def cancel_current_action(self) -> bool:
        # Existing search cancellation / active-client navigation keeps priority.
        if previous_cancel(self):
            return True
        if getattr(self, "_action_busy", False):
            return False
        if getattr(self, "_deleted_current", None) is None:
            return False

        timer = getattr(self, "_live_timer", None)
        if timer is not None:
            timer.stop()
        try:
            self._close_client_view()
        except Exception:
            return False

        self.current = None
        self._deleted_current = None
        self._selected_port = None
        try:
            self.results.setCurrentRow(-1)
            self._highlight_result(None)
            self.open_hint.setText("Клик по записи — открыть карточку")
            self.query.setFocus()
        except RuntimeError:
            # The card itself is already closed; a disposed decoration widget
            # must not turn a successful Escape navigation into a failure.
            return True
        return True

    def render_deleted(self) -> None:
        record = getattr(self, "_deleted_current", None)
        if record is None:
            return
        self._clear_detail()

        if hasattr(self, "detail_title"):
            self.detail_title.setText(f"Удалённый клиент · {record.login}")

        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        status_row.addWidget(StatusPill("Удалён", "warning"))
        if record.password_saved:
            status_row.addWidget(StatusPill("Можно восстановить", "success"))
        else:
            status_row.addWidget(StatusPill("Восстановление недоступно", "danger"))
        status_row.addStretch(1)
        self.detail_l.addLayout(status_row)

        row = dict(getattr(record, "row", {}) or {})
        info = Card(subtle=True)
        grid = QGridLayout(info)
        grid.setContentsMargins(16, 14, 16, 14)
        grid.setHorizontalSpacing(26)
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)

        pairs = (
            ("Сервер", record.server or "—", "Удалён", _time_text(record.deleted_at)),
            ("Remote Address", record.remote_address or "—", "Последняя активность", _time_text(row.get("last_seen_at"))),
            ("Профиль", record.profile or "—", "Резерв", "Полный" if record.password_saved else "Без пароля"),
        )
        for line, values in enumerate(pairs):
            left_label, left_value, right_label, right_value = values
            for column, (label_text, value_text) in enumerate(
                ((left_label, left_value), (right_label, right_value))
            ):
                label = QLabel(label_text)
                label.setObjectName("TinyMuted")
                value = QLabel(str(value_text))
                value.setObjectName("Value")
                value.setWordWrap(True)
                base = column * 2
                grid.addWidget(label, line, base)
                grid.addWidget(value, line, base + 1)

        port_label = QLabel("NAT / Порты")
        port_label.setObjectName("TinyMuted")
        port_value = QLabel(record.ports or "—")
        port_value.setObjectName("Value")
        port_value.setWordWrap(True)
        grid.addWidget(port_label, 3, 0)
        grid.addWidget(port_value, 3, 1, 1, 3)

        reason = str(row.get("deleted_reason") or "").strip()
        if reason:
            reason_label = QLabel("Причина удаления")
            reason_label.setObjectName("TinyMuted")
            reason_value = QLabel(reason)
            reason_value.setWordWrap(True)
            grid.addWidget(reason_label, 4, 0)
            grid.addWidget(reason_value, 4, 1, 1, 3)

        self.detail_l.addWidget(info)

        if not record.password_saved:
            warning = QLabel("Пароль не сохранён — автоматическое восстановление этой записи недоступно.")
            warning.setObjectName("DangerText")
            warning.setWordWrap(True)
            self.detail_l.addWidget(warning)

        actions = QHBoxLayout()
        actions.addStretch(1)
        restore = QPushButton("Восстановить")
        restore.setProperty("role", "primary")
        restore.setMinimumHeight(42)
        restore.setEnabled(bool(record.password_saved) and not getattr(self, "_action_busy", False))
        restore.clicked.connect(self._restore_deleted)
        actions.addWidget(restore)
        self._deleted_restore_btn = restore
        self.detail_l.addLayout(actions)
        self.detail_l.addStretch(1)

    SearchManagePage.cancel_current_action = cancel_current_action
    # cloud_archive_search_integration exposes this exact renderer. Patching an
    # unused _render_deleted attribute leaves the legacy technical card visible.
    SearchManagePage._render_deleted_client = render_deleted


def _patch_activity_page() -> None:
    from linkvideo_vpn_helper.ui.pages import vpn_activity_page as page_module

    VPNActivityPage = page_module.VPNActivityPage
    original_init = VPNActivityPage.__init__

    def init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)

        # This page is an operator audit journal, not a live RouterOS event console.
        for label in self.findChildren(QLabel):
            text = str(label.text() or "")
            if text == "История VPN":
                label.setText("Журнал действий")
            elif text.startswith("Единая история PostgreSQL:"):
                label.setText(
                    "Кто и что менял в VPN-клиентах. Системные события доступны через фильтр при необходимости."
                )

        self.source_filter.blockSignals(True)
        self.source_filter.clear()
        self.source_filter.addItem("Действия сотрудников", "desktop")
        self.source_filter.addItem("Восстановления", "archive")
        self.source_filter.addItem("Автоматика", "retention")
        self.source_filter.addItem("Все записи", "")
        self.source_filter.setCurrentIndex(0)
        self.source_filter.blockSignals(False)

        self.table.setSortingEnabled(False)
        header = self.table.horizontalHeader()
        for column in range(7):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)
        for column, width in enumerate((150, 110, 155, 150, 225, 135, 95)):
            self.table.setColumnWidth(column, width)
        self.connection_note.setText("Журнал загружается при открытии раздела и по кнопке «Обновить».")

    def refresh(self, silent: bool = False) -> None:
        if self._loading:
            return
        config = self.cloud.store.load()
        if not config.username or not config.password:
            self.connection_note.setText("LinkVideo.Cloud не настроен. Укажите подключение в разделе «Настройки».")
            self.table.setRowCount(0)
            return
        self.cloud.set_config(config, save=False)
        self._loading = True
        self.refresh_button.setEnabled(False)
        if not silent:
            self.task.show()
            self.task.busy("Обновляю журнал", config.base_url)
        login = self.login_filter.text().strip()
        source = str(self.source_filter.currentData() or "")

        def worker():
            try:
                rows = self.cloud.activity(120, login=login, source=source)
                self.rowsReady.emit(rows, None)
            except Exception as exc:
                self.rowsReady.emit([], exc)

        threading.Thread(target=worker, daemon=True, name="vpnsync-audit-load").start()

    def rows_ready(self, rows, error) -> None:
        self._loading = False
        self.refresh_button.setEnabled(True)
        if error is not None:
            self.connection_note.setText(f"LinkVideo.Cloud недоступен: {error}")
            self.task.show()
            self.task.error("Не удалось загрузить журнал", str(error))
            return

        records = list(rows or [])
        source = str(self.source_filter.currentData() or "")
        if source == "desktop":
            # Successful cloud logins are transport noise, not VPN operator work.
            records = [row for row in records if str(row.get("action") or "") != "auth.login"]

        self.table.setUpdatesEnabled(False)
        try:
            self.table.setSortingEnabled(False)
            self.table.setRowCount(len(records))
            for row_index, row in enumerate(records):
                source_raw = str(row.get("source") or "")
                action_raw = str(row.get("action") or "")
                values = [
                    self._time_text(row.get("created_at")),
                    page_module._SOURCE_LABELS.get(source_raw, source_raw or "—"),
                    str(row.get("server") or "—"),
                    str(row.get("login") or "—"),
                    page_module._ACTION_LABELS.get(action_raw, action_raw or "—"),
                    str(row.get("actor") or "—"),
                    "Успешно" if bool(row.get("success", True)) else "Ошибка",
                    self._details_text(row),
                ]
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    if column == 6:
                        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    self.table.setItem(row_index, column, item)
        finally:
            self.table.setUpdatesEnabled(True)
            self.table.viewport().update()

        mode_text = self.source_filter.currentText()
        self.connection_note.setText(f"{mode_text} · показано {len(records)} · обновление вручную · UTC+7")
        if self.task.isVisible():
            self.task.done("Журнал обновлён", f"Записей: {len(records)}")
            QTimer.singleShot(1000, self.task.hide)

    def start_stream(self) -> None:
        # High-frequency RouterOS SSE previously rebuilt a 300-row QTableWidget
        # after nearly every event and could freeze the whole desktop UI.
        return None

    def stream_wake(self) -> None:
        return None

    def on_activated(self) -> None:
        self.refresh(silent=True)

    def on_deactivated(self) -> None:
        self._stream_stop.set()

    VPNActivityPage.__init__ = init
    VPNActivityPage.refresh = refresh
    VPNActivityPage._on_rows_ready = rows_ready
    VPNActivityPage._start_stream = start_stream
    VPNActivityPage._on_stream_wake = stream_wake
    VPNActivityPage.onActivated = on_activated
    VPNActivityPage.onDeactivated = on_deactivated


def _patch_vpn_servers() -> None:
    from linkvideo_vpn_helper.ui.pages.vpn_servers_page import VPNServersPage

    previous_build = VPNServersPage._build
    previous_render_detail = VPNServersPage._render_server_detail

    def render_server_detail(self, host: str):
        result = previous_render_detail(self, host)
        card = getattr(self, "detail_card", None)
        if card is not None:
            card.setVisible(bool(str(host or "").strip()))
        return result

    def build(self):
        previous_build(self)

        # Two metrics are enough for the overview; lifecycle detail lives in
        # the dedicated VPN-clients page and LV state stays visible in the table.
        for name in ("m_installed", "m_quarantine", "start_all_btn", "stop_all_btn", "policy"):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.hide()

        if hasattr(self, "backup_btn"):
            self.backup_btn.setText("Резервная копия серверов")
        if hasattr(self, "install_all_btn"):
            self.install_all_btn.setText("Обновить LV на всех")

        for label in self.findChildren(QLabel):
            text = str(label.text() or "")
            if text.startswith("Состояние vpn*.linkvideo.ru"):
                label.setText("Нагрузка и состояние VPN-серверов.")
            elif text == "Действия со всеми серверами":
                label.setText("Обслуживание")
            elif text == "Памятка состояний VPN-клиентов":
                card = _parent_card(label)
                if card is not None:
                    card.hide()
            elif text.startswith("Безопасный порядок:"):
                label.hide()
            elif text == "База VPN-клиентов":
                label.setText("Аварийный резерв")

        status = getattr(self, "sheets_sync_status", None)
        if status is not None:
            status.setText("Google Sheets · ручной резерв")
        button = getattr(self, "sheets_sync_btn", None)
        if button is not None:
            button.setText("Создать резерв")
            button.setToolTip("Ручной аварийный экспорт текущего состояния VPN в Google Sheets")

        card = getattr(self, "detail_card", None)
        if card is not None and not str(getattr(self, "_selected_host", "") or "").strip():
            card.hide()

    VPNServersPage._render_server_detail = render_server_detail
    VPNServersPage._build = build


def install_operator_ux_polish() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _patch_search_archive()
    _patch_activity_page()
    _patch_vpn_servers()
    _INSTALLED = True
