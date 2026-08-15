from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
fixes = (ROOT / "linkvideo_vpn_helper/ui/search_visual_fixes.py").read_text(encoding="utf-8")
app = (ROOT / "linkvideo_vpn_helper/app.py").read_text(encoding="utf-8")
port_ui = (ROOT / "linkvideo_vpn_helper/ui/port_traffic_inline.py").read_text(encoding="utf-8")

# Successful search results must explicitly close TaskStatus' separate floating
# BusyDialog before the original handler hides the inline status widget.
assert "original_on_search = SearchManagePage._on_search" in fixes
assert 'getattr(report, "matches", None)' in fixes
assert 'getattr(task, "_close_busy_dialog", None)' in fixes
assert "close_busy()" in fixes

# Inline traffic replaces the whole visual row. The underlying QListWidgetItem
# display text therefore has to be cleared or Windows paints both labels.
assert "original_decorate = port_traffic_inline._decorate_port_rows" in fixes
assert "original_decorate(page)" in fixes
assert 'item.setText("")' in fixes
assert "setItemWidget" in port_ui

# Install after inline traffic so the wrapper sees the final render extension.
assert app.index("install_inline_port_traffic()") < app.index("install_search_visual_fixes()")

print("CORE TESTS 3.0.8 SEARCH OVERLAY / PORT ROW VISUALS OK")
