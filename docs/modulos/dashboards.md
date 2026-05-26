# Módulo: Configuração → Templates de Dashboard

> Última atualização: 2026-05-25 · Estado: Arc 16

> Rota UI: `/dashboards` | Roles: admin (templates); todos os roles (visualização via módulos)

## O que é

O módulo de Dashboards permite que administradores criem e gerenciem **templates** de painel analítico com cards de timeseries arrastáveis e redimensionáveis. Usuários com permissão visualizam esses templates e podem personalizar o layout sem alterar o template compartilhado.

**Nota**: os dados analíticos em si aparecem integrados dentro de cada módulo funcional (Contatos → AnaliseTab, Avaliação → ReportsPage, etc.). O `/dashboards` é exclusivamente para gestão de templates.

## Layout

```
┌──────────────────────────────────────────────┐
│  Sidebar (admin)                             │
│  ├── Lista de templates                      │
│  └── Botão "Novo template"                   │
├──────────────────────────────────────────────┤
│  Grid (react-grid-layout, 12 colunas)        │
│  Cards drag-and-drop + redimensionáveis      │
└──────────────────────────────────────────────┘
```

## DisplayTool registry / `catalog.ts`

Os tipos de card disponíveis são definidos no **DisplayTool registry** — o `ENDPOINT_CATALOG` em `catalog.ts` (`platform-ui`). Cada entrada do catálogo associa um endpoint analytics a um tipo de visualização (`metric_card`, `bar_chart`, `donut`, `line_chart`, etc.) e aos parâmetros aceitos. O backend correspondente vive em `display.py` (analytics-api). O `AddCardModal` lista os cards a partir desse catálogo.

## Tipos de card

| Tipo | Dados | Visualização |
|---|---|---|
| `timeseries_volume` | `/reports/timeseries/volume` | Barra/Linha de volume de sessões no tempo |
| `timeseries_handle_time` | `/reports/timeseries/handle_time` | Linha de tempo médio de atendimento |
| `timeseries_score` | `/reports/timeseries/score` | Linha de score de qualidade médio |
| `pool_status` | `/dashboard/operational` SSE | Status em tempo real dos pools |

### Cards de Journey (Arc 10)

| Card | Tipo | Conteúdo |
|---|---|---|
| `journey-active-count` | metric_card | Jornadas ativas |
| `journey-resolution-rate` | bar_chart | Taxa de resolução por skill_id |
| `journey-funnel` | donut | Funil de status das jornadas |
| `journey-median-duration` | bar_chart | Duração mediana por skill_id |

Queries baseadas em `argMax(status, event_time)` sobre `journey_events FINAL`.

### Cards de Agent Business Events (Arc 12)

| Card | Conteúdo |
|---|---|
| `agent_event_timeseries` | Série temporal de KPIs de negócio com marcadores de deploy; seletor dinâmico de categoria |
| `agent_event_summary` | Resumo agregado por categoria |

### Comparação por deploy (Arc 6 Fase 2)

Cards de observabilidade de mudanças usam eventos de deploy como âncoras temporais: gráfico Índice × Tempo com `ReferenceLine` nos deploys, cards de comparação dual-slice (`/reports/quality-comparison`, `/reports/quality-timeseries`, `/reports/deploy-timeline`).

Cada `TimeseriesChart` suporta:
- **Compact mode** (sparkline + KPI) — para cards pequenos no dashboard
- **Full mode** (interval picker, date range, CSV export) — para visualização expandida
- Breakdown por `pool_id`, `channel` ou `campaign_id`
- `chartType`: bar, line, area

## Templates de dashboard

Templates são armazenados no Config API como `dashboards.template:{uuid}`. Cada template contém:
- Nome e descrição
- Lista de cards com tipo, posição e configuração
- Layout grid (posição x/y, w/h em colunas e linhas)

O `default_template_id` (Config API namespace `dashboards`) define qual template é aberto por default.

## Dois modos de operação

### Modo admin (edição)
- Cria/remove templates
- Adiciona/remove cards (`AddCardModal` com seletor de tipo)
- Drag & drop e resize — persiste no template compartilhado via Config API
- Delete template com confirmação

### Modo usuário (personalização)
- Drag & drop e resize — persiste como layout pessoal (`dashboards.layout:{tenant}:{user}`)
- Não altera o template compartilhado
- Botão "Editar" desabilitado quando sem template selecionado

**Fallback**: quando admin token ausente, layouts pessoais ficam em `localStorage` da sessão.

## Gate ABAC

| Campo | Efeito |
|---|---|
| `config.plataforma` | Exibe nav item "Templates de Dashboard" no grupo Configuração |

## APIs envolvidas

| Endpoint | Descrição |
|---|---|
| `GET /config/dashboards` | Lista configurações do namespace (inclui templates) |
| `PUT /config/dashboards/template:{uuid}` | Salva template |
| `DELETE /config/dashboards/template:{uuid}` | Remove template |
| `PUT /config/dashboards/layout:{tenant}:{user}` | Layout pessoal do usuário |
| `GET /reports/timeseries/volume` | Dados para cards de volume |
| `GET /reports/timeseries/handle_time` | Dados para cards de tempo médio |
| `GET /reports/timeseries/score` | Dados para cards de qualidade |

## Pacotes envolvidos

| Pacote | Responsabilidade |
|---|---|
| `config-api` | Armazena templates e layouts pessoais (namespace `dashboards`) |
| `analytics-api` | Endpoints `/reports/timeseries/*` (ClickHouse) |
| `platform-ui` | `modules/dashboards/DashboardsPage.tsx`, `components/TimeseriesChart/` |

## Dependências frontend

- `react-grid-layout` — drag-and-drop e resize
- `recharts` — renderização dos gráficos de timeseries

## Referências

- Frontend: `packages/platform-ui/src/modules/dashboards/`
- Componente: `packages/platform-ui/src/components/TimeseriesChart/`
- Config API: namespace `dashboards` (default_template_id, allow_user_customization, max_cards_per_dashboard)
