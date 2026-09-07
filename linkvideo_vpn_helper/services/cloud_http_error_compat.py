from __future__ import annotations

from typing import Any

from linkvideo_vpn_helper.services.cloud_vpnsync import CloudConnectionError, CloudVPNSyncClient


_INSTALLED = False


def _detail_text(response) -> str:
    try:
        payload: Any = response.json()
    except Exception:
        return ""
    if not isinstance(payload, dict):
        return str(payload or "").strip()
    detail = payload.get("detail")
    if isinstance(detail, str):
        return detail.strip()
    if not isinstance(detail, dict):
        return ""

    message = str(detail.get("error") or detail.get("message") or "").strip()
    conflicts = list(detail.get("conflicts") or [])
    conflict_lines: list[str] = []
    labels = {
        "login": "PPP-логин уже существует",
        "profile": "PPP-профиль уже существует",
        "remote-address-secret": "Remote Address уже используется PPP-учёткой",
        "remote-address-profile": "Remote Address уже используется PPP-профилем",
        "nat-port": "внешний NAT-порт уже занят",
    }
    for item in conflicts[:12]:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "")
        value = item.get("value", "")
        owner = str(item.get("owner") or item.get("comment") or "").strip()
        text = labels.get(kind, kind or "конфликт RouterOS")
        if value not in (None, ""):
            text += f": {value}"
        if owner:
            text += f" · {owner}"
        conflict_lines.append("• " + text)
    if len(conflicts) > 12:
        conflict_lines.append(f"• ещё конфликтов: {len(conflicts) - 12}")
    if conflict_lines:
        prefix = message or "Восстановление заблокировано живым MikroTik"
        return prefix + "\n" + "\n".join(conflict_lines)
    return message


def install_cloud_http_error_details() -> None:
    """Preserve VPNSync JSON error details instead of hiding them behind HTTPError."""
    global _INSTALLED
    if _INSTALLED:
        return

    def request(self: CloudVPNSyncClient, method: str, path: str, **kwargs) -> Any:
        self._ensure_token()
        headers = dict(kwargs.pop("headers", {}) or {})
        headers["Authorization"] = f"Bearer {self._token}"
        timeout = kwargs.pop("timeout", self.timeout)
        try:
            response = self._session.request(
                str(method).upper(),
                self._url(path),
                headers=headers,
                timeout=timeout,
                **kwargs,
            )
            if response.status_code == 401:
                self._token = ""
                self._ensure_token()
                headers["Authorization"] = f"Bearer {self._token}"
                response = self._session.request(
                    str(method).upper(),
                    self._url(path),
                    headers=headers,
                    timeout=timeout,
                    **kwargs,
                )

            if response.status_code >= 400:
                detail = _detail_text(response)
                if detail:
                    raise CloudConnectionError(detail)
                response.raise_for_status()

            if response.status_code == 204 or not response.content:
                return None
            return response.json()
        except CloudConnectionError:
            raise
        except Exception as exc:
            raise CloudConnectionError(f"Ошибка запроса к облачному серверу: {exc}") from exc

    CloudVPNSyncClient.request = request
    _INSTALLED = True
