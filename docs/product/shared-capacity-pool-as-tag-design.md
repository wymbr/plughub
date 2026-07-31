# Capacidade compartilhada — pool é TAG do recurso *(desenho fechado, 2026-07-31)*

> Fecha as três decisões em aberto da seção homônima do `TODO.md`. Linha de base **medida**
> (2026-07-31, mesma seção): 1 humano `max_concurrent 3`, 3 pools, 1 vaga ocupada → cada snapshot
> diz `available 3`, a tela soma **6**, a verdade é **2**.

---

## 0. Enquadramento — são TRÊS defeitos, não um

A capacidade compartilhada **está certa**: o agente atende contato de qualquer pool em que está
logado, e cada pool legitimamente conta com ele. O que falha é a propagação do consumo, e ela falha
em três lugares independentes:

| # | Defeito | Onde | Correção |
|---|---|---|---|
| **A** | a *fonte* é um contador **por pool** (`active_count`) que ignora o consumo dos irmãos | `get_busy_count` → `busy`/`available` do snapshot | derivar do SET (Decisão 1+2) |
| **B** | o conjunto de *gatilhos* não cobre o pull, e o TTL de 1 h não cura obsolescência | `write_pool_snapshot` tem 1 call site no router | fan-out por recurso (Decisão 2) |
| **C** | a *agregação* soma `available` de pools que compartilham o mesmo recurso | `MonitorTab`, `PoolsPage`, `system_availability_check` | contar recursos distintos (Decisão 3) |

As três são cumulativas na direção errada: corrigir **A** sem **C** deixa o KPI inflado (2+2+2 = 6);
corrigir **A** sem **B** deixa o número certo escrito na hora errada.

### 0.1 Recorte por tipo de agente (medido 2026-07-31)

**Este documento trata do lado HUMANO.** O defeito medido é de capacidade compartilhada entre pools, e
quem compartilha é o humano: a linha de base é 1 humano `max_concurrent 3` em 3 pools, e o espelho
`-int` tornou todo humano multi-pool por construção.

Para IA, o bootstrap cria **N instâncias de 1 vaga** a partir do campo "Concurrent sessions"
(`instance_bootstrap.py:1054-1072`) — logo **instância == sessão**, não há capacidade compartilhada
entre pools (cada instância pertence a um pool), e o licenciamento é assunto da
[ADR de licenciamento](../adr/adr-agent-licensing-and-pool-isolation.md).

> **A tag serve o humano; a licença serve a IA.** Não são duas soluções para o mesmo problema, e por
> isso os dois arcos podem avançar quase sem sobreposição.

### 0.2 Estado medido do ambiente (2026-07-31 21:20)

`infra/test/measure_capacity_licensing_baseline.sh tenant_demo` — guardar a saída: é o "antes".

- **A reproduzido na forma "deriva"**: `formfill_demo` com `available 0 · busy 1 · total_instances 0 ·
  active_count 1 · fila 0`, sem instância nem ocupação em lugar algum. `busy > total_instances` é
  impossível por construção.
- **O escritor principal do snapshot é o BOOTSTRAP**, não o routing-engine: quase toda linha tem a
  assinatura `available == total_instances`, `busy 0`, e snapshots expiram em 60 s (TTL do bootstrap),
  não em 3600 s. Confirma que a "segunda implementação da fórmula" (§2) é hoje a principal — e reforça
  a decisão de fazê-la parar de afirmar capacidade.
- A forma **não-aditiva** de A exige humano logado em ≥2 pools com sessão ativa; a evidência dela
  continua sendo a linha de base das 11:29 registrada no `TODO.md`.

**Prioridade média, e o motivo é o que delimita o escopo:** nada disto gateia alocação — `admission.py`
não lê `available`, o árbitro é o semáforo via `claim_instance`. Alimenta `queue_context_get`,
`pool_status_get` e `system_availability_check`, que os Skill Flows usam para **oferecer troca de canal
e informar tempo de espera ao cliente**. Corrompe afirmação, não alocação. Logo o alvo de qualidade é
*honestidade do número*, não latência de convergência.

---

## 1. Decisão 1 — tag de pool no membro do semáforo

### Formato

```
occupant = {session_id}::{conference_id}::{pool_id}
hold     = __wrapup_hold__::{origin_session_id}::{pool_id}::{expires_at_ms}
```

**Invariante: o pool é sempre o 3º campo `::`.** Vale para os dois tipos de membro, então o parse é
um só. Consequências de preservação, todas verificadas contra o código atual (`registry.py` §147-277):

- **release por prefixo `{session_id}::` intacto** — a tag entra depois, nunca antes (o requisito do
  TODO). `_RELEASE_INSTANCE_LUA` não muda uma linha.
- **prefixo `__wrapup_hold__::` intacto** — não colide com prefixo de sessão (uuid).
- **parse de expiração do hold intacto** — `string.match(m, '::(%d+)$')` continua valendo porque no
  hold a tag entra **antes** do timestamp. Foi por isso que a tag não virou sufixo também no hold:
  sufixo ali quebraria o único parse numérico do Lua de claim. Pôr o pool no campo 3 nos dois casos
  resolve as duas restrições ao mesmo tempo.

### A mudança não-óbvia: idempotência do claim

Hoje o claim testa `SISMEMBER` **exato**. Com a tag, a mesma `(sessão, conferência)` reivindicada por
**outro pool** — transferência cross-pool para um pool onde a **mesma** instância está logada — passaria
no teste de existência com string diferente e criaria um **segundo membro**: dupla ocupação da mesma
sessão no mesmo recurso. Regressão silenciosa e plausível (o número só ficaria 1 acima).

Correção: a checagem de idempotência passa a ser por **prefixo `{session_id}::{conference_id}::`**, e
em *hit* com pool diferente o Lua faz **SREM antigo + SADD novo** — re-tag, contagem inalterada. É
exatamente isso que faz a tag ser **projeção** e não contagem: mudar de pool nunca muda quantas vagas
a sessão ocupa.

### Hold: a tag não precisa de plumbing novo

`swap_to_hold` **herda a tag do occupant que remove**. O pool que serviu é, por construção, o que
estava no membro removido — nenhum parâmetro novo atravessa `remove_conversation`, e a atribuição do
hold a um pool sai de brinde, como previsto no TODO.

### Compatibilidade

Membros de 2 campos (escritos antes do deploy; o SET tem TTL 24 h) são **untagged**. Contam na ocupação
do recurso — que é o que `available` usa — e não contam em nenhuma projeção por pool. O recompute
publica `untagged` no snapshot: *degradação nunca é silenciosa*. O número deve ir a zero em ≤ 24 h;
`untagged` persistente é bug de escritor, não ruído de migração.

### O que morre

`{t}:pool:{p}:active_count` fica **sem leitor** — e com ele o `INCR` do `mark_busy` e o `DECR` +
clamp do `remove_conversation`. É o último agregado incremental deste caminho, e a origem do
`available 4 / total 3` corrigido em 2026-07-30 por teto/chão. Remendo removido, não reforçado.

---

## 2. Decisão 2 — recompute em Lua, e um conjunto de gatilhos maior

### Fórmula

Por pool `P`, sobre `I = ready_set(P) ∪ busy_set(P)`:

```
total_capacity = Σ  max_concurrent(i)
used_global    = Σ  SCARD(sessions_i)                       ← inclui irmãos E holds
used_here      = Σ  #{ m ∈ sessions_i : tag(m) = P }
available      = max(0, total_capacity − used_global)
busy           = used_here
busy_elsewhere = used_global − used_here
```

`busy_elsewhere` não é enfeite: sem ele a linha fica aritmeticamente inexplicável na tela
(`available < total − busy`, sem motivo visível) e alguém eventualmente "conserta" de volta para o
modelo errado. Com ele, `available = total − busy − busy_elsewhere` fecha na própria linha e a tela
**ensina** o modelo compartilhado em vez de escondê-lo.

Um script, `KEYS` = os dois sets do pool, chaves de instância derivadas dentro. **Não é cluster-safe**
— diferente do claim/release, que são single-key de propósito. Registrado aqui porque a diferença é
deliberada: se Redis Cluster entrar em cena, ou hash-tag por tenant, ou volta a pipeline.

### Gatilhos — a mudança estrutural

O refresh deixa de ser *"o pool roteado"* e passa a ser **fan-out sobre os pools do RECURSO**
(`pools(instance) ∪ {pool_id}`). Sem isso, mesmo com a fórmula certa, o snapshot do pool irmão só é
reescrito quando algo o toca — que é literalmente o defeito relatado.

| Gatilho | Hoje | Depois |
|---|---|---|
| `route()` → `_write_snapshot` (`router.py:215`) | 1 pool | fan-out |
| `mark_busy` | INCR do contador | recompute (fan-out) |
| `remove_conversation` | patch `available += 1` c/ teto e chão | recompute (fan-out) |
| **`work_task_claim`** | **nada** (achado 2) | recompute (fan-out) |
| **`work_task_release`** | **nada** | recompute (fan-out) |
| **`work_task_expire`** | **nada** | recompute (fan-out) |
| `agent.lifecycle` listener | `_refresh_pool_snapshots` | mantém, sobre o recompute |
| pause / resume / logout | `refresh_pool_snapshot` | mantém, sobre o recompute |

### O TTL de 1 h fica

Com recompute nos gatilhos certos, o TTL deixa de ser mecanismo de correção. Era isso que o achado 3
revelava: com 120 s um snapshot obsoleto se auto-curava **por acidente**. Baixá-lo de volta seria
comprar cura por expiração — remendo. Em compensação, `snapshot_age_ms` (já devolvido por
`pool_status_get`, hoje sem nenhum leitor) vira o canal honesto para idade alta.

### O escritor duplicado do bootstrap

`instance_bootstrap.py:590` é uma **segunda implementação** da fórmula, com fonte própria
(`self._registered` + `active_count`), NX e TTL 60 s. Portar o Lua para lá duplica a regra em dois
serviços; deixá-la como está faz pools ociosos publicarem o modelo velho.

Decisão: **ela para de afirmar capacidade.** Escreve `available: null, busy: null, total_instances:
null, model: "bootstrap_placeholder"`, cumprindo seu único propósito real (o pool aparecer no feed SSE)
sem inventar número. Consumidores tratam `null` como *desconhecido* — nunca como `0`, que é justamente
o valor plausível que esconderia o buraco.

---

## 3. Decisão 3 — agregação por recurso DISTINTO

O erro é `Σ available(pool)`. O certo é `Σ` sobre instâncias **distintas** de `max(0, mc − SCARD)`.

Isso **não é derivável das linhas de pool** — a informação de sobreposição não está nelas. Logo exige
uma segunda superfície: rollup por tenant em `{t}:capacity:snapshot`.

**O rollup é POR TIPO DE LICENÇA, nunca um número só.** Disponibilidade de humano e de IA são moedas
distintas e não-fungíveis (ADR D1); somá-las repetiria a falácia de aditividade um nível acima — em vez
de contar o mesmo recurso duas vezes, contaria recursos que **não se substituem**. Por isso não existe
campo `available` no topo:

```jsonc
{
  "by_kind": {
    "human": { "total_capacity": 3, "used": 1, "available": 2, "instances": 1,
               "by_channel": { "whatsapp": { "available": 2, "instances": 1, "pools_available": 2 } } },
    "ai":    { "total_capacity": 360, "used": 0, "available": 360, "instances": 360,
               "by_channel": { "webchat": { "available": 360, "instances": 360, "pools_available": 19 } } }
  },
  "computed_at": "..."
}
```

Uma instância serve o canal `ch` se **algum** pool seu declara `ch`. Recomputado pelos mesmos gatilhos,
com **throttle de ~5 s por tenant** (chave de cooldown, no espírito do `_REAP_COOLDOWN_S`): alimenta
KPI e decisão em escala humana, não gate de milissegundo.

| Consumidor | Hoje | Depois |
|---|---|---|
| `system_availability_check` (`operational.ts:238`) | `total_agents += snap.available` | lê `by_kind[k].by_channel[ch].available` do rollup — e devolve **por tipo**, porque "há agente para este canal" depende de qual tipo serve o canal |
| idem, `pools_available` | conta pools com vaga | **mantém** — é aditivo e significa outra coisa |
| `MonitorTab:387` / `PoolsPage:366` | `reduce((s,p) => s + p.available)` | lê o rollup |
| linha do pool | `available` | **mantém** (correto, não-aditivo) + `busy_elsewhere`; some o total de coluna |
| `AnalisePoolsPage:463` | `b.available += r.available_agents` | **fora de escopo** — ver abaixo |

### 3.1 `AnalisePoolsPage` — não é a mesma grandeza (achado 2026-07-31)

A página é **viva** (`routes.tsx:115`, Sidebar com ABAC `contacts.visualizar`) — não é código morto.
Mas a métrica que ela plota não é o `available` deste arco, e tem **três** defeitos empilhados:

1. **Modelo errado.** `_publish_queue_position` (`main.py:988`) preenche `available_agents` com
   `get_available_count()` = `SCARD(pool:instances)` — **contagem de instâncias prontas**, o modelo
   abandonado quando `max_concurrent > 1` passou a existir. É o mesmo modelo cujo docstring fez
   `get_total_instances_count` ser removida em 2026-07-30 por ensinar algo falso; a irmã sobreviveu,
   com o nome `available`. Colisão de nome: `queue_events.available_agents` ≠ `snapshot.available`.
2. **Valor ambíguo — não enviesado (medido 2026-07-31, hipótese anterior REFUTADA).** A previsão era
   amostra condicionada a ≈0 ("enfileira porque ninguém está livre"). A medição diz o contrário:
   `624` linhas em 2 meses, `142` não-nulas, das quais **126 valem exatamente 1** e `max = 1` — o
   sistema registrou "1 disponível" no instante em que enfileirava. Esse `1` tem três leituras que o
   número **não** distingue: (a) legítima por filtro — agente pronto que não serve o canal; (b)
   legítima por desenho — em pool `dispatch_mode: pull` a fila é o caminho normal, não escassez;
   (c) defeito — `SCARD` conta **pertencimento**, e `get_ready_instances` filtra capacidade e
   `wrap_up_pending` mas o `SCARD` não filtra nada, então instância lotada dentro do ready_set conta
   como disponível. Ambiguidade estrutural é pior que viés: viés se corrige, ambiguidade não.
3. **Ausência disfarçada de zero.** `queue_events` tem **dois** produtores: `parse_conversations_queued`
   (`models.py:256`) escreve `available_agents: None` *hardcoded*; só o `position_updated`
   (`models.py:681`) carrega valor — daí `482` nulos (77%). O `avg()` do ClickHouse ignora nulo em
   silêncio (a linha descreve 23% das linhas apresentando-se como o bucket) e o
   `float(r.get("available_agents") or 0)` converte bucket sem dado em **0**, indistinguível de
   "nenhum agente disponível".
4. **Agregação inválida.** `AnalisePoolsPage:463` ainda soma entre pools (o defeito C).

Não há o que corrigir — só o que **redefinir**, e redefinir não backfilla. **Decisão: eliminar** a
linha `available` do gráfico da sub-aba Fila, a coluna do `q_series` (`reports_query.py:5186`) e o
**produtor** (`main.py:988`). A página e a tabela de fila (o conteúdo real) ficam. Deixar o campo sendo
escrito sem leitor é convidar a que seja replotado depois.

Substituto honesto, se a série for desejada de fato: amostragem **por relógio** da ocupação do rollup
de tenant (§3), não no evento de fila. Feature nova, não conserto — fora deste arco.

---

## 4. O que NÃO muda (e por quê)

- **pool webhook** segue com `max_concurrent_sessions` — throttle do POOL, exceção legítima já
  documentada; a fórmula compartilhada não se aplica.
- **fila** continua fato do pool (ZSET). Compartilhar capacidade não compartilha fila.
- **`current_sessions`** continua espelho de conveniência sincronizado pelo SCARD — **não** vira fonte.
  Achado 1: está certo hoje, mas é da mesma família do contador por pool; adotá-lo só mudaria qual
  número vai mentir depois.
- **`claim_instance` / admissão** inalterados. O árbitro da alocação continua sendo o semáforo — este
  arco mexe em quem *relata*, não em quem *decide*.

---

## 5. Alternativas descartadas

| Alternativa | Por que não |
|---|---|
| reservar vagas por pool | aritmética limpa, mas fragmenta a capacidade do recurso — contraria o invariante do `CLAUDE.md` e destrói a flexibilidade que o compartilhamento existe para dar |
| remover `available` da linha do pool | conceitualmente o mais honesto, mais disruptivo para quem já lê a tela. `busy_elsewhere` é a tentativa de tornar a linha auto-explicativa **sem** removê-la; se a tabela seguir sendo lida como aditiva, reconsiderar |
| adotar `current_sessions` como fonte | troca qual contador mente (achado 1) |
| baixar o TTL de volta a 120 s | compra cura por expiração — o acidente que mascarava o achado 2 |
| recompute no READ (sem snapshot) | eliminaria a classe inteira de obsolescência, mas move o custo para o caminho de leitura (SSE + toda chamada de tool) e joga fora um cache que já existe. **Plano B** se o fan-out se mostrar caro |
| laço de N round-trips por recompute | a objeção original ao recompute; resolvida pelo Lua (uma ida e volta) |
| cache novo com TTL | o cache já é o snapshot; um segundo seria redundante |

---

## 6. Fases

Cada fase tem uma verificação que **precisa poder ficar vermelha** — e, onde a linha de base já foi
medida, o teste deve ser escrito primeiro contra o código atual e **visto falhando**.

**F1 — tag no semáforo.** Formato do membro + `_CLAIM_INSTANCE_LUA` (idempotência por prefixo,
re-tag no cross-pool) + herança da tag no `swap_to_hold`. Snapshot ainda não muda.
*Verificação:* `test_instance_semaphore.py` estendido — re-tag não altera `SCARD`; release por prefixo
segue removendo membro com tag; hold expira com o parse intacto; membro legado de 2 campos conta como
untagged. Vermelho hoje: o teste de re-tag produz `SCARD 2`.

**F2 — recompute + fan-out.** Lua de recompute, `busy_elsewhere`/`untagged` no snapshot, fan-out por
`pools(instance)`; remove o patch `available += 1` e o INCR/DECR.
*Verificação:* reproduzir a linha de base medida — 1 humano `max_concurrent 3`, 3 pools, 1 vaga ocupada
→ `available 2/2/2`. Hoje dá `3/3/3`, então o teste nasce vermelho.

**F3 — gatilhos de pull + bootstrap.** `work_task_claim` / `_release` / `_expire` recomputam; bootstrap
vira placeholder com `null`.
*Verificação:* o cenário exato do achado 2 — claim posterior ao último snapshot muda os três pools; e
`formfill_demo` deixa de anunciar `queue_length 1` de fila vazia. Asserção sobre o **conteúdo** do
snapshot e sobre `updated_at`, não sobre a existência da chave.

**F4 — rollup por tenant (por tipo) + consumidores.** `{t}:capacity:snapshot` com `by_kind`, throttle,
e a troca em `system_availability_check`, `MonitorTab`, `PoolsPage`.
*Verificação:* 3 pools / 1 humano de 3 vagas com 1 ocupada → KPI **2**, e `system_availability_check`
devolve `total_available_agents 2` por canal. Hoje: 6. Segunda asserção: **não existe** um campo de
disponibilidade que some humano e IA — se alguém conseguir ler um número único, o rollup regrediu.

**F5 — limpeza e docs.** Remover `_pool_active_count_key` e todo o INCR/DECR/clamp; aplicar a remoção
do §3.1 (gráfico + `q_series` + produtor). **`get_available_count` fica sem chamador** ao remover
`main.py:988` (verificado: é o único) — deletar, pelo mesmo motivo que deletou
`get_total_instances_count` em 2026-07-30: é o modelo de contagem de instâncias, e sobreviver com o
nome `available` é o que o mantém em circulação. Atualizar `CLAUDE.md` § Operational Visibility
(campos novos do snapshot; o TTL já foi corrigido) e `docs/guias/conference-mechanics.md` se o formato
do membro do semáforo estiver descrito lá.

**F5b — o mesmo modelo errado no caminho que fala com o cliente.** `pool_status_get`
(`operational.ts:154-167`) tem um *live fallback* para quando não há snapshot: devolve
`available = SCARD(pool:instances)`. É a mesma contagem de pertencimento, num tool que o Skill Flow usa
para decidir oferta de canal e tempo de espera — o pior lugar para ela sobreviver. Fallback deve
devolver `available: null` + `live_fallback: true` (desconhecido, não zero e não SCARD), ou derivar do
semáforo. Sem snapshot, "não sei" é a resposta correta.

---

## 7. Riscos registrados

1. **Cluster-safety assimétrica** — claim/release seguem single-key; o recompute não é. Deliberado e
   documentado no §2.
2. **Custo do fan-out** — cresce com pools por recurso, não com contatos. Se doer, plano B é o
   recompute no read (§5).
3. **`untagged` persistente** — se não convergir a zero em 24 h, há escritor de membro fora do
   `claim_instance`. O campo existe exatamente para denunciar isso.
4. ~~**Série histórica de pools** segue sem total honesto~~ — **reenquadrado e resolvido por remoção**
   (§3.1): a série não media a mesma grandeza (contagem de instâncias, amostrada só no enfileiramento).
   Sai do gráfico, da query e do produtor. O risco residual é o de sempre ao remover: se alguém lia
   aquela linha como sinal, passa a não ter nenhum — e essa é a informação correta, já que o sinal
   antigo não sustentava a leitura.
