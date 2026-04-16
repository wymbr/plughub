# Conferência com Agente IA — Mapeamento de Gaps para Teste

> Spec de referência: PlugHub v24.0 seção 3.2a (Supervisor / Agent Assist), 4.6k (external-mcp)  
> Cenário: agente humano convida agente externo-mcp para conferência usando `@pool_id:{params}`  
> Data: 2026-04-16

---

## Cenário-alvo

```
Cliente (wscat/webchat)
    ↕  conversa normal
Agente humano (Agent Assist UI + mcp-server)
    → digita @agente_autenticacao_v1:{skill:autenticacao}
    → Agent Assist chama agent_join_conference
    → Agente IA externo entra na conferência
    → Agente IA interage diretamente com o cliente (visível com label "Assistente")
    → Agente humano vê tudo, pode intervir
    → Agente IA chama agent_done
    → Agente humano retoma atendimento
```

---

## O que já existe e funciona

| # | Componente | O que faz | Arquivo |
|---|---|---|---|
| ✅ 1 | `agent_join_conference` tool | Publica `conversations.inbound` com `conference_id` e `agent_type_id` hard constraint | `mcp-server-plughub/src/tools/supervisor.ts:289` |
| ✅ 2 | Routing Engine | Filtra instâncias por `agent_type_id` quando `conference_id` presente | `routing-engine/router.py:152` (`if event.agent_type_id and inst.agent_type_id != event.agent_type_id: continue`) |
| ✅ 3 | Routing Engine | Propaga `conference_id` para `RoutingResult` → `ConversationRoutedEvent` | `routing-engine/router.py:185`, `models.py:190` |
| ✅ 4 | Bridge | Extrai `conference_id` do `ConversationRoutedEvent` antes de ativar agente | `orchestrator-bridge/main.py:686` |
| ✅ 5 | Bridge (`process_inbound`) | Fan-out simultâneo: humano via `agent:events:{id}` + IA via `menu:result:{id}` — ambos independentes | `orchestrator-bridge/main.py:1138–1189` |
| ✅ 6 | Bridge | Publica `conference.agent_completed` em `agent:events:{id}` quando **agente nativo** conclui | `orchestrator-bridge/main.py:743–764` |
| ✅ 7 | Schema | `conference_id` e `participant_id` no schema do `context_package` | `schemas/src/context-package.ts:245` |
| ✅ 8 | `supervisor_state` | Retorna estado da conversa para Agent Assist | `mcp-server-plughub/src/tools/supervisor.ts:62` |
| ✅ 9 | `supervisor_capabilities` | Lista agentes disponíveis e `interaction_model` por intent | `mcp-server-plughub/src/tools/supervisor.ts:169` |

---

## Gaps — o que falta criar

### Gap 1 — `context_package` não inclui `conference_id` para agente externo-mcp

**Onde:** `orchestrator-bridge/main.py` → `activate_external_mcp_agent()` (linha ~466)

**Problema:** O `context_package` enviado ao agente externo via LPUSH não inclui `conference_id` nem `channel_identity`. O agente não tem como saber que está em modo conferência — ele trata a sessão como atendimento normal.

**O que criar:**
```python
# activate_external_mcp_agent — adicionar ao context_package:
context_package = {
    ...
    "conference_id":    routing_result.get("conference_id"),     # None se não é conferência
    "channel_identity": routing_result.get("channel_identity"),  # {"text": "Assistente", ...}
    "participant_id":   str(uuid.uuid4()),                       # gerado pelo bridge
    "is_conference":    bool(routing_result.get("conference_id")),
}
```

`channel_identity` precisa vir do `ConversationRoutedEvent` (ou do `InboundEvent` original). O `agent_join_conference` já inclui `channel_identity` no payload Kafka; o Routing Engine precisa propagá-lo junto com `conference_id`.

**Escopo:** bridge + routing-engine (adicionar `channel_identity` ao `ConversationInboundEvent` e ao `RoutingResult`)

---

### Gap 2 — `channel_identity` não persiste no Redis

**Onde:** `mcp-server-plughub/src/tools/supervisor.ts` → `agent_join_conference`

**Problema:** O `channel_identity` declarado pelo Agent Assist é passado na tool, publicado no Kafka, mas não gravado em Redis. Quando o Channel Gateway precisa rotular a mensagem do agente IA ao entregar ao cliente, não encontra o label em lugar nenhum.

**O que criar:**
```typescript
// agent_join_conference — após gerar conference_id:
await redis.set(
  `conference:${conference_id}:identity`,
  JSON.stringify(parsed.channel_identity ?? { text: "Assistente" }),
  "EX", 14400,  // 4h
)
// E por instance_id para lookup rápido no outbound:
await redis.set(
  `conference:identity:${instance_id}`,  // instance_id vem do RoutingResult — problema: não está disponível aqui ainda
  ...
)
```

> **Alternativa mais simples:** armazenar por `conference_id` (disponível no momento da tool call) e o Channel Gateway faz lookup via `conference_id` presente no evento Kafka de saída.

**Escopo:** `mcp-server-plughub/supervisor.ts` (pequena adição) + Redis key nova

---

### Gap 3 — Channel Gateway não rotula mensagens de agentes IA em conferência

**Onde:** `channel-gateway/src/plughub_channel_gateway/outbound_consumer.py`

**Problema:** Quando o agente IA chama `send_message`, publica em `conversations.outbound` com `author: { type: "agent_ai", id: instance_id }`. O Channel Gateway entrega a mensagem ao cliente mas não inclui o label "Assistente" (ou o `channel_identity.text` declarado). O cliente não sabe quem está falando.

**O que criar:**
```python
# outbound_consumer.py — ao processar mensagem outbound:
# 1. Se author.type == "agent_ai": verificar se há conference_id no payload
# 2. Se sim: buscar Redis conference:{conference_id}:identity
# 3. Prefixar mensagem com identity.text ou incluir no envelope do canal
#    ex: "[Assistente] Olá, vou ajudar com a autenticação."

conference_id = msg.get("conference_id")
if conference_id:
    raw = await redis.get(f"conference:{conference_id}:identity")
    if raw:
        identity = json.loads(raw)
        text = f"[{identity.get('text', 'IA')}] {msg['content']['text']}"
```

Para isso funcionar, `send_message` também precisa incluir `conference_id` no evento Kafka. O agente externo tem `conference_id` no `context_package` — basta passá-lo no `send_message`.

**Escopo:** `channel-gateway/outbound_consumer.py` + `mcp-server-plughub/external-agent.ts` (adicionar `conference_id` opcional ao `send_message`)

---

### Gap 4 — Agente humano não vê as mensagens enviadas pelo agente IA em conferência

**Onde:** `orchestrator-bridge/main.py` OU `channel-gateway/outbound_consumer.py`

**Problema:** O agente IA chama `send_message` → `conversations.outbound` → Channel Gateway → cliente. O agente humano não recebe cópia da mensagem. Para "ver tudo e poder intervir a qualquer momento" (spec 3.2a), o humano precisa ver as mensagens do parceiro IA em seu painel.

**O que criar:**
```python
# outbound_consumer.py — ao entregar mensagem de conference agent ao cliente:
# Adicionalmente, espelhar ao agent:events:{session_id}:
if conference_id:
    mirror_event = {
        "type":          "conference.agent_message",
        "session_id":    session_id,
        "conference_id": conference_id,
        "agent_type_id": msg.get("agent_type_id"),
        "text":          msg["content"]["text"],
        "timestamp":     msg["timestamp"],
        "identity":      identity,
    }
    await redis.publish(f"agent:events:{session_id}", json.dumps(mirror_event))
```

O Agent Assist UI (que já escuta `agent:events:{session_id}`) precisa lidar com o tipo `conference.agent_message` e exibir no painel de mensagens.

**Escopo:** `channel-gateway/outbound_consumer.py` + Agent Assist UI (handler de `conference.agent_message`)

---

### Gap 5 — `conference.agent_completed` não é publicado para agentes externo-mcp

**Onde:** `orchestrator-bridge/main.py`

**Problema:** O bridge publica `conference.agent_completed` em `agent:events:{session_id}` apenas para agentes nativos (linha 743). Agentes externo-mcp chamam `agent_done` via `runtime.ts` diretamente — o bridge não tem hook para isso.

**O que criar:**
```python
# Opção A: bridge consome agent.lifecycle events (agent_done) e, se conference_id
# presente na instância, publica conference.agent_completed.

# Opção B: runtime.ts publica conference.agent_completed em conversations.events
# quando agent_done é chamado com um conference_id no contexto da sessão.
```

Opção B é mais simples: `agent_done` em `runtime.ts` já tem acesso ao `session_token` e pode verificar `conference_id` no Redis antes de encerrar.

**Escopo:** `mcp-server-plughub/src/tools/runtime.ts` (agent_done) + Redis lookup de `conference_id`

---

### Gap 6 — Agente externo-mcp de teste não está preparado para modo conferência

**Onde:** `packages/mcp-server-plughub/test-conference-agent.mjs` (a criar)

**Problema:** O `test-external-agent.mjs` existente trata toda sessão como atendimento autônomo. Para testar conferência, precisamos de um agente que:
- Detecta `conference_id` no `context_package`
- Age como especialista convidado (não como atendente principal)
- Envia mensagens com `conference_id` no `send_message`
- Chama `agent_done` sem fechar a sessão do cliente (apenas encerra sua participação)
- Não chama `agent_ready` com continuidade — retorna ao pool após `agent_done`

**O que criar:** `test-conference-agent.mjs` — agent externo especializado que:
```javascript
// runCycle():
const { context_package } = await wait_for_assignment(...)
const { session_id, conference_id, channel_identity } = context_package

if (conference_id) {
    // modo conferência — especialista convidado
    await send_message({ text: "Olá, sou o assistente de autenticação. Como posso ajudar?", conference_id })
    const { message } = await wait_for_message({ session_id, timeout_s: 120 })
    // ... processar tarefa especializada ...
    await send_message({ text: "Autenticação concluída com sucesso.", conference_id })
    await agent_done({ outcome: "resolved", issue_status: [...] })
    // NÃO fecha a sessão do cliente — apenas encerra a participação do IA
}
```

---

### Gap 7 — `@mention` syntax não está implementada

**Onde:** Agent Assist UI (front-end) OU `orchestrator-bridge/main.py`

**Problema:** O agente humano precisa de uma UX para convidar o agente IA. A sugestão `@pool_id:{param1:aa}` precisa ser detectada e convertida em chamada `agent_join_conference`.

**Duas opções:**

**Opção A — Front-end (Agent Assist UI) — recomendada**
```
Agente humano digita: @agente_autenticacao_v1:{skill:autenticacao}
→ Agent Assist UI detecta padrão @poolId:{...} no input
→ Chama agent_join_conference({ session_id, agent_type_id: "agente_autenticacao_v1", ... })
→ Remove o @mention do texto antes de enviar ao cliente
```
**Vantagem:** não polui o canal do cliente; lógica de UX no front-end onde pertence.

**Opção B — Bridge parseia mensagem do agente humano**
```
Agente humano digita no canal: "@agente_autenticacao_v1 pode ajudar?"
→ bridge detecta regex /@([a-z_0-9]+)(?:\{([^}]*)\})?/ na mensagem outbound do humano
→ Extrai agent_type_id + params
→ Chama agent_join_conference internamente
→ Entrega mensagem sem o @mention ao cliente
```
**Desvantagem:** lógica de apresentação no bridge — viola separação de responsabilidades.

**Para o teste:** chamar `agent_join_conference` diretamente via MCP client — não precisamos do `@mention` para validar o fluxo completo de conferência.

---

## Resumo de itens a criar

| # | Arquivo | Mudança | Tamanho |
|---|---|---|---|
| 1a | `routing-engine/src/plughub_routing/models.py` | Adicionar `channel_identity: dict | None` a `ConversationInboundEvent` e `RoutingResult` | Pequena |
| 1b | `orchestrator-bridge/main.py` → `activate_external_mcp_agent` | Incluir `conference_id`, `channel_identity`, `participant_id`, `is_conference` no `context_package` | Pequena |
| 2 | `mcp-server-plughub/src/tools/supervisor.ts` → `agent_join_conference` | Gravar `conference:{conference_id}:identity` no Redis com TTL 4h | Pequena |
| 3a | `mcp-server-plughub/src/tools/external-agent.ts` → `send_message` | Adicionar `conference_id?: string` ao schema de input; incluir no evento Kafka | Pequena |
| 3b | `channel-gateway/outbound_consumer.py` | Ler `conference:{conference_id}:identity` do Redis e rotular mensagem ao cliente | Média |
| 4 | `channel-gateway/outbound_consumer.py` | Espelhar mensagens de conference agent em `agent:events:{session_id}` via Redis pub/sub | Média |
| 5 | `mcp-server-plughub/src/tools/runtime.ts` → `agent_done` | Verificar `conference_id` na sessão e publicar `conference.agent_completed` em `agent:events:{session_id}` | Pequena |
| 6 | `packages/mcp-server-plughub/test-conference-agent.mjs` | Agente externo-mcp de teste para modo conferência | Médio (novo arquivo) |
| 7 | `packages/mcp-server-plughub/test-trigger-conference.mjs` | Script que conecta ao MCP e chama `agent_join_conference` para disparar o teste | Pequeno (novo arquivo) |

---

## Ordem de implementação sugerida

```
Sprint 1 — infraestrutura de conferência (sem UI)
  1. Gap 1 (models + bridge) — context_package correto para externo-mcp
  2. Gap 2 (supervisor.ts)   — gravar channel_identity no Redis
  3. Gap 6 (test-conference-agent.mjs) — agente de teste
  4. Gap 7 (test-trigger-conference.mjs) — script de disparo
  → Objetivo: fluxo end-to-end funciona sem label e sem mirror

Sprint 2 — qualidade de conferência
  5. Gap 3 (send_message + outbound_consumer) — label no canal do cliente
  6. Gap 4 (outbound_consumer mirror)         — humano vê mensagens do IA
  7. Gap 5 (runtime.ts agent_done)            — conference.agent_completed para externo-mcp
  → Objetivo: experiência completa segundo spec 3.2a

Sprint 3 — UX de acionamento (opcional para piloto)
  8. Gap 7 Opção A — @mention parser no Agent Assist UI
```

---

## Diagrama de fluxo alvo (após todos os gaps resolvidos)

```
Agente Humano (MCP client / Agent Assist UI)
  │
  ├── digita @agente_autenticacao_v1 → UI chama agent_join_conference
  │     └── supervisor.ts: publica conversations.inbound{conference_id}
  │                         grava conference:{id}:identity no Redis
  │
Routing Engine
  ├── recebe inbound com conference_id
  ├── filtra por agent_type_id hard constraint
  └── aloca instância → publica conversations.routed{conference_id, channel_identity}

Bridge (orchestrator-bridge)
  ├── recebe conversations.routed
  ├── activa_external_mcp_agent(context_package{conference_id, channel_identity, ...})
  └── retorna — agente gerencia seu próprio ciclo

Agente IA Externo (test-conference-agent.mjs)
  ├── desbloqueado do BLPOP com context_package
  ├── detecta is_conference=true
  ├── chama send_message{text, conference_id}
  │     └── external-agent.ts: publica conversations.outbound{conference_id, ...}
  │
Channel Gateway (outbound_consumer)
  ├── recebe conversations.outbound
  ├── lê conference:{id}:identity → "[Assistente]"
  ├── entrega "[Assistente] Olá, posso ajudar com..." ao cliente
  └── espelha conference.agent_message em agent:events:{session_id}  ← humano vê

Bridge (process_inbound)
  ├── cliente responde → inbound event
  ├── is_human=True  → publish agent:events:{session_id}   (humano recebe)
  └── menu_waiting   → lpush menu:result:{session_id}      (IA recebe)

Agente IA Externo
  ├── wait_for_message desbloqueia com resposta do cliente
  ├── processa tarefa especializada
  ├── chama send_message com resultado
  └── chama agent_done
        └── runtime.ts: verifica conference_id, publica conference.agent_completed
            Bridge: humano notificado → retoma atendimento
```

---

## Referências

- `packages/orchestrator-bridge/src/plughub_orchestrator_bridge/main.py`
  - `activate_external_mcp_agent` linha ~441
  - `process_inbound` linha ~1095
  - `process_routed` linha ~530
- `packages/mcp-server-plughub/src/tools/supervisor.ts`
  - `agent_join_conference` linha ~289
- `packages/mcp-server-plughub/src/tools/external-agent.ts`
  - `send_message` linha ~274
  - `wait_for_message` linha ~312
- `packages/routing-engine/src/plughub_routing/router.py`
  - `_allocate` linha ~152 (conference hard constraint)
- `packages/channel-gateway/src/plughub_channel_gateway/outbound_consumer.py`
- Spec v24.0 seção 3.2a — Conferência, Agent Assist, channel_identity
- Spec v24.0 seção 4.6k — external-mcp framework
