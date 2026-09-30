"""RUL-05 — o contexto de todo turno vai ao histórico, e o dry-run o relê do ClickHouse."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import httpx
import pytest

from plughub_rules.history_reader import HistoryReader, HistoryUnavailable
from plughub_rules.kafka_publisher import TOPIC_TURN_CONTEXT, KafkaPublisher
from plughub_rules.main import _process_update
from plughub_rules.models import EvaluationContext
from plughub_rules.rule_store import RuleStore


class _Prod:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    async def send_and_wait(self, topic, value, key):
        if self.fail:
            raise ConnectionError("broker fora")
        self.sent.append((topic, json.loads(value), key))


async def _drain():
    pend = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pend:
        await asyncio.gather(*pend, return_exceptions=True)


def _msg(**extra):
    return {"type": "pmessage", "data": json.dumps({"session_id": "s1", "tenant_id": "t",
                                                     "turn_count": 2, "elapsed_ms": 900, **extra})}


async def _process(prod, sentiment_raw, **extra):
    redis = AsyncMock()
    redis.get.return_value = None
    redis.hget.return_value = sentiment_raw
    store = AsyncMock(spec=RuleStore)
    store.get_active_rules.return_value = []          # tenant sem regra nenhuma
    await _process_update(message=_msg(**extra), rule_store=store, evaluator=None,
                          escalator=None, redis=redis, publisher=KafkaPublisher(prod))
    await _drain()


@pytest.mark.asyncio
async def test_every_context_is_published_even_without_rules():
    prod = _Prod()
    await _process(prod, json.dumps({"value": -0.4}), trigger="sentiment_measured")
    assert len(prod.sent) == 1
    topic, body, key = prod.sent[0]
    assert topic == TOPIC_TURN_CONTEXT and key == b"s1"
    assert body["trigger"] == "sentiment_measured" and body["sentiment_score"] == -0.4
    assert (body["turn_count"], body["elapsed_ms"]) == (2, 900)
    assert body["event_id"] and body["observed_at"] and body["tenant_id"] == "t"


@pytest.mark.asyncio
async def test_unmeasured_is_published_as_null_and_unknown_trigger_as_turn():
    prod = _Prod()
    await _process(prod, None, trigger="qualquer")
    body = prod.sent[0][1]
    assert body["sentiment_score"] is None and body["trigger"] == "turn"


@pytest.mark.asyncio
async def test_publish_failure_is_reported_not_raised():
    ctx = EvaluationContext(session_id="s", tenant_id="t")
    assert await KafkaPublisher(_Prod(fail=True)).publish_turn_context(ctx, "turn") is False


# ── leitor ───────────────────────────────────────────────────────────────────

def _transport(rows, cov=("2026-09-30 10:00:00.000", 3), status=200):
    seen = []

    def handler(req: httpx.Request):
        sql = req.content.decode()
        seen.append((sql, dict(req.url.params), req.headers.get("X-ClickHouse-User")))
        if status != 200:
            return httpx.Response(status, text="boom")
        if "min(observed_at)" in sql:
            return httpx.Response(200, text=json.dumps({"since": cov[0], "n": str(cov[1])}) + "\n")
        return httpx.Response(200, text="".join(json.dumps(r) + "\n" for r in rows))
    return httpx.MockTransport(handler), seen


def _row(sid, turns, s=None):
    return {"session_id": sid, "turn_count": turns, "elapsed_ms": 10, "sentiment_score": s,
            "intent_confidence": 0.5, "flags": ["human_requested"]}


A = datetime(2026, 9, 1, tzinfo=timezone.utc)
B = datetime(2026, 9, 30, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_reader_groups_by_session_in_order_and_keeps_null():
    tr, seen = _transport([_row("a", 1, -0.5), _row("a", 2), _row("b", 1, 0.0)])
    h = await HistoryReader(tr).load("t1", A, B)
    assert [[c.turn_count for c in s] for s in h.sessions] == [[1, 2], [1]]
    assert h.sessions[0][1].sentiment_score is None and h.sessions[1][0].sentiment_score == 0.0
    assert h.coverage_from.startswith("2026-09-30") and not h.truncated and h.turn_contexts == 3
    sql, params, _ = seen[1]
    assert "FINAL" in sql and "ORDER BY session_id, observed_at" in sql
    assert params["param_t"] == "t1" and params["param_a"].startswith("2026-09-01")


@pytest.mark.asyncio
async def test_reader_with_no_history_has_no_coverage():
    tr, _ = _transport([], cov=("1970-01-01 00:00:00.000", 0))
    h = await HistoryReader(tr).load("t1", A, B)
    assert h.sessions == [] and h.coverage_from is None


@pytest.mark.asyncio
async def test_reader_says_when_it_truncates(monkeypatch):
    import plughub_rules.history_reader as hr
    monkeypatch.setattr(hr, "ROW_CAP", 2)
    tr, _ = _transport([_row("a", 1), _row("a", 2), _row("b", 1)])
    h = await HistoryReader(tr).load("t1", A, B)
    assert h.truncated and h.turn_contexts == 2


@pytest.mark.asyncio
async def test_reader_failure_is_unavailable_never_empty():
    tr, _ = _transport([], status=500)
    with pytest.raises(HistoryUnavailable):
        await HistoryReader(tr).load("t1", A, B)
