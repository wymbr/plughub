# Módulo: rules-engine (@plughub/rules-engine)

> Pacote: `rules-engine` (serviço)
> Runtime: Python 3.11+, FastAPI
> Spec de referência: seções 3.2, 3.2b

## O que é

O `rules-engine` é o monitor contínuo de conversas em andamento. Ele observa parâmetros de sessão a cada turno e decide se uma conversa deve ser escalada para outro pool de atendimento — sem LLM, sem estado próprio, apenas avaliação declarativa de regras configuradas pelo tenant.

É **stateless**: não guarda estado entre avaliações. Todo o estado das sessões monitoradas vive externamente (Redis / ClickHouse). O Rules Engine recebe o contexto do chamador e aplica regras.

---

## Invariante central

> O Rules Engine **nunca** toma decisões com LLM. Toda lógica é puramente declarativa — expressões sobre parâmetros observáveis. Complexidade de negócio fica nas regras, não no código.

---

## Estrutura do Pacote

```
rules-engine/src/plughub_rules/
  main.py            ← FastAPI + endpoints de gestão de regras e dry-run
  evaluator.py       ← RuleEvaluator — evaluate(rule, context)
  escalator.py       ← Escalator — trigger() → chama mcp-server ou só loga (shadow)
  lifecycle.py       ← validate_transition() — máquina de estados de regra
  dry_run.py         ← DryRunEngine — 4 mecanismos de sandbox
  models.py          ← Pydantic: Rule, Condition, EvaluationContext, EvaluationResult, etc.
  config.py          ← settings via variáveis de ambiente
  kafka_publisher.py ← publica eventos de escalonamento e shadow no Kafka
```

---

## Parâmetros Observáveis

Cada `Condition` monitora um destes parâmetros:

| Parâmetro | Tipo | Descrição |
|---|---|---|
| `sentiment_score` | `float [-1.0, 1.0]` | Score de sentimento do turno atual (ou média móvel com `window_turns`) |
| `intent_confidence` | `float [0.0, 1.0]` | Confiança da intenção classificada pelo AI Gateway |
| `turn_count` | `int` | Número de turnos da conversa |
| `elapsed_ms` | `int` | Tempo decorrido desde o início da conversa |
| `flag` | `str` | Flag booleana presente na lista `context.flags` (identificada por `flag_name`) |

### Média móvel de sentimento

Quando a condição define `window_turns`, o evaluador calcula a **média aritmética** dos últimos N valores de `sentiment_history`, não o valor instantâneo. Isso evita escalonamentos por picos isolados.

```python
# window_turns = 3, sentiment_history = [-0.3, -0.5, -0.4, -0.6]
# janela: [-0.5, -0.4, -0.6] → média = -0.5
```

---

## Operadores de Comparação

```
lt       < (menor que)
lte      ≤ (menor ou igual)
gt       > (maior que)
gte      ≥ (maior ou igual)
eq       = (igual — string ou número)
neq      ≠ (diferente)
contains str.contains (para parâmetros textuais)
```

---

## Lógica de Avaliação

Uma `Rule` tem uma lista de `Condition`s e um campo `logic`:

- `"AND"` — dispara somente se **todas** as condições são verdadeiras
- `"OR"` — dispara se **pelo menos uma** condição é verdadeira

```
rule = {
  logic: "AND",
  conditions: [
    { parameter: "sentiment_score", operator: "lt", value: -0.4, window_turns: 3 },
    { parameter: "turn_count",      operator: "gte", value: 5 }
  ],
  target_pool: "retencao_humano"
}
```

---

## Ciclo de Vida de uma Regra

As regras percorrem uma máquina de estados bem definida antes de entrar em produção:

```
draft → dry_run → shadow → active → disabled
          ↑         ↑         |
          └─────────┴─────────┘  (rollback possível)
```

**Transições válidas:**

| De | Para |
|---|---|
| `draft` | `dry_run`, `disabled` |
| `dry_run` | `shadow`, `draft`, `disabled` |
| `shadow` | `active`, `dry_run`, `disabled` |
| `active` | `shadow`, `disabled` |
| `disabled` | `draft` |

> **Importante:** Não é possível ir de `draft` ou `dry_run` diretamente para `active`. A tentativa lança `ValueError` com mensagem orientativa. A regra **obrigatoriamente** passa por `shadow` antes de ativar.

### Significado de cada estado

| Estado | Comportamento |
|---|---|
| `draft` | Existe mas nunca foi avaliada. Editável sem restrições. |
| `dry_run` | Rodada contra histórico do ClickHouse. Não afeta produção. |
| `shadow` | Avaliada em produção real. Se disparar: evento Kafka + log, mas **não** escalona. |
| `active` | Avaliada em produção. Se disparar com `target_pool`: aciona `conversation_escalate`. |
| `disabled` | Ignorada pelo avaliador. Pode ser reaberta como `draft`. |

---

## Fluxo de Escalonamento

```
1. AI Gateway ou Supervisor entrega EvaluationContext ao Rules Engine (por turno)
2. RuleEvaluator.evaluate(rule, context) — avalia condições, aplica logic AND/OR
3. Se triggered=True E rule.target_pool não é None:
   ├── shadow mode → publica EscalationTrigger no Kafka (shadow topic), não age
   └── active mode → POST /tools/conversation_escalate no mcp-server (timeout 5s)
                   → publica EscalationTrigger no Kafka (escalation topic)
4. Se triggered=True mas sem target_pool → log apenas, nenhuma ação
```

### Chamada ao mcp-server (modo active)

```
POST {mcp_server_url}/tools/conversation_escalate
Body: {
  session_id:  str,
  target_pool: str,
  reason:      "rule:{rule_id}",
  context:     EvaluationContext
}
Timeout: 5s
```

---

## Mecanismos de Sandbox (spec 3.2b)

O `DryRunEngine` oferece quatro ferramentas para testar regras com segurança antes da ativação:

### 1. `dry_run_historico`

Simula a regra contra conversas históricas carregadas do ClickHouse (janela configurável: 1–90 dias). Para cada sessão, avança turno a turno e registra se — e em qual turno — a regra dispararia.

Retorna: `total_conversations`, `would_trigger_count`, `trigger_rate`, `sample_triggers` (amostra de até 5 sessões).

### 2. Shadow Mode

Não é uma função do `DryRunEngine` em si — é o estado `shadow` da regra. A regra é avaliada em produção real mas registra apenas no Kafka o que **teria** feito, sem chamar `conversation_escalate`. Permite observar comportamento real antes de ativar.

### 3. `diff_regras`

Compara duas versões de uma regra (por exemplo, atual vs. proposta) contra o mesmo conjunto histórico. Retorna:

```python
{
  "only_rule_a":  int,   # conversas que disparariam A mas não B
  "only_rule_b":  int,   # conversas que disparariam B mas não A
  "both":         int,   # disparariam ambas
  "neither":      int,   # não disparariam nenhuma
  "rate_a":       float,
  "rate_b":       float,
}
```

### 4. `simulate_session`

Testa a regra com parâmetros fornecidos manualmente — sem necessidade de dados históricos. Útil para debug e testes unitários de novas regras.

```python
request = SessionSimulatorRequest(
  rule=             rule,
  sentiment_score=  -0.6,
  intent_confidence= 0.3,
  turn_count=       8,
  elapsed_ms=       45000,
  flags=            ["vip"]
)
# Retorna: triggered, condition_results, target_pool
```

---

## Modelos de Dados Principais

### `Rule`

```python
Rule {
  rule_id:     str
  tenant_id:   str
  name:        str
  status:      "draft" | "dry_run" | "shadow" | "active" | "disabled"
  conditions:  list[Condition]      # mínimo 1
  logic:       "AND" | "OR"         # default: "AND"
  target_pool: str | None           # None = nenhuma ação ao disparar
  priority:    int [1–10]           # default: 1
  created_at:  ISO datetime
  updated_at:  ISO datetime
}
```

### `Condition`

```python
Condition {
  parameter:    "sentiment_score" | "intent_confidence" | "turn_count" | "elapsed_ms" | "flag"
  operator:     "lt" | "lte" | "gt" | "gte" | "eq" | "neq" | "contains"
  value:        float | str
  window_turns: int | None    # média móvel de N turnos (só para sentiment_score)
  flag_name:    str | None    # nome da flag a verificar (só para parameter == "flag")
}
```

### `EvaluationContext`

```python
EvaluationContext {
  session_id:         str
  tenant_id:          str
  turn_count:         int   = 0
  elapsed_ms:         int   = 0
  sentiment_score:    float = 0.0   # range [-1.0, 1.0]
  intent_confidence:  float = 0.0   # range [0.0, 1.0]
  flags:              list[str] = []
  sentiment_history:  list[float] = []   # histórico para cálculo de média móvel
}
```

### `EvaluationResult`

```python
EvaluationResult {
  rule:              Rule
  triggered:         bool
  condition_results: list[ConditionResult]
  context:           EvaluationContext
  evaluated_at:      ISO datetime
}

ConditionResult {
  condition:      Condition
  matched:        bool
  observed_value: float | str | None
}
```

### `EscalationTrigger`

```python
EscalationTrigger {
  session_id:   str
  tenant_id:    str
  rule_id:      str
  rule_name:    str
  target_pool:  str
  shadow_mode:  bool
  triggered_at: ISO datetime
  context:      EvaluationContext
}
```

### `EscalationDecision`

```python
EscalationDecision {
  should_escalate: bool
  rule_id:         str | None
  pool_target:     str | None
  reason:          str | None
  mode:            "active" | "shadow" | None
}
```

---

## Tópicos Kafka

| Tópico | Direção | Evento | Quando |
|---|---|---|---|
| `conversations.events` | **Publica** | `escalation.triggered` | Regra active disparou e acionou mcp-server |
| `conversations.events` | **Publica** | `escalation.shadow` | Regra shadow disparou (observação sem ação) |
| `conversations.events` | **Consome** | `contact_closed` | Decisão de amostragem de avaliação |
| `agent.lifecycle` | **Consome** | `agent_login` | Inicializa contadores de amostragem no Redis |
| `agent.lifecycle` | **Consome** | `agent_done` | (futuro) arquivamento de contadores de sessão |
| `evaluation.events` | **Publica** | `evaluation.requested` | Contato selecionado pela amostragem |

---

## Amostragem de Avaliações

O Rules Engine é responsável por decidir se um `contact_closed` gera uma
avaliação, mantendo contadores por sessão de agente no Redis e publicando
`evaluation.requested` quando a cota de amostragem não foi atingida.

### Contadores Redis

```
eval:sampling:{tenant_id}:{agent_session_id}   Hash   TTL: 48h
  contacts_handled:   N   ← incrementado a cada contact_closed do agente
  contacts_evaluated: M   ← incrementado quando avaliação é gerada
```

Inicializados com valor 0 no `agent_login`. TTL de segurança de 48h garante
limpeza automática após logout mesmo que o evento de encerramento não chegue.

### Algoritmo de cota

```python
deve_avaliar = floor(contacts_handled * sampling_rate) > contacts_evaluated
```

O algoritmo de cota produz convergência determinística: ao final da sessão,
o número de avaliações geradas é exatamente `floor(total_contacts × rate)`.
Desvios aleatórios acumulados não ocorrem — diferente de um algoritmo probabilístico.

**Exemplo com `sampling_rate = 0.30` e 10 atendimentos:**

| Atendimento | handled | floor(N×0.3) | evaluated | Avalia? |
|---|---|---|---|---|
| 1 | 1 | 0 | 0 | Não |
| 2 | 2 | 0 | 0 | Não |
| 3 | 3 | 0 | 0 | Não |
| 4 | 4 | 1 | 0 | **Sim** → evaluated=1 |
| 5–6 | 5–6 | 1 | 1 | Não |
| 7 | 7 | 2 | 1 | **Sim** → evaluated=2 |
| 8–9 | 8–9 | 2 | 2 | Não |
| 10 | 10 | 3 | 2 | **Sim** → evaluated=3 |

### Fluxo de decisão

```
contact_closed (conversations.events)
  ↓
Rules Engine lê agent_session_id + pool_id do evento
  ↓
HINCRBY eval:sampling:{tenant_id}:{agent_session_id} contacts_handled 1
  ↓
Lê contacts_handled e contacts_evaluated do hash
  ↓
Se floor(contacts_handled × sampling_rate) > contacts_evaluated:
  ├── HINCRBY contacts_evaluated 1
  ├── Monta payload evaluation.requested com context_package snapshot
  └── Publica em evaluation.events
```

### Configuração por pool

A `sampling_rate` é declarada na `PoolConfig` no Agent Registry e propagada ao
Rules Engine via Kafka (mesmo mecanismo da `PoolConfig` do Routing Engine):

```yaml
pool_id: retencao_humano
evaluation:
  sampling_rate: 0.30          # 30% dos atendimentos
  skill_id_template: "eval_{pool_id}_v1"
```

O Rules Engine mantém uma cópia em cache local (Redis ou memória) dos parâmetros
de amostragem por pool, atualizada via kafka_listener.

### Montagem do payload `evaluation.requested`

O Rules Engine monta o payload completo do evento incluindo o `context_package`
snapshot no momento do `contact_closed`. Não faz consultas adicionais — todos
os campos necessários estão disponíveis no evento `contact_closed` ou no Redis
da sessão.

```python
{
    "evaluation_id":  str(uuid4()),
    "triggered_by":   "contact_closed",
    "triggered_at":   now_iso(),
    "contact":        event.contact,
    "agent": {
        "agent_id":         event.agent_id,
        "agent_session_id": event.agent_session_id,
        "agent_type":       event.agent_type,
        "pool_id":          event.pool_id,
    },
    "skill_id":        resolve_skill_id(pool_config, event.pool_id),
    "context_package": event.context_package,   # snapshot imutável
    "transcript_id":   event.transcript_id,
}
```

---

## Relação com Outros Módulos

```
rules-engine
  ├── recebe → EvaluationContext   (fornecido pelo AI Gateway / Supervisor por turno)
  ├── consome → Kafka              (conversations.events:contact_closed, agent.lifecycle:agent_login)
  ├── aciona → mcp-server-plughub  (conversation_escalate — modo active, HTTP POST)
  ├── publica → Kafka              (escalation events + shadow events + evaluation.requested)
  ├── lê/escreve → Redis           (contadores de amostragem por agent_session_id)
  ├── lê → ClickHouse              (histórico de sessões para dry_run_historico)
  └── referencia → routing-engine  (target_pool é um pool_id gerenciado pelo Routing Engine)
```

> **Nota sobre Redis no Rules Engine:** o acesso ao Redis é restrito à
> funcionalidade de amostragem (contadores por sessão). O `EvaluationContext`
> para avaliação de regras de escalonamento continua sendo fornecido pelo
> chamador a cada invocação — esse caminho não acessa Redis diretamente.
