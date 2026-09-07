#!/usr/bin/env bash
set -Eeuo pipefail

BRANCH="feature/3.0.13-postgres-vpnsync"
REPO_URL="https://github.com/WellJons/LinkVideo.Helper.git"
APP_ROOT="/opt/linkvideo-vpnsync"
APP_USER="linkvideo-vpnsync"
APP_GROUP="linkvideo-vpnsync"
ENV_DIR="/etc/linkvideo-vpnsync"
ENV_FILE="${ENV_DIR}/vpnsync.env"
STATE_DIR="/var/lib/linkvideo-vpnsync"
LOG_DIR="/var/log/linkvideo-vpnsync"
DB_NAME="linkvideo_vpn"
DB_USER="linkvideo_vpnsync"
SERVICE="linkvideo-vpnsync.service"

fail() { echo "[ERROR] $*" >&2; exit 1; }
info() { echo "[VPNSync] $*"; }

[[ ${EUID} -eq 0 ]] || fail "Run as root: sudo bash server/install_ubuntu.sh"
[[ -r /etc/os-release ]] || fail "/etc/os-release not found"
# shellcheck disable=SC1091
. /etc/os-release
[[ "${ID:-}" == "ubuntu" ]] || fail "This installer currently supports Ubuntu only"

info "Ubuntu ${VERSION_ID:-unknown}: installing PostgreSQL and Python runtime"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  postgresql postgresql-contrib \
  python3 python3-venv python3-pip \
  git curl ca-certificates openssl

systemctl enable --now postgresql
systemctl is-active --quiet postgresql || fail "PostgreSQL did not start"

if ! getent group "${APP_GROUP}" >/dev/null; then
  groupadd --system "${APP_GROUP}"
fi
if ! id "${APP_USER}" >/dev/null 2>&1; then
  useradd --system --gid "${APP_GROUP}" --home-dir "${STATE_DIR}" --shell /usr/sbin/nologin "${APP_USER}"
fi
install -d -o "${APP_USER}" -g "${APP_GROUP}" -m 0750 "${STATE_DIR}" "${LOG_DIR}"
install -d -o root -g "${APP_GROUP}" -m 0750 "${ENV_DIR}"

info "Installing application tree into ${APP_ROOT}"
if [[ -d "${APP_ROOT}/.git" ]]; then
  git -C "${APP_ROOT}" fetch --depth 1 origin "${BRANCH}"
  git -C "${APP_ROOT}" checkout -B "${BRANCH}" FETCH_HEAD
else
  rm -rf "${APP_ROOT}"
  git clone --depth 1 --branch "${BRANCH}" "${REPO_URL}" "${APP_ROOT}"
fi

python3 -m venv "${APP_ROOT}/.venv"
"${APP_ROOT}/.venv/bin/python" -m pip install --upgrade pip setuptools wheel
"${APP_ROOT}/.venv/bin/pip" install -r "${APP_ROOT}/server/requirements.txt"

if [[ ! -f "${ENV_FILE}" ]]; then
  info "Creating PostgreSQL role/database and local service secrets"
  DB_PASSWORD="$(openssl rand -hex 24)"
  API_TOKEN="$(openssl rand -hex 32)"
  ENCRYPTION_KEY="$(openssl rand -hex 32)"

  if sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}'" | grep -q 1; then
    sudo -u postgres psql -v ON_ERROR_STOP=1 -c "ALTER ROLE ${DB_USER} WITH LOGIN PASSWORD '${DB_PASSWORD}';"
  else
    sudo -u postgres psql -v ON_ERROR_STOP=1 -c "CREATE ROLE ${DB_USER} WITH LOGIN PASSWORD '${DB_PASSWORD}';"
  fi

  if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -q 1; then
    sudo -u postgres createdb --owner="${DB_USER}" "${DB_NAME}"
  else
    sudo -u postgres psql -v ON_ERROR_STOP=1 -c "ALTER DATABASE ${DB_NAME} OWNER TO ${DB_USER};"
  fi

  sudo -u postgres psql -v ON_ERROR_STOP=1 -d "${DB_NAME}" -c "CREATE EXTENSION IF NOT EXISTS pgcrypto;"

  BIND_HOST="$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -E '^([0-9]{1,3}\.){3}[0-9]{1,3}$' | grep -v '^127\.' | head -n1 || true)"
  [[ -n "${BIND_HOST}" ]] || BIND_HOST="127.0.0.1"

  umask 077
  cat >"${ENV_FILE}" <<EOF
VPNSYNC_BIND_HOST=${BIND_HOST}
VPNSYNC_BIND_PORT=8787
VPNSYNC_API_TOKEN=${API_TOKEN}
DATABASE_URL=postgresql://${DB_USER}:${DB_PASSWORD}@127.0.0.1:5432/${DB_NAME}
VPNSYNC_ENCRYPTION_KEY=${ENCRYPTION_KEY}
ROUTEROS_API_PORT=8728
ROUTEROS_TIMEOUT=6.0
EOF
  chown root:"${APP_GROUP}" "${ENV_FILE}"
  chmod 0640 "${ENV_FILE}"
else
  info "Existing ${ENV_FILE} found; preserving all secrets"
fi

set -a
# shellcheck disable=SC1090
. "${ENV_FILE}"
set +a

# Validate the newly fetched code while the currently running service is still
# untouched. A bad branch must never take the healthy VPNSync process offline.
info "Preflight: compiling VPNSync Python modules"
"${APP_ROOT}/.venv/bin/python" -m compileall -q "${APP_ROOT}/server/linkvideo_vpnsync" \
  || fail "Python compile preflight failed; current service was not stopped"

info "Preflight: importing FastAPI, routes and PostgreSQL archive"
PYTHONPATH="${APP_ROOT}/server" "${APP_ROOT}/.venv/bin/python" - <<'PY'
from linkvideo_vpnsync.api import app, _db

paths = {getattr(route, "path", "") for route in app.routes}
required = {
    "/health",
    "/v1/auth/login",
    "/v1/activity",
    "/v1/activity/stream",
    "/v1/activity/desktop",
    "/v1/deleted/search",
    "/v1/archive/restore/preflight",
    "/v1/archive/restore/server-preflight",
    "/v1/archive/restore",
}
missing = sorted(required - paths)
if missing:
    raise SystemExit("Missing VPNSync route(s): " + ", ".join(missing))

# Execute archive reads against the existing schema before touching the running
# service. The impossible queries return no rows while still checking SQL,
# decryption-path setup and psycopg parameter adaptation.
_db.open()
try:
    rows = _db.search_deleted_clients("__vpnsync_preflight_no_match__", limit=1)
    if rows:
        raise SystemExit("Unexpected VPNSync deleted-archive preflight result")
    row = _db.get_deleted_client_detail(
        "__vpnsync_preflight_no_server__",
        "__vpnsync_preflight_no_login__",
        include_password=False,
    )
    if row is not None:
        raise SystemExit("Unexpected VPNSync deleted-detail preflight result")
finally:
    _db.close()

print(f"[VPNSync] Preflight OK: {len(paths)} API routes loaded; PostgreSQL archive OK")
PY

# Schema/index migrations must not race a running sync worker. Stop the service
# only after compile/import/DB-read preflight has completed successfully.
if systemctl is-active --quiet "${SERVICE}" 2>/dev/null; then
  info "Stopping ${SERVICE} before PostgreSQL migrations"
  systemctl stop "${SERVICE}"
fi

info "Applying PostgreSQL migrations"
psql "${DATABASE_URL}" -v ON_ERROR_STOP=1 <<'SQL'
CREATE TABLE IF NOT EXISTS vpnsync_schema_migrations (
    name TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
SQL

for migration in "${APP_ROOT}"/server/sql/*.sql; do
  migration_name="$(basename "${migration}")"
  [[ "${migration_name}" =~ ^[0-9A-Za-z_.-]+$ ]] || fail "Unsafe migration filename: ${migration_name}"
  already="$(psql "${DATABASE_URL}" -Atqc "SELECT 1 FROM vpnsync_schema_migrations WHERE name = '${migration_name}' LIMIT 1")"
  if [[ "${already}" == "1" ]]; then
    info "Migration ${migration_name}: already applied"
    continue
  fi
  info "Migration ${migration_name}: applying"
  psql "${DATABASE_URL}" -v ON_ERROR_STOP=1 -f "${migration}"
  psql "${DATABASE_URL}" -v ON_ERROR_STOP=1 -c "INSERT INTO vpnsync_schema_migrations(name) VALUES ('${migration_name}') ON CONFLICT DO NOTHING" >/dev/null
done

OPERATOR_COUNT="$(psql "${DATABASE_URL}" -Atqc "SELECT count(*) FROM vpnsync_users WHERE enabled = TRUE" 2>/dev/null || echo 0)"
if [[ "${OPERATOR_COUNT}" =~ ^[0-9]+$ ]] && (( OPERATOR_COUNT == 0 )); then
  info "No enabled VPNSync desktop operator exists yet. SSH/Termius credentials are separate."
  info "Create one after install: sudo env PYTHONPATH=${APP_ROOT}/server ${APP_ROOT}/.venv/bin/python ${APP_ROOT}/server/manage_operator.py --username <login> --role admin"
else
  info "VPNSync desktop operators enabled: ${OPERATOR_COUNT}"
fi

install -m 0644 "${APP_ROOT}/server/systemd/linkvideo-vpnsync.service" "/etc/systemd/system/${SERVICE}"
systemctl daemon-reload
systemctl enable "${SERVICE}" >/dev/null
systemctl restart "${SERVICE}"

info "Waiting for API health check"
HEALTH_URL="http://${VPNSYNC_BIND_HOST}:${VPNSYNC_BIND_PORT}/health"
for _ in $(seq 1 30); do
  if curl -fsS --max-time 2 "${HEALTH_URL}" >/tmp/linkvideo-vpnsync-health.json 2>/dev/null; then
    echo
    cat /tmp/linkvideo-vpnsync-health.json
    echo
    rm -f /tmp/linkvideo-vpnsync-health.json
    info "Installation/update completed successfully"
    info "API: ${HEALTH_URL%/health}"
    info "Secrets: ${ENV_FILE} (not printed)"
    info "PostgreSQL is backup/archive storage; interactive Helper VPN work stays direct to RouterOS."
    exit 0
  fi
  sleep 1
done

journalctl -u "${SERVICE}" -n 80 --no-pager || true
fail "VPNSync did not pass /health"
