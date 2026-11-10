from __future__ import annotations

"""Public LV Automation compatibility facade for RouterOS fleet variants."""

import re
from typing import Callable, Iterable, TypeVar

from linkvideo_vpn_helper.mikrotik.api_ssl_client import RouterOSAPIClient
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
    """LV automation with conservative RouterOS compatibility.

    Functional identity matters more than optional metadata. In particular a
    ``/system/logging`` rule is identified by ``action + topics``. ``prefix``
    merely decorates the emitted log text and is not required by AutoRestore,
    which reads the dedicated LVAuth memory buffer and parses the message body.

    Some deployed routers acknowledge ``add`` with ``!done`` but return no
    ``ret``/``.id`` and may not immediately expose optional fields in a readback.
    A successful add is therefore accepted. Internal IDs are only required when
    an existing object actually has to be mutated.
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
        # Some menus expose find through API, some do not. Prefer it, then use
        # an exact filtered print and finally a broad print as compatibility.
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
            try:
                existing = cls._find(api.print(path), "name", name)
            except Exception:
                existing = None
            if existing is None:
                try:
                    # Empty ret is valid on some RouterOS menus. !done without
                    # !trap means the component was accepted.
                    returned = str(api.add(path, {"name": name, **required}) or "").strip()
                except Exception as exc:
                    if (
                        path == "/system/logging/action"
                        and name == LV_LOG_ACTION
                        and cls._looks_like_optional_field_error(exc)
                    ):
                        # Some RouterOS variants reject creating custom memory
                        # actions. The built-in memory action is a supported
                        # fallback; logging rules select it explicitly below.
                        return ""
                    raise cls._component_error(path, name, ", ".join(required), exc) from exc
                rid = returned or cls._find_id(api, path, "name", name)
            else:
                rid = str(existing.get(".id", "") or "").strip() or cls._find_id(api, path, "name", name)

        cls._apply_optional_fields(api, path, rid, name, optional)
        return rid

    @staticmethod
    def _topic_set(value) -> frozenset[str]:
        return frozenset(
            part.strip().lower()
            for part in str(value or "").split(",")
            if part.strip()
        )

    @classmethod
    def _managed_logging_rule(cls, row: dict | None, topics: str, prefix: str = "") -> bool:
        if not row:
            return False
        action = str(row.get("action", "") or "").strip()
        same_topics = cls._topic_set(row.get("topics")) == cls._topic_set(topics)
        tagged = bool(prefix) and str(row.get("prefix", "") or "").strip() == prefix

        if action == LV_LOG_ACTION:
            # Existing dedicated-buffer rules may lack the optional prefix.
            return same_topics or tagged
        if action == "memory":
            # Do not confuse a system/default memory rule with a managed LV
            # rule: fallback identity REQUIRES our exact, unique prefix.
            return same_topics and tagged
        return False

    @classmethod
    def _find_logging_rule(cls, rows: Iterable[dict], topics: str, prefix: str = "") -> dict | None:
        return next((row for row in rows if cls._managed_logging_rule(row, topics, prefix)), None)

    @classmethod
    def _logging_rows(cls, api) -> list[dict]:
        try:
            return api.print(
                "/system/logging",
                {".proplist": ".id,topics,action,prefix,disabled,regex"},
            )
        except Exception:
            return api.print("/system/logging")

    @staticmethod
    def _invalid_action_reference(exc: Exception) -> bool:
        detail = str(exc or "").lower()
        return (
            "does not match any value of action" in detail
            or "no such action" in detail
            or "invalid value for action" in detail
        )

    @classmethod
    def _logging_action_names(cls, api) -> set[str]:
        return {
            str(row.get("name", "") or "").strip()
            for row in api.print("/system/logging/action")
        }

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

    @classmethod
    def _upsert_logging_rule(cls, api, prefix: str, topics: str, enabled: bool = True) -> None:
        # The custom LVAuth buffer works on existing routers. If a fresh
        # router rejects the action reference, use the built-in "memory"
        # action and a uniquely prefixed rule instead. No unrelated log rule
        # may ever be modified.
        rows = cls._logging_rows(api)
        row = cls._find_logging_rule(rows, topics, prefix)
        rid = str((row or {}).get(".id", "") or "").strip()
        actions = cls._logging_action_names(api)

        if row and not rid:
            rid = cls._find_id(api, "/system/logging", "prefix", prefix)
            if not rid:
                # Legacy RouterOS accepts a logging rule yet exposes neither
                # ret nor .id. An exact functional match is already present,
                # so do not attempt to create duplicates just to obtain an ID.
                if cls._topic_set(row.get("topics")) == cls._topic_set(topics):
                    return
                raise RuntimeError(f"RouterOS не вернул ID логирования LV для темы {topics}")

        current_action = str((row or {}).get("action", "") or "").strip()
        candidates = (
            ["memory"] if row and current_action == "memory"
            else ([LV_LOG_ACTION, "memory"] if LV_LOG_ACTION in actions else ["memory"])
        )
        chosen = ""
        for action in candidates:
            try:
                if row:
                    # Existing row was selected by its topics already. Change
                    # only the action when needed: on some RouterOS builds an
                    # unnecessary combined topics/action set is rejected.
                    if current_action != action:
                        api.set("/system/logging", rid, {"action": action})
                else:
                    # Only documented core properties are required for add.
                    # Previously the memory fallback passed regex/prefix in
                    # the same add, causing "unknown parameter" on some builds.
                    # Those fields are applied as optional individual sets
                    # below and never prevent installation.
                    parameters = {"topics": topics, "action": action}
                    rid = str(api.add("/system/logging", parameters) or "").strip()
                chosen = action
                break
            except Exception as exc:
                if action == LV_LOG_ACTION and cls._invalid_action_reference(exc):
                    # The LVAuth name may exist in a stale/unsupported action
                    # listing but still be rejected by /system/logging.
                    continue
                raise cls._component_error("/system/logging", topics, "topics/action", exc) from exc

        if not chosen:
            raise RuntimeError(f"Не удалось создать правило логирования LV {topics}")

        # Look up the exact created rule; never take an unrelated system rule.
        if not rid:
            refreshed = cls._logging_rows(api)
            found = next(
                (
                    entry for entry in refreshed
                    if str(entry.get("action", "") or "").strip() == chosen
                    and cls._topic_set(entry.get("topics")) == cls._topic_set(topics)
                    and (chosen != "memory" or str(entry.get("prefix", "") or "").strip() == prefix)
                ),
                None,
            )
            rid = str((found or {}).get(".id", "") or "").strip()

        if chosen == "memory" and not rid:
            # No ID to assign a unique prefix; never declare an unmanaged
            # memory rule to be LV automation by accident.
            raise RuntimeError(
                f"RouterOS не вернул ID правила LV {topics} для безопасной настройки memory"
            )

        if rid:
            for field, value in (("prefix", prefix), ("regex", "login failure for user")):
                try:
                    api.set("/system/logging", rid, {field: value})
                except Exception as exc:
                    if not cls._looks_like_optional_field_error(exc):
                        raise cls._component_error("/system/logging", topics, field, exc) from exc
        cls._set_logging_rule_enabled(api, rid, enabled)

    def _set_logging_enabled(self, api, enabled: bool) -> None:
        rows = self._logging_rows(api)
        for topics, prefix in (("ppp", LV_LOG_PREFIX_PPP), ("l2tp", LV_LOG_PREFIX_L2TP)):
            row = self._find_logging_rule(rows, topics, prefix)
            if not row:
                continue
            rid = str(row.get(".id", "") or "").strip()
            self._set_logging_rule_enabled(api, rid, enabled)

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
        status = self._call_core_with_public_api(lambda: super(VPNAutomationService, self).get_status(server, creds))
        # Core keeps compatibility with old prefix-based installs. Re-evaluate
        # logging readiness by the actual functional identity used in 3.0.8.
        try:
            with RouterOSAPIClient(
                server,
                creds.username,
                creds.password,
                port=creds.port,
                timeout=creds.timeout,
            ) as api:
                actions = api.print("/system/logging/action")
                rules = self._logging_rows(api)
            installed_actions = {
                str(item.get("name", "") or "").strip() for item in actions
            }
            ppp = self._find_logging_rule(rules, "ppp", LV_LOG_PREFIX_PPP)
            l2tp = self._find_logging_rule(rules, "l2tp", LV_LOG_PREFIX_L2TP)
            managed = [row for row in (ppp, l2tp) if row]
            status.logging_ready = (
                len(managed) == 2
                and all(
                    # RouterOS guarantees the built-in "memory" action; some
                    # versions omit built-in actions in the API list. A rule
                    # which already references "memory" is sufficient proof.
                    (str(row.get("action", "") or "").strip() == "memory")
                    or str(row.get("action", "") or "").strip() in installed_actions
                    for row in managed
                )
            )
            status.logging_enabled = bool(managed) and all(
                not self._bool(row.get("disabled", "no")) for row in managed
            )
        except Exception:
            pass
        return status
