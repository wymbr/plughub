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
    # Agent Registry events — populate Redis cache of pool configs and instances
    kafka_topic_lifecycle:  str = "agent.lifecycle"
    kafka_topic_registry:   str = "agent.registry.events"

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
    # Pool configuration cache — renewed when agent-registry publishes an event
    pool_config_ttl_seconds: int = 300   # 5 min

    # Capacity alert: time before triggering oncall (spec 3.3a)
    keda_alert_timeout_seconds: int = 60

    # Crash detection — scan interval for orphaned instances
    crash_check_interval_s: int = 15

    # Evaluation consumer — evaluation.requested → SkillFlowEngine
    kafka_topic_evaluation:  str = "evaluation.events"
    # HTTP endpoint of the skill-flow-service (TypeScript wrapper around SkillFlowEngine)
    skill_flow_service_url:  str = "http://localhost:3400"
    # skill_id used for the generic evaluation SkillFlow agent
    evaluation_skill_id:     str = "agente_avaliacao_v1"


@lru_cache
def get_settings() -> Settings:
    return Settings()
