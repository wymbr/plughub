# ADR — Identidade por-pool do agente humano: derivar no escopo, não armazenar no global

> Status: **aceito** · 2026-07-27 · **F1 + F2 + F2b + F3 + F4 ✅ implementadas** (ver `CHANGELOG.md`);
> **F5 pendente** (higiene + `@mention`)
> · Contexto: exposto no E2E da Phase 2 do wrap-up unificado
> (ver `CHANGELOG.md` § Wrap-up unificado Phase 2 e `docs/guias/conference-mechanics.md` Mudança 27)
>
> Relacionado: [`adr-participant-identity-single-source.md`](adr-participant-identity-single-source.md)
> — mesma família de defeito, camada diferente (participante-em-segmento × instância-em-pool).

---

## 1. Contexto

Um humano logado em N pools tem **UMA** instância de roteamento, `human-{userId}`. O registro dela
(`{tenant}:instance:{instance_id}`, JSON) carrega num único documento **três categorias de fato com
escopos diferentes**, todas tratadas como campo global last-writer:

| Categoria | Campos | Escopo REAL do fato |
|---|---|---|
| Recurso | `user_id`, `user_login`, `max_concurrent`, `execution_model`, `source`, `status` | por-recurso ✅ correto |
| Membership/identidade por-pool | `agent_type_id`, `pool_id`, `pools[]` | **por-(recurso, pool)** ❌ |
| Contabilidade de contato | `current_sessions`, e o `pools` lido no `agent_done` | **por-sessão** ❌ |

Evidência ao vivo (2026-07-27): o Console mostrava "Ready in 3 pools" enquanto o Redis tinha
`agent_type_id: "human_agent_aprovacao_deploy"` e `pools: ["aprovacao_deploy"]`; minutos antes, o
MESMO id estava como `human_agent_formfill_demo` com `pools: ["formfill_demo"]`.

### 1.1 O motor da corrupção

O `platform-ui` abre **um WebSocket por pool selecionado** (`useMultiPoolWebSocket.ts:197-206`). Cada
conexão emite um pong a cada 15 s, e o pong vira `agent_heartbeat`
(`mcp-server-plughub/src/server.ts:2772-2788`) carregando:

```ts
agent_type_id: `human_agent_${poolId}`,   // identidade DAQUELA conexão
pools:         [poolId],                  // membership DAQUELA conexão
```

Do outro lado, `routing-engine/kafka_listener.py:346-361` (`_upsert_instance`) **reconstrói o registro
inteiro a partir do evento** — `pools = event.pools`, `pool_id = (event.pools or [""])[0]`,
`agent_type_id = event.agent_type_id`. Não é merge: é substituição. Com N conexões, `pools[]` e
`agent_type_id` **oscilam a cada 15 s** entre os N pools, conforme quem pingou por último.

Dois outros produtores agravam:
- `server.ts:617-627` (logout parcial) publica `agent_type_id: human_agent_${poolId}` com o pool que
  está sendo **deixado** — carimba a identidade do pool morto numa instância ainda ativa nos outros.
- `orchestrator-bridge/main.py:7827-7860` (`_restore_instance`) faz `SET` do **snapshot congelado
  inteiro** por cima do registro vivo (`session:{sid}:routing:{iid}`), podendo restaurar um
  `pools[]` parcial capturado horas antes.

### 1.2 Por que isso é a mesma família do ADR de participante

O invariante já registrado no CLAUDE.md — *"never derive or duplicate participant identity into a
wider-scope field"* — descreve exatamente este defeito, só que uma camada acima. Lá, um fato de
**segmento** (qual humano este hook serve) morava num campo de **sessão**. Aqui, um fato de **(recurso,
pool)** mora num campo de **recurso**. A manifestação é idêntica: com cardinalidade 1 tudo concorda por
acidente; com N o campo guarda **um** valor e todo leitor que assume outro escopo lê errado.

> **Nota de camada.** A identidade de *participante* (`part_*`, por join/segmento) **já está correta** e
> não é objeto deste ADR. O defeito é exclusivo da camada de **instância de roteamento**.

### 1.3 Manifestações (blast radius levantado)

| # | Sintoma | Site |
|---|---|---|
| **B1** | **Skill errado executado.** `router.py:400` / `:682` propagam `inst.agent_type_id` para `conversations.routed`; o bridge (`main.py:2983-2998`) resolve esse valor via `get_agent_type()` e **roda o que resolver**. Um contato roteado ao pool A pode chegar com `agent_type_id` do pool B. | routing-engine + bridge |
| **B2** | **Pools encolhendo.** `_restore_instance` (`main.py:7854`) e o flapping do heartbeat removem o humano de pools onde ele está logado (`set_instance:479-490` faz SREM dos pool sets a partir do `pools[]` do momento). | bridge + routing-engine |
| **B3** | **Pool errado decrementado.** `remove_conversation` (`registry.py:560-561`) escolhe o pool a decrementar por `meta.pools` → `event.pools` → `inst.pools`, todos last-writer. Um contato servido em A pode DECR o `active_count` de B → A fica com carga fantasma, fila não drena. | routing-engine |
| **B4** | **Capacidade errada.** `max_concurrent` alimenta o teto do `claim_instance` (`router.py:324`, `:646`) e o `total_capacity` do snapshot (`registry.py:1292`). Valor baixo demais rejeita alocações válidas em silêncio. | routing-engine |
| **B5** | **Contato não chega ao Console.** `main.py:3154` ativa o humano só se `execution_model == "stateful"`; a preservação desse campo (`registry.py:458-465`) depende de `source == "human_login"` ter sobrevivido ao último write. | bridge |
| **B6** | **@mention no pool errado.** `tools/session.ts:184` resolve `mentionable_pools` pelo `pool_id` **global** da instância, não pelo pool da conversa. *(Hoje o bug é mascarado: é um `HGET` contra uma chave JSON string → WRONGTYPE engolido por `catch {}` → mentions caem em silêncio. Corrigir o tipo sem corrigir o modelo troca um no-op silencioso por um convite ao pool errado.)* | mcp-server |
| **B7** | **Crash recovery no pool errado.** `crash_detector.py:144` usa `meta.pools[0]`. Mitigado só porque `:98` pula instâncias `human-*` — mas `remove_conversation` usa o MESMO `meta` e não é pulado. | routing-engine |

**B1 já se materializou em produção-demo**: no resume de um workflow reivindicado por pull, o
`agent_ready` reescreveu a instância humana com a identidade do WORKFLOW; o contato seguinte chegou com
`agent_type_id=skill_wrapup_detached_v1` e o bridge rodou o wrap-up **na sessão do contato**, que
completou e a fechou — o contato "sumia" da fila. O **sintoma** foi corrigido na Mudança 27
(`wf_agent.instance_id` + guard que descarta `instance_id` `human-*` no resume). A **causa** — fato
por-pool em campo global — é o objeto deste ADR.

### 1.4 Achado colateral — ativação humana por falha de resolução

O bridge só chega em `activate_human_agent` **depois** de `get_agent_type("human_agent_{pool}")` falhar
e cair no fallback 2 (`main.py:3141-3164`). A ativação de humano depende hoje de um lookup **não
resolver**. Qualquer AgentType cadastrado com esse nome quebra o Console inteiro. Registrado aqui
porque a decisão abaixo torna o conserto trivial (§4.2b).

---

## 2. Decisão

> **Fato por-pool não se armazena no registro global do recurso: deriva-se no escopo onde o pool é
> conhecido. Fato por-sessão não se lê do registro do recurso: viaja com a sessão. E evento de
> liveness (heartbeat) nunca carrega identidade nem membership.**

Concretamente, o registro `{tenant}:instance:{instance_id}` passa a ser **exclusivamente do recurso**:

| Fica no registro | Sai do registro |
|---|---|
| `instance_id`, `user_id`, `user_login`, `source`, `execution_model` | `agent_type_id` (humano) → **derivado** `f(pool)` |
| `max_concurrent` (capacidade **do humano**) | `pool_id` singular → **removido** (é `pools[0]`, estruturalmente sem significado em multi-pool) |
| `status` (`ready`/`paused`) | `current_sessions` como verdade → **já superado** pelo SCARD do semáforo |
| `pools[]` como **conjunto de membership**, mutável só por eventos autoritativos (login/logout) | `pools[]` como carona de heartbeat → **proibido** |

Para humanos, `agent_type_id` é **função pura do pool**: `human_agent_{pool_id}`. Todo leitor que
precisa dele já tem o pool em escopo (`for pool in pools: for inst in get_ready_instances(pool)`).
Logo o campo não precisa existir — e o que não existe não pode divergir.

Para instâncias **de IA** o campo continua sendo identidade legítima (uma instância de IA pertence a um
agent type e a um pool, criada assim pelo `instance_bootstrap`). A resolução é uniforme:

```
resolve_agent_type(inst, pool_id):
    if inst.source == "human_login":  return f"human_agent_{pool_id}"
    else:                             return inst.agent_type_id
```

---

## 3. Respostas às perguntas do desenho

### Q1 — Identidade por-pool (`human-{userId}@{pool}`) ou campos por-pool fora do JSON global?

**Nem uma nem outra na forma literal: os campos por-pool deixam de ser armazenados — passam a ser
derivados.** A instância continua **uma por recurso**.

**Instância por (user, pool) foi descartada** por três razões independentes, qualquer uma bastando:

1. **Fragmenta a capacidade.** O semáforo é `{t}:instance:{iid}:sessions`. Instância por pool ⇒
   semáforo por pool ⇒ um humano com `max_concurrent=3` logado em 3 pools aceita **9** sessões.
   Manter o semáforo por-recurso com instância por-pool exige chavear o semáforo pelo `user_id`
   enquanto tudo mais chaveia por `instance_id` — assimetria que vaza em claim/release/hold.
2. **`human-{userId}` é identificador de fio e de dado histórico.** É **desconstruído** para extrair o
   `user_id` em ~10 sites (`router.py:606`/`:640` no `assigned_to` do pull direcionado,
   `bridge:1393`/`:2524`/`:7038`, `segment.ts:154`, `PullInboxPanel.tsx:163`,
   `crash_detector.py:98`) e é **construído em SQL sobre linhas já persistidas no ClickHouse**
   (`analytics-api/reports_query.py:3598`, `:4588` — `concat('human-', user_id)`). Mudar o formato é
   migração de dado histórico, não refactor.
3. **Quebra a quota `C_human`.** `server.ts:369` conta `KEYS {tenant}:instance:human-*` como *logins
   concorrentes*. Por-pool, o mesmo humano passaria a consumir N unidades da quota contratada.

E o benefício alegado — "cada campo fica correto por construção" — é obtido **de graça** pela
derivação, sem tocar em id nenhum.

**Alternativa intermediária também descartada:** manter o campo, mas como mapa
(`agent_type_by_pool: {pool: type}`) dentro do JSON. É armazenar uma projeção derivável: cria um
segundo lugar para divergir (mapa desatualizado × pool set), sem responder o que a derivação já
responde. *Guardar o que dá para calcular é convidar o cálculo e o guardado a discordar.*

### Q2 — O semáforo é por recurso ou por pool?

**Por recurso, sem exceção.** `{t}:instance:{iid}:sessions` com `iid = human-{userId}` continua sendo
a única contagem de ocupação, e `max_concurrent` continua sendo atributo **do humano**. Esta é a
restrição que decide a Q1: qualquer desenho que fragmente esse SET está errado.

**Não-objetivo declarado:** sub-teto por pool ("no máximo 2 das minhas 3 vagas em `retencao_humano`")
é *política de pool*, não identidade, e está **fora de escopo**. Se um dia entrar, entra como um
predicado sobre occupants tagueados com o pool — o que exigiria estender o occupant
(`{session_id}::{conference_id}` → mais um campo) e é decisão separada. Registrado para que ninguém
justifique instância-por-pool com este requisito.

### Q3 — Snapshots `session:{sid}:routing:{iid}` já gravados: migrar ou tolerar?

**Nem migrar nem tolerar passivamente: encolher o snapshot para o que ele realmente é, e tornar a
restauração não-destrutiva.** Feito isso, os snapshots antigos ficam inofensivos e a migração deixa de
existir (TTL de 4 h drena o resto sozinho).

O snapshot foi criado para um problema que **não existe mais para humanos**: *"a routing marca a
instância busy com TTL 30 s; sem heartbeat a chave expira antes da sessão acabar"*. Hoje
`set_instance:469-471` grava instância humana com **KEEPTTL** (chave permanente, dona é a mcp-server) e
o WS renova a cada 15 s. E `current_sessions` deixou de ser verdade — o SCARD do semáforo é (Fatia B).
Então o `-1` em `snapshot["current_sessions"]` (`main.py:7849`) é **escrever contador derivado obsoleto
por cima de registro vivo**.

O que o snapshot legitimamente carrega e ninguém mais tem é o **pool desta sessão para esta
instância** — o único fato por-(sessão, instância) do conjunto. Ele encolhe para isso:

```
session:{sid}:routing:{iid}  →  { tenant_id, instance_id, pool_id }
```

E `_restore_instance` deixa de ser `SET` de documento inteiro:

- chave viva presente → **patch** só de `status`; nunca toca `pools`/`agent_type_id`/`max_concurrent`/`user_*`;
- chave ausente **e instância humana** → **não recria**. Ressuscitar humano deslogado é criar agente
  fantasma; quem é dono do ciclo de vida é o login WS. Loga o motivo (invariante de método:
  degradação nunca é silenciosa);
- chave ausente e instância de IA → recria (comportamento atual, legítimo: TTL 30 s real).

**Compatibilidade:** snapshots no formato antigo (com `snapshot: {...}`) continuam sendo lidos — o
leitor novo simplesmente **ignora** o sub-documento e usa só `pool_id`. Zero migração, zero janela.

### Q4 — Quem mais lê `instance.agent_type_id` de uma instância humana?

Varredura completa (todos os pacotes). Leitores do campo:

| Site | Uso | Impacto se errado |
|---|---|---|
| `router.py:290` | filtro duro de conferência (`event.agent_type_id != inst.agent_type_id`) | especialista convidado não é encontrado |
| **`router.py:400`, `:682`** | `RoutingResult.agent_type_id` → `conversations.routed` | **B1 — bridge roda o skill errado** |
| `router.py:296-298`, `:509` | chave de `{tenant}:agent_perf:{agent_type_id}` | score de performance do tipo errado |
| `decide.py:211-225` | resposta do `/v1/routing/decide` | decisão exibida errada |
| `crash_detector.py:171`, `:183`, `:195` | hint de re-rota + evento `agent_crash` | re-rota errada (hoje humanos são pulados em `:98`) |
| `mcp-server/server.ts:2173-2213` | `/api/agent-resume` republica `agent_ready` com ele | propaga a corrupção após pausa |
| `bridge/main.py:5831`, `:7781` | `agent_done` sintético do crash-recovery | fecha segmento com tipo errado |
| `mcp-server/server.ts:2319` → `platform-ui` AgentsPage | exibição | Monitor mostra tipo errado |

**Todos os leitores de decisão têm o `pool_id` em escopo** — é o que torna a derivação viável sem
mudar assinatura de nada. `analytics-api` **não** lê o registro: consome `agent_type_id`/`pools[]` do
payload Kafka de `agent.lifecycle` (`consumer.py:742-796`) — o que significa que o flapping do
heartbeat **também polui os intervalos de presença por pool** (`_apply_pool_diff`), e a correção do
produtor (Fase 1) conserta o analytics de tabela.

Leitores de `pools[]`/`pool_id` (mesma classe): `registry.py:560`, `:585`, `:625` (B3);
`kafka_listener.py:591-595` (`_deactivate_instance` SREM em todos os pools); `server.ts:2100-2125`
(pause) e `:2173-2213` (resume); `session.ts:184` (B6); `crash_detector.py:144`;
`instance_bootstrap.py:736-749`.

### Q5 — Fases, menor primeiro passo que já elimina a corrupção

Ordenadas por **razão dano-removido / superfície-tocada**.

| Fase | Entrega | Elimina | Superfície |
|---|---|---|---|
| **F1 — liveness ≠ identidade** ✅ | `agent_heartbeat` para de carregar `pools`/`agent_type_id`/`current_sessions` e passa a mandar `heartbeat_pool` (diz de qual conexão veio o sinal **sem se passar por membership**); `_upsert_instance` **preserva** os fatos de recurso do registro vivo e aceita substituição de `pools[]` **só** em `agent_ready` (login/logout, que já mandam `mergedPools`/`remainingPools`); logout parcial passa a nomear um pool **remanescente**. | **B2 inteiro** e a **origem** de B1/B4 (o flap de 15 s). Também limpa os intervalos de presença do analytics. | 2 arquivos, ~2 funções |
| **F2 — derivar no escopo** ✅ | `resolve_agent_type(inst, pool_id)` em `models.py`, usado nos **7** leitores de decisão (`router._allocate` ×2, `RoutingResult` do route/afinidade/`work_task_claim`, `decide` ×2). O campo armazenado deixa de ser load-bearing para humanos. | **B1 estruturalmente** (não só a origem) | routing-engine |
| **F2b** ✅ | Bridge decide humano pela **identidade da instância** (`_is_human_instance`, hoisted ANTES do `get_agent_type`) em vez de pela **falha** do lookup (§1.4). Fallback 2 permanece p/ stateful não-humano (MCP externo). | classe de bug latente | bridge, 1 branch |
| **F3 — contabilidade por-sessão** ✅ | **Precedência invertida** em `remove_conversation`: o `pools` do **evento** (per-sessão, mandado por quem sabe) vence `meta.pools` (per-recurso = conjunto INTEIRO). Bridge manda o pool da sessão no `agent_done` do claimante, lido do **snapshot de roteamento** (`session:{sid}:routing:{iid}.pool_id`, o único fato por-(sessão, instância) que já existe). | **B3** | 1 linha + 1 site no bridge |
| **F4 — snapshot não-destrutivo** ✅ | `session:{sid}:routing:{iid}` encolheu para `{tenant, instance, pool}` (novo `_write_routing_ref`, 4 sites de escrita); `_restore_instance` virou **patch** (só `status` + ocupação lida do SCARD) e **não recria** instância ausente; o `-1` cego em `current_sessions` saiu. Formato antigo tolerado por construção — ninguém lê mais o sub-documento `snapshot`. | **B2 residual**, deriva de contador | bridge |
| **F5 — higiene + rede de proteção** | Remove `pool_id` singular do `AgentInstance` (ou marca AI-only); corrige o docstring falso de `update_instance_meta` (`registry.py:499`: *"pools and agent_type_id do not change during the instance lifetime"* — falso para humanos, e é a premissa sobre a qual `remove_conversation` e `crash_detector` foram construídos); **testes de estabilidade multi-pool** (não existe nenhum: `test_work_queue_claim.py:78` e `test_scorer.py:34` só constroem `pools=[pool]`). B6 (`session.ts:184` → pool da sessão + conserto do WRONGTYPE) entra aqui ou vira item próprio. | regressão futura | vários, baixo risco |

### 3.1 Emenda as-built (F1) — liveness também não CRIA

Durante a F1 apareceu um caso que o desenho não tinha nomeado: **registro ausente + evento de
liveness**. A primeira versão reconstruía a instância a partir do `heartbeat_pool` para o agente não
ficar invisível ao roteamento. Está **errado**, e pelo mesmo motivo que o resto do ADR: uma aba
esquecida continua pingando depois do **logout completo** (que faz `DEL` da chave), e a reconstrução
ressuscitaria um **agente fantasma** — presente para o roteamento, ausente para o humano. Os contatos
alocados a ele não apareceriam em Console nenhum.

> **Evento de liveness não cria instância — só renova a que existe.** Criação é do login
> (`agent_ready`). Registro ausente + pong ⇒ ignorar e logar por quê.

Falhar **visível** (o agente some da fila até dar refresh) é preferível a falhar **invisível** (o
contato some). A mesma regra vale para `_restore_instance` na F4 — a emenda apenas a antecipa para o
consumidor de Kafka.

**Menor primeiro passo isolável: F1.** Duas funções, sem mudança de contrato, e mata o mecanismo que
produz a corrupção a cada 15 segundos. F2/F3 tornam o sistema imune a um produtor mal-comportado
futuro; F1 sozinha já para o sangramento observado.

**Sugestão de instrumentação junto da F1** (custo ~nulo, valor alto): logar `warning` quando um write
fosse **encolher** `pools[]` de instância humana ou **mudar** seu `agent_type_id`. Mede a frequência
real e vira detector de regressão permanente. Alinhado com *"um valor plausível esconde bugs"* — o
`pools` parcial sempre pareceu razoável, e por isso passou meses.

---

## 4. Consequências

- Categoria de bug eliminada: **fato por-(recurso, pool) morando em campo por-recurso**. É a segunda
  ocorrência da mesma família (a primeira foi participante-em-segmento no campo de sessão).
- O invariante do CLAUDE.md ganha alcance. Redação proposta para substituir a atual:

  > **Never store a narrower-scope fact in a wider-scope field — derive it where the scope is known.**
  > Identidade de participante é por-segmento (`segment.{segId}.*`), não por-sessão. Identidade e
  > membership de instância são por-(recurso, pool) — derivadas do pool em escopo, nunca congeladas no
  > registro do recurso. Contabilidade de contato é por-sessão e viaja com a sessão.
  > **Evento de liveness (heartbeat) nunca carrega identidade nem membership** — só prova que o
  > recurso está vivo.

- `max_concurrent` fica inequivocamente atributo **do humano**. Sub-teto por pool, se um dia existir,
  será política de pool sobre o mesmo semáforo — não um segundo semáforo.
- `human-{userId}` permanece estável: nenhum consumidor de fio, nenhum SQL de ClickHouse, nenhuma
  linha histórica é tocada.
- **Não** cria tabela, tópico, chave Redis nem serviço. É, no saldo, **remoção** de estado.
- Risco residual conhecido: `execution_model`/`source` continuam sendo preservados por leitura-antes-
  de-escrita (`registry.py:448-467`). F1 estende essa preservação aos demais campos de recurso, mas o
  padrão read-modify-write segue não sendo atômico. Aceitável (o produtor autoritativo é único por
  evento); registrado para não ser redescoberto como novidade.

## 5. Alternativas descartadas

- **Instância por (user, pool)** — §3/Q1: fragmenta capacidade, quebra `human-{userId}` como chave de
  fio e de dado histórico, e infla a quota `C_human`.
- **Mapa `agent_type_by_pool` no JSON** — armazena o que é derivável; cria um segundo lugar para
  divergir do pool set.
- **Só corrigir o produtor do heartbeat** (F1 sem F2/F3) — para a corrupção observada, mas deixa todo
  leitor confiando num campo global que qualquer produtor futuro pode reescrever. F1 é o *primeiro*
  passo, não o desenho.
- **Migrar os snapshots existentes** — desnecessário: tornar a restauração não-destrutiva (F4) faz os
  antigos ficarem inofensivos, e o TTL de 4 h drena o resto. Migração aqui seria trabalho para obter
  o que a tolerância no leitor dá de graça.
