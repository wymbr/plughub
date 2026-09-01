"""
test_close_root_session_id.py — Journey J1.

Cobre _resolve_close_root_session_id: carimba a raiz transitiva na linha de
fechamento (a que sobrevive no ReplacingMergeTree do analytics), lendo
core.contact.root_session_id do ContextStore; fallback = session_id (própria raiz)
quando ausente/ilegível.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

import plughub_orchestrator_bridge.main as bridge_mod


def _ctx_entry(value: str) -> str:
    return json.dumps({
        "value": value, "confidence": 1.0, "source": "webhook_trigger",
        "visibility": "agents_only", "updated_at": "2026-07-09T00:00:00Z",
    })


@pytest.mark.asyncio
async def test_reads_transitive_root_from_ctx():
    r = AsyncMock()
    r.hget = AsyncMock(return_value=_ctx_entry("W1-root"))
    out = await bridge_mod._resolve_close_root_session_id(r, "t", "W2-child")
    assert out == "W1-root"
    r.hget.assert_awaited_once_with("t:ctx:W2-child", "core.contact.root_session_id")


@pytest.mark.asyncio
async def test_fallback_to_self_when_absent():
    r = AsyncMock()
    r.hget = AsyncMock(return_value=None)
    out = await bridge_mod._resolve_close_root_session_id(r, "t", "S")
    assert out == "S"  # top-level session is its own root


@pytest.mark.asyncio
async def test_fallback_to_self_on_empty_value():
    r = AsyncMock()
    r.hget = AsyncMock(return_value=_ctx_entry("   "))
    assert await bridge_mod._resolve_close_root_session_id(r, "t", "S") == "S"


@pytest.mark.asyncio
async def test_fallback_to_self_on_malformed_json():
    r = AsyncMock()
    r.hget = AsyncMock(return_value="not-json{")
    assert await bridge_mod._resolve_close_root_session_id(r, "t", "S") == "S"


@pytest.mark.asyncio
async def test_handles_bytes_and_bare_value():
    r = AsyncMock()
    r.hget = AsyncMock(return_value=_ctx_entry("W1-bytes").encode())
    assert await bridge_mod._resolve_close_root_session_id(r, "t", "S") == "W1-bytes"
    r.hget = AsyncMock(return_value=json.dumps("W1-bare"))
    assert await bridge_mod._resolve_close_root_session_id(r, "t", "S") == "W1-bare"


@pytest.mark.asyncio
async def test_no_tenant_returns_self_without_redis_call():
    r = AsyncMock()
    r.hget = AsyncMock()
    out = await bridge_mod._resolve_close_root_session_id(r, "", "S")
    assert out == "S"
    r.hget.assert_not_awaited()
