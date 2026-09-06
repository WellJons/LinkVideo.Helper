from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .activity_bus import activity_bus
from .auth import AuthContext


class CreateClientsRequest(BaseModel):
    server: str = Field(..., min_length=1, max_length=255)
    base_login: str = Field(..., min_length=1, max_length=128)
    ports_per_client: int = Field(..., ge=1, le=14)
    accounts_count: int = Field(1, ge=1, le=20)


class AddPortsRequest(BaseModel):
    server: str = Field(..., min_length=1, max_length=255)
    login: str = Field(..., min_length=1, max_length=128)
    count: int = Field(..., ge=1, le=14)


class PortRequest(BaseModel):
    server: str = Field(..., min_length=1, max_length=255)
    login: str = Field(..., min_length=1, max_length=128)
    port: int = Field(..., ge=1, le=65535)


class PasswordRequest(BaseModel):
    server: str = Field(..., min_length=1, max_length=255)
    login: str = Field(..., min_length=1, max_length=128)
    password: str = Field(..., min_length=1, max_length=512)


class EnabledRequest(BaseModel):
    server: str = Field(..., min_length=1, max_length=255)
    login: str = Field(..., min_length=1, max_length=128)
    enabled: bool


class PortEnabledRequest(PortRequest):
    enabled: bool


class ClientRequest(BaseModel):
    server: str = Field(..., min_length=1, max_length=255)
    login: str = Field(..., min_length=1, max_length=128)


def build_operations_router(require_auth, auth_service, operations) -> APIRouter:
    router = APIRouter(prefix="/v1/operations", tags=["operations"])

    def can_write(auth: AuthContext = Depends(require_auth)) -> AuthContext:
        if auth.role not in {"operator", "admin"}:
            raise HTTPException(status_code=403, detail="Write permission required")
        return auth

    def audit(
        auth: AuthContext,
        action: str,
        *,
        server: str,
        login: str = "",
        success: bool,
        details: dict[str, Any] | None = None,
    ) -> None:
        server_id = None
        try:
            with operations.db.connection() as conn, conn.cursor() as cur:
                cur.execute("SELECT id FROM vpn_servers WHERE lower(hostname)=lower(%s)", (server,))
                row = cur.fetchone()
                server_id = int(row["id"]) if row else None
            auth_service.audit(
                actor=auth.username,
                role=auth.role,
                source="desktop",
                action=action,
                server_id=server_id,
                login=login,
                success=success,
                details=dict(details or {}),
            )
        finally:
            activity_bus.publish({
                "kind": "audit",
                "source": "desktop",
                "action": action,
                "actor": auth.username,
                "server": server,
                "login": login,
                "success": success,
            })

    def run(
        auth: AuthContext,
        action: str,
        server: str,
        login: str,
        fn: Callable[[], Any],
        *,
        success_details: Callable[[Any], dict[str, Any]] | None = None,
    ) -> Any:
        try:
            result = fn()
        except ValueError as exc:
            audit(auth, action, server=server, login=login, success=False, details={"error": str(exc)[:800]})
            raise HTTPException(status_code=400, detail=str(exc)) from None
        except RuntimeError as exc:
            audit(auth, action, server=server, login=login, success=False, details={"error": str(exc)[:800]})
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except Exception as exc:
            audit(auth, action, server=server, login=login, success=False, details={"error": str(exc)[:800]})
            raise HTTPException(status_code=502, detail=f"RouterOS operation failed: {exc}") from None
        details = success_details(result) if success_details is not None else {}
        audit(auth, action, server=server, login=login, success=True, details=details)
        return result

    @router.post("/clients/create")
    def create_clients(payload: CreateClientsRequest, auth: AuthContext = Depends(can_write)):
        return run(
            auth,
            "client.create",
            payload.server,
            payload.base_login,
            lambda: operations.create_clients(
                payload.server,
                payload.base_login,
                payload.ports_per_client,
                payload.accounts_count,
            ),
            success_details=lambda rows: {
                "count": len(rows or []),
                "logins": [str(row.get("login") or "") for row in list(rows or [])],
            },
        )

    @router.post("/ports/add")
    def add_ports(payload: AddPortsRequest, auth: AuthContext = Depends(can_write)):
        return run(
            auth, "nat.add_ports", payload.server, payload.login,
            lambda: operations.add_ports(payload.server, payload.login, payload.count),
            success_details=lambda _row: {"count": payload.count},
        )

    @router.post("/ports/remove")
    def remove_port(payload: PortRequest, auth: AuthContext = Depends(can_write)):
        return run(
            auth, "nat.remove_port", payload.server, payload.login,
            lambda: operations.remove_port(payload.server, payload.login, payload.port),
            success_details=lambda _row: {"port": payload.port},
        )

    @router.post("/clients/password")
    def set_password(payload: PasswordRequest, auth: AuthContext = Depends(can_write)):
        return run(
            auth, "client.password_change", payload.server, payload.login,
            lambda: operations.set_password(payload.server, payload.login, payload.password),
        )

    @router.post("/clients/enabled")
    def set_client_enabled(payload: EnabledRequest, auth: AuthContext = Depends(can_write)):
        return run(
            auth, "client.enabled_change", payload.server, payload.login,
            lambda: operations.set_secret_enabled(payload.server, payload.login, payload.enabled),
            success_details=lambda _row: {"enabled": payload.enabled},
        )

    @router.post("/ports/enabled")
    def set_port_enabled(payload: PortEnabledRequest, auth: AuthContext = Depends(can_write)):
        return run(
            auth, "nat.enabled_change", payload.server, payload.login,
            lambda: operations.set_port_enabled(payload.server, payload.login, payload.port, payload.enabled),
            success_details=lambda _row: {"port": payload.port, "enabled": payload.enabled},
        )

    @router.post("/ports/recreate")
    def recreate_port(payload: PortRequest, auth: AuthContext = Depends(can_write)):
        return run(
            auth, "nat.recreate", payload.server, payload.login,
            lambda: operations.recreate_port(payload.server, payload.login, payload.port),
            success_details=lambda _row: {"port": payload.port},
        )

    @router.post("/clients/delete")
    def delete_client(payload: ClientRequest, auth: AuthContext = Depends(can_write)):
        run(
            auth, "client.delete", payload.server, payload.login,
            lambda: operations.delete_client(payload.server, payload.login, actor=auth.username),
        )
        return {"ok": True}

    @router.post("/clients/disconnect")
    def disconnect_client(payload: ClientRequest, auth: AuthContext = Depends(can_write)):
        changed = run(
            auth, "client.disconnect", payload.server, payload.login,
            lambda: operations.disconnect_client(payload.server, payload.login),
            success_details=lambda value: {"disconnected": bool(value)},
        )
        return {"ok": True, "disconnected": bool(changed)}

    return router
