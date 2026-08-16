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
    diagnostic: str = ""

    @property
    def total_rate_bps(self) -> int:
        return max(0, int(self.orig_rate_bps)) + max(0, int(self.repl_rate_bps))

    @property
    def total_bytes(self) -> int:
        return max(0, int(self.orig_bytes)) + max(0, int(self.repl_bytes))


class PortTrafficService:
    """Read live traffic from one bounded DNAT conntrack snapshot.

    RouterOS documents ``dstnat=yes`` on a connection when that connection has
    gone through destination NAT / port forwarding.  Filtering by the NAT flag
    is substantially more compatible than filtering by tuple sub-fields: older
    RouterOS builds can display address:port together while newer builds expose
    dst-port/reply-src-port separately.

    The service therefore retrieves the active destination-NAT connections once
    per client-card refresh and performs all tuple matching locally.  Exact
    per-port queries are retained only as a compatibility fallback when a router
    returns no rows for the DNAT query.  No N-port query loop is used in the
    normal path.
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
            host, tail = text.rsplit(":", 1)
            if tail.isdigit():
                text = host
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

        covered: set[int] = set()
        for row in rows:
            covered.update(ports.intersection(self._ports(row.get("dst-port"))))

        # Old LinkVideo rules can have another/empty comment. NAT is much smaller
        # than conntrack; request only missing public ports.
        for port in sorted(ports - covered):
            try:
                rows.extend(
                    api.print(
                        "/ip/firewall/nat",
                        {".proplist": self.NAT_PROPLIST, "?dst-port=": str(port)},
                    )
                )
            except Exception:
                pass

        mapped: dict[int, int | None] = {}
        remote_norm = self._normalize_ip(remote)
        for row in rows:
            protocol = str(row.get("protocol", "") or "").strip().lower()
            if protocol and protocol != "tcp":
                continue
            chain = str(row.get("chain", "") or "").strip().lower()
            if chain not in {"", "dstnat"}:
                continue
            if self._truthy(row.get("disabled")):
                continue
            row_remote = self._normalize_ip(row.get("to-addresses"))
            comment = str(row.get("comment", "") or "").strip()
            if remote_norm and row_remote and row_remote != remote_norm and comment != str(login):
                continue
            internal_ports = self._ports(row.get("to-ports"))
            internal = internal_ports[0] if internal_ports else None
            for public in self._ports(row.get("dst-port")):
                if public in ports and public not in mapped:
                    mapped[public] = internal if internal is not None else public
        return mapped

    def _print_connections(self, api: RouterOSAPIClient, query: dict[str, str]) -> list[dict]:
        params = {".proplist": self.CONNECTION_PROPLIST, **query}
        try:
            return [dict(row) for row in api.print("/ip/firewall/connection", params)]
        except Exception:
            try:
                return [dict(row) for row in api.print("/ip/firewall/connection", query)]
            except Exception:
                return []

    @staticmethod
    def _dedupe(rows: Iterable[dict]) -> list[dict]:
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

    def _connection_snapshot(self, api: RouterOSAPIClient, wanted: set[int], remote: str, nat_map: dict[int, int | None]) -> tuple[list[dict], str]:
        # Primary path: one RouterOS-side filter that is independent of the
        # separate-vs-embedded port representation.
        rows = self._dedupe(self._print_connections(api, {"?dstnat=": "yes"}))
        if rows:
            return rows, f"DNAT snapshot: {len(rows)} записей"

        # Compatibility fallback for builds that do not expose/query dstnat as
        # expected. Try exact public ports and translated endpoints, then merge.
        fallback: list[dict] = []
        for port in sorted(wanted):
            fallback.extend(self._print_connections(api, {"?dst-port=": str(port)}))
            internal = nat_map.get(port)
            if remote and internal:
                fallback.extend(self._print_connections(api, {"?reply-src-address=": f"{remote}:{int(internal)}"}))
                fallback.extend(
                    self._print_connections(
                        api,
                        {"?reply-src-address=": remote, "?reply-src-port=": str(int(internal))},
                    )
                )
        rows = self._dedupe(fallback)
        return rows, f"DNAT query вернул 0; fallback: {len(rows)} записей"

    def _external_port(self, row: dict) -> int:
        ports = self._ports(row.get("dst-port"))
        if ports:
            return ports[0]
        return self._endpoint_port(row.get("dst-address"))

    def _reply_ip(self, row: dict) -> str:
        return self._normalize_ip(row.get("reply-src-address"))

    def _reply_port(self, row: dict) -> int:
        value = self._int(row.get("reply-src-port"))
        return value or self._endpoint_port(row.get("reply-src-address"))

    def _row_matches(self, row: dict, port: int, remote: str, internal_port: int | None, nat_known: bool) -> bool:
        protocol = str(row.get("protocol", "") or "").strip().lower()
        if protocol and protocol != "tcp":
            return False

        external = self._external_port(row)
        if external and external != int(port):
            return False
        if not external:
            return False

        dstnat_raw = str(row.get("dstnat", "") or "").strip()
        if dstnat_raw and not self._truthy(dstnat_raw):
            return False

        remote_norm = self._normalize_ip(remote)
        reply_ip = self._reply_ip(row)
        if remote_norm and reply_ip and reply_ip != remote_norm:
            return False
        if remote_norm and not reply_ip and not nat_known:
            return False

        reply_port = self._reply_port(row)
        if internal_port and reply_port and reply_port != int(internal_port):
            return False
        return True

    @staticmethod
    def _row_preview(rows: list[dict], limit: int = 3) -> str:
        bits = []
        for row in rows[:limit]:
            bits.append(
                f"{row.get('protocol', '?')} "
                f"{row.get('src-address', '?')}:{row.get('src-port', '')} → "
                f"{row.get('dst-address', '?')}:{row.get('dst-port', '')} | reply "
                f"{row.get('reply-src-address', '?')}:{row.get('reply-src-port', '')}"
            )
        return "; ".join(bits)

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
        traffic_timeout = min(4.0, max(1.5, configured_timeout))

        with RouterOSAPIClient(
            server,
            credentials.username,
            credentials.password,
            port=credentials.port,
            timeout=traffic_timeout,
        ) as api:
            nat_map = self._nat_map(api, str(login), remote, wanted)
            rows, snapshot_note = self._connection_snapshot(api, wanted, remote, nat_map)
            preview = self._row_preview(rows)

            for port in sorted(wanted):
                sample = result[port]
                sample.internal_port = nat_map.get(port)
                matched = [
                    row
                    for row in rows
                    if self._row_matches(row, port, remote, sample.internal_port, port in nat_map)
                ]
                sample.diagnostic = (
                    f"{snapshot_note}; NAT {port}→{remote or '?'}:{sample.internal_port or '?'}; "
                    f"совпадений: {len(matched)}"
                    + (f"; примеры: {preview}" if not matched and preview else "")
                )

                for row in matched:
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
