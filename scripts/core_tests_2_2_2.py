from pathlib import Path

root = Path(__file__).resolve().parents[1]
search = (root / "linkvideo_vpn_helper/ui/pages/search_manage_page.py").read_text(encoding="utf-8")
inactive = (root / "linkvideo_vpn_helper/ui/pages/inactive_clients_page.py").read_text(encoding="utf-8")
servers = (root / "linkvideo_vpn_helper/ui/pages/vpn_servers_page.py").read_text(encoding="utf-8")
components = (root / "linkvideo_vpn_helper/ui/components.py").read_text(encoding="utf-8")
version = (root / "linkvideo_vpn_helper/version.py").read_text(encoding="utf-8")

assert 'APP_VERSION = "3.0.7"' in version
assert 'self.detail_panel = Card()' in search
assert 'self._animate_search_panel(compact=True)' in search
assert 'self.client_dialog.show()' not in search
assert 'self.selected_port_pill = StatusPill' in search
assert 'self.port_action_label' not in search
assert 'self.port_toggle_label' not in search
assert 'SegmentedControl([' in inactive
assert 'QComboBox' not in inactive
assert 'Выгрузить этот сервер' in servers
assert 'def _backup_one(self, host: str):' in servers
assert 'self.progress.setMinimumHeight(10)' in components
print('CORE TESTS 3.0.1 NAV/PORT/SORT/BACKUP OK')
