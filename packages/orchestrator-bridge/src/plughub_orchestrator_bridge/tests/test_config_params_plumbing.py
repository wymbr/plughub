"""
Fatia 1 — deploy config params plumbing (bridge → $.config).

Verifies that the orchestrator-bridge:
  1. captures the `current` slot's config_json into _pool_config_cache
     (get_pool_current_flow), and
  2. injects that config into the /execute payload as `config`
     (activate_native_agent) — only when non-empty.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import plughub_orchestrator_bridge.main as bridge_mod

TENANT = "tenant_test"
POOL   = "survey_multi_ia"
SKILL  = "skill_survey_multi_v1"

_FLOW = {"entry": "a", "steps": [{"id": "a", "type": "complete", "outcome": "resolved"}]}


def _ctx(resp):
    """Wrap a response in an async context manager (async with http.x(...) as resp)."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=resp)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _resp(status, body):
    r = MagicMock()
    r.status = status
    r.json = AsyncMock(return_value=body)
    return r


def _slots(config_json):
    current = {
        "set": True,
        "skill_id": SKILL,
        "yaml_snapshot": _FLOW,
        "set_at": "2026-07-08T00:00:00Z",
    }
    if config_json is not None:
        current["config_json"] = config_json
    return {"slots": {"current": current}}


def _clear():
    bridge_mod._pool_flow_cache.pop(POOL, None)
    bridge_mod._pool_config_cache.pop(POOL, None)
    bridge_mod._pool_deploy_version_cache.pop(POOL, None)


# ── get_pool_current_flow — config capture ───────────────────────────────────

@pytest.mark.asyncio
async def test_captures_config_json_from_current_slot():
    _clear()
    http = AsyncMock()
    http.get = MagicMock(return_value=_ctx(_resp(200, _slots({"form_id": "dialog_x", "max_concurrent_sessions": 3}))))

    result = await bridge_mod.get_pool_current_flow(http, TENANT, POOL)

    assert result is not None
    skill_id, flow = result
    assert skill_id == SKILL and flow == _FLOW
    assert bridge_mod._pool_config_cache[POOL] == {"form_id": "dialog_x", "max_concurrent_sessions": 3}


@pytest.mark.asyncio
async def test_missing_config_json_defaults_to_empty_dict():
    _clear()
    http = AsyncMock()
    http.get = MagicMock(return_value=_ctx(_resp(200, _slots(None))))

    await bridge_mod.get_pool_current_flow(http, TENANT, POOL)

    # Entry present ⟺ pool ran via slot; empty config is a valid (no-param) deploy.
    assert bridge_mod._pool_config_cache[POOL] == {}


# ── activate_native_agent — payload config injection ─────────────────────────

def _capture_post(captured):
    def _post(url, json=None, timeout=None):
        captured["payload"] = json
        return _ctx(_resp(200, {"outcome": "resolved"}))
    return _post


@pytest.mark.asyncio
async def test_injects_config_into_execute_payload():
    _clear()
    bridge_mod._pool_config_cache[POOL] = {"form_id": "dialog_x"}
    captured: dict = {}
    http = AsyncMock()
    http.post = MagicMock(side_effect=_capture_post(captured))
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)

    with patch.object(bridge_mod, "resolve_flow_for_agent", new_callable=AsyncMock,
                      return_value=(SKILL, _FLOW)):
        await bridge_mod.activate_native_agent(
            http=http, redis_client=redis, session_id="sid", customer_id="cus",
            agent_type_id=SKILL, tenant_id=TENANT, skills=[], pool_id=POOL,
        )

    assert captured["payload"].get("config") == {"form_id": "dialog_x"}


@pytest.mark.asyncio
async def test_omits_config_when_no_slot_config():
    _clear()  # cache empty → no config
    captured: dict = {}
    http = AsyncMock()
    http.post = MagicMock(side_effect=_capture_post(captured))
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)

    with patch.object(bridge_mod, "resolve_flow_for_agent", new_callable=AsyncMock,
                      return_value=(SKILL, _FLOW)):
        await bridge_mod.activate_native_agent(
            http=http, redis_client=redis, session_id="sid", customer_id="cus",
            agent_type_id=SKILL, tenant_id=TENANT, skills=[], pool_id=POOL,
        )

    assert "config" not in captured["payload"]
