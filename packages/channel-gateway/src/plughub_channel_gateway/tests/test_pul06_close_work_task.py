# -*- coding: utf-8 -*-
"""PUL-06 — a pendencia nao sobrevive ao contato.

Medido em 2026-09-11: fechar a sessao cancelava o token e DEIXAVA o item. Por
~25 h `/monitor/work-items` mostrava `5120fe90` e `4841c60d` como "Being filled
in" (trabalho morto afirmado como pendente) e o Close do supervisor respondia
404, porque ele encerra RETOMANDO pelo token que acabara de ser cancelado.

⚠️ O caso load-bearing NAO e "apaga o ledger". E o de FALHA DO ARBITRO: ali o
ledger tem de FICAR, porque o scanner de prazo reidrata o endereco a partir dele
(medido em 2026-09-12, refutando a suposicao de que o prazo nunca dispararia).
Apagar sem confirmacao deixaria o item dentro do routing e fora das duas telas,
sem prazo que o alcance.
"""
import pytest

from plughub_channel_gateway.adapters.webhook import WebhookAdapter


class RedisFake:
    def __init__(self, chaves=None, hashes=None):
        self.chaves   = chaves or {}
        self.hashes   = hashes or {}
        self.apagadas = []

    async def get(self, k):
        return self.chaves.get(k)

    async def delete(self, k):
        self.apagadas.append(k)
        self.chaves.pop(k, None)

    async def hgetall(self, k):
        return dict(self.hashes.get(k, {}))

    async def hdel(self, k, campo):
        self.hashes.get(k, {}).pop(campo, None)


LEDGER = (
    '{"pool_id": "retencao_humano-int", "queue_session_id": "sess-1", '
    '"resume_token": "tok-a", "step_id": "coletar", "assigned_to": "u-1"}'
)
CHAVE = "tenant_demo:work_task:sess-1"


def _adapter(redis, resultado_expire=None, registrar=None):
    if resultado_expire is None:
        resultado_expire = {"expired": True}
    a = WebhookAdapter.__new__(WebhookAdapter)
    a._redis = redis

    async def _expire(tenant_id, pool_id, session_id, reason):
        if registrar is not None:
            registrar.append({"tenant_id": tenant_id, "pool_id": pool_id,
                              "session_id": session_id, "reason": reason})
        return resultado_expire

    a._routing_work_task_expire = _expire
    return a


@pytest.mark.asyncio
async def test_encerra_o_item_e_apaga_o_ledger():
    chamadas = []
    r = RedisFake({CHAVE: LEDGER})
    ok = await _adapter(r, registrar=chamadas)._close_work_task_on_session_close(
        "tenant_demo", "sess-1")

    assert ok is True
    assert chamadas == [{"tenant_id": "tenant_demo", "pool_id": "retencao_humano-int",
                         "session_id": "sess-1", "reason": "acw_session_closed"}]
    assert CHAVE in r.apagadas


@pytest.mark.asyncio
async def test_a_causa_e_PROPRIA_nem_prazo_nem_supervisor():
    # Reusar `acw_expired` diria que o prazo venceu (nao venceu) e
    # `acw_supervisor_closed` diria que alguem decidiu encerrar (ninguem decidiu).
    chamadas = []
    await _adapter(RedisFake({CHAVE: LEDGER}), registrar=chamadas)._close_work_task_on_session_close(
        "tenant_demo", "sess-1")
    assert chamadas[0]["reason"] not in ("acw_expired", "acw_supervisor_closed", "task_done")


@pytest.mark.asyncio
async def test_ARBITRO_FALHOU_o_ledger_FICA(caplog):
    # O caso que decide: sem confirmacao o item continua visivel e o prazo o
    # encerra depois (o scanner reidrata o endereco DESTE ledger).
    r = RedisFake({CHAVE: LEDGER})
    ok = await _adapter(r, resultado_expire=False)._close_work_task_on_session_close(
        "tenant_demo", "sess-1")

    assert ok is False
    assert CHAVE not in r.apagadas
    assert r.chaves.get(CHAVE) == LEDGER
    assert any("PUL-06" in reg.getMessage() for reg in caplog.records)


@pytest.mark.asyncio
async def test_sessao_SEM_item_nao_chama_o_arbitro():
    chamadas = []
    r = RedisFake({})
    ok = await _adapter(r, registrar=chamadas)._close_work_task_on_session_close(
        "tenant_demo", "sess-1")
    assert ok is False
    assert chamadas == []
    assert r.apagadas == []


@pytest.mark.asyncio
async def test_usa_o_id_que_esta_NA_FILA_e_apaga_a_chave_do_LEDGER():
    # Em conferencia os dois coincidem; quando divergem, o ZSET conhece o
    # `queue_session_id` e o ledger e chaveado pela sessao que o resume resolve.
    chamadas = []
    chave = "tenant_demo:work_task:sess-pai"
    r = RedisFake({chave: LEDGER.replace('"queue_session_id": "sess-1"',
                                         '"queue_session_id": "sess-filho"')})
    await _adapter(r, registrar=chamadas)._close_work_task_on_session_close(
        "tenant_demo", "sess-pai")
    assert chamadas[0]["session_id"] == "sess-filho"
    assert chave in r.apagadas


@pytest.mark.asyncio
async def test_ledger_sem_pool_nao_encerra_e_LOGA(caplog):
    r = RedisFake({CHAVE: '{"queue_session_id": "sess-1", "step_id": "coletar"}'})
    chamadas = []
    ok = await _adapter(r, registrar=chamadas)._close_work_task_on_session_close(
        "tenant_demo", "sess-1")
    assert ok is False
    assert chamadas == []
    assert any("PUL-06" in reg.getMessage() for reg in caplog.records)


@pytest.mark.asyncio
async def test_o_gancho_roda_dentro_do_cancel_pending_resumes():
    # A casa e UMA: quem fecha o contato ja chama `cancel_pending_resumes`, e o
    # item tem de morrer no mesmo lugar — um gancho por adapter reabriria o buraco.
    chamadas = []
    r = RedisFake({CHAVE: LEDGER},
                  {"tenant_demo:resume_tokens": {"tok-a": "sess-1:coletar:2099-01-01T00:00:00+00:00"}})
    n = await _adapter(r, registrar=chamadas).cancel_pending_resumes("tenant_demo", "sess-1")

    assert n == 1                      # o token continua sendo cancelado
    assert chamadas and chamadas[0]["reason"] == "acw_session_closed"
    assert CHAVE in r.apagadas
