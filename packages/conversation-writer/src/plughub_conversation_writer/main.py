"""
main.py
Conversation Writer entry point.
Spec: conversation-writer.md
"""

from __future__ import annotations
import asyncio
import json as _json
import logging
import urllib.request

import redis.asyncio as aioredis
from aiokafka import AIOKafkaProducer

from .config import get_settings
from .postgres_writer import PostgresWriter
from .redis_buffer import RedisBuffer
from .writer import ConversationWriter


async def _fetch_session_ttl(config_api_url: str, default: int, logger: logging.Logger) -> int:
    """
    Fetches transcript_ttl_s from the Config API session namespace at startup.
    Uses urllib (no extra dependency) in a thread executor to stay non-blocking.
    Falls back to `default` on any error.
    """
    url = f"{config_api_url.rstrip('/')}/config/session"
    loop = asyncio.get_event_loop()

    def _get() -> int | None:
        with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310
            body = _json.loads(resp.read())
            entries = body.get("entries") or body
            entry = entries.get("transcript_ttl_s")
            if isinstance(entry, dict) and "value" in entry:
                return int(entry["value"])
            if isinstance(entry, (int, float)):
                return int(entry)
        return None

    try:
        result = await loop.run_in_executor(None, _get)
        if result is not None:
            logger.info("Session TTL loaded from Config API: transcript_ttl_s=%d", result)
            return result
    except Exception as exc:
        logger.warning(
            "Could not fetch session TTL from Config API (%s) — using fallback %ds: %s",
            url, default, exc,
        )
    return default


async def _main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    logger = logging.getLogger("plughub.conversation-writer")

    settings = get_settings()

    # Resolve transcript TTL dynamically from Config API; fall back to settings default.
    transcript_ttl = await _fetch_session_ttl(
        settings.config_api_url, settings.transcript_ttl_seconds, logger
    )

    redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
    buffer = RedisBuffer(redis=redis_client, ttl=transcript_ttl)

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
