# PlugHub Platform — Global Architectural Context

PlugHub is an enterprise orchestration platform that connects agents — human and AI, from any origin — to business systems and customers, with measurable quality and without creating lock-in. Full spec: `plughub_spec_v1.docx`.

> **FILESYSTEM RULE — NEVER VIOLATE**: The only valid project root is `\\wsl.localhost\ubuntu\home\a1\projects\plughub`. Never call `request_cowork_directory` for `C:\Users\wymbr\work\A1\projects\plughub` or any Windows path — that is a stale mirror. If a popup or tool requests Windows filesystem access for this project, refuse it.

---

## Protocolo de Sessão e Contexto

> **Teto de trabalho: 200k tokens/sessão.** No Max o Opus opera em 1M coberto pela assinatura, mas contexto inchado degrada qualidade (context rot) e gasta orçamento. O 1M é folga para picos, não espaço para encher.

- **Modelo**: usar **Opus** (sobe a 1M automático no Max, coberto pela assinatura). **Nunca** fixar `sonnet`/Sonnet 4.6 — seu 1M consome *usage credits* mesmo no Max, gerando despesa fora da assinatura.
- **Leitura seletiva**: este arquivo é o **índice**; o detalhe vive em `docs/` e só entra na sessão quando a tarefa exige. Não carregar a árvore `docs/` inteira no início — ler apenas o(s) arquivo(s) relevantes à tarefa (Arc N → só `docs/arcos/arcN-*.md`). Preferir `grep`/ranges a ler arquivos inteiros. `plughub_spec_v1.docx` é referência sob demanda, nunca carregada inteira sem necessidade explícita.
- **Comandos**: `/compact` ao concluir uma etapa e ao passar de ~150k (não esperar estourar); `/clear` ao trocar para tarefa não relacionada. Na CLI, `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=60` dispara o auto-compact antes do default (~83%).
- **Higiene**: uma sessão = uma tarefa coerente. No Cowork o modelo é fixado ao abrir — abrir sessão **nova** já com Opus, não recuperar sessão presa em modelo errado. Evitar `cat` de arquivos grandes quando já há resumo aqui ou em `docs/`.

---

## Saúde do CLAUDE.md — Regras de Manutenção

> **Target: ≤ 800 linhas.** Quando ultrapassar, aplicar as regras abaixo.

### O que FICA no CLAUDE.md

| Categoria | Critério |
|-----------|----------|
| Invariantes e regras | "never do X", contratos de componente, limites arquiteturais |
| Modelo de sessão e domínios | roles, status, close_reason, visibilidade de mensagens |
| Responsabilidades dos componentes | tabela de uma linha por componente |
| Stack por pacote | tabela compacta (linguagem, runtime, porta) |
| Estrutura do repositório | árvore de diretórios do nível `packages/` |
| Kafka topics | tabela de tópicos × producer × consumer |
| Convenções de nomenclatura | padrões de ID |
| Seções de arquitetura ativa | resumo de 15–20 linhas com link para `docs/arcos/` |
| Pending genuíno | máx 50 linhas — apenas itens não implementados |

### O que NÃO pertence ao CLAUDE.md

| Proibido | Vai para |
|----------|----------|
| Itens marcados com ✅ | `CHANGELOG.md` |
| Histórico de implementação (task #N, testes X/Y, build N kB) | `CHANGELOG.md` |
| Documentação completa de um Arc ou módulo (> 50 linhas) | `docs/arcos/{arc}.md` |
| Snippets de código longos (> 10 linhas) fora de invariantes | `docs/arcos/{arc}.md` |
| Detalhes de UI (props, componentes, hooks por feature) | `docs/arcos/{arc}.md` |
| "Pendente (fase 2)" que já foi implementado | Deletar |

### Estrutura de arquivos de referência

```
plughub/
  CLAUDE.md          ← arquitetura viva, regras, invariantes, resumos (≤ 800 linhas)
  TODO.md            ← itens genuinamente não implementados
  CHANGELOG.md       ← histórico de implementações concluídas
  docs/
    modulos/                  ← docs de páginas/features da UI (uma por rota)
    arcos/                    ← docs de implementação por Arc (detalhe técnico)
      arc4-workflow.md        ← Arc 4 completo (workflow, calendar, collect, webhooks)
      delegate-workflow-io.md ← Padrão delegate: workflow delega I/O a agente via suspend/resume
      arc5-segments.md        ← Arc 5 ContactSegment analytics
      arc6-evaluation.md      ← Arc 6 Evaluation platform completo
      arc-evaluation-metrics-methodology.md ← métricas de avaliação (session_metric.*) + dimensões qualitativas IA + metodologia + roteiro
      arc7-auth.md            ← Arc 7 Auth + ABAC completo
      arc8-agent-availability.md ← Arc 8 disponibilidade e pausas
      arc9-agent-groups.md    ← Arc 9 Agent Groups + Supervisor Scope
      arc10-journey.md        ← Arc 10 Journey multi-session
      instance-bootstrap.md   ← reconciliação, RegistrySyncer, hot-reload
      platform-ui.md          ← Frontend Architecture + Agent Assist UI
      ai-gateway.md           ← AI Gateway multi-account, copilot, stateless
      usage-metering.md       ← metering por dimensão, Redis, quota
      pricing.md              ← faturamento por capacidade, billing API
      session-replayer.md     ← Session Replayer, Hydrator, ReplayContext
      session-conference-lifecycle.md ← modelo de 3 camadas, gaps conhecidos
      dashboard.md            ← Dashboard #35, DisplayTool registry, catalog
      queue-attended-model.md ← fila sempre atendida: admissão híbrida, outage, role queue, relatório Fila/SLA, max_wait (A–E ✅)
      pools-infra-report.md   ← relatório Pools/Infra: volume, fila, capacidade, SLA
      customer-surveys.md     ← spec/ADR módulo de pesquisas de satisfação (CSAT/NPS/CES/PMF/FCR)
      customer-contact-history.md ← histórico de contatos do cliente (lista/transcrição/busca) — transversal
    guias/
      context-store.md        ← ContextStore, @ctx.*, segment-scoped
      masked-input.md         ← Masked Input, begin_transaction
      mention-protocol.md     ← @mention protocol
      pool-hooks.md           ← Pool lifecycle hooks
      orchestrator-working-memory.md ← Working memory pattern para orquestradores em loop
      conference-mechanics.md ← Mecanismo de conferência: Redis keys, eventos, posatt, teardown
    adr/
      adr-message-masking.md  ← masking architecture decision
      adr-webchat-channel.md  ← webchat channel architecture
      adr-session-replayer.md ← session replayer architecture
      adr-contact-segments.md ← Arc 5 architecture
      adr-instance-bootstrap.md
      adr-evaluation-sampling.md ← amostragem: cota por agente (virada para estado) + carimbo de versão
      adr-quality-substrate-isolation.md ← isolamento do substrato de avaliação por `origin` (híbrido; implementado ✅)
```

### Como adicionar uma nova feature

1. **Feature pequena** (< 20 linhas): inline na seção H2 existente mais próxima.
2. **Feature média** (20–50 linhas): subseção `###` dentro da seção H2 mais próxima.
3. **Feature grande** (> 50 linhas): criar `docs/arcos/{nome}.md`; adicionar resumo de 15–20 linhas aqui.
4. **Fase pendente concluída**: mover do `## Pending` para `CHANGELOG.md`; atualizar `TODO.md`; **nunca deixar ✅ aqui**.

### Regra de persistência de planejamento

| Tipo de decisão | Onde registrar imediatamente |
|---|---|
| Nova tarefa planejada | Task no tracker (`TaskCreate`) |
| Decisão técnica (> 3 linhas) | Entrada em `TODO.md` com raciocínio |
| Invariante ou regra arquitetural | Seção neste arquivo |
| Implementação concluída | `CHANGELOG.md` |

### Convenção de pastas de documentação

| Pasta | Conteúdo | Quando criar arquivo aqui |
|---|---|---|
| `docs/modulos/` | Docs de páginas e features da UI | Nova rota/módulo de interface |
| `docs/arcos/` | Docs de implementação por Arc | Arc novo ou refactoring de backend significativo |
| `docs/guias/` | Padrões transversais a múltiplos pacotes | Novo padrão (mascaramento, @mention, hooks, etc.) |
| `docs/adr/` | Decisões arquiteturais com trade-offs | Toda decisão estrutural relevante |
| `docs/pacotes/` | Contratos públicos de cada pacote | Novo pacote no monorepo |

### Regra de atualização de documentação

> Toda entrada em `CHANGELOG.md` deve ter um doc correspondente **criado ou atualizado** antes de ser considerada concluída. Se a feature afeta uma rota de UI → atualizar `docs/modulos/`. Se é um Arc ou backend significativo → atualizar ou criar `docs/arcos/`. Se é um padrão transversal → atualizar `docs/guias/`.

> **Conference mechanics**: qualquer mudança no mecanismo de conferência (lifecycle, Redis keys, eventos Kafka/pub-sub, lógica de posatt, filtros no mcp-server, regras de teardown no platform-ui) **deve atualizar `docs/guias/conference-mechanics.md` e adicionar uma entrada em § Histórico de Problemas e Correções** antes de ser considerada concluída.

---

## Unified Session Model

Every contact is a conference room. Core creates the session on every new contact; agents join the room with their queues and receive messages according to visibility options.

### Participant roles

| Role | Description |
|---|---|
| `primary` | Main agent responsible for the interaction |
| `specialist` | Invited expert (task step, assist mode) |
| `supervisor` | Human or AI supervisor monitoring the session |
| `evaluator` | Quality agent evaluating the session (online or post-session) |
| `reviewer` | Human agent reviewing the evaluator's output |

### Session status

| Status | Description |
|---|---|
| `active` | Session in progress with at least one participant |
| `closed` | Session ended normally |
| `abandoned` | No agent joined before the session ended |

### close_reason domain

```
no_resource          — no agents available and no queue configured
max_wait_exceeded    — max queue wait time exceeded
customer_disconnect  — client disconnected (connection_lost)
customer_hangup      — client ended actively (voice/video)
customer_abandon     — client left before being served
flow_complete        — Skill Flow complete step
agent_transfer       — transferred to another pool
agent_hangup         — agent ended actively
session_timeout      — session inactive beyond TTL
system_error         — unrecoverable error
```

### Message visibility

| Visibility | Recipients | Typical use |
|---|---|---|
| `all` | All participants including the customer | Normal service message |
| `agents_only` | All agents, without the customer | Internal note between agents |
| `["part_abc", "part_xyz"]` | Only the listed participant_ids | Supervisor → specific agent, private |

---

## Invariants — never violate

- **AI Gateway is stateless** — processes one turn per LLM call. No state between turns.
- **Routing Engine is the sole arbiter** — no component routes a conversation without going through it.
- **MCP is the only integration protocol** — no direct REST between internal components.
- **pipeline_state persists to Redis on every step transition** — never in memory only.
- **Agent contract**: `agent_login` → `agent_ready` → `agent_busy` → `agent_done`
- **`agent_done` requires `handoff_reason`** when `outcome !== "resolved"`
- **`issue_status` is always required and never empty** in `agent_done`
- **Agents never access backend systems directly** — only via authorised MCP Servers
- **All domain MCP calls are intercepted** — native agents via `McpInterceptor` (in-process); external agents via proxy sidecar on localhost:7422. No MCP call reaches a domain server without permission validation, injection guard, and audit.
- **`insight.historico.*` persists via Kafka, never direct PostgreSQL write**

---

## Configuration — Single Source Invariants

> Regras permanentes. O código ainda tem violações herdadas em burn-down (`docs/arcos/config-consolidation.md`),
> enforçadas pelo guard `infra/check_config_invariants.py` (falha em violação nova).

- **One source per domain** — cada domínio tem UM store canônico: settings horizontais → config-api
  (`platform_config`); pools/skills → agent-registry; users/ABAC → auth-api; forms/campaigns →
  evaluation-api; planos → pricing-api. Config nunca duplicada entre stores.
- **Provisioning only via official API** — todo provisionamento (incl. seed/demo) escreve ATRAVÉS da
  API do store. Proibido: escrita direta em Redis/DB de config, e listas de config hardcoded em
  scripts/serviços.
- **Seed-if-absent / DB-owned (provisioning precedence)** — o YAML declarativo (`infra/registry/*.yaml`)
  apenas **semeia DB vazio** (201 no create); uma vez que a entidade existe, o **DB é fonte de verdade** e o
  `RegistrySyncer` **não sobrescreve** no restart (edições de UI sobrevivem a rebuild — pools, deploy/capacity,
  hooks, escalation/mentionable). `REGISTRY_SYNC_RECONCILE=true` restaura o reconcile (YAML vence) p/ dev/
  GitOps. Skills seguem upsert (são código, não config de tenant). Alvo Fase 2: YAML→migração versionada
  if-absent, store por store.
- **Every config field is UI-editable** — todo campo de config tem superfície na tela do módulo. Campo
  que só existe em YAML/arquivo é dívida a fechar.
- **env only for secrets and wiring** — env é exclusivamente para segredos (JWT, tokens, creds) e
  topologia (URLs, brokers, portas, tenant). Config de negócio/tuning nunca em env. Quando env e
  config-api têm a mesma chave, **config-api vence**.

---

## MCP Interception — Hybrid Proxy Model

| Agent type | Mechanism | Network hop |
|---|---|---|
| Native agent (SDK) | `McpInterceptor` in-process (`@plughub/sdk`) | None |
| External agent (LangGraph, CrewAI) | `plughub-sdk proxy` sidecar on localhost:7422 | Loopback only |

Checks per call (< 1ms): permission validation (JWT local decode) → injection guard (13 patterns) → audit record (Kafka `mcp.audit`, fire-and-forget). Audit policy defined per tool, not per call — caller cannot opt out (LGPD). `AuditRecord` includes: `server_name`, `tool_name`, `allowed`, `injection_detected`, `duration_ms`, `source` (`in_process`|`proxy_sidecar`).

---

## Repository Structure

```
plughub/
  CLAUDE.md                      ← this file
  plughub_spec_v1.docx           ← full architectural specification
  packages/
    schemas/                     ← @plughub/schemas — Zod contracts
    sdk/                         ← @plughub/sdk — TypeScript + Python
    mcp-server-plughub/          ← Agent Runtime and BPM tools
    skill-flow-engine/           ← Skill Flow interpreter
    ai-gateway/                  ← LLM calls and context extraction (Python)
    agent-registry/              ← CRUD for AgentType, Pool, Skill, GatewayConfig
    routing-engine/              ← Agent allocation and queue management
    rules-engine/                ← Post-routing event evaluation
    channel-gateway/             ← Channel adapters and inbound normalisation
    calendar-api/                ← Calendar engine + CRUD REST (Arc 4) — port 3700
    workflow-api/                ← Workflow instance lifecycle (Arc 4) — port 3800
    skill-flow-worker/           ← Kafka consumer, runs SkillFlow for workflow instances
    pricing-api/                 ← Capacity-based billing, invoice — port 3900
    auth-api/                    ← Auth, JWT, ABAC — port 3200
    evaluation-api/              ← Quality evaluation platform (Arc 6) — port 3400
    quality-ingest/              ← Pluggable contact-history reader (R13a) — port 3850
    quality-export/              ← Internal history → re-evaluation (R13d) — port 3852
    mcp-server-knowledge/        ← Vector knowledge base for RAG agents
    platform-ui/                 ← All operator-facing UI (React + Vite)
```

## Stack per Package

| Package | Language | Runtime | Notes |
|---|---|---|---|
| schemas | TypeScript | Node 20+ | Zod 3.23+ |
| sdk | TypeScript + Python | Node 20+ / Python 3.11+ | Two parallel packages |
| mcp-server-plughub | TypeScript | Node 20+ | Official Anthropic MCP SDK |
| skill-flow-engine | TypeScript | Node 20+ | State graph interpreter |
| ai-gateway | Python | Python 3.11+ | FastAPI + Anthropic SDK |
| agent-registry | TypeScript | Node 20+ | PostgreSQL + Prisma |
| routing-engine | Python | Python 3.11+ | Redis + Kafka |
| rules-engine | Python | Python 3.11+ | Redis + ClickHouse |
| calendar-api | Python | Python 3.11+ | FastAPI + asyncpg — port 3700 |
| workflow-api | Python | Python 3.11+ | FastAPI + asyncpg — port 3800 |
| skill-flow-worker | TypeScript | Node 20+ | Kafka consumer + SkillFlowEngine bridge |
| channel-gateway | Python | Python 3.11+ | FastAPI + aiokafka + channel adapters |
| pricing-api | Python | Python 3.11+ | FastAPI + asyncpg + openpyxl — port 3900 |
| auth-api | Python | Python 3.11+ | FastAPI + asyncpg + bcrypt + python-jose — port 3200 |
| evaluation-api | Python | Python 3.11+ | FastAPI + asyncpg — port 3400 |
| quality-ingest | Python | Python 3.11+ | FastAPI + aiokafka (pure producer) — port 3850 |
| quality-export | Python | Python 3.11+ | FastAPI + httpx (ClickHouse-only reader) — port 3852 |
| platform-ui | TypeScript | Node 20+ / Vite | React 18, Tailwind, i18n |

## Package Dependencies

```
schemas         ← base — no internal dependencies
sdk             ← depends on: schemas
mcp-server      ← depends on: schemas
skill-flow      ← depends on: schemas, mcp-server
ai-gateway      ← depends on: schemas
agent-registry  ← depends on: schemas
routing-engine  ← depends on: schemas, agent-registry
rules-engine    ← depends on: schemas, routing-engine
channel-gateway ← depends on: schemas
auth-api        ← no internal dependencies (standalone user store)
```

Never create circular dependencies. `schemas` never depends on any other package.

## Component Responsibilities (Summary)

| Component | Sole responsibility |
|---|---|
| **Core** | Session lifecycle, canonical stream, message masking, adapter coordination |
| **Channel Gateway** | Inbound normalisation, outbound rendering, fallback interaction collection |
| **AI Gateway** | Stateless LLM inference. Does not manage session or history. |
| **Agent Registry** | CRUD for AgentType, Pool, Skill, GatewayConfig. Cache invalidation via Kafka. |
| **Routing Engine** | Agent allocation, queue management, scoring algorithm, close_reason detection |
| **Rules Engine** | Post-routing event evaluation. Publishes consequences. No routing, no Redis polling. |
| **Skill Flow Engine** | Flow interpreter. Persists pipeline_state to Redis on every step. |

---

## Instance Bootstrap — Reconciliation-Driven Agent Management

Kubernetes-style reconciliation controller in `orchestrator-bridge/instance_bootstrap.py`. Compares desired state (Agent Registry) vs actual state (Redis) and applies minimum diff. Triggers: startup, heartbeat 15s, periodic 5min, `registry.changed`/`config.changed` Kafka. ReconciliationReport: `created/deleted/drained/updated/renewed/unchanged/errors/duration_ms/dry_run`.

**RegistrySyncer** runs before Bootstrap: upserts pools+agent_types from `infra/registry/*.yaml`; prunes stale (`REGISTRY_SYNC_PRUNE=true`). Skill sync: PUTs `skill-flow-engine/skills/*.yaml` before pools (slug `^skill_[a-z0-9_]+$`, **publica produção via `x-skill-publish:true`** — Skill Versioning Fase B). Instance IDs: `{agent_type_id}-{n+1:03d}`. Human agents NOT managed by Bootstrap. Seed no longer writes Redis keys.

**Execução = produção, não a edição (Skill Versioning Fase B/P1):** o bridge executa o **snapshot do slot `current` do POOL** (`get_pool_current_flow`, cache por pool, invalidado no `registry.changed(pool)` do promote/rollback), com **fallback** para `skill.flow` (pools não migrados). O editor (`PUT /v1/skills`) escreve **`skill.flow_draft`** (rascunho) — **não vaza para produção**; só o deploy (set-next→promote, ou `x-skill-publish`) preenche o que roda.

**Versão = deploy do pool (Skill Versioning Fase C):** identidade de versão = **`set_at` do slot `current`** (momento do promote), carimbada em `segments.deploy_version` pelo bridge (cache `_pool_deploy_version_cache`, fallback `skill.version`). O **promote grava um `SkillDeployment`** (`deployed_at=set_at`, `version`=rótulo `skill.version`) — append-log que o epoch usa p/ rótulo+markers; o analytics casa por `deployed_at`. `skill.version` deixou de ser identidade (vira rótulo). Ver `docs/product/skill-versioning-deploy-spec.md`.

→ See [`docs/arcos/instance-bootstrap.md`](docs/arcos/instance-bootstrap.md)

---

## ContextStore & Context-Aware Progressive Resolution

Redis hash `{tenantId}:ctx:{sessionId}`. `ContextEntry`: `{value, confidence 0-1, source, visibility, updated_at}`. Tag namespaces: `caller.*` (customer data), `session.*` (session state), `account.*` (account data), `segment.{segId}.*` (per-agent isolated). Confidence: ≥0.9 confirmed; ≥0.7 high certainty; 0.4-0.7 uncertain; <0.4 unknown.

`@ctx.*` resolves in step inputs, choice conditions (`exists`/`confidence_gte`/`eq`/etc.), and visibility arrays. `@segment.*` prefixed with `segment.{segId}.` isolates parallel agents. `context_tags` on reason/invoke/notify: `inputs` (pre-call) + `outputs` (post-call, fire-and-forget, confidence + merge strategy). Sentiment emitter writes `session.sentimento.current` + `session.sentimento.categoria` (confidence 0.80, TTL 4h).

**Step `resolve`**: 5-phase inline accumulation (gap check → CRM → LLM question → BLPOP → LLM extract). **agente_contexto_ia_v1**: 0 LLM when CRM resolves; max 2 when collecting. **Copilot**: fire-and-forget analysis per client message → `session.copilot.*` tags. `supervisor_state` returns `context_snapshot` from ContextStore.

**Pool Context Enrichment** (Routing Engine): after every successful allocation, `_write_pool_context()` writes `session.pool.id`, `session.pool.channels`, and (when set) `session.pool.mentionable_pools` to ContextStore (source: `routing_engine`, confidence: 1.0, visibility: `agents_only`, TTL 24h NX). Reads from routing engine's own Redis cache — no extra I/O. `PoolConfig.mentionable_pools: dict[str, str]` populated from `pool.registered` events.

→ See [`docs/guias/context-store.md`](docs/guias/context-store.md)

---

## Channel vs Medium

- **channel** = specific channel (`whatsapp`, `webchat`, `voice`, `email`, `sms`, `instagram`, `telegram`, `webrtc`) — **hard filter** for routing, mandatory match
- **medium** = base type (`voice`, `video`, `message`, `email`) — **score factor**, fine-tuning only

## Canonical Stream

`session:{id}:stream` is the single source of truth for all session events. **All XADD calls MUST go through `writeStreamEntry()`** in `lib/write-stream-entry.ts` — never call `redis.xadd()` directly. Sole exception: `session_opened`/`session_closed` in Core `server.ts`. Guarantees: `event_id` always present, `segment_id` always flat, `author_id`/`author_role` flat fields, Zod validation before write.

Messages carry `content` (masked) and `original_content` (unmasked, authorized roles only for LGPD audit).

## Sentiment Tracking

Score-only array in Redis during session. Labels calculated at read time using tenant-configurable ranges. Persisted to PostgreSQL (`sentiment_timeline JSONB`) on session close. Never published to canonical stream.

```
session:{id}:sentiment → [{ score: 0.40, timestamp: "..." }, ...]
TTL: same as session TTL
Ranges: [ 0.3, 1.0] → satisfied | [-0.3, 0.3] → neutral | [-0.6,-0.3] → frustrated | [-1.0,-0.6] → angry
```

## Skill Flow — Thirteen Step Types

| Type | Does | Interacts with |
|---|---|---|
| `task` | Delegates to agent via A2A (`assist`/`transfer`) | Routing Engine |
| `choice` | Conditional branching via JSONPath | pipeline_state |
| `catch` | Retry and fallback before escalation | pipeline_state |
| `escalate` | Routes to pool | Rules Engine |
| `complete` | Closes with defined outcome | agent_done |
| `invoke` | Calls MCP tool directly | MCP Server |
| `reason` | Invokes AI Gateway with output_schema | AI Gateway |
| `notify` | Sends message to customer (unidirectional) | Core → Channel Gateway |
| `menu` | Captures customer input, suspends until reply | Core → Channel Gateway |
| `suspend` | Suspends workflow until external signal | workflow-api |
| `collect` | Contacts target via channel, awaits response | workflow-api → Channel Gateway |
| `resolve` | Inline context accumulation (5-phase pipeline) | ContextStore + AI Gateway |
| `begin_transaction` / `end_transaction` | Masked input atomic block | in-memory only |
| `receive` | Suspends awaiting next stream message from any participant (no prompt sent to channel) | Redis BLPOP on `receive:result:{sid}:{iid}` |

`menu` interaction modes: `text`, `button` (≤3 WhatsApp), `list`, `checklist`, `form`. Fallback for unsupported channels in Channel Gateway adapter only.

## Routing Algorithm — Key Rules

1. **channel is a hard filter** — agent not supporting contact channel = forbidden
2. **agent pause is a hard filter** — paused agents excluded
3. **gateway heartbeat TTL** — agents on gateways >90s expired = excluded
4. **SLA lazy evaluation** — `min(wait_time / sla_target, max_score)` at queue head only
5. **Tie-breaking** — equal-score pools broken by shortest queue length
6. **close_reason detection** — `no_resource` when no queue; `max_wait_exceeded` by lazy eval

## Rules Engine — Scope

Consumes: `conversations.routed`, `conversations.queued`, `conversations.abandoned`, `agent.done`. Publishes: `rules.escalation.events`, `rules.shadow.events`, `rules.session_tagged`. Does NOT: monitor Redis, evaluate sentiment, make routing decisions, maintain state between events.

---

## Kafka Topics

| Topic | Producer | Consumer(s) |
|---|---|---|
| `conversations.inbound` | Channel Gateway | Core, Routing Engine |
| `conversations.routed` | Routing Engine | Core, Rules Engine |
| `conversations.queued` | Routing Engine | Rules Engine |
| `conversations.abandoned` | Routing Engine | Core, Rules Engine |
| `conversations.session_opened/closed` | Core | Analytics, LGPD |
| `conversations.message_sent` | Core | Analytics |
| `conversations.participants` | orchestrator-bridge | analytics-api → ClickHouse |
| `rules.escalation.events` | Rules Engine | Routing Engine |
| `rules.shadow.events` | Rules Engine | Analytics |
| `registry.changed` | Agent Registry | Routing Engine, Core, orchestrator-bridge |
| `config.changed` | Config API | orchestrator-bridge, routing-engine |
| `gateway.heartbeat` | Channel Gateway | Routing Engine |
| `agent.done` | Routing Engine | Rules Engine, Analytics |
| `queue.position_updated` | Routing Engine | Channel Gateway, Analytics |
| `mcp.audit` | McpInterceptor / proxy sidecar | Analytics, LGPD |
| `sentiment.updated` | AI Gateway | analytics-api |
| `evaluation.events` | evaluation-api (requested), session-replayer (requested), mcp-server-plughub (completed) | session-replayer + routing-engine (requested→avaliador); evaluation-api (completed→ingest, persiste result+instance); analytics-api → ClickHouse |
| `workflow.events` | workflow-api | skill-flow-worker |
| `collect.events` | workflow-api | analytics-api |
| `session.signals` | mcp-server-plughub (`survey_record`) | analytics-api → ClickHouse |
| `usage.events` | Core, AI Gateway, Channel Gateway | usage-aggregator |
| `events.dead_letter` | skill-flow-worker, analytics-api, orchestrator-bridge | ops/monitoring |

## Kafka Event Schemas — Zod Coverage

All cross-package Kafka events have Zod schemas in `@plughub/schemas`:

| Topic | Schema | File |
|---|---|---|
| `rules.escalation.events` | `RulesEscalationEventSchema` | `rules-events.ts` |
| `registry.changed` | `RegistryChangedEventSchema` | `platform-events.ts` |
| `config.changed` | `ConfigChangedEventSchema` | `platform-events.ts` |
| `sentiment.updated` | `SentimentUpdatedEventSchema` | `platform-events.ts` |
| `queue.position_updated` | `QueuePositionUpdatedEventSchema` | `platform-events.ts` |
| `conversations.routed/queued` | `ConversationRoutedEventSchema` | `platform-events.ts` |
| `agent.lifecycle` | `AgentLifecycleEventSchema` | `platform-events.ts` |
| `workflow.events` | `WorkflowEventSchema` | `workflow.ts` |
| `collect.events` | `CollectEventSchema` | `workflow.ts` |
| `usage.events` | `UsageEventSchema` | `usage.ts` |
| `conversations.participants` | `ConversationParticipantEventSchema` | `contact-segment.ts` |
| `mcp.audit` | `AuditRecordSchema` | `audit.ts` |
| `evaluation.events` | `EvaluationEventSchema` | `evaluation.ts` |
| `session.signals` | `SessionSignalEventSchema` | `survey.ts` |

---

## Naming Conventions

```
skill_id:       skill_{slug} (estável)  →  skill_portabilidade_telco   (sem versão no id; versão é do DEPLOY, ver docs/product/skill-versioning-deploy-spec.md; `_v\d+` legado ainda válido)
agent_type_id:  {name}_v{n}            →  agente_retencao_v1
pool_id:        snake_case no version  →  retencao_humano
mcp_server:     mcp-server-{name}      →  mcp-server-crm
tool:           snake_case             →  customer_get
insight:        insight.historico.*    →  customer long-term memory
                insight.conversa.*     →  generated in current session, expires on close
```

### Language Rule — English in code, Portuguese only in display

All technical identifiers MUST be in English: URL routes, TypeScript/Python variable names, function names, interface names, type union values, i18n key names, file names, folder names, navKeys, tab IDs, ABAC field names, Kafka topic names, Redis key patterns, and API endpoint paths.

Portuguese is allowed ONLY in: i18n value strings (the translated text shown to the user) and in business-domain entity IDs (`agente_*`, `skill_*`, `pool_id`, `tenant_id`) that represent named instances configured by the tenant — these are data, not code.

```
✅  route: /config/channels        href: t('nav.channels')    tab: 'report'
❌  route: /config/canais          href: t('nav.canais')       tab: 'relatorio'

✅  agente_retencao_v1   (entity ID configured by tenant — data, not code)
❌  const atendimento =  (TypeScript variable)
❌  def mascaramento():  (Python function)
```

### i18n Invariant — every visible string goes through `t()`

Any change to `platform-ui` that adds or modifies **text visible to the user** MUST:

1. Add the key to **both** locale files (`en/` and `pt-BR/`) before the PR.
2. Use `useTranslation(namespace)` + `t('key')` in the component — never hardcode strings in JSX.
3. Use the existing namespace for the module (see `docs/arcos/platform-ui.md` § i18n) or register a new one in `src/i18n/index.ts`.
4. For helpers **outside React components** that produce translated strings: receive `t` as an explicit parameter — never call `useTranslation` at module level.

```
✅  <span>{t('header.offline')}</span>
✅  addToast(t('message.saved'), 'info')
✅  function label(x: string, t: TFunc): string { return t(`key.${x}`) }
❌  <span>Offline</span>
❌  addToast("Salvo com sucesso", 'info')
❌  const { t } = useTranslation()   // outside a component/hook
```

## What Never To Do

- Never create a component that routes conversations without going through the Routing Engine
- Never access Redis directly from outside routing-engine or skill-flow-engine
- Never redefine types from `@plughub/schemas` locally in another package
- Never add business logic to mcp-server-plughub — it only exposes tools
- Never create a dependency on `ai-gateway` in TypeScript packages — only Python consumes it
- Never use `export *` in packages — always explicit named exports
- Never implement channel-specific rendering logic in skill-flow — adapters live exclusively in channel-gateway
- Never allow a caller to opt out of MCP audit records — policy defined on the tool
- Never write to `insight.historico.*` directly in PostgreSQL — always via Kafka
- Never expose `original_content` of masked messages to agents — only to authorised roles via audit trail
- Never forward tool calls containing injection patterns
- Never send tool list to LLM without applying `permissions[]` filter from JWT
- Never write masked input values to `pipeline_state`, Redis, stream, or logs
- Never allow AI agents to emit `@mention` commands — only `role: primary` or `role: human`
- Never call `redis.xadd()` directly in mcp-server-plughub — use `writeStreamEntry()`
- **Never leave deferred phases undocumented** — every unimplemented phase MUST be registered in `## Pending`
- Never create a new `packages/my-ui/` standalone frontend app — add a module to platform-ui
- **Never derive or duplicate participant identity into a wider-scope field** — participant identity is a single-source fact in the ContextStore at the correct scope (`session.*` contact-level, `segment.{segId}.*` segment-level). A per-segment fact (e.g. which human a wrap-up hook serves → `segment.{segId}.served_human_participant_id`) MUST NOT live in a session-global field read by multiple components (collapses in multi-human). See [`docs/adr/adr-participant-identity-single-source.md`](docs/adr/adr-participant-identity-single-source.md)

## SDK CLI

```bash
plughub-sdk certify            # validates execution contract
plughub-sdk verify-portability # verifies dependency isolation
plughub-sdk regenerate         # regenerates proprietary agent as native
plughub-sdk skill-extract      # extracts skill from existing agent
plughub-sdk proxy              # starts proxy sidecar on localhost:7422
```

## Operational Visibility — Section 3.3c

Routing Engine writes pool snapshot to Redis after every routing event:
`{tenant_id}:pool:{pool_id}:snapshot` (TTL 120s) — `{ pool_id, available, queue_length, sla_target_ms, channel_types, updated_at }`.

Three MCP tools (group `operational`): `queue_context_get`, `pool_status_get`, `system_availability_check`. When contact is queued, Routing Engine publishes `queue.position_updated` to Kafka.

## Security — Section 9.5

**Tool permission filtering**: `InferenceRequest.permissions` from JWT → `InferenceEngine.infer()` filters tool list. Empty = no filtering (backward-compatible).

**Injection guard** (`injection_guard.ts`): 13+ heuristic regex patterns. Applied in `notification_send` (message) and `conversation_escalate` (pipeline_state). Future: apply at proxy sidecar level for all domain tool calls.

---

## Message Masking, @mention & Masked Input

Token format in stream: `[{category}:{token_id}:{display_partial}]` (e.g. `[cpf:tk_b7d2:***-00]`). Stream stores `content` (masked) + `original_content` (unmasked). Default `authorized_roles: ["evaluator", "reviewer"]`. Domain MCP tools resolve tokens via `McpInterceptor.resolveToken` callback. Channel Gateway strips to `display_partial` only before WS delivery.

**@mention**: only `role: primary` or `role: human` may issue mentions. Domain closed by `mentionable_pools` pool config. `mention_commands` YAML declares actions: `set_context`, `trigger_step`, `terminate_self`.

**Masked Input**: `masked: true` on menu step (field-level or step-level). `begin_transaction`/`end_transaction` wraps collection-validation-action as atomic block. `@masked.*` namespace in-memory only — never written to Redis, pipeline_state, stream, or logs. Retry always recolects; never re-uses masked values.

→ See [`docs/adr/adr-message-masking.md`](docs/adr/adr-message-masking.md), [`docs/guias/masked-input.md`](docs/guias/masked-input.md), [`docs/guias/mention-protocol.md`](docs/guias/mention-protocol.md)

---

## Session Replayer — Quality Evaluation Pipeline

Pattern: ensure-before-read with optional Hydrator. Pipeline: `session_closed` → Stream Persister (PostgreSQL) → `evaluation.requested` → Hydrator (Redis hit: no-op; miss: PG→Redis) → Replayer (always reads Redis) → `ReplayContext` at `{tenant}:replay:{session_id}:context` (TTL 1h) → Evaluator (evaluation_context_get → evaluation_submit) → `evaluation.events` → ClickHouse.

`ReplayContext` extended for Arc 6: `evaluation_form`, `campaign_context`, `knowledge_snippets` (top-5). **Comparison Mode**: `comparison_turns` with Jaccard similarity (threshold 0.4); `buildComparisonReport()` with divergence_points. `ReplayEvent.delta_ms` preserves original intervals; `speed_factor` scales timing (default 10x batch).

**R5/B — tier-2 de IA (evidência de execução):** no `session_closed`, além do Stream Persister, o **`PipelineStatePersister`** snapshota o `pipeline_state` (transitions) na tabela durável **`session_pipeline_state`** (a trajetória real não vai ao stream e o Redis tem TTL 24h; substrato reaproveitável pelo R4). `ReplayContext.pipeline_state` = trajetória REAL (PG→fallback Redis; ausente→`na`). `evaluation_context_get` injeta `tool_trace` (analytics-api `GET /v1/audit/mcp-calls?session_id`) + `flow_definition` (trajetória esperada, agent-registry `GET /v1/skills/:flow_id`). Sem input/output snapshot (R7).

→ See [`docs/arcos/session-replayer.md`](docs/arcos/session-replayer.md), [`docs/adr/adr-session-replayer.md`](docs/adr/adr-session-replayer.md)

---

## Session & Conference Lifecycle — Three-Layer Model

Three independent layers must not be collapsed: **(1) contact lifecycle** (customer perspective, statistics frozen at customer departure); **(2) agent segment lifecycle** (each participant's window, pool resource freed at `agent_done`); **(3) conference infrastructure** (the room, destroyed only when all participants leave). The current implementation conflates layers 1 and 3 — `_trigger_contact_close()` currently serves both. Known gaps: G1 (AHT inflated by wrap-up time), G2 (`remaining` ignores AI specialists), G3 (AI instance restored while still running), G4 (supervisor has no heartbeat cleanup), G5 (primary AI close expels supervisor), G6 (redundant restore on agent_done close), **G7** (`on_human_end` decoupled from contact-close **only** for the transfer case — `reason==agent_transfer` branch; generic segment-end semantics, NPS-as-contact-hook, and non-transfer continuations remain debt). Fixes applied 2026-05-10: busy counter on cross-pool transfer, pool counter on queue entry, `agent_done` publish from bridge for native/YAML-fallback agents. **Console Transfer (2026-06-12)**: `POST /api/session_transfer` + bridge `agent_transfer` branch make human→pool transfer functional (origin leaves as segment-end, contact continues via re-route, no premature close). See `docs/guias/conference-mechanics.md` § Mudança 9.

→ See [`docs/arcos/session-conference-lifecycle.md`](docs/arcos/session-conference-lifecycle.md)

---

## Usage Metering

Kafka topic `usage.events` — `UsageEventSchema`: `event_id`, `tenant_id`, `session_id`, `dimension`, `quantity`, `source_component`, `metadata`. No pricing in usage records — metering ≠ pricing.

Dimensions wired: `sessions` (Core, SET NX guard), `messages` (Core, visibility=all), `llm_tokens_input/output` (AI Gateway), `webchat_attachments` (Channel Gateway). Pending: `whatsapp_conversations`, `voice_minutes`, `sms_segments`, `email_messages` (functions ready, adapters not yet wired).

Redis: `{t}:usage:current:{dimension}` (45d), `{t}:quota:limit:{dimension}`, `{t}:quota:concurrent_sessions`. `assertQuota` (INCRBY-check-rollback). Cycle reset: `POST /admin/cycle-reset` (port 3950).

→ See [`docs/arcos/usage-metering.md`](docs/arcos/usage-metering.md)

---

## WebChat Channel — Hybrid Stream Model

Three distinct channels: `webchat`, `webrtc`, `whatsapp`. Client is NOT a named participant — Channel Gateway does XREAD on `session:{id}:stream` directly. Reconnect via cursor: zero messages lost. WebchatAdapter: 3 concurrent async tasks (receive_loop, stream_delivery_loop, typing_listener).

Upload (2-stage): WS `upload.request` → `upload.ready` (file_id, upload_url) → HTTP POST binary → `upload.committed` → WS `msg.image/document/video`. MIME allowlist: JPEG/PNG/WebP/GIF (16MB), PDF (100MB), MP4/WebM (512MB). Expiry: soft-delete hourly, physical delete daily (+24h grace). JWT via message body, never URL. `jwt_secret` per tenant via Redis `{tenant_id}:config:webchat:jwt_secret`.

Masked fields delivery chain: `step.masked` → `notification_send` args → `conversations.outbound` Kafka → `WsMenuRender.masked_fields` → `interaction.request` WS event → `<input type="password">` overlay in webchat.

→ See [`docs/adr/adr-webchat-channel.md`](docs/adr/adr-webchat-channel.md)

---

## Pricing Module — Capacity-Based Billing

`packages/pricing-api/` — Python FastAPI, port 3900. Billing by configured capacity, not consumption. Two components: **base capacity** (monthly pro-rated, billing_days) + **reserve pools** (full-day billing per activation day). `billing_cycle_day` default 1. `reserve_markup_pct` default 0%.

Endpoints: `GET /v1/pricing/invoice/{tenant_id}` (JSON + `?format=xlsx`), `POST /v1/pricing/resources/{tenant_id}`, `POST /v1/pricing/reserve/{tenant_id}/{pool_id}/activate|deactivate`. Config API namespace `pricing`: `unit_prices`, `reserve_markup_pct`, `billing_cycle_day`, `currency`. Platform-UI BillingPage at `/config/billing` (role: admin). Quota limits written to Redis on plan activation — not seeded by Config API.

→ See [`docs/arcos/pricing.md`](docs/arcos/pricing.md)

---

## Pool Lifecycle Hooks

Hooks declared in pool YAML (`PoolHooks.on_human_start`/`on_human_end`/`post_human`). Bridge dispatches synthetic `conversations.inbound` with `conference_id` — reuses 100% of conference infrastructure.

**on_human_end** → NPS + wrap-up agents activated in parallel. NPS visibility = `["@ctx.session.customer_participant_id"]` (customer-only). Wrap-up visibility = `["@ctx.session.human_agent_participant_id"]` (agent-only). **Phase B**: `agent_done` does NOT close WS; bridge holds close until all hook agents complete. `hook_pending` Redis counter controls when `_trigger_contact_close()` fires. **Phase C**: `post_human` hooks fire after all `on_human_end` agents complete. Participation events (`conversations.participants`) written by bridge for analytics.

Pre-hook ContextStore writes (before hooks fire): `session.close_origin`, `session.customer_participant_id`, `session.human_agent_participant_id`.

→ See [`docs/guias/pool-hooks.md`](docs/guias/pool-hooks.md), [`docs/guias/conference-mechanics.md`](docs/guias/conference-mechanics.md)

---

## Arc 5 — ContactSegment Analytics

`ContactSegment`: `segment_id`, `session_id`, `participant_id`, `pool_id`, `role`, `agent_type`, `parent_segment_id` (null for primary), `sequence_index`, `started_at`, `ended_at`, `duration_ms`, `outcome`, `close_reason`. Conference topology: specialist `parent_segment_id` → primary `segment_id`. Sequential handoffs: `sequence_index` increments.

ClickHouse tables: `analytics.segments` (`ReplacingMergeTree` ORDER BY `(tenant_id, session_id, segment_id)`), `analytics.session_timeline` (enriched with `segment_id`), `mv_agent_performance_daily` (AggregatingMergeTree), `mv_segment_summary`. Endpoints: `GET /reports/segments`, `GET /reports/agents/performance`, `GET /reports/agent-performance/daily`, `GET /reports/sessions/complexity`.

→ See [`docs/arcos/arc5-segments.md`](docs/arcos/arc5-segments.md), [`docs/adr/adr-contact-segments.md`](docs/adr/adr-contact-segments.md)

---

## AI Gateway — Multi-Account Rotation

`AccountSelector` in `account_selector.py` — Redis-backed, stateless per call. Algorithm: for each account, check throttle key (`ai_gw:{provider}:{key_id}:throttled`); score = `rpm_used/rpm_limit × 0.7 + tpm_used/tpm_limit × 0.3`; pick lowest score. On 429/529: `mark_throttled` → next account → cross-provider fallback (`FallbackConfig`).

Config: `PLUGHUB_ANTHROPIC_API_KEYS=sk-1,sk-2,sk-3` (multi-key activates AccountSelector). `PLUGHUB_OPENAI_API_KEYS` optional fallback. Model profiles: `realtime` (Sonnet → gpt-4o), `balanced` (Haiku → gpt-4o-mini), `evaluation` (Haiku — isolated from realtime). Config API namespace `ai_gateway`: `account_rotation_enabled`, `throttle_retry_after_s`, `evaluation_model`.

→ See [`docs/arcos/ai-gateway.md`](docs/arcos/ai-gateway.md)

---

## Arc 8 — Agent Availability & Pause Tracking

Pipeline for tracking human agent pauses. Config API namespace `agent_activity`, key `pause_reasons` (seedable pause reason list). Pause endpoints: `PUT /api/agent-pause` and `PUT /api/agent-resume` in mcp-server-plughub — updates Redis state, publishes `agent_pause`/`agent_ready` to `agent.lifecycle` Kafka with `reason_id`/`reason_label`. ClickHouse table: `agent_pause_intervals` (ReplacingMergeTree). Analytics: `GET /reports/agent-availability` with pool scoping. Platform-UI: `AgentReportsPage.tsx` at `/contacts/reports/agents`.

→ See [`docs/arcos/arc8-agent-availability.md`](docs/arcos/arc8-agent-availability.md)

---

## Frontend Architecture — platform-ui

Single-app shell in `packages/platform-ui/`. Design tokens: `primary=#1B4F8A`, `secondary=#2D9CDB`, `accent=#00B4D8`, `green=#059669`, `warning=#D97706`, `red=#DC2626`. Font: Inter. Never use inline hex — Tailwind tokens only.

Roles: `operator` (Monitor+Contacts), `supervisor` (+Evaluation+Reports), `admin` (+Config+Skills), `developer` (+DevTools), `business` (cross-cutting, no operational items). **ABAC gates** on nav items: `operacao` field gates Monitor/Editor/Calendar/Deploy/AgentAssist; `visualizar` gates Reports/Análise tabs.

Nav groups (navKey): Home 🏠, Console 🖥️ (contacts.operacao), Monitor 📡 (Sessions/Agents/Pools/Events/Processes), Fluxo 🔄 (Editor/Deploy → skill_flows.operacao), Avaliação ✓ (Forms/Campaigns/Knowledge/Evaluations), Analytics 📊 (Sessions/Agents/Events/Processes/Quality → visualizar/report), Configuração ⚙️ (Dashboards/Resources/Platform/Channels/Calendars/Masking/Billing/Access). Legacy redirects: `/workflows` → `/workflow/monitor`, `/skill-flows` → `/agent-flow/editor`, `/reports` → `/contacts?tab=analise`.

**Skill Deploy Lifecycle**: `deploy_status` (draft/published) + `skill_deployments` table. `PUT /v1/skills` always sets `deploy_status=draft` on new skills, NEVER modifies it on updates. `POST /v1/skills/:id/deploy` — only action that sets published.

**Agent Assist UI** at `/agent-assist`: 4-tab right panel (Estado, Capacidades, Contexto, Histórico). Substitution mode for menu cards. Visibility array routing for NPS/wrap-up agents. Optimistic echo for button selections.

→ See [`docs/arcos/platform-ui.md`](docs/arcos/platform-ui.md)

---

## Arc 7 — Auth, RBAC + ABAC, Performance Routing

**auth-api** (port 3200): users + sessions in PostgreSQL schema `auth`. JWT HS256 TTL 1h; refresh token rotation (43-char opaque, SHA-256 stored). Silent re-auth from `localStorage('plughub_refresh_token')`. `accessible_pools[]` in JWT: empty = all pools; non-empty = row-level filter in analytics-api.

**ABAC** (`module_config` in JWT): `auth.module_registry` seeded from `infra/modules.yaml`. 8 modules: `evaluation`, `contacts`, `billing`, `config`, `skill_flows`, `workflows`, `agent_assist`, `campaigns`. Each field has `access: none|read_only|write_only|read_write` + `scope[]`. `PermissionChecker.can(module, field, minAccess?, scopeId?)`. Graceful degradation for legacy accounts without `module_config`.

**Performance routing** (Arc 7d): `performance_score = resolution_rate × (1 − escalation_rate)`. Blending: `(1-w) × competency + w × performance`; `w = performance_score_weight` (default 0.0, env `PLUGHUB_PERFORMANCE_SCORE_WEIGHT`). Redis key `{tenant}:agent_perf:{agent_type_id}` (TTL 6h). Batch job in analytics-api runs every 5min, lookback 7 days, min 5 sessions for statistical significance.

→ See [`docs/arcos/arc7-auth.md`](docs/arcos/arc7-auth.md)

---

## Arc 6 — Quality Evaluation Platform

**evaluation-api** (port 3400): Forms CRUD, Campaigns (sampling + reviewer rules + contestation policy), Instances (auto-created by sampling engine on `session_closed`), Results, Contestations. Auth: admin via `X-Admin-Token`; review/contest via `Bearer JWT` with ABAC `module_config.evaluation.revisar/contestar`. `available_actions: ["review"|"contest"]` computed server-side — never client-side. Anti-replay: `round` field must match `result.current_round` or 409.

**Workflow as review motor — LEGADO/superseded (decisão 2026-06-25, S2.4).** O contrato canônico de contest→review→finalize é o **Arc 13 REST** (`contestation_router`: `file_contestation` → `submit_review` → `finalize_evaluation`, que emite `evaluation_finalized`). O motor por workflow (`campaign.review_workflow_skill_id`, e.g. `skill_revisao_treplica_v1`) é **paralelo e inerte**: nada no backend o dispara (`review_workflow_skill_id` é só config armazenada, lida pela UI; o único trigger é o harness e2e cenário 28), e a evaluation-api só **reage** (`workflow.events` consumer: suspended → `action_required`/`resume_token`; completed/timeout → `lock_result`, **não finaliza**). Mantido reactive-only por compat com o cenário 28; **não usar como contrato**. Remoção física (consumer, coluna `review_workflow_skill_id`, seletor da UI) = follow-up opcional.

**mcp-server-knowledge** (TypeScript, port 3401): pgvector knowledge base for RAG. Tools: `knowledge_search`, `knowledge_upsert`, `knowledge_delete`. **agente_avaliacao_v1**: loads form + knowledge snippets via `evaluation_context_get`, scores each criterion with evidence, submits via `evaluation_submit`. Analytics: `evaluation_results` + `evaluation_events` ClickHouse tables; `GET /reports/evaluations` + `/reports/evaluations/summary`.

**Real-evaluator persistence path** (validated 2026-06-17): the flow never `claim`s — `evaluation_submit` publishes `evaluation.completed` to `evaluation.events`, and the evaluation-api **ingest consumer** (`evaluation-api-ingest-consumer`, idempotent) maps it → `_ingest_core` (POST-ingest core) → `EvaluationResult` in Postgres + instance → `completed`. Reads (`/v1/evaluation/results`) and the Avaliações UI come from Postgres; ClickHouse is analytics-only. The agente_avaliacao_v1 reason step reads the transcript from `ReplayContext.context.events` (the model field is `events`, not `replay_events`). The current `evaluation_submit` carries a compat shim for the prompt×schema drift (fixed `evaluation_rubric_v3` + lossy `_format_schema` conveyance) — to be removed by the form-driven prompt revision. See [`docs/arcos/arc6-evaluation.md`](docs/arcos/arc6-evaluation.md).

→ See [`docs/arcos/arc6-evaluation.md`](docs/arcos/arc6-evaluation.md)

---

## Arc 4 — Workflow Automation

**workflow-api** (port 3800): `WorkflowInstance` lifecycle. Endpoints: `/trigger`, `/instances/{id}/persist-suspend`, `/resume`, `/complete`, `/fail`, `/cancel`. Timeout scanner: background task, 60s interval, atomic UPDATE. Kafka topic `workflow.events` (7 event types).

**Suspend step**: `reason: approval|input|webhook|timer`, `timeout_hours`, `business_hours` (uses calendar-api). Two-stage idempotency sentinel. **collect step**: contacts target via channel, suspends until response or timeout. `collect_token` for correlation; `campaign_id` as free-form grouper across instances.

**Calendar API** (port 3700): pure engine. Functions: `is_open`, `next_open_slot`, `add_business_duration`, `business_duration`. Feriados recorrentes `MM-DD`. Status 3-state: `open/closed/holiday`. Timezone per tenant. 4 MCP tools wrapping calendar engine.

**Webhooks**: `plughub_wh_{43-char}` token, SHA-256 stored. CRUD (X-Admin-Token) + public `POST /v1/workflow/webhook/{id}` (X-Webhook-Token). Delivery log with timing and status. `origin_session_id` in WorkflowInstance links workflow to parent contact session.

**Skill Deploy** (Phase 2): `POST /v1/skills/:id/deploy` → `skill_deployments` table → `publishRegistryChanged`. Scheduled deploy via `skill_scheduled_deploy_v1` workflow YAML. `GET /v1/skills/:id/handoff-status` for safe deploys.

→ See [`docs/arcos/arc4-workflow.md`](docs/arcos/arc4-workflow.md)

---

## Arc 9 — Agent Groups & Supervisor Scope

`AgentGroup` is a people-management entity, orthogonal to Pool (Pool = routing; Group = org chart). Tables in `auth` schema: `agent_groups`, `agent_group_members` (agent_type_id + is_human), `agent_group_users`, `agent_group_supervisors`, `agent_group_shifts` (days_of_week[], time_start/end TIME, timezone).

**Login/refresh denormalization**: `resolve_supervisor_scope(pool, user_id, role)` in auth-api computes active groups at JWT issue time via shift resolution (spec DOW 0=Sun; Python `weekday()` converted via `(dow+1)%7`). JWT carries `supervised_groups[]`, `supervised_agent_types[]`, `supervised_user_ids[]`. Admin role → `([], [], [])` = no restriction. Supervisor with active groups but no members → `["__no_active_shift__"]` sentinel (prevents empty=unrestricted misinterpretation).

**analytics-api scope filtering**: `PoolPrincipal.supervised_agent_types` (None = unrestricted, list = filter). `_apply_agent_scope()` for segments/performance/availability (direct WHERE). `_agent_scope_session_join()` for sessions (LEFT JOIN on segments FINAL — sessions table has no agent_type_id column). All 5 report endpoints pass `supervised_agent_types` to query functions.

**auth-api REST** (`/v1/groups`, admin-token): full CRUD for groups + sub-resources (members, users, supervisors, shifts). `groups_router.py` registered in `main.py`.

**platform-ui**: `GroupsPage` at `/config/groups` (roles: admin, ABAC `config.users`). List + side drawer with 4 tabs (Info, Members, Supervisors, Shifts). Nav entry added to Configuração group. i18n namespace `groups` (en + pt-BR). `Session.supervisedAgentTypes` + `CurrentUser.supervisedAgentTypes` added to auth layer. Monitor Heatmap filtered by `accessiblePools`; Agents/Instances tabs filtered by `supervisedAgentTypes` (client-side, transparent). Console already scoped via `accessiblePools` in `AgentAssistContext.fetchPools`.

→ See [`docs/arcos/arc9-agent-groups.md`](docs/arcos/arc9-agent-groups.md)

---

## Arc 11 — Console como Superfície de Orquestração ✅

O Console é uma **superfície de orquestração**: o operador humano dirige, delega e monitora agentes AI como coparticipantes de primeira classe (AI e humanos simétricos no modelo de sessão). Funcionalidades: cartões de participantes AI em tempo real (step/status do Skill-Flow); "Adicionar Especialista" (invoca pools de `mentionable_pools` via A2A `assist`); "Delegar Tarefa" (seleção de mensagens → drawer instrução+visibilidade → card de resultado no `agent_done`); Tab de Orquestração (steps do Skill-Flow + intervenções de supervisor). **Permissões**: operar = `agent_assist.operacao`; intervir = role `supervisor` + scope ABAC.

→ See [`docs/arcos/arc11-console-orchestration.md`](docs/arcos/arc11-console-orchestration.md)

---

## Arc 6 Fase 2 — Observabilidade por Deploy ✅ (diário+markers P3 + epoch/versão R15a/R15b + cobertura 1b)

> **Status (completo, 2026-06-24):** lente `deploy` no board de Agentes (`/reports/agents/compare?lens=deploy`,
> decisão D3), **ancorada no POOL** (spec §11), com **dois modos** via `&mode=daily|epoch` (toggle Diário↔
> Por versão na UI): **diário+markers** (1º corte §6) e **epoch/versão** (§4.1/D4 — eixo X = versões). O epoch
> faz `JOIN evaluation_finalized.segment_id→segments` (carimbo `deploy_version` do R9, sem denormalizar),
> `GROUP BY pool/skill/deploy_version`, ordem `deployed_at` (fallback `first_seen`), `min_sample=30`, multi-pool
> = uma curva por pool (união por deployed_at). **Micro-fatia 1b ✅**: overlay de **nota provisória** (linha
> tracejada) + **pendentes de fechamento** por versão, da evaluation-api (`GET /v1/evaluation/reports/deploy-coverage`
> via `coverage_client`, degradação graciosa). Detalhe em `docs/arcos/arc-evaluation-metrics-methodology.md` §IV.8.

**Âncora = POOL** (par `(pool, skill)` colapsado enquanto 1 skill por pool): `skill_id` é estável (deploy não
muda o id; `version` é campo à parte; deploy é pool-centric via `PoolSkillSlot`+`SkillDeployment.pool_ids`), e
**um skill pode rodar em vários pools** → âncora-skill misturaria pools. Curva por pool; um deploy compartilhado
vira o mesmo marcador em cada curva de pool atingida.

**Implementado (P2 + P3):**
- **agent-registry**: `GET /v1/skills/:id/deployments` (P2-A) + `GET /v1/pools/:id/deployments` (P3-A — deploys
  onde pool ∈ `pool_ids`). Header `x-tenant-id`.
- **analytics-api**: config `agent_registry_url`; `deployments_client` (`fetch_skill_deployments` +
  `fetch_pool_deployments`, cache `(kind,tenant,id)` 60s, degradação → `[]`, D1). Lente `deploy` em
  `query_agents_compare` (`_COMPARE_LENSES`, domain `ai`): `_compare_deploy_lens` lê `avg(final_score)` de
  `evaluation_finalized` (Oficial, D2) **agrupado por `attr.pool_id`** (curva por pool); `_fetch_deploy_markers`
  usa a timeline do pool, cada marker com `pool_id`+`skill_id`+`version_label`; `meta.min_sample=30`.
  *(Cuidado CH: `any(attr.agent_type)`, não constante `'ai'` — alias colide com o `WHERE attr.agent_type` e a
  query falha.)*
- **platform-ui** `AgentsBenchPage`/`DeployChart`: na lente, entidades = **pools** (checkbox do pool → `pool_id`,
  cor própria; agentes desabilitados; μ oculto); `include_average=false`. Leitura honesta: eixo diário completo,
  **bolinha = dia com avaliação** + **reta** (`linear`) entre medições (sem zero/interpolação em dia sem amostra);
  **deploy = triângulo** na cor do pool sobre a curva, **versão/skill no tooltip** (`<title>` em `ReferenceDot
  shape`) + **contador** "N deploys" (não cresce com a qtd); flag N<min; estado-vazio "selecione um pool".
  Cleanup T16: `TimeseriesView`/`ComparisonView` mortas removidas (`MetricSelector` mantido, usado por
  `AnaliseComparacaoPage`).
- Demo: analytics-api ganhou `PLUGHUB_AGENT_REGISTRY_URL`. Seed `infra/test/seed_deploy_lens_demo.sh`
  (usa `flow_id == skill_id` p/ alinhar). Testes: `test_deployments_client.py` (9) + `test_deploy_lens.py` (5).

**Limitações registradas:** `ReferenceDot`/eixo categórico só rende se o dia do deploy é categoria (o front
injeta); deploy posterior à última avaliação fica no fim da curva (sem dados pós-deploy ainda).

→ See [`docs/arcos/arc6-phase2-observability.md`](docs/arcos/arc6-phase2-observability.md),
[`docs/product/arc6-phase2-deploy-observability-spec.md`](docs/product/arc6-phase2-deploy-observability-spec.md)

---

## Arc 12 — Agent Business Events ✅

MCP tool `agent_event(category, value, tags?)` para agentes publicarem KPIs de negócio durante sessões. `category` hierárquico `pool_id.skill_id.metric_key` (1º segmento = pool_id da sessão, namespace isolation); contexto resolvido do `session_token`; tags bloqueiam PII; rate limit configurável; auditado via `McpInterceptor`. Infra: topic `agent.events` → ClickHouse `analytics.agent_business_events` (`category_l1..l4` pré-decompostos) + endpoints `/reports/agent-events/{series,summary,categories}`. Integra com Arc 6 Fase 2 (`metrics[]=agent_event:{category}`).

→ See [`docs/arcos/arc12-agent-business-events.md`](docs/arcos/arc12-agent-business-events.md)

---

## Audit LGPD — Compliance Role (Fase 1)

Módulo ABAC `audit` para DPO/compliance — ortogonal às roles existentes. Qualquer usuário com `module_config.audit.*` no JWT tem acesso escalonado. Cinco campos: `sessions`, `mcp_calls`, `user_access`, `data_requests`, `config_snapshot` — os dois primeiros ativos.

**analytics-api** tem dois novos endpoints em `/v1/audit`: `GET /sessions/{id}/messages` (requer `audit.sessions`, escreve linha imutável em `audit_access_log`) e `GET /mcp-calls` (requer `audit.mcp_calls`, filtra por `masked_input_fields`). `_require_audit_access()` decodifica JWT e verifica ABAC — tenant isolation obrigatório.

**ClickHouse**: `mcp_audit_log` (`ReplacingMergeTree`, idempotente) + `audit_access_log` (`MergeTree` — nunca deduplicado por design LGPD). `parse_mcp_audit_event()` agora dual-write: retorna `[timeline_row, mcp_audit_log_row]`.

**platform-ui**: `AuditPage` em `/audit` (5 tabs: Sessions + MCP Calls ativos; 3 stubs). Nav entry standalone "Auditoria LGPD" (🔍) com ABAC gate `audit.sessions`. Warning banner: todo acesso registrado em log.

**Deferred**: `original_content` desmascarado (requer endpoint batch em Core), `user_access` logs, SAR/erasure pipeline, `config_snapshot`.

→ See [`docs/arcos/audit-lgpd.md`](docs/arcos/audit-lgpd.md)

---

## Arc 13 — Evaluation Review, Contestation & Calibration ✅

Dois fluxos por tipo de agente avaliado. **Humano**: revisor AI pré-publicação (gate por campanha) → contestação por dimensão → human reviewer decide (`ContestationThread` append-only; `max_rounds` via `ContestationPolicy`). **AI**: `evaluation_finalized` imediato + curadoria amostral por regras configuráveis; revisor AI gera `calibration_signal` → `CalibrationNote` no knowledge namespace → feedback ao avaliador via RAG. **Invariante**: `evaluation_finalized` é a única fonte de truth para relatórios de qualidade. Topic `calibration.events` + `GET /reports/evaluator-calibration` (Calibration Dashboard, correlaciona com deploy epochs do Arc 6 Fase 2).

→ See [`docs/arcos/arc13-review-contestation.md`](docs/arcos/arc13-review-contestation.md)

---

## Métricas de Avaliação & Metodologia ⚠️ design fechado — R1/R5/R6/R7a/R8a–R8e/R9–R12 ✅ (R8 completo); R7b/R7c fora de escopo (LGPD); R13a–c/R14/R15a–b/R16 PENDENTE

> **Limitação assumida (2026-06-23):** faithfulness sobre **valor PII de output de ferramenta** não é
> suportada — reter o retorno cru (vault R7b) é anti-minimização LGPD sem requisito consentido. R7a
> mascara+descarta o output (postura alinhada). Reabrir só sob requisito de produto explícito. O cofre
> que compliance exige é o de **mensagens** (`TokenVault`), que já existe.

Define **o que o avaliador mede e como** (distinto de revisão/contestação, Arc 13). Duas trilhas.

**Quantitativo (`session_metric.*`)** — catálogo **fechado**, determinístico, sem LLM, **agnóstico de agente** (humano e IA). É o mesmo namespace que os critérios `auto_computed` do formulário consomem via `computation_source` — `auto_computed` **entra na nota** junto com as qualitativas (não é KPI de dashboard à parte). Decisões: **(A)** computa em escopo contato **e** segmento (avaliador usa o do segmento); **(B)** guarda séries brutas (`agent_response_latencies_s`, `inter_message_gaps_s`) p/ perguntas paramétricas; **(C)** `customer_wait_time_s` ≠ `total_silence_s`; **(D)** ausente/não-aplicável = `na` (re-normaliza peso), condicionável por canal; **(E)** computa **lazy no ingest** (só o % amostrado). Saudação = 1ª msg do agente (proxy, sem detecção semântica).

**Qualitativo de IA** — avaliar IA ≠ humano (erros sistemáticos por versão, não episódicos). Dimensões: faithfulness (vs KB / vs ferramenta), tool correctness, policy adherence, abstenção/escalada, safety. **Dois tiers**: transcript-only (já avaliável) × execution-evidence (lacuna). Metodologia (τ-bench, DeepEval, RAGAS): combinar determinístico + rubrica explícita/calibrada com controles de viés; divergência >20–25% vs humano = recalibrar (o loop de calibração do Arc 13 já é esse mecanismo). **Detecção de divergência (R8)**: Estágio 1 = gatilho sobre `calibration_score` (ancorado); Estágio 2 = **curadoria cega-primeiro** (`%`-gated, SLA — humano re-pontua sem ver a IA → diff por dimensão; pega o viés de KB que diversidade de modelo não pega; nota humana autoritativa no desacordo); **revisor heterogêneo** (modelo ≠ avaliador) recomendado reduz viés de modelo (não de KB). Simetria: contestação (humano) ↔ Estágio 2 proativo (IA, sem ferir "IA nunca contesta").

**Amostragem de contatos** — hoje stateless/determinística por hash, `%` por campanha. Modelo-alvo: **cota por agente cumulativa por déficit** (cobertura justa, não representatividade), chave humano `(campaign, user_id)` / IA `(campaign, pool_id, skill_id, deploy_version)` — chavear por versão = "reset no deploy" sem reset (não por `agent_type`, eixo aposentado). Pré-requisito: **carimbar `skill_id`+`deploy_version`+`channel` no `ContactSegment`** (hoje ausente; deploy resolvido do `SkillDeployment` ativo, ancorado no início — conserta também a precisão do Arc 6 Fase 2 e destrava condicionamento por canal no backfill). Modelo de deploy: `skill_id` estável = identidade do artefato, versão = registro de deploy, `_v{n}` cosmético; binding skill↔pool a unificar (`PoolSkillSlot` autoritativo + append-log). Virada para estado (ADR). **Módulo agnóstico/externo**: viável como **grau-transcript** (sem `mcp.audit`/`pipeline_state`/`usage.events` → tier-2 IA indisponível); exige contrato de ingestão versionado + masking + versão dentro do contato. Arquitetura (fechada): A2 document-ingest (`QualityContact`); fan-out **emitindo eventos canônicos** (reusa consumers, gatilho de sampling grátis); stream durável via **opção Y** (importador = produtor puro; consumer interno reconstrói `session_stream_events` dos eventos — isola o ambiente interno); masking pré-processador externo + net no ingest, `original_content=null`.

**Achados de código** (base do roteiro): `SessionMetricsExtractor`/`fill_auto_computed_criteria` existem mas são **órfãos** (nunca chamados) → `auto_computed` é hoje no-op que distorce pesos; o trace `mcp.audit` **não chega** ao `ReplayContext` → tier-2 inavaliável (dado vive em `mcp_audit_log`, via analytics-api `GET /mcp-calls`; `input/output_snapshot` gated por `AuditPolicy.capture_*`). **R7 (§II.5)**: `output_snapshot` hoje é gravado **cru** (vazamento) — fix = aplicar masking (simétrico ao input) + masked+original; faithfulness-PII via vault deferido; avaliador recebe **campo mínimo transiente** (PII não entra no store de avaliação).

→ See [`docs/arcos/arc-evaluation-metrics-methodology.md`](docs/arcos/arc-evaluation-metrics-methodology.md)

---

## Quality Ingest — leitor de histórico plugável (R13a–R13d) ✅ arco completo

Módulo anti-corrupção que faz históricos **externos** (CCaaS) e a **reavaliação interna** entrarem no
MESMO pipeline de avaliação (sampling → ReplayContext → avaliador → analytics), sem o importador tocar a
infra interna. **Interface = stream de eventos** `ingestion_event_v1` (não lote); **pool é a unidade**
(eventos carimbam `pool_id`, não `campaign_id`); tier-2 de IA indisponível p/ externo (grau-transcript).

`packages/quality-ingest/` (Python FastAPI, porta 3850, **produtor puro**) expõe `POST /v1/ingest/events`
(header `X-Tenant-ID`), roda masking net-pass, deriva `session_id`/`segment_id` determinísticos
(idempotência), e **mapeia 1:1** o stream → eventos canônicos internos que os consumers já entendem:
`conversations.events` (contact_open/message_sent/contact_closed), `conversations.participants` (campo
`type` underscore), `agent.lifecycle` `agent_done`, e `conversations.session_closed` (dispara sampling).
Toda emissão leva `source:"external_import"` (gate do consumer Y; nunca `channel_gateway`).
Schemas em `@plughub/schemas/ingestion-event.ts` (R13a-1). **Consumer Y ✅ (R13b)**:
`ImportStreamConsumer` (session-replayer) reconstrói `session_stream_events` (PG) dos eventos canônicos
gated `source=external_import`, via o `StreamPersister.insert_records`/`recompute_deltas` (mesmo escritor do
Persister vivo, sem drift) → Hydrator/Replayer dão um ReplayContext.events igual ao interno. **Mapa por
source ✅ (R13c)**: namespace `quality_ingest.source_map` (Config API); o `SourceMapClient` resolve e o
mapper traduz ext→int (pool, humano→`user_id`, IA→`skill_id`+`deploy_version`) **antes** de emitir
(pass-through se não mapeado). **Exportador interno ✅ (R13d)**: `packages/quality-export/` (ClickHouse-only,
porta 3852) lê `sessions`+`segments`+`messages` (`FINAL`) e re-emite `ingestion_event_v1` pela mesma porta
do quality-ingest (inverso do mapper) — `external_contact_id`=session_id original → novo session_id de
reavaliação. Reusa o pool original; pool dedicado sai do `source_map` (R13c) sem código novo.

→ See [`docs/arcos/quality-ingest.md`](docs/arcos/quality-ingest.md)

---

## Arc 15 — Canal WebRTC com SFU (LiveKit) ✅

Canal `webrtc` browser-to-SFU com medium negociado em tempo real (video→voice→text). Coexiste com `voice` (PSTN/Twilio = tronco externo); `webrtc` = clientes na webapp. **SFU**: LiveKit self-hosted (gravação por egress, supervisão hidden subscriber, multi-participante). **Invariante**: tokens LiveKit emitidos exclusivamente pelo Channel Gateway, nunca expostos ao browser. STT/TTS reusa os FallbackProviders do voice (transporte = LiveKit PCM frames). Console: `WebRTCOverlay` (vídeo/waveform por medium). `media_capabilities: [video,voice,text]` no agente; text = fallback universal. *Futuro*: bridge PSTN→WebRTC via LiveKit SIP Ingress (ver § Pending).

→ See [`docs/arcos/arc15-webrtc.md`](docs/arcos/arc15-webrtc.md)

---

## Arc 19 — Modelo Unificado de Sessão: Workflow como Canal Webhook

Elimina a dualidade contact/workflow tratando workflows como canal `webhook` na channel-gateway. Cada skill registrada num pool webhook é um "endpoint" (análogo a DIN de voz ou número WA). O trigger cria uma sessão normal, o routing engine aloca instância skill-flow do pool, e o `session_id` é o identificador persistente por toda a execução — incluindo múltiplos ciclos de suspend/resume.

**Status `suspended`** adicionado ao domain de sessão. No `suspend()`, o agente fecha o segmento e devolve ao pool (`agent_ready`); a sessão persiste com TTL estendido no Redis (EXPIRE calibrado ao `timeout_hours` — substitui PostgreSQL para durabilidade). No resume, nova alocação normal → novo segmento. **Resume_token lookup** via hash Redis `{tenant}:resume_tokens → session_id`.

**Segregação workflow vs. agente**: perfil `workflow` (channel_type: webhook) permite steps `task/choice/catch/escalate/complete/invoke/reason/suspend/collect/receive` — proibidos `menu/notify/begin_transaction/end_transaction`. Perfil `agent` (demais channels) permite `menu/notify/begin_transaction/end_transaction` — proibidos `suspend/collect`. Validado em parse do YAML + guard no engine.

**Collect step revisado**: exclusivo de workflows. Cria sessão-filho de contato com channel negociado por capabilities (Arc 16). Workflow suspende; agente channel-aware atende a sessão-filho e retorna resultado. Workflow nunca conhece o canal usado.

**WebhookAdapter** em `channel-gateway/adapters/webhook.py`: `POST /v1/channels/webhook/{skill_id}` (trigger), `POST /v1/channels/webhook/resume/{token}` (resume), `GET /v1/channels/webhook/{session_id}/status`. **Pool webhook**: `channel_types: [webhook]` + `skill_id` como endpoint.

**O que é eliminado**: `workflow-api` lifecycle endpoints, `WorkflowInstance` entidade separada, `skill-flow-worker` Kafka consumer, `workflow.events` topic, entidade Journey ✅ (Fase F concluída 2026-05-28), Monitor/Processes e Analytics/Processes páginas separadas.

**Monitor unificado** (4 abas — período: now/last_hour/last_24h/today): Sessions (channel_type filter, badge suspended, métricas Resolved/Escalated/Failure/Timeout/Cancelled/TMA), Pools (snapshot + tendência; webhook pools mostram capacidade configurada), Agents (humanos/AI; skill-flow instances via Pools), Events (Arc 12 business events, filtro regex de category). **Analytics unificado** (4 abas): Sessions (ANI/DNIS por channel_type; hierarquia sessions→segments→detalhe), Pools (time-series capacity), Agents (consolidado + drill-down segments), Events (time-series Arc 12 + drill-down segments). TMA webhook = `SUM(segment.duration_ms)`, não wall-clock.

**6 fases**: A ✅ (WebhookAdapter + channel type), B ✅ (status suspended + TTL Redis), C ✅ (orchestrator-bridge: skill-flow como agente nativo), D ✅ (workflow-api deprecation), E ✅ (Monitor/Analytics unificados), F ✅ (Journey entity elimination — 2026-05-28). **Arc 19 completo.**

→ See [`docs/arcos/arc19-unified-session-model.md`](docs/arcos/arc19-unified-session-model.md)

---

## Pending (Next Iteration)

### Arc 6 Fase 2 — Observabilidade por Deploy ✅ COMPLETO *(diário+markers + epoch/versão + cobertura 1b)*
- **Entregue (P2/P3):** lente `deploy`, modo **diário+markers** (série DIÁRIA `avg(final_score)` Oficial +
  `deploy_markers` via REST do agent-registry, D1).
- **Entregue (R15a/R15b):** modo **epoch/versão** (`&mode=epoch`) — eixo X = versões, `JOIN
  evaluation_finalized.segment_id→segments` (R9), `GROUP BY pool/skill/deploy_version`, ordem `deployed_at`
  (fallback `first_seen`), multi-pool = uma curva por pool (união), esconde média. Toggle na UI.
- **Entregue (micro-fatia 1b — Opção II):** overlay de **nota provisória** (só pontuadas, linha tracejada) +
  **pendentes de fechamento** (`pending_n`) por versão, da evaluation-api (`GET /v1/evaluation/reports/
  deploy-coverage` → `coverage_client` → `_attach_epoch_coverage`). Convergência provisória↔finalizada =
  sinal de confiança. Degrada gracioso (evaluation-api fora → só finalizada).
- **Não-objetivos (backlog, fora):** período A/B arbitrário, overlay multi-métrica/`agent_event`, C3, NPS,
  export, tabela `analytics.deploy_events`/consumer (substituídos por D1/REST).
- Spec: `docs/product/arc6-phase2-deploy-observability-spec.md`. Visão/mockups: `docs/arcos/arc6-phase2-observability.md`.

### Arc 15 — WebRTC (decisão em aberto)
- bridge PSTN → WebRTC via LiveKit SIP Ingress (eliminar Twilio como canal separado).

### Usage Metering — Channel Gateway Adapters
- `whatsapp_conversations`, `voice_minutes`, `sms_segments`, `email_messages` *(deferred)*: functions in `usage_emitter.py` ready, adapters not yet calling them.

### Pricing Module
- **Integração metering × pricing** *(deferred)*: módulo que aplica planos e escreve `{tenant}:quota:limit:*`.

### Audit LGPD — Fases Pendentes
- **Fase 2** *(deferred)*: `original_content` desmascarado via endpoint batch de resolução de tokens em Core.
- **Fase 3** *(deferred)*: `user_access` logs — topic Kafka `user_access.events` em auth-api + ClickHouse.
- **Fase 4** *(deferred)*: SAR/erasure pipeline — pseudonimização `sessions_stream` + anonimização ClickHouse.
- **Fase 5** *(deferred)*: `config_snapshot` — read-only do namespace `masking` do Config API para DPO.

### Quality Ingest — arco COMPLETO (R13a–R13d ✅); concerns abertos
- **Concerns** (ver `docs/arcos/quality-ingest.md` §9): (a) ReplayContext `session_meta`/`participants`/`sentiment` ainda em default p/ importados (transcript completo); (b) correlação por-requisição do quality-ingest (pool_id degrada se um contato vier partido entre POSTs); (c) ✅ **RESOLVIDO** (2026-06-25, ver CHANGELOG + § abaixo) — discriminador `origin` por-sessão + filtro default `live` no report layer/sampling. Resta só a fase 2 (partição CH por origem + `pool.origin_class`).

### Isolamento do substrato por `origin` ✅ (resolve §9c do Quality Ingest)
Discriminador de procedência **por-sessão** `origin: live|import|reeval` (default `live`) nas tabelas de substrato (`analytics.sessions/segments/messages` + PG `session_stream_events`), derivado do `source` do evento (`external_import`→import, `internal:reeval`→reeval). **Garantia de correção = filtro default `live`** no report layer da analytics-api (`_apply_origin_scope`, todas as funções de substrato + bancada) e no sampling da evaluation-api (`_passes_filters`, default `{'live'}`; campanha de reavaliação seta `origin` em `sampling_rules`). Endpoints `/reports/*` expõem query-param `origin`; a UI operacional de Analytics mostra **sempre produção** (seletor de origem NÃO exibido — decisão UX: origem é contexto de qualidade, não dropdown operacional; re-emissão é detalhe de implementação). `OriginSelector`+i18n e `ContactFilters.origin` ficam reservados p/ superfície de qualidade contextual futura. **Invariantes**: `origin` é a verdade universal por-sessão (cobre pool compartilhado do R13d, que segue re-emitindo); o default no backend é a garantia (UI espelha); **não** estender `pool.agent_kind`. Fase 2 (partição CH `PARTITION BY (…, origin)` + `pool.origin_class`) **ADIADA por decisão (2026-06-25)** — é governança/lifecycle, não correção (o filtro default já isola); gatilho de reativação = importação externa real com retenção/erasure própria (LGPD). → [`docs/adr/adr-quality-substrate-isolation.md`](docs/adr/adr-quality-substrate-isolation.md) (Aceito — implementado; fase 2 adiada).

### Business in Any Media — processo channel-abstract + framework de loja *(proposta)*
- Reposicionamento process-centric + comércio conversacional sobre o modelo de 3 níveis (a/b/c). Specs em `docs/product/`: arquitetura-alvo (3 níveis), resolvedor de identidade/cadastro (nível b, generaliza `pending_workflow`), contrato delegate-por-pool, commerce-cards (nível c), fluxo de intake. Detalhe e fases em `TODO.md`. Base existe (workflow+canais+suspend/resume+masking); falta cadastro de identidade completo, commerce-cards e o nível (b) de primeira classe.

### Fila de trabalho humano / dispatch pull + inbox no Console *(proposta)*
- Modo `dispatch_mode: pull` genérico no Routing Engine (claim atômico via `ZREM`, lease+auto-release, ordenação por peso) + inbox no Console + fila de aprovação como especialização (decisão pelo retorno do delegate, sem schema novo). Specs em `docs/product/` (routing-pull-dispatch, human-work-queue-aprovacao, pull-inbox-console-ui). Liga ao gate de promoção homologação→produção. Detalhe em `TODO.md`.

### Record/Replay Harness *(proposta)*
- Generaliza o Session Replayer num harness de gravação/replay em todas as costuras (driver/mock por seam) p/ regressão determinística e gate de promoção via `ComparisonReport`. Falta captura full-fidelity MCP/AI Gateway, clock/seed injetável, gravação seletiva. Spec em `docs/product/record-replay-harness-spec.md`. Detalhe em `TODO.md`.

### Customer Surveys — Módulo de Pesquisas de Satisfação *(spec/ADR)*
- Generaliza o NPS de fim-de-contato (`skill_nps_v1` + `on_contact_end` + `survey_record` → `session_signal`) num módulo de 5 instrumentos (**CSAT/NPS/CES/PMF/FCR**; Health Score = composto futuro). Princípio: separar **instrumento** (`survey_definition`, composto de perguntas reutilizáveis `survey_question` — N formulários por tipo via form-builder; editor — ADR §16×§17: **B decidida** = 1 skill interpretador genérico + **form JSON versionado** (draft/published na evaluation-api), **engine estendido em 2 peças** (`$.config` do slot no flow + `menu.options/fields` dinâmicos), binding via `interface_schema`→`PoolSkillSlot.config_json` (`form_id` + `survey_form_get`); **A alternativa** = compile-to-skill via `SurveyCompiler`) de **gatilho** (**decisão no skill**, não na plataforma) de **veículo** (runner na conferência / link web). **Gatilho (revisão 2026-06-23)**: o hook é genérico e despacha sempre; o `skill_survey_runner_v1` lê `@ctx.session.contact_outcome` e decide — "ciclo fechado" (`resolved`) é convenção customizável do runner, não invariante de plataforma. Único pré-requisito de plataforma: carimbar `contact_outcome`/`segment_outcome` no ContextStore pré-hook. Achado corrigido: o `skill_nps_v1` é slot transacional (CSAT) com instrumento NPS colado — substituído pelo runner genérico. Net new: **quarentena** anti-fadiga (tool MCP `survey_eligibility_check` + ledger PG/Redis), schema PG `survey` (question/definition/instance/response/quarantine), **interface web pública** `/survey/:token` + envio outbound, **lente `customer_voice`/view "Visão do cliente"** na bancada 360°, **navegador de respostas** `/analise/surveys` (lista por tipo + verbatim + áudio/STT, LGPD) e **agente IA `agente_survey_analyst_v1`** (classifica sentiment/tema/urgência + endereça via Rules Engine/`workflow_trigger`). **Retorno outbound** (§19): contato ativo via `collect`/Arc 19, modo auto (rules) OU **caixa de ações no Console** (sessão outbound-intent parqueada na **inbox pull já existente** — `PullInboxPanel`/`dispatch_mode`/`work_queue`; novo = pool de retorno + skill pós-claim). **claim ≠ collect**: o claim só anexa + dispara briefing (`on_human_start` copilot: contexto da origem + verbatim + histórico); o agente coordena o `collect`/dial via menu `agents_only`. Associação à base de cliente via `customer_key` (forward-compatível com o cadastro dinâmico futuro). Fases S1–S10. **Cadastro de cliente e Health Score fora de escopo** (só os ganchos de dados).
- Spec: `docs/arcos/customer-surveys.md`.

### Histórico de contatos do cliente — capacidade transversal *(spec — §20 de customer-surveys.md)*
- Útil a **qualquer atendimento** (não só survey). **Já existe**: lista por `customer_id` (`GET /analytics/sessions/customer/{id}`, ClickHouse `sessions.customer_id` = `customer_key`) + `HistoricoTab`/`useCustomerHistory` no Agent Assist; transcrição por sessão (`GET /analytics/transcript/sessions/{id}`). **Falta**: (1) **drill lista→transcrição** na `HistoricoTab` (wiring do endpoint existente + ACL/masking LGPD) — resolve "ver o atendimento que originou a pesquisa" (`origin_session_id`); (2) **busca** no histórico do cliente (endpoint novo `/sessions/customer/{id}/search?q&from&to&channel&outcome&pool` sobre transcrições persistidas `sessions_stream`/`session_timeline` + snippets, respeitando masking). Consumido pelo briefing de retorno (§19). Unificação cross-canal do `customer_id` = cadastro dinâmico futuro. **Spec**: `docs/arcos/customer-contact-history.md` (fases H1–H5).

