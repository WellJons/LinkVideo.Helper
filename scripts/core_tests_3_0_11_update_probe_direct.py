from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from linkvideo_vpn_helper.services import update_service


def main() -> None:
    source = (ROOT / "linkvideo_vpn_helper" / "services" / "update_service.py").read_text(encoding="utf-8")
    assert "LINKVIDEO_UPDATE_FILE" in source
    assert 'script,\n            str(path),' not in source

    if os.name == "nt":
        # Reproduce the shape that broke 2.0.2: non-ASCII/space path and a
        # downloaded EXE carrying the .download suffix.
        with tempfile.TemporaryDirectory(prefix="lv probe ") as td:
            folder = Path(td) / "Тест пользователя"
            folder.mkdir(parents=True)
            target = folder / "LinkVideo.Helper_Setup_Update.exe.download"
            shutil.copyfile(sys.executable, target)
            version = update_service._windows_product_version(target)
            assert version.strip(), "ProductVersion probe returned an empty value"

    print("CORE TESTS 3.0.11 UPDATE PROBE DIRECT OK")


if __name__ == "__main__":
    main()
