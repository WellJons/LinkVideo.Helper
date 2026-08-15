from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QDialog, QHBoxLayout, QLabel, QProgressBar, QVBoxLayout, QWidget

from linkvideo_vpn_helper.version import APP_NAME, APP_VERSION


class StartupSplash(QWidget):
    """Очень лёгкое стартовое окно: не задерживает запуск искусственно."""

    def __init__(self, theme_style: str):
        super().__init__(None, Qt.WindowType.SplashScreen | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("AppRoot")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setFixedSize(410, 178)
        self.setStyleSheet(theme_style)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 22)
        layout.setSpacing(7)
        brand_row = QHBoxLayout()
        brand_row.setSpacing(12)
        icon_label = QLabel()
        icon_label.setFixedSize(46, 46)
        icon_path = Path(__file__).resolve().parents[1] / "icon.ico"
        if icon_path.exists():
            icon_label.setPixmap(QIcon(str(icon_path)).pixmap(46, 46))
        title = QLabel("LinkVideo.Helper")
        title.setObjectName("PageTitle")
        brand_row.addWidget(icon_label)
        brand_row.addWidget(title, 1)
        version = QLabel(f"Версия {APP_VERSION}")
        version.setObjectName("TinyMuted")
        self.status = QLabel("Запуск…")
        self.status.setObjectName("Muted")
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        layout.addStretch(1)
        layout.addLayout(brand_row)
        layout.addWidget(version)
        layout.addSpacing(6)
        layout.addWidget(self.status)
        layout.addWidget(self.progress)
        layout.addStretch(1)

    def set_status(self, text: str):
        self.status.setText(text)
        QApplication.processEvents()


def _migrate_settings(settings: QSettings):
    # 1.1.x уже использовал то же имя QSettings. Сохраняем логин/пароль/B2O.
    # Тема имела другие названия — переводим их в компактную схему 2.0.
    if not settings.contains("ui/theme_v2"):
        old = str(settings.value("ui/theme", "", str) or "").lower()
        if any(x in old for x in ("dark", "night", "graphite")):
            settings.setValue("ui/theme_v2", "dark")
        elif old:
            settings.setValue("ui/theme_v2", "light")
        else:
            legacy = QSettings("LinkVideo", "VPNHelper")
            legacy_theme = str(legacy.value("ui/theme", "", str) or "").lower()
            # На чистой установке фирменная LinkVideo — стандартная тема.
            # Явную старую тёмную тему при обновлении у пользователя не отбираем.
            settings.setValue("ui/theme_v2", "dark" if "dark" in legacy_theme else "linkvideo_2026")

    # Если приложение когда-то хранило учётные данные в старом VPNHelper,
    # переносим их только когда актуальные значения отсутствуют.
    if not str(settings.value("username", "", str) or "").strip():
        legacy = QSettings("LinkVideo", "VPNHelper")
        old_user = str(legacy.value("username", "", str) or "").strip()
        old_password = str(legacy.value("password", "", str) or "")
        if old_user and old_password:
            settings.setValue("username", old_user)
            settings.setValue("password", old_password)
            settings.setValue("remember", bool(legacy.value("remember", True, bool)))


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName("LinkVideo")
    app.setQuitOnLastWindowClosed(True)

    # Busy network operations that expose cancel_current_action() must remain
    # cancellable by Esc and must not disable the main window's close button.
    from linkvideo_vpn_helper.ui.operation_cancel_guard import install_operation_cancel_guard
    install_operation_cancel_guard()

    # Port connection tracking is attached only to an opened client card. Search
    # itself stays conntrack-free and therefore cannot become slower because of
    # the live per-port indicators.
    from linkvideo_vpn_helper.ui.port_traffic_inline import install_inline_port_traffic
    install_inline_port_traffic()

    # Exact-version patch releases are downloaded without a confirmation dialog
    # and applied by the pre-registered SYSTEM updater when Helper closes. Full
    # installers intentionally keep the normal visible confirmation flow.
    from linkvideo_vpn_helper.ui.silent_update_integration import install_silent_patch_updates
    install_silent_patch_updates()

    settings = QSettings("LinkVideo", "LinkVideo.Helper")
    _migrate_settings(settings)

    # Keep the historical settings key but replace its visible palette/name with
    # the visual language of LinkVideo.Monitor.
    from linkvideo_vpn_helper.brand_theme import install_linkvideo_brand_theme
    install_linkvideo_brand_theme()

    from linkvideo_vpn_helper.theme import get_theme_style
    theme_style = get_theme_style(str(settings.value("ui/theme_v2", "linkvideo_2026", str) or "linkvideo_2026"))
    app.setStyleSheet(theme_style)

    saved_username = str(settings.value("username", "", str) or "").strip()
    saved_password = str(settings.value("password", "", str) or "")
    remember = bool(settings.value("remember", True, bool))

    if remember and saved_username and saved_password:
        credential_values = (saved_username, saved_password)
    else:
        from linkvideo_vpn_helper.ui.login_window import LoginWindow
        login = LoginWindow(settings)
        if login.exec() != QDialog.DialogCode.Accepted or login.payload is None:
            return 0
        credential_values = (login.payload.username, login.payload.password)

    # Тяжёлые страницы по-прежнему не импортируются здесь — MainWindow создаёт
    # их только при первом открытии соответствующего раздела.
    splash = StartupSplash(theme_style)
    splash.show()
    splash.set_status("Загружаю ядро…")

    from linkvideo_vpn_helper.services.vpn_service import SessionCredentials, VPNService
    credentials = SessionCredentials(credential_values[0], credential_values[1], 8728, 4.5)
    service = VPNService()

    splash.set_status("Открываю интерфейс…")
    from linkvideo_vpn_helper.ui.main_window import MainWindow
    window = MainWindow(service, credentials, settings)
    window.show()
    splash.close()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())