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
        # Prefer exact/broad print because every supported RouterOS menu returns
        # stable internal .id there. /find is retained only as a last fallback
        # and uses an API query word rather than an assignment word.
        rows = []
        try:
            rows = api.print(path, {".proplist": f".id,{field}", f"?{field}=": value})
        except Exception:
            try:
                rows = api.print(path)
            except Exception:
                rows = []
        result = []
        for row in rows or []:
            if str(row.get(field, "") or "").strip() != str(value).strip():
                continue
            rid = str(row.get(".id", "") or "").strip()
            if rid and rid not in result:
                result.append(rid)
        if result:
            return result
        try:
            replies = api.talk(f"{path}/find", {f"?{field}=": value})
            return cls._ret_ids(replies)
        except Exception:
            return []

    VPNAutomationService._apply_optional_fields = apply_optional_fields
    VPNAutomationService._find_ids = find_ids
    _INSTALLED = True
