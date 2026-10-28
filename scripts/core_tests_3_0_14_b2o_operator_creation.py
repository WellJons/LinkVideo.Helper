from __future__ import annotations

"""Operator B2O verified POST contract using fake transport only."""

import io
import json
import os
import tempfile
import urllib.error
from unittest.mock import patch
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QLineEdit

from linkvideo_vpn_helper.services.b2o_client_admin import (
    ADMIN_URL, BASE_URL, B2OClientDraft, B2OOperatorReadOnly,
    B2OOperatorClientManager, B2OCreationOutcomeUnknown, B2OCreationRejected,
    B2OCreatedClient, safe_b2o_validation_hint,
    operator_for_country, settings_path, creation_path, created_account_id,
)
from linkvideo_vpn_helper.ui.b2o_client_create import (
    B2OClientCreationWidget, client_credentials_message, safe_b2o_error_code,
)


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

    def clear_token(self):
        self.session_token = ""

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
assert B2OClientDraft(**{
    **verified_draft.__dict__, "password": "Ab1234"
}).verified_payload()["password"] == "Ab1234"

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
# Rejections must preserve the numeric HTTP code without revealing server body.
for status in (400, 401, 403, 409, 422):
    fake.session_token = "fictional-B2O-token"
    body = b'{"message":"user private data and secret password"}'
    error = urllib.error.HTTPError(
        BASE_URL + "/operators/241/clients", status, "rejected", {}, None,
    )
    with patch("urllib.request.urlopen", side_effect=error) as urlopen:
        try:
            manager.create_client(verified_draft)
        except B2OCreationRejected as exc:
            assert exc.status_code == status
            assert f"HTTP {status}" in str(exc)
            assert "private data" not in str(exc)
            if status == 401:
                assert not fake.session_token
        else:
            raise AssertionError(f"Expected a safe rejection for HTTP {status}")
        assert urlopen.call_count == 1
# HTTP 400 error bodies may echo secrets; only predefined classifications pass.
assert safe_b2o_validation_hint(
    b'{"error":"Login already exists","password":"a-private-password"}'
) == "возможно, логин или клиент уже существует в B2O/биллинге"
assert safe_b2o_validation_hint(
    b'{"message":"invalid account_type and phone","name":"a-private-name"}'
) == "в ответе упомянуты поля: тип аккаунта, телефон"
assert safe_b2o_validation_hint(b'{"error":"An unknown error"}') == ""
assert safe_b2o_validation_hint(b'ORA-06502: invalid input') == "код серверной проверки ORA-06502"
assert safe_b2o_error_code(RuntimeError("B2O: HTTP 403 при запросе данных")) == "HTTP 403"
assert safe_b2o_error_code(ValueError("failure")) == "ValueError"

fake.session_token = "fictional-B2O-token"
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
    first_password = widget.password.text()
    assert len(first_password) == 9
    assert first_password.isalnum()
    assert any(ch.islower() for ch in first_password)
    assert any(ch.isupper() for ch in first_password)
    assert any(ch.isdigit() for ch in first_password)
    widget.generate_password.click()
    assert len(widget.password.text()) == 9
    assert widget.password.text() != first_password, "Password generator must generate a new value"
    widget.show_password.setChecked(True)
    assert widget.password.echoMode() == QLineEdit.EchoMode.Normal
    widget.show_password.setChecked(False)
    assert widget.password.echoMode() == QLineEdit.EchoMode.Password
    assert widget.account_type.count() == 2
    assert widget.account_type.itemText(0) == "Обычный"
    assert widget.account_type.itemText(1) == "Бизнес"
    assert widget.account_type.currentData() == "1"
    assert widget.account_type.isEnabled()
    assert widget.draft().country == "Казахстан"
    widget.draft().check()
    assert not widget.create_button.isEnabled(), "Non-RU POST must remain disabled"
    assert "1721" in widget.message.text()
    assert "POST" in widget.message.text()
    assert "нет POST API" in widget.create_button.text()
    widget._on_access(widget._attempt, "Казахстан", True, None)
    assert "1721" in widget.message.text(), "GET access must not imply write support"
    assert not widget.create_button.isEnabled()
    widget.country.setCurrentIndex(2)
    assert "1741" in widget.message.text()
    assert not widget.create_button.isEnabled()
    widget._on_access(widget._attempt, "Беларусь", True, None)
    assert "1741" in widget.message.text()
    assert not widget.create_button.isEnabled()
    widget.country.setCurrentIndex(0)
    assert widget.in_billing.isChecked(), "The only verified billing combination should be default"
    assert widget.create_button.isEnabled(), "Verified RU POST form should be enabled"
    widget.account_type.setCurrentIndex(1)
    assert widget.draft().account_type == "business-unverified"
    assert not widget.create_button.isEnabled(), "Unknown business code must not send a POST"
    assert "Для «Бизнеса»" in widget.message.text()
    widget.account_type.setCurrentIndex(0)
    assert widget.create_button.isEnabled()
    widget.cross_share.setChecked(True)
    assert not widget.create_button.isEnabled(), "Unverified sharing flag must block"
    widget.cross_share.setChecked(False)
    assert widget.create_button.isEnabled()
    copied_before = "unchanged-clipboard-until-manual-copy"
    QApplication.clipboard().setText(copied_before)
    submitted = widget.draft()
    widget._pending_draft = submitted
    widget._on_created(B2OCreatedClient(12345, True), None)
    expected_message = client_credentials_message(submitted.login, submitted.password)
    assert expected_message == (
        f"Создали вам аккаунт:\nЛогин: {submitted.login}\n"
        f"Пароль: {submitted.password}\n\n"
        "Данные можете поменять после того, как авторизуетесь в личном кабинете."
    )
    assert not widget.result_card.isHidden()
    assert widget.result_text.toPlainText() == expected_message
    assert QApplication.clipboard().text() == copied_before, "Do not copy passwords automatically"
    assert widget.copy_credentials.isEnabled()
    widget.copy_credentials.click()
    assert QApplication.clipboard().text() == expected_message
    assert "Скопировано" in widget.copy_credentials.text()
    assert not widget.create_button.isEnabled(), "Prevent duplicate creation"
    widget.login.setText("another-client")
    assert widget.create_button.isEnabled(), "A different login is a new operation"
    widget.login.setText(submitted.login)
    assert not widget.create_button.isEnabled(), "Returning to successful login must stay blocked"
    widget.login.setText("another-client")
    widget._pending_draft = widget.draft()
    widget._on_created(None, "unknown")
    assert not widget.create_button.isEnabled(), "Unknown outcome must stop resubmission"
    widget.login.setText("unique-client")
    assert widget.create_button.isEnabled()
    widget.login.setText("another-client")
    assert not widget.create_button.isEnabled(), "Returning to unknown-outcome login must stay blocked"
    widget.login.setText("test-error-client")
    widget._pending_draft = widget.draft()
    widget._on_created(None, "HTTP 403: нет прав на создание клиента в B2O")
    assert "HTTP 403" in widget.message.text()
    assert "нет прав" in widget.message.text()
    widget._pending_draft = widget.draft()
    widget._on_created(None, "preflight:HTTP 401")
    assert "POST не отправлялся" in widget.message.text()
    assert "HTTP 401" in widget.message.text()
    assert widget.account_type.isEnabled()
    widget.close()
app.processEvents()

print("CORE TESTS 3.0.14 OPERATOR B2O CLIENT CREATION PAYLOAD AND UI OK")
