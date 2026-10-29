from __future__ import annotations

"""Strict B2O serial-to-camera resolution for New Agent thumbnail previews.

Only exact serial matches may be used for the existing preview_path endpoint.
The operator account's token is reused from the already authorized archive
session; there are no new credentials or untrusted destination URLs.
"""

import re
from typing import Any

from linkvideo_vpn_helper.services.archive_service_core import B2OService
from linkvideo_vpn_helper.services.b2o_camera_preview import fetch_camera_preview

# RU first as requested; the same preview method is supported for BY and KZ.
PREVIEW_OPERATORS = ((241, "Россия"), (1741, "Беларусь"), (1721, "Казахстан"))
SERIAL_PATTERN = re.compile(r"[a-zA-Z0-9_.-]{3,96}\Z")
CAMERA_ID_PATTERN = re.compile(r"[1-9][0-9]{0,17}\Z")
SERIAL_FIELDS = ("serial_number", "serialNumber", "SerialNumber",
                 "camera_serial", "camera_serial_number", "serial", "sn")


def _has_serial_field(row: dict[str, Any]) -> bool:
    if not isinstance(row, dict):
        return False
    records = [row]
    for field in ("device", "camera", "agent"):
        if isinstance(row.get(field), dict):
            records.append(row[field])
    return any(str(record.get(field) or "").strip()
               for record in records for field in SERIAL_FIELDS)


def _matching_id(row: dict[str, Any], serial: str) -> str:
    if not isinstance(row, dict):
        return ""
    candidates = [row]
    for field in ("device", "camera", "agent"):
        if isinstance(row.get(field), dict):
            candidates.append(row[field])
    for record in candidates:
        if any(
            str(record.get(field) or "").strip().casefold() == serial.casefold()
            for field in SERIAL_FIELDS
        ):
            camera_id = str(row.get("id_camera") or "").strip()
            return camera_id if CAMERA_ID_PATTERN.fullmatch(camera_id) else ""
    return ""


def b2o_camera_for_serial(serial: str, token: str, b2o: B2OService) -> tuple[str, str] | None:
    """Read authorized operator camera inventories; never use a fuzzy result."""
    serial = str(serial or "").strip()
    if not SERIAL_PATTERN.fullmatch(serial):
        raise ValueError("Некорректный серийный номер камеры.")
    if not token or "\r" in token or "\n" in token:
        raise PermissionError("Войдите в B2O в разделе «Скачать архив».")

    failed_operators = 0
    for operator, country in PREVIEW_OPERATORS:
        url = B2OService.CAMERAS_URL.format(operator=operator)
        try:
            detail_checks = 0
            for page in range(1, 5):
                payload = b2o.request_json(
                    url, token,
                    {"Count": "50", "Page": str(page), "Search": serial,
                     "Show_deleted_cameras": "0"},
                    12,
                )
                if not isinstance(payload, dict):
                    raise ValueError("B2O вернул некорректный список камер.")
                items = payload.get("data")
                if not isinstance(items, list):
                    raise ValueError("B2O не вернул список камер.")
                for row in items:
                    camera_id = _matching_id(row, serial)
                    if camera_id:
                        return country, camera_id
                    if not isinstance(row, dict) or _has_serial_field(row):
                        continue
                    # Search may omit the serial number in its brief rows.
                    # Read at most three exact camera details and accept only
                    # a verified serial; never assume a fuzzy search hit.
                    candidate = str(row.get("id_camera") or "").strip()
                    if not CAMERA_ID_PATTERN.fullmatch(candidate) or detail_checks >= 3:
                        continue
                    detail_checks += 1
                    try:
                        detail = b2o.request_json(
                            B2OService.CAMERA_URL.format(
                                operator=operator, camera=candidate
                            ), token, {}, 12,
                        )
                        if not isinstance(detail, dict):
                            continue
                        data = detail.get("data", detail)
                        details = data if isinstance(data, list) else [data]
                        for record in details:
                            if isinstance(record, dict) and _matching_id(
                                {**record, "id_camera": candidate}, serial
                            ):
                                return country, candidate
                    except Exception:
                        continue
                if len(items) < 50:
                    break
        except Exception:
            # A token might have access to RU but not to the other operators.
            # Failure of one operator must not hide valid cameras in another.
            failed_operators += 1
            continue
    if failed_operators == len(PREVIEW_OPERATORS):
        raise ConnectionError("B2O не предоставил список камер ни одного оператора.")
    return None


def load_b2o_preview_for_serial(serial: str, token: str, b2o: B2OService) -> tuple[str, str, bytes] | None:
    """Resolve an exact camera ID then load a restricted still image."""
    match = b2o_camera_for_serial(serial, token, b2o)
    if match is None:
        return None
    country, camera_id = match
    raw = fetch_camera_preview(country, camera_id, token)
    return country, camera_id, raw
