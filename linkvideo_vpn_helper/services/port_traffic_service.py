from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable

from linkvideo_vpn_helper.mikrotik.api_ssl_client import RouterOSAPIClient


@dataclass
class PortTrafficSample:
    port: int
    internal_port: int | None = None
    connections: int = 0
    seen_reply: int = 0
    orig_rate_bps: int = 0
    repl_rate_bps: int = 0
    orig_bytes: int = 0
    repl_bytes: int = 0
    rate_supported: bool = False

    @property
    def total_rate_bps(self) -> int:
        return max(0, int(self.orig_rate_bps)) + max(0, int(self.repl_rate_bps))

    @property
    def total_bytes(self) -> int:
        return max(0, int(self.orig_bytes)) + max(0, int(self.repl_bytes))


class PortTrafficService:
    """Read live traffic only for the selected client's dst-nat ports.

    Conntrack representation is not identical on every RouterOS generation.
    Current RouterOS exposes the original public destination in ``dst-port``
    and the translated destination in ``reply-src-address``/``reply-src-port``.
    Older deployed routers can omit one of those fields from API output even
    though the corresponding query still works. For that reason the sampler:

    * first asks once for connections whose translated reply source is the
      selected client's Remote Address;
    * falls back to an exact public ``dst-port`` query for a port not found in
      that client-specific result;
    * validates every row against the NAT mapping and every tuple field that is
      actually present, instead of treating a missing compatibility field as a
      negative match.

    The service stays outside ``fetch_client_snapshot`` so a search over all VPN
    servers never pays the conntrack cost.
    """

    CONNECTION_PROPLIST = (
        ".id,protocol,src-address,src-port,dst-address,dst-port,"
        "reply-src-address,reply-src-port,reply-dst-address,reply-dst-port,"
        "dstnat,seen-reply,tcp-state,orig-rate,repl-rate,orig-bytes,repl-bytes,"
        "orig-fasttrack-bytes,repl-fasttrack-bytes"
    )
    NAT_PROPLIST = ".id,chain,protocol,dst-port,to-addresses,to-ports,comment,disabled"

    @staticmethod
    def _int(value) -> int:
        if value in (None, ""):
            return 0
        text = str(value).strip()
        try:
            return int(float(text))
        except Exception:
            match = re.search(r"-?\d+", text.replace(" ", ""))
            return int(match.group(0)) if match else 0

    @staticmethod
    def _rate_bps(value) -> int:
        """Parse RouterOS rate strings such as 0bps, 13.2kbps or 4.8Mbps."""
        if value in (None, ""):
            return 0
        text = str(value).strip().replace(" ", "")
        try:
            return max(0, int(float(text)))
        except Exception:
            pass
        match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)([kKmMgGtT]?)(?:bit/s|bps)?", text, re.I)
        if not match:
            return max(0, PortTrafficService._int(text))
        number = float(match.group(1))
        unit = match.group(2).lower()
        scale = {"": 1, "k": 1_000, "m": 1_000_000, "g": 1_000_000_000, "t": 1_000_000_000_000}[unit]
        return max(0, int(number * scale))

    @staticmethod
    def _truthy(value) -> bool:
        return str(value or "").strip().lower() in {"yes", "true", "1", "on"}

    @staticmethod
    def _normalize_ip(value) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        if "/" in text:
            text = text.split("/", 1)[0]
        # Conservative IPv4 endpoint fallback for RouterOS/API variants that
        # return address:port in an address field rather than separate fields.
        if text.count(":") == 1 and "." in text:
            left, right = text.rsplit(":", 1)
            if right.isdigit():
                text = left
        return text.strip()

    @staticmethod
    def _endpoint_port(value) -> int:
        text = str(value or "").strip()
        if text.count(":") == 1 and "." in text:
            _host, port = text.rsplit(":", 1)
            if port.isdigit():
                return int(port)
        return 0

    @staticmethod
    def _ports(value) -> list[int]:
        text = str(value or "").strip()
        if not text:
            return []
        result: list[int] = []
        for part in re.split(r"[,; ]+", text):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                a, b = part.split("-", 1)
                if a.isdigit() and b.isdigit():
                    start, end = int(a), int(b)
                    if 0 <= end - start <= 128:
                        result.extend(range(start, end + 1))
                continue
            if part.isdigit():
                result.append(int(part))
        return result

    def _nat_map(self, api: RouterOSAPIClient, login: str, remote: str, ports: set[int]) -> dict[int, int | None]:
        rows: list[dict] = []
        try:
            rows = api.print(
                "/ip/firewall/nat",
                {".proplist": self.NAT_PROPLIST, "?comment=": str(login)},
            )
        except Exception:
            rows = []

        # Historical LinkVideo rules may have no comment. NAT is much smaller
        # than conntrack, so one proplist-only fallback is acceptable here.
        if not rows or not any(ports.intersection(self._ports(r.get("dst-port"))) for r in rows):
            try:
                rows = api.print("/ip/firewall/nat", {".proplist": self.NAT_PROPLIST})
            except Exception:
                try:
                    rows = api.print("/ip/firewall/nat")
                except Exception:
                    rows = []

        mapped: dict[int, int | None] = {}
        remote_norm = self._normalize_ip(remote)
        for row in rows:
            protocol = str(row.get("protocol", "") or "").strip().lower()
            if protocol and protocol != "tcp":
                continue
            if str(row.get("chain", "") or "").strip().lower() not in {"", "dstnat"}:
                continue
            if self._truthy(row.get("disabled")):
                continue
            row_remote = self._normalize_ip(row.get("to-addresses"))
            comment = str(row.get("comment", "") or "").strip()
            if remote_norm and row_remote and row_remote != remote_norm and comment != str(login):
                continue
            internal_ports = self._ports(row.get("to-ports"))
            internal = internal_ports[0] if internal_ports else None
            for port in self._ports(row.get("dst-port")):
                if port in ports and port not in mapped:
                    mapped[port] = internal
        return mapped

    def _query_connections(self, api: RouterOSAPIClient, query: dict[str, str]) -> list[dict]:
        params = {".proplist": self.CONNECTION_PROPLIST, **query}
        try:
            return api.print("/ip/firewall/connection", params)
        except Exception:
            try:
                return api.print("/ip/firewall/connection", query)
            except Exception:
                return []

    @staticmethod
    def _dedupe_rows(rows: Iterable[dict]) -> list[dict]:
        result: list[dict] = []
        seen: set[tuple] = set()
        for row in rows:
            key = (
                str(row.get(".id", "") or ""),
                str(row.get("protocol", "") or ""),
                str(row.get("src-address", "") or ""),
                str(row.get("src-port", "") or ""),
                str(row.get("dst-address", "") or ""),
                str(row.get("dst-port", "") or ""),
                str(row.get("reply-src-address", "") or ""),
                str(row.get("reply-src-port", "") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            result.append(row)
        return result

    def _rows_for_remote(self, api: RouterOSAPIClient, remote: str) -> list[dict]:
        remote = self._normalize_ip(remote)
        if not remote:
            return []
        rows = self._query_connections(api, {"?reply-src-address=": remote})
        result = []
        for row in rows:
            item = dict(row)
            item["_lv-query-source"] = "remote"
            result.append(item)
        return self._dedupe_rows(result)

    def _rows_for_port(self, api: RouterOSAPIClient, port: int) -> list[dict]:
        rows = self._query_connections(api, {"?dst-port=": str(port)})
        result = []
        for row in rows:
            item = dict(row)
            item["_lv-query-source"] = "port"
            result.append(item)
        return self._dedupe_rows(result)

    def _row_external_port_matches(self, row: dict, port: int) -> bool:
        explicit = self._ports(row.get("dst-port"))
        if explicit:
            return port in explicit
        embedded = self._endpoint_port(row.get("dst-address"))
        if embedded:
            return embedded == port
        # An exact RouterOS query is itself evidence when this RouterOS build did
        # not serialise the queried field back into the API result.
        return str(row.get("_lv-query-source", "")) == "port"

    def _row_matches_client(
        self,
        row: dict,
        port: int,
        remote: str,
        internal_port: int | None,
        nat_known: bool,
    ) -> bool:
        protocol = str(row.get("protocol", "") or "").strip().lower()
        if protocol and protocol != "tcp":
            return False
        if not self._row_external_port_matches(row, port):
            return False

        dstnat_raw = str(row.get("dstnat", "") or "").strip()
        if dstnat_raw and not self._truthy(dstnat_raw):
            return False

        remote = self._normalize_ip(remote)
        reply_remote = self._normalize_ip(row.get("reply-src-address"))
        dst_remote = self._normalize_ip(row.get("dst-address"))
        source = str(row.get("_lv-query-source", ""))
        if remote:
            if reply_remote and reply_remote != remote:
                # Some older output represents the post-DNAT address in the
                # destination field. Accept that explicit representation only.
                if dst_remote != remote:
                    return False
            elif not reply_remote and dst_remote and dst_remote == remote:
                pass
            elif not reply_remote and source == "remote":
                # RouterOS already applied ?reply-src-address=<remote> but did
                # not return that compatibility field in the row.
                pass
            elif not reply_remote and source == "port" and not nat_known:
                # With no translated tuple and no NAT mapping there is not
                # enough evidence to attribute this public port to this client.
                return False

        reply_port = self._int(row.get("reply-src-port"))
        if internal_port and reply_port and int(internal_port) != reply_port:
            return False
        return True

    def sample_client(
        self,
        server: str,
        credentials,
        login: str,
        remote_address: str,
        ports: Iterable[int],
    ) -> dict[int, PortTrafficSample]:
        wanted = {int(p) for p in ports if int(p) > 0}
        if not wanted:
            return {}

        result = {p: PortTrafficSample(port=p) for p in wanted}
        remote = self._normalize_ip(remote_address)

        with RouterOSAPIClient(
            server,
            credentials.username,
            credentials.password,
            port=credentials.port,
            timeout=credentials.timeout,
        ) as api:
            nat_map = self._nat_map(api, str(login), remote, wanted)

            # One client-specific query is normally enough for every forwarded
            # port and is much cheaper than dumping the global conntrack table.
            remote_rows = self._rows_for_remote(api, remote)

            for port in sorted(wanted):
                sample = result[port]
                sample.internal_port = nat_map.get(port)

                candidates = [row for row in remote_rows if self._row_external_port_matches(row, port)]
                if not candidates:
                    candidates = self._rows_for_port(api, port)

                for row in candidates:
                    if not self._row_matches_client(
                        row,
                        port,
                        remote,
                        sample.internal_port,
                        port in nat_map,
                    ):
                        continue

                    sample.connections += 1
                    if self._truthy(row.get("seen-reply")):
                        sample.seen_reply += 1

                    if row.get("orig-rate") not in (None, "") or row.get("repl-rate") not in (None, ""):
                        sample.rate_supported = True
                    sample.orig_rate_bps += self._rate_bps(row.get("orig-rate"))
                    sample.repl_rate_bps += self._rate_bps(row.get("repl-rate"))

                    # orig/repl bytes are the primary monotonic counters. The
                    # fasttrack counters are not added to avoid double-counting;
                    # they are used only when the normal counter is absent.
                    if row.get("orig-bytes") not in (None, ""):
                        sample.orig_bytes += max(0, self._int(row.get("orig-bytes")))
                    else:
                        sample.orig_bytes += max(0, self._int(row.get("orig-fasttrack-bytes")))
                    if row.get("repl-bytes") not in (None, ""):
                        sample.repl_bytes += max(0, self._int(row.get("repl-bytes")))
                    else:
                        sample.repl_bytes += max(0, self._int(row.get("repl-fasttrack-bytes")))

        return result
