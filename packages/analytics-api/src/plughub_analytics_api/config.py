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
    # AUT-61 — a pricing-api passou a exigir credencial também nas leituras; a capacidade
    # contratada é leitura de SERVIÇO. env `PLUGHUB_PRICING_SERVICE_TOKEN`. Vazio → 401 e o
    # denominador degrada para a provisionada, COM o warning de `pricing_client`.
    pricing_service_token: str = ""

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
    # AUT-59 — a evaluation-api passou a exigir chamador em TODA rota; a leitura de
    # cobertura se identifica como serviço. env `PLUGHUB_EVALUATION_SERVICE_TOKEN`.
    # Vazio → a chamada sai sem header, recebe 401 e o overlay degrada COM log.
    evaluation_service_token: str = ""

    # ── AUD-03 — lojas do dossiê de acesso do titular ─────────────────────────
    # Cada uma responde só a serviço. Vazio NÃO derruba o dossiê: a seção da loja sai
    # `unavailable: … not set`, nomeada — nunca vazia em silêncio.
    channel_gateway_url: str = ""
    channel_gateway_service_token: str = ""   # == PLUGHUB_CHANNEL_GATEWAY_SERVICE_TOKEN
    mailing_api_url: str = ""
    mailing_service_token: str = ""           # == PLUGHUB_MAILING_SERVICE_TOKEN
    # AUD-09 — o registro durável da sessão (stream, contexto, trajetória) é do session-replayer.
    session_replayer_url: str = ""
    session_replayer_service_token: str = ""  # == SESSION_REPLAYER_SERVICE_TOKEN do replayer

    # ── Open access (demo / dev) ──────────────────────────────────────────
    # When True, all protected endpoints return an admin principal without
    # requiring a Bearer token. NEVER enable in production.
    analytics_open_access: bool = False

    # ── Credencial de SERVIÇO (X-Service-Token) ───────────────────────────────
    # Chamadores internos (evaluation-api, agent-registry) não têm usuário: eles
    # não podem apresentar um Bearer do auth-api. Sem isto, o fechamento de
    # credencial de 2026-08-29 os deixa 401 — e três dos quatro degradavam para
    # um ZERO plausível, em silêncio.
    #
    # ⚠️ Vazio NÃO libera — e desde a AUT-59 é também a postura de `_require_service`
    # da evaluation-api (lá era no-op, herança de demo aberto): aqui o header só
    # ACRESCENTA uma porta, nunca remove a exigência. Apresentar credencial de
    # serviço a um serviço que não tem uma é erro do DEPLOY, e sai 401 nomeado.
    # env: `PLUGHUB_ANALYTICS_SERVICE_TOKEN` (o prefixo do serviço é `PLUGHUB_`).
    # Os três nomes de env deste segredo terminam igual de propósito — os prefixos
    # diferem por serviço, e segredo compartilhado com nomes que não se parecem é
    # segredo que alguém rotaciona pela metade.
    analytics_service_token: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
