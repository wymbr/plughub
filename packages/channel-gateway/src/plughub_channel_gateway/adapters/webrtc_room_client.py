"""
adapters/webrtc_room_client.py
Server-side LiveKit room participation for Arc 15 WebRTC STT/TTS pipeline.

Architecture: docs/arcos/arc15-webrtc.md § Phase C

The room client connects to a LiveKit room as a server-side bot participant:
  - subscribe_customer_audio() → yields 48kHz PCM frames for resample + STT
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

logger = logging.getLogger("plughub.channel-gateway.webrtc.room_client")


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


# ── Protocol ───────────────────────────────────────────────────────────────────


@runtime_checkable
class IWebRTCRoomClient(Protocol):
    """
    Server-side LiveKit room participant (bot leg) for the STT/TTS pipeline.

    One instance per active WebRTC session with medium=voice or medium=video.
    Lifecycle:
        await client.connect(room_name, identity, token, url)
        async for chunk in client.subscribe_customer_audio():
            ulaw = resample_pcm_48_to_8(chunk)
            # feed ulaw to STT
        await client.publish_audio(pcm_24k_bytes)  # TTS injection
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

    def subscribe_customer_audio(self) -> AsyncIterator[bytes]:
        """
        Yield raw 48kHz 16-bit PCM frames from the customer audio track.

        Each yielded bytes value is one LiveKit AudioFrame's raw data
        (flattened to bytes, original channel count preserved).
        Caller must pass through resample_pcm_48_to_8() before STT.

        Terminates on track end or disconnect().
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
        """
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
      - subscribe_customer_audio() → yields nothing (empty async iterator)
      - publish_audio()            → no-op

    This lets Channel Gateway start without the full LiveKit SDK for
    deployments running Phases A/B only.
    """

    def __init__(self) -> None:
        self._room:         Any | None                   = None  # rtc.Room
        self._audio_source: Any | None                   = None  # rtc.AudioSource
        self._audio_track:  Any | None                   = None  # rtc.LocalAudioTrack
        self._audio_queue:  asyncio.Queue[bytes | None]  = asyncio.Queue(maxsize=500)
        self._connected:    bool                         = False
        self._tts_sr:       int                          = 24000  # published sample rate

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
            # Subscribe to the first audio track (customer's microphone)
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                asyncio.ensure_future(self._consume_audio_track(track, rtc))

        @self._room.on("disconnected")
        def _on_disconnected(*_) -> None:
            self._connected = False
            self._audio_queue.put_nowait(None)  # drain

        await self._room.connect(livekit_url, token)
        self._connected = True
        logger.info(
            "webrtc room_client connected: room=%s identity=%s livekit=%s",
            room_name, identity, livekit_url,
        )

    async def _consume_audio_track(self, track: Any, rtc: Any) -> None:
        """Push LiveKit AudioFrame bytes into the queue for subscribe_customer_audio."""
        try:
            audio_stream = rtc.AudioStream(track)
            async for frame_event in audio_stream:
                frame = frame_event.frame
                chunk = bytes(frame.data)
                try:
                    self._audio_queue.put_nowait(chunk)
                except asyncio.QueueFull:
                    # Drop rather than block — STT tolerates small gaps
                    logger.debug("webrtc room_client: audio queue full — dropping frame")
        except Exception as exc:
            logger.debug("webrtc room_client: customer audio track ended: %s", exc)
        finally:
            try:
                self._audio_queue.put_nowait(None)
            except asyncio.QueueFull:
                pass

    async def subscribe_customer_audio(self) -> AsyncIterator[bytes]:  # type: ignore[override]
        """Yield raw PCM frames from the customer audio track."""
        while True:
            chunk = await self._audio_queue.get()
            if chunk is None:
                return
            yield chunk

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

        samples_per_channel = len(pcm_bytes) // 2  # 16-bit = 2 bytes/sample
        frame = rtc.AudioFrame(
            data                = pcm_bytes,
            sample_rate         = self._tts_sr,
            num_channels        = 1,
            samples_per_channel = samples_per_channel,
        )
        await self._audio_source.capture_frame(frame)

    async def disconnect(self) -> None:
        """Disconnect from the LiveKit room."""
        self._connected = False
        try:
            self._audio_queue.put_nowait(None)
        except asyncio.QueueFull:
            pass
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
        self._audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    # ── Test helpers ──────────────────────────────────────────────────────────

    def inject_audio(self, chunk: bytes) -> None:
        """Push a fake audio frame to be yielded by subscribe_customer_audio."""
        self._audio_queue.put_nowait(chunk)

    def end_audio(self) -> None:
        """Signal end of audio stream (EOS sentinel)."""
        self._audio_queue.put_nowait(None)

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

    async def subscribe_customer_audio(self) -> AsyncIterator[bytes]:  # type: ignore[override]
        while True:
            chunk = await self._audio_queue.get()
            if chunk is None:
                return
            yield chunk

    async def publish_audio(self, pcm_bytes: bytes, sample_rate: int = 24000) -> None:
        self.published_chunks.append(pcm_bytes)

    async def disconnect(self) -> None:
        self.disconnected = True
        self.connected    = False
        try:
            self._audio_queue.put_nowait(None)
        except asyncio.QueueFull:
            pass
