"""
config.py
Settings for the PlugHub Scheduler / Agenda API.
All values have defaults suitable for the local demo compose.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PLUGHUB_SCHEDULER_", case_sensitive=False)

    # HTTP
    host:    str = "0.0.0.0"
    port:    int = 3650
    workers: int = 1

    # PostgreSQL (shared plughub DB, schema=scheduler — Camada 2, fonte de verdade)
    database_url: str = "postgresql://plughub:plughub@postgres:5432/plughub"

    # Redis — Camada 1: sorted-set de deadlines + hash de payload de disparo
    redis_url: str = "redis://redis:6379"

    # Downstream services
    # calendar-api: engine puro do "quando" (is_open / next_open_slot) — business_day_policy
    calendar_api_url:    str = "http://calendar-api:3700"
    # channel-gateway: disparo do webhook do pool (Arc 19) — POST /v1/channels/webhook/pool/{id}
    channel_gateway_url: str = "http://channel-gateway:8010"
    # agent-registry: validação de pool webhook / metadados de pool
    agent_registry_url:  str = "http://agent-registry:3300"

    # Poller (Camada 1)
    poll_interval_s:    int = 15
    dispatch_timeout_s: int = 20
    # Default quando a agenda não declara (schema também tem default "skip").
    default_misfire_policy: str = "skip"

    # Installation context (static per deployment)
    installation_id:  str = "install-local"
    organization_id:  str = "org-default"
    default_timezone: str = "America/Sao_Paulo"

    # Admin token for write operations (optional — omit to allow all)
    admin_token: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
