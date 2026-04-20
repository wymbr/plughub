"""main.py — ClickHouse Consumer entry point."""

from __future__ import annotations
import asyncio
import logging

from .clickhouse_writer import ClickHouseWriter
from .config import get_settings
from .consumer import ClickHouseConsumer


async def _main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    logger = logging.getLogger("plughub.clickhouse-consumer")
    settings = get_settings()

    writer = ClickHouseWriter.create(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        database=settings.clickhouse_database,
        username=settings.clickhouse_user,
        password=settings.clickhouse_password,
    )
    writer.migrate()

    consumer = ClickHouseConsumer(writer=writer, settings=settings)
    logger.info("✅ ClickHouse Consumer starting…")
    await consumer.run()


def run() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    run()
