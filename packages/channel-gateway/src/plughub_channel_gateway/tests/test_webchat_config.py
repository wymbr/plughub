"""
test_webchat_config.py
Unit tests for WebchatConfigCache (config-http-propagation arc).

The cache holds the Config API 'webchat' namespace for the deployment tenant,
loaded via HTTP and refreshed on config.changed. get() resolves: cached value →
built-in _DEFAULTS → passed default.
"""
import pytest

from ..webchat_config import WebchatConfigCache, _DEFAULTS


def test_get_returns_cached_value_over_defaults():
    c = WebchatConfigCache()
    c._data = {"auth_timeout_s": 45}
    assert c.get("auth_timeout_s", 99) == 45


def test_get_falls_back_to_builtin_default():
    c = WebchatConfigCache()
    assert c.get("auth_timeout_s", 99) == _DEFAULTS["auth_timeout_s"]
    assert c.get("attachment_expiry_days", 99) == _DEFAULTS["attachment_expiry_days"]


def test_get_unknown_key_uses_passed_default():
    c = WebchatConfigCache()
    assert c.get("nonexistent_key", 7) == 7


def test_starts_stale_and_invalidate_marks_stale():
    c = WebchatConfigCache()
    assert c.is_stale is True
    c._invalidated = False
    assert c.is_stale is False
    c.invalidate()
    assert c.is_stale is True


@pytest.mark.asyncio
async def test_reload_unreachable_api_keeps_defaults():
    c = WebchatConfigCache()
    # Unroutable URL → reload swallows the error and leaves the cache usable.
    await c.reload("http://127.0.0.1:1", "tenant_demo")
    assert c.is_stale is True
    assert c.get("auth_timeout_s", 30) == _DEFAULTS["auth_timeout_s"]
