# Changelog 2026-04-16 — Sessão de Design: Conferência e Histórico

## Contexto

Sessão de design explorando o modelo de conferência multi-participante e a estratégia de persistência de histórico de contatos para avaliação e litígio.

---

## Decisões de arquitetura tomadas

### 1. Modelo unificado — tudo é uma conferência

Todo atendimento é modelado como conferência com N participantes. O caso N=1 é o atendimento padrão; a conferência multi-agente é a generalização. Elimina code paths distintos para "modo normal" e "modo conferência".

Hierarquia: `Contact → Session → Participant` com atributos `role`, `visibility`, `data_policy`.

### 2. Sprint 0 implementada — Redis Streams para `wait_for_message`

Migração de BLPOP para XREADGROUP em `mcp-server-plughub/src/tools/external-agent.ts`:

- Consumer group por `instance_id` → fan-out nativo para múltiplos agentes IA simultâneos
- Sem `menu:waiting` flag — stream persiste sem coordenação explícita
- `session_closed` via item no stream (`type: session_closed`) em vez de chave separada
- Parâmetro `conference_id` opcional: offset `0` para ler histórico do stream desde o join
- Bridge atualizado: `process_inbound` faz XADD + mantém LPUSH legado para Skill Flow
- Bridge atualizado: `session:closed` também faz XADD no stream quando há consumer groups

Build: limpo (`npm run build` sem erros).

### 3. Audiência de mensagens como atributo do envio

Cada mensagem declara `audience: all | agents_only | customer_only | direct:{id}`. Isso resolve os cenários de supervisor que orienta o agente sem o cliente ouvir, IA de sugestão invisível ao cliente, e mensagens privadas entre participantes.

### 4. Dois planos de transporte — customer plane e internal plane

```
Customer plane:   conversations.outbound + session:{id}:messages:full|masked
Internal plane:   session:{id}:internal + agent:events:{session_id}
```

Mensagens `agents_only` nunca transitam pelo customer plane — isolamento de canal, não filtragem.

### 5. Serviço de mascaramento centralizado

`masking_profile:{tenant_id}` publicado no Redis pelo Masking Service. Consumidores (bridge, analytical_writer) aplicam localmente — sem rede no caminho crítico. Reutilizado em real-time inbound, real-time outbound e histórico analítico.

### 6. Dois tiers de stream

`session:{id}:messages:full` e `session:{id}:messages:masked`. Participante é atribuído ao tier no join via `data_policy.pii_mask`. Mascaramento acontece uma vez por tier no XADD, não N vezes por participante.

### 7. Event sourcing para histórico

Log imutável de `ConversationEvent` com:
- `contact_id` como unidade de persistência (não session)
- `text_canonical` + `deliveries[]` (quem recebeu, com qual masking_policy_id)
- Hash encadeado (previous_event_hash) para integridade — prova de não adulteração
- Timestamp de fonte confiável

Dois stores físicos:
- **canonical_writer**: dado completo, assinado, acesso restrito, retenção longa (litígio)
- **analytical_writer**: pseudonimizado via masking_profile, ClickHouse, retenção operacional (avaliação)

---

## Documentos criados

| Arquivo | Descrição |
|---|---|
| `docs/sections/conferencia-e-historico.md` | Spec completa do modelo unificado (v25.0 proposta) |
| `docs/guias/conferencia-agente-ia-mapeamento.md` | Gaps de implementação detalhados para Sprint 1 |
| `docs/guias/timeouts-e-deteccao-de-falhas.md` | Referência de timeouts (criado na mesma data) |

## Código alterado

| Arquivo | Mudança |
|---|---|
| `packages/mcp-server-plughub/src/tools/external-agent.ts` | Sprint 0: `wait_for_message` BLPOP → XREADGROUP |
| `packages/orchestrator-bridge/.../main.py` | Sprint 0: `process_inbound` XADD + `session:closed` XADD |

---

## Cenários de uso mapeados

1 cliente + 1 IA (padrão) · 1 cliente + 1 humano · supervisão silenciosa · warm transfer · equipe multi-humano · humano + múltiplas IAs (visíveis ou internas) · pipeline de análise paralela · IA tradutora · auditor de compliance · orquestrador IA + especialistas
