from __future__ import annotations

"""Observed /api/accounts/search/{login} plus verified GET composition.

Fixtures contain only invented IDs and names. No real tokens, credentials,
customer records, or network access is used.
"""

from copy import deepcopy
from dataclasses import replace

from linkvideo_vpn_helper.services.billing_gateway import (
    BillingNotConfigured, BillingPermissionDenied, BillingValidationError,
)
from linkvideo_vpn_helper.services.billing_readonly_gateway import BillingReadOnlyGateway
from linkvideo_vpn_helper.services.billing_search_parser import (
    parse_search_response, select_exact_search_hit,
)

search = {"success": True, "response": [{
    "id_account": "725",
    "account_name": "87000000001",
    "login": "lkz_87000000001",
    "balance": "0",
    "status_name": "Активный",
    "services_status": "2/2",
    "country_name": "Казахстан",
    "account_type_name": "Физическое лицо",
}]}
hits = parse_search_response(search)
assert len(hits) == 1
hit = hits[0]
assert (hit.id, hit.account_name, hit.login) == ("725", "87000000001", "lkz_87000000001")
assert hit.services_status == "2/2"
assert hit.country == "Казахстан" and hit.account_type == "Физическое лицо"
assert select_exact_search_hit(hits, "87000000001") == hit
assert select_exact_search_hit(hits, "LKZ_87000000001") == hit
assert select_exact_search_hit(hits, "870000") is None
assert select_exact_search_hit((), "87000000001") is None

for bad in (
    {"success": False, "response": []},
    {"success": True, "response": {}},
    {"success": True, "response": [None]},
    {"success": True, "response": [{"id_account": "725"}]},
    {"success": True, "response": [dict(search["response"][0], id_account="../725")]},
    {"success": True, "response": [search["response"][0]] * 2},
    {"success": True, "response": [dict(search["response"][0], login=123)]},
):
    try:
        parse_search_response(bad)
        raise AssertionError("Invalid search response was accepted")
    except BillingValidationError:
        pass

try:
    select_exact_search_hit(
        hits + (replace(hit, id="726"),), "87000000001"
    )
    raise AssertionError("Ambiguous exact account was selected")
except BillingValidationError:
    pass

try:
    select_exact_search_hit((replace(hit, country="Россия"),), hit.login)
    raise AssertionError("Unsupported country was selected")
except BillingPermissionDenied:
    pass

detail = {"success": True, "response": [{
    "login": "lkz_87000000001", "account_name": "87000000001",
    "status_name": "Активный", "dog_num": "TEST-725",
    "registration_date": "2026-10-13T00:00:00+07:00",
    "email": "test@example.invalid", "balance": 0,
}]}
cameras = {"success": True, "response": [
    {
        "id_camera": "1101", "id_camera_vc": "93001",
        "short_name": "Тестовая камера 1", "tariff_name": "Тестовый тариф 90 дней",
        "ab_plata": "0", "discount": "0", "costs": "0", "status": "1",
    },
    {
        "id_camera": "1102", "id_camera_vc": "93002",
        "short_name": "Тестовая камера 2", "tariff_name": "Тестовый тариф 30 дней",
        "ab_plata": "250", "discount": "0", "costs": "250", "status": "1",
    },
]}
responses = {
    "/api/accounts/search/87000000001": search,
    "/api/accounts/search/lkz_87000000001": search,
    "/api/accounts/725": detail,
    "/api/accounts/725/cameras": cameras,
}
calls = []


def mock_authorized_get(path):
    calls.append(path)
    return deepcopy(responses[path])


gateway = BillingReadOnlyGateway(mock_authorized_get)
assert gateway.configured and not gateway.can_change_tariffs
result = gateway.find_client("87000000001")
assert result is not None
assert result.id == "725" and result.country == "Казахстан"
assert result.login == "lkz_87000000001"
assert result.legal_type == "Физическое лицо"
assert result.services_status == "2/2"
assert result.display_name == "87000000001"
assert result.registered_at == "13.10.2026"
assert result.services_loaded and len(result.services) == 2
assert result.services[0].camera_vc_id == "93001"
assert calls == [
    "/api/accounts/search/87000000001",
    "/api/accounts/725",
    "/api/accounts/725/cameras",
]
assert gateway.find_client("lkz_87000000001") == result
# An exact query matching no result does not fetch details.
responses["/api/accounts/search/00000000000"] = search
calls.clear()
assert gateway.find_client("00000000000") is None
assert calls == ["/api/accounts/search/00000000000"]

# Detail login must match the selected search record before cameras are read.
bad_detail = deepcopy(detail)
bad_detail["response"][0]["login"] = "lkz_different_account"
responses["/api/accounts/725"] = bad_detail
calls.clear()
try:
    gateway.find_client("87000000001")
    raise AssertionError("Mismatched detail response accepted")
except BillingValidationError:
    pass
assert calls == ["/api/accounts/search/87000000001", "/api/accounts/725"]

try:
    gateway.change_tariff(None)
    raise AssertionError("Read-only gateway allowed tariff mutation")
except BillingNotConfigured:
    pass
try:
    gateway.tariff_options("725", "1101")
    raise AssertionError("Unverified tariff-list method was allowed")
except BillingNotConfigured:
    pass

print("CORE TESTS BILLING SEARCH AND READONLY GATEWAY OK")
