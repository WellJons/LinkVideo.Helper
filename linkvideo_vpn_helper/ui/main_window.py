from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QSettings, QSize, Qt, Signal, QTimer
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
from linkvideo_vpn_helper.theme import colors, get_theme_style
from linkvideo_vpn_helper.ui.components import Card
from linkvideo_vpn_helper.ui.components_compat import AnimatedStack, Toast
from linkvideo_vpn_helper.ui.dialogs import ConfirmDialog
from linkvideo_vpn_helper.ui.nav_icons import navigation_icon
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
        ("tariffs", "₽", "Клиенты и тарифы", "Корпоративный биллинг, VPN и КЦ"),
        ("new_agent", "◉", "Новый Агент", "Настройки камер LD через agent-vcore"),
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
        from linkvideo_vpn_helper.services.work_account_auth import WorkAccountAuth
        self.work_account = WorkAccountAuth(credentials, self.settings, self)
        self.work_account.b2oChanged.connect(self.work_account.on_b2o_result)
        self.work_account.loginRequested.connect(self._open_admin_login)
        self.updater = UpdateService()
        self._page_cache: dict[str, QWidget] = {}
        self._current_key = ""
        self._collapsed = False
        self._account_panel_open = False
        self._admin_panel_open = False
        self._work_b2o_ok: bool | None = None
        self._work_crm_ok: bool | None = None
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
        # Build pages on first use. Creating seven hidden tool pages during
        # startup triggers unnecessary native widgets and occasional transient
        # process/window activity on operator workstations.
        self._preload_queue = []
        # Verify the B2O service with the same employee credentials without
        # opening another login dialog or blocking the main window.
        QTimer.singleShot(150, self.work_account.ensure_b2o)
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
        self.sidebar.setFixedWidth(254)
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
        user_card.setObjectName("SidebarUserCard")
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
        self.user.setToolTip(self.credentials.username)
        self.user.setObjectName("Value")
        role = QLabel("MikroTik · подключён")
        role.setObjectName("TinyMuted")
        user_text.addWidget(self.user)
        user_text.addWidget(role)
        self.logout = QPushButton("⌄")
        self.logout.setObjectName("WorkAccountToggle")
        self.logout.setProperty("role", "icon")
        self.logout.setToolTip("Рабочая учётная запись и вход в сервисы")
        self.logout.clicked.connect(self._toggle_work_account_panel)
        ul.addWidget(avatar)
        ul.addLayout(user_text, 1)
        ul.addWidget(self.logout)
        sl.addWidget(user_card)
        # Two distinct accounts: MikroTik and administrator. Their usernames
        # and passwords never flow into each other's authentication endpoints.
        self.work_account_panel = Card(subtle=True)
        self.work_account_panel.setObjectName("WorkAccountPanel")
        router_layout = QVBoxLayout(self.work_account_panel)
        router_layout.setContentsMargins(10, 10, 10, 10)
        self.work_signout = QPushButton("Сменить аккаунт MikroTik")
        self.work_signout.setObjectName("WorkAccountSignOut")
        self.work_signout.setProperty("role", "soft")
        self.work_signout.clicked.connect(self._logout)
        router_layout.addWidget(self.work_signout)
        self.work_account_panel.hide()
        sl.addWidget(self.work_account_panel)

        self.admin_card = Card(subtle=True)
        self.admin_card.setObjectName("SidebarAdminCard")
        al = QHBoxLayout(self.admin_card)
        al.setContentsMargins(11, 9, 9, 9)
        al.setSpacing(8)
        admin_avatar = QLabel("A")
        admin_avatar.setObjectName("Avatar")
        admin_avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        admin_avatar.setFixedSize(32, 32)
        admin_text = QVBoxLayout()
        admin_text.setSpacing(0)
        self.admin_name = QLabel("Админка")
        self.admin_name.setObjectName("Value")
        self.admin_status = QLabel("Не подключена")
        self.admin_status.setObjectName("TinyMuted")
        self.admin_status.setWordWrap(True)
        admin_text.addWidget(self.admin_name)
        admin_text.addWidget(self.admin_status)
        self.admin_toggle = QPushButton("⌄")
        self.admin_toggle.setObjectName("AdminAccountToggle")
        self.admin_toggle.setProperty("role", "icon")
        self.admin_toggle.setToolTip("Вход и управление учётной записью админки")
        self.admin_toggle.clicked.connect(self._toggle_admin_panel)
        al.addWidget(admin_avatar)
        al.addLayout(admin_text, 1)
        al.addWidget(self.admin_toggle)
        sl.addWidget(self.admin_card)

        self.admin_account_panel = Card(subtle=True)
        self.admin_account_panel.setObjectName("AdminAccountPanel")
        admin_actions = QVBoxLayout(self.admin_account_panel)
        admin_actions.setContentsMargins(10, 10, 10, 10)
        admin_actions.setSpacing(7)
        self.admin_login_btn = QPushButton("Войти в админку")
        self.admin_login_btn.setObjectName("AdminAccountLogin")
        self.admin_login_btn.setProperty("role", "primary")
        self.admin_login_btn.clicked.connect(self._open_admin_login)
        admin_actions.addWidget(self.admin_login_btn)
        self.admin_retry_btn = QPushButton("Проверить подключение")
        self.admin_retry_btn.setObjectName("AdminAccountRetry")
        self.admin_retry_btn.setProperty("role", "soft")
        self.admin_retry_btn.clicked.connect(self._reconnect_work_billing)
        admin_actions.addWidget(self.admin_retry_btn)
        self.admin_forget_btn = QPushButton("Забыть данные админки")
        self.admin_forget_btn.setObjectName("AdminAccountForget")
        self.admin_forget_btn.setProperty("role", "soft")
        self.admin_forget_btn.clicked.connect(self._forget_admin)
        admin_actions.addWidget(self.admin_forget_btn)
        self.admin_account_panel.hide()
        sl.addWidget(self.admin_account_panel)
        sl.addSpacing(10)
        self.work_account.b2oChanged.connect(self._on_work_b2o_status)
        self._refresh_work_billing_status()

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
            button = QPushButton(text)
            button.setIconSize(QSize(20, 20))
            button.setProperty("nav", "true")
            button.clicked.connect(lambda checked=False, k=key: self._go(k))
            self.nav[key] = (button, icon_text, text, hint)
            sl.addWidget(button)

        sl.addStretch(1)
        self.settings_btn = QPushButton("Настройки")
        self.settings_btn.setIconSize(QSize(20, 20))
        self.settings_btn.setProperty("nav", "true")
        self.settings_btn.setProperty("footerNav", "true")
        self.settings_btn.clicked.connect(lambda: self._go("settings"))
        sl.addWidget(self.settings_btn)

        # Версия находится непосредственно под настройками и визуально
        # центрируется относительно боковой панели.
        self.version = QLabel(f"v{APP_VERSION}")
        # A user may have a stale pinned shortcut targeting another installation.
        # Expose the actual executable path to support without changing preferences.
        self.version.setToolTip(f"Запущено из: {Path(sys.executable)}")
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
            page = SearchManagePage(
                self.service, self.search, self.credentials, self.registry, self,
                work_account=self.work_account,
            )
        elif key == "vpn_servers":
            from linkvideo_vpn_helper.ui.pages.vpn_servers_page import VPNServersPage
            page = VPNServersPage(self.service, self.credentials, self.registry, self)
        elif key == "archive":
            from linkvideo_vpn_helper.ui.pages.archive_download_page import ArchiveDownloadPage
            page = ArchiveDownloadPage(self.archive, self.settings, self, work_account=self.work_account)
        elif key == "diagnostics":
            from linkvideo_vpn_helper.ui.pages.archive_diagnostics_page import ArchiveDiagnosticsPage
            page = ArchiveDiagnosticsPage(self.archive, self.settings, self, work_account=self.work_account)
        elif key == "inactive":
            from linkvideo_vpn_helper.ui.pages.inactive_clients_page import InactiveClientsPage
            page = InactiveClientsPage(self.service, self.credentials, self.registry, self)
        elif key == "tariffs":
            from linkvideo_vpn_helper.ui.pages.tariffs_page import TariffsPage
            page = TariffsPage(self, settings=self.settings, work_account=self.work_account)
            page.billing_login.sessionChanged.connect(self._on_work_crm_status)
            page.billing_login.loginFailed.connect(lambda _message: self._on_work_crm_status(False))
        elif key == "new_agent":
            from linkvideo_vpn_helper.ui.pages.new_agent_page import NewAgentPage
            page = NewAgentPage(self.settings, self.credentials, self)
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
        self._refresh_nav_icons()

    def _refresh_nav_icons(self):
        # Icon colors match selected states in every theme, while the rail
        # itself stays graphite as in the approved UI reference.
        theme_key = str(self.settings.value("ui/theme_v2", "linkvideo_2026", str) or "linkvideo_2026")
        accent_foreground = colors(theme_key)["accent_text"]
        for nav_key, (button, _, _, _) in self.nav.items():
            active = nav_key == self._current_key
            button.setIcon(navigation_icon(
                nav_key, color=accent_foreground if active else "#D8E0E8"
            ))
        self.settings_btn.setIcon(navigation_icon(
            "settings", color=accent_foreground if self._current_key == "settings" else "#D8E0E8"
        ))

    def _theme_changed(self):
        self._apply_theme()
        self._refresh_nav_icons()
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
        self.sidebar.setFixedWidth(76 if collapsed else 254)
        self.logo.setVisible(not collapsed)
        self.logo_sub.setVisible(not collapsed)
        self.user_card.setVisible(not collapsed)
        self.work_account_panel.setVisible(not collapsed and self._account_panel_open)
        self.admin_card.setVisible(not collapsed)
        self.admin_account_panel.setVisible(not collapsed and self._admin_panel_open)
        self.work_label.setVisible(not collapsed)
        self.version.setVisible(not collapsed)
        self.brand_mark.setVisible(True)
        for button, icon_text, text, hint in self.nav.values():
            button.setText("" if collapsed else text)
            button.setToolTip(text if collapsed else "")
            button.setStyleSheet("QPushButton { text-align:center; padding-left:0px; }" if collapsed else "")
        self.settings_btn.setText("" if collapsed else "Настройки")
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

    def _toggle_work_account_panel(self):
        self._account_panel_open = not self._account_panel_open
        self.work_account_panel.setVisible(self._account_panel_open and not self._collapsed)
        self.logout.setText("⌃" if self._account_panel_open else "⌄")

    def _toggle_admin_panel(self):
        self._admin_panel_open = not self._admin_panel_open
        self.admin_account_panel.setVisible(
            self._admin_panel_open and not self._collapsed
        )
        self.admin_toggle.setText("⌃" if self._admin_panel_open else "⌄")

    def _refresh_work_billing_status(self):
        account = self.work_account
        b2o, crm = self._work_b2o_ok, self._work_crm_ok
        self.admin_name.setText(account.username or "Админка")
        if not account.has_credentials:
            status = "Не подключена"
        elif b2o is False and crm is False:
            status = "Нет доступа"
        elif b2o is False or crm is False:
            status = "Часть функций недоступна"
        elif b2o is True and crm is True:
            status = "Подключена"
        elif b2o is True:
            status = "Камеры подключены"
        elif crm is True:
            status = "Клиенты подключены"
        else:
            status = "Проверка доступа…"
        self.admin_status.setText(status)
        self.admin_status.setToolTip(
            "Камеры: " + ("подключены" if b2o is True else
                           "нет доступа" if b2o is False else "не проверены") +
            "\\nКлиенты и тарифы: " + ("подключены" if crm is True else
                                       "нет доступа" if crm is False else "не проверены")
        )
        page = self._page_cache.get("tariffs")
        busy = account.b2o_busy or bool(page and page.billing_login._busy)
        self.admin_login_btn.setText(
            "Сменить аккаунт" if account.has_credentials else "Войти в админку"
        )
        self.admin_login_btn.setEnabled(not busy)
        self.admin_retry_btn.setEnabled(account.has_credentials and not busy)
        self.admin_forget_btn.setEnabled(account.has_credentials and not busy)

    def _open_admin_login(self):
        if self.work_account.b2o_busy:
            return
        from linkvideo_vpn_helper.ui.admin_login_dialog import AdminLoginDialog
        dialog = AdminLoginDialog(self.work_account.username, parent=self)
        if not dialog.exec():
            return
        username, password, remember = dialog.credentials()
        page = self._page_cache.get("tariffs")
        if page is not None and page.billing_login._busy:
            return
        if page is not None:
            page.billing_login._logout()
        try:
            self.work_account.set_admin_credentials(
                username, password, remember=remember
            )
        except (ValueError, RuntimeError):
            self.admin_status.setText("Не удалось изменить учётную запись")
            return
        self._work_b2o_ok = None
        self._work_crm_ok = None
        self._refresh_work_billing_status()
        self.work_account.ensure_b2o(force=True)
        if page is not None:
            page._connect_work_account()
        self._refresh_work_billing_status()

    def _forget_admin(self):
        if self.work_account.b2o_busy:
            return
        page = self._page_cache.get("tariffs")
        if page is not None and page.billing_login._busy:
            return
        if page is not None:
            page.billing_login._logout()
        self.work_account.clear(forget=True)
        self._work_b2o_ok = None
        self._work_crm_ok = None
        self._refresh_work_billing_status()

    def _on_work_b2o_status(self, ok: bool, error: str):
        self._work_b2o_ok = bool(ok)
        self._refresh_work_billing_status()

    def _on_work_crm_status(self, ok: bool):
        self._work_crm_ok = bool(ok)
        if ok:
            self.work_account.confirm_credentials()
        self._refresh_work_billing_status()

    def _reconnect_work_billing(self):
        if not self.work_account.has_credentials:
            self._open_admin_login()
            return
        if self.work_account.b2o_busy:
            return
        page = self._page_cache.get("tariffs")
        if page is not None and page.billing_login._busy:
            return
        self._work_b2o_ok = None
        self._work_crm_ok = None
        self._refresh_work_billing_status()
        self.work_account.ensure_b2o(force=True)
        # Only create CRM's page on explicit retry. The existing gateway owns
        # the resulting CRM token, so it is not silently discarded.
        page = self._ensure_page("tariffs")
        if page.billing_login.login_verified or not page.billing_login._busy:
            page._connect_work_account()
        self._refresh_work_billing_status()

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
        from linkvideo_vpn_helper.services.work_login_storage import clear_work_login
        self.work_account.clear()
        clear_work_login(self.settings, QSettings("LinkVideo", "VPNHelper"))
        # If the next launch is interrupted, the old account still cannot be
        # re-imported; the explicit switch-account flag also disables auto-login.
        arguments = ([sys.executable, "--signed-out"] if getattr(sys, "frozen", False)
                     else [sys.executable, "-m", "linkvideo_vpn_helper.app", "--signed-out"])
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
            subprocess.Popen(
                arguments,
                cwd=None if getattr(sys, "frozen", False) else str(Path(__file__).resolve().parents[2]),
                creationflags=flags,
            )
        except OSError:
            # Credentials are already cleared; restart failure must not undo logout.
            pass
        QApplication.quit()
