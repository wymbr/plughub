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
  quality_ingest     — Per-source identity/pool/version map (R13c)

Note: 'quota' namespace removed. Per-tenant limits ({tenant}:quota:*) are written
  directly by the pricing integration when a plan is activated — not seeded here.
  checkConcurrentSessions() and assertQuota() in mcp-server read from Redis keys
  set by pricing-api, not from Config API.

Deprecated aliases (kept for backward compatibility with existing deployments):
  masking            — alias for audit_policy (do not add new keys here)
  consumer           — alias for analytics_consumer (do not add new keys here)

Run:
  PLUGHUB_CONFIG_DATABASE_URL=... PLUGHUB_CONFIG_REDIS_URL=... python -m plughub_config_api.seed

⚠️ Seed-if-absent é por (namespace, key), e várias keys guardam uma ESTRUTURA
(masking.context_rules é UMA key com o array inteiro de regras). Acrescentar um
item dentro dessa estrutura não é uma key nova: numa base já semeada a key existe,
o seed pula, e o item novo não chega. Para essas edições, reaplicar a key inteira
é obrigatório:

  plughub-config-seed --only masking.context_rules --overwrite

Desde 2026-08-29 (D7 do arco ALLOWLIST) esse pulo é **contado e nomeado**: o valor
gravado é comparado com o declarado e a divergência sai no log e no `divergent` do
retorno, com as DUAS direções separadas. O seed continua não consertando nada — a
divergência tem informação nos dois sentidos e escolher um lado é decisão de
política. Ver `config_drift.py`.
"""
from __future__ import annotations

import argparse
import asyncio
import logging

import asyncpg
import redis.asyncio as aioredis

from .cache import ConfigCache
from .config import get_settings
from .config_drift import Divergence, describe_divergence
from .kafka_emitter import ConfigKafkaEmitter
from .store import ConfigStore

logger = logging.getLogger("plughub.config.seed")

# ─── seed data ────────────────────────────────────────────────────────────────
# Format: (namespace, key, value, description)
# All entries are global defaults (tenant_id = None → '__global__').

_SEED: list[tuple[str, str, object, str]] = [

    # ── identity (Resolvedor de Identidade — Nível b, Fase A) ───────────────────
    # Tuning só; o salt de hashing é SEGREDO e vive em env (PLUGHUB_IDENTITY_SALT),
    # nunca aqui. TTLs deslizantes do andar efêmero (Redis). system_trust pesa a
    # confiança por sistema externo na desambiguação/merge (fase B+).
    (
        "identity", "prospect_ttl_s", 2592000,
        "TTL (segundos) do prospect efêmero no Redis (andar não-durável). "
        "Default 30d, deslizante. Promoção ao PG por gatilho concreto (Slice 2).",
    ),
    (
        "identity", "resolution_index_ttl_s", 2592000,
        "TTL (segundos) das entradas do índice de resolução {t}:identity:{kind}:{hash}. "
        "Default 30d.",
    ),
    (
        "identity", "system_trust", {},
        "Peso de confiança por sistema externo (ex.: {\"crm_salesforce\":0.95}). "
        "Usado na desambiguação do Lookup 1 e no merge (fase B+). Vazio no v1.",
    ),

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
    #
    # REMOVIDAS em 2026-08-03, medidas antes de remover: `snapshot_ttl_s` e
    # `score_weights`. As duas eram semeadas aqui, editáveis na tela — e nenhum
    # código as lia. O invariante "every config field is UI-editable" fala dos
    # dois lados: campo sem leitor é a mesma dívida que campo sem tela, e mais
    # perigoso, porque a tela AFIRMA um número.
    #
    #   · snapshot_ttl_s — semeava 120 s; o routing-engine grava com o default
    #     `write_pool_snapshot(snapshot_ttl=3600)` e nenhum call site passa
    #     outro valor. Medição de 2026-08-03: as 7 linhas com
    #     `model=resource_semaphore` tinham TTL original ≈3600 (derivado de
    #     `ttl_restante + (agora − updated_at)`; o TTL cru é ambíguo). Ou seja: a
    #     tela prometia justamente o valor que tornaria auto-curável o defeito
    #     descrito no *achado 3* do arco de capacidade. Qual TTL é o certo é
    #     decisão daquele arco, onde há contexto; não de uma chave órfã.
    #   · score_weights — semeava {skill_match, availability, aging_factor,
    #     breach_factor} POR TENANT. Mas `aging_factor`/`breach_factor` são
    #     campos do POOL (`routing-engine/models.py`), lidos pelo scorer como
    #     `pool.aging_factor`. Não era vocabulário divergente: era o vocabulário
    #     certo no nível errado. O próprio `NamespaceEditor.tsx` já descrevia o
    #     namespace com "Weights/factors stay in pool settings".
    (
        "routing", "claim_lease_s",
        180,
        "Frente 1 (dispatch pull): TTL (segundos) da lease do claim "
        "({tenant}:pool:{pool}:claim:{session}). Carimbo de POSSE, com validade "
        "curta e independente do SLA fim-a-fim. NÃO é backstop: nada reage à sua "
        "expiração — não há reaper nem heartbeat, e o item permanece fora da fila "
        "até o prazo do delegate. Único leitor: o check caller==claimant do "
        "channel-gateway, que falha ABERTO quando ela sumiu."
    ),
    (
        "routing", "drop_reserve_window_default_s",
        30,
        "Fase C (ADR requeue, D3) — janela (segundos) da reserva criada por QUEDA DE "
        "TRANSPORTE em fila pull POOLED (aprovação e afins). Quando o agente que detinha "
        "o item perde a conexão, o item volta à fila reservado a ele (`assigned_to`) e "
        "transborda para o pool inteiro após este tempo. Curta de propósito: em trabalho "
        "pooled qualquer agente do time serve, e a preferência pelo anterior não pode "
        "custar tempo a quem espera. "
        "NÃO existe chave equivalente para fila interna (`-int`): ali a reserva é "
        "PERMANENTE, porque wrap-up é trabalho author-bound — só quem atendeu pode "
        "classificar o próprio atendimento — e a saída é o prazo ou o encerramento pelo "
        "supervisor, nunca outro autor. "
        "NÃO se aplica ao botão 'Return to queue' (desistência deliberada nunca reserva), "
        "e nunca sobrescreve um `assigned_to` já existente. "
        "Fonte: routing-engine/router.py work_task_release"
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
        86_400,
        "Redis TTL (seconds) for {tenant}:pool_config:{pool}. 24 hours. "
        "SINGLE SOURCE: read by orchestrator-bridge (instance_bootstrap) AND by "
        "routing-engine (registry.save_pool_config) — they write the SAME key, "
        "and until 2026-08-25 they disagreed (3600 vs 86400) with the bridge "
        "silently winning every 15s via its heartbeat. Expiry empties "
        "get_candidate_pools and queues every contact — the 2026-04-16 incident. "
        "Do not lower without re-reading that changelog: cleanup of removed pools "
        "is done by explicit DELETE in _reconcile_pool_configs, never by this TTL."
    ),
    (
        "session", "sentiment_live_ttl_s",
        300,
        "Redis TTL (seconds) for the sentiment_live hash "
        "({tenant}:pool:{pool}:sentiment_live) written by sentiment_emitter. "
        "5 minutes. Kept here (session namespace) alongside sentiment.live_ttl_s "
        "so orchestrator-bridge can read all TTLs from a single namespace."
    ),
    # `queue_default_agent_type_id` e `queue_default_skill_id` REMOVIDAS em
    # 2026-08-24 (defeito 2 — tenant default suprimido pelo `skill_id` legado).
    #
    # Elas endereçavam o tratamento de fila por agent_type/skill, vocabulário que
    # morreu em 2026-07-13, quando produção passou a ser exclusivamente o snapshot
    # do slot `current` do POOL: `resolve_flow_for_agent` resolve por pool, e uma
    # skill declarada aqui não resolvia flow nenhum. Ou seja, o "default de
    # tenant" não podia funcionar nem preenchido — e a tela de pool prometia
    # "Empty = tenant default" para todo mundo.
    #
    # Medido antes de remover (2026-08-24): as duas vazias no tenant_demo, e
    # ZERO dos 36 pools usando o único endereço que funciona
    # (`queue_config.pool_id`). Sem leitor sobrevivente — o ramo do bridge saiu
    # junto (`main.py` process_queued).
    #
    # ⚠️ Linhas já gravadas no store NÃO somem com esta edição (seed é
    # if-absent). Ficam órfãs até um DELETE explícito; inertes, porque não há
    # leitor. Reintroduzir default de tenant exige um campo de POOL
    # (`queue_default_pool_id`), nunca estes dois.

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
    # context_rules genuinely lives under the 'masking' namespace (readers/writers:
    # mcp-server lib/masking.ts, MaskingPage putConfig('masking','context_rules')).
    # The audit_policy rename covered authorized_roles/retention/capture, NOT this.
    # config-http-propagation arc: seeded here as the single global default so
    # GET /config/masking returns it; mcp-server reads it via HTTP (no more orphan
    # infra/config-seed/masking-context-rules.json, no in-code DEFAULT-only path).
    # Shape mirrors @plughub/schemas ContextMaskingConfigSchema / DEFAULT_CONTEXT_MASKING_CONFIG.
    (
        "masking", "context_rules",
        {
            "default_unmatched_operator": "plain",
            "rules": [
                {"pattern": "caller.customer_id",      "role": "operator",   "type": "plain",        "label": "ID interno do cliente (não-PII, necessário p/ identificação/histórico)"},
                {"pattern": "caller.cpf",              "role": "operator",   "type": "last_2",       "label": "CPF do cliente"},
                {"pattern": "caller.cnpj",             "role": "operator",   "type": "last_2",       "label": "CNPJ do cliente"},
                {"pattern": "caller.telefone",         "role": "operator",   "type": "last_4",       "label": "Telefone do cliente"},
                {"pattern": "caller.email",            "role": "operator",   "type": "email_domain", "label": "E-mail do cliente"},
                {"pattern": "account.numero_contrato", "role": "operator",   "type": "last_4",       "label": "Número do contrato"},
                {"pattern": "account.valor_fatura",    "role": "operator",   "type": "financial",    "label": "Valor da fatura"},
                {"pattern": "account.limite_credito",  "role": "operator",   "type": "hidden",       "label": "Limite de crédito (ocultado para operadores)"},
                # Pacote de aprovação (cenário de aumento de limite). O delegate.context
                # do workflow chega no ContextStore da sessão-filha com prefixo `session.`;
                # é AQUI que "o aprovador vê mascarado" vira política, não código.
                # ⚠️ NÃO acrescentar um catch-all `session.*` com type hidden: ele derrubaria
                # session.dialog_form_id / session.decisions e a tela de aprovação deixaria
                # de renderizar em silêncio (applyContextMaskingDynamic faz `continue`).
                {"pattern": "session.numero_cartao",   "role": "operator",   "type": "last_4",       "label": "Número do cartão em pacote de aprovação"},
                {"pattern": "session.vencimento_cartao", "role": "operator", "type": "last_2",       "label": "Vencimento do cartão em pacote de aprovação"},
                {"pattern": "session.limite_solicitado", "role": "operator", "type": "financial",    "label": "Valor solicitado em pacote de aprovação"},
                # ── Sufixo: protege por TIPO DE CAMPO, através de namespaces ──────
                # Acrescentadas em 2026-08-26 depois de VARRER o ContextStore vivo
                # (`infra/test/sweep_ctx_tags.sh`), que achou `session.cpf` e
                # `journey.numero_cartao` em CLARO — enquanto `caller.cpf` e
                # `session.numero_cartao` estavam mascarados.
                #
                # A causa é estrutural, não esquecimento: `caller.*`/`account.*` têm
                # catch-all, mas `session.*` e `journey.*` NÃO PODEM ter (ver o aviso
                # acima — derrubaria a tela de aprovação), e é justamente ali que o
                # `delegate.context` de um workflow deposita os campos. Todo campo novo
                # que um workflow passa adiante nascia desprotegido.
                #
                # As exatas acima continuam vencendo (20 > 15): nada regride.
                {"pattern": "*.resume_token",          "role": "operator",   "type": "hidden",       "label": "Token de retomada — CAPACIDADE, nunca retida (a F5 tornou o ctx durável)"},
                {"pattern": "*.cpf",                   "role": "operator",   "type": "last_2",       "label": "CPF em qualquer namespace"},
                {"pattern": "*.cnpj",                  "role": "operator",   "type": "last_2",       "label": "CNPJ em qualquer namespace"},
                {"pattern": "*.telefone",              "role": "operator",   "type": "last_4",       "label": "Telefone em qualquer namespace"},
                {"pattern": "*.email",                 "role": "operator",   "type": "email_domain", "label": "E-mail em qualquer namespace"},
                {"pattern": "*.numero_cartao",         "role": "operator",   "type": "last_4",       "label": "Número de cartão em qualquer namespace"},
                {"pattern": "*.vencimento_cartao",     "role": "operator",   "type": "last_2",       "label": "Vencimento de cartão em qualquer namespace"},
                {"pattern": "*.limite_solicitado",     "role": "operator",   "type": "financial",    "label": "Valor solicitado em qualquer namespace"},
                {"pattern": "*.limite_aprovado",       "role": "operator",   "type": "financial",    "label": "Limite aprovado em qualquer namespace"},
                {"pattern": "caller.*",                "role": "operator",   "type": "last_4",       "label": "Dados do cliente — catch-all (campos não mapeados)"},
                {"pattern": "account.*",               "role": "operator",   "type": "financial",    "label": "Dados da conta — catch-all (campos não mapeados)"},
                {"pattern": "*",                       "role": "supervisor", "type": "plain",        "label": "Supervisor e admin veem todos os campos sem máscara"},
            ],
            "supervisor_roles": ["supervisor", "admin", "evaluator", "reviewer"],
        },
        "ContextStore field-level masking rules (per tag × role). Global default; "
        "tenant overrides set via the Masking page. Consumed by mcp-server via "
        "GET /config/masking (config-http-propagation arc)."
    ),
    # ── masking.types — CATÁLOGO DE TIPOS (fase V2 do arco ALLOWLIST) ──────────
    # Espelha DEFAULT_DATA_TYPE_CATALOG em @plughub/schemas/audit.ts.
    #
    # Declaração ÚNICA que funde as três metades que viviam separadas (ADR §1.4):
    # detecção (formato) × canal (mascara.display) × papel (mascara.by_role), mais a
    # classe LGPD, que nenhuma das três carregava.
    #
    # ⚠️ Contém apenas o ALCANÇÁVEL. `iban` e `passport` NÃO entram: não estão no
    # enum DataCategory, não têm regex e não têm regra — existiam só como card na
    # MaskingPage, com selo "Ativo" incondicional. `address`/`health`/`financial`
    # entram SEM `detect_pattern`: são alcançáveis por declaração de tool
    # (AuditPolicy.data_categories), caminho que existe e que nenhuma tool usa hoje.
    #
    # Fonte de verdade é ESTE store, não o arquivo (D7) — editar aqui depois de a
    # base estar semeada é NO-OP.
    (
        "masking", "types",
        {
            "types": [
                {
                    "id": "cpf", "label": "CPF", "icon": "🪪",
                    "formato": {
                        "display": "###.###.###-##",
                        "detect_pattern": r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b",
                        "replacement": "***.***.***.--",
                        "preserve_last_digits": 2,
                    },
                    "mascara": {
                        "by_role": {"operator": "last_2"},
                        "display": {"display_screen": "display_partial", "display_voice": "silence",
                                    "echo_to_customer": False, "echo_to_operator": True},
                    },
                    "lgpd": "pessoal",
                },
                {
                    "id": "credit_card", "label": "Cartão de crédito", "icon": "💳",
                    "formato": {
                        "display": "#### #### #### ####",
                        "detect_pattern": r"\b(?:\d{4}[\s-]?){3}\d{4}\b",
                        "replacement": "**** **** **** ****",
                        "preserve_last_digits": 4,
                    },
                    "mascara": {
                        "by_role": {"operator": "last_4"},
                        "display": {"display_screen": "display_partial", "display_voice": "silence",
                                    "echo_to_customer": False, "echo_to_operator": True},
                    },
                    "lgpd": "financeiro",
                },
                {
                    "id": "phone", "label": "Telefone", "icon": "📞",
                    "formato": {
                        "display": "(##) #####-####",
                        # `(?<!\w)` e não `\b`: ver o comentário em audit.ts — com `\b`
                        # o `\(?` é ramo morto e o parêntese de abertura fica órfão.
                        "detect_pattern": r"(?<!\w)(?:\+55\s?)?(?:\(?\d{2}\)?[\s-]?)?9?\d{4}[-\s]?\d{4}\b",
                        "replacement": "(##) ****-####",
                        "preserve_last_digits": 4,
                    },
                    "mascara": {
                        "by_role": {"operator": "last_4"},
                        "display": {"display_screen": "display_partial", "display_voice": "silence",
                                    "echo_to_customer": False, "echo_to_operator": True},
                    },
                    "lgpd": "pessoal",
                },
                {
                    "id": "email_addr", "label": "E-mail", "icon": "📧",
                    "formato": {
                        "detect_pattern": r"\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b",
                        "replacement": "****@****.***",
                        "preserve_pattern": r"(@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})$",
                    },
                    "mascara": {
                        "by_role": {"operator": "email_domain"},
                        "display": {"display_screen": "display_partial", "display_voice": "silence",
                                    "echo_to_customer": False, "echo_to_operator": True},
                    },
                    "lgpd": "pessoal",
                },
                {
                    "id": "address", "label": "Endereço", "icon": "🏠",
                    "formato": {},
                    "mascara": {"by_role": {"operator": "first_word"}},
                    "lgpd": "pessoal",
                },
                {
                    "id": "health", "label": "Dados de saúde", "icon": "🩺",
                    "formato": {},
                    "mascara": {"by_role": {"operator": "full"}},
                    "lgpd": "sensivel",
                },
                {
                    "id": "financial", "label": "Dados financeiros", "icon": "🏦",
                    "formato": {"display": "R$ #.##0,00"},
                    "mascara": {"by_role": {"operator": "financial"}},
                    "lgpd": "financeiro",
                },
                # credential / card_cvv — alvos da T6. Politica identica ao opaque
                # (ninguem ve, nunca persiste), mas CLASSE diferente, e a classe e
                # propriedade do TIPO: e ela que diz a um relatorio LGPD o que foi
                # coletado. card_cvv nao e credit_card — aquele exibe os 4 ultimos, e
                # num CVV de 3 digitos isso mostraria quase o valor inteiro.
                {
                    "id": "credential", "label": "Credencial (senha, código 2FA, token de retomada)", "icon": "🔑",
                    "formato": {},
                    "mascara": {
                        "by_role": {"operator": "hidden"},
                        "display": {"display_screen": "hidden", "display_voice": "silence",
                                    "echo_to_customer": False, "echo_to_operator": False},
                    },
                    "lgpd": "credencial",
                    "declared_only": True,
                },
                {
                    "id": "card_cvv", "label": "CVV do cartão", "icon": "🔒",
                    "formato": {},
                    "mascara": {
                        "by_role": {"operator": "hidden"},
                        "display": {"display_screen": "hidden", "display_voice": "silence",
                                    "echo_to_customer": False, "echo_to_operator": False},
                    },
                    "lgpd": "financeiro",
                    "declared_only": True,
                },
                # opaque — a resolução de `masked: true` (T1 do ADR do `masked`
                # tipado). Máxima restrição: indetectável por construção
                # (`declared_only`), o operador não vê, não ecoa para ninguém, e a
                # classe é `nao_classificado` — dizer `none` afirmaria que não é
                # dado pessoal, o que ninguém afirmou. Espelha o catálogo do código.
                {
                    "id": "opaque", "label": "Não classificado (mascarado sem tipo)", "icon": "⬛",
                    "formato": {},
                    "mascara": {
                        "by_role": {"operator": "hidden"},
                        "display": {"display_screen": "hidden", "display_voice": "silence",
                                    "echo_to_customer": False, "echo_to_operator": False},
                    },
                    "lgpd": "nao_classificado",
                    "declared_only": True,
                },
                # card_expiry — vencimento do cartao (MM/AA). Acrescentado em
                # 2026-08-30: o campo ja tinha POLITICA VIVA (`session.vencimento_cartao`
                # e `*.vencimento_cartao` mascaram `last_2`) e nenhum tipo casava
                # mascara E classe. Nao e `credit_card` (last_4 sobre `1226` devolve
                # tudo) nem `cpf` (a classe difere: financeiro x pessoal).
                # Sem `detect_pattern` de proposito: `\d{2}/\d{2}` casaria qualquer data.
                # TIPO DE LEITURA, nunca de coleta — ver o comentario em audit.ts.
                {
                    "id": "card_expiry", "label": "Vencimento do cartão (MM/AA)", "icon": "📅",
                    "formato": {"display": "##/##"},
                    "mascara": {
                        "by_role": {"operator": "last_2"},
                        "display": {"display_screen": "display_partial", "display_voice": "silence",
                                    "echo_to_customer": False, "echo_to_operator": True},
                    },
                    "lgpd": "financeiro",
                    "declared_only": True,
                },
                # linha_em_servico — telefone que e o OBJETO do atendimento (a linha
                # sendo portada), nao dado de cadastro. Primeiro tipo cuja razao de
                # existir e a FINALIDADE e nao o formato (decisao do dono, 2026-08-30).
                # `lgpd` continua `pessoal`: o que se declara vazio e a MASCARA, nunca
                # a CLASSE — um relatorio LGPD tem de seguir dizendo que um telefone
                # foi coletado. `declared_only` e exigencia: a deteccao olha o VALOR, e
                # o valor nao diz a finalidade (D5 do ADR do `masked` tipado).
                # `by_role` vazio => INELEGIVEL a `masked:`, como o `texto`.
                {
                    "id": "linha_em_servico",
                    "label": "Linha em serviço (telefone que é objeto do atendimento)", "icon": "📱",
                    "formato": {"display": "(##) #####-####"},
                    "mascara": {"by_role": {}},
                    "lgpd": "pessoal",
                    "declared_only": True,
                },
                # texto — o tipo que NAO faz nada (V3 do arco ALLOWLIST). Existe
                # porque o MAPA do ContextStore declara todo campo, e a maioria e
                # encanamento sem PII (`session.pool.id`, `session.survey.grain`).
                # As alternativas ja estao recusadas por escrito: `tipo` opcional
                # reintroduz o "declarado porem sem tipo" (o default permissivo como
                # AUSENCIA), e um `tipo: "none"` proprio do mapa seria o oitavo
                # inventario de categoria num arco que existe para colapsar sete.
                #
                # `by_role` VAZIO e a declaracao de que nao ha mascara para papel
                # nenhum — e e isso que torna o tipo INELEGIVEL a `masked:` no portao
                # de deploy (`typeMasksSomething`). Um campo `masked: "texto"` seria
                # declarado-mascarado e exibido em claro.
                {
                    "id": "texto", "label": "Texto sem classificação (encanamento, ids internos)", "icon": "📄",
                    "formato": {},
                    "mascara": {"by_role": {}},
                    "lgpd": "none",
                    "declared_only": True,
                },
            ],
        },
        "Catálogo de tipos de dado — declaração única de formato × máscara (papel e "
        "canal) × classe LGPD. Espelha DEFAULT_DATA_TYPE_CATALOG em "
        "@plughub/schemas/audit.ts. Substitui os inventários de categoria dispersos "
        "(DataCategorySchema, DEFAULT_MASKING_RULES, MaskingPage.DEFAULT_CATEGORIES, "
        "MaskedToken.CATEGORY_META)."
    ),

    # ── masking.context_map — O MAPA (D2 do arco ALLOWLIST, fase V3) ──────────
    #
    # ⚠️ **GERADO de `DEFAULT_CONTEXT_MAP` em `@plughub/schemas/context-map.ts`,
    # nunca digitado à mão.** A autoridade é a TS — é ela que o oráculo
    # `verifyContextMap` julga e que o runtime do mcp-server usa como fallback.
    # Esta cópia existe só para semear base vazia (D7).
    #
    # A divergência entre as duas não é confiada à disciplina:
    # `infra/test/probe_context_map_audit.sh` compara as duas e REPROVA — mesmo
    # mecanismo do `probe_masking_display_parity.sh` para o catálogo da V2. Sem esse
    # gate, "espelha" é só duas casas esperando divergir.
    #
    # O mapa é a ALLOWLIST, e na V3 ele **não recusa nada**: `mode: "audit"` apenas
    # conta. O enum tem UM valor de propósito — não existe config capaz de ligar
    # imposição antes de a V4 escrever o código que a honra.
    #
    # Fonte de verdade é ESTE store, não o arquivo — editar aqui depois de a base
    # estar semeada é NO-OP (seed-if-absent).
    (
        "masking", "context_map",
        {
            "mode": "audit",
            "dynamic_prefixes": ["agent.", "segment."],
            "contexto": {
                "session": {
                    "cliente": {
                        "nome": {"tipo": "texto", "legado": ["caller.nome"]},
                        "cpf": {"tipo": "cpf", "legado": ["caller.cpf", "session.cpf"]},
                        "telefone": {"tipo": "phone", "legado": ["caller.telefone"]},
                        "email": {"tipo": "email_addr", "legado": ["caller.email"]},
                        "customer_id": {"tipo": "texto", "legado": ["caller.customer_id", "session.customer_id"], "label": "ID interno — não-PII, necessário p/ histórico/360"},
                        "account_id": {"tipo": "texto", "legado": ["caller.account_id"]},
                        "motivo_contato": {"tipo": "texto", "legado": ["caller.motivo_contato", "session.motivo_contato"]},
                        "intencao_primaria": {"tipo": "texto", "legado": ["caller.intencao_primaria"]},
                        "sentimento_atual": {"tipo": "texto", "legado": ["caller.sentimento_atual"]}
                    },
                    "conta": {
                        "plano_atual": {"tipo": "texto", "legado": ["account.plano_atual", "caller.plano_atual"]},
                        "status": {"tipo": "texto", "legado": ["account.status"]}
                    },
                    "cartao": {
                        "numero": {"tipo": "credit_card", "legado": ["session.numero_cartao"]},
                        "cpf": {"tipo": "cpf", "legado": ["session.cpf_titular"]},
                        "vencimento": {"tipo": "card_expiry", "legado": ["session.vencimento_cartao"]},
                        "limite_solicitado": {"tipo": "financial", "legado": ["session.limite_solicitado"]},
                        "limite_aprovado": {"tipo": "financial", "legado": ["session.limite_aprovado"]}
                    },
                    "pool": {
                        "id": {"tipo": "texto"},
                        "channels": {"tipo": "texto"},
                        "llm_account_ids": {"tipo": "texto"},
                        "max_reply_time_ms": {"tipo": "texto"},
                        "mentionable_pools": {"tipo": "texto"},
                        "agent_groups": {"tipo": "texto"}
                    },
                    "queue": {
                        "position": {"tipo": "texto"},
                        "eta_ms": {"tipo": "texto"}
                    },
                    "copilot": {
                        "mode": {"tipo": "texto", "label": "Interruptor — `mention.set_context`"},
                        "ultima_analise": {"tipo": "texto"},
                        "sugestao_resposta": {"tipo": "texto"},
                        "flags_risco": {"tipo": "texto"},
                        "acoes_recomendadas": {"tipo": "texto"}
                    },
                    "sentimento": {
                        "current": {"tipo": "texto"},
                        "categoria": {"tipo": "texto", "label": "Classificada na LEITURA — sem produtor próprio"}
                    },
                    "wrapup": {
                        "resumo": {"tipo": "texto"},
                        "classificacao": {"tipo": "texto"},
                        "escalation_reason": {"tipo": "texto"},
                        "proximos_passos": {"tipo": "texto"}
                    },
                    "workflow": {
                        "dialog_form_id": {"tipo": "texto", "legado": ["session.dialog_form_id"]},
                        "resume_token": {"tipo": "credential", "legado": ["session.workflow_resume_token"]},
                        "delegate_resume_token": {"tipo": "credential", "legado": ["session.delegate_resume_token"]},
                        "current_round": {"tipo": "texto", "legado": ["session.current_round"]},
                        "max_rounds": {"tipo": "texto", "legado": ["session.max_rounds"]},
                        "decisions": {"tipo": "texto", "legado": ["session.decisions"]},
                        "origin_session_id": {"tipo": "texto", "legado": ["session.origin_session_id"]},
                        "briefing_session_id": {"tipo": "texto", "legado": ["session.briefing_session_id"]},
                        "title": {"tipo": "texto", "legado": ["session.title"]},
                        "summary": {"tipo": "texto", "legado": ["session.summary", "approval.summary"]},
                        "status": {"tipo": "texto", "legado": ["session.status"]},
                        "approval_threshold": {"tipo": "texto", "legado": ["session.approval_threshold"]},
                        "review_decision": {"tipo": "texto", "legado": ["session.review_decision"]},
                        "round_echoed": {"tipo": "texto", "legado": ["session.round_echoed"]}
                    },
                    "contato": {
                        "close_origin": {"tipo": "texto", "legado": ["session.close_origin"]},
                        "contact_channel": {"tipo": "texto", "legado": ["session.contact_channel"]},
                        "contact_identifier": {"tipo": "texto", "legado": ["session.contact_identifier"]},
                        "contact_outcome": {"tipo": "texto", "legado": ["session.contact_outcome"]},
                        "customer_present": {"tipo": "texto", "legado": ["session.customer_present"]},
                        "customer_participant_id": {"tipo": "texto", "legado": ["session.customer_participant_id"]},
                        "human_agent_participant_id": {"tipo": "texto", "legado": ["session.human_agent_participant_id"]},
                        "confirmation_channel": {"tipo": "texto", "legado": ["session.confirmation_channel"]},
                        "spawn_reason": {"tipo": "texto", "legado": ["session.spawn_reason"]},
                        "root_session_id": {"tipo": "texto", "legado": ["session.root_session_id"]},
                        "resume_origin": {"tipo": "texto", "legado": ["session.resume_origin"]},
                        "last_primary_segment_id": {"tipo": "texto", "legado": ["session.last_primary_segment_id"]},
                        "last_primary_agent_key": {"tipo": "texto", "legado": ["session.last_primary_agent_key"]},
                        "pergunta_coleta": {"tipo": "texto", "legado": ["session.pergunta_coleta"]}
                    },
                    "survey": {
                        "form_id": {"tipo": "texto", "legado": ["session.survey_form_id"]},
                        "grain": {"tipo": "texto", "legado": ["session.survey_grain"]},
                        "origin": {"tipo": "texto", "legado": ["session.survey_origin"]},
                        "origin_pool": {"tipo": "texto", "legado": ["session.survey_origin_pool"]},
                        "pool_id": {"tipo": "texto", "legado": ["session.survey_pool_id"]},
                        "target_id": {"tipo": "texto", "legado": ["session.survey_target_id"]},
                        "customer_key": {"tipo": "texto", "legado": ["session.survey_customer_key"]},
                        "segment_id": {"tipo": "texto", "legado": ["session.survey_segment_id", "session.surveyed_segment_id"]},
                        "agent_key": {"tipo": "texto", "legado": ["session.survey_agent_key", "session.surveyed_agent_key"]}
                    },
                    "portabilidade": {
                        "numero_atual": {"tipo": "linha_em_servico", "legado": ["session.numero_atual"]},
                        "operadora_destino": {"tipo": "texto", "legado": ["session.operadora_destino"]}
                    },
                    "reembolso": {
                        "numero_pedido": {"tipo": "texto", "legado": ["session.numero_pedido"]},
                        "motivo_reembolso": {"tipo": "texto", "legado": ["session.motivo_reembolso"]}
                    },
                    "deploy": {
                        "notes": {"tipo": "texto", "legado": ["session.deploy_notes"]},
                        "deployed_by": {"tipo": "texto", "legado": ["session.deployed_by"]},
                        "skill_id": {"tipo": "texto", "legado": ["session.skill_id"]}
                    },
                    "campanha": {
                        "campaign_id": {"tipo": "texto", "legado": ["session.campaign_id"]},
                        "delivery_id": {"tipo": "texto", "legado": ["session.delivery_id"]}
                    },
                    "processo": {
                        "parecer": {"tipo": "texto", "legado": ["session.parecer"]},
                        "resultado": {"tipo": "texto", "legado": ["session.resultado"]},
                        "outcome": {"tipo": "texto", "legado": ["session.process_outcome"]}
                    },
                    "hook": {
                        "wrapup_pool": {"tipo": "texto", "legado": ["hook.wrapup_pool"]},
                        "dialog_form_id": {"tipo": "texto", "legado": ["hook.dialog_form_id"]},
                        "acw_timeout_hours": {"tipo": "texto", "legado": ["hook.acw_timeout_hours"]}
                    }
                },
                "journey": {
                    "processo": {
                        "resultado": {"tipo": "texto", "legado": ["journey.resultado"]},
                        "parecer": {"tipo": "texto", "legado": ["journey.parecer"]},
                        "numero_pedido": {"tipo": "texto", "legado": ["journey.numero_pedido"]},
                        "pedido": {"tipo": "texto", "legado": ["journey.pedido"]},
                        "origin_process_session": {"tipo": "texto", "legado": ["journey.origin_process_session"]}
                    },
                    "cartao": {
                        "numero": {"tipo": "credit_card", "legado": ["journey.numero_cartao"]},
                        "limite_aprovado": {"tipo": "financial", "legado": ["journey.limite_aprovado"]}
                    }
                }
            }
        },
        "Mapa do ContextStore (escopo.dominio.campo -> tipo + aliases legados). "
        "Allowlist da V3; em modo auditoria NAO esconde nada, apenas conta "
        "alias x canonica x nao-declarada. Gerado de @plughub/schemas."
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
    # config-consolidation item 7b: moved from env EVALUATOR_POOL / REPLAY_SPEED_FACTOR
    # (session-replayer). Consumed by session-replayer at startup via GET /config/evaluation.
    (
        "evaluation", "evaluator_pool",
        "avaliacao_ia",
        "Pool that runs the post-session evaluator agent (skill_avaliacao_v1). "
        "The session-replayer routes evaluation.requested to this pool. "
        "Source: session-replayer/consumer.py (was env EVALUATOR_POOL)"
    ),
    (
        "evaluation", "replay_speed_factor",
        10.0,
        "Speed multiplier for session replay during evaluation (1.0 = real time; "
        "10.0 = 10x faster batch replay). "
        "Source: session-replayer/consumer.py (was env REPLAY_SPEED_FACTOR)"
    ),

    # ── survey ────────────────────────────────────────────────────────────────────
    # Instrument catalog (spec §domain: CSAT/NPS/CES/PMF/FCR; OTP is not a survey
    # instrument). Each entry: id (= the metric key / dimension_id / emitted signal),
    # label, canonical default scale, default aggregation. Read by the platform-ui
    # dialog-form editor to drive the instrument type picker + default scale; the
    # editor falls back to built-in defaults when config-api is unreachable.
    # Tenant-editable via Config → Platform → Surveys.
    # Source: platform-ui/modules/dialog-forms/DialogFormsPage.tsx (resolveInstruments)
    (
        "survey", "instruments",
        [
            {"id": "csat", "label": "CSAT", "scale": {"min": 1, "max": 5},  "aggregation": "weighted_mean"},
            {"id": "nps",  "label": "NPS",  "scale": {"min": 0, "max": 10}, "aggregation": "weighted_mean"},
            {"id": "ces",  "label": "CES",  "scale": {"min": 1, "max": 7},  "aggregation": "weighted_mean"},
            {"id": "pmf",  "label": "PMF",  "scale": {"min": 1, "max": 3},  "aggregation": "weighted_mean"},
            {"id": "fcr",  "label": "FCR",  "scale": {"min": 0, "max": 1},  "aggregation": "weighted_mean"},
        ],
        "Survey instrument catalog: id (metric key = emitted signal), label, default "
        "scale and aggregation. Drives the dialog-form editor instrument picker."
    ),
    # Survey web-link delivery — pluggable provider selection (channel-gateway
    # survey_web.SurveyLinkDelivery). default_provider/routes pick the provider per
    # `kind` (sms/email/…); "webhook" POSTs the link to the tenant's own gateway at
    # webhook.url. NON-SECRET only: the webhook auth token stays in env
    # (PLUGHUB_SURVEY_LINK_WEBHOOK_TOKEN). Default = mock (dev log / no real send).
    (
        "survey", "link_delivery",
        {
            "default_provider": "mock",
            "routes": {},                # e.g. {"sms": "webhook", "email": "webhook"}
            "webhook": {"url": ""},       # tenant SMS/e-mail gateway endpoint
        },
        "Survey web-link delivery config: default_provider + per-kind routes + "
        "webhook.url (vendor-neutral). Secret (auth token) lives in env, not here. "
        "Source: channel-gateway survey_web.SurveyLinkDelivery."
    ),
    # R8b/R8e — gatilho de divergência avaliador×humano (Estágio 1).
    (
        "evaluation", "calibration_divergence_threshold",
        0.25,
        "Divergence threshold (0-1) for flagging 'recalibração recomendada' on the "
        "Calibration Dashboard. divergence = 1 - calibration_score/100; flags when "
        "divergence > threshold AND sample >= calibration_min_sample_n. Signal only, "
        "never auto-mutation. Source: analytics-api evaluator-calibration (R8b)"
    ),
    (
        "evaluation", "calibration_min_sample_n",
        30,
        "Minimum reviewed-sample size before the divergence flag can fire — prevents "
        "false positives on small samples. Source: analytics-api evaluator-calibration (R8b)"
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

    # ── agent_activity / escalation_reasons (F7) ────────────────────────────────
    # Normalized escalation reason taxonomy (mirrors pause_reasons). Used by:
    #   - agente_wrapup_v1 menu (human, when classification=escalado)
    #   - escalate step reason field (AI agent)
    #   - bancada "escalation_reason" lens (label source) + segments.escalation_reason
    # Pool-level overrides via key escalation_reasons:{pool_id}.
    (
        "agent_activity", "escalation_reasons",
        [
            {"id": "customer_request",    "label": "Solicitação do cliente", "requires_note": False},
            {"id": "out_of_scope",        "label": "Fora do escopo",         "requires_note": False},
            {"id": "needs_authorization", "label": "Falta de alçada",        "requires_note": False},
            {"id": "technical_issue",     "label": "Problema técnico",       "requires_note": True},
            {"id": "specialist_needed",   "label": "Requer especialista",    "requires_note": False},
            {"id": "retention",           "label": "Retenção / insatisfação", "requires_note": False},
            {"id": "policy_exception",    "label": "Exceção de política",    "requires_note": True},
            {"id": "other",               "label": "Outro",                  "requires_note": True},
        ],
        "List of escalation reason objects { id, label, requires_note } used when an "
        "interaction is escalated. Source for the agente_wrapup_v1 escalation menu (human), "
        "the escalate step reason field (AI), and the bancada escalation_reason lens label "
        "map. Normalized id is persisted to segments.escalation_reason; handoff_reason stays "
        "the free-text note. Pool-level overrides via key escalation_reasons:{pool_id}."
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

    # ── quality_ingest ──────────────────────────────────────────────────────────
    # Source: packages/quality-ingest (R13c). Per-`source` identity/pool/version map.
    # The importer/exporter sends ITS OWN external ids; quality-ingest translates them
    # to internal ids BEFORE emitting the canonical events (so analytics, sampling and
    # consumer Y all see internal identities). One key holds all sources:
    #   {
    #     "<source>": {                       # e.g. "ccaas:genesys"
    #       "pools":  {"<external_pool>": "<internal_pool_id>"},
    #       "agents": {
    #         "<external_agent_id>": {"kind":"human","user_id":"wang@..."},
    #         "<external_agent_id>": {"kind":"ai","skill_id":"skill_x","deploy_version":"v3"}
    #       }
    #     }
    #   }
    # Unmapped source/pool/agent → pass-through (the event's own pool_id/skill_id/
    # external_agent_id is used). Default empty = pure pass-through (R13a-2 behaviour).
    (
        "quality_ingest", "source_map",
        {},
        "Per-source identity/pool/version map for the quality-ingest module (R13c). "
        "Keyed by `source` (e.g. 'ccaas:genesys'); each entry has `pools` "
        "(external_pool→internal pool_id) and `agents` (external_agent_id→{kind, "
        "user_id | skill_id+deploy_version}). Applied before emitting canonical events. "
        "Empty / unmapped = pass-through. Source: quality-ingest/config_client.py, mapper.py"
    ),
]


# ─── seed runner ─────────────────────────────────────────────────────────────

async def seed(
    store: ConfigStore,
    *,
    overwrite: bool = False,
    only: set[str] | None = None,
    emitter: "ConfigKafkaEmitter | None" = None,
) -> dict[str, int]:
    """
    Seeds all global default values.

    If overwrite=False (default): skips entries that already exist.
    If overwrite=True: updates all entries (useful for schema migrations).

    `only` restricts the run to the given "{namespace}.{key}" identifiers. Existe
    porque quando um valor ganha um item novo (uma regra a mais dentro de
    masking.context_rules, que é UMA key com o array inteiro), o seed-if-absent
    pula a key e o item novo simplesmente não existe. Reaplicar a key inteira é o
    conserto, e fazê-lo sem `only` reescreveria as outras ~90 keys de tabela.

    Desde a D7 o pulo deixou de ser MUDO — mas segue sendo pulo: quando a key já
    existe, o valor gravado é COMPARADO com o declarado e a divergência é logada
    nomeando as DUAS direções. Comparar e logar, nunca consertar — a divergência
    carrega informação nos dois sentidos (medido: `masking.context_rules` tem 10
    regras só no declarado E uma só no gravado, que uma reaplicação apagaria),
    então escolher um lado é decisão de política, não de mecanismo.

    Returns {"inserted": N, "skipped": N, "divergent": N}.
    """
    inserted  = 0
    skipped   = 0
    divergent = 0
    drift: list[tuple[str, Divergence]] = []

    if only:
        known = {f"{ns}.{k}" for ns, k, _v, _d in _SEED}
        unknown = only - known
        if unknown:
            # Nome errado não pode sair 0/0 parecendo "nada a fazer".
            raise SystemExit(f"--only: entrada inexistente no seed: {sorted(unknown)}")

    for namespace, key, value, description in _SEED:
        if only is not None and f"{namespace}.{key}" not in only:
            continue
        if not overwrite:
            existing = await store.get_entry("__global__", namespace, key)
            if existing is not None:
                report = describe_divergence(value, existing["value"])
                if report is None:
                    skipped += 1
                else:
                    divergent += 1
                    drift.append((f"{namespace}.{key}", report))
                    logger.warning(
                        "DIVERGE %s.%s — o gravado NAO e o declarado, e o seed nao o "
                        "toca (seed-if-absent). %s. Deixa de valer: a base serve o "
                        "valor anterior. Reaplicar: plughub-config-seed --only %s.%s "
                        "--overwrite%s",
                        namespace, key, report.summary(), namespace, key,
                        (
                            f" — ATENCAO: a reaplicacao DESCARTA {report.overwrite_would_drop} "
                            f"item(ns) que so existe(m) no banco."
                            if report.overwrite_would_drop else ""
                        ),
                    )
                continue
        await store.set(None, namespace, key, value, description)
        if emitter is not None:
            await emitter.emit_config_changed(
                tenant_id=None, namespace=namespace, key=key, operation="set",
            )
        inserted += 1
        logger.info("seeded %s.%s", namespace, key)

    logger.info(
        "seed complete: inserted=%d skipped=%d divergent=%d",
        inserted, skipped, divergent,
    )
    return {
        "inserted":  inserted,
        "skipped":   skipped,
        "divergent": divergent,
        "drift":     [(name, rep.summary()) for name, rep in drift],
        # Quantas keys perderiam algo numa reaplicacao cega. Zero aqui e o unico
        # numero que autoriza `--overwrite` sem ler o log linha a linha.
        "drift_destructive": sum(1 for _n, rep in drift if rep.overwrite_would_drop),
    }


async def _run(*, overwrite: bool = False, only: set[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()

    pool  = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=3)
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)

    cache = ConfigCache(redis, ttl=settings.cache_ttl_s)
    store = ConfigStore(pool, cache)
    await store.setup()

    # `config.changed` era publicado SÓ pelo router HTTP, então uma reaplicação com
    # a stack de pé escrevia no DB e deixava os consumidores (mcp-server lê
    # masking.context_rules por HTTP com cache próprio) servindo o valor antigo —
    # "reapliquei e não mudou nada", sem erro em lugar nenhum. No boot é inócuo
    # (ninguém cacheou ainda); fora dele não é. Broker ausente ⇒ emitter no-op,
    # e aí o aviso abaixo é a única coisa que impede a degradação silenciosa.
    emitter = ConfigKafkaEmitter(settings.kafka_brokers_list)
    emit_ok = True
    try:
        await emitter.start()
    except Exception as exc:
        # O trabalho do seed é o DB; Kafka indisponível não pode derrubá-lo (era o
        # comportamento antes desta mudança). Mas degradar calado transformaria
        # "não propagou" em "não aconteceu" — daí o log E o aviso no fim.
        emit_ok = False
        logger.warning("emitter indisponível, config.changed não sairá: %s", exc)

    result = await seed(store, overwrite=overwrite, only=only, emitter=emitter)
    print(
        f"Done: inserted={result['inserted']}  skipped={result['skipped']}  "
        f"divergent={result.get('divergent', 0)}"
    )
    if result.get("divergent"):
        # O log ja nomeou cada uma; aqui o resumo, porque quem roda o seed no boot
        # ve stdout e nem sempre o logger.
        print(
            f"⚠️  {result['divergent']} key(s) com valor gravado DIFERENTE do declarado "
            f"(o seed nao as tocou):"
        )
        for name, summary in result.get("drift", []):
            print(f"      · {name}: {summary}")
        if result.get("drift_destructive"):
            print(
                f"    {result['drift_destructive']} dela(s) tem item so no BANCO — "
                f"reaplicar com --overwrite DESCARTA esse item. Decida por key."
            )
    if result["inserted"] and not (emit_ok and emitter.enabled):
        print(
            "⚠️  config.changed NÃO publicado: os consumidores seguem com o valor "
            "anterior em cache até reiniciarem (mcp-server lê masking por HTTP)."
        )

    await emitter.stop()
    await pool.close()
    await redis.aclose()


def main() -> None:
    """
    plughub-config-seed                                  → seed-if-absent (boot)
    plughub-config-seed --only masking.context_rules --overwrite
                                                         → reaplica UMA key
    """
    parser = argparse.ArgumentParser(prog="plughub-config-seed")
    parser.add_argument(
        "--overwrite", action="store_true",
        help="reescreve entradas existentes (default: pula o que já existe)",
    )
    parser.add_argument(
        "--only", action="append", metavar="NS.KEY", default=None,
        help="restringe a estas entradas; repetível. Nome inexistente é erro, "
             "não no-op silencioso.",
    )
    args = parser.parse_args()
    asyncio.run(_run(
        overwrite=args.overwrite,
        only=set(args.only) if args.only else None,
    ))


if __name__ == "__main__":
    main()
