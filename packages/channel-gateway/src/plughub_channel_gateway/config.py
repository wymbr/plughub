"""
config.py
Channel Gateway settings loaded from environment variables.
Spec: PlugHub v24.0 section 3.5
"""

from __future__ import annotations
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PLUGHUB_", case_sensitive=False)

    # Kafka
    kafka_brokers:              str = "localhost:9092"
    kafka_group_id:             str = "channel-gateway-webchat"
    kafka_topic_inbound:        str = "conversations.inbound"
    kafka_topic_outbound:       str = "conversations.outbound"
    kafka_topic_events:         str = "conversations.events"

    # Redis
    redis_url:                  str = "redis://localhost:6379"

    # Entry point pool — backward-compat fallback for single-pool deployments.
    # The preferred way to set the pool is via the URL path: /ws/chat/{pool_id}.
    # This env var is only used when pool_id is absent from the URL (e.g. older
    # docker-compose configs that use a fixed /ws/chat endpoint).
    # Set via PLUGHUB_ENTRY_POINT_POOL_ID.
    # Example: "sac_ia" — clients connecting to /ws/chat are routed to sac_ia.
    entry_point_pool_id:        str = ""

    # Tenant identifier published in routing events.
    # Defaults to the Kafka group_id for backward compatibility.
    tenant_id:                  str = "default"

    # WebSocket
    ws_heartbeat_interval_s:    int = 30
    ws_connection_timeout_s:    int = 300   # close if idle for 5 min
    ws_contact_max_duration_s:  int = 14400 # 4h max contact duration

    # Session Redis TTL (matches contact max duration)
    session_ttl_seconds:        int = 14400


@lru_cache
def get_settings() -> Settings:
    return Settings()
