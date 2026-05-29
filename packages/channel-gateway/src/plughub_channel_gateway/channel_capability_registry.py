"""
channel_capability_registry.py
Static registry of channel capabilities and capability-based channel selection.

Arc 16 Phase D — Channel Capability Negotiation.
Arc 19 Fase F — Journey entity eliminated; capability selection now operates
                directly on registered adapters (no journey ContextStore I/O).

The registry maps each channel name to the set of ChannelCapability values it
supports.  When a collect step omits an explicit channel and supplies a
`requires[]` list instead, the Channel Gateway calls select_channel() against
the full list of registered adapter channels.

Spec: docs/arcos/arc19-unified-session-model.md
"""

from __future__ import annotations

import logging

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


# REMOVED (Arc 19 Fase F) — Journey entity eliminated
# read_journey_channel_context, write_journey_channel_context,
# get_journey_contact_id, write_journey_pending_collect
