from __future__ import annotations

"""Source-wide release audit for LinkVideo.Helper.

This is deliberately broader than feature regression tests. It walks the whole
checked-out repository, parses every runtime Python file, checks high-risk I/O
contracts, validates the updater/installer publication chain and reports risky
constructs that deserve review. Critical findings block release preflight.
"""

import ast
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "linkvideo_vpn_helper"
SCRIPTS = ROOT / "scripts"
THIS_FILE = Path(__file__).resolve()

SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "build",
    "dist",
    "installer_output",
    "release_upload",
    "release_payload",
    "release_candidate",
    "patch_output",
    "__pycache__",
}

# Reviewed best-effort sites only: logging shutdown, parsing fallbacks, cleanup
# after an already-reported failure, and non-critical UI teardown. Fingerprints
# deliberately include the function and exception type. Any new/moved/changed
# ``except ...: pass`` blocks the release until it is explicitly reviewed.
_ALLOWED_PASS_SITES = {
    ("linkvideo_vpn_helper/services/app_logging.py", "_close_file_handler", "Exception"): 1,
    ("linkvideo_vpn_helper/services/app_logging.py", "clear_logs", "Exception"): 1,
    ("linkvideo_vpn_helper/services/app_logging.py", "hook", "Exception"): 1,
    ("linkvideo_vpn_helper/services/app_logging.py", "shutdown_runtime_logging", "Exception"): 1,
    ("linkvideo_vpn_helper/services/archive_download_methods.py", "_download_ffmpeg", "Exception"): 1,
    ("linkvideo_vpn_helper/services/archive_download_process_guard.py", "_run_ffmpeg_progress", "(TypeError, ValueError, OverflowError)"): 1,
    ("linkvideo_vpn_helper/services/archive_download_process_guard.py", "_stop_process", "Exception"): 2,
    ("linkvideo_vpn_helper/services/archive_service_core.py", "_cached_dvr_servers", "Exception"): 1,
    ("linkvideo_vpn_helper/services/archive_service_core.py", "_parse_dvr_timeline", "Exception"): 1,
    ("linkvideo_vpn_helper/services/archive_service_core.py", "_parse_hls_duration", "Exception"): 1,
    ("linkvideo_vpn_helper/services/archive_service_core.py", "_parse_reserve_dt", "Exception"): 2,
    ("linkvideo_vpn_helper/services/google_key_discovery_compat.py", "add", "Exception"): 1,
    ("linkvideo_vpn_helper/services/google_key_discovery_compat.py", "discover_service_account_file", "Exception"): 1,
    ("linkvideo_vpn_helper/services/google_key_discovery_compat.py", "robust_from_settings", "Exception"): 1,
    ("linkvideo_vpn_helper/services/nat_inventory_compat.py", "exact", "Exception"): 1,
    ("linkvideo_vpn_helper/services/routeros_search_compat.py", "install_routeros_search_compat", "Exception"): 2,
    ("linkvideo_vpn_helper/services/vpn_automation_service.py", "_find_ids", "Exception"): 1,
    ("linkvideo_vpn_helper/services/vpn_automation_service.py", "get_status", "Exception"): 1,
    ("linkvideo_vpn_helper/services/vpn_retention_policy.py", "_remove_client_objects", "ValueError"): 1,
    ("linkvideo_vpn_helper/services/vpn_retention_policy.py", "parse_extended_comment", "Exception"): 1,
    ("linkvideo_vpn_helper/services/vpn_service.py", "_api_print_exact", "Exception"): 1,
    ("linkvideo_vpn_helper/services/vpn_service.py", "create_clients_batch", "Exception"): 3,
    ("linkvideo_vpn_helper/services/vpn_service.py", "exact", "Exception"): 1,
    ("linkvideo_vpn_helper/services/vpn_service.py", "fetch_client_snapshot", "Exception"): 1,
    ("linkvideo_vpn_helper/services/vpn_service.py", "parse_router_datetime", "Exception"): 1,
    ("linkvideo_vpn_helper/services/vpn_sheets_resilience.py", "_retry_delay", "Exception"): 1,
    ("linkvideo_vpn_helper/services/vpn_sheets_resilience.py", "invalidate_token", "Exception"): 1,
    ("linkvideo_vpn_helper/services/vpn_sheets_retention_compat.py", "_parse_dt", "Exception"): 1,
    ("linkvideo_vpn_helper/services/vpn_sheets_sync.py", "sync_server", "Exception"): 1,
    ("linkvideo_vpn_helper/ui/access_policy_integration.py", "_employee_warning", "Exception"): 1,
    ("linkvideo_vpn_helper/ui/access_policy_integration.py", "_is_employee_window", "Exception"): 1,
    ("linkvideo_vpn_helper/ui/access_policy_integration.py", "patched_go", "Exception"): 1,
    ("linkvideo_vpn_helper/ui/archive_download_ux.py", "patched_open_folder", "Exception"): 1,
    ("linkvideo_vpn_helper/ui/components.py", "__init__", "Exception"): 1,
    ("linkvideo_vpn_helper/ui/components.py", "_close_busy_dialog", "RuntimeError"): 1,
    ("linkvideo_vpn_helper/ui/components.py", "_ensure_busy_dialog", "RuntimeError"): 1,
    ("linkvideo_vpn_helper/ui/components.py", "restore", "RuntimeError"): 1,
    ("linkvideo_vpn_helper/ui/components_compat.py", "_close_popup", "Exception"): 1,
    ("linkvideo_vpn_helper/ui/components_compat.py", "_close_popup", "RuntimeError"): 1,
    ("linkvideo_vpn_helper/ui/components_compat.py", "clear_effect", "Exception"): 1,
    ("linkvideo_vpn_helper/ui/components_compat.py", "popup_destroyed", "Exception"): 1,
    ("linkvideo_vpn_helper/ui/main_window.py", "_go", "Exception"): 3,
    ("linkvideo_vpn_helper/ui/main_window.py", "_logout", "Exception"): 1,
    ("linkvideo_vpn_helper/ui/main_window.py", "_preload_next_page", "Exception"): 1,
    ("linkvideo_vpn_helper/ui/main_window.py", "_servers_changed", "Exception"): 2,
    ("linkvideo_vpn_helper/ui/main_window.py", "keyPressEvent", "Exception"): 1,
    ("linkvideo_vpn_helper/ui/operation_cancel_guard.py", "_reject", "Exception"): 1,
    ("linkvideo_vpn_helper/ui/pages/archive_diagnostics_page.py", "worker", "Exception"): 1,
    ("linkvideo_vpn_helper/ui/search_escape_compat.py", "cancel_current_action", "Exception"): 1,
    ("linkvideo_vpn_helper/ui/update_ux_integration.py", "_finish", "RuntimeError"): 1,
    ("linkvideo_vpn_helper/ui/uptime_ru_compat.py", "dialog_init", "Exception"): 1,
    ("linkvideo_vpn_helper/ui/uptime_ru_compat.py", "dialog_sample", "Exception"): 1,
    ("linkvideo_vpn_helper/ui/vpn_sheets_key_ui_compat.py", "_install_selected_key", "Exception"): 1,
    ("linkvideo_vpn_helper/ui/vpn_sheets_sync_integration.py", "wrapper", "Exception"): 1,
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
    # urllib.request.urlopen(url, data=None, timeout=...) has timeout as its
    # third positional argument. The second positional argument is request data
    # and must not be mistaken for a timeout.
    if name.endswith("urlopen") and len(call.args) >= 3:
        return True
    return False


def _keyword_literal(call: ast.Call, name: str):
    for keyword in call.keywords:
        if keyword.arg == name and isinstance(keyword.value, ast.Constant):
            return keyword.value.value
    return None


def audit_python(audit: Audit) -> None:
    python_files = list(_files({".py"}))
    audit.stats["python_files"] = len(python_files)
    broad_pass: Counter[tuple[str, str, str]] = Counter()
    todo_hits: list[str] = []

    for path in python_files:
        text = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError as exc:
            audit.error(f"Python syntax: {_rel(path)}:{exc.lineno}: {exc.msg}")
            continue

        is_runtime = PACKAGE in path.parents or path == PACKAGE
        functions = [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
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
                }:
                    if not _has_timeout(node):
                        audit.error(f"HTTP request without timeout: {_rel(path)}:{node.lineno} ({name})")
                    if _keyword_literal(node, "verify") is False:
                        audit.error(f"TLS verification disabled: {_rel(path)}:{node.lineno} ({name})")
                if name == "os.system":
                    audit.error(f"os.system is forbidden in runtime code: {_rel(path)}:{node.lineno}")
                if name in {
                    "subprocess.run",
                    "subprocess.call",
                    "subprocess.check_call",
                    "subprocess.check_output",
                    "subprocess.Popen",
                } and _keyword_literal(node, "shell") is True:
                    audit.error(f"shell=True is forbidden in runtime code: {_rel(path)}:{node.lineno} ({name})")
                if name == "tempfile.mktemp":
                    audit.error(f"Insecure tempfile.mktemp use: {_rel(path)}:{node.lineno}")

            if isinstance(node, ast.ExceptHandler) and is_runtime:
                pass_only = len(node.body) == 1 and isinstance(node.body[0], ast.Pass)
                if pass_only:
                    if node.type is None:
                        audit.error(f"Bare except/pass hides every failure: {_rel(path)}:{node.lineno}")
                    elif isinstance(node.type, ast.Name) and node.type.id == "BaseException":
                        audit.error(f"BaseException/pass hides process-control failures: {_rel(path)}:{node.lineno}")
                    else:
                        enclosing = [
                            fn
                            for fn in functions
                            if fn.lineno <= node.lineno <= getattr(fn, "end_lineno", fn.lineno)
                        ]
                        function_name = max(enclosing, key=lambda fn: fn.lineno).name if enclosing else "<module>"
                        exception_name = ast.unparse(node.type) if node.type is not None else "bare"
                        broad_pass[(_rel(path), function_name, exception_name)] += 1

        if path.resolve() != THIS_FILE:
            for lineno, line in enumerate(text.splitlines(), 1):
                if re.search(r"\b(?:TODO|FIXME|XXX)\b", line, re.I):
                    todo_hits.append(f"{_rel(path)}:{lineno}")

    expected_pass = Counter(_ALLOWED_PASS_SITES)
    unexpected_pass = broad_pass - expected_pass
    missing_pass = expected_pass - broad_pass
    for fingerprint, count in sorted(unexpected_pass.items()):
        audit.error(f"Unreviewed exception/pass site ({count}x): {fingerprint}")
    for fingerprint, count in sorted(missing_pass.items()):
        audit.error(f"Reviewed exception/pass baseline changed ({count}x missing): {fingerprint}")
    if todo_hits:
        audit.warn(f"TODO/FIXME markers: {len(todo_hits)} (sample: {', '.join(todo_hits[:8])})")


def audit_release_chain(audit: Audit) -> None:
    required = {
        "app": ROOT / "linkvideo_vpn_helper" / "app.py",
        "version": ROOT / "linkvideo_vpn_helper" / "version.py",
        "update": ROOT / "linkvideo_vpn_helper" / "services" / "update_service.py",
        "archive_methods": ROOT / "linkvideo_vpn_helper" / "services" / "archive_download_methods.py",
        "archive_core": ROOT / "linkvideo_vpn_helper" / "services" / "archive_service_core.py",
        "archive_process_guard": ROOT / "linkvideo_vpn_helper" / "services" / "archive_download_process_guard.py",
        "spec": ROOT / "LinkVideo.Helper.spec",
        "verifier": ROOT / "scripts" / "verify_release.ps1",
        "go_audit": ROOT / "scripts" / "audit_go.ps1",
        "build_next": ROOT / "scripts" / "build_next_installer.ps1",
        "installer_backend": ROOT / "installer_next" / "backend_windows.go",
        "installer_selftest": ROOT / "installer_next" / "selftest_windows.go",
        "patcher": ROOT / "patcher" / "main_windows.go",
        "silent_updater": ROOT / "silent_updater" / "main_windows.go",
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
    archive_core = required["archive_core"].read_text(encoding="utf-8")
    process_guard = required["archive_process_guard"].read_text(encoding="utf-8")
    spec = required["spec"].read_text(encoding="utf-8")
    verifier = required["verifier"].read_text(encoding="utf-8")
    go_audit = required["go_audit"].read_text(encoding="utf-8")
    build = required["build_next"].read_text(encoding="utf-8")
    backend = required["installer_backend"].read_text(encoding="utf-8")
    selftest = required["installer_selftest"].read_text(encoding="utf-8")
    patcher = required["patcher"].read_text(encoding="utf-8")
    silent_updater = required["silent_updater"].read_text(encoding="utf-8")
    workflow = required["windows_workflow"].read_text(encoding="utf-8")
    publish = required["publish_workflow"].read_text(encoding="utf-8")

    markers = [
        ("GitHub production manifest", "WellJons/LinkVideo.Helper.Updates/main/update-manifest.json" in update),
        ("SHA verification", "actual_hash != expected_hash" in update),
        ("update manifests require SHA-256", "required=True" in update and "_MAX_SETUP_BYTES" in update),
        ("update URLs require HTTPS/trusted production hosts", "_validate_update_url" in update and "_TRUSTED_UPDATE_HOSTS" in update),
        ("update manifest download is size bounded", "_MAX_MANIFEST_BYTES + 1" in update),
        ("ProductVersion verification", "_windows_product_version" in update),
        ("safe Windows update path transport", "LINKVIDEO_UPDATE_FILE" in update),
        ("patcher uses safe ProductVersion path transport", "LINKVIDEO_PRODUCT_VERSION_FILE" in patcher and '"-Command", script, path' not in patcher),
        ("silent updater uses safe ProductVersion path transport", "LINKVIDEO_PRODUCT_VERSION_FILE" in silent_updater and '"-Command", script, path' not in silent_updater),
        ("single authoritative update ProductVersion probe", "install_update_version_probe_compat" not in app),
        ("VPN rollback failures are surfaced", "Автоматический откат выполнен не полностью" in (PACKAGE / "services" / "nat_conflict_compat.py").read_text(encoding="utf-8") and "не удалось полностью удалить созданные объекты" in (PACKAGE / "services" / "vpn_service.py").read_text(encoding="utf-8")),
        ("archive methods installed", "install_archive_download_methods()" in app),
        ("archive process guard wired directly", "archive_download_process_guard import" in methods and "guarded_run" in methods),
        ("default FFmpeg route uses cancellable implementation", "_download_with_ffmpeg_audio" in archive_core and "return original_download(self, discovery, output, progress, cancel_event)" not in methods),
        ("archive service does not depend on startup monkey patches", "return _download_ffmpeg(self, progress, cancel_event)" in archive_core and "ArchiveService.ensure_ffmpeg =" not in methods and "ArchiveService.download =" not in methods),
        ("Curl child cleanup", "_run_process_cancellable" in process_guard and "finally:" in process_guard),
        ("FFmpeg child cleanup", "_run_ffmpeg_progress" in process_guard and "_stop_process(proc)" in process_guard),
        ("FFmpeg progress cannot block cancellation", "queue.Queue" in process_guard and "timeout_seconds" in process_guard and "daemon=True" in process_guard),
        ("FFmpeg network timeout is explicit", '"-rw_timeout", "30000000"' in methods and '"-nostdin"' in methods and "install_archive_process_hardening" not in app),
        ("archive output validates MP4 before atomic commit", "_is_probable_mp4" in methods and "без корректного итогового MP4" in methods),
        ("FFmpeg LocalAppData download", "FFMPEG_DOWNLOAD_URL" in methods and "tools_dir()" in methods),
        ("FFmpeg download verifies vendor SHA-256", ".sha256" in methods and "actual_hash != expected_hash" in methods and "_trusted_ffmpeg_url" in methods),
        ("FFmpeg absent from PyInstaller spec", "ffmpeg = root /" not in spec),
        ("payload explicitly rejects FFmpeg", "installer payload correctly excludes ffmpeg.exe" in build),
        ("authoritative Setup filename", "LinkVideo.Helper_Setup.exe" in build and "LinkVideo.Helper_Setup_Next.exe" not in build),
        ("full installer stages and atomically activates runtime", "stageRuntimeSnapshot(dest, progress)" in backend and "activateStagedRuntime(dest, staging)" in backend),
        ("full installer can recover interrupted activation", "recoverInterruptedRuntimeUpgrade(dest)" in backend and "rollbackActivatedRuntime(dest, backup)" in backend),
        ("full installer prevents silent-patch races", "removeSilentUpdateTask()" in backend and "LinkVideo.Helper.Updater.Worker.exe" in backend and "official-patch.exe" in backend),
        ("exact Setup has side-effect-free self-test", 'hasArg("--self-test")' in selftest and "installerSelfTest()" in selftest),
        ("one reusable full verifier", "Build and preflight application runtime" in verifier and "Ruff critical correctness audit" in verifier),
        ("verifier runs Go audit", "scripts\\audit_go.ps1" in verifier and "go vet" in go_audit),
        ("verifier builds authoritative Setup", "scripts\\build_next_installer.ps1" in verifier),
        ("verifier compiles patch pipeline", "scripts\\test_patch_builder.ps1" in verifier),
        ("verifier self-tests exact Setup", "Self-test exact produced Setup payload" in verifier and "--self-test" in verifier),
        ("verifier checks ProductVersion", "Assert-Version" in verifier and "ProductVersion mismatch" in verifier),
        ("verifier records SHA256", "Get-FileHash -Algorithm SHA256" in verifier and "verification.json" in verifier),
        ("verifier protects source tree", "git status --porcelain --untracked-files=no" in verifier),
        ("CI calls reusable verifier", "scripts/verify_release.ps1" in workflow),
        ("RC is one private draft Release", "Create or update private RC draft" in workflow and '"rc-$version"' in workflow),
        ("Actions artifact quota is not used", "actions/upload-artifact" not in workflow),
        ("legacy Inno build is gone", "build_setup.bat" not in workflow and "innosetup" not in workflow.lower()),
        ("final draft uses authoritative Setup", "Create or update private final draft Release" in workflow and "installer_next/output/LinkVideo.Helper_Setup.exe" in workflow),
        ("release tag must match APP_VERSION", "does not match APP_VERSION" in workflow and "Invalid final release tag" in workflow),
        ("release assets match verification report", "verificationData.setup_sha256" in workflow and "verification.source_commit" in workflow),
        ("published private release assets are immutable", "Refusing to replace assets of an already published release" in workflow),
        ("final draft keeps private patch baseline", 'LinkVideo.Helper_Payload_${version}.zip' in workflow and 'LinkVideo.Helper_Payload_${version}.json' in workflow),
        ("temporary RC is deleted on final draft", "gh release delete $rcTag" in workflow and "--cleanup-tag" in workflow),
        ("public publisher downloads exact Setup", "--pattern 'LinkVideo.Helper_Setup.exe'" in publish and "--pattern '*Setup.exe'" not in publish),
        ("public manifest writes SHA", '"sha256": setup_sha' in publish),
        ("public channel verifies Setup before exposing manifest", "Verify uploaded Setup before exposing manifest" in publish and "Pre-publish Setup SHA-256 mismatch" in publish),
        ("public channel re-downloads and verifies Setup", "Public Setup SHA-256 mismatch" in publish and "/tmp/LinkVideo.Helper_Setup.exe" in publish),
        ("public publisher serializes releases", "group: publish-public-update" in publish and "cancel-in-progress: false" in publish),
        ("public publisher rejects draft/prerelease sources", "Require an already published final source release" in publish and "isPrerelease" in publish and "isDraft" in publish),
        ("public publisher validates verification evidence", "Validate source verification evidence" in publish and "verification.json" in publish and "patch_pipeline_compile" in publish),
        ("public publisher blocks rollback/rebuild", "Protect public channel from rollback or same-version rebuild" in publish and "Refusing public channel rollback" in publish and "Refusing to replace an already published version" in publish),
        ("production channel is full-Setup-only", "LinkVideo.Helper_Patch_*.exe" not in publish and '"patches": {}' in publish),
    ]
    for label, okay in markers:
        if not okay:
            audit.error(f"Release contract missing: {label}")

    if app.index("install_archive_download_methods()") > app.index("install_archive_download_ux()"):
        audit.error("Archive method UI must install before archive_download_ux")

    install_section = backend.split("func installProduct", 1)[-1]
    install_markers = (
        "recoverInterruptedRuntimeUpgrade(dest)",
        "stageRuntimeSnapshot(dest, progress)",
        "activateStagedRuntime(dest, staging)",
        "verifyRuntimeSnapshot(dest)",
        "rollbackActivatedRuntime(dest, backup)",
    )
    for marker in install_markers:
        if marker not in install_section:
            audit.error(f"Installer transactional upgrade path misses: {marker}")
    if all(marker in install_section for marker in install_markers):
        if install_section.index("stageRuntimeSnapshot(dest, progress)") > install_section.index("activateStagedRuntime(dest, staging)"):
            audit.error("Installer must fully stage the payload before activating it")

    transaction_section = backend.split("func verifyRuntimeSnapshot", 1)[-1].split("func extractPayload", 1)[0]
    for marker in ('dest + ".rollback"', "os.Rename(dest, backup)", "os.Rename(staging, dest)", "LinkVideo.Helper.Updater.exe"):
        if marker not in transaction_section:
            audit.error(f"Installer runtime transaction misses: {marker}")
    if "LOCALAPPDATA" in transaction_section or "APPDATA" in transaction_section:
        audit.error("Full installer transaction must not delete LocalAppData/AppData user cache")

    if "release_upload/LinkVideo_VPN_Helper_Setup.exe" in workflow:
        audit.error("Windows workflow still references the retired Inno release path")


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
        "_download_expected_sha256",
        "actual_hash != expected_hash",
        "_trusted_ffmpeg_url",
        "ffmpeg.exe.download",
        "_ffmpeg_usable",
    ):
        if marker not in text:
            audit.error(f"Archive download contract missing marker: {marker}")


def audit_sensitive_literals(audit: Audit) -> None:
    private_key_pattern = re.compile(
        r"-----BEGIN PRIVATE KEY-----\s+[A-Za-z0-9+/=\r\n]{80,}-----END PRIVATE KEY-----",
        re.MULTILINE,
    )
    token_patterns = [
        re.compile(r"ghp_[A-Za-z0-9]{20,}"),
        re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    ]
    for path in _files({".py", ".ps1", ".bat", ".yml", ".yaml", ".json", ".txt", ".md", ".go"}):
        text = path.read_text(encoding="utf-8", errors="replace")
        if private_key_pattern.search(text):
            audit.error(f"Private key material committed: {_rel(path)}")
        for pattern in token_patterns:
            if pattern.search(text):
                audit.error(f"GitHub token-like literal committed: {_rel(path)}")


def audit_obsolete_runtime(audit: Audit) -> None:
    forbidden = [
        PACKAGE / "services" / "vpn_quarantine_runtime_fix.py",
        PACKAGE / "services" / "update_version_probe_compat.py",
        PACKAGE / "services" / "archive_process_hardening.py",
        SCRIPTS / "prepare_bundled_ffmpeg.ps1",
        ROOT / "installer.iss",
        ROOT / "build_setup.bat",
        ROOT / "prepare_release.bat",
        ROOT / "server_example" / "version.json",
        SCRIPTS / "make_release_manifest.py",
    ]
    for path in forbidden:
        if path.exists():
            audit.error(f"Obsolete/conflicting release file must be removed: {_rel(path)}")

    for path in ROOT.glob("BUILD_RELEASE_*.bat"):
        audit.error(f"Versioned build script must not live in repository root: {_rel(path)}")


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
