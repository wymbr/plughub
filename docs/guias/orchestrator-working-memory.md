# Orquestrador Nativo — Working Memory via ContextStore

> Última atualização: 2026-05-25 · Estado: Arc 16

> **Contexto:** Este guia descreve o padrão recomendado para acumulação de contexto
> em orquestradores nativos PlugHub que operam em loop (captura → LLM → delegar
> especialista → repetir). Complementa [`context-store.md`](context-store.md) e
> [`pool-hooks.md`](pool-hooks.md).

---

## O Problema: Loop sem Memória de Trabalho

Um orquestrador nativo básico opera em ciclos:

```
loop:
  1. menu          — captura input do cliente
  2. reason        — LLM interpreta a demanda e decide qual especialista acionar
  3. task/invoke   — delega ao especialista
  4. (especialista conclui)
  5. reason        — LLM avalia o resultado e decide continuar, escalar ou encerrar
  goto loop
```

O problema surge a partir da segunda iteração: **cada chamada ao LLM começa sem
memória das iterações anteriores**. O LLM não sabe o que já foi resolvido, qual é
o estado atual da demanda, nem o que os especialistas anteriores produziram.

### Por que não basta passar o histórico completo de mensagens?

Funciona para conversas curtas, mas escala mal:

| Abordagem | Custo de tokens | Consistência | Estrutura |
|---|---|---|---|
| Histórico bruto completo | Cresce linearmente | Re-extrai a cada chamada | Nenhuma |
| ContextStore estruturado | Bounded (resumo progressivo) | Escrito uma vez, lido N vezes | Tipada + confidence |

O histórico completo (`session:{id}:stream`) continua disponível para auditoria e
Session Replayer — mas não precisa ir para o LLM a cada iteração.

---

## A Solução: ContextStore como Working Memory

O padrão usa o ContextStore (`{tenantId}:ctx:{sessionId}`) como memória de trabalho
estruturada do orquestrador. Após cada especialista completar, um `reason` step lê
o resultado + o estado acumulado, produz um objeto estruturado, e escreve de volta
via `context_tags.outputs`. Na próxima iteração, o LLM recebe estado comprimido
em vez de histórico bruto.

```
Iteração 1:
  menu → reason(demanda) → task(especialista_A) → reason(update_state) → ContextStore

Iteração 2:
  menu → reason(@ctx.session.orchestrator_summary + nova_mensagem) → task(especialista_B)
       → reason(update_state) → ContextStore

Iteração N:
  (LLM sempre recebe o resumo progressivo da última iteração — nunca toda a histórico)
```

---

## Tags de Working Memory Recomendadas

Namespace `session.*` no ContextStore — TTL igual ao da sessão, expira ao fechar.

| Tag | Tipo | merge_strategy | Descrição |
|---|---|---|---|
| `session.customer_demand` | string | `overwrite` | Demanda original do cliente (escrita na primeira iteração, raramente atualizada) |
| `session.current_need` | string | `overwrite` | Necessidade remanescente após as tarefas concluídas até aqui |
| `session.completed_tasks` | string | `append` | Lista acumulada de tarefas resolvidas (uma entrada por iteração) |
| `session.orchestrator_summary` | string | `overwrite` | Resumo comprimido de tudo até aqui — reescrito a cada iteração pelo LLM |
| `session.next_action` | string | `overwrite` | `continue` \| `escalate` \| `complete` — decisão da última iteração |

---

## Implementação YAML

### Step de atualização de estado (após especialista completar)

```yaml
- id: update_orchestrator_state
  type: reason
  prompt: |
    Você é o orquestrador deste atendimento. Atualize o estado de trabalho
    com base no resultado da tarefa que acabou de ser executada.

    Estado atual:
    - Demanda original do cliente: {{@ctx.session.customer_demand}}
    - Tarefas já concluídas: {{@ctx.session.completed_tasks}}
    - Necessidade remanescente até agora: {{@ctx.session.current_need}}

    Resultado desta tarefa:
    {{$.pipeline_state.specialist_result}}

    Produza o estado atualizado.
  output_schema:
    task_resolved:    string   # o que foi resolvido nesta iteração
    remaining_need:   string   # o que ainda falta resolver (vazio se nada)
    next_action:      string   # "continue" | "escalate" | "complete"
    updated_summary:  string   # resumo progressivo comprimido de toda a conversa até aqui
  context_tags:
    outputs:
      - tag: session.completed_tasks
        field: task_resolved
        confidence: 0.95
        merge_strategy: append
      - tag: session.current_need
        field: remaining_need
        confidence: 0.9
        merge_strategy: overwrite
      - tag: session.next_action
        field: next_action
        confidence: 1.0
        merge_strategy: overwrite
      - tag: session.orchestrator_summary
        field: updated_summary
        confidence: 0.9
        merge_strategy: overwrite
```

### Step de decisão do próximo especialista (início de cada iteração)

```yaml
- id: decide_next_action
  type: reason
  prompt: |
    Você é o orquestrador. Com base no estado atual e na última mensagem do
    cliente, decida qual especialista acionar ou se o atendimento deve ser
    encerrado.

    Estado do atendimento:
    {{@ctx.session.orchestrator_summary}}

    Necessidade atual do cliente:
    {{@ctx.session.current_need}}

    Última mensagem do cliente:
    {{$.pipeline_state.last_customer_message}}

    Qual é a próxima ação?
  output_schema:
    specialist_pool: string   # pool_id do especialista a acionar, ou vazio
    instruction:     string   # instrução para o especialista
    reasoning:       string   # justificativa da decisão
    should_complete: boolean  # true se o atendimento deve ser encerrado
```

### Captura da demanda original (primeira iteração)

```yaml
- id: capture_initial_demand
  type: reason
  prompt: |
    Extraia a demanda principal do cliente com base na primeira mensagem.
    {{$.pipeline_state.first_message}}
  output_schema:
    demand_summary: string
    urgency: string   # "low" | "medium" | "high"
  context_tags:
    outputs:
      - tag: session.customer_demand
        field: demand_summary
        confidence: 0.85
        merge_strategy: overwrite
```

---

## Fluxo Completo de uma Iteração

```
┌─────────────────────────────────────────────────────────┐
│                    ITERAÇÃO N                            │
│                                                         │
│  menu ──► captura input do cliente                      │
│    │                                                    │
│    ▼                                                    │
│  reason(decide_next_action)                             │
│    ├── lê @ctx.session.orchestrator_summary             │
│    ├── lê @ctx.session.current_need                     │
│    └── lê $.pipeline_state.last_customer_message        │
│    │                                                    │
│    ▼                                                    │
│  task / invoke ──► especialista executa                 │
│    │                                                    │
│    ▼                                                    │
│  reason(update_orchestrator_state)      ◄── NOVO        │
│    ├── lê resultado do especialista                     │
│    ├── lê estado acumulado do ContextStore              │
│    └── escreve estado atualizado no ContextStore        │
│    │                                                    │
│    ▼                                                    │
│  choice ──► continue? escalate? complete?               │
└─────────────────────────────────────────────────────────┘
```

---

## Namespaces Relevantes

O ContextStore já possui namespaces para contextos de vida útil diferente:

| Namespace | Escopo | Persistência | Uso no orquestrador |
|---|---|---|---|
| `session.*` | Sessão atual | Expira ao fechar | Working memory do loop (este guia) |
| `insight.conversa.*` | Sessão atual | Expira ao fechar | Análises derivadas da conversa (sentimento, intenção) |
| `insight.historico.*` | Cross-session | Persiste via Kafka/PG | Histórico longo do cliente (CRM, interações anteriores) |
| `caller.*` | Sessão atual | Expira ao fechar | Dados do cliente (CPF, nome, conta) |

Para orquestradores com demandas que transcendem sessões, combinar com **Arc 10
Journey**: o `journey_id` vincula sessões sucessivas, e `insight.historico.*`
carrega o contexto de longo prazo entre os contatos.

---

## Vantagens do Padrão

- **Token-efficient**: o LLM sempre recebe o estado mais recente comprimido,
  não o histórico crescente de mensagens
- **Consistency**: cada campo foi extraído e validado pelo LLM uma vez; iterações
  futuras consomem o dado estruturado, não re-extraem do texto bruto
- **Rastreabilidade**: cada escrita no ContextStore tem `confidence`, `source` e
  `updated_at` — auditável via `supervisor_state` e Session Replayer
- **Sem acoplamento ao stream**: o histórico completo (`session:{id}:stream`)
  continua disponível para auditoria; o orquestrador trabalha a partir da
  abstração estruturada
- **Compatível com hooks**: `session.orchestrator_summary` fica disponível para
  os agentes de wrap-up via `@ctx.session.orchestrator_summary` — o agente de
  wrap-up pode pré-preencher o resumo do atendimento sem pedir ao operador que
  re-descreva o que aconteceu

---

## Referências

- [`context-store.md`](context-store.md) — modelo completo do ContextStore, namespaces, confidence
- [`pool-hooks.md`](pool-hooks.md) — hooks `on_human_end` e acesso ao ContextStore antes dos hooks
- [`arc10-journey.md`](../arcos/arc10-journey.md) — propagação de contexto entre sessões (Journey)
- `CLAUDE.md § ContextStore` — invariantes e referências de implementação
