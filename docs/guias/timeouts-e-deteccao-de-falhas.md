# Timeouts e Detecção de Falhas

> Spec de referência: PlugHub v24.0 seções 4.5 e 4.6k  
> Módulos envolvidos: `routing-engine`, `mcp-server-plughub`, `channel-gateway`

Este guia descreve os três mecanismos independentes que o PlugHub usa para detectar e recuperar falhas: queda de agente, desconexão de cliente e expiração de dados temporários.

---

## 1. Detecção de queda de agente

### Instância TTL — 30 segundos

O Routing Engine mantém cada instância de agente registrada via uma chave Redis com TTL de 30 segundos:

```
{tenant_id}:instance:{instance_id}   TTL = 30s
```

A chave não é renovada por um timer interno — ela é renovada cada vez que o agente publica um evento Kafka que o Routing Engine consome:

| Evento Kafka | Quem produz | Renova TTL? |
|---|---|---|
| `agent_ready` | `runtime.ts` (mcp-server) | ✅ |
| `agent_heartbeat` | `wait_for_assignment` (mcp-server) | ✅ |
| `agent_busy` | Routing Engine (ao alocar) | ✅ |
| `agent_done` | `runtime.ts` (mcp-server) | ✅ |

Se a chave expirar sem renovação, o CrashDetector infere que a instância travou ou foi encerrada.

### CrashDetector — ciclo a cada 15 segundos

O `CrashDetector` (`routing-engine/src/plughub_routing/crash_detector.py`) é uma task em background que executa um ciclo de varredura a cada `crash_check_interval_s` (padrão: 15s):

```
Ciclo:
  SCAN *:pool:*:instances
    → para cada instance_id no set
        → verifica se {tenant_id}:instance:{instance_id} existe
        → se não existe → crash detectado
```

**Janela máxima de detecção:** TTL (30s) + intervalo do detector (15s) = **até 45 segundos** entre a queda real e o início da recuperação.

### Heartbeat por tipo de agente

O mecanismo de renovação do TTL difere conforme o tipo de agente:

**Agentes native (Skill Flow Engine)**  
O Routing Engine é quem publica `agent_busy` ao alocar, renovando o TTL. Durante a execução do skill flow, o engine mantém o lock `{tenant_id}:pipeline:{conversation_id}:running` — o CrashDetector detecta esse lock e **não** reenfileira conversas com lock ativo (a instância pode estar em um BLPOP ou aguardando I/O, não necessariamente morta).

**Agentes external-mcp (LangGraph, CrewAI, Anthropic SDK direto)**  
O agente externo chama `wait_for_assignment`, que internamente executa um loop de BLPOP com intervalo de 15 segundos. A cada iteração sem contato, publica `agent_heartbeat` via Kafka, renovando o TTL da instância no Routing Engine:

```typescript
// packages/mcp-server-plughub/src/tools/external-agent.ts
const HEARTBEAT_INTERVAL_S = 15  // metade do TTL — margem 2×

const deadline = Date.now() + timeout_s * 1000
while (true) {
  const remainingMs = deadline - Date.now()
  if (remainingMs <= 0) return mcpError("timeout", ...)
  const waitSecs = Math.min(HEARTBEAT_INTERVAL_S, Math.ceil(remainingMs / 1000))
  const result   = await redis.blpop(queueKey, waitSecs)
  if (result) {
    // contato recebido — retorna ao agente
    return ok({ context_package: JSON.parse(result[1]) })
  }
  // 15s sem contato — renovar TTL
  kafka.publish("agent.lifecycle", { event: "agent_heartbeat", ... }).catch(() => {})
}
```

`HEARTBEAT_INTERVAL_S = 15` é metade do TTL de 30s, garantindo margem de segurança de 2× mesmo com pequenas variações de latência.

### Recuperação após queda detectada

Ao detectar uma instância crashed, o `CrashDetector` executa `_handle_crash`:

```
1. Lê InstanceMeta do Redis (pools + active_conversations + agent_type_id)
2. Remove instance_id de todos os pool sets declarados no meta
3. Para cada conversa ativa:
   a. Verifica {tenant_id}:pipeline:{conversation_id}:running
      → se lock existe: pula (native AI agent ainda em execução — não é queda real)
      → se não existe: reenfileira em conversations.inbound
4. Publica agent_crash em agent.lifecycle (auditoria)
5. Deleta InstanceMeta
```

O reenfileiramento inclui `pool_id` e `agent_type_id` da instância anterior — o Router usa esses campos para redirecionar ao mesmo pool sem precisar re-score de intenção.

---

## 2. Detecção de desconexão de cliente

### WebSocket idle timeout — 300 segundos

O Channel Gateway configura um timeout de inatividade de **300 segundos** na conexão WebSocket. Se o cliente não enviar nenhuma frame (dados ou ping) dentro desse período, a conexão é encerrada pelo servidor.

Para evitar desconexões prematuras em conversas pausadas aguardando input do agente, o cliente deve implementar WebSocket ping/pong. O servidor responde a pings automaticamente — a latência do pong não conta para o idle timeout.

**Referência de configuração:**
```python
# packages/channel-gateway/src/config.py
websocket_idle_timeout_s: int = 300
```

### Sinal de desconexão — `session:closed`

Quando uma sessão é encerrada (por disconnect, timeout ou `agent_done`), o Orchestrator Bridge publica o sinal de fechamento no Redis:

```
SET session:closed:{session_id} "1"  EX 10
```

TTL de 10 segundos é suficiente para consumo imediato por qualquer componente que aguarda a sessão.

O `wait_for_message` (tool external-mcp) monitora essa chave **em paralelo** ao BLPOP da mensagem do cliente, via multi-key blpop do ioredis:

```typescript
// packages/mcp-server-plughub/src/tools/external-agent.ts
const resultKey  = `menu:result:${session_id}`
const closedKey  = `session:closed:${session_id}`
const waitingKey = `menu:waiting:${session_id}`

// Sinaliza ao bridge que mensagens devem ser entregues aqui
await redis.set(waitingKey, "1", "EX", timeout_s + 10)
try {
  const result = await redis.blpop(resultKey, closedKey, timeout_s)
  if (!result) return mcpError("timeout", "Cliente não respondeu no prazo")
  const [key, raw] = result
  if (key === closedKey) return mcpError("client_disconnected", "Cliente desconectou")
  // key === resultKey — mensagem recebida normalmente
  return ok({ message: JSON.parse(raw) })
} finally {
  redis.del(waitingKey).catch(() => {})
}
```

Isso permite que o agente externo reaja à desconexão **imediatamente** (sem aguardar o timeout completo de `wait_for_message`) e encerre a sessão com `agent_done` usando `outcome: "unresolved"`.

### Convenção `menu:waiting`

O Orchestrator Bridge só entrega mensagens inbound ao BLPOP de `menu:result:{session_id}` quando a chave `menu:waiting:{session_id}` está presente no Redis. Esse é o mesmo sinal usado pelo step `menu` do Skill Flow Engine.

Se `wait_for_message` não setar essa chave antes do BLPOP, o bridge loga `"No active agent for inbound message (dropped)"` e a mensagem é silenciosamente descartada.

**Regra:** sempre setar `menu:waiting:{session_id}` **antes** do BLPOP em `wait_for_message`. TTL = `timeout_s + 10` para cobrir o período de espera mais uma margem.

---

## 3. TTL de dados temporários

| Chave Redis | TTL | Descrição |
|---|---|---|
| `{tenant_id}:instance:{instance_id}` | 30s | Heartbeat de instância — renovado por eventos Kafka |
| `{tenant_id}:queue:{pool_id}` (ZSET score) | 4h | Contato em fila aguardando agente |
| `{tenant_id}:session:{session_id}` | 4h | Metadados da sessão ativa |
| `menu:waiting:{session_id}` | `timeout_s + 10` | Flag para o bridge entregar mensagens ao BLPOP |
| `session:closed:{session_id}` | 10s | Sinal de encerramento da sessão |
| `{tenant_id}:pool_config:{pool_id}` | 24h | Configuração de pool (padrão; via `PLUGHUB_POOL_CONFIG_TTL_SECONDS`) |
| `{tenant_id}:pipeline:{conversation_id}:running` | Enquanto em execução | Lock de execução do Skill Flow Engine — CrashDetector o respeita |

---

## 4. Timeline de pior caso

Cenário: agente external-mcp perde conectividade imediatamente após um heartbeat.

```
t=0s    Agente envia último heartbeat → TTL da instância renovado para 30s
t=0s    Processo do agente morre / rede cai
t=30s   TTL expira → chave {tenant_id}:instance:{instance_id} some do Redis
t=30s   (ou t=45s, dependendo do ciclo atual)
        CrashDetector detecta ausência na próxima varredura
t≤45s   _handle_crash() executado:
          • instância removida dos pool sets
          • conversas ativas reenfileiradas (salvo as com pipeline lock)
          • agent_crash publicado no Kafka (auditoria)
          • InstanceMeta deletado
t≤45s   Router recebe evento em conversations.inbound
        → tenta alocar nova instância (pool_id hint preservado)
        → se não há instância disponível, coloca em fila
```

**Para clientes WebSocket conectados na conversa afetada:** a conversa é reenfileirada — o cliente não precisa reconectar. O próximo agente alocado recebe o contexto via `context_package` no `wait_for_assignment`.

---

## Referências

- `packages/routing-engine/src/plughub_routing/crash_detector.py` — CrashDetector completo
- `packages/routing-engine/src/plughub_routing/registry.py` — `_instance_key`, `_pool_instances_key`, TTL de instância
- `packages/mcp-server-plughub/src/tools/external-agent.ts` — `wait_for_assignment` (heartbeat loop) e `wait_for_message` (menu:waiting + session:closed)
- `packages/channel-gateway/src/config.py` — `websocket_idle_timeout_s`
- Spec v24.0 seção 4.5 — CrashDetector spec
- Spec v24.0 seção 4.6k — framework external-mcp
