# Módulo: rules-engine (@plughub/rules-engine)

> Última atualização: 2026-05-25 · Estado: Arc 16

> Pacote: `rules-engine` (serviço)
> Runtime: Python 3.11+, FastAPI
> Spec de referência: seções 3.2, 3.2b

## O que é

O `rules-engine` é o avaliador de eventos pós-roteamento. Consome eventos de ciclo de roteamento e de encerramento de atendimento e decide, por avaliação declarativa de regras configuradas pelo tenant, se uma conversa deve ser escalada para outro pool — sem LLM, sem estado próprio.

É **stateless**: não mantém estado entre eventos. Cada evento é avaliado isoladamente; não há polling de Redis nem monitoramento de sentimento turno a turno. Todo o estado de sessão necessário chega no payload do evento.

**Escopo (CLAUDE.md — Rules Engine — Scope):** consome `conversations.routed`, `conversations.queued`, `conversations.abandoned` e `agent.done`; publica em `rules.escalation.events` e `rules.shadow.events`. O Rules Engine **não** monitora Redis, **não** avalia sentimento, **não** toma decisões de roteamento e **não** mantém estado entre eventos.

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
1. Evento pós-roteamento chega (conversations.routed/queued/abandoned, agent.done)
   → o EvaluationContext é montado a partir do payload do evento
2. RuleEvaluator.evaluate(rule, context) — avalia condições, aplica logic AND/OR
3. Se triggered=True E rule.target_pool não é None:
   ├── shadow mode → publica EscalationTrigger em rules.shadow.events, não age
   └── active mode → POST /tools/conversation_escalate no mcp-server (timeout 5s)
                   → publica EscalationTrigger em rules.escalation.events
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

| Tópico | Direção | Descrição |
|---|---|---|
| `conversations.routed` | **Consome** | Conversa roteada — aciona avaliação de regras |
| `conversations.queued` | **Consome** | Conversa enfileirada — aciona avaliação de regras |
| `conversations.abandoned` | **Consome** | Conversa abandonada — aciona avaliação de regras |
| `agent.done` | **Consome** | Encerramento de atendimento — aciona avaliação de regras |
| `rules.escalation.events` | **Publica** | Regra `active` disparou e acionou `conversation_escalate` |
| `rules.shadow.events` | **Publica** | Regra `shadow` disparou (observação sem ação) |

> O Rules Engine publica em tópicos dedicados (`rules.escalation.events` e `rules.shadow.events`), **não** em `conversations.events`. As escalações são consumidas pelo Routing Engine; os eventos shadow vão para Analytics.

---

## Amostragem de Avaliações — fora do escopo do Rules Engine

> A criação de instâncias de avaliação **não** é responsabilidade do Rules Engine.
> O *sampling engine* da `evaluation-api` (Arc 6) consome `session_closed` e
> decide, segundo as regras de campanha, se gera uma instância de avaliação. O
> Rules Engine não mantém contadores de amostragem, não consome `contact_closed`
> para esse fim e não publica `evaluation.requested`.

---

## Relação com Outros Módulos

```
rules-engine
  ├── consome → Kafka              (conversations.routed/queued/abandoned, agent.done)
  ├── aciona → mcp-server-plughub  (conversation_escalate — modo active, HTTP POST)
  ├── publica → Kafka              (rules.escalation.events, rules.shadow.events)
  ├── lê → ClickHouse              (histórico de sessões para dry_run_historico)
  └── referencia → routing-engine  (target_pool é um pool_id gerenciado pelo Routing Engine)
```

> **Nota sobre estado no Rules Engine:** o Rules Engine não mantém estado entre
> eventos. Cada evento de roteamento ou de `agent.done` carrega no payload todo
> o contexto necessário para a avaliação das regras. O acesso ao ClickHouse é
> restrito ao `dry_run_historico` (sandbox), nunca ao caminho de produção.
