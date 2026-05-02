"""
kafka_emitter.py
Publishes config.changed events to Kafka whenever a config value is written
or deleted via the Config API.

Event schema:
  {
    "event":      "config.changed",
    "tenant_id":  "tenant_demo" | "__global__",
    "namespace":  "routing",
    "key":        "sla_default_ms",
    "operation":  "set" | "delete",
    "updated_at": "2026-01-01T00:00:00Z"
  }

Consumers:
  orchestrator-bridge  — namespace=quota   → bootstrap.request_refresh()
  routing-engine       — namespace=routing → invalidate local SLA/scoring cache
  mcp-server-plughub   — namespace=masking → invalidate MaskingConfig cache

Design notes:
  - Fire-and-forget: publish failures are logged but never propagate to the
    HTTP response. A config write must not fail because Kafka is unavailable.
  - Singleton producer created once at app startup, shared across requests.
  - If kafka_brokers is empty the emitter is a no-op (dev / unit-test mode).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("plughub.config.kafka")

# Lazy import so the module loads cleanly when aiokafka is not installed
try:
    from aiokafka import AIOKafkaProducer  # type: ignore
    _AIOKAFKA_AVAILABLE = True
except ImportError:
    _AIOKAFKA_AVAILABLE = False

TOPIC_CONFIG_CHANGED = "config.changed"


class ConfigKafkaEmitter:
    """
    Thin wrapper around an AIOKafkaProducer.

    Lifecycle — call from FastAPI lifespan:
        emitter = ConfigKafkaEmitter(brokers)
        await emitter.start()
        ...
        await emitter.stop()
    """

    def __init__(self, brokers: list[str]) -> None:
        self._brokers  = brokers
        self._producer: Optional[object] = None   # AIOKafkaProducer or None

    @property
    def enabled(self) -> bool:
        return bool(self._brokers) and _AIOKAFKA_AVAILABLE

    async def start(self) -> None:
        if not self.enabled:
            if self._brokers and not _AIOKAFKA_AVAILABLE:
                logger.warning(
                    "kafka_brokers is set but aiokafka is not installed — "
                    "config.changed events will NOT be published"
                )
            return
        producer = AIOKafkaProducer(
            bootstrap_servers=self._brokers,
            value_serializer=lambda v: json.dumps(v).encode(),
            acks="all",
            enable_idempotence=True,
        )
        await producer.start()
        self._producer = producer
        logger.info(
            "ConfigKafkaEmitter started — brokers=%s topic=%s",
            self._brokers, TOPIC_CONFIG_CHANGED,
        )

    async def stop(self) -> None:
        if self._producer is not None:
            try:
                await self._producer.stop()  # type: ignore[union-attr]
            except Exception as exc:
                logger.warning("ConfigKafkaEmitter stop error: %s", exc)
            self._producer = None

    async def emit_config_changed(
        self,
        *,
        tenant_id: Optional[str],
        namespace: str,
        key:       str,
        operation: str,   # "set" | "delete"
    ) -> None:
        """
        Publishes a config.changed event. Never raises — failures are logged only.

        tenant_id=None means the global default was changed; the event is still
        published with tenant_id="__global__" so consumers can filter correctly.
        """
        if self._producer is None:
            return

        event = {
            "event":      "config.changed",
            "tenant_id":  tenant_id or "__global__",
            "namespace":  namespace,
            "key":        key,
            "operation":  operation,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            await self._producer.send_and_wait(  # type: ignore[union-attr]
                TOPIC_CONFIG_CHANGED, event
            )
            logger.debug(
                "config.changed published: tenant=%s ns=%s key=%s op=%s",
                event["tenant_id"], namespace, key, operation,
            )
        except Exception as exc:
            logger.warning(
                "Failed to publish config.changed (tenant=%s ns=%s key=%s): %s",
                event["tenant_id"], namespace, key, exc,
            )
