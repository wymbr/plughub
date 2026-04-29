-- scripts/init-clickhouse.sql
-- Schema inicial do ClickHouse para o PlugHub Platform
-- Uso: docker exec plughub-clickhouse clickhouse-client --user plughub --password plughub --query "$(cat scripts/init-clickhouse.sql)"

CREATE DATABASE IF NOT EXISTS plughub_metrics;

-- ─────────────────────────────────────────────
-- Disparos de regras do Rules Engine (spec 3.2)
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS plughub_metrics.rule_triggers
(
    tenant_id    String,
    rule_id      String,
    session_id   String,
    pool_destino Nullable(String),
    shadow_mode  UInt8,
    fired_at     DateTime,

    -- Parâmetros do turno no momento do disparo
    sentiment_score    Float32,
    intent_confidence  Float32,
    turn_count         UInt16,
    elapsed_ms         UInt32,
    flags              Array(String)
)
ENGINE = MergeTree()
ORDER BY (tenant_id, fired_at)
PARTITION BY toYYYYMM(fired_at)
TTL fired_at + INTERVAL 90 DAY;

-- ─────────────────────────────────────────────
-- Resultados de atendimento por sessão (spec 4.2)
-- Usado pelo Evaluation Agent e relatórios
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS plughub_metrics.session_outcomes
(
    tenant_id     String,
    session_id    String,
    pool_id       String,
    agent_type_id String,
    outcome       String,    -- resolved | escalated | abandoned | timeout
    handoff_reason Nullable(String),
    issue_status  String,
    started_at    DateTime,
    ended_at      DateTime,
    duration_ms   UInt32,
    turn_count    UInt16,
    final_sentiment Float32
)
ENGINE = MergeTree()
ORDER BY (tenant_id, ended_at)
PARTITION BY toYYYYMM(ended_at)
TTL ended_at + INTERVAL 365 DAY;
