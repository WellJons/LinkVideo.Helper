from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import patch

from linkvideo_vpn_helper.mikrotik.api_ssl_client import RouterOSAPIError
from linkvideo_vpn_helper.services import authentication as auth


class DummyAPI:
    def __init__(self, host, username, password, *, port, timeout):
        self.host = host
        self.username = username
        self.password = password

    def __enter__(self):
        if self.password == "wrong":
            raise RouterOSAPIError("invalid user name or password")
        if self.host == "offline":
            raise ConnectionRefusedError("connection refused")
        return self

    def __exit__(self, *_args):
        pass

    def print(self, path, *_args):
        if path == "/system/identity":
            return [{"name": "router"}]
        if path == "/user":
            if self.host == "restricted":
                raise RouterOSAPIError("not enough permissions")
            if self.host == "disabled":
                return [{"name": self.username, "disabled": "yes"}]
            return [{"name": self.username, "disabled": "no"}]
        raise AssertionError(path)


with patch.object(auth, "RouterOSAPIClient", DummyAPI):
    assert auth.verify_credentials("valid", "correct", ["offline", "online"]) == "online"
    assert auth.verify_credentials("valid", "correct", ["restricted"]) == "restricted"
    try:
        auth.verify_credentials("valid", "wrong", ["online"])
        raise AssertionError("Wrong password must not unlock Helper")
    except auth.AuthenticationFailure as exc:
        assert "Логин или пароль" in str(exc)
    try:
        auth.verify_credentials("valid", "correct", ["disabled"])
        raise AssertionError("Disabled account must not unlock Helper")
    except auth.AuthenticationFailure:
        pass
    try:
        auth.verify_credentials("valid", "correct", ["offline"])
        raise AssertionError("Network outage must not unlock Helper")
    except auth.AuthenticationFailure as exc:
        assert "подключиться" in str(exc)
    for username, password, hosts in (("", "pw", ["online"]), ("valid", "", ["online"]), ("valid", "pw", [])):
        try:
            auth.verify_credentials(username, password, hosts)
            raise AssertionError("Missing input must not unlock Helper")
        except auth.AuthenticationFailure:
            pass

root = Path(__file__).resolve().parents[1]
app = (root / "linkvideo_vpn_helper/app.py").read_text(encoding="utf-8")
login = (root / "linkvideo_vpn_helper/ui/login_window.py").read_text(encoding="utf-8")
ast.parse(app)
ast.parse(login)
assert "QTimer.singleShot(0, login._submit)" in app
assert "saved_username and saved_password" not in app.split("def main()", 1)[1]
assert "verify_credentials(user, password, hosts)" in login
assert login.index('verify_credentials(user, password, hosts)') < login.index('self.payload = LoginPayload(user, password)')
assert login.index('verify_credentials(user, password, hosts)') < login.index('self.settings.setValue("password", password)')
assert "self.validationFinished.connect(self._on_validation_finished)" in login

print("CORE TESTS ROUTEROS AUTHENTICATION OK")
