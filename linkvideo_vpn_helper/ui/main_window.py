from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QSettings, Qt, Signal, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QMainWindow, QPushButton,
    QVBoxLayout, QWidget,
)

from linkvideo_vpn_helper.services.archive_service import ArchiveService
from linkvideo_vpn_helper.services.search_service import FastSearchService
from linkvideo_vpn_helper.services.server_registry import ServerRegistry
from linkvideo_vpn_helper.services.update_service import UpdateService
from linkvideo_vpn_helper.services.vpn_service import SessionCredentials, VPNService
from linkvideo_vpn_helper.theme import get_theme_style
from linkvideo_vpn_helper.ui.components import AnimatedStack, Card, Toast
from linkvideo_vpn_helper.ui.dialogs import ConfirmDialog
from linkvideo_vpn_helper.version import APP_VERSION


class MainWindow(QMainWindow):
    updateReady = Signal(object, object, bool)
    updateDownloaded = Signal(object, object)

    NAV_ITEMS = (
        ("create", "＋", "Создание клиента", "Новая L2TP-учётная запись"),
        ("search", "⌕", "Поиск и управление", "Клиенты, порты и VPN"),
        ("archive", "↓", "Скачать архив", "Поиск и сохранение MP4"),
        ("diagnostics", "◇", "Диагностика архива", "Покрытие и пропуски"),
        ("inactive", "⌛", "VPN-клиенты", "Состояния и карантин"),
        # Управление инфраструктурой оставляем последним рабочим разделом,
        # а не вклиниваем между ежедневными операциями сотрудников.
        ("vpn_servers", "▦", "VPN-серверы", "Нагрузка, резервные копии и LV"),
    )

    def __init__(self, service: VPNService, credentials: SessionCredentials, settings: QSettings | None = None, parent=None):
        super().__init__(parent)
        self.service = service
        self.credentials = credentials
        self.settings = settings or QSettings("LinkVideo", "LinkVideo.Helper")
        self.registry = ServerRegistry(self.settings)
        self.search = FastSearchService(service)
        self.archive = ArchiveService(self.settings)
        self.updater = UpdateService()
        self._page_cache: dict[str, QWidget] = {}
        self._current_key = ""
        self._collapsed = False
        self._first_show = True
        self._window_fade = None
        self.updateReady.connect(self._on_update_ready)
        self._update_check_busy = False
        self.updateDownloaded.connect(self._on_update_downloaded)

        self.setWindowTitle("LinkVideo.Helper")
        # Стартовый размер: полноценное рабочее окно без избыточного растягивания.
        self.resize(1380, 900)
        self.setMinimumSize(900, 640)
        self._icon_path = Path(__file__).resolve().parents[2] / "icon.ico"
        if self._icon_path.exists():
            self.setWindowIcon(QIcon(str(self._icon_path)))

        self._apply_theme()
        self._build()
        self._go("create")
        # Build the remaining pages only after the main window is visible.
        # This removes the one-frame native/unstyled widget flash on first opening a tab.
        self._preload_queue = ["search", "vpn_servers", "archive", "diagnostics", "inactive", "settings"]
        QTimer.singleShot(350, self._preload_next_page)
        # Автоматически проверяем обновления после запуска, но не показываем
        # пользователю сообщения об успешной проверке/сетевой ошибке.
        QTimer.singleShot(2200, lambda: self._check_updates(startup=True))

    def _apply_theme(self):
        key = str(self.settings.value("ui/theme_v2", "rose_milk", str) or "ocean_blue")
        app = QApplication.instance()
        if app:
            app.setStyleSheet(get_theme_style(key))

    def _build(self):
        root = QWidget()
        root.setObjectName("AppRoot")
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.sidebar = QFrame()
        self.sidebar.setObjectName("Sidebar")
        self.sidebar.setFixedWidth(244)
        sl = QVBoxLayout(self.sidebar)
        sl.setContentsMargins(14, 18, 14, 14)
        sl.setSpacing(5)

        brand = QWidget()
        brand_l = QHBoxLayout(brand)
        brand_l.setContentsMargins(4, 0, 4, 0)
        brand_l.setSpacing(10)
        self.brand_mark = QLabel("LV")
        self.brand_mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.brand_mark.setFixedSize(40, 40)
        self.brand_mark.setObjectName("BrandMark")
        if getattr(self, "_icon_path", None) and self._icon_path.exists():
            self.brand_mark.setPixmap(QIcon(str(self._icon_path)).pixmap(40, 40))
            self.brand_mark.setStyleSheet("background: transparent; border: none;")
        brand_text = QVBoxLayout()
        brand_text.setSpacing(0)
        self.logo = QLabel("LinkVideo.Helper")
        self.logo.setObjectName("AppLogo")
        self.logo_sub = QLabel("Рабочие инструменты")
        self.logo_sub.setObjectName("TinyMuted")
        brand_text.addWidget(self.logo)
        brand_text.addWidget(self.logo_sub)
        brand_l.addWidget(self.brand_mark)
        brand_l.addLayout(brand_text, 1)
        sl.addWidget(brand)

        user_card = Card(subtle=True)
        self.user_card = user_card
        ul = QHBoxLayout(user_card)
        ul.setContentsMargins(11, 9, 9, 9)
        ul.setSpacing(8)
        avatar = QLabel((self.credentials.username[:1] or "U").upper())
        avatar.setObjectName("Avatar")
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setFixedSize(32, 32)
        user_text = QVBoxLayout()
        user_text.setSpacing(0)
        self.user = QLabel(self.credentials.username)
        self.user.setObjectName("Value")
        role = QLabel("MikroTik")
        role.setObjectName("TinyMuted")
        user_text.addWidget(self.user)
        user_text.addWidget(role)
        self.logout = QPushButton("↪")
        self.logout.setProperty("role", "icon")
        self.logout.setToolTip("Выйти из учётной записи")
        self.logout.clicked.connect(self._logout)
        ul.addWidget(avatar)
        ul.addLayout(user_text, 1)
        ul.addWidget(self.logout)
        sl.addWidget(user_card)
        sl.addSpacing(10)

        self.work_label = QLabel("РАБОТА")
        self.work_label.setObjectName("NavSection")
        sl.addWidget(self.work_label)

        self.nav: dict[str, tuple[QPushButton, str, str, str]] = {}
        for key, icon_text, text, hint in self.NAV_ITEMS:
            if key == "vpn_servers":
                infra = QLabel("ИНФРАСТРУКТУРА")
                infra.setObjectName("NavSection")
                sl.addSpacing(6)
                sl.addWidget(infra)
            button = QPushButton(f"{icon_text}    {text}")
            button.setProperty("nav", "true")
            button.clicked.connect(lambda checked=False, k=key: self._go(k))
            self.nav[key] = (button, icon_text, text, hint)
            sl.addWidget(button)

        sl.addStretch(1)
        self.settings_btn = QPushButton("⚙    Настройки")
        self.settings_btn.setProperty("nav", "true")
        self.settings_btn.setProperty("footerNav", "true")
        self.settings_btn.setStyleSheet("QPushButton { text-align:center; padding-left:0px; }")
        self.settings_btn.clicked.connect(lambda: self._go("settings"))
        sl.addWidget(self.settings_btn)

        # Версия находится непосредственно под настройками и визуально
        # центрируется относительно боковой панели.
        self.version = QLabel(f"v{APP_VERSION}")
        self.version.setObjectName("TinyMuted")
        self.version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sl.addWidget(self.version, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.sidebar)

        self.content = QWidget()
        self.content.setObjectName("ContentRoot")
        content_l = QVBoxLayout(self.content)
        content_l.setContentsMargins(0, 0, 0, 0)
        self.stack = AnimatedStack()
        content_l.addWidget(self.stack)
        layout.addWidget(self.content, 1)

        self.toast = Toast(root)
        self.toast.raise_()

    def _factory(self, key: str) -> QWidget:
        if key == "create":
            from linkvideo_vpn_helper.ui.pages.create_client_page import CreateClientPage
            page = CreateClientPage(self.service, self.search, self.credentials, self.registry, self)
        elif key == "search":
            from linkvideo_vpn_helper.ui.pages.search_manage_page import SearchManagePage
            page = SearchManagePage(self.service, self.search, self.credentials, self.registry, self)
        elif key == "vpn_servers":
            from linkvideo_vpn_helper.ui.pages.vpn_servers_page import VPNServersPage
            page = VPNServersPage(self.service, self.credentials, self.registry, self)
        elif key == "archive":
            from linkvideo_vpn_helper.ui.pages.archive_download_page import ArchiveDownloadPage
            page = ArchiveDownloadPage(self.archive, self.settings, self)
        elif key == "diagnostics":
            from linkvideo_vpn_helper.ui.pages.archive_diagnostics_page import ArchiveDiagnosticsPage
            page = ArchiveDiagnosticsPage(self.archive, self.settings, self)
        elif key == "inactive":
            from linkvideo_vpn_helper.ui.pages.inactive_clients_page import InactiveClientsPage
            page = InactiveClientsPage(self.service, self.credentials, self.registry, self)
        elif key == "settings":
            from linkvideo_vpn_helper.ui.pages.settings_page import SettingsPage
            page = SettingsPage(self.settings, self.registry, self.service, self.credentials, self)
            page.themeChanged.connect(lambda _key: self._theme_changed())
            page.updateRequested.connect(self._check_updates)
            page.serversChanged.connect(self._servers_changed)
        else:
            raise KeyError(key)
        return page

    def _ensure_page(self, key: str) -> QWidget:
        page = self._page_cache.get(key)
        if page is None:
            page = self._factory(key)
            self._page_cache[key] = page
            self.stack.addPage(key, page)
        return page

    def _preload_next_page(self):
        queue = getattr(self, "_preload_queue", None) or []
        if not queue:
            return
        key = queue.pop(0)
        try:
            self._ensure_page(key)
        except Exception:
            # A page failure should never prevent the rest of Helper from opening.
            pass
        if queue:
            QTimer.singleShot(80, self._preload_next_page)

    def _go(self, key: str):
        if key == self._current_key:
            page = self._page_cache.get(key)
            hook = getattr(page, "onActivated", None) if page is not None else None
            if callable(hook):
                try:
                    hook()
                except Exception:
                    pass
            return
        previous = self._page_cache.get(self._current_key)
        stop_hook = getattr(previous, "onDeactivated", None) if previous is not None else None
        if callable(stop_hook):
            try:
                stop_hook()
            except Exception:
                pass
        page = self._ensure_page(key)
        self.stack.setCurrent(key)
        self._current_key = key
        start_hook = getattr(page, "onActivated", None)
        if callable(start_hook):
            try:
                start_hook()
            except Exception:
                pass
        for k, (button, _, _, _) in self.nav.items():
            button.setProperty("active", "true" if k == key else "false")
            button.style().unpolish(button)
            button.style().polish(button)
        self.settings_btn.setProperty("active", "true" if key == "settings" else "false")
        self.settings_btn.style().unpolish(self.settings_btn)
        self.settings_btn.style().polish(self.settings_btn)

    def _theme_changed(self):
        self._apply_theme()
        self._position_toast()

    def _servers_changed(self):
        for page in self._page_cache.values():
            if hasattr(page, "refresh_servers"):
                try:
                    page.refresh_servers()
                except Exception:
                    pass
            elif hasattr(page, "server_picker"):
                try:
                    page.server_picker.refresh()
                except Exception:
                    pass
        self.toast.showMessage("Список VPN-серверов обновлён", f"Активно: {len(self.registry.hosts())}")
        self._position_toast()

    def showEvent(self, event):
        super().showEvent(event)
        if not self._first_show:
            return
        self._first_show = False
        try:
            self.setWindowOpacity(0.0)
            animation = QPropertyAnimation(self, b"windowOpacity", self)
            animation.setDuration(220)
            animation.setStartValue(0.0)
            animation.setEndValue(1.0)
            animation.setEasingCurve(QEasingCurve.Type.OutCubic)
            animation.start()
            self._window_fade = animation
        except Exception:
            self.setWindowOpacity(1.0)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            page = self._page_cache.get(self._current_key)
            cancel = getattr(page, "cancel_current_action", None) if page is not None else None
            if callable(cancel):
                try:
                    if cancel():
                        event.accept()
                        return
                except Exception:
                    pass
        super().keyPressEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        collapsed = self.width() < 1040
        if collapsed != self._collapsed:
            self._collapsed = collapsed
            self._sync_sidebar()
        self._position_toast()

    def _sync_sidebar(self):
        collapsed = self._collapsed
        self.sidebar.setFixedWidth(76 if collapsed else 244)
        self.logo.setVisible(not collapsed)
        self.logo_sub.setVisible(not collapsed)
        self.user_card.setVisible(not collapsed)
        self.work_label.setVisible(not collapsed)
        self.version.setVisible(not collapsed)
        self.brand_mark.setVisible(True)
        for button, icon_text, text, hint in self.nav.values():
            button.setText(icon_text if collapsed else f"{icon_text}    {text}")
            button.setToolTip(text if collapsed else "")
            button.setStyleSheet("QPushButton { text-align:center; padding-left:0px; }" if collapsed else "")
        self.settings_btn.setText("⚙" if collapsed else "⚙    Настройки")
        self.settings_btn.setToolTip("Настройки" if collapsed else "")
        self.settings_btn.setStyleSheet("QPushButton { text-align:center; padding-left:0px; }")

    def _position_toast(self):
        if not hasattr(self, "toast") or not self.toast.isVisible():
            return
        self.toast.adjustSize()
        root = self.centralWidget()
        if not root:
            return
        x = max(12, root.width() - self.toast.width() - 22)
        y = max(12, root.height() - self.toast.height() - 22)
        self.toast.move(x, y)
        self.toast.raise_()

    def _check_updates(self, startup: bool = False):
        if self._update_check_busy:
            return
        self._update_check_busy = True
        if not startup:
            self.toast.showMessage("Проверяю обновления", "Получаю информацию о последней версии…", 2200)
            self._position_toast()

        def worker():
            try:
                self.updateReady.emit(self.updater.check(), None, startup)
            except Exception as exc:
                self.updateReady.emit(None, exc, startup)

        threading.Thread(target=worker, daemon=True).start()

    def _on_update_ready(self, info, error, startup: bool):
        self._update_check_busy = False
        if error:
            if not startup:
                self.toast.showMessage("Не удалось проверить обновления", str(error), 5000)
                self._position_toast()
            return
        if not info.has_update:
            if not startup:
                self.toast.showMessage("Установлена актуальная версия", f"LinkVideo.Helper {info.current_version}")
                self._position_toast()
            return
        dialog = ConfirmDialog(
            f"Доступна версия {info.latest_version}",
            (info.notes or "Доступно обновление LinkVideo.Helper.") + "\n\nСкачать установщик и запустить обновление?",
            "Скачать обновление",
            False,
            self,
        )
        if not dialog.exec():
            return
        self.toast.showMessage("Скачиваю обновление", "Установщик загружается и проверяется…", 3000)
        self._position_toast()

        def worker():
            try:
                path = self.updater.download_setup(
                    info.setup_url,
                    expected_sha256=getattr(info, "sha256", ""),
                    expected_version=info.latest_version,
                )
                self.updateDownloaded.emit(path, None)
            except Exception as exc:
                self.updateDownloaded.emit(None, exc)

        threading.Thread(target=worker, daemon=True).start()

    def _on_update_downloaded(self, path, error):
        if error:
            self.toast.showMessage("Обновление не скачано", str(error), 5500)
            self._position_toast()
            return
        try:
            self.updater.run_setup(path)
        except Exception as exc:
            self.toast.showMessage("Не удалось запустить установщик", str(exc), 5500)
            self._position_toast()

    def _logout(self):
        dialog = ConfirmDialog(
            "Выйти из LinkVideo.Helper",
            "Сохранённые данные входа MikroTik будут удалены. Программа перезапустится и снова покажет окно авторизации.",
            "Выйти",
            False,
            self,
        )
        if not dialog.exec():
            return
        for key in ("username", "password"):
            self.settings.remove(key)
        self.settings.setValue("remember", False)
        try:
            if getattr(sys, "frozen", False):
                subprocess.Popen([sys.executable], creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
            else:
                subprocess.Popen(
                    [sys.executable, "-m", "linkvideo_vpn_helper.app"],
                    cwd=str(Path(__file__).resolve().parents[2]),
                )
        except Exception:
            pass
        QApplication.quit()
