"""Tests for the ingestion_event_v1 → canonical event mapper."""
from pydantic import TypeAdapter

from plughub_quality_ingest.config import Settings
from plughub_quality_ingest.events import IngestionEvent
from plughub_quality_ingest.mapper import map_events

_ADAPTER = TypeAdapter(list[IngestionEvent])
SETTINGS = Settings(kafka_enabled=False)


def _full_contact(cid: str, *, agent_kind: str = "ai", pii: bool = False) -> list[dict]:
    content = "meu cpf 123.456.789-01" if pii else "ola, preciso de ajuda"
    return [
        {"event_type": "contact.opened", "external_contact_id": cid, "source": "ccaas:genesys",
         "channel": "voice", "opened_at": "2026-06-24T12:00:00Z", "customer_ref": "cust-9"},
        {"event_type": "participant.joined", "external_contact_id": cid, "segment_ref": "s1",
         "external_agent_id": "agt-7", "agent_kind": agent_kind, "pool_id": "retencao_humano",
         "started_at": "2026-06-24T12:00:05Z",
         **({"skill_id": "skill_portabilidade_telco", "deploy_version": "v3"} if agent_kind == "ai" else {})},
        {"event_type": "message.sent", "external_contact_id": cid, "ts": "2026-06-24T12:00:10Z",
         "author_role": "customer", "content": content, "masked": True},
        {"event_type": "message.sent", "external_contact_id": cid, "ts": "2026-06-24T12:00:20Z",
         "author_role": "agent", "content": "claro, vou ajudar", "masked": True, "segment_ref": "s1"},
        {"event_type": "participant.left", "external_contact_id": cid, "segment_ref": "s1",
         "ended_at": "2026-06-24T12:05:00Z", "outcome": "resolved"},
        {"event_type": "contact.closed", "external_contact_id": cid, "outcome": "resolved",
         "closed_at": "2026-06-24T12:05:01Z", "close_reason": "flow_complete"},
    ]


def _map(raw: list[dict]):
    events = _ADAPTER.validate_python(raw)
    return map_events(events, tenant_id="tenant_demo", settings=SETTINGS)


def _by_topic(pairs):
    out: dict[str, list[dict]] = {}
    for topic, payload in pairs:
        out.setdefault(topic, []).append(payload)
    return out


def test_emits_full_canonical_set():
    pairs = _map(_full_contact("c-1"))
    topics = [t for t, _ in pairs]
    assert "conversations.events" in topics
    assert "conversations.participants" in topics
    assert "agent.lifecycle" in topics
    assert "conversations.session_closed" in topics


def test_phase_order_participants_before_session_closed():
    pairs = _map(_full_contact("c-1"))
    topics = [t for t, _ in pairs]
    last_participant = max(i for i, t in enumerate(topics) if t == "conversations.participants")
    session_closed = topics.index("conversations.session_closed")
    assert last_participant < session_closed


def test_contact_open_first():
    pairs = _map(_full_contact("c-1"))
    topic0, payload0 = pairs[0]
    assert topic0 == "conversations.events"
    assert payload0["event_type"] == "contact_open"
    assert payload0["channel"] == "voice"
    assert payload0["customer_id"] == "cust-9"


def test_participant_joined_wire_format_ai():
    pairs = _map(_full_contact("c-1", agent_kind="ai"))
    joined = next(p for t, p in pairs if t == "conversations.participants" and p["type"] == "participant_joined")
    assert joined["type"] == "participant_joined"      # underscore, not dotted
    assert joined["agent_type"] == "ai"
    assert joined["role"] == "primary"
    assert joined["flow_id"] == "skill_portabilidade_telco"
    assert joined["deploy_version"] == "v3"
    assert joined["pool_id"] == "retencao_humano"
    assert joined["source"] == "external_import"


def test_human_segment_sets_user_id():
    pairs = _map(_full_contact("c-1", agent_kind="human"))
    joined = next(p for t, p in pairs if t == "conversations.participants" and p["type"] == "participant_joined")
    assert joined["agent_type"] == "human"
    assert joined["user_id"] == "agt-7"
    assert joined["user_login"] == "agt-7"
    assert "flow_id" not in joined


def test_message_masking_netpass():
    pairs = _map(_full_contact("c-1", pii=True))
    msgs = [p for t, p in pairs if t == "conversations.events" and p.get("event_type") == "message_sent"]
    customer_msg = next(m for m in msgs if m["author_role"] == "customer")
    assert "123.456.789-01" not in customer_msg["content"]
    assert "cpf" in customer_msg["masked_categories"]
    assert customer_msg["masked"] is True


def test_message_author_id_resolved_from_segment_ref():
    pairs = _map(_full_contact("c-1"))
    joined = next(p for t, p in pairs if t == "conversations.participants" and p["type"] == "participant_joined")
    agent_msg = next(p for t, p in pairs if t == "conversations.events"
                     and p.get("event_type") == "message_sent" and p["author_role"] == "agent")
    assert agent_msg["author_id"] == joined["participant_id"]


def test_agent_done_emitted_on_left():
    pairs = _map(_full_contact("c-1"))
    done = next(p for t, p in pairs if t == "agent.lifecycle")
    assert done["event"] == "agent_done"
    assert done["session_id"]
    assert done["outcome"] == "resolved"
    assert done["handle_time_ms"] is not None and done["handle_time_ms"] > 0


def test_contact_closed_and_session_closed_carry_pool():
    pairs = _map(_full_contact("c-1"))
    closed = next(p for t, p in pairs if t == "conversations.events" and p.get("event_type") == "contact_closed")
    sclosed = next(p for t, p in pairs if t == "conversations.session_closed")
    assert closed["pool_id"] == "retencao_humano"
    assert closed["source"] != "channel_gateway"   # must not be dropped by analytics parser
    assert sclosed["pool_id"] == "retencao_humano"  # what sampling filters on
    assert sclosed["outcome"] == "resolved"
    assert sclosed["channel"] == "voice"
    assert sclosed["started_at"] == "2026-06-24T12:00:00Z"


def test_single_session_id_across_contact():
    pairs = _map(_full_contact("c-1"))
    sids = {p["session_id"] for _t, p in pairs if "session_id" in p}
    assert len(sids) == 1


def test_interleaved_two_contacts_grouped():
    a = _full_contact("c-1")
    b = _full_contact("c-2")
    # interleave the two streams
    interleaved = [x for pair in zip(a, b) for x in pair]
    pairs = _map(interleaved)
    sclosed = [p for t, p in pairs if t == "conversations.session_closed"]
    assert len(sclosed) == 2
    assert len({p["session_id"] for p in sclosed}) == 2
    # all session_closed land after every participant emission (phase order, batch-wide)
    topics = [t for t, _ in pairs]
    last_participant = max(i for i, t in enumerate(topics) if t == "conversations.participants")
    first_session_closed = topics.index("conversations.session_closed")
    assert last_participant < first_session_closed


def test_mapping_is_idempotent_on_ids():
    p1 = _map(_full_contact("c-1"))
    p2 = _map(_full_contact("c-1"))
    sid1 = next(p["session_id"] for _t, p in p1 if "session_id" in p)
    sid2 = next(p["session_id"] for _t, p in p2 if "session_id" in p)
    assert sid1 == sid2


# ── R13c — source_map translation ─────────────────────────────────────────────────

def _ext_contact(cid: str, *, agent_kind: str, ext_pool: str, ext_agent: str) -> list[dict]:
    """A contact carrying EXTERNAL pool/agent ids (to be translated by the source_map)."""
    return [
        {"event_type": "contact.opened", "external_contact_id": cid, "source": "ccaas:genesys",
         "channel": "voice", "opened_at": "2026-06-24T12:00:00Z"},
        {"event_type": "participant.joined", "external_contact_id": cid, "segment_ref": "s1",
         "external_agent_id": ext_agent, "agent_kind": agent_kind, "pool_id": ext_pool,
         "started_at": "2026-06-24T12:00:05Z"},
        {"event_type": "participant.left", "external_contact_id": cid, "segment_ref": "s1",
         "ended_at": "2026-06-24T12:05:00Z", "outcome": "resolved"},
        {"event_type": "contact.closed", "external_contact_id": cid, "outcome": "resolved",
         "closed_at": "2026-06-24T12:05:01Z"},
    ]


_SOURCE_MAP = {
    "ccaas:genesys": {
        "pools": {"Genesys-Q-42": "retencao_humano"},
        "agents": {
            "gx-agent-7": {"kind": "human", "user_id": "wang@opuscom.com.br"},
            "gx-bot-1":   {"kind": "ai", "skill_id": "skill_retencao_v2", "deploy_version": "v9"},
        },
    },
}


def _map_sm(raw, source_map):
    events = _ADAPTER.validate_python(raw)
    return map_events(events, tenant_id="tenant_demo", settings=SETTINGS, source_map=source_map)


def test_sourcemap_translates_pool_and_human_identity():
    pairs = _map_sm(_ext_contact("e1", agent_kind="human", ext_pool="Genesys-Q-42", ext_agent="gx-agent-7"),
                    _SOURCE_MAP)
    joined = next(p for t, p in pairs if t == "conversations.participants" and p["type"] == "participant_joined")
    assert joined["pool_id"] == "retencao_humano"          # external → internal
    assert joined["user_id"] == "wang@opuscom.com.br"      # external agent → internal user
    sclosed = next(p for t, p in pairs if t == "conversations.session_closed")
    assert sclosed["pool_id"] == "retencao_humano"          # stamped from translated primary pool


def test_sourcemap_translates_ai_skill_and_version():
    pairs = _map_sm(_ext_contact("e2", agent_kind="ai", ext_pool="Genesys-Q-42", ext_agent="gx-bot-1"),
                    _SOURCE_MAP)
    joined = next(p for t, p in pairs if t == "conversations.participants" and p["type"] == "participant_joined")
    assert joined["pool_id"] == "retencao_humano"
    assert joined["flow_id"] == "skill_retencao_v2"
    assert joined["deploy_version"] == "v9"
    assert "user_id" not in joined


def test_sourcemap_passthrough_when_unmapped():
    # No source_map → external ids pass through unchanged (R13a-2 behaviour).
    pairs = _map_sm(_ext_contact("e3", agent_kind="ai", ext_pool="ext-pool-X", ext_agent="ext-bot"), {})
    joined = next(p for t, p in pairs if t == "conversations.participants" and p["type"] == "participant_joined")
    assert joined["pool_id"] == "ext-pool-X"               # unchanged
    # AI with no skill_id in event and no map → flow_id omitted (no fabricated identity)
    assert "flow_id" not in joined


def test_sourcemap_unmapped_agent_falls_back_to_external_id_for_human():
    pairs = _map_sm(_ext_contact("e4", agent_kind="human", ext_pool="Genesys-Q-42", ext_agent="unknown-agent"),
                    _SOURCE_MAP)
    joined = next(p for t, p in pairs if t == "conversations.participants" and p["type"] == "participant_joined")
    assert joined["pool_id"] == "retencao_humano"          # pool still translated
    assert joined["user_id"] == "unknown-agent"            # agent unmapped → external id as user_id
