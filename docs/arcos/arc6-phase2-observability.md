# Arc 6 Fase 2 — Observabilidade de Mudanças e Comparação por Deploy

> Última atualização: 2026-06-19 (T16 — correção de verdade) · Estado: **PROPOSTA / NÃO IMPLEMENTADO**
>
> ⚠️ **CORREÇÃO (T16, 2026-06-19): este arco NÃO está implementado.** A versão anterior deste doc
> afirmava "Status: implementado ✅" para as quatro fases (A–D), mas a auditoria do T16 confirmou que
> **nada disso existe no código**:
> - **Backend ausente**: não há tabela `analytics.deploy_events`, nem consumer de `registry.changed`
>   `event_type=skill_deployed` no analytics-api, nem os endpoints `GET /reports/deploy-timeline`,
>   `/quality-comparison`, `/quality-timeseries` (grep zero em `packages/analytics-api`).
> - **UI órfã**: `AnaliseQualidadePage` tem `TimeseriesView`/`ComparisonView`/`MetricSelector` que
>   chamam `/reports/quality-timeseries` e `/reports/quality-comparison`, mas as abas estão
>   **desativadas** (`TAB_IDS = ['summary']`) — código morto apontando para endpoints inexistentes.
>
> O texto abaixo descreve o **alvo de design** (proposta), não o estado atual. Os "✅" nas seções de
> fase são **aspiracionais** e foram mantidos apenas como especificação do que construir. Ver
> `TODO.md` § "Arc 6 Fase 2 — Observabilidade por Deploy (NÃO implementado)". Descoberto no T8-D,
> reconfirmado no T16. Nota: o T11 (relatório Oficial×Operacional) entregou a base de qualidade
> finalizada (`evaluation_finalized`) de forma independente desta Fase 2.
>
> 📌 **Spec de implementação fechada (2026-06-19):** `docs/product/arc6-phase2-deploy-observability-spec.md`
> — aterrada no que existe hoje (pós-T11). Decisão de arquitetura: **a Fase 2 entra como uma NOVA LENTE
> `deploy` no board de Agentes** (`/reports/agents/compare?lens=deploy`), NÃO como página/abas separadas
> (consolidação 2026-06-16 — toda comparação no bench). Os mockups de UI deste doc (gráfico Índice×Tempo,
> card de comparação, painel de grupos) são **a visão**; a entrega real reusa o board e suas lentes.
> Use a spec como fonte de implementação; este doc é a visão.

Extensão da plataforma de avaliação para suportar **comparação estruturada de qualidade ao longo do tempo**, usando eventos de deploy como âncoras temporais objetivas. O objetivo é transformar o módulo de avaliação de "relatório de conformidade" em "ferramenta de melhoria contínua".

## Integração com Arc 12 — Agent Business Events

Os endpoints `quality-comparison` e `quality-timeseries` aceitam métricas de negócio emitidas por agentes via `agent_event` (Arc 12): o parâmetro `metrics[]` aceita o formato `agent_event:{category}` (ex. `metrics[]=agent_event:retencao_humano.skill_retencao_v1.upsell_aceito`), permitindo comparar KPIs de negócio entre deploy epochs e slices da mesma forma que `evaluation_score` ou `resolution_rate`. Ver [`arc12-agent-business-events.md`](arc12-agent-business-events.md).

---

## Problema

O Arc 6 (Fase 1) produz scores de qualidade por sessão, por agente e por campanha. O que falta é responder à pergunta: **uma mudança introduzida melhorou ou piorou os indicadores?**

Exemplos de mudanças que precisam de observabilidade:
- Nova versão de um Skill-Flow (v2 → v3 de `skill_retencao_v1`)
- Agente AI vs agente humano para o mesmo pool
- Antes e depois de um treinamento de equipe
- Dois agentes AI com configurações de prompt diferentes
- Novo script/ferramenta operacional introduzido em data específica

Sem uma primitiva de "âncora temporal objetiva", o gestor é forçado a comparar intervalos de data arbitrários — o que mistura variações sazonais, mudanças de volume e mudanças de qualidade sem distinção.

**Solução:** usar os eventos de deploy (`skill_deployments`) como âncoras naturais. Um deploy é um fato objetivamente datado que delimita "antes" e "depois" de forma inequívoca.

---

## Conceitos

### Dual-Slice Comparison

Modo de consulta que retorna dois conjuntos de métricas calculados independentemente para comparação direta. Os "slices" podem ser definidos por:

| Dimensão de slice | Parâmetro | Exemplo |
|---|---|---|
| Dois agentes | `agent_type_ids_a[]` vs `agent_type_ids_b[]` | Humano vs AI |
| Dois períodos | `period_a` vs `period_b` | Antes vs depois do treinamento |
| Dois deploys | `deploy_id_a` vs `deploy_id_b` | v2 vs v3 do Skill-Flow |
| Versão vs versão | `skill_id` + versão inferida por deploy | Automático |

O resultado é sempre o mesmo shape de resposta — só muda o que delimita cada grupo.

### Deploy Epoch

Um "epoch" é o intervalo de tempo entre dois deploys consecutivos do mesmo skill. Cada epoch tem início (`deployed_at` do deploy N) e fim (`deployed_at` do deploy N+1, ou "até agora"). As métricas calculadas dentro de um epoch representam o desempenho **daquela versão**.

```
skill_retencao_v2  ──────[deploy v2.1]──────[deploy v2.2]────[deploy v2.3]──▶ agora
                         epoch_1              epoch_2          epoch_3
```

### Deploy Timeline

Sequência de deploys para um skill (ou todos os skills de um pool), com timestamps e metadados, usada como eixo X de referência nos gráficos.

---

## Arquitetura de Dados

### Tabelas existentes aproveitadas

| Tabela | Conteúdo relevante | Localização |
|---|---|---|
| `skill_deployments` | `skill_id`, `deployed_at`, `deploy_status` | agent-registry PostgreSQL |
| `evaluation_results` | `session_id`, `agent_type_id`, `scores`, `created_at` | evaluation-api PostgreSQL → ClickHouse |
| `analytics.segments` | `agent_type`, `outcome`, `duration_ms`, `started_at` | ClickHouse |
| `mv_agent_performance_daily` | `resolution_rate`, `escalation_rate`, `aht_ms` por dia | ClickHouse |

### Tabela `deploy_events` (ClickHouse)

Cruza deploys com métricas em ClickHouse sem depender de JOIN cross-database:

```sql
CREATE TABLE IF NOT EXISTS analytics.deploy_events (
    tenant_id        String,
    skill_id         String,
    deploy_id        String,
    version_label    String,    -- tag semântica opcional (ex: "v2.1-hotfix")
    deployed_at      DateTime,
    deployed_by      String,    -- user_id ou "system"
    deploy_status    String,    -- "published" | "rolled_back"
    metadata         String     -- JSON livre
) ENGINE = ReplacingMergeTree(deployed_at)
ORDER BY (tenant_id, skill_id, deploy_id);
```

Alimentada por: `agent-registry` publicando `registry.changed` Kafka com `event_type: "skill_deployed"` → consumer em `analytics-api`.

---

## Endpoints

Os três endpoints abaixo existem no `analytics-api`.

### 1. Deploy Timeline

```
GET /reports/deploy-timeline
  ?tenant_id=
  &skill_id=         (optional — all skills if omitted)
  &date_from=
  &date_to=

Response: {
  deploys: [
    {
      deploy_id:     string
      skill_id:      string
      version_label: string
      deployed_at:   string  // ISO
      deployed_by:   string
    }
  ]
}
```

Usado pelo frontend para desenhar os marcadores verticais no eixo X dos gráficos.

---

### 2. Quality Comparison (Dual-Slice)

```
GET /reports/quality-comparison
  ?tenant_id=
  &slice_a=<json>    // {type, params}
  &slice_b=<json>
  &metrics[]=        // evaluation_score | resolution_rate | escalation_rate | aht_ms | nps

Tipos de slice:
  { type: "agent_types", agent_type_ids: string[] }
  { type: "period", date_from: string, date_to: string }
  { type: "deploy_epoch", deploy_id: string }           // início=deploy_id, fim=próximo deploy
  { type: "deploy_range", from_deploy_id, to_deploy_id }

Response: {
  slice_a: {
    label:              string
    session_count:      number
    metrics: {
      evaluation_score?:  { avg, p50, p90, std_dev }  // apenas se evaluation disponível
      resolution_rate?:   number
      escalation_rate?:   number
      aht_ms?:            { avg, p50, p90 }
      nps?:               { avg, count }
    }
    criteria_scores?: {   // apenas se evaluation_score incluído
      [criterion_id]: { avg, label }
    }[]
  }
  slice_b: { ... }   // mesmo shape
  delta: {           // diferença slice_b - slice_a (absoluta e relativa)
    evaluation_score?: { abs, pct }
    resolution_rate?:  { abs, pct }
    escalation_rate?:  { abs, pct }
    aht_ms?:           { abs, pct }
    nps?:              { abs, pct }
  }
  statistical_significance: {
    sample_a: number
    sample_b: number
    sufficient: boolean   // true se ambos >= MIN_SAMPLE (default 30)
    warning?: string      // "Amostra A insuficiente (N=5, mínimo 30)"
  }
}
```

---

### 3. Index × Time Series

```
GET /reports/quality-timeseries
  ?tenant_id=
  &agent_type_ids[]=
  &skill_id=
  &metric=          // evaluation_score | resolution_rate | escalation_rate | aht_ms
  &granularity=     // day | week | deploy_epoch
  &date_from=
  &date_to=

Response: {
  series: [
    {
      date:         string   // início do período (ISO)
      value:        number
      session_count: number
      deploy_id?:   string   // preenchido quando granularity=deploy_epoch
    }
  ]
  deploy_markers: [   // sempre incluído quando skill_id é informado
    {
      deploy_id:  string
      skill_id:   string
      deployed_at: string
      label:      string
    }
  ]
}
```

O frontend usa `deploy_markers` para desenhar linhas verticais sobre a série temporal.

---

## Componentes de UI

### C1 — Gráfico Índice × Tempo com Markers de Deploy

**Localização:** nova seção "Evolução" nas páginas Analytics (Sessions, Agents/Quality) e na página de Avaliações.

**Comportamento:**
- Linha de métricas (evaluation_score, resolution_rate, AHT) no eixo Y, tempo no eixo X.
- Linhas verticais tracejadas nos momentos de deploy, com tooltip mostrando `version_label` + `deployed_by` + data.
- Toggle de métricas (checkbox por métrica, múltiplas podem aparecer sobrepostas com eixo Y duplo se escalas forem muito diferentes).
- Filtro de agente: humano, AI específico, ou todos.
- Granularidade: dia / semana / por epoch de deploy.

**Implementação:** Recharts `LineChart` + `ReferenceLine` para os markers. Dados via `GET /reports/quality-timeseries`.

---

### C2 — Card de Comparação de Versões

**Localização:** sidebar do gráfico C1 (aparece ao selecionar dois deploys) + tab "Comparação" na página de Avaliações.

**Comportamento:**
- Seleção de Slice A e Slice B via dropdowns (tipo + parâmetros).
- Exibe delta por métrica com indicadores visuais: ▲ verde (melhora), ▼ vermelho (piora), → cinza (sem diferença significativa).
- Drill-down por critério de avaliação quando `evaluation_score` está disponível.
- Badge de aviso quando amostra insuficiente (`statistical_significance.sufficient = false`).
- Botão "Exportar comparação" → gera PDF ou XLSX com o relatório (reutiliza skills docx/pdf existentes).

**Métricas exibidas no card:**
```
┌─────────────────────────────────────────────────────────┐
│ v2.1 (15 dias, N=342)     vs     v2.2 (12 dias, N=289) │
├──────────────────────────┬──────────────────────────────┤
│ Score avaliação    82.4  │ 85.1   ▲ +3.2%              │
│ Taxa resolução     71%   │ 74%    ▲ +4.2%              │
│ Taxa escalação     12%   │ 9%     ▲ -2.5%              │
│ TMA                4m32s │ 4m18s  ▲ -5.1%              │
│ NPS                62    │ 67     ▲ +8.1%              │
├──────────────────────────┴──────────────────────────────┤
│ Critério: Saudação        81 → 87   ▲ +7.4%            │
│ Critério: Resolução       79 → 83   ▲ +5.1%            │
│ Critério: Tom             86 → 86   →  0.0%            │
└─────────────────────────────────────────────────────────┘
```

---

### C3 — Painel de Grupos de Comparação

**Localização:** página dedicada `Analytics > Comparação` (nova rota `/contacts?tab=comparacao` ou `/analytics/comparison`).

**Comportamento:**
- Interface de "adicionar grupo" — cada grupo é um slice (agente, período, ou epoch de deploy).
- Até 4 grupos simultâneos (limitação visual, não técnica).
- Gráfico de barras agrupadas por métrica (um grupo = uma cor).
- Útil para: comparar múltiplos agentes AI na mesma visualização, ou avaliar uma série de versões sequenciais.

---

## Fases de Implementação — PLANO (nenhuma implementada)

> Os "✅" abaixo são **aspiracionais** (plano de implementação), **não** estado entregue — ver o
> banner de correção no topo (T16). Nenhuma das fases A–D existe no código hoje.

### Fase A — Infraestrutura de Deploy Events *(não implementado)*

**Backend:**
1. `analytics-api`: consumer `registry.changed` com `event_type: "skill_deployed"` → `INSERT INTO analytics.deploy_events`.
2. `agent-registry`: publica `registry.changed` com `event_type: "skill_deployed"` quando `POST /v1/skills/:id/deploy` é chamado.
3. `GET /reports/deploy-timeline` no analytics-api.

Fase A foi infraestrutura pura, sem mudança de UI.

---

### Fase B — Quality Comparison Endpoint + Card de Comparação (C2) *(não implementado)*

**Backend:**
1. `GET /reports/quality-comparison` (dual-slice) no analytics-api — ClickHouse queries com parâmetros de slice.
2. Lógica de `deploy_epoch`: busca o próximo deploy para calcular o intervalo.
3. `statistical_significance` automático (N >= 30 por default, configurável).

**Frontend:**
1. `ComparisonCard` — dropdowns de slice, tabela de deltas, badges de significância.
2. Integrado na página de Avaliações (tab/sidebar).

---

### Fase C — Index × Time com Deploy Markers (C1) *(não implementado — UI órfã desativada)*

**Backend:**
1. `GET /reports/quality-timeseries` com `deploy_markers[]`.
2. Granularidade `deploy_epoch` — agrega por janela de versão.

**Frontend:**
1. `QualityTimeseriesChart` (Recharts + ReferenceLine).
2. Integrado em Analytics > Sessions e Analytics > Quality.
3. Toggle de granularidade + filtro de agente.

---

### Fase D — Painel de Grupos de Comparação (C3) *(não implementado)*

**Frontend:**
1. Rota dedicada de comparação em Analytics.
2. `ComparisonGroupBuilder` — adiciona/remove slices com persistência em localStorage.
3. Gráfico de barras agrupadas por métrica.

Fase D reutiliza os endpoints das Fases B e C — sem novo backend.

---

## Dependências

| Dependência | Arc | Status |
|---|---|---|
| `evaluation_results` em ClickHouse | Arc 6 Fase 1 | Existente |
| `mv_agent_performance_daily` | Arc 5 | Existente |
| `skill_deployments` table | Arc 4 (Skill Deploy) | Existente |
| `registry.changed` Kafka | Agent Registry | Existente |
| `analytics-api` consumer infrastructure | Arc 5/6 | Existente |
| i18n namespace `analytics` | platform-ui | Existente |

---

## Métricas de Sucesso

- Gestor consegue comparar v_anterior vs v_atual de um Skill-Flow em < 3 cliques.
- Card de comparação detecta e avisa amostras insuficientes (< 30 sessões).
- Gráfico de índice × tempo com deploy markers disponível em 100% das campanhas ativas com ≥ 1 deploy registrado.
- Exportação de relatório de comparação disponível em PDF e XLSX.
