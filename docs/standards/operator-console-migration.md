# Plano de Migração: operator-console → platform-ui

> Consolidação de painéis legados no shell unificado
> Status: Em execução (Fase 1 iniciada)
> Última atualização: 2026-04-28

---

## Contexto e Motivação

### Problema

O `operator-console` é um app React standalone criado antes do `platform-ui`. Hoje temos:

- **operator-console** (`packages/operator-console/`): 12 painéis independentes
  - Heatmap, Sessions, Transcript, Workflows, Campaigns, Config, Pricing, Webhooks, Registry, Skills, Channels, Agents
  - Design system próprio (não integrado)
  - Autenticação separada
  - APIs duplicadas/parcialmente duplicadas
  - Sem i18n

- **platform-ui** (`packages/platform-ui/`): Shell novo e único
  - Design system centralizado
  - Autenticação unificada
  - i18n (PT-BR + EN)
  - Estrutura de módulos organizada
  - React Router v6

### Visão

Consolidar todos os 12 painéis do operator-console no platform-ui, desligando o app legado.

---

## Mapeamento de Painéis

| Painel operator-console | Rota platform-ui | Módulo | Backend endpoints | Status |
|---|---|---|---|---|
| **Heatmap** (pool sentiment live) | `/monitor/heatmap` | `atendimento` | `GET /api/pool/snapshots`, `GET /api/pool/{id}/sentiment` | ✅ Parcial* |
| **Sessions** (ativas + histórico) | `/monitor/sessions` | `atendimento` | `GET /analytics/sessions/active`, `GET /analytics/sessions/{id}/stream` | ⚙️ Em progresso |
| **Transcript** (ao vivo) | `/monitor/sessions/{id}` | `atendimento` | `GET /analytics/sessions/{id}/stream` (SSE) | ⚙️ Em progresso |
| **Workflows** (instâncias) | `/workflows` | `workflows` | `GET /workflow/v1/instances`, `POST /workflow/v1/trigger` | ✅ Implementado |
| **Campaigns** (coletas) | `/workflows/campaigns` | `workflows` | `GET /analytics/campaigns`, `GET /reports/campaigns` | ✅ Implementado |
| **Config** (namespaces globais) | `/config/platform` | `config-plataforma` | `GET /config/v1/*`, `PUT /config/v1/*` | ✅ Implementado |
| **Pricing** (faturamento) | `/config/billing` | `config-plataforma` | `GET /pricing/v1/invoice`, `POST /pricing/v1/resources` | ✅ Implementado |
| **Webhooks** (triggers) | `/config/webhooks` | `config-plataforma` | `GET /workflow/v1/webhooks`, `POST /workflow/v1/webhooks` | ✅ Implementado |
| **Registry** (pools, types, instances) | `/config/recursos` | `config-recursos` | `GET /v1/pools`, `GET /v1/agent-types`, `GET /v1/instances` | ✅ Implementado |
| **Skills** (YAML editor) | `/config/recursos/skills` | `config-recursos` | `GET /v1/skills`, `PUT /v1/skills/{id}` | ✅ Implementado |
| **Channels** (credenciais) | `/config/recursos/channels` | `config-recursos` | `GET /v1/channels`, `POST /v1/channels` | ✅ Implementado |
| **Agents** (humanos + profiles) | `/config/recursos/agents` | `config-recursos` | `GET /v1/instances?framework=human`, `PATCH /v1/instances/{id}` | ✅ Implementado |

*Heatmap tem UI básica em MonitorPage, mas sem polling de sentiment ao vivo.

---

## Status por Fase

### Fase 1: Monitor + Registry + Skills (Pronto para deprecação)

**Timeline:** 2026-04-15 → 2026-04-30

**Painéis:** Heatmap, Sessions, Transcript, Registry, Skills, Channels, Agents

| Componente | operator-console | platform-ui | Status | Notas |
|---|---|---|---|---|
| Heatmap | PoolHeatmapComponent | MonitorPage (PoolHeatmap) | ✅ | Sem polling, versão estática em desenvolvimento |
| Sessions | SessionListComponent | MonitorPage (SessionList) | ✅ | Integrado, polling a cada 10s |
| Transcript | TranscriptComponent | SessionTranscriptView | ✅ | SSE funcional, original_content masked |
| Registry Pools | PoolsTab | ConfigRecursosIndex (PoolsTab) | ✅ | CRUD completo |
| Registry Agent Types | AgentTypesTab | ConfigRecursosIndex (AgentTypesTab) | ✅ | CRUD com traffic_weight |
| Registry Instances | InstancesTab | ConfigRecursosIndex (InstancesTab) | ✅ | Read-only + filter |
| Skills Editor | SkillFlowEditor | ConfigRecursosIndex (SkillsTab) | ✅ | Monaco + YAML/JSON |
| Channels | ChannelPanel | ConfigRecursosIndex (ChannelsTab) | ✅ | CRUD com masking de credenciais |
| Agents (human) | HumanAgentPanel | ConfigRecursosIndex (AgentsTab) | ✅ | Profiles + Live Status |

**Critério de encerramento Fase 1:**
- [ ] Heatmap com polling de sentiment ao vivo (monitor.ts hook)
- [ ] Transcrição ao vivo testada end-to-end em WebSocket
- [ ] CRUD de todos os recursos funcionais (pode-se deletar original em operator-console)
- [ ] i18n completo (PT-BR + EN) em todos os componentes
- [ ] Testes manuais passando em 3 cenários cada

### Fase 2: Workflows + Campaigns + Config Advanced (Em implementação)

**Timeline:** 2026-05-01 → 2026-05-30

**Painéis:** Workflows, Campaigns, Config, Webhooks

| Componente | operator-console | platform-ui | Status | Notas |
|---|---|---|---|---|
| Workflows List | WorkflowPanel | WorkflowsPage | ✅ | Tabela com filtro por status |
| Campaigns | CampaignPanel | WorkflowsPage (CampaignsTab) | ✅ | Summary cards + detail |
| Config Namespaces | ConfigPanel (tabs) | ConfigPlataformaPage (ConfigNamespaceTabs) | ✅ | Admin token local |
| Webhooks CRUD | WebhookPanel | ConfigPlataformaPage (WebhooksTab) | ✅ | One-time token display |
| Webhook Deliveries | WebhookPanel (deliveries) | ConfigPlataformaPage (DeliveryLog) | ⚙️ | Pendente |

**Critério de encerramento Fase 2:**
- [ ] Workflows e Campaigns com filtros + busca
- [ ] Config namespaces editáveis em UI (JSON editor inline)
- [ ] Webhooks com token rotation + log de entregas
- [ ] i18n e design tokens aplicados
- [ ] TypeScript sem `any` warnings

### Fase 3: Pricing + Full Migration (Planejada)

**Timeline:** 2026-06-01 → 2026-07-15

**Painéis:** Pricing, Agent Assist (novo)

| Componente | operator-console | platform-ui | Status | Notas |
|---|---|---|---|---|
| Pricing Invoice | PricingPanel | ConfigPlataformaPage (PricingTab) | ✅ | XLSX export |
| Pricing Resources | PricingPanel (resources) | ConfigPlataformaPage (PricingTab) | ✅ | Upsert + delete |
| Pricing Reserve Pools | PricingPanel (reserve) | ConfigPlataformaPage (PricingTab) | ✅ | Activate/deactivate |
| Agent Assist UI | agent-assist-ui app | `/agent-assist` (new module) | 📋 | Futura integração como módulo |

---

## Dependências de Backend por Fase

### Fase 1 (pronta)

```
analytics-api:
  GET /sessions/active
  GET /sessions/{id}/stream (SSE)
  GET /sessions/customer/{id}
  GET /reports/participation

agent-registry:
  GET /v1/pools
  POST /v1/pools
  PUT /v1/pools/{id}
  GET /v1/agent-types
  POST /v1/agent-types
  PUT /v1/agent-types/{id}
  PATCH /v1/agent-types/{id} (traffic_weight)
  GET /v1/instances?framework=human
  GET /v1/instances/{id}
  PATCH /v1/instances/{id} (action: pause|resume|force_logout)
  GET /v1/skills
  PUT /v1/skills/{id}
  DELETE /v1/skills/{id}
  GET /v1/channels
  POST /v1/channels
  PUT /v1/channels/{id}
  DELETE /v1/channels/{id}
```

**Status:** ✅ Todos endpoints existentes

### Fase 2 (em implementação)

```
workflow-api:
  GET /v1/workflow/instances
  POST /v1/workflow/trigger
  GET /v1/workflow/instances/{id}
  POST /v1/workflow/instances/{id}/cancel
  GET /v1/workflow/webhooks
  POST /v1/workflow/webhooks
  PUT /v1/workflow/webhooks/{id}
  PATCH /v1/workflow/webhooks/{id}
  DELETE /v1/workflow/webhooks/{id}
  POST /v1/workflow/webhooks/{id}/rotate
  GET /v1/workflow/webhooks/{id}/deliveries

config-api:
  GET /v1/{namespace}
  GET /v1/{namespace}/{key}
  PUT /v1/{namespace}/{key}
  DELETE /v1/{namespace}/{key}

analytics-api:
  GET /reports/campaigns
  GET /reports/campaigns/{id}/collects
```

**Status:** ⚙️ Endpoints existentes, integração ao platform-ui em andamento

### Fase 3 (planejada)

```
pricing-api:
  GET /v1/pricing/invoice/{tenant_id}
  GET /v1/pricing/invoice/{tenant_id}?format=xlsx
  GET /v1/pricing/resources/{tenant_id}
  POST /v1/pricing/resources/{tenant_id}
  DELETE /v1/pricing/resources/{tenant_id}/{resource_id}
  POST /v1/pricing/reserve/{tenant_id}/{pool_id}/activate
  POST /v1/pricing/reserve/{tenant_id}/{pool_id}/deactivate
  GET /v1/pricing/reserve/{tenant_id}/activity
```

**Status:** ✅ Todos endpoints existentes no pricing-api (Arc 2)

---

## Critério de Pronto por Painel

### Heatmap

- [ ] `PoolHeatmap.tsx` com polling 5s (não estático)
- [ ] Cores interpoladas por avg_score (worst-first ordering)
- [ ] Clique em tile → drill-down para SessionList
- [ ] Título, timestamp da última atualização
- [ ] i18n: `modules.atendimento.heatmap.*`

### Sessions

- [ ] Lista com filtro por status (all, queued, active, closed)
- [ ] Coluna de pool_id, channel, closed_at
- [ ] Clique em linha → drill-down para Transcript
- [ ] Polling 10s (ou manual refresh button)
- [ ] i18n: `modules.atendimento.sessions.*`

### Transcript

- [ ] XREAD bloqueante via SSE (stream ao vivo)
- [ ] Mensagens com visibility badges (`agents_only` = amber)
- [ ] Menu interactions renderizadas (read-only)
- [ ] Supervisor panel (optional: join como supervisor)
- [ ] i18n: `modules.atendimento.transcript.*`

### Registry (Pools, Agent Types, Instances)

- [ ] CRUD completo para Pools (create, read, update, soft-delete)
- [ ] CRUD para Agent Types com traffic_weight/canary deployment
- [ ] Instances read-only com filtros (pool_id, status)
- [ ] Validações no form (pool_id existe, agente_type_id válido, etc.)
- [ ] Success/error toasts
- [ ] i18n: `modules.config_recursos.pools.*`, `modules.config_recursos.agent_types.*`, etc.

### Skills Editor

- [ ] Monaco YAML editor com validação live
- [ ] Lista de skills no sidebar (busca + filtro)
- [ ] Botão Save (PUT /v1/skills/:id)
- [ ] Botão Delete com confirmação
- [ ] JSON ↔ YAML conversion (js-yaml.dump/load)
- [ ] i18n: `modules.config_recursos.skills.*`

### Channels

- [ ] CRUD para credentials (create, read, update, delete)
- [ ] Form gerador por tipo de canal (WhatsApp, Webchat, Voice, etc.)
- [ ] Masking de valores sensíveis (••••)
- [ ] Toggle active/inactive
- [ ] i18n: `modules.config_recursos.channels.*`

### Agents (Human)

- [ ] Live Status tab: tabel de instâncias + status filter
- [ ] Profiles tab: sidebar com lista de Agent Types (human), detail view
- [ ] Ações: pause, resume, force logout
- [ ] Criar novo profile (POST /v1/agent-types)
- [ ] Deprecate profile (soft delete)
- [ ] i18n: `modules.config_recursos.agents.*`

### Workflows

- [ ] Tabela com filtro por status (started, suspended, completed, failed, cancelled)
- [ ] Coluna: flow_id, status, created_at, updated_at, campaign_id
- [ ] Clique → detail view (suspend_reason, context_snapshot, origin_session_id)
- [ ] Ação: Cancel (com confirmação)
- [ ] i18n: `modules.workflows.instances.*`

### Campaigns

- [ ] Summary cards: total, responded, pending, timed_out
- [ ] Response rate %
- [ ] Channel breakdown (pie chart)
- [ ] Collect event list (paginated)
- [ ] i18n: `modules.workflows.campaigns.*`

### Config

- [ ] Sidebar com namespaces (sentiment, routing, session, consumer, dashboard, webchat, masking, quota)
- [ ] Tabela de keys com valores resolvidos (tenant override wins global)
- [ ] EditDrawer com JSON editor + scope selector
- [ ] Delete override (reset para global)
- [ ] Admin token local (não persistido)
- [ ] i18n: `modules.config_plataforma.config.*`

### Webhooks

- [ ] CRUD completo (create, read, list, update, delete, rotate)
- [ ] Formulário de criação com token one-time display (CopyBox)
- [ ] Detalhe: flow_id, description, token_prefix, trigger_count, last_triggered_at
- [ ] Delivery log (últimas 20)
- [ ] URL pública copiável
- [ ] i18n: `modules.config_plataforma.webhooks.*`

### Pricing

- [ ] Invoice tab: tabela base items + reserve pools com toggle activate/deactivate
- [ ] Totais por seção + GrandTotal
- [ ] Export XLSX
- [ ] Consumption tab: dados de `/reports/usage` (not included in billing — note explícita)
- [ ] Resource Sidebar (admin token local)
- [ ] i18n: `modules.config_plataforma.pricing.*`

---

## Faseamento Detalhado

### Fase 1.1 — Setup e Atendimento (Semanas 1-2)

**O que fazer:**
1. Criar módulo `atendimento` com:
   - `MonitorPage.tsx` (layout base)
   - `PoolHeatmap.tsx` (com polling 5s)
   - `SessionList.tsx` (com polling 10s)
   - `SessionTranscriptView.tsx` (SSE)
2. Criar hooks em `atendimento/api/`:
   - `usePoolSnapshots()` — GET /api/pool/snapshots
   - `useActiveSessions()` — GET /analytics/sessions/active
   - `useSessionStream()` — SSE GET /analytics/sessions/{id}/stream
3. Criar i18n strings para atendimento
4. Testar end-to-end: heatmap → sessions → transcript

**Dependências backend:**
- analytics-api: endpoints GET /sessions/* (✅ pronto)
- mcp-server-plughub: GET /api/pool/snapshots (✅ pronto)

**Critério de conclusão:**
- [ ] Drill-down de heatmap → sessions → transcript funciona
- [ ] Polling não degrada performance
- [ ] i18n testado (PT-BR + EN)
- [ ] Sem erros de TypeScript

### Fase 1.2 — Config Recursos (Semanas 2-3)

**O que fazer:**
1. Expandir `config-recursos/`:
   - `PoolsTab.tsx` (CRUD)
   - `AgentTypesTab.tsx` (CRUD + traffic_weight)
   - `InstancesTab.tsx` (read-only + filter)
   - `SkillsTab.tsx` (Monaco editor)
   - `ChannelsTab.tsx` (CRUD com masking)
   - `AgentsTab.tsx` (Profiles + Live Status)
2. Criar hooks em `config-recursos/api/`:
   - `usePools()` — GET /v1/pools, POST, PUT, DELETE
   - `useAgentTypes()` — GET /v1/agent-types, POST, PUT, PATCH
   - `useInstances()` — GET /v1/instances
   - `useSkills()` — GET /v1/skills, PUT, DELETE
   - `useChannels()` — GET /v1/channels, POST, PUT, DELETE
   - `useHumanAgents()` — GET /v1/instances?framework=human, PATCH
3. Criar i18n strings para config-recursos
4. Testar todos CRUDs

**Dependências backend:**
- agent-registry: todos endpoints (✅ pronto)

**Critério de conclusão:**
- [ ] Todos CRUDs funcionais
- [ ] Validações trabalhando (pool_id existe, etc.)
- [ ] Toasts de success/error
- [ ] i18n completo

### Fase 1.3 — Deprecação (Semana 4)

**O que fazer:**
1. Remover painéis do operator-console:
   - Deletar componentes legados (PoolHeatmapComponent, SessionListComponent, etc.)
   - Deletar hooks/API calls duplicadas
2. Marcar operator-console como read-only (remover CRUDs)
3. Adicionar banner: "Use platform-ui em /config/recursos"
4. Documentar migração em changelog

**Critério de conclusão:**
- [ ] operator-console é read-only
- [ ] Todos usuários migrados para platform-ui
- [ ] Zero breaking changes reportados

---

### Fase 2.1 — Workflows (Semanas 5-6)

**O que fazer:**
1. Expandir `workflows/`:
   - `WorkflowsPage.tsx` (list + filters)
   - `WorkflowDetailModal.tsx` (detail view)
   - `CampaignsTab.tsx` (summary + analytics)
2. Criar hooks:
   - `useWorkflows()` — GET /workflow/v1/instances
   - `useWorkflowDetail()` — GET /workflow/v1/instances/{id}
   - `useCampaignData()` — GET /analytics/campaigns
3. Criar i18n para workflows

**Dependências backend:**
- workflow-api: endpoints GET /v1/workflow/* (✅ pronto)
- analytics-api: GET /reports/campaigns (✅ pronto)

### Fase 2.2 — Config Avançada (Semanas 6-7)

**O que fazer:**
1. Expandir `config-plataforma/`:
   - `ConfigNamespaceTabs.tsx` (8 namespaces)
   - `WebhooksTab.tsx` (CRUD)
   - `WebhookDeliveryLog.tsx` (history)
2. Criar hooks:
   - `useConfig()` — GET /config/v1/*
   - `useWebhooks()` — GET /workflow/v1/webhooks, POST, PUT, DELETE
   - `useWebhookRotate()` — POST /workflow/v1/webhooks/{id}/rotate
3. Implementar CopyBox (one-time token display)

**Dependências backend:**
- config-api: endpoints GET/PUT/DELETE /v1/* (✅ pronto)
- workflow-api: webhook endpoints (✅ pronto)

### Fase 2.3 — Deprecação (Semana 8)

**O que fazer:**
1. Deletar Config/Webhooks/Workflows do operator-console
2. Marcar read-only
3. Migrar últimos usuários

---

### Fase 3 — Pricing + Agent Assist (Semanas 9-12)

**O que fazer:**
1. Integrar PricingPanel (já existe em operator-console, apenas mover)
2. (Futuro) Criar módulo `/agent-assist` como integração da agent-assist-ui legada

---

## Checklist de Deprecação

### Por painel (quando pronto em platform-ui)

Para cada painel descontinuado:

- [ ] Operator-console versão convertida para read-only
- [ ] Platform-ui versão testada em 3+ scenarios
- [ ] i18n completo (PT-BR + EN)
- [ ] Sem erros TypeScript
- [ ] Todos usuários migrados (log de acesso ao operator-console zerado por N dias)
- [ ] Changelog atualizado

### Ao final da Fase 3

- [ ] Deletar `packages/operator-console/` completamente
- [ ] Deletar Vite proxy para operator-console
- [ ] Atualizar documentação (remover menções ao app legado)
- [ ] Anunciar deprecação em changelog

---

## Critério de Deprecação do operator-console

```
OperatorConsole.Deprecated = (
  (Fase1.complete === true)
  && (Fase2.complete === true)
  && (Fase3.complete === true)
  && (ActiveUsers.operatorConsole === 0 for N days)
)
```

Quando verdadeiro:
1. Enviar email aos operadores: "O operator-console será desligado em 30 dias"
2. Redirecionar `/operator-console/*` → `/config/recursos`
3. Após 30 dias: deletar repositório, desligar servidor

---

## Monitoramento de Progresso

### Métricas por fase

| Fase | Objetivo | KPI | Current |
|---|---|---|---|
| 1 | Monitor + Registry | 8 painéis migrados | 8/8 (100%) ✅ |
| 2 | Workflows + Config | 4 painéis migrados | 3/4 (75%) ⚙️ |
| 3 | Pricing + Deprecação | operator-console deletado | 0/1 (0%) 📋 |

### Board de acompanhamento

Acompanhar em issues/PRs com labels:
- `migration/phase-1` (Monitor + Registry)
- `migration/phase-2` (Workflows + Config)
- `migration/phase-3` (Pricing + Deprecation)

---

## Questões Frequentes

### P: Quando operator-console será desligado?
R: Após conclusão das 3 fases (~16 semanas). Será mantido em read-only durante a migração.

### P: Preciso mudar minha integrações com operator-console?
R: Sim. Após Fase 1 (4 semanas), você deve migrar para platform-ui. O operator-console será read-only.

### P: E se eu encontrar um bug no platform-ui?
R: Reporte com label `migration` no repositório. Priorizaremos correção.

### P: Posso pedir nova feature durante a migração?
R: Prefira adicioná-la ao platform-ui (novo módulo ou módulo existente). Não adicione ao operator-console.

---

## Referências

- Módulo platform-ui: `docs/modulos/platform-ui.md`
- Padrão de arquitetura: `docs/standards/frontend-architecture.md`
- Estrutura de módulos: `packages/platform-ui/src/modules/`
