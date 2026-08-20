from __future__ import annotations

"""Source-wide release audit for LinkVideo.Helper.

This is deliberately broader than feature regression tests. It walks the whole
checked-out repository, parses every runtime Python file, checks high-risk I/O
contracts, validates the updater/installer publication chain and reports risky
constructs that deserve review. Critical findings block release preflight.
"""

import ast
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "linkvideo_vpn_helper"
SCRIPTS = ROOT / "scripts"
RUNTIME_ROOTS = (PACKAGE,)

SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "build",
    "dist",
    "installer_output",
    "release_upload",
    "release_payload",
    "patch_output",
    "__pycache__",
}


class Audit:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.stats: Counter[str] = Counter()

    def error(self, text: str) -> None:
        self.errors.append(text)

    def warn(self, text: str) -> None:
        self.warnings.append(text)


def _files(suffixes: set[str]):
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in suffixes:
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        yield path


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _call_name(node: ast.Call) -> str:
    parts: list[str] = []
    value = node.func
    while isinstance(value, ast.Attribute):
        parts.append(value.attr)
        value = value.value
    if isinstance(value, ast.Name):
        parts.append(value.id)
    return ".".join(reversed(parts))


def _has_timeout(call: ast.Call) -> bool:
    if any(keyword.arg == "timeout" for keyword in call.keywords):
        return True
    name = _call_name(call)
    # urllib.request.urlopen(url, data=None, timeout=...) accepts timeout as the
    # third positional parameter. Our runtime consistently uses keyword/second
    # parameter only for wrappers; allow any explicit extra positional value.
    if name.endswith("urlopen") and len(call.args) >= 2:
        return True
    return False


def audit_python(audit: Audit) -> None:
    runtime_files = list(_files({".py"}))
    audit.stats["python_files"] = len(runtime_files)
    broad_pass: list[str] = []
    todo_hits: list[str] = []

    for path in runtime_files:
        text = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError as exc:
            audit.error(f"Python syntax: {_rel(path)}:{exc.lineno}: {exc.msg}")
            continue

        is_runtime = PACKAGE in path.parents or path == PACKAGE
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and is_runtime:
                name = _call_name(node)
                if name.endswith("urlopen") and not _has_timeout(node):
                    audit.error(f"Network urlopen without timeout: {_rel(path)}:{node.lineno}")
                if name in {
                    "requests.get",
                    "requests.post",
                    "requests.put",
                    "requests.delete",
                    "requests.patch",
                    "requests.request",
                    "session.get",
                    "session.post",
                    "session.put",
                    "session.delete",
                    "session.patch",
                    "session.request",
                } and not _has_timeout(node):
                    audit.error(f"HTTP request without timeout: {_rel(path)}:{node.lineno} ({name})")

            if isinstance(node, ast.ExceptHandler) and is_runtime:
                if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                    broad_pass.append(f"{_rel(path)}:{node.lineno}")

        for lineno, line in enumerate(text.splitlines(), 1):
            if re.search(r"\b(?:TODO|FIXME|XXX)\b", line, re.I):
                todo_hits.append(f"{_rel(path)}:{lineno}")

    if broad_pass:
        audit.warn(
            f"Broad exception/pass sites: {len(broad_pass)} (review sample: {', '.join(broad_pass[:8])})"
        )
    if todo_hits:
        audit.warn(f"TODO/FIXME markers: {len(todo_hits)} (sample: {', '.join(todo_hits[:8])})")


def audit_release_chain(audit: Audit) -> None:
    required = {
        "app": ROOT / "linkvideo_vpn_helper" / "app.py",
        "version": ROOT / "linkvideo_vpn_helper" / "version.py",
        "update": ROOT / "linkvideo_vpn_helper" / "services" / "update_service.py",
        "archive_methods": ROOT / "linkvideo_vpn_helper" / "services" / "archive_download_methods.py",
        "spec": ROOT / "LinkVideo.Helper.spec",
        "build_next": ROOT / "scripts" / "build_next_installer.ps1",
        "windows_workflow": ROOT / ".github" / "workflows" / "windows-build.yml",
        "publish_workflow": ROOT / ".github" / "workflows" / "publish-public-update.yml",
    }
    for name, path in required.items():
        if not path.is_file():
            audit.error(f"Required release file missing ({name}): {_rel(path)}")
    if audit.errors:
        return

    app = required["app"].read_text(encoding="utf-8")
    update = required["update"].read_text(encoding="utf-8")
    methods = required["archive_methods"].read_text(encoding="utf-8")
    spec = required["spec"].read_text(encoding="utf-8")
    build = required["build_next"].read_text(encoding="utf-8")
    workflow = required["windows_workflow"].read_text(encoding="utf-8")
    publish = required["publish_workflow"].read_text(encoding="utf-8")

    markers = [
        ("GitHub production manifest", "WellJons/LinkVideo.Helper.Updates/main/update-manifest.json" in update),
        ("SHA verification", "actual_hash != expected_hash" in update),
        ("ProductVersion verification", "_windows_product_version" in update),
        ("update probe compatibility installed", "install_update_version_probe_compat()" in app),
        ("archive methods installed", "install_archive_download_methods()" in app),
        ("FFmpeg LocalAppData download", "FFMPEG_DOWNLOAD_URL" in methods and "tools_dir()" in methods),
        ("FFmpeg absent from PyInstaller spec", "ffmpeg = root /" not in spec),
        ("payload explicitly rejects FFmpeg", "installer payload correctly excludes ffmpeg.exe" in build),
        ("authoritative Setup filename", "LinkVideo.Helper_Setup.exe" in build and "LinkVideo.Helper_Setup_Next.exe" not in build),
        ("draft release uses authoritative Setup", '$asset = "installer_next/output/LinkVideo.Helper_Setup.exe"' in workflow),
        ("public publisher downloads Setup", "--pattern '*Setup.exe'" in publish),
        ("public manifest writes SHA", '"sha256": setup_sha' in publish),
    ]
    for label, okay in markers:
        if not okay:
            audit.error(f"Release contract missing: {label}")

    if app.index("install_archive_download_methods()") > app.index("install_archive_download_ux()"):
        audit.error("Archive method integration must install before archive_download_ux")

    # A release publication workflow must never point at the legacy Inno output.
    draft_section = workflow.split("Create or update private draft Release", 1)[-1]
    if "release_upload/LinkVideo_VPN_Helper_Setup.exe" in draft_section:
        audit.error("Private draft Release still references legacy Inno Setup")


def audit_archive_contract(audit: Audit) -> None:
    path = PACKAGE / "services" / "archive_download_methods.py"
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    for marker in (
        '("1. FFmpeg", "ffmpeg")',
        '("2. Curl", "curl")',
        '("3. Без звука", "ffmpeg_no_audio")',
        "manage/dvr/export_mp4",
        '"-an"',
        "Content-Length",
        "ffmpeg.exe.download",
        "_ffmpeg_usable",
    ):
        if marker not in text:
            audit.error(f"Archive download contract missing marker: {marker}")


def audit_sensitive_literals(audit: Audit) -> None:
    private_key = "-----BEGIN PRIVATE KEY-----"
    token_patterns = [
        re.compile(r"ghp_[A-Za-z0-9]{20,}"),
        re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    ]
    for path in _files({".py", ".ps1", ".bat", ".yml", ".yaml", ".json", ".txt", ".md", ".go"}):
        # Release notes/docs can contain examples, but a real private key must
        # never exist anywhere in the source checkout.
        text = path.read_text(encoding="utf-8", errors="replace")
        if private_key in text:
            audit.error(f"Private key material committed: {_rel(path)}")
        for pattern in token_patterns:
            if pattern.search(text):
                audit.error(f"GitHub token-like literal committed: {_rel(path)}")


def audit_obsolete_runtime(audit: Audit) -> None:
    forbidden = [
        PACKAGE / "services" / "vpn_quarantine_runtime_fix.py",
        SCRIPTS / "prepare_bundled_ffmpeg.ps1",
    ]
    for path in forbidden:
        if path.exists():
            audit.error(f"Obsolete/conflicting runtime file must be removed: {_rel(path)}")


def main() -> None:
    audit = Audit()
    audit_python(audit)
    audit_release_chain(audit)
    audit_archive_contract(audit)
    audit_sensitive_literals(audit)
    audit_obsolete_runtime(audit)

    print("FULL RELEASE AUDIT")
    print(f"Python files parsed: {audit.stats['python_files']}")
    for warning in audit.warnings:
        print("WARNING:", warning)
    if audit.errors:
        for error in audit.errors:
            print("ERROR:", error)
        raise SystemExit(f"FULL RELEASE AUDIT FAILED: {len(audit.errors)} critical finding(s)")
    print("FULL RELEASE AUDIT OK")


if __name__ == "__main__":
    main()
