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

    # calendar-api: contact-window gate (Fase 3a) — is-open by calendar_id. The engine
    # never re-models "when/open"; it asks calendar-api.
    calendar_api_url: str = "http://calendar-api:3700"

    # channel-gateway Identity Resolver: opt-out global gate (Fase 3b). The customer
    # cadastro is the single source of `do_not_contact`; outbound reads/writes it here.
    identity_api_url: str = "http://channel-gateway:8010"

    # Fase 4 — file import: cap on data rows per synchronous import (413 above it).
    import_max_rows: int = 5000

    # Installation context (static per deployment)
    installation_id: str = "install-local"
    organization_id: str = "org-default"


@lru_cache
def get_settings() -> Settings:
    return Settings()
