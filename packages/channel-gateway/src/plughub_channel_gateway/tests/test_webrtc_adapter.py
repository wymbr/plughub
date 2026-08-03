"""
tests/test_webrtc_adapter.py
Unit tests for Arc 15 Phase A WebRTC components.

Coverage:
  - negotiate_medium()         — medium negotiation cascade
  - build_room_name()          — room name helper
  - TokenGrants                — dataclass construction
  - MockWebRTCProvider         — generate_token, create_room, get_room, delete_room,
                                 list_participants, start_egress, stop_egress
  - LiveKitProvider dev mode   — token generation without real LiveKit
  - WebRTCAdapter              — deliver_text, deliver_menu, deliver_typing,
                                 deliver_session_closed, get_token
  - WebRTC WS lifecycle        — auth handshake, webrtc.ready on routing.assigned,
                                 webrtc.hangup, session close

Tests run without a real Kafka broker, Redis, or LiveKit server.
All external I/O is replaced with mocks or in-memory stubs.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ..adapters.webrtc import WebRTCAdapter, _AuthError
from ..adapters.webrtc_provider import (
    IWebRTCProvider,
    LiveKitProvider,
    MockWebRTCProvider,
    ParticipantInfo,
    RoomInfo,
    TokenGrants,
    build_room_name,
    negotiate_medium,
)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _fake_settings(**kwargs):
    """Build a minimal Settings-like object for tests."""
    s = MagicMock()
    s.webrtc_livekit_url            = kwargs.get("webrtc_livekit_url", "wss://localhost:7880")
    s.webrtc_livekit_api_key        = kwargs.get("webrtc_livekit_api_key", "")
    s.webrtc_livekit_api_secret     = kwargs.get("webrtc_livekit_api_secret", "")
    s.webrtc_token_ttl_s            = kwargs.get("webrtc_token_ttl_s", 3600)
    s.webrtc_default_pool_id        = kwargs.get("webrtc_default_pool_id", "webrtc_pool")
    s.webrtc_default_medium_order   = kwargs.get("webrtc_default_medium_order", "video,voice,text")
    s.webrtc_stt_enabled            = kwargs.get("webrtc_stt_enabled", True)
    s.webrtc_tts_injection_enabled  = kwargs.get("webrtc_tts_injection_enabled", False)
    s.jwt_secret                    = kwargs.get("jwt_secret", "changeme_32chars_webchat_secret!")
    s.tenant_id                     = kwargs.get("tenant_id", "default")
    s.session_ttl_seconds           = kwargs.get("session_ttl_seconds", 14400)
    s.kafka_topic_inbound           = kwargs.get("kafka_topic_inbound", "conversations.inbound")
    s.agent_registry_url            = kwargs.get("agent_registry_url", "")
    s.endpoint_cache_ttl_s          = kwargs.get("endpoint_cache_ttl_s", 30)
    return s


def _fake_redis():
    """Build an async Redis mock that records calls."""
    r = AsyncMock()
    r._store: dict[str, Any] = {}

    async def _setex(key, ttl, value):
        r._store[key] = value

    async def _get(key):
        return r._store.get(key)

    async def _expire(key, ttl):
        pass

    async def _delete(*keys):
        for k in keys:
            r._store.pop(k, None)

    r.setex = AsyncMock(side_effect=_setex)
    r.get   = AsyncMock(side_effect=_get)
    r.expire = AsyncMock(side_effect=_expire)
    r.delete = AsyncMock(side_effect=_delete)
    r.xread  = AsyncMock(return_value=[])
    return r


def _fake_producer():
    """Build a minimal Kafka producer mock."""
    p = AsyncMock()
    p.send = AsyncMock()
    return p


def _fake_ws(messages: list[str] | None = None):
    """
    Build a WebSocket mock that returns a sequence of messages.
    After the sequence, iter_text() raises StopAsyncIteration.
    """
    ws = AsyncMock()
    ws.sent_messages: list[dict] = []

    async def _send_json(msg):
        ws.sent_messages.append(msg)

    ws.send_json = AsyncMock(side_effect=_send_json)
    ws.accept    = AsyncMock()
    ws.close     = AsyncMock()

    _msgs = list(messages or [])

    async def _receive_text():
        if _msgs:
            return _msgs.pop(0)
        raise Exception("WebSocket disconnected")

    ws.receive_text = AsyncMock(side_effect=_receive_text)

    async def _iter_text():
        for m in list(_msgs):
            yield m

    ws.iter_text = _iter_text
    return ws


def _make_adapter(
    provider: IWebRTCProvider | None = None,
    settings=None,
    redis=None,
    producer=None,
) -> WebRTCAdapter:
    return WebRTCAdapter(
        producer       = producer or _fake_producer(),
        redis          = redis or _fake_redis(),
        settings       = settings or _fake_settings(),
        webrtc_provider = provider or MockWebRTCProvider(),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# negotiate_medium
# ═══════════════════════════════════════════════════════════════════════════════


class TestNegotiateMedium:
    def test_video_preferred_when_agent_supports_video(self):
        assert negotiate_medium(["video", "voice", "text"]) == "video"

    def test_voice_when_no_video(self):
        assert negotiate_medium(["voice", "text"]) == "voice"

    def test_text_fallback_when_only_text(self):
        assert negotiate_medium(["text"]) == "text"

    def test_text_fallback_when_empty(self):
        assert negotiate_medium([]) == "text"

    def test_pool_fallback_order_overrides_default(self):
        # Pool says no video allowed
        assert negotiate_medium(
            ["video", "voice", "text"],
            fallback_order=["voice", "text"],
        ) == "voice"

    def test_text_fallback_when_no_intersection(self):
        # Agent has video, pool only allows text
        assert negotiate_medium(
            ["video"],
            fallback_order=["voice", "text"],
        ) == "text"

    def test_pool_order_respected_order(self):
        # Pool: text-first (unusual)
        assert negotiate_medium(
            ["video", "voice", "text"],
            fallback_order=["text", "voice", "video"],
        ) == "text"


# ═══════════════════════════════════════════════════════════════════════════════
# build_room_name
# ═══════════════════════════════════════════════════════════════════════════════


class TestBuildRoomName:
    def test_prefix(self):
        assert build_room_name("abc123") == "plughub-abc123"

    def test_full_uuid(self):
        sid = str(uuid.uuid4())
        assert build_room_name(sid) == f"plughub-{sid}"


# ═══════════════════════════════════════════════════════════════════════════════
# TokenGrants
# ═══════════════════════════════════════════════════════════════════════════════


class TestTokenGrants:
    def test_customer_defaults(self):
        g = TokenGrants(room_name="room-1", identity="customer-x")
        assert g.can_publish is True
        assert g.can_subscribe is True
        assert g.hidden is False
        assert g.ttl_seconds == 3600

    def test_supervisor_grants(self):
        g = TokenGrants(
            room_name    = "room-1",
            identity     = "supervisor-x",
            can_publish  = False,
            can_subscribe = True,
            hidden       = True,
        )
        assert g.can_publish is False
        assert g.hidden is True


# ═══════════════════════════════════════════════════════════════════════════════
# MockWebRTCProvider
# ═══════════════════════════════════════════════════════════════════════════════


class TestMockWebRTCProvider:
    def setup_method(self):
        self.mock = MockWebRTCProvider()

    def test_generate_token_visible(self):
        grants = TokenGrants(room_name="room-x", identity="user-1")
        token  = self.mock.generate_token(grants)
        assert "mock-token" in token
        assert "user-1" in token
        assert "visible" in token

    def test_generate_token_hidden(self):
        grants = TokenGrants(room_name="room-x", identity="sup-1", hidden=True)
        token  = self.mock.generate_token(grants)
        assert "hidden" in token

    def test_token_recorded(self):
        grants = TokenGrants(room_name="room-x", identity="user-2")
        self.mock.generate_token(grants)
        assert len(self.mock.tokens_generated) == 1
        assert self.mock.tokens_generated[0]["grants"] is grants

    @pytest.mark.asyncio
    async def test_create_room(self):
        info = await self.mock.create_room("test-room")
        assert info.room_name == "test-room"
        assert info.room_sid.startswith("RM_mock_")
        assert len(self.mock.rooms_created) == 1
        assert self.mock.rooms_created[0]["room_name"] == "test-room"

    @pytest.mark.asyncio
    async def test_create_room_sid_increments(self):
        await self.mock.create_room("room-a")
        await self.mock.create_room("room-b")
        assert self.mock.rooms_created[0]["sid"] == "RM_mock_0000"
        assert self.mock.rooms_created[1]["sid"] == "RM_mock_0001"

    @pytest.mark.asyncio
    async def test_get_room_after_create(self):
        await self.mock.create_room("room-x")
        info = await self.mock.get_room("room-x")
        assert info is not None
        assert info.room_name == "room-x"

    @pytest.mark.asyncio
    async def test_get_room_missing(self):
        info = await self.mock.get_room("nonexistent")
        assert info is None

    @pytest.mark.asyncio
    async def test_delete_room(self):
        await self.mock.create_room("room-d")
        await self.mock.delete_room("room-d")
        assert "room-d" in self.mock.rooms_deleted
        assert await self.mock.get_room("room-d") is None

    @pytest.mark.asyncio
    async def test_list_participants_empty(self):
        participants = await self.mock.list_participants("any-room")
        assert participants == []

    @pytest.mark.asyncio
    async def test_start_egress(self):
        egress_id = await self.mock.start_egress("room-e", "s3://bucket/key")
        assert egress_id.startswith("EG_mock_")
        assert len(self.mock.egresses_started) == 1
        assert self.mock.egresses_started[0]["room_name"] == "room-e"
        assert self.mock.egresses_started[0]["output_url"] == "s3://bucket/key"

    @pytest.mark.asyncio
    async def test_stop_egress(self):
        egress_id = await self.mock.start_egress("room-e", "s3://bucket/key")
        await self.mock.stop_egress(egress_id)
        assert egress_id in self.mock.egresses_stopped

    @pytest.mark.asyncio
    async def test_egress_counter_increments(self):
        id1 = await self.mock.start_egress("room-1", "s3://a")
        id2 = await self.mock.start_egress("room-2", "s3://b")
        assert id1 == "EG_mock_0001"
        assert id2 == "EG_mock_0002"


# ═══════════════════════════════════════════════════════════════════════════════
# LiveKitProvider dev mode
# ═══════════════════════════════════════════════════════════════════════════════


class TestLiveKitProviderDevMode:
    def setup_method(self):
        # No api_key/secret → dev mode
        self.provider = LiveKitProvider(
            url        = "wss://localhost:7880",
            api_key    = "",
            api_secret = "",
        )

    def test_generate_token_returns_dev_placeholder(self):
        grants = TokenGrants(room_name="room-1", identity="user-1")
        token  = self.provider.generate_token(grants)
        assert token.startswith("dev-token-")
        assert "user-1" in token
        assert "room-1" in token

    @pytest.mark.asyncio
    async def test_create_room_dev_mode(self):
        info = await self.provider.create_room("dev-room")
        assert info.room_name == "dev-room"
        assert "RM_dev_" in info.room_sid

    @pytest.mark.asyncio
    async def test_get_room_dev_mode_returns_none(self):
        info = await self.provider.get_room("any")
        assert info is None

    @pytest.mark.asyncio
    async def test_delete_room_dev_mode_no_error(self):
        # Should not raise
        await self.provider.delete_room("any-room")

    @pytest.mark.asyncio
    async def test_list_participants_dev_mode(self):
        result = await self.provider.list_participants("any")
        assert result == []

    # A Fase D saiu do stub: em dev_mode o egress devolve um id mock e não faz I/O de
    # rede (`webrtc_provider.py:327`). Os testes cobravam `NotImplementedError`, que era
    # o contrato ANTIGO — e o docstring da classe ainda o anunciava, o que fazia o teste
    # parecer certo por escrito. Trocados por afirmações sobre o contrato vigente, que é
    # mais forte do que "levanta": id no formato esperado (para o chamador conseguir
    # distinguir gravação real de mock) e ausência de exceção no stop.
    @pytest.mark.asyncio
    async def test_start_egress_dev_mode_returns_mock_id(self):
        eid = await self.provider.start_egress("room", "s3://bucket/path")
        assert eid.startswith("EG_dev_")

    @pytest.mark.asyncio
    async def test_stop_egress_dev_mode_is_noop(self):
        await self.provider.stop_egress("EG_dev_deadbeef")   # não deve levantar


# ═══════════════════════════════════════════════════════════════════════════════
# WebRTCAdapter — outbound delivery
# ═══════════════════════════════════════════════════════════════════════════════


class TestWebRTCAdapterDelivery:
    def setup_method(self):
        self.provider = MockWebRTCProvider()
        self.adapter  = _make_adapter(provider=self.provider)
        self.session_id = str(uuid.uuid4())

    def _register_ws(self) -> AsyncMock:
        """Register a fake WS for the session."""
        ws = AsyncMock()
        ws.sent_messages: list[dict] = []

        async def _send(msg):
            ws.sent_messages.append(msg)

        ws.send_json = AsyncMock(side_effect=_send)
        ws.close     = AsyncMock()
        self.adapter._connections[self.session_id] = ws
        return ws

    @pytest.mark.asyncio
    async def test_deliver_text_sends_webrtc_message(self):
        ws = self._register_ws()
        await self.adapter.deliver_text({
            "session_id": self.session_id,
            "content":    {"text": "Hello from agent"},
        })
        assert len(ws.sent_messages) == 1
        msg = ws.sent_messages[0]
        assert msg["type"] == "webrtc.message"
        assert msg["text"] == "Hello from agent"

    @pytest.mark.asyncio
    async def test_deliver_text_no_connection_silent(self):
        # No connection registered — should not raise
        await self.adapter.deliver_text({
            "session_id": "nonexistent",
            "content":    {"text": "hi"},
        })

    @pytest.mark.asyncio
    async def test_deliver_menu_sends_webrtc_interaction(self):
        ws = self._register_ws()
        menu = {"menu_id": "m1", "fields": []}
        await self.adapter.deliver_menu({
            "session_id": self.session_id,
            "content":    menu,
        })
        assert ws.sent_messages[0]["type"] == "webrtc.interaction"
        assert ws.sent_messages[0]["payload"] == menu

    @pytest.mark.asyncio
    async def test_deliver_typing_sends_webrtc_typing(self):
        ws = self._register_ws()
        await self.adapter.deliver_typing({
            "session_id": self.session_id,
            "typing":     True,
        })
        msg = ws.sent_messages[0]
        assert msg["type"] == "webrtc.typing"
        assert msg["active"] is True

    @pytest.mark.asyncio
    async def test_deliver_session_closed_sends_and_closes_ws(self):
        ws = self._register_ws()
        await self.adapter.deliver_session_closed({
            "session_id":  self.session_id,
            "close_reason": "agent_hangup",
        })
        msg = ws.sent_messages[0]
        assert msg["type"] == "webrtc.session_closed"
        assert msg["reason"] == "agent_hangup"
        ws.close.assert_called_once()
        # Connection should be removed
        assert self.session_id not in self.adapter._connections

    @pytest.mark.asyncio
    async def test_deliver_session_closed_removes_medium(self):
        self._register_ws()
        self.adapter._mediums[self.session_id] = "voice"
        await self.adapter.deliver_session_closed({
            "session_id": self.session_id,
        })
        assert self.session_id not in self.adapter._mediums


# ═══════════════════════════════════════════════════════════════════════════════
# WebRTCAdapter — token endpoint
# ═══════════════════════════════════════════════════════════════════════════════


class TestWebRTCAdapterGetToken:
    def setup_method(self):
        self.provider   = MockWebRTCProvider()
        self.redis      = _fake_redis()
        self.settings   = _fake_settings()
        self.adapter    = _make_adapter(
            provider  = self.provider,
            redis     = self.redis,
            settings  = self.settings,
        )
        self.session_id = str(uuid.uuid4())

    @pytest.mark.asyncio
    async def test_get_token_returns_none_when_room_not_ready(self):
        result = await self.adapter.get_token(self.session_id, "agent", "agent-x")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_token_returns_token_after_room_created(self):
        room_name = f"plughub-{self.session_id}"
        await self.redis.setex(
            f"channel:webrtc:{self.session_id}:room_name",
            3600,
            room_name,
        )
        await self.redis.setex(
            f"channel:webrtc:{self.session_id}:medium",
            3600,
            "voice",
        )

        result = await self.adapter.get_token(self.session_id, "agent", "agente_v1")
        assert result is not None
        assert "token" in result
        assert result["livekit_url"] == self.settings.webrtc_livekit_url
        assert result["room_name"]   == room_name
        assert result["negotiated_medium"] == "voice"

    @pytest.mark.asyncio
    async def test_agent_token_can_publish(self):
        room_name = f"plughub-{self.session_id}"
        await self.redis.setex(
            f"channel:webrtc:{self.session_id}:room_name", 3600, room_name
        )
        await self.redis.setex(
            f"channel:webrtc:{self.session_id}:medium", 3600, "video"
        )

        await self.adapter.get_token(self.session_id, "agent", "agent-1")
        grants = self.provider.tokens_generated[-1]["grants"]
        assert grants.can_publish is True
        assert grants.hidden is False

    @pytest.mark.asyncio
    async def test_supervisor_token_hidden_cannot_publish(self):
        room_name = f"plughub-{self.session_id}"
        await self.redis.setex(
            f"channel:webrtc:{self.session_id}:room_name", 3600, room_name
        )
        await self.redis.setex(
            f"channel:webrtc:{self.session_id}:medium", 3600, "video"
        )

        await self.adapter.get_token(self.session_id, "supervisor", "sup-1")
        grants = self.provider.tokens_generated[-1]["grants"]
        assert grants.can_publish is False
        assert grants.hidden is True

    @pytest.mark.asyncio
    async def test_identity_in_grants(self):
        room_name = f"plughub-{self.session_id}"
        await self.redis.setex(
            f"channel:webrtc:{self.session_id}:room_name", 3600, room_name
        )
        await self.redis.setex(
            f"channel:webrtc:{self.session_id}:medium", 3600, "text"
        )

        await self.adapter.get_token(self.session_id, "agent", "my-agent")
        grants = self.provider.tokens_generated[-1]["grants"]
        assert "my-agent" in grants.identity


# ═══════════════════════════════════════════════════════════════════════════════
# WebRTCAdapter — routing.assigned → webrtc.ready
# ═══════════════════════════════════════════════════════════════════════════════


class TestWebRTCAdapterRoutingAssigned:
    def setup_method(self):
        self.provider   = MockWebRTCProvider()
        self.redis      = _fake_redis()
        self.settings   = _fake_settings()
        self.adapter    = _make_adapter(
            provider  = self.provider,
            redis     = self.redis,
            settings  = self.settings,
        )
        self.session_id = str(uuid.uuid4())
        self.ws         = AsyncMock()
        self.ws.sent_messages = []

        async def _send(msg):
            self.ws.sent_messages.append(msg)

        self.ws.send_json = AsyncMock(side_effect=_send)
        self.ws.close     = AsyncMock()

    @pytest.mark.asyncio
    async def test_on_routing_assigned_sends_webrtc_ready(self):
        fields = {
            "type":       "routing.assigned",
            "agent_type": json.dumps({"media_capabilities": ["video", "voice", "text"]}),
            "pool":       json.dumps({"webrtc_media_fallback_order": "video,voice,text"}),
        }
        # Store contact_id in redis
        await self.redis.setex(
            f"session:{self.session_id}:contact_id", 3600, "customer-1"
        )

        await self.adapter._on_routing_assigned(
            self.ws, self.session_id, fields, self.settings
        )

        assert len(self.ws.sent_messages) == 1
        msg = self.ws.sent_messages[0]
        assert msg["type"] == "webrtc.ready"
        assert msg["negotiated_medium"] == "video"
        assert msg["room_name"] == f"plughub-{self.session_id}"
        assert "token" in msg
        assert msg["livekit_url"] == self.settings.webrtc_livekit_url

    @pytest.mark.asyncio
    async def test_on_routing_assigned_negotiates_voice_when_no_video(self):
        fields = {
            "type":       "routing.assigned",
            "agent_type": json.dumps({"media_capabilities": ["voice", "text"]}),
            "pool":       json.dumps({}),
        }
        await self.redis.setex(
            f"session:{self.session_id}:contact_id", 3600, "customer-2"
        )
        await self.adapter._on_routing_assigned(
            self.ws, self.session_id, fields, self.settings
        )
        msg = self.ws.sent_messages[0]
        assert msg["negotiated_medium"] == "voice"

    @pytest.mark.asyncio
    async def test_on_routing_assigned_persists_room_and_medium_in_redis(self):
        fields = {
            "agent_type": json.dumps({"media_capabilities": ["text"]}),
            "pool":       json.dumps({}),
        }
        await self.redis.setex(
            f"session:{self.session_id}:contact_id", 3600, "customer-3"
        )
        await self.adapter._on_routing_assigned(
            self.ws, self.session_id, fields, self.settings
        )
        room_name = await self.redis.get(f"channel:webrtc:{self.session_id}:room_name")
        medium    = await self.redis.get(f"channel:webrtc:{self.session_id}:medium")
        assert room_name == f"plughub-{self.session_id}"
        assert medium == "text"

    @pytest.mark.asyncio
    async def test_on_routing_assigned_creates_livekit_room(self):
        fields = {
            "agent_type": json.dumps({"media_capabilities": ["video"]}),
            "pool":       json.dumps({}),
        }
        await self.redis.setex(
            f"session:{self.session_id}:contact_id", 3600, "customer-4"
        )
        await self.adapter._on_routing_assigned(
            self.ws, self.session_id, fields, self.settings
        )
        assert len(self.provider.rooms_created) == 1
        assert self.provider.rooms_created[0]["room_name"] == f"plughub-{self.session_id}"


# ═══════════════════════════════════════════════════════════════════════════════
# WebRTCAdapter — auth handshake
# ═══════════════════════════════════════════════════════════════════════════════


class TestWebRTCAdapterAuthHandshake:
    def setup_method(self):
        import jwt as pyjwt
        self.provider   = MockWebRTCProvider()
        self.redis      = _fake_redis()
        self.settings   = _fake_settings()
        self.adapter    = _make_adapter(
            provider  = self.provider,
            redis     = self.redis,
            settings  = self.settings,
        )
        # Build a valid JWT
        self.contact_id = "contact-auth-test"
        self.jwt_token  = pyjwt.encode(
            {"sub": self.contact_id, "exp": int(time.time()) + 3600},
            self.settings.jwt_secret,
            algorithm="HS256",
        )

    def _ws_with_auth_messages(self) -> MagicMock:
        msgs = [
            json.dumps({"type": "conn.hello", "version": "1"}),
            json.dumps({"type": "conn.authenticate", "token": self.jwt_token}),
        ]
        ws = AsyncMock()
        ws.sent_messages = []

        async def _send(msg):
            ws.sent_messages.append(msg)

        ws.send_json = AsyncMock(side_effect=_send)
        ws.close     = AsyncMock()
        _q = list(msgs)

        async def _recv():
            if _q:
                return _q.pop(0)
            raise Exception("no more messages")

        ws.receive_text = AsyncMock(side_effect=_recv)
        return ws

    @pytest.mark.asyncio
    async def test_auth_handshake_success(self):
        ws = self._ws_with_auth_messages()
        session_id, contact_id, participant_id = await self.adapter._auth_handshake(
            ws, "pool-1"
        )
        assert contact_id == self.contact_id
        assert session_id != ""
        assert participant_id != ""

    @pytest.mark.asyncio
    async def test_auth_handshake_sends_conn_authenticated(self):
        ws = self._ws_with_auth_messages()
        session_id, _, _ = await self.adapter._auth_handshake(ws, "pool-1")
        # The last message sent should be conn.authenticated
        authenticated = [m for m in ws.sent_messages if m.get("type") == "conn.authenticated"]
        assert len(authenticated) == 1
        assert authenticated[0]["session_id"] == session_id

    @pytest.mark.asyncio
    async def test_auth_handshake_bad_token_raises_auth_error(self):
        ws = AsyncMock()
        ws.sent_messages = []

        async def _send(msg):
            ws.sent_messages.append(msg)

        ws.send_json = AsyncMock(side_effect=_send)
        _q = [
            json.dumps({"type": "conn.hello", "version": "1"}),
            json.dumps({"type": "conn.authenticate", "token": "invalid.token.here"}),
        ]

        async def _recv():
            return _q.pop(0)

        ws.receive_text = AsyncMock(side_effect=_recv)

        with pytest.raises(_AuthError) as exc_info:
            await self.adapter._auth_handshake(ws, "pool-1")
        assert exc_info.value.code == "invalid_token"

    @pytest.mark.asyncio
    async def test_auth_handshake_publishes_contact_open(self):
        ws = self._ws_with_auth_messages()
        await self.adapter._auth_handshake(ws, "pool-1")
        # Producer.send should have been called (at least once for contact_open)
        calls = self.adapter._producer.send.call_args_list
        payloads = [json.loads(c[0][1]) for c in calls]
        types = {p["type"] for p in payloads}
        assert "contact_open" in types

    @pytest.mark.asyncio
    async def test_auth_handshake_publishes_routing_request(self):
        ws = self._ws_with_auth_messages()
        await self.adapter._auth_handshake(ws, "pool-1")
        calls = self.adapter._producer.send.call_args_list
        payloads = [json.loads(c[0][1]) for c in calls]
        types = {p["type"] for p in payloads}
        assert "routing.request" in types

    @pytest.mark.asyncio
    async def test_auth_handshake_stores_session_in_redis(self):
        ws = self._ws_with_auth_messages()
        session_id, contact_id, _ = await self.adapter._auth_handshake(ws, "pool-1")
        stored = await self.redis.get(f"channel:webrtc:{contact_id}:session")
        assert stored == session_id


# ═══════════════════════════════════════════════════════════════════════════════
# WebRTCAdapter — close_session
# ═══════════════════════════════════════════════════════════════════════════════


class TestWebRTCAdapterCloseSession:
    def setup_method(self):
        self.adapter    = _make_adapter()
        self.session_id = str(uuid.uuid4())

    @pytest.mark.asyncio
    async def test_close_session_publishes_contact_close(self):
        await self.adapter._close_session(self.session_id, "customer_hangup")
        calls = self.adapter._producer.send.call_args_list
        assert len(calls) == 1
        payload = json.loads(calls[0][0][1])
        assert payload["type"]         == "contact_close"
        assert payload["session_id"]   == self.session_id
        assert payload["close_reason"] == "customer_hangup"
        assert payload["channel"]      == "webrtc"


# ═══════════════════════════════════════════════════════════════════════════════
# WebRTCAdapter — IWebRTCProvider protocol compliance
# ═══════════════════════════════════════════════════════════════════════════════


class TestIWebRTCProviderProtocol:
    def test_mock_satisfies_protocol(self):
        mock = MockWebRTCProvider()
        assert isinstance(mock, IWebRTCProvider)

    def test_livekit_provider_satisfies_protocol(self):
        provider = LiveKitProvider(url="wss://x", api_key="", api_secret="")
        assert isinstance(provider, IWebRTCProvider)
