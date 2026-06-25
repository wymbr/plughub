"""
identity.py
Deterministic identity derivation for imported contacts.

external_contact_id (+ source) → session_id (SessionIdSchema-valid, stable).
segment_ref → segment_id / participant_id (uuid5, stable).
Determinism is the idempotency mechanism: a re-sent event derives the same ids, so
downstream ReplacingMergeTree / ON CONFLICT dedup naturally (no double counting).
"""
from __future__ import annotations

import base64
import hashlib
import uuid
from datetime import datetime, timezone

# Stable namespace for all quality-ingest uuid5 derivations.
_NS = uuid.UUID("6f1d2c3a-9b4e-4a7d-8c11-0a1b2c3d4e5f")


def _parse_iso(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp (tolerates trailing 'Z'); fall back to now(UTC)."""
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


def derive_session_id(source: str, external_contact_id: str, opened_at: str) -> str:
    """Deterministic session_id matching SessionIdSchema:
        sess_{YYYYMMDD}T{HHMMSS}_{[A-Z0-9]{20,26}}

    Timestamp segment from opened_at; random segment from a stable hash of
    source + external_contact_id (Base32 → [A-Z2-7], a subset of [A-Z0-9]).
    """
    dt = _parse_iso(opened_at)
    stamp = dt.strftime("%Y%m%dT%H%M%S")
    digest = hashlib.sha1(f"{source}:{external_contact_id}".encode("utf-8")).digest()  # noqa: S324
    rand = base64.b32encode(digest).decode("ascii").rstrip("=")[:26]  # 32 → take 26
    return f"sess_{stamp}_{rand}"


def derive_segment_id(session_id: str, segment_ref: str) -> str:
    return str(uuid.uuid5(_NS, f"{session_id}:{segment_ref}:segment"))


def derive_participant_id(session_id: str, segment_ref: str) -> str:
    return str(uuid.uuid5(_NS, f"{session_id}:{segment_ref}:participant"))


def derive_message_id(session_id: str, event_id: str) -> str:
    return str(uuid.uuid5(_NS, f"{session_id}:msg:{event_id}"))
