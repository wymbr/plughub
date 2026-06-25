"""
mapper.py
The single translator (anti-corruption): ingestion_event_v1 → internal canonical
events. Correlates a stream of external events by `external_contact_id` and emits
the canonical events the live consumers already understand. The module never
touches Kafka topics/stores directly — it returns (topic, payload) pairs that the
emitter publishes.

Emission order is phase-sorted so that, across the whole batch, all
participant_joined events are produced before any conversations.session_closed
(which triggers sampling in evaluation-api). Per-contact correlation state lives
for the duration of one map() call — see docs/arcos/quality-ingest.md §9 (durable
cross-request state is R13b's stream reconstruction).

Wire-format notes grounded in the live producers (orchestrator-bridge):
  - conversations.participants uses field `type` ("participant_joined"/_left),
    NOT the dotted event_type.
  - agent.lifecycle agent_done carries `event` (not event_type).
  - conversations.events contact_closed MUST NOT set source="channel_gateway"
    (the analytics parser drops those). We stamp source=<import marker> instead,
    which doubles as the R13b consumer-Y gate.
"""
from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any

from .config import Settings
from .events import (
    ContactClosed,
    ContactOpened,
    IngestionEventModels,
    MessageSent,
    ParticipantJoined,
    ParticipantLeft,
)
from .identity import (
    derive_message_id,
    derive_participant_id,
    derive_segment_id,
    derive_session_id,
)
from .masking import mask_text

# Phase ordering of canonical emissions (stable-sorted across the batch).
_PHASE_CONTACT_OPEN   = 0
_PHASE_JOINED         = 1
_PHASE_MESSAGE        = 2
_PHASE_LEFT           = 3
_PHASE_AGENT_DONE     = 4
_PHASE_CONTACT_CLOSED = 5
_PHASE_SESSION_CLOSED = 6


def _parse_iso(ts: str) -> datetime:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


def _duration_ms(start: str, end: str) -> int | None:
    try:
        d = int((_parse_iso(end) - _parse_iso(start)).total_seconds() * 1000)
        return d if d >= 0 else None
    except Exception:
        return None


class _ContactState:
    """Per-contact correlation accumulated during one map() call."""

    def __init__(self, external_contact_id: str) -> None:
        self.external_contact_id = external_contact_id
        self.source: str = ""
        self.channel: str = ""
        self.customer_ref: str | None = None
        self.opened_at: str = ""
        self.session_id: str = ""
        # Per-source submap (R13c): {"pools": {...}, "agents": {...}} for this contact.
        self.submap: dict[str, Any] = {}
        # segment_ref → resolved segment record
        self.segments: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
        self.primary_pool_id: str = ""

    def ensure_session_id(self) -> str:
        if not self.session_id:
            source = self.source or "external_import"
            opened_at = self.opened_at or datetime.now(timezone.utc).isoformat()
            self.session_id = derive_session_id(source, self.external_contact_id, opened_at)
        return self.session_id

    def register_segment(self, ev: ParticipantJoined) -> dict[str, Any]:
        sid = self.ensure_session_id()
        # R13c — translate external pool/agent → internal via the per-source map.
        # Unmapped → pass-through (the event's own values). Anti-corruption: the
        # emitted canonical events carry INTERNAL identities only.
        pools  = self.submap.get("pools", {}) if self.submap else {}
        agents = self.submap.get("agents", {}) if self.submap else {}
        internal_pool = pools.get(ev.pool_id, ev.pool_id)
        entry = agents.get(ev.external_agent_id) or {}

        if ev.agent_kind == "human":
            user_id        = entry.get("user_id") or ev.external_agent_id
            skill_id       = None
            deploy_version = None
            agent_type_id  = ""
        else:  # ai
            skill_id       = entry.get("skill_id") or ev.skill_id
            deploy_version = entry.get("deploy_version") or ev.deploy_version
            user_id        = ""
            agent_type_id  = skill_id or ""

        seg = {
            "segment_ref":       ev.segment_ref,
            "segment_id":        derive_segment_id(sid, ev.segment_ref),
            "participant_id":    derive_participant_id(sid, ev.segment_ref),
            "pool_id":           internal_pool,
            "role":              ev.role,
            "agent_kind":        ev.agent_kind,
            "agent_type_id":     agent_type_id,
            "user_id":           user_id,
            "skill_id":          skill_id,
            "deploy_version":    deploy_version,
            "started_at":        ev.started_at,
            "sequence_index":    len(self.segments),
        }
        self.segments[ev.segment_ref] = seg
        if not self.primary_pool_id and (ev.role == "primary" or len(self.segments) == 1):
            self.primary_pool_id = internal_pool
        return seg


def _segment_identity_fields(seg: dict[str, Any]) -> dict[str, Any]:
    """flow_id/deploy_version (AI) or user_id/user_login (human) for a segment."""
    out: dict[str, Any] = {}
    if seg["agent_kind"] == "ai":
        if seg.get("skill_id"):
            out["flow_id"] = seg["skill_id"]
        if seg.get("deploy_version"):
            out["deploy_version"] = seg["deploy_version"]
    else:  # human
        if seg.get("user_id"):
            out["user_id"] = seg["user_id"]
            out["user_login"] = seg["user_id"]
    return out


def map_events(
    events: list[IngestionEventModels],
    *,
    tenant_id: str,
    settings: Settings,
    source_map: dict[str, Any] | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    """Translate a batch (stream) of ingestion_event_v1 into canonical (topic, payload).

    Groups by external_contact_id, builds per-contact state, then emits canonical
    events phase-sorted. Returns the ordered list to publish.

    `source_map` (R13c): {source: {"pools": {...}, "agents": {...}}}. Applied per
    contact (by its `source`) to translate external pool/agent ids → internal before
    emission. None / unmapped → pass-through.
    """
    marker = settings.import_source_marker
    # Substrate isolation (ADR): a reavaliação interna tem um rótulo canônico próprio
    # (origin=reeval no analytics). Imports externos usam sources arbitrários
    # (ex. "ccaas:genesys") e são normalizados ao marker (origin=import).
    reeval_marker = "internal:reeval"
    source_map = source_map or {}

    # Group by contact preserving first-seen order.
    contacts: "OrderedDict[str, list[IngestionEventModels]]" = OrderedDict()
    for ev in events:
        contacts.setdefault(ev.external_contact_id, []).append(ev)

    emissions: list[tuple[int, str, dict[str, Any]]] = []

    for cid, evs in contacts.items():
        st = _ContactState(cid)

        # Pass 1 — establish contact-level fields (source/channel/opened_at) and segments.
        opened = next((e for e in evs if isinstance(e, ContactOpened)), None)
        if opened is not None:
            st.source = opened.source
            st.channel = opened.channel
            st.customer_ref = opened.customer_ref
            st.opened_at = opened.opened_at
        else:
            # Degraded: no contact.opened — derive from any event with a source/ts.
            st.source = next((e.source for e in evs if getattr(e, "source", None)), marker)
        # R13c — resolve the per-source submap for this contact (pass-through if absent).
        st.submap = source_map.get(st.source, {}) or {}
        st.ensure_session_id()
        sid = st.session_id
        # Substrate isolation (ADR adr-quality-substrate-isolation): os eventos canônicos
        # carregam o rótulo de procedência que o analytics-api entende. Reavaliação
        # interna é preservada (→ origin=reeval); qualquer source externo (CCaaS, ex.
        # "ccaas:genesys") é normalizado ao marker de importação (→ origin=import).
        # Só esses dois rótulos descem ao stream; o consumer Y aceita ambos.
        contact_source = reeval_marker if st.source == reeval_marker else marker

        for e in evs:
            if isinstance(e, ParticipantJoined):
                st.register_segment(e)

        # Pass 2 — emit canonical events.
        if opened is not None:
            emissions.append((_PHASE_CONTACT_OPEN, settings.topic_events, {
                "event_type": "contact_open",
                "session_id": sid,
                "tenant_id":  tenant_id,
                "channel":    st.channel,
                "customer_id": st.customer_ref,
                "started_at": st.opened_at,
                "timestamp":  st.opened_at,
                "source":     contact_source,
            }))

        for e in evs:
            if isinstance(e, ParticipantJoined):
                seg = st.segments[e.segment_ref]
                payload = {
                    "event_id":       e.event_id or f"ext:{cid}:joined:{seg['sequence_index']}",
                    "type":           "participant_joined",
                    "session_id":     sid,
                    "tenant_id":      tenant_id,
                    "segment_id":     seg["segment_id"],
                    "participant_id": seg["participant_id"],
                    "pool_id":        seg["pool_id"],
                    "agent_type_id":  seg["agent_type_id"],
                    "role":           seg["role"],
                    "agent_type":     seg["agent_kind"],
                    "sequence_index": seg["sequence_index"],
                    "joined_at":      seg["started_at"],
                    "timestamp":      seg["started_at"],
                    "channel":        st.channel,
                    "source":         contact_source,
                    **_segment_identity_fields(seg),
                }
                emissions.append((_PHASE_JOINED, settings.topic_participants, payload))

            elif isinstance(e, MessageSent):
                content = e.content
                categories = list(e.masked_categories)
                # Masking net-pass (§5): defensive even though contract requires masked.
                content, detected = mask_text(content)
                for c in detected:
                    if c not in categories:
                        categories.append(c)
                author_id = e.author_id
                if author_id is None and e.segment_ref and e.segment_ref in st.segments:
                    author_id = st.segments[e.segment_ref]["participant_id"]
                eid = e.event_id or derive_message_id(sid, f"{e.ts}:{e.author_role}")
                emissions.append((_PHASE_MESSAGE, settings.topic_events, {
                    "event_type":   "message_sent",
                    "session_id":   sid,
                    "tenant_id":    tenant_id,
                    "message_id":   derive_message_id(sid, eid),
                    "author_id":    author_id,
                    "author_role":  e.author_role,
                    "channel":      st.channel,
                    "content_type": e.content_type,
                    "visibility":   e.visibility,
                    "content":      content,
                    "masked":       True,
                    "masked_categories": categories,
                    "timestamp":    e.ts,
                    "source":       contact_source,
                }))

            elif isinstance(e, ParticipantLeft):
                seg = st.segments.get(e.segment_ref)
                if seg is None:
                    # left without a matching joined — synthesize a minimal segment so
                    # the segment/agent_done rows still land (best-effort).
                    seg = {
                        "segment_id":     derive_segment_id(sid, e.segment_ref),
                        "participant_id": derive_participant_id(sid, e.segment_ref),
                        "pool_id":        st.primary_pool_id,
                        "role":           "primary",
                        "agent_kind":     "ai",
                        "agent_type_id":  "",
                        "user_id":        "",
                        "skill_id":       None,
                        "deploy_version": None,
                        "started_at":     e.ended_at,
                        "sequence_index": len(st.segments),
                    }
                    st.segments[e.segment_ref] = seg
                dur = _duration_ms(seg["started_at"], e.ended_at)
                left_payload = {
                    "event_id":       e.event_id or f"ext:{cid}:left:{seg['sequence_index']}",
                    "type":           "participant_left",
                    "session_id":     sid,
                    "tenant_id":      tenant_id,
                    "segment_id":     seg["segment_id"],
                    "participant_id": seg["participant_id"],
                    "pool_id":        seg["pool_id"],
                    "agent_type_id":  seg["agent_type_id"],
                    "role":           seg["role"],
                    "agent_type":     seg["agent_kind"],
                    "sequence_index": seg["sequence_index"],
                    "joined_at":      seg["started_at"],
                    "timestamp":      e.ended_at,
                    "duration_ms":    dur,
                    "outcome":        e.outcome,
                    "channel":        st.channel,
                    "source":         contact_source,
                    **_segment_identity_fields(seg),
                }
                emissions.append((_PHASE_LEFT, settings.topic_participants, left_payload))

                # agent.lifecycle agent_done — frees the pool resource in analytics.
                emissions.append((_PHASE_AGENT_DONE, settings.topic_lifecycle, {
                    "event":          "agent_done",
                    "tenant_id":      tenant_id,
                    "instance_id":    seg["participant_id"],
                    "participant_id": seg["participant_id"],
                    "session_id":     sid,
                    "agent_type_id":  seg["agent_type_id"],
                    "pool_id":        seg["pool_id"],
                    "outcome":        e.outcome,
                    "handoff_reason": None,
                    "handle_time_ms": dur,
                    "timestamp":      e.ended_at,
                    "source":         contact_source,
                }))

        # contact.closed → sessions close + session_closed (sampling trigger)
        closed = next((e for e in evs if isinstance(e, ContactClosed)), None)
        if closed is not None:
            emissions.append((_PHASE_CONTACT_CLOSED, settings.topic_events, {
                "event_type":   "contact_closed",
                "session_id":   sid,
                "tenant_id":    tenant_id,
                "close_reason": closed.close_reason,
                "outcome":      closed.outcome,
                "pool_id":      st.primary_pool_id,
                "started_at":   st.opened_at or None,
                "ended_at":     closed.closed_at,
                "customer_id":  st.customer_ref,
                "channel":      st.channel,
                "sla_target_ms": None,
                "source":       contact_source,
            }))
            emissions.append((_PHASE_SESSION_CLOSED, settings.topic_session_closed, {
                "session_id":   sid,
                "tenant_id":    tenant_id,
                "outcome":      closed.outcome,
                "close_reason": closed.close_reason,
                "closed_at":    closed.closed_at,
                "pool_id":      st.primary_pool_id,
                "channel":      st.channel,
                "started_at":   st.opened_at or None,
                "source":       contact_source,
            }))

    # Stable-sort by phase so participants precede session_closed across the batch.
    emissions.sort(key=lambda t: t[0])
    return [(topic, payload) for _phase, topic, payload in emissions]
