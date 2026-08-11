"""
main.py
Channel Gateway entry point.
FastAPI app with WebSocket endpoint, Kafka producer/consumer, and attachment HTTP routes.
Spec: PlugHub v24.0 section 3.5 / channel-gateway-webchat.md
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
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
from .adapters.webhook import ResumeAlreadyTerminalError, WebhookAdapter
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
from .auth import verify_user_jwt, abac_can, pool_in_scope, accessible_pools, bearer_from_header
from .context_reader import ContextReader
from .endpoint_resolver import ResolvedEndpoint, resolve_endpoint, resolve_pool
from .outbound_consumer import OutboundConsumer
from .registry_invalidation_consumer import RegistryInvalidationConsumer
from .webchat_config import webchat_config
from .session_registry import SessionRegistry
from .survey_web import (
    SurveyWebService,
    SurveyLinkDelivery,
    SURVEY_PAGE_HTML,
    SURVEY_COLLECT_PAGE_HTML,   # Journey J4c — collect-based survey (webchat client)
)

def _configure_logging() -> None:
    """
    Configura o logging da APLICAÇÃO — no import, não só no `run()`.

    ⚠️ **Achado 2026-08-07 (Fase C do ADR de webhook).** Isto morava exclusivamente
    dentro de `run()`, que é o entry point de `python -m`. Mas o container sobe com
    `uvicorn plughub_channel_gateway.main:app` — uvicorn importa o módulo e **nunca
    chama `run()`**. Resultado: o serviço rodava com o root logger no default
    (`WARNING`), e **todo `logger.info` do pacote inteiro era descartado em
    silêncio** — "webhook trigger: session=…", "endpoint-resolver: … → pool=…",
    survey, collect, delegate. `logger.warning` continuava aparecendo (handler de
    último recurso do Python), o que tornava o defeito quase invisível: os logs
    existiam, só nunca os importantes.

    Como apareceu: um gate da Fase C afirmou "o positivo NÃO resolveu pelo registro"
    porque procurava uma linha INFO. O comportamento estava certo; a EVIDÊNCIA é que
    não existia. Configuração de logging presa ao entry point errado é a mesma
    família de "o aplicador é separado da fonte": o código está lá e não roda.

    Idempotente: `basicConfig` é no-op se o root já tem handler, então chamar aqui e
    de novo em `run()` não duplica saída.
    """
    logging.basicConfig(
        level  = os.getenv("PLUGHUB_LOG_LEVEL", "INFO").upper(),
        format = "%(asctime)s %(levelname)s %(name)s — %(message)s",
    )


_configure_logging()

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
    # Link delivery: per-tenant provider selection from config-api (survey.link_delivery);
    # webhook auth secret from env. Defaults to mock (dev log) when unconfigured.
    _survey_delivery = SurveyLinkDelivery(config_api_url=settings.config_api_url)
    _survey_web = SurveyWebService(
        redis          = _redis,
        producer       = _producer,
        dialog_api_url = settings.dialog_api_url,
        signals_topic  = settings.kafka_topic_signals,
        ttl_s          = settings.survey_web_ttl_s,
        # Público base URL para o link (SMS/e-mail); vazio = caminho relativo.
        base_url       = getattr(settings, "survey_web_base_url", "") or "",
        delivery       = _survey_delivery,
        # S8/S9 — persist-first da resposta operacional (verbatim/áudio LGPD).
        evaluation_api_url       = getattr(settings, "evaluation_api_url", "") or "",
        evaluation_service_token = getattr(settings, "evaluation_service_token", "") or "",
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
                    namespace = event.get("namespace")
                    if namespace == "webchat":
                        await webchat_config.reload(settings.config_api_url, settings.tenant_id)
                        logger.info(
                            "config.changed: webchat namespace reloaded (key=%s)",
                            event.get("key"),
                        )
                    elif namespace == "survey" and _survey_web is not None:
                        # link_delivery config changed → drop the cached provider config.
                        _survey_web.invalidate_delivery_config()
                        logger.info("config.changed: survey link_delivery cache invalidated (key=%s)", event.get("key"))
                except Exception as exc:
                    logger.warning("config.changed consumer error: %s", exc)
        finally:
            await consumer.stop()

    def _supervise(name: str, task: asyncio.Task) -> asyncio.Task:
        """
        Faz a MORTE de uma task de background aparecer.

        ⚠️ Conserto de uma cegueira medida em 2026-08-07. Estas cinco tasks rodam sob
        `asyncio.create_task` e **ninguém as aguarda** — se a corrotina levanta, a
        exceção fica presa no objeto Task e some. O serviço segue de pé, saudável no
        `/health`, com um consumidor a menos. O sintoma aparece longe: no caso que
        expôs isto, "revogar token não vale" — três camadas abaixo, num gate.
        `outbound`, `collect` e `config` têm a MESMA exposição desde sempre; a
        diferença é que ninguém tinha perguntado.

        Não reinicia a task de propósito: reiniciar em laço esconderia uma falha
        permanente atrás de ruído. Isto aqui é o alarme; a política de recuperação é
        decisão à parte, e precisa do alarme para ser tomada.
        """
        def _done(t: asyncio.Task) -> None:
            if t.cancelled():
                return                      # shutdown normal
            exc = t.exception()
            if exc is not None:
                logger.error(
                    "task de background '%s' MORREU: %s — o serviço segue de pé SEM "
                    "ela. Reinicie o channel-gateway depois de tratar a causa.",
                    name, exc, exc_info=exc,
                )
            else:
                logger.warning(
                    "task de background '%s' TERMINOU sozinha (sem exceção) — "
                    "consumidores não deveriam retornar enquanto o serviço vive.",
                    name,
                )
        task.add_done_callback(_done)
        return task

    pubsub_task     = _supervise("registry-pubsub", asyncio.create_task(_registry.start_pubsub_listener()))
    outbound_task   = _supervise("outbound",        asyncio.create_task(outbound.run()))
    collect_task    = _supervise("collect-events",  asyncio.create_task(_collect_events_consumer()))
    config_task     = _supervise("config-changed",  asyncio.create_task(_config_changed_consumer()))
    # Invalidação do cache de endereço por `registry.changed`. Sem isto, revogar ou
    # rotacionar um token de endpoint só passa a valer depois do TTL do cache
    # (`endpoint_cache_ttl_s`) — o gateway seguiria aceitando a credencial revogada.
    invalidation_task = _supervise(
        "registry-invalidation",
        asyncio.create_task(RegistryInvalidationConsumer(settings).run()),
    )
    # Arc 19 Fase D: expira suspends/delegates webhook vencidos (resume_tokens)
    timeout_scan_task = asyncio.create_task(_webhook_adapter.run_timeout_scanner())

    logger.info("✅ Channel Gateway started (instance=%s)", instance_id)
    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    pubsub_task.cancel()
    outbound_task.cancel()
    collect_task.cancel()
    config_task.cancel()
    invalidation_task.cancel()
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
    # T3 — PERTENÇA (distinta da proveniência, que é o origin_session_id acima).
    # "inherit" (default): entra na journey do chamador.
    # "new": inicia a PRÓPRIA journey (raiz = ela mesma), mantendo o fio de proveniência.
    # Use quando o cliente pediu algo SEM RELAÇÃO com o processo em curso.
    journey:           str = "inherit"

class WebhookResumeRequest(BaseModel):
    tenant_id: str
    payload:   dict | None = None
    # Identity Resolver (nível b §11) — how the customer returned: same_channel|token|identity.
    # Default "token" (explicit resume_token path). "identity" set by the cross-channel
    # reconnect-offer flow so session_resumed carries the provenance.
    resume_origin: str = "token"
    # A5 — claimant binding for INTERNAL approval resume. The Console sends the pool of
    # the approval task + its instance_id (`human-{userId}`); the ingress reads the claim
    # lease (via the routing arbiter) and requires caller==claimant. Absent for external/
    # system resumes (which stay on the credential/claimed path).
    pool_id:     str | None = None
    instance_id: str | None = None

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
    # Camada B (pull direcionado / "ramal") — reserva do item ao recurso + transbordo.
    assigned_to:              str | None = None
    fallback_to_pool_after_s: int | None = None
    # Wrap-up unificado (Camada E2) — auto-atendimento no Console (inline).
    auto_attend:              bool = False

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
        assigned_to              = body.assigned_to or "",
        fallback_to_pool_after_s = body.fallback_to_pool_after_s,
        auto_attend              = body.auto_attend,
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


@app.post("/v1/channels/webhook/collect", status_code=201)
async def webhook_collect(request: Request) -> dict:
    """
    Journey J4c — N2 collect handler. Called by the skill-flow-service persistCollect
    callback when a `collect` step fires. Negotiates the channel (process-agnostic),
    resolves the survey pool, and creates a ROUTED child contact session (journey
    member N1) — so tenant concurrency quota, pool max_concurrent_sessions and Core
    `sessions` metering are all enforced on admission.

    Declared BEFORE the greedy /{skill_id} route. Returns { send_at, expires_at }.
    """
    if _webhook_adapter is None:
        raise HTTPException(status_code=503, detail="Webhook adapter not initialised")

    settings = get_settings()
    body     = await request.json()
    tenant_id = body.get("tenant_id") or settings.tenant_id
    if not body.get("session_id") or not body.get("collect_token") or not body.get("step_id"):
        raise HTTPException(status_code=400, detail="session_id, step_id and collect_token required")

    try:
        result = await _webhook_adapter.handle_collect(
            tenant_id            = tenant_id,
            session_id           = body["session_id"],
            customer_id          = body.get("customer_id") or "",
            step_id              = body["step_id"],
            collect_token        = body["collect_token"],
            target               = body.get("target") or {},
            interaction          = body.get("interaction") or "text",
            prompt               = body.get("prompt") or "",
            channel              = body.get("channel"),
            requires             = body.get("requires"),
            channel_policy       = body.get("channel_policy"),
            options              = body.get("options"),
            fields               = body.get("fields"),
            dialog_form_id       = body.get("dialog_form_id") or "",
            # S2 — grão do sinal (config do deploy). Default `journey` = o que os
            # collects pré-S2 faziam hardcoded no runner.
            signal_grain         = body.get("signal_grain") or "journey",
            timeout_hours        = float(body.get("timeout_hours") or 48),
            campaign_id          = body.get("campaign_id") or "",
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"send_at": result["send_at"], "expires_at": result["expires_at"]}


@app.post("/v1/channels/webhook/pool/{pool_id}", status_code=201)
async def webhook_trigger_by_pool(pool_id: str, request: Request) -> dict:
    """
    S4 — Trigger endereçado por POOL (canônico).

    **O pool é a unidade endereçável; skill + config são detalhe INTERNO do seu deploy**
    (slot `current` + `config_json`). Endereçar por `skill_id` reabre a pergunta que o
    modelo de slots existe para fechar — "qual config está rodando?" —, porque o mesmo
    skill pode estar deployado em N pools com configs diferentes (é o desenho do survey:
    um `skill_survey_outbound_v1` em três pools, um por grão). Nesse regime a resolução
    por skill é AMBÍGUA e o router escolheria um pool por score, em silêncio.

    Com `pool_id`, o routing engine atribui o pool DIRETO e o bridge executa o snapshot
    do slot `current` daquele pool. Sem DNIS, sem resolução, sem ambiguidade.

    Declarada ANTES da rota greedy /{skill_id}. Retorna { session_id }.
    """
    if _webhook_adapter is None:
        raise HTTPException(status_code=503, detail="Webhook adapter not initialised")

    settings = get_settings()
    body     = await request.json()

    session_id = await _webhook_adapter.handle_trigger(
        skill_id          = "",            # endereço é o pool — o skill vem do slot
        pool_id           = pool_id,
        tenant_id         = body.get("tenant_id") or settings.tenant_id,
        trigger_type      = body.get("trigger_type") or "task",
        metadata          = body.get("metadata"),
        customer_id       = body.get("customer_id"),
        origin_session_id = body.get("origin_session_id"),
        root_session_id   = body.get("root_session_id"),
        context           = body.get("context"),
        # T3: pertença — "new" faz a sessão nascer como sua própria raiz (journey nova),
        # sem apagar a proveniência (origin_session_id segue apontando para o pai).
        journey           = body.get("journey") or "inherit",
    )
    return {"session_id": session_id}


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


@app.get("/v1/channels/webhook/identity/customers/search", status_code=200)
async def webhook_identity_customers_search(tenant_id: str, q: str, limit: int = 20) -> dict:
    """
    Cadastro manual (C1a — Cliente 360): busca de clientes por `customer_id` exato
    ou nome (`attributes`). NÃO por âncora (telefone/email exata resolve via
    /identity/resolve). Retorna { count, results: [{customer_id, status, attributes}] }.
    """
    if _webhook_adapter is None:
        return {"count": 0, "results": []}
    return await _webhook_adapter.search_customers(tenant_id=tenant_id, q=q, limit=limit)


@app.get("/v1/channels/webhook/identity/customers/{customer_id}", status_code=200)
async def webhook_identity_customer_get(customer_id: str, tenant_id: str) -> dict:
    """
    Read puro de um cliente por id (cadastro §11). Usado pelo outbound (Fase 3b) para
    consultar `attributes.do_not_contact` (opt-out global). 404 quando ausente — o
    chamador trata como "sem opt-out". Declarado APÓS /customers/search (literal vence
    o path-param na resolução do Starlette).
    """
    if _webhook_adapter is None:
        raise HTTPException(status_code=503, detail="Identity resolver not available")
    cust = await _webhook_adapter.get_customer(tenant_id, customer_id)
    if cust is None:
        raise HTTPException(status_code=404, detail="customer not found")
    return cust


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
            grain=body.get("grain", "session"),   # Journey J4
            # Pool-scoping (Segurança Fase B): pool da sessão pesquisada, congelado no
            # token → carimbado na resposta + session.signals no submit. Vazio = admin-only.
            pool_id=body.get("pool_id", "") or "",
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
    """
    Public survey surface — two vehicles behind one URL.

    **Journey J4c (collect):** when the token is a COLLECT pending, the first open is
    the ENGAGEMENT: the routed inbound survey session is created right here (the
    customer is present), so tenant quota + the survey pool's max_concurrent_sessions
    + Core `sessions` metering apply — to real engagements only, never to un-clicked
    invitations. This page then connects as a normal webchat client and the survey
    pool's dialog_runner renders the DialogForm live (single generic interpreter,
    config-driven by form_id). Journey membership comes from the root seeded in the
    ctx before the inbound (J1 consumer enrichment).

    **Legacy / anonymous (J4b):** any other token → the standalone form page that only
    records a signal (no session). Kept for unsolicited surveys with no known root.
    """
    from fastapi.responses import HTMLResponse

    settings = get_settings()
    if _webhook_adapter is not None:
        try:
            engaged = await _webhook_adapter.handle_collect_engage(
                tenant_id          = settings.tenant_id,
                collect_token      = token,
                jwt_secret_default = settings.jwt_secret,
            )
        except Exception as exc:  # noqa: BLE001 — never 500 a public page
            logger.warning("survey collect engage failed (token=%s): %s", token, exc)
            engaged = None
        if engaged:
            boot = json.dumps({"jwt": engaged["jwt"], "pool_id": engaged["pool_id"]})
            return HTMLResponse(
                content=SURVEY_COLLECT_PAGE_HTML.replace("__SURVEY_BOOTSTRAP__", boot)
            )

    return HTMLResponse(content=SURVEY_PAGE_HTML)


def _check_endpoint_auth(
    ep:         ResolvedEndpoint,
    request:    Request,
    identifier: str,
    tenant_id:  str,
) -> None:
    """
    Autenticação OPCIONAL por endpoint (arco webhook-endpoint-auth). Aplicada pelas
    DUAS portas de webhook, a partir da MESMA função — auth duplicada em dois lugares
    diverge, e a divergência aparece como "uma porta protegida e a outra não".

    `auth_required=False` (o default) ⇒ no-op. Nada do que existe hoje quebra.

    ── Três recusas, uma resposta ────────────────────────────────────────────────
    Header ausente, token errado e endpoint mal configurado saem todos como **401
    sem detalhe**. Distinguir seria contar a um chamador não autenticado que o
    endereço EXISTE e como ele está configurado — o mesmo raciocínio do filtro de
    procedência (§7.6.3). Quem precisa do motivo é o operador, e ele lê o log; por
    isso cada ramo loga a sua causa, com nível diferente.

    ── Fail-CLOSED quando não dá para verificar ─────────────────────────────────
    `auth_required=True` sem `token_hash` tem duas causas possíveis, e as duas levam
    à mesma decisão: recusar.
      · o gateway não tem credencial de serviço ⇒ o registry OMITE o hash. Aqui
        "não sei verificar" jamais pode virar "está autorizado" — é a diferença
        entre um portão fechado e um portão que some quando a luz apaga.
      · a linha está mesmo sem token (estado que a revogação evita criar, mas que
        uma escrita direta no banco produziria).
    O log distingue os dois para o operador; a resposta, não.
    """
    if not ep.auth_required:
        return

    presented = request.headers.get("x-webhook-token", "")

    if not ep.token_hash:
        settings = get_settings()
        if not settings.agent_registry_service_token:
            logger.error(
                "AUTH webhook: endpoint '%s' exige token, mas o gateway NÃO tem "
                "credencial de serviço (PLUGHUB_AGENT_REGISTRY_SERVICE_TOKEN) — o "
                "registry omite o token_hash e não há contra o que comparar. "
                "RECUSANDO (fail-closed): 'não sei verificar' não é 'autorizado'. "
                "tenant=%s", identifier, tenant_id,
            )
        else:
            logger.error(
                "AUTH webhook: endpoint '%s' tem auth_required=true e NENHUM token "
                "configurado — estado impossível de satisfazer (recusa 100%%). "
                "Gere um token (POST /v1/channel-endpoints/{id}/token) ou revogue "
                "para voltar a anônimo. tenant=%s", identifier, tenant_id,
            )
        raise HTTPException(status_code=401, detail="Unauthorized")

    if not presented:
        logger.warning(
            "AUTH webhook: disparo em '%s' SEM header X-Webhook-Token (tenant=%s)",
            identifier, tenant_id,
        )
        raise HTTPException(status_code=401, detail="Unauthorized")

    # Comparação em tempo constante sobre o digest — `==` sobre hash vaza, pelo
    # tempo, quantos caracteres iniciais bateram.
    computed = hashlib.sha256(presented.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(computed, ep.token_hash):
        logger.warning(
            "AUTH webhook: token INVÁLIDO em '%s' (tenant=%s)", identifier, tenant_id,
        )
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.post("/channel/webhook/{slug}", status_code=201)
async def webhook_endpoint_trigger(slug: str, body: WebhookTriggerRequest, request: Request) -> dict:
    """
    External webhook endpoint trigger (channel-endpoint model, like webchat).

    The `slug` is a stable channel endpoint configured in
    config/channels/webhook (a ChannelEndpoint record: slug → pool). It resolves
    to a POOL, and the pool runs whatever skill is currently DEPLOYED to it — so
    the public URL stays stable across skill versions (no skill_id in the URL).

    This is the recommended external entry point. The internal, skill_id-keyed
    path (POST /v1/channels/webhook/{skill_id}, used by workflow_trigger) is kept
    for backward compatibility and internal intake flows.

    ── ADR adr-webhook-endpoint-single-registry §7.6.3 ───────────────────────────
    **Esta porta serve apenas endpoints de procedência `external`.**

    A Fase B semeou uma linha para cada endereço INTERNO, e como esta rota sempre
    resolveu pelo registro, os dez passaram a responder aqui também — 404 antes,
    201 depois. Foi mudança de comportamento não prevista pela fase, medida em
    2026-08-07. Não era falha de autenticação (as duas rotas vivem no mesmo gateway
    e nenhuma exige credencial), mas apagava a distinção que o próprio docstring
    acima declara: um ambiente que publique este prefixo na borda e mantenha `/v1/*`
    restrito passaria a expor endereços internos.

    A escolha entre "aceitar as duas portas" e "filtrar" não se decide pelo código,
    e sim pela topologia — mas os dois erros custam diferente: aceitar e a topologia
    divergir depois expõe endereço interno EM SILÊNCIO; filtrar e a exposição ser
    uniforme custa um alias que ninguém usa. Filtramos.

    Returns: { session_id }
    """
    if _webhook_adapter is None:
        raise HTTPException(status_code=503, detail="Webhook adapter not initialised")

    settings  = get_settings()
    tenant_id = body.tenant_id or settings.tenant_id
    resolved = ResolvedEndpoint(None, None, False, None, "unavailable")
    if settings.agent_registry_url:
        resolved = await resolve_endpoint(
            channel            = "webhook",
            identifier         = slug,
            tenant_id          = tenant_id,
            agent_registry_url = settings.agent_registry_url,
            cache_ttl_s        = settings.endpoint_cache_ttl_s,
            allowed_origins    = frozenset({"external"}),
            service_token      = settings.agent_registry_service_token,
        )
    pool_id = resolved.pool_id
    outcome = resolved.outcome
    if not pool_id:
        # A RESPOSTA não distingue os motivos; o LOG distingue. Devolver 403 (ou
        # dizer "existe, mas é interno") confirmaria a existência do endereço a
        # quem chama de fora — o oposto do que filtrar pretende. Quem precisa saber
        # é o operador, e ele lê o log; degradar sem dizer por quê é que não vale.
        if outcome == "origin_refused":
            logger.warning(
                "webhook externo: '%s' EXISTE mas é de procedência interna — recusado "
                "nesta porta (tenant=%s). Endereços internos são acionáveis apenas em "
                "/v1/channels/webhook/{identifier}. Ver ADR §7.6.3.",
                slug, tenant_id,
            )
        raise HTTPException(
            status_code=404,
            detail=f"No webhook endpoint '{slug}' configured for this tenant",
        )

    # Autenticação DEPOIS da resolução e ANTES de criar sessão: só se pode exigir a
    # credencial de um endpoint depois de saber qual endpoint é, e nada deve ser
    # criado antes de a credencial ser aceita.
    _check_endpoint_auth(resolved, request, slug, tenant_id)

    session_id = await _webhook_adapter.handle_trigger(
        skill_id           = "",            # pool-driven: runs the pool's deployed skill
        tenant_id          = tenant_id,
        trigger_type       = body.trigger_type,
        metadata           = body.metadata,
        customer_id        = body.customer_id,
        origin_session_id  = body.origin_session_id,
        context            = body.context,
        pool_id            = pool_id,        # direct pool assignment (stable-URL path)
    )
    return {"session_id": session_id}


@app.post("/v1/channels/webhook/{skill_id}", status_code=201)
async def webhook_trigger(skill_id: str, body: WebhookTriggerRequest, request: Request) -> dict:
    """
    Trigger a new webhook workflow session — endereço interno.

    ── Fase C do ADR adr-webhook-endpoint-single-registry ────────────────────────
    O path param passou a ser tratado como **`identifier` OPACO** (D2), resolvido
    pelo **registro** (`ChannelEndpoint`, D1) — não mais como "o skill que roda".
    O nome do parâmetro segue `skill_id` porque é ele que forma a URL pública e os
    chamadores internos mandam a MESMA string (D4: é backfill, não reescrita);
    renomear a variável mudaria a rota e não mudaria nada de verdade.

    ── Fase E (2026-08-07): o FALLBACK SAIU ──────────────────────────────────────
    O registro é agora o **único** resolvedor. Não resolveu ⇒ a sessão não nasce.

      · `not_found`   → **404** nomeado. O endereço não existe: semeie a linha.
      · `unavailable` → **503** + `Retry-After`. **NUNCA 404** — 404 afirmaria que o
        endereço não existe por causa de um soluço de rede do agent-registry, e o
        chamador (fire-and-forget, na maioria) desistiria de um disparo legítimo.
        Falha de infraestrutura é retentável; endereço inexistente não é.

    A distinção existe porque `resolve_pool_ex` a preserva desde a Fase C. Ela era
    ornamental enquanto o fallback absorvia os dois casos; virou load-bearing aqui.

    **`skill_id` continua no evento.** Deixou de ser chave de roteamento (é o
    `pool_id` que roteia), mas segue sendo o registro de qual endereço foi discado —
    o papel de DNIS (D5). Zerá-lo apagaria a única evidência do endereço no evento.

    Returns: { session_id }
    """
    if _webhook_adapter is None:
        raise HTTPException(status_code=503, detail="Webhook adapter not initialised")

    settings   = get_settings()
    identifier = skill_id
    # SEM default de tenant aqui, de propósito: o caminho da slug usa
    # `body.tenant_id or settings.tenant_id`, mas nesta rota o `handle_trigger`
    # sempre recebeu `body.tenant_id` cru. Defaultar só na CONSULTA criaria uma
    # divergência nova — resolver no tenant padrão e abrir a sessão noutro — para
    # ganhar nada: tenant vazio já estava quebrado antes desta fase, e com o
    # default ele passaria a resolver, o que é mudança de comportamento
    # justamente onde a Fase C promete não ter nenhuma.
    tenant_id  = body.tenant_id

    resolved = ResolvedEndpoint(None, None, False, None, "unavailable")
    if settings.agent_registry_url:
        resolved = await resolve_endpoint(
            channel            = "webhook",
            identifier         = identifier,
            tenant_id          = tenant_id,
            agent_registry_url = settings.agent_registry_url,
            cache_ttl_s        = settings.endpoint_cache_ttl_s,
            service_token      = settings.agent_registry_service_token,
        )
        pool_id = resolved.pool_id
        outcome = resolved.outcome
    else:
        pool_id = None
        outcome = "no_registry_url"

    if not pool_id:
        # ── Fase E — sem fallback: a recusa é a resposta ──────────────────────
        # Os dois motivos pedem ações OPOSTAS de quem chama, então saem com status
        # diferentes. Colapsá-los num 404 só seria seguro enquanto o fallback
        # existia para absorver o engano.
        if outcome == "unavailable" or outcome == "no_registry_url":
            logger.error(
                "webhook trigger: registro INALCANÇÁVEL ao resolver identifier=%s "
                "(motivo=%s, tenant=%s) — 503. Isto NÃO diz que o endereço não "
                "existe; diz que não deu para perguntar. Retentável.",
                identifier, outcome, tenant_id,
            )
            raise HTTPException(
                status_code=503,
                detail=(
                    f"Endpoint registry unavailable — could not resolve "
                    f"'{identifier}'. This is retryable; it does not mean the "
                    f"endpoint is unknown."
                ),
                headers={"Retry-After": "5"},
            )
        logger.warning(
            "webhook trigger: identifier=%s SEM linha no registro (tenant=%s) — 404. "
            "Todo endereço acionável precisa de um ChannelEndpoint(channel=webhook); "
            "declare-o em infra/registry/*.yaml (origin=internal) ou cadastre na tela.",
            identifier, tenant_id,
        )
        raise HTTPException(
            status_code=404,
            detail=f"No webhook endpoint '{identifier}' configured for this tenant",
        )

    # Mesma ordem da porta externa: resolve, autentica, só então cria.
    _check_endpoint_auth(resolved, request, identifier, tenant_id)

    logger.info(
        "webhook trigger: identifier=%s → pool=%s (via REGISTRO, tenant=%s)",
        identifier, pool_id, tenant_id,
    )

    session_id = await _webhook_adapter.handle_trigger(
        # Endereço discado. Chave de roteamento só quando pool_id é None (fallback).
        skill_id           = identifier,
        tenant_id          = body.tenant_id,
        trigger_type       = body.trigger_type,
        metadata           = body.metadata,
        customer_id        = body.customer_id,
        origin_session_id  = body.origin_session_id,
        context            = body.context,
        journey            = body.journey,   # T3: inherit | new
        pool_id            = pool_id,        # Fase C: registro resolve → pool direto
    )
    return {"session_id": session_id}


def _resolve_approver_principal(
    request: Request,
    body: WebhookResumeRequest,
    required_abac: tuple[str, str] | None = None,
) -> dict | None:
    """
    A5 — resolve o principal AUTOR do resume + classe de confiança, a partir do header.

    Header `Authorization: Bearer <jwt>` válido → HUMANO logado (possessed): verifica
    assinatura (auth_jwt_secret) + a auto-consistência instance==human-{sub}. O check de
    POSSE do claim (o caller detém a lease) é feito no handle_resume via o árbitro (precisa
    do session_id). Ausente → None: caminho externo/sistema (claimed), inalterado. Falha de
    verificação → HTTPException 403 (atribuir a decisão a quem não é o autor é pior que não
    atribuir).

    Camada E2 — `required_abac` (modulo, campo) é resolvido SERVER-SIDE do contexto da
    workflow suspensa (`resume_required_abac`): quando setado (ex.: APROVAÇÃO →
    ("approvals","decide")), aplica esse ABAC + pool-scope a não-elevados. Quando None
    (form-fill genérico, ex.: wrap-up), NÃO exige ABAC de aprovação — o binding do claim
    (instance==human-{sub} + caller==claimant no handle_resume) já autoriza o operador comum.
    """
    token = bearer_from_header(request.headers.get("Authorization"))
    if not token:
        return None  # external / system path (claimed) — unchanged

    settings = get_settings()
    if not settings.auth_jwt_secret:
        # Verificação desabilitada (segredo não wirado) → NÃO bloqueia e NÃO finge
        # possessed: cai no caminho externo (claimed), com aviso (degradação não-silenciosa).
        logging.getLogger(__name__).warning(
            "A5: PLUGHUB_AUTH_JWT_SECRET não configurado — token do aprovador NÃO "
            "verificado; resume tratado como externo/claimed",
        )
        return None
    _log = logging.getLogger(__name__)
    payload = verify_user_jwt(token, settings.auth_jwt_secret)
    if payload is None:
        _log.warning("A5 403 invalid_token: JWT não verificou com auth_jwt_secret (segredo não bate com o auth-api?)")
        raise HTTPException(status_code=403, detail="approval: invalid or expired approver token")
    if payload.get("tenant_id") and payload["tenant_id"] != body.tenant_id:
        _log.warning("A5 403 tenant_mismatch: jwt=%s body=%s", payload.get("tenant_id"), body.tenant_id)
        raise HTTPException(status_code=403, detail="approval: tenant mismatch")
    # ABAC por TIPO DE TAREFA (Camada E2) — admin/supervisor bypassam por role (mesma
    # semântica do passesAbac da plataforma; contas elevadas não carregam module_config
    # por campo). Elevados têm autoridade sobre todos os pools e bypassam ABAC E pool-scope
    # (o pool-scope já foi exercido no inbox/claim; re-exigir cria assimetria claim↔resume).
    roles = payload.get("roles")
    roles = roles if isinstance(roles, list) else []
    is_elevated = ("admin" in roles) or ("supervisor" in roles)
    if not is_elevated and required_abac is not None:
        _mod, _field = required_abac
        # Tarefa que EXIGE capacidade (ex.: aprovação → approvals.decide). Form-fill
        # genérico (required_abac=None, ex.: wrap-up) NÃO cai aqui: o binding do claim
        # autoriza o operador comum (senão o agente de wrap-up tomaria 403 indevido).
        if not abac_can(payload, _mod, _field, "write_only"):
            _log.warning("E2 403 abac: roles=%s sem %s.%s", roles, _mod, _field)
            raise HTTPException(status_code=403, detail=f"resume: missing {_mod}.{_field}")
        if body.pool_id and not pool_in_scope(payload, body.pool_id):
            _log.warning("E2 403 pool_scope: pool=%s accessible_pools=%s", body.pool_id, accessible_pools(payload))
            raise HTTPException(status_code=403, detail="resume: pool not accessible")

    sub = str(payload.get("sub") or "")
    # Auto-consistência: a instância que o Console envia deve pertencer ao usuário do JWT.
    if body.instance_id and body.instance_id != f"human-{sub}":
        _log.warning("A5 403 instance_mismatch: instance_id=%s esperado=human-%s", body.instance_id, sub)
        raise HTTPException(status_code=403, detail="approval: instance/identity mismatch")
    _log.info("A5 approver OK: sub=%s roles=%s pool=%s instance=%s", sub, roles, body.pool_id, body.instance_id)

    return {
        "principal_type":     "human",
        "decided_by":         sub,
        "verification_class": "possessed",
    }


@app.post("/v1/channels/webhook/resume/{resume_token}", status_code=200)
async def webhook_resume(resume_token: str, body: WebhookResumeRequest, request: Request) -> dict:
    """
    Resume a suspended webhook session using the resume_token generated at suspend time.

    The token is resolved to a session_id via Redis hash {tenant_id}:resume_tokens.
    After resume, the routing engine reallocates a skill-flow instance to continue
    the workflow from the step that suspended.

    A5 — when an `Authorization: Bearer` header is present, the caller is a HUMAN
    approver: the principal is verified (JWT + ABAC + claimant) and threaded into
    handle_resume so the decision is authored/audited as the approver. Absent header
    → external/system (claimed), unchanged.

    Returns: { session_id }
    Raises 404 if the token is unknown/expired; 403 if approver verification fails;
    **409** (Fase F / D7) if another trigger already terminalised this resume, or is
    terminalising it right now — with `closed_by` / `cause` in the detail, so the
    caller can say *which* of the three ended it instead of "session expired".
    """
    if _webhook_adapter is None:
        raise HTTPException(status_code=503, detail="Webhook adapter not initialised")

    # Camada E2 — descobre SERVER-SIDE qual ABAC a submissão exige (aprovação vs
    # form-fill genérico), do contexto da workflow suspensa. Só então gateia.
    required_abac = await _webhook_adapter.resume_required_abac(body.tenant_id, resume_token)
    approver = _resolve_approver_principal(request, body, required_abac)

    try:
        session_id = await _webhook_adapter.handle_resume(
            resume_token      = resume_token,
            tenant_id         = body.tenant_id,
            payload           = body.payload,
            resume_origin     = body.resume_origin,
            approver          = approver,
            claim_pool_id     = body.pool_id,
            claim_instance_id = body.instance_id,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ResumeAlreadyTerminalError as exc:
        # Fase F (D7) — 409, nunca 404. O 404 afirma que o token não existe; o
        # agente cujo item o supervisor acabou de encerrar recebia essa frase e
        # concluía que a própria sessão tinha vencido. `state` separa a corrida
        # real (`in_flight`) do caso sequencial (`terminal`).
        raise HTTPException(status_code=409, detail=exc.as_detail())
    if session_id is None:
        raise HTTPException(
            status_code=404,
            detail="Resume token not found or expired",
        )
    return {"session_id": session_id}


class ExternalResumeRequest(BaseModel):
    """
    Corpo da porta EXTERNA de resume. Deliberadamente MENOR que o
    `WebhookResumeRequest` interno — cada campo que falta aqui é um campo que um
    terceiro não pode declarar. Ver `external_webhook_resume`.
    """
    tenant_id: str
    payload:   dict | None = None


@app.post("/channel/webhook/resume/{resume_token}", status_code=200)
async def external_webhook_resume(
    resume_token: str, body: ExternalResumeRequest,
) -> dict:
    """
    Porta EXTERNA de resume — D8 do ADR journey/session/segment, Fase 1 do arco de
    workflow (`docs/product/workflow-arc-implementation-spec.md`).

    Simétrica ao trigger (`POST /channel/webhook/{slug}`) em prefixo e em classe de
    alcance. **Não passa pelo registro de `ChannelEndpoint`**: não há endereço a
    registrar. O `resume_token` é uma *capability* — opaco, ligado a UMA sessão, de
    uso único (Camada F) — e não endereça pool nem canal, endereça *execução
    suspensa*. A posse do token é a credencial, como no link público de survey.

    Reusa `handle_resume` INTEIRO (lock da Fase F, registro terminal, 404 × 409,
    consumo do token). A porta é fina de propósito: duplicar a máquina de unicidade
    criaria a segunda fonte que o arco anterior gastou seis fases removendo.

    ⚠️ **TRÊS diferenças em relação à rota interna, e nenhuma é cosmética:**

    1. **`source` NÃO é asserido pelo chamador.** A rota interna repassa o payload
       verbatim, e `_terminal_cause` lê `payload["source"]` — logo um chamador podia
       declarar `source:"supervisor:x"` e obter o carimbo `acw_supervisor_closed` no
       registro terminal DURÁVEL de 25 h, que é o que o Console mostra ao agente
       como *"encerrado por …"*. Aqui o campo é **descartado e reescrito** como
       `external`. Sem principal verificado não existe encerramento de supervisor.
    2. **`decision` de encerramento não é aceita.** `decision:"timeout"` é o que
       separa `task_done` de `acw_*` em `_terminal_cause`; deixá-la passar daria a
       um terceiro o poder de marcar o item como expirado/encerrado por supervisor.
       Um sistema externo *responde* um resume; ele não encerra o trabalho de um
       humano.
    3. **`resume_origin` é fixo em `token`** — é o único valor honesto por esta
       porta. `identity`/`same_channel` descrevem caminhos internos, e aceitá-los
       de fora seria deixar o chamador escolher o próprio rótulo analítico.

    Sem `pool_id`/`instance_id`: são o caminho de posse (A5) do Console, que exige
    principal. Sem `Authorization`: quem tem JWT usa a porta interna.

    Retorna `{ session_id }`. 404 token desconhecido/vencido · 409 já terminal ou em
    curso (Camada F) · 503 adapter fora.
    """
    if _webhook_adapter is None:
        raise HTTPException(status_code=503, detail="Webhook adapter not initialised")

    # Saneamento do payload ANTES de qualquer uso. Campos de autoridade são
    # removidos, não validados: recusar com 4xx ensinaria ao chamador que eles
    # existem, e aceitar-os-ignorando é o comportamento que já se espera de um
    # corpo livre.
    safe_payload = dict(body.payload or {})
    _dropped = [k for k in ("source", "decision") if k in safe_payload]
    for k in _dropped:
        safe_payload.pop(k, None)
    safe_payload["source"] = "external"
    if _dropped:
        logger.info(
            "external resume: campo(s) de autoridade descartado(s) %s do payload "
            "(token=%s tenant=%s) — esta porta não autentica quem os declara",
            _dropped, resume_token, body.tenant_id,
        )

    try:
        session_id = await _webhook_adapter.handle_resume(
            resume_token  = resume_token,
            tenant_id     = body.tenant_id,
            payload       = safe_payload,
            resume_origin = "token",
            approver      = None,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ResumeAlreadyTerminalError as exc:
        raise HTTPException(status_code=409, detail=exc.as_detail())
    if session_id is None:
        raise HTTPException(status_code=404, detail="Resume token not found or expired")
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
    # Já configurado no import (`_configure_logging`), porque o container sobe por
    # `uvicorn …:app` e nunca passa por aqui. Mantido para o caso `python -m`;
    # `basicConfig` é no-op se o root já tem handler.
    _configure_logging()
    uvicorn.run(
        "plughub_channel_gateway.main:app",
        host   = "0.0.0.0",
        port   = 8010,
        reload = False,
    )


if __name__ == "__main__":
    run()
