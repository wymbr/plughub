# ADR: `masked` deixa de ser booleano e passa a nomear um TIPO do catálogo

**Status:** Aceito — 2026-08-29. **T1 e T2 entregues** (ver `CHANGELOG.md`); T3 é a próxima. T7, que fecha o ramo `boolean`, é a única não reversível e segue não iniciada.
**Data:** 2026-08-29
**Componentes:** `packages/schemas`, `packages/skill-flow-engine`, `packages/mcp-server-plughub`,
`packages/orchestrator-bridge`, `packages/platform-ui`, `packages/agent-registry`,
`packages/dialog-api`
**Relacionado:** [`adr-contextstore-allowlist.md`](adr-contextstore-allowlist.md) *(este ADR é o
consumidor do catálogo que a V2 criou; a V2b foi seu pré-requisito)* ·
[`adr-message-masking.md`](adr-message-masking.md) ·
[`adr-deploy-time-content-snapshot.md`](adr-deploy-time-content-snapshot.md) *(ambos tocam o bloco
`render`, mas em chaves diferentes — a dependência de ordem CAIU, ver D7 emendada)* ·
[`docs/guias/masked-input.md`](../guias/masked-input.md)
**Censo que o precede:** `infra/test/q_masked_declaration_census.sh` (F0, executada em 2026-08-29)

---

## 0. Sumário da decisão

`masked: true` é uma declaração **anônima**: diz *"esconda"* e não diz **o quê**. Com isso, tudo o
que depende de saber o quê — máscara por papel, regra de canal, classe LGPD — fica sem lugar onde
morar e acaba decidido **por formulário**. Conformidade não pode ser por formulário: se cada form
decide, existem N políticas de CPF no tenant e, como sempre, vale a mais permissiva.

A decisão é fazer `masked` **nomear um tipo** do catálogo `masking.types` (D1 do ADR da allowlist),
que já carrega as três dimensões (`formato` × `mascara` × `lgpd`). O tipo passa a ser a declaração;
o formulário passa a ser o **uso**.

Duas coisas que este ADR deliberadamente **não** faz, e a razão de cada uma está medida:

- **não absorve a DETECÇÃO** (§1.6 e D5) — declaração e detecção respondem perguntas diferentes em
  momentos diferentes, e fundi-las devolveria duas respostas para *"que máscara este campo usa?"*,
  que é exatamente o defeito que a V2b acabou de fechar;
- **não torna a persistência dependente do tipo** (D4) — *"valor mascarado nunca entra em
  `pipeline_state`"* segue absoluto, porque um tipo capaz de optar por persistir é um tipo capaz de
  derrubar a invariante sem que ninguém revise o deploy.

---

## 1. Contexto

### 1.1 O defeito: a declaração é anônima, então a política vira escolha do autor do form

Hoje `masked` é `z.boolean().optional()`. O autor de um formulário decide *se* mascara. Não existe
lugar em que ele declare **que dado é aquele** — logo não existe lugar em que a plataforma imponha
que CPF se comporte como CPF em todo lugar. As três dimensões que o catálogo já modela ficam órfãs
no caminho da declaração:

| dimensão | mora no catálogo desde a V2 | alcança um campo `masked: true`? |
|---|---|---|
| máscara por papel (`mascara.by_role`) | sim | **não** — o campo não diz qual tipo é |
| regra de canal (`mascara.display`) | sim (desde a V2b, uma casa só) | **não** — idem |
| classe LGPD (`lgpd`) | sim | **não** — idem |

O booleano não é *pouco* — ele é de **outra grandeza**. `true` responde *"esconder?"*; o produto
precisa responder *"o que é isto, e portanto quem pode ver, em que canal, sob que classe?"*.

### 1.2 Achado — quatro declarações, UM resolvedor, UM normalizador *(favorável)*

Medido pela F0. A declaração aparece em **2×2**, simétrica entre os dois schemas:

| escopo | skill (`MenuStep`) | dialog (`DialogForm`) |
|---|---|---|
| nível do nó/step | `schemas/src/skill.ts:476` | `schemas/src/dialog.ts:314` (`QuestionNode.masked`) |
| nível do campo | `schemas/src/skill.ts:503` | `schemas/src/dialog.ts:258` (`DialogField.masked`) |

Mas a **precedência** (*campo vence step*) é aplicada **uma vez só**, em
`skill-flow-engine/src/masking-policy.ts` (`isFieldMasked` + `computeMaskedFieldIds`, com arquivo de
teste próprio), e o `form_get` (`mcp-server-plughub/src/tools/dialog.ts:110-140`) **achata** as duas
declarações do DialogForm em `render.fields[].masked`, que o step `menu` consome por referência
(`fields: "$.pipeline_state.dialog.render.fields"`).

Isto é o achado que torna o arco barato: **a tipagem muda a declaração e o normalizador; não muda a
cauda de consumidores.** Quem está a jusante recebe `masked_fields[]` — uma lista de ids — e só
precisa mudar onde o **tipo** tiver de chegar (redação por papel, regra de canal).

### 1.3 Achado — a submissão de form ENTRA na transcrição durável, e o não-declarado vai em CLARO

Medido ao vivo em `plughub_demo.messages` (1 564 linhas de testemunha; 439 recados de cliente):

```
[Formulário: {"email": "abc@teste.com", "senha": "••••••", "codigo_2fa": "••••••"}]
```

`senha` e `codigo_2fa` estão redigidos porque **foram declarados**. `email` está em claro porque
**não foi** — e o catálogo tem `email_addr`, com `detect_pattern` e `lgpd=pessoal`.

Este é o argumento central do ADR, e ele é medição, não retórica: **a política existe, e o caminho
da declaração não a alcança.** Note o que o caso mostra e o que **não** mostra — ele mostra que a
declaração anônima não carrega classe LGPD; não mostra que a declaração deveria detectar (§1.6).

### 1.4 Achado — a quarta superfície ignora `masked`; armadilha ARMADA, dano hoje zero

`platform-ui/src/modules/agent-assist/components/DialogFormRenderer.tsx` tem **zero** ocorrências de
`masked`. É a superfície genérica de collect-form do Console (aprovação, wrap-up, R0), e um valor
declarado masked ali iria para `payload.answers` → `workflow_resume` → `pipeline_state`, contra a
invariante do `CLAUDE.md`.

**Exposição e dano são grandezas separadas, e aqui divergem:** dos **10** DialogForms do tenant,
**1** declara campo masked (`dialog_limite_solicitacao`, o `cvv`), e ele **não** é dos que chegam ao
Console — aprovação, wrap-up e demo têm zero. Dano hoje **0**; dispara no primeiro form masked
roteado ao Console. A tipagem **agrava**: um campo tipado e ignorado é um vazamento tipado.

### 1.5 O parque a migrar é pequeno, e isso decide a estratégia

**Seis** declarações vivas — e a contagem foi feita **na autoridade, não no arquivo**, porque nesta
base o YAML é *seed-if-absent* e o DB vence:

| origem | quantidade | onde foi medido |
|---|---|---|
| `skill_auth_form_v1` (YAML `agente_auth_form_v1.yaml:82,86,150,154`) | 4 | `skills.flow` no agent-registry |
| `skill_auth_ia_v1` (YAML `agente_auth_ia_v1.yaml:66`) | 1 | idem |
| DialogForm `dialog_limite_solicitacao` (`cvv`) | 1 | dialog-api |

**Três vias concordam**, o que é o que dá confiança à contagem: o arquivo diz 5 declarações inline,
o `flow` de produção no agent-registry diz **as mesmas 5** nas mesmas 2 skills, e os
`pool_skill_slots` — o que o bridge de fato executa — carregam `masked` em **2 de 37** snapshots, os
dois correspondentes. `flow_draft` tem **zero**, então não há rascunho com declaração fora do censo.

Contexto: o DB tem **44** skills contra **42** YAMLs (diferença não investigada, fora de escopo — não
afeta o censo, já que as duas com `masked` estão nos dois lados). O `cvv` já viaja pelo caminho do
`form_get`, o que confirma §1.2 na prática. Um parque deste tamanho torna a migração um detalhe e
desloca o risco para onde ele realmente está: a **forma** da união e o **fechamento** dela.

### 1.6 O que fica FORA, e por quê — declaração ≠ detecção

Registrado no `TODO.md` como arco próprio. A distinção é de natureza, não de prioridade:

| | declaração (`masked:`) | detecção (regex) |
|---|---|---|
| pergunta | *"este campo VAI receber segredo"* | *"este texto CONTÉM algo parecido com PII"* |
| quando se sabe | **antes** do valor existir | só **depois** |
| alcance | o valor inteiro | um trecho |
| o que pode fazer | **suprimir na origem** | tokenizar post-hoc |

Não são duas implementações da mesma coisa, e nenhuma substitui a outra: rodar regex num campo
`senha` é inútil (senha não se parece com CPF), e só a detecção poderia ter pego o `email` de §1.3,
que ninguém declarou. **O que as duas compartilham é o TIPO** — e a detecção já o lê desde a V2
(`DEFAULT_MASKING_RULES` é derivada de `DEFAULT_DATA_TYPE_CATALOG`).

O problema da detecção, medido, é de **colocação** e não de mecanismo: um único call site
(`mcp-server-plughub/src/tools/session.ts:472`), e o caminho que vazou não passa por ele porque a
submissão é escrita pelo bridge, em Python. Isso é arco próprio, com F0 próprio.

### 1.7 Blast radius

Contido, e é consequência de §1.2 e §1.5: 4 pontos de schema · 1 resolvedor · 1 normalizador ·
6 declarações no parque · 1 superfície a consertar (§1.4). Nenhum consumidor a jusante muda enquanto
o tipo não precisar chegar até ele.

---

## 2. Decisão

### D1 — `masked` aceita `boolean | string`, e **`true` é um TIPO, não a ausência dele**

```ts
masked: z.union([z.boolean(), z.string()]).optional()   // string = id de tipo do catálogo
```

O ponto que faz esta decisão valer: **`true` resolve para um tipo real do catálogo — `opaque` — e não
para um ramo sem tipo.** `opaque` é o nó de **máxima restrição**: nenhum papel vê, nenhum canal
exibe, `lgpd` declarada, e **sem `detect_pattern`** (não é detectável — é declarado por definição).

A alternativa óbvia (manter um ramo "mascarado porém sem tipo") reintroduziria exatamente o default
permissivo que o arco existe para eliminar, e o reintroduziria no lugar mais caro: como *ausência*,
que é o valor mais barato de produzir e o mais difícil de contar. Com `opaque`, **não existe ramo
sem política**; existe um tipo cuja política é *"não confio em ninguém"*.

`false` continua significando *"não mascarar"*, sem mudança.

### D2 — Uma passada, um resolvedor, dois resultados

A precedência é aplicada **uma vez**, como já é hoje. `masking-policy.ts` passa a expor:

```ts
resolveMaskedFields(stepMasked, fields, implicitFieldId)
  → { ids: string[], types: Record<string, string> }   // id do campo → id do tipo
```

`computeMaskedFieldIds` fica como derivação (`.ids`) **contada**, e sai quando os call sites
migrarem — não por decreto. **Nunca duas funções que reapliquem a precedência**: os ids dirigem a
renderização de canal (overlay de senha), que não quer saber de tipo; os tipos dirigem redação e
classificação, que querem. Duas perguntas, dois resultados, **uma** resolução.

### D3 — Duas guardas, posturas diferentes, ambas *fail-closed*

| momento | comportamento com tipo desconhecido |
|---|---|
| **deploy** (`promote`, publish de form) | **RECUSA** — a referência é validada contra o catálogo do tenant |
| **runtime** | resolve para `opaque` **e loga NOMEANDO** o tipo ausente e o campo |

Nenhuma das duas cai para o lado permissivo. A assimetria é deliberada: recusar no deploy é barato e
tem quem leia; recusar em runtime derrubaria um atendimento em curso por erro de config, então lá a
degradação é para o **mais restritivo**, com o motivo dito. Isso é o oposto de
`catch { /* não-fatal */ }`, e é o padrão que o `CLAUDE.md` pede.

### D4 — O tipo decide EXIBIÇÃO e CLASSE. **Nunca** persistência

`masked ⇒ o valor nunca entra em `pipeline_state`, Redis, stream ou log` permanece **absoluto e
independente do tipo**. O tipo decide quem vê o quê, em que canal, e sob que classe LGPD — não
decide *se* o valor sobrevive.

A razão é de mecanismo, não de gosto: um tipo capaz de optar por persistir permitiria derrubar a
invariante **editando config**, sem revisão de deploy e sem diff em skill nenhum. É a mesma família
do portão de namespace na persistência que o D4 do ADR da allowlist recusa.

### D5 — A detecção fica fora deste ADR

Por §1.6. Este ADR não move o call site da detecção, não cria um segundo detector e não muda o
`MaskingService`. O que ele faz é garantir que os dois eixos **apontem para o mesmo catálogo** — o
que já é verdade do lado da detecção e passa a ser do lado da declaração.

### D6 — A quarta superfície honra `masked` **antes** de a tipagem chegar ao Console

§1.4 é dívida hoje e regressão amanhã. `DialogFormRenderer` passa a honrar a declaração (não
renderizar em claro, não devolver no `payload.answers`), e essa fase é **pré-requisito** de a
tipagem alcançar o Console. Ordem inegociável.

### D7 — Ordem com a S1 do ADR de snapshot: **a dependência CAIU**

*Emendada em 2026-08-29, no dia seguinte à redação, ao medir a S1 para implementá-la.*

A versão original dizia *"S1 primeiro"*, porque as duas fases mexeriam no mesmo bloco `render` e
aditiva vem antes de mutativa. Duas medições derrubaram a ordem:

- **a S1 está BLOQUEADA** — duas de suas premissas foram refutadas
  ([`adr-deploy-time-content-snapshot.md`](adr-deploy-time-content-snapshot.md) §D6): `render.captures`
  perde o mapa opção→nota, e o único chamador de `segment_outcome_record` não tem `form_get`, logo não
  há `render` no `pipeline_state` dele. Manter a ordem faria a tipagem esperar por uma fase sem data;
- **a colisão era mais fraca do que eu afirmei.** As duas mexem em **chaves diferentes** do mesmo
  objeto: a S1 em `render.captures`, a tipagem em `render.fields[].masked`. Objeto comum não é campo
  comum — e chamar isso de colisão de contrato foi imprecisão minha, não medição.

**Fica:** nenhuma ordem entre S1 e T2. Se as duas forem implementadas, a S1 continua sendo aditiva e a
tipagem mutativa, mas em campos que não se tocam. As ordens internas deste ADR (T1→T2, T4 antes do
Console, T6→T7) **não** dependiam disso e seguem valendo.

### D8 — O fechamento do ramo `true` é por CONTADOR, nunca por decreto

O ramo `boolean` sai quando o contador de `masked: true` no parque zerar, medido por gate — o mesmo
mecanismo que autorizou a V2b, e pelo mesmo motivo: prazo não sabe se ainda há usuário; contador
sabe. Enquanto não zerar, `true` é **legítimo** e resolve para `opaque`; não é dívida silenciosa.

---

## 3. Alternativas consideradas

**(a) Campo novo `masked_type`, mantendo `masked: boolean`.** Recusada: cria a possibilidade de
`masked: false` com `masked_type: "cpf"` — dois campos que podem se contradizer, e alguém teria de
escrever a regra de qual vence. Um campo que pode discordar de si mesmo é a forma do defeito que
este arco fecha.

**(b) Quebrar o booleano de vez (`masked: string` obrigatório).** Recusada por custo/benefício: o
parque é de 6 declarações (§1.5), então a quebra é pequena — mas ela obriga a migrar **antes** de
qualquer benefício aparecer, e transforma um arco incremental num *big bang* sem fase de medição. A
união com fechamento por contador (D8) chega ao mesmo lugar com uma ordem que se pode interromper.

**(c) `masked` referenciar a regra de MASCARAMENTO diretamente, e não o tipo.** Recusada: seria
declarar *forma* (`last_4`, `hidden`) no formulário — que é precisamente a escolha por-formulário
que o ADR remove, só que com vocabulário mais fino. O formulário declara **o que o dado é**; o
catálogo decide **como se comporta**.

**(d) Tipar também a detecção neste mesmo arco.** Recusada por §1.6 e D5.

---

## 4. Consequências

**Ganhos.** Uma política de CPF por tenant, não por formulário. Máscara por papel e regra de canal
passam a alcançar o campo declarado. Classe LGPD deixa de ser inferida pelo leitor. E a tela de
masking passa a ser o lugar onde a política **de fato** se edita — fechando a invariante *"todo campo
de config é UI-editável"* para este eixo.

**Custos aceitos.** Um campo com união de tipos no schema, até D8 fechar. Uma dependência nova de
**deploy** sobre o catálogo do tenant (D3), que precisa de mensagem de recusa legível. E um tipo
`opaque` que, mal explicado, vira lixeira — por isso ele é o **mais restritivo**, e não o mais
permissivo: cair nele nunca é um atalho confortável.

**O que piora antes de melhorar.** Enquanto §1.4 não estiver consertada, tipar não muda o Console —
e por isso D6 é ordem, não sugestão.

**Consequência sobre o oráculo da V2 — e a correção do meu próprio exagero.**

Escrevi aqui, ao entregar a T1, que este ADR *"APOSENTA o critério de alcançabilidade"* e que a T2
**não poderia entrar** sem um critério de uso no lugar. **Exagerado, e a diferença muda o plano.**

O raciocínio era: se `masked` pode nomear qualquer id, todo tipo vira alcançável por declaração e a
pergunta *"alguém consegue chegar neste tipo?"* responde "sim" para tudo. Isso **só valeria se
"declarável" fosse propriedade automática de todo tipo** — e não é. O oráculo continua reprovando
`iban` novo, porque para entrar no catálogo sem `detect_pattern` e fora do enum `DataCategory` ele
precisa de `declared_only: true`, que é **ato deliberado de quem escreve o tipo**, não consequência
da tipagem. A proteção não é removida.

O que **de fato** aparece é um buraco menor e diferente: `declared_only` vira a porta pela qual se
contrabandeia um tipo qualquer — marque-o e o oráculo cala. Isso não é "critério vazio"; é uma
exceção que precisa de contrapeso.

**O contrapeso é o USO, e ele só é mensurável depois da T2** (antes, nada pode nomear um tipo, então
todo `declared_only` tem uso zero por construção, inclusive o `opaque`). Logo o critério de uso
**não é pré-requisito da T2 — é consequência dela**, e a red condition natural é *"tipo
`declared_only` com zero declarações no parque"*, que só passa a significar algo a partir da **T6**
(quando as 6 declarações migram). **Ordem corrigida: T2 pode entrar; o critério de uso entra com a
T6**, e até lá o parque é medido por `q_masked_declaration_census.sh`.

---

## 5. Fases

| fase | entrega | reversível |
|---|---|---|
| **T0** | **Contar o parque** e congelar a linha de base: 6 declarações (§1.5), 1 form com campo masked, 42 YAMLs. Gate que reprova declaração NOVA sem tipo depois de T2 | sim |
| **T1** | ✅ **FEITA em 2026-08-29.** Tipo `opaque` no catálogo (código + seed), **sem nenhum consumidor novo**. Entregou mais do que a linha previa, e as duas adições são exigência do oráculo, não enfeite: **(a) `DataType.declared_only`** — o oráculo da V2 trata como órfão todo tipo sem `detect_pattern` cujo id não seja `DataCategory`, e foi medido que ele **reprovaria `opaque`** (`orfaos=["opaque"]`, rodado na imagem antes de mudar). Pôr `opaque` no enum `DataCategory` foi **recusado**: campo opaco é SUPRIMIDO, nunca tokenizado, logo seria membro de enum que nenhum produtor emite — o fantasma `iban`/`passport` de volta. A marca é **por tipo**, não lista de exceção no oráculo, para que ele siga capaz de reprovar. **(b) `LgpdClass.nao_classificado`** — nenhuma das 5 classes servia com honestidade: `none` afirma *"não é dado pessoal"*, `sensivel`/`credencial` são afirmações jurídicas que ninguém fez. Mesmo padrão do balde `unknown` do rollup de capacidade: classe própria e contada, nunca dobrada numa real | sim |
| **T2** | ✅ **FEITA em 2026-08-29.** União nos 4 pontos (`MaskedDeclarationSchema`); `resolveMaskedFields` devolve `{ids, types}` numa passada, com `isFieldMasked`/`computeMaskedFieldIds` virando **derivações** — a precedência vive em `maskedFieldType`, uma casa só. `true` → `OPAQUE_DATA_TYPE_ID`, constante única. **Um consumidor teve de mudar contra a linha "nada a jusante"**, e por segurança, não por completude: `form_get` achatava com `f.masked === true`, o que faria `masked: "cpf"` virar `false` e o campo sair **DESmascarado** — a união sem consertar o achatamento cria fail-open. Mesmo motivo em `menu.ts` (`isStepMasked`, porque o `waitingMeta` lido pelo bridge como `any_masked` é contrato booleano). Provado ao vivo no agent-registry: **200** para `masked: "cpf"`, **422 `invalid_union`** para `masked: 7`, payloads diferindo só nesse campo. 8 testes novos, e a mutação (string→não-mascarado, o comportamento pré-T2) derruba **6** deles | sim |
| **T3** | ⚠️ **REESCRITA em 2026-08-29, ao medir para implementar.** A linha original dizia *"máscara por papel e regra de canal alcançam a submissão de form"* e **metade disso é impossível por invariante**: `by_role: last_2` e `display_screen: display_partial` exigem TER o valor para derivar o parcial, e num campo DECLARADO ele nunca persiste (§D4). Derivar e gravar seria gravar um derivado do segredo — em `senha`, os últimos caracteres da senha. ⇒ **`mascara.*` é dimensão do caminho de DETECÇÃO** (onde existem token e vault); no caminho da DECLARAÇÃO só `lgpd` e "suprimido" são alcançáveis. Segundo achado: **três casas** produzem `••••••` para campo de form — `orchestrator-bridge/main.py:352`, `channel-gateway/adapters/webchat.py:839` e `platform-ui/AgentAssistPage.tsx:398` —, então mudar o PLACEHOLDER numa delas cria divergência entre caminhos. ⇒ **T3 = o tipo viaja e é REGISTRADO como DADO**, não embutido no texto: mesma forma que a detecção já usa (`session_stream_events.masked_categories`). Placeholder intocado, as três casas seguem coerentes, e a consolidação delas vira fase própria | sim |
| **T4** | `DialogFormRenderer` honra `masked` (D6). **Fecha a armadilha de §1.4** | sim |
| **T5** | Guarda de deploy recusa tipo desconhecido; runtime resolve para `opaque` com log nomeado (D3) | sim |
| **T6** | Migrar as 6 declarações do parque; contador de `masked: true` decai | sim |
| **T7** | Fechar o ramo `boolean` **quando o contador zerar** (D8) | **não** |

**Ordens inegociáveis:** T1 antes de T2 · **T4 antes de a tipagem alcançar o Console** · T6 antes de
T7. *(A dependência da S1 do ADR de snapshot CAIU em 2026-08-29 — ver D7 emendada: a S1 está
bloqueada e a colisão era em chaves diferentes do mesmo objeto, não no mesmo campo.)*

---

## 6. Como isto pode ficar vermelho

O ADR está errado se alguma destas se sustentar:

- **se `opaque` virar o valor comum.** O desenho aposta que declarar o tipo certo é mais fácil do que
  cair no restritivo. Se depois de T6 a maioria das declarações for `opaque`, a tipagem não comprou
  política — comprou cerimônia, e o certo é rever o catálogo (faltam tipos), não o ADR;
- **se a guarda de deploy (D3) travar edição legítima.** Catálogo por-tenant + validação no promote
  significa que um tenant sem o tipo semeado não consegue publicar. Se isso acontecer na prática, a
  recusa está no momento errado e o lugar passa a ser o publish do form;
- **se `render.fields[].masked` tiver um segundo produtor** que ninguém contou. §1.2 afirma um
  normalizador único; um segundo faria a precedência ser aplicada duas vezes, e a mais permissiva
  venceria — a assinatura do defeito que a V2b fechou;
- **se a persistência precisar do tipo.** D4 afirma que não. Se aparecer requisito legítimo de um
  tipo que persiste (retenção regulatória, p.ex.), D4 cai — e aí é decisão nova, com o portão de
  namespace de volta à mesa, não um ajuste;
- **se o parque estiver maior do que §1.5 mediu.** *Risco levantado e depois FECHADO na própria
  redação deste ADR:* a primeira contagem foi feita no arquivo, que nesta base não é autoridade
  (*seed-if-absent*, o DB vence) — e essa é exatamente a população que o repositório já viu divergir.
  Remedido no agent-registry e no dialog-api: **as três vias concordam** (arquivo 5 = `skills.flow` 5
  = 2 de 37 slots executando, `flow_draft` zero). O risco **permanece** para outro tenant, cujo
  catálogo e cujas skills não foram medidos: **T0 mede no agent-registry por tenant, nunca no
  arquivo.**

---

## 7. O que este ADR NÃO decide

- **Onde a detecção roda** (§1.6). Arco próprio, F0 próprio, registrado no `TODO.md`.
- **O `catch` mudo de `session.ts:485`**, que entrega conteúdo cru quando a detecção falha. Registrado
  como defeito de segurança à parte; consertá-lo não depende deste ADR nem o bloqueia.
- **A proveniência de mascaramento na transcrição durável** — `masked`/`masked_categories` não
  existem em nenhuma das quatro camadas (schema canônico, produtor, parser, DDL). É pré-requisito de
  **medir cobertura de detecção**, não de tipar declaração.
- **As portas Python de masking** (`quality-ingest/masking.py`, `channel-gateway/adapters/webhook.py`),
  que seguem cópias literais. Fase própria já declarada no ADR da allowlist.
- **O desacoplamento `platform-ui × @plughub/schemas`**, que faria o front ler os tipos em vez de
  redefini-los. Registrado como dívida no `TODO.md`; nada aqui depende dele.
