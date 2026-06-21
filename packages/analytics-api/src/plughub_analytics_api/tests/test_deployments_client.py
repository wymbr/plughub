"""
test_deployments_client.py
Arc 6 Fase 2 (P2-A) — unit tests do cliente do agent-registry para a lente
`deploy` do bench de Agentes.

Cobre:
  - parse de {deployments:[...]} → lista normalizada (deploy_id/version_label/...)
  - header x-tenant-id e URL corretos
  - cache hit por (tenant, skill) dentro do TTL (não bate de novo)
  - degradação graciosa: base_url vazia, erro HTTP/timeout, 404 → []
"""
from __future__ import annotations

import pytest

import plughub_analytics_api.deployments_client as dc
from plughub_analytics_api.deployments_client import fetch_skill_deployments


# ─── Fake httpx.AsyncClient ───────────────────────────────────────────────────

class _FakeResp:
    def __init__(self, payload, status: int = 200):
        self._payload = payload
        self._status = status

    def raise_for_status(self):
        if self._status >= 400:
            raise RuntimeError(f"HTTP {self._status}")

    def json(self):
        return self._payload


class _FakeClient:
    """Captura a chamada e devolve uma resposta programada (ou levanta)."""
    calls: list[dict] = []

    def __init__(self, payload=None, status: int = 200, exc: Exception | None = None):
        self._payload = payload
        self._status = status
        self._exc = exc

    def __call__(self, *args, **kwargs):
        # httpx.AsyncClient(timeout=...) → instância usada como ctx manager
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, params=None, headers=None):
        _FakeClient.calls.append({"url": url, "params": params, "headers": headers})
        if self._exc is not None:
            raise self._exc
        return _FakeResp(self._payload, self._status)


@pytest.fixture(autouse=True)
def _reset():
    dc._cache.clear()
    _FakeClient.calls = []
    yield
    dc._cache.clear()
    _FakeClient.calls = []


def _patch(monkeypatch, **kwargs):
    monkeypatch.setattr(dc.httpx, "AsyncClient", _FakeClient(**kwargs))


# ─── Parse + request shape ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_parses_and_normalizes(monkeypatch):
    payload = {
        "deployments": [
            {"id": "dep_2", "skill_id": "skill_x_v2", "version": "v2",
             "deployed_at": "2026-06-19T10:00:00Z", "deployed_by": "u_ana",
             "pool_ids": ["p1"], "yaml_snapshot": {"big": "ignored"}},
            {"id": "dep_1", "skill_id": "skill_x_v2", "version": "v1",
             "deployed_at": "2026-06-01T08:00:00Z", "deployed_by": "u_bob"},
        ],
        "total": 2,
    }
    _patch(monkeypatch, payload=payload)

    out = await fetch_skill_deployments("http://reg:3300", "tenant_a", "skill_x_v2")

    assert out == [
        {"deploy_id": "dep_2", "skill_id": "skill_x_v2", "version_label": "v2",
         "deployed_at": "2026-06-19T10:00:00Z", "deployed_by": "u_ana"},
        {"deploy_id": "dep_1", "skill_id": "skill_x_v2", "version_label": "v1",
         "deployed_at": "2026-06-01T08:00:00Z", "deployed_by": "u_bob"},
    ]
    call = _FakeClient.calls[0]
    assert call["url"] == "http://reg:3300/v1/skills/skill_x_v2/deployments"
    assert call["headers"] == {"x-tenant-id": "tenant_a"}
    assert call["params"] == {"limit": 200}


@pytest.mark.asyncio
async def test_strips_trailing_slash(monkeypatch):
    _patch(monkeypatch, payload={"deployments": []})
    await fetch_skill_deployments("http://reg:3300/", "t", "skill_y_v1")
    assert _FakeClient.calls[0]["url"] == "http://reg:3300/v1/skills/skill_y_v1/deployments"


# ─── Cache ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cache_hit_avoids_second_call(monkeypatch):
    _patch(monkeypatch, payload={"deployments": [{"id": "d", "version": "v1"}]})
    a = await fetch_skill_deployments("http://reg:3300", "t", "skill_z_v1")
    b = await fetch_skill_deployments("http://reg:3300", "t", "skill_z_v1")
    assert a == b
    assert len(_FakeClient.calls) == 1  # segunda veio do cache


@pytest.mark.asyncio
async def test_cache_keyed_by_tenant_and_skill(monkeypatch):
    _patch(monkeypatch, payload={"deployments": []})
    await fetch_skill_deployments("http://reg:3300", "t1", "skill_a_v1")
    await fetch_skill_deployments("http://reg:3300", "t2", "skill_a_v1")
    await fetch_skill_deployments("http://reg:3300", "t1", "skill_b_v1")
    assert len(_FakeClient.calls) == 3  # chaves distintas


# ─── Degradação graciosa ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_empty_base_url_returns_empty_no_call(monkeypatch):
    _patch(monkeypatch, payload={"deployments": [{"id": "x"}]})
    out = await fetch_skill_deployments("", "t", "skill_a_v1")
    assert out == []
    assert _FakeClient.calls == []


@pytest.mark.asyncio
async def test_empty_skill_id_returns_empty_no_call(monkeypatch):
    _patch(monkeypatch, payload={"deployments": [{"id": "x"}]})
    out = await fetch_skill_deployments("http://reg:3300", "t", "")
    assert out == []
    assert _FakeClient.calls == []


@pytest.mark.asyncio
async def test_http_error_returns_empty(monkeypatch):
    _patch(monkeypatch, status=404)
    out = await fetch_skill_deployments("http://reg:3300", "t", "skill_missing_v1")
    assert out == []


@pytest.mark.asyncio
async def test_timeout_returns_empty(monkeypatch):
    _patch(monkeypatch, exc=TimeoutError("boom"))
    out = await fetch_skill_deployments("http://reg:3300", "t", "skill_a_v1")
    assert out == []


@pytest.mark.asyncio
async def test_missing_deployments_key_returns_empty(monkeypatch):
    _patch(monkeypatch, payload={"total": 0})
    out = await fetch_skill_deployments("http://reg:3300", "t", "skill_a_v1")
    assert out == []
