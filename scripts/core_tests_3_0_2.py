from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
theme = (ROOT / "linkvideo_vpn_helper" / "theme.py").read_text(encoding="utf-8")
create = (ROOT / "linkvideo_vpn_helper" / "ui" / "pages" / "create_client_page.py").read_text(encoding="utf-8")
search = (ROOT / "linkvideo_vpn_helper" / "ui" / "pages" / "search_manage_page.py").read_text(encoding="utf-8")
components = (ROOT / "linkvideo_vpn_helper" / "ui" / "components.py").read_text(encoding="utf-8")
version = (ROOT / "linkvideo_vpn_helper" / "version.py").read_text(encoding="utf-8")

assert re.search(r'^APP_VERSION\s*=\s*"\d+\.\d+\.\d+"\s*$', version, re.MULTILINE)
assert '"peach_light": (' not in theme
assert '"forest_green": (' not in theme
for name in ("Розовое молочко", "Светлая LinkVideo", "Лавандовая", "Тёмно-синяя", "Полуночная", "Тёмная вишня", "Графитовая"):
    assert name in theme, name
assert 'def _apply_qt_palette' in theme
assert 'QPalette.ColorRole.Highlight' in theme
assert 'params = Card(kind="accent")' in create
assert 'ClientWorkspaceCard' in search
assert 'QPalette.ColorRole.Highlight' in components
print("CORE TESTS 3.0.2 THEME/POLISH OK")
