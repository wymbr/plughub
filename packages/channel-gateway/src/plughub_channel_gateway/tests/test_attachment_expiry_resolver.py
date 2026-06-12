"""
test_attachment_expiry_resolver.py
Unit tests for resolve_attachment_expiry_days (config-http-propagation arc).

The resolver now reads from the HTTP-backed WebchatConfigCache (not Redis). The
cache is populated via GET /config/webchat and refreshed on config.changed; here
we drive it directly via webchat_config._data.
"""
import pytest

from ..attachment_store import resolve_attachment_expiry_days
from .. import webchat_config as wc


@pytest.fixture(autouse=True)
def _reset_cache():
    wc.webchat_config._data = {}
    yield
    wc.webchat_config._data = {}


@pytest.mark.asyncio
async def test_reads_config_cache_value():
    wc.webchat_config._data = {"attachment_expiry_days": 14}
    assert await resolve_attachment_expiry_days(None, "tenant_demo", 30) == 14


@pytest.mark.asyncio
async def test_falls_back_to_builtin_default_when_absent():
    # Cache empty → WebchatConfigCache._DEFAULTS provides 30.
    assert await resolve_attachment_expiry_days(None, "tenant_demo", 30) == 30


@pytest.mark.asyncio
async def test_coerces_string_value():
    wc.webchat_config._data = {"attachment_expiry_days": "7"}
    assert await resolve_attachment_expiry_days(None, "tenant_demo", 30) == 7
