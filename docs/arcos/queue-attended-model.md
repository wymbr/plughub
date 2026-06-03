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

## Rejeição na porta — outcome `outage` (demanda reprimida)

`session_id` é criada **sempre**, mesmo sem recurso de entrada. Sem recurso (teto contratado
ou pool de fila esgotado), o contato é rejeitado **com registro completo**: ANI, DNIS, canal,
endpoint, timestamps.

- `close_reason = no_resource` (gatilho) + `outcome = "outage"` (natureza: rejeitado na
  porta, zero segmentos).
- `outage_cause: quota | pool_exhausted` — distingue "comprar mais licença" de "provisionar
  mais agente".
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

## Padronização de `outcome` e `close_reason` (auditoria)

Evidência de drift: sessão atendida em 03/06 gravada com `close_reason = agent_done` — valor
que **não pertence ao domínio** de close_reason do CLAUDE.md. Itens:

1. **Inventariar** valores reais hoje: `SELECT DISTINCT outcome, close_reason FROM sessions`
   + origem de cada escrita (Core, bridge, Console humano, skill-flow `complete`).
2. **Fechar o domínio de `outcome`** (proposta inicial): `resolved | escalated | transferred
   | abandoned | outage | failed` — Zod em `@plughub/schemas`, validado na escrita (bridge e
   mcp-server `agent_done`), humano (Console) e IA (`complete` step) passam pelo mesmo enum.
3. **Corrigir o mapeamento** `agent_done` → close_reason da sessão (ex.: `flow_complete` /
   `agent_hangup`), nunca o nome do evento.
4. Contrato existente continua: `agent_done` exige `handoff_reason` quando
   `outcome !== "resolved"`; `issue_status` sempre presente.

## Mudanças por componente

| Componente | Mudança |
|---|---|
| **schemas** | enum `outcome`; `pool_kind` + `queue_pool_id` no PoolConfig; `outage_cause` |
| **agent-registry** | CRUD/YAML dos novos campos de pool |
| **routing-engine** | sem recurso → aloca pool de fila (mantém pedido pendente no alvo); no allocated → dispensa agente de fila; escreve `session.queue.position/eta` no ContextStore; sem pool de fila → caminho outage |
| **orchestrator-bridge** | sinal de dispensa ao skill-flow de fila; mapeamento outcome→close_reason auditado |
| **Core** | sessão criada sempre (outage incluso); fechar-sempre no disconnect; metering pula outage |
| **channel-gateway** | render de rejeição por canal; detecção de disconnect → close (caminho atendido já existe) |
| **analytics-api** | `/reports/pools/queue` reescrito sobre segments (pool de fila); KPI demanda reprimida no Volume |
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
