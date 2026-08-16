from pathlib import Path
import re
ROOT=Path(__file__).resolve().parents[1]
ver=(ROOT/'linkvideo_vpn_helper/version.py').read_text(encoding='utf-8')
assert re.search(r'^APP_VERSION\s*=\s*"\d+\.\d+\.\d+"\s*$', ver, re.MULTILINE)
theme=(ROOT/'linkvideo_vpn_helper/theme.py').read_text(encoding='utf-8')
for token in ['Розовое молочко','linkvideo_2026','Светлая LinkVideo','QCalendarWidget#CompactCalendar','QListWidget#SearchResultsList']:
    assert token in theme, token
search=(ROOT/'linkvideo_vpn_helper/ui/pages/search_manage_page.py').read_text(encoding='utf-8')
assert 'item.setSizeHint(QSize(0, 70))' in search
assert 'self.page_layout.addStretch(1)' not in search
assert 'self.workspace.setMinimumHeight(640)' in search
components=(ROOT/'linkvideo_vpn_helper/ui/components.py').read_text(encoding='utf-8')
compat=(ROOT/'linkvideo_vpn_helper/ui/components_compat.py').read_text(encoding='utf-8')
combined=components+'\n'+compat
assert 'QLocale("ru_RU")' in combined
assert 'QTime(t.hour(), t.minute(), 0)' in combined
assert 'class AnimatedStack' in combined
assert 'class Toast' in combined
for rel in ['archive_download_page.py','archive_diagnostics_page.py']:
    text=(ROOT/'linkvideo_vpn_helper/ui/pages'/rel).read_text(encoding='utf-8')
    assert 'now_dt = now_dt.addSecs(-now_dt.time().second())' in text
print('CORE TESTS 3.0.1 UI POLISH OK')
