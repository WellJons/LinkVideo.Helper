from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
if str(root) not in sys.path:
    sys.path.insert(0, str(root))


with tempfile.TemporaryDirectory() as temp:
    old_local = os.environ.get("LOCALAPPDATA")
    app_logging = None
    try:
        os.environ["LOCALAPPDATA"] = temp
        from linkvideo_vpn_helper.services import app_logging

        app_logging.event("TEST", "Проверка", "vpn06.linkvideo.ru password=SuperSecret access_token=abc")
        text = app_logging.read_recent(100)
        assert "TEST | Проверка" in text
        assert "vpn06.linkvideo.ru" in text
        assert "SuperSecret" not in text
        assert "access_token=abc" not in text
        assert "***" in text
        assert app_logging.log_file().is_file()
        app_logging.clear_logs()
        assert "Журнал очищен" in app_logging.read_recent(100)
    finally:
        if app_logging is not None:
            try:
                app_logging.shutdown_runtime_logging()
            except Exception:
                pass
        if old_local is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = old_local


port_ui = (root / "linkvideo_vpn_helper/ui/nat_counter_integration.py").read_text(encoding="utf-8")
assert 'item.setText("")' in port_ui, "Old QListWidget text must be cleared under custom port rows"
assert 'item.setToolTip("")' in port_ui, "Port rows must not show hover tooltips"
assert "setToolTip(note)" not in port_ui
assert "NAT-трафик" in port_ui and "Пакеты" in port_ui

log_ui = (root / "linkvideo_vpn_helper/ui/runtime_log_integration.py").read_text(encoding="utf-8")
for marker in ("Журнал работы", "Обновить", "Скопировать", "Открыть папку", "Очистить"):
    assert marker in log_ui

app_text = (root / "linkvideo_vpn_helper/app.py").read_text(encoding="utf-8")
assert "install_runtime_logging" in app_text
assert "install_runtime_log_ui" in app_text
assert "aboutToQuit.connect(shutdown_runtime_logging)" in app_text

sheets = (root / "linkvideo_vpn_helper/ui/vpn_sheets_sync_integration.py").read_text(encoding="utf-8")
assert "last_failures" in sheets
assert "Сверка частичная" in sheets
assert "Ошибка сверки сервера" in sheets

print("CORE TESTS 3.0.8 RUNTIME LOGGING + CLEAN PORT ROWS OK")
