from pathlib import Path
import re
ROOT=Path(__file__).resolve().parents[1]
ver=(ROOT/'linkvideo_vpn_helper/version.py').read_text(encoding='utf-8')
assert re.search(r'^APP_VERSION\s*=\s*"\d+\.\d+\.\d+"\s*$', ver, re.MULTILINE)
assert 'APP_NAME = "LinkVideo.Helper"' in ver
theme=(ROOT/'linkvideo_vpn_helper/theme.py').read_text(encoding='utf-8')
for token in ['Светлая LinkVideo','QPushButton[nav="true"]','QTableWidget','SegmentHost']:
    assert token in theme, token
create=(ROOT/'linkvideo_vpn_helper/ui/pages/create_client_page.py').read_text(encoding='utf-8')
assert 'self.workspace = QBoxLayout' in create
assert 'Порты' in create and 'Учётки' in create
assert (ROOT/'GOOGLE_SHEETS_SYNC_SCHEMA_3.0.md').exists()
print('CORE TESTS 3.0 UI FOUNDATION OK')
