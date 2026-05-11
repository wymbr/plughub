# TODO — PlugHub Itens Pendentes

> Itens genuinamente não implementados. Histórico de implementações concluídas em `CHANGELOG.md`.

---

## platform-ui — Próximas features planejadas

### #30 — ✅ Contacts & Nav Restructure — implementado (2026-05-08)
### Backend-dependent Pages — ✅ implementado (2026-05-09)

Ver CHANGELOG 2026-05-09.

**Pendências futuras decorrentes do #30 — concluídas.**

---

### #31 / #33 — ✅ Flow/Deploy: modelo pool-centric 3 slots — implementado (2026-05-08)

Ver CHANGELOG 2026-05-08 (Tasks #31 e #33).

**Pendências futuras decorrentes:**
- `skill_scheduled_deploy_v1` precisa existir como skill no registry para o agendamento funcionar

---

## Dashboard #35 — ✅ Sistema de Cards Genéricos — implementado (2026-05-09)

Ver CHANGELOG 2026-05-09 (Parts 1–4). Spec completa em `docs/modules/dashboard.md`.

---

## Usage Metering — Channel Gateway adapters

- **whatsapp_conversations, voice_minutes, sms_segments, email_messages** *(deferred)*: funções em `usage_emitter.py` implementadas, mas os adapters de canal ainda não as chamam. Será wired quando cada adapter for criado (WhatsApp, WebRTC/Voice, SMS, Email).

---

## Pricing Module

- **Integração metering × pricing** *(deferred)*: módulo que lê contadores de `usage.events` no Redis/ClickHouse, aplica planos configurados no Config API e escreve `{tenant}:quota:limit:*` no Redis. Metering registra mas pricing não consome.

---

## Channel Endpoints — channel-gateway integration (pending)

Layers 1 (agent-registry), 3 (platform-ui), 4 (schemas) ✅ complete — see CHANGELOG 2026-05-07.

**Layer 2 — channel-gateway** *(deferred)*: Replace hardcoded pool lookup with HTTP call to agent-registry `GET /v1/channel-endpoints?channel={ch}&identifier={id}`. Cache with short TTL (~30s) to avoid hot-path latency. Also run `prisma migrate dev --name add_channel_endpoint` in agent-registry when network is available.

---

## Arc 9 — Agent Groups & Supervisor Scope *(implementado — 2026-05-11)*

Implementação completa: auth-api (tabelas + CRUD REST + shift resolution + JWT claims), analytics-api (5 report endpoints filtrados), platform-ui (Config/Groups page + Monitor/Console scope filtering via `supervised_agent_types` e `accessiblePools`). Ver CHANGELOG.

**Spec completa:** [`docs/modules/arc9-agent-groups.md`](docs/modules/arc9-agent-groups.md)

---

## Masking — Channel-Aware Display Architecture

**Bloco 1 (concluído 2026-05-11)**: correções de segurança — logs de `menu_submit` redactados, fallback Kafka redacta `input_snapshot`/`output_snapshot`, `masked_input_fields` populado no `AuditRecord`. Ver CHANGELOG.

**Bloco 2 (concluído 2026-05-11)**: `MaskedToken` component + `renderWithTokens` + `useMaskingDisplayRules` + migração `SessionTranscript` (remove `maskSensitiveContent`) + `MaskingPage` seção 5 (display rules por categoria × canal). Ver CHANGELOG.

**Bloco 3 — Channel Gateway TTS** *(deferred até implementação de voz)*: quando qualquer adapter de voz/TTS for criado, deve consultar `rule.{category}.display_voice` no namespace `masking` do Config API antes de passar texto ao sintetizador. Comportamentos: `silence` (pula o valor), `beep` (tom de beep), `speak_placeholder` (fala "valor mascarado"). Aplica-se a qualquer canal que gere áudio — não só voice/webrtc. Não implementar antes de definir qual engine TTS será usada.

---

## Audit Profile — LGPD Compliance Role

Perfil dedicado ao DPO/Compliance para auditoria de dados pessoais. Decisão técnica: implementar como módulo ABAC `audit` separado (não uma role fixa) para manter ortogonalidade com `operator`/`supervisor`/`admin`.

**Escopo do módulo ABAC `audit`:**
- `sessions` — leitura de `original_content` (dados desmascarados) via endpoint separado com log de acesso próprio
- `mcp_calls` — leitura de `input_snapshot`/`output_snapshot` com `masked_input_fields` no `mcp.audit` ClickHouse
- `user_access` — leitura de logs de autenticação e refresh token rotation
- `data_requests` — CRUD de SARs (Subject Access Requests) e erasure requests
- `config_snapshot` — leitura somente de configurações ativas (masking rules, retention policies)

**Para implementar:**
1. Adicionar `audit` ao `infra/modules.yaml` e ao `PermissionChecker`
2. Endpoint `GET /v1/audit/sessions/{id}/original-content` (auth-api ou novo audit-api) com log de acesso obrigatório
3. Endpoint `GET /v1/audit/mcp-calls` consumindo ClickHouse `mcp_audit` com filtro por `masked_input_fields IS NOT NULL`
4. Pipeline SAR/erasure: pseudonimização em `sessions_stream` + anonimização em analytics ClickHouse
5. Platform-UI: página `AuditPage` em novo grupo de nav (somente role com módulo `audit`)

---

## Arc 10 — Journey: Multi-Session Service Automation

Spec completa em [`docs/modules/arc10-journey.md`](docs/modules/arc10-journey.md).

**Fase A — Backend foundation** *(concluída 2026-05-11)*
- `@plughub/schemas`: `JourneyStatusSchema`, `JourneySchema`, `JourneyEventTypeSchema`, `JourneyStartInputSchema`, `JourneyMergeInputSchema` — ver CHANGELOG
- `workflow-api`: tabela `workflow.journeys` + CRUD + `journey_router.py` (5 endpoints) + kafka emitters
- `mcp-server-plughub`: `tools/journey.ts` — `journey_start`, `journey_link_session`, `journey_merge`
- `analytics-api`: `parse_journey_event()` + `_DDL_JOURNEY_EVENTS` + `insert_journey_event()` + tópico `journey.events` no consumer

**Fase B — Vinculação automática via collect** *(concluída 2026-05-11)*
- `workflow-api/db.py`: migration `collect_instances.journey_id`; `_row_to_instance`/`_row_to_collect` expõem `journey_id`; `db_create_collect()` aceita `journey_id`; `db_create_journey_for_instance()` (idempotente, transacional)
- `workflow-api/config.py`: `journey_topic` adicionado a `Settings`
- `workflow-api/kafka_emitter.py`: `emit_collect_requested()` propaga `journey_id`
- `workflow-api/router.py`: `persist_collect` threads `journey_id`; `respond_collect` emite `journey_session_linked` quando collect tem `journey_id` + `body.session_id`
- `workflow-api/journey_router.py`: endpoint `POST /v1/journeys/from-instance/{instance_id}` (creates_journey:true)
- `skill-flow-worker/workflow-client.ts`: `journey_id` em `WorkflowInstance`; `createJourneyForInstance()` method
- `skill-flow-worker/engine-runner.ts`: verifica `creates_journey:true` antes de `engine.run()`; cria journey automaticamente (falha não-fatal)

**Fase C — Monitor (ProcessosPage)** *(concluída 2026-05-11)*
- `analytics-api/reports.py`: `GET /reports/journeys` (filtra por skill_id, status, customer_id; retorna data + kpis + meta)
- `platform-ui/hooks.ts`: `Journey`/`JourneyKpi` types + `useJourneys()` + `useJourney()`
- `ProcessosPage.tsx`: dois tabs — **Jornadas** (KPI strip + lista + detalhe) e **Instâncias** (view existente preservada)

**Fase D — Console (AgentAssistPage)** *(concluída 2026-05-11)*
- HistoricoTab: seção "Processos em aberto" para o customer_id atual (apenas active/suspended)
- ActionBar: botão "Iniciar Processo" dropdown filtrado por `pool.mentionable_journeys`; chama `POST /v1/journeys`
- `@mention` protocol: extensão `@journey:<skill_id>` — sem UI especial, agentes humanos digitam no campo de texto
- Botão "Unir jornadas" no painel de detalhes do tab Jornadas em ProcessosPage; chama `POST /v1/journeys/merge`

**Fase D.5 — Refinamentos de spec** *(concluída 2026-05-11)*

*Três decisões de design acordadas e implementadas em 2026-05-11:*

**1. Enriquecimento do evento `journey_session_linked`**
Campos adicionais a incluir no schema (`@plughub/schemas/src/journey.ts`) e no payload emitido em `workflow-api/kafka_emitter.py`:
- `current_step?: string` — passo do workflow no momento do vínculo
- `session_outcome?: string` — outcome da sessão neste contato (`resolved/escalated/abandoned/…`)
- `session_started_at?: string` — ISO timestamp de abertura da sessão
- `session_ended_at?: string` — ISO timestamp de encerramento
Isso cria linha do tempo auditável da evolução do workflow através dos contatos. ClickHouse `journey_events` armazena automaticamente; `GET /reports/journeys` pode expor a progressão por `journey_id`.

**2. SLA herdado do pool — sem campo novo**
Journey não tem `sla_target_ms` próprio. A cadeia é: `journey.origin_session_id → session.pool_id → pool.sla_target_ms`. Cumprimento medido como `(last_event_at − created_at) vs sla_target_ms`. Nenhuma mudança de schema.

**3. Interseção de sessões entre journeys independentes**
Uma sessão pode ser `origin_session_id` de **múltiplas journeys independentes** — seja o primeiro contato, seja qualquer sessão no meio de uma journey em andamento. Regra de comportamento:
- `journey_start(skill_id, session_id)` seta **apenas** `journey.origin_session_id = session_id`; nunca toca `sessions.journey_id` da sessão passada
- `sessions.journey_id` é setado **somente** em sessões criadas por `collect` (sessões que pertencem exclusivamente a uma journey)
- Uma sessão pode simultaneamente ter `sessions.journey_id = Journey_A` (collect) e ser `origin_session_id` de `Journey_B` (novo serviço iniciado neste contato)
- As journeys não têm relação entre si; a interseção é capturada apenas pelo `origin_session_id` comum
- Query "todas sessões desta journey": `sessions WHERE journey_id = ? UNION session WHERE session_id = journey.origin_session_id`

Exemplo canônico:
```
S1 → origin de Journey A (produto X)
S2 → collect de Journey A (journey_id=A)
S3 → collect de Journey A (journey_id=A) + origin de Journey B (produto Y, iniciado neste contato)
S4 → collect de Journey B (journey_id=B)
```

**Fase E — Dashboard cards de jornada** *(não implementado)*

Usar o sistema de cards genéricos existente (Dashboard #35). KPI data já disponível via `GET /reports/journeys`.

Cards a registrar no display tool registry:
- `journey_active_count` — contagem de journeys ativas (filtráveis por skill_id)
- `journey_resolution_rate` — taxa de resolução por skill_id (gauge ou trend)
- `journey_funnel` — distribuição por status (`active/suspended/completed/failed`) como barras
- `journey_median_duration` — duração mediana (p50) por skill_id

Cada card recebe parâmetros de filtro compatíveis com o `GlobalFilters` existente (`tenant_id`, `from_dt`, `to_dt`, `skill_id`).

**Fase F — Split de jornadas** *(fase futura — decisões em aberto)*
- MCP tool `journey_split(journey_id, session_ids[])` — extrai sessões para nova journey
- Decisões antes de implementar: destino do `workflow_instance_id`; nova journey recebe workflow ou inicia sem; restrição sobre `origin_session_id` da journey original

---

## CLAUDE.md — Otimização (Fase 3)

**Fase 3**: Revisão final para confirmar CLAUDE.md ≤ 800 linhas. Mover seções remanescentes se necessário. Fase 2 concluída — ver CHANGELOG 2026-05-09.
