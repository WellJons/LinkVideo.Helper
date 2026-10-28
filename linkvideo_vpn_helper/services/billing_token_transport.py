from __future__ import annotations

"""Fail-closed CRM GET transport waiting for a verified request-header format.

Login returns {"token": "..."}, but the protected GET request header has
not yet been observed. No default header format is guessed or tried.
Instantiate only after IT confirms the browser's actual request header.
"""

import re
from dataclasses import dataclass, field
from typing import Literal

import requests

from linkvideo_vpn_helper.services.billing_auth_contract import BILLING_API_ORIGIN
from linkvideo_vpn_helper.services.billing_gateway import (
    BillingNotConfigured, BillingPermissionDenied, BillingValidationError,
)


HeaderMode = Literal["token", "authorization_bearer", "authorization_raw"]
_ALLOWED_ROUTES = (
    re.compile(r"^/api/accounts/search/[A-Za-z0-9_.%+@-]{3,300}$"),
    re.compile(r"^/api/accounts/[0-9]+(?:/cameras)?$"),
    re.compile(r"^/api/tariffs/list/[01]$"),
    re.compile(r"^/api/cameras/archive-depth/1$"),
    re.compile(r"^/api/countries/1$"),
)


class BillingTransportError(RuntimeError):
    pass


@dataclass(repr=False)
class BillingTokenTransport:
    """Never logs/stores the token or automatically retries login.

    A request method restricted to observed safe read-only GET endpoints.
    The exact header style must be selected from actual DevTools evidence.
    """

    token: str = field(repr=False)
    header_mode: HeaderMode | None = None
    timeout: float = 8.0
    _session: requests.Session | None = field(default=None, repr=False)

    def __post_init__(self):
        if not isinstance(self.token, str) or not self.token.strip():
            raise BillingValidationError("В CRM нет действующего токена входа.")
        if any(char in self.token for char in ("\r", "\n")):
            raise BillingValidationError("Некорректный токен CRM.")
        if self.header_mode not in (None, "token", "authorization_bearer", "authorization_raw"):
            raise BillingValidationError("Неизвестный режим заголовка CRM.")
        self.timeout = max(2.0, min(float(self.timeout), 30.0))
        if self._session is None:
            self._session = requests.Session()

    @property
    def ready(self) -> bool:
        return bool(self.token and self.header_mode)

    def _headers(self):
        if not self.header_mode:
            raise BillingNotConfigured(
                "Для запросов клиентов ещё не подтверждён заголовок CRM token/Authorization."
            )
        if self.header_mode == "token":
            return {"Accept": "application/json", "token": self.token}
        if self.header_mode == "authorization_raw":
            return {"Accept": "application/json", "Authorization": self.token}
        return {"Accept": "application/json", "Authorization": f"Bearer {self.token}"}

    def get_json(self, path: str) -> dict:
        if not isinstance(path, str) or not any(p.fullmatch(path) for p in _ALLOWED_ROUTES):
            raise BillingValidationError("Запрошен неподдерживаемый адрес CRM.")
        headers = self._headers()
        try:
            response = self._session.get(
                BILLING_API_ORIGIN + path,
                headers=headers,
                timeout=self.timeout,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise BillingTransportError("Нет соединения с сервером CRM.") from exc

        if response.status_code in (401, 403):
            raise BillingPermissionDenied(
                "Сессия CRM истекла или недостаточно прав. Повторите вход."
            )
        if not (200 <= response.status_code < 300):
            raise BillingTransportError("Сервер CRM вернул ошибку при запросе данных.")
        try:
            data = response.json()
        except ValueError as exc:
            raise BillingTransportError("CRM вернула некорректный JSON.") from exc
        if not isinstance(data, dict):
            raise BillingTransportError("CRM вернула неожиданную структуру ответа.")
        return data

    def close(self):
        self.token = ""
        if self._session is not None:
            self._session.close()
            self._session = None
