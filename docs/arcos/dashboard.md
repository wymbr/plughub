# Dashboard — Generic Card System

> Última atualização: 2026-05-25 · Estado: Arc 16
> **Status: implementado.** O sistema de cards genéricos configuráveis (Dashboard #35) está em produção: `DisplayTool` registry, `ENDPOINT_CATALOG`/`catalog.ts`, os 5 tipos de card (`metric_card`, `table`, `bar_chart`, `line_chart`, `donut`), o `FilterBar` de runtime filters e os endpoints `/reports/display/*` no analytics-api. O texto abaixo descreve o estado atual da plataforma.
> Inspirado no modelo Grafana: Display Tool + Query configurável + Runtime Filters.

---

## Base existente (camada que não muda)

`DashboardsPage.tsx` implementa:
- **react-grid-layout** — 12 colunas, `rowHeight=60`, drag-and-drop, resize, compactType vertical
- **Template system** — admin cria templates compartilhados; usuários têm layout pessoal (override por `{tenant}:{userId}`)
- **Storage** — Config API namespace `dashboards`, chaves `template:{uuid}` e `layout:{tenantId}:{userId}`
- **Modo edit/view** — admin edita template; operator/supervisor reordena apenas o layout pessoal
- **Dirty flag + save** — template salvo como admin; layout pessoal salvo sem admin token (fallback localStorage)
- **ABAC** — `config.platform` gate na Sidebar; `admin/developer` pode editar; outros só visualizam

Essa camada permaneceu intacta. A evolução foi cirúrgica: o card type hardcoded foi substituído por um **Display Tool Registry** e o schema do `DashboardCard.config` evoluiu para `tool_id` + `query` + `tool_config`.

---

## Display Tool Registry

### Contrato de um Display Tool

```typescript
// platform-ui/src/dashboard/tools/types.ts

export interface DisplayToolDataShape {
  // Cada tool declara seu schema de entrada — veja seções por tool abaixo
}

export interface DisplayTool<TData extends DisplayToolDataShape = DisplayToolDataShape> {
  id:          string                      // identificador único, ex: "bar_chart"
  label:       string                      // nome display (i18n)
  icon:        string                      // emoji ou ícone
  description: string
  defaultW:    number                      // largura padrão em colunas (1-12)
  defaultH:    number                      // altura padrão em row-units
  component:   React.FC<{
    data:       TData                      // response da API já parseado
    config:     Record<string, unknown>    // tool_config do card (título, labels, etc.)
    loading:    boolean
    error:      string | null
  }>
}
```

A API de consulta é responsável por retornar dados no shape correto para cada tool. O frontend **não faz transformação** — apenas renderiza o que recebe.

### Localização

```
platform-ui/src/dashboard/tools/
  types.ts              ← contratos TypeScript
  registry.ts           ← mapa toolId → DisplayTool (importado pelo DashboardsPage)
  MetricCardTool.tsx
  TableTool.tsx
  BarChartTool.tsx
  LineChartTool.tsx
  DonutTool.tsx
```

---

## Tools — Data Shapes

Os 5 tools abaixo estão registrados no `registry.ts`.

### `metric_card` — KPI / Número grande

```typescript
interface MetricCardData {
  value:   number
  label:   string
  trend?:  number        // variação % em relação ao período anterior (positivo = crescimento)
  format:  'number' | 'duration_ms' | 'score' | 'percent'
}
```

Renderiza: número grande centralizado, label abaixo, trend arrow (↑↓) com cor verde/vermelho.
`defaultW: 3, defaultH: 2`

---

### `table` — Tabela MxN

```typescript
interface TableData {
  columns: { key: string; label: string; sortable?: boolean; align?: 'left'|'right'|'center' }[]
  rows:    Record<string, string | number | null>[]
  total?:  number    // total de registros (para paginação futura)
}
```

Renderiza: tabela com cabeçalho fixo, scroll vertical interno, ordenação client-side.
`defaultW: 12, defaultH: 5`

---

### `bar_chart` — Barras verticais

```typescript
interface BarChartData {
  x_labels: string[]
  series:   { name: string; data: number[]; color?: string }[]
  stacked?: boolean    // true = barras empilhadas
  y_label?: string
}
```

Renderiza: barras verticais usando o `TimeseriesChart` existente com `displayType="bar"`.
`defaultW: 6, defaultH: 4`

---

### `line_chart` — Série temporal

```typescript
interface LineChartData {
  x_labels: string[]
  series:   { name: string; data: number[]; color?: string }[]
  y_label?: string
}
```

Renderiza: linhas com pontos, eixo X com labels de data/hora.
`defaultW: 6, defaultH: 4`

---

### `donut` — Proporções

```typescript
interface DonutData {
  labels: string[]
  values: number[]
  total?: number    // se omitido, calculado como sum(values)
}
```

Renderiza: donut chart com legenda lateral, percentuais ao hover.
`defaultW: 4, defaultH: 3`

---

## Card Schema — Novo formato

### Antes (atual)

```typescript
export interface DashboardCard {
  id:     string
  x: number; y: number; w: number; h: number
  type:   DashboardCardType    // union enum hardcoded
  config: TimeseriesCardConfig | KpiCardConfig | PoolStatusCardConfig
}
```

### Depois (novo)

```typescript
export interface QueryParam {
  type:        'fixed'    // valor sempre o mesmo
  value:       unknown
}
| {
  type:        'runtime'  // sobrescrito pelo filtro global do dashboard
  filter_key:  string     // ex: "date_from", "date_to", "pool_id"
  default:     unknown    // usado quando o filtro global não está setado
}

export interface CardQuery {
  endpoint:    string                        // ex: "/reports/display/sessions-by-pool"
  params:      Record<string, QueryParam>   // params do endpoint
}

export interface DashboardCard {
  id:          string
  x: number; y: number; w: number; h: number
  tool_id:     string                        // ID do Display Tool registrado
  title:       string
  query:       CardQuery
  tool_config: Record<string, unknown>       // config específico do tool (eixo labels, etc.)
  refresh_ms?: number                        // intervalo de polling (default: 30_000)
}
```

### Backward compatibility

Cards existentes com `type` e `config` (formato antigo) continuam funcionando via **adapter**:

```typescript
// registry.ts — ao carregar um template
function normalizeCard(raw: any): DashboardCard {
  if (raw.tool_id) return raw            // já no novo formato
  return migrateOldCard(raw)             // converte type+config → tool_id+query
}
```

`migrateOldCard` mapeia os 6 tipos existentes para os novos:
| Tipo antigo | tool_id novo | endpoint |
|---|---|---|
| `timeseries_volume` | `bar_chart` | `/reports/display/session-volume` |
| `timeseries_handle_time` | `line_chart` | `/reports/display/handle-time` |
| `timeseries_score` | `line_chart` | `/reports/display/evaluation-score` |
| `kpi_sessions` | `metric_card` | `/reports/display/kpi-sessions` |
| `kpi_score` | `metric_card` | `/reports/display/kpi-score` |
| `pool_status` | `table` | `/reports/display/pool-status` |

---

## DashboardTemplate — Evolução

Adicionar `global_filters` ao template:

```typescript
export interface GlobalFilter {
  filter_key:   string     // ex: "date_from", "date_to", "pool_id"
  label:        string     // label exibido na filter bar
  type:         'date' | 'select' | 'multi_select'
  options?:     { value: string; label: string }[]   // para select/multi_select
  default:      unknown    // valor inicial
}

export interface DashboardTemplate {
  template_id:    string
  tenant_id:      string
  name:           string
  description?:   string
  cards:          DashboardCard[]
  global_filters: GlobalFilter[]   // NOVO — lista de filtros da filter bar
  created_by:     string
  created_at:     string
  updated_at?:    string
}
```

Os filtros globais existentes não quebram — `global_filters: []` por default.

---

## Runtime Filter Model

### Como funciona

1. O template declara `global_filters` (ex: `date_from`, `date_to`, `pool_id`)
2. O `DashboardsPage` renderiza uma **filter bar** no topo da área de cards com os controles declarados
3. Ao executar uma query, o card substitui todos os `params` com `type: "runtime"` pelos valores correntes da filter bar
4. Params `fixed` nunca são sobrescritos

### Merge de params na execução

```typescript
function buildQueryUrl(card: DashboardCard, runtimeFilters: Record<string, unknown>): string {
  const params = new URLSearchParams()
  for (const [key, param] of Object.entries(card.query.params)) {
    if (param.type === 'fixed') {
      if (param.value !== null && param.value !== undefined) params.set(key, String(param.value))
    } else {
      // runtime: usa filtro global se setado, senão usa default do card
      const value = runtimeFilters[param.filter_key] ?? param.default
      if (value !== null && value !== undefined) params.set(key, String(value))
    }
  }
  return `${card.query.endpoint}?${params.toString()}`
}
```

### Filter Bar — posicionamento

Inserida entre o TopBar e a GridArea, visível em modo view e edit:

```
┌──────────────────────────────────────────────────────────┐
│ TopBar: template name + mode toggle + save               │
├──────────────────────────────────────────────────────────┤
│ FilterBar: [📅 De: ___] [até: ___] [Pool: All ▾] [↺]    │
├────────────┬─────────────────────────────────────────────┤
│  Sidebar   │  react-grid-layout card grid                │
└────────────┴─────────────────────────────────────────────┘
```

---

## Analytics-API — Endpoints de Display

### Princípio

Os endpoints com prefixo `/reports/display/` retornam dados **já no shape do tool**. Eles chamam internamente os endpoints analíticos existentes e formatam o resultado — não duplicam lógica, apenas transformam. O catálogo é declarado em `ENDPOINT_CATALOG` (`catalog.ts`) no frontend.

Todos aceitam: `tenant_id` (required), `from` / `to` (ISO-8601 ou atalhos como `-7d`), `pool_id` (opcional).

### Catálogo (`ENDPOINT_CATALOG`)

| Endpoint | Tool esperado | Descrição |
|---|---|---|
| `GET /reports/display/session-volume` | `bar_chart` | Volume de sessões por intervalo de tempo |
| `GET /reports/display/handle-time` | `line_chart` | Tempo médio de atendimento por período |
| `GET /reports/display/evaluation-score` | `line_chart` | Nota média de avaliação por período |
| `GET /reports/display/sessions-by-pool` | `bar_chart` | Volume de sessões agrupado por pool |
| `GET /reports/display/outcome-distribution` | `donut` | Distribuição de outcomes (resolved/escalated/abandoned) |
| `GET /reports/display/pool-status` | `table` | Status operacional dos pools (disponível, fila, SLA) |
| `GET /reports/display/agent-performance` | `table` | Performance por agent_type (resolution_rate, escalation_rate) |
| `GET /reports/display/kpi-sessions` | `metric_card` | Total de sessões no período com trend vs período anterior |
| `GET /reports/display/kpi-resolution` | `metric_card` | Taxa de resolução com trend |
| `GET /reports/display/kpi-score` | `metric_card` | Nota média de avaliação com trend |

### Cards de Journey (Arc 10 Fase E)

Quatro entradas adicionadas ao `catalog.ts`, com queries `argMax(status, event_time)` sobre `journey_events FINAL`:

| Endpoint / card | Tool | Descrição |
|---|---|---|
| `journey-active-count` | `metric_card` | Total de Journeys ativas |
| `journey-resolution-rate` | `bar_chart` | Taxa de resolução de Journeys por skill_id |
| `journey-funnel` | `donut` | Distribuição de Journeys por status (funil) |
| `journey-median-duration` | `bar_chart` | Duração mediana de Journey por skill_id |

### Cards de Agent Business Events (Arc 12 Fase D)

Dois cards alimentados pelo tópico `agent.events` → `analytics.agent_business_events`, com seletor de categoria dinâmico:

| Endpoint / card | Tool | Descrição |
|---|---|---|
| `agent_event_timeseries` | `line_chart` | Série temporal de um KPI de negócio (`category`) com deploy markers |
| `agent_event_summary` | `table` / `metric_card` | Sumário agregado de eventos de negócio por categoria |

### Formato de resposta

Cada endpoint retorna diretamente o data shape do tool correspondente (sem envelope). Exemplo para `bar_chart`:

```json
{
  "x_labels": ["2026-05-01", "2026-05-02", "2026-05-03"],
  "series": [
    { "name": "retencao_humano", "data": [142, 178, 165] },
    { "name": "suporte_tecnico", "data": [89, 95, 103] }
  ],
  "y_label": "Sessões"
}
```

Erros retornam HTTP 4xx/5xx — o tool exibe mensagem de erro inline.

---

## Card Builder — UX do modal "Add Card"

O `AddCardModal` atual escolhe um preset fixo. O novo modal segue 3 passos:

**Passo 1 — Escolher metric** (o que você quer ver): lista de endpoints disponíveis com nome e ícone.

**Passo 2 — Escolher visualization** (como você quer ver): tools compatíveis com o endpoint selecionado. Cada endpoint declara `compatible_tools: string[]`.

**Passo 3 — Configurar filtros fixos** (opcional): params do endpoint que o usuário quer fixar no card (ex: `pool_id = retencao_humano`). Params não fixados ficam como `runtime` automaticamente.

Título do card é editável no passo 3 com sugestão automática.

---

## Implementação — 4 Partes (todas concluídas)

### Parte 1 — Display Tool Registry (platform-ui) ✅
- `src/dashboard/tools/` com os 5 tools
- `registry.ts` com mapa `toolId → DisplayTool`
- `normalizeCard()` com backward compat adapter
- `CardContent` switch substituído pelo registry lookup
- Cards antigos continuam funcionando via adapter

### Parte 2 — Card Schema + Card Builder ✅
- `DashboardCard` e `DashboardTemplate` evoluídos em `types/index.ts`
- `AddCardModal` com o fluxo de 3 passos
- Catálogo de endpoints declarado em `catalog.ts` (`ENDPOINT_CATALOG`)
- `buildQueryUrl()` com merge de runtime params

### Parte 3 — Runtime Filters ✅
- `global_filters` no `DashboardTemplate`
- `FilterBar` component
- `runtimeFilters` state propagado para todos os cards
- `buildQueryUrl()` integrado com os filtros ativos

### Parte 4 — Analytics-API endpoints `/reports/display/*` ✅
- Endpoints implementados em `analytics-api` (incluindo os cards de Journey e agent_event)
- Cada endpoint chama os existentes (`/reports/segments`, `/reports/agents/performance`, etc.) e formata
- Adapter de data shape centralizado em `analytics_api/display_formatters.py`

---

## Invariants

- Display Tools são **sempre** componentes frontend — zero representação no backend
- O backend **nunca** retorna tipo/tool ID na resposta — o card sabe qual tool usa
- Params `fixed` nunca são sobrescritos por filtros globais
- Cards antigos (formato `type+config`) continuam válidos via `normalizeCard()` — sem migração forçada
- `global_filters: []` é sempre válido — dashboards sem filter bar são suportados
- Novos endpoints `/reports/display/*` **não substituem** os existentes — coexistem

---

## Storage Summary

Sem alteração na camada de storage:
- Templates: Config API `dashboards/template:{uuid}` — JSON completo do `DashboardTemplate`
- Layout pessoal: Config API `dashboards/layout:{tenant}:{user}` — array de `{ id, x, y, w, h }`
- Admin token: `localStorage('plughub_admin_token')` — não vai para o backend
