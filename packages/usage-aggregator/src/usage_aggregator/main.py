"""
main.py
Entrypoint do Usage Aggregator.
Lê configuração de variáveis de ambiente e inicia o Kafka consumer.
"""
from __future__ import annotations

import asyncio
import logging
import os

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)

from .consumer import run_consumer

KAFKA_BROKERS = os.getenv("KAFKA_BROKERS", "kafka:9092")
REDIS_URL     = os.getenv("REDIS_URL",     "redis://redis:6379")
DATABASE_URL  = os.getenv("DATABASE_URL",  "postgresql://plughub:plughub@postgres:5432/plughub_demo")


def main() -> None:
    asyncio.run(run_consumer(
        kafka_brokers=KAFKA_BROKERS,
        redis_url=REDIS_URL,
        database_url=DATABASE_URL,
    ))


if __name__ == "__main__":
    main()
