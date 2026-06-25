"""
config.py
Settings for the Quality Export service (R13d).
env-prefixed with PLUGHUB_QUALITY_EXPORT_. env is for wiring only.

The exporter reads internal history (ClickHouse) and re-emits it through the
quality-ingest contract (the SAME open endpoint the external importer uses). It
never touches internal event topics/stores for writing — it only reads history and
posts ingestion_event_v1.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Server
    host:    str = "0.0.0.0"
    port:    int = 3852
    workers: int = 1

    # ClickHouse (read-only history source) — HTTP interface
    clickhouse_url:      str = "http://localhost:8123"
    clickhouse_db:       str = "plughub"
    clickhouse_user:     str = "plughub"
    clickhouse_password: str = "plughub"

    # quality-ingest contract endpoint (the same port the external importer posts to)
    quality_ingest_url:  str = "http://localhost:3850"

    # Default `source` stamped on re-emitted events. A source with no source_map
    # entry → pass-through (reuses the original internal pool/identity — §7 decision).
    # To send re-evaluation to a DEDICATED pool, register a source_map for this
    # source (R13c) mapping the original pools → the review pool — no new mechanism.
    export_source:       str = "internal:reeval"

    model_config = {"env_prefix": "PLUGHUB_QUALITY_EXPORT_"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
