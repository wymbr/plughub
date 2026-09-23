"""
performance_job.py
Arc 7d — Agent performance batch job.

Reads `segments FINAL` from ClickHouse (APF-01 — never the retired
mv_agent_performance_daily, which counted segment VERSIONS) and writes normalised
performance scores to Redis for consumption by the routing-engine:

  Key:   {tenant_id}:agent_perf:{agent_type_id}
  Value: str(float) in [0.0, 1.0]
  TTL:   PERF_KEY_TTL seconds (default 6 hours, refreshed every sync)

Score formula:
  performance_score = resolution_rate × (1 − escalation_rate)

Minimum sample size: MIN_SESSIONS sessions over the last LOOKBACK_DAYS days.
Agent types without enough data receive no Redis key; the routing-engine
falls back to the neutral default (0.5) configured in InstanceRegistry.

Background loop (run_performance_job_loop):
  - Runs immediately on startup so Redis is populated before first routing event
  - Re-runs every interval_s seconds (default 300 — 5 minutes)
  - Gracefully exits on CancelledError (application shutdown)
"""
from __future__ import annotations

import asyncio
import logging

# TRF-01 — uma casa para "o que é transferência"; o score não pode divergir dos relatórios.
from .reports_query import _ESCALATE_FAMILY_SQL, _IS_TRANSFER_SQL, _NOT_TRANSFER_SQL

logger = logging.getLogger("plughub.analytics.performance_job")

# ─── Constants ────────────────────────────────────────────────────────────────

PERF_KEY_TTL  = 6 * 3600   # 6 hours — routing-engine reads this; refresh every 5 min
LOOKBACK_DAYS = 7           # aggregate over the last 7 days
MIN_SESSIONS  = 5           # minimum sessions required for statistical significance

# APF-01 (2026-09-23) — lê `segments FINAL`, nunca mais a `mv_agent_performance_daily`.
#
# A MV era uma materialized view sobre `segments`, que é `ReplacingMergeTree`: ela
# dispara a cada INSERT, e cada VERSÃO fechada de um segmento (fechamento + regravação
# do wrap-up) entrava no estado agregado — o merge do RMT apaga versões na tabela,
# nunca na MV. Medido: `retencao_humano` 600 × 382 segmentos reais, e as transferências
# guardadas como `transferred`, a versão PLACEHOLDER que o wrap-up reescreveu. Este job
# alimenta o score de ROTEAMENTO, então a contagem errada viraria decisão de alocação
# no dia em que alguém ligasse `routing.performance_score_weight`.
#
# Três filtros que a MV não tinha, cada um com motivo:
#   · `origin = 'live'` — importado/reavaliado (quality-ingest/export) é substrato de
#     QUALIDADE; misturá-lo no score de produção rotearia pelo histórico de outra casa.
#   · `agent_type != 'system'` — segmento sintético de admissão não é agente.
#   · a regra da TRF-01 — transferência (`close_reason`) não é resolução nem escalação
#     pelo `outcome`; aqui ela entra como ESCALAÇÃO, como na bancada, porque o score
#     pune "não resolveu e passou adiante". Um fragmento só, importado de lá.
_PERF_QUERY = """
SELECT
    tenant_id,
    agent_type_id,
    count()                                                                     AS total_sessions,
    countIf(outcome = 'resolved' AND {not_transfer}) / count()                  AS resolution_rate,
    countIf(outcome IN {escalate_family} OR {is_transfer}) / count()            AS escalation_rate
FROM {db}.segments FINAL
WHERE ended_at IS NOT NULL
  AND started_at >= today() - {lookback}
  AND origin = 'live'
  AND agent_type != 'system'
GROUP BY tenant_id, agent_type_id
HAVING count() >= {min_sessions}
"""


# ─── Core helpers ─────────────────────────────────────────────────────────────

def compute_performance_score(
    resolution_rate: float,
    escalation_rate: float,
) -> float:
    """
    Normalised performance score in [0.0, 1.0].

    Rewards high resolution rate, penalises high escalation rate.
    Formula: resolution_rate × (1 − min(escalation_rate, 1.0))

    Examples:
      resolution=1.0, escalation=0.0 → 1.0  (perfect agent)
      resolution=0.8, escalation=0.2 → 0.64
      resolution=0.0, escalation=any → 0.0  (never resolves)
      resolution=any, escalation=1.0 → 0.0  (always escalates)
    """
    score = float(resolution_rate) * (1.0 - min(float(escalation_rate), 1.0))
    return round(max(0.0, min(1.0, score)), 4)


async def run_performance_sync(store, redis) -> dict:
    """
    Queries ClickHouse `segments FINAL` and writes one Redis key
    per (tenant_id, agent_type_id).

    Uses asyncio.to_thread() so the synchronous ClickHouse client does not
    block the event loop.

    Returns {"updated": N, "errors": M}.
    """
    updated = 0
    errors  = 0

    query = _PERF_QUERY.format(
        db              = store._database,
        lookback        = LOOKBACK_DAYS,
        min_sessions    = MIN_SESSIONS,
        is_transfer     = _IS_TRANSFER_SQL,
        not_transfer    = _NOT_TRANSFER_SQL,
        escalate_family = _ESCALATE_FAMILY_SQL,
    )

    try:
        result = await asyncio.to_thread(store.new_client().query, query)
    except Exception as exc:
        logger.error("Performance sync query failed: %s", exc)
        return {"updated": 0, "errors": 1}

    for row in result.result_rows:
        tenant_id, agent_type_id, _total, resolution_rate, escalation_rate = row
        score = compute_performance_score(
            float(resolution_rate or 0.0),
            float(escalation_rate or 0.0),
        )
        key = f"{tenant_id}:agent_perf:{agent_type_id}"
        try:
            await redis.setex(key, PERF_KEY_TTL, str(score))
            updated += 1
        except Exception as exc:
            logger.warning("Redis write failed for %s: %s", key, exc)
            errors += 1

    logger.info(
        "Performance sync complete: updated=%d errors=%d",
        updated, errors,
    )
    return {"updated": updated, "errors": errors}


async def run_performance_job_loop(
    store,
    redis,
    interval_s: int = 300,
) -> None:
    """
    Background task: runs performance sync every interval_s seconds (default 5 min).

    Runs immediately on first iteration so Redis is populated at startup before
    the routing-engine processes its first contact.

    Gracefully exits on asyncio.CancelledError (application shutdown).
    Non-CancelledError exceptions are logged and the loop continues — a transient
    ClickHouse or Redis failure should never crash the background task.
    """
    while True:
        try:
            await run_performance_sync(store, redis)
        except asyncio.CancelledError:
            logger.info("Performance job loop cancelled — exiting")
            # ⚠️ RET-13: `break` fazia a task terminar COM SUCESSO ao ser
            # cancelada, e o supervisor a reportaria como morte espontânea em
            # todo shutdown. Ver `plughub_tasks.supervisionar` (RET-17).
            raise
        except Exception as exc:
            logger.error("Performance job error (will retry in %ds): %s", interval_s, exc)

        try:
            await asyncio.sleep(interval_s)
        except asyncio.CancelledError:
            logger.info("Performance job sleep cancelled — exiting")
            break
