"""
exporter.py
Internal history → ingestion_event_v1 (R13d). The exporter is a CLIENT of the
quality-ingest contract: it reads the platform's own history (ClickHouse) and
re-emits it through the SAME open endpoint the external importer uses, so the
re-evaluation path has zero divergent code.

`build_ingestion_events` is the pure inverse of the quality-ingest mapper:
  sessions  → contact.opened / contact.closed
  segments  → participant.joined / participant.left   (primary/specialist only)
  messages  → message.sent                            (masked content from CH)

Pool decision (§7): reuse the ORIGINAL pool_id (events carry it; `source` has no
source_map entry → pass-through). A dedicated review pool is achievable with NO new
mechanism by registering a source_map (R13c) for `source` mapping original→review pool.

The re-emit uses external_contact_id = original session_id, so quality-ingest derives
a NEW re-evaluation session_id — separate from the original (no collision, no double
write to the original session's rows).
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from .ch_client import ClickHouseClient

logger = logging.getLogger("plughub.quality_export.exporter")

# Stream/analytics author roles → external transcript vocabulary.
_ROLE_BACK = {"customer": "customer", "system": "system"}

# Only the agent segments that make up the handled contact.
_EXPORT_ROLES = {"primary", "specialist"}


def _iso(value: Any) -> str | None:
    """Normalize a ClickHouse datetime ('YYYY-MM-DD HH:MM:SS.fff') to ISO-8601 UTC."""
    if not value:
        return None
    s = str(value)
    if "T" not in s:
        s = s.replace(" ", "T", 1)
    if not s.endswith("Z") and "+" not in s[10:]:
        s = s + "Z"
    return s


def _author_role(stream_role: str | None) -> str:
    if not stream_role:
        return "agent"
    return _ROLE_BACK.get(stream_role, "agent")


def build_ingestion_events(
    session: dict[str, Any],
    segments: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    *,
    source: str,
) -> list[dict[str, Any]]:
    """Pure: reconstruct an ingestion_event_v1 stream for one internal session.

    Returns [] when the session is not closed (closed_at missing) — incomplete
    contacts are not re-evaluable (mirrors the external completeness rule).
    """
    session_id = session.get("session_id")
    opened_at = _iso(session.get("opened_at"))
    closed_at = _iso(session.get("closed_at"))
    if not session_id or not opened_at or not closed_at:
        return []

    events: list[dict[str, Any]] = []

    opened: dict[str, Any] = {
        "event_type": "contact.opened",
        "external_contact_id": session_id,
        "source": source,
        "channel": session.get("channel") or "webchat",
        "opened_at": opened_at,
    }
    if session.get("customer_id"):
        opened["customer_ref"] = session["customer_id"]
    events.append(opened)

    agent_segments = [s for s in segments if (s.get("role") in _EXPORT_ROLES)]

    for seg in agent_segments:
        seg_id = seg.get("segment_id")
        if not seg_id:
            continue
        is_human = (seg.get("agent_type") == "human")
        flow_id = seg.get("flow_id") or ""
        user_id = seg.get("user_id") or ""
        external_agent_id = (user_id if is_human else flow_id) or f"seg:{seg_id}"
        joined: dict[str, Any] = {
            "event_type": "participant.joined",
            "external_contact_id": session_id,
            "segment_ref": seg_id,
            "external_agent_id": external_agent_id,
            "agent_kind": "human" if is_human else "ai",
            "pool_id": seg.get("pool_id") or "",
            "started_at": _iso(seg.get("started_at")) or opened_at,
            "role": seg.get("role") or "primary",
        }
        if not is_human:
            if flow_id:
                joined["skill_id"] = flow_id
            if seg.get("deploy_version"):
                joined["deploy_version"] = seg["deploy_version"]
        events.append(joined)

    for msg in messages:
        content = msg.get("content")
        if content is None:
            content = ""
        sent: dict[str, Any] = {
            "event_type": "message.sent",
            "external_contact_id": session_id,
            "ts": _iso(msg.get("timestamp")) or opened_at,
            "author_role": _author_role(msg.get("author_role")),
            "content": content,
            "masked": True,
            "content_type": msg.get("content_type") or "text",
        }
        if msg.get("author_id"):
            sent["author_id"] = msg["author_id"]
        events.append(sent)

    for seg in agent_segments:
        seg_id = seg.get("segment_id")
        if not seg_id:
            continue
        left: dict[str, Any] = {
            "event_type": "participant.left",
            "external_contact_id": session_id,
            "segment_ref": seg_id,
            "ended_at": _iso(seg.get("ended_at")) or closed_at,
        }
        if seg.get("outcome"):
            left["outcome"] = seg["outcome"]
        events.append(left)

    events.append({
        "event_type": "contact.closed",
        "external_contact_id": session_id,
        "outcome": session.get("outcome") or "unknown",
        "closed_at": closed_at,
        **({"close_reason": session["close_reason"]} if session.get("close_reason") else {}),
    })

    return events


class InternalExporter:
    def __init__(self, ch: ClickHouseClient, ingest_url: str, *, source: str) -> None:
        self._ch = ch
        self._ingest_url = ingest_url.rstrip("/")
        self._source = source

    async def _read_session(self, tenant_id: str, session_id: str) -> dict[str, Any] | None:
        rows = await self._ch.query(
            "SELECT session_id, channel, customer_id, "
            "toString(opened_at) AS opened_at, toString(closed_at) AS closed_at, "
            "close_reason, outcome "
            "FROM sessions FINAL "
            "WHERE tenant_id = {t:String} AND session_id = {s:String} LIMIT 1",
            {"t": tenant_id, "s": session_id},
        )
        return rows[0] if rows else None

    async def _read_segments(self, tenant_id: str, session_id: str) -> list[dict[str, Any]]:
        # Ordem CRONOLÓGICA, não por `sequence_index` (mudou 2026-08-10).
        # O índice não é ordenação total dos segmentos da sessão: `queue`, sintéticos e
        # especialistas de conferência ficam FORA do contador por decisão, então saem
        # todos em 0. Enquanto o índice estava quebrado (todo left humano gravava 0) o
        # empate universal fazia esta cláusula cair no `started_at` e o resultado era
        # correto POR ACIDENTE; com o índice consertado, um especialista que entra tarde
        # (0) passaria a ordenar ANTES de um primário de handoff (1+). Ordenar por
        # `started_at` é o único critério válido — ver ADR journey/session/segment §2.3.
        return await self._ch.query(
            "SELECT segment_id, pool_id, flow_id, deploy_version, user_id, role, "
            "agent_type, sequence_index, toString(started_at) AS started_at, "
            "toString(ended_at) AS ended_at, outcome "
            "FROM segments FINAL "
            "WHERE tenant_id = {t:String} AND session_id = {s:String} "
            "ORDER BY started_at ASC, segment_id ASC",
            {"t": tenant_id, "s": session_id},
        )

    async def _read_messages(self, tenant_id: str, session_id: str) -> list[dict[str, Any]]:
        return await self._ch.query(
            "SELECT message_id, author_id, author_role, content, content_type, "
            "toString(timestamp) AS timestamp "
            "FROM messages FINAL "
            "WHERE tenant_id = {t:String} AND session_id = {s:String} "
            "ORDER BY timestamp ASC",
            {"t": tenant_id, "s": session_id},
        )

    async def export_session(self, tenant_id: str, session_id: str) -> dict[str, Any]:
        """Read one internal session, rebuild ingestion_event_v1, POST to quality-ingest.

        Returns {"session_id", "status", "events", "reeval_session_ids"}.
        """
        session = await self._read_session(tenant_id, session_id)
        if session is None:
            return {"session_id": session_id, "status": "not_found", "events": 0}

        segments = await self._read_segments(tenant_id, session_id)
        messages = await self._read_messages(tenant_id, session_id)
        events = build_ingestion_events(session, segments, messages, source=self._source)
        if not events:
            return {"session_id": session_id, "status": "incomplete", "events": 0}

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{self._ingest_url}/v1/ingest/events",
                headers={"X-Tenant-ID": tenant_id, "Content-Type": "application/json"},
                json=events,
            )
        if resp.status_code != 200:
            logger.warning("ingest POST failed (%s) for %s: %s", resp.status_code, session_id, resp.text[:300])
            return {"session_id": session_id, "status": f"ingest_error_{resp.status_code}", "events": len(events)}

        body = resp.json()
        return {
            "session_id": session_id,
            "status": "exported",
            "events": len(events),
            "reeval_session_ids": body.get("session_ids", []),
        }
