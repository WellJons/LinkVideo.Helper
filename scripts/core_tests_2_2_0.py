from __future__ import annotations

from pathlib import Path
import py_compile
import re

ROOT = Path(__file__).resolve().parents[1]

# Compile all UI modules touched by the 2.2 redesign.
for rel in (
    'linkvideo_vpn_helper/ui/components.py',
    'linkvideo_vpn_helper/ui/main_window.py',
    'linkvideo_vpn_helper/ui/pages/create_client_page.py',
    'linkvideo_vpn_helper/ui/pages/search_manage_page.py',
    'linkvideo_vpn_helper/ui/pages/archive_download_page.py',
    'linkvideo_vpn_helper/ui/pages/archive_diagnostics_page.py',
    'linkvideo_vpn_helper/ui/pages/inactive_clients_page.py',
    'linkvideo_vpn_helper/ui/pages/vpn_servers_page.py',
    'linkvideo_vpn_helper/ui/pages/settings_page.py',
):
    py_compile.compile(str(ROOT / rel), doraise=True)

components = (ROOT / 'linkvideo_vpn_helper/ui/components.py').read_text(encoding='utf-8')
main = (ROOT / 'linkvideo_vpn_helper/ui/main_window.py').read_text(encoding='utf-8')
search = (ROOT / 'linkvideo_vpn_helper/ui/pages/search_manage_page.py').read_text(encoding='utf-8')
inactive = (ROOT / 'linkvideo_vpn_helper/ui/pages/inactive_clients_page.py').read_text(encoding='utf-8')
vpn = (ROOT / 'linkvideo_vpn_helper/ui/pages/vpn_servers_page.py').read_text(encoding='utf-8')
theme = (ROOT / 'linkvideo_vpn_helper/theme.py').read_text(encoding='utf-8')

assert 'def build_page_scaffold' in components
for rel in (
    'create_client_page.py', 'search_manage_page.py', 'archive_download_page.py',
    'archive_diagnostics_page.py', 'inactive_clients_page.py', 'settings_page.py',
):
    txt = (ROOT / 'linkvideo_vpn_helper/ui/pages' / rel).read_text(encoding='utf-8')
    assert 'build_page_scaffold' in txt, rel

# VPN infrastructure navigation is deliberately at the bottom of the work list.
assert main.index('(\"inactive\", \"⌛\", \"VPN-клиенты\"') < main.index('(\"vpn_servers\", \"▦\", \"VPN-серверы\"')

# Search/client page must update itself after mutations and surface newly created ports.
for token in (
    '_live_timer', 'onActivated', 'onDeactivated', '_silent_refresh', '_sync_result_record',
    '_recent_new_ports', 'Новые порты:', 'Копировать новые порты', 'QTimer.singleShot(700, self._silent_refresh)',
):
    assert token in search, token

# Sorting wording must be unambiguous to operators.
assert '\"Старые\"' in inactive
assert '\"Новые\"' in inactive
assert 'Без достоверной даты — всегда в конце' in inactive

# VPN dashboard no longer packs all controls and lifecycle text into an 11-column table.
assert 'ServerTableWidget(0, 8)' in vpn
assert 'Действия со всеми серверами' in vpn
assert '_render_server_detail' in vpn
assert 'Памятка состояний VPN-клиентов' in vpn
assert 'Остановить LV на всех' in vpn and 'Запустить LV на всех' in vpn

# Tables have a coherent theme instead of native Windows defaults.
assert 'QTableWidget {' in theme and 'QHeaderView::section' in theme

print('CORE TESTS 2.2.0 UI/AUTO-REFRESH OK')


# Startup regression: app imports stable metadata constants from version.py.
# Do not pin this legacy regression test to a specific release number, otherwise
# every valid version bump breaks the release build before PyInstaller starts.
version_text = (ROOT / "linkvideo_vpn_helper" / "version.py").read_text(encoding="utf-8")
for required in ('APP_NAME = "LinkVideo.Helper"', 'APP_PUBLISHER = "LinkVideo"'):
    assert required in version_text, f"startup version constant missing: {required}"
match = re.search(r'^APP_VERSION\s*=\s*"(\d+(?:\.\d+){1,3})"\s*$', version_text, re.MULTILINE)
assert match, "startup APP_VERSION constant missing or invalid"
app_text = (ROOT / "linkvideo_vpn_helper" / "app.py").read_text(encoding="utf-8")
assert "from linkvideo_vpn_helper.version import APP_NAME, APP_VERSION" in app_text
