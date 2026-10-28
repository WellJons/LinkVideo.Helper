from __future__ import annotations

"""B2O camera thumbnail using the observed operator preview_path endpoint.

The billing camera's id_camera_vc is the B2O camera ID, NOT its billing ID.
No arbitrary remote URL is fetched: only approved B2O media hosts are allowed.
All requests are GET, size-limited, redirect-free, and made off the UI thread.
"""

import json
import re
import urllib.error
import urllib.request
from urllib.parse import urlsplit

B2O_API = "https://api.b2o.goodline.info/ords/mobile/vc2"
OPERATOR_BY_COUNTRY = {"Россия": 241, "Казахстан": 1721, "Беларусь": 1741}
MAX_IMAGE_BYTES = 2 * 1024 * 1024


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Перенаправление превью запрещено.")


def preview_request_url(country: str, vc_id: str) -> str:
    if country not in OPERATOR_BY_COUNTRY:
        raise ValueError("Страна камеры не поддерживается B2O.")
    if not isinstance(vc_id, str) or not re.fullmatch(r"[1-9][0-9]{0,17}", vc_id):
        raise ValueError("У камеры отсутствует корректный id_camera_vc.")
    return (
        f"{B2O_API}/operators/{OPERATOR_BY_COUNTRY[country]}"
        f"/cameras/{vc_id}/preview_path"
    )


def _safe_preview_url(value: str) -> str:
    if not isinstance(value, str) or len(value) > 4096:
        raise ValueError("Некорректный адрес превью.")
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower()
    if (parsed.scheme != "https" or
        not (host.endswith(".video.goodline.info") and len(host) > len(".video.goodline.info"))
        or parsed.port not in (443, 444, None)
        or parsed.username or parsed.password or parsed.fragment):
        raise ValueError("B2O вернул неподдерживаемый адрес превью.")
    return value


def fetch_camera_preview(country: str, vc_id: str, token: str) -> bytes:
    """Return a small authenticated image or raise a sanitized error.

    The preview endpoint may instead return an HLS/stream URL: that is NOT a
    JPEG and must not be interpreted or silently played.
    """
    if not isinstance(token, str) or not token or any(x in token for x in "\r\n"):
        raise ValueError("Войдите в B2O для получения превью камеры.")
    api_url = preview_request_url(country, vc_id)
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(
        api_url, method="GET",
        headers={"Token": token, "Accept": "application/json",
                 "User-Agent": "LinkVideo.Helper/3.0"},
    )
    try:
        with opener.open(req, timeout=8) as response:
            content = response.read(65536)
        data = json.loads(content.decode("utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("preview_path"), str):
            raise ValueError("B2O не вернул путь к превью.")
        image_url = _safe_preview_url(data["preview_path"])
        with opener.open(urllib.request.Request(
            image_url, method="GET",
            headers={"User-Agent": "LinkVideo.Helper/3.0",
                     "Accept": "image/jpeg,image/png,image/webp"},
        ), timeout=8) as response:
            kind = (response.headers.get("Content-Type") or "").split(";", 1)[0].lower()
            if kind not in ("image/jpeg", "image/png", "image/webp"):
                raise ValueError("B2O вернул поток вместо изображения превью.")
            if int(response.headers.get("Content-Length") or 0) > MAX_IMAGE_BYTES:
                raise ValueError("Изображение превью слишком большое.")
            raw = response.read(MAX_IMAGE_BYTES + 1)
            if len(raw) > MAX_IMAGE_BYTES:
                raise ValueError("Изображение превью слишком большое.")
            return raw
    except (urllib.error.HTTPError, urllib.error.URLError):
        raise ValueError("Не удалось загрузить превью камеры через B2O.") from None
