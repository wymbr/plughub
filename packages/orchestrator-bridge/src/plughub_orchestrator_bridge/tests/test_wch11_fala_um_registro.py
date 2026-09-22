"""
test_wch11_fala_um_registro.py — WCH-11: a fala do cliente tem UM registro, e o rótulo diz o
que a resposta foi.

Medido na sessão `e606ef68` (2026-09-22): um menu de TEXTO LIVRE respondido por voz produzia
DUAS linhas na sessão — a fala (`audio_transcript`, registro do canal) e o desfecho da coleta
como mensagem do cliente, decorado `[Seleção: eu precisava falar com o atendente.]`. Ninguém
selecionou nada, e o cliente falou uma vez só.

Duas proposições, cada uma com o seu controle:

  1. **registro** — resposta por VOZ não vira segunda mensagem (stream, analytics, Console),
     mas CONTINUA sendo entregue ao menu (LPUSH). Entregar é transporte; registrar é história.
     Controle: a mesma resposta por TECLA (`dtmf`) e pela TELA (sem `via`) continua registrada —
     nelas o `menu_result` é o único registro que existe.
  2. **rótulo** — a decoração passa a olhar a INTERAÇÃO do menu, não o tipo da mensagem.
     Controle: interação ausente mantém `[Seleção: …]`, porque não se adivinha texto livre a
     partir da falta de informação.
"""
from __future__ import annotations

import json

import pytest

import plughub_orchestrator_bridge.main as bridge_mod

SID = "sess-wch11"


def _menu_result(*, interaction: str, result="eu precisava falar com o atendente.",
                 via: str | None = None, falado: bool = False) -> dict:
    payload: dict = {"menu_id": "m-1", "interaction": interaction, "result": result}
    if via is not None:
        payload["via"] = via
    msg = {"session_id": SID, "contact_id": "c-1", "message_id": "m-1",
           "timestamp": "2026-09-22T18:10:17Z", "channel": "webchat",
           "author": {"type": "customer", "id": "c-1"},
           "content": {"type": "menu_result", "payload": payload}}
    if falado:
        msg["content_type"] = "audio_transcript"
    return msg


class _Redis:
    def __init__(self, *, human: bool = False, waiting: bool = True):
        self.human = human
        self.waiting = waiting
        self.xadds: list[tuple[str, dict]] = []
        self.publishes: list[tuple[str, str]] = []
        self.lpushes: list[tuple[str, str]] = []

    async def get(self, key):
        return "1" if (self.human and key.endswith(":human_agent")) else None

    async def hgetall(self, key):
        if self.waiting and key.startswith("menu:waiting:"):
            return {"_default_": json.dumps({"visibility": "all"})}
        return {}

    async def xinfo_groups(self, key):
        return []

    async def xadd(self, key, fields, **kw):
        self.xadds.append((key, fields))

    async def expire(self, key, ttl):
        pass

    async def publish(self, channel, data):
        self.publishes.append((channel, data))

    async def lpush(self, key, value):
        self.lpushes.append((key, value))


class _Producer:
    def __init__(self):
        self.sent: list[tuple[str, dict]] = []

    async def send_and_wait(self, topic, value, key=None):
        self.sent.append((topic, json.loads(value)))


@pytest.fixture
def producer(monkeypatch):
    p = _Producer()
    monkeypatch.setattr(bridge_mod, "_kafka_producer", p)

    async def _sem_receive(**kw):
        return 0
    monkeypatch.setattr(bridge_mod, "_route_to_receive_waiting", _sem_receive)

    async def _tenant(*a, **kw):
        return "tenant_test"
    monkeypatch.setattr(bridge_mod, "resolve_session_tenant", _tenant)
    return p


def _textos_no_stream(r: _Redis) -> list[str]:
    return [json.loads(f["payload"])["content"]["text"] for _, f in r.xadds]


def _mensagens_no_analytics(p: _Producer) -> list[str]:
    return [ev["content"] for t, ev in p.sent
            if t == "conversations.events" and ev.get("event_type") == "message_sent"]


class TestUmRegistro:
    async def test_voz_entrega_ao_menu_mas_nao_registra_de_novo(self, producer):
        r = _Redis()
        await bridge_mod.process_inbound(_menu_result(interaction="text", via="voice"), r)
        # entregue: o fluxo recebe o valor cru, como sempre
        assert r.lpushes == [(f"menu:result:{SID}", "eu precisava falar com o atendente.")]
        # não registrado de novo: a fala já está na sessão
        assert _textos_no_stream(r) == []
        assert _mensagens_no_analytics(producer) == []

    async def test_controle_tecla_continua_registrando(self, producer):
        """Sem este, um bridge que nunca registrasse nada passaria no teste acima."""
        r = _Redis()
        await bridge_mod.process_inbound(_menu_result(interaction="button", result="2", via="dtmf"), r)
        assert r.lpushes == [(f"menu:result:{SID}", "2")]
        assert _textos_no_stream(r) == ["[Seleção: 2]"]
        assert _mensagens_no_analytics(producer) == ["2"]

    async def test_controle_tela_sem_via_continua_registrando(self, producer):
        r = _Redis()
        await bridge_mod.process_inbound(_menu_result(interaction="list", result="info_plano"), r)
        assert _textos_no_stream(r) == ["[Seleção: info_plano]"]
        assert _mensagens_no_analytics(producer) == ["info_plano"]

    async def test_com_humano_a_resposta_falada_nao_volta_ao_console(self, producer):
        r = _Redis(human=True)
        await bridge_mod.process_inbound(_menu_result(interaction="text", via="voice"), r)
        assert r.publishes == [] and _textos_no_stream(r) == []

    async def test_controle_com_humano_a_resposta_pela_tela_vai_ao_console(self, producer):
        r = _Redis(human=True)
        await bridge_mod.process_inbound(_menu_result(interaction="list", result="info_plano"), r)
        assert [c for c, _ in r.publishes] == [f"agent:events:{SID}"]
        assert _textos_no_stream(r) == ["[Seleção: info_plano]"]


class TestRotulo:
    def _decora(self, **kw) -> str:
        texto = kw.pop("texto", "quero falar com um atendente")
        kw.setdefault("any_masked", False)
        return bridge_mod.redact_customer_reply(texto, msg_type="menu_result", **kw)[0]

    def test_texto_livre_nao_e_selecao(self):
        assert self._decora(interaction="text") == "quero falar com um atendente"

    def test_opcao_continua_selecao(self):
        assert self._decora(texto="info_plano", interaction="list") == "[Seleção: info_plano]"
        assert self._decora(texto="2", interaction="button") == "[Seleção: 2]"

    def test_formulario_tem_rotulo_proprio(self):
        assert self._decora(texto='{"cpf": "1"}', interaction="form") == '[Formulário: {"cpf": "1"}]'

    def test_interacao_ausente_mantem_o_rotulo_de_antes(self):
        """Não se adivinha texto livre a partir da falta de informação."""
        assert self._decora(texto="info_plano") == "[Seleção: info_plano]"

    def test_mensagem_de_texto_nunca_e_decorada(self):
        assert bridge_mod.redact_customer_reply(
            "oi", msg_type="text", any_masked=False, interaction=None)[0] == "oi"

    def test_mascarado_vence_o_rotulo(self):
        """Precedência inalterada: mascaramento decide antes da decoração."""
        assert self._decora(interaction="text", any_masked=True) == bridge_mod._MASKED_SUPPRESSED
