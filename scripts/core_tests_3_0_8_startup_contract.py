from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "linkvideo_vpn_helper/app.py"
source = APP.read_text(encoding="utf-8")
tree = ast.parse(source)

# The GUI must preserve the authenticated constructor contract. A previous
# regression replaced this with MainWindow(settings), which packages cleanly
# but crashes immediately at runtime because service/credentials are required.
main_window_calls = []
for node in ast.walk(tree):
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "MainWindow":
        main_window_calls.append(node)
assert len(main_window_calls) == 1, len(main_window_calls)
call = main_window_calls[0]
assert len(call.args) >= 3, "MainWindow must receive service, credentials and settings"
assert isinstance(call.args[0], ast.Name) and call.args[0].id == "service"
assert isinstance(call.args[1], ast.Name) and call.args[1].id == "credentials"
assert isinstance(call.args[2], ast.Name) and call.args[2].id == "settings"

assert "LoginWindow(settings)" in source
assert "credential_values = (login.payload.username, login.payload.password)" in source
assert "SessionCredentials(credential_values[0], credential_values[1], 8728, 4.5)" in source
assert "service = VPNService()" in source

# The AdminChats role must be installed before the MainWindow instance is
# created, so the actual credentials entered by the operator determine the UI.
assert source.index("install_access_policy()") < source.index("window = MainWindow(service, credentials, settings)")
assert source.index("install_update_ux()") < source.index("window = MainWindow(service, credentials, settings)")
assert source.index("install_background_ux()") < source.index("window = MainWindow(service, credentials, settings)")

print("CORE TESTS 3.0.8 STARTUP CONTRACT OK")
