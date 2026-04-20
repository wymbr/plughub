# Módulo: agent-assist

> Spec de referência: v24.0 seções 3.2a, 4.5 (supervisor_config)
> Responsabilidade: painel de assistência ao agente humano — exibe estado da conversa, insights, capacidades disponíveis e permite acionar agentes IA em conferência ou background
>
> **Protótipo UI do piloto:** [agent-assist-piloto.md](agent-assist-piloto.md)

---

## Visão geral

O Agent Assist é a interface de assistência ao agente humano durante o atendimento. Não é um serviço com processo próprio — é um cliente das ferramentas Supervisor expostas pelo `mcp-server-plughub`. Toda a inteligência vive nos dados já produzidos pelo AI Gateway e pelas tools; o Agent Assist apenas os lê e os apresenta.

**Modelo de ativação por pool:** o Supervisor só está disponível em pools com `supervisor_config.enabled: true`. Pools de agentes IA **não devem** ter `supervisor_config` — o monitoramento de conversas IA é responsabilidade do Rules Engine.

**Modelo pull sem push:** não há processo vivendo por sessão. O Agent Assist decide quando chamar as tools. Não há websocket ou canal de push do servidor para o painel — o custo é proporcional à frequência de polling.

---

## Ferramentas MCP (Supervisor tools)

Todas expostas por `mcp-server-plughub`. Consumidas exclusivamente pelo Agent Assist.

### `supervisor_state`

Retorna o estado atual da conversa lendo diretamente o Redis da sessão. Disponível apenas em pools com `supervisor_config.enabled: true`.

**Entrada:**
```json
{ "session_id": "uuid" }
```

**Saída resumida:**
```json
{
  "session_id": "uuid",
  "sentiment": {
    "current": -0.35,
    "trajectory": [-0.10, -0.20, -0.35],
    "trend": "declining",
    "alert": true
  },
  "intent": {
    "current": "portability_check",
    "confidence": 0.87,
    "history": ["billing_query", "portability_check"]
  },
  "flags": ["churn_signal"],
  "sla": {
    "elapsed_ms": 240000,
    "target_ms": 480000,
    "urgency": 0.50,
    "breach_imminent": false
  },
  "turn_count": 8,
  "snapshot_at": "2026-03-16T14:32:00Z",
  "is_stale": false,
  "customer_context": {
    "history_window_days": 30,
    "historical_insights": [...],
    "conversation_insights": [...]
  }
}
```

**Campos relevantes para o painel:**

| Campo | Significado |
|---|---|
| `sentiment.trend` | Calculado sobre `trajectory`: `improving`, `stable`, `declining`. O Agent Assist usa diretamente sem recalcular. |
| `sentiment.alert` | `true` quando `current` está abaixo do `sentiment_alert_threshold` do `supervisor_config` do pool |
| `is_stale` | `true` quando Redis retornou dado em cache por indisponibilidade temporária — o Agent Assist exibe indicação visual sem tratar como erro |
| `historical_insights` | Fatos de interações anteriores carregados no início do contato. Filtrados pelas categorias de `insight_categories` do `supervisor_config` e limitados por `history_window_days` |
| `conversation_insights` | Fatos registrados pelo agente IA durante a conversa via `insight_register`. Crescem a cada turno, expiram ao fechar a sessão |

**Padrão de chamada recomendado:** a cada mensagem recebida ou enviada. Custo apenas de leitura do Redis — o AI Gateway mantém o estado atualizado independentemente.

---

### `supervisor_capabilities`

Retorna capacidades disponíveis e relevantes para o contexto atual, filtradas pelo `supervisor_config` do pool.

**Entrada:**
```json
{ "session_id": "uuid", "pool": "retencao_humano" }
```

**Saída resumida:**
```json
{
  "session_id": "uuid",
  "intent_matched": "portability_check",
  "confidence": 0.87,
  "relevance_model_invoked": false,
  "tools": [
    {
      "tool_id": "mcp-server-telco:portability_check",
      "relevance": "high",
      "reason": "intent_match",
      "interaction_model": "background",
      "available": true,
      "circuit_breaker": "closed"
    }
  ],
  "agents": [
    {
      "agent_type_id": "agente_portabilidade_v2",
      "relevance": "high",
      "reason": "intent_match",
      "interaction_model": "background",
      "version_status": "stable",
      "availability": { "instances_ready": 5, "estimated_wait_ms": 0 }
    },
    {
      "agent_type_id": "agente_autenticacao_v1",
      "relevance": "medium",
      "reason": "flag_active",
      "interaction_model": "conference",
      "channel_identity": { "text": "Assistente", "voice_profile": "assistant_voice_pt_br" },
      "auto_join": true,
      "version_status": "stable",
      "availability": { "instances_ready": 12, "estimated_wait_ms": 0 }
    }
  ],
  "escalations": [
    {
      "pool": "especialista_retencao",
      "reason": "churn_signal + declining_sentiment",
      "recommended": true,
      "estimated_wait_ms": 45000
    }
  ],
  "snapshot_at": "2026-03-16T14:32:00Z"
}
```

**Campos relevantes:**

| Campo | Significado |
|---|---|
| `relevance_model_invoked` | Informa se o modelo foi chamado nesta resposta — registrado no audit log |
| `circuit_breaker` nas tools | Estado atual do circuit breaker do MCP Server. O Agent Assist desabilita o acionamento se `open` — sem o humano tentar e receber erro |
| `recommended` nas escalações | `true` quando a combinação de flags e sentiment sugere fortemente a escalação |
| `interaction_model` | `background` (agente executa sem aparecer ao cliente) ou `conference` (agente entra no canal como participante visível) |

**Padrão de chamada recomendado:** quando o intent muda ou a cada N turnos configurável. Mais custoso que `supervisor_state` — pode invocar modelo de relevância.

---

### `agent_join_conference`

Aciona a entrada de um agente IA na sessão como participante de conferência. O agente IA entra no pool e interage diretamente com o cliente no canal. O agente humano permanece presente durante toda a conferência — vê tudo e pode intervir a qualquer momento.

**Entrada:**
```json
{
  "session_id": "uuid",
  "agent_type_id": "agente_autenticacao_v1",
  "version_policy": "stable",
  "channel_identity": {
    "text": "Assistente",
    "voice_profile": "assistant_voice_pt_br"
  }
}
```

**Saída:**
```json
{
  "conference_id": "uuid",
  "participant_id": "uuid",
  "agent_type_id": "agente_autenticacao_v1",
  "joined_at": "2026-03-16T14:32:00Z"
}
```

O agente IA recebe um `context_package` com o histórico completo da conversa até o momento da entrada. Ao encerrar via `agent_done`, o Escalation Engine remove o participante e registra o evento com `conference_id` e `participant_id`. O `outcome` e `issue_status` do `agent_done` seguem o contrato padrão da seção 4.2 — sem campos adicionais para saída de conferência.

**Suporte a voz:** o modelo de conferência funciona uniformemente em todos os canais. Em canais de voz, o STT/TTS normaliza a interação para o mesmo envelope de eventos de texto. O Channel Layer usa o `voice_profile` declarado para apresentar o agente IA com voz sintética distinta da do agente humano.

---

## Fluxo completo de assistência

```
1. Agente humano inicia atendimento
   ↓
2. Agent Assist chama supervisor_state a cada turno
   → atualiza painel: sentimento, intent, flags, SLA, insights
   ↓
3. Quando intent muda ou a cada N turnos:
   Agent Assist chama supervisor_capabilities
   → atualiza painel: tools disponíveis, agentes sugeridos, escalações
   ↓
4a. Capacidade com interaction_model: "background" e auto_join: true
    → Agent Assist aciona automaticamente (proactive_delegation)
    → agente IA executa em background sem visibilidade ao cliente
    ↓
4b. Capacidade com interaction_model: "conference"
    → Agent Assist apresenta sugestão ao humano
    → humano autoriza → agent_join_conference
    → agente IA entra no canal como "Assistente"
    → humano orquestra, pode intervir a qualquer momento
    ↓
5. sentiment.alert = true ou escalations[].recommended = true
   → Agent Assist destaca no painel — humano decide
```

---

## Painéis do Agent Assist

O Agent Assist organiza as informações em três painéis:

**Painel 1 — Estado da conversa** (atualizado via `supervisor_state`):
- Sentimento atual, trajetória e tendência com alerta visual quando `alert: true`
- Intent corrente e histórico de intents da sessão
- Flags ativas (ex: `churn_signal`, `high_frustration`)
- SLA: tempo decorrido, meta, urgência, `breach_imminent`
- Turno atual

**Painel 2 — Capacidades disponíveis** (atualizado via `supervisor_capabilities`):
- Tools de domínio relevantes com status de circuit breaker
- Agentes IA sugeridos com modelo de interação e disponibilidade
- Escalações recomendadas com justificativa e tempo estimado de espera

**Painel 3 — Contexto do cliente** (dentro de `supervisor_state.customer_context`):
- `historical_insights`: fatos de interações anteriores, com source e last_occurrence visíveis
- `conversation_insights`: fatos identificados nessa conversa via `insight_register`, com confidence e turn de registro

A separação entre histórico e conversa atual é visual — o agente humano sabe imediatamente o que é memória de longo prazo e o que foi identificado nessa sessão.

---

## Configuração via `supervisor_config`

Declarado no registro do pool (seção 4.5). Campos principais:

| Campo | Tipo | Descrição |
|---|---|---|
| `enabled` | bool | Habilita o Supervisor para o pool |
| `sentiment_alert_threshold` | float | Limiar abaixo do qual `sentiment.alert` retorna `true` |
| `proactive_delegation.enabled` | bool | Quando `true`, aciona automaticamente capacidades com `relevance: high` |
| `proactive_delegation.history_window_days` | int | Janela de dias para carregar `historical_insights` no início do contato |
| `proactive_delegation.insight_categories` | string[] | Prefixos de `insight.historico.*` a carregar. Suporta wildcard. Ex: `insight.historico.financeiro.*` |
| `intent_capability_map` | object | Mapeia intents e flags para tools/agentes com relevância e `interaction_model` |

Pools diferentes declaram janelas e categorias distintas — um pool de cobrança carrega `insight.historico.financeiro.*`, um pool de suporte técnico carrega `insight.historico.servico.*`.

---

## Persistência

O Agent Assist não escreve nada — é somente leitura. Toda escrita ocorre via MCP tools autorizadas executadas pelo agente IA durante a conversa.

| Dado lido | Onde vive | Quem escreve |
|---|---|---|
| Estado da sessão (`session:{session_id}:ai`) | Redis | AI Gateway |
| `insight.conversa.*` | Redis (por sessão) | `insight_register` (mcp-server-plughub) |
| `insight.historico.*` | Redis (carregado no início do contato) | Kafka consumer no `contact_closed` |
| Pool config / `supervisor_config` | PostgreSQL via agent-registry | Operador via API de configuração |

---

## Relações com outros módulos

| Módulo | Relação |
|---|---|
| `mcp-server-plughub` | Único ponto de acesso — expõe `supervisor_state`, `supervisor_capabilities`, `agent_join_conference` |
| `ai-gateway` | Produtor do estado de sessão (`session:{session_id}:ai`) que `supervisor_state` lê |
| `rules-engine` | Complementar, não sobreposto: Rules Engine monitora conversas IA; Agent Assist monitora conversas humanas |
| `routing-engine` | Gerencia session affinity e alocação do agente IA acionado via `agent_join_conference` |
| `channel-gateway` | Entrega física das mensagens do agente IA em conferência ao canal do cliente |
| `agent-registry` | Fonte do `supervisor_config` e do `intent_capability_map` por pool |

---

## Relação com o Rules Engine

As responsabilidades são complementares e não se sobrepõem:

| | Rules Engine | Agent Assist |
|---|---|---|
| Monitora | Conversas com agente IA | Conversas com agente humano |
| Aciona | Escalações automáticas | Sugestões — o humano decide |
| Modelo | Push (pub/sub Redis) | Pull (polling por tool call) |
| Config | `rules:active` (Redis) | `supervisor_config` (por pool) |

---

## Referência spec

- Seção 3.2a — Supervisor e Agent Assist
- Seção 4.5 — Agent Registry: `supervisor_config` e `intent_capability_map`
- Seção 9.4 — Supervisor tools: `supervisor_state`, `supervisor_capabilities`, `agent_join_conference`
- Seção 4.2 — Contrato de Execução (contrato do agente IA em conferência)
