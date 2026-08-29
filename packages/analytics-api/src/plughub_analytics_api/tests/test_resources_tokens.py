"""
test_resources_tokens.py — a lente de token da **Superfície B** (F3 · D2).

POR QUE ESTE ARQUIVO EXISTE
===========================
Ao escrever `GET /reports/resources/tokens` eu esqueci de importar `_ch_fmt`,
`_default_from` e `_default_to` no `reports.py`. Os 730 testes do pacote continuaram
VERDES: nenhum deles atravessa aquela rota, e um `NameError` que só acontece na
primeira chamada real é invisível para uma suíte que nunca chama.

É a família que o CLAUDE.md nomeia — *"um teste que não pode reprovar é pior que teste
nenhum"* —, e a lição específica é mais estreita: **rota nova sem teste que a
ATRAVESSE não está coberta por nada**, por mais testes que o módulo tenha.

O que se afirma aqui, em ordem de importância:

  1. a rota RESPONDE (o erro de import acima teria reprovado);
  2. a população é o `usage_events` INTEIRO — sem `INNER JOIN` com `sessions`. É a
     decisão que separa esta lente da da superfície A, e foi medida antes de escrever:
     reusar o breakdown de lá publicaria 47% do consumo;
  3. as três ausências são contadas SEPARADAMENTE (pré-época · sem conta · sem
     cadastro), e nunca somadas — a regra do `usage_attribution`;
  4. a rota NÃO aceita filtro de pool. Um `?pool_id=` que fosse silenciosamente
     ignorado seria pior que um 422: devolveria o todo sob o rótulo de uma parte.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plughub_analytics_api.pool_auth import PoolPrincipal, optional_pool_principal
from plughub_analytics_api.reports import router as reports_router


def _app(linhas=None, aux=None) -> FastAPI:
    app = FastAPI()
    app.include_router(reports_router)
    app.dependency_overrides[optional_pool_principal] = lambda: PoolPrincipal(
        accessible_pools=None, tenant_id="t", sub="test",
    )

    principal = MagicMock()
    principal.column_names = [
        "account_config_id", "account_key_id", "model_id", "model_profile",
        "source", "tokens_in", "tokens_out", "sessions", "events",
    ]
    principal.result_rows = linhas if linhas is not None else []

    contadores = MagicMock()
    contadores.column_names = ["pre_epoch_events", "unidentified_events", "uncatalogued_events"]
    contadores.result_rows = [aux if aux is not None else (0, 0, 0)]

    client = MagicMock()
    client.query = MagicMock(side_effect=[principal, contadores])

    store = MagicMock()
    store._database = "analytics"
    store.new_client.return_value = client
    app.state.store = store
    app.state._client = client
    return app


def test_rota_responde():
    """Controle mínimo — e o que teria pego o `NameError` de import."""
    app = _app()
    with TestClient(app) as c:
        r = c.get("/reports/resources/tokens?tenant_id=t")
    assert r.status_code == 200, r.text
    assert r.json()["meta"]["population"] == "all_usage_events"


def test_nao_junta_com_sessions():
    """A afirmação central: a população NÃO passa por `sessions`.

    Asserção sobre o SQL EXECUTADO, não sobre o fonte — é o mesmo mecanismo de
    `test_sla_reads_the_segment.py`, e pela mesma razão: um `grep` no arquivo contaria
    o comentário que documenta a decisão.
    """
    app = _app()
    with TestClient(app) as c:
        c.get("/reports/resources/tokens?tenant_id=t")
    sql = app.state._client.query.call_args_list[0][0][0]
    assert "usage_events" in sql
    assert "JOIN" not in sql.upper(), (
        "a lente da OFERTA não pode juntar com a população de contatos: medido em "
        "2026-08-29, isso reduziria o consumo publicado de 1991 para 945 tokens"
    )
    assert "GROUP BY account_config_id, account_key_id" in sql


def test_corta_na_epoca_de_atribuicao():
    app = _app()
    with TestClient(app) as c:
        c.get("/reports/resources/tokens?tenant_id=t")
    sql = app.state._client.query.call_args_list[0][0][0]
    assert "2026-08-28" in sql, "sem o corte, o pré-época entra num balde indistinguível"


def test_as_tres_ausencias_sao_contadas_separadamente():
    """Somá-las esconde o defeito dentro da história — a regra do `usage_attribution`."""
    app = _app(aux=(8, 2, 12))
    with TestClient(app) as c:
        r = c.get("/reports/resources/tokens?tenant_id=t")
    meta = r.json()["meta"]
    assert meta["pre_epoch_events"] == 8
    assert meta["unidentified_events"] == 2
    assert meta["uncatalogued_events"] == 12


def test_pool_id_nao_e_parametro():
    """Filtro de pool aqui devolveria a soma de um subconjunto sob o rótulo do todo.

    Controle POSITIVO ao lado: a rota aceita `limit`. Sem ele, este teste passaria
    também numa rota que rejeita QUALQUER parâmetro — inclusive por estar quebrada.
    """
    app = _app()
    with TestClient(app) as c:
        assert c.get("/reports/resources/tokens?tenant_id=t&pool_id=x").status_code == 422
        assert c.get("/reports/resources/tokens?tenant_id=t&limit=5").status_code == 200


def test_falha_da_consulta_nao_vira_lista_vazia():
    """`error` nomeado + **503**: consulta que falhou e conta que não gastou nada
    seriam a mesma tela sem isso.

    O 503 é a convenção da casa (`_respond`: `error` ⇒ 503), e não uma escolha desta
    rota — a primeira versão deste teste esperava 200 por analogia com o corpo, e o
    código estava certo. Degradação é BARULHENTA aqui: um 200 com lista vazia é
    indistinguível de "não houve consumo", que é o resultado mais caro de publicar
    numa tela de custo.
    """
    app = _app()
    app.state._client.query = MagicMock(side_effect=RuntimeError("clickhouse caiu"))
    with TestClient(app) as c:
        r = c.get("/reports/resources/tokens?tenant_id=t")
    assert r.status_code == 503
    assert r.json()["error"] == "data_unavailable"
    assert r.json()["data"] == []


@pytest.mark.parametrize("campo", ["tokens_in", "tokens_out", "sessions", "events"])
def test_linha_carrega_as_grandezas(campo):
    app = _app(linhas=[("", "abc123", "claude-haiku-4-5", "fast", "sentiment", 464, 36, 2, 4)])
    with TestClient(app) as c:
        r = c.get("/reports/resources/tokens?tenant_id=t")
    assert campo in r.json()["data"][0]


def test_conta_sem_cadastro_mantem_a_chave():
    """`account_config_id` vazio com `account_key_id` presente NÃO é "desconhecida".

    Medido em 2026-08-29: é o caso de 100% dos eventos deste ambiente, que roda pela
    chave de env legada. O consumo É atribuível — à chave, não ao cadastro —, e apagar
    a distinção transformaria "falta cadastrar" em "não sabemos quem gastou".
    """
    app = _app(linhas=[("", "754d27f40eb21592", "claude-sonnet-4-6", "balanced", "reason", 230, 215, 2, 4)])
    with TestClient(app) as c:
        r = c.get("/reports/resources/tokens?tenant_id=t")
    linha = r.json()["data"][0]
    assert linha["account_config_id"] == ""
    assert linha["account_key_id"] == "754d27f40eb21592"
