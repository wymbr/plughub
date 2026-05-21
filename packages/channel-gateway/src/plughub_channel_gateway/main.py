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
from .adapters.voice import VoiceAdapter
from .adapters.webchat import WebchatAdapter
from .adapters.webchat_channel import WebchatChannelAdapter
from .adapters.webrtc import WebRTCAdapter
from .adapters.whatsapp import WhatsAppAdapter
from .attachment_store import (
    AttachmentStore,
    FilesystemAttachmentStore,
    S3AttachmentStore,
)
from .channel_capability_registry import (
    get_journey_contact_id,
    read_journey_channel_context,
    select_channel,
    write_journey_channel_context,
    write_journey_pending_collect,
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
_voice_adapter:      VoiceAdapter                         | None = None
_webrtc_adapter:     WebRTCAdapter                        | None = None


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
    global _producer, _registry, _context, _redis, _attachment_store, _whatsapp_adapter, _sms_adapter, _email_adapter, _voice_adapter, _webrtc_adapter

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
    _voice_adapter = VoiceAdapter(
        producer         = _producer,
        redis            = _redis,
        settings         = settings,
        attachment_store = _attachment_store,
    )
    _webrtc_adapter = WebRTCAdapter(
        producer = _producer,
        redis    = _redis,
        settings = settings,
    )

    _channel_adapters = {
        "webchat":  WebchatChannelAdapter(registry=_registry),
        "whatsapp": _whatsapp_adapter,
        "sms":      _sms_adapter,
        "email":    _email_adapter,
        "voice":    _voice_adapter,
        "webrtc":   _webrtc_adapter,
    }

    outbound = OutboundConsumer(adapters=_channel_adapters, settings=settings)

    async def _collect_events_consumer() -> None:
        """
        Kafka consumer for collect.events — all channels (Arc 16 Phase D).

        Routes collect.requested events to the correct adapter:
          - Explicit channel: dispatched directly to the matching adapter.
          - No channel (capability-based): reads journey.available_channels and
            journey.canal_preferido from the journey ContextStore, calls
            select_channel() with the event's `requires[]` list, then dispatches
            to the selected adapter.

        For voice: VoiceAdapter.handle_collect_event() initiates an outbound call.
        For other channels: the adapter's handle_collect_event() sends the collect
        prompt as a message via the channel's native API.

        Note: Only collect.requested events require dispatch; collect.sent /
        collect.responded / collect.timed_out are purely for analytics and are
        handled by analytics-api.
        """
        from aiokafka import AIOKafkaConsumer
        import json as _json

        _adapters_with_collect = {
            k: v for k, v in _channel_adapters.items()
            if hasattr(v, "handle_collect_event")
        }

        consumer = AIOKafkaConsumer(
            "collect.events",
            bootstrap_servers = settings.kafka_brokers,
            group_id          = f"{settings.kafka_group_id}-collect",
            auto_offset_reset = "latest",
        )
        await consumer.start()
        try:
            async for msg in consumer:
                try:
                    event = _json.loads(msg.value)
                    # Only process collect.requested — other subtypes are for analytics
                    if event.get("event_type") != "collect.requested":
                        continue
                    await _dispatch_collect_event(event, _adapters_with_collect)
                except Exception as exc:
                    logger.warning("collect.events consumer error: %s", exc)
        finally:
            await consumer.stop()

    async def _dispatch_collect_event(
        event:    dict,
        adapters: dict,
    ) -> None:
        """
        Dispatch a single collect.requested event to the correct channel adapter.

        Channel resolution order:
          1. event["channel"] is set → use it directly
          2. event["channel"] is absent/empty + event["journey_id"] is set →
             read journey context from ContextStore and use select_channel()
          3. Fallback: warn and drop
        """
        channel    = (event.get("channel") or "").strip()
        journey_id = event.get("journey_id")
        tenant_id  = event.get("tenant_id", settings.tenant_id)
        requires   = event.get("requires") or []   # list[str] from CollectStep

        # ── Step 1: explicit channel ───────────────────────────────────────────
        if channel:
            adapter = adapters.get(channel)
            if adapter is None:
                logger.debug(
                    "collect.requested: no handle_collect_event for channel=%s — skipping",
                    channel,
                )
                return
            logger.info(
                "collect.requested: explicit channel=%s instance=%s",
                channel, event.get("instance_id"),
            )
            await adapter.handle_collect_event(event)
            # Record pending collect in journey ContextStore for journey_check_pending MCP tool
            if journey_id:
                await write_journey_pending_collect(
                    redis      = _redis,
                    tenant_id  = tenant_id,
                    journey_id = journey_id,
                    requires   = requires,
                    channel    = channel,
                    contact_id = event.get("target"),
                )
            return

        # ── Step 2: capability-based selection ────────────────────────────────
        if not journey_id:
            logger.warning(
                "collect.requested: no channel and no journey_id — cannot route "
                "(instance=%s)", event.get("instance_id"),
            )
            return

        available, preferred = await read_journey_channel_context(
            redis      = _redis,
            tenant_id  = tenant_id,
            journey_id = journey_id,
        )

        if not available:
            logger.warning(
                "collect.requested: journey=%s has no available_channels in ContextStore "
                "— cannot route (instance=%s)", journey_id, event.get("instance_id"),
            )
            return

        chosen = select_channel(
            available_channels = available,
            requires           = requires,
            preferred_channel  = preferred,
        )
        if chosen is None:
            logger.warning(
                "collect.requested: no channel satisfies requires=%s "
                "from available=%s (journey=%s)",
                requires, available, journey_id,
            )
            return

        # Enrich event with resolved channel so the adapter has it
        enriched = {**event, "channel": chosen}

        # Inject the customer's contact_id for this channel (needed by non-voice adapters)
        if not enriched.get("target"):
            contact_id = await get_journey_contact_id(
                redis      = _redis,
                tenant_id  = tenant_id,
                journey_id = journey_id,
                channel    = chosen,
            )
            if contact_id:
                enriched["target"] = contact_id

        adapter = adapters.get(chosen)
        if adapter is None:
            logger.debug(
                "collect.requested: selected channel=%s has no handle_collect_event "
                "— skipping (journey=%s)", chosen, journey_id,
            )
            return

        logger.info(
            "collect.requested: capability-selected channel=%s (requires=%s) "
            "journey=%s instance=%s",
            chosen, requires, journey_id, event.get("instance_id"),
        )
        await adapter.handle_collect_event(enriched)
        # Record pending collect in journey ContextStore for journey_check_pending MCP tool
        await write_journey_pending_collect(
            redis      = _redis,
            tenant_id  = tenant_id,
            journey_id = journey_id,
            requires   = requires,
            channel    = chosen,
            contact_id = enriched.get("target"),
        )

    pubsub_task     = asyncio.create_task(_registry.start_pubsub_listener())
    outbound_task   = asyncio.create_task(outbound.run())
    collect_task    = asyncio.create_task(_collect_events_consumer())

    logger.info("✅ Channel Gateway started (instance=%s)", instance_id)
    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    pubsub_task.cancel()
    outbound_task.cancel()
    collect_task.cancel()
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


# ── Voice webhooks ────────────────────────────────────────────────────────────

@app.post("/webhooks/voice/inbound", status_code=200)
async def voice_inbound(request: Request):
    """
    Twilio voice inbound webhook.
    Called on every new inbound (or answered outbound) call.
    Returns TwiML XML that opens Media Streams + places customer in conference.
    """
    from fastapi.responses import Response as _Response
    params    = dict(await request.form())
    signature = request.headers.get("X-Twilio-Signature", "")
    url       = str(request.url)

    if _voice_adapter is None:
        logger.error("voice_adapter not initialised")
        raise HTTPException(status_code=503, detail="Service unavailable")

    twiml = await _voice_adapter.handle_inbound(
        params=params, signature=signature, url=url
    )
    return _Response(content=twiml, media_type="text/xml")


@app.post("/webhooks/voice/status", status_code=200)
async def voice_status(request: Request) -> dict:
    """
    Twilio conference status callback.
    Called on participant join / leave / end events.
    Used to detect customer hangup and close the PlugHub session.
    """
    params = dict(await request.form())

    if _voice_adapter is None:
        logger.error("voice_adapter not initialised")
        raise HTTPException(status_code=503, detail="Service unavailable")

    await _voice_adapter.handle_status(params)
    return {"status": "ok"}


@app.post("/webhooks/voice/recording", status_code=200)
async def voice_recording(request: Request) -> dict:
    """
    Twilio recording status callback.
    Called when a conference recording is complete and ready for download.
    """
    params = dict(await request.form())

    if _voice_adapter is None:
        logger.error("voice_adapter not initialised")
        raise HTTPException(status_code=503, detail="Service unavailable")

    await _voice_adapter.handle_recording_complete(params)
    return {"status": "ok"}


@app.get("/voice/tts/{tts_id}")
async def voice_tts(tts_id: str):
    """
    TTS snippet endpoint — called by Twilio to fetch <Say> TwiML.
    Twilio hits this URL via conference.announce_url.
    Returns TwiML <Response><Say>...</Say></Response> or 404.
    """
    from fastapi.responses import Response as _Response
    if _voice_adapter is None:
        raise HTTPException(status_code=503, detail="Service unavailable")

    twiml = await _voice_adapter.get_tts_twiml(tts_id)
    if twiml is None:
        raise HTTPException(status_code=404, detail="TTS snippet not found or expired")
    return _Response(content=twiml, media_type="text/xml")


@app.get("/voice/tts-audio/{tts_id}")
async def voice_tts_audio(tts_id: str):
    """
    Deepgram Aura TTS audio endpoint.
    Served when PLUGHUB_VOICE_TTS_PROVIDER=deepgram_aura.
    Returns audio/mpeg bytes or 404.
    """
    from fastapi.responses import Response as _Response
    if _voice_adapter is None:
        raise HTTPException(status_code=503, detail="Service unavailable")

    audio = await _voice_adapter.get_tts_audio(tts_id)
    if audio is None:
        raise HTTPException(status_code=404, detail="TTS audio not found or expired")
    return _Response(content=audio, media_type="audio/mpeg")


@app.websocket("/voice/media")
async def voice_media_ws(ws: WebSocket) -> None:
    """
    Twilio Media Streams WebSocket endpoint.
    Twilio opens this connection after receiving the TwiML <Start><Stream> instruction.
    Handles: audio STT, DTMF collect, segment recording via stream watcher.
    """
    if _voice_adapter is None:
        await ws.close(code=1011)
        return
    await _voice_adapter.handle_media_ws(ws)


# ── WebRTC signaling ──────────────────────────────────────────────────────────

@app.websocket("/ws/webrtc/{pool_id}")
async def webrtc_ws(ws: WebSocket, pool_id: str) -> None:
    """
    WebRTC signaling WebSocket endpoint.

    Path param:
      pool_id — service pool (or ChannelEndpoint identifier resolved via
                agent-registry Layer 2, same as /ws/chat/{pool_id}).

    Protocol:
      After accept the server sends conn.ready; the client must reply with
      conn.hello then conn.authenticate (customer JWT) within 30 seconds.
      On success the server sends conn.authenticated and begins watching the
      session stream.  When routing.assigned arrives, the server negotiates
      the media medium, creates a LiveKit room, and sends webrtc.ready with
      a signed customer token and the LiveKit URL.

    Architecture: docs/arcos/arc15-webrtc.md
    """
    if _webrtc_adapter is None:
        await ws.close(code=1011)
        return
    await _webrtc_adapter.handle_ws(ws, pool_id)


@app.get("/webrtc/token/{session_id}")
async def webrtc_token(
    session_id: str,
    role:       str = "agent",
    identity:   str = "",
    request:    Request = None,
) -> dict:
    """
    Issue a LiveKit token for an agent or supervisor joining an active WebRTC session.

    Query params:
      role      — "agent" (default) | "supervisor"
      identity  — agent_type_id or user ID (used as LiveKit participant identity)

    Authorization:
      Bearer <agent_JWT>  — validated by the platform before issuing a LiveKit token.
      (Phase A: authorization header is recorded; full JWT validation in Phase B.)

    Returns:
      {token, livekit_url, room_name, negotiated_medium}

    Returns 404 if the session has no LiveKit room yet (routing not yet complete).
    Returns 503 if the WebRTC adapter is not initialised.
    """
    if _webrtc_adapter is None:
        raise HTTPException(status_code=503, detail="WebRTC adapter not initialised")

    # Phase A: use identity from query param; Phase B will validate agent JWT.
    if not identity:
        identity = f"{role}-{session_id[:8]}"

    result = await _webrtc_adapter.get_token(
        session_id = session_id,
        role       = role,
        identity   = identity,
    )
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="WebRTC room not ready for session — routing may still be in progress",
        )
    return result


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
