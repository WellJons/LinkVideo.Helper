from __future__ import annotations

"""Public LV Automation service facade for 3.0.8.

The original implementation is kept in ``vpn_automation_service_core``. This
facade adds RouterOS-version compatibility around component upserts while
preserving the tested lifecycle/runtime logic from the core implementation.
"""

from typing import Callable, Iterable, TypeVar

from linkvideo_vpn_helper.services import vpn_automation_service_core as _core
from linkvideo_vpn_helper.services.vpn_automation_service_core import *  # noqa: F401,F403
from linkvideo_vpn_helper.services.vpn_automation_service_core import (
    VPNAutomationService as _CoreVPNAutomationService,
)

_T = TypeVar("_T")

# RouterOS logging action names are stricter than script/scheduler names on the
# deployed VPN routers: action names may contain only ASCII letters and digits.
# The old core constant ("LV-Auth") therefore caused install/update to stop
# after the scripts were created, leaving every server partially installed.
# Keep the core module and this public facade on one canonical value because
# inherited methods and restore_script_source() resolve the core global at run
# time.
LEGACY_LV_LOG_ACTION = str(getattr(_core, "LV_LOG_ACTION", "LV-Auth") or "LV-Auth")
LV_LOG_ACTION = "LVAuth"
_core.LV_LOG_ACTION = LV_LOG_ACTION


class VPNAutomationService(_CoreVPNAutomationService):
    """LV automation with tolerant RouterOS component installation.

    RouterOS releases differ slightly in accepted fields for scripts/logging
    actions/logging rules. A single unsupported optional field must not leave
    the server in a half-installed state. Required fields are never dropped;
    only known optional compatibility fields are retried without.
    """

    _OPTIONAL_FIELDS_BY_PATH = {
        "/system/script": ("dont-require-permissions",),
        "/system/logging/action": ("memory-stop-on-full",),
        "/system/logging": ("regex",),
    }

    @staticmethod
    def _find(rows: Iterable[dict], field: str, value: str) -> dict | None:
        want = str(value).strip()
        return next((row for row in rows if str(row.get(field, "") or "").strip() == want), None)

    @classmethod
    def _compat_variants(cls, path: str, params: dict) -> list[dict]:
        """Return full params first, then one compatibility-safe reduced form."""
        full = dict(params)
        optional = cls._OPTIONAL_FIELDS_BY_PATH.get(path, ())
        reduced = {key: value for key, value in full.items() if key not in optional}
        if reduced == full:
            return [full]
        return [full, reduced]

    @staticmethod
    def _looks_like_optional_field_error(exc: Exception) -> bool:
        text = str(exc or "").lower()
        markers = (
            "regex",
            "dont-require-permissions",
            "memory-stop-on-full",
            "unknown parameter",
            "expected end of command",
            "input does not match",
        )
        return any(marker in text for marker in markers)

    @classmethod
    def _set_with_compat(cls, api, path: str, rid: str, variants: list[dict]) -> None:
        first_error: Exception | None = None
        for index, params in enumerate(variants):
            try:
                api.set(path, rid, params)
                return
            except Exception as exc:
                if first_error is None:
                    first_error = exc
                if index + 1 >= len(variants) or not cls._looks_like_optional_field_error(exc):
                    raise
        if first_error is not None:
            raise first_error

    @classmethod
    def _add_with_compat(cls, api, path: str, variants: list[dict]) -> str:
        first_error: Exception | None = None
        for index, params in enumerate(variants):
            try:
                return api.add(path, params)
            except Exception as exc:
                if first_error is None:
                    first_error = exc
                if index + 1 >= len(variants) or not cls._looks_like_optional_field_error(exc):
                    raise
        if first_error is not None:
            raise first_error
        return ""

    @classmethod
    def _upsert_named(cls, api, path: str, name: str, params: dict) -> str:
        rows = api.print(path)
        row = cls._find(rows, "name", name)
        variants = cls._compat_variants(path, params)
        if row:
            rid = str(row.get(".id", "") or "").strip()
            if not rid:
                raise RuntimeError(f"RouterOS не вернул .id для {path} {name}")
            cls._set_with_compat(api, path, rid, variants)
            return rid
        return cls._add_with_compat(api, path, [{"name": name, **item} for item in variants])

    @classmethod
    def _upsert_logging_rule(cls, api, prefix: str, topics: str, enabled: bool = True) -> None:
        rows = api.print("/system/logging")
        row = cls._find(rows, "prefix", prefix)
        params = {
            "topics": topics,
            "action": LV_LOG_ACTION,
            "regex": "login failure for user",
            "prefix": prefix,
            "disabled": "no" if enabled else "yes",
        }
        variants = cls._compat_variants("/system/logging", params)
        if row:
            rid = str(row.get(".id", "") or "").strip()
            if not rid:
                raise RuntimeError(f"RouterOS не вернул .id для /system/logging {prefix}")
            cls._set_with_compat(api, "/system/logging", rid, variants)
            return
        cls._add_with_compat(api, "/system/logging", variants)

    @staticmethod
    def _device_mode_hint(api) -> str:
        """Keep the core hint but make scheduler restrictions explicit."""
        hint = _CoreVPNAutomationService._device_mode_hint(api)
        low = hint.lower()
        if "scheduler=no" in low:
            return hint + "; RouterOS device-mode запрещает Scheduler"
        if "flagged=yes" in low:
            return hint + "; RouterOS помечен flagged — Scheduler может блокироваться"
        return hint

    @staticmethod
    def _call_core_with_public_api(call: Callable[[], _T]) -> _T:
        """Keep legacy tests/extensions that monkeypatch this module working.

        Methods inherited from ``vpn_automation_service_core`` resolve the API
        class in the core module's globals. The public module historically was
        monkeypatched directly by tests and troubleshooting harnesses, so mirror
        that binding only for the duration of the call.
        """
        public_api = globals().get("RouterOSAPIClient", _core.RouterOSAPIClient)
        old_api = _core.RouterOSAPIClient
        _core.RouterOSAPIClient = public_api
        try:
            return call()
        finally:
            _core.RouterOSAPIClient = old_api

    def install_or_update(self, server, creds):
        return self._call_core_with_public_api(lambda: super(VPNAutomationService, self).install_or_update(server, creds))

    def set_automation_enabled(self, server, creds, enabled):
        return self._call_core_with_public_api(
            lambda: super(VPNAutomationService, self).set_automation_enabled(server, creds, enabled)
        )

    def set_quarantine_enabled(self, server, creds, enabled):
        return self._call_core_with_public_api(
            lambda: super(VPNAutomationService, self).set_quarantine_enabled(server, creds, enabled)
        )

    def seed_lifecycle(self, server, creds):
        return self._call_core_with_public_api(lambda: super(VPNAutomationService, self).seed_lifecycle(server, creds))

    def mark_manual_state(self, server, creds, login, enabled):
        return self._call_core_with_public_api(
            lambda: super(VPNAutomationService, self).mark_manual_state(server, creds, login, enabled)
        )

    def get_status(self, server, creds):
        return self._call_core_with_public_api(lambda: super(VPNAutomationService, self).get_status(server, creds))
