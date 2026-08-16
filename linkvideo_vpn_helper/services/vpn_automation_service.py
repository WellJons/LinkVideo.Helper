from __future__ import annotations

"""Public LV Automation service facade for 3.0.8."""

from typing import Callable, Iterable, TypeVar

from linkvideo_vpn_helper.services import vpn_automation_service_core as _core
from linkvideo_vpn_helper.services.vpn_automation_service_core import *  # noqa: F401,F403
from linkvideo_vpn_helper.services.vpn_automation_service_core import (
    VPNAutomationService as _CoreVPNAutomationService,
)

_T = TypeVar("_T")

LEGACY_LV_LOG_ACTION = str(getattr(_core, "LV_LOG_ACTION", "LV-Auth") or "LV-Auth")
LV_LOG_ACTION = "LVAuth"
_core.LV_LOG_ACTION = LV_LOG_ACTION


class VPNAutomationService(_CoreVPNAutomationService):
    """LV automation with field-by-field RouterOS compatibility.

    RouterOS API/CLI guarantees that ``find`` returns internal item IDs. Some
    deployed routers acknowledge ``add`` without returning ``ret`` and some menu
    print forms omit ``.id``. Installation therefore never treats an empty add
    return as failure; it resolves the object through ``/find`` by its stable
    name/prefix and verifies the complete set afterwards.
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
        return any(
            marker in text
            for marker in (
                "unknown parameter",
                "expected end of command",
                "input does not match",
                "not supported",
                "no such item",
            )
        )

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

    @staticmethod
    def _ret_ids(replies) -> list[str]:
        result: list[str] = []
        for item in replies or []:
            raw = str(item.get("ret", "") or item.get(".id", "") or "").strip()
            if not raw:
                continue
            for part in re.split(r"[;,\s]+", raw):
                part = part.strip()
                if part and part not in result:
                    result.append(part)
        return result

    @classmethod
    def _find_ids(cls, api, path: str, field: str, value: str) -> list[str]:
        """Resolve internal IDs using RouterOS find, then a print fallback."""
        try:
            ids = cls._ret_ids(api.talk(f"{path}/find", {field: value}))
            if ids:
                return ids
        except Exception:
            pass
        try:
            rows = api.print(path, {".proplist": f".id,{field}", f"?{field}=": value})
        except Exception:
            try:
                rows = api.print(path)
            except Exception:
                rows = []
        row = cls._find(rows, field, value)
        rid = str((row or {}).get(".id", "") or "").strip()
        return [rid] if rid else []

    @classmethod
    def _find_id(cls, api, path: str, field: str, value: str) -> str:
        ids = cls._find_ids(api, path, field, value)
        return ids[0] if ids else ""

    @classmethod
    def _apply_optional_fields(cls, api, path: str, rid: str, name: str, fields: list[tuple[str, object]]) -> None:
        if not rid:
            return
        for field, value in fields:
            try:
                api.set(path, rid, {field: value})
            except Exception as exc:
                if cls._looks_like_optional_field_error(exc):
                    continue
                raise cls._component_error(path, name, field, exc) from exc

    @classmethod
    def _upsert_named(cls, api, path: str, name: str, params: dict) -> str:
        required, optional = cls._split_params(path, params)
        rid = cls._find_id(api, path, "name", name)

        if rid:
            if required:
                try:
                    api.set(path, rid, required)
                except Exception as exc:
                    raise cls._component_error(path, name, ", ".join(required), exc) from exc
        else:
            # A plain print is used only to avoid duplicates on an unusual menu
            # where find itself is unavailable. The object may legitimately be
            # present without an exposed .id.
            try:
                existing = cls._find(api.print(path), "name", name)
            except Exception:
                existing = None
            if existing is None:
                try:
                    returned = str(api.add(path, {"name": name, **required}) or "").strip()
                except Exception as exc:
                    raise cls._component_error(path, name, ", ".join(required), exc) from exc
                rid = returned or cls._find_id(api, path, "name", name)
            else:
                rid = str(existing.get(".id", "") or "").strip() or cls._find_id(api, path, "name", name)

        # If RouterOS created the named object but exposes no ID even through
        # find, do not fail here. Optional metadata cannot be applied, but final
        # get_status() still verifies that every required component exists.
        cls._apply_optional_fields(api, path, rid, name, optional)
        return rid

    @classmethod
    def _set_logging_rule_enabled(cls, api, rid: str, enabled: bool) -> None:
        if not rid:
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
        return (
            str(row.get("prefix", "") or "").strip() == prefix
            and str(row.get("action", "") or "").strip() == LV_LOG_ACTION
            and str(row.get("topics", "") or "").strip() == topics
        )

    @classmethod
    def _logging_rows(cls, api) -> list[dict]:
        try:
            return api.print(
                "/system/logging",
                {".proplist": ".id,topics,action,prefix,disabled,regex"},
            )
        except Exception:
            return api.print("/system/logging")

    @classmethod
    def _upsert_logging_rule(cls, api, prefix: str, topics: str, enabled: bool = True) -> None:
        required = {"topics": topics, "action": LV_LOG_ACTION, "prefix": prefix}
        rows = cls._logging_rows(api)
        row = cls._find(rows, "prefix", prefix)
        rid = cls._find_id(api, "/system/logging", "prefix", prefix)

        if row:
            if rid:
                try:
                    api.set("/system/logging", rid, required)
                except Exception as exc:
                    raise cls._component_error("/system/logging", prefix, "topics/action/prefix", exc) from exc
            elif not cls._logging_rule_matches(row, prefix, topics):
                raise RuntimeError(
                    f"RouterOS нашёл /system/logging {prefix}, но не дал ID для исправления его параметров"
                )
        else:
            try:
                returned = str(api.add("/system/logging", required) or "").strip()
            except Exception as exc:
                raise cls._component_error("/system/logging", prefix, "topics/action/prefix", exc) from exc
            rid = returned or cls._find_id(api, "/system/logging", "prefix", prefix)
            # Verify the stable tuple rather than the add return value.
            created = cls._find(cls._logging_rows(api), "prefix", prefix)
            if not cls._logging_rule_matches(created, prefix, topics):
                raise RuntimeError(
                    f"RouterOS подтвердил создание /system/logging {prefix}, но правило не найдено при проверке"
                )

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
