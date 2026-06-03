# Fila Sempre Atendida — Modelo Unificado de Fila

> Estado: **Fases A–D implementadas e validadas** (2026-06-03); resta a Fase E
> (fechar-sempre / cadeia de fallback). Decisões fechadas em 2026-06-03.
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
2. ~~**Pools de fila são marcados**: `pool_kind: queue_treatment` no PoolConfig; o pool-alvo
   aponta `queue_pool_id`. O agente de fila entra como `primary` normal; o invariante
   analítico passa a ser: **"atendido" = primeiro segmento `primary` em pool não-fila**.~~
   **Superseded pela Fase C (2026-06-03)**: o agente de fila roda **no próprio pool-alvo**
   (mecanismo `queue_config` existente, descoberta B0) e o segmento dele ganha
   **`role: queue`** — `pool_id` do segmento permanece o pool-alvo (a dimensão certa do
   relatório Fila/SLA). Invariante analítico: **"atendido" = primeiro segmento `primary`**.
   Métricas de agente (filtros `primary`/`specialist`) excluem fila por construção.
   `pool_kind`/`queue_pool_id` dispensados no MVP.
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
- `outage_cause: reservation_full | shared_full | quota` (no segmento sintético, via
  tag/campo) — ver § Admissão híbrida. Distingue "aumentar reserva do pool" de "comprar
  mais capacidade/licença".
- **Metering**: sessão outage NÃO incrementa a dimensão `sessions` (guard SET NX no Core
  pula outage) — cliente não paga por contato rejeitado.
- Channel Gateway renderiza a rejeição por canal (voz: anúncio/busy; webchat: mensagem) —
  o cliente nunca "cai" sem resposta.

Vira métrica de primeira classe: **demanda reprimida** por canal/endpoint/tempo — base de
redimensionamento e upsell.

## Admissão híbrida — reserva por pool + shared dinâmico (decisão 2026-06-03)

Modelo estático puro (todo pool com `max_session_pool`) bloqueia contato em pool no teto
enquanto a instalação ainda tem capacidade livre — desperdício por configuração. Adota-se
o modelo híbrido (*trunk reservation*):

- **`max_session_pool` é opcional e vira reserva**: teto **e** garantia (fatia dedicada).
  Pool reservado aloca até a reserva, nunca mais.
- **Pools sem reserva disputam o shared** = `max_session_total − Σ reservas` (contador
  compartilhado único).
- **Invariante**: `Σ max_session_pool ≤ max_session_total` — validado na escrita da config
  (Registry) e re-checado na reconciliação do Bootstrap (warning).
- **Enforcement no Routing Engine** (árbitro único): INCR-check-rollback (padrão
  `assertQuota`) sobre os contadores de sessões ativas já existentes (SET-based, Fase 2
  ocupação) + `{t}:quota:concurrent_sessions`.
- **`outage_cause` refinado**: `reservation_full` (pool reservado no teto) |
  `shared_full` (capacidade comum esgotada) | `quota` (teto contratado). O segmento
  sintético aponta o pool → demanda reprimida diz **qual ação tomar** (aumentar reserva
  vs comprar capacidade).
- **Mudança de reserva em runtime**: reduzir abaixo do uso corrente não derruba sessão —
  bloqueia novas entradas até drenar (convergência preguiçosa).
- **Transferência cross-pool** para pool reservado cheio → cadeia de fallback (abaixo),
  sem regra nova.
- **Billing: só o `max_session_total` é billável.** Reserva é *carving* do total
  (subtraída do shared), nunca um item de cobrança próprio.
- **Sem regra de "primeira entrada"**: a admissão roda a cada routing request contra o
  bucket do pool solicitado. Contadores são SETs de session_id → re-publish (drain,
  crash-recovery) é idempotente por construção. Escalação cross-pool = **migração de
  bucket** (SADD+check no destino; sucesso → SREM origem; destino cheio → cadeia de
  fallback, sessão permanece no bucket de origem). Pool de entrada sem reserva consome
  shared — um canal pode drenar o shared inteiro; quem quer proteção, reserva (by design).
- **Outage sempre aponta o pool**: `reservation_full` → o próprio pool; `shared_full` →
  pool solicitado na entrada (segmento sintético carrega `pool_id` + causa).
- **Release**: reconciler periódico no Routing (SSCAN buckets × marcador
  `session:{id}:closed`) — lag de ~60s aceitável para gauge de admissão; TTL backstop.
- **Feedback loop**: relatório de Capacidade mostra utilização da reserva (ociosidade =
  preço da garantia) e do shared — calibragem por dado.
- **v2 (deferred)**: flag `burstable` — reserva como piso com empréstimo do shared
  (contabilidade bidirecional, fora do MVP).

### Relação com `max_concurrent_sessions` (decisão 2026-06-03)

São dimensões distintas com ciclos de vida distintos: **reserva** = carving do contrato
(comercial, opt-in, raramente muda); **`max_concurrent_sessions`** = capacidade/deploy
(operacional). Não unificar: reserva precisa ser opt-in (se todo deploy reservasse, o
shared morre e o modelo volta a ser estático); pool humano nem tem valor de deploy
(capacidade emergente dos agentes logados). UX futura: checkbox "Reservar sessões do
contrato" pode espelhar um número só nos dois campos + warning quando divergirem.

**Webhook pools**: a capacidade `max_concurrent_sessions = 500` (default) é **fictícia** —
nada é pré-instanciado; o recurso real é a admissão. Decisão: remover o default; o campo
vira *throttle opcional de downstream* (backpressure). Registrado em `TODO.md` com
ressalva de **re-validar a lógica ao retomar** (impactos Arc 19: alocação, Bootstrap,
Monitor).

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
| **schemas** | enum `outcome`; `pool_kind` + `queue_pool_id` no PoolConfig; `max_session_pool` opcional (reserva); `outage_cause` |
| **agent-registry** | CRUD/YAML dos novos campos de pool |
| **routing-engine** | **admissão híbrida** (reserva por pool + shared = total − Σ reservas, INCR-check-rollback); sem recurso → aloca pool de fila (mantém pedido pendente no alvo); no allocated → dispensa agente de fila; escreve `session.queue.position/eta` no ContextStore; sem pool de fila → caminho outage com **segmento sintético** (`agent_type=system`, `outcome=outage`, `outage_cause`) em `conversations.participants` |
| **orchestrator-bridge** | sinal de dispensa ao skill-flow de fila; mapeamento outcome→close_reason auditado |
| **Core** | sessão criada sempre (outage incluso); fechar-sempre no disconnect; metering pula outage |
| **channel-gateway** | render de rejeição por canal; detecção de disconnect → close (caminho atendido já existe) |
| **analytics-api** | `/reports/pools/queue` reescrito sobre segments (pool de fila); KPI demanda reprimida no Volume; filtro `agent_type != 'system'` nas métricas de agente |
| **platform-ui** | aba Fila/SLA consome o novo shape; badge outage em Analytics/Sessions |

## Fases sugeridas

- **A — Padronização outcome/close_reason** ✅ (2026-06-03) — implementada e validada:
  `flow_complete+resolved` (IA), `agent_hangup+resolved` (humano via Console),
  `customer_disconnect+abandoned` (F5). Decisões de implementação: (1) **bridge é o
  escritor único** da linha de fechamento em `sessions` — o `contact_closed` do gateway
  (agora com `source: channel_gateway`) é só sinal de transporte, descartado pelo parse
  do analytics (eliminava corrida no ReplacingMergeTree); (2) `_close` do webchat adapter
  idempotente (publicava 2×: agent_done + client_disconnect); (3) marcador Redis
  `session:{id}:last_outcome` ({outcome, agent_kind}) escrito no agent_done IA primary,
  no contact_closed humano e no abandono — `_close_contact_layer` deriva
  close_reason+outcome dele + do marcador `session:{id}:closed` (transporte); (4) wire
  de transporte 100% intacto. Pendente desta fase: sessão só-fila (nunca roteada) sem
  `meta` → evento sem tenant → sem fechamento no ClickHouse (resolvido por B/E).
- **B — Admissão híbrida + outage na porta** ✅ (2026-06-03) — validada nos dois cenários:
  `shared_full` (teto 2, 3º contato rejeitado) e `reservation_full` (reserva 1 no sac_ia,
  2º contato rejeitado; bucket com exatamente 1 membro, shared limpo pelo reconciler).
  Sessão `no_resource`+`outage`, segmento sintético `system` com causa apontando o pool.
  Nota operacional: release de admissão é assíncrono (~60s) — rejeições logo após um
  fechamento são possíveis (trade-off do gauge auto-curável). Gap menor: PUT parcial não
  limpa `session_reservation` (Zod não aceita null) — limpar via SQL + republish até
  ajustar o schema. Implementação: `routing-engine/admission.py`
  (buckets SET reserva/shared, migração com fail-open mid-session, reconciler 60s via
  `session:{id}:closed`), `_emit_outage` (contact_closed autoritativo + segmento sintético
  + outbound close + guards anti-reclose), `PoolConfig.session_reservation` (schemas +
  Prisma + CRUD + YAML passthrough), filtro `agent_type != 'system'` no analytics.
  **Pendente da fase**: metering ainda conta sessão outage na dimensão `sessions`
  (compensação na integração metering×pricing); widget webchat fecha sem mensagem de
  rejeição (render v2); invariante Σ reservas ≤ total ainda sem validação na escrita
  (Registry) — hoje só guard `max(0, shared)` no runtime.
- **C — Fila atendida** ✅ (2026-06-03) — validada nos dois desfechos: **handoff**
  (`outcome=escalated_human`, espera 21s = gap exato até o primary; primary humano manteve
  `sequence_index=0`; ContextStore `position=1`/`eta_ms=210000`) e **abandono**
  (`outcome=abandoned`, 5.6s). Dois fixes da validação: (a) bridge no disconnect soma 1 push
  de `session:closed` quando `queue:agent_active:{sid}` existe — o agente de fila roda com
  `instance_id=""` e o `menu.ts` não cria activity key, então a contagem genérica o ignorava
  e o BLPOP ficava eterno (segmento nunca fechava); (b) override de outcome no fechamento do
  segmento de fila: `session:{id}:closed` presente → `abandoned` (abandono é detecção da
  plataforma — o complete do YAML reporta `escalated_human` mesmo saindo via `on_disconnect`,
  e o contrato Fase A proíbe o flow de declarar `abandoned`).
  **Decisão central**: segmento do agente de fila marcado com **`role: queue`** em vez de
  pool separado (`pool_kind`/`queue_pool_id` dispensados) — o `queue_config` existente já
  ativa o agente de fila no pool-alvo, e o segmento com `pool_id` = alvo é exatamente a
  dimensão do relatório da Fase D; queries de agente (primary/specialist) excluem fila sem
  retrofit. Implementação: (1) `@plughub/schemas` contact-segment.ts — `queue` nos enums de
  role (segment + participant event); (2) bridge `process_queued` — `participant_joined`
  (role=queue, `participant_id=queue-{session_id}`, instance_id="") antes do
  `activate_native_agent` e `participant_left` (duration, outcome do flow, flow_id) na
  conclusão; **não** toca `segment_seq`/`primary_segment`/`last_outcome` (só primary dirige
  outcome de sessão); (3) routing `_write_queue_context` — `session.queue.position` (tamanho
  do bucket pós-SADD, 1-based) + `session.queue.eta_ms` (posição × sla_target_ms × 0.7) no
  ContextStore a cada tentativa de enqueue (drain re-attempts refrescam posição); (4) default
  de tenant — Config API namespace `session` keys `queue_default_agent_type_id`/
  `queue_default_skill_id` (seed + `session_config.py`); pool sem `queue_config` cai no
  default; vazio = comportamento original (espera muda). Pendências da fase: timeout de fila
  (`queue_config.max_wait_s` não é enforced em lugar nenhum — cenário `max_wait_exceeded` é
  da Fase E); posição não re-escrita entre drains (só on-enqueue); `close_reason` do segmento
  de fila fica NULL (outcome basta pra Fase D); i18n do role `queue` no detalhe de sessão da UI.
- **D — Relatório de Fila/SLA sobre segments + demanda reprimida no Volume** ✅
  (2026-06-03) — validada com os dados das Fases B/C: `retencao_humano` queued=3,
  handoff=1 (espera média ~110s), abandoned=1 (rate 33%); Volume `rejected.total=2`
  com `by_cause` apontando `sac_ia` × {`shared_full`, `reservation_full`}.
  Implementação (analytics-api `reports_query.py` + platform-ui `AnalisePoolsPage`):
  (1) `/reports/pools/queue` reescrito — por sessão (excluindo `outcome='outage'`),
  LEFT JOIN com agregado de segments: `queued` = tem segmento `role='queue'`;
  **espera = `duration_ms` do segmento de fila** (fila ao vivo, `ended_at` NULL,
  fica fora das stats de espera mas conta em queued); `abandoned` =
  `q_outcome='abandoned'`; novo `handoff` = fila não-abandonada + segmento primary
  real (`agent_type != 'system'`). **`abandon_rate` = abandonados/enfileirados**
  (antes era /contatos). SLA: não-enfileirado espera 0 (dentro por construção);
  pool da sessão nunca roteada vem do `pool_id` do segmento de fila. O interim
  (gap até o primeiro primary, `pools-infra-report.md`) foi removido.
  (2) `/reports/pools/volume` ganhou bloco `rejected`: série bucket×pool×canal das
  sessões outage + `by_cause` (pool × `reservation_full|shared_full|quota`, join
  sessões outage × segmentos system) + `totals.rejected`; `totals.contacts` segue
  sendo a demanda total. (3) UI: card "Demanda reprimida" no Volume (total, % da
  demanda, tabela pool×causa) + KPI no header; coluna "Pós-fila" e hint de
  semântica na aba Fila; i18n en+pt-BR. `queue_events` permanece suplementar
  (max_queue_len/disponíveis na série). Pendências: `sessions.sla_target_ms`
  NULL na origem (aba SLA sem dado — dívida do routing→analytics, ver
  `pools-infra-report.md`); fila ao vivo conta como dentro do SLA até fechar.
- **E — Fechar-sempre / cadeia de fallback** (catch → notify+close → max_wait).

## O que substitui no relatório atual

~~A derivação de espera por `LEFT JOIN segments` implementada em 2026-06-03 no
`/reports/pools/queue` (ver `pools-infra-report.md` § dívida de origem) fica como **interim**
até a Fase D.~~ **Substituído pela Fase D (2026-06-03)** — o relatório agora deriva
espera/abandono/handoff diretamente dos segments `role='queue'`; o interim foi removido.
