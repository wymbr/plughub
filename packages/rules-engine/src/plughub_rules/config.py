"""
config.py
Rules Engine settings loaded from environment variables.
"""

from __future__ import annotations
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PLUGHUB_", case_sensitive=False)

    redis_url:         str = "redis://localhost:6379"
    mcp_server_url:    str = "http://localhost:3100"
    clickhouse_url:    str = "http://localhost:8123"
    clickhouse_db:     str = "plughub"

    # Redis channel for session updates
    redis_session_channel: str = "session:updates"

    # Rules
    rule_cache_ttl_seconds: int = 30   # per-tenant rule cache (spec: 30s)
    shadow_mode_log_table:  str = "shadow_mode_events"
    audit_log_table:        str = "escalation_audit"

    # Kafka
    kafka_broker:            str = "kafka:29092"
    kafka_topic_conversations: str = "conversations.events"
    kafka_topic_lifecycle:   str = "agent.lifecycle"
    kafka_topic_evaluation:  str = "evaluation.events"
    kafka_group_id:          str = "rules-engine"

    # Evaluation sampling
    # Redis TTL for sampling counters (seconds). 48h safety net for sessions
    # that don't produce an agent_done event.
    eval_sampling_counter_ttl: int = 172_800   # 48h

    # API
    api_port: int = 3201

    # Dry-run
    dry_run_sample_size: int = 5  # sample conversations in the result


@lru_cache
def get_settings() -> Settings:
    return Settings()
