from __future__ import annotations

"""Read-only, fail-closed VPN server + port -> B2O camera matching.

A DVR/NVR can expose many camera channels on ONE external RTSP port.
Never collapse this into an "ambiguous" match. Display each verified camera
when the account is exact, the RTSP URL's host is the client's VPN server,
and the URL explicitly contains the same external TCP port. A bare port,
an inferred default 554, or a different VPN host is not proof.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import re
from urllib.parse import urlsplit

from linkvideo_vpn_helper.services.archive_service_core import B2OService
from linkvideo_vpn_helper.services.camera_lookup import search_by_login

_SUFFIX = re.compile(r"(?:_[1-9][0-9]*)+$")
_OWNER_FIELDS = ("account_login", "customer_login", "client_login", "owner_login")
_PORT_FIELDS = (
    "external_rtsp_port", "rtsp_external_port", "external_port",
    "port_forward", "rtsp_port", "rtspPort", "port_rtsp",
    "camera_port", "cameraPort",
)
_URL_FIELDS = (
    "rtsp_url", "rtspUrl", "url_rtsp", "rtsp_uri", "rtspUri",
    "rtsp_link", "rtspLink", "camera_rtsp_url", "stream_rtsp_url",
    "main_stream_rtsp_url", "stream_url", "streamUrl",
    "camera_stream_main", "camera_stream_sub",
    "main", "sub", "url", "uri",
)
_NESTED = ("data", "items", "streams", "urls", "rtsp", "connection",
           "camera", "device", "network", "connection_settings",
           "stream", "main_stream", "channels", "results")
_MAX_CAMERA_DETAILS = 80


@dataclass(frozen=True)
class CameraPortLink:
    port: int
    camera_id: str
    label: str
    country: str
    server: str = ""


@dataclass(frozen=True)
class CameraPortReport:
    login: str
    links: tuple[CameraPortLink, ...]
    ambiguous: tuple[int, ...]
    unmatched: tuple[int, ...]
    checked_cameras: int
    incomplete: bool = False
    stream_cameras: int = 0
    stream_errors: int = 0
    # Multiple channels/cameras sharing a verified (server, port) pair.
    grouped: tuple[tuple[int, tuple[CameraPortLink, ...]], ...] = ()

    @property
    def matched_camera_count(self) -> int:
        return len(self.links) + sum(len(items) for _, items in self.grouped)


def account_login(vpn_login: str) -> str:
    return _SUFFIX.sub("", str(vpn_login or "").strip())


def _port_number(value) -> int | None:
    if isinstance(value, bool):
        return None
    s = str(value or "").strip()
    if not s.isascii() or not s.isdecimal():
        return None
    port = int(s)
    return port if 1 <= port <= 65535 else None


def canonical_server(host) -> str:
    """Normalize only the hostname, never strip a significant DNS label."""
    value = str(host or "").strip().lower().rstrip(".")
    if not value:
        return ""
    if "://" in value:
        try:
            value = urlsplit(value).hostname or ""
        except ValueError:
            return ""
    elif ":" in value or "/" in value:
        try:
            value = urlsplit("//" + value).hostname or ""
        except ValueError:
            return ""
    if not value or any(char.isspace() for char in value):
        return ""
    return value.lower().rstrip(".")


def _endpoint(value: str) -> tuple[str, int] | None:
    """Return verified (hostname, explicit TCP port) from an RTSP URL only."""
    if not isinstance(value, str) or not value.lower().startswith(("rtsp://", "rtsps://")):
        return None
    try:
        parsed = urlsplit(value)
        port = parsed.port
        host = canonical_server(parsed.hostname)
        if host and port is not None and 1 <= port <= 65535:
            return host, port
    except ValueError:
        pass
    return None


def explicit_camera_endpoints(payload) -> set[tuple[str, int]]:
    """Read nested stream URL collections including channels, without URLs in output.

    An external_port field on its own never proves WHICH VPN server it
    belongs to. It may still be inspected by explicit_camera_ports for
    diagnostics, but cannot authorize an ID in the client card.
    """
    found: set[tuple[str, int]] = set()
    stack = [(payload, 0)]
    seen = 0
    while stack and seen < 300:
        obj, depth = stack.pop()
        seen += 1
        if depth > 8:
            continue
        if isinstance(obj, list):
            stack.extend((item, depth + 1) for item in obj[:100])
        elif isinstance(obj, dict):
            for key, value in obj.items():
                if isinstance(value, (list, dict)):
                    # B2O variants return nested stream maps and channel lists.
                    stack.append((value, depth + 1))
                elif isinstance(value, str) and (
                    key in _URL_FIELDS or "rtsp" in key.casefold()
                    or key.casefold().endswith(("_url", "_uri"))
                ):
                    endpoint = _endpoint(value)
                    if endpoint:
                        found.add(endpoint)
    return found


def explicit_camera_ports(row: dict) -> set[int]:
    """Legacy diagnostic helper (NOT sufficient for assigning an ID)."""
    if not isinstance(row, dict):
        return set()
    found = {port for _server, port in explicit_camera_endpoints(row)}
    records = [row] + [row[key] for key in _NESTED if isinstance(row.get(key), dict)]
    for record in records:
        for key in _PORT_FIELDS:
            port = _port_number(record.get(key))
            if port:
                found.add(port)
    return found


def _single_camera_record(payload) -> dict:
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data", payload)
    if isinstance(data, list):
        return data[0] if len(data) == 1 and isinstance(data[0], dict) else {}
    return data if isinstance(data, dict) else {}


def _detail_for_camera(
    b2o: B2OService, choice, login: str
) -> tuple[object, set[tuple[str, int]], bool, bool]:
    """Read camera detail and actual stream URLs; redact URL credentials."""
    number, _ = b2o.normalize_camera_id(choice.camera_id, choice.operator_id)
    token = b2o.token()
    payload = b2o.request_json(
        b2o.CAMERA_URL.format(operator=choice.operator_id, camera=number),
        token, timeout=12,
    )
    row = _single_camera_record(payload)
    if not row:
        raise ValueError("B2O camera details unavailable")
    owners = [str(row[key]).strip() for key in _OWNER_FIELDS
              if isinstance(row.get(key), str) and row[key].strip()]
    if owners and any(owner.casefold() != login.casefold() for owner in owners):
        return choice, set(), False, False
    endpoints = explicit_camera_endpoints(row)
    url = (
        "https://api.b2o.goodline.info/ords/mobile/vc2/operators/"
        f"{int(choice.operator_id)}/cameras/{number}/camera-streams-urls"
    )
    try:
        response = b2o.request_json(url, token, timeout=10)
    except Exception:
        return choice, endpoints, False, True
    stream_endpoints = explicit_camera_endpoints(response)
    return choice, endpoints | stream_endpoints, bool(stream_endpoints), False


def match_vpn_ports(
    vpn_login: str, ports, b2o: B2OService, credentials, *,
    vpn_server: str = "", override_login: str = "", country: str = "",
    lookup=search_by_login, detail_reader=_detail_for_camera,
    max_details: int = _MAX_CAMERA_DETAILS,
) -> CameraPortReport:
    """Only expose IDs with exact account + VPN SERVER + external port match.

    Explicitly require server, so old callers that passed only an external
    port fail closed instead of displaying cross-server IDs.
    """
    login = (override_login or account_login(vpn_login)).strip()
    if not re.fullmatch(r"[A-Za-z0-9_.+@-]{3,100}", login):
        raise ValueError("Укажите корректный логин B2O")
    requested = {int(p) for p in ports if _port_number(p) is not None}
    if not requested:
        return CameraPortReport(login, (), (), (), 0)
    server = canonical_server(vpn_server)
    if not server:
        return CameraPortReport(login, (), (), tuple(sorted(requested)), 0,
                                incomplete=True)
    if not b2o.token():
        raise PermissionError("Войдите в Админку для проверки камер.")
    if credentials is None or not getattr(credentials, "username", "") or not getattr(credentials, "password", ""):
        raise PermissionError("Войдите в учётную запись «Админка».")
    choices = [choice for choice in lookup(login, b2o, credentials)
               if str(choice.customer_login or "").casefold() == login.casefold()
               and (not country or str(choice.country).casefold() == country.casefold())]
    # A fuzzy search or a camera of another account is never ownership proof.
    candidates = list({(choice.operator_id, choice.camera_id): choice for choice in choices}.values())
    incomplete = len(candidates) > max_details
    candidates = candidates[:max_details]
    owned: dict[int, dict[tuple[int, str], CameraPortLink]] = {}
    failed = False
    stream_cameras = 0
    stream_errors = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(detail_reader, b2o, choice, login) for choice in candidates]
        for future in as_completed(futures):
            try:
                observation = future.result()
                choice, endpoints = observation[:2]
            except Exception:
                failed = True
                stream_errors += 1
                continue
            if len(observation) >= 3 and observation[2]:
                stream_cameras += 1
            if len(observation) >= 4 and observation[3]:
                failed = True
                stream_errors += 1
            # Legacy numeric-only detail readers never authorize server match.
            valid = {
                port for entry in endpoints
                if isinstance(entry, tuple) and len(entry) == 2
                for host, port in (entry,)
                if canonical_server(host) == server and port in requested
            }
            for port in valid:
                link = CameraPortLink(
                    port, str(choice.camera_id), str(choice.label)[:140],
                    str(choice.country), server)
                owned.setdefault(port, {})[(int(choice.operator_id), link.camera_id)] = link

    links: list[CameraPortLink] = []
    grouped: list[tuple[int, tuple[CameraPortLink, ...]]] = []
    if not (failed or incomplete):
        for port, mapping in sorted(owned.items()):
            cameras = tuple(sorted(mapping.values(), key=lambda x: (x.label, x.camera_id)))
            if len(cameras) == 1:
                links.append(cameras[0])
            elif len(cameras) > 1:
                grouped.append((port, cameras))
    matched_ports = {link.port for link in links} | {port for port, _ in grouped}
    unmatched = tuple(sorted(requested - matched_ports))
    return CameraPortReport(
        login, tuple(links), (), unmatched, len(candidates),
        failed or incomplete, stream_cameras, stream_errors, tuple(grouped))
