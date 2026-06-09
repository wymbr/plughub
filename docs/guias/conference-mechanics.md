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

*Este documento é a referência canônica para o mecanismo de conferência do PlugHub.*
*Qualquer mudança no funcionamento deve ser registrada neste arquivo antes de ir para CHANGELOG.md.*
