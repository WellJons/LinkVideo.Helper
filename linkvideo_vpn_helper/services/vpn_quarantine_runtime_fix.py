from __future__ import annotations

"""Runtime hardening for LV quarantine/retention automation.

The lifecycle seeding pass may classify an old secret as Q without changing its
RouterOS enabled flag. In addition, using ``set disabled=yes comment=...`` in one
script statement made the actual disable operation harder to verify separately.
This compatibility layer makes the state transition explicit:

* LV-Aging uses the dedicated PPP ``disable``/``enable`` commands;
* enabling quarantine executes LV-Aging immediately instead of waiting for 03:20;
* updating automation while quarantine is already enabled also executes one pass;
* after the pass, every Q secret is verified and force-disabled if required.
"""

from linkvideo_vpn_helper.mikrotik.api_ssl_client import RouterOSAPIClient
from linkvideo_vpn_helper.services.app_logging import event, error


RUNTIME_VERSION = "1.1.1"
_INSTALLED = False


def _is_disabled(value) -> bool:
    return str(value or "no").strip().lower() in {"yes", "true", "1", "on", "disabled"}


def _harden_source(factory):
    source = factory()
    # Keep metadata and age logic untouched; only make the RouterOS state change
    # a separate, explicit menu command so it cannot be hidden inside comment set.
    source = source.replace(
        "/ppp secret set $sid disabled=yes comment=",
        "/ppp secret disable $sid\n                /ppp secret set $sid comment=",
    )
    source = source.replace(
        "/ppp secret set $sid disabled=no comment=",
        "/ppp secret enable $sid\n                /ppp secret set $sid comment=",
    )
    return source


def _run_aging_now(server, creds) -> tuple[int, int]:
    """Execute LV-Aging now and verify Q => disabled as a hard postcondition."""
    from linkvideo_vpn_helper.services.vpn_retention_policy import parse_extended_comment

    forced = 0
    with RouterOSAPIClient(
        server,
        creds.username,
        creds.password,
        port=creds.port,
        timeout=creds.timeout,
    ) as api:
        scripts = api.print("/system/script")
        script = next(
            (row for row in scripts if str(row.get("name", "") or "").strip() == "LV-Aging"),
            None,
        )
        if not script:
            raise RuntimeError("LV-Aging script не найден")
        script_id = str(script.get(".id", "") or "").strip()
        if not script_id:
            raise RuntimeError("RouterOS не вернул ID скрипта LV-Aging")

        # RouterOS /system script run executes by id/name with caller permissions.
        api.talk("/system/script/run", {".id": script_id})

        try:
            secrets = api.print("/ppp/secret", {".proplist": ".id,name,disabled,comment"})
        except Exception:
            secrets = api.print("/ppp/secret")

        for row in secrets:
            meta = parse_extended_comment(str(row.get("comment", "") or ""))
            if meta.state != "Q" or _is_disabled(row.get("disabled", "no")):
                continue
            rid = str(row.get(".id", "") or "").strip()
            if not rid:
                continue
            api.disable("/ppp/secret", rid)
            forced += 1

        # A false green status is worse than an error. Confirm every Q secret.
        try:
            verify = api.print("/ppp/secret", {".proplist": ".id,name,disabled,comment"})
        except Exception:
            verify = api.print("/ppp/secret")
        bad = []
        for row in verify:
            meta = parse_extended_comment(str(row.get("comment", "") or ""))
            if meta.state == "Q" and not _is_disabled(row.get("disabled", "no")):
                bad.append(str(row.get("name", "") or "").strip() or str(row.get(".id", "") or ""))
        if bad:
            raise RuntimeError(
                "RouterOS не отключил карантинные PPP-учётки: " + ", ".join(bad[:8])
            )
        quarantined = sum(
            1
            for row in verify
            if parse_extended_comment(str(row.get("comment", "") or "")).state == "Q"
        )
    event(
        "LV",
        "LV-Aging выполнен немедленно",
        f"{server} · карантинных {quarantined} · принудительно отключено {forced}",
    )
    return quarantined, forced


def install_quarantine_runtime_fix() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from linkvideo_vpn_helper.services import vpn_automation_service as public
    from linkvideo_vpn_helper.services import vpn_automation_service_core as core
    from linkvideo_vpn_helper.services import vpn_lifecycle
    from linkvideo_vpn_helper.services import vpn_retention_policy as retention

    # 1.1.1 identifies the first automation revision where Q is a verified
    # RouterOS-disabled state and quarantine is applied immediately on enable.
    retention.RETENTION_VERSION = RUNTIME_VERSION
    vpn_lifecycle.LV_AUTOMATION_VERSION = RUNTIME_VERSION
    core.LV_AUTOMATION_VERSION = RUNTIME_VERSION
    public.LV_AUTOMATION_VERSION = RUNTIME_VERSION

    original_compose = retention.compose_extended_comment

    def compose_extended_comment(base_comment, state, last_ns, created_ns, reason, version=None):
        return original_compose(
            base_comment,
            state,
            last_ns,
            created_ns,
            reason,
            RUNTIME_VERSION if version is None else version,
        )

    retention.compose_extended_comment = compose_extended_comment

    def fixed_activity_source():
        return _harden_source(retention.activity_script_source)

    def fixed_aging_source():
        return _harden_source(retention.aging_script_source)

    def fixed_restore_source():
        return _harden_source(retention.restore_script_source)

    sources = {
        core.LV_ACTIVITY_SCRIPT: fixed_activity_source,
        core.LV_AGING_SCRIPT: fixed_aging_source,
        core.LV_RESTORE_SCRIPT: fixed_restore_source,
    }
    core.VPNAutomationService.SCRIPT_SOURCES = dict(sources)
    public.VPNAutomationService.SCRIPT_SOURCES = dict(sources)

    original_install = public.VPNAutomationService.install_or_update
    original_quarantine = public.VPNAutomationService.set_quarantine_enabled

    def install_or_update(self, server, creds):
        status = original_install(self, server, creds)
        if status.aging_enabled:
            try:
                _run_aging_now(server, creds)
            except Exception as exc:
                error("LV", f"Немедленный LV-Aging после обновления не выполнен · {server}", exc)
                raise
            status = self.get_status(server, creds)
        return status

    def set_quarantine_enabled(self, server, creds, enabled):
        status = original_quarantine(self, server, creds, enabled)
        if enabled:
            try:
                _run_aging_now(server, creds)
            except Exception as exc:
                error("LV", f"Немедленный LV-Aging после включения карантина не выполнен · {server}", exc)
                raise
            status = self.get_status(server, creds)
        return status

    public.VPNAutomationService.install_or_update = install_or_update
    public.VPNAutomationService.set_quarantine_enabled = set_quarantine_enabled

    event("LV", "Quarantine runtime fix готов", f"automation {RUNTIME_VERSION} · немедленное применение")
    _INSTALLED = True
