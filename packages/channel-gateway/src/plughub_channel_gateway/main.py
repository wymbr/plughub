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

import httpx
from plughub_contextstore.loader import set_context_map_fetcher

import asyncpg
from datetime import datetime, timezone

from . import session_parking
import redis.asyncio as aioredis
import uvicorn
from aiokafka import AIOKafkaProducer
from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .adapters.email import EmailAdapter
from .adapters.sms import SMSAdapter
from .adapters.voice import VoiceAdapter
from .adapters.webchat import WebchatAdapter
from .adapters.webchat_channel import WebchatChannelAdapter
from .adapters.webhook import ResumeAlreadyTerminalError, WebhookAdapter
from .adapters.webrtc import MaskedCollectInProgress, WebRTCAdapter
from .adapters.voice_router import VoiceChannelRouter
from .adapters.whatsapp import WhatsAppAdapter
from .arrival_evidence import ArrivalEvidenceRecorder
from .resume_authority import judge_external_decision
from .attachment_store import (
    AttachmentStore,
    FilesystemAttachmentStore,
    S3AttachmentStore,
)
from .attachment_expiry import run_attachment_expiry
from .sip_trunk_watch import run_sip_trunk_watch
from .channel_capability_registry import (
    select_channel,
)
from . import speech_catalog
from .config import get_settings, Settings
# Verificador canônico (passo 3 da consolidação, 2026-08-28). O import vem DIRETO do
# pacote, não re-exportado por `.auth`: um re-export deixaria `auth.py` parecendo dono
# do verificador, que é exatamente a aparência que criou as seis cópias. De `.auth`
# ficam só os dois nomes que são fato do channel-gateway — o wrapper que carimba a
# origem no log de escopo, e o helper de log da lista crua.
from plughub_authz import abac_can, bearer_from_header, verify_user_jwt

from .auth import accessible_pools, pool_in_scope
from .speech_config import PROFILE_ID_RE, VOICE_PARAMS, conferir_forma
from .identity_auth import identity_principal, tenant_for
from .context_reader import ContextReader
from .endpoint_resolver import ResolvedEndpoint, resolve_endpoint, resolve_pool
from .outbound_consumer import OutboundConsumer
from .registry_invalidation_consumer import RegistryInvalidationConsumer
from .webchat_config import webchat_config
from .session_registry import SessionRegistry
from .survey_web import (
    ArchivedFormError,
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
# APR-10: a rota de encerramento por parque durável consulta o Postgres. O pool
# nascia e morria dentro do `lifespan`; a rota precisa dele por fora.
_db_pool:            asyncpg.Pool                         | None = None
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

    if not settings.channel_gateway_service_token:
        # IDN-06 — nomeia o que deixa de valer: a porta fica FECHADA, não aberta.
        logger.warning(
            "PLUGHUB_CHANNEL_GATEWAY_SERVICE_TOKEN vazio: as rotas de identidade/pendencia "
            "RECUSAM chamador de servico (401) — mcp-server (pending_workflow_get, "
            "customer_resolve, otp_*) e mailing-api (opt-out global) deixam de funcionar"
        )

    # ALW-02 — transporte do carregador de config do ContextStore (mapa + catalogo de
    # tipos), registrado UMA vez no boot.
    #
    # ⚠️ FALTAVA AQUI, e ficou invisivel por dois dias. Os sitios de escrita foram
    # migrados para o funil em 2026-09-02 e este registro nao; como o carregador do MAPA
    # tem fallback embutido, as escritas continuaram funcionando — carimbadas com
    # `atributo.fallback: true`, que era o sinal, e ninguem o contava. Medido ao vivo:
    # 16 de 16 entradas com fallback, e campos DECLARADOS saindo como `unknown` porque o
    # mapa embutido nao tem o vocabulario do tenant.
    #
    # Quem denunciou foi o CATALOGO de tipos, que NAO tem fallback de proposito: o
    # preview saiu com tudo `***` na primeira execucao. A resiliencia de um escondeu a
    # fiacao faltando; a recusa do outro a expos em uma rodada.
    async def _ctx_cfg_fetch(url: str) -> object:
        async with httpx.AsyncClient(timeout=5.0) as c:
            resp = await c.get(url)
            resp.raise_for_status()
            return resp.json()

    set_context_map_fetcher(_ctx_cfg_fetch)

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
    global _db_pool
    _db_pool = db_pool
    # RET-11: o registro duravel do parque precisa existir antes de o
    # consumidor tentar escrever nele.
    await session_parking.ensure_schema(db_pool)

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
        producer         = _producer,
        redis            = _redis,
        settings         = settings,
        registry         = _registry,
        context_reader   = _context,
        # VOZ-06 — sem isto a gravação é feita e NÃO guardada (medido pelo probe_voz06: a parte 1
        # gravou, parou antes do PIN e morreu em "sem AttachmentStore"). A Phase D tinha o mesmo
        # buraco e ninguém viu, porque ela nunca rodou.
        attachment_store = _attachment_store,
    )
    _webhook_adapter = WebhookAdapter(
        producer = _producer,
        redis    = _redis,
        settings = settings,
        db_pool  = db_pool,     # Identity Resolver Slice 2 — durable PG store
    )
    await _webhook_adapter.ensure_identity_schema()
    # PID-09 — a chegada pelo WhatsApp consulta o MESMO índice de identidade do webhook
    # adapter (um salt, um resolvedor de região) e grava pelo escritor único do mcp-server.
    _whatsapp_adapter.attach_arrival_evidence(ArrivalEvidenceRecorder(
        identity      = _webhook_adapter.identity_index,
        mcp_url       = settings.mcp_server_url,
        service_token = settings.mcp_internal_service_token,
    ))

    _channel_adapters = {
        "webchat":  WebchatChannelAdapter(registry=_registry),
        "whatsapp": _whatsapp_adapter,
        "sms":      _sms_adapter,
        "email":    _email_adapter,
        # VOZ-02: `voice` tem duas pernas — a SIP (sessão do adapter WebRTC, a sala) e o legado
        # Twilio. A saída vai para quem ABRIU a sessão; ver `adapters/voice_router.py`.
        "voice":    VoiceChannelRouter(_webrtc_adapter, _voice_adapter),
        "webrtc":   _webrtc_adapter,
        "webhook":  _webhook_adapter,
    }

    outbound = OutboundConsumer(adapters=_channel_adapters, settings=settings)

    async def _session_parking_consumer() -> None:
        """
        RET-11 — espelha o PARQUE de uma sessão suspensa para o Postgres.

        Consome `conversations.events` e mantém `parking.session_parks`:
          · `session_suspended` → grava o parque (token, prazo, motivo)
          · `session_resumed`   → resolve

        ⚠️ **Por que um consumidor, e não uma escrita no caminho do suspend.** Quem
        cunha o token é o engine, via callback `persistSuspendWebhook` — que é
        Redis-only por desenho do Arc 19 e vive no `skill-flow-service`. O evento,
        porém, já existe e já carrega tudo: é o `orchestrator-bridge` que publica
        `session_suspended` aqui, e é dele que a analytics deriva `status='suspended'`.
        Escutar o evento cobre os DOIS mecanismos de parque (`suspend` e `collect`)
        sem tocar em produtor nenhum.

        ⚠️ **`auto_offset_reset='latest'`, como os irmãos.** Não relemos o histórico:
        as 212 sessões já encalhadas não têm o que reidratar (o Redis delas se foi
        inteiro) e reprocessá-las produziria parques que nascem mortos. Fechar
        aquela população é a varredura, que é trabalho à parte e declarado.
        """
        import json as _json
        from aiokafka import AIOKafkaConsumer
        consumer = AIOKafkaConsumer(
            settings.kafka_topic_events,
            bootstrap_servers = settings.kafka_brokers,
            group_id          = f"{settings.kafka_group_id}-parking",
            auto_offset_reset = "latest",
        )
        await consumer.start()
        try:
            async for msg in consumer:
                try:
                    ev = _json.loads(msg.value)
                    await _aplicar_evento_de_parque(ev)
                except Exception as exc:
                    logger.warning("session-parking consumer error: %s", exc)
        finally:
            await consumer.stop()

    async def _aplicar_evento_de_parque(ev: dict) -> None:
        tipo    = ev.get("type") or ev.get("event_type") or ""
        tenant  = ev.get("tenant_id") or ""
        sessao  = ev.get("session_id") or ""
        if not tenant or not sessao:
            return

        if tipo == "session_resumed":
            n = await session_parking.resolver_parque(
                db_pool, tenant, sessao,
                por   = str(ev.get("resume_origin") or "resume"),
                token = str(ev.get("resume_token") or ""),
            )
            if n:
                logger.debug("parque resolvido: session=%s (%d)", sessao, n)
            return

        if tipo != "session_suspended":
            return

        token = str(ev.get("resume_token") or "")
        gravou = await session_parking.registrar_parque(
            db_pool, tenant, sessao,
            token      = token,
            step_id    = str(ev.get("step_id") or ""),
            reason     = str(ev.get("suspend_reason") or ""),
            expires_at = session_parking._quando(ev, "resume_expires_at", "expires_at"),
            parked_at  = session_parking._quando(ev, "timestamp", "suspended_at")
                         or datetime.now(timezone.utc),
        )
        if gravou and not token:
            # RET-12: parque SEM endereço de volta. A linha existe para ser contada;
            # o aviso existe para que a contagem seja procurada.
            logger.warning(
                "parque SEM endereco de volta: session=%s tenant=%s — o evento nao "
                "trouxe `resume_token` (mecanismo `collect`?). Nada podera retoma-lo; "
                "ver RET-12.", sessao, tenant,
            )

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
                    elif namespace == "webrtc" and _webrtc_adapter is not None:
                        # VOZ-21: segmentação da fala do tenant; sem invalidar, a mudança na tela
                        # só valeria no próximo boot
                        _webrtc_adapter.speech_config.invalidate(event.get("tenant_id"))  # loga
                    elif namespace == "speech_profiles" and _webrtc_adapter is not None:
                        # VOZ-25: perfis de fala por ponto de entrada; valem na PRÓXIMA chamada
                        _webrtc_adapter.speech_profiles.invalidate(event.get("tenant_id"))  # loga
                    elif namespace == "identity" and _webhook_adapter is not None:
                        # IDN-14: país padrão do telefone. Sem invalidar, trocar o país na
                        # tela só valeria no próximo boot — e até lá o hash usaria o antigo.
                        _webhook_adapter.phone_region.invalidate(event.get("tenant_id"))
                        logger.info("config.changed: identity region cache invalidated (tenant=%s key=%s)",
                                    event.get("tenant_id"), event.get("key"))
                    elif namespace == "survey" and _survey_web is not None:
                        # link_delivery config changed → drop the cached provider config.
                        _survey_web.invalidate_delivery_config()
                        logger.info("config.changed: survey link_delivery cache invalidated (key=%s)", event.get("key"))
                except Exception as exc:
                    logger.warning("config.changed consumer error: %s", exc)
        finally:
            await consumer.stop()

    pubsub_task     = supervisionar("registry-pubsub", asyncio.create_task(_registry.start_pubsub_listener()))
    outbound_task   = supervisionar("outbound",        asyncio.create_task(outbound.run()))
    collect_task    = supervisionar("collect-events",  asyncio.create_task(_collect_events_consumer()))
    parking_task    = supervisionar("session-parking", asyncio.create_task(_session_parking_consumer()))
    config_task     = supervisionar("config-changed",  asyncio.create_task(_config_changed_consumer()))
    # Invalidação do cache de endereço por `registry.changed`. Sem isto, revogar ou
    # rotacionar um token de endpoint só passa a valer depois do TTL do cache
    # (`endpoint_cache_ttl_s`) — o gateway seguiria aceitando a credencial revogada.
    invalidation_task = supervisionar(
        "registry-invalidation",
        asyncio.create_task(RegistryInvalidationConsumer(settings).run()),
    )
    # Arc 19 Fase D: expira suspends/delegates webhook vencidos (resume_tokens)
    #
    # ⚠️ RET-13: era a ÚNICA das sete que não passava pelo supervisor, e a exceção
    # à regra era justamente a task cujo silêncio custa mais caro — sem ela, nenhum
    # parque vencido é encerrado e a sessão fica suspensa para sempre (a população
    # que a RET-14 teve de varrer à mão).
    #
    # ⚠️ Medido antes de embrulhar, para não vender conserto que não conserta: o laço
    # de `run_timeout_scanner` tem `except Exception` POR ITERAÇÃO e re-levanta
    # `CancelledError`, então ele é praticamente imortal — o alarme aqui não muda o
    # comportamento de hoje. Ele fecha a CLASSE: a próxima task de boot nasce
    # supervisionada porque `probe_background_task_supervision.sh` a cobra, e não
    # porque alguém lembrou.
    timeout_scan_task = supervisionar(
        "webhook-timeout-scanner",
        asyncio.create_task(_webhook_adapter.run_timeout_scanner()),
    )
    # VOZ-07: o expurgo de anexos em dois estágios. Até 2026-09-13 ele estava
    # descrito em três documentos e em nenhuma task — `expires_at` era carimbado e
    # nunca aplicado.
    attachment_expiry_task = supervisionar(
        "attachment-expiry",
        asyncio.create_task(run_attachment_expiry(_attachment_store)),
    )
    # VOZ-41: todo número `voice` cadastrado tem tronco SIP no SFU. O Redis do SFU não persiste, e
    # sem tronco o serviço SIP descarta a chamada sem 4xx e sem linha aqui — esta task é quem diz.
    sip_trunk_watch_task = supervisionar(
        "sip-trunk-watch",
        asyncio.create_task(run_sip_trunk_watch(
            lambda: _webrtc_adapter.provider if _webrtc_adapter is not None else None,
            settings.agent_registry_url, settings.tenant_id, settings.agent_registry_service_token,
        )),
    )

    logger.info("✅ Channel Gateway started (instance=%s)", instance_id)
    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    pubsub_task.cancel()
    outbound_task.cancel()
    collect_task.cancel()
    parking_task.cancel()
    config_task.cancel()
    invalidation_task.cancel()
    timeout_scan_task.cancel()
    attachment_expiry_task.cancel()
    sip_trunk_watch_task.cancel()
    await _producer.stop()
    await db_pool.close()
    await _redis.aclose()
    logger.info("Channel Gateway stopped")


app = FastAPI(title="PlugHub Channel Gateway", lifespan=lifespan)

# ── Import and mount upload routes ────────────────────────────────────────────
# Deferred import so the router can reference module-level state set in lifespan.
from .upload_router import router as upload_router  # noqa: E402  (post-app creation import)
from .recording_router import router as recording_router  # noqa: E402 — VOZ-36
from plughub_tasks import disparar, supervisionar
app.include_router(upload_router)
app.include_router(recording_router)


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

    veredito = _whatsapp_adapter.signature_verdict(body, signature)
    if veredito == "invalid":
        logger.warning("whatsapp inbound rejected — invalid HMAC signature")
        raise HTTPException(status_code=400, detail="Invalid signature")

    # PID-09 — só a assinatura CONFERIDA faz da chegada evidência de posse.
    await _whatsapp_adapter.handle_inbound(body, authenticated=(veredito == "authenticated"))
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


# Capacidade exigida por papel na sala. O agente PUBLICA (fala, vê) — é atender; o
# supervisor entra OCULTO e só assina — é observar. Papel fora da tabela é recusado:
# um default aqui seria o chamador escolhendo o próprio grant.
_WEBRTC_TOKEN_ROLE_GRANT: dict[str, tuple[str, str, str]] = {
    "agent":      ("agent_assist", "atender",   "read_write"),
    "supervisor": ("contacts",     "monitorar", "read_only"),
}


@app.get("/webrtc/token/{session_id}")
async def webrtc_token(
    session_id: str,
    request:    Request,
    role:       str = "agent",
) -> dict:
    """
    Issue a LiveKit token for an agent or supervisor joining an active WebRTC session.

    ⚠️ VOZ-01 (2026-09-14): esta rota emitia token para QUALQUER chamador — sem
    credencial, com o papel e a IDENTIDADE escolhidos na query (`?role=supervisor&
    identity=…` rendia um participante OCULTO com o nome que se quisesse). Enquanto o
    provider devolvia placebo isso era inerte; o provisionamento do SFU transformaria
    o placebo em chave real, numa rota sob `/webrtc`, que é prefixo PUBLICÁVEL da borda.
    Por isso o portão entrou junto com a credencial, e não depois.

      * Bearer obrigatório (`plughub_authz`) → 401;
      * a sessão tem de existir e ser do tenant do token → 404 igual ao inexistente;
      * `role` ∈ {agent, supervisor} → 422; capacidade por papel RECORTADA ao pool
        da sessão (`_WEBRTC_TOKEN_ROLE_GRANT`) → 403;
      * a identidade na sala é `{role}-{sub}` do JWT — nunca da query.

    ⚠️ O pool é o de `session:{id}:meta`, que é o de ENTRADA (dívida conhecida,
    fatia C de `session-meta-ownership`), igual ao precedente de `operator/register`.

    Returns {token, livekit_url, room_name, publish, customer_publish, hidden, policy_sources}.
    404 se a sala ainda não existe · 503 se o plano de mídia não está configurado,
    NOMEANDO o que falta.
    """
    if _webrtc_adapter is None:
        raise HTTPException(status_code=503, detail="WebRTC adapter not initialised")
    if _webrtc_adapter.provider_unavailable is not None:
        raise HTTPException(status_code=503, detail=str(_webrtc_adapter.provider_unavailable))

    _tok = bearer_from_header(request.headers.get("authorization"))
    _payload = verify_user_jwt(_tok, get_settings().auth_jwt_secret) if _tok else None
    if not _payload or not str(_payload.get("sub") or ""):
        # Sem `sub` não há de quem ser a identidade na sala — é credencial inválida.
        raise HTTPException(status_code=401, detail="token de midia exige credencial")
    grant = _WEBRTC_TOKEN_ROLE_GRANT.get(role)
    if grant is None:
        raise HTTPException(
            status_code=422,
            detail=f"role desconhecido: {role!r} — esperado um de {sorted(_WEBRTC_TOKEN_ROLE_GRANT)}",
        )
    tenant = str(_payload.get("tenant_id") or "")
    raw = await _webrtc_adapter._redis.get(f"session:{session_id}:meta")
    try:
        meta = json.loads(raw) if raw else None
    except Exception:
        meta = None
    if not meta or str(meta.get("tenant_id") or "") != tenant:
        raise HTTPException(status_code=404, detail="sessao nao encontrada para este tenant")
    pool = str(meta.get("pool_id") or "")
    module, field, min_access = grant
    if not pool or not abac_can(_payload, module, field, min_access, scope_id=pool):
        logger.warning("webrtc token NEGADO: sub=%s role=%s pool=%s — sem %s.%s",
                       _payload.get("sub"), role, pool or "-", module, field)
        raise HTTPException(
            status_code=403,
            detail=f"token de midia como {role} exige `{module}.{field}` no pool da sessao",
        )

    # ── VOZ-15: capacidade no pool NÃO é "sou eu quem atende este contato" ────
    #
    # O portão acima pergunta *"você pode atender contatos deste pool?"* e para aí. Medido em
    # 2026-09-15 (`probe_webrtc_agent_console.sh`, linha INFO): com o agente atribuído e na sala,
    # OUTRO usuário com o mesmo grant, que não atende nada, recebia **200** — um token de agente
    # para a chamada de um cliente alheio, com áudio e vídeo ao vivo. A exposição era todo
    # portador do grant no pool; o dano pedia o `session_id` em mãos, e é por isso que ficou
    # tanto tempo invisível.
    #
    # O discriminador já existia: os ATENDENTES do estado de mídia (`channel:webrtc:{sid}:media`,
    # VOZ-10), escritos do `routing.assigned` e apagados no `participant_left`. A instância humana
    # é `human-{sub}` (mesmo `sub` que vira a identidade na sala), então a pergunta é direta.
    #
    # Vale só para `agent`. **Supervisor fica fora por definição**: `contacts.monitorar` assina
    # oculto justamente sem atender — exigir presença dele na lista de atendentes seria proibir a
    # supervisão, que é a função.
    #
    # Falha FECHADA, como no roster de `@mention`: sem leitura positiva de que sou atendente, não
    # há token. Estado ausente ou sem atendentes é a corrida normal do `routing.assigned` (o
    # Console pede o token ao receber a atribuição, e o bridge escreve na MESMA ativação), então
    # devolve o mesmo `room_not_ready` que o cliente já sabe repetir — nunca um 403 que o faria
    # desistir de uma chamada que ia funcionar meio segundo depois.
    if role == "agent":
        atendentes = await _webrtc_adapter.attendant_ids(session_id)
        minha = f"human-{_payload.get('sub')}"
        if not atendentes:
            logger.info("webrtc token: sessao %s ainda sem atendentes no estado de midia — "
                        "room_not_ready para %s", session_id, minha)
            raise HTTPException(
                status_code=404,
                detail={"code": "room_not_ready",
                        "message": "assignment not yet visible in the media state"},
            )
        if minha not in atendentes:
            logger.warning("webrtc token NEGADO: %s nao atende a sessao %s (atendentes: %s)",
                           minha, session_id, ",".join(sorted(atendentes)) or "-")
            raise HTTPException(
                status_code=403,
                detail="token de midia como agente exige ATENDER este contato, nao so o pool dele",
            )

    try:
        result = await _webrtc_adapter.get_token(
            session_id = session_id,
            role       = role,
            identity   = str(_payload.get("sub") or ""),
        )
    except MaskedCollectInProgress:
        # NIV-07: o cliente está teclando um dado protegido no telefone, e a tecla SIP chega a
        # todos na sala — ninguém além dele entra até o bloco acabar. "Ainda não", como o 404
        # abaixo: o Console repete, e mostra por quê.
        raise HTTPException(
            status_code=409,
            detail={
                "code":    "masked_collect_in_progress",
                "message": "coleta de dado protegido em curso — o audio volta ao fim dela",
            },
        )
    if result is None:
        # VOZ-04: o Console abre o contato quando recebe a atribuição, e a sala nasce do
        # `routing.assigned` que o bridge escreve na MESMA ativação — a corrida é normal. O
        # cliente precisa distinguir "ainda não" (repete) de "sessão desconhecida" (desiste),
        # e os dois são 404: por isso um `code`, nunca o texto.
        raise HTTPException(
            status_code=404,
            detail={
                "code":    "room_not_ready",
                "message": "WebRTC room not ready for session — routing may still be in progress",
            },
        )
    return result


# ── Eventos do SFU (VOZ-02) ───────────────────────────────────────────────────

@app.post("/v1/livekit/webhook")
async def livekit_webhook(request: Request) -> JSONResponse:
    """Eventos da sala, vindos do SFU. É por aqui que uma chamada de TELEFONE vira contato: o serviço
    SIP põe o chamador numa sala antes de existir sessão, e o gateway só sabe dela pelo
    `participant_joined` (e só desliga quando o `participant_left` chega). O `room_started` alimenta
    o controle compensatório do `auto_create` (`WebRTCAdapter.police_room`).

    A credencial é a ASSINATURA do SFU (JWT com a chave de API, e o hash do corpo dentro dele) —
    sem ela, 401: um evento forjado abriria contato em nome de um chamador que não existe. A rota
    mora em `/v1` (interno); o SFU chega pela rede do compose.

    Responde já e processa numa task: o SFU reenvia evento sem resposta rápida, e a abertura do
    contato (registry, Redis, Kafka) não cabe no prazo dele."""
    s = get_settings()
    if not s.webrtc_livekit_api_key or not s.webrtc_livekit_api_secret:
        raise HTTPException(status_code=503, detail="webhook do SFU sem chave para conferir — faltam "
                                                    "PLUGHUB_WEBRTC_LIVEKIT_API_KEY/_SECRET")
    corpo = (await request.body()).decode(errors="replace")
    try:
        from livekit import api as _lk
        ev = _lk.WebhookReceiver(_lk.TokenVerifier(s.webrtc_livekit_api_key, s.webrtc_livekit_api_secret))             .receive(corpo, request.headers.get("authorization") or "")
    except Exception as exc:
        logger.warning("livekit webhook RECUSADO — assinatura ou corpo invalido: %s", exc)
        raise HTTPException(status_code=401, detail="webhook do SFU exige assinatura valida")
    if _webrtc_adapter is None:
        logger.info("livekit webhook %s ignorado — canal webrtc desligado neste gateway", ev.event)
        return JSONResponse({"ignored": "webrtc_disabled"})
    sala = ev.room.name if ev.HasField("room") else ""
    participante = None
    if ev.HasField("participant"):
        from livekit.protocol import models as _lkm
        p = ev.participant
        participante = {"identity": p.identity, "kind": _lkm.ParticipantInfo.Kind.Name(p.kind),
                        "attributes": dict(p.attributes)}
    disparar(_webrtc_adapter.on_livekit_event(ev.event, sala, participante), nome=f"livekit-{ev.event}")
    return JSONResponse({"ok": True})


# ── Verificação ativa da fala: a porta da TELA (VOZ-27) ──────────────────────

class SpeechCheckRunRequest(BaseModel):
    """Só o perfil. Quem pede sai do TOKEN, nunca do corpo — ver abaixo."""
    speech_profile_id: str | None = None


@app.post("/v1/speech-checks", status_code=202)
async def speech_check_run(request: Request, body: SpeechCheckRunRequest) -> JSONResponse:
    """Pede uma verificação ativa do caminho de fala em nome de uma PESSOA (VOZ-27).

    O executor (`speech-check`) é interno e sua credencial é um token de SERVIÇO: quem o chama são
    o mcp-server (tool `speech_check_run`) e a Agenda, nenhum dos dois com gente do outro lado. A
    tela precisava de um caminho que o browser alcance e que saiba QUEM clicou — e as duas portas
    que já existiam não serviam: o token de serviço não pode viajar ao browser, e a porta do pool
    webhook é anônima por construção (ADR §7.6.1), o que faria o botão disparar chamada sem portão.

    Esta rota é o intermediário com portão, no molde do `/webrtc/token/{sid}`:
      * Bearer obrigatório (`plughub_authz`) → 401;
      * capacidade `config.channels` em ESCRITA → 403, o mesmo portão da tela que cadastra perfil
        (marcar base e pedir medição são atos de quem configura o canal, não de quem só olha);
      * o tenant é o do TOKEN, nunca do corpo;
      * `requested_by` é o `sub` do token, nunca do corpo — ele vai para o ClickHouse e é o que
        responde "quem mandou medir": campo de autoria que o chamador preenche não é autoria.

    Repassa o veredicto do serviço SEM traduzir: 202 aceito · 409 `check_running` · 422
    `profile_not_found` (perfil inexistente ou malformado). Sem `PLUGHUB_SPEECH_CHECK_URL` ou sem
    token de serviço, RECUSA 503 nomeando a env — nunca finge que pediu.
    """
    # VOZ-17: o mesmo portão da escrita de perfil e do catálogo — uma casa, não três cópias.
    _payload = _quem_configura_canal(request, "read_write", "pedir verificacao de fala")

    s = get_settings()
    if not s.speech_check_url or not s.speech_check_service_token:
        raise HTTPException(
            status_code=503,
            detail="verificacao de fala nao configurada neste gateway — "
                   "faltam PLUGHUB_SPEECH_CHECK_URL e/ou PLUGHUB_SPEECH_CHECK_SERVICE_TOKEN",
        )

    corpo = {
        "tenant_id":         str(_payload.get("tenant_id") or ""),
        "speech_profile_id": body.speech_profile_id or None,
        "requested_by":      f"user:{_payload.get('sub')}",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as cli:
            r = await cli.post(
                f"{s.speech_check_url.rstrip('/')}/v1/speech-checks",
                json=corpo,
                headers={"x-service-token": s.speech_check_service_token},
            )
    except Exception as exc:
        logger.warning("speech-check inalcancavel: %s", exc)
        raise HTTPException(status_code=502, detail=f"executor da verificacao inalcancavel: {exc}")

    try:
        dados = r.json()
    except Exception:
        dados = {"detail": r.text[:400]}
    if r.status_code >= 400:
        # O motivo do serviço chega inteiro à tela: "ocupado" e "perfil não existe" pedem
        # reações diferentes de quem clicou, e um 500 genérico apagaria a diferença.
        raise HTTPException(status_code=r.status_code, detail=dados.get("detail", dados))
    return JSONResponse(dados, status_code=r.status_code)


# ── Modelo, língua e voz da fala: catálogo e porta de escrita (VOZ-17) ───────
#
# O estado que originou: perfil e default eram gravados DIRETO no config-api pela tela, e a única
# conferência era o PADRÃO do nome (`speech_config.VOICE_PARAMS`), que não diz nada sobre existir.
# Modelo plausível e não instalado era aceito na gravação e falhava na CHAMADA — 404 do serviço a
# cada frase, `fala PERDIDA` no log, e nada vermelho. Aqui a config passa a ser conferida contra o
# que o serviço TEM (`speech_catalog`), no momento em que alguém a grava.
#
# Por que a porta é o GATEWAY e não o config-api: o config-api é store genérico de namespace, e
# ensiná-lo a falar com o serviço de fala de um canal acoplaria o store a uma topologia que não é
# dele. Quem sabe o que a chamada vai usar — as três camadas, o serviço, a tarefa de cada modelo —
# é o dono do canal. A credencial não muda de mão: o Bearer de QUEM PEDIU é repassado ao config-api,
# que aplica o mesmo `config.channels` de sempre — o gateway confere, não empresta poder.
#
# ⚠️ O config-api continua aceitando escrita direta de quem tem o grant (é a porta do resto da
# config, e os probes a usam de propósito para plantar perfil inválido). Esta rota fecha o caminho
# da TELA, não o da chave-mestra; a diferença está nomeada em `VOZ-29`.


def _quem_configura_canal(request: Request, minimo: str, what: str) -> dict:
    """Bearer + `config.channels` na capacidade pedida, ou levanta 401/403.

    Uma casa só: pedir verificação de fala (VOZ-27), ler o catálogo e gravar perfil/default são o
    MESMO portão — e duas cópias da mesma decisão é como elas passam a discordar (o caso medido
    está no `CLAUDE.md` § MCP Interception: a mesma lista com três semânticas)."""
    tok = bearer_from_header(request.headers.get("authorization"))
    payload = verify_user_jwt(tok, get_settings().auth_jwt_secret) if tok else None
    if not payload or not str(payload.get("sub") or ""):
        raise HTTPException(status_code=401, detail=f"{what} exige credencial")
    rotulo = {"read_write": "escrita", "read_only": "leitura"}.get(minimo, minimo)
    if not abac_can(payload, "config", "channels", minimo):
        logger.warning("%s NEGADO: sub=%s — sem config.channels em %s",
                       what, payload.get("sub"), rotulo)
        raise HTTPException(status_code=403, detail=f"{what} exige `config.channels` em {rotulo}")
    return payload


def _canal_de_fala() -> WebRTCAdapter:
    if _webrtc_adapter is None:
        raise HTTPException(status_code=503, detail="canal webrtc nao habilitado neste gateway")
    return _webrtc_adapter


async def _voz_efetiva(tenant_id: str, *, tenant: dict | None = None,
                       perfil: dict | None = None) -> dict:
    """A voz que a chamada REALMENTE usaria: env ⊕ tenant ⊕ perfil.

    `tenant=None` usa o que está gravado; quem escreve o DEFAULT passa a camada já com a mudança
    aplicada. Conferir só o corpo enviado deixaria passar o par (modelo de uma camada, voz de
    outra) — que é exatamente o que quebra na chamada."""
    ad = _canal_de_fala()
    if tenant is None:
        tenant, _ = await ad.speech_config.voice(tenant_id)
    efetivo = {**ad.voice_defaults(), **tenant}
    for k in VOICE_PARAMS:
        v = (perfil or {}).get(k)
        if isinstance(v, str) and v.strip():
            efetivo[k] = v.strip()
    return efetivo


async def _catalogo_ou_503() -> list:
    """Os modelos instalados, ou 503 nomeando a causa. **Serviço fora não vira "grava assim
    mesmo"** (decisão do dono, 2026-09-17): um perfil gravado sem conferência é indistinguível dos
    conferidos na leitura seguinte, e é a leitura que não tem como saber. Com o serviço fora não há
    fala acontecendo — a janela em que a recusa incomoda é a janela em que o canal já está parado."""
    try:
        return await speech_catalog.catalogo(get_settings().webrtc_speaches_url)
    except speech_catalog.CatalogoIndisponivel as exc:
        raise HTTPException(status_code=503, detail=f"config de fala NAO conferida e NAO gravada: {exc}")


def _recusa_422(recusas: list) -> None:
    if recusas:
        raise HTTPException(status_code=422, detail={"code": "speech_config_rejected", "reasons": recusas})


async def _config_api(metodo: str, caminho: str, request: Request, tenant_id: str,
                      **kw) -> httpx.Response:
    """Fala com o config-api COM A CREDENCIAL DE QUEM PEDIU — o gateway confere, não empresta
    poder: quem não passa no `config.channels` do config-api continua sem gravar."""
    url = f"{get_settings().config_api_url.rstrip('/')}{caminho}"
    cabecalhos = {"authorization": request.headers.get("authorization") or "",
                  "x-tenant-id": tenant_id}
    try:
        async with httpx.AsyncClient(timeout=10.0) as cli:
            return await cli.request(metodo, url, headers=cabecalhos, **kw)
    except Exception as exc:
        logger.warning("config-api inalcancavel em %s %s: %s", metodo, caminho, exc)
        raise HTTPException(status_code=502, detail=f"config-api inalcancavel: {exc}")


def _relata(r: httpx.Response, o_que: str) -> None:
    if r.status_code >= 400:
        try:
            det = r.json().get("detail")
        except Exception:  # noqa: BLE001
            det = r.text[:300]
        raise HTTPException(status_code=r.status_code, detail={"code": "config_api_refused",
                                                               "what": o_que, "detail": det})


@app.get("/v1/speech-models")
async def speech_models(request: Request) -> dict:
    """O que o serviço de fala TEM instalado, por tarefa, com as vozes de cada modelo de TTS.

    É o que a tela oferece: campo de texto livre é como um modelo inexistente entra. **Instalados,
    nunca o registro de downloads** — modelo baixável e não baixado falha igual na chamada (o
    porquê está em `speech_catalog`)."""
    _quem_configura_canal(request, "read_only", "ver os modelos de fala")
    modelos = await _catalogo_ou_503()
    return {
        "stt": [{"id": m["id"], "languages": m.get("language") or []}
                for m in speech_catalog.por_tarefa(modelos, speech_catalog.STT)],
        "tts": [{"id": m["id"], "voices": m.get("voices") or []}
                for m in speech_catalog.por_tarefa(modelos, speech_catalog.TTS)],
    }


@app.put("/v1/speech-profiles/{profile_id}")
async def speech_profile_put(profile_id: str, request: Request, body: dict) -> JSONResponse:
    """Grava um perfil de fala depois de conferir FORMA e SERVIÇO (VOZ-17).

    Três recusas, todas com o motivo nomeado: id fora do padrão · campo desconhecido ou fora da
    faixa (`speech_config.conferir_forma`, a mesma casa que aplica na leitura) · modelo/voz/língua
    que o serviço não serve (`speech_catalog.conferir`, sobre a resolução COMPLETA).

    O corpo é o perfil inteiro, como ele fica no namespace — gravação é substituição, igual ao
    `PUT` do config-api que ela embrulha, e um merge parcial aqui faria a tela e a chave-mestra
    escreverem coisas diferentes na mesma chave."""
    quem = _quem_configura_canal(request, "read_write", "gravar perfil de fala")
    tenant_id = str(quem.get("tenant_id") or "")
    if not PROFILE_ID_RE.fullmatch(profile_id):
        _recusa_422([f"`{profile_id}` nao e um id de perfil — esperado {PROFILE_ID_RE.pattern}"])
    if not isinstance(body, dict):
        _recusa_422(["o corpo tem de ser o objeto do perfil"])
    _recusa_422(conferir_forma(body))

    modelos = await _catalogo_ou_503()
    efetivo = await _voz_efetiva(tenant_id, perfil=body)
    _recusa_422(speech_catalog.conferir(modelos, efetivo))

    r = await _config_api("PUT", f"/config/speech_profiles/{profile_id}", request, tenant_id,
                          json={"value": body, "tenant_id": tenant_id})
    _relata(r, f"gravar speech_profiles.{profile_id}")
    logger.info("speech: perfil %r gravado por sub=%s (tenant=%s) — a chamada com ele usa %s",
                profile_id, quem.get("sub"), tenant_id,
                " ".join(f"{k}={efetivo.get(k) or '-'}" for k in sorted(VOICE_PARAMS)))
    return JSONResponse({"profile_id": profile_id, "effective_voice": efetivo}, status_code=200)


@app.delete("/v1/speech-profiles/{profile_id}", status_code=204)
async def speech_profile_delete(profile_id: str, request: Request):
    """Apaga o perfil. Passa por aqui para a tela ter UMA porta de perfil — e porque quem apaga um
    perfil referenciado por um endpoint precisa que isso apareça: a chamada por aquele endpoint
    volta à config do tenant e o gateway DIZ no log (`perfil referenciado ... nao existe`). Não há
    conferência de serviço: apagar nunca cria config que a chamada não sustente."""
    quem = _quem_configura_canal(request, "read_write", "apagar perfil de fala")
    tenant_id = str(quem.get("tenant_id") or "")
    if not PROFILE_ID_RE.fullmatch(profile_id):
        _recusa_422([f"`{profile_id}` nao e um id de perfil — esperado {PROFILE_ID_RE.pattern}"])
    r = await _config_api("DELETE", f"/config/speech_profiles/{profile_id}", request, tenant_id,
                          params={"tenant_id": tenant_id})
    if r.status_code not in (200, 204, 404):
        _relata(r, f"apagar speech_profiles.{profile_id}")
    logger.info("speech: perfil %r apagado por sub=%s (tenant=%s)", profile_id, quem.get("sub"), tenant_id)
    return JSONResponse(None, status_code=204)


@app.get("/v1/speech-defaults")
async def speech_defaults_get(request: Request) -> dict:
    """O que vale HOJE quando a chamada não aponta perfil, e de que camada veio cada campo.

    A tela precisa das três respostas ao mesmo tempo: o que o env do gateway oferece, o que o tenant
    escolheu e o que resulta. Mostrar só o resultado esconderia a pergunta que a VOZ-17 abriu — *este
    modelo veio de onde?* —, e é ela que decide se o botão "voltar ao padrão" muda alguma coisa."""
    quem = _quem_configura_canal(request, "read_only", "ver o default de fala")
    ad = _canal_de_fala()
    tenant_id = str(quem.get("tenant_id") or "")
    tenant, proc = await ad.speech_config.voice(tenant_id)
    env = ad.voice_defaults()
    return {"env": env, "tenant": tenant, "effective": {**env, **tenant},
            "provenance": {k: proc.get(k, "tenant") if k in tenant else "env" for k in VOICE_PARAMS}}


class SpeechDefaultsBody(BaseModel):
    """O default de fala do TENANT (namespace `webrtc`), campo a campo.

    Campo AUSENTE não é mexido; campo enviado vazio ou nulo REMOVE o override e devolve aquele
    campo ao env do gateway. O discriminador é `model_fields_set`, nunca o valor — "não mandou" e
    "mandou vazio" pedem coisas opostas, e lê-los pelo valor colapsaria as duas."""
    stt_model:    str | None = None
    stt_language: str | None = None
    tts_model:    str | None = None
    tts_voice:    str | None = None


@app.put("/v1/speech-defaults")
async def speech_defaults_put(request: Request, body: SpeechDefaultsBody) -> JSONResponse:
    """O que vale quando a chamada NÃO aponta perfil (VOZ-17).

    Até aqui isso era env do gateway — `PLUGHUB_WEBRTC_STT_MODEL` e irmãs —, contra a regra da casa
    (*env só para segredo e topologia; todo campo de config tem tela*). Passa a viver no namespace
    `webrtc`, nas MESMAS chaves do perfil, conferido contra o serviço igual a ele. O env não some:
    é a última camada, a que faz a imagem subir falando sem config-api."""
    quem = _quem_configura_canal(request, "read_write", "gravar o default de fala")
    tenant_id = str(quem.get("tenant_id") or "")
    ad = _canal_de_fala()
    enviados = {k: getattr(body, k) for k in body.model_fields_set}
    if not enviados:
        _recusa_422(["nenhum campo enviado — mande ao menos um de "
                     + ", ".join(sorted(SpeechDefaultsBody.model_fields))])

    atual, _ = await ad.speech_config.voice(tenant_id)
    nova = dict(atual)
    remover = []
    for k, v in enviados.items():
        if v is None or not str(v).strip():
            nova.pop(k, None)
            remover.append(k)
        else:
            nova[k] = str(v).strip()
    _recusa_422(conferir_forma(nova))

    modelos = await _catalogo_ou_503()
    efetivo = await _voz_efetiva(tenant_id, tenant=nova)
    _recusa_422(speech_catalog.conferir(modelos, efetivo))

    escritos = []
    for k in enviados:
        if k in remover:
            r = await _config_api("DELETE", f"/config/webrtc/{k}", request, tenant_id,
                                  params={"tenant_id": tenant_id})
            if r.status_code not in (200, 204, 404):
                _relata(r, f"remover webrtc.{k} (ja aplicados: {escritos or 'nenhum'})")
        else:
            r = await _config_api("PUT", f"/config/webrtc/{k}", request, tenant_id,
                                  json={"value": nova[k], "tenant_id": tenant_id})
            _relata(r, f"gravar webrtc.{k} (ja aplicados: {escritos or 'nenhum'})")
        escritos.append(k)
    logger.info("speech: default de fala do tenant=%s alterado por sub=%s (%s) — a chamada sem "
                "perfil passa a usar %s", tenant_id, quem.get("sub"),
                ", ".join(f"{k}={'(removido)' if k in remover else nova[k]}" for k in escritos),
                " ".join(f"{k}={efetivo.get(k) or '-'}" for k in sorted(VOICE_PARAMS)))
    return JSONResponse({"applied": escritos, "removed": remover, "effective_voice": efetivo},
                        status_code=200)


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
    # PID-06 — exigência de retomada (mecanismos), já resolvida pelo engine.
    resume_requires:    list[str] | None = None

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
    # PID-06 — exigência de retomada (mecanismos), já resolvida pelo engine.
    resume_requires:    list[str] | None = None
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
    tenant_id:   str
    # PID-10: a âncora tem de ser autoritativa PARA este cliente. Ausente → recusa
    # explícita (`customer_required`), nunca 422 — o chamador lê o motivo.
    customer_id: str = ""
    kind:        str   # só phone | email admitem desafio (D8); os demais recusam
    value:       str

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

class IdentityImportRow(BaseModel):
    external_id: str
    anchors:     list[IdentityAnchor]
    attributes:  dict = {}

class IdentityImportRequest(BaseModel):
    # SEM `tenant_id`, e isso é o ponto: o tenant da importação vem do JWT de quem
    # importa. No corpo, seria o chamador escolhendo em qual base carimbar
    # `authoritative` — o defeito que a IDN-06 mede nas rotas irmãs.
    system:    str
    customers: list[IdentityImportRow]

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
        resume_requires    = body.resume_requires,
        assigned_to              = body.assigned_to or "",
        fallback_to_pool_after_s = body.fallback_to_pool_after_s,
        auto_attend              = body.auto_attend,
    )
    return {"session_id": session_id}


def _resume_requires_of(body: dict) -> list[str] | None:
    """PID-06 — o corpo do collect é dict cru; campo malformado RECUSA (422), nunca some.

    Foi exatamente assim que `customer_resumable` passou meses descartado neste endpoint:
    kwargs montados à mão, e o que não é lido não existe. Aqui, ausente é `None` e
    qualquer coisa que não seja lista de strings é erro do chamador.
    """
    if "resume_requires" not in body or body["resume_requires"] is None:
        return None
    v = body["resume_requires"]
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise HTTPException(status_code=422, detail="resume_requires deve ser lista de mecanismos")
    return list(v)


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
        resume_requires    = body.resume_requires,
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
            # Identity Resolver (nível b · Slice 3) — gate da dual-write
            # `pending_by_customer`, simétrico aos dois handlers de delegate.
            # O engine SEMPRE enviou estes campos (skill-flow-service persistCollect);
            # eram descartados aqui, porque este endpoint monta os kwargs à mão a
            # partir de um dict cru — o campo não declarado some sem erro nenhum.
            customer_resumable   = bool(body.get("customer_resumable") or False),
            resume_policy        = body.get("resume_policy") or "offer",
            resume_requires      = _resume_requires_of(body),
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
#
# IDN-06 (2026-09-13): TODA rota deste bloco chama `_identity_caller` — o censo AST de
# `probe_identity_route_credential.sh` reprova a que não chamar. Sem grant declarado a
# rota é INTERNA (só serviço); o cadastro lido pela UI declara os campos que o abrem.

_IDENTITY_CADASTRO_READ = (
    ("contacts", "visualizar", "read_only"),      # Análise › Clientes
    ("agent_assist", "atender", "read_write"),    # Console › aba Cliente
)


def _identity_caller(request: Request, requested_tenant: str | None,
                     grants: tuple[tuple[str, str, str], ...] = ()) -> str:
    """Portão das rotas de identidade: devolve o tenant que vale, ou levanta 401/403."""
    s = get_settings()
    principal = identity_principal(request, service_token=s.channel_gateway_service_token,
                                   jwt_secret=s.auth_jwt_secret, user_grants=grants)
    return tenant_for(principal, requested_tenant)


@app.post("/v1/channels/webhook/identity/resolve", status_code=200)
async def webhook_identity_resolve(body: IdentityResolveRequest, request: Request) -> dict:
    """
    Lookup 1 — resolve/provision a native customer_id from identity anchors.
    Returns { customer_id, status, matched_by, confidence }. Interna (IDN-06).
    """
    tenant = _identity_caller(request, body.tenant_id)
    if _webhook_adapter is None:
        return {"customer_id": "", "status": "none", "matched_by": "none", "confidence": 0.0}
    return await _webhook_adapter.resolve_customer(
        tenant_id = tenant,
        anchors   = [a.model_dump() for a in body.anchors],
        provision = body.provision,
    )


@app.get("/v1/channels/webhook/pending/by-customer/{customer_id}", status_code=200)
async def webhook_pending_by_customer(customer_id: str, tenant_id: str, request: Request) -> dict:
    """
    Lookup 2 — pending workflows registered under a resolved customer_id.
    Returns { found, count, pendings[] }. Interna (IDN-06): devolve `resume_token`.
    """
    tenant = _identity_caller(request, tenant_id)
    if _webhook_adapter is None:
        return {"found": False, "count": 0, "pendings": []}
    return await _webhook_adapter.find_pending_by_customer(
        tenant_id   = tenant,
        customer_id = customer_id,
    )


@app.post("/v1/channels/webhook/identity/otp/challenge", status_code=200)
async def webhook_otp_challenge(body: OtpChallengeRequest, request: Request) -> dict:
    """OTP de posse — emite um desafio para âncora entregável e autoritativa (PID-10). Interna."""
    tenant = _identity_caller(request, body.tenant_id)
    if _webhook_adapter is None:
        return {"sent": False, "reason": "adapter_unavailable"}
    return await _webhook_adapter.otp_challenge(tenant, body.customer_id, body.kind, body.value)


@app.post("/v1/channels/webhook/identity/otp/verify", status_code=200)
async def webhook_otp_verify(body: OtpVerifyRequest, request: Request) -> dict:
    """OTP de posse — confere o código; sucesso promove a âncora a possessed. Interna."""
    tenant = _identity_caller(request, body.tenant_id)
    if _webhook_adapter is None:
        return {"verified": False, "reason": "adapter_unavailable"}
    return await _webhook_adapter.otp_verify(
        tenant, body.customer_id, body.kind, body.value, body.code,
    )


@app.post("/v1/channels/webhook/identity/key/attach", status_code=200)
async def webhook_identity_attach_key(body: IdentityAttachKeyRequest, request: Request) -> dict:
    """Enriquecimento — anexa uma âncora como claimed (possessed só via OTP). Interna."""
    tenant = _identity_caller(request, body.tenant_id)
    if _webhook_adapter is None:
        return {"attached": False}
    return await _webhook_adapter.attach_customer_key(
        tenant, body.customer_id, body.kind, body.value,
    )


@app.post("/v1/channels/webhook/identity/attributes", status_code=200)
async def webhook_identity_attributes(body: IdentityAttributesRequest, request: Request) -> dict:
    """Enriquecimento — merge de atributos mascarados/não-sensíveis no cadastro. Interna."""
    tenant = _identity_caller(request, body.tenant_id)
    if _webhook_adapter is None:
        return {"updated": False}
    return await _webhook_adapter.update_customer_attributes(
        tenant, body.customer_id, body.attributes,
    )


IDENTITY_IMPORT_MAX_ROWS = 1000


class OperatorRegisterRequest(BaseModel):
    # IDN-08: SEM `tenant_id` — ele vem do JWT. A sessão dá o POOL do recorte ABAC.
    session_id: str
    anchors:    list[IdentityAnchor]
    name:       str = ""


@app.post("/v1/channels/webhook/identity/operator/register", status_code=200)
async def webhook_identity_operator_register(body: OperatorRegisterRequest, request: Request) -> JSONResponse:
    """
    IDN-08 — o operador cadastra o cliente do contato que atende, com procedência
    `operator`, no cadastro DURÁVEL.

      * Bearer obrigatório (`plughub_authz`); tenant do JWT, nunca do corpo;
      * campo ABAC `agent_assist.atender` (read_write) RECORTADO ao pool da sessão —
        quem cadastra é quem atende aquele contato, e o escopo do grant vale aqui;
      * a sessão tem de existir e ser do tenant do token.

    As rotas irmãs são INTERNAS desde a IDN-06 (credencial de serviço), e mesmo assim o
    carimbo `operator` não passa por elas: o serviço não tem operador para nomear.

    200 `created|existing` · 422 âncora inválida (nomeada) · 409 ambíguo ou âncora
    de outro cliente (nomeada).
    """
    if _webhook_adapter is None:
        raise HTTPException(status_code=503, detail="Identity resolver not available")
    from plughub_authz import abac_can, bearer_from_header, verify_user_jwt

    _tok = bearer_from_header(request.headers.get("authorization"))
    _payload = verify_user_jwt(_tok, get_settings().auth_jwt_secret) if _tok else None
    if not _payload:
        raise HTTPException(status_code=401, detail="cadastro do operador exige credencial")
    tenant = str(_payload.get("tenant_id") or "")
    raw = await _webhook_adapter._redis.get(f"session:{body.session_id}:meta")
    try:
        meta = json.loads(raw) if raw else None
    except Exception:
        meta = None
    if not meta or str(meta.get("tenant_id") or "") != tenant:
        # Sessão de outro tenant responde igual à inexistente: não se confirma o que existe.
        raise HTTPException(status_code=404, detail="sessao nao encontrada para este tenant")
    pool = str(meta.get("pool_id") or "")
    if not pool or not abac_can(_payload, "agent_assist", "atender", "read_write", scope_id=pool):
        logger.warning("identity operator/register NEGADO: sub=%s pool=%s — sem agent_assist.atender",
                       _payload.get("sub"), pool or "-")
        raise HTTPException(status_code=403, detail="cadastro exige `agent_assist.atender` no pool da sessao")

    out = await _webhook_adapter.register_customer_by_operator(
        tenant, [a.model_dump() for a in body.anchors], body.name, str(_payload.get("sub") or ""),
    )
    status = 200
    if out["outcome"] == "refused":
        status = 422 if out["reason"] in ("invalid_anchors", "no_anchors") else 409
    return JSONResponse(status_code=status, content=out)


@app.post("/v1/channels/webhook/identity/import", status_code=200)
async def webhook_identity_import(body: IdentityImportRequest, request: Request) -> dict:
    """
    PID-12 — importação da base do tenant como FONTE AUTORITATIVA.

    Decisão do dono (2026-09-12): *"a importação com credencial de admin é a única
    porta que carimba `authoritative`"*. A confiança passa a ser do PROCESSO de
    importação, e esta rota é onde ele é conferido:

      * Bearer obrigatório, verificado pelo `plughub_authz` (nunca uma cópia);
      * campo ABAC `contacts.importar_cadastro` em `read_write` — papel NÃO é
        portão (§ Arc 7); o preset de `admin` é que concede o campo;
      * o tenant vem do JWT, nunca do corpo.

    As rotas IRMÃS (`/identity/resolve`, `/key/attach`, `/attributes`…) exigem credencial
    de serviço desde a IDN-06 — e ainda assim nenhuma alcança `authoritative`, porque a
    trava mora no índice (`_writer_provenance`) e não na rota.

    Devolve `{created, updated, refused, results[]}`; linha recusada vem NOMEADA.
    """
    if _webhook_adapter is None:
        raise HTTPException(status_code=503, detail="Identity resolver not available")

    from plughub_authz import abac_can, bearer_from_header, verify_user_jwt

    _tok = bearer_from_header(request.headers.get("authorization"))
    _payload = verify_user_jwt(_tok, get_settings().auth_jwt_secret) if _tok else None
    if not _payload:
        raise HTTPException(
            status_code=401,
            detail="importacao exige credencial (Bearer ausente ou invalido)",
        )
    if not abac_can(_payload, "contacts", "importar_cadastro", "read_write"):
        logger.warning(
            "identity import NEGADO: sub=%s — sem contacts.importar_cadastro",
            _payload.get("sub"),
        )
        raise HTTPException(
            status_code=403,
            detail="importacao exige `contacts.importar_cadastro` (read_write)",
        )
    tenant_id = str(_payload.get("tenant_id") or "")
    if not tenant_id:
        raise HTTPException(status_code=403, detail="credencial sem tenant_id")
    if not body.system.strip():
        raise HTTPException(status_code=422, detail="`system` e obrigatorio (origem da base)")
    if len(body.customers) > IDENTITY_IMPORT_MAX_ROWS:
        raise HTTPException(
            status_code=413,
            detail="no maximo %d linhas por chamada; recebidas %d"
                   % (IDENTITY_IMPORT_MAX_ROWS, len(body.customers)),
        )
    return await _webhook_adapter.import_customers(
        tenant_id   = tenant_id,
        system      = body.system.strip(),
        rows        = [c.model_dump() for c in body.customers],
        imported_by = str(_payload.get("sub") or ""),
    )


@app.get("/v1/channels/webhook/identity/customers/search", status_code=200)
async def webhook_identity_customers_search(request: Request, q: str, tenant_id: str = "",
                                            limit: int = 20) -> dict:
    """
    Cadastro manual (C1a — Cliente 360): busca de clientes por `customer_id` exato
    ou nome (`attributes`). NÃO por âncora (telefone/email exata resolve via
    /identity/resolve). Retorna { count, results: [{customer_id, status, attributes}] }.

    IDN-06: serviço, ou usuário com `contacts.visualizar` / `agent_assist.atender`;
    para usuário o tenant é o do JWT (o `tenant_id` da query só pode repeti-lo).
    """
    tenant = _identity_caller(request, tenant_id or None, _IDENTITY_CADASTRO_READ)
    if _webhook_adapter is None:
        return {"count": 0, "results": []}
    return await _webhook_adapter.search_customers(tenant_id=tenant, q=q, limit=limit)


@app.get("/v1/channels/webhook/identity/customers/{customer_id}", status_code=200)
async def webhook_identity_customer_get(customer_id: str, request: Request, tenant_id: str = "") -> dict:
    """
    Read puro de um cliente por id (cadastro §11). Usado pelo outbound (Fase 3b) para
    consultar `attributes.do_not_contact` (opt-out global). 404 quando ausente — o
    chamador trata como "sem opt-out". Declarado APÓS /customers/search (literal vence
    o path-param na resolução do Starlette). IDN-06: mesmo portão da busca.
    """
    tenant = _identity_caller(request, tenant_id or None, _IDENTITY_CADASTRO_READ)
    if _webhook_adapter is None:
        raise HTTPException(status_code=503, detail="Identity resolver not available")
    cust = await _webhook_adapter.get_customer(tenant, customer_id)
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
    # 409 ANTES do 502 genérico, e por motivo de diagnóstico: "o form está arquivado" é
    # recusa deliberada desta borda, não falha do dialog-api. Colapsar as duas em 502 diria
    # ao operador que o store quebrou, quando o store respondeu certo.
    except ArchivedFormError as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                f"dialog form '{exc.form_id}' esta arquivado (deleted_at={exc.deleted_at}) — "
                "nao e possivel criar link de survey novo sobre ele; restaure o form ou "
                "escolha outro"
            ),
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
    # O catálogo viaja no GET, e não congelado no token: ele pode mudar entre a
    # criação do link e a resposta, e a fonte de verdade em runtime é o store.
    formatos = await _survey_web.format_catalog(rec.get("tenant_id") or "")
    return {"form": rec.get("form"), "status": rec.get("status"), "formats": formatos}


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
    *,
    pool_da_tarefa: str | None = None,
    exigir_credencial: bool = False,
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
        # ⚠️ AUT-46 (2026-09-09): o caminho externo/sistema CONTINUA aberto — para a
        # populacao que o usa. O que deixou de valer e ele decidir tarefa que DECLARA
        # capacidade. Medido em 30 dias de `session_stream_events`: **85** decisoes de
        # aprovacao vieram com credencial (`possessed`) e **1** sem — e essa 1 foi a
        # sonda que abriu esta ficha. Do lado de la, **322 de 323** resumes sem
        # credencial NAO decidem nada (form-fill, wrap-up, sistema): e para eles que a
        # porta existe, e e por isso que ela fica.
        #
        # Ramo legado morre CONTADO, nunca por decreto: a populacao foi medida ANTES.
        # 401, nao 403 — "nao sei quem e" e "sei e nao pode" sao dois estados
        # (decisao canonica do `plughub_authz`).
        if exigir_credencial and required_abac is not None:
            _mod, _field = required_abac
            logging.getLogger(__name__).warning(
                "AUT-46 401: resume de tarefa que exige %s.%s chegou SEM credencial "
                "— o caminho anonimo nao decide tarefa escopada", _mod, _field,
            )
            raise HTTPException(
                status_code=401,
                detail=f"resume: credential required for {_mod}.{_field} tasks",
            )
        return None  # external / system path (claimed) — inalterado p/ tarefa sem ABAC

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
    # ABAC por TIPO DE TAREFA (Camada E2).
    #
    # ⚠️ O BYPASS DE PAPEL CAIU em 2026-08-27 (passo 8). Ele era
    # `("admin" in roles) or ("supervisor" in roles)`, justificado por "contas elevadas
    # não carregam module_config por campo" e por "mesma semântica do passesAbac da
    # plataforma". As DUAS premissas morreram no mesmo arco: desde o passo 3 todo usuário
    # nasce com grants por campo, e no passo 5 o `passesAbac` deixou de olhar papel.
    #
    # Quem aprova agora precisa de `approvals.decide` E do pool no domínio. Medido antes
    # de remover, porque remover barra gente:
    #   · admin  — tem o grant, e virou `unrestricted` (decisão do dono: os 22 pools eram
    #     resíduo de teste), então `pool_in_scope` o libera pelo claim;
    #   · supervisor — ⚠️ **esta linha dizia que ele NÃO tem `approvals.decide`**, e era
    #     verdade quando foi escrita (2026-08-27). Caiu em 2026-09-08 (MOD-08/G1b): o
    #     guard de RANK exige `preset(operator) ⊆ preset(supervisor)`, e como o operator
    #     tem o campo, o supervisor passou a tê-lo — capacidade que entra por uma regra
    #     de CONTRATAÇÃO. Medido em 2026-09-09 (AUT-46): 6 portadores, todos
    #     `read_write`. O que ainda o mantém fora do `aprovacao_deploy` é o ESCOPO DE
    #     POOL — e é por isso que ele deixou de ser opcional para o chamador (abaixo).
    roles = payload.get("roles")
    roles = roles if isinstance(roles, list) else []
    if required_abac is not None:
        _mod, _field = required_abac
        # Tarefa que EXIGE capacidade (ex.: aprovação → approvals.decide). Form-fill
        # genérico (required_abac=None, ex.: wrap-up) NÃO cai aqui: o binding do claim
        # autoriza o operador comum (senão o agente de wrap-up tomaria 403 indevido).
        # ⚠️ AUT-40 (2026-09-09): era `"write_only"`, e a troca por `"read_only"`
        # NAO muda comportamento — foi medida, nao presumida. `ACCESS_RANK` COLAPSA
        # os dois em 1 (sao graus laterais, nao degraus), e o catalogo vivo **nao
        # oferece `write_only` em dominio nenhum** (0 linhas em `auth.module_registry`),
        # enquanto `validate_module_config` recusa com 422 o `access` fora do dominio.
        # Logo nenhum grant pode SER `write_only`, e os dois minimos selecionam o
        # mesmo conjunto. A equivalencia e ESTRUTURAL, nao conjuntural.
        #
        # ⚠️ Este era o call site que a ficha mandava medir antes: o par (_mod,_field)
        # e DINAMICO, vindo de `session.resume_abac` declarado pelo autor do workflow.
        # Medido: **0** skills o declaram (registry, repo e sessoes vivas), entao o
        # unico par que chega aqui e o do retrocompat — ("approvals","decide").
        if not abac_can(payload, _mod, _field, "read_only"):
            _log.warning("E2 403 abac: roles=%s sem %s.%s", roles, _mod, _field)
            raise HTTPException(status_code=403, detail=f"resume: missing {_mod}.{_field}")

        # ⚠️ AUT-46 (2026-09-09): o eixo de ESCOPO era `if body.pool_id`, e por isso o
        # chamador o desligava por OMISSAO. Medido ao vivo numa promocao de deploy
        # real: com `pool_id` declarado 403, **sem ele 200** — e a linha durauel
        # gravou `verification_class: possessed`, atribuindo a decisao a um aprovador
        # "verificado" que o proprio sistema recusaria se o corpo tivesse dito a
        # verdade. Agora o pool vem do SERVIDOR (`resume_task_pool`), como o campo ja
        # vinha; o do corpo so e usado quando a origem autoritativa nao responde, e
        # dizendo em voz alta que isso aconteceu.
        alvo_escopo = pool_da_tarefa or body.pool_id
        if pool_da_tarefa is None and exigir_credencial:
            if body.pool_id:
                _log.warning(
                    "AUT-46: pool da tarefa nao derivavel do token (item fora de fila "
                    "e sem claim_record); caindo no pool_id do CORPO (%s) — escopo "
                    "verificado contra valor do chamador", body.pool_id,
                )
            else:
                _log.warning(
                    "AUT-46 403: tarefa exige %s.%s e o pool nao foi derivavel nem "
                    "declarado — recusa por indeterminacao, nunca por omissao aceita",
                    _mod, _field,
                )
                raise HTTPException(
                    status_code=403, detail="resume: task pool undeterminable",
                )
        if alvo_escopo and not pool_in_scope(payload, alvo_escopo):
            _log.warning(
                "E2 403 pool_scope: pool=%s (origem=%s) accessible_pools=%s",
                alvo_escopo, "servidor" if pool_da_tarefa else "corpo",
                accessible_pools(payload),
            )
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


def _resume_identity_clearance(request: Request) -> str | None:
    """PID-13 — o atestado do mcp-server de que JULGOU a evidência da sessão chamadora.

    Só vale com a credencial de SERVIÇO do gateway (a mesma das rotas de identidade,
    IDN-06). O header sem a credencial é ignorado e LOGADO: um chamador anônimo que se
    declara provado é exatamente o que este portão existe para recusar. Sem credencial
    configurada no gateway, nenhum atestado vale (falha fechada).
    """
    declarado = request.headers.get("x-resume-identity-clearance")
    if not declarado:
        return None
    esperado = get_settings().channel_gateway_service_token or ""
    apresentado = request.headers.get("x-service-token") or ""
    if esperado and hmac.compare_digest(apresentado, esperado) and declarado == "session_evidence":
        return "session_evidence"
    logger.warning(
        "PID-13: atestado de identidade '%s' IGNORADO — sem credencial de serviço válida",
        declarado,
    )
    return None


async def _customer_cancel_allowed(
    request:       Request,
    body:          "WebhookResumeRequest",
    resume_token:  str,
    required_abac: tuple[str, str] | None,
    clearance:     str | None,
) -> bool:
    """PID-15 — o cliente que PROVOU identidade pode cancelar uma tarefa de aprovação.

    Medido em 2026-09-14: o intake do limite oferece "Cancelar solicitação" e chama
    `workflow_resume` com `decision=rejected` sobre o token de `aprovar`; o processo trata
    `rejected` como *"o cliente cancelou"* (`on_reject`). Mas a AUT-46 exige Bearer humano
    para toda retomada de tarefa que declara capacidade — o MCP não tem Bearer, o cliente
    lia *"não consegui processar"* e o processo seguia suspenso.

    Decisão do dono: o cliente provado **encerra, nunca decide**. Quatro condições, todas
    obrigatórias:
      1. a tarefa declara capacidade (sem isso o portão de aprovação nem roda);
      2. nenhum Bearer foi apresentado — quem tem credencial humana segue o caminho dela;
      3. `decision == "rejected"` — `input`/`approved` continuam exigindo o aprovador;
      4. atestado de evidência do mcp-server (PID-13) E exigência de identidade no token.
         O atestado só é emitido contra uma exigência satisfeita; sem exigência declarada
         não há base de identidade do cliente, e a tarefa segue fechada.
    """
    if required_abac is None or clearance != "session_evidence":
        return False
    if bearer_from_header(request.headers.get("Authorization")):
        return False
    if str((body.payload or {}).get("decision") or "") != "rejected":
        logger.warning(
            "PID-15: atestado de cliente sobre tarefa de aprovação com decision=%r RECUSADO — "
            "o cliente só cancela (rejected); decidir é do aprovador (token=%s)",
            (body.payload or {}).get("decision"), resume_token,
        )
        return False
    if not await _webhook_adapter.resume_requirement(body.tenant_id, resume_token):
        logger.warning(
            "PID-15: cancelamento atestado sobre tarefa SEM exigência de identidade RECUSADO "
            "(token=%s) — sem exigência não há base de identidade do cliente", resume_token,
        )
        return False
    logger.info("PID-15: cancelamento do cliente provado aceito na tarefa de aprovação token=%s", resume_token)
    return True


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
    # AUT-46: o POOL tambem é do servidor. Só custa a busca quando a tarefa declara
    # capacidade — resume de form-fill/wrap-up (322 de 323 dos anônimos) não paga.
    pool_da_tarefa = (
        await _webhook_adapter.resume_task_pool(body.tenant_id, resume_token)
        if required_abac is not None else None
    )
    clearance = _resume_identity_clearance(request)
    if await _customer_cancel_allowed(request, body, resume_token, required_abac, clearance):
        # PID-15 — o CLIENTE provado encerra a tarefa de aprovação; não a decide.
        approver = None
    else:
        approver = _resolve_approver_principal(
            request, body, required_abac,
            pool_da_tarefa    = pool_da_tarefa,
            exigir_credencial = True,
        )

    try:
        session_id = await _webhook_adapter.handle_resume(
            resume_token      = resume_token,
            tenant_id         = body.tenant_id,
            payload           = body.payload,
            resume_origin     = body.resume_origin,
            approver          = approver,
            claim_pool_id     = body.pool_id,
            claim_instance_id = body.instance_id,
            identity_clearance = clearance,
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


class EncerrarParqueRequest(BaseModel):
    """Corpo do encerramento por parque. `tenant_id` é obrigatório: o parque é do
    tenant, e adivinhá-lo encerraria a sessão de outro."""
    tenant_id: str
    motivo:    str | None = None


@app.post("/v1/channels/webhook/sessions/{session_id}/encerrar-parque", status_code=200)
async def encerrar_parque_da_sessao(
    session_id: str, body: EncerrarParqueRequest, request: Request
) -> dict:
    """
    Encerra uma sessão suspensa a partir do PARQUE DURÁVEL (APR-10).

    ⚠️ **Por que ela existe, e é medição.** O `force-complete` do supervisor
    (`mcp-server`, `POST /api/force-complete/{sid}`) resolve o endereço da sessão
    pelo ledger `work_task` do **Redis**, que neste deploy não persiste (`--save ""`
    + `appendonly no`). Medido em 2026-09-09: **zero** chaves `work_task` para
    **54** sessões suspensas — a ação do supervisor não alcançava nenhuma delas, e
    um botão na tela do Monitor responderia 404 em 100% dos casos. O parque
    (RET-11) é a fonte que sobrevive; esta rota é como se age sobre ela.

    ⚠️ **O gateway executa, e não devolve o token.** Seria mais simples expor
    *"qual o token desta sessão?"* e deixar o chamador resumir — e seria entregar
    a credencial de retomada a quem só precisa da AÇÃO. O dono do parque age; o
    chamador pede.

    ⚠️ **UMA porta para o chamador.** O Console e o Monitor continuam chamando o
    `force-complete`; é ELE que cai aqui quando o ledger volátil não existe. Duas
    portas para *"encerrar sessão suspensa"* seria o defeito que este repositório
    persegue — e a que ninguém confere é a que vale.

    Devolve `{session_id, via}`. **404** quando não há parque com endereço: a
    sessão órfã (parque sem token, ou parque nenhum) só é alcançável pelo mutirão
    `infra/scripts/reap_parques_orfaos.sh`, e a mensagem NOMEIA isso em vez de
    dizer só "não encontrado".
    """
    if _webhook_adapter is None or _db_pool is None:
        raise HTTPException(status_code=503, detail="Webhook adapter not initialised")

    # ⚠️ EXIGE credencial, e isto foi medido ao vivo antes de existir: a primeira
    # versão desta rota devolvia **200 e encerrava a sessão SEM `Authorization`
    # nenhum**. A herança veio do `_resolve_approver_principal`, que trata header
    # ausente como *sistema* — postura correta no RESUME (terceiro externo chega com
    # o token na mão) e errada aqui, onde o token quem descobre somos nós e a ação é
    # de SUPERVISOR. O único chamador é o `force-complete` do mcp-server, que já
    # repassa o Bearer.
    #
    # Verificador canônico (`plughub_authz`), nunca uma cópia — § Security. E o
    # campo é o MESMO que o `force-complete` exige (`agent_assist.supervisionar`,
    # `read_write`): dois portões com campos diferentes sobre a mesma ação fariam o
    # mais frouxo ser o que vale.
    from plughub_authz import abac_can, bearer_from_header, verify_user_jwt

    _tok = bearer_from_header(request.headers.get("authorization"))
    # `get_settings()`, e não um `settings` de módulo: ele não existe aqui — a
    # primeira versão usou e o portão morreu com NameError. Falhou FECHADO (500),
    # que é a única coisa aceitável num portão quebrado, mas era defeito igual.
    _payload = verify_user_jwt(_tok, get_settings().auth_jwt_secret) if _tok else None
    if not _payload:
        raise HTTPException(
            status_code=401,
            detail="encerrar-parque exige credencial de supervisor (Bearer ausente ou invalido)",
        )
    if not abac_can(_payload, "agent_assist", "supervisionar", "read_write"):
        # `logger` (do módulo), NÃO `_log`: aquele é local de outra função, e a
        # falha apareceria só no caminho de NEGAR — o pior momento possível.
        logger.warning(
            "encerrar-parque NEGADO: sub=%s session=%s — sem agent_assist.supervisionar",
            _payload.get("sub"), session_id,
        )
        raise HTTPException(
            status_code=403,
            detail="encerrar-parque exige `agent_assist.supervisionar` (read_write)",
        )

    token = await session_parking.endereco_da_sessao(_db_pool, body.tenant_id, session_id)
    if not token:
        raise HTTPException(
            status_code=404,
            detail=(
                f"sessao {session_id} nao tem parque com endereco de retomada: ou nunca "
                f"foi registrada (parque anterior a RET-11), ou foi parqueada sem token. "
                f"Sessao assim nao e retomavel nem encerravel pelo fluxo — o caminho e o "
                f"mutirao `infra/scripts/reap_parques_orfaos.sh`."
            ),
        )

    # Mesmo caminho do gatilho de prazo e do supervisor: o flow segue o seu
    # `on_timeout`, o item é encerrado e o bridge fecha o segmento. Nada aqui
    # escreve status na mão — inventar um `completed` sem evento é a degradação de
    # SINAL TROCADO que a reescrita do force-complete (2026-08-05) fechou.
    aprovador = _resolve_approver_principal(
        request,
        WebhookResumeRequest(tenant_id=body.tenant_id),
        await _webhook_adapter.resume_required_abac(body.tenant_id, token),
    )
    try:
        sid = await _webhook_adapter.handle_resume(
            resume_token  = token,
            tenant_id     = body.tenant_id,
            payload       = {"decision": "timeout",
                             "source":   body.motivo or "supervisor:encerrar-parque"},
            resume_origin = "supervisor",
            approver      = aprovador,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ResumeAlreadyTerminalError as exc:
        raise HTTPException(status_code=409, detail=exc.as_detail())
    if sid is None:
        # O parque diz que há endereço e o Redis não o conhece: o token expirou
        # entre uma coisa e outra. Resolver o parque aqui evita que a proxima
        # leitura repita a oferta de uma acao que nao funciona.
        await session_parking.resolver_parque(
            _db_pool, body.tenant_id, session_id, por="token_expirado", token=token,
        )
        raise HTTPException(
            status_code=409,
            detail=("o parque tinha endereco mas o token ja nao existe no Redis "
                    "(expirado ou consumido). O parque foi resolvido como "
                    "`token_expirado`; use o mutirao para encerrar a sessao."),
        )
    await session_parking.resolver_parque(
        _db_pool, body.tenant_id, session_id, por="supervisor", token=token,
    )
    return {"session_id": sid, "via": "parque_duravel"}


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

    # APR-11 — a regra da AUT-46 vale nas DUAS portas. Tarefa que declara capacidade
    # (aprovação humana → approvals.decide) não é decidida por esta porta, que não tem
    # principal: medido ao vivo, a promoção de deploy que a rota interna recusa com 401
    # foi APROVADA por aqui. Quem tem o Bearer usa a porta interna.
    required_abac = await _webhook_adapter.resume_required_abac(body.tenant_id, resume_token)
    if required_abac is not None:
        _mod, _field = required_abac
        logger.warning(
            "APR-11 401: resume EXTERNO de tarefa que exige %s.%s (token=%s tenant=%s) — "
            "esta porta não tem principal", _mod, _field, resume_token, body.tenant_id,
        )
        raise HTTPException(status_code=401, detail=f"resume: credential required for {_mod}.{_field} tasks")

    # Saneamento do payload ANTES de qualquer uso. Campos de autoridade são
    # removidos, não validados: recusar com 4xx ensinaria ao chamador que eles
    # existem, e aceitar-os-ignorando é o comportamento que já se espera de um
    # corpo livre.
    safe_payload = dict(body.payload or {})
    pedida = safe_payload.get("decision")
    _dropped = [k for k in ("source", "decision") if k in safe_payload]
    for k in _dropped:
        safe_payload.pop(k, None)
    safe_payload["source"] = "external"

    # APR-11 — decisão do dono: num `suspend reason: approval` o aprovador é um SISTEMA e
    # o token é a credencial; a decisão dele (approved | rejected) é o conteúdo do resume.
    # Medido antes: descartada aqui, o bridge assumia `input` e a RECUSA da operadora
    # seguia como aprovação. Sem decisão válida, recusa — aprovação nunca é o default.
    veredito, valor = judge_external_decision(
        pedida, await _webhook_adapter.resume_suspend_reason(body.tenant_id, resume_token),
    )
    if veredito == "refuse":
        logger.warning(
            "APR-11 422: resume externo de aprovação sem decisão válida (%r) token=%s tenant=%s",
            pedida, resume_token, body.tenant_id,
        )
        raise HTTPException(status_code=422, detail=valor)
    if veredito == "accept":
        safe_payload["decision"] = valor
        _dropped = [k for k in _dropped if k != "decision"]
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
async def webhook_pending(contact_identifier: str, tenant_id: str, request: Request) -> dict:
    """
    Check whether a customer has an active pending workflow awaiting confirmation.

    Called by intake agents (via the pending_workflow_get MCP tool) after
    collecting the customer's contact_identifier.  Returns the resume_token
    needed to continue the workflow without creating a new one.
    Interna (IDN-06): quem sabe um identificador não leva o `resume_token`.

    Returns:
      { found: false }                          — no pending workflow
      { found: true, resume_token, context }    — active pending workflow found
    """
    tenant = _identity_caller(request, tenant_id)
    if _webhook_adapter is None:
        raise HTTPException(status_code=503, detail="Webhook adapter not initialised")

    result = await _webhook_adapter.get_pending_workflow(
        tenant_id          = tenant,
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
