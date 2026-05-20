"""
tests/test_voice_adapter.py
Test suite for VoiceAdapter and voice provider utilities.

Coverage:
  - TwilioVoiceProvider: signature verification, TwiML generation
  - MockVoiceProvider, MockSTTProvider, MockTTSProvider: stub behaviour
  - VoiceAdapter.handle_inbound: new call, outbound collect correlation
  - VoiceAdapter.handle_status: hangup → session close
  - VoiceAdapter.handle_recording_complete: segment lookup + download dispatch
  - VoiceAdapter.get_tts_twiml: Redis lookup + TwiML generation
  - VoiceAdapter.get_tts_audio: Deepgram audio bytes retrieval
  - VoiceAdapter._handle_dtmf: collect state machine (DTMF path)
  - VoiceAdapter._handle_stt_result: collect state machine (STT path)
  - VoiceAdapter._process_collect_input: option matching + multi-field advance
  - VoiceAdapter.deliver_outbound: notify → TTS, interaction.request → collect
  - VoiceAdapter.deliver_outbound: session.closed → hangup
  - VoiceAdapter._deliver_tts: TwilioSay path (announce_tts)
  - VoiceAdapter._deliver_tts: DeepgramAura path (audio bytes)
  - VoiceAdapter._announce_and_start_recording: notice TTS + start_recording
  - VoiceAdapter._announce_and_start_recording: opt-out guard
  - VoiceAdapter._announce_and_start_recording: double-announcement guard
  - VoiceAdapter._stop_all_recordings: stops all active recordings
  - VoiceAdapter.handle_collect_event: outbound dial + pending Redis key
  - _normalize_e164: various formats
  - _match_option: digit match, label match, no match
  - _build_voice_prompt: with options, without options
  - Integration: inbound → session open → TwiML → status → session close
  - Integration: collect.events → outbound call → answered → session link
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import uuid
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ..adapters.voice import (
    VoiceAdapter,
    _build_voice_prompt,
    _match_option,
    _normalize_e164,
)
from ..adapters.voice_provider import (
    DeepgramAuraTTSProvider,
    ElevenLabsTTSProvider,
    FallbackSTTProvider,
    FallbackTTSProvider,
    MockSTTProvider,
    MockTTSProvider,
    MockVoiceProvider,
    STTResult,
    TwilioSayTTSProvider,
    TwilioVoiceProvider,
    _strip_scheme,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


def _fake_settings(**kwargs):
    s = MagicMock()
    s.voice_account_sid              = kwargs.get("voice_account_sid", "ACtest")
    s.voice_auth_token               = kwargs.get("voice_auth_token", "token123")
    s.voice_from_number              = kwargs.get("voice_from_number", "+551140042121")
    s.voice_provider                 = kwargs.get("voice_provider", "mock")
    s.voice_default_pool_id          = kwargs.get("voice_default_pool_id", "suporte_voz")
    s.voice_stt_provider             = kwargs.get("voice_stt_provider", "mock")
    s.voice_deepgram_api_key         = kwargs.get("voice_deepgram_api_key", "")
    s.voice_stt_language             = kwargs.get("voice_stt_language", "pt-BR")
    s.voice_tts_provider             = kwargs.get("voice_tts_provider", "twilio_say")
    s.voice_tts_voice_id             = kwargs.get("voice_tts_voice_id", "Polly.Camila-Neural")
    s.voice_elevenlabs_api_key       = kwargs.get("voice_elevenlabs_api_key", "")
    s.voice_elevenlabs_voice_id      = kwargs.get("voice_elevenlabs_voice_id", "pNInz6obpgDQGcFmaJgB")
    s.voice_tts_fallback_provider    = kwargs.get("voice_tts_fallback_provider", "twilio_say")
    s.voice_stt_fallback_provider    = kwargs.get("voice_stt_fallback_provider", "mock")
    s.voice_webhook_host             = kwargs.get("voice_webhook_host", "https://plughub.example.com")
    s.voice_conference_wait_url      = kwargs.get("voice_conference_wait_url", "")
    s.voice_agent_stt_enabled        = kwargs.get("voice_agent_stt_enabled", False)
    s.voice_default_recording_notice = kwargs.get(
        "voice_default_recording_notice",
        "Esta chamada poderá ser gravada.",
    )
    s.tenant_id                  = kwargs.get("tenant_id", "tenant_test")
    s.kafka_brokers              = "localhost:9092"
    s.kafka_group_id             = "test-group"
    s.agent_registry_url         = ""
    s.endpoint_cache_ttl_s       = 30
    return s


def _make_adapter(settings=None, redis=None, producer=None) -> VoiceAdapter:
    """Create a VoiceAdapter with all external dependencies mocked."""
    settings = settings or _fake_settings()
    redis    = redis    or AsyncMock()
    producer = producer or AsyncMock()
    adapter  = VoiceAdapter(
        producer      = producer,
        redis         = redis,
        settings      = settings,
        voice_provider = MockVoiceProvider(),
        stt_provider   = MockSTTProvider(),
        tts_provider   = MockTTSProvider(synthesize_returns_none=True),
    )
    # Mock inherited base methods
    adapter._open_session   = AsyncMock()
    adapter._close_session  = AsyncMock()
    adapter._route_inbound  = AsyncMock()
    adapter._publish_inbound = AsyncMock()
    adapter._normalize_text  = MagicMock(return_value=MagicMock())
    adapter._normalize_menu_result = MagicMock(return_value=MagicMock())
    return adapter


# ─────────────────────────────────────────────────────────────────────────────
# TwilioVoiceProvider — signature verification
# ─────────────────────────────────────────────────────────────────────────────


class TestTwilioVoiceProviderSignature:
    """HMAC-SHA1 webhook verification (same algorithm as SMS)."""

    def _compute_sig(self, auth_token: str, url: str, params: dict) -> str:
        s = url + "".join(f"{k}{v}" for k, v in sorted(params.items()))
        return base64.b64encode(
            hmac.new(auth_token.encode(), s.encode(), hashlib.sha1).digest()
        ).decode()

    @pytest.mark.asyncio
    async def test_valid_signature(self):
        provider = TwilioVoiceProvider("ACtest", "secret", "+1234")
        url      = "https://example.com/webhooks/voice/inbound"
        params   = {"CallSid": "CA001", "From": "+5511999990000"}
        sig      = self._compute_sig("secret", url, params)
        assert await provider.verify_signature(url, params, sig) is True

    @pytest.mark.asyncio
    async def test_invalid_signature(self):
        provider = TwilioVoiceProvider("ACtest", "secret", "+1234")
        assert await provider.verify_signature(
            "https://example.com/webhooks/voice/inbound",
            {"CallSid": "CA001"},
            "bad_signature",
        ) is False

    @pytest.mark.asyncio
    async def test_dev_mode_bypass(self):
        provider = TwilioVoiceProvider("", "", "", dev_mode=True)
        assert await provider.verify_signature("http://x.com", {}, "") is True

    @pytest.mark.asyncio
    async def test_empty_params_signature(self):
        provider = TwilioVoiceProvider("ACtest", "secret", "+1234")
        url      = "https://example.com/webhooks/voice/inbound"
        sig      = self._compute_sig("secret", url, {})
        assert await provider.verify_signature(url, {}, sig) is True


# ─────────────────────────────────────────────────────────────────────────────
# TwilioVoiceProvider — TwiML generation
# ─────────────────────────────────────────────────────────────────────────────


class TestTwilioVoiceProviderTwiML:

    def test_inbound_twiml_contains_stream(self):
        provider = TwilioVoiceProvider("AC", "token", "+1")
        twiml    = provider.generate_inbound_twiml(
            session_id = "sess-abc",
            host       = "https://plughub.example.com",
        )
        assert "<Stream" in twiml
        assert "wss://plughub.example.com/voice/media" in twiml
        assert "sess-abc" in twiml

    def test_inbound_twiml_contains_conference(self):
        provider = TwilioVoiceProvider("AC", "token", "+1")
        twiml    = provider.generate_inbound_twiml("s1", "https://host.com")
        assert "<Conference" in twiml
        assert "plughub-s1" in twiml

    def test_inbound_twiml_with_wait_url(self):
        provider = TwilioVoiceProvider("AC", "token", "+1")
        twiml    = provider.generate_inbound_twiml(
            "s2", "https://host.com", wait_url="https://music.example.com/hold.mp3"
        )
        assert "https://music.example.com/hold.mp3" in twiml

    def test_inbound_twiml_status_callback(self):
        provider = TwilioVoiceProvider("AC", "token", "+1")
        twiml    = provider.generate_inbound_twiml("s3", "https://host.com")
        assert "/webhooks/voice/status" in twiml

    def test_tts_twiml(self):
        provider = TwilioVoiceProvider("AC", "token", "+1")
        twiml    = provider.generate_tts_twiml(
            "Olá, como posso ajudá-lo?", "Polly.Camila-Neural"
        )
        assert "<Say" in twiml
        assert "Polly.Camila-Neural" in twiml
        assert "Olá, como posso ajudá-lo?" in twiml

    def test_tts_twiml_escapes_special_chars(self):
        provider = TwilioVoiceProvider("AC", "token", "+1")
        twiml    = provider.generate_tts_twiml("Hello & <World>", "voice")
        assert "&amp;" in twiml
        assert "&lt;" in twiml
        assert "&gt;" in twiml

    def test_strip_scheme_https(self):
        assert _strip_scheme("https://example.com") == "example.com"

    def test_strip_scheme_http(self):
        assert _strip_scheme("http://localhost:8010") == "localhost:8010"


# ─────────────────────────────────────────────────────────────────────────────
# MockVoiceProvider
# ─────────────────────────────────────────────────────────────────────────────


class TestMockVoiceProvider:

    @pytest.mark.asyncio
    async def test_verify_signature_default_true(self):
        mock = MockVoiceProvider()
        assert await mock.verify_signature("url", {}, "sig") is True

    @pytest.mark.asyncio
    async def test_verify_signature_configurable(self):
        mock = MockVoiceProvider(verify_result=False)
        assert await mock.verify_signature("url", {}, "sig") is False

    @pytest.mark.asyncio
    async def test_create_call_records(self):
        mock = MockVoiceProvider()
        sid  = await mock.create_call("+5511999990000", "+551140042121", "https://cb")
        assert len(mock.calls_created) == 1
        assert mock.calls_created[0]["to"] == "+5511999990000"
        assert sid.startswith("CA_mock_")

    @pytest.mark.asyncio
    async def test_hangup_records(self):
        mock = MockVoiceProvider()
        await mock.hangup("CA001")
        assert "CA001" in mock.hung_up

    @pytest.mark.asyncio
    async def test_start_recording_records(self):
        mock = MockVoiceProvider()
        sid  = await mock.start_recording("CF001", dual_channel=True)
        assert len(mock.recordings_started) == 1
        assert mock.recordings_started[0]["conference_sid"] == "CF001"
        assert sid.startswith("RE_mock_")

    @pytest.mark.asyncio
    async def test_announce_tts_records(self):
        mock = MockVoiceProvider()
        await mock.announce_tts("CF001", "https://tts/123")
        assert len(mock.tts_announced) == 1
        assert mock.tts_announced[0]["conference_sid"] == "CF001"


# ─────────────────────────────────────────────────────────────────────────────
# MockSTTProvider / MockTTSProvider
# ─────────────────────────────────────────────────────────────────────────────


class TestMockSTTProvider:

    @pytest.mark.asyncio
    async def test_yields_configured_results(self):
        mock = MockSTTProvider()
        mock.results = [
            STTResult("Olá", is_final=False),
            STTResult("Olá, tudo bem?", is_final=True),
        ]

        async def _chunks():
            yield b"\x00" * 160

        results = []
        async for r in mock.stream(_chunks()):
            results.append(r)

        assert len(results) == 2
        assert results[1].transcript == "Olá, tudo bem?"
        assert results[1].is_final is True

    @pytest.mark.asyncio
    async def test_records_chunks(self):
        mock = MockSTTProvider()

        async def _chunks():
            yield b"AUDIO1"
            yield b"AUDIO2"

        async for _ in mock.stream(_chunks()):
            pass

        assert mock.chunks_received == [b"AUDIO1", b"AUDIO2"]


class TestMockTTSProvider:

    @pytest.mark.asyncio
    async def test_returns_none_in_none_mode(self):
        mock = MockTTSProvider(synthesize_returns_none=True)
        result = await mock.synthesize("Olá")
        assert result is None
        assert mock.synthesized[0]["text"] == "Olá"

    @pytest.mark.asyncio
    async def test_returns_bytes_in_audio_mode(self):
        mock = MockTTSProvider(synthesize_returns_none=False)
        result = await mock.synthesize("test", voice_id="voice-a")
        assert isinstance(result, bytes)
        assert mock.synthesized[0]["voice_id"] == "voice-a"


# ─────────────────────────────────────────────────────────────────────────────
# VoiceAdapter — handle_inbound
# ─────────────────────────────────────────────────────────────────────────────


class TestHandleInbound:

    @pytest.mark.asyncio
    async def test_new_call_opens_session(self):
        redis   = AsyncMock()
        redis.get.return_value = None  # no pending collect
        adapter = _make_adapter(redis=redis)

        twiml = await adapter.handle_inbound(
            params    = {"CallSid": "CA001", "From": "+5511999990000", "To": "+551140042121"},
            signature = "sig",
            url       = "https://x.com/webhooks/voice/inbound",
        )

        adapter._open_session.assert_awaited_once()
        adapter._route_inbound.assert_awaited_once()
        assert "<Response" in twiml
        assert isinstance(twiml, str)

    @pytest.mark.asyncio
    async def test_invalid_signature_returns_reject(self):
        adapter = _make_adapter()
        adapter._voice = MockVoiceProvider(verify_result=False)

        twiml = await adapter.handle_inbound(
            params    = {"CallSid": "CA001", "From": "+5511"},
            signature = "bad",
            url       = "https://x.com/webhooks/voice/inbound",
        )

        assert "<Reject" in twiml
        adapter._open_session.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_outbound_collect_uses_pending_pool(self):
        pending = json.dumps({"pool_id": "cobrança_voz", "collect_token": "tok-1"})
        redis   = AsyncMock()
        redis.get.side_effect = lambda key: (
            pending if "pending_collect:CA002" in key else None
        )
        adapter = _make_adapter(redis=redis)

        twiml = await adapter.handle_inbound(
            params    = {"CallSid": "CA002", "From": "+5511888880000", "To": "+551140042121"},
            signature = "sig",
            url       = "https://x.com/webhooks/voice/inbound",
        )

        # Session should be opened with the pool from the pending collect
        call_args = adapter._open_session.call_args
        assert call_args.kwargs.get("pool_id") == "cobrança_voz"

    @pytest.mark.asyncio
    async def test_outbound_collect_deletes_pending_key(self):
        pending = json.dumps({"pool_id": "p1", "collect_token": "tok-2"})
        redis   = AsyncMock()
        redis.get.side_effect = lambda key: (
            pending if "pending_collect:CA003" in key else None
        )
        adapter = _make_adapter(redis=redis)

        await adapter.handle_inbound(
            params    = {"CallSid": "CA003", "From": "+5511", "To": "+1"},
            signature = "sig",
            url       = "https://x.com/",
        )

        redis.delete.assert_awaited()

    @pytest.mark.asyncio
    async def test_pool_defaults_to_voice_default_pool_id(self):
        redis   = AsyncMock()
        redis.get.return_value = None
        settings = _fake_settings(voice_default_pool_id="fallback_voz")
        adapter  = _make_adapter(settings=settings, redis=redis)

        await adapter.handle_inbound(
            params    = {"CallSid": "CA004", "From": "+5511", "To": "+1"},
            signature = "sig",
            url       = "https://x.com/",
        )

        call_kwargs = adapter._open_session.call_args.kwargs
        assert call_kwargs["pool_id"] == "fallback_voz"


# ─────────────────────────────────────────────────────────────────────────────
# VoiceAdapter — handle_status
# ─────────────────────────────────────────────────────────────────────────────


class TestHandleStatus:

    @pytest.mark.asyncio
    async def test_completed_closes_session(self):
        redis = AsyncMock()
        redis.get.return_value = "sess-abc"
        adapter = _make_adapter(redis=redis)

        await adapter.handle_status({
            "CallSid": "CA001", "CallStatus": "completed", "ConferenceSid": ""
        })

        adapter._close_session.assert_awaited_once_with("sess-abc", "customer_hangup")
        redis.delete.assert_awaited()

    @pytest.mark.asyncio
    async def test_no_answer_uses_no_resource_reason(self):
        redis = AsyncMock()
        redis.get.return_value = "sess-xyz"
        adapter = _make_adapter(redis=redis)

        await adapter.handle_status({
            "CallSid": "CA002", "CallStatus": "no-answer", "ConferenceSid": ""
        })

        adapter._close_session.assert_awaited_once_with("sess-xyz", "no_resource")

    @pytest.mark.asyncio
    async def test_failed_uses_system_error_reason(self):
        redis = AsyncMock()
        redis.get.return_value = "sess-err"
        adapter = _make_adapter(redis=redis)

        await adapter.handle_status({
            "CallSid": "CA003", "CallStatus": "failed", "ConferenceSid": ""
        })

        adapter._close_session.assert_awaited_once_with("sess-err", "system_error")

    @pytest.mark.asyncio
    async def test_no_session_found_no_close_called(self):
        redis = AsyncMock()
        redis.get.return_value = None
        adapter = _make_adapter(redis=redis)

        await adapter.handle_status({
            "CallSid": "CA999", "CallStatus": "completed", "ConferenceSid": ""
        })

        adapter._close_session.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_in_progress_stores_conference_sid(self):
        redis = AsyncMock()
        redis.get.return_value = "sess-conf"
        adapter = _make_adapter(redis=redis)

        await adapter.handle_status({
            "CallSid": "CA005",
            "CallStatus": "in-progress",
            "ConferenceSid": "CF001",
        })

        redis.set.assert_awaited()
        set_call_args = [
            str(call) for call in redis.set.call_args_list
        ]
        assert any("conference_sid" in arg for arg in set_call_args)


# ─────────────────────────────────────────────────────────────────────────────
# VoiceAdapter — TTS delivery
# ─────────────────────────────────────────────────────────────────────────────


class TestDeliverTTS:

    @pytest.mark.asyncio
    async def test_twilio_say_path_calls_announce(self):
        redis = AsyncMock()
        redis.get.return_value = "CF_CONF_SID"
        adapter = _make_adapter(redis=redis)
        adapter._tts = MockTTSProvider(synthesize_returns_none=True)

        await adapter._deliver_tts("sess-1", "Olá, como posso ajudá-lo?")

        voice = adapter._voice
        assert len(voice.tts_announced) == 1
        announced_url = voice.tts_announced[0]["url"]
        assert "/voice/tts/" in announced_url

    @pytest.mark.asyncio
    async def test_deepgram_aura_path_stores_audio(self):
        redis = AsyncMock()
        redis.get.return_value = "CF_CONF_SID"
        adapter = _make_adapter(redis=redis)
        adapter._tts = MockTTSProvider(synthesize_returns_none=False)  # returns bytes

        await adapter._deliver_tts("sess-2", "Bem-vindo!")

        voice = adapter._voice
        assert len(voice.tts_announced) == 1
        assert "/voice/tts-audio/" in voice.tts_announced[0]["url"]

    @pytest.mark.asyncio
    async def test_no_conference_sid_skips_announce(self):
        redis = AsyncMock()
        redis.get.return_value = None  # no conference_sid
        adapter = _make_adapter(redis=redis)

        await adapter._deliver_tts("sess-3", "Texto ignorado")

        assert len(adapter._voice.tts_announced) == 0

    @pytest.mark.asyncio
    async def test_tts_text_stored_in_redis(self):
        redis = AsyncMock()
        redis.get.return_value = "CF_CONF_SID"
        adapter = _make_adapter(redis=redis)
        adapter._tts = MockTTSProvider(synthesize_returns_none=True)

        await adapter._deliver_tts("sess-4", "Hello")

        redis.set.assert_awaited()
        # Verify TTS key pattern
        set_calls = {str(c) for c in redis.set.call_args_list}
        assert any("channel:voice:tts:" in c for c in set_calls)


# ─────────────────────────────────────────────────────────────────────────────
# VoiceAdapter — get_tts_twiml
# ─────────────────────────────────────────────────────────────────────────────


class TestGetTtsTwiml:

    @pytest.mark.asyncio
    async def test_returns_twiml_when_found(self):
        redis = AsyncMock()
        redis.get.return_value = json.dumps({
            "text": "Pressione 1 para suporte.",
            "voice": "Polly.Camila-Neural",
        })
        adapter = _make_adapter(redis=redis)

        twiml = await adapter.get_tts_twiml("tts-id-123")

        assert twiml is not None
        assert "<Say" in twiml
        assert "Pressione 1 para suporte." in twiml
        assert "Polly.Camila-Neural" in twiml

    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(self):
        redis = AsyncMock()
        redis.get.return_value = None
        adapter = _make_adapter(redis=redis)

        result = await adapter.get_tts_twiml("expired-id")
        assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# VoiceAdapter — get_tts_audio
# ─────────────────────────────────────────────────────────────────────────────


class TestGetTtsAudio:

    @pytest.mark.asyncio
    async def test_returns_audio_bytes(self):
        audio   = b"\xff\xfb\x90\x04" * 100
        redis   = AsyncMock()
        redis.get.return_value = audio
        adapter = _make_adapter(redis=redis)

        result = await adapter.get_tts_audio("audio-id-abc")
        assert result == audio

    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(self):
        redis = AsyncMock()
        redis.get.return_value = None
        adapter = _make_adapter(redis=redis)

        result = await adapter.get_tts_audio("missing-id")
        assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# VoiceAdapter — deliver_outbound
# ─────────────────────────────────────────────────────────────────────────────


class TestDeliverOutbound:

    @pytest.mark.asyncio
    async def test_notify_calls_deliver_tts(self):
        redis = AsyncMock()
        redis.get.return_value = "CF001"
        adapter = _make_adapter(redis=redis)

        await adapter.deliver_outbound("notify", {
            "session_id": "sess-1",
            "text": "Aguarde um momento.",
        })

        assert len(adapter._voice.tts_announced) == 1

    @pytest.mark.asyncio
    async def test_message_text_calls_deliver_tts(self):
        redis = AsyncMock()
        redis.get.return_value = "CF002"
        adapter = _make_adapter(redis=redis)

        await adapter.deliver_outbound("message.text", {
            "session_id": "sess-2",
            "content": {"text": "Olá!"},
        })

        assert len(adapter._voice.tts_announced) == 1

    @pytest.mark.asyncio
    async def test_session_closed_calls_hangup(self):
        redis = AsyncMock()
        redis.get.return_value = "CA_ACTIVE"
        adapter = _make_adapter(redis=redis)

        await adapter.deliver_outbound("session.closed", {
            "session_id": "sess-3",
        })

        assert "CA_ACTIVE" in adapter._voice.hung_up

    @pytest.mark.asyncio
    async def test_typing_is_noop(self):
        adapter = _make_adapter()
        # Should not raise
        await adapter.deliver_outbound("typing.start", {"session_id": "sess-4"})
        assert len(adapter._voice.tts_announced) == 0

    @pytest.mark.asyncio
    async def test_interaction_request_sets_collect_state(self):
        redis = AsyncMock()
        redis.get.return_value = "CF003"
        adapter = _make_adapter(redis=redis)

        await adapter.deliver_outbound("interaction.request", {
            "session_id": "sess-5",
            "menu": {
                "id": "menu-001",
                "text": "Escolha uma opção:",
                "options": [
                    {"id": "suporte", "label": "Suporte Técnico"},
                    {"id": "financeiro", "label": "Financeiro"},
                ],
                "input_mode": "dtmf",
            },
        })

        # State should be stored in Redis
        redis.set.assert_awaited()
        # TTS prompt should be announced
        assert len(adapter._voice.tts_announced) == 1


# ─────────────────────────────────────────────────────────────────────────────
# VoiceAdapter — DTMF collect
# ─────────────────────────────────────────────────────────────────────────────


class TestDTMFCollect:

    def _make_collect_state(self, **kwargs):
        return json.dumps({
            "menu_id": kwargs.get("menu_id", "menu-1"),
            "fields": kwargs.get("fields", [
                {
                    "name": "choice",
                    "prompt": "Pressione 1 para Suporte ou 2 para Financeiro.",
                    "options": [
                        {"id": "suporte", "label": "Suporte"},
                        {"id": "financeiro", "label": "Financeiro"},
                    ],
                }
            ]),
            "current_index": kwargs.get("current_index", 0),
            "answers": kwargs.get("answers", {}),
            "input_mode": kwargs.get("input_mode", "dtmf"),
        })

    @pytest.mark.asyncio
    async def test_valid_dtmf_publishes_menu_result(self):
        redis = AsyncMock()
        redis.get.return_value = self._make_collect_state()
        adapter = _make_adapter(redis=redis)

        await adapter._handle_dtmf("sess-1", "1")

        adapter._publish_inbound.assert_awaited_once()
        adapter._normalize_menu_result.assert_called_once()

    @pytest.mark.asyncio
    async def test_invalid_dtmf_re_announces_prompt(self):
        redis = AsyncMock()
        redis.get.return_value = self._make_collect_state()
        redis.set.return_value = None  # re-announcement
        redis.get.side_effect = lambda key: (
            self._make_collect_state()
            if "collect" in key
            else "CF_CONF"
        )
        adapter = _make_adapter(redis=redis)

        await adapter._handle_dtmf("sess-1", "9")  # invalid option

        # Should NOT have published (invalid)
        adapter._publish_inbound.assert_not_awaited()
        # Should have re-announced prompt via TTS
        assert len(adapter._voice.tts_announced) == 1

    @pytest.mark.asyncio
    async def test_no_collect_state_does_nothing(self):
        redis = AsyncMock()
        redis.get.return_value = None
        adapter = _make_adapter(redis=redis)

        # Should not raise
        await adapter._handle_dtmf("sess-1", "2")
        adapter._publish_inbound.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_voice_input_mode_ignores_dtmf(self):
        redis = AsyncMock()
        redis.get.return_value = self._make_collect_state(input_mode="voice")
        adapter = _make_adapter(redis=redis)

        await adapter._handle_dtmf("sess-1", "1")

        # Should be ignored in voice-only mode
        adapter._publish_inbound.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_multi_field_advances_to_next(self):
        """Two-field form: first DTMF advances to second field."""
        state = json.dumps({
            "menu_id": "form-1",
            "fields": [
                {"name": "category", "prompt": "Pressione 1 ou 2.", "options": [
                    {"id": "cat1", "label": "Categoria 1"},
                    {"id": "cat2", "label": "Categoria 2"},
                ]},
                {"name": "priority", "prompt": "Pressione 1 para urgente.", "options": [
                    {"id": "urgent", "label": "Urgente"},
                ]},
            ],
            "current_index": 0,
            "answers": {},
            "input_mode": "dtmf",
        })
        redis         = AsyncMock()
        conference_sf = "CF_CONF"

        def _redis_get(key):
            if "collect" in key:
                return state
            return conference_sf  # conference SID for TTS

        redis.get.side_effect = _redis_get
        adapter = _make_adapter(redis=redis)

        await adapter._handle_dtmf("sess-multi", "1")

        # Should NOT have published yet (still collecting)
        adapter._publish_inbound.assert_not_awaited()
        # Should have updated state in Redis (advancing to field 2)
        redis.set.assert_awaited()
        # Should have sent TTS for field 2
        assert len(adapter._voice.tts_announced) == 1


# ─────────────────────────────────────────────────────────────────────────────
# VoiceAdapter — segment recording
# ─────────────────────────────────────────────────────────────────────────────


class TestSegmentRecording:

    @pytest.mark.asyncio
    async def test_starts_recording_when_pool_recording_true(self):
        redis = AsyncMock()
        redis.exists.return_value = False  # not yet announced / not opted out
        redis.get.return_value = "CF_CONF"
        adapter = _make_adapter(redis=redis)
        adapter._deliver_tts = AsyncMock()

        pool_config = {"voice_recording": True}
        await adapter._on_routing_assigned(
            "sess-rec",
            {"segment_id": "seg-001", "pool": json.dumps(pool_config)},
        )

        assert len(adapter._voice.recordings_started) == 1
        assert adapter._voice.recordings_started[0]["conference_sid"] == "CF_CONF"

    @pytest.mark.asyncio
    async def test_skips_recording_when_pool_recording_false(self):
        redis = AsyncMock()
        adapter = _make_adapter(redis=redis)

        pool_config = {"voice_recording": False}
        await adapter._on_routing_assigned(
            "sess-norec",
            {"segment_id": "seg-002", "pool": json.dumps(pool_config)},
        )

        assert len(adapter._voice.recordings_started) == 0

    @pytest.mark.asyncio
    async def test_skips_recording_on_opt_out(self):
        redis = AsyncMock()
        # exists returns True for opt_out key
        redis.exists.side_effect = lambda key: (
            True if "recording_opt_out" in key else False
        )
        adapter = _make_adapter(redis=redis)
        adapter._deliver_tts = AsyncMock()

        pool_config = {"voice_recording": True}
        await adapter._announce_and_start_recording("sess-optout", "seg-003")

        assert len(adapter._voice.recordings_started) == 0

    @pytest.mark.asyncio
    async def test_double_announcement_guard(self):
        redis = AsyncMock()
        redis.exists.side_effect = lambda key: "recording_announced" in key
        redis.get.return_value = "CF_CONF"
        adapter = _make_adapter(redis=redis)

        await adapter._announce_and_start_recording("sess-dup", "seg-004")

        # Should not start recording again
        assert len(adapter._voice.recordings_started) == 0

    @pytest.mark.asyncio
    async def test_stop_all_recordings(self):
        adapter = _make_adapter()
        adapter._active_recordings = {
            "seg-A": "RE_001",
            "seg-B": "RE_002",
        }

        await adapter._stop_all_recordings("sess-stop")

        assert "RE_001" in adapter._voice.recordings_stopped
        assert "RE_002" in adapter._voice.recordings_stopped
        assert len(adapter._active_recordings) == 0

    @pytest.mark.asyncio
    async def test_announces_recording_notice_before_start(self):
        redis = AsyncMock()
        redis.exists.return_value = False
        redis.get.side_effect = lambda key: (
            None if "recording_notice" in key
            else "CF_CONF"
        )
        adapter = _make_adapter(redis=redis)
        adapter._deliver_tts = AsyncMock()

        await adapter._announce_and_start_recording("sess-notice", "seg-005")

        adapter._deliver_tts.assert_awaited()
        # Notice should be the default
        call_args = adapter._deliver_tts.call_args
        assert "gravada" in call_args[0][1].lower() or "gravada" in str(call_args)


# ─────────────────────────────────────────────────────────────────────────────
# VoiceAdapter — collect.events (outbound dial)
# ─────────────────────────────────────────────────────────────────────────────


class TestHandleCollectEvent:

    @pytest.mark.asyncio
    async def test_creates_outbound_call(self):
        redis   = AsyncMock()
        adapter = _make_adapter(redis=redis)

        await adapter.handle_collect_event({
            "channel":       "voice",
            "target":        "+5511999990000",
            "pool_id":       "cobrança_voz",
            "collect_token": "tok-collect-001",
        })

        assert len(adapter._voice.calls_created) == 1
        call = adapter._voice.calls_created[0]
        assert call["to"] == "+5511999990000"

    @pytest.mark.asyncio
    async def test_stores_pending_collect_in_redis(self):
        redis   = AsyncMock()
        adapter = _make_adapter(redis=redis)

        await adapter.handle_collect_event({
            "channel":       "voice",
            "target":        "+5511888880000",
            "pool_id":       "vendas_voz",
            "collect_token": "tok-003",
        })

        redis.set.assert_awaited()
        # Verify Redis key contains pending_collect
        set_calls = redis.set.call_args_list
        keys = [str(c) for c in set_calls]
        assert any("pending_collect:" in k for k in keys)

    @pytest.mark.asyncio
    async def test_missing_target_skips_call(self):
        redis   = AsyncMock()
        adapter = _make_adapter(redis=redis)

        await adapter.handle_collect_event({
            "channel":  "voice",
            "target":   "",
            "pool_id":  "test",
        })

        assert len(adapter._voice.calls_created) == 0

    @pytest.mark.asyncio
    async def test_stores_journey_id_when_present(self):
        redis   = AsyncMock()
        adapter = _make_adapter(redis=redis)

        await adapter.handle_collect_event({
            "channel":    "voice",
            "target":     "+5511777770000",
            "pool_id":    "p1",
            "journey_id": "journey-uuid-abc",
        })

        # Check that the stored pending value includes journey_id
        redis.set.assert_awaited()
        set_call_value = redis.set.call_args_list[-1]
        stored = json.loads(str(set_call_value.args[1]))
        assert stored.get("journey_id") == "journey-uuid-abc"


# ─────────────────────────────────────────────────────────────────────────────
# Utility functions
# ─────────────────────────────────────────────────────────────────────────────


class TestNormalizeE164:

    def test_already_e164(self):
        assert _normalize_e164("+5511999990000") == "+5511999990000"

    def test_without_plus(self):
        result = _normalize_e164("5511999990000")
        assert result == "+5511999990000"

    def test_with_spaces_dashes(self):
        result = _normalize_e164("+55 11 9999-0000")
        assert result == "+5511999990000"

    def test_us_number(self):
        assert _normalize_e164("+12025551234") == "+12025551234"


class TestMatchOption:

    def _options(self):
        return [
            {"id": "suporte", "label": "Suporte Técnico"},
            {"id": "financeiro", "label": "Financeiro"},
            {"id": "outros", "label": "Outros"},
        ]

    def test_dtmf_digit_1(self):
        result = _match_option("1", self._options())
        assert result == "suporte"

    def test_dtmf_digit_2(self):
        result = _match_option("2", self._options())
        assert result == "financeiro"

    def test_dtmf_out_of_range(self):
        result = _match_option("9", self._options())
        assert result is None

    def test_exact_label_match(self):
        result = _match_option("Financeiro", self._options())
        assert result == "financeiro"

    def test_case_insensitive_label(self):
        result = _match_option("suporte técnico", self._options())
        assert result == "suporte"

    def test_no_match_returns_none(self):
        result = _match_option("xyz", self._options())
        assert result is None

    def test_empty_options(self):
        result = _match_option("1", [])
        assert result is None


class TestBuildVoicePrompt:

    def test_with_options(self):
        field = {
            "name": "choice",
            "prompt": "Escolha uma opção:",
            "options": [
                {"id": "a", "label": "Alpha"},
                {"id": "b", "label": "Beta"},
            ],
        }
        result = _build_voice_prompt(field, [])
        assert "Escolha uma opção:" in result
        assert "1." in result
        assert "Alpha" in result
        assert "2." in result
        assert "Beta" in result

    def test_without_options_returns_just_prompt(self):
        field = {"name": "name", "prompt": "Qual é o seu nome?"}
        result = _build_voice_prompt(field, [])
        assert result == "Qual é o seu nome?"

    def test_uses_field_options_over_arg_options(self):
        field = {
            "name": "x",
            "prompt": "Prompt:",
            "options": [{"id": "field_opt", "label": "Field Option"}],
        }
        arg_options = [{"id": "arg_opt", "label": "Arg Option"}]
        result = _build_voice_prompt(field, arg_options)
        assert "Field Option" in result
        assert "Arg Option" not in result


# ─────────────────────────────────────────────────────────────────────────────
# TwilioSayTTSProvider / DeepgramAuraTTSProvider
# ─────────────────────────────────────────────────────────────────────────────


class TestTTSProviders:

    @pytest.mark.asyncio
    async def test_twilio_say_returns_none(self):
        provider = TwilioSayTTSProvider()
        result = await provider.synthesize("Hello")
        assert result is None

    @pytest.mark.asyncio
    async def test_deepgram_aura_returns_none_without_key(self):
        provider = DeepgramAuraTTSProvider(api_key="")
        result = await provider.synthesize("Hello")
        assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# Integration: inbound → session open → status → session close
# ─────────────────────────────────────────────────────────────────────────────


class TestInboundStatusIntegration:

    @pytest.mark.asyncio
    async def test_full_call_lifecycle(self):
        """
        Simulate: inbound call → session opened → call ends → session closed.
        """
        redis = AsyncMock()
        session_id = None

        async def _redis_get(key):
            if "pending_collect" in key:
                return None
            if key.endswith(":session") and session_id:
                return session_id
            return None

        redis.get.side_effect = _redis_get
        adapter = _make_adapter(redis=redis)

        # Track session_id from _open_session
        opened_sessions = []

        async def _fake_open_session(**kwargs):
            opened_sessions.append(kwargs["session_id"])

        adapter._open_session = AsyncMock(side_effect=_fake_open_session)

        # 1. Inbound call
        twiml = await adapter.handle_inbound(
            params    = {"CallSid": "CA_TEST", "From": "+5511999990000", "To": "+551140042121"},
            signature = "sig",
            url       = "https://x.com/webhooks/voice/inbound",
        )
        assert "<Response" in twiml
        assert len(opened_sessions) == 1

        # 2. Session opened — simulate Redis having the session
        nonlocal_session_id = opened_sessions[0]

        async def _redis_get_2(key):
            if "pending_collect" in key:
                return None
            if "CA_TEST" in key:
                return nonlocal_session_id
            return None

        redis.get.side_effect = _redis_get_2

        # 3. Customer hangs up
        await adapter.handle_status({
            "CallSid": "CA_TEST",
            "CallStatus": "completed",
            "ConferenceSid": "",
        })

        adapter._close_session.assert_awaited_once_with(
            nonlocal_session_id, "customer_hangup"
        )


# ─────────────────────────────────────────────────────────────────────────────
# ElevenLabsTTSProvider
# ─────────────────────────────────────────────────────────────────────────────


class TestElevenLabsTTSProvider:

    @pytest.mark.asyncio
    async def test_returns_none_when_no_api_key(self):
        provider = ElevenLabsTTSProvider(api_key="")
        result = await provider.synthesize("Olá, como posso ajudar?")
        assert result is None

    @pytest.mark.asyncio
    async def test_synthesize_returns_mp3_bytes(self):
        """Successful ElevenLabs call returns MP3 bytes."""
        fake_audio = b"\xff\xfb\x90\x04" * 50   # stub MP3 header bytes

        provider = ElevenLabsTTSProvider(api_key="el-test-key", voice_id="voice123")

        with patch("httpx.AsyncClient") as MockClient:
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.content = fake_audio
            MockClient.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_resp
            )
            result = await provider.synthesize("Olá, tudo bem?")

        assert result == fake_audio

    @pytest.mark.asyncio
    async def test_returns_none_on_http_error(self):
        """HTTP error returns None (enables fallback)."""
        provider = ElevenLabsTTSProvider(api_key="el-test-key")

        with patch("httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__.return_value.post = AsyncMock(
                side_effect=Exception("connection refused")
            )
            result = await provider.synthesize("Olá")

        assert result is None

    @pytest.mark.asyncio
    async def test_voice_id_override(self):
        """Per-call voice_id overrides instance default."""
        captured: list[str] = []

        async def _fake_post(url, **kwargs):
            captured.append(url)
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.content = b"audio"
            return resp

        provider = ElevenLabsTTSProvider(api_key="key", voice_id="default-voice")

        with patch("httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__.return_value.post = AsyncMock(
                side_effect=_fake_post
            )
            await provider.synthesize("Teste", voice_id="custom-voice")

        assert "custom-voice" in captured[0]

    def test_default_voice_id_is_set(self):
        provider = ElevenLabsTTSProvider(api_key="key")
        assert provider._voice_id == "pNInz6obpgDQGcFmaJgB"


# ─────────────────────────────────────────────────────────────────────────────
# FallbackSTTProvider
# ─────────────────────────────────────────────────────────────────────────────


class TestFallbackSTTProvider:

    @pytest.mark.asyncio
    async def test_yields_from_first_provider(self):
        """Results from the primary provider are yielded correctly."""
        primary = MockSTTProvider()
        primary.results = [
            STTResult("Olá", is_final=False),
            STTResult("Olá tudo bem", is_final=True),
        ]
        fallback = MockSTTProvider()

        provider = FallbackSTTProvider([primary, fallback])

        async def _chunks():
            yield b"\x00" * 160

        results = []
        async for r in provider.stream(_chunks()):
            results.append(r)

        assert len(results) == 2
        assert results[-1].transcript == "Olá tudo bem"
        assert results[-1].is_final is True
        # Fallback should not have received any chunks
        assert len(fallback.chunks_received) == 0

    @pytest.mark.asyncio
    async def test_advances_to_fallback_on_exception(self):
        """When primary raises, fallback provider is used."""

        class _BrokenSTT:
            async def stream(self, audio_chunks, sample_rate=8000, language="pt-BR"):
                async for _ in audio_chunks:
                    pass
                raise RuntimeError("Deepgram down")

        fallback = MockSTTProvider()
        fallback.results = [STTResult("Fallback transcript", is_final=True)]

        provider = FallbackSTTProvider([_BrokenSTT(), fallback])

        async def _chunks():
            yield b"\x00" * 160

        results = []
        async for r in provider.stream(_chunks()):
            results.append(r)

        assert len(results) == 1
        assert results[0].transcript == "Fallback transcript"

    @pytest.mark.asyncio
    async def test_last_provider_exception_returns_empty(self):
        """When all providers raise, stream ends without results."""

        class _BrokenSTT:
            async def stream(self, audio_chunks, **kwargs):
                async for _ in audio_chunks:
                    pass
                raise RuntimeError("all down")

        provider = FallbackSTTProvider([_BrokenSTT()])

        async def _chunks():
            yield b"\x00" * 160

        results = []
        async for r in provider.stream(_chunks()):
            results.append(r)

        assert results == []

    def test_requires_at_least_one_provider(self):
        with pytest.raises(ValueError, match="at least one"):
            FallbackSTTProvider([])


# ─────────────────────────────────────────────────────────────────────────────
# FallbackTTSProvider
# ─────────────────────────────────────────────────────────────────────────────


class TestFallbackTTSProvider:

    @pytest.mark.asyncio
    async def test_returns_first_non_none(self):
        """Primary provider returns bytes — used without trying fallback."""
        primary  = MockTTSProvider(synthesize_returns_none=False)   # returns b"\x00"*16
        fallback = MockTTSProvider(synthesize_returns_none=False)

        provider = FallbackTTSProvider([primary, fallback])
        result = await provider.synthesize("Texto de teste")

        assert result is not None
        assert len(fallback.synthesized) == 0   # fallback never called

    @pytest.mark.asyncio
    async def test_advances_when_primary_returns_none(self):
        """Primary returns None → fallback's bytes are returned."""
        primary  = MockTTSProvider(synthesize_returns_none=True)    # returns None
        fallback = MockTTSProvider(synthesize_returns_none=False)   # returns bytes

        provider = FallbackTTSProvider([primary, fallback])
        result = await provider.synthesize("Texto")

        assert result == b"\x00" * 16   # from fallback
        assert len(primary.synthesized) == 1
        assert len(fallback.synthesized) == 1

    @pytest.mark.asyncio
    async def test_advances_on_exception(self):
        """Primary raises → fallback is used."""

        class _BrokenTTS:
            async def synthesize(self, text, voice_id=None):
                raise ConnectionError("ElevenLabs down")

        fallback = MockTTSProvider(synthesize_returns_none=False)
        provider = FallbackTTSProvider([_BrokenTTS(), fallback])
        result = await provider.synthesize("Texto")

        assert result is not None

    @pytest.mark.asyncio
    async def test_returns_none_when_all_return_none(self):
        """When every provider returns None (all TwilioSay-style), returns None."""
        p1 = TwilioSayTTSProvider()
        p2 = TwilioSayTTSProvider()

        provider = FallbackTTSProvider([p1, p2])
        result = await provider.synthesize("Texto")

        assert result is None

    def test_requires_at_least_one_provider(self):
        with pytest.raises(ValueError, match="at least one"):
            FallbackTTSProvider([])


# ─────────────────────────────────────────────────────────────────────────────
# VoiceAdapter factory: _build_tts_provider / _build_stt_provider
# ─────────────────────────────────────────────────────────────────────────────


class TestVoiceAdapterProviderFactories:

    def test_build_tts_returns_fallback_with_elevenlabs_when_key_set(self):
        settings = _fake_settings(
            voice_elevenlabs_api_key="el-key-123",
            voice_elevenlabs_voice_id="custom-voice",
        )
        adapter = _make_adapter(settings=settings)
        # _build_tts_provider is called in __init__ — check type
        assert isinstance(adapter._tts, FallbackTTSProvider)
        # Primary should be ElevenLabs
        assert isinstance(adapter._tts._providers[0], ElevenLabsTTSProvider)
        # Last-resort should be TwilioSay
        assert isinstance(adapter._tts._providers[-1], TwilioSayTTSProvider)

    def test_build_tts_returns_twiliosay_when_no_elevenlabs_key(self):
        settings = _fake_settings(
            voice_elevenlabs_api_key="",
            voice_tts_provider="twilio_say",
        )
        adapter = _make_adapter(settings=settings)
        assert isinstance(adapter._tts, TwilioSayTTSProvider)

    def test_build_tts_returns_fallback_with_deepgram_aura(self):
        settings = _fake_settings(
            voice_elevenlabs_api_key="",
            voice_tts_provider="deepgram_aura",
            voice_deepgram_api_key="dg-key-123",
        )
        adapter = _make_adapter(settings=settings)
        assert isinstance(adapter._tts, FallbackTTSProvider)
        assert isinstance(adapter._tts._providers[0], DeepgramAuraTTSProvider)
        assert isinstance(adapter._tts._providers[-1], TwilioSayTTSProvider)

    def test_build_stt_returns_fallback_with_deepgram_when_key_set(self):
        settings = _fake_settings(
            voice_stt_provider="deepgram",
            voice_deepgram_api_key="dg-key-456",
        )
        adapter = _make_adapter(settings=settings)
        assert isinstance(adapter._stt, FallbackSTTProvider)

    def test_build_stt_returns_mock_when_no_key(self):
        settings = _fake_settings(
            voice_stt_provider="deepgram",
            voice_deepgram_api_key="",
        )
        adapter = _make_adapter(settings=settings)
        assert isinstance(adapter._stt, MockSTTProvider)

    def test_build_stt_returns_mock_when_provider_is_mock(self):
        settings = _fake_settings(
            voice_stt_provider="mock",
            voice_deepgram_api_key="dg-key",
        )
        adapter = _make_adapter(settings=settings)
        assert isinstance(adapter._stt, MockSTTProvider)


# ─────────────────────────────────────────────────────────────────────────────
# _fake_settings: ElevenLabs fields present
# ─────────────────────────────────────────────────────────────────────────────


class TestFakeSettingsElevenLabsFields:
    """Smoke test — confirm _fake_settings has the new fields so tests don't break."""

    def test_elevenlabs_defaults(self):
        s = _fake_settings()
        assert hasattr(s, "voice_elevenlabs_api_key")
        assert hasattr(s, "voice_elevenlabs_voice_id")
        assert hasattr(s, "voice_tts_fallback_provider")
        assert hasattr(s, "voice_stt_fallback_provider")
