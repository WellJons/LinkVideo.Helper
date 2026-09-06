from __future__ import annotations

import hmac
import json
import queue
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .activity_bus import activity_bus
from .archive_restore import ArchiveRestoreConflict, ArchiveRestoreService
from .auth import AuthContext, AuthService
from .config import get_settings
from .db import VPNDatabase
from .deadline_scheduler import DeadlineScheduler
from .monitor import RouterOSMonitorManager
from .operations import VPNSyncOperations
from .operations_api import build_operations_router


_settings = get_settings()
_db = VPNDatabase(_settings.database_url, _settings.encryption_key)
_monitor = RouterOSMonitorManager(_db, _settings)
_deadlines = DeadlineScheduler(_db, _settings, _monitor)
_auth = AuthService(_db, _settings.api_token, session_ttl=_settings.session_ttl_seconds)
_operations = VPNSyncOperations(_db, _settings, _monitor)
_archive_restore = ArchiveRestoreService(_db, _settings, _monitor)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _db.open()
    _monitor.start()
    _deadlines.start()
    try:
        yield
    finally:
        _deadlines.stop()
        _monitor.stop()
        _db.close()


app = FastAPI(
    title="LinkVideo.VPNSync",
    version="0.3.0",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=128)
    password: str = Field(..., min_length=1, max_length=512)


class DesktopActivityRequest(BaseModel):
    action: str = Field(..., min_length=1, max_length=128)
    server: str = Field("", max_length=255)
    login: str = Field("", max_length=128)
    success: bool = True
    details: dict[str, Any] = Field(default_factory=dict)


class ArchiveRestoreRequest(BaseModel):
    server: str = Field(..., min_length=1, max_length=255)
    login: str = Field(..., min_length=1, max_length=128)


def require_auth(authorization: Annotated[str | None, Header()] = None) -> AuthContext:
    value = str(authorization or "").strip()
    if not value.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    token = value[7:].strip()
    if hmac.compare_digest(token, _settings.api_token):
        return AuthContext(username="legacy-api-token", role="admin", legacy=True)
    try:
        return _auth.verify_token(token)
    except ValueError:
        raise HTTPException(status_code=401, detail="Unauthorized") from None


def _require_operator(auth: AuthContext) -> None:
    if auth.role not in {"operator", "admin"}:
        raise HTTPException(status_code=403, detail="Operator role required")


def _server_id(hostname: str) -> int | None:
    host = str(hostname or "").strip().lower()
    if not host:
        return None
    with _db.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM vpn_servers WHERE lower(hostname)=lower(%s)", (host,))
        row = cur.fetchone()
        return int(row["id"]) if row else None


def _can_read_password(auth: AuthContext) -> bool:
    return auth.role in {"operator", "admin"}


def _audit_archive(auth: AuthContext, action: str, server: str, login: str, success: bool, details: dict[str, Any]) -> None:
    _auth.audit(
        actor=auth.username,
        role=auth.role,
        source="archive",
        action=action,
        server_id=_server_id(server),
        login=login,
        success=success,
        details={**dict(details or {}), "server": str(server or "")},
    )
    activity_bus.publish({
        "kind": "audit",
        "source": "archive",
        "action": action,
        "actor": auth.username,
        "server": server,
        "login": login,
        "success": success,
        "details": details,
    })


@app.get("/health")
def health() -> dict:
    info = _db.ping()
    monitor_status = _monitor.status()
    deadline_status = _deadlines.status()
    offset = int(_settings.business_utc_offset_hours)
    return {
        "ok": True,
        "database": info.get("database"),
        "database_time": info.get("now"),
        "routeros_monitor": monitor_status.get("enabled", False),
        "routeros_servers": monitor_status.get("target_count", 0),
        "routeros_workers_alive": monitor_status.get("workers_alive", 0),
        "routeros": monitor_status,
        "retention_enabled": bool(_settings.retention_enabled),
        "deadline_scheduler": deadline_status,
        "business_timezone": f"UTC{offset:+d}",
        "auth": "operator-session",
        "interactive_helper_routing": "direct-routeros",
        "postgres_role": "passive-backup-and-archive",
        "archive_restore": True,
    }


@app.post("/v1/auth/login")
def login(payload: LoginRequest, request: Request) -> dict:
    actor = str(payload.username or "").strip()
    try:
        token, context, expires_in = _auth.login(actor, payload.password)
    except ValueError:
        try:
            _auth.audit(
                actor=actor,
                source="desktop",
                action="auth.login",
                success=False,
                details={"remote": request.client.host if request.client else ""},
            )
            activity_bus.publish({"kind": "audit", "source": "desktop", "action": "auth.login", "actor": actor, "success": False})
        except Exception:
            pass
        raise HTTPException(status_code=401, detail="Invalid username or password") from None
    _auth.audit(
        actor=context.username,
        role=context.role,
        source="desktop",
        action="auth.login",
        success=True,
        details={"remote": request.client.host if request.client else ""},
    )
    activity_bus.publish({"kind": "audit", "source": "desktop", "action": "auth.login", "actor": context.username, "success": True})
    offset = int(_settings.business_utc_offset_hours)
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": expires_in,
        "username": context.username,
        "role": context.role,
        "business_timezone": f"UTC{offset:+d}",
    }


@app.get("/v1/auth/me")
def auth_me(auth: AuthContext = Depends(require_auth)) -> dict:
    return {"username": auth.username, "role": auth.role, "legacy": auth.legacy}


@app.get("/v1/servers")
def servers(auth: AuthContext = Depends(require_auth)) -> list[dict]:
    return _db.list_servers()


# These PostgreSQL endpoints are retained for backup/archive diagnostics only.
# LinkVideo.Helper interactive search does not call them.
@app.get("/v1/clients/search")
def search_clients(
    q: str = Query(..., min_length=1, max_length=128),
    limit: int = Query(50, ge=1, le=200),
    auth: AuthContext = Depends(require_auth),
) -> list[dict]:
    return _db.search_client_details(
        q,
        limit=limit,
        include_password=_can_read_password(auth),
    )


@app.get("/v1/clients/detail")
def client_detail(
    server: str = Query(..., min_length=1, max_length=255),
    login: str = Query(..., min_length=1, max_length=128),
    auth: AuthContext = Depends(require_auth),
) -> dict:
    row = _db.get_client_detail(
        server,
        login,
        include_password=_can_read_password(auth),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="VPN client backup not found")
    return row


@app.get("/v1/deleted/search")
def search_deleted(
    q: str = Query(..., min_length=1, max_length=128),
    limit: int = Query(50, ge=1, le=200),
    auth: AuthContext = Depends(require_auth),
) -> list[dict]:
    return _db.search_deleted_clients(q, limit=limit)


@app.get("/v1/archive/restore/preflight")
def archive_restore_preflight(
    server: str = Query(..., min_length=1, max_length=255),
    login: str = Query(..., min_length=1, max_length=128),
    auth: AuthContext = Depends(require_auth),
) -> dict:
    _require_operator(auth)
    try:
        result = _archive_restore.preflight(server, login)
    except Exception as exc:
        _audit_archive(auth, "archive.restore.preflight", server, login, False, {"error": str(exc)[:1000]})
        raise HTTPException(status_code=400, detail=str(exc)) from None
    _audit_archive(auth, "archive.restore.preflight", server, login, bool(result.get("ok")), {"conflicts": result.get("conflicts", [])})
    return result


@app.get("/v1/archive/restore/server-preflight")
def archive_restore_server_preflight(
    server: str = Query(..., min_length=1, max_length=255),
    auth: AuthContext = Depends(require_auth),
) -> dict:
    _require_operator(auth)
    try:
        result = _archive_restore.preflight_server(server)
    except Exception as exc:
        _audit_archive(auth, "archive.restore.server_preflight", server, "", False, {"error": str(exc)[:1000]})
        raise HTTPException(status_code=400, detail=str(exc)) from None
    _audit_archive(auth, "archive.restore.server_preflight", server, "", bool(result.get("ok")), result)
    return result


@app.post("/v1/archive/restore")
def archive_restore(
    payload: ArchiveRestoreRequest,
    auth: AuthContext = Depends(require_auth),
) -> dict:
    _require_operator(auth)
    server = str(payload.server or "").strip()
    login = str(payload.login or "").strip()
    try:
        result = _archive_restore.restore(server, login)
    except ArchiveRestoreConflict as exc:
        details = {"error": str(exc), "conflicts": exc.conflicts}
        _audit_archive(auth, "archive.restore", server, login, False, details)
        raise HTTPException(status_code=409, detail=details) from None
    except Exception as exc:
        details = {"error": str(exc)[:1000]}
        _audit_archive(auth, "archive.restore", server, login, False, details)
        raise HTTPException(status_code=400, detail=details) from None
    _audit_archive(auth, "archive.restore", server, login, True, result)
    return result


@app.post("/v1/activity/desktop")
def record_desktop_activity(
    payload: DesktopActivityRequest,
    auth: AuthContext = Depends(require_auth),
) -> dict:
    server_id = _server_id(payload.server)
    _auth.audit(
        actor=auth.username,
        role=auth.role,
        source="desktop",
        action=payload.action,
        server_id=server_id,
        login=payload.login,
        success=payload.success,
        details={**dict(payload.details or {}), "server": str(payload.server or "")},
    )
    activity_bus.publish({
        "kind": "audit",
        "source": "desktop",
        "action": payload.action,
        "actor": auth.username,
        "server": payload.server,
        "login": payload.login,
        "success": payload.success,
    })
    return {"ok": True}


@app.get("/v1/activity")
def activity(
    limit: int = Query(200, ge=1, le=1000),
    login: str = Query("", max_length=128),
    source: str = Query("", max_length=64),
    auth: AuthContext = Depends(require_auth),
) -> list[dict]:
    conditions: list[str] = []
    params: list[object] = []
    if str(login or "").strip():
        conditions.append("lower(a.login) LIKE %s")
        params.append(f"%{str(login).strip().lower()}%")
    if str(source or "").strip():
        conditions.append("lower(a.source) = lower(%s)")
        params.append(str(source).strip())
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params.append(int(limit))
    with _db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT a.activity_id, a.operation_id, a.created_at, a.source,
                   a.actor, a.role, a.action, a.login, a.success, a.details,
                   s.hostname AS server
              FROM vpnsync_activity a
              LEFT JOIN vpn_servers s ON s.id = a.server_id
              {where}
             ORDER BY a.created_at DESC
             LIMIT %s
            """,
            tuple(params),
        )
        return [dict(row) for row in cur.fetchall()]


@app.get("/v1/activity/stream")
def activity_stream(auth: AuthContext = Depends(require_auth)) -> StreamingResponse:
    def stream():
        with activity_bus.subscribe() as subscriber:
            yield ": connected\n\n"
            while True:
                try:
                    item = subscriber.get(timeout=25.0)
                except queue.Empty:
                    yield ": keepalive\n\n"
                    continue
                yield "data: " + json.dumps(item, ensure_ascii=False, default=str, separators=(",", ":")) + "\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# Server-side interactive RouterOS endpoints are retained as a staged/admin API,
# but LinkVideo.Helper does not route normal search/create/edit operations here.
app.include_router(build_operations_router(require_auth, _auth, _operations))
