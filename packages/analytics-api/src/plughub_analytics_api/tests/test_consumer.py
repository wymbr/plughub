"""
test_consumer.py
Unit tests for the Analytics API event parsers (models.py) and
consumer routing logic (_write_row dispatch).

Strategy:
  - All parsers are pure functions — no I/O mocked.
  - ClickHouse writes are mocked via AsyncMock on AnalyticsStore methods.
  - Verifies: correct table targeting, required fields, None returns on bad input.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from ..models import (
    parse_inbound,
    parse_routed,
    parse_queued,
    parse_conversations_event,
    parse_agent_lifecycle,
    parse_usage_event,
    parse_sentiment_event,
    parse_queue_position,
    parse_participant_event,
    parse_evaluation_event,
    parse_session_signal_event,
    _normalize_signal_value,
    origin_from_source,
)
from ..consumer import (
    _write_row,
    _enrich_signal_session_at,
    _session_opened_cache,
    _learn_session_identity,
    _inject_session_identity,
    _session_identity_cache,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def make_store() -> MagicMock:
    s = MagicMock()
    s.upsert_session               = AsyncMock()
    s.insert_queue_event           = AsyncMock()
    s.insert_message               = AsyncMock()
    s.insert_usage_event           = AsyncMock()
    s.insert_sentiment_event       = AsyncMock()
    s.upsert_participation_interval = AsyncMock()
    s.upsert_segment               = AsyncMock()
    s.insert_timeline_event        = AsyncMock()
    s.upsert_evaluation_result     = AsyncMock()
    s.insert_evaluation_event      = AsyncMock()
    s.insert_evaluation_dimension_score = AsyncMock()
    s.insert_contact_insight       = AsyncMock()
    s.insert_agent_business_event  = AsyncMock()
    s.insert_session_signal        = AsyncMock()
    s.lookup_session_opened_at     = AsyncMock(return_value=None)
    return s


SESSION = "sess-analytics-001"
TENANT  = "tenant_telco"
POOL    = "retencao_humano"


# ── parse_inbound ─────────────────────────────────────────────────────────────

class TestParseInbound:
    def test_returns_session_row(self):
        row = parse_inbound({
            "session_id": SESSION, "tenant_id": TENANT,
            "channel": "webchat", "pool_id": POOL,
            "started_at": "2026-01-01T10:00:00+00:00",
        })
        assert row is not None
        assert row["table"] == "sessions"
        assert row["session_id"] == SESSION
        assert row["tenant_id"] == TENANT
        assert row["channel"] == "webchat"
        assert row["pool_id"] == POOL
        assert row["closed_at"] is None

    def test_returns_none_without_session_id(self):
        assert parse_inbound({"tenant_id": TENANT, "channel": "webchat"}) is None

    def test_returns_none_without_tenant_id(self):
        assert parse_inbound({"session_id": SESSION, "channel": "webchat"}) is None

    def test_empty_pool_id_when_missing(self):
        row = parse_inbound({"session_id": SESSION, "tenant_id": TENANT})
        assert row["pool_id"] == ""


# ── parse_routed ──────────────────────────────────────────────────────────────

class TestParseRouted:
    def _payload(self, allocated=True):
        return {
            "session_id": SESSION,
            "tenant_id":  TENANT,
            "routed_at":  "2026-01-01T10:00:01+00:00",
            "result": {
                "allocated":    allocated,
                "pool_id":      POOL,
                "instance_id":  "inst-001",
                "routing_mode": "autonomous",
            },
        }

    def test_returns_only_sessions_row(self):
        # A linha `agent_events` saiu em 2026-07-28 junto com a tabela (substrato
        # derivado que duplicava `segments`). O roteamento continua virando
        # segmento via `conversations.participants` → `segments.started_at`.
        rows = parse_routed(self._payload())
        assert rows is not None
        assert len(rows) == 1
        assert {r["table"] for r in rows} == {"sessions"}

    def test_sessions_row_has_pool_id(self):
        rows = parse_routed(self._payload())
        sess = next(r for r in rows if r["table"] == "sessions")
        assert sess["pool_id"] == POOL

    def test_returns_none_without_session_id(self):
        assert parse_routed({"tenant_id": TENANT, "result": {}}) is None

    def test_conference_routing_skips_sessions_row(self):
        # Hook/specialist routings (conference_id set) are segment-level and must
        # NOT write the contact-level sessions row — otherwise a wrap-up routing
        # arriving after contact_closed re-opens the session (no-version
        # ReplacingMergeTree, last-inserted-wins) and clobbers the contact pool.
        #
        # Invariante preservado; o que mudou é que agora NADA sobra (antes sobrava
        # a linha de agent_events).
        payload = self._payload()
        payload["result"]["conference_id"] = "conf-xyz"
        rows = parse_routed(payload)
        assert rows is not None
        assert rows == []
        assert all(r["table"] != "sessions" for r in rows)

    def test_primary_routing_still_writes_sessions_row(self):
        # No conference_id (primary allocation) → sessions row present.
        rows = parse_routed(self._payload())
        assert any(r["table"] == "sessions" for r in rows)


# ── parse_queued ──────────────────────────────────────────────────────────────

class TestParseQueued:
    def _payload(self):
        return {
            "session_id": SESSION,
            "tenant_id":  TENANT,
            "routed_at":  "2026-01-01T10:00:02+00:00",
            "result":     {"pool_id": POOL},
        }

    def test_returns_sessions_and_queue_event(self):
        rows = parse_queued(self._payload())
        assert rows is not None
        tables = {r["table"] for r in rows}
        assert tables == {"sessions", "queue_events"}

    def test_queue_event_type_is_queued(self):
        rows = parse_queued(self._payload())
        qe = next(r for r in rows if r["table"] == "queue_events")
        assert qe["event_type"] == "queued"
        assert qe["pool_id"] == POOL

    def test_conference_queued_skips_sessions_row(self):
        # Same rationale as parse_routed: a conference/hook agent queued must not
        # touch the contact-level sessions row.
        payload = self._payload()
        payload["result"]["conference_id"] = "conf-xyz"
        rows = parse_queued(payload)
        assert rows is not None
        tables = {r["table"] for r in rows}
        assert tables == {"queue_events"}


# ── parse_conversations_event ─────────────────────────────────────────────────

class TestParseConversationsEvent:
    def test_contact_open(self):
        rows = parse_conversations_event({
            "event_type": "contact_open",
            "session_id": SESSION, "tenant_id": TENANT,
            "channel": "webchat", "started_at": "2026-01-01T10:00:00+00:00",
        })
        assert rows is not None
        assert rows[0]["table"] == "sessions"
        assert rows[0]["channel"] == "webchat"

    def test_contact_closed_sets_closed_at(self):
        rows = parse_conversations_event({
            "event_type":  "contact_closed",
            "session_id":  SESSION, "tenant_id": TENANT,
            "channel":     "webchat",
            "started_at":  "2026-01-01T10:00:00+00:00",
            "ended_at":    "2026-01-01T10:05:00+00:00",
            "reason":      "flow_complete",
            "outcome":     "resolved",
        })
        assert rows is not None
        row = rows[0]
        assert row["table"] == "sessions"
        assert row["closed_at"] == "2026-01-01T10:05:00+00:00"
        assert row["close_reason"] == "flow_complete"
        assert row["outcome"] == "resolved"

    def test_message_sent(self):
        rows = parse_conversations_event({
            "event_type":   "message_sent",
            "session_id":   SESSION, "tenant_id": TENANT,
            "message_id":   "msg-001",
            "author_role":  "primary",
            "channel":      "webchat",
            "content_type": "text",
            "visibility":   "all",
            "timestamp":    "2026-01-01T10:01:00+00:00",
        })
        assert rows is not None
        row = rows[0]
        assert row["table"] == "messages"
        assert row["message_id"] == "msg-001"
        assert row["visibility"] == "all"

    def test_unknown_event_type_returns_none(self):
        assert parse_conversations_event({
            "event_type": "conference_agent_completed",
            "session_id": SESSION, "tenant_id": TENANT,
        }) is None

    def test_missing_ids_returns_none(self):
        assert parse_conversations_event({"event_type": "contact_open"}) is None


# ── parse_agent_lifecycle ─────────────────────────────────────────────────────

class TestParseAgentLifecycle:
    def test_agent_done_is_not_persisted(self):
        # 2026-07-28 — o ramo agent_done → agent_events saiu com a tabela. O EVENTO
        # continua existindo em `agent.lifecycle` (o routing-engine depende dele
        # para liberar capacidade); o que saiu foi a gravação analítica redundante.
        # O fim de atendimento é `segments.ended_at` + `outcome`, via
        # `conversations.participants` (ver TestParseParticipantEvent).
        assert parse_agent_lifecycle({
            "event":          "agent_done",
            "session_id":     SESSION,
            "tenant_id":      TENANT,
            "agent_type_id":  "agente_retencao_v1",
            "pool_id":        POOL,
            "instance_id":    "inst-001",
            "outcome":        "resolved",
            "handoff_reason": None,
            "handle_time_ms": 12000,
        }) is None

    def test_non_agent_done_returns_none(self):
        assert parse_agent_lifecycle({"event": "agent_ready", "session_id": SESSION, "tenant_id": TENANT}) is None
        assert parse_agent_lifecycle({"event": "agent_busy", "session_id": SESSION, "tenant_id": TENANT}) is None


# ── parse_usage_event ─────────────────────────────────────────────────────────

class TestParseUsageEvent:
    def test_passthrough_fields(self):
        row = parse_usage_event({
            "event_id":         "evt-001",
            "tenant_id":        TENANT,
            "session_id":       SESSION,
            "dimension":        "llm_tokens_input",
            "quantity":         512,
            "source_component": "ai-gateway",
            "timestamp":        "2026-01-01T10:02:00+00:00",
        })
        assert row is not None
        assert row["table"] == "usage_events"
        assert row["event_id"] == "evt-001"
        assert row["dimension"] == "llm_tokens_input"
        assert row["quantity"] == 512

    def test_missing_required_fields_returns_none(self):
        assert parse_usage_event({"tenant_id": TENANT}) is None
        assert parse_usage_event({"event_id": "x", "tenant_id": TENANT}) is None

    # ── T2/D3 — o metadata deixou de ser descartado ───────────────────────────

    def test_promove_metadata_a_colunas(self):
        """
        Até 2026-08-28 este parser jogava fora o `metadata` INTEIRO. Medido ao vivo
        na T1: duas linhas de caminhos diferentes (reason e sentiment) chegavam ao
        ClickHouse indistinguíveis, enquanto a cópia do Postgres sabia separá-las.
        """
        row = parse_usage_event({
            "event_id":         "evt-t2",
            "tenant_id":        TENANT,
            "session_id":       SESSION,
            "segment_id":       "seg-abc",
            "dimension":        "llm_tokens_input",
            "quantity":         97,
            "source_component": "ai-gateway",
            "timestamp":        "2026-08-28T10:00:00+00:00",
            "metadata": {
                "source":            "sentiment",
                "model_id":          "claude-haiku-4-5",
                "model_profile":     "fast",
                "account_config_id": "conta_prod_br",
                "account_key_id":    "754d27f40eb21592",
            },
        })
        assert row is not None
        assert row["segment_id"]        == "seg-abc"
        assert row["source"]            == "sentiment"
        assert row["model_id"]          == "claude-haiku-4-5"
        assert row["model_profile"]     == "fast"
        assert row["account_config_id"] == "conta_prod_br"
        assert row["account_key_id"]    == "754d27f40eb21592"

    def test_sem_metadata_nao_quebra_e_nao_inventa(self):
        """
        Evento legado (anterior à T2) tem de continuar entrando — com os campos
        VAZIOS, nunca com valor fabricado. Distinguir "não medíamos" de "não
        informado" é papel da época (`usage_attribution.USAGE_ATTRIBUTION_EPOCH`),
        não de um default aqui.
        """
        row = parse_usage_event({
            "event_id":         "evt-legado",
            "tenant_id":        TENANT,
            "dimension":        "llm_tokens_output",
            "quantity":         8,
            "source_component": "ai-gateway",
            "timestamp":        "2026-01-01T10:00:00+00:00",
        })
        assert row is not None
        for campo in ("segment_id", "source", "model_id",
                      "model_profile", "account_config_id", "account_key_id"):
            assert row[campo] == "", campo

    def test_dois_caminhos_ficam_distinguiveis(self):
        """
        O teste que descreve o defeito que a T2 conserta: mesma sessão, mesma
        dimensão, caminhos diferentes. Antes, as duas linhas eram idênticas em
        tudo que o ClickHouse guardava.
        """
        base = {
            "tenant_id": TENANT, "session_id": SESSION,
            "dimension": "llm_tokens_input", "quantity": 100,
            "source_component": "ai-gateway",
            "timestamp": "2026-08-28T10:00:00+00:00",
        }
        a = parse_usage_event({**base, "event_id": "a",
                               "metadata": {"source": "reason", "model_id": "sonnet"}})
        b = parse_usage_event({**base, "event_id": "b",
                               "metadata": {"source": "sentiment", "model_id": "haiku"}})
        assert (a["source"], a["model_id"]) != (b["source"], b["model_id"])


# ── parse_sentiment_event ─────────────────────────────────────────────────────

class TestParseSentimentEvent:
    def test_passthrough_fields(self):
        row = parse_sentiment_event({
            "event_id":   "evt-sent-001",
            "tenant_id":  TENANT,
            "session_id": SESSION,
            "pool_id":    POOL,
            "score":      0.72,
            "category":   "satisfied",
            "timestamp":  "2026-01-01T10:03:00+00:00",
        })
        assert row is not None
        assert row["table"] == "sentiment_events"
        assert row["score"] == 0.72
        assert row["category"] == "satisfied"
        assert row["pool_id"] == POOL

    def test_missing_required_returns_none(self):
        assert parse_sentiment_event({"tenant_id": TENANT}) is None


# ── parse_queue_position ──────────────────────────────────────────────────────

class TestParseQueuePosition:
    def test_returns_position_updated_row(self):
        row = parse_queue_position({
            "event":             "queue.position_updated",
            "session_id":        SESSION,
            "tenant_id":         TENANT,
            "pool_id":           POOL,
            "queue_length":      3,
            "estimated_wait_ms": 90000,
            "available_agents":  0,
            "published_at":      "2026-01-01T10:00:05+00:00",
        })
        assert row is not None
        assert row["table"] == "queue_events"
        assert row["event_type"] == "position_updated"
        assert row["queue_position"] == 3
        assert row["estimated_wait_ms"] == 90000

    def test_available_agents_is_NOT_passed_through(self):
        """F5: mesmo que alguém volte a publicar o campo, ele não entra na linha.

        A asserção antiga era `row["available_agents"] == 0` — passthrough do payload
        que o próprio teste fornecia, num campo cujo produtor morreu em 2026-08-02.
        Verde por construção, e documentando um contrato revogado: quem lesse o teste
        concluiria que o pipeline suporta o campo.

        Invertida, ela vira a guarda do que se decidiu: o valor era
        `SCARD(pool:instances)` — PERTENCIMENTO, não capacidade — e reintroduzi-lo
        contaminaria a série de novo, sem nada ficar vermelho.
        """
        row = parse_queue_position({
            "event":            "queue.position_updated",
            "session_id":       SESSION,
            "tenant_id":        TENANT,
            "pool_id":          POOL,
            "queue_length":     3,
            "available_agents": 7,          # produtor ressuscitado por engano
        })
        assert row["available_agents"] is None


# ── _write_row dispatch ───────────────────────────────────────────────────────

class TestWriteRowDispatch:
    async def test_sessions_dispatched_to_upsert_session(self):
        store = make_store()
        await _write_row(store, {"table": "sessions", "session_id": SESSION, "tenant_id": TENANT}, "topic", 0)
        store.upsert_session.assert_called_once()

    async def test_queue_events_dispatched(self):
        store = make_store()
        await _write_row(store, {"table": "queue_events"}, "topic", 0)
        store.insert_queue_event.assert_called_once()

    async def test_messages_dispatched(self):
        store = make_store()
        await _write_row(store, {"table": "messages"}, "topic", 0)
        store.insert_message.assert_called_once()

    async def test_usage_events_dispatched(self):
        store = make_store()
        await _write_row(store, {"table": "usage_events"}, "topic", 0)
        store.insert_usage_event.assert_called_once()

    async def test_sentiment_events_dispatched(self):
        store = make_store()
        await _write_row(store, {"table": "sentiment_events"}, "topic", 0)
        store.insert_sentiment_event.assert_called_once()

    async def test_participation_intervals_dispatched(self):
        store = make_store()
        await _write_row(store, {"table": "participation_intervals"}, "conversations.participants", 0)
        store.upsert_participation_interval.assert_called_once()

    async def test_segments_dispatched(self):
        store = make_store()
        await _write_row(store, {"table": "segments"}, "conversations.participants", 0)
        store.upsert_segment.assert_called_once()

    async def test_session_timeline_dispatched(self):
        store = make_store()
        await _write_row(store, {"table": "session_timeline"}, "conversations.participants", 0)
        store.insert_timeline_event.assert_called_once()

    async def test_unknown_table_does_not_call_any_method(self):
        store = make_store()
        await _write_row(store, {"table": "unknown_table"}, "topic", 0)
        store.upsert_session.assert_not_called()
        store.insert_queue_event.assert_not_called()


# ── parse_participant_event ───────────────────────────────────────────────────

PARTICIPANT = "part-agent-001"
INSTANCE    = "agente_retencao_v1-001"


class TestParseParticipantEvent:
    def _joined_payload(self, conference_id: str | None = None) -> dict:
        p = {
            "type":           "participant_joined",
            "session_id":     SESSION,
            "tenant_id":      TENANT,
            "participant_id": PARTICIPANT,
            "pool_id":        POOL,
            "agent_type_id":  "agente_retencao_v1",
            "role":           "primary",
            "agent_type":     "ai",
            "joined_at":      "2026-01-01T10:00:00+00:00",
            "timestamp":      "2026-01-01T10:00:00+00:00",
        }
        if conference_id:
            p["conference_id"] = conference_id
        return p

    def _left_payload(self) -> dict:
        return {
            "type":           "participant_left",
            "session_id":     SESSION,
            "tenant_id":      TENANT,
            "participant_id": PARTICIPANT,
            "pool_id":        POOL,
            "agent_type_id":  "agente_retencao_v1",
            "role":           "primary",
            "agent_type":     "ai",
            "joined_at":      "2026-01-01T10:00:00+00:00",
            "duration_ms":    180000,
            "timestamp":      "2026-01-01T10:03:00+00:00",
        }

    # Arc 5: parse_participant_event now returns a list of 2 rows:
    #   [0] participation_intervals row  (legacy)
    #   [1] segments row                 (Arc 5 ContactSegment)

    def test_participant_joined_returns_two_rows(self):
        rows = parse_participant_event(self._joined_payload())
        assert rows is not None
        assert isinstance(rows, list)
        assert len(rows) == 2

    def test_participation_row_correct(self):
        rows = parse_participant_event(self._joined_payload())
        row = rows[0]
        assert row["table"] == "participation_intervals"
        assert row["session_id"] == SESSION
        assert row["tenant_id"] == TENANT
        assert row["participant_id"] == PARTICIPANT
        assert row["pool_id"] == POOL
        assert row["role"] == "primary"
        assert row["type"] == "participant_joined"
        assert row["duration_ms"] is None

    def test_segment_row_correct(self):
        rows = parse_participant_event(self._joined_payload())
        seg = rows[1]
        assert seg["table"] == "segments"
        assert seg["session_id"] == SESSION
        assert seg["tenant_id"] == TENANT
        assert seg["participant_id"] == PARTICIPANT
        assert seg["pool_id"] == POOL
        assert seg["role"] == "primary"
        assert isinstance(seg["segment_id"], str) and len(seg["segment_id"]) > 0
        assert seg["sequence_index"] == 0

    def test_segment_id_passed_through(self):
        payload = self._joined_payload()
        payload["segment_id"] = "fixed-seg-uuid"
        rows = parse_participant_event(payload)
        seg = rows[1]
        assert seg["segment_id"] == "fixed-seg-uuid"

    def test_sequence_index_passed_through(self):
        payload = self._joined_payload()
        payload["segment_id"] = "seg-1"
        payload["sequence_index"] = 2
        rows = parse_participant_event(payload)
        assert rows[1]["sequence_index"] == 2

    def test_parent_segment_id_passed_through(self):
        payload = self._joined_payload()
        payload["parent_segment_id"] = "parent-seg-uuid"
        rows = parse_participant_event(payload)
        assert rows[1]["parent_segment_id"] == "parent-seg-uuid"

    def test_participant_left_has_duration(self):
        rows = parse_participant_event(self._left_payload())
        assert rows is not None
        assert rows[0]["table"] == "participation_intervals"
        assert rows[0]["type"] == "participant_left"
        assert rows[0]["duration_ms"] == 180000

    def test_escalation_reason_mapped_to_segment(self):
        payload = self._left_payload()
        payload["outcome"] = "escalated"
        payload["escalation_reason"] = "needs_authorization"
        payload["handoff_reason"] = "cliente pediu desconto acima do limite"
        rows = parse_participant_event(payload)
        seg = next(r for r in rows if r["table"] == "segments")
        assert seg["escalation_reason"] == "needs_authorization"
        assert seg["handoff_reason"] == "cliente pediu desconto acima do limite"

    def test_escalation_reason_absent_is_none(self):
        rows = parse_participant_event(self._left_payload())
        seg = next(r for r in rows if r["table"] == "segments")
        assert seg.get("escalation_reason") is None

    # ── D14 (ii): alvo de espera carimbado no SEGMENTO ───────────────────────
    #
    # ⚠️ Este parser é uma **ALLOWLIST**. Campo que o produtor envie e que não
    # esteja no dict é descartado em silêncio: a coluna nasce NULL com o produtor
    # correto, o consumidor verde e nada vermelho em lugar nenhum. É o modo de
    # falha mais barato desta fatia inteira, e é o que os dois testes abaixo
    # existem para pegar.

    def test_sla_target_ms_survives_the_allowlist(self):
        payload = self._left_payload()
        payload["role"] = "queue"
        payload["sla_target_ms"] = 300_000
        rows = parse_participant_event(payload)
        seg = next(r for r in rows if r["table"] == "segments")
        assert seg["sla_target_ms"] == 300_000, (
            "o alvo não atravessou o parser — a allowlist o descartou e a coluna "
            "nasceria NULL com todo o pipeline verde"
        )

    def test_sla_target_ms_absent_is_none_not_zero(self):
        """
        TESTEMUNHA NEGATIVA do teste acima. Segmento que não é espera não tem
        alvo — e a ausência tem de chegar como `None`, nunca `0`.

        Se alguém "consertar" isto com `payload.get("sla_target_ms", 0)` ou com
        `or 0`, todo segmento de atendimento passaria a declarar alvo zero, e
        toda espera do relatório viraria violação.

        ⚠️ `.get()`, não indexação — e a diferença foi MEDIDA, não estilo. Com
        `seg["sla_target_ms"]` este teste levantava `KeyError` quando o campo
        saía da allowlist, ou seja, reprovava por **presença de chave** em vez da
        proposição que ele declara. Reprovar pelo motivo errado é o mesmo defeito
        que passar pelo motivo errado: presença é trabalho do teste acima, e
        misturar os dois deixaria a suíte sem quem responda "ausente vira None?".
        """
        rows = parse_participant_event(self._left_payload())   # role='primary'
        seg = next(r for r in rows if r["table"] == "segments")
        assert seg.get("sla_target_ms") is None, (
            f"segmento sem espera saiu com alvo {seg.get('sla_target_ms')!r}"
        )

    def test_conference_id_propagated(self):
        rows = parse_participant_event(self._joined_payload(conference_id="conf-abc"))
        assert rows is not None
        assert rows[0]["conference_id"] == "conf-abc"
        assert rows[1]["conference_id"] == "conf-abc"

    def test_conference_id_absent_is_none(self):
        rows = parse_participant_event(self._joined_payload())
        assert rows is not None
        assert rows[0].get("conference_id") is None

    def test_unknown_type_returns_none(self):
        payload = self._joined_payload()
        payload["type"] = "participant_muted"
        assert parse_participant_event(payload) is None

    def test_missing_session_id_returns_none(self):
        payload = self._joined_payload()
        del payload["session_id"]
        assert parse_participant_event(payload) is None

    def test_missing_participant_id_returns_none(self):
        payload = self._joined_payload()
        del payload["participant_id"]
        assert parse_participant_event(payload) is None

    def test_event_id_generated_when_absent(self):
        payload = self._joined_payload()
        rows = parse_participant_event(payload)
        assert rows is not None
        # Both rows share the same event_id
        assert isinstance(rows[0]["event_id"], str)
        assert len(rows[0]["event_id"]) > 0
        assert rows[0]["event_id"] == rows[1]["event_id"]


# ── parse_evaluation_event ───────────────────────────────────────────────────

class TestParseEvaluationEvent:
    """Arc 6 — parse_evaluation_event returns [result_row, event_row]."""

    RESULT_ID  = "res-eval-001"
    INSTANCE_ID = "inst-eval-001"
    SESSION_ID  = "sess-eval-001"
    CAMPAIGN_ID = "camp-q1-2026"
    EVALUATOR   = "agente_avaliacao_v1-001"

    def _submitted_payload(self) -> dict:
        return {
            "event_type":   "evaluation.submitted",
            "tenant_id":    TENANT,
            "result_id":    self.RESULT_ID,
            "instance_id":  self.INSTANCE_ID,
            "session_id":   self.SESSION_ID,
            "campaign_id":  self.CAMPAIGN_ID,
            "evaluator_id": self.EVALUATOR,
            "form_id":      "form-sac-v1",
            "overall_score": 0.85,
            "compliance_flags": [],
            "timestamp":    "2026-04-01T10:00:00+00:00",
        }

    def test_returns_two_rows(self):
        rows = parse_evaluation_event(self._submitted_payload())
        assert rows is not None
        assert isinstance(rows, list)
        assert len(rows) == 2

    def test_result_row_table(self):
        rows = parse_evaluation_event(self._submitted_payload())
        assert rows[0]["table"] == "evaluation_results"

    def test_event_row_table(self):
        rows = parse_evaluation_event(self._submitted_payload())
        assert rows[1]["table"] == "evaluation_events"

    def test_result_row_fields(self):
        rows = parse_evaluation_event(self._submitted_payload())
        row = rows[0]
        assert row["result_id"]    == self.RESULT_ID
        assert row["tenant_id"]    == TENANT
        assert row["session_id"]   == self.SESSION_ID
        assert row["campaign_id"]  == self.CAMPAIGN_ID
        assert row["evaluator_id"] == self.EVALUATOR
        assert row["overall_score"] == pytest.approx(0.85)
        assert row["eval_status"]  == "submitted"
        assert row["locked"]       == 0

    def test_event_row_fields(self):
        rows = parse_evaluation_event(self._submitted_payload())
        row = rows[1]
        assert row["result_id"]   == self.RESULT_ID
        assert row["tenant_id"]   == TENANT
        assert row["event_type"]  == "evaluation.submitted"
        assert row["eval_status"] == "submitted"
        assert row["overall_score"] == pytest.approx(0.85)
        assert isinstance(row["event_id"], str) and len(row["event_id"]) > 0

    def test_event_row_actor_id_from_evaluator(self):
        rows = parse_evaluation_event(self._submitted_payload())
        assert rows[1]["actor_id"] == self.EVALUATOR

    def test_reviewed_event_actor_from_reviewed_by(self):
        payload = self._submitted_payload()
        payload["event_type"] = "evaluation.reviewed"
        payload["eval_status"] = "approved"
        payload["reviewed_by"] = "supervisor-001"
        rows = parse_evaluation_event(payload)
        assert rows is not None
        assert rows[1]["actor_id"] == "supervisor-001"
        assert rows[0]["eval_status"] == "approved"

    def test_contested_event_actor_from_contested_by(self):
        payload = self._submitted_payload()
        payload["event_type"] = "evaluation.contested"
        payload["contested_by"] = "operator-007"
        rows = parse_evaluation_event(payload)
        assert rows is not None
        assert rows[1]["actor_id"] == "operator-007"
        assert rows[0]["eval_status"] == "contested"

    def test_locked_event_sets_locked_flag(self):
        payload = self._submitted_payload()
        payload["event_type"] = "evaluation.locked"
        rows = parse_evaluation_event(payload)
        assert rows is not None
        assert rows[0]["locked"] == 1
        assert rows[0]["eval_status"] == "locked"

    def test_compliance_flags_propagated(self):
        payload = self._submitted_payload()
        payload["compliance_flags"] = ["gdpr_breach", "tone_violation"]
        rows = parse_evaluation_event(payload)
        assert rows is not None
        assert rows[0]["compliance_flags"] == ["gdpr_breach", "tone_violation"]

    def test_missing_result_id_returns_none(self):
        payload = self._submitted_payload()
        del payload["result_id"]
        assert parse_evaluation_event(payload) is None

    def test_missing_tenant_id_returns_none(self):
        payload = self._submitted_payload()
        del payload["tenant_id"]
        assert parse_evaluation_event(payload) is None

    def test_missing_event_type_returns_none(self):
        payload = self._submitted_payload()
        del payload["event_type"]
        assert parse_evaluation_event(payload) is None

    def test_none_overall_score_in_event_row(self):
        payload = self._submitted_payload()
        del payload["overall_score"]
        rows = parse_evaluation_event(payload)
        assert rows is not None
        # result_row defaults to 0.0; event_row stays None
        assert rows[0]["overall_score"] == pytest.approx(0.0)
        assert rows[1]["overall_score"] is None

    # ── F8 — per-dimension rows ───────────────────────────────────────────────
    def _completed_with_dimensions(self) -> dict:
        return {
            "event_type":   "evaluation.completed",
            "tenant_id":    TENANT,
            "evaluation_id": self.RESULT_ID,   # F2: completed usa evaluation_id
            "session_id":   self.SESSION_ID,
            "evaluator_id": self.EVALUATOR,
            "form_id":      "form-sac-v1",
            "composite_score": 8.0,
            "evaluated_at": "2026-04-01T10:00:00+00:00",
            "dimensions": [
                {"dimension_id": "empatia",      "name": "Empatia",      "score": 9.0, "weight": 0.5},
                {"dimension_id": "conformidade", "name": "Conformidade", "score": 7.0, "weight": 0.5},
            ],
        }

    def test_dimensions_emit_extra_rows(self):
        rows = parse_evaluation_event(self._completed_with_dimensions())
        assert rows is not None
        dim_rows = [r for r in rows if r["table"] == "evaluation_dimension_scores"]
        assert len(dim_rows) == 2

    def test_dimension_row_fields(self):
        rows = parse_evaluation_event(self._completed_with_dimensions())
        dim_rows = {r["dimension_id"]: r for r in rows if r["table"] == "evaluation_dimension_scores"}
        emp = dim_rows["empatia"]
        assert emp["result_id"]      == self.RESULT_ID
        assert emp["session_id"]     == self.SESSION_ID
        assert emp["form_id"]        == "form-sac-v1"
        assert emp["dimension_name"] == "Empatia"
        assert emp["score"]          == pytest.approx(9.0)
        assert emp["weight"]         == pytest.approx(0.5)

    def test_no_dimensions_no_extra_rows(self):
        rows = parse_evaluation_event(self._submitted_payload())
        assert all(r["table"] != "evaluation_dimension_scores" for r in rows)

    def test_dimension_without_id_skipped(self):
        payload = self._completed_with_dimensions()
        payload["dimensions"].append({"name": "Sem id", "score": 5.0})
        rows = parse_evaluation_event(payload)
        dim_rows = [r for r in rows if r["table"] == "evaluation_dimension_scores"]
        assert len(dim_rows) == 2  # a dimensão sem dimension_id é ignorada

    def test_dimension_threads_fallback_when_no_dimensions(self):
        # Caminho Arc 13 (demo): sem dimensions[], usa dimension_threads[].
        payload = {
            "event_type":    "evaluation.completed",
            "tenant_id":     TENANT,
            "evaluation_id": self.RESULT_ID,
            "session_id":    self.SESSION_ID,
            "evaluator_id":  self.EVALUATOR,
            "form_id":       "form-sac-v1",
            "composite_score": 8.0,
            "dimension_threads": [
                {"dimension_id": "empatia",      "score": 9.0,
                 "justification": "x" * 12, "evidence_entries": [{}]},
                {"dimension_id": "conformidade", "score": 7.0,
                 "justification": "y" * 12, "evidence_entries": [{}]},
            ],
        }
        rows = parse_evaluation_event(payload)
        dim_rows = {r["dimension_id"]: r for r in rows if r["table"] == "evaluation_dimension_scores"}
        assert set(dim_rows) == {"empatia", "conformidade"}
        assert dim_rows["empatia"]["score"] == pytest.approx(9.0)
        # sem name/weight nos threads → name cai pro dimension_id; weight 0 no row builder
        assert dim_rows["empatia"]["dimension_name"] == "empatia"

    def test_dimensions_preferred_over_threads(self):
        payload = self._completed_with_dimensions()
        payload["dimension_threads"] = [
            {"dimension_id": "outra", "score": 1.0,
             "justification": "z" * 12, "evidence_entries": [{}]},
        ]
        rows = parse_evaluation_event(payload)
        dim_ids = {r["dimension_id"] for r in rows if r["table"] == "evaluation_dimension_scores"}
        assert dim_ids == {"empatia", "conformidade"}  # dimensions[] vence; threads ignorado


# ── _write_row dispatch — evaluation tables ───────────────────────────────────

class TestWriteRowDispatchEvaluation:
    @pytest.mark.asyncio
    async def test_evaluation_results_dispatched(self):
        store = make_store()
        row = {"table": "evaluation_results", "result_id": "r1", "tenant_id": TENANT}
        await _write_row(store, row, "evaluation.events", 0)
        store.upsert_evaluation_result.assert_awaited_once_with(row)
        store.insert_evaluation_event.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_evaluation_events_dispatched(self):
        store = make_store()
        row = {"table": "evaluation_events", "event_id": "e1", "tenant_id": TENANT}
        await _write_row(store, row, "evaluation.events", 1)
        store.insert_evaluation_event.assert_awaited_once_with(row)
        store.upsert_evaluation_result.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_evaluation_dimension_scores_dispatched(self):
        store = make_store()
        row = {"table": "evaluation_dimension_scores", "result_id": "r1",
               "dimension_id": "empatia", "tenant_id": TENANT}
        await _write_row(store, row, "evaluation.events", 2)
        store.insert_evaluation_dimension_score.assert_awaited_once_with(row)
        store.upsert_evaluation_result.assert_not_awaited()


# ── parse_conversations_event — insight.* events ──────────────────────────────

class TestParseConversationsEventInsight:
    """parse_conversations_event dispatches insight.* event_types to contact_insights table."""

    def _payload(self, event_type="insight.registered", **extra):
        base = {
            "event_type": event_type,
            "session_id": SESSION,
            "tenant_id":  TENANT,
            "insight_id": "ins-001",
            "timestamp":  "2026-01-15T12:00:00+00:00",
            "category":   "cancelamento",
            "value":      "produto_x",
            "tags":       ["churn", "vip"],
            "agent_id":   "agente_sac_v1-001",
        }
        base.update(extra)
        return base

    def test_returns_contact_insights_row(self):
        rows = parse_conversations_event(self._payload())
        assert rows is not None and len(rows) == 1
        assert rows[0]["table"] == "contact_insights"

    def test_insight_id_preserved(self):
        rows = parse_conversations_event(self._payload())
        assert rows[0]["insight_id"] == "ins-001"

    def test_insight_type_equals_event_type(self):
        rows = parse_conversations_event(self._payload("insight.historico.cancelamento"))
        assert rows[0]["insight_type"] == "insight.historico.cancelamento"

    def test_category_and_value_mapped(self):
        rows = parse_conversations_event(self._payload())
        assert rows[0]["category"] == "cancelamento"
        assert rows[0]["value"] == "produto_x"

    def test_tags_propagated(self):
        rows = parse_conversations_event(self._payload())
        assert rows[0]["tags"] == ["churn", "vip"]

    def test_agent_id_from_agent_id_field(self):
        rows = parse_conversations_event(self._payload(agent_id="agente_sac_v1-001"))
        assert rows[0]["agent_id"] == "agente_sac_v1-001"

    def test_agent_id_from_instance_id_fallback(self):
        payload = self._payload()
        del payload["agent_id"]
        payload["instance_id"] = "agente_sac_v1-002"
        rows = parse_conversations_event(payload)
        assert rows[0]["agent_id"] == "agente_sac_v1-002"

    def test_category_from_nested_data(self):
        payload = {
            "event_type": "insight.registered",
            "session_id": SESSION,
            "tenant_id":  TENANT,
            "data": {"category": "retencao", "value": "oferta_aceita", "tags": ["retencao"]},
        }
        rows = parse_conversations_event(payload)
        assert rows[0]["category"] == "retencao"
        assert rows[0]["value"] == "oferta_aceita"

    def test_insight_id_generated_when_absent(self):
        payload = self._payload()
        del payload["insight_id"]
        rows = parse_conversations_event(payload)
        assert rows[0]["insight_id"]  # auto-generated UUID

    def test_unknown_event_type_returns_none(self):
        # Non-insight event types that aren't known are skipped
        rows = parse_conversations_event(self._payload("unknown.event.type"))
        assert rows is None

    def test_missing_session_id_returns_none(self):
        payload = self._payload()
        del payload["session_id"]
        assert parse_conversations_event(payload) is None

    def test_missing_tenant_id_returns_none(self):
        payload = self._payload()
        del payload["tenant_id"]
        assert parse_conversations_event(payload) is None


# ── _write_row dispatch — contact_insights ────────────────────────────────────

class TestWriteRowDispatchContactInsight:
    @pytest.mark.asyncio
    async def test_contact_insight_dispatched(self):
        store = make_store()
        store.insert_contact_insight = AsyncMock()
        row = {
            "table":        "contact_insights",
            "insight_id":   "ins-001",
            "tenant_id":    TENANT,
            "session_id":   SESSION,
            "insight_type": "insight.registered",
            "category":     "cancelamento",
            "value":        "produto_x",
            "tags":         ["churn"],
            "agent_id":     None,
            "timestamp":    "2026-01-15T12:00:00+00:00",
        }
        await _write_row(store, row, "conversations.events", 0)
        store.insert_contact_insight.assert_awaited_once_with(row)

    @pytest.mark.asyncio
    async def test_contact_insight_does_not_touch_other_stores(self):
        store = make_store()
        store.insert_contact_insight = AsyncMock()
        row = {"table": "contact_insights", "insight_id": "ins-002", "tenant_id": TENANT}
        await _write_row(store, row, "conversations.events", 0)
        store.upsert_session.assert_not_awaited()
        store.insert_evaluation_event.assert_not_awaited()


# ── parse_agent_lifecycle — Arc 8 pause/resume ────────────────────────────────

class TestParseAgentLifecyclePause:
    """Arc 8 — agent_pause and agent_ready events map to agent_pause_intervals."""

    INSTANCE = "agente_retencao_v1-001"
    TS       = "2026-05-01T09:00:00+00:00"

    def _pause_payload(self, **extra):
        base = {
            "event":         "agent_pause",
            "tenant_id":     TENANT,
            "instance_id":   self.INSTANCE,
            "agent_type_id": "agente_retencao_v1",
            "pool_id":       POOL,
            "reason_id":     "intervalo",
            "reason_label":  "Intervalo",
            "timestamp":     self.TS,
        }
        base.update(extra)
        return base

    def _ready_payload(self, **extra):
        base = {
            "event":         "agent_ready",
            "tenant_id":     TENANT,
            "instance_id":   self.INSTANCE,
            "agent_type_id": "agente_retencao_v1",
            "pools":         [POOL],
            "status":        "ready",
            "execution_model": "stateless",
            "max_concurrent_sessions": 5,
            "current_sessions": 0,
            "timestamp":     self.TS,
        }
        base.update(extra)
        return base

    # ── agent_pause ──────────────────────────────────────────────────────────

    def test_agent_pause_returns_open_row(self):
        row = parse_agent_lifecycle(self._pause_payload())
        assert row is not None
        assert row["table"] == "agent_pause_intervals"
        assert row["action"] == "open"

    def test_agent_pause_fields_propagated(self):
        row = parse_agent_lifecycle(self._pause_payload(note="Pausa para café"))
        assert row["tenant_id"]    == TENANT
        assert row["instance_id"]  == self.INSTANCE
        assert row["agent_type_id"] == "agente_retencao_v1"
        assert row["pool_id"]      == POOL
        assert row["reason_id"]    == "intervalo"
        assert row["reason_label"] == "Intervalo"
        assert row["note"]         == "Pausa para café"
        assert row["paused_at"]    == self.TS

    def test_agent_pause_generates_interval_id(self):
        row = parse_agent_lifecycle(self._pause_payload())
        assert row is not None
        assert "interval_id" in row
        assert len(row["interval_id"]) == 36  # UUID format

    def test_agent_pause_note_none_when_absent(self):
        row = parse_agent_lifecycle(self._pause_payload())
        assert row["note"] is None

    def test_agent_pause_missing_tenant_returns_none(self):
        payload = self._pause_payload()
        del payload["tenant_id"]
        assert parse_agent_lifecycle(payload) is None

    def test_agent_pause_missing_instance_returns_none(self):
        payload = self._pause_payload()
        del payload["instance_id"]
        assert parse_agent_lifecycle(payload) is None

    # ── agent_ready (close_check) ────────────────────────────────────────────

    def test_agent_ready_returns_close_check(self):
        row = parse_agent_lifecycle(self._ready_payload())
        assert row is not None
        assert row["table"]  == "agent_pause_intervals"
        assert row["action"] == "close_check"

    def test_agent_ready_carries_tenant_instance_resumed_at(self):
        row = parse_agent_lifecycle(self._ready_payload())
        assert row["tenant_id"]   == TENANT
        assert row["instance_id"] == self.INSTANCE
        assert row["resumed_at"]  == self.TS

    def test_agent_ready_missing_tenant_returns_none(self):
        payload = self._ready_payload()
        del payload["tenant_id"]
        assert parse_agent_lifecycle(payload) is None

    def test_agent_ready_missing_instance_returns_none(self):
        payload = self._ready_payload()
        del payload["instance_id"]
        assert parse_agent_lifecycle(payload) is None

    # ── agent_done ───────────────────────────────────────────────────────────
    # Era o guard de regressão do Arc 8 ("pause/ready não quebrou o agent_done").
    # A premissa deixou de existir em 2026-07-28: agent_done não gera mais linha
    # (a tabela agent_events saiu). O guard vira o inverso — pause/ready seguem
    # vivos (testes acima) e agent_done é inerte para o analytics.

    def test_agent_done_returns_none(self):
        assert parse_agent_lifecycle({
            "event":      "agent_done",
            "tenant_id":  TENANT,
            "instance_id": self.INSTANCE,
            "session_id": SESSION,
            "timestamp":  self.TS,
        }) is None

    # ── untracked events ─────────────────────────────────────────────────────

    def test_agent_login_returns_none(self):
        assert parse_agent_lifecycle({
            "event": "agent_login", "tenant_id": TENANT, "instance_id": self.INSTANCE,
        }) is None

    def test_agent_heartbeat_returns_none(self):
        assert parse_agent_lifecycle({
            "event": "agent_heartbeat", "tenant_id": TENANT, "instance_id": self.INSTANCE,
            "status": "ready",
        }) is None

    def test_agent_busy_returns_none(self):
        assert parse_agent_lifecycle({
            "event": "agent_busy", "tenant_id": TENANT, "instance_id": self.INSTANCE,
            "session_id": SESSION,
        }) is None


# ── _write_row dispatch — agent_pause_intervals ───────────────────────────────

class TestWriteRowDispatchPauseIntervals:
    @pytest.mark.asyncio
    async def test_agent_pause_intervals_dispatched(self):
        store = make_store()
        store.upsert_agent_pause_interval = AsyncMock()
        row = {
            "table":        "agent_pause_intervals",
            "action":       "close",
            "interval_id":  "00000000-0000-0000-0000-000000000001",
            "tenant_id":    TENANT,
            "instance_id":  "agente_retencao_v1-001",
            "agent_type_id": "agente_retencao_v1",
            "pool_id":      POOL,
            "reason_id":    "intervalo",
            "reason_label": "Intervalo",
            "note":         None,
            "paused_at":    "2026-05-01T09:00:00+00:00",
            "resumed_at":   "2026-05-01T09:30:00+00:00",
            "duration_ms":  1800000,
        }
        await _write_row(store, row, "agent.lifecycle", 0)
        store.upsert_agent_pause_interval.assert_awaited_once_with(row)

    @pytest.mark.asyncio
    async def test_agent_pause_intervals_does_not_touch_other_stores(self):
        store = make_store()
        store.upsert_agent_pause_interval = AsyncMock()
        row = {"table": "agent_pause_intervals", "interval_id": "x", "tenant_id": TENANT}
        await _write_row(store, row, "agent.lifecycle", 0)
        store.upsert_session.assert_not_awaited()
        store.insert_evaluation_event.assert_not_awaited()


# ── F10: session.signals → session_signal (parse_session_signal_event) ─────────

class TestSessionSignalNormalization:
    def test_nps_promoter(self):
        assert _normalize_signal_value("nps", 10.0) == (10.0, "promotor")
        assert _normalize_signal_value("nps", 9.0) == (9.0, "promotor")

    def test_nps_neutral(self):
        assert _normalize_signal_value("nps", 8.0) == (8.0, "neutro")
        assert _normalize_signal_value("nps", 7.0) == (7.0, "neutro")

    def test_nps_detractor(self):
        assert _normalize_signal_value("nps", 6.0) == (6.0, "detrator")
        assert _normalize_signal_value("nps", 0.0) == (0.0, "detrator")

    def test_csat_buckets(self):
        assert _normalize_signal_value("csat", 5.0) == (5.0, "satisfeito")
        assert _normalize_signal_value("csat", 3.0) == (3.0, "neutro")
        assert _normalize_signal_value("csat", 1.0) == (1.0, "insatisfeito")

    def test_unknown_metric_no_label(self):
        assert _normalize_signal_value("valor_contrato", 299.0) == (299.0, None)

    # ── S1: CES / PMF / FCR (antes caíam crus, sem rótulo) ────────────────────

    def test_ces_buckets_high_score_is_good(self):
        # Spec: nota ALTA = bom (baixo esforço). O catálogo do relatório dizia o
        # contrário; este teste trava a semântica.
        assert _normalize_signal_value("ces", 7.0) == (7.0, "low_effort")
        assert _normalize_signal_value("ces", 5.0) == (5.0, "low_effort")
        assert _normalize_signal_value("ces", 4.0) == (4.0, "neutral")
        assert _normalize_signal_value("ces", 1.0) == (1.0, "high_effort")

    def test_pmf_buckets_inverted_scale(self):
        # 1 = "very_disappointed" = MELHOR sinal de product-market fit.
        assert _normalize_signal_value("pmf", 1.0) == (1.0, "very_disappointed")
        assert _normalize_signal_value("pmf", 2.0) == (2.0, "somewhat_disappointed")
        assert _normalize_signal_value("pmf", 3.0) == (3.0, "not_disappointed")

    def test_fcr_binary(self):
        assert _normalize_signal_value("fcr", 1.0) == (1.0, "resolved")
        assert _normalize_signal_value("fcr", 0.0) == (0.0, "unresolved")

    def test_scale_stamp_shifts_the_band_but_not_the_value(self):
        # CSAT respondido numa escala 1–10 (a spec admite): o valor GRAVADO continua
        # sendo o cru (a resposta do cliente não é reescrita); só a BANDA usa a
        # re-escala linear para a escala 1–5 do catálogo.
        value, label = _normalize_signal_value("csat", 8.0, {"min": 1, "max": 10})
        assert value == 8.0
        assert label == "satisfeito"          # 8/10 → 4.1 em 1–5
        # Ponto médio de 1–10 (5,5) → 3,0 em 1–5 → neutro.
        assert _normalize_signal_value("csat", 5.5, {"min": 1, "max": 10})[1] == "neutro"
        # 5/10 fica ABAIXO do meio → 2,8 em 1–5 → insatisfeito. Sem a re-escala,
        # o mesmo 5 seria lido como topo da escala 1–5 ("satisfeito") — a inversão
        # de leitura que a `scale` carimbada existe para evitar.
        assert _normalize_signal_value("csat", 5.0, {"min": 1, "max": 10})[1] == "insatisfeito"

    def test_scale_stamp_ignored_when_equal_to_catalog(self):
        assert _normalize_signal_value("csat", 4.0, {"min": 1, "max": 5})[1] == "satisfeito"

    def test_malformed_scale_falls_back_to_catalog(self):
        # Degradação nunca silenciosa no sentido de dado: escala inválida não
        # derruba o parse nem inventa banda — usa a do catálogo.
        assert _normalize_signal_value("nps", 10.0, {"min": "x", "max": None})[1] == "promotor"


class TestSurveyCatalogSources:
    def test_five_instruments_have_distinct_sources(self):
        from ..models import _signal_source_for_metric as src
        assert src("nps")  == "customer_nps"
        assert src("csat") == "customer_csat"
        assert src("ces")  == "customer_ces"
        assert src("pmf")  == "customer_pmf"
        assert src("fcr")  == "customer_survey"   # FCR percebido (spec §4)
        assert src("valor_contrato") == "customer_survey"

    def test_report_catalog_is_derived_from_the_same_source(self):
        # A divergência que motivou o catálogo único: CES estava `higher_is_better:
        # False` no relatório contra "nota alta = bom" na spec.
        from ..reports_query import CV_INSTRUMENTS
        from ..survey_catalog import SURVEY_INSTRUMENTS
        for metric, inst in SURVEY_INSTRUMENTS.items():
            assert CV_INSTRUMENTS[metric]["rollup"] == inst["rollup"]
            assert CV_INSTRUMENTS[metric]["higher_is_better"] == inst["higher_is_better"]
        assert CV_INSTRUMENTS["ces"]["higher_is_better"] is True
        assert CV_INSTRUMENTS["pmf"]["rollup"] == "pct"
        assert CV_INSTRUMENTS["fcr"]["rollup"] == "pct"
        assert CV_INSTRUMENTS["sla"]["source"] == "operational"   # não é survey


class TestParseSessionSignalEvent:
    def _payload(self, signals, **extra):
        p = {
            "event_id":          "evt-survey-001",
            "tenant_id":         TENANT,
            "origin_session_id": "sess-original-007",
            "grain":             "session",
            "survey_session_id": "sess-survey-999",
            "pool_id":           POOL,
            "captured_at":       "2026-06-10T15:00:00+00:00",
            "signals":           signals,
        }
        p.update(extra)
        return p

    def test_nps_contact_keys_to_origin(self):
        rows = parse_session_signal_event(
            self._payload([{"metric": "nps", "value": 9}])
        )
        assert isinstance(rows, list)
        assert len(rows) == 1
        sig = rows[0]
        assert sig["table"] == "session_signal"
        assert sig["grain"] == "session"
        assert sig["source"] == "customer_nps"
        assert sig["metric"] == "nps"
        assert sig["value_num"] == 9.0
        assert sig["value_label"] == "promotor"
        assert sig["agent_key"] == ""                       # não atribuível
        assert sig["session_id"] == "sess-original-007"     # chaveado ao original
        assert sig["origin_session_id"] == "sess-original-007"
        assert sig["session_at"] == sig["captured_at"] == "2026-06-10T15:00:00+00:00"

    def test_value_coerces_from_string(self):
        # menu output_as devolve string "3" — o parser coage.
        rows = parse_session_signal_event(
            self._payload([{"metric": "nps", "value": "3"}])
        )
        assert rows[0]["value_num"] == 3.0
        assert rows[0]["value_label"] == "detrator"

    def test_multiple_metrics_one_row_each(self):
        # Pesquisa pode agregar várias métricas numa só chamada.
        # S1: `ces` DEIXOU de ser "métrica extra sem semântica" — é instrumento
        # catalogado (source customer_ces + banda de esforço). O fallback sem label
        # continua valendo, mas só para métrica realmente fora do catálogo.
        rows = parse_session_signal_event(
            self._payload([
                {"metric": "nps",  "value": 8},
                {"metric": "csat", "value": 5},
                {"metric": "ces",  "value": 2},
                {"metric": "valor_contrato", "value": 299},   # métrica extra do tenant
            ])
        )
        assert len(rows) == 4
        by_metric = {r["metric"]: r for r in rows}
        assert by_metric["nps"]["value_label"] == "neutro"
        assert by_metric["csat"]["source"] == "customer_csat"
        assert by_metric["csat"]["value_label"] == "satisfeito"
        assert by_metric["ces"]["source"] == "customer_ces"
        assert by_metric["ces"]["value_label"] == "high_effort"   # 2 em 1–7 = esforço alto
        assert by_metric["valor_contrato"]["source"] == "customer_survey"
        assert by_metric["valor_contrato"]["value_label"] is None
        # signal_id único por métrica
        assert len({r["signal_id"] for r in rows}) == 4

    def test_workflow_grain(self):
        rows = parse_session_signal_event(
            self._payload([{"metric": "csat", "value": 1}], grain="workflow")
        )
        assert rows[0]["grain"] == "workflow"
        assert rows[0]["value_label"] == "insatisfeito"

    def test_journey_grain(self):
        rows = parse_session_signal_event(
            self._payload([{"metric": "nps", "value": 10}], grain="journey")
        )
        assert rows[0]["grain"] == "journey"
        assert rows[0]["value_label"] == "promotor"

    def test_segment_grain_with_attribution(self):
        # grão segment é aceito com segment_id + agent_key (atribuição ao agente).
        rows = parse_session_signal_event(
            self._payload(
                [{"metric": "nps", "value": 9}],
                grain="segment",
                segment_id="seg-abc",
                agent_key="user_42",
            )
        )
        sig = rows[0]
        assert sig["grain"] == "segment"
        assert sig["segment_id"] == "seg-abc"
        assert sig["agent_key"] == "user_42"
        assert sig["session_id"] == "sess-original-007"

    def test_segment_grain_without_segment_id_rejected(self):
        # grão segment exige segment_id (atribuição + dedup-safe).
        assert parse_session_signal_event(
            self._payload([{"metric": "nps", "value": 9}], grain="segment")
        ) is None

    def test_explicit_label_wins(self):
        rows = parse_session_signal_event(
            self._payload([{"metric": "nps", "value": 6, "value_label": "custom"}])
        )
        assert rows[0]["value_label"] == "custom"

    def test_missing_origin_returns_none(self):
        p = self._payload([{"metric": "nps", "value": 9}])
        del p["origin_session_id"]
        assert parse_session_signal_event(p) is None

    def test_bad_grain_returns_none(self):
        assert parse_session_signal_event(
            self._payload([{"metric": "nps", "value": 9}], grain="bogus")
        ) is None

    def test_empty_signals_returns_none(self):
        assert parse_session_signal_event(self._payload([])) is None

    @pytest.mark.asyncio
    async def test_dispatch_routes_to_session_signal(self):
        store = make_store()
        rows = parse_session_signal_event(
            self._payload([{"metric": "nps", "value": 10}])
        )
        for row in rows:
            await _write_row(store, row, "session.signals", 0)
        store.insert_session_signal.assert_awaited_once()


# ── F11: session_at enrichment for deferred surveys ───────────────────────────

class TestSignalSessionAtEnrichment:
    """_enrich_signal_session_at: bucketize by the ORIGINAL session's opened_at."""

    def _rows(self):
        return parse_session_signal_event({
            "event_id":          "evt-survey-defer-1",
            "tenant_id":         TENANT,
            "origin_session_id": "sess-original-007",
            "grain":             "session",
            "captured_at":       "2026-06-20T15:00:00+00:00",   # 10 dias depois
            "signals":           [{"metric": "nps", "value": 9}],
        })

    def setup_method(self):
        _session_opened_cache.clear()

    @pytest.mark.asyncio
    async def test_deferred_overwrites_session_at_with_origin_opened_at(self):
        store = make_store()
        store.lookup_session_opened_at = AsyncMock(
            return_value="2026-06-10T09:30:00+00:00"   # data da sessão original
        )
        rows = self._rows()
        await _enrich_signal_session_at(rows, store)
        # session_at passa a ser o opened_at da origem; captured_at intacto.
        assert rows[0]["session_at"]  == "2026-06-10T09:30:00+00:00"
        assert rows[0]["captured_at"] == "2026-06-20T15:00:00+00:00"
        store.lookup_session_opened_at.assert_awaited_once_with(TENANT, "sess-original-007")

    @pytest.mark.asyncio
    async def test_origin_not_found_keeps_captured_at(self):
        store = make_store()  # lookup returns None by default
        rows = self._rows()
        await _enrich_signal_session_at(rows, store)
        # sem origem resolvível → fallback seguro: session_at = captured_at.
        assert rows[0]["session_at"] == "2026-06-20T15:00:00+00:00"

    @pytest.mark.asyncio
    async def test_lookup_error_keeps_captured_at(self):
        store = make_store()
        store.lookup_session_opened_at = AsyncMock(side_effect=RuntimeError("ch down"))
        rows = self._rows()
        await _enrich_signal_session_at(rows, store)
        assert rows[0]["session_at"] == "2026-06-20T15:00:00+00:00"

    @pytest.mark.asyncio
    async def test_cache_avoids_repeat_lookup(self):
        store = make_store()
        store.lookup_session_opened_at = AsyncMock(
            return_value="2026-06-10T09:30:00+00:00"
        )
        await _enrich_signal_session_at(self._rows(), store)
        await _enrich_signal_session_at(self._rows(), store)
        # segunda chamada serve do cache → lookup só uma vez.
        store.lookup_session_opened_at.assert_awaited_once()


# ── Quality substrate isolation (ADR) — origin derivation + stamping ───────────

class TestOriginFromSource:
    def test_external_import_maps_to_import(self):
        assert origin_from_source("external_import") == "import"

    def test_internal_reeval_maps_to_reeval(self):
        assert origin_from_source("internal:reeval") == "reeval"

    @pytest.mark.parametrize("src", [
        "channel_gateway", "bridge:conference", "routing_engine",
        "webhook_trigger", "", None, 123, "anything_else",
    ])
    def test_live_default(self, src):
        # Qualquer source de produção (ou ausente/inválido) → live (forward-compatible).
        assert origin_from_source(src) == "live"


class TestParsersStampOrigin:
    def test_inbound_stamps_origin(self):
        # tráfego vivo (sem source) → live
        row = parse_inbound({"session_id": SESSION, "tenant_id": TENANT, "channel": "webchat"})
        assert row["origin"] == "live"

    def test_inbound_import_source(self):
        row = parse_inbound({
            "session_id": SESSION, "tenant_id": TENANT,
            "channel": "webchat", "source": "external_import",
        })
        assert row["origin"] == "import"

    def test_contact_open_reeval_source(self):
        rows = parse_conversations_event({
            "event_type": "contact_open", "session_id": SESSION,
            "tenant_id": TENANT, "source": "internal:reeval",
        })
        assert rows[0]["table"] == "sessions"
        assert rows[0]["origin"] == "reeval"

    def test_message_sent_stamps_origin(self):
        rows = parse_conversations_event({
            "event_type": "message_sent", "session_id": SESSION,
            "tenant_id": TENANT, "content": "oi", "source": "external_import",
        })
        assert rows[0]["table"] == "messages"
        assert rows[0]["origin"] == "import"

    def test_segment_stamps_origin(self):
        rows = parse_participant_event({
            "type": "participant_joined", "session_id": SESSION,
            "tenant_id": TENANT, "participant_id": "p1",
            "source": "external_import",
        })
        seg = next(r for r in rows if r["table"] == "segments")
        assert seg["origin"] == "import"

    def test_segment_live_default(self):
        rows = parse_participant_event({
            "type": "participant_joined", "session_id": SESSION,
            "tenant_id": TENANT, "participant_id": "p1",
        })
        seg = next(r for r in rows if r["table"] == "segments")
        assert seg["origin"] == "live"


# ── F1b: `entrou por` — first-write-wins em sessions.pool_id ──────────────────
#
# As linhas são construídas pelos PARSERS reais, não à mão: o que se quer provar é
# que o carimbo sobrevive ao formato de payload que os produtores mandam de fato.
# Cada asserção positiva tem a sua negativa ao lado — um teste que só afirma que o
# pool de entrada aparece passaria também numa implementação que congelasse tudo.

_T0 = "2026-08-14T10:00:00Z"   # inbound  — a entrada
_T1 = "2026-08-14T10:05:00Z"   # routed   — depois
_T2 = "2026-08-14T10:30:00Z"   # closed   — por último


def _pump(*rows: dict) -> None:
    """Um batch do consumer: aprender antes de injetar (ordem do call site)."""
    batch = list(rows)
    _learn_session_identity(batch)
    _inject_session_identity(batch)


def _inbound(pool: str, ts: str = _T0) -> dict:
    return parse_inbound({
        "session_id": SESSION, "tenant_id": TENANT, "channel": "webchat",
        "pool_id": pool, "started_at": ts,
    })


def _routed(pool: str, ts: str = _T1) -> dict:
    return parse_routed({
        "session_id": SESSION, "tenant_id": TENANT, "routed_at": ts,
        "result": {"pool_id": pool},
    })[0]


def _closed(pool: str, ts: str = _T2) -> dict:
    return parse_conversations_event({
        "event_type": "contact_closed", "session_id": SESSION, "tenant_id": TENANT,
        "channel": "webchat", "pool_id": pool, "started_at": _T0, "ended_at": ts,
    })[0]


class TestEntryPoolFirstWriteWins:
    def setup_method(self):
        _session_identity_cache.clear()

    def test_routed_no_longer_overwrites_the_entry_pool(self):
        # O caso medido: sac_ia → retencao_humano (14 sessões em tenant_demo).
        inbound, routed = _inbound("sac_ia"), _routed("retencao_humano")
        _pump(inbound)
        _pump(routed)
        assert routed["pool_id"] == "sac_ia"

    def test_close_no_longer_overwrites_the_entry_pool(self):
        # A linha de close é a SOBREVIVENTE no ReplacingMergeTree — se ela não
        # carregar a entrada, nada mais carrega.
        inbound, closed = _inbound("limite_processo"), _closed("aprovacao_credito")
        _pump(inbound)
        _pump(closed)
        assert closed["pool_id"] == "limite_processo"

    def test_order_of_arrival_does_not_decide(self):
        # inbound/routed são TÓPICOS diferentes: ordem entre eles não é garantida.
        # Chegando ao contrário, a linha escrita por último (a que o RMT conserva)
        # tem de trazer a entrada do mesmo jeito.
        routed, inbound = _routed("retencao_humano"), _inbound("sac_ia")
        _pump(routed)
        _pump(inbound)
        assert inbound["pool_id"] == "sac_ia"
        # e um evento posterior continua recebendo a entrada, não o que veio 1º
        closed = _closed("retencao_humano")
        _pump(closed)
        assert closed["pool_id"] == "sac_ia"

    def test_same_batch_min_timestamp_wins(self):
        inbound, routed = _inbound("sac_ia"), _routed("retencao_humano")
        _pump(routed, inbound)          # ordem invertida DENTRO do batch
        assert routed["pool_id"] == "sac_ia"
        assert inbound["pool_id"] == "sac_ia"

    def test_contact_open_empty_pool_does_not_freeze_the_session(self):
        # `contact_open` escreve pool_id="" literal (models.py:319). Um "primeiro
        # valor" que aceitasse vazio deixaria a sessão sem pool para SEMPRE.
        opened = parse_conversations_event({
            "event_type": "contact_open", "session_id": SESSION,
            "tenant_id": TENANT, "channel": "webchat", "started_at": _T0,
        })[0]
        assert opened["pool_id"] == ""          # o produtor segue como era
        _pump(opened)
        routed = _routed("retencao_humano")
        _pump(routed)
        assert routed["pool_id"] == "retencao_humano"

    def test_row_without_pool_still_gets_it_injected(self):
        # A reidratação de linha parcial (o motivo original da cache) não regride.
        _pump(_inbound("sac_ia"))
        suspended = parse_conversations_event({
            "event_type": "session_suspended", "session_id": SESSION,
            "tenant_id": TENANT, "timestamp": _T1, "opened_at": _T0,
        })[0]
        _pump(suspended)
        assert suspended["pool_id"] == "sac_ia"

    def test_no_cache_entry_keeps_the_rows_own_pool(self):
        # Cache fria (restart do consumer): a linha mantém o que trouxe. É a
        # degradação DECLARADA — nunca um vazio inventado.
        routed = _routed("retencao_humano")
        _pump(routed)
        assert routed["pool_id"] == "retencao_humano"

    def test_two_sessions_do_not_contaminate_each_other(self):
        _pump(_inbound("sac_ia"))
        other = parse_inbound({
            "session_id": "outra-sessao", "tenant_id": TENANT,
            "channel": "webchat", "pool_id": "limite_processo", "started_at": _T0,
        })
        _pump(other)
        assert other["pool_id"] == "limite_processo"

    def test_channel_still_follows_last_non_empty(self):
        # CONTROLE: prova que a regra nova é do pool e não vazou para a tupla
        # genérica. Se este teste virar "webchat", `_IDENTITY_FIELDS` foi trocada
        # por first-write-wins junto — e aí o teste de cima passaria por engano.
        _pump(_inbound("sac_ia"))
        later = parse_conversations_event({
            "event_type": "contact_closed", "session_id": SESSION,
            "tenant_id": TENANT, "channel": "voice", "pool_id": "retencao_humano",
            "started_at": _T0, "ended_at": _T2,
        })[0]
        _pump(later)
        assert later["channel"] == "voice"
        assert later["pool_id"] == "sac_ia"
