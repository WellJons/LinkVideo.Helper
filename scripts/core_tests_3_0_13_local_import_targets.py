from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOTS = ("linkvideo_vpn_helper", "server/linkvideo_vpnsync")


def _module_exists(module: str) -> bool:
    rel = Path(*module.split("."))
    return (ROOT / f"{rel}.py").is_file() or (ROOT / rel / "__init__.py").is_file()


def main() -> int:
    missing: list[str] = []
    files: list[Path] = []
    files.extend((ROOT / "linkvideo_vpn_helper").rglob("*.py"))
    files.extend((ROOT / "server" / "linkvideo_vpnsync").rglob("*.py"))

    for path in sorted(files):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
            elif isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            for module in modules:
                if module.startswith("linkvideo_vpn_helper") and not _module_exists(module):
                    missing.append(f"{path.relative_to(ROOT)}:{getattr(node, 'lineno', '?')}: {module}")
                elif module.startswith("linkvideo_vpnsync"):
                    rel = Path("server", *module.split("."))
                    if not (ROOT / f"{rel}.py").is_file() and not (ROOT / rel / "__init__.py").is_file():
                        missing.append(f"{path.relative_to(ROOT)}:{getattr(node, 'lineno', '?')}: {module}")

    if missing:
        raise AssertionError("Missing local import target(s):\n" + "\n".join(missing))

    bridge = (ROOT / "linkvideo_vpn_helper/ui/vpn_automation_sheets_bridge.py").read_text(encoding="utf-8")
    assert "vpn_final_3_0_13_ux" not in bridge
    assert "Google Sheets is disaster-recovery only" in bridge
    print(f"LOCAL_IMPORT_TARGETS_OK files={len(files)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
