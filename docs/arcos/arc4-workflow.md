# Arc 4 — Workflow Automation

Permite que agentes nativos sejam usados como automação de processos com etapas manuais (aprovação, input, webhook, timer), sem BPM formal.

## Novos pacotes

- `packages/calendar-api/` — Python FastAPI, porta 3700. Engine puro de calendário.
- `packages/workflow-api/` — Python FastAPI, porta 3800. Ciclo de vida de WorkflowInstance.

## Novos schemas em `@plughub/schemas`

| Schema | Arquivo | Descrição |
|---|---|---|
| `SuspendStep` | `skill.ts` | Novo step type no FlowStepSchema |
| `WorkflowInstance` | `workflow.ts` | Registro persistido em PostgreSQL |
| `WorkflowTrigger`, `WorkflowResume` | `workflow.ts` | Requests de entrada |
| `WorkflowEvent` | `workflow.ts` | 7 eventos Kafka (started/suspended/resumed/completed/timed_out/failed/cancelled) |
| `HolidaySet`, `Calendar`, `CalendarAssociation` | `calendar.ts` | Hierarquia de calendários |
| `InstallationContext`, `ResourceScope` | `platform.ts` | Contexto de instalação |

## Calendar API — engine puro (no I/O)

| Função | Descrição |
|---|---|
| `is_open(associations, holidays, at)` | Verifica se uma entidade está aberta num instante |
| `next_open_slot(associations, holidays, after)` | Próxima janela aberta |
| `add_business_duration(associations, holidays, from_dt, hours)` | Deadline em horas úteis |
| `business_duration(associations, holidays, from_dt, to_dt)` | Horas úteis entre dois instantes |

Resolução de prioridade: exceptions > holidays > weekly_schedule.
Operadores: UNION (OR) + INTERSECTION (AND) por entidade.
Tests: `test_engine.py` — 41 assertions (25 engine + 16 novos para MM-DD holidays e status 3-state).

## Calendar API improvements

### Feriados recorrentes MM-DD

O campo `date` em `HolidaySchema` aceita tanto `YYYY-MM-DD` (feriado pontual) quanto `MM-DD` (recorrente todo ano). `_build_holidays_index` indexa pelo valor original; `_resolve_date` verifica `YYYY-MM-DD` primeiro e cai para `MM-DD` quando não há match exato. Engine e schemas atualizados.

### Status 3-state open/closed/holiday

`get_open_status()` retorna `"open" | "closed" | "holiday"` em vez de booleano. `_calendar_status()` e `_aggregate_status()` propagam `"holiday"` quando uma entidade está fechada por feriado. `is_open()` mantido como wrapper booleano (`@deprecated`). Endpoint `GET /v1/engine/is-open` retorna `{ status, open (deprecated), evaluated_at, entity_type, entity_id, calendars_count }`.

### Timezone por tenant

Tabela `calendar.tenant_config` com campo `default_timezone`. `CalendarCreate.timezone` é `Optional[str]` — `None` herda o default do tenant (fallback: `America/Sao_Paulo`). Validação IANA via `pytz.timezone()` no PATCH, antes de tocar o banco.

| Endpoint | Descrição |
|---|---|
| `GET /v1/tenant-config` | Lê timezone padrão do tenant |
| `PATCH /v1/tenant-config` | Atualiza timezone padrão |

Tests: `test_router.py` — 17 assertions (TestGetTenantConfig ×4, TestUpdateTenantConfig ×9, TestCreateCalendarTimezoneInheritance ×4). Total calendar-api: **58/58**.

## Calendar MCP tools — `mcp-server-plughub`

Quatro ferramentas MCP que envolvem os endpoints de engine do Calendar API, permitindo que agentes Skill Flow consultem horários de negócio via steps `invoke` sem acessar a REST API diretamente.

Arquivo: `packages/mcp-server-plughub/src/tools/calendar.ts`
Registradas em `server.ts` via `registerCalendarTools(server, calendarDeps)`.
Env var: `CALENDAR_API_URL` (default: `http://localhost:3700`).

| Ferramenta | Endpoint proxied | Descrição |
|---|---|---|
| `calendar_is_open` | `GET /v1/engine/is-open` | Status open/closed/holiday de uma entidade no instante `at` |
| `calendar_next_open_slot` | `GET /v1/engine/next-open-slot` | Próxima janela aberta após `after` |
| `calendar_add_business_duration` | `POST /v1/engine/add-business-duration` | Deadline = from_dt + N horas úteis |
| `calendar_business_duration` | `POST /v1/engine/business-duration` | Horas úteis entre from_dt e to_dt |

Todos os tools aceitam `tenant_id` opcional (fallback para o tenant do servidor) e retornam os mesmos payloads da API REST subjacente. Erros de rede ou HTTP não-2xx retornam `isError: true` com código `calendar_api_error` ou `network_error`.

## Skill Flow `suspend` step

```typescript
// Flow definition
{ type: "suspend", id: "aguardar_aprovacao",
  reason: "approval",       // approval | input | webhook | timer
  timeout_hours: 48,
  business_hours: true,     // uses calendar-api for deadline
  on_resume:  { next: "processar" },
  on_timeout: { next: "escalar" },
  on_reject:  { next: "notificar_rejeicao" },
  notify: { visibility: "agents_only", text: "Token: {{resume_token}}" }
}
```

Mecanismo de idempotência (dois estágios): sentinel `"suspending"` → `"suspended"` em pipeline_state.results. Crash entre os dois stages resulta em re-suspend seguro na retomada.

`SkillFlowEngineConfig.persistSuspend` — callback opcional injetado pelo workflow-api worker. Quando ausente, deadline é wall-clock.
`engine.run({ resumeContext: { decision, step_id, payload } })` — sinal de retomada passa direto para o suspend step.

Tests: `suspend.test.ts` — 13 assertions.

## @mention — mention_commands handler (skill-flow-engine)

`packages/skill-flow-engine/src/mention-commands.ts` — pure async handler for specialist agent @mention commands.

| Export | Description |
|---|---|
| `parseCommandName(args_raw)` | Extracts first whitespace-delimited token from args_raw; `null` for bare mention |
| `handleMentionCommand(skill, commandName, ctx)` | Dispatches command: `set_context` → ContextStore write (fire-and-forget, non-fatal), `trigger_step` → returns `trigger_step` field for caller, `terminate_self` → returns flag for caller |

`MentionCommandResult`: `{ handled, acknowledge, trigger_step?, terminate_self }` — caller is responsible for Redis LPUSH and agent_done; this function does no I/O besides ContextStore writes.

Unknown commands return `{ handled: false }` — silently ignored per spec.

Tests: `mention-commands.test.ts` — 15 assertions (parseCommandName ×5, handleMentionCommand ×10: unknown, set_context ack/no-ack, multiple fields, no contextStore, ContextStore throws, trigger_step, terminate_self, empty mention_commands).

## Masked Input — begin_transaction / end_transaction step tests

`packages/skill-flow-engine/src/__tests__/steps/transaction.test.ts` — 9 unit tests for `executeBeginTransaction` and `executeEndTransaction`:
- `begin_transaction` clears maskedScope, sets `transactionOnFailure`, returns `__transaction_begin__`
- `end_transaction` clears maskedScope + transactionOnFailure, uses `__transaction_end__` or explicit `on_success`
- `result_as` persists `{ status: "ok", fields_collected: [...] }` — field names only, never values

`packages/skill-flow-engine/src/__tests__/engine-transaction.test.ts` — 5 engine integration tests:
- Happy path: `begin_transaction` → `menu(masked)` → `invoke(@masked.*)` → `end_transaction(result_as)` → `complete`; masked value passed to invoke, `tx_result` persisted without sensitive content
- Failure: invoke fails inside block → engine rewinds to `begin_transaction.on_failure`, maskedScope cleared
- Menu timeout inside block → rewind to `on_failure`

Total skill-flow-engine: **101/101 tests** (11 test files).

## agent-registry — masked block validation

`packages/agent-registry/src/validators/skill.ts` — `validateMaskedBlock(flow: SkillFlow): string[]`

Position-based BFS: for each `begin_transaction` at array position N, seeds BFS from `steps[N+1]` (matching engine's positional advance via `__transaction_begin__`). Visits success edges only (`on_success`, `choice.conditions[].next`, `choice.default`, `suspend.on_resume.next`, `collect.on_response.next`). Stops at `end_transaction`. Reports error for any `reason` step found inside the block.

HTTP 422 returned by both POST and PUT `/v1/skills` routes:
```json
{ "error": "invalid_masked_block", "details": ["Step \"bad_reason\" (reason) is inside masked transaction block..."] }
```

Tests: `packages/agent-registry/src/__tests__/skill-validator.test.ts` — 14 unit tests covering: no begin_transaction, empty steps, clean block, reason before/after block, reason directly inside, reason via on_success chain, reason via choice branch/default, on_failure exit (not visited), multiple blocks, last-step begin_transaction (no crash), end_transaction stops propagation.

## Workflow API — ciclo de vida

Tabela PostgreSQL `workflow.instances` (schema `workflow`).

| Endpoint | Chamado por | O que faz |
|---|---|---|
| `POST /v1/workflow/trigger` | Sistema externo / operator | Cria WorkflowInstance, emite `workflow.started` |
| `POST /v1/workflow/instances/{id}/persist-suspend` | Skill Flow worker (TS) | Calcula deadline (calendar-api ou wall-clock), persiste suspensão, emite `workflow.suspended` |
| `POST /v1/workflow/resume` | Sistema externo / aprovador | Valida token, verifica expiração, registra decisão, emite `workflow.resumed` |
| `POST /v1/workflow/instances/{id}/complete` | Skill Flow worker | Marca completed, emite `workflow.completed` |
| `POST /v1/workflow/instances/{id}/fail` | Skill Flow worker | Marca failed, emite `workflow.failed` |
| `POST /v1/workflow/instances/{id}/cancel` | Operator Console | Cancela active/suspended, emite `workflow.cancelled` |
| `GET /v1/workflow/instances` | Operator Console | Lista com filtros (tenant_id, status, flow_id) |
| `GET /v1/workflow/instances/{id}` | Operator Console | Detalhe |

**Timeout scanner** — asyncio background task (intervalo configurável, padrão 60s). `UPDATE ... SET status='timed_out' WHERE status='suspended' AND resume_expires_at < now()` — atômico, sem double-processing.

Tests: `test_router.py` — 48 assertions (TestTrigger, TestPersistSuspend, TestResume, TestComplete, TestFail, TestCancel, TestList, TestDetail, TestHealth, TestTimeoutScanner, TestWebhookCRUD, TestWebhookTrigger, TestWebhookDeliveries).

## Webhook Trigger — authenticated public endpoints

Permite que sistemas externos (Salesforce, ERP, etc.) disparem workflows via URL pública autenticada por token, substituindo o trigger manual do operador.

### Token format

```
plughub_wh_<url-safe-43-chars>    (~258 bits de entropia)
```

Armazenamento: **SHA-256 hex digest** em `workflow.webhooks.token_hash` — plain token nunca é persistido.
`token_prefix` (16 primeiros chars) é armazenado para exibição no admin UI.
Comparação: `hmac.compare_digest` para proteção contra timing attacks.

### PostgreSQL schema

```sql
-- Webhooks registrados (um por flow/tenant)
CREATE TABLE workflow.webhooks (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id         TEXT        NOT NULL,
    flow_id           TEXT        NOT NULL,
    description       TEXT        NOT NULL DEFAULT '',
    token_hash        TEXT        NOT NULL UNIQUE,
    token_prefix      TEXT        NOT NULL,
    active            BOOL        NOT NULL DEFAULT TRUE,
    trigger_count     BIGINT      NOT NULL DEFAULT 0,
    last_triggered_at TIMESTAMPTZ,
    context_override  JSONB       NOT NULL DEFAULT '{}',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Log append-only de disparos (auditoria)
CREATE TABLE workflow.webhook_deliveries (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    webhook_id   UUID        NOT NULL REFERENCES workflow.webhooks(id) ON DELETE CASCADE,
    tenant_id    TEXT        NOT NULL,
    triggered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    status_code  INT         NOT NULL,
    payload_hash TEXT        NOT NULL,
    instance_id  UUID,
    error        TEXT,
    latency_ms   INT
);
```

### Endpoints

| Método | Rota | Auth | Descrição |
|--------|------|------|-----------|
| `POST`   | `/v1/workflow/webhooks`                         | `X-Admin-Token` | Cria webhook; retorna plain token (exibido uma vez) |
| `GET`    | `/v1/workflow/webhooks`                         | `X-Admin-Token` | Lista webhooks do tenant (filtros: `active`, `limit`, `offset`) |
| `GET`    | `/v1/workflow/webhooks/{id}`                    | `X-Admin-Token` | Detalhe do webhook |
| `PATCH`  | `/v1/workflow/webhooks/{id}`                    | `X-Admin-Token` | Atualiza `description`, `active`, `context_override` |
| `POST`   | `/v1/workflow/webhooks/{id}/rotate`             | `X-Admin-Token` | Rotaciona token (invalida o anterior; retorna novo plain token) |
| `DELETE` | `/v1/workflow/webhooks/{id}`                    | `X-Admin-Token` | Remove webhook e cascade-deletes deliveries |
| `GET`    | `/v1/workflow/webhooks/{id}/deliveries`         | `X-Admin-Token` | Últimos N registros de entrega (padrão 50, máx 200) |
| `POST`   | `/v1/workflow/webhook/{id}`                     | `X-Webhook-Token` (plain) | **Público** — dispara workflow |

### Trigger flow (POST /v1/workflow/webhook/{id})

```
1. Lê raw body → SHA-256 payload_hash
2. Autentica: SHA-256(X-Webhook-Token) → lookup DB por token_hash
3. verify_token(plain, stored_hash) — constant-time guard extra
4. Verifica active; se inativo → db_record_delivery(403) + 403
5. Merge: context = {**webhook.context_override, **body_json}
6. db_create_instance + emit_started (mesmo que trigger manual)
7. db_record_delivery(202, instance_id, latency_ms)
8. trigger_count++, last_triggered_at = now() (atômico, 2xx only)
9. Retorna 202 { instance_id, flow_id, webhook_id, status: "accepted" }
```

### Arquivos

| Arquivo | Responsabilidade |
|---------|-----------------|
| `webhooks.py` | `generate_token()`, `_hash_token()`, `verify_token()` — utilitários de token |
| `db.py` | DDL + CRUD: `db_create_webhook`, `db_get_webhook`, `db_get_webhook_by_token_hash`, `db_list_webhooks`, `db_update_webhook`, `db_rotate_webhook_token`, `db_delete_webhook`, `db_record_delivery`, `db_list_deliveries` |
| `router.py` | 7 endpoints admin + 1 endpoint público; `_require_admin` Dependency |
| `tests/test_router.py` | `TestWebhookCRUD` (11), `TestWebhookTrigger` (5), `TestWebhookDeliveries` (4) |

## Status transitions

## Kafka topic: workflow.events

Publicado pelo workflow-api em todos os status transitions. Consumido pelo Skill Flow worker para disparar `engine.run()` com `resumeContext`.

## Collect Step — async multi-channel data collection

Novo step type `collect` no Skill Flow. Permite que um workflow entre em contato com um alvo (customer/agent/external) via qualquer canal, apresenta uma interação estruturada, e suspende até receber resposta ou expirar o prazo.

```typescript
// Flow definition
{ type: "collect", id: "coletar_cpf",
  target:        { type: "customer", id: "{{customer_id}}" },
  channel:       "whatsapp",
  interaction:   "form",
  prompt:        "Por favor informe seu CPF",
  fields:        [{ id: "cpf", label: "CPF", type: "text" }],
  delay_hours:   0,            // envio imediato (ou scheduled_at para horário absoluto)
  timeout_hours: 24,
  business_hours: true,
  campaign_id:   "camp_cobranca_jan",
  output_as:     "cpf_response",
  on_response:   { next: "processar_cpf" },
  on_timeout:    { next: "escalar_sem_resposta" },
}
```

### Timing

| Parâmetro | Descrição |
|---|---|
| `scheduled_at` | ISO-8601 absoluto — quando contatar o alvo |
| `delay_hours` | Relativo: agora + N horas |
| (nenhum) | Envio imediato |
| `timeout_hours` | Quanto esperar pela resposta após o envio (business-hours-aware) |

### Correlação via collect_token

O Skill Flow gera um UUID (`collect_token`) e o workflow-api o persiste no `collect_instances`. O channel-gateway lê o token nos metadados da sessão outbound e publica `collect.responded` ao fechar a sessão → workflow-api resume o workflow com `decision: "input"`.

### Campaign = N instâncias com mesmo campaign_id

Não há entidade "campaign" separada. Um `campaign_id` é um agrupador livre em `workflow.instances` e `collect_instances`. A CampaignPanel do Operator Console agrega via `collect_events` no ClickHouse.

### Implementado

- `packages/schemas/src/skill.ts` — `CollectTargetSchema`, `CollectStepSchema` (inclui scheduled_at, delay_hours, timeout_hours, business_hours, campaign_id)
- `packages/schemas/src/workflow.ts` — `CollectStatusSchema`, `CollectRequestedSchema`, `CollectSentSchema`, `CollectRespondedSchema`, `CollectTimedOutSchema`, `CollectEventSchema`; `campaign_id` em `WorkflowInstanceSchema`
- `packages/skill-flow-engine/src/steps/collect.ts` — executor com idempotência de dois estágios, resume path (input/timeout), wall-clock fallback
- `packages/skill-flow-engine/src/executor.ts` — `persistCollect?` callback em `StepContext`, dispatch `case "collect"`
- `packages/workflow-api/src/plughub_workflow_api/db.py` — tabela `workflow.collect_instances` + funções CRUD; `campaign_id` em `workflow.instances`
- `packages/workflow-api/src/plughub_workflow_api/kafka_emitter.py` — `emit_collect_requested/sent/responded/timed_out` (topic `collect.events`)
- `packages/workflow-api/src/plughub_workflow_api/config.py` — `collect_topic: str = "collect.events"`
- `packages/workflow-api/src/plughub_workflow_api/router.py` — `POST /v1/workflow/instances/{id}/collect/persist`, `POST /v1/workflow/collect/respond`, `GET /v1/workflow/campaigns/{id}/collects`
- `packages/workflow-api/src/plughub_workflow_api/timeout_job.py` — scanner de collect_instances expiradas → collect.timed_out + resume with decision=timeout
- `packages/analytics-api/src/plughub_analytics_api/clickhouse.py` — tabelas `workflow_events` + `collect_events` (ReplacingMergeTree)
- `packages/analytics-api/src/plughub_analytics_api/models.py` — `parse_workflow_event`, `parse_collect_event`
- `packages/analytics-api/src/plughub_analytics_api/consumer.py` — topics `workflow.events` + `collect.events`
- `packages/analytics-api/src/plughub_analytics_api/reports_query.py` — `query_workflows_report`, `query_campaigns_report` (com summary agregado por campaign_id)
- `packages/analytics-api/src/plughub_analytics_api/reports.py` — `GET /reports/workflows`, `GET /reports/campaigns`
- `packages/operator-console/src/components/CampaignPanel.tsx` — painel de campanhas: summary cards com response rate, mini-bar de status, detail com KPIs + channel breakdown + collect event list
- `packages/operator-console/src/api/campaign-hooks.ts` — `useCampaignData` hook (poll 30s)
- `packages/operator-console/src/types/index.ts` — `CollectEvent`, `CampaignSummary`, `campaign_id` em `WorkflowInstance`
- `packages/operator-console/src/components/Header.tsx` — botão "Campaigns" na nav
- `packages/operator-console/src/App.tsx` — view `campaigns` + `CampaignPanel`

### Kafka topics

| Topic | Producer | Consumer(s) |
|---|---|---|
| `collect.events` | workflow-api (collect endpoints + timeout scanner) | analytics-api → ClickHouse collect_events |

## ContextStore integration — origin_session_id

Workflows lançados a partir de uma sessão ativa de cliente (via `task` step `mode: transfer`,
escalação, ou coleta outbound) devem ler e escrever no ContextStore da sessão originadora —
não no hash do workflow UUID.

### Regra

`{tenant}:ctx:{origin_session_id}` é o ContextStore key correto para @ctx.* em workflows.

### Campo `origin_session_id`

Adicionado a:
- `WorkflowInstanceSchema` (`@plughub/schemas/workflow.ts`) — campo nullable, documenta a sessão originadora
- `workflow.instances` (PostgreSQL) — coluna `origin_session_id TEXT` com migration idempotente
- `TriggerRequest` (workflow-api `router.py`) — campo opcional no body do trigger
- `WorkflowInstance` interface (`skill-flow-worker/workflow-client.ts`) — campo opcional

### Resolução no EngineRunner

```typescript
// origin_session_id presente → usa ContextStore da sessão real do cliente
// origin_session_id ausente  → usa instance.id (headless/standalone workflow)
const contextSessionId = instance.origin_session_id ?? instance.id

await engine.run({
  tenantId:  instance.tenant_id,
  sessionId: contextSessionId,   // ← chave do ContextStore ({tenant}:ctx:{contextSessionId})
  instanceId: instance.id,       // ← UUID do workflow para pipeline_state e lifecycle
  ...
})
```

### Como usar no trigger

```json
POST /v1/workflow/trigger
{
  "tenant_id":         "tenant_demo",
  "flow_id":           "fluxo_cobranca_v1",
  "trigger_type":      "event",
  "session_id":        "sess_abc123",
  "origin_session_id": "sess_abc123",
  "context": { "invoice_id": "INV-001", "amount": 15000 }
}
```

Quando o workflow executa steps `reason` com `context_tags.inputs`, os campos
`@ctx.caller.nome`, `@ctx.caller.cpf` etc. são lidos do ContextStore da sessão `sess_abc123` —
onde foram acumulados pelo `agente_contexto_ia_v1` durante o atendimento.

### Workflows standalone

`origin_session_id = null` → engine usa `{tenant}:ctx:{instance.id}` — hash isolado por workflow.

## Implementado neste módulo

- `packages/skill-flow-worker/` — TypeScript worker: consome `workflow.events`, roda engine.run() com resumeContext, wired com persistSuspend callback para deadline calculation
- Operator Console — painel de instâncias Workflow (WorkflowPanel.tsx): status filter, timeline, resume token, cancel action
- Operator Console — WebhookPanel (WebhookPanel.tsx): CRUD de webhooks, delivery log, one-time token display, activate/deactivate/rotate/delete
- Operator Console — RegistryPanel (RegistryPanel.tsx): Pools / Agent Types / Skills / Running instances CRUD via agent-registry REST
- Operator Console — SkillFlowEditor (SkillFlowEditor.tsx): Monaco YAML editor for SkillFlow definitions, live validation, JSON↔YAML conversion
- Operator Console — ChannelPanel (ChannelPanel.tsx): channel credential management for WhatsApp, Webchat, Voice, Email, SMS, Instagram, Telegram, WebRTC; credentials masked on read
- Operator Console — HumanAgentPanel (HumanAgentPanel.tsx): Live Status tab (human instances, operator actions) + Profiles tab (AgentType CRUD for human framework)
- agent-registry — GatewayConfig model + migration + `routes/channels.ts` CRUD (`GET/POST /v1/channels`, `GET/PUT/DELETE /v1/channels/:id`)
- agent-registry — `GET /v1/instances?framework=human`, `GET /v1/instances/:id` detail, `PATCH /v1/instances/:id` operator actions (pause/resume/force_logout)
- Vite proxy configuration para `/v1/workflow` routes
