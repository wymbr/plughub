"""VOZ-54 — o bot leg que CAI no meio da chamada é dito.

PROPOSIÇÃO: com a chamada de pé e a IA ouvindo e falando, o serviço de fala que passa a falhar
vira `media.degraded` (`bot_leg_lost`) e aviso ao cliente; quando volta, `media.restored` e o
aviso de que a voz voltou. Uma falha isolada não muda nada. O teto no SFU NÃO cai — o áudio tem
de seguir chegando para a volta ser percebida.
"""
from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock

import pytest

from plughub_channel_gateway.adapters import media_policy as mp
from plughub_channel_gateway.adapters import webrtc as webrtc_mod
from plughub_channel_gateway.adapters.bot_leg_health import BotLegHealth
from plughub_channel_gateway.adapters.speaches_provider import SpeachesSTTProvider

from .test_webrtc_adapter import MockWebRTCProvider, _fake_redis, _fake_settings, _make_adapter


# ── contador puro ─────────────────────────────────────────────────────────────

def test_one_failure_is_a_hiccup_two_in_a_row_is_a_loss():
    h = BotLegHealth()
    assert h.record("s", "stt", False) is None, "uma frase perdida nao e queda"
    assert h.record("s", "stt", True) is None and not h.is_lost("s"), "o sucesso zera a contagem"
    assert h.record("s", "stt", False) is None
    assert h.record("s", "stt", False) == "lost" and h.is_lost("s")
    assert h.record("s", "stt", False) is None, "a queda e dita UMA vez"


def test_restored_only_when_no_part_is_still_lost():
    h = BotLegHealth()
    h.record("s", "stt", False); h.record("s", "stt", False)
    assert h.record("s", "tts", False) is None
    assert h.record("s", "tts", False) is None, "segunda parte caindo nao repete a queda"
    assert h.lost_parts("s") == ["stt", "tts"]
    assert h.record("s", "stt", True) is None, "o TTS segue fora: ainda nao voltou"
    assert h.record("s", "tts", True) == "restored" and not h.is_lost("s")


def test_success_of_a_part_that_never_fell_does_not_restore():
    h = BotLegHealth()
    h.record("s", "tts", False); h.record("s", "tts", False)
    assert h.record("s", "stt", True) is None and h.is_lost("s")


def test_sessions_are_independent_and_forget_clears():
    h = BotLegHealth()
    h.record("a", "stt", False); h.record("a", "stt", False)
    assert not h.is_lost("b")
    h.forget("a")
    assert not h.is_lost("a") and h.record("a", "stt", False) is None


def test_rule_names_the_loss_by_its_own_reason():
    atts = {"ia1": {"framework": "native", "customer_publish": ["audio"], "agent_publish": []}}
    d = mp.degradations(atts, bot_leg_audio=False, bot_leg_reason=mp.REASON_BOT_LEG_LOST)
    assert [(x["kind"], x["reason"]) for x in d] == [("audio", "bot_leg_lost")]
    assert mp.degradations(atts, bot_leg_audio=False)[0]["reason"] == "bot_leg_unavailable", \
        "o default continua sendo a config"


# ── provedor: cada transcrição diz se o serviço respondeu ─────────────────────

@pytest.mark.asyncio
async def test_speaches_reports_each_transcription_outcome():
    stt = SpeachesSTTProvider("http://x", "m", silence_ms=40, min_speech_ms=20, gap_ms=1000)
    respostas = iter([("", None, True), ("", None, False), ("oi", 0.9, False)])

    async def _raw(*a, **k):
        return next(respostas)
    stt._transcribe_raw = _raw  # type: ignore[method-assign]
    alto, mudo = b"\xff\x7f" * 480, b"\x00\x00" * 480     # 30 ms a 16 kHz

    async def _chunks():
        for _ in range(3):
            yield alto; yield alto; yield mudo; yield mudo
    vistos: list[bool] = []
    [r async for r in stt.stream(_chunks(), sample_rate=16000, outcome=vistos.append)]
    assert vistos == [False, True, True], "erro do servico = False; VAD vazio e texto = True"


# ── adapter ───────────────────────────────────────────────────────────────────

class TestAdapter:
    def setup_method(self):
        self.redis = _fake_redis()
        self.settings = _fake_settings(webrtc_stt_enabled=False)
        self.adapter = _make_adapter(provider=MockWebRTCProvider(), redis=self.redis, settings=self.settings)
        # a config CONVERTE: STT e TTS de pé — o que cai é o serviço, no meio da chamada
        self.adapter._stt_unavailable = None
        self.adapter._tts_unavailable = None
        self.sid = str(uuid.uuid4())
        self.ws = AsyncMock()
        self.sent: list = []

        async def _send(msg):
            self.sent.append(msg)
        self.ws.send_json = AsyncMock(side_effect=_send)
        self.state = {"attendants": {"ia1": {"framework": "native", "customer_publish": ["audio"],
                                             "agent_publish": []}}, "degraded": []}
        self.ceiling = mp.customer_ceiling(self.state["attendants"], bot_leg_audio=True)

    def stream(self, tipo):
        return [json.loads(c.args[1]["payload"]) for c in self.redis.xadd.call_args_list
                if c.args and c.args[0] == f"session:{self.sid}:stream" and c.args[1].get("type") == tipo]

    async def reconcile(self):
        await self.adapter._reconcile_degradations(self.ws, self.sid, self.state, self.ceiling)

    @pytest.mark.asyncio
    async def test_control_bot_leg_up_says_nothing(self):
        await self.reconcile()
        assert self.stream("media.degraded") == [] and not self.sent

    @pytest.mark.asyncio
    async def test_loss_is_named_and_the_customer_told_then_restored(self):
        assert "audio" in self.ceiling, "o teto no SFU tem audio — a queda nao o tira"
        await self.reconcile()
        self.adapter._bot_leg_health.record(self.sid, "stt", False)
        self.adapter._bot_leg_health.record(self.sid, "stt", False)
        await self.reconcile()
        degr = self.stream("media.degraded")
        assert [(d["kind"], d["reason"]) for d in degr] == [("audio", "bot_leg_lost")]
        avisos = [m for m in self.sent if m.get("type") == "webrtc.notice"]
        assert [(m["kind"], m["reason"]) for m in avisos] == [("audio", "bot_leg_lost")], \
            "a IA e a unica que ouve: o cliente sente, mesmo com o teto intacto"
        self.adapter._bot_leg_health.record(self.sid, "stt", True)
        await self.reconcile()
        assert [d["reason"] for d in self.stream("media.restored")] == ["bot_leg_lost"]
        voltou = [m for m in self.sent if m.get("type") == "webrtc.notice"][-1]
        assert voltou["reason"] == "media_restored" and voltou["text"]

    @pytest.mark.asyncio
    async def test_with_a_human_listening_the_customer_is_not_told(self):
        self.state["attendants"]["human-u1"] = {"framework": "human", "customer_publish": ["audio"],
                                                "agent_publish": ["audio"]}
        self.adapter._bot_leg_health.record(self.sid, "tts", False)
        self.adapter._bot_leg_health.record(self.sid, "tts", False)
        await self.reconcile()
        assert [d["reason"] for d in self.stream("media.degraded")] == ["bot_leg_lost"], "os agentes sabem"
        assert not [m for m in self.sent if m.get("type") == "webrtc.notice"], \
            "o humano segue ouvindo: o cliente nao perdeu nada"

    @pytest.mark.asyncio
    async def test_outcome_transition_drives_media_changed(self, monkeypatch):
        chamadas: list = []
        self.adapter._media_changed = AsyncMock(side_effect=lambda s, r, **k: chamadas.append((s, r)))
        corrotinas: list = []
        monkeypatch.setattr(webrtc_mod, "disparar", lambda c, nome: corrotinas.append(c))
        self.adapter._speech_outcome(self.sid, "tts", False)
        assert corrotinas == [], "uma falha nao dispara nada"
        self.adapter._speech_outcome(self.sid, "tts", False)
        self.adapter._speech_outcome(self.sid, "tts", True)
        for c in corrotinas:
            await c
        assert chamadas == [(self.sid, "bot_leg_lost"), (self.sid, "bot_leg_restored")]

    @pytest.mark.asyncio
    async def test_synthesis_reports_failure_and_success(self):
        vistos: list = []
        self.adapter._speech_outcome = lambda s, p, ok: vistos.append((p, ok))  # type: ignore[method-assign]
        tts = AsyncMock()
        tts.supports_model_choice = False
        tts.output_sample_rate = 24000
        self.adapter._tts = tts
        tts.synthesize = AsyncMock(return_value=None)
        await self.adapter._synthesize_pcm(self.sid, "oi")
        tts.synthesize = AsyncMock(side_effect=RuntimeError("fora"))
        await self.adapter._synthesize_pcm(self.sid, "oi")
        tts.synthesize = AsyncMock(return_value=b"\x00\x00")
        await self.adapter._synthesize_pcm(self.sid, "oi")
        assert vistos == [("tts", False), ("tts", False), ("tts", True)]

    @pytest.mark.asyncio
    async def test_call_stt_loop_feeds_the_health(self, monkeypatch):
        """A fiação: o laço de STT da chamada passa `outcome` ao provedor que o declara — e só a ele."""
        monkeypatch.setattr(webrtc_mod, "disparar", lambda c, nome: c.close())
        recebidos: list = []

        class _STT:
            reports_outcome = True

            async def stream(self, chunks, **kw):
                recebidos.append(set(kw))
                kw["outcome"](False)
                kw["outcome"](False)
                if False:
                    yield None

        async def _chunks():
            if False:
                yield b""
        self.adapter._stt = _STT()
        await self.adapter._stt_speaker(self.sid, "agent-u1", "human-u1", _chunks())
        assert "outcome" in recebidos[0] and self.adapter._bot_leg_health.is_lost(self.sid)

        class _Legado(_STT):
            reports_outcome = False

            async def stream(self, chunks, **kw):
                recebidos.append(set(kw))
                if False:
                    yield None
        self.adapter._stt = _Legado()
        await self.adapter._stt_speaker(self.sid, "agent-u1", "human-u1", _chunks())
        assert "outcome" not in recebidos[1], "provedor que nao reporta nao recebe o parametro"
