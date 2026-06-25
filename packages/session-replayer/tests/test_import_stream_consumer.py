"""
test_import_stream_consumer.py — R13b unit tests (no infra required).

Cobre o mapper puro canonical_event_to_record + a inserção via FakeConn:
  - gate source=external_import (eventos vivos são ignorados)
  - mapeamento por tipo (contact_open/message_sent/contact_closed/participant_*)
  - event_ids estáveis (idempotência)
  - original_content sempre null; author.role do transcript
  - ImportStreamConsumer._handle insere e recomputa deltas no fechamento

Standalone:  python3 packages/session-replayer/tests/test_import_stream_consumer.py
Via pytest:  pytest packages/session-replayer/tests/test_import_stream_consumer.py
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(_SRC))

from session_replayer.import_stream_consumer import (  # noqa: E402
    ImportStreamConsumer,
    canonical_event_to_record,
    _origin_from_source,
)

TENANT = "tenant_demo"
SID = "sess_20260624T120000_ABCDEFGHIJKLMNOPQRSTUV"


def _evt(**kw):
    base = {"source": "external_import", "session_id": SID, "tenant_id": TENANT}
    base.update(kw)
    return base


# ── gate ────────────────────────────────────────────────────────────────────────

def test_gate_skips_non_import():
    ev = _evt(event_type="message_sent", source="channel_gateway",
              timestamp="2026-06-24T12:00:10Z", author_role="customer", content="oi")
    assert canonical_event_to_record("conversations.events", ev) is None


def test_gate_skips_unknown_topic_and_type():
    assert canonical_event_to_record("agent.lifecycle", _evt(event="agent_done")) is None
    assert canonical_event_to_record("conversations.events", _evt(event_type="weird")) is None


# ── contact_open → session_opened ────────────────────────────────────────────────

def test_contact_open_maps_session_opened():
    rec = canonical_event_to_record("conversations.events",
        _evt(event_type="contact_open", channel="voice", started_at="2026-06-24T12:00:00Z"))
    assert rec["event_type"] == "session_opened"
    assert rec["event_id"] == f"{SID}:session_opened"
    assert rec["payload"]["channel"] == "voice"
    assert rec["original_content"] is None
    assert isinstance(rec["timestamp"], datetime)


# ── message_sent → message ────────────────────────────────────────────────────────

def test_message_customer_role_and_content():
    rec = canonical_event_to_record("conversations.events",
        _evt(event_type="message_sent", timestamp="2026-06-24T12:00:10Z",
             author_role="customer", content="oi", message_id="m1",
             masked_categories=["cpf"]))
    assert rec["event_type"] == "message"
    assert rec["event_id"] == "m1"
    assert rec["author"]["role"] == "customer"
    assert rec["author"]["participant_id"] == "customer"
    assert rec["payload"]["content"]["text"] == "oi"
    assert rec["payload"]["content"]["type"] == "text"
    assert rec["payload"]["masked"] is True
    assert rec["masked_categories"] == ["cpf"]
    assert rec["original_content"] is None


def test_message_agent_role_mapped_to_primary():
    rec = canonical_event_to_record("conversations.events",
        _evt(event_type="message_sent", timestamp="2026-06-24T12:00:20Z",
             author_role="agent", content="ajudo sim", author_id="part-1", message_id="m2"))
    assert rec["author"]["role"] == "primary"           # agent → primary
    assert rec["author"]["participant_id"] == "part-1"


# ── contact_closed → session_closed ───────────────────────────────────────────────

def test_contact_closed_maps_session_closed():
    rec = canonical_event_to_record("conversations.events",
        _evt(event_type="contact_closed", outcome="resolved",
             close_reason="flow_complete", ended_at="2026-06-24T12:05:00Z"))
    assert rec["event_type"] == "session_closed"
    assert rec["event_id"] == f"{SID}:session_closed"
    assert rec["payload"]["outcome"] == "resolved"
    assert rec["payload"]["close_reason"] == "flow_complete"


# ── participants ──────────────────────────────────────────────────────────────────

def test_participant_joined_and_left():
    j = canonical_event_to_record("conversations.participants",
        _evt(type="participant_joined", event_id="e1", participant_id="p1",
             role="primary", pool_id="retencao_humano", agent_type="ai",
             timestamp="2026-06-24T12:00:05Z"))
    assert j["event_type"] == "participant_joined"
    assert j["event_id"] == "e1"
    assert j["author"]["role"] == "primary"
    assert j["payload"]["pool_id"] == "retencao_humano"

    l = canonical_event_to_record("conversations.participants",
        _evt(type="participant_left", event_id="e2", participant_id="p1",
             role="primary", outcome="resolved", timestamp="2026-06-24T12:05:00Z"))
    assert l["event_type"] == "participant_left"
    assert l["payload"]["reason"] == "resolved"


def test_event_ids_are_stable():
    a = canonical_event_to_record("conversations.events", _evt(event_type="contact_open", channel="voice"))
    b = canonical_event_to_record("conversations.events", _evt(event_type="contact_open", channel="voice"))
    assert a["event_id"] == b["event_id"]


# ── _handle inserts + recomputes deltas on close ──────────────────────────────────

class _FakePersister:
    def __init__(self):
        self.inserted: list[dict] = []
        self.recomputed: list[tuple[str, str]] = []

    async def insert_records(self, session_id, tenant_id, records):
        self.inserted.extend(records)
        return len(records)

    async def recompute_deltas(self, session_id, tenant_id):
        self.recomputed.append((session_id, tenant_id))


async def test_handle_inserts_and_recomputes_on_close():
    fake = _FakePersister()
    c = ImportStreamConsumer("brokers", fake)  # type: ignore[arg-type]

    await c._handle("conversations.events",
        _evt(event_type="message_sent", timestamp="2026-06-24T12:00:10Z",
             author_role="customer", content="oi", message_id="m1"))
    assert len(fake.inserted) == 1
    assert fake.recomputed == []          # not a close → no recompute yet

    await c._handle("conversations.events",
        _evt(event_type="contact_closed", outcome="resolved", ended_at="2026-06-24T12:05:00Z"))
    assert len(fake.inserted) == 2
    assert fake.recomputed == [(SID, TENANT)]   # close → recompute fired


async def test_handle_skips_non_import():
    fake = _FakePersister()
    c = ImportStreamConsumer("brokers", fake)  # type: ignore[arg-type]
    await c._handle("conversations.events",
        _evt(event_type="message_sent", source="channel_gateway",
             timestamp="2026-06-24T12:00:10Z", author_role="customer", content="oi"))
    assert fake.inserted == []


# ── substrate isolation (ADR): origin stamping ────────────────────────────────────

def test_origin_from_source_mapping():
    assert _origin_from_source("external_import") == "import"
    assert _origin_from_source("internal:reeval") == "reeval"
    assert _origin_from_source("channel_gateway") == "live"
    assert _origin_from_source("") == "live"
    assert _origin_from_source(None) == "live"


async def test_handle_stamps_origin_import():
    fake = _FakePersister()
    c = ImportStreamConsumer("brokers", fake)  # type: ignore[arg-type]
    await c._handle("conversations.events",
        _evt(event_type="message_sent", timestamp="2026-06-24T12:00:10Z",
             author_role="customer", content="oi", message_id="m1"))
    assert len(fake.inserted) == 1
    assert fake.inserted[0]["origin"] == "import"   # gated external_import → import


def test_gate_accepts_reeval():
    # reavaliação interna (quality-export) também reconstrói o stream (ReplayContext).
    rec = canonical_event_to_record("conversations.events",
        _evt(event_type="contact_open", channel="webchat", source="internal:reeval"))
    assert rec is not None
    assert rec["event_type"] == "session_opened"


async def test_handle_stamps_origin_reeval():
    fake = _FakePersister()
    c = ImportStreamConsumer("brokers", fake)  # type: ignore[arg-type]
    await c._handle("conversations.events",
        _evt(event_type="message_sent", source="internal:reeval",
             timestamp="2026-06-24T12:00:10Z", author_role="customer",
             content="oi", message_id="m1"))
    assert len(fake.inserted) == 1
    assert fake.inserted[0]["origin"] == "reeval"   # internal:reeval → reeval


# ── Runner ───────────────────────────────────────────────────────────────────────

def _main() -> int:
    sync_tests = [
        test_gate_skips_non_import, test_gate_skips_unknown_topic_and_type,
        test_contact_open_maps_session_opened, test_message_customer_role_and_content,
        test_message_agent_role_mapped_to_primary, test_contact_closed_maps_session_closed,
        test_participant_joined_and_left, test_event_ids_are_stable,
        test_origin_from_source_mapping, test_gate_accepts_reeval,
    ]
    async_tests = [
        test_handle_inserts_and_recomputes_on_close, test_handle_skips_non_import,
        test_handle_stamps_origin_import, test_handle_stamps_origin_reeval,
    ]
    failed = 0
    for t in sync_tests:
        try:
            t(); print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failed += 1; print(f"  FAIL {t.__name__}: {e}")
    for t in async_tests:
        try:
            asyncio.run(t()); print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failed += 1; print(f"  FAIL {t.__name__}: {e}")
    print(f"\n{'ALL PASS' if failed == 0 else f'{failed} FAILED'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())
