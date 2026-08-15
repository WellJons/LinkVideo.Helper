from __future__ import annotations

from pathlib import Path
import py_compile

ROOT = Path(__file__).resolve().parents[1]
files = [
    'linkvideo_vpn_helper/ui/components.py',
    'linkvideo_vpn_helper/ui/pages/create_client_page.py',
    'linkvideo_vpn_helper/ui/pages/search_manage_page.py',
    'linkvideo_vpn_helper/ui/pages/inactive_clients_page.py',
    'linkvideo_vpn_helper/ui/pages/vpn_servers_page.py',
]
for rel in files:
    py_compile.compile(str(ROOT / rel), doraise=True)

create = (ROOT / files[1]).read_text(encoding='utf-8')
search = (ROOT / files[2]).read_text(encoding='utf-8')
inactive = (ROOT / files[3]).read_text(encoding='utf-8')
vpn = (ROOT / files[4]).read_text(encoding='utf-8')
components = (ROOT / files[0]).read_text(encoding='utf-8')
version = (ROOT / 'linkvideo_vpn_helper/version.py').read_text(encoding='utf-8')

assert 'APP_VERSION = "3.0.7"' in version
assert 'self.counters_row.setDirection(QBoxLayout.Direction.LeftToRight)' in create
assert 'Клик — открыть карточку' in search
assert 'self.detail_panel.show()' in search
assert 'Копировать новые порты' in search
assert '"Старые"' in inactive and '"Новые"' in inactive
assert 'SegmentedControl' in inactive
assert 'class ServerTableWidget(QTableWidget)' in vpn
assert 'event.accept()' in vpn
assert 'self.table.setFixedHeight(360)' in vpn
assert 'self.progress.setTextVisible(False)' in components
assert 'progress_text or f"{value}%"' in components
print('CORE TESTS 3.0.1 LAYOUT/SCROLL COMPAT OK')
