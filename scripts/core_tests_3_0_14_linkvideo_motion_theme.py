from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
read = lambda relative: (ROOT / relative).read_text(encoding="utf-8")
theme = read("linkvideo_vpn_helper/theme.py")
login = read("linkvideo_vpn_helper/ui/login_window.py")
settings = read("linkvideo_vpn_helper/ui/pages/settings_page.py")
stack = read("linkvideo_vpn_helper/ui/components_compat.py")
brand = read("linkvideo_vpn_helper/brand_theme.py")

for source in (theme, login, settings, stack, brand):
    ast.parse(source)

raw = next(
    node.value for node in ast.parse(theme).body
    if isinstance(node, ast.Assign)
    and any(isinstance(target, ast.Name) and target.id == "_RAW_THEMES" for target in node.targets)
)
palettes = ast.literal_eval(raw)
_, background, sidebar, panel, panel2, border, text, muted, accent, hover, danger = palettes["linkvideo_2026"]
assert background == "#F3F4F6" and sidebar == "#F8F9FA"
assert panel == "#FFFFFF" and panel2 == "#ECEFF2"
assert accent != "#3478F6" and accent != "#FFAD19"
assert all(accent != color for color in (background, sidebar, panel, panel2))
assert 'theme["name"] = "LinkVideo"' in brand


def luminance(color: str) -> float:
    channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4 for channel in channels]
    return sum(x * weight for x, weight in zip(linear, (0.2126, 0.7152, 0.0722)))


assert 1.05 / (luminance(accent) + 0.05) >= 4.5, "Orange primary needs readable white text"
assert 1.05 / (luminance(hover) + 0.05) >= 4.5, "Orange hover needs readable white text"

assert 'self._shake_on_invalid_password()' in login
assert 'credential_rejected = message.startswith("Логин или пароль")' in login
assert 'ui/animations_enabled' in login and 'ui/animations_enabled' in settings and 'ui/animations_enabled' in stack
assert "QCheckBox(\"Плавные анимации интерфейса\")" in settings
assert "QPropertyAnimation(self, b\"pos\", self)" in login
assert "QPropertyAnimation(effect, b\"opacity\", new)" in stack
assert "new.setGraphicsEffect(None)" in stack
assert 'field.setProperty("error", True)' in login
print("CORE TESTS LINKVIDEO ORANGE/GRAY UI MOTION OK")
