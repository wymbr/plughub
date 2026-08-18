# Handoff — segmento que nunca fecha (causa raiz CONSERTADA) + o que sobrou

> Sucede [`kickoff-segmento-aberto-e-volume-sessoes.md`](kickoff-segmento-aberto-e-volume-sessoes.md),
> que está **encerrado**: os dois itens dele foram respondidos. Este arquivo existe para a próxima
> sessão não recomeçar pelo que já caiu.

---

## O que ficou fechado (não reabrir sem dado novo)

| | |
|---|---|
| **Causa raiz do segmento aberto** | `conversations.participants` publicado **sem `key`** em tópico de 3 partições ⇒ `joined`/`left` do mesmo segmento invertiam ⇒ o `joined`, inserido depois, vencia a dedup nas DUAS tabelas |
| **Conserto** | `key=session_id` no bridge **+** `ReplacingMergeTree(row_version)` (versão do EVENTO) em `segments` e `participation_intervals`. Uma parte sozinha não bastaria |
| **Gate** | 3/3 reproduções fecharam, na forma que antes era moeda (2/4). Confirmado por segundo instrumento: `queue` com `ultimo_dia` = dia do teste e abertos ainda 2 (os históricos) |
| **Volume de sessões (118 × 285)** | Mesmo dado, principais diferentes: o ABAC `accessible_pools` custa 166 linhas; sem header `Authorization` o endpoint devolve 285 (falha ABERTA, documentada em `pool_auth.py:130-135`) |

**Hipóteses eliminadas por medição — não refazer:** LPUSH `__agent_available__` ausente · marcador
`queue:agent_active` sumindo · despejo do Redis · wipe do e2e · empate de RMT como *causa* (é o
mecanismo da perda, não a origem) · `asyncio.create_task` sem referência forte · os 3 tiers do
`/reports/sessions` · degradação do `pools_client` · rajada de e2e · produtor fantasma de sessões.

---

## Pendência 1 — o passado *(DECIDIDO 2026-08-18: expurgar, não reparar)*

Eram **9** segmentos abertos em sessão fechada: `primary` 5 · `queue` 2 · `specialist` 2, todos
anteriores a 2026-08-15. O `DEFAULT` do `row_version` só repara onde as duas linhas ainda coexistem; nos
antigos o merge já apagou fisicamente a perdedora.

**Decisão: EXPURGAR com data de corte, não reparar por reprocessamento.** As três opções e por que esta:

| opção | veredicto |
|---|---|
| reparar por replay do tópico | **recusada** — re-emite eventos que o consumer trata como `live`, e o discriminador `origin` existe justamente para essa fronteira (`adr-quality-substrate-isolation.md`). Risco caro por 9 linhas em 800 |
| fechar sinteticamente (inventar `ended_at`) | **recusada, e é a pior** — fabrica dado. "Não sei quando terminou" vira um número plausível, que é o modo de falha que a § Postura de Engenharia manda caçar |
| **expurgar antes do corte** | **adotada** — a linha é irrecuperável e sabidamente quebrada; removê-la é honesto, e faz a baseline do gate cair a **0**, o que torna o probe capaz de ficar vermelho |

O ganho decisivo é o do instrumento: com baseline 9 o gate lia 9 para sempre e **nunca poderia
reprovar**; com baseline 0 qualquer aberto novo é vermelho sem aritmética.

---

## Pendência 2 — `queue_config.skill_id` é decorativo *(defeito vivo, separado)*

Não era a causa do segmento aberto, e continua de pé. `resolve_flow_for_agent`
(`orchestrator-bridge/main.py:494-497`) resolve produção pelo **slot `current` do POOL**, e
`_activate_queue_agent` passa o **pool de destino** ⇒ o `queue_config.skill_id` **nunca é consultado**.

Medido: `retencao_humano` declara `skill_fila_v1` (existe, `published`, com flow), não tem slot, e o
agente de fila **nunca roda** — `activate_native_agent` devolve `{}` em 3 ms, visível como `ERROR`
*"NENHUM slot `current`"* em toda reprodução. Os 12 segmentos `queue` que completam com `handoff` são de
pools **sem** `queue_config`: rodaram o skill do próprio pool sob `role='queue'`.

**Três consequências, e a terceira é a que decide o desenho:**

1. a config aparece na UI e não executa nada;
2. o relatório de Fila/SLA registra "espera atendida" onde não houve agente de fila algum
   (`docs/arcos/queue-attended-model.md` mede um agente que, neste pool, nunca rodou);
3. **o segmento de fila nasce antes de se saber se o agente pode rodar** — o marcador (`:5504`) e o
   `participant_joined` (`:5527`) são escritos antes da resolução do flow (`:5546`). Daí existir
   segmento `queue` de 3 ms que não enfileirou ninguém.

**DECIDIDO 2026-08-18 — saída (a):** o queue agent passa a resolver pelo `queue_config.skill_id`, e o
pool de fila ganha **deploy próprio** (slot). A saída (b) — remover `queue_config` do schema — foi
descartada; ela parecia barata sob o alvo n8n, que foi abortado no mesmo dia.

**Duas medições que mudaram o desenho da saída (a):**

1. **O endereço não pode ser `skill_id`.** `resolve_flow_for_agent` (docstring, `main.py:585`) é
   categórico: *"PRODUÇÃO = snapshot do slot `current` do POOL. Ponto."*, e registra que os fallbacks
   para `skill.flow` foram removidos em 2026-07-13 por serem **vazamento** (pool sem slot executava a
   definição viva ⇒ edição ia a produção sem deploy). Resolver o agente de fila por `skill_id`
   reabriria esse vazamento. Somado ao invariante *"o POOL é a unidade endereçável — nunca o
   `skill_id`"* (com a razão da ambiguidade N-pools), a saída (a) só tem uma forma coerente:
   **`queue_config` endereça um POOL de fila**, com deploy próprio. `skill_id` vira endereço legado.
2. **`pool_id` carrega dois fatos na mesma variável.** Em `:5670` é a **dimensão de relatório** (pool de
   destino — *"onde o contato esperou"*, deliberado e documentado); em `:5693` é **o deploy a executar**.
   Coincidirem é a causa de o agente de fila rodar o skill do pool de destino. Separá-los é o invariante
   *"never store a narrower-scope fact in a wider-scope field"*.

### Fases

- **F1 ✅ 2026-08-18 — a ORDEM.** A resolução do flow subiu para **antes** do marcador e do
  `participant_joined`; sem flow, o bridge loga ERROR nomeando o pool e retorna **sem marcador e sem
  segmento** (mesmo comportamento de um pool sem `queue_config`: o contato espera em silêncio e o drain
  re-roteia). Fecha a consequência 3 — o segmento `queue` de 3 ms que não enfileirou ninguém. Custo de
  I/O zero no caso comum: `get_pool_current_flow` guarda por pool em `_pool_flow_cache`, e é a mesma
  leitura que o `activate_native_agent` faria em seguida. Gate:
  `infra/test/gate_queue_segment_not_born_without_flow.sh <T0>` (reprodução manual, como na família A).
  **F1 não conserta o endereçamento** — o slot consultado segue sendo o do pool de DESTINO.

  **Reprodução ao vivo (2026-08-18 20:15), com a cadeia inteira amarrada pelo `session_id`:**
  `20:15:22.083` routing `Queued session=28e999f3 pool=retencao_humano — no agents available` →
  `20:15:22.092` bridge `Agente de fila NÃO ativado … agent=skill_fila_v1 — nenhum flow executável`
  (9 ms depois) → `20:16:50` drain `no queue agent active; marker=… ttl=-2` → re-roteia →
  **0 segmentos `role='queue'`**. O `ttl=-2` — que na tentativa anterior era só indício — passou a ter
  causa nomeada na linha acima dele. E o `agent=skill_fila_v1` do log é o defeito de endereçamento em
  concreto: o agente resolveu para o SKILL, mas o slot consultado é o do pool de destino.

  ⚠️ **A testemunha de presença NÃO foi exercitada** nessa execução (`ativações: 0`): o verde diz *"a
  recusa não deixa segmento nascer"*, não *"o caminho feliz continua criando segmento"*. O gate passou a
  declarar isso em voz alta em vez de calar. Cobrir as duas metades exige, na MESMA janela, um pool que
  recusa e um que ativa — o que a F3 destrava naturalmente.
- **F2 — o ENDEREÇO** *(pendente)*: `queue_config.pool_id` em schemas + agent-registry + UI; o bridge
  passa o **pool de fila** ao `activate_native_agent` e mantém o **pool de destino** no segmento.
- **F3 — o DEPLOY** *(pendente)*: dar pool de fila com slot promovido ao `retencao_humano` e validar
  ponta a ponta que um agente de fila roda de fato. Sem F3, F2 é campo novo sem consumidor — a própria
  consequência 1 que P2 existe para fechar.

---

## Pendência 3 — o conserto foi validado em UMA forma só *(família B `primary` MEDIDA 2026-08-18)*

O gate original cobriu o caso de **falha rápida** (3 ms, `role='queue'`).

### Fechado por medição: família B, papel `primary`

`infra/test/gate_family_b_resume_closes.sh` reproduz ao vivo o ciclo **trigger → delegate que
suspende → resume → complete** no pool webhook `formfill_demo_ia`, e mede a janela `started_at >= T0`
(T0 lido do relógio do **ClickHouse**, não do host). Exercita os quatro publishes do caminho:
`4352` joined · `4653` left (`outcome='suspended'` — **o que sumia**) · `8210` joined · `8241` left.

Previsão escrita ANTES de rodar, e o medido, com N=6:

| | previsto | medido |
|---|---|---|
| `total_janela` | 12 | **12** (todos `primary`/`native`) |
| `fechados_suspended` | 6 | **6** |
| `abertos_janela` | 0 | **0** |
| `abertos_total` | 9 (inalterado) | **9** |

A calibração com N=1 (2 · 1 · 0 · 9) resolveu a incerteza declarada: o parqueamento no pool pull
**não** emite segmento `queue`. Os pares `joined`/`left` ficaram a **6–21 ms** — o regime mais
apertado possível, o mesmo em que a família A reproduzia (3 ms). N=6 = 12 pares: sob a taxa de falha
medida na família A (moeda, 2/4) um verde por sorte tem probabilidade 1/4096.

**O que o verde prova, e o que não prova.** Prova que o caminho `primary` de suspend/resume **não
órfã hoje**. NÃO prova, por si, que foi o conserto de 08-18 que o fechou — não houve controle
negativo (exigiria stash + rebuild). O que liga as duas famílias não é estatística e sim **código**:
`key=session_id` mora no funil `_publish_participant_event` (`main.py:3250`), por onde passam os 13
call sites do bridge. "Mesmo conserto" é fato estrutural; o que era inferência — *"mesmo defeito"* —
passou a ter medição do lado `primary`.

`close_reason` NULL nos 12 é **por construção** (o site `:4653` não passa o campo), não achado. Não
confundir com o caso humano, onde vazio seria defeito (`smoke_approval_segment_closes.sh`).

### Continua NÃO medido

- **`specialist`.** O único join com esse papel é `process_routed:4352` com `conference_id`
  preenchido (delegate-as-conference), e nenhum pool webhook do demo alcança esse caminho — o
  `formfill` delega a pool **pull**. Estruturalmente coberto pelo mesmo funil; sem amostra viva.
- segmento de fila **longo** (agente rodando de fato — só reproduzível com
  `ALLOW_LIVE_FLOW_FALLBACK=true`, hoje declarado no compose e vazio por defeito).
- **Limite do verde sob carga.** O `participant_left` de `:4653` sai por `asyncio.create_task` sem
  referência forte — é literalmente uma das 78 da Pendência 4, na linha que este gate exercita. Um
  verde em máquina ociosa não cobre o caso em que o loop coleta a task antes do publish.

---

## Pendência 4 — dívidas expostas, nenhuma é causa de nada conhecido

- **78 `asyncio.create_task` no bridge, zero com referência forte** (`_bg_tasks`/`add_done_callback`:
  nenhuma ocorrência). A doc do asyncio pede a referência; o loop guarda só referência fraca. Não foi a
  causa deste bug — o evento chegou ao tópico —, mas é falha silenciosa por construção. Conserto
  mecânico: um helper que retenha e logue exceção no `add_done_callback`.
- **`_migrate_sessions_row_version` segue específica** enquanto as duas novas usam
  `_migrate_row_version` genérica. Deixado assim de propósito: reescrever um caminho já validado em
  produção, sem medição, é risco sem retorno.
- **`probe_family_a_queue_signal.sh`** cumpriu o papel e hoje tem enquadramento obsoleto (procura o
  ramo do sinal como discriminador). Manter como histórico ou podar.

---

## Instrumentos novos desta sessão

| Script | Para quê |
|---|---|
| `probe_participant_event_in_kafka.sh <sid>` | **o primeiro a rodar** se aparecer órfão novo: diz se o `left` chegou ao tópico, separando produtor de consumidor |
| `watch_queue_marker.sh <T0-ISO-UTC>` | leitor da reprodução: marcador, resolução do flow, ramo do drain com TTL, segmento resultante |
| `probe_queue_segment_exit_paths.sh` | por qual porta cada segmento de fila fechou (o `outcome` é o discriminador) |
| `probe_contacts_count_funnel.sh` | funil do `WHERE` do `/reports/sessions`; auto-verificável contra o endpoint |
| `probe_open_segments_closed_sessions.sh` | o gate; rodapé já atualizado com a baseline e o próximo comando |
| `gate_family_b_resume_closes.sh [N]` | **família B `primary`**: reproduz N ciclos suspend/resume ao vivo e mede a janela `>= T0`. Diferencial de propósito — a baseline histórica de 9 nunca muda, então um gate por total jamais poderia ficar vermelho |

Instrumentação permanente no código: `marker SET` / `marker DELETE deleted=N` em INFO no bridge, e o
**TTL da chave** no ramo ELSE do drain — foi ela que separou *"o marcador sumiu"* de *"o marcador foi
apagado por quem devia"*.

---

## Armadilhas que morderam nesta sessão (a próxima herda)

- **Parte já mesclada não mostra empate.** Consultei `segments` sem `FINAL` num caso novo e declarei
  "empate refutado" — mas o `_part` era de nível 232, já mesclado. Para ver duas versões é preciso
  pegar a linha **antes** do merge, ou usar outra testemunha.
- **`< /dev/null` no lugar errado do pipe.** `cmd | grep P < /dev/null` faz o *grep* ler `/dev/null` e
  devolver vazio. A saída vazia parece medição e não é.
- **`--tail N` não alcança o boot.** Grep de log de migração com `--tail 200` volta vazio mesmo com a
  migração tendo rodado; a prova é `system.tables.engine_full`, não o log.
- **`command -v` não acha binário fora do PATH** — `apache/kafka` guarda tudo em `/opt/kafka/bin`. O
  detector devolveu "não existe" para um binário existente.
- **MV em banco Atomic prende-se por UUID**, não por nome: `RENAME` da tabela-origem deixa a MV órfã e
  ela para de receber INSERT em silêncio. Toda migração por rebuild de tabela com MV dependente tem de
  derrubar e recriar as views — e recriar em `finally`, não no caminho feliz.
