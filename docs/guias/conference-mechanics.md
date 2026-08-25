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
| `session:{id}:participants` | 7 dias | Bridge (`_publish_participant_event` → `_upsert_participant_roster`, **Lua atômico**) | mcp-server (`session_context_get`, `resolveParticipantRole`), session-replayer (`ReplayContext.participants`) | **Roster** — array JSON de `Participant` (§1055 Fatia B, 2026-08-05). Fonte do PAPEL DE PARTICIPAÇÃO, que é fato de *(participante, sessão)* e por isso nunca coube no hash da instância. TTL 7 d porque o replayer o lê DEPOIS do `session_closed`, para montar o contexto de avaliação. **Upsert atômico é obrigatório**: o bridge despacha com `create_task` e não preserva ordem (§ 7b), logo dois joins concorrentes num read-modify-write em Python perderiam entradas em silêncio. Não guarda `duration_ms` — presença (`joined_at`→`left_at`) ≠ duração de segmento; para agente nativo os dois divergem porque a presença atravessa o suspend |

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

## 7b. Ordenação de eventos no bridge — não existe (medido 2026-08-05)

Fato que precisa estar escrito antes de alguém desenhar em cima de uma garantia que não há:

> **O bridge não preserva ordem entre eventos Kafka — de tópicos diferentes NEM do mesmo tópico.**
> Um único consumidor assina os seis tópicos e o laço é
> `async for msg in consumer: asyncio.create_task(_dispatch(...))` (`main.py` §9021), sem `await`.
> Cada mensagem vira uma corrotina concorrente; a ordenação por partição do Kafka termina nessa linha.

Consequência viva: o desmonte da presença do humano (`contact_closed(agent_release_item)` →
`DEL session:{sid}:human_agent`, §6841) corre em paralelo com o guard de dedup do `process_routed`
(§3517, que descarta routed SEM `conference_id` quando a presença existe). Um re-claim processado
antes do desmonte é engolido: vaga gasta, nenhum cartão — o achado 2, em forma transitória.

**Medido** (`infra/test/probe_release_reclaim_race.sh`): desmonte em **~30 ms**; re-claim
back-to-back a **~15 ms**; **0 engolidas em 5/5**. O que protege **não** é ordem, e sim o
`contact_closed` ser publicado no início do release enquanto o routed depende de release-responder +
claim-ir-e-voltar. É um **offset de publicação**, incidental. Sob carga, o `create_task` pode atrasar
o handler de `contact_closed` enquanto o de routed corre — o probe roda ocioso e não cobre isso.

Ao desenhar qualquer caminho novo que reivindique logo após soltar, assumir que **os dois handlers
correm juntos** e que a proteção é uma diferença de tempo de publicação, não uma garantia.

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

### Mudança 24 — sessão presa em `active` no customer-disconnect quando o único hook de cliente é `nps_on_disconnect=skip` (2026-07-03)

**Sintoma.** Quando o **cliente** (webchat) encerra o contato, o wrap-up (`on_human_end`,
side=agent) roda e completa, os dois segmentos aparecem `closed` no drill-down, **mas a sessão
permanece `active`** por até `_HOOK_TIMEOUT_S=180s` — só então o safety net force-close a fecha
(via `_hook_timeout_guard`, com `logger.warning`). No fim pelo **agente** o fechamento é limpo e
imediato. Config do pico: `retencao_humano.hooks` = `on_human_end→wrapup_ia (Agent)` +
`on_contact_end→nps_ia (Customer, nps_on_disconnect=skip)`.

**Causa raiz — assimetria entre os dois caminhos de fechamento.** O fechamento da **camada de
contato** (`active→closed`, publica `conversations.session_closed`) é feito **exclusivamente** por
`_close_contact_layer()`. O `_destroy_conference()` só derruba os **segmentos** — por isso os
segmentos fechavam e a sessão não.

- Caminho **`agent_done`** (correto): computa `_has_customer_hooks` e, se **nenhum** hook de cliente
  vai rodar, chama `_close_contact_layer()` **imediatamente** (espelho em `main.py` ~5760). Como o
  cliente não caiu, `nps_on_disconnect=skip` não se aplica e o NPS roda → fecha via
  `posatt:customer_active→0`.
- Caminho **`client_disconnect`** (bug): disparava `on_human_end` + `on_contact_end` **sem** o guard
  `_has_customer_hooks` e **sem** nenhuma chamada a `_close_contact_layer()`. Dentro de
  `fire_pool_hooks`, a entrada do NPS (`side=customer` + `nps_on_disconnect=skip` +
  `close_origin=customer_disconnect`) é **pulada** → `posatt:customer_active` nunca é incrementado →
  o `DECR→0` que dispara `_close_contact_layer()` **nunca ocorre**. Agravante: o contador
  `hook_pending:on_contact_end` era armado **incondicionalmente** por `len(hook_list)` (antes do
  loop de skip) → ficava **órfão** em 1 e só o guard de 180s o zerava (era o único a fechar a sessão,
  de forma degradada). Não era o skill-flow de NPS "não tratando o cenário" — com `skip` o NPS
  **nem é instanciado**; a decisão é do bridge.

**Correção (transporte, não regra de negócio) — dois pontos complementares:**

1. **`fire_pool_hooks` — contador sem órfão.** `close_origin` é lido **uma vez** no topo
   (`_close_origin_val`); o `hook_pending:{hook_type}` passa a ser dimensionado pelas entradas que
   **realmente serão disparadas** (`_entry_will_dispatch`, que reproduz os predicados de skip), e
   não é armado quando esse total é 0. O skip por-entrada do loop usa o mesmo `_close_origin_val`
   (skip e contagem coerentes). Beneficia todos os callers; o caminho `agent_done` fica
   byte-equivalente (nada é pulado quando `close_origin≠customer_disconnect`).

2. **Caminho `client_disconnect` — fechamento determinístico.** Após `_write_pre_hook_context`,
   computa-se `_cs_customer_will_run` (existe alguma entrada `side=customer` que NÃO é
   `nps_on_disconnect=skip` nesta queda?). Se **não**, chama `_close_contact_layer()`
   **imediatamente** — espelhando o guard do `agent_done`. O disparo de `on_contact_end` (e seu
   `_hook_timeout_guard`) passa a ser **gated** por `_cs_customer_will_run` (evita dispatch/guard
   órfãos quando tudo é skip). Casos: **tudo skip** → sem dispatch, fecha já; **misto** → dispara os
   que rodam, fecha via `posatt:customer_active→0`; **sem hook de cliente** → fecha já. Idempotência
   preservada (`_close_contact_layer` tem guard NX `contact_close_fired`; convive com o fan-out de
   peers e com o guard de 180s como rede).

**Sites:** `orchestrator-bridge/main.py` — `fire_pool_hooks` (arming do contador + skip por-entrada)
e `process_contact_event` (bloco de dispatch de hooks no `client_disconnect`).

**Segunda causa (analytics-api) — reabertura da sessão pelo routing do hook.** Após o fix do bridge,
os logs confirmaram que o `_close_contact_layer` fecha corretamente (`contact_closed` publicado), mas a
sessão voltava a `active`. Motivo: a tabela `analytics.sessions` é `ReplacingMergeTree()` **sem coluna
de versão** (last-inserted-wins, assume ordem causal do Kafka). O `parse_routed`/`parse_queued`
escreviam uma linha de `sessions` (pool + `closed_at=NULL`) para **todo** routing — inclusive o do
agente de hook (wrap-up `pool=wrapup_ia`). Com o fechamento agora **imediato**, o `contact_closed` é
publicado ANTES do wrap-up rotear; a linha `routed(wrapup_ia, closed_at=NULL)` entra **depois** da linha
de close → reabre a sessão (status `active`, pool exibido = pool do hook). O caminho `agent_done` não
sofria porque lá o close é o último evento (após NPS/posatt). **Correção:** `parse_routed`/`parse_queued`
**não escrevem a linha de `sessions`** quando `result.conference_id` está presente — routing de hook/
especialista é fato de **segmento** (já rastreado em `conversations.participants → segments`), nunca do
contato. Só a alocação do **primário** (sem `conference_id`) é dona da linha de `sessions`. Corrige também
o pool exibido em qualquer cenário de hook. Site: `analytics-api/models.py`.

---

### Mudança 25 — hooks de finalização `dispatch: detached` (Camada D do detach de hooks, 2026-07-24)

**Contexto.** Hooks de finalização (`on_human_end`/`on_contact_end`/`on_process_end`) só podiam rodar
**inline** — como especialista convidado na conferência viva, o que **segura o contato** até o hook
concluir (barrier `hook_pending`/`posatt`; trata `suspended` como concluído → fecha cedo). A razão de
segurar é **atribuição**, resolvida por referência de segmento + Journey **sem** segurar. A Camada A
adicionou `dispatch: "inline"|"detached"` ao `PoolHookEntry` (default inline; parse rejeita detached em
`on_human_start`). Esta Camada D faz o **bridge honrar `detached`**.

**Comportamento.** Para uma entrada `dispatch: detached`, `fire_pool_hooks`:
1. **Não** convida especialista de conferência (não publica o `conversations.inbound` sintético) e
   **não arma nada do barrier** — `hook_conf`, `posatt:active`/`posatt:customer_active`, `wrap_up_pending`,
   participants SET. O `_entry_will_dispatch` passa a retornar `False` para detached, então o
   `hook_pending:{hook_type}` também **não** o conta (contá-lo travaria o contato até o force-close de 180s).
2. Dispara um **workflow webhook fire-and-forget** via novo helper `_fire_detached_hook`
   (`POST {CHANNEL_GATEWAY_URL}/v1/channels/webhook/pool/{target_pool}`, novo env `CHANNEL_GATEWAY_URL`):
   `origin_session_id = session_id` + `journey: "inherit"` (a sessão-filha **herda o `root_session_id`**
   transitivo → membro da mesma journey) + `context` com a **referência de segmento**
   (`session.surveyed_segment_id`/`surveyed_agent_key`, `session.close_origin`, `hook.type`,
   `hook.origin_pool`). O agente destacado lê `@ctx.session.surveyed_*` e grava atribuído ao segmento —
   **sem** ser fisicamente um segmento da conferência. Não-2xx/erro é **logado** (degradação nunca silenciosa).
3. **Fecha o contato na hora** quando a leva de finalização é **100% detached** (`_detached_fired and not
   _inline_dispatched` e `hook_type in {on_human_end, on_contact_end, on_process_end}`): chama
   `_trigger_contact_close` (= `_close_contact_layer` + `_destroy_conference`, guards NX idempotentes),
   espelhando o caminho **sem-hook**. É isto que fecha **G1** (AHT deixa de inflar pelo wrap-up) e generaliza
   **G7** (desacople de `on_human_end`). Congela as estatísticas na saída do cliente.

**Guardas de fecho ajustadas (`_has_customer_hooks`).** Nos dois call sites que decidem fechar o WS do
cliente na hora (caminho IA-primário `on_process_end`/`on_contact_end`, e caminho humano `agent_done`), um
hook **detached NÃO conta como "customer hook"** — ele não segura o WS (vai por veículo outbound/webhook).
Sem esse ajuste, um `on_contact_end` detached deixaria `_has_customer_hooks=True` → o contato ficaria
eternamente `active` esperando um `posatt:customer_active` que nunca é incrementado (o mesmo modo de falha
da Mudança 24). Agora só **inline** segura.

**Mix inline+detached.** Suportado: entradas inline seguem armando o barrier e dirigindo o fecho diferido
por contador; entradas detached só disparam o webhook e não tocam contadores. O auto-close imediato só
ocorre quando **nenhuma** entrada inline foi disparada na leva.

**Limitações registradas (fora do escopo da Camada D):** (a) **`post_human` + `on_human_end` 100% detached**
— `post_human` é encadeado à conclusão dos agentes inline de `on_human_end`; se todos forem detached, nenhum
agente inline conclui e o `post_human` não dispara (config incomum; a migração da Camada E não usa post_human).
(b) **`segment_wrapup` detached no fan-out de customer-disconnect** — o `segment_wrapup` (fim-de-segmento,
contato continua) não recebe auto-close e não arma `contact_close_pending` quando detached; a coordenação do
teardown multi-humano detached fica para a Camada E (validação E2E). Sites: `orchestrator-bridge/main.py`
(`_fire_detached_hook`, `fire_pool_hooks`, os dois `_has_customer_hooks`); `docker-compose.demo.yml` (env).

### Mudança 26 — wiring do wrap-up detached no `on_human_end` + guarda do NPS irmão + teardown do Console (Camada E2, 2026-07-27)

**Contexto.** A Camada D (Mudança 25) fez o bridge honrar `detached`, mas o wrap-up destacado ainda dependia
de trigger manual (smoke). Esta mudança liga o `on_human_end` do `retencao_humano` ao `wrapup_detached_ia`
(`dispatch: detached`) — o wrap-up dispara **sozinho** no fim do atendimento — e corrige duas regressões que
o wiring expôs.

**Wiring.** `fire_pool_hooks` já computava `_hook_human_seg_id`/`_surveyed_agent_key` e semeava o `seg_signal`
(`_seed_segment_signal`); o gap era o `_fire_detached_hook` passar `origin_session_id` só no top-level do body.
Agora ele **também injeta `session.origin_session_id` no `context`** — o workflow de wrap-up lê
`@ctx.session.origin_session_id` no briefing (transcrição) e no `segment_outcome_record`. E2E validado com
atendimento real (`Detached hook fired … → wrapup_detached_ia` + gravação no segmento real).

**Regressão 1 — NPS do cliente derrubado (corrigida).** O fim-de-atendimento dispara DUAS levas em paralelo:
`on_human_end` (wrap-up, agora detached) e `on_contact_end` (NPS, **inline** side=customer). Como a leva
`on_human_end` ficou 100% detached, o **auto-close da Camada D** (`_detached_fired and not _inline_dispatched`)
disparava `_trigger_contact_close` e **derrubava a conferência/WS antes do NPS irmão armar o
`posatt:customer_active`** → cliente sem NPS. Fix: **guarda de irmão inline** no auto-close — quando
`hook_type=="on_human_end"`, se o pool tem um `on_contact_end` **inline+customer que VAI rodar** (aplica o
mesmo skip do `nps_on_disconnect` vs `_close_origin_val`), o auto-close é **suprimido**; o NPS segura o WS e
conduz o fecho no seu término (como antes do detach). Sem NPS (ausente/pulado por desconexão), o auto-close
segue. Log: `auto-close SUPRIMIDO; o NPS conduz o fecho`.

**Regressão 2 — Console mostrava banner de wrap-up inline p/ wrap-up detached (corrigida).** O `handleClose`
setava `sessionClosed=true` otimista → banner "Wrap-up in progress" + sessão presa até o fecho final. Errado
p/ detached (não há prompt inline; o wrap-up é item de fila pull). Fix: `POST /api/agent_done` resolve o **modo
do wrap-up do AGENTE** (side=agent do `on_human_end` do pool via `GET /v1/pools/:id`; fail-open inline) e
retorna `inline_wrapup`. O Console: **inline** → mantém o banner + sessão aberta (legado inalterado);
**detached** → **limpa a tela de atendimento na hora** (remove o contato + desseleciona), sem banner — o
cleanup final (`unregisterSession`) vem no `session.closed(agent_done)` que o bridge ainda emite ao fim do NPS.
Só o wrap-up do agente rege o banner; o NPS (side=customer) renderiza no WebChat do cliente, não no Console.

Sites: `orchestrator-bridge/main.py` (`_fire_detached_hook` +ctx origin; guarda `_sibling_customer_hold` no
auto-close); `mcp-server-plughub/server.ts` (`/api/agent_done` → `inline_wrapup`); `platform-ui`
(`AgentAssistPage.handleClose`; i18n `message.closedDetached`); `infra/registry/tenant_demo.yaml`
(`retencao_humano.on_human_end` → `wrapup_detached_ia dispatch: detached`).

### Mudança 27 — hand-off da vaga no wrap-up inline + identidade da instância no resume de delegate-conference (2026-07-27)

**Contexto.** Phase 2 do wrap-up unificado: no modo `inline` a ocupação da instância OSCILAVA entre o fim do
contato (release da vaga no `agent_done`) e o auto-claim do wrap-up (~2-3 s do poll da inbox). A max_concurrent=1
um contato push podia tomar a vaga na janela — o agente recebia contato novo com wrap-up pendente. O E2E do
hand-off destravou **dois defeitos pré-existentes** no ciclo de conferência do pull, ambos corrigidos aqui.

**Hand-off da vaga.** A vaga da origem não é mais LIBERADA no close quando há wrap-up inline seguindo: é
**trocada por um HOLD** (swap net 0) que o auto-claim do wrap-up **herda**. Ocupação nunca oscila.
- Ocupante do hold: `__wrapup_hold__::{origin_session_id}::{pool_id}::{expires_at_ms}` no SET
  `{t}:instance:{iid}:sessions` (prefixo não colide com `{session_id}::`, então o release por prefixo de
  sessão nunca o remove). O campo `{pool_id}` entrou na fatia F1 de capacidade compartilhada (2026-08-02):
  o pool é **sempre o 3º campo `::`** nos dois tipos de membro — occupant
  `{session_id}::{conference_id}::{pool_id}` e hold —, e no hold ele entra **antes** do timestamp
  justamente para não quebrar o único parse numérico do Lua (`::(%d+)$`). O hold **herda** a tag do
  occupant que substitui. Membro sem o campo = *untagged* (escrito antes do deploy; o SET tem TTL 24 h):
  conta na ocupação do recurso e em nenhuma projeção por pool.
- `_SWAP_TO_HOLD_LUA` (novo) + `_CLAIM_INSTANCE_LUA` (reescrita): todo claim **descarta holds expirados**
  (senão um wrap-up que nunca chega prenderia a vaga até o EXPIRE de 24 h do SET); só o claim `auto_attend` do
  **dono** herda um hold vivo. Tolerante às duas ordens de chegada.
- Decisão vem PRONTA no evento: o bridge carimba `keep_slot_for_wrapup` no `agent_done` do humano quando o pool
  tem `on_human_end` `side=agent` + `dispatch=inline` (`_has_inline_agent_wrapup`). O routing **não consulta
  hooks de pool** (invariante preservado).

**Defeito 1 — identidade da instância humana corrompida pelo resume (corrigido).** O claim é o último a escrever
`session:{id}:meta`, então `meta.instance_id` da sessão do WORKFLOW passa a apontar para o humano que reivindicou
o item. O `_handle_webhook_session_resumed` corrigia `agent_type_id`/`pool_id` pelo `wf_agent` mas **não o
`instance_id`** → o `agent_ready` publicado ao fim do resume **reescrevia a instância humana** com a identidade
do WORKFLOW (`agent_type_id=skill_*`, `current_sessions=0`, `max` do snapshot). Consequência observada: o
próximo contato roteado àquele humano chegava com `agent_type_id=skill_wrapup_detached_v1` e o bridge rodava o
**wrap-up na sessão do CONTATO**, que completava na hora e o fechava — o contato da fila "sumia". Fix:
`session:{id}:wf_agent` passa a guardar também o `instance_id` do workflow, e um guard descarta qualquer
`instance_id` `human-*` no resume (log warning; o resume segue sem snapshot de instância).

**Defeito 2 — a vaga do claimante só voltava por efeito colateral (corrigido).** Quem devolvia a vaga do humano
no fim do wrap-up era justamente o `agent_done` publicado com a instância trocada (Defeito 1). Corrigir o 1
removeria a devolução → vaga presa. O resume passa a publicar um `agent_done` **explícito para o claimante**
(`conversation_id` = sessão do workflow, `pools` lidos da instância dele) — e **só `agent_done`, nunca
`agent_ready`**: é o `agent_ready` que corrompe a identidade.

Sites: `routing-engine/registry.py` (Lua + `swap_to_hold` + `remove_conversation(hold_for_wrapup)`),
`kafka_listener.py` (`keep_slot_for_wrapup` → TTL `routing_config.wrapup_hold_ttl_s`, default 90 s),
`router.py` (`can_inherit_hold` no `work_task_claim`), `orchestrator-bridge/main.py`
(`_has_inline_agent_wrapup`, carimbo no `agent_done`, `wf_agent.instance_id`, guard do claimante, `agent_done`
do claimante), `platform-ui` (`key={sessionId}` no `DialogFormRenderer`/`ApprovalPanel` — o estado do form
grudava ao trocar de tarefa). Testes: `test_instance_semaphore.py` (+8 casos), `smoke_wrapup_slot_handoff.sh`.
E2E validado 2026-07-27 (4 contatos, 1 na fila, wrap-ups respondidos, fila drenada sem perda).

### Mudança 28 — identidade por-pool do agente humano: liveness ≠ identidade (F1, 2026-07-27)

Fecha a **causa** que a Mudança 27 deixou aberta ("o SINTOMA foi corrigido […]; a CAUSA — fato por-pool
morando em campo global — continua"). ADR: [`adr-human-agent-pool-scoped-identity`](../adr/adr-human-agent-pool-scoped-identity.md).

**O motor.** Um humano tem UMA instância (`human-{userId}`) e **N conexões WS** — o Console abre uma por
pool selecionado. Cada conexão mandava, a cada 15 s, um `agent_heartbeat` com `agent_type_id:
human_agent_${poolId}` e `pools: [poolId]` (a identidade **daquela conexão**), e o `_upsert_instance`
reconstruía o registro **inteiro** a partir do evento. `pools[]` e `agent_type_id` oscilavam conforme
quem pingou por último — e é esse `agent_type_id` que vira `conversations.routed.agent_type_id`, com o
qual o bridge escolhe **o que executar**.

**Regra.** *Evento de liveness prova apenas que o recurso está vivo: nunca carrega identidade nem
membership, e nunca cria instância.*

- `agent_heartbeat` perdeu `agent_type_id`/`pools`/`current_sessions` e ganhou **`heartbeat_pool`**
  (diz de qual conexão veio o sinal **sem se passar por membership**).
- `_upsert_instance` preserva os fatos de RECURSO do registro vivo; `pools[]` só muda em `agent_ready`
  (login manda `mergedPools`, logout parcial manda `remainingPools`). Defesa no **consumidor**: produtor
  legado é ignorado com log.
- **Registro ausente + pong ⇒ não recria.** Uma aba esquecida pingando após o logout completo (que faz
  `DEL` da chave) criaria **agente fantasma**: presente para o roteamento, ausente para o humano, com
  contatos alocados que não aparecem em Console nenhum.
- **Buraco correlato fechado:** `set_instance` só percorre os pools que a instância ainda declara, então
  o pool abandonado num logout parcial só era limpo do SET de roteamento pela escrita **direta** do
  `unregisterHumanAgent` (mcp-server) — o consumidor dependia de efeito colateral de outro serviço.
  Novo `remove_from_pool_sets()`.
- Instrumentação permanente: `membership SHRANK` e `agent_type_id divergence IGNORED`.

Sites: `mcp-server-plughub/src/server.ts` (pong + logout parcial), `routing-engine/kafka_listener.py`
(`_upsert_instance`, `_is_human_instance`, meta espelhando o registro), `registry.py`
(`get_instance_raw`, `remove_from_pool_sets`). Testes: `test_human_instance_identity.py` (11) +
`infra/test/smoke_human_instance_identity.sh` — ambos verdes 2026-07-27. **F2–F5 pendentes** (ADR §3/Q5).

---

### Mudança 29 — @mention por implementação única, com o pool do remetente como parâmetro (F5, 2026-07-28)

Fecha o arco de identidade por-pool (F1–F5). ADR:
[`adr-human-agent-pool-scoped-identity`](../adr/adr-human-agent-pool-scoped-identity.md) § B6.

O `@mention` é o convite mais barato à conferência: um alias resolvido publica dois eventos em
`conversations.inbound` — dispatch para um especialista **já ativo** e um `ConversationInboundEvent`
com `conference_id` para o Routing Engine **alocar** quem ainda não está. Quem decide **quais** aliases
existem é o `mentionable_pools` do pool **do remetente** — e era aí que estava o defeito.

**Havia duas implementações.** A do WS de agente (Console) resolvia o pool pelo query-param da conexão
— correto por construção, porque há uma conexão WS por pool selecionado. A da tool MCP `message_send`
resolvia pelo `pool_id` **global** da instância, com `HGET` contra uma chave que é String JSON: o
WRONGTYPE caía num `catch {}` e a menção morria em silêncio. Como o Console não passa por lá, o defeito
nunca teve caminho vivo — e por isso sobreviveu.

**Correção estrutural:** o roteamento virou módulo único (`lib/mention-routing.ts`) com o pool do
remetente como **parâmetro**. Cada superfície resolve no escopo que conhece — a conexão, no WS; o
registro por-(sessão, instância) `session:{sid}:routing:{iid}`, na tool MCP (mesmo fato que a F3 já
lia para decrementar o `active_count` certo). Nenhum caminho que deixa de rotear é mudo.

**Terceira metade:** o gate de `role` do `message_send` lia a mesma chave errada e falhava **aberto**
(`role` sempre `"primary"`), de modo que consertar só o pool teria autorizado agente de IA a convidar
pool — violando o invariante "IA nunca emite @mention". O gate passa a exigir leitura positiva.

Sites: `mcp-server-plughub/src/lib/mention-routing.ts` (novo), `lib/routing-ref.ts` (novo — leitor do
fato por-(sessão, instância), tolerante ao formato pré-F4), `server.ts`, `tools/session.ts`,
`tools/supervisor.ts` e `tools/bpm.ts` (estes dois liam o sub-documento `snapshot` que a F4 removeu —
leituras mortas silenciosas). **Arco F1–F5 completo.**

---

### Mudança 30 — logout de UMA conexão não apaga o recurso (unregisterHumanAgent, 2026-07-28)

`unregisterHumanAgent` roda **por conexão WS que fecha**, e o Console abre **uma conexão por pool**. O
evento prova só "esta conexão saiu deste pool" — escopo `(recurso, pool)`. O `DEL` do registro é escopo
**recurso**. Derivar o segundo do primeiro produziu, em 2026-07-28, um agente conectado sem registro:
`{t}:instance:human-{uid}` ausente com `{t}:pool:formfill_demo:instances` ainda listando o id →
`work_task_claim` = `instance_not_found`, pool "sem agentes", Console dizendo "Connected".

Três correções, e só a terceira é a causa: (1) membership ilegível bloqueia o ramo de full logout —
não se apaga por ignorância; o default antigo (`allPools = [poolId]`) transformava toda leitura falha
em "último pool". (2) O full logout faz `SREM` em **todos** os pools de `allPools`, em vez de delegar
a limpeza ao consumidor do `agent_logout`. (3) **Lost update** — um reload fecha as N conexões juntas,
as N chamadas leem o mesmo snapshot de `pools` e cada uma escreve o campo inteiro com "tudo menos o
meu pool"; vence a última, a perda é cumulativa e, quando sobra um pool, a chamada dele calcula
`remaining = []` e DELeta a instância de um agente conectado. A operação virou **EVAL (Lua)**:
"remova o MEU pool do conjunto", com `DEL`/`SET KEEPTTL` decididos dentro da mesma execução atômica.

> Regra que vale além deste site: **evento por-pool nunca reescreve o campo de membership inteiro.**
> Ele remove ou adiciona o próprio pool, atomicamente. Escrever o conjunto calculado a partir de uma
> leitura anterior é lost update esperando N conexões — e N é exatamente o número de pools do agente.

Órfão de pool set é especialmente traiçoeiro porque `get_ready_instances` **pula membro sem chave sem
evictar** (decisão deliberada, `registry.py:375-392`, para não causar off-by-1 no snapshot): ninguém
colhe o lixo e o pool parece povoado. Antes da **Mudança 28** (F1) o pong recriava a chave em 15 s e o
sintoma se apagava sozinho — o defeito é anterior, a F1 só parou de encobri-lo.

Instrumentação junto: `get_instance` e `get_ready_instances` deixaram de devolver `None`/`continue`
mudos; agora distinguem **chave ausente** de **chave inválida** no log (a UI recebe o mesmo
`instance_not_found` para os dois, e cada um pede uma ação diferente).

### Mudança 31 — encerramento do item de trabalho no resume (I5 núcleo A+B, 2026-07-30)

**Chave nova:** `{tenant}:work_task:{session_id}` = `{pool_id, queue_session_id, resume_token,
step_id, assigned_to, deadline}`, escrita pelo `handle_delegate_conference`/`handle_delegate` no
despacho e **consumida no `handle_resume`**. Ela existe porque o pool REAL do item não é derivável
depois: `session:{id}:meta` carrega o pool do WORKFLOW enquanto ninguém reivindica — mente
exatamente no caso que interessa — e `{t}:queue_contact:{sid}` morre por TTL antes do prazo.

**Fluxo novo no resume** (qualquer resume, não só timeout): lê o ledger → `POST
/v1/work_queue/expire` no árbitro (`Router.work_task_expire`: ZREM + JSON + lease + vaga) → apaga o
ledger. Idempotente: faz só o que restou.

**A ordem é load-bearing**, presa entre dois vizinhos: **depois** do check A5 (caller==claimant), que
lê a lease apagada aqui; e **antes** do publish de `conversations.inbound`, porque um flow pode
re-delegar no `on_timeout` — publicar primeiro criaria um item NOVO que a limpeza tardia apagaria, e o
sintoma seria uma tarefa sumindo da inbox sem ninguém a ter tocado.

**A vaga só volta quando há lease.** A lease é a evidência de claim de PULL; chamar `release_instance`
às cegas derrubaria o occupant de um contato PUSH alocado na mesma sessão.

**Premissa corrigida:** `delegate` roda **sempre** como especialista de conferência
(`persistDelegate` → `/delegate-conference`), logo o item na fila é a PRÓPRIA sessão do workflow e
`child_session_id == parent`. O `handle_delegate` roteado (uuid próprio) está inerte. `queue_session_id`
no ledger guarda o id que está DE FATO no ZSET, para o dia em que isso mudar.

**TTL do JSON da fila** passou a acompanhar o `work_item_deadline` do delegate (era fixo em 4 h contra
prazos de 24 h). Entre as duas marcas o membro do ZSET sobrevivia sozinho e o item seguia listado na
inbox **sem `assigned_to`** — perdendo o author-binding — e irreivindicável (`not_in_queue`).

**Segmento humano:** `close_reason` ganhou um terceiro valor. `task_submitted` (entregou) ·
`acw_expired` (prazo) · `acw_supervisor_closed` (supervisor encerrou). Os dois últimos têm
`outcome = None`, então é o `close_reason` que os separa.

### Mudança 32 — "Return to queue" desmonta a presença do humano (achado 2, 2026-08-05)

**Sintoma:** devolver N itens à fila e reivindicá-los de novo consumia as N vagas e **não gerava
cartão nenhum**. Reproduzido 4×, determinístico, com controle positivo: depois de um F5 — que passa
pelo desmonte completo — os mesmos 3 claims viravam 3 cartões.

**Causa:** o botão do Console chamava `work_task_release` **direto no árbitro**. A vaga voltava e a
presença ficava: `session:{sid}:human_agent` + `session:{sid}:human_agents`, escritos por
`activate_human_agent` e apagados só em caminhos de encerramento/queda. O guard de dedup do
`process_routed` — que existe para o drain periódico não gerar `participant_joined` repetido — então
descartava o `conversations.routed` do re-claim, indistinguível de uma re-emissão do drain.

**Transporte novo: `agent_release_item`.** Depois do `released: true` do árbitro, o mcp-server publica
`contact_closed(reason=agent_release_item)` em `conversations.events` — o mesmo par tópico/evento que
o `session_transfer` já usa para *"o segmento deste agente acabou, o contato continua"*. **Quem
escreveu o fato é quem o desfaz:** o mcp-server não toca chave de presença; o bridge desmonta pelo
caminho que já existia.

**No bridge**, o transporte entra em três lugares e sai por um quarto:
- `_TRANSPORT_TO_SEGMENT_CLOSE_REASON` (domínio de SEGMENTO — o contato não fechou);
- `_has_continuation` → `(True, "item_requeued")`. É o único caso em que `remaining == 0` **não**
  implica fim de contato: o item continua na fila. Sem esta linha o log diria `no_continuation` ao
  lado de um contato vivo;
- `agent_released` (não `agent_done`), `_resolve_hold=False`, junto com `agent_disconnect`;
- ramo próprio irmão do `agent_disconnect`, **antes** do marcador `session:closed`: retorna sem
  re-rota, sem `_release_work_item` (o árbitro já devolveu), sem `_mark_contact_ended` e sem
  `on_human_end`. Devolver não é encerrar, e congelar o AHT ali inventaria um fim que não houve.

**Por que `agent_released` e não suprimir o evento.** O árbitro já devolveu a vaga, então parecia
liberação dupla — o mesmo raciocínio que a Fase E fez e reverteu para o `agent_disconnect`. Não é
dupla: a ocupação é DERIVADA do `SCARD` do semáforo, logo o segundo SREM é idempotente. E só o
`remove_conversation` restaura a **membership** dos SETs do pool (SADD `ready_set` / SREM
`busy_set`), que o `work_task_release` não toca — sem o evento, o agente ficava invisível ao
roteamento por push depois de cada devolução. Era um segundo defeito da mesma raiz, e a correção
mínima (apagar as duas chaves) o teria deixado de pé.

**Não afrouxar o guard.** Uma exceção nele trocaria um caso mudo por outro (o spam de
`participant_joined` que ele existe para impedir). Corrigir o ESTADO torna o guard verdadeiro.

**Wrap-up de peer** exclui o novo transporte pelo motivo simétrico ao do `agent_disconnect`: lá o
humano sumiu, aqui ele está mas não há disposição a colher — devolveu sem atender.

**Consequência de leitura:** cada ciclo claim→devolução→re-claim gera um segmento humano a mais
(`segment_seq` incrementa por claim), agora todos fechados por `participant_left` com
`close_reason=agent_release_item`. São N janelas de participação reais, não órfãos.

**Janela residual conhecida:** `contact_closed` e `conversations.routed` são tópicos diferentes; a
ordem entre eles não é garantida. O anúncio é produzido antes de o HTTP do release responder, então
o intervalo é humano (segundos) contra dezenas de ms de Kafka. Um re-claim suficientemente rápido
ainda cairia no guard.

**Validado na tela (2026-08-05)**, com previsão escrita antes: probe de presença VERMELHO→VERDE no
MESMO item · 3× `Return to queue` e 3× `continuation=True (item_requeued)` no log · **0** linhas
`Skipping duplicate … human_active=True` · **3 cartões** no re-claim (antes: 0) · árbitro 3 × tela 3,
mesmos ids. Instrumento: `infra/test/probe_release_presence.sh` — item no ZSET não tem dono (o claim
é um ZREM), então item na fila COM marcador de presença é o defeito em estado puro; o veredicto fecha
sem entrada humana, e sai `INCONCLUSIVO` quando só há item virgem, porque aí o verde seria verdade
por construção.

---

### Mudança 33 — o dimensionador do barrier passa a compartilhar o predicado de despacho (2026-08-11)

**Sintoma:** aos 180 s de **todo** contato humano do demo, o log publicava
`_hook_timeout_guard: on_human_end hooks did not complete within 180s — remaining=1, force-closing
contact`, seguido de um `_trigger_contact_close` que não fazia nada.

**Causa — duas implementações da mesma regra.** O loop de `fire_pool_hooks` decide o veículo por
`dispatch == "detached" OU (side == "agent" E dispatch == "inline")`: desde o wrap-up unificado
(Mudança 26/27) o wrap-up roda pelo MESMO workflow destacado nos dois modos, e o que `inline` muda é a
ENTREGA (auto-atendimento com `auto_attend` × pull manual da inbox). O `_entry_will_dispatch`, que
dimensiona `hook_pending`, reimplementava a regra pela metade — só `dispatch == "detached"`. Com
`retencao_humano.on_human_end` = wrap-up **inline**, a entrada era CONTADA no barrier e depois
despachada para fora da conferência, onde nunca armaria o `hook_conf` que a decrementaria.

**O que o contador órfão NÃO fazia** (levantado antes do conserto, porque a hipótese natural é a
errada): não segurava o contato. Os três únicos pontos que tocam `hook_pending` são o DECR
(`main.py:4916`, chaveado pelo `completed_hook_type` do `hook_conf` que terminou — o NPS decrementa
`on_contact_end` e nunca lê `on_human_end`), o GET do `_hook_timeout_guard` (`:2081`, que **força** o
fecho quando `remaining > 0`) e a guarda anti-ghost-routing do webchat (`webchat.py:294`, onde
presença é conservadora). Nenhum é precondição de fecho. O `closed_at` do contato segue governado
pelo NPS irmão (Mudança 26) ou pelo auto-close de leva 100 %-workflow, e o AHT já estava congelado
antes dos hooks por `_mark_contact_ended` (`:7279`).

**O dano era o alarme.** Um aviso que soa em toda sessão torna o timeout de hook **verdadeiro**
indistinguível de ruído — o mesmo modo de falha do teste que não pode reprovar, do lado do alerta.

**Correção:** a regra virou `_is_workflow_dispatch_entry(entry)` (módulo, `main.py`), consumida pelo
loop **e** pelo dimensionador. A divergência fecha por construção; não depende de alguém lembrar de
editar os dois lugares.

**Risco conferido antes de aplicar:** sem o contador, a guarda de reconexão do webchat perde uma das 5
chaves durante a janela de hooks. Não descobre nada — `session:{id}:closed` é escrito em `:7267`
(ramo `agent_closed`/no_continuation) e `:6007` (ramo customer-side), nos dois casos **antes** de
`fire_pool_hooks`.

**Como ficar vermelho:** contato humano no `retencao_humano`; ao fim, `session:{id}:hook_pending:on_human_end`
deve estar **ausente**, e não deve surgir linha `did not complete within 180s` nos 200 s seguintes.
Sem contato humano na janela o veredicto é INCONCLUSIVO, não verde.

### Problema 34 — segmento de FILA que nunca fecha (diagnosticado, **NÃO corrigido**, 2026-08-17)

> Entrada de **problema**, não de correção. Está aqui porque toca o mecanismo de conferência e porque
> o custo de alguém redescobrir isto do zero já foi pago uma vez.

**Sintoma:** um contato **encerrado** exibe um segmento `role='queue'` com `ended_at IS NULL` — a UI
mostra `live` + `join` e o cabeçalho diz `1 active`. `SegmentList.tsx:96` deriva `live` de
`ended_at === null`, ou seja, a UI está honesta; o defeito é a montante. Dois casos medidos
(`61dd213c` em 2026-08-14 16:09, `05f4bc74` em 21:18), ambos em `retencao_humano`/webchat.

**A forma, idêntica nos dois:**

```
sac_ia            escala   → fecha  escalated_human   16:09:28.912
queue-{sid}       abre                                16:09:28.965   (53 ms depois)  ← nunca fecha
retencao_humano   humano assume                       16:09:41.037   → fecha resolved (80 s)
auth_form_ia / nps_ia  specialists                                    → fecham normalmente
```

O contato roda até o fim e TODOS os outros segmentos fecham. Só o de fila fica.

**Onde o par é publicado.** `process_queued` (`orchestrator-bridge/main.py`) publica os DOIS eventos:
`participant_joined` em `:5504` e `participant_left` em `:5552` — e o segundo só sai **depois** que
`activate_native_agent` (`:5523`) retorna. Esse `await` só volta quando o flow do agente de fila
completa. O routing publica um `participant_left` de fila **sintético** apenas no caminho de timeout de
fila muda (`routing/main.py:585`), que não é este caso.

~~**O caminho normal FUNCIONA** — 14 dos 16 segmentos `queue` do tenant fecham.~~
**Retirado em 2026-08-18 por medição** — a frase contava linhas sem olhar o que cada uma é. Ver abaixo.

---

#### Medição de 2026-08-18 — o agente de fila do pool que declara fila NUNCA rodou

*(Gates: `infra/test/probe_queue_segment_exit_paths.sh` · `infra/test/probe_family_a_queue_signal.sh`.)*

**Os 16 segmentos `queue` são DUAS populações, e só uma delas é fila de verdade:**

| população | pools | `queue_config` | outcome | fecha? |
|---|---|---|---|---|
| 12 | `formfill_demo_ia`, `limite_processo`, `aprovacao_credito`, `limite_ia` | **null** | `handoff` | 12/12 |
| 4 | **`retencao_humano`** | `{skill_id: skill_fila_v1, max_wait_s: 1800}` | **∅ (NULL)** | 2 em **3 ms / 6 ms**, 2 **nunca** |

O único pool que **declara** fila é o único em que ela nunca completa — 0 de 4. E a razão está em
`resolve_flow_for_agent` (`main.py:494-497`, mudança de 2026-07-13): produção = **snapshot do slot
`current` do POOL**, e `_activate_queue_agent` passa como `pool_id` o **pool de destino**. Duas
consequências, ambas medidas:

- **`queue_config.skill_id` não é consultado.** `retencao_humano` declara `skill_fila_v1` — que existe,
  está `published` e tem `flow` — e **não tem nenhum slot** (`previous`/`current`/`next` todos
  `set:false`). Com `ALLOW_LIVE_FLOW_FALLBACK` ausente no bridge, `resolve_flow_for_agent` devolve
  `None`, `activate_native_agent` devolve `{}` na hora, e o `left` sai com `outcome` NULL: **são os
  casos de 3 ms e 6 ms**. A config existe, aparece na UI e não executa nada — "valor plausível".
- **Os 12 `handoff` não são agente de fila.** Aqueles pools não declaram `queue_config`; entram pelo
  default de tenant (`main.py:5436`) e resolvem o slot do PRÓPRIO pool. O que rodou ali sob
  `role='queue'` foi o skill do pool, não um flow de espera.

**A ordem no código é o defeito estrutural:** o marcador (`:5504`) e o `participant_joined` (`:5527`)
são escritos **antes** de qualquer tentativa de resolver o flow, que só acontece dentro de
`activate_native_agent` (`:5546`). O segmento de fila nasce independentemente de o agente poder rodar
— por isso existe segmento `queue` de 3 ms que nunca enfileirou ninguém.

**O ramo do drain foi medido e NÃO é o discriminador.** Os dois órfãos trazem
`Queue drain: re-routing … (agent=… became ready, no queue agent active)` — o ramo ELSE de
`kafka_listener.py:707`, com o marcador ausente. Mas `fa2c7cfb` passa pelo **mesmo** ramo e **fecha**.
Além disso `signalled queue agent` = **0 no log inteiro** (12/08 → 18/08) contra 3 do ramo ELSE: o
caminho do sinal não é raro, **nunca rodou**. O candidato do `work_task_claim` (turno anterior) fica
sem suporte: o humano destes casos entrou por re-rota do drain, não por claim de inbox.

#### CAUSA RAIZ (2026-08-18, reproduzida) — `conversations.participants` é publicado SEM CHAVE

A reprodução ao vivo (sessão `dce98532`, instrumentação de marcador + TTL) desfez tudo o que parecia
ser o caso e nomeou o defeito:

- o marcador **foi** escrito (`marker SET … ttl=14400`) e **foi** apagado pelo dono
  (`marker DELETE … deleted=1`), 7 s antes do drain — o `ttl=-2` que o drain reportou era **honesto**.
  Não há marcador sumindo; o "fio aberto" da versão anterior desta seção não existia;
- o `participant_left` **ESTÁ no tópico Kafka** (`probe_participant_event_in_kafka.sh`:
  `queue-dce98532… joined=1 left=1`, ao lado de `sac_ia-001 joined=1 left=1` como testemunha);
- e mesmo assim ele não está em **nenhuma** das duas tabelas.

**O produtor publica sem `key`** (`main.py:3232` — `send_and_wait(TOPIC_PARTICIPANTS, payload)`) e o
tópico tem **3 partições** (`docker-compose.demo.yml:533`). Sem chave, o particionador espalha: o
`participant_joined` e o `participant_left` do MESMO segmento podem cair em partições diferentes, e
**a ordem do Kafka é por partição**. O consumidor (`getmany`) processa lote por partição, então pode
inserir o `left` ANTES do `joined`. Quando isso acontece:

| tabela | engine | quem vence |
|---|---|---|
| `segments` | `ReplacingMergeTree(ingested_at)`, `ingested_at DateTime` (segundo) | o `joined`, inserido depois ⇒ **`ended_at` NULL para sempre** |
| `participation_intervals` | `ReplacingMergeTree()` **sem coluna de versão** | o `joined`, inserido depois ⇒ **`left_at` NULL** |

As duas perdem pelo mesmo motivo, sem erro em lugar nenhum — que é exatamente o quadro medido. E o
DDL de `participation_intervals` (`clickhouse.py:350`) **escreve a premissa falsa em prosa**:
*"The 'left' event is always inserted after 'joined' (Kafka ordering)"*. Ordenação do Kafka é por
partição, e nunca houve chave; a premissa nunca valeu.

Encaixa em tudo o que estava solto: a intermitência (2 de 4 no `retencao_humano`, ~1,3% no tenant), a
sobre-representação de segmentos **curtos** (os dois eventos saem com 3 ms de diferença, então
atravessam o broker simultaneamente — é o caso de maior chance de inversão), e a família B, onde o log
provava publicação e nenhuma tabela tinha o evento.

**O conserto é de DUAS partes, e uma só não basta:**

1. **`key=session_id` no publish** — restaura a ordenação por segmento (necessária, não suficiente);
2. **coluna de versão que discrimine** — com ordenação garantida, `RMT(ingested_at)` em resolução de
   SEGUNDO ainda empata para eventos que distam milissegundos, e empate em RMT não tem vencedor
   definido. `participation_intervals` sequer tem coluna de versão.

Não repara as linhas já quebradas: o tópico ainda tem os eventos, então um reprocessamento é possível
— decisão à parte.

**O que o segmento aberto de fila NÃO custa:** `agent_time_ms` filtra
`role IN ('primary','specialist')` (`reports_query.py:1354`), então `queue` está fora **por papel**. O
dano é de UI e de contador de ativos, não de métrica de tempo de agente. *(A perda em `agent_time_ms`
vem de outra família — 7 casos em pools de workflow, ver `TODO.md` § "Segmento que nunca fecha".)*

**Hipóteses já descartadas por medição** (não redescobrir): corrida de ordenação entre tópicos (o par
vem do MESMO tópico `conversations.participants`, `clickhouse.py:376`); sobrescrita de
`ReplacingMergeTree`; retomada por prazo; concorrência da mesma instância. Detalhe e probes em
`TODO.md`.

**Como ficará vermelho quando alguém consertar:** contagem de `ended_at IS NULL` em sessões fechadas
**antes e depois**, com as três linhas de papel lado a lado (`infra/test/probe_open_segments_closed_sessions.sh`;
base atual `primary` 5 · `queue` 2 · `specialist` 2). A testemunha é obrigatória: um conserto que feche
o segmento de fila fechando **todos** os segmentos no `session_closed` passaria numa asserção ingênua
e destruiria a distinção entre segmento fechado por `agent_done` e fechado à força — os 14 que já
fecham têm de continuar fechando pelo caminho deles.

---

### Mudança 35 — o agente de FILA era surdo à mensagem do cliente: `""` caindo em lados opostos de duas guardas (2026-08-24)

**Sintoma:** cliente na fila digita, a mensagem aparece na tela dele, e o agente de fila **nunca
responde**. Nenhum erro em lugar nenhum. O `on_failure` do step `responder_cliente` volta para
`aguardar_mensagem` sem falar com o cliente, então a tela é indistinguível de "o agente ignorou".

**O que mede o caso** (sessão `c1cdcfc1`, reproduzida ao vivo):

```
menu:waiting:{sid}          → campo com NOME VAZIO, valor {"visibility":"all",…}
menu:result:{sid}:          → a fala do cliente PARADA na lista (dois-pontos final)
bridge log                  → agent=          key=menu:result:{sid}:            ← órfã
                              agent=sac_ia-009 key=menu:result:{sid}:sac_ia-009  ← funcionava
```

A lista **ainda existia**: um `BLPOP` teria consumido e apagado. Ou seja, escritor e leitor estavam em
nomes diferentes.

**Causa raiz — duas derivações do MESMO `ctx.instanceId`, com guardas incompatíveis:**

| lado | arquivo | guarda | com `""` |
|---|---|---|---|
| campo do hash | `skill-flow-engine/src/steps/menu.ts:211` | `ctx.instanceId ?? "_default_"` | `??` só pega null/undefined ⇒ campo **vazio** |
| chave do BLPOP | `skill-flow-engine/src/redis-keys.ts:28` | `instanceId ? …suffix : …` | truthiness ⇒ chave **sem sufixo** |
| leitores | `orchestrator-bridge/main.py:9180`, `mcp-server/server.ts:2471/2487/2497/3641` | `!== "_default_"` | `""` não é o sentinela ⇒ **sufixa com nada** |

O vazio é **legítimo e deliberado**: o agente de fila é o único ativado com `instance_id=""`
(`orchestrator-bridge/main.py:5952` — *"queue agents don't hold a routing slot"*, porque
`conversations.queued` traz `allocated=False`). Quem errou foi o `??`, não o bridge. E
`activate_native_agent` inclui `instance_id` no payload **incondicionalmente** (`main.py:898`, ao
contrário de `segment_id`/`journey_id`/`config`, todos condicionais), então o `""` atravessa intacto
os dois `??` (`engine.ts:421`, `menu.ts:211`) sem ser normalizado.

**Por que sobreviveu:** o sinal `__agent_available__` é publicado pelo routing na chave session-scoped
**hardcoded** (`kafka_listener.py:728`, `main.py:1415`), que coincide com o BLPOP. O agente de fila
ouvia *"chegou humano"* e era surdo ao cliente — **meia funcionalidade viva**, e a metade viva é a que
aparece na demo. Nenhum cenário e2e cobre a fila (`packages/e2e-tests/scenarios/` não tem nenhum; o 07
só chama `queue_context_get` direto por MCP, sem flow rodando).

**Correção:** `||` no lugar de `??` em `menu.ts:211` e em `resolve.ts:192` (cópia idêntica do mesmo
trecho). O campo passa a ser `_default_`, os leitores caem no ramo session-scoped, e as duas pontas
concordam.

**Teste** (`skill-flow-engine/src/__tests__/steps/menu.test.ts`): a asserção que importa é
**relacional** — *o campo é `_default_` se e somente se a chave do BLPOP não tem sufixo* —, varrendo
`""`, `undefined` e dois ids reais. Fixar só o literal de `waitingField` passaria com a chave errada.
O mock do teste também ganhou `hset`/`hdel`: o step os chama dentro de `try/catch`, então a ausência
virava exceção **engolida** e nenhum teste podia enxergar o registro em `menu:waiting`.

**Resíduo:** a assimetria `!== "_default_"` × truthiness continua nos 5 leitores; nenhum normaliza
`""`. Hoje não há produtor de campo vazio, mas o próximo caminho que ativar um agente nativo sem
`instance_id` reabre o mesmo buraco. Normalizar no leitor (tratar `""` como `_default_` e LOGAR) é
conserto de defesa em profundidade, ainda não feito.

---

### Problema 36 — abandono na FILA: o segmento que não nasce, a sessão que não fecha, e duas definições de "abandono" que discordam (medido 2026-08-21, **NÃO corrigido**)

Irmão do **Problema 34** (segmento de fila que não fecha) e lacuna da **Mudança 24** (sessão presa em
`active` no customer-disconnect). A Mudança 24 cobriu a queda do cliente **com humano atendendo** — os
dois guards que ela instalou moram no `agent_done` e no dispatch de hooks. Quando o cliente cai **na
fila, antes de qualquer humano**, não há `on_human_end`, não há `agent_done`, e nenhum dos dois
caminhos chega a rodar.

**Três defeitos distintos, medidos juntos e separados pelo contador.** Nada aqui é hipótese: cada
linha tem query ou arquivo:linha.

#### 36.1 — O "estado impossível" da tela é montado na LEITURA, não existe no dado

`sess-e2e-2920b0d1-…-c803d28a171a` aparece na lista de contatos como badge verde **`active`** ao lado
da palavra **`abandoned`**. A linha em `plughub_demo.sessions` tem `status`, `outcome`, `close_reason`
e `closed_at` **todos nulos**, em versão única. As duas palavras vêm de lugares diferentes:

| Palavra | Origem | Regra |
|---|---|---|
| `active` | frontend, `ListaTab.tsx:294` | `if (!row.closed_at)` — **não lê `status`** |
| `abandoned` | backend, `reports_query.py:895` | `COALESCE(NULLIF(s.outcome,''), _seg_out.outcome_v)` — cai no outcome do **segmento** |

Nenhuma das duas é bug sozinha. ⚠️ **Consequência para quem for medir isto:** procurar o par
`status='active' AND outcome='abandoned'` no ClickHouse devolve **zero**, e o zero é fabricado pelo
recorte — o instrumento certo é `closed_at IS NULL`. Foi exatamente esse erro que custou quatro
previsões erradas seguidas na sessão de medição.

#### 36.2 — A sessão com fila abandonada não fecha em 1 de cada 3 casos

Recorte correto (`closed_at IS NULL` × existe segmento `role='queue'` com `outcome='abandoned'`),
`tenant_demo`, população 522 sessões:

| | tem fila abandonada | sem fila abandonada |
|---|---|---|
| **nunca fechada** | **5** ← o defeito | 8 `active` + 3 nulas + 27 `suspended` |
| **fechada** | **10** ← a testemunha | 469 |

Query re-executável (é ela que tem de ser rodada antes e depois de qualquer conserto):

```sql
WITH q AS (
  SELECT session_id, countIf(outcome = 'abandoned') AS ab
  FROM plughub_demo.segments FINAL
  WHERE tenant_id = 'tenant_demo' AND role = 'queue'
  GROUP BY session_id
)
SELECT if(s.closed_at IS NULL, 'never_closed', 'closed')                  AS sess,
       coalesce(s.status, '(null)')                                       AS st,
       if(coalesce(q.ab, 0) > 0, 'queue_abandoned', 'no_abandoned_queue') AS segq,
       count() AS n
FROM plughub_demo.sessions AS s FINAL
LEFT JOIN q ON q.session_id = s.session_id
WHERE s.tenant_id = 'tenant_demo'
GROUP BY sess, st, segq ORDER BY n DESC
```

⚠️ `FROM t AS alias FINAL`, nunca `FROM t FINAL AS alias` — a segunda forma é erro de sintaxe no
ClickHouse 23.8.

**10 fecham e 5 não ⇒ intermitência, não produtor ausente.** Qualquer conserto tem de manter os 10
fechando pelo caminho deles.

> ⚠️ **Medido 2026-08-28 — a intermitência NÃO é explicada por duplicação de segmento de fila, e a
> hipótese nasceu de uma coincidência numérica.** Há **5** sessões com mais de um segmento `role='queue'`
> (4 com dois, 1 com três) e **5** sessões `never_closed` com fila abandonada. Mesmo número; **a
> interseção é 1**, não 5. Recorte completo, `INNER JOIN` sessão × fila, 46 sessões com fila:
>
> | duplicado? | sessão | fila abandonada? | segs abertos | n |
> |---|---|---|---|---|
> | single | closed | não | 2 | 24 |
> | single | closed | sim | 0 | 7 |
> | single | never_closed | não | 0 | 6 |
> | **single** | **never_closed** | **sim** | **0** | **4** ← o defeito 36.2, sem duplicação |
> | dup | closed | sim | 2 | 3 |
> | dup | closed | não | 0 | 1 |
> | dup | never_closed | sim | 1 | 1 |
>
> **Três leituras que a tabela força:**
> 1. **4 das 5 sessões que não fecham têm exatamente UM segmento de fila, e ele FECHOU** (`duration_ms`
>    não-nulo, `outcome='abandoned'`). O produtor rodou e terminou; a sessão é que ficou. Duplicação não
>    é a causa.
> 2. **4 das 5 sessões duplicadas FECHAM normalmente.** Duplicar não impede o fechamento — os dois
>    fenômenos são independentes, não dois sintomas de um.
> 3. **Os segmentos abertos são outra população ainda:** 5 no total, sendo **4 em sessões FECHADAS**
>    (2+2 na tabela — casa com o `queue 4` já medido) e 1 na única sessão que é dup **e** never_closed.
>    *Segmento aberto em sessão fechada* (Problema 34) e *sessão aberta com segmento fechado* (36.2) são
>    **inversos um do outro**, não variantes.
>
> **Consequência para qualquer plano:** o id determinístico da D12 conserta a duplicação (5 sessões) e
> **deve-se prever que ele NÃO mova o `never_closed = 5`**. Escrever essa previsão antes é o que impede
> a leitura *"consertei e não mudou nada, logo não aplicou"*. As 8 `active` e as 3 nulas **sem** fila abandonada são família à parte e
não devem ser misturadas neste item.

As 8 sessões `active` foram conferidas no Redis: **`keys=0`, `ttl=-2` em todas as 8**, contra 56
chaves `session:*` vivas (testemunha). Elas morreram no runtime e deixaram a linha aberta. Coerente
com isto: `close_reason` declara `session_timeout`, `no_resource` e `system_error` no domínio, e os
**três têm zero ocorrência** nas 522 linhas — promessa sem produtor. Na direção oposta, `agent_closed`
aparece 14 vezes e **não está no domínio**.

#### 36.3 — O reload na fila fecha bem, não deixa rastro de fila, e as duas telas discordam

Reproduzido ao vivo duas vezes (`e6056b6b…`, `11c288a9…`): cliente escalado do `sac_ia` para a fila,
recarrega a página enquanto espera. A sessão **fecha corretamente**, em 11 s e 24 s, com
`outcome='escalated_human'` e `close_reason='customer_disconnect'`. Tem **1 segmento** — o do `sac_ia`.
O segmento de fila **nunca nasce** (é a D12 do ADR: a espera não tem produtor).

O efeito que ainda não estava registrado não é a espera não medida, é o **abandono não contado**:

| Superfície | Define abandono como | Este caso |
|---|---|---|
| Lista de contatos (`ListaTab.tsx:276`) | `close_reason ∈ {customer_abandon, no_resource, max_wait_exceeded, **customer_disconnect**, customer_hangup, session_timeout}` | **exibe "abandoned"** |
| Relatório Fila/SLA (`reports_query.py:5762`) | entra com `q_count > 0`, conta com `q_outcome='abandoned'` | **invisível** — fora do numerador **e** do denominador |

Sem segmento de fila o contato não é "enfileirado" para o relatório que existe para medir fila. Nem a
taxa de abandono acusa, porque ele sai dos dois lados da fração ao mesmo tempo. Há 13 sessões com
`customer_disconnect` na população.

**Ordem de conserto.** 36.3 é pré-requisito dos outros dois: enquanto o segmento de fila não nascer,
não há onde pendurar nem o fechamento nem a contagem. ⚠️ Criar o produtor tem efeito colateral
declarado — reclassificar o agente de fila para `specialist` move o tempo dele para dentro de
`agent_time_ms` e muda TMA/AHT do ambiente (ver ADR §D9 e D12).

**Gate.** Os casos são reproduzíveis pelo e2e (`e2e-inbound-*`). Testemunha obrigatória em qualquer
correção: os **10** que fecham têm de continuar fechando, e os 469 `closed` não podem mudar de forma.

**Não-achado, registrado para não ser redescoberto:** durante a medição pareceu haver um quarto
defeito — "o contato não aparece na lista". Medido: a API devolve a linha no recorte default
(`alvo=1`), e depois de `Ctrl+Shift+R` a tela passou de 72 para 103 contatos com as linhas presentes.
Era render velho. O tell que quase passou batido é que o total **não tinha mudado** apesar de duas
sessões novas.

---

### Mudança 37 — a janela de espera ganha produtor, e o portão dele nasceu morto: `""` no lugar de ausência (2026-08-21)

Corrige o **36.3** (a espera que não tinha produtor) e, na mesma passada, um defeito que o próprio
conserto introduziu e que só apareceu porque se mediu a população que **não** devia ter linha.

**O que passou a existir.** `mute_queue.resolve_mute_exit` → **`resolve_queue_exit`**: a função já
estava plugada em todas as saídas de fila, mas se recusava a agir fora do tier mudo (abria com
`SREM(unadmitted)` + `return False`). A mudança foi **remover a recusa** — o `SREM` virou bookkeeping
e quem decide passou a ser o `first_queued_ms`, que existe nos dois tiers. Mais dois pontos de saída
que nenhuma das 4 chamadas cobria: `SessionClosedEventHandler` (o caminho do 36.3 — o handler não
tinha producer) e o drain com agente disponível (com agente de fila ativo o contato é *sinalizado* por
LPUSH, não re-publicado, então o roteamento não roda de novo).

**O defeito introduzido, e por que ele é da família catalogada.** O portão novo era:

```python
raw = _decode(await redis_client.get(fq_key))
if raw is None:          # ← nunca verdadeiro
    return False
```

`_decode` devolve **`""`** para chave ausente (`mute_queue.py:60-63`), nunca `None`. O `return False`
jamais disparava; logo abaixo, `int(float(raw)) if raw else now_ms` caía no `now_ms` e a subtração dava
zero. Resultado: **todo contato roteado direto — sem fila nenhuma — emitia um segmento
`role='queue' outcome='handoff' duration_ms=0`**, e ele aparecia na UI como a *primeira participação do
contato* (drill de sessão: `queue system · 0s · handoff`, antes do `primary`).

É a **Mudança 35 outra vez**: lá o `""` caía em lados opostos de duas guardas (`??` × truthiness);
aqui ele cai do lado errado de uma guarda só. Duas ocorrências no mesmo mecanismo em quatro dias ⇒
não é azar, é o vazio sendo tratado como valor em Python. Regra derivada: **guarda sobre retorno de
Redis testa `if not x`, nunca `is None`** — o decodificador do repo normaliza ausência para string
vazia, e `is None` compara com um valor que ele não produz.

**Medição** (3 contatos reais, mesma coorte antes e depois):

| caso | antes | depois |
|---|---|---|
| escalou → F5 na fila | `primary` + `queue/abandoned/47 327 ms` | **igual** |
| atendido até o fim, com NPS | `primary` + `specialist` + **`queue/handoff/0`** | `primary` + `specialist` |
| menu inicial + F5 | `primary` + **`queue/handoff/0`** | `primary` |

Linhas `role='queue'` na coorte: **3 → 1**. Taxa da fantasma antes do fix: **2 de 2** contatos sem fila.

⚠️ **A fantasma era invisível na query canônica do 36.2**, que conta `outcome='abandoned'` — a fantasma
é `handoff`. O verde daquela query era verdadeiro *e* incompleto ao mesmo tempo: testemunha correta
para o defeito que ela mede, cega para o que o conserto criou.

**`key=session_id`: a justificativa original caiu, mas a evidência que a derrubou caiu junto.** Dizia-se
*"este caminho emite um evento só, então não há par que possa se inverter"*. O log de um contato que
escala mostrou **três** `participant_left` para a mesma sessão, todos com o mesmo `segment_id`
determinístico — quem vence a dedup é quem chega por último, e isso só é determinístico **dentro de uma
partição**.

⚠️ **Duas daquelas três eram as fantasmas.** Com o portão corrigido, uma passagem pela fila emite
**uma** vez (a 1ª apaga o carimbo) e aquele caso deixou de ser reproduzível. *Registrado assim, e não
reescrito, porque a diferença entre "medi" e "medi antes de consertar o que produzia a medição" é
exatamente o que este documento existe para preservar.*

O que sobrevive: *"o evento é único"* segue falso por um caminho legítimo — sessão que passa por **duas**
filas (espera no pool A, transferência, espera no pool B) recebe dois carimbos e emite duas vezes. E a
chave continua exigida para ordenar contra os **demais** eventos da mesma sessão. Ver **Problema 34** (o
mesmo tópico, sem chave, custou o defeito mais caro do repositório).

🔎 **Resíduo que isto expõe, ainda não medido:** `queue_wait_segment_id` é `uuid5(tenant, session_id)`,
**sem discriminador de passagem nem de pool** — as duas esperas do parágrafo acima colapsam numa linha
só e vence a última. Mesma família do resíduo de `participation_intervals`. **Bloqueia a D14**: pôr o
alvo de SLA no segmento não adianta enquanto os dois segmentos forem a mesma linha.
✅ **Fechado pela Mudança 38 (2026-08-24)** — e com duas correções ao que está escrito acima: o caso
deixou de ser hipotético (foi produzido e medido) e o conserto **não** foi o pool, foi o
`first_queued_ms`.

**Gate re-executável:** `packages/routing-engine/src/plughub_routing/tests/test_queue_wait_segment.py`
(4 testes, Redis real, skip explícito lendo as duas variáveis de ambiente). O que cada um faria ficar
vermelho está no docstring. **Falseabilidade conferida**, não presumida: revertendo o portão para
`is None` dentro do container, o resultado é `1 failed, 3 passed` e o que falha é exatamente
`test_no_stamp_emits_nothing`. O `_FakeProducer` exige `key=` na forma real da chamada — mock mais
permissivo que o contrato já escondeu bug aqui antes.

**Resíduos declarados:**
- `_emit_queue_timeout` mantém emissor próprio (`emit_segment=False` no caminho de `max_wait`).
  Unificar agora produziria emissão dupla. **Lacuna:** `max_wait_exceeded` na fila **atendida** segue
  sem segmento de espera.
- A reclassificação do agente de fila para `role='specialist'` (bridge) move o tempo dele para dentro
  de `agent_time_ms`: medido `retencao_humano` 456 083 → 477 968 ms (**+4,8 %**), 27 sessões, 11 hoje
  em zero. **Não é retroativo** — linhas antigas mantêm `role='queue'`; o número deriva com tráfego
  novo.
- A linha `pool_id → _flow_pool_id` (D10) é **no-op** enquanto o `queue_config` do pool tiver só
  `skill_id`.
- **36.2 segue sem causa** (as 5 sessões que não fecham). Confirmado nesta rodada: a população ficou
  **imóvel em 5**, como previsto — a duplicação não a explica (interseção medida = 1).

---

### Mudança 38 — o segmento de espera discrimina a PASSAGEM: a premissa estava no docstring, não no mecanismo (2026-08-24)

**O que mudou:** `mute_queue.queue_wait_segment_id` passou a receber o `first_queued_ms` e a embuti-lo
no namespace do `uuid5`. Assinatura de 3 parâmetros, o terceiro **obrigatório**.

**Por quê.** O id era `uuid5(NAMESPACE_URL, "plughub:queue-wait:{tenant}:{session}")`, e a
justificativa vivia no docstring: *"uma sessão tem UMA passagem pela fila"*. **Falso.** Um contato que
espera, é atendido, é transferido e espera de novo emite dois `participant_left` em
`conversations.participants` com o **mesmo** `segment_id` — o `ReplacingMergeTree` guarda a última e a
primeira espera **deixa de existir**. Irrecuperável: o carimbo da passagem perdida é apagado na saída,
então nenhuma migração alcança a linha depois.

É a família já catalogada aqui no **Problema 34** (o DDL de `participation_intervals` *afirmando em
prosa* uma ordenação que nenhum produtor impunha) e na § Postura de Engenharia do `CLAUDE.md`. A
diferença de forma: lá era comentário de DDL, aqui era docstring de função. **Comentário que promete
invariante sem mecanismo que a imponha é a mesma dívida com outra roupa.**

**Medição — o caso foi produzido, não deduzido.** O resíduo estava registrado desde 08-21 como
*"não medido — não há caso de duas filas na população"*, e o instrumento confirmou: 3 saídas de fila no
log, nenhuma sessão repetida. Montado de propósito (`sac_ia` → escala → espera → humano assume →
transfere → espera → cliente cai):

| Sessão | Passagem 1 | Passagem 2 | Linhas `role='queue'` |
|---|---|---|---|
| `9403a14b-…6a937dae41c4` (antes) | `retencao_humano` `handoff` 24 118 ms | `especialista_onboarding` `abandoned` 85 009 ms | **1** (`4a539b95-458d-56a5-…` = `uuid5` previsto) |
| `27651d1b-…dc9a3d1a0c0c` (depois) | `retencao_humano` `handoff` 43 791 ms | `especialista_onboarding` `abandoned` 80 980 ms | **2** (`3cc267dd-…`, `b4d51ae4-…`) |

**O instrumento é um par**, e sozinho nenhum lado julga: saídas registradas no log do routing
(testemunha de PRESENÇA) × linhas sobreviventes no ClickHouse (contador de AUSÊNCIA). *"1 linha"* não
distingue colisão de "houve uma espera só". Probe: `infra/test/q_queue_collision_witness.sh`.

**O discriminador é o carimbo, e o pool foi recusado** — a proposta registrada em 08-21 dizia
*"incluir o `pool_id` de destino"*:

- `first_queued_ms` é fato da **passagem**: NX na entrada (`registry.py:2628`), DELETE na saída ⇒ a
  chave já *significa* "esta passagem". Re-enfileiramento dentro da mesma passagem preserva o valor
  (exigido por `test_release_preserves_first_queued`), logo o id segue estável onde precisa.
- `pool_id` é fato do **call site**: `main.py:286` emite com `event.pool_id or ""`. Duas saídas da
  mesma passagem por sites com pools diferentes dariam dois ids para uma passagem — matando a
  idempotência que a Mudança 37 comprou. E não separa duas esperas no mesmo pool.

**Descontinuidade de identidade declarada.** Linhas anteriores mantêm o id sem carimbo; nenhuma é
reescrita. Assinatura verificável: no contato de depois, o id pela fórmula antiga (`b20aa0f0-…`) não
bate com nenhuma das duas linhas.

**Gate:** `test_queue_wait_segment.py` **10 passed** (8 + 2). Os dois novos são um **par que não se
separa** — `test_two_passages_get_distinct_ids` ficaria verde com `uuid4()` de volta e
`test_same_passage_emits_the_same_id_twice` ficaria verde com o defeito antigo.

**Resíduos declarados:**
- **A tela não melhorou, e isso é esperado.** `reports_query.py:5752` colapsa a sessão numa linha
  (`anyIf(pool_id, role='queue')` / `anyIf(outcome, …)` / `maxIf(duration_ms, …)`), então a segunda
  linha é descartada na leitura: o relatório mostra 80 980 ms de 124 771 medidos. Onde as duas
  discordam, o `anyIf` **sorteia** — 2 de 5 sessões multi-linha já discordam hoje. Era resíduo
  histórico congelado; agora cresce com o tráfego. Conserto = **D14**, e **não é somar**.
- **O bridge tem a mesma colisão** (`main.py:5963`, namespace `queue-agent`), confirmada na mesma
  medição. Hoje inalcançável (exige dois pools com fila atendida). Fatia própria, com uma pergunta
  extra: o discriminador dele **não pode ser wall-clock**, ou a idempotência morre.
- `_emit_queue_timeout` passo 3 segue com `uuid4()` **e sem `key=`** — os dois defeitos que este
  emissor já pagou.
- `duration_ms` do segmento humano divergiu da janela dos próprios carimbos nas **duas** medições
  (26 448 ms/80 s; 25 519 ms/60 s). Não investigado; backlog fora do arco de fila.
  ⚠️ **Uma hipótese já foi levantada e REFUTADA** em 2026-08-24 (*"a duração sai do último F5"*) —
  ver Mudança 39. Não repetir esse caminho.

---

### Mudança 39 — N segmentos por contato NÃO são duplicação: hipótese levantada e REFUTADA no mesmo dia (2026-08-24)

**Nada foi alterado no mecanismo.** Registrado porque a aparência é convincente, o diagnóstico
errado é sedutor, e já custou uma rodada — quem vier depois vai ver o mesmo sintoma.

**Sintoma que dispara a suspeita:** o relógio de atendimento do Console zera a cada F5, e um contato
acumula muitos segmentos `primary` — medidos **8** em `18232569-…`, **6** em `af64c36b-…`, **6** em
`fb66eed5-…`. Teste do gatilho confirma: **7 → 8** e **2 → 3** após um único F5.

**Diagnóstico "óbvio" pela leitura do código, e é FALSO:** `activate_human_agent` roda de novo a cada
reconexão e `setex session:{sid}:segment:{inst}` sobrescreveria o id do segmento anterior — que
então nunca receberia `participant_left` e ficaria aberto para sempre.

**O número que refuta é `countIf(duration_ms IS NULL)`:** aquelas sessões têm **ZERO segmentos
abertos**. Todos fecham. A ordem real é **fechar-depois-abrir**, não abrir-sobre-abrir:

| reconexão | o que acontece | segmento |
|---|---|---|
| **> `UNREGISTER_GRACE_MS`** (2,5 s, `mcp-server/server.ts:2879`) | queda GENUÍNA → `contact_closed(agent_disconnect)` → segmento fechado + GETDEL da chave de join → contato volta à fila → re-rota → ativação nova | **novo, e correto** |
| **< grace** | o unregister é cancelado; o contato não volta à fila e `activate_human_agent` **não roda** — a tela é restaurada pelo replay de `pool:pending_assignment` (forward no mcp-server), que não cria segmento | **o mesmo** |

⇒ **N segmentos = N quedas reais = N janelas de participação**, que é a definição de segmento
(`CLAUDE.md`: *"segment = janela de UM participante"*). `assigned_at = now()` está **certo**: é o
início da janela nova. E o relógio zerando na tela está certo pelo mesmo motivo.

**Por que o guard "reusa o segmento se já há join aberto" foi escrito e revertido:** ele guarda um
estado inalcançável (se a chave sobreviveu, não houve segunda ativação para suprimir) e, no dia em
que disparasse, suprimiria um segmento legítimo. Código morto no melhor caso, defeito no pior.

⚠️ **Se um dia houver suspeita real de duplicação, o discriminador é SEGMENTO ÓRFÃO**
(`duration_ms IS NULL` acima de 1 por sessão viva), **nunca a contagem**. Contar segmentos e chamar
de duplicação é medir exposição e chamar de dano — o invariante que o `CLAUDE.md` ganhou nesta mesma
sessão, violado duas horas depois de ser escrito.

*Precedente coerente: o ADR `adr-work-item-requeue-and-agent-affinity` já decidira que tratar um F5
como abandono era bug — mas para item de fila pull, e por causa do grace. Aqui o grace **venceu**,
então a saída é real e o novo segmento também.*

---

### Mudança 40 — `max_wait_exceeded` passa a registrar espera nos DOIS tiers: um produtor, um parâmetro (2026-08-24)

**O que mudou no mecanismo:** o `_emit_queue_timeout` (routing-engine) **deixou de publicar segmento
de espera por conta própria**. A publicação passou inteira para o `mute_queue.resolve_queue_exit`,
que já era o produtor único das demais saídas de fila (`handoff`, `abandoned`) desde a D12.

**O que havia:** um segundo emissor, no ramo `else` do teste `queue:agent_active` — isto é, **só a
fila MUDA**. Na fila ATENDIDA, um contato fechado por teto de retenção saía sem nenhum segmento
`role='queue'`. Não é linha errada, é linha **ausente**: o relatório de Fila/SLA perdia exatamente a
população de que trata, sem sintoma próprio. Junto vinham `uuid4()` no `segment_id` (duas emissões =
duas linhas) e publish **sem `key=`** em `conversations.participants`.

**O que fechou:** `resolve_queue_exit` ganhou `close_reason` (era hardcoded `""`). Era o único campo
que o segundo emissor tinha e este não — e era o que sustentava a duplicação de código.

**Ordem, e ela é o mecanismo:** a chamada fica no **topo** de `_emit_queue_timeout`, antes de
`session:{id}:closed`. O DRAIN lê esse marker (`kafka_listener.py:695`) e chama o mesmo resolve com
`"abandoned"`/`close_reason=""`; emitir depois abriria corrida em que o drain carimba primeiro e o
motivo real vira abandono genérico. Emitindo antes, quem tem a informação escreve, e o drain encontra
o `first_queued_ms` já consumido ⇒ `False`, sem emissão. **É o carimbo que dá a idempotência, não um
guard.**

⚠️ **O aviso de "emissão dupla" que estava nos dois docstrings havia EXPIRADO.** Ele foi escrito
antes de a própria D12 reclassificar o segmento do agente de fila para `role='specialist'`
(`orchestrator-bridge\main.py:6007`). Depois disso não existe outro produtor de `role='queue'` no
tier atendido, e o aviso sobreviveu à mudança que o tornou falso — **comentário que descreve um
estado anterior é da mesma família do DDL que promete invariante sem produtor.** Foi medido antes de
mexer, não deduzido.

**Exposição × dano, contados separado:** 70 segmentos de espera no tenant, **1** com
`close_reason='max_wait_exceeded'` (`retencao_humano`, 1 802 441 ms = o teto de 1800 s, 2026-08-18) e
**1** sessão fechada por teto — com segmento. Ou seja: **dano histórico ZERO**, porque em 08-18
aquele pool ainda era `legacy_only` (tier MUDO, caminho que funcionava). Só em 08-24 ele ganhou
`queue_config.pool_id` e virou fila ATENDIDA — a exposição é **prospectiva**. Consequência de método:
a população não contém o caso consertado, logo **medição em runtime é inconclusiva como gate** e quem
julga é o teste (`test_queue_wait_segment.py`, 4 novos, 14 passed).

---

### Mudança 41 — o alvo de SLA passa a ser fato do segmento de espera, copiado no fechamento (D14 ii, 2026-08-24)

> ✅ **Complemento (D14 iii, 2026-08-25) — a LEITURA migrou, e o arco D14 fechou.** Os três leitores
> (`query.py:get_pool_sla_1h` · `reports_query._cv_sla_series` · `query_pools_queue`) passaram a ler
> o alvo do segmento; `sessions.sla_target_ms` virou projeção com **mecanismo** que o impõe
> (`test_sla_reads_the_segment.py`, asserção sobre o SQL EXECUTADO). Decisão do dono: **corte da
> série em data declarada** (`sla_source.SEGMENT_SLA_EPOCH = "2026-08-25 00:52:29"`, o instante
> medido da primeira espera carimbada), nunca fallback à sessão.
>
> ⚠️ **Consequência que este documento precisa registrar, porque é do mecanismo e não do relatório:**
> a época existe para separar *"não medíamos"* (pré-produtor) de **`{t}:pool_config:{p}` expirado
> antes do fechamento da espera** — o TTL medido acima (**3 593 s**, do bridge; o 86 400 do
> routing-engine está morto) faz a segunda ser rotina com gatilho de RELÓGIO. Ela agora é contada
> (`sla_unstamped` no `by_pool` do Fila/SLA) em vez de se esconder dentro do histórico. **O conserto
> do TTL discordante continua aberto**, e esta é a evidência de que ele deixou de ser "só cache
> frio": é buraco no ledger, e o ledger não se corrige por deploy.
>
> Gate próprio: `infra/test/gate_sla_segment_target.sh`, que **insere** a população discriminante
> (uma sessão, duas esperas, alvos diferentes) porque ela **não existe** no ambiente (`discord = 0`).

**O que mudou no mecanismo:** `mute_queue.resolve_queue_exit` passou a resolver o alvo de espera do
pool e a **carimbá-lo no evento** `participant_left` que já publicava. Nova coluna
`analytics.segments.sla_target_ms` (`Nullable(Int64)`).

**O que havia:** `sla_target_ms` só existia como coluna de `sessions`, escrita por `parse_routed`. Uma
sessão carrega **um** alvo, então contato que espera 30 s numa fila (alvo 300 s), é transferido e
espera 120 s noutra (alvo 60 s) registra só um dos dois — a violação da segunda é **invisível**, e a
média mistura populações não comparáveis. Não é hipótese: medido no `retencao_humano` depois da (i),
**48 esperas = 5 abertas + 10 SEM ALVO + 33 julgáveis**, e as 10 pertencem a sessões cujo
`sessions.sla_target_ms` é 0/NULL **enquanto o pool tem 300 000 ms configurado e a espera aconteceu
naquele pool**. 23% das esperas concluídas daquele pool injulgáveis por o alvo estar guardado na
entidade errada.

**Um site de derivação, não seis.** `resolve_queue_exit` tem seis chamadores (`main.py:288,591,1456` ·
`kafka_listener.py:177,713,764`). Receber o alvo por parâmetro faria o campo ser derivado em seis
lugares — literalmente o defeito que a fatia do predicado (`resolve_sla_target_ms`) acabara de
desfazer em quatro, dias antes. A resolução mora **dentro** da função.

**Obstáculo estrutural, e por que ele mudou um arquivo terceiro:** `registry.py:42` importa de
`mute_queue`, então `mute_queue` não pode importar do `registry` — e era lá que vivia
`_pool_config_key`. Escrever o formato uma segunda vez seria a família de defeito de sempre: duas
grafias da mesma chave não ficam vermelhas, devolvem `None` e viram "pool sem alvo". O formato passou
a `models.pool_config_key`, definição única, e o `registry` delega.

**Copiado, não resolvido na leitura** (decisão do dono). Só a cópia guarda "o alvo do dia": resolver na
leitura re-lê a config de HOJE para julgar ONTEM, e mudar o alvo de um pool reescreveria toda a
conformidade histórica sem deixar rastro. Preço aceito: alvo carimbado errado **não é corrigível por
deploy**, só por migração — e é por isso que a fatia do predicado entrou de propósito ANTES desta.

**`SLA_TARGET_MS_FALLBACK` NÃO é aplicado neste call site**, embora `models` o ofereça. O
`{t}:pool_config:{p}` expira (TTL efetivo medido: **3 593 s**, escrito pelo bridge — o 86 400 do
routing-engine está morto), logo ausência é **rotina com gatilho de relógio**, não anomalia. Fabricar
480 s aqui gravaria no ledger um número com cara de config do tenant. Ausente ⇒ `null`, honestamente.

**Sem ramo por `agent_kind`** — sub-pergunta da D14.1, aberta desde 08-24 e agora decidida: **espera é
espera**. Dos 63 segmentos `role='queue'` medidos, 19 estavam em pools de IA; qualquer fila carrega o
alvo do seu pool e o rótulo perde o "humana". Gravado como teste para não ser reaberto por engano.

**A allowlist era o ponto cego.** `analytics_api.models.parse_participant_event` monta o `segment_row`
campo a campo: o que não está no dict é descartado **em silêncio**. Produtor correto, consumidor
verde, coluna `NULL` — e nada vermelho em lugar nenhum. Tem teste próprio, e a mutação que o comprova
está descrita abaixo.

**Falseabilidade provada por MUTAÇÃO, não por `git stash`** (que com a fatia em disco é no-op, e verde
por ausência de mudança é indistinguível de teste que não pode reprovar):
· fallback vazando no ramo de ausência ⇒ `2 failed, 283 passed`, o vermelho novo sendo exatamente
  `test_absent_pool_config_stamps_null_never_zero_nor_fallback`;
· campo fora da allowlist ⇒ `2 failed, 593 passed`, os dois testes do parser.
Preflight em ambas por `inspect.getsource` da função **carregada** (`MUT`/`ORIG`) — "o build não
pegou" e "o teste é inútil" produzem os dois um verde e são conclusões opostas.

**Defeito de instrumento achado pela própria mutação:** a testemunha negativa do parser usava
indexação (`seg["sla_target_ms"]`) e por isso levantava `KeyError` quando o campo saía da allowlist —
reprovava pelo motivo certo mas medindo **presença de chave**, não a proposição que declara. Corrigida
para `.get()`. Reprovar pelo motivo errado é o mesmo defeito que passar pelo motivo errado.

**E2E em tráfego real** (00:52 UTC de 2026-08-25): contato que enfileirou em `retencao_humano`,
`duration_ms=10 065`, **`sla_target_ms=300 000`** — contra as cinco esperas anteriores, todas `\N`,
que servem de testemunha de que a consulta alcança a população. O `pool_id` da linha é
`retencao_humano` (o **destino**), não `fila_humano` (quem executou a fila): D10.1 se comportando como
especificada.

⚠️ **É forward-only e ninguém lê ainda.** Linha antiga fica `NULL` e não há migração possível — o
`first_queued_ms` que daria o alvo já foi consumido na saída. E os três leitores de SLA
(`query.py:240` · `reports_query.py:3803` · `_sla_eligible`) seguem lendo `sessions.sla_target_ms`,
que a partir daqui é **PROJEÇÃO, nunca fonte de cálculo**. Migrá-los é a (iii), fatia própria por
decisão. **Nenhum número de conformidade se moveu com esta mudança** — quem esperar isso vai concluir
que ela não pegou.

---

*Este documento é a referência canônica para o mecanismo de conferência do PlugHub.*
*Qualquer mudança no funcionamento deve ser registrada neste arquivo antes de ir para CHANGELOG.md.*
