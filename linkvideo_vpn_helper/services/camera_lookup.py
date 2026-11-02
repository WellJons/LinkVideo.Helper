from __future__ import annotations

"""Camera search by exact customer login, camera ID or device serial.

CRM uses its existing, verified GET contract for Belarus and Kazakhstan.
Russian B2O inventory is accepted only with an exact account-login match:
a fuzzy Search response must never be treated as proof of ownership.
"""

from dataclasses import dataclass
import re
from typing import Callable

from linkvideo_vpn_helper.services.archive_service_core import B2OService
from linkvideo_vpn_helper.services.billing_auth_contract import BillingLoginRequest
from linkvideo_vpn_helper.services.billing_login_probe import check_billing_login
from linkvideo_vpn_helper.services.billing_token_transport import BillingTokenTransport
from linkvideo_vpn_helper.services.billing_editable_gateway import BillingEditableGateway


@dataclass(frozen=True)
class CameraChoice:
    camera_id: str
    operator_id: int
    country: str
    label: str
    customer_login: str = ""
    status: str = ""
    tariff: str = ""
    crm_id: str = ""


class CameraSearchError(ValueError):
    pass


_ID = re.compile(r"(?:linkvideo(?:kz|by)?_)?[0-9]{1,18}\Z", re.I)
_LOGIN = re.compile(r"[A-Za-z0-9_.+@-]{3,100}\Z")
_OWNERS = ("account_login", "customer_login", "client_login", "login", "owner_login")


def search_by_login(login: str, b2o: B2OService, credentials, *,
                    crm_factory: Callable | None = None) -> tuple[CameraChoice, ...]:
    query = str(login or "").strip()
    if not _LOGIN.fullmatch(query):
        raise CameraSearchError("Укажите корректный логин клиента.")
    if credentials is None or not credentials.username or not credentials.password:
        raise CameraSearchError("Нужен вход в учётную запись «Админка».")

    results: list[CameraChoice] = []
    crm_error = ""
    try:
        if crm_factory is not None:
            gateway = crm_factory(credentials)
        else:
            response = check_billing_login(
                BillingLoginRequest(credentials.username, credentials.password)
            )
            transport = BillingTokenTransport(response.token, header_mode="auto")
            gateway = BillingEditableGateway(transport)

        hits = gateway.search_clients(query)
        for hit in hits:
            client = gateway.find_client_by_hit(hit)
            operator = {"Беларусь": 1741, "Казахстан": 1721}.get(client.country)
            if operator is None:
                continue
            for camera in client.services:
                # CRM service ID is not the B2O camera ID.
                vc = str(camera.camera_vc_id or "").strip()
                if not vc.isascii() or not vc.isdecimal() or int(vc) <= 0:
                    continue
                results.append(CameraChoice(
                    camera_id=vc, operator_id=operator, country=client.country,
                    label=camera.camera_name or
                          f"{b2o.operator_prefix(operator)}{vc}",
                    customer_login=client.login, status=camera.status,
                    tariff=camera.tariff_name, crm_id=camera.camera_id,
                ))
    except Exception:
        # CRM can be unavailable even when the B2O login is valid.
        crm_error = "Нет доступа к CRM или поиск клиентов недоступен."

    # RU cameras do not use the BY/KZ CRM contract. Query the B2O inventory,
    # but never accept results where owner login is missing/ambiguous.
    b2o_error = ""
    token = b2o.token()
    if token:
        try:
            for page in range(1, 5):
                payload = b2o.request_json(
                    b2o.CAMERAS_URL.format(operator=241), token,
                    {"Count": "50", "Page": str(page),
                     "Search": query, "Show_deleted_cameras": "0"},
                    timeout=12,
                )
                rows = payload.get("data") if isinstance(payload, dict) else None
                if not isinstance(rows, list):
                    raise CameraSearchError("B2O не вернул список камер.")
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    owner = next(
                        (str(row[key]).strip() for key in _OWNERS
                         if isinstance(row.get(key), str) and row[key].strip()), ""
                    )
                    if owner.casefold() != query.casefold():
                        continue
                    vc = str(row.get("id_camera") or "").strip()
                    if not vc.isascii() or not vc.isdecimal() or int(vc) <= 0:
                        continue
                    results.append(CameraChoice(
                        camera_id=vc, operator_id=241, country="Россия",
                        label=str(row.get("camera_name") or row.get("short_name")
                                  or "linkvideo_" + vc)[:160],
                        customer_login=owner,
                        status=str(row.get("camera_status") or "")[:80],
                    ))
                if len(rows) < 50:
                    break
        except Exception:
            b2o_error = "B2O не предоставил список российских камер."
    else:
        b2o_error = "Для российских камер требуется подключение админки."

    unique = {}
    for camera in results:
        unique[(camera.operator_id, camera.camera_id)] = camera
    if unique:
        return tuple(unique.values())
    if crm_error and b2o_error:
        raise CameraSearchError(crm_error + " " + b2o_error)
    return ()


def looks_like_camera_identifier(text: str) -> bool:
    return bool(_ID.fullmatch(str(text or "").strip()))


def direct_choice(text: str, country_id: int) -> CameraChoice:
    query = str(text or "").strip()
    if not query or len(query) > 96 or not re.fullmatch(r"[A-Za-z0-9_.-]+", query):
        raise CameraSearchError("Некорректный идентификатор камеры.")
    operator = B2OService.resolve_operator_id(query, country_id)
    return CameraChoice(
        camera_id=query, operator_id=operator,
        country=B2OService.operator_country(operator), label=query,
    )



def search_all_cameras(
    query: str, b2o: B2OService, credentials, *,
    login_search=search_by_login, detail_reader=None, serial_resolver=None,
) -> tuple[CameraChoice, ...]:
    """Search exact camera IDs, exact serials and customer login automatically.

    Never report a merely syntactically valid ID as a found camera. All
    candidate IDs must be verified against the protected B2O detail API.
    Results are deduplicated by operator+camera number; a CRM-derived name,
    customer and tariff take precedence over a bare B2O ID.
    """
    from linkvideo_vpn_helper.services.camera_diagnostics import inspect_camera
    from linkvideo_vpn_helper.services.agent_b2o_preview import b2o_camera_for_serial

    query = str(query or "").strip()
    if not query or len(query) > 100 or not _LOGIN.fullmatch(query):
        raise CameraSearchError("Введите логин клиента, ID или серийный номер.")
    detail_reader = detail_reader or inspect_camera
    serial_resolver = serial_resolver or b2o_camera_for_serial
    token = b2o.token()
    if not token:
        raise CameraSearchError("Войдите в учётную запись «Админка».")

    results: dict[tuple[int, str], CameraChoice] = {}
    problems: list[str] = []
    explicit_id = looks_like_camera_identifier(query)
    prefix = query.lower().startswith(("linkvideo_", "linkvideoby_", "linkvideokz_"))

    def remember(choice: CameraChoice):
        numeric, _ = b2o.normalize_camera_id(choice.camera_id, choice.operator_id)
        key = (choice.operator_id, numeric)
        if key not in results or (choice.customer_login and
                                  not results[key].customer_login):
            results[key] = choice

    if explicit_id:
        operators = (
            (b2o.resolve_operator_id(query, 241),)
            if prefix else (241, 1741, 1721)
        )
        for operator in operators:
            try:
                report = detail_reader(b2o, query, operator, with_network=False)
                # Refuse empty error objects masquerading as a camera record.
                row = getattr(report, "metadata", {}) or {}
                valid = any(str(row.get(k) or "").strip() for k in (
                    "id_camera", "camera_name", "name", "serial_number",
                    "serial", "main_stream_name", "server_name",
                ))
                if not valid:
                    continue
                number, label = b2o.normalize_camera_id(report.camera_id, operator)
                # For an ID match, the returned number must match exactly.
                expected, _ = b2o.normalize_camera_id(query, operator)
                if number != expected:
                    continue
                remember(CameraChoice(
                    camera_id=number, operator_id=operator,
                    country=b2o.operator_country(operator),
                    label=report.name or label,
                    status=report.status,
                ))
            except Exception:
                continue

    # Numeric strings can be device serial numbers, not camera IDs.
    # The existing resolver accepts only exact serial matches, including
    # detail checks on rows returned by the fuzzy inventory search.
    if not prefix:
        try:
            found = serial_resolver(query, token, b2o)
            if found:
                country, number = found
                operator = {"Россия": 241, "Беларусь": 1741,
                            "Казахстан": 1721}.get(country)
                if operator:
                    report = detail_reader(b2o, number, operator, with_network=False)
                    remember(CameraChoice(
                        camera_id=number, operator_id=operator, country=country,
                        label=report.name or report.camera_id,
                        status=report.status,
                    ))
        except Exception:
            # A missing/unauthorized serial endpoint does not invalidate
            # positive results from other exact search modes, but it must be
            # reported when no other method produced a verified result.
            problems.append("Проверка серийного номера недоступна.")

    if not prefix:
        try:
            for choice in login_search(query, b2o, credentials):
                remember(choice)
        except CameraSearchError as exc:
            problems.append(str(exc))
        except Exception:
            problems.append("Поиск клиентов недоступен.")

    if results:
        return tuple(results.values())
    if problems:
        raise CameraSearchError(problems[0])
    return ()
