"""
consumer.py
ClickHouse Consumer — Kafka consumer for evaluation.results.
Spec: clickhouse-consumer.md
"""

from __future__ import annotations
import json
import logging

from aiokafka import AIOKafkaConsumer

from .clickhouse_writer import ClickHouseWriter
from .config import Settings

logger = logging.getLogger("plughub.clickhouse-consumer")


class ClickHouseConsumer:
    def __init__(self, writer: ClickHouseWriter, settings: Settings) -> None:
        self._writer   = writer
        self._settings = settings

    async def run(self) -> None:
        consumer = AIOKafkaConsumer(
            self._settings.kafka_topic_eval_results,
            bootstrap_servers=self._settings.kafka_brokers,
            group_id=self._settings.kafka_group_id,
            auto_offset_reset="earliest",
            enable_auto_commit=False,   # manual commit after successful write
        )
        await consumer.start()
        logger.info(
            "ClickHouse Consumer started — topic=%s",
            self._settings.kafka_topic_eval_results,
        )
        try:
            async for msg in consumer:
                payload = json.loads(msg.value.decode())
                if payload.get("event_type") != "evaluation.completed":
                    await consumer.commit()
                    continue
                try:
                    self._writer.write_evaluation(payload)
                    await consumer.commit()
                except Exception as exc:
                    logger.error(
                        "Failed to persist evaluation_id=%s: %s — will retry on restart",
                        payload.get("evaluation_id"), exc, exc_info=True,
                    )
                    # Do NOT commit — offset will be re-delivered on restart
        finally:
            await consumer.stop()
