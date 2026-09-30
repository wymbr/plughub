"""
test_rules_engine.py
Five mandatory tests for the Rules Engine.
Spec: PlugHub v24.0 section 3.2 / 3.2b

1. Integration: read params from Redis key written by AI Gateway
2. Dry-run: no Kafka event published
3. Shadow: event to rules.shadow.events only
4. Active: publishes to rules.escalation.events — the bridge acts on it (RUL-02)
5. Lifecycle: draft → active rejected
"""

from __future__ import annotations
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from plughub_rules.dry_run import DryRunEngine
from plughub_rules.escalator import Escalator
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
            return True

        async def publish_escalation(self, trigger):
            escalation_events.append(trigger.model_dump())
            return True

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
# Test 4 — Active: publishes the escalation (RUL-02)
# ─────────────────────────────────────────────────────────────────────────────
#
# RUL-01 recusava o modo ativo (a escalação não tinha caminho). A RUL-02 deu o caminho: o
# evento vai ao bridge, que para a IA na fronteira do passo. Aqui o contrato é: ativa publica
# em `rules.escalation.events` (e só lá), com o aviso ao cliente da regra; publicação que
# falha NÃO devolve trigger — o chamador nunca afirma uma escalação que não saiu.

def _rule(status: str, target_pool: str | None = "pool_retencao", notice: str | None = None) -> Rule:
    now = datetime.now(timezone.utc).isoformat()
    return Rule(
        rule_id="rule_x", tenant_id="tenant_test", name="Rule X", status=status,
        conditions=[Condition(parameter="sentiment_score", operator="lt", value=-0.3)],
        logic="AND", target_pool=target_pool, created_at=now, updated_at=now,
        customer_notice=notice,
    )


def _fired(rule: Rule):
    ctx = EvaluationContext(session_id="s1", tenant_id="tenant_test",
                            sentiment_score=-0.5, intent_confidence=0.8)
    result = RuleEvaluator().evaluate(rule, ctx)
    assert result.triggered, "fixture: a regra tem de disparar, senão o teste não mede nada"
    return result


class _Publisher:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.shadow: list[dict] = []
        self.escalation: list[dict] = []

    async def publish_shadow(self, trigger):
        self.shadow.append(trigger.model_dump())
        return self.ok

    async def publish_escalation(self, trigger):
        self.escalation.append(trigger.model_dump())
        return self.ok


@pytest.mark.asyncio
async def test_active_publishes_the_escalation_with_the_notice():
    pub = _Publisher()
    trigger = await Escalator(kafka_publisher=pub).trigger(
        _fired(_rule("active", notice="Vou te passar para um especialista.")))
    assert trigger is not None and trigger.shadow_mode is False
    assert pub.shadow == [] and len(pub.escalation) == 1
    ev = pub.escalation[0]
    assert ev["target_pool"] == "pool_retencao" and ev["session_id"] == "s1"
    assert ev["customer_notice"] == "Vou te passar para um especialista."


@pytest.mark.asyncio
async def test_control_shadow_publishes_only_to_shadow():
    pub = _Publisher()
    trigger = await Escalator(kafka_publisher=pub).trigger(_fired(_rule("shadow")))
    assert trigger is not None and trigger.shadow_mode is True
    assert len(pub.shadow) == 1 and pub.escalation == []


@pytest.mark.asyncio
async def test_failed_publish_returns_nothing():
    pub = _Publisher(ok=False)
    assert await Escalator(kafka_publisher=pub).trigger(_fired(_rule("active"))) is None
    assert len(pub.escalation) == 1, "tentou publicar"


@pytest.mark.asyncio
async def test_without_publisher_nothing_is_claimed(caplog):
    with caplog.at_level("ERROR", logger="plughub.rules"):
        assert await Escalator().trigger(_fired(_rule("active"))) is None
    assert "NÃO há publicador" in caplog.text


@pytest.mark.asyncio
async def test_rule_without_pool_publishes_nothing():
    pub = _Publisher()
    assert await Escalator(kafka_publisher=pub).trigger(_fired(_rule("active", target_pool=None))) is None
    assert pub.shadow == [] and pub.escalation == []


def test_notice_has_a_ceiling():
    with pytest.raises(Exception):
        _rule("active", notice="x" * 501)


@pytest.mark.asyncio
async def test_publisher_keys_by_session_and_reports_failure():
    from plughub_rules.kafka_publisher import KafkaPublisher, TOPIC_ESCALATION

    class Prod:
        def __init__(self, fail=False):
            self.fail, self.sent = fail, []

        async def send_and_wait(self, topic, value=None, key=None):
            if self.fail:
                raise RuntimeError("broker down")
            self.sent.append((topic, key))

    trig = await Escalator(kafka_publisher=_Publisher()).trigger(_fired(_rule("active")))
    prod = Prod()
    assert await KafkaPublisher(prod).publish_escalation(trig) is True
    assert prod.sent == [(TOPIC_ESCALATION, b"s1")], "sem chave, eventos da sessão perdem a ordem"
    assert await KafkaPublisher(Prod(fail=True)).publish_escalation(trig) is False


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
async def test_activation_with_target_pool_now_passes():
    """RUL-02: a trava da RUL-01 saiu junto com o caminho que ela esperava."""
    reg = await _registry_with(_rule("shadow"))
    updated = await reg.update_status("tenant_test", "rule_x", "active")
    assert updated.status == "active" and updated.target_pool == "pool_retencao"


@pytest.mark.asyncio
async def test_control_lifecycle_still_guards_the_jump():
    """Controle: sair da trava não abre o ciclo — draft não pula para active."""
    reg = await _registry_with(_rule("draft"))
    with pytest.raises(ValueError):
        await reg.update_status("tenant_test", "rule_x", "active")


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
    # RUL-04: o sentimento que a regra vê é o do ContextStore, não o do payload.
    redis.hget.return_value = json.dumps({"value": -0.6, "confidence": 0.8})

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


# ─────────────────────────────────────────────────────────────────────────────
# Test 7 — RUL-02: a atualização REAL do ai-gateway (sentimento não medido = null)
# ─────────────────────────────────────────────────────────────────────────────
#
# O teste acima publica `sentiment_score: -0.6` — um payload que o ai-gateway não produz
# desde 2026-08-23 na maioria dos turnos: sem medida ele publica `null`. Com o campo
# exigindo float, TODA avaliação quebrava na validação (medido ao vivo na RUL-02), e o teste
# acima seguia verde. Este usa o payload de verdade.

def _payload(declared=None):
    # `sentiment_score` no payload é o AUTO-DECLARADO; desde a RUL-04 a regra o ignora.
    return {"type": "pmessage", "channel": "session:updates:s-null",
            "data": json.dumps({"session_id": "s-null", "tenant_id": "t", "sentiment_score": declared,
                                "intent_confidence": 0.0, "flags": [], "turn_count": 0,
                                "elapsed_ms": 900})}


async def _fire(rule, sentiment, declared=None):
    """`sentiment` = o MEDIDO, em `{t}:ctx:{sid}` › core.sentiment.current (None = sem a tag)."""
    redis = AsyncMock()
    redis.get.return_value = None
    redis.hget.return_value = (None if sentiment is None
                               else json.dumps({"value": sentiment, "confidence": 0.8}))
    store = AsyncMock(spec=RuleStore)
    store.get_active_rules.return_value = [rule]
    got: list = []

    class Esc:
        async def trigger(self, result):
            got.append(result)

    await _process_update(message=_payload(declared), rule_store=store,
                          evaluator=RuleEvaluator(), escalator=Esc(), redis=redis)
    return got


@pytest.mark.asyncio
async def test_unmeasured_sentiment_still_evaluates_the_other_conditions():
    now = datetime.now(timezone.utc).isoformat()
    rule = Rule(rule_id="r_el", tenant_id="t", name="tempo", status="active",
                conditions=[Condition(parameter="elapsed_ms", operator="gte", value=500)],
                target_pool="p", created_at=now, updated_at=now)
    got = await _fire(rule, None)
    assert len(got) == 1 and got[0].triggered, "sentimento nulo derrubava a avaliação inteira"
    assert got[0].context.sentiment_score is None


@pytest.mark.asyncio
async def test_unmeasured_sentiment_never_matches_a_sentiment_condition():
    """Controle: sem medida não é neutro — `sentiment_score lt 0.5` NÃO casa com null."""
    now = datetime.now(timezone.utc).isoformat()
    rule = Rule(rule_id="r_s", tenant_id="t", name="humor", status="active",
                conditions=[Condition(parameter="sentiment_score", operator="lt", value=0.5)],
                target_pool="p", created_at=now, updated_at=now)
    assert await _fire(rule, None) == [], "null tratado como neutro dispararia aqui"
    assert len(await _fire(rule, 0.1)) == 1, "com medida, casa"


# ─────────────────────────────────────────────────────────────────────────────
# Test 8 — RUL-04: a regra lê a MEDIÇÃO (ContextStore), e só ela
# ─────────────────────────────────────────────────────────────────────────────

def _sentiment_rule():
    now = datetime.now(timezone.utc).isoformat()
    return Rule(rule_id="r_m", tenant_id="t", name="humor", status="active",
                conditions=[Condition(parameter="sentiment_score", operator="lt", value=-0.3)],
                target_pool="p", created_at=now, updated_at=now)


@pytest.mark.asyncio
async def test_measured_sentiment_fires_even_when_the_payload_declares_none():
    got = await _fire(_sentiment_rule(), -0.8, declared=None)
    assert len(got) == 1 and got[0].context.sentiment_score == -0.8


@pytest.mark.asyncio
async def test_declared_payload_value_is_not_a_second_house():
    """O payload diz -0.9, o ContextStore não tem medida: não casa."""
    assert await _fire(_sentiment_rule(), None, declared=-0.9) == []
    assert await _fire(_sentiment_rule(), 0.4, declared=-0.9) == []


@pytest.mark.asyncio
async def test_the_measurement_trigger_without_turn_fields_still_reads_the_ctx():
    from plughub_rules.main import _build_context
    redis = AsyncMock()
    redis.get.return_value = json.dumps({"consolidated_turns": [{}, {}]})
    redis.hget.return_value = json.dumps({"value": -0.5})
    ctx = await _build_context(redis, "s1", "t", {"session_id": "s1", "tenant_id": "t",
                                                   "trigger": "sentiment_measured"})
    assert ctx.sentiment_score == -0.5 and ctx.turn_count == 2
    redis.hget.assert_awaited_with("t:ctx:s1", "core.sentiment.current")


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["nao-json", json.dumps({"value": "alto"}), json.dumps({"value": True})])
async def test_malformed_measurement_is_unmeasured_never_neutral(raw):
    from plughub_rules.session_reader import read_measured_sentiment
    redis = AsyncMock()
    redis.hget.return_value = raw
    assert await read_measured_sentiment(redis, "t", "s") is None


@pytest.mark.asyncio
async def test_unreadable_store_is_unmeasured():
    from plughub_rules.session_reader import read_measured_sentiment
    redis = AsyncMock()
    redis.hget.side_effect = ConnectionError("down")
    assert await read_measured_sentiment(redis, "t", "s") is None
