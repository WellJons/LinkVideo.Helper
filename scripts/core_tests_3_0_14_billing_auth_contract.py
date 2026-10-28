from __future__ import annotations

"""Contract-only regression for the observed CRM authentication request."""

from linkvideo_vpn_helper.services.billing_auth_contract import (
    BILLING_API_ORIGIN, BILLING_LOGIN_PATH, BILLING_LOGIN_METHOD,
    BILLING_LOGIN_CONTENT_TYPE, BillingLoginRequest,
)
from linkvideo_vpn_helper.services.billing_gateway import BillingValidationError

assert BILLING_API_ORIGIN == "https://api-billing-global.linkvideo.ru"
assert BILLING_LOGIN_PATH == "/auth/login"
assert BILLING_LOGIN_METHOD == "POST"
assert BILLING_LOGIN_CONTENT_TYPE == "application/json"

credentials = BillingLoginRequest(" employee.example ", "fictional-password-value")
assert credentials.as_json() == {
    "login": "employee.example", "password": "fictional-password-value",
}
assert "fictional-password-value" not in repr(credentials)
assert "employee.example" not in repr(credentials)

for login, password in (
    ("", "password"), ("person", ""), ("person\nInjected", "password"),
    (None, "password"), ("person", None),
):
    try:
        BillingLoginRequest(login, password)
        raise AssertionError("Malformed credentials were accepted")
    except BillingValidationError:
        pass

# The request shape is known, but no bearer token, cookie, response parser,
# HTTP call, or credential inheritance from MikroTik should be invented.
print("CORE TESTS BILLING AUTH REQUEST CONTRACT OK")
