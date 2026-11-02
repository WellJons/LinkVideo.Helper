from __future__ import annotations

"""Single verified employee credential source for Helper integrations.

RouterOS authenticates the operator at application startup. B2O and CRM have
independent endpoints/tokens and MUST verify that same operator independently.
Passwords remain in the already authenticated SessionCredentials object; do
not copy them into another QSettings key or a second credential database.
"""

import threading

from PySide6.QtCore import QObject, Signal

from linkvideo_vpn_helper.services.archive_service_core import B2OService
from linkvideo_vpn_helper.services.billing_credential_store import BillingCredentialStore


class WorkAccountAuth(QObject):
    b2oChanged = Signal(bool, str)

    def __init__(self, credentials, settings, parent=None, *, b2o_factory=B2OService):
        super().__init__(parent)
        username = str(getattr(credentials, "username", "") or "").strip()
        password = str(getattr(credentials, "password", "") or "")
        if not username or not password:
            raise ValueError("Для интеграций требуется подтверждённый вход MikroTik.")
        # Held in memory for this application's verified user session only.
        self.credentials = credentials
        self.settings = settings
        self._b2o_factory = b2o_factory
        self._b2o_busy = False
        self._b2o_ready = False
        self._b2o_error = ""
        self._lock = threading.Lock()

        # Do not accidentally use an old B2O token from a previous employee.
        # A new B2O session is verified on every Helper launch.
        self.settings.remove("b2o/token")
        if str(self.settings.value("b2o/login", "", str) or "").strip() != username:
            self.settings.remove("b2o/login")
        # CRM no longer has an independent saved copy of the work password.
        BillingCredentialStore(self.settings).clear()

    @property
    def username(self) -> str:
        return str(self.credentials.username).strip()

    @property
    def b2o_busy(self) -> bool:
        with self._lock:
            return self._b2o_busy

    @property
    def b2o_error(self) -> str:
        return self._b2o_error

    def ensure_b2o(self, *, force: bool = False) -> bool:
        """Nonblocking B2O login; concurrent callers share one attempt."""
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
                # B2OService performs an actual server-side login and only then
                # stores its distinct B2O token (not the RouterOS password).
                service = self._b2o_factory(self.settings)
                service.login(username, password)
                self.b2oChanged.emit(True, "")
            except Exception:
                # Never expose untrusted response bodies or credential-bearing URLs.
                self.b2oChanged.emit(
                    False,
                    "B2O отклонил рабочую учётную запись или недоступен.",
                )

        threading.Thread(
            target=worker, name="work-account-b2o-login", daemon=True,
        ).start()
        return False

    def on_b2o_result(self, ok: bool, error: str) -> None:
        """Update status on the Qt main thread, connected to b2oChanged."""
        with self._lock:
            self._b2o_busy = False
            self._b2o_ready = bool(ok)
            self._b2o_error = str(error or "") if not ok else ""
        if not ok:
            self.settings.remove("b2o/token")

    def clear(self) -> None:
        self.settings.remove("b2o/token")
        self.settings.remove("b2o/login")
        BillingCredentialStore(self.settings).clear()
