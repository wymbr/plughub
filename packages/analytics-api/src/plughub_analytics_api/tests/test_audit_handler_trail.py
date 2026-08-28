"""
test_audit_handler_trail.py — o HANDLER de `/v1/audit/*`: a recusa vira resposta E vira
linha de trilha.

POR QUE ESTE ARQUIVO NASCEU (uma mutação sobreviveu)
====================================================
`test_audit_gate.py` cobre o VEREDICTO (`_check_audit_access`) e é bom nisso. Mas a
bateria de mutação do passo 4 plantou `status_code=denied.status → status_code=403` no
handler e **23 de 23 continuaram verdes**: nenhum teste atravessava a rota. Ou seja, o
fio entre *"o portão decidiu 401"* e *"o cliente recebeu 401"* não era medido, e é
justamente onde mora o comportamento novo — código de recusa e gravação da trilha.

Um verificador com tabela-verdade perfeita e um handler que ignora a resposta dela é o
mesmo defeito de sempre, um andar acima.

O QUE ESTE ARQUIVO AFIRMA
=========================
  1. o `status` da recusa CHEGA ao cliente (401 ≠ 403 ≠ 503);
  2. **toda** recusa grava em `audit_access_log` — inclusive a sem credencial, que era
     a que NÃO gravava, porque o `401` vinha de `optional_pool_principal`, um `Depends`
     que corta antes do corpo do handler. O docstring do módulo e o banner da tela
     afirmavam que todo acesso fica registrado; para o acesso anônimo era falso, e é o
     acesso anônimo que uma trilha mais precisa registrar;
  3. a linha da recusa NOMEIA quem foi barrado quando há quem nomear;
  4. o caminho de sucesso continua gravando `result="ok"` com a contagem.

Sem I/O: o store é `MagicMock`, e é o `insert_audit_access_log` dele que testemunha.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plughub_analytics_api.audit import router as audit_router

SECRET = "segredo-de-teste-handler"
SESSION = "sess-abc"


def _token(module_config: dict, sub: str = "u1", tenant: str = "tenant_x") -> str:
    return jwt.encode(
        {
            "sub": sub,
            "tenant_id": tenant,
            "module_config": module_config,
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        },
        SECRET,
        algorithm="HS256",
    )


@pytest.fixture
def app_e_store(monkeypatch):
    from plughub_analytics_api import config as cfg

    class S:
        analytics_open_access = False
        auth_jwt_secret = SECRET

    cfg.get_settings.cache_clear()
    monkeypatch.setattr(cfg, "get_settings", lambda: S())

    app = FastAPI()
    app.include_router(audit_router)

    store = MagicMock()
    store._database = "plughub"
    store.new_client = MagicMock(return_value=MagicMock())
    store.insert_audit_access_log = AsyncMock()
    # A query real é substituída: o alvo aqui é o portão e a trilha, não o SQL.
    monkeypatch.setattr(
        "plughub_analytics_api.audit._fetch_audit_messages",
        lambda *a, **k: [{"stream_entry_id": "1"}, {"stream_entry_id": "2"}],
    )
    monkeypatch.setattr(
        "plughub_analytics_api.audit._fetch_mcp_calls",
        lambda *a, **k: [{"event_id": "e1"}],
    )
    app.state.store = store
    return TestClient(app, raise_server_exceptions=False), store


def _linhas(store) -> list[dict]:
    return [c.args[0] for c in store.insert_audit_access_log.call_args_list]


ROTAS = [
    ("/v1/audit/sessions/%s/messages" % SESSION, "audit.sessions.messages", "sessions"),
    ("/v1/audit/mcp-calls", "audit.mcp_calls", "mcp_calls"),
]


# ── 1+2. sem credencial: 401 E gravado ───────────────────────────────────────

@pytest.mark.parametrize("url,endpoint,_campo", ROTAS)
def test_sem_credencial_401_E_REGISTRADO(app_e_store, url, endpoint, _campo):
    """⚠️ O caso que a trilha perdia inteiro.

    O `401` era levantado por `optional_pool_principal` — uma dependência — e por isso
    acontecia ANTES do corpo do handler. Nenhuma linha era escrita, e a tela continuava
    prometendo que todo acesso fica registrado. Tabela vazia parecendo "ninguém
    acessou" é pior que tabela ausente.
    """
    client, store = app_e_store
    r = client.get(url)
    assert r.status_code == 401
    linhas = _linhas(store)
    assert len(linhas) == 1, "a recusa TEM de gravar"
    assert linhas[0]["result"] == "denied"
    assert linhas[0]["endpoint"] == endpoint
    assert linhas[0]["actor_kind"] == "anonymous"
    assert linhas[0]["row_count"] == 0


@pytest.mark.parametrize("url,endpoint,_campo", ROTAS)
def test_token_malformado_401_E_REGISTRADO(app_e_store, url, endpoint, _campo):
    client, store = app_e_store
    r = client.get(url, headers={"Authorization": "Bearer nao.e.um.jwt"})
    assert r.status_code == 401
    assert len(_linhas(store)) == 1


# ── 3. grant ausente: 403, e a linha NOMEIA quem ─────────────────────────────

@pytest.mark.parametrize("url,endpoint,_campo", ROTAS)
def test_grant_ausente_403_e_a_linha_NOMEIA(app_e_store, url, endpoint, _campo):
    client, store = app_e_store
    tok = _token({"contacts": {"operacao": {"access": "read_write"}}}, sub="fulano")
    r = client.get(url, headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 403, "sei quem é, e não pode — nunca 401"
    (linha,) = _linhas(store)
    assert linha["result"] == "denied"
    assert (linha["actor_sub"], linha["actor_kind"]) == ("fulano", "user")


def test_campo_vizinho_nao_serve(app_e_store):
    """`audit.sessions` concedido não abre `audit.mcp_calls` — e a recusa é gravada."""
    client, store = app_e_store
    tok = _token({"audit": {"sessions": {"access": "read_write"}}})
    assert client.get(ROTAS[0][0], headers={"Authorization": f"Bearer {tok}"}).status_code == 200
    assert client.get(ROTAS[1][0], headers={"Authorization": f"Bearer {tok}"}).status_code == 403
    resultados = [l["result"] for l in _linhas(store)]
    assert resultados == ["ok", "denied"]


# ── o 503 do serviço mal configurado chega como 503 ──────────────────────────

def test_sem_segredo_e_503_nao_403(monkeypatch, app_e_store):
    """Falha do SERVIÇO, não do chamador. Um 403 aqui mandaria o operador procurar um
    grant que não resolveria nada."""
    client, store = app_e_store
    from plughub_analytics_api import config as cfg

    class S:
        analytics_open_access = False
        auth_jwt_secret = ""

    monkeypatch.setattr(cfg, "get_settings", lambda: S())
    r = client.get(ROTAS[0][0], headers={"Authorization": "Bearer qualquer"})
    assert r.status_code == 503
    assert len(_linhas(store)) == 1, "nem o erro de config escapa da trilha"


# ── 4. sucesso: grava `ok` com a contagem ────────────────────────────────────

def test_sucesso_grava_ok_com_contagem(app_e_store):
    client, store = app_e_store
    tok = _token({"audit": {"sessions": {"access": "read_only"}}}, sub="dpo")
    r = client.get(ROTAS[0][0], headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    (linha,) = _linhas(store)
    assert linha["result"] == "ok"
    assert linha["row_count"] == 2
    assert (linha["actor_sub"], linha["actor_kind"]) == ("dpo", "user")


def test_open_access_grava_o_ator_NOMEADO(monkeypatch, app_e_store):
    """Bypass de demo aparece como `open_access` na trilha, nunca como usuário.

    Confundir os dois faria a auditoria de um ambiente com bypass ligado parecer uma
    auditoria de acessos autenticados.
    """
    client, store = app_e_store
    from plughub_analytics_api import config as cfg

    class S:
        analytics_open_access = True
        auth_jwt_secret = SECRET

    monkeypatch.setattr(cfg, "get_settings", lambda: S())
    assert client.get(ROTAS[1][0]).status_code == 200
    (linha,) = _linhas(store)
    assert linha["actor_kind"] == "open_access"


# ── a falha da própria trilha nunca derruba a resposta ───────────────────────

def test_falha_ao_gravar_trilha_nao_derruba_a_resposta(app_e_store, caplog):
    """Mas é ERROR, não `pass`: trilha que some em silêncio é pior que trilha nenhuma,
    porque a tela continua prometendo que ela existe."""
    client, store = app_e_store
    store.insert_audit_access_log = AsyncMock(side_effect=RuntimeError("ch fora"))
    tok = _token({"audit": {"sessions": {"access": "read_only"}}})
    with caplog.at_level("ERROR"):
        r = client.get(ROTAS[0][0], headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert any("audit_access_log" in rec.message for rec in caplog.records)
