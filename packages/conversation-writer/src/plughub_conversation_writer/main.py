"""
main.py
Conversation Writer entry point.
Spec: conversation-writer.md
"""

from __future__ import annotations
import asyncio
import logging

import redis.asyncio as aioredis
from aiokafka import AIOKafkaProducer

from .config import get_settings
from .postgres_writer import PostgresWriter
from .redis_buffer import RedisBuffer
from .writer import ConversationWriter


async def _main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    logger = logging.getLogger("plughub.conversation-writer")

    settings = get_settings()

    redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
    buffer = RedisBuffer(redis=redis_client, ttl=settings.transcript_ttl_seconds)

    db = await PostgresWriter.create(settings.postgres_dsn)
    await db.migrate()

    producer = AIOKafkaProducer(bootstrap_servers=settings.kafka_brokers)
    await producer.start()

    writer = ConversationWriter(
        buffer=buffer,
        db=db,
        producer=producer,
        settings=settings,
    )

    logger.info("✅ Conversation Writer starting…")
    try:
        await writer.run()
    finally:
        await producer.stop()
        await db.close()
        await redis_client.aclose()
        logger.info("Conversation Writer stopped")


def run() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    run()
