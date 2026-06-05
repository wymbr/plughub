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

Capacity-governance item 7a (2026-06-05) — atribuição do shared por pool:
  {t}:admission:shared_pools  HASH {session_id → pool_id} — índice de atribuição
  das sessões no bucket compartilhado (o SET continua sendo O limite; o HASH
  alimenta Monitor/Analytics com fatias exatas por pool). HSET/HDEL nos mesmos
  pontos do member key; higiene no reconciler (auto-curável).

Capacity-governance item 2 / Etapa 2 (2026-06-05) — gate por tipo:
  {t}:admission:kind:ai                  SET — sessions em pools agent_kind='ai'
  {t}:admission:kind_member:{session_id} STRING — "ai"|"human" (TTL 7d)
  {t}:quota:capacity:ai_agent            STRING — C_ai (pricing quota sync)
Sessões entrando em pool 'ai' respeitam C_ai (rejeição cause="quota" → outage,
visível na demanda reprimida como "Teto contratado"). Humano NÃO é gateado por
sessão — o gate humano é por logins concorrentes (registerHumanAgent ≤ C_human).
Fail-open: pool sem agent_kind ou chave C_ai ausente → sem gate por tipo.
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

        # ── Gate por tipo (item 2 / Etapa 2): sessões em pools 'ai' ≤ C_ai ──────
        kind        = (pool.agent_kind if pool else None) or None
        kind_member = f"{tenant_id}:admission:kind_member:{session_id}"
        prev_kind   = _decode(await self._redis.get(kind_member))
        kind_added  = False
        kind_set    = f"{tenant_id}:admission:kind:ai"
        if kind == "ai" and prev_kind != "ai":
            c_ai = await self._type_limit(tenant_id, "ai_agent")
            if c_ai is not None:
                kind_added = bool(await self._redis.sadd(kind_set, session_id))
                kcount = await self._redis.scard(kind_set)
                if kcount > c_ai:
                    if kind_added:
                        await self._redis.srem(kind_set, session_id)
                        kind_added = False
                    if not prev:
                        # Door-entry beyond contracted AI capacity → outage "quota"
                        logger.warning(
                            "admission rejected (type gate): tenant=%s session=%s pool=%s "
                            "kind=ai current=%d limit=%d",
                            tenant_id, session_id, pool_id, kcount, c_ai,
                        )
                        return AdmissionDecision(
                            admitted=False, cause="quota", pool_id=pool_id,
                            limit=c_ai, current=kcount,
                        )
                    # Mid-session migration into saturated AI capacity: fail-open
                    # (same rule as bucket migration — never outage an active session).
                    # Keep the ORIGIN kind attribution (no half-state in tracking).
                    logger.warning(
                        "admission type-gate migration fail-open: tenant=%s session=%s "
                        "pool=%s current=%d limit=%d", tenant_id, session_id, pool_id, kcount, c_ai,
                    )
                    kind = prev_kind or None
            else:
                # Sem C_ai configurado → sem gate, mas mantém o tracking de kind.
                kind_added = bool(await self._redis.sadd(kind_set, session_id))

        if prev == bucket:
            # Already admitted here — commit kind tracking (idempotent re-publish).
            await self._commit_kind(tenant_id, session_id, kind, prev_kind, kind_member, kind_set)
            await self._commit_shared_attribution(tenant_id, session_id, bucket, pool_id)
            return AdmissionDecision(admitted=True)

        added = await self._redis.sadd(bucket, session_id)
        count = await self._redis.scard(bucket)
        if limit is not None and count > limit:
            if added:
                await self._redis.srem(bucket, session_id)   # rollback
            if kind_added:
                await self._redis.srem(kind_set, session_id)  # rollback type tracking
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
        await self._commit_kind(tenant_id, session_id, kind, prev_kind, kind_member, kind_set)
        await self._commit_shared_attribution(tenant_id, session_id, bucket, pool_id)
        return AdmissionDecision(admitted=True)

    async def _commit_shared_attribution(
        self, tenant_id: str, session_id: str, bucket: str, pool_id: str
    ) -> None:
        """Item 7a: mantém o HASH {sid→pool} espelhando a permanência no shared."""
        try:
            hash_key = f"{tenant_id}:admission:shared_pools"
            if bucket.endswith(":admission:shared"):
                await self._redis.hset(hash_key, session_id, pool_id)
            else:
                await self._redis.hdel(hash_key, session_id)   # reserved/migrou p/ fora
        except Exception as exc:
            logger.warning(
                "shared attribution failed (reconciler heals) session=%s — %s",
                session_id, exc,
            )

    async def _commit_kind(
        self,
        tenant_id:   str,
        session_id:  str,
        kind:        str | None,
        prev_kind:   str,
        kind_member: str,
        kind_set:    str,
    ) -> None:
        """Atualiza o tracking de kind após admissão bem-sucedida (item 2)."""
        if kind is None:
            return  # pool sem tipagem → não mexe no tracking (conservador)
        await self._redis.set(kind_member, kind, ex=_MEMBER_TTL_S)
        if prev_kind == "ai" and kind != "ai":
            await self._redis.srem(kind_set, session_id)

    async def has_headroom(
        self,
        tenant_id:           str,
        pool_id:             str,
        session_reservation: int | None = None,
        agent_kind:          str | None = None,
    ) -> bool:
        """
        Checagem READ-ONLY de vaga na admissão (fila de sistema, Fase A):
        os drains só re-publicam sessão NÃO-ADMITIDA (fila muda/overflow)
        quando há vaga no contrato — sem isso, re-publicar com C cheio vira
        churn rejeita→re-enfileira a cada ciclo. Espelha a lógica do admit().
        """
        try:
            if session_reservation:
                count = await self._redis.scard(
                    f"{tenant_id}:admission:reserved:{pool_id}"
                )
                if count >= session_reservation:
                    return False
            else:
                limit = await self._shared_limit(tenant_id)
                if limit is not None:
                    count = await self._redis.scard(f"{tenant_id}:admission:shared")
                    if count >= limit:
                        return False
            if agent_kind == "ai":
                c_ai = await self._type_limit(tenant_id, "ai_agent")
                if c_ai is not None:
                    kcount = await self._redis.scard(f"{tenant_id}:admission:kind:ai")
                    if kcount >= c_ai:
                        return False
            return True
        except Exception as exc:
            logger.warning("has_headroom failed (fail-open) tenant=%s — %s", tenant_id, exc)
            return True

    async def release(self, tenant_id: str, session_id: str) -> None:
        """
        Fila de sistema (system-queue.md Fase A): libera os slots de admissão de
        uma sessão que entrou em fila MUDA — ela deixa de debitar C enquanto
        espera (isenção do tier gratuito). A re-admissão acontece naturalmente
        quando o drain re-publica o contato (admit roda em todo inbound).
        """
        member_key = f"{tenant_id}:admission:member:{session_id}"
        prev = _decode(await self._redis.get(member_key))
        if prev:
            await self._redis.srem(prev, session_id)
            await self._redis.delete(member_key)
        kind_member = f"{tenant_id}:admission:kind_member:{session_id}"
        prev_kind = _decode(await self._redis.get(kind_member))
        if prev_kind:
            if prev_kind == "ai":
                await self._redis.srem(f"{tenant_id}:admission:kind:ai", session_id)
            await self._redis.delete(kind_member)
        # Item 7a: atribuição do shared sai junto.
        await self._redis.hdel(f"{tenant_id}:admission:shared_pools", session_id)

    async def _type_limit(self, tenant_id: str, resource_type: str) -> int | None:
        """C por tipo ({t}:quota:capacity:{type}, pricing quota sync). None = sem gate."""
        raw = _decode(await self._redis.get(f"{tenant_id}:quota:capacity:{resource_type}"))
        if not raw:
            return None
        try:
            v = int(float(raw))
        except ValueError:
            return None
        return v if v > 0 else None

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
            # Item 2: type-gate sets follow the same closed-session release.
            async for key in self._redis.scan_iter(match="*:admission:kind:ai", count=200):
                buckets.append(_decode(key))
            # Fila de sistema (Fase A): backstop do buffer grátis — remove
            # sessões fechadas que os drains não limparam (segmento sintético
            # de abandono é emitido pelos drains; aqui é só higiene).
            async for key in self._redis.scan_iter(match="*:queue:unadmitted", count=200):
                tenant_id = _decode(key).split(":queue:", 1)[0]
                async for member in self._redis.sscan_iter(_decode(key), count=200):
                    sid = _decode(member)
                    if await self._redis.exists(f"session:{sid}:closed"):
                        await self._redis.srem(_decode(key), sid)
                        await self._redis.delete(f"{tenant_id}:queue:first_queued:{sid}")
                        released += 1

            for bucket in buckets:
                tenant_id = bucket.split(":admission:", 1)[0]
                is_kind   = ":admission:kind:" in bucket
                is_shared = bucket.endswith(":admission:shared")
                async for member in self._redis.sscan_iter(bucket, count=200):
                    session_id = _decode(member)
                    closed = await self._redis.exists(f"session:{session_id}:closed")
                    if closed:
                        await self._redis.srem(bucket, session_id)
                        await self._redis.delete(
                            f"{tenant_id}:admission:kind_member:{session_id}"
                            if is_kind else
                            f"{tenant_id}:admission:member:{session_id}"
                        )
                        if is_shared:
                            await self._redis.hdel(
                                f"{tenant_id}:admission:shared_pools", session_id
                            )
                        released += 1
                # Item 7a — higiene do HASH de atribuição: entradas cujo sid já
                # não está no SET (crash entre os dois writes) são removidas,
                # garantindo Σ fatias == SCARD(shared).
                if is_shared:
                    try:
                        hash_key = f"{tenant_id}:admission:shared_pools"
                        for sid_raw in await self._redis.hkeys(hash_key):
                            sid = _decode(sid_raw)
                            if not await self._redis.sismember(bucket, sid):
                                await self._redis.hdel(hash_key, sid)
                    except Exception:
                        pass
        except Exception as exc:
            logger.warning("admission reconcile failed — %s", exc)
        if released:
            logger.info("admission reconcile: released %d closed session(s)", released)
        return released
