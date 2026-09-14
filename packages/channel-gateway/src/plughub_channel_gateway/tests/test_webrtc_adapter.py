"""
tests/test_webrtc_adapter.py
Unit tests for Arc 15 Phase A WebRTC components.

Coverage:
  - media_policy               — teto de mídia por participante (VOZ-09)
  - build_room_name()          — room name helper
  - TokenGrants                — dataclass construction
  - MockWebRTCProvider         — generate_token, create_room, get_room, delete_room,
                                 list_participants, start_egress, stop_egress
  - LiveKitProvider refusal    — sem credencial/SDK recusa NOMEANDO o que falta (VOZ-01)
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

from ..adapters import media_policy
from ..adapters import webrtc_provider as webrtc_provider_mod
from ..adapters.webrtc import WebRTCAdapter, _AuthError
from ..adapters.webrtc_provider import (
    IWebRTCProvider,
    LiveKitProvider,
    MockWebRTCProvider,
    ParticipantInfo,
    RoomInfo,
    TokenGrants,
    WebRTCProviderUnavailable,
    build_room_name,
)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _fake_settings(**kwargs):
    """Build a minimal Settings-like object for tests."""
    s = MagicMock()
    s.webrtc_livekit_url            = kwargs.get("webrtc_livekit_url", "wss://localhost:7880")
    s.webrtc_livekit_public_url     = kwargs.get("webrtc_livekit_public_url", "")
    s.webrtc_livekit_api_key        = kwargs.get("webrtc_livekit_api_key", "")
    s.webrtc_livekit_api_secret     = kwargs.get("webrtc_livekit_api_secret", "")
    s.webrtc_token_ttl_s            = kwargs.get("webrtc_token_ttl_s", 3600)
    s.webrtc_default_pool_id        = kwargs.get("webrtc_default_pool_id", "webrtc_pool")
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
# LiveKitProvider — RECUSA sem credencial (VOZ-01)
# ═══════════════════════════════════════════════════════════════════════════════
#
# Esta classe se chamava `TestLiveKitProviderDevMode` e cobrava o PLACEBO: token
# `dev-token-…`, sala `RM_dev_…`, egress `EG_dev_…`. Os testes eram fiéis ao código e
# era isso o defeito — fixavam como contrato o valor plausível que deixou o Arc 15
# parecer pronto sem SFU nenhum. O contrato agora é a recusa NOMEADA.


_CREDS = dict(url="ws://livekit:7880", api_key="k", api_secret="s" * 32)


class TestLiveKitProviderRefusal:
    @pytest.mark.parametrize("vazio, env", [
        ("url",        "PLUGHUB_WEBRTC_LIVEKIT_URL"),
        ("api_key",    "PLUGHUB_WEBRTC_LIVEKIT_API_KEY"),
        ("api_secret", "PLUGHUB_WEBRTC_LIVEKIT_API_SECRET"),
    ])
    def test_credencial_ausente_recusa_nomeando_a_env(self, monkeypatch, vazio, env):
        monkeypatch.setattr(webrtc_provider_mod, "_sdk_present", lambda: True)
        with pytest.raises(WebRTCProviderUnavailable) as exc:
            LiveKitProvider(**{**_CREDS, vazio: ""})
        assert exc.value.missing == [env]
        assert env in str(exc.value)

    def test_sdk_ausente_recusa_mesmo_com_credencial(self, monkeypatch):
        monkeypatch.setattr(webrtc_provider_mod, "_sdk_present", lambda: False)
        with pytest.raises(WebRTCProviderUnavailable) as exc:
            LiveKitProvider(**_CREDS)
        assert len(exc.value.missing) == 1 and "livekit-api" in exc.value.missing[0]

    def test_tudo_ausente_nomeia_tudo(self, monkeypatch):
        monkeypatch.setattr(webrtc_provider_mod, "_sdk_present", lambda: False)
        with pytest.raises(WebRTCProviderUnavailable) as exc:
            LiveKitProvider(url="", api_key="", api_secret="")
        assert len(exc.value.missing) == 4

    def test_controle_positivo_com_credencial_e_sdk_constroi(self, monkeypatch):
        # Sem este, uma recusa INCONDICIONAL passaria nos quatro de cima.
        monkeypatch.setattr(webrtc_provider_mod, "_sdk_present", lambda: True)
        p = LiveKitProvider(**_CREDS)
        assert not hasattr(p, "_dev_mode")

    def test_assina_token_REAL_com_o_sdk(self):
        # Sem monkeypatch e sem skip: o SDK é dependência declarada. Este caminho nunca
        # tinha rodado — `with_ttl(int)` levantava `TypeError` no `to_jwt()`, e o
        # `_dev_mode` o pulava sempre. Achado pelo primeiro SFU real (VOZ-01).
        import base64
        tok = LiveKitProvider(**_CREDS).generate_token(
            TokenGrants(room_name="plughub-s1", identity="agent-u1", ttl_seconds=120, hidden=True)
        )
        part = tok.split(".")[1]
        pl = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        assert pl["sub"] == "agent-u1" and pl["iss"] == "k"
        assert pl["video"]["room"] == "plughub-s1" and pl["video"]["hidden"] is True
        assert 110 <= pl["exp"] - pl["nbf"] <= 130


class TestAdapterSemPlanoDeMidia:
    def _adapter(self, monkeypatch):
        monkeypatch.setattr(webrtc_provider_mod, "_sdk_present", lambda: True)
        return WebRTCAdapter(
            producer = _fake_producer(),
            redis    = _fake_redis(),
            settings = _fake_settings(webrtc_livekit_api_key="", webrtc_livekit_api_secret=""),
        )

    def test_boot_nao_cai_e_guarda_o_motivo(self, monkeypatch):
        a = self._adapter(monkeypatch)
        assert a._provider is None
        assert a.provider_unavailable is not None
        assert "PLUGHUB_WEBRTC_LIVEKIT_API_KEY" in str(a.provider_unavailable)

    @pytest.mark.asyncio
    async def test_get_token_recusa_em_vez_de_placebo(self, monkeypatch):
        a = self._adapter(monkeypatch)
        await a._redis.setex("channel:webrtc:s1:room_name", 60, "plughub-s1")
        with pytest.raises(WebRTCProviderUnavailable):
            await a.get_token("s1", "agent", "u1")

    @pytest.mark.asyncio
    async def test_ws_fecha_antes_de_autenticar_e_rotear(self, monkeypatch):
        a  = self._adapter(monkeypatch)
        ws = _fake_ws([json.dumps({"type": "conn.hello", "version": "1"})])
        await a.handle_ws(ws, "webrtc_pool")
        assert [m["type"] for m in ws.sent_messages] == ["conn.error"]
        assert ws.sent_messages[0]["code"] == "media_plane_unavailable"
        assert "PLUGHUB_WEBRTC_LIVEKIT_API_KEY" in ws.sent_messages[0]["message"]
        a._producer.send.assert_not_called()      # nada roteado

    def test_url_publica_vence_a_interna_no_cliente(self):
        a = _make_adapter(settings=_fake_settings(webrtc_livekit_public_url="ws://localhost:7880"))
        assert a._client_livekit_url() == "ws://localhost:7880"


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
    async def test_deliver_session_closed_removes_customer_media(self):
        self._register_ws()
        self.adapter._customer_media[self.session_id] = frozenset({"audio"})
        await self.adapter.deliver_session_closed({
            "session_id": self.session_id,
        })
        assert self.session_id not in self.adapter._customer_media


# ═══════════════════════════════════════════════════════════════════════════════
# media_policy — teto por participante (VOZ-09)
# ═══════════════════════════════════════════════════════════════════════════════


class TestMediaPolicy:
    """Teto = UNIÃO de (política do POOL ∩ consumo do framework) — VOZ-09/VOZ-10."""

    @staticmethod
    def _rec(framework, customer=("audio", "video"), agent=("audio", "video"), pool_id="p"):
        rec, aviso = media_policy.attendant_from_pool_field(framework, {
            "pool_id": pool_id, "media_policy_source": "registry",
            "media_policy": {"customer_publish": list(customer), "agent_publish": list(agent)},
        })
        assert aviso is None
        return rec

    def test_humano_consome_o_que_o_pool_oferece(self):
        assert media_policy.customer_ceiling({"h1": self._rec("human")}) == {"audio", "video"}

    def test_pool_so_de_audio_corta_o_video(self):
        assert media_policy.customer_ceiling({"h1": self._rec("human", customer=("audio",))}) == {"audio"}

    def test_ia_de_texto_nao_consome_mesmo_com_pool_de_video(self):
        assert media_policy.customer_ceiling({"ia1": self._rec("native")}) == frozenset()

    def test_uniao_especialista_de_texto_nao_rebaixa_chamada_de_video(self):
        # O defeito medido ao vivo: o 2º atendente SUBSTITUÍA o meio da sessão.
        att = {"h1": self._rec("human"), "ia1": self._rec("native", customer=())}
        assert media_policy.customer_ceiling(att) == {"audio", "video"}

    def test_framework_desconhecido_consome_nada(self):
        assert media_policy.customer_ceiling({"x": self._rec("")}) == frozenset()
        assert media_policy.customer_ceiling({"x": self._rec("algo")}) == frozenset()

    @pytest.mark.parametrize("campo, fonte", [
        ({"pool_id": "p", "media_policy_source": "registry", "media_policy": None}, "pool_sem_politica:p"),
        ({"pool_id": "p", "media_policy_source": "registry_unavailable"},          "registry_indisponivel:p"),
        ({"pool_id": "p"},                                                          "sem_leitura:p"),
        ({"pool_id": "p", "media_policy_source": "not_webrtc"},                    "sem_leitura:p"),
        ({},                                                                        "evento_sem_pool"),
    ])
    def test_ausencia_nunca_vira_permissao_e_diz_por_que(self, campo, fonte):
        rec, aviso = media_policy.attendant_from_pool_field("human", campo)
        assert aviso
        assert rec["policy_source"] == fonte
        assert media_policy.customer_ceiling({"h1": rec}) == frozenset()
        assert media_policy.agent_ceiling({"h1": rec}) == frozenset()

    def test_tipo_desconhecido_e_ignorado_e_avisado(self):
        rec, aviso = media_policy.attendant_from_pool_field("human", {
            "pool_id": "p", "media_policy_source": "registry",
            "media_policy": {"customer_publish": ["audio", "tela"], "agent_publish": []},
        })
        assert "tela" in aviso and rec["customer_publish"] == ["audio"]

    def test_teto_do_atendente_e_uniao_dos_pools_humanos(self):
        att = {"h1": self._rec("human", agent=("audio",)), "h2": self._rec("human", agent=("video",)),
               "ia1": self._rec("native", agent=("audio", "video"))}
        assert media_policy.agent_ceiling(att) == {"audio", "video"}
        assert media_policy.agent_ceiling({"ia1": self._rec("native")}) == frozenset()

    def test_chaves_lidas_sao_as_do_schema(self):
        assert media_policy.POLICY_KEYS == ("customer_publish", "agent_publish")

    def test_fontes_em_ordem_estavel(self):
        assert media_policy.publish_sources({"video", "audio"}) == ["microphone", "camera"]
        assert media_policy.publish_sources(frozenset()) == []

    def test_papel_desconhecido_e_erro(self):
        with pytest.raises(ValueError):
            media_policy.role_policy("root")
        with pytest.raises(ValueError):
            media_policy.role_policy("agent")    # agente não é papel de plataforma: vem do pool


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

    async def _room(self, customer_publish=None, attendants=None):
        await self.redis.setex(
            f"channel:webrtc:{self.session_id}:room_name", 3600, f"plughub-{self.session_id}"
        )
        if customer_publish is not None or attendants is not None:
            await self.redis.setex(
                f"channel:webrtc:{self.session_id}:media", 3600,
                json.dumps({"attendants": attendants or {},
                            "customer": {"publish": customer_publish or []}}),
            )

    @staticmethod
    def _human(agent_publish, pool_id="video_humano"):
        rec, _ = media_policy.attendant_from_pool_field("human", {
            "pool_id": pool_id, "media_policy_source": "registry",
            "media_policy": {"customer_publish": ["audio", "video"], "agent_publish": agent_publish},
        })
        return rec

    @pytest.mark.asyncio
    async def test_get_token_returns_none_when_room_not_ready(self):
        result = await self.adapter.get_token(self.session_id, "agent", "agent-x")
        assert result is None

    @pytest.mark.asyncio
    async def test_resposta_traz_tetos_e_nao_traz_meio_unico(self):
        await self._room(customer_publish=["audio"], attendants={"h1": self._human(["audio", "video"])})
        result = await self.adapter.get_token(self.session_id, "agent", "agente_v1")
        assert result["publish"] == ["audio", "video"]
        assert result["customer_publish"] == ["audio"]
        assert result["hidden"] is False
        assert result["policy_sources"] == ["pool:video_humano"]
        assert "negotiated_medium" not in result
        assert result["livekit_url"] == self.settings.webrtc_livekit_url

    @pytest.mark.asyncio
    async def test_agente_recortado_pelo_agent_publish_do_pool(self):
        await self._room(attendants={"h1": self._human(["audio"])})
        result = await self.adapter.get_token(self.session_id, "agent", "agent-1")
        grants = self.provider.tokens_generated[-1]["grants"]
        assert grants.can_publish_sources == ("microphone",)
        assert grants.hidden is False and result["publish"] == ["audio"]

    @pytest.mark.asyncio
    async def test_agente_sem_pool_com_politica_entra_so_assistindo_e_avisa(self, caplog):
        await self._room()
        with caplog.at_level("WARNING"):
            result = await self.adapter.get_token(self.session_id, "agent", "agent-1")
        assert self.provider.tokens_generated[-1]["grants"].can_publish_sources == ()
        assert result["publish"] == [] and result["policy_sources"] == []
        assert "sem teto de publicacao" in caplog.text

    @pytest.mark.asyncio
    async def test_supervisor_oculto_sem_fonte(self):
        await self._room()
        result = await self.adapter.get_token(self.session_id, "supervisor", "sup-1")
        grants = self.provider.tokens_generated[-1]["grants"]
        assert grants.can_publish_sources == ()
        assert grants.hidden is True and result["hidden"] is True
        assert result["publish"] == []

    @pytest.mark.asyncio
    async def test_identity_in_grants(self):
        await self._room()
        await self.adapter.get_token(self.session_id, "agent", "my-agent")
        grants = self.provider.tokens_generated[-1]["grants"]
        assert "my-agent" in grants.identity


class TestTokenFontesNoSDK:
    """A regra "lista vazia no LiveKit = TODAS as fontes" tem de virar can_publish=False."""

    def _payload(self, sources):
        import base64
        tok = LiveKitProvider(url="ws://x", api_key="k", api_secret="s" * 32).generate_token(
            TokenGrants(room_name="r", identity="i", can_publish_sources=sources)
        )
        part = tok.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))["video"]

    def test_teto_vazio_desliga_publish(self):
        v = self._payload(())
        assert v.get("canPublish") is False

    def test_teto_com_microfone(self):
        v = self._payload(("microphone",))
        assert v.get("canPublish") is True and v.get("canPublishSources") == ["microphone"]


# ═══════════════════════════════════════════════════════════════════════════════
# WebRTCAdapter — atendentes → teto do cliente (VOZ-09)
# ═══════════════════════════════════════════════════════════════════════════════


_FULL = {"customer_publish": ["audio", "video"], "agent_publish": ["audio", "video"]}


def _assigned(framework: str, instance_id: str, *, policy=_FULL, pool_id: str = "p",
              source: str = "registry", **extra) -> dict:
    pool = {"pool_id": pool_id, "media_policy_source": source}
    if source == "registry":
        pool["media_policy"] = policy
    return {"type": "routing.assigned", "framework": framework,
            "instance_id": instance_id, "pool": json.dumps(pool), **extra}


def _fw(state: dict) -> dict:
    return {iid: rec["framework"] for iid, rec in state["attendants"].items()}


class TestWebRTCAdapterMediaCeiling:
    def setup_method(self):
        self.provider   = MockWebRTCProvider()
        self.redis      = _fake_redis()
        self.settings   = _fake_settings(webrtc_stt_enabled=False)
        self.adapter    = _make_adapter(provider=self.provider, redis=self.redis, settings=self.settings)
        self.session_id = str(uuid.uuid4())
        self.ws         = AsyncMock()
        self.ws.sent_messages = []

        async def _send(msg):
            self.ws.sent_messages.append(msg)

        self.ws.send_json = AsyncMock(side_effect=_send)

    async def _state(self):
        return json.loads(await self.redis.get(f"channel:webrtc:{self.session_id}:media"))

    @pytest.mark.asyncio
    async def test_humano_atende_cliente_pode_audio_e_video(self):
        await self.redis.setex(f"session:{self.session_id}:contact_id", 3600, "c1")
        await self.adapter._on_routing_assigned(self.ws, self.session_id, _assigned("human", "h1"), self.settings)
        msg = self.ws.sent_messages[0]
        assert msg["type"] == "webrtc.ready"
        assert msg["publish"] == ["audio", "video"]
        assert msg["policy_sources"] == ["pool:p"]
        assert "negotiated_medium" not in msg
        grants = self.provider.tokens_generated[-1]["grants"]
        assert grants.identity == "customer-c1"
        assert grants.can_publish_sources == ("microphone", "camera")
        st = await self._state()
        assert _fw(st) == {"h1": "human"}
        assert st["customer"]["reason"] == "attendant_joined:human"
        assert len(self.provider.rooms_created) == 1

    @pytest.mark.asyncio
    async def test_ia_de_texto_atende_cliente_sem_midia(self):
        await self.adapter._on_routing_assigned(self.ws, self.session_id, _assigned("native", "ia1"), self.settings)
        assert self.ws.sent_messages[0]["publish"] == []
        assert self.provider.tokens_generated[-1]["grants"].can_publish_sources == ()

    @pytest.mark.asyncio
    async def test_especialista_de_texto_NAO_rebaixa_o_cliente(self):
        # O cenário do vermelho ao vivo de 2026-09-14.
        await self.adapter._on_routing_assigned(self.ws, self.session_id, _assigned("human", "h1"), self.settings)
        await self.adapter._on_routing_renegotiate(self.ws, self.session_id, _assigned("native", "ia1"), self.settings)
        assert [m["type"] for m in self.ws.sent_messages] == ["webrtc.ready"]     # nada mudou
        assert self.provider.permission_updates == []
        st = await self._state()
        assert _fw(st) == {"h1": "human", "ia1": "native"}
        assert st["customer"]["publish"] == ["audio", "video"]

    @pytest.mark.asyncio
    async def test_humano_sai_teto_cai_no_sfu_e_no_cliente(self):
        await self.redis.setex(f"session:{self.session_id}:contact_id", 3600, "c1")
        self.provider.joined.add("customer-c1")
        await self.adapter._on_routing_assigned(self.ws, self.session_id, _assigned("native", "ia1"), self.settings)
        await self.adapter._on_routing_renegotiate(self.ws, self.session_id, _assigned("human", "h1"), self.settings)
        await self.adapter._on_attendant_left(self.ws, self.session_id, {"type": "participant_left", "author_id": "h1"})
        tipos = [m["type"] for m in self.ws.sent_messages]
        assert tipos == ["webrtc.ready", "webrtc.media", "webrtc.media"]
        subiu, caiu = self.ws.sent_messages[1], self.ws.sent_messages[2]
        assert subiu["publish"] == ["audio", "video"] and subiu["reason"] == "attendant_joined:human"
        assert caiu["publish"] == [] and caiu["reason"] == "attendant_left:human"
        assert caiu["token"]      # token novo para quem ainda vai entrar
        assert [u["can_publish_sources"] for u in self.provider.permission_updates] == [
            ("microphone", "camera"), (),
        ]
        assert self.provider.permission_updates[-1]["identity"] == "customer-c1"
        assert self.adapter._customer_media[self.session_id] == frozenset()

    @pytest.mark.asyncio
    async def test_saida_de_quem_nao_e_atendente_nao_mexe_e_avisa(self, caplog):
        await self.adapter._on_routing_assigned(self.ws, self.session_id, _assigned("human", "h1"), self.settings)
        with caplog.at_level("WARNING"):
            await self.adapter._on_attendant_left(self.ws, self.session_id, {"author_id": "desconhecido"})
        assert _fw(await self._state()) == {"h1": "human"}
        assert "desconhecido" in caplog.text

    @pytest.mark.asyncio
    async def test_framework_ausente_consome_nada_e_avisa(self, caplog):
        with caplog.at_level("WARNING"):
            await self.adapter._on_routing_assigned(
                self.ws, self.session_id, {"type": "routing.assigned", "instance_id": "x"}, self.settings,
            )
        assert self.ws.sent_messages[0]["publish"] == []
        assert "sem framework reconhecido" in caplog.text

    @pytest.mark.asyncio
    async def test_pool_so_de_audio_recorta_o_cliente(self):
        await self.adapter._on_routing_assigned(
            self.ws, self.session_id,
            _assigned("human", "h1", policy={"customer_publish": ["audio"], "agent_publish": []}),
            self.settings,
        )
        assert self.ws.sent_messages[0]["publish"] == ["audio"]
        assert self.provider.tokens_generated[-1]["grants"].can_publish_sources == ("microphone",)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kw, fonte, nivel", [
        ({"policy": None},                   "pool_sem_politica:p",     "WARNING"),
        ({"source": "registry_unavailable"}, "registry_indisponivel:p", "ERROR"),
        ({"source": "not_webrtc"},           "sem_leitura:p",           "WARNING"),
    ])
    async def test_politica_ausente_nao_oferece_nada_e_diz(self, caplog, kw, fonte, nivel):
        with caplog.at_level("WARNING"):
            await self.adapter._on_routing_assigned(
                self.ws, self.session_id, _assigned("human", "h1", **kw), self.settings,
            )
        msg = self.ws.sent_messages[0]
        assert msg["publish"] == [] and msg["policy_sources"] == [fonte]
        assert self.provider.tokens_generated[-1]["grants"].can_publish_sources == ()
        assert any(r.levelname == nivel and "webrtc media" in r.getMessage() for r in caplog.records)

    @pytest.mark.asyncio
    async def test_estado_anterior_a_voz10_nao_vira_permissao(self, caplog):
        await self.redis.setex(
            f"channel:webrtc:{self.session_id}:media", 3600,
            json.dumps({"attendants": {"h0": "human"}, "customer": {"publish": ["audio", "video"]}}),
        )
        with caplog.at_level("WARNING"):
            await self.adapter._on_routing_renegotiate(
                self.ws, self.session_id, _assigned("native", "ia1"), self.settings,
            )
        assert self.ws.sent_messages[-1]["publish"] == []
        assert "estado anterior a VOZ-10" in caplog.text

    @pytest.mark.asyncio
    async def test_falha_no_sfu_ao_aplicar_e_barulhenta(self, caplog):
        await self.adapter._on_routing_assigned(self.ws, self.session_id, _assigned("native", "ia1"), self.settings)

        async def _boom(*a, **k):
            raise RuntimeError("sfu fora")
        self.provider.update_participant_permission = _boom
        with caplog.at_level("ERROR"):
            await self.adapter._on_routing_renegotiate(self.ws, self.session_id, _assigned("human", "h1"), self.settings)
        assert "FALHOU aplicar teto" in caplog.text
        assert self.ws.sent_messages[-1]["type"] == "webrtc.media"

    @pytest.mark.asyncio
    async def test_persiste_room_e_estado_de_midia(self):
        await self.adapter._on_routing_assigned(self.ws, self.session_id, _assigned("human", "h1"), self.settings)
        assert await self.redis.get(f"channel:webrtc:{self.session_id}:room_name") == f"plughub-{self.session_id}"
        assert await self.redis.get(f"channel:webrtc:{self.session_id}:medium") is None


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

    def test_livekit_provider_satisfies_protocol(self, monkeypatch):
        monkeypatch.setattr(webrtc_provider_mod, "_sdk_present", lambda: True)
        provider = LiveKitProvider(url="ws://x", api_key="k", api_secret="s" * 32)
        assert isinstance(provider, IWebRTCProvider)
