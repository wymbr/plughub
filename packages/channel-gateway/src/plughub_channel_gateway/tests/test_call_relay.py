"""
test_call_relay.py — WCH-12: com DUAS réplicas, a chamada é atendida por quem a segura.

As proposições, cada uma com o controle ao lado:

  * SAÍDA — mensagem de uma chamada consumida pela réplica que NÃO a segura chega à dona, e só a
    ela (a réplica que consumiu não fala, e o legado Twilio não recebe); a ordem do Kafka é a
    ordem na dona mesmo com o Redis respondendo fora de ordem. Controle: sessão sem dona é
    entregue localmente, como antes (o `voice` legado segue funcionando).
  * CHAT — no contato de chat, o chat é entregue por quem consumiu e só a FALA vai à dona da
    chamada presa a ele.
  * WEBHOOK — o `participant_left` de uma sala SIP que chega à outra réplica desliga a chamada na
    dona; o que chega enquanto a dona ainda abre o contato ESPERA, não é perdido.
  * POSSE — liberar só apaga a posse se ela ainda for desta réplica; dona que não ouve é ERROR e
    nada é entregue no lugar dela.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
from unittest.mock import AsyncMock, MagicMock

import pytest

from ..adapters.voice_router import VoiceChannelRouter
from ..adapters.webrtc import WebRTCAdapter
from ..call_relay import CallRelay, deliver_channel, owner_key
from ..outbound_consumer import OutboundConsumer
from .test_sip_leg import PARTICIPANTE, SALA, _Redis, _adapter, _endpoint, _eventos


class _PubSub:
    def __init__(self, hub: "_Hub") -> None:
        self._hub, self._q = hub, asyncio.Queue()

    async def subscribe(self, canal: str) -> None:
        self._hub.subs.setdefault(canal, []).append(self._q)
        await self._q.put({"type": "subscribe", "channel": canal, "data": 1})

    async def listen(self):
        while True:
            yield await self._q.get()


class _Hub(_Redis):
    """UM Redis para as duas réplicas: chaves, o `eval` do release e o pub/sub. `get` responde com
    atraso aleatório, para que a ordem de chegada NÃO seja a de pedido — é a condição que o
    encadeamento por sessão existe para vencer."""

    def __init__(self, jitter: bool = False) -> None:
        super().__init__()
        self.subs: dict[str, list[asyncio.Queue]] = {}
        self.jitter = jitter

    async def get(self, k):
        if self.jitter:
            await asyncio.sleep(random.uniform(0, 0.02))
        return self.kv.get(k)

    async def eval(self, script, n, key, valor):
        assert "redis.call('get'" in script and n == 1
        if self.kv.get(key) == valor:
            del self.kv[key]
            return 1
        return 0

    async def publish(self, canal, corpo):
        filas = self.subs.get(canal, [])
        for q in filas:
            await q.put({"type": "message", "channel": canal, "data": corpo})
        return len(filas)

    def pubsub(self):
        return _PubSub(self)


async def _ate(cond, teto: float = 2.0) -> None:
    fim = asyncio.get_running_loop().time() + teto
    while not cond():
        if asyncio.get_running_loop().time() > fim:
            raise AssertionError("condicao nao cumprida no prazo")
        await asyncio.sleep(0.01)


class _Replica:
    """Uma réplica do gateway: adapter WebRTC real (com o Redis compartilhado), relay e consumidor."""

    def __init__(self, hub: _Hub, nome: str) -> None:
        self.ad, self.producer = _adapter()
        self.ad._redis = hub
        self.relay = CallRelay(redis=hub, instance_id=nome, ttl=60)
        self.ad.attach_relay(self.relay)
        self.falas: list[str] = []
        assert hasattr(WebRTCAdapter, "_speak")
        self.ad._speak = lambda s, t, played=None: self.falas.append(t)
        self.legado = MagicMock()
        self.legado.deliver_text = AsyncMock()
        self.chat = MagicMock()
        self.chat.deliver_text = AsyncMock()
        self.consumer = OutboundConsumer(
            adapters={"voice": VoiceChannelRouter(self.ad, self.legado), "webrtc": self.ad,
                      "webchat": self.chat},
            settings=MagicMock(tenant_id="t"), relay=self.relay)
        self.escuta: asyncio.Task | None = None

    async def ouvir(self) -> None:
        self.escuta = asyncio.create_task(self.relay.listen())
        await _ate(lambda: deliver_channel(self.relay.instance_id) in self.relay._redis.subs)

    def parar(self) -> None:
        if self.escuta is not None:
            self.escuta.cancel()


def _texto(sid: str, texto: str, canal: str = "voice") -> dict:
    return {"type": "message.text", "session_id": sid, "contact_id": "+5511999990000",
            "channel": canal, "author": {"type": "agent_ai"}, "content": {"text": texto},
            "timestamp": "2026-09-24T00:00:00Z"}


@pytest.fixture
async def duas(monkeypatch):
    _endpoint(monkeypatch)
    hub = _Hub()
    a, b = _Replica(hub, "inst-a"), _Replica(hub, "inst-b")
    await a.ouvir()
    await b.ouvir()
    yield hub, a, b
    a.parar()
    b.parar()


class TestSaida:
    async def test_fala_consumida_pela_outra_replica_sai_na_dona(self, duas):
        hub, a, b = duas
        await a.ad.on_livekit_event("participant_joined", SALA, PARTICIPANTE)
        sid = next(iter(a.ad._sip))
        assert hub.kv[owner_key(sid)] == "inst-a" and a.ad.holds_call(sid) and not b.ad.holds_call(sid)

        await b.consumer._dispatch(_texto(sid, "Ola, em que posso ajudar?"))
        await _ate(lambda: a.falas)
        assert a.falas == ["Ola, em que posso ajudar?"]
        assert b.falas == [] and b.legado.deliver_text.await_count == 0

    async def test_ordem_do_kafka_e_a_ordem_na_dona(self, duas):
        hub, a, b = duas
        await a.ad.on_livekit_event("participant_joined", SALA, PARTICIPANTE)
        sid = next(iter(a.ad._sip))
        hub.jitter = True                                    # respostas do Redis fora de ordem
        frases = [f"frase {i}" for i in range(12)]
        for f in frases:
            await b.consumer._dispatch(_texto(sid, f))
        await _ate(lambda: len(a.falas) == len(frases))
        assert a.falas == frases

    async def test_sem_dona_entrega_local_como_antes(self, duas):
        hub, a, b = duas
        await b.consumer._dispatch(_texto("sessao-twilio", "oi"))
        await _ate(lambda: b.legado.deliver_text.await_count == 1)
        assert a.falas == [] and a.legado.deliver_text.await_count == 0

    async def test_dona_local_nao_passa_pelo_redis(self, duas):
        hub, a, b = duas
        await a.ad.on_livekit_event("participant_joined", SALA, PARTICIPANTE)
        sid = next(iter(a.ad._sip))
        hub.subs.clear()                                     # nenhum encaminhamento possível
        await a.consumer._dispatch(_texto(sid, "direto"))
        assert a.falas == ["direto"]                         # síncrono: antes de qualquer await


class TestChat:
    async def test_chat_entregue_aqui_e_fala_na_dona_da_chamada(self, duas):
        hub, a, b = duas
        sid = "sessao-chat"
        # a chamada presa ao chat está em A
        a.ad._attached.add(sid)
        a.ad._call_started.add(sid)
        await a.ad._claim_call(sid)
        msg = _texto(sid, "texto da IA", canal="webchat")
        await b.consumer._dispatch(msg)
        await _ate(lambda: a.falas)
        assert a.falas == ["texto da IA"] and b.falas == []
        assert b.chat.deliver_text.await_count == 1 and a.chat.deliver_text.await_count == 0

    async def test_chat_sem_chamada_nao_encaminha_nada(self, duas, caplog):
        hub, a, b = duas
        caplog.set_level(logging.INFO, logger="plughub.channel_gateway.call_relay")
        await b.consumer._dispatch(_texto("chat-sem-chamada", "oi", canal="webchat"))
        await asyncio.gather(*b.relay._tail.values())
        assert b.chat.deliver_text.await_count == 1
        assert not [r for r in caplog.records if "encaminhado" in r.getMessage()]


class TestWebhook:
    async def test_participant_left_na_outra_replica_desliga_na_dona(self, duas):
        hub, a, b = duas
        await a.ad.on_livekit_event("participant_joined", SALA, PARTICIPANTE)
        sid = next(iter(a.ad._sip))
        await b.ad.on_livekit_event("participant_left", SALA, PARTICIPANTE)
        await _ate(lambda: not a.ad.is_sip_session(sid))
        fech = [e for e in _eventos(a.producer) if e.get("event_type") == "contact_closed"]
        assert len(fech) == 1 and fech[0]["close_reason"] == "customer_hangup"
        assert not _eventos(b.producer)
        assert owner_key(sid) not in hub.kv                  # a posse saiu junto

    async def test_evento_durante_a_abertura_espera_a_dona(self, duas):
        hub, a, b = duas
        hub.kv[f"channel:sip:room:{SALA}"] = "abrindo"       # A ainda abrindo o contato
        saida = asyncio.create_task(b.ad.on_livekit_event("participant_left", SALA, PARTICIPANTE))
        await asyncio.sleep(0.3)
        assert not saida.done()                              # esperando, não descartado
        del hub.kv[f"channel:sip:room:{SALA}"]
        await a.ad.on_livekit_event("participant_joined", SALA, PARTICIPANTE)
        await saida
        sid = next(iter(a.ad._sip), None)
        await _ate(lambda: not a.ad.is_sip_session(sid) if sid else True)
        assert sid is not None


class TestPosse:
    async def test_browser_fechando_libera_a_posse(self, duas):
        """Browser `webrtc` não passa pelo teardown SIP: é o `_close_session` que libera."""
        hub, a, b = duas
        await a.ad._claim_call("sessao-browser")
        assert hub.kv[owner_key("sessao-browser")] == "inst-a"
        await a.ad._close_session("sessao-browser", "customer_disconnect")
        assert owner_key("sessao-browser") not in hub.kv and not a.ad.holds_call("sessao-browser")

    async def test_chamada_do_chat_encerrada_libera_a_posse(self, duas):
        hub, a, b = duas
        a.ad._attached.add("sessao-chat")
        await a.ad._claim_call("sessao-chat")
        await a.ad._end_attached_call("sessao-chat", "customer_hangup")
        assert owner_key("sessao-chat") not in hub.kv and not a.ad.holds_call("sessao-chat")

    async def test_release_so_apaga_se_for_desta_replica(self):
        hub = _Hub()
        a, b = CallRelay(hub, "inst-a", 60), CallRelay(hub, "inst-b", 60)
        await a.claim("s1")
        await b.claim("s1")                                  # re-anexada em B
        await a.release("s1")                                # o release atrasado de A
        assert hub.kv[owner_key("s1")] == "inst-b"
        await b.release("s1")
        assert owner_key("s1") not in hub.kv

    async def test_dona_que_nao_ouve_e_erro_e_nada_e_entregue_no_lugar(self, caplog):
        hub = _Hub()
        relay = CallRelay(hub, "inst-b", 60)
        hub.kv[owner_key("s1")] = "inst-morta"
        local = AsyncMock()
        caplog.set_level(logging.ERROR, logger="plughub.channel_gateway.call_relay")
        await relay.send_later("s1", {"kind": "outbound", "payload": {}}, unowned=local)
        assert local.await_count == 0
        assert any("NAO ouve" in r.getMessage() for r in caplog.records)

    async def test_envelope_sem_tratador_e_dito(self, caplog):
        relay = CallRelay(_Hub(), "inst-a", 60)
        caplog.set_level(logging.ERROR, logger="plughub.channel_gateway.call_relay")
        relay.dispatch_raw(json.dumps({"kind": "desconhecido", "session_id": "s"}))
        assert any("sem tratador" in r.getMessage() for r in caplog.records)
