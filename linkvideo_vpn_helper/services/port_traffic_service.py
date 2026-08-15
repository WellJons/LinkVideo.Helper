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

    The old Helper scanned the entire connection table and considered a port
    active whenever its number appeared in *any* src/dst/reply field. That can
    mark unrelated connections as active and is expensive on a busy VPN server.

    This sampler asks RouterOS for one external ``dst-port`` at a time and then
    validates the tracked connection against the client's translated address and
    internal port. It intentionally stays outside ``fetch_client_snapshot`` so a
    search across all VPN servers never pays the conntrack cost.
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
    def _truthy(value) -> bool:
        return str(value or "").strip().lower() in {"yes", "true", "1", "on"}

    @staticmethod
    def _normalize_ip(value) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        # RouterOS normally exposes address and port separately here, but keep a
        # conservative IPv4 fallback for older builds that may append ':port'.
        if "/" in text:
            text = text.split("/", 1)[0]
        if text.count(":") == 1 and "." in text:
            left, right = text.rsplit(":", 1)
            if right.isdigit():
                text = left
        return text.strip()

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

        # Some historical LinkVideo rules have no comment. NAT tables are much
        # smaller than conntrack, so one proplist-only fallback is acceptable.
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

    def _connection_rows(self, api: RouterOSAPIClient, port: int) -> list[dict]:
        # The exact query keeps RouterOS from serialising its whole conntrack
        # table to Helper. Older RouterOS variants may reject one of the newer
        # proplist fields; in that case retry the same filtered query without it.
        try:
            return api.print(
                "/ip/firewall/connection",
                {".proplist": self.CONNECTION_PROPLIST, "?dst-port=": str(port)},
            )
        except Exception:
            return api.print("/ip/firewall/connection", {"?dst-port=": str(port)})

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
            for port in sorted(wanted):
                sample = result[port]
                sample.internal_port = nat_map.get(port)
                rows = self._connection_rows(api, port)
                for row in rows:
                    protocol = str(row.get("protocol", "") or "").strip().lower()
                    if protocol and protocol != "tcp":
                        continue
                    row_ports = self._ports(row.get("dst-port"))
                    if row_ports and port not in row_ports:
                        continue

                    # If RouterOS exposes the dstnat flag it must be true. If an
                    # older build omits it, the translated reply tuple below is
                    # still sufficient to bind the connection to this client.
                    if "dstnat" in row and str(row.get("dstnat", "")).strip() and not self._truthy(row.get("dstnat")):
                        continue

                    reply_remote = self._normalize_ip(row.get("reply-src-address"))
                    if remote:
                        if not reply_remote or reply_remote != remote:
                            continue

                    internal = sample.internal_port
                    reply_port = self._int(row.get("reply-src-port"))
                    if internal and reply_port and int(internal) != reply_port:
                        continue

                    sample.connections += 1
                    if self._truthy(row.get("seen-reply")):
                        sample.seen_reply += 1

                    if "orig-rate" in row or "repl-rate" in row:
                        sample.rate_supported = True
                    sample.orig_rate_bps += max(0, self._int(row.get("orig-rate")))
                    sample.repl_rate_bps += max(0, self._int(row.get("repl-rate")))

                    # orig/repl bytes are the primary monotonic counters. The
                    # fasttrack counters are not added to avoid double-counting;
                    # they are used only if the normal counter is absent.
                    if row.get("orig-bytes") not in (None, ""):
                        sample.orig_bytes += max(0, self._int(row.get("orig-bytes")))
                    else:
                        sample.orig_bytes += max(0, self._int(row.get("orig-fasttrack-bytes")))
                    if row.get("repl-bytes") not in (None, ""):
                        sample.repl_bytes += max(0, self._int(row.get("repl-bytes")))
                    else:
                        sample.repl_bytes += max(0, self._int(row.get("repl-fasttrack-bytes")))

        return result
