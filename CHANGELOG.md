# CHANGELOG — PlugHub Implementações Concluídas

> Histórico de itens implementados removidos do `## Pending` do CLAUDE.md para reduzir noise no contexto.
> Itens pendentes: ver `TODO.md`.

---

## platform-ui — Config/Recursos + Config/Plataforma + Config/Canais (2026-05-06)

### Configuração / Recursos

- ✅ **Pool form — routing_expression weights**: 5 pesos dinâmicos de prioridade (`weight_sla=1.0`, `weight_wait=0.8`, `weight_tier=0.6`, `weight_churn=0.9`, `weight_business=0.4`) adicionados ao form de pool. Grid 2×3, inputs numéricos com step=0.1, botão "Restaurar padrões". Payload só envia `routing_expression` quando difere dos defaults. Coluna "Prioridade" na tabela mostra SLA+Churn com tooltip completo.
- ✅ **Pool form — calendar_id + routing_skills**: dropdown de template de calendário (calendar-api) e checkboxes de competency skills (Config API namespace `routing`) integrados ao form.
- ✅ **Types**: `RoutingExpression`, `ROUTING_EXPRESSION_DEFAULTS` adicionados a `types/index.ts`; `Pool`, `CreatePoolInput`, `UpdatePoolInput` atualizados.
- ✅ **Remover tab AgentTypes**: tab AgentTypes removida de `config-recursos/index.tsx` (Pools · Skills · Channels apenas). AgentType é criado pelo editor de SkillFlow ao criar novo skill.
- ✅ **SkillFlowsPage — inline AgentType**: `NewSkillForm` substitui `NewSkillPrompt`; ao criar skill, formulário completo de AgentType (framework, execution_model, role, max_concurrent, pools) é preenchido inline; `agent_type_id = skill_id`; POST /v1/agent-types disparado após skill salvo.

### Configuração / Plataforma

- ✅ **NamespaceEditor reestruturado**: 8 namespaces → 5 ativos. Removidos: `sentiment` (UI dedicada), `dashboard` (migrado ao módulo Dashboards), `webchat` (migrado a Configuração/Canais). Renomeados labels: `consumer` → "Consumer Analytics", `masking` mantido como legado com label "Mascaramento (legado)" + novo `audit_policy`. Entrada `routing` com descrição atualizada (sem score_weights — agora por pool).
- ✅ **SentimentBandsEditor**: novo componente dedicado para edição de faixas de sentimento com níveis numéricos (1=pior, N=melhor). Validação de contiguidade e cobertura [-1.0, 1.0]. Barra visual de cor vermelho→verde. Suporte a 2–6 faixas configuráveis. Salva `sentiment.bands` no Config API sem texto (i18n resolve labels). Nova tab "💬 Sentimento" no ConfigPlataformaPage.
- ✅ **RoutingSkillsManager**: CRUD de competency skills (key + domain range) no namespace `routing`. Tab "🎯 Roteamento" no ConfigPlataformaPage.
- ✅ **ConfigPlataformaPage tabs**: ⚙️ Configuração | 💬 Sentimento | 📅 Calendários | 🎯 Roteamento.

### Configuração / Canais (novo módulo)

- ✅ **config-canais/index.tsx**: seção `/config/canais` com tabs por canal. WebChat ativo; WhatsApp/Voice/Email/SMS marcados "em breve".
- ✅ **WebChatConfigPage.tsx**: configuração do canal WebChat em 3 grupos — autenticação WS (`auth_timeout_s`), política de attachments (`attachment_expiry_days`, `upload_limits_mb` por MIME type), info sobre JWT secret. Lê/escreve namespace `webchat` no Config API. Admin token inline.
- ✅ **Rota + Sidebar**: `/config/canais` registrado em `routes.tsx`; item "📡 Canais" na sidebar entre Plataforma e Calendários. Chave i18n `nav.canais` em pt-BR e en.

---

## platform-ui — Sidebar / Navigation refactor (2026-05-06)

- ✅ **Fix eco no histórico de segmento (Atendimento/Contatos)**: `mcp-server-plughub/src/server.ts` — substituído `redis.xadd()` direto por `writeStreamEntry()` no handler `menu_submit` (respeita invariante arquitetural). O `xadd` direto não populava os campos flat `author_id`/`author_role` que `_parse_entry()` do analytics-api exige, causando eco/duplicação na exibição das conversas do segmento no histórico do contato. Adicionado `import { writeStreamEntry }` do `lib/write-stream-entry`. Verificado e funcionando na docker-demo.
- ✅ **Scripts linux**: permissões de execução corrigidas em `check-infra.sh`, `seed-demo.sh`, `set-env.sh`, `setup.sh`.

---

## Context-Aware / ContextStore

- ✅ **Fase 2 — Co-pilot**: `copilot_emitter.py` (AI Gateway) — `analyze_for_copilot()` fire-and-forget; endpoint `POST /v1/copilot/analyze`; `GET /copilot_state/:sessionId` no mcp-server-plughub; `useCopilotState` hook; `CapacidadesTab.tsx` com `CopilotSection`; 28/28 testes.
- ✅ **Fase 3 — Step `resolve` nativo**: Executor completo de 5 fases (gap check → CRM → LLM question → BLPOP → LLM extract); schemas em `@plughub/schemas`; 15 unit tests.

---

## Arc 5 — ContactSegment (v2)

- ✅ **Enrichment post-hoc de `segment_id`**: `SegmentEnricher` em `analytics-api/segment_enricher.py` — lookup chain LRU → Redis → ClickHouse FINAL; `_ENRICHED_TOPICS = {"sentiment.updated", "mcp.audit"}`; 27/27 testes em `test_segment_enricher.py`. Total analytics-api: 226/226.
- ✅ **Materialized views ClickHouse**: `mv_agent_performance_daily` (AggregatingMergeTree, POPULATE) + `v_agent_performance` — `GET /reports/agent-performance/daily`; `mv_segment_summary` + `v_segment_summary` — `GET /reports/sessions/complexity`. Pool scoping em ambos.

---

## Skill Deploy Lifecycle (Phase 2)

- ✅ **Deploy agendado**: `skill_scheduled_deploy_v1.yaml` com `reason: timer` suspend; MCP tool `skill_deploy` em `mcp-server-plughub`; `scheduled_at` em `PersistSuspendRequest`; `GET /v1/skills/:id/deployments/scheduled`; UI `AgentFlowDeployPage.tsx` com agendamento e cancelamento.
- ✅ **Graceful handoff monitor**: `GET /v1/skills/:id/handoff-status` — detecta deploy recente, consulta analytics-api por sessões ativas antes de `deployed_at`. UI com polling a cada 10s.
- ✅ **Automated rollback button**: botão "↩ Rollback" no histórico; two-step (PUT yaml_snapshot + POST re-deploy); `RollbackConfirmModal`; badge "rollback" em entradas originadas por rollback.

---

## Config API / Routing Engine

- ✅ **Consumo de `config.changed` namespace `routing`**: `RoutingConfigCache` em `routing_config.py` — fetch no startup, invalida/recarrega via `ConfigChangedHandler`. `performance_score_weight` dinâmico com fallback para env var. 15/15 testes em `test_routing_config.py`.

---

## Arc 8 — Frontend

- ✅ **platform-ui — AgentReportsPage**: `/config/agent-reports` (supervisor, admin) — duas sub-abas: "Disponibilidade" (pivot agent × data, células âmbar por intensidade) e "Pausas" (tabela flat de reason_breakdown + exportação CSV). `useAvailability` hook.
- ✅ **platform-ui — PauseReasonModal**: modal intercepta "Pausar"; busca motivos do Config API com fallback para DEFAULT_REASONS; `requires_note: true` exibe textarea obrigatória; chama best-effort `PUT /api/agent-pause`.

---

## Arc 2 — Fechamento

- ✅ E2E scenario 12: webchat auth flow + media upload end-to-end
- ✅ Usage Metering no Channel Gateway (voice_minutes, whatsapp_conversations, sms_segments)
- ✅ WebChat reconexão fase 2: stream TTL expirado + jwt_secret por tenant
- ✅ AttachmentStore fase 2: S3/MinIO
- ✅ Magic bytes validation no upload
- ✅ Pricing Module v1: planos, tarifas, ciclo de billing

---

## Arc 3 — Analytics, Dashboard Operacional e Relatórios

Todos os 12 itens implementados:

1. ✅ AI Gateway — `sentiment_emitter.py`: `emit_sentiment_updated` (Kafka) + `update_sentiment_live` (Redis). 41 assertions.
2. ✅ analytics-api — consumer + ClickHouse (6 tabelas ReplacingMergeTree, 8 tópicos, commit manual). 30 assertions.
3. ✅ analytics-api — dashboard endpoints: `GET /dashboard/operational` (SSE), `/metrics`, `/sentiment`. 18 assertions.
4. ✅ analytics-api — reports + BI export: sessions/agents/quality/usage com paginação e CSV. 26 assertions.
5. ✅ analytics-api — camada admin consolidada: `/admin/consolidated`, RBAC JWT, cross-tenant. 21 assertions.
6. ✅ operator-console fase 1 — heatmap + métricas realtime: SSE, PoolTile, MetricsPanel. 157 kB JS gzip 50 kB.
7. ✅ operator-console fase 2 — drill-down: sessions ativas → transcrição ao vivo (SSE + ClickHouse fallback). 61 assertions.
8. ✅ operator-console fase 3 — intervenção ativa: supervisor join/message/leave via REST. 173 kB JS gzip 54 kB.
9. ✅ Metabase setup: `docker-compose.infra.yml`, driver ClickHouse, Row Policies por tenant, 5 questions + dashboard.
10. ✅ Config Management Module: `packages/config-api/`, PostgreSQL, Redis cache 60s, 9 namespaces seedados. 27 assertions.
11. ✅ Timeseries endpoints: `/reports/timeseries/volume`, `/handle_time`, `/score`. Bucketing dinâmico. Pool scoping.
12. ✅ Dashboard drag-and-drop + templates: `DashboardsPage.tsx`, `TimeseriesChart`, react-grid-layout, Config API namespace `dashboards`.

---

## Arc 5 — ContactSegment v1

- ✅ Schemas em `@plughub/schemas/src/contact-segment.ts`: `ContactSegmentSchema`, `ConversationParticipantEventSchema`
- ✅ orchestrator-bridge: `_publish_participant_event` com `segment_id`, `sequence_index`, `parent_segment_id`, `outcome`
- ✅ analytics-api: tabelas `segments` + `session_timeline` (ClickHouse); endpoints `/reports/segments`, `/reports/agents/performance`, `/reports/agent-performance/daily`, `/reports/sessions/complexity`
- ✅ E2E scenario 23: Parts A/B/C — 11 assertions (`--segments` flag)

---

## AI Gateway — Multi-account Rotation

- ✅ `AccountSelector` em `account_selector.py`: Redis-backed, stateless, scoring por (rpm_used/rpm_limit × 0.7) + (tpm_used/tpm_limit × 0.3)
- ✅ Configuração multi-chave: `PLUGHUB_ANTHROPIC_KEYS` (vírgula) + `PLUGHUB_OPENAI_KEYS` como fallback
- ✅ `_call_with_fallback`: rotação automática em 429/529 → `mark_throttled` → próxima conta → cross-provider fallback
- ✅ Isolamento de workloads: profiles `realtime` (Sonnet → gpt-4o), `balanced` (Haiku → gpt-4o-mini), `evaluation` (Haiku isolado)
- ✅ `OpenAIProvider` com graceful degradation se SDK ausente; tool format conversion; role mapping
- ✅ Config API namespace `ai_gateway`: `account_rotation_enabled`, `throttle_retry_after_s`, `utilization_rpm_weight`, `evaluation_model`, `evaluation_max_tokens`
- ✅ 29 assertions em `test_account_selector.py`
- ✅ E2E scenario 26: throttle → fallback → recovery (`--fallback` flag)

---

## Arc 6 v2 — Permissões 2D + Workflow Motor

- ✅ `evaluation_permissions` substituído por ABAC (`module_config.evaluation.revisar` / `contestar`)
- ✅ `_check_abac_permission` em `router.py`; graceful degradation para tokens legacy
- ✅ Campos de workflow em `evaluation_results`: `workflow_instance_id`, `resume_token`, `action_required`, `current_round`, `deadline_at`, `lock_reason`
- ✅ Anti-replay de `round` em review/contestation endpoints
- ✅ Consumer `workflow.events` no evaluation-api
- ✅ `skill_revisao_simples_v1.yaml` e `skill_revisao_treplica_v1.yaml`
- ✅ MCP tool `evaluation_lock` (idempotente)
- ✅ E2E scenarios 27/28 (11 + 11 assertions)

---

## Calendar API — Fase 4: Múltiplos Intervalos de Tempo por Dia

- ✅ **Engine**: já suportava múltiplos slots desde a implementação inicial (estrutura `{day, open, slots: [{open, close}]}`); nenhuma mudança necessária no backend
- ✅ **5 novos testes de engine** (`test_engine.py` — total: 46/46):
  - `test_between_slots_returns_start_of_next_slot` — 12:30 durante pausa → retorna 13:00 mesmo dia
  - `test_within_first_slot_returns_current_time` — 10:00 dentro do primeiro slot → já aberto
  - `test_after_all_slots_returns_next_day_open` — 17:30 após último slot → 08:00 dia seguinte
  - `test_is_open_false_during_gap` — gap entre slots retorna `False`; início de slot retorna `True`
  - `test_crosses_gap_between_slots` — `add_business_duration` 11:00 + 3h cruza pausa (12–13) → 15:00
- ✅ **CalendarsPage.tsx** — corrigido bug de formato de dados (UI enviava `{day_of_week, start_time, end_time}` mas o engine lê `{day, open, slots: [{open, close}]}`); tipos `TimeInterval` + `WeeklyDaySchedule` agora corretos
- ✅ **WeeklyEditor** — reescrito para suportar N intervalos por dia: cada slot é uma linha; botão `× ` remove um intervalo; botão `+ intervalo` adiciona (máx 4); fallback para `08:00–18:00` ao remover último slot
- ✅ **`scheduleLabel`** — exibe `Segunda(2)` quando o dia tem múltiplos slots
