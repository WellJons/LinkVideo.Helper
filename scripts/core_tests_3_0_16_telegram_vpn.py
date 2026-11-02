from __future__ import annotations

"""No-network regression: VPN create must be confirmed and idempotent."""
import os
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from linkvideo_vpn_helper.services.telegram_vpn_operations import (
    TelegramVPNOperations,
)
from linkvideo_vpn_helper.services.telegram_bot_menu import TelegramMenuAgent

class FakeService:
    def __init__(self):
        self.created = []
        self.host_checks = 0
        self.login_checks = 0

    def pick_best_server_parallel(self, hosts, creds):
        self.host_checks += 1
        assert "vpn01.linkvideo.ru" in hosts
        assert creds.username == "fixture-user"
        return "vpn01.linkvideo.ru"

    def suggest_next_login(self, server, creds, login):
        self.login_checks += 1
        assert server == "vpn01.linkvideo.ru"
        return login

    def create_clients_batch(self, server, creds, login, ports, accounts,
                             progress_callback=None, cancel_event=None):
        assert server == "vpn01.linkvideo.ru"
        assert ports == 3 and accounts == 1
        self.created.append(login)
        return [SimpleNamespace(copy_text=lambda: (
            "Имя: LV_VPN\nСервер: vpn01.linkvideo.ru\n"
            "Логин: " + login + "\nПароль: fixture-created-client-secret"
        ))]

with tempfile.TemporaryDirectory() as temp:
    os.environ["LINKVIDEO_TELEGRAM_STATE_DB"] = str(Path(temp) / "invites.sqlite3")
    backend = FakeService()
    creds = SimpleNamespace(username="fixture-user", password="fixture-secret")
    vpn = TelegramVPNOperations(service=backend,
                                credentials_provider=lambda: creds)
    proposal = vpn.prepare("89200000001", "Россия", 3)
    assert proposal.login == proposal.expected_login == "89200000001"
    assert backend.host_checks == backend.login_checks == 1
    assert not backend.created, "Prepare cannot change real MikroTik state."
    try:
        vpn.prepare("some invalid user", "Россия")
        raise AssertionError("Invalid login accepted")
    except ValueError:
        pass

    # Telegram menu: first step never creates a client.
    agent = TelegramMenuAgent(
        "123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        frozenset({12345678}), SimpleNamespace(), invites=None,
    )
    agent.vpn_operations = vpn
    sent = []
    def request(method, payload, photo=None):
        sent.append((method, payload))
        return {"ok": True}
    agent._request = request
    def inbound(text):
        agent.handle_update({"message": {
            "from": {"id": 12345678},
            "chat": {"id": 12345678, "type": "private"},
            "text": text,
        }})

    inbound("🔐 Создать VPN")
    assert 12345678 not in agent.waiting_vpn_login
    assert not backend.created
    first_prompt = sent[-1][1]
    keyboard = first_prompt["reply_markup"]["inline_keyboard"]
    assert [row[0]["callback_data"] for row in keyboard[:3]] == [
        "vpn_country:ru", "vpn_country:kz", "vpn_country:by",
    ]
    assert all(name in first_prompt["text"] for name in ["страну"])
    def callback(value):
        agent.handle_update({"callback_query": {
            "id": "test",
            "data": value,
            "from": {"id": 12345678},
            "message": {"chat": {"id": 12345678, "type": "private"}},
        }})
    callback("vpn_country:ru")
    assert agent.vpn_country[12345678] == "Россия"
    assert 12345678 in agent.waiting_vpn_login
    agent.last_request.pop(12345678, None)
    inbound("89200000001")
    assert len(agent.vpn_pending) == 1
    assert not backend.created
    token = agent.vpn_pending[12345678].request_id
    assert sent[-1][1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == (
        "vpn_confirm:" + token
    )
    callback("vpn_confirm:" + token)
    assert backend.created == ["89200000001"]
    assert "fixture-created-client-secret" in sent[-1][1]["text"]
    callback("vpn_confirm:" + token)
    assert backend.created == ["89200000001"], "Repeated callback created duplicate."
    try:
        vpn.execute(12345678, proposal)
        vpn.execute(12345678, proposal)
        raise AssertionError("Repeated database operation was allowed.")
    except RuntimeError:
        pass
    assert backend.created == ["89200000001", "89200000001"]

    callback("vpn_confirm:" + "f" * 24)
    assert backend.created == ["89200000001", "89200000001"]
    # Strangers must never initiate a MikroTik write.
    agent.handle_update({"message": {
        "from": {"id": 999999},
        "chat": {"id": 999999, "type": "private"},
        "text": "🔐 Создать VPN",
    }})
    assert not agent.vpn_pending.get(999999)
    os.environ.pop("LINKVIDEO_TELEGRAM_STATE_DB", None)


# Region-specific automatic server selection: never allow a Russian host
# when Kazakhstan or Belarus was explicitly chosen in Telegram.
from linkvideo_vpn_helper.services.server_registry import VPNServer

class RegionalRegistry:
    def all(self, include_disabled=False):
        return [
            VPNServer("vpn01.linkvideo.ru", "Россия"),
            VPNServer("vpn02.linkvideo.ru", "Россия"),
            VPNServer("kz-vpn01.linkvideo.ru", "Казахстан"),
            VPNServer("rb-vpn01.linkvideo.ru", "Беларусь"),
        ]

class RegionalService:
    def __init__(self):
        self.lookups = []
        self.created = []
    def pick_best_server_parallel(self, hosts, creds):
        self.lookups.append(tuple(hosts))
        return hosts[0]
    def suggest_next_login(self, server, creds, login):
        return login
    def create_clients_batch(self, server, creds, login, ports, accounts):
        self.created.append((server, login))
        return [SimpleNamespace(copy_text=lambda: "Создано: " + server)]

regional_service = RegionalService()
regional = TelegramVPNOperations(
    service=regional_service, registry=RegionalRegistry(),
    credentials_provider=lambda: creds,
)
for region, allowed_hosts in [
    ("Россия", ("vpn01.linkvideo.ru", "vpn02.linkvideo.ru")),
    ("Казахстан", ("kz-vpn01.linkvideo.ru",)),
    ("Беларусь", ("rb-vpn01.linkvideo.ru",)),
]:
    result = regional.prepare("89200000001", region, 3)
    assert result.country == region
    assert result.server in allowed_hosts
    assert regional_service.lookups[-1] == allowed_hosts
    assert not regional_service.created

for code, country in (("ru", "Россия"), ("kz", "Казахстан"),
                      ("by", "Беларусь")):
    agent = TelegramMenuAgent(
        "123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        frozenset({12345678}), SimpleNamespace(), invites=None,
    )
    agent.vpn_operations = regional
    sent = []
    agent._request = lambda method, payload, photo=None: (
        sent.append((method, payload)) or {"ok": True}
    )
    agent._begin_vpn(12345678)
    agent._handle_callback({
        "id": "regional-test",
        "data": "vpn_country:" + code,
        "from": {"id": 12345678},
        "message": {"chat": {"id": 12345678, "type": "private"}},
    })
    assert agent.vpn_country[12345678] == country
    agent._prepare_vpn(12345678, "89200000001")
    proposal = agent.vpn_pending[12345678]
    assert proposal.country == country
    assert ("Страна: " + country) in sent[-1][1]["text"]
    assert proposal.server in sent[-1][1]["text"]
    # Test stops before creating anything on MikroTik.
    assert not regional_service.created

try:
    regional.prepare("89200000001", "Украина", 3)
    raise AssertionError("Unlisted country was accepted")
except ValueError:
    pass

print("CORE TESTS 3.0.16 TELEGRAM VPN CONFIRM + IDEMPOTENCY OK")
