# -*- coding: utf-8 -*-
"""RET-03 — o token do chamador morre com o contato.

⚠️ O caso load-bearing NAO e "apaga o token da sessao que fechou" — e o CONTROLE
POSITIVO ao lado: **o token de OUTRA sessao sobrevive**. Uma implementacao que
limpasse o hash inteiro passaria no primeiro teste e derrubaria todo delegate
pendente do tenant, e o sintoma seria contatos alheios caindo no `on_timeout`
sem ninguem relacionar as duas coisas.
"""
import pytest

from plughub_channel_gateway.adapters.webhook import WebhookAdapter
from plughub_channel_gateway.outbound_consumer import OutboundConsumer


class RedisFake:
    def __init__(self, hashes=None, chaves=None):
        self.hashes = hashes or {}
        self.chaves = chaves or {}
        self.apagadas = []

    async def hgetall(self, k):
        return dict(self.hashes.get(k, {}))

    async def hdel(self, k, campo):
        self.hashes.get(k, {}).pop(campo, None)

    async def delete(self, k):
        self.apagadas.append(k)
        self.chaves.pop(k, None)

    async def exists(self, k):
        return 1 if k in self.chaves else 0


def _adapter(redis):
    a = WebhookAdapter.__new__(WebhookAdapter)
    a._redis = redis
    return a


HASH = "tenant_demo:resume_tokens"


@pytest.mark.asyncio
async def test_cancela_apenas_os_tokens_da_sessao_que_fechou():
    r = RedisFake({HASH: {
        "tok-a": "sess-1:delegar:2099-01-01T00:00:00+00:00",
        "tok-b": "sess-2:delegar:2099-01-01T00:00:00+00:00",
        "tok-c": "sess-1:outro:2099-01-01T00:00:00+00:00",
    }})
    n = await _adapter(r).cancel_pending_resumes("tenant_demo", "sess-1")

    assert n == 2
    # ── O CONTROLE POSITIVO: a sessao vizinha continua inteira ──
    assert set(r.hashes[HASH]) == {"tok-b"}
    # e o registro por token some junto, senao fica meta orfao sem dono
    assert "tenant_demo:resume_meta:tok-a" in r.apagadas
    assert "tenant_demo:resume_meta:tok-b" not in r.apagadas


@pytest.mark.asyncio
async def test_session_id_que_e_prefixo_de_outro_nao_e_atingido():
    # Comparar por PREFIXO de string apagaria `sess-10` ao fechar `sess-1`.
    r = RedisFake({HASH: {
        "tok-a": "sess-1:delegar:2099-01-01T00:00:00+00:00",
        "tok-b": "sess-10:delegar:2099-01-01T00:00:00+00:00",
    }})
    n = await _adapter(r).cancel_pending_resumes("tenant_demo", "sess-1")
    assert n == 1
    assert set(r.hashes[HASH]) == {"tok-b"}


@pytest.mark.asyncio
async def test_sem_token_da_sessao_nao_faz_nada():
    r = RedisFake({HASH: {"tok-b": "sess-2:delegar:2099-01-01T00:00:00+00:00"}})
    assert await _adapter(r).cancel_pending_resumes("tenant_demo", "sess-1") == 0
    assert set(r.hashes[HASH]) == {"tok-b"}


@pytest.mark.asyncio
async def test_falha_de_leitura_degrada_BARULHENTO_e_nao_estoura(caplog):
    class Quebrado(RedisFake):
        async def hgetall(self, k):
            raise RuntimeError("redis fora")

    n = await _adapter(Quebrado()).cancel_pending_resumes("tenant_demo", "sess-1")
    assert n == 0
    # Falhar aqui deixa o token vivo e o dano volta — o log tem de dizer isso,
    # senao o fechamento parece limpo.
    assert any("cancel_pending_resumes" in r.message or "cancel_pending_resumes" in r.getMessage()
               for r in caplog.records)


# ── O gancho: UM lugar, e antes das guardas de canal ────────────────────────

class AdapterEspiao:
    def __init__(self):
        self.chamado = []

    async def cancel_pending_resumes(self, tenant_id, session_id):
        self.chamado.append((tenant_id, session_id))
        return 1


class SettingsFake:
    tenant_id = "tenant_demo"


@pytest.mark.asyncio
async def test_o_gancho_dispara_no_session_closed():
    espiao = AdapterEspiao()
    c = OutboundConsumer(adapters={"webhook": espiao}, settings=SettingsFake())
    await c._dispatch({
        "type": "session.closed", "session_id": "sess-1",
        "tenant_id": "tenant_demo", "contact_id": "c1", "channel": "webchat",
    })
    assert espiao.chamado == [("tenant_demo", "sess-1")]


@pytest.mark.asyncio
async def test_cancela_MESMO_sem_adapter_para_o_canal():
    # ⚠️ Este e o motivo de o gancho vir ANTES das guardas: um fechamento em canal
    # sem adapter registrado ainda tem de limpar o token. Se dependesse do
    # `deliver_session_closed`, o buraco voltaria pela porta menos vigiada.
    espiao = AdapterEspiao()
    c = OutboundConsumer(adapters={"webhook": espiao}, settings=SettingsFake())
    await c._dispatch({
        "type": "session.closed", "session_id": "sess-9",
        "tenant_id": "tenant_demo", "contact_id": "c1", "channel": "canal_exotico",
    })
    assert espiao.chamado == [("tenant_demo", "sess-9")]


@pytest.mark.asyncio
async def test_outros_tipos_nao_disparam_o_cancelamento():
    espiao = AdapterEspiao()
    c = OutboundConsumer(adapters={"webhook": espiao}, settings=SettingsFake())
    await c._dispatch({
        "type": "message.text", "session_id": "sess-1",
        "tenant_id": "tenant_demo", "contact_id": "c1", "channel": "webchat",
    })
    assert espiao.chamado == []
