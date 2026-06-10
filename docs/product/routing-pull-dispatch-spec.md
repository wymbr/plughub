# Routing Engine — Dispatch Pull (genérico) · Especificação

> **Conceito:** um **modo de despacho de pool** — `dispatch_mode: push | pull` — **genérico para qualquer tipo de contato** (voz, chat, e-mail, webhook/workflow). Em `pull`, o Routing Engine **entrega a task à fila do pool e não auto-aloca**; o agente **lista** e **pede a retirada (claim)** ao Routing Engine, que concede atomicamente. Push (auto-alocação) permanece o default e inalterado.
> **Princípio:** o Routing Engine continua o **único árbitro** — a interface só pede; o claim/alocação acontecem dentro dele.
> **Status:** especificação. Fiel a `packages/routing-engine/` (router, registry). **Data:** Junho 2026.
> **Especializações:** fila de aprovação no Console (`human-work-queue-aprovacao-spec.md` §3) é um **caso particular** deste mecanismo.

---

## 1. Por que genérico

Pull não é feature de aprovação — é um **modo de fila** clássico ("agente puxa o próximo / escolhe da fila") aplicável a **qualquer contato**:

- **E-mail** — caso de exercício natural (assíncrono; o agente escolhe da caixa).
- **Webhook/workflow** (aprovação, revisão) — a task é uma sessão de workflow suspensa.
- **Back-office / casos complexos / especialistas** — o humano auto-seleciona.

**Guidance (não restrição):** pull encaixa melhor em **assíncrono**. Em canal **síncrono ao vivo** (voz), o cliente ficaria na linha esperando alguém "pegar" — ali normalmente se mantém **push** (ou push com agente de fila entretendo). Pull fica disponível para todos, recomendado para async.

Item "pulled" = **a sessão/queued-contact que já existe** (o `session_id` no sorted set). **Não há entidade nova**; a única diferença é o `dispatch_mode`. Pools push e pull **coexistem normalmente**.

---

## 2. Reuso do que já existe (fiel ao código)

- **Fila** = sorted set `{tenant}:pool:{pool_id}:queue` (score = `queued_at_ms`); `add_queued_contact`/`remove_queued_contact`/`get_queued_contacts`/`get_oldest_queue_wait_ms`. Pacote do contato em `{tenant}:queue_contact:{session_id}`.
- **Alocação** = `mark_busy` (`SADD` atômico) + contadores busy/available + abertura de segmento.
- **Snapshot** por pool (`write_pool_snapshot`) + `queue.position_updated`.
- **Reaper** — já há precedente (`crash_detector.py`) para varrer/recuperar estado preso.

Pull **acrescenta**: a flag `dispatch_mode`, a operação de claim atômico, a lease do claim e as operações de listagem/devolução. Não reescreve a fila.

---

## 3. Dois relógios (não confundir)

| Relógio | O que mede | Mecanismo |
|---|---|---|
| **Espera não-claimada (SLA)** | task parada na fila, ninguém pegou | **Já existe** — `sla_target_ms`, avaliação preguiçosa, `max_wait_exceeded`; mapeia no `timeout_hours`/`on_timeout` do delegate. Estourou → desfecho de timeout/escala. |
| **Claim lease** (novo) | alguém pegou e ficou ocioso/sumiu | **TTL curto** (minutos) renovado por **heartbeat**; ao expirar, a task **volta para a fila**. Magnitude << SLA fim-a-fim (que pode ser horas). |

A lease é **event-driven pela conexão**: o Console mantém WS com heartbeat; desconexão é detectada pelo **crash_detector** (já existente) → re-enqueue do que o agente tinha claimado. A **lease TTL** é o backstop para "conectado mas ocioso" (§10.1).

### 3.1 Ordenação por peso (push e pull)

A ordem da fila **não é só FIFO**: reusa o **peso** que o Routing Engine já computa por fila — uma **expressão configurável por pool** sobre chaves de **perfil de agente** e **perfil de usuário**. Padroniza-se um **namespace de tags no ContextStore** — `session.queue.*` (ex.: `session.queue.priority`, `session.queue.value`, `session.queue.deadline`, `session.queue.vip`) — que **fluxos/agentes setam** e que entram como **chaves adicionais** na expressão de peso. O **score do sorted set** passa a ser o peso computado (com componente de **idade** para evitar starvation). Vale igual para **push** (ordem de dequeue) e **pull** (ordem da listagem). Em pull o `claim` é **livre** (qualquer task visível), mas a lista chega **ordenada pelo peso**.

---

## 4. Operações (no Routing Engine; expostas como operational tools/API)

A interface (Console/inbox) **só pede**; o Routing Engine executa.

### 4.1 `work_queue_list(tenant, pools[], filtros)`
Lê os sorted sets das **filas pull em que o agente está logado** (multi-pool, §6) + o estado de claim, e devolve resumos (do `queue_contact` JSON) por estado: **claimável**, **claimada por mim**, **claimada por outro** — com idade e SLA. Leitura pura (grupo das operational tools, junto de `queue_context_get`/`pool_status_get`).

### 4.2 `work_task_claim(tenant, pool, session_id, instance)` — claim atômico
```
ZREM {tenant}:pool:{pool}:queue {session_id}
  → 1  (ganhei): mark_busy(instance) + abre segmento +
                 grava lease {tenant}:pool:{pool}:claim:{session_id} (TTL) +
                 devolve o pacote da task
  → 0  (já levado): { claimed: false, reason: "already_claimed" }
```
O `ZREM` de um membro específico é o "um único vencedor" sem lock distribuído — atômico e barato. **Preview** (abrir read-only sem claimar) **não** faz `ZREM` — só leitura.

### 4.3 `work_task_release(tenant, pool, session_id)` — devolver
Remove a lease; **re-enfileira pelo enqueue padrão do routing** (`add_queued_contact`) — **não** preserva a posição/`queued_at`; o Routing Engine **reordena pelos seus próprios critérios**. Desfaz `busy`; fecha o segmento sem decisão.

### 4.4 Auto-release (lease expira)
Heartbeat não renovou → lease expira → handler re-enfileira via `add_queued_contact` (mesma rota do release). Evita task presa.

---

## 5. Estruturas Redis

```
{tenant}:pool:{pool}:queue              sorted set (EXISTE) — claimáveis, score=queued_at_ms
{tenant}:queue_contact:{session_id}     JSON do contato (EXISTE)
{tenant}:pool:{pool}:claim:{session_id} NOVO — lease do claim {instance,user,claimed_at}; TTL=claim_lease_s, renovado por heartbeat
```

`claim_lease_s` é parâmetro de Config API (namespace `routing`), curto e independente do SLA fim-a-fim. (Pode-se derivar do SLA, mas recomenda-se dedicado por diferença de magnitude.)

---

## 6. Alterações no fluxo (mínimas)

- **`route()`** — no topo, se `pool.dispatch_mode == "pull"`: vai **direto** para `_build_queued_result` (parqueia no sorted set) e **pula `_allocate`**. Publica `queue.position_updated`/snapshot como hoje.
- **Agent-ready / dequeue** — pools `pull` são **ignorados** no auto-push do head da fila (em pull, só o claim explícito retira).
- **`PoolConfig`** — novo campo `dispatch_mode: "push" | "pull"` (default `push`). Único toque de schema.
- Tudo o mais (scoring, capacity governance, snapshot, SLA não-claimada) **inalterado**.

---

## 7. Invariante e coexistência

- **Routing Engine é o único árbitro:** `ZREM`/`mark_busy`/segmento/lease acontecem **dentro** dele; a Console só solicita `list`/`claim`/`release`.
- **Capacity governance inalterada:** claim consome instância/licença (vira `busy`) → mesmos contadores e gates de admissão (pool pull tem capacidade contratada).
- **Limite de sessões no claim:** o `claim` só é concedido se `instance_has_capacity` (push + pull combinados); agente esgotado **não claima e não é notificado** (§10.5).
- **`dispatch_mode` é por pool** — push e pull convivem; um agente pode estar logado em **N filas pull** (e/ou push) ao mesmo tempo.
- **ABAC por pool/fila:** acesso é por `accessible_pools` — todos os logados num pool têm acesso pleno à fila dele; `work_queue_list` filtra pelos pools acessíveis.
- **Não confundir com `queue_config`** (fila atendida): aquilo é cliente esperando atendimento (push, agente de fila entretém); pull é operador puxando trabalho. Eixos distintos.

---

## 8. Especializações

- **E-mail (genérico):** pool de e-mail com `dispatch_mode: pull`; o item é a sessão do thread; o agente lista a caixa e claima.
- **Aprovação (workflow):** o item é uma **sessão de workflow suspensa** (delegate ao pool pull); por cima vem o **pacote** (form padrão + `decisions`) e a **decisão pelo retorno do delegate**. Ver `human-work-queue-aprovacao-spec.md` — a §3 de lá referencia este mecanismo; o que é específico de aprovação é só o pacote/decisões, não a fila/claim.

---

## 9. Fases

| Fase | Entrega |
|---|---|
| **A — pull core** | `dispatch_mode` no `PoolConfig`; branch em `route()` + agent-ready; claim atômico (`ZREM`) + `mark_busy`/segmento; lease (`claim:{session_id}` TTL) + heartbeat; release/auto-release via `add_queued_contact` (event-driven). |
| **B — operations + inbox** | `work_queue_list`/`work_task_claim`/`work_task_release` (multi-pool); preview soft; integração com a inbox do Console. |
| **C — especialização aprovação** | Pacote (form/decisions) + decisão pelo retorno do delegate (já no doc de aprovação). |

---

## 10. Decisões (fechadas)

1. ~~Trigger do auto-release~~ **Resolvido:** **event-driven pela conexão** — a inbox vive no Console (WS), então reusa o **heartbeat/crash_detector** existente (desconectou → re-enfileira o que tinha claimado), com a **lease TTL como backstop** para "conectado mas ocioso". Sem sweep dedicado.
2. ~~`claim_lease_s`~~ **Resolvido:** **dedicado** (não derivado do SLA), Config API namespace `routing`, default por pool.
3. ~~Ordenação~~ **Resolvido:** reusa o **peso da fila** que o routing já computa (expressão sobre chaves de perfil de agente + usuário) — ver §3.1. O claim é **livre** (qualquer task visível), mas a **lista vem ordenada pelo peso**.
4. ~~Concorrência preview→claim~~ **Resolvido:** preview é **soft** (read-only, sem `ZREM`); o `claim` resolve por `ZREM` (um vencedor); o perdedor recebe "acabou de ser pego" e a task **some da lista** (detalhe na UI — `pull-inbox-console-ui-spec.md`).
5. **Limite de sessões (novo):** o `claim` respeita `max_concurrent_sessions` do agente (**push + pull combinados**) via `instance_has_capacity` — esgotado, o agente **não claima e não recebe o aviso** de contato em fila. A notificação de chegada vai só a agentes logados **com capacidade**.
