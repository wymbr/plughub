"""
admission.py — Hybrid session admission control (queue-attended-model, Fase B).

Model (docs/arcos/queue-attended-model.md § Admissão híbrida):
  - Pools with `session_reservation` get a dedicated slice (cap AND guarantee),
    carved out of the installation total.
  - Pools without reservation draw from the shared bucket:
        shared_limit = max_session_total − Σ session_reservation
  - Billing is on the total only; reservations are carving, never billable items.

Mechanics:
  - Counters are Redis SETs of session_id (SCARD = concurrency) — idempotent on
    re-publish (drain, crash-recovery) and self-healing via the reconciler.
  - Admission runs on EVERY routing request against the requested pool's bucket.
    Cross-pool escalation = bucket migration (SADD+check target, then SREM origin;
    target full → rejection, session stays in the origin bucket and the caller's
    fallback chain applies).
  - Release is performed by the periodic reconciler: members whose
    `session:{id}:closed` marker exists are removed (~60s lag, acceptable for an
    admission gauge). Member-key TTL is the backstop.

Keys:
  {t}:admission:reserved:{pool_id}   SET — sessions admitted into the reservation
  {t}:admission:shared               SET — sessions admitted into the shared bucket
  {t}:admission:member:{session_id}  STRING — current bucket key (TTL 7d)
  {t}:quota:max_concurrent_sessions  STRING — installation total (operator/pricing)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import redis.asyncio as aioredis

from .models import PoolConfig
from .registry import PoolRegistry

logger = logging.getLogger("plughub.routing.admission")

_MEMBER_TTL_S          = 604_800   # 7 days — same horizon as session:closed markers
_RESERVATION_CACHE_TTL = 30.0      # seconds — Σ reservations per tenant


def _decode(value) -> str:
    if value is None:
        return ""
    return value.decode() if isinstance(value, bytes) else str(value)


@dataclass
class AdmissionDecision:
    admitted: bool
    cause:    str = ""          # "reservation_full" | "shared_full"
    pool_id:  str = ""
    limit:    int | None = None
    current:  int | None = None


class AdmissionController:
    def __init__(self, redis_client: aioredis.Redis, pool_registry: PoolRegistry) -> None:
        self._redis = redis_client
        self._pools = pool_registry
        # tenant_id → (expires_at_monotonic, sum_of_reservations)
        self._reservation_cache: dict[str, tuple[float, int]] = {}

    # ── Admission ──────────────────────────────────────────────────────────────

    async def admit(
        self,
        tenant_id:  str,
        session_id: str,
        pool:       PoolConfig | None,
        pool_id:    str,
    ) -> AdmissionDecision:
        """
        Admits (or migrates) a session into the requested pool's bucket.
        Idempotent: re-admitting into the current bucket is a no-op.
        """
        reservation = pool.session_reservation if pool else None
        if reservation:
            bucket = f"{tenant_id}:admission:reserved:{pool_id}"
            limit: int | None = reservation
            cause = "reservation_full"
        else:
            bucket = f"{tenant_id}:admission:shared"
            limit  = await self._shared_limit(tenant_id)
            cause  = "shared_full"

        member_key = f"{tenant_id}:admission:member:{session_id}"
        prev = _decode(await self._redis.get(member_key))
        if prev == bucket:
            return AdmissionDecision(admitted=True)   # already admitted here

        added = await self._redis.sadd(bucket, session_id)
        count = await self._redis.scard(bucket)
        if limit is not None and count > limit:
            if added:
                await self._redis.srem(bucket, session_id)   # rollback
            if prev:
                # Mid-session migration (escalation/transfer) into a full bucket:
                # NEVER outage-close an active session — keep the origin bucket
                # attribution and let routing proceed (target pool is full, the
                # contact will queue). Proper mid-session handling = fallback
                # chain (Fase E). Door-entry rejections (no prev) DO outage.
                logger.warning(
                    "admission migration rejected (fail-open, keeps origin bucket): "
                    "tenant=%s session=%s pool=%s cause=%s current=%d limit=%d",
                    tenant_id, session_id, pool_id, cause, count, limit,
                )
                return AdmissionDecision(admitted=True)
            logger.warning(
                "admission rejected: tenant=%s session=%s pool=%s cause=%s "
                "current=%d limit=%d",
                tenant_id, session_id, pool_id, cause, count, limit,
            )
            return AdmissionDecision(
                admitted=False, cause=cause, pool_id=pool_id,
                limit=limit, current=count,
            )

        await self._redis.set(member_key, bucket, ex=_MEMBER_TTL_S)
        if prev and prev != bucket:
            # Migration: session moved pools — release the origin bucket slot.
            await self._redis.srem(prev, session_id)
        return AdmissionDecision(admitted=True)

    async def _shared_limit(self, tenant_id: str) -> int | None:
        """shared = total − Σ reservations. None = unlimited (no total configured)."""
        raw = _decode(await self._redis.get(f"{tenant_id}:quota:max_concurrent_sessions"))
        if not raw:
            return None
        try:
            total = int(float(raw))
        except ValueError:
            return None
        if total <= 0:
            return None
        return max(0, total - await self._sum_reservations(tenant_id))

    async def _sum_reservations(self, tenant_id: str) -> int:
        now = time.monotonic()
        cached = self._reservation_cache.get(tenant_id)
        if cached and cached[0] > now:
            return cached[1]
        total = 0
        try:
            for pool in await self._pools.list_pools(tenant_id):
                if pool.session_reservation:
                    total += int(pool.session_reservation)
        except Exception as exc:
            logger.warning("Could not sum reservations tenant=%s — %s", tenant_id, exc)
        self._reservation_cache[tenant_id] = (now + _RESERVATION_CACHE_TTL, total)
        return total

    # ── Reconciliation (release) ───────────────────────────────────────────────

    async def reconcile(self) -> int:
        """
        Removes closed sessions from all admission buckets.
        A member is released when `session:{id}:closed` exists (set by the
        bridge/mcp-server on every close, TTL 7d). Returns released count.
        """
        released = 0
        try:
            buckets: list[str] = []
            async for key in self._redis.scan_iter(match="*:admission:reserved:*", count=200):
                buckets.append(_decode(key))
            async for key in self._redis.scan_iter(match="*:admission:shared", count=200):
                buckets.append(_decode(key))

            for bucket in buckets:
                tenant_id = bucket.split(":admission:", 1)[0]
                async for member in self._redis.sscan_iter(bucket, count=200):
                    session_id = _decode(member)
                    closed = await self._redis.exists(f"session:{session_id}:closed")
                    if closed:
                        await self._redis.srem(bucket, session_id)
                        await self._redis.delete(
                            f"{tenant_id}:admission:member:{session_id}"
                        )
                        released += 1
        except Exception as exc:
            logger.warning("admission reconcile failed — %s", exc)
        if released:
            logger.info("admission reconcile: released %d closed session(s)", released)
        return released
