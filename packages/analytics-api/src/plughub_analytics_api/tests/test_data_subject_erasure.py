"""
test_data_subject_erasure.py — AUD-06: eliminação do titular.

  · a ROTA: portão em ESCRITA (read_only do dossiê não basta), a prévia não toca em
    nada, a execução gera o marcador, execução parcial é 207 e fica na trilha;
  · o PERCURSO: as mutações do ClickHouse levam o tenant e as sessões no WHERE, apagam
    o texto de TODAS as mensagens e trocam só o autor CLIENTE; loja fora do ar sai
    nomeada em `failed_stores`; o cadastro de identidade é o ÚLTIMO a ser apagado.
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


LEITOR = {"audit": {"data_requests": {"access": "read_only", "scope": []}}}
DPO = {"audit": {"data_requests": {"access": "read_write", "scope": []}}}

_DOSSIER = {
    "customer_ids": ["cus_1"],
    "identity": {"status": "ok", "record": {"attributes": {"do_not_contact": True}}},
    "sessions": {"status": "ok", "count": 2, "truncated": False},
    "messages": {"status": "ok", "items": [{}, {}, {}]},
    "insights": {"status": "ok", "items": []},
    "attachments": {"status": "ok", "items": [{}]},
    "outbound": {"status": "ok", "entries": [{}], "contact_log": [{}, {}]},
    "surveys": {"status": "ok", "items": [{}]},
}


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
    calls: dict = {"execute": []}

    async def _fake_build(settings, store, tenant_id, *, customer_id, anchors):
        return dict(_DOSSIER) if (customer_id or anchors) else {**_DOSSIER, "customer_ids": [],
                                                                 "sessions": {"status": "ok", "count": 0,
                                                                              "truncated": False}}

    async def _fake_execute(settings, store, tenant_id, dossier, anchors, marker):
        calls["execute"].append({"tenant": tenant_id, "marker": marker, "anchors": anchors})
        return calls.get("outcome") or {"marker": marker, "complete": True, "failed_stores": [],
                                        "clickhouse": {"status": "ok", "sessions_found": 2}}

    monkeypatch.setattr(ds, "build_access_dossier", _fake_build)
    monkeypatch.setattr(ds, "execute_erasure", _fake_execute)
    a = FastAPI()
    a.include_router(audit_mod.router)
    a.state.store = _Store()
    return a, calls


def _post(app, body, token=None):
    a, _ = app
    h = {"Authorization": f"Bearer {token}"} if token else {}
    return TestClient(a).post("/v1/audit/data-requests/erasure", json=body, headers=h)


# ── rota ──────────────────────────────────────────────────────────────────────

def test_anonymous_is_401_and_the_refusal_is_on_the_trail(app):
    r = _post(app, {"tenant_id": "t1", "phone": "+5511999990000", "confirm": True})
    assert r.status_code == 401
    log = app[0].state.store.log
    assert log[-1]["result"] == "denied"
    assert log[-1]["endpoint"] == "audit.data_requests.erasure"
    assert app[1]["execute"] == []


def test_access_reader_cannot_erase(app):
    """O DPO que só LÊ o dossiê não apaga: read_write é o grau da eliminação."""
    r = _post(app, {"customer_id": "cus_1", "confirm": True}, _token(LEITOR))
    assert r.status_code == 403
    assert "read_write" in r.json()["detail"]
    assert app[1]["execute"] == []


def test_preview_touches_nothing_and_counts_per_store(app):
    r = _post(app, {"customer_id": "cus_1"}, _token(DPO))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "preview"
    assert body["messages"]["count"] == 3
    assert body["outbound"] == {"status": "ok", "entries": 1, "contact_log": 2}
    assert body["identity"]["veto_kept"] is True
    assert body["not_covered"], "a prévia tem de dizer o que NÃO é alcançado"
    assert app[1]["execute"] == []
    assert app[0].state.store.log[-1]["endpoint"] == "audit.data_requests.erasure_preview"


def test_confirm_executes_with_a_fresh_marker(app):
    r = _post(app, {"phone": "+5511999990000", "confirm": True}, _token(DPO))
    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "executed"
    (call,) = app[1]["execute"]
    assert call["marker"].startswith("erased:") and len(call["marker"]) > 10
    assert call["tenant"] == "t1"
    log = app[0].state.store.log[-1]
    assert log["result"] == "ok" and log["target_id"] == "cus_1"
    assert "+5511999990000" not in str(log), "telefone nunca vai para a trilha"


def test_partial_execution_is_207_and_partial_on_the_trail(app):
    app[1]["outcome"] = {"marker": "erased:x", "complete": False, "failed_stores": ["outbound"],
                         "clickhouse": {"status": "ok", "sessions_found": 2}}
    r = _post(app, {"customer_id": "cus_1", "confirm": True}, _token(DPO))
    assert r.status_code == 207
    assert app[0].state.store.log[-1]["result"] == "partial"


def test_other_tenant_in_body_is_403(app):
    r = _post(app, {"tenant_id": "t2", "customer_id": "cus_1", "confirm": True}, _token(DPO))
    assert r.status_code == 403
    assert app[1]["execute"] == []


# ── percurso ──────────────────────────────────────────────────────────────────

class _CH:
    def __init__(self):
        self.commands: list[str] = []
        self.settings: list = []

    class _R:
        def __init__(self, rows):
            self.result_rows = rows

    def query(self, sql, parameters=None):
        if sql.lstrip().startswith("SELECT DISTINCT session_id"):
            return self._R([("s1",), ("s'2",)])
        return self._R([(5,)])

    def command(self, sql, settings=None):
        self.commands.append(sql)
        self.settings.append(settings)


def test_clickhouse_mutations_are_scoped_and_quoted():
    ch = _CH()
    counts = ds.erase_clickhouse(ch, "db", "t1", ["s1", "s'2"], "erased:abc")
    assert counts == {"sessions": 5, "messages": 5, "insights": 5, "timeline": 5}
    assert len(ch.commands) == 5
    for sql in ch.commands:
        assert "tenant_id = 't1'" in sql
        assert "('s1','s\\'2')" in sql, "id com aspa tem de ir escapado"
    sessions = next(c for c in ch.commands if ".sessions UPDATE" in c)
    assert "customer_id = 'erased:abc'" in sessions and "ani = NULL" in sessions
    content = next(c for c in ch.commands if "UPDATE content" in c)
    assert "author_role" not in content, "o texto de TODAS as mensagens sai, não só o do cliente"
    author = next(c for c in ch.commands if "UPDATE author_id" in c)
    assert "author_role = 'customer'" in author
    assert all(s == {"mutations_sync": 2} for s in ch.settings)


def test_clickhouse_without_sessions_mutates_nothing():
    ch = _CH()
    assert ds.erase_clickhouse(ch, "db", "t1", [], "erased:abc") == \
        {"sessions": 0, "messages": 0, "insights": 0, "timeline": 0}
    assert ch.commands == []


def test_execute_names_the_failed_store_and_erases_identity_last(monkeypatch):
    order: list[str] = []

    class Store:
        _database = "db"

        def new_client(self):
            order.append("clickhouse")
            return _CH()

    async def _mail(*a, **k):
        order.append("mailing")
        return {"status": "unavailable: mailing-api (down)"}

    async def _surv(*a, **k):
        order.append("surveys")
        return {"status": "ok", "responses_anonymized": 1}

    async def _gw(*a, **k):
        order.append("gateway")
        return {"identity_status": "ok", "identity": {"customer_ids": ["cus_1"], "kept_veto": True},
                "attachments_status": "ok", "attachments": {"marked_deleted": 1, "blobs_pending": 1}}

    monkeypatch.setattr(ds, "mailing_erase", _mail)
    monkeypatch.setattr(ds, "survey_erase", _surv)
    monkeypatch.setattr(ds, "gateway_erase", _gw)
    out = asyncio.run(ds.execute_erasure(object(), Store(), "t1", {"customer_ids": ["cus_1"]},
                                         [{"kind": "phone", "value": "+55"}], "erased:abc"))
    assert out["complete"] is False
    assert out["failed_stores"] == ["outbound"]
    assert out["identity"]["kept_veto"] is True
    assert out["clickhouse"]["sessions_found"] == 2
    assert order[-1] == "gateway", f"o cadastro sai por último: {order}"
