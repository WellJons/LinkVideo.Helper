from __future__ import annotations

"""Operator B2O form and read-only GET contract, no client mutations."""

import os
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from linkvideo_vpn_helper.services.b2o_client_admin import (
    ADMIN_URL, BASE_URL, B2OClientDraft, B2OOperatorReadOnly,
    operator_for_country, settings_path,
)
from linkvideo_vpn_helper.ui.b2o_client_create import B2OClientCreationWidget


assert operator_for_country("Россия") == 241
assert operator_for_country("Казахстан") == 1721
assert operator_for_country("Беларусь") == 1741
assert settings_path("Россия") == "/operators/241/settings"
assert settings_path("Казахстан") == "/operators/1721/settings"
assert settings_path("Беларусь") == "/operators/1741/settings"
assert ADMIN_URL == "https://operator.b2o.goodline.info"

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
        return {"id_operator": url.split("/operators/")[1].split("/")[0], "timezone": 7}


fake = FakeB2O()
client = B2OOperatorReadOnly(fake)
for country in ("Россия", "Казахстан", "Беларусь"):
    out = client.check_access(country)
    assert str(out["id_operator"]) == str(operator_for_country(country))
assert len(fake.requests) == 3
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
    assert not widget.create_button.isEnabled(), "No undocumented POST"
    widget._on_access(widget._attempt, "Казахстан", True, None)
    assert not widget.create_button.isEnabled()
    widget.close()
app.processEvents()

print("CORE TESTS 3.0.14 OPERATOR B2O CLIENT CREATION UI READONLY OK")
