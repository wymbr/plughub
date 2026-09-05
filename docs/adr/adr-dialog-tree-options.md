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

## F6 FECHADA (2026-09-05) — a arvore mediu inteira, com o pin no ar

Terceiro contato real, ja com o pin implantado (sessao `d36639f8`, wrap-up `88a98d26`). Verdade de
origem:

```json
{ "classificacao": "resolvido",
  "motivo":        "tecnico.conectividade.lentidao",
  "servico":       ["cadastro.segunda_via", "cadastro.troca_titularidade"] }
```

**O que so este contato provou:** `servico` com **duas folhas na MESMA pasta** rendeu **3 eventos**
(1 de motivo + 2 de servico). Lista de um elemento — que era o caso das passadas anteriores — nao
distingue *o emissor itera a lista* de *o emissor pega o primeiro*; duas distinguem. E a metade multi
da F2 exercida por contato real, e fecha a `DLG-22`.

**O prefixo comum da D5 vale por construcao, e agora tem testemunha:** as duas folhas compartilham
`servico.cadastro` — navegar no renderer limpa as marcacoes, entao cesta cross-ramo nao e montavel
pela tela.

**O pin atravessou as tres pontes.** `core.workflow.dialog_form_version = "1"` no ctx da sessao de
wrap-up, `source: delegate_conference`, gravado as 19:33:47 — **antes** do render; e as tres linhas do
ClickHouse saem com `tags {dialog_form_id: dialog_wrapup_arvore_v1, dialog_form_version: 1}`.

### A divergéncia deixou de ser argumento e virou dado

| medida, na pasta `servico.cadastro` | n |
|---|---|
| marcacoes (`countIf` por prefixo) | **2** |
| contatos (`uniqExactIf` por prefixo) | **1** |

E o caso que sustenta a decisao do dono de exibir **as duas colunas, sem padrao**: publicar so a soma
faria `servico` totalizar mais que o numero de atendimentos, e alguem tiraria percentual disso.
`uniqExact` nao soma — por isso a coluna de contatos e **server-side por necessidade**, nao por gosto.

### Gate

`infra/test/probe_agent_event_form_stamp.sh` — 4 ramos, com **INCONCLUSIVO como ramo proprio**: sem
evento na janela ele NAO fica verde, porque *"todos carimbados"* sobre populacao zero e verdade vazia.
Falseado por tres mutacoes: epoca no futuro (sem amostra), forma inexistente no store (o carimbo que
aponta para documento que nao existe e pior que carimbo nenhum), e ClickHouse inalcancavel.

## As-built da D13 (2026-09-05) — a epoca recorta, e o conflito some

`GET /reports/agent-events/epochs` devolve um bloco por **run contiguo**, por sessionizacao:
marca-se a TRANSICAO de `tags['dialog_form_id']` e soma-se acumuladamente para obter o id do run.
O `/tree` ganhou `form_id` para recortar.

**A prova de que recortar resolve, medida ao vivo:**

| consulta | `single_vocabulary` | o que a arvore mostra |
|---|---|---|
| sem recorte | **false** | `segunda_via` em DOIS caminhos (9 na raiz, 1 sob `cadastro`) |
| recortada na epoca carimbada | **true** | so `cadastro.*` — a duplicata nao existe |

O conflito nao passa a ser tratado: ele **deixa de existir**. Dentro de um bloco ha um vocabulario
so, entao total e percentual voltam a ser legitimos sem guarda nenhuma — que e a preferencia que
este arco ja exerceu no `form` virando tipo de bloco e na pasta×folha sendo derivada.

### A run SEM CARIMBO e uma run

Evento anterior ao carimbo tem `form_id` vazio. Ele nao e descartado (esconderia dado real) nem
atribuido a uma forma (inventaria proveniencia): vira uma epoca propria, rotulada como tal. E ela
**ainda mistura vocabularios** — a tela diz isso, porque a forma daqueles eventos nunca foi
gravada e nenhum recorte os separa. Forward-only, como declarado na D14.

⚠️ **O `form_id` vazio e uma EPOCA, nao ausencia de filtro** — e o discriminador no servidor e
`is not None`, nunca truthiness. Um `if form_id:` colapsaria a epoca anterior ao carimbo com
*todas as epocas*, que e a familia do `if not x` catalogada na § Postura de Engenharia. O ramo D do
gate existe so para prender isso, e a mutacao correspondente o deixa vermelho com a mensagem exata.

### Limite declarado: o discriminador run × forma NAO foi exercido

Nao houve rollback de hook nestes dados, entao nunca existiu a mesma forma em dois runs separados.
A mutacao que troca `GROUP BY forma, grupo` por `GROUP BY forma` foi detectada — mas por **quebrar
o SQL** (coluna fora do agrupamento), nao pela proposicao. O desenho segue o decidido na D13; o que
falta e um caso que o prove. **Gatilho:** o primeiro rollback de `dialog_form_id` num pool.

Gate: `infra/test/probe_agent_event_epochs.sh` — 4 ramos, com **B** conferindo que as epocas
PARTICIONAM (a soma delas e o total da janela: nada perdido, nada contado duas vezes) e **C**
comparando recortada × nao recortada, porque *"o endpoint responde"* nao e a proposicao.

## D14 — o carimbo e `(form_id, version)`, e nasce no RENDER (decidida 2026-09-05)

Proposta do dono: *"gravar o id + timestamp da publicacao do dialogform, sempre com os dados
enviados"*. Resolve — e a medicao do store melhora a chave e endurece o requisito.

### A versao publicada e IMUTAVEL — entao a chave e `version`, nao o timestamp

Medido em `dialog-api/db.py`:

| operacao | efeito |
|---|---|
| `db_put_form`, ultima versao e **rascunho** | edita a linha do rascunho no lugar |
| `db_put_form`, ultima versao e **publicada** | cria **`N+1`** como rascunho |
| `db_publish_form` | so `SET status='published'` — promove, **nunca reescreve conteudo** |

Logo o conteudo de uma versao publicada **nunca muda**, e `(tenant_id, form_id, version)` ja e a
PRIMARY KEY: e a identidade exata do documento. O timestamp e chave mais fraca para o mesmo fato — e
`updated_at` e bumpado pelo PROPRIO publish, o que o coloca na familia *"foi escrito ≠ mudou"* (o
`updated_at` bumped a cada boot, § Postura de Engenharia).

⚠️ **Isto REVOGA o residuo da D13.** La ficou escrito que *"`form_id` nao pega edicao in-place"* e que
marcar versoes dentro do bloco era aposta. Falso: edicao in-place de versao PUBLICADA nao existe. E o
desfecho e melhor que a aposta — com a identidade exata gravada, **partir ou nao partir por versao vira
decisao de EXIBICAO**, tomada na leitura. Grava-se o exato; agrega-se conforme a pergunta.

### O carimbo nasce no RENDER, nunca no submit

E a metade forte da proposta (*"sempre com os dados enviados"*), e ela tem mecanismo obrigatorio: o
**achado 1** deste ADR mede que o formulario e lido DUAS VEZES — o renderer no claim (o que o atendente
ve) e `segment.ts:325` no submit (o que deriva os eventos) — com `timeout_s: -1` e ACW de 24 h no meio,
e **nada as amarra**.

Carimbo tirado no submit nomearia um documento que o atendente nunca viu. E o dano nao e o rotulo
errado: os eventos seriam **derivados da arvore NOVA sobre resposta dada na VELHA** — um caminho que
era folha pode ter virado pasta, e a categoria sai reinterpretada sem nada ficar vermelho.

### Convergencia: e a S1 do `adr-deploy-time-content-snapshot`

Aquela fase e **pin de versao (caminho A)**, decidida pelo dono em 2026-08-29 e nao iniciada; a
supersessao da F0b deste ADR ficou SUSPENSA esperando-a. A proposta de hoje e a mesma peca chegando
pelo outro lado — leitura (relatorio) em vez de escrita (execucao). **Uma implementacao serve as
duas**, e a ordem se resolve sozinha: o pin grava a versao que o render usou; o emissor a copia para o
evento.

⚠️ **O pin e gravado pelo SERVIDOR**, nao enviado pelo cliente — decisao ja tomada naquele ADR. Versao
vinda do cliente seria o chamador declarando a propria identidade, a forma que derrubou o gate de
avaliador na CAP-01.

## D13 — troca de forma vira EPOCA, derivada dos eventos (decidida 2026-09-05)

Fecha a pergunta que a secao abaixo deixou aberta (*historico continua visivel na mesma lente, ou a
troca e corte de serie?*). **Decisao do dono: um bloco por forma, mesmo periodo recortado** — a
manipulacao e a exibicao ficam identicas, muda so a janela; o dado fica inteiro e a troca fica
legivel.

**Nao e conceito novo.** A lente `deploy` ja tem `mode=epoch`, lendo por `(pool, deploy_version)`,
com overlay de cobertura. Isto e o mesmo idioma com outro discriminador — o que e o argumento
principal a favor: custa **zero** conceito de exibicao, e a D5 do ADR de relatorios existe para
cobrar exatamente isso.

### A epoca se deriva dos EVENTOS, nunca da configuracao

Nao e preferencia — **a configuracao nao sabe**. Medido: o registry tem `skill_deployments`
(append-log dos promotes) e **nada equivalente para hooks**; a tabela `pools` e atualizada no lugar,
entao o `dialog_form_id` anterior deixou de existir no instante do `PUT`. So o `updated_at` lembra
que *algo* mudou.

E derivar do evento e **melhor**, nao um consolo: troca de forma sem trafego nenhum nao produz
epoca — e nao deve mesmo, porque epoca e periodo em que se MEDIU algo. Config sem evento e intencao,
nao medicao.

### O bloco e por RUN CONTIGUO, nao por forma

Rollback do hook faz a mesma forma valer em dois periodos separados. *"Um bloco por forma"* funde os
dois e apaga a fase do meio; *"um bloco por run"* mostra as tres fases como foram. Mesma distincao do
`queue_wait_segment_id`: o fenomeno e a PASSAGEM, nao o conteiner — e o discriminador tem de
descreve-la.

### A fronteira e uma FAIXA, e a largura e medivel

O ACW deste pool e `acw_timeout_hours: 24`. Um wrap-up reivindicado antes da troca e submetido depois
carrega a forma NOVA — logo duas epocas podem se sobrepor por ate **um dia inteiro**. Derivar dos
eventos exibe isso como sobreposicao real; qualquer corte por timestamp de config fingiria um instante
que nao existe. **A sobreposicao e dado, nao ruido**, e some sozinha quando a janela se afasta da
troca.

### Residuo declarado: `form_id` nao pega edicao in-place

Pela D6 o `id` da forma e imutavel e a serie e append-only — acrescentar uma folha **nao** muda o
`form_id`. Acrescentar e compativel (caminho antigo segue valido) e nao deveria partir a serie;
reestruturar nao e. Em vez de inventar um criterio que envelhece (*"e adicao pura?"*), a escolha e:
**partir por `form_id`, marcar as VERSOES dentro do bloco**, como os marcadores de deploy ja fazem.
Se a marcacao mostrar que reestruturacoes silenciosas sao comuns, a regra se revisa com dado.

### Pre-requisito unico

Tudo acima depende de **uma** coisa: o evento carregar `dialog_form_id` + versao (`DLG-24`). Sem o
carimbo nao ha como recortar, e a alternativa — inferir a forma pela SILHUETA da arvore — e heuristica
que quebra quando duas formas compartilham prefixo.

## O evento nao carrega a FORMA, e a serie ja esta misturada (2026-09-05)

Achado ao desenhar o relatorio de taxonomia, a partir de uma duvida do dono: *"o formulario
de wrap-up e por pool; com o filtro em 'tudo', nao vai misturar formularios diferentes?"*.

**Esta certo, e e pior — nao precisa de dois pools.** Medido ao vivo, num pool so, no mesmo dia:

```
retencao_humano.wrapup.servico.troca_titularidade            prof 1   n=2   <- forma arc12
retencao_humano.wrapup.servico.cadastro.troca_titularidade   prof 2   n=1   <- forma arvore
```

O `dialog_wrapup_arc12_v1` tem `servico` como **lista plana**; o `dialog_wrapup_arvore_v1` tem
`servico.cadastro.*`. Repontar o hook do pool (o que foi feito as 17h) trocou o vocabulario **sob
a mesma serie**. O mesmo servico do mundo real passou a ser contado em dois caminhos, e uma
agregacao por prefixo `…servico.` soma os dois como se fossem coisas diferentes — ou, pior, um
deles vira "pasta" e o outro "folha" na mesma arvore.

### Por que filtro nenhum conserta isto

O vinculo **pool → formulario** e `PoolHookEntry.context.dialog_form_id`: config do pool, mutavel
a qualquer momento. Logo:

| tentativa | por que falha |
|---|---|
| forcar **um pool** no filtro | o mesmo pool troca de forma ao longo do periodo — medido acima |
| resolver **forma → pools** na consulta | resolve o **agora**, nao o periodo; a consulta de amanha reescreve o passado |
| encurtar o periodo | so estreita a classe de falha, nao a remove — a troca pode cair dentro de qualquer janela |

E a regra da casa outra vez: **identidade derivada tem de conter o discriminador do FENOMENO,
nunca o do conteiner**. O fenomeno aqui e *"resposta a ESTE vocabulario"*; o pool e o conteiner,
e nao o identifica. Mesma forma do `queue_wait_segment_id`, que carimbava a SESSAO enquanto o fato
era a PASSAGEM pela fila.

### A correcao que fecha, e a que da para fazer agora

**Fecha:** o evento carrega `dialog_form_id` (e a versao). O `deriveAgentEvents` **ja le o
formulario** para derivar a categoria — o dado esta na mao no instante da emissao; nao ha consulta
nova. E forward-only: linha antiga fica sem forma, e o corte se declara em data, como a
`SEGMENT_SLA_EPOCH` — nunca se fabrica a forma do passado.

**Da para fazer antes disso, sem coluna nova:** a colisao tem **assinatura nos proprios dados** —
o mesmo id aparecendo em profundidades diferentes sob o mesmo pai, ou um id que e folha numa linha
e pasta noutra. Detectando, a lente **recusa total e percentual** e nomeia as formas em conflito,
em vez de somar em silencio. E o `comparability: 'same_form'` do contrato de lentes operando —
campo que ja existe, nascido da guarda cross-form da lente `quality`.

### O que NAO fazer: proibir o "tudo"

Tentador e errado por duas razoes. **(1)** Nao resolve — a mistura e temporal, nao espacial.
**(2)** Proibir quebra o caso que a taxonomia controlada existe para servir: N pools compartilhando
o MESMO vocabulario e comparaveis de proposito (Retencao × Cobranca sob a mesma arvore e o
objetivo, nao o risco). O eixo de comparabilidade e a FORMA, e o filtro deve dize-lo — mostrando
quantas formas distintas cairam na janela — em vez de restringir o pool, que e a proxy errada.

## Medicao da F6, segunda passada (2026-09-05, apos o rebuild) — a arvore mediu

Console rebuildado, contato novo: sessao `4919af4a-6c97-48d0-aaef-b783dc1f46a0`, segmento humano
`cfbafa41`, wrap-up as 17:20. A **verdade de origem** (o `__resume_payload__` do delegate, lido do
`pipeline_state` da sessao de wrap-up `46c4e8d9`) diz o que o operador respondeu:

```json
{ "classificacao": "resolvido",
  "motivo":        "financeiro.pagamento.nao_identificado",
  "servico":       ["cadastro.troca_titularidade"],
  "resumo":        "tudo bem" }
```

Cinco coisas ficam provadas por contato real, e nenhuma delas era observavel na primeira passada:

1. **A resposta e o CAMINHO ate a folha**, nao a pasta — `financeiro.pagamento.nao_identificado`, os
   3 niveis. As colunas Miller entregaram o que a D2 e a D7 pedem.
2. **`checklist` chega como LISTA** (`["cadastro.troca_titularidade"]`), nao como a string
   `'["..."]'` que a F2 removeu. O array sobreviveu engine → tool.
3. **A guarda `prefix` (D12) rodou, e rodou discriminando.** `valor_contestado` esta **ausente** das
   respostas: o `ask_when {field: motivo, op: prefix, value: financeiro.cobranca}` nao disparou
   porque o caminho foi `financeiro.pagamento.*`. Comeca com `financeiro.` e **nao** com
   `financeiro.cobranca` — a pergunta condicional foi corretamente **nao feita**.
4. **A classificacao virou desfecho**: `resolvido` → `outcome: resolved` no segmento `cfbafa41`, por
   referencia, e a tool devolveu `agent_events: 2` — que e o numero de linhas no ClickHouse.
5. **A categoria coube**: 6 segmentos (`retencao_humano.wrapup.motivo.financeiro.pagamento.nao_identificado`)
   contra o teto de 8 derivado na F4, com os pontos preservados por segmento.

### A asserção que a fase pede

*Contagem por prefixo confere com os eventos emitidos* — medido sobre o dado real:

| medida | n |
|---|---|
| por **PREFIXO** (D10) — `…motivo.financeiro.` | **1** |
| por **PREFIXO** — `…motivo.` (os dois contatos) | **2** |
| por **INDICE** `l4 = financeiro` | 1 |
| por **INDICE** `l4 = nao_identificado` (a FOLHA) | **0** |

A ultima linha e o ponto da D10, agora com dado: **`l4` nao alcanca a folha** — a decomposicao para
em quatro por decisao, e quem agrega por indice conta o galho. Por isso o relatorio agrega por
`startsWith` sobre a `category` inteira, e os `l1..l4` servem o INDICE.

### O que a F6 ainda nao exerceu, e por que importa

**Multi-folha.** O operador marcou **um** servico. Uma lista de um elemento nao distingue *"o emissor
itera a lista"* de *"o emissor pega o primeiro"* — e essa e exatamente a proposicao que a F2
existe para sustentar. O `probe_multi_answer_events.sh` cobre 2 folhas, mas na cadeia **engine →
tool**; o que falta e a ponta de cima, a tela emitindo um array de 2 a partir das caixas dentro de
uma pasta. Um terceiro contato marcando **duas folhas dentro da mesma pasta** em `servico` fecha a
fase. Registrado como `DLG-22`.

⚠️ **Residuo declarado:** os 2 eventos de 17:03 (`motivo.tecnico`, `servico.cadastro`) sao valores de
PASTA numa serie de folha, produzidos pelo Console velho. A serie e append-only por desenho (D6), e
eles continuam contando em qualquer agregacao por `…motivo.` — como se ve na tabela acima, onde o 2
mistura uma folha e uma pasta. Sao 2 linhas num ambiente de demo; ficam, nomeados aqui.

## Medicao da F6 (2026-09-05) — a cadeia funcionou, e o VALOR saiu errado

Contato real atravessou o `retencao_humano` as 17:02-17:03 (sessao
`4db6dbff-4e15-422b-b6ae-8eea1321ff33`, segmento humano `68bc8aed`). O wrap-up rodou, gravou
`outcome: resolved` no segmento por referencia, e emitiu **2** eventos de negocio:

```
retencao_humano.wrapup.motivo.tecnico     l1=retencao_humano l2=wrapup l3=motivo  l4=tecnico
retencao_humano.wrapup.servico.cadastro   l1=retencao_humano l2=wrapup l3=servico l4=cadastro
```

**A forma esta certa e o conteudo esta errado**: `tecnico` e `cadastro` sao **PASTAS**, nao folhas.
O que se quis contar era `tecnico.conectividade.sem_sinal`; o que existe no ClickHouse e o rotulo do
galho. E o modo de falha que a D2 e a D7 existem para impedir, agora com dado gravado.

### Por que aconteceu, medido nos dois lados

**O Console servido estava ATRAS do build.** A imagem do `platform-ui` e das 09:32; a F3 (colunas
Miller + guarda D7) entrou depois. Provado com **controle positivo** dentro do bundle servido — sem
ele o zero nao significaria nada, porque grep que nao alcanca o arquivo tambem devolve zero:

| marcador | ocorrencias no bundle servido | o que prova |
|---|---|---|
| `maskedRefused` (chave i18n pre-existente) | **3** | o grep alcanca os locales embutidos |
| `leafRequired` (guarda D7, so pos-F3) | **0** | a tela nao tinha a guarda |
| `options_tree` (declaracao derivada, F3) | **0** | a tela nao lia a declaracao |

**E o outro lado estava em dia**, o que descarta as hipoteses alternativas: o `@plughub/schemas` do
mcp-server em execucao **tem** `options_tree` (logo o `form_get` entregou a subarvore, declarada como
arvore) e **tem** o `sanitizeCategoryPath` da F4 (logo um caminho completo teria chegado com os pontos
intactos, em 6 segmentos). A resposta submetida era mesmo a pasta.

### O que isto acrescenta a D11, e nao e detalhe

A `DLG-15` estava escrita como risco **prospectivo**, com o gatilho *"primeira forma com arvore
endereçada a canal nao-web"*. Ela acabou de ser **reproduzida ao vivo**, e por um caminho que a
redacao nao previa: o degrade nao veio de canal pobre, veio de **superficie desatualizada**. A licao
e sobre onde a recusa pode morar — **quem nao sabe desenhar arvore nem sempre se declara**, entao uma
politica que dependa de o consumidor se identificar tem um buraco do tamanho de um deploy atrasado.

### Estado da F6

Segue 🟡. O que a fase pede — *contagem por prefixo confere com os eventos emitidos* — e conferivel
mecanicamente hoje (`startsWith` acha 1 evento por pergunta), mas conferir prefixo sobre **pasta** nao
responde a pergunta: e a mesma familia da categoria `_a_b_` que a F2 removeu, uma serie que existe e
nao significa. Falta rebuildar o Console e um SEGUNDO contato que desça ate folha em `motivo` e marque
**2+ folhas dentro de uma pasta** em `servico` — sem multi-folha, a D5 e a metade multi da F2 nao sao
exercidas por contato real nenhum. Registrado como `DLG-21`.

## As-built do pre-requisito da F6 (2026-09-05)

A forma da arvore deixou de ser artefato so de gate: **`retencao_humano.on_human_end`**
passou a apontar para `dialog_wrapup_arvore_v1`. Foi decisao do dono (*"pode deixar no pool
retencao_humano para teste"*), e nao de seed — ligar muda o wrap-up que o atendente humano
daquele pool ve.

**A mudanca foi no agent-registry, nunca no YAML.** `PUT /v1/pools/retencao_humano` com o
bloco `hooks` inteiro (o schema aceita o campo por completo, nao por caminho), autenticado
pelo `x-service-token`. O YAML e seed-if-absent: editar pool ja semeado e no-op, e o DB vence.
Verificado relendo a rota EXATA que o bridge consome — `GET /v1/pools/{pool_id}` com
`x-tenant-id` —, nao a resposta do proprio PUT, que seria eco.

**Nao precisa de restart.** `get_pool_config` (bridge, `main.py:694`) declara-se **nao
cacheada** justamente porque config de pool muda em runtime; o proximo `agent_done` de um
humano de retencao ja le a forma nova.

**A semente acompanha, e isso e a regra da casa, nao zelo.** O `tenant_demo.yaml` foi
atualizado junto pelo mesmo motivo que o comentario de 2026-09-04 ja registrava ali: semente
que discorda do que roda faz o proximo `--wipe` reverter o comportamento sem que ninguem ligue
uma coisa a outra.

⚠️ **O `infra/dialog/README.md` estava mentindo, e ja antes desta mudanca.** A tabela
*"quem consome cada form"* dizia que o pool referenciava `dialog_wrapup_v1`, quando a config
viva apontava para `dialog_wrapup_arc12_v1` **desde 2026-09-04** — a linha foi escrita lendo o
YAML de antes. E o corolario do `CLAUDE.md` outra vez (*"para hooks, pergunte ao
agent-registry, NUNCA ao YAML"*), desta vez cometido num README que existe para orientar quem
nao sabe onde perguntar. As tres linhas foram reescritas, e a do `arvore` agora diz **onde
conferir o estado que vale**.

Falta da F6 so o que nao se fabrica: **um contato real** atravessando o pool com arvore de 3+
niveis e multi-folha, para a contagem por prefixo ser conferida contra os eventos emitidos.
## As-built da F5 (2026-09-05)

A taxonomia passou a ser autorável sem JSON. É a MESMA tabela de opções que já existia, com um
chevron por linha: descer um nível é abrir a linha, não trocar de tela. Sem drag-and-drop, porque
não há lib de DnD no projeto e a ordem se resolve com ↑↓ como no resto do editor.

**Editor e renderer desenham DIFERENTE, e isso é decisão:** o autor precisa ver a árvore inteira de
uma vez (tabela aninhada); quem responde precisa descer rápido sem se perder (colunas Miller, F3).
Duas perguntas, dois desenhos.

### As três regras que a tela impõe

- **D2** — pasta × folha derivado, e o aviso vem ANTES: apagar o ÚLTIMO filho converte a pasta em
  folha selecionável e o rótulo dela vira resposta. O `confirm` nomeia a pasta.
- **D6, `id` imutável** — o campo do `id` é editável **só na opção que não veio do documento
  carregado**. Comparar com o carregado (e não marcar linha a linha) é o que torna a regra estável
  através das reescritas imutáveis da árvore; o `id` de quem está no store tem série histórica
  atrás, e trocá-lo funde duas coisas na agregação por prefixo, em silêncio.
- **D6, aposentar ≠ apagar** — o botão de aposentadoria põe `active: false`: sai da OFERTA, fica no
  documento. Apagar continua existindo, para quem nunca publicou aquela folha.

Mais duas afordâncias que dizem a regra em vez de comentá-la: o `value` fica **desabilitado** numa
pasta (ela não é resposta), e `+ opção` **desabilita** ao atingir a profundidade da D3.

### Duas casas que estavam para trás, e apareceram ao ligar a tela

A F1 pôs `prefix` no schema, e o editor **não o oferecia** — nem no seletor de op (`AW_OPS`) nem no
tipo espelhado (`AskWhenOp`, em `dialog-hooks.ts`, que o platform-ui mantém porque não importa
`@plughub/schemas`). Espelho que perde um caso transforma ausência de afordância em código que
compila — a mesma família do `capture.kind` da F0, e a terceira ocorrência registrada.

⚠️ **Correção de uma afirmação minha, de horas antes:** eu disse que `ask_when` só era autorável
POR BLOCO e que a F5 dependeria de torná-lo por pergunta. **Falso** — o `AskWhenRow` já existe
dentro do editor de pergunta *e* do de statement; o de bloco é uma conveniência que fan-out. A
dependência que anunciei não existia.

### O que NÃO entrou

O botão *"+ pergunta"* na linha da opção — criar a pergunta IRMÃ já com `ask_when {op: prefix}`
preenchido, mostrada indentada sob a opção que a dispara. É a síntese que o mockup propôs, e não é
requisito desta fase. Fica como `DLG-18`, com a nota de desenho que a implementação revelou: a
versão do mockup editava prompt/`output_key` INLINE, e isso seria um **segundo editor** para o
mesmo nó. A vista deve mostrar um chip e levar ao editor existente, nunca duplicá-lo.

Gate: ramo **F** de `gate_dialog_capture_roundtrip.sh` — a árvore (3 níveis + `active:false`)
sobrevive a abrir-e-salvar. É o defeito do `capture.kind` (F0) um andar abaixo: projeção que
descarte a subárvore mata a taxonomia no PRIMEIRO save. Fixture sintética de propósito — nenhuma
forma publicada tem árvore ainda, e esperar o dado real deixaria o gate vermelho só DEPOIS de
destruir a taxonomia de alguém. Falseado: a reconstrução voltando a ser allowlist deixa F vermelho.

---

## As-built da F3 — PARCIAL (2026-09-05)

Entregue: a subárvore chega à superfície, a exigência é derivada, o Console desenha colunas Miller
e a D7 passou a valer. **Não** entregue: a recusa alta em canal pobre (D11) — ver abaixo.

### O achado que obrigou a fatia: a F1 abriu um buraco

`buildRender` mapeava opção como `{id, label}` e **descartava os filhos**. Depois da F1 uma forma
com taxonomia já podia ser autorada e publicada — e chegaria à superfície como lista PLANA de
raízes. Escolher *"Financeiro"* gravaria a **pasta** como resposta: dado errado, sem nada vermelho.
É o mesmo modo de falha que a D4 recusa no schema (render parcial de subárvore), reaparecendo um
andar abaixo. Fatia de segurança, não de conforto.

### `options_tree` é DERIVADO, e a recusa é de quem desenha

⚠️ **A D11 dizia *"`form_get` recusa quando o canal não suporta"*, e isso não é implementável ali:**
o `form_get` **não conhece o canal**, e dar-lhe um parâmetro de canal cruzaria a costura
conteúdo × canal, que é uma das quatro inegociáveis. Quem conhece o canal é o gateway.

A decisão fica de pé mudando de LUGAR, não de conteúdo: o `render` passa a carregar
`options_tree: boolean`, **derivado da presença de subárvore** (campo pode ser esquecido, a
estrutura não — que é exatamente o que a D11 pedia), e cada superfície decide. O Console desenha; as
demais precisam recusar.

**A recusa em si continua ABERTA** (`DLG-15`), e de propósito: ela depende de saber quais canais
sabem desenhar árvore, e isso é medição que ainda não fiz. Política contra mecanismo não medido é o
erro que este arco vem evitando desde a F2.

### Colunas Miller, e a resposta é o CAMINHO

Pasta abre coluna, folha seleciona; a resposta é o caminho de `id`s (`financeiro.cobranca.indevida`),
nunca o rótulo (D6). **Navegar LIMPA as marcações**, e é assim que a D5 (multi dentro de UMA pasta)
passa a valer **por construção** nesta superfície — não há como montar cesta cross-ramo clicando.

`active: false` é filtrado no `buildRender`: a folha aposentada sai da OFERTA e permanece no form.
⚠️ Pasta que fica sem filho ATIVO **não** emite `options: []` — emitir abriria uma coluna vazia na
tela; sem a chave ela vira folha selecionável, que é o comportamento derivado da D2.

### D7 — obrigatória por construção, e o motivo aparece

Pergunta com opções aninhadas **não pode ser enviada sem folha**: o botão bloqueia e a razão é
impressa ao lado da pergunta (o padrão que o renderer já usava para formato inválido — envio que
falha sem dizer onde devolve a adivinhação). Pergunta pulada por `ask_when` não conta: ela não foi
feita. Quem quiser permitir pular declara a folha de escape `nao_se_aplica`.

Gates: `dialog-render.test.ts` (subárvore preservada · `options_tree` derivado com controle
positivo · aposentada fora da oferta e sem `options: []`), duas mutações provadas; e o ramo **S7**
de `probe_dialog_json_surface.sh`, que mede o dry-run **AO VIVO** — falseado com o serviço
reconstruído a partir do código mutado, não só no fonte.

---

## As-built da F4 (2026-09-05)

**O achado 4 era metade da verdade, e a outra metade era pior.** Ele dizia que a regex do Arc 12
aceita 2–5 segmentos e que a profundidade decidida daria 8 — bloqueio. Verdade. Mas medindo o
emissor antes de subir o teto: **a hierarquia nunca chegava lá**. O sanitizador de
`deriveAgentEvents` era `replace(/[^a-z0-9_]+/g, "_")` sobre a resposta INTEIRA, e o ponto cai
nessa classe: `financeiro.cobranca.indevida` virava `financeiro_cobranca_indevida`, **um**
segmento. Subir a regex sozinha não teria efeito nenhum — a categoria nunca passava de 4.

Três consequências, todas mudas:

- a hierarquia morria no emissor, e o teto nunca era alcançado;
- `startsWith(category, "…motivo.financeiro.")` — o recorte da **D10** — não casaria com nada,
  porque não havia ponto onde procurar;
- a pasta `financeiro.cobranca` e uma folha chamada `financeiro_cobranca` colidiriam na MESMA
  categoria: duas coisas numa série só.

Hoje o saneamento é POR SEGMENTO (`split(".") → sanea → join(".")`), preservando o `.` como
separador. Medido antes de mudar: **0 de 49** opções publicadas têm ponto no `value`/`id`, então
nada muda para o dado existente — só passa a existir o que a árvore precisa.

### O teto é DERIVADO, e a derivação tem mecanismo

`AGENT_EVENT_CATEGORY_MAX_SEGMENTS = 8` = 3 (`pool.skill.metric`) + 5 (`DIALOG_OPTION_MAX_DEPTH`),
e a regex é construída a partir dele. A relação entre as duas constantes é **conferida por teste**,
não por comentário: quem mexer na D3 vê vermelho apontando para a regex. Duas casas afirmando o
mesmo número por prosa é exatamente como ele diverge.

**E o emissor recusa na origem** quando o caminho estoura o teto, nomeando no log — emitir ali
produziria um evento que o schema rejeita depois, longe dali, e o buraco na série não teria
endereço.

### `decomposeCategoryLevels` fica em QUATRO, e agora isso é fixado por teste

Do 5º segmento em diante o valor existe **só** em `category`. As colunas `l1..l4` servem ao ÍNDICE
(o `ORDER BY` da tabela começa por elas); o recorte hierárquico é por prefixo sobre a `category`
completa. O que seria defeito é alguém "consertar" `l4` para significar folha — aí ele significaria
coisas diferentes conforme a profundidade do ramo. Há teste que reprova essa mudança.

Gate: ramo **E** de `probe_multi_answer_events.sh`, que lê o teto DO SCHEMA (nunca escrito no
probe) e confere as duas metades: o caminho sobreviveu com os pontos, e o prefixo da pasta alcança
a folha. A mutação que colapsa o ponto reproduz o defeito literalmente.

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
| **F3** 🟡 | **Renderer Miller + recusa alta** *(parcial, 2026-09-05)* | ✅ colunas no Console · ✅ subárvore no `render` + `options_tree` derivado · ✅ D7. ❌ recusa em canal pobre (D11) — mudou de casa (o `form_get` NÃO conhece o canal) e virou `DLG-15`. D5 vale POR CONSTRUÇÃO nesta superfície (navegar limpa as marcações); a conferência no submit é `DLG-16`. | F1, F2 |
| **F4** ✅ | **Categoria de caminho** *(2026-09-05)* | Regex sobe para a profundidade da D3; `decomposeCategoryLevels` mantém 4 **declaradamente**; relatório por `startsWith` (D10). | F2 |
| **F5** ✅ | **Editor de árvore** *(2026-09-05)* | Autoria da árvore, aviso de pasta-que-virou-arquivo (D2), `id` bloqueado para edição e `active` como aposentadoria (D6). | F0, F1 |
| **F6** ✅ | **Validação** *(2026-09-05)* | Contato real com `motivo` = `tecnico.conectividade.lentidao` (folha, 3 níveis, 6 segmentos, teto 8) e `servico` = **duas folhas na MESMA pasta** (`cadastro.segunda_via` + `cadastro.troca_titularidade`) ⇒ **3 eventos**, provando que o emissor ITERA a lista. Guarda `prefix` discriminou; classificação → `outcome`; contagem por prefixo confere, e a divergência **marçações 2 × contatos 1** na pasta `cadastro` existe agora em dado real. Eventos carimbados com `dialog_form_id` + `version` (D14). Gate: `probe_agent_event_form_stamp.sh`. | todas |


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
