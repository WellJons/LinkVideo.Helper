from __future__ import annotations

import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from linkvideo_vpn_helper.services import update_service
from linkvideo_vpn_helper.services import update_version_probe_compat as compat


source = (root / "linkvideo_vpn_helper/services/update_version_probe_compat.py").read_text(encoding="utf-8")
assert "LINKVIDEO_UPDATE_FILE" in source
assert "$args[0]" not in source
assert '"-Command",\n            script,\n        ]' in source

compat._INSTALLED = False
original = update_service._windows_product_version
compat.install_update_version_probe_compat()
assert update_service._windows_product_version is compat._windows_product_version
assert update_service._windows_product_version is not original

# Hosted release CI runs on Windows. Probe a real PE with a path supplied only
# through the environment variable, exercising the exact PowerShell 5.1-safe
# invocation shape that failed in the field.
if os.name == "nt":
    version = compat._windows_product_version(Path(sys.executable))
    assert version and any(ch.isdigit() for ch in version), version

print("CORE TESTS 3.0.10 UPDATE VERSION PROBE OK")
