from pathlib import Path
import ast

root = Path(__file__).resolve().parents[1]
vpn = (root / "linkvideo_vpn_helper/services/vpn_service.py").read_text(encoding="utf-8")
search = (root / "linkvideo_vpn_helper/services/search_service.py").read_text(encoding="utf-8")
ui = (root / "linkvideo_vpn_helper/ui/pages/search_manage_page.py").read_text(encoding="utf-8")
ver = (root / "linkvideo_vpn_helper/version.py").read_text(encoding="utf-8")

assert 'APP_VERSION = "3.0.' in ver
assert 'class PortConflict' in vpn
assert 'port_conflicts: dict[int, list[PortConflict]]' in vpn
assert 'def inspect_port_conflicts' in vpn
assert 'include_port_conflicts: bool = False' in vpn
assert 'protocol and protocol != "tcp"' in vpn
assert 'chain and chain != "dstnat"' in vpn
assert 'include_port_conflicts=True' in search
assert 'Конфликт внешних NAT-портов' in ui
assert '⚠ Порт {port}' in ui
assert 'Конфликтов:' in ui
ast.parse(vpn)
ast.parse(search)
ast.parse(ui)
print('CORE TESTS 3.0.7 PORT CONFLICT GUARD OK')
