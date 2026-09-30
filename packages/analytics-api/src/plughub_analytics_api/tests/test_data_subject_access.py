"""
test_data_subject_access.py — AUD-03: o dossiê de acesso do titular.

Duas metades:
  · a ROTA: o portão (`audit.data_requests`), a recusa gravada, o tenant do token, e
    que telefone/e-mail NUNCA vão para a trilha;
  · o PERCURSO (`build_access_dossier`): cada loja diz o seu status, e loja fora do
    ar ou sem configuração sai NOMEADA — nunca vazia em silêncio.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plughub_analytics_api import audit as audit_mod
from plughub_analytics_api import data_subject as ds

SECRET = "segredo-de-teste"


def _token(module_config: dict, tenant: str = "t1", sub: str = "dpo") -> str:
    return jwt.encode(
        {"sub": sub, "tenant_id": tenant, "module_config": module_config,
         "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        SECRET, algorithm="HS256",
    )


DPO = {"audit": {"data_requests": {"access": "read_only", "scope": []}}}
OUTRO = {"audit": {"sessions": {"access": "read_only", "scope": []}}}


class _Store:
    def __init__(self):
        self.log: list[dict] = []

    async def insert_audit_access_log(self, row):
        self.log.append(row)


@pytest.fixture
def app(monkeypatch):
    from plughub_analytics_api import config as cfg

    class S:
        analytics_open_access = False
        auth_jwt_secret = SECRET

    cfg.get_settings.cache_clear()
    monkeypatch.setattr(cfg, "get_settings", lambda: S())
    captured: dict = {}

    async def _fake_build(settings, store, tenant_id, *, customer_id, anchors):
        captured.update(tenant=tenant_id, customer_id=customer_id, anchors=anchors)
        return {"customer_ids": ["cus_1"] if (customer_id or anchors) else [],
                "sessions": {"count": 2}}

    monkeypatch.setattr(ds, "build_access_dossier", _fake_build)
    a = FastAPI()
    a.include_router(audit_mod.router)
    a.state.store = _Store()
    return a, captured


def _post(app, body, token=None):
    a, _ = app
    h = {"Authorization": f"Bearer {token}"} if token else {}
    return TestClient(a).post("/v1/audit/data-requests/access", json=body, headers=h)


def test_anonimo_e_401_e_a_recusa_fica_na_trilha(app):
    r = _post(app, {"tenant_id": "t1", "phone": "+5511999990000"})
    assert r.status_code == 401
    log = app[0].state.store.log
    assert log and log[-1]["result"] == "denied"
    assert "+55" not in log[-1]["target_id"] and log[-1]["target_id"] == "anchors:phone"


def test_anonimo_SEM_CORPO_passa_pelo_portao_e_pela_trilha(app):
    """Com o corpo obrigatório, o FastAPI respondia 422 antes do handler, e a tentativa
    anônima não deixava linha (medido pela varredura anônima)."""
    a, _ = app
    r = TestClient(a).post("/v1/audit/data-requests/access")
    assert r.status_code == 401
    assert a.state.store.log[-1]["result"] == "denied"


def test_anonimo_com_corpo_INVALIDO_e_401_nao_422(app):
    """A varredura anônima manda corpo inválido de propósito: rota aberta responde 422
    sem executar. Aqui o portão tem de responder antes da validação."""
    a, _ = app
    r = TestClient(a).post("/v1/audit/data-requests/access", content='"x"',
                           headers={"content-type": "application/json"})
    assert r.status_code == 401


def test_dpo_com_corpo_invalido_e_422(app):
    a, _ = app
    r = TestClient(a).post("/v1/audit/data-requests/access", content='"x"',
                           headers={"content-type": "application/json",
                                    "Authorization": f"Bearer {_token(DPO)}"})
    assert r.status_code == 422


def test_grant_de_OUTRO_campo_de_auditoria_nao_abre(app):
    r = _post(app, {"customer_id": "cus_1"}, _token(OUTRO))
    assert r.status_code == 403


def test_controle_POSITIVO_o_dpo_recebe_o_dossie(app):
    r = _post(app, {"customer_id": "cus_1"}, _token(DPO))
    assert r.status_code == 200, r.text
    log = app[0].state.store.log[-1]
    assert log["result"] == "ok" and log["target_id"] == "cus_1" and log["row_count"] == 2
    assert log["tenant_id"] == "t1" and log["actor_sub"] == "dpo"


def test_telefone_e_email_nao_vao_para_a_trilha_nem_quando_aceito(app):
    r = _post(app, {"phone": "+5511999990000", "email": "a@b.c"}, _token(DPO))
    assert r.status_code == 200
    row = app[0].state.store.log[-1]
    assert "+5511" not in str(row) and "a@b.c" not in str(row)
    assert app[1]["anchors"] == [{"kind": "phone", "value": "+5511999990000"},
                                 {"kind": "email", "value": "a@b.c"}]


def test_tenant_do_corpo_divergente_e_403(app):
    r = _post(app, {"tenant_id": "outro", "customer_id": "cus_1"}, _token(DPO))
    assert r.status_code == 403 and r.json()["detail"] == "tenant_mismatch"


def test_sem_identificador_e_422(app):
    assert _post(app, {}, _token(DPO)).status_code == 422


# ── o percurso ────────────────────────────────────────────────────────────────

class _Cfg:
    channel_gateway_url = ""
    channel_gateway_service_token = ""
    mailing_api_url = ""
    mailing_service_token = ""
    evaluation_api_url = ""
    evaluation_service_token = ""
    session_replayer_url = ""
    session_replayer_service_token = ""


class _CH:
    """ClickHouse falso: devolve sessões, mensagens e insights pela ordem das queries."""

    def __init__(self, sessions):
        self._sessions = sessions
        self.queries: list[str] = []

    def query(self, sql, parameters=None):
        self.queries.append(sql)

        class R:
            pass
        r = R()
        if "FROM" in sql and ".sessions" in sql:
            r.result_rows = self._sessions
        elif ".messages" in sql:
            r.result_rows = [("s1", "m1", "customer", "text", "all", '{"text":"oi"}', None)]
        elif ".segments" in sql:
            r.result_rows = [("s1", "seg1", "primary", "cliente mudou de endereco", None, None)]
        else:
            r.result_rows = []
        return r


class _Store2:
    _database = "db"

    def __init__(self, ch):
        self._ch = ch

    def new_client(self):
        return self._ch


def test_lojas_sem_configuracao_saem_NOMEADAS_nunca_vazias_em_silencio(monkeypatch):
    ch = _CH([("s1", "webchat", "p", "cus_1", None, None, None, None, "", None, "s1")])
    d = asyncio.run(ds.build_access_dossier(_Cfg(), _Store2(ch), "t1",
                                            customer_id="cus_1", anchors=[]))
    assert d["sessions"]["count"] == 1 and d["messages"]["items"][0]["content"] == "oi"
    for secao in (d["identity"]["status"], d["attachments"]["status"],
                  d["outbound"]["status"], d["surveys"]["status"],
                  d["session_record"]["status"], d["evaluations"]["status"]):
        assert secao.startswith("unavailable:"), secao
    assert d["not_covered"], "o dossiê precisa dizer o que NÃO olhou"
    assert d["wrapups"]["items"] == [{"session_id": "s1", "segment_id": "seg1", "role": "primary",
                                      "summary": "cliente mudou de endereco", "next_steps": None,
                                      "started_at": None}]


def test_telefone_sem_resolvedor_nao_inventa_cliente_mas_acha_a_chamada(monkeypatch):
    """Sem cliente resolvido, `customer_ids` fica vazio — mas o telefone ainda é
    procurado nas sessões: na voz SIP o `customer_id` da sessão é o número."""
    ch = _CH([("s9", "voice", "p", "+5511", None, None, None, None, "", None, "s9")])
    captured = {}
    orig = ds.fetch_clickhouse

    def _spy(client, db, tenant, ids):
        captured["ids"] = ids
        return orig(client, db, tenant, ids)

    monkeypatch.setattr(ds, "fetch_clickhouse", _spy)
    d = asyncio.run(ds.build_access_dossier(_Cfg(), _Store2(ch), "t1", customer_id="",
                                            anchors=[{"kind": "phone", "value": "+5511"}]))
    assert d["customer_ids"] == []
    assert d["resolution"]["status"].startswith("unavailable:")
    assert captured["ids"] == ["+5511"] and d["sessions"]["count"] == 1


def test_fusao_os_ids_fundidos_entram_na_busca_de_sessoes(monkeypatch):
    async def _gw(settings, tenant_id, customer_id, session_ids):
        if customer_id:
            return {"customer": {"customer_id": "cus_novo", "merged_from": ["cus_velho"]},
                    "customer_status": "ok"}
        return {"attachments": [], "attachments_status": "ok"}

    monkeypatch.setattr(ds, "gateway_export", _gw)
    ch = _CH([])
    captured = {}
    orig = ds.fetch_clickhouse

    def _spy(client, db, tenant, ids):
        captured["ids"] = ids
        return orig(client, db, tenant, ids)

    monkeypatch.setattr(ds, "fetch_clickhouse", _spy)
    d = asyncio.run(ds.build_access_dossier(_Cfg(), _Store2(ch), "t1",
                                            customer_id="cus_velho", anchors=[]))
    assert set(captured["ids"]) == {"cus_velho", "cus_novo"}
    assert d["identity"]["status"] == "ok"


def test_aud09_o_que_sobra_fora_do_dossie_e_so_o_que_expira_sozinho():
    """Loja DURÁVEL nova entra no percurso, nunca na lista do que não foi olhado."""
    stores = " ".join(n["store"] for n in ds.NOT_COVERED)
    assert "postgres" not in stores, stores
    assert {n["store"].split()[0] for n in ds.NOT_COVERED} == {"redis", "kafka"}


def test_aud09_registro_duravel_e_avaliacoes_vao_pelas_sessoes_achadas(monkeypatch):
    seen = {}

    async def _rep(settings, tenant_id, session_ids):
        seen["rep"] = session_ids
        return {"status": "ok", "stream_events": [{"event_id": "e1"}], "context_snapshots": [],
                "pipeline_states": []}

    async def _ev(settings, tenant_id, session_ids):
        seen["ev"] = session_ids
        return {"status": "ok", "evaluations": [{"id": "r1"}]}

    monkeypatch.setattr(ds, "replayer_export", _rep)
    monkeypatch.setattr(ds, "evaluations_export", _ev)
    ch = _CH([("s1", "webchat", "p", "cus_1", None, None, None, None, "", None, "s1")])
    d = asyncio.run(ds.build_access_dossier(_Cfg(), _Store2(ch), "t1", customer_id="cus_1", anchors=[]))
    assert seen == {"rep": ["s1"], "ev": ["s1"]}
    assert d["session_record"]["stream_events"] == [{"event_id": "e1"}]
    assert d["evaluations"]["items"] == [{"id": "r1"}]


def test_aud09_sem_sessao_nao_pergunta_ao_replayer(monkeypatch):
    async def _boom(*a, **k):
        raise AssertionError("sem sessão não há o que pedir")

    monkeypatch.setattr(ds, "replayer_export", _boom)
    monkeypatch.setattr(ds, "evaluations_export", _boom)
    d = asyncio.run(ds.build_access_dossier(_Cfg(), _Store2(_CH([])), "t1", customer_id="cus_1", anchors=[]))
    assert d["session_record"]["status"] == "ok" and d["session_record"]["stream_events"] == []
