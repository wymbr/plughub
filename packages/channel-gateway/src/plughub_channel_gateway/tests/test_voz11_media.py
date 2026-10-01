"""VOZ-11 fatias b, c e d — estado real de mídia, capacidade do recurso, degradação nomeada.

Modelo de videoconferência (decisão do dono, 2026-09-30): câmera e microfone desligados são
ESCOLHA e nunca viram degradação; o que se nomeia é a INCAPACIDADE."""
from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock

import pytest

from plughub_channel_gateway import call_events
from plughub_channel_gateway.adapters import media_policy as mp

from .test_webrtc_adapter import (
    MockWebRTCProvider, _assigned, _fake_redis, _fake_settings, _make_adapter,
)

VIDEO_POOL = {"customer_publish": ["audio", "video"], "agent_publish": ["audio", "video"]}


# ── regra pura (fatia d) ──────────────────────────────────────────────────────

def _rec(fw, **kw):
    return {"framework": fw, "customer_publish": ["audio", "video"],
            "agent_publish": ["audio", "video"], **kw}


def test_ai_of_text_in_a_video_pool_is_named_by_kind_and_reason():
    sem_bot = mp.degradations({"ia1": _rec("native")}, bot_leg_audio=False)
    assert [(d["kind"], d["direction"], d["reason"]) for d in sem_bot] == [
        ("audio", "to_attendant", "bot_leg_unavailable"),
        ("video", "to_attendant", "attendant_cannot_consume"),
    ]
    com_bot = mp.degradations({"ia1": _rec("native")}, bot_leg_audio=True)
    assert [(d["kind"], d["reason"]) for d in com_bot] == [("video", "attendant_cannot_consume")]


def test_human_capacity_is_only_claimed_when_known():
    assert mp.degradations({"human-u": _rec("human")}) == [], "capacidade nao sabida nao e queixa"
    sem_camera = mp.degradations({"human-u": _rec("human", capacity=["audio"])})
    assert [(d["kind"], d["direction"], d["reason"]) for d in sem_camera] == [
        ("video", "to_customer", "attendant_no_device")]
    assert mp.degradations({"human-u": _rec("human", capacity=["audio", "video"])}) == []


def test_the_customer_is_told_only_when_his_call_changes():
    atts = {"h": _rec("human", capacity=["audio", "video"]), "ia": _rec("native")}
    d_ia_video = next(d for d in mp.degradations(atts, True) if d["participant"] == "ia")
    # o humano vê o vídeo: a perna da IA não serve, mas o cliente não perde nada
    assert mp.customer_feels(d_ia_video, atts, frozenset({"audio", "video"})) is False
    assert mp.customer_feels(d_ia_video, {"ia": atts["ia"]}, frozenset({"audio"})) is True
    # humano sem câmera, outro humano com câmera: o cliente segue vendo alguém
    atts2 = {"h1": _rec("human", capacity=["audio"]), "h2": _rec("human", capacity=["audio", "video"])}
    d = mp.degradations(atts2)[0]
    assert mp.customer_feels(d, atts2, frozenset({"audio", "video"})) is False
    assert mp.customer_feels(d, {"h1": atts2["h1"]}, frozenset({"audio", "video"})) is True


def test_capacity_parsing():
    assert mp.parse_capacity(None) is None
    assert mp.parse_capacity("") == []
    assert mp.parse_capacity("video,audio,screen") == ["audio", "video"]


# ── adapter: degradação dita (fatia d) ────────────────────────────────────────

class _Setup:
    def setup_method(self):
        self.provider = MockWebRTCProvider()
        self.redis = _fake_redis()
        self.settings = _fake_settings(webrtc_stt_enabled=False)
        self.adapter = _make_adapter(provider=self.provider, redis=self.redis, settings=self.settings)
        self.sid = str(uuid.uuid4())
        self.ws = AsyncMock()
        self.sent: list = []

        async def _send(msg):
            self.sent.append(msg)
        self.ws.send_json = AsyncMock(side_effect=_send)

    def stream(self, tipo):
        return [c.args[1] for c in self.redis.xadd.call_args_list
                if c.args and c.args[0] == f"session:{self.sid}:stream" and c.args[1].get("type") == tipo]


class TestDegradation(_Setup):
    @pytest.mark.asyncio
    async def test_ai_alone_in_a_video_call_is_named_once_and_the_customer_told(self):
        await self.adapter._on_routing_assigned(self.ws, self.sid, _assigned("native", "ia1", policy=VIDEO_POOL), self.settings)
        degr = [json.loads(f["payload"]) for f in self.stream("media.degraded")]
        assert {(d["participant"], d["kind"]) for d in degr} == {("ia1", "audio"), ("ia1", "video")}
        assert all(f["visibility"] == "agents_only" for f in self.stream("media.degraded"))
        notices = [m for m in self.sent if m.get("type") == "webrtc.notice"]
        assert {m["kind"] for m in notices} == {"audio", "video"} and all(m["text"] for m in notices)
        assert len(self.stream("system_notice")) == 2, "o aviso tambem e fato do stream (historico)"
        tipos = [m.get("type") for m in self.sent]
        assert tipos.index("webrtc.ready") < tipos.index("webrtc.notice"), \
            "o aviso sai depois da chamada existir, nunca antes"
        # o mesmo estado de novo não repete nada
        n = len(self.stream("media.degraded"))
        st = await self.adapter._load_media_state(self.sid)
        await self.adapter._apply_customer_ceiling(self.ws, self.sid, st, "replay")
        assert len(self.stream("media.degraded")) == n

    @pytest.mark.asyncio
    async def test_human_joining_does_not_restore_the_ai_leg_and_leaving_ai_is_not_restored(self):
        await self.adapter._on_routing_assigned(self.ws, self.sid, _assigned("native", "ia1", policy=VIDEO_POOL), self.settings)
        await self.adapter._on_routing_renegotiate(self.ws, self.sid, _assigned("human", "h1", policy=VIDEO_POOL), self.settings)
        assert self.stream("media.restored") == [], "a perna da IA continua sem video"
        await self.adapter._on_attendant_left(self.ws, self.sid, {"type": "participant_left", "author_id": "ia1"})
        assert self.stream("media.restored") == [], "quem saiu nao 'voltou a servir'"

    @pytest.mark.asyncio
    async def test_choice_is_never_a_degradation(self):
        """Controle: humano com câmera e microfone — desligar é escolha, nada é dito."""
        await self.redis.setex(f"session:{self.sid}:contact_id", 3600, "c1")
        await self.adapter._on_routing_assigned(self.ws, self.sid, _assigned("human", "h1", policy=VIDEO_POOL), self.settings)
        assert self.stream("media.degraded") == [] and not [m for m in self.sent if m.get("type") == "webrtc.notice"]


# ── capacidade do humano (fatia c) ────────────────────────────────────────────

class TestCapacity(_Setup):
    async def _human_in_room(self):
        await self.redis.setex(f"channel:webrtc:{self.sid}:room_name", 3600, f"plughub-{self.sid}")
        await self.adapter._on_routing_assigned(self.ws, self.sid, _assigned("human", "human-u1", policy=VIDEO_POOL), self.settings)
        self.adapter._owned.add(self.sid)

    @pytest.mark.asyncio
    async def test_no_camera_is_recorded_and_named_to_both_sides(self):
        await self._human_in_room()
        await self.adapter.get_token(session_id=self.sid, role="agent", identity="u1", capable="audio")
        st = await self.adapter._load_media_state(self.sid)
        assert st["attendants"]["human-u1"]["capacity"] == ["audio"]
        degr = [json.loads(f["payload"]) for f in self.stream("media.degraded")]
        assert degr == [{"participant": "human-u1", "framework": "human", "kind": "video",
                         "direction": "to_customer", "reason": "attendant_no_device"}]
        assert [m["kind"] for m in self.sent if m.get("type") == "webrtc.notice"] == ["video"]

    @pytest.mark.asyncio
    async def test_old_console_without_capable_claims_nothing(self):
        await self._human_in_room()
        await self.adapter.get_token(session_id=self.sid, role="agent", identity="u1", capable=None)
        st = await self.adapter._load_media_state(self.sid)
        assert "capacity" not in st["attendants"]["human-u1"]
        assert self.stream("media.degraded") == []

    @pytest.mark.asyncio
    async def test_camera_back_is_restored(self):
        await self._human_in_room()
        await self.adapter.get_token(session_id=self.sid, role="agent", identity="u1", capable="audio")
        await self.adapter.get_token(session_id=self.sid, role="agent", identity="u1", capable="audio,video")
        rest = [json.loads(f["payload"]) for f in self.stream("media.restored")]
        assert [(d["participant"], d["kind"]) for d in rest] == [("human-u1", "video")]


# ── estado real (fatia b) ─────────────────────────────────────────────────────

class _Hash(_Setup):
    def setup_method(self):
        super().setup_method()
        self.h: dict = {}
        self.s: dict = {}

        async def hget(k, f):
            return self.h.get(k, {}).get(f)

        async def hset(k, f, v):
            self.h.setdefault(k, {})[f] = v

        async def sadd(k, *m):
            self.s.setdefault(k, set()).update(m)

        async def smembers(k):
            return set(self.s.get(k, set()))
        self.redis.hget, self.redis.hset = AsyncMock(side_effect=hget), AsyncMock(side_effect=hset)
        self.redis.sadd, self.redis.smembers = AsyncMock(side_effect=sadd), AsyncMock(side_effect=smembers)


class TestTracks(_Hash):
    async def track(self, identity, source, published, kind="STANDARD"):
        await self.adapter.on_livekit_event(
            "track_published" if published else "track_unpublished", f"plughub-{self.sid}",
            {"identity": identity, "kind": kind}, track={"sid": "TR", "type": "VIDEO", "source": source})

    @pytest.mark.asyncio
    async def test_camera_on_and_off_is_the_real_state_and_flowed_remembers(self):
        await self.track("customer-c1", "CAMERA", True)
        await self.track("customer-c1", "CAMERA", False)
        await self.track("agent-u1", "MICROPHONE", True)
        live = self.h[f"channel:webrtc:{self.sid}:live"]
        assert json.loads(live["customer-c1"]) == {"role": "customer", "audio": False, "video": False}
        assert json.loads(live["agent-u1"])["audio"] is True
        assert await self.adapter.media_flowed(self.sid) == {"customer": ["video"], "agent": ["audio"]}
        evs = [json.loads(f["payload"]) for f in self.stream("media.track")]
        assert [(e["role"], e["kind"], e["state"]) for e in evs] == [
            ("customer", "video", "on"), ("customer", "video", "off"), ("agent", "audio", "on")]

    @pytest.mark.asyncio
    async def test_screen_share_and_recorder_are_not_camera_or_microphone(self):
        await self.track("customer-c1", "SCREEN_SHARE", True)
        await self.track("EG_x", "MICROPHONE", True, kind="EGRESS")
        assert self.stream("media.track") == [] and not self.h

    @pytest.mark.asyncio
    async def test_ai_voice_is_the_attendant_and_the_silent_line_is_not_media(self):
        await self.track(f"voz-{self.sid[:8]}", "MICROPHONE", True)
        await self.track(f"linha-{self.sid[:8]}", "MICROPHONE", True)
        assert await self.adapter.media_flowed(self.sid) == {"agent": ["audio"]}
        assert [json.loads(f["payload"])["identity"] for f in self.stream("media.track")] == [f"voz-{self.sid[:8]}"]

    @pytest.mark.asyncio
    async def test_unknown_room_is_ignored_and_said(self, caplog):
        with caplog.at_level("INFO"):
            await self.adapter.on_livekit_event("track_published", "sala-alheia",
                                                {"identity": "customer-c1", "kind": "STANDARD"},
                                                track={"sid": "T", "type": "AUDIO", "source": "MICROPHONE"})
        assert self.stream("media.track") == [] and "sem sessao conhecida" in caplog.text


def test_call_end_carries_what_flowed_not_the_ceiling():
    begun = call_events.started(tenant_id="t", session_id="s", call_id="1-0", channel="webchat",
                                pool_id="p", customer_publish=["audio", "video"],
                                started_at="2026-09-30T10:00:00+00:00")
    ev = call_events.ended(begun=begun, ended_at="2026-09-30T10:01:00+00:00", end_reason="hangup",
                           flowed={"customer": ["audio"], "agent": ["audio", "video"]})
    assert ev["customer_flowed"] == ["audio"] and ev["agent_flowed"] == ["audio", "video"]
    assert call_events.ended(begun=begun, ended_at="2026-09-30T10:01:00+00:00",
                             end_reason="x")["customer_flowed"] == []
