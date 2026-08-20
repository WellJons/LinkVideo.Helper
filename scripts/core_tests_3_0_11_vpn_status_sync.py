from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from linkvideo_vpn_helper.ui import vpn_servers_status_ui as status_ui


class _Item:
    def __init__(self, text: str):
        self._text = text
        self.tooltip = ""

    def text(self) -> str:
        return self._text

    def setText(self, value: str) -> None:
        self._text = value

    def setToolTip(self, value: str) -> None:
        self.tooltip = value


class _Table:
    def __init__(self):
        self._cells = {(0, 0): _Item("vpn1.linkvideo.ru"), (0, 7): _Item("stale")}

    def rowCount(self) -> int:
        return 1

    def item(self, row: int, column: int):
        return self._cells.get((row, column))


class _Auto:
    state_text = "Работает · карантин включён"
    installed = True
    paused = False
    aging_enabled = True


class _Page:
    def __init__(self):
        self.table = _Table()
        self._automation_by_host = {"vpn1.linkvideo.ru": _Auto()}


def main() -> None:
    page = _Page()
    status_ui._refresh_automation_cells(page)

    status_item = page.table.item(0, 7)
    assert status_item.text() == "Работает · карантин включён"
    assert status_item.text().count("карантин включён") == 1
    assert "365+" in status_item.tooltip

    source = inspect.getsource(status_ui.install_vpn_servers_status_ui)
    assert "original_on_action = VPNServersPage._on_action" in source
    assert "VPNServersPage._on_action = patched_on_action" in source
    patched = source[source.index("def patched_on_action"):]
    assert "original_on_action(self, name, payload, error)" in patched
    assert "_refresh_automation_cells(self)" in patched

    print("CORE TESTS 3.0.11 VPN STATUS SYNC OK")


if __name__ == "__main__":
    main()
