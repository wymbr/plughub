# Arc 13 — Evaluation Review, Contestation & Calibration

> Última atualização: 2026-05-25 · Estado: Arc 16 · Status: Fases A–H implementadas (2026-05-18). Arc 13 completo.

Fecha o ciclo completo de qualidade da plataforma de avaliação (Arc 6): revisão pré-publicação por AI, contestação estruturada por dimensão para agentes humanos, curadoria amostral para agentes AI, e loop de calibração contínua dos avaliadores automáticos via feedback → knowledge base → deploy epochs.

---

## Reconciliação Arc 6 + Arc 13 (em andamento — fonte de verdade: spec)

> **Atenção:** vários ✅ abaixo descrevem o **alvo**, não o que o código fazia no baseline
> `eval-baseline` (2026-06-17). A correção completa destes docs é a tarefa **T16/G-DOCS**.
> A arquitetura-alvo e o estado-atual×alvo vivem em
> [`docs/product/evaluation-reconciliation-spec.md`](../product/evaluation-reconciliation-spec.md).

### T5 chunk 5c — Contestação em lote por critério + gate "tratar todas" (2026-06-18) ✅

Unifica o contrato de contestação no nível de **critério** sob o envelope de round/estado do
resultado (§4, §15 da spec). Validado verde ponta-a-ponta na demo
(`infra/test/test_5c_contestation.sh`).

- **`POST /v1/evaluation/instances/{id}/contest`** aceita um **conjunto** de critérios num
  round: `{dimension_ids[], reasons{criterion_id→texto}, evidence?{criterion_id→[...]}, round?}`.
  Cria uma `ContestationThread` (`author_type=human_agent`) por critério e move o resultado
  `contestation_open → under_review` **uma única vez** (o round inteiro segue para revisão).
  `round` opcional faz anti-replay (409 em divergência). Forma single legada continua aceita.
- **`POST /v1/evaluation/instances/{id}/review`** aceita `dimension_decisions[]` em lote. **Gate
  server-side "tratar todas" (§15.3):** as decisões têm de cobrir o conjunto **exato** dos
  critérios contestados no round corrente — faltando algum → **`409 pending_contestations`**
  (com `missing`/`contested`/`round` no detail); critério não-contestado → `400`. Cria uma
  thread `human_reviewer` por decisão e aplica a transição do round **uma vez**: reabre
  `round+1` enquanto há round restante ou **finaliza no último** via o emissor único
  `finalize_evaluation` (T3) — reason `revised` se houve qualquer override no round, senão
  `upheld`.
- **Conjunto contestado do round** vem de `db.list_contested_criteria_for_round` (distinct
  `dimension_id` com `author_type='human_agent'` no round).
- **ABAC/posse (5a) preservados:** contest exige posse do segmento + campo `contestar*` do
  round; review exige `revisar*` do round + guarda **revisor≠avaliado**.

**Fronteira do 5c (não é bug):** a consolidação do `score_override` na **nota final** pelos
pesos do formulário é a **T7** (saída form-driven/agregação). No 5c o `finalize()` usa a
`overall_score` corrente como placeholder — por isso `final_score` ainda não reflete overrides.

Mudança só na `evaluation-api`, **code-only (sem migração)**: `db.py`
(`list_contested_criteria_for_round`) + `contestation_router.py` (`/contest` e `/review` em
lote + gate). Rebuild: `docker compose -f docker-compose.demo.yml up -d --build evaluation-api`.

---

## Dependência — Arc 6 Fase 2 (Quality Timeseries × Deploy Epochs)

**Este Arc é produtor de dados para o Arc 6 Fase 2.** A série histórica de qualidade só é válida se usar o **score canônico final** — após encerramento do processo de revisão/contestação, nunca o score bruto do AI avaliador.

> **Invariante**: uma avaliação não finalizada não existe para fins analíticos. O evento `evaluation_finalized` é a única fonte de verdade para os relatórios de qualidade.

```
Estados terminais → evaluation_finalized
  closed_upheld
  closed_revised      → evaluation.events { type: "evaluation_finalized",
  timeout_*             final_score, final_scores_by_dimension[], process_duration_ms }
  auto_finalized (AI)
                      → ClickHouse — só então entra nos relatórios
```

**Arc 6 Fase 2 queries** filtram exclusivamente `WHERE type = 'evaluation_finalized'`. O evento `evaluation_submitted` nunca é lido por relatórios de qualidade.

**Lag por design**: score de agente humano demora `contest_deadline_hours` para aparecer. Indicador "N avaliações pendentes de fechamento" deve ser exibido na faixa de deploy atual para contextualizar série incompleta.

**Calibration score** (novo, produzido por este Arc): por versão de skill, % de avaliações curadas e aprovadas pelo curador humano — mede a qualidade do próprio avaliador AI ao longo do tempo.

---

## Os Dois Fluxos — Visão Geral

O Arc 13 opera com **dois fluxos completamente separados** pelo tipo de agente avaliado:

```
┌─────────────────────────────────────────────────────────────────────────┐
│  FLUXO 1 — Agente HUMANO avaliado                                       │
│                                                                         │
│  AI Evaluator → score + evidências                                      │
│       ↓ [se campanha tem pre_review habilitado]                         │
│  AI Reviewer (pré-publicação) → melhora qualidade + calibration_signal  │
│       ↓ [sinal → Curator Queue, assíncrono, não bloqueia]               │
│  Resultado publicado ao agente humano                                   │
│       ↓                                                                 │
│  contestation_open → [se contesta] → Human Reviewer (decisão final)     │
│       ↓                                                                 │
│  evaluation_finalized                                                   │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│  FLUXO 2 — Agente AI avaliado                                           │
│                                                                         │
│  AI Evaluator → score + evidências                                      │
│       ↓                                                                 │
│  evaluation_finalized (imediato — sem contestação)                      │
│                                                                         │
│  [paralelo, amostral, não bloqueia]                                     │
│  Sampling Engine (regras configuráveis) → Curator Queue                 │
│       ↓                                                                 │
│  Curador humano revisa qualidade da avaliação AI                        │
│       ↓                                                                 │
│  approved | recalibrated | bias_flagged → calibration_event             │
│       ↓                                                                 │
│  CalibrationNote → knowledge namespace → feedback ao avaliador via RAG  │
└─────────────────────────────────────────────────────────────────────────┘
```

**Separação chave:**
- Curadoria existe nos **dois fluxos** — é sempre sobre a qualidade do **avaliador AI**, não sobre o agente avaliado. O que difere é o mecanismo de disparo.
- **Agente humano avaliado**: curadoria acionada pelo `calibration_signal` do revisor pré-publicação. Contestação é um direito do avaliado.
- **Agente AI avaliado**: curadoria acionada pelas regras de sampling após `evaluation_finalized`. Sem contestação.
- Revisor AI: atua *antes* da publicação ao agente humano (gate de qualidade), não após contestação.
- Human Reviewer: decisão *sempre* final — entra apenas se há contestação de agente humano.

---

## Como Avaliar um Agente AI

Avaliar um agente AI é fundamentalmente diferente de avaliar um agente humano. O volume é maior, não há contestação, e os erros têm padrões sistemáticos — o agente não "teve um dia ruim", ele tem um comportamento consistente derivado do seu treinamento e da sua knowledge base. Isso muda o que medir, como medir e o que fazer com os resultados.

### Duas categorias com pipelines distintos

**Métricas quantitativas** são objetivas, automáticas e disponíveis sem custo de LLM. São extraídas diretamente dos eventos de sessão já existentes na plataforma. Não pertencem ao formulário de avaliação — pertencem ao dashboard de analytics e ao painel de performance do agente.

**Métricas qualitativas** exigem julgamento — o avaliador AI lê a transcrição e decide se o agente fez algo certo ou errado conforme os critérios definidos no formulário. São configuradas pelo operador por campanha, têm pesos e podem ser do tipo `score`, `pass_fail`, `text` ou `auto_computed` (novo — ver abaixo). O formulário é essencialmente um questionário estruturado: cada critério é uma pergunta ("o agente confirmou a identidade do cliente?") que o avaliador responde com nota + evidência + justificativa.

### Métricas quantitativas — referência completa

Extraídas automaticamente após cada sessão, sem avaliação AI. Disponíveis como critérios `auto_computed` no formulário ou como KPIs no dashboard de analytics.

**Tempos de atendimento:**

| Métrica | Definição | Fonte |
|---|---|---|
| `first_response_time_s` | Tempo entre primeira mensagem do cliente e primeira resposta do agente | Stream: `message_sent` timestamps |
| `avg_response_time_s` | Média de tempo entre mensagem do cliente e resposta do agente ao longo da sessão | Stream: pares client→agent |
| `max_response_time_s` | Maior latência de resposta individual na sessão | Stream |
| `total_session_duration_s` | Duração total da sessão (abertura → fechamento) | `session_opened` → `session_closed` |
| `customer_wait_time_s` | Soma de todos os períodos em que o cliente aguardou resposta | Stream |
| `step_avg_duration_ms` | Tempo médio por step do skill flow | `pipeline_state` transitions |
| `step_max_duration_ms` | Step mais lento da sessão — identifica gargalos no fluxo | `pipeline_state` |

**Composição das mensagens:**

| Métrica | Definição | Fonte |
|---|---|---|
| `total_messages` | Total de mensagens trocadas na sessão | Stream `visibility=all` |
| `agent_messages` | Quantidade de mensagens enviadas pelo agente | Stream `author_role` |
| `customer_messages` | Quantidade de mensagens enviadas pelo cliente | Stream |
| `agent_message_pct` | % de mensagens do agente sobre o total | Calculado |
| `customer_message_pct` | % de mensagens do cliente sobre o total | Calculado |
| `avg_agent_message_length` | Tamanho médio das mensagens do agente (chars) | Stream content |
| `turns_to_resolution` | Número de turnos (pares pergunta-resposta) até resolução | Stream |

**Eficiência de coleta e ferramentas:**

| Métrica | Definição | Fonte |
|---|---|---|
| `required_fields_captured_pct` | % de campos obrigatórios do skill coletados com sucesso | ContextStore vs skill spec |
| `collect_retries` | Quantas vezes o agente precisou re-solicitar um dado ao cliente | Step `menu`/`collect` retries |
| `tool_calls_total` | Total de chamadas MCP tool na sessão | `mcp.audit` |
| `tool_calls_failed` | Chamadas que retornaram erro | `mcp.audit` |
| `tool_error_rate` | `tool_calls_failed / tool_calls_total` | Calculado |

**Resultado e escalada:**

| Métrica | Definição | Fonte |
|---|---|---|
| `escalated` | Bool — foi transferido para agente humano | `agent_done.outcome` |
| `escalation_reason` | Motivo da escalada (`handoff_reason`) | `agent_done` |
| `steps_completed` | Steps executados com sucesso | `pipeline_state` |
| `steps_retried` | Steps que passaram por retry/catch | `pipeline_state` |

**Custo de inferência:**

| Métrica | Definição | Fonte |
|---|---|---|
| `llm_calls_total` | Total de chamadas LLM na sessão | `usage.events` |
| `tokens_input_total` | Tokens de input consumidos | `usage.events` `llm_tokens_input` |
| `tokens_output_total` | Tokens de output gerados | `usage.events` `llm_tokens_output` |
| `cost_estimate_usd` | Custo estimado baseado no modelo e tokens | Pricing config |

### Métricas qualitativas — o formulário de avaliação

O formulário é definido pelo operador por campanha. Cada critério é uma pergunta que o avaliador AI responde com score + evidência + justificativa. O operador decide o peso de cada critério no score final.

**Categorias típicas de critérios qualitativos:**

**Reconhecimento de intenção** — o agente entendeu o que o cliente queria?
- Relativamente simples para o avaliador AI. A intenção declarada pelo cliente e as ações tomadas pelo agente estão no transcript. Evidência objetiva disponível.

**Qualidade da captura de dados** — o agente coletou os campos corretamente?
- Verificável: o avaliador cruza o que está no ContextStore (`@ctx.caller.*`) com o que o cliente disse na transcrição. Divergências são evidência de captura incorreta.

**Correção das informações fornecidas** — o agente disse algo errado?
- O mais difícil. Requer fonte de verdade (knowledge base, FAQ, regras de negócio) para comparar. Sem ground truth, o avaliador AI julga plausibilidade, não correção factual. **Esta dimensão deve sempre acionar curadoria humana quando a nota é baixa** — é o caso onde o avaliador AI tem menor confiabilidade, pois pode compartilhar os mesmos vieses ou knowledge base desatualizada do agente avaliado.

**Cumprimento de protocolo** — o agente seguiu os passos esperados?
- Verificável por evidências explícitas no transcript (saudação, confirmação de identidade, encerramento, avisos legais obrigatórios). Bem avaliável por AI.

**Qualidade do handoff** — quando escalou, o contexto entregue ao humano estava correto e completo?
- O avaliador verifica o ContextStore no momento da transferência vs o que o cliente havia informado. Verificável por evidência.

**Tom e adequação** — a linguagem do agente foi apropriada para o contexto?
- Subjetivo mas avaliável por AI com critério bem definido no formulário e exemplos no knowledge namespace.

### O novo tipo de critério: `auto_computed`

Tipo de critério onde o valor é calculado automaticamente a partir dos dados de sessão — sem LLM, sem custo, disponível imediatamente após a sessão encerrar. O avaliador AI não precisa ler o transcript para esses critérios.

```typescript
EvaluationCriterion {
  // tipos existentes: "score" | "pass_fail" | "text" | "na_allowed"
  type: "auto_computed"          // novo
  computation_source: string     // ex: "session_metric.first_response_time_s"
  threshold_pass?: number        // valor abaixo/acima do qual score = 1.0
  threshold_fail?: number        // valor abaixo/acima do qual score = 0.0
  comparison: "lt" | "gt" | "lte" | "gte"  // direção da comparação
}
```

Exemplo: critério "Tempo de primeira resposta < 5s" com `computation_source: "session_metric.first_response_time_s"`, `threshold_pass: 5`, `comparison: "lt"` → score 1.0 se < 5s, interpolado entre 5–10s, 0.0 acima de 10s.

### A correção factual e o loop de calibração

A informação errada é o caso mais crítico e o menos coberto automaticamente. O desafio: o avaliador AI pode ser tão falível quanto o agente avaliado — se ambos usam o mesmo modelo com a mesma knowledge base desatualizada, o avaliador não detecta o erro.

Por isso, qualquer avaliação que pontue baixo na dimensão de **correção de informações** deve disparar automaticamente a regra de curadoria `score_outlier` — garantindo que um curador humano revise esse caso. Quando o curador detecta um erro factual sistemático, ele cria uma `CalibrationNote` descrevendo a informação correta → essa nota vai para o knowledge namespace → o avaliador AI a lê via RAG nas próximas sessões → e o operador atualiza a knowledge base do agente avaliado.

Esse é o único loop que fecha o ciclo completo: sessão → avaliação → detecção de erro → correção na fonte → melhoria mensurável na próxima versão do skill.

---

## Decisões de Arquitetura

### 1 — Imutabilidade do resultado original

O `EvaluationResult` inicial nunca é modificado. Toda revisão, contestação e réplica é um registro separado (`ContestationThread`) associado à dimensão do formulário. A thread cronológica monta o histórico completo.

### 2 — Granularidade: por dimensão com `dimension_id` explícito ✅

Cada `EvaluationCriterion` ganha `dimension_id` e `dimension_label`. Critérios com o mesmo `dimension_id` formam um bloco — contestados juntos via uma única `ContestationThread`. Se `dimension_id` ausente, `criterion_id` serve como fallback (retrocompatibilidade).

### 3 — Agentes AI nunca contestam

Somente `author_type: "human_agent"` pode abrir contestação. `available_actions` retorna `[]` para avaliações de agentes AI. Isso é verificado server-side, nunca client-side.

> **T10-A (2026-06-19) — `available_actions` por estado+round+posse.** `_compute_available_actions`
> (evaluation-api `router.py`) foi reescrito p/ a regra da spec §17.2 (não depende mais de
> `action_required`): `open(R)` ∧ caller é o avaliado (`jwt.sub == result.evaluated_user_id`) ∧ campo
> de contestação do round R → `["contest"]`; `under_review(R)` ∧ caller ≠ avaliado ∧ campo de revisão
> do round R → `["review"]`; senão `[]` (read-only). Campo casado por round
> (`contestar`/`_replica`/`_treplica`; idem `revisar`). Guardas: locked/finalized/sem-token → `[]`;
> não-dono não contesta; ninguém se revisa. Cobertura: `tests/test_available_actions.py`. Pendente:
> T10-D (ações na rota dedicada do nível 3).

> **T10-C (2026-06-19) — visibilidade self-scope.** `list_results` escopa por linha via
> `_compute_result_scope(jwt)`: admin → tudo; não-admin → `evaluated_user_id ∈ (supervised_user_ids ∪
> {sub})` (atendente = só os próprios; supervisor = Grupo Arc 9 + próprios); `accessible_pools` (Arc 7)
> filtra por pool. `db.list_results` ganhou `evaluated_user_ids`/`accessible_pools`; `InstanceCreate`
> expõe `evaluated_user_id`. **ABAC nunca amplia visibilidade** — só governa a ação nas linhas visíveis.
> Diferido: escopo por `supervised_agent_types` (AI por tipo) — result sem `agent_type_id`.

### 4 — Revisor AI atua pré-publicação, configurável por campanha ✅

O revisor AI não é um árbitro pós-contestação — é um **gate de qualidade que atua antes do resultado ser publicado ao agente humano**. Configurado por campanha via `pre_review_enabled` e `pre_review_agent_pool`. Objetivo: melhorar a qualidade da avaliação antes que o avaliado a veja, reduzindo contestações evitáveis.

O revisor AI produz dois outputs:
- **Output primário**: revisão de qualidade (pode ajustar scores e evidências da avaliação).
- **Output secundário** (opcional): `calibration_signal` — observação estruturada sobre o comportamento do avaliador. Vai para a Curator Queue de forma assíncrona, não bloqueia a publicação.

### 5 — Human Reviewer sempre fecha — entra apenas pós-contestação

O revisor humano nunca atua antes da contestação. É acionado quando o agente humano contesta — e sua decisão é sempre final. O workflow só roteia para pool humano quando `reviewer_type == "human"` ou `"ai_then_human"` e o agente contestou.

### 6 — max_rounds configurável via ContestationPolicy, padrão 3 ✅

Infraestrutura de rounds já existe no Arc 6 (`review_roles_by_round`, `lock_reason: "max_rounds_reached"`). O Arc 13 adiciona `max_rounds: int` explícito (padrão 3, máx 5):

| Round | Ator | Ação |
|---|---|---|
| 1 | `agente_avaliacao_v1` | Avaliação inicial + evidências por dimensão |
| 1.5 | `agente_revisor_v1` *(se habilitado)* | Revisão pré-publicação — melhora qualidade |
| 2 | `human_agent` | Contestação por dimensão |
| 3 | Revisor AI ou humano | Decisão upheld/revised por dimensão |
| 4 | `human_agent` *(max_rounds ≥ 4)* | Tréplica |
| 5 | Revisor *(max_rounds = 5)* | Palavra final |

### 7 — Curadoria de agentes AI: amostral, paralela, não bloqueante ✅

A curadoria não integra a state machine do Fluxo 2 — corre em paralelo. O `evaluation_finalized` é emitido imediatamente. O Sampling Engine seleciona avaliações para revisão humana assíncrona. Calibration Score é calculado sobre as avaliações curadas, não sobre todas.

### 8 — Calibration_signal unifica curadoria dos dois fluxos

O `calibration_signal` gerado pelo revisor AI no Fluxo 1 vai automaticamente para a Curator Queue — tornando a curadoria de avaliações de agentes humanos também possível, sem criar um fluxo separado. Isso conecta os dois fluxos ao mesmo mecanismo de feedback.

---

## Modelo de Dados

### EvaluationCriterion — campos novos

```typescript
EvaluationCriterion {
  // campos existentes: id, label, description, weight, options
  type:               string   // "score" | "pass_fail" | "text" | "na_allowed" | "auto_computed"
  dimension_id?:      string   // agrupa critérios. Se ausente, usa criterion_id
  dimension_label?:   string   // "Abertura", "Resolução", "Empatia"
  // campos exclusivos de type="auto_computed":
  computation_source?: string  // "session_metric.{metric_name}" — ex: "session_metric.first_response_time_s"
  threshold_pass?:     number  // valor onde score = 1.0
  threshold_fail?:     number  // valor onde score = 0.0
  comparison?:         string  // "lt" | "gt" | "lte" | "gte" — direção da comparação
}
```

Critérios `auto_computed` são preenchidos pelo Sampling Engine após `evaluation_finalized`, antes de o avaliador AI processar o formulário. O avaliador AI pula esses critérios — o score já está calculado. O curador humano pode revisar mas não é esperado intervir em critérios objetivos.

### ContestationThread (novo)

```python
ContestationThread {
  thread_id:               UUID
  evaluation_instance_id:  str        # FK → evaluation_instances
  dimension_id:            str        # dimension_id ou criterion_id (fallback)
  round:                   int        # 1=avaliação, 2=contestação, 3=revisão, ...
  author_type:             str        # "evaluator_ai" | "pre_reviewer_ai" | "human_agent"
                                      # | "reviewer_ai" | "human_reviewer"
  author_id:               str        # user_id ou agent_type_id
  text:                    str        # justificativa ou resposta
  decision:                str | None # "upheld" | "revised" — revisor apenas
  score_override:          float | None
  evidence_entries:        JSONB      # Evidence[] — obrigatório em rounds AI
  calibration_signal:      JSONB | None  # CalibrationSignal — revisor pré-publicação apenas
  created_at:              datetime
}
```

O round `1.5` (pré-publicação) é gravado como `round=1` com `author_type="pre_reviewer_ai"` — assim a thread cronológica exibe a revisão antes da contestação sem quebrar a numeração de rounds pós-publicação.

### Evidence (JSONB em ContestationThread)

```python
Evidence {
  stream_entry_id: str   # ID no canonical stream
  excerpt:         str   # trecho da mensagem (masked)
  relevance_note:  str   # por que este trecho embasou a nota
}
```

### CalibrationSignal (JSONB em ContestationThread)

```python
CalibrationSignal {
  severity:       str   # "low" | "medium" | "high"
  dimension_id:   str   # dimensão onde o problema foi detectado
  observation:    str   # "Avaliador aplicou critério X com rigor acima do esperado"
  evaluator_id:   str   # agent_type_id do avaliador que originou o sinal
  skill_version:  str   # versão do skill do avaliador no momento
}
```

### CurationReview (novo)

```python
CurationReview {
  review_id:               UUID
  evaluation_instance_id:  str        # FK → evaluation_instances
  trigger:                 str        # "sampling_rule:{rule_name}" | "reviewer_signal"
  curator_id:              str | None # user_id do curador (None = pendente)
  status:                  str        # "pending" | "approved" | "recalibrated" | "bias_flagged"
  curator_notes:           str | None # complemento do curador ao calibration_signal
  calibration_note_id:     UUID | None # FK → CalibrationNote (se gerada)
  created_at:              datetime
  resolved_at:             datetime | None
}
```

### CalibrationNote (novo)

```python
CalibrationNote {
  note_id:          UUID
  campaign_id:      str
  dimension_id:     str
  evaluator_id:     str        # agent_type_id do avaliador a ser calibrado
  skill_version:    str        # versão do skill no momento da detecção
  text:             str        # nota gerada pelo revisor AI + complementada pelo curador
  severity:         str        # "low" | "medium" | "high"
  published_to_kb:  bool       # True após ingestão no knowledge namespace
  created_at:       datetime
}
```

`CalibrationNote` publicada → knowledge namespace da campanha → `agente_avaliacao_v1` lê via RAG em avaliações futuras.

### ContestationPolicy — campos novos

```typescript
ContestationPolicy {
  // campos existentes: contestation_roles, review_roles_by_round, review_deadline_hours
  max_rounds:              number    // padrão 3, máx 5
  contest_deadline_hours:  number
  use_business_hours:      boolean
  reviewer_type:           string    // "ai" | "human" | "ai_then_human"
  pre_review_enabled:      boolean   // habilita revisão AI pré-publicação
  pre_review_agent_pool:   string | null  // pool do agente revisor pré-publicação
}
```

### CurationSamplingRule (novo, por campanha)

```python
CurationSamplingRule {
  rule_id:       UUID
  campaign_id:   str
  rule_type:     str        # ver tabela de regras abaixo
  params:        JSONB      # parâmetros específicos da regra
  enabled:       bool
  priority:      int        # ordem de avaliação (menor = maior prioridade)
}
```

### EvaluationInstance — campos novos

```python
EvaluationInstance {
  # campos existentes
  evaluated_agent_type:   str        # "human_agent" | "ai_agent" — derivado do session
  contestation_state:     str        # estado da state machine (Fluxo 1 apenas)
  current_round:          int
  pre_review_complete:    bool       # True após revisor AI pré-publicação concluir
}
```

---

## State Machines

### Fluxo 1 — Agente humano avaliado

```
evaluation_submitted (AI Evaluator, round=1)
    │
    ├── [pre_review_enabled = false] ──────────────────────────────┐
    │                                                              │
    └── [pre_review_enabled = true]                                │
            ↓                                                      │
       pre_review_pending                                          │
            ↓                                                      │
       AI Reviewer atua (round=1, author_type=pre_reviewer_ai)    │
            ↓                                                      │
       [calibration_signal gerado → Curator Queue async]          │
            ↓                                                      ↓
       pre_review_complete ──────────────────────────► contestation_open
                                                              │
                                    [contest_deadline expirado]│
                                                              ▼
                                              timeout_contestation → evaluation_finalized
                                                              │
                                    [human_agent contesta ≥1 dimensão]
                                                              ▼
                                                        under_review
                                                              │
                                        [review_deadline expirado]│
                                                              ▼
                                              timeout_review → evaluation_finalized
                                                              │
                                    [revisor responde todas dimensões]
                                                              ▼
                                    [current_round < max_rounds] ──► contestation_open
                                                              │
                                    [current_round == max_rounds]
                                        ├── [score_override] ──► closed_revised
                                        └── [sem override]   ──► closed_upheld
                                                              │
                                                              ▼
                                                    evaluation_finalized
```

### Fluxo 2 — Agente AI avaliado

```
evaluation_submitted (AI Evaluator, round=1)
    ↓
evaluation_finalized (imediato — sem contestação)

[paralelo, não bloqueia — Sampling Engine avalia regras]
    ↓ [se alguma regra match]
curation_pending → CurationReview criado
    ↓
Curador humano age
    ├── approved      → CurationReview.status = approved
    ├── recalibrated  → CalibrationNote criada → published_to_kb = true
    └── bias_flagged  → CalibrationNote (severity=high) → published_to_kb = true
    ↓
calibration.events Kafka → analytics-api
```

---

## Regras de Sampling de Curadoria

Configuráveis por campanha via `CurationSamplingRule`. Avaliadas em ordem de prioridade após `evaluation_finalized` no Fluxo 2 (e também quando `calibration_signal` chega do Fluxo 1).

| rule_type | Params | Objetivo |
|---|---|---|
| `score_extremes` | `top_pct: float`, `bottom_pct: float` | Detecta permissividade (notas altas sem critério) e rigor excessivo (notas baixas injustificadas) |
| `deploy_baseline` | `first_n: int` | Primeiras N avaliações após cada novo deploy do skill — estabelece calibração baseline da versão |
| `score_outlier` | `std_dev_threshold: float` | Score desvia mais de X desvios padrão da média do agent_type no período |
| `na_excess` | `min_na_count: int` | Avaliador marcou ≥ N critérios como `na: true` — suspeita de evasão de julgamento |
| `random_baseline` | `rate: float` | % fixo de todas as avaliações — monitoramento de deriva ao longo do tempo |
| `reviewer_signal` | `min_severity: str` | Toda avaliação onde revisor AI gerou `calibration_signal` com severidade ≥ threshold |

**Regra de prioridade**: uma avaliação que dispara múltiplas regras gera um único `CurationReview` com `trigger` composto (ex: `"score_extremes,reviewer_signal"`).

---

## Spec — agente_avaliacao_v1 (refinamento)

- Recebe `ReplayContext` via `evaluation_context_get` (form + knowledge_snippets + transcrição).
- Para cada dimensão: atribui score + justificativa + `evidence_entries[]`.
- Submete via `evaluation_submit`.
- `ContestationThread` round=1 criado para cada dimensão ao submeter.

**Output por dimensão:**
```yaml
dimension_id:     "abertura"
score:            0.8
justification:    "Agente usou saudação protocolar mas não confirmou o nome do cliente"
evidence_entries:
  - stream_entry_id: "1715700000000-0"
    excerpt:         "Olá, seja bem-vindo ao serviço"
    relevance_note:  "Saudação presente mas incompleta"
```

**Invariantes:**
- Nunca atribui `score_override` (somente o revisor pode).
- `evidence_entries` obrigatório em todas as dimensões pontuadas.
- `na: true` somente se a dimensão genuinamente não se aplica ao contexto.
- Lê `CalibrationNote[]` do knowledge namespace via RAG antes de pontuar — ajusta calibração com base no histórico.

---

## Spec — agente_revisor_v1 (pré-publicação)

**Ativação:** configurável por campanha (`pre_review_enabled: true`). Acionado pelo workflow *antes* de publicar o resultado ao agente humano. Recebe: `EvaluationResult` + `ContestationThread[]` round=1 + `ReplayContext` completo.

**Output primário — revisão de qualidade (por dimensão):**
```yaml
dimension_id:     "abertura"
action:           "adjust"            # "approve" | "adjust"
score_override:   0.7                 # só se action=adjust
revised_evidence:
  - stream_entry_id: "1715700000120-0"
    excerpt:         "... o agente não perguntou o nome..."
    relevance_note:  "Evidência mais relevante para a nota"
justification:    "Evidência original não suportava a nota atribuída"
```

**Output secundário — calibration_signal (opcional):**
```yaml
severity:      "medium"
dimension_id:  "abertura"
observation:   "Avaliador consistentemente ignora contexto de canal de voz nesta dimensão"
```

**Invariantes:**
- Nunca abre contestação.
- Justificativa obrigatória quando `action=adjust`.
- `calibration_signal` emitido apenas quando há evidência de padrão sistemático, não por discordância pontual.
- Score ajustado deve ser acompanhado de evidência alternativa ou corrected.

---

## Spec — agente_revisor_v1 (pós-contestação)

**Ativação:** pelo workflow `skill_revisao_treplica_v1` quando `reviewer_type == "ai"` e o agente humano contestou. Recebe: `EvaluationResult` + `ContestationThread[]` rounds 1 e 2 + `ReplayContext` completo.

**Output por dimensão contestada:**
```yaml
dimension_id:   "abertura"
decision:       "revised"            # "upheld" | "revised"
score_override: 0.9                  # obrigatório se decision=revised
justification:  "Contestação procedente — agente confirmou nome no início da chamada (T+0:34)"
evidence_entries:
  - stream_entry_id: "1715700034000-0"
    excerpt:         "Bom dia, Sr. Carlos..."
    relevance_note:  "Confirmação de nome presente, critério atendido"
```

**Invariantes:**
- Só responde dimensões contestadas no round=2.
- Justificativa obrigatória mesmo quando `upheld`.
- Evidências obrigatórias quando `revised`.

---

## UX — Telas

### Tela de Revisão de Avaliação (Fluxo 1)

```
┌─────────────────────────────────────────┬──────────────────────┐
│  Avaliação #inst-001                    │  Contestações (2)     │
│  Campanha: Retenção Q2 · Score: 78%     │  ┌──────────────────┐ │
│  Revisado por AI em: 14/05 às 10:22    │  │ ● Abertura       │ │
├─────────────────────────────────────────┤  │ ● Resolução      │ │
│                                         │  └──────────────────┘ │
│  [Dimensão: Abertura]  ─────────── ●   │                       │
│  Score: 7/10  (ajustado de 8 pelo AI)  │                       │
│  Justificativa: "..."                   │                       │
│  Evidências: > "Olá, Sr. Carlos..."     │                       │
│                                         │                       │
│  Thread:                                │                       │
│  Round 1  (AI Avaliador):  score 8  ... │                       │
│  Round 1★ (AI Revisor):    score 7  ... │                       │
│  Round 2  (João Silva):    contestação  │                       │
│  Round 3  (AI Revisor):    upheld   ... │                       │
│                                         │                       │
└─────────────────────────────────────────┴──────────────────────┘
```

Estados visuais por dimensão:

| Estado | Cor | Descrição |
|---|---|---|
| `neutral` | Cinza | Avaliação inicial sem contestação |
| `pre_reviewed` | Roxo suave | AI revisou pré-publicação (score pode ter mudado) |
| `contested` | Âmbar `#D97706` | Contestado pelo avaliado, aguardando revisão |
| `upheld` | Azul `#1B4F8A` | Revisor manteve a nota |
| `revised` | Verde `#059669` | Revisor alterou a nota |
| `timeout` | Vermelho `#DC2626` | Prazo expirado |

### Tela de Curadoria (Fluxo 2 + sinais do Fluxo 1)

```
┌─────────────────────────────────────────────────────────────────┐
│  Curadoria de Avaliações  [Campanha: AI Atendimento Q2]         │
│  Pendente: 12   Esta semana: 34   Calibration Score: 91%        │
├───────────────────────────────────────────────────────────────── │
│  #eval-884  agente_retencao_v2  Score: 9.4/10  [score_extremes] │
│  Sinal AI: "Avaliador permissivo no critério de encerramento"   │
│  [Ver avaliação] [Ver conversa]  [Aprovar] [Recalibrar] [Viés]  │
├─────────────────────────────────────────────────────────────────┤
│  #eval-901  agente_retencao_v2  Score: 2.1/10  [score_extremes] │
│  Sem sinal AI — curadoria manual                                │
│  [Ver avaliação] [Ver conversa]  [Aprovar] [Recalibrar] [Viés]  │
└─────────────────────────────────────────────────────────────────┘
```

Ação **Recalibrar**: abre drawer com sinal AI pré-preenchido + campo para o curador complementar → gera `CalibrationNote` → publicada no knowledge namespace.

### Calibration Dashboard (Arc 6 Fase 2 — novo painel)

```
Calibration Score por Skill Version  ──────────────────────────
                                                        ▲
  100% ┤                                       ●────────●  v3
   90% ┤           ●────────●  v2             /
   80% ┤  ●  v1   /                          /
   70% ┤           \                        /
         Deploy 1   Deploy 2         Deploy 3
         (v1→v2)    (v2→v3)          (v3→v4)

Legenda: % avaliações aprovadas por curador sem recalibração
```

Exibido ao lado das curvas de quality_score dos agentes (Arc 6 Fase 2), permitindo correlacionar melhora de qualidade dos agentes com melhora de calibração do avaliador.

---

## Kafka Topics (novos)

| Tópico | Produtor | Consumidor | Evento |
|---|---|---|---|
| `calibration.events` | evaluation-api | analytics-api | `calibration_reviewed` com `decision`, `campaign_id`, `skill_version`, `evaluator_id` |

Novo tipo em `evaluation.events`: `calibration_note_published` — emitido quando `CalibrationNote.published_to_kb = true`.

---

## Loop de Evolução Contínua

```
Curation Queue detecta padrão sistemático no avaliador
    ↓
Curador cria CalibrationNote (texto + dimensão + severidade)
    ↓
CalibrationNote publicada no knowledge namespace da campanha
    ↓
agente_avaliacao_v1 lê via RAG nas próximas avaliações
    ↓
[calibration_score melhora nas próximas semanas?]
    ├── Sim → calibration_signal de severidade "high" diminui → avaliador recalibrado
    └── Não → curador ajusta o YAML do skill ou prompt de avaliação → novo deploy
                    ↓
             novo deploy_epoch (Arc 6 Fase 2)
                    ↓
             nova linha no Calibration Dashboard
```

---

## Fases de Implementação

### ✅ Fase A — Data Model (2026-05-18)
- Migration asyncpg DDL: `evaluation.contestation_threads`, `evaluation.curation_reviews`, `evaluation.calibration_notes`, `evaluation.curation_sampling_rules`
- ALTER TABLEs: `campaigns` (pre_review_enabled, pre_review_agent_pool), `results` (contestation_state, pre_review_complete, evaluated_agent_type, finalized_at, final_score, process_duration_ms)
- Schemas Zod: `ContestationPolicySchema`, `ContestationStateSchema`, `EvidenceEntrySchema`, `CalibrationSignalSchema`, `ContestationThreadSchema`, `CurationReviewSchema`, `CalibrationNoteSchema`, `CurationSamplingRuleSchema`, `EvalFinalizedSchema`, `CalibrationReviewedSchema`, `CalibrationNotePublishedSchema`
- `contestation_router.py` com 11 endpoints: threads, contest, review, pre-review, curations, resolve, calibration-notes, publish, sampling-rules CRUD
- `kafka_emitter.py`: `emit_calibration_reviewed`, `emit_calibration_note_published`, `emit_evaluation_finalized`

### Fase B — Session Metrics Extractor + Evaluator Agent
- `SessionMetricsExtractor`: serviço que computa `session_metric.*` após `session_closed` — lê stream, `mcp.audit`, `usage.events`, `pipeline_state`. Grava resultado em `evaluation_instances.session_metrics` (JSONB).
- Critérios `auto_computed` preenchidos pelo Extractor antes do avaliador AI processar o formulário.
- Atualizar `agente_avaliacao_v1.yaml` com `evidence_entries[]` obrigatório e skip de critérios `auto_computed`.
- Atualizar `evaluation_submit` no mcp-server-plughub para persistir `ContestationThread` round=1.
- Leitura de `CalibrationNote[]` via RAG no `evaluation_context_get`.

### Fase C — Pre-publication Reviewer
- Criar `agente_pre_revisor_v1.yaml` + YAML em `infra/registry/`
- Novo MCP tool `evaluation_pre_review_submit` (ajusta score + emite calibration_signal)
- Wiring no workflow: step pré-publicação condicional (`@ctx.campaign.pre_review_enabled`)
- `ContestationThread` round=1 author_type=`pre_reviewer_ai` gerado ao completar

### Fase D — Post-contestation Reviewer + Human Reviewer
- Criar `agente_revisor_v1.yaml` (pós-contestação) + `infra/registry/`
- Novo MCP tool `evaluation_review_submit`
- Atualizar `skill_revisao_treplica_v1.yaml`: `reviewer_type` da campanha, `max_rounds` dinâmico, `timeout_hours` por round
- Pool humano de revisão configurável por campanha

### Fase E — Human Review UX
- `EvaluationReviewPage` em platform-ui: formulário + painel lateral de contestações
- Componentes: `DimensionCard`, `ContestationPanel`, `ContestThread`
- Hook `useEvaluationThreads(instanceId)`
- Estado visual `pre_reviewed` para dimensões ajustadas pelo revisor pré-publicação
- Link `→ Ver na conversa` para cada `stream_entry_id` via Session Replayer

### Fase F — Curation Module
- `CurationSamplingRule` CRUD na evaluation-api
- Sampling Engine: job pós-`evaluation_finalized`, avalia regras, cria `CurationReview`
- Intake de `calibration_signal` → cria `CurationReview` com `trigger=reviewer_signal`
- `CuradoriaPage` em platform-ui: fila pendente, drawer de decisão, campo de complemento
- `CalibrationNote` → ingestão no knowledge namespace via `mcp-server-knowledge`
- Kafka topic `calibration.events` + consumer em analytics-api

### Fase G — Campaign Config UI
- Campos `ContestationPolicy` no drawer da CampaignsPage: `pre_review_enabled`, `reviewer_type`, `max_rounds`, `contest_deadline_hours`
- Seção "Regras de Curadoria" com CRUD de `CurationSamplingRule`
- Validação: se `use_business_hours: true`, `evaluation_calendar_id` obrigatório

### Fase H — Feedback Loop RAG / Curation Module ✅ (2026-05-18)
- `sampling_engine.py` — avalia 6 regras de curadoria pós-`evaluation_finalized` (Fluxo 2 AI) em background asyncio task
- `CuradoriaPage` (`/evaluation/curadoria`) — fila de curadoria humana com KPI strip, filtros, polling 15s, drawer Recalibrar/Viés
- `CalibrationNote` → `POST /v1/knowledge/snippets` no `mcp-server-knowledge` (namespace `evaluation:calibration:{campaign_id}`)
- `mark_calibration_note_published` + evento `calibration_note_published` ao `evaluation.events` após KB publish
- `list_curation_reviews` enriquecida com `campaign_id` + `calibration_signal` via JOIN + correlated subquery
- Nav item "Curadoria" (🔍) em Sidebar, rota `evaluation/curadoria`, i18n en + pt-BR

---

## Decisões Resolvidas (2026-05-18)

| Decisão | Decisão tomada |
|---|---|
| Dimensões explícitas ou inferidas? | `dimension_id` + `dimension_label` em `EvaluationCriterion`. Fallback: `criterion_id`. |
| Posição do revisor AI | Pré-publicação (gate de qualidade), configurável por campanha. Não pós-contestação. |
| Revisor AI como curador? | Revisor AI gera `calibration_signal` (output secundário) que alimenta a Curator Queue. Curador humano valida e complementa. Um único output por LLM call. |
| Revisor pós-contestação | Agente separado (`agente_revisor_v1` modo pós-contestação). Human Reviewer sempre decisão final. |
| Curadoria: obrigatória ou amostral? | Amostral e paralela — não bloqueia `evaluation_finalized`. |
| Sampling rules | 6 regras configuráveis por campanha: `score_extremes`, `deploy_baseline`, `score_outlier`, `na_excess`, `random_baseline`, `reviewer_signal`. |
| Calibração feedback | `CalibrationNote` gerada pelo curador (AI sinal + complemento humano) → knowledge namespace → RAG. |
| max_rounds | `ContestationPolicy.max_rounds` (padrão 3, máx 5). Infraestrutura já existe no Arc 6. |
| Acesso do revisor à conversa | `ReplayContext` completo via `evaluation_context_get` para ambos os modos. |

---

## Arquivos a Criar/Modificar

| Arquivo | Ação |
|---|---|
| `evaluation-api/prisma/schema.prisma` | `ContestationThread`, `CurationReview`, `CalibrationNote`, `CurationSamplingRule`, campos Campaign/Instance |
| `evaluation-api/src/routes/contestation.py` | `/contest`, `/review`, `/threads`, `/curations`, `/calibration-notes` |
| `evaluation-api/src/services/session_metrics_extractor.py` | Computa `session_metric.*` pós-`session_closed`, preenche critérios `auto_computed` |
| `evaluation-api/src/services/sampling_engine.py` | Avalia regras de curadoria pós-finalization |
| `packages/schemas/src/evaluation.ts` | Todos os novos tipos + `EvaluationEventSchema` atualizado |
| `infra/registry/agents/agente_pre_revisor_v1.yaml` | Novo agent type pré-publicação |
| `infra/registry/agents/agente_revisor_v1.yaml` | Agent type pós-contestação |
| `infra/registry/skills/skill_revisao_treplica_v1.yaml` | `max_rounds` dinâmico, pre_review step condicional |
| `mcp-server-plughub/src/tools/evaluation.ts` | `evaluation_pre_review_submit`, `evaluation_review_submit` |
| `platform-ui/src/modules/evaluation/` | `EvaluationReviewPage`, `CuradoriaPage`, `CalibrationDashboard` |
| `analytics-api/src/consumers/calibration_consumer.py` | Consumer `calibration.events` |
| `analytics-api/src/routes/evaluator_calibration.py` | `GET /reports/evaluator-calibration` |

→ Ver [arc6-evaluation.md](arc6-evaluation.md) para contexto do Arc 6 base.
→ Ver [arc6-phase2-observability.md](arc6-phase2-observability.md) para integração com deploy epochs.
