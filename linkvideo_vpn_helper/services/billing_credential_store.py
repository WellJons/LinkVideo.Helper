from __future__ import annotations

"""Separate Windows-protected CRM credentials, never a RouterOS login token."""

import sys
from dataclasses import dataclass

from linkvideo_vpn_helper.services.cloud_vpnsync import (
    _dpapi_protect, _dpapi_unprotect,
)


@dataclass(frozen=True, repr=False)
class StoredBillingLogin:
    username: str
    password: str

    def __repr__(self) -> str:
        return "StoredBillingLogin(<redacted>)"


class BillingCredentialStore:
    PREFIX = "billing/crm"

    def __init__(self, settings, *, platform: str | None = None,
                 protector=None, unprotector=None):
        self.settings = settings
        self.platform = sys.platform if platform is None else platform
        self._protect = protector or _dpapi_protect
        self._unprotect = unprotector or _dpapi_unprotect

    def load(self) -> StoredBillingLogin:
        username = str(self.settings.value(f"{self.PREFIX}/username", "", str) or "")
        ciphertext = str(self.settings.value(f"{self.PREFIX}/password_dpapi", "", str) or "")
        if self.platform != "win32" or not ciphertext.startswith("dpapi:"):
            return StoredBillingLogin(username, "")
        return StoredBillingLogin(username, self._unprotect(ciphertext))

    def save(self, username: str, password: str, *, remember: bool) -> None:
        username = (username or "").strip()
        if not remember:
            self.clear()
            return
        if not username or not password:
            raise ValueError("Укажите логин и пароль CRM.")
        if self.platform != "win32":
            raise RuntimeError("Сохранение пароля CRM поддерживается только в Windows DPAPI.")
        protected = self._protect(password)
        if not protected.startswith("dpapi:"):
            raise RuntimeError("Не удалось защитить пароль через Windows DPAPI.")
        self.settings.setValue(f"{self.PREFIX}/username", username)
        self.settings.setValue(f"{self.PREFIX}/password_dpapi", protected)
        self.settings.sync()

    def clear(self) -> None:
        self.settings.remove(f"{self.PREFIX}/username")
        self.settings.remove(f"{self.PREFIX}/password_dpapi")
        self.settings.sync()

    def helper_credentials(self) -> StoredBillingLogin:
        """Opt-in ONLY: read old Helper settings, without copying by default."""
        username = str(self.settings.value("username", "", str) or "")
        password = str(self.settings.value("password", "", str) or "")
        return StoredBillingLogin(username, password)
