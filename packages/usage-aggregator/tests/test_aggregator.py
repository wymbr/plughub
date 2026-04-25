"""
test_aggregator.py
Unit tests para UsageAggregator e funções auxiliares.

Usa mocks para Redis e PostgreSQL — testa a lógica de agregação sem infra externa.
Cobre:
  - INCRBY correto no Redis (via MULTI/EXEC)
  - pipeline(transaction=True) é chamado (garantia de atomicidade)
  - Idempotência via event_id (ON CONFLICT DO NOTHING simulado)
  - Truncagem de timestamp para hora
  - Graceful degradation em falha de Redis
  - Graceful degradation em falha de PostgreSQL
"""
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call

from usage_aggregator.aggregator import UsageAggregator, _truncate_to_hour
from usage_aggregator.models     import UsageEvent


# ─── Fixtures ─────────────────────────────────────────────────────────────────

def make_event(**kwargs) -> UsageEvent:
    defaults = {
        "event_id":         "evt-001",
        "tenant_id":        "tenant-abc",
        "session_id":       "sess-xyz",
        "dimension":        "sessions",
        "quantity":         1.0,
        "timestamp":        "2026-04-21T10:30:45+00:00",
        "source_component": "core",
        "metadata":         {"channel": "webchat"},
    }
    defaults.update(kwargs)
    return UsageEvent(**defaults)


def make_redis_mock():
    """
    Returns (mock_redis, mock_pipe).
    The pipeline is wired as an async context manager so that
    `async with redis.pipeline(transaction=True) as pipe:` works in tests.
    pipeline(transaction=True) is asserted in dedicated tests.
    """
    mock = MagicMock()
    pipe = AsyncMock()
    pipe.incrbyfloat = AsyncMock(return_value=pipe)
    pipe.expire      = AsyncMock(return_value=pipe)
    pipe.set         = AsyncMock(return_value=pipe)
    pipe.execute     = AsyncMock(return_value=[1.0, True, True])
    # async context manager support: async with redis.pipeline(transaction=True) as pipe:
    pipe.__aenter__  = AsyncMock(return_value=pipe)
    pipe.__aexit__   = AsyncMock(return_value=None)
    mock.pipeline    = MagicMock(return_value=pipe)
    return mock, pipe


def make_pg_mock() -> MagicMock:
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=conn),
        __aexit__=AsyncMock(return_value=None),
    ))
    return pool, conn


# ─── Testes de lógica de agregação ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_redis_incrby_called_with_correct_key_and_quantity():
    """INCRBY deve usar a chave correta e a quantidade do evento."""
    redis_mock, pipe = make_redis_mock()
    pg_pool, pg_conn = make_pg_mock()
    agg = UsageAggregator(redis_client=redis_mock, pg_pool=pg_pool)

    event = make_event(tenant_id="t1", dimension="sessions", quantity=1.0)
    await agg.process(event)

    pipe.incrbyfloat.assert_called_once_with("t1:usage:current:sessions", 1.0)


@pytest.mark.asyncio
async def test_redis_pipeline_called_with_transaction_true():
    """pipeline() deve ser chamado com transaction=True para garantir atomicidade (MULTI/EXEC)."""
    redis_mock, _ = make_redis_mock()
    pg_pool, _    = make_pg_mock()
    agg = UsageAggregator(redis_client=redis_mock, pg_pool=pg_pool)

    await agg.process(make_event())

    redis_mock.pipeline.assert_called_once_with(transaction=True)


@pytest.mark.asyncio
async def test_redis_incrby_uses_token_quantity_for_llm():
    """Tokens LLM com quantidade > 1 devem usar o valor correto."""
    redis_mock, pipe = make_redis_mock()
    pg_pool, pg_conn = make_pg_mock()
    agg = UsageAggregator(redis_client=redis_mock, pg_pool=pg_pool)

    event = make_event(
        dimension="llm_tokens_input",
        quantity=1240.0,
        source_component="ai-gateway",
        metadata={"model_id": "claude-sonnet-4-6"},
    )
    await agg.process(event)

    pipe.incrbyfloat.assert_called_once_with(
        "tenant-abc:usage:current:llm_tokens_input", 1240.0
    )


@pytest.mark.asyncio
async def test_postgres_insert_called_with_event_fields():
    """Deve inserir evento bruto com todos os campos."""
    redis_mock, _    = make_redis_mock()
    pg_pool, pg_conn = make_pg_mock()
    agg = UsageAggregator(redis_client=redis_mock, pg_pool=pg_pool)

    event = make_event(event_id="evt-unique-123")
    await agg.process(event)

    assert pg_conn.execute.call_count == 2  # usage_events + usage_hourly
    first_call_sql = pg_conn.execute.call_args_list[0][0][0]
    assert "usage_events" in first_call_sql
    assert "ON CONFLICT (event_id) DO NOTHING" in first_call_sql


@pytest.mark.asyncio
async def test_postgres_upsert_usage_hourly():
    """Deve fazer upsert em usage_hourly com a quantidade correta."""
    redis_mock, _    = make_redis_mock()
    pg_pool, pg_conn = make_pg_mock()
    agg = UsageAggregator(redis_client=redis_mock, pg_pool=pg_pool)

    event = make_event(quantity=5.0)
    await agg.process(event)

    second_call_sql = pg_conn.execute.call_args_list[1][0][0]
    assert "usage_hourly" in second_call_sql
    assert "ON CONFLICT" in second_call_sql
    assert "DO UPDATE" in second_call_sql
    # Quantidade passada como parâmetro posicional $4
    second_call_args = pg_conn.execute.call_args_list[1][0]
    assert 5.0 in second_call_args


@pytest.mark.asyncio
async def test_redis_failure_does_not_raise():
    """Falha no Redis não deve propagar exceção."""
    redis_mock = MagicMock()
    pipe = AsyncMock()
    pipe.incrbyfloat = AsyncMock(return_value=pipe)
    pipe.expire      = AsyncMock(return_value=pipe)
    pipe.set         = AsyncMock(return_value=pipe)
    pipe.execute     = AsyncMock(side_effect=ConnectionError("redis down"))
    pipe.__aenter__  = AsyncMock(return_value=pipe)
    pipe.__aexit__   = AsyncMock(return_value=None)
    redis_mock.pipeline = MagicMock(return_value=pipe)

    pg_pool, pg_conn = make_pg_mock()
    agg = UsageAggregator(redis_client=redis_mock, pg_pool=pg_pool)

    # Não deve levantar exceção
    await agg.process(make_event())


@pytest.mark.asyncio
async def test_postgres_failure_does_not_raise():
    """Falha no PostgreSQL não deve propagar exceção."""
    redis_mock, _    = make_redis_mock()
    pg_pool, pg_conn = make_pg_mock()
    pg_conn.execute  = AsyncMock(side_effect=Exception("pg down"))
    agg = UsageAggregator(redis_client=redis_mock, pg_pool=pg_pool)

    # Não deve levantar exceção
    await agg.process(make_event())


# ─── Testes de _truncate_to_hour ──────────────────────────────────────────────

def test_truncate_to_hour_removes_minutes_and_seconds():
    result = _truncate_to_hour("2026-04-21T10:30:45+00:00")
    assert result == "2026-04-21T10:00:00+00:00"


def test_truncate_to_hour_handles_utc_z():
    result = _truncate_to_hour("2026-04-21T23:59:59Z")
    assert result == "2026-04-21T23:00:00+00:00"


def test_truncate_to_hour_already_on_hour():
    result = _truncate_to_hour("2026-04-21T08:00:00+00:00")
    assert result == "2026-04-21T08:00:00+00:00"


# ─── Regression: event_id idempotency ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_duplicate_event_id_is_silently_ignored():
    """
    Regression: evento duplicado (mesmo event_id) não deve propagar erro.
    O PostgreSQL lança UniqueViolationError que deve ser capturado silenciosamente.
    """
    import asyncpg

    redis_mock, _    = make_redis_mock()
    pg_pool, pg_conn = make_pg_mock()
    pg_conn.execute  = AsyncMock(
        side_effect=asyncpg.UniqueViolationError("duplicate key")
    )
    agg = UsageAggregator(redis_client=redis_mock, pg_pool=pg_pool)

    # Não deve levantar exceção
    await agg.process(make_event(event_id="evt-duplicated"))
