# Layer 4 — Orchestration Layer

> Spec de referência: v24.0 seções 3.2, 3.3, 4.7, 9.5
> Responsabilidade: tomada de decisão da plataforma — alocação de agentes, interpretação de Skill Flow, monitoramento e escalação, acesso a modelos LLM
> Implementado por: `routing-engine`, `rules-engine`, `skill-flow-engine`, `ai-gateway`

---

## Visão geral

A Orchestration Layer é o cérebro operacional da plataforma. Ela decide quem atende cada conversa, como o fluxo de atendimento avança, quando escalar, e como acessar modelos de linguagem de forma controlada.

Quatro módulos com responsabilidades complementares e sem sobreposição:

| Módulo | Responsabilidade principal |
|---|---|
| **Routing Engine** | Único árbitro de alocação — decide qual agente atende qual conversa |
| **Rules Engine** | Monitora conversas IA em tempo real e aciona escalações automáticas |
| **Skill Flow Engine** | Interpreta flows declarativos (JSON) — coordena steps e delega via A2A |
| **AI Gateway** | Ponto único de acesso a LLM — extrai parâmetros de sessão a cada chamada |

O Routing Engine é o único componente autorizado a rotear conversas. Nenhum outro componente faz roteamento direto.

---

## Componentes e responsabilidades

### Routing Engine (`routing-engine`, Python)

- Consome `conversations.inbound` e aloca agente com base em: intent, canal, contexto, disponibilidade de pool, score de negócio, SLA
- Mantém filas por pool com prioridade dinâmica (`ZSET` Redis)
- Gerencia session affinity — redireciona para o agente que atendeu anteriormente quando disponível
- Aplica política de fallback quando pool primário e fallback estão simultaneamente indisponíveis
- Publica em `conversations.routed` e `conversations.queued`
- **SLA:** 99,99% (52min/ano) — active-active cross-site com failover automático

### Rules Engine (`rules-engine`, Python)

- Consome `session:updates:{session_id}` (Redis pub/sub) a cada chamada do AI Gateway
- Avalia regras contra parâmetros de sessão: `sentiment_score`, `intent_confidence`, flags semânticos
- Aciona escalações via `rules.escalation.events` (Kafka) → Routing Engine (Escalation Engine)
- Monitora exclusivamente conversas com agente IA — conversas com agente humano são domínio do Agent Assist
- Regras ativas em `rules:active` (Redis), carregadas na inicialização
- **Sem estado entre turnos:** stateless, escala junto com o agent pool

### Skill Flow Engine (`skill-flow-engine`, TypeScript)

- Interpreta flows declarativos em JSON (9 tipos de step: task, choice, catch, escalate, complete, invoke, reason, notify, menu)
- Persiste `pipeline_state` no Redis a cada transição de step — nunca em memória apenas
- Delega sub-tarefas a agentes via A2A (step `task`) passando pelo Routing Engine
- Chama MCP tools diretamente (step `invoke`) e AI Gateway (step `reason`)
- Coordena `notify` e `menu` via Notification Agent e Channel Gateway
- Retomada automática após interrupção — estado persistido garante continuidade

### AI Gateway (`ai-gateway`, Python)

- Rota `/inference`: ponto de entrada único para todos os componentes
- Extrai intent, confidence, sentiment_score, flags a cada chamada LLM — grava no Redis **antes de retornar**
- Publica em `session:updates:{session_id}` para o Rules Engine via pub/sub
- Semantic cache, rate limiting, fallback de modelo — transparente para o chamador
- Não tem estado entre turnos

---

## Interfaces

**Routing Engine:**
- Entrada: `conversations.inbound` (Kafka)
- Saída: `conversations.routed`, `conversations.queued` (Kafka)
- Lê/escreve: Redis (pool queues, session affinity, pool config)
- Lê: PostgreSQL via agent-registry (configuração de pools e agent-types)

**Rules Engine:**
- Entrada: `session:updates:*` (Redis pub/sub, pattern subscribe)
- Saída: `rules.escalation.events`, `rules.shadow.events` (Kafka)
- Lê: `session:{session_id}:ai` (Redis) para contexto de turno consolidado
- Lê: `rules:active` (Redis) para regras em vigor

**Skill Flow Engine:**
- Entrada: `conversations.routed` (Kafka) — evento de alocação com flow a executar
- Saída: delegações A2A ao Routing Engine, chamadas a MCP, AI Gateway, Notification Agent
- Lê/escreve: `pipeline:{tenant_id}:pipeline:{session_id}` (Redis, TTL 24h)

**AI Gateway:**
- Entrada: HTTP POST `/inference`, `/v1/turn`, `/v1/reason`
- Saída: `session:{session_id}:ai` (Redis), pub/sub `session:updates:{session_id}`
- Saída para modelos: Anthropic API (ou providers configurados)

---

## Fluxo de dados

```
conversations.inbound (Kafka)
↓ Routing Engine: aloca agente, determina flow
↓ conversations.routed (Kafka)
↓ Skill Flow Engine: interpreta flow, executa steps

  step task → A2A → Routing Engine → aloca sub-agente
  step invoke → MCP Server
  step reason → AI Gateway → /inference
                  ↓ extrai params → Redis session
                  ↓ pub/sub session:updates
                    ↓ Rules Engine avalia regras
                    ↓ rules.escalation.events → Escalation Engine
                      ↓ Routing Engine redireciona conversa
  step notify → Notification Agent
  step menu → Channel Gateway (coleta) → resultado normalizado
  step complete → agent_done → conversations.events (Kafka)
```

---

## Considerações operacionais

**Invariante de roteamento:** o Routing Engine é o único componente que roteia conversas. Nenhum outro componente pode alocar um agente sem passar por ele.

**Invariante de persistência:** `pipeline_state` é persistido no Redis em toda transição de step — nunca em memória apenas. Interrupções (restart, falha) não perdem progresso do flow.

**Invariante do AI Gateway:** todo acesso a LLM passa pelo AI Gateway. Agentes, Rules Engine e Skill Flow não chamam modelos diretamente.

**Multi-site:** Routing Engine e Rules Engine em active-active cross-site. Coordenação via Redis Cluster compartilhado. Reservas de recursos escassos via `DECR` atômico (sem lock). Rules Engine é stateless — escala horizontalmente sem coordenação.

**KEDA:** lag em `conversations.inbound` e `conversations.routed` aciona auto-scaling do Agent Pool. Dimensionado por tópico Kafka, não por métrica de CPU.

---

## Referência spec

- Seção 3.2 — Rules Engine (Motor de Regras)
- Seção 3.3 — Routing Engine
- Seção 4.7 — Skill Flow e Skill Registry
- Seção 9.5 — Protocolo A2A
- Seção 2.2a — AI Gateway
- [modulos/routing-engine.md](../modulos/routing-engine.md)
- [modulos/rules-engine.md](../modulos/rules-engine.md)
- [modulos/skill-flow-engine.md](../modulos/skill-flow-engine.md)
- [modulos/ai-gateway.md](../modulos/ai-gateway.md)
