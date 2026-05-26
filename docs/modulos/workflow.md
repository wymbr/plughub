# Módulo: Workflow

> Última atualização: 2026-05-25 · Estado: Arc 16

> Rota UI: `/workflow/*` | Roles: operator, supervisor, admin (operação); business (relatório)

## O que é

O módulo Workflow permite criar e monitorar automações de processos com etapas manuais (aprovação, coleta assíncrona, timers, webhooks). Diferente do AgentFlow (que roda em atendimentos em tempo real), o Workflow é orientado a processos batch ou semi-assíncronos: cobrança outbound, onboarding de produto, pesquisas pós-atendimento, aprovações de crédito.

## Abas / Rotas

| Rota | Arquivo | Descrição |
|---|---|---|
| `/workflow/editor` | `WorkflowEditorPage.tsx` | Editor YAML de flows (Monaco) |
| `/workflow/monitor` | `WorkflowMonitorPage.tsx` | Monitor de instâncias ativas com filtros de status |
| `/workflow/calendar` | `WorkflowCalendarPage.tsx` | Calendário de janelas de operação (calendar-api) |
| `/workflow/report` | `WorkflowReportPage.tsx` | Relatório analítico de execuções (analytics-api) |

Redirects legados oficiais: `/workflows` → `/workflow/monitor`, `/skill-flows` → `/agent-flow/editor`, `/reports` → `/contacts?tab=analise`.

## Gate ABAC

| Campo | Efeito |
|---|---|
| `workflows.operacao` | Exibe Editor, Monitor e Calendar |
| `workflows.visualizar` | Exibe Report |
| `workflows.cancelar` | Habilita botão "Cancelar instância" no Monitor |
| `workflows.webhooks` | Exibe painel de gestão de webhooks |

## Editor (WorkflowEditorPage)

Monaco YAML editor para definições de Skill Flow. Mesma base do AgentFlow Editor mas filtrado para skills de workflow (tipo: `horizontal`). Converte JSON ↔ YAML automaticamente. Salva via `PUT /v1/skills/:id`. Validação YAML live; erros de schema retornam HTTP 422 da agent-registry.

## Monitor (WorkflowMonitorPage)

Tabela de `WorkflowInstance` com filtros por status (`active / suspended / completed / timed_out / failed / cancelled`), `flow_id`, e período. Para cada instância:
- Timeline de eventos (started → suspended → resumed → completed)
- `resume_token` visível (para aprovadores externos)
- Botão "Cancelar" (requer `workflows.cancelar`)

**Fonte**: `GET /v1/workflow/instances?tenant_id=&status=&flow_id=` (workflow-api porta 3800).

Timeout scanner automático (60 s) marca `timed_out` instâncias suspensas com `resume_expires_at < now()`.

## Calendar (WorkflowCalendarPage)

Visualização e edição de calendários de janelas de operação. Usado pelo step `suspend` e `collect` com `business_hours: true` para calcular deadlines em horas úteis.

**Componentes**:
- `HolidaySet` — conjuntos de feriados (nacional, estadual, municipal)
- `Calendar` — agenda semanal + exceções + holidays vinculados
- `CalendarAssociation` — associa um Calendar a um pool ou flow

**Fonte**: calendar-api (porta 3700). Engine puro — sem I/O nos cálculos de deadline.

## Report (WorkflowReportPage)

Relatório analítico agregado de instâncias: volume por flow_id, taxa de conclusão vs. timeout, tempo médio por etapa, distribuição de outcomes. Gráficos de timeseries com interval picker.

**Fonte**: `GET /reports/workflows` + `GET /reports/campaigns` (analytics-api → ClickHouse tabelas `workflow_events` + `collect_events`).

## Step types relevantes para Workflow

| Step | O que faz |
|---|---|
| `suspend` | Suspende workflow até sinal externo (aprovação, input, webhook, timer). Suporta `business_hours`, `timeout_hours`, `on_resume`, `on_timeout`, `on_reject` |
| `collect` | Contata target via canal, apresenta interação estruturada, suspende até resposta ou expiração. No Arc 16 aceita `requires: [text\|audio\|video\|file_upload\|masked_input\|rich_menu]` em vez de `channel` explícito — o Channel Gateway escolhe o canal outbound pela matriz de capacidades + contexto de Journey |
| `notify` | **Depreciado (Arc 16)** — usar `invoke: notification_send`. O sub-campo `notify` dentro de `suspend` permanece válido |
| `invoke` | Chama MCP tool (CRM, ERP, billing) |
| `reason` | Chama AI Gateway para decisão inteligente |
| `choice` | Branching condicional sobre pipeline_state ou ContextStore |

## Webhooks

Sistemas externos (Salesforce, ERP) podem disparar flows via URL autenticada por token. Gerenciado pelo painel de Webhooks (`workflows.webhooks`).

| Endpoint | Descrição |
|---|---|
| `POST /v1/workflow/webhooks` | Cria webhook (token exibido uma vez) |
| `POST /v1/workflow/webhook/{id}` | Dispara flow (público, auth via `X-Webhook-Token`) |
| `GET /v1/workflow/webhooks/{id}/deliveries` | Log de entregas |

Token format: `plughub_wh_<url-safe-43-chars>` (~258 bits de entropia). Armazenado como SHA-256 no banco.

## Integração com Journey / Processos (Arc 10/16)

O `workflow-api` também expõe a API pública de Journey — a unidade de serviço que agrupa múltiplas sessões de um mesmo processo:

- `GET /v1/journeys` (filtros: `status`, `skill_id`, `customer_id`, `pool_id`), `POST /v1/journeys/{id}/resume` (encapsula o `resume_token` interno), `POST /v1/journeys/from-instance/{id}` — viabiliza o padrão Tier 1 poller (Arc 16).
- Tópico Kafka `journey.events` (9 tipos) — consumido pelo analytics-api → ClickHouse `journey_events`.
- `origin_session_id` na `WorkflowInstance` liga o workflow à sessão de contato parent; sessões `collect` recebem `journey_id`.

As Journeys são monitoradas no módulo Processos (`/agent-flow/processos`). Ver `docs/arcos/arc10-journey.md` e `docs/arcos/arc16-flow-orchestration.md`.

## Collect Step — campanhas outbound

Um `campaign_id` é um agrupador livre em instâncias de workflow. N instâncias com o mesmo `campaign_id` formam uma campanha. O WorkflowReportPage agrega por `campaign_id` mostrando:
- Taxa de resposta (respondidos / total)
- Breakdown por canal e status
- Timeline de collect events

## Casos de uso típicos

- **Cobrança outbound**: contatar lista via WhatsApp, coletar promessa de pagamento, acionar sistema de cobrança
- **Onboarding**: coletar documentos, aguardar aprovação de crédito, notificar resultado
- **NPS pós-atendimento**: pesquisa automática após encerramento de sessão humana
- **Aprovação de contratos**: aguardar assinatura eletrônica, confirmar execução

## Pacotes envolvidos

| Pacote | Responsabilidade |
|---|---|
| `workflow-api` | Ciclo de vida de WorkflowInstance, persist-suspend, resume, webhooks, collect instances (porta 3800) |
| `calendar-api` | Engine de calendário e horários úteis (porta 3700) |
| `skill-flow-worker` | Kafka consumer de `workflow.events` → engine.run() com resumeContext |
| `skill-flow-engine` | Interpretador do YAML — steps suspend, collect, invoke, reason |
| `analytics-api` | Consumer `workflow.events` + `collect.events` → ClickHouse; endpoints `/reports/workflows`, `/reports/campaigns` |
| `platform-ui` | `modules/workflows/` — 4 páginas (Editor, Monitor, Calendar, Report) |

## Kafka topics

| Tópico | Produtor | Consumidor |
|---|---|---|
| `workflow.events` | workflow-api (7 tipos de evento) | skill-flow-worker, analytics-api |
| `collect.events` | workflow-api (collect endpoints + timeout scanner) | analytics-api → ClickHouse |

## Referências

- Schemas: `packages/schemas/src/workflow.ts`, `packages/schemas/src/skill.ts` (CollectStep, SuspendStep)
- Backend: `packages/workflow-api/`, `packages/calendar-api/`, `packages/skill-flow-worker/`
- Frontend: `packages/platform-ui/src/modules/workflows/`
