# Arc 14 — Pós-Atendimento: Segmentos Independentes

> **Status**: Em especificação — validação em andamento. Nenhuma fase implementada.

---

## Problema

O modelo atual de hooks de pós-atendimento (`on_human_end`) trata todos os segmentos de pós-atendimento como um bloco monolítico: um contador único `hook_pending` aguarda a conclusão de **todos** os hooks antes de liberar qualquer infraestrutura. Isso causa três problemas concretos:

| # | Sintoma | Causa |
|---|---------|-------|
| P1 | Wrap-up aguarda NPS terminar (e vice-versa) antes de fechar sua interface | `_trigger_contact_close()` só dispara quando `hook_pending == 0` |
| P2 | WebSocket do cliente permanece aberto até o wrap-up terminar, mesmo após NPS concluído | Infraestrutura da conferência só é destruída no `_trigger_contact_close()` único |
| P3 | Agente fica disponível para próximo contato antes do wrap-up terminar | `remove_conversation()` do humano dispara imediatamente no `agent_done`, sem aguardar wrap-up |

Além disso, o cenário de queda do cliente (F5, perda de conexão) é tratado com a mesma lógica, apesar de ser conceitualmente idêntico: o contato encerrou, os hooks devem funcionar como segmentos independentes.

---

## Conceitos e Terminologia

```
Contato        → atendimento do ponto de vista do cliente (Layer 1)
Segmento       → atendimento do ponto de vista de agentes (Layer 2)
Conferência    → infraestrutura técnica compartilhada (Layer 3): stream, pub/sub, Redis keys
Pós-atendimento (posatt) → segmentos que reutilizam a infraestrutura da conferência após o
                           encerramento do contato para executar tarefas de pós-contato
```

O Three-Layer Model do CLAUDE.md descreve que as três camadas são **independentes** e não devem ser colapsadas. Este Arc aplica esse princípio especificamente aos segmentos de pós-atendimento.

---

## Modelo Proposto

### Princípio central

**Segmentos de pós-atendimento são independentes entre si.** Cada segmento:
- Tem seu próprio ciclo de vida (início, execução, fim)
- Finaliza seus próprios participantes quando termina (envia `session.closed` apenas para quem participa daquele segmento)
- Não sabe nem depende do estado dos outros segmentos de pós-atendimento

**A infraestrutura da conferência é liberada pelo último segmento a terminar.** Não existe mais um `_trigger_contact_close()` centralizado que aguarda todos. O mecanismo de rastreamento (`posatt:active` counter) detecta quando o último segmento saiu e então destrói a conferência.

### Ciclo de vida por camada

```
Layer 1 — Contato (encerra imediatamente ao evento de close)
  ├─ Congela contadores de tempo (AHT, SLA)
  ├─ Fecha canal do cliente (WebSocket do cliente, se aplicável)
  ├─ Publica evento de encerramento do contato para analytics
  └─ Dispara hooks posatt como segmentos independentes

Layer 2 — Segmentos de pós-atendimento (cada um independente)
  ├─ Segmento Wrap-up
  │    ├─ Participantes: agente humano + agente wrap-up
  │    ├─ Ao terminar: envia session.closed para seus participantes
  │    ├─ Ao terminar: libera agente humano para próximo contato
  │    └─ DECR posatt:active → se == 0: destroy conference (Layer 3)
  └─ Segmento NPS
       ├─ Participantes: cliente + agente NPS
       ├─ Ao terminar: envia session.closed para seus participantes
       ├─ Ao terminar: fecha WebSocket do cliente (se ainda aberto)
       └─ DECR posatt:active → se == 0: destroy conference (Layer 3)

Layer 3 — Conferência (infraestrutura compartilhada)
  ├─ Permanece ativa enquanto posatt:active > 0
  └─ Destruída pelo último segmento posatt que sair
```

### Comportamento esperado por cenário

**Agente clica Desligar:**
1. Contato encerra → AHT congelado
2. Canal do cliente fecha imediatamente (se conectado)
3. `posatt:active = 2` (wrap-up + NPS)
4. Wrap-up termina primeiro → `session.closed` para agente humano + agente wrap-up → agente disponível para próximo contato → `posatt:active = 1`
5. NPS termina → `session.closed` para cliente → `posatt:active = 0` → conferência destruída

**Cliente desconecta (F5 / perda de conexão):**
1. Contato encerra → AHT congelado
2. Canal do cliente já foi embora → cleanup imediato do lado cliente
3. `posatt:active = 2` (wrap-up + NPS)
4. NPS: sem cliente para enviar → timeout ou skip imediato → `posatt:active = 1`
5. Wrap-up segue normalmente → conclui → agente liberado → `posatt:active = 0` → conferência destruída

**Apenas wrap-up configurado (sem NPS):**
1. Contato encerra
2. `posatt:active = 1`
3. Wrap-up termina → agente liberado → `posatt:active = 0` → conferência destruída

---

## Infraestrutura Redis

### Chaves novas

| Chave | Tipo | TTL | Descrição |
|-------|------|-----|-----------|
| `session:{id}:posatt:active` | STRING (contador) | 4h | Número de segmentos posatt ainda ativos |
| `session:{id}:posatt:{conference_id}:type` | STRING | 4h | Tipo do segmento: `wrap-up`, `nps`, etc. |
| `session:{id}:posatt:{conference_id}:participants` | SET | 4h | `participant_id` dos envolvidos no segmento |
| `session:{id}:posatt:{conference_id}:side` | STRING | 4h | `agent` ou `customer` — quem é o "dono" da interface |

### Chaves existentes (comportamento alterado)

| Chave | Mudança |
|-------|---------|
| `session:{id}:hook_pending:{type}` | Mantida para tracking de conclusão dos hooks (sem mudança) |
| `session:{id}:hook_conf:{conference_id}` | Mantida — agora também registra `side` do segmento |

---

## Mudanças por Componente

### orchestrator-bridge — `fire_pool_hooks()`

- Ao disparar cada hook: `INCR session:{id}:posatt:active`
- Registra `session:{id}:posatt:{conference_id}:type` e `session:{id}:posatt:{conference_id}:side`
- Popula `session:{id}:posatt:{conference_id}:participants` com os participant_ids esperados

### orchestrator-bridge — `process_routed()` (detecção de conclusão de hook)

Ao detectar que um agente com `hook_conf` key completou:

```
1. Identificar o conference_id e o side do segmento
2. Publicar session.closed SOMENTE para os participantes do segmento
   (em vez de broadcast para todos via agent:events)
3. Se side == "agent": marcar agente humano como disponível
   (somente o wrap-up desbloqueia o agente)
4. DECR session:{id}:posatt:active
5. Se posatt:active == 0: chamar _destroy_conference()
```

### orchestrator-bridge — `_trigger_contact_close()` → `_destroy_conference()`

A função atual é dividida em duas responsabilidades distintas:

- **`_close_contact_layer()`** — chamada imediatamente no evento de close (agent_done ou customer_disconnect):
  - Congela contadores
  - Fecha canal do cliente
  - Publica evento de encerramento do contato para analytics/Kafka

- **`_destroy_conference()`** — chamada pelo último segmento posatt a terminar (via `posatt:active == 0`):
  - Deleta keys da conferência (stream, meta, ctx, etc.)
  - Publica `session.closed reason=conference_destroyed` para qualquer subscriber restante
  - Publica Kafka `conversations.outbound session.closed` final

### orchestrator-bridge — bloqueio do agente para próximo contato

Mudança no fluxo do `agent_done` humano:

- **Hoje**: `remove_conversation()` → DECR active_count → agente `ready` → routing pode alocar → Console recebe novo contato
- **Proposto**: `remove_conversation()` → DECR active_count → agente em estado `wrap_up_pending` → routing **não** aloca → só muda para `ready` quando wrap-up segment conclui

Implementação: nova flag `session:{id}:agent_wrap_up_pending:{instance_id}` impede que o routing engine marque o agente como `ready` enquanto o segmento de wrap-up ainda está ativo. Quando o segmento wrap-up conclui, a bridge deleta a flag e publica `agent_ready` para o routing engine.

### mcp-server-plughub — `session.closed` targeted

Hoje `session.closed` é publicado em `agent:events:{session_id}` sem destinatário específico — todos os subscribers recebem. Para a independência de segmentos, precisamos de um campo `recipients` opcional:

```json
{
  "type": "session.closed",
  "session_id": "...",
  "reason": "posatt_segment_complete",
  "recipients": ["part_abc", "part_xyz"]   // null = broadcast para todos
}
```

O mcp-server filtra: só faz teardown da conexão do agente se seu `participant_id` estiver em `recipients` (ou se `recipients` for null).

---

## Cenário de Queda do Cliente (F5 / Disconnect)

O tratamento é idêntico ao `agent_done` na camada do contato:

1. `customer_disconnect` → `_close_contact_layer()` imediatamente
2. Canal do cliente fechado (WebSocket já foi embora)
3. Hooks disparados normalmente como segmentos independentes
4. NPS: sem cliente disponível → o segmento NPS recebe timeout ou skip imediato (configurável por pool: `nps_on_disconnect: skip|timeout`)
5. Wrap-up: segue normalmente

Isso elimina a assimetria atual onde o código de `customer_disconnect` e `agent_done` tomam caminhos diferentes apesar de ter o mesmo objetivo: encerrar o contato e disparar pós-atendimento.

---

## Decisões de Design (Resolvidas)

**D1 — Bloqueio do agente: routing engine, não UI**

O bloqueio é no routing engine — o agente (humano ou IA) não volta ao estado `ready` até que o segmento de wrap-up conclua. Isso previne que a plataforma roteie um contato para um agente que pode estar com problemas (browser travado, instância não responsiva). Reutiliza a infraestrutura de pausa mas com `reason_id = "system_wrap_up"` (sistema-iniciado, auto-resolvido) — semântica distinta da pausa operador-iniciada.

Aplica-se a **humanos e IA**:
- Humano: `agent_done` → `remove_conversation()` DECR active_count mas não publica `agent_ready` → wrap-up conclui → bridge publica `agent_ready`
- IA: instância permanece em estado `busy` até skill flow do wrap-up terminar (já é o comportamento natural)

Timeout de bloqueio: configurável por pool (`wrap_up_timeout_s`, default herdado do `_HOOK_TIMEOUT_S` atual). Após timeout o agente é liberado incondicionalmente.

**D2 — NPS em disconnect: skill-flow decide**

Sem mudança de infraestrutura. `close_origin` já é escrito no ContextStore antes dos hooks (`_write_pre_hook_context`). O skill NPS lê `@ctx.session.close_origin` e define o comportamento via branches normais do skill-flow: pode encerrar sem enviar, tentar canal alternativo via workflow, ou aguardar timeout. Cada operador define a política no skill YAML.

**D3 — Segmento criado no invite; `segment_id` gerado por hook**

### Terminologia

```
session_id   → identificador do atendimento/conferência original (já existia, não muda)
               é o elo que associa o pós-atendimento ao atendimento — nenhuma associação nova necessária
segment_id   → UUID gerado no momento do invite, um por hook, identifica o segmento posatt
               mapeia para o campo conference_id no wire (routing engine já interpreta conference_id
               como "invite de especialista" — o nome do campo no payload não muda por compatibilidade)
```

O segmento posatt é criado no momento do invite (`fire_pool_hooks()`). Cada hook gera um `segment_id` UUID único. Não há ambiguidade mesmo com múltiplos hooks do mesmo tipo em paralelo (ex: dois wrap-ups), pois cada invite tem seu `segment_id` distinto.

### Registro de participantes

No momento do invite, a bridge já conhece e registra o lado fixo do segmento:
- `side: customer` → participant fixo = `customer_participant_id` (ContextStore)
- `side: agent` → participant fixo = `human_agent_participant_id` (ContextStore)

Ambos já disponíveis via `_write_pre_hook_context` antes dos hooks dispararem.

Quando o agente de hook entra na conferência (`process_routed` recebe `conversations.routed` com `conference_id` = `segment_id`), a bridge localiza o segmento via `posatt:{segment_id}:*` e adiciona o `participant_id` do agente. Registro: lado-fixo no invite + agente-hook no join.

**D4 — Journey/collect: isolado por session_id**

`posatt:active` é escopado por `session_id`. Sessions de collect e journey têm `session_id` próprio — não há contaminação cruzada. A única precaução necessária: verificar que nenhuma key da conferência usa `journey_id` como escopo (não usa — tudo é `session:{id}:*`). Collect sessions normalmente não têm hooks posatt (são AI-only), mas se tiverem, seguem o mesmo modelo independentemente.

**D5 — `side` explícito na config do hook YAML**

Campo `side` adicionado a cada entry de hook no pool YAML:

```yaml
hooks:
  on_human_end:
    - pool: wrapup_ia
      side: agent     # interage com o agente humano (primary)
    - pool: nps_ia
      side: customer  # interage com o cliente
```

Default quando omitido: `side: agent` (backward compatible — todos os hooks existentes são agent-side). NPS e equivalentes exigem declaração explícita. Campo adicionado ao `PoolHooksSchema` em `@plughub/schemas`.

**D6 — Multiple human agents: bloqueio apenas no primary**

Quando há supervisor + agente primary, o bloqueio de wrap-up se aplica apenas ao **primary** (quem estava atendendo o contato). O supervisor pode sair livremente. `human_agent_participant_id` no ContextStore já referencia o primary — é esse o participant_id que o segmento wrap-up bloqueia.

---

## Fases de Implementação (Proposta)

| Fase | Escopo | Dependências |
|------|--------|--------------|
| **A** | Introduzir `posatt:active` counter; `_close_contact_layer()` separado de `_destroy_conference()`; conferência destruída pelo último segmento | Nenhuma |
| **B** | `session.closed` com `recipients` targeted; cada segmento fecha apenas seus participantes | Fase A |
| **C** | Bloqueio do agente humano para próximo contato até wrap-up terminar | Fase A |
| **D** | NPS skip/timeout configurável em disconnect (`nps_on_disconnect`) | Fase A |

---

## Impacto em Componentes Existentes

| Componente | Impacto |
|------------|---------|
| `orchestrator-bridge/main.py` | Alto — `fire_pool_hooks`, `process_routed`, `_trigger_contact_close` redesenhados |
| `mcp-server-plughub/server.ts` | Médio — `session.closed` com `recipients` field |
| `routing-engine/registry.py` | Baixo — nova flag `wrap_up_pending` para bloquear `mark_ready` |
| `platform-ui/AgentAssistContext.tsx` | Baixo — respeitar `recipients` no `session.closed` |
| `@plughub/schemas` | Baixo — novo campo `recipients` em `SessionClosedEvent` |

---

*Criado em 2026-05-16. Pendente de validação antes de iniciar Fase A.*
