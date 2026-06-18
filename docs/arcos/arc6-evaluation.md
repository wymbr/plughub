# Arc 6 — Plataforma de Avaliação de Qualidade

> Última atualização: 2026-06-17 · Estado: Arc 16 + S2.2 (avaliador real campaign-driven validado)

Plataforma completa de avaliação de qualidade de interações: formulários configuráveis, campanhas de amostragem, agentes avaliadores com RAG, revisão humana, contestação e relatórios analíticos.

## Reconciliação Arc 6 + Arc 13 (em andamento — fonte de verdade: spec)

> Arquitetura-alvo e estado-atual×alvo em
> [`docs/product/evaluation-reconciliation-spec.md`](../product/evaluation-reconciliation-spec.md).
> Alguns ✅ abaixo/neste doc descrevem o baseline, não o alvo (correção = T16/G-DOCS).

### T6a — Modelo do critério enriquecido + normalização-na-leitura (2026-06-18) ✅

Primeiro chunk da T6 (form como fonte única, §5.3/§16.1). **Add-only, retrocompatível,
sem reescrita de dados.** Validado verde via `infra/test/test_t6a_form_model.sh`.

- **`@plughub/schemas` (`EvaluationCriterionSchema`)** ganhou campos opcionais: `question`
  (canônico; cai pra `description`), `scoring_guidance`, `min_score`, `choice_scores`,
  `true_score`/`false_score`, `na_guidance`, `applies_when`, `evidence_required`,
  `contestable`. Helpers `deriveContestable(type)` (auto_computed→false) e
  `deriveEvidenceRequired(type)` (score/boolean→true) — **fonte única** da regra de
  derivação, reusada por backend, UI (T6c) e agregação (T7).
- **evaluation-api `normalize_form()`** preenche os derivados/default **na leitura**
  (`get_form`/`list_forms`/`create_form`/`update_form`) — a "migração sem reescrita":
  forms legados expõem `type=score`, `question=description`, `min_score=0`,
  `evidence_required`/`contestable` derivados, **sem tocar o JSONB armazenado**. Campos
  explícitos nunca são sobrescritos.
- DB/router já tratam `dimensions` como JSONB/`list[dict]` opaco → novos campos fazem
  round-trip sem mudança de schema. **Sem migração.**

### T6b — Deploy lifecycle do form + snapshots imutáveis de versão (2026-06-18) ✅

Versionamento alinhado ao Skill Deploy Lifecycle (§16.1). **Tem schema novo** (aplicado por
`ensure_schema` no boot). Validado verde via `infra/test/test_t6b_form_versioning.sh`.

- **Schema:** `evaluation.forms.deploy_status` (`draft|published`, CHECK idempotente; ortogonal
  ao `status` draft/active/archived) + tabela imutável `evaluation.form_versions`
  (PK `(form_id, version)`) — snapshot da definição por versão publicada.
- **db (`db.py`):** `publish_form` (snapshot `ON CONFLICT DO NOTHING` + `deploy_status=published`;
  idempotente — a versão publicada nunca muda), `get_form_version` (lê o snapshot; fallback ao
  form vivo p/ legados), `list_form_versions`, `latest_published_version`. `update_form`
  **bifurca um novo draft** (`version+1`, `deploy_status=draft`) ao editar um form publicado;
  drafts editam in-place, snapshot intacto.
- **router:** `POST /forms/{id}/publish`, `GET /forms/{id}/versions`, `GET /forms/{id}/versions/{version}`.
- **Pin:** o sampling (`_sample_one_target`) fixa a **versão publicada** na instance
  (`form_version = latest_published_version ?? versão viva`), substituindo o stub `=1` da T2.

**Fronteira (não-bug):** o **avaliador ler o snapshot pinado** é a **T7** (reconstrói o caminho
do avaliador/saída form-driven). No T6b o pin é gravado e os snapshots existem; o consumo é T7.

Pendente na T6: **T6c** (FormsPage: seletor de `type`, `scoring_guidance`, editor de opções,
`na_guidance`, toggle `evidence_required`, `auto_source`, controles de versão/publish).

### T7a — Agregação determinística form-driven + validação no ingest (2026-06-18) ✅

Primeiro chunk da T7 (form como fonte única da NOTA, §5.2/§16.2). **Code-only, sem migração.**
Validado verde via `infra/test/test_t7a_aggregation.sh`.

- **`scoring.py` (novo, lógica pura):** `aggregate_scores(form, criterion_responses)` recomputa a
  nota bottom-up pelos pesos/tipos do form (`na`/`text` fora; pesos re-normalizados;
  score/auto→`score`, boolean→`true/false_score`, choice→`choice_scores`, tudo 0–10) →
  `(overall, [{dimension_id, score}])`. `validate_criterion_responses` → violações (criterion
  inexistente, regra de `na`, faixa).
- **`_ingest_core`:** carrega o **snapshot pinado** (`get_form_version` pela `form_version` da
  instance; fallback ao form vivo) e **descarta a `overall_score` recebida**, usando a
  recomputada (e `normalized_score`). Valida `criterion_responses`: `strict_validation=True` na
  rota HTTP `/ingest` → **422**; consumer real e seeder passam `False` (logam e seguem —
  endurecer/forçar shape é T7b). Threads round-1 nascem **por critério** de `criterion_responses`
  (author `evaluator_ai`; fallback `dimension_threads` legado). Resposta do ingest passa a
  incluir `overall_score` + `final_scores_by_dimension`.

**Fronteira (T7b):** o conveyance tool-use nativo (JSON Schema do form ao `reason`),
`output_schema` dinâmico no skill e a **remoção dos shims** ficam para o T7b.

### T7b-1 — ai-gateway: reason aceita JSON Schema via tool-use nativo (2026-06-18) ✅

Primeiro sub-chunk do conveyance (§5.4). **Code-only no ai-gateway.** Validado verde ao vivo
(`infra/test/test_t7b1_reason_toolschema.sh`, Claude real) + 17 unit tests
(`tests/test_reason.py`, validador recursivo).

- **`ReasonRequest.json_schema`** opcional: quando presente, o `reason` usa **tool-use nativo**
  (uma tool cujo `input_schema` é o JSON Schema **montado upstream do form** — o ai-gateway
  **não monta nada**, só repassa) com `tool_choice` forçado; ausente → caminho flat (compat).
- **`LLMProvider.call(..., force_tool=None)`** — Anthropic mapeia `tool_choice={"type":"tool",
  "name":...}`, OpenAI `{"type":"function",...}`.
- **`reason._process_tool_use`** lê `tool_calls[0].input`, valida com `_validate_json_schema`
  (validador recursivo lite: object/array/number/string/boolean, `required`=presença de chave,
  `enum`, `min/max`, `nullable` via `nullable:true` ou `type:[...,"null"]`) e **re-tenta** até 3×
  com correção — a rede de segurança do §5.4.

**Fronteira (T7b-2/3):** montar o JSON Schema do snapshot do form no `evaluation_context_get`
+ skill `agente_avaliacao_v1.yaml` usar o schema dinâmico (T7b-2); remover os shims do
`evaluation_submit` + `evaluation_rubric_v3` fixo (T7b-3).

### T7b-2 — Composição do JSON Schema do form + skill form-driven (2026-06-18) ✅

Liga o avaliador ao conveyance do T7b-1. **2a** (skill-flow-engine) + **2b** (composição/skill).

- **T7b-2a (skill-flow-engine):** `ReasonStep` ganhou `json_schema` (inline) e
  `json_schema_ref` (JSONPath resolvido do `pipeline_state`); `reason.ts` resolve e repassa
  `json_schema` ao ai-gateway e **pula a validação estática local** quando presente (o
  ai-gateway valida via tool-use). Tipos de `aiGatewayCall` (executor+engine) + runners
  (`skill-flow-worker`, `skill-flow-service`) forwardam `json_schema`. 3 unit tests.
- **T7b-2b (mcp-server + skill):** `buildEvaluationOutputSchema(form)` deriva o JSON Schema
  (`criterion_responses[]` com `criterion_id` enum dos critérios não-auto, `score` 0..max
  nullable, `na`, `justification`, `evidence`) e o `evaluation_context_get` expõe
  `evaluation_output_schema`. O skill `agente_avaliacao_v1.yaml` referencia via
  `json_schema_ref: "$.pipeline_state.eval_context.evaluation_output_schema"`. `composite_score`
  do `evaluation_submit` virou opcional (a nota é recomputada no ingest — T7a); removidos os
  mapeamentos mortos `composite_score`/`dimension_threads` no submit do skill.

**Validação:** o e2e completo do avaliador está bloqueado pela infra de replay/alocação do
demo (o `session-replayer` curto-circuita a alocação no cache-hit; sessões antigas não
re-hidratam). A substância do conveyance foi validada via **proxy**
(`infra/test/test_t7b2_schema_conveyance.sh`): o mesmo envelope que `buildEvaluationOutputSchema`
produz → `/v1/reason` tool-use → `criterion_responses` conforme (incl. `score` nullable). O
T7b-1 já provara o tool-use ao vivo. O e2e natural confirma quando houver sessão real fresca.

**Fronteira (T7b-3):** remover os shims do `evaluation_submit` (`observation→justification`,
default `evidence_entries`, coerção `compliance_flags`) + o `evaluation_rubric_v3` fixo.

---

## Evolução posterior

Este documento descreve o Arc 6 Fase 1. Partes substanciais — em especial o fluxo de revisão e contestação — foram **reescritas por arcos posteriores**. Ao consultar este doc, considere:

- **(a) Arc 13 — Review, Contestation & Calibration** reescreveu substancialmente o fluxo de review/contestação descrito aqui. O modelo atual usa **contestação por dimensão** (`dimension_threads[]` com `evidence_entries[]` obrigatório por dimensão), `evaluation_finalized` como única fonte de verdade para relatórios de qualidade, dois agentes novos (`agente_pre_revisor_v1` — gate de qualidade pré-publicação; `agente_revisor_v1` — árbitro pós-contestação), além de curadoria amostral e calibração para o fluxo de agentes AI. As afirmações sobre `EvaluationContestation`/`ContestationRound` simples e `available_actions` neste doc estão **superadas** pelo Arc 13. Ver [`arc13-review-contestation.md`](arc13-review-contestation.md).
- **(b) Arc 6 Fase 2 — Observabilidade de Mudanças** adicionou comparação estruturada de qualidade por **deploy epoch** (Dual-Slice Comparison, deploy timeline, endpoints `quality-comparison`/`quality-timeseries`). Ver [`arc6-phase2-observability.md`](arc6-phase2-observability.md).
- **(c) UI**: além das páginas descritas neste doc, o módulo de avaliação ganhou `CalibrationDashboard` (`/evaluation/calibration` — calibration score por skill version) e `CuradoriaPage` (`/evaluation/curadoria` — fila de curadoria do feedback loop RAG), ambas introduzidas pelo Arc 13.
- **(d) S2.2 — avaliador real campaign-driven (validado 2026-06-17)**: ver § "Caminho do avaliador real" abaixo.

## Caminho do avaliador real (campaign-driven) — validado 2026-06-17

Cadeia ponta-a-ponta provada com uma sessão webchat **real** (ver `CHANGELOG.md` 2026-06-17):

```
sessão real → POST /v1/evaluation/instances (scheduled)
  → POST /v1/evaluation/campaigns/{id}/dispatch  (admin; emite evaluation.requested por instância scheduled)
  → session-replayer: monta ReplayContext (transcript + form + campaign/instance) em {tenant}:replay:{sid}:context
  → routing-engine EvaluationConsumer → POST skill-flow-service /execute (flow agente_avaliacao_v1, session_id = evaluation_id)
  → agente_avaliacao_v1: login → evaluation_context_get → (RAG) → evaluate (reason, Claude real) → evaluation_submit
  → evaluation_submit publica evaluation.completed em evaluation.events
  → evaluation-api ingest consumer (evaluation-api-ingest-consumer) → _ingest_core
  → EvaluationResult no Postgres (overall_score) + EvaluationInstance → completed + ContestationThread round=1 por dimensão
```

**Invariantes/decisões deste caminho:**

- O flow do avaliador **não dá `claim`** na instance. O ciclo de vida (`scheduled → completed`) é avançado pelo
  **ingest consumer** ao processar `evaluation.completed`, não pelo agente. O `evaluator_agent_id` da instance
  permanece `null` (o `evaluator_id` do resultado é o `evaluation_id`/`participant_id` do avaliador).
- **Persistência**: `evaluation.completed` é consumido por DOIS destinos independentes — analytics-api/clickhouse-consumer
  (ClickHouse, só analytics) **e** evaluation-api (`_ingest_from_completed_event` → `_ingest_core`, Postgres). As leituras
  de `GET /v1/evaluation/results` e a página **Avaliações** vêm do **Postgres**. O consumer é **idempotente** (pula
  instance já `completed`; `auto_offset_reset=latest`, então só vê eventos publicados após subir → re-dispatch p/ revalidar).
- **Transcript**: o step `reason` lê `$.pipeline_state.eval_context.context.events` (o campo do `ReplayContext` é
  `events`, **não** `replay_events` — bug latente corrigido no gate; sessões vazias o mascaravam).
- **Shim de compat no `evaluation_submit`** (pendente da revisão form-driven, ver `TODO.md`): `dimension_threads`
  normaliza `observation→justification` + default `evidence_entries=[]` com `score` nullable; `criterion.score` nullable
  (N/A); `compliance_flags` coage objeto→string. Causa: prompt `evaluation_rubric_v3` **fixo** + `_format_schema` do
  ai-gateway transmite o `output_schema` de forma **lossy** (só top-level) → o LLM adivinha o shape. A revisão unifica
  o contrato (YAML `output_schema` ≡ submit Zod), transmite o schema aninhado e parametriza o prompt pela form,
  removendo o shim.
- **Sessão sem dados** *(backlog)*: sessão "magra" ainda falha duro (`overall_score=null` × `composite_score` obrigatório).
  Contrato escolhido: avaliador marca a instance `skipped`/`error` sem chamar submit (ver `TODO.md`).

## Novos pacotes

- `packages/evaluation-api/` — Python FastAPI, porta 3400. Ciclo de vida completo de formulários, campanhas, instâncias, resultados e contestações.
- `packages/mcp-server-knowledge/` — TypeScript MCP Server. Base de conhecimento vetorial (pgvector) para RAG nos agentes avaliadores.

## Novos schemas em `@plughub/schemas`

| Schema | Arquivo | Descrição |
|---|---|---|
| `EvaluationForm`, `EvaluationCriterion` | `evaluation.ts` | Formulário com critérios configuráveis |
| `EvaluationCampaign`, `SamplingRules`, `ReviewerRules` | `evaluation.ts` | Campanha de amostragem com regras |
| `ContestationPolicy`, `ContestationRound` | `evaluation.ts` | Política de contestação configurável por campanha (Arc 6 v2) |
| `EvaluationPermission` | `evaluation.ts` | Permissão 2D usuário × (pool \| campanha) (Arc 6 v2) |
| `EvaluationInstance` | `evaluation.ts` | Instância de avaliação de uma sessão |
| `EvaluationResult`, `EvaluationCriterionResponse` | `evaluation.ts` | Resultado com respostas por critério |
| `EvaluationResultWithActions` | `evaluation.ts` | Resultado + `available_actions` computado server-side (Arc 6 v2) |
| `EvaluationContestation` | `evaluation.ts` | Contestação de resultado |
| `EvaluationEvent` | `evaluation.ts` | Evento Kafka `evaluation.events` |
| `KnowledgeSnippet` | `evaluation.ts` | Snippet da base de conhecimento |

### EvaluationForm / EvaluationCriterion

```typescript
EvaluationCriterion {
  id: string                  // criterion_id único no formulário
  label: string               // "Seguiu protocolo de saudação"
  description: string         // instrução para o avaliador
  weight: number              // 0.0–1.0, sum of all criteria = 1.0
  type: "score" | "pass_fail" | "text" | "na_allowed"
  options?: { value: number; label: string }[]   // para score com escala customizada
}

EvaluationForm {
  form_id: string
  tenant_id: string
  name: string
  description?: string
  criteria: EvaluationCriterion[]
  knowledge_namespace?: string    // namespace RAG para snippets relevantes
  active: boolean
  created_at, updated_at: string
}
```

### EvaluationCampaign / SamplingRules / ReviewerRules

```typescript
SamplingRules {
  mode: "all" | "percentage" | "fixed"
  rate?: number                 // 0.0–1.0 (modo percentage)
  every_n?: number              // N (modo fixed — amostra 1 a cada N sessões)
  min_duration_s?: number       // ignora sessões mais curtas que N segundos
  agent_type_ids?: string[]     // whitelist de agent_type_ids (vazio = qualquer)
  pool_ids?: string[]           // whitelist de pool_ids (vazio = qualquer)
  channels?: string[]           // whitelist de canais (vazio = qualquer)
  outcome_filter?: string[]     // whitelist de outcomes de sessão (vazio = qualquer)
  default_priority?: number     // prioridade padrão 1–10 (padrão: 5)
  priority_overrides?: {field: string; value: string; priority: number}[]
}

ReviewerRules {
  auto_approve_above: number    // score ≥ threshold → approved sem revisão humana
  auto_reject_below: number     // score < threshold → rejected sem revisão humana
  require_human_review: boolean // força revisão humana independente do score
}

// Política de contestação configurável por campanha
ContestationRound {
  round_number:     number        // 1-based
  contestation_roles: string[]   // roles que podem contestar neste round
  review_roles:     string[]      // roles que podem revisar neste round
  authority_level:  string        // "supervisor" | "manager" | "director"
  review_deadline_hours: number   // SLA do round (business_hours: true implícito)
}

ContestationPolicy {
  contestation_roles: string[]           // roles globais que podem contestar
  review_roles_by_round: Record<number, string[]>  // role por round (herda contestation_roles como fallback)
  authority_by_round: Record<number, string>       // authority_level por round
  review_deadline_hours: number          // SLA padrão de revisão
}

EvaluationCampaign {
  campaign_id: string
  tenant_id: string
  name: string
  description?: string
  form_id: string
  status: "draft" | "active" | "paused" | "closed"
  sampling_rules: SamplingRules
  reviewer_rules: ReviewerRules
  contestation_policy?: ContestationPolicy   // configura ciclos de revisão/contestação
  review_workflow_skill_id?: string          // skill YAML motor de estado (ex: "skill_revisao_treplica_v1")
  // Novos campos (2026-05-13)
  evaluation_pool_id?: string      // pool sob avaliação — hard filter em check_sample
  evaluation_calendar_id?: string  // calendário de SLA — usado em compute_expires_at (business hours)
  gateway_config_ids?: string[]    // GatewayConfig IDs dos agentes avaliadores (reservado para AI Gateway)
  // Campos calculados (read-only)
  total_instances: number
  completed: number
  pending: number
  in_review: number
  avg_score: number | null
  created_at, updated_at: string
}
```

### EvaluationInstance / EvaluationResult / EvaluationCriterionResponse

```typescript
EvaluationCriterionResponse {
  criterion_id: string
  score?: number        // valor numérico (para type=score)
  passed?: boolean      // (para type=pass_fail)
  na: boolean           // critério marcado como N/A
  evidence?: string     // trecho da transcrição usado como evidência
  note?: string         // observação do avaliador
}

EvaluationResult {
  result_id: string
  instance_id: string
  session_id: string
  tenant_id: string
  evaluator_id: string              // instance_id do agente avaliador
  form_id: string
  campaign_id?: string
  criterion_responses: EvaluationCriterionResponse[]
  overall_score: number             // ponderado pelos weights dos critérios
  eval_status: "submitted" | "under_review" | "reviewed" | "contested" | "locked"
  locked: boolean                   // resultado finalizado, imutável
  lock_reason?: string              // "review_timeout" | "max_rounds_reached" | "manual"
  compliance_flags: string[]        // ["sla_breached", "escalation_required"]
  review_note?: string              // nota do revisor humano
  reviewed_by?: string
  reviewed_at?: string
  timestamp: string                 // ISO-8601 de submissão
  // Campos do motor de workflow (Arc 6 v2)
  workflow_instance_id?: string     // UUID da instância workflow-api associada
  resume_token?: string             // token atual para retomar o workflow (TTL = deadline do suspend)
  action_required?: string          // "review" | "contestation" | null (persisted from workflow.events consumer)
  current_round: number             // round atual do ciclo (0 = pré-revisão)
  deadline_at?: string              // ISO-8601 do prazo do round atual
}

// Retornado pelo endpoint GET /v1/evaluation/results/{id}?caller_user_id=
// Campo adicional computado server-side — nunca persisted no banco
EvaluationResultWithActions extends EvaluationResult {
  available_actions: ("review" | "contest")[]   // [] quando locked ou sem permissão
  action_context?: {
    deadline_at: string
    round: number
    authority_level: string
  }
}
```

## evaluation-api (porta 3400)

### Forms CRUD

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/v1/evaluation/forms?tenant_id=` | Lista formulários ativos |
| `POST` | `/v1/evaluation/forms` | Cria formulário |
| `PATCH` | `/v1/evaluation/forms/{form_id}` | Atualiza formulário |
| `DELETE` | `/v1/evaluation/forms/{form_id}` | Remove formulário |

### Campaigns CRUD + controle

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/v1/evaluation/campaigns?tenant_id=` | Lista campanhas |
| `POST` | `/v1/evaluation/campaigns` | Cria campanha |
| `POST` | `/v1/evaluation/campaigns/{id}/pause` | Pausa campanha |
| `POST` | `/v1/evaluation/campaigns/{id}/resume` | Retoma campanha |

### Instances lifecycle

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/v1/evaluation/instances?campaign_id=&status=` | Lista instâncias por campanha |

Instâncias são criadas automaticamente pelo **sampling engine** ao consumir eventos `conversations.session_closed`. O engine avalia `SamplingRules` e cria a instância se a sessão for selecionada.

### Results

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/v1/evaluation/results?tenant_id=&campaign_id=&evaluator_id=` | Lista resultados |
| `GET` | `/v1/evaluation/results/{id}?caller_user_id=` | Detalhe com `available_actions` computado server-side |
| `POST` | `/v1/evaluation/results/{result_id}/review` | Revisor humano age (requer JWT + permissão de review no pool/campanha) |

Body de review: `{ decision: "approved" | "rejected", round: number, review_note? }`. O campo `round` é anti-replay — deve ser igual a `result.current_round` ou o servidor retorna `409`.

### Contestations

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/v1/evaluation/contestations?tenant_id=&result_id=` | Lista contestações |
| `POST` | `/v1/evaluation/contestations` | Cria contestação (requer JWT + permissão de contest no pool/campanha) |
| `POST` | `/v1/evaluation/contestations/{id}/adjudicate` | Adjudica contestação — mantido para compatibilidade (fluxo legado sem workflow) |

Body de contestation: `{ result_id, reason, round: number }`. O campo `round` é anti-replay — deve ser igual a `result.current_round`.

### Reports

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/v1/evaluation/reports/campaigns/{id}` | Relatório por campanha |
| `GET` | `/v1/evaluation/reports/agents?tenant_id=&pool_id=` | Relatório por agente |

## Auth

### Operações admin (CRUD de formulários e campanhas)

Requerem `X-Admin-Token` header.

### Operações de revisão e contestação

Requerem `Authorization: Bearer <jwt>` com claims `sub` (user_id), `roles[]` e `module_config`.

O evaluation-api extrai `caller.user_id` do `sub` do JWT para registrar `reviewed_by` / `contested_by`. Permissões verificadas via ABAC `module_config.evaluation.revisar` / `module_config.evaluation.contestar` (sem DB lookup). Graceful degradation: sem module_config → permite (conta legacy). Escopo: scope list vazio → acesso global; não-vazio → `pool:{pool_id}` deve estar na lista.

**Nota:** a tabela `evaluation.permissions` e os endpoints `GET/POST/PATCH/DELETE /v1/evaluation/permissions` foram removidos. Permissões de avaliação são configuradas exclusivamente via ABAC no auth-api (`PUT /auth/users/{id}/module-config`), eliminando o risco de inconsistência entre os dois sistemas.

### _check_abac_permission helper

```python
def _check_abac_permission(jwt_payload, field, pool_id):
    # Extrai module_config do JWT
    # Retorna True se module_config.evaluation[field].access != "none"
    # e pool_id está na lista de scope ou scope está vazio (global)
```

## mcp-server-knowledge

MCP Server separado para a base de conhecimento vetorial dos agentes avaliadores.

### PostgreSQL schema (pgvector)

```sql
CREATE TABLE knowledge_snippets (
    snippet_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    TEXT NOT NULL,
    namespace    TEXT NOT NULL,           -- ex: "politicas_sac", "sla_contrato"
    content      TEXT NOT NULL,
    embedding    vector(1536),            -- OpenAI text-embedding-3-small
    source_ref   TEXT,                   -- documento de origem
    metadata     JSONB DEFAULT '{}',
    created_at   TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX ON knowledge_snippets USING ivfflat (embedding vector_cosine_ops);
CREATE INDEX ON knowledge_snippets (tenant_id, namespace);
```

### Tools expostos

| Tool | Descrição |
|---|---|
| `knowledge_search` | Busca semântica top-K no namespace, retorna `KnowledgeSnippet[]` |
| `knowledge_upsert` | Insere/atualiza snippet com embedding automático |
| `knowledge_delete` | Remove snippet por snippet_id |

### API REST (proxied via Vite `/v1/knowledge`)

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/v1/knowledge/search?tenant_id=&query=&namespace=&top_k=` | Busca semântica |
| `POST` | `/v1/knowledge/snippets` | Upsert snippet |
| `DELETE` | `/v1/knowledge/snippets/{id}` | Remove snippet |

## Agents

### agente_avaliacao_v1 — form-aware + RAG + evidência

`packages/skill-flow-engine/skills/agente_avaliacao_v1.yaml`

Fluxo:
```
carregar_contexto (invoke: evaluation_context_get)
  → ReplayContext: stream events, form definition, campaign_context, knowledge_snippets (top-5)

avaliar_criterios (reason LLM):
  - Para cada critério do formulário:
    - Analisa transcrição → score / pass_fail / N/A
    - Extrai evidence (trecho textual)
    - Computa overall_score ponderado
  - Incorpora knowledge_snippets como contexto normativo
  - Detecta compliance_flags (sla_breached, escalation_required, protocol_violation)

submeter_resultado (invoke: evaluation_submit):
  - criterion_responses[] com evidence por critério
  - overall_score, compliance_flags
  - eval_status: "submitted"
```

**Fallback:** se agent-registry retorna HTTP 422 (YAML sem `complete`/`escalate`), `_load_yaml_fallback()` no orchestrator-bridge lê o arquivo YAML diretamente.

### agente_reviewer_ia_v1 — auto-aprovação/rejeição

`packages/skill-flow-engine/skills/agente_reviewer_ia_v1.yaml`

Fluxo:
```
carregar_resultado (invoke: evaluation_context_get)
  → EvaluationResult + critérios + threshold rules

decisao_automatica (choice):
  overall_score >= reviewer_rules.auto_approve_above → aprovar
  overall_score <  reviewer_rules.auto_reject_below  → rejeitar
  reviewer_rules.require_human_review eq true        → fila_humana
  default                                            → fila_humana

aprovar (invoke: evaluation_submit):
  eval_status: "approved", review_note: "Auto-aprovado por score ≥ threshold"

rejeitar (invoke: evaluation_submit):
  eval_status: "rejected", review_note: "Auto-rejeitado por score < threshold"

fila_humana (notify agents_only):
  Sinaliza ao supervisor para revisão manual
```

## Modelo de Permissão 2D — usuário × (pool | campanha)

Eixo de permissão independente do papel do usuário no sistema: um mesmo usuário pode ter permissão de contestar num pool e de revisar em outro, e ter ambas em uma campanha específica.

### PostgreSQL schema

```sql
CREATE TABLE evaluation_permissions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    scope_type  TEXT NOT NULL CHECK (scope_type IN ('pool', 'campaign', 'global')),
    scope_id    TEXT,           -- pool_id ou campaign_id; NULL para global
    can_contest BOOL NOT NULL DEFAULT FALSE,
    can_review  BOOL NOT NULL DEFAULT FALSE,
    granted_by  TEXT NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT now(),
    UNIQUE (tenant_id, user_id, scope_type, scope_id)
);
```

### Resolução de permissão (herança de escopo)

Resolução do mais específico para o mais geral. Permissões são acumulativas (union), não se excluem:

```
campaign-level  >  pool-level  >  global
```

Exemplo: usuário X com `can_review=true` no pool A e `can_contest=true` na campanha C (que usa o pool A) → X pode revisar via herança do pool, e contestar via herança da campanha. Ambas as permissões são válidas.

```python
async def resolve_permissions(tenant_id, user_id, campaign_id, pool_id) -> set[str]:
    rows = await db.fetch("""
        SELECT can_contest, can_review FROM evaluation_permissions
        WHERE tenant_id = $1 AND user_id = $2
          AND (
            (scope_type = 'campaign' AND scope_id = $3)
            OR (scope_type = 'pool'     AND scope_id = $4)
            OR (scope_type = 'global')
          )
    """, tenant_id, user_id, campaign_id, pool_id)

    permissions = set()
    for row in rows:
        if row["can_contest"]: permissions.add("contest")
        if row["can_review"]:  permissions.add("review")
    return permissions
```

### `available_actions` — campo computado server-side

O endpoint `GET /v1/evaluation/results/{id}?caller_user_id=` devolve `available_actions` já calculado, combinando o estado do workflow (`action_required` + `locked`) com as permissões do usuário. A UI nunca computa permissão localmente — apenas lê o campo:

```
available_actions = []
if not locked and action_required == "review"       and "review"  in perms → ["review"]
if not locked and action_required == "contestation" and "contest" in perms → ["contest"]
```

| `action_required` | Permissão do caller | `available_actions` | Botões na UI |
|---|---|---|---|
| `"review"` | `can_review=true` | `["review"]` | Revisar ✓ / Contestar ✗ |
| `"review"` | `can_contest=true` | `[]` | Revisar ✗ / Contestar ✗ |
| `"contestation"` | `can_contest=true` | `["contest"]` | Revisar ✗ / Contestar ✓ |
| `null` (outra parte age) | qualquer | `[]` | Ambos desabilitados + mensagem "Aguardando {authority}" |
| `null` + `locked=true` | qualquer | `[]` | Badge "Encerrado" |

**Defesa em profundidade**: a UI desabilita botões com base em `available_actions`, mas o endpoint de submit repete a verificação de permissão no servidor. O servidor nunca confia no estado calculado pelo cliente.

## Workflow como Motor de Contestação/Revisão

O ciclo de revisão/contestação é executado pelo Workflow API (Arc 4) como motor de estado. O YAML da skill define quantos rounds existem, timeouts e alçadas — sem lógica hardcoded no evaluation-api. Mudar o ciclo de um cliente = atualizar um YAML via `PUT /v1/skills/{id}`.

### Ligação campanha → workflow skill

```
EvaluationCampaign.review_workflow_skill_id = "skill_revisao_simples_v1"
                                             | "skill_revisao_treplica_v1"
                                             | qualquer skill configurada pelo cliente
```

### Ciclo de vida completo

```
1. EvaluationResult submetido
   → evaluation-api: POST /v1/workflow/trigger
     { flow_id: campaign.review_workflow_skill_id,
       origin_session_id: result.session_id,
       context: { result_id, campaign_id, tenant_id } }
   → workflow entra no primeiro suspend (aguardar_revisao)
   → workflow.events consumer atualiza o result:
       action_required = "review", current_round = 1, deadline_at, resume_token

2. Usuário age na UI — endpoint ecoa o round recebido (anti-replay)
   POST /v1/evaluation/results/{id}/review   { decision, round: 1 }
   POST /v1/evaluation/contestations         { result_id, round: 1, reason }
   → evaluation-api verifica permissão (resolve_permissions)
   → verifica anti-replay: round_body == result.current_round ou rejeita 409
   → grava no banco (audit trail)
   → escreve no ContextStore:
       session.review_decision  = "approved" | "contested"
       session.reviewer_id      = caller.user_id
       session.round_echoed     = 1
   → POST /v1/workflow/resume { token: result.resume_token, decision: "input" }

3. Workflow lê @ctx.session.review_decision no choice step → transita
   → próximo suspend: escreve current_round incrementado no ContextStore
   → workflow.events consumer atualiza action_required, current_round, deadline_at, resume_token

4. Timeout: suspend expira sem retomada
   → workflow entra em on_timeout → congelar_resultado step
   → evaluation-api consumer: locked=true, lock_reason="review_timeout", action_required=null
   → qualquer chamada subsequente sobre o result_id retorna 409 Conflict — result locked
```

### Padrão do round counter — controlado pelo workflow, ecoado pela UI

O workflow escreve `@ctx.session.current_round` ao entrar em cada suspend. A UI lê o valor recebido no result e o devolve no submit. O evaluation-api usa esse valor para o anti-replay check. O YAML é o único lugar com lógica de quantas voltas existem.

```yaml
# Fragmento — o workflow incrementa o próprio contador
- id: incrementar_round
  type: invoke
  tool: context_write
  input:
    tag: session.current_round
    value: "{{add(@ctx.session.current_round, 1)}}"
  on_success: verificar_limite

- id: verificar_limite
  type: choice
  conditions:
    - field: "@ctx.session.review_decision"
      operator: eq
      value: "approved"
      next: encerrar_aprovado
    - field: "@ctx.session.current_round"
      operator: gt
      value: 3              # tréplica: único lugar onde o limite existe
      next: congelar_resultado
    - field: "@ctx.session.review_decision"
      operator: eq
      value: "contested"
      next: aguardar_contestacao
```

Clientes com réplica configuram `value: 2`; tréplica, `value: 3` — sem nenhuma alteração de código.

### ContextStore keys usadas pelo motor de avaliação

| Tag | Valor | Escrito por |
|---|---|---|
| `session.current_round` | `number` | Workflow (ao entrar no suspend) |
| `session.action_required` | `"review" \| "contestation"` | Workflow (ao entrar no suspend) |
| `session.review_decision` | `"approved" \| "contested"` | evaluation-api (antes do resume) |
| `session.reviewer_id` | `user_id` | evaluation-api (antes do resume) |
| `session.round_echoed` | `number` | evaluation-api (confirmação do anti-replay) |

TTL: os campos de workflow de avaliação usam TTL de 7 dias (`604800s`) — diferente do TTL padrão de 4h do ContextStore — para suportar ciclos de revisão longos. Configurável via Config API namespace `evaluation` key `workflow_context_ttl_s`.

### consumer `workflow.events` no evaluation-api

```python
async def on_workflow_event(event):
    result_id = event.get("context", {}).get("result_id")
    if not result_id:
        return

    if event["event_type"] == "workflow.suspended":
        step = event.get("suspended_at_step", "")
        action = "review" if "revisao" in step else "contestation" if "contestacao" in step else None
        await db.update_result_workflow_state(result_id,
            action_required  = action,
            current_round    = event["context"].get("current_round", 1),
            deadline_at      = event.get("resume_expires_at"),
            resume_token     = event.get("resume_token"),
        )

    elif event["event_type"] == "workflow.completed":
        lock_reason = event.get("context", {}).get("lock_reason", "completed")
        await db.update_result_workflow_state(result_id,
            action_required = None,
            resume_token    = None,
            locked          = True,
            lock_reason     = lock_reason,
        )
```

### Exemplo de YAML para ciclo com tréplica

`packages/skill-flow-engine/skills/skill_revisao_treplica_v1.yaml`

```yaml
id: skill_revisao_treplica_v1
entry: init_round
steps:
  - id: init_round
    type: invoke
    tool: context_write
    input: { tag: session.current_round, value: 1 }
    on_success: aguardar_revisao

  - id: aguardar_revisao
    type: suspend
    reason: input
    timeout_hours: 48
    business_hours: true
    on_resume:  { next: verificar_decisao }
    on_timeout: { next: congelar_resultado }

  - id: verificar_decisao
    type: choice
    conditions:
      - field: "@ctx.session.review_decision"
        operator: eq
        value: "approved"
        next: encerrar_aprovado
      - field: "@ctx.session.review_decision"
        operator: eq
        value: "contested"
        next: incrementar_round

  - id: incrementar_round
    type: invoke
    tool: context_write
    input:
      tag: session.current_round
      value: "{{add(@ctx.session.current_round, 1)}}"
    on_success: verificar_limite

  - id: verificar_limite
    type: choice
    conditions:
      - field: "@ctx.session.current_round"
        operator: gt
        value: 3
        next: congelar_resultado
    default: aguardar_contestacao

  - id: aguardar_contestacao
    type: suspend
    reason: input
    timeout_hours: 72
    business_hours: true
    on_resume:  { next: aguardar_revisao }
    on_timeout: { next: congelar_resultado }

  - id: congelar_resultado
    type: invoke
    tool: evaluation_lock
    input:
      result_id:   "@ctx.session.result_id"
      lock_reason: "review_timeout"
    on_success: encerrar
    on_failure: encerrar

  - id: encerrar_aprovado
    type: complete
    outcome: resolved

  - id: encerrar
    type: complete
    outcome: resolved
```

### Novos campos PostgreSQL em `evaluation_results`

```sql
ALTER TABLE evaluation_results
  ADD COLUMN workflow_instance_id UUID,
  ADD COLUMN resume_token         TEXT,
  ADD COLUMN action_required      TEXT CHECK (action_required IN ('review', 'contestation')),
  ADD COLUMN current_round        INT  NOT NULL DEFAULT 0,
  ADD COLUMN deadline_at          TIMESTAMPTZ,
  ADD COLUMN lock_reason          TEXT;

ALTER TABLE evaluation_contestations
  ADD COLUMN round_number     INT  NOT NULL DEFAULT 1,
  ADD COLUMN authority_level  TEXT;
```

### Novos campos Kafka `evaluation.events`

```json
{
  "event_type": "submitted | reviewed | contested | locked",
  "round_number": 1,
  "authority_level": "supervisor",
  "lock_reason": "review_timeout | max_rounds_reached | manual",
  ...
}
```

## session-replayer — extensões Arc 6

O `ReplayContext` foi estendido com campos de avaliação:

```python
@dataclass
class ReplayContext:
    # ... campos existentes ...
    evaluation_form:     dict | None     # formulário associado pela campanha
    campaign_context:    dict | None     # metadados da campanha (sampling, reviewer_rules)
    knowledge_snippets:  list[dict]      # top-K snippets do namespace do formulário
```

O Replayer busca `evaluation_form` e `campaign_context` via evaluation-api ao construir o `ReplayContext` quando `evaluation_instance_id` está presente no evento `evaluation.requested`.

## MCP tools — extensões Arc 6

### evaluation_context_get (estendido)

Retorna `ReplayContext` enriquecido com `evaluation_form`, `campaign_context` e `knowledge_snippets`. O agente avaliador vê os critérios do formulário e os snippets de conhecimento relevantes num único call.

### evaluation_submit (estendido)

```typescript
// Input Arc 6 (estendido)
{
  result_id?:           string      // novo resultado ou update de rascunho
  instance_id:          string
  session_id:           string
  form_id:              string
  campaign_id?:         string
  criterion_responses:  EvaluationCriterionResponse[]
  overall_score:        number
  eval_status:          string
  compliance_flags?:    string[]
  review_note?:         string
  reviewed_by?:         string
  // Comparison Mode (Arc 3 — mantido)
  comparison_turns?:    ComparisonTurn[]
  comparison_replay_outcome?:   string
  comparison_replay_sentiment?: number
}
```

## platform-ui — módulo de avaliação

6 páginas sob `/evaluation`:

| Rota | Arquivo | Roles | Descrição |
|---|---|---|---|
| `/evaluation/forms` | `FormsPage.tsx` | admin | CRUD de formulários com critérios |
| `/evaluation/campaigns` | `CampaignsPage.tsx` | supervisor, admin | Campanhas + KPIs em tempo real |
| `/evaluation/knowledge` | `KnowledgePage.tsx` | admin | Base de conhecimento vetorial |
| `/evaluation/avaliacoes` | `AvaliacoesPage.tsx` | operator, supervisor, admin | Tabela unificada: todas as avaliações, filtros completos, drill-down, ações disponíveis via ABAC |
| `/evaluation/reports` | `ReportsPage.tsx` | supervisor+ | Dashboard analítico (analytics-api) |

Nav group "Avaliação" adicionado ao `Sidebar.tsx`.

**`src/api/evaluation-hooks.ts`** — hooks de API completos:

| Hook / Função | Endpoint | Descrição |
|---|---|---|
| `useForms(tenantId)` | `GET /v1/evaluation/forms` | Lista formulários |
| `createForm`, `updateForm`, `deleteForm` | POST/PATCH/DELETE | CRUD |
| `useCampaigns(tenantId, pollMs)` | `GET /v1/evaluation/campaigns` | Lista campanhas (polling) |
| `createCampaign`, `pauseCampaign`, `resumeCampaign` | POST | Ações de campanha |
| `useInstances(campaignId, status, pollMs)` | `GET /v1/evaluation/instances` | Instâncias por campanha |
| `useResults(tenantId, campaignId, evaluatorId, pollMs)` | `GET /v1/evaluation/results` | Resultados |
| `reviewResult(resultId, body)` | `POST /v1/evaluation/results/{id}/review` | Revisão humana |
| `useContestations(tenantId, resultId)` | `GET /v1/evaluation/contestations` | Contestações |
| `createContestation`, `adjudicateContestation` | POST | Ações de contestação |
| `useCampaignReport(campaignId)` | `GET /v1/evaluation/reports/campaigns/{id}` | Relatório por campanha |
| `useAgentReport(tenantId, poolId)` | `GET /v1/evaluation/reports/agents` | Relatório por agente |
| `searchKnowledge(tenantId, query, namespace, topK)` | `GET /v1/knowledge/search` | Busca RAG |
| `upsertSnippet`, `deleteSnippet` | POST/DELETE | CRUD de snippets |
| `useEvaluationsAnalytics(tenantId, params, pollMs)` | `GET /reports/evaluations` | analytics-api ClickHouse |
| `useEvaluationsSummary(tenantId, params, pollMs)` | `GET /reports/evaluations/summary` | Sumário agregado |

Endpoints `/v1/evaluation` e `/v1/knowledge` proxied pelo Vite para porta 3400; `/reports` proxied para porta 3500 (analytics-api).

## analytics-api — ClickHouse Arc 6

### Tabelas

```sql
-- Estado atual de cada resultado (ReplacingMergeTree — latest eval_status wins)
CREATE TABLE analytics.evaluation_results (
    result_id        String,
    instance_id      String,
    session_id       String,
    tenant_id        String,
    evaluator_id     String,
    form_id          String,
    campaign_id      Nullable(String),
    overall_score    Float64,
    eval_status      String,
    locked           UInt8,
    compliance_flags Array(String),
    timestamp        DateTime,
    ingested_at      DateTime DEFAULT now()
) ENGINE = ReplacingMergeTree(ingested_at)
  ORDER BY (tenant_id, result_id);

-- Log append-only de eventos (submitted/reviewed/contested/locked)
CREATE TABLE analytics.evaluation_events (
    event_id      String,
    result_id     String,
    session_id    String,
    tenant_id     String,
    event_type    String,    -- "submitted" | "reviewed" | "contested" | "locked"
    actor_id      String,    -- evaluator_id / reviewed_by / contested_by
    eval_status   String,
    overall_score Nullable(Float64),
    timestamp     DateTime,
    ingested_at   DateTime DEFAULT now()
) ENGINE = MergeTree()
  ORDER BY (tenant_id, result_id, timestamp);
```

### Kafka consumer

Tópico `evaluation.events` adicionado a `_TOPICS` e `_PARSERS` em `consumer.py`.

`parse_evaluation_event(msg)` retorna dois rows por evento:
- `{"table": "evaluation_results", ...}` — estado atual do resultado (upsert)
- `{"table": "evaluation_events", ...}` — entrada do log de auditoria

`_write_row` despacha via `store.upsert_evaluation_result()` e `store.insert_evaluation_event()`.

### Endpoints analytics

| Endpoint | Filtros | Descrição |
|---|---|---|
| `GET /reports/evaluations` | `tenant_id`, `from_dt`, `to_dt`, `campaign_id`, `form_id`, `evaluator_id`, `eval_status`, `page`, `page_size`, `format` | Linhas individuais de `evaluation_results FINAL` |
| `GET /reports/evaluations/summary` | `tenant_id`, `from_dt`, `to_dt`, `campaign_id`, `form_id`, `group_by` | Agregação por `campaign_id` / `evaluator_id` / `form_id` / `date` |

**Campos do sumário:** `total_evaluated`, `count_submitted`, `count_approved`, `count_rejected`, `count_contested`, `count_locked`, `count_locked_flag`, `avg_score`, `min_score`, `max_score`, `score_excellent (≥0.9)`, `score_good (0.7–0.9)`, `score_fair (0.5–0.7)`, `score_poor (<0.5)`, `with_compliance_flags`.

**Proteção SQL injection:** `group_by` validado contra whitelist `{"campaign_id", "evaluator_id", "form_id", "date"}` antes de injetar na cláusula GROUP BY. Valores inválidos retornam ao default `campaign_id`.

## Kafka topics

| Topic | Producer | Consumer(s) |
|---|---|---|
| `evaluation.events` | evaluation-api (result submit + review + contestation + lock) | analytics-api → ClickHouse `evaluation_results` + `evaluation_events` |

**Payload `evaluation.events`:**
```json
{
  "event_type":        "submitted" | "reviewed" | "contested" | "locked",
  "result_id":         "uuid",
  "instance_id":       "uuid",
  "session_id":        "sess_...",
  "tenant_id":         "tenant_demo",
  "evaluator_id":      "agente_avaliacao_v1-001",
  "form_id":           "form_sac_padrao",
  "campaign_id":       "camp_...",
  "overall_score":     0.87,
  "eval_status":       "approved",
  "locked":            false,
  "compliance_flags":  [],
  "reviewed_by":       null,
  "contested_by":      null,
  "timestamp":         "ISO8601"
}
```

## Vite proxies adicionados (platform-ui)

| Prefixo | Target | Porta |
|---|---|---|
| `^/v1/evaluation` | evaluation-api | 3400 |
| `^/v1/knowledge` | mcp-server-knowledge | 3401 |

## Repository additions

```
plughub/
  packages/
    evaluation-api/               ← plughub-evaluation-api (Python FastAPI — porta 3400)
    mcp-server-knowledge/         ← mcp-server-knowledge (TypeScript MCP Server)
  packages/platform-ui/src/
    modules/evaluation/           ← 5 páginas: FormsPage, CampaignsPage, KnowledgePage,
    │                                          AvaliacoesPage (tabela unificada), ReportsPage
    api/evaluation-hooks.ts       ← hooks completos (evaluation-api + analytics-api)
```

## Tests

- `analytics-api/tests/test_consumer.py`: `TestParseEvaluationEvent` (14 assertions), `TestWriteRowDispatchEvaluation` (2 assertions)
- `analytics-api/tests/test_reports.py`: `TestQueryEvaluationsReport` (4 assertions), `TestQueryEvaluationsSummary` (4 assertions)
- Total analytics-api: **108/108**

### E2E scenarios

- `e2e-tests/scenarios/24_evaluation_campaign.ts` — 14 assertions (--evaluation flag)
- `e2e-tests/scenarios/25_evaluation_contestation.ts` — 10 assertions (--contestation flag)
- `e2e-tests/scenarios/26_ai_gateway_fallback.ts` — 10 assertions (--fallback flag; inference parts require ANTHROPIC_API_KEY)

## Arc 6 v2 — ✅ Implementado (Permissões 2D + Workflow Motor)

Todos os componentes abaixo foram implementados:

- ~~`evaluation_permissions` table + endpoints~~ → **removido**: permissões unificadas no ABAC (`module_config.evaluation.revisar` / `module_config.evaluation.contestar`); tabela dropada no DDL startup
- ✅ `_check_abac_permission(jwt_payload, field, pool_id)` em `router.py` — substitui `resolve_permissions()`; graceful degradation para tokens legacy sem `module_config`
- ✅ `EvaluationCampaign`: campos `review_workflow_skill_id` + `contestation_policy` (DDL + schema update em `db.py`)
- ✅ `EvaluationResult`: campos `workflow_instance_id`, `resume_token`, `action_required`, `current_round`, `deadline_at`, `lock_reason` (DDL em `db.py`)
- ✅ `EvaluationContestation`: campos `round_number`, `authority_level` (DDL em `db.py`)
- ✅ `GET /v1/evaluation/results/{id}` — `available_actions` computado server-side via `_compute_available_actions(result, jwt_payload, pool_id)` (ABAC; Bearer opcional)
- ✅ `POST /v1/evaluation/results/{id}/review` — JWT decode, `_check_abac_permission(…, "revisar", pool_id)`, anti-replay de `round`, ContextStore write, workflow resume
- ✅ `POST /v1/evaluation/contestations` — JWT decode, `_check_abac_permission(…, "contestar", pool_id)`, anti-replay de `round`, ContextStore write (`session.review_decision = "contested"`), workflow resume
- ✅ Consumer `workflow.events` no `evaluation-api/main.py` — `_on_workflow_event()`: atualiza `action_required`, `current_round`, `deadline_at`, `resume_token`, `locked`, `lock_reason` via `update_result_workflow_state()` e `lock_result()`
- ✅ Trigger de workflow ao submeter resultado: `POST /v1/workflow/trigger` com `flow_id = campaign.review_workflow_skill_id`
- ✅ `packages/skill-flow-engine/skills/skill_revisao_simples_v1.yaml` — ciclo simples (1 round, 6 steps)
- ✅ `packages/skill-flow-engine/skills/skill_revisao_treplica_v1.yaml` — tréplica (até 3 rounds, 10 steps); alterar `value: 3` para `value: 2` para réplica
- ✅ MCP tool `evaluation_lock` em `mcp-server-plughub/src/tools/evaluation.ts` — idempotente: 409 = já locked (tratado como sucesso)
- ✅ ContextStore TTL 7 dias: Config API namespace `evaluation` key `workflow_context_ttl_s = 604800` + 4 keys adicionais (`default_review_skill_id`, `review_deadline_hours`, `contestation_deadline_hours`, `auto_lock_on_workflow_complete`)
- ~~platform-ui: `EvaluationPermissionsPage.tsx`~~ → **removido**: permissões de avaliação configuradas na tela de usuários (AccessPage) via `ModulePermissionForm`
- ~~platform-ui: `ReviewPage.tsx` + `MyEvaluationsPage.tsx`~~ → **unificados** em `AvaliacoesPage.tsx` (`/evaluation/avaliacoes`): tabela com filtros completos (status, campanha, "Aguardando minha ação"), drill-down lateral, `available_actions` server-side via Bearer JWT + ABAC
- ✅ E2E scenarios 27/28: `27_evaluation_permissions.ts` (11 assertions, `--permissions`) + `28_evaluation_workflow_cycle.ts` (11 assertions, `--workflow-review`)

## Arc 6 v2 — Tests

- `e2e-tests/scenarios/27_evaluation_permissions.ts` — 11 assertions (--permissions flag): grant campaign/pool/global, list, update, resolve via available_actions, UNIQUE idempotency, revoke
- `e2e-tests/scenarios/28_evaluation_workflow_cycle.ts` — 11 assertions (--workflow-review flag; requires JWT_SECRET + workflow-api): submit → trigger → suspended → anti-replay 409 → review → ContextStore → workflow.completed → locked

## Invariantes desta arquitetura (nunca violar)

- Nunca computar `available_actions` no cliente — sempre vem do servidor
- Nunca pular a verificação de `round` no submit — `round_body != result.current_round` → 409
- Nunca escrever `resume_token` em logs — é um segredo de retomada do workflow
- Nunca modificar resultado com `locked=true` — qualquer tentativa retorna 409
- Nunca fazer `workflow/resume` sem antes gravar `session.review_decision` no ContextStore — o choice step do workflow depende desse valor
- O YAML da skill é o único lugar com lógica de quantos rounds existem — nunca hardcodar `max_rounds` no evaluation-api
