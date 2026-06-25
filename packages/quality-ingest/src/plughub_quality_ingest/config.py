"""
config.py
Settings for the Quality Ingest service.
All values are read from environment variables prefixed with PLUGHUB_QUALITY_INGEST_.

env is for secrets and wiring only (CLAUDE.md invariant). Business/tuning config
(identity/pool/version maps per source) lives in the Config API — wired in R13c.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Server
    host:    str = "0.0.0.0"
    port:    int = 3850
    workers: int = 1

    # Kafka (wiring)
    kafka_brokers: str  = "localhost:9092"   # comma-separated
    kafka_enabled: bool = True

    # Canonical internal topics this module emits onto (the ONLY infra it touches,
    # via the producer — it never reads stores). Names match the live producers.
    topic_events:        str = "conversations.events"
    topic_participants:  str = "conversations.participants"
    topic_lifecycle:     str = "agent.lifecycle"
    topic_session_closed: str = "conversations.session_closed"

    # Marker stamped on every emitted canonical event so the R13b consumer Y can
    # gate stream reconstruction to imported contacts. Ignored by live consumers.
    import_source_marker: str = "external_import"

    # Config API (R13c — per-source identity/pool/version map). Wiring only.
    config_api_url:          str = "http://localhost:3600"
    source_map_cache_ttl_s:  int = 60

    model_config = {"env_prefix": "PLUGHUB_QUALITY_INGEST_"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
