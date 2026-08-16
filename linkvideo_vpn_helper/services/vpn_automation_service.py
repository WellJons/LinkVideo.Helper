from __future__ import annotations

"""Public LV Automation service facade for 3.0.8.

The original lifecycle/runtime implementation stays in
``vpn_automation_service_core``.  This facade owns compatibility with the
RouterOS versions deployed on the VPN fleet.  The important rule is that an
optional property rejected by one router must never leave the whole LV set
half-installed.
"""

from typing import Callable, Iterable, TypeVar

from linkvideo_vpn_helper.services import vpn_automation_service_core as _core
from linkvideo_vpn_helper.services.vpn_automation_service_core import *  # noqa: F401,F403
from linkvideo_vpn_helper.services.vpn_automation_service_core import (
    VPNAutomationService as _CoreVPNAutomationService,
)

_T = TypeVar("_T")

# Logging action names on the deployed routers are stricter than script and
# scheduler names. Keep the inherited core code on the same alphanumeric name.
LEGACY_LV_LOG_ACTION = str(getattr(_core, "LV_LOG_ACTION", "LV-Auth") or "LV-Auth")
LV_LOG_ACTION = "LVAuth"
_core.LV_LOG_ACTION = LV_LOG_ACTION


class VPNAutomationService(_CoreVPNAutomationService):
    """LV automation with field-by-field RouterOS compatibility.

    Older RouterOS builds may return only ``unknown parameter`` without naming
    the rejected property. Sending a large add/set payload therefore makes it
    impossible to know which harmless compatibility field broke installation.

    Required fields are written first. Optional fields are then applied one at
    a time, so an unsupported option can be skipped without losing the script,
    scheduler, logging action or rule itself.

    One more RouterOS compatibility quirk matters for ``/system/logging``:
    several deployed versions successfully create a logging rule but do not
    return ``ret`` from ``add`` and may omit ``.id`` from a plain ``print``.
    Such a rule is still a valid installed component. We therefore verify it by
    its stable tuple (prefix/topics/action) and only require ``.id`` when we
    actually need to mutate that already-created row.
    """

    _OPTIONAL_FIELDS_BY_PATH = {
        "/system/script": ("comment", "dont-require-permissions"),
        "/system/logging/action": ("memory-lines", "memory-stop-on-full"),
        "/system/scheduler": ("start-time", "comment"),
    }

    @staticmethod
    def _find(rows: Iterable[dict], field: str, value: str) -> dict | None:
        want = str(value).strip()
        return next((row for row in rows if str(row.get(field, "") or "").strip() == want), None)

    @staticmethod
    def _looks_like_optional_field_error(exc: Exception) -> bool:
        text = str(exc or "").lower()
        markers = (
            "unknown parameter",
            "expected end of command",
            "input does not match",
            "not supported",
            "no such item",
        )
        return any(marker in text for marker in markers)

    @classmethod
    def _split_params(cls, path: str, params: dict) -> tuple[dict, list[tuple[str, object]]]:
        optional_names = set(cls._OPTIONAL_FIELDS_BY_PATH.get(path, ()))
        required = {key: value for key, value in params.items() if key not in optional_names}
        optional = [(key, params[key]) for key in cls._OPTIONAL_FIELDS_BY_PATH.get(path, ()) if key in params]
        return required, optional

    @staticmethod
    def _component_error(path: str, name: str, field: str, exc: Exception) -> RuntimeError:
        suffix = f" · параметр {field}" if field else ""
        return RuntimeError(f"Не удалось настроить {path} {name}{suffix}: {exc}")

    @classmethod
    def _apply_optional_fields(cls, api, path: str, rid: str, name: str, fields: list[tuple[str, object]]) -> None:
        for field, value in fields:
            try:
                api.set(path, rid, {field: value})
            except Exception as exc:
                if cls._looks_like_optional_field_error(exc):
                    continue
                raise cls._component_error(path, name, field, exc) from exc

    @classmethod
    def _upsert_named(cls, api, path: str, name: str, params: dict) -> str:
        rows = api.print(path)
        row = cls._find(rows, "name", name)
        required, optional = cls._split_params(path, params)

        if row:
            rid = str(row.get(".id", "") or "").strip()
            if not rid:
                # Try an explicit proplist before declaring an existing named
                # object immutable. Some RouterOS menus omit .id from the broad
                # print representation.
                try:
                    explicit = api.print(path, {".proplist": ".id,name"})
                    explicit_row = cls._find(explicit, "name", name)
                    rid = str((explicit_row or {}).get(".id", "") or "").strip()
                except Exception:
                    rid = ""
            if not rid:
                raise RuntimeError(f"RouterOS не вернул .id для {path} {name}")
            if required:
                try:
                    api.set(path, rid, required)
                except Exception as exc:
                    raise cls._component_error(path, name, ", ".join(required), exc) from exc
        else:
            try:
                rid = api.add(path, {"name": name, **required})
            except Exception as exc:
                raise cls._component_error(path, name, ", ".join(required), exc) from exc
            rid = str(rid or "").strip()
            if not rid:
                try:
                    created_rows = api.print(path, {".proplist": ".id,name"})
                except Exception:
                    created_rows = api.print(path)
                created = cls._find(created_rows, "name", name)
                rid = str((created or {}).get(".id", "") or "").strip()
            if not rid:
                raise RuntimeError(f"RouterOS создал {path} {name}, но не вернул .id")

        cls._apply_optional_fields(api, path, rid, name, optional)
        return rid

    @classmethod
    def _set_logging_rule_enabled(cls, api, rid: str, enabled: bool) -> None:
        if not rid:
            # A logging rule created without a returned internal id is enabled
            # by default. Runtime pause is still enforced by Scheduler. Do not
            # turn a successfully installed rule into a fatal install error.
            return
        method = getattr(api, "enable" if enabled else "disable", None)
        if callable(method):
            try:
                method("/system/logging", rid)
                return
            except Exception as exc:
                if not cls._looks_like_optional_field_error(exc):
                    raise
        try:
            api.set("/system/logging", rid, {"disabled": "no" if enabled else "yes"})
        except Exception as exc:
            if not cls._looks_like_optional_field_error(exc):
                raise

    @staticmethod
    def _logging_rule_matches(row: dict | None, prefix: str, topics: str) -> bool:
        if not row:
            return False
        actual_prefix = str(row.get("prefix", "") or "").strip()
        actual_action = str(row.get("action", "") or "").strip()
        actual_topics = str(row.get("topics", "") or "").strip()
        return actual_prefix == prefix and actual_action == LV_LOG_ACTION and actual_topics == topics

    @classmethod
    def _logging_rows_with_ids(cls, api) -> list[dict]:
        try:
            return api.print(
                "/system/logging",
                {".proplist": ".id,topics,action,prefix,disabled,regex"},
            )
        except Exception:
            return api.print("/system/logging")

    @classmethod
    def _upsert_logging_rule(cls, api, prefix: str, topics: str, enabled: bool = True) -> None:
        rows = cls._logging_rows_with_ids(api)
        row = cls._find(rows, "prefix", prefix)
        required = {
            "topics": topics,
            "action": LV_LOG_ACTION,
            "prefix": prefix,
        }

        rid = ""
        if row:
            rid = str(row.get(".id", "") or "").strip()
            if rid:
                try:
                    api.set("/system/logging", rid, required)
                except Exception as exc:
                    raise cls._component_error("/system/logging", prefix, "topics/action/prefix", exc) from exc
            elif not cls._logging_rule_matches(row, prefix, topics):
                # We cannot safely mutate a row for which this RouterOS does not
                # expose an internal id. Creating a second rule with the same
                # prefix would duplicate logging, so surface a precise error.
                raise RuntimeError(
                    f"RouterOS не вернул .id для существующего /system/logging {prefix}, "
                    "и его параметры отличаются от ожидаемых"
                )
        else:
            try:
                rid = str(api.add("/system/logging", required) or "").strip()
            except Exception as exc:
                raise cls._component_error("/system/logging", prefix, "topics/action/prefix", exc) from exc

            if not rid:
                # Some RouterOS releases acknowledge a successful logging/add
                # with !done but without =ret=. Re-read using an explicit
                # proplist; if the stable rule tuple is present, creation was
                # successful even when .id is still omitted.
                created_rows = cls._logging_rows_with_ids(api)
                created = cls._find(created_rows, "prefix", prefix)
                if not cls._logging_rule_matches(created, prefix, topics):
                    raise RuntimeError(
                        f"RouterOS подтвердил создание /system/logging {prefix}, "
                        "но правило не найдено при повторной проверке"
                    )
                rid = str((created or {}).get(".id", "") or "").strip()

        # regex is an optimisation only. If this RouterOS does not expose .id
        # for logging rows, the base topic/action/prefix rule is already enough:
        # AutoRestore filters messages inside the RouterOS script itself.
        if rid:
            try:
                api.set("/system/logging", rid, {"regex": "login failure for user"})
            except Exception as exc:
                if not cls._looks_like_optional_field_error(exc):
                    raise cls._component_error("/system/logging", prefix, "regex", exc) from exc

        cls._set_logging_rule_enabled(api, rid, enabled)

    @staticmethod
    def _device_mode_hint(api) -> str:
        hint = _CoreVPNAutomationService._device_mode_hint(api)
        low = hint.lower()
        if "scheduler=no" in low:
            return hint + "; RouterOS device-mode запрещает Scheduler"
        if "flagged=yes" in low:
            return hint + "; RouterOS помечен flagged — Scheduler может блокироваться"
        return hint

    @staticmethod
    def _call_core_with_public_api(call: Callable[[], _T]) -> _T:
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
