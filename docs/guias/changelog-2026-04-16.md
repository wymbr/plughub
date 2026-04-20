# Changelog — 2026-04-16

> Commits cobertos: `c344be4` → `5db8fe4` → `e2b2721` → `c659490` → `597fbb9` → `c83e0c3` → `5fe8e40` → `770f3ca`
> Módulos afetados: `mcp-server-plughub`, `routing-engine`, `agent-registry`

---

## Contexto geral

Esta sessão corrigiu a integração end-to-end do framework **external-mcp** (spec 4.6k). O framework permite que agentes externos (LangGraph, CrewAI, Anthropic SDK direto, qualquer framework proprietário) se conectem ao mcp-server-plughub via SSE e gerenciem seu próprio ciclo de atendimento usando quatro tools: `wait_for_assignment`, `send_message`, `wait_for_message` e `invoke`.

Ao final da sessão, o fluxo completo foi validado em ambiente de desenvolvimento: agente conecta, anuncia disponibilidade, recebe contato, troca mensagens com o cliente e encerra — em ciclo contínuo sem degradação.

---

## 1. Integração do routing engine com agentes external-mcp (`c344be4`)

### Problemas corrigidos

**A. Evento `agent_ready` sem campos obrigatórios**

O routing engine esperava `agent_type_id`, `execution_model`, `max_concurrent_sessions`, `current_sessions` e `status` no evento Kafka `agent_ready` para criar a instância corretamente. O mcp-server publicava apenas `tenant_id`, `instance_id` e `pools`.

**Solução (`runtime.ts`):** `agent_ready` agora lê os campos necessários do hash Redis da instância antes de publicar:

```typescript
await kafka.publish("agent.lifecycle", {
  event:                   "agent_ready",
  tenant_id, instance_id,
  agent_type_id:           agentTypeId,
  pools,
  status:                  "ready",
  execution_model:         executionModel,
  max_concurrent_sessions: parseInt(maxConcurrentRaw, 10),
  current_sessions:        parseInt(currentSessRaw, 10),
  timestamp:               new Date().toISOString(),
})
```

**B. `agent_login` não persistia `execution_model`**

O campo `execution_model` lido do Agent Registry não era salvo no hash Redis da instância. Como `agent_ready` passou a ler esse campo, o `agent_login` precisou salvá-lo.

**C. `agent-registry` não publicava no Kafka**

O routing engine popula seu cache Redis de pools exclusivamente a partir do tópico `agent.registry.events`. O `agent-registry` nunca publicava nesse tópico — apenas persistia no PostgreSQL. Como resultado, o routing engine não enxergava nenhum pool e descartava todos os contatos para fila.

**Solução (`agent-registry`):** adicionada publicação fire-and-forget ao tópico `agent.registry.events` nas rotas `POST /v1/pools` e `PUT /v1/pools/:pool_id`.

Novos arquivos:
- `packages/agent-registry/src/infra/kafka.ts` — produtor KafkaJS com retry e error swallowing
- Adicionada dependência `kafkajs ^2.2.4` ao `package.json`

---

## 2. Correção de compatibilidade `zrange` no routing engine + test agent contínuo (`5db8fe4`)

### A. `zrange(rev=True)` não suportado em redis-py < 4.2

O método `InstanceRegistry.get_queued_contacts()` usava `zrange(..., rev=True)` que só existe em redis-py ≥ 4.2. Substituído por `zrevrange()` em ambas as classes (`InstanceRegistry` e `PoolRegistry`).

### B. Test agent encerrava após 1 ciclo

O `test-external-agent.mjs` chamava `process.exit(0)` após o primeiro `agent_done`, consumindo toda a capacidade de teste. Reescrito como loop contínuo com `MAX_CYCLES=0` (infinito por padrão). Graceful shutdown via `SIGINT` (Ctrl+C).

---

## 3. `wait_for_assignment` com heartbeats periódicos (`e2b2721`)

### Problema

O routing engine mantém instâncias com TTL de 30s no Redis, renovado a cada `agent_ready` ou `agent_busy`. Durante o BLPOP de longa duração em `wait_for_assignment`, o TTL expirava e o routing engine removia o agente do pool — contatos chegavam e não encontravam agentes disponíveis.

### Solução

`wait_for_assignment` reescrito como loop: BLPOP por 15s → se sem contato, publica `agent_heartbeat` → repete até contato chegar ou timeout total atingido.

```typescript
const deadline = Date.now() + timeout_s * 1000
while (true) {
  const remainingMs = deadline - Date.now()
  if (remainingMs <= 0) return mcpError("timeout", ...)
  const waitSecs = Math.min(HEARTBEAT_INTERVAL_S, Math.ceil(remainingMs / 1000))
  const result   = await redis.blpop(queueKey, waitSecs)
  if (result) {
    const [, raw] = result
    return ok({ context_package: JSON.parse(raw) })
  }
  // Timeout de 15s sem contato — renovar TTL via heartbeat
  kafka.publish("agent.lifecycle", { event: "agent_heartbeat", ... }).catch(() => {})
}
```

`HEARTBEAT_INTERVAL_S = 15` — metade do TTL de 30s, margem de segurança de 2×.

---

## 4. TTL do pool_config aumentado para 24h (`c659490` + `597fbb9`)

### Problema

`pool_config_ttl_seconds` tinha padrão de 300s (5 minutos). Pool configs são estáticas durante operação — não mudam a cada 5 minutos. Após a expiração, `get_pool()` retornava `None`, o routing engine interpretava como "pool inexistente" e enfileirava todos os contatos com a mensagem "Aguardando agente disponível", mesmo com agentes disponíveis.

### Solução

- `config.py`: default de `pool_config_ttl_seconds` alterado de 300 para 86400 (24h)
- `ecosystem.config.js`: adicionado `PLUGHUB_POOL_CONFIG_TTL_SECONDS: "86400"` à env section

**Nota operacional:** `pm2 restart --update-env` não relê `ecosystem.config.js` do disco. Para aplicar alterações de env de um arquivo ecosystem, usar `pm2 delete <nome> && pm2 start ecosystem.config.js --only <nome>`.

---

## 5. `wait_for_message` — convenção `menu:waiting` + chave Redis correta (`c83e0c3`)

### Problema 1 — Mensagens do cliente descartadas silenciosamente

O Orchestrator Bridge só entrega mensagens inbound ao BLPOP de `wait_for_message` quando a chave `menu:waiting:{session_id}` está presente no Redis — o mesmo sinal usado pelo `menu` step do skill-flow. O `wait_for_message` original não setava essa chave. Resultado: o bridge logava "No active agent for inbound message (dropped)" e a mensagem era perdida.

**Solução:** `wait_for_message` seta `menu:waiting:{session_id}` com TTL = `timeout_s + 10` antes do BLPOP e remove no `finally`.

### Problema 2 — Chave Redis com prefixo de tenant incorreto

`wait_for_message` escutava em `${tenant_id}:menu:result:{session_id}` mas o bridge (e o routing engine) publicam em `menu:result:{session_id}` — sem prefixo de tenant. BLPOP nunca desbloqueava.

**Solução:** chave corrigida para `menu:result:${session_id}`.

### Melhoria adicional — Detecção de desconexão

`wait_for_message` agora monitora `session:closed:{session_id}` em paralelo ao `menu:result`, retornando `client_disconnected` imediatamente ao detectar desconexão em vez de aguardar o timeout completo.

```typescript
const result = await redis.blpop(resultKey, closedKey, timeout_s)
// resultKey  → mensagem do cliente
// closedKey  → cliente desconectou
```

---

## 6. Correções no test agent (`5fe8e40`)

### A. Schema de `agent_done` incorreto

O test agent passava `{ issue: "...", status: "resolved" }` mas o `IssueSchema` exige `{ issue_id: string, description: string, status: IssueStatusValue }`. Erro de validação Zod causava falha no `agent_done`.

**Corrigido para:**
```javascript
issue_status: [{
  issue_id:    "external_mcp_test",
  description: "Teste de integração do agente externo via MCP (spec 4.6k).",
  status:      "resolved",  // ou "unresolved" se sem resposta do cliente
}]
```

### B. Estado `busy` persistia após falha no ciclo

Quando `agent_done` falhava (ex: erro de validação), a instância ficava presa em estado `busy`. O próximo `agent_ready` falhava com "invalid_state" — ciclo quebrado indefinidamente.

**Solução:** o loop principal do test agent detecta ciclos que falharam com sessão aberta (via `e.sessionId` propagado pelo `runCycle`) e tenta `agent_done` de recuperação antes de iniciar o próximo ciclo.

### C. `outcome` dinâmico

`agent_done` agora usa `outcome: "resolved"` quando o cliente respondeu e `outcome: "unresolved" + handoff_reason` quando `wait_for_message` expirou sem resposta.

---

## 7. `agent_ready` aceita estado `ready` (idempotente) (`770f3ca`)

### Problema

Após `agent_done` com `current_sessions == 0`, a instância transiciona automaticamente para `ready`. O próximo ciclo chamava `agent_ready` mas a validação só aceitava `logged_in` ou `paused` — o ciclo quebrava com "invalid_state".

### Solução

`agent_ready` passou a aceitar também `ready` como estado válido. A semântica é idempotente: se já em `ready`, apenas renova o evento Kafka e mantém o TTL da instância no routing engine.

```typescript
if (state !== "logged_in" && state !== "paused" && state !== "ready") {
  return mcpError("invalid_state", ...)
}
```

---

## Resumo de Redis keys introduzidas ou modificadas

| Key | Mudança |
|---|---|
| `menu:waiting:{session_id}` | Setada por `wait_for_message` antes do BLPOP (TTL = timeout_s + 10). Sinal para o bridge entregar mensagens inbound. |

## Resumo de tópicos Kafka afetados

| Tópico | Mudança |
|---|---|
| `agent.lifecycle` | Novo produtor: `wait_for_assignment` (evento `agent_heartbeat` a cada 15s durante BLPOP) |
| `agent.registry.events` | Novo produtor: `agent-registry` (eventos `pool.registered` e `pool.updated`) |

## Índice de commits

| Hash | Tipo | Descrição resumida |
|---|---|---|
| `c344be4` | fix | Integração routing engine com agentes external-mcp (agent_ready fields + agent-registry Kafka) |
| `5db8fe4` | fix | `zrevrange` para redis-py < 4.2 + test agent modo contínuo |
| `e2b2721` | fix | `wait_for_assignment` com heartbeats a cada 15s |
| `c659490` | fix | `pool_config_ttl_seconds` 5min → 24h |
| `597fbb9` | fix | `PLUGHUB_POOL_CONFIG_TTL_SECONDS=86400` no ecosystem.config.js |
| `c83e0c3` | fix | `wait_for_message`: menu:waiting + chave Redis sem tenant prefix + detecção de desconexão |
| `5fe8e40` | fix | Test agent: IssueSchema correto + recuperação de estado busy + outcome dinâmico |
| `770f3ca` | fix | `agent_ready` idempotente — aceita estado `ready` |
