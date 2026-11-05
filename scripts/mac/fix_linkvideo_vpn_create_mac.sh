#!/bin/bash
# LinkVideo Helper WEB (macOS): enable/disable VPN client creation via launchd.
# This changes only HELPER_ENABLE_VPN_CREATE; does not create or delete clients.
set -euo pipefail

PLIST='/Library/LaunchDaemons/ru.linkvideo.helper.plist'
LABEL='ru.linkvideo.helper'
KEY='HELPER_ENABLE_VPN_CREATE'
PB='/usr/libexec/PlistBuddy'
ACTION="${1:---status}"

usage() {
  echo 'Usage: sudo bash fix_linkvideo_vpn_create_mac.sh --enable|--disable|--status' >&2
  exit 2
}

[[ "$ACTION" == '--enable' || "$ACTION" == '--disable' || "$ACTION" == '--status' ]] || usage
[[ -f "$PLIST" ]] || { echo "ERROR: launchd plist not found: $PLIST" >&2; exit 1; }
[[ -x "$PB" ]] || { echo "ERROR: PlistBuddy not found: $PB" >&2; exit 1; }

read_flag() {
  "$PB" -c "Print :EnvironmentVariables:$KEY" "$PLIST" 2>/dev/null || printf '%s\n' '(not set)'
}

show_status() {
  echo "Config: $KEY=$(read_flag)"
  if launchctl print "system/$LABEL" >/dev/null 2>&1; then
    echo 'launchd: service loaded'
    if launchctl print "system/$LABEL" 2>/dev/null | grep -Fq "$KEY"; then
      echo 'launchd: VPN creation flag present in loaded job'
    else
      echo 'launchd: flag not visible in loaded job (check after reload)'
    fi
  else
    echo 'launchd: service not loaded'
  fi
  echo 'Health check (read-only):'
  curl --silent --show-error --fail --max-time 5 'http://127.0.0.1:8000/api/health' || true
  printf '\n'
}

if [[ "$ACTION" == '--status' ]]; then
  show_status
  exit 0
fi

[[ "$EUID" -eq 0 ]] || { echo 'ERROR: run with sudo for --enable/--disable' >&2; exit 1; }

if [[ "$ACTION" == '--enable' ]]; then VALUE='1'; else VALUE='0'; fi
BACKUP="${PLIST}.backup-vpn-create-$(date '+%Y%m%d-%H%M%S')"
cp -p "$PLIST" "$BACKUP"
chmod 600 "$BACKUP"
echo "Backup: $BACKUP"

if ! "$PB" -c 'Print :EnvironmentVariables' "$PLIST" >/dev/null 2>&1; then
  "$PB" -c 'Add :EnvironmentVariables dict' "$PLIST"
fi
if "$PB" -c "Print :EnvironmentVariables:$KEY" "$PLIST" >/dev/null 2>&1; then
  "$PB" -c "Delete :EnvironmentVariables:$KEY" "$PLIST"
fi
"$PB" -c "Add :EnvironmentVariables:$KEY string $VALUE" "$PLIST"

if ! plutil -lint "$PLIST"; then
  cp -p "$BACKUP" "$PLIST"
  echo 'ERROR: invalid plist; backup restored.' >&2
  exit 1
fi

restart_job() {
  launchctl bootout "system/$LABEL" >/dev/null 2>&1 || true
  launchctl bootout system "$PLIST" >/dev/null 2>&1 || true
  launchctl bootstrap system "$PLIST" && launchctl kickstart -k "system/$LABEL"
}

if ! restart_job; then
  echo 'ERROR: restart failed; restoring previous plist...' >&2
  cp -p "$BACKUP" "$PLIST"
  restart_job || true
  exit 1
fi

echo "Applied: $KEY=$VALUE"
show_status
printf '\nNote: reload the Helper page. This script does not perform a VPN write test.\n'
