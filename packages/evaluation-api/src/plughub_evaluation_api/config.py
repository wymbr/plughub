"""config.py — pydantic-settings config for evaluation-api."""
from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://plughub:plughub@localhost:5432/plughub"
    redis_url: str = "redis://localhost:6379/0"
    kafka_brokers: str = "localhost:9092"
    admin_token: str = ""

    # Kafka topics
    evaluation_topic: str = "evaluation.events"
    workflow_events_topic: str = "workflow.events"

    # Calendar API (for business-hours deadline calculation)
    calendar_api_url: str = "http://localhost:3700"

    # Session Replayer (to fetch ReplayContext)
    session_replayer_url: str = "http://localhost:3300"  # mcp-server-plughub

    # Workflow API (Arc 4 — review/contestation state machine)
    workflow_api_url: str = "http://localhost:3800"

    # Knowledge API (mcp-server-knowledge — Arc 13 Fase H CalibrationNote publish)
    knowledge_api_url: str = "http://localhost:3401"

    # JWT secret for reviewer/contestation identity (HS256)
    jwt_secret: str = "changeme_evaluation_jwt_secret"

    # ContextStore TTL for evaluation workflow fields (7 days — longer than session TTL)
    workflow_context_ttl_s: int = 604800

    # Sampling defaults
    default_sample_rate: float = 0.1   # 10% of sessions
    default_instance_ttl_hours: int = 72

    # S2.2 dispatcher — pool do agente avaliador quando a campanha não define
    # evaluator_pool. Fallback global (mesmo default do session-replayer).
    default_evaluator_pool: str = "avaliacao_ia"

    # T15 — dispatcher por janela de calendário (§18.4)
    dispatch_scanner_enabled: bool      = True   # liga a tarefa de fundo
    dispatch_scanner_interval_s: int    = 60     # período de varredura
    dispatch_redispatch_cooldown_s: int = 3600   # não re-despacha scheduled dentro disso
    dispatch_batch_limit: int           = 100    # máx. instances por campanha/ciclo

    port: int = 3400

    model_config = {"env_prefix": "PLUGHUB_EVALUATION_"}


settings = Settings()
