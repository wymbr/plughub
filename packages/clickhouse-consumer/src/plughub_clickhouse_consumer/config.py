"""config.py — ClickHouse Consumer settings."""

from __future__ import annotations
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PLUGHUB_", case_sensitive=False)

    # Kafka
    kafka_brokers:              str = "localhost:9092"
    kafka_group_id:             str = "clickhouse-consumer"
    kafka_topic_eval_results:   str = "evaluation.results"

    # ClickHouse
    clickhouse_host:     str = "localhost"
    clickhouse_port:     int = 8123
    clickhouse_database: str = "plughub"
    clickhouse_user:     str = "default"
    clickhouse_password: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
