# Arc 13 — Evaluation Review & Contestation UX

Refina o ciclo de revisão/contestação da plataforma de avaliação (Arc 6): redesenha o modelo de dados para imutabilidade do resultado original, thread de contestação por dimensão, UX de revisão baseada no próprio formulário, spec dos agentes avaliador e revisor, e SLA/deadline configurável por Campanha.

> **Status**: Em especificação — nenhuma fase implementada.

---

## Dependência — Arc 6 Fase 2 (Quality Timeseries × Deploy Epochs)

**Este Arc é produtor de dados para o Arc 6 Fase 2.** A série histórica de qualidade (scores de avaliação ao longo do tempo vs versões de agentes) só é válida se usar o **score canônico final** — i.e., o score após encerramento do processo de contestação/revisão, não o score bruto do AI avaliador.

### Invariante cross-arc

> **Uma avaliação não finalizada não existe para fins analíticos.** Nenhum score é considerado nos relatórios de qualidade (Arc 6 Fase 2) enquanto a instância não atingir um estado terminal.

O evento `evaluation_finalized` é a única fonte de verdade para qualidade histórica de agentes.

```
Arc 13 state machine → terminal state
    closed_upheld    │
    closed_revised   ├─→ evaluation.events { type: "evaluation_finalized", final_score, instance_id }
    timeout_*        │
                     └─→ ClickHouse — só então a instância entra nos relatórios

evaluation_submitted → nunca lido por Arc 6 Fase 2
```

### Implicações práticas

1. **Arc 6 Fase 2 queries** (`quality-timeseries`, `quality-comparison`): filtro exclusivo `WHERE type = 'evaluation_finalized'`. O evento `evaluation_submitted` não é lido por essas queries em nenhum cenário.
2. **ClickHouse `evaluation_results`**: deixa de ser `ReplacingMergeTree` dedup — pode ser tabela simples porque só chega um evento definitivo por `instance_id`. Alternativa: manter `ReplacingMergeTree` para idempotência de redelivery, mas a semântica de "score válido = apenas `evaluation_finalized`" é o que governa.
3. **Lag por design**: o score de uma sessão demora pelo menos `contest_deadline_hours` para aparecer nos relatórios. Isso é esperado e correto — reflete o score real, não o provisório.
4. **Indicador de pendência**: o Arc 6 Fase 2 deve exibir contador "N avaliações pendentes de fechamento" na faixa de deploy atual, para contextualizar que a série ainda está incompleta para o período recente.
5. **Schema em `@plughub/schemas`**: `EvaluationEventSchema` ganha `type: z.union([..., z.literal("evaluation_finalized")])` + campos `final_score`, `final_scores_by_dimension[]`, `contestation_rounds: int`, `process_duration_ms` (submitted_at → finalized_at).

---

## Motivação

O Arc 6 implementou o motor de contestação via workflow (`skill_revisao_treplica_v1`), mas deixou em aberto:
- A UX da tela de revisão (como o avaliado vê e contesta a avaliação)
- O modelo de evidências por critério (trechos da conversa que embasam a nota)
- A granularidade da contestação (por critério, dimensão ou formulário inteiro)
- A spec dos agentes avaliador e revisor (inputs, outputs, comportamento esperado)
- Os campos de SLA por etapa na Campanha (hoje apenas no YAML do workflow)

---

## Decisões de Arquitetura

### 1 — Imutabilidade do resultado original

O `EvaluationResult` inicial nunca é modificado. Toda contestação, revisão e réplica é um registro **separado** (`ContestationThread`) associado ao item do formulário. O formulário de revisão monta a thread cronológica a partir desses registros.

### 2 — Granularidade: por dimensão (agrupamento de critérios)

Contestações e revisões são registradas por **dimensão** (`dimension_id` = grupo de critérios no formulário), não por critério individual. Isso reduz fricção para o avaliado (uma justificativa por bloco temático) sem perder rastreabilidade. Se o formulário não tiver dimensões explícitas, cada critério vira sua própria dimensão.

### 3 — Agentes IA nunca contestam

Somente agentes humanos (`author_type: "human_agent"`) podem abrir uma contestação. O revisor automático (`agente_revisor_v1`) responde contestações mas nunca as inicia. Isso simplifica `available_actions`: se `author_type_for_session == "ai"` → nunca retorna `"contest"`.

### 4 — SLA por etapa na Campanha

Os prazos para contestar e para revisar são campos explícitos na Campanha, não apenas no YAML do workflow. O workflow usa esses campos como `timeout_hours` ao construir cada step `suspend`. Ao expirar sem ação, o estado assume o último `ContestationThread` registrado + sufixo `_timeout`.

### 5 — Revisor pode ser AI ou humano

A Campanha configura `reviewer_type: "ai" | "human" | "ai_then_human"`. O skill de revisão (`skill_revisao_treplica_v1`) usa essa config para decidir se roteada para `agente_revisor_v1` ou para um pool humano.

---

## Modelo de Dados

### ContestationThread (novo)

```python
ContestationThread {
  thread_id:             UUID       # PK
  evaluation_instance_id: str       # FK → evaluation_instances
  dimension_id:          str        # ID da dimensão do formulário (ou criterion_id se sem dimensão)
  round:                 int        # 1=avaliação, 2=contestação, 3=revisão, 4=réplica, 5=tréplica
  author_type:           str        # "evaluator_ai" | "human_agent" | "reviewer_ai" | "human_reviewer"
  author_id:             str        # user_id ou agent_type_id
  text:                  str        # justificativa ou resposta
  score_override?:       float      # se revisor alterar a nota deste item
  created_at:            datetime
}
```

O `round=1` é criado pelo `agente_avaliacao_v1` para cada dimensão com a justificativa e as evidências.

### Evidence (embutido no ContestationThread round=1)

```python
Evidence {
  stream_entry_id: str        # ID do evento no canonical stream
  excerpt:         str        # trecho da mensagem (masked)
  relevance_note:  str        # por que este trecho embasou a nota
}
```

Armazenado como JSONB `evidence_entries[]` em `ContestationThread` para rounds gerados por AI.

### Campaign — novos campos de SLA

```python
EvaluationCampaign {
  # ... campos existentes ...
  contest_deadline_hours:  int | None  # prazo para o avaliado contestar após receber resultado
  review_deadline_hours:   int | None  # prazo para o revisor responder cada round
  use_business_hours:      bool        # se True, usa evaluation_calendar_id para calcular prazo
  reviewer_type:           str         # "ai" | "human" | "ai_then_human"
}
```

---

## State Machine de Contestação

```
evaluation_completed
    │
    ▼
contestation_open  ──[contest_deadline expired]──► timeout_contestation (aceito implicitamente)
    │
    [human agent contests ≥1 dimension]
    ▼
under_review  ──[review_deadline expired]──► timeout_review (mantém contestação sem resposta)
    │
    [reviewer responds all dimensions]
    ▼
review_completed
    │
    ├── [score unchanged] ──► closed_upheld
    └── [score changed]   ──► closed_revised
```

O `workflow_instance_id` na `EvaluationInstance` rastreia onde o processo está no estado machine. O workflow usa `review_deadline_hours` e `contest_deadline_hours` como `timeout_hours` nos steps `suspend`.

---

## Spec — agente_avaliacao_v1 (refinamento)

### Responsabilidades

- Recebe `ReplayContext` via `evaluation_context_get`
- Para cada dimensão do formulário: atribui score + justificativa + evidências (`stream_entry_id` + excerpt)
- Submete via `evaluation_submit`
- Cria `ContestationThread` round=1 para cada dimensão (evidence_entries preenchido)

### Input esperado

```yaml
inputs:
  - evaluation_context     # via evaluation_context_get: form + knowledge_snippets + session transcript
  - scoring_instructions   # do knowledge_namespace da campanha
```

### Output esperado por dimensão

```yaml
dimension_id:     "abertura"
score:            0.8
justification:    "Agente usou saudação protocolar mas não confirmou o nome do cliente"
evidence_entries:
  - stream_entry_id: "1715700000000-0"
    excerpt:         "Olá, seja bem-vindo ao serviço"
    relevance_note:  "Saudação presente mas incompleta"
```

### Invariantes

- Nunca atribui `score_override` (só o revisor pode)
- Sempre preenche `evidence_entries` — sem evidência = justificativa vaga = penalização de confiança
- `na: true` somente se a dimensão genuinamente não se aplica ao canal/contexto

---

## Spec — agente_revisor_v1 (novo)

### Responsabilidades

- Ativado pelo `skill_revisao_treplica_v1` após contestação humana abrir
- Recebe: resultado original + `ContestationThread[]` round=2 (contestações humanas) + `ReplayContext`
- Para cada dimensão contestada: decide `upheld | revised` com justificativa
- Se `revised`: propõe `score_override` com evidência
- Submete resultado via `evaluation_review_submit` (novo MCP tool)

### Invariantes

- Só responde dimensões que foram contestadas no round=2
- Nunca abre contestação própria
- Justificativa obrigatória mesmo quando mantém nota (`upheld`)
- Evidências obrigatórias quando `revised`

---

## UX — Tela de Revisão de Avaliação

### Layout

```
┌─────────────────────────────────────┬──────────────────┐
│  Avaliação #inst-001                │  Contestações (2) │
│  Campanha: Retenção Q2 · Score: 78% │  ┌──────────────┐ │
│  Avaliado em: 14/05/2026            │  │ ● Abertura   │ │
├─────────────────────────────────────┤  │ ● Resolução  │ │
│                                     │  └──────────────┘ │
│  [Dimensão: Abertura]  ────────── ● │                  │
│  Score: 8/10   [Contested]          │                  │
│  Justificativa: "..."               │                  │
│  Evidências: > "Olá, seja bem..."   │                  │
│                                     │                  │
│  Thread:                            │                  │
│  Round 1 (AI Avaliador): ...        │                  │
│  Round 2 (João Silva):   ...        │                  │
│  Round 3 (AI Revisor):   ...        │                  │
│                                     │                  │
│  [Dimensão: Resolução]  ────────── ● │                  │
│  Score: 6/10   [Revised → 8/10]     │                  │
│  ...                                │                  │
└─────────────────────────────────────┴──────────────────┘
```

### Estados visuais por dimensão

| Estado | Cor | Descrição |
|---|---|---|
| `neutral` | Cinza | Avaliação inicial, sem contestação |
| `contested` | Âmbar `#D97706` | Contestado pelo avaliado, aguardando revisão |
| `upheld` | Azul `#1B4F8A` | Revisor manteve a nota original |
| `revised` | Verde `#059669` | Revisor alterou a nota |
| `timeout` | Vermelho suave `#DC2626` | Prazo expirado sem ação |

### Painel lateral "Contestações"

- Lista apenas dimensões com round ≥ 2 (contestadas)
- Cada item: nome da dimensão + badge de estado + atalho (scroll + highlight no formulário)
- Visível para avaliado (para acompanhar) e para revisor (para navegar rapidamente)
- Badge com contagem total no header do painel

### Regras de disponibilidade de ações

| Quem | Quando pode contestar |
|---|---|
| `human_agent` (avaliado) | `status == "contestation_open"` E `author_type_for_session != "ai"` |
| Revisor AI/humano | `status == "under_review"` E é o revisor designado pela Campanha |
| Ninguém | Status `closed_*` ou `timeout_*` |

---

## Fases de Implementação

### Fase A — Data Model
- Novo modelo `ContestationThread` em evaluation-api (migration Prisma)
- Campos `contest_deadline_hours`, `review_deadline_hours`, `use_business_hours`, `reviewer_type` na Campaign
- Refatorar `EvaluationContestation` existente como alias ou deprecar
- Endpoint: `POST /v1/evaluation/instances/{id}/contest` (body: `dimension_id`, `text`)
- Endpoint: `POST /v1/evaluation/instances/{id}/review` (body: `dimension_id`, `decision`, `text`, `score_override?`)
- Atualizar `available_actions` para incluir `"contest"` e `"review"` com granularidade por dimensão

### Fase B — Evaluator Agent Spec
- Atualizar `agente_avaliacao_v1.yaml` com output `evidence_entries[]`
- Atualizar `evaluation_submit` no mcp-server-plughub para aceitar `evidence_entries`
- Persistir `ContestationThread` round=1 ao submeter avaliação
- Endpoint: `GET /v1/evaluation/instances/{id}/threads` para leitura pelo cliente

### Fase C — Reviewer Agent Spec
- Criar `agente_revisor_v1.yaml` + YAML em `infra/registry/`
- Novo MCP tool `evaluation_review_submit`
- Atualizar `skill_revisao_treplica_v1.yaml` para usar `reviewer_type` da Campanha
- Passar `contest_deadline_hours` e `review_deadline_hours` como `timeout_hours` nos steps suspend

### Fase D — Human Review UX
- `EvaluationReviewPage` em platform-ui: layout formulário + painel lateral de contestações
- Componentes: `DimensionCard` (thread + estado visual), `ContestationPanel` (lista shortcuts), `ContestThread` (thread cronológica por round)
- Hook `useEvaluationThreads(instanceId)`
- Integração com Session Replayer: link `→ Ver na conversa` para cada `stream_entry_id`

### Fase E — Campaign SLA Config UI
- Adicionar campos de SLA ao drawer de edição da CampaignsPage
- Seletor `reviewer_type` (AI / Humano / AI depois Humano)
- Validação: se `use_business_hours: true`, `evaluation_calendar_id` obrigatório

---

## Decisões Pendentes Antes de Implementar

| Decisão | Opções | Impacto |
|---|---|---|
| Dimensões explícitas no formulário ou inferidas dos critérios? | Adicionar `dimension_id` a `EvaluationCriterion` vs agrupar automaticamente | Schema do formulário |
| Contestação parcial (por dimensão) vs total (uma caixa) | Por dimensão (já decidido, confirmar) | UX + API |
| `agente_revisor_v1` tem acesso à conversa original ou só ao resultado + threads? | Via `ReplayContext` ou só `ContestationThread[]` | Spec do agente |
| Tréplica: o avaliado pode responder a revisão do revisor? | Rounds adicionais (3, 4, 5) ou máximo 1 rodada de contestação | `ContestationPolicy.max_rounds` |

---

## Arquivos a Criar/Modificar

| Arquivo | Ação |
|---|---|
| `evaluation-api/prisma/schema.prisma` | Adicionar `ContestationThread`, campos Campaign |
| `evaluation-api/src/routes/contestation.py` | Endpoints contest + review por dimensão |
| `packages/schemas/src/evaluation.ts` | `ContestationThread`, `Evidence`, Campaign SLA fields |
| `infra/registry/agents/agente_revisor_v1.yaml` | Novo agent type |
| `infra/registry/skills/skill_revisao_treplica_v1.yaml` | Atualizar timeouts dinâmicos |
| `mcp-server-plughub/src/tools/evaluation.ts` | `evaluation_review_submit` tool |
| `platform-ui/src/modules/evaluation/` | `EvaluationReviewPage`, componentes |

→ Ver [arc6-evaluation.md](arc6-evaluation.md) para contexto do Arc 6 base.
