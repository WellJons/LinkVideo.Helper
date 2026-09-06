from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_ROUTEROS_SERVERS = ",".join([
    *(f"vpn{i:02d}.linkvideo.ru" for i in range(1, 11)),
    "rb-vpn01.linkvideo.ru",
    "kz-vpn01.linkvideo.ru",
])


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=("/etc/linkvideo-vpnsync/vpnsync.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bind_host: str = Field("127.0.0.1", alias="VPNSYNC_BIND_HOST")
    bind_port: int = Field(8787, alias="VPNSYNC_BIND_PORT")
    api_token: str = Field(..., min_length=24, alias="VPNSYNC_API_TOKEN")
    session_ttl_seconds: int = Field(43200, ge=300, le=604800, alias="VPNSYNC_SESSION_TTL_SECONDS")

    database_url: str = Field(..., alias="DATABASE_URL")
    encryption_key: str = Field(..., min_length=24, alias="VPNSYNC_ENCRYPTION_KEY")

    # All lifecycle/deadline calculations are performed in the LinkVideo
    # business timezone. +03:00 is intentionally fixed and must not depend on
    # the Linux host timezone (the current server itself is +07:00).
    business_utc_offset_hours: int = Field(3, ge=-12, le=14, alias="VPNSYNC_BUSINESS_UTC_OFFSET_HOURS")

    # RouterOS is enabled only after PostgreSQL migration is validated. A
    # per-server JSON file can override the common credentials below.
    routeros_username: str = Field("", alias="ROUTEROS_USERNAME")
    routeros_password: str = Field("", alias="ROUTEROS_PASSWORD")
    routeros_api_port: int = Field(8728, alias="ROUTEROS_API_PORT")
    routeros_timeout: float = Field(6.0, alias="ROUTEROS_TIMEOUT")
    routeros_servers: str = Field(DEFAULT_ROUTEROS_SERVERS, alias="ROUTEROS_SERVERS")
    routeros_servers_file: str = Field("/etc/linkvideo-vpnsync/routers.json", alias="ROUTEROS_SERVERS_FILE")
    routeros_monitor_enabled: bool = Field(False, alias="ROUTEROS_MONITOR_ENABLED")

    # Destructive retention remains off until the central DB/listener has been
    # validated against the live fleet. Enabling it is a separate rollout step.
    retention_enabled: bool = Field(False, alias="VPNSYNC_RETENTION_ENABLED")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
