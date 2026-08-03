"""
models.py
Event parsers for each Kafka topic consumed by the Analytics API.

Each parser returns a normalised dict ready for ClickHouse insertion.
Returns None for events that should be skipped (unknown type, wrong format).

Topics consumed:
  conversations.inbound      → sessions (initial record)
  conversations.routed       → sessions (pool_id update) + agent_events (routing)
  conversations.queued       → sessions (queued) + queue_events
  conversations.events       → sessions (contact_open / contact_closed) + messages
  agent.lifecycle            → agent_events (agent_done)
  usage.events               → usage_events (passthrough)
  sentiment.updated          → sentiment_events (+ segment_id enrichment via SegmentEnricher)
  queue.position_updated     → queue_events (position update)
  workflow.events            → workflow_events (lifecycle)
  collect.events             → collect_events (lifecycle)
  conversations.participants → participation_intervals (participant joined / left)
  evaluation.events          → evaluation_results + evaluation_events (Arc 6)
  mcp.audit                  → session_timeline (+ segment_id enrichment via SegmentEnricher)
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from . import survey_catalog


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _gen_id() -> str:
    return str(uuid.uuid4())


# ─── Quality substrate isolation (ADR adr-quality-substrate-isolation) ─────────

def origin_from_source(source: Any) -> str:
    """Deriva a procedência por-sessão (`origin`) a partir do `source` que os
    eventos canônicos já carregam. Eixo de isolamento do substrato de avaliação:

      external_import (quality-ingest)  → import
      internal:reeval (quality-export)  → reeval
      qualquer outro / vazio            → live   (channel_gateway, bridge,
                                                   routing_engine = tráfego vivo)

    Default `live` torna a derivação forward-compatible: eventos sem `source`
    e todo o tráfego de produção caem em `live` sem backfill.
    """
    s = source.strip() if isinstance(source, str) else ""
    if s == "external_import":
        return "import"
    if s == "internal:reeval":
        return "reeval"
    return "live"


# ─── conversations.inbound ────────────────────────────────────────────────────

def parse_inbound(payload: dict[str, Any]) -> dict | None:
    """
    Creates an initial sessions row when the contact first arrives.
    The row will be replaced (ReplacingMergeTree) when contact_closed fires.
    """
    session_id = payload.get("session_id")
    tenant_id  = payload.get("tenant_id")
    if not session_id or not tenant_id:
        return None

    # Conference specialist invites (conference_id present) are NOT new contacts —
    # they are a participant (delegate target / @mention specialist) joining an
    # existing session. Skip: creating a sessions row here would reset the parent's
    # status to "active" and churn its channel. The participation shows up as a
    # segment via the participant_joined/left events instead.
    if payload.get("conference_id"):
        return None

    # ANI/DNIS — source/destination identifiers, channel-agnostic:
    #   voice:     ANI = caller number, DNIS = dialed number
    #   whatsapp:  ANI = sender number, DNIS = business number
    #   email:     ANI = from address, DNIS = to address
    meta = payload.get("metadata") or payload.get("channel_metadata") or {}
    ani  = (payload.get("ani") or payload.get("caller_id") or payload.get("from")
            or meta.get("ani") or meta.get("caller_id") or meta.get("from") or None)
    dnis = (payload.get("dnis") or payload.get("dialed_number") or payload.get("to")
            or meta.get("dnis") or meta.get("dialed_number") or meta.get("to") or None)

    event_type = payload.get("event_type") or payload.get("type")

    return {
        "table":        "sessions",
        "session_id":   session_id,
        "tenant_id":    tenant_id,
        "channel":      payload.get("channel", ""),
        "pool_id":      payload.get("pool_id") or "",
        "customer_id":  payload.get("customer_id") or payload.get("contact_id"),
        "opened_at":    payload.get("started_at") or _now(),
        "closed_at":    None,
        "close_reason": None,
        "outcome":      None,
        "wait_time_ms":   None,
        "handle_time_ms": None,
        "timestamp":    payload.get("started_at") or _now(),
        "ani":          ani,
        "dnis":         dnis,
        # Arc 19: mark re-opened (resumed) sessions as active; new sessions also start active.
        "status":       "active",
        # Arc 19: webhook workflow sessions carry origin_session_id linking back to the
        # intake agent session that triggered them via workflow_trigger MCP tool.
        "origin_session_id": payload.get("origin_session_id") or None,
        # Journey T4: rótulo da aresta — POR QUE esta sessão existe (trigger|delegate|
        # collect). NULL numa sessão de topo (iniciada pelo cliente).
        "spawn_reason": payload.get("spawn_reason") or None,
        # Journey J1: root_session_id = raiz TRANSITIVA da proveniência (propagada do
        # chamador pela plataforma; ausente → self). journey_id = cache = root no
        # nascimento. Ambos nunca null (fallback session_id).
        "root_session_id": payload.get("root_session_id") or session_id,
        "journey_id":      payload.get("root_session_id") or session_id,
        # Substrate isolation: procedência derivada do source (live|import|reeval).
        "origin":       origin_from_source(payload.get("source")),
    }


# ─── conversations.routed ─────────────────────────────────────────────────────

def parse_routed(payload: dict[str, Any]) -> list[dict] | None:
    """
    Returns 0 or 1 row:
      - sessions upsert (com pool_id do resultado de roteamento)

    Lista VAZIA em roteamento de conferência/hook (`conference_id` presente): é
    nível-segmento e não pode reescrever a linha de contato. O segmento em si vem
    de `conversations.participants`, não daqui.

    Antes devolvia também uma linha `agent_events`; a tabela foi descontinuada em
    2026-07-28 (duplicava `segments`).
    """
    session_id = payload.get("session_id")
    tenant_id  = payload.get("tenant_id")
    if not session_id or not tenant_id:
        return None

    result     = payload.get("result") or {}
    pool_id    = result.get("pool_id") or ""
    instance_id = result.get("instance_id") or ""
    routed_at  = payload.get("routed_at") or _now()
    sla_target_ms = result.get("sla_target_ms")  # None when pool has no SLA or not yet propagated
    conference_id = result.get("conference_id")

    rows: list[dict] = []

    # Conference/hook routings (wrap-up, NPS, invited specialists) carry a
    # conference_id — they are SEGMENT-level facts, tracked via
    # conversations.participants → segments. They must NOT write the
    # contact-level `sessions` row: doing so (a) overwrites the contact's real
    # pool_id with the specialist pool (e.g. wrapup_ia), and (b) — critically —
    # inserts a row with closed_at=NULL that, when it arrives AFTER
    # contact_closed in the no-version ReplacingMergeTree (last-inserted-wins),
    # RE-OPENS an already-closed session. This happens whenever a hook agent
    # routes after the contact layer was closed (e.g. customer-disconnect wrap-up
    # dispatched right after _close_contact_layer). Only the primary allocation
    # (no conference_id) owns the sessions row.
    if not conference_id:
        rows.append(
            # sessions — update with pool_id and sla_target_ms.
            # NOTE: channel="" here because ConversationRoutedEvent (Zod schema) does not carry
            # channel — the Routing Engine only knows pool/agent. Writing channel="" will REPLACE
            # the parse_inbound row's channel="webchat" in ReplacingMergeTree (last-write-wins,
            # no partial merge). The query layer in reports_query.py compensates by using a
            # COALESCE subquery to recover the effective channel from any historical row,
            # and by allowing closed_at IS NULL rows through the channel filter.
            # Do NOT change channel="" to channel=payload.get("channel","") here without also
            # updating ConversationRoutedEventSchema to carry the channel field.
            {
                "table":         "sessions",
                "session_id":    session_id,
                "tenant_id":     tenant_id,
                "channel":       "",
                "pool_id":       pool_id,
                "opened_at":     routed_at,
                "timestamp":     routed_at,
                "sla_target_ms": sla_target_ms,
                "origin":        origin_from_source(payload.get("source")),
            }
        )

    # (removido 2026-07-28) A linha `agent_events` com event_type='routed'.
    #
    # `agent_events` era substrato DERIVADO que duplicava `segments`: cada
    # roteamento já vira um `participant_joined` → uma linha de segmento com
    # `started_at`, e o fim vira `ended_at`/`outcome` na MESMA linha. A tabela
    # antiga guardava dois eventos que nenhuma query juntava, com menos campos
    # (sem role, channel, close_reason, sequence_index, conference_id).
    #
    # Ela não pertencia a nenhum eixo: não é marcação semântica (essa tem porta
    # única, a tool `agent_event` do Arc 12 → `agent_business_events`) nem
    # substrato legítimo (esse é `segments`). Ver CHANGELOG 2026-07-28.
    #
    # Único campo exclusivo perdido: `routing_mode` — era write-only, lido apenas
    # pelo endpoint `/reports/agents`, que não tinha chamadores.
    return rows


# ─── conversations.queued ─────────────────────────────────────────────────────

def parse_queued(payload: dict[str, Any]) -> list[dict] | None:
    """
    Returns:
      - sessions upsert (pool_id from routing result)
      - queue_events (queued)
    """
    session_id = payload.get("session_id")
    tenant_id  = payload.get("tenant_id")
    if not session_id or not tenant_id:
        return None

    result   = payload.get("result") or {}
    pool_id  = result.get("pool_id") or ""
    queued_at = payload.get("routed_at") or _now()
    conference_id = result.get("conference_id")

    rows: list[dict] = []

    # Same rationale as parse_routed: a conference/hook agent that gets queued is
    # a segment-level fact and must not touch the contact-level sessions row
    # (would clobber pool_id and can re-open a closed session).
    if not conference_id:
        rows.append(
            # sessions — update with pool_id (queued). Same channel="" caveat as parse_routed.
            {
                "table":      "sessions",
                "session_id": session_id,
                "tenant_id":  tenant_id,
                "channel":    "",
                "pool_id":    pool_id,
                "opened_at":  queued_at,
                "timestamp":  queued_at,
                "origin":     origin_from_source(payload.get("source")),
            }
        )

    rows.append(
        {
            "table":           "queue_events",
            "event_id":        _gen_id(),
            "tenant_id":       tenant_id,
            "session_id":      session_id,
            "pool_id":         pool_id,
            "event_type":      "queued",
            "queue_position":  None,
            "estimated_wait_ms": None,
            # F5 (2026-08-02): `available_agents` MORREU no produtor e não é mais lido
            # por relatório nenhum. Segue como `None` aqui só porque a coluna existe em
            # `queue_events` (Nullable, com dados históricos) e dropá-la é migração à
            # parte. NÃO voltar a preenchê-la: o valor era `SCARD(pool:instances)` —
            # pertencimento, não capacidade — e ambíguo por construção.
            "available_agents":  None,
            "timestamp":       queued_at,
        }
    )
    return rows


# ─── conversations.events ─────────────────────────────────────────────────────

def parse_conversations_event(payload: dict[str, Any]) -> list[dict] | None:
    """
    Handles the multi-type conversations.events topic.
    Recognised event_type values:
      contact_open       → sessions upsert
      contact_closed     → sessions upsert (with closed_at, close_reason, outcome)
      message_sent       → messages insert
      session_suspended  → sessions upsert (Arc 19: status = 'suspended')
    All others are silently skipped.
    """
    event_type = payload.get("event_type") or payload.get("type")
    session_id = payload.get("session_id")
    tenant_id  = payload.get("tenant_id")

    if not session_id or not tenant_id:
        return None

    if event_type == "contact_open":
        return [
            {
                "table":       "sessions",
                "session_id":  session_id,
                "tenant_id":   tenant_id,
                "channel":     payload.get("channel", ""),
                "pool_id":     "",
                "customer_id": payload.get("customer_id") or payload.get("contact_id"),
                "opened_at":   payload.get("started_at") or payload.get("timestamp") or _now(),
                "timestamp":   payload.get("started_at") or payload.get("timestamp") or _now(),
                # Journey J1: root propagado (ausente → self).
                "root_session_id": payload.get("root_session_id") or session_id,
                "journey_id":      payload.get("root_session_id") or session_id,
                "origin":      origin_from_source(payload.get("source")),
            }
        ]

    if event_type == "contact_closed":
        # Fase A (queue-attended-model): the BRIDGE is the sole sessions-close
        # writer. The gateway's contact_closed is a transport signal (consumed
        # by the bridge to detect disconnects) — using it to upsert sessions
        # races the bridge's enriched event (close_reason + outcome) in
        # ReplacingMergeTree and overwrites it with NULLs (the webchat UI tears
        # down the WS right after platform close, so the gateway event is often
        # consumed LAST). The bridge fires _close_contact_layer for every
        # customer_side close — including sessions with no agents — so no
        # coverage is lost by skipping gateway-sourced events entirely.
        if payload.get("source") == "channel_gateway":
            return None
        started_at = payload.get("started_at")
        ended_at   = payload.get("ended_at") or _now()
        # Compute handle_time_ms from timestamps when available
        handle_time_ms: int | None = None
        if started_at and ended_at:
            try:
                from datetime import datetime as _dt, timezone as _tz
                def _parse(s: str) -> _dt:
                    return _dt.fromisoformat(s.replace("Z", "+00:00")).astimezone(_tz.utc)
                handle_time_ms = int((_parse(ended_at) - _parse(started_at)).total_seconds() * 1000)
                if handle_time_ms < 0:
                    handle_time_ms = None
            except Exception:
                handle_time_ms = None
        return [
            {
                "table":          "sessions",
                "session_id":     session_id,
                "tenant_id":      tenant_id,
                "channel":        payload.get("channel", ""),
                "pool_id":        payload.get("pool_id") or "",
                "customer_id":    payload.get("customer_id") or payload.get("contact_id"),
                # Journey T1/T4: a linha de close é a SOBREVIVENTE no ReplacingMergeTree —
                # tem de repetir a aresta de proveniência E o seu rótulo, ou o fechamento
                # os apaga. O bridge os carimba lendo o ctx (durável), em vez de depender
                # da cache em memória do consumer.
                "origin_session_id": payload.get("origin_session_id") or None,
                "spawn_reason":      payload.get("spawn_reason") or None,
                "opened_at":      started_at or ended_at,
                "closed_at":      ended_at,
                # Fase A (queue-attended-model): business close_reason takes priority;
                # transport "reason" is only a legacy fallback for old producers.
                "close_reason":   payload.get("close_reason") or payload.get("reason"),
                "outcome":        payload.get("outcome"),
                "handle_time_ms": handle_time_ms,
                "timestamp":      ended_at,
                "status":         "closed",
                # SLA do pool repetido no close: a linha de fechamento é a que
                # sobrevive no ReplacingMergeTree — sem isso o valor gravado
                # pelo parse_routed é substituído por NULL.
                "sla_target_ms":  payload.get("sla_target_ms"),
                # Journey J1: MESMO motivo do sla — a linha de fechamento (bridge, único
                # writer de close) é a que sobrevive; precisa repetir o root propagado,
                # senão o _session_row cai no DEFAULT session_id e a raiz da FILHA some.
                # O bridge carimba root_session_id neste evento (_close_contact_layer).
                "root_session_id": payload.get("root_session_id") or session_id,
                "journey_id":      payload.get("root_session_id") or session_id,
                "origin":         origin_from_source(payload.get("source")),
            }
        ]

    # Arc 19: webhook session suspended (skill-flow hit a suspend step).
    # The orchestrator-bridge publishes this to conversations.events after writing
    # the event to the canonical Redis stream, so analytics can track it in ClickHouse.
    if event_type == "session_suspended":
        # `sessions` é ReplacingMergeTree de LINHA INTEIRA — a versão mais nova SUBSTITUI
        # a anterior, não faz merge por coluna. Uma linha parcial (só `status`) tem
        # row_version mais novo que o do `routed` e **apaga** pool_id/channel/customer_id.
        # Por isso o bridge repete a identidade da sessão neste evento e nós a mapeamos
        # (mesma razão pela qual o root já era repetido aqui — o problema era mais amplo
        # do que o root).
        #
        # `opened_at` é a ABERTURA da sessão, não o instante do suspend: carimbar o
        # timestamp do suspend aqui reescrevia a abertura e corrompia TMA/duração.
        # Fallback para o timestamp só existe para eventos legados sem `opened_at`.
        return [
            {
                "table":      "sessions",
                "session_id": session_id,
                "tenant_id":  tenant_id,
                "opened_at":  payload.get("opened_at") or payload.get("timestamp") or _now(),
                "timestamp":  payload.get("timestamp") or _now(),
                "status":     "suspended",
                "pool_id":     payload.get("pool_id") or None,
                "channel":     payload.get("channel") or None,
                "customer_id": payload.get("customer_id") or None,
                # Journey J1: repetir root p/ não reverter no ReplacingMergeTree.
                "root_session_id": payload.get("root_session_id") or session_id,
                "journey_id":      payload.get("root_session_id") or session_id,
                "origin":     origin_from_source(payload.get("source")),
            }
        ]

    if event_type == "message_sent":
        return [
            {
                "table":        "messages",
                "message_id":   payload.get("message_id") or _gen_id(),
                "tenant_id":    tenant_id,
                "session_id":   session_id,
                "author_id":    payload.get("author_id") or payload.get("participant_id"),
                "author_role":  payload.get("author_role") or payload.get("role", ""),
                "channel":      payload.get("channel", ""),
                "content_type": payload.get("content_type") or "",
                "visibility":   payload.get("visibility") or "all",
                "content":      payload.get("content"),
                "timestamp":    payload.get("timestamp") or _now(),
                "origin":       origin_from_source(payload.get("source")),
            }
        ]

    # Business events from insight_register MCP tool (insight.registered, insight.historico.*, etc.)
    if event_type and event_type.startswith("insight."):
        import uuid as _uuid
        insight_type = event_type  # e.g. "insight.registered" or "insight.historico.cancelamento"
        data = payload.get("data") or payload.get("insight") or payload
        return [
            {
                "table":        "contact_insights",
                "insight_id":   payload.get("insight_id") or str(_uuid.uuid4()),
                "tenant_id":    tenant_id,
                "session_id":   session_id,
                "insight_type": insight_type,
                "category":     data.get("category") or payload.get("category") or "",
                "value":        str(data.get("value") or payload.get("value") or ""),
                "tags":         data.get("tags") or payload.get("tags") or [],
                "agent_id":     payload.get("agent_id") or payload.get("instance_id") or None,
                "timestamp":    payload.get("timestamp") or _now(),
            }
        ]

    # Unknown/untracked event type — skip silently
    return None


# ─── agent.lifecycle ──────────────────────────────────────────────────────────

def parse_agent_lifecycle(payload: dict[str, Any]) -> dict | None:
    """
    Handles agent.lifecycle events.

    agent_done  → None (não persiste). O EVENTO segue existindo e é essencial ao
                  routing-engine (libera capacidade); o que saiu foi a gravação
                  analítica em `agent_events`, tabela descontinuada em 2026-07-28.
                  O fim de atendimento vive em `segments.ended_at`/`outcome`.
    agent_pause → agent_pause_intervals table (open interval; action="open").
    agent_ready → may close an open pause interval (action="close").
                  The consumer is responsible for Redis state tracking and
                  will only write a close row when a matching open interval
                  is found in Redis.

    For agent_pause and agent_ready the dict carries an "action" key
    ("open" / "close_check") so the consumer can dispatch them correctly.
    Other event types are skipped (return None).
    """
    event     = payload.get("event", "")
    tenant_id = payload.get("tenant_id", "")
    instance_id = payload.get("instance_id", "")

    # (removido 2026-07-28) O ramo `agent_done` → `agent_events`.
    #
    # A tabela era substrato derivado que duplicava `segments`, e este ramo era o
    # sintoma mais visível disso: exigia `session_id`, mas os 9 call sites do
    # orchestrator-bridge chaveiam o contato como `conversation_id` — então
    # descartava 100% do agent_done do bridge, em silêncio. O que sobrava vinha do
    # `runtime.ts`, que não manda outcome/pool_id/agent_type_id/handle_time_ms:
    # linha praticamente vazia.
    #
    # O fim de atendimento continua registrado — em `segments.ended_at` +
    # `outcome`, na mesma linha do início (via `conversations.participants`), com
    # os campos que faltavam aqui.
    #
    # ⚠️ O EVENTO `agent_done` NÃO foi removido: o routing-engine depende dele em
    # `agent.lifecycle` para liberar capacidade (`remove_conversation` → semáforo
    # de vagas). O que saiu foi apenas a gravação analítica redundante.
    #
    # Não confundir com a tool `agent_event` (Arc 12) → `agent_business_events`:
    # eixo diferente (KPI que o agente declara), preservado.

    if event == "agent_pause":
        if not tenant_id or not instance_id:
            return None
        import uuid as _uuid
        return {
            "table":        "agent_pause_intervals",
            "action":       "open",
            "interval_id":  str(_uuid.uuid4()),
            "tenant_id":    tenant_id,
            "instance_id":  instance_id,
            "agent_type_id": payload.get("agent_type_id") or "",
            "pool_id":       payload.get("pool_id") or "",
            "reason_id":    payload.get("reason_id", ""),
            "reason_label": payload.get("reason_label", ""),
            "note":         payload.get("note") or None,
            "paused_at":    payload.get("timestamp") or _now(),
        }

    if event == "agent_ready":
        if not tenant_id or not instance_id:
            return None
        # Return a sentinel that tells the consumer to check Redis for an
        # open pause interval.  The consumer will enrich this into a full
        # close row or drop it if no open interval exists.
        return {
            "table":        "agent_pause_intervals",
            "action":       "close_check",
            "tenant_id":    tenant_id,
            "instance_id":  instance_id,
            "resumed_at":   payload.get("timestamp") or _now(),
        }

    # agent_login / agent_logout: handled out-of-band by the consumer's login
    # interval state machine (_handle_login_interval) → agent_login_intervals,
    # not via this parse→write flow. agent_busy / agent_heartbeat stay untracked.
    return None


# ─── usage.events ─────────────────────────────────────────────────────────────

def parse_usage_event(payload: dict[str, Any]) -> dict | None:
    """Passthrough from usage.events → usage_events table."""
    event_id  = payload.get("event_id")
    tenant_id = payload.get("tenant_id")
    dimension = payload.get("dimension")
    if not event_id or not tenant_id or not dimension:
        return None

    return {
        "table":            "usage_events",
        "event_id":         event_id,
        "tenant_id":        tenant_id,
        "session_id":       payload.get("session_id") or "",
        "dimension":        dimension,
        "quantity":         int(payload.get("quantity", 1)),
        "source_component": payload.get("source_component") or "",
        "timestamp":        payload.get("timestamp") or _now(),
    }


# ─── pool.occupancy (Fase 2 — pico de concorrência por minuto) ────────────────

def parse_pool_occupancy(payload: dict[str, Any]) -> dict | None:
    """Maps pool.occupancy (Routing Engine occupancy sampler) → pool_occupancy_peaks."""
    tenant_id = payload.get("tenant_id")
    pool_id   = payload.get("pool_id")
    minute    = payload.get("minute")
    if not tenant_id or not pool_id or not minute:
        return None
    return {
        "table":                "pool_occupancy_peaks",
        "tenant_id":            tenant_id,
        "pool_id":              pool_id,
        "minute":               minute,
        "peak_concurrency":     int(payload.get("peak_concurrency", 0)),
        "provisioned_capacity": int(payload.get("provisioned_capacity", 0)),
        # Item 7b — sessões debitando C atribuídas ao pool (reserva + shared HASH);
        # nas linhas agregadas __reserved__/__shared__/__buffer__, espelha o peak.
        "admitted_peak":        int(payload.get("admitted_peak", 0)),
    }


# ─── sentiment.updated ────────────────────────────────────────────────────────

def parse_sentiment_event(
    payload:    dict[str, Any],
    segment_id: str | None = None,
) -> dict | None:
    """
    Maps sentiment.updated → sentiment_events table.

    The optional ``segment_id`` parameter is injected by the consumer after
    post-hoc enrichment via SegmentEnricher.lookup_primary().  When enrichment
    fails (Redis and ClickHouse both miss) the field is stored as None.
    """
    event_id  = payload.get("event_id")
    tenant_id = payload.get("tenant_id")
    session_id = payload.get("session_id")
    if not event_id or not tenant_id or not session_id:
        return None

    return {
        "table":      "sentiment_events",
        "event_id":   event_id,
        "tenant_id":  tenant_id,
        "session_id": session_id,
        "pool_id":    payload.get("pool_id") or "",
        "score":      float(payload.get("score", 0.0)),
        "category":   payload.get("category") or "neutral",
        "segment_id": segment_id or None,
        "timestamp":  payload.get("timestamp") or _now(),
    }


# ─── mcp.audit ────────────────────────────────────────────────────────────────

def parse_mcp_audit_event(
    payload:    dict[str, Any],
    segment_id: str | None = None,
) -> dict | None:
    """
    Maps mcp.audit → session_timeline table.

    AuditRecord fields used:
      tenant_id, session_id, instance_id, server_name, tool_name,
      allowed, injection_detected, duration_ms, timestamp.

    The optional ``segment_id`` is injected by the consumer after post-hoc
    enrichment via SegmentEnricher.lookup_by_instance(instance_id).
    When no segment can be resolved the row is still written with
    segment_id = "" (session_timeline.segment_id is non-Nullable String).
    """
    import json as _json

    tenant_id  = payload.get("tenant_id")
    session_id = payload.get("session_id")
    if not tenant_id or not session_id:
        # mcp.audit events without a session_id are system-level calls
        # (e.g. from the orchestrator-bridge startup); not attributable to a
        # session so we skip them here.
        return None

    instance_id = payload.get("instance_id") or ""
    server_name = payload.get("server_name") or ""
    tool_name   = payload.get("tool_name") or ""
    allowed     = bool(payload.get("allowed", True))
    injection   = bool(payload.get("injection_detected", False))
    duration_ms = payload.get("duration_ms")

    # Pack relevant audit fields into the generic payload JSON
    audit_payload = {
        "server_name":         server_name,
        "tool_name":           tool_name,
        "allowed":             allowed,
        "injection_detected":  injection,
        "duration_ms":         duration_ms,
        "source":              payload.get("source") or "",
        "data_categories":     payload.get("data_categories") or [],
        "masked_input_fields": payload.get("masked_input_fields") or [],
    }

    return {
        "table":      "session_timeline",
        "event_id":   payload.get("event_id") or _gen_id(),
        "tenant_id":  tenant_id,
        "session_id": session_id,
        "segment_id": segment_id or "",
        "event_type": "mcp.tool_call",
        "actor_id":   instance_id,
        "actor_role": "agent",
        "payload":    _json.dumps(audit_payload),
        "timestamp":  payload.get("timestamp") or _now(),
    }


# ─── queue.position_updated ───────────────────────────────────────────────────

def parse_queue_position(payload: dict[str, Any]) -> dict | None:
    """Maps queue.position_updated events → queue_events table."""
    session_id = payload.get("session_id")
    tenant_id  = payload.get("tenant_id")
    if not session_id or not tenant_id:
        return None

    return {
        "table":             "queue_events",
        "event_id":          _gen_id(),
        "tenant_id":         tenant_id,
        "session_id":        session_id,
        "pool_id":           payload.get("pool_id") or "",
        "event_type":        "position_updated",
        # `queue_position` = posição DESTE contato (1-based). Compat: publishers
        # antigos só mandavam `queue_length` (tamanho da fila) e a coluna recebia o
        # tamanho no lugar da posição — mantido como fallback para não perder o
        # histórico, mas o publisher atual manda os dois campos separados.
        "queue_position":    (
            payload["queue_position"] if payload.get("queue_position") is not None
            else payload.get("queue_length")
        ),
        "estimated_wait_ms": payload.get("estimated_wait_ms"),
        # F5 — resíduo fechado em 2026-08-03. Este mapper era o ÚLTIMO leitor de
        # `available_agents`: o produtor (`_publish_queue_position`) parou de publicá-lo
        # em 2026-08-02, então o `payload.get()` já devolvia None sempre — leitor sem
        # produtor, a mesma família de dívida que o arco fecha do outro lado
        # (config sem leitor).
        #
        # Fixado em None de propósito, em vez de deletado: a coluna existe em
        # `queue_events` (Nullable, dados históricos) e dropá-la é migração à parte.
        # A diferença entre `payload.get(...)` e `None` é quem decide — com o `get`,
        # bastaria alguém voltar a publicar o campo para ele reviver em silêncio, com o
        # mesmo valor errado de antes (`SCARD(pool:instances)` = PERTENCIMENTO, não
        # capacidade). Com o `None`, ressuscitar exige editar esta linha e ler isto.
        "available_agents":  None,
        "timestamp":         payload.get("published_at") or _now(),
    }


# ─── workflow.events ──────────────────────────────────────────────────────────

# Maps workflow event_type → status label stored in workflow_events.status
_WORKFLOW_STATUS_MAP = {
    "workflow.started":   "active",
    "workflow.suspended": "suspended",
    "workflow.resumed":   "active",
    "workflow.completed": "completed",
    "workflow.timed_out": "timed_out",
    "workflow.failed":    "failed",
    "workflow.cancelled": "cancelled",
}


def parse_workflow_event(payload: dict[str, Any]) -> dict | None:
    """Maps workflow.* events → workflow_events table."""
    event_type  = payload.get("event_type")
    tenant_id   = payload.get("tenant_id")
    instance_id = payload.get("instance_id")
    flow_id     = payload.get("flow_id", "")
    if not event_type or not tenant_id or not instance_id:
        return None

    return {
        "table":            "workflow_events",
        "event_id":         _gen_id(),
        "tenant_id":        tenant_id,
        "instance_id":      instance_id,
        "flow_id":          flow_id,
        "pool_id":          payload.get("pool_id"),
        "campaign_id":      payload.get("campaign_id"),
        "event_type":       event_type,
        "status":           _WORKFLOW_STATUS_MAP.get(event_type),
        "current_step":     payload.get("current_step"),
        "suspend_reason":   payload.get("suspend_reason"),
        "decision":         payload.get("decision"),
        "outcome":          payload.get("outcome"),
        "duration_ms":      payload.get("duration_ms"),
        "wait_duration_ms": payload.get("wait_duration_ms"),
        "error":            payload.get("error"),
        "timestamp":        payload.get("timestamp") or _now(),
    }


# ─── collect.events ───────────────────────────────────────────────────────────

# Maps collect event_type → status
_COLLECT_STATUS_MAP = {
    "collect.requested": "requested",
    "collect.sent":      "sent",
    "collect.responded": "responded",
    "collect.timed_out": "timed_out",
}


def parse_collect_event(payload: dict[str, Any]) -> dict | None:
    """Maps collect.* events → collect_events table."""
    event_type    = payload.get("event_type")
    tenant_id     = payload.get("tenant_id")
    instance_id   = payload.get("instance_id")
    collect_token = payload.get("collect_token")
    if not event_type or not tenant_id or not instance_id or not collect_token:
        return None

    return {
        "table":         "collect_events",
        "collect_token": collect_token,
        "tenant_id":     tenant_id,
        "instance_id":   instance_id,
        "flow_id":       payload.get("flow_id", ""),
        "campaign_id":   payload.get("campaign_id"),
        "step_id":       payload.get("step_id", ""),
        "target_type":   payload.get("target_type", ""),
        "channel":       payload.get("channel", ""),
        "interaction":   payload.get("interaction", ""),
        "status":        _COLLECT_STATUS_MAP.get(event_type, event_type),
        "send_at":       payload.get("send_at"),
        "responded_at":  payload.get("timestamp") if event_type == "collect.responded" else None,
        "elapsed_ms":    payload.get("elapsed_ms"),
        "timestamp":     payload.get("timestamp") or _now(),
    }


# ─── conversations.participants ───────────────────────────────────────────────

def parse_participant_event(payload: dict[str, Any]) -> list[dict] | None:
    """
    Maps participant_joined / participant_left events → two tables:
      - participation_intervals  (legacy, ORDER BY tenant_id+session_id+participant_id)
      - segments                 (Arc 5 ContactSegment, ORDER BY tenant_id+session_id+segment_id)

    Both event types upsert the same row in each table. ReplacingMergeTree ensures
    the later "left" write (with ended_at / left_at set) wins on background merge.
    """
    event_type     = payload.get("type")
    session_id     = payload.get("session_id")
    tenant_id      = payload.get("tenant_id")
    participant_id = payload.get("participant_id")

    if event_type not in ("participant_joined", "participant_left"):
        return None
    if not session_id or not tenant_id or not participant_id:
        return None

    _event_id = payload.get("event_id") or _gen_id()
    _segment_id = payload.get("segment_id") or _gen_id()

    participation_row = {
        "table":          "participation_intervals",
        "event_id":       _event_id,
        "session_id":     session_id,
        "tenant_id":      tenant_id,
        "participant_id": participant_id,
        "pool_id":        payload.get("pool_id") or "",
        "agent_type_id":  payload.get("agent_type_id") or "",
        "role":           payload.get("role") or "",
        "agent_type":     payload.get("agent_type") or "",
        "conference_id":  payload.get("conference_id") or None,
        "joined_at":      payload.get("joined_at"),
        "type":           event_type,
        "duration_ms":    payload.get("duration_ms"),
        "timestamp":      payload.get("timestamp") or _now(),
    }

    # Arc 5: segment row — written on both joined and left;
    # the "left" write populates ended_at and outcome fields.
    segment_row = {
        "table":             "segments",
        "event_id":          _event_id,
        "segment_id":        _segment_id,
        "session_id":        session_id,
        "tenant_id":         tenant_id,
        "participant_id":    participant_id,
        "pool_id":           payload.get("pool_id") or "",
        "agent_type_id":     payload.get("agent_type_id") or "",
        "flow_id":           payload.get("flow_id") or "",
        "deploy_version":    payload.get("deploy_version") or "",   # R9 — versão do deploy (AI)
        "channel":           payload.get("channel") or "",          # R9 — canal da sessão
        "user_id":           payload.get("user_id") or "",
        "user_login":        payload.get("user_login") or "",
        "instance_id":       payload.get("participant_id") or "",
        "role":              payload.get("role") or "",
        "agent_type":        payload.get("agent_type") or "",
        "parent_segment_id": payload.get("parent_segment_id") or None,
        "sequence_index":    int(payload.get("sequence_index", 0)),
        "conference_id":     payload.get("conference_id") or None,
        "joined_at":         payload.get("joined_at"),
        "started_at":        payload.get("joined_at") or payload.get("timestamp") or _now(),
        "type":              event_type,
        "duration_ms":       payload.get("duration_ms"),
        "outcome":           payload.get("outcome") or None,
        "close_reason":      payload.get("close_reason") or None,
        "handoff_reason":    payload.get("handoff_reason") or None,
        "issue_status":      payload.get("issue_status") or None,
        "escalation_reason": payload.get("escalation_reason") or None,
        # Prosa do wrap-up — sempre gravada, inclusive quando resolvido (fix 2026-07-30).
        "wrapup_summary":    payload.get("wrapup_summary") or None,
        "wrapup_next_steps": payload.get("wrapup_next_steps") or None,
        "timestamp":         payload.get("timestamp") or _now(),
        # Substrate isolation: procedência derivada do source (live|import|reeval).
        "origin":            origin_from_source(payload.get("source")),
    }

    return [participation_row, segment_row]


# ─── evaluation.events (Arc 6) ────────────────────────────────────────────────

# Maps evaluation event_type → status stored in evaluation_events.eval_status
_EVAL_EVENT_STATUS_MAP = {
    "evaluation.submitted":  "submitted",
    "evaluation.reviewed":   None,          # actual status in payload (approved/rejected/etc.)
    "evaluation.contested":  "contested",
    "evaluation.locked":     "locked",
}


def parse_evaluation_event(payload: dict[str, Any]) -> list[dict] | None:
    """
    Maps evaluation.* events to two tables:
      - evaluation_results  — upserts the latest status of the result
      - evaluation_events   — append-only lifecycle event log

    Recognised event_type values:
      evaluation.submitted  → new EvaluationResult written by agente_avaliacao_v1
      evaluation.reviewed   → supervisor approved/adjusted/rejected
      evaluation.contested  → agent submitted contestation
      evaluation.locked     → result permanently locked
    """
    event_type = payload.get("event_type")
    tenant_id  = payload.get("tenant_id")
    result_id  = payload.get("result_id")

    # T11 — evaluation_finalized: invariante de qualidade (modo Oficial). Vai p/ a tabela
    # dedicada `evaluation_finalized` (keyed por instance_id), NÃO p/ evaluation_results
    # (evita a colisão de identidade result_id=evaluation_id do completed). final_score
    # normalizado 0–1 (escala dos buckets do relatório).
    if event_type == "evaluation_finalized":
        if not tenant_id:
            return None
        fs = payload.get("final_score")
        try:
            fs = float(fs)
            fs = fs / 10.0 if fs > 1.0 else fs
        except (TypeError, ValueError):
            fs = 0.0
        return [{
            "table":                "evaluation_finalized",
            "instance_id":          payload.get("instance_id") or "",
            "result_id":            payload.get("result_id") or "",
            "session_id":           payload.get("session_id") or "",
            "tenant_id":            tenant_id,
            "campaign_id":          payload.get("campaign_id") or None,
            "final_score":          fs,
            "finalize_reason":      payload.get("finalize_reason") or "",
            "contestation_state":   payload.get("contestation_state") or "",
            "evaluated_agent_type": payload.get("evaluated_agent_type") or "",
            "segment_id":           payload.get("segment_id") or "",
            "form_version":         payload.get("form_version") or 0,
            "round":                payload.get("round") or 1,
            "process_duration_ms":  payload.get("process_duration_ms") or 0,
            "timestamp":            payload.get("timestamp") or _now(),
        }]

    # F2 (bancada de agentes): aceita o evento publicado DIRETO pelo avaliador
    # (evaluation_submit → event_type "evaluation.completed", com evaluation_id e
    # composite_score 0–10). O caminho desenhado no Arc 13 (eval.instance.submitted
    # → ingest na evaluation-api → re-publish "evaluation.submitted" com result_id)
    # está inacabado — o consumer não existe na evaluation-api — então sem este
    # mapeamento o resultado do avaliador nunca chega ao ClickHouse.
    # result_id := evaluation_id; overall_score := composite_score/10 (escala 0–1
    # dos buckets do relatório). timestamp := evaluated_at quando presente.
    if event_type == "evaluation.completed" and not result_id:
        result_id = payload.get("evaluation_id")

    if not event_type or not tenant_id or not result_id:
        return None

    ts         = payload.get("timestamp") or payload.get("evaluated_at") or _now()
    session_id = payload.get("session_id") or ""
    instance_id = payload.get("instance_id") or ""
    campaign_id = payload.get("campaign_id") or None
    # Os buckets/avg do relatório operam em escala 0–1. Preferir normalized_score
    # (já 0–1); senão normalizar overall_score (÷10 se vier em 0–10); fallback composite.
    overall_score = payload.get("normalized_score")
    if overall_score is None:
        raw = payload.get("overall_score")
        if raw is not None:
            try:
                raw = float(raw)
                overall_score = raw / 10.0 if raw > 1.0 else raw
            except (TypeError, ValueError):
                overall_score = None
        elif payload.get("composite_score") is not None:
            try:
                overall_score = float(payload["composite_score"]) / 10.0
            except (TypeError, ValueError):
                overall_score = None
    eval_status = payload.get("eval_status") or _EVAL_EVENT_STATUS_MAP.get(event_type, event_type)
    locked = 1 if payload.get("locked") or event_type == "evaluation.locked" else 0
    compliance_flags = payload.get("compliance_flags") or []
    actor_id = (
        payload.get("reviewed_by")
        or payload.get("contested_by")
        or payload.get("evaluator_id")
        or None
    )

    result_row = {
        "table":           "evaluation_results",
        "result_id":       result_id,
        "instance_id":     instance_id,
        "session_id":      session_id,
        "tenant_id":       tenant_id,
        "evaluator_id":    payload.get("evaluator_id") or "",
        "form_id":         payload.get("form_id") or "",
        "campaign_id":     campaign_id,
        "overall_score":   float(overall_score) if overall_score is not None else 0.0,
        "eval_status":     eval_status or "submitted",
        "locked":          locked,
        "compliance_flags": list(compliance_flags),
        "timestamp":       ts,
    }

    event_row = {
        "table":         "evaluation_events",
        "event_id":      _gen_id(),
        "tenant_id":     tenant_id,
        "result_id":     result_id,
        "instance_id":   instance_id,
        "session_id":    session_id,
        "campaign_id":   campaign_id,
        "event_type":    event_type,
        "eval_status":   eval_status,
        "overall_score": float(overall_score) if overall_score is not None else None,
        "actor_id":      actor_id,
        "timestamp":     ts,
    }

    rows = [result_row, event_row]

    # F8 (bancada): nota por dimensão → evaluation_dimension_scores. Decompõe o
    # overall_score. Atribuição ao agente avaliado é query-time (via session_id,
    # como a lente quality). Duas fontes possíveis no evento evaluation.completed:
    #   - dimensions[]        (Arc 6): {dimension_id, name, score 0–10, weight}
    #   - dimension_threads[] (Arc 13, caminho do agente_avaliacao_v1 no demo):
    #                         {dimension_id, score, justification, evidence_entries}
    # Preferimos dimensions[] (tem name/weight); senão derivamos dos threads
    # (name := dimension_id; o label legível vem do form na consulta da F8.2).
    dims_src = payload.get("dimensions")
    if not (isinstance(dims_src, list) and dims_src):
        threads = payload.get("dimension_threads")
        if isinstance(threads, list):
            dims_src = [
                {"dimension_id": th.get("dimension_id"),
                 "name": th.get("dimension_id"),
                 "score": th.get("score"), "weight": None}
                for th in threads if isinstance(th, dict)
            ]
    if isinstance(dims_src, list):
        for dim in dims_src:
            if not isinstance(dim, dict):
                continue
            did = dim.get("dimension_id")
            if not did:
                continue
            rows.append({
                "table":          "evaluation_dimension_scores",
                "result_id":      result_id,
                "instance_id":    instance_id,
                "session_id":     session_id,
                "tenant_id":      tenant_id,
                "evaluator_id":   payload.get("evaluator_id") or "",
                "form_id":        payload.get("form_id") or "",
                "campaign_id":    campaign_id,
                "dimension_id":   did,
                "dimension_name": dim.get("name") or did,
                "score":          dim.get("score"),
                "weight":         dim.get("weight"),
                "eval_status":    eval_status or "submitted",
                "timestamp":      ts,
            })

    return rows


# journey.events (Arc 10) — REMOVED (Arc 19 Fase F)
# Journey entity superseded by Arc 19 unified session model.
# See CHANGELOG.md for history (Arcs 10, 16, 17).


# ─── agent.events (Arc 12) ────────────────────────────────────────────────────

def parse_agent_business_event(
    payload: dict[str, Any], segment_id: str | None = None,
) -> dict | None:
    """
    Maps agent.events topic → agent_business_events table.

    The mcp-server pre-decomposes category into category_l1..l4 at publish time,
    so the consumer simply passes them through.  If a level is absent it defaults
    to an empty string.

    `segment_id` (Arc 12 fatia 2) chega por dois caminhos e a PRECEDÊNCIA importa: o do
    payload (o skill declarou o próprio segmento) vence o resolvido pelo enricher. Se
    divergirem, quem sabe é o emissor — o enricher deduz a partir do `instance_id`, e uma
    instância pode ter mais de um segmento ao longo da sessão.
    """
    tenant_id  = payload.get("tenant_id")
    session_id = payload.get("session_id")
    category   = payload.get("category")

    if not tenant_id or not session_id or not category:
        return None

    value = payload.get("value")
    if value is None:
        return None

    try:
        value = float(value)
    except (TypeError, ValueError):
        return None

    # category levels — pre-decomposed by mcp-server; fall back to splitting here
    # in case an older publisher omits them.
    parts = category.split(".")
    category_l1 = payload.get("category_l1") or (parts[0] if len(parts) > 0 else "")
    category_l2 = payload.get("category_l2") or (parts[1] if len(parts) > 1 else "")
    category_l3 = payload.get("category_l3") or (parts[2] if len(parts) > 2 else "")
    category_l4 = payload.get("category_l4") or (parts[3] if len(parts) > 3 else "")

    tags = payload.get("tags") or {}
    if not isinstance(tags, dict):
        tags = {}

    return {
        "table":          "agent_business_events",
        "event_id":       payload.get("event_id") or _gen_id(),
        "tenant_id":      tenant_id,
        "session_id":     session_id,
        "journey_id":     payload.get("journey_id") or None,
        "agent_type_id":  payload.get("agent_type_id") or "",
        "skill_id":       payload.get("skill_id") or "",
        "pool_id":        payload.get("pool_id") or "",
        "category":       category,
        "category_l1":    category_l1,
        "category_l2":    category_l2,
        "category_l3":    category_l3,
        "category_l4":    category_l4,
        "value":          value,
        "tags":           {str(k): str(v) for k, v in tags.items()},
        # Arc 12 fatia 2 — atribuição por participante.
        # `segment_id` vem do skill (`$.segment_id`, caminho A). `instance_id` viaja
        # junto e NÃO é coluna: é a chave que o `SegmentEnricher` usa para resolver o
        # segmento quando A não veio (caminho B), no mesmo ponto em que `mcp.audit` já
        # é enriquecido. Por isso ele fica na linha intermediária e some antes do
        # INSERT — o row builder só lê `segment_id`.
        "segment_id":     payload.get("segment_id") or segment_id or None,
        "instance_id":    payload.get("instance_id") or None,
        "emitted_at":     payload.get("emitted_at") or _now(),
    }


# ─── session.signals (F10 — voz do cliente/agente grão session/workflow) ──────
#
# Produzido pela tool MCP dedicada `survey_record`: uma pesquisa outbound (sessão
# própria) que religa à sessão original via origin_session_id e grava o resultado
# contra ela. Vantagem sobre reusar agent_event: origin_session_id, grain e as
# métricas são parâmetros estruturados de 1ª classe — sem a checagem de namespace
# (category[0]==pool) nem convenção de sufixo. Grão segmento (por agente) NÃO entra
# aqui (vive em segments, F5). Grão = O QUE a pesquisa cobre (session|workflow); o
# timing (no ato × diferido) é captured_at × session_at, não um grão.


# Grãos que a tabela session_signal possui — TODOS (gravação explícita via
# survey_record). Espelha SESSION_SIGNAL_GRAINS de @plughub/schemas. `segment`
# carrega segment_id + agent_key (atribuição); os demais não são atribuíveis.
# segments.nps_score (F5) foi DROPADA (item 5): NPS de segmento vive só aqui.
_SESSION_SIGNAL_GRAINS = ("segment", "session", "workflow", "journey")


def _signal_source_for_metric(metric: str) -> str:
    """Fonte normalizada a partir da métrica do sinal (catálogo — S1)."""
    return survey_catalog.source_for(metric)


def _normalize_signal_value(
    metric: str, value: float, scale: dict | None = None,
) -> tuple[float, str | None]:
    """Valor + rótulo categórico do sinal, pelo catálogo de instrumentos (S1).

    Cobre os CINCO instrumentos (antes só nps/csat tinham rótulo; ces/pmf/fcr caíam
    crus em `customer_survey` — distorção silenciosa no relatório). O `value` é
    devolvido SEM alteração: a escala carimbada pelo produtor serve para escolher a
    banda, nunca para reescrever a resposta do cliente. Métrica fora do catálogo
    (pesquisa customizada do tenant) segue passando cru, com label None.
    """
    return value, survey_catalog.label_for(metric, value, scale)


def parse_session_signal_event(payload: dict[str, Any]) -> list[dict] | None:
    """
    Maps session.signals topic → session_signal table (one row per metric).

    Event (publicado por survey_record):
      { event_id, tenant_id, origin_session_id, grain (segment|session|workflow|journey),
        segment_id?, agent_key?, survey_session_id?, pool_id?, captured_at,
        signals: [{ metric, value, value_label? }, ...] }

    O sinal é chaveado ao origin_session_id (a sessão pesquisada). Para grão
    `segment` o evento carrega segment_id (obrigatório) + agent_key (atribuição ao
    agente); demais grãos têm agent_key vazio (não atribuíveis). Normalização
    nps/csat aplicada quando value_label não vem explícito. session_at = captured_at
    aqui (default no-ato/mesmo dia); para surveys DIFERIDAS o consumer sobrescreve
    session_at com o opened_at da sessão original via enrichment (F11 —
    _enrich_signal_session_at em consumer.py). value_label explícito do produtor
    prevalece. O timing (no ato × diferido) é captured_at × session_at, não um grão.
    """
    tenant_id  = payload.get("tenant_id")
    origin_id  = payload.get("origin_session_id")
    grain      = payload.get("grain")
    signals    = payload.get("signals")

    if not tenant_id or not origin_id or not grain:
        return None
    if grain not in _SESSION_SIGNAL_GRAINS:
        return None
    if not isinstance(signals, list) or not signals:
        return None

    segment_id = payload.get("segment_id") or ""
    agent_key  = payload.get("agent_key") or ""
    # grão segment exige atribuição (segment_id) — sem ele a linha não é comparável
    # por agente nem dedup-safe (segment_id entra na chave do ReplacingMergeTree).
    if grain == "segment" and not segment_id:
        return None

    captured_at = payload.get("captured_at") or _now()
    base_event  = payload.get("event_id") or _gen_id()
    pool_ctx    = payload.get("pool_id") or ""

    rows: list[dict] = []
    for idx, sig in enumerate(signals):
        if not isinstance(sig, dict):
            continue
        metric = sig.get("metric")
        raw_value = sig.get("value")
        if not metric or raw_value is None:
            continue
        try:
            value_num = float(raw_value)
        except (TypeError, ValueError):
            continue

        # Customer Voice: escala carimbada pelo produtor (snapshot imutável da
        # DialogDimension). Ausente → NULL (roll-up cai no default do catálogo).
        scale = sig.get("scale") if isinstance(sig.get("scale"), dict) else None

        # S1: a escala carimbada entra na escolha da BANDA (CSAT 1–5 × 1–10 têm
        # cortes distintos); o valor gravado segue cru.
        norm_value, norm_label = _normalize_signal_value(metric, value_num, scale)
        # value_label explícito do produtor prevalece sobre a normalização.
        value_label = sig.get("value_label") or norm_label

        rows.append({
            "table":             "session_signal",
            "signal_id":         f"{base_event}:{idx}",
            "tenant_id":         tenant_id,
            "session_id":        origin_id,   # chaveado à sessão original
            "grain":             grain,
            "segment_id":        segment_id,  # set p/ grão segment; '' nos demais
            "agent_key":         agent_key,   # atribuição p/ segment; '' nos demais
            "pool_id":           pool_ctx,    # contexto, não atribuição
            "source":            _signal_source_for_metric(metric),
            "metric":            metric,
            "value_num":         norm_value,
            "value_label":       value_label,
            "scale_min":         scale.get("min") if scale else None,
            "scale_max":         scale.get("max") if scale else None,
            "session_at":        captured_at,  # no-ato: mesmo dia; diferido (≠) → F11 enrichment
            "captured_at":       captured_at,
            "origin_session_id": origin_id,
            "journey_id":        origin_id,    # compat (coluna legada)
        })

    return rows or None


# ─── calibration.events (Arc 13) ─────────────────────────────────────────────

def parse_journey_merged(payload: dict[str, Any]) -> dict | None:
    """
    Journey J3 — maps journey.merges topic → journey_aliases table.

    Grava a aresta de merge source_root (journey NOVA, absorvida) → canonical_root
    (journey ANTIGA, sobrevivente). Ordem novo→antigo é enforçada pela tool
    journey_merge; a resolução canônica (union-find) roda no read layer. Self-merge
    (source == canonical) é no-op. active=1 (revert seria active=0, não wirado em v1).
    """
    tenant_id      = payload.get("tenant_id")
    source_root    = payload.get("source_root")
    canonical_root = payload.get("canonical_root")
    if not tenant_id or not source_root or not canonical_root:
        return None
    if source_root == canonical_root:
        return None
    return {
        "table":          "journey_aliases",
        "tenant_id":      tenant_id,
        "source_root":    source_root,
        "canonical_root": canonical_root,
        "merged_at":      payload.get("merged_at") or _now(),
        "actor":          payload.get("actor", "") or "",
        "active":         1,
    }


def parse_calibration_event(payload: dict[str, Any]) -> dict | None:
    """
    Maps calibration.events topic → calibration_events table.

    Recognised event_type values:
      calibration_reviewed        — curator took action on a CurationReview
      calibration_note_published  — CalibrationNote was ingested into knowledge namespace

    calibration_note_published events are skipped (informational only — not a
    curated review decision).  Only calibration_reviewed carries the decision
    field that feeds the calibration_score metric.
    """
    event_type = payload.get("event_type")
    tenant_id  = payload.get("tenant_id")

    if not event_type or not tenant_id:
        return None

    if event_type != "calibration_reviewed":
        return None  # calibration_note_published — skip

    campaign_id  = payload.get("campaign_id")
    evaluator_id = payload.get("evaluator_id")
    decision     = payload.get("decision")

    if not campaign_id or not evaluator_id or not decision:
        return None

    return {
        "table":          "calibration_events",
        "event_id":       payload.get("event_id") or _gen_id(),
        "tenant_id":      tenant_id,
        "campaign_id":    campaign_id,
        "evaluator_id":   evaluator_id,
        "skill_version":  payload.get("skill_version") or "",
        "decision":       decision,
        "dimension_id":   payload.get("dimension_id") or "",
        "severity":       payload.get("severity") or "",
        "curator_id":     payload.get("curator_id") or None,
        "note_id":        payload.get("note_id") or None,
        "event_time":     payload.get("event_time") or payload.get("timestamp") or _now(),
    }
