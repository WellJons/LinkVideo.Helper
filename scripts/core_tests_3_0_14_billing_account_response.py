from __future__ import annotations

"""Observed billing GET /api/accounts/{id} response contract (sanitized fixtures)."""

from copy import deepcopy

from linkvideo_vpn_helper.services.billing_gateway import (
    BillingPermissionDenied, BillingValidationError,
)
from linkvideo_vpn_helper.services.billing_response_parser import parse_account_detail

# Fictional details; no customers or request authorization secrets are stored.
envelope = {
    "success": True,
    "response": [{
        "login": "lkz_demo_001",
        "account_name": "Example Account",
        "balance": 0,
        "status_name": "Активный",
        "email": "test@example.invalid",
        "phone_number": "",
        "dog_num": "TEST-123",
        "manager_name": " ",
        "registration_date": "2026-10-13T00:00:00+07:00",
        "address": "",
    }],
}
client = parse_account_detail(
    envelope, account_id=286, country="Казахстан", expected_login="lkz_demo_001",
)
assert client is not None
assert client.id == "286"
assert client.login == "lkz_demo_001"
assert client.display_name == "Example Account"
assert client.status == "Активный"
assert client.balance_text == "0"   # no unverified currency
assert client.contract_number == "TEST-123"
assert client.email == "test@example.invalid"
assert client.phone == ""
assert client.manager == ""
assert client.registered_at == "13.10.2026"
assert client.address == ""
assert not client.services and not client.services_loaded
assert parse_account_detail(
    envelope, account_id=287, country="Беларусь", expected_login="lkz_demo_001",
).country == "Беларусь"  # country comes from the trusted source, not login prefix
assert parse_account_detail(
    {"success": True, "response": []},
    account_id=286, country="Казахстан",
) is None

for invalid_payload, account_id, country, expected in (
    ({"success": False, "response": []}, 286, "Казахстан", ""),
    ({"success": True, "response": {}}, 286, "Казахстан", ""),
    ({"success": True, "response": [envelope["response"][0]] * 2}, 286, "Казахстан", ""),
    (envelope, "286/cameras", "Казахстан", ""),
    (envelope, 0, "Казахстан", ""),
    (envelope, 286, "Казахстан", "other_client"),
    (envelope, 286, "Россия", ""),
):
    try:
        parse_account_detail(
            invalid_payload, account_id=account_id, country=country, expected_login=expected,
        )
        raise AssertionError("Invalid billing response accepted")
    except (BillingValidationError, BillingPermissionDenied):
        pass

bad = deepcopy(envelope)
bad["response"][0]["registration_date"] = "garbled"
try:
    parse_account_detail(bad, account_id=286, country="Казахстан")
    raise AssertionError("Malformed date accepted")
except BillingValidationError:
    pass

print("CORE TESTS BILLING ACCOUNT RESPONSE PARSER OK")
