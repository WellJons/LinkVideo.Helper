from __future__ import annotations

"""Explicit, safe CRM login probe for the observed POST /auth/login.

This does not infer how subsequent GET calls are authorized. It does not log or persist response values, cookies or tokens.
"""

from dataclasses import dataclass, field
from typing import Callable

import requests

from linkvideo_vpn_helper.services.billing_auth_contract import (
    BILLING_API_ORIGIN, BILLING_LOGIN_PATH, BillingLoginRequest,
)


class BillingLoginError(RuntimeError):
    pass


@dataclass(frozen=True)
class LoginProbeResult:
    status_code: int
    has_cookie: bool
    response_keys: tuple[str, ...]
    has_token_field: bool
    # Confirmed CRM response: {"token": "..."}; secret stays in memory only.
    token: str = field(default="", repr=False, compare=False)

    @property
    def session_known(self) -> bool:
        # A cookie or a token-shaped field alone cannot prove that protected
        # endpoints accept that authentication format.
        return False


def check_billing_login(request: BillingLoginRequest, *, sender: Callable | None = None) -> LoginProbeResult:
    """POST only to the fixed corporate HTTPS login endpoint on explicit click.

    The sender injection is for tests; no API session is exported from this
    function. Redirects are disabled so no password can cross to other hosts.
    """
    sender = sender or requests.post
    try:
        response = sender(
            BILLING_API_ORIGIN + BILLING_LOGIN_PATH,
            json=request.as_json(),
            headers={"Accept": "application/json"},
            timeout=8,
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        raise BillingLoginError("Не удалось связаться с сервером биллинга.") from exc

    code = response.status_code
    if code in (401, 403):
        raise BillingLoginError("Неверный логин, пароль или недостаточно прав в биллинге.")
    if not (200 <= code < 300):
        raise BillingLoginError("Сервер биллинга отклонил вход или недоступен.")
    try:
        data = response.json()
    except ValueError as exc:
        raise BillingLoginError("Биллинг вернул неожиданный ответ авторизации.") from exc
    if not isinstance(data, dict):
        raise BillingLoginError("Биллинг вернул неожиданный формат ответа.")
    if data.get("success") is False or data.get("error") is True:
        raise BillingLoginError("Биллинг отклонил корпоративную авторизацию.")

    # DevTools confirmed an exact JSON key: token. Its content is secret.
    # A successful HTTP status without a usable token is not a login.
    token = data.get("token")
    if not isinstance(token, str) or not token.strip():
        raise BillingLoginError("Сервер не выдал токен корпоративной авторизации.")
    token = token.strip()
    if any(char in token for char in ("\\r", "\\n")):
        raise BillingLoginError("Сервер вернул некорректный токен.")
    keys = tuple(sorted(str(k) for k in data if isinstance(k, str)))[:30]
    return LoginProbeResult(
        status_code=code,
        has_cookie=bool(response.headers.get("set-cookie")),
        response_keys=keys,
        has_token_field=True,
        token=token,
    )
