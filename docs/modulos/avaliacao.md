# Módulo: Avaliação

> Última atualização: 2026-05-25 · Estado: Arc 16

> Rota UI: `/evaluation/*` | Roles: operator (avaliações), supervisor + admin (tudo)

## O que é

O módulo Avaliação é a plataforma de qualidade do PlugHub. Permite criar formulários de avaliação, definir campanhas de amostragem, executar avaliadores IA com RAG sobre base de conhecimento, gerenciar ciclos de revisão e contestação humana, e gerar relatórios analíticos de qualidade — tanto para agentes IA quanto humanos.

## Abas / Rotas

| Rota | Arquivo | Roles | Descrição |
|---|---|---|---|
| `/evaluation/forms` | `FormsPage.tsx` | admin | CRUD de formulários com critérios |
| `/evaluation/campaigns` | `CampaignsPage.tsx` | supervisor, admin | Campanhas + KPIs em tempo real |
| `/evaluation/knowledge` | `KnowledgePage.tsx` | admin | Base de conhecimento vetorial (RAG) |
| `/evaluation/avaliacoes` | `AvaliacoesPage.tsx` | operator, supervisor, admin | Tabela unificada de avaliações com filtros e ações |
| `/evaluation/reports` | `ReportsPage.tsx` | supervisor, admin | Dashboard analítico (analytics-api) |
| `/evaluation/calibration` | `CalibrationDashboard.tsx` | supervisor, admin | Dashboard de calibração do avaliador IA (Arc 13) |
| `/evaluation/curadoria` | `CuradoriaPage.tsx` | supervisor, admin | Fila de curadoria humana das avaliações de agentes IA (Arc 13) |

## Gate ABAC

| Campo | Efeito |
|---|---|
| `evaluation.formularios` | Exibe Forms e Campaigns |
| `evaluation.contestar` | Habilita botão "Contestar" na tabela de avaliações |
| `evaluation.revisar` | Habilita botão "Revisar" na tabela de avaliações |
| `evaluation.relatorio` | Exibe Reports |

Permissões de contestar/revisar são verificadas **server-side** via ABAC JWT — a UI apenas lê o campo `available_actions` retornado pelo servidor. Nunca computa permissão localmente.

## Formulários (FormsPage)

Criar e gerenciar `EvaluationForm` com múltiplos critérios:

```typescript
EvaluationCriterion {
  id:          string   // criterion_id único no formulário
  label:       string   // "Seguiu protocolo de saudação"
  description: string   // instrução para o avaliador IA
  weight:      number   // 0.0–1.0; soma dos pesos = 1.0
  type:        "score" | "pass_fail" | "text" | "na_allowed" | "auto_computed"
}
```

**Critérios `auto_computed`** (Arc 13): preenchidos automaticamente pelo `SessionMetricsExtractor` após `evaluation_finalized`, antes de o avaliador IA processar o formulário. O avaliador IA pula esses critérios — o score já está calculado a partir de métricas objetivas da sessão (AHT, escalation, etc.).

## Campanhas (CampaignsPage)

Campanhas definem **quais** sessões são avaliadas e **como**:

- **Sampling**: `all`, `random` (com taxa), `pool_filter`, `segment_filter`
- **Reviewer Rules**: `auto_approve_above` / `auto_reject_below` / `require_human_review`
- **`review_workflow_skill_id`**: skill YAML que governa o ciclo de revisão (ex: `skill_revisao_simples_v1`, `skill_revisao_treplica_v1`)
- **Contestation Policy** (Arc 13): campos `reviewer_type` (`ai` / `human` / `ai_then_human`), `max_rounds` (padrão 3, máx 5), `contest_deadline_hours`, `use_business_hours`, `pre_review_enabled`, `pre_review_agent_pool`

O sampling engine cria instâncias automaticamente ao consumir `conversations.session_closed`.

### Regras de curadoria (Arc 13)

Para campanhas que avaliam **agentes IA** (Fluxo 2), o `CreateModal` — gateado por `isArc13Skill = workflowSkillId === 'skill_revisao_treplica_v1'` — habilita o editor de regras de curadoria amostral. Cada `CurationSamplingRule` define uma regra de amostragem para revisão humana das avaliações IA. Seis tipos (`CurationRuleType`):

| Tipo | Quando amostra |
|---|---|
| `score_extremes` | Avaliações com score muito alto ou muito baixo |
| `deploy_baseline` | Baseline amostrado em torno de eventos de deploy |
| `score_outlier` | Scores estatisticamente atípicos |
| `na_excess` | Excesso de critérios marcados N/A |
| `random_baseline` | Amostra aleatória de referência |
| `reviewer_signal` | Acionado quando o revisor IA emite `calibration_signal` |

O `CurationSamplingRulesEditor` mantém a lista ordenada por prioridade; as regras são persistidas em um segundo request após criar a campanha. No detalhe da campanha, o `CurationSamplingRulesDetailPanel` permite leitura/edição.

## Avaliações (AvaliacoesPage)

Tabela unificada de todos os resultados com filtros:
- Por status: submitted / under_review / reviewed / contested / locked
- Por campanha, avaliador, pool
- "Aguardando minha ação" — filtra por `action_required` do caller

**Drill-down lateral**: o `DetailPanel` detecta automaticamente `isArc13 = threads.length > 0` e renderiza um de dois modos:

- **Modo Arc 13** (com `ContestationThread`): contestação e revisão **por dimensão**.
  - `DimensionStateIndicator` — dot colorido por estado da dimensão (`DimensionState`)
  - `DimensionThreadCard` — card expansível com o thread completo de cada round (append-only, imutável), incluindo as `evidence_entries` obrigatórias por dimensão
  - `HumanReviewPanel` — decisão `upheld`/`revised` por dimensão, com `score_override` e justificativa ≥ 20 palavras
  - `DimensionContestPanel13` — checkbox por dimensão + `reason` ≥ 10 palavras
- **Modo Arc 6** (fallback transparente, sem threads): `ContestPanel` / `ReviewPanel` legados.

**Anti-replay**: o campo `round` no body do submit deve ser igual a `result.current_round` — caso contrário o servidor retorna 409.

Hooks: `useContestationThreads` (`GET /threads`), `submitHumanReview` (`POST /review`), `submitDimensionContestation` (`POST /contest`).

## Base de Conhecimento (KnowledgePage)

Snippets vetorizados (pgvector / text-embedding-3-small) usados pelo agente avaliador via RAG. Organizados por `namespace` (ex: `politicas_sac`, `sla_contrato`).

| Endpoint | Descrição |
|---|---|
| `GET /v1/knowledge/search` | Busca semântica top-K no namespace |
| `POST /v1/knowledge/snippets` | Upsert snippet (com embedding automático) |
| `DELETE /v1/knowledge/snippets/{id}` | Remove snippet |

## Agente Avaliador (agente_avaliacao_v1)

Fluxo:
```
carregar_contexto (evaluation_context_get)
  → ReplayContext: stream events + form definition + top-5 knowledge_snippets

avaliar_criterios (reason LLM)
  → score/pass_fail/NA por critério + evidence_entries[] obrigatórias por dimensão
  → critérios auto_computed são pulados (já preenchidos pelo Extractor)
  → calibration_notes injetadas no prompt do LLM
  → overall_score ponderado por weights
  → compliance_flags (sla_breached, escalation_required)

submeter_resultado (evaluation_submit)
  → criterion_responses[], overall_score, compliance_flags
  → eval_status: "submitted"
  → dispara trigger de workflow de revisão
```

## Dois fluxos de revisão por tipo de agente avaliado (Arc 13)

O Arc 13 separa o ciclo de qualidade em **dois fluxos distintos**, escolhidos pelo tipo do agente avaliado (`evaluated_agent_type`):

### Fluxo 1 — Agente humano: revisão pré-publicação + contestação

1. **Revisor IA pré-publicação** (`agente_pre_revisor_v1`) — gate de qualidade configurável por campanha (`pre_review_enabled` + `pre_review_agent_pool`). Atua **antes** de o resultado ser publicado ao agente avaliado, melhorando a qualidade da avaliação e reduzindo contestações evitáveis. Pode emitir `calibration_signal` para padrões sistemáticos.
2. **Contestação por dimensão** — o agente humano contesta dimensões específicas; cada contestação exige `evidence_entries`. O `ContestationThread` é append-only e imutável.
3. **Árbitro pós-contestação** (`agente_revisor_v1`) — quando `reviewer_type == "ai"`, decide `upheld`/`revised` por dimensão contestada.
4. **Human reviewer sempre tem a decisão final** — quando `reviewer_type == "human"` ou `"ai_then_human"`, o `skill_revisao_treplica_v1` roteia para um suspend de revisão humana.

### Fluxo 2 — Agente IA: finalização imediata + curadoria amostral

1. **`evaluation_finalized` imediato** — sem contestação. A avaliação de um agente IA é finalizada assim que submetida; é a única fonte de verdade para os relatórios de qualidade.
2. **Curadoria amostral paralela** — o Sampling Engine avalia as 6 regras de `CurationSamplingRule` (ver Campanhas) em background após `evaluation_finalized`. As avaliações amostradas vão para a fila de curadoria humana (`CuradoriaPage`).
3. O curador humano complementa o sinal do revisor IA; o resultado vira uma `CalibrationNote` publicada no knowledge namespace `evaluation:calibration:{campaign_id}` — feedback ao avaliador IA via RAG.

### Motor de revisão via Workflow

O ciclo do Fluxo 1 é governado pelo Workflow API. O YAML da skill é o único lugar com lógica de quantos rounds existem.

```
EvaluationResult submetido
  → POST /v1/workflow/trigger { flow_id: campaign.review_workflow_skill_id }
  → workflow suspende em "aguardar_revisao"
  → workflow.events consumer → result.action_required, current_round, deadline_at

Revisor / contestante age → evaluation-api escreve session.review_decision
  no ContextStore → POST /v1/workflow/resume

Timeout sem ação → locked = true → 409 em tentativas posteriores
```

Skills de revisão disponíveis:
- `skill_revisao_simples_v1` — 1 round (aprovação/rejeição simples)
- `skill_revisao_treplica_v1` v2.0 — roteia para IA (`dispatch_revisor_ai`) ou humano (`aguardar_revisao_humana` suspend) conforme `reviewer_type`; suporta `ai_then_human` com fallback; `max_rounds` dinâmico; timeout → `congelar_resultado`

## Reports (ReportsPage)

Dashboard analítico com métricas agregadas das campanhas:

| Métrica | Descrição |
|---|---|
| `total_evaluated` | Total de sessões avaliadas |
| `avg_score / min_score / max_score` | Distribuição de scores |
| Score buckets | excellent (≥0.9), good (0.7–0.9), fair (0.5–0.7), poor (<0.5) |
| `count_approved / rejected / contested / locked` | Status breakdown |
| `with_compliance_flags` | Sessões com flags de compliance |

**Fonte**: `GET /reports/evaluations` + `GET /reports/evaluations/summary` (analytics-api → ClickHouse `evaluation_results` + `evaluation_events`).

## Calibration Dashboard (CalibrationDashboard — Arc 13)

Em `/evaluation/calibration`. Mede a calibração do avaliador IA ao longo do tempo: KPI strip, `LineChart` de `calibration_score` por skill version × tempo (`ReferenceLine` em 90%), tabela detalhada. `calibration_score = approved / total × 100`. Correlaciona a melhora do avaliador com epochs de deploy (Arc 6 Fase 2).

**Fonte**: `GET /reports/evaluator-calibration` (analytics-api → ClickHouse `calibration_events`). Hook `useEvaluatorCalibration` (`CalibrationPoint`, `CalibrationSummary`).

## Curadoria (CuradoriaPage — Arc 13)

Em `/evaluation/curadoria`. Fila de curadoria humana das avaliações de **agentes IA** (Fluxo 2): KPI strip, filtros, polling 15 s. Cada `CurationCard` mostra os trigger badges (qual regra de curadoria amostrou), o preview do sinal do revisor IA e 3 ações. O `RecalibrateDrawer` permite ao curador complementar o sinal — a resolução publica uma `CalibrationNote` no knowledge namespace via `httpx POST /v1/knowledge/snippets` e emite `calibration_note_published`.

## Pacotes envolvidos

| Pacote | Responsabilidade |
|---|---|
| `evaluation-api` | Ciclo de vida de forms, campaigns, instances, results, contestations, threads, curation, calibration (porta 3400) |
| `mcp-server-knowledge` | Base de conhecimento vetorial (pgvector) — MCP tools para o agente avaliador (porta 3401) |
| `session-replayer` | Constrói `ReplayContext` com transcrição, form e snippets; Kafka consumer `evaluation.requested` |
| `workflow-api` | Motor de estado do ciclo de revisão/contestação |
| `analytics-api` | Consumers `evaluation.events` + `calibration.events` → ClickHouse; endpoints `/reports/evaluations*`, `/reports/evaluator-calibration` |
| `platform-ui` | `modules/evaluation/` — 7 páginas |

## Kafka topics

| Tópico | Produtor | Consumidor |
|---|---|---|
| `evaluation.events` | evaluation-api (submit/review/contest/lock) | analytics-api → ClickHouse `evaluation_results` + `evaluation_events` |
| `calibration.events` | evaluation-api (`calibration_reviewed`, `calibration_note_published`) | analytics-api → ClickHouse `calibration_events` |

## Referências

- Schemas: `packages/schemas/src/evaluation.ts`
- Backend: `packages/evaluation-api/`, `packages/mcp-server-knowledge/`, `packages/session-replayer/`
- Frontend: `packages/platform-ui/src/modules/evaluation/`
- Skills de avaliação: `packages/skill-flow-engine/skills/agente_avaliacao_v1.yaml`, `skill_revisao_simples_v1.yaml`, `skill_revisao_treplica_v1.yaml`
- Agentes Arc 13: `infra/registry/agents/agente_pre_revisor_v1.yaml`, `agente_revisor_v1.yaml`
- Arc 13 (revisão, contestação, calibração): `docs/arcos/arc13-review-contestation.md`
