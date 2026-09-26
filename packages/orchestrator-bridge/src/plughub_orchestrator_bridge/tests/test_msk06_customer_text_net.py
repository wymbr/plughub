"""
test_msk06_customer_text_net.py — MSK-06: a fala do CLIENTE passa pela rede de texto livre nos
destinos de pessoa e de armazenamento, e NUNCA no do fluxo.

Medido em 2026-09-25 num contato real (`demo_ia`, portabilidade pelo webchat): o telefone e o
e-mail que o cliente digitou ficaram EM CLARO no stream canônico, de onde o Console e o
ClickHouse leem — o bridge só redigia campo DECLARADO mascarado.

Decisão do dono: máscara pelo `by_role` do `operator` nos três destinos, com o original no
`original_content` do stream (layout do `message_send`, servido só a `authorized_roles`).

Proposições, cada uma com o seu controle:
  1. o FLUXO recebe o valor cru — sem isto a máscara quebraria o atendimento;
  2. stream, ClickHouse e Console recebem a exibição do catálogo, e o valor cru não sobra;
  3. o original vai ao `original_content` — e SÓ quando houve detecção (texto limpo mantém o
     layout de antes, sem `masked`);
  4. catálogo indisponível ESCONDE (`***`), nunca revela.
"""
from __future__ import annotations

import json

import pytest

import plughub_orchestrator_bridge.main as bridge_mod

SID = "sess-msk06"
FONE = "(11) 98765-4321"   # `55 ` sem `+` fica fora do padrão (o mesmo do TS): sairia `55 ***4321`


def _cat(**por_id: str) -> dict:
    return {i: {"id": i, "mascara": {"by_role": {"operator": m}}} for i, m in por_id.items()}


SEMEADO = _cat(cpf="last_2", credit_card="last_4", phone="last_4", email_addr="email_domain")


def _texto(text: str) -> dict:
    return {"session_id": SID, "contact_id": "c-1", "message_id": "m-1",
            "timestamp": "2026-09-25T18:10:17Z", "channel": "webchat",
            "author": {"type": "customer", "id": "c-1"},
            "content": {"type": "text", "text": text}}


class _Redis:
    def __init__(self, *, human: bool = False):
        self.human = human
        self.xadds: list[tuple[str, dict]] = []
        self.publishes: list[tuple[str, str]] = []
        self.lpushes: list[tuple[str, str]] = []

    async def get(self, key):
        return "1" if (self.human and key.endswith(":human_agent")) else None

    async def hgetall(self, key):
        if key.startswith("menu:waiting:"):
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
def ambiente(monkeypatch):
    p = _Producer()
    monkeypatch.setattr(bridge_mod, "_kafka_producer", p)

    async def _sem_receive(**kw):
        return 0
    monkeypatch.setattr(bridge_mod, "_route_to_receive_waiting", _sem_receive)

    async def _tenant(*a, **kw):
        return "tenant_test"
    monkeypatch.setattr(bridge_mod, "resolve_session_tenant", _tenant)

    estado = {"catalogo": SEMEADO, "pedidos": []}

    async def _catalogo(tenant_id, *a, **kw):
        estado["pedidos"].append(tenant_id)
        return estado["catalogo"]
    monkeypatch.setattr(bridge_mod, "get_masking_catalog", _catalogo)
    return p, estado


def _payloads(r: _Redis) -> list[dict]:
    return [json.loads(f["payload"]) for _, f in r.xadds]


def _analytics(p: _Producer) -> list[str]:
    return [ev["content"] for t, ev in p.sent
            if t == "conversations.events" and ev.get("event_type") == "message_sent"]


class TestSessaoDeIA:
    async def test_o_fluxo_recebe_o_valor_cru(self, ambiente):
        r = _Redis()
        await bridge_mod.process_inbound(_texto(FONE), r)
        assert r.lpushes == [(f"menu:result:{SID}", FONE)]

    async def test_stream_e_clickhouse_recebem_a_exibicao_do_catalogo(self, ambiente):
        p, estado = ambiente
        r = _Redis()
        await bridge_mod.process_inbound(_texto(f"meu numero e {FONE}"), r)
        [pl] = _payloads(r)
        assert pl["content"]["text"] == "meu numero e ***4321"
        assert "98765" not in json.dumps(pl["content"]) and "98765" not in pl["text"]
        assert _analytics(p) == ["meu numero e ***4321"]
        assert estado["pedidos"] == ["tenant_test"]      # catálogo DO tenant da sessão

    async def test_o_original_vai_ao_original_content(self, ambiente):
        r = _Redis()
        await bridge_mod.process_inbound(_texto(f"meu numero e {FONE}"), r)
        [pl] = _payloads(r)
        assert pl["original_content"] == {"type": "text", "text": f"meu numero e {FONE}"}
        assert pl["masked"] is True and pl["masked_categories"] == ["phone"]

    async def test_controle_texto_limpo_mantem_o_layout(self, ambiente):
        """Sem este, uma rede que marcasse tudo passaria nos de cima."""
        p, _ = ambiente
        r = _Redis()
        await bridge_mod.process_inbound(_texto("quero trocar de plano"), r)
        [pl] = _payloads(r)
        assert pl["content"]["text"] == "quero trocar de plano"
        assert "original_content" not in pl and "masked" not in pl
        assert _analytics(p) == ["quero trocar de plano"]

    async def test_trocar_o_by_role_troca_a_exibicao(self, ambiente):
        _, estado = ambiente
        estado["catalogo"] = _cat(phone="full")
        r = _Redis()
        await bridge_mod.process_inbound(_texto(FONE), r)
        assert _payloads(r)[0]["content"]["text"] == "***"

    async def test_catalogo_indisponivel_esconde(self, ambiente):
        _, estado = ambiente
        estado["catalogo"] = {}
        r = _Redis()
        await bridge_mod.process_inbound(_texto("email joao@exemplo.com"), r)
        assert _payloads(r)[0]["content"]["text"] == "email ***"


class TestSessaoComHumano:
    async def test_console_stream_e_clickhouse_mascarados_e_o_fluxo_cru(self, ambiente):
        p, _ = ambiente
        r = _Redis(human=True)
        await bridge_mod.process_inbound(_texto(f"cpf 529.982.247-25"), r)
        # Console
        consoles = [json.loads(d) for c, d in r.publishes if c == f"agent:events:{SID}"]
        assert [e["text"] for e in consoles] == ["cpf ***25"]
        # stream (ramo humano) com o original à parte
        [pl] = _payloads(r)
        assert pl["content"]["text"] == "cpf ***25"
        assert pl["original_content"]["text"] == "cpf 529.982.247-25"
        # ClickHouse
        assert _analytics(p) == ["cpf ***25"]
        # e o fluxo que esperava a resposta recebe o valor
        assert r.lpushes == [(f"menu:result:{SID}", "cpf 529.982.247-25")]


class TestLayoutDoStream:
    def test_sem_categorias_nao_ha_campos_novos(self):
        f = bridge_mod.customer_message_stream_fields(
            event_id="e", timestamp="t", author_id="c", text="oi", visibility="all",
            original_text="oi", masked_categories=[])
        assert set(json.loads(f["payload"])) == {"message_id", "content", "text"}

    def test_rede_texto_cliente_devolve_none_sem_deteccao(self):
        assert bridge_mod.rede_texto_cliente("bom dia", SEMEADO) == ("bom dia", None, [])
