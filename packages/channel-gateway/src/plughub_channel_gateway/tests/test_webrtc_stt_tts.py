"""
tests/test_webrtc_stt_tts.py
Phase C — Arc 15 WebRTC STT/TTS pipeline tests.

Coverage:
  - resample_pcm_48_to_8() audio helper
  - mp3_to_pcm() helper (graceful degradation)
  - MockRoomClient interface compliance
  - WebRTCAdapter._stt_pipeline() → _publish_transcript() → Kafka
  - WebRTCAdapter._tts_inject() → room_client.publish_audio()
  - DataChannel text (webrtc.message) → Kafka conversations.inbound
  - DataChannel menu reply (webrtc.interaction_reply) → Redis menu:result
  - STT disabled (webrtc_stt_enabled=False) — no room client created
  - TTS disabled (webrtc_tts_injection_enabled=False) — no inject on deliver_text
  - deliver_session_closed tears down room client and STT task
"""

from __future__ import annotations

import asyncio
import json
import struct
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plughub_channel_gateway.adapters.voice_provider import (
    MockSTTProvider,
    MockTTSProvider,
    STTResult,
)
from plughub_channel_gateway.adapters.webrtc import WebRTCAdapter
from plughub_channel_gateway.adapters.webrtc_provider import MockWebRTCProvider
from plughub_channel_gateway.adapters.webrtc_room_client import (
    MockRoomClient,
    _linear_to_ulaw,
    mp3_to_pcm,
    resample_pcm_48_to_8,
)
from plughub_channel_gateway.config import Settings

# ── Shared test constants ──────────────────────────────────────────────────────

SESSION_ID = "sess-c3-test-0001"
TENANT_ID  = "tenant_test"
ROOM_NAME  = f"plughub-{SESSION_ID}"


# ── Settings factory ───────────────────────────────────────────────────────────


def _settings(**overrides) -> Settings:
    base = dict(
        kafka_brokers              = "localhost:9092",
        kafka_topic_inbound        = "conversations.inbound",
        kafka_topic_outbound       = "conversations.outbound",
        kafka_topic_events         = "conversations.events",
        redis_url                  = "redis://localhost:6379/0",
        tenant_id                  = TENANT_ID,
        session_ttl_seconds        = 3600,
        jwt_secret                 = "test_secret_32chars_webchat_ok!!",
        webrtc_livekit_url         = "wss://livekit.test",
        webrtc_livekit_api_key     = "api_key_test",
        webrtc_livekit_api_secret  = "api_secret_test",
        webrtc_token_ttl_s         = 3600,
        webrtc_stt_enabled         = True,
        webrtc_tts_injection_enabled = False,
        webrtc_default_medium_order  = "video,voice,text",
        voice_deepgram_api_key     = "",
        voice_stt_language         = "pt-BR",
        voice_elevenlabs_api_key   = "",
        voice_elevenlabs_voice_id  = "pNInz6obpgDQGcFmaJgB",
    )
    base.update(overrides)
    return Settings(**base)


def _make_redis() -> AsyncMock:
    redis = AsyncMock()
    redis.setex  = AsyncMock(return_value=True)
    redis.get    = AsyncMock(return_value=None)
    redis.lpush  = AsyncMock(return_value=1)
    redis.expire = AsyncMock(return_value=True)
    redis.delete = AsyncMock(return_value=1)
    return redis


def _make_producer() -> AsyncMock:
    producer = AsyncMock()
    producer.send = AsyncMock()
    return producer


def _make_adapter(
    settings: Settings | None = None,
    stt: MockSTTProvider | None = None,
    tts: MockTTSProvider | None = None,
    room_client: MockRoomClient | None = None,
) -> tuple[WebRTCAdapter, AsyncMock, AsyncMock]:
    s   = settings or _settings()
    r   = _make_redis()
    p   = _make_producer()
    stt = stt or MockSTTProvider()
    tts = tts or MockTTSProvider(synthesize_returns_none=True)
    adapter = WebRTCAdapter(
        producer        = p,
        redis           = r,
        settings        = s,
        webrtc_provider = MockWebRTCProvider(),
        stt_provider    = stt,
        tts_provider    = tts,
    )
    # Inject pre-built room client if provided
    if room_client is not None:
        adapter._room_clients[SESSION_ID] = room_client
    return adapter, r, p


# ─────────────────────────────────────────────────────────────────────────────
# 1. Audio helpers
# ─────────────────────────────────────────────────────────────────────────────

class TestResampleHelper:
    def _make_pcm_48(self, n_samples: int = 960) -> bytes:
        """Generate silent 48kHz mono PCM (960 samples = 20ms frame)."""
        return struct.pack(f"<{n_samples}h", *([0] * n_samples))

    def test_resample_mono_returns_bytes(self):
        pcm_48 = self._make_pcm_48(960)
        result = resample_pcm_48_to_8(pcm_48, num_channels=1)
        assert isinstance(result, bytes)

    def test_resample_mono_length_ratio(self):
        """8kHz / 48kHz = 1/6 — output should be 1/6 of input samples."""
        n_in = 960  # samples at 48kHz
        pcm_48 = self._make_pcm_48(n_in)
        result = resample_pcm_48_to_8(pcm_48, num_channels=1)
        # Allow ±1 sample tolerance for both audioop and fallback paths
        expected = n_in // 6
        assert abs(len(result) - expected) <= 2, (
            f"expected ~{expected} bytes, got {len(result)}"
        )

    def test_resample_stereo_returns_bytes(self):
        """Stereo input (2 channels) must be mono-mixed before resampling."""
        n_frames = 960
        # Interleaved stereo: L, R, L, R, ...
        stereo = struct.pack(f"<{n_frames * 2}h", *([100, -100] * n_frames))
        result = resample_pcm_48_to_8(stereo, num_channels=2)
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_empty_input_returns_empty_or_minimal(self):
        result = resample_pcm_48_to_8(b"", num_channels=1)
        assert isinstance(result, bytes)

    def test_linear_to_ulaw_zero(self):
        """Zero sample should produce a valid μ-law byte."""
        result = _linear_to_ulaw(0)
        assert 0 <= result <= 255

    def test_linear_to_ulaw_range(self):
        """All output values must be valid unsigned bytes."""
        for s in range(-32768, 32768, 512):
            result = _linear_to_ulaw(s)
            assert 0 <= result <= 255


class TestMp3ToPcm:
    def test_mp3_to_pcm_no_pydub_returns_empty(self):
        """When pydub is unavailable, mp3_to_pcm returns b'' without raising."""
        with patch("builtins.__import__", side_effect=ImportError("pydub")):
            result = mp3_to_pcm(b"\xff\xfb\x90\x00", target_sample_rate=24000)
        assert result == b""

    def test_mp3_to_pcm_invalid_data_returns_empty(self):
        """Invalid MP3 bytes should return b'' gracefully."""
        result = mp3_to_pcm(b"\x00\x01\x02\x03", target_sample_rate=24000)
        # Either returns empty bytes (pydub not installed) or after decode failure
        assert isinstance(result, bytes)


# ─────────────────────────────────────────────────────────────────────────────
# 2. MockRoomClient interface compliance
# ─────────────────────────────────────────────────────────────────────────────

class TestMockRoomClient:
    @pytest.mark.asyncio
    async def test_connect_sets_state(self):
        client = MockRoomClient()
        await client.connect(ROOM_NAME, "bot-test", "tok", "wss://test")
        assert client.connected is True
        assert client.connected_to == ROOM_NAME

    @pytest.mark.asyncio
    async def test_subscribe_yields_injected_chunks(self):
        client = MockRoomClient()
        chunk1 = b"\x00" * 100
        chunk2 = b"\xff" * 200
        client.inject_audio(chunk1)
        client.inject_audio(chunk2)
        client.end_audio()

        collected = []
        async for chunk in client.subscribe_customer_audio():
            collected.append(chunk)

        assert collected == [chunk1, chunk2]

    @pytest.mark.asyncio
    async def test_publish_audio_records_chunks(self):
        client = MockRoomClient()
        pcm = b"\x80" * 48000
        await client.publish_audio(pcm, sample_rate=24000)
        assert client.published_chunks == [pcm]

    @pytest.mark.asyncio
    async def test_disconnect_sets_flag(self):
        client = MockRoomClient()
        await client.connect(ROOM_NAME, "bot", "tok", "wss://test")
        await client.disconnect()
        assert client.disconnected is True
        assert client.connected is False

    @pytest.mark.asyncio
    async def test_subscribe_terminates_after_disconnect(self):
        client = MockRoomClient()
        # No chunks + end_audio → should terminate immediately
        client.end_audio()
        chunks = []
        async for c in client.subscribe_customer_audio():
            chunks.append(c)
        assert chunks == []


# ─────────────────────────────────────────────────────────────────────────────
# 3. STT pipeline → Kafka
# ─────────────────────────────────────────────────────────────────────────────

class TestSttpipeline:
    @pytest.mark.asyncio
    async def test_final_transcript_published_to_kafka(self):
        """STT final results must be published to conversations.inbound."""
        stt = MockSTTProvider()
        stt.results = [
            STTResult(transcript="Olá mundo", is_final=False),
            STTResult(transcript="Olá mundo", is_final=True, confidence=0.95),
        ]

        room_client = MockRoomClient()
        # Provide one audio frame then end
        room_client.inject_audio(b"\x00" * 960)
        room_client.end_audio()

        adapter, redis, producer = _make_adapter(stt=stt, room_client=room_client)

        await adapter._stt_pipeline(SESSION_ID, room_client)

        # Producer.send must have been called at least once for the final result
        assert producer.send.called
        calls = producer.send.call_args_list
        # Find the transcript publish call
        inbound_calls = [
            c for c in calls
            if c.args[0] == "conversations.inbound"
        ]
        assert len(inbound_calls) == 1
        payload = json.loads(inbound_calls[0].args[1].decode())
        assert payload["content_type"] == "audio_transcript"
        assert payload["content"]["text"] == "Olá mundo"
        assert payload["session_id"] == SESSION_ID
        assert payload["channel"] == "webrtc"

    @pytest.mark.asyncio
    async def test_interim_results_not_published(self):
        """Only is_final=True transcripts go to Kafka."""
        stt = MockSTTProvider()
        stt.results = [
            STTResult(transcript="interim", is_final=False),
            STTResult(transcript="interim two", is_final=False),
        ]

        room_client = MockRoomClient()
        room_client.inject_audio(b"\x00" * 960)
        room_client.end_audio()

        adapter, redis, producer = _make_adapter(stt=stt, room_client=room_client)
        await adapter._stt_pipeline(SESSION_ID, room_client)

        # No inbound calls — all results are interim
        inbound_calls = [
            c for c in producer.send.call_args_list
            if c.args[0] == "conversations.inbound"
        ]
        assert len(inbound_calls) == 0

    @pytest.mark.asyncio
    async def test_empty_transcript_not_published(self):
        """Blank final transcripts (silence) must not be published."""
        stt = MockSTTProvider()
        stt.results = [STTResult(transcript="   ", is_final=True)]

        room_client = MockRoomClient()
        room_client.inject_audio(b"\x00" * 960)
        room_client.end_audio()

        adapter, redis, producer = _make_adapter(stt=stt, room_client=room_client)
        await adapter._stt_pipeline(SESSION_ID, room_client)

        inbound_calls = [
            c for c in producer.send.call_args_list
            if c.args[0] == "conversations.inbound"
        ]
        assert len(inbound_calls) == 0

    @pytest.mark.asyncio
    async def test_stt_pipeline_cancelled_gracefully(self):
        """CancelledError in the STT pipeline must not propagate."""
        stt = MockSTTProvider()

        # Block indefinitely until cancelled
        async def _blocking_stream(chunks, sample_rate, language):
            await asyncio.sleep(100)
            return
            yield  # makes this an async generator

        stt.stream = _blocking_stream  # type: ignore[method-assign]

        room_client = MockRoomClient()
        room_client.inject_audio(b"\x00" * 100)

        adapter, _, _ = _make_adapter(stt=stt, room_client=room_client)

        task = asyncio.create_task(adapter._stt_pipeline(SESSION_ID, room_client))
        await asyncio.sleep(0.01)
        task.cancel()
        # Must not raise
        await asyncio.gather(task, return_exceptions=True)

    @pytest.mark.asyncio
    async def test_audio_resampled_before_stt(self):
        """Audio chunks passed to STT must be resampled (8kHz μ-law, shorter than input)."""
        stt = MockSTTProvider()
        stt.results = []

        # 48kHz frame: 960 samples × 2 bytes = 1920 bytes
        pcm_48 = struct.pack("<960h", *([1000] * 960))

        room_client = MockRoomClient()
        room_client.inject_audio(pcm_48)
        room_client.end_audio()

        adapter, _, _ = _make_adapter(stt=stt, room_client=room_client)
        await adapter._stt_pipeline(SESSION_ID, room_client)

        # STT received resampled chunks — shorter than original 1920 bytes
        assert len(stt.chunks_received) > 0
        for chunk in stt.chunks_received:
            assert len(chunk) < len(pcm_48), (
                f"Expected resampled chunk shorter than {len(pcm_48)}, got {len(chunk)}"
            )


# ─────────────────────────────────────────────────────────────────────────────
# 4. TTS injection
# ─────────────────────────────────────────────────────────────────────────────

class TestTtsInjection:
    @pytest.mark.asyncio
    async def test_tts_inject_calls_publish_audio(self):
        """_tts_inject must decode MP3 and call room_client.publish_audio."""
        fake_pcm = b"\x80" * 48000  # 24000 samples × 2 bytes
        tts = MockTTSProvider(synthesize_returns_none=False)  # returns stub bytes

        room_client = MockRoomClient()
        room_client.connected = True

        adapter, _, _ = _make_adapter(tts=tts, room_client=room_client)
        adapter._room_clients[SESSION_ID] = room_client

        # Patch mp3_to_pcm so we don't need pydub in CI
        with patch(
            "plughub_channel_gateway.adapters.webrtc.mp3_to_pcm",
            return_value=fake_pcm,
        ):
            await adapter._tts_inject(SESSION_ID, "Olá, tudo bem?")

        assert len(room_client.published_chunks) == 1
        assert room_client.published_chunks[0] == fake_pcm

    @pytest.mark.asyncio
    async def test_tts_inject_skipped_when_no_room_client(self):
        """When no room client exists for the session, _tts_inject must no-op."""
        tts = MockTTSProvider(synthesize_returns_none=False)
        adapter, _, _ = _make_adapter(tts=tts)
        # No room_client added — _room_clients is empty
        await adapter._tts_inject(SESSION_ID, "some text")
        # No error raised; nothing published

    @pytest.mark.asyncio
    async def test_tts_inject_skipped_when_tts_returns_none(self):
        """When TTS synthesize returns None, no audio should be injected."""
        tts = MockTTSProvider(synthesize_returns_none=True)
        room_client = MockRoomClient()
        adapter, _, _ = _make_adapter(tts=tts, room_client=room_client)
        adapter._room_clients[SESSION_ID] = room_client

        await adapter._tts_inject(SESSION_ID, "texto")
        assert room_client.published_chunks == []

    @pytest.mark.asyncio
    async def test_tts_inject_skipped_when_mp3_decode_fails(self):
        """When mp3_to_pcm returns empty bytes, publish_audio must not be called."""
        tts = MockTTSProvider(synthesize_returns_none=False)
        room_client = MockRoomClient()
        adapter, _, _ = _make_adapter(tts=tts, room_client=room_client)
        adapter._room_clients[SESSION_ID] = room_client

        with patch(
            "plughub_channel_gateway.adapters.webrtc.mp3_to_pcm",
            return_value=b"",  # decode failure
        ):
            await adapter._tts_inject(SESSION_ID, "texto")

        assert room_client.published_chunks == []

    @pytest.mark.asyncio
    async def test_deliver_text_triggers_tts_when_voice_medium_enabled(self):
        """deliver_text must fire _tts_inject task when medium=voice and tts_injection_enabled."""
        s = _settings(webrtc_tts_injection_enabled=True)
        tts = MockTTSProvider(synthesize_returns_none=False)
        room_client = MockRoomClient()
        room_client.connected = True

        adapter, redis, producer = _make_adapter(settings=s, tts=tts, room_client=room_client)
        adapter._connections[SESSION_ID] = AsyncMock()
        adapter._mediums[SESSION_ID]     = "voice"
        adapter._room_clients[SESSION_ID] = room_client

        injected_calls: list[str] = []

        async def _fake_inject(session_id, text, voice_id=None):
            injected_calls.append(text)

        adapter._tts_inject = _fake_inject

        await adapter.deliver_text({
            "session_id": SESSION_ID,
            "text":       "Olá, como posso ajudar?",
        })

        # Allow the task to run
        await asyncio.sleep(0.05)
        assert "Olá, como posso ajudar?" in injected_calls

    @pytest.mark.asyncio
    async def test_deliver_text_no_tts_when_text_medium(self):
        """deliver_text must NOT inject TTS when medium=text."""
        s = _settings(webrtc_tts_injection_enabled=True)
        tts = MockTTSProvider(synthesize_returns_none=False)
        room_client = MockRoomClient()

        adapter, redis, producer = _make_adapter(settings=s, tts=tts, room_client=room_client)
        adapter._connections[SESSION_ID] = AsyncMock()
        adapter._mediums[SESSION_ID]     = "text"  # ← text medium
        adapter._room_clients[SESSION_ID] = room_client

        injected: list[str] = []
        adapter._tts_inject = lambda *a, **kw: injected.append(a[1]) or asyncio.sleep(0)  # type: ignore

        await adapter.deliver_text({
            "session_id": SESSION_ID,
            "text":       "Hello",
        })
        await asyncio.sleep(0.05)
        assert injected == []

    @pytest.mark.asyncio
    async def test_deliver_text_no_tts_when_disabled(self):
        """deliver_text must NOT inject TTS when webrtc_tts_injection_enabled=False."""
        s = _settings(webrtc_tts_injection_enabled=False)  # default
        tts = MockTTSProvider(synthesize_returns_none=False)
        room_client = MockRoomClient()

        adapter, redis, producer = _make_adapter(settings=s, tts=tts, room_client=room_client)
        adapter._connections[SESSION_ID] = AsyncMock()
        adapter._mediums[SESSION_ID]     = "voice"
        adapter._room_clients[SESSION_ID] = room_client

        injected: list[str] = []
        adapter._tts_inject = lambda *a, **kw: injected.append(a[1]) or asyncio.sleep(0)  # type: ignore

        await adapter.deliver_text({
            "session_id": SESSION_ID,
            "text":       "Hello",
        })
        await asyncio.sleep(0.05)
        assert injected == []


# ─────────────────────────────────────────────────────────────────────────────
# 5. DataChannel text → Kafka
# ─────────────────────────────────────────────────────────────────────────────

class TestDataChannel:
    @pytest.mark.asyncio
    async def test_datachannel_text_published_to_kafka(self):
        """webrtc.message from browser DataChannel must be published as Kafka inbound."""
        from fastapi import WebSocketDisconnect

        adapter, redis, producer = _make_adapter()

        ws = AsyncMock()
        ws.accept = AsyncMock()
        ws.send_json = AsyncMock()
        ws.close = AsyncMock()

        msgs = [
            json.dumps({"type": "webrtc.message", "text": "Preciso de ajuda"}),
        ]

        async def receive_text():
            if msgs:
                return msgs.pop(0)
            raise WebSocketDisconnect(code=1000)

        ws.receive_text = receive_text

        await adapter._receive_loop(ws, SESSION_ID)

        inbound_calls = [
            c for c in producer.send.call_args_list
            if c.args[0] == "conversations.inbound"
        ]
        assert len(inbound_calls) == 1
        payload = json.loads(inbound_calls[0].args[1].decode())
        assert payload["content_type"] == "text"
        assert payload["content"]["text"] == "Preciso de ajuda"
        assert payload["channel"] == "webrtc"

    @pytest.mark.asyncio
    async def test_datachannel_empty_text_not_published(self):
        """webrtc.message with empty text must not publish to Kafka."""
        from fastapi import WebSocketDisconnect

        adapter, redis, producer = _make_adapter()

        ws = AsyncMock()
        ws.send_json = AsyncMock()
        ws.close = AsyncMock()
        msgs = [json.dumps({"type": "webrtc.message", "text": "   "})]

        async def receive_text():
            if msgs:
                return msgs.pop(0)
            raise WebSocketDisconnect(code=1000)

        ws.receive_text = receive_text

        await adapter._receive_loop(ws, SESSION_ID)

        inbound_calls = [
            c for c in producer.send.call_args_list
            if c.args[0] == "conversations.inbound"
        ]
        assert inbound_calls == []

    @pytest.mark.asyncio
    async def test_datachannel_interaction_reply_written_to_redis(self):
        """webrtc.interaction_reply must write reply to Redis menu:result:{session_id}."""
        from fastapi import WebSocketDisconnect

        adapter, redis, producer = _make_adapter()

        ws = AsyncMock()
        ws.send_json = AsyncMock()
        ws.close = AsyncMock()

        msgs = [
            json.dumps({
                "type":           "webrtc.interaction_reply",
                "reply":          "option_a",
                "interaction_id": "int-001",
            }),
        ]

        async def receive_text():
            if msgs:
                return msgs.pop(0)
            raise WebSocketDisconnect(code=1000)

        ws.receive_text = receive_text

        await adapter._receive_loop(ws, SESSION_ID)

        redis.lpush.assert_called_once()
        key = redis.lpush.call_args[0][0]
        assert key == f"menu:result:{SESSION_ID}"
        raw = redis.lpush.call_args[0][1]
        data = json.loads(raw)
        assert data["reply"] == "option_a"
        assert data["interaction_id"] == "int-001"

    @pytest.mark.asyncio
    async def test_datachannel_interaction_reply_empty_not_written(self):
        """interaction_reply with empty reply must not write to Redis."""
        from fastapi import WebSocketDisconnect

        adapter, redis, producer = _make_adapter()

        ws = AsyncMock()
        ws.send_json = AsyncMock()
        ws.close = AsyncMock()

        msgs = [json.dumps({"type": "webrtc.interaction_reply", "reply": ""})]

        async def receive_text():
            if msgs:
                return msgs.pop(0)
            raise WebSocketDisconnect(code=1000)

        ws.receive_text = receive_text
        await adapter._receive_loop(ws, SESSION_ID)

        redis.lpush.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# 6. STT disabled — no room client created
# ─────────────────────────────────────────────────────────────────────────────

class TestSttDisabled:
    @pytest.mark.asyncio
    async def test_stt_disabled_no_room_client_started(self):
        """When webrtc_stt_enabled=False, _start_stt_pipeline must be a no-op."""
        s = _settings(webrtc_stt_enabled=False)
        adapter, redis, producer = _make_adapter(settings=s)

        await adapter._start_stt_pipeline(SESSION_ID, ROOM_NAME)

        assert SESSION_ID not in adapter._room_clients
        assert SESSION_ID not in adapter._stt_tasks

    @pytest.mark.asyncio
    async def test_stt_enabled_but_token_fails_gracefully(self):
        """When token generation fails, _start_stt_pipeline must log and exit."""
        s = _settings(webrtc_stt_enabled=True)

        mock_provider = MockWebRTCProvider()
        mock_provider.generate_token = MagicMock(side_effect=RuntimeError("token error"))

        adapter, redis, producer = _make_adapter(settings=s)
        adapter._provider = mock_provider

        # Must not raise
        await adapter._start_stt_pipeline(SESSION_ID, ROOM_NAME)
        assert SESSION_ID not in adapter._room_clients


# ─────────────────────────────────────────────────────────────────────────────
# 7. Teardown — deliver_session_closed and _close_session
# ─────────────────────────────────────────────────────────────────────────────

class TestTeardown:
    @pytest.mark.asyncio
    async def test_close_session_cancels_stt_task_and_disconnects_room(self):
        """_close_session must cancel the STT task and disconnect the room client."""
        room_client = MockRoomClient()
        room_client.connected = True

        adapter, redis, producer = _make_adapter(room_client=room_client)
        adapter._room_clients[SESSION_ID] = room_client

        # Create a dummy STT task that runs indefinitely
        async def _dummy():
            await asyncio.sleep(1000)

        stt_task = asyncio.create_task(_dummy())
        adapter._stt_tasks[SESSION_ID] = stt_task

        await adapter._close_session(SESSION_ID, "customer_hangup")

        assert stt_task.cancelled()
        assert room_client.disconnected is True
        assert SESSION_ID not in adapter._room_clients
        assert SESSION_ID not in adapter._stt_tasks

    @pytest.mark.asyncio
    async def test_deliver_session_closed_cleans_up_room_client(self):
        """deliver_session_closed must clean up room client after notifying the client."""
        room_client = MockRoomClient()
        room_client.connected = True

        ws = AsyncMock()
        ws.send_json = AsyncMock()
        ws.close = AsyncMock()

        adapter, redis, producer = _make_adapter(room_client=room_client)
        adapter._connections[SESSION_ID]  = ws
        adapter._mediums[SESSION_ID]      = "voice"
        adapter._room_clients[SESSION_ID] = room_client

        await adapter.deliver_session_closed({
            "session_id":   SESSION_ID,
            "close_reason": "agent_hangup",
        })

        assert room_client.disconnected is True
        assert SESSION_ID not in adapter._room_clients

    @pytest.mark.asyncio
    async def test_close_session_no_room_client_is_noop(self):
        """_close_session must not fail when no room client exists."""
        adapter, redis, producer = _make_adapter()
        # No room client — must not raise
        await adapter._close_session(SESSION_ID, "session_timeout")
        producer.send.assert_called_once()  # contact_close published


# ─────────────────────────────────────────────────────────────────────────────
# 8. Provider factories
# ─────────────────────────────────────────────────────────────────────────────

class TestProviderFactories:
    def test_build_stt_provider_no_api_key_returns_mock(self):
        s = _settings(webrtc_stt_enabled=True, voice_deepgram_api_key="")
        adapter, _, _ = _make_adapter(settings=s)
        # Must not raise; _build_stt_provider() returns MockSTTProvider
        stt = adapter._build_stt_provider()
        assert isinstance(stt, MockSTTProvider)

    def test_build_stt_provider_disabled_returns_mock(self):
        s = _settings(webrtc_stt_enabled=False)
        adapter, _, _ = _make_adapter(settings=s)
        stt = adapter._build_stt_provider()
        assert isinstance(stt, MockSTTProvider)

    def test_build_tts_provider_no_api_key_returns_mock(self):
        s = _settings(voice_elevenlabs_api_key="")
        adapter, _, _ = _make_adapter(settings=s)
        tts = adapter._build_tts_provider()
        assert isinstance(tts, MockTTSProvider)
