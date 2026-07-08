# Retorno do conceito de Journey sob o modelo de 3 níveis — Design

> **Estado:** design fechado no essencial (associação), pré-código. 2026-07-08.
> **Decisão de associação:** **D1.5** — journey = componente conexa de sessões sob (proveniência ∪ alias),
> identificada pela **raiz canônica** (valorada em `session_id`, resolvida por union-find na leitura).
> **Não** reintroduz a entidade `Journey` do Arc 10 (sem lifecycle transacional, sem topic `journey.events`,
> sem merge/split governado). Relação com Arc 19 Fase F em §7.
> **Documentos-base:** `business-in-any-media-arquitetura-alvo.md`, `identity-resolver-nivel-b-spec.md`,
> `delegate-contrato-por-pool-spec.md`, `arc19-unified-session-model.md`, `arc5-segments.md`,
> `customer-contact-history.md`.
> **Diagrama:** `journey-3-cenarios-unionfind.svg` (três cenários de associação + resolução canônica).
> **Spec de implementação:** [`journey-3-niveis-implementation-spec.md`](journey-3-niveis-implementation-spec.md).

---

## 0. Nota de nomenclatura (crítica)

Os specs de "Business in Any Media" rotulam os níveis por letra, com o **negocial no topo = (a)**. Este
documento usa **N1/N2/N3** casando com o vocabulário do produto:

| Este doc | Letra no spec | Nível | Responsabilidade | Perfil / infra |
|---|---|---|---|---|
| **N3** | (a) | Negocial | Lógica de negócio, abstraída de canal | perfil `workflow`, pool `webhook` |
| **N2** | (b) | Acesso aos canais | Concilia trocas de canal, negocia mídia, resolve identidade | perfil `agent` + Arc 16 |
| **N1** | (c) | I/O no canal | Render nativo, captura de input, mídia | perfil `agent` + Channel Adapter |

Regra de ouro (mantida): *"N3 nunca sabe por onde fala; N1 nunca conhece o negócio; N2 traduz entre os dois e
é o único que entende 'canal'."* **Ao abrir os SVGs canônicos, lembre que eles usam as letras invertidas.**

---

## 1. O problema e a tese

A entidade `Journey` (Arc 10) agrupava N contatos de um processo de longa duração. Foi **eliminada** no Arc 19
Fase F (2026-05-28): a unificação de sessão elegeu o `session_id` persistente + status `suspended` como espinha
da execução, e ditou "rastreabilidade via `parent_session_id`, sem entidade separada". A capacidade multi-contato
**migrou de camada** — renasceu como o resolvedor de identidade em N2 (`customer_id` → `pending_by_customer`).

Com o modelo de 3 níveis, o conceito **volta como requisito de produto** (ver um caso ponta-a-ponta, unir threads,
capturar a entrada real), mas **não como a entidade Arc 10**. A tese:

> **Uma journey é a componente conexa de sessões sob (arestas de proveniência ∪ arestas de merge), identificada
> pela sua raiz canônica.** A raiz é valorada em `session_id` (não há id opaco `jrn_…`). Sem arestas de merge,
> journey ≡ raiz transitiva de `origin_session_id` (o modelo de hoje). As arestas de merge são a **única** coisa
> que a derivação por proveniência não expressa, e a razão de existir uma camada explícita.

### 1.1 Por que D1.5 e não D1 (derivação pura) nem D2 (entidade)

- **D1 (derivar `root_session_id` de `origin_session_id`)** é limpo mas subdimensionado: proveniência é imutável,
  então (i) não captura a sessão de intake do inbound sob a journey pendente sem descartá-la (cenário 3, o caso
  **comum**); (ii) nunca unifica dois processos nascidos separados (cenário 2-unify); (iii) não oferece correção.
- **D2 (reviver a entidade)** reintroduz exatamente o que o Arc 19 removeu (lifecycle, topic, merge/split).
- **D1.5** mantém a rastreabilidade derivada do trace (espírito do Arc 19) e adiciona só a união de árvores:
  uma coluna denormalizada + uma tabela de arestas + resolução na leitura. Cobre os três cenários.

---

## 2. Modelo de associação

### 2.1 Os três identificadores

| Campo | Semântica | Mutável? | Origem |
|---|---|---|---|
| `session_id` | Espinha da sessão (Arc 19) | Não | Core, no nascimento |
| `origin_session_id` | **Proveniência** — quem me disparou (topologia estrela, Arc 19) | Não | born-with, propagado no `delegate`/`collect` |
| `root_session_id` | **Raiz local** da minha árvore de proveniência (raiz canônica **antes** de merges) | Não | param na largada, ou auto-mint = `self` |
| `journey_aliases(child_root → canonical_root)` | **Arestas de merge** — une duas árvores | Aresta é append/reversível | ato de fluxo `journey_merge` |

A **journey canônica** de uma sessão = `find(root_session_id)` sobre o grafo union-find `journey_aliases`
(path-compressed na leitura). A **fonte de verdade** é `root_session_id` + `journey_aliases`; a coluna dormente
`sessions.journey_id Nullable` no ClickHouse é reaproveitada como **cache eventualmente consistente** do canônico
(escrito no `session_opened` e refrescado no merge das sessões afetadas). Reads em v1 resolvem por union-find;
adotar o cache depois é só trocar a query, sem backfill — e a correção nunca depende do cache até confiarmos nele
(ver §9.2).

**Invariante central:** `root_session_id` é **imutável e nunca null**. Merge nunca reescreve `root_session_id`;
só escreve uma aresta. Isso preserva idempotência e elimina o tratamento de null em todo o caminho de leitura.

### 2.2 Regra de nascimento (premissa 1, ajustada para "nunca null")

Todo skill-flow, ao iniciar, resolve `root_session_id` assim:

1. **Recebeu o parâmetro `journey`** (via `context` do `delegate`/`collect`, ou `$.config` do `PoolSkillSlot`)
   → `root_session_id = <recebido>`. *(É a propagação top-down; o filho herda a raiz do pai.)*
2. **Não recebeu** → `root_session_id = self` (a `session_id` da própria sessão). *(Journey de um — caso
   degenerado uniforme; vale para workflow N3 e para contato N1/N2.)*

Não há ramo "deixa null". Uma journey de um contato é apenas uma componente de tamanho 1.

### 2.3 Merge (premissa 2, como alias — premissa 3: comandado pelo fluxo)

`journey_merge(source_root, canonical_root)` escreve **uma aresta** `source_root → canonical_root`. O fluxo
**nomeia a sobrevivente** (normalmente a journey pré-existente / mais antiga). Guardas: resolver ambos os lados a
suas raízes antes de ligar (evita ciclo); ligar sempre a não-canônica → canônica. Reversível (remover a aresta).

- `root_session_id` de cada sessão **permanece o que era** — só a resolução canônica muda.
- Sessões em voo que ainda nascem sob a raiz antiga são canonicalizadas na leitura → sem corrida.
- Merges encadeados (`a→b→c`) resolvem por transitividade.

### 2.4 Hierarquia derivada

`journey → [session → [segment]]` é **resultado de query**, não estrutura armazenada:

```
sessions WHERE canonical(root_session_id) = J   ORDER BY started_at
  └─ segments (herdados por JOIN em session_id)  ORDER BY started_at
```

Segmentos herdam a journey por join em `session_id`; não são re-carimbados no merge.

---

## 3. Os três cenários (fechados)

**Cenário 1 — top-down, bem comportado.** N3 nasce com `root_session_id = self` (auto-mint). Cada `delegate`/
`collect` propaga `journey = root_session_id` no `context`; filhos nascem carimbados. **Sem merge.** Associação
por construção.

**Cenário 2 — journey criada no meio de um segmento.**
- Se o segmento que dispara **já tem** raiz → passa a própria `root_session_id` como param ao novo workflow;
  o workflow nasce dentro da mesma journey. **Sem merge.**
- Se você quer **unir dois processos já populados** (ex.: carrinho aberto + troca em andamento que se revelam o
  mesmo caso) → `journey_merge` (uma aresta). Este é o único gatilho real de merge.

**Cenário 3 — inbound antes de saber (o caso comum).** A sessão de intake nasce com `root_session_id = self`
(não é workflow, mas a regra "nunca null" a torna raiz de si mesma). O intake (N2/identity) resolve
`customer_id` → consulta `pending_by_customer` (que passa a carregar a `root_session_id` da journey pendente no
`PendingEntry`). Achou pendência → o fluxo comanda `journey_merge(intake_root → pending_root)`. **A sessão de
intake fica visível sob a journey** (a journey dela foi fundida) — sem descartar o toque de entrada e sem a
decisão 3a/3b. Não achou → segue avulsa (journey de um) ou dispara N3 (cenário 2).

O resolvedor de identidade em N2 é o dono natural da resolução: já mantém `pending_by_customer`; basta o
`PendingEntry` carregar `root_session_id` além de `session_id`/`resume_token`.

---

## 4. (a) Como MOSTRAR os 3 níveis

**Uma superfície única com zoom hierárquico**, não três telas. É o drill-down atual (`sessions → segments`)
estendido **um nível para cima**: `journey → [session (episódio)] → [segment (turno)]`.

- **Vista Processo (N3)** — "card de journey": uma linha por journey canônica. Colunas: skill negocial
  (o `pool.skill` webhook que define o processo), status agregado (`active`/`suspended`/`closed` derivado dos
  membros), **duração de negócio** (`SUM(segment.duration_ms)`, exclui tempo suspenso — Arc 19 já usa isso p/ TMA
  webhook), nº de episódios, canais tocados, funnel de etapas. É a reencarnação da `ProcessosPage`, alimentada por
  derivação. Motor visual: o **`SessionTrace`** do Arc 19 (já reconstrói a árvore `task`/`origin_session_id`),
  agora ancorado na raiz canônica e estendido pelas arestas de alias.
- **Vista Episódio (N2)** — expandindo uma journey: os contatos/sessões-membro, cada um com canal escolhido,
  negociação de mídia (Arc 16), outcome do `collect`/`delegate`.
- **Vista Turno (N1)** — expandindo um episódio: segmentos + render concreto no canal (commerce-cards, masked
  input), turnos, latência. É o que `analytics.segments` + `session_timeline` já mostram.

Superfícies: reaparece uma aba **Processos** (ou "Journeys") em Monitor e Analytics, ao lado de
Sessions/Pools/Agents/Events. Contatos avulsos (journey de um, sem workflow, nunca fundidos) **não** poluem a
vista — ver filtro em §6.

---

## 5. (b) Como MEDIR e AVALIAR cada nível

Cada nível tem métrica e avaliação próprias, com granularidade distinta. **A maior parte já existe** — o gap real
é o rollup N3.

| Nível | Mede (métrica) | Avalia (qualidade) | Grão / fonte hoje |
|---|---|---|---|
| **N1** (I/O) | Sucesso de render por canal, turnos, latência de resposta, taxa de captura/validação, degradação de commerce-card | **QA por segmento** (cortesia, aderência, tool correctness) | `segment` — `evaluation_finalized` grain=segment; `mv_agent_performance_daily`; `/reports/segments` ✅ |
| **N2** (acesso a canais) | Cumpriu o `InteractionRequest`? canal eleito, troca de mídia, tempo até obter resultado, **taxa de retomada cross-canal** | Qualidade do episódio: escolha de canal, esforço imposto ao cliente | `session`(filho) — `collect_events`/`segments`; "outcome do episódio" a formalizar (gap menor) |
| **N3** (negocial) | **Conclusão do negócio** (workflow → `complete`/`resolved`?), duração de negócio, nº de contatos/canais até resolver, **abandono por etapa** (funnel do skill) | Qualidade do **processo**: resolveu ponta-a-ponta? CSAT/NPS **do processo** (≠ do turno) | **journey** — hoje **ausente**; ver abaixo |

**Assimetria útil:** a avaliação hoje é *bottom-up* (por segmento). N3 pede avaliação *top-down* (do processo) —
não competem. O enum `session_signal.grain` **já reserva `journey`** (e `workflow`), então o sinal de qualidade
N3 (CSAT/NPS do processo, resolução end-to-end) pendura ali, chaveado pela raiz canônica, **sem tabela nova**.

**Regra de composição:** métrica de N inferior **não** vira métrica de N superior por soma ingênua. A journey tem
métricas próprias (conclusão, funnel, duração de negócio) derivadas dos membros; o QA de segmento continua no seu
grão. A journey *agrega* (ex.: pior sentiment observado, nº de escalações) mas *avalia* no grão dela.

---

## 6. (c) Como EXIBIR os contatos N1/N2 sob a journey N3

Dada uma journey canônica J, a Vista Processo lista todos os contatos por **duas fontes unidas** (espelha o antigo
`UNION(journey_sessions, origin_session_id)` do Arc 10, agora derivado):

1. **Por proveniência** — sessões cuja `root_session_id` resolve (transitivamente) para J. São os episódios que o
   próprio fluxo disparou (`collect`, `delegate`, sub-workflows). É a árvore estrela do Arc 19.
2. **Por merge** — sessões cuja raiz local foi fundida em J via `journey_aliases` (cenário 3: o inbound; cenário
   2-unify: o outro processo). É o que proveniência sozinha não daria.

Query conceitual: `SELECT * FROM sessions WHERE canonical(root_session_id) = J ORDER BY started_at`, cada folha
clicável até a transcrição (reusa **H1** de `customer-contact-history`, já implementado; a busca por cliente
**H2** também). Drill: **Processo → [episódios] → [segmentos] → transcrição**.

**Duas vistas complementares, mesmos filtros.** A **Vista Sessions** atual é mantida como está (todo contato,
inclusive journey de um). A **Vista Processos** é adicionada ao lado, reusando os **mesmos filtros existentes**
(`channel_type`, `pool`, `period`, `status`, `ANI`/`DNIS`). A semântica dos filtros muda de "a sessão casa" para
**"a journey tem ≥1 membro que casa"** — uma journey é multi-canal/multi-pool por natureza, então filtrar por
pool = "journeys que tocaram este pool".

**Vistas distintas por profundidade de drill (não redundantes).** Sessions e Processos não se sobrepõem porque o
drill é diferente: Sessions = `session → [segments]` (2 níveis); Processos = `journey → [sessions → [segments]]`
(3 níveis — o nível de sessão vira intermediário, revelado após o primeiro drill). Uma journey de 1 contato existe
nas duas, mas com ponto de entrada e agregação diferentes. Logo o **filtro "journey significativa"**
(`session_count > 1` OU ancorada por workflow N3 OU já fundida) é apenas um **default de UX** da Vista Processos
para reduzir volume — **não** correção de redundância. Workflow N3 com 1 sessão aparece (é processo); o filtro por
pool ameniza ainda mais.

---

## 7. Relação com Arc 19 e reúso de infra dormente

O Arc 19 Fase F removeu a **entidade** (lifecycle, `journey.events` de 9 tipos, colunas `merged_into`/`split_from`,
tabela `journeys`, `journey_types`). O D1.5 **não** as ressuscita. O que ele reacende é o **rótulo e o
encanamento** que a remoção deixou dormentes:

- `sessions.journey_id Nullable` (ClickHouse) — hoje morta; reaproveitável como **cache do canônico resolvido**.
- `session_signal.grain` já enumera `workflow|journey` + coluna `journey_id` — destino do sinal de qualidade N3.
- Namespace `@ctx.journey.*` no engine — vivo, sem alimentador; volta a ser alimentado pela raiz canônica
  (contexto compartilhado entre sessões da journey, útil ao N3).
- `agent_business_events.journey_id Nullable` (Arc 12) — permite eixo negocial por **instância** (não só por tipo
  `category_l1.l2`), carimbando a raiz canônica no evento.
- `SessionTrace` (Arc 19) — motor visual da Vista Processo.

**Coerência com o invariante do Arc 19:** rastreabilidade continua derivada do trace; a novidade (arestas de
merge) é aditiva e read-side, não uma entidade transacional com estado governado.

---

## 8. Footprint de implementação (para a spec)

**Novo:**
- Coluna `root_session_id` em `analytics.sessions` (e no evento de sessão) — resolvida no `session_opened`:
  param recebido, ou `self`. Um consumer/derivação resolve; re-emissão via `ReplacingMergeTree` cobre carimbo
  tardio se necessário.
- Tabela de arestas `journey_aliases (tenant_id, child_root, canonical_root, created_at, created_by, active)`.
- Resolução union-find na leitura (path-compressed) — na analytics-api.
- Tool MCP `journey_merge(source_root, canonical_root)` (auditável via McpInterceptor; comandada pelo fluxo).
- Parâmetro `journey` no start do skill-flow (via `context`/`$.config`) — propagação top-down.
- Endpoint `/reports/journeys` (ou `/reports/processes`) agregando por raiz canônica; filtro "significativa".
- Sinal de qualidade N3 em `session_signal` grain=`journey`.
- Aba Processos em Monitor/Analytics; `SessionTrace` ancorado na raiz canônica.

**Reaproveitado:** `origin_session_id`, `session_signal.grain`, `@ctx.journey.*`, `journey_id` dormente,
`SessionTrace`, H1/H2 de contact-history, `pending_by_customer` (+`root_session_id` no `PendingEntry`).

---

## 9. Decisões (resolvidas 2026-07-08)

1. **Direção canônica no merge.** ✅ **Journey mais antiga vence** (`min(started_at)` da raiz). O fluxo pode
   sobrescrever a sobrevivente explicitamente, mas o default é a mais antiga.
2. **Cache do canônico.** ✅ **Fonte de verdade = `root_session_id` + `journey_aliases`**, resolvido por
   union-find na leitura (v1). Em paralelo, escrever `sessions.journey_id` como **cache eventualmente
   consistente** do canônico (no `session_opened`, e **refresh no merge** — re-emissão das sessões afetadas,
   raro/limitado). Adotar o cache depois = trocar a query, **sem backfill**. A correção nunca depende do cache
   até confiarmos nele.
3. **Parâmetro de journey + manter os dois campos.** ✅ Regra de nascimento = §2.2 (recebeu param → usa;
   senão → `self`). São, na verdade, **duas** perguntas: (i) o param define `root_session_id` — confirmado;
   (ii) **mantemos `origin_session_id` (1 salto) E `root_session_id` (raiz transitiva)** — eles respondem
   coisas diferentes: `origin_session_id` desenha as arestas reais no `SessionTrace` (quem disparou quem);
   `root_session_id` agrupa. Nenhum substitui o outro.
4. **Propagação do param no delegate/collect.** ✅ `journey` = **do chamador**: `delegate`/`collect`/`task`
   propagam a `root_session_id` do chamador ao filho (top-down). *(A antiga nota "alinhamento `task → pool`" é
   ortogonal a journey — pertence a `delegate-contrato-por-pool` — e sai do escopo deste doc; só importa aqui
   que o param `journey` viaje uniformemente por qualquer step de delegação.)*
5. **`@ctx.journey.*` compartilhado.** ✅ Reacender o namespace alimentado pela raiz canônica; política de
   escrita/visibilidade herda a taxonomia de ContextStore.
6. **Filtro "journey significativa" / duas vistas.** ✅ Ver §6 — **mantém a Vista Sessions** e **acrescenta a
   Vista Processos**, com os **mesmos filtros existentes** (semântica ajustada a "journey com ≥1 membro que
   casa"). A journey de 1 contato **não** polui a Vista Processos porque já está inteira na Vista Sessions
   (divisão de trabalho, não gambiarra); o filtro por pool ameniza ainda mais.

---

## 10. Invariantes travados (resumo)

- `root_session_id` em toda sessão, **imutável, nunca null** (param na largada ou auto-mint = `self`).
- **Journey = componente conexa** sob (proveniência ∪ alias), identificada pela **raiz canônica** (valorada em
  `session_id`, resolvida por union-find na leitura).
- **Merge = aresta de alias**, comandada pelo fluxo, reversível; **nunca** reescreve `root_session_id`.
- Hierarquia `journey → [session → [segment]]` **derivada** na consulta, ordenada por `started_at`.
- "Journey significativa" é **filtro de display**, não estado.
- **Não** há entidade transacional, máquina de lifecycle, nem topic `journey.events` (distinção D1.5 × D2).
