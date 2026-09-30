"""AUD-09 — avaliações das sessões do titular: sai a CITAÇÃO da conversa, fica o resto.

Cada ramo tem o controle ao lado: um erase que não troca nada passaria em todo teste que só
olha "não apagou a nota", e um que zera a evidência passaria em todo teste que só olha
"a citação sumiu"."""
from __future__ import annotations

import asyncio
import json

from plughub_evaluation_api import db

M = "erased:req1"


def test_strip_quotes_replaces_every_quote_key_at_any_depth_and_keeps_the_rest():
    obj = [{"excerpt": "meu cpf e 123", "relevance_note": "cliente informou",
            "stream_entry_id": "e1"},
           {"nested": {"evidence": [{"excerpt": "rua X"}]}},
           {"divergence_points": [{"production_text": "a", "replay_text": "b", "similarity": 0.2}]}]
    new, n = db.strip_quotes(obj, M)
    assert n == 4
    assert new[0] == {"excerpt": M, "relevance_note": "cliente informou", "stream_entry_id": "e1"}
    assert new[1]["nested"]["evidence"][0]["excerpt"] == M
    assert new[2]["divergence_points"][0] == {"production_text": M, "replay_text": M, "similarity": 0.2}


def test_strip_quotes_counts_zero_when_already_erased_or_nothing_to_erase():
    assert db.strip_quotes([{"excerpt": M}], M)[1] == 0
    assert db.strip_quotes({"relevance_note": "x", "score": 3}, M) == ({"relevance_note": "x", "score": 3}, 0)
    assert db.QUOTE_KEYS == {"excerpt", "production_text", "replay_text"}, \
        "chave nova de citação entra aqui E no teste acima"


class _Conn:
    def __init__(self, tables):
        self.t, self.updates = tables, []

    def transaction(self):
        conn = self

        class _T:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *a):
                return False
        return _T()

    async def fetch(self, sql, *args):
        if "FROM evaluation.results" in sql:
            return self.t["results"]
        if "FROM evaluation.criterion_responses" in sql:
            return self.t["crit"]
        if "FROM evaluation.contestation_threads" in sql:
            return self.t["threads"]
        if "FROM evaluation.curation_result_blinds" in sql:
            return self.t.get("blinds", [])
        raise AssertionError(sql)

    async def execute(self, sql, *args):
        self.updates.append((sql, args))


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        conn = self.conn

        class _A:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *a):
                return False
        return _A()


def test_erase_rewrites_only_rows_with_quotes_and_never_touches_scores_or_notes():
    conn = _Conn({
        "results": [{"id": "r1", "instance_id": "i1", "comparison_report": None}],
        "crit": [{"id": "c1", "evidence": json.dumps([{"excerpt": "rua X", "relevance_note": "n"}])},
                 {"id": "c2", "evidence": "[]"}],
        "threads": [{"id": "t1", "evidence_entries": [{"excerpt": M}]}],
    })
    out = asyncio.run(db.evaluation_subject_erase(_Pool(conn), tenant_id="t", session_ids=["s1"], marker=M))
    assert out["criterion_responses"] == 1 and out["quotes_erased"] == 1
    assert out["contestation_threads"] == 0, "já trocada não conta nem regrava"
    assert len(conn.updates) == 1
    sql, (rid, payload) = conn.updates[0]
    assert "SET evidence = $2::jsonb" in sql and rid == "c1"
    assert json.loads(payload) == [{"excerpt": M, "relevance_note": "n"}]
    assert not any("score" in s or "notes" in s for s, _ in conn.updates)


def test_erase_without_sessions_or_results_does_nothing():
    conn = _Conn({"results": [], "crit": [], "threads": []})
    assert asyncio.run(db.evaluation_subject_erase(_Pool(conn), tenant_id="t", session_ids=[], marker=M))[
        "quotes_erased"] == 0
    asyncio.run(db.evaluation_subject_erase(_Pool(conn), tenant_id="t", session_ids=["s"], marker=M))
    assert conn.updates == []
