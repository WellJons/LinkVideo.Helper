from __future__ import annotations

"""Administrative account for B2O and CRM, separate from the RouterOS login.

The RouterOS session is used only for MikroTik. The administrator enters an
independent username/password, optionally remembered with Windows DPAPI.
The two backend APIs validate the same administrator credentials separately;
their tokens are never interchanged or used as proof for each other.
"""

import threading

from PySide6.QtCore import QObject, Signal

from linkvideo_vpn_helper.services.archive_service_core import B2OService
from linkvideo_vpn_helper.services.billing_credential_store import BillingCredentialStore


class WorkAccountAuth(QObject):
    b2oChanged = Signal(bool, str)

    def __init__(self, credentials, settings, parent=None, *, b2o_factory=B2OService,
                 credential_store=None):
        super().__init__(parent)
        # The first positional argument remains for backward-compatible callers
        # but MUST NOT be read: it contains the unrelated MikroTik password.
        self.settings = settings
        self.store = credential_store or BillingCredentialStore(settings)
        self._b2o_factory = b2o_factory
        self.credentials = None
        self._remember = False
        self._saved_confirmed = False
        self._b2o_busy = False
        self._b2o_ready = False
        self._b2o_error = ""
        self._lock = threading.Lock()
        # Never inherit the B2O server session from a former operator.
        self.settings.remove("b2o/token")
        self.settings.remove("b2o/login")
        try:
            saved = self.store.load()
        except Exception:
            # A corrupt/unavailable DPAPI record must not fall back to RouterOS.
            saved = None
        if saved is not None and saved.username and saved.password:
            self._set_credentials(saved.username, saved.password, remember=True,
                                  saved_confirmed=True)

    @property
    def username(self) -> str:
        return str(getattr(self.credentials, "username", "") or "").strip()

    @property
    def has_credentials(self) -> bool:
        return bool(self.username and getattr(self.credentials, "password", ""))

    @property
    def b2o_busy(self) -> bool:
        with self._lock:
            return self._b2o_busy

    @property
    def b2o_error(self) -> str:
        return self._b2o_error

    def _set_credentials(self, username: str, password: str, *, remember: bool,
                         saved_confirmed: bool = False) -> None:
        from linkvideo_vpn_helper.services.vpn_service import SessionCredentials
        username = str(username or "").strip()
        password = str(password or "")
        if not username or not password:
            raise ValueError("Введите логин и пароль админки.")
        self.credentials = SessionCredentials(username, password)
        self._remember = bool(remember)
        self._saved_confirmed = bool(saved_confirmed)
        self._b2o_ready = False
        self._b2o_error = ""
        self.settings.remove("b2o/token")
        self.settings.remove("b2o/login")

    def set_admin_credentials(self, username: str, password: str, *,
                              remember: bool = True) -> None:
        if self.b2o_busy:
            raise RuntimeError("Дождитесь завершения проверки админки.")
        self._set_credentials(username, password, remember=remember)
        # An explicit change must invalidate the former protected login.
        self.store.clear()

    def confirm_credentials(self) -> bool:
        """Save only a server-verified account, never an unchecked password."""
        if not self.has_credentials or self._saved_confirmed:
            return True
        if not self._remember:
            self.store.clear()
            self._saved_confirmed = True
            return True
        try:
            self.store.save(self.username, self.credentials.password, remember=True)
            self._saved_confirmed = True
            return True
        except Exception:
            # Do not pretend the Windows secret has been remembered.
            return False

    def ensure_b2o(self, *, force: bool = False) -> bool:
        """Nonblocking B2O login using ONLY the administrative account."""
        if not self.has_credentials:
            return False
        with self._lock:
            if self._b2o_busy:
                return False
            if self._b2o_ready and not force and self.settings.value("b2o/token", "", str):
                return True
            self._b2o_busy = True
            self._b2o_ready = False
            self._b2o_error = ""
        if force:
            self.settings.remove("b2o/token")
        username = self.username
        password = str(self.credentials.password)

        def worker():
            try:
                service = self._b2o_factory(self.settings)
                service.login(username, password)
                self.b2oChanged.emit(True, "")
            except Exception:
                self.b2oChanged.emit(
                    False, "Админка отклонила данные или сервер недоступен."
                )

        threading.Thread(target=worker, name="admin-account-b2o-login",
                         daemon=True).start()
        return False

    def on_b2o_result(self, ok: bool, error: str) -> None:
        with self._lock:
            self._b2o_busy = False
            self._b2o_ready = bool(ok)
            self._b2o_error = str(error or "") if not ok else ""
        if ok:
            self.confirm_credentials()
        else:
            self.settings.remove("b2o/token")

    def clear(self, *, forget: bool = False) -> None:
        if self.b2o_busy:
            raise RuntimeError("Дождитесь завершения подключения.")
        self.credentials = None
        self._remember = False
        self._saved_confirmed = False
        self._b2o_ready = False
        self.settings.remove("b2o/token")
        self.settings.remove("b2o/login")
        if forget:
            self.store.clear()
