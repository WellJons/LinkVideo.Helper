from __future__ import annotations

"""Offscreen visual smoke: login errors and themed modal dialogs.

The test never connects to MikroTik, CRM or B2O.
"""

import os
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QFrame, QLabel

from linkvideo_vpn_helper.theme import get_theme_style
from linkvideo_vpn_helper.ui.login_window import LoginWindow
from linkvideo_vpn_helper.ui.dialogs import (
    AddServerDialog, B2OLoginDialog, ConfirmDialog, CountDialog,
    PasswordDialog, style_helper_dialog,
)

app = QApplication.instance() or QApplication([])
message = (
    "Ни один VPN-сервер не завершил проверку входа. Проверьте доступность "
    "RouterOS API (8728/8729). Техническая причина: соединение прервано, "
    "данные не подтверждены и требуется проверить маршрут к серверам. "
    "Дополнительное подробное диагностическое сообщение о нескольких "
    "недоступных VPN-серверах должно полностью помещаться в плашку."
)

with tempfile.TemporaryDirectory() as folder:
    settings = QSettings(str(Path(folder) / "ui.ini"), QSettings.Format.IniFormat)
    settings.setValue("remember", False)
    settings.setValue("username", "")
    settings.setValue("password", "")
    settings.setValue("ui/theme_v2", "linkvideo_2026")

    login = LoginWindow(settings)
    login.show()
    for size in ((1080, 680), (760, 620), (950, 720)):
        login.resize(*size)
        app.processEvents()
        login._status(message, danger=True)
        app.processEvents()
        assert login.error_banner.isVisible(), f"No error visible at {size}"
        assert login.error_banner.height() >= login.error_banner.minimumHeight(), size
        assert login.error.height() >= login.error.minimumHeight(), size
        assert login.error.text() == message
        assert login.stage_scroll.widgetResizable()
        assert login.stage_scroll.verticalScrollBarPolicy() != Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        assert login.error_banner.minimumHeight() >= login.error.minimumHeight() + 20
        # Text should be allowed to expand, not clipped into the fixed 76px.
        assert login.error.minimumHeight() > 44, (size, login.error.minimumHeight())
        login._status("")
        app.processEvents()
        assert not login.error_banner.isVisible()
    login.close()

    for theme in ("linkvideo_2026", "midnight_purple"):
        app.setStyleSheet(get_theme_style(theme))
        confirm = ConfirmDialog(
            "Подтверждение изменения тарифа",
            "Клиент: тестовый. Камеры: 4. Новый тариф: ЮЛ 90 дней. "
            "Дата: завтра. Изменение будет применено после подтверждения. "
            "Проверьте стоимость, срок и количество выбранных камер.",
            "Применить тариф", parent=None,
        )
        confirm.show()
        app.processEvents()
        assert confirm.property("helperModal") is True
        body = confirm.findChild(QFrame, "DialogBody")
        assert body is not None and body.isVisible()
        description = confirm.findChild(QLabel, "DialogDescription")
        assert description is not None and description.wordWrap()
        assert description.height() >= description.minimumHeight()
        assert "QDialog[helperModal=" in app.styleSheet()
        confirm.close()

        for modal in (
            CountDialog("Количество камер", "Выберите число камер."),
            PasswordDialog("fake-pass"),
            B2OLoginDialog("fictional"),
        ):
            modal.show()
            app.processEvents()
            assert modal.property("helperModal") is True, type(modal).__name__
            assert modal.width() >= modal.minimumWidth()
            modal.close()

    app.setStyleSheet(get_theme_style("linkvideo_2026"))
    app.processEvents()

print("CORE TESTS THEMED MODALS AND LOGIN ERROR WRAPPING OK")
