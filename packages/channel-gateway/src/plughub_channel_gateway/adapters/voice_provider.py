"""
adapters/voice_provider.py
Provider abstractions for voice channels.

Architecture: channel-gateway-multi-channel.md § 9.7 / 9.11

Two Protocol interfaces, used by the bot leg of the WebRTC adapter (browser call, chat call
and the SIP leg of the `voice` channel):
  ISTTProvider    — Speech-to-Text
  ITTSProvider    — Text-to-Speech

Concrete implementations kept here (the default pair lives in `speaches_provider.py`):
  DeepgramSTTProvider     — Deepgram WebSocket streaming STT (legacy pair, when configured)
  ElevenLabsTTSProvider   — ElevenLabs REST TTS (legacy pair, when configured)
  MockSTTProvider / MockTTSProvider — in-memory stubs for unit tests

Plus the speech data types (`STTResult`, `SpeechTuning`, `SpeechSegmentation`, `SpeechStats`).

VOZ-03 (2026-09-28): the Twilio TwiML leg (`voice.py`, `IVoiceProvider`, `TwilioVoiceProvider`,
the `<Say>`/Aura TTS and the Fallback wrappers only it used) was RETIRED. A phone call reaches
the platform through the SIP trunk into the SFU room; Twilio stays only as a SIP carrier.
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.parse
from dataclasses import dataclass, field
from typing import AsyncIterator, Protocol, runtime_checkable

import httpx

logger = logging.getLogger("plughub.channel-gateway.voice.provider")

# ── Data types ────────────────────────────────────────────────────────────────


@dataclass
class STTResult:
    """Single STT result emitted by ISTTProvider.stream().

    `confidence` None = o provedor não MEDIU esta fala (VOZ-18). Nunca preencher com 1.0: era o
    default que fazia `min_confidence` parecer aplicado sem nada ter sido medido."""
    transcript:  str
    is_final:    bool
    confidence:  float | None = 1.0
    start_ms:    int   = 0
    end_ms:      int   = 0


@dataclass
class SpeechTuning:
    """Ajuste da segmentação de fala de UM falante, mutável e lido a cada quadro (VOZ-18).

    A coleta por voz (`collect.voice.end_silence_ms`/`max_speech_s`) o liga enquanto o menu espera
    e o desliga ao terminar; `None` = o default do provedor. Só vale no provedor que declara
    `supports_tuning = True` — os outros ignoram, e quem declarou é avisado no log."""
    silence_ms:       int | None = None
    max_utterance_ms: int | None = None

    def clear(self) -> None:
        self.silence_ms = None
        self.max_utterance_ms = None


@dataclass(frozen=True)
class SpeechSegmentation:
    """Como a fala de uma CHAMADA é segmentada (VOZ-21): config do tenant por canal, lida uma vez
    quando a chamada abre o STT (`speech_config.py`). Os defaults são os de código — os mesmos do
    seed do config-api — e `provenance` diz, por campo, de onde veio cada valor."""
    energy_threshold: float = 400.0     # RMS int16 que conta como voz
    end_silence_ms:   int   = 700       # silêncio que fecha a fala
    gap_ms:           int   = 700       # sem quadro nenhum por tanto tempo também fecha
    min_speech_ms:    int   = 250       # abaixo disto é ruído, não fala
    max_speech_ms:    int   = 15_000    # teto de uma fala
    vad_filter:       bool  = True      # VAD do serviço de transcrição (VOZ-19)
    provenance:       dict  = field(default_factory=dict, compare=False)

    def describe(self) -> str:
        def rot(campo: str, valor: object) -> str:
            return f"{campo}={valor} ({self.provenance.get(campo, 'default')})"
        return " ".join(rot(c, getattr(self, c)) for c in (
            "energy_threshold", "end_silence_ms", "gap_ms", "min_speech_ms", "max_speech_ms", "vad_filter"))


class SpeechStats:
    """O que o provedor viu num fluxo de fala — só números (VOZ-22). Mutável: o provedor conta,
    quem abriu o fluxo publica no fim (`speech_metrics.stream_summary`).

    O chão de ruído é o RMS dos quadros ABAIXO do limiar de voz; guarda os valores até
    `NOISE_CAP` (uma hora de quadros de 20 ms) — depois só conta."""
    NOISE_CAP = 180_000

    def __init__(self) -> None:
        self.audio_ms = 0.0
        self.frames = 0
        self.voiced_frames = 0
        self.noise_rms: list[float] = []
        self.utterances_sent = 0          # trechos enviados à transcrição
        self.utterances_transcribed = 0   # com texto
        self.discarded_vad = 0            # enviados e esvaziados pelo VAD do serviço
        self.discarded_short = 0          # voz abaixo da fala mínima — nem enviados
        self.cut_max_speech = 0           # fechados pelo teto de fala, não por silêncio
        self.stt_errors = 0               # serviço fora, http != 200, resposta ilegível
        self.confidences: list[float] = []

    def frame(self, ms: float, level: float, voiced: bool) -> None:
        self.audio_ms += ms
        self.frames += 1
        if voiced:
            self.voiced_frames += 1
        elif len(self.noise_rms) < self.NOISE_CAP:
            self.noise_rms.append(level)

    @staticmethod
    def _pct(valores: list[float], nd: int) -> tuple[float | None, float | None, float | None]:
        if not valores:
            return (None, None, None)
        v = sorted(valores)
        def q(p: float) -> float:
            return round(v[min(len(v) - 1, int(p * (len(v) - 1) + 0.5))], nd)
        return (q(0.10), q(0.50), q(0.90))

    def noise_percentiles(self) -> tuple[float | None, float | None, float | None]:
        return self._pct(self.noise_rms, 1)

    def confidence_percentiles(self) -> tuple[float | None, float | None, float | None]:
        return self._pct(self.confidences, 4)


# ── Protocol interfaces ───────────────────────────────────────────────────────


@runtime_checkable
class ISTTProvider(Protocol):
    """Streaming Speech-to-Text — yields STTResult as audio arrives."""

    async def stream(
        self,
        audio_chunks: AsyncIterator[bytes],
        sample_rate:  int = 8000,
        language:     str = "pt-BR",
    ) -> AsyncIterator[STTResult]:
        """
        Consume audio chunks and yield STT results.
        Partial results have is_final=False; final transcript is_final=True.
        Caller cancels the iterator to stop the session.
        """
        ...


@runtime_checkable
class ITTSProvider(Protocol):
    """Text-to-Speech — optional audio synthesis."""

    async def synthesize(
        self,
        text:     str,
        voice_id: str | None = None,
    ) -> bytes | None:
        """
        Convert text to audio bytes (PCM/MP3). None means no audio was produced.
        """
        ...


# ── DeepgramSTTProvider ───────────────────────────────────────────────────────


class DeepgramSTTProvider:
    """
    Deepgram WebSocket streaming STT.

    Connects to wss://api.deepgram.com/v1/listen with μ-law 8kHz encoding.
    Yields STTResult for each transcript event (interim + final).
    """

    _WS_URL = "wss://api.deepgram.com/v1/listen"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def stream(
        self,
        audio_chunks: AsyncIterator[bytes],
        sample_rate:  int = 8000,
        language:     str = "pt-BR",
    ) -> AsyncIterator[STTResult]:
        """
        Async generator: consume audio_chunks → yield STTResult.

        Uses Deepgram v1 streaming API via websockets library.
        Falls back gracefully to empty results when api_key is not set (dev mode).
        """
        if not self._api_key:
            logger.debug("DeepgramSTT: no api_key — yielding empty transcripts")
            async for _ in audio_chunks:
                pass
            return

        try:
            import websockets  # optional dep — deepgram-sdk or websockets
        except ImportError:
            logger.warning("DeepgramSTT: 'websockets' not installed — no STT")
            async for _ in audio_chunks:
                pass
            return

        params = urllib.parse.urlencode({
            "encoding":         "mulaw",
            "sample_rate":      sample_rate,
            "channels":         1,
            "language":         language,
            "model":            "nova-2",
            "interim_results":  "true",
            "endpointing":      "500",   # ms of silence → utterance end
            "utterance_end_ms": "1000",
        })
        url = f"{self._WS_URL}?{params}"

        results_queue: asyncio.Queue[STTResult | None] = asyncio.Queue()

        async def _sender(ws) -> None:
            """Push audio chunks → Deepgram WS."""
            try:
                async for chunk in audio_chunks:
                    await ws.send(chunk)
                # Signal end of stream
                await ws.send(json.dumps({"type": "CloseStream"}))
            except Exception as exc:
                logger.debug("Deepgram sender closed: %s", exc)

        async def _receiver(ws) -> None:
            """Receive Deepgram transcript events → results_queue."""
            try:
                async for message in ws:
                    data = json.loads(message)
                    if data.get("type") != "Results":
                        continue
                    channel = data.get("channel", {})
                    alts = channel.get("alternatives", [])
                    if not alts:
                        continue
                    alt = alts[0]
                    transcript = alt.get("transcript", "").strip()
                    if not transcript:
                        continue
                    is_final = data.get("is_final", False)
                    words = alt.get("words", [])
                    start_ms = int(words[0]["start"] * 1000) if words else 0
                    end_ms   = int(words[-1]["end"] * 1000)  if words else 0
                    await results_queue.put(STTResult(
                        transcript  = transcript,
                        is_final    = is_final,
                        confidence  = alt.get("confidence", 1.0),
                        start_ms    = start_ms,
                        end_ms      = end_ms,
                    ))
            except Exception as exc:
                logger.debug("Deepgram receiver closed: %s", exc)
            finally:
                await results_queue.put(None)  # sentinel

        headers = {"Authorization": f"Token {self._api_key}"}
        try:
            async with websockets.connect(url, additional_headers=headers) as ws:
                sender_task   = asyncio.create_task(_sender(ws))
                receiver_task = asyncio.create_task(_receiver(ws))
                while True:
                    result = await results_queue.get()
                    if result is None:
                        break
                    yield result
                sender_task.cancel()
                receiver_task.cancel()
        except Exception as exc:
            logger.warning("DeepgramSTT connection failed: %s", exc)


# ── ElevenLabsTTSProvider ─────────────────────────────────────────────────────


class ElevenLabsTTSProvider:
    """
    High-quality TTS via ElevenLabs REST API.

    Returns MP3 bytes for the WebRTC bot leg. Supports all ElevenLabs multilingual voices.

    Docs: https://elevenlabs.io/docs/api-reference/text-to-speech
    """

    _BASE_URL = "https://api.elevenlabs.io/v1/text-to-speech"

    # Default: "Adam" multilingual voice — good for PT-BR, EN, ES.
    # Override via PLUGHUB_VOICE_ELEVENLABS_VOICE_ID.
    _DEFAULT_VOICE_ID = "pNInz6obpgDQGcFmaJgB"

    def __init__(
        self,
        api_key:  str,
        voice_id: str = _DEFAULT_VOICE_ID,
    ) -> None:
        self._api_key  = api_key
        self._voice_id = voice_id

    async def synthesize(
        self,
        text:     str,
        voice_id: str | None = None,
    ) -> bytes | None:
        """
        Synthesize text → MP3 bytes via ElevenLabs REST API.
        Returns None on failure (the caller treats it as no audio).
        """
        if not self._api_key:
            logger.debug("ElevenLabsTTS: no api_key — skipping")
            return None

        vid = voice_id or self._voice_id
        url = f"{self._BASE_URL}/{vid}"
        headers = {
            "xi-api-key":   self._api_key,
            "Content-Type": "application/json",
            "Accept":       "audio/mpeg",
        }
        payload = {
            "text":           text,
            "model_id":       "eleven_multilingual_v2",
            "voice_settings": {
                "stability":        0.5,
                "similarity_boost": 0.75,
            },
        }
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                return resp.content
        except Exception as exc:
            logger.warning("ElevenLabsTTS failed: %s", exc)
            return None


# ── Mocks ─────────────────────────────────────────────────────────────────────


class MockSTTProvider:
    """
    In-memory STT stub — yields pre-configured transcript results.

    Usage in tests:
        mock_stt = MockSTTProvider()
        mock_stt.results = [STTResult("Olá", is_final=True)]
    """

    def __init__(self) -> None:
        self.results: list[STTResult] = []
        self.chunks_received: list[bytes] = []

    async def stream(
        self,
        audio_chunks: AsyncIterator[bytes],
        sample_rate:  int = 8000,
        language:     str = "pt-BR",
    ) -> AsyncIterator[STTResult]:
        async for chunk in audio_chunks:
            self.chunks_received.append(chunk)
        for result in self.results:
            yield result


class MockTTSProvider:
    """
    In-memory TTS stub — returns fixed audio bytes or None.

    synthesize_returns_none=True simulates a provider that produced no audio.
    """

    def __init__(self, synthesize_returns_none: bool = True) -> None:
        self._none_mode   = synthesize_returns_none
        self.synthesized: list[dict] = []

    async def synthesize(
        self,
        text:     str,
        voice_id: str | None = None,
    ) -> bytes | None:
        self.synthesized.append({"text": text, "voice_id": voice_id})
        if self._none_mode:
            return None
        return b"\x00" * 16  # stub PCM bytes


