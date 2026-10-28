from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
brand = (ROOT / "linkvideo_vpn_helper/brand_theme.py").read_text(encoding="utf-8")
theme = (ROOT / "linkvideo_vpn_helper/theme.py").read_text(encoding="utf-8")
app = (ROOT / "linkvideo_vpn_helper/app.py").read_text(encoding="utf-8")

# Keep the palette definitions in theme.py. Avoid the old broad
# yellow/cream Monitor override while allowing restrained orange accents.
assert 'theme["name"] = "LinkVideo"' in brand
assert 'THEMES.get("linkvideo_2026")' in brand
for forbidden in ("#FFAD19", "#EB9600", "#FFF9EC"):
    assert forbidden not in brand, f"broad amber runtime override returned: {forbidden}"

# The stable linkvideo_2026 key uses neutral gray surfaces and orange accents.
for token in ("#F3F4F6", "#F8F9FA", "#FFFFFF", "#ECEFF2", "#DCE1E6", "#F07119", "#D95C0A"):
    assert token in theme, token

# Clean installations default to LinkVideo while an explicit old dark theme is preserved.
assert 'else "linkvideo_2026"' in app
assert 'install_linkvideo_brand_theme()' in app
assert 'settings.value("ui/theme_v2", "linkvideo_2026"' in app

print("CORE TESTS 3.0.8 CLEAN LINKVIDEO THEME OK")
