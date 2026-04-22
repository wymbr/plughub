"""
config.py
Settings for the Workflow API.
All values are read from environment variables prefixed with PLUGHUB_WORKFLOW_.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Server
    host:    str = "0.0.0.0"
    port:    int = 3800
    workers: int = 1

    # Identity (static installation config — not tenant-scoped)
    installation_id: str = "installation-001"
    organization_id: str = "org-001"

    # PostgreSQL
    database_url: str = "postgresql://plughub:plughub@localhost:5432/plughub"

    # Kafka
    kafka_brokers:   str  = "localhost:9092"    # comma-separated
    kafka_topic:     str  = "workflow.events"
    kafka_enabled:   bool = True

    # Calendar API (for business-hours deadline calculation)
    calendar_api_url: str = "http://localhost:3700"

    # Timeout scanner interval in seconds
    timeout_scan_interval_s: int = 60

    # Admin token for protected operations (optional)
    admin_token: str = ""

    model_config = {"env_prefix": "PLUGHUB_WORKFLOW_"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
