from __future__ import annotations

"""Observed CRM login *request* contract; not a live authentication client.

Network evidence confirms POST https://api-billing-global.linkvideo.ru/auth/login
with JSON keys "login" and "password". It does NOT establish the response
schema, session/token mechanism, refresh protocol, or permission model.

The active RouterOS-authenticated employee session is the only credential
source in the desktop Helper workflow. CRM independently validates those same
values against its own HTTPS endpoint and returns a distinct bearer token.
Do not copy RouterOS credentials to another storage key or reuse RouterOS
session tokens as billing tokens. Never include secrets in logs.
"""

from dataclasses import dataclass, field

from linkvideo_vpn_helper.services.billing_gateway import BillingValidationError


BILLING_API_ORIGIN = "https://api-billing-global.linkvideo.ru"
BILLING_LOGIN_PATH = "/auth/login"
BILLING_LOGIN_METHOD = "POST"
BILLING_LOGIN_CONTENT_TYPE = "application/json"


@dataclass(frozen=True, repr=False)
class BillingLoginRequest:
    """Ephemeral CRM login payload from the verified Helper employee session."""

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
