"""
main.py
Channel Gateway entry point.
FastAPI app with WebSocket endpoint + Kafka producer/consumer startup.
Spec: PlugHub v24.0 section 3.5 / channel-gateway-webchat.md
"""

from __future__ import annotations
import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
import uvicorn
from aiokafka import AIOKafkaProducer
from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect

from .adapters.webchat import WebchatAdapter
from .config import get_settings
from .context_reader import ContextReader
from .outbound_consumer import OutboundConsumer
from .session_registry import SessionRegistry

logger = logging.getLogger("plughub.channel-gateway")

# ── Application state (shared across requests) ────────────────────────────

_producer:  AIOKafkaProducer | None = None
_registry:  SessionRegistry  | None = None
_context:   ContextReader     | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _producer, _registry, _context

    settings  = get_settings()
    instance_id = str(uuid.uuid4())

    redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)

    _producer = AIOKafkaProducer(
        bootstrap_servers=settings.kafka_brokers,
    )
    await _producer.start()

    _registry = SessionRegistry(
        redis=redis_client,
        instance_id=instance_id,
        ttl=settings.session_ttl_seconds,
    )
    _context = ContextReader(redis=redis_client)

    outbound = OutboundConsumer(registry=_registry, settings=settings)

    # Background tasks
    pubsub_task  = asyncio.create_task(_registry.start_pubsub_listener())
    outbound_task = asyncio.create_task(outbound.run())

    logger.info("✅ Channel Gateway started (instance=%s)", instance_id)
    yield

    # Shutdown
    pubsub_task.cancel()
    outbound_task.cancel()
    await _producer.stop()
    await redis_client.aclose()
    logger.info("Channel Gateway stopped")


app = FastAPI(title="PlugHub Channel Gateway", lifespan=lifespan)


# ── WebSocket endpoint ────────────────────────────────────────────────────

@app.websocket("/ws/chat")
async def websocket_endpoint(
    ws:         WebSocket,
    contact_id: str | None = Query(default=None),
) -> None:
    """
    WebSocket endpoint for web chat contacts.

    Query params:
      contact_id (optional) — existing contact to reconnect to.
                              If omitted, a new contact_id is generated.

    On connect:
      1. Assigns/validates contact_id and session_id
      2. Registers in SessionRegistry
      3. Sends connection.accepted to client
      4. Publishes contact_open to conversations.events
      5. Enters receive loop
    """
    settings   = get_settings()
    cid        = contact_id or str(uuid.uuid4())
    session_id = str(uuid.uuid4())

    adapter = WebchatAdapter(
        ws=ws,
        contact_id=cid,
        session_id=session_id,
        producer=_producer,
        registry=_registry,
        context_reader=_context,
        settings=settings,
    )
    await adapter.handle()


# ── Health check ──────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "channel-gateway"}


# ── Entry point ───────────────────────────────────────────────────────────

def run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    uvicorn.run(
        "plughub_channel_gateway.main:app",
        host="0.0.0.0",
        port=8010,
        reload=False,
    )


if __name__ == "__main__":
    run()
