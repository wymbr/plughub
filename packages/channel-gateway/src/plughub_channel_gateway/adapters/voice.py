"""
adapters/voice.py
Voice channel adapter — Twilio Media Streams + CPaaS conference bridge.

Architecture: channel-gateway-multi-channel.md § 9

Two simultaneous bindings per active call:
  1. HTTP webhooks  → /webhooks/voice/inbound, /status, /recording
  2. WebSocket      → /voice/media  (Twilio Media Streams — audio bot leg)

Three async loops inside handle_media_ws():
  - _stt_loop      : audio chunks → Deepgram → transcript → Kafka
  - _collect_loop  : DTMF / STT collect state machine
  - _keepalive     : session Redis TTL renewal

Outbound (deliver_outbound):
  - notify / interaction.request → TTS via conference announce_url
  - session.closed               → CPaaS hangup

Segment recording (§ 13):
  - Watches session stream for routing.assigned events
  - Starts / stops CPaaS recording per segment based on pool.voice_recording

Collect step (§ 9.10):
  - Consumes collect.events Kafka topic (channel: voice)
  - Creates outbound call, stores pending_collect Redis key for correlation
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

import redis.asyncio as aioredis
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from fastapi import WebSocket

from ..attachment_store import AttachmentStore
from ..config import Settings
from .base import ChannelAdapter
from .voice_provider import (
    ISTTProvider,
    ITTSProvider,
    IVoiceProvider,
    STTResult,
    TwilioVoiceProvider,
    TwilioSayTTSProvider,
    DeepgramSTTProvider,
    DeepgramAuraTTSProvider,
    ElevenLabsTTSProvider,
    FallbackSTTProvider,
    FallbackTTSProvider,
    MockVoiceProvider,
    MockSTTProvider,
    MockTTSProvider,
)

logger = logging.getLogger("plughub.channel-gateway.voice")

# ── Constants ─────────────────────────────────────────────────────────────────

_SESSION_TTL       = 86_400   # 24h — active call session Redis TTL
_COLLECT_TTL       = 1_800    # 30min — voice menu collect state
_PENDING_CALL_TTL  = 300      # 5min — outbound call pending collect
_TTS_TEXT_TTL      = 60       # 60s — TTS text in Redis for /voice/tts endpoint
_KEEPALIVE_INTERVAL = 15      # seconds between Redis TTL renewal

_DEFAULT_RECORDING_NOTICE = (
    "Esta chamada poderá ser gravada para fins de qualidade e treinamento."
)


# ── VoiceAdapter ──────────────────────────────────────────────────────────────


class VoiceAdapter(ChannelAdapter):
    """
    Voice channel adapter.

    Unlike WebhookAdapter subclasses (WhatsApp, SMS, Email), VoiceAdapter
    is not a WebSocket *server* — it's a WebSocket *server* for the CPaaS
    Media Streams bot leg AND a webhook handler for call control events.

    Instantiated once in main.py lifespan; routes are registered there.
    """

    channel_name = "voice"

    def __init__(
        self,
        producer:         AIOKafkaProducer,
        redis:            aioredis.Redis,
        settings:         Settings,
        attachment_store: AttachmentStore | None = None,
        voice_provider:   IVoiceProvider | None = None,
        stt_provider:     ISTTProvider   | None = None,
        tts_provider:     ITTSProvider   | None = None,
    ) -> None:
        super().__init__(
            producer=producer,
            redis=redis,
            settings=settings,
            store=attachment_store,
        )
        self._voice = voice_provider or self._build_voice_provider()
        self._stt   = stt_provider   or self._build_stt_provider()
        self._tts   = tts_provider   or self._build_tts_provider()

        # segment_id → CPaaS recording SID
        self._active_recordings: dict[str, str] = {}

    # ── Provider factories ────────────────────────────────────────────────────

    def _build_voice_provider(self) -> IVoiceProvider:
        s = self._settings
        if s.voice_provider == "mock":
            return MockVoiceProvider()
        dev_mode = not s.voice_account_sid or not s.voice_auth_token
        return TwilioVoiceProvider(
            account_sid = s.voice_account_sid,
            auth_token  = s.voice_auth_token,
            from_number = s.voice_from_number,
            dev_mode    = dev_mode,
        )

    def _build_stt_provider(self) -> ISTTProvider:
        """
        Build the STT provider chain.

        Primary: Deepgram streaming WebSocket (when api_key is set).
        Fallback: MockSTTProvider (silent — no STT output, safe for production).

        The primary is wrapped in FallbackSTTProvider so that transient
        Deepgram outages do not crash active calls.
        """
        s = self._settings
        if s.voice_stt_provider == "mock" or not s.voice_deepgram_api_key:
            return MockSTTProvider()

        primary  = DeepgramSTTProvider(api_key=s.voice_deepgram_api_key)
        fallback = MockSTTProvider()   # silent — keeps call alive without STT
        return FallbackSTTProvider([primary, fallback])

    def _build_tts_provider(self) -> ITTSProvider:
        """
        Build the TTS provider chain.

        Primary options (in descending quality):
          1. ElevenLabs REST (PLUGHUB_VOICE_ELEVENLABS_API_KEY set)
          2. Deepgram Aura REST (voice_tts_provider=deepgram_aura)
          3. TwilioSay (built-in <Say> verb, no external API)

        TwilioSay is always the last-resort fallback when other providers
        fail or are not configured — it never requires an external API call.
        """
        s = self._settings
        twiliosay = TwilioSayTTSProvider()

        # ElevenLabs as primary when api_key is set
        if s.voice_elevenlabs_api_key:
            primary = ElevenLabsTTSProvider(
                api_key  = s.voice_elevenlabs_api_key,
                voice_id = s.voice_elevenlabs_voice_id,
            )
            return FallbackTTSProvider([primary, twiliosay])

        # Deepgram Aura as primary when explicitly selected
        if s.voice_tts_provider == "deepgram_aura" and s.voice_deepgram_api_key:
            primary = DeepgramAuraTTSProvider(
                api_key  = s.voice_deepgram_api_key,
                voice_id = s.voice_tts_voice_id,
            )
            return FallbackTTSProvider([primary, twiliosay])

        # Default: TwilioSay only (no external TTS API required)
        return twiliosay

    # ── ChannelAdapter abstract (not used for voice — webhook/WS paths below) ─

    async def handle(self) -> None:
        # Voice lifecycle is driven by webhooks and the media WS.
        # This method is not called by OutboundConsumer.
        raise NotImplementedError("VoiceAdapter is driven by webhooks, not handle()")

    # ── Inbound webhook (POST /webhooks/voice/inbound) ────────────────────────

    async def handle_inbound(
        self,
        params:    dict[str, str],
        signature: str,
        url:       str,
    ) -> str:
        """
        Called by the FastAPI route on every new inbound call.
        Returns a TwiML XML string — FastAPI serves it as text/xml.

        Also handles inbound legs for outbound collect.events calls
        (correlates via pending_collect:{call_sid} Redis key).
        """
        s = self._settings

        if not await self._voice.verify_signature(url, params, signature):
            logger.warning("voice inbound rejected — invalid HMAC signature")
            return "<Response><Reject/></Response>"

        call_sid   = params.get("CallSid", "")
        from_num   = params.get("From", "")
        to_num     = params.get("To", "")
        contact_id = _normalize_e164(from_num)

        # ── Check if this is an outbound collect call ─────────────────────────
        pending_key   = f"channel:voice:pending_collect:{call_sid}"
        pending_raw   = await self._redis.get(pending_key)
        if pending_raw:
            pending = json.loads(pending_raw)
            pool_id = pending["pool_id"]
            await self._redis.delete(pending_key)
            logger.info(
                "voice inbound: outbound collect call answered (call=%s collect_token=%s)",
                call_sid, pending.get("collect_token"),
            )
        else:
            pool_id = await self._resolve_pool(contact_id, to_num)

        session_id = str(uuid.uuid4())

        # Register session
        session_key = f"channel:voice:{contact_id}:session"
        await self._redis.set(session_key, session_id, ex=_SESSION_TTL)
        await self._redis.set(
            f"channel:voice:{call_sid}:session", session_id, ex=_SESSION_TTL
        )

        # Open session in platform
        await self._open_session(
            contact_id         = contact_id,
            session_id         = session_id,
            pool_id            = pool_id,
            channel            = "voice",
            tenant_id          = s.tenant_id,
            customer_participant_id = str(uuid.uuid4()),
            channel_session_id = call_sid,
        )

        # Route to agent pool
        await self._route_inbound(
            pool_id    = pool_id,
            contact_id = contact_id,
            session_id = session_id,
            channel    = "voice",
            tenant_id  = s.tenant_id,
        )

        # Generate TwiML — opens Media Streams WS + places customer in conference
        twiml = self._voice.generate_inbound_twiml(
            session_id = session_id,
            host       = s.voice_webhook_host,
            wait_url   = s.voice_conference_wait_url,
        )
        logger.info(
            "voice inbound: call=%s from=%s session=%s pool=%s",
            call_sid, contact_id, session_id, pool_id,
        )
        return twiml

    async def _resolve_pool(self, contact_id: str, to_number: str) -> str:
        """
        Resolve pool_id from the called DID number via agent-registry Layer 2.
        Falls back to voice_default_pool_id if no ChannelEndpoint matches.
        """
        from ..endpoint_resolver import resolve_pool
        s = self._settings
        if s.agent_registry_url:
            try:
                resolved = await resolve_pool(
                    channel            = "voice",
                    identifier         = to_number,
                    tenant_id          = s.tenant_id,
                    agent_registry_url = s.agent_registry_url,
                    cache_ttl_s        = s.endpoint_cache_ttl_s,
                )
                if resolved:
                    return resolved
            except Exception as exc:
                logger.warning("voice pool resolve failed: %s", exc)
        return s.voice_default_pool_id

    # ── Status webhook (POST /webhooks/voice/status) ──────────────────────────

    async def handle_status(self, params: dict[str, str]) -> None:
        """
        Called by Twilio on conference participant events (leave/join/end).
        Used to detect customer hangup and close the PlugHub session.
        """
        call_sid        = params.get("CallSid", "")
        call_status     = params.get("CallStatus", "")
        conference_sid  = params.get("ConferenceSid", "")

        if call_status in ("completed", "no-answer", "busy", "failed"):
            session_id = await self._redis.get(
                f"channel:voice:{call_sid}:session"
            )
            if session_id:
                reason = {
                    "completed": "customer_hangup",
                    "no-answer": "no_resource",
                    "busy":      "no_resource",
                    "failed":    "system_error",
                }.get(call_status, "customer_hangup")

                logger.info(
                    "voice status: call=%s status=%s → closing session=%s reason=%s",
                    call_sid, call_status, session_id, reason,
                )
                await self._close_session(session_id, reason)
                await self._redis.delete(f"channel:voice:{call_sid}:session")
            else:
                logger.debug(
                    "voice status: call=%s status=%s (no session found)",
                    call_sid, call_status,
                )

        if conference_sid:
            # Store conference SID for TTS delivery
            session_id = await self._redis.get(
                f"channel:voice:{call_sid}:session"
            )
            if session_id and call_status == "in-progress":
                await self._redis.set(
                    f"channel:voice:{session_id}:conference_sid",
                    conference_sid,
                    ex=_SESSION_TTL,
                )

    # ── Recording webhook (POST /webhooks/voice/recording) ───────────────────

    async def handle_recording_complete(self, params: dict[str, str]) -> None:
        """
        Called by Twilio when a conference recording is ready for download.
        Downloads the file and stores it in AttachmentStore.
        """
        recording_sid = params.get("RecordingSid", "")
        recording_url = params.get("RecordingUrl", "")
        call_sid      = params.get("CallSid", "")
        duration      = int(params.get("RecordingDuration", "0"))

        if not recording_url or not self._store:
            logger.debug("recording webhook: no URL or no store — skipping")
            return

        session_id = await self._redis.get(
            f"channel:voice:{call_sid}:session"
        )
        if not session_id:
            logger.warning(
                "recording webhook: no session for call=%s rec=%s",
                call_sid, recording_sid,
            )
            return

        # Find segment_id that produced this recording
        segment_id = await self._find_segment_id_for_recording(
            session_id, recording_sid
        )

        asyncio.create_task(
            self._download_and_store_recording(
                session_id    = session_id,
                segment_id    = segment_id,
                recording_url = recording_url,
                recording_sid = recording_sid,
                duration_s    = duration,
            )
        )

    async def _find_segment_id_for_recording(
        self, session_id: str, recording_sid: str
    ) -> str | None:
        """
        Scan Redis keys to find which segment_id owns this recording SID.
        Pattern: channel:voice:{session_id}:rec_id:{segment_id}
        """
        pattern = f"channel:voice:{session_id}:rec_id:*"
        async for key in self._redis.scan_iter(pattern):
            val = await self._redis.get(key)
            if val == recording_sid:
                return key.split(":")[-1]
        return None

    async def _download_and_store_recording(
        self,
        session_id:    str,
        segment_id:    str | None,
        recording_url: str,
        recording_sid: str,
        duration_s:    int,
    ) -> None:
        import httpx as _httpx
        s = self._settings
        try:
            async with _httpx.AsyncClient(
                auth=(s.voice_account_sid, s.voice_auth_token),
                timeout=60.0,
            ) as client:
                resp = await client.get(f"{recording_url}.mp3")
                resp.raise_for_status()
                audio_bytes = resp.content

            file_id, serve_url = await self._store.store(
                session_id  = session_id,
                file_bytes  = audio_bytes,
                mime_type   = "audio/mpeg",
                filename    = f"{segment_id or recording_sid}.mp3",
                uploader_id = "system",
            )
            logger.info(
                "voice recording stored: session=%s segment=%s rec=%s size=%d",
                session_id, segment_id, recording_sid, len(audio_bytes),
            )

            # Publish recording.completed to session stream via Kafka
            event = {
                "type":         "recording.completed",
                "session_id":   session_id,
                "segment_id":   segment_id,
                "recording_sid": recording_sid,
                "url":          serve_url,
                "duration_ms":  duration_s * 1000,
                "channels":     2,
                "size_bytes":   len(audio_bytes),
            }
            await self._publish_inbound(
                self._normalize_text(
                    text       = json.dumps(event),
                    session_id = session_id,
                    contact_id = "system",
                    tenant_id  = s.tenant_id,
                    content_type = "text",
                )
            )
        except Exception as exc:
            logger.error(
                "voice recording download failed (rec=%s): %s", recording_sid, exc
            )

    # ── TTS endpoint helper ───────────────────────────────────────────────────

    async def get_tts_twiml(self, tts_id: str) -> str | None:
        """
        Called by /voice/tts/{tts_id} FastAPI route.
        Returns TwiML <Say> string or None (404) if not found.
        """
        raw = await self._redis.get(f"channel:voice:tts:{tts_id}")
        if not raw:
            return None
        data  = json.loads(raw)
        voice = data.get("voice", self._settings.voice_tts_voice_id)
        return self._voice.generate_tts_twiml(data["text"], voice)

    # ── Media WebSocket (WS /voice/media) ────────────────────────────────────

    async def handle_media_ws(self, ws: WebSocket) -> None:
        """
        FastAPI WebSocket handler for Twilio Media Streams.
        Called once per active call — runs until the call ends.

        Flow:
          1. Receive Twilio "start" event → extract session_id
          2. Start STT loop (audio chunks → Deepgram → Kafka)
          3. Start collect loop (DTMF / STT result → menu state machine)
          4. Start stream watcher (routing.assigned → segment recording)
          5. On "stop" event → cleanup
        """
        await ws.accept()
        session_id: str | None = None
        call_sid:   str | None = None

        # Audio queue fed by the receiver loop, consumed by the STT coroutine
        audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=100)
        # STT results from Deepgram
        stt_queue:   asyncio.Queue[STTResult | None] = asyncio.Queue()

        async def _receive_loop() -> None:
            nonlocal session_id, call_sid
            try:
                async for raw in ws.iter_text():
                    msg = json.loads(raw)
                    event = msg.get("event", "")

                    if event == "start":
                        start = msg.get("start", {})
                        call_sid = start.get("callSid", "")
                        custom   = start.get("customParameters", {})
                        session_id = custom.get("session_id", "")
                        if not session_id and call_sid:
                            # Fallback: look up session by call SID
                            session_id = await self._redis.get(
                                f"channel:voice:{call_sid}:session"
                            ) or ""
                        logger.info(
                            "voice media WS started: call=%s session=%s",
                            call_sid, session_id,
                        )

                    elif event == "media":
                        payload = msg.get("media", {}).get("payload", "")
                        if payload and session_id:
                            import base64 as _b64
                            chunk = _b64.b64decode(payload)
                            try:
                                audio_queue.put_nowait(chunk)
                            except asyncio.QueueFull:
                                pass  # drop chunk — backpressure

                    elif event == "dtmf":
                        digit = msg.get("dtmf", {}).get("digit", "")
                        if digit and session_id:
                            await self._handle_dtmf(session_id, digit)

                    elif event == "stop":
                        logger.info(
                            "voice media WS stopped: call=%s session=%s",
                            call_sid, session_id,
                        )
                        break
            except Exception as exc:
                logger.debug("voice media WS receive loop ended: %s", exc)
            finally:
                await audio_queue.put(None)   # signal STT to stop
                await stt_queue.put(None)     # signal collect loop to stop

        async def _audio_source() -> None:
            """Async generator yielding audio chunks from the queue."""
            while True:
                chunk = await audio_queue.get()
                if chunk is None:
                    return
                yield chunk

        async def _stt_loop() -> None:
            """Consume audio → STT → publish transcripts → Kafka."""
            if not session_id:
                await asyncio.sleep(0.5)  # wait for session_id from start event
            s = self._settings
            try:
                async for result in self._stt.stream(
                    audio_chunks = _audio_source(),
                    sample_rate  = 8000,
                    language     = s.voice_stt_language,
                ):
                    if not result.is_final:
                        continue  # skip partial transcripts
                    if not session_id:
                        continue

                    contact_id = await self._get_contact_id(session_id)
                    event = self._normalize_text(
                        text         = result.transcript,
                        session_id   = session_id,
                        contact_id   = contact_id or "",
                        tenant_id    = s.tenant_id,
                        content_type = "audio_transcript",
                    )
                    await self._publish_inbound(event)
                    logger.debug(
                        "voice STT: session=%s → %r", session_id, result.transcript
                    )

                    # Feed STT result to collect loop
                    try:
                        stt_queue.put_nowait(result)
                    except asyncio.QueueFull:
                        pass
            except Exception as exc:
                logger.debug("voice STT loop ended: %s", exc)
            finally:
                await stt_queue.put(None)

        async def _keepalive() -> None:
            """Renew session Redis TTL every _KEEPALIVE_INTERVAL seconds."""
            while True:
                await asyncio.sleep(_KEEPALIVE_INTERVAL)
                if not session_id:
                    continue
                try:
                    await self._redis.expire(
                        f"channel:voice:{session_id}:conference_sid", _SESSION_TTL
                    )
                except Exception:
                    pass

        async def _stream_watcher() -> None:
            """
            Watch session:{id}:stream for routing.assigned events.
            Starts / stops CPaaS recording based on pool.voice_recording.
            """
            if not session_id:
                return
            stream_key = f"session:{session_id}:stream"
            last_id    = "0-0"
            while True:
                try:
                    results = await self._redis.xread(
                        {stream_key: last_id}, count=10, block=5000
                    )
                    if not results:
                        continue
                    for _, entries in results:
                        for entry_id, fields in entries:
                            last_id = entry_id
                            event_type = fields.get("type", "")
                            if event_type == "routing.assigned":
                                await self._on_routing_assigned(
                                    session_id, fields
                                )
                            elif event_type in ("agent_done", "session.closed"):
                                await self._stop_all_recordings(session_id)
                except Exception as exc:
                    logger.debug("voice stream watcher error: %s", exc)
                    await asyncio.sleep(1.0)

        # Run all loops concurrently — cancel survivors when the first exits
        tasks = [
            asyncio.create_task(_receive_loop(), name="voice-receive"),
            asyncio.create_task(_stt_loop(),     name="voice-stt"),
            asyncio.create_task(_keepalive(),    name="voice-keepalive"),
            asyncio.create_task(_stream_watcher(), name="voice-stream-watcher"),
        ]
        done, pending = await asyncio.wait(
            tasks, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        logger.info(
            "voice media WS session complete: call=%s session=%s", call_sid, session_id
        )

    # ── DTMF / collect state machine ─────────────────────────────────────────

    async def _handle_dtmf(self, session_id: str, digit: str) -> None:
        """Process a DTMF digit against the active collect state (if any)."""
        collect_key = f"channel:voice:{session_id}:collect"
        raw = await self._redis.get(collect_key)
        if not raw:
            logger.debug("DTMF %r received but no active collect for %s", digit, session_id)
            return

        state      = json.loads(raw)
        input_mode = state.get("input_mode", "dtmf")
        if input_mode == "voice":
            # In voice-only mode, ignore DTMF
            return

        await self._process_collect_input(session_id, collect_key, state, digit)

    async def _handle_stt_result(self, session_id: str, result: STTResult) -> None:
        """Process an STT transcript against the active collect state (if any)."""
        collect_key = f"channel:voice:{session_id}:collect"
        raw = await self._redis.get(collect_key)
        if not raw:
            return

        state      = json.loads(raw)
        input_mode = state.get("input_mode", "dtmf")
        if input_mode == "dtmf":
            # In DTMF-only mode, ignore voice input
            return

        await self._process_collect_input(
            session_id, collect_key, state, result.transcript
        )

    async def _process_collect_input(
        self,
        session_id:  str,
        collect_key: str,
        state:       dict,
        value:       str,
    ) -> None:
        """
        Advance the sequential collect state machine with a new input value.
        When all fields are collected, publish a menu_result NormalizedInboundEvent.
        """
        s             = self._settings
        fields        = state["fields"]
        current_index = state["current_index"]
        answers       = state.get("answers", {})
        menu_id       = state["menu_id"]

        field = fields[current_index]

        # Validate for option lists
        options = field.get("options", [])
        if options:
            # Try to match digit or spoken option
            matched = _match_option(value, options)
            if matched is None:
                # Re-send the question
                option_list = "\n".join(
                    f"{i+1}. {opt['label']}" for i, opt in enumerate(options)
                )
                prompt = f"Opção inválida. Por favor, pressione ou diga o número.\n{option_list}"
                await self._deliver_tts(session_id, prompt)
                return
            answers[field["name"]] = matched
        else:
            answers[field["name"]] = value

        next_index = current_index + 1

        if next_index >= len(fields):
            # Collect complete — publish menu result and clear state
            await self._redis.delete(collect_key)
            contact_id = await self._get_contact_id(session_id)
            event = self._normalize_menu_result(
                menu_id    = menu_id,
                interaction = "submit",
                result      = answers,
                session_id  = session_id,
                contact_id  = contact_id or "",
                tenant_id   = s.tenant_id,
            )
            await self._publish_inbound(event)
            logger.info(
                "voice collect complete: session=%s menu=%s answers=%s",
                session_id, menu_id, list(answers.keys()),
            )
        else:
            # Advance to next field
            state["current_index"] = next_index
            state["answers"]       = answers
            await self._redis.set(
                collect_key, json.dumps(state), ex=_COLLECT_TTL
            )
            # Prompt for next field
            next_field = fields[next_index]
            await self._deliver_tts(session_id, next_field["prompt"])

    # ── Outbound delivery ────────────────────────────────────────────────────

    # ── ChannelAdapter ABC implementation ────────────────────────────────────

    async def deliver_text(self, payload: dict) -> None:
        """ABC: synthesize text via TTS and play it in the active call."""
        session_id = payload.get("session_id", "")
        if not session_id:
            return
        text = (
            payload.get("text")
            or payload.get("content", {}).get("text", "")
            or ""
        )
        if text:
            await self._deliver_tts(session_id, text)

    async def deliver_menu(self, payload: dict) -> None:
        """ABC: deliver interactive menu / collect prompt via TTS + DTMF."""
        session_id = payload.get("session_id", "")
        if session_id:
            await self._deliver_interaction(session_id, payload)

    async def deliver_typing(self, payload: dict) -> None:
        """ABC: no-op — voice channel has no typing indicator concept."""

    async def deliver_session_closed(self, payload: dict) -> None:
        """ABC: hang up the CPaaS call when the platform closes the session."""
        session_id = payload.get("session_id", "")
        if session_id:
            await self._deliver_session_closed(session_id, payload)

    async def deliver_outbound(self, event_type: str, payload: dict) -> None:
        """
        Route outbound Kafka event to the correct delivery method.
        Called by OutboundConsumer for channel="voice" messages.
        """
        session_id = payload.get("session_id", "")
        if not session_id:
            logger.warning("voice deliver_outbound: missing session_id")
            return

        if event_type in ("notify", "message.text"):
            text = (
                payload.get("text")
                or payload.get("content", {}).get("text", "")
                or ""
            )
            if text:
                await self._deliver_tts(session_id, text)

        elif event_type == "interaction.request":
            await self._deliver_interaction(session_id, payload)

        elif event_type == "session.closed":
            await self._deliver_session_closed(session_id, payload)

        elif event_type in ("typing.start", "typing.stop"):
            pass  # no-op for voice

        else:
            logger.debug(
                "voice deliver_outbound: unhandled event_type=%s session=%s",
                event_type, session_id,
            )

    async def _deliver_tts(self, session_id: str, text: str) -> None:
        """
        Synthesize text and play it in the CPaaS conference.
        TwilioSayTTS: stores text in Redis → conference.announce_url → Twilio fetches TTS endpoint.
        DeepgramAura: synthesize bytes → serve URL → conference.announce_url.
        """
        conference_sid = await self._redis.get(
            f"channel:voice:{session_id}:conference_sid"
        )
        if not conference_sid:
            logger.debug(
                "voice TTS: no conference_sid for session=%s — buffering skipped",
                session_id,
            )
            return

        audio_bytes = await self._tts.synthesize(text)

        if audio_bytes is None:
            # TwilioSayTTS path — store text in Redis, let Twilio fetch it
            tts_id   = str(uuid.uuid4()).replace("-", "")
            tts_key  = f"channel:voice:tts:{tts_id}"
            voice    = self._settings.voice_tts_voice_id
            await self._redis.set(
                tts_key,
                json.dumps({"text": text, "voice": voice}),
                ex=_TTS_TEXT_TTL,
            )
            tts_url = f"{self._settings.voice_webhook_host}/voice/tts/{tts_id}"
            await self._voice.announce_tts(conference_sid, tts_url)
        else:
            # Deepgram Aura path — store audio bytes and serve URL
            tts_id  = str(uuid.uuid4()).replace("-", "")
            tts_key = f"channel:voice:tts_audio:{tts_id}"
            await self._redis.set(tts_key, audio_bytes, ex=_TTS_TEXT_TTL)
            tts_url = f"{self._settings.voice_webhook_host}/voice/tts-audio/{tts_id}"
            await self._voice.announce_tts(conference_sid, tts_url)

    async def get_tts_audio(self, tts_id: str) -> bytes | None:
        """Return stored Deepgram Aura audio bytes for /voice/tts-audio/{tts_id}."""
        raw = await self._redis.get(f"channel:voice:tts_audio:{tts_id}")
        return raw

    async def _deliver_interaction(self, session_id: str, payload: dict) -> None:
        """
        Deliver a menu / collect interaction via TTS + collect state machine.
        """
        menu       = payload.get("menu", {}) or payload.get("payload", {})
        menu_id    = menu.get("id", str(uuid.uuid4()))
        prompt     = menu.get("text", menu.get("body", ""))
        fields     = menu.get("fields", [])
        options    = menu.get("options", [])
        input_mode = menu.get("input_mode", "dtmf")

        # Build field list from either fields (form) or options (menu)
        if not fields and options:
            fields = [{"name": "choice", "prompt": prompt, "options": options}]
        elif not fields:
            fields = [{"name": "input", "prompt": prompt}]

        collect_state = {
            "menu_id":       menu_id,
            "fields":        fields,
            "current_index": 0,
            "answers":       {},
            "input_mode":    input_mode,
        }
        collect_key = f"channel:voice:{session_id}:collect"
        await self._redis.set(collect_key, json.dumps(collect_state), ex=_COLLECT_TTL)

        # Deliver TTS prompt for first field
        first_prompt = _build_voice_prompt(fields[0], options)
        await self._deliver_tts(session_id, first_prompt)

    async def _deliver_session_closed(
        self, session_id: str, payload: dict
    ) -> None:
        """Hang up the CPaaS call when the platform closes the session."""
        call_sid = await self._redis.get(
            f"channel:voice:{session_id}:call_sid"
        )
        if call_sid:
            await self._voice.hangup(call_sid)
            logger.info(
                "voice session_closed: hung up call=%s session=%s",
                call_sid, session_id,
            )

    # ── Segment recording (§ 13) ──────────────────────────────────────────────

    async def _on_routing_assigned(
        self, session_id: str, fields: dict
    ) -> None:
        """
        Triggered when routing.assigned arrives in the session stream.
        Starts segment recording if pool.voice_recording == true.
        """
        segment_id   = fields.get("segment_id", "")
        pool_config  = json.loads(fields.get("pool", "{}") or "{}")
        should_record = pool_config.get("voice_recording", False)

        if not should_record or not segment_id:
            return

        # Guard: already recording this segment
        rec_key = f"channel:voice:{session_id}:rec_id:{segment_id}"
        if await self._redis.exists(rec_key):
            return

        await self._announce_and_start_recording(session_id, segment_id)

    async def _announce_and_start_recording(
        self, session_id: str, segment_id: str
    ) -> None:
        """
        Announce recording notice (TTS) then start CPaaS conference recording.
        Guard against double-announcement via Redis flag.
        """
        announced_key = (
            f"channel:voice:{session_id}:recording_announced:{segment_id}"
        )
        if await self._redis.exists(announced_key):
            return

        # Opt-out guard
        opt_out_key = f"channel:voice:{session_id}:recording_opt_out"
        if await self._redis.exists(opt_out_key):
            logger.info(
                "voice recording: opt-out active — skipping segment=%s", segment_id
            )
            return

        notice = await self._get_config("voice.recording_notice")
        await self._deliver_tts(
            session_id, notice or self._settings.voice_default_recording_notice
        )
        await asyncio.sleep(1.2)  # natural pause after notice

        conference_sid = await self._redis.get(
            f"channel:voice:{session_id}:conference_sid"
        )
        if not conference_sid:
            logger.warning(
                "voice recording: no conference_sid for session=%s — cannot start recording",
                session_id,
            )
            return

        recording_sid = await self._voice.start_recording(
            conference_sid = conference_sid,
            dual_channel   = True,
            metadata       = {"segment_id": segment_id},
        )

        self._active_recordings[segment_id] = recording_sid
        rec_key = f"channel:voice:{session_id}:rec_id:{segment_id}"
        await self._redis.set(rec_key, recording_sid, ex=_SESSION_TTL)
        await self._redis.set(announced_key, "1", ex=_SESSION_TTL)

        logger.info(
            "voice recording started: session=%s segment=%s rec=%s",
            session_id, segment_id, recording_sid,
        )

    async def _stop_all_recordings(self, session_id: str) -> None:
        """Stop all active recordings for a session (on agent_done / session.closed)."""
        for segment_id, recording_sid in list(self._active_recordings.items()):
            await self._voice.stop_recording(recording_sid)
            del self._active_recordings[segment_id]
            logger.info(
                "voice recording stopped: session=%s segment=%s rec=%s",
                session_id, segment_id, recording_sid,
            )

    # ── Collect events — outbound voice dial (§ 9.10) ────────────────────────

    async def handle_collect_event(self, event: dict) -> None:
        """
        Process a collect.events Kafka message for channel="voice".
        Creates an outbound call and stores the pending_collect Redis key
        for correlation when the customer answers.
        """
        s             = self._settings
        target        = event.get("target", "")
        pool_id       = event.get("pool_id", s.voice_default_pool_id)
        collect_token = event.get("collect_token", "")
        trunk_id      = event.get("trunk_id", "")
        journey_id    = event.get("journey_id")

        if not target:
            logger.warning("voice collect event: missing target — skipping")
            return

        # The callback URL for the outbound call is the same inbound webhook
        callback_url = f"{s.voice_webhook_host}/webhooks/voice/inbound"

        try:
            call_sid = await self._voice.create_call(
                to           = target,
                from_        = s.voice_from_number,
                callback_url = callback_url,
            )
        except Exception as exc:
            logger.error(
                "voice collect: create_call failed (target=%s): %s", target, exc
            )
            return

        pending = {
            "collect_token": collect_token,
            "pool_id":       pool_id,
            "journey_id":    journey_id,
        }
        await self._redis.set(
            f"channel:voice:pending_collect:{call_sid}",
            json.dumps(pending),
            ex=_PENDING_CALL_TTL,
        )

        logger.info(
            "voice outbound collect: call=%s target=%s pool=%s token=%s",
            call_sid, target, pool_id, collect_token,
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _get_contact_id(self, session_id: str) -> str | None:
        """Reverse-lookup contact_id from session_id via Redis."""
        # Not stored directly; check call_sid → from_number via the session key
        # In practice the contact_id was set when the session was opened.
        # For now, return None — caller handles gracefully.
        return None

    async def _get_config(self, key: str) -> str | None:
        """Fetch a tenant config value from Config API / Redis."""
        try:
            tenant_key = f"{self._settings.tenant_id}:config:{key}"
            return await self._redis.get(tenant_key)
        except Exception:
            return None


# ── Module-level helpers ──────────────────────────────────────────────────────


def _normalize_e164(number: str) -> str:
    """Normalize a phone number to E.164 format (keep + and digits only)."""
    digits = "".join(c for c in number if c.isdigit() or c == "+")
    return digits if digits.startswith("+") else f"+{digits}"


def _match_option(value: str, options: list[dict]) -> str | None:
    """
    Match a DTMF digit or spoken value against a list of options.
    Returns the option value/id or None if no match.
    """
    value = value.strip()
    # Try numeric index (DTMF "1" → options[0])
    if value.isdigit():
        idx = int(value) - 1
        if 0 <= idx < len(options):
            return options[idx].get("id") or options[idx].get("value") or value
    # Try exact label match (case-insensitive)
    for opt in options:
        label = opt.get("label", "")
        if label.lower() == value.lower():
            return opt.get("id") or opt.get("value") or label
    return None


def _build_voice_prompt(field: dict, options: list[dict]) -> str:
    """
    Build a TTS prompt for a collect field.
    For option lists, append numbered choices.
    """
    prompt = field.get("prompt", "")
    field_options = field.get("options", options)
    if field_options:
        numbered = "\n".join(
            f"{i+1}. {opt.get('label', opt.get('id', str(i+1)))}"
            for i, opt in enumerate(field_options)
        )
        prompt = f"{prompt}\n{numbered}"
    return prompt
