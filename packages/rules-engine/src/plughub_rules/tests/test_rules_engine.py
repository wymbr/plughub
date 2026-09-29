"""
test_rules_engine.py
Five mandatory tests for the Rules Engine.
Spec: PlugHub v24.0 section 3.2 / 3.2b

1. Integration: read params from Redis key written by AI Gateway
2. Dry-run: no Kafka event published
3. Shadow: event to rules.shadow.events only
4. Active: REFUSED loudly — no escalation path yet (RUL-01/RUL-02); activation refused too
5. Lifecycle: draft → active rejected
"""

from __future__ import annotations
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from plughub_rules.dry_run import DryRunEngine
from plughub_rules.escalator import ESCALATION_PATH_TICKET, Escalator
from plughub_rules.rule_registry import RuleRegistry
from plughub_rules.evaluator import RuleEvaluator
from plughub_rules.lifecycle import validate_transition
from plughub_rules.main import _process_update
from plughub_rules.models import (
    Condition,
    DryRunRequest,
    EvaluationContext,
    Rule,
)
from plughub_rules.rule_store import RuleStore
from plughub_rules.session_reader import SessionParamsReader


# ─────────────────────────────────────────────────────────────────────────────
# Test 1 — Integration: read params from Redis key written by AI Gateway
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_evaluate_reads_params_from_redis_key():
    """
    SessionParamsReader reads {tenant_id}:session:{session_id}:turn:{turn_id}:params
    written by the AI Gateway. EvaluationContext is built from those params.
    """
    redis = AsyncMock()

    # Simulate what AI Gateway writes to Redis (inference.py _write_session_params)
    params = {
        "intent":          "cancellation",
        "confidence":      0.85,
        "sentiment_score": -0.7,
        "risk_flag":       True,
        "flags":           ["churn_signal"],
        "recorded_at":     1700000000,
    }
    redis.get.return_value = json.dumps(params)

    reader  = SessionParamsReader(redis)
    result  = await reader.read_turn_params("tenant_telco", "sess-001", "turn-001")

    assert result is not None
    assert result["sentiment_score"] == -0.7
    assert result["confidence"]      == 0.85
    assert "churn_signal" in result["flags"]

    # Verify the correct key was read
    expected_key = "tenant_telco:session:sess-001:turn:turn-001:params"
    redis.get.assert_called_once_with(expected_key)


# ─────────────────────────────────────────────────────────────────────────────
# Test 2 — Dry-run: no Kafka event published
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dry_run_does_not_publish_kafka_events():
    """
    dry_run_historico must NEVER publish to rules.shadow.events
    or rules.escalation.events — it is a pure simulation.
    """
    published: list[tuple[str, dict]] = []

    class FakePublisher:
        async def publish_shadow(self, trigger):
            published.append(("shadow", trigger.model_dump()))

        async def publish_escalation(self, trigger):
            published.append(("escalation", trigger.model_dump()))

    engine = DryRunEngine()  # pure simulation, no publisher
    now    = datetime.now(timezone.utc).isoformat()
    rule   = Rule(
        rule_id="rule_dry",
        tenant_id="tenant_test",
        name="Test",
        status="dry_run",
        conditions=[Condition(parameter="sentiment_score", operator="lt", value=-0.3)],
        logic="AND",
        target_pool="humano_retencao",
        created_at=now,
        updated_at=now,
    )
    sessions = [
        [EvaluationContext(
            session_id="s1", tenant_id="tenant_test",
            sentiment_score=-0.5, intent_confidence=0.8,
        )]
    ]
    req    = DryRunRequest(rule=rule, tenant_id="tenant_test")
    result = await engine.dry_run_historico(req, sessions)

    # Dry-run fires but no Kafka events
    assert result.would_trigger_count == 1
    assert len(published)             == 0, "Dry-run must never publish Kafka events"


# ─────────────────────────────────────────────────────────────────────────────
# Test 3 — Shadow: event to rules.shadow.events only
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_shadow_publishes_to_shadow_topic_only():
    """
    Shadow mode evaluates and publishes to rules.shadow.events.
    Must NOT publish to rules.escalation.events.
    """
    shadow_events:     list[dict] = []
    escalation_events: list[dict] = []

    class FakePublisher:
        async def publish_shadow(self, trigger):
            shadow_events.append(trigger.model_dump())

        async def publish_escalation(self, trigger):
            escalation_events.append(trigger.model_dump())

    escalator   = Escalator(kafka_publisher=FakePublisher())

    now  = datetime.now(timezone.utc).isoformat()
    rule = Rule(
        rule_id="rule_shadow",
        tenant_id="tenant_test",
        name="Shadow Rule",
        status="shadow",   # shadow mode
        conditions=[Condition(parameter="sentiment_score", operator="lt", value=-0.3)],
        logic="AND",
        target_pool="humano_retencao",
        created_at=now,
        updated_at=now,
    )
    ctx = EvaluationContext(
        session_id="s1", tenant_id="tenant_test",
        sentiment_score=-0.5, intent_confidence=0.8,
    )
    evaluator = RuleEvaluator()
    result    = evaluator.evaluate(rule, ctx)
    trigger   = await escalator.trigger(result)

    assert trigger is not None
    assert len(shadow_events)     == 1, "Must publish one shadow event"
    assert len(escalation_events) == 0, "Must NOT publish escalation events in shadow mode"
    assert shadow_events[0]["rule_id"] == "rule_shadow"


# ─────────────────────────────────────────────────────────────────────────────
# Test 4 — Active: REFUSED loudly (RUL-01)
# ─────────────────────────────────────────────────────────────────────────────
#
# Até 2026-09-29 este teste fazia o mock de `http.post` responder 200 e asseria o evento de
# escalação — o mock CRIOU a rota `/tools/conversation_escalate`, que o mcp-server nunca teve.
# Agora o contrato é o que o código consegue cumprir: nada escalado, nada publicado como se
# tivesse sido, e o log diz onde está o trabalho que falta.

def _rule(status: str, target_pool: str | None = "pool_retencao") -> Rule:
    now = datetime.now(timezone.utc).isoformat()
    return Rule(
        rule_id="rule_x", tenant_id="tenant_test", name="Rule X", status=status,
        conditions=[Condition(parameter="sentiment_score", operator="lt", value=-0.3)],
        logic="AND", target_pool=target_pool, created_at=now, updated_at=now,
    )


def _fired(rule: Rule):
    ctx = EvaluationContext(session_id="s1", tenant_id="tenant_test",
                            sentiment_score=-0.5, intent_confidence=0.8)
    result = RuleEvaluator().evaluate(rule, ctx)
    assert result.triggered, "fixture: a regra tem de disparar, senão o teste não mede nada"
    return result


class _Publisher:
    def __init__(self) -> None:
        self.shadow: list[dict] = []
        self.escalation: list[dict] = []

    async def publish_shadow(self, trigger):
        self.shadow.append(trigger.model_dump())

    async def publish_escalation(self, trigger):
        self.escalation.append(trigger.model_dump())


@pytest.mark.asyncio
async def test_active_is_refused_loudly_and_publishes_nothing(caplog):
    pub = _Publisher()
    with caplog.at_level("ERROR", logger="plughub.rules"):
        trigger = await Escalator(kafka_publisher=pub).trigger(_fired(_rule("active")))
    assert trigger is None
    assert pub.escalation == [] and pub.shadow == []
    assert "SEM CAMINHO" in caplog.text and ESCALATION_PATH_TICKET in caplog.text
    assert "pool_retencao" in caplog.text


@pytest.mark.asyncio
async def test_control_shadow_still_measures(caplog):
    """Controle: a recusa é do modo ATIVO — shadow continua medindo, sem ERROR."""
    pub = _Publisher()
    with caplog.at_level("ERROR", logger="plughub.rules"):
        trigger = await Escalator(kafka_publisher=pub).trigger(_fired(_rule("shadow")))
    assert trigger is not None and trigger.shadow_mode is True
    assert len(pub.shadow) == 1 and pub.escalation == []
    assert "SEM CAMINHO" not in caplog.text


class _FakeRedis:
    """O mínimo que o RuleRegistry usa: get/set/sadd/smembers."""
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.sets: dict[str, set] = {}

    async def get(self, k):
        return self.kv.get(k)

    async def set(self, k, v, *a, **kw):
        self.kv[k] = v

    async def sadd(self, k, *m):
        self.sets.setdefault(k, set()).update(m)

    async def smembers(self, k):
        return set(self.sets.get(k, set()))


async def _registry_with(rule: Rule) -> RuleRegistry:
    reg = RuleRegistry(_FakeRedis())
    await reg._redis.set(reg._rule_key(rule.tenant_id, rule.rule_id), rule.model_dump_json())
    await reg._redis.sadd(reg._index_key(rule.tenant_id), rule.rule_id)
    return reg


@pytest.mark.asyncio
async def test_activation_with_target_pool_is_refused():
    reg = await _registry_with(_rule("shadow"))
    with pytest.raises(ValueError) as exc:
        await reg.update_status("tenant_test", "rule_x", "active")
    assert ESCALATION_PATH_TICKET in str(exc.value)
    assert (await reg.get("tenant_test", "rule_x")).status == "shadow", "nada pode ter sido gravado"


@pytest.mark.asyncio
async def test_control_activation_without_target_pool_passes():
    """Controle: regra ativa SEM pool (sem escalação) segue válida — a recusa não é geral."""
    reg = await _registry_with(_rule("shadow", target_pool=None))
    updated = await reg.update_status("tenant_test", "rule_x", "active")
    assert updated.status == "active"


# ─────────────────────────────────────────────────────────────────────────────
# Test 5 — Lifecycle: draft → active rejected
# ─────────────────────────────────────────────────────────────────────────────

def test_lifecycle_draft_to_active_rejected():
    """
    A rule cannot go from draft directly to active.
    Must pass through dry_run first.
    """
    with pytest.raises(ValueError) as exc_info:
        validate_transition("draft", "active")

    assert "not allowed" in str(exc_info.value).lower()


def test_lifecycle_valid_transitions_accepted():
    """Valid transitions must not raise."""
    validate_transition("draft",    "dry_run")
    validate_transition("dry_run",  "shadow")
    validate_transition("shadow",   "active")
    validate_transition("active",   "disabled")
    validate_transition("disabled", "draft")


def test_lifecycle_dry_run_to_active_rejected():
    """dry_run → active is also not allowed (must go through shadow first)."""
    with pytest.raises(ValueError):
        validate_transition("dry_run", "active")


# ─────────────────────────────────────────────────────────────────────────────
# Test 6 — End-to-end: AI Gateway publishes → Rules Engine receives →
#           rule evaluates → escalation triggers
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pubsub_publish_triggers_escalation():
    """
    Simulates the full pub/sub path:
      1. AI Gateway publishes { session_id, tenant_id, sentiment_score, … }
         to session:updates:{session_id}
      2. Rules Engine _process_update receives the message
      3. RuleEvaluator fires the matching active rule
      4. Escalator.trigger is called with the fired result
    """
    now = datetime.now(timezone.utc).isoformat()
    rule = Rule(
        rule_id="rule_e2e",
        tenant_id="tenant_acme",
        name="Negative sentiment escalation",
        status="active",
        conditions=[Condition(parameter="sentiment_score", operator="lt", value=-0.3)],
        logic="AND",
        target_pool="pool_retention",
        created_at=now,
        updated_at=now,
    )

    # Redis returns the AI session state written by the AI Gateway (no history yet)
    redis = AsyncMock()
    redis.get.return_value = json.dumps({
        "consolidated_turns": [],
        "current_turn": {
            "llm_calls": [],
            "partial_params": {"intent": "cancel", "confidence": 0.9, "sentiment_score": -0.6},
            "detected_flags": ["churn_signal"],
        },
    })

    rule_store = AsyncMock(spec=RuleStore)
    rule_store.get_active_rules.return_value = [rule]

    triggered: list = []

    class CapturingEscalator:
        async def trigger(self, result):
            triggered.append(result)

    # Payload shape published by AI Gateway session.update_partial_params()
    payload = {
        "session_id":        "sess-e2e-001",
        "tenant_id":         "tenant_acme",
        "sentiment_score":   -0.6,
        "intent_confidence": 0.9,
        "flags":             ["churn_signal"],
        "turn_count":        0,
        "elapsed_ms":        4200,
    }
    message = {
        "type":    "pmessage",
        "channel": "session:updates:sess-e2e-001",
        "data":    json.dumps(payload),
    }

    await _process_update(
        message=    message,
        rule_store= rule_store,
        evaluator=  RuleEvaluator(),
        escalator=  CapturingEscalator(),
        redis=      redis,
    )

    assert len(triggered) == 1, "Escalator must be triggered once"
    assert triggered[0].triggered        is True
    assert triggered[0].rule.rule_id     == "rule_e2e"
    assert triggered[0].rule.target_pool == "pool_retention"
