"""
tests/test_webrtc_stt_tts.py
Phase C — Arc 15 WebRTC STT/TTS pipeline tests.

Coverage:
  - resample_pcm_48_to_8() audio helper
  - mp3_to_pcm() helper (graceful degradation)
  - MockRoomClient interface compliance
  - WebRTCAdapter._stt_pipeline() → _publish_transcript() → Kafka
  - Fala do agente: quem fala, frases em ordem, prompt de menu, barge-in (VOZ-05 fatia 3)
  - DataChannel text (webrtc.message) → Kafka conversations.inbound
  - Menu reply (webrtc.menu_submit) → `menu_result` + histórico redigido; coleta mascarada
    descarta fala e texto livre (VOZ-05, fatia A)
  - STT disabled (webrtc_stt_enabled=False) — no room client created
  - deliver_session_closed tears down room client and STT task
"""

from __future__ import annotations

import asyncio
import json
import logging
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
        voice_deepgram_api_key     = "",
        voice_stt_language         = "pt-BR",
        voice_elevenlabs_api_key   = "",
        voice_elevenlabs_voice_id  = "pNInz6obpgDQGcFmaJgB",
        # Explícitos (VOZ-05): o container do demo exporta PLUGHUB_WEBRTC_SPEECH_PROVIDER=speaches,
        # e sem isto os testes de "sem provedor" passavam fora do container e reprovavam dentro.
        webrtc_speech_provider     = "",
        webrtc_speaches_url        = "",
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
    # explícito: um AsyncMock devolveria MagicMock, e a coleta mascarada seria decidida
    # pelo acaso de um MagicMock iterar vazio
    redis.hgetall = AsyncMock(return_value={})
    return redis


def _make_context() -> AsyncMock:
    from ..models import ContextSnapshot
    ctx = AsyncMock()
    ctx.get_snapshot = AsyncMock(return_value=ContextSnapshot())
    return ctx


def _abre(adapter) -> None:
    """A sessão aberta pelo handshake — o que o fechamento e a mensagem precisam (VOZ-04)."""
    adapter._sessions[SESSION_ID] = {
        "contact_id": "c-stt", "pool_id": "p-stt", "started_at": "2026-09-14T00:00:00+00:00"}


def _make_producer() -> AsyncMock:
    producer = AsyncMock()
    producer.send = AsyncMock()
    return producer


def _make_adapter(
    settings: Settings | None = None,
    stt: MockSTTProvider | None = None,
    tts: MockTTSProvider | None = None,
    room_client: MockRoomClient | None = None,
    voice_client: MockRoomClient | None = None,
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
        registry        = AsyncMock(),
        context_reader  = _make_context(),
        webrtc_provider = MockWebRTCProvider(),
        stt_provider    = stt,
        tts_provider    = tts,
    )
    # Inject pre-built room client if provided
    if room_client is not None:          # OUVINTE (VOZ-05 fatia 4)
        adapter._room_clients[SESSION_ID] = room_client
    if voice_client is not None:         # VOZ
        adapter._voice_clients[SESSION_ID] = voice_client
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
    async def test_speakers_yield_each_track_separately(self):
        client = MockRoomClient()
        chunk1, chunk2, chunk3 = b"\x00" * 100, b"\xff" * 200, b"\x01" * 50
        client.inject_audio(chunk1)
        client.inject_audio(chunk3, identity="agent-sub-1")
        client.inject_audio(chunk2)
        client.end_audio()

        collected: dict[str, list[bytes]] = {}
        async for ident, chunks in client.speakers():
            collected[ident] = [c async for c in chunks]

        assert collected == {MockRoomClient.CUSTOMER: [chunk1, chunk2], "agent-sub-1": [chunk3]}

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
        falantes = [ident async for ident, _ in client.speakers()]
        assert falantes == []


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
        _abre(adapter)

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
        # o bridge descarta `content` sem `type` (VOZ-04)
        assert payload["content"]["type"] == "text"
        assert payload["content"]["text"] == "Olá mundo"
        assert payload["content"]["payload"]["confidence"] == 0.95
        assert payload["contact_id"] == "c-stt"
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
    async def test_each_speaker_is_transcribed_in_its_own_channel_and_author(self):
        # VOZ-05 fatia 4: cliente e atendente humano, cada um no seu fluxo de STT; a frase do
        # humano sai como mensagem DELE (`agent_human`, `human-{sub}`), marcada como fala.
        stt = _SttPorTamanho()
        room = MockRoomClient()
        room.inject_audio(b"\x00" * 960)                              # cliente:  80 bytes μ-law
        room.inject_audio(b"\x00" * 4800, identity="agent-sub-hum")   # humano:  400 bytes
        room.end_audio()
        adapter, _, producer = _make_adapter(stt=stt, room_client=room)
        _abre(adapter)
        await adapter._stt_pipeline(SESSION_ID, room)

        por_texto = {e["content"]["text"]: e for e in _inbound(producer)}
        assert set(por_texto) == {"tamanho 80", "tamanho 400"}, list(por_texto)
        cliente, humano = por_texto["tamanho 80"], por_texto["tamanho 400"]
        assert cliente["author"]["type"] == "customer"
        assert humano["author"] == {"type": "agent_human", "id": "human-sub-hum", "display_name": None}
        assert humano["content_type"] == cliente["content_type"] == "audio_transcript"
        assert humano["content"]["payload"]["confidence"] == 0.9
        # nenhuma das duas falas entra no histórico de chat que o Console recarrega
        adapter._registry.append_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_voice_and_supervisor_tracks_are_not_transcribed(self):
        stt = _SttPorTamanho()
        room = MockRoomClient()
        room.inject_audio(b"\x00" * 960, identity="voz-sess-c3")
        room.inject_audio(b"\x00" * 960, identity="supervisor-sup-1")
        room.end_audio()
        adapter, _, producer = _make_adapter(stt=stt, room_client=room)
        _abre(adapter)
        await adapter._stt_pipeline(SESSION_ID, room)
        assert _inbound(producer) == [] and stt.chamadas == 0

    @pytest.mark.asyncio
    async def test_only_the_customer_voice_interrupts_the_ai(self):
        room = MockRoomClient()
        adapter, _, _ = _make_adapter(tts=_PcmTTS(), room_client=room)
        adapter._stt = _SttQueConsome()
        adapter._speaking.add(SESSION_ID)
        chamadas = []
        adapter._barge_in = lambda sid: chamadas.append(sid)
        for _ in range(30):
            room.inject_audio(_pcm48(3000, 10), identity="agent-sub-hum")
        room.end_audio()
        await adapter._stt_pipeline(SESSION_ID, room)
        assert chamadas == []

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

class _PcmTTS:
    """TTS que devolve PCM (como o auto-hospedado) e registra a ordem do que sintetizou."""
    output_sample_rate = 24000

    def __init__(self, atraso: float = 0.0) -> None:
        self.textos: list[str] = []
        self._atraso = atraso

    async def synthesize(self, text, voice_id=None):
        self.textos.append(text)
        if self._atraso:
            await asyncio.sleep(self._atraso)
        return b"\x01\x00" * 480


class _RoomLenta(MockRoomClient):
    """Sala cuja reprodução só termina quando o teste solta — para haver fala EM CURSO."""

    def __init__(self) -> None:
        super().__init__()
        self.soltar = asyncio.Event()

    async def publish_audio(self, pcm_bytes, sample_rate=24000):
        self.published_chunks.append(pcm_bytes)
        await self.soltar.wait()


class _SttQueConsome:
    """STT que CONSOME o áudio (o MockSTTProvider não itera a entrada, e o barge-in mora no
    laço que a itera) e não transcreve nada."""
    input_sample_rate = 16000
    _thr = 400.0

    async def stream(self, chunks, language=None, sample_rate=None):
        async for _ in chunks:
            pass
        if False:
            yield None


class _SttPorTamanho:
    """STT que transcreve o TAMANHO do que recebeu (μ-law a 8 kHz): distingue falantes sem
    depender da ordem em que os fluxos rodam."""
    def __init__(self) -> None:
        self.chamadas = 0

    async def stream(self, chunks, language=None, **kw):
        self.chamadas += 1
        total = 0
        async for c in chunks:
            total += len(c)
        yield STTResult(transcript=f"tamanho {total}", is_final=True, confidence=0.9)


async def _drena(adapter, sid=SESSION_ID, voltas=50):
    for _ in range(voltas):
        await asyncio.sleep(0)
    t = adapter._speech_tasks.get(sid)
    return t


def _pcm48(amplitude: int, ms: int) -> bytes:
    n = 48 * ms
    return struct.pack(f"<{n}h", *([amplitude] * n))


class TestSpeechSentences:
    def test_strips_emoji_and_markup_and_splits(self):
        from plughub_channel_gateway.adapters.webrtc import speech_sentences
        frases = speech_sentences("✅ Identidade **verificada** com sucesso. Transferindo para um especialista — aguarde.")
        assert frases == ["Identidade verificada com sucesso.", "Transferindo para um especialista — aguarde."]

    def test_short_piece_joins_the_next(self):
        from plughub_channel_gateway.adapters.webrtc import speech_sentences
        assert speech_sentences("Ok. Vou verificar os seus dados agora mesmo.") == ["Ok. Vou verificar os seus dados agora mesmo."]
        assert speech_sentences("Primeira frase bem comprida aqui. Fim.") == ["Primeira frase bem comprida aqui. Fim."]

    def test_nothing_speakable(self):
        from plughub_channel_gateway.adapters.webrtc import speech_sentences
        assert speech_sentences("  ✅ 🎉  ") == []


class TestAgentSpeech:
    """VOZ-05 fatia 3 — quem fala, em que ordem, e o barge-in."""

    def _adapter(self, tts=None, room=None):
        adapter, redis, producer = _make_adapter(tts=tts or _PcmTTS(), voice_client=room or MockRoomClient())
        adapter._connections[SESSION_ID] = AsyncMock()
        _abre(adapter)
        return adapter

    @pytest.mark.asyncio
    async def test_ai_message_is_spoken_sentence_by_sentence(self):
        tts, room = _PcmTTS(), MockRoomClient()
        adapter = self._adapter(tts, room)
        await adapter.deliver_text({"session_id": SESSION_ID, "author": {"type": "agent_ai"},
                                    "content": {"text": "Bem-vindo à central de energia. Vou confirmar os seus dados."},
                                    "timestamp": "2026-09-15T00:00:00Z"})
        await _drena(adapter)
        assert tts.textos == ["Bem-vindo à central de energia.", "Vou confirmar os seus dados."]
        assert len(room.published_chunks) == 2

    @pytest.mark.asyncio
    async def test_human_and_system_text_are_not_spoken(self):
        tts = _PcmTTS()
        adapter = self._adapter(tts)
        for autor in ("agent_human", "system"):
            await adapter.deliver_text({"session_id": SESSION_ID, "author": {"type": autor},
                                        "content": {"text": "Texto que nao se fala."}, "timestamp": "x"})
        await _drena(adapter)
        assert tts.textos == [] and SESSION_ID not in adapter._speech_tasks

    @pytest.mark.asyncio
    async def test_without_bot_leg_nothing_is_spoken_and_it_is_said(self, monkeypatch, caplog):
        # CONTROLE do primeiro: a mesma mensagem de IA, sem voz na sala e sem atribuição, não
        # vira fala — e diz que foi a atribuição que não chegou
        from plughub_channel_gateway.adapters import webrtc as mod
        monkeypatch.setattr(mod, "_SPEECH_WAIT_ROOM_S", 0.2)
        tts = _PcmTTS()
        adapter, _, _ = _make_adapter(tts=tts)
        adapter._connections[SESSION_ID] = AsyncMock()
        _abre(adapter)
        with caplog.at_level("INFO"):
            await adapter.deliver_text({"session_id": SESSION_ID, "author": {"type": "agent_ai"},
                                        "content": {"text": "Bem-vindo à central de energia."}, "timestamp": "x"})
            await asyncio.sleep(0.4)
        assert tts.textos == [] and "atribuicao nao chegou" in caplog.text

    @pytest.mark.asyncio
    async def test_voice_decided_absent_drops_now_naming_the_cause(self, monkeypatch, caplog):
        # Achado da revisão da fatia 3: IA com TTS e SEM STT não tem voz, e cada mensagem
        # esperava os 15 s culpando a sala. Com a atribuição conhecida, desiste na hora e diz
        # a causa real. Prazo longo DE PROPÓSITO: se esperar, o teste não termina a tempo.
        from plughub_channel_gateway.adapters import webrtc as mod
        monkeypatch.setattr(mod, "_SPEECH_WAIT_ROOM_S", 30.0)
        tts = _PcmTTS()
        adapter, _, _ = _make_adapter(tts=tts)
        adapter._stt, adapter._stt_unavailable = None, "sem provedor de STT (teste)"
        adapter._connections[SESSION_ID] = AsyncMock()
        _abre(adapter)
        with caplog.at_level("DEBUG"):
            # a primeira fala chega ANTES da atribuição (medido na fatia 3) e espera...
            await adapter.deliver_text({"session_id": SESSION_ID, "author": {"type": "agent_ai"},
                                        "content": {"text": "Bem-vindo à central de energia."}, "timestamp": "x"})
            await _drena(adapter)
            # ...a atribuição chega e decide: IA de áudio, mas sem STT não há voz
            adapter._customer_media[SESSION_ID] = frozenset()
            assert adapter._decide_voice(SESSION_ID, {"attendants": {"ia1": _registro("native")}}) is False
            await asyncio.sleep(0.2)
            # a seguinte nem entra na fila
            await adapter.deliver_text({"session_id": SESSION_ID, "author": {"type": "agent_ai"},
                                        "content": {"text": "Segunda mensagem da IA."}, "timestamp": "x"})
        assert tts.textos == []
        assert caplog.text.count("sem provedor de STT (teste)") >= 2
        assert "atribuicao nao chegou" not in caplog.text and "nao entrou na sala" not in caplog.text

    @pytest.mark.asyncio
    async def test_speech_arriving_during_routing_assigned_is_not_dropped(self, monkeypatch):
        # Medido AO VIVO na fatia 4 (probe_webrtc_tts_spoken T1/T2/C): `_customer_media` era
        # gravado antes da decisão da voz, com `await`s entre os dois; a primeira fala da IA,
        # que chega nesse intervalo, lia "sem voz" e caía em debug. O `create_room` aqui SEGURA
        # a atribuição no meio, e a fala chega exatamente ali.
        from plughub_channel_gateway.adapters import webrtc as mod
        from .test_webrtc_adapter import _assigned
        monkeypatch.setattr(mod, "LiveKitRoomClient", MockRoomClient)
        tts = _PcmTTS()
        adapter, _, _ = _make_adapter(tts=tts)
        adapter._connections[SESSION_ID] = AsyncMock()
        _abre(adapter)
        segura, chegou = asyncio.Event(), asyncio.Event()

        async def _create_room(name):
            chegou.set()
            await segura.wait()
        adapter._provider.create_room = _create_room
        atrib = asyncio.ensure_future(adapter._on_routing_assigned(
            AsyncMock(), SESSION_ID, _assigned("native", "ia1"), adapter._settings))
        await chegou.wait()
        assert SESSION_ID in adapter._customer_media          # testemunha: estamos NO intervalo
        await adapter.deliver_text({"session_id": SESSION_ID, "author": {"type": "agent_ai"},
                                    "content": {"text": "Bem-vindo à central de energia."}, "timestamp": "x"})
        assert SESSION_ID in adapter._speech_queues            # enfileirada, não descartada
        segura.set()
        await atrib
        await adapter._stop_bot_leg(SESSION_ID)

    @pytest.mark.asyncio
    async def test_human_call_with_listener_only_ai_hook_message_is_not_spoken(self):
        # Fatia 4: o OUVINTE está na sala numa chamada de humano; uma mensagem de IA (hook)
        # não pode ir para ele — oculto, ninguém o ouve. Sem voz decidida, não fala.
        tts, ouvinte = _PcmTTS(), MockRoomClient()
        adapter, _, _ = _make_adapter(tts=tts, room_client=ouvinte)
        adapter._connections[SESSION_ID] = AsyncMock()
        _abre(adapter)
        adapter._customer_media[SESSION_ID] = frozenset({"audio"})
        adapter._decide_voice(SESSION_ID, {"attendants": {"h1": _registro("human")}})
        await adapter.deliver_text({"session_id": SESSION_ID, "author": {"type": "agent_ai"},
                                    "content": {"text": "Avalie o atendimento de zero a dez."}, "timestamp": "x"})
        await _drena(adapter)
        assert tts.textos == [] and ouvinte.published_chunks == []

    @pytest.mark.asyncio
    async def test_menu_prompt_is_spoken(self):
        tts = _PcmTTS()
        adapter = self._adapter(tts)
        await adapter.deliver_menu({"session_id": SESSION_ID, "menu_id": "m1", "interaction": "button",
                                    "prompt": "Você prefere fatura por email ou correio?",
                                    "options": [{"id": "e", "label": "Email"}]})
        await _drena(adapter)
        assert tts.textos == ["Você prefere fatura por email ou correio?"]

    @pytest.mark.asyncio
    async def test_two_messages_never_interleave(self):
        tts = _PcmTTS(atraso=0.01)
        adapter = self._adapter(tts)
        await adapter.deliver_text({"session_id": SESSION_ID, "author": {"type": "agent_ai"},
                                    "content": {"text": "Primeira mensagem, frase um. Primeira mensagem, frase dois."}, "timestamp": "x"})
        await adapter.deliver_text({"session_id": SESSION_ID, "author": {"type": "agent_ai"},
                                    "content": {"text": "Segunda mensagem, frase um. Segunda mensagem, frase dois."}, "timestamp": "x"})
        await asyncio.sleep(0.2)
        assert tts.textos == ["Primeira mensagem, frase um.", "Primeira mensagem, frase dois.",
                              "Segunda mensagem, frase um.", "Segunda mensagem, frase dois."]

    @pytest.mark.asyncio
    async def test_order_follows_arrival_even_with_one_task_per_message(self):
        # o OutboundConsumer despacha cada mensagem numa task; aviso e menu chegam colados
        tts = _PcmTTS()
        adapter = self._adapter(tts)
        aviso = asyncio.ensure_future(adapter.deliver_text({
            "session_id": SESSION_ID, "author": {"type": "agent_ai"},
            "content": {"text": "Bem-vindo à central de energia."}, "timestamp": "x"}))
        menu = asyncio.ensure_future(adapter.deliver_menu({
            "session_id": SESSION_ID, "menu_id": "m1", "interaction": "button",
            "prompt": "Você prefere a fatura por email?", "options": []}))
        await asyncio.gather(aviso, menu)
        await asyncio.sleep(0.1)
        assert tts.textos == ["Bem-vindo à central de energia.", "Você prefere a fatura por email?"]

    @pytest.mark.asyncio
    async def test_agent_counts_as_speaking_until_the_audio_finishes_playing(self):
        # `capture_frame` devolve com até 1 s de áudio ainda na fila do SFU: o agente só se
        # cala quando a REPRODUÇÃO termina — senão um barge-in no último segundo seria ignorado
        class _RoomTocando(MockRoomClient):
            def __init__(self):
                super().__init__()
                self.tocou = asyncio.Event()

            async def wait_audio_playout(self):
                await self.tocou.wait()

        tts, room = _PcmTTS(), _RoomTocando()
        adapter = self._adapter(tts, room)
        await adapter.deliver_text({"session_id": SESSION_ID, "author": {"type": "agent_ai"},
                                    "content": {"text": "Uma frase que ainda toca na sala."}, "timestamp": "x"})
        await _drena(adapter)
        assert room.published_chunks and SESSION_ID in adapter._speaking
        room.tocou.set()
        await _drena(adapter)
        assert SESSION_ID not in adapter._speaking

    @pytest.mark.asyncio
    async def test_barge_in_stops_current_drops_pending_and_speech_comes_back(self):
        tts, room = _PcmTTS(), _RoomLenta()
        adapter = self._adapter(tts, room)
        await adapter.deliver_text({"session_id": SESSION_ID, "author": {"type": "agent_ai"},
                                    "content": {"text": "Aviso longo, primeira frase. Aviso longo, segunda frase."}, "timestamp": "x"})
        await adapter.deliver_text({"session_id": SESSION_ID, "author": {"type": "agent_ai"},
                                    "content": {"text": "Mensagem que estava na fila."}, "timestamp": "x"})
        await _drena(adapter)
        assert SESSION_ID in adapter._speaking and tts.textos == ["Aviso longo, primeira frase."]

        adapter._barge_in(SESSION_ID)
        room.soltar.set()
        await _drena(adapter)
        assert room.interrupts == 1
        assert tts.textos == ["Aviso longo, primeira frase."]     # a 2ª frase e a fila caíram
        assert SESSION_ID not in adapter._speaking

        await adapter.deliver_text({"session_id": SESSION_ID, "author": {"type": "agent_ai"},
                                    "content": {"text": "Certo, obrigado por aguardar na linha."}, "timestamp": "x"})
        await _drena(adapter)
        assert tts.textos[-1] == "Certo, obrigado por aguardar na linha."

    @pytest.mark.asyncio
    async def test_barge_in_stops_the_voice_not_the_listener(self):
        ouvinte, voz = MockRoomClient(), MockRoomClient()
        adapter, _, _ = _make_adapter(tts=_PcmTTS(), room_client=ouvinte, voice_client=voz)
        adapter._speaking.add(SESSION_ID)
        adapter._barge_in(SESSION_ID)
        assert voz.interrupts == 1 and ouvinte.interrupts == 0

    @pytest.mark.asyncio
    async def test_customer_voice_while_agent_speaks_triggers_barge_in(self):
        room = MockRoomClient()
        adapter, _, _ = _make_adapter(tts=_PcmTTS(), room_client=room)
        adapter._stt = _SttQueConsome()
        adapter._speaking.add(SESSION_ID)
        chamadas = []
        adapter._barge_in = lambda sid: chamadas.append(sid)
        for _ in range(30):                       # 300 ms de voz, em quadros de 10 ms
            room.inject_audio(_pcm48(3000, 10))
        room.end_audio()
        await adapter._stt_pipeline(SESSION_ID, room)
        assert chamadas and chamadas[0] == SESSION_ID

    @pytest.mark.asyncio
    async def test_no_barge_in_for_short_noise_silence_or_when_agent_is_quiet(self):
        # CONTROLES do anterior: estalo curto, silêncio, e voz com o agente calado
        for amplitude, ms, falando in ((3000, 100, True), (0, 500, True), (3000, 500, False)):
            room = MockRoomClient()
            adapter, _, _ = _make_adapter(tts=_PcmTTS(), room_client=room)
            adapter._stt = _SttQueConsome()
            if falando:
                adapter._speaking.add(SESSION_ID)
            chamadas = []
            adapter._barge_in = lambda sid: chamadas.append(sid)
            for _ in range(ms // 10):
                room.inject_audio(_pcm48(amplitude, 10))
            room.end_audio()
            await adapter._stt_pipeline(SESSION_ID, room)
            assert chamadas == [], (amplitude, ms, falando)

    @pytest.mark.asyncio
    async def test_listener_joins_hidden_and_mute_voice_joins_visible_and_deaf(self, monkeypatch):
        from plughub_channel_gateway.adapters import webrtc as mod
        adapter, _, _ = _make_adapter(tts=_PcmTTS())
        vistos = []
        orig = adapter._provider.generate_token
        adapter._provider.generate_token = lambda g: vistos.append(g) or orig(g)
        monkeypatch.setattr(mod, "LiveKitRoomClient", MockRoomClient)
        await adapter._start_stt_pipeline(SESSION_ID, ROOM_NAME)
        ouvinte = vistos[-1]
        assert ouvinte.hidden is True and ouvinte.can_publish is False and ouvinte.can_subscribe is True
        adapter._voice_wanted.add(SESSION_ID)
        await adapter._start_voice(SESSION_ID, ROOM_NAME)
        voz = vistos[-1]
        # oculta, o SFU não entrega a trilha dela a ninguém (medido na fatia 3): a IA ficaria muda
        assert voz.hidden is False and voz.can_publish is True and voz.can_subscribe is False
        assert voz.identity != ouvinte.identity
        assert SESSION_ID in adapter._room_clients and SESSION_ID in adapter._voice_clients
        await adapter._stop_bot_leg(SESSION_ID)
        assert SESSION_ID not in adapter._room_clients and SESSION_ID not in adapter._voice_clients

    @pytest.mark.asyncio
    async def test_voice_that_lost_its_decision_while_connecting_leaves(self, monkeypatch):
        from plughub_channel_gateway.adapters import webrtc as mod
        monkeypatch.setattr(mod, "LiveKitRoomClient", MockRoomClient)
        adapter, _, _ = _make_adapter(tts=_PcmTTS())
        await adapter._start_voice(SESSION_ID, ROOM_NAME)       # sem `_voice_wanted`
        assert SESSION_ID not in adapter._voice_clients

    @pytest.mark.asyncio
    async def test_routing_decides_listener_and_voice_separately(self, monkeypatch):
        # IA de áudio com STT e TTS: os dois entram. Sai a IA e fica só um especialista de
        # texto: os dois saem.
        from plughub_channel_gateway.adapters import webrtc as mod
        monkeypatch.setattr(mod, "LiveKitRoomClient", MockRoomClient)
        adapter, _, _ = _make_adapter(tts=_PcmTTS())
        adapter._redis.get = AsyncMock(return_value="c-1")
        ws = AsyncMock()
        state = {"attendants": {"ia1": _registro("native")}, "customer": {"publish": []}}
        await adapter._apply_customer_ceiling(ws, SESSION_ID, state, "attendant_joined:native")
        for _ in range(20):
            await asyncio.sleep(0)
        assert SESSION_ID in adapter._room_clients and SESSION_ID in adapter._voice_clients
        voz = adapter._voice_clients[SESSION_ID]
        state = {"attendants": {"ia2": _registro("native", audio=False)}, "customer": {"publish": ["audio"]}}
        await adapter._apply_customer_ceiling(ws, SESSION_ID, state, "attendant_left:native")
        # WCH-10 — o OUVINTE sai na hora (não fala); a VOZ sai AGENDADA, para a frase em curso
        # terminar. Aqui não há fala nenhuma, então a saída acontece na primeira volta do laço.
        assert SESSION_ID not in adapter._room_clients
        for _ in range(20):
            if voz.disconnected:
                break
            await asyncio.sleep(0.01)
        assert voz.disconnected is True
        assert SESSION_ID not in adapter._voice_clients

    @pytest.mark.asyncio
    async def test_first_message_waits_for_the_bot_to_enter(self):
        # medido ao vivo: o aviso inicial chega ANTES do routing.assigned, e era descartado
        tts, room = _PcmTTS(), MockRoomClient()
        adapter, _, _ = _make_adapter(tts=tts)
        adapter._connections[SESSION_ID] = AsyncMock()
        _abre(adapter)
        await adapter.deliver_text({"session_id": SESSION_ID, "author": {"type": "agent_ai"},
                                    "content": {"text": "Bem-vindo à central de energia."}, "timestamp": "x"})
        await asyncio.sleep(0.15)
        assert tts.textos == []                      # ainda sem voz: espera, não descarta
        adapter._voice_clients[SESSION_ID] = room
        await asyncio.sleep(0.15)
        assert tts.textos == ["Bem-vindo à central de energia."]

    @pytest.mark.asyncio
    async def test_speech_waits_customer_and_gives_up_saying_so(self, monkeypatch, caplog):
        from plughub_channel_gateway.adapters import webrtc as mod
        monkeypatch.setattr(mod, "_SPEECH_WAIT_ROOM_S", 0.2)
        tts, room = _PcmTTS(), MockRoomClient()
        room.customer_in_room = False
        adapter = self._adapter(tts, room)
        with caplog.at_level("INFO"):
            await adapter.deliver_text({"session_id": SESSION_ID, "author": {"type": "agent_ai"},
                                        "content": {"text": "Ninguem vai ouvir esta frase."}, "timestamp": "x"})
            await asyncio.sleep(0.4)
        assert tts.textos == [] and "cliente nao entrou na sala" in caplog.text

    @pytest.mark.asyncio
    async def test_stopping_bot_leg_stops_the_speech_worker(self):
        tts, room = _PcmTTS(), _RoomLenta()
        adapter = self._adapter(tts, room)
        await adapter.deliver_text({"session_id": SESSION_ID, "author": {"type": "agent_ai"},
                                    "content": {"text": "Uma fala que nao termina nunca."}, "timestamp": "x"})
        await _drena(adapter)
        tarefa = adapter._speech_tasks[SESSION_ID]
        await adapter._stop_bot_leg(SESSION_ID)
        await _drena(adapter)
        assert tarefa.cancelled() or tarefa.done()
        assert SESSION_ID not in adapter._speech_queues and SESSION_ID not in adapter._speaking



class TestVozNaTransferencia:
    """WCH-10 — a voz termina a frase antes de sair da sala.

    A proposição: *quando a IA deixa de atender, a voz sai da sala DEPOIS do que já está
    falando — e sai de qualquer jeito no teto*. São dois números, não um: o que ela espera
    (a frase em curso) e o que ela NÃO espera (o teto, e o fim da chamada).

    O que faria isto ficar vermelho: voltar a `_stop_voice` direto no ponto de decisão do teto
    — era assim até 2026-09-22, e o cliente ouvia meia frase antes do humano entrar
    (sessão `492aa613`).
    """

    def _adapter(self, tts=None, room=None):
        adapter, _, _ = _make_adapter(tts=tts or _PcmTTS(), voice_client=room or MockRoomClient())
        adapter._connections[SESSION_ID] = AsyncMock()
        _abre(adapter)
        return adapter

    async def _falando(self, adapter):
        await adapter.deliver_text({"session_id": SESSION_ID, "author": {"type": "agent_ai"},
                                    "content": {"text": "Entendi: aumento de limite do cartao. Vou te encaminhar agora."},
                                    "timestamp": "x"})
        await _drena(adapter)

    async def _ate(self, cond, prazo=2.0) -> bool:
        fim = asyncio.get_running_loop().time() + prazo
        while asyncio.get_running_loop().time() < fim:
            if cond():
                return True
            await asyncio.sleep(0.01)
        return cond()

    @pytest.mark.asyncio
    async def test_espera_a_frase_em_curso_antes_de_sair(self):
        tts, room = _PcmTTS(), _RoomLenta()
        adapter = self._adapter(tts, room)
        await self._falando(adapter)
        assert SESSION_ID in adapter._speaking          # premissa: há fala EM CURSO

        adapter._stop_voice_soon(SESSION_ID)
        await asyncio.sleep(0.15)                        # bem mais que o tique da drenagem
        assert SESSION_ID in adapter._voice_clients and not room.disconnected, (
            "a voz saiu da sala com a frase em curso — é o defeito da WCH-10"
        )

        room.soltar.set()                                # a frase terminou
        assert await self._ate(lambda: room.disconnected)
        assert SESSION_ID not in adapter._voice_clients

    @pytest.mark.asyncio
    async def test_sem_fala_pendente_sai_na_hora(self):
        """CONTROLE do anterior: sem nada para terminar, não há espera nenhuma."""
        room = MockRoomClient()
        adapter = self._adapter(room=room)
        adapter._stop_voice_soon(SESSION_ID)
        assert await self._ate(lambda: room.disconnected, prazo=0.5)
        assert SESSION_ID not in adapter._voice_clients

    @pytest.mark.asyncio
    async def test_teto_sai_assim_mesmo_e_diz_por_que(self, caplog, monkeypatch):
        from plughub_channel_gateway.adapters import webrtc as mod
        monkeypatch.setattr(mod, "_VOICE_DRAIN_MAX_S", 0.2)
        tts, room = _PcmTTS(), _RoomLenta()
        adapter = self._adapter(tts, room)
        await self._falando(adapter)
        with caplog.at_level(logging.WARNING):
            adapter._stop_voice_soon(SESSION_ID)
            assert await self._ate(lambda: room.disconnected)   # a sala NUNCA solta a reprodução
        assert SESSION_ID not in adapter._voice_clients
        assert "nao terminou" in caplog.text

    @pytest.mark.asyncio
    async def test_fim_da_chamada_nao_espera(self):
        """Teardown é outro fato: não há para quem falar, e a saída agendada some junto."""
        tts, room = _PcmTTS(), _RoomLenta()
        adapter = self._adapter(tts, room)
        await self._falando(adapter)
        adapter._stop_voice_soon(SESSION_ID)
        await adapter._stop_bot_leg(SESSION_ID)
        assert room.disconnected and SESSION_ID not in adapter._voice_clients
        assert SESSION_ID not in adapter._voice_stopping
        assert SESSION_ID not in adapter._speech_pending

    @pytest.mark.asyncio
    async def test_agente_de_ia_que_volta_cancela_a_saida(self):
        """A drenagem é janela: se um agente de IA de áudio volta nela, a voz FICA."""
        tts, room = _PcmTTS(), _RoomLenta()
        adapter = self._adapter(tts, room)
        await self._falando(adapter)
        adapter._stop_voice_soon(SESSION_ID)
        await asyncio.sleep(0.1)
        adapter._cancel_voice_stop(SESSION_ID)
        room.soltar.set()
        await asyncio.sleep(0.2)
        assert SESSION_ID in adapter._voice_clients and not room.disconnected

    @pytest.mark.asyncio
    async def test_pendencia_nao_vaza_quando_a_fala_nao_toca(self):
        """O contador é o que a drenagem lê: se ele vazasse, toda saída esperaria o teto.

        Mensagem que o tocador larga (sem sala) tem de zerar igual à que tocou — este é o
        caso que a contagem no `finally` sozinha NÃO cobre, porque ele nem chega lá.
        """
        adapter = self._adapter()
        adapter._voice_clients.pop(SESSION_ID)           # sem voz na sala: a mensagem não toca
        adapter._wait_room_for_speech = AsyncMock(return_value=None)
        adapter._speak(SESSION_ID, "Texto que ninguem vai ouvir.")
        assert adapter._speech_pending.get(SESSION_ID) == 1
        await _drena(adapter)
        assert SESSION_ID not in adapter._speech_pending


# ─────────────────────────────────────────────────────────────────────────────
# 5. DataChannel text → Kafka
# ─────────────────────────────────────────────────────────────────────────────

def _ws_streaming(messages: list[str]):
    """WS cujo `iter_text()` é ITERÁVEL — como o do Starlette (corrigido 2026-08-03).

    O `_receive_loop` faz `async for raw in ws.iter_text()` (`webrtc.py:788`). Num
    `AsyncMock`, `iter_text()` devolve uma **corrotina**, e o `async for` levanta
    `'async for' requires an object with __aiter__ method`. Os testes ainda montavam
    `receive_text`, que era o formato ANTERIOR do loop.

    **Dois testes desta classe passavam por causa disso**
    (`..._empty_text_not_published` e `..._interaction_reply_empty_not_written`): eles
    afirmam que NADA foi publicado, e com o loop quebrado nada é mesmo. Verde por
    ausência de execução — o inverso exato do que o teste pretende provar. Por isso os
    quatro passam a usar este helper, não só os dois que estavam vermelhos.

    `iter_text` é função SÍNCRONA que devolve um gerador assíncrono — não `AsyncMock`,
    não `async def`. É a forma do objeto real.
    """
    ws = AsyncMock()
    ws.accept    = AsyncMock()
    ws.send_json = AsyncMock()
    ws.close     = AsyncMock()

    async def _iter_text():
        for m in messages:
            yield m

    ws.iter_text = _iter_text
    return ws


class TestDataChannel:
    @pytest.mark.asyncio
    async def test_datachannel_text_published_to_kafka(self):
        """webrtc.message from browser DataChannel must be published as Kafka inbound."""
        adapter, redis, producer = _make_adapter()
        _abre(adapter)

        ws = _ws_streaming([
            json.dumps({"type": "webrtc.message", "text": "Preciso de ajuda"}),
        ])

        await adapter._receive_loop(ws, SESSION_ID)

        inbound_calls = [
            c for c in producer.send.call_args_list
            if c.args[0] == "conversations.inbound"
        ]
        assert len(inbound_calls) == 1
        payload = json.loads(inbound_calls[0].args[1].decode())
        assert payload["content_type"] == "text"
        assert payload["content"] == {"type": "text", "text": "Preciso de ajuda", "payload": None}
        assert payload["author"]["type"] == "customer" and payload["contact_id"] == "c-stt"
        assert payload["message_id"]
        assert payload["channel"] == "webrtc"

    @pytest.mark.asyncio
    async def test_datachannel_empty_text_not_published(self):
        """webrtc.message with empty text must not publish to Kafka."""
        adapter, redis, producer = _make_adapter()

        ws = _ws_streaming([json.dumps({"type": "webrtc.message", "text": "   "})])

        await adapter._receive_loop(ws, SESSION_ID)

        inbound_calls = [
            c for c in producer.send.call_args_list
            if c.args[0] == "conversations.inbound"
        ]
        assert inbound_calls == []

    @pytest.mark.asyncio
    async def test_interaction_reply_is_refused_and_writes_nothing(self):
        """O formato aposentado (envelope JSON em `menu:result:{sid}`, que o motor não lê)
        é recusado DITO — nem Redis, nem Kafka."""
        adapter, redis, producer = _make_adapter()
        _abre(adapter)

        ws = _ws_streaming([json.dumps({
            "type": "webrtc.interaction_reply", "reply": "option_a", "interaction_id": "int-001"})])
        await adapter._receive_loop(ws, SESSION_ID)

        redis.lpush.assert_not_called()
        assert producer.send.call_args_list == []
        erros = [c.args[0] for c in ws.send_json.call_args_list if c.args[0].get("type") == "conn.error"]
        assert [e["code"] for e in erros] == ["unsupported_message"]


# ─────────────────────────────────────────────────────────────────────────────
# 5b. Coleta mascarada (VOZ-05, fatia A)
# ─────────────────────────────────────────────────────────────────────────────

_FORM_RESULT = {"email": "a@b.c", "senha": "135791", "codigo_2fa": "246802"}


def _inbound(producer) -> list[dict]:
    return [json.loads(c.args[1].decode()) for c in producer.send.call_args_list
            if c.args[0] == "conversations.inbound"]


def _historico(adapter) -> list[str]:
    return [c.kwargs["text"] for c in adapter._registry.append_message.call_args_list]


def _waiting(masked_fields=None, masked=False) -> dict:
    return {"inst-1": json.dumps({"visibility": "all", "masked": masked,
                                  "masked_fields": masked_fields or [], "standby": False})}


class TestMenuResultHistoryText:
    """A casa única da linha de histórico — webchat e webrtc a consomem."""

    def test_form_redacts_only_masked_fields(self):
        from plughub_channel_gateway.adapters.webchat import menu_result_history_text
        t = menu_result_history_text("form", _FORM_RESULT, {"senha", "codigo_2fa"})
        assert t == '[Formulário: {"email": "a@b.c", "senha": "••••••", "codigo_2fa": "••••••"}]'

    def test_form_as_json_string_and_empty_masked_field(self):
        from plughub_channel_gateway.adapters.webchat import menu_result_history_text
        t = menu_result_history_text("form", json.dumps({"email": "x", "senha": ""}), {"senha"})
        assert t == '[Formulário: {"email": "x", "senha": ""}]'

    def test_undecodable_form_never_falls_back_to_raw(self):
        from plughub_channel_gateway.adapters.webchat import menu_result_history_text
        t = menu_result_history_text("form", "senha=135791", {"senha"})
        assert "135791" not in t and t == "[Formulário: {}]"

    def test_non_form_masked_redacts_everything(self):
        from plughub_channel_gateway.adapters.webchat import menu_result_history_text
        assert menu_result_history_text("text", "135791", {"pin"}) == "[Entrada mascarada (pin): ••••••]"

    def test_unmasked_passes(self):
        from plughub_channel_gateway.adapters.webchat import menu_result_history_text
        assert menu_result_history_text("button", "sim", set()) == "[Resposta: sim]"
        assert menu_result_history_text("checklist", ["a", "b"], set()) == '[Resposta: ["a", "b"]]'


class TestMaskedCapture:
    @pytest.mark.asyncio
    async def test_menu_submit_publishes_real_value_and_redacted_history(self):
        adapter, redis, producer = _make_adapter()
        _abre(adapter)
        adapter._menu_masked[SESSION_ID] = {"m1": ["senha", "codigo_2fa"]}

        ws = _ws_streaming([json.dumps({"type": "webrtc.menu_submit", "menu_id": "m1",
                                        "interaction": "form", "result": _FORM_RESULT})])
        await adapter._receive_loop(ws, SESSION_ID)

        ev = _inbound(producer)
        assert len(ev) == 1
        # o valor REAL vai ao bridge: é ele que o entrega ao menu que espera
        assert ev[0]["content"]["type"] == "menu_result"
        assert ev[0]["content"]["payload"] == {"menu_id": "m1", "interaction": "form", "result": _FORM_RESULT}
        assert ev[0]["author"]["type"] == "customer" and ev[0]["channel"] == "webrtc"
        hist = _historico(adapter)
        assert hist == ['[Formulário: {"email": "a@b.c", "senha": "••••••", "codigo_2fa": "••••••"}]']
        assert "m1" not in adapter._menu_masked[SESSION_ID]

    @pytest.mark.asyncio
    async def test_menu_submit_after_restart_uses_engine_declaration(self):
        adapter, redis, producer = _make_adapter()
        _abre(adapter)
        redis.hgetall = AsyncMock(return_value=_waiting(["senha", "codigo_2fa"]))

        ws = _ws_streaming([json.dumps({"type": "webrtc.menu_submit", "menu_id": "m-perdido",
                                        "interaction": "form", "result": _FORM_RESULT})])
        await adapter._receive_loop(ws, SESSION_ID)

        assert "135791" not in _historico(adapter)[0] and "246802" not in _historico(adapter)[0]
        assert '"email": "a@b.c"' in _historico(adapter)[0]

    @pytest.mark.asyncio
    async def test_menu_submit_with_nothing_known_redacts_whole_answer(self, caplog):
        adapter, redis, producer = _make_adapter()
        _abre(adapter)
        with caplog.at_level("WARNING"):
            ws = _ws_streaming([json.dumps({"type": "webrtc.menu_submit", "menu_id": "m-x",
                                            "interaction": "form", "result": _FORM_RESULT})])
            await adapter._receive_loop(ws, SESSION_ID)
        assert "135791" not in _historico(adapter)[0] and "a@b.c" not in _historico(adapter)[0]
        assert "desconhecidos" in caplog.text
        assert len(_inbound(producer)) == 1

    @pytest.mark.asyncio
    async def test_unmasked_menu_submit_keeps_answer_and_opens_no_grace(self):
        adapter, redis, producer = _make_adapter()
        _abre(adapter)
        adapter._menu_masked[SESSION_ID] = {}
        redis.hgetall = AsyncMock(side_effect=[{"inst-1": json.dumps({"masked": False, "masked_fields": []})}, {}])
        ws = _ws_streaming([json.dumps({"type": "webrtc.menu_submit", "menu_id": "m2",
                                        "interaction": "button", "result": "sim"})])
        await adapter._receive_loop(ws, SESSION_ID)
        assert _historico(adapter) == ["[Resposta: sim]"]
        assert SESSION_ID not in adapter._masked_grace_until

    @pytest.mark.asyncio
    async def test_malformed_menu_submit_is_refused(self):
        adapter, redis, producer = _make_adapter()
        _abre(adapter)
        ws = _ws_streaming([json.dumps({"type": "webrtc.menu_submit", "menu_id": "m1", "result": "x"})])
        await adapter._receive_loop(ws, SESSION_ID)
        assert _inbound(producer) == [] and _historico(adapter) == []
        erros = [c.args[0]["code"] for c in ws.send_json.call_args_list if c.args[0].get("type") == "conn.error"]
        assert erros == ["bad_message"]

    @pytest.mark.asyncio
    async def test_free_text_during_masked_wait_is_refused(self):
        adapter, redis, producer = _make_adapter()
        _abre(adapter)
        redis.hgetall = AsyncMock(return_value=_waiting(["senha"]))
        ws = _ws_streaming([json.dumps({"type": "webrtc.message", "text": "minha senha e 135791"})])
        await adapter._receive_loop(ws, SESSION_ID)
        assert _inbound(producer) == [] and _historico(adapter) == []
        erros = [c.args[0]["code"] for c in ws.send_json.call_args_list if c.args[0].get("type") == "conn.error"]
        assert erros == ["masked_capture_active"]

    @pytest.mark.asyncio
    async def test_free_text_during_unmasked_wait_is_published(self):
        # CONTROLE do anterior: menu esperando SEM máscara não recusa texto
        adapter, redis, producer = _make_adapter()
        _abre(adapter)
        redis.hgetall = AsyncMock(return_value=_waiting([]))
        ws = _ws_streaming([json.dumps({"type": "webrtc.message", "text": "oi"})])
        await adapter._receive_loop(ws, SESSION_ID)
        assert len(_inbound(producer)) == 1

    @pytest.mark.asyncio
    async def test_step_level_masked_wait_also_counts(self):
        adapter, redis, producer = _make_adapter()
        _abre(adapter)
        redis.hgetall = AsyncMock(return_value=_waiting([], masked=True))
        assert await adapter._masked_capture_active(SESSION_ID) is True

    @pytest.mark.asyncio
    async def test_unreadable_wait_counts_as_active(self):
        adapter, redis, producer = _make_adapter()
        redis.hgetall = AsyncMock(side_effect=ConnectionError("redis fora"))
        assert await adapter._masked_capture_active(SESSION_ID) is True
        redis.hgetall = AsyncMock(return_value={"inst-1": "nao-json"})
        assert await adapter._masked_capture_active(SESSION_ID) is True

    @pytest.mark.asyncio
    async def test_transcript_during_masked_wait_is_dropped_and_customer_told(self, caplog):
        adapter, redis, producer = _make_adapter()
        _abre(adapter)
        ws = AsyncMock()
        adapter._connections[SESSION_ID] = ws
        redis.hgetall = AsyncMock(return_value=_waiting(["senha"]))
        with caplog.at_level("INFO"):
            await adapter._publish_transcript(SESSION_ID, "a senha e um tres cinco", 0.9, 0, 900)
        assert _inbound(producer) == [] and _historico(adapter) == []
        assert "tres cinco" not in caplog.text and "DESCARTADA" in caplog.text
        avisos = [c.args[0] for c in ws.send_json.call_args_list]
        assert avisos and avisos[0]["author"] == "system" and "protegido" in avisos[0]["text"]

    @pytest.mark.asyncio
    async def test_transcript_outside_masked_wait_is_published(self):
        # CONTROLE: sem espera mascarada e sem folga, a fala segue para o bridge
        adapter, redis, producer = _make_adapter()
        _abre(adapter)
        await adapter._publish_transcript(SESSION_ID, "quero a fatura", 0.9, 0, 900)
        assert len(_inbound(producer)) == 1

    @pytest.mark.asyncio
    async def test_grace_covers_both_edges_and_expires(self, monkeypatch):
        from plughub_channel_gateway.adapters import webrtc as mod
        agora = [1000.0]
        monkeypatch.setattr(mod.time, "monotonic", lambda: agora[0])
        adapter, redis, producer = _make_adapter()
        _abre(adapter)
        # borda 1: o menu mascarado chegou ao adapter, o motor ainda não gravou menu:waiting
        await adapter.deliver_menu({"session_id": SESSION_ID, "menu_id": "m1", "interaction": "text",
                                    "masked_fields": ["pin"]})
        await adapter._publish_transcript(SESSION_ID, "um tres cinco", 0.9, 0, 900)
        assert _inbound(producer) == []
        # folga vencida e motor sem espera: a fala volta a ser publicada
        agora[0] += mod._MASKED_SPEECH_GRACE_S + 0.1
        await adapter._publish_transcript(SESSION_ID, "quero a fatura", 0.9, 0, 900)
        assert len(_inbound(producer)) == 1
        # borda 2: a submissão mascarada reabre a folga (a fala em curso chega depois do HDEL)
        adapter._menu_masked[SESSION_ID] = {"m2": ["pin"]}
        ws = _ws_streaming([json.dumps({"type": "webrtc.menu_submit", "menu_id": "m2",
                                        "interaction": "text", "result": "135"})])
        await adapter._receive_loop(ws, SESSION_ID)
        await adapter._publish_transcript(SESSION_ID, "um tres cinco", 0.9, 0, 900)
        textos = [e["content"].get("text") for e in _inbound(producer)]
        assert "um tres cinco" not in textos


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

        # `_close_session` chama `stt_task.cancel()` e NÃO aguarda a task
        # (`webrtc.py:955-956`) — cancelamento em asyncio é um pedido, não um fato.
        # Entre o `cancel()` e a task de fato terminar ela fica em estado *cancelling*, e
        # `Task.cancelled()` só devolve True depois que ela termina. O teste afirmava
        # `cancelled()` no instante seguinte à chamada, então dependia de a task ter
        # sido escalonada — o que é corrida, não contrato. Aqui a espera é explícita.
        with pytest.raises(asyncio.CancelledError):
            await stt_task
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
        adapter._customer_media[SESSION_ID]      = frozenset({"audio"})
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
        _abre(adapter)
        # No room client — must not raise
        await adapter._close_session(SESSION_ID, "session_timeout")
        producer.send.assert_called_once()  # contact_closed published
        assert producer.send.call_args.args[0] == "conversations.events"


# ─────────────────────────────────────────────────────────────────────────────
# 8. Provider factories
# ─────────────────────────────────────────────────────────────────────────────

class TestProviderFactories:
    # VOZ-05: estes testes afirmavam que, sem chave, a fábrica devolvia um MOCK — o placebo
    # que entrava na sala e não transcrevia nada. Sem provedor, a fábrica devolve None e o
    # bot leg fica INDISPONÍVEL com o motivo nomeado.

    def test_build_stt_provider_no_api_key_returns_none(self):
        s = _settings(webrtc_stt_enabled=True, voice_deepgram_api_key="")
        adapter, _, _ = _make_adapter(settings=s)
        assert adapter._build_stt_provider() is None

    def test_build_stt_provider_disabled_returns_none(self):
        s = _settings(webrtc_stt_enabled=False)
        adapter, _, _ = _make_adapter(settings=s)
        assert adapter._build_stt_provider() is None

    def test_build_tts_provider_no_api_key_returns_none(self):
        s = _settings(voice_elevenlabs_api_key="")
        adapter, _, _ = _make_adapter(settings=s)
        assert adapter._build_tts_provider() is None

    def test_sem_provedores_o_adapter_nomeia_o_que_falta(self):
        s = _settings(webrtc_stt_enabled=True, voice_deepgram_api_key="", voice_elevenlabs_api_key="")
        adapter = WebRTCAdapter(producer=MagicMock(), redis=MagicMock(), settings=s,
                                registry=MagicMock(), context_reader=MagicMock(),
                                webrtc_provider=MockWebRTCProvider())
        assert "STT" in (adapter._stt_unavailable or "")
        assert "TTS" in (adapter._tts_unavailable or "")


# ─────────────────────────────────────────────────────────────────────────────
# 9. Gatilho do bot leg (VOZ-05) — TRANSCREVE toda chamada com áudio (STT);
#    CONVERTE para o agente de IA (STT + TTS). Nunca por mock.
# ─────────────────────────────────────────────────────────────────────────────

def _registro(framework: str, audio: bool = True) -> dict:
    return {"framework": framework, "pool_id": "p", "policy_source": "pool:p",
            "customer_publish": ["audio"] if audio else [], "agent_publish": ["audio"]}


class TestBotLegGatilho:
    def _adapter(self, stt: bool, tts: bool):
        s = _settings(webrtc_stt_enabled=True, voice_deepgram_api_key="", voice_elevenlabs_api_key="")
        kw = {}
        if stt:
            kw["stt_provider"] = MockSTTProvider()
        if tts:
            kw["tts_provider"] = MockTTSProvider(False)
        return WebRTCAdapter(producer=MagicMock(), redis=MagicMock(), settings=s, registry=MagicMock(),
                             context_reader=MagicMock(), webrtc_provider=MockWebRTCProvider(), **kw)

    def test_humano_com_audio_e_stt_chama_o_ouvinte_e_nao_a_voz(self):
        # Fatia 4: a chamada de humano é transcrita (cliente e humano, cada um no seu canal).
        # Até ali o ouvinte não entrava, porque a fala só teria destino como mensagem de chat.
        a = self._adapter(stt=True, tts=True)
        state = {"attendants": {"h1": _registro("human")}}
        assert a._ceiling(state) == frozenset({"audio"})
        assert a._bot_leg_should_run(state, a._ceiling(state)) is True
        assert a._voice_should_run(state) is False
        assert a._bot_leg_state(state, SESSION_ID) == {"transcribe": True, "convert": False, "available": True}

    def test_ia_e_humano_juntos_com_provedores_chamam_o_bot(self):
        a = self._adapter(stt=True, tts=True)
        state = {"attendants": {"h1": _registro("human"), "ia1": _registro("native")}}
        assert a._bot_leg_should_run(state, a._ceiling(state)) is True

    def test_humano_com_audio_sem_stt_nao_ha_bot_e_o_estado_diz_que_nao_transcreve(self):
        a = self._adapter(stt=False, tts=False)
        state = {"attendants": {"h1": _registro("human")}}
        assert a._ceiling(state) == frozenset({"audio"})       # o humano ouve pela sala
        assert a._bot_leg_should_run(state, a._ceiling(state)) is False
        bot = a._bot_leg_state(state, SESSION_ID)
        assert bot["available"] is False and "NAO transcrita" in bot["reason"]

    def test_ia_com_stt_e_tts_ganha_audio_e_chama_o_bot(self):
        a = self._adapter(stt=True, tts=True)
        state = {"attendants": {"ia1": _registro("native")}}
        assert a._ceiling(state) == frozenset({"audio"})
        assert a._bot_leg_should_run(state, a._ceiling(state)) is True

    def test_ia_com_stt_sem_tts_fica_sem_audio_e_o_estado_diz_que_ela_nao_fala(self):
        a = self._adapter(stt=True, tts=False)
        state = {"attendants": {"ia1": _registro("native")}}
        assert a._ceiling(state) == frozenset()
        bot = a._bot_leg_state(state, SESSION_ID)
        assert bot["convert"] is True and bot["available"] is False and "sem voz" in bot["reason"]

    def test_ia_sem_provedores_fica_sem_audio_e_o_estado_diz(self):
        a = self._adapter(stt=False, tts=False)
        state = {"attendants": {"ia1": _registro("native")}}
        assert a._ceiling(state) == frozenset()
        assert a._bot_leg_should_run(state, a._ceiling(state)) is False
        bot = a._bot_leg_state(state, SESSION_ID)
        assert bot["available"] is False and "STT" in bot["reason"] and "TTS" in bot["reason"]

    @pytest.mark.asyncio
    async def test_ultimo_atendente_de_audio_sai_e_o_bot_sai_da_sala(self):
        a = self._adapter(stt=True, tts=True)
        a._redis = AsyncMock()
        a._redis.get = AsyncMock(return_value="c-1")
        rc = MockRoomClient()
        rc.connected = True
        a._room_clients[SESSION_ID] = rc
        # sobra só um especialista de texto sem áudio no pool: nada a transcrever
        state = {"attendants": {"ia1": _registro("native", audio=False)},
                 "customer": {"publish": ["audio"]}}
        await a._apply_customer_ceiling(AsyncMock(), SESSION_ID, state, "attendant_left:human")
        assert rc.disconnected is True
        assert SESSION_ID not in a._room_clients

    def test_pool_sem_audio_nao_precisa_do_bot(self):
        a = self._adapter(stt=True, tts=True)
        state = {"attendants": {"ia1": _registro("native", audio=False)}}
        assert a._bot_leg_should_run(state, a._ceiling(state)) is False
        assert a._bot_leg_state(state, SESSION_ID) == {"transcribe": False, "convert": False, "available": True}
