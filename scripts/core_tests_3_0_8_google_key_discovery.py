from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from linkvideo_vpn_helper.services.google_key_discovery_compat import (
    discover_service_account_file,
    install_google_key_discovery,
)
from linkvideo_vpn_helper.services.vpn_sheets_sync import GoogleSheetsBackend


with tempfile.TemporaryDirectory() as temp:
    program_data = Path(temp)
    reported_dir = program_data / "LinkVideo.Helper" / "Helper"
    reported_dir.mkdir(parents=True)
    key_path = reported_dir / "linkvideo-helper-e4285d115c7c.json"
    key_path.write_text(json.dumps({
        "type": "service_account",
        "project_id": "linkvideo-helper",
        "private_key_id": "test-key",
        "private_key": "-----BEGIN PRIVATE KEY-----\nTEST\n-----END PRIVATE KEY-----\n",
        "client_email": "linkvideo-vpn-sync@linkvideo-helper.iam.gserviceaccount.com",
        "token_uri": "https://oauth2.googleapis.com/token",
    }), encoding="utf-8")

    old_program_data = os.environ.get("PROGRAMDATA")
    old_file = os.environ.pop("LINKVIDEO_SHEETS_SERVICE_ACCOUNT_FILE", None)
    old_json = os.environ.pop("LINKVIDEO_SHEETS_SERVICE_ACCOUNT_JSON", None)
    try:
        os.environ["PROGRAMDATA"] = str(program_data)
        found = discover_service_account_file(None)
        assert found == key_path, (found, key_path)

        install_google_key_discovery()
        backend = GoogleSheetsBackend.from_settings(None)
        assert backend is not None
        assert Path(str(getattr(backend, "source_path", ""))) == key_path
        assert backend.service_account_info.get("client_email") == "linkvideo-vpn-sync@linkvideo-helper.iam.gserviceaccount.com"
    finally:
        if old_program_data is None:
            os.environ.pop("PROGRAMDATA", None)
        else:
            os.environ["PROGRAMDATA"] = old_program_data
        if old_file is not None:
            os.environ["LINKVIDEO_SHEETS_SERVICE_ACCOUNT_FILE"] = old_file
        if old_json is not None:
            os.environ["LINKVIDEO_SHEETS_SERVICE_ACCOUNT_JSON"] = old_json


ui_text = (root / "linkvideo_vpn_helper/ui/nat_counter_integration.py").read_text(encoding="utf-8")
assert "NAT-трафик" in ui_text
assert "Пакеты" in ui_text
assert "Включён" in ui_text and "Отключён" in ui_text
assert "Конфликт" in ui_text

agent_text = (root / "scripts/vpn_sync_agent.py").read_text(encoding="utf-8")
assert "install_google_key_discovery" in agent_text
assert "GoogleSheetsBackend.from_settings(None)" in agent_text

print("CORE TESTS 3.0.8 GOOGLE KEY DISCOVERY + PORT ROWS OK")
