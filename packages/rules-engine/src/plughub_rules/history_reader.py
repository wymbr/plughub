"""
history_reader.py — RUL-05: o passado de onde o dry-run simula.

Lê `rule_turn_contexts` no ClickHouse (escrita pela analytics-api a partir do tópico
`rules.turn_contexts`, que o próprio rules-engine produz): o contexto que uma regra VIU em
cada turno. O dry-run reavalia esses contextos com o MESMO avaliador da execução real.

Duas ausências não se confundem com zero:
  - ClickHouse fora ⇒ `HistoryUnavailable` (a rota diz 503) — nunca "0 sessões";
  - dado que começou a ser gravado depois do início da janela ⇒ `coverage_from`, para que
    uma taxa sobre três dias não se leia como taxa sobre trinta.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime

import httpx

from .config import get_settings
from .models import EvaluationContext

logger = logging.getLogger("plughub.rules")

ROW_CAP = 200_000   # teto de linhas por simulação; passar dele é dito (`truncated`)


class HistoryUnavailable(Exception):
    """Não deu para ler o histórico — dito, nunca vira 'nenhuma sessão'."""


@dataclass
class History:
    sessions:      list[list[EvaluationContext]] = field(default_factory=list)
    turn_contexts: int = 0
    coverage_from: str | None = None
    truncated:     bool = False


class HistoryReader:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    async def _query(self, sql: str, params: dict[str, str]) -> list[dict]:
        s = get_settings()
        query_params = {"database": s.clickhouse_db, **{f"param_{k}": v for k, v in params.items()}}
        headers = {"X-ClickHouse-User": s.clickhouse_user, "X-ClickHouse-Key": s.clickhouse_password}
        try:
            async with httpx.AsyncClient(timeout=30.0, transport=self._transport) as client:
                res = await client.post(s.clickhouse_url, params=query_params, headers=headers,
                                        content=sql + " FORMAT JSONEachRow")
        except httpx.HTTPError as exc:
            raise HistoryUnavailable(f"ClickHouse inalcançável ({s.clickhouse_url}): {exc}") from exc
        if res.status_code != 200:
            raise HistoryUnavailable(f"ClickHouse respondeu {res.status_code}: {res.text[:300]}")
        return [json.loads(line) for line in res.text.splitlines() if line.strip()]

    async def load(self, tenant_id: str, start: datetime, end: datetime) -> History:
        fmt = "%Y-%m-%d %H:%M:%S.%f"
        cov = await self._query(
            "SELECT toString(min(observed_at)) AS since, count() AS n "
            "FROM rule_turn_contexts WHERE tenant_id = {t:String}",
            {"t": tenant_id})
        coverage_from = cov[0]["since"] if cov and int(cov[0].get("n") or 0) > 0 else None

        rows = await self._query(
            "SELECT session_id, turn_count, elapsed_ms, sentiment_score, intent_confidence, flags "
            "FROM rule_turn_contexts FINAL "
            "WHERE tenant_id = {t:String} "
            "AND observed_at >= parseDateTime64BestEffort({a:String}, 3, 'UTC') "
            "AND observed_at <  parseDateTime64BestEffort({b:String}, 3, 'UTC') "
            f"ORDER BY session_id, observed_at, event_id LIMIT {ROW_CAP + 1}",
            {"t": tenant_id, "a": start.strftime(fmt), "b": end.strftime(fmt)})

        truncated = len(rows) > ROW_CAP
        if truncated:
            logger.warning("dry-run tenant=%s: mais de %d turnos na janela — simulação TRUNCADA "
                           "(a última sessão pode estar incompleta)", tenant_id, ROW_CAP)
            rows = rows[:ROW_CAP]

        sessions: list[list[EvaluationContext]] = []
        current: str | None = None
        for r in rows:
            if r["session_id"] != current:
                sessions.append([])
                current = r["session_id"]
            sessions[-1].append(EvaluationContext(
                session_id=        r["session_id"],
                tenant_id=         tenant_id,
                turn_count=        int(r["turn_count"]),
                elapsed_ms=        int(r["elapsed_ms"]),
                sentiment_score=   None if r["sentiment_score"] is None else float(r["sentiment_score"]),
                intent_confidence= float(r["intent_confidence"]),
                flags=             list(r["flags"] or []),
            ))
        return History(sessions=sessions, turn_contexts=len(rows),
                       coverage_from=coverage_from, truncated=truncated)
