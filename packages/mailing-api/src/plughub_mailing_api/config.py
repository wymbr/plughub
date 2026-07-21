"""
config.py
Settings for the PlugHub Outbound Mailing API.
All values have defaults suitable for the local demo compose.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PLUGHUB_MAILING_", case_sensitive=False)

    # HTTP
    host:    str = "0.0.0.0"
    port:    int = 3660
    workers: int = 1

    # PostgreSQL (shared plughub DB, schema=outbound — canonical store of the domain)
    database_url: str = "postgresql://plughub:plughub@postgres:5432/plughub"

    # Installation context (static per deployment)
    installation_id: str = "install-local"
    organization_id: str = "org-default"


@lru_cache
def get_settings() -> Settings:
    return Settings()
