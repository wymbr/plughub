"""
config.py
Analytics API settings loaded from environment variables.
All env vars are prefixed with PLUGHUB_.
"""
from __future__ import annotations
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PLUGHUB_", case_sensitive=False)

    # ── Kafka ─────────────────────────────────────────────────────────────────
    kafka_brokers:    str = "kafka:9092"
    kafka_group_id:   str = "analytics-api"

    # ── ClickHouse ────────────────────────────────────────────────────────────
    clickhouse_host:     str = "clickhouse"
    clickhouse_port:     int = 8123
    clickhouse_user:     str = "plughub"
    clickhouse_password: str = "plughub"
    clickhouse_database: str = "plughub"

    # ── Redis (for health check + future SSE) ─────────────────────────────────
    redis_url: str = "redis://redis:6379"

    # ── HTTP ──────────────────────────────────────────────────────────────────
    port:    int = 3500
    host:    str = "0.0.0.0"
    workers: int = 1

    # ── Consumer behaviour ────────────────────────────────────────────────────
    consumer_batch_size:    int = 200   # max records per getmany() call
    consumer_timeout_ms:    int = 500   # getmany() poll timeout

    # ── Admin auth (JWT HS256) ────────────────────────────────────────────────
    # In production, replace with a strong random secret.
    admin_jwt_secret: str = "changeme_analytics_admin_secret"

    # ── Auth-API JWT secret (Arc 7c — pool-scoped visibility) ─────────────────
    # Must match PLUGHUB_AUTH_JWT_SECRET used by auth-api.
    # When set, Bearer tokens from auth-api are verified and accessible_pools[]
    # is extracted to restrict report queries to the caller's allowed pools.
    # When empty, pool scoping is disabled (all pools visible — dev / open-access).
    auth_jwt_secret: str = ""

    # ── Open access (demo / dev) ──────────────────────────────────────────
    # When True, all protected endpoints return an admin principal without
    # requiring a Bearer token. NEVER enable in production.
    analytics_open_access: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
