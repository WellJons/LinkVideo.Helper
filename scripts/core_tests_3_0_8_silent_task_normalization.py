from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "installer_next/silent_update_task_windows.go").read_text(encoding="utf-8")

# Task Scheduler may localize/canonicalize SYSTEM and normalize an action path.
# The installer must verify the security identity by SID and paths
# case-insensitively instead of comparing display strings literally.
assert "S-1-5-18" in source
assert "System.Security.Principal.NTAccount" in source
assert "System.Security.Principal.SecurityIdentifier" in source
assert "ExpandEnvironmentVariables" in source
assert "GetFullPath" in source
assert "System.StringComparison]::OrdinalIgnoreCase" in source
assert ".Trim().Trim('\\\"')" not in source  # guard a malformed escaped literal
assert "task principal is not SYSTEM" in source
assert "task action mismatch" in source
assert "task arguments mismatch" in source

print("CORE TESTS 3.0.8 SILENT TASK NORMALIZATION OK")
