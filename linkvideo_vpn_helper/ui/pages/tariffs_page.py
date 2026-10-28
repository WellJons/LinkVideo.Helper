from __future__ import annotations

"""Billing-first Tariffs workspace.

Until the corporate billing API is documented this page deliberately performs
no tariff reads or writes. The external login is the only live operation.
"""

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from linkvideo_vpn_helper.ui.components import (
    Card, PageHeader, StatusPill, TaskStatus, build_page_scaffold,
)


BILLING_LOGIN_URL = "https://crm.linkvideo.ru/login"


class TariffsPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("TariffsPage")
        self._build()

    def _build(self):
        self.page_scroll, self.page_canvas, root = build_page_scaffold(
            self, max_width=1240, min_width=740, margins=22, spacing=13,
        )
        self.page_layout = root
        root.addWidget(PageHeader(
            "Тарифы",
            "Новый биллинг LinkVideo, КЦ и «Юля» — единое рабочее пространство.",
        ))

        self.task = TaskStatus()
        self.task.hide()
        root.addWidget(self.task)

        primary = Card(kind="hero")
        layout = QVBoxLayout(primary)
        layout.setContentsMargins(21, 19, 21, 19)
        layout.setSpacing(13)

        line = QHBoxLayout()
        title = QLabel("Новый биллинг")
        title.setObjectName("SectionTitle")
        self.api_status = StatusPill("API не подключён", "warning")
        line.addWidget(title)
        line.addStretch(1)
        line.addWidget(self.api_status)
        layout.addLayout(line)

        hint = QLabel(
            "Корпоративный кабинет доступен по crm.linkvideo.ru. "
            "Сейчас его можно открыть в браузере под своей учётной записью. "
            "Прямой просмотр и изменение тарифов появятся после согласования API."
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.open_billing = QPushButton("Открыть биллинг")
        self.open_billing.setObjectName("OpenBillingButton")
        self.open_billing.setProperty("role", "primary")
        self.open_billing.setMinimumHeight(44)
        self.open_billing.clicked.connect(self._open_billing)
        actions.addWidget(self.open_billing)
        actions.addStretch(1)
        layout.addLayout(actions)

        url_note = QLabel(BILLING_LOGIN_URL)
        url_note.setObjectName("TinyMuted")
        url_note.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(url_note)
        root.addWidget(primary)

        placeholder = Card()
        body = QVBoxLayout(placeholder)
        body.setContentsMargins(21, 19, 21, 19)
        body.setSpacing(8)
        section_title = QLabel("Данные о тарифах")
        section_title.setObjectName("SectionTitle")
        body.addWidget(section_title)

        empty = QLabel(
            "Подключение к API нового биллинга ещё не настроено. "
            "Данные клиентов, стоимость, сроки оплаты и история здесь пока не отображаются."
        )
        empty.setObjectName("Muted")
        empty.setWordWrap(True)
        empty.setMinimumHeight(90)
        empty.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        body.addWidget(empty)
        root.addWidget(placeholder)

        sources = Card()
        sources_l = QVBoxLayout(sources)
        sources_l.setContentsMargins(21, 17, 21, 17)
        sources_l.setSpacing(10)
        section = QLabel("Источники")
        section.setObjectName("SectionTitle")
        sources_l.addWidget(section)
        for name, detail, status in (
            ("Новый биллинг", "Корпоративный веб-кабинет", "API не подключён"),
            ("КЦ", "Подключение запланировано", "Не подключено"),
            ("Юля", "Подключение запланировано", "Не подключено"),
        ):
            source_row = QHBoxLayout()
            source_row.setSpacing(10)
            group = QVBoxLayout()
            group.setSpacing(3)
            source_title = QLabel(name)
            source_title.setObjectName("Value")
            subtitle = QLabel(detail)
            subtitle.setObjectName("TinyMuted")
            group.addWidget(source_title)
            group.addWidget(subtitle)
            source_row.addLayout(group, 1)
            source_row.addWidget(StatusPill(status, "neutral"))
            sources_l.addLayout(source_row)
        root.addWidget(sources)
        root.addStretch(1)

    def _open_billing(self):
        # No CRM password, cookies, bearer token or employee SSO session is
        # copied into Helper. The default browser owns the authentication.
        if not QDesktopServices.openUrl(QUrl(BILLING_LOGIN_URL)):
            self.task.show()
            self.task.error(
                "Не удалось открыть биллинг",
                "Проверьте браузер по умолчанию и доступность crm.linkvideo.ru.",
            )
