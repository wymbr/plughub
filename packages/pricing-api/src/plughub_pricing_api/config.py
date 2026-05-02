"""
config.py
Settings for the Pricing API service.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PLUGHUB_PRICING_", case_sensitive=False)

    # ── PostgreSQL ─────────────────────────────────────────────────────────────
    database_url: str = "postgresql://plughub:plughub@postgres:5432/plughub"

    # ── Config API (reads pricing namespace for unit prices) ───────────────────
    config_api_url: str = "http://localhost:3600"

    # ── HTTP ───────────────────────────────────────────────────────────────────
    host:    str = "0.0.0.0"
    port:    int = 3900
    workers: int = 1

    # ── Admin auth (same pattern as config-api) ────────────────────────────────
    admin_token: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
