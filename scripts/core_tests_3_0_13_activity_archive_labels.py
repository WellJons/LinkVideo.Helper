from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "linkvideo_vpn_helper/ui/pages/vpn_activity_page.py"
text = path.read_text(encoding="utf-8")
ast.parse(text, filename=str(path))
assert '"archive": "Восстановление"' in text
assert '"archive.restore.preflight": "Проверка перед восстановлением"' in text
assert '"archive.restore": "Восстановление VPN-клиента"' in text
assert 'self.source_filter.addItem("Восстановление", "archive")' in text
print("ACTIVITY_ARCHIVE_LABELS_OK")
