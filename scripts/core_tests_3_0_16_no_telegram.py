from __future__ import annotations

"""3.0.16 desktop RC must not bundle or start experimental Telegram bot."""

from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
services=ROOT/"linkvideo_vpn_helper/services"
ui=ROOT/"linkvideo_vpn_helper/ui"
assert not list(services.glob("telegram_*.py")), "Bot services must not ship"
assert not list(ui.glob("telegram_*.py")), "Bot settings UI must not ship"
bot_tests = [p for p in ROOT.glob("scripts/core_tests_*telegram*.py")
             if p.name != Path(__file__).name]
assert not bot_tests, "Bot tests not included"
assert not (ROOT/"docs/telegram_camera_agent.md").exists()
app=(ROOT/"linkvideo_vpn_helper/app.py").read_text(encoding="utf-8")
settings=(ui/"pages/settings_page.py").read_text(encoding="utf-8")
installer=(ROOT/"installer_next/backend_windows.go").read_text(encoding="utf-8")
assert "--telegram-worker" not in app
assert "--telegram-admin-op" not in app
assert "Telegram-бот" not in settings
assert "restartConfiguredTelegramBot" not in installer
assert "removeLegacyHelperBotTask" in installer, "Retire any leftover scheduled task from prior RC"
print("CORE TESTS 3.0.16 NO TELEGRAM RUNTIME OR UI OK")
