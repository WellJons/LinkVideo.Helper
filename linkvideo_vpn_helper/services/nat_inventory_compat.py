from __future__ import annotations

"""Reliable client ↔ NAT inventory for mixed old/new RouterOS configurations.

Old LinkVideo VPN records were often created manually.  Some NAT rules have no
comment, some use a free-form comment, and PPP Secret may point to a Profile
whose name differs from the login.  The Helper must therefore treat RouterOS
addresses/profile relations as authoritative and never use ``comment=login`` as
proof that the returned NAT subset is complete.

This module also exposes RouterOS firewall-rule byte/packet counters per port.
Those counters are cumulative rule statistics; they are deliberately NOT used
as a live/online status.
"""

from typing import Any

from linkvideo_vpn_helper.mikrotik.api_ssl_client import RouterOSAPIClient
from linkvideo_vpn_helper.services.vpn_service import VPNService


_INSTALLED = False


def _parse_int(value: Any) -> int:
    try:
        return max(0, int(str(value or "0").strip()))
    except Exception:
        return 0


def _parse_ports(raw_value: Any) -> list[int]:
    raw = str(raw_value or "").strip()
    if not raw:
        return []
    result: list[int] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            left, right = chunk.split("-", 1)
            try:
                start, end = int(left.strip()), int(right.strip())
            except Exception:
                continue
            if 0 < start <= end <= 65535:
                result.extend(range(start, end + 1))
            continue
        try:
            port = int(chunk)
        except Exception:
            continue
        if 0 < port <= 65535:
            result.append(port)
    return result


def _rule_external_ports(rule: dict[str, Any]) -> list[int]:
    raw = rule.get("dst-port", rule.get("dst_port", ""))
    if raw in (None, ""):
        raw = rule.get("to-ports", rule.get("to_ports", ""))
    return _parse_ports(raw)


def _dedupe_rules(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows or []:
        rid = str(row.get(".id", "") or "").strip()
        key = rid or "|".join(
            str(row.get(name, "") or "")
            for name in ("chain", "protocol", "dst-port", "to-addresses", "to-ports", "comment")
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def install_nat_inventory_compat() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    original_fetch_client_snapshot = VPNService.fetch_client_snapshot
    original_build_client_records = VPNService._build_client_records
    original_api_print_exact = VPNService._api_print_exact
    original_create_clients_batch = VPNService.create_clients_batch

    def robust_api_print_exact(api, path: str, field: str, value: str, proplist: str | None = None) -> list[dict]:
        rows = original_api_print_exact(api, path, field, value, proplist)
        if path != "/ip/firewall/nat" or field != "dst-port":
            return rows

        try:
            target = int(str(value).strip())
        except Exception:
            return rows

        # Exact RouterOS query does not match a range such as 12000-12005 and on
        # some deployed versions may return an empty list without an error.  For
        # port-allocation safety, an empty/inconclusive answer must be followed by
        # a local scan of the NAT menu.
        if any(target in _rule_external_ports(row) for row in rows or []):
            return rows
        params = {".proplist": proplist} if proplist else None
        try:
            all_rows = api.print("/ip/firewall/nat", params or {})
            if not all_rows:
                all_rows = api.print("/ip/firewall/nat")
        except Exception:
            all_rows = api.print("/ip/firewall/nat")
        return [row for row in all_rows or [] if target in _rule_external_ports(row)]

    def robust_fetch_client_snapshot(self: VPNService, server, creds, login: str):
        login = str(login or "").strip()
        if not login:
            raise ValueError("Логин клиента не указан")

        def exact(api, path: str, field: str, value: str, proplist: str):
            try:
                rows = api.print(path, {".proplist": proplist, f"?{field}=": value})
                if rows:
                    return rows
            except Exception:
                pass
            try:
                rows = api.print(path, {".proplist": proplist})
                if not rows:
                    rows = api.print(path)
            except Exception:
                rows = api.print(path)
            return [row for row in rows or [] if str(row.get(field, "") or "").strip() == str(value)]

        with RouterOSAPIClient(server, creds.username, creds.password, port=creds.port, timeout=creds.timeout) as api:
            secrets = exact(
                api,
                "/ppp/secret",
                "name",
                login,
                ".id,name,password,profile,service,disabled,last-logged-out,remote-address,comment",
            )
            if not secrets:
                return {
                    "secrets": [], "actives": [], "profiles": [], "nat_rules": [],
                    "connections": [], "resources": [], "interfaces": [], "traffic_monitors": {},
                }

            secret = secrets[0]
            profile_name = str(secret.get("profile", "") or login).strip()
            profiles = exact(
                api,
                "/ppp/profile",
                "name",
                profile_name,
                ".id,name,local-address,remote-address",
            )
            actives = exact(
                api,
                "/ppp/active",
                "name",
                login,
                ".id,name,address,caller-id,uptime,encoding,service,bytes,packets,rx-byte,tx-byte,rx-bits-per-second,tx-bits-per-second",
            )
            remote = self._get_client_remote_address(secret, profiles[0] if profiles else {})

            # Correctness first: read the complete lightweight NAT inventory and
            # filter locally.  A comment query is not sufficient because legacy
            # manually-created rules are often un-commented.
            nat_proplist = (
                ".id,chain,action,protocol,dst-port,to-addresses,to-ports,"
                "comment,disabled,bytes,packets"
            )
            try:
                all_nat = api.print("/ip/firewall/nat", {".proplist": nat_proplist})
                if not all_nat:
                    all_nat = api.print("/ip/firewall/nat")
            except Exception:
                all_nat = api.print("/ip/firewall/nat")

            nat_rules: list[dict[str, Any]] = []
            for row in all_nat or []:
                comment = str(row.get("comment", "") or "").strip()
                rule_remote = self._normalize_ip(self._get_rule_remote(row))
                if comment == login or (remote and rule_remote == remote):
                    nat_rules.append(row)
            nat_rules = _dedupe_rules(nat_rules)

            # Very old records may have lost/changed the PPP profile while their
            # NAT comment still identifies the login.  If all comment-matched NAT
            # rules point to one address, use it as a recovery hint for the card.
            if not remote:
                hinted = {
                    self._normalize_ip(self._get_rule_remote(row))
                    for row in nat_rules
                    if str(row.get("comment", "") or "").strip() == login
                }
                hinted.discard("")
                if len(hinted) == 1:
                    recovered_remote = next(iter(hinted))
                    if profiles:
                        patched = dict(profiles[0])
                        patched["remote-address"] = recovered_remote
                        profiles = [patched]
                    else:
                        profiles = [{
                            ".id": "",
                            "name": profile_name or login,
                            "local-address": "",
                            "remote-address": recovered_remote,
                        }]
                    remote = recovered_remote
                    # Add every NAT pointing to the recovered address, not just
                    # the commented rule that gave us the hint.
                    for row in all_nat or []:
                        if self._normalize_ip(self._get_rule_remote(row)) == remote:
                            nat_rules.append(row)
                    nat_rules = _dedupe_rules(nat_rules)

            interfaces = []
            try:
                interfaces = api.print(
                    "/interface",
                    {".proplist": ".id,name,type,running,dynamic,rx-byte,tx-byte,rx-bits-per-second,tx-bits-per-second"},
                )
            except Exception:
                try:
                    interfaces = api.print("/interface")
                except Exception:
                    interfaces = []

        return {
            "secrets": secrets,
            "actives": actives,
            "profiles": profiles,
            "nat_rules": nat_rules,
            # Per-port live state from conntrack remains intentionally disabled;
            # cumulative NAT rule counters are collected separately below.
            "connections": [],
            "resources": [],
            "interfaces": interfaces,
            "traffic_monitors": {},
        }

    def robust_build_client_records(self: VPNService, server: str, snapshot):
        # The legacy builder looked up Profile by login.  Preserve its behaviour
        # while feeding it an alias for the Secret's actual profile name.
        patched = dict(snapshot or {})
        patched["secrets"] = [dict(row) for row in (snapshot.get("secrets", []) or [])]
        patched["profiles"] = [dict(row) for row in (snapshot.get("profiles", []) or [])]
        patched["nat_rules"] = [dict(row) for row in (snapshot.get("nat_rules", []) or [])]
        patched.setdefault("actives", list(snapshot.get("actives", []) or []))
        patched.setdefault("connections", list(snapshot.get("connections", []) or []))
        patched.setdefault("interfaces", list(snapshot.get("interfaces", []) or []))
        patched.setdefault("traffic_monitors", dict(snapshot.get("traffic_monitors", {}) or {}))

        profiles_by_name = {
            str(row.get("name", "") or "").strip(): row
            for row in patched["profiles"]
            if str(row.get("name", "") or "").strip()
        }
        for secret in patched["secrets"]:
            login = str(secret.get("name", "") or "").strip()
            profile_name = str(secret.get("profile", "") or login).strip()
            profile = profiles_by_name.get(profile_name)
            if login and profile is not None and profile_name != login and login not in profiles_by_name:
                alias = dict(profile)
                alias["name"] = login
                patched["profiles"].append(alias)
                profiles_by_name[login] = alias

        records = original_build_client_records(self, server, patched)

        # Attach cumulative NAT counters to each concrete external port.  For a
        # ranged RouterOS rule the same rule-level cumulative counter is visible
        # on every expanded port; UI explicitly labels it as a NAT rule counter.
        for client in records:
            bytes_by_port: dict[int, int] = {int(port): 0 for port in client.ports}
            packets_by_port: dict[int, int] = {int(port): 0 for port in client.ports}
            client_remote = self._normalize_ip(client.remote_address)
            for rule in patched["nat_rules"]:
                comment = str(rule.get("comment", "") or "").strip()
                rule_remote = self._normalize_ip(self._get_rule_remote(rule))
                if comment != client.login and (not client_remote or rule_remote != client_remote):
                    continue
                rule_bytes = _parse_int(rule.get("bytes", 0))
                rule_packets = _parse_int(rule.get("packets", rule.get("packet-count", 0)))
                for port in _rule_external_ports(rule):
                    if port not in bytes_by_port:
                        continue
                    bytes_by_port[port] += rule_bytes
                    packets_by_port[port] += rule_packets
            client.port_nat_bytes = bytes_by_port
            client.port_nat_packets = packets_by_port

        return records

    def verified_create_clients_batch(self: VPNService, server, creds, base_login, ports_per_client, accounts_count, progress_callback=None, cancel_event=None):
        created = original_create_clients_batch(
            self,
            server,
            creds,
            base_login,
            ports_per_client,
            accounts_count,
            progress_callback,
            cancel_event,
        )
        if not created:
            return created

        try:
            # One authoritative read-back after the batch.  UI receives the actual
            # RouterOS records rather than only the locally planned port list.
            snapshot = self.fetch_config_snapshot(server, creds)
            actual_by_login = {
                item.login: item
                for item in self._build_client_records(server, snapshot)
            }
            verified = []
            failures: list[str] = []
            for planned in created:
                actual = actual_by_login.get(planned.login)
                expected_ports = set(int(port) for port in planned.ports)
                actual_ports = set(int(port) for port in (actual.ports if actual else []))
                if actual is None:
                    failures.append(f"{planned.login}: PPP Secret не найден после создания")
                    continue
                missing = sorted(expected_ports - actual_ports)
                if missing:
                    failures.append(f"{planned.login}: RouterOS не подтвердил NAT-порты {', '.join(map(str, missing))}")
                    continue
                # Password is normally readable from /ppp/secret.  Preserve the
                # generated value if this RouterOS omits it from print output.
                if not actual.password:
                    actual.password = planned.password
                verified.append(actual)

            if failures:
                raise RuntimeError("Проверка созданных учёток не пройдена: " + "; ".join(failures))
            return verified
        except Exception:
            # Do not leave half-verified accounts behind.  This uses the creation
            # ledger returned by the successful add operations and does not depend
            # on role-gated delete_client().
            for item in reversed(created):
                try:
                    self._rollback_create(server, creds, item.profile_id, item.secret_id, list(item.nat_rule_ids or []))
                except Exception:
                    pass
            raise

    VPNService._api_print_exact = staticmethod(robust_api_print_exact)
    VPNService.fetch_client_snapshot = robust_fetch_client_snapshot
    VPNService._build_client_records = robust_build_client_records
    VPNService.create_clients_batch = verified_create_clients_batch

    _INSTALLED = True
