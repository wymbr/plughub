"""
adapters/webrtc_room_client.py
Server-side LiveKit room participation for Arc 15 WebRTC STT/TTS pipeline.

Architecture: docs/arcos/arc15-webrtc.md § Phase C

The room client connects to a LiveKit room as a server-side bot participant:
  - speakers()                 → (identidade, quadros 48kHz PCM) por trilha transcrita — cliente e humanos
  - publish_audio()            → TTS injection via LocalAudioTrack

Audio format contract
---------------------
  subscribe output: 48kHz 16-bit signed PCM bytes (LiveKit native format)
  STT input:        8kHz μ-law — caller must pass through resample_pcm_48_to_8()
  TTS inject:       24kHz 16-bit signed PCM (ElevenLabs MP3 decoded to PCM)

Resampler
---------
  resample_pcm_48_to_8():
    1. audioop (Python stdlib, available through Python 3.12)
    2. struct-based decimation fallback for Python 3.13+ (dev/test only)
"""

from __future__ import annotations

import asyncio
import logging
import struct
from typing import Any, AsyncIterator, Protocol, runtime_checkable
from plughub_tasks import disparar

logger = logging.getLogger("plughub.channel-gateway.webrtc.room_client")

# Identidade do cliente na sala: `customer-{contact_id}` (`WebRTCAdapter._customer_identity`).
CUSTOMER_IDENTITY_PREFIX = "customer-"
# Identidade do atendente HUMANO na sala: `agent-{sub}` (`WebRTCAdapter.get_token`, role=agent).
AGENT_IDENTITY_PREFIX = "agent-"
# Quem o OUVINTE transcreve (VOZ-05 fatia 4): o cliente e os atendentes humanos — cada um no seu
# canal. Fica de fora a VOZ do agente de IA (`voz-…`: o texto já é a mensagem) e o supervisor
# (`supervisor-…`: escuta, não participa da conversa com o cliente).
TRANSCRIBED_PREFIXES = (CUSTOMER_IDENTITY_PREFIX, AGENT_IDENTITY_PREFIX)


# ── Audio helpers ──────────────────────────────────────────────────────────────


def resample_pcm_48_to_8(pcm_48: bytes, num_channels: int = 1) -> bytes:
    """
    Downsample 48kHz 16-bit PCM to 8kHz μ-law.

    Input:  raw 16-bit signed PCM at 48kHz, interleaved if stereo.
    Output: 8kHz μ-law bytes — format expected by Deepgram / FallbackSTTProvider.

    Uses audioop when available (Python ≤ 3.12).  Falls back to
    struct-based decimation on Python 3.13+ where audioop was removed.
    The fallback is not production-grade but keeps unit tests green.
    """
    try:
        import audioop  # type: ignore[import-not-found]

        # Step 1: stereo → mono
        if num_channels == 2:
            pcm_48 = audioop.tomono(pcm_48, 2, 0.5, 0.5)
        # Step 2: 48kHz → 8kHz (ratio 6:1)
        pcm_8, _ = audioop.ratecv(pcm_48, 2, 1, 48000, 8000, None)
        # Step 3: PCM16 → μ-law
        return audioop.lin2ulaw(pcm_8, 2)
    except ImportError:
        return _resample_pcm_fallback(pcm_48, num_channels)


def _resample_pcm_fallback(pcm_48: bytes, num_channels: int) -> bytes:
    """
    Fallback resampler for Python 3.13+: struct decimation + μ-law approximation.
    """
    n_samples = len(pcm_48) // (2 * num_channels)
    fmt       = f"<{n_samples * num_channels}h"
    raw       = struct.unpack(fmt, pcm_48[: n_samples * num_channels * 2])

    # Mono-mix
    if num_channels == 2:
        mono: list[int] = [
            (raw[i * 2] + raw[i * 2 + 1]) // 2 for i in range(n_samples)
        ]
    else:
        mono = list(raw)

    # Decimate 48kHz → 8kHz
    decimated = mono[::6]
    return bytes(_linear_to_ulaw(s) for s in decimated)


def _linear_to_ulaw(sample: int) -> int:
    """Linear PCM16 → μ-law byte (ITU-T G.711 approximation)."""
    BIAS = 0x84
    CLIP = 32767
    sign = 0
    if sample < 0:
        sample = -sample
        sign   = 0x80
    sample = min(sample + BIAS, CLIP)
    exp  = 7
    mask = 0x4000
    while sample < mask and exp > 0:
        exp  -= 1
        mask >>= 1
    mantissa = (sample >> (exp + 3)) & 0x0F
    return (~(sign | (exp << 4) | mantissa)) & 0xFF


def mp3_to_pcm(mp3_bytes: bytes, target_sample_rate: int = 24000) -> bytes:
    """
    Decode MP3 bytes to raw 16-bit PCM at target_sample_rate Hz, mono.

    Used to convert ElevenLabs / Deepgram Aura MP3 output to the PCM format
    expected by LiveKit's AudioSource.capture_frame().

    Requires pydub + ffmpeg (or libav).  Returns empty bytes on failure so
    the TTS pipeline can degrade silently rather than crash.
    """
    try:
        from pydub import AudioSegment  # type: ignore[import-not-found]
        import io

        seg = AudioSegment.from_file(io.BytesIO(mp3_bytes), format="mp3")
        seg = seg.set_channels(1).set_sample_width(2).set_frame_rate(target_sample_rate)
        return seg.raw_data
    except Exception as exc:
        logger.warning("mp3_to_pcm failed: %s — TTS injection skipped", exc)
        return b""


def _fim(fila: asyncio.Queue) -> None:
    """Sinal de fim na fila; se ela está cheia, abre espaço (o fim vale mais que um quadro)."""
    try:
        fila.put_nowait(None)
    except asyncio.QueueFull:
        fila.get_nowait()
        fila.put_nowait(None)


async def _drena(fila: asyncio.Queue) -> AsyncIterator[bytes]:
    while True:
        chunk = await fila.get()
        if chunk is None:
            return
        yield chunk


# ── Protocol ───────────────────────────────────────────────────────────────────


@runtime_checkable
class IWebRTCRoomClient(Protocol):
    """
    Server-side LiveKit room participant (bot leg) for the STT/TTS pipeline.

    Uma instância por PAPEL do bot leg na sala (VOZ-05 fatia 4): o ouvinte usa `speakers()`,
    a voz usa `publish_audio()`.
    Lifecycle:
        await client.connect(room_name, identity, token, url)
        async for identity, chunks in client.speakers():   # ouvinte: um fluxo por falante
            ...                                             # STT do falante
        await client.publish_audio(pcm_24k_bytes)          # voz: TTS
        await client.disconnect()
    """

    async def connect(
        self,
        room_name:   str,
        identity:    str,  # e.g. "bot-{session_id[:8]}"
        token:       str,  # LiveKit JWT with can_subscribe + can_publish grants
        livekit_url: str,  # wss://livekit.example.com
    ) -> None:
        """Connect to the LiveKit room as a server-side bot participant."""
        ...

    def speakers(self) -> AsyncIterator[tuple[str, AsyncIterator[bytes]]]:
        """
        Um par (identidade, quadros) por TRILHA de áudio transcrita que aparece na sala — o
        cliente e cada atendente humano (`TRANSCRIBED_PREFIXES`), cada um no seu fluxo.

        Os quadros são PCM 16 bits a 48 kHz, crus. O fluxo de um falante termina quando a trilha
        dele termina; `speakers()` termina no `disconnect()`.
        """
        ...

    async def publish_audio(
        self, pcm_bytes: bytes, sample_rate: int = 24000
    ) -> None:
        """
        Inject 16-bit mono PCM audio into the room as a local (bot) track.

        pcm_bytes:   16-bit signed PCM at sample_rate Hz, mono.
        sample_rate: Hz of pcm_bytes (default 24000 — ElevenLabs output rate).

        Creates the LocalAudioTrack on first call if not already published.
        Returns early (without error) when `interrupt_audio()` is called mid-utterance.
        """
        ...

    def interrupt_audio(self) -> None:
        """Barge-in (VOZ-05 fatia 3): para a fala em curso e descarta o áudio já enfileirado."""
        ...

    def customer_present(self) -> bool:
        """O cliente está na sala — sem ele, falar é falar para ninguém (VOZ-05 fatia 3)."""
        ...

    async def wait_audio_playout(self) -> None:
        """Espera o áudio já entregue ao SFU terminar de tocar (ou ser interrompido)."""
        ...

    async def disconnect(self) -> None:
        """Leave the room and release all local tracks."""
        ...


# ── LiveKitRoomClient ──────────────────────────────────────────────────────────


class LiveKitRoomClient:
    """
    Production room client using the LiveKit Python server SDK (livekit.rtc).

    Install:  pip install "livekit[rtc]"
    Docs:     https://docs.livekit.io/reference/server-sdk-python/

    Graceful degradation when 'livekit' is not installed:
      - connect()                  → logs warning, no-op
      - speakers()                 → yields nothing (empty async iterator)
      - publish_audio()            → no-op

    This lets Channel Gateway start without the full LiveKit SDK for
    deployments running Phases A/B only.
    """

    def __init__(self) -> None:
        self._room:         Any | None                   = None  # rtc.Room
        self._audio_source: Any | None                   = None  # rtc.AudioSource
        self._audio_track:  Any | None                   = None  # rtc.LocalAudioTrack
        # (identidade, fila de quadros) por trilha transcrita; None = a sala acabou
        self._speakers_q:   asyncio.Queue[tuple[str, asyncio.Queue[bytes | None]] | None] = asyncio.Queue()
        self._track_queues: list[asyncio.Queue[bytes | None]] = []
        self._connected:    bool                         = False
        self._tts_sr:       int                          = 24000  # published sample rate
        self._interrupted:  bool                         = False

    async def connect(
        self,
        room_name:   str,
        identity:    str,
        token:       str,
        livekit_url: str,
    ) -> None:
        try:
            from livekit import rtc  # type: ignore[import-not-found]
        except ImportError:
            logger.warning(
                "livekit SDK not installed — WebRTC STT/TTS pipeline disabled. "
                "Install: pip install 'livekit[rtc]'"
            )
            return

        self._room = rtc.Room()

        @self._room.on("track_subscribed")
        def _on_track(track, publication, participant) -> None:
            # VOZ-05: uma fila POR TRILHA. Antes da fatia 2 era "a primeira trilha de áudio" de
            # quem fosse, e cliente e humano iriam para a mesma fila — transcrição misturada.
            # Na fatia 2 ficou só o cliente; na 4, cliente e humanos, cada um no seu canal.
            if track.kind != rtc.TrackKind.KIND_AUDIO:
                return
            ident = getattr(participant, "identity", "") or ""
            if not ident.startswith(TRANSCRIBED_PREFIXES):
                logger.info("webrtc room_client: trilha de audio de %r nao transcrita (nem cliente nem humano)", ident)
                return
            fila: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=500)
            self._track_queues.append(fila)
            self._speakers_q.put_nowait((ident, fila))
            disparar(self._consume_audio_track(track, rtc, fila, ident), nome="webrtc-audio-track")

        @self._room.on("disconnected")
        def _on_disconnected(*_) -> None:
            self._connected = False
            self._end_all()

        await self._room.connect(livekit_url, token)
        self._connected = True
        logger.info(
            "webrtc room_client connected: room=%s identity=%s livekit=%s",
            room_name, identity, livekit_url,
        )

    async def _consume_audio_track(self, track: Any, rtc: Any, fila: asyncio.Queue, ident: str) -> None:
        """Quadros da trilha de `ident` para a fila dele."""
        descartados = 0
        try:
            audio_stream = rtc.AudioStream(track)
            async for frame_event in audio_stream:
                try:
                    fila.put_nowait(bytes(frame_event.frame.data))
                except asyncio.QueueFull:
                    # o STT tolera lacuna curta; bloquear atrasaria a sala inteira
                    descartados += 1
        except Exception as exc:
            logger.debug("webrtc room_client: trilha de %s terminou: %s", ident, exc)
        finally:
            if descartados:
                logger.warning("webrtc room_client: %d quadro(s) de %s descartados (fila cheia — STT atrasado)",
                               descartados, ident)
            _fim(fila)

    async def speakers(self) -> AsyncIterator[tuple[str, AsyncIterator[bytes]]]:  # type: ignore[override]
        while True:
            item = await self._speakers_q.get()
            if item is None:
                return
            ident, fila = item
            yield ident, _drena(fila)

    def _end_all(self) -> None:
        for fila in self._track_queues:
            _fim(fila)
        self._speakers_q.put_nowait(None)

    async def publish_audio(self, pcm_bytes: bytes, sample_rate: int = 24000) -> None:
        """Inject PCM audio into the room via a LocalAudioTrack."""
        if not self._connected or self._room is None or not pcm_bytes:
            return
        try:
            from livekit import rtc  # type: ignore[import-not-found]
        except ImportError:
            return

        # Create + publish local track on first TTS call
        if self._audio_source is None:
            self._tts_sr      = sample_rate
            self._audio_source = rtc.AudioSource(
                sample_rate  = sample_rate,
                num_channels = 1,
            )
            self._audio_track = rtc.LocalAudioTrack.create_audio_track(
                "plughub-tts", self._audio_source
            )
            options = rtc.TrackPublishOptions(
                source = rtc.TrackSource.SOURCE_MICROPHONE,
            )
            await self._room.local_participant.publish_track(
                self._audio_track, options
            )
            logger.debug("webrtc room_client: TTS LocalAudioTrack published")

        # Quadros de 20 ms, não a fala num quadro só (VOZ-05 fatia 3): o SFU entregava o quadro
        # único, mas assim não havia ONDE parar — o barge-in precisa de uma fronteira a cada
        # poucos milissegundos. `capture_frame` bloqueia quando a fila do AudioSource (1 s)
        # enche, o que dá a cadência de tempo real sem relógio próprio.
        self._interrupted = False
        passo = self._tts_sr // 50 * 2
        for off in range(0, len(pcm_bytes), passo):
            if self._interrupted or not self._connected:
                return
            chunk = pcm_bytes[off:off + passo]
            if len(chunk) % 2:
                chunk = chunk[:-1]
            if not chunk:
                break
            frame = rtc.AudioFrame(
                data                = chunk,
                sample_rate         = self._tts_sr,
                num_channels        = 1,
                samples_per_channel = len(chunk) // 2,
            )
            await self._audio_source.capture_frame(frame)

    def customer_present(self) -> bool:
        if self._room is None or not self._connected:
            return False
        return any((getattr(p, "identity", "") or "").startswith(CUSTOMER_IDENTITY_PREFIX)
                   for p in self._room.remote_participants.values())

    def interrupt_audio(self) -> None:
        self._interrupted = True
        if self._audio_source is not None:
            try:
                self._audio_source.clear_queue()
            except Exception as exc:
                logger.warning("webrtc room_client: clear_queue falhou no barge-in: %s", exc)

    async def wait_audio_playout(self) -> None:
        if self._audio_source is not None and not self._interrupted:
            await self._audio_source.wait_for_playout()

    async def disconnect(self) -> None:
        """Disconnect from the LiveKit room."""
        self._connected = False
        self._end_all()
        if self._room is not None:
            try:
                await self._room.disconnect()
            except Exception as exc:
                logger.debug("webrtc room_client: disconnect error: %s", exc)
            self._room = None
        logger.info("webrtc room_client disconnected")


# ── MockRoomClient ─────────────────────────────────────────────────────────────


class MockRoomClient:
    """
    In-memory room client stub for unit tests — no network I/O.

    Usage:
        client = MockRoomClient()
        client.inject_audio(b"\\x00" * 960)   # push fake audio frame
        client.end_audio()                     # signal EOS

        # After test:
        assert client.connected_to == "plughub-{session_id}"
        assert client.published_chunks        # TTS was injected
        assert client.disconnected
    """

    def __init__(self) -> None:
        self.connected_to:     str              = ""
        self.connected:        bool             = False
        self.published_chunks: list[bytes]      = []
        self.disconnected:     bool             = False
        self.interrupts:       int              = 0
        self.customer_in_room: bool             = True
        self._speakers_q: asyncio.Queue[tuple[str, asyncio.Queue] | None] = asyncio.Queue()
        self._filas: dict[str, asyncio.Queue[bytes | None]] = {}

    # ── Test helpers ──────────────────────────────────────────────────────────

    CUSTOMER = f"{CUSTOMER_IDENTITY_PREFIX}c-mock"

    def _fila(self, identity: str) -> asyncio.Queue:
        if identity not in self._filas:
            self._filas[identity] = asyncio.Queue()
            self._speakers_q.put_nowait((identity, self._filas[identity]))
        return self._filas[identity]

    def inject_audio(self, chunk: bytes, identity: str | None = None) -> None:
        """Quadro falso de `identity` (default: o cliente); a trilha aparece no primeiro quadro."""
        self._fila(identity or self.CUSTOMER).put_nowait(chunk)

    def end_audio(self) -> None:
        """Fim de todas as trilhas e da sala."""
        for fila in self._filas.values():
            fila.put_nowait(None)
        self._speakers_q.put_nowait(None)

    # ── IWebRTCRoomClient interface ───────────────────────────────────────────

    async def connect(
        self,
        room_name:   str,
        identity:    str,
        token:       str,
        livekit_url: str,
    ) -> None:
        self.connected_to = room_name
        self.connected    = True

    async def speakers(self) -> AsyncIterator[tuple[str, AsyncIterator[bytes]]]:  # type: ignore[override]
        while True:
            item = await self._speakers_q.get()
            if item is None:
                return
            yield item[0], _drena(item[1])

    async def publish_audio(self, pcm_bytes: bytes, sample_rate: int = 24000) -> None:
        self.published_chunks.append(pcm_bytes)

    def interrupt_audio(self) -> None:
        self.interrupts += 1

    def customer_present(self) -> bool:
        return self.customer_in_room

    async def wait_audio_playout(self) -> None:
        return None

    async def disconnect(self) -> None:
        self.disconnected = True
        self.connected    = False
        self.end_audio()
