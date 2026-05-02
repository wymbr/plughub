# ContextStore — Guia do Modelo de Contexto

> **Versão:** PlugHub Arc 2+  
> **Substitui:** `contact_context` / `context_package` (legado — em depreciação)  
> **Fonte de verdade técnica:** `CLAUDE.md § ContextStore — unified session state`

---

## O que é o ContextStore

O ContextStore é o repositório unificado de estado de sessão do PlugHub. É um hash
Redis por sessão no qual qualquer componente da plataforma pode ler e escrever campos
tipados, com confiança e rastreabilidade de origem.

```
{tenantId}:ctx:{sessionId}   ← Redis hash
  field = nome do tag (ex.: "caller.nome", "session.sentimento.current")
  value = JSON-encoded ContextEntry
```

Diferente do `pipeline_state` — que é estado de execução efêmero de um fluxo —
o ContextStore persiste durante **toda a sessão** e é acessível a todos os agentes,
ao Agent Assist UI e às ferramentas MCP de supervisão.

---

## Modelo de dados

### ContextEntry

Cada campo armazena um `ContextEntry` serializado como JSON:

```typescript
interface ContextEntry {
  value:      unknown           // string | number | boolean | object
  confidence: number            // 0.0–1.0
  source:     string            // origin do dado
  visibility: "agents_only" | "all"
  updated_at: string            // ISO-8601
}
```

### Escala de confiança

| Faixa | Significado | Comportamento |
|---|---|---|
| 0.9–1.0 | Confirmado explicitamente | Usar sem confirmação |
| 0.7–0.9 | Inferido com alta certeza | Usar sem confirmação |
| 0.4–0.7 | Incerto | Confirmar se `force_confirmation = true` |
| 0.0–0.4 | Desconhecido | Coletar novamente |

### Fontes (`source`)

| Valor | Origem |
|---|---|
| `mcp_call:mcp-server-crm:customer_get` | Consultado via MCP tool |
| `ai_inferred:sentiment_emitter` | Inferido pelo AI Gateway |
| `ai_inferred:reason_step` | Extraído por step `reason` com `context_tags` |
| `customer_input` | Fornecido diretamente pelo cliente |
| `pipeline_state` | Herdado de agente anterior na mesma sessão |

### Namespaces

| Namespace | Escopo | Escrito por |
|---|---|---|
| `caller.*` | Dados do cliente (nome, cpf, conta, motivo) | McpInterceptor; `context_tags.outputs` em reason/invoke |
| `session.*` | Estado da sessão atual (sentimento, pergunta, histórico) | reason/invoke steps; `sentiment_emitter` |
| `account.*` | Dados de conta (plano, status) | invoke step com buscar_crm |

---

## Leitura: `@ctx.*` em step inputs

Qualquer campo de `input:` em um step `reason` ou `invoke` pode referenciar o
ContextStore com a sintaxe `@ctx.<namespace>.<campo>`:

```yaml
- id: analisar
  type: reason
  prompt_id: sac_analisar_v1
  input:
    nome_cliente:    "@ctx.caller.nome"           # resolve ContextEntry.value
    historico:       "@ctx.session.historico_mensagens"
    plano_atual:     "@ctx.account.plano_atual"
    sentimento:      "@ctx.session.sentimento.categoria"
```

**Resolução:** o engine lê o hash Redis, parseia o `ContextEntry` e retorna `entry.value`.
Retorna `""` se o campo estiver ausente — nunca lança exceção.

### `@ctx.*` em interpolação de mensagens

Em steps `menu` e `notify`, use `{{@ctx.*}}`:

```yaml
- id: saudar
  type: notify
  message: "Olá, {{@ctx.caller.nome}}! Como posso ajudar hoje?"
```

---

## Leitura: `@ctx.*` em choice conditions

O step `choice` suporta condições baseadas no ContextStore:

```yaml
- id: verificar_gaps
  type: choice
  conditions:
    - field:    "@ctx.caller.customer_id"
      operator: exists
      next:     buscar_crm
    - field:    "@ctx.caller.cpf"
      operator: exists
      next:     buscar_crm
    - field:    "@ctx.caller.motivo_contato"
      operator: confidence_gte
      value:    0.7
      next:     finalizar
  default: gerar_pergunta
```

**Operadores disponíveis:**

| Operador | Descrição |
|---|---|
| `exists` | Campo presente com qualquer valor |
| `not_exists` | Campo ausente |
| `eq` | Valor igual |
| `ne` | Valor diferente |
| `gt` / `gte` | Maior que / maior ou igual |
| `lt` / `lte` | Menor que / menor ou igual |
| `confidence_gte` | Confiança do entry ≥ valor |

---

## Escrita: `context_tags` em steps reason e invoke

Qualquer step `reason` ou `invoke` pode declarar mapeamentos de saída para o
ContextStore via `context_tags.outputs`. A escrita é **fire-and-forget** — não
bloqueia o fluxo:

```yaml
- id: extrair_campos
  type: reason
  prompt_id: contexto_extracao_v2
  input:
    resposta: "$.pipeline_state.resposta_cliente"
  context_tags:
    outputs:
      cpf:
        tag: caller.cpf
        confidence: 0.85
        merge: highest_confidence   # não sobrescreve se já existe com confiança maior
      motivo_contato:
        tag: caller.motivo_contato
        confidence: 0.80
        merge: highest_confidence
      sentimento_atual:
        tag: caller.sentimento_atual
        confidence: 0.70
        merge: overwrite            # sempre sobrescreve
  output_schema:
    cpf:            { type: string, required: false }
    motivo_contato: { type: string, required: false }
    sentimento_atual: { type: string, required: false }
  output_as: extracao
```

**Estratégias de merge:**

| Estratégia | Comportamento |
|---|---|
| `overwrite` | Sempre substitui o valor existente |
| `highest_confidence` | Só substitui se a nova confiança for maior |
| `append` | Adiciona ao array existente (para campos multivalorados) |

### `context_tags` em inputs (leitura declarativa)

```yaml
context_tags:
  inputs:
    nome_cliente:
      tag: caller.nome
      required: false   # não falha se ausente
```

Antes de chamar o LLM/MCP, o engine lê `@ctx.caller.nome` e injeta o valor no
campo `nome_cliente` do input. Equivalente a `"@ctx.caller.nome"` no input, mas
com a vantagem de declarar explicitamente os contratos do step.

---

## Escrita automática: McpInterceptor

Agentes nativos que usam `@plughub/sdk` recebem acumulação de contexto **sem
código adicional**. O `McpInterceptor` detecta `contextRegistry[serverName][toolName]`
e extrai inputs/outputs automaticamente antes e depois de cada `callTool()`.

Exemplo: ao chamar `mcp-server-crm/customer_get`, o interceptor escreve
automaticamente no ContextStore:

```
caller.nome        → "João Silva"  (confidence: 0.95, source: mcp_call:mcp-server-crm:customer_get)
caller.cpf         → "123.456.789-00"  (confidence: 0.95)
caller.account_id  → "ACC-001"  (confidence: 0.95)
caller.telefone    → "+55 11 99999-0000"  (confidence: 0.95)
account.plano_atual → "plano_premium"  (confidence: 0.95)
account.status     → "ativo"  (confidence: 0.95)
```

---

## Escrita automática: AI Gateway — sentimento

O AI Gateway escreve dois campos após cada turno LLM:

| Tag | Valor | Confidence | Source |
|---|---|---|---|
| `session.sentimento.current` | score numérico (4 decimais, ex.: `-0.4123`) | 0.80 | `ai_inferred:sentiment_emitter` |
| `session.sentimento.categoria` | `"satisfied"` / `"neutral"` / `"frustrated"` / `"angry"` | 0.80 | `ai_inferred:sentiment_emitter` |

Faixas de classificação (configuráveis por tenant):

| Score | Categoria |
|---|---|
| ≥ 0.3 | `satisfied` |
| ≥ -0.3 | `neutral` |
| ≥ -0.6 | `frustrated` |
| < -0.6 | `angry` |

---

## `required_context` — pré-condições declarativas

O cabeçalho de um skill YAML pode declarar os campos mínimos necessários:

```yaml
required_context:
  - tag: caller.nome
    confidence_min: 0.8
  - tag: caller.motivo_contato
    confidence_min: 0.7
  - tag: caller.intencao_primaria
    confidence_min: 0.7
    required: false   # desejável mas não bloqueante
```

O engine pré-computa um `GapsReport` antes do primeiro step e escreve
`@ctx.__gaps__` no ContextStore. O step inicial pode inspecionar os gaps para
decidir se precisa coletar dados antes de prosseguir.

---

## TTL e lifecycle

- TTL padrão: **14 400 s (4 horas)** — renovado em cada escrita
- Namespace padrão de visibilidade: `agents_only`
- O hash é deletado junto com a sessão ao fechar (ou expira por TTL)
- **Nunca use Redis diretamente** — use sempre o SDK (`ContextStore`) ou `context_tags`

---

## Casos de uso

### Caso 1 — Agente simples com CRM lookup

Cenário: agente SAC recebe chamada, busca dados no CRM e responde.

```yaml
entry: buscar_cliente

steps:
  - id: buscar_cliente
    type: invoke
    target:
      mcp_server: mcp-server-crm
      tool: customer_get
    input:
      customer_id: "@ctx.caller.customer_id"
    context_tags:
      outputs:
        nome:         { tag: caller.nome,         confidence: 0.95, merge: highest_confidence }
        plano_atual:  { tag: account.plano_atual,  confidence: 0.95, merge: overwrite }
        status_conta: { tag: account.status,        confidence: 0.95, merge: overwrite }
    output_as: dados_crm
    on_success: analisar
    on_failure: analisar   # continua mesmo sem CRM

  - id: analisar
    type: reason
    prompt_id: sac_responder_v1
    input:
      nome_cliente:  "@ctx.caller.nome"
      plano:         "@ctx.account.plano_atual"
      status:        "@ctx.account.status"
      ultima_msg:    "$.pipeline_state.ultima_mensagem"
    context_tags:
      outputs:
        resposta:
          tag: session.ultima_resposta
          confidence: 1.0
          merge: overwrite
    output_as: resposta_ia
    on_success: responder

  - id: responder
    type: notify
    message: "{{@ctx.session.ultima_resposta}}"
    on_success: finalizar

  - id: finalizar
    type: complete
    outcome: resolved
```

**O que fica no ContextStore após execução:**
```json
{
  "caller.nome":           { "value": "João Silva",    "confidence": 0.95, "source": "mcp_call:mcp-server-crm:customer_get" },
  "account.plano_atual":   { "value": "plano_premium", "confidence": 0.95, "source": "mcp_call:mcp-server-crm:customer_get" },
  "account.status":        { "value": "ativo",         "confidence": 0.95, "source": "mcp_call:mcp-server-crm:customer_get" },
  "session.ultima_resposta": { "value": "Olá João...", "confidence": 1.0,  "source": "ai_inferred:reason_step" },
  "session.sentimento.current":   { "value": 0.42, "confidence": 0.80, "source": "ai_inferred:sentiment_emitter" },
  "session.sentimento.categoria": { "value": "satisfied", "confidence": 0.80, "source": "ai_inferred:sentiment_emitter" }
}
```

---

### Caso 2 — Cadeia multi-agente com context_tags

Cenário: agente SAC detecta necessidade de retenção e escala para agente especialista.
O agente de retenção não precisa re-coletar dados — lê direto do ContextStore.

```
agente_sac_ia_v1
  → analisar (reason): escreve session.escalar_solicitado via context_tags
  → verificar_escalada (choice): @ctx.session.escalar_solicitado eq true → acumular_contexto
  → acumular_contexto (task assist: agente_contexto_ia_v1)
       │ lê @ctx.caller.customer_id / @ctx.caller.cpf
       │ busca CRM → escreve caller.nome, caller.plano_atual, account.status
       └─ escreve caller.motivo_contato, caller.sentimento_atual via extrair_campos
  → escalar (escalate: pool retencao_ia)
       └─ agente_retencao_ia_v1 lê @ctx.caller.* sem nenhuma coleta adicional
```

O hash `{tenantId}:ctx:{sessionId}` é **o mesmo** durante toda a sessão — não
há cópia de dados entre agentes. O segundo agente herda tudo o que o primeiro
acumulou.

```yaml
# agente_retencao_ia_v1.yaml — pode ler contexto sem coletar
- id: elaborar_oferta
  type: reason
  prompt_id: retencao_oferta_v1
  input:
    nome:        "@ctx.caller.nome"
    plano:       "@ctx.account.plano_atual"
    motivo:      "@ctx.caller.motivo_contato"
    sentimento:  "@ctx.session.sentimento.categoria"
```

---

### Caso 3 — Coleta progressiva com `agente_contexto_ia_v1`

O agente de contexto é invocado via `task assist` por qualquer agente especialista
antes de executar lógica de negócio que dependa de dados do cliente.

```yaml
# Em qualquer agente especialista:
- id: acumular_contexto
  type: task
  target:
    skill_id: agente_contexto_ia_v1
  mode: assist
  execution_mode: sync
  on_success: proximo_step
  on_failure: proximo_step   # nunca bloquear o fluxo principal
```

**Fluxo interno do agente_contexto_ia_v1 (v2):**

```
verificar_gaps (choice):
  @ctx.caller.customer_id exists  → buscar_crm       ← 0 LLM calls
  @ctx.caller.cpf exists          → buscar_crm       ← 0 LLM calls
  default                         → verificar_completude

verificar_completude (choice):
  @ctx.caller.motivo_contato confidence_gte 0.7  → finalizar   ← 0 LLM calls
  default                                         → gerar_pergunta

buscar_crm (invoke: mcp-server-crm/customer_get)
  → context_tags.outputs: caller.nome/cpf/account_id/plano_atual... (confidence=0.95)
  → on_success: verificar_completude

gerar_pergunta (reason LLM #1) → session.pergunta_coleta
coletar_cliente (menu)         → resposta do cliente
extrair_campos (reason LLM #2) → caller.cpf, caller.motivo_contato, etc.
```

**Custo de LLM por caminho:**
- customer_id presente + CRM resolve: **0 LLM calls**
- Precisa só de motivo_contato: **1 LLM call** (gerar_pergunta)
- Pior caso (sem identificadores): **2 LLM calls**

---

### Caso 4 — Supervisão humana via Agent Assist UI

O supervisor abre o painel de uma sessão ativa. A aba **Contexto** exibe os campos
agrupados por namespace com badge de confiança e fonte:

```
┌─ Contexto ──────────────────────────────────────────────┐
│ ContextStore  [6 campos]                                 │
│                                                          │
│ caller                                                   │
│   Nome          João Silva      ████ 95%   CRM          │
│   CPF           ***.456.789-00  ████ 95%   CRM          │
│   Motivo        cancelamento    ███░ 80%   IA            │
│   Sentimento    frustrado       ██░░ 70%   IA     🔒     │
│                                                          │
│ account                                                  │
│   Plano atual   plano_premium   ████ 95%   CRM          │
│   Status        ativo           ████ 95%   CRM          │
└──────────────────────────────────────────────────────────┘
```

O `supervisor_state` MCP tool lê direto do Redis (`{tenantId}:ctx:{sessionId}`)
e retorna `customer_context.context_snapshot`:

```json
{
  "customer_context": {
    "context_snapshot": {
      "caller.nome":        { "value": "João Silva",  "confidence": 0.95, "source": "mcp_call:mcp-server-crm:customer_get", "visibility": "agents_only" },
      "caller.motivo_contato": { "value": "cancelamento", "confidence": 0.80, "source": "ai_inferred:reason_step", "visibility": "agents_only" },
      "session.sentimento.categoria": { "value": "frustrated", "confidence": 0.80, "source": "ai_inferred:sentiment_emitter", "visibility": "agents_only" }
    },
    "contact_context": null
  }
}
```

---

### Caso 5 — Workflow outbound com sessão originadora

Cenário: durante atendimento ativo, agente SAC aciona workflow de cobrança.
O workflow precisa acessar o contexto acumulado na sessão do cliente.

**Trigger do workflow:**

```json
POST /v1/workflow/trigger
{
  "tenant_id":         "tenant_demo",
  "flow_id":           "fluxo_cobranca_v1",
  "trigger_type":      "event",
  "session_id":        "sess_abc123",
  "origin_session_id": "sess_abc123",
  "context": {
    "invoice_id": "INV-001",
    "amount": 15000
  }
}
```

**No workflow YAML — `@ctx.*` resolve para a sessão `sess_abc123`:**

```yaml
- id: personalizar_oferta
  type: reason
  prompt_id: cobranca_personalizar_v1
  input:
    nome:        "@ctx.caller.nome"           # lê de sess_abc123
    plano:       "@ctx.account.plano_atual"   # lê de sess_abc123
    motivo:      "@ctx.caller.motivo_contato" # lê de sess_abc123
    fatura_id:   "$.pipeline_state.contact_context.invoice_id"
    valor:       "$.pipeline_state.contact_context.amount"
```

**Resolução de `sessionId` no EngineRunner:**

```typescript
// origin_session_id presente → usa ContextStore da sessão real
// origin_session_id ausente  → usa instance.id (workflow headless)
const contextSessionId = instance.origin_session_id ?? instance.id
```

**Workflows standalone** (schedule, webhook externo, sem sessão originadora):
`origin_session_id = null` → o ContextStore key é `{tenant}:ctx:{instance.id}`,
criando um hash isolado por workflow para os dados acumulados durante a execução.

---

### Caso 6 — Leitura em step choice sem LLM

Cenário: roteamento condicional baseado no sentimento detectado, sem chamar LLM.

```yaml
- id: verificar_sentimento
  type: choice
  conditions:
    - field:    "@ctx.session.sentimento.categoria"
      operator: eq
      value:    "angry"
      next:     escalar_urgente
    - field:    "@ctx.session.sentimento.categoria"
      operator: eq
      value:    "frustrated"
      next:     escalar_normal
    - field:    "@ctx.caller.motivo_contato"
      operator: eq
      value:    "cancelamento_plano"
      next:     fluxo_retencao
  default: continuar_atendimento
```

Custo: **0 LLM calls** — leituras Redis puras (~1ms).

---

## Boas práticas

### O produtor é responsável por escrever — não o consumidor

Errado:
```yaml
# agente B lendo e "copiando" dado produzido por agente A
input:
  nome: "$.pipeline_state.agente_a.nome_cliente"  # frágil — depende de estrutura interna
```

Correto:
```yaml
# agente A escreve via context_tags.outputs ao produzir o dado
# agente B lê via @ctx sem saber de onde veio
input:
  nome: "@ctx.caller.nome"
```

### `$.pipeline_state.*` vs `@ctx.*`

| Use `$.pipeline_state.*` quando... | Use `@ctx.*` quando... |
|---|---|
| É output de um step específico (ex.: `$.pipeline_state.resposta_ia.resposta`) | É dado do cliente ou da sessão que outros agentes precisam |
| Só existe dentro deste fluxo de execução | Precisa sobreviver a transições entre agentes |
| Não é contexto do cliente — é estado do algoritmo | O Agent Assist UI ou supervisor precisa ver |

### Nunca perguntar o que já está com confiança suficiente

```yaml
# Correto: verificar antes de perguntar
- id: verificar
  type: choice
  conditions:
    - field: "@ctx.caller.cpf"
      operator: confidence_gte
      value: 0.8
      next: proximo_step   # CPF já confirmado — não pedir novamente
  default: coletar_cpf
```

### Usar `merge: highest_confidence` para dados persistentes

Campos como `caller.nome` e `caller.cpf` geralmente têm alta confiança quando
vieram do CRM. Ao extrair da conversa (menor confiança), não sobrescrever:

```yaml
context_tags:
  outputs:
    nome:
      tag: caller.nome
      confidence: 0.85
      merge: highest_confidence  # preserva 0.95 do CRM se já existir
```

---

## Referências de código

| Componente | Arquivo | Responsabilidade |
|---|---|---|
| Schema | `packages/schemas/src/context-store.ts` | `ContextEntry`, `ContextEntrySchema` |
| SDK | `packages/sdk/src/context-store.ts` | `ContextStore` — leitura/escrita Redis |
| SDK | `packages/sdk/src/context-accumulator.ts` | `ContextAccumulator` — extração de MCP calls |
| Engine | `packages/skill-flow-engine/src/interpolate.ts` | Resolução de `@ctx.*` |
| Engine | `packages/skill-flow-engine/src/steps/choice.ts` | Operadores `exists`, `confidence_gte` |
| Engine | `packages/skill-flow-engine/src/steps/reason.ts` | `context_tags` leitura/escrita |
| AI Gateway | `packages/ai-gateway/sentiment_emitter.py` | Escrita de `session.sentimento.*` |
| MCP Server | `packages/mcp-server-plughub/tools/supervisor.ts` | `supervisor_state` → `context_snapshot` |
| Agent Assist | `packages/agent-assist-ui/src/components/tabs/ContextoTab.tsx` | Renderização do ContextSnapshotCard |
| Skill Flow Worker | `packages/skill-flow-worker/src/engine-runner.ts` | `origin_session_id` → `contextSessionId` |
| Skill YAML | `packages/skill-flow-engine/skills/agente_contexto_ia_v1.yaml` | Referência de implementação |
| E2E | `packages/e2e-tests/scenarios/17_context_store.ts` | Scenario de teste end-to-end |
