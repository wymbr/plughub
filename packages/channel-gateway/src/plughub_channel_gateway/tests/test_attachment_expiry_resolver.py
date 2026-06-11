"""
test_attachment_expiry_resolver.py
Unit tests for resolve_attachment_expiry_days (config-consolidation F1.2).

Invariante "Configuration — Single Source": config-api vence — o valor efetivo
vem de {tenant}:config:webchat:attachment_expiry_days (Redis, escrito pelo
config-api), com fallback ao default (settings) quando ausente.
"""
import pytest

from ..attachment_store import resolve_attachment_expiry_days


class _FakeRedis:
    def __init__(self, val):
        self._val = val

    async def get(self, key):  # noqa: ANN001
        if isinstance(self._val, Exception):
            raise self._val
        return self._val


@pytest.mark.asyncio
async def test_reads_config_api_value_over_default():
    # config-api vence: 3 (Redis) sobrepõe o default 30.
    assert await resolve_attachment_expiry_days(_FakeRedis("3"), "tenant_demo", 30) == 3


@pytest.mark.asyncio
async def test_falls_back_to_default_when_key_absent():
    assert await resolve_attachment_expiry_days(_FakeRedis(None), "tenant_demo", 30) == 30


@pytest.mark.asyncio
async def test_no_tenant_or_no_redis_returns_default():
    assert await resolve_attachment_expiry_days(_FakeRedis("5"), "", 30) == 30
    assert await resolve_attachment_expiry_days(None, "tenant_demo", 30) == 30


@pytest.mark.asyncio
async def test_redis_error_returns_default():
    assert await resolve_attachment_expiry_days(
        _FakeRedis(RuntimeError("redis down")), "tenant_demo", 30
    ) == 30


@pytest.mark.asyncio
async def test_decodes_bytes_value():
    assert await resolve_attachment_expiry_days(_FakeRedis(b"14"), "tenant_demo", 30) == 14
