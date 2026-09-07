# -*- coding: utf-8 -*-
"""GAT-03 — o escopo da ESCRITA de config vem do CORPO, e a QUERY é RECUSADA.

── O que foi MEDIDO antes de existir a recusa (2026-09-07) ─────────────────────

`GET` e `DELETE` de `/config/{ns}/{key}` leem `?tenant_id=`; o `PUT` **nunca leu** — o
escopo dele vem do corpo. Três verbos no mesmo caminho, e um discordando de onde mora o
escopo. Um `PUT .../probe?tenant_id=tenant_demo` voltava **200** e a linha pousava no
`__global__` da plataforma: o parâmetro era descartado, `body.tenant_id` ficava `None`, e
`None` significa escopo global. A resposta dizia a verdade (`"tenant_id": "__global__"`),
e ninguém lê corpo de resposta de escrita.

O dano não é hipotético: foi assim que `masking.context_map` — o mapa da plataforma, 97
folhas no vivo — foi substituído por UMA durante a triagem da GAT-03, por um `PUT` que
pretendia escrever no tenant. O `_reject_tenant_context_map` existe exatamente para
impedir isso e **não disparou**, porque ele julga `body.tenant_id`. O portão não tinha
defeito: foi contornado por um escopo que o chamador achou que tinha declarado.

── Por que RECUSAR, e não passar a honrar ──────────────────────────────────────

Honrar faria do parâmetro um SEGUNDO escritor do mesmo fato, e *"qual escopo?"* voltaria
a ter duas casas — com a precedência decidindo em silêncio quando os dois viessem
preenchidos. O corpo é a casa: é o que a UI usa (`putConfig(ns, key, value, tenantId)`).

── Por que estes casos, e não outros ───────────────────────────────────────────

T1 sozinho passaria num serviço que recusasse TODO `PUT`. T2 e T3 são as duas escritas
LEGÍTIMAS — tenant e global — e são elas que provam que a recusa é do parâmetro, não da
rota; sem T3 um serviço que passasse a exigir tenant no corpo também passaria, e
quebraria o seed, que escreve `__global__` de propósito. T4 é a ORDEM: se a recusa viesse
depois do guard do mapa, o caso medido continuaria passando.
"""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from plughub_config_api.router import PutConfigBody, put_config


class _StoreFalso:
    def __init__(self) -> None:
        self.escritas: list[tuple] = []

    async def set(self, tenant_id, namespace, key, value, description=None):
        self.escritas.append((tenant_id, namespace, key, value))

    async def tenants_overriding(self, namespace, key):
        return []


class _EmissorFalso:
    def __init__(self) -> None:
        self.eventos: list[tuple] = []

    async def emit_config_changed(self, tenant_id, namespace, key, operation):
        self.eventos.append((tenant_id, namespace, key, operation))


def _request():
    store, emitter = _StoreFalso(), _EmissorFalso()
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        store=store, emitter=emitter)))
    return req, store


@pytest.mark.asyncio
async def test_t1_query_tenant_id_e_recusada_com_422() -> None:
    req, store = _request()
    with pytest.raises(HTTPException) as e:
        await put_config("routing", "k", PutConfigBody(value="v"), req,
                         tenant_id="tenant_demo")
    assert e.value.status_code == 422
    # A recusa NOMEIA o corpo como o lugar certo e diz onde a escrita pousava antes.
    # Um 422 mudo manda o autor adivinhar qual dos dois caminhos usar — que é como o
    # defeito nasceu.
    assert "corpo" in e.value.detail
    assert "__global__" in e.value.detail
    # E nada foi escrito: a recusa acontece ANTES do store.
    assert store.escritas == []


@pytest.mark.asyncio
async def test_t2_o_tenant_no_corpo_continua_escrevendo_no_tenant() -> None:
    req, store = _request()
    # `tenant_id=None` EXPLÍCITO, e a razão é artefato do CHAMADOR, não do produto:
    # chamando a função fora do FastAPI o valor default é o objeto `Query(None)` — não
    # `None` —, então o guard dispararia. Pela rota, o FastAPI resolve para `None`.
    r = await put_config("routing", "k", PutConfigBody(value="v", tenant_id="t1"), req,
                         tenant_id=None)
    assert r.status_code == 200
    assert store.escritas == [("t1", "routing", "k", "v")]


@pytest.mark.asyncio
async def test_t3_sem_tenant_nenhum_a_escrita_e_global_de_proposito() -> None:
    """Testemunha POSITIVA — a recusa é do PARÂMETRO, nunca da escrita global.

    O seed da plataforma escreve `__global__` omitindo o tenant. Se este caso
    reprovasse, o portão teria quebrado o provisionamento em vez de fechar um buraco.
    """
    req, store = _request()
    r = await put_config("routing", "k", PutConfigBody(value="v"), req, tenant_id=None)
    assert r.status_code == 200
    assert store.escritas == [(None, "routing", "k", "v")]


@pytest.mark.asyncio
async def test_t4_a_recusa_da_query_vem_ANTES_do_guard_do_mapa() -> None:
    """A ORDEM é o que torna o portão do `context_map` alcançável.

    Com a recusa depois de `_reject_tenant_context_map`, um
    `PUT masking/context_map?tenant_id=x` continuaria chegando ao guard com
    `body.tenant_id = None` — e continuaria passando, que é o caso medido em 2026-09-07.
    O sinal de que a ordem está certa: o detail é o da QUERY, não o do mapa.
    """
    req, store = _request()
    with pytest.raises(HTTPException) as e:
        await put_config("masking", "context_map",
                         PutConfigBody(value={"uma": "folha"}), req,
                         tenant_id="tenant_demo")
    assert e.value.status_code == 422
    assert "tenant_id" in e.value.detail
    assert "override por tenant" not in e.value.detail  # esse é o do guard do mapa
    assert store.escritas == []
