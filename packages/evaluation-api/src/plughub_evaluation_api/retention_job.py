"""
retention_job.py — expurgo do TEXTO LIVRE das respostas de pesquisa (AUD-08).

Política do dono (2026-09-30): retenção POR CLASSE de dado, por tenant, no namespace
`retention` do config-api. A classe daqui é `survey_free_text_days` (default 365).

Passado o prazo (contado de `responded_at`), saem `open_text`, `verbatims` e as referências
de áudio e transcrição — o que torna a resposta dado pessoal. A nota (`signals`), o canal e
as datas ficam: relatório de NPS/CSAT passado não muda. Mesmo recorte da eliminação do
titular (AUD-06, `db.survey_subject_erase`), agora por idade em vez de por pessoa.

URL do config-api: `PLUGHUB_CONFIG_API_URL`, a MESMA variável que o carregador do mapa do
ContextStore já lê neste serviço (ALW-02) — uma fiação, não duas.

Degradação na direção SEGURA para o dado, com as regras do session-replayer
(`session_replayer/retention_purge.py`): config-api fora ou valor inválido → o tenant é
PULADO naquela rodada, com o motivo no log; chave ausente → o default declarado, dito.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

logger = logging.getLogger("plughub.evaluation.retention")

NAMESPACE = "retention"
KEY = "survey_free_text_days"
DEFAULT_DAYS = 365
MIN_DAYS, MAX_DAYS = 1, 3650
INTERVAL_S = 86_400

_PENDING = ("(open_text IS NOT NULL OR verbatims <> '[]'::jsonb "
            "OR audio_ref IS NOT NULL OR transcript_ref IS NOT NULL)")

TENANTS_SQL = f"SELECT DISTINCT tenant_id FROM survey.survey_response WHERE {_PENDING}"

PURGE_SQL = f"""
UPDATE survey.survey_response
   SET open_text = NULL, verbatims = '[]'::jsonb, audio_ref = NULL, transcript_ref = NULL
 WHERE tenant_id = $1
   AND responded_at < $2
   AND {_PENDING}
"""


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


def config_api_url() -> str:
    return os.environ.get("PLUGHUB_CONFIG_API_URL", "")


async def fetch_retention(url: str, tenant_id: str) -> Retention:
    if not url:
        logger.warning("retention: PLUGHUB_CONFIG_API_URL vazio — %s NÃO expurgado (tenant=%s)",
                       KEY, tenant_id)
        return Retention(None, "skip: PLUGHUB_CONFIG_API_URL vazio")
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.get(f"{url.rstrip('/')}/config/{NAMESPACE}", params={"tenant_id": tenant_id})
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


def _count(status: str | None) -> int:
    last = (status or "").split()[-1:] or ["0"]
    return int(last[0]) if last[0].isdigit() else 0


async def expire_once(pool: Any, url: str, now: datetime | None = None) -> dict[str, int]:
    now = now or datetime.now(timezone.utc)
    out: dict[str, int] = {}
    for r in await pool.fetch(TENANTS_SQL):
        tenant = r["tenant_id"]
        ret = await fetch_retention(url, tenant)
        if ret.days is None:
            continue
        cutoff = now - timedelta(days=ret.days)
        n = _count(await pool.execute(PURGE_SQL, tenant, cutoff))
        out[tenant] = n
        logger.info("retention: tenant=%s %s expirado em %d resposta(s) anteriores a %s "
                    "(%d dias, fonte=%s)", tenant, KEY, n, cutoff.isoformat(), ret.days, ret.source)
    return out


async def run_forever(pool: Any, interval_s: int = INTERVAL_S) -> None:
    logger.info("retention: expurgo de %s ativo (a cada %ds)", KEY, interval_s)
    while True:
        try:
            await expire_once(pool, config_api_url())
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("retention: rodada FALHOU — tenta de novo em %ds", interval_s)
        await asyncio.sleep(interval_s)
