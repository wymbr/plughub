"""
test_routing_config.py
Tests for RoutingConfigCache and ConfigChangedHandler.
"""

from __future__ import annotations

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from ..routing_config import RoutingConfigCache, _DEFAULTS


# ---------------------------------------------------------------------------
# RoutingConfigCache unit tests
# ---------------------------------------------------------------------------

class TestRoutingConfigCacheDefaults:
    """get() returns built-in defaults when cache is empty."""

    def test_returns_default_for_unknown_key(self):
        cache = RoutingConfigCache()
        assert cache.get("nonexistent_key", "fallback") == "fallback"

    def test_returns_builtin_default_when_not_loaded(self):
        cache = RoutingConfigCache()
        assert cache.get("performance_score_weight") == 0.0

    def test_returns_builtin_default_sla(self):
        cache = RoutingConfigCache()
        assert cache.get("sla_default_ms") == 480_000

    def test_returns_builtin_default_estimated_wait_factor(self):
        cache = RoutingConfigCache()
        assert cache.get("estimated_wait_factor") == 0.7

    def test_starts_invalid(self):
        cache = RoutingConfigCache()
        assert cache.is_stale is True


class TestRoutingConfigCacheInvalidate:
    """invalidate() marks cache stale without clearing existing values."""

    def test_invalidate_marks_stale(self):
        cache = RoutingConfigCache()
        cache._data = {"performance_score_weight": 0.3}
        cache._invalidated = False

        cache.invalidate()

        assert cache.is_stale is True

    def test_invalidate_does_not_clear_data(self):
        """Existing cached values remain readable during reload."""
        cache = RoutingConfigCache()
        cache._data = {"performance_score_weight": 0.3}
        cache._invalidated = False

        cache.invalidate()

        # Data still accessible — stale but not gone
        assert cache.get("performance_score_weight") == 0.3

    def test_idempotent(self):
        cache = RoutingConfigCache()
        cache.invalidate()
        cache.invalidate()
        assert cache.is_stale is True


class TestRoutingConfigCacheReload:
    """reload() fetches from Config API and populates cache."""

    @pytest.mark.asyncio
    async def test_reload_populates_cache(self):
        cache = RoutingConfigCache()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "entries": {
                "performance_score_weight": {"value": 0.3, "tenant_id": "__global__"},
                "sla_default_ms":           {"value": 300_000, "tenant_id": "__global__"},
            }
        }

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        await cache.reload("http://config-api:3600", mock_client)

        assert cache.get("performance_score_weight") == 0.3
        assert cache.get("sla_default_ms") == 300_000
        assert cache.is_stale is False

    @pytest.mark.asyncio
    async def test_reload_handles_flat_response(self):
        """Config API may return flat {key: value} without ConfigEntry envelope."""
        cache = RoutingConfigCache()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "performance_score_weight": 0.5,
        }

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        await cache.reload("http://config-api:3600", mock_client)

        assert cache.get("performance_score_weight") == 0.5
        assert cache.is_stale is False

    @pytest.mark.asyncio
    async def test_reload_on_http_error_keeps_cache_stale(self):
        """Network failures must not crash the routing-engine."""
        cache = RoutingConfigCache()
        cache._data = {"performance_score_weight": 0.2}
        cache._invalidated = False

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("connection refused"))

        await cache.reload("http://config-api:3600", mock_client)

        # Cache stays stale but existing values remain
        assert cache.is_stale is True
        assert cache.get("performance_score_weight") == 0.2

    @pytest.mark.asyncio
    async def test_reload_uses_correct_url(self):
        cache = RoutingConfigCache()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        await cache.reload("http://config-api:3600/", mock_client)

        mock_client.get.assert_called_once_with(
            "http://config-api:3600/config/routing",
            timeout=5.0,
        )


# ---------------------------------------------------------------------------
# ConfigChangedHandler unit tests
# ---------------------------------------------------------------------------

class TestConfigChangedHandler:
    """ConfigChangedHandler invalidates and reloads on routing namespace."""

    @pytest.mark.asyncio
    async def test_routing_namespace_triggers_invalidation(self):
        from ..kafka_listener import ConfigChangedHandler
        from ..routing_config import routing_config as _global_cache

        mock_client = AsyncMock()
        handler = ConfigChangedHandler("http://config-api:3600", mock_client)

        # Patch the module-level singleton to track calls
        with patch("plughub_routing.kafka_listener.routing_config") as mock_cache:
            mock_cache.invalidate = MagicMock()
            mock_cache.reload     = AsyncMock()

            # Need asyncio.create_task to actually run the coroutine in tests
            tasks = []
            with patch("asyncio.create_task", side_effect=lambda coro: tasks.append(coro)):
                await handler.handle({
                    "event":      "config.changed",
                    "namespace":  "routing",
                    "key":        "performance_score_weight",
                    "tenant_id":  "tenant_demo",
                    "operation":  "set",
                    "updated_at": "2026-05-01T00:00:00Z",
                })

            mock_cache.invalidate.assert_called_once()
            assert len(tasks) == 1   # reload task scheduled

    @pytest.mark.asyncio
    async def test_other_namespace_is_ignored(self):
        from ..kafka_listener import ConfigChangedHandler

        mock_client = AsyncMock()
        handler = ConfigChangedHandler("http://config-api:3600", mock_client)

        with patch("plughub_routing.kafka_listener.routing_config") as mock_cache:
            mock_cache.invalidate = MagicMock()

            await handler.handle({
                "event":     "config.changed",
                "namespace": "masking",
                "key":       "authorized_roles",
            })

            mock_cache.invalidate.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_namespace_field_is_ignored(self):
        from ..kafka_listener import ConfigChangedHandler

        mock_client = AsyncMock()
        handler = ConfigChangedHandler("http://config-api:3600", mock_client)

        with patch("plughub_routing.kafka_listener.routing_config") as mock_cache:
            mock_cache.invalidate = MagicMock()

            await handler.handle({"event": "config.changed"})   # no namespace key

            mock_cache.invalidate.assert_not_called()
