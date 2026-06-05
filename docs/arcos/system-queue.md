# Fila de Sistema — tier gratuito

> Estado: **esboço em discussão** (2026-06-05). Origem: decisão comercial da
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
3. **`dequeued`/`abandoned` na fila muda**: routing emite `queue.dequeued` no
   drain bem-sucedido e `queue.abandoned` no max_wait/disconnect → analytics
   deriva espera real `queued→dequeued/abandoned` (a derivação interim removida
   na Fase D volta, agora com evento correto, só para filas mudas).
4. **Relatório**: `/reports/pools/queue` dual-source — segments (atendida) ∪
   queue_events (muda); por pool, marcar o tier da fila (atendida/sistema/sem
   fila) para o operador saber o que está comparando.
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

## Pendente (implementação)

**Fase A (routing)**:
1. Isenção de C no enqueue mudo (condicionada à ausência de `queue_config`):
   SREM dos buckets de admissão + kind set + member keys; SADD em
   `{t}:queue:unadmitted`; re-admissão natural no drain.
2. Overflow: admissão rejeitada (shared_full/quota/reservation_full) em pool
   humano → enqueue mudo em vez de outage, enquanto SCARD(unadmitted) <
   `max_queue_total`; estouro → outage `queue_full`. Canal com
   `max_wait_by_channel = 0` (ex. voice) não entra em fila muda → outage direto.
3. Config: `max_queue_total` + `queue_max_wait_by_channel` (Config API, com
   defaults hard-coded — nunca ilimitado com Config fora).
4. Eventos `queue.dequeued` (drain ok) e `queue.abandoned` (max_wait/disconnect).
5. Reconciler: limpar `{t}:queue:unadmitted` de sessões fechadas; TTL backstop
   nas chaves de espera.
6. Drain com orçamento (re-publica ≤ headroom por ciclo).

**Fase B (analytics + UI)**:
6. Consumer dos novos eventos → `queue_events`; dual-source no
   `/reports/pools/queue` (segments=atendida ∪ events=muda) + tier da fila por
   pool.
7. platform-ui: label da causa `queue_full` na demanda reprimida (i18n);
   relatório de fila com tier.
8. Item 7 do capacity-governance destravado (admissível considera a isenção).
