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
from pydantic import BaseModel

from .adapters.email import EmailAdapter
from .adapters.sms import SMSAdapter
from .adapters.voice import VoiceAdapter
from .adapters.webchat import WebchatAdapter
from .adapters.webchat_channel import WebchatChannelAdapter
from .adapters.webhook import WebhookAdapter
from .adapters.webrtc import WebRTCAdapter
from .adapters.whatsapp import WhatsAppAdapter
from .attachment_store import (
    AttachmentStore,
    FilesystemAttachmentStore,
    S3AttachmentStore,
)
from .channel_capability_registry import (
    select_channel,
)
from .config import get_settings, Settings
from .context_reader import ContextReader
from .endpoint_resolver import resolve_pool
from .outbound_consumer import OutboundConsumer
from .webchat_config import webchat_config
from .session_registry import SessionRegistry
from .survey_web import SurveyWebService, SURVEY_PAGE_HTML

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
_webhook_adapter:    WebhookAdapter                       | None = None
_survey_web:         SurveyWebService                     | None = None


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
    global _producer, _registry, _context, _redis, _attachment_store, _whatsapp_adapter, _sms_adapter, _email_adapter, _voice_adapter, _webrtc_adapter, _webhook_adapter

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

    # Survey web vehicle (dialog primitive §9.2/§19): tokenized public survey page.
    global _survey_web
    _survey_web = SurveyWebService(
        redis          = _redis,
        producer       = _producer,
        dialog_api_url = settings.dialog_api_url,
        signals_topic  = settings.kafka_topic_signals,
        ttl_s          = settings.survey_web_ttl_s,
        # Público base URL para o link (SMS/e-mail); vazio = caminho relativo.
        base_url       = getattr(settings, "survey_web_base_url", "") or "",
    )

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
    _webhook_adapter = WebhookAdapter(
        producer = _producer,
        redis    = _redis,
        settings = settings,
        db_pool  = db_pool,     # Identity Resolver Slice 2 — durable PG store
    )
    await _webhook_adapter.ensure_identity_schema()

    _channel_adapters = {
        "webchat":  WebchatChannelAdapter(registry=_registry),
        "whatsapp": _whatsapp_adapter,
        "sms":      _sms_adapter,
        "email":    _email_adapter,
        "voice":    _voice_adapter,
        "webrtc":   _webrtc_adapter,
        "webhook":  _webhook_adapter,
    }

    outbound = OutboundConsumer(adapters=_channel_adapters, settings=settings)

    async def _collect_events_consumer() -> None:
        """
        Kafka consumer for collect.events — all channels (Arc 16 Phase D).

        Routes collect.requested events to the correct adapter:
          - Explicit channel: dispatched directly to the matching adapter.
          - No channel (capability-based): calls select_channel() with the
            event's `requires[]` list against all registered adapter channels,
            then dispatches to the selected adapter.

        For voice: VoiceAdapter.handle_collect_event() initiates an outbound call.
        For other channels: the adapter's handle_collect_event() sends the collect
        prompt as a message via the channel's native API.

        Note: Only collect.requested events require dispatch; collect.sent /
        collect.responded / collect.timed_out are purely for analytics and are
        handled by analytics-api.

        Arc 19 Fase F: Journey entity eliminated — capability selection no longer
        reads journey ContextStore.
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
          1. event["channel"] is set → use it directly.
          2. event["channel"] is absent/empty + event["requires"] is non-empty →
             call select_channel() against all registered adapter channels and
             dispatch to the best matching one.
          3. Fallback: warn and drop.

        Arc 19 Fase F: Journey entity eliminated — capability-based selection
        no longer reads journey ContextStore; it operates directly on the set of
        registered adapters.
        """
        channel  = (event.get("channel") or "").strip()
        requires = event.get("requires") or []   # list[str] from CollectStep

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
            return

        # ── Step 2: capability-based selection ────────────────────────────────
        if not requires:
            logger.warning(
                "collect.requested: no channel and no requires list — cannot route "
                "(instance=%s)", event.get("instance_id"),
            )
            return

        # Use all registered adapter channels as the available set.
        all_channels = list(adapters.keys())

        chosen = select_channel(
            available_channels = all_channels,
            requires           = requires,
            preferred_channel  = None,
        )
        if chosen is None:
            logger.warning(
                "collect.requested: no channel satisfies requires=%s "
                "from registered=%s (instance=%s)",
                requires, all_channels, event.get("instance_id"),
            )
            return

        adapter = adapters.get(chosen)
        if adapter is None:
            logger.debug(
                "collect.requested: selected channel=%s has no handle_collect_event "
                "— skipping", chosen,
            )
            return

        enriched = {**event, "channel": chosen}
        logger.info(
            "collect.requested: capability-selected channel=%s (requires=%s) "
            "instance=%s",
            chosen, requires, event.get("instance_id"),
        )
        await adapter.handle_collect_event(enriched)

    # config-http-propagation arc: load the webchat config namespace from the
    # Config API (HTTP) at startup, then keep it fresh via config.changed events.
    await webchat_config.reload(settings.config_api_url, settings.tenant_id)

    async def _config_changed_consumer() -> None:
        """
        Kafka consumer for config.changed — reloads the WebchatConfigCache when a
        value in the `webchat` namespace changes (e.g. auth_timeout_s,
        attachment_expiry_days edited in the Config UI). Canonical pattern shared
        with orchestrator-bridge / routing-engine config caches.
        """
        from aiokafka import AIOKafkaConsumer
        import json as _json

        consumer = AIOKafkaConsumer(
            "config.changed",
            bootstrap_servers = settings.kafka_brokers,
            group_id          = f"{settings.kafka_group_id}-config",
            auto_offset_reset = "latest",
        )
        await consumer.start()
        try:
            async for msg in consumer:
                try:
                    event = _json.loads(msg.value)
                    if event.get("namespace") == "webchat":
                        await webchat_config.reload(settings.config_api_url, settings.tenant_id)
                        logger.info(
                            "config.changed: webchat namespace reloaded (key=%s)",
                            event.get("key"),
                        )
                except Exception as exc:
                    logger.warning("config.changed consumer error: %s", exc)
        finally:
            await consumer.stop()

    pubsub_task     = asyncio.create_task(_registry.start_pubsub_listener())
    outbound_task   = asyncio.create_task(outbound.run())
    collect_task    = asyncio.create_task(_collect_events_consumer())
    config_task     = asyncio.create_task(_config_changed_consumer())
    # Arc 19 Fase D: expira suspends/delegates webhook vencidos (resume_tokens)
    timeout_scan_task = asyncio.create_task(_webhook_adapter.run_timeout_scanner())

    logger.info("✅ Channel Gateway started (instance=%s)", instance_id)
    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    pubsub_task.cancel()
    outbound_task.cancel()
    collect_task.cancel()
    config_task.cancel()
    timeout_scan_task.cancel()
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


# ── Webhook channel endpoints (Arc 19) ───────────────────────────────────────

class WebhookTriggerRequest(BaseModel):
    tenant_id:         str
    trigger_type:      str = "api"          # api | webhook | task | scheduled | yaml_auto
    metadata:          dict | None = None
    customer_id:       str | None = None
    origin_session_id: str | None = None    # Arc 19: session that triggered this workflow
    context:           dict | None = None   # Arc 19: seed ContextStore entries {tag: value}

class WebhookResumeRequest(BaseModel):
    tenant_id: str
    payload:   dict | None = None
    # Identity Resolver (nível b §11) — how the customer returned: same_channel|token|identity.
    # Default "token" (explicit resume_token path). "identity" set by the cross-channel
    # reconnect-offer flow so session_resumed carries the provenance.
    resume_origin: str = "token"

class WebhookDelegateRequest(BaseModel):
    tenant_id:         str
    pool_id:           str
    customer_id:       str
    origin_session_id: str
    resume_token:      str
    context:           dict[str, str] = {}
    timeout_hours:     float = 24.0
    # Identity Resolver (nível b) — gate the pending_by_customer dual-write.
    customer_resumable: bool = False
    resume_policy:      str  = "offer"   # offer | auto

class WebhookDelegateConferenceRequest(BaseModel):
    tenant_id:     str
    pool_id:       str
    session_id:    str     # parent session (customer connected here)
    customer_id:   str
    resume_token:  str     # delegate step resume token for parent session
    step_id:       str = "" # parent's delegate step id — used to build the resume_token value
    context:       dict[str, str] = {}
    timeout_hours: float = 1.0
    # Identity Resolver (nível b) — gate the pending_by_customer dual-write.
    customer_resumable: bool = False
    resume_policy:      str  = "offer"   # offer | auto

class IdentityAnchor(BaseModel):
    kind:  str   # phone | email | cpf | princ | dev
    value: str

class IdentityResolveRequest(BaseModel):
    tenant_id: str
    anchors:   list[IdentityAnchor]
    provision: bool = True

# ── OTP + enrichment (Fase 2) ───────────────────────────────────────────────────

class OtpChallengeRequest(BaseModel):
    tenant_id: str
    kind:      str   # phone | email | cpf | princ | dev
    value:     str

class OtpVerifyRequest(BaseModel):
    tenant_id:   str
    customer_id: str
    kind:        str
    value:       str
    code:        str

class IdentityAttachKeyRequest(BaseModel):
    tenant_id:   str
    customer_id: str
    kind:        str
    value:       str

class IdentityAttributesRequest(BaseModel):
    tenant_id:   str
    customer_id: str
    attributes:  dict


@app.post("/v1/channels/webhook/delegate-conference", status_code=201)
async def webhook_delegate_conference(body: WebhookDelegateConferenceRequest) -> dict:
    """
    Create a conference specialist in an existing agent (webchat) session.

    Called by skill-flow-service when delegate() fires in a non-webhook session
    (e.g. intake reconnect — Session A-new). The specialist joins the parent
    session as a conference participant; messages go to the parent stream so
    the customer stays on the same WebSocket connection.

    Returns: { session_id } — the PARENT session_id (specialist runs inside it).
    """
    if _webhook_adapter is None:
        raise HTTPException(status_code=503, detail="Webhook adapter not initialised")

    session_id = await _webhook_adapter.handle_delegate_conference(
        tenant_id          = body.tenant_id,
        pool_id            = body.pool_id,
        session_id         = body.session_id,
        customer_id        = body.customer_id,
        resume_token       = body.resume_token,
        step_id            = body.step_id,
        context            = body.context,
        timeout_hours      = body.timeout_hours,
        customer_resumable = body.customer_resumable,
        resume_policy      = body.resume_policy,
    )
    return {"session_id": session_id}


@app.post("/v1/channels/webhook/delegate", status_code=201)
async def webhook_delegate(body: WebhookDelegateRequest) -> dict:
    """
    Create a child session in a specific (non-webhook) pool for delegate I/O.

    Called by the skill-flow-service when a delegate step fires in a webhook workflow.
    The child session is a normal webchat session in the target pool. The agent
    allocated to it receives workflow_resume_token in its ContextStore, enabling
    it to resume the parent workflow when I/O is complete.

    NOTE: This route must be declared BEFORE /{skill_id} to avoid being captured
    by the path-parameter route in Starlette's first-match routing.

    Returns: { session_id } — the new child session ID.
    """
    if _webhook_adapter is None:
        raise HTTPException(status_code=503, detail="Webhook adapter not initialised")

    child_session_id = await _webhook_adapter.handle_delegate(
        tenant_id          = body.tenant_id,
        pool_id            = body.pool_id,
        customer_id        = body.customer_id,
        origin_session_id  = body.origin_session_id,
        resume_token       = body.resume_token,
        context            = body.context,
        timeout_hours      = body.timeout_hours,
        customer_resumable = body.customer_resumable,
        resume_policy      = body.resume_policy,
    )
    return {"session_id": child_session_id}


# ── Identity Resolver (Fase A · Slice 1) ───────────────────────────────────────
# Declared BEFORE the greedy /{skill_id} and /pending/{contact_identifier} routes.
# PII travels only on the loopback body; hashing is server-side (never in the URL).

@app.post("/v1/channels/webhook/identity/resolve", status_code=200)
async def webhook_identity_resolve(body: IdentityResolveRequest) -> dict:
    """
    Lookup 1 — resolve/provision a native customer_id from identity anchors.
    Returns { customer_id, status, matched_by, confidence }.
    """
    if _webhook_adapter is None:
        return {"customer_id": "", "status": "none", "matched_by": "none", "confidence": 0.0}
    return await _webhook_adapter.resolve_customer(
        tenant_id = body.tenant_id,
        anchors   = [a.model_dump() for a in body.anchors],
        provision = body.provision,
    )


@app.get("/v1/channels/webhook/pending/by-customer/{customer_id}", status_code=200)
async def webhook_pending_by_customer(customer_id: str, tenant_id: str) -> dict:
    """
    Lookup 2 — pending workflows registered under a resolved customer_id.
    Returns { found, count, pendings[] }.
    """
    if _webhook_adapter is None:
        return {"found": False, "count": 0, "pendings": []}
    return await _webhook_adapter.find_pending_by_customer(
        tenant_id   = tenant_id,
        customer_id = customer_id,
    )


@app.post("/v1/channels/webhook/identity/otp/challenge", status_code=200)
async def webhook_otp_challenge(body: OtpChallengeRequest) -> dict:
    """OTP de posse — emite um desafio para a âncora. Entrega mockada no demo."""
    if _webhook_adapter is None:
        return {"sent": False, "reason": "adapter_unavailable"}
    return await _webhook_adapter.otp_challenge(body.tenant_id, body.kind, body.value)


@app.post("/v1/channels/webhook/identity/otp/verify", status_code=200)
async def webhook_otp_verify(body: OtpVerifyRequest) -> dict:
    """OTP de posse — confere o código; sucesso promove a âncora a possessed."""
    if _webhook_adapter is None:
        return {"verified": False, "reason": "adapter_unavailable"}
    return await _webhook_adapter.otp_verify(
        body.tenant_id, body.customer_id, body.kind, body.value, body.code,
    )


@app.post("/v1/channels/webhook/identity/key/attach", status_code=200)
async def webhook_identity_attach_key(body: IdentityAttachKeyRequest) -> dict:
    """Enriquecimento — anexa uma âncora como claimed (possessed só via OTP)."""
    if _webhook_adapter is None:
        return {"attached": False}
    return await _webhook_adapter.attach_customer_key(
        body.tenant_id, body.customer_id, body.kind, body.value,
    )


@app.post("/v1/channels/webhook/identity/attributes", status_code=200)
async def webhook_identity_attributes(body: IdentityAttributesRequest) -> dict:
    """Enriquecimento — merge de atributos mascarados/não-sensíveis no cadastro."""
    if _webhook_adapter is None:
        return {"updated": False}
    return await _webhook_adapter.update_customer_attributes(
        body.tenant_id, body.customer_id, body.attributes,
    )


# ── Survey web vehicle (dialog primitive §9.2/§19) ────────────────────────────
# Link tokenizado → página pública /survey/{token} que renderiza o MESMO
# DialogForm e grava pela MESMA trilha (session.signals). Prefixos /v1/survey e
# /survey não colidem com o catch-all /v1/channels/webhook/{skill_id} abaixo.

@app.post("/v1/survey/web/create", status_code=201)
async def survey_web_create(request: Request) -> dict:
    """Cria um token de survey web (congela o form publicado do dialog-api)."""
    if _survey_web is None:
        raise HTTPException(status_code=503, detail="Survey web not initialised")
    body      = await request.json()
    tenant_id = body.get("tenant_id") or get_settings().tenant_id
    form_id   = body.get("form_id")
    if not form_id:
        raise HTTPException(status_code=400, detail="form_id required")
    try:
        return await _survey_web.create(
            tenant_id, form_id,
            body.get("origin_session_id", ""), body.get("customer_key", ""),
            # Entrega opcional do link (camada plugável — mock/dev por ora).
            body.get("deliver_kind", ""), body.get("deliver_address", ""),
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"dialog-api error: {exc}")


@app.get("/v1/survey/web/{token}", status_code=200)
async def survey_web_get(token: str) -> dict:
    """Resolve o token → o form + status (consumido pela página pública)."""
    if _survey_web is None:
        raise HTTPException(status_code=503, detail="Survey web not initialised")
    rec = await _survey_web.get(token)
    if rec is None:
        raise HTTPException(status_code=404, detail="survey not found")
    return {"form": rec.get("form"), "status": rec.get("status")}


@app.post("/v1/survey/web/{token}/submit", status_code=200)
async def survey_web_submit(token: str, request: Request) -> dict:
    if _survey_web is None:
        raise HTTPException(status_code=503, detail="Survey web not initialised")
    body = await request.json()
    return await _survey_web.submit(token, body.get("answers") or {})


@app.get("/survey/{token}")
async def survey_web_page(token: str):
    """Página pública do survey (mesmo DialogForm, veículo web)."""
    from fastapi.responses import HTMLResponse
    return HTMLResponse(content=SURVEY_PAGE_HTML)


@app.post("/v1/channels/webhook/{skill_id}", status_code=201)
async def webhook_trigger(skill_id: str, body: WebhookTriggerRequest) -> dict:
    """
    Trigger a new webhook workflow session.

    The skill_id is the endpoint identifier (analogous to a WA number or voice DIN).
    The routing engine resolves the pool that owns this skill_id and allocates
    a skill-flow instance to execute the workflow.

    Returns: { session_id }
    """
    if _webhook_adapter is None:
        raise HTTPException(status_code=503, detail="Webhook adapter not initialised")

    session_id = await _webhook_adapter.handle_trigger(
        skill_id           = skill_id,
        tenant_id          = body.tenant_id,
        trigger_type       = body.trigger_type,
        metadata           = body.metadata,
        customer_id        = body.customer_id,
        origin_session_id  = body.origin_session_id,
        context            = body.context,
    )
    return {"session_id": session_id}


@app.post("/v1/channels/webhook/resume/{resume_token}", status_code=200)
async def webhook_resume(resume_token: str, body: WebhookResumeRequest) -> dict:
    """
    Resume a suspended webhook session using the resume_token generated at suspend time.

    The token is resolved to a session_id via Redis hash {tenant_id}:resume_tokens.
    After resume, the routing engine reallocates a skill-flow instance to continue
    the workflow from the step that suspended.

    Returns: { session_id }
    Raises 404 if the token is unknown or expired.
    """
    if _webhook_adapter is None:
        raise HTTPException(status_code=503, detail="Webhook adapter not initialised")

    session_id = await _webhook_adapter.handle_resume(
        resume_token  = resume_token,
        tenant_id     = body.tenant_id,
        payload       = body.payload,
        resume_origin = body.resume_origin,
    )
    if session_id is None:
        raise HTTPException(
            status_code=404,
            detail="Resume token not found or expired",
        )
    return {"session_id": session_id}


@app.get("/v1/channels/webhook/pending/{contact_identifier}", status_code=200)
async def webhook_pending(contact_identifier: str, tenant_id: str) -> dict:
    """
    Check whether a customer has an active pending workflow awaiting confirmation.

    Called by intake agents (via the pending_workflow_get MCP tool) after
    collecting the customer's contact_identifier.  Returns the resume_token
    needed to continue the workflow without creating a new one.

    Returns:
      { found: false }                          — no pending workflow
      { found: true, resume_token, context }    — active pending workflow found
    """
    if _webhook_adapter is None:
        raise HTTPException(status_code=503, detail="Webhook adapter not initialised")

    result = await _webhook_adapter.get_pending_workflow(
        tenant_id          = tenant_id,
        contact_identifier = contact_identifier,
    )
    if result is None:
        return {"found": False}
    return {"found": True, **result}


@app.get("/v1/channels/webhook/{session_id}/status", status_code=200)
async def webhook_status(session_id: str, tenant_id: str) -> dict:
    """
    Query the current status of a webhook session.

    Returns: { session_id, status: "active"|"suspended"|"closed" }
    """
    if _webhook_adapter is None:
        raise HTTPException(status_code=503, detail="Webhook adapter not initialised")

    return await _webhook_adapter.get_status(
        session_id = session_id,
        tenant_id  = tenant_id,
    )


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
