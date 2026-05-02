"""
config.py
Settings for the Config API service (env vars only — infra endpoints and secrets).
"""
from __future__ import annotations
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PLUGHUB_CONFIG_", case_sensitive=False)

    # ── PostgreSQL ────────────────────────────────────────────────────────────
    database_url: str = "postgresql://plughub:plughub@postgres:5432/plughub"

    # ── Redis ─────────────────────────────────────────────────────────────────
    redis_url: str = "redis://redis:6379"

    # ── Cache ─────────────────────────────────────────────────────────────────
    cache_ttl_s: int = 60   # how long resolved values are cached in Redis

    # ── HTTP ──────────────────────────────────────────────────────────────────
    host:    str = "0.0.0.0"
    port:    int = 3600
    workers: int = 1

    # ── Kafka ─────────────────────────────────────────────────────────────────
    # Comma-separated broker list. Leave empty to disable config.changed events
    # (dev / unit-test mode). Example: "kafka:9092"
    kafka_brokers: str = ""

    @property
    def kafka_brokers_list(self) -> list[str]:
        return [b.strip() for b in self.kafka_brokers.split(",") if b.strip()]

    # ── Admin auth ────────────────────────────────────────────────────────────
    # Static token for write operations (X-Admin-Token header).
    # Leave empty to disable auth (internal-only deployments).
    admin_token: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
