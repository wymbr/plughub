"""
test_route_credential_gate.py — as rotas que passaram a EXIGIR credencial em 2026-08-29.

POR QUE ESTE ARQUIVO EXISTE, e por que ele não é redundante com os 690 já verdes
================================================================================
Ao gatear as 18 rotas, a suíte inteira continuou verde — e continuaria verde se o
gate não existisse. Todo teste de rota deste pacote injeta um chamador AUTENTICADO
(`_override_pool_principal` / `dependency_overrides`), então nenhum deles pode
distinguir "a rota exige credencial" de "a rota não exige nada". É a família que o
CLAUDE.md nomeia: *"um teste que não pode reprovar é pior que teste nenhum"*.

O que este arquivo acrescenta é o **controle negativo**: a mesma rota, SEM override,
tem de responder 401. E ao lado dele o **controle positivo** — a mesma rota, COM
override, não pode responder 401 —, porque um 401 pode vir de qualquer coisa
(parâmetro faltando, router não incluído) e sem o par o teste passaria pelo motivo
errado.

A dimensão de DEPLOY (o que o serviço responde de verdade) é do
`infra/test/probe_route_credential_coverage.sh` § B; aqui é o contrato do código.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plughub_analytics_api.pool_auth import (
    PoolPrincipal,
    optional_pool_principal,
    require_pool_principal,
    sse_pool_principal,
)
from plughub_analytics_api.reports import router as reports_router
from plughub_analytics_api.sessions import router as sessions_router

# ── Rotas gateadas em 2026-08-29, com a query mínima para chegar ao handler ──────
#
# As doze de `reports.py` eram o achado registrado no TODO; as quatro de `sessions.py`
# saíram do censo (`_route_principal_census.py`) e NÃO estavam no achado — o recorte
# `/reports/*` era o do achado, não o do eixo. `/sessions/{id}/stream` é a mais grave
# das dezoito: servia a transcrição inteira do contato, medida ao vivo antes do
# conserto, enquanto a rota irmã `/v1/transcript/sessions/{id}` já exigia credencial.
ROTAS_REPORTS = [
    "/reports/usage?tenant_id=t",
    "/reports/workflows?tenant_id=t",
    "/reports/campaigns?tenant_id=t",
    "/reports/evaluations?tenant_id=t",
    "/reports/evaluations/summary?tenant_id=t",
    "/reports/evaluations/quality?tenant_id=t",
    "/reports/customer-voice/instruments",
    "/reports/customers/cus_x/360?tenant_id=t",
    "/reports/agent-events/series?tenant_id=t",
    "/reports/agent-events/summary?tenant_id=t",
    "/reports/agent-events/categories?tenant_id=t",
    "/reports/evaluator-calibration?tenant_id=t",
]

ROTAS_SESSIONS = [
    "/sessions/active?tenant_id=t&pool_id=p",
    "/sessions/s1/stream?tenant_id=t",
    "/sessions/s1/workflow-trace?tenant_id=t",
    "/sessions/s1/pipeline-state?tenant_id=t",
]


def _app(router, *, autenticado: bool, accessible=None) -> FastAPI:
    """App mínimo. `autenticado=False` = anônimo de verdade: nenhum override, e as
    settings dizem que HÁ segredo e o bypass de demo está DESLIGADO — que é a
    configuração de produção e a do demo desde 2026-08-27."""
    app = FastAPI()
    app.include_router(router)
    if autenticado:
        fake = lambda: PoolPrincipal(
            accessible_pools=accessible, tenant_id="t", sub="test",
        )
        app.dependency_overrides[optional_pool_principal] = fake
        app.dependency_overrides[sse_pool_principal] = fake
        app.dependency_overrides[require_pool_principal] = fake

    # Store/Redis mockados: o alvo é o CÓDIGO de status, nunca o corpo. Sem isto um
    # 500 do ClickHouse ausente mascararia o 200 que o controle positivo procura.
    store = MagicMock()
    store._database = "analytics"
    store._client = MagicMock()
    store._client.query.return_value = MagicMock(result_rows=[], column_names=[])
    store.new_client.return_value = store._client
    store.query_session_messages = MagicMock(return_value=[])
    redis = AsyncMock()
    redis.xrange = AsyncMock(return_value=[])
    redis.get = AsyncMock(return_value=None)
    redis.smembers = AsyncMock(return_value=set())
    # O `/stream` faz live-tail em laço infinito; `CancelledError` no `xread` é como
    # os testes de stream já o encerram. Sem isto o controle POSITIVO pendura — foi
    # o que aconteceu na primeira execução deste arquivo.
    redis.xread = AsyncMock(side_effect=asyncio.CancelledError())
    app.state.store = store
    app.state.redis = redis
    return app


def _settings_producao():
    """`auth_jwt_secret` presente + `analytics_open_access` desligado."""
    m = patch("plughub_analytics_api.pool_auth.get_settings")
    fake = m.start()
    fake.return_value.auth_jwt_secret = "s" * 32
    fake.return_value.analytics_open_access = False
    return m


# ══════════════════════════════════════════════════════════════════════════════
# Controle NEGATIVO — sem credencial, a rota recusa
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("rota", ROTAS_REPORTS + ROTAS_SESSIONS)
def test_anonimo_recebe_401(rota):
    router = reports_router if rota.startswith("/reports") else sessions_router
    m = _settings_producao()
    try:
        app = _app(router, autenticado=False)
        with TestClient(app) as client:
            r = client.get(rota)
    finally:
        m.stop()
    assert r.status_code == 401, (
        f"{rota} respondeu {r.status_code} sem credencial. Antes de 2026-08-29 estas "
        f"rotas respondiam 200 anônimo, e dois censos de autorização não as viam "
        f"porque contam QUEM DECIDE, e uma rota sem dependência não tem decisor."
    )


# ══════════════════════════════════════════════════════════════════════════════
# Controle POSITIVO — com credencial, o 401 some
# ══════════════════════════════════════════════════════════════════════════════
#
# Sem este par, o teste acima passaria por qualquer 401 — inclusive um vindo de
# parâmetro faltando ou de router mal montado.

@pytest.mark.parametrize("rota", ROTAS_REPORTS + ROTAS_SESSIONS)
def test_autenticado_nao_recebe_401(rota):
    router = reports_router if rota.startswith("/reports") else sessions_router
    app = _app(router, autenticado=True)
    with TestClient(app) as client:
        r = client.get(rota)
    assert r.status_code != 401, f"{rota} recusou um chamador autenticado"


# ══════════════════════════════════════════════════════════════════════════════
# `/sessions/active` — o único dos dezoito que RECORTA, e por quê
# ══════════════════════════════════════════════════════════════════════════════
#
# Aqui o escopo é membership (o chamador NOMEIA o pool), não predicado de coluna:
# "posso ver este pool?" é um teste de pertinência à lista do token. Nas doze de
# `reports.py` seria preciso inventar qual coluna é o pool da agregação — e recorte
# inventado esvazia em vez de filtrar (F2 do ADR de relatórios).

def test_pool_fora_do_escopo_recebe_403():
    app = _app(sessions_router, autenticado=True, accessible=["pool_a"])
    with TestClient(app) as client:
        r = client.get("/sessions/active?tenant_id=t&pool_id=pool_b")
    assert r.status_code == 403
    assert r.json()["detail"] == "pool_scope_denied"


def test_pool_dentro_do_escopo_passa():
    """Controle positivo do recorte: sem ele, um 403 constante passaria no teste
    acima e ninguém veria que a rota parou de servir QUALQUER pool."""
    app = _app(sessions_router, autenticado=True, accessible=["pool_a"])
    with TestClient(app) as client:
        r = client.get("/sessions/active?tenant_id=t&pool_id=pool_a")
    assert r.status_code == 200


def test_irrestrito_alcanca_qualquer_pool():
    app = _app(sessions_router, autenticado=True, accessible=None)
    with TestClient(app) as client:
        r = client.get("/sessions/active?tenant_id=t&pool_id=qualquer")
    assert r.status_code == 200


# ══════════════════════════════════════════════════════════════════════════════
# `sse_pool_principal` — a afirmação de TRANSPORTE
# ══════════════════════════════════════════════════════════════════════════════
#
# `EventSource` não manda cabeçalho. Se o `?token=` não fosse aceito, o gate do
# `/stream` teria trocado um vazamento por uma tela morta — e o sintoma seria
# AUSÊNCIA (o Console sem mensagens), não erro.

@pytest.mark.asyncio
async def test_sse_aceita_token_na_query():
    import jwt as _jwt
    tok = _jwt.encode(
        {"sub": "u", "tenant_id": "t", "accessible_pools": ["pool_a"]},
        "s" * 32, algorithm="HS256",
    )
    request = MagicMock()
    request.query_params = {"token": tok}
    request.headers = {}
    m = _settings_producao()
    try:
        p = await sse_pool_principal(request=request, token=tok)
    finally:
        m.stop()
    assert p.sub == "u"
    assert "pool_a" in p.accessible_pools


@pytest.mark.asyncio
async def test_sse_aceita_token_no_cabecalho():
    """A outra origem: quem chama por `fetch` (não por `EventSource`) manda header.
    As duas têm de dar o MESMO principal — um endpoint que autoriza por uma origem e
    escopa por outra é o defeito que `raw_bearer_from_request` existe para evitar."""
    import jwt as _jwt
    tok = _jwt.encode(
        {"sub": "u", "tenant_id": "t", "accessible_pools": ["pool_a"]},
        "s" * 32, algorithm="HS256",
    )
    request = MagicMock()
    request.query_params = {}
    request.headers = {"Authorization": f"Bearer {tok}"}
    m = _settings_producao()
    try:
        p = await sse_pool_principal(request=request, token=None)
    finally:
        m.stop()
    assert p.sub == "u"
    assert "pool_a" in p.accessible_pools


# ── E a mesma afirmação NA ROTA, não só na dependência ──────────────────────────
#
# ⚠️ Os três testes acima exercitam `sse_pool_principal` isolada, e isso NÃO prova que
# `/sessions/{id}/stream` a use. Medido por mutação ao escrever este arquivo: trocar
# `Depends(sse_pool_principal)` por `Depends(optional_pool_principal)` na rota deixava
# os 38 testes VERDES — e em produção mataria o Console, porque `EventSource` não manda
# cabeçalho e o `?token=` deixaria de ser lido. O sintoma seria AUSÊNCIA (stream vazio),
# não erro. Os dois testes abaixo são os que fecham a distância entre "a dependência
# funciona" e "a rota a usa"; por isso montam o app SEM `dependency_overrides`.

def _jwt_de_teste() -> str:
    # ⚠️ O escopo era `[]` (AUT-27, corrigido em 2026-08-31). Enquanto `[]` significava
    # "todos os pools", o recorte de conteudo passava direto e a rota devolvia 200. Com a
    # inversao da AUT-03, `[]` virou NENHUM pool e estes dois testes passaram a falhar
    # com 403 — por ESCOPO, nao por credencial.
    #
    # O defeito era do INSTRUMENTO, e da familia mais sutil: o teste continuava honesto e
    # ramificado, mas media uma proposicao ADJACENTE a que da nome a ele. Um relatorio
    # fiel ao vermelho teria publicado "o `?token=` parou de ser aceito", que e falso.
    import jwt as _jwt
    return _jwt.encode(
        {"sub": "u", "tenant_id": "t", "accessible_pools": ["sac"]},
        "s" * 32, algorithm="HS256",
    )


# A PROPOSICAO destes dois testes e "a rota LE a credencial nesta origem", nunca "o
# chamador ve o conteudo". Quem responde a segunda e o recorte de sessao, que tem testes
# proprios — e aqui ele recusa de forma legitima, porque a sessao `s1` nao existe.
#
# Por isso o veredicto e sobre o 401: ele, e so ele, significa "a credencial nao foi
# lida". Um 403 prova o oposto — a requisicao ATRAVESSOU a autenticacao e morreu depois.
# O limite de 5xx existe para o teste nao passar por acidente quando a rota explode.
def _credencial_foi_lida(r) -> None:
    assert r.status_code != 401, (
        "a credencial nao foi aceita pela ROTA — `EventSource` nao tem outra origem "
        "alem do `?token=`, entao o Console ficaria sem stream, em silencio."
    )
    assert r.status_code < 500, f"a rota explodiu ({r.status_code}), o teste nao julga nada"


def test_rota_stream_aceita_token_na_query():
    tok = _jwt_de_teste()
    m = _settings_producao()
    try:
        app = _app(sessions_router, autenticado=False)
        with TestClient(app) as client:
            r = client.get(f"/sessions/s1/stream?tenant_id=t&token={tok}")
    finally:
        m.stop()
    _credencial_foi_lida(r)


def test_rota_stream_aceita_token_no_cabecalho():
    """A outra origem, na mesma rota: quem chama por `fetch` manda header."""
    tok = _jwt_de_teste()
    m = _settings_producao()
    try:
        app = _app(sessions_router, autenticado=False)
        with TestClient(app) as client:
            r = client.get(
                "/sessions/s1/stream?tenant_id=t",
                headers={"Authorization": f"Bearer {tok}"},
            )
    finally:
        m.stop()
    _credencial_foi_lida(r)


@pytest.mark.asyncio
async def test_sse_sem_token_em_nenhuma_origem_recusa():
    from fastapi import HTTPException
    request = MagicMock()
    request.query_params = {}
    request.headers = {}
    m = _settings_producao()
    try:
        with pytest.raises(HTTPException) as exc:
            await sse_pool_principal(request=request, token=None)
    finally:
        m.stop()
    assert exc.value.status_code == 401
    assert exc.value.detail == "auth_required"
