# Módulo: Avaliação

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
  type:        "score" | "pass_fail" | "text" | "na_allowed"
}
```

## Campanhas (CampaignsPage)

Campanhas definem **quais** sessões são avaliadas e **como**:

- **Sampling**: `all`, `random` (com taxa), `pool_filter`, `segment_filter`
- **Reviewer Rules**: `auto_approve_above` / `auto_reject_below` / `require_human_review`
- **`review_workflow_skill_id`**: skill YAML que governa o ciclo de revisão (ex: `skill_revisao_simples_v1`, `skill_revisao_treplica_v1`)
- **Contestation Policy**: rounds configuráveis, prazos por round, alçada (`supervisor/manager/director`)

O sampling engine cria instâncias automaticamente ao consumir `conversations.session_closed`.

## Avaliações (AvaliacoesPage)

Tabela unificada de todos os resultados com filtros:
- Por status: submitted / under_review / reviewed / contested / locked
- Por campanha, avaliador, pool
- "Aguardando minha ação" — filtra por `action_required` do caller

**Drill-down lateral**: cada linha abre painel com:
- Critérios avaliados com score/pass_fail/NA e evidências (trechos da transcrição)
- `ContestPanel` (se `available_actions` inclui `"contest"`): contestação por critério, textarea ≥ 30 chars
- `ReviewPanel` (se `available_actions` inclui `"review"`): revisão com notas por critério, nota geral obrigatória para `adjusted_approved`/`rejected`

**Anti-replay**: o campo `round` no body do submit deve ser igual a `result.current_round` — caso contrário o servidor retorna 409.

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
  → score/pass_fail/NA por critério + evidência textual
  → overall_score ponderado por weights
  → compliance_flags (sla_breached, escalation_required)

submeter_resultado (evaluation_submit)
  → criterion_responses[], overall_score, compliance_flags
  → eval_status: "submitted"
  → dispara trigger de workflow de revisão
```

## Ciclo de Revisão/Contestação via Workflow

O ciclo é governado pelo Workflow API. O YAML da skill é o único lugar com lógica de quantos rounds existem.

```
EvaluationResult submetido
  → POST /v1/workflow/trigger { flow_id: campaign.review_workflow_skill_id }
  → workflow entra no suspend "aguardar_revisao"
  → workflow.events consumer → result.action_required = "review", current_round = 1, deadline_at

Revisor age:
  POST /v1/evaluation/results/{id}/review { decision: "approved"|"rejected", round: 1 }
  → anti-replay check (round == current_round)
  → ContextStore: session.review_decision = "approved"
  → POST /v1/workflow/resume { token, decision: "input" }

Contestante age:
  POST /v1/evaluation/contestations { result_id, round: 1, reason (formato estruturado por critério) }
  → anti-replay check
  → ContextStore: session.review_decision = "contested"
  → POST /v1/workflow/resume { token, decision: "input" }

Timeout sem ação:
  → locked = true, lock_reason = "review_timeout"
  → 409 em qualquer tentativa posterior
```

Skills de revisão disponíveis:
- `skill_revisao_simples_v1` — 1 round (simples aprovação/rejeição)
- `skill_revisao_treplica_v1` — até 3 rounds (réplica, tréplica); alterar `value: 3` → `value: 2` para réplica

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

## Pacotes envolvidos

| Pacote | Responsabilidade |
|---|---|
| `evaluation-api` | Ciclo de vida de forms, campaigns, instances, results, contestations (porta 3400) |
| `mcp-server-knowledge` | Base de conhecimento vetorial (pgvector) — MCP tools para o agente avaliador (porta 3401) |
| `session-replayer` | Constrói `ReplayContext` com transcrição, form e snippets; Kafka consumer `evaluation.requested` |
| `workflow-api` | Motor de estado do ciclo de revisão/contestação |
| `analytics-api` | Consumer `evaluation.events` → ClickHouse; endpoints `/reports/evaluations*` |
| `platform-ui` | `modules/evaluation/` — 5 páginas |

## Kafka topics

| Tópico | Produtor | Consumidor |
|---|---|---|
| `evaluation.events` | evaluation-api (submit/review/contest/lock) | analytics-api → ClickHouse `evaluation_results` + `evaluation_events` |

## Referências

- Schemas: `packages/schemas/src/evaluation.ts`
- Backend: `packages/evaluation-api/`, `packages/mcp-server-knowledge/`, `packages/session-replayer/`
- Frontend: `packages/platform-ui/src/modules/evaluation/`
- Skills de avaliação: `packages/skill-flow-engine/skills/agente_avaliacao_v1.yaml`, `skill_revisao_simples_v1.yaml`, `skill_revisao_treplica_v1.yaml`
