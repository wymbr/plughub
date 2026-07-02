"""
test_close_customer_id.py — Identity Resolver Fase A · Slice 4.

Cobre _resolve_close_customer_id: prefere o caller.customer_id NATIVO do
ContextStore para carimbar na linha de fechamento (sessions.customer_id),
com fallback ao contact_id efêmero quando não resolvido.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

import plughub_orchestrator_bridge.main as bridge_mod


def _ctx_entry(value: str) -> str:
    return json.dumps({
        "value": value, "confidence": 1.0, "source": "resolve_step",
        "visibility": "agents_only", "updated_at": "2026-07-02T00:00:00Z",
    })


@pytest.mark.asyncio
async def test_prefers_native_customer_id():
    r = AsyncMock()
    r.hget = AsyncMock(return_value=_ctx_entry("cus_native_123"))
    out = await bridge_mod._resolve_close_customer_id(r, "t", "sess", "contact_abc")
    assert out == "cus_native_123"
    r.hget.assert_awaited_once_with("t:ctx:sess", "caller.customer_id")


@pytest.mark.asyncio
async def test_fallback_when_absent():
    r = AsyncMock()
    r.hget = AsyncMock(return_value=None)
    out = await bridge_mod._resolve_close_customer_id(r, "t", "sess", "contact_abc")
    assert out == "contact_abc"


@pytest.mark.asyncio
async def test_fallback_when_empty_value():
    r = AsyncMock()
    r.hget = AsyncMock(return_value=_ctx_entry("   "))
    out = await bridge_mod._resolve_close_customer_id(r, "t", "sess", "contact_abc")
    assert out == "contact_abc"


@pytest.mark.asyncio
async def test_fallback_on_malformed_json():
    r = AsyncMock()
    r.hget = AsyncMock(return_value="not-json{")
    out = await bridge_mod._resolve_close_customer_id(r, "t", "sess", "contact_abc")
    assert out == "contact_abc"


@pytest.mark.asyncio
async def test_handles_bytes_and_bare_value():
    r = AsyncMock()
    # bytes ContextEntry
    r.hget = AsyncMock(return_value=_ctx_entry("cus_bytes").encode())
    assert await bridge_mod._resolve_close_customer_id(r, "t", "s", "fb") == "cus_bytes"
    # bare (non-dict) JSON value
    r.hget = AsyncMock(return_value=json.dumps("cus_bare"))
    assert await bridge_mod._resolve_close_customer_id(r, "t", "s", "fb") == "cus_bare"


@pytest.mark.asyncio
async def test_no_tenant_returns_fallback_without_redis():
    r = AsyncMock()
    r.hget = AsyncMock()
    out = await bridge_mod._resolve_close_customer_id(r, "", "sess", "contact_abc")
    assert out == "contact_abc"
    r.hget.assert_not_awaited()
