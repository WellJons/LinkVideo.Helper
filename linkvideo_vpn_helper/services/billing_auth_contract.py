from __future__ import annotations

"""Observed CRM login *request* contract; not a live authentication client.

Network evidence confirms POST https://api-billing-global.linkvideo.ru/auth/login
with JSON keys "login" and "password". It does NOT establish the response
schema, session/token mechanism, refresh protocol, or permission model.

Never automatically reuse credentials from RouterOS or QSettings. A future
authorized transport must obtain the employee's approved billing credentials
explicitly, handle secrets outside logs, and check server-side permissions.
"""

from dataclasses import dataclass, field

from linkvideo_vpn_helper.services.billing_gateway import BillingValidationError


BILLING_API_ORIGIN = "https://api-billing-global.linkvideo.ru"
BILLING_LOGIN_PATH = "/auth/login"
BILLING_LOGIN_METHOD = "POST"
BILLING_LOGIN_CONTENT_TYPE = "application/json"


@dataclass(frozen=True, repr=False)
class BillingLoginRequest:
    """Explicit CRM credentials, kept separate from RouterOS credentials."""

    login: str
    password: str = field(repr=False)

    def __post_init__(self):
        if not isinstance(self.login, str) or not self.login.strip():
            raise BillingValidationError("Укажите корпоративный логин биллинга.")
        if not isinstance(self.password, str) or not self.password:
            raise BillingValidationError("Укажите пароль биллинга.")
        if "\r" in self.login or "\n" in self.login:
            raise BillingValidationError("Логин биллинга содержит недопустимые символы.")

    def __repr__(self) -> str:
        return "BillingLoginRequest(login=<redacted>, password=<redacted>)"

    def as_json(self) -> dict[str, str]:
        """Payload shape for a future explicitly authorized HTTPS transport."""
        return {"login": self.login.strip(), "password": self.password}
