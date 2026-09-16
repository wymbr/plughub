"""
test_menu_signal_queue.py — sinais da plataforma numa fila PRÓPRIA (MEN-07) e o desfecho da
coleta por voz/teclado (VOZ-05 fatia 5a).

MEN-07: as interrupções de @mention iam para `menu:result`, a fila da resposta do cliente, e o
engine as reconhecia por `JSON.parse` do texto — o cliente as forjava digitando. Agora o bridge as
escreve em `menu:signal`, e nada que venha do cliente vai para lá.

O desfecho `timeout`/`invalid` que o canal manda num `menu_result` com `outcome` também é SINAL:
vai ao menu que espera o CLIENTE, e a nenhum destino de mensagem (Console, stream, analytics).
"""
from __future__ import annotations

import json

import pytest

import plughub_orchestrator_bridge.main as bridge_mod

SID = "sess-men07"


class _Redis:
    def __init__(self, waiting: dict | None = None, customer_pid: str | None = None, human: bool = False):
        self.waiting = waiting or {}
        self.customer_pid = customer_pid
        self.human = human
        self.lpushes: list[tuple[str, str]] = []
        self.publishes: list = []
        self.xadds: list = []

    async def hgetall(self, key):
        return dict(self.waiting) if key == f"menu:waiting:{SID}" else {}

    async def get(self, key):
        if key.endswith(":customer_participant_id"):
            return self.customer_pid
        if key.endswith(":human_agent"):
            return "1" if self.human else None
        return None

    async def lpush(self, key, value):
        self.lpushes.append((key, value))

    async def publish(self, channel, data):
        self.publishes.append((channel, data))

    async def xadd(self, key, fields, **kw):
        self.xadds.append((key, fields))

    async def expire(self, key, ttl):
        pass

    async def xinfo_groups(self, key):
        return []


class _Producer:
    def __init__(self):
        self.sent = []

    async def send_and_wait(self, topic, value, key=None):
        self.sent.append((topic, json.loads(value)))


@pytest.fixture
def producer(monkeypatch):
    p = _Producer()
    monkeypatch.setattr(bridge_mod, "_kafka_producer", p)

    async def _sem_receive(**kw):
        return 0
    monkeypatch.setattr(bridge_mod, "_route_to_receive_waiting", _sem_receive)
    return p


def _meta(vis="all", standby=False):
    return json.dumps({"visibility": vis, "masked": False, "masked_fields": [], "standby": standby})


def _menu_result(payload: dict) -> dict:
    return {"session_id": SID, "contact_id": "c-1", "author": {"type": "customer"},
            "content": {"type": "menu_result", "payload": payload}}


class TestMentionNaFilaDeSinal:
    @pytest.mark.parametrize("acao,esperado", [
        ({"trigger_step": "analisar"}, {"_mention_trigger_step": "analisar"}),
        ({"terminate_self": True}, {"_mention_terminate": True}),
    ])
    async def test_interrupcao_vai_para_menu_signal_e_nunca_para_menu_result(self, acao, esperado):
        r = _Redis()
        await bridge_mod.dispatch_mention_command(r, SID, "t", "cmd", {"action": acao}, instance_id="copilot-001")
        assert r.lpushes == [(f"menu:signal:{SID}:copilot-001", json.dumps(esperado))]

    def test_chave_espelha_o_engine(self):
        assert bridge_mod.menu_signal_key(SID, "i-1") == f"menu:signal:{SID}:i-1"
        assert bridge_mod.menu_signal_key(SID) == f"menu:signal:{SID}"


class TestDesfechoDaColeta:
    async def test_timeout_vai_so_aos_menus_do_cliente_como_sinal(self, producer):
        r = _Redis(waiting={
            "ia-001": _meta("all"),
            "nps-002": _meta(["cust_x"]),
            "interno-003": _meta("agents_only"),
            "copilot-004": _meta("all", standby=True),
            "_default_": _meta("all"),
        }, customer_pid="cust_x")
        await bridge_mod.process_inbound(_menu_result({"menu_id": "m1", "outcome": "timeout"}), r)
        sinal = json.dumps({"_collect_outcome": "timeout"})
        assert sorted(r.lpushes) == sorted([
            (f"menu:signal:{SID}:ia-001", sinal),
            (f"menu:signal:{SID}:nps-002", sinal),
            (f"menu:signal:{SID}", sinal),
        ])
        # não é mensagem: nada ao Console, ao stream, ao analytics, nem à fila de resposta
        assert r.publishes == [] and r.xadds == [] and producer.sent == []
        assert not any(k.startswith("menu:result") for k, _ in r.lpushes)

    async def test_desfecho_desconhecido_nao_entrega_nada(self, producer, caplog):
        r = _Redis(waiting={"ia-001": _meta("all")})
        with caplog.at_level("WARNING"):
            await bridge_mod.process_inbound(_menu_result({"menu_id": "m1", "outcome": "talvez"}), r)
        assert r.lpushes == [] and "desconhecido" in caplog.text

    async def test_sem_menu_esperando_e_dito(self, producer, caplog):
        r = _Redis()
        with caplog.at_level("WARNING"):
            await bridge_mod.process_inbound(_menu_result({"menu_id": "m1", "outcome": "invalid"}), r)
        assert r.lpushes == [] and "SEM menu do cliente esperando" in caplog.text

    async def test_controle_menu_result_com_valor_vai_a_fila_de_resposta(self, producer):
        r = _Redis(waiting={"ia-001": _meta("all")})
        await bridge_mod.process_inbound(_menu_result({"menu_id": "m1", "result": "fatura"}), r)
        assert (f"menu:result:{SID}:ia-001", "fatura") in r.lpushes
        assert not any(k.startswith("menu:signal") for k, _ in r.lpushes)

    async def test_controle_texto_do_cliente_com_forma_de_sinal_fica_na_fila_de_resposta(self, producer):
        # o bridge não interpreta o texto; quem o trataria como sinal era o engine, que agora só
        # interpreta a fila de sinal (menu.test.ts § MEN-07)
        forjado = json.dumps({"_mention_trigger_step": "liberar"})
        r = _Redis(waiting={"ia-001": _meta("all")})
        msg = {"session_id": SID, "contact_id": "c-1", "author": {"type": "customer"},
               "content": {"type": "text", "text": forjado}}
        await bridge_mod.process_inbound(msg, r)
        assert (f"menu:result:{SID}:ia-001", forjado) in r.lpushes
        assert not any(k.startswith("menu:signal") for k, _ in r.lpushes)
