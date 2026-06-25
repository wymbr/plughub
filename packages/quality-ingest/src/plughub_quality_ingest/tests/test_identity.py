"""Tests for deterministic identity derivation."""
import re

from plughub_quality_ingest.identity import (
    derive_participant_id,
    derive_segment_id,
    derive_session_id,
)

_SESSION_RE = re.compile(r"^sess_\d{8}T\d{6}_[A-Z0-9]{20,26}$")
ISO = "2026-06-24T13:45:09Z"


def test_session_id_matches_schema():
    sid = derive_session_id("ccaas:genesys", "c-1", ISO)
    assert _SESSION_RE.match(sid), sid
    assert sid.startswith("sess_20260624T134509_")


def test_session_id_is_deterministic():
    a = derive_session_id("ccaas:genesys", "c-1", ISO)
    b = derive_session_id("ccaas:genesys", "c-1", ISO)
    assert a == b


def test_session_id_differs_by_contact_and_source():
    base = derive_session_id("ccaas:genesys", "c-1", ISO)
    assert base != derive_session_id("ccaas:genesys", "c-2", ISO)
    assert base != derive_session_id("ccaas:other", "c-1", ISO)


def test_segment_and_participant_ids_deterministic_and_distinct():
    sid = derive_session_id("s", "c", ISO)
    seg1 = derive_segment_id(sid, "seg-a")
    seg2 = derive_segment_id(sid, "seg-b")
    assert seg1 == derive_segment_id(sid, "seg-a")   # deterministic
    assert seg1 != seg2                               # differ by ref
    # segment_id and participant_id are distinct namespaces for the same ref
    assert derive_segment_id(sid, "seg-a") != derive_participant_id(sid, "seg-a")
