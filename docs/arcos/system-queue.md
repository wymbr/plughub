# Fila de Sistema — tier gratuito

> Estado: **implementado** (Fases A+B, 2026-06-05). Origem: decisão comercial da
> tipagem de pool (capacity-governance § Tipagem) — fila inteligente é agente IA
> licenciado; o tier gratuito é a fila de sistema (sem agente, sem interação).
> Pré-requisito do item 7 do capacity-governance (UX do available nos Monitores):
> a contabilização daqui muda a fórmula do admissível.

---

## Motivação

- **Comercial**: quem não quer pagar fila inteligente não pode ficar sem
  downgrade — hoje pool humano saturado sem `queue_config` = espera muda e, na
  porta da admissão, rejeição (demanda reprimida). Fila de sistema gratuita é o
  baseline de mercado; a atendida vira upsell claro.
- **Modelo**: `queue_config: null` → fila de sistema (grátis, **não debita C**);
  `queue_config: {skill...}` → fila atendida (agente IA licenciado, debita C_ai).
  A regra `queue_config ⇒ agent_kind: human` vale para os dois níveis.

---

## O que JÁ existe (recon 2026-06-05 — o arco é bem menor que o esboço do TODO)

A "fila muda" está majoritariamente viva no routing:

- **Ledger**: `_persist_queued_contact` → ZSET `{t}:pool:{pool}:queue` com o
  evento íntegro (re-publicável), posição/ETA no ContextStore.
- **Aviso ao cliente**: mensagem de espera no primeiro enqueue — *mantida
  explicitamente para filas mudas* no render v2 (suprimida só na atendida).
- **Drain-on-agent-ready**: `_drain_queue_for_agent` re-publica contatos
  enfileirados quando agente fica disponível (re-roteamento natural).
- **Teto de espera**: `queue_max_wait_default_s` (1800s) bounds filas mudas →
  `close_reason=max_wait_exceeded`; graceful drop sem pool.
- **Analytics**: `conversations.queued` → `queue_events` (evento `queued`) já é
  emitido para TODA fila (muda incluída); `queue.position_updated` idem.

**O que falta de verdade**: (a) contabilização — sessão em fila muda hoje
**debita C** (admissão na porta, antes do routing); (b) evento `dequeued`
(dívida conhecida) para derivar espera real da fila muda; (c) dual-source no
`/reports/pools/queue` (atendida = segments `role='queue'`; muda = events);
(d) teto de tamanho da fila muda (`max_queue_length`).

---

## Esclarecimento estrutural (2026-06-05)

O **enqueue/ledger (ZSET) é infraestrutura universal** — roda para todo contato
não alocado, com ou sem `queue_config`, e é ele que alimenta o drain-on-ready
nos dois tiers. "Fila de sistema" × "fila atendida" é o **tratamento em cima**
do ledger: sem `queue_config` = só aviso de espera (grátis, isenta de C); com
`queue_config` = agente de fila ativado pelo bridge (IA licenciada, debita C).
Logo: a isenção de C e o `max_queue_length` são **condicionados à ausência de
`queue_config`** (mesmo branch que já decide suprimir o aviso de espera), não
ao enqueue em si. A atendida tem teto natural (slots do pool de fila + admissão).

## Decisões (fechadas com o usuário — 2026-06-05)

1. **Isenção de C: libera no enqueue ✅** — ao persistir na fila muda, SREM dos
   buckets de admissão (+kind set+member keys); re-admissão natural no drain
   (re-publica no inbound → admissão roda de novo). C cheio no drain →
   re-enfileira, nunca fura o teto. Zero estado novo. Fila atendida segue
   debitando (é IA licenciada).
2. **Teto da fila muda: TOTAL do tenant + causa `queue_full` ✅ (revisada
   2026-06-05)** — o buffer grátis de espera é recurso da instalação (quem
   esgota é o C total), então o teto é um **contador total**: `max_queue_total`
   global no Config API (sem `max_queue_length` por pool). Mecânica: SET
   `{t}:queue:unadmitted` (SADD no enqueue mudo; SREM no drain/abandono/
   max_wait; SCARD = ocupação; auto-curável via reconciler — mesmo padrão da
   admissão; o critério "não admitido" é a própria definição da isenção e
   separa entradas mudas das atendidas no mesmo ZSET). Estouro → outage causa
   NOVA `queue_full`. Trade-off registrado: vizinho barulhento (um pool pode
   lotar a sala de espera) — refinamento futuro se doer: reservas de fila por
   pool, mesmo padrão da admissão.
2b. **Overflow ✅ (2026-06-05)**: C esgotado em pool humano NÃO rejeita na
   porta — cai na **fila muda como overflow gratuito** (isento por construção:
   nunca foi admitido) até `max_queue_total`; o drain re-publica → re-admite
   quando C liberar → tratamento (atendido ou direto) começa. Rejeição só com
   fila cheia (`queue_full`). Padrão de mercado: rejeita quando a FILA lota,
   não quando os atendentes lotam; demanda reprimida vira espera maior
   (visível no SLA) em vez de contato perdido.
3. **~~Eventos `dequeued`/`abandoned`~~ → SEGMENTOS SINTÉTICOS ✅ (superada na
   implementação, 2026-06-05)**: descoberto no recon que o `_emit_queue_timeout`
   JÁ emitia segmento sintético `role=queue` para fila muda no max_wait. A
   Fase A generaliza: toda saída de fila muda emite o segmento sintético
   (`handoff` na transição unadmitted→admitida; `abandoned` na desistência
   detectada pelos drains) com a espera real (`first_queued` preservado através
   de re-enfileiramentos). **Zero tópicos Kafka novos e zero dual-source**: o
   `/reports/pools/queue` (Fase D, segments) passa a contar a fila muda sem
   nenhuma mudança no analytics.
4. **Relatório**: dual-source DESNECESSÁRIO (decisão 3); resta só marcar o
   tier da fila por pool (atendida/sistema/sem fila) — Fase B.
5. **Feedback ao cliente**: aviso inicial já existe (webchat); updates
   periódicos de posição via `deliver_text` = v2 opcional (não bloqueia o arco).

---

## Proteções operacionais (2026-06-05 — a fila grátis não pode derrubar o ambiente)

Isenção de C remove o limite comercial → os limites técnicos são explícitos:

1. **`max_queue_total` é hard limit com default embutido no código** — Config
   API fora/ausente ⇒ vale o default (ex. 100), NUNCA ilimitado.
2. **Teto de espera por canal** (`queue_max_wait_by_channel`, Config API +
   defaults no código): voz curta (ex. 300s — espera muda em voz é dead air
   segurando trunk; `0` = canal não aceita fila muda, vai direto a outage —
   provável correto p/ voice), webchat médio (1800s), whatsapp longo (assíncrono).
   Estouro → `queue.abandoned` + encerramento gracioso (max_wait_exceeded).
3. **Drain com orçamento**: re-publicar por ciclo no máximo o headroom
   disponível (não a fila inteira) — evita tempestade de re-admissão/
   re-enfileiramento (churn Kafka/routing proporcional à fila a cada ciclo).
4. **Backstops de memória**: entradas do ZSET (evento íntegro) bounded pelo
   teto total; TTL nas chaves de espera; reconciler limpa `{t}:queue:unadmitted`
   e ZSETs de sessões fechadas.

## Pendente → Fase A ✅ (2026-06-05, routing)

1. ✅ Isenção de C no enqueue mudo (`_persist_queued_contact`): condicionada à
   ausência de `queue_config` (ou `force_mute` no overflow) — `admission.release`
   (SREM buckets + kind + member keys) + SADD `{t}:queue:unadmitted` +
   `first_queued` NX (score do ZSET = primeiro enqueue: re-enfileiramentos não
   resetam posição nem relógio). Re-admissão natural no drain.
2. ✅ Overflow (`_try_overflow_enqueue`): admissão rejeitada em pool
   `agent_kind=human` com canal aceitando fila muda e buffer com vaga →
   enqueue mudo (sempre mudo — tratamento atendido só após re-admissão);
   buffer cheio → outage causa `queue_full` com `msg_queue_full`; canal 0 ou
   pool IA → outage com a causa original.
3. ✅ Config: `queue_max_total` (100) + `queue_max_wait_by_channel`
   (voice/webrtc 300, webchat 1800, whatsapp 14400; 0 = sem fila muda) +
   `msg_queue_full` — namespace `routing` (Config API seed + defaults
   hard-coded no `routing_config`, nunca ilimitado com Config fora). Sweep de
   timeout channel-aware p/ filas mudas (atendida mantém max_wait_s do pool);
   canal 0 no enqueue mudo → close gracioso imediato (nunca dead air).
4. ✅ Segmentos sintéticos (`mute_queue.resolve_queue_exit`): `handoff` na
   transição unadmitted→admitida; `abandoned` nos drains (desistência) e
   limpeza-sem-segmento no caminho de max_wait (que já emite o dele).
   ⚠️ **Renomeada em 2026-08-21** (era `resolve_mute_exit`): passou a registrar a
   espera nos **DOIS** tiers — atendido e mudo —, porque já era chamada em todas
   as saídas de fila e só se recusava a agir fora do mudo. O portão deixou de ser
   a pertença ao SET `unadmitted` e passou a ser o carimbo `first_queued_ms`, que
   existe nos dois. Sem carimbo não se emite nada — ausência honesta, nunca
   `duration_ms` fabricado a partir de `now`. Ver `conference-mechanics.md`
   § Mudança 37 (inclui o defeito `if raw is None` × `""` que veio junto e o
   gate `test_queue_wait_segment.py`).
5. ✅ Reconciler da admissão limpa `{t}:queue:unadmitted` de sessões fechadas
   (backstop; TTL 7d nas chaves `first_queued`).
6. ✅ Drain com orçamento — **corrigido na validação** (2026-06-05): a checagem
   estrutural (1/pool/ciclo + capacidade de instância) NÃO bastava para
   entradas não-admitidas — agente pronto + contrato cheio gerava churn
   rejeita→re-enfileira a cada 5s com aviso repetido ao cliente. Fix: ambos os
   drains (periódico + agent-ready) só re-publicam sessão `unadmitted` com
   **headroom de admissão** (`AdmissionController.has_headroom`, read-only,
   espelha o admit; fail-open); e o aviso de espera é deduplicado pela chave
   `first_queued` (re-enfileiramento não re-avisa).
7. ✅ Release imediato da admissão no `contact_closed` — **corrigido na
   validação** (2026-06-05): a liberação era só do reconciler (~60s), e o
   drain da fila muda depende do headroom → handoff pós-fechamento esperava
   até 60s por vaga já livre. Event-driven agora (`SessionClosedEventHandler`
   → `admission.release`); reconciler vira backstop. Latência do handoff:
   ≤ ciclo do drain periódico (5s).

## Nota de comportamento (validação 2026-06-05 — esperado, registrado)

A vaga contratada (C) só libera no `contact_closed` real — que dispara APÓS os
hooks de pós-atendimento (NPS/wrap-up). Cliente respondendo NPS = sessão ativa
atendida por IA licenciada ⇒ **debita C até o fim** (coerente com o modelo;
F5/disconnect encurta porque derruba o cliente e o NPS não segura). Já a
capacidade do AGENTE humano libera no `agent_done` (Arc 14: wrap-up em hook
agents não prende o humano) — por isso o Console pode mostrar a sessão antiga
em wrap-up + uma nova. Trade-off com C apertado: NPS lento segura vaga e
atrasa a fila — alavancas do operador: timeout dos hooks e dimensionamento de
C. Isentar pós-atendimento furaria o modelo (IA atendendo sem debitar).

## Fase B ✅ (2026-06-05 — analytics intocado, só UI)

7. ✅ Causa `queue_full` na demanda reprimida ("Fila de espera cheia",
   en + pt-BR).
8. ✅ Tier da fila por pool na aba Fila (Analytics→Pools): badge
   Atendida (IA) / Sistema (grátis) / — , derivado da config do registry
   (`queue_config` ⇒ atendida; humano sem ⇒ sistema; IA ⇒ sem fila) — zero
   mudança no analytics.
9. **Arco concluído.** Item 7 do capacity-governance destravado (admissível
   considera a isenção do unadmitted).
