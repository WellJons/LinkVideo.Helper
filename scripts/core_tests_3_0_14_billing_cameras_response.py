from __future__ import annotations

"""Observed /api/accounts/{id}/cameras contract: sanitized tests only."""

from copy import deepcopy

from linkvideo_vpn_helper.services.billing_gateway import BillingValidationError
from linkvideo_vpn_helper.services.billing_response_parser import (
    attach_cameras_to_client, parse_account_detail, parse_cameras_response,
)

account = {
    "success": True,
    "response": [{
        "login": "lkz_test_0001",
        "account_name": "Fictional account",
        "balance": 0,
        "status_name": "Активный",
        "dog_num": "TEST-10",
    }],
}
client = parse_account_detail(
    account, account_id=123, country="Казахстан", expected_login="lkz_test_0001"
)
assert client is not None
assert not client.services_loaded and len(client.services) == 0

# Same keys/types as the supplied response, but fictional camera values.
payload = {
    "success": True,
    "response": [
        {
            "id_camera": "901",
            "id_camera_vc": "11001",
            "short_name": "Тестовая камера склада",
            "tariff_name": "Тест 90 дней",
            "ab_plata": "0",
            "discount": "0",
            "costs": "0",
            "status": "1",
        },
        {
            "id_camera": "902",
            "id_camera_vc": "11002",
            "short_name": "Тестовая камера входа",
            "tariff_name": "Тест 30 дней",
            "ab_plata": "250",
            "discount": "20",
            "costs": "230",
            "status": "7",  # unconfirmed code MUST NOT become 'Выключен'
        },
    ],
}
services = parse_cameras_response(payload)
assert len(services) == 2
first, second = services
assert first.id == "901" and first.camera_id == "901"
assert first.camera_vc_id == "11001"
assert first.camera_name == "Тестовая камера склада"
assert first.tariff_name == "Тест 90 дней"
assert (first.price_text, first.discount_text, first.total_text) == ("0", "0", "0")
assert first.status == "Активный"
assert second.status == "Код статуса: 7"
assert (second.price_text, second.discount_text, second.total_text) == ("250", "20", "230")

enriched = attach_cameras_to_client(client, payload)
assert enriched.id == client.id and enriched.login == client.login
assert enriched.services_loaded and len(enriched.services) == 2
assert enriched.services == services
assert not client.services_loaded and not client.services

empty = attach_cameras_to_client(client, {"success": True, "response": []})
assert empty.services_loaded and len(empty.services) == 0

for bad in (
    {"success": False, "response": []},
    {"success": True, "response": {}},
    {"success": True, "response": [None]},
    {"success": True, "response": [{"id_camera": ""}]},
    {"success": True, "response": [payload["response"][0]] * 2},
    {"success": True, "response": [dict(payload["response"][0], id_camera="../1")]},
    {"success": True, "response": [dict(payload["response"][0], id_camera_vc="token?")]},
    {"success": True, "response": "not an array"},
):
    try:
        parse_cameras_response(bad)
        raise AssertionError(f"Invalid cameras response was accepted: {type(bad['response'])}")
    except BillingValidationError:
        pass

# Ensure absence of status does not imply a real connectivity state.
without_status = deepcopy(payload)
without_status["response"][0].pop("status")
assert parse_cameras_response(without_status)[0].status == "Неизвестно"

print("CORE TESTS BILLING CAMERAS RESPONSE PARSER OK")
