from __future__ import annotations

"""Regression for login request transport, protected CRM storage, and tabs."""

import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QSettings

from linkvideo_vpn_helper.services.billing_auth_contract import BillingLoginRequest
from linkvideo_vpn_helper.services.billing_credential_store import BillingCredentialStore
from linkvideo_vpn_helper.services.billing_login_probe import (
    BillingLoginError, check_billing_login,
)


def fake_encrypt(value: str) -> str:
    return "dpapi:" + value[::-1]


def fake_decrypt(value: str) -> str:
    return value[6:][::-1]


class FakeResponse:
    def __init__(self, status, json_value, headers=None):
        self.status_code = status
        self._json_value = json_value
        self.headers = headers or {}

    def json(self):
        return self._json_value


def sender_for(response, calls):
    def send(url, **kwargs):
        calls.append((url, kwargs))
        return response
    return send


with tempfile.TemporaryDirectory() as temp:
    path = Path(temp) / "credentials.ini"
    settings = QSettings(str(path), QSettings.Format.IniFormat)
    # Historical Helper settings use another key namespace. It must never
    # become CRM password unless the employee explicitly presses reuse.
    settings.setValue("username", "fake.helper.user")
    settings.setValue("password", "fake.helper.secret")
    store = BillingCredentialStore(
        settings, platform="win32", protector=fake_encrypt, unprotector=fake_decrypt,
    )
    assert store.load().password == ""
    assert store.helper_credentials().username == "fake.helper.user"
    assert store.helper_credentials().password == "fake.helper.secret"
    assert not settings.contains("billing/crm/password_dpapi")
    store.save("fake.crm.user", "fictional-crm-secret", remember=True)
    assert store.load().username == "fake.crm.user"
    assert store.load().password == "fictional-crm-secret"
    assert settings.value("billing/crm/password_dpapi") != "fictional-crm-secret"
    assert "fictional-crm-secret" not in path.read_text(encoding="utf-8")
    assert "fake.helper.secret" in path.read_text(encoding="utf-8")
    # Old settings untouched.
    assert settings.value("password") == "fake.helper.secret"
    assert "fictional-crm-secret" not in repr(store.load())
    store.save("fake.crm.user", "x", remember=False)
    assert store.load().password == ""
    assert not settings.contains("billing/crm/password_dpapi")

    other_os = BillingCredentialStore(
        settings, platform="linux", protector=fake_encrypt, unprotector=fake_decrypt,
    )
    try:
        other_os.save("crm", "abc", remember=True)
        raise AssertionError("Non-Windows password persisted")
    except RuntimeError:
        pass

calls = []
req = BillingLoginRequest(" fake.crm.user ", "fictional-crm-secret")
result = check_billing_login(req, sender=sender_for(
    FakeResponse(200, {"success": True, "access_token": "fake-secret-token"},
                 {"set-cookie": "session=fake-cookie"}), calls,
))
assert result.status_code == 200
assert result.has_cookie and result.has_token_field
assert result.response_keys == ("access_token", "success")
assert result.session_known is False
assert "fake-secret-token" not in repr(result)
assert "fake-cookie" not in repr(result)
assert calls[0][0] == "https://api-billing-global.linkvideo.ru/auth/login"
assert calls[0][1]["json"] == {"login": "fake.crm.user", "password": "fictional-crm-secret"}
assert calls[0][1]["allow_redirects"] is False
assert calls[0][1]["timeout"] == 8

for response in (
    FakeResponse(401, {"detail": "bad password"}),
    FakeResponse(403, {"detail": "forbidden"}),
    FakeResponse(302, {"success": True}),
    FakeResponse(200, {"success": False}),
):
    try:
        check_billing_login(req, sender=sender_for(response, []))
        raise AssertionError("Failed CRM auth was accepted")
    except BillingLoginError:
        pass

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from linkvideo_vpn_helper.ui.billing_all_tariffs import BillingAllTariffsWidget
from linkvideo_vpn_helper.ui.billing_login_panel import BillingLoginPanel
from linkvideo_vpn_helper.ui.pages.tariffs_page import TariffsPage

app = QApplication.instance() or QApplication([])
with tempfile.TemporaryDirectory() as temp:
    settings = QSettings(str(Path(temp) / "ui.ini"), QSettings.Format.IniFormat)
    settings.setValue("username", "existing.helper")
    settings.setValue("password", "fictional-helper-password")
    store = BillingCredentialStore(
        settings, platform="win32", protector=fake_encrypt, unprotector=fake_decrypt,
    )
    panel = BillingLoginPanel(settings, credential_store=store)
    assert panel.username.text() == ""
    assert panel.password.text() == ""
    panel.use_saved.click()
    assert panel.username.text() == "existing.helper"
    assert panel.password.text() == "fictional-helper-password"
    assert not settings.contains("billing/crm/password_dpapi")
    panel.clear_btn.click()
    assert not panel.username.text() and not panel.password.text()
    panel.close()

    page = TariffsPage(settings=settings)
    assert page.workspace_tabs.count() == 2
    assert page.workspace_tabs.tabText(0) == "Поиск клиента"
    assert page.workspace_tabs.tabText(1) == "Все тарифы"
    assert isinstance(page.all_tariffs_page, BillingAllTariffsWidget)
    assert page.billing_login is not None
    assert not page.all_tariffs_page.refresh.isEnabled()
    page.close()
    app.processEvents()

print("CORE TESTS BILLING LOGIN UI AND DPAPI STORAGE OK")
