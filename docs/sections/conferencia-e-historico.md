# Conferência e Histórico de Contatos

> Spec PlugHub v25.0 — seção proposta  
> Status: rascunho para revisão  
> Data: 2026-04-16  
> Origem: sessão de design 2026-04-16 (ver changelog)

---

## Visão geral

Todo atendimento no PlugHub é modelado como uma **conferência**: um contato com um ou mais participantes ativos. O caso mais simples — um cliente e um agente — é uma conferência com N=1. A conferência com múltiplos participantes não é uma exceção ou modo especial; é a generalização natural do modelo base.

Essa perspectiva unificada elimina code paths distintos para "atendimento normal" e "conferência", simplifica o roteamento e torna o suporte a múltiplos participantes uma consequência da infraestrutura, não uma feature adicional.

---

## 1. Estrutura de um contato

### 1.1 Hierarquia

```
Contact (contact_id)
  └── Session (session_id)        ← perna de canal: WebSocket, WhatsApp, voz
        └── Participant (participant_id)
              ├── instance_id     ← instância do agente (humano ou IA)
              ├── role            ← sender | observer | transformer
              ├── visibility      ← customer_visible | agents_only | hidden
              └── data_policy     ← { pii_mask, accessible_scopes, readonly }
```

Um `contact_id` agrupa todo o ciclo de vida do atendimento — incluindo reconexões do cliente, escalações, transferências e conferências. Um `session_id` representa uma perna ativa dentro do contato: um WebSocket aberto, uma alocação de agente, uma janela de conferência. Quando o cliente reconecta após uma queda, o `contact_id` é o mesmo; o `session_id` é novo.

### 1.2 Ciclo de vida do contato

```
contact_opened
  → [0..N] agent_joined | agent_left       ← participantes entram e saem
  → [0..N] conference_started | conference_ended
  → contact_closed
```

`agent_done` de um participante não fecha o contato — apenas remove aquele participante. O contato fecha quando todos os participantes activos saem ou quando explicitamente encerrado.

---

## 2. Participantes

### 2.1 Tipos

| Tipo | Framework | Identificação |
|---|---|---|
| Agente humano | Agent Assist UI | instance_id = session de operador |
| Agente IA nativo | Skill Flow Engine | instance_id do pool de IA |
| Agente IA externo | external-mcp (spec 4.6k) | instance_id registrado via agent_login |
| Cliente | Channel Gateway | customer_id |

### 2.2 Papéis (role)

| Papel | Comportamento |
|---|---|
| `sender` | Envia mensagens para a audiência declarada; recebe mensagens do stream |
| `observer` | Apenas lê o stream; nunca envia ao canal do cliente nem ao canal interno |
| `transformer` | Intercepta mensagens, transforma e republica; ex: agente de tradução |

### 2.3 Visibilidade (visibility)

| Valor | Quem vê as mensagens deste participante |
|---|---|
| `customer_visible` | Cliente + todos os agentes |
| `agents_only` | Somente agentes (humanos e IAs); cliente não sabe que este participante existe |
| `hidden` | Nenhum participante vê diretamente; apenas o log canônico registra |

### 2.4 Política de dados (data_policy)

```json
{
  "pii_mask": true,
  "accessible_scopes": ["intent", "sentiment", "issue_status"],
  "blocked_insight_categories": ["insight.historico.financeiro.*"],
  "readonly": false
}
```

`pii_mask: true` atribui o participante ao tier de dados mascarado (ver seção 4). `readonly: true` permite que o participante leia o stream mas não publique mensagens — modelo de supervisor/lurk.

---

## 3. Audiência de mensagens

Todo participante declara para quem suas mensagens se destinam ao enviar:

| Audiência | Destinatários |
|---|---|
| `all` | Cliente + todos os agentes participantes |
| `agents_only` | Somente agentes (humanos e IAs); não chega ao canal do cliente |
| `customer_only` | Somente o cliente; não espelhado nos canais internos de agentes |
| `direct:{participant_id}` | Mensagem privada para um participante específico |

**Exemplo de matriz de roteamento:**

| Quem envia | Audiência | Canal do cliente | Stream interno |
|---|---|---|---|
| Agente humano primário | `all` | ✅ `conversations.outbound` | ✅ `agent:events` + stream |
| Supervisor humano | `agents_only` | ❌ | ✅ `agent:events` + stream interno |
| IA visível | `all` | ✅ com `channel_identity.text` | ✅ espelho em `agent:events` |
| IA de sugestão | `agents_only` | ❌ | ✅ stream interno |
| IA auditora | `hidden` (só lê) | ❌ | ❌ |
| IA tradutora | `transformer` | ✅ versão traduzida | ✅ |

---

## 4. Infraestrutura de mensagens — Redis Streams

### 4.1 Streams por contato

```
session:{session_id}:messages:full    ← tier completo, sem mascaramento
session:{session_id}:messages:masked  ← tier mascarado, PII substituído
session:{session_id}:internal         ← canal agents_only (não chega ao cliente)
```

O bridge faz **XADD** nos streams relevantes a cada mensagem inbound do cliente. Cada participante IA tem seu próprio **consumer group** (`agent:{instance_id}`) no stream ao qual está atribuído, recebendo fan-out nativo sem coordenação.

### 4.2 Atribuição de tier

A atribuição ao tier é feita no momento do join e não muda durante a participação:

```
data_policy.pii_mask = false → consumer group em :messages:full
data_policy.pii_mask = true  → consumer group em :messages:masked
```

O bridge decide qual tier usar antes do XADD:

```
Se todos os AIs ativos têm pii_mask=false → XADD apenas em :full
Se há AIs com pii_mask=true              → XADD em :full + XADD (mascarado) em :masked
```

Quando nenhum participante está no tier `:masked`, o XADD mascarado não acontece — custo zero.

### 4.3 Canal interno (agents_only)

Mensagens com `audience: agents_only` vão para `session:{session_id}:internal`, separado dos streams de mensagens do cliente. O cliente nunca tem acesso a esse stream. Participantes IA com `readonly: true` podem criar consumer groups neste stream sem enviar.

### 4.4 Sinal de encerramento

Quando o cliente desconecta, o bridge publica em **todos** os streams ativos da sessão:

```
XADD session:{session_id}:messages:full   * type session_closed reason {reason}
XADD session:{session_id}:messages:masked * type session_closed reason {reason}
XADD session:{session_id}:internal        * type session_closed reason {reason}
```

O `wait_for_message` de cada agente IA detecta o item `type: session_closed` e retorna `mcpError("client_disconnected")`.

### 4.5 Agentes humanos — pub/sub

Agentes humanos não usam XREADGROUP — recebem via Redis pub/sub em `agent:events:{session_id}`. O pub/sub é inerentemente fan-out: múltiplos humanos subscritos ao mesmo canal recebem cada mensagem simultaneamente. O bridge publica em `agent:events:{session_id}` sempre que `session:{session_id}:human_agents` (SET) tem membros.

### 4.6 TTL dos streams

| Chave | TTL | Renovação |
|---|---|---|
| `session:{session_id}:messages:*` | 4h | Renovado a cada XADD |
| `session:{session_id}:internal` | 4h | Renovado a cada XADD |
| Consumer groups | Expiram com a chave | — |

---

## 5. Serviço de mascaramento

### 5.1 Princípio

O mascaramento é uma propriedade do canal, não do destinatário. O dado mascarado é o único que existe no tier `:masked` — não é filtrado após a entrega, é produzido antes do XADD.

### 5.2 Arquitetura

```
Masking Service
  → lê políticas de PII de um repositório central (por tenant)
  → publica masking_profile:{tenant_id} no Redis com TTL longo
  → consumidores (bridge, analytical_writer) fazem GET local e aplicam

Custo por mensagem: leitura de cache Redis (~0.1ms) + aplicação local das regras
Sem chamada de rede no caminho crítico — mesma filosofia do JWT
```

Para casos que exigem inferência semântica (ex: "minha conta termina em 789" contém dado financeiro), o serviço expõe uma tool MCP `mask_text(text, profile_id)` chamada de forma assíncrona e opcional, baseada no nível de sensibilidade declarado no perfil.

### 5.3 Reutilização

O mesmo `masking_profile` é usado em três contextos:

| Contexto | Onde aplicado |
|---|---|
| Real-time inbound | Bridge antes do XADD em `:masked` |
| Real-time outbound | outbound_consumer antes de espelhar ao agente restrito |
| Histórico analítico | analytical_writer antes de escrever no ClickHouse |

Mudança nas regras de PII é feita uma vez no Masking Service. Todos os contextos atualizam via refresh do cache Redis.

---

## 6. Histórico de contatos — Event Sourcing

### 6.1 Princípio

O histórico não é uma "conversa armazenada" — é um **log imutável de eventos** do qual qualquer visão pode ser reconstruída. A unidade de persistência é o `contact_id`, não o `session_id` nem o agent assignment.

### 6.2 Schema do evento

```json
{
  "event_id":            "uuid monotônico",
  "contact_id":          "uuid",
  "sequence":            42,
  "event_type":          "message_sent | agent_joined | agent_left | conference_started | conference_ended | masking_applied | contact_closed",
  "timestamp":           "ISO8601 — fonte confiável, não wall clock",
  "previous_event_hash": "sha256 do evento anterior",
  "event_hash":          "sha256 deste evento",

  "actor": {
    "participant_id": "uuid",
    "agent_type":     "human | ai_native | ai_external | customer",
    "instance_id":    "string",
    "pool_id":        "string",
    "session_id":     "uuid"
  },

  "payload": {
    "text_canonical": "Meu CPF é 123.456.789-00",

    "deliveries": [
      { "participant_id": "humano-1",  "tier": "full",   "masking_policy_id": null },
      { "participant_id": "ia-auth-2", "tier": "masked", "masking_policy_id": "lgpd_std_v1" }
    ],

    "audience": "all | agents_only | direct:{id}"
  }
}
```

**Por que não armazenar N cópias do texto mascarado:** `masking_profile` é determinístico. O texto mascarado pode ser reconstruído a qualquer momento via `apply_masking(text_canonical, masking_policy_id)`. O `deliveries` prova quem viu o quê; a reconstrução prova o que exatamente viram.

### 6.3 Encadeamento de hashes

Cada evento referencia o hash do evento anterior (`previous_event_hash`). Qualquer adulteração em um evento invalida todos os hashes subsequentes — estrutura tipo Merkle. Para litígio, a prova de integridade é: verificar a cadeia de `event_hash` do primeiro ao último evento do `contact_id`.

### 6.4 Dois stores físicos

```
conversations.events (Kafka)
       ↓                         ↓
canonical_writer            analytical_writer
  store imutável               ClickHouse
  acesso restrito              apply_masking(text_canonical, policy_id)
  assinado por evento          text_canonical → pseudonimizado
  retenção longa (regulatória) retenção operacional (meses)
  uso: litígio, compliance     uso: evaluation agent, MLOps, quality
```

O **canonical store** tem o log completo com `text_canonical` e `deliveries`. O **analytical store** tem o mesmo log com `text_canonical` substituído pela versão mascarada via `masking_policy_id` do tenant. Se as regras de mascaramento mudarem, o analytical store pode ser re-derivado integralmente do canônico.

### 6.5 Casos de uso suportados

**Avaliação de atendimento:**
```
Filtrar eventos por actor.participant_id = {agente_avaliado}
Para cada message_sent:
  reconstruir text_as_received = apply_masking(text_canonical, delivery.masking_policy_id)
Evaluation Agent recebe a visão exata que o agente teve
```

**Resolução de litígio:**
```
Buscar todos os eventos do contact_id
Verificar cadeia de previous_event_hash (prova de integridade)
Para cada message_sent: text_canonical + deliveries (prova do que cada agente viu)
actor.session_id → session_token JWT (prova de identidade do agente)
```

**Análise de conferência:**
```
Filtrar eventos no mesmo intervalo de tempo com actor.participant_id distintos
Ver sobreposição de participantes, audiência de cada mensagem, ordem de turno
```

---

## 7. Cenários de uso

### 7.1 Mapa de cenários

| Cenário | Participantes | Variante de audiência |
|---|---|---|
| Atendimento padrão | 1 cliente + 1 agente IA | Todos os turnos: `all` |
| Atendimento humano | 1 cliente + 1 humano | Todos os turnos: `all` |
| Conferência básica | 1 cliente + 1 humano + 1 IA | IA: `all` (visível ao cliente) |
| Supervisão silenciosa | 1 cliente + 1 humano + 1 supervisor | Supervisor: `agents_only` |
| Múltiplos humanos (warm transfer) | 1 cliente + 2 humanos | Ambos: `all`; primário sai após N turnos |
| Múltiplos humanos (equipe) | 1 cliente + N humanos | Cada um: `all` com `channel_identity` distinto |
| Humano + múltiplas IAs visíveis | 1 cliente + 1 humano + N IAs | Cada IA: `all` com identity própria |
| Humano + IA visível + IA interna | 1 cliente + 1 humano + 1 IA visível + 1 IA sugestora | IA sugestora: `agents_only` |
| Pipeline de análise paralela | 1 cliente + 1 humano + N IAs analistas | Todos analistas: `observer` + `agents_only` |
| IA tradutora | 1 cliente (JP) + 1 humano (PT) + 1 IA | IA: `transformer`, `customer_visible` |
| Auditor de compliance | qualquer + 1 IA auditora | IA: `observer` + `hidden` + `pii_mask: false` |
| Orquestrador IA + especialistas | 1 cliente + 1 IA orquestradora + N especialistas | Especialistas: via A2A, não direto ao cliente |

### 7.2 Acionamento de conferência

**Via Agent Assist UI — `@mention` no campo de input do agente:**

```
Agente humano digita: @agente_autenticacao_v1
→ UI detecta padrão @{agent_type_id} no input
→ Chama agent_join_conference({ session_id, agent_type_id, channel_identity })
→ Remove o @mention antes de enviar a mensagem ao cliente
```

Parâmetros opcionais inline: `@agente_autenticacao_v1:{skill:autenticacao,timeout:120}`

**Via `auto_join`** (spec 4.5 supervisor_config):

Quando `intent_capability_map` declara `interaction_model: conference` e `auto_join: true`, o Agent Assist aciona `agent_join_conference` automaticamente quando a intenção é detectada com `relevance: high`.

**Via tool direta** (para agentes externos e testes):

```json
agent_join_conference({
  "session_id": "uuid",
  "agent_type_id": "agente_autenticacao_v1",
  "pool_id": "autenticacao_ia",
  "interaction_model": "conference",
  "channel_identity": { "text": "Assistente", "voice_profile": "assistant_pt_br" },
  "data_policy": { "pii_mask": false }
})
```

---

## 8. Registro de participante

Ao entrar na conferência, o bridge grava o registro do participante no Redis:

```
conference:{conference_id}:participant:{participant_id} → HASH
  instance_id:        string
  agent_type_id:      string
  pool_id:            string
  role:               sender | observer | transformer
  visibility:         customer_visible | agents_only | hidden
  data_policy:        JSON
  channel_identity:   JSON   ← { text, voice_profile }
  stream_tier:        full | masked
  joined_at:          ISO8601
  TTL:                4h (renovado enquanto participante ativo)

conference:{conference_id}:participants → SET de participant_ids
  TTL: 4h
```

O bridge consulta este registro para:
- decidir para quais streams fazer XADD ao receber mensagem inbound
- aplicar `channel_identity` ao entregar mensagem do agente IA ao cliente
- espelhar mensagens do agente IA em `agent:events:{session_id}` (humano vê tudo)
- publicar `conference.agent_completed` quando agente IA chama `agent_done`

---

## 9. Gaps de implementação

Os itens abaixo estão identificados mas não implementados. A Sprint 0 (Redis Streams para `wait_for_message`) foi concluída em 2026-04-16.

| # | Componente | O que implementar | Prioridade |
|---|---|---|---|
| 1 | `orchestrator-bridge` | Incluir `conference_id`, `channel_identity`, `participant_id`, `is_conference` no `context_package` de agentes external-mcp | Alta |
| 2 | `mcp-server-plughub/supervisor.ts` | Gravar `conference:{id}:participant:{id}` e `conference:{id}:identity` no Redis | Alta |
| 3 | `routing-engine/models.py` | Adicionar `channel_identity` a `ConversationInboundEvent` e `RoutingResult` | Alta |
| 4 | `mcp-server-plughub/external-agent.ts` | Adicionar `conference_id` opcional ao `send_message` | Média |
| 5 | `channel-gateway/outbound_consumer.py` | Rotular mensagens de conference agents com `channel_identity.text` | Média |
| 6 | `channel-gateway/outbound_consumer.py` | Espelhar mensagens de agente IA em `agent:events:{session_id}` | Média |
| 7 | `mcp-server-plughub/runtime.ts` | Publicar `conference.agent_completed` ao `agent_done` quando `conference_id` presente | Média |
| 8 | `orchestrator-bridge` | Stream `:masked` — XADD com mascaramento quando participantes têm `pii_mask: true` | Futura |
| 9 | `orchestrator-bridge` | Stream `:internal` — roteamento de mensagens `agents_only` | Futura |
| 10 | `conversation-writer` | Schema de evento com `deliveries` + hash encadeado + assinatura | Futura |
| 11 | Masking Service | Serviço centralizado com `masking_profiles` publicados no Redis | Futura |
| 12 | `analytical_writer` | Consumer do Kafka que aplica mascaramento e escreve no ClickHouse | Futura |
| 13 | Agent Assist UI | Parser de `@mention` no campo de input | Futura |
| 14 | `test-conference-agent.mjs` | Agente de teste para modo conferência | Alta (para testar Sprint 1) |

---

## 10. Referências

- Spec PlugHub v24.0 seção 3.2a — Supervisor, Agent Assist, `agent_join_conference`
- Spec PlugHub v24.0 seção 4.6k — framework external-mcp
- Spec PlugHub v24.0 seção 4.5 — Agent Registry, `supervisor_config`, `intent_capability_map`
- `docs/guias/conferencia-agente-ia-mapeamento.md` — gaps detalhados (Sprint 1)
- `docs/guias/timeouts-e-deteccao-de-falhas.md` — timeouts e detecção de queda
- `packages/mcp-server-plughub/src/tools/external-agent.ts` — `wait_for_message` (Sprint 0 concluída)
- `packages/orchestrator-bridge/src/plughub_orchestrator_bridge/main.py` — `process_inbound` com XADD
