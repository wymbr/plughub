"""
test_journey_root.py — Journey J1.

Cobre _enrich_session_root: sobrescreve root_session_id (+ journey_id cache) de
uma linha `sessions` com o valor AUTORITATIVO do ContextStore
(session.root_session_id), imunizando contra o clobber do ReplacingMergeTree por
writers que não carregam a raiz (routed/queued/suspended/closed). Fail-soft:
ctx ausente/ilegível → mantém o valor do parser (self, correto p/ raiz top-level).
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from plughub_analytics_api.consumer import _enrich_session_root


def _ctx_entry(value: str) -> str:
    return json.dumps({"value": value, "confidence": 1.0, "source": "webhook_trigger"})


@pytest.mark.asyncio
async def test_overrides_root_and_journey_from_ctx():
    r = AsyncMock()
    r.hget = AsyncMock(return_value=_ctx_entry("W1-root"))
    row = {"table": "sessions", "tenant_id": "t", "session_id": "W2",
           "root_session_id": "W2", "journey_id": "W2"}
    await _enrich_session_root(row, r)
    assert row["root_session_id"] == "W1-root"
    assert row["journey_id"] == "W1-root"   # cache = root
    r.hget.assert_awaited_once_with("t:ctx:W2", "session.root_session_id")


@pytest.mark.asyncio
async def test_keeps_self_when_ctx_absent():
    r = AsyncMock()
    r.hget = AsyncMock(return_value=None)
    row = {"table": "sessions", "tenant_id": "t", "session_id": "S",
           "root_session_id": "S", "journey_id": "S"}
    await _enrich_session_root(row, r)
    assert row["root_session_id"] == "S"   # top-level: self is correct
    assert row["journey_id"] == "S"


@pytest.mark.asyncio
async def test_failsoft_on_malformed_json():
    r = AsyncMock()
    r.hget = AsyncMock(return_value="not-json{")
    row = {"table": "sessions", "tenant_id": "t", "session_id": "S", "root_session_id": "S"}
    await _enrich_session_root(row, r)
    assert row["root_session_id"] == "S"


@pytest.mark.asyncio
async def test_failsoft_when_redis_raises():
    r = AsyncMock()
    r.hget = AsyncMock(side_effect=RuntimeError("redis down"))
    row = {"table": "sessions", "tenant_id": "t", "session_id": "S", "root_session_id": "S"}
    await _enrich_session_root(row, r)
    assert row["root_session_id"] == "S"


@pytest.mark.asyncio
async def test_handles_bytes_entry():
    r = AsyncMock()
    r.hget = AsyncMock(return_value=_ctx_entry("W1-bytes").encode())
    row = {"table": "sessions", "tenant_id": "t", "session_id": "W2", "root_session_id": "W2"}
    await _enrich_session_root(row, r)
    assert row["root_session_id"] == "W1-bytes"


@pytest.mark.asyncio
async def test_noop_without_tenant_or_session():
    r = AsyncMock()
    r.hget = AsyncMock()
    row = {"table": "sessions", "tenant_id": "", "session_id": "S", "root_session_id": "S"}
    await _enrich_session_root(row, r)
    assert row["root_session_id"] == "S"
    r.hget.assert_not_awaited()
