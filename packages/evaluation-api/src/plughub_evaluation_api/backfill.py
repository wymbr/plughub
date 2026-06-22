"""
backfill.py — T17-backfill (spec §18.5).

Job batch que reprocessa o PASSADO de uma campanha: enumera os segmentos já fechados na
janela de dados da campanha (`[period_start, period_end]`) a partir do store persistido
(`analytics.segments`, via REST `GET /reports/segments` da analytics-api) e cria as
instances por segmento (mesma lógica de sampling do caminho forward). As instances nascem
`scheduled` e são despachadas pelo dispatcher windowed (T15, §18.4) — o backfill NÃO
despacha nem roda o avaliador.

Complementa o filtro forward do `_sample_on_close` (T17 core): forward amostra da ativação
em diante; o backfill cobre `period_start` no passado.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from . import db as _db
from .sampling import should_sample, compute_priority

logger = logging.getLogger("plughub.evaluation.backfill")

# Segmentos avaliáveis = janelas de agente (exclui supervisor/evaluator/reviewer), como no
# fan-out do _sample_on_close (spec §13.2).
_EVALUABLE_ROLES = {"primary", "specialist"}


async def fetch_closed_segments(
    analytics_api_url: str,
    tenant_id: str,
    *,
    pool_id: str | None,
    from_dt: str,
    to_dt: str,
    page_size: int = 200,
    max_segments: int = 5000,
) -> list[dict[str, Any]]:
    """Pagina `GET /reports/segments` (analytics-api) e devolve os segmentos avaliáveis
    (role ∈ {primary, specialist}) da janela. Best-effort: erro/indisponível → lista vazia
    (o backfill reporta scanned=0, não falha)."""
    out: list[dict[str, Any]] = []
    page = 1
    base = analytics_api_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            while len(out) < max_segments:
                params: dict[str, Any] = {
                    "tenant_id": tenant_id,
                    "from_dt":   from_dt,
                    "to_dt":     to_dt,
                    "page":      page,
                    "page_size": page_size,
                }
                if pool_id:
                    params["pool_id"] = pool_id
                resp = await client.get(f"{base}/reports/segments", params=params)
                if resp.status_code != 200:
                    logger.warning("backfill: /reports/segments %s (page=%s) — parando",
                                   resp.status_code, page)
                    break
                body = resp.json()
                rows = body.get("data") or []
                for r in rows:
                    if r.get("role") in _EVALUABLE_ROLES and r.get("segment_id"):
                        out.append(r)
                total = int((body.get("meta") or {}).get("total") or 0)
                if page * page_size >= total or not rows:
                    break
                page += 1
    except Exception as exc:
        logger.warning("backfill: enumeração de segmentos falhou tenant=%s pool=%s: %s",
                       tenant_id, pool_id, exc)
    return out[:max_segments]


async def run_campaign_backfill(
    db_pool: _db.asyncpg.Pool,
    campaign: dict[str, Any],
    *,
    analytics_api_url: str,
    from_dt: str,
    to_dt: str,
    page_size: int = 200,
    max_segments: int = 5000,
) -> dict[str, Any]:
    """Enumera os segmentos fechados da janela e cria instances `scheduled` (uma por
    segmento amostrado). Idempotente por `(campaign_id, segment_id)` — re-rodar não
    duplica. Retorna {scanned, created, skipped_pool, skipped_sample, skipped_dup}."""
    tenant_id   = campaign["tenant_id"]
    campaign_id = campaign["id"]
    epid        = campaign.get("evaluation_pool_id") or campaign.get("pool_id")
    rules       = campaign.get("sampling_rules") or {}

    segments = await fetch_closed_segments(
        analytics_api_url, tenant_id,
        pool_id=epid, from_dt=from_dt, to_dt=to_dt,
        page_size=page_size, max_segments=max_segments,
    )

    # form_version pinado uma vez (versão publicada do form no momento do backfill).
    form_version = await _db.latest_published_version(db_pool, campaign["form_id"], tenant_id)
    if form_version is None:
        _form = await _db.get_form(db_pool, campaign["form_id"], tenant_id)
        form_version = int((_form or {}).get("version") or 1)

    scanned = created = skipped_pool = skipped_sample = skipped_dup = 0
    base_counter = int(campaign.get("total_instances") or 0)

    for seg in segments:
        scanned += 1
        seg_pool = seg.get("pool_id")
        # hard filter por pool avaliado (o /reports/segments já filtra por pool_id, mas
        # mantemos por robustez quando epid é None).
        if epid and seg_pool != epid:
            skipped_pool += 1
            continue
        dur_ms = seg.get("duration_ms")
        meta = {
            "pool_id":       seg_pool,
            "channel":       None,  # segmento persistido não carrega channel; rules por canal não se aplicam no backfill
            "outcome":       seg.get("outcome"),
            "agent_type_id": seg.get("agent_type_id"),
            "duration_s":    (int(dur_ms) / 1000.0) if dur_ms not in (None, "") else 0.0,
        }
        segment_id = seg["segment_id"]
        if not should_sample(segment_id, meta, rules, counter=base_counter + created):
            skipped_sample += 1
            continue
        if await _db.instance_exists_for_segment(db_pool, campaign_id, segment_id, tenant_id):
            skipped_dup += 1
            continue
        inst = await _db.create_instance(
            db_pool, tenant_id=tenant_id, campaign_id=campaign_id, form_id=campaign["form_id"],
            session_id=seg.get("session_id") or "", segment_id=segment_id,
            evaluated_user_id=(seg.get("user_id") or None),
            form_version=form_version, priority=compute_priority(meta, rules),
            deploy_version=(seg.get("deploy_version") or None),   # R9d (None até /reports/segments expor)
        )
        created += 1
        logger.info("backfill: instance %s (campaign=%s segment=%s pool=%s)",
                    inst.get("id"), campaign_id, segment_id, seg_pool)

    return {
        "campaign_id":    campaign_id,
        "window":         {"from": from_dt, "to": to_dt},
        "scanned":        scanned,
        "created":        created,
        "skipped_pool":   skipped_pool,
        "skipped_sample": skipped_sample,
        "skipped_dup":    skipped_dup,
    }
