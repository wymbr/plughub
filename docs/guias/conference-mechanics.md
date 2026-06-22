# Conference Mechanics — Guia Técnico de Referência

> Última atualização: 2026-05-25 · Estado: Arc 16

> **Regra de manutenção**: qualquer mudança no mecanismo de conferência (lifecycle, Redis keys,
> eventos Kafka/pub-sub, lógica de posatt, filtros no mcp-server ou regras no platform-ui)
> **deve atualizar este documento antes de ser considerada concluída**. Registre a mudança
> também em `CHANGELOG.md` e adicione uma entrada em [§ Histórico de Problemas e Correções](#histórico-de-problemas-e-correções).

---

## 1. Visão Geral

Uma **conferência** (conference room) é o espaço de colaboração que existe enquanto um ou mais
agentes estão atendendo uma sessão. É separada da sessão em si — a sessão pode continuar ativa
(para fins de posatt) mesmo depois que o cliente encerrou o contato.

**Analogia**: a sessão é o processo de negócio; a conferência é a sala física onde todos
se encontram. A sala pode continuar aberta durante a pós-venda (wrap-up, NPS) mesmo depois
que o cliente saiu.

### Quando uma conferência é criada

A conferência é **sempre iniciada por um `conversations.inbound` no Kafka**. O que varia é a origem desse evento:

| Origem | Produtor do `conversations.inbound` | Documentação de detalhe |
|---|---|---|
| Contato inbound (cliente chega) | Channel Gateway | Este documento |
| Hook de posatt (`on_human_end`, `post_human`) | Bridge — `fire_pool_hooks()` | Este documento — §6 |
| A2A assist / specialist invite | Routing Engine (re-publica) | `docs/arcos/arc11-console-orchestration.md` |
| `collect` step de workflow (outbound) | workflow-api | `docs/arcos/arc4-workflow.md` |
| Webhook externo → trigger de workflow | workflow-api (via resume) | `docs/arcos/arc4-workflow.md` |
| Agendamento via calendar-api | skill-flow-worker (step `suspend` + timer) | `docs/arcos/arc4-workflow.md` |
| `journey_start` com `creates_journey: true` | skill-flow-worker (step automático) | `docs/arcos/arc10-journey.md` |

> **Importante:** o comportamento interno da conferência a partir do `conversations.inbound` é **idêntico em todos esses casos** — mesmos Redis keys, mesmo ciclo de vida, mesmo mecanismo de posatt. Este documento cobre essa mecânica comum. Os mecanismos de disparo específicos de cada origem estão nos docs linkados acima.

---

## 2. Ciclo de Vida — Três Camadas

O modelo correto tem **três camadas independentes** que não devem ser colapsadas:

```
Layer 1 — Contato (perspectiva do cliente)
  Eventos: WS do cliente fecha, AHT congelado no momento de saída do cliente.
  Trigger: _close_contact_layer()

Layer 2 — Segmento do agente (janela de participação de cada participante)
  Eventos: pool libera recurso (agent_done), métricas de segmento gravadas.
  Trigger: process_routed() via agent_done → remove_conversation()

Layer 3 — Infraestrutura da conferência (a sala)
  Eventos: human_agent keys deletadas, session.closed broadcast para todos Consoles.
  Trigger: _destroy_conference()
```

### Condições de disparo por camada

| Camada | Sem hooks (path simples) | Com hooks on_human_end |
|---|---|---|
| Layer 1 | `_trigger_contact_close()` imediato na saída do agente humano | `_close_contact_layer()` imediato se não há hook customer-side; senão, aguarda `posatt:customer_active == 0` |
| Layer 2 | `agent_done` na conclusão do agente | `agent_done` na conclusão de cada segmento de hook |
| Layer 3 | `_trigger_contact_close()` (chama `_destroy_conference()`) | `_destroy_conference()` quando `posatt:active == 0` |

### Estados de participantes e roles

| Role | Descrição |
|---|---|
| `primary` | Agente principal responsável pela interação |
| `specialist` | Especialista convidado (assist mode, hook agent) |
| `supervisor` | Supervisor humano ou AI monitorando |
| `evaluator` | Agente de qualidade |

---

## 3. Redis Keys — Referência Completa

### 3.1 Keys de tracking de conferência

| Key | TTL | Escrito por | Lido por | Propósito |
|---|---|---|---|---|
| `session:{id}:meta` | Sessão TTL (padrão 4h) | Core / Bridge (activate) | Bridge (fire_pool_hooks, _close_contact_layer) | JSON com pool_id, channel, started_at, customer_id, tenant_id |
| `session:{id}:human_agent` | Sessão TTL | Bridge (activate_human_agent) | Bridge (process_routed, hooks), mcp-server (notify/menu routing) | participant_id do agente humano atual |
| `session:{id}:human_agents` | Sessão TTL | Bridge (activate_human_agent) | mcp-server (scard para detect specialist role) | SET com participant_ids de todos agentes humanos ativos |
| `session:{id}:contact_ended_at` | 4h | Bridge (_mark_contact_ended) | Bridge (_close_contact_layer — G1 fix AHT) | ISO timestamp do momento exato que o cliente saiu |
| `session:{id}:contact_close_fired` | 7 dias | `_close_contact_layer()` (NX) | Idempotência de `_close_contact_layer()` | Evita duplo disparo de Layer 1 |
| `session:{id}:close_fired` | 7 dias | `_destroy_conference()` (NX) | Idempotência de `_destroy_conference()` | Evita duplo disparo de Layer 3 |
| `session:{id}:closed` | 7 dias | mcp-server `/agent_done` handler | mcp-server (reconnect guard para pending_assignment) | Marca sessão como encerrada para redelivery guard |

### 3.2 Keys de posatt (pós-atendimento)

| Key | TTL | Escrito por | Lido por | Propósito |
|---|---|---|---|---|
| `session:{id}:posatt:active` | 4h | `fire_pool_hooks()` — INCR por segmento | `process_routed()` — DECR na conclusão; `_destroy_conference()` — leitura de guarda | Contador de segmentos posatt ainda em execução. Quando chega a 0, `_destroy_conference()` pode prosseguir |
| `session:{id}:posatt:customer_active` | 4h | `fire_pool_hooks()` — INCR só para `side=customer` | `process_routed()` — DECR ao finalizar hook customer-side | Contador de hooks do lado cliente (NPS). Quando chega a 0, `_close_contact_layer()` é disparado |
| `session:{id}:hook_pending:{hook_type}` | 4h | `fire_pool_hooks()` — SET com count de hooks | `process_routed()` — DECR na conclusão | Contador por tipo de hook (`on_human_end`, `post_human`). Controla quando disparar o próximo tipo |
| `session:{id}:hook_conf:{conference_id}` | 4h | `fire_pool_hooks()` — SET com `"{hook_type}:{pool}:{side}"` | `process_routed()` — GETDEL para identificar segmento hook ao concluir | Liga um conference_id ao tipo de hook, pool de origem e lado (agent/customer) |
| `session:{id}:posatt:{conference_id}:participants` | 4h | `fire_pool_hooks()` — SADD do participante fixo-side; `process_routed()` — SADD do instance_id do hook agent quando ele se conecta | `process_routed()` — SMEMBERS para construir `recipients[]` do `posatt_segment_complete` | SET de participant_ids que devem receber o `session.closed` daquele segmento específico |

### 3.3 Keys de instâncias e pools

| Key | TTL | Escrito por | Lido por | Propósito |
|---|---|---|---|---|
| `pool:pending_assignment:{poolId}` | 5min (300s) | Bridge (activate_human_agent) | mcp-server (reconnect delivery) | Persiste assignment para reentrega após refresh do Console |
| `{tenant}:instance:{participant_id}:wrap_up_pending` | TTL hook + 5min | `fire_pool_hooks()` — agent-side hooks | Routing Engine (`get_ready_instances`) | Bloqueia novo contato ao agente durante o wrap-up |
| `{tenant}:session:pool:{session_id}` | Sessão TTL | Routing Engine (mark_busy) | Routing Engine (GETSET para chain DECR entre pools) | Tracking de qual pool está servindo a sessão (para DECRs corretos em transferências) |
| `{tenant}:routing:instance:{instance_id}:meta` | Sessão TTL | Bridge (process_routed) | Routing Engine (get_instance_meta para DECR) | Hash com pool_id do agente — necessário para routing engine decrementar corretamente |
| `session:{id}:customer_participant_id` | 4h | Bridge (ContextStore write pré-hooks) | `fire_pool_hooks()` — SADD no participants SET (side=customer) | participant_id do cliente, lido para construir recipients do NPS |

### 3.4 Keys de ContextStore usadas pelo mecanismo de conferência

Namespace: `{tenant_id}:ctx:{session_id}` (Redis Hash)

| Campo do hash | Escrito por | Usado por | Valor |
|---|---|---|---|
| `session.close_origin` | Bridge (pré-hooks) | `fire_pool_hooks()` (nps_on_disconnect check) | `"agent_closed"` ou `"customer_disconnect"` |
| `session.customer_participant_id` | Bridge (pré-hooks) | `fire_pool_hooks()` | participant_id do cliente |
| `session.human_agent_participant_id` | Bridge (pré-hooks) | `fire_pool_hooks()` (side=agent participants SET) | participant_id do agente humano |
| `session.pool.id` | Routing Engine (Pool Context Enrichment) | Agentes (contexto de pool) | pool_id alocado |

---

## 4. Eventos Kafka e Redis Pub/Sub

### 4.1 Tópicos Kafka relevantes para conferência

| Tópico | Produtor | Consumidor(es) | Evento-chave |
|---|---|---|---|
| `conversations.inbound` | Channel Gateway (contato real); Bridge (hook sintético) | Routing Engine | Inicia routing de um contato ou hook |
| `conversations.routed` | Routing Engine | Core, Rules Engine | Agente alocado com sucesso |
| `conversations.events` | Bridge | analytics-api | `contact_closed` com AHT correto |
| `conversations.outbound` | Bridge | Channel Gateway | `session.closed` → fecha WS do cliente |
| `agent.lifecycle` | mcp-server (agent_done REST) | Routing Engine | `agent_done`, `agent_ready` |
| `conversations.participants` | Bridge | analytics-api → ClickHouse | `participant_joined`, `participant_left` |

### 4.2 Canal Redis Pub/Sub

O mecanismo de entrega de eventos para o Console usa Redis pub/sub (não Kafka):

| Canal | Publicado por | Subscrito por | Uso |
|---|---|---|---|
| `agent:events:{session_id}` | Bridge (`_destroy_conference`, `process_routed`) | mcp-server (subscriber por conexão WS) | Eventos de ciclo de vida de sessão para o Console |
| `pool:events:{pool_id}` | Routing Engine (via `conversations.routed`) | mcp-server (subscriber por conexão WS) | `conversation.assigned` — novo contato chegou ao agente |

### 4.3 Estrutura dos eventos pub/sub chave

#### `conversation.assigned`
```json
{
  "type": "conversation.assigned",
  "session_id": "ses_abc123",
  "pool_id": "retencao_humano",
  "instance_id": "human-retencao_humano",
  "participant_id": "human-retencao_humano",
  "tenant_id": "tenant_001",
  "contact_id": "cust_xyz"
}
```
*Publicado em:* `pool:events:{pool_id}`

#### `session.closed` — reason: `posatt_segment_complete`
```json
{
  "type": "session.closed",
  "session_id": "ses_abc123",
  "reason": "posatt_segment_complete",
  "recipients": ["human-retencao_humano", "wrapup_agent_v1-001"]
}
```
*Publicado em:* `agent:events:{session_id}`
*Efeito:* somente os IDs em `recipients` recebem o teardown. NPS não inclui o agente humano.

#### `session.closed` — reason: `conference_destroyed`
```json
{
  "type": "session.closed",
  "session_id": "ses_abc123",
  "reason": "conference_destroyed"
}
```
*Publicado em:* `agent:events:{session_id}`
*Efeito:* broadcast — todos os Consoles conectados à sessão fazem teardown.

#### `session.closed` — reason: `agent_done` (legado)
```json
{
  "type": "session.closed",
  "session_id": "ses_abc123",
  "reason": "agent_done"
}
```
*Publicado pelo:* `_trigger_contact_close()` — path sem hooks.
*Efeito:* broadcast — todos os Consoles fazem teardown imediatamente.

---

## 5. Tratamento por Módulo

### 5.1 Routing Engine

**Responsabilidade:** alocar agentes, publicar `conversations.routed`, gerenciar counters de instância.

**Ações relevantes para conferência:**
- Recebe `conversations.inbound` (real ou sintético de hook).
- Consulta instâncias disponíveis no pool alvo.
- `mark_busy()`: escreve `{tenant}:session:pool:{session_id}` (GETSET — chain DECR entre pools).
- Publica `conversations.routed` com `session_id`, `pool_id`, `instance_id`, `conference_id`.
- `remove_conversation()`: lê `{tenant}:routing:instance:{id}:meta`, DECR contador de ocupação.
- `get_ready_instances()`: verifica `{tenant}:instance:{pid}:wrap_up_pending` — exclui instâncias em wrap-up.
- Publica `pool:events:{pool_id}` com o `conversation.assigned` para o Console.

**Regra:** o Routing Engine nunca lê `posatt:active` nem conhece o conceito de Layer 3. Sua única responsabilidade é alocar e liberar instâncias.

### 5.2 Orchestrator Bridge

**Responsabilidade:** controller central — gerencia o ciclo de vida da conferência, dispara hooks, controla layers 1 e 3.

**Funções críticas:**

| Função | Propósito |
|---|---|
| `activate_human_agent()` | Registra agente humano no Redis, escreve `pool:pending_assignment`, publica `agent_ready` |
| `process_contact_event()` | Handler de `agent_done` — verifica hooks, dispara `fire_pool_hooks("on_human_end")` ou `_trigger_contact_close()` |
| `fire_pool_hooks(hook_type)` | Publica `conversations.inbound` sintético por hook, INCR `posatt:active`, INCR `posatt:customer_active` (side=customer), seta `hook_conf`, inicializa `participants SET` |
| `process_routed()` | Handler de `conversations.routed` — detecta conclusão de hook via `GETDEL hook_conf`, DECR `posatt:active`, publica `posatt_segment_complete`, chama `_destroy_conference()` quando counter chega a 0 |
| `_mark_contact_ended()` | Grava `session:{id}:contact_ended_at` com timestamp exato (usado para AHT correto — G1 fix) |
| `_close_contact_layer()` | Layer 1: publica `conversations.outbound session.closed` (fecha WS cliente) + `conversations.events contact_closed` (analytics). Idempotente via `contact_close_fired` NX |
| `_destroy_conference()` | Layer 3: verifica `posatt:active > 0` (retorna se ainda há hooks), adquire `close_fired` NX, deleta `human_agent`/`human_agents`, publica `conference_destroyed` broadcast |
| `_trigger_contact_close()` | Wrapper legado: chama Layer 1 + Layer 3 em sequência (path sem hooks) |

**Algoritmo de conclusão de hook em `process_routed()`:**
```
1. GETDEL hook_conf:{conference_id}  → retorna "{hook_type}:{pool}:{side}"
2. DECR hook_pending:{hook_type}
3. Se on_human_end e remaining_hooks <= 0:
   → dispatch post_human hooks (INCRs posatt:active primeiro)
4. Se side == "customer": DECR posatt:customer_active
   → Se == 0: _close_contact_layer()
5. SMEMBERS posatt:{conference_id}:participants → recipients[]
6. Publicar session.closed reason=posatt_segment_complete recipients=[...]
7. DECR posatt:active
   → Se == 0: _destroy_conference()
```

### 5.3 MCP Server (`mcp-server-plughub`)

**Responsabilidade:** WebSocket para Consoles de agentes humanos. Recebe e encaminha eventos pub/sub do Redis.

**Fluxo de conexão:**
1. Agent abre WS com `?pool_id={pool}` (lobby) ou `?session_id={ses}` (reconexão).
2. `subscriber` Redis duplicado para aquela conexão.
3. Subscreve `pool:events:{pool_id}` (lobby) e/ou `agent:events:{session_id}` (sessão).

**Variáveis de estado por conexão:**
- `agentInstanceId`: preenchido pelo primeiro `conversation.assigned` que chegar. Usado como referência para recipient filtering.
- `subscribedSessions`: Set de session_ids ativos nesta conexão.
- `agentRole`: `"primary"` ou `"specialist"`.

**Função `forward(channel, message)`:**
Chama `ws.send(message)` **diretamente** — sem nenhuma filtragem de recipient. Todo evento que chega ao `subscriber.on("message")` é enviado ao frontend, **exceto** o filtro de `posatt_segment_complete` descrito abaixo.

**Filtro Arc 14 Fase B — recipient filter (implementado em `subscriber.on("message")`):**
```typescript
// ANTES de chamar forward():
if (event.type === "session.closed" && event.reason === "posatt_segment_complete") {
  const recipients = Array.isArray(event.recipients) ? event.recipients : null
  if (recipients !== null && agentInstanceId && !recipients.includes(agentInstanceId)) {
    // Evento não é para este agente — descarta sem enviar ao frontend
    return
  }
}
forward(channel, message)
```

**Lógica de `shouldTearDown` em `forward()` após receber `session.closed`:**

| `reason` | Comportamento |
|---|---|
| `posatt_segment_complete` | Teardown **somente se** `agentInstanceId` está em `recipients` |
| `conference_destroyed` | Teardown sempre (broadcast) |
| `agent_done` | Teardown sempre (legado) |
| qualquer outro | Mantém sessão aberta (ex: `client_disconnect` — hooks ainda podem estar rodando) |

### 5.4 Channel Gateway

**Responsabilidade:** adaptar mensagens entre o canal externo (WhatsApp, webchat, etc.) e o stream interno.

**Ações relevantes para conferência:**
- Consome `conversations.outbound`: quando recebe `session.closed` com `reason=flow_complete` (ou equivalente), fecha o WebSocket do cliente.
- Durante posatt: o cliente já pode ter saído (`_close_contact_layer()` disparado), mas o Channel Gateway não fecha a conexão dos agentes — isso é responsabilidade do pub/sub `agent:events`.
- Entrega mensagens de hooks NPS/wrap-up ao canal correto via `notification_send` com `visibility` correto (NPS → customer-only; wrap-up → agent-only).

### 5.5 Platform UI (`AgentAssistContext.tsx`)

**Responsabilidade:** gerenciar a lista de sessões ativas no Console do agente humano.

**Handler `session.closed` (linhas 373-430 em `AgentAssistContext.tsx`):**

| `reason` recebido | Comportamento |
|---|---|
| `posatt_segment_complete` | Remove contato da lista **somente se** o mcp-server já filtrou e enviou (recipient match) |
| `conference_destroyed` | Remove contato da lista (broadcast — todos os Consoles recebem) |
| `agent_done` | Remove contato da lista (legado) |
| `client_disconnect`, `customer_disconnect`, `session_timeout`, `timeout` | Marca `sessionClosed=true` sem remover — posatt hooks ainda podem estar rodando |

**Invariante importante:** o frontend não faz filtragem de recipient por si mesmo. A filtragem é responsabilidade do mcp-server (no `subscriber.on("message")`). Se o evento chegou ao frontend, ele é tratado.

---

## 6. Posatt (Pós-Atendimento) — Mecânica Detalhada

### 6.1 Configuração de hooks na YAML do pool

```yaml
hooks:
  on_human_end:
    - pool: nps_bot_pool
      side: customer           # interação com o cliente
      nps_on_disconnect: skip  # pular NPS se cliente desconectou
    - pool: wrapup_bot_pool
      side: agent              # interação com o agente humano
  post_human:
    - pool: resumo_bot_pool
      side: agent              # resumo após on_human_end completo
```

### 6.2 Counters Redis para controle de posatt

#### `posatt:active` — controle de Layer 3
```
Exemplo com 2 hooks (NPS + wrap-up):
  fire_pool_hooks("on_human_end"):
    INCR → posatt:active = 2  (um por hook)

  NPS concluído → process_routed DECR:
    posatt:active = 1
    → _destroy_conference() lê 1 → retorna (hooks ainda rodando)

  Wrap-up concluído → process_routed DECR:
    posatt:active = 0
    → _destroy_conference() lê 0 → prossegue → broadcast conference_destroyed
```

#### `posatt:customer_active` — controle de Layer 1 (WS do cliente)
```
Exemplo com 1 hook NPS (side=customer):
  fire_pool_hooks("on_human_end"):
    INCR → posatt:customer_active = 1
    _close_contact_layer() NÃO é chamado ainda

  NPS concluído → process_routed:
    DECR posatt:customer_active = 0
    → _close_contact_layer() disparado agora
    → cliente recebe session.closed no WebSocket
```

#### Sem hooks customer-side (apenas wrap-up):
```
  agent_done do humano:
    → _close_contact_layer() disparado imediatamente
    → cliente fecha WS agora
    → wrap-up agent pode mandar mensagens ao agente humano normalmente
```

### 6.3 participants SET — targeted session.closed

Para cada hook conference:

```
fire_pool_hooks() popula o fixed-side:
  side=customer → SADD posatt:{conf}:participants customer_participant_id
  side=agent    → SADD posatt:{conf}:participants human_agent_participant_id

process_routed() quando hook agent faz join:
  SADD posatt:{conf}:participants native_instance_id_do_hook_agent

Ao concluir (process_routed hook detection):
  SMEMBERS posatt:{conf}:participants → recipients = [fixed_pid, hook_agent_id]
  Publicar session.closed reason=posatt_segment_complete recipients=[...]
  DELETE posatt:{conf}:participants
```

### 6.4 `hook_conf` key — ligação conference_id → hook metadata

```
Valor: "{hook_type}:{pool}:{side}"
Exemplo: "on_human_end:wrapup_bot_pool:agent"

Fluxo:
  fire_pool_hooks() → SETEX hook_conf:{conf_id} 14400 "on_human_end:wrapup_bot_pool:agent"
  process_routed() → GETDEL hook_conf:{conf_id}
                   → parse: completed_hook_type, _hook_pool, _hook_side
                   → usa para: DECR hook_pending, DECR posatt:customer_active (se customer),
                                decidir se dispatch post_human
```

---

## 7. Exemplos de Fluxo Completo

### 7.1 Fluxo simples — sem hooks

```
Cliente inicia contato
  → Channel Gateway → conversations.inbound (Kafka)
  → Routing Engine aloca agente: conversations.routed
  → Bridge: activate_human_agent()
    WRITE: session:{id}:human_agent = "human-pool_id"
    WRITE: session:{id}:human_agents → SADD "human-pool_id"
    WRITE: pool:pending_assignment:{pool_id} (TTL 300s)
  → mcp-server: conversation.assigned enviado via pool:events:{pool_id}
  → Console: adiciona contato à lista

Atendimento ocorre normalmente...

Agente clica "Encerrar"
  → mcp-server: POST /agent_done
  → Bridge process_contact_event():
    Pool não tem on_human_end hooks
    → _trigger_contact_close():
      _close_contact_layer():
        WRITE: contact_close_fired (NX, TTL 7d)
        PUBLISH conversations.outbound: session.closed (fecha WS cliente)
        PUBLISH conversations.events: contact_closed (analytics)
      _destroy_conference():
        GET posatt:active → nil (sem hooks) → prossegue
        SET close_fired (NX, TTL 7d)
        DELETE session:{id}:human_agent, session:{id}:human_agents
        PUBLISH agent:events:{session_id}: {type: "session.closed", reason: "agent_done"}
  → mcp-server subscriber: forward() → ws.send()
  → Console: shouldTearDown=true → remove contato da lista
```

### 7.2 Fluxo com NPS (customer-side) + Wrap-up (agent-side)

```
Agente clica "Encerrar"
  → mcp-server: POST /agent_done
  → Bridge process_contact_event():
    Pool tem on_human_end: [nps_bot_pool(customer), wrapup_bot_pool(agent)]

    Pre-hook ContextStore writes:
      session.close_origin = "agent_closed"
      session.customer_participant_id = "cust_xyz"
      session.human_agent_participant_id = "human-pool_id"

    fire_pool_hooks("on_human_end"):
      Hook 1 — NPS (side=customer):
        conference_id_nps = uuid4()
        PUBLISH conversations.inbound:
          { session_id, pool_id: "nps_bot_pool", conference_id: conference_id_nps,
            hook_type: "on_human_end" }
        INCR posatt:active → 1
        INCR posatt:customer_active → 1
        SETEX hook_conf:{conference_id_nps} "on_human_end:nps_bot_pool:customer"
        SADD posatt:{conference_id_nps}:participants "cust_xyz"  ← fixed-side (customer)

      Hook 2 — Wrap-up (side=agent):
        conference_id_wup = uuid4()
        PUBLISH conversations.inbound:
          { session_id, pool_id: "wrapup_bot_pool", conference_id: conference_id_wup,
            hook_type: "on_human_end" }
        INCR posatt:active → 2
        SETEX hook_conf:{conference_id_wup} "on_human_end:wrapup_bot_pool:agent"
        SADD posatt:{conference_id_wup}:participants "human-pool_id"  ← fixed-side (agent)
        SETEX {tenant}:instance:human-pool_id:wrap_up_pending {session_id}

    _close_contact_layer() NÃO é chamado (posatt:customer_active = 1)

--- Fase de posatt em paralelo ---

[NPS routing]
  Routing Engine recebe conversations.inbound do NPS:
    → aloca nps_agent_v1-001
    → PUBLISH conversations.routed: {session_id, conference_id: conference_id_nps, instance_id: "nps_agent_v1-001"}

  Bridge process_routed() — join do NPS:
    conference_id presente → hook agent joining
    SADD posatt:{conference_id_nps}:participants "nps_agent_v1-001"

  [NPS conversa com cliente via webchat...]

[Wrap-up routing — paralelo]
  Routing Engine recebe conversations.inbound do wrap-up:
    → aloca wrapup_v1-001
    → PUBLISH conversations.routed: {session_id, conference_id: conference_id_wup, instance_id: "wrapup_v1-001"}

  Bridge process_routed() — join do Wrap-up:
    SADD posatt:{conference_id_wup}:participants "wrapup_v1-001"

  [Wrap-up conversa com agente humano no Console...]
  [Console ainda aberto — session.closed não foi enviado para ele]

--- NPS termina PRIMEIRO ---

  Bridge process_routed() — agent_done do NPS:
    GETDEL hook_conf:{conference_id_nps} → "on_human_end:nps_bot_pool:customer"
    DECR hook_pending:on_human_end → 1  (wrap-up ainda rodando)
    side=customer → DECR posatt:customer_active → 0
      → _close_contact_layer():
           SET contact_close_fired (NX)
           PUBLISH conversations.outbound: session.closed → cliente fecha WS ✓
           PUBLISH conversations.events: contact_closed → analytics ✓

    SMEMBERS posatt:{conference_id_nps}:participants → ["cust_xyz", "nps_agent_v1-001"]
    PUBLISH agent:events:{session_id}:
      { type: "session.closed", reason: "posatt_segment_complete",
        recipients: ["cust_xyz", "nps_agent_v1-001"] }
    DELETE posatt:{conference_id_nps}:participants

    DECR posatt:active → 1

  mcp-server subscriber.on("message"):
    event.type="session.closed" reason="posatt_segment_complete"
    recipients=["cust_xyz", "nps_agent_v1-001"]
    agentInstanceId="human-pool_id"  ← NÃO está em recipients
    → return (descarta sem chamar forward())
    Console do agente humano: NADA ACONTECE ← wrap-up continua!

--- Wrap-up termina ---

  Bridge process_routed() — agent_done do Wrap-up:
    GETDEL hook_conf:{conference_id_wup} → "on_human_end:wrapup_bot_pool:agent"
    DECR hook_pending:on_human_end → 0  (último on_human_end)
    side=agent → sem posatt:customer_active

    Verifica post_human hooks: nenhum configurado

    SMEMBERS posatt:{conference_id_wup}:participants → ["human-pool_id", "wrapup_v1-001"]
    PUBLISH agent:events:{session_id}:
      { type: "session.closed", reason: "posatt_segment_complete",
        recipients: ["human-pool_id", "wrapup_v1-001"] }
    DELETE posatt:{conference_id_wup}:participants

    DECR posatt:active → 0
    → _destroy_conference():
        GET posatt:active → 0 → prossegue
        SET close_fired (NX, TTL 7d)
        DELETE session:{id}:human_agent, session:{id}:human_agents
        PUBLISH agent:events:{session_id}:
          { type: "session.closed", reason: "conference_destroyed" }

  mcp-server subscriber.on("message") — posatt_segment_complete:
    recipients=["human-pool_id", "wrapup_v1-001"]
    agentInstanceId="human-pool_id"  ← ESTÁ em recipients
    → forward() → ws.send()
    Console: shouldTearDown=true → remove contato ✓

  mcp-server subscriber.on("message") — conference_destroyed:
    shouldTearDown=true (broadcast)
    → forward() → ws.send() (para qualquer Console ainda conectado)
```

### 7.3 Fluxo com post_human

```
Após todos on_human_end completarem (posatt:active = N where N = count de post_human hooks):

  process_routed() — último on_human_end completa:
    DECR hook_pending:on_human_end → 0
    → dispatch fire_pool_hooks("post_human") ANTES do DECR de posatt:active
    fire_pool_hooks("post_human"):
      INCR posatt:active → N  (um por hook post_human)
      SETEX hook_conf:{conf_post} "post_human:{pool}:{side}"
      (mesma mecânica de participantes)

    DECR posatt:active (de on_human_end) → (N - 0) porque post_human já incrementou

  post_human conclui → process_routed:
    GETDEL hook_conf → "post_human:{pool}:{side}"
    DECR hook_pending:post_human
    (sem novo dispatch — post_human não tem successor)
    DECR posatt:active → 0
    → _destroy_conference()
```

### 7.4 Fluxo de reconexão do agente (F5 no Console)

```
Agente pressiona F5 durante atendimento ativo

  WS fecha → mcp-server cleanup handler:
    unsubscribe de pool:events + agent:events
    scheduleUnregisterHumanAgent() — timer de 5s (cancelado se reconectar antes)

  WS abre novamente:
    cancelPendingUnregister() — cancela unregister
    Subscribe pool:events:{poolId}
    GET pool:pending_assignment:{poolId} → assignment JSON
    Validação: GET session:{sid}:closed → nil (sessão ainda ativa)
    → forward(pool:pending_assignment) → ws.send(conversation.assigned)
    Console: recebe assignment → restaura contato na lista ✓
```

---

## 8. Histórico de Problemas e Correções

### Problema 1 — AHT inflado pelo tempo de wrap-up (G1)

**Data:** 2026-05 (Arc 14 Fase A)
**Sintoma:** AHT (Average Handling Time) incluía o tempo de wrap-up, inflando a métrica.
**Causa:** `_close_contact_layer()` usava `datetime.now()` como `ended_at` ao publicar `contact_closed`. Como `_close_contact_layer()` só rodava após o posatt, o AHT incluía NPS + wrap-up.
**Correção:** Adicionada `_mark_contact_ended()`: registra `session:{id}:contact_ended_at` com o timestamp exato quando o agente clica "Encerrar" (antes dos hooks). `_close_contact_layer()` agora lê essa key, caindo back para `now()` apenas em crash recovery.
**Arquivo:** `orchestrator-bridge/main.py` — `_mark_contact_ended()` + leitura em `_close_contact_layer()`.

---

### Problema 2 — NPS terminando antes derrubava o Console do agente humano (Arc 14 Bug, 2026-05-17)

**Sintoma:** Quando o NPS (`side=customer`) concluía antes do wrap-up (`side=agent`), o Console do agente humano removia o contato da lista. O wrap-up ficava "preso" sem sessão aberta.

**Diagnóstico:**
1. Investigação inicial: suspeitou-se de race condition em `_destroy_conference()` — sendo chamado prematuramente.
2. Logs do orchestrator-bridge mostraram: `posatt:active DECR remaining=1` na conclusão do NPS — backend estava **correto**. `_destroy_conference()` não estava sendo chamado.
3. Causa real encontrada: em `mcp-server`, a função `forward()` chamava `ws.send(message)` **antes** de qualquer verificação de recipient. O evento `posatt_segment_complete` (com `recipients` da NPS que NÃO incluía o agente humano) chegava ao frontend e o `AgentAssistContext.tsx` removia o contato da lista imediatamente.
4. Confirmado: `shouldTearDown` em `forward()` controlava apenas o Redis unsubscribe, não o `ws.send()`.

**Correções aplicadas (duas camadas de defesa):**

**Fix 1 — Defense in depth no backend (posatt guard em `_destroy_conference()`):**
Adicionado guard no início de `_destroy_conference()`: lê `posatt:active`; se `> 0`, retorna sem destruir. Cobre race conditions em paths de crash/timeout que possam chamar `_destroy_conference()` com hooks ainda rodando.

```python
# orchestrator-bridge/main.py — início de _destroy_conference()
posatt_raw = await redis_client.get(f"session:{session_id}:posatt:active")
if posatt_raw:
    remaining = int(posatt_raw ...)
    if remaining > 0:
        logger.info("_destroy_conference: posatt:active=%d — deferring destroy", remaining)
        return
```

**Fix 2 — Recipient filter no mcp-server (correção da causa raiz):**
Adicionado filtro em `subscriber.on("message")` **antes** de chamar `forward()`:

```typescript
// mcp-server-plughub/src/server.ts — subscriber.on("message")
if (_ev["type"] === "session.closed" && _ev["reason"] === "posatt_segment_complete") {
  const _recip = Array.isArray(_ev["recipients"]) ? (_ev["recipients"] as string[]) : null
  if (_recip !== null && agentInstanceId && !_recip.includes(agentInstanceId)) {
    // Este agente não é destinatário — descarta sem enviar ao frontend
    return
  }
}
forward(channel, message)
```

**Resultado:** NPS concluindo antes não afeta mais o Console do agente humano. Wrap-up continua normalmente. Testado com botão "Desliga" no Console e com F5 no lado do cliente webchat.

**Commit:** `arc14-fix: posatt guard em _destroy_conference + recipient filter mcp-server`
**Arquivos modificados:**
- `packages/orchestrator-bridge/src/plughub_orchestrator_bridge/main.py`
- `packages/mcp-server-plughub/src/server.ts`

---

### Problema 3 — busy counter acumulando em transferências cross-pool (G3/G6, 2026-05-10)

**Sintoma:** busy counter do agente ficava permanentemente incrementado após transferências entre pools, impedindo novos contatos.
**Causa:** `fire_pool_hooks()` não limpava `{tenant}:session:pool:{session_id}` antes de despachar hooks em paralelo. O segundo hook a ser roteado encontrava o `pool_id` do primeiro e DECR'd incorretamente.
**Correção:** `fire_pool_hooks()` deleta `{tenant}:session:pool:{session_id}` antes do loop de hooks. Cada hook INCR seu próprio pool independentemente.

---

### Problema 4 — instância AI restaurada durante execução (G3, 2026-05-10)

**Sintoma:** quando o agente AI primário concluía, `instance_bootstrap.py` restaurava a instância imediatamente — enquanto o Bridge ainda processava o `agent_done` e podia haver hooks pendentes.
**Correção:** `posatt:active` guarda `_destroy_conference()`, que é onde as human_agent keys são deletadas. Enquanto keys existem, a instância não é restaurada incorretamente. Adicionado: `agent_done` publicado explicitamente pelo Bridge para agentes native/YAML-fallback.

---

### Mudança 5 — outcome real do segmento primário humano via wrap-up (F1.4 bancada de agentes, 2026-06-07)

**Contexto:** o `participant_left` do primário humano é publicado no `agent_done` com outcome
**placeholder** (a Console hardcoda `resolved`/`abandoned` — não há coleta de outcome na UI). A
disposição real (`resolvido/pendente/escalado/cancelado`) é coletada pelo `agente_wrapup_v1` (hook
`on_human_end`, side=agent) que grava `session.wrapup.classificacao` (cru) + `session.wrapup.resumo`
no ContextStore com **scope: session** (mudado de `segment` para o Bridge ler sem conhecer o
segment-id do wrap-up).

**Mecanismo (B1′ — re-publish, idempotente):**
1. No primeiro `participant_left` do primário humano, o Bridge grava
   `session:{id}:primary_human_segment` (JSON: segment_id, instance_id, pool, agent_type_id,
   user_login, joined_at, duration_ms, sequence_index, tenant — TTL 7d).
2. `_finalize_human_outcome_from_wrapup()` lê a classificação CRUA, normaliza pelo
   `_WRAPUP_OUTCOME_MAP` (`resolvido→resolved, pendente→suspended, escalado→escalated,
   cancelado→abandoned` — decisão §13.2 do analytics-agents-workbench), atualiza
   `session:{id}:last_outcome` e re-publica o `participant_left` com o **MESMO segment_id**
   (ReplacingMergeTree substitui a linha) + `issue_status`=cru + `handoff_reason`=resumo (quando
   outcome≠resolved) + `close_reason` derivado do transporte.
3. **Dois gatilhos** (NX `session:{id}:primary_outcome_republished` garante um único re-publish):
   conclusão do hook side=agent em `process_routed` (caminho normal — `_close_contact_layer` já
   disparou na conclusão do NPS ou imediatamente sem hooks de cliente, ANTES do wrap-up terminar);
   e `_close_contact_layer` (cobre wrap-up terminando antes do fechamento — corrige também o
   outcome de SESSÃO via `last_outcome`).
4. **`close_reason` pela INICIATIVA**: fonte preferida é `session.close_origin` (ContextStore,
   gravado PRE-hook, congelado no instante do fim do contato). O marcador `session:{id}:closed` é
   sobrescrito pelo teardown do WS do cliente pós-NPS (`client_disconnect`) e corrompia a
   iniciativa (Encerrar do agente virava `customer_disconnect`). Fallback no marcador só quando
   `close_origin` ausente.

**Causa-raiz descoberta na validação (F1.4b)**: o `executeNotify` **nunca implementou
`context_tags`** (extração existia só em invoke/reason) e o schema inline do notify descartava
`scope`. Wrap-up e NPS gravavam no vácuo — nem `session.wrapup.*` nem `session.nps_score` chegavam
ao ContextStore. Corrigido no engine (extração com outputObj = `pipeline_state.results`) + `scope`
no `NotifyStepSchema.context_tags` (default `session`).

**Limitações conhecidas:** (a) quando o fechamento precede o wrap-up, a linha de SESSÃO mantém o
outcome placeholder — o **segmento** é a fonte da verdade para relatórios; (b) pools sem hook de
wrap-up (ou wrap-up com timeout/pulado) mantêm o placeholder — resolution por agente humano só é
fiel onde há wrap-up; nunca se inventa `resolved`.

**Keys novas:** `session:{id}:primary_human_segment` (TTL 7d) ·
`session:{id}:primary_outcome_republished` (NX, TTL 7d).

---

### Mudança 6 — `conversations.session_closed` publicado no fechamento (F2 bancada, 2026-06-07)

**Contexto:** o tópico `conversations.session_closed` (gatilho do pipeline de avaliação Arc 3/6 —
Persister do session-replayer) **nunca teve produtor**: a doc atribuía ao "Core", que não existe
como serviço no demo. O pipeline de avaliação ficou dormente desde a criação.

**Mudança:** `_close_contact_layer()` passa a publicar `conversations.session_closed`
(payload `SessionClosedEvent`: session_id, tenant_id, outcome, close_reason, closed_at) logo após
o `contact_closed` de analytics. Dispara uma vez por contato (guard `contact_close_fired` NX).
Consumidor único hoje: Persister do session-replayer (persiste o stream e publica
`evaluation.requested`). Gatilho é incondicional por fechamento — a amostragem por campanha é
da visão final (avaliador via calendário; ver TODO).

---

### Mudança 7 — atribuição per-segmento de wrap-up + NPS (F5 bancada, 2026-06-09)

**Contexto:** a F1.4 atribuía a disposição do wrap-up ao "último segmento primário humano"
(`primary_human_segment`, chave única por sessão) e lia a classificação do ContextStore
(`scope: session`). Isso é simplificação de demo: um contato pode passar por **vários humanos**
(handoff sequencial), cada pool com seu `on_human_end` — e dois wrap-ups colidiriam.

**Modelo per-segmento (F5):** cada `on_human_end` serve um **segmento humano específico**
(o do pool que disparou). A disposição (wrap-up) e o NPS são atribuídos a **esse** segmento
(`session_id`+`segment_id`), suportando N humanos/pools por contato.

**Mecanismo:**
1. No `participant_left` do humano: grava `session:{id}:human_seg:{pool}` (registro do segmento)
   e semeia o acumulador `session:{id}:seg_signal:{segment_id}` com o registro + `outcome`
   placeholder (do `agent_done`).
2. `fire_pool_hooks` (recebe o `pool_id` do humano): lê `human_seg:{pool}`, deriva `close_reason`
   da iniciativa (`session.close_origin`), e **carimba o `segment_id` no `hook_conf`** (5º campo:
   `{hook}:{target}:{side}:{origin}:{segment_id}`).
3. Na conclusão de cada hook (`process_routed`): a disposição/NPS vêm do
   **`agent_result.pipeline_state.results`** do próprio agente (`wrapup_classificacao`/`wrapup_resumo`
   no side=agent; `nps_resposta` no side=customer) — **não do ContextStore**. São acumulados no
   `seg_signal:{segment_id}` e o segmento é re-publicado com o estado COMPLETO (acumulador evita que
   wrap-up e NPS se anulem no `ReplacingMergeTree(ingested_at)`).

**Por que acumulador:** wrap-up e NPS completam em conferências/momentos distintos; um re-publish
parcial sobrescreveria o anterior. O hash `seg_signal` mantém todos os campos conhecidos; cada
re-publish carrega o estado completo → a última versão (maior `ingested_at`) tem tudo.

**Removido:** `_republish_human_primary_segment`/`_finalize_human_outcome_from_wrapup`/
`primary_human_segment`/`primary_outcome_republished` (F1.4). Tags `session.wrapup.*` voltaram a
`scope: segment` (não são mais lidas pelo bridge; persistem para o detalhe sob demanda).

**Keys novas:** `session:{id}:human_seg:{pool}` (TTL 7d) · `session:{id}:seg_signal:{segment_id}`
(hash, TTL 7d). **Schema:** `analytics.segments.nps_score Nullable(Int32)`.

**Limitação:** o demo só tem um pool humano → multi-humano fica correto por construção, sem E2E.

---

### Mudança 8 — motivo de escalação normalizado no segmento (F7 bancada, 2026-06-09)

**Contexto:** escalações não tinham um "porquê" estruturado — só o `handoff_reason` em texto livre.
A F7 adiciona uma taxonomia configurável (`agent_activity/escalation_reasons`) e grava o id
normalizado em `segments.escalation_reason`, mantendo `handoff_reason` como nota livre.

**Mecanismo (dois caminhos):**
1. **Humano**: o `agente_wrapup_v1` pergunta o motivo (menu `list`) só quando classificação=escalado
   (step `choice`). Na conclusão do hook, o bridge lê `pipeline_state.results.wrapup_escalation_reason`
   e `_apply_wrapup_to_segment` grava `escalation_reason` no acumulador `seg_signal` — **somente
   quando o outcome normalizado é `escalated`**.
2. **IA**: o step `escalate` ganha `reason`; `executeEscalate` persiste via `output_as` em
   `pipeline_state.results.escalation_reason`. No `participant_left` do agente nativo, o bridge lê esse
   campo e passa a `_publish_participant_event`.

`_publish_participant_event` e `_republish_segment_from_signal` propagam o novo campo;
`conversation_escalate` repassa o motivo ao Rules Engine via `process_context`.

**Schema:** `analytics.segments.escalation_reason Nullable(String)` (migração idempotente);
`ContactSegment`/`ConversationParticipantEvent`/`EscalateStep` ganham o campo. **Sem keys Redis novas**
(reusa o acumulador `seg_signal` da Mudança 7). O label legível vem do config (remapeado na UI).

### Mudança 8 — filtro de `conversation.assigned` por instância (bug multi-agente, 2026-06-11)

**Problema**: `conversation.assigned` é publicado no canal **do pool** `pool:events:{poolId}` (bridge
`_notify_human_agent_assigned`), e o WS handler do mcp-server aceitava QUALQUER assignment daquele canal
sem checar o alvo. Com dois humanos no mesmo pool (ex.: admin + operator em `retencao_humano`), um
contato roteado a UM agente aparecia no Console de AMBOS. Regressão: o canal por pool é legado (1 humano
por pool); o modelo de identidade por usuário (`registerHumanAgent` → `instance_id="human-{userId}"`, C1)
entrou sem filtrar o fan-out.

**Correção**: a conexão WS calcula `expectedInstanceId="human-${userId}"` no connect e **descarta**
`conversation.assigned` cujo `instance_id` aponta para outro agente — nos dois caminhos de entrega: o
pub/sub ao vivo (`subscriber.on("message")`, ANTES do `forward()` que faz `ws.send`) e a reentrega do
`pool:pending_assignment:{poolId}` na reconexão. Lógica pura em `lib/assignment-filter.ts`
(`shouldDropAssignment`). Backward-compatible: `userId` vazio (legado) ou `instance_id` vazio no evento →
não filtra. **A Routing Engine continua alocando a uma única instância** — só a entrega ao Console passou
a respeitar o alvo. Efeito colateral positivo: `agentInstanceId` da conexão passa a ser sempre a própria
instância (antes podia ser capturado do assignment de outro agente).

**Limitação aberta**: `pool:pending_assignment:{poolId}` é uma chave única por pool (last-write wins) —
chave por-instância fica como melhoria futura (liga à proposta de fila pull/inbox).

---

### Mudança 9 — Console Transfer funcional + G7 (decoupling segment-end × contact-end, 2026-06-12)

**Problema**: o "Transfer" do Console era um **stub** (`handleTransferTo` → `addToast(transferComingSoon)`) —
nunca executava nada. A lista de destinos (`supervisor_config.escalation_pools`) estava cabeada, mas a
**ação** não. Além disso, o mecanismo de transfer do tool `session_escalate` (mode: transfer) tinha um bug
**latente**: publicava em `conversations.inbound` um payload com `mode/from_participant/handoff_reason` mas
**sem `started_at`/`customer_id` válidos** → falhava a validação Pydantic do `ConversationInboundEvent` no
Routing Engine, que o descartava como **"Unrecognised inbound event (not a routing request)"**. Ou seja, a
re-rota nunca acontecia — o transfer "executava mas a origem não saía e o destino não recebia".

**Correção (G7 — branch cirúrgico)**:

- **mcp-server** `POST /api/session_transfer/:sessionId` (auth JWT, `participant_id = human-{userId}`), publica
  em ordem: (1) `participant_left` no stream (cliente vê a saída); (2) `session.closed{reason:agent_transfer}`
  em `agent:events:{session_id}` — só a **origem** está inscrita nesse canal no momento do transfer, então o
  Console dela **larga o contato** (branch de remoção do `AgentAssistContext`); (3) `conversations.events
  contact_closed{reason:agent_transfer, instance_id:origem, outcome:transferred}` — aciona a limpeza da origem
  no bridge; (4) `conversations.inbound` **válido** (`session_id, tenant_id, customer_id, channel literal,
  pool_id=target, started_at`) → re-rota pro pool destino (o router migra o bucket).
- **orchestrator-bridge** `process_contact_event` (`contact_closed`): (A) **não** seta `session:{id}:closed`
  quando `reason==agent_transfer` (senão o `is_closing` guard do routing descartaria a re-rota); (B) o branch
  agente-específico roda a limpeza normal da origem (restore + `participant_left` analytics
  `outcome=transferred` + `agent_done` no lifecycle → `remove_conversation` DECR + **SREM** `human_agents`),
  mas no `remaining<=0`, se `reason==agent_transfer` → **return** sem `_mark_contact_ended`, sem disparar
  `on_human_end` e sem `_trigger_contact_close`. O SREM libera o flag `human_active`, removendo o guard
  "Skipping duplicate routing / already-served" que bloqueava a ativação do destino.

**Efeito**: `on_human_end` deixa de significar obrigatoriamente "fim de contato" — no transfer é tratado como
**fim de segmento** (a origem sai, o contato segue pela re-rota). Validado E2E: 1 contato com 2 segmentos
humanos primários distintos (origem `transferred` no pool A → destino `resolved` no pool B), `on_human_end`
(wrap-up + NPS) disparando só no **fechamento do humano final**, e o sinal NPS de `session_signal`
corretamente chaveado ao `segment_id`/`agent_key` do segmento final (atribuição per-segmento — F5).

**Config necessária**: o pool humano de destino precisa ter os hooks `on_human_end` (ex.: `wrapup_ia` +
`nps_ia`) configurados, senão o fechamento do humano final fecha o contato **sem** wrap-up/NPS (o dispatch
lê `pool.hooks.on_human_end`; vazio → `_trigger_contact_close` direto).

**G7 — dívida aberta (decoupling parcial)**: o desacoplamento segment-end × contact-end foi feito **só para o
caso transfer** (`reason==agent_transfer`). Permanece dívida: (1) `on_human_end` como fim-de-segmento
**genérico** — num conference com 2+ humanos primários, um humano que **não é o último** a sair (sem transfer)
não dispara `on_human_end` → seu segmento não ganha wrap-up; (2) NPS como hook de **fim-de-contato** de 1ª
classe (hoje "pega carona" no `on_human_end` do último humano); (3) reconhecer outras continuações além do
transfer explícito (re-fila, handback IA). O **wrap-up transfer-aware** (segmento que sai coletar o motivo via
`escalation_reasons` → `survey_record`) **não é um caminho dedicado** — é caso particular de "todo fim de
segmento gera wrap-up" e fica **absorvido no arco G7** ([`docs/arcos/g7-segment-contact-decoupling.md`](../arcos/g7-segment-contact-decoupling.md));
o transfer é funcional sem ele (o segmento que sai registra `outcome=transferred` sem nota). A avaliação
**per-segmento do cliente** é **outbound** (modelo multi-grão journey/session/segment — F11), não inline.

---

### Mudança 10 — wrap-up multi-humano: identidade de participante por-segmento (G7 Slice A, 2026-06-12)

**Problema**: o wrap-up (`on_human_end` side=agent) só funcionava com o humano sendo o **segmento
final** do contato. Em multi-humano (humano convidado como specialist; origem+destino de transfer)
o isolamento dependia de **um** campo de SESSÃO `session.human_agent_participant_id`, lido por 4
componentes (`fire_pool_hooks` `_fixed_pid`; `mcp-server` `menu_submit` e texto WS; visibility da
`agente_wrapup_v1.yaml`) e **sobrescrito** a cada humano que sai → colapsa com ≥2 humanos. Saída
mal-endereçada (visibility errada) + entrega broadcast (`forward()` fazia `ws.send` incondicional) +
entrada resolvendo o humano errado. Ver
[`docs/adr/adr-participant-identity-single-source.md`](../adr/adr-participant-identity-single-source.md).

**Correção (3 partes)**:
- **(a) saída — endereço**: `fire_pool_hooks` deriva o `_fixed_pid` (side=agent) do `instance_id` do
  humano DESTE segmento (`human_seg:{pool}`) e guarda `session:{id}:hook_served_human:{conference_id}`;
  o join do wrap-up (`process_routed`, antes de `activate_native_agent`) grava
  `segment.{wrapupSegId}.served_human_participant_id` no ContextStore (padrão `inviter_participant_id`).
  YAML usa `@segment.served_human_participant_id` (auto-prefixa para `segment.{ctx.segmentId}.…`;
  `@ctx.segment.*` **não** auto-prefixa). `ctx.segmentId == _part_seg_id` (verificado).
- **(b) saída — entrega**: filtro em `subscriber.on("message")` antes do `forward()` — descarta evento
  de **array-visibility** que não inclui a identidade da conexão (`expectedInstanceId`/`agentInstanceId`);
  `"all"`/`"agents_only"` passam; identidade desconhecida → encaminha conservador.
- **(c) entrada — remetente**: (c1) texto WS resolve `agentPid` pela conexão (`expectedInstanceId`),
  fallback no global só sem conexão; (c2) `menu_submit` aceita `agent_key` e roteia direto ao
  `menu:result:{sid}:{agent_key}` (fallback no scan). `menu.render` expõe `source_instance = authorId`
  (= `ctx.instanceId` = chave do `menu:waiting` = sufixo do BLPOP); o Console ecoa como `agent_key`.

**Keys novas**: `session:{id}:hook_served_human:{conference_id}` (TTL 4h, side=agent) ·
ctx `segment.{segId}.served_human_participant_id`. **`session.human_agent_participant_id`** mantido só
como fallback single-humano (não aposentado). **Limitação**: o `author_id` do echo no `menu_submit`
(botão) ainda sai do campo global — cosmético, roteamento já correto.

### Mudança 11 — wrap-up no transfer (G7 Slice B, 2026-06-13)

**Problema**: no transfer (Mudança 9) a origem saía como fim-de-segmento **sem** wrap-up — registrava
`outcome=transferred` sem o motivo. A Slice B coleta a disposição do humano que transferiu, atribuída
ao **segmento da origem**, sem fechar o contato (segue pelo destino).

**Tipo de hook novo `segment_wrapup`** (fim-de-segmento, não fim-de-contato):
- `fire_pool_hooks(hook_type="segment_wrapup")` reusa a lista `on_human_end` do pool **filtrando para
  `side=agent`** (só o wrap-up, **sem NPS**); grava `hook_conf` (`segment_wrapup:…`), o stash
  `hook_served_human` (Slice A) e `wrap_up_pending`; **NÃO** faz `INCR posatt:active`/`hook_pending`
  (não pode gatilhar `_close_contact_layer`/`_destroy_conference`).
- Branch `agent_transfer` em `process_contact_event`: troca o `return` seco por
  `fire_pool_hooks(segment_wrapup, pool_id=origin_pool)` + `return` — sem `_mark_contact_ended`, sem close.
- Conclusão em `process_routed` (`completed_hook_type == "segment_wrapup"`): aplica
  `_apply_wrapup_to_segment` (disposição→`seg_signal`→re-publish do segmento da origem), limpa
  `wrap_up_pending`, publica `posatt_segment_complete` (fecha o painel da origem) e **pula** o
  `DECR hook_pending`/`DECR posatt:active`/`_destroy_conference`.

**Console (B2)**: o `session.closed{reason:agent_transfer}` deixa de **remover** o contato da origem —
agora entra em **modo wrap-up** (`sessionClosed=true`, mantém inscrito em `agent:events`), recebendo o
`menu.render` do wrap-up (visibility `[origin_pid]`, isolado pela Slice A). A remoção acontece no
`posatt_segment_complete` (origem nos recipients) quando o wrap-up conclui. (O mcp-server já não fazia
teardown em `agent_transfer` — só nos reasons `agent_done`/`conference_destroyed`/`posatt`.)

**Keys novas**: nenhuma (reusa `hook_conf`/`hook_served_human`/`seg_signal`/`wrap_up_pending`).
**Validação E2E**: transfer A→B → origem coleta motivo via wrap-up (isolado, não vaza pro cliente/destino),
segmento da origem re-publicado com a disposição, contato segue e fecha só no humano final.
**Ponto de validação aberto**: contabilidade de pool com destino+wrap-up concorrentes (`session:pool`
é slot único — pior caso +1 no contador do destino; ver nota em `fire_pool_hooks`).

### Mudança 12 — close governado por `_has_continuation` + marcador condicional (G7 Fase 3a, 2026-06-13)

**Problema**: o marcador `session:{id}:closed` (lido pelo Routing Engine em `_drain_queue_for_agent` /
`is_closing` para descartar re-rotas de sessões que estão fechando) era escrito **incondicionalmente** no
topo de `process_contact_event` para todo reason ≠ `agent_transfer`, **antes** de saber se o fim do
segmento humano era também fim de contato. Em multi-humano (`remaining>0`, "Agent dropped, N still
active") o marcador vazava → o Routing Engine descartava re-rotas/reconexões legítimas de uma sessão que
**continuava** ativa (§4 / §8.1).

**Mudança** (parity-preserving em single+transfer):
- A escrita do marcador foi **fatiada por caminho**: `customer_side` (cliente saiu/timeout/agent_done)
  escreve no topo (sempre fim-de-contato); `agent_closed` escreve **só** no path `no_continuation`/defer
  por specialist (dentro de `remaining<=0`, após o `transfer` retornar); o branch `other_human_active`
  (`remaining>0`) **não** escreve — o contato continua com outro `primary`.
- O literal `if reason == "agent_transfer"` no `remaining<=0` virou `if _g7_cont and
  _g7_motive == "transfer"` (o classificador `_has_continuation` passa a **governar** o close; default
  por `reason` antes do `try` preserva o transfer se o classificador falhar).

**Comportamento**: single-humano e transfer **inalterados** (marcador escrito/omitido como antes). Única
mudança observável: o marcador deixa de ser escrito em `other_human_active` — remove um dano conhecido
(re-rotas descartadas) e é fundação para o sub-arco multi-humano. O encerramento completo do contato
multi-humano (segment-end do não-último + fan-out + NPS) continua fora de escopo (sub-arco multi-humano).

**Keys novas**: nenhuma. **Rebuild**: `orchestrator-bridge` (Python).
**Validação E2E**: (1) single-humano fecha com wrap-up+NPS 1× e `session:closed` setado; (2) transfer
A→B sem marcador prematuro na origem, NPS só em B.

> **Adendo (G7 Slice 4′ Item 1, 2026-06-13):** o bridge tratava só a sua própria escrita do marcador.
> O **mcp-server** ainda seta `session:closed` de forma INCONDICIONAL no `/api/agent_done` (server.ts
> ~1475, p/ ganhar a corrida com `pending_assignment` no reconnect single-humano). Em `other_human_active`
> o bridge agora **desfaz** (`delete session:{id}:closed`) — o mcp-server segue setando síncrono (race
> protection no single-humano), o bridge só desfaz quando há continuação. Fecha o vazamento do §4.

### Mudança 13 — NPS como hook de fim-de-CONTATO (`on_contact_end`, G7 Fase 3b-i, 2026-06-13)

**Problema**: o NPS era um entry `side=customer` dentro de `on_human_end` (hook de fim-de-SEGMENTO),
pegando carona no último segmento humano. Em transfer/multi-humano isso confunde "qual segmento dispara
o NPS do contato". G7 Fase 3 separa: **wrap-up = fim-de-segmento** (`on_human_end`, side=agent),
**NPS = fim-de-contato** (`on_contact_end`, side=customer, 1× por contato).

**Hook type novo `on_contact_end`** (cutover limpo, sem dual-read):
- **schema**: `on_contact_end: PoolHookEntry[]` em `PoolHooksSchema`. `hooks` é `Json?` no Prisma → sem
  migração de DB.
- `fire_pool_hooks`: `on_contact_end` entra nos 4 conjuntos existentes — set `hook_pending`, stash
  `human_seg`→`surveyed_segment_id` (para o agente de NPS gravar `survey_record grain=segment`), escrita
  do `hook_conf`, e INCR `posatt:active`+`posatt:customer_active` (side=customer). **Não** precisou de
  `arm_close`: o wrap-up segue em `on_human_end` (que já arma `posatt:active`) e o NPS em `on_contact_end`
  (que arma os dois contadores) → o `_destroy_conference` (posatt:active==0) espera **ambos** e o
  `_close_contact_layer` (posatt:customer_active==0) espera o NPS.
- **completion handler** (`process_routed`): **zero mudança** — é genérico por `_hook_side` + contador
  por `hook_pending:{tipo}`. Única especificidade: `post_human` é gatilhado por `on_human_end` completar
  (dispara após o wrap-up; `post_human=[]` no demo).
- `process_contact_event`: nos 4 sites de dispatch (no_continuation, customer_disconnect, e os 2 caminhos
  de defer-por-specialist — nativo e Kafka) dispara `on_human_end` **e** `on_contact_end` separadamente.
  A decisão "mantém WS aberto" passa a olhar `on_contact_end` (com fallback p/ entries side=customer em
  `on_human_end` de pools não migrados).

**Cutover**: `infra/registry/tenant_demo.yaml` (`retencao_humano`) move `nps_ia` para `on_contact_end`;
pools de DB (criados via UI, ex.: `humanoxxx`) migram por `infra/migrations/g7_nps_to_on_contact_end.py`
(API oficial `/v1/pools`, idempotente, dry-run por default). **Rebuild**: `schemas`+`agent-registry`+
`orchestrator-bridge`. **Validação E2E**: single-humano byte-parity (wrap-up no Console + NPS ao cliente,
fecha 1×); pool sem NPS fecha após wrap-up sem teardown prematuro; transfer inalterado.

### Mudança 14 — wrap-up por peer humano (G7 Slice 2′, 2026-06-13)

**Modelo peer/Teams-like** (invariante g7 §10): humanos numa conferência são peers; o contato fecha
quando o último agente customer-facing sai. Esta mudança dá wrap-up ao humano **não-último**.

No branch `other_human_active` (`remaining>0`) de `process_contact_event`, o humano que sai passa a
disparar `fire_pool_hooks(hook_type="segment_wrapup", pool_id=_ha_pool)` — antes só logava. Mesmo
mecanismo do `agent_transfer` (Mudança 11): `segment_wrapup` dispara só `side=agent`, **não** arma
`posatt:active`/`hook_pending` (o contato segue sob os outros), e a conclusão aplica a disposição ao
`seg_signal`→re-publish do segmento. `_ha_pool`/`_ha_tenant` vêm por-instance (`participant_meta`, Slice
1b) e `human_seg:{_ha_pool}` (escrito na saída deste humano) atribui ao segmento dele. O último humano
(`no_continuation`) segue com `on_human_end`+`on_contact_end`.

**Limitação** (pré-existente): `human_seg` é keyed por pool → 2 humanos no MESMO pool colidem. Wrap-up
por peer no path **customer-disconnect** (N humanos) fica para a Slice 4′.
**Keys novas**: nenhuma. **Rebuild**: `orchestrator-bridge`. **Validação E2E**: admin (não-último) fecha →
`Peer wrap-up (segment_wrapup) dispatched`, Console coleta wrap-up, contato segue sob o operator.

### Mudança 15 — fan-out humano↔humano (G7 Slice 3, 2026-06-13)

**Problema** (gap 1 do §7): numa conferência com 2+ humanos, a mensagem **normal** (texto ao cliente) de
um humano era publicada em `conversations.outbound` (→ cliente) + stream + analytics, mas **não** em
`agent:events:{session}` → os outros humanos (subscritos a `agent:events` via Console) não recebiam. Os
ramos `@mention` e resposta-a-hook do agent-WS já publicavam em `agent:events`; só o ramo normal não.

**Mudança** (`mcp-server/server.ts`, agent-WS):
- O ramo normal customer-facing passa a `redis.publish(agent:events:{session}, {type:"message.text",
  author:{type:"agent_human", instance_id}, session_id, contact_id, visibility:"all", …})` — fan-out aos
  outros humanos. **Gotcha**: o `session_id` no payload é **obrigatório** — o handler `message.text` do
  Console faz `if (!sid) return` e dropa o evento sem ele (foi o bug da 1ª tentativa).
- O filtro de forward (mesmo bloco do filtro de array-visibility) ganha **self-skip**: `message.text`
  cujo `author.instance_id == self` (`expectedInstanceId||agentInstanceId`) não é reenviado ao próprio
  remetente — ele já exibe via echo otimista local (id `local-…` ≠ `message_id` real → dedup-por-id do
  Console não pegaria → render duplo). Outros humanos (instance ≠ self) recebem.

**Por que não duplica**: o cliente não assina `agent:events` (recebe via channel-gateway/outbound);
agentes IA leem o **stream** (a msg já é escrita lá, visibility=all). O fan-out em `agent:events` é só
para Consoles humanos. **Escopo**: msg aparece como `agent_human` genérico (atribuição-por-nome é polish).
**Keys novas**: nenhuma. **Rebuild**: `mcp-server-plughub`.

### Mudança 16 — queda involuntária de humano: detecção + re-rota (heartbeat Slice 1, 2026-06-13)

**Problema (gap G4)**: humano que cai mid-contato (WS drop, não `agent_done`) só disparava
`participant_left` no stream + `unregisterHumanAgent` (tira do roster do pool) — **nenhum**
`contact_closed`. O bridge nunca processava o drop → contato **órfão** (sem re-rota nem close) até o
cliente sair ou o watchdog de órfãos.

**Detecção**: o `ws.close` + grace de 2.5s (cancela em reconnect — cobre refresh/StrictMode) é o sinal
de drop genuíno. No callback do grace, o mcp-server publica `contact_closed(reason="agent_disconnect",
instance_id)` para cada sessão inscrita onde o humano **ainda** está em `human_agents` (`sismember` dedup
vs. quem já fez `agent_done`).

**Bridge** (`process_contact_event`): `agent_disconnect` reusa o branch `agent_closed` (segment-end:
restore + `participant_left` + agent_done lifecycle DECR + SREM `human_agents`). Duas diferenças:
- `other_human_active` (`remaining>0`): **não** dispara o peer wrap-up da Slice 2′ (humano sumiu, não
  preenche menu) — só encerra o segmento; contato segue sob o outro.
- `remaining<=0`: **re-rota** — publica `conversations.inbound` ao `_ha_pool` do humano que caiu
  (espelha o transfer, Mudança 9) → routing aloca novo humano ou enfileira. Posse re-estabelecida por
  **alocação** (não promoção — invariante §10). Cliente presente é implícito (se tivesse saído, viria
  por `customer_side`). **Não** escreve `session:closed` (contato continua); o mcp-server tb não setou
  (só `/api/agent_done` seta). Degrada gracioso: sem humano no pool → routing enfileira/`no_resource`.

**Keys novas**: nenhuma. **Rebuild**: `mcp-server-plughub` + `orchestrator-bridge`.

> **Adendo (heartbeat Slice 2, 2026-06-13) — pong-tracking p/ "drop sujo":** o `ws.close` nem sempre
> dispara numa meia-conexão (sleep, partição). O agent-WS passa a usar **ping de protocolo** (`ws.ping`,
> auto-respondido pelo browser via RFC 6455): o evento `pong` reseta `isAlive`; se um ciclo de 30s passa
> sem pong → `ws.terminate()` → dispara `ws.on('close')` → grace → `agent_disconnect` (Slice 1). Falso
> positivo é auto-curável (Console reconecta dentro do grace → cancela). O `{type:"ping"}` app-level é
> mantido. **Arco heartbeat completo** (Slices 1+2). Rebuild: `mcp-server-plughub`.

### Mudança 17 — `human_seg` por-instância (G7 Item 1 / Slice 4′ Fatia 1, 2026-06-14)

**Contexto**: o registro do segmento humano `session:{id}:human_seg:{pool}` (Mudança 7/14) era keyed
por **pool**. A "limitação" anotada (2 humanos no mesmo pool colidem) é, na prática, **operacionalmente
inexistente** — agentes de um pool são equivalentes, basta 1 por pool num contato. O motivo real da
mudança é ser a **fundação** do fan-out da Fatia 3 (wrap-up por peer no customer-disconnect): para dar
wrap-up ao segmento de **cada** humano (pools distintos), o fan-out precisa endereçar por `instance_id`
ao iterar o SET `human_agents`.

**Mudança** (parity-preserving):
- Chave canônica `session:{id}:human_seg:{instance_id}`; **dual-write** do espelho legado
  `human_seg:{pool}` como fallback de back-compat (sessões in-flight / callers não migrados).
- `fire_pool_hooks(..., human_instance_id="")`: o reader prefere a chave por-instância e só cai no
  espelho por-pool se a por-instância faltar (`fallback=True` no log).
- Threading do `human_instance_id` nos 10 call-sites que leem `human_seg` (on_human_end /
  on_contact_end / segment_wrapup): defer nativo/Kafka (`_npd_h_inst`/`_pd_h_inst`), customer_disconnect
  (`_last_human_instance_id`), transfer / no_continuation / other_human_active (`instance_id`).
- Logs `G7 Item1 human_seg WRITE/READ` (INFO) para observabilidade do gate (manter até a Fatia 3;
  rebaixar a debug no cleanup da Fatia 4).

**Keys**: `session:{id}:human_seg:{instance_id}` (TTL 7d, nova canônica) + `human_seg:{pool}` (espelho,
a remover no cleanup). **Rebuild**: `orchestrator-bridge`. **Validação E2E**: single-humano (byte-parity,
READ `fallback=False`) + multi-humano pools distintos (admin não-último `segment_wrapup` → seg do admin;
operator último `on_human_end`+`on_contact_end` → seg do operator + NPS; duas chaves por-instância, zero
cross-attribution). A Mudança 14 (peer wrap-up) deixa de ter a limitação "mesmo pool".

> **Achado (pré-existente)**: no E2E multi-humano o `agent_done` do não-último foi processado **2×**
> (double-dispatch de `segment_wrapup` + segmento fantasma de duração zero em `analytics.segments`).
> Idempotência ausente em `process_contact_event` (redelivery/publish duplicado), **upstream** da Fatia 1.
> **Corrigido na Fatia 2a (Mudança 18).**

### Mudança 18 — idempotência do close do agente (G7 Item 1 / Fatia 2a, 2026-06-14)

**Problema**: o `agent_done`/`contact_closed` do humano podia chegar **2×** (double-submit do Console ou
redelivery Kafka — o consumer do bridge despacha cada msg como `asyncio.create_task`, sem serialização).
O 2º passe recriava o segmento humano (`participant_joined_at` já `getdel`'d no 1º → duração 0 →
**segmento fantasma** em `analytics.segments`) e re-disparava `segment_wrapup` (conferência redundante).
Exposto pela Mudança 17 (logs), pré-existente.

**Mudança** (sem chave nova, race-safe): o branch de close do agente em `process_contact_event` ganha um
**gate de idempotência** logo após o `is_human`, **antes** do `_restore_instance` e dos side-effects:
```python
if instance_id:
    _close_removed = await redis_client.srem(f"session:{session_id}:human_agents", instance_id)
    if _close_removed == 0:
        # já saiu (duplicado, ou encerrado por outro caminho — heartbeat drop) → no-op
        return
```
`SREM` é atômico e retorna quantos removeu → o 2º passe vê 0 e retorna. O `SREM` redundante mais abaixo
(antes do `scard remaining`) foi removido. O duplicado do **último** humano já era pego pelo `is_human`
(flag deletada no `remaining<=0`); o gate cobre o **não-último** (flag viva sob o outro humano) — onde o
fantasma aparecia. Cobre também a corrida entre `agent_done` e heartbeat-drop do mesmo instance.

**Keys**: nenhuma. **Rebuild**: `orchestrator-bridge`. **Validação**: multi-humano agent_done pós-fix —
um só `Peer wrap-up dispatched` por close, **sem segmento fantasma**, wrap-ups+NPS corretos
(não-regressão). Log `Duplicate/late agent close ignored` aparece quando um duplicado de fato chega
(intermitente); correção correta por construção (SREM atômico).

### Mudança 19 — fan-out do wrap-up no customer-disconnect + gap-2 de menu concorrente (G7 Fatia 2b/3, 2026-06-14)

**Objetivo**: no customer-disconnect com N humanos, dar wrap-up a **cada** humano (antes só o pool do
`meta` recebia). **Lado bridge entregue e correto; E2E bloqueado por gap-2 (menu concorrente).**

**Mecanismo (bridge)**:
- Contador novo `session:{id}:contact_close_pending` (+ marcador `close_arming:{conference_id}`): cada
  `segment_wrapup` do fan-out faz INCR; `_destroy_conference` adia enquanto `>0`; a conclusão do
  `segment_wrapup` (`process_routed`) faz `GETDEL close_arming`→DECR e, ao zerar, dispara
  `_close_contact_layer`+`_destroy_conference` (ambos idempotentes/auto-guardados).
- `process_contact_event` (customer_disconnect): âncora (`_last_human_instance_id`) mantém
  on_human_end+on_contact_end; cada **peer** dispara `fire_pool_hooks(segment_wrapup, arm_contact_close=True)`.
- O loop `customer_side` passou a escrever `human_seg:{instance}` por humano (o branch agent_closed já
  fazia) — sem isto o fan-out caía no pid global e mandava todos os wrap-ups ao último humano.
- `_contact_close_timeout_guard` (180s): segment_wrapup não usa hook_pending → guarda dedicada.

**Validado**: entrega/atribuição corretas (2 `human_seg WRITE`, READs `fallback=False`, cada menu ao seu
console por visibility de segmento).

**Bloqueio E2E — CAUSA-RAIZ REAL: corrida de sobre-alocação no router (corrigido o diagnóstico
2026-06-14)**: as duas conferências de wrap-up caíram na **mesma instância** `wrapup_ia-019`, e como são da
**mesma sessão** a chave de menu `menu:result:{sid}:{instanceId}` colidiu (inputs cruzam, menus expiram).
Mas a co-locação **não é por design** — é **bug de concorrência**: (1) cada instância de `wrapup_ia` é
**single-occupancy** (`max_concurrent=1`; bootstrap cria N instâncias de cap 1 a partir de
`max_concurrent_sessions`); (2) o consumer do routing processa inbound **concorrente**
(`main.py` `asyncio.create_task(_process_message)` por msg); (3) `get_ready_instances`→`mark_busy` é
**não-atômico** (read-modify-write em `current_sessions`, sem claim). Os dois inbound paralelos leem a -019
com `current_sessions=0`, ambos a escolhem, ambos `mark_busy` `0→1` (**lost update**). **Evidência**:
`posatt segment closed` dos dois com `recipients=[wrapup_ia-019,…]`; `[menu_submit]` com
`agent_key=wrapup_ia-019` e visibility ora `[operator]` ora `[admin]`; `menu:waiting` com 1 campo só.

**Impacto geral**: a corrida afeta **qualquer pool** sob alocação concorrente — latente (para sessões
distintas só desbalanceia carga / estoura capacidade em silêncio, pois as chaves de menu diferem por
`sessionId`), ficou **visível** aqui via 2 segmentos da MESMA sessão.

**Fix primário = alocação atômica no router** (claim que rejeita sobre-capacidade e re-seleciona; ex.: Lua
ou INCR-and-check em `current_sessions`). Conserta o wrap-up (concorrentes passam a ir para instâncias
distintas → chaves de menu distintas) **e** a sobre-alocação em todos os pools. **Hardening secundário
(opcional)**: chave de menu por `segmentId` (já no `ctx`) como defesa-em-profundidade para pools que
legitimamente tenham `max_concurrent>1` com 2 segmentos da mesma sessão.

**Keys novas**: `session:{id}:contact_close_pending`, `session:{id}:close_arming:{conference_id}`.
**Rebuild**: `orchestrator-bridge`.

### Mudança 20 — alocação atômica no router (fix da corrida) + camada 3 restante (2026-06-14)

**Resolve a causa-raiz da Mudança 19** (corrida de sobre-alocação): o router agora faz **seleção otimista +
claim atômico + re-seleção** (semáforo de contagem por-instância via Lua, `claim_instance`/`release_instance`
sobre `instance:{id}:sessions`; occupant `"{session_id}::{conference_id}"`; release por prefixo de sessão).
`mark_busy`/`remove_conversation` deixam de fazer read-modify-write em `current_sessions` (sincronizam do
`SCARD`). Validado E2E: dois wrap-ups concorrentes da mesma sessão caem em **instâncias distintas** (não mais a
mesma `-019`/`-002`), `router.claim ... claim=-1 — re-selecting` confirmando a arbitragem. Detalhe e fatias em
CHANGELOG (Router · Alocação atômica A/B) + TODO § Router.

**Camada 3 — DIAGNÓSTICO REVISADO (2026-06-15, ver Mudança 21):** a afirmação acima ("skill-flow chaveia
`pipeline_state`/lock por `session_id`") estava **incorreta para o binário em HEAD** — o bridge **já sufixa**
`pipeline_session_id` por `--seg--{segment_id}` para agentes de conferência (`activate_native_agent`); a
evidência `5ea8dfae` veio de um **build stale**. O bloqueio real do E2E concorrente eram **outros dois**
pontos (corrigidos na Mudança 21): o YAML-fallback que não sufixava, e o **dedup de specialist por `pool_id`**
que colapsava os dois wrap-ups do mesmo pool numa corrida.

### Mudança 21 — Camada 3: hardening do pipeline por conferência + isenção de hook no dedup (G7 Item 2 ✅, 2026-06-15)

**Fecha o E2E do G7 Item 2** (2 humanos com wrap-up determinístico no customer-disconnect). Duas correções no
`orchestrator-bridge`:

- **Fatia A — chave de pipeline robusta** (`activate_native_agent`): para qualquer `conference_id`, isolar por
  `segment_id or instance_id or uuid` — nunca `session_id` cru. Byte-parity no native comum
  (`--seg--{segment_id[:8]}`). Conserta o branch sem-segmento (era `--conf--{conf}`, colidia ao compartilhar
  `conference_id`) e o **YAML-fallback** (`process_routed`, registry 404) que ativava sem `conference_id`/
  `segment_id` (chaveava `session_id` puro) — agora propaga `conference_id`.
- **Fatia A2 — hook isento do dedup de specialist** (`process_routed`): no fan-out multi-humano, âncora
  (`on_human_end`) e peer (`segment_wrapup`) miram ambos `wrapup_ia`. O dedup `conference:specialist:{pool_id}`
  (anti repeat-@mention) é **corrida** (marcador escrito após o check) → colapsava o 2º hook → 1 humano sem
  wrap-up, intermitente. Hooks (detectados por `hook_conf:{conference_id}`, gravado no `fire_pool_hooks`) são
  **isentos**; repeat-@mention de specialist (sem `hook_conf`) segue protegido.

**Validado E2E** (admin `retencao_humano` + operator `humanoxxx`, reiniciar cliente): 2 runs verdes,
`wrapup_ia-001`+`-002` em `menu:waiting`, `pipeline=` distintos, ambos `pushed=true`, **zero** `Skipping
duplicate conference invite`. **Rebuild**: `orchestrator-bridge`. **Follow-up**: âncora usa `_cs_pool_id` do
`meta` (last-writer) em vez do `participant_meta` (classe Slice-1b; invisível enquanto pools convergem p/
`wrapup_ia`); latência do `@mention` (TODO § Camada 3).

### Mudança 22 — hook-pool por segmento: on_human_end/on_contact_end do pool de quem fecha (2026-06-15)

Os hooks de fim-de-segmento (wrap-up) e fim-de-contato (NPS) do **último/âncora** segmento passam a resolver o
pool de `participant_meta:{instância que fecha}` (fallback `session:meta`), alinhando-os aos **peers** (que já
usavam `participant_meta`). Antes vinham do `session:meta` (last-writer = último humano **ativado**) → config do
pool errado quando os pools humanos divergem. Dois sites: `agent_closed` (`_pool_id_hooks` por `instance_id`;
cobre o **deferred** via stash `pending_on_human_end`) e `customer_disconnect` (`_cs_pool_id` por
`_last_human_instance_id`).

Modelo de referência (G7): **wrap-up** é por-segmento (todo fim de segmento → wrap-up com o pool **próprio**;
sem relação com âncora). **NPS** é grão-configurável (`segment | contact | journey`, definido no skill-flow do
agente NPS; `survey_record(grain)`): em grão=contact dispara **uma vez** na âncora (último a se desligar) com o
pool **desse** segmento — a âncora é só o ponto de disparo único, não "o NPS do Primary". Validado E2E
(sequencial, admin último → `on_human_end`/`on_contact_end origin_pool=retencao_humano`; pré-fix saía
`humanoxxx` do meta). Paridade de comportamento (`target_pool` `wrapup_ia`/`nps_ia`).

---

### Mudança 23 — `on_contact_end` disparado também no fim de contato de primário IA (2026-06-22)

`on_contact_end` (NPS de fim-de-CONTATO, side=customer — Mudança 13) é o mecanismo **genérico** de
fim-de-contato: ao disparar, faz `INCR posatt:customer_active` (segura o WS do cliente via
`_close_contact_layer` adiado) e roda o skill do pool configurado **na conferência**. Até aqui ele só
era **disparado** no caminho com humano (`process_contact_event` / native-specialist com
`pending_on_human_end`). Um contato resolvido **só por IA** (ex.: `sac_ia` sem escalar) fecha por
outro ponto — `process_routed`, conclusão do primário IA (`_part_role=="primary"`) — que chamava
`_trigger_contact_close()` direto, **sem** dar a chance ao hook.

**Mudança (completude do mecanismo):** nesse ponto, quando o primário IA conclui com
`outcome=="resolved"` (sinal "cliente presente no fim" do fluxo só-IA) **e** o pool declara
`hooks.on_contact_end`, o bridge grava o pre-hook context (`close_origin=flow_complete`) e dispara
`fire_pool_hooks("on_contact_end")` + `_hook_timeout_guard("on_contact_end")` em vez de fechar direto.
Demais outcomes (failed/abandoned/timeout/escalação) ou pool sem hook → `_trigger_contact_close()`
como antes. Toda a maquinaria de posatt é a **mesma** da Mudança 13; o fixed-side participant
(customer) vem de `session:{id}:customer_participant_id`, presente nas sessões IA.

**Não é lógica de survey.** O que o hook faz (NPS in-conference, skip no disconnect, ou até disparar
um outbound) é decisão do **skill** do pool — customização da instalação. A plataforma só dispara o
hook e segura a sessão. Princípio: skills são customizáveis, não regra de plataforma.

**Pool reutilizado + grão por contexto.** Usa-se o **mesmo pool `nps_ia`** do humano (já bootstrapado),
não um pool novo. O skill `skill_nps_v1` ganhou um step `escolher_grao` (choice): se
`@ctx.session.surveyed_segment_id` existe (humano — carimbado pelo bridge) → grão **segment**
(atribuível ao agente); senão (contato só-IA, sem segmento humano) → grão **session** (origin = a
própria sessão). Um pool, dois grãos. Carimbar o segmento do primário IA para habilitar `grain=segment`
por `deploy_version` no caso IA é evolução futura.

**Config:** `sac_ia.hooks.on_contact_end = [{pool: nps_ia, side: customer, nps_on_disconnect: skip}]`;
o step `disparar_survey` foi removido do `skill_atendimento_sac_v1` (NPS agora é o hook). A survey
OUTBOUND (`skill_survey_v1`) permanece como **padrão de skill para caso especial** (fluxo multi-humano),
iniciada pelo skill, não pela plataforma.

**Aplicação:** o pool `sac_ia` já existe no DB (seed-if-absent/DB-owned) → a hook nova **não**
auto-aplica no rebuild; aplicar via `PUT /v1/pools/sac_ia` (publica `registry.changed`, hot-reload) ou
`REGISTRY_SYNC_RECONCILE=true`. `nps_ia` já tem instância (reuso). **Site:**
`orchestrator-bridge/main.py` `process_routed` (bloco "Primary AI agent complete").

---

*Este documento é a referência canônica para o mecanismo de conferência do PlugHub.*
*Qualquer mudança no funcionamento deve ser registrada neste arquivo antes de ir para CHANGELOG.md.*
