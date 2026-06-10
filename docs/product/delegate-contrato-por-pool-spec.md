# Delegate — Contrato (a)→(b) por Pool · Especificação

> **Contexto:** *Business in Any Media*, modelo de 3 níveis. Define como o nível (a) — fluxo negocial, channel-abstract — delega uma interação ao nível (b)/(c). Companion da `identity-resolver-nivel-b-spec.md` (o gate de identificação vive aqui).
> **Princípio central:** **delegação é por POOL, nunca por skill/flow.** O pool tem uma skill publicada por **ação de deploy**; (a) nomeia o pool, o Routing Engine aloca uma instância, e a instância roda a skill atualmente publicada.
> **Status:** especificação. Fiel ao código (`packages/schemas/src/skill.ts`) onde marcado.
> **Data:** Junho 2026.

---

## 1. Por que por pool (e não por skill/flow)

Mirar skill/flow direto acopla (a) a uma versão de fluxo e fura três pilares:

- **Routing Engine é o único árbitro** — toda alocação passa por pool. Delegar a uma skill direto contorna o roteador.
- **Provisionamento é deploy-driven** — desde a remoção do `AgentType` (Fase C) e a migração "Slots por pool.deploy" (Fase 3b–3d), uma instância existe porque um **deploy** publicou uma skill num **pool**. O par operacional é `(pool, skill publicada)`, não a skill solta.
- **Hot deploy / rollback / homologação** — porque (a) nomeia o pool, troca-se a skill publicada (deploy/rollback) **sem tocar em (a)** e sem interromper os contatos em andamento (graceful drain). Se (a) referenciasse a skill, todo deploy quebraria o contrato.

No modelo de 3 níveis isso é exatamente a separação: **(a) declara intenção = "delego a este pool"; a skill publicada no pool é o nível (b)/(c)** — infraestrutura trocável.

## 2. Pool ↔ skill via deploy (binding)

```
PUT  /v1/skills/:id            → deploy_status = draft        (salva, não publica)
POST /v1/skills/:id/deploy     → deploy_status = published     (publica em pools-alvo)
                               → grava skill_deployments + publishRegistryChanged (hot-reload)
rollback                       → restaura yaml_snapshot anterior + re-deploy nos mesmos pools
```

**Invariante confirmado:** **cada pool tem exatamente UMA skill publicada num dado momento** — a "ação de deploy" associa a skill ao pool. As instâncias do pool rodam essa skill; o reconcile é deploy-only; (a) é indiferente à versão. Logo `delegate.pool` resolve, sem ambiguidade, para a skill atual do pool.

## 3. Estado atual dos targets (fiel a `skill.ts`)

| Step | Target hoje | Comentário |
|---|---|---|
| `delegate` | **`pool: string`** | ✅ Já por pool. "I/O agent allocated via routing engine". Só no perfil `workflow`. Tem `context`, `timeout_hours`, `on_resume`/`on_reject`/`on_timeout`, e gera `workflow_resume_token`. |
| `escalate` | **`{ pool }`** | ✅ Por pool. |
| `collect`  | `target`(quem) + `channel`/`requires` | Seleciona canal/pool de saída; outbound. |
| `task`     | `{ skill_id }` → **alinhar a `{ pool }`** (decidido) | Era o único por skill; passa a ser por pool como `delegate`/`escalate`. Ver §7. |

**O `delegate` já é o primitivo correto do contrato (a)→(b)** para interação que suspende e retoma. A spec de identidade deve referenciá-lo (não `collect`/`intent` solto, como estava na §6 — corrigido).

## 4. O `delegate` estendido — campos de identidade e gate

Adições **opcionais** ao step `delegate` (e, por simetria, ao `collect`, que também aguarda o cliente):

```yaml
- id: confirmar_checkout
  type: delegate
  pool: loja_checkout_io          # ← ALVO É O POOL (skill publicada nele = agente de I/O nível c)
  customer_resumable: true        # registra pendência em pending_by_customer (resolver §)
  resume_policy: offer            # offer | auto   (default offer)
  # gate de identificação NÃO é campo aqui — é wirado no fluxo ANTES deste delegate
  # (coleta + identity_verify + choice); ver intake-flow §3.
  context:                        # escrito no ContextStore da sessão-filho
    itens:   "$.pipeline_state.cart.items"
    total:   "$.pipeline_state.cart.total"
  timeout_hours: 48
  on_resume: revisar_pedido
  on_reject: encerrar
  on_timeout: lembrete_carrinho
```

- **`pool`** é o único endereçamento — nada de `skill_id`/`flow`. A skill publicada em `loja_checkout_io` é o agente de I/O (nível c) que conduz o checkout no canal.
- **`customer_resumable`** / **`resume_policy`** — semântica do resolvedor de identidade (pendência keyed por `customer_id`).
- **Gate de identificação** — **não é campo do `delegate`**, é **lógica do fluxo**: o fluxo de entrada coleta a âncora, repassa à retaguarda via MCP (`identity_verify` — a plataforma não decide) e, conforme o resultado, chama o `delegate` com `customer_resumable: true` ou sem (degradado, só intra-canal). Primitiva de plataforma = `customer_resumable`/`resume_policy` + as tools; o *quando/como* gatear é do fluxo (ver `intake-flow-nivel-c-spec.md` §3).
- (a) permanece **channel-abstract**: não há campo de canal; (b)/(c) negociam mídia (Arc 16) e o pool define a skill.

## 5. Quem é a skill publicada no pool de delegação

No 3-níveis, o pool de destino do `delegate` é um pool de **interação** (nível c): sua skill publicada é um agente perfil-`agent` (pode `menu/notify/begin/end_transaction`) que (i) executa o gate de identificação quando pedido, (ii) conduz a interação (cards/checkout) e (iii) chama `workflow_resume` ao concluir. Trocar a versão desse agente = um deploy no pool, sem tocar no fluxo negocial (a).

## 6. Capability NÃO entra no delegate — é conceito de fila/roteamento (decidido)

`delegate.pool` permanece **literal**. Cogitou-se uma variante por "capability" (a declara "preciso de checkout interativo" e o routing escolhe o pool), mas a análise mostra que **isso é assunto de fila, não de delegação**:

- **O pool já É a fila** — unidade de roteamento, SLA, capacidade e billing. Quando não há agente livre, o contato entra na fila *daquele* pool.
- Capability → **1 pool** = apenas um apelido (não muda nada).
- Capability → **N pools** = **roteamento entre filas / transbordo** (fila do pool A estourou → pool B com mesma capability). Isso é skill-based routing com overflow — responsabilidade do **Routing Engine**, não do step `delegate`.

**Conclusão:** se um dia houver transbordo por capability, ele entra no Routing Engine (mapa `capability → pools` + política de overflow); (a) continua nomeando um alvo lógico e o roteador faz o leque. O contrato `delegate` fica simples: **um pool**.

### 6.1 Dois eixos de fila — ambos ortogonais ao `delegate`

Não confundir capability/overflow com o **agente de fila** (Queue-Attended-Model, já implementado):

| Eixo | O que é | Onde mora | Status |
|---|---|---|---|
| **Atendida vs. muda** | Como a espera de **um** pool se comporta. `queue_config` no pool liga um **agente de fila** (skill-flow de IA, segmento `role: queue` no próprio pool-alvo) que informa posição/ETA (`session.queue.*`), oferece outro canal com menor espera, etc. Sem `queue_config` → espera muda (zero LLM ou silêncio). Agente de fila **consome licença** (capacity-governance). | Propriedade do **pool** | ✅ existe |
| **Overflow por capability** | Escolher/transbordar **entre** pools com a mesma capacidade. | **Routing Engine** (hipotético, §6) | ✗ não existe |

Os dois são assunto de pool/roteamento — **nenhum entra no `delegate`**. (a) delega ao pool; se não há I/O agent livre, o agente de fila daquele pool entretém o cliente; (a) não sabe de nada disso.

> **Sinergia "business in any media":** o agente de fila oferecendo **outro canal com espera menor** é uma manobra cross-canal — se aceita, pendura no mesmo `session_id` via suspend/resume + resolvedor de identidade, preservando contexto. Ponto natural de troca de canal.

## 7. A inconsistência do `task` (a resolver)

`task.target = { skill_id }` é o único que mira skill direto. Para o modelo de 3 níveis e deploy-driven, isso deveria ser **por pool** como os demais. Caminhos:

- **(A) Alinhar `task` a pool** — `task.target = { pool }`, consistente com `delegate`/`escalate`; a skill publicada no pool é o especialista (assist/transfer). Recomendado.
- **(B) Documentar exceção** — se `task` por `skill_id` é intencional para invocar uma sub-skill *in-process* sem alocação de pool (orquestrador chamando sub-rotina), então não é delegação ao nível (b) e deve ser renomeado/segregado para não se confundir com `delegate`.

**Decidido: (A)** — `task.target` passa a ser `{ pool }`, consistente com `delegate`/`escalate`. A skill publicada no pool é o especialista (assist/transfer). Mudança de schema em `TaskTargetSchema` (`{ skill_id }` → `{ pool }`); migração com aceite de ambos durante a transição. Chamada in-process pura, se necessária no futuro, fica em mecanismo próprio, fora de "delegação a agente".

## 8. Relação com o resolvedor de identidade

- `customer_resumable: true` no `delegate` → (b) grava `PendingEntry` em `{t}:pending_by_customer:{customer_id}` (resolvido/provisionado no ato).
- `requires_identity` → gate que garante âncora durável (necessária para retomada cross-canal).
- `workflow_resume(resume_token, decision)` (inalterado) é o que destrava (a) — chamado pela skill publicada no pool de I/O.

→ Ver `identity-resolver-nivel-b-spec.md` §5/§6/§13.

## 9. Decisões (fechadas)

1. ~~`delegate` por capability~~ **Resolvido:** `delegate.pool` literal. Capability é conceito de fila/roteamento (transbordo), não de delegação — fica no Routing Engine se um dia existir (§6).
2. ~~`task` → pool~~ **Resolvido:** alinhar `task.target` a `{ pool }` (opção A, §7). Toca `TaskTargetSchema`.
3. ~~Um pool, uma skill publicada~~ **Resolvido (confirmado):** cada pool tem exatamente 1 skill publicada por vez (§2). `delegate.pool` resolve sem ambiguidade para a skill atual.
