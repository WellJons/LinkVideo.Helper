from __future__ import annotations

"""Preserve extended LV2 metadata when the operator initializes lifecycle state.

The legacy core seed routine only knows ``state`` and ``last``. Calling it after
3.0.10 would therefore rewrite a compact extended LV2 marker and lose ``c``
(the never-active creation/reference day) and ``r`` (the reason code). This small
adapter replaces only that seed entry point and lets the authoritative retention
policy do the migration/classification itself.

Initialization must also stay semantically non-destructive: an enabled PPP Secret
must never be left with ``s=Q`` merely because it is old enough for quarantine.
Until LV-Aging is actually enabled/applied such an account remains ``S`` (sleeping).
"""


_INSTALLED = False


def install_retention_seed_guard() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from linkvideo_vpn_helper.mikrotik.api_ssl_client import RouterOSAPIClient
    from linkvideo_vpn_helper.services import vpn_automation_service as public
    from linkvideo_vpn_helper.services import vpn_automation_service_core as core
    from linkvideo_vpn_helper.services.app_logging import event
    from linkvideo_vpn_helper.services.vpn_retention_policy import (
        _bool,
        _ensure_tracking_for_server,
        compose_extended_comment,
        parse_extended_comment,
    )

    def seed_lifecycle(self, server, creds):
        changed = _ensure_tracking_for_server(server, creds)
        counts = {"A": 0, "S": 0, "Q": 0, "R": 0, "M": 0, "U": 0}

        with RouterOSAPIClient(
            server,
            creds.username,
            creds.password,
            port=creds.port,
            timeout=creds.timeout,
        ) as api:
            try:
                rows = api.print("/ppp/secret", {".proplist": ".id,name,disabled,comment"})
            except Exception:
                rows = api.print("/ppp/secret")

            normalized_rows: list[dict] = []
            for raw in rows:
                row = dict(raw)
                rid = str(row.get(".id", "") or "").strip()
                login = str(row.get("name", "") or "").strip()
                if not login:
                    continue
                meta = parse_extended_comment(str(row.get("comment", "") or ""))
                disabled = _bool(row.get("disabled", "no"))

                # Q means an account is actually quarantined. If initialization
                # merely discovered an old enabled account, retain the inactivity
                # timestamp/creation day but label it S until policy enforcement.
                if meta.state == "Q" and not disabled and rid:
                    new_comment = compose_extended_comment(
                        meta.base_comment,
                        "S",
                        meta.last_ns,
                        meta.created_ns,
                        "inactive_30",
                    )
                    api.set("/ppp/secret", rid, {"comment": new_comment})
                    row["comment"] = new_comment
                    changed += 1
                normalized_rows.append(row)

        for row in normalized_rows:
            meta = parse_extended_comment(str(row.get("comment", "") or ""))
            state = meta.state if meta.state in counts else "U"
            counts[state] += 1

        result = core.SeedResult(
            server=server,
            total=sum(counts.values()),
            changed=changed,
            active=counts["A"],
            sleeping=counts["S"],
            quarantine=counts["Q"],
            archive=counts["R"],
            manual=counts["M"],
            unknown=counts["U"],
        )
        event(
            "LV",
            "Lifecycle инициализирован без потери LV2 metadata",
            f"{server} · всего {result.total} · изменено {result.changed}",
        )
        return result

    public.VPNAutomationService.seed_lifecycle = seed_lifecycle
    _INSTALLED = True
