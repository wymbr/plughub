"""
test_router.py
Integration tests for evaluation-api router.
All PostgreSQL and Kafka calls are mocked.

Tests: 42 assertions across:
  TestHealth           (1)
  TestForms            (8)
  TestCampaigns        (7)
  TestInstances        (8)
  TestIngest           (5)
  TestResults          (6)
  TestContestations    (5)
  TestSampleCheck      (3)
  TestReports          (3)
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Any

import jwt as pyjwt
import pytest
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from ..router import router
from ..config import settings

# Bearer JWT com os grants ABAC da config humana (G-PROBE fase 1): forms/campaigns
# exigem evaluation.formularios; rubric exige evaluation.gerir_rubrica. Os testes de
# forms/campanhas passam este header nos endpoints gateados (list + mutações).
_EVAL_TOKEN = pyjwt.encode(
    {"sub": "u_test", "module_config": {"evaluation": {
        "formularios": {"access": "read_write", "scope": []},
        "gerir_rubrica": {"access": "read_write", "scope": []},
    }}},
    settings.jwt_secret, algorithm="HS256",
)
_AUTH = {"Authorization": f"Bearer {_EVAL_TOKEN}"}


# ─── Fixtures ─────────────────────────────────────────────────────────────────

def _make_form(form_id: str = "evform_abc", tenant_id: str = "t1") -> dict:
    return {
        "id": form_id, "tenant_id": tenant_id, "name": "Test Form",
        "description": "", "version": 1, "status": "active",
        "dimensions": [], "total_weight": 1.0, "passing_score": 0.7,
        "allow_na": True, "knowledge_domains": [],
        "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
        "created_by": "operator",
    }


def _make_campaign(
    campaign_id: str = "evcampaign_abc",
    form_id: str = "evform_abc",
    tenant_id: str = "t1",
    status: str = "active",
) -> dict:
    return {
        "id": campaign_id, "tenant_id": tenant_id, "name": "Test Campaign",
        "description": "", "form_id": form_id, "pool_id": "sac_ia",
        "status": status, "sampling_rules": {}, "reviewer_rules": {}, "schedule": {},
        "total_instances": 0, "completed_instances": 0, "avg_score": None,
        "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
        "created_by": "operator",
    }


def _make_instance(
    instance_id: str = "evinstance_abc",
    campaign_id: str = "evcampaign_abc",
    tenant_id: str = "t1",
    status: str = "scheduled",
) -> dict:
    return {
        "id": instance_id, "tenant_id": tenant_id,
        "campaign_id": campaign_id, "form_id": "evform_abc",
        "session_id": "sess_001", "segment_id": None,
        "evaluator_agent_id": None, "reviewer_agent_id": None,
        "status": status, "priority": 5,
        "scheduled_at": "2026-01-01T00:00:00Z", "assigned_at": None,
        "completed_at": None, "expires_at": None, "error_message": None,
        "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
    }


def _make_result(
    result_id: str = "evresult_abc",
    instance_id: str = "evinstance_abc",
    tenant_id: str = "t1",
    eval_status: str = "submitted",
) -> dict:
    return {
        "id": result_id, "tenant_id": tenant_id,
        "instance_id": instance_id, "session_id": "sess_001",
        "campaign_id": "evcampaign_abc", "form_id": "evform_abc",
        "evaluator_agent_id": "agente_avaliacao_v1-001",
        "overall_score": 8.5, "max_score": 10.0, "normalized_score": 0.85,
        "passed": True, "eval_status": eval_status,
        "evaluator_notes": "", "comparison_mode": False,
        "comparison_report": None, "knowledge_snippets": [],
        "reviewer_agent_id": None, "reviewer_outcome": None,
        "reviewer_notes": None, "reviewer_score": None, "reviewed_at": None,
        "contested_by": None, "contested_at": None, "contestation_reason": None,
        "locked_at": None, "locked_by": None,
        "submitted_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
    }


def _make_contestation(
    contest_id: str = "evcontest_abc",
    result_id: str = "evresult_abc",
    tenant_id: str = "t1",
    status: str = "open",
) -> dict:
    return {
        "id": contest_id, "tenant_id": tenant_id,
        "result_id": result_id, "instance_id": "evinstance_abc",
        "session_id": "sess_001",
        "contested_by": "agent_human_001",
        "contested_at": "2026-01-01T00:00:00Z",
        "contestation_reason": "Score too low",
        "status": status, "adjudicated_by": None, "adjudicated_at": None,
        "adjudication_notes": None, "adjusted_score": None,
        "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
    }


def _wire_acquire(db_mock: Any) -> Any:
    """Faz `async with pool.acquire() as conn` devolver um conn que responde "sem linha".

    **Por que (2026-08-03).** `MagicMock()` configura `__aenter__` como `AsyncMock`
    automaticamente (Python 3.8+). Então `async with pool.acquire() as conn` entregava um
    **AsyncMock**, e todo `await conn.fetchrow(...)` devolvia outro AsyncMock — que o
    `db.py:699` (`dict(record).items()`) tenta iterar, estourando
    `AsyncMock.keys() returned a non-iterable (type coroutine)`.

    O dublê não estava *errado*: estava **respondendo qualquer coisa**. Seis testes morriam
    não pelo que afirmavam, mas porque o handler ganhou uma query a mais que ninguém
    patchou — e o dublê, em vez de dizer "não há linha", devolvia um objeto que se parece
    com tudo. É a mesma regra que os quatro casos de 2026-08-02 deixaram: *dublê responde à
    ESTRUTURA do que foi pedido*. A estrutura de uma consulta sem resultado é `None`, não
    um mock.

    Consequência aceita de propósito: uma query não-patchada agora degrada para "vazio" em
    vez de explodir. Some o ruído, mas some também o aviso — por isso o conn registra as
    chamadas, e um teste que dependa da linha DEVE patchar o `_db.*` correspondente. O
    critério é o de sempre: prefira INCONCLUSIVO a passar por ausência de amostra.
    """
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)
    conn.fetch    = AsyncMock(return_value=[])
    conn.fetchval = AsyncMock(return_value=None)
    conn.execute  = AsyncMock(return_value="")

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__  = AsyncMock(return_value=False)
    db_mock.acquire = MagicMock(return_value=cm)

    # O pool também é usado diretamente em vários helpers (sem acquire).
    for meth, val in (("fetchrow", None), ("fetch", []), ("fetchval", None), ("execute", "")):
        if not isinstance(getattr(db_mock, meth, None), AsyncMock):
            setattr(db_mock, meth, AsyncMock(return_value=val))
    return db_mock


def _app_with_mocks(db_mock: Any, kafka_mock: Any, redis_mock: Any | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.state.db_pool = _wire_acquire(db_mock)
    app.state.kafka_producer = kafka_mock
    # `state.redis` passou a ser exigido por rotas que escrevem no ContextStore
    # (`_redis`/`_write_ctx`). Sem ele o acesso levanta AttributeError DENTRO do
    # handler e vira 500 — dois testes de contestação morriam com
    # "'State' object has no attribute 'redis'", que se lê como defeito de código.
    # A escrita é fire-and-forget dentro de try/except, então um MagicMock basta:
    # o `await` falha, o except registra o motivo, e a rota segue — que é exatamente
    # a degradação desenhada.
    app.state.redis = redis_mock if redis_mock is not None else MagicMock()
    return app


@pytest.fixture(autouse=True)
def _open_token_gates(monkeypatch):
    """Neutraliza os portões de token PARA OS TESTES DE NEGÓCIO — de propósito.

    **Achado 2026-08-03.** `_require_admin` e `_require_service` são no-op quando o
    token correspondente é vazio (*"`service_token` vazio = no-op (postura demo
    aberta)"*, `router.py:209`). Os testes foram escritos contra esse ramo — e o default
    É vazio **no repositório**. No container do compose,
    `PLUGHUB_EVALUATION_ADMIN_TOKEN` e `PLUGHUB_EVALUATION_SERVICE_TOKEN` estão
    definidos (`docker-compose.demo.yml:1423-1424`), então oito testes passaram a
    receber 401 sem que uma linha de produção estivesse errada.

    É a MESMA causa dos 7 vermelhos do pricing-api, medida no mesmo dia: teste que
    herda a configuração do DEPLOY em vez de declarar a sua. O sintoma muda de pacote
    para pacote (403 lá, 401 aqui); a causa não. E o modo de falha é traiçoeiro nos dois
    sentidos — num ambiente sem os tokens, esta suíte fica verde **sem nunca exercitar o
    portão**, que foi o estado anterior.

    Por isso o portão ganhou teste PRÓPRIO (`TestServiceTokenGate`), onde o token é
    definido pelo teste e a recusa é o comportamento afirmado.
    """
    monkeypatch.setattr(settings, "admin_token", "", raising=False)
    monkeypatch.setattr(settings, "service_token", "", raising=False)


async def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ─── Tests ────────────────────────────────────────────────────────────────────

class TestHealth:
    @pytest.mark.asyncio
    async def test_health_ok(self):
        app = _app_with_mocks(MagicMock(), MagicMock())
        async with await _client(app) as c:
            resp = await c.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestForms:
    def _app(self, db_mock):
        return _app_with_mocks(db_mock, AsyncMock())

    @pytest.mark.asyncio
    async def test_list_forms(self):
        db = MagicMock()
        with patch("plughub_evaluation_api.router._db.list_forms", new=AsyncMock(return_value=[_make_form()])):
            async with await _client(self._app(db)) as c:
                resp = await c.get("/v1/evaluation/forms?tenant_id=t1", headers=_AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["forms"][0]["id"] == "evform_abc"

    @pytest.mark.asyncio
    async def test_create_form(self):
        form = _make_form()
        with patch("plughub_evaluation_api.router._db.create_form", new=AsyncMock(return_value=form)):
            async with await _client(self._app(MagicMock())) as c:
                resp = await c.post("/v1/evaluation/forms", headers=_AUTH, json={
                    "tenant_id": "t1", "name": "Test Form"
                })
        assert resp.status_code == 201
        assert resp.json()["id"] == "evform_abc"

    @pytest.mark.asyncio
    async def test_get_form_found(self):
        with patch("plughub_evaluation_api.router._db.get_form", new=AsyncMock(return_value=_make_form())):
            async with await _client(self._app(MagicMock())) as c:
                resp = await c.get("/v1/evaluation/forms/evform_abc?tenant_id=t1")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_get_form_not_found(self):
        with patch("plughub_evaluation_api.router._db.get_form", new=AsyncMock(return_value=None)):
            async with await _client(self._app(MagicMock())) as c:
                resp = await c.get("/v1/evaluation/forms/evform_missing?tenant_id=t1")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_update_form(self):
        updated = _make_form()
        updated["name"] = "Updated"
        with patch("plughub_evaluation_api.router._db.update_form", new=AsyncMock(return_value=updated)):
            async with await _client(self._app(MagicMock())) as c:
                resp = await c.put("/v1/evaluation/forms/evform_abc?tenant_id=t1", headers=_AUTH, json={"name": "Updated"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "Updated"

    @pytest.mark.asyncio
    async def test_delete_form_archives(self):
        archived = _make_form()
        archived["status"] = "archived"
        with patch("plughub_evaluation_api.router._db.update_form", new=AsyncMock(return_value=archived)):
            async with await _client(self._app(MagicMock())) as c:
                resp = await c.delete("/v1/evaluation/forms/evform_abc?tenant_id=t1", headers=_AUTH)
        assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_delete_form_not_found(self):
        with patch("plughub_evaluation_api.router._db.update_form", new=AsyncMock(return_value=None)):
            async with await _client(self._app(MagicMock())) as c:
                resp = await c.delete("/v1/evaluation/forms/evform_missing?tenant_id=t1", headers=_AUTH)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_form_with_dimensions(self):
        form = _make_form()
        form["dimensions"] = [{"id": "dim_1", "name": "Quality", "weight": 0.5, "criteria": []}]
        with patch("plughub_evaluation_api.router._db.create_form", new=AsyncMock(return_value=form)):
            async with await _client(self._app(MagicMock())) as c:
                resp = await c.post("/v1/evaluation/forms", headers=_AUTH, json={
                    "tenant_id": "t1", "name": "Form with dims",
                    "dimensions": [{"id": "dim_1", "name": "Quality", "weight": 0.5, "criteria": []}],
                })
        assert resp.status_code == 201
        assert len(resp.json()["dimensions"]) == 1


class TestConfigAbacGate:
    """G-PROBE fase 1 — gate ABAC grant-first na config humana (forms/campaigns/rubric)."""

    @pytest.fixture(autouse=True)
    def _service_token_set(self, monkeypatch):
        """
        DESFAZ, so nesta classe, o `_open_token_gates` do modulo.

        Aquele fixture zera `settings.service_token` de proposito (os testes de
        NEGOCIO nao devem herdar a config do deploy). Mas
        `_require_service_or_eval_write` comeca com

            if not settings.service_token: return

        entao, com o token zerado, a guarda devolve ANTES da checagem ABAC — e esta
        classe, que existe para afirmar que a checagem ABAC RECUSA, passava direto
        para o handler e morria em `ResponseValidationError`. O fixture do modulo
        desligava exatamente o que esta classe mede.

        Os testes daqui NAO mandam `x-service-token`, entao a guarda cai no ramo
        Bearer+ABAC — que e a proposicao afirmada.
        """
        monkeypatch.setattr(settings, "service_token", "svc-token-do-teste", raising=False)

    @staticmethod
    def _tok(**fields: str) -> dict:
        cfg = {f: {"access": acc, "scope": []} for f, acc in fields.items()}
        t = pyjwt.encode({"sub": "u", "module_config": {"evaluation": cfg}},
                         settings.jwt_secret, algorithm="HS256")
        return {"Authorization": f"Bearer {t}"}

    @pytest.mark.asyncio
    async def test_list_forms_open_no_token_200(self):
        # Fase 1: LISTA é read compartilhado → aberta (sem Bearer).
        with patch("plughub_evaluation_api.router._db.list_forms", new=AsyncMock(return_value=[])):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.get("/v1/evaluation/forms?tenant_id=t1")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_create_form_no_token_401(self):
        async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
            resp = await c.post("/v1/evaluation/forms", json={"tenant_id": "t1", "name": "X"})
        assert resp.status_code == 401  # mutação exige Bearer

    @pytest.mark.asyncio
    async def test_create_form_without_grant_403(self):
        async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
            resp = await c.post("/v1/evaluation/forms", headers=self._tok(report="read_only"),
                                json={"tenant_id": "t1", "name": "X"})
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_create_form_readonly_grant_403(self):
        # read_only NÃO satisfaz mutação (read_write)
        async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
            resp = await c.post("/v1/evaluation/forms", headers=self._tok(formularios="read_only"),
                                json={"tenant_id": "t1", "name": "X"})
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_create_form_readwrite_grant_ok(self):
        with patch("plughub_evaluation_api.router._db.create_form", new=AsyncMock(return_value=_make_form())):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.post("/v1/evaluation/forms", headers=self._tok(formularios="read_write"),
                                    json={"tenant_id": "t1", "name": "X"})
        assert resp.status_code == 201

    @pytest.mark.asyncio
    async def test_create_rubric_wrong_field_403(self):
        # rubric exige gerir_rubrica; formularios não serve
        async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
            resp = await c.post("/v1/evaluation/rubric-templates", headers=self._tok(formularios="read_write"),
                                json={"tenant_id": "t1", "name": "X"})
        assert resp.status_code == 403


class TestCampaigns:
    @pytest.mark.asyncio
    async def test_create_campaign(self):
        camp = _make_campaign()
        with patch("plughub_evaluation_api.router._db.get_form", new=AsyncMock(return_value=_make_form())), \
             patch("plughub_evaluation_api.router._db.create_campaign", new=AsyncMock(return_value=camp)):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.post("/v1/evaluation/campaigns", headers=_AUTH, json={
                    "tenant_id": "t1", "name": "Test Campaign",
                    "form_id": "evform_abc", "pool_id": "sac_ia",
                })
        assert resp.status_code == 201
        assert resp.json()["id"] == "evcampaign_abc"

    @pytest.mark.asyncio
    async def test_create_campaign_form_not_found(self):
        with patch("plughub_evaluation_api.router._db.get_form", new=AsyncMock(return_value=None)):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.post("/v1/evaluation/campaigns", headers=_AUTH, json={
                    "tenant_id": "t1", "name": "Bad Campaign",
                    "form_id": "evform_missing", "pool_id": "sac_ia",
                })
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_list_campaigns(self):
        with patch("plughub_evaluation_api.router._db.list_campaigns", new=AsyncMock(return_value=[_make_campaign()])):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.get("/v1/evaluation/campaigns?tenant_id=t1", headers=_AUTH)
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    @pytest.mark.asyncio
    async def test_get_campaign(self):
        with patch("plughub_evaluation_api.router._db.get_campaign", new=AsyncMock(return_value=_make_campaign())):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.get("/v1/evaluation/campaigns/evcampaign_abc?tenant_id=t1")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_pause_campaign(self):
        paused = _make_campaign(status="paused")
        with patch("plughub_evaluation_api.router._db.update_campaign", new=AsyncMock(return_value=paused)):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.post("/v1/evaluation/campaigns/evcampaign_abc/pause?tenant_id=t1", headers=_AUTH)
        assert resp.status_code == 200
        assert resp.json()["status"] == "paused"

    @pytest.mark.asyncio
    async def test_resume_campaign(self):
        active = _make_campaign(status="active")
        with patch("plughub_evaluation_api.router._db.update_campaign", new=AsyncMock(return_value=active)):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.post("/v1/evaluation/campaigns/evcampaign_abc/resume?tenant_id=t1", headers=_AUTH)
        assert resp.status_code == 200
        assert resp.json()["status"] == "active"

    @pytest.mark.asyncio
    async def test_update_sampling_rules(self):
        updated = _make_campaign()
        updated["sampling_rules"] = {"mode": "fixed", "every_n": 3}
        with patch("plughub_evaluation_api.router._db.update_campaign", new=AsyncMock(return_value=updated)):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.put(
                    "/v1/evaluation/campaigns/evcampaign_abc?tenant_id=t1",
                    headers=_AUTH,
                    json={"sampling_rules": {"mode": "fixed", "every_n": 3}},
                )
        assert resp.status_code == 200
        assert resp.json()["sampling_rules"]["mode"] == "fixed"


class TestInstances:
    @pytest.mark.asyncio
    async def test_create_instance(self):
        inst = _make_instance()
        with patch("plughub_evaluation_api.router._db.get_campaign", new=AsyncMock(return_value=_make_campaign())), \
             patch("plughub_evaluation_api.router.compute_expires_at", new=AsyncMock(return_value=None)), \
             patch("plughub_evaluation_api.router._db.create_instance", new=AsyncMock(return_value=inst)), \
             patch("plughub_evaluation_api.router._kafka.emit_instance_created", new=AsyncMock()):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.post("/v1/evaluation/instances", json={
                    "tenant_id": "t1", "campaign_id": "evcampaign_abc", "session_id": "sess_001",
                })
        assert resp.status_code == 201
        assert resp.json()["id"] == "evinstance_abc"

    @pytest.mark.asyncio
    async def test_create_instance_campaign_not_found(self):
        with patch("plughub_evaluation_api.router._db.get_campaign", new=AsyncMock(return_value=None)):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.post("/v1/evaluation/instances", json={
                    "tenant_id": "t1", "campaign_id": "bad_campaign", "session_id": "sess_001",
                })
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_create_instance_paused_campaign_rejected(self):
        with patch("plughub_evaluation_api.router._db.get_campaign",
                   new=AsyncMock(return_value=_make_campaign(status="paused"))):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.post("/v1/evaluation/instances", json={
                    "tenant_id": "t1", "campaign_id": "evcampaign_abc", "session_id": "sess_001",
                })
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_list_instances(self):
        with patch("plughub_evaluation_api.router._db.list_instances", new=AsyncMock(return_value=[_make_instance()])):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.get("/v1/evaluation/instances?tenant_id=t1")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    @pytest.mark.asyncio
    async def test_get_instance_not_found(self):
        with patch("plughub_evaluation_api.router._db.get_instance", new=AsyncMock(return_value=None)):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.get("/v1/evaluation/instances/missing?tenant_id=t1")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_claim_instance(self):
        inst = _make_instance(status="assigned")
        inst["evaluator_agent_id"] = "agente_avaliacao_v1-001"
        with patch("plughub_evaluation_api.router._db.claim_next_instance", new=AsyncMock(return_value=inst)), \
             patch("plughub_evaluation_api.router._kafka.emit_instance_assigned", new=AsyncMock()):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.post("/v1/evaluation/instances/claim", json={
                    "tenant_id": "t1", "evaluator_agent_id": "agente_avaliacao_v1-001",
                })
        assert resp.status_code == 200
        assert resp.json()["status"] == "assigned"

    @pytest.mark.asyncio
    async def test_claim_no_available(self):
        with patch("plughub_evaluation_api.router._db.claim_next_instance", new=AsyncMock(return_value=None)):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.post("/v1/evaluation/instances/claim", json={"tenant_id": "t1"})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_expire_instance(self):
        inst = _make_instance(status="expired")
        with patch("plughub_evaluation_api.router._db.update_instance_status", new=AsyncMock(return_value=inst)), \
             patch("plughub_evaluation_api.router._kafka.emit_instance_expired", new=AsyncMock()):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.post("/v1/evaluation/instances/evinstance_abc/expire?tenant_id=t1")
        assert resp.status_code == 204


class TestServiceTokenGate:
    """O portão `_require_service`, exercitado com o token DEFINIDO pelo teste.

    Até 2026-08-03 nada aqui verificava recusa: com token vazio o portão é no-op, e com
    token setado (container) a suíte inteira reprovava. Os dois estados eram
    indistinguíveis de "não há portão".
    """

    _TOKEN = "service_token_de_teste"

    @pytest.fixture(autouse=True)
    def _close_the_gate(self, monkeypatch):
        monkeypatch.setattr(settings, "service_token", self._TOKEN, raising=False)

    @pytest.mark.asyncio
    async def test_claim_without_service_token_is_401(self):
        async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
            resp = await c.post("/v1/evaluation/instances/claim", json={"tenant_id": "t1"})
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_claim_with_wrong_service_token_is_401(self):
        async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
            resp = await c.post("/v1/evaluation/instances/claim",
                                json={"tenant_id": "t1"},
                                headers={"x-service-token": "chute"})
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_claim_with_correct_service_token_passes_the_gate(self):
        """Controle positivo — sem ele, os 401 acima passariam num portão que nega tudo.

        404 (e não 200) porque `claim_next_instance` devolve None: o que se afirma aqui
        é ter ATRAVESSADO a autenticação e alcançado a regra de negócio.
        """
        with patch("plughub_evaluation_api.router._db.claim_next_instance",
                   new=AsyncMock(return_value=None)):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.post("/v1/evaluation/instances/claim",
                                    json={"tenant_id": "t1"},
                                    headers={"x-service-token": self._TOKEN})
        assert resp.status_code == 404


class TestIngest:
    @pytest.mark.asyncio
    async def test_ingest_creates_result(self):
        result = _make_result()
        with patch("plughub_evaluation_api.router._db.get_instance", new=AsyncMock(return_value=_make_instance())), \
             patch("plughub_evaluation_api.router._db.create_result", new=AsyncMock(return_value=result)), \
             patch("plughub_evaluation_api.router._db.create_criterion_responses", new=AsyncMock(return_value=[])), \
             patch("plughub_evaluation_api.router._kafka.emit_instance_completed", new=AsyncMock()):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.post("/v1/evaluation/ingest", json={
                    "tenant_id": "t1",
                    "instance_id": "evinstance_abc",
                    "session_id": "sess_001",
                    "campaign_id": "evcampaign_abc",
                    "form_id": "evform_abc",
                    "evaluator_agent_id": "agente_avaliacao_v1-001",
                    "overall_score": 8.5,
                })
        assert resp.status_code == 201
        data = resp.json()
        assert data["result_id"] == "evresult_abc"
        assert data["eval_status"] == "submitted"

    @pytest.mark.asyncio
    async def test_ingest_instance_not_found(self):
        with patch("plughub_evaluation_api.router._db.get_instance", new=AsyncMock(return_value=None)):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.post("/v1/evaluation/ingest", json={
                    "tenant_id": "t1", "instance_id": "missing",
                    "session_id": "s", "campaign_id": "c", "form_id": "f",
                    "evaluator_agent_id": "a",
                })
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_ingest_with_criterion_responses(self):
        result = _make_result()
        responses = [{"id": "evcrr_001", "criterion_id": "crit_1"}]
        with patch("plughub_evaluation_api.router._db.get_instance", new=AsyncMock(return_value=_make_instance())), \
             patch("plughub_evaluation_api.router._db.create_result", new=AsyncMock(return_value=result)), \
             patch("plughub_evaluation_api.router._db.create_criterion_responses", new=AsyncMock(return_value=responses)), \
             patch("plughub_evaluation_api.router._kafka.emit_instance_completed", new=AsyncMock()):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.post("/v1/evaluation/ingest", json={
                    "tenant_id": "t1", "instance_id": "evinstance_abc",
                    "session_id": "sess_001", "campaign_id": "evcampaign_abc",
                    "form_id": "evform_abc", "evaluator_agent_id": "a",
                    "criterion_responses": [{"criterion_id": "crit_1", "score": 8.0}],
                })
        assert resp.status_code == 201
        assert resp.json()["criteria_rows_created"] == 1

    @pytest.mark.asyncio
    async def test_ingest_emits_kafka(self):
        result = _make_result()
        kafka_mock = AsyncMock()
        emit_mock = AsyncMock()
        with patch("plughub_evaluation_api.router._db.get_instance", new=AsyncMock(return_value=_make_instance())), \
             patch("plughub_evaluation_api.router._db.create_result", new=AsyncMock(return_value=result)), \
             patch("plughub_evaluation_api.router._db.create_criterion_responses", new=AsyncMock(return_value=[])), \
             patch("plughub_evaluation_api.router._kafka.emit_instance_completed", new=emit_mock):
            async with await _client(_app_with_mocks(MagicMock(), kafka_mock)) as c:
                await c.post("/v1/evaluation/ingest", json={
                    "tenant_id": "t1", "instance_id": "evinstance_abc",
                    "session_id": "sess_001", "campaign_id": "evcampaign_abc",
                    "form_id": "evform_abc", "evaluator_agent_id": "a",
                })
        emit_mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_ingest_comparison_mode(self):
        result = _make_result()
        result["comparison_mode"] = True
        with patch("plughub_evaluation_api.router._db.get_instance", new=AsyncMock(return_value=_make_instance())), \
             patch("plughub_evaluation_api.router._db.create_result", new=AsyncMock(return_value=result)), \
             patch("plughub_evaluation_api.router._db.create_criterion_responses", new=AsyncMock(return_value=[])), \
             patch("plughub_evaluation_api.router._kafka.emit_instance_completed", new=AsyncMock()):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.post("/v1/evaluation/ingest", json={
                    "tenant_id": "t1", "instance_id": "evinstance_abc",
                    "session_id": "sess_001", "campaign_id": "evcampaign_abc",
                    "form_id": "evform_abc", "evaluator_agent_id": "a",
                    "comparison_mode": True,
                    "comparison_report": {"jaccard_avg": 0.85},
                })
        assert resp.status_code == 201


class TestResults:
    @pytest.mark.asyncio
    async def test_list_results(self):
        with patch("plughub_evaluation_api.router._db.list_results", new=AsyncMock(return_value=[_make_result()])):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.get("/v1/evaluation/results?tenant_id=t1")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    @pytest.mark.asyncio
    async def test_get_result_not_found(self):
        with patch("plughub_evaluation_api.router._db.get_result", new=AsyncMock(return_value=None)):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.get("/v1/evaluation/results/missing?tenant_id=t1")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_criteria(self):
        responses = [{"id": "evcrr_001", "criterion_id": "crit_1", "score": 8.0}]
        with patch("plughub_evaluation_api.router._db.list_criterion_responses", new=AsyncMock(return_value=responses)):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.get("/v1/evaluation/results/evresult_abc/criteria?tenant_id=t1")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    # ── Contrato do review mudou para o do Arc 13 (corrigido 2026-08-03) ──────────
    # Era `{reviewer_agent_id, reviewer_outcome}`; hoje `ReviewBody` é
    # `{decision, round, review_note}` (`router.py:2000-2003`) e o handler exige, nesta
    # ordem: JWT (`_decode_jwt`) → `decision` ∈ {approved, rejected} → resultado existe →
    # **anti-replay `round == result.current_round`** → ABAC `revisar` no pool da campanha.
    # Os testes antigos morriam em 422 no PRIMEIRO portão (validação do corpo), sem
    # nunca alcançar nenhuma dessas regras — inclusive o anti-replay, que é o motivo de
    # o campo `round` existir (CLAUDE.md § Arc 6: *"must match `result.current_round` or 409"*).

    _REVIEWER = {"Authorization": "Bearer " + pyjwt.encode(
        {"sub": "u_reviewer", "module_config": {"evaluation": {
            "revisar": {"access": "read_write", "scope": []},
        }}},
        settings.jwt_secret, algorithm="HS256",
    )}

    @pytest.mark.asyncio
    async def test_review_result(self):
        reviewed = _make_result(eval_status="reviewed")
        reviewed["reviewer_outcome"] = "approved"
        current = _make_result()
        current["current_round"] = 1
        with patch("plughub_evaluation_api.router._db.get_result", new=AsyncMock(return_value=current)), \
             patch("plughub_evaluation_api.router._db.get_campaign", new=AsyncMock(return_value=_make_campaign())), \
             patch("plughub_evaluation_api.router._db.update_result", new=AsyncMock(return_value=reviewed)):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.post("/v1/evaluation/results/evresult_abc/review?tenant_id=t1",
                                    json={"decision": "approved", "round": 1},
                                    headers=self._REVIEWER)
        assert resp.status_code == 200
        assert resp.json()["reviewer_outcome"] == "approved"

    @pytest.mark.asyncio
    async def test_review_invalid_decision_is_400(self):
        """400 (regra de negócio), não 422: `decision` é `str` livre no schema.

        O nome antigo era `test_review_invalid_outcome` e ele reprovava com 422 — não
        porque o valor fosse recusado, mas porque o CORPO INTEIRO era de outra época.
        Confundir os dois status apagaria a distinção que importa: 422 = não entendi o
        pedido; 400 = entendi e o valor é inválido. O ramo do 400 (`router.py:2206`)
        continua vivo e agora é exercitado de fato.
        """
        async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
            resp = await c.post("/v1/evaluation/results/evresult_abc/review?tenant_id=t1",
                                json={"decision": "invalid_value", "round": 1},
                                headers=self._REVIEWER)
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_review_stale_round_is_409(self):
        """Anti-replay, que nenhum teste cobria: round diferente do corrente → 409."""
        current = _make_result()
        current["current_round"] = 2
        with patch("plughub_evaluation_api.router._db.get_result", new=AsyncMock(return_value=current)), \
             patch("plughub_evaluation_api.router._db.update_result", new=AsyncMock()) as upd:
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.post("/v1/evaluation/results/evresult_abc/review?tenant_id=t1",
                                    json={"decision": "approved", "round": 1},
                                    headers=self._REVIEWER)
        assert resp.status_code == 409
        upd.assert_not_called()   # a decisão obsoleta NÃO pode ter sido persistida

    @pytest.mark.asyncio
    async def test_lock_result(self):
        """O lock usa `_db.lock_result`, não `update_result`.

        O teste patchava `update_result` — função que este handler **não chama**. Com o
        dublê antigo o mock devolvia algo para qualquer coisa e o teste ficava verde sem
        tocar no caminho real; com o conn honesto ele passou a devolver 404, o que
        finalmente expôs a divergência. O patch de `get_result` que tentei antes também
        era palpite: o handler consulta `get_result_by_id`, e só para DISTINGUIR
        "não existe" de "já travado" quando o lock devolve None.
        """
        locked = _make_result(eval_status="locked")
        with patch("plughub_evaluation_api.router._db.lock_result", new=AsyncMock(return_value=locked)):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.post("/v1/evaluation/results/evresult_abc/lock?tenant_id=t1",
                                    json={"locked_by": "admin"})
        assert resp.status_code == 200
        assert resp.json()["eval_status"] == "locked"

    @pytest.mark.asyncio
    async def test_lock_already_locked_is_409(self):
        """Idempotência para retry de workflow — contrato documentado e sem teste.

        `lock_result` filtra `eval_status != 'locked'` no WHERE, então None é ambíguo:
        inexistente ou já travado. Os dois status precisam ser afirmados separadamente,
        senão a distinção que o handler faz de propósito não é verificada por ninguém.
        """
        with patch("plughub_evaluation_api.router._db.lock_result", new=AsyncMock(return_value=None)), \
             patch("plughub_evaluation_api.router._db.get_result_by_id",
                   new=AsyncMock(return_value=_make_result(eval_status="locked"))):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.post("/v1/evaluation/results/evresult_abc/lock?tenant_id=t1",
                                    json={"locked_by": "admin"})
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_lock_missing_result_is_404(self):
        with patch("plughub_evaluation_api.router._db.lock_result", new=AsyncMock(return_value=None)), \
             patch("plughub_evaluation_api.router._db.get_result_by_id", new=AsyncMock(return_value=None)):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.post("/v1/evaluation/results/missing/lock?tenant_id=t1",
                                    json={"locked_by": "admin"})
        assert resp.status_code == 404


class TestContestations:
    # A rota exige JWT (`_decode_jwt`) e ABAC `evaluation.contestar` no pool da campanha
    # (`router.py:2327` e `:2347`). Sem Bearer o handler devolve 401 ANTES de qualquer
    # regra — era o que os dois testes abaixo mediam sem saber. Note que `contested_by`
    # deixou de vir do corpo: a identidade é o `sub` do token, não algo que o chamador
    # se autoatribui. O teste antigo mandava `"contested_by": "agent_human_001"` e o
    # campo era simplesmente ignorado.
    _CONTESTER = {"Authorization": "Bearer " + pyjwt.encode(
        {"sub": "u_humano", "module_config": {"evaluation": {
            "contestar": {"access": "read_write", "scope": []},
        }}},
        settings.jwt_secret, algorithm="HS256",
    )}

    @pytest.mark.asyncio
    async def test_create_contestation(self):
        contest = _make_contestation()
        current = _make_result()
        current["current_round"] = 1
        with patch("plughub_evaluation_api.router._db.get_result", new=AsyncMock(return_value=current)), \
             patch("plughub_evaluation_api.router._db.get_campaign", new=AsyncMock(return_value=_make_campaign())), \
             patch("plughub_evaluation_api.router._db.create_contestation", new=AsyncMock(return_value=contest)), \
             patch("plughub_evaluation_api.router._kafka.emit_contestation_opened", new=AsyncMock()):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.post("/v1/evaluation/contestations", json={
                    "tenant_id": "t1", "result_id": "evresult_abc",
                    "instance_id": "evinstance_abc", "session_id": "sess_001",
                    "contestation_reason": "Score too low", "round": 1,
                }, headers=self._CONTESTER)
        assert resp.status_code == 201
        assert resp.json()["id"] == "evcontest_abc"

    @pytest.mark.asyncio
    async def test_contestation_without_bearer_is_401(self):
        async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
            resp = await c.post("/v1/evaluation/contestations", json={
                "tenant_id": "t1", "result_id": "evresult_abc",
                "instance_id": "evinstance_abc", "session_id": "sess_001",
            })
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_contestation_without_the_grant_is_403(self):
        """Bearer válido sem `contestar` → 403. Distinção que o 401 sozinho apagava."""
        tok = pyjwt.encode({"sub": "u", "module_config": {"evaluation": {
            "formularios": {"access": "read_write", "scope": []},
        }}}, settings.jwt_secret, algorithm="HS256")
        current = _make_result()
        current["current_round"] = 1
        with patch("plughub_evaluation_api.router._db.get_result", new=AsyncMock(return_value=current)), \
             patch("plughub_evaluation_api.router._db.get_campaign", new=AsyncMock(return_value=_make_campaign())):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.post("/v1/evaluation/contestations", json={
                    "tenant_id": "t1", "result_id": "evresult_abc",
                    "instance_id": "evinstance_abc", "session_id": "sess_001",
                    "round": 1,
                }, headers={"Authorization": f"Bearer {tok}"})
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_cannot_contest_locked_result(self):
        locked = _make_result(eval_status="locked")
        with patch("plughub_evaluation_api.router._db.get_result", new=AsyncMock(return_value=locked)):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.post("/v1/evaluation/contestations", json={
                    "tenant_id": "t1", "result_id": "evresult_abc",
                    "instance_id": "evinstance_abc", "session_id": "sess_001",
                }, headers=self._CONTESTER)
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_list_contestations(self):
        with patch("plughub_evaluation_api.router._db.list_contestations",
                   new=AsyncMock(return_value=[_make_contestation()])):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.get("/v1/evaluation/contestations?tenant_id=t1")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    @pytest.mark.asyncio
    async def test_adjudicate_contestation(self):
        adj = _make_contestation(status="accepted")
        adj["result_id"] = "evresult_abc"
        pool_mock = MagicMock()
        pool_mock.acquire = MagicMock(return_value=MagicMock(
            __aenter__=AsyncMock(return_value=MagicMock(
                fetchrow=AsyncMock(return_value=adj)
            )),
            __aexit__=AsyncMock(return_value=None),
        ))
        with patch("plughub_evaluation_api.router._db.adjudicate_contestation", new=AsyncMock(return_value=adj)), \
             patch("plughub_evaluation_api.router._kafka.emit_contestation_closed", new=AsyncMock()):
            async with await _client(_app_with_mocks(pool_mock, AsyncMock())) as c:
                resp = await c.post("/v1/evaluation/contestations/evcontest_abc/adjudicate?tenant_id=t1", json={
                    "adjudicated_by": "supervisor_001",
                    "status": "accepted",
                })
        assert resp.status_code == 200
        assert resp.json()["status"] == "accepted"

    @pytest.mark.asyncio
    async def test_adjudicate_invalid_status(self):
        async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
            resp = await c.post("/v1/evaluation/contestations/evcontest_abc/adjudicate?tenant_id=t1", json={
                "adjudicated_by": "sup", "status": "bad_status",
            })
        assert resp.status_code == 400


class TestSampleCheck:
    @pytest.mark.asyncio
    async def test_should_sample_true(self):
        camp = _make_campaign()
        camp["sampling_rules"] = {"mode": "all"}
        with patch("plughub_evaluation_api.router._db.get_campaign", new=AsyncMock(return_value=camp)):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.post("/v1/evaluation/sample", json={
                    "tenant_id": "t1", "campaign_id": "evcampaign_abc",
                    "session_id": "sess_001", "session_meta": {},
                })
        assert resp.status_code == 200
        assert resp.json()["should_sample"] is True

    @pytest.mark.asyncio
    async def test_paused_campaign_not_sampled(self):
        with patch("plughub_evaluation_api.router._db.get_campaign",
                   new=AsyncMock(return_value=_make_campaign(status="paused"))):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.post("/v1/evaluation/sample", json={
                    "tenant_id": "t1", "campaign_id": "evcampaign_abc", "session_id": "s",
                })
        assert resp.status_code == 200
        assert resp.json()["should_sample"] is False

    @pytest.mark.asyncio
    async def test_campaign_not_found_404(self):
        with patch("plughub_evaluation_api.router._db.get_campaign", new=AsyncMock(return_value=None)):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.post("/v1/evaluation/sample", json={
                    "tenant_id": "t1", "campaign_id": "missing", "session_id": "s",
                })
        assert resp.status_code == 404


class TestReports:
    @pytest.mark.asyncio
    async def test_campaign_report(self):
        camp = _make_campaign()
        pool_mock = MagicMock()
        conn_mock = AsyncMock()
        conn_mock.fetch = AsyncMock(return_value=[])
        ctx_mock = MagicMock(__aenter__=AsyncMock(return_value=conn_mock), __aexit__=AsyncMock(return_value=None))
        pool_mock.acquire = MagicMock(return_value=ctx_mock)
        with patch("plughub_evaluation_api.router._db.get_campaign", new=AsyncMock(return_value=camp)):
            async with await _client(_app_with_mocks(pool_mock, AsyncMock())) as c:
                resp = await c.get("/v1/evaluation/reports/campaign/evcampaign_abc?tenant_id=t1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["campaign"]["id"] == "evcampaign_abc"
        assert "status_breakdown" in data
        assert "criteria_breakdown" in data

    @pytest.mark.asyncio
    async def test_campaign_report_not_found(self):
        with patch("plughub_evaluation_api.router._db.get_campaign", new=AsyncMock(return_value=None)):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.get("/v1/evaluation/reports/campaign/missing?tenant_id=t1")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_agent_report(self):
        pool_mock = MagicMock()
        conn_mock = AsyncMock()
        conn_mock.fetch = AsyncMock(return_value=[])
        ctx_mock = MagicMock(__aenter__=AsyncMock(return_value=conn_mock), __aexit__=AsyncMock(return_value=None))
        pool_mock.acquire = MagicMock(return_value=ctx_mock)
        async with await _client(_app_with_mocks(pool_mock, AsyncMock())) as c:
            resp = await c.get("/v1/evaluation/reports/agent?tenant_id=t1")
        assert resp.status_code == 200
        assert "agents" in resp.json()
