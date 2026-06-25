"""
Tests for build_ingestion_events — the pure inverse of the quality-ingest mapper.
Round-trip is also asserted: export output → quality-ingest mapper produces canonical
events (so the re-emit path is contract-valid).
"""
import sys
from pathlib import Path

# Allow importing the export package (src layout) without installation.
_SRC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_SRC))

from plughub_quality_export.exporter import build_ingestion_events  # noqa: E402

SID = "sess_20260624T120000_ABCDEFGHIJKLMNOPQRSTUV"


def _session(**kw):
    base = {
        "session_id": SID, "channel": "voice", "customer_id": "cust-1",
        "opened_at": "2026-06-24 12:00:00.000", "closed_at": "2026-06-24 12:05:00.000",
        "close_reason": "flow_complete", "outcome": "resolved",
    }
    base.update(kw)
    return base


def _ai_seg(**kw):
    base = {
        "segment_id": "seg-1", "pool_id": "retencao_humano", "flow_id": "skill_x",
        "deploy_version": "v2", "user_id": "", "role": "primary", "agent_type": "ai",
        "sequence_index": 0, "started_at": "2026-06-24 12:00:05.000",
        "ended_at": "2026-06-24 12:05:00.000", "outcome": "resolved",
    }
    base.update(kw)
    return base


def _msg(role, text, ts, **kw):
    base = {"message_id": f"m-{ts}", "author_id": None, "author_role": role,
            "content": text, "content_type": "text", "timestamp": ts}
    base.update(kw)
    return base


def _by_type(events):
    out = {}
    for e in events:
        out.setdefault(e["event_type"], []).append(e)
    return out


def test_builds_full_event_set_for_ai_contact():
    events = build_ingestion_events(
        _session(),
        [_ai_seg()],
        [_msg("customer", "oi", "2026-06-24 12:00:10.000"),
         _msg("primary", "ajudo", "2026-06-24 12:00:20.000")],
        source="internal:reeval",
    )
    bt = _by_type(events)
    assert len(bt["contact.opened"]) == 1
    assert len(bt["participant.joined"]) == 1
    assert len(bt["message.sent"]) == 2
    assert len(bt["participant.left"]) == 1
    assert len(bt["contact.closed"]) == 1


def test_datetimes_normalized_to_iso():
    events = build_ingestion_events(_session(), [_ai_seg()], [], source="s")
    opened = next(e for e in events if e["event_type"] == "contact.opened")
    assert opened["opened_at"] == "2026-06-24T12:00:00.000Z"
    closed = next(e for e in events if e["event_type"] == "contact.closed")
    assert closed["closed_at"] == "2026-06-24T12:05:00.000Z"


def test_ai_segment_carries_skill_and_version_external_contact_is_session():
    events = build_ingestion_events(_session(), [_ai_seg()], [], source="internal:reeval")
    joined = next(e for e in events if e["event_type"] == "participant.joined")
    assert joined["agent_kind"] == "ai"
    assert joined["pool_id"] == "retencao_humano"     # original pool reused
    assert joined["skill_id"] == "skill_x"
    assert joined["deploy_version"] == "v2"
    assert joined["external_agent_id"] == "skill_x"
    assert joined["external_contact_id"] == SID        # original session = correlation key
    assert events[0]["source"] == "internal:reeval"


def test_human_segment_uses_user_id():
    seg = _ai_seg(agent_type="human", user_id="wang@opuscom.com.br", flow_id="")
    events = build_ingestion_events(_session(), [seg], [], source="s")
    joined = next(e for e in events if e["event_type"] == "participant.joined")
    assert joined["agent_kind"] == "human"
    assert joined["external_agent_id"] == "wang@opuscom.com.br"
    assert "skill_id" not in joined


def test_author_role_mapped_back():
    events = build_ingestion_events(
        _session(), [_ai_seg()],
        [_msg("customer", "a", "2026-06-24 12:00:10.000"),
         _msg("primary", "b", "2026-06-24 12:00:11.000"),
         _msg("system", "c", "2026-06-24 12:00:12.000")],
        source="s",
    )
    roles = [e["author_role"] for e in events if e["event_type"] == "message.sent"]
    assert roles == ["customer", "agent", "system"]     # primary → agent
    assert all(e.get("masked") is True for e in events if e["event_type"] == "message.sent")


def test_only_primary_specialist_segments_exported():
    segs = [
        _ai_seg(segment_id="s-prim", role="primary"),
        _ai_seg(segment_id="s-queue", role="queue"),
        _ai_seg(segment_id="s-sup", role="supervisor"),
        _ai_seg(segment_id="s-spec", role="specialist"),
    ]
    events = build_ingestion_events(_session(), segs, [], source="s")
    joined_refs = {e["segment_ref"] for e in events if e["event_type"] == "participant.joined"}
    assert joined_refs == {"s-prim", "s-spec"}


def test_incomplete_session_not_closed_returns_empty():
    assert build_ingestion_events(_session(closed_at=None), [_ai_seg()], [], source="s") == []
    assert build_ingestion_events(_session(closed_at=""), [_ai_seg()], [], source="s") == []


def test_outcome_fallback_when_missing():
    events = build_ingestion_events(_session(outcome=None), [_ai_seg()], [], source="s")
    closed = next(e for e in events if e["event_type"] == "contact.closed")
    assert closed["outcome"] == "unknown"      # never fabricates a real outcome


def test_roundtrip_through_quality_ingest_mapper():
    """Export output must be valid input to the quality-ingest mapper (contract).

    Skips when quality-ingest is not colocated (e.g. inside the baked container image,
    where only this package's src is present)."""
    qi_src = None
    for anc in Path(__file__).resolve().parents:
        for cand in (anc / "quality-ingest" / "src", anc / "packages" / "quality-ingest" / "src"):
            if cand.exists():
                qi_src = cand
                break
        if qi_src:
            break
    if qi_src is None:
        return  # quality-ingest not colocated in this checkout/image — skip silently
    sys.path.insert(0, str(qi_src))
    from pydantic import TypeAdapter
    from plughub_quality_ingest.events import IngestionEvent
    from plughub_quality_ingest.mapper import map_events
    from plughub_quality_ingest.config import Settings

    events = build_ingestion_events(
        _session(), [_ai_seg()],
        [_msg("customer", "oi", "2026-06-24 12:00:10.000")],
        source="internal:reeval",
    )
    parsed = TypeAdapter(list[IngestionEvent]).validate_python(events)
    pairs = map_events(parsed, tenant_id="tenant_demo", settings=Settings(kafka_enabled=False))
    topics = [t for t, _ in pairs]
    assert "conversations.events" in topics
    assert "conversations.session_closed" in topics
    sclosed = next(p for t, p in pairs if t == "conversations.session_closed")
    assert sclosed["pool_id"] == "retencao_humano"   # original pool preserved through round-trip
