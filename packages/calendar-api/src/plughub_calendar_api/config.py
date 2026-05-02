"""
config.py
Settings for the PlugHub Calendar API.
All values have defaults suitable for local/visual development.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PLUGHUB_CALENDAR_", case_sensitive=False)

    # HTTP
    host:    str = "0.0.0.0"
    port:    int = 3700
    workers: int = 1

    # PostgreSQL (uses the shared plughub DB, schema=calendar)
    database_url: str = "postgresql://plughub:plughub@postgres:5432/plughub"

    # Redis — cache for engine results (TTL 60s)
    redis_url:    str = "redis://redis:6379"
    cache_ttl_s:  int = 60

    # Kafka — for calendar.window_opened / calendar.window_closed events
    kafka_brokers: str = "kafka:29092"
    kafka_topic:   str = "calendar.events"

    # Installation context (static per deployment)
    installation_id:  str = "install-local"
    organization_id:  str = "org-default"
    default_timezone: str = "America/Sao_Paulo"

    # Background task: how often to check for window transitions (seconds)
    window_check_interval_s: int = 60

    # Admin token for write operations (optional — omit to allow all)
    admin_token: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
