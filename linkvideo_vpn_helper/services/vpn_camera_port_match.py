from __future__ import annotations

"""Read-only VPN-port -> B2O camera verification for the desktop client card.

The B2O search can be fuzzy. A camera is shown beside a VPN port only when
(1) account ownership is verified by existing exact-login search,
(2) its B2O detail or the documented camera-streams-urls endpoint has
    an EXPLICIT matching RTSP port,
(3) the port is not claimed by multiple B2O cameras.
No inferred 554, invented matches, credential output or B2O writes.
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
)
_NESTED = ("rtsp", "connection", "camera", "device", "network",
           "connection_settings", "stream", "main_stream")
_MAX_CAMERA_DETAILS = 40


@dataclass(frozen=True)
class CameraPortLink:
    port: int
    camera_id: str
    label: str
    country: str


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


def account_login(vpn_login: str) -> str:
    """Normalize vpn-login_1, vpn-login_3, etc. to the account login.

    Underscores not followed by a positive numeric suffix are retained.
    An operator can supply a different B2O account explicitly in the UI.
    """
    login = str(vpn_login or "").strip()
    return _SUFFIX.sub("", login)


def _port_number(value) -> int | None:
    if isinstance(value, bool):
        return None
    s = str(value or "").strip()
    if not s.isascii() or not s.isdecimal():
        return None
    number = int(s)
    return number if 1 <= number <= 65535 else None


def explicit_camera_ports(row: dict) -> set[int]:
    """Only genuine B2O RTSP endpoint ports or explicit port keys.

    A URL without explicit port has *unknown external port*, not 554.
    """
    if not isinstance(row, dict):
        return set()
    result: set[int] = set()
    objects = [row] + [row[key] for key in _NESTED if isinstance(row.get(key), dict)]
    for record in objects:
        for key in _PORT_FIELDS:
            port = _port_number(record.get(key))
            if port is not None:
                result.add(port)
        for key in _URL_FIELDS:
            url = record.get(key)
            if not isinstance(url, str) or not url.lower().startswith(("rtsp://", "rtsps://")):
                continue
            try:
                parsed = urlsplit(url)
                port = parsed.port
                if parsed.hostname and port is not None and 1 <= port <= 65535:
                    result.add(port)
            except ValueError:
                # An invalid or password-obscured RTSP URL proves no port.
                continue
    return result


def _single_camera_record(payload) -> dict:
    """Unwrap the verified B2O camera/stream response, without guessing."""
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data", payload)
    if isinstance(data, list):
        return data[0] if len(data) == 1 and isinstance(data[0], dict) else {}
    return data if isinstance(data, dict) else {}


def _detail_for_camera(
    b2o: B2OService, choice, login: str
) -> tuple[object, set[int], bool, bool]:
    """Read BOTH camera metadata and its actual B2O RTSP URLs.

    The camera detail endpoint commonly omits the external RTSP port.
    An existing desktop feature already uses /camera-streams-urls.
    Never infer port 554 for URLs with no explicit port, or expose RTSP
    URLs (which may contain embedded credentials) to callers/logs.
    """
    number, _ = b2o.normalize_camera_id(choice.camera_id, choice.operator_id)
    token = b2o.token()
    payload = b2o.request_json(
        b2o.CAMERA_URL.format(operator=choice.operator_id, camera=number),
        token, timeout=12,
    )
    row = _single_camera_record(payload)
    if not row:
        raise ValueError("B2O camera details unavailable")
    # The inventory search already validated exact customer ownership.
    # Reject a conflicting owner found in an individual camera detail.
    owners = [str(row[key]).strip() for key in _OWNER_FIELDS
              if isinstance(row.get(key), str) and row[key].strip()]
    if owners and any(owner.casefold() != login.casefold() for owner in owners):
        return choice, set(), False, False
    ports = explicit_camera_ports(row)
    url = (
        "https://api.b2o.goodline.info/ords/mobile/vc2/operators/"
        f"{int(choice.operator_id)}/cameras/{number}/camera-streams-urls"
    )
    try:
        streams = _single_camera_record(b2o.request_json(url, token, timeout=10))
    except Exception:
        # A partial response must not be interpreted as a confirmed
        # absence of any competing camera/port match.
        return choice, ports, False, True
    rtsp_ports = explicit_camera_ports(streams)
    return choice, ports | rtsp_ports, bool(rtsp_ports), False


def match_vpn_ports(
    vpn_login: str, ports, b2o: B2OService, credentials, *,
    override_login: str = "", country: str = "", lookup=search_by_login, detail_reader=_detail_for_camera,
    max_details: int = _MAX_CAMERA_DETAILS,
) -> CameraPortReport:
    """Never guess B2O port ownership from camera name or VPN login alone."""
    login = (override_login or account_login(vpn_login)).strip()
    if not re.fullmatch(r"[A-Za-z0-9_.+@-]{3,100}", login):
        raise ValueError("Укажите корректный логин B2O")
    requested = {int(port) for port in ports if _port_number(port) is not None}
    if not requested:
        return CameraPortReport(login, (), (), (), 0)
    if not b2o.token():
        raise PermissionError("Войдите в Админку, чтобы сверить порты с B2O.")
    if credentials is None or not getattr(credentials, "username", "") or not getattr(credentials, "password", ""):
        raise PermissionError("Войдите в учётную запись «Админка».")
    choices = [choice for choice in lookup(login, b2o, credentials)
               if str(choice.customer_login or "").casefold() == login.casefold()
               and (not country or str(choice.country).casefold() == country.casefold())]
    # The account must match EXACTLY. A fuzzy search or camera from another
    # account never authorizes a match, even if its RTSP port happens to match.
    candidates = list({(choice.operator_id, choice.camera_id): choice for choice in choices}.values())
    candidates = candidates[:max_details]
    incomplete = len(choices) > max_details
    owned: dict[int, list[CameraPortLink]] = {}
    failed = False
    stream_cameras = 0
    stream_errors = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(detail_reader, b2o, choice, login) for choice in candidates]
        for future in as_completed(futures):
            try:
                observation = future.result()
                choice, found = observation[:2]
            except Exception:
                failed = True
                stream_errors += 1
                continue
            # Legacy injectable readers can return two fields; real B2O
            # reader also reports stream URL presence and request errors.
            if len(observation) >= 3 and observation[2]:
                stream_cameras += 1
            if len(observation) >= 4 and observation[3]:
                failed = True
                stream_errors += 1
            for port in requested.intersection(found):
                owned.setdefault(port, []).append(CameraPortLink(
                    port, str(choice.camera_id), str(choice.label)[:140], str(choice.country)))
    links = tuple(entries[0] for port, entries in sorted(owned.items())
                  if len(entries) == 1 and not (failed or incomplete))
    ambiguous = tuple(sorted(port for port, entries in owned.items() if len(entries) > 1))
    unmatched = tuple(sorted(requested - {row.port for row in links} - set(ambiguous)))
    return CameraPortReport(login, links, ambiguous, unmatched, len(candidates),
                            failed or incomplete, stream_cameras, stream_errors)
