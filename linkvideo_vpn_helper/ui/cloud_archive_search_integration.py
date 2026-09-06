from __future__ import annotations

import threading
from typing import Any

from PySide6.QtCore import QObject, QSettings, QTimer, Signal
from PySide6.QtWidgets import QDialog, QGridLayout, QHBoxLayout, QLabel, QPushButton

from linkvideo_vpn_helper.services.cloud_vpnsync import CloudVPNSyncClient
from linkvideo_vpn_helper.services.vpn_restore_service import DeletedVPNClient
from linkvideo_vpn_helper.ui.components import Card, StatusPill
from linkvideo_vpn_helper.ui.dialogs import ConfirmDialog


_INSTALLED = False


def _ports_text(value: Any) -> str:
    rules = list(value or []) if isinstance(value, list) else []
    parts: list[str] = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        external = int(rule.get("external_port") or 0)
        if not 1 <= external <= 65535:
            continue
        internal = int(rule.get("internal_port") or external)
        protocol = str(rule.get("protocol") or "tcp").lower()
        suffix = " [off]" if bool(rule.get("disabled")) else ""
        parts.append(f"{protocol} {external} → {internal}{suffix}")
    return ", ".join(parts)


def _deleted_record(row: dict[str, Any]) -> DeletedVPNClient | None:
    login = str(row.get("login") or "").strip()
    server = str(row.get("server") or "").strip()
    if not login or not server:
        return None
    return DeletedVPNClient(
        server=server,
        login=login,
        password_saved=bool(row.get("password_saved")),
        remote_address=str(row.get("remote_address") or ""),
        profile=str(row.get("profile") or ""),
        deleted_at=str(row.get("deleted_at") or ""),
        ports=_ports_text(row.get("ports")),
        row={str(key): value for key, value in row.items()},
    )


def _conflict_text(conflicts: list[dict[str, Any]]) -> str:
    labels = {
        "login": "такой PPP-логин уже существует",
        "profile": "такой PPP-профиль уже существует",
        "remote-address-secret": "Remote Address уже используется PPP-учёткой",
        "remote-address-profile": "Remote Address уже используется PPP-профилем",
        "nat-port": "внешний NAT-порт уже занят",
    }
    lines: list[str] = []
    for item in conflicts[:12]:
        kind = str(item.get("type") or "")
        value = item.get("value", "")
        owner = str(item.get("owner") or item.get("comment") or "").strip()
        text = labels.get(kind, kind or "конфликт RouterOS")
        if value not in (None, ""):
            text += f": {value}"
        if owner:
            text += f" · {owner}"
        lines.append("• " + text)
    if len(conflicts) > 12:
        lines.append(f"• ещё конфликтов: {len(conflicts) - 12}")
    return "\n".join(lines)


class _CloudArchiveBridge(QObject):
    deletedSearchReady = Signal(str, object, object)
    preflightReady = Signal(object, object, object)
    restoreReady = Signal(object, object, object)

    def __init__(self, settings: QSettings, parent=None):
        super().__init__(parent)
        self.settings = settings

    def _client(self) -> CloudVPNSyncClient:
        client = CloudVPNSyncClient(self.settings)
        config = client.store.load()
        if not config.username or not config.password:
            raise RuntimeError(
                "Облачный сервер VPNSync не настроен. Укажите адрес и учётную запись в «Настройки»."
            )
        client.set_config(config, save=False)
        return client

    def search_deleted(self, query: str) -> None:
        wanted = str(query or "").strip()

        def worker():
            try:
                payload = self._client().request(
                    "GET",
                    "/v1/deleted/search",
                    params={"q": wanted, "limit": 50},
                )
                records: list[DeletedVPNClient] = []
                for row in list(payload or []):
                    if not isinstance(row, dict):
                        continue
                    record = _deleted_record(row)
                    if record is not None:
                        records.append(record)
                self.deletedSearchReady.emit(wanted, records, None)
            except Exception as exc:
                self.deletedSearchReady.emit(wanted, [], exc)

        threading.Thread(target=worker, daemon=True, name="vpnsync-archive-search").start()

    def preflight(self, record: DeletedVPNClient) -> None:
        def worker():
            try:
                result = self._client().request(
                    "GET",
                    "/v1/archive/restore/preflight",
                    params={"server": record.server, "login": record.login},
                    timeout=20.0,
                )
                self.preflightReady.emit(record, result, None)
            except Exception as exc:
                self.preflightReady.emit(record, None, exc)

        threading.Thread(
            target=worker,
            daemon=True,
            name=f"vpnsync-archive-preflight-{record.login}",
        ).start()

    def restore(self, record: DeletedVPNClient) -> None:
        def worker():
            try:
                result = self._client().request(
                    "POST",
                    "/v1/archive/restore",
                    json={"server": record.server, "login": record.login},
                    timeout=45.0,
                )
                self.restoreReady.emit(record, result, None)
            except Exception as exc:
                self.restoreReady.emit(record, None, exc)

        threading.Thread(
            target=worker,
            daemon=True,
            name=f"vpnsync-archive-restore-{record.login}",
        ).start()


def install_cloud_archive_search() -> None:
    """Use PostgreSQL archive only after the normal direct MikroTik search."""
    global _INSTALLED
    if _INSTALLED:
        return

    from linkvideo_vpn_helper.ui.pages.search_manage_page import SearchManagePage

    original_init = SearchManagePage.__init__

    def init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        parent = kwargs.get("parent")
        if parent is None and len(args) >= 5:
            parent = args[4]
        settings = getattr(parent, "settings", None) or QSettings("LinkVideo", "LinkVideo.Helper")
        bridge = _CloudArchiveBridge(settings, self)
        bridge.deletedSearchReady.connect(self._on_deleted_search)
        bridge.preflightReady.connect(self._cloud_archive_preflight_ready)
        bridge.restoreReady.connect(self._cloud_archive_restore_ready)
        self._cloud_archive_bridge = bridge

    def on_search(self, report):
        self._cancel_event = None
        self.btn_search.setEnabled(True)
        failed_servers = {str(x.server).lower() for x in report.errors}
        successful = max(0, int(report.checked) - len(failed_servers))
        self._active_match_count = len(report.matches)
        self._search_had_errors = bool(report.errors)

        for client in report.matches:
            self._add_result(client)

        archive_lookup = False
        if self._mode == "login" and self._deleted_lookup_query == self.query.text().strip():
            bridge = getattr(self, "_cloud_archive_bridge", None)
            if bridge is not None:
                archive_lookup = True
                self._deleted_lookup_pending = True
                bridge.search_deleted(self._deleted_lookup_query)

        if report.matches:
            self.task.hide()
            self.search_note.clear()
        elif report.errors:
            self.task.warning(
                "Клиент не найден на всех доступных серверах",
                f"Успешно проверено {successful}/{report.total}. {len(report.errors)} сервер(ов) проверить не удалось.",
            )
            self.search_note.setText(
                "Не удалось проверить:\n"
                + "\n".join(f"• {e.server}: {e.error.message}" for e in report.errors[:10])
            )
        elif archive_lookup:
            self.task.busy(
                "Проверяю удалённые учётки",
                "Ищу логин в архиве PostgreSQL после проверки живых MikroTik…",
            )
            self.search_note.clear()
        else:
            self.task.done("Клиент не найден", f"Проверено серверов: {successful}/{report.total}")
            self.search_note.clear()

        if self.results.count():
            self.results.setCurrentRow(-1)
            self.open_hint.setText("Клик по записи — открыть карточку")
        elif archive_lookup:
            self.open_hint.setText("Проверяю архив удалённых…")
        else:
            self.open_hint.setText("Совпадений нет")

    def render_deleted(self):
        record = self._deleted_current
        if record is None:
            return
        self._clear_detail()

        header_row = QHBoxLayout()
        title = QLabel(f"Клиент {record.login}")
        title.setObjectName("SectionTitle")
        header_row.addWidget(title)
        header_row.addStretch(1)
        header_row.addWidget(StatusPill("Удалён", "warning"))
        self.detail_l.addLayout(header_row)

        info = Card(subtle=True)
        grid = QGridLayout(info)
        grid.setContentsMargins(14, 12, 14, 12)
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(8)
        values = (
            ("VPN-сервер", record.server),
            ("Удалён", record.deleted_at or "—"),
            ("Remote Address", record.remote_address or "—"),
            ("Profile", record.profile or "—"),
            ("NAT / Порты", record.ports or "—"),
            ("Пароль в PostgreSQL", "Сохранён" if record.password_saved else "НЕ СОХРАНЁН"),
        )
        for index, (label_text, value_text) in enumerate(values):
            label = QLabel(label_text)
            label.setObjectName("TinyMuted")
            value = QLabel(str(value_text))
            value.setObjectName("Value")
            value.setWordWrap(True)
            grid.addWidget(label, index, 0)
            grid.addWidget(value, index, 1)
        self.detail_l.addWidget(info)

        note = QLabel(
            (
                "Восстановление выполняется из PostgreSQL. Перед любой записью VPNSync заново читает живой MikroTik. "
                "Если уже существует этот логин, профиль, Remote Address или занят хотя бы один старый внешний NAT-порт, "
                "операция полностью блокируется. Существующие объекты не перезаписываются и порты автоматически не заменяются."
            )
            if record.password_saved
            else (
                "Старый пароль не сохранён в резервной записи. Автоматическое восстановление заблокировано, "
                "чтобы не создать клиенту другой пароль."
            )
        )
        note.setObjectName("Muted" if record.password_saved else "DangerText")
        note.setWordWrap(True)
        self.detail_l.addWidget(note)

        actions = QHBoxLayout()
        actions.addStretch(1)
        restore = QPushButton("Восстановить клиента")
        restore.setProperty("role", "primary")
        restore.setEnabled(bool(record.password_saved) and not self._action_busy)
        restore.clicked.connect(self._restore_deleted)
        actions.addWidget(restore)
        self._deleted_restore_btn = restore
        self.detail_l.addLayout(actions)
        self.detail_l.addStretch(1)

    def restore_deleted(self):
        record = self._deleted_current
        if record is None or self._action_busy or not record.password_saved:
            return
        bridge = getattr(self, "_cloud_archive_bridge", None)
        if bridge is None:
            self.client_task.show()
            self.client_task.error("Восстановление недоступно", "Модуль PostgreSQL recovery не инициализирован")
            return
        self._action_busy = True
        if hasattr(self, "_deleted_restore_btn"):
            self._deleted_restore_btn.setEnabled(False)
        self.client_task.show()
        self.client_task.busy(
            "Проверяю MikroTik перед восстановлением",
            f"{record.login} · {record.server} · пока ничего не записывается",
        )
        bridge.preflight(record)

    def preflight_ready(self, record, result, error):
        if self._deleted_current is None or self._deleted_current.login != record.login or self._deleted_current.server != record.server:
            self._action_busy = False
            return
        if error is not None:
            self._action_busy = False
            self.client_task.show()
            self.client_task.error("Проверка перед восстановлением не выполнена", str(error))
            if hasattr(self, "_deleted_restore_btn"):
                self._deleted_restore_btn.setEnabled(True)
            return

        conflicts = list((result or {}).get("conflicts") or [])
        if not bool((result or {}).get("ok")) or conflicts:
            self._action_busy = False
            self.client_task.show()
            self.client_task.error(
                "Восстановление заблокировано",
                _conflict_text(conflicts) or "На живом MikroTik обнаружен конфликт. Ничего не изменено.",
            )
            if hasattr(self, "_deleted_restore_btn"):
                self._deleted_restore_btn.setEnabled(True)
            return

        ports = ", ".join(str(value) for value in list((result or {}).get("ports") or [])) or "—"
        dialog = ConfirmDialog(
            "Восстановить удалённого клиента?",
            f"{record.server}\nЛогин: {record.login}\nRemote Address: {record.remote_address or '—'}\n"
            f"Старые внешние порты: {ports}\n\n"
            "Живой MikroTik только что проверен: конфликтов нет. Повторная проверка будет выполнена сервером непосредственно перед записью.",
            confirm_text="Восстановить",
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self._action_busy = False
            self.client_task.hide()
            if hasattr(self, "_deleted_restore_btn"):
                self._deleted_restore_btn.setEnabled(True)
            return

        bridge = getattr(self, "_cloud_archive_bridge", None)
        if bridge is None:
            self._action_busy = False
            return
        self.client_task.show()
        self.client_task.busy("Восстанавливаю клиента", f"{record.login} · {record.server}")
        bridge.restore(record)

    def restore_ready(self, record, result, error):
        self._action_busy = False
        if error is not None:
            self.client_task.show()
            self.client_task.error("Восстановление не выполнено", str(error))
            if hasattr(self, "_deleted_restore_btn"):
                self._deleted_restore_btn.setEnabled(True)
            return

        payload = dict(result or {})
        restored_objects = int(payload.get("restored_objects") or 0)
        self.client_task.show()
        self.client_task.done(
            "Клиент восстановлен",
            f"{record.login} · {record.server} · объектов RouterOS создано: {restored_objects} · PostgreSQL подтверждён повторной синхронизацией",
        )
        self._deleted_current = None
        QTimer.singleShot(
            1400,
            lambda: (
                self._close_client_view(immediate=True),
                self._search(),
            ),
        )

    SearchManagePage.__init__ = init
    SearchManagePage._on_search = on_search
    SearchManagePage._render_deleted_client = render_deleted
    SearchManagePage._restore_deleted = restore_deleted
    SearchManagePage._cloud_archive_preflight_ready = preflight_ready
    SearchManagePage._cloud_archive_restore_ready = restore_ready
    _INSTALLED = True
