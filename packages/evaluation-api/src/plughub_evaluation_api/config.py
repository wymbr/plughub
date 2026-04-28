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

    # Calendar API (for business-hours deadline calculation)
    calendar_api_url: str = "http://localhost:3700"

    # Session Replayer (to fetch ReplayContext)
    session_replayer_url: str = "http://localhost:3300"  # mcp-server-plughub

    # Sampling defaults
    default_sample_rate: float = 0.1   # 10% of sessions
    default_instance_ttl_hours: int = 72

    port: int = 3400

    model_config = {"env_prefix": "PLUGHUB_EVALUATION_"}


settings = Settings()
