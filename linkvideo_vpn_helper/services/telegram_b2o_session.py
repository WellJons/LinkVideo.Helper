from __future__ import annotations

"""Independent B2O identity for the 24/7 Windows Telegram task.

The service never reads the currently logged-in employee's HKCU registry, 
and all refreshed B2O API tokens stay in process memory.
"""

import threading

from linkvideo_vpn_helper.services.archive_service_core import B2OService
from linkvideo_vpn_helper.services.telegram_service_runtime import (
    service_b2o_credentials,
)


class VolatileSettings:
    """Minimal Qt QSettings-compatible object: no persistent bearer tokens."""

    def __init__(self):
        self._values: dict[str, object] = {}
        self._lock = threading.RLock()

    def value(self, key, default="", typ=None):
        with self._lock:
            return self._values.get(key, default)

    def setValue(self, key, value):
        with self._lock:
            self._values[key] = value

    def remove(self, key):
        with self._lock:
            self._values.pop(key, None)

    def sync(self):
        return None


class BotB2OService(B2OService):
    def __init__(self, settings=None):
        super().__init__(settings or VolatileSettings())
        self._credential_lock = threading.RLock()

    def ensure_login(self, *, force=False) -> str:
        with self._credential_lock:
            token = super().token()
            if token and not force:
                return token
            username, password = service_b2o_credentials()
            return super().login(username, password)

    def token(self) -> str:
        return self.ensure_login()

    def request_json(self, url, token, headers=None, timeout=25):
        try:
            return super().request_json(url, token, headers=headers, timeout=timeout)
        except RuntimeError as exc:
            # B2OService clears an expired token on 401/403; retry once.
            if "истекла" not in str(exc).lower():
                raise
            renewed = self.ensure_login(force=True)
            return super().request_json(url, renewed, headers=headers, timeout=timeout)
