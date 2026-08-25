"""
config.py
Routing Engine settings loaded from environment variables.
"""

from __future__ import annotations
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PLUGHUB_", case_sensitive=False)

    # Kafka
    kafka_brokers:          str = "localhost:9092"
    kafka_group_id:         str = "routing-engine"
    kafka_topic_inbound:    str = "conversations.inbound"
    kafka_topic_routed:     str = "conversations.routed"
    kafka_topic_queued:     str = "conversations.queued"
    kafka_topic_outbound:   str = "conversations.outbound"
    # Posição na fila (analytics `queue_events`; futuros subscribers de canal).
    # Publicado no ponto pós-enqueue de main.py — ver _publish_queue_position.
    kafka_topic_queue_positions: str = "queue.position_updated"
    # Agent Registry events — populate Redis cache of pool configs and instances
    kafka_topic_lifecycle:  str = "agent.lifecycle"
    kafka_topic_registry:   str = "agent.registry.events"
    # Channel Gateway session close events — used to remove orphan sessions from queue
    kafka_topic_events:     str = "conversations.events"

    # Redis
    redis_url: str = "redis://localhost:6379"

    # Agent Registry API (used only by kafka_listener for initial fallback)
    agent_registry_url: str = "http://localhost:3300"

    # Routing
    routing_timeout_ms:             int   = 150    # spec 3.3: decision timeout
    queue_sla_factor:               float = 1.5    # spec 3.3a: congestion factor
    routing_confidence_autonomous:  float = 0.85   # autonomous AI zone
    routing_confidence_hybrid:      float = 0.60   # hybrid zone

    # Re-evaluation turns per mode
    reevaluation_turn_hybrid:       int   = 5      # re-evaluate after 5 turns in hybrid
    reevaluation_turn_supervised:   int   = 1      # re-evaluate every turn in supervised

    # Redis TTL
    # Spec: "TTL: 30s, renewed on each agent_ready or agent_busy"
    instance_ttl_seconds:   int = 30
    # ⚠️ REMOVIDA em 2026-08-25 — `pool_config_ttl_seconds` NÃO decide mais nada.
    #
    # Era env (`PLUGHUB_POOL_CONFIG_TTL_SECONDS=86400`) e valia para o único
    # escritor deste serviço, `registry.save_pool_config`. Só que a chave
    # `{t}:pool_config:{p}` tem DOIS escritores, e o outro (orchestrator-bridge)
    # a re-SETa a cada 15 s com o número dele — então este valor era sobrescrito
    # 15 segundos por vez e o conserto de `docs/guias/changelog-2026-04-16.md`
    # (300 s → 86 400) estava desfeito desde então, sem nada ficar vermelho.
    #
    # Deixar a linha aqui, mesmo correta, seria manter um botão que promete
    # efeito e não tem — a família de `sla_default_ms`. O TTL agora vem do
    # Config API (namespace `session`, `pool_config_ttl_s`), lido pelos DOIS
    # escritores: `routing_config.pool_config_ttl_s()` deste lado e
    # `instance_bootstrap._pool_config_ttl_s()` do outro. Config de tuning não
    # mora em env (§ Configuration — Single Source Invariants).
    #
    # `PLUGHUB_POOL_CONFIG_TTL_SECONDS` continua sendo aceita pelo ambiente e
    # IGNORADA — ver `ecosystem.config.js`, onde a linha foi anotada.

    # Capacity alert: time before triggering oncall (spec 3.3a)
    keda_alert_timeout_seconds: int = 60

    # Crash detection — scan interval for orphaned instances
    crash_check_interval_s: int = 15

    # Periodic queue drain — fallback for environments without agent_ready Kafka events
    # (e.g. demo/dev where Agent Assist UI connects directly to Redis pub/sub).
    # Set to 0 to disable. Default: 15s.
    queue_drain_interval_s: int = 5

    # Fase E (queue-attended-model): retention bound for queued contacts.
    # Used when the pool has no queue_config.max_wait_s (mute queues included).
    # The periodic drain closes contacts waiting longer than this with
    # close_reason=max_wait_exceeded. Set to 0 to disable the default bound
    # (pool-level max_wait_s still applies where configured).
    queue_max_wait_default_s: int = 1800

    # Grace (s) between signalling __queue_timeout__ to the queue agent and
    # closing the customer connection — gives the flow's notify time to render.
    queue_timeout_close_grace_s: int = 4

    # Config API — used to fetch routing namespace settings on startup and reload
    config_api_url:               str = "http://localhost:3600"
    kafka_topic_config_changed:   str = "config.changed"
    # Tenant usado na resolução do namespace routing (o GET exige ?tenant_id=).
    # "__global__" = defaults da instalação; o demo seta tenant_demo via env.
    tenant_id:                    str = "__global__"

    # Evaluation consumer — evaluation.requested → SkillFlowEngine
    kafka_topic_evaluation:  str = "evaluation.events"
    # HTTP endpoint of the skill-flow-service (TypeScript wrapper around SkillFlowEngine)
    skill_flow_service_url:  str = "http://localhost:3400"
    # skill_id used for the generic evaluation SkillFlow agent
    evaluation_skill_id:     str = "agente_avaliacao_v1"

    # Arc 7d — performance-based routing
    # Weight (0.0–1.0) given to historical agent performance in score_resource().
    # 0.0 = pure competency match (default — backward-compatible, no Redis reads).
    # 0.3 = 70% competency + 30% historical performance (recommended in production).
    # Sourced from PLUGHUB_PERFORMANCE_SCORE_WEIGHT; align with Config API
    # namespace "routing" key "performance_score_weight".
    performance_score_weight: float = 0.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
