# ADR — Relatórios: duas superfícies, lente declarada e a mesa como modo

**Status:** **F0 ✅ · F1 ✅ · F0b ✅ · T0 ✅ · T1 ✅ · T2 ✅ · F2 ✅ · T3 ✅ · F3 ✅** (2026-08-29) — resta a **F4**. As-built e achados por fase no
`CHANGELOG.md`. A F1 mexeu em três decisões deste ADR ao encontrar a realidade; as emendas estão
marcadas **[emenda F1]** na D5 e na D6, e nenhuma delas mudou o que o contrato precisa responder.
A **[emenda F3]** registra o as-built da Superfície B, o de-para das lentes da D7 (escrito antes de
medir o que existia) e as duas garantias que estavam escritas e não existiam.
**Supersede parcialmente:** a suposição, implícita nos dez endereços de `/analise/*`, de que cada
recorte de relatório merece página própria.
**Não altera:** [`adr-journey-session-segment-model.md`](adr-journey-session-segment-model.md) (os três
níveis e a natureza derivada da journey) nem
[`adr-historico-unificado-duas-visoes.md`](adr-historico-unificado-duas-visoes.md) — cujo arco F0–F5 é
justamente o substrato que este ADR passa a hospedar.
**Origem:** revisão pedida pelo dono em 2026-08-28 — *"os relatórios são pouco intuitivos e confusos"*,
mais um relatório de consumo de tokens que não existe, mais a revisão da mesa de comparação.

---

## 1. Contexto

O pedido chegou como três coisas (reorganizar relatórios · criar relatório de tokens · revisar a mesa) e
a medição mostrou que são **uma**: falta a *declaração* do que é uma lente, e falta **uma chave de
atribuição**. Tudo o mais é leitura sobre tabela que já existe.

### 1.1 Inventário medido — 17 endereços, 2 páginas mortas, 1 duplicado

| Achado | Evidência |
|---|---|
| `AnaliseComparacaoPage.tsx` (553 linhas) — **zero imports** | `grep` em `platform-ui/src`; `MetricSelector.tsx:9` ainda a anuncia como consumidora |
| `AgentReportsPage.tsx` (482 linhas) + módulo `agent-reports/` — **zero imports** | idem; o `CLAUDE.md` § Arc 8 a documenta em `/contacts/reports/agents`, **rota que não existe** |
| `EventsPage` renderizada em **dois** endereços | `routes.tsx` — `/contacts/events` e `/analise/events`, mesmo componente, duas entradas de menu |
| 3 rotas vivas e **fora do menu** | `/analise/agents-legacy`, `/flow/processos`, `/evaluation/reports` |
| `/evaluation/reports` **sem `RequireEvalAccess`** | `routes.tsx` — todos os irmãos têm; achado colateral, fora do escopo deste ADR |

A patologia não é nova e já foi diagnosticada **neste repositório, com o argumento certo**
(`routes.tsx:41-47`, sobre `AnaliseContatosPage`/`ContactsPage`): *"Import órfão não é inofensivo:
mantém a página compilando e viva no bundle, e sugere ao próximo leitor que existe um caminho até ela."*
Foi diagnosticada, escrita — e recorreu duas vezes. **Diagnóstico sem mecanismo não segura nada** (a
mesma família do DDL de `participation_intervals`, que prometia em prosa a ordenação que ninguém impunha).

### 1.2 O produtor de token está morto, e a tabela não guardaria a resposta

| Fato | Onde |
|---|---|
| `emit_llm_tokens` tem **um** chamador | `ai-gateway/.../inference.py:152`, dentro de `InferenceEngine.infer` |
| `infer` só é alcançável por `POST /inference` | `main.py:401` — **e nada em `packages/` o chama** |
| O caminho vivo é `/v1/reason` | `engine-runner.ts:206`, `skill-flow-service/index.ts:243` |
| `reason.py` **tem** os tokens e não os publica | `:158-159` e `:231-232`; usa-os só em `record_usage` (escolha de conta) |
| `analytics.usage_events` **descarta** o `metadata` | `clickhouse.py:188` + `_USAGE_COLS:1620` — perde `model_id`/`agent_type_id` que o emissor manda |

Construir a lente antes do produtor daria uma tela de zeros, ou — pior — um número plausível vindo de
algum caminho marginal. É a família *"um valor plausível esconde bugs"*, no caso mais caro: **custo**.

### 1.3 A mesa é uma máquina geral construída como página

`AgentsBenchPage.tsx` (1.590 linhas) declara a lente com **quatro** campos
(`{ id, domain, primaryKey, pct }`, `:36`) e desenha com uma cascata de **11 `if`** despachando para 6
componentes (`:874-956`). Três sintomas:

1. **Lista de exceção no estado vazio** (`:874`): `if (!hasData && lens !== 'pause_reason' && lens !== 'wrapup' && lens !== 'quality_criteria' && lens !== 'escalation_reason' && lens !== 'deploy')` —
   cinco lentes nomeadas à mão. *"Vazio ≠ zero"* virou lista que envelhece.
2. **Booleano `deployLens` espalhado pelo seletor** (`:1440-1466`, cinco condicionais): a lente `deploy`
   troca o **tipo de entidade** (pools, não agentes). O modelo real é *(tipo de entidade × métrica ×
   forma de gráfico)*, e só o do meio foi modelado.
3. **A guarda de comparabilidade existe uma vez** (cross-form em `quality`, `:894-912`) e **falta onde
   também é exigida** (`quality_criteria`, cujo requisito está escrito só em prosa no `CLAUDE.md`).

`primaryKey: null` em 4 das 10 lentes e formatos reais (`time`/`score`/`count`) inline no JSX provam que
o tipo declarado e a necessidade já divergiram.

### 1.4 O enquadramento que a medição desfez

*"Reorganizar relatórios"* parecia trabalho de UI. Não é. A mesa não está mal escrita — o código é
cuidadoso (a guarda cross-form é boa, os comentários registram a colisão de alias no ClickHouse, a
âncora no pool é bem justificada). O **modelo** é que é raso. Reescrever a página sem extrair o contrato
produziria um arquivo mais bonito com os mesmos seis pontos de edição por lente.

---

## 2. Decisões

### D1 — O **segmento** é a unidade de atribuição de custo

O evento de consumo passa a carregar `segment_id` (e `participant_id`). `pool`, `sessão`, `contato` e
`journey` são **derivações** por JOIN, nunca campos do evento.

Por que o segmento e não o pool:

- Todo segmento é executado por um recurso de **um** pool (`segments.pool_id` é não-nulo), então
  segmento→pool é total. **A inversa não vale**: um pool serve N segmentos no período, e uma sessão
  toca vários pools.
- Tirar o pool da **sessão** mentiria: `sessions.pool_id` é o pool de **ENTRADA** (D10 do modelo de
  journey), e um especialista IA invocado por `@mention` queima tokens do pool **dele**. O erro seria
  sistemático e para baixo justamente nos pools de especialista, que são os caros.
- `segments` já carrega `pool_id`, `participant_id`, `instance_id`, `flow_id`, **`deploy_version`**,
  `user_id`, `parent_segment_id` (`clickhouse.py`, `_DDL_SEGMENTS`). Carimbar o segmento entrega todos
  esses eixos de graça — inclusive **custo por versão de deploy**, que responde à observação do dono de
  que o mesmo skill em pools diferentes consome diferente (e, pelo mesmo argumento, o mesmo skill no
  mesmo pool consome diferente depois de trocar o prompt).
- Precedente: o Arc 6 Fase 2 ancorou a lente `deploy` no **pool** e não no skill, pela razão idêntica —
  *"um skill pode rodar em vários pools → âncora-skill misturaria pools"*.

**Custo real:** baixo. O `segment_id` já está em escopo em todos os saltos — chega ao `/execute` e é
injetado no engine como `segmentId` (`skill-flow-service/index.ts:635`). Falta só levá-lo ao
`ReasonRequest` (`models.py:95`, que hoje tem `session_id`/`agent_id`/`tenant_id`).

### D2 — A conta é a **efetiva**, tem identidade dupla, e a ausência é `null` nomeado

O evento carrega `account_config_id`, `account_key_id`, `model_used` e `model_profile`.

- **Efetiva, nunca configurada.** `Pool.llm_account_ids` é *preferência*: o `AccountSelector.pick()`
  a usa na primeira tentativa e cai fora dela sob throttle, e o fallback cross-provider troca de
  provedor. Derivar "conta do pool" da config acertaria em dia normal e erraria **no dia do incidente**,
  que é quando o relatório é lido.
- **Duas identidades, duas perguntas.** `config_id` (catálogo `llm_accounts`, tem `display_name`,
  sobrevive à rotação de chave) responde *custo por conta*; `key_id` (prefixo do SHA-256 da chave,
  `account_selector.py:26`) responde *depuração de rate-limit*. Guardando só o hash, **uma rotação de
  chave parece "surgiu uma conta nova"**.
- **Ausência é `null` com motivo.** `_select_provider` devolve `provider_key = None` no caminho do alias
  legado (`reason.py:94-95`). Se isso virar "conta padrão", esse caminho infla uma conta em silêncio.
- **Dois campos de modelo, não um.** `model_profile` é o que o skill pediu (`balanced`); `model_used` é
  o que respondeu. A divergência entre eles **é** o diagnóstico de fallback — some se só um for gravado.

**Ajuste de código exigido:** `_select_provider` hoje devolve só o `provider_key`; o `config_id` existe
no `LLMAccount` (`account_selector.py:83`) e é descartado no retorno. Precisa subir junto.

### D3 — Colunas de primeira classe no ClickHouse; o Postgres fica com faturamento

Existem **duas** tabelas `usage_events` alimentadas do mesmo tópico, com fidelidade diferente: a do
Postgres (`usage-aggregator/consumer.py:129`) preserva `metadata JSONB`; a do ClickHouse descarta. A
exigência de discriminar conta × modelo × tempo × quem **não é atendível por nenhuma das duas hoje**.

`analytics.usage_events` ganha `segment_id`, `account_config_id`, `account_key_id`, `model_used`,
`model_profile` como **colunas**, porque é a trilha analítica. O Postgres permanece como fonte de
faturamento e idempotência (`event_id` PK).

**Sem backfill possível** ⇒ época declarada, no molde do `sla_source.SEGMENT_SLA_EPOCH`, e a ausência
vira **contador**, não silêncio.

### D4 — Recursos por contato são **dois** números, e o pico simultâneo é do contato

| Métrica | Fórmula | Humano que sai e volta |
|---|---|---|
| recursos consumidos | `count(distinct instance_id)` sobre `segments` da sessão | **1** |
| passagens / trocas de mão | `count(segment_id)` | **2** |

São perguntas diferentes (custo × experiência do cliente) e **precisam de nomes diferentes na tela**.

O **máximo de agentes simultâneos** é varredura de intervalos sobre `[started_at, ended_at)` dos
segmentos da sessão (`+1`/`−1`, soma corrente, `max`). Duas regras: segmento aberto (`ended_at IS NULL`)
é **clampado no fechamento da sessão ou excluído, declaradamente** (silenciar subestima o pico
justamente nos contatos longos); e este pico **não é comparável** ao `pool_occupancy_peaks`, que é pico
por pool por minuto **através** de contatos — mesma palavra, grãos distintos, rótulos obrigatoriamente
diferentes.

> A sobreposição de segmentos que torna `Σ duration` inválido é **exatamente** a grandeza medida aqui.
> A não-aditividade deixa de ser advertência e vira a métrica.

**O pool de entrada é dimensão de agrupamento do contato; o pool do recurso é fato do segmento.** Usar a
entrada como atribuição do recurso creditaria a `sac_ia` o especialista de `retencao_humano`.

### D5 — Lente é **declaração**, com três campos

Uma lente declara, em `@plughub/schemas` (não em comentário nem em `set[str]`):

| Campo | Responde | Exemplos |
|---|---|---|
| `aggregation` | como o bucket agrega | `sum` (token) · `avg` (duration) · `max` (ocupação) · `interval_union` (tempo em estado) · `none` |
| `emptiness` | o que faz a lente ficar **vazia** em vez de zero | sem amostra ≠ amostra valendo 0 |
| `comparability` | o que torna duas entidades comparáveis | sempre (token) · mesmo nível (duration) · mesmo formulário (quality) · **nunca somável** (capacidade) |

A UI **deriva** o comportamento: `aggregation: none` não desenha total; unidades diferentes não
compartilham eixo; `comparability` insatisfeita recusa o gráfico em vez de desenhá-lo.

O terceiro campo é o que a mesa descobriu e resolveu **uma vez**, inline. Sem ele, generalizar o modo
comparar produz gráficos sintaticamente válidos e semanticamente vazios — a classe de relatório mais
cara, porque não fica vermelho.

> **[emenda F1 — o que mudou ao declarar as 10 lentes reais]**
>
> 1. **`aggregation` é por MÉTRICA, não por lente.** Na mesma lente `sessions_aht`, `sessions` **soma**
>    e `aht_ms` **promedia**. O campo não cabe no topo, e só se descobre isso tentando declarar.
> 2. **Nasceu um quarto valor de agregação: `recomputed`.** Taxa e índice (`resolution_rate`, NPS) não
>    somam **nem promediam** — recalculam-se da população. Promediar `resolution_rate` entre dois
>    agentes de volumes diferentes dá um número que parece certo e não é; sem esse valor, `avg` seria
>    a escolha natural e errada.
> 3. **`emptiness` chama-se `evidence` no código** — é a forma operacional da pergunta: *onde vive a
>    prova de que HÁ dado*. Dois valores (`series` · `delegated`), porque só há dois em uso; um
>    `reasons`/`dimensions` genérico seria vocabulário sem consumidor, a mesma dívida que o contrato
>    remove.
> 4. **A guarda de `quality_criteria` é declarada e NÃO é executável**, e isso é achado, não descuido:
>    `form_ids` é produzido só pela lente `quality` (`reports_query.py:4613`). A guarda se declara
>    `unverifiable` e a tela diz isso — passar calado por falta de dado seria a guarda que nunca
>    reprova. O conserto é de backend e não é da F1.
> 5. **O compilador exigiu separar `LensId` de `FetchableLensId`.** `session_nps` é servida pelo
>    backend e consumida pelo painel de detalhe, mas não é plotável. A distinção vivia num comentário;
>    ao derivar `LensId` da declaração, deixou de compilar e virou tipo (`BACKEND_ONLY_LENSES`).
>
> **Onde o contrato mora — desvio medido desta fase.** Esta seção dizia "em `@plughub/schemas`".
> Medido antes de escrever: **o platform-ui não importa esse pacote** — não é dependência, não há
> alias em `vite.config.ts`, e a UI espelha tipos à mão em quatro lugares. Pôr a declaração lá a
> deixaria sem nenhum leitor, criando o órfão que a F0 acabou de caçar; e o backend é Python, que não
> importaria TS de qualquer modo. O contrato mora onde é **consumido**
> (`platform-ui/src/modules/analise/lens-contract.ts`), e a coerência com o backend virou
> **mecanismo**: a seção D de `probe_report_surface.sh` compara os ids declarados com o
> `_COMPARE_LENSES` de `reports_query.py` e reprova na divergência — nas duas direções.

**Corolários que não são negociáveis:** total fabricado é proibido onde a álgebra não o admite (o
`compute_tenant_capacity` já recusa `available` escalar no topo, e a série de capacidade lê as linhas
`__capacity_{kind}__`, **nunca** a soma por pool); e a janela de arranque sem `__capacity_*`
(`routing/main.py:1692`) tem de ser honrada pelo consumidor, não só marcada pelo produtor.

### D6 — A mesa é **modo**, não página

Comparar difere de evoluir em **uma** dimensão: uma série por entidade selecionada × uma série para a
população filtrada. Filtro, lista de entidades, lente e bucket são idênticos.

Logo `/analise/agents` deixa de ser endereço e vira o **modo comparar** da superfície Recursos.

**Previsão testável que justifica a ordem das fases:** a lente de token introduz o **terceiro** tipo de
entidade (a conta LLM, depois de agente e pool). Sem D5, ela ganha um segundo booleano `tokenLens` ao
lado do `deployLens`. *Se ao implementar o token for preciso tocar naquelas cinco condicionais do
seletor, o contrato não foi extraído.*

> **[emenda F1]** O campo `entity` entrou já na **F1**, não na F3. Ele é o que o booleano `deployLens`
> dizia, e mantê-lo para depois deixaria a previsão acima sem defesa durante toda a T3. As cinco
> condicionais do seletor passaram a ler `lensDef.entity === 'pool'` — uma segunda lente por-pool, ou
> a lente de token (entidade = conta), entra sem tocar em nenhuma delas. O que **fica** para a F3 é o
> terceiro membro da tripla da D6: a **forma do gráfico**, hoje ainda cascata de `if`. O probe declara
> isso na seção "não coberto ainda", para não parecer resolvido.

### D7 — Duas superfícies, uma regra de sobrevivência, e a lista de morte é a tabela do gate

**Superfície A · Contatos** (demanda) — hospedada em `/analise/sessions`. Filtro: intervalo + pool.
Níveis: `journey > session > segment`. Lentes: lista · volume · duration · resource usage · token ·
disposição.

**Superfície B · Recursos** (oferta) — endereço novo. Filtro: intervalo + pools. Entidade: recursos.
Lentes: resources · availability · occupancy · usage · token. Modo: comparar (D6).

> **Regra:** uma página só sobrevive se sua **unidade de análise** não for `journey|session|segment`
> nem `recurso`. Se for, ela é lente, nível ou modo — não endereço.

A regra gera a lista; a lista não é preferência.

**Morre de vez** (sem destino, sem dependência):

| Alvo | Linhas |
|---|---|
| `AnaliseComparacaoPage.tsx` | 553 |
| `AgentReportsPage.tsx` + módulo `agent-reports/` | 482 |
| `AnaliseAgentesPage.tsx` + rota `/analise/agents-legacy` | 469 |
| `ProcessosPage.tsx` + rota `/flow/processos` | — |
| `/contacts/events` (endereço duplicado) → redirect para `/analise/events` | — |

`/flow/processos` morre por contradizer a **D2** do `adr-historico-unificado-duas-visoes` (*processo é
pivô, nunca navegação livre*) — e já estava fora do menu.

**Endereço morre, componente é re-hospedado:**

| Endereço | Vira |
|---|---|
| `/analise/agents` (1.590) | modo comparar da superfície B |
| `/analise/pools` (781) | lentes da superfície B |
| `/analise/wrapup` (267) | lente de **disposição** no nível segmento da superfície A |
| `/analise/surveys` (332) | **drill** de `/analise/customer-voice` |

**Fica** (unidade de análise diferente): `quality` (avaliação) · `customer-voice` (sinal) · `customers`
(cliente — tem ADR próprio, [`adr-customer-360-two-surfaces.md`](adr-customer-360-two-surfaces.md), e
fica **fora do escopo** desta revisão) · `events` (Arc 12, categoria hierárquica) · `audit` ·
`dashboards` · **todo o Monitor**, que mede *agora* e não *período*.

**Saldo:** `/analise/` sai de 10 endereços para 6 (5 sobreviventes + Recursos); ~1.500 linhas apagadas.

---

## 3. Consequências aceitas

- **A série de token começa numa data declarada.** Não há backfill: o consumo anterior ao produtor não
  existe em lugar nenhum. Encolher a série é o esperado, não sintoma.
- **Editar a lente passa a exigir tocar a declaração**, e não mais um branch de render. É o ponto.
- **Duração no nível journey muda de unidade.** Não há duração de journey armazenada;
  `max(closed_at) − min(started_at)` sobre a componente inclui os dias parados entre contatos. É *lead
  time* do processo, legítimo, e o eixo salta de minutos para dias — **rótulo próprio obrigatório**.
- **Média de duração exclui elementos ainda abertos**, o que enviesa para baixo no bucket mais recente:
  ou o último bucket é marcado como parcial, ou a lente declara "só fechados".
- **O total do dia no drill de recurso não pode reusar `available_ms`.** Em
  `/reports/agent-availability` (`reports.py:1074`) ele é `logged_ms − pause_ms` e **inclui o tempo
  ocupado** — não bate com a soma das raias do gráfico da mesma feature. Ou recomputa da união, ou o
  campo antigo é renomeado para `unpaused_ms`.

## 4. O que NÃO fazer

- **Não reescrever `AgentsBenchPage.tsx` antes de D5.** O resultado é um arquivo mais bonito com os
  mesmos seis pontos de edição por lente.
- **Não usar o contador do `AccountSelector` como fonte de metering.** `record_usage` grava
  `ai_gw:{provider}:{key_id}:tpm` com `EXPIRE 60` (`account_selector.py:223`) — janela deslizante de
  rate-limit, *lossy por projeto*. É o atalho mais tentador e o errado.
- **Não somar linhas de capacidade por pool** para obter "total disponível" — o mesmo recurso conta uma
  vez por pool (Defeito C), e humano × IA não se substituem.
- **Não somar segmentos para obter duração** de sessão ou journey; a duração vem do próprio elemento.
- **Não emitir `pool_id` no evento de consumo no lugar de `segment_id`** (D1): perde o drill e o
  `deploy_version`.
- **Não estender o modelo para "pool chamador"** — avaliado e descartado em 2026-08-28: a proveniência
  cross-sessão é `sessions.origin_session_id`, que é **sessão→sessão, não segmento→segmento**, e o
  `sessions.pool_id` é o de entrada. O objetivo real (recursos por contato e pico simultâneo) é atendido
  pela D4 **sem campo novo**. Reabrir exigiria carimbar `origin_segment_id` no spawn.

> **[emenda F2 — o que a construção da superfície A mediu]**
>
> **1. O predicado da lista virou compartilhado, e essa era a fase inteira.** A D7 descrevia
> lentes ao lado da lista; o que ela não dizia é que a lente e a lista podem responder sobre
> **populações diferentes** sob a mesma barra de filtro. Os endpoints existentes
> (`/reports/timeseries/{volume,handle_time}`) aceitam **um** recorte — `pool_id`, que é o pool
> de ENTRADA (D10) — contra os **doze** campos da barra. Pendurar a lente neles daria "lista
> mostra 12, gráfico mostra 300", ambos certos, sem nada dizendo qual foi respondido.
> A saída foi `_session_conditions`, extraída de `_fetch_sessions`: **uma expressão, dois
> consumidores**, a mesma forma que a F4 do `adr-historico-unificado-duas-visoes` deu à direção
> do acesso. Os endpoints antigos ficam — têm chamadores vivos e contrato mais simples.
>
> **2. O filtro de canal da lista de contatos NUNCA funcionou.** Achado ao construir a série, e
> anterior a ela: a condição era `EXISTS (SELECT 1 … WHERE tenant_id = s.tenant_id …)` —
> subconsulta **correlacionada**, que o ClickHouse 23.8 recusa com o código 47, e que está
> documentada como não-suportada **200 linhas abaixo, no mesmo arquivo**. A query inteira
> levantava, o `except` do wrapper devolvia `data_unavailable` com `data: []`, e o endpoint
> respondia **200 com zero linhas**: o seletor não filtrava, ele **esvaziava**. Medido: 398
> sessões `webchat` na instalação, `channel=webchat` devolvia 0 — e **683 testes não notavam**.
> Conserto: `_CHANNEL_EXPR`, que é a MESMA string que a coluna `channel` da listagem devolve.
>
> **3. `honors` é o quinto campo do contrato de lente, e nasceu de uma lente real.** A
> disposição (ex-`/analise/wrapup`) agrega sobre pools **internos** (`-int`), onde o filtro de
> pool da barra não se aplica. Sem declarar isso, a tela exibiria uma barra de filtro que não
> filtra — a mentira mais barata desta superfície, e que não fica vermelha em lugar nenhum.
> Entrou junto `source` (`agents_compare | contacts_series | own`), por exigência do **gate**: a
> seção D compara os ids do contrato com o `_COMPARE_LENSES` da mesa, e sem o campo cada lente
> da superfície A a faria reprovar **pelo motivo errado**.
>
> **4. As ausências são CONTADAS, não omitidas — e o primeiro número justificou o campo.** A
> lente de duração exclui **510 de 881** contatos (58%, sem `handle_time_ms`); a de recursos
> perde **20** (contato abandonado antes de qualquer agente) e **clampa 4** segmentos que nunca
> fecharam em contatos já encerrados (o Problema 34). As três contas fecham: `371 + 510 = 881` e
> `861 + 20 = 881`. Sem os números no `meta`, o gráfico de duração apresentaria a duração
> "típica" de 42% da população como se fosse a de todos.
>
> **5. Segmento sem fim é fechado no fim da SESSÃO, nunca em `now()`.** Com `now()`, o pico
> simultâneo desses contatos **cresceria todo dia sem que nenhum evento acontecesse**. Em contato
> ABERTO o `now()` é o certo — aquele recurso está ocupado agora.
>
> **6. Três defeitos de instrumento, encontrados pela própria bateria de mutação:**
> · o teste de alias de agregado media *"todo alias tem sufixo"* quando o defeito é *"o alias
>   sombreia a coluna que o agregado lê"* — reprovou `count() AS handoffs`, que não sombreia nada
>   (irmão do caveat da D14.1: instrumento falseável medindo a proposição vizinha);
> · o probe **rebaixava um vermelho confirmado a INCONCLUSIVO** quando uma seção posterior não
>   conseguia medir. Hoje o `inconclusive()` verifica `FAIL` antes de sair;
> · nenhum mock reproduz `ILLEGAL_AGGREGATION` (184) nem o código 47 — a suíte inteira ficava
>   verde sobre queries que não rodam. Quem os pega é a asserção sobre a FORMA do SQL, e a
>   **seção F** do probe, que compara `Σ sample` da série com `total_contacts` da lista contra o
>   ClickHouse da instalação (4 filtros, todos com população não-vazia — testemunha de presença
>   obrigatória: concordância entre dois zeros não é evidência de nada).
>
> **7. `AnaliseTab.tsx` saiu da lista de dívida.** Órfã desde antes desta fase, era a única
> consumidora viva daqueles dois endpoints na área de contatos e agregava KPIs **no cliente**
> sobre um `FETCH_LIMIT = 1000` — um total que parava de crescer aos mil sem dizer nada. Dívida
> de órfãs: **18 → 17**.
>
> **8. Decoração escondendo dado.** A animação do recharts desenha a linha por `stroke-dasharray`
> e **não renderiza os pontos enquanto anima**: o bucket isolado de 21/08 (47 contatos, cercado
> por dois dias sem contato) não aparecia em tela alguma. `isAnimationActive={false}`. Junto
> disso, o eixo passou a ser **preenchido**: o `GROUP BY` não devolve dia vazio, então 21 e 24 de
> agosto ficavam adjacentes como se fossem consecutivos, e os dois dias sem contato sumiam em vez
> de virarem queda. Bucket ausente entra com valor `null` e `connectNulls={false}` — **buraco,
> nunca zero**.
>
> **Fica aberto, nomeado:** estender `/reports/wrapup-summary` aos demais filtros da barra (é o
> que tiraria o `period_only` da disposição), e o `interval` da série ser derivado da janela em
> vez de escolhido — dar um controle de granularidade ao lado do seletor de período criaria duas
> formas de dizer a mesma coisa.

> **[emenda T3 — a lente de token, e o que ela obrigou a separar]**
>
> **1. Duas perguntas, dois joins — e trocá-los é o erro silencioso desta fase.** A
> SÉRIE responde *"quanto este contato custou"* e junta por **`session_id`**: todo evento
> tem sessão, e exigir `segment_id` ali descartaria em silêncio o consumo de qualquer
> caminho que ainda não propague a chave, **subestimando o custo**. O BREAKDOWN responde
> *"quem gastou"* e junta por **`segment_id`** (D1), porque o `pool_id` da SESSÃO é o de
> ENTRADA (D10) — creditar por ele daria o gasto do especialista de IA ao pool onde o
> contato começou. Os dois devolvem número, e nenhum fica vermelho se forem trocados.
>
> **2. A época é PREDICADO num e RÓTULO no outro.** O breakdown corta em
> `USAGE_ATTRIBUTION_EPOCH` (agrupar por atribuição não pode misturar *"não media"* com
> *"não informado"*); a série não corta — `session_id` sempre viajou. Entrou um segundo
> nome, `USAGE_PRODUCER_EPOCH`, para o fato de **não haver linha nenhuma** antes do
> produtor. As duas têm o mesmo valor hoje, e é justamente por isso que precisam de
> nomes separados: fundi-las tornaria impossível explicar a série no dia em que uma
> mudar.
>
> **3. A época com granularidade de DIA voltou a mentir — no segundo leitor.** Ela
> acusou **8 defeitos que não existem**: eventos emitidos horas antes de a coluna
> existir são pós-época pelo calendário e história pelos fatos. O discriminador é
> **ordem, não data** — exatamente a correção que a seção C do `probe_llm_call_paths.sh`
> já precisara fazer na T2, pelo mesmo motivo. Hoje o `meta` carrega dois fatos:
> `unattributed_events` (quantos) e `unattributed_in_flight` (se ainda chegam). Publicar
> só a contagem seria um número que parece resultado.
>
> **4. Não existe `tokens_total`, e a ausência é decisão.** Entrada e saída têm preços
> diferentes em todo provedor: somá-las daria o número mais fácil de publicar e o menos
> utilizável — mesma família da soma de licença humana com licença de IA que a admissão
> recusa. `tokens_per_contact` está ao lado das somas porque responde outra pergunta: um
> bucket pode ter o dobro de tokens por ter o dobro de contatos, ou por os contatos
> custarem o dobro.
>
> **5. A nota da época se contradizia na tela.** A primeira redação dizia *"a série
> começa em 28/08"* e havia ponto no dia 27 — porque o bucket é do **contato**
> (`opened_at`), não do evento. O que a data significa é *"nada foi REGISTRADO antes de
> X"*. Texto que descreve o eixo errado é da mesma família do DDL que promete ordenação.
>
> **6. Testemunha negativa, agora que há produtor.** Seção D do
> `probe_llm_call_paths.sh`: contato que não chamou LLM **não pode ter linha**, nunca uma
> linha valendo 0 — que na lente viraria *"usou IA e gastou zero"*, indistinguível de
> quem usou e é barato. A nota *"T3 não coberto"* do outro probe saiu junto: ela deixou
> de ser verdade no instante em que a testemunha passou a existir.
>
> ⚠️ **ACHADO COLATERAL, FORA DO ESCOPO DESTA FASE E MAIS GRAVE QUE ELA.** Ao inventariar
> os leitores de `usage_events`, medido: **12 das 38 rotas `/reports/*` não declaram
> principal algum**. Quatro verificadas ao vivo respondem **200 sem credencial** —
> `/reports/usage`, `/reports/evaluations`, `/reports/agent-events/summary` e
> `/reports/customers/{id}/360` —, enquanto `/reports/sessions` e `/reports/segments`
> respondem 401. Não foi consertado aqui: são 12 rotas com consumidores próprios, e
> cada uma precisa da sua medição. **É a regra do CLAUDE.md recorrendo:** o
> `probe_authz_single_verifier` conta *quem decodifica JWT*, não *quais rotas exigem
> um* — "um censo desenhado para um eixo não prova nada sobre o eixo vizinho". Falta o
> censo do terceiro eixo: COBERTURA de rota.

## 5. Fases

| Fase | O quê | Depende de |
|---|---|---|
| **F0 ✅** | Apagar os 5 alvos, `probe_report_surface.sh`, correções de doc | **nada** |
| **F1 ✅** | Contrato de lente (D5+`entity` da D6), consumido pela mesa; seção D do gate | — |
| **F0b ✅** | 5 páginas de relatório com rota `Navigate` + 2 de cascata; resolvedor de imports no gate | F0 |
| **F2 ✅** | Superfície A em `/analise/sessions`; absorve wrap-up | F1 |
| **F3 ✅** | Superfície B + mesa como modo (D6); absorve pools | F1 |
| **F4** | `customer-voice` absorve `surveys` | F1 |
| **T0 ✅** | **Medir** chamadas LLM por caminho; gate `probe_llm_call_paths.sh` | — |
| **T1 ✅** | Produtor nos 4 caminhos vivos (`source` obrigatório); validado ponta a ponta | T0 |
| **T2 ✅** | `segment_id` + conta + modelo no evento e nas colunas (D1–D3); época declarada | T1 |
| **T3 ✅** | Lente de token — superfície A entregue; **a metade B é da F3**, onde a entidade deixa de ser o contato | T2 + F1 |

**F0 é independente e vale sozinha** — não espera contrato, ADR nem token, e é ela que faz a tabela do
gate existir pela primeira vez. **F1 antes de F2/F3 não é preferência**: é a previsão da D6.

> **[emenda T0 — a medição mudou o alvo do produtor]**
>
> A T1 estava descrita como *"emitir do `/v1/reason`"*. Não basta: **12 de 28 chamadas (42%) vêm do
> `sentiment_analyzer`**, que é uma chamada dedicada disparada **de dentro** do `/v1/reason` e **não
> tem rota própria**. Um produtor ligado ao HANDLER perderia essa fatia inteira, e perderia **calado**.
> O emissor fica no site que fala com o provider, não na rota.
>
> **Quatro caminhos vivos** a cobrir: `/v1/reason` · sentiment · `/v1/copilot/analyze` (chamador em
> `mcp-server-plughub/src/server.ts:3394` — volume zero é **ambiente ocioso**, não rota morta) · sonda
> de credencial do boot.
>
> **Duas rotas mortas**, sem chamador algum em `packages/`: `/v1/turn` e `/inference` — e esta última
> abriga a **única** emissão que a plataforma tem hoje. Daí uma ordem inegociável: **não apagar
> `/inference` antes da T1**, sob pena de remover o único emissor existente.
>
> **Decisão que a T1 tem de tomar explicitamente:** a sonda de boot consome tokens reais (o provedor
> cobra) e não tem sessão nem segmento. Ou emite com `session_id: null` e marcador de origem, ou não
> emite — mas **declarado**. Em silêncio, a soma do relatório nunca bate com a fatura e ninguém
> descobre por quê. É também a prova de que a chave de atribuição da D1 precisa ser anulável.
>
> **Caveat de método que o probe carrega:** 28 chamadas em 9 dias é ambiente ocioso, e volume zero
> **não** distingue *"caminho morto"* de *"ninguém usou a plataforma"*. Quem separa os dois é o
> chamador, resolvido estaticamente — por isso ele é coluna da tabela declarada, não nota de rodapé.


> **[emenda F3 — o as-built, e as quatro coisas que a medição mudou]**
>
> A F3 saiu em quatro fatias, cada uma verificada contra dado real: **F3a** a forma do
> gráfico vira declaração · **F3b/c** a Superfície B nasce e a mesa vira modo · **F3d** a
> lente de token do lado da oferta.
>
> **1. A previsão da D6 se cumpriu, e o teste dela foi barato.** *"A lente de token
> introduz o terceiro tipo de entidade; se for preciso tocar naquelas cinco condicionais
> do seletor, o contrato não foi extraído."* Não foi preciso: `entity: 'account'` entrou
> sem tocar em nenhuma delas, porque desde a F1 elas leem `lensDef.entity === 'pool'`.
>
> **2. Duas garantias estavam ESCRITAS e não existiam** — as duas minhas, ambas
> desfeitas por sonda de tipo, não por revisão:
>
>   · o comentário do `SessionsPage` dizia que uma lente de contato com forma nova *"não
>     compila"*. `const _t: number = shape` respondeu `Type 'string'`:
>     `REPORT_LENSES.filter(l => l.entity === 'contact')` colapsa os literais, e o
>     `assertNever` não reprovava nada. Consertado com predicado de tipo
>     (`byEntity`/`bySource`/`byChart`) — e só então a mutação (lente de contato com
>     `chart: 'criteria_heatmap'`) passou a quebrar o build;
>   · o cabeçalho da `ResourcesPage` declarava a partição da URL entre hospedeiro e
>     hospedado **e reusava o nome `mode`** nos dois lados (`evolve|compare` na
>     superfície, `daily|epoch` no toggle de deploy). Trocar de lente no modo comparar
>     apagava `mode=compare`; o reload caía no modo evoluir, mostrando outra tela sem
>     erro. O parâmetro da mesa virou `deploy`, e o redirect de `/analise/agents`
>     **renomeia** o legado antes de carimbar `mode=compare`.
>
> Regra que fica: **partição de namespace declarada em prosa não é partição.** É a mesma
> família do DDL de `participation_intervals`, agora dentro deste arco.
>
> **3. A lente de token da B não podia reusar o endpoint da A**, e a diferença é de
> POPULAÇÃO, não de `group by`. Medido antes de escrever: 20 eventos / 1 991 tokens no
> período, dos quais **8 eventos / 945 tokens** têm sessão em `sessions`. O breakdown da
> A faz `INNER JOIN` com as sessões filtradas — de propósito, porque vive sob a mesma
> barra que o gráfico dela. Reusá-lo publicaria **47% do consumo**, em silêncio. Daí
> `GET /reports/resources/tokens`, sobre `usage_events` inteiro, e o campo
> `meta.population` para que ninguém compare os dois números sem perceber que são duas
> perguntas. A rota **recusa `?pool_id=` com 422** em vez de ignorá-lo: o gasto de uma
> conta é do tenant, e devolver o total sob o rótulo de um recorte é a mentira que o
> `honors` do contrato existe para impedir.
>
> **4. A época mentiu pela TERCEIRA vez, e desta vez fica declarada em vez de
> consertada.** O contador de "sem conta" acusava 8 eventos como defeito vivo de
> propagação; medidos, são `t1-verify-B`/`t1-verify-C` — as sessões de verificação da
> própria T1, de 20:33 e 20:37, enquanto o primeiro evento COM conta é de 20:59.
> `USAGE_ATTRIBUTION_EPOCH` tem granularidade de **dia** e o corte é de **instante**.
> Não converti a constante para `DateTime`: o único instante disponível seria escolhido
> **olhando estes dados**, que é a definição de fitting, e ela é compartilhada com outro
> leitor. O número passou a ser publicado como **teto** do defeito, com o porquê na tela,
> e o limite está escrito onde a constante mora.
>
> **O que a F3 achou e não era dela** — dois defeitos vivos, ambos de fases anteriores:
>
>   · **a mesa mostrava seis botões escritos `bench.lens.list`, `bench.lens.volume`, …**
>     — a chave crua de i18n. Ela fazia `LENSES = REPORT_LENSES`, e a F2, ao acrescentar
>     as lentes de contato à declaração, fez a mesa oferecer lentes que o
>     `/reports/agents/compare` não conhece. Conserto pela FONTE (`COMPARE_LENSES` = o
>     que a mesa sabe pedir), não pela entidade;
>   · **o combo de pool da `/analise/pools` não é o problema** — o meu era: a primeira
>     versão da `ResourcesPage` lia `/v1/pools` como array quando o corpo é
>     `{pools:[...]}`, e o seletor ficava vazio enquanto a URL levava `?pool=sac_ia` e o
>     painel filtrava certo. Só o seletor mentia.
>
> **De-para das lentes da B.** A D7 lista *"resources · availability · occupancy · usage
> · token"*. Aquela lista foi escrita antes de medir o que existia; ao abrir a página há
> quatro painéis com fontes distintas. O as-built mantém os nomes do que EXISTE —
> `pool_volume` (usage) · `pool_queue` · `pool_occupancy` (occupancy) · `pool_sla` ·
> `account_tokens` (token) —, e `availability` continua sendo lente da MESA, por agente,
> que é onde ela pertence: disponibilidade é fato de recurso, e o recurso comparável é o
> agente, não o pool. Renomear quatro painéis para caberem numa lista indicativa seria
> fazer a documentação descrever um sistema que não existe.
>
> **Gate:** seção **G** do `probe_report_surface.sh` — conta comparação por ID DE LENTE
> nas telas de despacho, com controle positivo (≥ 6 `case` de forma) para que um parser
> que deixou de casar não saia verde. Ela nasceu acusando uma linha dentro de `{/* … */}`
> que EXPLICA a cascata removida, o que deu `_strip_comments.py` — a terceira varredura
> deste repositório a confundir prosa com código.

## 6. Gate — o que o faria ficar vermelho

`infra/test/probe_report_surface.sh`, no molde do `probe_edge_surface.sh` (que **declara** a
classificação dos prefixos e reprova prefixo novo sem linha na tabela):

1. **Rota de relatório sem linha no de-para da D7** → vermelho.
2. **Arquivo de módulo sem import** → vermelho. **[emenda F0b — a contagem abaixo também estava errada,
   e o mecanismo é que estava.]** A checagem casava *basename*, o que tem duas classes de erro, ambas
   presentes na árvore: falso negativo por colisão (`campaigns/CampaignsPage` escondida pela homônima
   viva em `evaluation/`) e, ao corrigir com caminho, falso positivo em `index.tsx` (import de
   diretório) e em irmãos (`./Base`). Hoje quem decide é a **resolução do especificador de import**
   (`infra/test/_ui_orphans.py`). Linha de base real: **18**, e **nenhuma é página de relatório** — a
   F0b esgotou essa família removendo as 5 cujo `Navigate` entrara no Arc 19, mais 2 de cascata
   (`WorkflowsPage`, `campaigns/CampaignsPage`), 1.706 linhas. Lição de método que fica: **a contagem
   de órfãos é limite inferior até que se apague de fato** — cada rodada revela a próxima, porque
   código morto segura código morto.
   <!-- Redação anterior, preservada para mostrar o erro que a medição desfez: -->
   ~~**Linha de base MEDIDA na F0 (2026-08-28), e ela corrige
   este ADR:** a §1.1 contou 2 órfãs porque mediu só a área de relatórios; varrendo `platform-ui/src/modules`
   inteiro são **10 páginas órfãs** (mais 8 componentes). A F0 remove 2; as **8 restantes ficam declaradas
   como dívida que não pode CRESCER** — órfã nova reprova. **Cinco delas são de relatório/monitoração**
   (`AgentFlowMonitorPage` · `AgentFlowReportPage` · `MonitorJourneysPage` · `WorkflowMonitorPage` ·
   `WorkflowReportPage`, 816 linhas) cuja rota já é `Navigate` desde o Arc 19: o redirect entrou, o arquivo
   nunca saiu. São F0b por mérito próprio, sob a mesma regra da D7.~~ **← feito na F0b.**
3. **Rota roteada e ausente do `Sidebar.tsx`** → vermelho, salvo linha explícita de exceção.
4. Para a lente de token, **testemunha negativa**: sessão **sem** chamada LLM tem de produzir **zero
   linhas** em `usage_events`, não uma linha valendo 0 — a lição do `first_queued_ms`, em que todo
   contato roteado direto emitia espera fantasma de 0 ms. Ao lado, testemunha de presença, senão um
   produtor que nunca emite passa.
5. Lente declarada sem os três campos da D5 → falha de tipo em build.

## 7. Dívidas nomeadas (não bloqueiam)

- **`/evaluation/reports` sem `RequireEvalAccess`**, ao contrário de todos os irmãos, e fora do menu.
  Achado colateral; decisão de dono, fora deste ADR.
- **Raia `busy` no drill do dia**: `agent_login_intervals`/`agent_pool_intervals`/`agent_pause_intervals`
  existem e `/reports/agent-timeline` já as devolve; **`busy` não tem tabela de intervalo** e viria de
  `segments`. Numa linha do tempo os quatro estados são mutuamente exclusivos num instante, então a
  partição é honesta — o problema é só o agregado (ver §3).
- **Não existe série de ocupação média**, só de pico. A lente se chama pico, ou é preciso produtor novo.
- **Correções de documentação que acompanham a F0**: `CLAUDE.md` § Arc 8 (aponta
  `/contacts/reports/agents`, rota inexistente) · docstring do `MetricSelector.tsx:9` · a menção no
  `CLAUDE.md` a `AnaliseComparacaoPage` como consumidora do `MetricSelector`.
- **`SessionMetricsExtractor` NÃO é órfão** — o `CLAUDE.md` afirma que ele e o
  `fill_auto_computed_criteria` "nunca são chamados"; estão ligados em
  `evaluation-api/router.py:1632-1641`. Ele lê `llm_calls`/`tokens_input_total`/`tokens_output_total`
  por sessão (`session_metrics_extractor.py:283-285`) e degrada com honestidade quando a tabela não
  existe — ou seja, **critérios `auto_computed` de token estão silenciosamente vazios hoje**, pela mesma
  causa raiz da §1.2. É o terceiro consumidor à espera do mesmo produtor.
