# Fila Sempre Atendida — Modelo Unificado de Fila

> Estado: **spec / ADR** (não implementado). Decisões fechadas em 2026-06-03.
> Contexto: bugs de visibilidade de fila no relatório de Pools (Fase 2) revelaram que a fase
> de fila do ciclo do contato é sub-registrada na origem. Ver `docs/arcos/pools-infra-report.md`.

---

## Problema

Um contato em fila hoje deixa só fragmentos: evento `queued` no `queue_events`, sessão sem
`pool_id` (ou sem sessão), nenhum evento de saída da fila, nenhum fechamento no
abandono/desconexão (`close_reason` NULL eterno), nenhum `wait_time_ms`. Evidência (03/06,
`retencao_humano`): 3 eventos `queued`; só 1 sessão com ciclo completo; 1 sessão aberta
eterna pós-F5; 1 sessão invisível no pool. Nem o relatório de Pools nem Analytics/Sessions
conseguem contar fila, abandono ou espera — o dado não existe na origem.

## Decisão central — fila deixa de ser estado especial

**Todo enfileiramento vira alocação imediata num pool de tratamento de fila.** O Routing
Engine, ao não conseguir recurso no pool-alvo, em vez de suspender o contato mudo, aloca um
agente de fila (agente IA comum, skill-flow configurável por pool). Elimina a dualidade
fila/atendimento como o Arc 19 eliminou workflow/contato: o contato está **sempre** numa
conferência com alguém.

Consequência analítica: **o ledger de fila são os `segments`** — infraestrutura existente:

| Métrica | Derivação |
|---|---|
| queued | sessões com segmento em pool de fila |
| espera | duração do segmento de fila (start → handoff ao alvo) |
| abandono | segmento de fila terminando em `customer_*` sem segmento subsequente no alvo |
| fila ao vivo | concorrência do pool de fila (SET/SCARD já implementado p/ ocupação) |
| transição fila→alvo | `agent_transfer` + `sequence_index` (semântica existente) |

Sem `queue_exit` novo, sem tabela nova, sem topic novo. `queue_events` permanece como
suplementar (posição histórica), não como fonte de verdade.

## Condições do modelo (invariantes novos)

1. **O pedido pendente no pool-alvo continua existindo no Routing Engine.** O agente de fila
   *acompanha* a espera, não a substitui — FIFO, posição e SLA vivem no pedido. Quando o alvo
   libera, o Routing aloca (árbitro único) e dispara a **dispensa** do agente de fila
   (bridge sinaliza o skill-flow — mesmo padrão do terminate dos pool hooks).
2. **Pools de fila são marcados**: `pool_kind: queue_treatment` no PoolConfig; o pool-alvo
   aponta `queue_pool_id`. O agente de fila entra como `primary` normal; o invariante
   analítico passa a ser: **"atendido" = primeiro segmento `primary` em pool não-fila**.
3. **Canal é hard filter**: o pool de fila deve cobrir os `channel_types` do pool-alvo
   (voz exige capacidade de mídia — música de espera + anúncios são um skill-flow).
4. **Custo controlável**: o skill-flow de fila pode ser mecânico (`notify` + `receive` +
   posição via ContextStore escrita pelo Routing — `session.queue.position`, `session.queue.eta`)
   com **zero chamada de LLM**. "Fila muda" é um skill-flow que não fala nada.

## Outcome mora no segmento (decisão 2026-06-03)

**`segments.outcome` é a única fonte de verdade de outcome.** Motivos: (1) evita dupla
escrita independente (a auditoria abaixo mostrou exatamente essa classe de drift);
(2) `segment.pool_id` identifica **qual recurso faltou**, na entrada ou no meio do
atendimento — um outcome solto na sessão nunca diria.

- **`sessions.outcome` = derivação, nunca escrita independente**: outcome do último
  segmento `primary` (`argMax(outcome, sequence_index)`). Pode ser denormalizado na
  linha da sessão no close (lista do Analytics sem JOIN), mas se divergir o segmento ganha.
- **Falta de recurso = segmento sintético** emitido pelo **Routing Engine** (quem sabe da
  falha): `pool_id` = pool que faltou, `agent_type = "system"`, `role = primary`,
  `outcome = "outage"`, `duration_ms = 0`. Mesmo mecanismo na porta e no meio do contato.
- **Segmentos `system` excluídos das métricas de agente** (AHT, resolução, performance
  routing Arc 7d): filtro `agent_type != 'system'`. Demanda reprimida sai direto deles:
  outage por pool × canal × tempo.
- `close_reason` permanece da **sessão** (é sobre o fim da sessão) — ortogonal ao outcome.

Com a fila sempre atendida, **o ledger universal do contato são os segments**: fila =
segmento de pool de fila; atendimento = segmento normal; rejeição = segmento system.

## Rejeição na porta — outcome `outage` (demanda reprimida)

`session_id` é criada **sempre**, mesmo sem recurso de entrada. Sem recurso (teto contratado
ou pool de fila esgotado), o contato é rejeitado **com registro completo**: ANI, DNIS, canal,
endpoint, timestamps.

- `close_reason = no_resource` (gatilho, na sessão) + **segmento sintético**
  `outcome = "outage"` (ver seção acima) apontando o pool que faltou.
- `outage_cause: quota | pool_exhausted` (no segmento sintético, via tag/campo) — distingue
  "comprar mais licença" de "provisionar mais agente".
- **Metering**: sessão outage NÃO incrementa a dimensão `sessions` (guard SET NX no Core
  pula outage) — cliente não paga por contato rejeitado.
- Channel Gateway renderiza a rejeição por canal (voz: anúncio/busy; webchat: mensagem) —
  o cliente nunca "cai" sem resposta.

Vira métrica de primeira classe: **demanda reprimida** por canal/endpoint/tempo — base de
redimensionamento e upsell.

## Falta de recurso no meio do contato — cadeia de fallback

Drop é **último recurso**, nunca reação imediata:

1. **`catch` do skill-flow** do agente que tentou escalar (retry/fallback — já existe).
2. **Sem catch**: drop gracioso — `notify` ao cliente + fecha `close_reason = no_resource`.
3. **Bound de retenção**: `max_wait_exceeded` é o teto de espera (política de liberação =
   timeout, não drop imediato).

Nos dados, dois casos distintos: **rejeitado na porta** (`outcome=outage`, zero segmentos —
demanda perdida) ≠ **derrubado no meio** (`close_reason=no_resource` com segmentos —
jornada quebrada). KPIs e telas diferentes.

Com a fila sempre atendida, o caso meio-de-contato fica raro por construção: escalação para
pool cheio cai no agente de fila; o degenerado só ocorre quando até o pool de fila esgota.

## Padronização de `outcome` e `close_reason` (auditoria — achados 2026-06-03)

Inventário (`SELECT outcome, close_reason, count() FROM sessions GROUP BY ...`):
`outcome` NULL em **100%**; close_reason ∈ {`client_disconnect` 71, `agent_done` 43, NULL 29}
— **nenhum valor pertence ao domínio** de close_reason do CLAUDE.md.

**Causa raiz** (dois escritores do evento `contact_closed` em `conversations.events`):

1. Bridge `_close_contact_layer` (main.py:1376): `"reason": "agent_done"` **hard-coded**,
   qualquer que seja o desfecho real (43×).
2. channel-gateway webchat adapter: `close_reason = "client_disconnect"` no drop do WS (71×).
3. `outcome` nunca incluído no evento — o bridge o recebe no `agent_done` e publica nos
   segments, mas não propaga ao `contact_closed`.
4. 29 NULL = `contact_closed` nunca disparou (fila muda + sessões ativas).

**Restrição de design**: `client_disconnect`/`agent_done`/`timeout` são contrato de
**transporte vivo** (bridge `customer_side` em main.py:3515; Console reage a
`client_disconnect`). A Fase A **não toca o wire** — mapeia transporte→negócio só na
emissão do evento analítico:

| Transporte + contexto | close_reason (negócio) |
|---|---|
| `client_disconnect` + já atendido | `customer_disconnect` |
| `client_disconnect` + nunca atendido | `customer_abandon` |
| `agent_done` + IA `complete` | `flow_complete` |
| `agent_done` + humano encerrou | `agent_hangup` |
| `timeout` | `session_timeout` |

**Plano Fase A:**

1. Bridge: `contact_closed` ganha `close_reason` (negócio, via tabela acima); o `outcome`
   da sessão é **derivado do último segmento primary** (regra da seção "Outcome mora no
   segmento") e denormalizado no evento; campo `reason` (transporte) permanece para compat.
2. channel-gateway: idem no caminho de disconnect (`close_reason` de negócio no evento
   analítico; transporte intacto).
3. analytics models.py:255: inverter prioridade — `payload.get("close_reason") or
   payload.get("reason")` (compat retroativa).
4. **Fechar o domínio de `outcome`**: `resolved | escalated | transferred | abandoned |
   outage | failed` — Zod em `@plughub/schemas`, validado na escrita (`agent_done` no
   mcp-server), humano (Console) e IA (`complete` step) passam pelo mesmo enum.
5. Contrato existente continua: `agent_done` exige `handoff_reason` quando
   `outcome !== "resolved"`; `issue_status` sempre presente.

## Mudanças por componente

| Componente | Mudança |
|---|---|
| **schemas** | enum `outcome`; `pool_kind` + `queue_pool_id` no PoolConfig; `outage_cause` |
| **agent-registry** | CRUD/YAML dos novos campos de pool |
| **routing-engine** | sem recurso → aloca pool de fila (mantém pedido pendente no alvo); no allocated → dispensa agente de fila; escreve `session.queue.position/eta` no ContextStore; sem pool de fila → caminho outage com **segmento sintético** (`agent_type=system`, `outcome=outage`) em `conversations.participants` |
| **orchestrator-bridge** | sinal de dispensa ao skill-flow de fila; mapeamento outcome→close_reason auditado |
| **Core** | sessão criada sempre (outage incluso); fechar-sempre no disconnect; metering pula outage |
| **channel-gateway** | render de rejeição por canal; detecção de disconnect → close (caminho atendido já existe) |
| **analytics-api** | `/reports/pools/queue` reescrito sobre segments (pool de fila); KPI demanda reprimida no Volume; filtro `agent_type != 'system'` nas métricas de agente |
| **platform-ui** | aba Fila/SLA consome o novo shape; badge outage em Analytics/Sessions |

## Fases sugeridas

- **A — Padronização outcome/close_reason** (auditoria + enum + correção de escrita). Pré-requisito de tudo.
- **B — Outage na porta** (sessão sempre criada + render de rejeição + metering skip). Independente da fila atendida.
- **C — Fila atendida** (pool_kind, alocação de fila no Routing, dispensa, ContextStore posição/eta).
- **D — Relatório de Fila/SLA sobre segments** + demanda reprimida no Volume.
- **E — Fechar-sempre / cadeia de fallback** (catch → notify+close → max_wait).

## O que substitui no relatório atual

A derivação de espera por `LEFT JOIN segments` implementada em 2026-06-03 no
`/reports/pools/queue` (ver `pools-infra-report.md` § dívida de origem) fica como **interim**
até a Fase D — ela mede só contatos atendidos; abandono/fila incompleta permanecem
conhecidamente errados até lá.
