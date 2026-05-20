"""
adapters/voice_provider.py
Provider abstractions for voice channels.

Architecture: channel-gateway-multi-channel.md § 9.7 / 9.11

Three independent Protocol interfaces:
  IVoiceProvider  — CPaaS operations (Twilio / Telnyx)
  ISTTProvider    — Speech-to-Text (Deepgram streaming)
  ITTSProvider    — Text-to-Speech (ElevenLabs / Twilio Say / Deepgram Aura)

Concrete implementations:
  TwilioVoiceProvider     — Twilio REST API + TwiML generation (trunk/PSTN only)
  DeepgramSTTProvider     — Deepgram WebSocket streaming STT (primary)
  TwilioSayTTSProvider    — Twilio native <Say> TTS (fallback, no extra API key)
  DeepgramAuraTTSProvider — Deepgram Aura REST TTS (alternative high-quality)
  ElevenLabsTTSProvider   — ElevenLabs REST TTS (primary high-quality)
  FallbackSTTProvider     — Wraps an ordered list of ISTTProvider; tries in sequence
  FallbackTTSProvider     — Wraps an ordered list of ITTSProvider; tries in sequence
  Mock*                   — in-memory stubs for unit tests

Provider selection (VoiceAdapter factory):
  TTS primary: ElevenLabs (when api_key set) wrapped in FallbackTTSProvider
               with TwilioSay as automatic last-resort fallback
  STT primary: Deepgram (when api_key set) wrapped in FallbackSTTProvider

Adding a new CPaaS (Telnyx):
  1. Implement IVoiceProvider Protocol
  2. Set PLUGHUB_VOICE_PROVIDER=telnyx env var
  3. Register in VoiceAdapter._build_providers()
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import urllib.parse
import uuid
from dataclasses import dataclass, field
from typing import AsyncIterator, Protocol, runtime_checkable
from xml.sax.saxutils import escape as xml_escape

import httpx

logger = logging.getLogger("plughub.channel-gateway.voice.provider")

# ── Data types ────────────────────────────────────────────────────────────────


@dataclass
class CallInfo:
    """Minimal call metadata returned by IVoiceProvider.create_call / answer_call."""
    call_sid:       str
    from_number:    str
    to_number:      str
    direction:      str             # "inbound" | "outbound"
    conference_sid: str | None = None


@dataclass
class STTResult:
    """Single STT result emitted by ISTTProvider.stream()."""
    transcript:  str
    is_final:    bool
    confidence:  float = 1.0
    start_ms:    int   = 0
    end_ms:      int   = 0


# ── Protocol interfaces ───────────────────────────────────────────────────────


@runtime_checkable
class IVoiceProvider(Protocol):
    """CPaaS operations — conference bridge + call control."""

    async def verify_signature(
        self, url: str, params: dict, signature: str
    ) -> bool:
        """Validate HMAC-SHA1 (Twilio) or equivalent webhook signature."""
        ...

    def generate_inbound_twiml(
        self,
        session_id:  str,
        host:        str,
        wait_url:    str = "",
    ) -> str:
        """
        Return TwiML / TXML string to answer an inbound call:
        - open Media Streams WebSocket for STT
        - place customer in conference room plughub-{session_id}
        """
        ...

    def generate_tts_twiml(self, text: str, voice: str) -> str:
        """Return TwiML <Response><Say>...</Say></Response> for a TTS snippet."""
        ...

    async def get_conference_sid(self, friendly_name: str) -> str | None:
        """Look up a conference by friendly name. Returns SID or None."""
        ...

    async def announce_tts(self, conference_sid: str, tts_url: str) -> None:
        """Play TTS audio URL to all participants in the conference."""
        ...

    async def add_participant(
        self,
        conference_sid: str,
        to:             str,
        from_:          str,
        **kwargs,
    ) -> str:
        """Add a leg (human agent SIP/WebRTC) to the conference. Returns participant SID."""
        ...

    async def remove_participant(
        self, conference_sid: str, participant_sid: str
    ) -> None: ...

    async def hangup(self, call_sid: str) -> None:
        """Terminate a call leg."""
        ...

    async def create_call(
        self,
        to:          str,
        from_:       str,
        callback_url: str,
    ) -> str:
        """Initiate an outbound call. Returns call SID."""
        ...

    async def start_recording(
        self,
        conference_sid: str,
        dual_channel:   bool = True,
        metadata:       dict | None = None,
    ) -> str:
        """Start recording the conference. Returns recording SID."""
        ...

    async def stop_recording(self, recording_sid: str) -> None: ...


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
        Convert text to audio bytes (PCM/MP3).
        Returns None if this provider delegates TTS to the CPaaS <Say> verb
        (TwilioSayTTSProvider) — caller must use announce_tts instead.
        """
        ...


# ── TwilioVoiceProvider ───────────────────────────────────────────────────────


class TwilioVoiceProvider:
    """
    Twilio REST API + TwiML.

    Auth: HMAC-SHA1 over URL + sorted params (X-Twilio-Signature header).
    TTS:  conference announce_url → /voice/tts/{tts_id} endpoint.
    STT:  handled externally by DeepgramSTTProvider via Media Streams WS.
    """

    _BASE_URL = "https://api.twilio.com/2010-04-01"

    def __init__(
        self,
        account_sid: str,
        auth_token:  str,
        from_number: str,
        dev_mode:    bool = False,
    ) -> None:
        self._account_sid = account_sid
        self._auth_token  = auth_token
        self._from_number = from_number
        self._dev_mode    = dev_mode
        self._http        = httpx.AsyncClient(
            auth=(account_sid, auth_token),
            base_url=f"{self._BASE_URL}/Accounts/{account_sid}",
            timeout=10.0,
        )

    async def verify_signature(
        self, url: str, params: dict, signature: str
    ) -> bool:
        if self._dev_mode and not self._auth_token:
            logger.debug("dev_mode: bypassing Twilio signature verification")
            return True
        s = url + "".join(
            f"{k}{v}" for k, v in sorted(params.items())
        )
        computed = base64.b64encode(
            hmac.new(
                self._auth_token.encode(),
                s.encode(),
                hashlib.sha1,
            ).digest()
        ).decode()
        return hmac.compare_digest(computed, signature)

    def generate_inbound_twiml(
        self,
        session_id: str,
        host:       str,
        wait_url:   str = "",
    ) -> str:
        """
        TwiML response for inbound call:
        1. <Start><Stream> → opens Media Streams WS for STT audio
        2. <Dial><Conference> → places customer in named conference room
        """
        stream_url = f"wss://{_strip_scheme(host)}/voice/media"
        conf_name  = xml_escape(f"plughub-{session_id}")
        wait_attr  = f'waitUrl="{xml_escape(wait_url)}" ' if wait_url else 'waitUrl="" '
        cb_url     = xml_escape(f"{host}/webhooks/voice/status")
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            "<Response>\n"
            "  <Start>\n"
            f'    <Stream url="{stream_url}" track="inbound_track">\n'
            f'      <Parameter name="session_id" value="{xml_escape(session_id)}"/>\n'
            "    </Stream>\n"
            "  </Start>\n"
            "  <Dial>\n"
            f'    <Conference {wait_attr}beep="false"\n'
            '                startConferenceOnEnter="true"\n'
            '                statusCallbackEvent="leave join end"\n'
            f'                statusCallback="{cb_url}">\n'
            f"      {conf_name}\n"
            "    </Conference>\n"
            "  </Dial>\n"
            "</Response>"
        )

    def generate_tts_twiml(self, text: str, voice: str) -> str:
        """TwiML <Say> snippet served at /voice/tts/{tts_id}."""
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            "<Response>\n"
            f'  <Say voice="{xml_escape(voice)}">{xml_escape(text)}</Say>\n'
            "</Response>"
        )

    async def get_conference_sid(self, friendly_name: str) -> str | None:
        try:
            resp = await self._http.get(
                "/Conferences.json",
                params={"FriendlyName": friendly_name, "Status": "in-progress"},
            )
            resp.raise_for_status()
            data = resp.json()
            conferences = data.get("conferences", [])
            return conferences[0]["sid"] if conferences else None
        except Exception as exc:
            logger.warning("get_conference_sid failed: %s", exc)
            return None

    async def announce_tts(self, conference_sid: str, tts_url: str) -> None:
        """Play TTS URL to all participants in the conference."""
        try:
            resp = await self._http.post(
                f"/Conferences/{conference_sid}.json",
                data={"AnnounceUrl": tts_url, "AnnounceMethod": "GET"},
            )
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("announce_tts failed (conf=%s): %s", conference_sid, exc)

    async def add_participant(
        self,
        conference_sid: str,
        to:             str,
        from_:          str,
        **kwargs,
    ) -> str:
        resp = await self._http.post(
            f"/Conferences/{conference_sid}/Participants.json",
            data={"To": to, "From": from_ or self._from_number, **kwargs},
        )
        resp.raise_for_status()
        return resp.json()["call_sid"]

    async def remove_participant(
        self, conference_sid: str, participant_sid: str
    ) -> None:
        try:
            resp = await self._http.delete(
                f"/Conferences/{conference_sid}/Participants/{participant_sid}.json"
            )
            resp.raise_for_status()
        except Exception as exc:
            logger.warning(
                "remove_participant failed (conf=%s part=%s): %s",
                conference_sid, participant_sid, exc,
            )

    async def hangup(self, call_sid: str) -> None:
        try:
            resp = await self._http.post(
                f"/Calls/{call_sid}.json",
                data={"Status": "completed"},
            )
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("hangup failed (call=%s): %s", call_sid, exc)

    async def create_call(
        self,
        to:           str,
        from_:        str,
        callback_url: str,
    ) -> str:
        resp = await self._http.post(
            "/Calls.json",
            data={
                "To":         to,
                "From":       from_ or self._from_number,
                "Url":        callback_url,
                "Method":     "POST",
                "StatusCallbackMethod": "POST",
            },
        )
        resp.raise_for_status()
        return resp.json()["sid"]

    async def start_recording(
        self,
        conference_sid: str,
        dual_channel:   bool = True,
        metadata:       dict | None = None,
    ) -> str:
        data: dict = {
            "RecordingChannels": "dual" if dual_channel else "mono",
            "RecordingStatusCallbackMethod": "POST",
            "Trim": "trim-silence",
        }
        if metadata:
            # Twilio supports up to 5 metadata parameters via RecordingStatusCallbackEvent
            # We pass metadata as JSON in the StatusCallback URL query string
            data["RecordingStatusCallback"] = ""  # will be set by VoiceAdapter
        resp = await self._http.post(
            f"/Conferences/{conference_sid}/Recordings.json",
            data=data,
        )
        resp.raise_for_status()
        return resp.json()["sid"]

    async def stop_recording(self, recording_sid: str) -> None:
        try:
            resp = await self._http.post(
                f"/Recordings/{recording_sid}.json",
                data={"Status": "stopped"},
            )
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("stop_recording failed (rec=%s): %s", recording_sid, exc)


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


# ── TwilioSayTTSProvider ──────────────────────────────────────────────────────


class TwilioSayTTSProvider:
    """
    TTS via Twilio's built-in <Say> verb — no external TTS API required.

    VoiceAdapter stores text in Redis, then tells Twilio to fetch
    /voice/tts/{tts_id} which returns a <Say> TwiML snippet.
    synthesize() returns None, signaling the caller to use announce_tts.
    """

    async def synthesize(
        self,
        text:     str,
        voice_id: str | None = None,
    ) -> bytes | None:
        # Delegate to CPaaS <Say> — no local synthesis
        return None


# ── DeepgramAuraTTSProvider ───────────────────────────────────────────────────


class DeepgramAuraTTSProvider:
    """
    High-quality TTS via Deepgram Aura REST API.
    Returns MP3 bytes that the VoiceAdapter can serve via a URL.
    """

    _BASE_URL = "https://api.deepgram.com/v1/speak"

    def __init__(self, api_key: str, voice_id: str = "aura-asteria-en") -> None:
        self._api_key  = api_key
        self._voice_id = voice_id

    async def synthesize(
        self,
        text:     str,
        voice_id: str | None = None,
    ) -> bytes | None:
        if not self._api_key:
            return None
        voice = voice_id or self._voice_id
        params = {"model": voice}
        headers = {
            "Authorization": f"Token {self._api_key}",
            "Content-Type":  "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    self._BASE_URL,
                    headers=headers,
                    params=params,
                    json={"text": text},
                )
                resp.raise_for_status()
                return resp.content
        except Exception as exc:
            logger.warning("DeepgramAuraTTS failed: %s", exc)
            return None


# ── ElevenLabsTTSProvider ─────────────────────────────────────────────────────


class ElevenLabsTTSProvider:
    """
    High-quality TTS via ElevenLabs REST API.

    Returns MP3 bytes that VoiceAdapter stores in Redis and serves via
    /voice/tts-audio/{tts_id}.  Supports all ElevenLabs multilingual voices.

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
        Returns None on failure so FallbackTTSProvider advances to the next provider.
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


# ── FallbackSTTProvider ───────────────────────────────────────────────────────


class FallbackSTTProvider:
    """
    STT with automatic provider fallback.

    Tries providers in order.  Moves to the next provider when the current one:
      - raises an exception during streaming, or
      - produces zero results for the entire audio stream.

    Usage:
        stt = FallbackSTTProvider([
            DeepgramSTTProvider(api_key="..."),
            MockSTTProvider(),   # last-resort silent fallback
        ])
    """

    def __init__(self, providers: list) -> None:
        if not providers:
            raise ValueError("FallbackSTTProvider requires at least one provider")
        self._providers = providers

    async def stream(
        self,
        audio_chunks: AsyncIterator[bytes],
        sample_rate:  int = 8000,
        language:     str = "pt-BR",
    ) -> AsyncIterator[STTResult]:
        """
        Try providers in order, yielding results from the first that succeeds.

        Because audio_chunks is a single-pass async iterator, we buffer audio
        bytes from a tee so each subsequent provider gets the full stream.
        """
        # Buffer audio so we can replay for fallback providers.
        audio_buffer: list[bytes] = []
        results_yielded = 0

        async def _buffered(source: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
            async for chunk in source:
                audio_buffer.append(chunk)
                yield chunk

        async def _replay() -> AsyncIterator[bytes]:
            for chunk in audio_buffer:
                yield chunk

        for index, provider in enumerate(self._providers):
            is_last = index == len(self._providers) - 1
            source  = _buffered(audio_chunks) if index == 0 else _replay()
            try:
                async for result in provider.stream(source, sample_rate, language):
                    results_yielded += 1
                    yield result
                # If we got results, we're done.
                if results_yielded > 0:
                    return
                # Zero results from this provider — try next.
                if not is_last:
                    logger.warning(
                        "FallbackSTT: provider[%d] %s returned no results — trying next",
                        index, type(provider).__name__,
                    )
            except Exception as exc:
                if is_last:
                    logger.warning(
                        "FallbackSTT: provider[%d] %s raised %s — no more providers",
                        index, type(provider).__name__, exc,
                    )
                    return
                logger.warning(
                    "FallbackSTT: provider[%d] %s raised %s — trying next",
                    index, type(provider).__name__, exc,
                )


# ── FallbackTTSProvider ───────────────────────────────────────────────────────


class FallbackTTSProvider:
    """
    TTS with automatic provider fallback.

    Tries providers in order.  Advances to the next provider when the current one:
      - raises an exception, or
      - returns None.

    TwilioSayTTSProvider always returns None (delegates to CPaaS <Say>), so it
    acts as a transparent last-resort fallback that never blocks the chain.

    Usage:
        tts = FallbackTTSProvider([
            ElevenLabsTTSProvider(api_key="..."),
            TwilioSayTTSProvider(),   # always succeeds by returning None → CPaaS Say
        ])
    """

    def __init__(self, providers: list) -> None:
        if not providers:
            raise ValueError("FallbackTTSProvider requires at least one provider")
        self._providers = providers

    async def synthesize(
        self,
        text:     str,
        voice_id: str | None = None,
    ) -> bytes | None:
        """
        Try providers in order, returning the first non-None bytes result.
        If every provider returns None (e.g. all are TwilioSay-style), returns None
        so the caller falls back to CPaaS <Say>.
        """
        for index, provider in enumerate(self._providers):
            try:
                result = await provider.synthesize(text, voice_id)
                if result is not None:
                    return result
                # Provider returned None — advance to next (or final None).
                logger.debug(
                    "FallbackTTS: provider[%d] %s returned None — trying next",
                    index, type(provider).__name__,
                )
            except Exception as exc:
                logger.warning(
                    "FallbackTTS: provider[%d] %s raised %s — trying next",
                    index, type(provider).__name__, exc,
                )
        return None


# ── Mocks ─────────────────────────────────────────────────────────────────────


class MockVoiceProvider:
    """
    In-memory stub for unit tests — no network I/O.

    Records all calls for assertion: hung_up, recordings_started, participants_added.
    """

    def __init__(self, verify_result: bool = True) -> None:
        self._verify_result    = verify_result
        self.calls_created:    list[dict]          = []
        self.hung_up:          list[str]           = []
        self.participants_added: list[dict]        = []
        self.participants_removed: list[dict]      = []
        self.recordings_started: list[dict]        = []
        self.recordings_stopped: list[str]         = []
        self.tts_announced:    list[dict]          = []
        self._conf_sid:        str                 = "CF_mock_conference"
        self._rec_counter:     int                 = 0
        self._call_counter:    int                 = 0

    async def verify_signature(
        self, url: str, params: dict, signature: str
    ) -> bool:
        return self._verify_result

    def generate_inbound_twiml(
        self, session_id: str, host: str, wait_url: str = ""
    ) -> str:
        return f'<Response><Say>mock inbound {session_id}</Say></Response>'

    def generate_tts_twiml(self, text: str, voice: str) -> str:
        return f'<Response><Say voice="{voice}">{text}</Say></Response>'

    async def get_conference_sid(self, friendly_name: str) -> str | None:
        return self._conf_sid

    async def announce_tts(self, conference_sid: str, tts_url: str) -> None:
        self.tts_announced.append({"conference_sid": conference_sid, "url": tts_url})

    async def add_participant(
        self, conference_sid: str, to: str, from_: str, **kwargs
    ) -> str:
        sid = f"PA_mock_{len(self.participants_added)}"
        self.participants_added.append({
            "conference_sid": conference_sid,
            "to": to, "from": from_, "sid": sid,
        })
        return sid

    async def remove_participant(
        self, conference_sid: str, participant_sid: str
    ) -> None:
        self.participants_removed.append({
            "conference_sid": conference_sid,
            "participant_sid": participant_sid,
        })

    async def hangup(self, call_sid: str) -> None:
        self.hung_up.append(call_sid)

    async def create_call(
        self, to: str, from_: str, callback_url: str
    ) -> str:
        self._call_counter += 1
        sid = f"CA_mock_{self._call_counter:04d}"
        self.calls_created.append({"to": to, "from": from_, "url": callback_url, "sid": sid})
        return sid

    async def start_recording(
        self,
        conference_sid: str,
        dual_channel:   bool = True,
        metadata:       dict | None = None,
    ) -> str:
        self._rec_counter += 1
        sid = f"RE_mock_{self._rec_counter:04d}"
        self.recordings_started.append({
            "conference_sid": conference_sid,
            "dual_channel": dual_channel,
            "metadata": metadata or {},
            "sid": sid,
        })
        return sid

    async def stop_recording(self, recording_sid: str) -> None:
        self.recordings_stopped.append(recording_sid)


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

    synthesize_returns_none=True simulates TwilioSayTTSProvider behaviour.
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


# ── Helpers ───────────────────────────────────────────────────────────────────


def _strip_scheme(host: str) -> str:
    """Remove https:// or http:// prefix for use in wss:// URL."""
    return host.removeprefix("https://").removeprefix("http://")
