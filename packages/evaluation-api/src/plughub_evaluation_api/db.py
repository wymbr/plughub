"""
db.py
asyncpg DDL + CRUD for the evaluation module.

Schema: evaluation (dedicated PostgreSQL schema)

Tables:
  evaluation.forms             — EvaluationForm definitions with JSONB dimensions + criteria
  evaluation.campaigns         — EvaluationCampaign: links form + pool, sampling + reviewer rules
  evaluation.instances         — EvaluationInstance: one per session scheduled for evaluation
  evaluation.results           — EvaluationResult: the evaluator agent's output per instance
  evaluation.criterion_responses — per-criterion scores/values with evidence refs
  evaluation.contestations     — human contestation workflow

All timestamps are TIMESTAMPTZ.
All IDs are TEXT (UUIDs as strings, prefixed e.g. "evform_", "evcampaign_", "evinstance_").
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import asyncpg

logger = logging.getLogger("plughub.evaluation.db")

# ─── DDL ──────────────────────────────────────────────────────────────────────

_DDL = """
CREATE SCHEMA IF NOT EXISTS evaluation;

-- ── EvaluationForm ────────────────────────────────────────────────────────────
-- Stores the full form definition as JSONB (dimensions → criteria hierarchy).
CREATE TABLE IF NOT EXISTS evaluation.forms (
    id              TEXT        PRIMARY KEY,            -- "evform_{uuid}"
    tenant_id       TEXT        NOT NULL,
    name            TEXT        NOT NULL,
    description     TEXT        NOT NULL DEFAULT '',
    version         INTEGER     NOT NULL DEFAULT 1,
    status          TEXT        NOT NULL DEFAULT 'active'
                    CHECK (status IN ('draft', 'active', 'archived')),
    dimensions      JSONB       NOT NULL DEFAULT '[]',  -- EvaluationDimension[]
    total_weight    NUMERIC(6,3) NOT NULL DEFAULT 1.0,
    passing_score   NUMERIC(6,3),                       -- NULL = no minimum
    allow_na        BOOLEAN     NOT NULL DEFAULT TRUE,
    knowledge_domains TEXT[]    NOT NULL DEFAULT '{}',  -- namespaces used for RAG
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by      TEXT        NOT NULL DEFAULT 'operator'
);

CREATE INDEX IF NOT EXISTS idx_evforms_tenant
    ON evaluation.forms (tenant_id, status);

-- ── EvaluationCampaign ────────────────────────────────────────────────────────
-- Links a form to a pool; controls sampling, scheduling, and reviewer rules.
CREATE TABLE IF NOT EXISTS evaluation.campaigns (
    id                  TEXT        PRIMARY KEY,         -- "evcampaign_{uuid}"
    tenant_id           TEXT        NOT NULL,
    name                TEXT        NOT NULL,
    description         TEXT        NOT NULL DEFAULT '',
    form_id             TEXT        NOT NULL REFERENCES evaluation.forms(id),
    pool_id             TEXT        NOT NULL,
    status              TEXT        NOT NULL DEFAULT 'active'
                        CHECK (status IN ('draft', 'active', 'paused', 'closed')),
    -- Sampling rules (JSONB — SamplingRules schema)
    sampling_rules      JSONB       NOT NULL DEFAULT '{}',
    -- Reviewer rules (JSONB — ReviewerRules schema)
    reviewer_rules      JSONB       NOT NULL DEFAULT '{}',
    -- Scheduling (JSONB — CampaignSchedule schema: window_start, window_end, days_of_week)
    schedule            JSONB       NOT NULL DEFAULT '{}',
    -- Stats (denormalised counters, updated by results insert)
    total_instances     INTEGER     NOT NULL DEFAULT 0,
    completed_instances INTEGER     NOT NULL DEFAULT 0,
    avg_score           NUMERIC(6,3),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by          TEXT        NOT NULL DEFAULT 'operator'
);

CREATE INDEX IF NOT EXISTS idx_evcampaigns_tenant
    ON evaluation.campaigns (tenant_id, pool_id, status);

CREATE INDEX IF NOT EXISTS idx_evcampaigns_form
    ON evaluation.campaigns (form_id);

-- ── EvaluationInstance ────────────────────────────────────────────────────────
-- One record per session scheduled for evaluation.
-- Status lifecycle: scheduled → assigned → in_progress → completed | expired | error
CREATE TABLE IF NOT EXISTS evaluation.instances (
    id                  TEXT        PRIMARY KEY,         -- "evinstance_{uuid}"
    tenant_id           TEXT        NOT NULL,
    campaign_id         TEXT        NOT NULL REFERENCES evaluation.campaigns(id),
    form_id             TEXT        NOT NULL REFERENCES evaluation.forms(id),
    session_id          TEXT        NOT NULL,
    segment_id          TEXT,                            -- ContactSegment for the evaluated agent
    evaluator_agent_id  TEXT,                            -- instance_id of the assigned evaluator
    reviewer_agent_id   TEXT,                            -- instance_id of assigned reviewer (if any)
    status              TEXT        NOT NULL DEFAULT 'scheduled'
                        CHECK (status IN (
                            'scheduled', 'assigned', 'in_progress',
                            'completed', 'under_review', 'reviewed',
                            'contested', 'locked', 'expired', 'error'
                        )),
    priority            INTEGER     NOT NULL DEFAULT 5,  -- 1=highest, 10=lowest
    scheduled_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    assigned_at         TIMESTAMPTZ,
    completed_at        TIMESTAMPTZ,
    expires_at          TIMESTAMPTZ,
    error_message       TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_evinstances_tenant_status
    ON evaluation.instances (tenant_id, status, scheduled_at);

CREATE INDEX IF NOT EXISTS idx_evinstances_session
    ON evaluation.instances (session_id);

CREATE INDEX IF NOT EXISTS idx_evinstances_campaign
    ON evaluation.instances (campaign_id, status);

-- ── EvaluationResult ─────────────────────────────────────────────────────────
-- The evaluator agent's final output for one instance.
-- One result per instance (UNIQUE constraint).
CREATE TABLE IF NOT EXISTS evaluation.results (
    id                  TEXT        PRIMARY KEY,         -- "evresult_{uuid}"
    tenant_id           TEXT        NOT NULL,
    instance_id         TEXT        NOT NULL REFERENCES evaluation.instances(id),
    session_id          TEXT        NOT NULL,
    campaign_id         TEXT        NOT NULL,
    form_id             TEXT        NOT NULL,
    evaluator_agent_id  TEXT        NOT NULL,
    -- Scores
    overall_score       NUMERIC(6,3),
    max_score           NUMERIC(6,3),
    normalized_score    NUMERIC(6,3),                    -- 0–1
    passed              BOOLEAN,                         -- NULL if no passing_score on form
    -- Evaluation metadata
    eval_status         TEXT        NOT NULL DEFAULT 'submitted'
                        CHECK (eval_status IN ('submitted', 'under_review', 'reviewed', 'contested', 'locked')),
    evaluator_notes     TEXT        NOT NULL DEFAULT '',
    comparison_mode     BOOLEAN     NOT NULL DEFAULT FALSE,
    comparison_report   JSONB,                           -- ComparisonReport if comparison_mode
    knowledge_snippets  JSONB       NOT NULL DEFAULT '[]', -- KnowledgeSnippet[] used
    -- Reviewer outcome
    reviewer_agent_id   TEXT,
    reviewer_outcome    TEXT        CHECK (reviewer_outcome IN ('approved', 'adjusted', 'rejected', NULL)),
    reviewer_notes      TEXT,
    reviewer_score      NUMERIC(6,3),
    reviewed_at         TIMESTAMPTZ,
    -- Contestation
    contested_by        TEXT,
    contested_at        TIMESTAMPTZ,
    contestation_reason TEXT,
    locked_at           TIMESTAMPTZ,
    locked_by           TEXT,
    -- Timing
    submitted_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_result_per_instance UNIQUE (instance_id)
);

CREATE INDEX IF NOT EXISTS idx_evresults_tenant
    ON evaluation.results (tenant_id, submitted_at DESC);

CREATE INDEX IF NOT EXISTS idx_evresults_campaign
    ON evaluation.results (campaign_id, submitted_at DESC);

CREATE INDEX IF NOT EXISTS idx_evresults_session
    ON evaluation.results (session_id);

-- ── EvaluationCriterionResponse ───────────────────────────────────────────────
-- Per-criterion scores for one result.
CREATE TABLE IF NOT EXISTS evaluation.criterion_responses (
    id              TEXT        PRIMARY KEY,             -- "evcrr_{uuid}"
    result_id       TEXT        NOT NULL REFERENCES evaluation.results(id) ON DELETE CASCADE,
    instance_id     TEXT        NOT NULL,
    campaign_id     TEXT        NOT NULL,
    tenant_id       TEXT        NOT NULL,
    criterion_id    TEXT        NOT NULL,
    criterion_name  TEXT        NOT NULL DEFAULT '',
    dimension_id    TEXT        NOT NULL DEFAULT '',
    na              BOOLEAN     NOT NULL DEFAULT FALSE,
    score           NUMERIC(6,3),
    max_score       NUMERIC(6,3),
    boolean_value   BOOLEAN,
    choice_value    TEXT,
    text_value      TEXT,
    notes           TEXT,
    evidence        JSONB       NOT NULL DEFAULT '[]',   -- EvidenceRef[]
    weight          NUMERIC(6,3) NOT NULL DEFAULT 1.0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_evcrr_result
    ON evaluation.criterion_responses (result_id);

CREATE INDEX IF NOT EXISTS idx_evcrr_campaign_criterion
    ON evaluation.criterion_responses (campaign_id, criterion_id);

-- ── EvaluationContestation ────────────────────────────────────────────────────
-- Human agent contests an evaluation result; supervisor reviews.
CREATE TABLE IF NOT EXISTS evaluation.contestations (
    id                  TEXT        PRIMARY KEY,         -- "evcontest_{uuid}"
    tenant_id           TEXT        NOT NULL,
    result_id           TEXT        NOT NULL REFERENCES evaluation.results(id),
    instance_id         TEXT        NOT NULL,
    session_id          TEXT        NOT NULL,
    -- Who filed
    contested_by        TEXT        NOT NULL,            -- agent instance_id or user_id
    contested_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    contestation_reason TEXT        NOT NULL DEFAULT '',
    -- Adjudication
    status              TEXT        NOT NULL DEFAULT 'open'
                        CHECK (status IN ('open', 'under_review', 'accepted', 'rejected', 'withdrawn')),
    adjudicated_by      TEXT,
    adjudicated_at      TIMESTAMPTZ,
    adjudication_notes  TEXT,
    adjusted_score      NUMERIC(6,3),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_evcontest_result
    ON evaluation.contestations (result_id);

CREATE INDEX IF NOT EXISTS idx_evcontest_tenant_status
    ON evaluation.contestations (tenant_id, status, contested_at DESC);

-- ── Arc 6 v2 migrations (idempotent ALTER TABLE) ──────────────────────────────

-- evaluation.campaigns: workflow skill reference + contestation policy
ALTER TABLE evaluation.campaigns
    ADD COLUMN IF NOT EXISTS review_workflow_skill_id TEXT,
    ADD COLUMN IF NOT EXISTS contestation_policy JSONB NOT NULL DEFAULT '{}';

-- evaluation.campaigns: evaluation pool + calendar + gateway configs (Task #74/#75)
ALTER TABLE evaluation.campaigns
    ADD COLUMN IF NOT EXISTS evaluation_pool_id     TEXT,           -- pool being evaluated (sampling filter)
    ADD COLUMN IF NOT EXISTS evaluation_calendar_id TEXT,           -- calendar for SLA + scheduling windows
    ADD COLUMN IF NOT EXISTS gateway_config_ids     TEXT[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS evaluator_pool         TEXT;           -- S2.2: pool do AGENTE avaliador (null = default global)

-- evaluation.results: workflow motor state tracking
ALTER TABLE evaluation.results
    ADD COLUMN IF NOT EXISTS workflow_instance_id TEXT,
    ADD COLUMN IF NOT EXISTS resume_token TEXT,
    ADD COLUMN IF NOT EXISTS action_required TEXT
        CHECK (action_required IN ('review', 'contestation')),
    ADD COLUMN IF NOT EXISTS current_round INT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS deadline_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS lock_reason TEXT;

-- evaluation.contestations: round + authority tracking
ALTER TABLE evaluation.contestations
    ADD COLUMN IF NOT EXISTS round_number INT NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS authority_level TEXT;

-- evaluation.permissions table removed: permissions are now handled via
-- ABAC module_config in the auth-api JWT (module_config.evaluation.revisar /
-- module_config.evaluation.contestar). Drop the legacy table if it exists.
DROP TABLE IF EXISTS evaluation.permissions;

-- ── Arc 13 Fase B migrations ──────────────────────────────────────────────────

-- evaluation.instances: session_metrics computed by SessionMetricsExtractor (Arc 13 Fase B)
ALTER TABLE evaluation.instances
    ADD COLUMN IF NOT EXISTS session_metrics JSONB;

-- R9d: deploy_version (versão do skill que rodou no segmento avaliado, AI). Insumo da
-- cota por versão (R10). Resolvido do segmento na amostragem.
ALTER TABLE evaluation.instances
    ADD COLUMN IF NOT EXISTS deploy_version TEXT;

-- ── Arc 13 Fase A migrations ──────────────────────────────────────────────────

-- evaluation.campaigns: pre-review and contestation policy fields (Arc 13)
ALTER TABLE evaluation.campaigns
    ADD COLUMN IF NOT EXISTS pre_review_enabled     BOOLEAN  NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS pre_review_agent_pool  TEXT;

-- T17 — janela de DADOS da campanha (quais sessões entram, por closed_at). Ortogonal ao
-- `schedule` (quando o avaliador roda). NULL = aberto. Forward: filtro no sampling;
-- backfill (start no passado) = job batch (follow-up). Spec §18.5.
ALTER TABLE evaluation.campaigns
    ADD COLUMN IF NOT EXISTS period_start TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS period_end   TIMESTAMPTZ;

-- evaluation.results: contestation state machine + pre_review tracking (Arc 13)
ALTER TABLE evaluation.results
    ADD COLUMN IF NOT EXISTS contestation_state  TEXT
        CHECK (contestation_state IN (
            'pre_review_pending', 'contestation_open', 'under_review',
            'timeout_contestation', 'timeout_review', 'closed_upheld', 'closed_revised'
        )),
    ADD COLUMN IF NOT EXISTS pre_review_complete BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS evaluated_agent_type TEXT
        CHECK (evaluated_agent_type IN ('human_agent', 'ai_agent')),
    ADD COLUMN IF NOT EXISTS finalized_at        TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS final_score         NUMERIC(6,3),
    ADD COLUMN IF NOT EXISTS process_duration_ms BIGINT;

-- ── T1 — Modelo de estado canônico (docs/product/evaluation-reconciliation-spec.md §13.1)
-- Migração idempotente (opção b): DDL explícito guardado, sem framework novo.
-- Colapsa contestation_state/eval_status/action_required no canônico result_state +
-- finalize_reason + round. Fase 1 (aditiva + backfill); as funções escritoras passam a
-- gravar result_state num passo seguinte. contestation_state segue como legado durante a
-- transição (eval_status mantém-se como espelho depreciado — decisão A).
ALTER TABLE evaluation.results
    ADD COLUMN IF NOT EXISTS result_state    TEXT,
    ADD COLUMN IF NOT EXISTS finalize_reason TEXT,
    ADD COLUMN IF NOT EXISTS round           SMALLINT NOT NULL DEFAULT 1;

-- Backfill a partir do legado (roda uma vez; guardado por result_state ainda NULL).
UPDATE evaluation.results SET
    result_state = CASE
        WHEN contestation_state IN (
            'auto_finalized','closed_upheld','closed_revised','closed_max_rounds',
            'timeout_contestation','timeout_review') THEN 'finalized'
        WHEN contestation_state = 'pre_review_pending' THEN 'ai_review'
        WHEN contestation_state = 'contestation_open'  THEN 'open'
        WHEN contestation_state = 'under_review'        THEN 'under_review'
        WHEN evaluated_agent_type = 'ai_agent'          THEN 'finalized'
        ELSE 'open'
    END,
    finalize_reason = CASE
        WHEN contestation_state = 'auto_finalized'       THEN 'auto_ai'
        WHEN contestation_state = 'closed_upheld'        THEN 'upheld'
        WHEN contestation_state = 'closed_revised'       THEN 'revised'
        WHEN contestation_state = 'closed_max_rounds'    THEN 'max_rounds'
        WHEN contestation_state = 'timeout_contestation' THEN 'contest_timeout'
        WHEN contestation_state = 'timeout_review'       THEN 'review_timeout'
        WHEN evaluated_agent_type = 'ai_agent'           THEN 'auto_ai'
        ELSE NULL
    END,
    round = COALESCE(NULLIF(current_round, 0), 1)
WHERE result_state IS NULL;

-- CHECKs nomeados (idempotentes via pg_constraint).
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_result_state') THEN
        ALTER TABLE evaluation.results ADD CONSTRAINT chk_result_state
            CHECK (result_state IS NULL OR result_state IN
                ('ai_review','open','under_review','finalized','error_rejected'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_finalize_reason') THEN
        ALTER TABLE evaluation.results ADD CONSTRAINT chk_finalize_reason
            CHECK (finalize_reason IS NULL OR finalize_reason IN
                ('auto_ai','uncontested','upheld','revised','max_rounds',
                 'contest_timeout','review_timeout'));
    END IF;
END $$;

-- T1/§2.2 — o CHECK legado de contestation_state não incluía os valores que as
-- escritoras gravam (auto_finalized via ingest IA; closed_max_rounds via submit_review)
-- → CheckViolation latente. contestation_state é ESPELHO DEPRECADO (decisão A); recria-se
-- o CHECK permissivo p/ não bloquear o fluxo canônico (a verdade é result_state/chk_result_state).
DO $$ BEGIN
    ALTER TABLE evaluation.results DROP CONSTRAINT IF EXISTS results_contestation_state_check;
    ALTER TABLE evaluation.results ADD CONSTRAINT results_contestation_state_check
        CHECK (contestation_state IS NULL OR contestation_state IN (
            'pre_review_pending','contestation_open','under_review',
            'timeout_contestation','timeout_review','closed_upheld','closed_revised',
            'auto_finalized','closed_max_rounds'));
END $$;

-- instances.status: adicionar 'skipped' (thin-session). Mantém os estados legados
-- (under_review/reviewed/contested/locked) por compat com linhas existentes; a remoção
-- deles é cleanup posterior, quando nada mais os escrever.
DO $$ BEGIN
    ALTER TABLE evaluation.instances DROP CONSTRAINT IF EXISTS instances_status_check;
    ALTER TABLE evaluation.instances DROP CONSTRAINT IF EXISTS chk_instance_status;
    ALTER TABLE evaluation.instances ADD CONSTRAINT chk_instance_status CHECK (
        status IN ('scheduled','assigned','in_progress','completed','skipped',
                   'expired','error',
                   'under_review','reviewed','contested','locked'));  -- legado
END $$;

-- ── T2 — Chave por segmento + form_version pin (spec §13.2) ───────────────────
ALTER TABLE evaluation.instances
    ADD COLUMN IF NOT EXISTS evaluated_user_id TEXT,
    ADD COLUMN IF NOT EXISTS form_version      INTEGER NOT NULL DEFAULT 1;
ALTER TABLE evaluation.results
    ADD COLUMN IF NOT EXISTS segment_id        TEXT,
    ADD COLUMN IF NOT EXISTS evaluated_user_id TEXT,
    ADD COLUMN IF NOT EXISTS form_version      INTEGER NOT NULL DEFAULT 1;
-- Unicidade por segmento (linhas legadas têm segment_id NULL → fora do índice).
CREATE UNIQUE INDEX IF NOT EXISTS uq_evinstance_campaign_segment
    ON evaluation.instances (campaign_id, segment_id) WHERE segment_id IS NOT NULL;

-- ── T15 — dispatcher por janela de calendário (spec §18.4) ────────────────────
-- `dispatched_at`: carimbo do último despacho windowed (evaluation.requested emitido).
-- Idempotência do scanner: instances `scheduled` só são re-despachadas após o cooldown
-- (não re-despacha assigned/in_progress, já fora do filtro status='scheduled'). NULL =
-- nunca despachada pelo scanner. O dispatch manual ("Rodar agora") NÃO mexe nesse campo.
ALTER TABLE evaluation.instances
    ADD COLUMN IF NOT EXISTS dispatched_at TIMESTAMPTZ;

-- ── ContestationThread (Arc 13) ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS evaluation.contestation_threads (
    id                      TEXT        PRIMARY KEY,        -- "evthread_{uuid}"
    tenant_id               TEXT        NOT NULL,
    evaluation_instance_id  TEXT        NOT NULL REFERENCES evaluation.instances(id),
    dimension_id            TEXT        NOT NULL,           -- dimension_id or criterion_id (fallback)
    round                   INTEGER     NOT NULL,
    author_type             TEXT        NOT NULL
        CHECK (author_type IN (
            'evaluator_ai', 'pre_reviewer_ai', 'human_agent',
            'reviewer_ai', 'human_reviewer'
        )),
    author_id               TEXT        NOT NULL,
    text                    TEXT        NOT NULL DEFAULT '',
    decision                TEXT        CHECK (decision IN ('upheld', 'revised')),
    score_override          NUMERIC(6,3),
    evidence_entries        JSONB       NOT NULL DEFAULT '[]',  -- EvidenceEntry[]
    calibration_signal      JSONB,                              -- CalibrationSignal | NULL
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_evthreads_instance
    ON evaluation.contestation_threads (evaluation_instance_id, dimension_id, round);

CREATE INDEX IF NOT EXISTS idx_evthreads_tenant
    ON evaluation.contestation_threads (tenant_id, created_at DESC);

-- ── CurationReview (Arc 13) ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS evaluation.curation_reviews (
    id                      TEXT        PRIMARY KEY,        -- "evcuration_{uuid}"
    tenant_id               TEXT        NOT NULL,
    evaluation_instance_id  TEXT        NOT NULL REFERENCES evaluation.instances(id),
    trigger                 TEXT        NOT NULL,           -- "sampling_rule:{name}" | "reviewer_signal" | combined
    curator_id              TEXT,                           -- user_id (NULL = unassigned)
    status                  TEXT        NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'recalibrated', 'bias_flagged')),
    curator_notes           TEXT,
    calibration_note_id     TEXT,                           -- FK → calibration_notes.id (set after creation)
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at             TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_evcuration_tenant_status
    ON evaluation.curation_reviews (tenant_id, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_evcuration_instance
    ON evaluation.curation_reviews (evaluation_instance_id);

-- ── CalibrationNote (Arc 13) ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS evaluation.calibration_notes (
    id               TEXT        PRIMARY KEY,               -- "evcalnote_{uuid}"
    tenant_id        TEXT        NOT NULL,
    campaign_id      TEXT        NOT NULL REFERENCES evaluation.campaigns(id),
    dimension_id     TEXT        NOT NULL,
    evaluator_id     TEXT        NOT NULL,                  -- agent_type_id
    skill_version    TEXT        NOT NULL,
    text             TEXT        NOT NULL,
    severity         TEXT        NOT NULL DEFAULT 'low'
        CHECK (severity IN ('low', 'medium', 'high')),
    published_to_kb  BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_evcalnotes_campaign
    ON evaluation.calibration_notes (campaign_id, evaluator_id, created_at DESC);

-- T14 (c) — criterion_id: ancora a nota de calibração no CRITÉRIO implicado (não só na
-- dimensão), p/ o RAG injetar a orientação no bloco do critério certo (spec §6/§18.3).
-- Nullable: notas legadas têm null; dimension_id mantém-se p/ retrocompat.
ALTER TABLE evaluation.calibration_notes
    ADD COLUMN IF NOT EXISTS criterion_id TEXT;

-- ── CurationSamplingRule (Arc 13) ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS evaluation.curation_sampling_rules (
    id           TEXT        PRIMARY KEY,                   -- "evcsrule_{uuid}"
    tenant_id    TEXT        NOT NULL,
    campaign_id  TEXT        NOT NULL REFERENCES evaluation.campaigns(id),
    rule_type    TEXT        NOT NULL
        CHECK (rule_type IN (
            'score_extremes', 'deploy_baseline', 'score_outlier',
            'na_excess', 'random_baseline', 'reviewer_signal'
        )),
    params       JSONB       NOT NULL DEFAULT '{}',
    enabled      BOOLEAN     NOT NULL DEFAULT TRUE,
    priority     INTEGER     NOT NULL DEFAULT 10,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_evcsrules_campaign
    ON evaluation.curation_sampling_rules (campaign_id, enabled, priority ASC);

-- ── T6b — Form deploy lifecycle + immutable version snapshots (spec §16.1) ─────
-- deploy_status (draft|published) espelha o Skill Deploy Lifecycle (CLAUDE.md).
-- `status` (draft/active/archived) é ortogonal (ciclo de listagem/arquivamento).
ALTER TABLE evaluation.forms
    ADD COLUMN IF NOT EXISTS deploy_status TEXT NOT NULL DEFAULT 'draft';
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='forms_deploy_status_check') THEN
        ALTER TABLE evaluation.forms
            ADD CONSTRAINT forms_deploy_status_check
            CHECK (deploy_status IN ('draft','published'));
    END IF;
END $$;

-- Snapshot imutável da definição por versão publicada. Instances pinam (form_id, version);
-- avaliações em curso leem o snapshot da versão sob a qual nasceram (consumo: T7).
CREATE TABLE IF NOT EXISTS evaluation.form_versions (
    form_id       TEXT         NOT NULL REFERENCES evaluation.forms(id),
    tenant_id     TEXT         NOT NULL,
    version       INTEGER      NOT NULL,
    name          TEXT         NOT NULL,
    description   TEXT         NOT NULL DEFAULT '',
    dimensions    JSONB        NOT NULL DEFAULT '[]',   -- snapshot imutável da definição
    total_weight  NUMERIC(6,3) NOT NULL DEFAULT 1.0,
    passing_score NUMERIC(6,3),
    scoring_method TEXT,
    published_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    published_by  TEXT         NOT NULL DEFAULT 'operator',
    PRIMARY KEY (form_id, version)
);
CREATE INDEX IF NOT EXISTS idx_evformversions_tenant
    ON evaluation.form_versions (tenant_id, form_id, version DESC);

-- ── T8-A — Rubrica-template (spec §16.3) ──────────────────────────────────────
-- Instruções gerais de avaliação (como pontuar 0/5/10, citar evidência por
-- stream_entry_id, N/A, anti-viés) — fonte única do prompt, fora do ai-gateway
-- (que segue stateless). Default por tenant + override por campanha; versionada
-- (snapshot imutável), espelhando o lifecycle de forms (T6b) e ancorando deploy epochs.
CREATE TABLE IF NOT EXISTS evaluation.rubric_templates (
    id            TEXT        PRIMARY KEY,                 -- "evrubric_{uuid}"
    tenant_id     TEXT        NOT NULL,
    scope         TEXT        NOT NULL DEFAULT 'tenant'
                  CHECK (scope IN ('tenant','campaign')),
    campaign_id   TEXT,                                    -- NULL p/ default; set p/ override
    name          TEXT        NOT NULL DEFAULT 'Rubric template',
    body          TEXT        NOT NULL DEFAULT '',         -- texto das instruções gerais
    version       INTEGER     NOT NULL DEFAULT 1,
    deploy_status TEXT        NOT NULL DEFAULT 'draft'
                  CHECK (deploy_status IN ('draft','published')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by    TEXT        NOT NULL DEFAULT 'operator'
);
-- Um default por tenant; um override por campanha (índices parciais únicos).
CREATE UNIQUE INDEX IF NOT EXISTS uq_rubric_tenant_default
    ON evaluation.rubric_templates (tenant_id) WHERE scope='tenant';
CREATE UNIQUE INDEX IF NOT EXISTS uq_rubric_campaign
    ON evaluation.rubric_templates (campaign_id) WHERE scope='campaign' AND campaign_id IS NOT NULL;

-- Snapshot imutável da rubrica por versão publicada (avaliações pinam a versão).
CREATE TABLE IF NOT EXISTS evaluation.rubric_template_versions (
    rubric_id     TEXT        NOT NULL REFERENCES evaluation.rubric_templates(id),
    tenant_id     TEXT        NOT NULL,
    version       INTEGER     NOT NULL,
    name          TEXT        NOT NULL,
    body          TEXT        NOT NULL,
    published_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_by  TEXT        NOT NULL DEFAULT 'operator',
    PRIMARY KEY (rubric_id, version)
);
CREATE INDEX IF NOT EXISTS idx_evrubricversions_tenant
    ON evaluation.rubric_template_versions (tenant_id, rubric_id, version DESC);

-- ── R8c — Curadoria cega-primeiro (Estágio 2, §III.4) ─────────────────────────
-- A curadoria do avaliador IA ganha um MODO CEGO: o curador re-pontua o MESMO form
-- sem ver a nota da IA → reveal + diff por dimensão. A nota cega é um ARTEFATO DE
-- CALIBRAÇÃO — NUNCA altera evaluation.results.final_score nem re-emite
-- evaluation_finalized (decisão 2026-06-23): corrige avaliações FUTURAS via
-- CalibrationNote→KB→RAG e alimenta a divergência (R8b), não esta avaliação.
--   mode          — discrimina a tarefa do curador ('standard' = approve/recalibrate/bias
--                   por texto livre, fluxo Arc 13; 'blind' = re-pontuação cega R8c).
--   deadline_at   — SLA SOFT do curador. Expirar = higiene de fila (sem consequência
--                   para a avaliação), distinto do timeout do revisor de contestação
--                   (que trava a nota como não-revisada).
--   expired_at    — carimbo informativo de expiração do SLA soft.
--   skill_version — versão avaliada (chave de divergência por versão / estratos de amostragem).
ALTER TABLE evaluation.curation_reviews
    ADD COLUMN IF NOT EXISTS mode          TEXT NOT NULL DEFAULT 'standard',
    ADD COLUMN IF NOT EXISTS deadline_at   TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS expired_at    TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS skill_version TEXT;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='curation_reviews_mode_check') THEN
        ALTER TABLE evaluation.curation_reviews
            ADD CONSTRAINT curation_reviews_mode_check
            CHECK (mode IN ('standard','blind'));
    END IF;
END $$;

-- Fila do curador filtrada por modo cego + SLA (varredura de expiração soft).
CREATE INDEX IF NOT EXISTS idx_evcuration_blind_open
    ON evaluation.curation_reviews (tenant_id, mode, status, deadline_at)
    WHERE mode = 'blind';

-- Re-pontuação CEGA do curador — 1:1 com a curation_review de mode='blind'.
-- Guarda APENAS o artefato de calibração: nunca toca o resultado imutável.
-- O snapshot da nota IA (ai_*) é gravado no REVEAL para diff reproduzível.
CREATE TABLE IF NOT EXISTS evaluation.curation_result_blinds (
    id                        TEXT        PRIMARY KEY,           -- "evblind_{uuid}"
    tenant_id                 TEXT        NOT NULL,
    curation_review_id        TEXT        NOT NULL REFERENCES evaluation.curation_reviews(id),
    evaluation_instance_id    TEXT        NOT NULL,
    campaign_id               TEXT        NOT NULL,
    curator_id                TEXT        NOT NULL,              -- humano que re-pontuou cego
    blind_criterion_responses JSONB       NOT NULL DEFAULT '[]', -- respostas do humano (shape de criterion_responses)
    blind_overall_score       NUMERIC(6,3),                     -- recomputado por scoring.aggregate_scores (0..10)
    blind_by_dimension        JSONB       NOT NULL DEFAULT '[]', -- [{dimension_id, score}]
    ai_overall_score          NUMERIC(6,3),                     -- snapshot da nota IA no reveal (0..10)
    ai_by_dimension           JSONB       NOT NULL DEFAULT '[]', -- snapshot por dimensão da IA (0..10)
    per_dimension_diffs       JSONB       NOT NULL DEFAULT '[]', -- [{dimension_id, ai_score, human_score, diff, disagree}]
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_blind_per_review UNIQUE (curation_review_id)
);
CREATE INDEX IF NOT EXISTS idx_evblind_instance
    ON evaluation.curation_result_blinds (evaluation_instance_id);
CREATE INDEX IF NOT EXISTS idx_evblind_campaign
    ON evaluation.curation_result_blinds (tenant_id, campaign_id, created_at DESC);
"""


# ─── Survey response store (schema `survey`) — ADR adr-survey-response-store ────
# Store operacional POR-RESPOSTA (verbatim/áudio LGPD) que S8/S9 exigem e o
# ClickHouse (session_signal, numérico/agregado) não comporta. Schema PG dedicado,
# aditivo idempotente (mesma convenção do _DDL). §7.2 podado: definições=DialogForm
# (dialog-api), quarentena=contact_policy (mailing-api) — aqui só instância+resposta.
_SURVEY_DDL = """
CREATE SCHEMA IF NOT EXISTS survey;

CREATE TABLE IF NOT EXISTS survey.survey_instance (
  instance_id        TEXT PRIMARY KEY,
  tenant_id          TEXT NOT NULL,
  idempotency_key    TEXT NOT NULL,
  survey_id          TEXT,
  origin_session_id  TEXT,
  grain              TEXT NOT NULL,
  segment_id         TEXT,
  agent_key          TEXT,
  pool_id            TEXT,
  customer_key       TEXT,
  channel            TEXT,
  survey_session_id  TEXT,
  status             TEXT NOT NULL DEFAULT 'responded',
  session_at         TIMESTAMPTZ,
  responded_at       TIMESTAMPTZ,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_survey_instance_idem UNIQUE (tenant_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS ix_survey_instance_customer
  ON survey.survey_instance (tenant_id, customer_key);
CREATE INDEX IF NOT EXISTS ix_survey_instance_origin
  ON survey.survey_instance (tenant_id, origin_session_id);

CREATE TABLE IF NOT EXISTS survey.survey_response (
  response_id      TEXT PRIMARY KEY,
  instance_id      TEXT NOT NULL
                     REFERENCES survey.survey_instance(instance_id) ON DELETE CASCADE,
  tenant_id        TEXT NOT NULL,
  signals          JSONB NOT NULL DEFAULT '[]',
  open_text        TEXT,
  verbatims        JSONB NOT NULL DEFAULT '[]',
  audio_ref        TEXT,
  transcript_ref   TEXT,
  response_channel TEXT,
  responded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_survey_response_instance UNIQUE (instance_id)
);
CREATE INDEX IF NOT EXISTS ix_survey_response_tenant
  ON survey.survey_response (tenant_id, responded_at);
"""


async def ensure_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_DDL)
        await conn.execute(_SURVEY_DDL)
    logger.info("evaluation schema ensured")


# ─── Helper ───────────────────────────────────────────────────────────────────

def _parse_jsonb(val: Any) -> Any:
    """asyncpg returns JSONB columns as raw strings — parse them back to Python objects."""
    if isinstance(val, str) and val and val[0] in ('{', '['):
        try:
            import json as _json
            return _json.loads(val)
        except Exception:
            pass
    return val


def _row(record: asyncpg.Record | None) -> dict[str, Any] | None:
    if record is None:
        return None
    return {k: _parse_jsonb(v) for k, v in dict(record).items()}


def _rows(records: list[asyncpg.Record]) -> list[dict[str, Any]]:
    return [_row(r) for r in records]  # type: ignore[misc]


def _new_id(prefix: str) -> str:
    return f"{prefix}{uuid4().hex}"


def _parse_ts(v: Any) -> Any:
    """T17 — ISO string → datetime (asyncpg exige datetime p/ TIMESTAMPTZ, não str).
    datetime passa direto; None/inválido → None."""
    if v is None or isinstance(v, datetime):
        return v
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except Exception:
        return None


# ─── Survey response store — persistência por-resposta (S8/S9) ──────────────────

async def persist_survey_response(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    idempotency_key: str,
    grain: str,
    survey_id: str | None = None,
    origin_session_id: str | None = None,
    segment_id: str | None = None,
    agent_key: str | None = None,
    pool_id: str | None = None,
    customer_key: str | None = None,
    channel: str | None = None,
    survey_session_id: str | None = None,
    signals: list[dict] | None = None,
    open_text: str | None = None,
    verbatims: list[dict] | None = None,
    audio_ref: str | None = None,
    transcript_ref: str | None = None,
    response_channel: str | None = None,
    session_at: Any = None,
    responded_at: Any = None,
) -> dict[str, Any]:
    """Persiste a resposta operacional por-resposta (verbatim/áudio LGPD — vivem SÓ
    aqui, nunca no session_signal analítico). Idempotente: instância por
    (tenant_id, idempotency_key), resposta por (instance_id). Replay = created:false,
    sem duplicar. Transação: instância upsert → resposta insert."""
    instance_id = _new_id("svi_")
    response_id = _new_id("svr_")
    _session_at = _parse_ts(session_at)
    _responded_at = _parse_ts(responded_at)
    async with pool.acquire() as conn:
        async with conn.transaction():
            inst = await conn.fetchrow(
                """
                INSERT INTO survey.survey_instance
                    (instance_id, tenant_id, idempotency_key, survey_id, origin_session_id,
                     grain, segment_id, agent_key, pool_id, customer_key, channel,
                     survey_session_id, status, session_at, responded_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,'responded',
                        COALESCE($13, now()), COALESCE($14, now()))
                ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
                RETURNING instance_id
                """,
                instance_id, tenant_id, idempotency_key, survey_id, origin_session_id,
                grain, segment_id, agent_key, pool_id, customer_key, channel,
                survey_session_id, _session_at, _responded_at,
            )
            if inst is None:  # já existia → replay idempotente
                inst = await conn.fetchrow(
                    "SELECT instance_id FROM survey.survey_instance"
                    " WHERE tenant_id = $1 AND idempotency_key = $2",
                    tenant_id, idempotency_key,
                )
            instance_id = inst["instance_id"]
            resp = await conn.fetchrow(
                """
                INSERT INTO survey.survey_response
                    (response_id, instance_id, tenant_id, signals, open_text, verbatims,
                     audio_ref, transcript_ref, response_channel, responded_at)
                VALUES ($1,$2,$3,$4::jsonb,$5,$6::jsonb,$7,$8,$9, COALESCE($10, now()))
                ON CONFLICT (instance_id) DO NOTHING
                RETURNING response_id
                """,
                response_id, instance_id, tenant_id,
                json.dumps(signals or []), open_text, json.dumps(verbatims or []),
                audio_ref, transcript_ref, response_channel, _responded_at,
            )
            created = resp is not None
            if resp is None:
                resp = await conn.fetchrow(
                    "SELECT response_id FROM survey.survey_response WHERE instance_id = $1",
                    instance_id,
                )
            response_id = resp["response_id"]
    return {"instance_id": instance_id, "response_id": response_id, "created": created}


async def list_survey_responses(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    from_dt: str | None = None,
    to_dt: str | None = None,
    grain: str | None = None,
    pool_id: str | None = None,
    survey_id: str | None = None,
    metric: str | None = None,
    accessible_pools: list[str] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """S8 — navegador de respostas: lista survey_response JOIN survey_instance (verbatim
    incluído — LGPD, gateado no router). Filtros opcionais; pool-scope por accessible_pools;
    ordenado por responded_at DESC; paginado. `metric` filtra por sinal presente (jsonb @>)."""
    # `[]` = chamador sem NENHUM pool ⇒ nada a devolver; `None` = irrestrito, segue.
    # Sem esta guarda o `if accessible_pools:` abaixo simplesmente não adiciona filtro,
    # e o passo 3 (`LEGACY_EMPTY_MEANS_UNRESTRICTED = False`) converteria "nenhum acesso"
    # em acesso a TUDO, sem erro nem log. Ver `pending.md` AUT-05.
    if accessible_pools is not None and not accessible_pools:
        return {"data": [], "total": 0, "limit": limit, "offset": offset}

    conds = ["i.tenant_id = $1"]
    args: list[Any] = [tenant_id]
    def ph(v: Any) -> str:
        args.append(v)
        return f"${len(args)}"
    if from_dt:   conds.append(f"r.responded_at >= {ph(_parse_ts(from_dt))}")
    if to_dt:
        # `to_dt` date-only (YYYY-MM-DD) = fim do dia INCLUSIVO: sem isto, o limite cai à
        # meia-noite e as respostas de HOJE ficam de fora (bug do 1º render). Espelha o
        # _ch_fmt(upper=True) do analytics-api.
        _to = _parse_ts(to_dt)
        if _to is not None and len(to_dt) <= 10:
            _to = _to + timedelta(days=1)
        conds.append(f"r.responded_at < {ph(_to)}")
    if grain:     conds.append(f"i.grain = {ph(grain)}")
    if pool_id:   conds.append(f"i.pool_id = {ph(pool_id)}")
    if survey_id: conds.append(f"i.survey_id = {ph(survey_id)}")
    if metric:    conds.append(f"r.signals @> {ph(json.dumps([{'metric': metric}]))}::jsonb")
    if accessible_pools:
        conds.append("i.pool_id IN (" + ", ".join(ph(p) for p in accessible_pools) + ")")
    where = " AND ".join(conds)
    async with pool.acquire() as conn:
        total = await conn.fetchval(
            f"SELECT count(*) FROM survey.survey_response r "
            f"JOIN survey.survey_instance i ON i.instance_id = r.instance_id WHERE {where}",
            *args,
        )
        rows = await conn.fetch(
            f"""SELECT r.response_id, r.instance_id, r.signals, r.open_text, r.verbatims,
                       r.audio_ref, r.transcript_ref, r.response_channel, r.responded_at,
                       i.survey_id, i.grain, i.origin_session_id, i.segment_id,
                       i.agent_key, i.pool_id, i.customer_key, i.channel, i.session_at
                  FROM survey.survey_response r
                  JOIN survey.survey_instance i ON i.instance_id = r.instance_id
                 WHERE {where}
                 ORDER BY r.responded_at DESC
                 LIMIT {ph(limit)} OFFSET {ph(offset)}""",
            *args,
        )
    return {"data": _rows(rows), "total": int(total or 0), "limit": limit, "offset": offset}


# ─── T6a — form criterion model normalization (migration-without-rewrite) ───────
# The criterion model is enriched in spec §5.3, but legacy forms only carry
# label/description/max_score/type. Rather than rewrite stored forms, we fill the
# derived/default fields ON READ so every consumer (FormsPage, evaluator context,
# aggregation) sees a complete criterion. Non-destructive: only the returned dict
# is enriched; the stored JSONB is untouched. Derivation mirrors the @plughub/
# schemas helpers (deriveContestable / deriveEvidenceRequired).

def _derive_contestable(ctype: str) -> bool:
    return ctype != "auto_computed"


def _derive_evidence_required(ctype: str) -> bool:
    return ctype in ("score", "boolean")


def normalize_form(form: dict[str, Any] | None) -> dict[str, Any] | None:
    """Fill derived/default criterion fields on a form read. Walks the nested
    dimensions[].criteria[] model. Idempotent and non-destructive."""
    if not form:
        return form
    dims = form.get("dimensions")
    if not isinstance(dims, list):
        return form
    for dim in dims:
        if not isinstance(dim, dict):
            continue
        crits = dim.get("criteria")
        if not isinstance(crits, list):
            continue
        for c in crits:
            if not isinstance(c, dict):
                continue
            ctype = c.get("type") or "score"
            c["type"] = ctype
            if c.get("question") is None:
                c["question"] = c.get("description")
            c.setdefault("scoring_guidance", None)
            c.setdefault("na_guidance", None)
            c.setdefault("applies_when", None)
            c.setdefault("min_score", 0)
            if c.get("evidence_required") is None:
                c["evidence_required"] = _derive_evidence_required(ctype)
            if c.get("contestable") is None:
                c["contestable"] = _derive_contestable(ctype)
    return form


# ─── Forms CRUD ───────────────────────────────────────────────────────────────

async def create_form(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    name: str,
    description: str = "",
    dimensions: list[dict] | None = None,
    total_weight: float = 1.0,
    passing_score: float | None = None,
    allow_na: bool = True,
    knowledge_domains: list[str] | None = None,
    created_by: str = "operator",
) -> dict[str, Any]:
    form_id = _new_id("evform_")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO evaluation.forms
                (id, tenant_id, name, description, dimensions, total_weight,
                 passing_score, allow_na, knowledge_domains, created_by)
            VALUES ($1,$2,$3,$4,$5::jsonb,$6,$7,$8,$9,$10)
            RETURNING *
            """,
            form_id, tenant_id, name, description,
            json.dumps(dimensions or []),
            total_weight, passing_score, allow_na,
            knowledge_domains or [], created_by,
        )
    return normalize_form(_row(row))  # type: ignore[return-value]


async def get_form(pool: asyncpg.Pool, form_id: str, tenant_id: str) -> dict[str, Any] | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM evaluation.forms WHERE id=$1 AND tenant_id=$2",
            form_id, tenant_id,
        )
    return normalize_form(_row(row))


async def list_forms(
    pool: asyncpg.Pool,
    tenant_id: str,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    cond = "WHERE tenant_id=$1"
    args: list[Any] = [tenant_id]
    if status:
        cond += " AND status=$2"
        args.append(status)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT * FROM evaluation.forms {cond} ORDER BY created_at DESC LIMIT ${len(args)+1} OFFSET ${len(args)+2}",
            *args, limit, offset,
        )
    return [normalize_form(r) for r in _rows(rows)]  # type: ignore[misc]


async def update_form(
    pool: asyncpg.Pool,
    form_id: str,
    tenant_id: str,
    **fields: Any,
) -> dict[str, Any] | None:
    allowed = {"name", "description", "dimensions", "total_weight", "passing_score",
               "allow_na", "knowledge_domains", "status", "deploy_status"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return await get_form(pool, form_id, tenant_id)

    # T6b — editar um form PUBLICADO bifurca um novo DRAFT (version+1), preservando o
    # snapshot publicado. Drafts editam in-place. (deploy_status explícito no corpo,
    # ex. a própria publicação, não dispara o fork.)
    if "deploy_status" not in updates:
        cur = await get_form(pool, form_id, tenant_id)
        if cur and cur.get("deploy_status") == "published":
            updates["deploy_status"] = "draft"
            updates["version"] = int(cur.get("version") or 1) + 1
            allowed = allowed | {"version"}

    set_parts = []
    args: list[Any] = []
    idx = 1
    for k, v in updates.items():
        if k == "dimensions":
            set_parts.append(f"{k}=${idx}::jsonb")
            args.append(json.dumps(v))
        else:
            set_parts.append(f"{k}=${idx}")
            args.append(v)
        idx += 1
    set_parts.append(f"updated_at=${idx}")
    args.append(datetime.utcnow())
    idx += 1
    args.extend([form_id, tenant_id])

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"UPDATE evaluation.forms SET {', '.join(set_parts)} "
            f"WHERE id=${idx} AND tenant_id=${idx+1} RETURNING *",
            *args,
        )
    return normalize_form(_row(row))


async def delete_form(pool: asyncpg.Pool, form_id: str, tenant_id: str) -> bool:
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM evaluation.forms WHERE id=$1 AND tenant_id=$2",
            form_id, tenant_id,
        )
    return result.split()[-1] != "0"


# ─── T6b — Form deploy lifecycle (publish + immutable version snapshots) ─────────

async def publish_form(
    pool: asyncpg.Pool, form_id: str, tenant_id: str, *, published_by: str = "operator",
) -> dict[str, Any] | None:
    """Snapshot imutável da definição corrente em form_versions (na versão atual) e
    marca o form como published. Idempotente: o snapshot é INSERT ON CONFLICT DO
    NOTHING (a versão publicada nunca muda); republicar a mesma versão é no-op."""
    async with pool.acquire() as conn:
        raw = await conn.fetchrow(
            "SELECT * FROM evaluation.forms WHERE id=$1 AND tenant_id=$2",
            form_id, tenant_id,
        )
        if raw is None:
            return None
        f = dict(raw)
        version = int(f.get("version") or 1)
        await conn.execute(
            """
            INSERT INTO evaluation.form_versions
                (form_id, tenant_id, version, name, description, dimensions,
                 total_weight, passing_score, scoring_method, published_by)
            VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7,$8,$9,$10)
            ON CONFLICT (form_id, version) DO NOTHING
            """,
            form_id, tenant_id, version, f.get("name") or "", f.get("description") or "",
            f.get("dimensions") if isinstance(f.get("dimensions"), str) else json.dumps(f.get("dimensions") or []),
            f.get("total_weight"), f.get("passing_score"), None, published_by,
        )
        row = await conn.fetchrow(
            "UPDATE evaluation.forms SET deploy_status='published', updated_at=now() "
            "WHERE id=$1 AND tenant_id=$2 RETURNING *",
            form_id, tenant_id,
        )
    return normalize_form(_row(row))


async def latest_published_version(
    pool: asyncpg.Pool, form_id: str, tenant_id: str,
) -> int | None:
    """Maior versão já publicada (snapshot) de um form; None se nunca publicado."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT max(version) AS v FROM evaluation.form_versions "
            "WHERE form_id=$1 AND tenant_id=$2",
            form_id, tenant_id,
        )
    return int(row["v"]) if row and row["v"] is not None else None


async def list_form_versions(
    pool: asyncpg.Pool, form_id: str, tenant_id: str,
) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM evaluation.form_versions "
            "WHERE form_id=$1 AND tenant_id=$2 ORDER BY version DESC",
            form_id, tenant_id,
        )
    return [normalize_form(r) for r in _rows(rows)]  # type: ignore[misc]


async def get_form_version(
    pool: asyncpg.Pool, form_id: str, tenant_id: str, version: int,
) -> dict[str, Any] | None:
    """Lê o snapshot imutável de uma versão. Fallback para o form vivo (normalizado)
    quando não há snapshot (forms legados nunca publicados)."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM evaluation.form_versions "
            "WHERE form_id=$1 AND tenant_id=$2 AND version=$3",
            form_id, tenant_id, version,
        )
    if row is not None:
        return normalize_form(_row(row))
    return await get_form(pool, form_id, tenant_id)


# ─── T8-A — Rubric templates (spec §16.3) ─────────────────────────────────────

async def get_rubric_template(
    pool: asyncpg.Pool, rubric_id: str, tenant_id: str,
) -> dict[str, Any] | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM evaluation.rubric_templates WHERE id=$1 AND tenant_id=$2",
            rubric_id, tenant_id,
        )
    return _row(row)


async def list_rubric_templates(
    pool: asyncpg.Pool, tenant_id: str, *, campaign_id: str | None = None,
) -> list[dict[str, Any]]:
    """Lista a default do tenant + (se houver) o override da campanha pedida."""
    cond = "WHERE tenant_id=$1"
    args: list[Any] = [tenant_id]
    if campaign_id is not None:
        args.append(campaign_id)
        cond += f" AND (scope='tenant' OR campaign_id=${len(args)})"
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT * FROM evaluation.rubric_templates {cond} ORDER BY scope ASC, created_at DESC",
            *args,
        )
    return _rows(rows)


async def get_tenant_default_rubric(pool: asyncpg.Pool, tenant_id: str) -> dict[str, Any] | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM evaluation.rubric_templates WHERE tenant_id=$1 AND scope='tenant'",
            tenant_id,
        )
    return _row(row)


async def get_campaign_rubric(
    pool: asyncpg.Pool, tenant_id: str, campaign_id: str,
) -> dict[str, Any] | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM evaluation.rubric_templates "
            "WHERE tenant_id=$1 AND scope='campaign' AND campaign_id=$2",
            tenant_id, campaign_id,
        )
    return _row(row)


async def create_rubric_template(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    scope: str = "tenant",
    campaign_id: str | None = None,
    name: str = "Rubric template",
    body: str = "",
    created_by: str = "operator",
) -> dict[str, Any]:
    rubric_id = _new_id("evrubric_")
    if scope == "campaign" and not campaign_id:
        raise ValueError("campaign scope requires campaign_id")
    if scope == "tenant":
        campaign_id = None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO evaluation.rubric_templates
                (id, tenant_id, scope, campaign_id, name, body, created_by)
            VALUES ($1,$2,$3,$4,$5,$6,$7)
            RETURNING *
            """,
            rubric_id, tenant_id, scope, campaign_id, name, body, created_by,
        )
    return _row(row)  # type: ignore[return-value]


async def update_rubric_template(
    pool: asyncpg.Pool, rubric_id: str, tenant_id: str, updates: dict[str, Any],
) -> dict[str, Any] | None:
    """Atualiza name/body. Editar uma rubrica PUBLICADA bifurca para draft E **incrementa
    a versão** (a versão publicada fica congelada no snapshot; a próxima publicação cria um
    snapshot novo). Espelha o intent do update_form (T6b)."""
    allowed = {"name", "body", "deploy_status"}
    upd = {k: v for k, v in updates.items() if k in allowed and v is not None}
    if not upd:
        return await get_rubric_template(pool, rubric_id, tenant_id)
    extra_sets: list[str] = []
    if "deploy_status" not in upd:
        cur = await get_rubric_template(pool, rubric_id, tenant_id)
        if cur and cur.get("deploy_status") == "published":
            upd["deploy_status"] = "draft"
            extra_sets.append("version=version+1")   # bifurca p/ nova versão
    sets, args = [], []
    for k, v in upd.items():
        args.append(v)
        sets.append(f"{k}=${len(args)}")
    sets.extend(extra_sets)
    args.extend([rubric_id, tenant_id])
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"UPDATE evaluation.rubric_templates SET {', '.join(sets)}, updated_at=now() "
            f"WHERE id=${len(args)-1} AND tenant_id=${len(args)} RETURNING *",
            *args,
        )
    return _row(row)


async def publish_rubric_template(
    pool: asyncpg.Pool, rubric_id: str, tenant_id: str, *, published_by: str = "operator",
) -> dict[str, Any] | None:
    """Snapshot imutável da rubrica corrente em rubric_template_versions e marca
    published. Idempotente (ON CONFLICT DO NOTHING; a versão publicada nunca muda)."""
    async with pool.acquire() as conn:
        raw = await conn.fetchrow(
            "SELECT * FROM evaluation.rubric_templates WHERE id=$1 AND tenant_id=$2",
            rubric_id, tenant_id,
        )
        if raw is None:
            return None
        r = dict(raw)
        version = int(r.get("version") or 1)
        await conn.execute(
            """
            INSERT INTO evaluation.rubric_template_versions
                (rubric_id, tenant_id, version, name, body, published_by)
            VALUES ($1,$2,$3,$4,$5,$6)
            ON CONFLICT (rubric_id, version) DO NOTHING
            """,
            rubric_id, tenant_id, version, r.get("name") or "", r.get("body") or "", published_by,
        )
        row = await conn.fetchrow(
            "UPDATE evaluation.rubric_templates SET deploy_status='published', updated_at=now() "
            "WHERE id=$1 AND tenant_id=$2 RETURNING *",
            rubric_id, tenant_id,
        )
    return _row(row)


async def list_rubric_template_versions(
    pool: asyncpg.Pool, rubric_id: str, tenant_id: str,
) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM evaluation.rubric_template_versions "
            "WHERE rubric_id=$1 AND tenant_id=$2 ORDER BY version DESC",
            rubric_id, tenant_id,
        )
    return _rows(rows)


async def get_rubric_template_version(
    pool: asyncpg.Pool, rubric_id: str, tenant_id: str, version: int,
) -> dict[str, Any] | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM evaluation.rubric_template_versions "
            "WHERE rubric_id=$1 AND tenant_id=$2 AND version=$3",
            rubric_id, tenant_id, version,
        )
    return _row(row)


async def latest_published_rubric_version(
    pool: asyncpg.Pool, rubric_id: str, tenant_id: str,
) -> int | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT max(version) AS v FROM evaluation.rubric_template_versions "
            "WHERE rubric_id=$1 AND tenant_id=$2",
            rubric_id, tenant_id,
        )
    return int(row["v"]) if row and row["v"] is not None else None


async def _resolve_published_snapshot(
    pool: asyncpg.Pool, rubric: dict[str, Any] | None, tenant_id: str, scope: str, source: str,
) -> dict[str, Any] | None:
    """Devolve o último SNAPSHOT publicado de uma rubrica (independe do estado vivo: a
    rubrica pode estar em draft por edição, mas a versão publicada continua válida)."""
    if not rubric:
        return None
    v = await latest_published_rubric_version(pool, rubric["id"], tenant_id)
    if not v:
        return None
    snap = await get_rubric_template_version(pool, rubric["id"], tenant_id, v)
    if not snap:
        return None
    return {"rubric_id": rubric["id"], "scope": scope, "version": v,
            "name": snap.get("name", ""), "body": snap.get("body", ""), "source": source}


async def resolve_rubric(
    pool: asyncpg.Pool, tenant_id: str, *, campaign_id: str | None = None,
) -> dict[str, Any] | None:
    """Rubrica EFETIVA (spec §5.1/§16.3): override PUBLICADO da campanha vence; senão a
    default PUBLICADA do tenant; senão None (o compositor — chunk B — cai num built-in).
    Sempre lê o SNAPSHOT publicado (não o draft vivo), p/ avaliações usarem a versão
    estável mesmo enquanto a rubrica é re-editada. Retorna {rubric_id, scope, version,
    name, body, source}."""
    if campaign_id:
        ov = await get_campaign_rubric(pool, tenant_id, campaign_id)
        res = await _resolve_published_snapshot(pool, ov, tenant_id, "campaign", "campaign_override")
        if res:
            return res
    df = await get_tenant_default_rubric(pool, tenant_id)
    return await _resolve_published_snapshot(pool, df, tenant_id, "tenant", "tenant_default")


# ─── Campaigns CRUD ───────────────────────────────────────────────────────────

async def create_campaign(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    name: str,
    description: str = "",
    form_id: str,
    pool_id: str,
    sampling_rules: dict | None = None,
    reviewer_rules: dict | None = None,
    schedule: dict | None = None,
    created_by: str = "operator",
    evaluation_pool_id: str | None = None,
    evaluation_calendar_id: str | None = None,
    gateway_config_ids: list[str] | None = None,
    evaluator_pool: str | None = None,
    period_start: str | None = None,      # T17 — janela de dados (ISO; NULL=aberto)
    period_end: str | None = None,
) -> dict[str, Any]:
    campaign_id = _new_id("evcampaign_")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO evaluation.campaigns
                (id, tenant_id, name, description, form_id, pool_id,
                 sampling_rules, reviewer_rules, schedule, created_by,
                 evaluation_pool_id, evaluation_calendar_id, gateway_config_ids,
                 evaluator_pool, period_start, period_end)
            VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8::jsonb,$9::jsonb,$10,
                    $11,$12,$13,$14,$15,$16)
            RETURNING *
            """,
            campaign_id, tenant_id, name, description, form_id, pool_id,
            json.dumps(sampling_rules or {}),
            json.dumps(reviewer_rules or {}),
            json.dumps(schedule or {}),
            created_by,
            evaluation_pool_id,
            evaluation_calendar_id,
            gateway_config_ids or [],
            evaluator_pool,
            _parse_ts(period_start),
            _parse_ts(period_end),
        )
    return _row(row)  # type: ignore[return-value]


async def get_campaign(pool: asyncpg.Pool, campaign_id: str, tenant_id: str) -> dict[str, Any] | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM evaluation.campaigns WHERE id=$1 AND tenant_id=$2",
            campaign_id, tenant_id,
        )
    return _row(row)


async def list_campaigns(
    pool: asyncpg.Pool,
    tenant_id: str,
    pool_id: str | None = None,
    evaluation_pool_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    cond = "WHERE tenant_id=$1"
    args: list[Any] = [tenant_id]
    if pool_id:
        args.append(pool_id)
        cond += f" AND pool_id=${len(args)}"
    if evaluation_pool_id:
        args.append(evaluation_pool_id)
        cond += f" AND evaluation_pool_id=${len(args)}"
    if status:
        args.append(status)
        cond += f" AND status=${len(args)}"
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT * FROM evaluation.campaigns {cond} ORDER BY created_at DESC LIMIT ${len(args)+1} OFFSET ${len(args)+2}",
            *args, limit, offset,
        )
    return _rows(rows)


async def update_campaign(
    pool: asyncpg.Pool,
    campaign_id: str,
    tenant_id: str,
    **fields: Any,
) -> dict[str, Any] | None:
    allowed = {"name", "description", "status", "sampling_rules", "reviewer_rules", "schedule",
               "total_instances", "completed_instances", "avg_score",
               "review_workflow_skill_id", "contestation_policy",
               "evaluation_pool_id", "evaluation_calendar_id", "gateway_config_ids",
               "evaluator_pool", "period_start", "period_end"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return await get_campaign(pool, campaign_id, tenant_id)

    set_parts = []
    args: list[Any] = []
    idx = 1
    jsonb_fields = {"sampling_rules", "reviewer_rules", "schedule", "contestation_policy"}
    ts_fields    = {"period_start", "period_end"}   # T17 — cast ISO→timestamptz
    for k, v in updates.items():
        if k in jsonb_fields:
            set_parts.append(f"{k}=${idx}::jsonb")
            args.append(json.dumps(v))
        elif k in ts_fields:
            set_parts.append(f"{k}=${idx}")
            args.append(_parse_ts(v))
        else:
            set_parts.append(f"{k}=${idx}")
            args.append(v)
        idx += 1
    set_parts.append(f"updated_at=${idx}")
    args.append(datetime.utcnow())
    idx += 1
    args.extend([campaign_id, tenant_id])

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"UPDATE evaluation.campaigns SET {', '.join(set_parts)} "
            f"WHERE id=${idx} AND tenant_id=${idx+1} RETURNING *",
            *args,
        )
    return _row(row)


async def delete_campaign(pool: asyncpg.Pool, campaign_id: str, tenant_id: str) -> bool:
    """Remove a campanha e seus dependentes (instances/results/critérios/threads/
    curation) numa transação — hard delete. Filhos antes dos pais (best-effort)."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                "SELECT id FROM evaluation.instances WHERE campaign_id=$1 AND tenant_id=$2",
                campaign_id, tenant_id,
            )
            inst_ids = [r["id"] for r in rows]
            if inst_ids:
                for tbl, col in (
                    ("criterion_responses", "instance_id"),
                    ("contestation_threads", "evaluation_instance_id"),
                    ("curation_reviews",     "instance_id"),
                    ("results",              "instance_id"),
                    ("instances",            "id"),
                ):
                    try:
                        await conn.execute(
                            f"DELETE FROM evaluation.{tbl} WHERE {col} = ANY($1::text[])",
                            inst_ids,
                        )
                    except Exception:
                        pass
            try:
                await conn.execute(
                    "DELETE FROM evaluation.results WHERE campaign_id=$1 AND tenant_id=$2",
                    campaign_id, tenant_id,
                )
            except Exception:
                pass
            res = await conn.execute(
                "DELETE FROM evaluation.campaigns WHERE id=$1 AND tenant_id=$2",
                campaign_id, tenant_id,
            )
    return res.split()[-1] != "0"


# ─── Instances CRUD ───────────────────────────────────────────────────────────

async def create_instance(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    campaign_id: str,
    form_id: str,
    session_id: str,
    segment_id: str | None = None,
    evaluated_user_id: str | None = None,   # T2 — identidade do humano avaliado (posse, 5a)
    form_version: int = 1,                  # T2 — versão fixada do formulário
    priority: int = 5,
    expires_at: datetime | None = None,
    deploy_version: str | None = None,      # R9d — versão do skill (AI) do segmento avaliado
) -> dict[str, Any]:
    instance_id = _new_id("evinstance_")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO evaluation.instances
                (id, tenant_id, campaign_id, form_id, session_id, segment_id,
                 evaluated_user_id, form_version, priority, expires_at, deploy_version)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
            RETURNING *
            """,
            instance_id, tenant_id, campaign_id, form_id, session_id, segment_id,
            evaluated_user_id, form_version, priority, expires_at, deploy_version,
        )
        # increment campaign counter
        await conn.execute(
            "UPDATE evaluation.campaigns SET total_instances=total_instances+1, updated_at=now() WHERE id=$1",
            campaign_id,
        )
    return _row(row)  # type: ignore[return-value]


async def instance_exists_for_segment(
    pool: asyncpg.Pool, campaign_id: str, segment_id: str, tenant_id: str
) -> bool:
    """T2 — idempotência por segmento: unicidade (campaign_id, segment_id)."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM evaluation.instances "
            "WHERE campaign_id=$1 AND segment_id=$2 AND tenant_id=$3 LIMIT 1",
            campaign_id, segment_id, tenant_id,
        )
    return row is not None


async def instance_exists_for_session(
    pool: asyncpg.Pool, campaign_id: str, session_id: str, tenant_id: str
) -> bool:
    """S2.1 (sampling no close): idempotência — evita instância duplicada quando o
    evento conversations.session_closed é re-consumido (restart / at-least-once)."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM evaluation.instances "
            "WHERE campaign_id=$1 AND session_id=$2 AND tenant_id=$3 LIMIT 1",
            campaign_id, session_id, tenant_id,
        )
    return row is not None


async def flush_synthetic(pool: asyncpg.Pool, tenant_id: str) -> dict[str, int]:
    """Apaga a massa SINTÉTICA do tenant (instâncias com session_id LIKE 'synthetic_%')
    e tudo que a referencia — para o ciclo gerar/limpar do avaliador fake. Best-effort
    por tabela (filhos antes dos pais)."""
    counts: dict[str, int] = {}
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id FROM evaluation.instances "
            "WHERE tenant_id=$1 AND session_id LIKE 'synthetic_%'",
            tenant_id,
        )
        inst_ids = [r["id"] for r in rows]
        if not inst_ids:
            return {"instances": 0}
        for tbl, col in (
            ("criterion_responses", "instance_id"),
            ("contestation_threads", "evaluation_instance_id"),
            ("curation_reviews",     "instance_id"),
            ("results",              "instance_id"),
            ("instances",            "id"),
        ):
            try:
                res = await conn.execute(
                    f"DELETE FROM evaluation.{tbl} WHERE {col} = ANY($1::text[])",
                    inst_ids,
                )
                counts[tbl] = int(res.split()[-1])
            except Exception:
                counts[tbl] = -1
    return counts


async def get_instance(pool: asyncpg.Pool, instance_id: str, tenant_id: str) -> dict[str, Any] | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM evaluation.instances WHERE id=$1 AND tenant_id=$2",
            instance_id, tenant_id,
        )
    return _row(row)


async def list_instances(
    pool: asyncpg.Pool,
    tenant_id: str,
    campaign_id: str | None = None,
    status: str | None = None,
    session_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    cond = "WHERE tenant_id=$1"
    args: list[Any] = [tenant_id]
    for col, val in [("campaign_id", campaign_id), ("status", status), ("session_id", session_id)]:
        if val is not None:
            args.append(val)
            cond += f" AND {col}=${len(args)}"
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT * FROM evaluation.instances {cond} ORDER BY scheduled_at DESC LIMIT ${len(args)+1} OFFSET ${len(args)+2}",
            *args, limit, offset,
        )
    return _rows(rows)


async def update_instance_status(
    pool: asyncpg.Pool,
    instance_id: str,
    tenant_id: str,
    status: str,
    **extra_fields: Any,
) -> dict[str, Any] | None:
    allowed_extra = {"evaluator_agent_id", "reviewer_agent_id", "assigned_at",
                     "completed_at", "error_message", "expires_at"}
    set_parts = ["status=$1", "updated_at=$2"]
    args: list[Any] = [status, datetime.utcnow()]
    for k, v in extra_fields.items():
        if k in allowed_extra:
            args.append(v)
            set_parts.append(f"{k}=${len(args)}")
    args.extend([instance_id, tenant_id])
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"UPDATE evaluation.instances SET {', '.join(set_parts)} "
            f"WHERE id=${len(args)-1} AND tenant_id=${len(args)} RETURNING *",
            *args,
        )
    return _row(row)


async def set_instance_session_metrics(
    pool: asyncpg.Pool,
    instance_id: str,
    tenant_id: str,
    session_metrics: dict[str, Any],
) -> dict[str, Any] | None:
    """Persist computed session_metric.* values onto the EvaluationInstance."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE evaluation.instances
               SET session_metrics=$1::jsonb, updated_at=now()
             WHERE id=$2 AND tenant_id=$3
            RETURNING *
            """,
            json.dumps(session_metrics), instance_id, tenant_id,
        )
    return _row(row)


async def claim_next_instance(
    pool: asyncpg.Pool,
    tenant_id: str,
    campaign_id: str | None = None,
    evaluator_agent_id: str | None = None,
) -> dict[str, Any] | None:
    """Atomically claim the next scheduled instance (highest priority first)."""
    cond = "WHERE tenant_id=$1 AND status='scheduled'"
    args: list[Any] = [tenant_id]
    if campaign_id:
        args.append(campaign_id)
        cond += f" AND campaign_id=${len(args)}"
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            UPDATE evaluation.instances
               SET status='assigned',
                   evaluator_agent_id=$2,
                   assigned_at=now(),
                   updated_at=now()
             WHERE id = (
               SELECT id FROM evaluation.instances {cond}
                 AND (expires_at IS NULL OR expires_at > now())
               ORDER BY priority ASC, scheduled_at ASC
               LIMIT 1
               FOR UPDATE SKIP LOCKED
             )
            RETURNING *
            """,
            tenant_id, evaluator_agent_id, *args[1:],
        )
    return _row(row)


# ─── Results CRUD ─────────────────────────────────────────────────────────────

async def create_result(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    instance_id: str,
    session_id: str,
    campaign_id: str,
    form_id: str,
    evaluator_agent_id: str,
    overall_score: float | None = None,
    max_score: float | None = None,
    normalized_score: float | None = None,
    passed: bool | None = None,
    eval_status: str = "submitted",
    evaluator_notes: str = "",
    comparison_mode: bool = False,
    comparison_report: dict | None = None,
    knowledge_snippets: list[dict] | None = None,
) -> dict[str, Any]:
    result_id = _new_id("evresult_")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO evaluation.results
                (id, tenant_id, instance_id, session_id, campaign_id, form_id,
                 evaluator_agent_id, overall_score, max_score, normalized_score, passed,
                 eval_status, evaluator_notes, comparison_mode, comparison_report, knowledge_snippets)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15::jsonb,$16::jsonb)
            RETURNING *
            """,
            result_id, tenant_id, instance_id, session_id, campaign_id, form_id,
            evaluator_agent_id, overall_score, max_score, normalized_score, passed,
            eval_status, evaluator_notes, comparison_mode,
            json.dumps(comparison_report) if comparison_report else None,
            json.dumps(knowledge_snippets or []),
        )
        # update campaign stats
        await conn.execute(
            """
            UPDATE evaluation.campaigns
               SET completed_instances = completed_instances + 1,
                   avg_score = (
                     SELECT AVG(overall_score) FROM evaluation.results
                      WHERE campaign_id = $1 AND overall_score IS NOT NULL
                   ),
                   updated_at = now()
             WHERE id = $1
            """,
            campaign_id,
        )
        # advance instance to completed
        await conn.execute(
            "UPDATE evaluation.instances SET status='completed', completed_at=now(), updated_at=now() WHERE id=$1",
            instance_id,
        )
    return _row(row)  # type: ignore[return-value]


async def get_result(pool: asyncpg.Pool, result_id: str, tenant_id: str) -> dict[str, Any] | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM evaluation.results WHERE id=$1 AND tenant_id=$2",
            result_id, tenant_id,
        )
    return _row(row)


async def get_result_by_id(pool: asyncpg.Pool, result_id: str) -> dict[str, Any] | None:
    """Look up a result by ID only — used when tenant_id is not available (e.g. lock endpoint)."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM evaluation.results WHERE id=$1", result_id)
    return _row(row)


async def get_result_by_instance(pool: asyncpg.Pool, instance_id: str, tenant_id: str) -> dict[str, Any] | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM evaluation.results WHERE instance_id=$1 AND tenant_id=$2",
            instance_id, tenant_id,
        )
    return _row(row)


async def list_results(
    pool: asyncpg.Pool,
    tenant_id: str,
    campaign_id: str | None = None,
    session_id: str | None = None,
    eval_status: str | None = None,
    action_required: str | None = None,   # "review" | "contestation" | "any" (non-null)
    pool_id: str | None = None,            # filter via campaign → pool
    evaluator_id: str | None = None,
    locked: bool | None = None,
    # T10-C — visibilidade (escopo de linha): None = sem filtro; lista = restringe.
    evaluated_user_ids: list[str] | None = None,   # posse: result.evaluated_user_id ∈ lista
    accessible_pools:   list[str] | None = None,    # Arc 7: campaign.pool_id ∈ lista
    # Bug fix 2026-07-02: quando setado, a posse (evaluated_user_id == self_user_id) é
    # SEMPRE visível, independente de accessible_pools — ver `_compute_result_scope`.
    self_user_id:       str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    # Join na campanha quando filtramos por pool (explícito ou accessible_pools).
    use_join = bool(pool_id or accessible_pools)
    if use_join:
        base = """
            SELECT r.*
            FROM evaluation.results r
            LEFT JOIN evaluation.campaigns c ON c.id = r.campaign_id
            WHERE r.tenant_id=$1
        """
    else:
        base = "SELECT * FROM evaluation.results WHERE tenant_id=$1"
    cond_prefix = "AND"
    rp = "r." if use_join else ""   # prefixo de coluna da tabela results

    cond = ""
    args: list[Any] = [tenant_id]

    for col, val in [
        (f"{rp}campaign_id",  campaign_id),
        (f"{rp}session_id",   session_id),
        (f"{rp}eval_status",  eval_status),
        (f"{rp}evaluator_id", evaluator_id),
    ]:
        if val is not None:
            args.append(val)
            cond += f" {cond_prefix} {col}=${len(args)}"

    if pool_id:
        args.append(pool_id)
        cond += f" {cond_prefix} c.pool_id=${len(args)}"

    # T10-C — escopo de visibilidade (filtro de linha; nunca amplia).
    # Bug fix 2026-07-02: posse (evaluated_user_id == self_user_id) é SEMPRE visível para o
    # dono, independente de accessible_pools — o pool ADMINISTRATIVO da campanha (c.pool_id)
    # pode divergir do pool OPERACIONAL real onde o agente trabalhou; a interseção AND cega
    # não deveria bloquear alguém de ver a própria avaliação por causa dessa divergência.
    # accessible_pools continua restringindo a visibilidade de OUTRAS pessoas supervisionadas.
    # `accessible_pools == []` = chamador sem NENHUM pool. Hoje essa entrada não chega aqui
    # (o resolvedor devolve `None` para lista vazia); depois do passo 3 ela chega, e TODOS os
    # ramos abaixo leem lista vazia como "sem filtro" — convertendo nenhum-acesso em acesso a
    # tudo, sem erro nem log. A recusa não pode ser cega: a regra de posse logo acima diz que a
    # própria avaliação é SEMPRE visível. Logo, sem pool algum, sobra exatamente a posse.
    # Ver `pending.md` AUT-05.
    if accessible_pools is not None and not accessible_pools:
        if not self_user_id:
            return []
        args.append(self_user_id)
        cond += f" {cond_prefix} {rp}evaluated_user_id = ${len(args)}"
    elif evaluated_user_ids is not None:
        if self_user_id and accessible_pools:
            args.append(self_user_id)
            self_idx = len(args)
            args.append(evaluated_user_ids)
            eu_idx = len(args)
            args.append(accessible_pools)
            ap_idx = len(args)
            cond += (
                f" {cond_prefix} ({rp}evaluated_user_id = ${self_idx}"
                f" OR ({rp}evaluated_user_id = ANY(${eu_idx}) AND c.pool_id = ANY(${ap_idx})))"
            )
        else:
            args.append(evaluated_user_ids)
            cond += f" {cond_prefix} {rp}evaluated_user_id = ANY(${len(args)})"
            if accessible_pools:
                args.append(accessible_pools)
                cond += f" {cond_prefix} c.pool_id = ANY(${len(args)})"
    elif accessible_pools:
        args.append(accessible_pools)
        cond += f" {cond_prefix} c.pool_id = ANY(${len(args)})"

    if action_required == "any":
        cond += f" {cond_prefix} {rp}action_required IS NOT NULL"
    elif action_required is not None:
        args.append(action_required)
        cond += f" {cond_prefix} {rp}action_required=${len(args)}"

    if locked is not None:
        args.append(locked)
        cond += f" {cond_prefix} {rp}locked=${len(args)}"

    order_col = f"{rp}submitted_at"
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"{base}{cond} ORDER BY {order_col} DESC NULLS LAST"
            f" LIMIT ${len(args)+1} OFFSET ${len(args)+2}",
            *args, limit, offset,
        )
    return _rows(rows)


async def update_result(
    pool: asyncpg.Pool,
    result_id: str,
    tenant_id: str,
    **fields: Any,
) -> dict[str, Any] | None:
    allowed = {"eval_status", "reviewer_agent_id", "reviewer_outcome", "reviewer_notes",
               "reviewer_score", "reviewed_at", "contested_by", "contested_at",
               "contestation_reason", "locked_at", "locked_by"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return await get_result(pool, result_id, tenant_id)
    set_parts = []
    args: list[Any] = []
    idx = 1
    for k, v in updates.items():
        set_parts.append(f"{k}=${idx}")
        args.append(v)
        idx += 1
    set_parts.append(f"updated_at=${idx}")
    args.append(datetime.utcnow())
    idx += 1
    args.extend([result_id, tenant_id])
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"UPDATE evaluation.results SET {', '.join(set_parts)} "
            f"WHERE id=${idx} AND tenant_id=${idx+1} RETURNING *",
            *args,
        )
    return _row(row)


# ─── CriterionResponses ───────────────────────────────────────────────────────

async def create_criterion_responses(
    pool: asyncpg.Pool,
    result_id: str,
    instance_id: str,
    campaign_id: str,
    tenant_id: str,
    responses: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not responses:
        return []
    rows = []
    async with pool.acquire() as conn:
        for r in responses:
            row = await conn.fetchrow(
                """
                INSERT INTO evaluation.criterion_responses
                    (id, result_id, instance_id, campaign_id, tenant_id,
                     criterion_id, criterion_name, dimension_id,
                     na, score, max_score, boolean_value, choice_value,
                     text_value, notes, evidence, weight)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16::jsonb,$17)
                RETURNING *
                """,
                _new_id("evcrr_"), result_id, instance_id, campaign_id, tenant_id,
                r.get("criterion_id", ""), r.get("criterion_name", ""), r.get("dimension_id", ""),
                r.get("na", False), r.get("score"), r.get("max_score"),
                r.get("boolean_value"), r.get("choice_value"), r.get("text_value"),
                # T9-C.fix — o avaliador IA emite a fundamentação como `justification` e a
                # evidência como `evidence`/`evidence_entries` (saída form-driven, evaluation.ts
                # buildEvaluationOutputSchema). Sem este fallback, a justificativa por critério e
                # os chips de evidência (clicáveis → transcript, C.3) ficam vazios na UI do nível 3.
                r.get("notes") or r.get("justification"),
                json.dumps(r.get("evidence") or r.get("evidence_entries") or []),
                r.get("weight", 1.0),
            )
            rows.append(_row(row))
    return rows  # type: ignore[return-value]


async def list_criterion_responses(
    pool: asyncpg.Pool,
    result_id: str,
    tenant_id: str,
) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM evaluation.criterion_responses WHERE result_id=$1 AND tenant_id=$2 ORDER BY created_at ASC",
            result_id, tenant_id,
        )
    return _rows(rows)


# ─── Contestations ────────────────────────────────────────────────────────────

async def create_contestation(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    result_id: str,
    instance_id: str,
    session_id: str,
    contested_by: str,
    contestation_reason: str = "",
) -> dict[str, Any]:
    contest_id = _new_id("evcontest_")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO evaluation.contestations
                (id, tenant_id, result_id, instance_id, session_id, contested_by, contestation_reason)
            VALUES ($1,$2,$3,$4,$5,$6,$7)
            RETURNING *
            """,
            contest_id, tenant_id, result_id, instance_id, session_id, contested_by, contestation_reason,
        )
        # update result and instance status
        await conn.execute(
            "UPDATE evaluation.results SET eval_status='contested', contested_by=$1, contested_at=now(), contestation_reason=$2, updated_at=now() WHERE id=$3",
            contested_by, contestation_reason, result_id,
        )
        await conn.execute(
            "UPDATE evaluation.instances SET status='contested', updated_at=now() WHERE id=$1",
            instance_id,
        )
    return _row(row)  # type: ignore[return-value]


async def adjudicate_contestation(
    pool: asyncpg.Pool,
    contest_id: str,
    tenant_id: str,
    *,
    status: str,
    adjudicated_by: str,
    adjudication_notes: str = "",
    adjusted_score: float | None = None,
) -> dict[str, Any] | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE evaluation.contestations
               SET status=$1, adjudicated_by=$2, adjudicated_at=now(),
                   adjudication_notes=$3, adjusted_score=$4, updated_at=now()
             WHERE id=$5 AND tenant_id=$6
            RETURNING *
            """,
            status, adjudicated_by, adjudication_notes, adjusted_score, contest_id, tenant_id,
        )
    return _row(row)


async def list_contestations(
    pool: asyncpg.Pool,
    tenant_id: str,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    cond = "WHERE tenant_id=$1"
    args: list[Any] = [tenant_id]
    if status:
        args.append(status)
        cond += f" AND status=${len(args)}"
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT * FROM evaluation.contestations {cond} ORDER BY contested_at DESC LIMIT ${len(args)+1} OFFSET ${len(args)+2}",
            *args, limit, offset,
        )
    return _rows(rows)


# ─── Workflow state helpers ────────────────────────────────────────────────────

async def update_result_workflow_state(
    pool: asyncpg.Pool,
    result_id: str,
    *,
    action_required: str | None,
    current_round: int | None = None,
    deadline_at: datetime | None = None,
    resume_token: str | None = None,
    workflow_instance_id: str | None = None,
    locked: bool = False,
    lock_reason: str | None = None,
) -> dict[str, Any] | None:
    """
    Called by the workflow.events Kafka consumer to sync result workflow state.
    - workflow.suspended → action_required set, current_round/deadline_at/resume_token updated
    - workflow.completed → locked=True, action_required=None, resume_token=None
    """
    set_parts = ["action_required=$1", "updated_at=now()"]
    args: list[Any] = [action_required]
    idx = 2

    if current_round is not None:
        set_parts.append(f"current_round=${idx}")
        args.append(current_round)
        idx += 1
    if deadline_at is not None:
        set_parts.append(f"deadline_at=${idx}")
        args.append(deadline_at)
        idx += 1
    if resume_token is not None:
        set_parts.append(f"resume_token=${idx}")
        args.append(resume_token)
        idx += 1
    if workflow_instance_id is not None:
        set_parts.append(f"workflow_instance_id=${idx}")
        args.append(workflow_instance_id)
        idx += 1
    if locked:
        set_parts.append("eval_status='locked'")
        set_parts.append("locked_at=now()")
        set_parts.append("resume_token=NULL")
        if lock_reason:
            set_parts.append(f"lock_reason=${idx}")
            args.append(lock_reason)
            idx += 1

    args.append(result_id)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"UPDATE evaluation.results SET {', '.join(set_parts)} "
            f"WHERE id=${idx} RETURNING *",
            *args,
        )
    return _row(row)


async def lock_result(
    pool: asyncpg.Pool,
    result_id: str,
    *,
    lock_reason: str = "manual",
    locked_by: str = "system",
) -> dict[str, Any] | None:
    """
    Permanently lock a result. Called by:
    - evaluation_lock MCP tool (from congelar_resultado workflow step)
    - workflow.events consumer on workflow.completed with lock_reason
    Once locked, eval_status='locked' is irreversible — any further write returns None.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE evaluation.results
               SET eval_status    = 'locked',
                   locked_at      = now(),
                   locked_by      = $1,
                   lock_reason    = $2,
                   action_required = NULL,
                   resume_token   = NULL,
                   updated_at     = now()
             WHERE id = $3
               AND eval_status != 'locked'
            RETURNING *
            """,
            locked_by, lock_reason, result_id,
        )
    return _row(row)


# ─── Arc 13 — ContestationThread ─────────────────────────────────────────────

async def create_contestation_thread(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    evaluation_instance_id: str,
    dimension_id: str,
    round: int,
    author_type: str,
    author_id: str,
    text: str,
    decision: str | None = None,
    score_override: float | None = None,
    evidence_entries: list[dict] | None = None,
    calibration_signal: dict | None = None,
) -> dict[str, Any]:
    thread_id = _new_id("evthread_")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO evaluation.contestation_threads
                (id, tenant_id, evaluation_instance_id, dimension_id, round,
                 author_type, author_id, text, decision, score_override,
                 evidence_entries, calibration_signal)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb,$12::jsonb)
            RETURNING *
            """,
            thread_id, tenant_id, evaluation_instance_id, dimension_id, round,
            author_type, author_id, text, decision, score_override,
            json.dumps(evidence_entries or []),
            json.dumps(calibration_signal) if calibration_signal else None,
        )
    return _row(row)  # type: ignore[return-value]


async def list_contestation_threads(
    pool: asyncpg.Pool,
    evaluation_instance_id: str,
    tenant_id: str,
    dimension_id: str | None = None,
) -> list[dict[str, Any]]:
    cond = "WHERE evaluation_instance_id=$1 AND tenant_id=$2"
    args: list[Any] = [evaluation_instance_id, tenant_id]
    if dimension_id:
        args.append(dimension_id)
        cond += f" AND dimension_id=${len(args)}"
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT * FROM evaluation.contestation_threads {cond} ORDER BY dimension_id, round ASC",
            *args,
        )
    return _rows(rows)


def _iso_ts(ts: Any) -> str:
    """datetime/qualquer → ISO8601 string (vazio se None)."""
    if ts is None:
        return ""
    if isinstance(ts, datetime):
        return ts.isoformat()
    return str(ts)


async def get_instance_threads_grouped(
    pool: asyncpg.Pool,
    evaluation_instance_id: str,
    tenant_id: str,
) -> dict[str, Any]:
    """T10-D2 — leitura AGRUPADA das contestation_threads para a UI (Arc 13).

    O storage é plano (uma linha por entry: dimension_id+round+author). A UI espera UMA thread
    por dimensão com `entries[]` + `current_state` + `original_score`/`current_score` (0–1).
    Aqui agrupamos por `dimension_id`, reconstruímos a timeline (entries por round), derivamos o
    estado pela máquina (última ação significativa) e os scores: `original` vem do
    `criterion_responses` (normalizado 0–1); `current` = último override revisado, senão original.
    """
    flat   = await list_contestation_threads(pool, evaluation_instance_id, tenant_id)
    result = await get_result_by_instance(pool, evaluation_instance_id, tenant_id)
    result_id = result.get("id") if result else None

    # score (normalizado 0–1) + label por critério/dimensão, do criterion_responses
    crit_meta: dict[str, dict[str, Any]] = {}
    if result_id:
        for cr in await list_criterion_responses(pool, result_id, tenant_id):
            cid = cr.get("criterion_id")
            if not cid:
                continue
            norm: float | None = None
            score = cr.get("score")
            if score is not None:
                try:
                    s = float(score)
                    m = float(cr.get("max_score") or 10) or 10.0
                    norm = round(s / m, 4)
                except Exception:
                    norm = None
            crit_meta[cid] = {"score": norm, "label": cr.get("criterion_name") or cid}

    # Label oficial vem do FORM (versão fixada), não do criterion_responses (criterion_name
    # costuma vir vazio). Precedência: form.label → criterion_name → criterion_id.
    form_labels: dict[str, str] = {}
    if result and result.get("form_id"):
        form = await get_form_version(
            pool, result["form_id"], tenant_id, int(result.get("form_version") or 1),
        )
        for d in ((form or {}).get("dimensions") or []):
            for c in (d.get("criteria") or []):
                cid = c.get("criterion_id") or c.get("id")
                if cid:
                    form_labels[cid] = c.get("label") or c.get("name") or cid

    by_dim: dict[str, list[dict[str, Any]]] = {}
    for r in flat:
        by_dim.setdefault(r["dimension_id"], []).append(r)

    threads: list[dict[str, Any]] = []
    max_round = 0
    for dim_id, rows in by_dim.items():
        rows.sort(key=lambda x: (x.get("round", 0) or 0, _iso_ts(x.get("created_at"))))
        meta     = crit_meta.get(dim_id, {})
        original = meta.get("score") or 0
        current  = original
        state    = "neutral"
        entries: list[dict[str, Any]] = []
        for r in rows:
            at       = r.get("author_type")
            decision = r.get("decision")
            override = r.get("score_override")
            if at == "evaluator_ai":
                e_score: float | None = original
            elif override is not None:
                e_score = round(float(override), 4)
            else:
                e_score = None
            entries.append({
                "round":            r.get("round", 1),
                "author_role":      at,
                "action":           decision,
                "score":            e_score,
                "justification":    r.get("text", "") or "",
                "evidence_entries": r.get("evidence_entries") or [],
                "submitted_at":     _iso_ts(r.get("created_at")),
            })
            max_round = max(max_round, int(r.get("round", 1) or 1))
            # estado pela última ação (rounds crescem com o avanço do fluxo)
            if at == "pre_reviewer_ai":
                state = "pre_reviewed"
            elif at == "human_agent":
                state = "contested"
            elif at in ("reviewer_ai", "human_reviewer"):
                if decision == "revised":
                    state = "revised"
                    if override is not None:
                        current = round(float(override), 4)
                elif decision == "upheld":
                    state = "upheld"
        threads.append({
            "dimension_id":    dim_id,
            "dimension_label": form_labels.get(dim_id) or meta.get("label") or dim_id,
            "current_state":   state,
            "original_score":  original,
            "current_score":   current,
            "entries":         entries,
        })
    threads.sort(key=lambda t: t["dimension_id"])
    return {
        "instance_id":   evaluation_instance_id,
        "result_id":     result_id,
        "current_round": max_round or 1,
        "threads":       threads,
    }


async def list_contested_criteria_for_round(
    pool: asyncpg.Pool,
    evaluation_instance_id: str,
    tenant_id: str,
    round: int,
) -> list[str]:
    """T5/5c — conjunto de critérios contestados pelo avaliado (author_type=human_agent)
    no round corrente. Base do gate 'tratar todas' do submit_review (§15.3)."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT dimension_id
              FROM evaluation.contestation_threads
             WHERE evaluation_instance_id=$1 AND tenant_id=$2
               AND round=$3 AND author_type='human_agent'
            """,
            evaluation_instance_id, tenant_id, round,
        )
    return [r["dimension_id"] for r in rows]


# ─── Arc 13 — CurationReview ──────────────────────────────────────────────────

async def create_curation_review(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    evaluation_instance_id: str,
    trigger: str,
    mode: str = "standard",
    deadline_at: Any = None,
    skill_version: str | None = None,
) -> dict[str, Any]:
    """Cria uma curation_review. `mode='blind'` + `deadline_at`/`skill_version` são
    o caminho R8c (Estágio 2); o default 'standard' preserva o fluxo Arc 13."""
    review_id = _new_id("evcuration_")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO evaluation.curation_reviews
                (id, tenant_id, evaluation_instance_id, trigger, mode, deadline_at, skill_version)
            VALUES ($1,$2,$3,$4,$5,$6,$7)
            RETURNING *
            """,
            review_id, tenant_id, evaluation_instance_id, trigger,
            mode, _parse_ts(deadline_at), skill_version,
        )
    return _row(row)  # type: ignore[return-value]


async def count_blind_reviews_for_instance(
    pool: asyncpg.Pool, tenant_id: str, evaluation_instance_id: str,
) -> int:
    """R8c — idempotência: quantas reviews CEGAS já existem p/ esta instância
    (Kafka redelivery / re-finalize não devem dobrar a fila)."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT COUNT(*) AS c FROM evaluation.curation_reviews
             WHERE tenant_id=$1 AND evaluation_instance_id=$2 AND mode='blind'
            """,
            tenant_id, evaluation_instance_id,
        )
    return int(row["c"]) if row else 0


async def expire_overdue_blind_reviews(
    pool: asyncpg.Pool, *, now: Any = None,
) -> int:
    """R8c — SLA SOFT: marca reviews CEGAS pendentes com `deadline_at` vencido como
    expiradas (`expired_at`). Puramente informativo — NÃO toca a avaliação (a nota IA
    já é final/autoritativa), distinto do timeout do revisor de contestação. Idempotente
    (só pega `expired_at IS NULL`). Retorna a contagem expirada."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            UPDATE evaluation.curation_reviews
               SET expired_at = COALESCE($1::timestamptz, now())
             WHERE mode = 'blind'
               AND status = 'pending'
               AND expired_at IS NULL
               AND deadline_at IS NOT NULL
               AND deadline_at < COALESCE($1::timestamptz, now())
            RETURNING id
            """,
            _parse_ts(now),
        )
    return len(rows)


async def get_curation_review(
    pool: asyncpg.Pool, review_id: str, tenant_id: str,
) -> dict[str, Any] | None:
    """R8c — uma curation_review por id (a `list_*` é p/ a fila; aqui é o detalhe)."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM evaluation.curation_reviews WHERE id=$1 AND tenant_id=$2",
            review_id, tenant_id,
        )
    return _row(row)


# ─── R8c — CurationResultBlind (re-pontuação cega) ────────────────────────────

async def create_blind_result(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    curation_review_id: str,
    evaluation_instance_id: str,
    campaign_id: str,
    curator_id: str,
    blind_criterion_responses: list[dict],
    blind_overall_score: float | None,
    blind_by_dimension: list[dict],
    ai_overall_score: float | None,
    ai_by_dimension: list[dict],
    per_dimension_diffs: list[dict],
) -> dict[str, Any]:
    """Persiste a re-pontuação CEGA do curador (artefato de calibração). 1:1 com a review
    (`uq_blind_per_review` → 2ª inserção viola = idempotência checada no router)."""
    blind_id = _new_id("evblind_")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO evaluation.curation_result_blinds
                (id, tenant_id, curation_review_id, evaluation_instance_id, campaign_id,
                 curator_id, blind_criterion_responses, blind_overall_score, blind_by_dimension,
                 ai_overall_score, ai_by_dimension, per_dimension_diffs)
            VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8,$9::jsonb,$10,$11::jsonb,$12::jsonb)
            RETURNING *
            """,
            blind_id, tenant_id, curation_review_id, evaluation_instance_id, campaign_id,
            curator_id, json.dumps(blind_criterion_responses),
            blind_overall_score, json.dumps(blind_by_dimension),
            ai_overall_score, json.dumps(ai_by_dimension), json.dumps(per_dimension_diffs),
        )
    return _row(row)  # type: ignore[return-value]


async def get_blind_result(
    pool: asyncpg.Pool, curation_review_id: str, tenant_id: str,
) -> dict[str, Any] | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM evaluation.curation_result_blinds "
            "WHERE curation_review_id=$1 AND tenant_id=$2",
            curation_review_id, tenant_id,
        )
    return _row(row)


async def resolve_curation_review(
    pool: asyncpg.Pool,
    review_id: str,
    tenant_id: str,
    *,
    status: str,
    curator_id: str,
    curator_notes: str | None = None,
    calibration_note_id: str | None = None,
) -> dict[str, Any] | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE evaluation.curation_reviews
               SET status=$1, curator_id=$2, curator_notes=$3,
                   calibration_note_id=$4, resolved_at=now()
             WHERE id=$5 AND tenant_id=$6
            RETURNING *
            """,
            status, curator_id, curator_notes, calibration_note_id, review_id, tenant_id,
        )
    return _row(row)


async def list_curation_reviews(
    pool: asyncpg.Pool,
    tenant_id: str,
    campaign_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """
    List CurationReview records enriched with:
      - i.campaign_id
      - latest calibration_signal from pre_reviewer_ai thread (nullable)
    Always joins evaluation.instances for enrichment.
    """
    args: list[Any] = [tenant_id]
    conditions = ["cr.tenant_id=$1"]

    if campaign_id:
        args.append(campaign_id)
        conditions.append(f"i.campaign_id=${len(args)}")
    if status:
        args.append(status)
        conditions.append(f"cr.status=${len(args)}")

    where = " AND ".join(conditions)
    args.extend([limit, offset])

    base = f"""
        SELECT cr.*,
               i.campaign_id AS campaign_id,
               (
                 SELECT ct.calibration_signal
                   FROM evaluation.contestation_threads ct
                  WHERE ct.evaluation_instance_id = cr.evaluation_instance_id
                    AND ct.author_type = 'pre_reviewer_ai'
                    AND ct.calibration_signal IS NOT NULL
                  ORDER BY ct.created_at DESC
                  LIMIT 1
               ) AS calibration_signal
          FROM evaluation.curation_reviews cr
          JOIN evaluation.instances i ON i.id = cr.evaluation_instance_id
         WHERE {where}
         ORDER BY cr.created_at DESC
         LIMIT ${len(args)-1} OFFSET ${len(args)}
    """

    async with pool.acquire() as conn:
        rows = await conn.fetch(base, *args)
    results = _rows(rows)
    # calibration_signal is returned as JSON string from asyncpg — parse it
    for r in results:
        if isinstance(r.get("calibration_signal"), str):
            try:
                r["calibration_signal"] = json.loads(r["calibration_signal"])
            except Exception:
                pass
    return results


# ─── Arc 13 — CalibrationNote ─────────────────────────────────────────────────

async def create_calibration_note(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    campaign_id: str,
    dimension_id: str,
    evaluator_id: str,
    skill_version: str,
    text: str,
    severity: str = "low",
    criterion_id: str | None = None,   # T14 (c) — critério implicado (opcional)
) -> dict[str, Any]:
    note_id = _new_id("evcalnote_")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO evaluation.calibration_notes
                (id, tenant_id, campaign_id, dimension_id, criterion_id, evaluator_id,
                 skill_version, text, severity)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            RETURNING *
            """,
            note_id, tenant_id, campaign_id, dimension_id, criterion_id, evaluator_id,
            skill_version, text, severity,
        )
    return _row(row)  # type: ignore[return-value]


async def mark_calibration_note_published(
    pool: asyncpg.Pool,
    note_id: str,
    tenant_id: str,
) -> dict[str, Any] | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE evaluation.calibration_notes SET published_to_kb=TRUE WHERE id=$1 AND tenant_id=$2 RETURNING *",
            note_id, tenant_id,
        )
    return _row(row)


async def list_calibration_notes(
    pool: asyncpg.Pool,
    tenant_id: str,
    campaign_id: str | None = None,
    evaluator_id: str | None = None,
    published_to_kb: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    cond = "WHERE tenant_id=$1"
    args: list[Any] = [tenant_id]
    for col, val in [("campaign_id", campaign_id), ("evaluator_id", evaluator_id)]:
        if val is not None:
            args.append(val)
            cond += f" AND {col}=${len(args)}"
    if published_to_kb is not None:
        args.append(published_to_kb)
        cond += f" AND published_to_kb=${len(args)}"
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT * FROM evaluation.calibration_notes {cond} "
            f"ORDER BY created_at DESC LIMIT ${len(args)+1} OFFSET ${len(args)+2}",
            *args, limit, offset,
        )
    return _rows(rows)


# ─── Arc 13 — CurationSamplingRule ────────────────────────────────────────────

async def create_sampling_rule(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    campaign_id: str,
    rule_type: str,
    params: dict | None = None,
    enabled: bool = True,
    priority: int = 10,
) -> dict[str, Any]:
    rule_id = _new_id("evcsrule_")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO evaluation.curation_sampling_rules
                (id, tenant_id, campaign_id, rule_type, params, enabled, priority)
            VALUES ($1,$2,$3,$4,$5::jsonb,$6,$7)
            RETURNING *
            """,
            rule_id, tenant_id, campaign_id, rule_type,
            json.dumps(params or {}), enabled, priority,
        )
    return _row(row)  # type: ignore[return-value]


async def list_sampling_rules(
    pool: asyncpg.Pool,
    tenant_id: str,
    campaign_id: str,
) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM evaluation.curation_sampling_rules
             WHERE tenant_id=$1 AND campaign_id=$2
             ORDER BY priority ASC, created_at ASC
            """,
            tenant_id, campaign_id,
        )
    return _rows(rows)


async def update_sampling_rule(
    pool: asyncpg.Pool,
    rule_id: str,
    tenant_id: str,
    **fields: Any,
) -> dict[str, Any] | None:
    allowed = {"rule_type", "params", "enabled", "priority"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM evaluation.curation_sampling_rules WHERE id=$1 AND tenant_id=$2",
                rule_id, tenant_id,
            )
        return _row(row)
    set_parts = []
    args: list[Any] = []
    idx = 1
    for k, v in updates.items():
        if k == "params":
            set_parts.append(f"{k}=${idx}::jsonb")
            args.append(json.dumps(v))
        else:
            set_parts.append(f"{k}=${idx}")
            args.append(v)
        idx += 1
    args.extend([rule_id, tenant_id])
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"UPDATE evaluation.curation_sampling_rules SET {', '.join(set_parts)} "
            f"WHERE id=${idx} AND tenant_id=${idx+1} RETURNING *",
            *args,
        )
    return _row(row)


async def delete_sampling_rule(pool: asyncpg.Pool, rule_id: str, tenant_id: str) -> bool:
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM evaluation.curation_sampling_rules WHERE id=$1 AND tenant_id=$2",
            rule_id, tenant_id,
        )
    return result.split()[-1] != "0"


# ─── Arc 13 — Result finalization helpers ─────────────────────────────────────

# T1 — mapeamento do contestation_state legado → finalize_reason canônico.
_FINALIZE_REASON_MAP = {
    "auto_finalized":       "auto_ai",
    "closed_upheld":        "upheld",
    "closed_revised":       "revised",
    "closed_max_rounds":    "max_rounds",
    "timeout_contestation": "contest_timeout",
    "timeout_review":       "review_timeout",
}

# T1 — mapeamento do contestation_state legado → result_state canônico.
_RESULT_STATE_MAP = {
    "pre_review_pending": "ai_review",
    "contestation_open":  "open",
    "under_review":       "under_review",
}


async def finalize_result(
    pool: asyncpg.Pool,
    result_id: str,
    *,
    contestation_state: str,
    final_score: float,
    process_duration_ms: int,
    finalize_reason: str | None = None,
) -> dict[str, Any] | None:
    """
    Mark a result as evaluation_finalized.
    Sets result_state='finalized' + finalize_reason (canônico) e, em lockstep,
    contestation_state (legado) + eval_status='locked' (espelho depreciado).
    `finalize_reason` explícito (T3) vence; senão deriva do contestation_state.
    Idempotente: guarda por result_state já 'finalized'.
    """
    reason = finalize_reason or _FINALIZE_REASON_MAP.get(contestation_state)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE evaluation.results
               SET result_state        = 'finalized',
                   finalize_reason     = $1,
                   contestation_state  = $2,
                   final_score         = $3,
                   process_duration_ms = $4,
                   finalized_at        = now(),
                   eval_status         = 'locked',
                   locked_at           = now(),
                   locked_by           = 'arc13_finalization',
                   lock_reason         = $5,
                   action_required     = NULL,
                   resume_token        = NULL,
                   updated_at          = now()
             WHERE id = $6
               AND result_state IS DISTINCT FROM 'finalized'
            RETURNING *
            """,
            reason, contestation_state, final_score, process_duration_ms,
            contestation_state, result_id,
        )
    return _row(row)


async def set_contestation_state(
    pool: asyncpg.Pool,
    result_id: str,
    state: str,
    *,
    action_required: str | None = None,
    current_round: int | None = None,
) -> dict[str, Any] | None:
    """Update contestation_state (legado) + result_state canônico (em lockstep) e,
    opcionalmente, action_required / round on a result."""
    rstate = _RESULT_STATE_MAP.get(state)  # None p/ estados não mapeados → mantém o atual
    if current_round is not None:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE evaluation.results
                   SET contestation_state = $1,
                       result_state       = COALESCE($2, result_state),
                       action_required    = $3,
                       current_round      = $4,
                       round              = $5,
                       updated_at         = now()
                 WHERE id = $6
                RETURNING *
                """,
                state, rstate, action_required, current_round, current_round, result_id,
            )
    else:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE evaluation.results
                   SET contestation_state = $1,
                       result_state       = COALESCE($2, result_state),
                       action_required    = $3,
                       updated_at         = now()
                 WHERE id = $4
                RETURNING *
                """,
                state, rstate, action_required, result_id,
            )
    return _row(row)


# ─── T4 — Deadline scanner ─────────────────────────────────────────────────────

async def set_deadline_at(
    pool: asyncpg.Pool, result_id: str, deadline_at: datetime,
) -> None:
    """Grava o deadline_at (computado na entrada do estado open/under_review)."""
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE evaluation.results SET deadline_at=$1, updated_at=now() WHERE id=$2",
            deadline_at, result_id,
        )


async def list_expired_results(
    pool: asyncpg.Pool, *, limit: int = 200,
) -> list[dict[str, Any]]:
    """Resultados em open/under_review com deadline_at vencido — alvo do scanner."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM evaluation.results
             WHERE result_state IN ('open', 'under_review')
               AND deadline_at IS NOT NULL
               AND deadline_at <= now()
             ORDER BY deadline_at ASC
             LIMIT $1
            """,
            limit,
        )
    return [_row(r) for r in rows]


# ─── T15 — dispatcher por janela de calendário ────────────────────────────────

async def list_active_campaigns(
    pool: asyncpg.Pool, *, limit: int = 500,
) -> list[dict[str, Any]]:
    """Campanhas ativas de TODOS os tenants — alvo do dispatch scanner (§18.4)."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM evaluation.campaigns
             WHERE status = 'active'
             ORDER BY created_at ASC
             LIMIT $1
            """,
            limit,
        )
    return _rows(rows)


async def claim_dispatchable_instances(
    pool: asyncpg.Pool,
    campaign_id: str,
    tenant_id: str,
    *,
    cooldown_s: int,
    limit: int,
) -> list[dict[str, Any]]:
    """T15 — reivindica atomicamente as instances `scheduled` despacháveis da campanha e
    carimba `dispatched_at=now()` no mesmo UPDATE (idempotência + race-safe entre ciclos do
    scanner). Despacháveis = `scheduled`, não expiradas, e que nunca foram despachadas ou
    cuja última tentativa já passou do cooldown (re-despacha se o avaliador não pegou).
    Retorna as linhas reivindicadas (para emitir `evaluation.requested`)."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            UPDATE evaluation.instances
               SET dispatched_at = now(),
                   updated_at    = now()
             WHERE id IN (
               SELECT id FROM evaluation.instances
                WHERE campaign_id = $1
                  AND tenant_id   = $2
                  AND status      = 'scheduled'
                  AND (expires_at IS NULL OR expires_at > now())
                  AND (dispatched_at IS NULL
                       OR dispatched_at < now() - make_interval(secs => $3))
                ORDER BY priority ASC, scheduled_at ASC
                LIMIT $4
                FOR UPDATE SKIP LOCKED
             )
            RETURNING *
            """,
            campaign_id, tenant_id, float(cooldown_s), limit,
        )
    return _rows(rows)


# ─── T9-A2 — sumário por campanha (nível 1 da lista de Avaliações) ────────────

async def campaign_summaries(
    pool: asyncpg.Pool, tenant_id: str, *, campaign_ids: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Agregados por campanha p/ o nível 1 (cards de campanha). **Global por campanha**
    (o consolidado é a média da campanha; o escopo de QUAIS campanhas o viewer vê é do
    caller — ABAC+Pools). Poucas queries GROUP BY tenant-wide. Spec §7.5 / blueprint T9."""
    cond = ""
    args: list[Any] = [tenant_id]
    if campaign_ids:
        args.append(campaign_ids)
        cond = f" AND campaign_id = ANY(${len(args)})"

    out: dict[str, dict[str, Any]] = {}
    def slot(cid: str) -> dict[str, Any]:
        return out.setdefault(cid, {
            "instance_status": {}, "result_state": {}, "finalize_reason": {},
            "evaluated": {}, "avg_process_ms": None, "sla_overdue": 0, "total_results": 0,
        })

    async with pool.acquire() as conn:
        for r in await conn.fetch(
            f"SELECT campaign_id, status, COUNT(*) AS n FROM evaluation.instances "
            f"WHERE tenant_id=$1{cond} GROUP BY campaign_id, status", *args):
            slot(r["campaign_id"])["instance_status"][r["status"]] = int(r["n"])

        for r in await conn.fetch(
            f"SELECT campaign_id, result_state, COUNT(*) AS n FROM evaluation.results "
            f"WHERE tenant_id=$1{cond} GROUP BY campaign_id, result_state", *args):
            s = slot(r["campaign_id"])
            s["result_state"][r["result_state"] or "unknown"] = int(r["n"])
            s["total_results"] += int(r["n"])

        for r in await conn.fetch(
            f"SELECT campaign_id, finalize_reason, COUNT(*) AS n FROM evaluation.results "
            f"WHERE tenant_id=$1 AND result_state='finalized' AND finalize_reason IS NOT NULL{cond} "
            f"GROUP BY campaign_id, finalize_reason", *args):
            slot(r["campaign_id"])["finalize_reason"][r["finalize_reason"]] = int(r["n"])

        for r in await conn.fetch(
            f"SELECT campaign_id, evaluated_agent_type, COUNT(*) AS n FROM evaluation.results "
            f"WHERE tenant_id=$1 AND evaluated_agent_type IS NOT NULL{cond} "
            f"GROUP BY campaign_id, evaluated_agent_type", *args):
            slot(r["campaign_id"])["evaluated"][r["evaluated_agent_type"]] = int(r["n"])

        for r in await conn.fetch(
            f"SELECT campaign_id, AVG(process_duration_ms)::float AS avg_ms FROM evaluation.results "
            f"WHERE tenant_id=$1 AND result_state='finalized' AND process_duration_ms IS NOT NULL{cond} "
            f"GROUP BY campaign_id", *args):
            slot(r["campaign_id"])["avg_process_ms"] = r["avg_ms"]

        for r in await conn.fetch(
            f"SELECT campaign_id, COUNT(*) AS n FROM evaluation.results "
            f"WHERE tenant_id=$1 AND result_state IN ('open','under_review') "
            f"AND deadline_at IS NOT NULL AND deadline_at < now(){cond} "
            f"GROUP BY campaign_id", *args):
            slot(r["campaign_id"])["sla_overdue"] = int(r["n"])

    return out


# Status de instância que ainda NÃO produziram nota oficial (fechamento pendente):
#   - amostrado/sem avaliar:  scheduled, assigned, in_progress  (backlog da IA)
#   - provisório, em revisão: under_review, contested           (lag humano)
# Terminais que NÃO contam como pendente: completed/reviewed/locked (finalizados),
# skipped (thin-session), expired/error (mortos — nunca vão finalizar).
_PENDING_INSTANCE_STATUSES = (
    "scheduled", "assigned", "in_progress", "under_review", "contested",
)


def _parse_dt(s: str | None) -> datetime | None:
    """Aceita 'YYYY-MM-DD' ou 'YYYY-MM-DD HH:MM:SS' (ou ISO com Z) → datetime UTC.
    asyncpg exige objeto datetime para colunas timestamptz (não aceita str)."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        dt = datetime.strptime(s[:10], "%Y-%m-%d")
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def deploy_coverage(
    pool: asyncpg.Pool, tenant_id: str, *,
    pool_id: str | None = None, from_dt: str | None = None, to_dt: str | None = None,
) -> list[dict[str, Any]]:
    """Cobertura por `(pool, deploy_version)` p/ o overlay do epoch (Arc 6 Fase 2,
    micro-fatia 1b). Insumo da Opção II: nota PROVISÓRIA (só avaliações já pontuadas)
    + backlog (instâncias amostradas não finalizadas) por versão.

    - `provisional_avg` = AVG(results.normalized_score 0–1) das instâncias com nota;
      `provisional_n` = quantas têm nota. **Só pontuadas** (decisão: não estima o
      universo amostrado).
    - `pending_n` = instâncias amostradas ainda não finalizadas (status ∈
      `_PENDING_INSTANCE_STATUSES`). É o "N pendentes de fechamento".
    Pool = `COALESCE(c.evaluation_pool_id, c.pool_id)` (pool avaliado). Janela por
    `instances.created_at` (alinha à janela do gráfico). Só versão carimbada (R9d)."""
    args: list[Any] = [tenant_id]
    cond = "i.tenant_id = $1 AND i.deploy_version IS NOT NULL AND i.deploy_version <> ''"
    dt_from, dt_to = _parse_dt(from_dt), _parse_dt(to_dt)
    if dt_from:
        args.append(dt_from); cond += f" AND i.created_at >= ${len(args)}"
    if dt_to:
        args.append(dt_to);   cond += f" AND i.created_at <  ${len(args)}"
    if pool_id:
        args.append(pool_id)
        cond += f" AND COALESCE(c.evaluation_pool_id, c.pool_id) = ${len(args)}"

    pending_list = ", ".join(f"'{s}'" for s in _PENDING_INSTANCE_STATUSES)
    async with pool.acquire() as conn:
        rows = await conn.fetch(f"""
            SELECT
                COALESCE(c.evaluation_pool_id, c.pool_id) AS pool_id,
                i.deploy_version                          AS deploy_version,
                COUNT(*) FILTER (WHERE i.status IN ({pending_list}))        AS pending_n,
                COUNT(r.normalized_score)                                   AS provisional_n,
                AVG(r.normalized_score)::float                             AS provisional_avg
            FROM evaluation.instances i
            JOIN evaluation.campaigns c ON c.id = i.campaign_id
            LEFT JOIN evaluation.results r ON r.instance_id = i.id
            WHERE {cond}
            GROUP BY 1, 2
        """, *args)
    return [
        {
            "pool_id":         r["pool_id"],
            "deploy_version":  r["deploy_version"],
            "pending_n":       int(r["pending_n"] or 0),
            "provisional_n":   int(r["provisional_n"] or 0),
            "provisional_avg": round(r["provisional_avg"], 4) if r["provisional_avg"] is not None else None,
        }
        for r in rows
    ]


# ─── Pool factory ─────────────────────────────────────────────────────────────

async def create_pool(dsn: str) -> asyncpg.Pool:
    return await asyncpg.create_pool(
        dsn,
        min_size=2,
        max_size=10,
        command_timeout=30,
    )
