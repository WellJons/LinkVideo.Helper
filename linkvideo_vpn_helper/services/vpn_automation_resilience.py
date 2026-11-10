from __future__ import annotations

"""Final compatibility guard for optional LV Automation RouterOS fields.

The deployed VPN fleet spans RouterOS builds that can accept the functional
script/scheduler component but reject a subsequent optional metadata property in
surprising ways (for example "item with such name already exists" while setting
``dont-require-permissions`` on an already-existing script).  Optional fields
must never make the whole LV installation fail; required source/policy/action
fields still fail normally.
"""


_INSTALLED = False


def install_vpn_automation_resilience() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from linkvideo_vpn_helper.services.vpn_automation_service import VPNAutomationService

    @classmethod
    def apply_optional_fields(cls, api, path: str, rid: str, name: str, fields):
        if not rid:
            return
        for field, value in list(fields or []):
            try:
                api.set(path, rid, {field: value})
            except Exception:
                # comment / dont-require-permissions / memory-* / start-time are
                # compatibility enhancements only. The required component was
                # already created or updated before this point.
                continue

    @classmethod
    def find_ids(cls, api, path: str, field: str, value: str):
        # CRITICAL: RouterOS API query words are valid ONLY for /print.
        # Never fall back to /find: it can return the first, unrelated ID
        # and overwrite LV-Activity with LV-Aging or LV-AutoRestore.
        # A full name/id-only /print is small and works on RouterOS 6/7.
        try:
            rows = api.print(path, {".proplist": f".id,{field}"})
        except Exception:
            # Legacy implementations may reject .proplist; retry once with
            # plain print, but propagate a genuine read failure.
            rows = api.print(path)
        expected = str(value).strip()
        return list(dict.fromkeys(
            str(row.get(".id", "") or "").strip()
            for row in (rows or [])
            if str(row.get(field, "") or "").strip() == expected
            and str(row.get(".id", "") or "").strip()
        ))

    VPNAutomationService._apply_optional_fields = apply_optional_fields
    # This runtime monkeypatch is installed at app startup. Keep its name
    # lookup semantics identical to the public service, never a weaker /find.
    VPNAutomationService._find_ids = find_ids
    _INSTALLED = True
