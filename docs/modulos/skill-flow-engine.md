# Módulo: skill-flow-engine (@plughub/skill-flow)

> Pacote: `skill-flow-engine` (serviço + biblioteca)
> Runtime: Node 20+, TypeScript
> Spec de referência: seções 4.7, 4.7m, 4.7n, 9.5i

## O que é

O `skill-flow-engine` é o interpretador de Skill Flows. Recebe o campo `flow` de uma skill de orquestração e executa o grafo de steps declarado, coordenando delegações A2A, chamadas MCP, inferências via AI Gateway, interações com o cliente e escalações.

O engine é **stateless** em execução: todo estado do pipeline vive no Redis (`pipeline_state`). Se o processo que roda o engine cair no meio de um step, uma nova instância retoma exatamente do ponto onde parou — sem perder dados nem re-executar steps já concluídos.

---

## Invariantes

- `pipeline_state` é persistido no Redis **antes** de executar o próximo step — não após
- Lock distribuído via Redis (`SET NX`) impede execução concorrente do mesmo pipeline
- O engine nunca reinicia um pipeline do `entry` se existir `pipeline_state` ativo com `status: "in_progress"` — sempre retoma do `current_step_id`
- Contadores de retry são persistidos a cada tentativa — um catch retomado sabe quantas já foram feitas
- `job_id` do `agent_delegate` é persistido antes do polling — re-execução não duplica delegação

---

## Estrutura do Pacote

```
skill-flow-engine/src/
  engine.ts      ← SkillFlowEngine — orquestra execução, gerencia loop e retomada
  executor.ts    ← executeStep() — dispatch por tipo de step
  state.ts       ← PipelineStateManager — leitura/escrita no Redis
  steps/
    task.ts      ← delegação A2A (agent_delegate + polling)
    choice.ts    ← ramificação condicional (JSONPath)
    catch.ts     ← retry e fallback
    escalate.ts  ← escalação para pool via Rules Engine
    complete.ts  ← encerramento do pipeline
    invoke.ts    ← chamada direta a tool MCP
    reason.ts    ← inferência via AI Gateway
    notify.ts    ← mensagem ao cliente via Notification Agent
    (menu — step type suspense, retomado externamente via MenuSubmitEvent)
```

> Nota: o step `menu` suspende o pipeline retornando `awaiting_selection`. A retomada acontece quando o Channel Gateway entrega um `MenuSubmitEvent` normalizado. Não há um `menu.ts` dedicado porque a suspensão é tratada pelo engine principal.

---

## PipelineState — Estrutura no Redis

O schema canônico de `PipelineState` é definido em `@plughub/schemas` e validado pelo Zod em toda leitura do Redis.

```typescript
PipelineState {
  flow_id:         string           // skill_id do flow em execução
  current_step_id: string           // step sendo executado ou a executar
  status:          "in_progress" | "completed" | "failed"
  started_at:      ISO datetime
  updated_at:      ISO datetime

  results:         Record<string, unknown>   // dados de negócio acumulados
  retry_counters:  Record<string, number>    // tentativas por step catch
  transitions:     Array<{
    from_step:  string
    to_step:    string
    reason:     "on_success" | "on_failure" | "on_timeout" | "manual"
    timestamp:  ISO datetime
  }>
  error_context?:  { step_id: string; error: string; timestamp: ISO datetime }
}
```

### Chaves Redis

```
{tenant_id}:pipeline:{session_id}              String (JSON) → PipelineState    TTL: 24h
{tenant_id}:pipeline:{session_id}:running      String → "1"                     TTL: 5min (lock)
{tenant_id}:pipeline:{session_id}:job:{step_id} String → job_id                 TTL: 24h
```

### O campo `results` — repositório de dados de negócio

`results` é o mapa livre onde cada step que produz saída grava seu resultado. Cresce ao longo do flow.

| Tipo de step | Campo de destino | Acessado como |
|---|---|---|
| `invoke` | `output_as: "cliente"` | `results.cliente` |
| `reason` | `output_as: "classif"` | `results.classif` |
| `menu` | `result: "contrato"` | `results.contrato` |
| `task` | automático (`step.id`) | `results.{step_id}` |

Steps posteriores acessam esses valores via JSONPath:

```
choice condition: "$.pipeline_state.results.classif.intencao"
invoke input:     "$.pipeline_state.results.cliente.historico"
notify message:   "Protocolo {{$.pipeline_state.results.protocolo}} registrado."
```

### Distinção entre campos de `PipelineState`

| Campo | Quem usa | Para quê |
|---|---|---|
| `results` | Engine, agente, steps `choice`/`invoke`/`reason`/`notify` | Dados de negócio — crescem a cada step concluído |
| `retry_counters` | Exclusivamente o engine | Controle de tentativas em steps `catch` |
| `transitions` | Rules Engine, observabilidade | Histórico de auditoria do flow |
| `error_context` | Engine, diagnóstico | Presente quando `status: "failed"` ou durante `catch` |

---

## Ciclo de Execução (engine.ts)

```
1. Tenta adquirir lock exclusivo (Redis SET NX)
   └── Se lock já existe: retorna { error: "PRECONDITION_FAILED", active_job_id }

2. Lê pipeline_state do Redis
   ├── Se status == "in_progress": retoma do current_step_id
   └── Se não existe: cria novo pipeline_state a partir do entry

3. Loop de execução:
   ┌── Busca step pelo current_step_id no mapa
   │   ├── Se não encontrado: marca failed + lança exceção
   │
   ├── Executa step via executeStep(step, ctx)
   │
   ├── Persiste output em results (se step produz output_as)
   │
   ├── Verifica next_step_id:
   │   ├── "__complete__"             → marca completed, retorna outcome
   │   ├── "__awaiting_task__"        → persiste estado, retorna "awaiting_task"
   │   ├── "__awaiting_escalation__"  → persiste estado, retorna "escalated_human"
   │   └── outro step_id             → adiciona transição, persiste, continua loop
   └─────────────────────────────────────────────────────

4. Libera lock (sempre, inclusive em exceções — bloco finally)
```

---

## Os 9 Tipos de Step

### `task` — Delegação A2A

Delega uma subtarefa a um agente que implementa a `skill_id` declarada. O Routing Engine aloca o agente; o orquestrador não sabe (nem precisa saber) qual instância foi alocada.

```typescript
// Schema
{
  id:              string
  type:            "task"
  target: {
    skill_id:      string    // capability que o agente delegado deve ter
  }
  execution_mode:  "sync" | "async"   // default: sync
  on_success:      string             // próximo step_id
  on_failure:      string
}
```

**Modo `sync` (fire-and-poll):**

```
1. Verifica job_id no Redis — se existe, pula o agent_delegate (idempotência)
2. Chama agent_delegate → recebe job_id imediatamente
3. Persiste job_id no Redis ANTES do polling
4. Loop de polling a cada 2s, máximo 150 tentativas (5 min):
   ├── status == "completed" → on_success ou on_failure conforme outcome
   ├── status == "failed"    → on_failure
   └── outro status          → continua polling
5. Timeout de polling → on_failure com { error: "poll_timeout" }
```

**Modo `async` (fire-and-return):**

```
1. Verifica job_id no Redis — se existe, checa status UMA vez
2. Se ainda em execução: persiste job_id em results[{step_id}:__job_id__]
   e retorna __awaiting_task__
3. Quando reacionado: verifica status novamente (UMA vez)
   ├── completed → on_success/on_failure
   └── ainda em execução → volta para __awaiting_task__
```

**`target.skill_id` — literal ou JSONPath:**

O campo `target.skill_id` aceita tanto um skill ID literal quanto uma referência
JSONPath que é resolvida em runtime contra o `pipeline_state`:

```yaml
# Literal (caso comum)
target:
  skill_id: skill_retencao_oferta_v1

# JSONPath (skill determinado dinamicamente pelo flow)
target:
  skill_id: "$.pipeline_state.evaluation_context.agent_context_skill_id"
```

Strings que começam com `$.` são resolvidas antes de chamar `agent_delegate`.
Útil quando o flow precisa delegar a um agente cujo `skill_id` é declarado em
dados externos (ex.: `evaluation_context_resolve` retorna qual agente especialista
invocar para coleta de contexto de avaliação).

**Payload da delegação:**

```typescript
agent_delegate({
  session_id:    sessionId,
  target_skill:  resolvedSkillId,    // literal ou valor resolvido do JSONPath
  payload: {
    customer_id:      customerId,
    pipeline_step:    step.id,        // nome do step no flow
    pipeline_context: state.results   // apenas results — não a estrutura interna
  },
  delegation_mode: "silent"
})
```

O agente delegado recebe os dados de negócio acumulados até aquele ponto (`results`) sem acesso a `retry_counters`, `transitions` ou `error_context`.

---

### `choice` — Ramificação Condicional

Avaliado **pelo engine localmente** — sem delegação, sem LLM. Avalia condições em ordem e transita para o `next` da primeira condição satisfeita.

```typescript
{
  id:         string
  type:       "choice"
  conditions: Array<{
    field:    string     // JSONPath no pipeline_state ou session: $.pipeline_state.results.*
    operator: "eq" | "neq" | "gt" | "gte" | "lt" | "lte" | "contains"
    value:    unknown
    next:     string    // step_id se condição satisfeita
  }>
  default:    string    // step_id quando nenhuma condição satisfeita
}
```

Exemplo de uso após step `reason`:

```typescript
// Step reason gravou em results.classificacao.intencao
{ field: "$.pipeline_state.results.classificacao.intencao", operator: "eq", value: "cancelamento", next: "tratar_cancelamento" }
{ field: "$.pipeline_state.results.classificacao.confianca", operator: "gte", value: 0.85, next: "atender_autonomo" }
```

---

### `catch` — Tratamento de Falha com Retry e Fallback

Executa **internamente pelo engine** — sem delegação A2A, sem envolver o Rules Engine. É acionado quando um step anterior (declarado em `error_context`) retornou `on_failure`.

```typescript
{
  id:             string
  type:           "catch"
  error_context:  string    // step_id que falhou
  strategies:     Array<RetryStrategy | FallbackStrategy>
  on_failure:     string    // step_id quando todas strategies esgotadas
}

RetryStrategy {
  type:         "retry"
  max_attempts: number
  delay_ms:     number   // default: 1000ms
  on_exhausted: string   // step_id interno (tipicamente o próximo fallback)
}

FallbackStrategy {
  type:       "fallback"
  id:         string
  target:     { skill_id: string } | { pool: string }
  on_success: string
  on_failure: string
}
```

**Sequência de execução do `catch`:**

```
Para cada strategy em ordem:
  retry:
    ├── Lê retry_counter do step
    ├── Se attempts < max_attempts:
    │   ├── Incrementa counter e persiste no Redis
    │   ├── Aguarda delay_ms
    │   ├── Re-executa o step original (agent_delegate novamente)
    │   ├── Sucesso → retorna resultado
    │   └── Falha → continua para próxima strategy
    └── Counter esgotado → continua para próxima strategy
  fallback:
    ├── Executa agent_delegate com target alternativo
    ├── Sucesso → on_success do fallback
    └── Falha → continua para próxima strategy

Todas strategies esgotadas → on_failure do catch
  com { error: "all_strategies_exhausted", failed_step, failed_result }
```

**Garantia de retomada:** os `retry_counters` são persistidos no Redis a cada tentativa. Se o engine falhar no meio de um `catch`, a nova instância sabe quantas tentativas já foram feitas e não as repete.

---

### `escalate` — Escalação para Pool

Emite uma escalação estruturada para o Rules Engine, que aloca um agente do pool declarado. Diferente do `task`, o Rules Engine injeta o `pipeline_state` completo no `context_package` do agente alocado — o agente sabe tudo que foi feito até aquele ponto.

```typescript
{
  id:      string
  type:    "escalate"
  target:  { pool: string }
  context: "pipeline_state"   // contexto injetado pelo Rules Engine
}
```

**Fluxo:**

```
Orquestrador emite conversation_escalate com pipeline_state completo
  → Rules Engine aloca agente do pool declarado
  → Agente recebe pipeline_state no context_package
  → Agente executa e sinaliza agent_done
  → Rules Engine atualiza pipeline_state no Redis com resultado do step
  → Orquestrador retoma com estado atualizado
  → Engine avalia condições e transita para próximo step
```

O step `escalate` retorna `__awaiting_escalation__` ao engine, que persiste o estado e retorna `escalated_human`. Quando o agente do pool conclui, o Rules Engine re-aciona o engine com o estado atualizado.

---

### `complete` — Encerramento do Pipeline

Encerra o pipeline com o `outcome` declarado. O orquestrador chama `agent_done` com esse outcome.

```typescript
{
  id:      string
  type:    "complete"
  outcome: "resolved" | "escalated_human" | "transferred_agent"
}
```

Retorna `__complete__` ao loop do engine. O engine marca o `pipeline_state` como `completed` e retorna `{ outcome, pipeline_state }`.

---

### `invoke` — Chamada Direta a Tool MCP

Chama uma tool de um domain MCP Server diretamente e persiste o resultado em `results`. Não envolve agente — é chamado diretamente pelo engine.

```typescript
{
  id:     string
  type:   "invoke"
  target: {
    mcp_server: string   // ex: "mcp-server-crm"
    tool:       string   // ex: "customer_get"
  }
  input:      Record<string, unknown>   // valores literais ou JSONPath
  output_as:  string                    // chave em results
  on_success: string
  on_failure: string
}
```

**Referências JSONPath no input:**

```typescript
// Valores do contexto de sessão
{ "customer_id": "$.session.customer_id" }

// Valores acumulados no pipeline
{ "historico": "$.pipeline_state.results.cliente.historico" }
```

Resultado acessível como `$.pipeline_state.results.{output_as}.*` por steps posteriores.

---

### `reason` — Inferência via AI Gateway

Invoca o AI Gateway com um prompt declarado e retorna JSON estruturado conforme `output_schema`. É uma **operação atômica** — uma única decisão estruturada (classificar, avaliar, extrair, selecionar). Para tarefas com múltiplos turnos de raciocínio, use um step `task` com skill especializada.

```typescript
{
  id:          string
  type:        "reason"
  prompt_id:   string    // referência ao Prompt Registry
  input:       Record<string, unknown>   // literais ou JSONPath
  output_schema: Record<string, {
    type:    "string" | "number" | "boolean" | "object" | "array"
    enum?:   unknown[]
    minimum?: number
    maximum?: number
    required?: string[]
    items?:    Record<string, unknown>
  }>
  output_as:          string
  max_format_retries: number   // default: 1
  on_success:         string
  on_failure:         string   // acionado após max_format_retries falhas de formato
}
```

**Validação pelo AI Gateway antes de persistir:**

| Tipo | Validação |
|---|---|
| `string` com `enum` | Valor deve ser um dos listados |
| `number` com `minimum`/`maximum` | Fora dos limites → `on_failure` |
| `object` com `required` | Propriedades ausentes → `on_failure` |
| `array` | Items fora do tipo → `on_failure` |

Se o modelo retorna JSON inválido, o AI Gateway tenta correção até `max_format_retries` vezes antes de acionar `on_failure`.

---

### `notify` — Mensagem ao Cliente (Unidirecional)

Envia mensagem ao cliente via Notification Agent. Não aguarda resposta. Para interações que aguardam resposta, use `menu`.

```typescript
{
  id:         string
  type:       "notify"
  message:    string   // suporta {{$.pipeline_state.results.*}} para interpolação
  channel:    "session" | "whatsapp" | "sms" | "email"   // default: session
  on_success: string
  on_failure: string
}
```

---

### `menu` — Captura de Input do Cliente

Captura input do cliente e **suspende o pipeline** até receber resposta. O Channel Gateway é responsável por toda renderização canal-específica e coleta sequencial em canais sem suporte nativo.

```typescript
{
  id:          string
  type:        "menu"
  interaction: "text" | "button" | "list" | "checklist" | "form"
  prompt:      string    // texto exibido ao cliente
  options?:    Array<{ id: string; label: string; description?: string }>  // para button, list, checklist
  fields?:     Array<{                                                      // para form
    id:       string
    label:    string
    type:     "text" | "email" | "phone" | "number" | "date" | "select" | "multiselect" | "boolean"
    required: boolean
    options?: Array<{ id: string; label: string }>  // para select/multiselect
  }>
  validation?: {
    min_length?:      number   // para text
    max_length?:      number
    min_selections?:  number   // para checklist
    max_selections?:  number
  }
  result:           string    // chave de destino em results
  timeout_seconds:  number
  on_success:       string
  on_failure:       string    // validação de formato falhou
  on_timeout:       string
}
```

**Resultado em `pipeline_state.results` por modo de interação:**

| Interaction | Tipo do resultado | Canais nativos | Fallback |
|---|---|---|---|
| `text` | `string` | Todos | — |
| `button` | `string` (option id) | WhatsApp (≤3), web chat | Numeração em texto |
| `list` | `string` (option id) | WhatsApp, web chat | Numeração em texto |
| `checklist` | `string[]` | Web chat | Entrada por vírgula |
| `form` | `object` | Web chat | Campo a campo sequencial |

**Ciclo de suspensão/retomada:**

```
Engine executa step menu
  → Envia prompt ao cliente via Notification Agent
  → Retorna __awaiting_selection__ ao engine
  → Engine persiste pipeline_state com status "in_progress"
  → Engine retorna (aguarda evento externo)

  ... cliente responde no canal ...

Channel Gateway coleta e normaliza a resposta
  → Entrega MenuSubmitEvent ao engine
  → Engine carrega pipeline_state
  → Valida resposta conforme interaction e validation
  ├── Válida: persiste em results[step.result], transita para on_success
  └── Inválida: transita para on_failure
```

**Responsabilidade do Channel Gateway:** toda lógica de renderização canal-específica e coleta sequencial (form campo a campo em WhatsApp) acontece no `channel-gateway`. O engine sempre recebe um único `MenuSubmitEvent` normalizado — não sabe quantos turnos de canal foram necessários.

**Validação de negócio fora do step menu:**

O step `menu` só valida formato (comprimento, seleções mínimas). Validação de negócio (CPF válido, data no futuro, etc.) pertence a steps subsequentes:

```typescript
// menu captura o CPF
{ id: "capturar_cpf", type: "menu", interaction: "text", result: "dados.cpf", on_success: "validar_cpf" }

// step seguinte valida o negócio
{ id: "validar_cpf", type: "task", target: { skill_id: "skill_validacao_cpf_v1" }, on_failure: "reperguntar_cpf" }
```

---

## Idempotência e Concorrência

### Lock distribuído

O engine usa `SET NX` com TTL de 5 minutos para garantir que apenas uma instância execute um pipeline de cada vez:

```
Chave: {tenant_id}:pipeline:{session_id}:running
Valor: "1"
TTL:   300s (5 min — tempo máximo de execução de um step)
```

Se o lock já existe ao tentar executar, o engine retorna `{ error: "PRECONDITION_FAILED", active_job_id }`. Chamadas duplicadas para o mesmo pipeline retornam esse erro sem executar nada.

O lock é **sempre** liberado no bloco `finally` do engine — mesmo em caso de exceção não tratada.

### Idempotência do `agent_delegate` (step task)

Antes de chamar `agent_delegate`, o engine verifica se já existe um `job_id` para o step no Redis:

```
Chave: {tenant_id}:pipeline:{session_id}:job:{step_id}
```

Se existe: pula a chamada `agent_delegate` e retoma o polling com o `job_id` salvo. Isso garante que uma re-execução após falha do engine **nunca** cria dois agentes delegados para o mesmo step.

---

## Retomada após Falha do Orquestrador

O orquestrador, ao iniciar, sempre verifica se existe `pipeline_state` ativo:

```typescript
const state = await stateManager.get(tenantId, sessionId)

if (state?.status === "in_progress") {
  // retoma do current_step_id — nunca reinicia do entry
} else {
  // novo pipeline — inicia do entry
  state = PipelineStateManager.create(skillId, flow.entry)
}
```

Isso garante que falhas de qualquer tipo (timeout de SLA, crash de instância, restart por deploy) não percam o estado do pipeline. O cliente não percebe a interrupção.

---

## Delegação Parcial do Context — pipeline_context

Quando o orquestrador delega um step `task`, passa apenas `results` como `pipeline_context` — não o `pipeline_state` completo:

```typescript
payload: {
  pipeline_step:    step.id,
  pipeline_context: state.results   // apenas dados de negócio
}
```

O agente delegado recebe o que precisa para executar a tarefa sem acesso à estrutura interna do flow (`retry_counters`, `transitions`, `error_context`). Ao concluir com `agent_done`, o resultado retorna em `results[step.id]` — disponível ao orquestrador para o próximo step.

---

## Relação com Outros Módulos

```
skill-flow-engine
  ├── consome → @plughub/schemas       (PipelineState, FlowStep, SkillFlow)
  ├── consome → mcp-server-plughub     (agent_delegate, agent_done via mcpCall)
  ├── escreve → Redis                  (pipeline_state, locks, job_ids)
  ├── chama   → AI Gateway             (steps reason via aiGatewayCall)
  └── é acionado por:
        ├── agente orquestrador        (via SDK, ao receber skill de orquestração)
        └── Rules Engine               (ao retornar controle após step escalate)
```

---

## Status de Implementação

| Componente | Status |
|---|---|
| `SkillFlowEngine` (engine.ts) | ✅ Implementado — loop, retomada, lock, persistência |
| `PipelineStateManager` (state.ts) | ✅ Implementado — get/save/complete/fail/lock/job_id |
| `executeStep` dispatch (executor.ts) | ✅ Implementado |
| step `task` (sync + async + idempotência) | ✅ Implementado |
| step `catch` (retry + fallback + persistência de contadores) | ✅ Implementado |
| step `choice` | ✅ Implementado |
| step `escalate` | ✅ Implementado |
| step `complete` | ✅ Implementado |
| step `invoke` | ✅ Implementado |
| step `reason` | ✅ Implementado |
| step `notify` | ✅ Implementado |
| step `menu` — suspensão e retomada via `MenuSubmitEvent` | ⚠️ Suspensão implementada; retomada depende de integração com Channel Gateway |
