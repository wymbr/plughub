"""RUL-02 — a escalação por regra chega ao bridge, que decide e marca; a IA para sozinha.

O ramo que prova a decisão não é "marcou": marcar sempre também passaria nele. Pesam os
controles — humano presente, roster ausente, dois condutores, segunda regra na mesma sessão,
evento de shadow — porque é neles que a regra arrancaria um contato que não devia."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

from plughub_orchestrator_bridge import main as bridge_mod
from plughub_orchestrator_bridge import rule_escalation as re_mod

SID = "sess-1"
EV = {"session_id": SID, "tenant_id": "t", "rule_id": "r1", "rule_name": "Irritado",
      "target_pool": "humano_ret", "shadow_mode": False,
      "customer_notice": "Vou te passar para um especialista."}


def _ia(pid="agente_ia-001", role="primary", **kw):
    return {"participant_id": pid, "role": role, "agent_type": "native", **kw}


# ── decisão pura ─────────────────────────────────────────────────────────────

def test_ai_conductor_alone_is_escalable():
    d = re_mod.decide_conductor([_ia(), _ia("copilot-002", role="specialist")], set())
    assert d == re_mod.Decision(True, "ok", "agente_ia-001")


def test_human_present_refuses_by_set_or_by_roster():
    assert re_mod.decide_conductor([_ia()], {"human-u1"}).reason == "human_present"
    humano = {"participant_id": "human-u1", "role": "specialist", "agent_type": "human"}
    assert re_mod.decide_conductor([_ia(), humano], set()).reason == "human_present"


def test_human_who_left_does_not_block():
    saiu = {"participant_id": "human-u1", "role": "primary", "agent_type": "human", "left_at": "x"}
    assert re_mod.decide_conductor([saiu, _ia()], set()).ok


def test_no_positive_reading_never_acts():
    assert re_mod.decide_conductor(None, set()).reason == "no_roster"
    assert re_mod.decide_conductor([], set()).reason == "no_ai_conductor"
    assert re_mod.decide_conductor([_ia(left_at="x")], set()).reason == "no_ai_conductor"
    assert re_mod.decide_conductor([_ia(role="specialist")], set()).reason == "no_ai_conductor"


def test_two_conductors_is_ambiguous_not_a_guess():
    assert re_mod.decide_conductor([_ia(), _ia("outra-002")], set()).reason == "ambiguous_conductor"


def test_event_validation():
    assert re_mod.validate_event(EV) == ""
    assert "shadow" in re_mod.validate_event({**EV, "shadow_mode": True})
    assert re_mod.validate_event({**EV, "target_pool": ""}) != ""
    assert re_mod.validate_event({**EV, "customer_notice": "x" * 501}) != ""
    assert re_mod.validate_event({**EV, "customer_notice": None}) == ""


def test_mark_carries_instance_pool_and_trimmed_notice():
    m = json.loads(re_mod.build_mark({**EV, "customer_notice": "  oi  "}, "agente_ia-001"))
    assert (m["instance_id"], m["target_pool"], m["rule_id"], m["customer_notice"]) == \
        ("agente_ia-001", "humano_ret", "r1", "oi")
    assert "customer_notice" not in json.loads(re_mod.build_mark({**EV, "customer_notice": " "}, "i"))


# ── handler ──────────────────────────────────────────────────────────────────

class FakeRedis:
    def __init__(self, roster, humans=(), meta=True):
        self.kv = {f"session:{SID}:participants": json.dumps(roster)} if roster is not None else {}
        if meta:
            self.kv[f"session:{SID}:meta"] = "{}"
        self.sets = {f"session:{SID}:human_agents": set(humans)}
        self.lists: dict[str, list] = {}
        self.expires: dict[str, int] = {}

    async def exists(self, k):
        return int(k in self.kv)

    async def get(self, k):
        return self.kv.get(k)

    async def smembers(self, k):
        return set(self.sets.get(k, set()))

    async def set(self, k, v, ex=None, nx=False):
        if nx and k in self.kv:
            return None
        self.kv[k] = v
        return True

    async def lpush(self, k, v):
        self.lists.setdefault(k, []).insert(0, v)

    async def expire(self, k, s):
        self.expires[k] = s


def _run(redis):
    woken = []

    async def fake_wake(r, sid, field, reason):
        woken.append((sid, field, reason))
        return "not_parked"

    def fake_spawn(coro, **_):
        return asyncio.ensure_future(coro)

    async def go():
        with patch.object(bridge_mod, "wake_parked_run", fake_wake), \
             patch.object(bridge_mod, "_spawn", fake_spawn):
            out = await bridge_mod.process_rule_escalation(dict(EV), redis)
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            return out
    return asyncio.run(go()), woken


def test_marks_once_signals_the_conductor_and_wakes_it():
    r = FakeRedis([_ia()])
    out, woken = _run(r)
    assert out == "marked"
    mark = json.loads(r.kv[re_mod.mark_key(SID)])
    assert mark["instance_id"] == "agente_ia-001" and mark["target_pool"] == "humano_ret"
    fila = f"menu:signal:{SID}:agente_ia-001"
    assert r.lists[fila] == [json.dumps({"_rule_preempt": True})]
    assert r.expires[fila] == 3600
    assert woken == [(SID, "agente_ia-001", "rule_escalation")]


def test_second_trigger_in_the_same_session_is_dropped():
    r = FakeRedis([_ia()])
    _run(r)
    out, woken = _run(r)
    assert out == "already_escalated" and woken == []
    assert len(r.lists[f"menu:signal:{SID}:agente_ia-001"]) == 1


def test_refusal_writes_nothing_and_wakes_nobody():
    r = FakeRedis([_ia()], humans={"human-u1"})
    out, woken = _run(r)
    assert out == "human_present"
    assert re_mod.mark_key(SID) not in r.kv and r.lists == {} and woken == []


def test_session_gone_and_invalid_event():
    out, woken = _run(FakeRedis([_ia()], meta=False))
    assert out == "session_gone" and woken == []

    async def go():
        return await bridge_mod.process_rule_escalation({**EV, "shadow_mode": True}, FakeRedis([_ia()]))
    assert asyncio.run(go()) == "invalid"


def test_the_topic_is_consumed_and_dispatched():
    import inspect
    src = inspect.getsource(bridge_mod)
    assert bridge_mod.TOPIC_RULE_ESCALATION == "rules.escalation.events"
    assert "TOPIC_RULE_ESCALATION,\n        bootstrap_servers" in src, "o consumidor não assina o tópico"
    assert "elif topic == TOPIC_RULE_ESCALATION:\n        await process_rule_escalation(" in src
