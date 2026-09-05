# ADR: Opções em árvore no DialogForm — vocabulário controlado hierárquico (não control-flow)

**Status:** Proposto
**Data:** 2026-08-29
**Componentes:** `packages/schemas` (`dialog.ts` — `DialogOption` recursivo, `evaluateAskWhen`),
`packages/platform-ui` (editor de forms; `DialogFormRenderer` — colunas Miller),
`packages/mcp-server-plughub` (`form_get`, `segment_outcome_record`/`deriveAgentEvents`, `survey_record`),
`packages/skill-flow-engine` (`steps/menu.ts`, `steps/loop.ts`),
`packages/channel-gateway` (`survey_web.py`), `packages/orchestrator-bridge` (`main.py` — submit do webchat),
`packages/analytics-api` (`agent_business_events`).
**Relacionado:** [`adr-dialog-conditional-skip-logic.md`](adr-dialog-conditional-skip-logic.md) (guarda `ask_when`
declarativa; o form permanece linear), [`adr-otp-workflow-and-dialog-primitive.md`](adr-otp-workflow-and-dialog-primitive.md)
(as 4 costuras), [`adr-dialog-form-deletion.md`](adr-dialog-form-deletion.md) (arquivar, não apagar; leitura por id
não fecha), [`adr-survey-form-scoring-composition.md`](adr-survey-form-scoring-composition.md) (composição/NA),
`docs/arcos/arc12-agent-business-events.md`.

---

## Contexto

Os formulários de wrap-up mais úteis não são listas de opções planas: são **taxonomias** — motivo, submotivo,
detalhe — em que o operador desce níveis até uma folha. O objetivo é **padronizar a resposta**: o mesmo desfecho
sempre escrito do mesmo jeito, para a série histórica ser contável.

O dano de não ter isso já está escrito no próprio schema (`packages/schemas/src/dialog.ts:158-162`): texto livre
produz `troca_titularidade` × `troca_de_titularidade`, duas séries que jamais reconciliam. A árvore é a versão
sistemática desse conserto.

Hoje o `DialogForm` é **linear e plano**: `nodes: DialogNode[]` sem `next` condicional (`dialog.ts:346-349`), e
`DialogOptionSchema` é `{id, label, value, capture}` sem filhos (`dialog.ts:224-230`). Nenhum dos consumidores
tem estrutura hierárquica: **seis superfícies** leem `nodes` e **todas** percorrem lista plana —
`form_get` (`mcp-server-plughub/src/tools/dialog.ts:100`), step `loop` (`skill-flow-engine/src/steps/loop.ts:35-119`),
step `menu` (`steps/menu.ts:33-52`), página web (`channel-gateway/.../survey_web.py:424-467`),
`DialogFormRenderer` (`platform-ui/.../DialogFormRenderer.tsx:399`), editor
(`platform-ui/.../dialog-forms/DialogFormsPage.tsx:127,193,408`).

A tentação óbvia — e errada — é dar ao form um `next` condicional ou sub-formulários. Isso é exatamente o que o
ADR de `ask_when` fechou: *branching é controle, do skill chamador, não do JSON*. Este ADR entrega a capacidade
**sem** cruzar essa linha, porque o que se está modelando não é o fluxo do diálogo.

---

## A distinção que sustenta o ADR

> **Uma taxonomia é DOMÍNIO DE VALOR, não control-flow.**

`Financeiro > Cobrança > Cobrança indevida` **não decide o que vem depois**. É *uma* resposta cujo valor é
hierárquico. A árvore é a forma da **lista de opções**, não a forma do diálogo.

Daí o desenho inteiro em uma frase: **a recursão entra em `DialogOption`, nunca em `DialogNode`.** `nodes`
continua plano, as seis superfícies mantêm seu laço linear, e as 4 costuras ficam de pé.

---

## Decisões

### D1 — Recursão em `DialogOption`, `nodes` intocado

```ts
export type DialogOption = {
  id: string                    // único entre IRMÃOS; IMUTÁVEL (ver D6)
  label: LocalizedText          // livre para reescrever/traduzir
  value?: string                // ignorado quando há `options` (é pasta)
  capture?: DialogCapture
  options?: DialogOption[]      // NOVO — presente ⇒ pasta
  active?: boolean              // NOVO — default true; false = aposentada (D6)
}
```

Nota de implementação: recursão em Zod exige `z.lazy()` **com anotação explícita** `z.ZodType<DialogOption>` —
`z.infer` não deduz sozinho.

### D2 — Pasta × arquivo, com seletividade DERIVADA

Um nó da árvore é **selecionável se e somente se não tem `options`**. Pasta contém arquivos e/ou subpastas, e
os dois podem conviver no mesmo nível. Nenhuma flag a marcar, nenhuma a esquecer — mesma postura do portão
grant-first do menu (*"quando a correção pode ser 'marcar cada caso' ou 'remover a alternativa', a segunda é a
que não depende de memória"*).

*Armadilha registrada:* apagar o último filho de uma pasta a converte, em silêncio, em arquivo selecionável — o
rótulo vira resposta. O dano é contido (a agregação por prefixo distingue `financeiro` de `financeiro.*`), mas o
editor **avisa** antes de salvar.

### D3 — Profundidade máxima 5, validada no schema

`superRefine` que caminha a árvore. Acima disso é erro de autoria, não truncamento.

### D4 — Nesting só sob `interaction: "list" | "checklist"`

Sob `button` e `form` é **erro de schema**, nunca render parcial. A razão é a de sempre: um renderizador que
desenhasse só o primeiro nível perderia subárvores inteiras sem reclamar — degradação muda é pior que a flag
esquecida que D2 evita.

Com isso a multi-seleção **não precisa de vocabulário novo**:

| `interaction` | com nesting |
|---|---|
| `list` | árvore, resposta = **um** path |
| `checklist` | árvore, resposta = **lista** de paths |

### D5 — Multi-seleção é DENTRO DE UMA PASTA

Marcar vários arquivos da coluna atual; não há cesta cross-ramo. Consequência que vira mecanismo: **todos os
paths selecionados compartilham o mesmo pai**, e isso é um invariante **conferível** — `segment_outcome_record`
recusa alto quando dois paths chegam sem prefixo comum, em vez de gravar séries que não deviam coexistir.

Ganho analítico de graça: N eventos com prefixo comum até a pasta. `count` por `…motivo.financeiro.cobranca.%`
dá quantos wrap-ups tocaram a pasta; as folhas quebram por dentro.

### D6 — A resposta é o caminho de `id`s; `label` nunca entra na série

| campo | destino | pode mudar? |
|---|---|---|
| `id` | máquina — compõe a categoria do Arc 12 | **nunca** |
| `label` | humano — tela, tradução | à vontade |

Regras operacionais, que são requisito do **editor**, não convenção:

- **Renomear `label`: livre. Trocar `id`: proibido.** Mudou de conceito ⇒ folha nova, aposenta a antiga.
- **Aposentar ≠ apagar** (`active: false`): a folha sai da oferta e permanece no form, para o dado histórico
  continuar explicável. Mesma decisão de `adr-dialog-form-deletion` (arquivar, não apagar).

Versionamento (D8/D9) torna o **label** recuperável; ele **não** torna o `id` fungível. Se `cobranca` significar
coisas diferentes na v7 e na v9, a agregação por prefixo funde as duas em silêncio.

### D7 — Obrigatoriedade derivada + folha de escape

`QuestionNode` **não tem `required`** hoje (só `DialogField` tem — `dialog.ts:243`). Para survey isso nunca doeu;
para vocabulário controlado, poder pular derrota o propósito.

**Decisão: pergunta com `options` aninhado é obrigatória por construção.** Nada a marcar. Quem quiser
"opcional" declara uma folha de escape (`nao_se_aplica`) — que, pela D2, é simplesmente um **arquivo na raiz da
árvore, ao lado das pastas**. Sem colisão entre perguntas, porque a categoria é o path
(`motivo.nao_se_aplica` ≠ `servico.nao_se_aplica`).

Isso é melhor que `required` por uma razão de dado, não de ergonomia: `required` transforma "não respondeu" em
**erro**, e um `required` burlado (bug, versão antiga, canal degradado) grava NULL — indistinguível de *"não
perguntamos"*. A folha explícita transforma em **fato contável**. É a mesma regra que a casa aplica no sentido
inverso no sentimento (`current: null` = não medido, nunca `0`).

*Falha de autoria é ALTA:* esquecer a folha de escape trava o agente no mesmo dia, em vez de abrir um buraco
silencioso na série seis meses depois.

### D8 — A árvore vive INLINE no form, versionada com ele

Não se cria store de taxonomia. O argumento não é economia de infra, é recuperabilidade: a série do Arc 12 é
histórico append-only, e o vocabulário que a produziu tem que ser recuperável. Taxonomia externa **mutável**
torna categorias antigas inexplicáveis, e não há de onde reconstruir dentro do ClickHouse.

Se o reuso entre pools morder, o caminho é catálogo referenciado **com pin de versão** — nunca referência a
"o atual".

### D9 — ~~A versão viaja do render ao submit~~ **SUPERSEDED (2026-08-29)**

> **Substituída por [`adr-deploy-time-content-snapshot.md`](adr-deploy-time-content-snapshot.md).** A redação
> original era: *"`form_get`/renderer devolvem `{form, version}`; o submit devolve `form_version`;
> `segment_outcome_record` resolve `?version=N`, nunca `?status=published`."*
>
> Ela sincronizava as duas leituras. A decisão que a substitui **remove a segunda**: `form_get` já normaliza
> `captures` dentro do `render`, então o skill passa `$.pipeline_state.dialog.render.captures` no lugar de
> `dialog_form_id` e `segment_outcome_record` deixa de buscar o form. Sem segunda leitura não há corrida, e
> não há versão a carimbar. Menos mecanismo para o mesmo fim.
>
> O **achado 1** que motivava esta decisão continua válido e é o que o outro ADR resolve. A **D6** (caminho de
> `id`s imutáveis) **não** é substituída: o snapshot congela o que executa, não como o histórico se lê.

### D10 — Agregação por PREFIXO; agregação por nível fixo é inválida

Com profundidade variável, `category_l4` de um ramo curto é folha e de um ramo longo é intermediário — somar os
dois soma granularidades diferentes. O recorte hierárquico é `startsWith(category, 'pool.wrapup.motivo.financeiro.')`.

Consequência declarada, não escondida: os `category_l1..l4` seguem carregando `pool.skill.metric.<1º nível da
taxonomia>`, e **os níveis mais fundos existem só em `category`** (achado 4). O que é proibido é *inventar*
significado para `l4` num modelo de profundidade variável.

### D11 — Recusa alta em canal pobre, INFERIDA

Árvore não renderiza num `list` de WhatsApp em um turno. `form_get` **recusa** quando a pergunta tem `options`
aninhado e o canal não suporta — nunca achata. Achatar `Financeiro > Cobrança indevida` em 40 botões é emulação
muda (mesma postura de capability do `adr-cti-gateway-multi-driver`).

A condição é **inferida** da presença de nesting, não declarada num campo do form: campo pode ser esquecido, a
estrutura não.

### D12 — `ask_when` ganha `prefix`; multi = "algum casa"

`field` passa a resolver o caminho joinado. Novo op `prefix`, porque a skip-logic natural é *"se o motivo está em
qualquer lugar sob Financeiro, pergunte X"* — sem ele seria preciso listar todas as folhas do ramo.

`evaluateAskWhen` normaliza escalar→lista e casa se **algum** path casar. Ordenação (`lt/gt/lte/gte`) sobre
lista permanece indefinida ⇒ guarda falsa — que é literalmente a proposta da **decisão em aberto #3** do
`adr-dialog-conditional-skip-logic`. **A D5 a fecha por necessidade**: com multi-folha a resposta é array, e a
decisão deixa de ser adiável.

**A linha conteúdo×controle permanece.** Um form pode "começar pela raiz" e escopar as perguntas seguintes —
isso é `ask_when` + `prefix` (esconder nós), **nunca** sub-formulário (trocar quais nós existem).

---

## Achados de medição

Cada decisão acima está ancorada num destes. Medidos em 2026-08-29.

### 1. O form é lido DUAS VEZES, de dois momentos, e nada os amarra

O `DialogFormRenderer` busca o form quando o agente reivindica o item; `segment_outcome_record` busca **de novo**
— `GET …/forms/{id}?status=published` (`mcp-server-plughub/src/tools/segment.ts:76-95`, chamado em `:325`) — na
hora de compor. Entre os dois cabe um `publish`, e a janela não é curta: `dialog_wrapup_v1` tem `timeout_s: -1`
em **todo** nó (`infra/dialog/dialog_wrapup_v1.json`), então o item fica aberto até o submit.

Resultado: o agente responde uma árvore e a tool compõe contra outra — `capture.kind`, `metric` e as folhas
podem ter mudado. Sem erro em lugar nenhum. **É a D9.**

### 2. O editor perde `capture` que não conhece — armadilha armada

`flattenBlocks` **reconstrói** o `capture` do zero: bloco instrumento vira `{dimension_id, weight?}`
(`platform-ui/.../dialog-forms/dialog-blocks.ts:120` — perde `kind` **e** `metric`), bloco dialog vira
`metric ? {metric} : undefined` (`:136-137` — perde `kind`). O editor tampouco **expõe** `capture.kind`: a string
`nominal` não existe em `platform-ui/src`.

A outra metade confirma o dano: `deriveAgentEvents` tem guarda literal
`if (!cap?.kind || !cap.metric) continue` (`segment.ts:110`). Sem `kind`, **não emite nada** — o wrap-up
(outcome, resumo) continua gravado e só a série Arc 12 some.

Blast radius **hoje é zero**: o único form que declara `kind` é `infra/dialog/dialog_wrapup_arc12_v1.json`
(`fcr`/`scored` em `:35`, `servico`/`nominal` em `:48`), fixture semeada direto na dialog-api e não referenciada
por pool nenhum (`infra/dialog/README.md:45`, `TODO.md:396`). **É armadilha armada, não incêndio** — e o
primeiro form de produção com árvore a desarma para cima de si. **Daí a F0 ser pré-requisito, e não higiene.**

### 3. O ramo multi-select é CÓDIGO MORTO, e o caminho real corrompe a categoria

> ⚠️ **Corrigido em 2026-09-05, ao implementar a F2 — metade deste achado ENVELHECEU.**
> Os dois renderizadores passaram a fazer multi-seleção de verdade em algum ponto entre
> 2026-08-29 e hoje (`survey_web.py` e `DialogFormRenderer.tsx` têm o toggle, e ambos já trazem
> a regra *"zero marcações REMOVE a chave em vez de gravar `[]`"*). O que continuava verdadeiro
> era a OUTRA metade, e ela é a que produzia o dano.
>
> **E o alvo estava errado:** *"o bridge para de `json.dumps` a lista"* não é o conserto — o
> `LPUSH` carrega texto, então codificar a lista em JSON é o TRANSPORTE, e removê-lo mandaria
> uma repr de Python ou nada. O que faltava era o outro lado: **o engine nunca desfazia a
> codificação**. `menu.ts` gravava a string `'["a","b"]'` como escalar no `pipeline_state`, e
> `deriveAgentEvents` — único consumidor que espera array — a colava em `_a_b_`.
>
> Lição de método: um achado nomeia o SINTOMA com precisão e ainda assim aponta o componente
> errado. Antes de implementar a fase, re-medir os arquivos que ela cita.


O comentário *"multi-select ⇒ N eventos"* aparece em dois lugares (`segment.ts:56-57`,
`schemas/src/dialog.ts:157-158`) e descreve comportamento que **nenhuma UI produz**:

- `DialogFormRenderer.tsx:415-433` — seleção **única**; `checklist` é tratado igual a `button`/`list`, e clicar
  numa segunda opção substitui a primeira.
- `survey_web.py:438-461` — seleção **única** explícita (`:457` remove `.sel` das irmãs).
- `steps/menu.ts:450-455` — comentário literal na `:451`: *"text, button, list, checklist — resposta é uma
  string scalar"*.

E há um caminho venenoso: o protocolo do webchat **aceita** lista (`channel-gateway/.../models.py:61`,
`WsMenuSubmit.result: str | list[str] | dict`), e o bridge, ao receber uma, faz **`json.dumps`**
(`orchestrator-bridge/.../main.py:9116-9126`). O engine grava a string literal `["opt_a","opt_b"]` como escalar.
`deriveAgentEvents` (`segment.ts:132`) é o **único** ponto do sistema que espera array — e recebendo a string
produz `leaf = "_opt_a_opt_b_"`: **uma categoria-lixo, não N eventos**. `composeScore` é pior por outro lado:
`RawAnswer = string | number | null` (`survey.ts:82-95`) nem representa array ⇒ `NaN` ⇒ NA silencioso.

O ramo só é exercido por fixture que injeta JSON cru direto no payload da tool, pulando todos os renderizadores
(`infra/test/smoke_wrapup_arc12_capture.sh:126`). **A D5 não é de graça: exige a F2.**

### 4. A categoria do Arc 12 RECUSA a profundidade decidida

`AGENT_EVENT_CATEGORY_REGEX = /^[a-z0-9_]+(\.[a-z0-9_]+){1,4}$/` (`schemas/src/agent-events.ts:29`) aceita
**2 a 5 segmentos**. Uma taxonomia de 5 níveis daria `pool.skill.metric` + 5 = **8 segmentos** ⇒ **rejeitado**.
Não é teto suave: é bloqueio.

Além disso `decomposeCategoryLevels` só extrai 4 (`agent-events.ts:194`,
`const [l1,l2,l3,l4] = category.split(".")`), então um 5º segmento hoje é aceito pela regex, gravado em
`category` e **silenciosamente invisível** nas colunas de nível — sem erro, sem log. E o `nominal` atual
(`pool.skill.metric.folha`) já consome os 4.

Do lado da leitura: `category` completa existe como `String` (`analytics-api/.../clickhouse.py:756`) e
`startsWith(category, …)` já é usado (`reports_query.py:7054`, `:7174`) — **fora do índice**, porque o `ORDER BY`
é `(tenant_id, category_l1, category_l2, category_l3, emitted_at)` (`:769`) e `category` não está na chave.
Nenhum endpoint expõe agregação por `category_l*`: `/summary` tem whitelist
`^(category|skill_id|pool_id|agent_type_id)$` (`reports.py:1778`) que **não** os inclui.

Isto é a **D10** e a **F5**: a regex sobe para o número de segmentos da D3, `decomposeCategoryLevels` mantém 4
**declaradamente**, e o custo de índice do `startsWith` é aceito e registrado.

### 5. Versionamento já EXISTE — é uso, não build

A PK de `dialog.forms` é `(tenant_id, form_id, version)` (`dialog-api/.../db.py:43-57`): **cada versão é uma
linha**, e não há tabela de snapshot separada (ao contrário da evaluation-api, que fez o desenho oposto em
`evaluation.form_versions`). O `publish` **promove e não grava**: `SET status='published', updated_at=now()`
(`db.py:309-341`), sem tocar `json`/`name`/`tags`, e **sem rebaixar** a versão anterior.

`GET /v1/dialog/forms/{form_id}?version=N` já existe (`router.py:141` → `db.py:174-178`). Logo a **D9 é plumbing**
(carimbar e propagar), não construção de store.

Duas ressalvas que a medição encontrou e este ADR **não** resolve:

- **Não existe `GET /{form_id}/versions`** — o catálogo devolve só a maior versão de cada form
  (`db.py:139`, `DISTINCT ON`). Para saber que a v2 existe, é preciso já saber.
- **Rollback não faz rollback.** `POST /{form_id}/publish?version=1` funciona (`db.py:216-219`), mas deixa v1 e
  v2 ambas publicadas — e `?status=published` resolve por `ORDER BY version DESC` (`db.py:179-187`), ou seja,
  continua devolvendo a v2. É bug latente, sem teste nem comentário que o cubra. **Fora de escopo aqui,
  registrado para não se perder.**

---

## Decisões em aberto

1. **Multi-seleção com pasta e subpasta no mesmo nível.** Marcar arquivos da coluna atual e *também* navegar
   para uma subpasta é ambíguo. Proposta: navegar limpa as marcações do nível; marcar desabilita a navegação.
   Decidir na F3, com a tela à mão.
2. **Busca / typeahead** sobre folhas, para árvores grandes de profundidade 5. É UX, não modelo — não bloqueia.
3. **`survey_record` e árvore.** Este ADR trata do sink de wrap-up (Arc 12). Se um survey de cliente usar árvore,
   `composeScore` precisa de regra para path (provavelmente: árvore é sempre `nominal`, nunca `scored`).

---

## Consequências

- Vocabulário de wrap-up padronizado e contável, com a taxonomia autorável pelo operador sem tocar skill.
- `nodes` permanece plano: as seis superfícies mantêm o laço linear; a costura conteúdo×controle fica intacta.
- Forms com árvore são **rich-only** por construção, e a recusa é alta.
- A decisão em aberto #3 do ADR de `ask_when` fecha.
- **Custo:** um op novo (`prefix`) em três avaliadores (`schemas`, `survey_web.py`, `DialogFormRenderer` — o
  triplicamento já existe e este ADR não o piora nem o conserta); um editor de árvore que não existe (hoje o
  editor de opções é add/remove com ↑↓, `DialogFormsPage.tsx:826-849`, e **não há lib de DnD no projeto**);
  e a F2, que é dívida alheia que a D5 obriga a pagar.

---

## As-built da F2 (2026-09-05)

Entregue: `coerceMultiAnswer` no `menu` step — a resposta de `checklist` vira uma LISTA no
`pipeline_state`, em vez da string JSON que ninguém desfazia.

**Metade da fase já estava pronta**, e só a medição mostrou isso: os dois renderizadores fazem
multi-seleção desde antes (ver a correção do achado 3). O que faltava era o engine, e o bridge
nunca foi o alvo.

**A promessa existia em DOIS lugares e não tinha produtor em nenhum:** o comentário de
`deriveAgentEvents` (*"Multi-select chega como array ⇒ N eventos"*) e o `CLAUDE.md` do
`skill-flow-engine`, que declara **em tabela** `checklist → string[]`. Prosa afirmando invariante
sem mecanismo é a família do DDL de `participation_intervals`; aqui foram duas casas afirmando o
mesmo fato falso.

**Escalar de canal que não manda JSON vira lista de UM.** Uma marcação continua sendo uma
marcação, e o consumidor não precisa de dois caminhos. Medido antes de escolher: **zero** steps
`menu` com `interaction: checklist` nos skills e **uma** pergunta `checklist` publicada (a
fixture do Arc 12) — não havia o que migrar, e por isso a semântica pôde ser escolhida limpa.

**Consequência declarada no lado do survey:** `SurveyRecordInputSchema.answers` é
`string | number | null`, então uma resposta multi passou a ser **RECUSADA no limite do schema**
(medido: array reprova, escalares passam). Antes ela era aceita como string JSON, virava `NaN` no
`scoreOfAnswer` e sumia como NA **silencioso**. Trocar um NA mudo por uma recusa barulhenta é a
direção certa, e fecha a decisão em aberto #3 na prática: **árvore e multi são `nominal`, nunca
`scored`** — uma marcação múltipla não tem UM valor a compor.

Gate: `infra/test/probe_multi_answer_events.sh`, sobre a forma REAL publicada, exercitando a
CADEIA (canal → engine → tool) e não só a ponta. Testar só o `deriveAgentEvents` passaria com o
defeito no lugar: ele sempre soube tratar array; quem não entregava array era o engine. A mutação
que devolve o escalar reproduz o defeito histórico **literalmente** —
`sac_humano.wrapup.servico._suporte_financeiro_`.

---

## As-built da F1 (2026-09-05)

Entregue: `DialogOption.options`/`active` recursivo (`z.lazy` com anotacao explicita, porque
`z.infer` nao deduz), `optionTreeIssues` (profundidade 5, `id` unico entre IRMAOS, aninhamento so
sob `list`/`checklist`) e o op `prefix` no avaliador, com normalizacao escalar→lista.

**As regras NAO moram num `superRefine`.** `superRefine` devolve `ZodEffects`, que nao entra em
`discriminatedUnion` (o `DialogNodeSchema`) nem aceita `.omit()` (o `DialogFormDraftSchema`, que o
dry-run do editor usa). Elas vivem numa funcao PURA chamada pelo validador canonico
(`validateDialogForm`) — a mesma casa de `duplicateNodeIds`, e a mesma que o `form_get` roda. O
schema carrega a FORMA; o veredicto carrega as REGRAS.

**Duas decisoes que a D12 nao fixava, e que a implementacao teve de fechar:**

- **`prefix` casa por SEGMENTO, nunca por substring.** `startsWith` cru faria a guarda de
  `financeiro` casar `financeiro_avulso`, que nao e filho do ramo — a pergunta errada apareceria
  sem nada ficar vermelho. Vale `caminho === prefixo || caminho.startsWith(prefixo + ".")`.
- **`ne` e a NEGACAO de `eq`, nunca "algum difere".** Com multi-resposta, "algum difere" faria uma
  marcacao com X e Y satisfazer `eq X` **e** `ne X` ao mesmo tempo, e *"pergunte a menos que tenham
  escolhido X"* mentiria. Consequencia declarada: `ne === !eq` para qualquer resposta, escalar ou
  lista.

Duas ausencias tambem sao decisao: **lista VAZIA e "nao respondeu"** (entra na mesma guarda de
`""`), e **`options: []` e RECUSADO** — pasta sem filho le-se de dois jeitos, e a D2 ja avisa que
pasta que perde o ultimo filho vira folha selecionavel.

**A D7 nao tem superficie de schema.** `QuestionNode` nao tem `required`; a obrigatoriedade
derivada do aninhamento e regra de RENDER, e fica com a F3. Registrar isso importa: seria facil
dizer que a F1 a entregou e ninguem notaria a falta ate um operador pular a pergunta.

Gates: `probe_ask_when_parity.sh` (as tres copias, que a D12 obrigou a mexer juntas — o op novo
nasceu VERMELHO ali, com a canonica sozinha, e so ficou verde com as tres) · `dialog.test.ts`
(22 casos, tres mutacoes provadas) · `dialog-render.test.ts` (a LIGACAO da regra ao veredicto,
falseada desligando a chamada).

---

## Fases

| # | Fase | Entrega | Depende de |
|---|---|---|---|
| **F0** | **`flattenBlocks` lossless** | O editor edita o objeto parseado in-place; nunca reconstrói `capture`. Gate de round-trip: abrir + salvar `dialog_wrapup_arc12_v1` preserva `kind`. | — |
| ~~**F0b**~~ | ~~Pin de versão~~ — **movida** para a fase **S1** de [`adr-deploy-time-content-snapshot.md`](adr-deploy-time-content-snapshot.md), que mata a corrida removendo a segunda leitura em vez de sincronizá-la (ver D9). | — |
| **F1** ✅ | **Schema** *(2026-09-05)* | `DialogOption.options`/`active` (`z.lazy` + anotação), `superRefine` de profundidade (D3), nesting só sob `list`/`checklist` (D4), `id` único entre irmãos. `evaluateAskWhen` + `prefix` e normalização escalar→lista (D12). ⚠️ A **obrigatoriedade derivada (D7) NÃO entrou**: `QuestionNode` não tem `required`, então ela é regra de RENDER e foi para a F3. | `probe_ask_when_parity.sh` (DLG-09) |
| **F2** ✅ | **Resposta multi de verdade** *(2026-09-05)* | `menu.ts` para de tratar `checklist` como escalar — **o unico item que faltava**. Os renderizadores ja faziam multi, e o `json.dumps` do bridge e TRANSPORTE, nao defeito (ver a correcao do achado 3). Testemunha negativa: `["a","b"]` produz **2** eventos, nunca 1 com categoria `_a_b_`. | F1 |
| **F3** | **Renderer Miller + recusa alta** | Colunas no Console (pasta abre coluna, arquivo seleciona); `form_get` recusa em canal pobre (D11); invariante de pai comum conferido no submit (D5). | F1, F2 |
| **F4** | **Categoria de caminho** | Regex sobe para a profundidade da D3; `decomposeCategoryLevels` mantém 4 **declaradamente**; relatório por `startsWith` (D10). | F2 |
| **F5** | **Editor de árvore** | Autoria da árvore, aviso de pasta-que-virou-arquivo (D2), `id` bloqueado para edição e `active` como aposentadoria (D6). | F0, F1 |
| **F6** | **Validação** | Wrap-up real ponta a ponta com árvore de 3+ níveis e multi-folha; contagem por prefixo confere com os eventos emitidos. | todas |


> **Emenda de 2026-09-05 (DLG-09).** A D12 acrescenta o op `prefix` a um avaliador que existe
> **tres vezes**, e nenhuma das tres podia importar as outras — o platform-ui nao importa
> `@plughub/schemas`, e a copia do `survey_web.py` e **JavaScript dentro de uma string Python**.
> Ate hoje nada as comparava. `infra/test/probe_ask_when_parity.sh` passou a conferir, e o ramo **C**
> le a lista de ops do proprio `switch` canonico: quem acrescentar `prefix` sem a linha na tabela
> reprova **antes** de espalhar a divergencia. Por isso a F1 deixou de depender de nada e passou a
> depender dele. ⚠️ O mesmo trabalho encontrou uma divergencia REAL, e ela nao e do veredicto:
> a resposta de um no pulado e **apagada** na web e **mantida** no Console (DLG-10).

**Ordem inegociável:** F0 antes de F5 (editor sobre projeção com perda destrói árvore inteira, em silêncio) e
F2 antes de F4 (categoria de caminho sobre resposta escalar-com-JSON-dentro produz lixo, não hierarquia).

**Gate:** `infra/test/probe_dialog_tree_options.sh`, no molde de `probe_dialog_form_delete.sh` — vermelho antes
do build, verde depois, com testemunha negativa em cada fase (o que o faria ficar vermelho está nomeado na
coluna "Entrega").


---

## Apêndice — resumo denso migrado do índice do `CLAUDE.md` (2026-08-31)

> Este bloco vivia como **uma linha** do índice `docs/` no `CLAUDE.md`, onde ocupava 2273 bytes.
> Medido antes de mover: **~85% do seu vocabulário já existe neste ADR** — ele é uma condensação
> independente, não uma cópia, e por isso os ~15% restantes (achados, números e nomes de arquivo que
> só foram registrados no índice) **não existiam em lugar nenhum além dali**. Movido inteiro, sem
> resumir, porque a alternativa — cortar no CLAUDE.md e confiar que o ADR já dizia tudo — perderia
> exatamente a fração que não dá para recuperar.
>
> **É trabalho aberto**, não documentação final: a fração nova deve ser dobrada no corpo do ADR e
> este apêndice, encolhido. Enquanto isso não acontece, ele é a única cópia.

opções em ÁRVORE no DialogForm (taxonomia de wrap-up). **A recursão entra em `DialogOption`, nunca em `DialogNode`** — taxonomia é DOMÍNIO DE VALOR, não control-flow (`Financeiro > Cobrança > indevida` é UMA resposta, não decide o que vem depois), então `nodes` segue plano e as SEIS superfícies mantêm o laço linear. Pasta × arquivo com seletividade **derivada** (selecionável ⟺ sem `options`); profundidade 5; nesting só sob `list`/`checklist` (sob `button`/`form` é erro de schema, nunca render parcial); multi-seleção **dentro de uma pasta** ⇒ prefixo comum vira invariante CONFERÍVEL; resposta = caminho de **`id`s** (label nunca entra na série); obrigatoriedade derivada do nesting + folha de escape `nao_se_aplica` (que é um *arquivo na raiz*) — `required` burlado grava NULL, indistinguível de "não perguntamos"; árvore INLINE e versionada; `ask_when` ganha `prefix`, o que **fecha a decisão em aberto #3** do ADR de skip-logic. Quatro achados que sustentam as fases: **(1)** o form é lido DUAS VEZES (renderer no claim × `segment.ts:325` no submit, com `timeout_s:-1` no meio) e nada as amarra ⇒ pin de versão; **(2)** `flattenBlocks` reconstrói `capture` e perde `kind` (`dialog-blocks.ts:120,136`), que `deriveAgentEvents` exige (`segment.ts:110`) — armadilha ARMADA, blast radius zero hoje (só fixture a declara); **(3)** o ramo `multi-select ⇒ N eventos` é **CÓDIGO MORTO** — os 3 renderizadores tratam `checklist` como escalar e o bridge faz `json.dumps` na lista (`main.py:9116-9126`) ⇒ hoje sairia **uma** categoria-lixo `_a_b_`; **(4)** `AGENT_EVENT_CATEGORY_REGEX` aceita 2–5 segmentos e a profundidade decidida daria 8 ⇒ **bloqueio**, e `decomposeCategoryLevels` só extrai 4 (5º segmento hoje é gravado e silenciosamente invisível). Versionamento é **uso, não build** (PK inclui `version`, publish só promove, `?version=N` existe). Adjacentes registrados e fora de escopo: sem `GET /{form_id}/versions`, e **rollback não faz rollback**. Fases F0 (lossless) · F0b (pin) · F1 (schema) · F2 (multi de verdade) · F3 (Miller + recusa alta) · F4 (categoria de caminho) · F5 (editor) · F6. Ordem inegociável: F0 antes de F5, F2 antes de F4 — proposto
