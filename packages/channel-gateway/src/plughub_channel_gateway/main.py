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
from fastapi import FastAPI, HTTPException, Request, WebSocket

from .adapters.email import EmailAdapter
from .adapters.sms import SMSAdapter
from .adapters.webchat import WebchatAdapter
from .adapters.webchat_channel import WebchatChannelAdapter
from .adapters.whatsapp import WhatsAppAdapter
from .attachment_store import (
    AttachmentStore,
    FilesystemAttachmentStore,
    S3AttachmentStore,
)
from .config import get_settings, Settings
from .context_reader import ContextReader
from .endpoint_resolver import resolve_pool
from .outbound_consumer import OutboundConsumer
from .session_registry import SessionRegistry

logger = logging.getLogger("plughub.channel-gateway")

# ── Application state (shared across requests) ────────────────────────────────

_producer:           AIOKafkaProducer                      | None = None
_registry:           SessionRegistry                       | None = None
_context:            ContextReader                         | None = None
_redis:              aioredis.Redis                        | None = None
_attachment_store:   FilesystemAttachmentStore | S3AttachmentStore | None = None
_whatsapp_adapter:   WhatsAppAdapter                      | None = None
_sms_adapter:        SMSAdapter                           | None = None
_email_adapter:      EmailAdapter                         | None = None


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
    global _producer, _registry, _context, _redis, _attachment_store, _whatsapp_adapter, _sms_adapter, _email_adapter

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

    # ── Channel adapter registry ──────────────────────────────────────────────
    # Register one ChannelAdapter singleton per supported channel.
    # Adding a new channel: instantiate its adapter here and add to the dict.
    _whatsapp_adapter = WhatsAppAdapter(
        producer         = _producer,
        redis            = _redis,
        settings         = settings,
        attachment_store = _attachment_store,
    )
    _sms_adapter = SMSAdapter(
        producer  = _producer,
        redis     = _redis,
        settings  = settings,
    )
    _email_adapter = EmailAdapter(
        producer         = _producer,
        redis            = _redis,
        settings         = settings,
        attachment_store = _attachment_store,
    )

    _channel_adapters = {
        "webchat":  WebchatChannelAdapter(registry=_registry),
        "whatsapp": _whatsapp_adapter,
        "sms":      _sms_adapter,
        "email":    _email_adapter,
    }

    outbound = OutboundConsumer(adapters=_channel_adapters, settings=settings)

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
      pool_id  — channel identifier (webchat slug) or direct pool_id.
                 Layer 2 lookup: if a ChannelEndpoint record exists in
                 agent-registry for this identifier, its pool_id is used.
                 Otherwise the identifier is treated as the pool_id directly
                 (backward-compatible with existing single-pool deployments).

    Protocol:
      After accept the server sends conn.hello; the client must reply with
      conn.authenticate {token, cursor?} within ws_auth_timeout_s seconds.
      On success the server sends conn.authenticated and the session begins.

    Reconnect:
      Include cursor=<last_event_id> in conn.authenticate to resume the stream
      from the last received event — no messages are missed.
    """
    settings = get_settings()

    # ── Layer 2: channel endpoint lookup ─────────────────────────────────────
    # Try to resolve the path param as a channel identifier → pool_id via the
    # agent-registry.  Falls back gracefully when:
    #   • no active ChannelEndpoint record exists (new or unknown identifier)
    #   • the registry is unreachable (network error, cold-start race)
    # In both cases we treat the path param itself as the pool_id, preserving
    # full backward compatibility for existing deployments.
    resolved_pool: str
    if pool_id and settings.agent_registry_url:
        looked_up = await resolve_pool(
            channel            = "webchat",
            identifier         = pool_id,
            tenant_id          = settings.tenant_id,
            agent_registry_url = settings.agent_registry_url,
            cache_ttl_s        = settings.endpoint_cache_ttl_s,
        )
        resolved_pool = looked_up or pool_id
    else:
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


# ── WhatsApp webhook ──────────────────────────────────────────────────────────

@app.get("/webhooks/whatsapp")
async def whatsapp_verify(request: Request) -> str:
    """
    Meta webhook verification challenge.
    Called once when the webhook URL is registered in Meta Developer Portal.
    Responds with hub.challenge if hub.verify_token matches.
    """
    settings    = get_settings()
    mode        = request.query_params.get("hub.mode")
    token       = request.query_params.get("hub.verify_token")
    challenge   = request.query_params.get("hub.challenge", "")

    if mode == "subscribe" and token == settings.whatsapp_verify_token:
        logger.info("whatsapp webhook verified successfully")
        return challenge

    logger.warning("whatsapp webhook verification failed — invalid token")
    raise HTTPException(status_code=403, detail="Forbidden")


@app.post("/webhooks/whatsapp", status_code=200)
async def whatsapp_inbound(request: Request) -> dict:
    """
    Meta Cloud API inbound webhook.
    HTTP 200 is returned immediately; processing happens in a background task.
    """
    body      = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")

    if _whatsapp_adapter is None:
        logger.error("whatsapp_adapter not initialised")
        raise HTTPException(status_code=503, detail="Service unavailable")

    if not _whatsapp_adapter.verify_signature(body, signature):
        logger.warning("whatsapp inbound rejected — invalid HMAC signature")
        raise HTTPException(status_code=400, detail="Invalid signature")

    await _whatsapp_adapter.handle_inbound(body)
    return {"status": "ok"}


# ── Email webhook ─────────────────────────────────────────────────────────────

@app.post("/webhooks/email", status_code=200)
async def email_inbound(request: Request) -> dict:
    """
    Mailgun inbound email webhook.
    Mailgun sends multipart/form-data with parsed email fields + raw MIME.
    HTTP 200 returned immediately; processing in a background task.
    """
    headers = dict(request.headers)
    body    = await request.body()

    if _email_adapter is None:
        logger.error("email_adapter not initialised")
        raise HTTPException(status_code=503, detail="Service unavailable")

    await _email_adapter.process_inbound(headers=headers, body=body)
    return {"status": "ok"}


# ── SMS webhook ───────────────────────────────────────────────────────────────

@app.post("/webhooks/sms", status_code=200)
async def sms_inbound(request: Request) -> str:
    """
    Twilio SMS inbound webhook.
    Twilio sends form-encoded bodies and expects a TwiML XML response.
    HTTP 200 + empty TwiML is returned immediately; processing is in a background task.
    """
    params    = dict(await request.form())
    signature = request.headers.get("X-Twilio-Signature", "")
    url       = str(request.url)

    if _sms_adapter is None:
        logger.error("sms_adapter not initialised")
        raise HTTPException(status_code=503, detail="Service unavailable")

    await _sms_adapter.process_inbound(
        params=params,
        signature=signature,
        url=url,
    )
    # Twilio requires a TwiML response; empty <Response/> suppresses any callback action
    return "<Response/>"


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
