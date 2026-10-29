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

    for operator, country in PREVIEW_OPERATORS:
        url = B2OService.CAMERAS_URL.format(operator=operator)
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
            if len(items) < 50:
                break
    return None


def load_b2o_preview_for_serial(serial: str, token: str, b2o: B2OService) -> tuple[str, str, bytes] | None:
    """Resolve an exact camera ID then load a restricted still image."""
    match = b2o_camera_for_serial(serial, token, b2o)
    if match is None:
        return None
    country, camera_id = match
    raw = fetch_camera_preview(country, camera_id, token)
    return country, camera_id, raw
