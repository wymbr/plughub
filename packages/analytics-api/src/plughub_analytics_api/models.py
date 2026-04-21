"""
models.py
Event parsers for each Kafka topic consumed by the Analytics API.

Each parser returns a normalised dict ready for ClickHouse insertion.
Returns None for events that should be skipped (unknown type, wrong format).

Topics consumed:
  conversations.inbound      → sessions (initial record)
  conversations.routed       → sessions (pool_id update) + agent_events (routing)
  conversations.queued       → sessions (queued) + queue_events
  conversations.events       → sessions (contact_open / contact_closed) + messages
  agent.lifecycle            → agent_events (agent_done)
  usage.events               → usage_events (passthrough)
  sentiment.updated          → sentiment_events (passthrough)
  queue.position_updated     → queue_events (position update)
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _gen_id() -> str:
    return str(uuid.uuid4())


# ─── conversations.inbound ────────────────────────────────────────────────────

def parse_inbound(payload: dict[str, Any]) -> dict | None:
    """
    Creates an initial sessions row when the contact first arrives.
    The row will be replaced (ReplacingMergeTree) when contact_closed fires.
    """
    session_id = payload.get("session_id")
    tenant_id  = payload.get("tenant_id")
    if not session_id or not tenant_id:
        return None

    return {
        "table":      "sessions",
        "session_id": session_id,
        "tenant_id":  tenant_id,
        "channel":    payload.get("channel", ""),
        "pool_id":    payload.get("pool_id") or "",
        "opened_at":  payload.get("started_at") or _now(),
        "closed_at":  None,
        "close_reason": None,
        "outcome":    None,
        "wait_time_ms":   None,
        "handle_time_ms": None,
        "timestamp":  payload.get("started_at") or _now(),
    }


# ─── conversations.routed ─────────────────────────────────────────────────────

def parse_routed(payload: dict[str, Any]) -> list[dict] | None:
    """
    Returns up to two rows:
      - sessions upsert (with pool_id from routing result)
      - agent_events (routing event)
    """
    session_id = payload.get("session_id")
    tenant_id  = payload.get("tenant_id")
    if not session_id or not tenant_id:
        return None

    result     = payload.get("result") or {}
    pool_id    = result.get("pool_id") or ""
    instance_id = result.get("instance_id") or ""
    routed_at  = payload.get("routed_at") or _now()

    rows: list[dict] = [
        # sessions — update with pool_id
        {
            "table":      "sessions",
            "session_id": session_id,
            "tenant_id":  tenant_id,
            "channel":    "",           # already set by inbound; keep empty for merge
            "pool_id":    pool_id,
            "opened_at":  routed_at,
            "timestamp":  routed_at,
        },
        # agent_events — routing entry
        {
            "table":        "agent_events",
            "event_id":     _gen_id(),
            "tenant_id":    tenant_id,
            "session_id":   session_id,
            "agent_type_id": result.get("agent_type_id") or "",
            "pool_id":      pool_id,
            "instance_id":  instance_id,
            "event_type":   "routed",
            "outcome":      None,
            "handoff_reason": None,
            "handle_time_ms": None,
            "routing_mode": result.get("routing_mode"),
            "timestamp":    routed_at,
        },
    ]
    return rows


# ─── conversations.queued ─────────────────────────────────────────────────────

def parse_queued(payload: dict[str, Any]) -> list[dict] | None:
    """
    Returns:
      - sessions upsert (pool_id from routing result)
      - queue_events (queued)
    """
    session_id = payload.get("session_id")
    tenant_id  = payload.get("tenant_id")
    if not session_id or not tenant_id:
        return None

    result   = payload.get("result") or {}
    pool_id  = result.get("pool_id") or ""
    queued_at = payload.get("routed_at") or _now()

    rows: list[dict] = [
        {
            "table":      "sessions",
            "session_id": session_id,
            "tenant_id":  tenant_id,
            "channel":    "",
            "pool_id":    pool_id,
            "opened_at":  queued_at,
            "timestamp":  queued_at,
        },
        {
            "table":           "queue_events",
            "event_id":        _gen_id(),
            "tenant_id":       tenant_id,
            "session_id":      session_id,
            "pool_id":         pool_id,
            "event_type":      "queued",
            "queue_position":  None,
            "estimated_wait_ms": None,
            "available_agents":  None,
            "timestamp":       queued_at,
        },
    ]
    return rows


# ─── conversations.events ─────────────────────────────────────────────────────

def parse_conversations_event(payload: dict[str, Any]) -> list[dict] | None:
    """
    Handles the multi-type conversations.events topic.
    Recognised event_type values:
      contact_open    → sessions upsert
      contact_closed  → sessions upsert (with closed_at, close_reason, outcome)
      message_sent    → messages insert
    All others are silently skipped.
    """
    event_type = payload.get("event_type") or payload.get("type")
    session_id = payload.get("session_id")
    tenant_id  = payload.get("tenant_id")

    if not session_id or not tenant_id:
        return None

    if event_type == "contact_open":
        return [
            {
                "table":      "sessions",
                "session_id": session_id,
                "tenant_id":  tenant_id,
                "channel":    payload.get("channel", ""),
                "pool_id":    "",
                "opened_at":  payload.get("started_at") or payload.get("timestamp") or _now(),
                "timestamp":  payload.get("started_at") or payload.get("timestamp") or _now(),
            }
        ]

    if event_type == "contact_closed":
        started_at = payload.get("started_at")
        ended_at   = payload.get("ended_at") or _now()
        return [
            {
                "table":        "sessions",
                "session_id":   session_id,
                "tenant_id":    tenant_id,
                "channel":      payload.get("channel", ""),
                "pool_id":      "",
                "opened_at":    started_at or ended_at,
                "closed_at":    ended_at,
                "close_reason": payload.get("reason") or payload.get("close_reason"),
                "outcome":      payload.get("outcome"),
                "timestamp":    ended_at,
            }
        ]

    if event_type == "message_sent":
        return [
            {
                "table":        "messages",
                "message_id":   payload.get("message_id") or _gen_id(),
                "tenant_id":    tenant_id,
                "session_id":   session_id,
                "author_role":  payload.get("author_role") or payload.get("role", ""),
                "channel":      payload.get("channel", ""),
                "content_type": payload.get("content_type") or "",
                "visibility":   payload.get("visibility") or "all",
                "timestamp":    payload.get("timestamp") or _now(),
            }
        ]

    # Unknown/untracked event type — skip silently
    return None


# ─── agent.lifecycle ──────────────────────────────────────────────────────────

def parse_agent_lifecycle(payload: dict[str, Any]) -> dict | None:
    """
    Handles agent.lifecycle events.
    Only agent_done is relevant for analytics — other events are skipped.
    """
    event = payload.get("event", "")
    if event != "agent_done":
        return None

    session_id = payload.get("session_id")
    tenant_id  = payload.get("tenant_id")
    if not session_id or not tenant_id:
        return None

    return {
        "table":         "agent_events",
        "event_id":      _gen_id(),
        "tenant_id":     tenant_id,
        "session_id":    session_id,
        "agent_type_id": payload.get("agent_type_id") or "",
        "pool_id":       payload.get("pool_id") or "",
        "instance_id":   payload.get("instance_id") or "",
        "event_type":    "agent_done",
        "outcome":       payload.get("outcome"),
        "handoff_reason": payload.get("handoff_reason"),
        "handle_time_ms": payload.get("handle_time_ms"),
        "routing_mode":  None,
        "timestamp":     payload.get("timestamp") or _now(),
    }


# ─── usage.events ─────────────────────────────────────────────────────────────

def parse_usage_event(payload: dict[str, Any]) -> dict | None:
    """Passthrough from usage.events → usage_events table."""
    event_id  = payload.get("event_id")
    tenant_id = payload.get("tenant_id")
    dimension = payload.get("dimension")
    if not event_id or not tenant_id or not dimension:
        return None

    return {
        "table":            "usage_events",
        "event_id":         event_id,
        "tenant_id":        tenant_id,
        "session_id":       payload.get("session_id") or "",
        "dimension":        dimension,
        "quantity":         int(payload.get("quantity", 1)),
        "source_component": payload.get("source_component") or "",
        "timestamp":        payload.get("timestamp") or _now(),
    }


# ─── sentiment.updated ────────────────────────────────────────────────────────

def parse_sentiment_event(payload: dict[str, Any]) -> dict | None:
    """Passthrough from sentiment.updated → sentiment_events table."""
    event_id  = payload.get("event_id")
    tenant_id = payload.get("tenant_id")
    session_id = payload.get("session_id")
    if not event_id or not tenant_id or not session_id:
        return None

    return {
        "table":      "sentiment_events",
        "event_id":   event_id,
        "tenant_id":  tenant_id,
        "session_id": session_id,
        "pool_id":    payload.get("pool_id") or "",
        "score":      float(payload.get("score", 0.0)),
        "category":   payload.get("category") or "neutral",
        "timestamp":  payload.get("timestamp") or _now(),
    }


# ─── queue.position_updated ───────────────────────────────────────────────────

def parse_queue_position(payload: dict[str, Any]) -> dict | None:
    """Maps queue.position_updated events → queue_events table."""
    session_id = payload.get("session_id")
    tenant_id  = payload.get("tenant_id")
    if not session_id or not tenant_id:
        return None

    return {
        "table":             "queue_events",
        "event_id":          _gen_id(),
        "tenant_id":         tenant_id,
        "session_id":        session_id,
        "pool_id":           payload.get("pool_id") or "",
        "event_type":        "position_updated",
        "queue_position":    payload.get("queue_length"),
        "estimated_wait_ms": payload.get("estimated_wait_ms"),
        "available_agents":  payload.get("available_agents"),
        "timestamp":         payload.get("published_at") or _now(),
    }
