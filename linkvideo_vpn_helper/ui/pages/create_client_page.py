from __future__ import annotations

import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QTextEdit, QVBoxLayout, QWidget,
)

from linkvideo_vpn_helper.services.errors import classify_exception
from linkvideo_vpn_helper.services.search_service import FastSearchService
from linkvideo_vpn_helper.services.server_registry import ServerRegistry
from linkvideo_vpn_helper.services.vpn_service import ClientRecord, SessionCredentials, VPNService
from linkvideo_vpn_helper.ui.components import Card, CounterControl, PageHeader, ServerPicker, TaskStatus, button_feedback, build_page_scaffold
from linkvideo_vpn_helper.ui.dialogs import AddServerDialog


class CreateClientPage(QWidget):
    resultReady = Signal(object, object)
    progressReady = Signal(str, str, int)
    checkReady = Signal(object)
    suggestionReady = Signal(str, object, str)

    MAX_CONTENT_WIDTH = 1120

    def __init__(self, service: VPNService, search: FastSearchService, credentials: SessionCredentials, registry: ServerRegistry, parent=None):
        super().__init__(parent)
        self.service = service
        self.search = search
        self.credentials = credentials
        self.registry = registry
        self._last_records: list[ClientRecord] = []
        self._retry_action = None
        self._cancel_event = None
        self.resultReady.connect(self._on_result)
        self.progressReady.connect(self._on_progress)
        self.checkReady.connect(self._on_check)
        self.suggestionReady.connect(self._on_suggestion)
        self._build()

    def _build(self):
        scroll, canvas, root = build_page_scaffold(self, max_width=1360, min_width=860, margins=28, spacing=16)
        self.page_scroll = scroll
        self.page_canvas = canvas
        self.page_layout = root

        root.addWidget(PageHeader("Создание клиента", "Новая VPN-учётная запись без лишних шагов. Выберите сервер, задайте логин и параметры."))

        self.workspace = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.workspace.setSpacing(16)

        form = Card(kind="hero")
        fl = QVBoxLayout(form)
        fl.setContentsMargins(22, 20, 22, 22)
        fl.setSpacing(16)

        server_label = QLabel("VPN-сервер")
        server_label.setObjectName("CardTitle")
        self.server_picker = ServerPicker(self.registry, allow_auto=False)
        self.server_picker.addRequested.connect(self._add_server)
        self.server_picker.refresh()
        fl.addWidget(server_label)
        fl.addWidget(self.server_picker)

        login_header = QHBoxLayout()
        login_label = QLabel("Логин клиента")
        login_label.setObjectName("CardTitle")
        login_header.addWidget(login_label)
        login_header.addStretch(1)
        fl.addLayout(login_header)
        login_row = QHBoxLayout()
        login_row.setSpacing(8)
        self.login = QLineEdit()
        self.login.setPlaceholderText("Например, 890000001")
        self.login.returnPressed.connect(self._check_login)
        self.login.textChanged.connect(self._login_changed)
        self.btn_check = QPushButton("Проверить")
        self.btn_check.clicked.connect(self._check_login)
        login_row.addWidget(self.login, 1)
        login_row.addWidget(self.btn_check)
        fl.addLayout(login_row)

        params = Card(kind="accent")
        pl = QBoxLayout(QBoxLayout.Direction.LeftToRight, params)
        self.counters_row = pl
        pl.setContentsMargins(14, 12, 14, 12)
        pl.setSpacing(22)
        ports_box = QVBoxLayout(); ports_box.setSpacing(5)
        ports_label = QLabel("Порты"); ports_label.setObjectName("TinyMuted")
        self.ports = CounterControl(3, 1, 14)
        ports_box.addWidget(ports_label); ports_box.addWidget(self.ports)
        accounts_box = QVBoxLayout(); accounts_box.setSpacing(5)
        accounts_label = QLabel("Учётки"); accounts_label.setObjectName("TinyMuted")
        self.accounts = CounterControl(1, 1, 20)
        accounts_box.addWidget(accounts_label); accounts_box.addWidget(self.accounts)
        pl.addLayout(ports_box, 1); pl.addLayout(accounts_box, 1)
        fl.addWidget(params)

        self.actions_row = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.actions_row.setSpacing(8)
        self.btn_create = QPushButton("Создать клиента")
        self.btn_create.setProperty("role", "primary")
        self.btn_create.setMinimumHeight(46)
        self.btn_create.clicked.connect(lambda: self._start_create(False))
        self.btn_create_auto = QPushButton("Подобрать сервер автоматически")
        self.btn_create_auto.setMinimumHeight(46)
        self.btn_create_auto.clicked.connect(lambda: self._start_create(True))
        self.actions_row.addWidget(self.btn_create, 2)
        self.actions_row.addWidget(self.btn_create_auto, 1)
        fl.addLayout(self.actions_row)
        self.workspace.addWidget(form, 6)

        self.result = Card()
        rl = QVBoxLayout(self.result)
        rl.setContentsMargins(22, 20, 22, 22)
        rl.setSpacing(12)
        result_title = QLabel("Данные клиента")
        result_title.setObjectName("SectionTitle")
        result_hint = QLabel("После создания здесь появятся сервер, логин, пароль, адрес и новые порты.")
        result_hint.setObjectName("Muted")
        result_hint.setWordWrap(True)
        rl.addWidget(result_title); rl.addWidget(result_hint)
        self.result_text = QTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setMinimumHeight(250)
        self.result_text.setPlaceholderText("Клиент ещё не создан")
        rl.addWidget(self.result_text, 1)
        self.copy_all = QPushButton("Копировать данные")
        self.copy_all.setEnabled(False)
        self.copy_all.clicked.connect(self._copy_all)
        rl.addWidget(self.copy_all, 0, Qt.AlignmentFlag.AlignLeft)
        self.workspace.addWidget(self.result, 4)
        root.addLayout(self.workspace)

        self.task = TaskStatus()
        self.task.hide()
        self.task.retryRequested.connect(self._retry)
        root.addWidget(self.task)
        root.addStretch(1)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "workspace"):
            self.workspace.setDirection(QBoxLayout.Direction.TopToBottom if self.width() < 1080 else QBoxLayout.Direction.LeftToRight)
        if hasattr(self, "counters_row"):
            self.counters_row.setDirection(QBoxLayout.Direction.LeftToRight)
        if hasattr(self, "actions_row"):
            self.actions_row.setDirection(QBoxLayout.Direction.TopToBottom if self.width() < 980 else QBoxLayout.Direction.LeftToRight)

    def _begin_operation(self):
        event = threading.Event()
        self._cancel_event = event
        return event

    def cancel_current_action(self) -> bool:
        event = self._cancel_event
        if event is None or event.is_set():
            return False
        event.set()
        self._cancel_event = None
        self._set_actions_enabled(True)
        self.task.show()
        self.task.warning("Операция остановлена", "Действие отменено клавишей Esc. Уже отправленная команда MikroTik может завершиться на сервере, но Helper больше не продолжает цепочку.")
        return True

    def _login_changed(self):
        self.login.setProperty("success", "false")
        self.login.setProperty("error", "false")
        self.login.style().unpolish(self.login)
        self.login.style().polish(self.login)

    def _add_server(self):
        dialog = AddServerDialog(self.registry, self)
        if dialog.exec() and dialog.server:
            self.server_picker.refresh()
            self.server_picker.setHost(dialog.server.host)

    def _set_actions_enabled(self, enabled: bool):
        self.btn_create.setEnabled(enabled)
        self.btn_create_auto.setEnabled(enabled)
        self.btn_check.setEnabled(enabled)

    def _check_login(self):
        query = self.login.text().strip()
        if not query:
            self.task.show()
            self.task.warning("Введите логин", "Проверка начнётся после ввода логина клиента.")
            return
        selected = self.server_picker.host()
        if not selected:
            self.task.show()
            self.task.warning("Выберите сервер", "Проверка свободного логина выполняется на выбранном VPN-сервере.")
            return
        cancel_event = self._begin_operation()
        self._set_actions_enabled(False)
        self.task.show()
        self.task.busy("Проверяю логин", f"Проверяю {selected}…")

        def worker():
            report = self.search.search_exact_login(selected, self.credentials, query)
            if not cancel_event.is_set() and self._cancel_event is cancel_event:
                self.checkReady.emit(report)

        threading.Thread(target=worker, daemon=True).start()

    def _on_check(self, report):
        cancel_event = self._cancel_event
        if cancel_event is None or cancel_event.is_set():
            return
        if report.matches:
            original = self.login.text().strip()
            servers = ", ".join(x.server for x in report.matches)
            self.login.setProperty("error", "true")
            self.login.style().unpolish(self.login)
            self.login.style().polish(self.login)
            self.task.busy("Логин уже занят", f"Найден на: {servers}. Подбираю следующий свободный логин…")

            selected = self.server_picker.host()

            def worker():
                try:
                    suggestion = self.service.suggest_next_login(selected, self.credentials, original)
                    if not cancel_event.is_set() and self._cancel_event is cancel_event:
                        self.suggestionReady.emit(suggestion, [], original)
                except Exception as exc:
                    if not cancel_event.is_set() and self._cancel_event is cancel_event:
                        self.suggestionReady.emit("", [classify_exception(exc)], original)

            threading.Thread(target=worker, daemon=True).start()
            return

        self._set_actions_enabled(True)
        self._cancel_event = None
        self.login.setProperty("error", "false")
        self.login.setProperty("success", "true")
        self.login.style().unpolish(self.login)
        self.login.style().polish(self.login)
        if report.errors:
            failed_servers = {str(x.server).lower() for x in report.errors}
            successful = max(0, int(report.checked) - len(failed_servers))
            self.task.warning(
                "Совпадений не найдено на доступных серверах",
                f"Успешно проверено {successful}/{report.total}. Недоступные серверы будут повторно проверены при создании.",
            )
        else:
            self.task.done("Логин свободен", f"Свободен на {self.server_picker.host()}")

    def _on_suggestion(self, suggestion: str, errors, original: str):
        self._set_actions_enabled(True)
        self._cancel_event = None
        if not suggestion:
            self.task.error("Не удалось подобрать логин", "Попробуйте проверить логин ещё раз.")
            return
        self.login.blockSignals(True)
        self.login.setText(suggestion)
        self.login.blockSignals(False)
        self.login.setProperty("error", "false")
        self.login.setProperty("success", "true")
        self.login.style().unpolish(self.login)
        self.login.style().polish(self.login)
        suffix = f" Не удалось проверить серверов: {len(errors)}." if errors else ""
        self.task.warning("Логин был занят — подставлен свободный", f"{original} → {suggestion}.{suffix}")

    def _russian_hosts(self) -> list[str]:
        return [x.host for x in self.registry.all(include_disabled=False) if x.country == "Россия"]

    def _start_create(self, auto_pick: bool = False):
        login = self.login.text().strip()
        if not login:
            self.task.show()
            self.task.warning("Введите логин", "Поле логина не может быть пустым.")
            return
        self._retry_action = lambda: self._start_create(auto_pick)
        cancel_event = self._begin_operation()
        self._set_actions_enabled(False)
        self.task.show()
        self.task.busy("Подготовка", "Проверяю логин, сервер и свободные адреса…")
        ports = self.ports.value()
        accounts = self.accounts.value()
        selected = self.server_picker.host()

        def worker():
            try:
                chosen = selected
                if auto_pick:
                    russian = self._russian_hosts()
                    if not russian:
                        raise RuntimeError("Нет активных российских VPN-серверов для автоматического выбора")
                    self.progressReady.emit(
                        "Подбираю VPN-сервер",
                        f"Параллельно проверяю {len(russian)} российских серверов…",
                        15,
                    )
                    chosen = self.service.pick_best_server_parallel(russian, self.credentials, cancel_event=cancel_event)
                if not chosen:
                    raise RuntimeError("VPN-сервер не выбран")

                # Логин обязан быть уникальным только внутри выбранного MikroTik.
                # Одинаковый базовый логин на vpn04 и kz-vpn01 допустим и не должен
                # искусственно превращаться в _3 из-за записей на других серверах.
                planned_login = login
                try:
                    suggested = self.service.suggest_next_login(chosen, self.credentials, login)
                    if suggested:
                        planned_login = suggested
                    if planned_login != login and not cancel_event.is_set():
                        self.progressReady.emit("Логин занят", f"На {chosen} будет использован {planned_login}", 22)
                except Exception:
                    planned_login = login

                self.progressReady.emit("Создаю клиента", f"{chosen} · проверяю IP и свободные NAT-порты…", 30)

                def create_progress(done, total, current_login):
                    if cancel_event.is_set() or self._cancel_event is not cancel_event:
                        return
                    pct = 35 + int((done / max(1, total)) * 60)
                    self.progressReady.emit("Создаю учётные записи", f"{done}/{total} · {current_login}", min(95, pct))

                records = self.service.create_clients_batch(
                    chosen, self.credentials, planned_login, ports, accounts, create_progress, cancel_event
                )
                if not cancel_event.is_set() and self._cancel_event is cancel_event:
                    self.resultReady.emit(records, None)
            except Exception as exc:
                if not cancel_event.is_set() and self._cancel_event is cancel_event:
                    self.resultReady.emit(None, classify_exception(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _on_progress(self, title: str, detail: str, progress: int):
        self.task.busy(title, detail, progress)

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())

    def _on_result(self, records, error):
        self._set_actions_enabled(True)
        self._cancel_event = None
        if error:
            detail = getattr(error, "message", None) or str(error)
            self.task.error("Создать клиента не удалось", detail, retry=True)
            return
        self._last_records = list(records or [])
        if not self._last_records:
            self.task.error("Клиент не создан", "MikroTik не вернул созданные данные.")
            return
        chosen = self._last_records[0].server
        actual_login = self._last_records[0].login
        if actual_login and self.login.text().strip() != actual_login:
            self.login.blockSignals(True)
            self.login.setText(actual_login)
            self.login.blockSignals(False)
        self.task.done("Готово", f"Создано учётных записей: {len(self._last_records)} · сервер {chosen}")
        self.result_text.setPlainText("\n\n".join(record.copy_text() for record in self._last_records))
        self.copy_all.setEnabled(True)
        self.result.show()

    def _record_card(self, record: ClientRecord) -> QWidget:
        card = Card(subtle=True)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(15, 13, 15, 13)
        lay.setSpacing(7)

        top = QHBoxLayout()
        title = QLabel(record.login)
        title.setObjectName("SectionTitle")
        server = QLabel(record.server)
        server.setObjectName("Muted")
        country = QLabel(self.registry.get(record.server).country)
        country.setObjectName("Muted")
        top.addWidget(title)
        top.addStretch(1)
        top.addWidget(server)
        top.addSpacing(12)
        top.addWidget(country)
        lay.addLayout(top)

        text = QLabel(
            f"Пароль: {record.password}\n"
            f"Remote Address: {record.remote_address}\n"
            f"Порты: {', '.join(str(x) for x in record.ports) if record.ports else '—'}"
        )
        text.setObjectName("Value")
        text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(text)
        return card

    def _copy_all(self):
        if not self._last_records:
            return
        QGuiApplication.clipboard().setText("\n\n".join(x.copy_text() for x in self._last_records))
        button_feedback(self.copy_all, "✓ Скопировано")

    def _retry(self):
        if callable(self._retry_action):
            self._retry_action()

    def refresh_servers(self):
        self.server_picker.refresh()
