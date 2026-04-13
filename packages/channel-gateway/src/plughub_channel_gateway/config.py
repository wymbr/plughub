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

    # Entry point configuration — the service pool this channel entry point serves.
    # Set via PLUGHUB_ENTRY_POINT_POOL_ID in the environment.
    # When set, the webchat adapter publishes a ConversationInboundEvent with
    # this pool_id on every new contact, so the Routing Engine routes immediately
    # to the correct service pool without any inference.
    # Example: "sac_ia" for the SAC AI entry point, "vendas_ia" for Sales.
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
