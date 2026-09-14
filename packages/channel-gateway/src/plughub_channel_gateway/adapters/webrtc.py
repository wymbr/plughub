"""
adapters/webrtc.py
WebRTC channel adapter — LiveKit SFU signaling, STT/TTS pipeline, session lifecycle.

Architecture: docs/arcos/arc15-webrtc.md

WebSocket endpoint: /ws/webrtc/{pool_id}
Token endpoint:     GET /webrtc/token/{session_id}

WebSocket protocol (per-connection state machine):

  Client → Server:
    {"type": "conn.hello",        "version": "1"}
    {"type": "conn.authenticate", "token": "<customer_JWT>"}
    {"type": "webrtc.hangup"}                    — customer ends call
    {"type": "webrtc.message",    "text": "..."}  — DataChannel text (medium=text)
    {"type": "webrtc.interaction_reply",
     "reply": "...", "interaction_id": "..."}     — DataChannel menu reply
    {"type": "conn.ping"}                         — keepalive probe

  Server → Client:
    {"type": "conn.ready"}
    {"type": "conn.authenticated", "session_id": "...", "participant_id": "..."}
    {"type": "webrtc.ready",
     "livekit_url": "wss://...", "token": "...", "room_name": "plughub-{session_id}",
     "publish": ["audio","video"], "policy_source": "platform_default"}
    {"type": "webrtc.media",                       — o TETO do cliente mudou (VOZ-09)
     "token": "...", "room_name": "...", "publish": [...], "policy_source": "...",
     "reason": "attendant_joined:human|attendant_left:human|..."}

  ⚠️ `publish` é TETO, não ordem: o cliente liga microfone e câmera por ESCOLHA, dentro
     dele, e o SFU recusa o que estiver fora. Até 2026-09-14 o servidor mandava UM meio
     (`negotiated_medium`) para a sessão inteira e `webrtc.renegotiate` o sobrescrevia a
     cada atribuição — um especialista de texto rebaixava o cliente de uma chamada de vídeo.
    {"type": "webrtc.message",   "text": "...", "author": "agent", "ts": "..."}
    {"type": "webrtc.interaction","payload": {...}}
    {"type": "webrtc.typing",    "active": true|false}
    {"type": "webrtc.session_closed", "reason": "..."}
    {"type": "conn.pong"}
    {"type": "conn.error",       "code": "...", "message": "..."}

Token endpoint (agent / supervisor joining LiveKit room):
  GET /webrtc/token/{session_id}?role=agent|supervisor
  Authorization: Bearer <agent_JWT>
  Response: {"token", "livekit_url", "room_name", "publish", "customer_publish",
             "hidden", "policy_source"}

Phase C — STT/TTS pipeline (Arc 15):
  - LiveKitRoomClient connects as bot, subscribes to customer audio track
  - resample_pcm_48_to_8() converts 48kHz PCM → 8kHz μ-law for Deepgram
  - STT finals published to conversations.inbound (content_type=audio_transcript)
  - TTS: MP3 bytes → PCM → LocalAudioTrack injection (when the customer ceiling carries audio)
  - DataChannel text (webrtc.message) → Kafka conversations.inbound (medium=text)
  - DataChannel menu reply (webrtc.interaction_reply) → Redis menu:result:{session_id}

Security invariants (from arc15-webrtc.md):
  - LiveKit tokens are signed exclusively by Channel Gateway.
  - LIVEKIT_API_SECRET is never exposed to browsers.
  - Supervisor tokens always have hidden=True, can_publish=False.
  - Text (WS/DataChannel) is always available; audio/video are per-participant ceilings
    from `media_policy` (VOZ-09).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import jwt as pyjwt
import redis.asyncio as aioredis
from aiokafka import AIOKafkaProducer
from fastapi import WebSocket, WebSocketDisconnect

from ..config import Settings
from .base import ChannelAdapter
from .voice_provider import (
    FallbackSTTProvider,
    FallbackTTSProvider,
    ISTTProvider,
    ITTSProvider,
    DeepgramSTTProvider,
    ElevenLabsTTSProvider,
    MockSTTProvider,
    MockTTSProvider,
)
from .webrtc_provider import (
    IWebRTCProvider,
    LiveKitProvider,
    MockWebRTCProvider,
    TokenGrants,
    WebRTCProviderUnavailable,
    build_room_name,
)
from . import media_policy
from .webrtc_room_client import (
    IWebRTCRoomClient,
    LiveKitRoomClient,
    mp3_to_pcm,
    resample_pcm_48_to_8,
)
from plughub_tasks import disparar

logger = logging.getLogger("plughub.channel-gateway.webrtc")

# ── Constants ─────────────────────────────────────────────────────────────────

_SESSION_TTL        = 14_400   # 4h — matches ws_contact_max_duration_s default
_AUTH_TIMEOUT_S     = 30       # seconds to receive conn.authenticate after hello
_KEEPALIVE_INTERVAL = 20       # seconds between server-side ping probes
_STREAM_BLOCK_MS    = 5_000    # ms to wait on XREAD before looping
_STREAM_WATCHER_SLEEP = 1.0    # seconds to sleep on stream watcher error


# ── WebRTCAdapter ──────────────────────────────────────────────────────────────


class WebRTCAdapter(ChannelAdapter):
    """
    WebRTC channel adapter singleton.

    One instance is created at startup and shared across all active WebSocket
    connections.  Per-connection state is tracked internally via:
      - _connections:  session_id → WebSocket  (for outbound delivery)
      - _customer_media: session_id → teto de mídia do cliente (espelho do Redis)

    The adapter implements the full ChannelAdapter interface so it is
    dispatched by OutboundConsumer the same way as SMS/WhatsApp/Email.
    Additionally, handle_ws() drives the WS signaling lifecycle and
    get_token() issues LiveKit JWT tokens for agents joining the room.
    """

    channel = "webrtc"

    def __init__(
        self,
        *,
        producer:          AIOKafkaProducer,
        redis:             aioredis.Redis,
        settings:          Settings,
        webrtc_provider:   IWebRTCProvider | None = None,
        stt_provider:      ISTTProvider    | None = None,
        tts_provider:      ITTSProvider    | None = None,
        attachment_store:  Any | None = None,   # AttachmentStore | None
    ) -> None:
        self._producer          = producer
        self._redis             = redis
        self._settings          = settings
        # VOZ-01: sem credencial/SDK o provider RECUSA. O adapter guarda o MOTIVO e fecha
        # a porta do canal nomeando-o (`handle_ws`, `get_token`) — em vez de derrubar o
        # boot do gateway inteiro, que levaria webchat/WhatsApp junto por um canal só.
        self._provider_unavailable: WebRTCProviderUnavailable | None = None
        self._provider: IWebRTCProvider | None
        if webrtc_provider is not None:
            self._provider = webrtc_provider
        else:
            try:
                self._provider = self._build_provider()
            except WebRTCProviderUnavailable as exc:
                self._provider = None
                self._provider_unavailable = exc
                logger.error("webrtc: canal FECHADO — %s", exc)
        self._stt               = stt_provider or self._build_stt_provider()
        self._tts               = tts_provider or self._build_tts_provider()
        self._attachment_store  = attachment_store

        # Active WebSocket connections keyed by session_id.
        # Populated in handle_ws(); removed on close.
        self._connections: dict[str, WebSocket] = {}

        # VOZ-09: espelho em memória do TETO do cliente por sessão (fonte: Redis
        # `channel:webrtc:{sid}:media`). Substitui `_mediums`, que guardava UM meio para a
        # sessão inteira e era sobrescrito a cada atribuição.
        self._customer_media: dict[str, frozenset[str]] = {}

        # Phase C: server-side LiveKit room clients (session_id → room client)
        # and STT pipeline tasks.
        self._room_clients: dict[str, IWebRTCRoomClient] = {}
        self._stt_tasks:    dict[str, asyncio.Task]       = {}

        # Phase D: active egress recordings.
        # _session_egress: session_id → { segment_id → egress_id }
        self._session_egress: dict[str, dict[str, str]] = {}

    # ── Provider factories ────────────────────────────────────────────────────

    def _build_provider(self) -> IWebRTCProvider:
        s = self._settings
        return LiveKitProvider(
            url        = s.webrtc_livekit_url,
            api_key    = s.webrtc_livekit_api_key,
            api_secret = s.webrtc_livekit_api_secret,
        )

    @property
    def provider_unavailable(self) -> WebRTCProviderUnavailable | None:
        """O motivo pelo qual o canal está fechado, ou None se o provider existe."""
        return self._provider_unavailable

    def _client_livekit_url(self) -> str:
        """
        URL do SFU que vai ao CLIENTE (browser/Console).

        A interna (`webrtc_livekit_url`) é endereço de dentro da rede do deploy — no
        compose, `ws://livekit:7880`, que browser nenhum resolve. Sem a pública
        configurada devolve a interna, e isso é DITO no log uma vez por processo.
        """
        s = self._settings
        if s.webrtc_livekit_public_url:
            return s.webrtc_livekit_public_url
        if not getattr(self, "_warned_public_url", False):
            self._warned_public_url = True
            logger.warning(
                "webrtc: PLUGHUB_WEBRTC_LIVEKIT_PUBLIC_URL ausente — o cliente recebe a "
                "URL INTERNA do SFU (%s); só funciona se ela for alcançável de fora",
                s.webrtc_livekit_url,
            )
        return s.webrtc_livekit_url

    def _build_stt_provider(self) -> ISTTProvider:
        """
        Build STT chain for WebRTC.
        Reuses voice channel's Deepgram credentials when available.
        Wraps in FallbackSTTProvider so Deepgram outages degrade silently.
        """
        s = self._settings
        if not s.webrtc_stt_enabled or not s.voice_deepgram_api_key:
            return MockSTTProvider()
        primary  = DeepgramSTTProvider(api_key=s.voice_deepgram_api_key)
        fallback = MockSTTProvider()
        return FallbackSTTProvider([primary, fallback])

    def _build_tts_provider(self) -> ITTSProvider:
        """
        Build TTS chain for WebRTC injection.
        ElevenLabs (when api_key set) → MockTTSProvider (no-op stub).
        MockTTSProvider returns b"\\x00"*16 when synthesize_returns_none=False,
        meaning TTS injection will be silently skipped when no TTS provider is
        configured (webrtc_tts_injection_enabled=False is the default guard).
        """
        s = self._settings
        if s.voice_elevenlabs_api_key:
            primary = ElevenLabsTTSProvider(
                api_key  = s.voice_elevenlabs_api_key,
                voice_id = s.voice_elevenlabs_voice_id,
            )
            return FallbackTTSProvider([primary, MockTTSProvider(synthesize_returns_none=True)])
        return MockTTSProvider(synthesize_returns_none=True)

    # ── ChannelAdapter interface — outbound delivery ──────────────────────────

    async def deliver_text(self, payload: dict) -> None:
        """
        Deliver a text message to the WebRTC client.
        Called by OutboundConsumer for msg_type="message.text".

        Always sends webrtc.message over the WebSocket. When the customer ceiling
        carries audio, also attempts TTS injection into the LiveKit room when
        webrtc_tts_injection_enabled=True.
        """
        session_id = payload.get("session_id", "")
        ws = self._connections.get(session_id)
        if not ws:
            logger.debug(
                "webrtc deliver_text: no active connection session=%s", session_id
            )
            return

        text   = (
            payload.get("content", {}).get("text", "")
            or payload.get("text", "")
        )
        author = payload.get("author", {}).get("type", "agent")
        ts     = payload.get("ts", datetime.now(timezone.utc).isoformat())

        await self._ws_send(ws, {
            "type":   "webrtc.message",
            "text":   text,
            "author": author,
            "ts":     ts,
        })

        # Phase C: inject TTS into LiveKit room when the customer's media carries audio
        if (
            text
            and media_policy.AUDIO in self._customer_media.get(session_id, frozenset())
            and self._settings.webrtc_tts_injection_enabled
        ):
            disparar(
                self._tts_inject(session_id, text),
                nome=f"webrtc-tts-{session_id[:8]}",
            )

    async def deliver_menu(self, payload: dict) -> None:
        """
        Deliver an interactive menu or form to the WebRTC client.
        Called by OutboundConsumer for msg_type="menu.payload".
        Sends {"type": "webrtc.interaction", "payload": {...}} over WS.
        """
        session_id = payload.get("session_id", "")
        ws = self._connections.get(session_id)
        if not ws:
            logger.debug(
                "webrtc deliver_menu: no active connection session=%s", session_id
            )
            return

        await self._ws_send(ws, {
            "type":    "webrtc.interaction",
            "payload": payload.get("content", payload),
        })

    async def deliver_typing(self, payload: dict) -> None:
        """
        Deliver a typing indicator to the WebRTC client.
        Called by OutboundConsumer for msg_type="agent.typing".
        Sends {"type": "webrtc.typing", "active": bool} over WS.
        """
        session_id = payload.get("session_id", "")
        ws = self._connections.get(session_id)
        if not ws:
            return

        is_typing = payload.get("typing", True)
        await self._ws_send(ws, {
            "type":   "webrtc.typing",
            "active": is_typing,
        })

    async def deliver_session_closed(self, payload: dict) -> None:
        """
        Notify the WebRTC client that the session has ended and close the WS.
        Called by OutboundConsumer for msg_type="session.closed".
        """
        session_id = payload.get("session_id", "")
        ws = self._connections.get(session_id)
        if not ws:
            logger.debug(
                "webrtc deliver_session_closed: no active connection session=%s",
                session_id,
            )
            return

        reason = payload.get("close_reason", "session_timeout")
        await self._ws_send(ws, {
            "type":   "webrtc.session_closed",
            "reason": reason,
        })
        try:
            await ws.close(code=1000)
        except Exception:
            pass

        # Phase D: stop egress recordings
        await self._stop_all_egress(session_id)

        # Phase C: cancel STT task and disconnect room client
        stt_task = self._stt_tasks.pop(session_id, None)
        if stt_task and not stt_task.done():
            stt_task.cancel()
        room_client = self._room_clients.pop(session_id, None)
        if room_client is not None:
            try:
                await room_client.disconnect()
            except Exception:
                pass

        self._connections.pop(session_id, None)
        self._customer_media.pop(session_id, None)
        logger.info(
            "webrtc session_closed delivered: session=%s reason=%s",
            session_id, reason,
        )

    # ── WebSocket lifecycle ───────────────────────────────────────────────────

    async def handle_ws(self, ws: WebSocket, pool_id: str) -> None:
        """
        Full WebSocket signaling lifecycle for one WebRTC connection.

        Flow:
          accept → send conn.ready
          → receive conn.hello (validate version)
          → receive conn.authenticate (validate JWT, open session, route)
          → send conn.authenticated
          → run 3 concurrent tasks:
              _stream_watcher  — routing.assigned → negotiate → create_room → webrtc.ready
              _receive_loop    — webrtc.hangup → _close_session; conn.ping → conn.pong
              _keepalive       — renew Redis TTL every _KEEPALIVE_INTERVAL seconds
        """
        await ws.accept()
        if self._provider is None:
            # VOZ-01: a porta fecha ANTES da autenticação e do roteamento — um contato
            # não pode ser admitido e roteado para um canal cujo plano de mídia não existe.
            await self._ws_error(
                ws, "media_plane_unavailable", str(self._provider_unavailable),
            )
            return
        await self._ws_send(ws, {"type": "conn.ready"})

        # ── Auth handshake ────────────────────────────────────────────────────
        session_id:     str = ""
        contact_id:     str = ""
        participant_id: str = ""

        # Auth timeout — config-api wins (Single Source); _AUTH_TIMEOUT_S is the
        # fallback default. Same config key as webchat (webchat.auth_timeout_s).
        from ..attachment_store import resolve_ws_auth_timeout_s
        auth_timeout_s = await resolve_ws_auth_timeout_s(
            self._redis, self._settings.tenant_id, _AUTH_TIMEOUT_S,
        )
        try:
            session_id, contact_id, participant_id = await asyncio.wait_for(
                self._auth_handshake(ws, pool_id),
                timeout=float(auth_timeout_s),
            )
        except asyncio.TimeoutError:
            logger.warning("webrtc auth_timeout — closing WS")
            await self._ws_error(ws, "auth_timeout", "Authentication timed out")
            return
        except _AuthError as exc:
            logger.warning("webrtc auth_error code=%s: %s", exc.code, exc.message)
            await self._ws_error(ws, exc.code, exc.message)
            return
        except WebSocketDisconnect:
            logger.debug("webrtc: client disconnected during auth handshake")
            return

        # ── Register connection ───────────────────────────────────────────────
        self._connections[session_id] = ws
        logger.info(
            "webrtc WS connected: session=%s contact=%s pool=%s participant=%s",
            session_id, contact_id, pool_id, participant_id,
        )

        # ── Concurrent tasks ──────────────────────────────────────────────────
        tasks = [
            asyncio.create_task(
                self._stream_watcher(ws, session_id, pool_id),
                name=f"webrtc-stream-{session_id[:8]}",
            ),
            asyncio.create_task(
                self._receive_loop(ws, session_id),
                name=f"webrtc-receive-{session_id[:8]}",
            ),
            asyncio.create_task(
                self._keepalive(session_id),
                name=f"webrtc-keepalive-{session_id[:8]}",
            ),
        ]

        done, pending = await asyncio.wait(
            tasks, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()

        # ── Cleanup ───────────────────────────────────────────────────────────
        self._connections.pop(session_id, None)
        self._customer_media.pop(session_id, None)
        logger.info("webrtc WS session complete: session=%s contact=%s", session_id, contact_id)

    # ── Auth handshake ────────────────────────────────────────────────────────

    async def _auth_handshake(
        self, ws: WebSocket, pool_id: str
    ) -> tuple[str, str, str]:
        """
        Exchange conn.hello / conn.authenticate with the client.

        Returns (session_id, contact_id, participant_id).
        Raises _AuthError on invalid token.
        Publishes contact_open + routing request to conversations.inbound Kafka.
        """
        s = self._settings

        # Step 1: receive conn.hello
        raw = await ws.receive_text()
        msg = json.loads(raw)
        if msg.get("type") != "conn.hello":
            raise _AuthError("bad_message", f"Expected conn.hello, got: {msg.get('type')}")

        # Step 2: receive conn.authenticate
        raw = await ws.receive_text()
        msg = json.loads(raw)
        if msg.get("type") != "conn.authenticate":
            raise _AuthError("bad_message", f"Expected conn.authenticate, got: {msg.get('type')}")

        token = msg.get("token", "")
        if not token:
            raise _AuthError("missing_token", "conn.authenticate requires 'token'")

        # Validate customer JWT — per-tenant secret with Redis override
        jwt_secret = await self._resolve_jwt_secret(s.tenant_id)
        try:
            claims = pyjwt.decode(
                token,
                jwt_secret,
                algorithms=["HS256"],
            )
        except pyjwt.ExpiredSignatureError:
            raise _AuthError("token_expired", "Customer token has expired")
        except pyjwt.InvalidTokenError as exc:
            raise _AuthError("invalid_token", f"Customer token invalid: {exc}")

        contact_id = claims.get("sub", "")
        if not contact_id:
            raise _AuthError("missing_sub", "Token must contain 'sub' claim (contact_id)")

        # Resolve pool_id via Layer 2 (ChannelEndpoint lookup in agent-registry)
        resolved_pool = await self._resolve_pool(pool_id, contact_id)

        # Assign session ID and participant ID
        session_id     = str(uuid.uuid4())
        participant_id = str(uuid.uuid4())

        # Store session meta in Redis
        ttl = s.session_ttl_seconds
        await self._redis.setex(
            f"channel:webrtc:{contact_id}:session", ttl, session_id
        )
        await self._redis.setex(
            f"session:{session_id}:contact_id", ttl, contact_id
        )
        await self._redis.setex(
            f"session:{session_id}:meta",
            ttl,
            json.dumps({
                "contact_id":             contact_id,
                "session_id":             session_id,
                "tenant_id":              s.tenant_id,
                "channel":                "webrtc",
                "pool_id":                resolved_pool,
                "customer_participant_id": participant_id,
            }),
        )

        # Publish contact_open event to start session in platform
        await self._publish_inbound({
            "type":                   "contact_open",
            "session_id":             session_id,
            "tenant_id":              s.tenant_id,
            "customer_id":            contact_id,
            "channel":                "webrtc",
            "pool_id":                resolved_pool,
            "customer_participant_id": participant_id,
        })

        # Route inbound to the pool
        await self._publish_inbound({
            "type":        "routing.request",
            "session_id":  session_id,
            "tenant_id":   s.tenant_id,
            "customer_id": contact_id,
            "channel":     "webrtc",
            "pool_id":     resolved_pool,
        })

        # Confirm authentication to client
        await self._ws_send(ws, {
            "type":           "conn.authenticated",
            "session_id":     session_id,
            "participant_id": participant_id,
        })

        logger.info(
            "webrtc auth ok: contact=%s session=%s pool=%s",
            contact_id, session_id, resolved_pool,
        )
        return session_id, contact_id, participant_id

    # ── Stream watcher — routing.assigned → webrtc.ready ─────────────────────

    async def _stream_watcher(
        self, ws: WebSocket, session_id: str, pool_id: str
    ) -> None:
        """
        Watch session:{id}:stream for routing.assigned events.

        On routing.assigned:
          1st → register attendant, compute the customer ceiling (`media_policy`),
                create_room, token recortado por fonte, webrtc.ready
          next → attendant SOMA ao conjunto e o teto é refeito (webrtc.media)
        On participant_left: attendant sai do conjunto e o teto é refeito.

        Continues watching after webrtc.ready to detect session.closed events
        (e.g. agent done, max duration exceeded) and stop the keepalive.
        """
        s          = self._settings
        stream_key = f"session:{session_id}:stream"
        last_id    = "0-0"
        ready_sent = False

        while True:
            try:
                results = await self._redis.xread(
                    {stream_key: last_id},
                    count=20,
                    block=_STREAM_BLOCK_MS,
                )
                if not results:
                    continue

                for _, entries in results:
                    for entry_id, fields in entries:
                        last_id    = entry_id
                        event_type = fields.get("type", "")

                        if event_type == "routing.assigned":
                            if not ready_sent:
                                # First assignment — full setup (room + token + ready)
                                await self._on_routing_assigned(
                                    ws, session_id, fields, s
                                )
                                ready_sent = True
                            else:
                                # Atendente a mais (transferência, especialista, hook):
                                # SOMA ao conjunto e refaz o teto do cliente.
                                await self._on_routing_renegotiate(
                                    ws, session_id, fields, s
                                )

                        elif event_type == "participant_left" and ready_sent:
                            await self._on_attendant_left(ws, session_id, fields)

                        elif event_type in ("session.closed", "agent_done"):
                            # Session ended from server side — stop watcher
                            logger.info(
                                "webrtc stream_watcher: %s for session=%s",
                                event_type, session_id,
                            )
                            return

            except Exception as exc:
                logger.debug(
                    "webrtc stream_watcher error (session=%s): %s",
                    session_id, exc,
                )
                await asyncio.sleep(_STREAM_WATCHER_SLEEP)

    # ── Mídia por PARTICIPANTE (VOZ-09) ───────────────────────────────────────
    #
    # O estado de mídia da sessão é `channel:webrtc:{sid}:media`, um JSON com DOIS fatos
    # de escopo diferente, e é por isso que são dois campos e não um meio:
    #   attendants  {instance_id: framework} — quem atende AGORA (entra no
    #               `routing.assigned`, sai no `participant_left`);
    #   customer    {publish, policy_source, reason, updated_at} — o TETO do cliente,
    #               derivado dos atendentes pela `media_policy`.
    # Um único watcher por sessão processa o stream em ordem, então ler-modificar-gravar
    # aqui não concorre consigo mesmo.

    def _media_key(self, session_id: str) -> str:
        return f"channel:webrtc:{session_id}:media"

    async def _load_media_state(self, session_id: str) -> dict:
        raw = await self._redis.get(self._media_key(session_id))
        if not raw:
            return {"attendants": {}, "customer": None}
        try:
            state = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.error(
                "webrtc media: estado ILEGIVEL em %s — recomeçando SEM atendentes (teto vazio)",
                self._media_key(session_id),
            )
            return {"attendants": {}, "customer": None}
        state.setdefault("attendants", {})
        state.setdefault("customer", None)
        return state

    async def _save_media_state(self, session_id: str, state: dict) -> None:
        await self._redis.setex(
            self._media_key(session_id), self._settings.session_ttl_seconds, json.dumps(state),
        )

    async def _customer_identity(self, session_id: str) -> str:
        contact_id = await self._redis.get(f"session:{session_id}:contact_id") or session_id
        return f"customer-{contact_id}"

    def _customer_grants(self, room_name: str, identity: str, publish: frozenset[str]) -> TokenGrants:
        policy = media_policy.role_policy(media_policy.CUSTOMER)
        return TokenGrants(
            room_name           = room_name,
            identity            = identity,
            display_name        = "Customer",
            can_publish         = True,
            can_subscribe       = policy.subscribe,
            can_publish_data    = True,
            hidden              = policy.hidden,
            ttl_seconds         = self._settings.webrtc_token_ttl_s,
            can_publish_sources = tuple(media_policy.publish_sources(publish)),
        )

    @staticmethod
    def _framework_of(fields: dict, instance_id: str) -> str:
        framework = fields.get("framework", "")
        if framework not in media_policy.CONSUMES_BY_FRAMEWORK:
            # Restritivo vence: framework ausente consome NADA. Dito, não suposto.
            logger.warning(
                "webrtc media: routing.assigned sem framework reconhecido (%r) para "
                "instance=%s — tratado como atendente que NAO consome midia",
                framework, instance_id,
            )
        return framework

    async def _on_routing_assigned(
        self,
        ws:         WebSocket,
        session_id: str,
        fields:     dict,
        s:          Settings,
    ) -> None:
        """
        PRIMEIRO `routing.assigned` da sessão: registra o atendente, calcula o teto do
        cliente, cria a sala e envia `webrtc.ready` com o token já recortado por fonte.
        """
        instance_id = fields.get("instance_id", "")
        framework   = self._framework_of(fields, instance_id)
        state = await self._load_media_state(session_id)
        state["attendants"][instance_id or "?"] = framework
        publish = media_policy.customer_ceiling(state["attendants"])
        state["customer"] = {
            "publish":       media_policy.kinds_list(publish),
            "policy_source": media_policy.PLATFORM_DEFAULT_SOURCE,
            "reason":        f"attendant_joined:{framework or 'unknown'}",
            "updated_at":    datetime.now(timezone.utc).isoformat(),
        }
        self._customer_media[session_id] = publish

        room_name = build_room_name(session_id)
        try:
            await self._provider.create_room(room_name)
        except Exception as exc:
            logger.warning(
                "webrtc: create_room failed (session=%s room=%s): %s — continuing",
                session_id, room_name, exc,
            )

        identity = await self._customer_identity(session_id)
        token = self._provider.generate_token(self._customer_grants(room_name, identity, publish))

        ttl = s.session_ttl_seconds
        await self._redis.setex(f"channel:webrtc:{session_id}:room_name", ttl, room_name)
        await self._save_media_state(session_id, state)

        await self._ws_send(ws, {
            "type":          "webrtc.ready",
            "livekit_url":   self._client_livekit_url(),
            "token":         token,
            "room_name":     room_name,
            "publish":       state["customer"]["publish"],
            "policy_source": state["customer"]["policy_source"],
        })
        logger.info(
            "webrtc ready: session=%s publish=%s attendants=%s room=%s",
            session_id, state["customer"]["publish"], state["attendants"], room_name,
        )

        if media_policy.AUDIO in publish:
            disparar(
                self._start_stt_pipeline(session_id, room_name),
                nome=f"webrtc-stt-start-{session_id[:8]}",
            )

        pool_obj   = self._json_field(fields, "pool")
        segment_id = fields.get("segment_id", "")
        if pool_obj.get("webrtc_recording", False) and segment_id and publish:
            disparar(
                self._start_egress(session_id, segment_id, room_name),
                nome=f"webrtc-egress-start-{session_id[:8]}",
            )

    async def _on_routing_renegotiate(
        self,
        ws:         WebSocket,
        session_id: str,
        fields:     dict,
        s:          Settings,
    ) -> None:
        """
        `routing.assigned` SUBSEQUENTE: o atendente SOMA ao conjunto (transferência,
        especialista, hook) — nunca substitui o anterior. Quem sai sai pelo
        `participant_left`.
        """
        instance_id = fields.get("instance_id", "")
        framework   = self._framework_of(fields, instance_id)
        state = await self._load_media_state(session_id)
        state["attendants"][instance_id or "?"] = framework
        await self._apply_customer_ceiling(
            ws, session_id, state, f"attendant_joined:{framework or 'unknown'}",
        )

    async def _on_attendant_left(self, ws: WebSocket, session_id: str, fields: dict) -> None:
        """`participant_left` no stream: o atendente sai do conjunto e o teto é refeito."""
        who = fields.get("author_id", "") or self._json_field(fields, "payload").get("participant_id", "")
        state = await self._load_media_state(session_id)
        if who not in state["attendants"]:
            # Não se inventa quem saiu. Se ids de entrada e saída divergirem, o teto fica
            # MAIS PERMISSIVO do que devia — por isso o aviso nomeia os dois lados.
            logger.warning(
                "webrtc media: participant_left de %r, que nao e atendente registrado "
                "(atendentes=%s) — teto do cliente NAO recalculado (session=%s)",
                who, sorted(state["attendants"]), session_id,
            )
            return
        framework = state["attendants"].pop(who)
        await self._apply_customer_ceiling(ws, session_id, state, f"attendant_left:{framework or 'unknown'}")

    async def _apply_customer_ceiling(
        self, ws: WebSocket, session_id: str, state: dict, reason: str,
    ) -> None:
        """
        Recalcula o teto do cliente e, se mudou, aplica NOS DOIS LADOS: permissão no SFU
        (se o cliente já está na sala) e `webrtc.media` com token novo ao cliente (se
        ainda não entrou, entra com o teto certo). Sem mudança, não faz nada.
        """
        publish = media_policy.customer_ceiling(state["attendants"])
        previous = (state.get("customer") or {}).get("publish")
        new_list = media_policy.kinds_list(publish)
        if previous == new_list:
            await self._save_media_state(session_id, state)
            logger.debug("webrtc media: teto inalterado %s (%s) session=%s", new_list, reason, session_id)
            return

        state["customer"] = {
            "publish":       new_list,
            "policy_source": media_policy.PLATFORM_DEFAULT_SOURCE,
            "reason":        reason,
            "updated_at":    datetime.now(timezone.utc).isoformat(),
        }
        self._customer_media[session_id] = publish
        await self._save_media_state(session_id, state)

        room_name = build_room_name(session_id)
        identity  = await self._customer_identity(session_id)
        try:
            in_room = await self._provider.update_participant_permission(
                room_name, identity, tuple(media_policy.publish_sources(publish)),
            )
        except Exception as exc:
            # O watcher engoliria isto em `debug` e o evento já foi consumido: sem esta
            # linha, o SFU ficaria com a permissão velha e ninguém saberia.
            in_room = None
            logger.error(
                "webrtc media: FALHOU aplicar teto %s no SFU para %s (session=%s): %s — "
                "o cliente recebe o token novo, mas a permissao na sala segue a ANTERIOR",
                new_list, identity, session_id, exc,
            )
        token = self._provider.generate_token(self._customer_grants(room_name, identity, publish))
        await self._ws_send(ws, {
            "type":          "webrtc.media",
            "token":         token,
            "room_name":     room_name,
            "publish":       new_list,
            "policy_source": state["customer"]["policy_source"],
            "reason":        reason,
        })
        logger.info(
            "webrtc media: teto do cliente %s -> %s (%s; aplicado no SFU=%s) session=%s",
            previous, new_list, reason, in_room, session_id,
        )
        if media_policy.AUDIO in publish and session_id not in self._room_clients:
            disparar(
                self._start_stt_pipeline(session_id, room_name),
                nome=f"webrtc-stt-start-{session_id[:8]}",
            )

    @staticmethod
    def _json_field(fields: dict, name: str) -> dict:
        raw = fields.get(name, "{}")
        try:
            value = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            return {}
        return value if isinstance(value, dict) else {}

    # ── Receive loop ─────────────────────────────────────────────────────────

    async def _receive_loop(self, ws: WebSocket, session_id: str) -> None:
        """
        Receive messages from the WebRTC client until disconnect or hangup.

        Handled message types:
          webrtc.hangup             → publish contact_close, stop tasks
          webrtc.message            → DataChannel text → Kafka conversations.inbound
                                      (medium=text path — customer typing in text mode)
          webrtc.interaction_reply  → DataChannel menu reply → Redis menu:result:{id}
          conn.ping                 → reply conn.pong
          (others)                  → logged and ignored
        """
        try:
            async for raw in ws.iter_text():
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    logger.debug(
                        "webrtc receive_loop: invalid JSON from session=%s", session_id
                    )
                    continue

                msg_type = msg.get("type", "")

                if msg_type == "webrtc.hangup":
                    logger.info("webrtc hangup received: session=%s", session_id)
                    await self._close_session(session_id, "customer_hangup")
                    return

                elif msg_type == "webrtc.message":
                    # DataChannel text input from the customer (medium=text).
                    # Normalize to conversations.inbound the same way webchat does.
                    text = msg.get("text", "").strip()
                    if text:
                        await self._publish_inbound({
                            "type":        "message",
                            "session_id":  session_id,
                            "tenant_id":   self._settings.tenant_id,
                            "channel":     "webrtc",
                            "content_type": "text",
                            "content":     {"text": text},
                            "author":      {"type": "customer"},
                        })
                        logger.debug(
                            "webrtc: DataChannel text → Kafka session=%s len=%d",
                            session_id, len(text),
                        )

                elif msg_type == "webrtc.interaction_reply":
                    # DataChannel menu/form reply — mirror webchat interaction_reply.
                    # Write to Redis so the menu step's BLPOP resolves.
                    reply          = msg.get("reply", "")
                    interaction_id = msg.get("interaction_id", "")
                    if reply:
                        redis_key = f"menu:result:{session_id}"
                        await self._redis.lpush(
                            redis_key,
                            json.dumps({
                                "reply":          reply,
                                "interaction_id": interaction_id,
                            }),
                        )
                        await self._redis.expire(redis_key, _SESSION_TTL)
                        logger.debug(
                            "webrtc: DataChannel interaction_reply → Redis session=%s",
                            session_id,
                        )

                elif msg_type == "conn.ping":
                    await self._ws_send(ws, {"type": "conn.pong"})

                else:
                    logger.debug(
                        "webrtc receive_loop: unhandled type=%s session=%s",
                        msg_type, session_id,
                    )

        except WebSocketDisconnect:
            logger.info("webrtc WS disconnected: session=%s", session_id)
            await self._close_session(session_id, "customer_disconnect")

        except Exception as exc:
            logger.warning(
                "webrtc receive_loop error (session=%s): %s", session_id, exc
            )

    # ── Keepalive ─────────────────────────────────────────────────────────────

    async def _keepalive(self, session_id: str) -> None:
        """
        Renew Redis TTL for WebRTC session keys every _KEEPALIVE_INTERVAL seconds.
        Prevents stale session metadata from expiring during long calls.
        """
        ttl = self._settings.session_ttl_seconds
        while True:
            await asyncio.sleep(_KEEPALIVE_INTERVAL)
            try:
                await self._redis.expire(
                    f"session:{session_id}:meta", ttl
                )
                await self._redis.expire(
                    f"channel:webrtc:{session_id}:room_name", ttl
                )
                await self._redis.expire(self._media_key(session_id), ttl)
            except Exception as exc:
                logger.debug(
                    "webrtc keepalive error (session=%s): %s", session_id, exc
                )

    # ── Token endpoint (for agents / supervisors) ─────────────────────────────

    async def get_token(
        self,
        session_id: str,
        role:       str,   # "agent" | "supervisor"
        identity:   str,   # agent_type_id or human agent user ID
    ) -> dict[str, str] | None:
        """
        Issue a LiveKit token for an agent or supervisor joining an active session.

        Called by the GET /webrtc/token/{session_id} HTTP endpoint after the
        agent's JWT is validated by the FastAPI route (Authorization: Bearer).

        Returns {token, livekit_url, room_name, publish, customer_publish, hidden,
        policy_source} — `publish` é o teto DESTE participante; `customer_publish` é o do
        cliente, para a tela decidir o que mostrar. Não há mais `negotiated_medium`: um meio
        único para a sessão era o defeito (VOZ-09).
        Returns None if the session has no active LiveKit room yet.
        Raises WebRTCProviderUnavailable when the media plane is not configured.
        """
        if self._provider is None:
            raise self._provider_unavailable or WebRTCProviderUnavailable(["provider"])
        room_name = await self._redis.get(f"channel:webrtc:{session_id}:room_name")

        if not room_name:
            logger.debug(
                "webrtc get_token: room not yet ready for session=%s", session_id
            )
            return None

        policy = media_policy.role_policy(
            media_policy.SUPERVISOR if role == "supervisor" else media_policy.AGENT
        )
        grants = TokenGrants(
            room_name           = room_name,
            identity            = f"{role}-{identity}",
            display_name        = identity,
            can_publish         = True,
            can_subscribe       = policy.subscribe,
            can_publish_data    = not policy.hidden,
            hidden              = policy.hidden,
            ttl_seconds         = self._settings.webrtc_token_ttl_s,
            can_publish_sources = tuple(media_policy.publish_sources(policy.publish)),
        )

        # Sem try: assinar é local, e uma falha aqui é defeito. Antes ela virava `None`
        # e a rota respondia 404 *"room not ready"* — motivo plausível e falso.
        token = self._provider.generate_token(grants)
        state = await self._load_media_state(session_id)

        return {
            "token":            token,
            "livekit_url":      self._client_livekit_url(),
            "room_name":        room_name,
            "publish":          media_policy.kinds_list(policy.publish),
            "customer_publish": (state.get("customer") or {}).get("publish", []),
            "hidden":           policy.hidden,
            "policy_source":    media_policy.PLATFORM_DEFAULT_SOURCE,
        }

    # ── Session close ─────────────────────────────────────────────────────────

    async def _close_session(self, session_id: str, reason: str) -> None:
        """
        Publish a contact_close event to conversations.inbound and tear down
        Phase C resources (STT task + room client).

        The platform (Core) handles session bookkeeping and publishes
        session.closed to the stream, which eventually reaches deliver_session_closed.
        """
        # Phase D: stop egress recordings before Phase C cleanup
        await self._stop_all_egress(session_id)

        # Phase C: stop STT pipeline and disconnect room client
        stt_task = self._stt_tasks.pop(session_id, None)
        if stt_task and not stt_task.done():
            stt_task.cancel()

        room_client = self._room_clients.pop(session_id, None)
        if room_client is not None:
            try:
                await room_client.disconnect()
            except Exception as exc:
                logger.debug(
                    "webrtc: room_client disconnect error (session=%s): %s",
                    session_id, exc,
                )

        try:
            await self._publish_inbound({
                "type":         "contact_close",
                "session_id":   session_id,
                "tenant_id":    self._settings.tenant_id,
                "channel":      "webrtc",
                "close_reason": reason,
            })
            logger.info(
                "webrtc: published contact_close session=%s reason=%s",
                session_id, reason,
            )
        except Exception as exc:
            logger.error(
                "webrtc: contact_close publish failed (session=%s): %s",
                session_id, exc,
            )

    # ── Phase C: STT pipeline ─────────────────────────────────────────────────

    async def _start_stt_pipeline(self, session_id: str, room_name: str) -> None:
        """
        Connect a server-side LiveKit room client and start the STT pipeline.

        Called when the customer ceiling carries audio (VOZ-09)
        and webrtc_stt_enabled=True.
        """
        s = self._settings
        if not s.webrtc_stt_enabled:
            return

        # Generate a bot token for the server-side room participant
        try:
            bot_identity = f"bot-{session_id[:8]}"
            grants       = TokenGrants(
                room_name        = room_name,
                identity         = bot_identity,
                display_name     = "STT Bot",
                can_publish      = s.webrtc_tts_injection_enabled,
                can_subscribe    = True,
                can_publish_data = False,
                hidden           = True,
                ttl_seconds      = s.webrtc_token_ttl_s,
            )
            bot_token = self._provider.generate_token(grants)
        except Exception as exc:
            logger.warning(
                "webrtc: bot token generation failed (session=%s): %s — STT disabled",
                session_id, exc,
            )
            return

        # Create and connect room client
        room_client = LiveKitRoomClient()
        try:
            await room_client.connect(
                room_name   = room_name,
                identity    = bot_identity,
                token       = bot_token,
                livekit_url = s.webrtc_livekit_url,
            )
        except Exception as exc:
            logger.warning(
                "webrtc: room_client connect failed (session=%s): %s — STT disabled",
                session_id, exc,
            )
            return

        self._room_clients[session_id] = room_client

        # Launch STT pipeline as background task
        task = asyncio.create_task(
            self._stt_pipeline(session_id, room_client),
            name=f"webrtc-stt-{session_id[:8]}",
        )
        self._stt_tasks[session_id] = task
        logger.info(
            "webrtc: STT pipeline started: session=%s room=%s", session_id, room_name
        )

    async def _stt_pipeline(
        self, session_id: str, room_client: IWebRTCRoomClient
    ) -> None:
        """
        STT pipeline coroutine (runs as background task).

        Loop:
          1. Read 48kHz PCM frames from room_client.subscribe_customer_audio()
          2. Resample → 8kHz μ-law via resample_pcm_48_to_8()
          3. Feed resampled chunks to FallbackSTTProvider.stream()
          4. Publish is_final=True transcripts to Kafka conversations.inbound
             with content_type="audio_transcript"
        """
        s = self._settings

        async def _audio_chunks():
            async for chunk in room_client.subscribe_customer_audio():
                # Resample 48kHz PCM to 8kHz μ-law in-line
                try:
                    yield resample_pcm_48_to_8(chunk)
                except Exception as exc:
                    logger.debug(
                        "webrtc stt_pipeline: resample error (session=%s): %s",
                        session_id, exc,
                    )

        try:
            language = s.voice_stt_language  # reuse voice channel language setting
            async for result in self._stt.stream(_audio_chunks(), language=language):
                if result.is_final and result.transcript.strip():
                    await self._publish_transcript(
                        session_id  = session_id,
                        transcript  = result.transcript,
                        confidence  = result.confidence,
                        start_ms    = result.start_ms,
                        end_ms      = result.end_ms,
                    )
        except asyncio.CancelledError:
            logger.debug("webrtc stt_pipeline cancelled: session=%s", session_id)
        except Exception as exc:
            logger.warning(
                "webrtc stt_pipeline error (session=%s): %s", session_id, exc
            )

    async def _publish_transcript(
        self,
        session_id: str,
        transcript: str,
        confidence: float,
        start_ms:   int,
        end_ms:     int,
    ) -> None:
        """
        Publish a final STT transcript to Kafka conversations.inbound.
        content_type="audio_transcript" distinguishes voice input from text input.
        """
        try:
            await self._publish_inbound({
                "type":        "message",
                "session_id":  session_id,
                "tenant_id":   self._settings.tenant_id,
                "channel":     "webrtc",
                "content_type": "audio_transcript",
                "content": {
                    "text":       transcript,
                    "confidence": confidence,
                    "start_ms":   start_ms,
                    "end_ms":     end_ms,
                },
                "author": {"type": "customer"},
            })
            logger.debug(
                "webrtc: transcript published session=%s len=%d",
                session_id, len(transcript),
            )
        except Exception as exc:
            logger.warning(
                "webrtc: transcript publish failed (session=%s): %s", session_id, exc
            )

    async def _tts_inject(
        self,
        session_id: str,
        text:       str,
        voice_id:   str | None = None,
    ) -> None:
        """
        Synthesize text → MP3 → PCM and inject into the LiveKit room.

        Called as a fire-and-forget task from deliver_text() when:
          - the customer ceiling carries audio (VOZ-09)
          - webrtc_tts_injection_enabled=True
          - a room client is active for this session
        """
        room_client = self._room_clients.get(session_id)
        if room_client is None:
            logger.debug(
                "webrtc tts_inject: no room client for session=%s — skipping",
                session_id,
            )
            return

        try:
            mp3_bytes = await self._tts.synthesize(text, voice_id)
        except Exception as exc:
            logger.warning(
                "webrtc tts_inject: TTS synthesis failed (session=%s): %s",
                session_id, exc,
            )
            return

        if not mp3_bytes:
            logger.debug(
                "webrtc tts_inject: TTS returned None (session=%s) — skipping",
                session_id,
            )
            return

        pcm_bytes = mp3_to_pcm(mp3_bytes, target_sample_rate=24000)
        if not pcm_bytes:
            return  # mp3_to_pcm already logged the warning

        try:
            await room_client.publish_audio(pcm_bytes, sample_rate=24000)
            logger.debug(
                "webrtc tts_inject: injected %d bytes (session=%s)",
                len(pcm_bytes), session_id,
            )
        except Exception as exc:
            logger.warning(
                "webrtc tts_inject: publish_audio failed (session=%s): %s",
                session_id, exc,
            )

    # ── Phase D: Egress Recording ─────────────────────────────────────────────

    async def _start_egress(
        self,
        session_id: str,
        segment_id: str,
        room_name:  str,
    ) -> None:
        """
        Announce LGPD notice then start a LiveKit composite egress for this
        session/segment.

        Guard against double-start: if the Redis key already exists (e.g. rapid
        re-trigger), the call is a no-op.  The egress_id is stored both in the
        in-process dict and in Redis so teardown works even after a Gateway
        restart (best-effort — restart gap leaves egress running, not crashed).

        Called as a fire-and-forget task from _on_routing_assigned() when
        pool.webrtc_recording=True and the customer ceiling is not empty.
        """
        s = self._settings

        # Double-start guard
        rec_key = f"channel:webrtc:{session_id}:egress:{segment_id}"
        if await self._redis.exists(rec_key):
            logger.info(
                "webrtc egress: already recording session=%s segment=%s — skip",
                session_id, segment_id,
            )
            return

        # Claim the recording slot before any async work to prevent races
        await self._redis.set(rec_key, "starting", ex=_SESSION_TTL)

        # LGPD recording notice — prefer TTS injection when the room client is
        # active; fall back to a text message over WebSocket.
        notice = s.webrtc_recording_notice
        if (
            media_policy.AUDIO in self._customer_media.get(session_id, frozenset())
            and s.webrtc_tts_injection_enabled
            and session_id in self._room_clients
        ):
            await self._tts_inject(session_id, notice)
        else:
            ws = self._connections.get(session_id)
            if ws:
                await self._ws_send(ws, {
                    "type": "webrtc.message",
                    "text": notice,
                    "author": "system",
                    "ts": datetime.now(timezone.utc).isoformat(),
                })

        # Natural pause after notice (mirrors voice channel behaviour)
        await asyncio.sleep(1.5)

        # Build output file path (shared volume between LiveKit and Gateway)
        output_dir = Path(s.webrtc_egress_output_dir) / session_id
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(output_dir / f"{segment_id}.mp4")

        try:
            egress_id = await self._provider.start_egress(
                room_name    = room_name,
                output_url   = output_path,
                layout       = "speaker",
                dual_channel = True,
            )
        except Exception as exc:
            logger.error(
                "webrtc egress: start_egress failed session=%s segment=%s: %s",
                session_id, segment_id, exc,
            )
            # Release the claim so a retry is possible
            await self._redis.delete(rec_key)
            return

        # Persist egress_id
        await self._redis.set(rec_key, egress_id, ex=_SESSION_TTL)
        if session_id not in self._session_egress:
            self._session_egress[session_id] = {}
        self._session_egress[session_id][segment_id] = egress_id

        logger.info(
            "webrtc egress started: session=%s segment=%s egress_id=%s output=%s",
            session_id, segment_id, egress_id, output_path,
        )

    async def _stop_all_egress(self, session_id: str) -> None:
        """
        Stop all active egress recordings for a session and commit them to the
        AttachmentStore.  Called from both _close_session() and
        deliver_session_closed() — idempotent (cleared from dict on first call).
        """
        active = self._session_egress.pop(session_id, {})
        if not active:
            return

        for segment_id, egress_id in active.items():
            disparar(
                self._stop_egress_and_store(session_id, segment_id, egress_id),
                nome=f"webrtc-egress-stop-{session_id[:8]}-{segment_id[:8]}",
            )

    async def _stop_egress_and_store(
        self,
        session_id: str,
        segment_id: str,
        egress_id:  str,
    ) -> None:
        """
        Stop the LiveKit egress, wait for file finalization, commit bytes to
        AttachmentStore, and write a recording.completed event to the session
        stream.

        Steps:
          1. stop_egress(egress_id)
          2. sleep(webrtc_egress_wait_s) — LiveKit flushes the MP4 container
          3. Read file from shared output path
          4. Commit to AttachmentStore (if configured) → file_id, serving_url
          5. XADD recording.completed to session stream
          6. Delete local temp file
        """
        s          = self._settings
        output_dir = Path(s.webrtc_egress_output_dir) / session_id
        output_path = output_dir / f"{segment_id}.mp4"

        # Step 1 — stop egress
        try:
            await self._provider.stop_egress(egress_id)
        except Exception as exc:
            logger.warning(
                "webrtc egress: stop_egress error session=%s egress=%s: %s",
                session_id, egress_id, exc,
            )

        # Step 2 — wait for LiveKit to flush the output file
        await asyncio.sleep(s.webrtc_egress_wait_s)

        # Step 3 — read recording bytes
        file_bytes: bytes = b""
        file_size  = 0
        try:
            file_bytes = output_path.read_bytes()
            file_size  = len(file_bytes)
        except FileNotFoundError:
            logger.warning(
                "webrtc egress: recording file not found session=%s path=%s",
                session_id, output_path,
            )
        except Exception as exc:
            logger.warning(
                "webrtc egress: could not read recording session=%s: %s",
                session_id, exc,
            )

        # Step 4 — commit to AttachmentStore
        file_id     = str(uuid.uuid4())
        serving_url = str(output_path)  # fallback: local path

        if file_bytes and self._attachment_store is not None:
            try:
                from ..attachment_store import resolve_attachment_expiry_days
                _expiry_days = await resolve_attachment_expiry_days(
                    self._redis, s.tenant_id, s.attachment_expiry_days
                )
                expires_at = datetime.now(timezone.utc) + timedelta(days=_expiry_days)
                file_id_reserved, _ = await self._attachment_store.reserve(
                    tenant_id  = s.tenant_id,
                    session_id = session_id,
                    file_name  = f"recording-{session_id[:8]}-{segment_id[:8]}.mp4",
                    mime_type  = "video/mp4",
                    size_bytes = file_size,
                    expires_at = expires_at,
                )
                meta = await self._attachment_store.commit(
                    file_id   = file_id_reserved,
                    tenant_id = s.tenant_id,
                    data      = file_bytes,
                )
                file_id     = meta.file_id
                serving_url = getattr(meta, "serving_url", serving_url)
                logger.info(
                    "webrtc egress: recording committed file_id=%s url=%s "
                    "session=%s segment=%s size=%d",
                    file_id, serving_url, session_id, segment_id, file_size,
                )
            except Exception as exc:
                logger.error(
                    "webrtc egress: AttachmentStore commit failed session=%s: %s",
                    session_id, exc,
                )

        # Step 5 — write recording.completed to session stream
        try:
            stream_key = f"session:{session_id}:stream"
            await self._redis.xadd(
                stream_key,
                {
                    "type":       "recording.completed",
                    "session_id": session_id,
                    "segment_id": segment_id,
                    "egress_id":  egress_id,
                    "file_id":    file_id,
                    "serving_url": serving_url,
                    "size_bytes": str(file_size),
                    "channel":    "webrtc",
                },
            )
            logger.info(
                "webrtc egress: recording.completed event written to stream "
                "session=%s segment=%s file_id=%s",
                session_id, segment_id, file_id,
            )
        except Exception as exc:
            logger.error(
                "webrtc egress: stream XADD failed session=%s: %s", session_id, exc
            )

        # Step 6 — clean up local temp file
        if file_bytes:
            try:
                output_path.unlink(missing_ok=True)
                # Remove dir if empty
                try:
                    output_dir.rmdir()
                except OSError:
                    pass
            except Exception as exc:
                logger.debug(
                    "webrtc egress: cleanup error session=%s path=%s: %s",
                    session_id, output_path, exc,
                )

        # Clear Redis egress key
        try:
            await self._redis.delete(
                f"channel:webrtc:{session_id}:egress:{segment_id}"
            )
        except Exception:
            pass

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _resolve_pool(self, pool_id: str, contact_id: str) -> str:
        """
        Resolve pool_id via Layer 2 agent-registry lookup (ChannelEndpoint).
        Falls back to webrtc_default_pool_id when no endpoint record matches.
        """
        s = self._settings
        if pool_id and s.agent_registry_url:
            try:
                from ..endpoint_resolver import resolve_pool as _resolve
                resolved = await _resolve(
                    channel            = "webrtc",
                    identifier         = pool_id,
                    tenant_id          = s.tenant_id,
                    agent_registry_url = s.agent_registry_url,
                    cache_ttl_s        = s.endpoint_cache_ttl_s,
                )
                if resolved:
                    return resolved
            except Exception as exc:
                logger.warning("webrtc pool resolve failed: %s", exc)
        return pool_id or s.webrtc_default_pool_id

    async def _resolve_jwt_secret(self, tenant_id: str) -> str:
        """
        Resolve JWT secret: Redis per-tenant override → env var default.
        Mirrors the webchat channel JWT secret resolution.
        """
        try:
            override = await self._redis.get(
                f"{tenant_id}:config:webchat:jwt_secret"
            )
            if override:
                return override
        except Exception:
            pass
        return self._settings.jwt_secret

    async def _publish_inbound(self, payload: dict) -> None:
        """Publish to conversations.inbound Kafka topic."""
        await self._producer.send(
            self._settings.kafka_topic_inbound,
            json.dumps(payload).encode(),
        )

    @staticmethod
    async def _ws_send(ws: WebSocket, message: dict) -> None:
        """Send a JSON message over the WebSocket; silently ignore closed socket."""
        try:
            await ws.send_json(message)
        except Exception as exc:
            logger.debug("webrtc ws_send failed: %s", exc)

    @staticmethod
    async def _ws_error(ws: WebSocket, code: str, message: str) -> None:
        """Send conn.error and close the WebSocket."""
        await WebRTCAdapter._ws_send(ws, {
            "type":    "conn.error",
            "code":    code,
            "message": message,
        })
        try:
            await ws.close(code=4003)
        except Exception:
            pass


# ── Internal exceptions ───────────────────────────────────────────────────────


class _AuthError(Exception):
    """Raised during WebRTC auth handshake with a structured error code."""
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code    = code
        self.message = message
