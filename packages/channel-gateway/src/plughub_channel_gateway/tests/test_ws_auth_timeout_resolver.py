"""
test_ws_auth_timeout_resolver.py
Unit tests for resolve_ws_auth_timeout_s (config-http-propagation arc).

The resolver now reads from the HTTP-backed WebchatConfigCache (not Redis). Used
by webchat and webrtc for the WS auth handshake timeout.
"""
import pytest

from ..attachment_store import resolve_ws_auth_timeout_s
from .. import webchat_config as wc


@pytest.fixture(autouse=True)
def _reset_cache():
    wc.webchat_config._data = {}
    yield
    wc.webchat_config._data = {}


@pytest.mark.asyncio
async def test_reads_config_cache_value():
    wc.webchat_config._data = {"auth_timeout_s": 45}
    assert await resolve_ws_auth_timeout_s(None, "tenant_demo", 30) == 45


@pytest.mark.asyncio
async def test_falls_back_to_builtin_default_when_absent():
    # Cache empty → WebchatConfigCache._DEFAULTS provides 30.
    assert await resolve_ws_auth_timeout_s(None, "tenant_demo", 30) == 30


@pytest.mark.asyncio
async def test_coerces_string_value():
    wc.webchat_config._data = {"auth_timeout_s": "60"}
    assert await resolve_ws_auth_timeout_s(None, "tenant_demo", 30) == 60
