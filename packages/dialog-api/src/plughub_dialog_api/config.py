"""
config.py
Settings for the PlugHub Dialog API.
All values have defaults suitable for local/visual development.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PLUGHUB_DIALOG_", case_sensitive=False)

    # HTTP
    host:    str = "0.0.0.0"
    port:    int = 3760
    workers: int = 1

    # PostgreSQL (uses the shared plughub DB, schema=dialog)
    database_url: str = "postgresql://plughub:plughub@postgres:5432/plughub"

    # Admin token for write operations (optional — omit to allow all).
    # Reads (list/get/form_get) are always open (masked-by-construction content).
    admin_token: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
