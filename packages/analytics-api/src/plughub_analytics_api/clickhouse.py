"""
clickhouse.py
ClickHouse client wrapper + DDL for the Analytics API.

Tables, all in database `plughub`:

  sessions               — session lifecycle (opened, closed, channel, pool, durations)
  queue_events           — contact queued / dequeued / abandoned / position_updated
  messages               — messages published to the canonical stream
  usage_events           — metering events (passthrough from usage.events Kafka topic)
  sentiment_events       — per-turn sentiment scores from AI Gateway
  segments               — Arc 5: ContactSegment per participant participation window
  session_timeline       — Arc 5: time-series events enriched with segment_id
  evaluation_results     — Arc 6: EvaluationResult state (ReplacingMergeTree)
  evaluation_events      — Arc 6: lifecycle audit log (submitted/reviewed/contested/locked)
  contact_insights       — business events from agent flows (insight_register MCP tool)

Materialized views (AggregatingMergeTree — incremental, POPULATE on creation):

  mv_agent_performance_daily — pre-aggregated daily stats per (agent_type_id, pool_id)
  mv_segment_summary         — pre-aggregated participation stats per session_id

Readable views (regular SQL views over the MVs — always up-to-date):

  v_agent_performance — resolution_rate, escalation_rate, avg_duration_ms per agent_type/pool/day
  v_segment_summary   — segment_count, handoff_count, escalations per session

Design decisions:
  - ReplacingMergeTree on every table for idempotent re-inserts (Kafka at-least-once).
  - No explicit version column (ClickHouse rejects Nullable and String as version).
    Deduplication keeps the LAST inserted row per ORDER BY key. Kafka ordering
    guarantees that close/leave events arrive after open/join events, so last-write-wins
    is correct for sessions, participation_intervals, and collect_events.
  - All DateTime columns store UTC (ClickHouse DateTime64 with timezone 'UTC').
  - date column (Date) is the partition key for efficient time-range pruning.
  - ORDER BY always starts with (tenant_id, ...) so tenant-scoped queries are fast.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import clickhouse_connect  # type: ignore[import-untyped]

logger = logging.getLogger("plughub.analytics.clickhouse")

# ─── DDL ─────────────────────────────────────────────────────────────────────

_DDL_DATABASE = "CREATE DATABASE IF NOT EXISTS {db}"

_DDL_SESSIONS = """
CREATE TABLE IF NOT EXISTS {db}.sessions
(
    session_id     String,
    tenant_id      String,
    channel        String,
    pool_id        String,
    customer_id    Nullable(String),
    opened_at      DateTime64(3, 'UTC'),
    closed_at      Nullable(DateTime64(3, 'UTC')),
    close_reason   Nullable(String),
    outcome        Nullable(String),
    wait_time_ms   Nullable(Int64),
    handle_time_ms Nullable(Int64),
    date           Date,
    row_version    DateTime64(3, 'UTC') DEFAULT coalesce(closed_at, opened_at)
)
ENGINE = ReplacingMergeTree(row_version)
PARTITION BY toYYYYMM(date)
ORDER BY (tenant_id, session_id)
"""

# ── row_version — por que existe (bug 2026-07-13) ─────────────────────────────
# A tabela era ReplacingMergeTree() SEM coluna de versão, apostando que "a última
# linha inserida vence" + a ordem do Kafka. A premissa é FALSA: o consumer lê de
# MÚLTIPLOS tópicos (conversations.inbound / .routed / .events / …) e o Kafka só
# ordena DENTRO de uma partição, nunca ENTRE tópicos. Resultado: uma linha
# `routed` (status=active) inserida DEPOIS do `contact_closed` vencia a dedup e
# APAGAVA o fechamento (closed_at → NULL), corrompendo open_count/TMA/SLA.
# Reproduzido no J4c: resume → close em ~14ms; a linha de close existia na tabela
# mas o FINAL devolvia a `active`.
#
# Com ReplacingMergeTree(row_version), vence o EVENTO mais recente, não a inserção
# mais recente. row_version = timestamp do próprio evento (ver _session_row):
# o close carrega `ended_at`, que é por definição o instante final da vida da
# sessão → sempre >= qualquer routed/queued/suspend anterior.
#
# O DEFAULT coalesce(closed_at, opened_at) serve ao REBUILD: nas linhas antigas
# (que não têm row_version gravado) ele faz a linha de close — a única com
# closed_at — vencer a de abertura, reparando o histórico.

# Forward-compatible migration for tables that already exist without customer_id.
# ClickHouse ADD COLUMN IF NOT EXISTS is idempotent.
_DDL_SESSIONS_MIGRATE = (
    "ALTER TABLE {db}.sessions ADD COLUMN IF NOT EXISTS"
    " customer_id Nullable(String) DEFAULT NULL"
)

# ANI = caller/source identifier (phone number, email address, etc.)
# DNIS = dialed/destination identifier — applies to voice, WhatsApp, email, etc.
_DDL_SESSIONS_MIGRATE_ANI_DNIS = (
    "ALTER TABLE {db}.sessions"
    " ADD COLUMN IF NOT EXISTS ani  Nullable(String) DEFAULT NULL,"
    " ADD COLUMN IF NOT EXISTS dnis Nullable(String) DEFAULT NULL"
)

# sla_target_ms — pool SLA threshold (ms) at the time the session was served.
# Used by get_pool_sla_1h to compute compliance %.  NULL = no SLA configured for that pool.
# Populated by parse_routed when the routing result carries sla_target_ms.
_DDL_SESSIONS_MIGRATE_SLA = (
    "ALTER TABLE {db}.sessions"
    " ADD COLUMN IF NOT EXISTS sla_target_ms Nullable(Int64) DEFAULT NULL"
)

_DDL_QUEUE_EVENTS = """
CREATE TABLE IF NOT EXISTS {db}.queue_events
(
    event_id           String,
    tenant_id          String,
    session_id         String,
    pool_id            String,
    event_type         String,
    queue_position     Nullable(Int32),
    estimated_wait_ms  Nullable(Int64),
    available_agents   Nullable(Int32),
    timestamp          DateTime64(3, 'UTC'),
    date               Date
)
ENGINE = ReplacingMergeTree()
PARTITION BY toYYYYMM(date)
ORDER BY (tenant_id, event_id)
"""

# ⚠️ DESCONTINUADA (2026-07-28) — nenhum parser escreve nesta tabela desde então.
#
# Era substrato DERIVADO que duplicava `segments`: guardava `routed` e `agent_done`
# como duas linhas que nenhuma query juntava, enquanto `segments` guarda o mesmo
# par como UMA linha fechada (`started_at`/`ended_at`) e ainda traz `role`,
# `channel`, `close_reason`, `sequence_index`, `conference_id`, `flow_id`.
#
# Não pertencia a nenhum eixo do sistema: não é marcação semântica (essa tem porta
# única — a tool `agent_event` do Arc 12 → `agent_business_events`) nem substrato
# legítimo (esse é `segments`). A semelhança de nome com `agent_business_events` e
# com as rotas `/reports/agent-events/*` (que são Arc 12) é histórica e já induziu
# erro na própria documentação.
#
# FATIA 2 (2026-07-29) — DROP. O CREATE saiu de `_ALL_DDL` e virou este DROP
# idempotente em `_MIGRATIONS`, mesmo padrão do `_DDL_SEGMENTS_DROP_NPS`. A fatia 1
# deixou o DDL de pé por um ciclo para manter o histórico consultável enquanto se
# confirmava que ninguém a lia por fora do código (card ad-hoc no Metabase, query
# direta). Confirmado → a tabela sai fisicamente.
#
# ⚠️ NÃO confundir com `agent_business_events` (Arc 12) nem com as rotas
# `/reports/agent-events/*`, que são OUTRO eixo e ficam. A semelhança de nome já
# induziu erro na documentação.
_DDL_AGENT_EVENTS_DROP = "DROP TABLE IF EXISTS {db}.agent_events"

_DDL_MESSAGES = """
CREATE TABLE IF NOT EXISTS {db}.messages
(
    message_id   String,
    tenant_id    String,
    session_id   String,
    author_id    Nullable(String),
    author_role  String,
    channel      String,
    content_type String,
    visibility   String,
    content      Nullable(String),
    timestamp    DateTime64(3, 'UTC'),
    date         Date
)
ENGINE = ReplacingMergeTree()
PARTITION BY toYYYYMM(date)
ORDER BY (tenant_id, message_id)
"""

# Forward-compatible migrations for messages table (idempotent)
_DDL_MESSAGES_MIGRATE_CONTENT = (
    "ALTER TABLE {db}.messages"
    " ADD COLUMN IF NOT EXISTS author_id Nullable(String) DEFAULT NULL,"
    " ADD COLUMN IF NOT EXISTS content   Nullable(String) DEFAULT NULL"
)

_DDL_USAGE_EVENTS = """
CREATE TABLE IF NOT EXISTS {db}.usage_events
(
    event_id         String,
    tenant_id        String,
    session_id       String,
    dimension        String,
    quantity         Int64,
    source_component String,
    timestamp        DateTime64(3, 'UTC'),
    date             Date
)
ENGINE = ReplacingMergeTree()
PARTITION BY toYYYYMM(date)
ORDER BY (tenant_id, event_id)
"""

_DDL_SENTIMENT_EVENTS = """
CREATE TABLE IF NOT EXISTS {db}.sentiment_events
(
    event_id   String,
    tenant_id  String,
    session_id String,
    pool_id    String,
    score      Float32,
    category   String,
    segment_id Nullable(String),
    timestamp  DateTime64(3, 'UTC'),
    date       Date
)
ENGINE = ReplacingMergeTree()
PARTITION BY toYYYYMM(date)
ORDER BY (tenant_id, session_id, timestamp)
"""

# Forward-compatible migration: add segment_id to pre-existing sentiment_events tables.
_DDL_SENTIMENT_EVENTS_MIGRATE_SEGMENT = (
    "ALTER TABLE {db}.sentiment_events"
    " ADD COLUMN IF NOT EXISTS segment_id Nullable(String) DEFAULT NULL"
)

_DDL_WORKFLOW_EVENTS = """
CREATE TABLE IF NOT EXISTS {db}.workflow_events
(
    event_id        String,
    tenant_id       String,
    instance_id     String,
    flow_id         String,
    pool_id         Nullable(String),
    campaign_id     Nullable(String),
    event_type      String,
    status          Nullable(String),
    current_step    Nullable(String),
    suspend_reason  Nullable(String),
    decision        Nullable(String),
    outcome         Nullable(String),
    duration_ms     Nullable(Int64),
    wait_duration_ms Nullable(Int64),
    error           Nullable(String),
    timestamp       DateTime64(3, 'UTC'),
    date            Date
)
ENGINE = ReplacingMergeTree()
PARTITION BY toYYYYMM(date)
ORDER BY (tenant_id, instance_id, timestamp)
"""

_ALTER_WORKFLOW_EVENTS_POOL_ID = (
    "ALTER TABLE {db}.workflow_events"
    " ADD COLUMN IF NOT EXISTS pool_id Nullable(String) DEFAULT NULL"
)

# Arc 19: session status column — tracks 'active', 'suspended', 'closed'.
# Written by session_suspended / session_resumed stream events consumed by analytics-api.
# NULL for pre-Arc-19 sessions (treated as 'closed' at query time).
_DDL_SESSIONS_MIGRATE_STATUS = (
    "ALTER TABLE {db}.sessions"
    " ADD COLUMN IF NOT EXISTS status Nullable(String) DEFAULT NULL"
)

# Arc 19: origin_session_id — links a webhook workflow session back to the
# intake session (webchat/voice/etc.) that triggered it via workflow_trigger.
# Populated by parse_inbound from the conversations.inbound event field.
# Used by the workflow-trace endpoint to fetch the input agent segment.
_DDL_SESSIONS_MIGRATE_ORIGIN = (
    "ALTER TABLE {db}.sessions"
    " ADD COLUMN IF NOT EXISTS origin_session_id Nullable(String) DEFAULT NULL"
)

# Journey T4: spawn_reason — o RÓTULO da aresta de proveniência: *por que* esta sessão
# existe. `origin_session_id` (T1) diz QUEM me criou; este diz COMO/POR QUÊ.
#
# É o que torna a árvore LEGÍVEL: sem o rótulo, o operador vê a hierarquia mas não sabe
# por que cada filho está ali. Com ele, a cadeia conta a história numa olhada:
#   processo —trigger→ workflow de survey —collect→ contato de survey
#
# Valores: trigger (workflow_trigger) | delegate | collect | NULL (sessão de topo:
# iniciada pelo cliente — ninguém a "criou", ela é a raiz da árvore).
_DDL_SESSIONS_MIGRATE_SPAWN = (
    "ALTER TABLE {db}.sessions"
    " ADD COLUMN IF NOT EXISTS spawn_reason Nullable(String) DEFAULT NULL"
)

# Journey J1: root_session_id — raiz TRANSITIVA da árvore de proveniência (agrupa
# N sessões de um mesmo processo). Distinto de origin_session_id (1 salto). Nunca
# null: DEFAULT session_id garante que legado e sessões-raiz apontem para si mesmas;
# sessões-filha carregam a raiz propagada do chamador (webhook.py/bridge/engine).
# journey_id = CACHE eventualmente consistente da raiz canônica (= root no nascimento;
# refrescado no merge em J3). NUNCA é fonte de verdade — reads resolvem por union-find.
_DDL_SESSIONS_MIGRATE_ROOT = (
    "ALTER TABLE {db}.sessions"
    " ADD COLUMN IF NOT EXISTS root_session_id String DEFAULT session_id"
)
_DDL_SESSIONS_MIGRATE_JOURNEY = (
    "ALTER TABLE {db}.sessions"
    " ADD COLUMN IF NOT EXISTS journey_id String DEFAULT session_id"
)

# Quality substrate isolation (ADR adr-quality-substrate-isolation) — passo 1.
# `origin` = procedência do contato no substrato de avaliação: live | import | reeval.
# Aditivo, default 'live' → cobre legado e tráfego vivo sem backfill. Distinto de
# `origin_session_id` (Arc 19, link webhook→intake). Derivado do `source` do evento
# e persistido pelo consumer (passo 2); o filtro default `origin='live'` (passo 4)
# é a garantia de correção dos relatórios de produção.
_DDL_SESSIONS_MIGRATE_ORIGIN_CLASS = (
    "ALTER TABLE {db}.sessions"
    " ADD COLUMN IF NOT EXISTS origin String DEFAULT 'live'"
)
_DDL_SEGMENTS_MIGRATE_ORIGIN_CLASS = (
    "ALTER TABLE {db}.segments"
    " ADD COLUMN IF NOT EXISTS origin String DEFAULT 'live'"
)
_DDL_MESSAGES_MIGRATE_ORIGIN_CLASS = (
    "ALTER TABLE {db}.messages"
    " ADD COLUMN IF NOT EXISTS origin String DEFAULT 'live'"
)

_DDL_COLLECT_EVENTS = """
CREATE TABLE IF NOT EXISTS {db}.collect_events
(
    collect_token  String,
    tenant_id      String,
    instance_id    String,
    flow_id        String,
    campaign_id    Nullable(String),
    step_id        String,
    target_type    String,
    channel        String,
    interaction    String,
    status         String,
    send_at        Nullable(DateTime64(3, 'UTC')),
    responded_at   Nullable(DateTime64(3, 'UTC')),
    elapsed_ms     Nullable(Int64),
    timestamp      DateTime64(3, 'UTC'),
    date           Date
)
ENGINE = ReplacingMergeTree()
PARTITION BY toYYYYMM(date)
ORDER BY (tenant_id, collect_token)
"""

# participation_intervals: one row per participant per session interval.
# ReplacingMergeTree() — no version column (Nullable(DateTime64) is not valid as version).
# The "left" event is always inserted after "joined" (Kafka ordering), so last-write-wins
# keeps the row with left_at set. ORDER BY (tenant_id, session_id, participant_id).
_DDL_PARTICIPATION_INTERVALS = """
CREATE TABLE IF NOT EXISTS {db}.participation_intervals
(
    event_id       String,
    session_id     String,
    tenant_id      String,
    participant_id String,
    pool_id        String,
    agent_type_id  String,
    role           String,
    agent_type     String,
    conference_id  Nullable(String),
    joined_at      Nullable(DateTime64(3, 'UTC')),
    left_at        Nullable(DateTime64(3, 'UTC')),
    duration_ms    Nullable(Int64),
    date           Date
)
ENGINE = ReplacingMergeTree()
PARTITION BY toYYYYMM(date)
ORDER BY (tenant_id, session_id, participant_id)
"""

# ── Arc 5: segments — one row per ContactSegment (joined+left merged via ReplacingMergeTree)
# ORDER BY (tenant_id, session_id, segment_id) — segment_id is the primary key.
# participant_joined writes with ended_at=NULL; participant_left rewrites with ended_at set.
# ReplacingMergeTree(ingested_at) ensures the latest write wins on background merge.
_DDL_SEGMENTS = """
CREATE TABLE IF NOT EXISTS {db}.segments
(
    segment_id         String,
    session_id         String,
    tenant_id          String,
    participant_id     String,
    pool_id            String,
    agent_type_id      String,
    flow_id            String DEFAULT '',
    deploy_version     String DEFAULT '',
    channel            String DEFAULT '',
    user_id            String DEFAULT '',
    user_login         String DEFAULT '',
    instance_id        String,
    role               String,
    agent_type         String,
    parent_segment_id  Nullable(String),
    sequence_index     Int32,
    started_at         DateTime64(3, 'UTC'),
    ended_at           Nullable(DateTime64(3, 'UTC')),
    duration_ms        Nullable(Int64),
    outcome            Nullable(String),
    close_reason       Nullable(String),
    handoff_reason     Nullable(String),
    issue_status       Nullable(String),
    escalation_reason  Nullable(String),
    wrapup_summary     Nullable(String),
    wrapup_next_steps  Nullable(String),
    conference_id      Nullable(String),
    ingested_at        DateTime DEFAULT now(),
    date               Date
)
ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(date)
ORDER BY (tenant_id, session_id, segment_id)
"""

# Relatórios: flow_id (skill-flow deployado que o agente executou) por segmento.
# Identidade correta de avaliação para IA (agent_type_id é deprecated).
_DDL_SEGMENTS_MIGRATE_FLOW = (
    "ALTER TABLE {db}.segments ADD COLUMN IF NOT EXISTS flow_id String DEFAULT ''"
)

# R9: deploy_version (versão do skill que rodou, AI) + channel da sessão, carimbados
# no segmento. Insumo da cota por versão (amostragem), do núcleo epoch (Arc 6 Fase 2)
# e do condicionamento por canal no backfill.
_DDL_SEGMENTS_MIGRATE_DEPLOY_VERSION = (
    "ALTER TABLE {db}.segments ADD COLUMN IF NOT EXISTS deploy_version String DEFAULT ''"
)
_DDL_SEGMENTS_MIGRATE_CHANNEL = (
    "ALTER TABLE {db}.segments ADD COLUMN IF NOT EXISTS channel String DEFAULT ''"
)

# Relatórios (C1): identidade do agente humano por user_id (login). Para humanos
# agent_type_id é o placeholder sintético human_agent_{pool}; user_id é a identidade
# real. IA segue por flow_id; user_id fica '' para IA.
_DDL_SEGMENTS_MIGRATE_USER = (
    "ALTER TABLE {db}.segments ADD COLUMN IF NOT EXISTS user_id String DEFAULT ''"
)

# Relatórios (C1): login (email) do agente humano, denormalizado para exibição
# ("quem atendeu" legível, em vez do UUID user_id ou do placeholder agent_type_id).
_DDL_SEGMENTS_MIGRATE_USER_LOGIN = (
    "ALTER TABLE {db}.segments ADD COLUMN IF NOT EXISTS user_login String DEFAULT ''"
)

# F5 → item 5: a coluna segments.nps_score foi APOSENTADA. NPS de segmento vive em
# session_signal (grain=segment, metric=nps), gravado via survey_record (cutover F10.3b).
# Migração de DROP idempotente — remove a coluna vestigial em instalações existentes.
_DDL_SEGMENTS_DROP_NPS = (
    "ALTER TABLE {db}.segments DROP COLUMN IF EXISTS nps_score"
)

# F7: escalation_reason normalizado por segmento (id do config escalation_reasons).
# Escrito pelo bridge quando outcome é da família escalate (humano via wrap-up, IA via
# escalate step). Nullable: só segmentos escalados têm. handoff_reason segue como nota livre.
_DDL_SEGMENTS_MIGRATE_ESCALATION = (
    "ALTER TABLE {db}.segments ADD COLUMN IF NOT EXISTS escalation_reason Nullable(String)"
)

# Prosa do wrap-up (fix 2026-07-30). O formulário sempre pergunta "resumo" e
# "próximos passos", mas eles só eram gravados quando `outcome != 'resolved'` — no
# caso MAIS COMUM (resolvido) o texto que o atendente digitou não ia a lugar nenhum,
# sem sinal nenhum na tela.
#
# Colunas PRÓPRIAS, e não `handoff_reason`, por dois motivos:
#   1. `handoff_rate` é definido como `countIf(handoff_reason != '') / count()` —
#      escrever o resumo ali levaria a taxa de repasse a ~100%, trocando uma perda
#      silenciosa por uma métrica que muda de significado sem avisar;
#   2. mesmo precedente do `escalation_reason`, que foi extraído desta mesma nota
#      livre quando ganhou significado próprio.
# Prosa não cabe em `agent_business_events` (Arc 12): lá `value` é numérico e o
# nominal vive na CATEGORIA — texto livre não é nem um nem outro.
_DDL_SEGMENTS_MIGRATE_WRAPUP_SUMMARY = (
    "ALTER TABLE {db}.segments ADD COLUMN IF NOT EXISTS wrapup_summary Nullable(String)"
)
_DDL_SEGMENTS_MIGRATE_WRAPUP_NEXT_STEPS = (
    "ALTER TABLE {db}.segments ADD COLUMN IF NOT EXISTS wrapup_next_steps Nullable(String)"
)

# ── Arc 5: session_timeline — time-series events tied to segments.
# Populated by multiple Kafka topics; segment_id enriched post-hoc via timestamp overlap.
# ReplacingMergeTree(ingested_at) for idempotent re-processing.
_DDL_SESSION_TIMELINE = """
CREATE TABLE IF NOT EXISTS {db}.session_timeline
(
    event_id    String,
    tenant_id   String,
    session_id  String,
    segment_id  String,
    event_type  String,
    actor_id    String,
    actor_role  String,
    payload     String,
    timestamp   DateTime64(3, 'UTC'),
    ingested_at DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(timestamp)
ORDER BY (tenant_id, session_id, timestamp, event_id)
"""

# ── Arc 6: evaluation_results — one row per EvaluationResult (submitted + review updates).
# ReplacingMergeTree(ingested_at) ensures the latest review decision wins.
# ORDER BY (tenant_id, result_id) — primary key for point lookups.
_DDL_EVALUATION_RESULTS = """
CREATE TABLE IF NOT EXISTS {db}.evaluation_results
(
    result_id          String,
    instance_id        String,
    session_id         String,
    tenant_id          String,
    evaluator_id       String,
    form_id            String,
    campaign_id        Nullable(String),
    overall_score      Float32,
    eval_status        String,
    locked             UInt8,
    compliance_flags   Array(String),
    ingested_at        DateTime DEFAULT now(),
    timestamp          DateTime64(3, 'UTC'),
    date               Date
)
ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(date)
ORDER BY (tenant_id, result_id)
"""

# ── Arc 6: evaluation_events — general evaluation lifecycle events from evaluation.events Kafka topic.
# Covers: evaluation.submitted, evaluation.reviewed, evaluation.contested, evaluation.locked.
# ReplacingMergeTree() for idempotency (event_id is unique per event type).
_DDL_EVALUATION_EVENTS = """
CREATE TABLE IF NOT EXISTS {db}.evaluation_events
(
    event_id      String,
    tenant_id     String,
    result_id     String,
    instance_id   String,
    session_id    String,
    campaign_id   Nullable(String),
    event_type    String,
    eval_status   Nullable(String),
    overall_score Nullable(Float32),
    actor_id      Nullable(String),
    ingested_at   DateTime DEFAULT now(),
    timestamp     DateTime64(3, 'UTC'),
    date          Date
)
ENGINE = ReplacingMergeTree()
PARTITION BY toYYYYMM(date)
ORDER BY (tenant_id, event_id)
"""

# ── F8 (bancada): evaluation_dimension_scores — uma linha por (result_id, dimension_id).
# Decompõe o overall_score em dimensões do EvaluationForm. A atribuição ao agente
# AVALIADO é feita em query-time via session_id (join em segments), como a lente
# quality (F2). ReplacingMergeTree(ingested_at): revisão que reescreve o resultado vence.
_DDL_EVALUATION_DIMENSION_SCORES = """
CREATE TABLE IF NOT EXISTS {db}.evaluation_dimension_scores
(
    result_id       String,
    instance_id     String,
    session_id      String,
    tenant_id       String,
    evaluator_id    String,
    form_id         String,
    campaign_id     Nullable(String),
    dimension_id    String,
    dimension_name  String,
    score           Float32,
    weight          Float32,
    eval_status     String,
    ingested_at     DateTime DEFAULT now(),
    timestamp       DateTime64(3, 'UTC'),
    date            Date
)
ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(date)
ORDER BY (tenant_id, result_id, dimension_id)
"""

# ── T11: evaluation_finalized — uma linha por avaliação FINALIZADA (invariante de
# qualidade, spec §1/§17.3). Alimentada pelo evento `evaluation_finalized` (único emissor:
# finalize_evaluation). Keyed por instance_id (presente no completed E no finalized; o
# result_id do completed no demo = evaluation_id, então instance_id é a chave estável).
# É a fonte do modo OFICIAL dos relatórios; fatiável por finalize_reason/segment_id/form_version.
_DDL_EVALUATION_FINALIZED = """
CREATE TABLE IF NOT EXISTS {db}.evaluation_finalized
(
    instance_id           String,
    result_id             String,
    session_id            String,
    tenant_id             String,
    campaign_id           Nullable(String),
    final_score           Float32,
    finalize_reason       String,
    contestation_state    String,
    evaluated_agent_type  String,
    segment_id            String,
    form_version          Int32,
    round                 Int32,
    process_duration_ms   Int64,
    ingested_at           DateTime DEFAULT now(),
    timestamp             DateTime64(3, 'UTC'),
    date                  Date
)
ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(date)
ORDER BY (tenant_id, instance_id)
"""

# ── contact_insights — business events published via insight_register MCP tool.
# Each row represents a domain event emitted by an agent flow (e.g. "cancelamento",
# "erro_consulta_saldo"). Consumed from conversations.events Kafka topic where
# event_type starts with "insight.".
# ReplacingMergeTree for at-least-once idempotency.
_DDL_CONTACT_INSIGHTS = """
CREATE TABLE IF NOT EXISTS {db}.contact_insights
(
    insight_id    String,
    tenant_id     String,
    session_id    String,
    insight_type  String,
    category      String,
    value         String,
    tags          Array(String),
    agent_id      Nullable(String),
    timestamp     DateTime64(3, 'UTC'),
    date          Date
)
ENGINE = ReplacingMergeTree()
PARTITION BY toYYYYMM(date)
ORDER BY (tenant_id, insight_id)
"""

# journey_events (Arc 10) — REMOVED (Arc 19 Fase F)
# Journey entity superseded by Arc 19 unified session model.
# See CHANGELOG.md for history (Arcs 10, 16, 17).

# ── Arc 13: calibration_events — CurationReview outcomes from calibration.events Kafka topic.
# Each row corresponds to one curator decision (approved / recalibrated / bias_flagged).
# ReplacingMergeTree on (tenant_id, event_id) for at-least-once idempotency.
_DDL_CALIBRATION_EVENTS = """
CREATE TABLE IF NOT EXISTS {db}.calibration_events
(
    event_id      String,
    tenant_id     String,
    campaign_id   String,
    evaluator_id  String,
    skill_version String,
    decision      LowCardinality(String),
    dimension_id  String,
    severity      LowCardinality(String),
    curator_id    Nullable(String),
    note_id       Nullable(String),
    event_time    DateTime64(3, 'UTC'),
    date          Date
)
ENGINE = ReplacingMergeTree()
PARTITION BY toYYYYMM(date)
ORDER BY (tenant_id, event_id)
"""

# ── Arc 12: agent_business_events — time-series KPIs emitted by agents via agent_event MCP tool.
# category_l1..l4 are pre-decomposed from category at publish time for fast ClickHouse queries.
# MergeTree (not Replacing) because events are immutable — no deduplication needed.
# TTL 2 years; ORDER BY puts time last so range scans on category are efficient.
_DDL_AGENT_BUSINESS_EVENTS = """
CREATE TABLE IF NOT EXISTS {db}.agent_business_events
(
    event_id       String,
    tenant_id      String,
    session_id     String,
    journey_id     Nullable(String),
    agent_type_id  String,
    skill_id       String,
    pool_id        String,
    category       String,
    category_l1    String,
    category_l2    String,
    category_l3    String,
    category_l4    String,
    value          Float64,
    tags           Map(String, String),
    emitted_at     DateTime64(3, 'UTC'),
    date           Date
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(date)
ORDER BY (tenant_id, category_l1, category_l2, category_l3, emitted_at)
TTL toDateTime(emitted_at) + INTERVAL 2 YEAR
"""

# ── F10 (bancada): session_signal — voz do cliente/agente, store ÚNICO de sinais.
# Cobre TODOS os grãos (segment|session|workflow|journey), gravados explicitamente
# via a tool MCP survey_record (um invoke no skill-flow de pesquisa) — sem mecanismo
# de eventos/derivação. grain = O QUE a pesquisa cobre: segment (1 agente; carrega
# segment_id + agent_key p/ atribuição), session (a sessão inteira), workflow (uma
# execução de workflow), journey (relacionamento multi-sessão). Survey OUTBOUND
# religa à sessão original por origin_session_id. Timing (no ato × diferido) =
# captured_at × session_at, não um grão. segments.nps_score (F5) foi DROPADA (item 5):
# NPS de segmento vive só aqui agora. Ingest: survey_record → Kafka session.signals →
# parse_session_signal_event. Bucketização sempre por session_at (regra de ouro §7).
# ReplacingMergeTree dedup por (tenant, session, grain, segment_id, metric) —
# segment_id na chave evita colidir N segmentos da mesma sessão no grão segment.
_DDL_SESSION_SIGNAL = """
CREATE TABLE IF NOT EXISTS {db}.session_signal
(
    signal_id          String,
    tenant_id          String,
    session_id         String,
    grain              String,
    segment_id         String DEFAULT '',
    agent_key          String DEFAULT '',
    pool_id            String DEFAULT '',
    source             String,
    metric             String,
    value_num          Nullable(Float64),
    value_label        Nullable(String),
    scale_min          Nullable(Float64),
    scale_max          Nullable(Float64),
    session_at         DateTime64(3, 'UTC'),
    captured_at        DateTime64(3, 'UTC'),
    origin_session_id  Nullable(String),
    journey_id         Nullable(String),
    date               Date
)
ENGINE = ReplacingMergeTree()
PARTITION BY toYYYYMM(date)
ORDER BY (tenant_id, session_id, grain, segment_id, metric)
TTL toDateTime(session_at) + INTERVAL 2 YEAR
"""

# Customer Voice (Fatia 1): escala IMUTÁVEL do instrumento carimbada no sinal (da
# DialogDimension no momento da resposta). Habilita roll-ups dependentes de escala
# (top-box) sem reler o form (editável). NULL = sinal sem escala (legado/sem dimensão).
_DDL_SESSION_SIGNAL_MIGRATE_SCALE = (
    "ALTER TABLE {db}.session_signal"
    " ADD COLUMN IF NOT EXISTS scale_min Nullable(Float64) DEFAULT NULL,"
    " ADD COLUMN IF NOT EXISTS scale_max Nullable(Float64) DEFAULT NULL"
)

# ── Arc 8: agent_pause_intervals — one row per pause interval per human agent.
# ReplacingMergeTree on (tenant_id, instance_id, paused_at): the close row
# (with resumed_at + duration_ms) wins over the open row on background merge
# because ingested_at is later.
_DDL_AGENT_PAUSE_INTERVALS = """
CREATE TABLE IF NOT EXISTS {db}.agent_pause_intervals
(
    interval_id    String,
    tenant_id      String,
    instance_id    String,
    agent_type_id  String,
    pool_id        String,
    reason_id      String,
    reason_label   String,
    note           Nullable(String),
    paused_at      DateTime64(3, 'UTC'),
    resumed_at     Nullable(DateTime64(3, 'UTC')),
    duration_ms    Nullable(Int64),
    ingested_at    DateTime DEFAULT now(),
    date           Date
)
ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(date)
ORDER BY (tenant_id, instance_id, paused_at)
"""

# ── Fase 1b: agent_login_intervals — one row per logged-in interval per agent.
# Same open/close pattern as agent_pause_intervals: the open row (logged_out_at
# NULL) is written on the first agent_ready/agent_login; the close row (with
# logged_out_at + duration_ms) wins on merge via the later ingested_at.
# Carries user_id/user_login so the availability report groups humans by identity
# (consistent with C1/C1b) — empty for native AI agents.
_DDL_AGENT_LOGIN_INTERVALS = """
CREATE TABLE IF NOT EXISTS {db}.agent_login_intervals
(
    interval_id    String,
    tenant_id      String,
    instance_id    String,
    user_id        String,
    user_login     String,
    agent_type_id  String,
    pool_id        String,
    logged_in_at   DateTime64(3, 'UTC'),
    logged_out_at  Nullable(DateTime64(3, 'UTC')),
    duration_ms    Nullable(Int64),
    ingested_at    DateTime DEFAULT now(),
    date           Date
)
ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(date)
ORDER BY (tenant_id, instance_id, logged_in_at)
"""

# ── Timeline: agent_pool_intervals — one row per (agent, pool) presence interval.
# A human is logged in once (agent_login_intervals) but may serve several pools;
# this table records when they entered/left EACH pool, so the timeline can draw a
# lane per pool aligned to the agent's total lane. login_interval_id links each
# pool presence to its parent login interval. Pauses are agent-level and overlaid
# from agent_pause_intervals (a pause removes the agent from all pools at once).
_DDL_AGENT_POOL_INTERVALS = """
CREATE TABLE IF NOT EXISTS {db}.agent_pool_intervals
(
    interval_id       String,
    login_interval_id String,
    tenant_id         String,
    instance_id       String,
    user_id           String,
    user_login        String,
    agent_type_id     String,
    pool_id           String,
    entered_at        DateTime64(3, 'UTC'),
    left_at           Nullable(DateTime64(3, 'UTC')),
    duration_ms       Nullable(Int64),
    ingested_at       DateTime DEFAULT now(),
    date              Date
)
ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(date)
ORDER BY (tenant_id, instance_id, pool_id, entered_at)
"""

# ── Fase 2: pool_occupancy_peaks — pico de concorrência por minuto por pool.
# Producer: Routing Engine occupancy sampler → Kafka pool.occupancy. ReplacingMerge
# Tree em (tenant, pool, minute) deduplica flushes de múltiplas instâncias de routing.
# pool_id = '__total__' carrega o pico instantâneo do tenant (≠ soma dos picos por pool).
_DDL_POOL_OCCUPANCY_PEAKS = """
CREATE TABLE IF NOT EXISTS {db}.pool_occupancy_peaks
(
    tenant_id            String,
    pool_id              String,
    minute               DateTime64(3, 'UTC'),
    peak_concurrency     Int32,
    provisioned_capacity Int32,
    admitted_peak        Int32 DEFAULT 0,
    ingested_at          DateTime DEFAULT now(),
    date                 Date
)
ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(date)
ORDER BY (tenant_id, pool_id, minute)
"""

# Item 7b — migração de bases existentes (ADD COLUMN é idempotente no ClickHouse).
# admitted_peak = sessões debitando C atribuídas ao pool no minuto (reserva +
# atribuição do shared via HASH do 7a). Linhas agregadas novas no mesmo padrão
# do __total__: __reserved__ / __shared__ / __buffer__ (peak vs capacity=limite).
_ALTER_POOL_OCCUPANCY_ADMITTED = """
ALTER TABLE {db}.pool_occupancy_peaks
    ADD COLUMN IF NOT EXISTS admitted_peak Int32 DEFAULT 0 AFTER provisioned_capacity
"""

# ── Arc 5: mv_agent_performance_daily — AggregatingMergeTree MV over segments.
# Captures a row per (tenant_id, agent_type_id, pool_id, period_date) on every INSERT.
# Uses State/Merge aggregating functions so partial results compose correctly.
# POPULATE backfills existing segments rows on first creation.
_DDL_MV_AGENT_PERFORMANCE = """
CREATE MATERIALIZED VIEW IF NOT EXISTS {db}.mv_agent_performance_daily
ENGINE = AggregatingMergeTree()
PARTITION BY toYYYYMM(period_date)
ORDER BY (tenant_id, agent_type_id, pool_id, period_date)
POPULATE
AS SELECT
    tenant_id,
    agent_type_id,
    pool_id,
    toDate(started_at)                                    AS period_date,
    countState()                                          AS total_sessions_state,
    avgState(assumeNotNull(duration_ms))                  AS avg_duration_ms_state,
    countIfState(outcome = 'resolved')                    AS resolved_count_state,
    countIfState(outcome = 'escalated')                   AS escalated_count_state,
    countIfState(outcome = 'transferred')                 AS transferred_count_state,
    countIfState(agent_type = 'human')                    AS human_sessions_state
FROM {db}.segments
WHERE ended_at IS NOT NULL
GROUP BY tenant_id, agent_type_id, pool_id, toDate(started_at)
"""

# Readable SQL view over mv_agent_performance_daily.
# resolution_rate and escalation_rate are ratios computed with Merge aggregators.
# Use greatest(..., 1) to avoid division by zero on empty buckets.
_DDL_V_AGENT_PERFORMANCE = """
CREATE VIEW IF NOT EXISTS {db}.v_agent_performance AS
SELECT
    tenant_id,
    agent_type_id,
    pool_id,
    period_date,
    countMerge(total_sessions_state)                                              AS total_sessions,
    round(avgMerge(avg_duration_ms_state), 0)                                     AS avg_duration_ms,
    countIfMerge(resolved_count_state)
        / greatest(countMerge(total_sessions_state), 1)                           AS resolution_rate,
    countIfMerge(escalated_count_state)
        / greatest(countMerge(total_sessions_state), 1)                           AS escalation_rate,
    countIfMerge(transferred_count_state)
        / greatest(countMerge(total_sessions_state), 1)                           AS transfer_rate,
    countIfMerge(human_sessions_state)
        / greatest(countMerge(total_sessions_state), 1)                           AS human_rate
FROM {db}.mv_agent_performance_daily
GROUP BY tenant_id, agent_type_id, pool_id, period_date
"""

# ── Arc 5: mv_segment_summary — AggregatingMergeTree MV over segments per session.
# Captures a row per (tenant_id, session_id) on every INSERT into segments.
# handoff_count = max(sequence_index) = number of primary-agent hand-offs in the session.
_DDL_MV_SEGMENT_SUMMARY = """
CREATE MATERIALIZED VIEW IF NOT EXISTS {db}.mv_segment_summary
ENGINE = AggregatingMergeTree()
ORDER BY (tenant_id, session_id)
POPULATE
AS SELECT
    tenant_id,
    session_id,
    countState()                                    AS segment_count_state,
    countIfState(role = 'primary')                  AS primary_count_state,
    countIfState(role = 'specialist')               AS specialist_count_state,
    countIfState(agent_type = 'human')              AS human_count_state,
    sumState(assumeNotNull(duration_ms))            AS total_duration_ms_state,
    maxState(toInt64(sequence_index))               AS max_sequence_state,
    countIfState(outcome = 'escalated')             AS escalation_count_state,
    countIfState(outcome = 'resolved')              AS resolved_count_state
FROM {db}.segments
GROUP BY tenant_id, session_id
"""

# Readable SQL view over mv_segment_summary.
# handoff_count = max sequence_index observed (0 = single agent, 1 = one hand-off, etc.).
_DDL_V_SEGMENT_SUMMARY = """
CREATE OR REPLACE VIEW {db}.v_segment_summary AS
SELECT
    tenant_id,
    session_id,
    countMerge(segment_count_state)         AS segment_count,
    countIfMerge(primary_count_state)       AS primary_segments,
    countIfMerge(specialist_count_state)    AS specialist_segments,
    countIfMerge(human_count_state)         AS human_segments,
    sumMerge(total_duration_ms_state)       AS total_duration_ms,
    maxMerge(max_sequence_state)            AS handoff_count,
    countIfMerge(escalation_count_state)    AS escalation_count,
    countIfMerge(resolved_count_state)      AS resolved_count
FROM {db}.mv_segment_summary
GROUP BY tenant_id, session_id
"""

# Journey J3 — journey_aliases: arestas de merge (source_root NOVO → canonical_root
# ANTIGO). Fonte de verdade das uniões; a resolução canônica (union-find) roda no
# read layer da analytics-api (Python), não em CTE recursiva no ClickHouse.
# `active=0` = merge revertido. ReplacingMergeTree por (tenant, source_root) — uma
# aresta por raiz-fonte (o último merge vence).
_DDL_JOURNEY_ALIASES = """
CREATE TABLE IF NOT EXISTS {db}.journey_aliases
(
    tenant_id       String,
    source_root     String,
    canonical_root  String,
    merged_at       DateTime64(3, 'UTC'),
    actor           String DEFAULT '',
    active          UInt8 DEFAULT 1,
    _ingested_at    DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(_ingested_at)
PARTITION BY toYYYYMM(merged_at)
ORDER BY (tenant_id, source_root)
"""

_ALL_DDL = [
    _DDL_DATABASE,
    _DDL_SESSIONS,
    _DDL_JOURNEY_ALIASES,
    _DDL_QUEUE_EVENTS,
    _DDL_MESSAGES,
    _DDL_USAGE_EVENTS,
    _DDL_SENTIMENT_EVENTS,
    _DDL_WORKFLOW_EVENTS,
    _DDL_COLLECT_EVENTS,
    _DDL_PARTICIPATION_INTERVALS,
    _DDL_SEGMENTS,
    _DDL_SESSION_TIMELINE,
    _DDL_EVALUATION_RESULTS,
    _DDL_EVALUATION_EVENTS,
    _DDL_EVALUATION_DIMENSION_SCORES,
    _DDL_EVALUATION_FINALIZED,
    _DDL_CONTACT_INSIGHTS,
    _DDL_AGENT_PAUSE_INTERVALS,
    _DDL_AGENT_LOGIN_INTERVALS,
    _DDL_AGENT_POOL_INTERVALS,
    _DDL_POOL_OCCUPANCY_PEAKS,
    _ALTER_POOL_OCCUPANCY_ADMITTED,   # item 7b — migração idempotente
    _DDL_AGENT_BUSINESS_EVENTS,
    _DDL_SESSION_SIGNAL,
    _DDL_CALIBRATION_EVENTS,
    # Materialized views — must come AFTER the source tables they reference.
    # AggregatingMergeTree with POPULATE backfills existing data on first creation.
    _DDL_MV_AGENT_PERFORMANCE,
    _DDL_V_AGENT_PERFORMANCE,
    _DDL_MV_SEGMENT_SUMMARY,
    _DDL_V_SEGMENT_SUMMARY,
]

# Migrations applied after CREATE IF NOT EXISTS (idempotent ALTER TABLE statements).
_MIGRATIONS = [
    _DDL_SESSIONS_MIGRATE,
    _DDL_SESSIONS_MIGRATE_ANI_DNIS,
    _DDL_SESSIONS_MIGRATE_SLA,
    _DDL_SENTIMENT_EVENTS_MIGRATE_SEGMENT,
    _DDL_MESSAGES_MIGRATE_CONTENT,
    _ALTER_WORKFLOW_EVENTS_POOL_ID,       # Add pool_id to workflow_events
    _DDL_SESSIONS_MIGRATE_STATUS,         # Arc 19: session status (active|suspended|closed)
    _DDL_SESSIONS_MIGRATE_ORIGIN,         # Arc 19: origin_session_id (webhook → intake link)
    _DDL_SESSIONS_MIGRATE_SPAWN,          # Journey T4: spawn_reason (rótulo da aresta)
    _DDL_SESSIONS_MIGRATE_ROOT,           # Journey J1: root_session_id (raiz transitiva da proveniência)
    _DDL_SESSIONS_MIGRATE_JOURNEY,        # Journey J1: journey_id (cache = root no nascimento)
    _DDL_SEGMENTS_MIGRATE_FLOW,           # Relatórios: flow_id (skill deployado) por segmento
    _DDL_SEGMENTS_MIGRATE_DEPLOY_VERSION, # R9: deploy_version (versão do skill, AI) por segmento
    _DDL_SEGMENTS_MIGRATE_CHANNEL,        # R9: channel da sessão por segmento
    _DDL_SEGMENTS_MIGRATE_USER,           # C1: user_id (login) — identidade do agente humano
    _DDL_SEGMENTS_MIGRATE_USER_LOGIN,     # C1: user_login (email) — exibição legível
    _DDL_SEGMENTS_DROP_NPS,               # item 5: DROP nps_score (vestigial → session_signal)
    _DDL_SEGMENTS_MIGRATE_ESCALATION,     # F7: escalation_reason normalizado por segmento
    _DDL_SEGMENTS_MIGRATE_WRAPUP_SUMMARY,    # prosa do wrap-up (antes descartada quando resolved)
    _DDL_SEGMENTS_MIGRATE_WRAPUP_NEXT_STEPS,
    # Quality substrate isolation (ADR) — passo 1: origin (live|import|reeval) no substrato.
    _DDL_SESSIONS_MIGRATE_ORIGIN_CLASS,
    _DDL_SEGMENTS_MIGRATE_ORIGIN_CLASS,
    _DDL_MESSAGES_MIGRATE_ORIGIN_CLASS,
    _DDL_SESSION_SIGNAL_MIGRATE_SCALE,    # Customer Voice: escala carimbada no sinal (top-box)
    _DDL_AGENT_EVENTS_DROP,               # fatia 2: DROP agent_events (substrato derivado → segments)
]


# ─── Client wrapper ───────────────────────────────────────────────────────────

class AnalyticsStore:
    """
    Wraps a synchronous clickhouse_connect client.
    All insert methods run via asyncio.to_thread() to avoid blocking the event loop.

    Thread-safety note: clickhouse_connect clients must not be shared across concurrent
    threads (raises "Attempt to execute concurrent queries within the same session").
    Use ``new_client()`` whenever you need a client inside asyncio.to_thread() — it
    creates a fresh, independent connection each time.  ``self._client`` is kept only
    for DDL (ensure_schema) and insert operations which are naturally serialised.
    """

    def __init__(
        self,
        host:     str,
        port:     int,
        user:     str,
        password: str,
        database: str,
    ) -> None:
        self._conn_params = dict(host=host, port=port, username=user, password=password)
        self._client   = clickhouse_connect.get_client(**self._conn_params)
        self._database = database

    def new_client(self) -> Any:
        """Return a fresh ClickHouse client.

        Call this inside every asyncio.to_thread() invocation so that concurrent
        requests never share the same underlying session.
        """
        return clickhouse_connect.get_client(**self._conn_params)

    # ── Schema ────────────────────────────────────────────────────────────────

    def ensure_schema(self) -> None:
        """Creates the database, all tables, and materialized views if they don't exist. Idempotent."""
        # Base tables: execute strictly — errors are real problems.
        base_ddl = [d for d in _ALL_DDL if "MATERIALIZED VIEW" not in d and "CREATE VIEW" not in d]
        for ddl in base_ddl:
            stmt = ddl.format(db=self._database)
            self._client.command(stmt)
        # Materialized views and readable views: wrap in try/except because POPULATE
        # can raise on ClickHouse versions that don't support IF NOT EXISTS + POPULATE
        # atomically. On re-runs the view already exists and the error is harmless.
        view_ddl = [d for d in _ALL_DDL if "MATERIALIZED VIEW" in d or "CREATE VIEW" in d]
        for ddl in view_ddl:
            try:
                self._client.command(ddl.format(db=self._database))
            except Exception as exc:
                logger.warning("View DDL skipped (already exists?): %s — %s", ddl[:80], exc)
        # Forward-compatible migrations (idempotent ALTER TABLE statements).
        for ddl in _MIGRATIONS:
            try:
                self._client.command(ddl.format(db=self._database))
            except Exception as exc:
                logger.warning("Migration skipped (already applied?): %s — %s", ddl[:60], exc)
        # Structural migration — needs a rebuild (ClickHouse cannot ALTER the engine).
        self._migrate_sessions_row_version()
        logger.info("ClickHouse schema ensured (database=%s)", self._database)

    def _migrate_sessions_row_version(self) -> None:
        """
        Bug fix (2026-07-13) — `contact_closed` silently lost to a ReplacingMergeTree race.

        `sessions` was ReplacingMergeTree() with NO version column, relying on
        "last inserted row wins" + Kafka ordering. That premise is false: the
        consumer reads MULTIPLE topics (conversations.inbound/.routed/.events/…)
        and Kafka only orders WITHIN a partition, never ACROSS topics. So a
        `routed` row (status=active) inserted AFTER `contact_closed` won the dedup
        and erased the close (closed_at → NULL), corrupting open_count/AHT/SLA.

        Fix: ReplacingMergeTree(row_version), where row_version is the EVENT's
        timestamp — so the newest EVENT wins regardless of insert order.

        ClickHouse cannot ALTER the engine, so an existing table is REBUILT. The
        rebuild also REPAIRS history: the `row_version` DEFAULT is
        coalesce(closed_at, opened_at), so the close row (the only one carrying
        closed_at) beats the open/routed rows for the same session.

        Idempotent: no-op once the engine already carries the version column.
        """
        db = self._database
        try:
            engine = self._client.command(
                f"SELECT engine_full FROM system.tables "
                f"WHERE database = '{db}' AND name = 'sessions'"
            )
        except Exception as exc:                       # table not there yet → fresh DDL already correct
            logger.warning("sessions row_version migration: cannot read engine — %s", exc)
            return

        engine_str = str(engine or "")
        if not engine_str:
            return
        if "row_version" in engine_str:
            return                                     # already migrated

        logger.warning(
            "sessions: rebuilding to ReplacingMergeTree(row_version) — the versionless "
            "engine was losing contact_closed rows to cross-topic insert races"
        )
        try:
            # 1. Add the column to the OLD table. The DEFAULT is computed per row, so
            #    historical rows get a version that makes the close row win.
            self._client.command(
                f"ALTER TABLE {db}.sessions ADD COLUMN IF NOT EXISTS "
                f"row_version DateTime64(3, 'UTC') DEFAULT coalesce(closed_at, opened_at)"
            )
            # 2. New table: same structure, versioned engine.
            self._client.command(f"DROP TABLE IF EXISTS {db}.sessions_rv")
            self._client.command(
                f"CREATE TABLE {db}.sessions_rv AS {db}.sessions "
                f"ENGINE = ReplacingMergeTree(row_version) "
                f"PARTITION BY toYYYYMM(date) ORDER BY (tenant_id, session_id)"
            )
            # 3. Copy EVERY row (not FINAL — the new engine dedupes correctly by version).
            self._client.command(
                f"INSERT INTO {db}.sessions_rv SELECT * FROM {db}.sessions"
            )
            # 4. Atomic swap, then drop the old table.
            self._client.command(
                f"RENAME TABLE {db}.sessions TO {db}.sessions_pre_rv, "
                f"{db}.sessions_rv TO {db}.sessions"
            )
            self._client.command(f"DROP TABLE IF EXISTS {db}.sessions_pre_rv")
            logger.info("sessions: rebuilt with ReplacingMergeTree(row_version) — history repaired")
        except Exception as exc:
            logger.error(
                "sessions row_version rebuild FAILED — closes may still be lost: %s", exc
            )

    async def ensure_schema_async(self) -> None:
        await asyncio.to_thread(self.ensure_schema)

    # ── Inserts ───────────────────────────────────────────────────────────────

    def _insert(self, table: str, rows: list[list[Any]], columns: list[str]) -> None:
        if not rows:
            return
        self._client.insert(
            f"{self._database}.{table}",
            rows,
            column_names=columns,
        )

    # sessions

    _SESSION_COLS = [
        "session_id", "tenant_id", "channel", "pool_id", "customer_id",
        "opened_at", "closed_at", "close_reason", "outcome",
        "wait_time_ms", "handle_time_ms", "date",
        "ani", "dnis", "status",
        # SLA do pool no momento do atendimento — a coluna existia (migration)
        # mas nunca entrava no INSERT (chave descartada pelo _session_row).
        "sla_target_ms",
        # Substrate isolation (ADR): origin (live|import|reeval).
        "origin",
        # Journey T1: origin_session_id = a ARESTA pai→filho (1 salto) — quem me criou.
        #
        # A coluna existia (migration Arc 19) e o `parse_inbound` a populava no dict, mas
        # ela NUNCA entrava aqui: era descartada no INSERT e ficava sempre NULL. Ou seja,
        # o modelo falava em ÁRVORE de proveniência e só persistia a RAIZ ACHATADA —
        # sabia-se quais sessões eram da journey, perdia-se **quem gerou quem**. Por isso
        # a Vista Processos listava as sessões como irmãs: a hierarquia era jogada fora na
        # escrita, não por decisão de UI. (A spec do J1 já anotava isso como "no-op
        # latente" — este é o conserto.)
        #
        # Distinto do `root_session_id`: origin = 1 salto (proveniência); root = raiz
        # transitiva (pertença). Separá-los é o que permite um filho pertencer a OUTRA
        # journey e ainda assim manter o fio de quem o criou (ver `journey: new`, T3).
        "origin_session_id",
        # Journey T4: rótulo da aresta — POR QUE esta sessão existe (trigger|delegate|
        # collect). NULL = sessão de topo (o cliente a iniciou; ninguém a criou).
        "spawn_reason",
        # Journey J1: raiz transitiva da proveniência (agrupa) + cache journey_id.
        # Como o sessions é ReplacingMergeTree (linha inteira substituída), TODO
        # writer de linha precisa repetir o root p/ ele sobreviver ao fechamento —
        # _session_row cai no DEFAULT session_id quando ausente (raiz = self).
        "root_session_id",
        "journey_id",
        # Versão do ReplacingMergeTree — timestamp do EVENTO (não da inserção).
        # Sem isso, um `routed` inserido depois de um `contact_closed` (tópicos
        # diferentes = sem ordem garantida no Kafka) apagava o fechamento.
        "row_version",
    ]

    async def upsert_session(self, row: dict) -> None:
        await asyncio.to_thread(
            self._insert, "sessions", [_session_row(row)], self._SESSION_COLS
        )

    # queue_events

    _QUEUE_COLS = [
        "event_id", "tenant_id", "session_id", "pool_id",
        "event_type", "queue_position", "estimated_wait_ms", "available_agents",
        "timestamp", "date",
    ]

    async def insert_queue_event(self, row: dict) -> None:
        await asyncio.to_thread(
            self._insert, "queue_events", [_queue_row(row)], self._QUEUE_COLS
        )

    # agent_events — caminho de escrita REMOVIDO (2026-07-28).
    # `_AGENT_COLS` / `insert_agent_event` / `_agent_row` formavam uma cadeia cujo
    # único ponto de entrada era o dispatch do consumer, que saiu junto com os dois
    # parsers. A tabela foi dropada na fatia 2 (2026-07-29) — ver
    # `_DDL_AGENT_EVENTS_DROP` no topo deste arquivo.

    # messages

    _MESSAGE_COLS = [
        "message_id", "tenant_id", "session_id", "author_id", "author_role",
        "channel", "content_type", "visibility", "content", "timestamp", "date",
        "origin",   # substrate isolation (ADR)
    ]

    async def insert_message(self, row: dict) -> None:
        await asyncio.to_thread(
            self._insert, "messages", [_message_row(row)], self._MESSAGE_COLS
        )

    def query_session_messages(
        self, client: Any, tenant_id: str, session_id: str
    ) -> list[dict]:
        """
        Fallback: returns all messages for a closed session ordered by timestamp.
        Used by the SSE endpoint when the Redis stream key has expired.

        Returns dicts compatible with _parse_entry() output:
          entry_id, type, timestamp, author_id, author_role, visibility, content, payload
        """
        import json as _json

        result = client.query(f"""
            SELECT
                message_id,
                author_id,
                author_role,
                visibility,
                content_type,
                content,
                timestamp
            FROM {self._database}.messages FINAL
            WHERE tenant_id  = {{tenant_id:String}}
              AND session_id = {{session_id:String}}
            ORDER BY timestamp ASC
        """, parameters={"tenant_id": tenant_id, "session_id": session_id})

        rows = []
        for r in result.result_rows:
            msg_id, author_id, author_role, visibility, content_type, content_raw, ts = r
            # Parse content if it's a JSON string
            content_parsed = None
            if content_raw:
                try:
                    content_parsed = _json.loads(content_raw)
                except Exception:
                    content_parsed = content_raw

            ts_str: str | None = None
            if ts is not None:
                from datetime import datetime as _dt, timezone as _tz
                if isinstance(ts, _dt):
                    ts_str = ts.replace(tzinfo=_tz.utc).isoformat()
                else:
                    ts_str = str(ts)

            rows.append({
                "entry_id":    msg_id,
                "type":        "message",
                "timestamp":   ts_str,
                "author_id":   author_id,
                "author_role": author_role or "",
                "visibility":  visibility or "all",
                "content":     content_parsed,
                "payload":     None,
            })
        return rows

    # usage_events

    _USAGE_COLS = [
        "event_id", "tenant_id", "session_id",
        "dimension", "quantity", "source_component", "timestamp", "date",
    ]

    async def insert_usage_event(self, row: dict) -> None:
        await asyncio.to_thread(
            self._insert, "usage_events", [_usage_row(row)], self._USAGE_COLS
        )

    # sentiment_events

    _SENTIMENT_COLS = [
        "event_id", "tenant_id", "session_id", "pool_id",
        "score", "category", "segment_id", "timestamp", "date",
    ]

    async def insert_sentiment_event(self, row: dict) -> None:
        await asyncio.to_thread(
            self._insert, "sentiment_events", [_sentiment_row(row)], self._SENTIMENT_COLS
        )

    # workflow_events

    _WORKFLOW_EVENT_COLS = [
        "event_id", "tenant_id", "instance_id", "flow_id", "campaign_id",
        "event_type", "status", "current_step", "suspend_reason", "decision",
        "outcome", "duration_ms", "wait_duration_ms", "error", "timestamp", "date",
    ]

    async def insert_workflow_event(self, row: dict) -> None:
        await asyncio.to_thread(
            self._insert, "workflow_events", [_workflow_event_row(row)], self._WORKFLOW_EVENT_COLS
        )

    # collect_events

    _COLLECT_EVENT_COLS = [
        "collect_token", "tenant_id", "instance_id", "flow_id", "campaign_id",
        "step_id", "target_type", "channel", "interaction", "status",
        "send_at", "responded_at", "elapsed_ms", "timestamp", "date",
    ]

    async def insert_collect_event(self, row: dict) -> None:
        await asyncio.to_thread(
            self._insert, "collect_events", [_collect_event_row(row)], self._COLLECT_EVENT_COLS
        )

    # participation_intervals

    _PARTICIPATION_COLS = [
        "event_id", "session_id", "tenant_id", "participant_id",
        "pool_id", "agent_type_id", "role", "agent_type",
        "conference_id", "joined_at", "left_at", "duration_ms", "date",
    ]

    async def upsert_participation_interval(self, row: dict) -> None:
        await asyncio.to_thread(
            self._insert,
            "participation_intervals",
            [_participation_row(row)],
            self._PARTICIPATION_COLS,
        )

    # segments (Arc 5)

    _SEGMENT_COLS = [
        "segment_id", "session_id", "tenant_id", "participant_id",
        "pool_id", "agent_type_id", "flow_id", "deploy_version", "channel", "user_id", "user_login", "instance_id", "role", "agent_type",
        "parent_segment_id", "sequence_index",
        "started_at", "ended_at", "duration_ms",
        "outcome", "close_reason", "handoff_reason", "issue_status",
        "escalation_reason", "wrapup_summary", "wrapup_next_steps",
        "conference_id", "date",
        "origin",   # substrate isolation (ADR)
    ]

    async def upsert_segment(self, row: dict) -> None:
        """Insert/update a ContactSegment row.  Called on both participant_joined and
        participant_left — the second write (with ended_at) wins via ReplacingMergeTree."""
        await asyncio.to_thread(
            self._insert,
            "segments",
            [_segment_row(row)],
            self._SEGMENT_COLS,
        )

    # session_timeline (Arc 5)

    _TIMELINE_COLS = [
        "event_id", "tenant_id", "session_id", "segment_id",
        "event_type", "actor_id", "actor_role", "payload", "timestamp",
    ]

    async def insert_timeline_event(self, row: dict) -> None:
        """Insert a generic time-series event into session_timeline."""
        await asyncio.to_thread(
            self._insert,
            "session_timeline",
            [_timeline_row(row)],
            self._TIMELINE_COLS,
        )

    # evaluation_results (Arc 6)

    _EVAL_RESULT_COLS = [
        "result_id", "instance_id", "session_id", "tenant_id",
        "evaluator_id", "form_id", "campaign_id",
        "overall_score", "eval_status", "locked",
        "compliance_flags", "timestamp", "date",
    ]

    async def upsert_evaluation_result(self, row: dict) -> None:
        """Insert or update an EvaluationResult row (review decisions overwrite prior status)."""
        await asyncio.to_thread(
            self._insert,
            "evaluation_results",
            [_eval_result_row(row)],
            self._EVAL_RESULT_COLS,
        )

    # evaluation_events (Arc 6)

    _EVAL_EVENT_COLS = [
        "event_id", "tenant_id", "result_id", "instance_id", "session_id",
        "campaign_id", "event_type", "eval_status", "overall_score",
        "actor_id", "timestamp", "date",
    ]

    async def insert_evaluation_event(self, row: dict) -> None:
        """Insert a lifecycle event from the evaluation.events Kafka topic."""
        await asyncio.to_thread(
            self._insert,
            "evaluation_events",
            [_eval_event_row(row)],
            self._EVAL_EVENT_COLS,
        )

    # evaluation_finalized (T11 — invariante de qualidade / modo Oficial)

    _EVAL_FINALIZED_COLS = [
        "instance_id", "result_id", "session_id", "tenant_id", "campaign_id",
        "final_score", "finalize_reason", "contestation_state", "evaluated_agent_type",
        "segment_id", "form_version", "round", "process_duration_ms", "timestamp", "date",
    ]

    async def upsert_evaluation_finalized(self, row: dict) -> None:
        """Insert/upsert a finalized-evaluation row (ReplacingMergeTree por instance_id)."""
        await asyncio.to_thread(
            self._insert,
            "evaluation_finalized",
            [_eval_finalized_row(row)],
            self._EVAL_FINALIZED_COLS,
        )

    # evaluation_dimension_scores (F8 — nota por dimensão)

    _EVAL_DIMENSION_COLS = [
        "result_id", "instance_id", "session_id", "tenant_id",
        "evaluator_id", "form_id", "campaign_id",
        "dimension_id", "dimension_name", "score", "weight",
        "eval_status", "timestamp", "date",
    ]

    async def insert_evaluation_dimension_score(self, row: dict) -> None:
        """Insert one per-dimension score row (F8 — fonte da lente quality_criteria)."""
        await asyncio.to_thread(
            self._insert,
            "evaluation_dimension_scores",
            [_eval_dimension_row(row)],
            self._EVAL_DIMENSION_COLS,
        )

    # contact_insights

    _CONTACT_INSIGHT_COLS = [
        "insight_id", "tenant_id", "session_id",
        "insight_type", "category", "value", "tags",
        "agent_id", "timestamp", "date",
    ]

    async def insert_contact_insight(self, row: dict) -> None:
        """Insert a business insight event from insight_register MCP tool."""
        await asyncio.to_thread(
            self._insert,
            "contact_insights",
            [_contact_insight_row(row)],
            self._CONTACT_INSIGHT_COLS,
        )

    # agent_pause_intervals (Arc 8)

    _AGENT_PAUSE_INTERVAL_COLS = [
        "interval_id", "tenant_id", "instance_id", "agent_type_id", "pool_id",
        "reason_id", "reason_label", "note",
        "paused_at", "resumed_at", "duration_ms",
        # ingested_at omitted — DEFAULT now()
        "date",
    ]

    async def upsert_agent_pause_interval(self, row: dict) -> None:
        """Insert (open) or update (close) a pause interval row.

        Open row:  resumed_at=None, duration_ms=None  — written on agent_pause event.
        Close row: resumed_at+duration_ms filled       — written on agent_ready after pause.
        ReplacingMergeTree(ingested_at) ensures the close row wins on merge.
        """
        await asyncio.to_thread(
            self._insert,
            "agent_pause_intervals",
            [_agent_pause_interval_row(row)],
            self._AGENT_PAUSE_INTERVAL_COLS,
        )

    # agent_login_intervals (Fase 1b)

    _AGENT_LOGIN_INTERVAL_COLS = [
        "interval_id", "tenant_id", "instance_id", "user_id", "user_login",
        "agent_type_id", "pool_id",
        "logged_in_at", "logged_out_at", "duration_ms",
        # ingested_at omitted — DEFAULT now()
        "date",
    ]

    async def upsert_agent_login_interval(self, row: dict) -> None:
        """Insert (open) or update (close) a login interval row.

        Open row:  logged_out_at=None, duration_ms=None — written on first agent_ready/agent_login.
        Close row: logged_out_at+duration_ms filled      — written on agent_logout.
        ReplacingMergeTree(ingested_at) ensures the close row wins on merge.
        """
        await asyncio.to_thread(
            self._insert,
            "agent_login_intervals",
            [_agent_login_interval_row(row)],
            self._AGENT_LOGIN_INTERVAL_COLS,
        )

    # agent_pool_intervals (timeline — per-pool presence)

    _AGENT_POOL_INTERVAL_COLS = [
        "interval_id", "login_interval_id", "tenant_id", "instance_id",
        "user_id", "user_login", "agent_type_id", "pool_id",
        "entered_at", "left_at", "duration_ms",
        # ingested_at omitted — DEFAULT now()
        "date",
    ]

    async def upsert_agent_pool_interval(self, row: dict) -> None:
        """Insert (open) or update (close) a per-pool presence interval row.

        Open row:  left_at=None, duration_ms=None — written when the agent enters a pool.
        Close row: left_at+duration_ms filled      — written when the agent leaves it.
        ReplacingMergeTree(ingested_at) ensures the close row wins on merge.
        """
        await asyncio.to_thread(
            self._insert,
            "agent_pool_intervals",
            [_agent_pool_interval_row(row)],
            self._AGENT_POOL_INTERVAL_COLS,
        )

    # pool_occupancy_peaks (Fase 2)

    _POOL_OCCUPANCY_COLS = [
        "tenant_id", "pool_id", "minute", "peak_concurrency", "provisioned_capacity",
        "admitted_peak",   # item 7b
        # ingested_at omitted — DEFAULT now()
        "date",
    ]

    async def upsert_pool_occupancy_peak(self, row: dict) -> None:
        """Insert a per-minute occupancy peak (pool or '__total__')."""
        await asyncio.to_thread(
            self._insert,
            "pool_occupancy_peaks",
            [_pool_occupancy_row(row)],
            self._POOL_OCCUPANCY_COLS,
        )

    # journey_events (Arc 10) — REMOVED (Arc 19 Fase F)

    # agent_business_events (Arc 12)

    _AGENT_BUSINESS_EVENT_COLS = [
        "event_id", "tenant_id", "session_id", "journey_id",
        "agent_type_id", "skill_id", "pool_id",
        "category", "category_l1", "category_l2", "category_l3", "category_l4",
        "value", "tags", "emitted_at", "date",
    ]

    async def insert_agent_business_event(self, row: dict) -> None:
        """Insert a business KPI event from the agent.events Kafka topic (Arc 12)."""
        await asyncio.to_thread(
            self._insert,
            "agent_business_events",
            [_agent_business_event_row(row)],
            self._AGENT_BUSINESS_EVENT_COLS,
        )

    # session_signal (F10 bancada — voz do cliente/agente grão contato/jornada)

    _SESSION_SIGNAL_COLS = [
        "signal_id", "tenant_id", "session_id", "grain", "segment_id", "agent_key",
        "pool_id", "source", "metric", "value_num", "value_label",
        "scale_min", "scale_max",
        "session_at", "captured_at", "origin_session_id", "journey_id", "date",
    ]

    async def insert_session_signal(self, row: dict) -> None:
        """Insert a normalized contact/journey-grain signal (F10 bancada)."""
        await asyncio.to_thread(
            self._insert,
            "session_signal",
            [_session_signal_row(row)],
            self._SESSION_SIGNAL_COLS,
        )

    # journey_aliases (Journey J3 — arestas de merge novo→antigo)

    _JOURNEY_ALIAS_COLS = [
        "tenant_id", "source_root", "canonical_root", "merged_at", "actor", "active",
    ]

    async def insert_journey_alias(self, row: dict) -> None:
        """Insert a journey merge edge (topic journey.merges → journey_aliases, J3)."""
        await asyncio.to_thread(
            self._insert,
            "journey_aliases",
            [_journey_alias_row(row)],
            self._JOURNEY_ALIAS_COLS,
        )

    # calibration_events (Arc 13)

    _CALIBRATION_EVENT_COLS = [
        "event_id", "tenant_id", "campaign_id", "evaluator_id", "skill_version",
        "decision", "dimension_id", "severity", "curator_id", "note_id",
        "event_time", "date",
    ]

    async def insert_calibration_event(self, row: dict) -> None:
        """Insert a curator decision from the calibration.events Kafka topic (Arc 13)."""
        await asyncio.to_thread(
            self._insert,
            "calibration_events",
            [_calibration_event_row(row)],
            self._CALIBRATION_EVENT_COLS,
        )

    # ── Arc 5: segment_id lookups (for post-hoc enrichment) ───────────────────

    async def lookup_segment_id(
        self,
        tenant_id:    str,
        session_id:   str,
        participant_id: str,
    ) -> str | None:
        """
        Return the most recent segment_id for a (session, participant) pair.
        Used as ClickHouse fallback when Redis key has expired (TTL 4 h).
        Runs in a thread-pool executor to avoid blocking the event loop.
        """
        def _query() -> str | None:
            client = self.new_client()
            try:
                result = client.query(
                    f"""
                    SELECT segment_id
                    FROM {self._database}.segments FINAL
                    WHERE tenant_id     = %(tenant_id)s
                      AND session_id   = %(session_id)s
                      AND participant_id = %(participant_id)s
                    ORDER BY started_at DESC
                    LIMIT 1
                    """,
                    parameters={
                        "tenant_id":     tenant_id,
                        "session_id":    session_id,
                        "participant_id": participant_id,
                    },
                )
                rows = result.result_rows
                return rows[0][0] if rows else None
            finally:
                client.close()

        return await asyncio.to_thread(_query)

    async def lookup_primary_segment_id(
        self,
        tenant_id:  str,
        session_id: str,
    ) -> str | None:
        """
        Return the most recent primary-role segment_id for a session.
        Used when no instance_id is available (e.g. sentiment.updated events).
        Prefers open segments (ended_at IS NULL); falls back to most recently
        started segment with role='primary'.
        """
        def _query() -> str | None:
            client = self.new_client()
            try:
                result = client.query(
                    f"""
                    SELECT segment_id
                    FROM {self._database}.segments FINAL
                    WHERE tenant_id  = %(tenant_id)s
                      AND session_id = %(session_id)s
                      AND role       = 'primary'
                    ORDER BY (ended_at IS NULL) DESC, started_at DESC
                    LIMIT 1
                    """,
                    parameters={
                        "tenant_id":  tenant_id,
                        "session_id": session_id,
                    },
                )
                rows = result.result_rows
                return rows[0][0] if rows else None
            finally:
                client.close()

        return await asyncio.to_thread(_query)

    async def lookup_session_opened_at(
        self,
        tenant_id:  str,
        session_id: str,
    ) -> str | None:
        """
        Return the ISO8601 opened_at of the ORIGINAL session (F11 enrichment).

        Used by the session.signals consumer to bucketize survey signals by the
        original session's date (golden rule §7) instead of captured_at — which
        only coincides for same-day "no-ato" surveys.  For DEFERRED surveys
        (captured_at days later) this resolves the correct session_at.

        Returns None when the session is not (yet) in analytics.sessions; the
        caller then keeps captured_at as a safe fallback (no event dropped).
        Runs in a thread-pool executor to avoid blocking the event loop.
        """
        def _query() -> str | None:
            client = self.new_client()
            try:
                result = client.query(
                    f"""
                    SELECT opened_at
                    FROM {self._database}.sessions FINAL
                    WHERE tenant_id  = %(tenant_id)s
                      AND session_id = %(session_id)s
                    ORDER BY opened_at ASC
                    LIMIT 1
                    """,
                    parameters={
                        "tenant_id":  tenant_id,
                        "session_id": session_id,
                    },
                )
                rows = result.result_rows
                if not rows or rows[0][0] is None:
                    return None
                val = rows[0][0]
                # clickhouse-connect returns a datetime for DateTime64 columns;
                # normalise to ISO8601 so the row builder's _parse_dt handles it.
                return val.isoformat() if hasattr(val, "isoformat") else str(val)
            finally:
                client.close()

        return await asyncio.to_thread(_query)


# ─── Row builders ─────────────────────────────────────────────────────────────

def _parse_dt(ts: str | None) -> datetime | None:
    """Parses an ISO8601 string to a naive UTC datetime (ClickHouse expects naive)."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    except Exception:
        return datetime.utcnow()


def _today_utc(ts: str | None = None) -> datetime:
    """Returns a date for the partition key. Prefers the event timestamp."""
    dt = _parse_dt(ts)
    return dt if dt else datetime.utcnow()


def _session_row(d: dict) -> list:
    ts = d.get("timestamp") or d.get("opened_at") or d.get("started_at")
    return [
        d.get("session_id", ""),
        d.get("tenant_id", ""),
        d.get("channel", ""),
        d.get("pool_id", "") or "",
        d.get("customer_id") or d.get("contact_id") or None,
        _parse_dt(d.get("opened_at") or d.get("started_at") or d.get("timestamp")) or datetime.utcnow(),
        _parse_dt(d.get("closed_at") or d.get("ended_at")),
        d.get("close_reason"),
        d.get("outcome"),
        d.get("wait_time_ms"),
        d.get("handle_time_ms"),
        _today_utc(ts),
        # ANI/DNIS — caller/source and dialed/destination identifiers (any channel)
        d.get("ani") or d.get("caller_id") or d.get("from") or None,
        d.get("dnis") or d.get("dialed_number") or d.get("to") or None,
        # Arc 19: session status — 'active', 'suspended', or 'closed'. None = pre-Arc-19.
        d.get("status") or None,
        # SLA do pool (ms) — vem do parse_routed (routing result) e do
        # parse_contact_closed (close row precisa repetir o valor: no
        # ReplacingMergeTree a última escrita substitui a linha inteira).
        d.get("sla_target_ms"),
        # Substrate isolation (ADR): origin não-nullable, default 'live'.
        d.get("origin") or "live",
        # Journey T1: origin_session_id — a aresta pai→filho (1 salto). NULL numa sessão
        # de topo (ninguém a criou), que é a leitura correta: raiz de árvore não tem pai.
        #
        # `_inject_session_identity` (consumer) o reinjeta nas linhas parciais — o campo
        # já estava em `_IDENTITY_FIELDS`, esperando por este INSERT que nunca vinha.
        d.get("origin_session_id") or None,
        # Journey T4: rótulo da aresta. NULL numa sessão de topo.
        d.get("spawn_reason") or None,
        # Journey J1: root (raiz transitiva) — fallback = self (session_id) quando o
        # writer não carrega a raiz. Writers que têm a raiz (parse_inbound/close) a
        # repetem para sobreviver ao ReplacingMergeTree; routed/queued caem no self
        # (transitório até a linha de fechamento, que carrega o valor propagado).
        d.get("root_session_id") or d.get("session_id") or "",
        # journey_id: cache = root no nascimento (nunca fonte de verdade).
        d.get("journey_id") or d.get("root_session_id") or d.get("session_id") or "",
        # ── row_version (versão do ReplacingMergeTree) ────────────────────────
        # Timestamp do EVENTO, nunca da inserção — é isso que torna a dedup
        # determinística mesmo com eventos chegando fora de ordem de tópicos
        # diferentes (o Kafka não ordena entre tópicos).
        #
        # Ordem de preferência:
        #   closed_at/ended_at  — o fechamento é, por definição, o instante final
        #                         da vida da sessão → sempre vence routed/queued/
        #                         suspend anteriores, mesmo se inserido antes deles.
        #   timestamp           — evento intermediário (routed, queued, suspended).
        #   opened_at/started_at— abertura.
        # Fallback utcnow() só para writers legados sem nenhum timestamp.
        _parse_dt(
            d.get("closed_at") or d.get("ended_at")
            or d.get("timestamp")
            or d.get("opened_at") or d.get("started_at")
        ) or datetime.utcnow(),
    ]


def _first_not_none(*values):
    """Primeiro valor não-None (≠ `or`, que descarta 0/""/False)."""
    for v in values:
        if v is not None:
            return v
    return None


def _queue_row(d: dict) -> list:
    ts = d.get("timestamp") or d.get("published_at")
    return [
        d.get("event_id", ""),
        d.get("tenant_id", ""),
        d.get("session_id", ""),
        d.get("pool_id", "") or "",
        d.get("event_type", ""),
        # `or` engolia o ZERO (posição/legado 0 → cai no fallback ausente → NULL):
        # "primeiro da fila" e "sem dado" viravam a mesma coisa na tabela.
        _first_not_none(d.get("queue_position"), d.get("queue_length")),
        d.get("estimated_wait_ms"),
        d.get("available_agents"),
        _parse_dt(ts) or datetime.utcnow(),
        _today_utc(ts),
    ]


# _agent_row removida com o caminho de escrita de `agent_events` (2026-07-28).


def _message_row(d: dict) -> list:
    ts = d.get("timestamp")
    content = d.get("content")
    # Normalise content to a JSON string if it's a dict/list
    if content is not None and not isinstance(content, str):
        import json as _json
        content = _json.dumps(content, ensure_ascii=False)
    return [
        d.get("message_id", ""),
        d.get("tenant_id", ""),
        d.get("session_id", ""),
        d.get("author_id") or None,
        d.get("author_role", ""),
        d.get("channel", "") or "",
        d.get("content_type", "") or "",
        d.get("visibility", "all"),
        content,
        _parse_dt(ts) or datetime.utcnow(),
        _today_utc(ts),
        d.get("origin") or "live",   # substrate isolation (ADR)
    ]


def _usage_row(d: dict) -> list:
    ts = d.get("timestamp")
    return [
        d.get("event_id", ""),
        d.get("tenant_id", ""),
        d.get("session_id", "") or "",
        d.get("dimension", ""),
        int(d.get("quantity", 0)),
        d.get("source_component", "") or "",
        _parse_dt(ts) or datetime.utcnow(),
        _today_utc(ts),
    ]


def _sentiment_row(d: dict) -> list:
    ts = d.get("timestamp")
    return [
        d.get("event_id", ""),
        d.get("tenant_id", ""),
        d.get("session_id", ""),
        d.get("pool_id", "") or "",
        float(d.get("score", 0.0)),
        d.get("category", "neutral"),
        d.get("segment_id") or None,   # Nullable — None when enrichment failed
        _parse_dt(ts) or datetime.utcnow(),
        _today_utc(ts),
    ]


def _workflow_event_row(d: dict) -> list:
    ts = d.get("timestamp")
    return [
        d.get("event_id", ""),
        d.get("tenant_id", ""),
        d.get("instance_id", ""),
        d.get("flow_id", ""),
        d.get("campaign_id"),
        d.get("event_type", ""),
        d.get("status"),
        d.get("current_step"),
        d.get("suspend_reason"),
        d.get("decision"),
        d.get("outcome"),
        d.get("duration_ms"),
        d.get("wait_duration_ms"),
        d.get("error"),
        _parse_dt(ts) or datetime.utcnow(),
        _today_utc(ts),
    ]


def _collect_event_row(d: dict) -> list:
    ts = d.get("timestamp")
    return [
        d.get("collect_token", ""),
        d.get("tenant_id", ""),
        d.get("instance_id", ""),
        d.get("flow_id", ""),
        d.get("campaign_id"),
        d.get("step_id", ""),
        d.get("target_type", ""),
        d.get("channel", ""),
        d.get("interaction", ""),
        d.get("status", ""),
        _parse_dt(d.get("send_at")),
        _parse_dt(d.get("responded_at")),
        d.get("elapsed_ms"),
        _parse_dt(ts) or datetime.utcnow(),
        _today_utc(ts),
    ]


def _participation_row(d: dict) -> list:
    ts = d.get("timestamp") or d.get("joined_at")
    event_type = d.get("type", "")
    joined_at  = _parse_dt(d.get("joined_at"))
    left_at    = _parse_dt(d.get("timestamp")) if event_type == "participant_left" else None
    return [
        d.get("event_id", ""),
        d.get("session_id", ""),
        d.get("tenant_id", ""),
        d.get("participant_id", ""),
        d.get("pool_id", "") or "",
        d.get("agent_type_id", "") or "",
        d.get("role", ""),
        d.get("agent_type", ""),
        d.get("conference_id") or None,
        joined_at,
        left_at,
        d.get("duration_ms"),
        _today_utc(ts),
    ]


def _segment_row(d: dict) -> list:
    """Row builder for the segments table (Arc 5 — ContactSegment)."""
    ts         = d.get("timestamp") or d.get("joined_at")
    event_type = d.get("type", d.get("event_type", ""))
    started_at = _parse_dt(d.get("joined_at") or d.get("started_at") or d.get("timestamp"))
    ended_at   = (
        _parse_dt(d.get("timestamp"))
        if event_type in ("participant_left", "participant.left")
        else None
    )
    return [
        d.get("segment_id", "") or "",
        d.get("session_id", ""),
        d.get("tenant_id", ""),
        d.get("participant_id", ""),
        d.get("pool_id", "") or "",
        d.get("agent_type_id", "") or "",
        d.get("flow_id", "") or "",
        d.get("deploy_version", "") or "",
        d.get("channel", "") or "",
        d.get("user_id", "") or "",
        d.get("user_login", "") or "",
        d.get("instance_id", d.get("participant_id", "")) or "",
        d.get("role", ""),
        d.get("agent_type", ""),
        d.get("parent_segment_id") or None,
        int(d.get("sequence_index", 0)),
        started_at or datetime.utcnow(),
        ended_at,
        d.get("duration_ms"),
        d.get("outcome") or None,
        d.get("close_reason") or None,
        d.get("handoff_reason") or None,
        d.get("issue_status") or None,
        d.get("escalation_reason") or None,
        d.get("wrapup_summary") or None,
        d.get("wrapup_next_steps") or None,
        d.get("conference_id") or None,
        _today_utc(ts),
        d.get("origin") or "live",   # substrate isolation (ADR)
    ]


def _timeline_row(d: dict) -> list:
    """Row builder for the session_timeline table (Arc 5)."""
    ts = d.get("timestamp")
    import json as _json
    payload_raw = d.get("payload", {})
    payload_str = (
        payload_raw if isinstance(payload_raw, str)
        else _json.dumps(payload_raw)
    )
    return [
        d.get("event_id", "") or "",
        d.get("tenant_id", ""),
        d.get("session_id", ""),
        d.get("segment_id", "") or "",
        d.get("event_type", ""),
        d.get("actor_id", "") or "",
        d.get("actor_role", "") or "",
        payload_str,
        _parse_dt(ts) or datetime.utcnow(),
    ]


def _eval_result_row(d: dict) -> list:
    """Row builder for evaluation_results table (Arc 6)."""
    ts = d.get("timestamp") or d.get("created_at")
    flags = d.get("compliance_flags") or []
    if isinstance(flags, str):
        import json as _json
        try:
            flags = _json.loads(flags)
        except Exception:
            flags = []
    return [
        d.get("result_id", ""),
        d.get("instance_id", "") or "",
        d.get("session_id", "") or "",
        d.get("tenant_id", ""),
        d.get("evaluator_id", "") or "",
        d.get("form_id", "") or "",
        d.get("campaign_id") or None,
        float(d.get("overall_score", 0.0)),
        d.get("eval_status", "submitted"),
        1 if d.get("locked") else 0,
        list(flags),
        _parse_dt(ts) or datetime.utcnow(),
        _today_utc(ts),
    ]


def _eval_event_row(d: dict) -> list:
    """Row builder for evaluation_events table (Arc 6)."""
    ts = d.get("timestamp")
    score = d.get("overall_score")
    return [
        d.get("event_id", "") or "",
        d.get("tenant_id", ""),
        d.get("result_id", "") or "",
        d.get("instance_id", "") or "",
        d.get("session_id", "") or "",
        d.get("campaign_id") or None,
        d.get("event_type", ""),
        d.get("eval_status") or None,
        float(score) if score is not None else None,
        d.get("actor_id") or None,
        _parse_dt(ts) or datetime.utcnow(),
        _today_utc(ts),
    ]


def _eval_dimension_row(d: dict) -> list:
    """Row builder for evaluation_dimension_scores table (F8)."""
    ts = d.get("timestamp") or d.get("created_at")
    return [
        d.get("result_id", "") or "",
        d.get("instance_id", "") or "",
        d.get("session_id", "") or "",
        d.get("tenant_id", ""),
        d.get("evaluator_id", "") or "",
        d.get("form_id", "") or "",
        d.get("campaign_id") or None,
        d.get("dimension_id", "") or "",
        d.get("dimension_name", "") or "",
        float(d.get("score", 0.0) or 0.0),
        float(d.get("weight", 0.0) or 0.0),
        d.get("eval_status", "submitted") or "submitted",
        _parse_dt(ts) or datetime.utcnow(),
        _today_utc(ts),
    ]


def _eval_finalized_row(d: dict) -> list:
    """Row builder for evaluation_finalized table (T11 — invariante de qualidade)."""
    ts = d.get("timestamp")
    return [
        d.get("instance_id", "") or "",
        d.get("result_id", "") or "",
        d.get("session_id", "") or "",
        d.get("tenant_id", ""),
        d.get("campaign_id") or None,
        float(d.get("final_score", 0.0) or 0.0),
        d.get("finalize_reason", "") or "",
        d.get("contestation_state", "") or "",
        d.get("evaluated_agent_type", "") or "",
        d.get("segment_id", "") or "",
        int(d.get("form_version", 0) or 0),
        int(d.get("round", 1) or 1),
        int(d.get("process_duration_ms", 0) or 0),
        _parse_dt(ts) or datetime.utcnow(),
        _today_utc(ts),
    ]


def _contact_insight_row(d: dict) -> list:
    """Row builder for contact_insights table."""
    import uuid as _uuid
    ts = d.get("timestamp")
    tags = d.get("tags") or []
    if isinstance(tags, str):
        import json as _json
        try:
            tags = _json.loads(tags)
        except Exception:
            tags = [tags] if tags else []
    return [
        d.get("insight_id", "") or str(_uuid.uuid4()),
        d.get("tenant_id", ""),
        d.get("session_id", ""),
        d.get("insight_type", ""),
        d.get("category", ""),
        str(d.get("value", "")) if d.get("value") is not None else "",
        list(tags),
        d.get("agent_id") or None,
        _parse_dt(ts) or datetime.utcnow(),
        _today_utc(ts),
    ]


def _agent_pause_interval_row(d: dict) -> list:
    """Row builder for agent_pause_intervals table (Arc 8).

    An "open" row has resumed_at=None and duration_ms=None.
    A "close" row (written when agent_ready follows agent_pause) has
    resumed_at and duration_ms filled in.  ReplacingMergeTree(ingested_at)
    ensures the close row wins on background merge.
    """
    paused_ts   = d.get("paused_at")
    resumed_ts  = d.get("resumed_at")
    paused_dt   = _parse_dt(paused_ts) if paused_ts else datetime.utcnow()
    resumed_dt  = _parse_dt(resumed_ts) if resumed_ts else None
    return [
        d.get("interval_id", ""),
        d.get("tenant_id", ""),
        d.get("instance_id", ""),
        d.get("agent_type_id", ""),
        d.get("pool_id", ""),
        d.get("reason_id", ""),
        d.get("reason_label", ""),
        d.get("note") or None,
        paused_dt,
        resumed_dt,
        d.get("duration_ms") or None,
        # ingested_at — DEFAULT now(), omitted so ClickHouse fills it
        _today_utc(paused_ts),
    ]


def _agent_login_interval_row(d: dict) -> list:
    """Row builder for agent_login_intervals table (Fase 1b).

    An "open" row has logged_out_at=None and duration_ms=None.
    A "close" row (written on agent_logout) has logged_out_at and duration_ms
    filled in.  ReplacingMergeTree(ingested_at) ensures the close row wins.
    """
    in_ts   = d.get("logged_in_at")
    out_ts  = d.get("logged_out_at")
    in_dt   = _parse_dt(in_ts) if in_ts else datetime.utcnow()
    out_dt  = _parse_dt(out_ts) if out_ts else None
    return [
        d.get("interval_id", ""),
        d.get("tenant_id", ""),
        d.get("instance_id", ""),
        d.get("user_id", ""),
        d.get("user_login", ""),
        d.get("agent_type_id", ""),
        d.get("pool_id", ""),
        in_dt,
        out_dt,
        d.get("duration_ms") or None,
        # ingested_at — DEFAULT now(), omitted so ClickHouse fills it
        _today_utc(in_ts),
    ]


def _agent_pool_interval_row(d: dict) -> list:
    """Row builder for agent_pool_intervals (timeline — per-pool presence).

    Open row: left_at=None, duration_ms=None. Close row fills both.
    """
    in_ts  = d.get("entered_at")
    out_ts = d.get("left_at")
    in_dt  = _parse_dt(in_ts) if in_ts else datetime.utcnow()
    out_dt = _parse_dt(out_ts) if out_ts else None
    return [
        d.get("interval_id", ""),
        d.get("login_interval_id", ""),
        d.get("tenant_id", ""),
        d.get("instance_id", ""),
        d.get("user_id", ""),
        d.get("user_login", ""),
        d.get("agent_type_id", ""),
        d.get("pool_id", ""),
        in_dt,
        out_dt,
        d.get("duration_ms") or None,
        # ingested_at — DEFAULT now(), omitted so ClickHouse fills it
        _today_utc(in_ts),
    ]


def _pool_occupancy_row(d: dict) -> list:
    """Row builder for pool_occupancy_peaks (Fase 2; item 7b: admitted_peak)."""
    minute_ts = d.get("minute")
    minute_dt = _parse_dt(minute_ts) if minute_ts else datetime.utcnow()
    return [
        d.get("tenant_id", ""),
        d.get("pool_id", ""),
        minute_dt,
        int(d.get("peak_concurrency") or 0),
        int(d.get("provisioned_capacity") or 0),
        int(d.get("admitted_peak") or 0),
        # ingested_at — DEFAULT now()
        _today_utc(minute_ts),
    ]


# _journey_event_row — REMOVED (Arc 19 Fase F)


def _agent_business_event_row(d: dict) -> list:
    """Row builder for agent_business_events table (Arc 12).

    category_l1..l4 arrive pre-decomposed from the MCP tool publish step.
    Fallback decomposition is applied here in case the consumer receives a
    raw payload that skipped the MCP layer (e.g. replayed from DLQ).
    tags must be a dict[str, str] — non-string values are coerced to str.
    """
    ts       = d.get("emitted_at") or d.get("timestamp")
    category = d.get("category", "")
    # Fallback decomposition from category string when pre-decomposed fields absent
    parts = category.split(".") if category else []
    def _level(key: str, idx: int) -> str:
        return d.get(key) or (parts[idx] if len(parts) > idx else "")

    tags_raw = d.get("tags") or {}
    if not isinstance(tags_raw, dict):
        tags_raw = {}
    tags = {str(k): str(v) for k, v in tags_raw.items()}

    return [
        d.get("event_id", ""),
        d.get("tenant_id", ""),
        d.get("session_id", ""),
        d.get("journey_id") or None,
        d.get("agent_type_id", "") or "",
        d.get("skill_id", "") or "",
        d.get("pool_id", "") or "",
        category,
        _level("category_l1", 0),
        _level("category_l2", 1),
        _level("category_l3", 2),
        _level("category_l4", 3),
        float(d.get("value", 0.0)),
        tags,
        _parse_dt(ts) or datetime.utcnow(),
        _today_utc(ts),
    ]


def _session_signal_row(d: dict) -> list:
    """Row builder for session_signal table (F10 bancada).

    Voz do cliente/agente no grão contato/jornada. session_at é a base de
    bucketização (data da sessão original); captured_at é quando o sinal chegou
    (≠ session_at em pesquisa diferida). date (partição) deriva de session_at.
    """
    session_at  = d.get("session_at") or d.get("captured_at") or d.get("timestamp")
    captured_at = d.get("captured_at") or session_at
    value_num   = d.get("value_num")
    return [
        d.get("signal_id", "") or "",
        d.get("tenant_id", "") or "",
        d.get("session_id", "") or "",
        d.get("grain", "") or "",
        d.get("segment_id", "") or "",
        d.get("agent_key", "") or "",
        d.get("pool_id", "") or "",
        d.get("source", "") or "",
        d.get("metric", "") or "",
        float(value_num) if value_num is not None else None,
        d.get("value_label") or None,
        float(d["scale_min"]) if d.get("scale_min") is not None else None,
        float(d["scale_max"]) if d.get("scale_max") is not None else None,
        _parse_dt(session_at) or datetime.utcnow(),
        _parse_dt(captured_at) or datetime.utcnow(),
        d.get("origin_session_id") or None,
        d.get("journey_id") or None,
        _today_utc(session_at),
    ]


def _journey_alias_row(d: dict) -> list:
    """Row builder for journey_aliases table (Journey J3 — merge edge novo→antigo)."""
    return [
        d.get("tenant_id", "") or "",
        d.get("source_root", "") or "",
        d.get("canonical_root", "") or "",
        _parse_dt(d.get("merged_at")) or datetime.utcnow(),
        d.get("actor", "") or "",
        int(d.get("active", 1)),
    ]


def _calibration_event_row(d: dict) -> list:
    """Row builder for calibration_events table (Arc 13).

    Maps calibration_reviewed Kafka events to their ClickHouse representation.
    event_time stored as DateTime64 UTC; date is the partition key.
    """
    ts = d.get("event_time") or d.get("timestamp")
    return [
        d.get("event_id", ""),
        d.get("tenant_id", ""),
        d.get("campaign_id", "") or "",
        d.get("evaluator_id", "") or "",
        d.get("skill_version", "") or "",
        d.get("decision", "") or "",
        d.get("dimension_id", "") or "",
        d.get("severity", "") or "",
        d.get("curator_id") or None,
        d.get("note_id") or None,
        _parse_dt(ts) or datetime.utcnow(),
        _today_utc(ts),
    ]
