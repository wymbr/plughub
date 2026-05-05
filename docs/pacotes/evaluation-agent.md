# Módulo: Evaluation Agent

> Spec de referência: v24.0 seção 10.2, seção 14
> Responsabilidade: avaliar a qualidade de um atendimento encerrado a partir de
> um evento `evaluation.requested`, aplicando a evaluation skill declarada no pool,
> e persistir o resultado no ClickHouse via Kafka.

---

## Visão geral

O Evaluation Agent é um **agente nativo de orquestração** implementado como um
Skill Flow YAML genérico. Não é um serviço Python dedicado — é executado pelo
Routing Engine via `SkillFlowEngine.run()` exatamente como qualquer outro agente
orquestrador da plataforma.

Ao ser acionado por um evento `evaluation.requested`, o engine carrega o flow
do `agente_avaliacao_v1`, mapeia os campos do evento para o `sessionContext` e
executa os steps em sequência: busca do transcript, resolução de dados externos
declarados na evaluation skill, inferência via AI Gateway e publicação do resultado.

Dois YAML distintos governam o processo:

| YAML | Escopo | Gerenciado por |
|---|---|---|
| `agente_avaliacao_v1` (SkillFlow) | **Como** avaliar — orquestração genérica, único para todos os pools | Engenharia de plataforma |
| `eval_{pool}_{dominio}_v{n}` (evaluation skill) | **O que** avaliar — formulário de seções e itens, um por pool | Analista de qualidade / tenant |

O analista de qualidade só edita o segundo YAML. O SkillFlow de orquestração
não muda ao alterar critérios de avaliação de um pool.

---

## Tópicos Kafka

| Tópico | Produzido por | Consumido por |
|---|---|---|
| `evaluation.events` | Rules Engine (sampling) | Routing Engine |
| `evaluation.results` | `evaluation_publish` (mcp-server-plughub) | ClickHouse consumer |

---

## Evento: `evaluation.requested`

Publicado em `evaluation.events` pelo Rules Engine após a decisão de amostragem.
Carrega tudo que o agente de avaliação precisa para operar de forma autônoma.

```json
{
  "evaluation_id": "uuid-v4",
  "triggered_by": "contact_closed | manual | supervisor | batch",
  "triggered_at": "2026-04-06T14:00:00Z",

  "contact": {
    "contact_id": "uuid",
    "started_at": "2026-04-06T13:45:00Z",
    "ended_at": "2026-04-06T14:00:00Z",
    "outcome": "resolved | escalated | abandoned",
    "channel": "webchat"
  },

  "agent": {
    "agent_id": "uuid",
    "agent_session_id": "uuid",
    "agent_type": "human | ai",
    "pool_id": "retencao_humano"
  },

  "skill_id": "eval_retencao_humano_v1",

  "context_package": {
    "intent": "portability_check",
    "confidence": 0.87,
    "sentiment_score": -0.35,
    "sentiment_trajectory": [-0.10, -0.20, -0.35],
    "flags": ["churn_signal"],
    "turn_count": 12,
    "sla_elapsed_ms": 900000,
    "sla_target_ms": 960000,
    "issue_status": "portability_requested",
    "handoff_reason": null,
    "historical_insights": [],
    "conversation_insights": []
  },

  "transcript_id": "uuid"
}
```

### Campos obrigatórios

| Campo | Regra |
|---|---|
| `evaluation_id` | UUID gerado pelo Rules Engine — idempotência no reprocessamento |
| `agent.agent_type` | Determina quais itens `applies_to` são incluídos |
| `agent.pool_id` | Determina qual evaluation skill carregar |
| `skill_id` | Versão explícita — sem resolução automática de "latest" em avaliações |
| `context_package` | Snapshot no momento do `contact_closed` — imutável |
| `transcript_id` | Referência ao transcript persistido pelo Conversation Writer |

---

## Amostragem — decisão no Rules Engine

Nem todo `contact_closed` gera uma avaliação. O Rules Engine mantém contadores
por sessão de agente no Redis e decide se um contato deve ser avaliado com base
na taxa configurada por pool.

### Contadores Redis

```
eval:sampling:{tenant_id}:{agent_session_id}   Hash
  contacts_handled:   N   ← incrementado a cada contact_closed
  contacts_evaluated: M   ← incrementado quando avaliação é gerada
```

Criados no `agent_login`. Persistem até o fim da sessão (TTL de segurança: 48h).

### Algoritmo de cota

```
deve_avaliar = floor(contacts_handled × sampling_rate) > contacts_evaluated
```

O algoritmo de cota garante convergência previsível: ao longo da sessão, o
percentual de atendimentos avaliados converge para `sampling_rate` exatamente,
sem desvios acumulados. Uma taxa de 0.30 com 10 atendimentos gera exatamente
3 avaliações, nas posições determinísticas 4, 7 e 10 (floor(4×0.3)=1>0, etc).

### Configuração por pool

A taxa de amostragem é uma propriedade da `PoolConfig`, declarada pelo tenant
no Agent Registry e propagada ao Rules Engine via Kafka:

```yaml
pool_id: retencao_humano
evaluation:
  sampling_rate: 0.30   # 30% dos atendimentos
  skill_id_template: "eval_{pool_id}_v1"
```

### Trigger manual

Para reprocessamento ou avaliação pontual sem depender do fluxo normal:

```bash
plughub-sdk evaluate --contact-id <uuid> --skill-id eval_retencao_humano_v1
```

Publica diretamente em `evaluation.events` com `triggered_by: "manual"`.
Não incrementa contadores de amostragem.

---

## Execução — SkillFlow do agente de avaliação

O Routing Engine consome `evaluation.requested`, mapeia os campos para
`sessionContext` e chama `SkillFlowEngine.run()` com `session_id = evaluation_id`:

```python
engine.run(
    tenantId       = event.tenant_id,
    sessionId      = event.evaluation_id,   # sessionId sintético
    customerId     = event.contact.contact_id,
    skillId        = "agente_avaliacao_v1",
    flow           = load_flow("agente_avaliacao_v1"),
    sessionContext = {
        "evaluation_id":   event.evaluation_id,
        "skill_id":        event.skill_id,
        "transcript_id":   event.transcript_id,
        "context_package": event.context_package,
        "agent":           event.agent,
        "contact":         event.contact,
        "triggered_by":    event.triggered_by,
    }
)
```

### SkillFlow: `agente_avaliacao_v1`

Arquivo: `packages/skill-flow-engine/skills/agente_avaliacao_v1.yaml`

```
get_transcript
    ↓
resolve_context
    ↓
init_agent_loop ◄──────────────────────────────────────────────┐
    ↓                                                           │
check_agent_pending ──(has_next = false)──▶ evaluate           │
    ↓ (has_next = true)                          ↓             │
gather_agent_context                       publish_result       │
    ↓                                            ↓             │
advance_agent_loop ──────────────────────────────────────────── ┘
  (pop próximo, acumula resultado, volta ao check)
```

O loop executa um agente especialista por iteração. Ao esgotar a fila
(`has_next = false`), o flow avança para `evaluate` com o acumulado de
todos os agentes já executados em `agent_context_next.accumulated`.

```yaml
entry: get_transcript

steps:

  - id: get_transcript
    type: invoke
    tool: transcript_get
    input:
      transcript_id: "$.session.transcript_id"
    output_as: transcript
    on_success: resolve_context
    on_failure: resolve_context

  - id: resolve_context
    type: invoke
    tool: evaluation_context_resolve
    input:
      skill_id:        "$.session.skill_id"
      context_package: "$.session.context_package"
      template_vars:
        evaluation_id: "$.session.evaluation_id"
        agent:         "$.session.agent"
        contact:       "$.session.contact"
        context:       "$.session.context_package"
    output_as: evaluation_context
    on_success: init_agent_loop
    on_failure: init_agent_loop

  - id: init_agent_loop
    type: invoke
    tool: evaluation_agent_context_next
    input:
      queue:       "$.pipeline_state.evaluation_context.agent_context_queue"
      accumulated: {}
    output_as: agent_context_next
    on_success: check_agent_pending
    on_failure: evaluate

  - id: check_agent_pending
    type: choice
    conditions:
      - field:    "$.pipeline_state.agent_context_next.has_next"
        operator: eq
        value:    true
        next:     gather_agent_context
    default: evaluate

  - id: gather_agent_context
    type: task
    target:
      skill_id: "$.pipeline_state.agent_context_next.current_skill_id"
    execution_mode: sync
    on_success: advance_agent_loop
    on_failure: advance_agent_loop

  - id: advance_agent_loop
    type: invoke
    tool: evaluation_agent_context_next
    input:
      queue:              "$.pipeline_state.agent_context_next.remaining"
      task_result:        "$.pipeline_state.gather_agent_context"
      current_output_key: "$.pipeline_state.agent_context_next.current_output_key"
      accumulated:        "$.pipeline_state.agent_context_next.accumulated"
    output_as: agent_context_next
    on_success: check_agent_pending
    on_failure: evaluate

  - id: evaluate
    type: reason
    prompt_id: evaluation_rubric_v1
    input:
      transcript:       "$.pipeline_state.transcript"
      external_context: "$.pipeline_state.evaluation_context.external_context"
      agent_context:    "$.pipeline_state.agent_context_next.accumulated"
      context_package:  "$.session.context_package"
      agent_type:       "$.session.agent.agent_type"
    output_schema:
      items:
        type: array
        required: true
      overall_observation:
        type: string
        required: false
    output_as: llm_evaluation
    on_success: publish_result
    on_failure: complete_error

  - id: publish_result
    type: invoke
    tool: evaluation_publish
    input:
      evaluation_id:       "$.session.evaluation_id"
      tenant_id:           "$.session.tenant_id"
      skill_id:            "$.session.skill_id"
      agent_id:            "$.session.agent.agent_id"
      agent_type:          "$.session.agent.agent_type"
      pool_id:             "$.session.agent.pool_id"
      contact_id:          "$.session.contact.contact_id"
      triggered_by:        "$.session.triggered_by"
      llm_items:           "$.pipeline_state.llm_evaluation.items"
      overall_observation: "$.pipeline_state.llm_evaluation.overall_observation"
      context_package:     "$.session.context_package"
    output_as: publish_result
    on_success: complete_ok
    on_failure: complete_error

  - id: complete_ok
    type: complete
    outcome: resolved

  - id: complete_error
    type: complete
    outcome: failed
```

Os steps `gather_agent_context` e `advance_agent_loop` usam JSONPath em campos
resolvidos em runtime a partir do `pipeline_state`. O SkillFlowEngine suporta
ciclos nativamente — `advance_agent_loop` aponta de volta para `check_agent_pending`
sem qualquer mecanismo especial. Quando nenhuma seção declara `requires_agent`,
a fila retornada por `evaluation_context_resolve` é vazia, `init_agent_loop`
retorna `has_next = false` e o `choice` avança diretamente para `evaluate`.

---

## Tool: `evaluation_context_resolve`

Ferramenta do `mcp-server-plughub` responsável por duas funções:

1. **Resolve `requires_context`** — para cada seção ativa, chama as tools declaradas via proxy sidecar (localhost:7422) e acumula os resultados em `external_context`.
2. **Monta a fila de agentes** — entre todas as seções ativas, coleta todas que declaram `requires_agent` e retorna `agent_context_queue` — um array com todos os agentes especialistas a executar, em ordem de declaração. O array pode ser vazio (sem delegação), ter um elemento ou ter muitos.

A lógica de quais tools/agentes chamar reside inteiramente no YAML da evaluation
skill — o agente de avaliação não conhece CRM, knowledge base ou qualquer sistema
específico do tenant.

**Input:**

```typescript
{
  skill_id:        string              // evaluation skill a carregar do Skill Registry
  context_package: Record<string,any>  // filtra seções via applies_when
  template_vars: {                     // variáveis para resolução de templates {{ key }}
    evaluation_id: string
    agent:         AgentFields
    contact:       ContactFields
    context:       ContextPackage
  }
}
```

**Output:**

```typescript
{
  external_context:    Record<string, any>                  // resultados de requires_context
  agent_context_queue: Array<{ skill_id: string; output_key: string }>
  //  fila de agentes especialistas a executar, em ordem de declaração no formulário
  //  (vazia se nenhuma seção ativa declara requires_agent)
}
```

**Comportamento em falha:** falhas individuais de tool são logadas e o `output_key`
correspondente é omitido. A avaliação prossegue com os dados disponíveis.

---

## Tool: `evaluation_agent_context_next`

Gerencia a fila de agentes especialistas durante o loop de coleta de contexto.
É chamada duas vezes por agente: uma para inicializar (`init_agent_loop`) e uma
para avançar após cada execução (`advance_agent_loop`).

**Input:**

```typescript
{
  queue:              Array<{ skill_id: string; output_key: string }>
  // fila atual (remaining da iteração anterior ou agent_context_queue na primeira chamada)
  task_result?:        unknown
  // resultado do último step task (ausente na primeira chamada — init_agent_loop)
  current_output_key?: string
  // output_key do agente que acabou de executar
  accumulated?:        Record<string, any>
  // acumulador com resultados de todos os agentes já executados
}
```

**Output:**

```typescript
{
  has_next:           boolean              // true → próximo agente disponível
  current_skill_id:   string              // skill_id a despachar (ou "" se has_next=false)
  current_output_key: string              // onde armazenar o resultado no acumulador
  remaining:          Array<{ skill_id: string; output_key: string }>
  // fila após desempilhar o próximo — passada ao invoke da próxima iteração
  accumulated:        Record<string, any>
  // resultado mesclado de todos os agentes executados até agora
  // disponível para o step evaluate como agent_context
}
```

**Comportamento:** falhas na tool durante o loop causam `on_failure: evaluate` no
step correspondente, avançando para avaliação com o acumulador parcial.

### Templates disponíveis nos inputs de requires_context

| Placeholder | Valor |
|---|---|
| `{{ agent.pool_id }}` | `agent.pool_id` do evento |
| `{{ agent.agent_id }}` | `agent.agent_id` |
| `{{ agent.agent_type }}` | `"human"` ou `"ai"` |
| `{{ contact.contact_id }}` | UUID do contato |
| `{{ contact.channel }}` | `"webchat"` etc. |
| `{{ context.intent }}` | intent detectada |
| `{{ context.flags }}` | lista de flags |
| `{{ evaluation_id }}` | UUID da avaliação |
| `{{ <output_key> }}` | output de requires_context anterior (seções em sequência) |

---

## Padrões para avaliação com dados externos

Por padrão, o agente de avaliação usa apenas transcript e `context_package`.
Quando uma seção do formulário exige dados de sistemas externos, existem três
padrões de injeção:

### Padrão 1 — `requires_context` na evaluation skill (recomendado)

Declaração explícita na seção do formulário. Processada pelo step
`resolve_context` via `evaluation_context_resolve` antes de invocar o AI Gateway.

```yaml
sections:
  - id: oferta_comercial
    requires_context:
      - tool: crm_get_active_discounts
        input:
          pool_id: "{{ agent.pool_id }}"
        output_key: available_discounts
      - tool: crm_get_customer_plan
        input:
          contact_id: "{{ contact.contact_id }}"
        output_key: customer_plan
    subsections:
      - items:
          - id: desconto_correto
            instruction: >
              O agente ofereceu um desconto dentre os vigentes: {{ available_discounts }}.
              Plano do cliente: {{ customer_plan }}.
```

**Quando usar:** dados pontuais e bem definidos (descontos, status de conta).
Auditável e reproduzível — cada avaliação registra exatamente quais dados foram usados.

---

### Padrão 2 — `requires_agent` na evaluation skill

Para contextos que exigem raciocínio complexo ou múltiplas chamadas encadeadas
que não se expressam bem como tool calls simples. O analista declara um agente
especialista por seção — mais de uma seção pode declarar agentes distintos e
todos serão executados em sequência.

```yaml
sections:
  - id: gestao_churn
    applies_when:
      flags_include: churn_signal
    requires_agent:
      skill_id:   skill_churn_context_v1   # agente especialista para churn
      output_key: churn_analysis
    subsections:
      - items:
          - id: identificou_motivador_real
            instruction: >
              Considere o contexto retornado pelo agente especialista
              (churn_analysis) junto com o transcript para avaliar se
              o atendente chegou ao motivador real.

  - id: compliance_regulatorio
    applies_when:
      intent: portability_check
    requires_agent:
      skill_id:   skill_compliance_telco_v2   # agente especialista diferente
      output_key: compliance_analysis
    subsections:
      - items:
          - id: informou_prazo_legal
            instruction: >
              Verifique via compliance_analysis se o agente informou
              corretamente o prazo legal de portabilidade.
```

`evaluation_context_resolve` coleta **todos** os `requires_agent` das seções
ativas e retorna `agent_context_queue`. O SkillFlow executa um agente por
iteração do loop `init_agent_loop → check_agent_pending → gather_agent_context
→ advance_agent_loop → check_agent_pending`. O resultado de cada agente é
acumulado em `agent_context_next.accumulated` com sua respectiva `output_key`,
disponível como `agent_context` no step `evaluate`.

**Quando usar:** contexto que requer raciocínio, busca multi-step, ou chamadas
a sistemas que não expõem MCP tools simples. Suporta múltiplos agentes
distintos por formulário — cada seção pode delegar a um especialista diferente.

---

### Padrão 3 — `invoke` explícito no SkillFlow por pool

Para pools que precisam de steps adicionais específicos, cria-se uma variante
do SkillFlow com `invoke` steps extras antes do `reason`:

```yaml
# agente_avaliacao_retencao_v1 (variante por pool)
steps:
  - id: fetch_portfolio
    type: invoke
    target:
      mcp_server: mcp-server-crm
      tool: get_active_retention_offers
    input:
      pool_id: "$.session.agent.pool_id"
    output_as: retention_portfolio
    on_success: fetch_transcript
    ...
```

**Quando usar:** dados cujo schema é conhecido e fixo, mas que não cabem no
padrão `requires_context` por exigirem transformação ou múltiplas chamadas encadeadas,
e o pool justifica uma variante dedicada do SkillFlow.

---

### Padrão 4 — RAG para documentos grandes

Para aderência a scripts, regulamentações ou playbooks extensos, o step
`resolve_context` ou um `invoke` dedicado chama uma ferramenta de busca
semântica e injeta apenas os trechos relevantes:

```yaml
sections:
  - id: aderencia_script
    requires_context:
      - tool: knowledge_base_search
        input:
          query: "script de escalada para cancelamento voluntário"
          top_k: 3
        output_key: script_trechos
```

**Quando usar:** documentos grandes demais para incluir integralmente (manuais,
regulamentações). Evita custo e latência de prompts com dezenas de milhares de tokens.

---

### Comparativo dos quatro padrões

| | requires_context | requires_agent | invoke explícito | RAG |
|---|---|---|---|---|
| **Controle** | Total | Total | Total | Total |
| **Reprodutibilidade** | Alta | Alta | Alta | Alta |
| **Configuração** | YAML de skill (analista) | YAML de skill (analista) | SkillFlow (engenharia) | YAML de skill (analista) |
| **Execução** | Síncrona via proxy sidecar | A2A assíncrona (sync poll) | Síncrona | Síncrona via proxy sidecar |
| **Melhor para** | Dados pontuais por seção | Raciocínio complexo ou multi-step | Fetches transversais ao flow | Documentos grandes |

> **Nota sobre limites de contexto:** uma conversa longa de 20 minutos com
> formulário completo fica em torno de 6.000–8.000 tokens — folga de 25× antes
> do limite de 200K tokens. Custo e latência por chamada são as métricas
> relevantes em volume, não tamanho do contexto.

---

## output_schema solicitado ao AI Gateway

O step `reason` passa este schema como `output_schema`. O LLM preenche apenas
valores e justificativas — nunca calcula scores.

```json
{
  "items": [
    {
      "item_id": "string",
      "section_id": "string",
      "subsection_id": "string",
      "value": "number (0–10)",
      "justification": "string (1–3 frases referenciando o transcript)"
    }
  ],
  "overall_observation": "string (observação geral opcional, máx 5 frases)"
}
```

O cálculo de scores (média ponderada bottom-up) é feito deterministicamente
pela tool `evaluation_publish` antes de montar o evento `evaluation.completed`.

---

## Evento: `evaluation.completed`

Publicado em `evaluation.results` pela tool `evaluation_publish` após o cálculo
determinístico dos scores. Uma linha por seção avaliada.

```json
{
  "evaluation_id": "uuid",
  "contact_id": "uuid",
  "agent_id": "uuid",
  "agent_type": "human | ai",
  "pool_id": "retencao_humano",
  "skill_id": "eval_retencao_humano_v1",
  "evaluated_at": "2026-04-06T14:00:45Z",
  "triggered_by": "contact_closed",

  "scores": [
    {
      "section_id": "mandatory",
      "score_type": "base_score",
      "score": 8.4,
      "subsections": [
        {
          "subsection_id": "postura_atendimento",
          "score": 8.7,
          "items": [
            {
              "item_id": "escuta_ativa",
              "value": 9,
              "weight": 3,
              "justification": "O agente realizou sondagem estruturada em dois momentos..."
            }
          ]
        }
      ]
    },
    {
      "section_id": "gestao_churn",
      "score_type": "context_score",
      "triggered_by": { "flags_include": "churn_signal" },
      "score": 6.1,
      "subsections": []
    }
  ],

  "overall_observation": "Atendimento com postura sólida...",
  "items_excluded": [
    {
      "item_id": "empatia",
      "reason": "applies_to: human — agente avaliado é IA"
    }
  ]
}
```

---

## ClickHouse — modelo de dados

O consumer de `evaluation.results` persiste em duas tabelas.

### `evaluation_scores`

```sql
CREATE TABLE evaluation_scores (
  evaluation_id     UUID,
  contact_id        UUID,
  agent_id          UUID,
  agent_type        Enum('human', 'ai'),
  pool_id           String,
  skill_id          String,
  section_id        String,
  score_type        Enum('base_score', 'context_score'),
  score             Float32,
  triggered_by_key  Nullable(String),
  triggered_by_val  Nullable(String),
  evaluated_at      DateTime,
  triggered_by_src  String
) ENGINE = MergeTree()
  PARTITION BY toYYYYMM(evaluated_at)
  ORDER BY (pool_id, agent_id, section_id, evaluated_at);
```

### `evaluation_items`

```sql
CREATE TABLE evaluation_items (
  evaluation_id   UUID,
  contact_id      UUID,
  agent_id        UUID,
  pool_id         String,
  section_id      String,
  subsection_id   String,
  item_id         String,
  value           UInt8,
  weight          UInt8,
  justification   String,
  evaluated_at    DateTime
) ENGINE = MergeTree()
  PARTITION BY toYYYYMM(evaluated_at)
  ORDER BY (pool_id, agent_id, section_id, item_id, evaluated_at);
```

---

## Queries de referência para o dashboard

### base_score médio por agente no pool (últimos 30 dias)
```sql
SELECT
  agent_id,
  round(avg(score), 2) AS base_score_avg,
  count()              AS n_evaluations
FROM evaluation_scores
WHERE pool_id = 'retencao_humano'
  AND section_id = 'mandatory'
  AND evaluated_at >= now() - INTERVAL 30 DAY
GROUP BY agent_id
HAVING n_evaluations >= 5
ORDER BY base_score_avg DESC;
```

### context_scores por agente — apenas seções com n >= 5
```sql
SELECT
  agent_id,
  section_id,
  round(avg(score), 2) AS context_score_avg,
  count()              AS n_evaluations
FROM evaluation_scores
WHERE pool_id = 'retencao_humano'
  AND score_type = 'context_score'
  AND evaluated_at >= now() - INTERVAL 30 DAY
GROUP BY agent_id, section_id
HAVING n_evaluations >= 5
ORDER BY agent_id, section_id;
```

### drill-down: itens de um agente numa seção
```sql
SELECT
  item_id,
  round(avg(value), 2) AS avg_value,
  count()              AS n,
  any(justification)   AS exemplo_justificativa
FROM evaluation_items
WHERE pool_id    = 'retencao_humano'
  AND agent_id   = '<uuid>'
  AND section_id = 'gestao_churn'
  AND evaluated_at >= now() - INTERVAL 30 DAY
GROUP BY item_id
ORDER BY avg_value ASC;
```

---

## Relações com outros módulos

| Módulo | Relação |
|---|---|
| `Rules Engine` | Decide amostragem e publica `evaluation.requested` em `evaluation.events` |
| `Routing Engine` | Consome `evaluation.requested`, chama `SkillFlowEngine.run()` com o flow do agente |
| `SkillFlowEngine` | Executa o flow `agente_avaliacao_v1` — loop, retomada, persistência de estado |
| `mcp-server-plughub` | Fornece tools `transcript_get`, `evaluation_context_resolve` e `evaluation_publish` |
| `Conversation Writer` | Produz o transcript buscado via `transcript_id` do PostgreSQL |
| `Skill Registry` | Fonte da evaluation skill carregada por `skill_id` |
| `AI Gateway` | Invocado via step `reason` para preenchimento dos itens |
| `MCP proxy sidecar` | Intermediário para chamadas `requires_context` — valida permissões e audita |
| `Domain MCP Servers` | Destino final das chamadas requires_context (CRM, knowledge base, etc.) |
| `ClickHouse consumer` | Persiste `evaluation.completed` nas tabelas de scores e itens |
| `Agent Registry` | Fonte da `sampling_rate` e `skill_id_template` por pool |
