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
    """Read live traffic for selected LinkVideo dst-nat ports.

    The VPN fleet is not homogeneous. Newer RouterOS builds expose connection
    tuple fields separately (``dst-port``, ``reply-src-port``), while older API
    output can serialize an endpoint as ``address:port`` in ``dst-address`` or
    ``reply-src-address``. API query words compare an exact property value, so a
    query such as ``?dst-port=11158`` necessarily returns nothing on a build
    whose connection rows do not have a separate ``dst-port`` property.

    For each requested port we therefore use the NAT rule as the authoritative
    mapping and try bounded conntrack queries in this order:

    1. modern translated tuple: reply-src-address + reply-src-port;
    2. legacy translated endpoint: reply-src-address=IP:PORT;
    3. modern public tuple: dst-port=external-port;
    4. final bounded fallback: dstnat=yes, filtered locally.

    No unfiltered connection-table dump is performed. The UI currently asks for
    one port at a time, so even the last fallback remains bounded to active
    destination-NAT connections rather than every tracked connection.
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
    def _endpoint(ip: str, port: int | None) -> str:
        ip = PortTrafficService._normalize_ip(ip)
        if not ip or not port:
            return ""
        return f"{ip}:{int(port)}"

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
            rows.extend(
                api.print(
                    "/ip/firewall/nat",
                    {".proplist": self.NAT_PROPLIST, "?comment=": str(login)},
                )
            )
        except Exception:
            pass

        covered = set()
        for row in rows:
            covered.update(ports.intersection(self._ports(row.get("dst-port"))))

        # Firewall NAT rules have a stable dst-port property even on older
        # RouterOS. Query only missing ports rather than dumping all NAT rules.
        for port in sorted(ports - covered):
            try:
                rows.extend(
                    api.print(
                        "/ip/firewall/nat",
                        {".proplist": self.NAT_PROPLIST, "?dst-port=": str(port)},
                    )
                )
            except Exception:
                try:
                    rows.extend(api.print("/ip/firewall/nat", {"?dst-port=": str(port)}))
                except Exception:
                    pass

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
                    # If dst-nat omits to-ports, RouterOS keeps the destination
                    # port unchanged. Treat the external port as the translated
                    # port for tuple matching.
                    mapped[port] = internal if internal is not None else port
        return mapped

    def _query(self, api: RouterOSAPIClient, query: dict[str, str], source: str) -> list[dict]:
        try:
            rows = api.print(
                "/ip/firewall/connection",
                {".proplist": self.CONNECTION_PROPLIST, **query},
            )
        except Exception:
            # A legacy build can reject one of the modern .proplist names even
            # though the query itself is valid. Preserve the query and retry.
            try:
                rows = api.print("/ip/firewall/connection", query)
            except Exception:
                return []

        result: list[dict] = []
        seen: set[tuple] = set()
        for row in rows:
            item = dict(row)
            item["_lv-query-source"] = source
            key = (
                str(item.get(".id", "") or ""),
                str(item.get("protocol", "") or ""),
                str(item.get("src-address", "") or ""),
                str(item.get("src-port", "") or ""),
                str(item.get("dst-address", "") or ""),
                str(item.get("dst-port", "") or ""),
                str(item.get("reply-src-address", "") or ""),
                str(item.get("reply-src-port", "") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            result.append(item)
        return result

    def _row_external_port_matches(self, row: dict, port: int) -> bool:
        explicit = self._ports(row.get("dst-port"))
        if explicit:
            return port in explicit
        embedded = self._endpoint_port(row.get("dst-address"))
        if embedded:
            return embedded == port
        # Exact public-port query is evidence if the queried property is not
        # serialised back in this RouterOS API response.
        return str(row.get("_lv-query-source", "")) == "public-port"

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

        remote_norm = self._normalize_ip(remote)
        reply_raw = str(row.get("reply-src-address", "") or "").strip()
        reply_remote = self._normalize_ip(reply_raw)
        source = str(row.get("_lv-query-source", ""))

        if remote_norm:
            if reply_remote and reply_remote != remote_norm:
                return False
            if not reply_remote and source not in {"modern-reply", "legacy-reply"} and not nat_known:
                return False

        reply_port = self._int(row.get("reply-src-port"))
        if not reply_port:
            reply_port = self._endpoint_port(reply_raw)
        if internal_port and reply_port and int(internal_port) != reply_port:
            return False

        return True

    def _candidate_rows(
        self,
        api: RouterOSAPIClient,
        port: int,
        remote: str,
        internal_port: int | None,
        nat_known: bool,
    ) -> list[dict]:
        """Return the first useful bounded conntrack result for this port."""
        remote = self._normalize_ip(remote)

        # RouterOS 7-style tuple: translated address and port are independent
        # properties. Two query words are ANDed by the API query stack.
        if remote and internal_port:
            rows = self._query(
                api,
                {
                    "?reply-src-address=": remote,
                    "?reply-src-port=": str(int(internal_port)),
                },
                "modern-reply",
            )
            matched = [r for r in rows if self._row_matches_client(r, port, remote, internal_port, nat_known)]
            if matched:
                return matched

            # RouterOS 6/legacy representation: the port is part of the address
            # property, so an exact query must include IP:PORT.
            legacy_endpoint = self._endpoint(remote, internal_port)
            if legacy_endpoint:
                rows = self._query(
                    api,
                    {"?reply-src-address=": legacy_endpoint},
                    "legacy-reply",
                )
                matched = [r for r in rows if self._row_matches_client(r, port, remote, internal_port, nat_known)]
                if matched:
                    return matched

        # Modern public tuple. This is cheap and also covers cases where a NAT
        # rule has no translated address property in conntrack output.
        rows = self._query(api, {"?dst-port=": str(port)}, "public-port")
        matched = [r for r in rows if self._row_matches_client(r, port, remote, internal_port, nat_known)]
        if matched:
            return matched

        # Last compatibility fallback: retrieve only connections RouterOS itself
        # marks as destination-NAT and filter the public + translated tuples in
        # Python. This avoids an unrestricted conntrack dump.
        rows = self._query(api, {"?dstnat=": "yes"}, "dstnat")
        return [r for r in rows if self._row_matches_client(r, port, remote, internal_port, nat_known)]

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
        configured_timeout = float(getattr(credentials, "timeout", 3.5) or 3.5)
        traffic_timeout = min(3.5, max(1.5, configured_timeout))

        with RouterOSAPIClient(
            server,
            credentials.username,
            credentials.password,
            port=credentials.port,
            timeout=traffic_timeout,
        ) as api:
            nat_map = self._nat_map(api, str(login), remote, wanted)

            for port in sorted(wanted):
                sample = result[port]
                sample.internal_port = nat_map.get(port)
                rows = self._candidate_rows(
                    api,
                    port,
                    remote,
                    sample.internal_port,
                    port in nat_map,
                )

                for row in rows:
                    sample.connections += 1
                    if self._truthy(row.get("seen-reply")):
                        sample.seen_reply += 1

                    if row.get("orig-rate") not in (None, "") or row.get("repl-rate") not in (None, ""):
                        sample.rate_supported = True
                    sample.orig_rate_bps += self._rate_bps(row.get("orig-rate"))
                    sample.repl_rate_bps += self._rate_bps(row.get("repl-rate"))

                    if row.get("orig-bytes") not in (None, ""):
                        sample.orig_bytes += max(0, self._int(row.get("orig-bytes")))
                    else:
                        sample.orig_bytes += max(0, self._int(row.get("orig-fasttrack-bytes")))
                    if row.get("repl-bytes") not in (None, ""):
                        sample.repl_bytes += max(0, self._int(row.get("repl-bytes")))
                    else:
                        sample.repl_bytes += max(0, self._int(row.get("repl-fasttrack-bytes")))

        return result
