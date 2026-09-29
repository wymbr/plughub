"""
test_caller_gate.py — AUT-59: toda rota da evaluation-api exige CHAMADOR, e o tenant do
usuário é o do token.

Medido antes (2026-09-29, `probe_route_anon_sweep.sh`): 35 rotas respondiam a um chamador
anônimo — resultados, transcrição, respostas de pesquisa —, pela borda pública. Estes testes
montam o app REAL (`create_app`, sem lifespan) para que a dependência seja a que o deploy
monta, e não a que um teste declarou.

Ramos afirmados, cada um com o seu código:
  · anônimo                               → 401 (em rota de router E de contestation_router)
  · Bearer sem nenhum campo de evaluation → 403
  · Bearer com grant, tenant próprio      → passa do portão
  · Bearer com tenant alheio (query/header/corpo) → 403 tenant_mismatch
  · token de usuário sem tenant           → 403 user_token_without_tenant
  · X-Service-Token certo                 → passa e escolhe o tenant
  · X-Service-Token com segredo ausente   → 503 service_token_not_configured
  · /health                               → 200 sem credencial (isenção declarada)

"Passa do portão" é medido com uma rota cujo handler dá 404/200 — qualquer código que NÃO
seja 401/403/503 prova que o portão deixou passar. Sem esse ramo positivo, um portão que
recusasse tudo passaria no teste.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import jwt as pyjwt
import pytest
from httpx import ASGITransport, AsyncClient

from ..config import settings
from ..main import create_app

_SVC = "svc-do-teste-aut59"


def _tok(tenant: str | None = "t1", **fields: str) -> dict[str, str]:
    cfg = {f: {"access": a, "scope": []} for f, a in (fields or {"report": "read_only"}).items()}
    payload = {"sub": "u1", "module_config": {"evaluation": cfg}}
    if tenant is not None:
        payload["tenant_id"] = tenant
    return {"Authorization": "Bearer " + pyjwt.encode(payload, settings.jwt_secret, algorithm="HS256")}


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(settings, "service_token", _SVC, raising=False)
    a = create_app()
    conn = MagicMock()
    for m, v in (("fetchrow", None), ("fetch", []), ("fetchval", None), ("execute", "")):
        setattr(conn, m, AsyncMock(return_value=v))
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    db = MagicMock()
    db.acquire = MagicMock(return_value=cm)
    for m, v in (("fetchrow", None), ("fetch", []), ("fetchval", None), ("execute", "")):
        setattr(db, m, AsyncMock(return_value=v))
    a.state.db_pool = db
    a.state.kafka_producer = AsyncMock()
    a.state.redis = MagicMock()
    return a


async def _get(app, path, headers=None, **kw):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return await c.get(path, headers=headers or {}, **kw)


_RESULT = "/v1/evaluation/results/r_inexistente?tenant_id=t1"
_THREADS = "/v1/evaluation/instances/i_inexistente/threads"


@pytest.mark.asyncio
async def test_health_is_the_declared_exemption(app):
    r = await _get(app, "/health")
    assert r.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [_RESULT, _THREADS, "/v1/evaluation/survey/responses?tenant_id=t1"])
async def test_anonymous_is_refused_on_both_routers(app, path):
    r = await _get(app, path)
    assert r.status_code == 401, r.text


@pytest.mark.asyncio
async def test_bearer_without_evaluation_grant_is_403(app):
    r = await _get(app, _RESULT, headers=_tok(report="none"))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_bearer_with_grant_and_own_tenant_passes_the_gate(app):
    with patch("plughub_evaluation_api.router._db.get_result", new=AsyncMock(return_value=None)):
        r = await _get(app, _RESULT, headers=_tok())
    assert r.status_code == 404, r.text  # o HANDLER respondeu: o portão deixou passar


@pytest.mark.asyncio
async def test_query_tenant_of_another_tenant_is_refused(app):
    r = await _get(app, "/v1/evaluation/results/x?tenant_id=outro", headers=_tok("t1"))
    assert r.status_code == 403
    assert r.json()["detail"] == "tenant_mismatch"


@pytest.mark.asyncio
async def test_header_tenant_of_another_tenant_is_refused(app):
    r = await _get(app, _THREADS, headers={**_tok("t1"), "X-Tenant-ID": "outro"})
    assert r.status_code == 403
    assert r.json()["detail"] == "tenant_mismatch"


@pytest.mark.asyncio
async def test_body_tenant_of_another_tenant_is_refused(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/v1/evaluation/forms", headers=_tok("t1", formularios="read_write"),
                         json={"tenant_id": "outro", "name": "x"})
    assert r.status_code == 403
    assert r.json()["detail"] == "tenant_mismatch"


@pytest.mark.asyncio
async def test_user_token_without_tenant_is_refused(app):
    r = await _get(app, _RESULT, headers=_tok(None))
    assert r.status_code == 403
    assert r.json()["detail"] == "user_token_without_tenant"


@pytest.mark.asyncio
async def test_service_passes_and_chooses_the_tenant(app):
    with patch("plughub_evaluation_api.router._db.get_result", new=AsyncMock(return_value=None)):
        r = await _get(app, "/v1/evaluation/results/x?tenant_id=qualquer", headers={"X-Service-Token": _SVC})
    assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_wrong_service_token_without_bearer_is_401(app):
    r = await _get(app, _RESULT, headers={"X-Service-Token": "errado"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_service_header_with_secret_unset_is_503(app, monkeypatch):
    monkeypatch.setattr(settings, "service_token", "", raising=False)
    r = await _get(app, _RESULT, headers={"X-Service-Token": "qualquer"})
    assert r.status_code == 503
    assert r.json()["detail"] == "service_token_not_configured"


@pytest.mark.asyncio
async def test_every_route_but_health_refuses_an_anonymous_caller(app):
    """Censo por HTTP sobre o OpenAPI do app REAL: toda rota menos `/health` responde 401
    a um anônimo, inclusive escrita com corpo inválido (a dependência do router decide
    ANTES da validação do corpo).

    ⚠️ A primeira versão inspecionava `app.routes` atrás de `APIRoute` — e esta versão do
    FastAPI guarda os routers incluídos como `_IncludedRouter`: o censo varria ZERO rotas
    e passava por ausência de amostra (medido com a dependência removida do
    `contestation_router`: vermelho nos testes HTTP, verde no censo). Por isso o piso."""
    import re
    spec = app.openapi()
    abertas, n = [], 0
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        for path, ops in spec["paths"].items():
            for method in ops:
                if method not in {"get", "post", "put", "patch", "delete"} or path == "/health":
                    continue
                n += 1
                url = re.sub(r"\{[^}]+\}", "x", path)
                r = await c.request(method.upper(), url, params={"tenant_id": "t1"}, content='"x"',
                                    headers={"content-type": "application/json"})
                if r.status_code != 401:
                    abertas.append(f"{method.upper()} {path} → {r.status_code}")
    assert n >= 50, f"censo varreu só {n} rotas — o instrumento não está vendo o app"
    assert not abertas, f"rotas que não recusam anônimo: {abertas}"


@pytest.mark.asyncio
async def test_aud03_data_subject_surveys_is_service_only(app):
    """AUD-03: as pesquisas de UMA pessoa (texto livre, verbatims) saem para a
    analytics-api, que confere o DPO. Usuário, mesmo com todo campo de evaluation: não."""
    body = {"tenant_id": "t1", "customer_keys": ["cus_1"], "session_ids": ["s1"]}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/v1/evaluation/data-subject/surveys", json=body,
                         headers=_tok(report="read_write", formularios="read_write"))
        assert r.status_code == 401, r.text   # `_require_service`: sem X-Service-Token
        with patch("plughub_evaluation_api.db.survey_subject_export",
                   new=AsyncMock(return_value=[{"instance_id": "i1"}])) as db:
            r = await c.post("/v1/evaluation/data-subject/surveys", json=body,
                             headers={"X-Service-Token": _SVC})
    assert r.status_code == 200, r.text
    assert r.json() == {"surveys": [{"instance_id": "i1"}]}
    assert db.await_args.kwargs["customer_keys"] == ["cus_1"]
