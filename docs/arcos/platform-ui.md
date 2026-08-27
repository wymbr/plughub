# Frontend Architecture — platform-ui as standard shell

> Última atualização: 2026-05-25 · Estado: Arc 16

All operator-facing UI lives in `packages/platform-ui/`. Never create standalone frontend packages.

## Shell structure

```
packages/platform-ui/
  src/
    app/          ← App.tsx, routes.tsx (React Router v6)
    auth/         ← AuthContext, useAuth, ProtectedRoute, LoginPage
    components/ui/ ← Button, Card, Table, Badge, Modal, Input, Select, Spinner, PageHeader, EmptyState
    modules/      ← one subfolder per route module
    shell/        ← Shell.tsx (layout), Sidebar.tsx, TopBar.tsx
    i18n/         ← pt-BR (default), en locale files
```

## Design tokens (Tailwind)

| Token | Hex | Uso |
|---|---|---|
| `primary` | `#1B4F8A` | Sidebar, botões principais, links primários |
| `secondary` | `#2D9CDB` | Ações secundárias, badges informativos |
| `accent` | `#00B4D8` | Destaques, hover states |
| `green` | `#059669` | Sucesso, status ativo |
| `warning` | `#D97706` | Alertas, estados de atenção |
| `red` | `#DC2626` | Erros, estados críticos |

Font: Inter (via Google Fonts). Never write hex colors inline — always use Tailwind tokens.

## Adding a new module

1. Create `src/modules/{name}/{ModulePage}.tsx` — use only components from `@/components/ui/`
2. Register route in `src/app/routes.tsx` as a child of the Shell route
3. Add `NavItem` to `navItems[]` in `src/shell/Sidebar.tsx` with `roles` filter

```typescript
// routes.tsx — add to children array
{ path: 'config/billing', element: <BillingPage /> }

// Sidebar.tsx — add to navItems array
{ label: t('nav.billing'), href: '/config/billing', icon: '💳', roles: ['admin'] }
```

## Auth pattern

```typescript
import { useAuth } from '@/auth/useAuth'
const { session } = useAuth()  // session.role: 'operator' | 'supervisor' | 'admin' | 'developer' | 'business'
```

## Roles

Os grupos de navegação são: Home 🏠, Console 🖥️, Monitor 📡, Fluxo 🔄, Avaliação ✓, Analytics 📊, Configuração ⚙️ (ver § Nav structure).

| Role | Grupos acessíveis |
|---|---|
| `operator` | Home, Console, Monitor, Avaliação |
| `supervisor` | operator + Fluxo, Analytics |
| `admin` | supervisor + Configuração |
| `developer` | admin + Fluxo (Editor/Deploy), DevTools |
| `business` | Home, Fluxo, Avaliação, Analytics, Configuração (cross-cutting, sem itens operacionais — `operacao: none` no ABAC) |

## Migrated panels — config-recursos tabs

The `packages/platform-ui/src/modules/config-recursos/` tab container holds 6 tabs:

| Tab | File | Description |
|---|---|---|
| Pools | `PoolsPage.tsx` | Pool CRUD |
| Agent Types | `AgentTypesPage.tsx` | AgentType CRUD |
| Skills | `SkillsPage.tsx` | Skill list + detail |
| Instances | `InstancesPage.tsx` | Running instances (read-only) |
| Canais | `ChannelsPage.tsx` | GatewayConfig CRUD (8 channel types), migrated from operator-console ChannelPanel |
| Agentes Humanos | `HumanAgentsPage.tsx` | Human instance live status + agent type CRUD, migrated from operator-console HumanAgentPanel |

### New API functions in `src/api/registry.ts`

- `listChannels`, `createChannel`, `updateChannel`, `deleteChannel` → `/v1/channels`
- `listHumanInstances`, `instanceAction` → `/v1/instances?framework=human` / `PATCH /v1/instances/:id`
- `listHumanAgentTypes`, `createHumanAgentType`, `updateHumanAgentType`, `deleteAgentType` → `/v1/agent-types`
- `operatorHeaders()` — variant of `headers()` that includes `x-user-id: operator`

### RegistryPanel migration

- `AgentTypesPage.tsx` — full rewrite: correct frameworks (plughub-native, human, external-mcp, langgraph, crewai, anthropic_sdk, azure_ai, google_vertex, generic_mcp), role select, max_concurrent_sessions, pool checkboxes, skills checkboxes, Deprecate→Confirm flow; pools rendered as chips in table
- `SkillsPage.tsx` — full rewrite: removed create form (skills are YAML-managed), info banner pointing to skill-flow-engine/skills/, detail modal shows tools/knowledge_domains chips
- `InstancesPage.tsx` — full rewrite: correct status filters (ready/busy/paused/draining), dynamic pool filter from API, channel_types column, 15s auto-refresh
- `PoolsPage.tsx` — added instagram/telegram/webrtc channel options
- Both i18n JSON files updated with new keys for executionModel, role, maxConcurrent, channels
- `types/index.ts` — fixed `AgentType.pools: Array<{pool_id: string}>`, `skills: Array<{skill_id; version_policy?}>`, added `updated_at?`
- `api/registry.ts` — `createAgentType` now maps `pools: string[]` → `{pool_id}[]` before POST

### New types in `src/types/index.ts`

`ChannelType`, `GatewayConfig`, `CreateGatewayConfigInput`, `UpdateGatewayConfigInput`,
`HumanAgentType`, `CreateHumanAgentInput`, `UpdateHumanAgentInput`, `AgentInstance`

## Migrated panels — billing module

`packages/platform-ui/src/modules/billing/BillingPage.tsx` — migrated from `packages/operator-console/src/components/PricingPanel.tsx`.

Route: `/config/billing` (role: `admin`). Nav entry: 💳 Faturamento under Configuração group.

### Components

- `ResourceSidebar` (220px left panel) — base + reserve resource list grouped by pool; admin token input
- `InvoiceTab` — base items table + reserve group blocks with activate/deactivate toggle; grand total; XLSX export link
- `ConsumptionTab` — usage dimensions from analytics-api with info banner (not included in billing)

### Inline hooks (no separate hooks file needed)

- `useInvoice(tenantId)` → `GET /v1/pricing/invoice/{tenantId}`
- `useResources(tenantId)` → `GET /v1/pricing/resources/{tenantId}`
- `useUsage(tenantId)` → `GET /reports/usage?tenant_id={tenantId}`

### Vite proxy added to `vite.config.ts`

- `'^/v1/pricing'` → `http://localhost:3900` (before the generic `'^/v1'` → port 3300 entry)

### New types in `src/types/index.ts`

`InvoiceLineItem`, `ReserveGroup`, `Invoice`, `InstallationResource`

## Migrated panels — skill-flows module

`packages/platform-ui/src/modules/skill-flows/SkillFlowsPage.tsx` — migrated from the former `operator-console` SkillFlowEditor.

Route: `/agent-flow/editor` (roles: `admin`, `developer`). Replaces the former `PlaceholderPage`. The legacy path `/skill-flows` redirects to `/agent-flow/editor`.

### Features (fully ported)

- Monaco YAML editor (`vs-dark` theme, `@monaco-editor/react`) with live YAML validation
- Left sidebar: skill list with search, type color-coding (orchestrator=violet, vertical=cyan, horizontal=yellow), modification indicator `●`
- New skill flow: prompts for skill_id, injects blank template with the entered id
- Save: YAML→JSON parse → `PUT /v1/skills/:id` — 422 validation errors shown in status bar
- Delete: three-stage confirmation (Delete → Confirmar → execute)
- Discard: reverts to last saved state
- ⌘S keyboard shortcut
- Auto-refresh skill list every 30s

### New dependencies added to `package.json`

`@monaco-editor/react@^4.7.0`, `js-yaml@^4.1.1`, `@types/js-yaml@^4.0.9`

## Migrated panels — campaigns module

`packages/platform-ui/src/modules/campaigns/CampaignsPage.tsx` — migrated from the former `operator-console` CampaignPanel.

Route: `/workflow/report` (roles: `operator`, `supervisor`, `admin`, `business`). Accessible via Analytics → Processos/Campanhas nav entry. The legacy path `/campaigns` redirects to `/workflow/report`.

### Features (fully ported)

- Left 320px sidebar: global KPI bar (Campanhas / Total / Taxa), channel + status filter dropdowns, campaign card list with `MiniBar` (4-color status bar) and `RateBadge` (green/yellow/red)
- Right detail panel: campaign header with rate badge, 4-up KPI grid (Total / Respondidos / Expirados / Tempo médio), status distribution bar with legend, channel breakdown with progress bars, recent collect events table (token · canal · status · enviado · tempo)
- `useCampaignData` inline hook — polls `GET /reports/campaigns` every 30s, supports channel/status filters
- New types added to `src/types/index.ts`: `CampaignSummary`, `CollectEvent`
- i18n: `nav.campanhas` added to pt-BR and en locales

## Migrated panels — config-plataforma module

`packages/platform-ui/src/modules/config-plataforma/components/NamespaceEditor.tsx` — upgraded to match full `ConfigPanel` feature set from operator-console.

Route: `/config/platform` (role: `admin`), tab ⚙️ Configuração. No new route needed — the ConfigPlataformaPage already existed.

### New features added to NamespaceEditor

- **Scope selector** in edit mode: 🌐 Global default vs 🏢 Tenant override — `putConfig(ns, key, value, null | tenantId, adminToken)`
- **"tenant override" badge** on entries where `entry.tenant_id ≠ '__global__'`
- **Reset button** (delete override) — restores global default; only shown when `adminToken` is set
- **Description display** per key (from `ConfigEntry.description`)
- **Tailwind redesign** — replaces inline CSS with design system tokens (`text-primary`, `bg-gray-50`, etc.)

### `config-hooks.ts` updated

- `ConfigEntry` extended with `tenant_id: string | null`, `namespace?: string`, `updated_at?: string`
- `useNamespace` return type changed from `Record<string, unknown>` → `Record<string, ConfigEntry>`
- Normalisation shim handles APIs that return plain values instead of `ConfigEntry` objects
- `AllConfig.config` type updated to `Record<string, Record<string, ConfigEntry>>`

### `MaskingPage.tsx` updated

Adapted to use `entries[key]?.value` instead of direct entry (due to type change).

## Legacy standalone apps — ✅ migration completo, pacotes removidos

- `packages/operator-console/` — **Removido** (diretório deletado; docker-compose atualizado). Todos os 12 painéis migrados para `platform-ui`:
  - ✅ ChannelPanel → `config-recursos/ChannelsPage.tsx`
  - ✅ HumanAgentPanel → `config-recursos/HumanAgentsPage.tsx`
  - ✅ PricingPanel → `modules/billing/BillingPage.tsx`
  - ✅ SkillFlowEditor → `modules/skill-flows/SkillFlowsPage.tsx`
  - ✅ RegistryPanel (Pools/AgentTypes/Skills/Instances) → `config-recursos/` tabs
  - ✅ CampaignPanel → `modules/campaigns/CampaignsPage.tsx`
  - ✅ ConfigPanel → `modules/config-plataforma/components/NamespaceEditor.tsx`
- `packages/agent-assist-ui/` (port 5175) — chat + right panel → ✅ migrated to `modules/agent-assist/AgentAssistPage.tsx`

## Nav structure — groups, roles and ABAC gates

Source of truth: `src/shell/Sidebar.tsx`. Sidebar is collapsible (icon-only strip when collapsed). Groups with `children[]` are expandable accordions; leaf items are direct `<Link>` entries.

### Top-level items

| Item | Icon | href | Roles | ABAC gate |
|------|------|------|-------|-----------|
| Home | 🏠 | `/` | operator, supervisor, admin, developer, business | — |
| Console | 🖥️ | `/console` | operator, supervisor, admin | `contacts.operacao` |

### Monitor group (navKey: `monitor`)

Icon: 📡 — roles: operator, supervisor, admin

| Child | Icon | href | ABAC gate |
|-------|------|------|-----------|
| Sessions | 📋 | `/flow/monitor` | `contacts.operacao` |
| Agents | 👥 | `/contacts/agents` | `contacts.operacao` |
| Pools | 🏊 | `/contacts/pools` | `contacts.operacao` |
| Events | 📡 | `/contacts/events` | `contacts.operacao` |
| Processes | ⚙️ | `/flow/processos` | `workflows.operacao` |

### Fluxo group (navKey: `flow`)

Icon: 🔄 — roles: admin, developer, business, supervisor

| Child | Icon | href | ABAC gate |
|-------|------|------|-----------|
| Editor | ✏️ | `/agent-flow/editor` | `skill_flows.operacao` |
| Deploy | 🚀 | `/agent-flow/deploy` | `skill_flows.operacao` |

### Avaliação group (navKey: `quality`)

Icon: ✓ — roles: operator, supervisor, admin, business

| Child | Icon | href | Roles (override) | ABAC gate |
|-------|------|------|------------------|-----------|
| Forms | 📝 | `/evaluation/forms` | admin | `evaluation.formularios` |
| Campaigns | 📋 | `/evaluation/campaigns` | supervisor, admin | `evaluation.formularios` |
| Knowledge | 📚 | `/evaluation/knowledge` | admin | — |
| Evaluations | 🗂️ | `/evaluation/evaluations` | operator, supervisor, admin | — |

### Analytics group (navKey: `analise`)

Icon: 📊 — roles: supervisor, admin, business

| Child | Icon | href | ABAC gate |
|-------|------|------|-----------|
| Sessions | 📋 | `/analise/sessions` | `contacts.visualizar` |
| Agents | 👥 | `/analise/agents` | `contacts.visualizar` |
| Events | 📡 | `/analise/events` | `contacts.visualizar` |
| Processes | ⚙️ | `/analise/processos` | `workflows.operacao` |
| Quality | ✓ | `/analise/quality` | `evaluation.report` |

### Configuração group (navKey: `config`)

Icon: ⚙️ — roles: admin, business

| Child | Icon | href | ABAC gate |
|-------|------|------|-----------|
| Dashboards | 📊 | `/dashboards` | `config.dashboards` |
| Resources | 📦 | `/config/resources` | `config.resources` |
| Platform | 🖥️ | `/config/platform` | `config.platform` |
| Channels | 📡 | `/config/channels` | `config.channels` |
| Calendars | 📅 | `/config/calendars` | `config.calendars` ⚠️ menu-only |
| Schedules | ⏰ | `/config/schedules` | `scheduler.configurar` (strict) |
| Outbound | 📣 | `/config/outbound` | `outbound.configurar` (strict) |
| Masking | 🔒 | `/config/masking` | `config.masking` |
| DialogForms | 💬 | `/config/dialog-forms` | `config.dialog_forms` ⚠️ menu-only |
| Billing | 💳 | `/config/billing` | roles: admin, business (no ABAC gate) |
| Access | 🔐 | `/config/access` | `config.users` |
| Groups | 👥 | `/config/groups` | `config.users` (aba Donos: `config.permissions`) |

> **Atualizada em 2026-08-27 (passo 2 do arco de ABAC total).** Cinco linhas diziam
> `config.platform` — era catch-all, e no caso do Channels **divergia do backend**, que já
> exigia `config.channels`. Ver `docs/arcos/arc7-auth.md`.
>
> ⚠️ **menu-only** = o campo decide quem VÊ a tela, não quem pode escrever: `calendar-api` e
> `dialog-api` não têm portão (medido — escrita anônima aceita). Dívida em `TODO.md`.

### Filtering rules

**Atualizado em 2026-08-27 (passo 5 do arco de ABAC total).** A descrição anterior — dois passes,
`roles[]` e depois `passesAbac()`, com admin/supervisor e `moduleConfig` vazio passando por cima —
**não vale mais**, e cada uma daquelas três liberações foi removida:

Hoje há **um** passe: `passesAbacRule(rule, moduleConfig, role, unrestricted)`, grant-first para
todo mundo. Não há `roles[]` no `Sidebar.tsx`, não há decisão por papel dentro da função, e
`moduleConfig` vazio significa **não pode nada** (antes significava "pode tudo").

A única forma de ver um item sem ter o grant dele é o claim **`unrestricted`**, declarado por
usuário. Cabeçalho de grupo continua **derivado**: visível ⟺ ao menos um filho visível.

**ABAC tier semantics:**
- `operacao` gates operational write items (Monitor, Console, Editor, Deploy) — users with `operacao: none` (e.g. business) don't see these
- `visualizar` gates read-only analytics items (Analise tabs, report pages)
- `report` gates quality/evaluation reports (evaluation.report)

Legacy redirects in `routes.tsx`:
- `/workflows` → `/workflow/monitor`
- `/skill-flows` → `/agent-flow/editor`
- `/reports` → `/contacts?tab=analise`

## Skill Deploy Lifecycle (Phase 1) — ✅ implemented

Skills follow a two-stage lifecycle: **draft** (saved YAML not yet in production) → **published** (deployed to pools).

### PostgreSQL additions (migration `20260430000000_add_skill_deployments`)

- `skills.deploy_status: TEXT DEFAULT 'draft'` — `"draft"` or `"published"`
- `skills.published_at: TIMESTAMPTZ?` — timestamp of first/last deploy
- `skill_deployments` table — deployment history with `pool_ids[]`, `yaml_snapshot`, `deployed_by`, `deployed_at`, `notes`

### New agent-registry endpoints

| Method | Route | Description |
|--------|-------|-------------|
| `POST` | `/v1/skills/:skill_id/deploy` | Deploys skill to `pool_ids[]`; sets `deploy_status=published`, records `SkillDeployment`, triggers `publishRegistryChanged` |
| `GET` | `/v1/skills/:skill_id/deployments` | Returns deployment history (newest first, `limit` param, max 200) |

### Invariants

- `PUT /v1/skills/:id` (save) always sets `deploy_status = "draft"` on new skills; NEVER modifies `deploy_status` on updates — only the deploy action changes it
- Every deploy snapshot the `flow` JSON at deploy time into `yaml_snapshot` for rollback reference
- Deploy calls `publishRegistryChanged(tenantId, "skill", skillId, "updated")` to trigger hot-reload in orchestrator-bridge

### Rollback

= trigger a new deploy pointing to the previous `yaml_snapshot` version — ✅ implemented (rollback button in Deploy UI history).

### Phase 2 — new agent-registry endpoints (added in Phase 2)

| Method | Route | Description |
|--------|-------|-------------|
| `GET` | `/v1/skills/:skill_id/deployments/scheduled` | Lists pending scheduled deploys (proxies to workflow-api, filters by `skill_scheduled_deploy_v1` + skill_id in context) |
| `GET` | `/v1/skills/:skill_id/handoff-status` | Returns `{ deployed, active_sessions, pool_ids, deployed_at, deployed_by, deployment_id }` — queries analytics-api for sessions started before `deployed_at` in affected pools |

### Phase 2 — new packages

- `packages/skill-flow-engine/skills/skill_scheduled_deploy_v1.yaml` — timer-based workflow; `on_timeout` IS the deploy trigger
- `packages/mcp-server-plughub/src/tools/deploy.ts` — `skill_deploy` MCP tool (calls agent-registry POST /v1/skills/:id/deploy)
- `workflow-api/router.py` — `PersistSuspendRequest.scheduled_at` (ISO-8601) overrides `timeout_hours` calculation

**Phase 2 — complete.**

## What never to do

- Never create a new `packages/my-ui/` standalone app — add a module to platform-ui
- Never use inline hex colors — use Tailwind tokens (`text-primary`, `bg-secondary`)
- Never write custom CSS when a Tailwind class exists
- Never create a NavItem without `roles` filter
- Never modify `deploy_status` in PUT /v1/skills — only the deploy action owns that field
- **Never build a table as two sibling grids** — um `<div grid>` para o cabeçalho e outro por linha
  NÃO compartilham trilha: cada `auto` dimensiona pelo conteúdo da sua própria grid, e o conteúdo é
  de naturezas diferentes (palavra no título, dígito no dado), então as colunas divergem por
  construção. Trocar `auto` por px fixo conserta a foto e não a causa — o primeiro rótulo traduzido
  mais longo reabre. Use `<table>` (padrão dominante: 46 usos em 31 arquivos). Aberto em
  `work-items/WorkItemsPage.tsx` (:88/:352) e `schedules/SchedulesMonitorPage.tsx` (:101/:108);
  corrigido em `analise/WrapupSummaryPage.tsx` (2026-08-11)
- **Never offer in the UI a choice the backend refuses** — a opção inválida não falha com erro
  legível, falha com um estado mudo (o pool IA no seletor do Console ficava "Disconnected" porque o
  login era negado por `pool_kind_mismatch`). Quando a UI espelha um gate do backend, o predicado da
  tela deve ser **igual ou mais permissivo** que o do gate: mais restritivo esconde por AUSÊNCIA

---

# Agent Assist UI — `packages/platform-ui/src/modules/agent-assist/`

**Migrated from `packages/agent-assist-ui/` to platform-ui shell.** Route: `/agent-assist` (roles: operator, supervisor, admin). agentName from `useAuth()` session.name; poolId from `?pool=` URL param with inline picker when absent. Uses `h-full` instead of `h-screen` (Shell provides the outer container).

Vite proxies added: `'^/api'` → `http://localhost:3100`, `'^/agent-ws'` → `ws://localhost:3100` (ws: true).

New dependency: `recharts@^2.x` (used by EstadoTab sentiment line chart).

## Seletor de presença — o que o humano pode ESCOLHER (2026-08-11)

Três listas, e confundi-las é o defeito histórico desta tela:

| lista | conteúdo | quem consome |
|---|---|---|
| `availablePools` | CRUA do registry — inclui espelho `-int` e pools IA | inbox, `poolSlaMap`, `withMirror` |
| `selectablePools` | `!p.mirror_of && p.agent_kind !== 'ai'` | combo do Header **e** `handleJoinAll` |
| `activePools` | pools com WS aberto (inclui o espelho) | `pullPoolIds`, roteamento de eventos |

`selectablePools` mora no **`AgentAssistContext`** (dono único) porque tem dois consumidores. Enquanto
era um `filter` local da página, o "All pools" mapeava a lista crua e tentava logar em todo pool IA do
tenant. Duas exclusões, a mesma forma — *a opção existe na tela e não no backend*:

- **espelho `-int`**: entrar no pai já entra na fila interna (ADR `adr-internal-work-queue-author-bound` D3);
- **`agent_kind: 'ai'`**: `handleHumanLogin` nega com `pool_kind_mismatch`.

**Predicado `!== 'ai'`, nunca `=== 'human'`.** `Pool.agent_kind` é nullable (o backfill por inferência
só roda no boot do agent-registry) e o gate do backend também nega apenas em `'ai'`. Um `null` aparece
na lista **de propósito**: esconder seria mais restritivo que o backend, e por ausência.

O Header recebe `activePools.filter(p => !p.endsWith("-int"))` (`AgentAssistPage:642`) — por isso o
contador `2/2` convive com 3 pools ativos, e isso é correto, não drift.

## Capacidade do agente na tela (2026-08-04)

Duas fontes contam a capacidade do agente, e o Console só enxerga uma:

| quem decide | fonte | inclui |
|---|---|---|
| **Console** (`atCapacity`) | `contacts.size >= JWT.maxConcurrentSessions ?? 3` | só o que virou **cartão** |
| **Árbitro** (`work_task_claim` → `claim_instance`) | `SCARD {t}:instance:{iid}:sessions >= max_concurrent` do registro | **holds** `__wrapup_hold__::` (Phase 2) e sessões sem cartão |

Enquanto concordam, o botão "Atender (Pull)" do preview (`disabled={atCapacity}`) é um previsor
fiel. Quando discordam, ele fica habilitado e o servidor recusa com `no_capacity` — e a recusa é a
única coisa que informa. Instrumento para diagnosticar:
`infra/test/probe_claim_capacity_sources.sh`, que **identifica** cada ocupante (`SMEMBERS`) e separa
hold de sessão.

Por isso o teto é **exibido**: o crachá do `Header` mostra fração (`Serving 3/3`, cor de lotação
quando cheio; sem a prop `maxConcurrent` mantém o rótulo antigo) e o preview mostra "Todas as vagas
em uso (n/max)" ao lado do botão desabilitado. Antes, a razão do bloqueio existia só num `title` —
hover apenas, inexistente no toque —, e um botão cinza sem causa legível é lido como tela quebrada.

**Não há botão de claim na linha da inbox**: a linha abre o preview, e o claim manual acontece só na
barra do preview. O `handlePull` do `PullInboxPanel` serve exclusivamente o auto-atendimento de
wrap-up (Camada E2).

## Module structure

```
modules/agent-assist/
  AgentAssistPage.tsx          ← main page (adapts App.tsx)
  types.ts                     ← all type definitions
  hooks/
    useAgentWebSocket.ts       ← persistent WS, reconnect, heartbeat
    useSupervisorState.ts      ← polls /api/supervisor_state/{sessionId}
    useSupervisorCapabilities.ts
    useCustomerHistory.ts      ← GET /analytics/sessions/customer/{id}
  components/
    Header.tsx                 ← handle-time, SLA bar, WS dot
    ChatArea.tsx               ← messages + live sentiment strip
    AgentInput.tsx             ← textarea + Encerrar button
    MessageBubble.tsx          ← per-author styles + MenuCard delegation
    MenuCard.tsx               ← read-only menu interaction preview
    ContactList.tsx            ← per-contact cards with sentiment/SLA/timer
    RightPanel.tsx             ← tab container (5º tab OrchestrationTab gateado por supervisor/admin — Arc 11)
    ToastContainer.tsx         ← fixed bottom-right notifications
    tabs/
      EstadoTab.tsx            ← sentiment chart (recharts), intent, flags, SLA, AiParticipantCard (Arc 11)
      CapacidadesTab.tsx       ← suggested agents + escalation options
      ContextoTab.tsx          ← ContextSnapshotCard (teal) + ContactContextCard (emerald)
      HistoricoTab.tsx         ← customer session history + processos em aberto (Arc 10)
      OrchestrationTab.tsx     ← Arc 11 — agentes AI + pipeline transitions + intervenções (supervisor/admin)
```

**Legacy app** (`packages/agent-assist-ui/`, port 5175) — frozen, kept as reference.

React 18 + TypeScript + Vite. **Original** porta de dev: 5175. Proxy: `/api` → mcp-server-plughub (3100), `/agent-ws` → WS mcp-server (3100), `/analytics` → analytics-api (3500).

## Layout

```
┌─────────────────────────────────────────┐
│  Header (agente, pool, sessão, SLA, WS) │
├────────────────────┬────────────────────┤
│  ChatArea (60%)    │  RightPanel (40%)  │
├────────────────────┴────────────────────┤
│  AgentInput + CloseModal trigger        │
└─────────────────────────────────────────┘
```

## Fluxo de sessão

1. UI abre em modo lobby (`wsSessionId=null`, conecta via `pool` no WS)
2. `conversation.assigned` chega via `pool:events:{poolId}` → `setSessionId`, `fetchHistory`, atualiza URL
3. Mensagens chegam por `message.text` WS events → adicionadas a `messages[]`
4. Agente encerra → botão Close → `handleClose` → POST `/api/agent_done/{sessionId}`
   com `{ issue_status: "closed", outcome: "resolved" }` — **sem modal**. O antigo
   `CloseModal` (issue_status + outcome + handoff_reason) foi removido: a partir do
   Arc 14, o wrap-up é delegado aos hook agents `side: agent | customer` e o
   `agent_done` é disparado direto, sem coleta interativa de campos no encerramento.
5. Cliente desconecta → `session.closed` com `client_disconnect` → `pendingCloseModal: true`
   → `useEffect` auto-dispara `agent_done` com `{ outcome: "abandoned" }` — sem modal
6. Agente clica "Desligar" → `handleDesligar` → `handleClose` com `{ outcome: "abandoned" }` → `agent_done` imediato

## Componentes

| Componente | Responsabilidade |
|---|---|
| `Header` | Nome do agente, pool, session_id, status WS, SLA badge, timer de atendimento ao vivo |
| `ChatArea` | Lista de mensagens + indicador de digitação AI + painel de sentimento ao vivo |
| `AgentInput` | Input de texto, botão enviar |
| `RightPanel` | Tab container: Estado / Capacidades / Contexto / Histórico |
| `ToastContainer` | Notificações temporárias e persistentes |

## RightPanel — tabs

| Tab | Conteúdo |
|---|---|
| `estado` | `EstadoTab` — sentimento (score, trend, alert), intent, SLA, flags |
| `capacidades` | `CapacidadesTab` — suggested_agents + escalation suggestions |
| `contexto` | `ContextoTab` — historical_insights (azul) + conversation_insights (roxo) |
| `historico` | `HistoricoTab` — últimos 20 contatos fechados do cliente via analytics-api |

## HistoricoTab — implementação

- Hook `useCustomerHistory(customerId)` — fetch `GET /analytics/sessions/customer/{id}?tenant_id=VITE_TENANT_ID&limit=20`
- Env vars: `VITE_ANALYTICS_URL` (default `/analytics`), `VITE_TENANT_ID` (default `tenant_demo`)
- Re-busca automaticamente quando `customerId` muda
- Cancela fetch anterior em cada re-render (cleanup via flag `cancelled`)
- `HistoryRow` — expansível: summary (ícone de canal, badge de outcome, data, duração, close_reason) + detalhes (pool, canal, session_id)
- Estado vazio quando `customerId === null` ("Cliente não identificado")
- Graceful degradation: erro retorna `[]` com mensagem de erro não-bloqueante

## Auto-reconexão WebSocket

`useAgentWebSocket` — reconnect automático com delay de 3s em close inesperado:
- `reconnectCount` state: incrementado por `ws.onclose` quando `!intentionalClose.current`
- `intentionalClose` ref: setado no cleanup do useEffect (unmount ou mudança de dep)
- Dependency array: `[sessionId ?? poolId, reconnectCount]` — reconecta ao bump de `reconnectCount`
- Na reconexão, mcp-server entrega `pool:pending_assignment:{poolId}` (TTL 300s) para retomar sessão em andamento

## Handle-time counter

`Header.tsx` recebe `sessionStartedAt: Date | null` — prop passado de App.tsx quando `conversation.assigned` chega. `useEffect`/`setInterval` a cada 1s atualiza `handleMs = Date.now() - sessionStartedAt`. Formato: `M:SS` (< 1h) ou `H:MM:SS` (≥ 1h). Vira laranja após 30 minutos para alertar o agente. Resetado para `null` ao encerrar sessão em ambos os fluxos.

## Renderização de mensagens `agents_only`

**Backend fix** — `message_send` em `mcp-server/tools/session.ts` agora publica no canal Redis `agent:events:{session_id}` depois do XADD. Publicação ocorre para `visibility: "all"` e `"agents_only"` (não para arrays de participant_ids). O `author.type` no envelope WS é determinado consultando `{tenant_id}:agent:instance:{participant_id}` — se tiver `agent_type_id`, emite `"agent_ai"`, caso contrário `"agent_human"`. Entrega WS é best-effort (try/catch não-fatal).

**Gap corrigido** — o bridge de orquestração só encaminhava `conversations.inbound` (mensagens do cliente). Com essa mudança, mensagens de agentes IA com `visibility: "all"` e notas internas com `visibility: "agents_only"` chegam ao agente humano em tempo real.

**Frontend** — `ChatMessage.visibility?: string` e `WsMessageText.visibility?: string` adicionados em `types.ts`. `App.tsx` propaga `event.visibility` ao construir o `ChatMessage`. `MessageBubble.tsx` detecta `visibility === "agents_only"` e renderiza:
- Background âmbar (`bg-amber-50`) com borda tracejada âmbar (`border-dashed border-amber-400`)
- Badge "Interno" em âmbar antes do label do autor
- Posicionado à esquerda (nunca à direita, independente do autor)

## Menu de aprovação — renderização no chat (modo observação)

`ChatMenuData` interface adicionada em `types.ts` com campos `menu_id`, `interaction`, `prompt`, `options?`, `fields?`. `ChatMessage.menuData?: ChatMenuData` adicionado — quando presente, `MessageBubble.tsx` delega para `MenuCard` em vez de renderizar um bubble normal.

### `components/MenuCard.tsx` (novo) — card read-only com badge de tipo de interação + label "IA → Cliente · observação". Renderizadores por tipo

| Tipo | Renderização |
|---|---|
| `text` | Prompt + indicador "Aguardando resposta em texto livre…" |
| `button` | Chips com borda indigo arredondada, `disabled` |
| `list` | Lista numerada com itens separados por linha, `disabled` |
| `checklist` | Checkboxes com labels, todos `disabled` |
| `form` | Campos `<input>` com label acima, `disabled` |

### `App.tsx` — evento `menu.render` agora popula `menuData` estruturado no lugar do texto plano com bullets

O campo `text` mantém o `prompt` como fallback para consumidores simples.

## Modo substituição (Phase 2 — ✅ implementado)

`substitutionMode: boolean` prop em `MenuCard.tsx` alterna entre observação (disabled) e substituição (interativo). Quando ativo: borda âmbar, badge "substituição", todos os 5 tipos de interação (button/list/checklist/form/text) tornam-se funcionais. `SubmitResult = string | string[] | Record<string, string>`. `onSubmit` propaga por `ChatArea → MessageBubble → MenuCard`. Botão "🔄 Substituir/Substituindo" na `ActionBar` liga/desliga o modo; reset automático ao trocar de contato. Backend: `POST /api/menu_submit/:sessionId` no mcp-server-plughub faz XADD `interaction_result` no stream Redis e pub/sub `agent:events:{sessionId}` — o Skill Flow Engine retoma o suspend step. Auto-disable após submit bem-sucedido.

## Roteamento por participant_id (✅ implementado)

Quando múltiplos agentes (NPS + wrap-up) estão bloqueados em `menu:waiting:{sessionId}` com visibility arrays distintas, o `menu_submit` endpoint resolve o `participant_id` do agente humano via ContextStore (`session.human_agent_participant_id`) e faz `vis.includes(agentPid)` — mesmo padrão do WS text handler (linha 1383 de `server.ts`). Garante que o click de botão do wrap-up seja roteado para o agente correto e não para o NPS.

## Echo otimista de menu buttons (✅ implementado)

`handleMenuSubmit` em `AgentAssistPage.tsx` adiciona a mensagem selecionada ao estado local React (id: `local-menu-{timestamp}`, author: `agent_human`, visibility: `agents_only`) antes do fetch para `/api/menu_submit`. Labels de opções são resolvidos via `menuData.options` (ex: id `resolvido` → label `✅ Resolvido`). Mesmo padrão do `handleSend` para texto digitado.

## i18n — estado dos namespaces

Toda string exibida ao usuário deve usar `useTranslation(namespace)` + `t(key)`.
Locale padrão: `pt-BR`. Locale alternativo: `en`. Arquivos em `src/i18n/locales/`.

| Namespace | Arquivo | Status |
|---|---|---|
| `agentAssist` | `agentAssist.json` | ✅ completo — cobre todo o módulo `modules/agent-assist/` |
| `contacts` | `contacts.json` | ✅ completo |
| `evaluation` | `evaluation.json` | ✅ completo |
| `channels` | `channels.json` | ✅ completo |
| `groups` | `groups.json` | ✅ completo |
| `common` | `common.json` | ✅ completo |
| `nav` | (dentro de `common`) | ✅ completo |

**Regra de helpers fora de componentes React**: funções module-level que produzem strings
traduzidas devem receber `t` como parâmetro explícito — não chamar `useTranslation` fora
de componentes/hooks. Exemplo em `ContextoTab.tsx`: `confidenceLabel(c, t)`, `sourceLabel(s, t)`.

**Convenção de tipo para `t` em props**: usar
`t: (key: string, opts?: Record<string, unknown>) => string`
para aceitar tanto chamadas simples (`t('key')`) quanto com interpolação (`t('key', { var })`).

---

# Módulos de UI adicionados por arcos posteriores

## Arc 11 — Console como superfície de orquestração

O Console ganhou componentes para tratar agentes AI como coparticipantes de primeira classe:

- `AiParticipantCard` — no EstadoTab; mostra step/status do Skill-Flow de cada agente AI em tempo real (polling 3s) + drawer com últimas 5 mensagens e ação "Encerrar segmento".
- `AdicionarEspecialistaButton` — dropdown 2-step no ActionBar; lista agentes de `mentionable_pools` e os invoca via `@mention` por A2A `assist`.
- `DelegarTarefaDrawer` + `DelegarButton` — seleção de mensagens no transcript → drawer de delegação (agent picker + instrução + visibilidade); card de resultado quando `agent_done` chega.
- `OrchestrationTab` — **5º tab** do RightPanel, gateado por role `supervisor|admin`: agentes AI + linha do tempo de `pipeline_transitions` + ações de intervenção (injetar contexto, force-complete).

## Arc 13 — Calibração e Curadoria

- `CalibrationDashboard` — rota `/evaluation/calibration` (roles `supervisor`, `admin`): KPI strip, LineChart de calibration score por skill version × tempo, ReferenceLine 90%, tabela detalhada. Hook `useEvaluatorCalibration`.
- `CuradoriaPage` — rota `/evaluation/curadoria` (roles `supervisor`, `admin`): fila de curadoria do feedback loop RAG, `CurationCard` com trigger badges + sinal AI, `RecalibrateDrawer`.
- Human review UX na `AvaliacoesPage`: `DimensionStateIndicator`, `DimensionThreadCard`, `HumanReviewPanel`, `DimensionContestPanel13`; campaign config na `CampaignsPage` (`CurationSamplingRulesEditor`).

## Arc 15 — WebRTC overlay no Console

> ⚠️ 2026-08-19 — os componentes abaixo **existem**; o que não existe é o SFU do outro lado (plano de mídia nunca provisionado — ver [`arc15-webrtc.md`](arc15-webrtc.md):3-17 e [`../adr/adr-voice-media-plane.md`](../adr/adr-voice-media-plane.md)).

- `WebRTCOverlay` — embutido no Console para sessões `channel=webrtc`; renderiza `VideoGrid` (2-up), waveform animado ou nenhum overlay conforme o medium negociado. `MediaControls`, `WebRTCSupervisorView` (observer read-only), hook `useWebRTCSession`. Namespace i18n `webrtc`.

## Audit LGPD

- `AuditPage` — rota `/audit` (5 tabs: Sessions + MCP Calls ativos, 3 stubs). Nav entry standalone "Auditoria LGPD" (🔍) com ABAC gate `audit.sessions`. Banner de aviso: todo acesso é registrado em log imutável.
