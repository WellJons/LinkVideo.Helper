from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query

from .config import get_settings
from .db import VPNDatabase


_settings = get_settings()
_db = VPNDatabase(_settings.database_url, _settings.encryption_key)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _db.open()
    try:
        yield
    finally:
        _db.close()


app = FastAPI(
    title="LinkVideo.VPNSync",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)


def require_token(authorization: Annotated[str | None, Header()] = None) -> None:
    expected = f"Bearer {_settings.api_token}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.get("/health")
def health() -> dict:
    info = _db.ping()
    return {
        "ok": True,
        "database": info.get("database"),
        "database_time": info.get("now"),
    }


@app.get("/v1/servers", dependencies=[Depends(require_token)])
def servers() -> list[dict]:
    return _db.list_servers()


@app.get("/v1/clients/search", dependencies=[Depends(require_token)])
def search_clients(
    q: str = Query(..., min_length=1, max_length=128),
    limit: int = Query(50, ge=1, le=200),
) -> list[dict]:
    return _db.search_clients(q, limit=limit)


@app.get("/v1/deleted/search", dependencies=[Depends(require_token)])
def search_deleted(
    q: str = Query(..., min_length=1, max_length=128),
    limit: int = Query(50, ge=1, le=200),
) -> list[dict]:
    return _db.search_deleted_clients(q, limit=limit)
