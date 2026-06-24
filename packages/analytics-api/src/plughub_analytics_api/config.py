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
    kafka_dlq_topic:        str = "events.dead_letter"

    # ── Admin auth (JWT HS256) ────────────────────────────────────────────────
    # In production, replace with a strong random secret.
    admin_jwt_secret: str = "changeme_analytics_admin_secret"

    # ── Auth-API JWT secret (Arc 7c — pool-scoped visibility) ─────────────────
    # Must match PLUGHUB_AUTH_JWT_SECRET used by auth-api.
    # When set, Bearer tokens from auth-api are verified and accessible_pools[]
    # is extracted to restrict report queries to the caller's allowed pools.
    # When empty, pool scoping is disabled (all pools visible — dev / open-access).
    auth_jwt_secret: str = ""

    # ── Pricing API (Fase 2 — Pools/Infra: capacidade configurada) ────────────
    # Quando setado, /reports/pools/occupancy usa a capacidade-base configurada
    # no pricing como denominador do TOTAL (per-pool segue a provisionada).
    # Vazio ou indisponível → degrada graciosamente para a provisionada.
    pricing_api_url: str = ""

    # ── Agent Registry (Arc 6 Fase 2 — lente `deploy` no bench) ───────────────
    # Origem do deploy timeline (skill_deployments) lido em query-time pela lente
    # `deploy` de /reports/agents/compare. D1 da spec: REST no agent-registry, sem
    # tabela/consumer. Indisponível → série sem markers (degrada, nunca 500).
    agent_registry_url: str = "http://localhost:3300"

    # ── Config API (R8b — tuning de calibração em tempo de request) ───────────
    # Lê settings horizontais do namespace `evaluation` (limiar de divergência,
    # N mínimo). Vazio ou indisponível → usa os defaults do código (0.25 / 30).
    config_api_url: str = ""

    # ── Evaluation API (micro-fatia 1b — cobertura/pendentes do epoch) ────────
    # Origem da nota PROVISÓRIA + backlog (instâncias amostradas não finalizadas)
    # por (pool, deploy_version), lida em query-time pela lente `deploy&mode=epoch`.
    # Indisponível → epoch sem overlay provisório/pendentes (degrada, nunca 500).
    evaluation_api_url: str = ""

    # ── Open access (demo / dev) ──────────────────────────────────────────
    # When True, all protected endpoints return an admin principal without
    # requiring a Bearer token. NEVER enable in production.
    analytics_open_access: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
