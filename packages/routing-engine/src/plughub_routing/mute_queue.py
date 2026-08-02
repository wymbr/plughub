"""
mute_queue.py — Fila de sistema (tier gratuito) — system-queue.md, Fase A.

Helpers do ciclo de vida da fila MUDA (pool sem `queue_config`, ou overflow de
admissão): isenção de C, contador total, tetos por canal e o ledger analítico
via SEGMENTO SINTÉTICO `role=queue` — mesma fonte que a Fase D do
queue-attended-model já consome no /reports/pools/queue (sem tópicos novos).

Chaves:
  {t}:queue:unadmitted          SET  — sessões em fila muda (isentas de C).
                                       SCARD = ocupação do buffer grátis
                                       (teto: max_queue_total).
  {t}:queue:first_queued:{sid}  STR  — epoch ms do PRIMEIRO enqueue (NX + TTL).
                                       Preserva a espera real através de
                                       re-enfileiramentos (re-admissão negada
                                       no drain) e dá a duração do segmento.

Saídas da fila muda (emitem o segmento sintético e limpam o estado):
  handoff   — sessão foi ADMITIDA (transição unadmitted→admitted no inbound).
  abandoned — cliente desconectou enquanto esperava (marker closed no drain).
  (max_wait_exceeded é emitido pelo _emit_queue_timeout, que já tinha o seu
   próprio segmento sintético — aqui só limpamos o estado.)

Proteções (spec § Proteções operacionais): tetos vêm do namespace `routing` do
Config API via `routing_config` (cache com defaults hard-coded e degradação
graciosa — Config fora/ausente NUNCA significa ilimitado; reload automático no
config.changed).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from .routing_config import routing_config

logger = logging.getLogger("plughub.routing.mute_queue")

_TTL_S = 604_800  # 7d — mesmo horizonte dos markers de sessão


def unadmitted_key(tenant_id: str) -> str:
    return f"{tenant_id}:queue:unadmitted"


def first_queued_key(tenant_id: str, session_id: str) -> str:
    return f"{tenant_id}:queue:first_queued:{session_id}"


def _decode(v) -> str:
    if v is None:
        return ""
    return v.decode() if isinstance(v, bytes) else str(v)


# ── Tetos (proteções operacionais — Config API namespace `routing`) ───────────

def max_queue_total() -> int:
    """Teto TOTAL do buffer grátis (hard limit; default 100, nunca ilimitado)."""
    try:
        v = int(routing_config.get("queue_max_total", 100))
        return v if v > 0 else 100
    except (TypeError, ValueError):
        return 100


def channel_max_wait_s(settings, channel: str) -> int:
    """
    Teto de espera muda por canal (0 = canal não aceita fila muda → outage).
    Config: `queue_max_wait_by_channel` {canal: segundos}; canais ausentes caem
    no `queue_max_wait_default_s` dos settings (1800).
    """
    default = int(getattr(settings, "queue_max_wait_default_s", 1800))
    by_channel = routing_config.get("queue_max_wait_by_channel") or {}
    try:
        v = by_channel.get(channel)
        return int(v) if v is not None else default
    except (TypeError, ValueError, AttributeError):
        return default


# ── Ciclo de vida ─────────────────────────────────────────────────────────────

async def mark_mute_queued(redis_client, tenant_id: str, session_id: str, now_ms: int) -> int:
    """
    Registra a sessão como em fila muda (isenta de C) e devolve o epoch ms do
    PRIMEIRO enqueue (NX — re-enfileiramentos preservam a espera original).
    """
    fq_key = first_queued_key(tenant_id, session_id)
    await redis_client.set(fq_key, str(now_ms), nx=True, ex=_TTL_S)
    await redis_client.sadd(unadmitted_key(tenant_id), session_id)
    raw = _decode(await redis_client.get(fq_key))
    try:
        return int(float(raw)) if raw else now_ms
    except ValueError:
        return now_ms


async def resolve_mute_exit(
    redis_client,
    producer,
    tenant_id:  str,
    pool_id:    str,
    session_id: str,
    outcome:    str,                 # "handoff" | "abandoned"
    emit_segment: bool = True,
) -> bool:
    """
    Encerra a passagem pela fila muda: SREM do unadmitted (retorna False se a
    sessão não estava em fila muda — no-op barato no caminho quente) e, quando
    `emit_segment`, publica o segmento sintético `role=queue` (agent_type=system)
    com a espera real — o ledger que o /reports/pools/queue (Fase D) já lê.
    `emit_segment=False` para o caminho de max_wait (o _emit_queue_timeout já
    emite o segmento dele).
    """
    removed = await redis_client.srem(unadmitted_key(tenant_id), session_id)
    if not removed:
        return False

    fq_key = first_queued_key(tenant_id, session_id)
    raw    = _decode(await redis_client.get(fq_key))
    await redis_client.delete(fq_key)

    if not emit_segment:
        return True

    now    = datetime.now(timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    try:
        first_ms = int(float(raw)) if raw else now_ms
    except ValueError:
        first_ms = now_ms
    duration_ms = max(0, now_ms - first_ms)
    joined_iso  = datetime.fromtimestamp(first_ms / 1000, tz=timezone.utc).isoformat()

    try:
        await producer.send("conversations.participants", value={
            "event_id":       str(uuid.uuid4()),
            "type":           "participant_left",
            "session_id":     session_id,
            "tenant_id":      tenant_id,
            "segment_id":     str(uuid.uuid4()),
            "participant_id": "system-queue",
            "pool_id":        pool_id,
            "agent_type_id":  "system",
            "agent_type":     "system",
            "role":           "queue",
            "sequence_index": 0,
            "joined_at":      joined_iso,
            "timestamp":      now.isoformat(),
            "duration_ms":    duration_ms,
            "outcome":        outcome,
            "close_reason":   "",
        })
        logger.info(
            "mute queue exit: session=%s pool=%s outcome=%s wait_ms=%d",
            session_id, pool_id, outcome, duration_ms,
        )
    except Exception as exc:
        logger.warning(
            "mute queue exit: failed to publish synthetic segment session=%s — %s",
            session_id, exc,
        )
    return True


# `buffer_usage` REMOVIDA (fatia 3, 2026-08-02). Único chamador era
# `main._try_overflow_enqueue`, que checava se o buffer grátis tinha vaga antes de
# acomodar o overflow da admissão — overflow que ficou sem entrada possível quando
# sessão humana deixou de ser gateada por `C`. O teto do buffer (`max_queue_total`)
# continua vivo: é o denominador da linha `__buffer__` da série e do tile do Monitor.
