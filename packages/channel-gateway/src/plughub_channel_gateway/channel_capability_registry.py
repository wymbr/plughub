"""
channel_capability_registry.py
Static registry of channel capabilities and capability-based channel selection.

Arc 16 Phase D — Channel Capability Negotiation.

The registry maps each channel name to the set of ChannelCapability values it
supports.  When a collect step omits an explicit channel and supplies a
`requires[]` list instead, the Channel Gateway reads the journey's
`available_channels` list from the ContextStore and selects the best matching
channel.

Spec: docs/arcos/arc16-flow-orchestration.md § Channel Capability Negotiation
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as aioredis

logger = logging.getLogger("plughub.channel-gateway.capability")


# ── Capability sets per channel ───────────────────────────────────────────────
# Keep in sync with ChannelCapabilitySchema in @plughub/schemas/src/skill.ts.
# Values: "text" | "audio" | "video" | "file_upload" | "masked_input" | "rich_menu"

CHANNEL_CAPABILITIES: dict[str, frozenset[str]] = {
    "whatsapp": frozenset({"text", "file_upload", "rich_menu"}),
    "sms":      frozenset({"text"}),
    "email":    frozenset({"text", "file_upload"}),
    "voice":    frozenset({"audio"}),
    "webchat":  frozenset({"text", "file_upload", "rich_menu", "masked_input"}),
    "webrtc":   frozenset({"text", "audio", "video", "file_upload"}),
}

# Priority ordering when no preference is set (most capable → least).
# Channels not listed fall to the end.
_CHANNEL_PRIORITY: list[str] = [
    "webrtc", "whatsapp", "webchat", "email", "voice", "sms",
]


# ── Pure selection logic ──────────────────────────────────────────────────────

def channel_satisfies(channel: str, requires: list[str]) -> bool:
    """Return True if *channel* supports every capability in *requires*."""
    if not requires:
        return True
    caps = CHANNEL_CAPABILITIES.get(channel, frozenset())
    return all(req in caps for req in requires)


def select_channel(
    available_channels: list[str],
    requires:           list[str],
    preferred_channel:  str | None,
) -> str | None:
    """
    Select the best outbound channel for a collect step.

    Algorithm:
      1. If *preferred_channel* is in *available_channels* and satisfies
         all *requires*, return it immediately.
      2. Otherwise sort *available_channels* by _CHANNEL_PRIORITY and return
         the first that satisfies *requires*.
      3. Return None if no channel satisfies the requirements.

    Args:
        available_channels: Channels the customer has been reached on in this journey
                            (read from journey.available_channels in ContextStore).
        requires:           Capability strings from the collect step's `requires[]` field.
        preferred_channel:  journey.canal_preferido — the most recently active channel.
    """
    if not available_channels:
        return None

    # Step 1 — honour preference when it works
    if preferred_channel and preferred_channel in available_channels:
        if channel_satisfies(preferred_channel, requires):
            return preferred_channel

    # Step 2 — pick highest-priority qualifying channel
    priority = {ch: i for i, ch in enumerate(_CHANNEL_PRIORITY)}
    ordered  = sorted(
        available_channels,
        key=lambda ch: priority.get(ch, len(_CHANNEL_PRIORITY)),
    )
    for ch in ordered:
        if channel_satisfies(ch, requires):
            return ch

    return None


# ── Journey ContextStore I/O ──────────────────────────────────────────────────

def _journey_key(tenant_id: str, journey_id: str) -> str:
    return f"{tenant_id}:ctx:journey:{journey_id}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def read_journey_channel_context(
    redis:      aioredis.Redis,
    tenant_id:  str,
    journey_id: str,
) -> tuple[list[str], str | None]:
    """
    Read journey.available_channels and journey.canal_preferido from the
    journey ContextStore hash.

    Returns:
        (available_channels, preferred_channel)
        available_channels is [] when the key does not exist.
        preferred_channel is None when not set.
    """
    key = _journey_key(tenant_id, journey_id)
    try:
        raw_ch, raw_pref = await redis.hmget(
            key, "journey.available_channels", "journey.canal_preferido"
        )
        available: list[str] = json.loads(raw_ch)["value"] if raw_ch else []
        preferred: str | None = json.loads(raw_pref)["value"] if raw_pref else None
        return available, preferred
    except Exception as exc:
        logger.warning(
            "read_journey_channel_context: journey=%s error=%s", journey_id, exc
        )
        return [], None


async def write_journey_channel_context(
    redis:      aioredis.Redis,
    tenant_id:  str,
    journey_id: str,
    channel:    str,
    contact_id: str,
) -> None:
    """
    Update journey channel-presence information in the journey ContextStore hash.

    Called when a customer's message arrives on a channel that is associated
    with an active journey.  Idempotent — safe to call on every inbound event.

    Writes:
      journey.available_channels   — deduplicated list of all channels used
      journey.canal_preferido      — most recently active channel
      journey.{channel}_contact_id — customer identifier on that channel
                                     (e.g. journey.whatsapp_contact_id = "+5511...")
    """
    key = _journey_key(tenant_id, journey_id)
    now = _now_iso()
    _JOURNEY_TTL = 30 * 86_400  # 30 days

    try:
        # Read existing available_channels (NX-merge)
        raw = await redis.hget(key, "journey.available_channels")
        existing: list[str] = json.loads(raw)["value"] if raw else []
        if channel not in existing:
            existing.append(channel)

        def _entry(value: Any) -> str:
            return json.dumps({
                "value":      value,
                "confidence": 1.0,
                "source":     "channel_gateway",
                "visibility": "agents_only",
                "updated_at": now,
            })

        await redis.hset(key, mapping={
            "journey.available_channels":       _entry(existing),
            "journey.canal_preferido":          _entry(channel),
            f"journey.{channel}_contact_id":    _entry(contact_id),
        })
        # Clear pending_collect_info — the customer's response has arrived
        await redis.hdel(key, "journey.pending_collect_info")
        # Extend TTL only if this is the first write to the key
        await redis.expire(key, _JOURNEY_TTL, nx=True)

        logger.debug(
            "journey channel context updated: journey=%s channel=%s available=%s",
            journey_id, channel, existing,
        )
    except Exception as exc:
        logger.warning(
            "write_journey_channel_context: journey=%s error=%s", journey_id, exc
        )


async def get_journey_contact_id(
    redis:      aioredis.Redis,
    tenant_id:  str,
    journey_id: str,
    channel:    str,
) -> str | None:
    """
    Return the customer's contact identifier for *channel* within this journey.

    Used by the collect consumer when selecting a non-voice channel to send
    the collect prompt: the target contact_id is stored in the journey namespace
    by *write_journey_channel_context* on the customer's first inbound message.
    """
    key = _journey_key(tenant_id, journey_id)
    field = f"journey.{channel}_contact_id"
    try:
        raw = await redis.hget(key, field)
        if raw:
            return json.loads(raw)["value"]
        return None
    except Exception as exc:
        logger.warning(
            "get_journey_contact_id: journey=%s channel=%s error=%s",
            journey_id, channel, exc,
        )
        return None


async def write_journey_pending_collect(
    redis:      aioredis.Redis,
    tenant_id:  str,
    journey_id: str,
    requires:   list[str],
    channel:    str | None,
    contact_id: str | None,
) -> None:
    """
    Record that a collect step is pending for this journey.

    Written by _dispatch_collect_event() immediately after dispatching the
    collect prompt so that the journey_check_pending MCP tool can discover
    journeys that are awaiting a customer response.

    The field is removed by write_journey_channel_context() when the
    customer's inbound reply arrives and is correlated to the collect.

    Field: journey.pending_collect_info
      value: {requires, channel, contact_id, dispatched_at}
    """
    key = _journey_key(tenant_id, journey_id)
    now = _now_iso()
    _JOURNEY_TTL = 30 * 86_400  # 30 days

    entry = json.dumps({
        "value": {
            "requires":      requires,
            "channel":       channel,
            "contact_id":    contact_id,
            "dispatched_at": now,
        },
        "confidence": 1.0,
        "source":     "channel_gateway",
        "visibility": "agents_only",
        "updated_at": now,
    })
    try:
        await redis.hset(key, "journey.pending_collect_info", entry)
        await redis.expire(key, _JOURNEY_TTL, nx=True)
        logger.debug(
            "journey pending collect recorded: journey=%s channel=%s requires=%s",
            journey_id, channel, requires,
        )
    except Exception as exc:
        logger.warning(
            "write_journey_pending_collect: journey=%s error=%s", journey_id, exc
        )
