from __future__ import annotations

"""Same-origin CRM transport with verified GETs and a single confirmed tariff PUT.

Only GET may probe header formats. PUT uses the header mode already proven
by a successful authenticated GET, never retries and never redirects.
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
    re.compile(r"^/api/cameras/[1-9][0-9]{0,17}/history$"),
    re.compile(r"^/api/tariffs/list/[01]$"),
    re.compile(r"^/api/cameras/archive-depth/1$"),
    re.compile(r"^/api/countries/1$"),
)


class BillingTransportError(RuntimeError):
    pass


class BillingWriteOutcomeUnknown(BillingTransportError):
    """The tariff PUT may have reached billing; check before attempting again."""


class BillingWriteRejected(BillingTransportError):
    """The server explicitly rejected the PUT."""

    def __init__(self, code: int):
        self.status_code = int(code)
        super().__init__(f"Биллинг отклонил изменение тарифа: HTTP {code}.")


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

    def put_camera_status(self, camera_id: str, status: int, effective_date) -> dict:
        """Observed PUT /api/cameras/{id}/status, once, no automatic retry."""
        from datetime import date
        if not isinstance(camera_id, str) or not re.fullmatch(
            r"[1-9][0-9]{0,17}", camera_id
        ):
            raise BillingValidationError("Некорректный ID камеры.")
        if type(status) is not int or status not in (0, 1):
            raise BillingValidationError("Статус камеры должен быть 0 или 1.")
        if not isinstance(effective_date, date) or effective_date < date.today():
            raise BillingValidationError("Выберите текущую или будущую дату.")
        path = f"/api/cameras/{camera_id}/status"
        with self._lock:
            if not self.token or self._session is None:
                raise BillingPermissionDenied("Требуется повторный вход в биллинг.")
            if self.header_mode not in (
                "token", "authorization_bearer", "authorization_raw"
            ):
                raise BillingNotConfigured("Сначала загрузите камеры из CRM.")
            try:
                response = self._session.put(
                    BILLING_API_ORIGIN + path,
                    json={"status": status, "date": effective_date.strftime("%d-%m-%Y")},
                    headers=self._headers(self.header_mode),
                    timeout=self.timeout,
                    allow_redirects=False,
                )
            except requests.RequestException:
                raise BillingWriteOutcomeUnknown(
                    "Ответ смены статуса неизвестен. Проверьте биллинг перед повтором."
                ) from None
            code = response.status_code
            if code in (401, 403):
                raise BillingPermissionDenied(
                    f"Недостаточно прав для смены статуса (HTTP {code})."
                )
            if code in (408, 425, 429) or code >= 500:
                raise BillingWriteOutcomeUnknown(
                    f"HTTP {code}: неизвестен результат изменения статуса. "
                    "Проверьте биллинг перед повтором."
                )
            if not 200 <= code < 300:
                raise BillingWriteRejected(code)
            try:
                data = response.json()
            except ValueError:
                raise BillingWriteOutcomeUnknown(
                    "Биллинг не вернул подтверждение изменения статуса."
                ) from None
            if not isinstance(data, dict):
                raise BillingWriteOutcomeUnknown(
                    "Некорректный ответ изменения статуса."
                )
            return data

    def put_camera_owner(
        self, camera_id: str, from_account_id: str,
        to_account_id: str, *, move_in_vc: bool = False,
    ) -> dict:
        """One confirmed owner transfer, using the captured CRM request shape.

        Only move_in_vc=0 is verified; never silently move a video camera in B2O.
        A timeout or unclear response is not permission to retry.
        """
        for identifier in (camera_id, from_account_id, to_account_id):
            if not isinstance(identifier, str) or not re.fullmatch(
                r"[1-9][0-9]{0,17}", identifier
            ):
                raise BillingValidationError("Некорректный ID камеры или аккаунта.")
        if from_account_id == to_account_id:
            raise BillingValidationError("Выберите другой аккаунт получателя.")
        if move_in_vc is not False:
            raise BillingNotConfigured(
                "Перенос в VC пока не подтверждён запросом API."
            )
        path = f"/api/cameras/{camera_id}/owner"
        with self._lock:
            if not self.token or self._session is None:
                raise BillingPermissionDenied("Требуется повторный вход в CRM.")
            if self.header_mode not in (
                "token", "authorization_bearer", "authorization_raw"
            ):
                raise BillingNotConfigured("Сначала загрузите камеры из CRM.")
            try:
                response = self._session.put(
                    BILLING_API_ORIGIN + path,
                    json={
                        "from_id_account": from_account_id,
                        "to_id_account": to_account_id,
                        "move_in_vc": "0",
                    },
                    headers=self._headers(self.header_mode),
                    timeout=self.timeout,
                    allow_redirects=False,
                )
            except requests.RequestException:
                raise BillingWriteOutcomeUnknown(
                    "Неизвестен результат передачи камеры. Проверьте CRM "
                    "перед повтором."
                ) from None
            code = response.status_code
            if code in (401, 403):
                raise BillingPermissionDenied(
                    f"Нет прав на передачу камеры (HTTP {code})."
                )
            if code in (408, 425, 429) or code >= 500:
                raise BillingWriteOutcomeUnknown(
                    f"HTTP {code}: результат передачи неизвестен. "
                    "Проверьте CRM перед повтором."
                )
            if not 200 <= code < 300:
                raise BillingWriteRejected(code)
            try:
                result = response.json()
            except ValueError:
                raise BillingWriteOutcomeUnknown(
                    "CRM не вернула подтверждение передачи камеры."
                ) from None
            if not isinstance(result, dict):
                raise BillingWriteOutcomeUnknown(
                    "Неожиданная структура ответа передачи камеры."
                )
            return result

    def put_tariff(self, camera_id: str, tariff_id: str, effective_date) -> dict:
        """Observed PUT /api/cameras/{id_camera}/tariff/{id_tariff}.

        Date is serialized DD-MM-YYYY exactly as shown in DevTools.
        A successful preceding GET must have resolved the CRM token header.
        Never repeat a write on timeout, 5xx or ambiguous response.
        """
        from datetime import date
        if not isinstance(effective_date, date) or effective_date < date.today():
            raise BillingValidationError("Укажите сегодняшнюю или будущую дату тарифа.")
        for ident in (camera_id, tariff_id):
            if not isinstance(ident, str) or not re.fullmatch(r"[1-9][0-9]{0,17}", ident):
                raise BillingValidationError("Некорректный ID камеры или тарифа.")
        path = f"/api/cameras/{camera_id}/tariff/{tariff_id}"
        with self._lock:
            if not self.token or self._session is None:
                raise BillingPermissionDenied("Нужен повторный вход в биллинг.")
            # GET determines this. Never try alternative tokens on a write.
            if self.header_mode not in ("token", "authorization_bearer", "authorization_raw"):
                raise BillingNotConfigured(
                    "Сначала загрузите клиента для проверки CRM-авторизации."
                )
            try:
                response = self._session.put(
                    BILLING_API_ORIGIN + path,
                    json={"date": effective_date.strftime("%d-%m-%Y")},
                    headers=self._headers(self.header_mode),
                    timeout=self.timeout,
                    allow_redirects=False,
                )
            except requests.RequestException:
                raise BillingWriteOutcomeUnknown(
                    "Связь потеряна при изменении тарифа. Проверьте биллинг перед повтором."
                ) from None
            status = response.status_code
            if status in (401, 403):
                raise BillingPermissionDenied(f"Нет прав на изменение тарифа (HTTP {status}).")
            if status in (408, 425, 429) or status >= 500:
                raise BillingWriteOutcomeUnknown(
                    f"Биллинг вернул HTTP {status}; результат смены тарифа неизвестен. "
                    "Проверьте биллинг перед повтором."
                )
            if not 200 <= status < 300:
                raise BillingWriteRejected(status)
            try:
                parsed = response.json()
            except ValueError:
                raise BillingWriteOutcomeUnknown(
                    "Биллинг не вернул понятное подтверждение PUT. "
                    "Проверьте услугу перед повтором."
                ) from None
            if not isinstance(parsed, dict):
                raise BillingWriteOutcomeUnknown(
                    "Биллинг вернул неизвестную структуру ответа. Проверьте услугу."
                )
            return parsed

    def close(self):
        with self._lock:
            self.token = ""
            self.header_mode = None
            if self._session is not None:
                self._session.close()
                self._session = None
