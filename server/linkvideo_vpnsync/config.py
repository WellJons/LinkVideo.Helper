from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bind_host: str = Field("127.0.0.1", alias="VPNSYNC_BIND_HOST")
    bind_port: int = Field(8787, alias="VPNSYNC_BIND_PORT")
    api_token: str = Field(..., min_length=24, alias="VPNSYNC_API_TOKEN")

    database_url: str = Field(..., alias="DATABASE_URL")
    encryption_key: str = Field(..., min_length=24, alias="VPNSYNC_ENCRYPTION_KEY")

    routeros_username: str = Field(..., alias="ROUTEROS_USERNAME")
    routeros_password: str = Field(..., alias="ROUTEROS_PASSWORD")
    routeros_api_port: int = Field(8728, alias="ROUTEROS_API_PORT")
    routeros_timeout: float = Field(6.0, alias="ROUTEROS_TIMEOUT")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
