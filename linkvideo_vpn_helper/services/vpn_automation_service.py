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
        """Resolve IDs by an EXACT property match, never by an unverified find.

        RouterOS API query words (?name=...) are only documented for /print.
        The previous /find with =name=... could return unrelated IDs. This
        caused repeated LV script/scheduler creation to overwrite the first
        object and leave the server at 1/3 components.
        """
        expected = str(value).strip()
        # A filtered print is efficient; inspect returned row values even if
        # a RouterOS build or fake API ignores the query.
        try:
            rows = api.print(path, {".proplist": f".id,{field}", f"?{field}=": expected})
        except Exception:
            rows = []
        matches = [
            str(row.get(".id", "") or "").strip()
            for row in rows
            if str(row.get(field, "") or "").strip() == expected
            and str(row.get(".id", "") or "").strip()
        ]
        if matches:
            return list(dict.fromkeys(matches))

        # Fall back to full enumeration: some RouterOS builds ignore or do
        # not support print query/proplist combinations on specific menus.
        rows = api.print(path)
        return list(dict.fromkeys(
            str(row.get(".id", "") or "").strip()
            for row in rows
            if str(row.get(field, "") or "").strip() == expected
            and str(row.get(".id", "") or "").strip()
        ))

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
            # Exact topic identity is mandatory for any automatic cleanup.
            # A mismatched prefix alone must never target another log rule.
            return same_topics
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
        """Idempotent LV logging setup, including half-installed RouterOS.

        Memory is a built-in action, not a custom action which must be listed
        by /system/logging/action/print. Keep foreign rules unchanged.
        """
        rows = cls._logging_rows(api)
        action_names = cls._logging_action_names(api)
        own = cls._find_logging_rule(rows, topics, prefix)
        # An existing system memory rule for the topic is enough for reading
        # events. Do not edit or disable a foreign logging rule.
        system_memory = next((
            row for row in rows
            if cls._topic_set(row.get("topics")) == cls._topic_set(topics)
            and str(row.get("action", "") or "").strip() == "memory"
            and not cls._bool(row.get("disabled", "no"))
        ), None)
        if not own and system_memory is not None:
            return

        rid = str((own or {}).get(".id", "") or "").strip()
        action = str((own or {}).get("action", "") or "").strip()
        usable_custom = LV_LOG_ACTION in action_names
        candidates = (
            ([LV_LOG_ACTION, "memory"] if usable_custom else ["memory"])
            if action != "memory" else ["memory"]
        )

        if own and action == LV_LOG_ACTION and usable_custom:
            cls._set_logging_rule_enabled(api, rid, enabled)
            return
        if own and action == "memory":
            cls._set_logging_rule_enabled(api, rid, enabled)
            return

        created = None
        for candidate in candidates:
            try:
                if own and rid:
                    api.set("/system/logging", rid, {"action": candidate})
                    created = dict(own, action=candidate)
                else:
                    returned = str(api.add("/system/logging", {
                        "topics": topics, "action": candidate,
                    }) or "").strip()
                    created = {".id": returned, "topics": topics, "action": candidate}
                break
            except Exception as exc:
                if candidate == LV_LOG_ACTION and cls._invalid_action_reference(exc):
                    continue
                raise cls._component_error("/system/logging", topics, "topics/action", exc) from exc

        if created is None:
            raise RuntimeError(f"RouterOS не принял правило LV {topics} ни с LVAuth, ни с memory")

        selected_action = str(created["action"])
        if not str(created.get(".id", "") or "").strip():
            refreshed = cls._logging_rows(api)
            matched = next((
                row for row in refreshed
                if cls._topic_set(row.get("topics")) == cls._topic_set(topics)
                and str(row.get("action", "") or "").strip() == selected_action
                and (
                    str(row.get("prefix", "") or "").strip() in ("", prefix)
                    or selected_action == LV_LOG_ACTION
                )
            ), None)
            if not matched:
                raise RuntimeError(
                    f"RouterOS не подтвердил созданное правило LV {topics}, action={selected_action}"
                )
            created = matched

        rid = str(created.get(".id", "") or "").strip()
        # Prefix/regex are optional and some RouterOS builds reject regex.
        # Never fail the whole install because of an optional property.
        if rid:
            try:
                api.set("/system/logging", rid, {"prefix": prefix})
            except Exception as exc:
                if not cls._looks_like_optional_field_error(exc):
                    raise cls._component_error("/system/logging", topics, "prefix", exc) from exc
            cls._set_logging_rule_enabled(api, rid, enabled)

    @classmethod
    def _cleanup_lv_logging(cls, api) -> None:
        """Remove only provably redundant LV logging objects, not system rules."""
        rows = cls._logging_rows(api)
        actions = cls._logging_action_names(api)
        for topics, prefix in (
            ("ppp", LV_LOG_PREFIX_PPP),
            ("l2tp", LV_LOG_PREFIX_L2TP),
        ):
            owned = [
                row for row in rows
                if cls._managed_logging_rule(row, topics, prefix)
            ]
            if not owned:
                continue
            # An abandoned LVAuth rule without the custom action is unusable.
            # Delete it only after a functioning memory rule for this exact
            # topic has been verified, never touch any stock memory rule.
            if LV_LOG_ACTION not in actions and any(
                cls._topic_set(row.get("topics")) == cls._topic_set(topics)
                and str(row.get("action", "") or "").strip() == "memory"
                and not cls._bool(row.get("disabled", "no"))
                for row in rows
            ):
                for obsolete in owned:
                    if str(obsolete.get("action", "") or "") == LV_LOG_ACTION:
                        rid = str(obsolete.get(".id", "") or "").strip()
                        if rid:
                            api.remove("/system/logging", rid)
                continue
            # Never remove an active destination without a verified replacement.
            owned.sort(key=lambda row: (
                str(row.get("action", "") or "") == LV_LOG_ACTION
                and LV_LOG_ACTION in actions,
                str(row.get("prefix", "") or "") == prefix,
            ), reverse=True)
            for extra in owned[1:]:
                rid = str(extra.get(".id", "") or "").strip()
                if rid:
                    api.remove("/system/logging", rid)

        # Remove an unused custom LVAuth action only when no rule references it.
        remaining = cls._logging_rows(api)
        if (
            LV_LOG_ACTION in actions
            and not any(str(r.get("action", "") or "") == LV_LOG_ACTION for r in remaining)
        ):
            rid = cls._find_id(api, "/system/logging/action", "name", LV_LOG_ACTION)
            if rid:
                api.remove("/system/logging/action", rid)

    def _sources_for_routeros(self, api):
        """Install v6-compatible epoch-day sources on RouterOS 6.x.

        The regular 2.1.2 scripts use v7-only :timestamp/:tonsec.
        The version is read from /system/resource on the SAME connection
        before any LV scripts are created.
        """
        sources = super()._sources_for_routeros(api)
        try:
            info = api.print("/system/resource", {".proplist": "version"})
        except Exception:
            info = api.print("/system/resource")
        version = str((info[0] if info else {}).get("version", "") or "")
        if not version.startswith("6."):
            return sources
        from linkvideo_vpn_helper.services.routeros6_lv_clock import adapt_script_for_routeros6
        return {
            name: (lambda f=factory: adapt_script_for_routeros6(f()))
            for name, factory in sources.items()
        }

    def _ensure_components(self, api, *, preserve_pause: bool = True) -> None:
        # Repair named LV scripts and schedulers, preserving operator settings.
        super()._ensure_components(api, preserve_pause=preserve_pause)
        self._cleanup_lv_logging(api)

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
            def actual_rule(topics, prefix):
                owned = self._find_logging_rule(rules, topics, prefix)
                if owned:
                    return owned
                # An existing system memory rule is sufficient and is never
                # changed by the Helper.
                return next((
                    row for row in rules
                    if self._topic_set(row.get("topics")) == self._topic_set(topics)
                    and str(row.get("action", "") or "").strip() == "memory"
                    and not self._bool(row.get("disabled", "no"))
                ), None)

            ppp = actual_rule("ppp", LV_LOG_PREFIX_PPP)
            l2tp = actual_rule("l2tp", LV_LOG_PREFIX_L2TP)
            managed = [row for row in (ppp, l2tp) if row]
            status.logging_ready = (
                len(managed) == 2
                and all(
                    str(row.get("action", "") or "").strip() == "memory"
                    or str(row.get("action", "") or "").strip() in installed_actions
                    for row in managed
                )
            )
            status.logging_enabled = len(managed) == 2 and all(
                not self._bool(row.get("disabled", "no")) for row in managed
            )
        except Exception:
            pass
        return status
