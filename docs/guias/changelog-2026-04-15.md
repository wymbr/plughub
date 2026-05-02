# Changelog — 2026-04-15

> Commits cobertos: `94eea57` → `95c7ec7` → `a188a7d` → `676be36`
> Módulos afetados: `mcp-server-plughub`, `routing-engine`, `ai-gateway`, `agent-assist-ui`, `orchestrator-bridge`, `schemas`, `agent-registry`, `skill-flow-engine`, `channel-gateway`

---

## 1. Implementação das 6 ferramentas MCP stub (`94eea57`)

### Contexto

O `mcp-server-plughub` expunha 6 ferramentas com implementação stub (corpo vazio retornando placeholder). Sem essas ferramentas funcionando, o Skill Flow Engine não conseguia completar fluxos reais: não havia como iniciar conversas programaticamente, obter estado de supervisão, nem executar escaladas com contexto correto.

### Ferramentas BPM implementadas (`bpm.ts`)

| Ferramenta | O que faz |
|---|---|
| `conversation_start` | Gera `session_id`/`contact_id`, grava metadados no Redis (`session:{id}:meta`, `session:{id}:contact_id`), publica `contact_open` em `conversations.events` e routing event em `conversations.inbound` |
| `conversation_status` | Lê session meta, SETs `ai_agents`/`human_agents`, snapshot de roteamento; calcula urgência de SLA a partir de `started_at` |
| `conversation_end` | Publica `session.closed` no Redis pub/sub para shutdown gracioso, `contact_closed` em `conversations.events`, e `session.closed` em `conversations.outbound` para fechar o WebSocket do cliente |
| `rule_dry_run` | Delega para a REST API do Rules Engine (`RULES_ENGINE_URL/v1/rules/dry-run`) — avalia regras sem efeito colateral |

### Ferramentas Supervisor implementadas (`supervisor.ts`)

| Ferramenta | O que faz |
|---|---|
| `supervisor_state` | Lê `session:{id}:ai` (consolidated_turns + partial_params); calcula trajetória de sentimento e tendência (improving/stable/declining sobre janela de 3 turns); verifica staleness (>30s desde último update); calcula SLA a partir de `started_at`; lê insights históricos de `{tenant}:session:{id}:context` |
| `supervisor_capabilities` | Lê session meta para `pool_id`; busca pool config no Agent Registry; extrai `supervisor_config.intent_capability_map`; filtra por intenção atual; retorna agentes IA disponíveis para conferência e pools de escalada |

### Arquivos alterados

```
packages/mcp-server-plughub/src/tools/bpm.ts        +192 linhas
packages/mcp-server-plughub/src/tools/supervisor.ts  +207 linhas
```

---

## 2. Correção de fila de agentes humanos + monitoração em tempo real (`95c7ec7`)

### Problema 1 — Contato não ficava em fila quando agente humano indisponível

**Causa raiz:** três lacunas simultâneas:
1. Routing Engine não persistia o contato em fila ao não conseguir alocar
2. Nenhum componente consumia `conversations.queued` para ativar o contato quando um agente ficasse disponível
3. O método `router.dequeue()` existia no código mas nunca era chamado — código morto

**Solução (`routing-engine`):**

**`registry.py`** — 3 novos métodos em `InstanceRegistry`:

```python
# Sorted set: score = queued_at_ms (FIFO base, ou score de prioridade)
# Hash: JSON completo do evento original para re-publicação verbatim
add_queued_contact(tenant_id, pool_id, session_id, contact_data, queued_at_ms)
remove_queued_contact(tenant_id, pool_id, session_id)
get_full_queued_contact(tenant_id, session_id) -> dict | None
```

Redis keys introduzidas:
```
{tenant_id}:pool:{pool_id}:queue          — sorted set, score = queued_at_ms
{tenant_id}:queue_contact:{session_id}    — JSON do evento original, TTL 4h
```

**`main.py`** — quando `not result.allocated`:
- Chama `_persist_queued_contact()` que grava o contato no sorted set e notifica o cliente com mensagem "Aguardando agente disponível..." via `conversations.outbound`

**`kafka_listener.py`** — `LifecycleEventHandler` agora recebe `router`, `producer`, `pool_registry`. No evento `agent_ready`, dispara `asyncio.create_task(_drain_queue_for_agent())`:
1. Verifica que a instância está realmente `ready`
2. Para cada pool da instância, chama `router.dequeue()` (que já implementava scoring de fila)
3. Recupera o evento original completo via `get_full_queued_contact()`
4. Remove do sorted set antes de re-publicar (evita double-routing)
5. Re-publica em `conversations.inbound` → Routing Engine aloca ao agente disponível

```
config.py — adicionado kafka_topic_outbound: str = "conversations.outbound"
```

### Problema 2 — Dashboard de supervisão não atualizava a cada mensagem

**Causa raiz:** dois bugs independentes:
1. `current_turn` era sempre sobrescrito sem arquivar o turno anterior → `consolidated_turns` nunca crescia → sem trajetória de sentimento
2. Sessões IA não publicavam `message.text` em `agent:events:{session_id}` → UI nunca re-buscava o estado do supervisor após steps IA

**Solução (`ai-gateway/session.py`):**

`update_partial_params()` agora fecha o turno anterior antes de escrever o novo:
```python
# Fecha o turno anterior se havia dados significativos
if prev.get("intent") is not None or prev.get("sentiment_score", 0.0) != 0.0:
    state.consolidated_turns.append(ConsolidatedTurn(
        turn_number=len(state.consolidated_turns) + 1, ...
    ))
# Publica supervisor_state.updated → UI re-busca imediatamente
await redis.publish(f"agent:events:{session_id}", json.dumps({
    "type": "supervisor_state.updated", ...
}))
```

**Solução (`agent-assist-ui/src/hooks/useSupervisorState.ts`):**

```typescript
// Antes: só reagia a message.text e menu.render
// Depois: também reage a supervisor_state.updated
if (
  lastEvent.type === "message.text" ||
  lastEvent.type === "menu.render" ||
  lastEvent.type === "supervisor_state.updated"   // ← novo
) {
  fetchState();
}
```

### Arquivos alterados

```
packages/agent-assist-ui/src/hooks/useSupervisorState.ts    +4 linhas
packages/ai-gateway/src/plughub_ai_gateway/session.py       +50 linhas
packages/routing-engine/src/plughub_routing/config.py       +1 linha
packages/routing-engine/src/plughub_routing/kafka_listener.py +113 linhas
packages/routing-engine/src/plughub_routing/main.py          +95 linhas
packages/routing-engine/src/plughub_routing/registry.py      +47 linhas
```

---

## 3. Queue Agent Pattern (`a188a7d`)

### Motivação

Quando um pool humano não tem agentes disponíveis, o cliente ficava em fila sem interação. A solução implementa o **Queue Agent Pattern**: um agente nativo (skill-flow) é ativado por pool para cada contato em espera. O agente conversa com o cliente enquanto aguarda, e recebe um sinal automático quando um humano fica disponível.

### Arquitetura do padrão

```
Cliente entra em fila
       │
       ▼
conversations.queued ──► orchestrator-bridge
                              │
                         fetch pool config
                         (queue_config?)
                              │ sim
                              ▼
                    set queue:agent_active:{session_id}
                    activate_native_agent(agente_fila_v1)
                              │
                    ┌─────────┴─────────────────────────┐
                    │  skill flow loop (timeout_s: 0)    │
                    │  aguarda mensagem ou sinal         │
                    └─────────────────────────────────────┘
                              │
                    agente humano fica disponível
                              │
                         kafka_listener._drain_queue_for_agent()
                              │
                    queue:agent_active:{session_id} existe?
                    ┌─── sim ──────────────────── não ───┐
                    ▼                                     ▼
         LPUSH '__agent_available__'           re-publish conversations.inbound
         menu:result:{session_id}             (comportamento original)
                    │
         menu step desbloqueado
         → choice → escalar → humano
```

### Configuração de pool

Novo campo `queue_config` em `PoolRegistrationSchema`:

```typescript
QueueConfigSchema = z.object({
  agent_type_id: z.string(),          // agente a ativar (ex: agente_fila_retencao_v1)
  max_wait_s:    z.number().default(1800),  // 0 = sem limite
  skill_id:      z.string().optional(), // skill explícita (opcional)
})
```

Exemplo de registro de pool com queue agent:
```json
{
  "pool_id": "retencao_humano",
  "channel_types": ["chat"],
  "sla_target_ms": 480000,
  "queue_config": {
    "agent_type_id": "agente_fila_retencao_v1",
    "max_wait_s": 1800
  }
}
```

### Componentes alterados

**`@plughub/schemas` — `agent-registry.ts`**
- Adicionado `QueueConfigSchema` e `QueueConfig` (exported)
- `PoolRegistrationSchema` recebe `queue_config: QueueConfigSchema.optional()`

**`agent-registry` — prisma + routes**
- `schema.prisma`: campo `queue_config Json?` no modelo `Pool`
- Migration `20260415000000_add_queue_config`: `ALTER TABLE "pools" ADD COLUMN "queue_config" JSONB`
- `routes/pools.ts`: POST e PUT persistem `queue_config`

**`orchestrator-bridge` — `main.py`**
- Nova constante `TOPIC_QUEUED = "conversations.queued"`
- Consumer assina o novo tópico
- Nova função `get_pool_config(http, tenant_id, pool_id)` — busca config do pool no Agent Registry (não cacheada, config pode mudar)
- Nova função `process_queued(msg, http, redis_client)`:
  1. Extrai `pool_id` do evento
  2. Busca `queue_config` via `get_pool_config()`
  3. Resolve o `agent_type_id` via `get_agent_type()`
  4. Grava marcador `queue:agent_active:{session_id}` no Redis (TTL 4h)
  5. Chama `activate_native_agent()` — bloqueia pelo tempo de espera inteiro
  6. Remove marcador ao terminar
- `activate_native_agent()` recebe novo parâmetro `extra_context: dict | None`:
  - Inclui `session_id` no `session_context` (acessível via `$.session.session_id`)
  - `process_queued` passa `extra_context={"pool_id": pool_id}` (acessível via `$.session.pool_id`)

**`routing-engine` — `kafka_listener.py`**
- `_drain_queue_for_agent()`: antes de re-publicar, verifica `queue:agent_active:{session_id}`
  - Se marcador presente → `LPUSH menu:result:{session_id}` com `"__agent_available__"` (sinaliza o agente de fila)
  - Se ausente → comportamento original (re-publica em `conversations.inbound`)

**`skill-flow-engine` — `agente_fila_v1.yaml`** (novo arquivo)

Skill flow de referência para agente de fila:

| Step | Tipo | Função |
|---|---|---|
| `boas_vindas` | `notify` | Mensagem de acolhimento + info sobre espera |
| `aguardar_mensagem` | `menu` (timeout_s:0) | Aguarda mensagem do cliente OU sinal `__agent_available__` |
| `verificar_sinal` | `choice` | Se `$.pipeline_state.ultima_mensagem == "__agent_available__"` → transferir; senão → responder |
| `responder_cliente` | `reason` | Claude gera resposta empática sobre o status da fila |
| `enviar_resposta` | `notify` | Entrega resposta ao cliente; volta para `aguardar_mensagem` |
| `avisar_transferencia` | `notify` | "Especialista disponível, transferindo agora..." |
| `escalar` | `invoke` | Chama `conversation_escalate` com `target_pool: "$.session.pool_id"` |
| `finalizar` | `complete` | `outcome: escalated_human` |

### Redis keys introduzidas

```
queue:agent_active:{session_id}    — JSON com pool_id + agent_type_id + activated_at, TTL 4h
                                     Presente enquanto o agente de fila está ativo
                                     Removido ao terminar ou por TTL
```

### Como registrar um agente de fila para um pool

```bash
# 1. Registrar o agent type
curl -X POST http://localhost:3300/v1/agent-types \
  -H "x-tenant-id: acme" \
  -d '{
    "agent_type_id": "agente_fila_retencao_v1",
    "framework": "plughub-native",
    "execution_model": "stateless",
    "role": "orchestrator",
    "max_concurrent_sessions": 100,
    "pools": ["retencao_humano"],
    "skills": [{"skill_id": "skill_agente_fila_v1", "version": "1"}]
  }'

# 2. Registrar (ou atualizar) o pool com queue_config
curl -X PUT http://localhost:3300/v1/pools/retencao_humano \
  -H "x-tenant-id: acme" \
  -d '{
    "queue_config": {
      "agent_type_id": "agente_fila_retencao_v1",
      "max_wait_s": 1800
    }
  }'
```

### Arquivos alterados

```
packages/schemas/src/agent-registry.ts                                 +25 linhas
packages/agent-registry/prisma/schema.prisma                           +1 linha
packages/agent-registry/prisma/migrations/20260415.../migration.sql    novo
packages/agent-registry/src/routes/pools.ts                            +8 linhas
packages/orchestrator-bridge/src/plughub_orchestrator_bridge/main.py   +167 linhas
packages/routing-engine/src/plughub_routing/kafka_listener.py          +36 linhas
packages/skill-flow-engine/skills/agente_fila_v1.yaml                  novo (122 linhas)
```

---

## 4. Pool ID via path da URL no WebSocket (`676be36`)

### Motivação

O `channel-gateway` aceitava apenas um pool por instância, configurado via `PLUGHUB_ENTRY_POINT_POOL_ID`. Para testar múltiplos pools era necessário subir múltiplas instâncias do gateway ou reiniciar com env diferente.

### Solução

O pool_id migrou do env para o **path da URL**:

```
Antes: ws://localhost:8010/ws/chat
Depois: ws://localhost:8010/ws/chat/{pool_id}
```

Exemplos:
```bash
wscat -c "ws://localhost:8010/ws/chat/retencao_humano"
wscat -c "ws://localhost:8010/ws/chat/suporte_ia"
wscat -c "ws://localhost:8010/ws/chat/fila_demo"
```

### Regra de precedência

```
pool_id da URL  >  PLUGHUB_ENTRY_POINT_POOL_ID (env)
```

O env continua funcionando como fallback para deployments legados que usam `/ws/chat` sem path param (ex: docker-compose existentes que não serão migrados imediatamente).

### Detalhes de implementação

**`main.py`** — novo endpoint:
```python
@app.websocket("/ws/chat/{pool_id}")
async def websocket_endpoint(ws, pool_id: str, contact_id: str | None = Query(None)):
    resolved_pool = pool_id or settings.entry_point_pool_id
    adapter = WebchatAdapter(..., pool_id=resolved_pool, ...)
```

**`adapters/webchat.py`** — `WebchatAdapter` recebe `pool_id` explícito:
```python
def __init__(self, ..., pool_id: str = "") -> None:
    self._pool_id = pool_id or settings.entry_point_pool_id
```
Todos os usos de `self._settings.entry_point_pool_id` substituídos por `self._pool_id`.

**`config.py`** — `entry_point_pool_id` mantido, comentário atualizado para indicar papel de fallback.

### Impacto em session meta

O campo `pool_id` do session meta Redis (`session:{id}:meta`) agora reflete sempre o pool correto da URL:

```json
{
  "contact_id": "...",
  "session_id": "...",
  "tenant_id": "default",
  "customer_id": "...",
  "channel": "chat",
  "pool_id": "retencao_humano",
  "started_at": "..."
}
```

Este campo é lido por `conversation_escalate`, `supervisor_capabilities` e `process_queued` — todos se beneficiam da resolução correta.

### Arquivos alterados

```
packages/channel-gateway/src/plughub_channel_gateway/main.py              +20 linhas
packages/channel-gateway/src/plughub_channel_gateway/adapters/webchat.py  +15 linhas
packages/channel-gateway/src/plughub_channel_gateway/config.py            +6 linhas
```

---

## Resumo de Redis keys introduzidas ou modificadas

| Key | Tipo | Produtor | Consumidor | TTL |
|---|---|---|---|---|
| `{tenant}:pool:{pool}:queue` | sorted set (score=queued_at_ms) | routing-engine | routing-engine (drain) | — |
| `{tenant}:queue_contact:{session}` | string JSON | routing-engine | routing-engine (drain) | 4h |
| `queue:agent_active:{session}` | string JSON | orchestrator-bridge | kafka_listener (drain) | 4h |

## Resumo de tópicos Kafka afetados

| Tópico | Mudança |
|---|---|
| `conversations.queued` | Novo consumidor: `orchestrator-bridge` (activa queue agent) |
| `conversations.outbound` | Novo produtor: `routing-engine` (mensagem "Aguardando agente...") |
| `agent:events:{session}` (Redis pub/sub) | Novo evento publicado: `supervisor_state.updated` (ai-gateway) |

## Índice de commits

| Hash | Tipo | Descrição resumida |
|---|---|---|
| `94eea57` | feat | Implementação das 6 ferramentas MCP stub (BPM + supervisor) |
| `95c7ec7` | fix | Fila de agente humano + monitoração em tempo real |
| `a188a7d` | feat | Queue Agent Pattern — agente nativo na fila de espera |
| `676be36` | feat | Pool ID via URL path `/ws/chat/{pool_id}` |
