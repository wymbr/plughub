# Operational Visibility — snapshot de pool, ocupação, picos e rollup de capacidade

> **Movido do `CLAUDE.md` em 2026-09-05 (DOC-01).** Corpo integral da seção *Operational Visibility
> — Section 3.3c*, que passara de 106 linhas. Documentação de arco > 50 linhas pertence a
> `docs/arcos/` pela regra de *Saúde do `CLAUDE.md`*; lá ficou o resumo com os invariantes e o link.
> **Nada foi resumido nem reescrito na mudança.**
>
> ⚠️ Este arquivo é o único lugar onde o mecanismo vive por extenso — medido em 2026-09-05, nenhum
> outro doc do repositório descreve `record_pool_peak`, `paused_capacity`, `busy_elsewhere` ou
> `untagged`. `docs/product/shared-capacity-pool-as-tag-design.md` traz o DESENHO da capacidade
> compartilhada; [`docs/arcos/capacity-governance.md`](capacity-governance.md) traz a governança do
> contratado. Nenhum dos dois substitui o que está aqui.

---

## Snapshot de pool

Routing Engine writes pool snapshot to Redis: `{tenant_id}:pool:{pool_id}:snapshot`
(**TTL 3600s**) — `{ pool_id, available, busy, busy_elsewhere, untagged, paused_capacity,
total_instances, queue_length, sla_target_ms, channel_types, model, updated_at }`.

**A PAUSA é fato da ARITMÉTICA, não só do roteamento** (2026-08-21). O recompute lê `status` da
instância (`_INACTIVE_STATES` = `paused|logged_out|logout|draining`, **fonte única** — o trecho Lua é
gerado do conjunto Python). Instância inativa contribui **capacidade zero** e mantém a ocupação:
pausar **não** interrompe a sessão em curso, e o que sai de circulação são as vagas **livres** —
`paused_capacity = Σ max(0, max_concurrent − ocupação)` **por instância**. Com `max_concurrent=3` e 1
sessão viva, saem **2**, não 3. **INVARIANTE: a linha FECHA** —
`total_instances = busy + busy_elsewhere + paused_capacity + available` (salvo sobre-alocação, em que
`available` clampa em zero). `paused_capacity` é obrigatório na linha pelo mesmo motivo de
`busy_elsewhere`: sem ele `available < total − busy` fica inexplicável e alguém reverte para o modelo
sem pausa. **A pausa NÃO limpa o `busy_set`** (só o logout limpa) — limpar zeraria o `busy` com sessão
em andamento e deflacionaria o `busy_elsewhere` dos pools irmãos do mesmo recurso. Gate:
`infra/test/gate_pause_capacity.sh`.

**A ocupação é DERIVADA do semáforo do RECURSO, nunca de um contador** (fatia 2 da capacidade
compartilhada, 2026-08-02). Um recompute em Lua (`_RECOMPUTE_POOL_OCCUPANCY_LUA`) sobre
`ready_set ∪ busy_set` do pool:

```
total_capacity = Σ max_concurrent(i)                    available      = max(0, total_capacity − used_global)
used_global    = Σ SCARD({t}:instance:{i}:sessions)     busy           = used_here
used_here      = Σ #{ m : occupant_pool(m) = P }        busy_elsewhere = used_global − used_here
```

`{t}:pool:{p}:active_count` foi **removido** (contava por POOL uma capacidade que é do RECURSO
— 1 humano de 3 vagas em 3 pools dava três linhas `available 3`, soma 6, verdade 2), e com ele o
INCR/DECR e o patch `available += 1` (com o teto/chão que o remendo exigia). `current_sessions`
**não** foi promovido a fonte: é da mesma família, e trocar um contador por outro só muda qual
mente depois. **`busy_elsewhere` é obrigatório na linha** — sem ele `available = total − busy` não
fecha e o modelo compartilhado parece bug. **`untagged` denuncia escritor de ocupante fora do
`claim_instance`**: deve ir a zero em ≤24 h (TTL do SET); persistente é bug, não ruído.

Gatilhos: `route()` (pool roteado) + **fan-out sobre `pools(instance)`** em `mark_busy`,
`remove_conversation`, `release_session_from_pool` e — desde a **F3a** (2026-08-02) —
`work_task_release`/`work_task_expire` (`refresh_snapshots_for_instance`; só reescreve
pool que já tem snapshot — inventar `sla_target_ms`/`channel_types` seria publicar config falsa).
`work_task_claim` entra de carona no `mark_busy`. O bootstrap (`instance_bootstrap._refresh_pool_snapshots`, NX, TTL 60 s) é
uma segunda implementação: publica `model: "bootstrap_placeholder"` com `available`/`total_instances`
derivados do SCARD e **omite `busy`/`busy_elsewhere`/`untagged`** — ausência é honesta, zero não
seria.

**Defeito C — `Σ available(pool)` conta o mesmo recurso uma vez por pool** e **não é corrigível na
linha do pool**: a linha está certa (aquele pool alcança mesmo N vagas); somá-la é que não pode, e a
informação de sobreposição não está lá. Segunda superfície, **F4a ✅ 2026-08-02**: rollup
`{t}:capacity:snapshot` (`compute_tenant_capacity`, throttle 5 s, TTL 1 h) agregando `max(0,
max_concurrent − SCARD)` sobre instâncias **DISTINTAS**, **por TIPO de licença** — humano e IA são
moedas não-fungíveis, então **não existe `available` escalar no topo** (somá-las seria a falácia de
aditividade um nível acima). Tipo vem de `Pool.agent_kind` (autoridade canônica, nunca de
`source`/`agent_type_id`); pool sem `agent_kind` ou instância em pools de tipos DIFERENTES cai no
balde **`unknown`**, publicado como tipo próprio e logado — dobrar em `human` seria escolher a moeda
cara em silêncio. `pools_available` sobrevive como contagem aditiva ("há por onde entrar?"), mas
chaveada por **(tipo, canal)**: contá-la só por canal fazia `human/whatsapp` publicar 19 num tenant
com 2 pools humanos. **`by_channel` é PROJEÇÃO, não partição** — instância que serve 2 canais conta
nos dois, então `Σ by_channel` excede o total do tipo (628 p/ 353 instâncias no demo); não existe
soma válida entre canais. Gatilhos: fan-out (`refresh_snapshots_for_instance`) + flusher (cobre
tenant ocioso). `system_availability_check` devolve `available_by_kind` do rollup; rollup ausente →
`null` + `capacity_unknown`, **nunca** voltando a somar as linhas (a soma é o defeito, não o fallback
dele). **F4b ✅:** `/v1/operational/pools` repassa em `summary.capacity` e `MonitorTab`/`PoolsPage`
mostram um cartão por tipo. **Escopo (`accessible_pools`) exige RECOMPUTE, não recorte** — a dedução
não projeta sobre subconjunto: `compute_tenant_capacity(only_pools=…)` via `GET /v1/capacity?pools=`
(porta 3550), chamado pelo agent-registry com cache 5 s. Recurso logado dentro E fora do domínio
conta INTEIRO (escopo = "quanto os MEUS pools alcançam"); `only_pools=[]` ≠ `None`.
**F4c ✅:** na série `pool_occupancy_peaks`, `__total__.provisioned_capacity` passou à capacidade
deduplicada e entraram linhas `__capacity_{kind}__` (a linha do pool **não** mudou — está certa e é
não-aditiva). Janela de arranque (1–2 min pós-restart, sem rollup) publica o `Σ` inflado com log
**e marcador na própria série**: minuto sem linhas `__capacity_*` ⇒ `__total__` não confiável.
Ocupação por tipo segue AMOSTRADA (`max` de somas — P2).

**F5b ✅ 2026-08-02:** o *live fallback* de `pool_status_get` devolvia `SCARD(pool:instances)` —
PERTENCIMENTO, não capacidade (conta instância lotada como disponível, ignora vaga gasta em pool
irmão, não filtra pausa/wrap-up), num tool que o Skill Flow usa para decidir oferta de canal **ao
cliente**. Agora devolve `available: null`, `status: "unknown"` e o motivo; a fila segue respondida
(é fato do pool). **Mudança de contrato**: fluxo que compare `available` numericamente recebe `null`
no caso sem snapshot.

**Pico de ocupação é EVENT-DRIVEN, não amostrado** (P1, 2026-08-02). Pico é o máximo de uma função
escada: qualquer intervalo de amostra pode cair inteiro entre duas subidas, e encurtar o intervalo
só estreita a classe de falha. O valor é gravado na TRANSIÇÃO — watermark `{t}:pool:{p}:peak:{minuto}`
(+ `:peakcap:` com a capacidade **do instante do pico**, TTL 2 h), por `record_pool_peak`, com três
chamadores e nenhum a mais: **(1) alocação** (`mark_busy`, sobre o `used_here` que o recompute já
devolveu — único que faz o pico SUBIR), **(2) virada do bucket** no flusher (carga carregada:
`max(novo) := ocupação corrente`), **(3) liberação** (`release_instance`, com o valor de ANTES — o
mesmo seed da virada, disparado por evento, para o pico que sobe e desce entre duas passadas do
flusher). **INVARIANTE: o bump NUNCA mora dentro de `write_pool_snapshot`** — lá ele faria a F3a
bumpar em liberações e o pico voltaria a ser *amostrado nos instantes de escrita de snapshot*, sem
nada ficar vermelho; `write_pool_snapshot` apenas **devolve** o recompute. `_occupancy_sampler` virou
**flusher** (mesmo tópico `pool.occupancy`, mesma tabela `pool_occupancy_peaks`, mesmo endpoint,
mesma UI); segue amostrando só os agregados de admissão (item 7b).

**P2 ✅ — o `__total__` do tenant também é event-driven.** Não é derivável dos watermarks por pool
(`max` de SOMAS ≠ soma de `max`: quatro pools com pico 1 no mesmo minuto e total real 2). Fonte =
ZSET `{t}:occupancy` (`instance → ocupação`, `ZREM` em zero ⇒ cardinalidade O(ocupadas)); atalho O(1)
= contador `{t}:occupancy:total`, ambos escritos num Lua que tira o delta de `ZSCORE` antes/depois.
Ganchos DENTRO de `claim_instance`/`release_instance`/`swap_to_hold` (nunca nos call sites), FORA do
Lua da vaga (que é single-key/cluster-safe por decisão). **INVARIANTE: o contador só existe porque é
CONFERIDO** — `reconcile_tenant_occupancy` roda 1×/min no flusher, corrige para a fonte e LOGA o
drift; sem ela este contador é o `active_count` que o arco removeu, e deve sair junto. Não clampa
negativo: total impossível é a única evidência de caminho de vaga fora dos ganchos. **Descontinuidade na série:**
a fonte mudou de `active_count` (derivava para cima) para `used_here`, e agora o método mudou —
marcar a data no eixo se a série virar base de dimensionamento.

---

## Ver também

- `CLAUDE.md` § *Admissão de sessão — UM gate, na moeda certa* — o portão que consome esta
  aritmética, e a lista do que **não reviver**
- [`docs/product/shared-capacity-pool-as-tag-design.md`](../product/shared-capacity-pool-as-tag-design.md)
  — desenho da capacidade compartilhada (pool como tag)
- [`docs/arcos/capacity-governance.md`](capacity-governance.md) — contratado como fonte única
- Gate: `infra/test/gate_pause_capacity.sh`
