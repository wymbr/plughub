"""
scheduler.py
Camada 1 — timer substrate (Redis sorted-set + single poller) + boot re-hydration.

Keys (deviation from ADR's per-tenant `{tenant}:agenda_timers`, justified: a single
global poller + agenda_id is a globally-unique UUID, so one ZSET suffices; the tenant
travels in the per-timer hash):

    scheduler:timers            ZSET   member = agenda_id, score = deadline epoch (s)
    scheduler:timer:{agenda_id} HASH   { tenant_id, scheduled_for }

One pending timer per agenda at a time — matches "compute only the NEXT occurrence".
Re-arming a recurring agenda = ZADD the same member with the new score.

The "what to do when a timer fires" is a callback (`fire_cb`) injected by the dispatch
layer (task 5). With no callback (task 3), a due timer is observed + consumed
(next_fire_at cleared) — no dispatch, no state fabrication.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable

import redis.asyncio as aioredis

from .db import db_get_agenda, db_list_active_agendas, db_set_next_fire_at

logger = logging.getLogger("plughub.scheduler.timer")

_TIMERS_KEY = "scheduler:timers"


def _timer_hash(agenda_id: str) -> str:
    return f"scheduler:timer:{agenda_id}"


def _parse_dt(iso: str) -> datetime:
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# fire_cb(agenda: dict, scheduled_for_iso: str) -> Awaitable[None]
FireCallback = Callable[[dict, str], Awaitable[None]]


class Scheduler:
    def __init__(self, pool, settings, fire_cb: FireCallback | None = None) -> None:
        self.pool = pool
        self.settings = settings
        self.redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        self._fire_cb = fire_cb
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    def set_fire_callback(self, cb: FireCallback) -> None:
        self._fire_cb = cb

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        await self.rehydrate()
        self._task = asyncio.create_task(self._run())
        logger.info("scheduler poller started (interval=%ss)", self.settings.poll_interval_s)

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            await self._task
        try:
            await self.redis.aclose()
        except Exception:
            pass

    # ── Arm / disarm (called by the router on create/update/pause/cancel/delete) ─

    async def arm(self, agenda_id: str, tenant_id: str, deadline_iso: str) -> None:
        score = _parse_dt(deadline_iso).timestamp()
        await self.redis.zadd(_TIMERS_KEY, {agenda_id: score})
        await self.redis.hset(
            _timer_hash(agenda_id),
            mapping={"tenant_id": tenant_id, "scheduled_for": deadline_iso},
        )
        logger.info("armed agenda=%s at=%s", agenda_id, deadline_iso)

    async def disarm(self, agenda_id: str) -> None:
        await self.redis.zrem(_TIMERS_KEY, agenda_id)
        await self.redis.delete(_timer_hash(agenda_id))

    async def rehydrate(self) -> None:
        agendas = await db_list_active_agendas(self.pool)
        armed = 0
        for a in agendas:
            if a.get("next_fire_at"):
                await self.arm(a["id"], a["tenant_id"], a["next_fire_at"])
                armed += 1
        logger.info("rehydrated %d timer(s) from %d active agenda(s)", armed, len(agendas))

    # ── Poller ────────────────────────────────────────────────────────────────

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self._tick()
            except Exception as exc:
                logger.exception("poller tick failed: %s", exc)
            # Sleep poll_interval_s, but wake early on stop.
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.settings.poll_interval_s)
            except asyncio.TimeoutError:
                pass

    async def _tick(self) -> None:
        now = datetime.now(timezone.utc).timestamp()
        due = await self.redis.zrangebyscore(_TIMERS_KEY, "-inf", now)
        for agenda_id in due:
            # Claim by removing first — a member handled at most once.
            removed = await self.redis.zrem(_TIMERS_KEY, agenda_id)
            if not removed:
                continue
            meta = await self.redis.hgetall(_timer_hash(agenda_id))
            await self.redis.delete(_timer_hash(agenda_id))
            await self._handle_due(agenda_id, meta)

    async def _handle_due(self, agenda_id: str, meta: dict) -> None:
        tenant_id = meta.get("tenant_id", "")
        scheduled_for = meta.get("scheduled_for", "")
        agenda = await db_get_agenda(self.pool, tenant_id, agenda_id)
        if not agenda:
            logger.warning("due timer for unknown agenda %s (tenant=%s)", agenda_id, tenant_id)
            return
        logger.info(
            "AGENDA DUE id=%s name=%r scheduled_for=%s", agenda_id, agenda.get("name"), scheduled_for
        )
        if self._fire_cb is not None:
            # Dispatch + advance/complete are owned by the callback (tasks 4/5).
            await self._fire_cb(agenda, scheduled_for)
            return
        # No dispatch layer yet (task 3): consume the timer so it doesn't re-arm on
        # the next boot. Advancing recurrences is the evaluator's job (task 4).
        await db_set_next_fire_at(self.pool, agenda_id, None)
