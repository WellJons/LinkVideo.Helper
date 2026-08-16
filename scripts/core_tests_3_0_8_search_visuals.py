from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
fixes = (ROOT / "linkvideo_vpn_helper/ui/search_visual_fixes.py").read_text(encoding="utf-8")
app = (ROOT / "linkvideo_vpn_helper/app.py").read_text(encoding="utf-8")

# Successful search results must explicitly close TaskStatus' separate floating
# BusyDialog before the original handler hides the inline status widget.
assert "original_on_search = SearchManagePage._on_search" in fixes
assert 'getattr(report, "matches", None)' in fixes
assert 'getattr(task, "_close_busy_dialog", None)' in fixes
assert "close_busy()" in fixes

# Per-port traffic was deliberately removed after real RouterOS servers failed
# to provide dependable port-level connection data. Search visual fixes must no
# longer import or depend on that feature.
assert "port_traffic_inline" not in fixes
assert "install_inline_port_traffic" not in app
assert "port_traffic_service" not in app

print("CORE TESTS 3.0.8 SEARCH OVERLAY VISUALS OK")
