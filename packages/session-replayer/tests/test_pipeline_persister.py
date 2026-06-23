"""
test_pipeline_persister.py — R5/B unit tests (no infra required).

Cobre a lógica net-nova do PipelineStatePersister:
  - _parse_ts / _as_json (helpers puros)
  - persist(): lê pipeline_state do Redis e faz upsert no PG (args corretos)
  - persist(): ausência de chave → no-op (return False)
  - fetch(): PG hit → source=postgres
  - fetch(): PG miss + Redis vivo → fallback source=redis
  - fetch(): ambos ausentes → None (→ policy adherence vira `na`)

Roda standalone (sem pytest):  python3 packages/session-replayer/tests/test_pipeline_persister.py
Ou via pytest:                 pytest packages/session-replayer/tests/test_pipeline_persister.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Permite importar o pacote sem instalação (src layout).
_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(_SRC))

from session_replayer.pipeline_persister import (  # noqa: E402
    PipelineStatePersister, _as_json, _parse_ts,
)


# ── Fakes ──────────────────────────────────────────────────────────────────────

class FakeRedis:
    def __init__(self, store: dict[str, str] | None = None) -> None:
        self._store = store or {}

    async def get(self, key: str):
        return self._store.get(key)


class _AcquireCtx:
    def __init__(self, conn): self._conn = conn
    async def __aenter__(self): return self._conn
    async def __aexit__(self, *a): return False


class FakeConn:
    def __init__(self, row=None) -> None:
        self.row = row
        self.executes: list[tuple] = []

    async def execute(self, sql, *args):
        self.executes.append((sql, args))
        return "INSERT 0 1"

    async def fetchrow(self, sql, *args):
        return self.row


class FakePool:
    def __init__(self, conn: FakeConn) -> None:
        self._conn = conn

    def acquire(self):
        return _AcquireCtx(self._conn)


PIPELINE_JSON = json.dumps({
    "flow_id": "skill_retencao_v2",
    "current_step_id": "complete_ok",
    "status": "completed",
    "started_at": "2026-06-20T10:00:00Z",
    "updated_at": "2026-06-20T10:05:00Z",
    "retry_counters": {"catch_1": 2},
    "transitions": [
        {"from_step": "login", "to_step": "decide", "reason": "on_success", "timestamp": "2026-06-20T10:00:01Z"},
        {"from_step": "decide", "to_step": "complete_ok", "reason": "condition_match", "timestamp": "2026-06-20T10:04:59Z"},
    ],
})


# ── Tests ──────────────────────────────────────────────────────────────────────

def test_parse_ts():
    assert _parse_ts(None) is None
    assert _parse_ts("not-a-date") is None
    dt = _parse_ts("2026-06-20T10:00:00Z")
    assert isinstance(dt, datetime) and dt.tzinfo is not None


def test_as_json():
    assert _as_json(None, []) == []
    assert _as_json('[1,2]', None) == [1, 2]
    assert _as_json([1, 2], None) == [1, 2]          # already parsed (asyncpg may return obj)
    assert _as_json("garbage", {"d": 1}) == {"d": 1}  # fallback to default


async def test_persist_writes_upsert():
    conn = FakeConn()
    p = PipelineStatePersister(
        FakeRedis({"tenant_x:pipeline:sess1": PIPELINE_JSON}), FakePool(conn),
    )
    ok = await p.persist("sess1", "tenant_x")
    assert ok is True
    assert len(conn.executes) == 1
    _sql, args = conn.executes[0]
    # args order: tenant, session, flow_id, status, current_step_id, transitions,
    #             retry_counters, error_context, started_at, updated_at
    assert args[0] == "tenant_x"
    assert args[1] == "sess1"
    assert args[2] == "skill_retencao_v2"
    assert args[3] == "completed"
    assert json.loads(args[5]) and len(json.loads(args[5])) == 2  # 2 transitions
    assert json.loads(args[6]) == {"catch_1": 2}
    assert args[7] is None  # no error_context


async def test_persist_absent_key_is_noop():
    conn = FakeConn()
    p = PipelineStatePersister(FakeRedis({}), FakePool(conn))
    ok = await p.persist("sess_missing", "tenant_x")
    assert ok is False
    assert conn.executes == []


async def test_fetch_pg_hit():
    row = {
        "flow_id": "skill_x_v1", "status": "completed", "current_step_id": "done",
        "transitions": '[{"from_step":"a","to_step":"b","reason":"on_success","timestamp":"t"}]',
        "retry_counters": "{}", "error_context": None,
        "started_at": datetime.now(timezone.utc), "updated_at": datetime.now(timezone.utc),
    }
    p = PipelineStatePersister(FakeRedis({}), FakePool(FakeConn(row=row)))
    out = await p.fetch("sess1", "tenant_x")
    assert out is not None
    assert out["source"] == "postgres"
    assert out["flow_id"] == "skill_x_v1"
    assert isinstance(out["transitions"], list) and len(out["transitions"]) == 1


async def test_fetch_redis_fallback():
    # PG empty (row=None), Redis still has the live key → fallback.
    p = PipelineStatePersister(
        FakeRedis({"tenant_x:pipeline:sess1": PIPELINE_JSON}), FakePool(FakeConn(row=None)),
    )
    out = await p.fetch("sess1", "tenant_x")
    assert out is not None
    assert out["source"] == "redis"
    assert out["flow_id"] == "skill_retencao_v2"
    assert len(out["transitions"]) == 2


async def test_fetch_absent_returns_none():
    p = PipelineStatePersister(FakeRedis({}), FakePool(FakeConn(row=None)))
    out = await p.fetch("sess_missing", "tenant_x")
    assert out is None


# ── Runner ───────────────────────────────────────────────────────────────────

def _main() -> int:
    sync_tests = [test_parse_ts, test_as_json]
    async_tests = [
        test_persist_writes_upsert, test_persist_absent_key_is_noop,
        test_fetch_pg_hit, test_fetch_redis_fallback, test_fetch_absent_returns_none,
    ]
    failed = 0
    for t in sync_tests:
        try:
            t(); print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failed += 1; print(f"  FAIL {t.__name__}: {e}")
    for t in async_tests:
        try:
            asyncio.run(t()); print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failed += 1; print(f"  FAIL {t.__name__}: {e}")
    print(f"\n{'ALL PASS' if failed == 0 else f'{failed} FAILED'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())
