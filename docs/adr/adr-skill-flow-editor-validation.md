# ADR: Feedback de validação no editor de skill-flow — AFORDÂNCIA ≠ VEREDICTO

**Status:** Proposto
**Data:** 2026-08-29
**Componentes:** `packages/platform-ui` (`modules/skill-flows/SkillFlowsPage.tsx` — o editor),
`packages/agent-registry` (`routes/skills.ts`, `validators/skill.ts`, `app.ts` — o verificador),
`packages/schemas` (`skill.ts` — a fonte de verdade), `packages/sdk` (`certify/flow.ts`),
`packages/skill-flow-engine` (`engine.ts` — `validateFlow`).
**Relacionado:** [`adr-dialog-tree-options.md`](adr-dialog-tree-options.md) (o outro lado da mesma pressão de
autoria), `docs/arcos/platform-ui.md` § editor, `CLAUDE.md` § Postura de Engenharia
(*"um teste que não pode reprovar é pior que teste nenhum"*).

---

## Contexto

O editor de skill-flow é um `<Editor defaultLanguage="yaml">` que **não conhece nenhum dos 17 tipos de step**
(`SkillFlowsPage.tsx:840-861`; grep por `escalate|delegate|collect|…` no arquivo só devolve `catch` de
JavaScript). A única validação local é parse sintático com `js-yaml` (`:106-111`, `:631`). Todo erro estrutural
só aparece como **422 do servidor**, depois do save.

A proposta que originou este ADR era *"JSON Schema no Monaco: uma dependência e um passo de build"*.
**A medição refutou o custo e, mais importante, refutou o papel.** Este ADR registra as duas correções, porque
a segunda é a que decide o desenho.

---

## As duas correções que a medição impôs

### Correção 1 — JSON Schema não é o barato aqui

Três fatos empilhados, cada um suficiente:

- O Monaco vem por **CDN via AMD loader** (`@monaco-editor/loader@1.7.0`, `package-lock.json:875-877`);
  `monaco-editor` não é dependência direta, e **`vite.config.ts` não tem configuração nenhuma para Monaco**.
- **`monaco-yaml` não existe** no projeto, e é ele — não o Monaco — que aplica schema a YAML. O
  `monaco.languages.json.jsonDefaults` **não atua em `language="yaml"`**.
- `monaco-yaml` exige Monaco **bundlado** e webworkers próprios, na **mesma instância** de `monaco-editor`.
  Com o loader de CDN, isso é **migração de bundling**, não `npm i`.

Ou seja: a parte que eu chamei de barata (gerar o schema — Zod 3.25.76 + `zod-to-json-schema@3.25.2`, já
presente como transitivo em quatro lockfiles) é mesmo trivial; o **consumo** é que não é.

### Correção 2 — JSON Schema não pode ser o VEREDICTO, e essa é a razão de fundo

`SkillSchema` é um **`ZodEffects`** — tanto que `validators/skill.ts:16-17` acessa `_def.schema` por cast para
derivar o `UpdateSkillSchema`. E `SkillFlowSchema` carrega **dois `.refine`** (`schemas/src/skill.ts:1318-1324`):
`entry` referencia step existente, e o flow tem ao menos um `complete|escalate`.

**`zod-to-json-schema` não representa refinements.** Um JSON Schema derivado do `SkillSchema` diria **válido**
para um flow sem `complete` — que o servidor recusa com 422. Isso é a família *valor plausível* na pior forma:
um verde local que contradiz o verde que importa, e ninguém fica vermelho.

> **Regra que este ADR fixa:** JSON Schema serve para **afordância** (autocomplete, hover, forma). O
> **veredicto** vem de quem executa a validação real. Fundir os dois cria duas respostas para *"isto é
> válido?"* — e, pelo padrão que este repositório já pagou várias vezes, **a mais permissiva é a que vale**.

Consequência de sequenciamento: se a afordância depende da migração de bundling e o veredicto não, **o
veredicto vem primeiro** — e ele entrega sozinho o que motivou a ideia (*"parar de descobrir erro por 422"*).

---

## Decisões

### D1 — Dois papéis, nunca fundidos

| papel | pergunta | quem responde | quando |
|---|---|---|---|
| **afordância** | *"que campos existem aqui?"* | JSON Schema derivado do Zod | ao digitar (fase 2) |
| **veredicto** | *"o servidor aceita isto?"* | o próprio verificador do servidor | debounce, antes do save |

### D2 — O veredicto é do SERVIDOR, por dry-run

`POST /v1/skills/validate` — mesmo corpo do `PUT`, **sem persistir**. Devolve `{ valid, errors[] }`.

Motivo de não reimplementar no browser: além do problema dos refinements (Correção 2), o platform-ui **não
declara `@plughub/schemas`**, **não há workspaces** no monorepo (npm `file:` links, lockfile por pacote), o
`Dockerfile` do platform-ui copia **só** `packages/platform-ui/`, e o risco de **dual-instance de Zod** já está
documentado no código: `agent-registry/src/app.ts:58-61` faz duck-typing em vez de `instanceof ZodError`
exatamente por isso. Rodar Zod no browser é atravessar tudo isso por um ganho que o dry-run já dá.

### D3 — O dry-run e o `PUT` rodam o MESMO verificador, extraído

Um `validateSkillPayload()` único, chamado pelos dois. Se divergirem, volta-se ao padrão de duas respostas.

**Isto é mudança de comportamento, não refactor:** hoje o `PUT` roda `CreateSkillSchema.parse`
(`skills.ts:210`) + `validateMaskedBlock` (`:226-229`) e **não** roda `validateFlow`. A detecção de **ciclo
não-guardado** vive no engine (`engine.ts:270-343`, chamada em `:430`), ou seja, **hoje um flow com ciclo
não-guardado passa no save e só explode em execução**. O verificador único move essa checagem para a esquerda.

**Pré-requisito medido, não opcional:** rodar `validateSkillPayload()` sobre os **42 YAMLs existentes** antes de
ligar. Se algum reprovar, o arco decide caso a caso — nunca se liga um verificador mais estrito sem contar quem
ele quebra.

### D4 — Erro ESTRUTURADO, e o `detail`/`details` unificado

Hoje o 422 tem **duas formas incompatíveis**: erro Zod devolve `{error:"validation_error", detail: issues[]}`
(`app.ts:57-68`, **issues cruas**), e erro de bloco masked devolve `{error:"invalid_masked_block", details: [...]}`
(`skills.ts:226-229`) — `detail` singular × `details` plural.

O front lê só `body.detail` (`SkillFlowsPage.tsx:608`), então **o erro de bloco masked é hoje invisível ao
autor**: cai em `undefined` e vira um "HTTP 422" mudo. É defeito vivo, pequeno e independente — sai na F0.

Forma alvo: `{ valid: false, errors: [{ code, path, message, step_id? }] }`, com `path` no formato do YAML
(`steps[3].on_success`), que é o que a F3 precisa para virar marker de linha.

### D5 — Degradação do dry-run é ALTA

Endpoint fora do ar ⇒ o editor diz **"não validado"**, nunca fica verde por omissão. Um painel vazio é
indistinguível de "sem erros", e é assim que um verificador vira decoração.

### D6 — `monaco-yaml` fica FORA da v1

Pelo custo da Correção 1. Reavaliar **depois** que o dry-run provar valor — e aí como arco próprio de bundling
(Monaco bundlado + workers + `vite.config.ts`), não como item de um arco de validação.

Sem language service, **um JSON Schema gerado não teria consumidor**. Por isso a geração também sai da v1: é a
peça que só faz sentido junto do consumidor dela.

### D7 — `onMount` passa a receber `monaco`

`SkillFlowsPage.tsx:846` hoje é `onMount={editor => …}` e **descarta o segundo argumento**. Sem esse handle não
existe `setModelMarkers`, nem configuração de language service depois. Mudança de uma linha, e é pré-requisito
de tudo que vem.

### D8 — Refs (`$.pipeline_state.*`, `@ctx.*`) ficam fora de escopo

Para JSON Schema são `string`. Autocomplete útil ali exigiria conhecer a forma do `pipeline_state` **em cada
ponto do grafo** — inferência de fluxo, mecanismo inteiramente distinto. Declarado fora, não esquecido.

### D9 — O desacoplamento `platform-ui × @plughub/schemas` NÃO é revertido aqui

Medido: o platform-ui redefine à mão `Skill`, `Pool`, `AgentType`, `Session` e derivados em
`src/types/index.ts` (~960 linhas) **e** de novo, localmente e divergente, em dezenas de arquivos — `Pool` e
`Skill` reaparecem em `AgentFlowDeployPage.tsx:28,35` (a tela vizinha do editor), `PoolConnectionStatus` está
duplicado dentro do mesmo módulo (`agent-assist/types.ts:287` × `useMultiPoolWebSocket.ts:25`), e **nenhum tipo
de step do flow é tipado** — o flow é `Record<string, unknown>` de ponta a ponta (`types/index.ts:310-328`).

**Não existe justificativa escrita.** Não há ADR; `docs/standards/frontend-architecture.md` manda criar tipos
locais por módulo (linhas 48, 244-247) mas nunca menciona `@plughub/schemas` nem explica a não-reutilização. É
convenção herdada, não decisão.

Registrar é o que cabe aqui. **A D2 existe em parte porque esse desacoplamento existe** — e resolvê-lo é arco
próprio, com o dual-instance de Zod e a ausência de workspaces no escopo.

---

## Achados de medição

1. **O erro de bloco masked é invisível ao autor** — `detail` × `details` (D4). Vivo, pequeno, independente.
2. **O `PUT` não detecta ciclo não-guardado** — a checagem existe (`engine.ts:270`) mas só roda na execução
   (D3). Um flow com ciclo salva verde e falha depois.
3. **Existem DUAS `validateFlow`**, e a melhor para UI não é a que o engine usa: `engine.ts:270` lança e
   acumula violações numa string (`:335-342`); `sdk/src/certify/flow.ts:91` **devolve resultado estruturado**
   (`FlowValidationResult`) e tem testes próprios. A segunda é a candidata natural do verificador único.
   *Cuidado de bundling se algum dia for para o browser:* importar do barril `skill-flow-engine/src/index.ts`
   arrasta o `SkillFlowEngine` inteiro (ioredis).
4. **`SkillSchema` é `ZodEffects` com refinements** que JSON Schema não representa (Correção 2) — é o achado
   que decide o ADR inteiro.
5. **Monaco vem de CDN, sem config no Vite; `onMount` descarta o `monaco`** (Correção 1, D7).
6. **`zod-to-json-schema@3.25.2` já está em quatro lockfiles** como transitivo do MCP SDK — quando a F4
   chegar, é versão conhecida e compatível com o Zod 3.25.76 do `schemas`. Zod 3 não tem `z.toJSONSchema()`
   nativo (isso é Zod 4).
7. **`@plughub/schemas` emite só CommonJS** (`build: "tsc"`, `module: "Node16"`, sem `module`/`exports`/`files`
   no package.json) — irrelevante para este ADR, relevante para quem um dia tentar consumi-lo no browser.

---

## Decisões em aberto

1. **Posição de linha para os markers (F3).** Mapear `steps[3].on_success` → linha exige posições do YAML, e
   **`js-yaml` não as expõe**. Duas saídas: trocar por `yaml` (eemeli, tem CST com offsets) — mudança de
   dependência no caminho quente do editor — ou derivar a posição por busca textual do path, que é aproximado.
   Decidir com o dado, na F3; a F2 entrega painel sem squiggle e já vale.
2. **Debounce e custo.** O dry-run é uma chamada por pausa de digitação sobre um YAML de até 520 linhas
   (`agente_portabilidade_intake_v1.yaml`). Medir latência antes de escolher o intervalo.
3. **O dry-run exige credencial?** O `PUT` exige Bearer + `x-tenant-id` (`SkillFlowsPage.tsx:123-131`). O
   dry-run não escreve, mas revela a forma do schema. Herdar o mesmo portão é o default seguro.

---

## Consequências

- O autor passa a saber **antes de salvar** e, na F3, **em qual linha**.
- A checagem de ciclo migra da execução para o save (D3) — ganho real, com o pré-requisito de contar quem
  quebra.
- Uma única resposta para *"este skill é válido?"*, por construção e não por disciplina.
- **Custo:** endpoint novo + extração do verificador + painel no editor. Nenhuma migração de bundling na v1.
- **O que NÃO entrega:** autocomplete e hover — que era metade da motivação original. Fica na F4, honestamente
  precificado como arco de bundling, em vez de escondido dentro de "uma dependência e um passo de build".

---

## Fases

| # | Fase | Entrega | Depende de |
|---|---|---|---|
| **F0** | **Handle + erro visível** | `onMount={(editor, monaco) => …}` (D7); o front lê `detail` **e** `details` — o erro de bloco masked deixa de ser mudo (D4, achado 1). | — |
| **F1** | **Verificador único** | Extrair `validateSkillPayload()` (Zod + masked block + `validateFlow` estruturado do `sdk/certify`); `PUT` e dry-run passam a chamá-lo (D3). **Antes de ligar:** rodar sobre os 42 YAMLs e contar reprovações. | — |
| **F2** | **`POST /v1/skills/validate` + painel** | Dry-run sem persistir; editor chama com debounce; painel de erros com `path`; degradação alta (D5). | F0, F1 |
| **F3** | **Markers de linha** | `setModelMarkers` a partir do `path`; resolve a decisão em aberto #1 (`js-yaml` × `yaml`). | F2 |
| **F4** | **Afordância** *(arco próprio)* | Monaco bundlado + workers + `monaco-yaml` + JSON Schema gerado do `SkillSchema` ⇒ autocomplete e hover. Reavaliar prioridade **depois** da F2. | F3 |

**Ordem inegociável:** F1 antes de F2 — um dry-run que não seja o mesmo verificador do `PUT` é precisamente a
segunda resposta que este ADR existe para impedir.

**Gate:** `infra/test/probe_skill_editor_validation.sh`. Testemunhas negativas, nomeadas: um flow **sem
`complete`** tem de reprovar no dry-run (é refinement — o caso que provaria JSON Schema insuficiente); um flow
com **ciclo não-guardado** tem de reprovar no `PUT` **e** no dry-run com a mesma mensagem; um skill **válido**
tem de passar nos dois (controle positivo — sem ele o probe passa pelo motivo errado); e o dry-run **fora do ar**
tem de deixar o editor em "não validado", nunca em verde.
