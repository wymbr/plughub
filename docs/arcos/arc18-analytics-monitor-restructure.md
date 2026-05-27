# Arc 18 — Analytics × Monitor Restructuring

## Objetivo

Separar claramente as responsabilidades de Monitor (operacional em tempo real — KPIs, cards, gráficos) e Analytics (exploração histórica — listas brutas com drill-down). Adicionar hierarquia de navegação Journey → Process → Session reutilizando componentes existentes de sessão.

## Hierarquia de Navegação Final

```
Monitor/Journeys    → KPIs, cards, gráficos, lista operacional (+ split/merge)
Monitor/Processes   → cards groupby + InstancesTab (operacional)

Analytics/Journeys  → lista de jornadas  [nova página]
  → clica journey   → lista de processos (workflow instances) daquela journey
    → clica processo → lista de sessions daquele processo
      → clica session → transcript (componente existente reutilizado)

Analytics/Processes → lista de instâncias de workflow  [reconstruída]
  → clica processo  → lista de sessions daquele processo
    → clica session → transcript (componente existente reutilizado)
```

## Padrão de navegação

Drill-down por URL params dentro da mesma rota (breadcrumb visível). Não usa painéis aninhados.

- `/analise/processos` → lista de instâncias
- `/analise/processos?instance=:id` → sessions daquela instância
- `/analise/journeys` → lista de jornadas
- `/analise/journeys?journey=:id` → processos daquela jornada
- `/analise/journeys?journey=:id&instance=:id` → sessions daquele processo

Estado de sessão aberta: reutiliza `SessionTranscript` + painel lateral existente de Sessions.

---

## Fase A — Reorganizar Monitor (UI lift-and-shift)

### A1 — Monitor/Process: adicionar aba Summary

**Arquivo:** `packages/platform-ui/src/modules/agent-flow/ProcessosPage.tsx`

- Criar nova aba "Summary" (primeira aba, default) com os cards consolidados hoje em `AnaliseProcessosPage`:
  - GroupBy selector (pool / campaign)
  - Cards: Triggered, Completed, Failures+Timeout, Avg Duration
  - Reutilizar o componente `WorkflowSummaryCards` extraído de AnaliseProcessosPage
- Manter aba "Instances" existente (InstancesTab)
- `AnaliseProcessosPage` perde os cards → será reconstruída na Fase B

### A2 — Monitor/Journey: nova página

**Arquivo novo:** `packages/platform-ui/src/modules/agent-flow/MonitorJourneysPage.tsx`

- Mover conteúdo da `JourneysTab` do `ProcessosPage` para esta nova página:
  - KPI strip (active, suspended, completed, resolution rate)
  - L1 journey-type chip row (filtro por tipo)
  - Pool dropdown
  - Lista de jornadas com colunas + painel de detalhe lateral
  - SplitDrawer + MergeButton
- `ProcessosPage` perde a `JourneysTab` (passa a ter Summary + Instances)
- Rota: `/monitor/journeys`
- Nav: Monitor group ganha item "Journeys"

---

## Fase B — Analytics/Processes (lista + drill-down)

### B1 — Backend: workflow-api endpoints de lista

**Arquivo:** `packages/workflow-api/src/plughub_workflow_api/router.py`

- Verificar/criar `GET /v1/workflow/instances` com query params:
  - `tenant_id` (obrigatório, via header x-tenant-id)
  - `from_dt`, `to_dt` (ISO date strings)
  - `status` (all / active / suspended / completed / failed / timed_out / cancelled)
  - `pool_id` (via join com journey.pool_id)
  - `flow_id`
  - `limit`, `offset` (paginação)
- Criar `GET /v1/workflow/instances/:id/sessions` → retorna `{ session_ids: string[] }`:
  - origin_session_id da instância
  - sessions collect: busca journeys com journey_id == instance.journey_id → session_ids vinculados

### B2 — Frontend: AnaliseProcessosPage reconstruída

**Arquivo:** `packages/platform-ui/src/modules/analise/AnaliseProcessosPage.tsx`

- Layout: filtros no topo (pool, status, período, flow_id opcional)
- Tabela de instâncias:
  - Colunas: `flow_id`, `status` (badge), `origin_session_id`, `journey_id` (se presente), `started_at`, `duration`, `outcome`
  - Clique na linha → navega para `?instance=:id`
- Sub-tela (quando `?instance=:id`):
  - Breadcrumb: "Processes > instance_id"
  - Lista de sessions vinculadas (origin + collect)
  - Clique na session → abre SessionTranscript reutilizando componente existente
- Hook: `useWorkflowInstances(tenantId, filters)` + `useWorkflowInstanceSessions(instanceId)`

---

## Fase C — Analytics/Journeys (nova página, três níveis)

### C1 — Backend: analytics-api + workflow-api

**workflow-api:**
- `GET /v1/journeys` — já existe, verificar params: `journey_type_id`, `status`, `from_dt`, `to_dt`, `pool_id`, `limit`, `offset`
- `GET /v1/journeys/:id/instances` → lista de workflow instances com `journey_id = :id`

**analytics-api (se necessário):**
- Verificar se `GET /reports/journeys` serve para lista bruta ou só para aggregations

### C2 — Frontend: nova AnaliseJourneysPage

**Arquivo novo:** `packages/platform-ui/src/modules/analise/AnaliseJourneysPage.tsx`

- Rota: `/analise/journeys`
- Nível 1 (default): lista de jornadas
  - Filtros: journey_type, status, período, pool
  - Colunas: `journey_type_id` (chip), `pool_id`, `status` (badge), `origin_session_id`, `started_at`, `duration`, `outcome`
  - Clique → navega para `?journey=:id`
- Nível 2 (`?journey=:id`): lista de processos daquela jornada
  - Breadcrumb: "Journeys > journey_id"
  - Mesma tabela de instâncias do B2
  - Clique → navega para `?journey=:id&instance=:id`
- Nível 3 (`?journey=:id&instance=:id`): lista de sessions
  - Breadcrumb: "Journeys > journey_id > instance_id"
  - Lista de sessions
  - Clique na session → SessionTranscript reutilizado
- Hook: `useJourneyInstances(journeyId)` + reutiliza `useWorkflowInstanceSessions`

---

## Fase D — Nav, rotas e i18n

### D1 — Shell.tsx e routes.tsx

| Rota | Componente | Nav group | Item |
|------|-----------|-----------|------|
| `/monitor/journeys` | MonitorJourneysPage | Monitor | Journeys |
| `/flow/processos` | ProcessosPage (modificada) | Monitor | Processes |
| `/analise/processos` | AnaliseProcessosPage (rebuild) | Analytics | Processes |
| `/analise/journeys` | AnaliseJourneysPage (nova) | Analytics | Journeys |

### D2 — i18n

Namespaces afetados: `contacts`, `workflows`
- Adicionar chaves para novos labels/colunas/filtros em en/ e pt-BR/

---

## Componentes reutilizados (não duplicar)

| Componente | Origem | Reutilizado em |
|---|---|---|
| `SessionTranscript` | `analise/sessions/` | Analytics/Processes + Analytics/Journeys |
| `StatusBadge` | `components/ui/` | Todas as listas |
| `PageBreadcrumb` | `components/PageBreadcrumb` | Sub-telas do drill-down |
| KPI strip da JourneysTab | `ProcessosPage` | MonitorJourneysPage |
| Cards de WorkflowSummary | `AnaliseProcessosPage` | ProcessosPage/Summary tab |

---

## Estado de implementação

| Fase | Tarefa | Status |
|---|---|---|
| A1 | Monitor/Process — aba Summary | pending |
| A2 | Monitor/Journey — nova página | pending |
| B1 | Backend workflow instances list + sessions | pending |
| B2 | Analytics/Process rebuild | pending |
| C1 | Backend journeys drill-down | pending |
| C2 | Analytics/Journeys nova página | pending |
| D | Nav + rotas + i18n | pending |
