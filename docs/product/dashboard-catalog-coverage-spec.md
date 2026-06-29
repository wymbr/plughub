# Dashboards composáveis — cobertura de catálogo (não é Grafana novo)

> Spec fechada (2026-06-28). Ideia (a): "criar todos os gráficos da plataforma como componentes e montar
> dashboards com qualquer gráfico, estilo Grafana". **Achado: o sistema composável já existe** (Dashboard #35 /
> Arc 16). O que falta é **cobertura** — expor no catálogo os relatórios que ainda não viraram card. Esta spec
> trata só disso e fecha a decisão de **não** construir a camada de datasource/query-builder do Grafana.

## 1. Estado atual (já implementado — ver `docs/arcos/dashboard.md`)

- **DisplayTool registry** (`platform-ui/src/dashboard/tools/`): 5 tools — `metric_card`, `table`,
  `bar_chart`, `line_chart`, `donut`. Gráfico = componente registrado.
- **DashboardsPage**: react-grid-layout (drag/resize), templates compartilhados (admin) + layout pessoal
  por usuário, modo edit/view, ABAC.
- **Add Card em 3 passos**: métrica (endpoint) → visualização (`compatible_tools`) → filtros fixos; resto vira
  `runtime`. **FilterBar** global (date/pool/etc.).
- **analytics-api `/reports/display/*`** + `ENDPOINT_CATALOG` (`catalog.ts`): cada endpoint retorna o data
  shape do tool; `display_formatters.py` reusa os relatórios analíticos existentes (sem duplicar lógica).
- **Catálogo hoje** (~16 entradas): session-volume, handle-time, evaluation-score, sessions-by-pool,
  outcome-distribution, pool-status, agent-performance, kpi-sessions, kpi-resolution, kpi-score, +
  journey (4) + agent_event (2).

## 2. Decisão: cobertura por catálogo curado, NÃO datasource genérico

Confirmada a postura (responde "é tudo interno, ainda faz sentido?"):
- **Sim faz sentido — e o modelo já é o certo justamente por ser interno.** Os dados vivem em endpoints
  conhecidos (analytics-api/ClickHouse). O valor do Grafana (montar painel sem código, reusar componentes,
  painel por perfil) **já está entregue** pelo registry + grid + Add Card.
- **Não construir**: camada de **datasource genérica** nem **query-builder** (SQL/PromQL livre). Seria a parte
  cara do Grafana e desnecessária — além de furar invariantes (acesso a dado só via endpoints, ABAC/scope,
  tenant isolation). Dado interno + catálogo curado é o encaixe.

## 3. Escopo: o que falta (cobertura)

Tornar **cada relatório relevante da plataforma** disponível como card, via o contrato que já existe
(endpoint `/reports/display/*` + entrada no `ENDPOINT_CATALOG` + `compatible_tools`). Candidatos ainda
ausentes do catálogo (confirmar 1:1 no `catalog.ts` antes de implementar):
- **Segmentos / complexidade** (`/reports/segments`, `/reports/sessions/complexity`).
- **Disponibilidade de agente** (Arc 8 — `/reports/agent-availability`, pausas por motivo).
- **Fila / SLA** (modelo de fila sempre atendida — volume, fila, SLA, max_wait).
- **Pools / Infra** (volume, capacidade, SLA por pool).
- **Qualidade** (`/reports/evaluations`, `/summary`) + **calibração de avaliador** (Arc 13).
- **Surveys / Voz do cliente** (CSAT/NPS/CES — quando o módulo de pesquisas existir).
- **Performance diária por agente** (`/reports/agent-performance/daily`).

> Para cada um: (1) endpoint `/reports/display/{slug}` que **reusa** o relatório existente e formata no shape do
> tool (em `display_formatters.py`); (2) entrada no `ENDPOINT_CATALOG` com `compatible_tools` + params
> (`fixed`/`runtime`); (3) i18n do label. **Sem novo tool** quando os 5 existentes servem.

## 4. Novos tools (só se um relatório não couber nos 5)
Avaliar caso a caso. Candidatos plausíveis: **heatmap** (disponibilidade por hora×dia), **stat list / leaderboard**
(ranking de agentes), **gauge** (SLA vs alvo). Cada novo tool segue o contrato `DisplayTool` (componente front,
zero backend) — adicionar só sob demanda real, não especulativo.

## 5. Modelo de consumo — Home + allowlist por role (decidido 2026-06-28)

> Separa **construir** (admin, em Config) de **consumir** (usuário, no Home). Resolve "onde o usuário acessa
> dashboards customizados". A infra de **layout pessoal por usuário** já existe
> (`dashboards/layout:{tenant}:{user}` = `[{id,x,y,w,h}]`) + react-grid-layout — então o custo é baixo.

### 5.1 Papéis
- **Admin (Config → Dashboards)** define, por role, **(a)** a **allowlist** de componentes liberados e
  **(b)** um **starter default** (subconjunto já arrumado, p/ o primeiro acesso não vir vazio). Só isso —
  não monta o painel de cada usuário.
- **Usuário (Home)** escolhe **quais** componentes (dentro da allowlist) e **arruma o layout**; salvo como
  layout pessoal. Primeiro acesso = herda o starter da role.
- **Monitor/Analytics** inalterados — visões profundas/ao vivo; os cards do Home dão **deep-link** pra elas.

### 5.2 Onde mora
- **Home** (`/` → `HomePage`, visível a todas as roles) **renderiza o dashboard do usuário**. Hoje a
  `DashboardsPage` (o builder) está só em `Config → Dashboards` atrás de `config.platform` (≈ admin) — o
  **consumo precisa ser destravado no Home p/ todas as roles** (o builder continua gated em Config).
- **Escopo automático**: a allowlist controla **o quê**; ABAC/`accessible_pools`/`supervised_*` (aplicados no
  endpoint `/reports/display/*`) controlam **quais dados**. Dois usuários, mesmo card, dados diferentes.

### 5.3 Armazenamento (Config API namespace `dashboards`)
- `role_catalog:{role}` (novo) → `{ allowed: string[] /* catalog ids */, starter: DashboardCard[] }`.
- `layout:{tenant}:{user}` (já existe) → seleção + posições do usuário.
- **Reconcile no load** (única lógica nova de robustez): o layout do usuário é **filtrado contra a allowlist
  atual** — card cujo componente saiu da allowlist é silenciosamente descartado; componente novo fica
  disponível no picker (não auto-adicionado). Sem migração.

### 5.4 Por que híbrido (starter + liberdade), não fixo nem vazio
Starter resolve o "primeiro acesso vazio"; a liberdade dentro da allowlist resolve "usuário preso a um painel
que não serve". Custo extra vs. starter puro = só o **picker restrito** + o **reconcile** — ambos baratos.

## 6. Fases
- **F1 — Cobertura de catálogo** (§3): plugar relatórios faltantes como `/reports/display/*` + entradas no
  catálogo, incrementalmente (1–2 por vez). Independe das demais.
- **F2 — Consumo no Home**: `HomePage` passa a renderizar o dashboard do usuário (reusa `DashboardsPage`
  viewer); destravar visualização p/ todas as roles (builder segue em Config/admin).
- **F3 — Allowlist + starter por role**: `role_catalog:{role}` no Config API + UI de admin (em Config →
  Dashboards) p/ definir allowed/starter; reconcile no load.
- **F4 — Picker do usuário**: "adicionar/remover card" no Home restrito à allowlist da role; layout pessoal.
- **F5 (opcional) — novos tools** (§4) sob demanda (heatmap/gauge/leaderboard).

## 7. Invariantes (do módulo, preservados)
- Display Tool é **sempre** componente front; backend nunca retorna tool id.
- Endpoint `/reports/display/*` **reusa** relatório existente — não duplica lógica de query.
- Param `fixed` nunca sobrescrito por filtro global; `global_filters: []` válido.
- Scope (ABAC, `accessible_pools`, `supervised_agent_types`) é aplicado **no endpoint analítico** — o card
  herda; nunca expor dado fora do escopo do principal.
- Inglês no código; PT só em i18n.

## 8. Fora de escopo
- Datasource genérico / query-builder (SQL/PromQL livre) — decisão explícita de NÃO fazer.
- Alerting estilo Grafana (thresholds → notificação) — arco próprio se um dia for requisito.
- Export/embedding de painel.
- Edição de capability/permissão por aqui (não é deste módulo).
