# mcp-server-plughub

MCP Server da **PlugHub Platform** — expõe as tools de Agent Runtime, BPM e Supervisor.

## Transporte

SSE (Server-Sent Events) sobre HTTP. Porta padrão: **3100**.

## Iniciar

```bash
npm install
npm run dev          # desenvolvimento
npm run build && npm start  # produção
```

Variáveis de ambiente:
```
PORT=3100
HOST=0.0.0.0
PLUGHUB_REDIS_URL=redis://localhost:6379
PLUGHUB_KAFKA_BROKERS=localhost:9092
```

## Tools disponíveis

### BPM (sistemas externos)
| Tool | Descrição |
|---|---|
| `conversation_start` | Inicia atendimento — retorna session_id |
| `conversation_status` | Estado atual de uma conversa |
| `conversation_end` | Encerra forçado (timeout, cancelamento) |
| `rule_dry_run` | Simula regra do Rules Engine |

### Agent Runtime (agentes durante atendimento)
| Tool | Descrição |
|---|---|
| `agent_login` | Registra instância — retorna JWT |
| `agent_ready` | Coloca na fila de alocação |
| `agent_busy` | Marca como ocupado com uma sessão |
| `agent_done` | Sinaliza conclusão — dispara avaliação |
| `agent_logout` | Graceful shutdown da instância |
| `insight_register` | Registra insight.conversa.* na sessão |
| `agent_delegate` | Delega subtarefa A2A |

### Supervisor (Agent Assist em pools humanos)
| Tool | Descrição |
|---|---|
| `supervisor_state` | Estado da conversa (sentiment, intent, SLA) |
| `supervisor_capabilities` | Capacidades filtradas pelo intent atual |
| `agent_join_conference` | Convida agente IA para conferência |

## Spec de referência

- 3.2a — Supervisor tools
- 4.2  — agent_done (contrato de conclusão)
- 4.5  — agent_login/ready/busy/logout (ciclo de vida)
- 9.4  — todas as tools
- 9.5  — agent_delegate (protocolo A2A)
