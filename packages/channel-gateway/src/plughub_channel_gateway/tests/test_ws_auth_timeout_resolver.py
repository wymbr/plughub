"""
test_ws_auth_timeout_resolver.py
Unit tests for resolve_ws_auth_timeout_s (config-consolidation F2-TTL).

Invariante "Configuration — Single Source": config-api vence — o timeout efetivo
do handshake WS vem de {tenant}:config:webchat:auth_timeout_s (Redis, escrito pelo
config-api), com fallback ao default quando ausente. Usado por webchat e webrtc.
"""
import pytest

from ..attachment_store import resolve_ws_auth_timeout_s


class _FakeRedis:
    def __init__(self, val):
        self._val = val

    async def get(self, key):  # noqa: ANN001
        if isinstance(self._val, Exception):
            raise self._val
        return self._val


@pytest.mark.asyncio
async def test_reads_config_api_value_over_default():
    # config-api vence: 45 (Redis) sobrepõe o default 30.
    assert await resolve_ws_auth_timeout_s(_FakeRedis("45"), "tenant_demo", 30) == 45


@pytest.mark.asyncio
async def test_falls_back_to_default_when_key_absent():
    assert await resolve_ws_auth_timeout_s(_FakeRedis(None), "tenant_demo", 30) == 30


@pytest.mark.asyncio
async def test_no_tenant_or_no_redis_returns_default():
    assert await resolve_ws_auth_timeout_s(_FakeRedis("60"), "", 30) == 30
    assert await resolve_ws_auth_timeout_s(None, "tenant_demo", 30) == 30


@pytest.mark.asyncio
async def test_redis_error_returns_default():
    assert await resolve_ws_auth_timeout_s(
        _FakeRedis(RuntimeError("redis down")), "tenant_demo", 30
    ) == 30


@pytest.mark.asyncio
async def test_decodes_bytes_value():
    assert await resolve_ws_auth_timeout_s(_FakeRedis(b"20"), "tenant_demo", 30) == 20
