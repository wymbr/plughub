"""
retention_job.py — expurgo do CONTEÚDO de conversa no ClickHouse (AUD-08).

Política do dono (2026-09-30): retenção POR CLASSE de dado, por tenant, no namespace
`retention` do config-api. A classe daqui é `conversation_content_days` (default 365) —
a MESMA chave que o session-replayer lê para o stream durável: um prazo, duas lojas.

O que sai, contado do horário do evento (a linha de MÉTRICA fica, como na eliminação da
AUD-06 — contagens, papéis e horários continuam, sem o que foi dito):
  · `messages.content`          → `[expired]`
  · `contact_insights.value`    → `[expired]`
  · `session_timeline.payload`  → `{}`

Por que mutação (`ALTER … UPDATE`) e não `TTL` da tabela: o prazo é POR TENANT, e o TTL do
ClickHouse é um só por tabela. Mutação reescreve partes, então roda UMA vez por dia.

Degradação na direção SEGURA para o dado, com as mesmas regras do session-replayer
(`session_replayer/retention_purge.py`): config-api fora ou valor inválido → o tenant é
PULADO naquela rodada, com o motivo no log; chave ausente → o default declarado, dito.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

logger = logging.getLogger("plughub.analytics.retention")

NAMESPACE = "retention"
KEY = "conversation_content_days"
DEFAULT_DAYS = 365
MIN_DAYS, MAX_DAYS = 1, 3650
INTERVAL_S = 86_400
EXPIRED = "[expired]"

# (tabela, coluna de tempo, SET, filtro de "ainda não expirado")
TARGETS: tuple[tuple[str, str, str, str], ...] = (
    ("messages", "timestamp", f"content = '{EXPIRED}'",
     f"content IS NOT NULL AND content != '{EXPIRED}'"),
    ("contact_insights", "timestamp", f"value = '{EXPIRED}'", f"value != '{EXPIRED}'"),
    ("session_timeline", "timestamp", "payload = '{}'", "payload != '{}'"),
    # AUD-09 — o resumo de wrap-up é conteúdo da conversa escrito pelo atendente; a AUD-08
    # não o tinha na lista. Vazio fica vazio (NULL não vira texto).
    ("segments", "started_at",
     f"wrapup_summary = if(coalesce(wrapup_summary, '') = '', wrapup_summary, '{EXPIRED}'), "
     f"wrapup_next_steps = if(coalesce(wrapup_next_steps, '') = '', wrapup_next_steps, '{EXPIRED}')",
     f"(coalesce(wrapup_summary, '') NOT IN ('', '{EXPIRED}') "
     f"OR coalesce(wrapup_next_steps, '') NOT IN ('', '{EXPIRED}'))"),
)


@dataclass(frozen=True)
class Retention:
    days: int | None
    source: str


def _parse_days(raw: Any) -> int | None:
    if isinstance(raw, bool):
        return None
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None
    if isinstance(raw, float) and raw != n:
        return None
    return n if MIN_DAYS <= n <= MAX_DAYS else None


async def fetch_retention(config_api_url: str, tenant_id: str) -> Retention:
    if not config_api_url:
        logger.warning("retention: config_api_url vazio — %s NÃO expurgado (tenant=%s)", KEY, tenant_id)
        return Retention(None, "skip: config_api_url vazio")
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.get(f"{config_api_url.rstrip('/')}/config/{NAMESPACE}",
                            params={"tenant_id": tenant_id})
        r.raise_for_status()
        body = r.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("retention: config-api indisponível para tenant=%s (%s) — %s NÃO "
                       "expurgado nesta rodada", tenant_id, exc, KEY)
        return Retention(None, f"skip: config-api indisponível ({exc})")
    entries = body.get("entries") if isinstance(body, dict) and "entries" in body else body
    entry = entries.get(KEY) if isinstance(entries, dict) else None
    if entry is None:
        logger.warning("retention: %s.%s ausente para tenant=%s — usando o default de %d dias",
                       NAMESPACE, KEY, tenant_id, DEFAULT_DAYS)
        return Retention(DEFAULT_DAYS, "default")
    raw = entry.get("value") if isinstance(entry, dict) and "value" in entry else entry
    days = _parse_days(raw)
    if days is None:
        logger.error("retention: %s.%s=%r inválido para tenant=%s — NÃO expurgado",
                     NAMESPACE, KEY, raw, tenant_id)
        return Retention(None, f"skip: valor inválido {raw!r}")
    return Retention(days, "config")


def _q(v: str) -> str:
    return "'" + str(v).replace("\\", "\\\\").replace("'", "\\'") + "'"


def _tenants(client: Any, db: str) -> list[str]:
    rows = client.query(
        " UNION DISTINCT ".join(f"SELECT DISTINCT tenant_id FROM {db}.{t}" for t, *_ in TARGETS)
    ).result_rows
    return sorted({r[0] for r in rows if r[0]})


def expire_tenant(client: Any, db: str, tenant_id: str, cutoff: datetime) -> dict[str, int]:
    """Conta e muta, por tabela, as linhas do tenant anteriores ao corte que ainda têm
    conteúdo. Mutação não devolve contagem — por isso a contagem vem antes."""
    ts = _q(cutoff.strftime("%Y-%m-%d %H:%M:%S"))
    out: dict[str, int] = {}
    for table, col, set_expr, pending in TARGETS:
        where = f"tenant_id = {_q(tenant_id)} AND {col} < {ts} AND {pending}"
        n = int(client.query(f"SELECT count() FROM {db}.{table} WHERE {where}").result_rows[0][0])
        out[table] = n
        if n:
            client.command(f"ALTER TABLE {db}.{table} UPDATE {set_expr} WHERE {where}",
                           settings={"mutations_sync": 1})
    return out


async def expire_once(store: Any, config_api_url: str,
                      now: datetime | None = None) -> dict[str, dict[str, int]]:
    now = now or datetime.now(timezone.utc)
    client = store.new_client()
    db = store._database
    result: dict[str, dict[str, int]] = {}
    for tenant in await asyncio.to_thread(_tenants, client, db):
        ret = await fetch_retention(config_api_url, tenant)
        if ret.days is None:
            continue
        cutoff = now - timedelta(days=ret.days)
        counts = await asyncio.to_thread(expire_tenant, client, db, tenant, cutoff)
        result[tenant] = counts
        logger.info("retention: tenant=%s %s expirado em %s (anteriores a %s, %d dias, fonte=%s)",
                    tenant, KEY, counts, cutoff.isoformat(), ret.days, ret.source)
    return result


async def run_forever(store: Any, config_api_url: str, interval_s: int = INTERVAL_S) -> None:
    """Uma rodada que falha loga e a próxima tenta de novo; a task não morre por isso."""
    logger.info("retention: expurgo de %s no ClickHouse ativo (a cada %ds)", KEY, interval_s)
    while True:
        try:
            await expire_once(store, config_api_url)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("retention: rodada FALHOU — tenta de novo em %ds", interval_s)
        await asyncio.sleep(interval_s)
