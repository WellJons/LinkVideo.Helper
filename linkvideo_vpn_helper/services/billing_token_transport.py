from __future__ import annotations

"""HTTPS-only CRM read-only transport with same-origin token header negotiation.

CRM login yields a token. Try the explicitly permitted token/Authorization
header variants *only after user login* on safe GET endpoints; retry solely
on 401, never on 403 or writes. Retain the successful mode for this session.
"""

import re
from threading import RLock
from urllib.parse import quote, unquote
from dataclasses import dataclass, field
from typing import Literal

import requests

from linkvideo_vpn_helper.services.billing_auth_contract import BILLING_API_ORIGIN
from linkvideo_vpn_helper.services.billing_gateway import (
    BillingNotConfigured, BillingPermissionDenied, BillingValidationError, validate_login,
)


HeaderMode = Literal["auto", "token", "authorization_bearer", "authorization_raw"]
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
    _lock: RLock = field(default_factory=RLock, repr=False, compare=False)
    timeout: float = 8.0
    _session: requests.Session | None = field(default=None, repr=False)

    def __post_init__(self):
        if not isinstance(self.token, str) or not self.token.strip():
            raise BillingValidationError("В CRM нет действующего токена входа.")
        if any(char in self.token for char in ("\r", "\n")):
            raise BillingValidationError("Некорректный токен CRM.")
        if self.header_mode not in (None, "auto", "token", "authorization_bearer", "authorization_raw"):
            raise BillingValidationError("Неизвестный режим заголовка CRM.")
        self.timeout = max(2.0, min(float(self.timeout), 30.0))
        if self._session is None:
            self._session = requests.Session()

    @property
    def ready(self) -> bool:
        return bool(self.token and self.header_mode and self._session is not None)

    def _headers(self, mode: str):
        if mode not in ("token", "authorization_bearer", "authorization_raw"):
            raise BillingNotConfigured("Формат авторизации API не поддерживается.")
        if mode == "token":
            return {"Accept": "application/json", "token": self.token}
        if mode == "authorization_raw":
            return {"Accept": "application/json", "Authorization": self.token}
        return {"Accept": "application/json", "Authorization": f"Bearer {self.token}"}

    def get_json(self, path: str) -> dict:
        if not isinstance(path, str) or not any(p.fullmatch(path) for p in _ALLOWED_ROUTES):
            raise BillingValidationError("Запрошен неподдерживаемый адрес CRM.")
        # Reject double-decoding or encoded path separators even when the
        # route regex otherwise matches. A login is one path segment.
        if path.startswith("/api/accounts/search/"):
            encoded_login = path.rsplit("/", 1)[-1]
            decoded_login = unquote(encoded_login)
            validate_login(decoded_login)
            if quote(decoded_login, safe="") != encoded_login:
                raise BillingValidationError("Некорректное кодирование логина CRM.")
        with self._lock:
            if self._session is None or not self.token:
                raise BillingPermissionDenied("Требуется повторный вход в CRM.")
            if not self.header_mode:
                raise BillingNotConfigured("Сначала выполните вход в CRM.")

            modes = (
                ("token", "authorization_bearer", "authorization_raw")
                if self.header_mode == "auto" else (self.header_mode,)
            )
            response = None
            for mode in modes:
                try:
                    response = self._session.get(
                        BILLING_API_ORIGIN + path,
                        headers=self._headers(mode),
                        timeout=self.timeout,
                        allow_redirects=False,
                    )
                except requests.RequestException as exc:
                    raise BillingTransportError(
                        "Нет соединения с сервером CRM."
                    ) from exc
                # 401 means this header format was rejected. 403 means
                # permissions are insufficient: never guess other headers.
                if response.status_code != 401:
                    if self.header_mode == "auto" and 200 <= response.status_code < 300:
                        self.header_mode = mode
                    break
            if response.status_code in (401, 403):
                raise BillingPermissionDenied(
                    "Сессия CRM истекла или недостаточно прав. Повторите вход."
                )
            if not (200 <= response.status_code < 300):
                raise BillingTransportError(
                    "Сервер CRM вернул ошибку при запросе данных."
                )
        try:
            data = response.json()
        except ValueError as exc:
            raise BillingTransportError("CRM вернула некорректный JSON.") from exc
        if not isinstance(data, dict):
            raise BillingTransportError("CRM вернула неожиданную структуру ответа.")
        return data

    def close(self):
        with self._lock:
            self.token = ""
            self.header_mode = None
            if self._session is not None:
                self._session.close()
                self._session = None
