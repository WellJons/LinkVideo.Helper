from __future__ import annotations

"""Offline privacy regression: bot remains silent without a valid invitation."""
import tempfile
from pathlib import Path
from types import SimpleNamespace

from linkvideo_vpn_helper.services.telegram_invites import InviteStore, invitation_url
from linkvideo_vpn_helper.services.telegram_camera_agent import TelegramCameraAgent

with tempfile.TemporaryDirectory() as td:
    database = Path(td) / "invites.sqlite"
    store = InviteStore(database)
    secret = store.create(hours=1, uses=1)
    assert "start=" + secret in invitation_url("LinkVideoHelperBot", secret)
    assert secret not in database.read_bytes().decode("utf-8", "ignore")
    assert store.accept(111111, "invalid") is False
    assert store.accept(111111, secret)
    assert store.is_member(111111)
    assert not store.accept(222222, secret), "Consumed invitation must be one-use"
    assert not store.is_member(222222)
    # Membership survives a restart and no additional invitation is needed.
    store = InviteStore(database)
    assert store.is_member(111111)
    store.revoke(111111)
    assert not store.is_member(111111)

    second = store.create(hours=1, uses=2)
    assert store.accept(111111, second)
    assert store.accept(222222, second)
    assert not store.accept(333333, second)

    agent = TelegramCameraAgent(
        "123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        frozenset(),
        SimpleNamespace(), invites=store,
    )
    replies = []
    agent._say = lambda chat, msg: replies.append((chat, msg))
    def message(uid, text, *, private=True):
        return {"message": {
            "from": {"id": uid},
            "chat": {"id": uid if private else -100, "type": "private" if private else "supergroup"},
            "text": text,
        }}
    # Ungranted strangers cannot obtain /help, cameras or VPN commands.
    agent.handle_update(message(444444, "/help"))
    agent.handle_update(message(444444, "/client 89001234567"))
    assert replies == []
    agent.handle_update(message(444444, "/start"))
    assert "приглашению" in replies[-1][1]
    agent.handle_update(message(444444, "/start INVALID"))
    assert not store.is_member(444444)
    agent.handle_update(message(444444, "/start " + store.create(hours=1)))
    assert store.is_member(444444)
    assert "открыт" in replies[-1][1]
    agent.handle_update(message(555555, "/start " + store.create(hours=1), private=False))
    assert not store.is_member(555555)
    agent.handle_update(message(444444, "/help"))
    assert "/client" in replies[-1][1]
    store.revoke(444444)
    previous = len(replies)
    agent.handle_update(message(444444, "/help"))
    assert len(replies) == previous

print("CORE TESTS 3.0.16 TELEGRAM INVITE ACCESS OK")
