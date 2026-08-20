from __future__ import annotations

import compileall
import os
import re
import subprocess
import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
PACKAGE = ROOT / "linkvideo_vpn_helper"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from linkvideo_vpn_helper.version import APP_NAME, APP_PUBLISHER, APP_VERSION


_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:\.\d+)?$")
_PINNED_VERSION_RE = re.compile(r"APP_VERSION\s*=\s*[\"']\d+\.\d+\.\d+(?:\.\d+)?[\"']")


def _natural_key(path: Path):
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", path.name)]


def _collect_tests() -> list[Path]:
    tests = [SCRIPTS / "core_tests.py"]
    tests.extend(sorted(SCRIPTS.glob("core_tests_*.py"), key=_natural_key))
    return [path for path in tests if path.exists()]


def _check_version_contract() -> None:
    if APP_NAME != "LinkVideo.Helper":
        raise SystemExit(f"Unexpected APP_NAME: {APP_NAME!r}")
    if APP_PUBLISHER != "LinkVideo":
        raise SystemExit(f"Unexpected APP_PUBLISHER: {APP_PUBLISHER!r}")
    if not _VERSION_RE.fullmatch(APP_VERSION):
        raise SystemExit(f"APP_VERSION must be numeric x.y.z[.w], got: {APP_VERSION!r}")

    notes = ROOT / f"RELEASE_{APP_VERSION}_RU.txt"
    if not notes.exists() or not notes.read_text(encoding="utf-8").strip():
        raise SystemExit(f"Release notes missing or empty: {notes.name}")

    installer = (ROOT / "installer.iss").read_text(encoding="utf-8")
    if f'#define MyAppVersion "{APP_VERSION}"' not in installer:
        raise SystemExit("installer.iss version is not synchronized with APP_VERSION")

    obsolete_runtime = PACKAGE / "services" / "vpn_quarantine_runtime_fix.py"
    if obsolete_runtime.exists():
        raise SystemExit("Obsolete vpn_quarantine_runtime_fix.py must not be shipped")

    offenders: list[str] = []
    for path in _collect_tests():
        text = path.read_text(encoding="utf-8")
        if _PINNED_VERSION_RE.search(text):
            offenders.append(path.name)
    if offenders:
        raise SystemExit(
            "Concrete APP_VERSION pin found in regression test(s): " + ", ".join(offenders)
        )


def _compile_python() -> None:
    # SyntaxWarning caught a real invalid escape during the 3.0.11 audit. Treat
    # such warnings as release failures so they cannot become background noise.
    with warnings.catch_warnings():
        warnings.simplefilter("error", SyntaxWarning)
        try:
            ok_package = compileall.compile_dir(str(PACKAGE), quiet=1, force=True)
            ok_scripts = compileall.compile_dir(str(SCRIPTS), quiet=1, force=True)
        except SyntaxWarning as exc:
            raise SystemExit(f"Python SyntaxWarning preflight failed: {exc}") from exc
    if not (ok_package and ok_scripts):
        raise SystemExit("Python compile preflight failed")


def _run_full_audit() -> None:
    print("\n=== full_release_audit.py ===", flush=True)
    try:
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "full_release_audit.py")],
            cwd=str(ROOT),
            check=False,
            timeout=90,
        )
    except subprocess.TimeoutExpired as exc:
        raise SystemExit("Full source audit exceeded 90 seconds") from exc
    if result.returncode != 0:
        raise SystemExit(f"Full source audit failed (exit {result.returncode})")


def _run_regressions() -> None:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + existing if existing else "")

    tests = _collect_tests()
    if not tests:
        raise SystemExit("No core regression tests found")

    print(f"Running {len(tests)} regression test files...")
    for path in tests:
        print(f"\n=== {path.name} ===", flush=True)
        try:
            result = subprocess.run(
                [sys.executable, str(path)],
                cwd=str(ROOT),
                env=env,
                check=False,
                timeout=180,
            )
        except subprocess.TimeoutExpired as exc:
            raise SystemExit(f"Regression timed out after 180s: {path.name}") from exc
        if result.returncode != 0:
            raise SystemExit(f"Regression failed: {path.name} (exit {result.returncode})")


def main() -> None:
    _check_version_contract()
    _compile_python()
    _run_full_audit()
    _run_regressions()
    print(f"\nRELEASE PREFLIGHT OK — {APP_NAME} {APP_VERSION}")


if __name__ == "__main__":
    main()
