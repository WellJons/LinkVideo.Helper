from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
brand = (ROOT / "linkvideo_vpn_helper/brand_theme.py").read_text(encoding="utf-8")
app = (ROOT / "linkvideo_vpn_helper/app.py").read_text(encoding="utf-8")

# LinkVideo.Monitor visual language: neutral workspace + graphite + orange.
for token in ("#F4F5F7", "#FFFFFF", "#20252B", "#69727C", "#FFAD19", "#EB9600"):
    assert token in brand, token
assert '"name": "LinkVideo"' in brand
assert 'THEMES.get("linkvideo_2026")' in brand

# Clean installations default to LinkVideo while an explicit old dark theme is preserved.
assert 'else "linkvideo_2026"' in app
assert 'install_linkvideo_brand_theme()' in app
assert 'settings.value("ui/theme_v2", "linkvideo_2026"' in app

print("CORE TESTS 3.0.8 LINKVIDEO MONITOR THEME OK")
