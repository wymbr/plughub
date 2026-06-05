"""
seed.py
Platform configuration seed — all values currently hardcoded across packages.

Running this script populates the platform_config table with global defaults.
Existing entries are NOT overwritten (ON CONFLICT DO NOTHING logic via store).
Tenant-specific overrides are never set by the seed — only global defaults.

Namespaces:
  sentiment          — AI Gateway sentiment scoring
  routing            — Routing Engine scheduling and SLA
  session            — Session TTLs per component (all TTLs centralised here)
  analytics_consumer — Analytics API Kafka consumer  (replaces: consumer)
  dashboard          — Analytics API dashboard SSE
  webchat            — Channel Gateway webchat adapter
  audit_policy       — Message masking/audit access policies  (replaces: masking)
  pricing            — Unit prices per resource type, currency, reserve markup
  ai_gateway         — Multi-account rotation, workload isolation, evaluation model
  agent_activity     — Agent pause/resume reasons (Arc 8)
  evaluation         — Evaluation platform defaults
  dashboards         — Dashboard template management

Note: 'quota' namespace removed. Per-tenant limits ({tenant}:quota:*) are written
  directly by the pricing integration when a plan is activated — not seeded here.
  checkConcurrentSessions() and assertQuota() in mcp-server read from Redis keys
  set by pricing-api, not from Config API.

Deprecated aliases (kept for backward compatibility with existing deployments):
  masking            — alias for audit_policy (do not add new keys here)
  consumer           — alias for analytics_consumer (do not add new keys here)

Run:
  PLUGHUB_CONFIG_DATABASE_URL=... PLUGHUB_CONFIG_REDIS_URL=... python -m plughub_config_api.seed
"""
from __future__ import annotations

import asyncio
import logging

import asyncpg
import redis.asyncio as aioredis

from .cache import ConfigCache
from .config import get_settings
from .store import ConfigStore

logger = logging.getLogger("plughub.config.seed")

# ─── seed data ────────────────────────────────────────────────────────────────
# Format: (namespace, key, value, description)
# All entries are global defaults (tenant_id = None → '__global__').

_SEED: list[tuple[str, str, object, str]] = [

    # ── sentiment ─────────────────────────────────────────────────────────────
    # Source: ai-gateway/sentiment_emitter.py (_classify function)
    (
        "sentiment", "thresholds",
        {
            "satisfied":  [0.3,  1.0],
            "neutral":    [-0.3, 0.3],
            "frustrated": [-0.6, -0.3],
            "angry":      [-1.0, -0.6],
        },
        "Score ranges per category. Boundaries: lower inclusive, upper exclusive "
        "(except angry which is lower inclusive). Applied at read time."
    ),
    (
        "sentiment", "live_ttl_s",
        300,
        "Redis TTL (seconds) for the sentiment_live hash "
        "({tenant}:pool:{pool}:sentiment_live). Source: ai-gateway/sentiment_emitter.py"
    ),

    # ── routing ───────────────────────────────────────────────────────────────
    # Source: routing-engine/registry.py, router.py, kafka_listener.py
    (
        "routing", "snapshot_ttl_s",
        120,
        "Redis TTL (seconds) for pool operational snapshots "
        "({tenant}:pool:{pool}:snapshot). Stale snapshots are excluded from the "
        "dashboard automatically."
    ),
    (
        "routing", "sla_default_ms",
        480_000,
        "Default SLA target in milliseconds (8 minutes) used when a pool "
        "does not define its own sla_target_ms. Source: routing-engine/kafka_listener.py"
    ),
    (
        "routing", "estimated_wait_factor",
        0.7,
        "Conservative factor applied to sla_target_ms to compute estimated_wait_ms "
        "when a contact is queued. estimated_wait = queue_length × sla_target × factor. "
        "Source: routing-engine/router.py"
    ),
    (
        "routing", "score_weights",
        {
            "skill_match":    1.0,
            "availability":   1.0,
            "aging_factor":   0.5,
            "breach_factor":  2.0,
        },
        "Scoring algorithm weight factors for agent allocation. "
        "breach_factor amplifies priority for contacts that have exceeded SLA."
    ),
    (
        "routing", "congestion_sla_factor",
        1.5,
        "Multiplier applied to sla_target_ms to define the congestion SLA threshold. "
        "When queue wait exceeds sla × factor, the pool is considered congested. "
        "Source: routing-engine/saturated.py"
    ),
    (
        "routing", "performance_score_weight",
        0.0,
        "Arc 7d — Weight (0.0–1.0) given to historical agent performance when scoring "
        "instances. 0.0 = pure competency match (default — backward-compatible, no Redis "
        "reads). 0.3 = 70% competency + 30% historical performance (recommended in "
        "production). Read from env PLUGHUB_PERFORMANCE_SCORE_WEIGHT or this Config API "
        "key. Source: routing-engine/config.py, routing-engine/scorer.py"
    ),
    # Render v2 (queue-attended-model) — mensagens de sistema viradas ao cliente.
    # O tenant edita aqui no idioma desejado; hot-reload via config.changed.
    # As mensagens da fila ATENDIDA (saudação, timeout) são do skill-flow YAML.
    (
        "routing", "msg_queue_waiting",
        "Aguardando agente disponível. Por favor, aguarde...",
        "Mensagem ao cliente ao entrar em fila MUDA (pool sem queue_config). "
        "Source: routing-engine/main.py _persist_queued_contact"
    ),
    (
        "routing", "msg_outage_rejection",
        "Não há atendentes disponíveis no momento. Por favor, tente novamente mais tarde.",
        "Mensagem de rejeição na porta (outage: reservation_full/shared_full/quota), "
        "entregue como farewell_text no session.closed. Source: routing-engine/main.py _emit_outage"
    ),
    (
        "routing", "msg_queue_timeout",
        "Tempo máximo de espera atingido. Por favor, tente novamente mais tarde.",
        "Mensagem de timeout de fila MUDA (max_wait_exceeded), farewell_text no close. "
        "Fila atendida usa o notify do skill-flow. Source: routing-engine/main.py _emit_queue_timeout"
    ),
    (
        "routing", "msg_no_resource",
        "Não há recurso disponível para continuar o atendimento. Por favor, tente novamente mais tarde.",
        "Mensagem do drop gracioso (sem pool/recurso, close_reason=no_resource), "
        "farewell_text no close. Source: routing-engine/main.py _emit_no_resource_drop"
    ),

    # ── routing: fila de sistema (system-queue.md, Fase A) ────────────────────
    (
        "routing", "queue_max_total",
        100,
        "Teto TOTAL do buffer de fila muda/gratuita (sessões isentas de C, "
        "SCARD de {t}:queue:unadmitted). Hard limit: Config API fora ⇒ default "
        "100 no código, nunca ilimitado. Estouro ⇒ outage causa queue_full. "
        "Source: routing-engine/mute_queue.py"
    ),
    (
        "routing", "queue_max_wait_by_channel",
        {"voice": 300, "webrtc": 300, "webchat": 1800, "whatsapp": 14_400},
        "Teto de espera em fila MUDA por canal (segundos). 0 = canal não aceita "
        "fila muda (vai direto a outage — recomendado p/ voz: espera muda em voz "
        "é dead air segurando trunk). Canais ausentes usam o default global "
        "(1800s). Fila ATENDIDA usa queue_config.max_wait_s do pool. "
        "Source: routing-engine/mute_queue.py + _periodic_queue_drain"
    ),
    (
        "routing", "msg_queue_full",
        "Nossa fila de espera está cheia no momento. Por favor, tente novamente mais tarde.",
        "Mensagem de rejeição quando o buffer de fila gratuita está cheio "
        "(outage causa queue_full), farewell_text no close. "
        "Source: routing-engine/main.py (overflow da admissão)"
    ),

    # ── session ───────────────────────────────────────────────────────────────
    # Central TTL registry for all components that manage session-scoped Redis keys.
    # Consumers: ai-gateway, channel-gateway, orchestrator-bridge, conversation-writer,
    #            session_replayer, routing-engine (pool config cache), sentiment_emitter.
    (
        "session", "ai_gateway_ttl_s",
        86_400,
        "Redis TTL (seconds) for AI Gateway session state (pipeline_state, history). "
        "24 hours. Source: ai-gateway/config.py"
    ),
    (
        "session", "channel_gateway_ttl_s",
        14_400,
        "Redis TTL (seconds) for Channel Gateway session references. "
        "4 hours. Source: channel-gateway/config.py"
    ),
    (
        "session", "orchestrator_session_ttl_s",
        14_400,
        "Redis TTL (seconds) for orchestrator-bridge session state "
        "(session:{id}:* keys managed by the bridge). 4 hours. "
        "Currently hardcoded as 14400 in orchestrator-bridge/main.py — "
        "migrating to dynamic read from this key."
    ),
    (
        "session", "transcript_ttl_s",
        14_400,
        "Redis TTL (seconds) for transcript entries written by conversation-writer. "
        "4 hours. Currently hardcoded as transcript_ttl_seconds in conversation-writer."
    ),
    (
        "session", "replayer_hydration_ttl_s",
        3_600,
        "Redis TTL (seconds) for session data hydrated into Redis by the Hydrator "
        "(session_replayer) before evaluation. 1 hour. "
        "Currently hardcoded as HYDRATION_TTL_SECONDS in session_replayer."
    ),
    (
        "session", "replay_context_ttl_s",
        3_600,
        "Redis TTL (seconds) for the ReplayContext hash "
        "({tenant}:replay:{session_id}:context). 1 hour. "
        "Currently hardcoded as REPLAY_CONTEXT_TTL in session_replayer."
    ),
    (
        "session", "pool_config_ttl_s",
        3_600,
        "Redis TTL (seconds) for pool configuration snapshots cached by "
        "orchestrator-bridge (used by PoolConfigCache). 1 hour. "
        "Routing-engine has a separate pool_config_ttl_seconds (24h) for its own cache."
    ),
    (
        "session", "sentiment_live_ttl_s",
        300,
        "Redis TTL (seconds) for the sentiment_live hash "
        "({tenant}:pool:{pool}:sentiment_live) written by sentiment_emitter. "
        "5 minutes. Kept here (session namespace) alongside sentiment.live_ttl_s "
        "so orchestrator-bridge can read all TTLs from a single namespace."
    ),
    (
        "session", "queue_default_agent_type_id",
        "",
        "Fase C (queue-attended-model): tenant-wide default queue-treatment agent. "
        "Used by orchestrator-bridge process_queued when the target pool has no "
        "queue_config — empty string disables the fallback (contact waits silently). "
        "Example: 'agente_fila_v1'."
    ),
    (
        "session", "queue_default_skill_id",
        "",
        "Fase C (queue-attended-model): optional skill override paired with "
        "queue_default_agent_type_id (same semantics as queue_config.skill_id). "
        "Empty = use the agent's default skill."
    ),

    # ── analytics_consumer ────────────────────────────────────────────────────
    # Source: analytics-api/config.py, consumer.py
    # Renamed from 'consumer' → 'analytics_consumer' for clarity.
    # Deprecated aliases kept below under 'consumer' namespace.
    (
        "analytics_consumer", "batch_size",
        200,
        "Maximum number of Kafka records fetched per getmany() call in the "
        "analytics-api consumer. Tune for throughput vs latency. "
        "Source: analytics-api/config.py"
    ),
    (
        "analytics_consumer", "timeout_ms",
        500,
        "Kafka consumer poll timeout in milliseconds (getmany). "
        "Source: analytics-api/config.py"
    ),
    (
        "analytics_consumer", "restart_delay_s",
        5,
        "Initial delay before restarting the consumer after a crash. "
        "Doubles on each failure up to max_restart_delay_s. "
        "Source: analytics-api/main.py (_run_consumer_safe)"
    ),
    (
        "analytics_consumer", "max_restart_delay_s",
        60,
        "Maximum delay between consumer restarts. "
        "Source: analytics-api/main.py (_run_consumer_safe)"
    ),

    # ── consumer (deprecated alias for analytics_consumer) ────────────────────
    # Kept so existing deployments that already read from 'consumer' continue to work.
    # Do NOT add new keys here — use 'analytics_consumer' instead.
    (
        "consumer", "batch_size",
        200,
        "[DEPRECATED — use analytics_consumer.batch_size] "
        "Maximum number of Kafka records fetched per getmany() call."
    ),
    (
        "consumer", "timeout_ms",
        500,
        "[DEPRECATED — use analytics_consumer.timeout_ms] "
        "Kafka consumer poll timeout in milliseconds."
    ),
    (
        "consumer", "restart_delay_s",
        5,
        "[DEPRECATED — use analytics_consumer.restart_delay_s] "
        "Initial delay before restarting the consumer after a crash."
    ),
    (
        "consumer", "max_restart_delay_s",
        60,
        "[DEPRECATED — use analytics_consumer.max_restart_delay_s] "
        "Maximum delay between consumer restarts."
    ),

    # ── dashboard ─────────────────────────────────────────────────────────────
    # Source: analytics-api/dashboard.py
    (
        "dashboard", "sse_interval_s",
        5,
        "Interval in seconds between SSE pushes on GET /dashboard/operational. "
        "Source: analytics-api/dashboard.py"
    ),
    (
        "dashboard", "sse_retry_ms",
        3_000,
        "SSE retry hint sent to the client (milliseconds). "
        "Tells the browser how long to wait before reconnecting on disconnect. "
        "Source: analytics-api/dashboard.py"
    ),

    # ── webchat ───────────────────────────────────────────────────────────────
    # Source: channel-gateway/config.py, adapters/webchat.py
    (
        "webchat", "auth_timeout_s",
        30,
        "Seconds the server waits for a conn.authenticate message after WebSocket "
        "connection is accepted. Connection is dropped on timeout. "
        "Source: channel-gateway/config.py"
    ),
    (
        "webchat", "attachment_expiry_days",
        30,
        "Days before uploaded attachments are soft-deleted (stage 1 expiry). "
        "Physical deletion occurs 24h later (stage 2). "
        "Source: channel-gateway/config.py"
    ),
    (
        "webchat", "upload_limits_mb",
        {
            "image":    16,
            "pdf":      100,
            "video":    512,
        },
        "Maximum upload size in MB per content type. "
        "MIME allowlist: image/jpeg, image/png, image/webp, image/gif, "
        "application/pdf, video/mp4, video/webm."
    ),

    # ── audit_policy ──────────────────────────────────────────────────────────
    # Source: schemas/audit.ts (DEFAULT_MASKING_RULES, MaskingAccessPolicy)
    # Renamed from 'masking' → 'audit_policy' to reflect broader scope (LGPD audit,
    # token masking, capture policy). Deprecated aliases kept below under 'masking'.
    (
        "audit_policy", "authorized_roles",
        ["evaluator", "reviewer"],
        "Roles that can read original_content (unmasked) in session_context_get. "
        "primary and specialist always receive masked (display_partial) content. "
        "Source: schemas/audit.ts MaskingAccessPolicy"
    ),
    (
        "audit_policy", "default_retention_days",
        90,
        "Default number of days masked tokens are retained in the audit trail. "
        "After this period, token resolution may return null."
    ),
    (
        "audit_policy", "capture_input_default",
        False,
        "Whether MCP tool call inputs are captured in audit records by default. "
        "Can be overridden per tool via tool-level audit_policy config. "
        "Source: schemas/audit.ts DEFAULT_MASKING_RULES"
    ),
    (
        "audit_policy", "capture_output_default",
        False,
        "Whether MCP tool call outputs are captured in audit records by default. "
        "Source: schemas/audit.ts DEFAULT_MASKING_RULES"
    ),

    # ── masking (deprecated alias for audit_policy) ───────────────────────────
    # Kept so existing deployments that already read from 'masking' continue to work.
    # Do NOT add new keys here — use 'audit_policy' instead.
    (
        "masking", "authorized_roles",
        ["evaluator", "reviewer"],
        "[DEPRECATED — use audit_policy.authorized_roles] "
        "Roles that can read original_content (unmasked)."
    ),
    (
        "masking", "default_retention_days",
        90,
        "[DEPRECATED — use audit_policy.default_retention_days] "
        "Days masked tokens are retained in the audit trail."
    ),
    (
        "masking", "capture_input_default",
        False,
        "[DEPRECATED — use audit_policy.capture_input_default] "
        "Whether MCP tool call inputs are captured in audit records by default."
    ),
    (
        "masking", "capture_output_default",
        False,
        "[DEPRECATED — use audit_policy.capture_output_default] "
        "Whether MCP tool call outputs are captured in audit records by default."
    ),

    # ── pricing ───────────────────────────────────────────────────────────────
    # Source: packages/pricing-api — unit prices per resource type.
    # All values in the platform's base currency (see pricing.currency).
    # reserve_markup_pct: additional surcharge applied to reserve pool capacity.
    # Editable per tenant to support custom commercial agreements.
    (
        "pricing", "currency",
        "BRL",
        "ISO 4217 currency code used in all invoice calculations. "
        "Change to 'USD' or 'EUR' for international deployments."
    ),
    (
        "pricing", "unit_prices",
        {
            "ai_agent":          120.00,
            "human_agent":        50.00,
            "whatsapp_number":    15.00,
            "voice_trunk_in":     40.00,
            "voice_trunk_out":    40.00,
            "email_inbox":        25.00,
            "sms_number":         10.00,
            "webchat_instance":   20.00,
        },
        "Monthly unit price per resource type (base capacity). "
        "Keys match resource_type values in pricing.installation_resources. "
        "Reserve pools use the same unit prices, scaled by reserve_markup_pct."
    ),
    (
        "pricing", "reserve_markup_pct",
        0.0,
        "Percentage surcharge applied on top of base unit prices for reserve pool "
        "capacity. 0.0 = same price as base. 10.0 = 10% more expensive than base. "
        "Allows operators to price reserve capacity at a premium."
    ),
    (
        "pricing", "billing_cycle_day",
        1,
        "Day of month when the billing cycle resets (1 = first of month). "
        "Used by the invoice calculator to determine cycle_start when not "
        "explicitly provided."
    ),

    # ── ai_gateway ────────────────────────────────────────────────────────────
    # Source: packages/ai-gateway — multi-account + workload isolation config.
    (
        "ai_gateway", "account_rotation_enabled",
        True,
        "Whether the AccountSelector is used for load balancing across multiple "
        "API keys. When false, the first key is always used. "
        "Source: ai-gateway/account_selector.py"
    ),
    (
        "ai_gateway", "throttle_retry_after_s",
        60,
        "How long (seconds) an account is excluded from selection after receiving "
        "a 429 or 529 response. Source: ai-gateway/account_selector.py"
    ),
    (
        "ai_gateway", "utilization_rpm_weight",
        0.7,
        "Weight of RPM utilization in the AccountSelector scoring function. "
        "TPM weight = 1 - rpm_weight. Higher values prefer accounts with lower "
        "request rate. Source: ai-gateway/account_selector.py"
    ),
    (
        "ai_gateway", "evaluation_model",
        "claude-haiku-4-5-20251001",
        "Model ID used for the 'evaluation' model profile — batch evaluation "
        "workload. Isolated from realtime agents to avoid contention. "
        "Override with 'claude-sonnet-4-6' for higher-quality evaluations. "
        "Source: ai-gateway/config.py"
    ),
    (
        "ai_gateway", "evaluation_max_tokens",
        2048,
        "Max tokens for evaluation inference calls. Higher than default (1024) "
        "because evaluation responses include per-criterion justification. "
        "Source: ai-gateway/config.py"
    ),
    (
        "ai_gateway", "openai_fallback_enabled",
        False,
        "Whether OpenAI is used as a fallback provider when all Anthropic accounts "
        "are throttled. Requires PLUGHUB_OPENAI_API_KEY(S) to be set. "
        "Source: ai-gateway/main.py"
    ),

    # ── evaluation ────────────────────────────────────────────────────────────────
    (
        "evaluation", "workflow_context_ttl_s",
        604800,
        "TTL in seconds for ContextStore entries written by the evaluation workflow motor. "
        "Default 7 days (604800s) — longer than the standard session TTL (4h) to support "
        "multi-day review/contestation cycles. Configurable per tenant for compliance. "
        "Source: evaluation-api/config.py"
    ),
    (
        "evaluation", "default_review_skill_id",
        "skill_revisao_simples_v1",
        "Default review workflow skill used when a campaign does not specify "
        "review_workflow_skill_id. Options: skill_revisao_simples_v1 (1 round), "
        "skill_revisao_treplica_v1 (up to 3 rounds). "
        "Source: evaluation-api/router.py"
    ),
    (
        "evaluation", "review_deadline_hours",
        48,
        "Default SLA in business hours for each review round. "
        "Maps to timeout_hours in the suspend step of the review workflow skill. "
        "Source: evaluation-api/config.py, skill_revisao_*.yaml"
    ),
    (
        "evaluation", "contestation_deadline_hours",
        72,
        "Default SLA in business hours for each contestation window. "
        "Maps to timeout_hours in the aguardar_contestacao step of the treplica workflow. "
        "Source: skill_revisao_treplica_v1.yaml"
    ),
    (
        "evaluation", "auto_lock_on_workflow_complete",
        True,
        "When True, a workflow.completed event triggers automatic locking of the "
        "evaluation result (lock_reason=completed). Set to False to require explicit "
        "POST /v1/evaluation/results/{id}/lock by an operator. "
        "Source: evaluation-api/main.py"
    ),

    # ── agent_activity ────────────────────────────────────────────────────────
    # Source: orchestrator-bridge (agent_pause events), Agent Assist UI (PauseReasonModal)
    # pause_reasons: list of { id, label, requires_note } used in the PauseReasonModal.
    # Agents must select a reason before pausing; if requires_note=true, a textarea is shown.
    # Tenant-specific overrides can add/replace entries via Config API.
    (
        "agent_activity", "pause_reasons",
        [
            {"id": "intervalo",    "label": "Intervalo",        "requires_note": False},
            {"id": "almoco",       "label": "Almoço",           "requires_note": False},
            {"id": "treinamento",  "label": "Treinamento",      "requires_note": False},
            {"id": "reuniao",      "label": "Reunião",          "requires_note": True},
            {"id": "outro",        "label": "Outro",            "requires_note": True},
        ],
        "List of pause reason objects { id, label, requires_note } shown in the "
        "PauseReasonModal in Agent Assist UI. When requires_note=true, the agent must "
        "enter a free-text note before confirming. Pool-level overrides can be configured "
        "via key pause_reasons:{pool_id}. Source: platform-ui PauseReasonModal, "
        "orchestrator-bridge agent_pause Kafka event."
    ),

    # ── dashboards ────────────────────────────────────────────────────────────
    # Source: platform-ui DashboardsPage — dashboard template management.
    # Templates are stored as JSON values with key pattern template:{uuid}.
    # default_template_id: which template is loaded when no user override exists.
    # allow_user_customization: users may drag/resize cards and save a personal layout.
    # max_cards_per_dashboard: guard against runaway dashboard configs.
    (
        "dashboards", "default_template_id",
        None,
        "UUID of the default dashboard template loaded for users without a personal "
        "override. null = show an empty grid with an 'Add card' prompt. "
        "Set this to a template UUID after creating your first template."
    ),
    (
        "dashboards", "allow_user_customization",
        True,
        "When True, users can drag, resize, and save a personal layout override "
        "on top of their assigned template. Admins always retain full edit access."
    ),
    (
        "dashboards", "max_cards_per_dashboard",
        20,
        "Maximum number of cards allowed per dashboard template. "
        "Prevents performance issues from overly large dashboards."
    ),
]


# ─── seed runner ─────────────────────────────────────────────────────────────

async def seed(store: ConfigStore, *, overwrite: bool = False) -> dict[str, int]:
    """
    Seeds all global default values.

    If overwrite=False (default): skips entries that already exist.
    If overwrite=True: updates all entries (useful for schema migrations).

    Returns {"inserted": N, "skipped": N}.
    """
    inserted = 0
    skipped  = 0

    for namespace, key, value, description in _SEED:
        if not overwrite:
            existing = await store.get_entry("__global__", namespace, key)
            if existing is not None:
                skipped += 1
                continue
        await store.set(None, namespace, key, value, description)
        inserted += 1
        logger.info("seeded %s.%s", namespace, key)

    logger.info("seed complete: inserted=%d skipped=%d", inserted, skipped)
    return {"inserted": inserted, "skipped": skipped}


async def _run() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()

    pool  = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=3)
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)

    cache = ConfigCache(redis, ttl=settings.cache_ttl_s)
    store = ConfigStore(pool, cache)
    await store.setup()

    result = await seed(store)
    print(f"Done: inserted={result['inserted']}  skipped={result['skipped']}")

    await pool.close()
    await redis.aclose()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
