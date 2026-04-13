"""
config.py
Conversation Writer settings via environment variables.
Spec: conversation-writer.md — Configuração section
"""

from __future__ import annotations
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PLUGHUB_", case_sensitive=False)

    # Kafka
    kafka_brokers:           str = "localhost:9092"
    kafka_group_id:          str = "conversation-writer"
    kafka_topic_inbound:     str = "conversations.inbound"
    kafka_topic_outbound:    str = "conversations.outbound"
    kafka_topic_events:      str = "conversations.events"
    kafka_topic_eval_events: str = "evaluation.events"

    # Redis
    redis_url:                  str = "redis://localhost:6379/0"
    transcript_ttl_seconds:     int = 14_400   # 4h default

    # PostgreSQL
    postgres_dsn: str = "postgresql://plughub:plughub@localhost:5432/plughub"


@lru_cache
def get_settings() -> Settings:
    return Settings()
