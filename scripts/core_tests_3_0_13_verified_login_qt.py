from __future__ import annotations

"""Qt login smoke: no main-window access until live verification succeeds."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import threading
import time
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QDialog

from linkvideo_vpn_helper.services.routeros_login_verification import (
    LoginRejected, VerifiedRouterOSUser,
)
from linkvideo_vpn_helper.ui import login_window as login_module


def spin(app, condition, seconds=5):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        if condition():
            return
        time.sleep(0.015)
    raise AssertionError("Qt login worker result was never delivered")


def main():
    app = QApplication.instance() or QApplication([])
    original = login_module.verify_routeros_user
    try:
        with tempfile.TemporaryDirectory() as raw:
            settings = QSettings(str(Path(raw) / "login.ini"), QSettings.Format.IniFormat)
            settings.setValue("remember", True)

            entered = threading.Event()
            proceed = threading.Event()

            def verify_pending(user, password, servers):
                entered.set()
                assert proceed.wait(5)
                return VerifiedRouterOSUser(user, "full", "vpn01.linkvideo.ru")

            login_module.verify_routeros_user = verify_pending
            form = login_module.LoginWindow(settings)
            form.login.setText("evstegneev.na")
            form.password.setText("correct")
            form._submit()
            assert entered.wait(3)
            assert form._pending
            assert form.payload is None
            assert not settings.contains("password"), "Unverified password was saved"
            assert form.result() != QDialog.DialogCode.Accepted
            proceed.set()
            spin(app, lambda: form.payload is not None)
            assert form.result() == QDialog.DialogCode.Accepted
            assert settings.value("password", "", str) == "correct"

            # Revoked/stale saved credentials: show the login screen and
            # remove the cached secret instead of skipping authentication.
            def reject(user, password, servers):
                raise LoginRejected("Неверный логин или пароль MikroTik.")

            login_module.verify_routeros_user = reject
            stale = login_module.LoginWindow(settings)
            stale.show()
            spin(app, lambda: bool(stale.error.text()))
            assert stale.payload is None
            assert stale.result() != QDialog.DialogCode.Accepted
            assert not settings.contains("password")
            stale.close()

            # A manual attempt with a new valid password can recover.
            login_module.verify_routeros_user = (
                lambda user, password, servers: VerifiedRouterOSUser(user, "full", "vpn01.linkvideo.ru")
            )
            retry = login_module.LoginWindow(settings)
            retry.login.setText("evstegneev.na")
            retry.password.setText("new-password")
            retry._submit()
            spin(app, lambda: retry.payload is not None)
            assert settings.value("password", "", str) == "new-password"
            retry.close()
    finally:
        login_module.verify_routeros_user = original

    print("CORE TESTS: QT VERIFIED LOGIN UI OK")


if __name__ == "__main__":
    main()
