"""AUD-09 — a porta de titular do session-replayer: só serviço, nunca o desmascarado, e a
eliminação anonimiza sem apagar a linha."""
from __future__ import annotations

from fastapi.testclient import TestClient

from session_replayer import data_subject_api as dsa

TOKEN = "svc-tok"
BODY = {"tenant_id": "t1", "session_ids": ["s1", "", "s2"]}


class _Conn:
    def __init__(self):
        self.calls = []

    def transaction(self):
        class _T:
            async def __aenter__(self_):
                return self

            async def __aexit__(self_, *a):
                return False
        return _T()

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        if "session_stream_events" in sql:
            return [{"session_id": "s1", "event_id": "e1", "payload": '{"text": "oi"}'}]
        return []

    async def execute(self, sql, *args):
        self.calls.append(("execute", sql, args))
        return "UPDATE 3"


class _Pool:
    def __init__(self):
        self.conn = _Conn()

    def acquire(self):
        conn = self.conn

        class _A:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *a):
                return False
        return _A()


def _client(pool=None, token=TOKEN):
    pool = pool or _Pool()
    return TestClient(dsa.build_app(lambda: pool, token)), pool


def test_without_configured_token_the_door_refuses_instead_of_opening():
    c, pool = _client(token="")
    r = c.post("/v1/data-subject/sessions/export", json=BODY, headers={"X-Service-Token": "x"})
    assert r.status_code == 503
    assert pool.conn.calls == []


def test_wrong_or_missing_token_is_401_and_touches_nothing():
    c, pool = _client()
    assert c.post("/v1/data-subject/sessions/export", json=BODY).status_code == 401
    assert c.post("/v1/data-subject/sessions/erase", json={**BODY, "marker": "erased:x"},
                  headers={"X-Service-Token": "outro"}).status_code == 401
    assert pool.conn.calls == []
    assert c.get("/health").status_code == 200


def test_export_never_returns_the_unmasked_text():
    c, pool = _client()
    r = c.post("/v1/data-subject/sessions/export", json=BODY, headers={"X-Service-Token": TOKEN})
    assert r.status_code == 200, r.text
    assert r.json()["stream_events"] == [{"session_id": "s1", "event_id": "e1", "payload": {"text": "oi"}}]
    ev_sql = pool.conn.calls[0][1]
    assert "payload - 'original_content'" in ev_sql
    assert "original_content," not in ev_sql and "original_content\n" not in ev_sql.split("AS payload")[1]
    assert pool.conn.calls[0][2] == ("t1", ["s1", "s2"]), "id vazio não vira filtro"


def test_erase_anonymizes_the_three_tables_and_keeps_the_rows():
    c, pool = _client()
    bad = c.post("/v1/data-subject/sessions/erase", json={**BODY, "marker": "x"},
                 headers={"X-Service-Token": TOKEN})
    assert bad.status_code == 422 and pool.conn.calls == []
    r = c.post("/v1/data-subject/sessions/erase", json={**BODY, "marker": "erased:r1"},
               headers={"X-Service-Token": TOKEN})
    assert r.json() == {"stream_events": 3, "context_snapshots": 3, "pipeline_states": 3}
    sqls = [s for _, s, _ in pool.conn.calls]
    assert all(s.lstrip().startswith("UPDATE") for s in sqls), "anonimiza; nunca DELETE"
    ev = sqls[0]
    assert "original_content = NULL" in ev
    assert "IS DISTINCT FROM jsonb_build_object('erased'" in ev, "sem isto, repetir recontaria"
    assert "author->>'role' = 'customer'" in ev
    assert "entries <> '{}'::jsonb" in sqls[1]
    assert "error_context IS NOT NULL" in sqls[2]
    assert pool.conn.calls[0][2] == ("t1", ["s1", "s2"], "erased:r1")


def test_no_sessions_no_database_call():
    c, pool = _client()
    r = c.post("/v1/data-subject/sessions/erase",
               json={"tenant_id": "t1", "session_ids": [], "marker": "erased:r1"},
               headers={"X-Service-Token": TOKEN})
    assert r.json() == {"stream_events": 0, "context_snapshots": 0, "pipeline_states": 0}
    assert pool.conn.calls == []
