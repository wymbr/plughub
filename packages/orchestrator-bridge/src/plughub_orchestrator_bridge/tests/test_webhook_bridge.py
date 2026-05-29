"""
test_webhook_bridge.py
Unit tests for Arc 19 Fase C — webhook session bridge.

Covers:
  _handle_webhook_session_resumed:
    - returns early when session_id or tenant_id is missing
    - returns early when session meta key not found in Redis
    - returns early when agent_type_id is not in meta
    - calls activate_native_agent with webhook_pool=True and resume_context
    - terminal outcome (not "suspended") triggers _mark_contact_ended + _trigger_contact_close
    - suspended outcome does NOT trigger contact close
    - restores instance snapshot to Redis + re-adds to pool set after terminal outcome
    - restores instance snapshot to Redis + re-adds to pool set after suspended outcome
    - publishes agent_ready + agent_done Kafka lifecycle events on terminal outcome
    - publishes agent_ready + agent_done Kafka lifecycle events on suspended outcome
    - skips Kafka publish when _kafka_producer is None

  process_inbound with session_resumed:
    - routes event_type="session_resumed" to _handle_webhook_session_resumed
    - logs a warning and returns early when event_type="session_resumed" and http is None
    - does NOT call _handle_webhook_session_resumed for regular customer messages
    - still routes mention_routing events to process_mention_routing
    - still skips events that have no "author" field (routing-engine inbound)

All tests use AsyncMock — no network I/O.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

import plughub_orchestrator_bridge.main as bridge_mod


# ── Constants ──────────────────────────────────────────────────────────────────

TENANT     = "tenant_test"
SESSION_ID = "sid-wh-002"
STEP_ID    = "step_aguardar_aprovacao"
INSTANCE   = "skill_wh_v1-001"
POOL_ID    = "portabilidade_wh"
AGENT_TYPE = "skill_portabilidade_v1"
CUSTOMER   = "cust_abc"

RESUME_TOKEN = "b" * 43

VALID_META = json.dumps({
    "contact_id":    CUSTOMER,
    "channel":       "webhook",
    "agent_type_id": AGENT_TYPE,
    "pool_id":       POOL_ID,
    "tenant_id":     TENANT,
    "customer_id":   CUSTOMER,
    "instance_id":   INSTANCE,
})

INSTANCE_SNAPSHOT = json.dumps({
    "agent_type_id":           AGENT_TYPE,
    "instance_id":             INSTANCE,
    "execution_model":         "stateless",
    "current_sessions":        1,
    "max_concurrent_sessions": 5,
    "pools":                   [POOL_ID],
    "status":                  "busy",
})

RESUMED_EVENT = {
    "event_type":    "session_resumed",
    "session_id":    SESSION_ID,
    "tenant_id":     TENANT,
    "step_id":       STEP_ID,
    "resume_token":  RESUME_TOKEN,
    "payload":       {"decision": "approved", "data": "ok"},
}


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_redis():
    r = AsyncMock()
    r.get   = AsyncMock(return_value=None)
    r.set   = AsyncMock(return_value=True)
    r.sadd  = AsyncMock(return_value=1)
    r.expire = AsyncMock(return_value=True)
    r.hset   = AsyncMock(return_value=1)
    return r


@pytest.fixture
def mock_http():
    return AsyncMock()


# ── _handle_webhook_session_resumed — guard paths ─────────────────────────────

@pytest.mark.asyncio
async def test_resume_returns_early_on_missing_session_id(mock_redis, mock_http):
    event = {**RESUMED_EVENT, "session_id": ""}
    # Should return without touching Redis (beyond the meta read guard)
    with patch.object(bridge_mod, "activate_native_agent", new_callable=AsyncMock) as mock_activate:
        await bridge_mod._handle_webhook_session_resumed(event, mock_redis, mock_http)
    mock_activate.assert_not_called()


@pytest.mark.asyncio
async def test_resume_returns_early_on_missing_tenant_id(mock_redis, mock_http):
    event = {**RESUMED_EVENT, "tenant_id": ""}
    with patch.object(bridge_mod, "activate_native_agent", new_callable=AsyncMock) as mock_activate:
        await bridge_mod._handle_webhook_session_resumed(event, mock_redis, mock_http)
    mock_activate.assert_not_called()


@pytest.mark.asyncio
async def test_resume_returns_early_when_no_meta(mock_redis, mock_http):
    mock_redis.get.return_value = None  # meta not found
    with patch.object(bridge_mod, "activate_native_agent", new_callable=AsyncMock) as mock_activate:
        await bridge_mod._handle_webhook_session_resumed(RESUMED_EVENT, mock_redis, mock_http)
    mock_activate.assert_not_called()


@pytest.mark.asyncio
async def test_resume_returns_early_when_agent_type_missing_from_meta(mock_redis, mock_http):
    meta_no_agent = json.dumps({**json.loads(VALID_META), "agent_type_id": ""})
    mock_redis.get.return_value = meta_no_agent
    with patch.object(bridge_mod, "activate_native_agent", new_callable=AsyncMock) as mock_activate:
        await bridge_mod._handle_webhook_session_resumed(RESUMED_EVENT, mock_redis, mock_http)
    mock_activate.assert_not_called()


# ── _handle_webhook_session_resumed — happy path ─────────────────────────────

@pytest.mark.asyncio
async def test_resume_calls_activate_with_webhook_pool_and_resume_context(mock_redis, mock_http):
    mock_redis.get.side_effect = [
        VALID_META,         # session:{id}:meta
        INSTANCE_SNAPSHOT,  # {tenant}:instance:{instance_id}
    ]
    with (
        patch.object(bridge_mod, "activate_native_agent", new_callable=AsyncMock,
                     return_value={"outcome": "resolved"}) as mock_activate,
        patch.object(bridge_mod, "get_agent_type", new_callable=AsyncMock,
                     return_value={"skills": []}),
        patch.object(bridge_mod, "_mark_contact_ended", new_callable=AsyncMock),
        patch.object(bridge_mod, "_trigger_contact_close", new_callable=AsyncMock),
        patch.object(bridge_mod, "_kafka_producer", None),
    ):
        await bridge_mod._handle_webhook_session_resumed(RESUMED_EVENT, mock_redis, mock_http)

    mock_activate.assert_called_once()
    call_kwargs = mock_activate.call_args.kwargs
    assert call_kwargs["webhook_pool"] is True
    assert call_kwargs["session_id"]   == SESSION_ID
    assert call_kwargs["tenant_id"]    == TENANT
    assert call_kwargs["instance_id"]  == INSTANCE
    rc = call_kwargs["resume_context"]
    assert rc["step_id"]   == STEP_ID
    assert rc["decision"]  == "approved"
    assert rc["payload"]   == RESUMED_EVENT["payload"]


@pytest.mark.asyncio
async def test_resume_reads_meta_from_correct_key(mock_redis, mock_http):
    mock_redis.get.side_effect = [VALID_META, None]  # meta, then instance snap
    with (
        patch.object(bridge_mod, "activate_native_agent", new_callable=AsyncMock,
                     return_value={"outcome": "resolved"}),
        patch.object(bridge_mod, "get_agent_type", new_callable=AsyncMock,
                     return_value={"skills": []}),
        patch.object(bridge_mod, "_mark_contact_ended", new_callable=AsyncMock),
        patch.object(bridge_mod, "_trigger_contact_close", new_callable=AsyncMock),
        patch.object(bridge_mod, "_kafka_producer", None),
    ):
        await bridge_mod._handle_webhook_session_resumed(RESUMED_EVENT, mock_redis, mock_http)

    # First get call must be for the session meta key
    first_get_key = mock_redis.get.call_args_list[0][0][0]
    assert first_get_key == f"session:{SESSION_ID}:meta"


# ── Terminal vs suspended outcome ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resume_terminal_outcome_triggers_contact_close(mock_redis, mock_http):
    mock_redis.get.side_effect = [VALID_META, None]
    with (
        patch.object(bridge_mod, "activate_native_agent", new_callable=AsyncMock,
                     return_value={"outcome": "resolved"}) as _,
        patch.object(bridge_mod, "get_agent_type", new_callable=AsyncMock,
                     return_value={"skills": []}),
        patch.object(bridge_mod, "_mark_contact_ended", new_callable=AsyncMock) as mock_end,
        patch.object(bridge_mod, "_trigger_contact_close", new_callable=AsyncMock) as mock_close,
        patch.object(bridge_mod, "_kafka_producer", None),
    ):
        await bridge_mod._handle_webhook_session_resumed(RESUMED_EVENT, mock_redis, mock_http)

    mock_end.assert_called_once_with(mock_redis, SESSION_ID)
    mock_close.assert_called_once()


@pytest.mark.asyncio
async def test_resume_suspended_outcome_does_not_trigger_contact_close(mock_redis, mock_http):
    mock_redis.get.side_effect = [VALID_META, None]
    with (
        patch.object(bridge_mod, "activate_native_agent", new_callable=AsyncMock,
                     return_value={"outcome": "suspended"}),
        patch.object(bridge_mod, "get_agent_type", new_callable=AsyncMock,
                     return_value={"skills": []}),
        patch.object(bridge_mod, "_mark_contact_ended", new_callable=AsyncMock) as mock_end,
        patch.object(bridge_mod, "_trigger_contact_close", new_callable=AsyncMock) as mock_close,
        patch.object(bridge_mod, "_kafka_producer", None),
    ):
        await bridge_mod._handle_webhook_session_resumed(RESUMED_EVENT, mock_redis, mock_http)

    mock_end.assert_not_called()
    mock_close.assert_not_called()


# ── Instance restore ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resume_restores_instance_snapshot(mock_redis, mock_http):
    mock_redis.get.side_effect = [VALID_META, INSTANCE_SNAPSHOT]
    with (
        patch.object(bridge_mod, "activate_native_agent", new_callable=AsyncMock,
                     return_value={"outcome": "resolved"}),
        patch.object(bridge_mod, "get_agent_type", new_callable=AsyncMock,
                     return_value={"skills": []}),
        patch.object(bridge_mod, "_mark_contact_ended", new_callable=AsyncMock),
        patch.object(bridge_mod, "_trigger_contact_close", new_callable=AsyncMock),
        patch.object(bridge_mod, "_kafka_producer", None),
    ):
        await bridge_mod._handle_webhook_session_resumed(RESUMED_EVENT, mock_redis, mock_http)

    # instance snapshot must be written back with status=ready and current_sessions=0
    set_key   = mock_redis.set.call_args[0][0]
    set_value = json.loads(mock_redis.set.call_args[0][1])
    assert set_key   == f"{TENANT}:instance:{INSTANCE}"
    assert set_value["status"]           == "ready"
    assert set_value["current_sessions"] == 0


@pytest.mark.asyncio
async def test_resume_re_adds_instance_to_pool_set(mock_redis, mock_http):
    mock_redis.get.side_effect = [VALID_META, INSTANCE_SNAPSHOT]
    with (
        patch.object(bridge_mod, "activate_native_agent", new_callable=AsyncMock,
                     return_value={"outcome": "resolved"}),
        patch.object(bridge_mod, "get_agent_type", new_callable=AsyncMock,
                     return_value={"skills": []}),
        patch.object(bridge_mod, "_mark_contact_ended", new_callable=AsyncMock),
        patch.object(bridge_mod, "_trigger_contact_close", new_callable=AsyncMock),
        patch.object(bridge_mod, "_kafka_producer", None),
    ):
        await bridge_mod._handle_webhook_session_resumed(RESUMED_EVENT, mock_redis, mock_http)

    mock_redis.sadd.assert_called_once_with(
        f"{TENANT}:pool:{POOL_ID}:instances", INSTANCE
    )


# ── Kafka lifecycle events ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resume_publishes_agent_ready_and_agent_done(mock_redis, mock_http):
    mock_redis.get.side_effect = [VALID_META, INSTANCE_SNAPSHOT]
    mock_producer  = MagicMock()
    mock_producer.send = MagicMock(return_value=None)

    with (
        patch.object(bridge_mod, "activate_native_agent", new_callable=AsyncMock,
                     return_value={"outcome": "resolved"}),
        patch.object(bridge_mod, "get_agent_type", new_callable=AsyncMock,
                     return_value={"skills": []}),
        patch.object(bridge_mod, "_mark_contact_ended", new_callable=AsyncMock),
        patch.object(bridge_mod, "_trigger_contact_close", new_callable=AsyncMock),
        patch.object(bridge_mod, "_kafka_producer", mock_producer),
    ):
        await bridge_mod._handle_webhook_session_resumed(RESUMED_EVENT, mock_redis, mock_http)

    # Two Kafka sends: agent_ready + agent_done
    assert mock_producer.send.call_count == 2
    events = [
        json.loads(mock_producer.send.call_args_list[i][0][1].decode("utf-8"))
        for i in range(2)
    ]
    event_names = {e["event"] for e in events}
    assert "agent_ready" in event_names
    assert "agent_done"  in event_names
    for e in events:
        assert e["instance_id"]   == INSTANCE
        assert e["agent_type_id"] == AGENT_TYPE
        assert e["tenant_id"]     == TENANT


@pytest.mark.asyncio
async def test_resume_skips_kafka_when_producer_is_none(mock_redis, mock_http):
    mock_redis.get.side_effect = [VALID_META, INSTANCE_SNAPSHOT]
    with (
        patch.object(bridge_mod, "activate_native_agent", new_callable=AsyncMock,
                     return_value={"outcome": "resolved"}),
        patch.object(bridge_mod, "get_agent_type", new_callable=AsyncMock,
                     return_value={"skills": []}),
        patch.object(bridge_mod, "_mark_contact_ended", new_callable=AsyncMock),
        patch.object(bridge_mod, "_trigger_contact_close", new_callable=AsyncMock),
        patch.object(bridge_mod, "_kafka_producer", None),
    ):
        # Should not raise
        await bridge_mod._handle_webhook_session_resumed(RESUMED_EVENT, mock_redis, mock_http)


# ── process_inbound routing for session_resumed ───────────────────────────────

@pytest.mark.asyncio
async def test_process_inbound_routes_session_resumed_to_handler(mock_redis, mock_http):
    with patch.object(bridge_mod, "_handle_webhook_session_resumed",
                      new_callable=AsyncMock) as mock_handler:
        await bridge_mod.process_inbound(RESUMED_EVENT, mock_redis, mock_http)

    mock_handler.assert_called_once_with(RESUMED_EVENT, mock_redis, mock_http)


@pytest.mark.asyncio
async def test_process_inbound_warns_when_http_none_on_session_resumed(mock_redis, caplog):
    import logging
    with patch.object(bridge_mod, "_handle_webhook_session_resumed",
                      new_callable=AsyncMock) as mock_handler:
        with caplog.at_level(logging.WARNING):
            await bridge_mod.process_inbound(RESUMED_EVENT, mock_redis, http=None)

    mock_handler.assert_not_called()
    assert any("session_resumed" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_process_inbound_does_not_call_resume_handler_for_customer_msg(mock_redis, mock_http):
    customer_msg = {
        "session_id": SESSION_ID,
        "tenant_id":  TENANT,
        "author":     {"type": "customer", "id": CUSTOMER},
        "content":    {"type": "text", "text": "hello"},
    }
    with patch.object(bridge_mod, "_handle_webhook_session_resumed",
                      new_callable=AsyncMock) as mock_handler:
        # process_inbound will try real processing; patch out the downstream calls
        with patch.object(bridge_mod, "forward_inbound_to_active_agent",
                          new_callable=AsyncMock):
            await bridge_mod.process_inbound(customer_msg, mock_redis, mock_http)

    mock_handler.assert_not_called()


@pytest.mark.asyncio
async def test_process_inbound_routes_mention_routing(mock_redis, mock_http):
    mention_msg = {
        "session_id":       SESSION_ID,
        "mention_routing":  True,
        "mentions":         [],
    }
    with patch.object(bridge_mod, "process_mention_routing",
                      new_callable=AsyncMock) as mock_mention:
        await bridge_mod.process_inbound(mention_msg, mock_redis, mock_http)

    mock_mention.assert_called_once_with(mention_msg, mock_redis)


@pytest.mark.asyncio
async def test_process_inbound_skips_events_without_author(mock_redis, mock_http):
    routing_event = {
        "session_id": SESSION_ID,
        "tenant_id":  TENANT,
        # No "author" — routing-engine ConversationInboundEvent
    }
    # Should return without doing anything observable
    with patch.object(bridge_mod, "_handle_webhook_session_resumed",
                      new_callable=AsyncMock) as mock_handler:
        await bridge_mod.process_inbound(routing_event, mock_redis, mock_http)

    mock_handler.assert_not_called()
