"""
test_pools_client.py — E2f: descoberta de pools INTERNOS no agent-registry.

O foco destes testes é a DEGRADAÇÃO, não o caminho feliz. Degradar para conjunto
vazio significa voltar a contar wrap-up como contato — um número errado com cara de
certo. A rede é o "último bom", e ela precisa de teste porque é justamente o
comportamento que ninguém percebe faltando.
"""
from __future__ import annotations

import logging

import pytest

from plughub_analytics_api import pools_client
from plughub_analytics_api.pools_client import fetch_internal_pools, _reset_cache_for_tests

BASE = "http://agent-registry"
TENANT = "tenant_test"


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    """Stand-in de httpx.AsyncClient: devolve payload ou levanta."""

    def __init__(self, payload: dict | None = None, exc: Exception | None = None):
        self._payload = payload
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def get(self, *_args, **_kwargs):
        if self._exc:
            raise self._exc
        return _FakeResponse(self._payload or {})


def _patch(monkeypatch, payload=None, exc=None):
    monkeypatch.setattr(
        pools_client.httpx, "AsyncClient",
        lambda **_kw: _FakeClient(payload, exc),
    )


@pytest.fixture(autouse=True)
def _clean():
    _reset_cache_for_tests()
    yield
    _reset_cache_for_tests()


@pytest.mark.asyncio
async def test_returns_only_internal_pools(monkeypatch):
    _patch(monkeypatch, {"pools": [
        {"pool_id": "retencao_humano",    "purpose": "contact"},
        {"pool_id": "wrapup_detached_ia", "purpose": "internal"},
        {"pool_id": "suporte_ia"},                 # sem purpose = legado ⇒ contact
    ]})
    assert await fetch_internal_pools(BASE, TENANT) == frozenset({"wrapup_detached_ia"})


@pytest.mark.asyncio
async def test_missing_purpose_is_contact_not_internal(monkeypatch):
    """Default não-regressivo: pool antigo (sem a coluna) NUNCA some do relatório."""
    _patch(monkeypatch, {"pools": [{"pool_id": "legado"}]})
    assert await fetch_internal_pools(BASE, TENANT) == frozenset()


@pytest.mark.asyncio
async def test_cache_avoids_second_call(monkeypatch):
    calls = {"n": 0}

    class _Counting(_FakeClient):
        async def get(self, *a, **kw):
            calls["n"] += 1
            return await super().get(*a, **kw)

    monkeypatch.setattr(
        pools_client.httpx, "AsyncClient",
        lambda **_kw: _Counting({"pools": [{"pool_id": "w", "purpose": "internal"}]}),
    )
    await fetch_internal_pools(BASE, TENANT)
    await fetch_internal_pools(BASE, TENANT)
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_failure_reuses_last_good_and_warns(monkeypatch, caplog):
    """A rede: registry cai DEPOIS de um sucesso ⇒ mantém o conjunto conhecido."""
    _patch(monkeypatch, {"pools": [{"pool_id": "wrapup_detached_ia", "purpose": "internal"}]})
    first = await fetch_internal_pools(BASE, TENANT)
    assert first == frozenset({"wrapup_detached_ia"})

    # Expira o cache SEM apagar o "último bom" — é exatamente o estado em que a
    # rede tem de agir (TTL vencido + registry fora).
    pools_client._cache.clear()
    _patch(monkeypatch, exc=RuntimeError("connection refused"))
    with caplog.at_level(logging.WARNING):
        again = await fetch_internal_pools(BASE, TENANT)

    assert again == frozenset({"wrapup_detached_ia"})
    assert "reusando último conjunto" in caplog.text


@pytest.mark.asyncio
async def test_failure_without_last_good_logs_error(monkeypatch, caplog):
    """Sem conjunto conhecido o número VAI sair inflado — tem de ser ERROR, não
    silêncio nem warning morno."""
    _patch(monkeypatch, exc=RuntimeError("boom"))
    with caplog.at_level(logging.ERROR):
        result = await fetch_internal_pools(BASE, TENANT)

    assert result == frozenset()
    assert any(r.levelno >= logging.ERROR for r in caplog.records)
    assert "INFLADAS" in caplog.text


@pytest.mark.asyncio
async def test_empty_base_url_warns(monkeypatch, caplog):
    with caplog.at_level(logging.WARNING):
        result = await fetch_internal_pools("", TENANT)
    assert result == frozenset()
    assert "DISABLED" in caplog.text


@pytest.mark.asyncio
async def test_empty_tenant_short_circuits(monkeypatch):
    _patch(monkeypatch, exc=RuntimeError("nunca deveria ser chamado"))
    assert await fetch_internal_pools(BASE, "") == frozenset()
