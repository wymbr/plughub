"""
dispatcher.py
The fire callback (task 5): when a timer fires, POST the pool's webhook (Arc 19),
capture the outcome into the AgendaDispatch ledger, then advance/re-arm.

Contract (channel-gateway):
  POST /v1/channels/webhook/pool/{pool_id}  → 201 { session_id }

Dispatch honesty: "dispatched" only when a session_id came back. Any HTTP error /
exception / missing session_id = "failed" with the reason recorded (no silent drop,
no auto-retry in v1). Execution status is the fired session's concern (drill-through
via session_id) — never mirrored here.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from .db import (
    db_insert_dispatch,
    db_set_next_fire_at,
    db_update_agenda_runtime,
)
from .evaluator import compute_next_fire

logger = logging.getLogger("plughub.scheduler.dispatcher")


def _parse(iso: str) -> datetime:
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class Dispatcher:
    def __init__(self, pool, settings, calendar_client, scheduler) -> None:
        self.pool = pool
        self.settings = settings
        self.calendar_client = calendar_client
        self.scheduler = scheduler

    async def on_fire(self, agenda: dict, scheduled_for: str) -> None:
        tenant = agenda["tenant_id"]
        now = datetime.now(timezone.utc)
        result, session_id, root_session_id, error = await self._trigger_webhook(agenda)

        sched_dt = _parse(scheduled_for) if scheduled_for else now
        await db_insert_dispatch(self.pool, tenant, {
            "agenda_id":       agenda["id"],
            "scheduled_for":   sched_dt,
            "fired_at":        now,
            "result":          result,
            "session_id":      session_id,
            "root_session_id": root_session_id,
            "error":           error,
        })
        await db_update_agenda_runtime(self.pool, agenda["id"], last_fired_at=now)
        await self._advance(agenda, scheduled_for, now)
        logger.info(
            "agenda=%s fired result=%s session=%s error=%s",
            agenda["id"], result, session_id, error,
        )

    async def fire_manual(self, agenda: dict) -> dict:
        """Fase 3 — 'disparar agora': força um disparo imediato do pool da agenda.

        Difere do on_fire por NÃO avançar a recorrência: um disparo manual não
        consome nem recalcula a próxima ocorrência (a política de recorrência é da
        Config; o Monitor só empurra um disparo extra). Grava um AgendaDispatch com
        scheduled_for = now (marca o disparo sob demanda). Reusa o mesmo caminho de
        webhook/ledger — dispatched só quando volta session_id; senão failed com o
        motivo (sem retry, degradação nunca silenciosa)."""
        tenant = agenda["tenant_id"]
        now = datetime.now(timezone.utc)
        result, session_id, root_session_id, error = await self._trigger_webhook(agenda)
        row = await db_insert_dispatch(self.pool, tenant, {
            "agenda_id":       agenda["id"],
            "scheduled_for":   now,
            "fired_at":        now,
            "result":          result,
            "session_id":      session_id,
            "root_session_id": root_session_id,
            "error":           error,
        })
        await db_update_agenda_runtime(self.pool, agenda["id"], last_fired_at=now)
        logger.info(
            "agenda=%s MANUAL fire result=%s session=%s error=%s",
            agenda["id"], result, session_id, error,
        )
        return row

    async def _trigger_webhook(self, agenda: dict):
        """Returns (result, session_id, root_session_id, error)."""
        pool_id = agenda["target_pool_id"]
        url = f"{self.settings.channel_gateway_url}/v1/channels/webhook/pool/{pool_id}"
        body = {
            "tenant_id":    agenda["tenant_id"],
            "trigger_type": "task",
            # The agenda's generic payload becomes the trigger context; the pool's
            # deployed skill interprets it (deploy reads {action: promote}, etc.).
            "context":      agenda.get("payload") or {},
            # A scheduled dispatch starts its own process root (no parent session).
            "journey":      "new",
        }
        try:
            async with httpx.AsyncClient(timeout=self.settings.dispatch_timeout_s) as client:
                r = await client.post(url, json=body)
        except Exception as exc:
            return "failed", None, None, f"unreachable: {exc}"

        if r.status_code == 201:
            try:
                data = r.json()
            except Exception:
                return "failed", None, None, "201 with non-JSON body"
            sid = data.get("session_id")
            if sid:
                return "dispatched", sid, data.get("root_session_id"), None
            return "failed", None, None, "201 without session_id"
        return "failed", None, None, f"HTTP {r.status_code}: {r.text[:200]}"

    async def _advance(self, agenda: dict, scheduled_for: str, now: datetime) -> None:
        """Compute the next occurrence and re-arm; complete when exhausted / once."""
        if agenda["schedule"].get("mode") == "once":
            next_fire = None
        else:
            after = _parse(scheduled_for) if scheduled_for else now
            next_fire = await compute_next_fire(agenda, after, self.calendar_client)

        if next_fire is None:
            await db_update_agenda_runtime(self.pool, agenda["id"], status="completed")
            await db_set_next_fire_at(self.pool, agenda["id"], None)
            await self.scheduler.disarm(agenda["id"])
        else:
            await db_set_next_fire_at(self.pool, agenda["id"], next_fire)
            await self.scheduler.arm(agenda["id"], agenda["tenant_id"], next_fire.isoformat())
