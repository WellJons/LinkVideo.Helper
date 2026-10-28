from __future__ import annotations

"""Operator B2O verified POST contract using fake transport only."""

import json
import os
import tempfile
import urllib.error
from unittest.mock import patch
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from linkvideo_vpn_helper.services.b2o_client_admin import (
    ADMIN_URL, BASE_URL, B2OClientDraft, B2OOperatorReadOnly,
    B2OOperatorClientManager, B2OCreationOutcomeUnknown, B2OCreatedClient,
    operator_for_country, settings_path, creation_path, created_account_id,
)
from linkvideo_vpn_helper.ui.b2o_client_create import B2OClientCreationWidget


assert operator_for_country("Россия") == 241
assert operator_for_country("Казахстан") == 1721
assert operator_for_country("Беларусь") == 1741
assert settings_path("Россия") == "/operators/241/settings"
assert settings_path("Казахстан") == "/operators/1721/settings"
assert settings_path("Беларусь") == "/operators/1741/settings"
assert ADMIN_URL == "https://operator.b2o.goodline.info"
assert creation_path("Россия") == "/operators/241/clients"
assert creation_path("Казахстан") == "/operators/1721/clients"
assert creation_path("Беларусь") == "/operators/1741/clients"
assert created_account_id({"id_account": 12345}) == 12345
assert created_account_id({"id_account": "12345"}) == 12345
for bad_response in ({}, {"id_account": None}, {"id_account": 0},
                     {"id_account": "-2"}, {"id_account": True}, {"id_account": "5.2"},
                     [], {"id_account": "0011"}):
    try:
        created_account_id(bad_response)
    except ValueError:
        pass
    else:
        raise AssertionError("Wrongly accepted invalid creation response")

for value in ("", "Germany", "admin"):
    try:
        operator_for_country(value)
    except ValueError:
        pass
    else:
        raise AssertionError("Unexpected operator accepted")


class FakeB2O:
    def __init__(self):
        self.requests = []
        self.session_token = "fictional-B2O-token"

    def token(self):
        return self.session_token

    def request_json(self, url, token):
        assert token == self.session_token
        assert url.startswith(BASE_URL)
        self.requests.append(url)
        if url.endswith("/settings"):
            return {"id_operator": url.split("/operators/")[1].split("/")[0], "timezone": 7}
        if url.endswith("/clients/12345"):
            return {"id_account": 12345, "login": "fictional-client"}
        raise AssertionError("Unexpected B2O GET path")


fake = FakeB2O()
client = B2OOperatorReadOnly(fake)
for country in ("Россия", "Казахстан", "Беларусь"):
    out = client.check_access(country)
    assert str(out["id_operator"]) == str(operator_for_country(country))
assert len(fake.requests) == 3
assert client.read_client("Россия", 12345)["id_account"] == 12345
for invalid_id in (0, -1, True, 10**20):
    try:
        client.read_client("Россия", invalid_id)
    except ValueError:
        pass
    else:
        raise AssertionError("Unexpected account id accepted")
fake.session_token = ""
try:
    client.check_access("Россия")
except PermissionError:
    pass
else:
    raise AssertionError("Missing B2O login should block GET")

valid = B2OClientDraft(
    country="Казахстан", name="Пример", login="demo_operator_01",
    password="fictional-password", account_type="Физическое лицо",
)
valid.check()
for changed in (
    {"country": "Other"}, {"name": ""}, {"login": ""},
    {"password": "short"}, {"account_type": ""},
):
    kwargs = dict(
        country="Казахстан", name="Пример", login="demo_operator_01",
        password="fictional-password", account_type="Физическое лицо",
    )
    kwargs.update(changed)
    try:
        B2OClientDraft(**kwargs).check()
    except ValueError:
        pass
    else:
        raise AssertionError(f"Invalid draft accepted: {tuple(changed)}")

# The captured successful POST supports only Russia, type "1", billing=1, sharing=0.
verified_draft = B2OClientDraft(
    country="Россия", name="Тест", login="fictional-client",
    password="fictional-password", phone="70000000000",
    account_type="1", create_in_billing=True, cross_sharing=False,
)
expected_payload = {
    "account_type": "1",
    "client_name": "Тест",
    "cross_sharing_enabled": 0,
    "login": "fictional-client",
    "password": "fictional-password",
    "phone": "70000000000",
    "push_to_billing": 1,
}
assert verified_draft.verified_payload() == expected_payload
assert type(verified_draft.verified_payload()["account_type"]) is str
assert type(verified_draft.verified_payload()["push_to_billing"]) is int
assert type(verified_draft.verified_payload()["cross_sharing_enabled"]) is int

for forbidden in (
    {"country": "Казахстан"}, {"country": "Беларусь"},
    {"account_type": "2"}, {"account_type": "Физическое лицо"},
    {"create_in_billing": False}, {"cross_sharing": True},
):
    params = dict(
        country="Россия", name="Тест", login="fictional-client",
        password="fictional-password", phone="70000000000",
        account_type="1", create_in_billing=True, cross_sharing=False,
    )
    params.update(forbidden)
    try:
        B2OClientDraft(**params).verified_payload()
    except ValueError:
        pass
    else:
        raise AssertionError(f"Unverified variant accepted: {forbidden}")

fake.session_token = "fictional-B2O-token"
manager = B2OOperatorClientManager(fake)
posts = []

class FakeReply:
    def __init__(self, payload=b'{"id_account":12345}'):
        self.payload = payload
    def __enter__(self):
        return self
    def __exit__(self, *_):
        return False
    def read(self):
        return self.payload

def mock_post(request, timeout):
    assert timeout == 25
    assert request.get_method() == "POST"
    assert request.full_url == BASE_URL + "/operators/241/clients"
    assert request.get_header("Token") == fake.session_token
    assert request.get_header("Content-type") == "application/json"
    assert json.loads(request.data.decode("utf-8")) == expected_payload
    posts.append(request)
    return FakeReply()

with patch("urllib.request.urlopen", side_effect=mock_post):
    result = manager.create_client(verified_draft)
    assert result == B2OCreatedClient(12345, True)
assert len(posts) == 1
assert fake.requests[-1].endswith("/operators/241/clients/12345")

with patch("urllib.request.urlopen", side_effect=mock_post):
    try:
        manager.create_client(B2OClientDraft(**{
            **verified_draft.__dict__, "cross_sharing": True
        }))
    except ValueError:
        pass
    else:
        raise AssertionError("Attempted unverified mutation")
assert len(posts) == 1

with patch("urllib.request.urlopen", return_value=FakeReply(b'{}')) as urlopen:
    try:
        manager.create_client(verified_draft)
    except B2OCreationOutcomeUnknown:
        pass
    else:
        raise AssertionError("POST without ID treated as success")
    assert urlopen.call_count == 1, "Must never retry uncertain POST"
with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("fictional timeout")) as urlopen:
    try:
        manager.create_client(verified_draft)
    except B2OCreationOutcomeUnknown:
        pass
    else:
        raise AssertionError("Network failure must flag unknown outcome")
    assert urlopen.call_count == 1

app = QApplication.instance() or QApplication([])
with tempfile.TemporaryDirectory() as temp:
    settings = QSettings(str(Path(temp) / "local.ini"), QSettings.Format.IniFormat)
    widget = B2OClientCreationWidget(settings, b2o_service=fake)
    assert widget.country.count() == 3
    assert widget.country.itemData(0) == 241
    assert widget.country.itemData(1) == 1721
    assert widget.country.itemData(2) == 1741
    assert not widget.create_button.isEnabled()
    widget.country.setCurrentIndex(1)
    widget.name.setText("Пример")
    widget.login.setText("demo_operator_01")
    widget.generate_password.click()
    assert len(widget.password.text()) == 18
    widget.account_type.setCurrentIndex(1)
    assert widget.draft().country == "Казахстан"
    widget.draft().check()
    assert not widget.create_button.isEnabled(), "Non-RU POST must remain disabled"
    widget._on_access(widget._attempt, "Казахстан", True, None)
    assert not widget.create_button.isEnabled()
    widget.country.setCurrentIndex(0)
    widget.in_billing.setChecked(True)
    assert widget.create_button.isEnabled(), "Verified RU POST form should be enabled"
    widget.cross_share.setChecked(True)
    assert not widget.create_button.isEnabled(), "Unverified sharing flag must block"
    widget.cross_share.setChecked(False)
    assert widget.create_button.isEnabled()
    widget._on_created(B2OCreatedClient(12345, True), None)
    assert not widget.create_button.isEnabled(), "Prevent duplicate creation"
    widget.login.setText("another-client")
    assert widget.create_button.isEnabled(), "A different login is a new operation"
    widget._on_created(None, "unknown")
    assert not widget.create_button.isEnabled(), "Unknown outcome must stop resubmission"
    widget.close()
app.processEvents()

print("CORE TESTS 3.0.14 OPERATOR B2O CLIENT CREATION PAYLOAD AND UI OK")
