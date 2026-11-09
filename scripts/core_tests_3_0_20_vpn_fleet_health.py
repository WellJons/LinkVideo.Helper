from __future__ import annotations

"""VPN fleet health monitoring is read-only and never exports PPP secrets."""
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
source = ROOT / "scripts" / "vpn_fleet_health_agent.py"
spec = importlib.util.spec_from_file_location("vpn_fleet_health_agent", source)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)

assert "Пароль" not in module.HEADER
assert "RouterOS snapshot" not in module.HEADER
assert len(module.HEADER) == 14

with TemporaryDirectory() as root:
    file = Path(root) / "config.json"
    marker = "SENTINEL_SECRET_DO_NOT_EXPORT"
    file.write_text(json.dumps({
        "routeros_username": "health-readonly",
        "routeros_password": marker,
        "servers": ["vpn01.linkvideo.ru", "vpn01.linkvideo.ru", "vpn02.linkvideo.ru"],
        "interval_seconds": 5,
    }), encoding="utf-8")
    config = module.load_config(file)
    assert config["servers"] == ["vpn01.linkvideo.ru", "vpn02.linkvideo.ru"]
    assert config["interval_seconds"] == 60
    assert config["routeros_password"] == marker

    fake_stat = SimpleNamespace(
        clients_online=400, clients_total=500, ports_active=1100,
        ports_total=1450, cpu_load=57, memory_usage_percent=26,
    )
    fake_auto = SimpleNamespace(
        state_text="Работает", version="2.0.0",
        activity_run_count=10, restore_run_count=15, aging_run_count=2,
    )
    class VPN:
        def analyze_server_quick(self, host, creds):
            if host == "vpn02.linkvideo.ru":
                raise ConnectionError("Secret " + marker)
            return fake_stat
    class Automation:
        def get_status(self, host, creds):
            return fake_auto

    creds = SimpleNamespace(username="health-readonly", password=marker)
    rows = module.collect_fleet(config["servers"], creds, vpn=VPN(), automation=Automation())
    assert len(rows) == 2
    assert rows[0][0] == "vpn01.linkvideo.ru" and rows[0][3] == 400
    assert rows[0][9] == "Работает" and rows[0][10] == "2.0.0"
    assert rows[1][2] == "Ошибка: ConnectionError"
    assert marker not in json.dumps(rows, ensure_ascii=False)

    class FakeBackend:
        spreadsheet_id = "test-spreadsheet"
        def __init__(self):
            self.calls = []
        def _request(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            if method == "GET":
                return {"sheets": [{"properties": {"title": "LV История"}}]}
            return {}

    backend = FakeBackend()
    module.publish_health(backend, rows)
    assert [entry[0] for entry in backend.calls] == ["GET", "POST", "POST"]
    assert backend.calls[1][2]["payload"]["requests"][0]["addSheet"]["properties"]["title"] == "LV Мониторинг"
    values = backend.calls[2][2]["payload"]["data"][0]["values"]
    assert values[0] == list(module.HEADER)
    assert values[1][:3] == rows[0][:3]
    assert len(values) == 100
    assert marker not in json.dumps(backend.calls, ensure_ascii=False)
    assert "Пароль" not in json.dumps(values, ensure_ascii=False)

powershell = (ROOT / "scripts/install_windows_vpn_fleet_monitor.ps1").read_text(encoding="utf-8")
for required in (
    "New-ScheduledTaskTrigger -AtStartup", "-UserId 'SYSTEM'",
    "Register-ScheduledTask", "Read-Host 'RouterOS password' -AsSecureString",
    "icacls.exe", "--once --dry-run",
    "Copy-Item -LiteralPath $agent -Destination $protectedAgent -Force",
    "New-ScheduledTaskAction -Execute $python -Argument $argument -WorkingDirectory $protectedSource",
    "if (-not $trustedPython)",
):
    assert required in powershell, required
assert "RouterOS password is empty" in powershell
assert "--password" not in powershell

print("CORE TESTS 3.0.20 READ-ONLY FLEET MONITOR AND SAFE SHEETS EXPORT OK")
