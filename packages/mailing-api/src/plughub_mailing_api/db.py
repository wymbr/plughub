"""
db.py
DDL and raw asyncpg operations for the mailing-api (schema `outbound` — canonical
store of the outbound domain: mailings + campaigns + per-campaign deliveries).

`metadata`, `contacts`, `channel_policy`, `selection`, `retry`, `pacing` are JSONB
blobs whose shape is the Zod contract in @plughub/schemas/outbound.ts (validated on
ingest by the Pydantic models in router.py). `entry.metadata` is OPAQUE to the platform
(producer↔consumer contract).

Fase 1: mailings / mailing_entries / campaigns / campaign_deliveries. Governance
(contact_log / contact_policy) is Fase 2.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import asyncpg

logger = logging.getLogger("plughub.mailing.db")

_DDL_SCHEMA = "CREATE SCHEMA IF NOT EXISTS outbound"

_DDL_MAILINGS = """
CREATE TABLE IF NOT EXISTS outbound.mailings (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id         TEXT        NOT NULL,
    name              TEXT        NOT NULL,
    description       TEXT,
    dedup_policy      TEXT        NOT NULL DEFAULT 'customer_context'
                                  CHECK (dedup_policy IN ('customer','customer_context','none')),
    metadata_contract TEXT,
    entry_ttl_seconds INTEGER,
    column_map        JSONB,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

_DDL_MAILINGS_IDX = (
    "CREATE INDEX IF NOT EXISTS idx_mailings_tenant "
    "ON outbound.mailings (tenant_id, created_at DESC)"
)

_DDL_ENTRIES = """
CREATE TABLE IF NOT EXISTS outbound.mailing_entries (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    mailing_id    UUID        NOT NULL REFERENCES outbound.mailings(id) ON DELETE CASCADE,
    tenant_id     TEXT        NOT NULL,
    customer_id   TEXT,
    contacts      JSONB       NOT NULL DEFAULT '{}',
    metadata      JSONB       NOT NULL DEFAULT '{}',
    dedup_key     TEXT        NOT NULL,
    source        TEXT,
    status        TEXT        NOT NULL DEFAULT 'active'
                              CHECK (status IN ('active','expired','unsubscribed','invalid')),
    added_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at    TIMESTAMPTZ,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (mailing_id, dedup_key)
)
"""

_DDL_ENTRIES_IDX = (
    "CREATE INDEX IF NOT EXISTS idx_entries_drain "
    "ON outbound.mailing_entries (mailing_id, status, added_at)"
)

_DDL_CAMPAIGNS = """
CREATE TABLE IF NOT EXISTS outbound.campaigns (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           TEXT        NOT NULL,
    name                TEXT        NOT NULL,
    mailing_id          UUID        NOT NULL REFERENCES outbound.mailings(id),
    pool_id             TEXT        NOT NULL,
    selection           JSONB,
    ordering            JSONB       NOT NULL DEFAULT '[]',
    channel_policy      JSONB       NOT NULL DEFAULT '{}',
    contact_calendar_id TEXT,
    contact_policy_id   UUID,
    transactional       BOOLEAN     NOT NULL DEFAULT false,
    batch_size          INTEGER     NOT NULL DEFAULT 50,
    pacing              JSONB       NOT NULL DEFAULT '{}',
    retry               JSONB       NOT NULL DEFAULT '{}',
    agenda_id           UUID,
    status              TEXT        NOT NULL DEFAULT 'active'
                                    CHECK (status IN ('active','paused','completed','archived')),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

_DDL_CAMPAIGNS_IDX = (
    "CREATE INDEX IF NOT EXISTS idx_campaigns_tenant "
    "ON outbound.campaigns (tenant_id, created_at DESC)"
)

_DDL_DELIVERIES = """
CREATE TABLE IF NOT EXISTS outbound.campaign_deliveries (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id      UUID        NOT NULL REFERENCES outbound.campaigns(id) ON DELETE CASCADE,
    mailing_entry_id UUID        NOT NULL REFERENCES outbound.mailing_entries(id) ON DELETE CASCADE,
    tenant_id        TEXT        NOT NULL,
    claimed_at       TIMESTAMPTZ,
    contacted_at     TIMESTAMPTZ,
    session_id       TEXT,
    root_session_id  TEXT,
    result           TEXT        NOT NULL DEFAULT 'claimed'
                                 CHECK (result IN ('claimed','pending','contacted','responded',
                                                   'failed','skipped_ineligible','suppressed')),
    attempts         INTEGER     NOT NULL DEFAULT 0,
    error            TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (campaign_id, mailing_entry_id)
)
"""

_DDL_DELIVERIES_IDX = (
    "CREATE INDEX IF NOT EXISTS idx_deliveries_campaign "
    "ON outbound.campaign_deliveries (campaign_id, created_at DESC)"
)

# ── Fase 2 — contact governance (fact × rule) ─────────────────────────────────

_DDL_CONTACT_LOG = """
CREATE TABLE IF NOT EXISTS outbound.contact_log (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    TEXT        NOT NULL,
    customer_id  TEXT        NOT NULL,
    channel      TEXT        NOT NULL,
    campaign_id  UUID,
    contacted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    result       TEXT        NOT NULL DEFAULT 'sent',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

_DDL_CONTACT_LOG_IDX = (
    "CREATE INDEX IF NOT EXISTS idx_contact_log_customer "
    "ON outbound.contact_log (tenant_id, customer_id, contacted_at)"
)
_DDL_CONTACT_LOG_IDX_CH = (
    "CREATE INDEX IF NOT EXISTS idx_contact_log_customer_channel "
    "ON outbound.contact_log (tenant_id, customer_id, channel, contacted_at)"
)

_DDL_CONTACT_POLICY = """
CREATE TABLE IF NOT EXISTS outbound.contact_policy (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        TEXT        NOT NULL,
    scope            TEXT        NOT NULL CHECK (scope IN ('tenant','campaign')),
    scope_id         TEXT,
    frequency_caps   JSONB       NOT NULL DEFAULT '[]',
    quarantine_after TEXT,
    channel_caps     JSONB       NOT NULL DEFAULT '{}',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, scope, scope_id)
)
"""


async def ensure_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(_DDL_SCHEMA)
            await conn.execute(_DDL_MAILINGS)
            # Idempotent add for DBs created before the column_map column (Fase 4).
            await conn.execute(
                "ALTER TABLE outbound.mailings "
                "ADD COLUMN IF NOT EXISTS column_map JSONB"
            )
            await conn.execute(_DDL_MAILINGS_IDX)
            await conn.execute(_DDL_ENTRIES)
            await conn.execute(_DDL_ENTRIES_IDX)
            await conn.execute(_DDL_CAMPAIGNS)
            # Idempotent add for DBs created before the ordering column existed.
            await conn.execute(
                "ALTER TABLE outbound.campaigns "
                "ADD COLUMN IF NOT EXISTS ordering JSONB NOT NULL DEFAULT '[]'"
            )
            await conn.execute(_DDL_CAMPAIGNS_IDX)
            await conn.execute(_DDL_DELIVERIES)
            await conn.execute(_DDL_DELIVERIES_IDX)
            await conn.execute(_DDL_CONTACT_LOG)
            await conn.execute(_DDL_CONTACT_LOG_IDX)
            await conn.execute(_DDL_CONTACT_LOG_IDX_CH)
            await conn.execute(_DDL_CONTACT_POLICY)
    logger.info("outbound schema ensured")


# ── Row mappers ───────────────────────────────────────────────────────────────

def _iso(dt: Any) -> str | None:
    return dt.isoformat() if dt is not None else None


def _jl(v: Any) -> Any:
    """JSONB comes back as str (asyncpg default) — decode; tolerate already-dict."""
    if v is None:
        return None
    return json.loads(v) if isinstance(v, (str, bytes)) else v


def _row_to_mailing(row: asyncpg.Record) -> dict[str, Any]:
    return {
        "id":                str(row["id"]),
        "tenant_id":         row["tenant_id"],
        "name":              row["name"],
        "description":       row["description"],
        "dedup_policy":      row["dedup_policy"],
        "metadata_contract": row["metadata_contract"],
        "entry_ttl_seconds": row["entry_ttl_seconds"],
        "column_map":        _jl(row["column_map"]),
        "created_at":        _iso(row["created_at"]),
        "updated_at":        _iso(row["updated_at"]),
    }


def _row_to_entry(row: asyncpg.Record) -> dict[str, Any]:
    return {
        "id":          str(row["id"]),
        "mailing_id":  str(row["mailing_id"]),
        "tenant_id":   row["tenant_id"],
        "customer_id": row["customer_id"],
        "contacts":    _jl(row["contacts"]) or {},
        "metadata":    _jl(row["metadata"]) or {},
        "dedup_key":   row["dedup_key"],
        "source":      row["source"],
        "status":      row["status"],
        "added_at":    _iso(row["added_at"]),
        "expires_at":  _iso(row["expires_at"]),
        "updated_at":  _iso(row["updated_at"]),
    }


def _row_to_campaign(row: asyncpg.Record) -> dict[str, Any]:
    return {
        "id":                  str(row["id"]),
        "tenant_id":           row["tenant_id"],
        "name":                row["name"],
        "mailing_id":          str(row["mailing_id"]),
        "pool_id":             row["pool_id"],
        "selection":           _jl(row["selection"]),
        "ordering":            _jl(row["ordering"]) or [],
        "channel_policy":      _jl(row["channel_policy"]) or {},
        "contact_calendar_id": row["contact_calendar_id"],
        "contact_policy_id":   str(row["contact_policy_id"]) if row["contact_policy_id"] else None,
        "transactional":       row["transactional"],
        "batch_size":          row["batch_size"],
        "pacing":              _jl(row["pacing"]) or {},
        "retry":               _jl(row["retry"]) or {},
        "agenda_id":           str(row["agenda_id"]) if row["agenda_id"] else None,
        "status":              row["status"],
        "created_at":          _iso(row["created_at"]),
        "updated_at":          _iso(row["updated_at"]),
    }


def _row_to_delivery(row: asyncpg.Record) -> dict[str, Any]:
    return {
        "id":               str(row["id"]),
        "campaign_id":      str(row["campaign_id"]),
        "mailing_entry_id": str(row["mailing_entry_id"]),
        "tenant_id":        row["tenant_id"],
        "claimed_at":       _iso(row["claimed_at"]),
        "contacted_at":     _iso(row["contacted_at"]),
        "session_id":       row["session_id"],
        "root_session_id":  row["root_session_id"],
        "result":           row["result"],
        "attempts":         row["attempts"],
        "error":            row["error"],
        "created_at":       _iso(row["created_at"]),
        "updated_at":       _iso(row["updated_at"]),
    }


# ── Mailings — CRUD ───────────────────────────────────────────────────────────

async def db_list_mailings(pool: asyncpg.Pool, tenant_id: str) -> list[dict]:
    rows = await pool.fetch(
        "SELECT * FROM outbound.mailings WHERE tenant_id = $1 ORDER BY created_at DESC",
        tenant_id,
    )
    return [_row_to_mailing(r) for r in rows]


async def db_get_mailing(pool: asyncpg.Pool, tenant_id: str, id: str) -> dict | None:
    row = await pool.fetchrow(
        "SELECT * FROM outbound.mailings WHERE id = $1 AND tenant_id = $2",
        UUID(id), tenant_id,
    )
    return _row_to_mailing(row) if row else None


async def db_create_mailing(pool: asyncpg.Pool, tenant_id: str, data: dict) -> dict:
    row = await pool.fetchrow(
        """
        INSERT INTO outbound.mailings
            (tenant_id, name, description, dedup_policy, metadata_contract,
             entry_ttl_seconds, column_map)
        VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb)
        RETURNING *
        """,
        tenant_id,
        data["name"], data.get("description"),
        data.get("dedup_policy", "customer_context"),
        data.get("metadata_contract"), data.get("entry_ttl_seconds"),
        json.dumps(data["column_map"]) if data.get("column_map") is not None else None,
    )
    return _row_to_mailing(row)


async def db_update_mailing(pool: asyncpg.Pool, tenant_id: str, id: str, data: dict) -> dict | None:
    row = await pool.fetchrow(
        """
        UPDATE outbound.mailings
        SET name              = COALESCE($3, name),
            description       = COALESCE($4, description),
            dedup_policy      = COALESCE($5, dedup_policy),
            metadata_contract = COALESCE($6, metadata_contract),
            entry_ttl_seconds = COALESCE($7, entry_ttl_seconds),
            column_map        = COALESCE($8::jsonb, column_map),
            updated_at        = now()
        WHERE id = $1 AND tenant_id = $2
        RETURNING *
        """,
        UUID(id), tenant_id,
        data.get("name"), data.get("description"),
        data.get("dedup_policy"), data.get("metadata_contract"),
        data.get("entry_ttl_seconds"),
        json.dumps(data["column_map"]) if data.get("column_map") is not None else None,
    )
    return _row_to_mailing(row) if row else None


async def db_delete_mailing(pool: asyncpg.Pool, tenant_id: str, id: str) -> bool:
    result = await pool.execute(
        "DELETE FROM outbound.mailings WHERE id = $1 AND tenant_id = $2",
        UUID(id), tenant_id,
    )
    return result.endswith("1")


# ── Entries — mailing_add (upsert by dedup_key) ───────────────────────────────

def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _derive_dedup_key(policy: str, customer_id: str | None, metadata: dict) -> str:
    """dedup_key per the mailing's dedup_policy.
      customer          → the customer_id (falls back to a content hash if null)
      customer_context  → hash(customer_id + canonical(metadata))
      none              → always distinct (uuid handled by caller as a random key)
    """
    if policy == "customer" and customer_id:
        return f"cust:{customer_id}"
    if policy == "none":
        # Random so entries never collapse; the UNIQUE never collides.
        return "none:" + hashlib.sha256(f"{customer_id}|{_canonical(metadata)}|{datetime.now(timezone.utc).timestamp()}".encode()).hexdigest()[:24]
    # customer_context (and the customer-with-null fallback)
    basis = f"{customer_id or ''}|{_canonical(metadata)}"
    return "ctx:" + hashlib.sha256(basis.encode()).hexdigest()[:32]


async def db_add_entry(pool: asyncpg.Pool, tenant_id: str, mailing: dict, data: dict) -> dict:
    """Upsert an entry by (mailing_id, dedup_key). Returns {entry_id, deduped}."""
    customer_id = data.get("customer_id")
    metadata    = data.get("metadata") or {}
    contacts    = data.get("contacts") or {}
    dedup_key   = data.get("dedup_key") or _derive_dedup_key(
        mailing["dedup_policy"], customer_id, metadata,
    )
    ttl = data.get("ttl_seconds") or mailing.get("entry_ttl_seconds")
    expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=int(ttl)) if ttl else None
    )
    row = await pool.fetchrow(
        """
        INSERT INTO outbound.mailing_entries
            (mailing_id, tenant_id, customer_id, contacts, metadata, dedup_key,
             source, status, expires_at)
        VALUES ($1,$2,$3,$4::jsonb,$5::jsonb,$6,$7,'active',$8)
        ON CONFLICT (mailing_id, dedup_key) DO UPDATE
            SET customer_id = COALESCE(EXCLUDED.customer_id, outbound.mailing_entries.customer_id),
                contacts    = EXCLUDED.contacts,
                metadata    = EXCLUDED.metadata,
                source      = COALESCE(EXCLUDED.source, outbound.mailing_entries.source),
                status      = 'active',
                expires_at  = EXCLUDED.expires_at,
                updated_at  = now()
        RETURNING id, (xmax <> 0) AS deduped
        """,
        UUID(mailing["id"]), tenant_id, customer_id,
        json.dumps(contacts), json.dumps(metadata), dedup_key,
        data.get("source"), expires_at,
    )
    return {"entry_id": str(row["id"]), "deduped": bool(row["deduped"])}


async def db_list_entries(
    pool: asyncpg.Pool, tenant_id: str, mailing_id: str, status: str | None = None,
) -> list[dict]:
    if status:
        rows = await pool.fetch(
            "SELECT * FROM outbound.mailing_entries "
            "WHERE mailing_id = $1 AND tenant_id = $2 AND status = $3 ORDER BY added_at",
            UUID(mailing_id), tenant_id, status,
        )
    else:
        rows = await pool.fetch(
            "SELECT * FROM outbound.mailing_entries "
            "WHERE mailing_id = $1 AND tenant_id = $2 ORDER BY added_at",
            UUID(mailing_id), tenant_id,
        )
    return [_row_to_entry(r) for r in rows]


# ── Campaigns — CRUD ──────────────────────────────────────────────────────────

async def db_list_campaigns(pool: asyncpg.Pool, tenant_id: str) -> list[dict]:
    rows = await pool.fetch(
        "SELECT * FROM outbound.campaigns WHERE tenant_id = $1 ORDER BY created_at DESC",
        tenant_id,
    )
    return [_row_to_campaign(r) for r in rows]


async def db_get_campaign(pool: asyncpg.Pool, tenant_id: str, id: str) -> dict | None:
    row = await pool.fetchrow(
        "SELECT * FROM outbound.campaigns WHERE id = $1 AND tenant_id = $2",
        UUID(id), tenant_id,
    )
    return _row_to_campaign(row) if row else None


async def db_create_campaign(pool: asyncpg.Pool, tenant_id: str, data: dict) -> dict:
    row = await pool.fetchrow(
        """
        INSERT INTO outbound.campaigns
            (tenant_id, name, mailing_id, pool_id, selection, ordering, channel_policy,
             contact_calendar_id, transactional, batch_size, retry, agenda_id, status)
        VALUES ($1,$2,$3,$4,$5::jsonb,$6::jsonb,$7::jsonb,$8,$9,$10,$11::jsonb,$12,$13)
        RETURNING *
        """,
        tenant_id, data["name"], UUID(data["mailing_id"]), data["pool_id"],
        json.dumps(data["selection"]) if data.get("selection") is not None else None,
        json.dumps(data.get("ordering", [])),
        json.dumps(data.get("channel_policy", {})),
        data.get("contact_calendar_id"),
        data.get("transactional", False), data.get("batch_size", 50),
        json.dumps(data.get("retry", {})),
        UUID(data["agenda_id"]) if data.get("agenda_id") else None,
        data.get("status", "active"),
    )
    return _row_to_campaign(row)


async def db_update_campaign(pool: asyncpg.Pool, tenant_id: str, id: str, data: dict) -> dict | None:
    row = await pool.fetchrow(
        """
        UPDATE outbound.campaigns
        SET name                = COALESCE($3, name),
            pool_id             = COALESCE($4, pool_id),
            selection           = COALESCE($5::jsonb, selection),
            channel_policy      = COALESCE($6::jsonb, channel_policy),
            transactional       = COALESCE($7, transactional),
            batch_size          = COALESCE($8, batch_size),
            retry               = COALESCE($9::jsonb, retry),
            agenda_id           = COALESCE($10, agenda_id),
            status              = COALESCE($11, status),
            contact_calendar_id = COALESCE($12, contact_calendar_id),
            ordering            = COALESCE($13::jsonb, ordering),
            updated_at          = now()
        WHERE id = $1 AND tenant_id = $2
        RETURNING *
        """,
        UUID(id), tenant_id,
        data.get("name"), data.get("pool_id"),
        json.dumps(data["selection"]) if "selection" in data and data["selection"] is not None else None,
        json.dumps(data["channel_policy"]) if "channel_policy" in data else None,
        data.get("transactional"), data.get("batch_size"),
        json.dumps(data["retry"]) if "retry" in data else None,
        UUID(data["agenda_id"]) if data.get("agenda_id") else None,
        data.get("status"),
        data.get("contact_calendar_id"),
        json.dumps(data["ordering"]) if "ordering" in data else None,
    )
    return _row_to_campaign(row) if row else None


# ── Drain (claim a batch) + delivery result ───────────────────────────────────

# Declarative ordering — path sanitized to a safe token so it can be interpolated
# into the metadata accessor (never user quotes). added_at is always the final
# tiebreaker (determinism). `{order_by}` is substituted from campaign.ordering.
_ORDER_PATH_RE = re.compile(r"^[a-zA-Z0-9_]+$")


def _build_order_by(ordering: Any) -> str:
    """Build the ORDER BY expression list (no 'ORDER BY' prefix) from campaign.ordering.
    Alias is `e` (outbound.mailing_entries). Invalid/unsafe paths are skipped. Numeric
    ordering is guarded (non-numeric → NULL → NULLS LAST) to never error on bad data."""
    parts: list[str] = []
    for o in ordering or []:
        if not isinstance(o, dict):
            continue
        path = o.get("path")
        if not path or not _ORDER_PATH_RE.match(str(path)):
            logger.warning("campaign.ordering: unsafe/invalid path %r skipped", path)
            continue
        direction = "DESC" if str(o.get("dir", "asc")).lower() == "desc" else "ASC"
        if str(o.get("type", "text")).lower() == "number":
            expr = (
                f"(CASE WHEN e.metadata->>'{path}' ~ '^-?[0-9]+(\\.[0-9]+)?$' "
                f"THEN (e.metadata->>'{path}')::numeric END)"
            )
        else:
            expr = f"(e.metadata->>'{path}')"
        parts.append(f"{expr} {direction} NULLS LAST")
    parts.append("e.added_at ASC")   # guaranteed final tiebreaker
    return ", ".join(parts)


_DRAIN_SQL_TMPL = """
WITH cand AS (
  SELECT e.id
  FROM outbound.mailing_entries e
  WHERE e.mailing_id = $6
    AND e.tenant_id  = $2
    AND e.status = 'active'
    AND (e.expires_at IS NULL OR e.expires_at > now())
    AND ($4::jsonb IS NULL OR e.metadata @> $4::jsonb)
    AND NOT EXISTS (
      SELECT 1 FROM outbound.campaign_deliveries d
      WHERE d.campaign_id = $1 AND d.mailing_entry_id = e.id
        AND (d.result <> 'failed' OR d.attempts >= $5)
    )
  ORDER BY {order_by}
  LIMIT $3
  FOR UPDATE OF e SKIP LOCKED
)
INSERT INTO outbound.campaign_deliveries
    (campaign_id, mailing_entry_id, tenant_id, claimed_at, result)
SELECT $1, cand.id, $2, now(), 'claimed' FROM cand
ON CONFLICT (campaign_id, mailing_entry_id) DO UPDATE
    SET result = 'claimed', claimed_at = now(), error = NULL, updated_at = now()
    WHERE outbound.campaign_deliveries.result = 'failed'
      AND outbound.campaign_deliveries.attempts < $5
RETURNING id AS delivery_id, mailing_entry_id AS entry_id
"""


async def db_drain_campaign(
    pool: asyncpg.Pool, tenant_id: str, campaign: dict, limit: int | None,
) -> list[dict]:
    """Atomically claim a batch of eligible entries for a campaign.

    Runs in one transaction: SELECT ... FOR UPDATE SKIP LOCKED over eligible entries
    (active, not-expired, matching selection, not-yet-delivered-by-this-campaign or a
    retryable failure), then claims each by inserting/updating a campaign_delivery.
    Returns the drained entries (delivery_id + entry payload) for the outbound skill.
    """
    batch_size  = campaign["batch_size"]
    eff_limit   = min(limit, batch_size) if limit else batch_size
    max_attempts = int((campaign.get("retry") or {}).get("max_attempts", 1) or 1)
    selection   = campaign.get("selection")
    selection_j = json.dumps(selection) if selection else None
    mailing_id  = UUID(campaign["mailing_id"])
    campaign_id = UUID(campaign["id"])

    # Declarative ordering (campaign.ordering) drives which entries the LIMIT picks AND
    # the order of the returned batch. Same clause in both queries (alias `e`).
    order_by  = _build_order_by(campaign.get("ordering"))
    drain_sql = _DRAIN_SQL_TMPL.replace("{order_by}", order_by)

    async with pool.acquire() as conn:
        async with conn.transaction():
            claimed = await conn.fetch(
                drain_sql,
                campaign_id, tenant_id, eff_limit, selection_j, max_attempts, mailing_id,
            )
            if not claimed:
                return []
            by_entry = {r["entry_id"]: str(r["delivery_id"]) for r in claimed}
            # The INSERT...RETURNING order is not guaranteed — re-fetch the claimed
            # entries with the SAME ordering so `drained[]` is in priority order.
            entries = await conn.fetch(
                f"SELECT e.id, e.customer_id, e.contacts, e.metadata "
                f"FROM outbound.mailing_entries e WHERE e.id = ANY($1::uuid[]) "
                f"ORDER BY {order_by}",
                list(by_entry.keys()),
            )

    drained: list[dict] = []
    for e in entries:
        drained.append({
            "delivery_id": by_entry[e["id"]],
            "entry_id":    str(e["id"]),
            "customer_id": e["customer_id"],
            "contacts":    _jl(e["contacts"]) or {},
            "metadata":    _jl(e["metadata"]) or {},
        })
    return drained


async def db_set_delivery_result(
    pool: asyncpg.Pool, tenant_id: str, delivery_id: str, data: dict,
) -> dict | None:
    """Record the outcome of a delivery after the collect. 'failed' bumps attempts;
    'contacted'/'responded' stamp contacted_at. Never silent — error is stored."""
    row = await pool.fetchrow(
        """
        UPDATE outbound.campaign_deliveries
        SET result          = $3,
            session_id      = COALESCE($4, session_id),
            root_session_id = COALESCE($5, root_session_id),
            error           = $6,
            contacted_at    = CASE WHEN $3 IN ('contacted','responded')
                                   THEN now() ELSE contacted_at END,
            attempts        = CASE WHEN $3 = 'failed' THEN attempts + 1 ELSE attempts END,
            updated_at      = now()
        WHERE id = $1 AND tenant_id = $2
        RETURNING *
        """,
        UUID(delivery_id), tenant_id, data["result"],
        data.get("session_id"), data.get("root_session_id"), data.get("error"),
    )
    return _row_to_delivery(row) if row else None


async def db_list_deliveries(
    pool: asyncpg.Pool, tenant_id: str, campaign_id: str, limit: int = 200,
) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT * FROM outbound.campaign_deliveries
        WHERE campaign_id = $1 AND tenant_id = $2
        ORDER BY created_at DESC
        LIMIT $3
        """,
        UUID(campaign_id), tenant_id, limit,
    )
    return [_row_to_delivery(r) for r in rows]


# ── Fase 2 — contact governance (fact × rule × decision) ──────────────────────

def _parse_window_seconds(w: Any) -> int:
    """Parse a contact window into seconds. Accepts an int (seconds) or a duration
    string: '30s' | '60m' | '24h' | '7d'. Falls back to 0 on garbage (never raises —
    a bad window degrades to 'no window', logged by the caller if needed)."""
    if w is None:
        return 0
    if isinstance(w, (int, float)):
        return int(w)
    s = str(w).strip().lower()
    try:
        if s.endswith("s"):
            return int(float(s[:-1]))
        if s.endswith("m"):
            return int(float(s[:-1]) * 60)
        if s.endswith("h"):
            return int(float(s[:-1]) * 3600)
        if s.endswith("d"):
            return int(float(s[:-1]) * 86400)
        return int(float(s))
    except ValueError:
        logger.warning("contact policy: unparseable window %r → treated as 0", w)
        return 0


def _row_to_policy(row: asyncpg.Record) -> dict[str, Any]:
    return {
        "id":               str(row["id"]),
        "tenant_id":        row["tenant_id"],
        "scope":            row["scope"],
        "scope_id":         row["scope_id"],
        "frequency_caps":   _jl(row["frequency_caps"]) or [],
        "quarantine_after": row["quarantine_after"],
        "channel_caps":     _jl(row["channel_caps"]) or {},
        "created_at":       _iso(row["created_at"]),
        "updated_at":       _iso(row["updated_at"]),
    }


async def db_list_policies(
    pool: asyncpg.Pool, tenant_id: str, scope: str | None = None,
) -> list[dict]:
    if scope:
        rows = await pool.fetch(
            "SELECT * FROM outbound.contact_policy WHERE tenant_id = $1 AND scope = $2 "
            "ORDER BY created_at DESC",
            tenant_id, scope,
        )
    else:
        rows = await pool.fetch(
            "SELECT * FROM outbound.contact_policy WHERE tenant_id = $1 ORDER BY created_at DESC",
            tenant_id,
        )
    return [_row_to_policy(r) for r in rows]


async def db_create_policy(pool: asyncpg.Pool, tenant_id: str, data: dict) -> dict:
    # UPSERT by (tenant, scope, scope_id) — one policy per scope. scope_id NULL for
    # tenant. NULL is not distinct in a UNIQUE by default, so guard the tenant case.
    scope    = data["scope"]
    scope_id = data.get("scope_id")
    row = await pool.fetchrow(
        """
        INSERT INTO outbound.contact_policy
            (tenant_id, scope, scope_id, frequency_caps, quarantine_after, channel_caps)
        VALUES ($1,$2,$3,$4::jsonb,$5,$6::jsonb)
        ON CONFLICT (tenant_id, scope, scope_id) DO UPDATE
            SET frequency_caps   = EXCLUDED.frequency_caps,
                quarantine_after = EXCLUDED.quarantine_after,
                channel_caps     = EXCLUDED.channel_caps,
                updated_at       = now()
        RETURNING *
        """,
        tenant_id, scope, scope_id,
        json.dumps(data.get("frequency_caps", [])),
        _window_to_text(data.get("quarantine_after")),
        json.dumps(data.get("channel_caps", {})),
    )
    return _row_to_policy(row)


def _window_to_text(w: Any) -> str | None:
    """Store windows as text ('24h', or '86400')."""
    if w is None:
        return None
    return str(w)


async def db_update_policy(pool: asyncpg.Pool, tenant_id: str, id: str, data: dict) -> dict | None:
    row = await pool.fetchrow(
        """
        UPDATE outbound.contact_policy
        SET frequency_caps   = COALESCE($3::jsonb, frequency_caps),
            quarantine_after = COALESCE($4, quarantine_after),
            channel_caps     = COALESCE($5::jsonb, channel_caps),
            updated_at       = now()
        WHERE id = $1 AND tenant_id = $2
        RETURNING *
        """,
        UUID(id), tenant_id,
        json.dumps(data["frequency_caps"]) if "frequency_caps" in data else None,
        _window_to_text(data["quarantine_after"]) if "quarantine_after" in data else None,
        json.dumps(data["channel_caps"]) if "channel_caps" in data else None,
    )
    return _row_to_policy(row) if row else None


async def db_delete_policy(pool: asyncpg.Pool, tenant_id: str, id: str) -> bool:
    result = await pool.execute(
        "DELETE FROM outbound.contact_policy WHERE id = $1 AND tenant_id = $2",
        UUID(id), tenant_id,
    )
    return result.endswith("1")


async def _resolve_effective_policy(
    conn: asyncpg.Connection, tenant_id: str, campaign_id: str | None,
) -> dict | None:
    """Campaign-scoped policy wins if present; else the tenant default; else None."""
    if campaign_id:
        row = await conn.fetchrow(
            "SELECT * FROM outbound.contact_policy "
            "WHERE tenant_id = $1 AND scope = 'campaign' AND scope_id = $2",
            tenant_id, campaign_id,
        )
        if row:
            return _row_to_policy(row)
    row = await conn.fetchrow(
        "SELECT * FROM outbound.contact_policy "
        "WHERE tenant_id = $1 AND scope = 'tenant' AND scope_id IS NULL",
        tenant_id,
    )
    return _row_to_policy(row) if row else None


async def _count_contacts(
    conn: asyncpg.Connection, tenant_id: str, customer_id: str,
    channel: str | None, since: datetime, at: datetime,
) -> tuple[int, datetime | None]:
    """Count contact_log rows for the customer in (since, at], optionally scoped to a
    channel. Returns (count, earliest_contacted_at_in_window)."""
    if channel is not None:
        row = await conn.fetchrow(
            "SELECT count(*) AS n, min(contacted_at) AS earliest FROM outbound.contact_log "
            "WHERE tenant_id = $1 AND customer_id = $2 AND channel = $3 "
            "AND contacted_at > $4 AND contacted_at <= $5",
            tenant_id, customer_id, channel, since, at,
        )
    else:
        # No unused parameter: Postgres rejects a passed-but-unreferenced $n
        # ("could not determine data type of parameter"). Renumber for this branch.
        row = await conn.fetchrow(
            "SELECT count(*) AS n, min(contacted_at) AS earliest FROM outbound.contact_log "
            "WHERE tenant_id = $1 AND customer_id = $2 "
            "AND contacted_at > $3 AND contacted_at <= $4",
            tenant_id, customer_id, since, at,
        )
    return int(row["n"]), row["earliest"]


async def db_contact_eligibility(
    pool: asyncpg.Pool, tenant_id: str, req: dict,
    calendar: Any = None, identity: Any = None,
) -> dict:
    """Evaluate the eligibility of an outbound contact and, when allowed and claim=true,
    write a contact_log fact (the window starts at SEND).

    Precedence (design §6): opt-out global (Fase 3b) → contact window (calendar, Fase 3a)
    → quarantine → frequency_caps → channel_caps. Any gate denies (no claim). No config →
    allowed. Never silent: the machine `reason` always names the gate that denied."""
    customer_id = req["customer_id"]
    channel     = req["channel"]
    campaign_id = req.get("campaign_id")
    claim       = req.get("claim", True)
    at: datetime = req.get("at") or datetime.now(timezone.utc)

    # Campaign attrs used by the gates (contact_calendar_id, transactional).
    cal_id: str | None = None
    transactional = False
    if campaign_id:
        crow = await pool.fetchrow(
            "SELECT contact_calendar_id, transactional FROM outbound.campaigns "
            "WHERE id = $1 AND tenant_id = $2",
            UUID(campaign_id), tenant_id,
        )
        if crow:
            cal_id        = crow["contact_calendar_id"]
            transactional = bool(crow["transactional"])

    # ── Portão de opt-out GLOBAL (Fase 3b) — MAIOR precedência, fora da transação ──
    # O `do_not_contact` vive no cadastro do cliente (Resolvedor de Identidade). Veto
    # ABSOLUTO por canal ou total — salvo campanha `transactional` (notificação legal/
    # obrigatória). Erro/ausência no cadastro → degrada para NÃO opted-out (barulhento).
    if identity is not None and not transactional:
        dnc = await identity.get_do_not_contact(tenant_id, customer_id)
        if dnc and (dnc.get("all") is True or channel in (dnc.get("channels") or [])):
            return {"allowed": False, "reason": "opt_out",
                    "retry_after": None, "claimed": False}

    # ── Portão de janela de contato (Fase 3a) — fora da transação ────────────────
    # A campanha aponta um calendar_id; o calendar-api é a única autoridade do "quando".
    # Fechado/feriado → nega `outside_window` (sem claim); `retry_after` = até o próximo
    # horário. Erro do calendar / sem calendar → degrada para ABERTO (barulhento no log,
    # como o scheduler) — uma checagem perdida não bloqueia um contato em silêncio.
    if cal_id and calendar is not None:
        status = await calendar.is_open_status(cal_id, at)
        if status in ("closed", "holiday"):
            retry_after = None
            nxt = await calendar.next_open_slot(cal_id, at)
            if nxt is not None:
                retry_after = max(0, int((nxt - at).total_seconds()))
            return {"allowed": False, "reason": "outside_window",
                    "retry_after": retry_after, "claimed": False}

    async with pool.acquire() as conn:
        async with conn.transaction():
            policy = await _resolve_effective_policy(conn, tenant_id, campaign_id)

            reason: str | None = None
            retry_after: int | None = None

            if policy is not None:
                # 1) quarantine_after — no contact of ANY channel within the window.
                q_secs = _parse_window_seconds(policy.get("quarantine_after"))
                if reason is None and q_secs > 0:
                    since = at - timedelta(seconds=q_secs)
                    n, earliest = await _count_contacts(conn, tenant_id, customer_id, None, since, at)
                    if n >= 1:
                        reason = "quarantine"
                        if earliest is not None:
                            retry_after = max(0, q_secs - int((at - earliest).total_seconds()))

                # 2) frequency_caps — global or per_channel.
                if reason is None:
                    for cap in policy.get("frequency_caps", []):
                        w = _parse_window_seconds(cap.get("window"))
                        if w <= 0:
                            continue
                        ch = channel if cap.get("per_channel") else None
                        since = at - timedelta(seconds=w)
                        n, earliest = await _count_contacts(conn, tenant_id, customer_id, ch, since, at)
                        if n >= int(cap["max"]):
                            reason = "frequency_cap"
                            if earliest is not None:
                                retry_after = max(0, w - int((at - earliest).total_seconds()))
                            break

                # 3) channel_caps — per-channel limit for THIS channel.
                if reason is None:
                    cc = (policy.get("channel_caps") or {}).get(channel)
                    if cc:
                        w = _parse_window_seconds(cc.get("window"))
                        if w > 0:
                            since = at - timedelta(seconds=w)
                            n, earliest = await _count_contacts(conn, tenant_id, customer_id, channel, since, at)
                            if n >= int(cc["max"]):
                                reason = "channel_cap"
                                if earliest is not None:
                                    retry_after = max(0, w - int((at - earliest).total_seconds()))

            allowed = reason is None
            claimed = False
            if allowed and claim:
                await conn.execute(
                    "INSERT INTO outbound.contact_log "
                    "(tenant_id, customer_id, channel, campaign_id, contacted_at, result) "
                    "VALUES ($1,$2,$3,$4,$5,'sent')",
                    tenant_id, customer_id, channel,
                    UUID(campaign_id) if campaign_id else None, at,
                )
                claimed = True

    return {"allowed": allowed, "reason": reason, "retry_after": retry_after, "claimed": claimed}


async def db_unsubscribe(
    pool: asyncpg.Pool, tenant_id: str, data: dict, identity: Any = None,
) -> dict:
    """Suppression. Two scopes:
      - `mailing` (default): flip a customer's entries to 'unsubscribed' (the drain
        excludes non-active). mailing_id omitted = all of the customer's mailings.
      - `global` (Fase 3b): write `do_not_contact` in the customer cadastro (via the
        Identity Resolver) — a veto enforced by the opt_out gate at eligibility. channel
        omitted/'all' → {all:true}; a specific channel → {channels:[channel]}.
    """
    customer_id = data["customer_id"]
    mailing_id  = data.get("mailing_id")
    channel     = data.get("channel")
    scope       = data.get("scope", "mailing")

    if scope == "global":
        dnc = {"all": True} if (not channel or channel == "all") else {"channels": [channel]}
        ok = await identity.set_do_not_contact(tenant_id, customer_id, dnc) if identity else False
        return {"scope": "global", "do_not_contact": dnc, "do_not_contact_set": ok}

    if mailing_id:
        result = await pool.execute(
            "UPDATE outbound.mailing_entries SET status = 'unsubscribed', updated_at = now() "
            "WHERE tenant_id = $1 AND customer_id = $2 AND mailing_id = $3 AND status <> 'unsubscribed'",
            tenant_id, customer_id, UUID(mailing_id),
        )
    else:
        result = await pool.execute(
            "UPDATE outbound.mailing_entries SET status = 'unsubscribed', updated_at = now() "
            "WHERE tenant_id = $1 AND customer_id = $2 AND status <> 'unsubscribed'",
            tenant_id, customer_id,
        )
    # result like 'UPDATE N'
    try:
        n = int(result.split()[-1])
    except (ValueError, IndexError):
        n = 0
    return {"scope": "mailing", "unsubscribed": n}
