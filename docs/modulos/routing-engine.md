# Módulo: routing-engine (@plughub/routing-engine)

> Pacote: `routing-engine` (serviço)
> Runtime: Python 3.11+, FastAPI
> Spec de referência: seções 3.3, 3.3a, 3.3b, 4.5, 4.6

## O que é

O `routing-engine` é o árbitro único de alocação de conversas. Nenhuma conversa chega a um agente sem passar por ele. Toda decisão de quem atende quê — na chegada de um contato, em reavaliações periódicas, e quando um agente fica disponível — é tomada aqui.

É **stateless**: não guarda estado em memória entre decisões. Todo o estado operacional vive no Redis (instâncias disponíveis, filas de pool, afinidade de sessão). O Redis é a fonte de verdade; o Routing Engine só lê e escreve nele.

---

## Invariante central

> **Nunca** criar um componente que roteia conversas sem passar pelo Routing Engine. Toda alocação, re-alocação e recuperação de crash passa por aqui.

---

## Estrutura do Pacote

```
routing-engine/src/plughub_routing/
  main.py            ← FastAPI + inicialização dos backgrounds tasks
  decide.py          ← Decider — função decide(), entrada principal
  router.py          ← Router — cenários 1 (contact arrives) e 2 (resource available)
  scorer.py          ← score_resource(), score_contact_in_queue(), compute_priority_score()
  saturated.py       ← SaturationHandler — política por canal quando todos os pools saturados
  crash_detector.py  ← CrashDetector — detecta crashes de instâncias e recupera conversas
  insights.py        ← fetch_session_context() — carrega insights e pending deliveries
  registry.py        ← InstanceRegistry, PoolRegistry — abstração sobre Redis
  kafka_listener.py  ← consome agent.lifecycle para manter estado de instâncias no Redis
  models.py          ← modelos Pydantic (RoutingDecision, AgentInstance, PoolConfig, etc.)
  config.py          ← settings via variáveis de ambiente
```

---

## Os Dois Cenários de Roteamento (spec 3.3b)

### Cenário 1 — Contato chega (`route()`)

Pergunta: **qual recurso é o melhor match para este contato?**

Critério dominante: compatibilidade de competência (perfil do recurso vs. requisitos do contato).

```
1. Busca pools candidatos para o canal (tenant_id + channel)
2. Tenta alocação no site local (timeout: 150ms)
   Para cada pool → instâncias com status == "ready" e capacity disponível:
     ├── Verifica afinidade de sessão (stateful) — se existir, tenta primeiro
     └── Calcula resource_score (compatibilidade de competência) para cada instância
         ├── Hard filter: se recurso não tem competência exigida → descarta (-1.0)
         └── Score proporcional: min(resource_level / required_level, 1.0) × weight
   Seleciona instância com maior resource_score
   Se stateful: persiste afinidade de sessão no Redis
3. Se site local indisponível: tenta sites remotos (timeout: 300ms cada)
   cross_site = true no RoutingResult quando alocado em site remoto
4. Se nenhum site disponível: aplica política de saturação (spec 3.3a)
```

### Cenário 2 — Recurso fica disponível (`dequeue()`)

Pergunta: **qual contato da fila este recurso deve atender primeiro?**

Critério dominante: prioridade efetiva = tier + envelhecimento por SLA.

```
1. Carrega top_n contatos da fila do pool (Redis Sorted Set)
2. Recalcula score de fila para cada um com o tempo atual (now_ms)
   effective_priority(t) = base_priority(tier)
                         + aging_factor  × min(t/sla_target, 1.0)
                         + breach_factor × max((t/sla_target) - 1.0, 0)
3. Verifica compatibilidade (resource_score) recurso × contato
4. Retorna o contato de maior prioridade compatível
```

---

## Classificação de Zona de Atendimento (modo)

```python
confidence > 0.85           → "autonomous"   (IA gerencia sem supervisão)
0.60 ≤ confidence ≤ 0.85   → "hybrid"       (IA com reavaliação periódica)
confidence < 0.60 ou risk_flag → "supervised"  (força pool humano)
```

O `risk_flag` no `CustomerProfile` força `supervised` independentemente de qualquer confidence.

**Turno de reavaliação por modo:**

| Modo | `reevaluation_turn` |
|---|---|
| `autonomous` | `None` — IA gerencia |
| `hybrid` | configurável (default: 5) |
| `supervised` | configurável (default: 1) |

---

## Priority Score (spec 4.6)

Usado no `decide()` para comparar e ordenar pools disponíveis:

```
score = (sla_urgency    × weight_sla)
      + (wait_time_norm  × weight_wait)
      + (customer_tier   × weight_tier)
      + (churn_risk      × weight_churn)
      + (business_score  × weight_business)

sla_urgency     = oldest_wait_ms / sla_target_ms
sla_urgency > 1.0 → prioridade máxima absoluta (retorna inf → score 9999 no RoutingDecision)
wait_time_norm  = min(elapsed_ms / sla_target_ms, 1.0)
```

Pesos por pool (`RoutingExpression` no `PoolConfig`):

| Campo | Default |
|---|---|
| `weight_sla` | 1.0 |
| `weight_wait` | 0.8 |
| `weight_tier` | 0.6 |
| `weight_churn` | 0.9 |
| `weight_business` | 0.4 |

Tier → score numérico: `platinum = 1.0`, `gold = 0.6`, `standard = 0.2`.

---

## Política de Saturação (spec 3.3a)

Quando todos os pools candidatos (primário + fallback) estão indisponíveis simultaneamente:

| Canal | `sla_urgency` | Ação | KEDA | Oncall |
|---|---|---|---|---|
| `voice` | ≤ 2.0 | Fila prioritária. SLA expandido = `sla_target × 1.5`. | ✅ alert (timeout 60s) | ❌ |
| `voice` | > 2.0 | Redirect para site secundário (`pool.remote_sites[0]`) | ✅ | ✅ CRÍTICO |
| `chat` / `whatsapp` | qualquer | Mensagem de espera + opção de callback assíncrono (Pending Delivery Store) | ❌ | ❌ |
| `email` | qualquer | Confirmação de recebimento. SLA expandido = `sla_target × 2` | ❌ | ❌ |
| outros (`sms`, `webrtc`) | qualquer | Fila com callback, mesmo tratamento de chat | ❌ | ❌ |

O `RoutingDecision` retorna com `saturated: true` e `saturation_action` com o tipo da ação aplicada.

---

## Detecção de Crash de Instâncias (`CrashDetector`)

Background task que roda periodicamente (default: a cada `crash_check_interval_s`).

**Como detecta:** instâncias publicam `agent_heartbeat` no Kafka a cada ~10s. O `kafka_listener` mantém a chave `{tenant_id}:instance:{instance_id}` com TTL de 30s no Redis. Se o TTL expirar sem heartbeat → instância considerada crashed.

**O que faz ao detectar crash:**

```
1. Varre pool sets via SCAN: "*:pool:*:instances"
2. Para cada instance_id no set: verifica se a chave da instância existe
3. Se não existe:
   a. Lê InstanceMeta: pools e conversas ativas
   b. Remove instance_id de todos os pool sets declarados no meta
   c. Re-publica cada conversa ativa em conversations.inbound para re-roteamento
   d. Publica evento "agent_crash" em agent.lifecycle (audit)
   e. Deleta InstanceMeta
```

O `CrashDetector` **não usa keyspace notifications** do Redis — usa polling periódico sobre os pool sets, mais robusto em cenários de alta carga.

---

## Carregamento de Contexto de Sessão (`insights.py`)

No início de cada contato, o Routing Engine consulta e consolida o contexto do cliente:

```python
context = {
  "customer_id":           str,
  "conversation_id":       str,
  "conversation_insights": [...],   # insight.conversa.* + insight.historico.* ativos
  "pending_deliveries":    [...],   # outbound.* ativos para o cliente
}
```

O resultado é persistido em `{tenant_id}:session:{conversation_id}:context` (TTL: 1h) e consumido pelo AI Gateway e Supervisor Agent durante o atendimento.

**Chaves Redis lidas para montar o contexto:**

| Padrão de chave | Conteúdo |
|---|---|
| `{tenant_id}:insight:{conversation_id}:*` | Insights da conversa atual (`insight.conversa.*`) |
| `{tenant_id}:insight:h:{customer_id}:*` | Insights históricos do cliente (`insight.historico.*`) |
| `{tenant_id}:pending:{customer_id}:*` | Pending deliveries ativos (`outbound.*`) |

Ambas as listas são ordenadas por `priority` (maior primeiro) antes de persistir.

---

## Modelos de Dados Principais

### `RoutingDecision` — resultado de `decide()`

```python
RoutingDecision {
  conversation_id:   str
  tenant_id:         str
  mode:              "autonomous" | "hybrid" | "supervised"
  primary:           AllocatedAgent | None
  fallback:          AllocatedAgent | None
  reevaluation_turn: int | None
  saturated:         bool
  saturation_action: str | None
  decided_at:        ISO datetime
}

AllocatedAgent {
  instance_id:   str
  agent_type_id: str
  pool_id:       str
  score:         float
}
```

### `AgentInstance` — estado em tempo real (Redis)

```python
AgentInstance {
  instance_id:      str
  agent_type_id:    str
  tenant_id:        str
  pool_id:          str
  pools:            list[str]
  execution_model:  "stateless" | "stateful"
  max_concurrent:   int
  current_sessions: int
  state:            str    # ready | busy | paused | draining
  last_seen:        ISO datetime | None
  profile:          dict[str, int]   # competências declaradas
}
```

### `PoolConfig` — configuração por pool (Redis cache)

```python
PoolConfig {
  pool_id:             str
  tenant_id:           str
  channel_types:       list[str]
  sla_target_ms:       int
  routing_expression:  RoutingExpression   # pesos para priority_score
  competency_weights:  dict[str, float]    # pesos por competência (cenário 1)
  aging_factor:        float               # crescimento até SLA (cenário 2)
  breach_factor:       float               # aceleração pós-breach (cenário 2)
  remote_sites:        list[str]           # sites para cross-site routing
  is_human_pool:       bool
}
```

> **Importante:** `PoolConfig` nunca é lido do PostgreSQL diretamente — é populado no Redis pelo `kafka_listener` a partir dos eventos `pool.registered` e `pool.updated` publicados pelo `agent-registry` no tópico `agent.registry.events`. O Routing Engine nunca acessa o banco relacional. O TTL padrão do cache é 24h (configurável via `PLUGHUB_POOL_CONFIG_TTL_SECONDS`) — suficiente para cobrir reinicios normais sem perda de visibilidade de pools.

---

## Chaves Redis

```
{tenant_id}:instance:{instance_id}                  Hash (TTL: 30s)  → AgentInstance
{tenant_id}:pool:{pool_id}:instances                Set              → instance_ids disponíveis
{tenant_id}:pool:{pool_id}:queue                    Sorted Set       → contatos em fila (score = prioridade)
{tenant_id}:routing:instance:{instance_id}:meta     Hash (sem TTL)   → InstanceMeta (pools + conversas ativas)
{tenant_id}:routing:instance:{instance_id}:conversations  Set        → UUIDs de conversas ativas
{tenant_id}:routing:affinity:{session_id}           String           → instance_id (sessões stateful)
{tenant_id}:session:{conversation_id}:context       String JSON      → contexto carregado (TTL: 1h)
```

---

## Tópicos Kafka

| Tópico | Direção | Descrição |
|---|---|---|
| `conversations.inbound` | **Consome** | Eventos de entrada — aciona `route()` |
| `conversations.inbound` | **Publica** (crash recovery) | Re-publica conversas órfãs de instâncias crashed |
| `agent.lifecycle` | **Consome** | `agent_login/ready/busy/done/logout/heartbeat` — mantém estado no Redis |
| `agent.lifecycle` | **Publica** | `agent_crash` — evento de audit de crash detectado |
| `agents.decisions` | **Publica** | `RoutingDecision` para audit de cada decisão |
| `evaluation.events` | **Consome** | `evaluation.requested` — aciona `SkillFlowEngine.run()` para avaliação |

---

## Execução de Avaliações

O Routing Engine consome `evaluation.requested` de `evaluation.events` e executa
o agente de avaliação nativo via `SkillFlowEngine.run()`. O `evaluation_id` do
evento funciona como `sessionId` sintético — não há cliente real nesta sessão.

```python
# consumer: evaluation.events → evaluation.requested
engine.run(
    tenantId       = event.tenant_id,
    sessionId      = event.evaluation_id,      # sessionId sintético
    customerId     = event.contact.contact_id,
    skillId        = "agente_avaliacao_v1",
    flow           = load_flow("agente_avaliacao_v1"),
    sessionContext = {
        "evaluation_id":   event.evaluation_id,
        "skill_id":        event.skill_id,
        "transcript_id":   event.transcript_id,
        "context_package": event.context_package,
        "agent":           event.agent,
        "contact":         event.contact,
        "triggered_by":    event.triggered_by,
    }
)
```

Não há alocação de instância de agente nem interação com cliente. O flow executa
do `entry` até o `complete` em chamada única sem suspensões (`menu` e `escalate`
não são usados no agente de avaliação). Retomada após crash funciona normalmente
via `pipeline_state` salvo no Redis com chave `{tenant_id}:pipeline:{evaluation_id}`.

---

## Relação com Outros Módulos

```
routing-engine
  ├── consome → agent-registry   (PoolConfig via kafka_listener — nunca direto ao PostgreSQL)
  ├── lê/escreve → Redis         (instâncias, pools, filas, afinidade, contexto de sessão)
  ├── consome → Kafka            (conversations.inbound, agent.lifecycle, evaluation.events)
  ├── publica → Kafka            (agents.decisions, agent.lifecycle:agent_crash)
  └── é chamado por:
        ├── mcp-server-plughub   (agent_delegate → aloca agente para step task)
        ├── rules-engine         (conversation_escalate → re-alocação via Escalation Engine)
        └── channel-gateway      (inbound event → decisions)
```
