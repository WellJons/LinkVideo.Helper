from __future__ import annotations

import importlib
import os
import pkgutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Import every shipped Python module on the same Windows runner used for the
# release build. This catches renamed/deleted modules, missing dependencies and
# import-time regressions that plain syntax compilation cannot see.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import linkvideo_vpn_helper


def main() -> None:
    names = sorted(
        module.name
        for module in pkgutil.walk_packages(
            linkvideo_vpn_helper.__path__,
            prefix="linkvideo_vpn_helper.",
        )
    )
    assert names, "no LinkVideo.Helper modules discovered"

    failures: list[str] = []
    for name in names:
        try:
            importlib.import_module(name)
        except Exception as exc:
            failures.append(f"{name}: {type(exc).__name__}: {exc}")

    if failures:
        raise AssertionError("Package import smoke failed:\n" + "\n".join(failures))
    print(f"CORE TESTS 3.0.11 IMPORT ALL OK — {len(names)} modules")


if __name__ == "__main__":
    main()
