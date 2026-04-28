"""
main.py
Channel Gateway entry point.
FastAPI app with WebSocket endpoint, Kafka producer/consumer, and attachment HTTP routes.
Spec: PlugHub v24.0 section 3.5 / channel-gateway-webchat.md
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager

import asyncpg
import redis.asyncio as aioredis
import uvicorn
from aiokafka import AIOKafkaProducer
from fastapi import FastAPI, WebSocket

from .adapters.webchat import WebchatAdapter
from .attachment_store import (
    AttachmentStore,
    FilesystemAttachmentStore,
    S3AttachmentStore,
)
from .config import get_settings, Settings
from .context_reader import ContextReader
from .outbound_consumer import OutboundConsumer
from .session_registry import SessionRegistry

logger = logging.getLogger("plughub.channel-gateway")

# ── Application state (shared across requests) ────────────────────────────────

_producer:         AIOKafkaProducer                      | None = None
_registry:         SessionRegistry                       | None = None
_context:          ContextReader                         | None = None
_redis:            aioredis.Redis                        | None = None
_attachment_store: FilesystemAttachmentStore | S3AttachmentStore | None = None


def _create_attachment_store(
    settings: Settings,
    db_pool:  asyncpg.Pool,
) -> FilesystemAttachmentStore | S3AttachmentStore:
    """
    Factory que instancia o backend de storage correto conforme
    PLUGHUB_ATTACHMENT_STORE_TYPE.
      - "filesystem" (padrão) → FilesystemAttachmentStore (disco local)
      - "s3"                  → S3AttachmentStore (S3 / MinIO)
    """
    if settings.attachment_store_type == "s3":
        logger.info(
            "AttachmentStore: usando S3 backend (endpoint=%s bucket=%s)",
            settings.s3_endpoint_url or "AWS",
            settings.s3_bucket,
        )
        return S3AttachmentStore(
            bucket                = settings.s3_bucket,
            db_pool               = db_pool,
            serving_base_url      = settings.webchat_serving_base_url,
            upload_base_url       = settings.webchat_upload_base_url,
            endpoint_url          = settings.s3_endpoint_url or None,
            aws_access_key_id     = settings.s3_access_key or None,
            aws_secret_access_key = settings.s3_secret_key or None,
            region_name           = settings.s3_region,
        )

    logger.info("AttachmentStore: usando filesystem backend (root=%s)", settings.storage_root)
    return FilesystemAttachmentStore(
        storage_root     = settings.storage_root,
        db_pool          = db_pool,
        serving_base_url = settings.webchat_serving_base_url,
        upload_base_url  = settings.webchat_upload_base_url,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _producer, _registry, _context, _redis, _attachment_store

    settings    = get_settings()
    instance_id = str(uuid.uuid4())

    _redis = aioredis.from_url(settings.redis_url, decode_responses=True)

    _producer = AIOKafkaProducer(bootstrap_servers=settings.kafka_brokers)
    await _producer.start()

    _registry = SessionRegistry(
        redis       = _redis,
        instance_id = instance_id,
        ttl         = settings.session_ttl_seconds,
    )
    _context = ContextReader(redis=_redis)

    # PostgreSQL pool for attachment metadata
    db_pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=10)

    _attachment_store = _create_attachment_store(settings, db_pool)
    await _attachment_store.ensure_schema()

    outbound = OutboundConsumer(registry=_registry, settings=settings)

    pubsub_task   = asyncio.create_task(_registry.start_pubsub_listener())
    outbound_task = asyncio.create_task(outbound.run())

    logger.info("✅ Channel Gateway started (instance=%s)", instance_id)
    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    pubsub_task.cancel()
    outbound_task.cancel()
    await _producer.stop()
    await db_pool.close()
    await _redis.aclose()
    logger.info("Channel Gateway stopped")


app = FastAPI(title="PlugHub Channel Gateway", lifespan=lifespan)

# ── Import and mount upload routes ────────────────────────────────────────────
# Deferred import so the router can reference module-level state set in lifespan.
from .upload_router import router as upload_router  # noqa: E402  (post-app creation import)
app.include_router(upload_router)


# ── WebSocket endpoint ────────────────────────────────────────────────────────

@app.websocket("/ws/chat/{pool_id}")
async def websocket_endpoint(ws: WebSocket, pool_id: str) -> None:
    """
    WebSocket endpoint for web chat contacts.

    Path params:
      pool_id  — service pool to route this contact to (e.g. "retencao_humano").

    Protocol:
      After accept the server sends conn.hello; the client must reply with
      conn.authenticate {token, cursor?} within ws_auth_timeout_s seconds.
      On success the server sends conn.authenticated and the session begins.

    Reconnect:
      Include cursor=<last_event_id> in conn.authenticate to resume the stream
      from the last received event — no messages are missed.
    """
    settings      = get_settings()
    resolved_pool = pool_id or settings.entry_point_pool_id

    adapter = WebchatAdapter(
        ws               = ws,
        pool_id          = resolved_pool,
        producer         = _producer,
        registry         = _registry,
        context_reader   = _context,
        settings         = settings,
        redis            = _redis,
        attachment_store = _attachment_store,
    )
    await adapter.handle()


# ── Health check ──────────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "channel-gateway"}


# ── Entry point ───────────────────────────────────────────────────────────────

def run() -> None:
    logging.basicConfig(
        level  = logging.INFO,
        format = "%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    uvicorn.run(
        "plughub_channel_gateway.main:app",
        host   = "0.0.0.0",
        port   = 8010,
        reload = False,
    )


if __name__ == "__main__":
    run()
