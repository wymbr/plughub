"""
identity/index.py — IdentityIndex: Lookup 1 (resolução) + Lookup 2 (pendências).

Slice 1 é Redis-only (sem cadastro durável). Chaves:

  {t}:identity:{kind}:{value_hash}      → customer_id        (Lookup 1)
  {t}:customer:prospect:{customer_id}   → JSON prospect       (TTL deslizante)
  {t}:pending_by_customer:{customer_id} → HASH sid→PendingEntry (Lookup 2, TTL)

Nenhuma PII em claro nas chaves (§ normalize.hash_anchor). Validação forte de
identidade (identity_verify na retaguarda) está fora da Fase A — aqui a âncora é
tratada como "origem/fraca": deriva/provisiona o customer_id nativo.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as aioredis

from .normalize import hash_anchor, kind_confidence, normalize_anchor

logger = logging.getLogger("plughub.channel-gateway.identity")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_customer_id() -> str:
    # nativo, canônico, imutável — nasce aqui e é reusado na promoção ao PG (Slice 2).
    return "cus_" + uuid.uuid4().hex[:24]


@dataclass
class CustomerRef:
    customer_id: str
    status:      str            # prospect | identified
    matched_by:  str            # existing | provisioned | ambiguous | none
    confidence:  float


@dataclass
class PendingEntry:
    session_id:      str
    customer_id:     str
    resume_token:    str
    pool:            str
    skill_id:        str | None = None
    suspended_at:    str = field(default_factory=_now_iso)
    expires_at:      str | None = None
    policy:          str = "offer"          # offer | auto
    intent:          str | None = None
    context_preview: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @staticmethod
    def from_json(raw: str | bytes) -> "PendingEntry":
        d = json.loads(raw)
        return PendingEntry(**d)


class IdentityIndex:
    """
    Índice de identidade sobre Redis. Instanciado pelo WebhookAdapter (reusa o
    mesmo cliente Redis). `salt` é segredo (env); `prospect_ttl_s` /
    `resolution_index_ttl_s` são tuning (defaults; config-api no Slice 2).
    """

    def __init__(
        self,
        redis:                  aioredis.Redis,
        salt:                   str,
        prospect_ttl_s:         int = 2_592_000,   # 30d
        resolution_index_ttl_s: int = 2_592_000,   # 30d
        db_pool:                Any = None,        # asyncpg.Pool | None (Slice 2 durability)
    ) -> None:
        self._redis = redis
        self._salt  = salt
        self._prospect_ttl_s  = prospect_ttl_s
        self._index_ttl_s     = resolution_index_ttl_s
        self._db    = db_pool               # None → Redis-only (Slice 1 behaviour)

    # ── keys ──────────────────────────────────────────────────────────────────

    def _identity_key(self, tenant_id: str, kind: str, value_hash: str) -> str:
        return f"{tenant_id}:identity:{kind}:{value_hash}"

    def _prospect_key(self, tenant_id: str, customer_id: str) -> str:
        return f"{tenant_id}:customer:prospect:{customer_id}"

    def _pending_key(self, tenant_id: str, customer_id: str) -> str:
        return f"{tenant_id}:pending_by_customer:{customer_id}"

    def _resume_tokens_key(self, tenant_id: str) -> str:
        return f"{tenant_id}:resume_tokens"

    # ── Lookup 1 — resolução / provisionamento ─────────────────────────────────

    async def resolve_or_provision(
        self,
        tenant_id: str,
        anchors:   list[dict[str, str]],   # [{kind, value}] crus (loopback; hash server-side)
        provision: bool = True,
    ) -> CustomerRef:
        """
        Resolve as âncoras a um customer_id. Se nenhuma casar e provision=True,
        cria um prospect efêmero (customer_id nativo) e indexa as âncoras.

        Desambiguação: cada âncora que casa contribui um candidato com a confiança
        do seu tipo; vence a maior confiança. Empate entre customer_ids diferentes
        na maior confiança → matched_by="ambiguous" (o fluxo decide 'ask').
        """
        # candidatos: customer_id → melhor confiança observada
        candidates: dict[str, float] = {}
        valid_anchors: list[tuple[str, str, str]] = []   # (kind, value_hash, normalized-not-stored)

        for a in anchors:
            kind  = a.get("kind", "")
            value = a.get("value", "")
            try:
                vh = hash_anchor(self._salt, kind, value)
            except ValueError:
                continue
            valid_anchors.append((kind, vh, ""))
            cid = await self._redis.get(self._identity_key(tenant_id, kind, vh))
            if cid:
                cid_s = cid.decode() if isinstance(cid, bytes) else cid
                conf  = kind_confidence(kind)
                if cid_s not in candidates or conf > candidates[cid_s]:
                    candidates[cid_s] = conf

        if candidates:
            top_conf = max(candidates.values())
            winners  = [cid for cid, c in candidates.items() if c == top_conf]
            if len(winners) == 1:
                return CustomerRef(winners[0], status="identified",
                                   matched_by="existing", confidence=top_conf)
            # colisão real: mesma confiança, ids diferentes
            return CustomerRef(winners[0], status="identified",
                               matched_by="ambiguous", confidence=top_conf)

        # Redis miss → fallback ao cadastro durável (Slice 2): um cliente já
        # promovido ao PG pode ter saído do índice Redis (TTL/cold). Reidrata o
        # índice quando acha, para os próximos lookups voltarem a ser O(1) no Redis.
        pg_hit = await self._pg_resolve(tenant_id, valid_anchors)
        if pg_hit:
            customer_id, conf = pg_hit
            for (kind, vh, _n) in valid_anchors:
                await self._redis.set(
                    self._identity_key(tenant_id, kind, vh), customer_id, ex=self._index_ttl_s,
                )
            return CustomerRef(customer_id, status="identified",
                               matched_by="durable", confidence=conf)

        if not provision:
            return CustomerRef("", status="none", matched_by="none", confidence=0.0)

        # provisiona prospect efêmero + indexa âncoras
        customer_id = _new_customer_id()
        await self._redis.set(
            self._prospect_key(tenant_id, customer_id),
            json.dumps({
                "customer_id": customer_id,
                "status":      "prospect",
                "created_at":  _now_iso(),
                "kinds":       sorted({k for (k, _vh, _n) in valid_anchors}),
            }),
            ex=self._prospect_ttl_s,
        )
        for (kind, vh, _n) in valid_anchors:
            await self._redis.set(
                self._identity_key(tenant_id, kind, vh),
                customer_id,
                ex=self._index_ttl_s,
            )
        conf = max((kind_confidence(k) for (k, _vh, _n) in valid_anchors), default=0.0)
        return CustomerRef(customer_id, status="prospect",
                           matched_by="provisioned", confidence=conf)

    # ── Lookup 2 — pendências por cliente ──────────────────────────────────────

    async def write_pending(
        self, tenant_id: str, customer_id: str, entry: PendingEntry, ttl_s: int,
    ) -> None:
        key = self._pending_key(tenant_id, customer_id)
        await self._redis.hset(key, entry.session_id, entry.to_json())
        # renova o TTL do hash para o maior horizonte visto
        await self._redis.expire(key, ttl_s)

    async def find_pending(
        self, tenant_id: str, customer_id: str,
    ) -> list[PendingEntry]:
        """
        Retorna pendências vivas. Limpa entradas cujo resume_token não está mais
        em {t}:resume_tokens (consumido/expirado → stale).
        """
        key = self._pending_key(tenant_id, customer_id)
        raw = await self._redis.hgetall(key)
        if not raw:
            return []
        tokens_key = self._resume_tokens_key(tenant_id)
        live: list[PendingEntry] = []
        for field_id, value in raw.items():
            sid = field_id.decode() if isinstance(field_id, bytes) else field_id
            try:
                entry = PendingEntry.from_json(value)
            except Exception:
                await self._redis.hdel(key, sid)
                continue
            token_alive = await self._redis.hget(tokens_key, entry.resume_token)
            if not token_alive:
                await self._redis.hdel(key, sid)
                continue
            live.append(entry)
        # ordena mais recente primeiro
        live.sort(key=lambda e: e.suspended_at, reverse=True)
        return live

    async def consume_pending(
        self, tenant_id: str, customer_id: str, session_id: str,
    ) -> None:
        await self._redis.hdel(self._pending_key(tenant_id, customer_id), session_id)

    # ── Durabilidade (Slice 2 — PG schema `identity`) ──────────────────────────

    async def ensure_schema(self) -> None:
        """Cria o schema `identity` e tabelas (idempotente). No-op sem db_pool."""
        if self._db is None:
            return
        async with self._db.acquire() as conn:
            await conn.execute(_IDENTITY_SCHEMA_DDL)
        logger.info("IdentityIndex: PG schema `identity` ensured")

    async def _pg_resolve(
        self, tenant_id: str, valid_anchors: list[tuple[str, str, str]],
    ) -> tuple[str, float] | None:
        """Lookup 1 no PG durável: (kind, value_hash) → (customer_id, confidence)."""
        if self._db is None or not valid_anchors:
            return None
        best: tuple[str, float] | None = None
        async with self._db.acquire() as conn:
            for (kind, vh, _n) in valid_anchors:
                row = await conn.fetchrow(
                    """
                    SELECT customer_id, confidence
                      FROM identity.customer_secondary_keys
                     WHERE tenant_id = $1 AND kind = $2 AND value_hash = $3
                     LIMIT 1
                    """,
                    tenant_id, kind, vh,
                )
                if row:
                    conf = float(row["confidence"] or kind_confidence(kind))
                    if best is None or conf > best[1]:
                        best = (row["customer_id"], conf)
        return best

    async def promote_to_durable(
        self, tenant_id: str, customer_id: str, anchors: list[dict[str, str]],
        status: str = "prospect",
    ) -> None:
        """
        Promove um cliente efêmero ao PG (gatilho concreto — ex.: registro de
        pendência). Upsert idempotente reusando o mesmo customer_id nativo, e
        grava as chaves secundárias hasheadas. No-op sem db_pool.
        """
        if self._db is None or not customer_id:
            return
        rows: list[tuple[str, str, float]] = []
        for a in anchors:
            kind, value = a.get("kind", ""), a.get("value", "")
            try:
                vh = hash_anchor(self._salt, kind, value)
            except ValueError:
                continue
            rows.append((kind, vh, kind_confidence(kind)))
        async with self._db.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO identity.customers (customer_id, tenant_id, status)
                VALUES ($1, $2, $3)
                ON CONFLICT (customer_id) DO UPDATE SET updated_at = NOW()
                """,
                customer_id, tenant_id, status,
            )
            for (kind, vh, conf) in rows:
                await conn.execute(
                    """
                    INSERT INTO identity.customer_secondary_keys
                        (tenant_id, kind, value_hash, customer_id, confidence)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (tenant_id, kind, value_hash)
                        DO UPDATE SET customer_id = EXCLUDED.customer_id,
                                      confidence  = GREATEST(identity.customer_secondary_keys.confidence, EXCLUDED.confidence)
                    """,
                    tenant_id, kind, vh, customer_id, conf,
                )
        logger.info("IdentityIndex: promoted customer=%s to durable (keys=%d)", customer_id, len(rows))


_IDENTITY_SCHEMA_DDL = """
CREATE SCHEMA IF NOT EXISTS identity;

CREATE TABLE IF NOT EXISTS identity.customers (
    customer_id  TEXT PRIMARY KEY,
    tenant_id    TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'prospect',   -- prospect | identified | merged
    merged_into  TEXT,
    attributes   JSONB NOT NULL DEFAULT '{}',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_identity_customers_tenant ON identity.customers (tenant_id);

CREATE TABLE IF NOT EXISTS identity.customer_secondary_keys (
    tenant_id    TEXT NOT NULL,
    kind         TEXT NOT NULL,
    value_hash   TEXT NOT NULL,
    customer_id  TEXT NOT NULL,
    confidence   DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    verified_at  TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, kind, value_hash)
);
CREATE INDEX IF NOT EXISTS idx_identity_seckeys_customer ON identity.customer_secondary_keys (customer_id);

CREATE TABLE IF NOT EXISTS identity.customer_external_refs (
    tenant_id    TEXT NOT NULL,
    system       TEXT NOT NULL,
    external_id  TEXT NOT NULL,
    customer_id  TEXT NOT NULL,
    confidence   DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    resolved_at  TIMESTAMPTZ,
    PRIMARY KEY (tenant_id, system, external_id)
);
CREATE INDEX IF NOT EXISTS idx_identity_extrefs_customer ON identity.customer_external_refs (customer_id);

CREATE TABLE IF NOT EXISTS identity.customer_merges (
    tenant_id     TEXT NOT NULL,
    from_customer TEXT NOT NULL,
    into_customer TEXT NOT NULL,
    merged_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, from_customer)
);
"""
