# Arc 16 — Three-Tier Business Process Orchestration

> Versão 1.0 — 2026-05-21

## Visão Geral

Arc 16 formaliza o modelo de orquestração em três camadas que emerge da combinação de Journey (Arc 10), Workflow (Arc 4) e Skill Flow em um framework coeso de execução de processos de negócio.

O modelo responde à pergunta: **como um processo de negócio longo, multi-sessão e multi-canal é decomposto em unidades executáveis que os primitivos da plataforma já suportam?**

---

## O Modelo de Três Tiers

```
Tier 1 — Business Workflow
  ├── Processo de negócio de longa duração
  ├── Channel-agnostic, Journey-scoped
  ├── Declara intenção, nunca mecânica de canal
  └── task / collect / invoke / suspend / choice / complete

      Tier 2 — Execution Workflow
        ├── Orquestra uma sessão/contato específico
        ├── Sequencia agentes de interação
        ├── Session-scoped, lê/escreve @ctx.journey.*
        └── task / reason / invoke / choice / complete

            Tier 3 — Interaction Agent
              ├── I/O atômico — uma tarefa por agente
              ├── Reutilizável em qualquer Execution Workflow
              ├── Completamente channel-agnostic
              └── LLM + MCP tools (context_set, context_get, etc.)
```

### Tier 1 — Business Workflow

Corresponde ao `WorkflowInstance` vinculado a uma `Journey`. Define o processo completo sem conhecimento de sessões, canais ou mecânica conversacional.

**Responsabilidades:**
- Sequenciar etapas de negócio de longa duração
- Invocar sistemas externos via MCP tools
- Aguardar sinalizações externas (`suspend`)
- Criar novos contatos proativos (`collect`)
- Delegar interações a Execution Workflows (`task`, `collect + skill`)

**Não é responsabilidade do Tier 1:**
- Saber qual canal usar (Channel Gateway decide)
- Saber como conduzir a conversa (Tier 2 decide)
- Saber como perguntar ou validar dados (Tier 3 decide)

### Tier 2 — Execution Workflow

Skill Flow de escopo de sessão que orquestra os agentes de interação para um único contato. Criado como `skill` de um `task` ou `collect` do Tier 1.

**Responsabilidades:**
- Sequenciar agentes de interação (Tier 3) para a sessão atual
- Ler contexto da Journey (`@ctx.journey.*`) para personalizar a interação
- Escrever resultados coletados de volta à Journey (`context_tags.outputs: [journey.*]`)
- Lidar com retry e fallback dentro da sessão

**Não é responsabilidade do Tier 2:**
- Conhecer o processo de negócio completo
- Decidir etapas futuras além da sessão atual
- Invocar sistemas externos de backend

### Tier 3 — Interaction Agent

Agente AI com LLM que executa uma única tarefa de I/O. Invocado como `task` pelo Tier 2.

**Responsabilidades:**
- Conduzir troca conversacional atômica (coletar um dado, confirmar uma escolha, entregar uma informação)
- Validar e tratar inputs do cliente
- Escrever resultado no ContextStore via `context_set`
- Sinalizar conclusão via `agent_done`

**Características:**
- Reutilizável: `agente_coleta_cpf_v1` é usado por qualquer Execution Workflow que precise de CPF
- Stateless entre invocações: todo estado vem do ContextStore
- Completamente ignorante de canal: vê apenas mensagens normalizadas

---

## Journey ContextStore — Namespace @ctx.journey.*

### Problema atual

O ContextStore é keyed por `sessionId`. Quando um `collect` cria uma nova sessão, dados escritos pelos agentes (Tier 3) naquela sessão são invisíveis para o Business Workflow (Tier 1) que roda em sessão diferente. Isso impede o fluxo natural de dados entre etapas de processos multi-sessão.

### Solução: namespace journey

**Nova Redis key:** `{tenantId}:ctx:journey:{journeyId}`

Estrutura idêntica ao ContextStore de sessão: hash de `ContextEntry` com `value`, `confidence`, `source`, `visibility`, `updated_at`.

```
Hoje:     {tenantId}:ctx:{sessionId}
Arc 16:   {tenantId}:ctx:journey:{journeyId}   ← compartilhado entre todas as sessões da Journey
```

### Resolução @ctx.journey.*

O Skill Flow engine resolve `@ctx.journey.*` da seguinte forma:

1. Verifica se a sessão atual tem `journey_id` no ContextStore de sessão
2. Se sim, lê do hash `{tenantId}:ctx:journey:{journeyId}`
3. Fallback: `@ctx.session.*` para compatibilidade com sessões sem Journey

### Escrita journey.* via context_tags

Os steps de Tier 1 e Tier 2 podem especificar namespace `journey.*` em `context_tags.outputs`:

```yaml
context_tags:
  outputs:
    - tag: journey.nome_paciente      # ← escreve no journey namespace
      confidence: 0.95
    - tag: session.last_agent         # ← escreve no session namespace (comportamento atual)
```

O engine direciona a escrita para o hash correto baseado no prefixo do tag.

### Escopo de visibilidade

`journey.*` tags têm `visibility: agents_only` por padrão (nunca expostos ao cliente). Tags específicas podem sobrescrever: `journey.canal_preferido` pode ter `visibility: all` se o canal de resposta precisar ser conhecido pelo Channel Gateway.

### TTL

O hash journey persiste enquanto a Journey existir. Removido quando a Journey fecha (`journey_completed`, `journey_failed`, `journey_cancelled`). TTL de segurança: 30 dias (configurável).

---

## Channel Capability Negotiation — Collect sem Canal Explícito

### Problema atual

O step `collect` exige `channel` explícito no YAML, o que acopla o Business Workflow a um canal específico. Isso viola o princípio de que Tier 1 é channel-agnostic.

### Solução: campo requires[]

```yaml
# Atual (acoplado a canal)
- id: capturar_documentos
  type: collect
  channel: webchat
  target: "@ctx.caller.phone"
  skill: exec_upload_docs_v1

# Arc 16 (channel-agnostic)
- id: capturar_documentos
  type: collect
  skill: exec_upload_docs_v1
  requires: [file_upload]            # ← capacidade necessária, não canal
  timeout_hours: 48
```

### Como o Channel Gateway resolve o canal

Quando o skill-flow-worker dispara um `collect` sem `channel`:

```
1. Lê @ctx.journey.canal_preferido e @ctx.journey.available_channels
2. Filtra: qual canal suporta as capacidades em requires[]?
3. Prioriza canal preferido se suporta a capacidade
4. Fallback para próximo canal disponível
5. Se nenhum suporta: falha com collect_error (tratável via catch step)
```

### Capacidades declaráveis

| Capability | Descrição | Canais que suportam |
|---|---|---|
| `file_upload` | Receber arquivo do cliente | webchat, whatsapp, email |
| `audio` | Comunicação por voz | webrtc, voice |
| `video` | Comunicação por vídeo | webrtc |
| `text` | Troca de mensagens de texto | todos |
| `masked_input` | Input seguro (senha, CPF) | webchat |
| `rich_menu` | Menu com botões/imagens | webchat, whatsapp |

### available_channels no ContextStore

O Channel Gateway escreve `journey.available_channels` no namespace journey na primeira sessão de cada canal, com `confidence: 1.0`:

```
journey.available_channels = ["voice", "webchat"]
journey.canal_preferido = "voice"           # canal do primeiro contato
journey.whatsapp_phone = "+5511999999999"   # se disponível no cadastro
journey.email = "cliente@email.com"         # se disponível
```

Esses valores ficam disponíveis para todos os steps `collect` subsequentes.

### Campo channel permanece opcional

`channel` explícito ainda é suportado para casos onde o workflow PRECISA de um canal específico (ex: envio de PDF por email). Quando presente, Channel Gateway usa o canal especificado sem negociação.

---

## Journey como Superfície Pública de Resume

### Problema atual

Retomada de workflows suspensos usa `POST /v1/workflow/instances/{id}/resume` — uma entidade interna que callers externos não deveriam conhecer diretamente. Isso expõe detalhes de implementação e não aproveita a Journey como entidade estável de negócio.

### Novos endpoints Journey

```http
# Listar Journeys suspensas por pool (estável entre upgrades de skill; alinhado com accessible_pools[] do JWT)
GET /v1/journeys?pool_id={pool_id}&status=suspended&reason=approval
→ [{ journey_id, customer_id, started_at, suspended_since, context_snapshot }]

# Retomar Journey (delega internamente ao WorkflowInstance)
POST /v1/journeys/{journey_id}/resume
Body: { "context": { "decisao": "aprovado", "observacao": "..." } }
→ 200 OK | 409 (não está suspensa) | 404 (não encontrada)
```

### Armazenamento pool_id na Journey

Journey armazena `pool_id` no momento da criação. Dois motivos:

1. **Estabilidade de versão:** `skill_id` inclui versão (`_v1`, `_v2`). Filtrar por `skill_id` quebraria pollers a cada upgrade. `pool_id` é estável — `journey_list_suspended` resolve internamente `pool_id → skill_ids ativos`.
2. **Alinhamento com access control:** o JWT já carrega `accessible_pools[]`. Supervisores têm visibilidade por pool, não por skill. Filtrar journeys por `pool_id` usa o mesmo eixo de autorização existente sem criar `accessible_skills[]`.

Origem do `pool_id`: skill-flow-worker lê `@ctx.session.pool.id` do ContextStore (escrito pelo Routing Engine na alocação) antes de criar a Journey quando `creates_journey: true` dispara.

### Relacionamento com WorkflowInstance

```
Journey (entidade de negócio)
  └── workflow_instance_id (nullable FK)
        └── resume_token (UUID interno)
              ← encapsulado — caller externo nunca precisa ver
```

`POST /v1/journeys/{id}/resume` resolve internamente o `resume_token` e chama o endpoint existente do workflow-api. O caller externo não vê a camada de execução.

---

## Novos MCP Tools para Journey

Para suportar o padrão de poller workflow (Tier 1 que monitora Journeys de outros processos):

### journey_list_suspended

```typescript
tool: journey_list_suspended
args:
  pool_id: string   // estável entre versões de skill; resolve internamente pool → skill_ids ativos
  reason?: "approval" | "webhook" | "input" | "timer"
  limit?: number    // default 50
returns: JourneyInfo[]
```

### journey_resume

```typescript
tool: journey_resume
args:
  journey_id: string
  context?: Record<string, unknown>   // escrito no journey namespace antes de resumir
returns: { resumed: boolean, next_step: string }
```

Esses tools passam pelo McpInterceptor (auditados em `mcp.audit`) e requerem permissão `journey.resume` no JWT.

---

## Eliminação do Step Type notify

O step type `notify` é açúcar sintático sobre `invoke: notification_send`. Em Arc 16 é oficialmente depreciado como step type, substituído por:

```yaml
# Antes (depreciado)
- id: avisar
  type: notify
  text: "Seu exame foi aprovado"
  visibility: all

# Agora
- id: avisar
  type: invoke
  tool: notification_send
  args:
    text: "Seu exame foi aprovado"
    visibility: all
```

**Exceção preservada:** o sub-campo `notify` dentro do step `suspend` é mantido por garantia de atomicidade — a notificação deve ser enviada no exato momento da persistência da suspensão, não como step separado.

O engine continua reconhecendo `type: notify` para compatibilidade com YAMLs existentes, mas emite warning no log de execução.

---

## Cenários de Referência

### Cenário 1 — Aprovação de Exame pela Seguradora

**Fluxo multi-canal:** ligação telefônica → upload de documentos via webchat → retorno via WhatsApp

```yaml
# skill_aprovacao_exame_v1.yaml — Tier 1 (Business Workflow)
id: skill_aprovacao_exame_v1
creates_journey: true

steps:
  # Sessão de origem — telefone ou qualquer canal inbound
  - id: sessao_coleta
    type: task
    pool: atendimento_saude
    skill: exec_coleta_exame_v1          # Tier 2
    context_tags:
      outputs:
        - tag: journey.nome_paciente
        - tag: journey.cpf
        - tag: journey.tipo_exame
        - tag: journey.convenio_id
        - tag: journey.documentos_ids

  # Submete à seguradora via MCP
  - id: submeter
    type: invoke
    tool: seguradora_submeter_pedido
    args:
      paciente:   "@ctx.journey.nome_paciente"
      exame:      "@ctx.journey.tipo_exame"
      documentos: "@ctx.journey.documentos_ids"
      convenio:   "@ctx.journey.convenio_id"
    context_tags:
      outputs:
        - tag: journey.protocolo_seguradora

  # Aguarda decisão — poller externo ou webhook chamará journey_resume
  - id: aguardar_decisao
    type: suspend
    reason: approval
    timeout_hours: 72

  # Roteamento pelo resultado
  - id: verificar_resultado
    type: choice
    conditions:
      - if:   "@ctx.journey.decisao == 'aprovado'"
        goto: informar_aprovado
      - else: goto: informar_negado

  # Novo contato — canal negociado automaticamente
  - id: informar_aprovado
    type: collect
    skill: exec_retorno_aprovado_v1      # Tier 2
    requires: [text]
    timeout_hours: 48

  - id: informar_negado
    type: collect
    skill: exec_retorno_negado_v1        # Tier 2
    requires: [text]
    timeout_hours: 48

  - id: finalizar
    type: complete
    outcome: resolved
```

```yaml
# exec_coleta_exame_v1.yaml — Tier 2 (Execution Workflow)
steps:
  - id: identificar_paciente
    type: task
    skill: agente_identificacao_v1       # Tier 3
    context_tags:
      outputs: [journey.nome_paciente, journey.cpf]

  - id: coletar_exame
    type: task
    skill: agente_coleta_tipo_exame_v1   # Tier 3
    context_tags:
      outputs: [journey.tipo_exame, journey.convenio_id]

  - id: capturar_documentos
    type: task
    skill: agente_upload_docs_v1         # Tier 3 — reusável
    context_tags:
      outputs: [journey.documentos_ids]

  - id: confirmar
    type: task
    skill: agente_confirmacao_resumo_v1  # Tier 3

  - id: finalizar
    type: complete
    outcome: resolved
```

**Poller workflow (verifica aprovação periodicamente):**

```yaml
# skill_poller_aprovacao_saude_v1.yaml — agendado a cada hora
steps:
  - id: listar_pendentes
    type: invoke
    tool: journey_list_suspended
    args:
      pool_id: atendimento_saude
      reason: approval
    context_tags:
      outputs: [session.journeys_pendentes]

  - id: processar
    type: reason
    prompt: |
      Para cada journey em @ctx.session.journeys_pendentes,
      consulte seguradora_consultar_status com o protocolo armazenado.
      Se status != 'pendente', chame journey_resume com a decisão.
    tools: [seguradora_consultar_status, journey_resume, context_get]

  - id: finalizar
    type: complete
    outcome: resolved
```

---

### Cenário 2 — Compra de Bilhete Aéreo

**Journey como "Pending Delivery":** cliente pode retomar em qualquer canal a qualquer momento.

```yaml
# skill_compra_bilhete_v1.yaml — Tier 1 (Business Workflow)
id: skill_compra_bilhete_v1
creates_journey: true

steps:
  # Sessão de origem — qualquer canal inbound
  - id: sessao_coleta_viagem
    type: task
    pool: vendas_passagens
    skill: exec_coleta_viagem_v1         # Tier 2
    context_tags:
      outputs:
        - tag: journey.origem
        - tag: journey.destino
        - tag: journey.data_viagem
        - tag: journey.quantidade

  # Busca disponibilidade no GDS
  - id: buscar_opcoes
    type: invoke
    tool: gds_search
    args:
      origin:      "@ctx.journey.origem"
      destination: "@ctx.journey.destino"
      date:        "@ctx.journey.data_viagem"
      passengers:  "@ctx.journey.quantidade"
    context_tags:
      outputs: [journey.opcoes_voo]

  # Cliente escolhe voo e conclui pagamento — pode ser mesmo canal ou outro
  - id: sessao_escolha_pagamento
    type: collect
    skill: exec_escolha_pagamento_v1     # Tier 2
    requires: [text, masked_input]
    timeout_hours: 24
    context_tags:
      outputs: [journey.voo_escolhido, journey.payment_id]

  # Aguarda confirmação do gateway de pagamento
  - id: aguardar_pagamento
    type: suspend
    reason: webhook
    timeout_hours: 1

  - id: verificar_pagamento
    type: choice
    conditions:
      - if:   "@ctx.journey.pagamento_status == 'aprovado'"
        goto: emitir_bilhetes
      - else: goto: sessao_retry_pagamento

  # Emite bilhetes no GDS
  - id: emitir_bilhetes
    type: invoke
    tool: gds_issue_tickets
    args:
      flight:     "@ctx.journey.voo_escolhido"
      payment_id: "@ctx.journey.payment_id"
    context_tags:
      outputs: [journey.localizador, journey.bilhetes_url]

  # Entrega bilhetes — canal negociado automaticamente
  - id: sessao_entrega
    type: collect
    skill: exec_entrega_bilhetes_v1      # Tier 2
    requires: [text]
    timeout_hours: 48

  - id: finalizar
    type: complete
    outcome: resolved

  # Caminho de retry de pagamento
  - id: sessao_retry_pagamento
    type: collect
    skill: exec_pagamento_recusado_v1    # Tier 2
    requires: [text, masked_input]
    timeout_hours: 2
    goto: verificar_pagamento
```

**Journey como Pending Delivery inbound:**

Quando o cliente faz um novo contato em qualquer canal sem nenhuma sessão ativa, o Channel Gateway verifica:

```
1. Existe Journey ativa para este customer_id?
2. A Journey tem collect pendente com capacidade compatível com este canal?
3. Sim → oferecer: "Você tem uma compra de passagem em andamento. Deseja continuar?"
4. Cliente confirma → sessão vinculada à Journey → Execution Workflow do collect step inicia
```

---

## Fases de Implementação

### Fase A — Journey ContextStore Namespace ✅ implementada (2026-05-21)

**Implementado:**

- Redis hash `{tenantId}:ctx:journey:{journeyId}` com mesma estrutura do ContextStore de sessão; TTL 30 dias, renovado em cada `journey_context_set`
- Skill Flow engine (`extract-outputs.ts`): `extractOutputsToCtx` aceita `journeyId?` como 8º argumento; quando tag começa com `journey.` e `journeyId` está presente, escreve em `"journey:" + journeyId` (virtual sessionId → SDK resolve hash correto)
- Skill Flow engine (`interpolate.ts`): `resolveCtxRef` detecta prefixo `journey.` e lê do hash journey quando `ctx.journeyId` está definido
- skill-flow-worker (`engine-runner.ts`): passa `journeyId` ao `engine.run()` via spread condicional (campo já existia em `WorkflowInstance.journey_id`)
- Três step types atualizados para passar `ctx.journeyId`: `invoke.ts`, `reason.ts`, `resolve.ts`
- AI Gateway (`models.py`): `InferenceRequest.journey_id: str | None` — opcional, backward-compatible
- AI Gateway (`inference.py`): `_build_journey_context_block()` lê tags do hash journey (filtra confidence < 0.3); `_prepend_journey_context()` injeta bloco no system message; chamada em `infer()` quando `req.journey_id` está presente
- MCP tools (`mcp-server-plughub/tools/journey.ts`): `journey_context_get` (lê tags seletivas ou todas) + `journey_context_set` (enforça prefixo `journey.`, renova TTL 30 dias, `source` para auditoria); `JourneyDeps` agora inclui `redis: Redis`
- `mcp-server-plughub/server.ts`: passa `redis` em `journeyDeps`

**Nota:** `@plughub/schemas` (WorkflowEventSchema) e workflow-api (propagação de journey_id no Kafka) já estavam implementados desde Arc 10 — não foi necessário modificar.

**Pacotes modificados:** `skill-flow-engine`, `skill-flow-worker`, `ai-gateway`, `mcp-server-plughub`

---

### Fase B — Journey Public API Surface

**O que implementar:**

- `WorkflowInstance` + `Journey`: adicionar coluna `pool_id` (nullable) na Journey, preenchida na criação quando originada de um pool
- `GET /v1/journeys`: adicionar filtros `pool_id`, `status`, `reason`
- `POST /v1/journeys/{id}/resume`: recebe context dict, escreve no journey namespace, delega a `POST /v1/workflow/instances/{wf_id}/resume`
- Response de `POST /v1/journeys/{id}/resume`: inclui `next_step` para sistemas externos saberem o que aconteceu

**Pacotes afetados:** `workflow-api`, `@plughub/schemas`

---

### Fase C — MCP Tools para Journey

**O que implementar:**

- `journey_list_suspended(pool_id, reason?, limit?)` — lista Journeys suspensas de um pool
- `journey_resume(journey_id, context?)` — retoma Journey com contexto opcional
- Ambos expostos em `mcp-server-plughub`, auditados via McpInterceptor
- Permissão ABAC: `journey.resume` e `journey.read` como campos do módulo `workflows`

**Viabiliza:** padrão de poller workflow (Tier 1 que gerencia Journeys de outros processos)

**Pacotes afetados:** `mcp-server-plughub`, `auth-api` (novo campo ABAC)

---

### Fase D — Channel Capability Negotiation *(implementada 2026-05-21)*

**O que foi implementado:**

- `collect` step: campo `requires: string[]` (opcional); campo `channel` torna-se opcional — ambos em `@plughub/schemas` `CollectStepSchema`
- `channel-gateway/src/plughub_channel_gateway/channel_capability_registry.py`: `CHANNEL_CAPABILITIES` static dict; `channel_satisfies(channel, requires)` helper; `write_journey_channel_context()` escreve `journey.available_channels`, `journey.canal_preferido` e contatos do cliente no journey namespace do ContextStore; `write_journey_pending_collect()` escreve `journey.pending_collect_info` (lida pela MCP tool `journey_check_pending`); `write_journey_channel_context()` limpa `journey.pending_collect_info` quando a resposta chega
- `channel-gateway/src/plughub_channel_gateway/main.py`: `_dispatch_collect_event()` — seleciona canal por capacidade quando `requires[]` presente e `channel` ausente; chama `write_journey_pending_collect()` após cada dispatch; todos os adapters inbound chamam `write_journey_channel_context()` na chegada de collect reply
- Todos os adapters inbound (whatsapp, sms, email, webchat) verificam `channel:{ch}:{contact_id}:pending_collect` Redis key e correlacionam resposta com collect token

**Pacotes afetados:** `channel-gateway`, `skill-flow-engine`, `skill-flow-worker`, `@plughub/schemas`

---

### Fase E — Inbound Journey Resume via Agente de Pool *(implementada 2026-05-21)*

**Princípio:** Channel Gateway e Routing Engine não sabem nada sobre journeys. O roteamento inbound funciona exatamente como hoje — o agente do pool é quem detecta e oferece a retomada, como parte natural da conversa.

**Fluxo:**

```
Inbound chega
→ Channel Gateway → Routing Engine → pool configurado para o canal (sem mudança)

Agente AI do pool abre sessão:
  1. Chama journey_check_pending(customer_id) — MCP tool implementada
  2. Journey pendente compatível com canal atual?
     Sim → faz oferta na conversa ("você tem um processo em andamento, quer continuar?")
     Não → atendimento normal
  3. Cliente aceita → journey_link_session + journey_resume
  4. Tier 2 do collect step roda dentro da mesma sessão ativa
  5. Após Tier 2 completar: agente permanece na sessão → pode verificar necessidades adicionais
  6. Cliente recusa: atendimento normal, sessão segue normalmente

Sem recursos no pool → fila como hoje:
  → Agente de fila AI recebe o cliente enquanto aguarda
  → Mesmo agente pode chamar journey_check_pending e fazer a oferta
  → Se aceita: resolve durante a espera, cliente pode desligar satisfeito
  → Se recusa: permanece na fila normalmente
```

**Vantagens sobre interception em infraestrutura:**
- Channel Gateway e Routing Engine permanecem sem conhecimento de journeys
- O agente já está na sessão → após journey completar, verifica necessidades adicionais naturalmente (sem lógica extra de "retorno ao agente")
- Agente de fila pode também oferecer retomada — reduz abandono na fila

**O que foi implementado:**

- MCP tool `journey_check_pending(customer_id, channel?, limit?)` em `mcp-server-plughub/src/tools/journey.ts`:
  - Consulta `GET /v1/journeys?customer_id=...&status=active` no workflow-api
  - Lê `journey.pending_collect_info` do journey ContextStore Redis hash para cada journey
  - Parâmetro `channel` opcional: filtra journeys cujo `requires[]` é compatível com o canal atual (via `_CHANNEL_CAPABILITIES` mirror em TypeScript)
  - Retorna `{ customer_id, channel, pending_count, pending_journeys: [...] }`
  - Auditada via McpInterceptor
- Config de pool: `inbound_journey_resume: boolean` (default `false` — opt-in informacional)
  - Campo adicionado a `PoolRegistrationSchema` em `@plughub/schemas`
  - Coluna adicionada ao modelo Pool em Prisma + migration SQL
  - CRUD em `agent-registry/src/routes/pools.ts`
  - Toggle UI em `PoolsPage.tsx` com i18n en + pt-BR
  - **Não lido** pelo Routing Engine ou Channel Gateway — é sinal para o autor da skill YAML
- `journey_link_session` e `journey_resume` já existiam — usados pelo agente ao confirmar retomada

**Pacotes afetados:** `mcp-server-plughub` (nova tool `journey_check_pending`), `agent-registry` (campo `inbound_journey_resume` no pool), `platform-ui` (toggle na config de pool)

---

## Impacto nos Componentes Existentes

| Componente | Mudança |
|---|---|
| `skill-flow-engine` | Resolver `@ctx.journey.*`; redirecionar writes com prefixo `journey.*` |
| `workflow-api` | Coluna `pool_id` em Journey; endpoints resume/list públicos |
| `skill-flow-worker` | Propagar `journey_id` para o engine em cada step |
| `channel-gateway` | Seleção de canal por capacidade; escribir available_channels no journey namespace |
| `mcp-server-plughub` | Dois novos tools: `journey_list_suspended`, `journey_resume` |
| `@plughub/schemas` | `CollectStepSchema`: `requires[]`, `channel` opcional; `JourneySchema`: `pool_id` |
| `platform-ui` | Config de pool: `inbound_journey_resume` toggle (Fase E); sem mudança em Channel Gateway ou Routing Engine |

## Decisões Arquiteturais (2026-05-21)

| # | Questão | Decisão |
|---|---|---|
| 1 | Quem escreve em `journey_sessions`? | `workflow-api` consome `collect.events` Kafka diretamente — sem REST cross-service |
| 2 | `journey_id` no schema de `workflow.events`? | Sim — campo nullable adicionado ao `WorkflowEventSchema`; instâncias sem journey não quebram |
| 3 | Tier 3 pode ler `@ctx.journey.*`? | Sim — AI Gateway inclui namespace journey na construção de contexto quando sessão tem `journey_id`. Agentes leem, nunca escrevem diretamente no namespace journey |
| 4 | Journey precisa de `pool_id`? | Sim — `pool_id` mantido. Dois motivos: (1) estabilidade de versão: `skill_id` muda a cada upgrade, `pool_id` é invariante; (2) access control: JWT já carrega `accessible_pools[]`, não `accessible_skills[]`. `journey_list_suspended` resolve internamente `pool_id → skill_ids ativos` |
| 5 | Timeout de `collect` — qual status da Journey? | `collect_expired` event → catch step decide; se sem catch: `journey_failed` com `failure_reason: collect_timeout`. Nenhum status `expired` — enum de status permanece mínimo |

---

## Invariantes do Modelo

- **Tier 1 nunca conhece o canal** — toda menção a canal específico em Business Workflow é anti-padrão
- **Tier 3 pode LER `@ctx.journey.*`, nunca ESCREVER** — AI Gateway inclui namespace journey no contexto do agente; escrita só via `context_tags.outputs` do step Tier 2 que invoca o agente
- **`context_set` de Tier 3 sempre escreve no namespace session** — prefixo `journey.*` em `context_tags` é privilégio exclusivo de steps de Tier 1 e Tier 2
- **collect sem requires é válido** — Channel Gateway usa `journey.canal_preferido` como único critério
- **journey_resume sempre passa pelo McpInterceptor** — nunca chamado direto ao workflow-api sem auditoria
- **`failure_reason` é a única distinção de causa terminal** — enum `status` não cresce com `expired`; use `failure_reason: collect_timeout | suspend_timeout | workflow_error`
