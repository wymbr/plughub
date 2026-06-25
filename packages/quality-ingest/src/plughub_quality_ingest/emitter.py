"""
emitter.py
Thin aiokafka producer wrapper. The module is a PURE PRODUCER of internal
canonical events — it never reads stores and never consumes. send_and_wait is used
so callers get a broker ack (deterministic for tests/smoke); a Kafka failure is
logged and counted, never raised.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger("plughub.quality_ingest.emitter")

_EMIT_TIMEOUT_S = 5.0


class KafkaEmitter:
    def __init__(self, brokers: str, *, enabled: bool = True) -> None:
        self._brokers = brokers
        self._enabled = enabled
        self._producer: Any | None = None

    async def start(self) -> None:
        if not self._enabled:
            logger.info("Kafka disabled — emitter is a no-op")
            return
        from aiokafka import AIOKafkaProducer
        self._producer = AIOKafkaProducer(bootstrap_servers=self._brokers)
        await self._producer.start()
        logger.info("Kafka emitter started (brokers=%s)", self._brokers)

    async def stop(self) -> None:
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None

    async def emit(self, topic: str, payload: dict) -> bool:
        if self._producer is None:
            logger.debug("Kafka disabled — skipping emit topic=%s", topic)
            return False
        try:
            await asyncio.wait_for(
                self._producer.send_and_wait(topic, json.dumps(payload).encode("utf-8")),
                timeout=_EMIT_TIMEOUT_S,
            )
            return True
        except Exception as exc:  # noqa: BLE001 — best-effort, never raise
            logger.warning("emit failed topic=%s: %s", topic, exc)
            return False

    async def emit_many(self, pairs: list[tuple[str, dict]]) -> int:
        sent = 0
        for topic, payload in pairs:
            if await self.emit(topic, payload):
                sent += 1
        return sent
