from __future__ import annotations

"""Compatibility fixes for exact RouterOS search queries.

Some deployed RouterOS versions accept API query words such as ``?dst-port=``
but return an empty result instead of an error.  Interactive search used to
interpret that empty result as proof that a port did not exist.  The rest of
VPNService already treats an empty exact-query result as inconclusive and falls
back to a local filter over the menu; port/remote search must do the same.
"""

from linkvideo_vpn_helper.mikrotik.api_ssl_client import RouterOSAPIClient


_INSTALLED = False


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def install_routeros_search_compat() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from linkvideo_vpn_helper.services.search_service_core import FastSearchService as CoreFastSearchService

    original_port_hint = CoreFastSearchService._server_port_hint
    original_remote_lookup = CoreFastSearchService._logins_for_remote

    def robust_port_hint(self, server: str, creds, port: int) -> list[str]:
        target = int(port)
        # Preserve the fast exact-query path first.
        hints = original_port_hint(self, server, creds, target)
        if hints:
            return hints

        # Empty exact-query results are not authoritative on all RouterOS builds.
        # Re-read the NAT menu and filter locally, including dst-port ranges.
        proplist = ".id,dst-port,to-addresses,to-ports,comment,disabled"
        with RouterOSAPIClient(
            server,
            creds.username,
            creds.password,
            port=creds.port,
            timeout=creds.timeout,
        ) as api:
            try:
                rows = api.print("/ip/firewall/nat", {".proplist": proplist})
                if not rows:
                    rows = api.print("/ip/firewall/nat")
            except Exception:
                rows = api.print("/ip/firewall/nat")

        logins: list[str] = []
        remotes: list[str] = []
        for row in rows or []:
            ports = self.vpn_service._parse_ports(self.vpn_service._get_rule_external_port(row))
            if target not in ports:
                continue
            comment = str(row.get("comment", "") or "").strip()
            if comment:
                logins.append(comment)
            remote = self.vpn_service._normalize_ip(self.vpn_service._get_rule_remote(row))
            if remote:
                remotes.append(remote)
        return _dedupe(logins) + ["@remote:" + value for value in _dedupe(remotes)]

    def robust_remote_lookup(self, server: str, creds, remote: str) -> list[str]:
        target = self.vpn_service._normalize_ip(remote)
        if not target:
            return []

        names = original_remote_lookup(self, server, creds, target)
        if names:
            return names

        # Same compatibility rule for Profile/Secret exact queries.  This is only
        # reached when the fast query returned no owner for a NAT remote address.
        with RouterOSAPIClient(
            server,
            creds.username,
            creds.password,
            port=creds.port,
            timeout=creds.timeout,
        ) as api:
            try:
                secrets = api.print(
                    "/ppp/secret",
                    {".proplist": ".id,name,remote-address,profile"},
                )
            except Exception:
                secrets = api.print("/ppp/secret")
            try:
                profiles = api.print(
                    "/ppp/profile",
                    {".proplist": ".id,name,remote-address"},
                )
            except Exception:
                profiles = api.print("/ppp/profile")

        profile_names = {
            str(row.get("name", "") or "").strip()
            for row in profiles or []
            if self.vpn_service._normalize_ip(row.get("remote-address", "")) == target
            and str(row.get("name", "") or "").strip()
        }
        result: list[str] = []
        for row in secrets or []:
            direct_remote = self.vpn_service._normalize_ip(row.get("remote-address", ""))
            profile = str(row.get("profile", "") or "").strip()
            if direct_remote != target and profile not in profile_names:
                continue
            name = str(row.get("name", "") or "").strip()
            if name:
                result.append(name)
        return _dedupe(result)

    CoreFastSearchService._server_port_hint = robust_port_hint
    CoreFastSearchService._logins_for_remote = robust_remote_lookup

    # Runtime FastSearchService currently inherits both methods from the core
    # class. Assign explicitly as well so a future override cannot silently
    # reintroduce the regression.
    try:
        from linkvideo_vpn_helper.services.search_service import FastSearchService as RuntimeFastSearchService
        RuntimeFastSearchService._server_port_hint = robust_port_hint
        RuntimeFastSearchService._logins_for_remote = robust_remote_lookup
    except Exception:
        pass

    # Port search and the client card must use the same complete RouterOS NAT
    # inventory.  This also installs safe create/read-back verification and the
    # cumulative NAT byte/packet counters shown in the client card.
    from linkvideo_vpn_helper.services.nat_inventory_compat import install_nat_inventory_compat
    install_nat_inventory_compat()
    from linkvideo_vpn_helper.services.nat_conflict_compat import install_nat_conflict_compat
    install_nat_conflict_compat()
    try:
        from linkvideo_vpn_helper.ui.nat_counter_integration import install_nat_counter_ui
        install_nat_counter_ui()
    except Exception:
        # Service-side correctness is mandatory; UI enrichment must never block
        # Helper startup if a future page import changes.
        pass

    _INSTALLED = True
