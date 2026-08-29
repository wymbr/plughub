# ADR: Conteúdo referenciado resolvido no DEPLOY — snapshot, não runtime

**Status:** Proposto — **S1 BLOQUEADA em 2026-08-29** (duas premissas refutadas ao implementar; ver §D6).
**Data:** 2026-08-29
**Componentes:** `packages/agent-registry` (`PoolSkillSlot`, `set-next`/`promote`),
`packages/mcp-server-plughub` (`form_get`, `segment_outcome_record`),
`packages/orchestrator-bridge` (`get_pool_current_flow`),
`packages/platform-ui` (`AgentFlowDeployPage`, editor de skill-flow),
`packages/dialog-api` (leitura por `?version=N`).
**Relacionado:** [`adr-dialog-tree-options.md`](adr-dialog-tree-options.md) (**supersessão da F0b SUSPENSA em 2026-08-29** — ver refutação na D6),
[`adr-dialog-form-deletion.md`](adr-dialog-form-deletion.md) (D2 — a purga restrita ao nunca-publicado),
[`adr-skill-flow-editor-validation.md`](adr-skill-flow-editor-validation.md),
[`adr-otp-workflow-and-dialog-primitive.md`](adr-otp-workflow-and-dialog-primitive.md) (as 4 costuras),
`docs/product/skill-versioning-deploy-spec.md`.

---

## Contexto

Um skill que usa DialogForm carrega uma **referência** (`invoke form_get, form_id: X`), e o conteúdo é
resolvido **em runtime**, no momento da execução, pela versão publicada corrente. Isso produz três problemas
distintos que vinham sendo tratados como se fossem um só:

1. **A corrida das duas leituras.** O renderizador busca o form quando o agente reivindica o item;
   `segment_outcome_record` busca **de novo** (`segment.ts:76-95`, chamado em `:325`) para compor. Entre as duas
   cabe um `publish`, e a janela vai até o submit — `dialog_wrapup_v1` tem `timeout_s: -1` em todo nó.
2. **"Vi X, subiu Y."** O autor do skill vê um conteúdo ao editar; o `promote` acontece depois, possivelmente
   por outra pessoa, e injeta o que estiver publicado *naquele* momento. O form é editado noutra tela
   (`/config/dialog-forms`), por outra pessoa, em outra cadência — e **não existe indicador de defasagem**
   para ele (existe para o flow: `AgentFlowDeployPage._isStale` compara `skill.updated_at` com o snapshot).
3. **Dispersão de autoria.** Quem lê o YAML não vê o texto que o step vai apresentar.

---

## A decisão em uma frase

> **O conteúdo referenciado é resolvido no `promote` e congelado no snapshot do slot — o mesmo momento e o
> mesmo artefato em que o flow já é congelado.**

Isto não é padrão novo. A base já decidiu assim **duas vezes**:

- **O slot.** O bridge não executa `skill.flow`; executa o `yaml_snapshot` do slot `current` do pool
  (`get_pool_current_flow`, cache por pool, invalidado no `registry.changed(pool)`). Editar e republicar não
  muda produção — só `set-next` → `promote` muda.
- **O link de survey.** `survey_link_create` **já guarda um snapshot do form** no token (Redis). O veículo web
  já resolve no vínculo, não na execução.

---

## Decisões

### D1 — Resolução no `promote`, congelada no snapshot

Ao promover, o agent-registry resolve toda referência de conteúdo do flow e grava o resultado **dentro do
snapshot**. Em execução, `form_get` lê do snapshot; não há chamada à dialog-api no caminho quente.

### D2 — A referência aceita `version` opcional: pin × float

```yaml
- id: carregar_form
  type: invoke
  tool: form_get
  args:
    form_id: dialog_wrapup_v1
    version: 7        # ausente = flutua (resolve a publicada no promote); presente = pin
```

As duas cadências são legítimas e a escolha fica **explícita por referência**:

| | quando |
|---|---|
| **pin** | mudar o vocabulário é evento deliberado (taxonomia de wrap-up: uma folha nova muda a série do Arc 12) |
| **float** | correção deve propagar no próximo deploy (typo num prompt de NPS) |

A dialog-api já serve `?version=N` (`router.py:141` → `db.py:174-178`) e preserva toda versão publicada (PK
`(tenant_id, form_id, version)`, `db.py:43-57`; o publish só promove, `db.py:309-341`). *A verificar: se o tool
`form_get` repassa `version` — hoje o `segment_outcome_record` resolve por `?status=published`
(`segment.ts:81`).*

### D3 — O `promote` é otimista: declara contra o que exibiu

A tela de deploy envia as versões que **mostrou**; se a publicada mudou nesse intervalo, o promote **recusa com
409** e apresenta o diff. Assim "vi X e subiu Y" deixa de ser *visível* e passa a ser **impossível** — mesma
forma de concorrência otimista que a base usa em outros pontos: quem escreve declara contra o que estava lendo.

Aviso não basta: um banner que ninguém lê é a mesma família do *"using default values"* que sobreviveu meses
no bridge.

### D4 — A injeção NÃO acontece na edição

Alternativa considerada e **recusada**: inlinar o conteúdo no YAML no momento em que o autor adiciona a
referência. Troca um risco **visível e limitado** por um **silencioso e ilimitado**:

- A cópia congela na edição e **envelhece sem sinal**. Alguém corrige a taxonomia e republica; todo skill que
  inlinou na edição fica com a versão errada **para sempre**, funcionando normalmente. É o mesmo modo de falha
  do `flattenBlocks` (uma cópia que ninguém sabe que é cópia). **Velho é pior que diferente**: no promote, ao
  menos, injeta-se o que está publicado.
- Bloat: uma taxonomia de wrap-up de 5 níveis são centenas de linhas dentro do YAML do skill — **menos**
  legível, não mais, contra a própria meta que motivou a ideia.

### D5 — O YAML autorado mantém a referência; o EDITOR resolve para exibir

Há duas metas em jogo, e elas parecem a mesma: *"produção roda uma cópia congelada"* (build) e *"o autor vê
tudo num lugar"* (autoria). Só convivem se forem mecanismos distintos:

- **snapshot** carrega o conteúdo (D1);
- **o editor resolve o `form_id` e mostra prompt/opções in loco**, read-only, com link para o editor de forms.

Nenhuma das duas sozinha entrega as duas metas: inlinar no YAML entrega autoria e destrói legibilidade;
snapshot puro entrega build e deixa o autor às cegas.

> **Consequência que justifica a D3:** a afordância da D5 **cria** a expectativa de que o que se vê é o que
> vai subir. Sem o conflito no promote, esta decisão pioraria o problema 2 em vez de resolvê-lo.

> ## ⚠️ D6 e S1 REFUTADAS POR MEDIÇÃO — 2026-08-29
>
> Ao começar a implementar a S1, **duas premissas caíram**. Nenhuma das duas é detalhe: juntas, elas
> tornam a S1 **não implementável como escrita**. O texto original fica abaixo, intacto, porque o
> raciocínio dele continua válido *se* as premissas fossem verdadeiras — e porque apagar a versão
> refutada esconderia por que a alternativa foi descartada.
>
> **(1) `render.captures` NÃO é suficiente — perde o mapa opção→nota.** O achado nº 5 (*"`form_get`
> já produz `captures` — a D6 não precisa de campo novo"*) é falso. `form_get` monta
> `captures[output_key] = node.capture` (`dialog.ts:151`), um `Record` que carrega `{kind, metric}` e
> **não** carrega `options`; e `render.questions[].options` carrega `{id, label}`, **descartando
> `option.capture.value`** (`dialog.ts:137-140`). Mas `deriveAgentEvents` precisa exatamente desse
> mapa para `kind: "scored"` (`segment.ts:119-121`), porque o número mora na OPÇÃO.
> **Dano vivo, não latente:** `dialog_wrapup_arc12_v1` — o form que existe para exercitar a captura
> Arc 12 — tem `fcr` com `kind: scored` e respostas `"sim"`/`"nao"`, cujos valores (1 e 0) estão em
> `option.capture.value`. Sob a S1 como escrita, `Number("sim")` → `NaN` → `continue` com `warn`
> (`segment.ts:122-125`): a métrica **para de ser emitida**, com aviso num log que ninguém lê.
> Degradação honesta, resultado silencioso. ⇒ **A D6 PRECISA de campo novo** (`option_values` dentro
> de `captures`, para o consumidor não ter de fazer o join entre `captures` e `questions` — join que
> criaria uma segunda casa que conhece a forma).
>
> **(2) O único chamador não tem `render` no `pipeline_state`.** `segment_outcome_record` tem **um**
> chamador — `skill_wrapup_detached_v1`, confirmado no `flow` do agent-registry, não só no YAML — e
> ele tem **zero** `form_get`: os steps são `coletar` (delegate) → `gravar` (invoke) → `complete`. O
> `dialog_form_id` vem do **contexto do hook** (`@ctx.hook.dialog_form_id`), e quem busca o form para
> renderizar é o **`DialogFormRenderer` do Console**, no cliente. Logo
> `$.pipeline_state.dialog.render.captures` **não existe** ali, e a prescrição da S1 (*"o skill passa
> `$.pipeline_state.dialog.render.captures`"*) não tem de onde passar.
>
> **Consequência sobre a supersessão da F0b.** A D6 afirma resolver a corrida *"por remoção, com menos
> mecanismo"* — e é essa a base para superseder a fase F0b do ADR da árvore (pin de versão). Medido, as
> duas leituras que correm são **o renderizador do Console** e **o composer**, e nenhuma delas está no
> skill. Acrescentar um `form_get` ao skill não remove a corrida: **move** a segunda leitura para
> dentro do workflow e deixa o renderizador buscando por conta própria. Só há remoção de verdade se o
> renderizador **parar de buscar** e passar a consumir o bloco que o workflow já produziu — o que é
> mudança maior do que a S1 declara. **Portanto a supersessão da F0b está SUSPENSA**, não confirmada:
> volta a ser escolha entre pinar a versão (F0b) e fazer o renderizador consumir o render do workflow.
>
> ⚠️ **Caminho recusado no mesmo passo:** fazer o renderizador **ecoar** os `captures` no payload do
> `workflow_resume`. É o mais curto e está errado por segurança — os `captures` passariam a vir do
> CLIENTE, e um browser poderia forjar valor de métrica de negócio do Arc 12. Origem do dado tem de
> ser servidor.
>
> **A S1 fica BLOQUEADA aguardando decisão** entre os dois caminhos acima. As demais fases (S2–S6) não
> dependem desta medição.

### D6 — A segunda leitura morre; `captures` viajam no `render` *(texto original — ver refutação acima)*

`segment_outcome_record` busca o form apenas para ler as declarações de `capture`. Mas `form_get` **já
normaliza `captures` dentro do `render`** (`mcp-server-plughub/src/tools/dialog.ts:90-181`). O skill passa
`$.pipeline_state.dialog.render.captures` no lugar de `dialog_form_id`, e **a segunda leitura deixa de
existir**.

Sem segunda leitura não há corrida — o problema 1 morre por **remoção**, não por sincronização. É por isso que
esta decisão **supersede a fase F0b** de [`adr-dialog-tree-options.md`](adr-dialog-tree-options.md) (pin de
versão carimbado do render ao submit): resolve o mesmo problema com menos mecanismo.

### D7 — O que isto NÃO resolve

O snapshot congela o que **executa**; não congela como o **histórico se lê**. Uma resposta de wrap-up gravou
`financeiro.cobranca.indevida` no Arc 12 seis meses atrás; se aquele `id` foi renomeado, a série parte, e
nenhum snapshot alcança o ClickHouse. A **imutabilidade do `id`** (D6 do ADR da árvore) permanece invariante
independente. São dois problemas de aparência idêntica e só um morre aqui.

---

## Achados de medição

1. **O precedente do slot é literal** — produção já roda snapshot, não a edição. `docs/product/skill-versioning-deploy-spec.md`
   e o `get_pool_current_flow` do bridge.
2. **O precedente do survey é literal** — `survey_link_create` já snapshota o form no token.
3. **A dialog-api preserva versões e serve `?version=N`** — logo a D2 é plumbing, não construção de store.
4. **Não existe indicador de defasagem para o form** — `_isStale` (`AgentFlowDeployPage.tsx:47-50`) cobre o
   `skill.updated_at` contra o snapshot; nada equivalente para o conteúdo referenciado. É o que torna o
   problema 2 indetectável hoje.
5. ~~**`form_get` já produz `captures`** no bloco `render` — a D6 não precisa de campo novo.~~ **REFUTADO em 2026-08-29:** produz, mas `captures` é `Record<output_key, capture>` e **descarta `options`**; `render.questions[].options` descarta `capture.value`. `deriveAgentEvents` precisa desse mapa para `kind: "scored"`, e há dano VIVO (`dialog_wrapup_arc12_v1.fcr`). A D6 **precisa** de campo novo. Ver bloco de refutação na §D6.
6. **Não existe critério escrito** para escolher entre menu inline e `form_get`. As duas formas coexistem
   (`agente_triagem_v2.yaml:56-63` × `agente_nps_v1.yaml:62-64`) por acréscimo histórico, não por decisão.
   Este ADR não fecha esse critério, mas remove o argumento que eu vinha usando para justificá-lo: **reuso
   entre canais é servido pela normalização `render`, e funcionaria igualmente inline** — o que a referência
   compra é (a) superfícies sem skill rodando (`/survey/{token}`, `DialogFormRenderer`), (b) editar texto sem
   re-deploy, (c) o mesmo form em N pools. Só a (c) obriga referência.

---

## Decisões em aberto

1. **Tamanho do snapshot.** Uma taxonomia de 5 níveis embutida em cada snapshot de slot multiplica por pool.
   Medir antes de decidir se o snapshot guarda o conteúdo ou um hash + cópia em store próprio.
2. **Float e o que "publicada no promote" significa em rollback.** `publish?version=1` hoje **não** torna a v1
   corrente (`?status=published` resolve por `ORDER BY version DESC`) — bug latente já registrado no ADR da
   árvore. Float sobre esse comportamento resolveria a versão errada; pin não sofre. Fechar antes da F2.
3. **Referências que não são `form_get`.** Se o `$.config.*` do slot ou outras indireções entram no mesmo
   regime, decidir junto ou declarar fora.

---

## Consequências

- Produção roda o que foi homologado, também no **conteúdo** — não só no flow.
- O problema 1 morre por remoção (D6); o 2, por conflito no promote (D3); o 3, por afordância (D5).
- **Afrouxa o ADR de deleção:** a D2 de lá restringe a purga ao nunca-publicado porque *"o `form_id` literal
  mora dentro do flow do snapshot do slot"*, tornando a checagem cross-service incompleta por construção. Com
  o conteúdo resolvido no snapshot, arquivar um form deixa de poder quebrar deploy em execução.
- **Custo:** resolução no promote (agent-registry), `version` na referência, conflito otimista na tela de
  deploy, e a mudança de contrato do `segment_outcome_record` (recebe `captures`, não `dialog_form_id`).
- **Mudança de comportamento:** corrigir um typo num form deixa de propagar sozinho — passa a exigir
  re-promote. É o preço de rodar o que foi homologado, e é o mesmo preço que o flow já paga.

---

## Fases

| # | Fase | Entrega | Depende de |
|---|---|---|---|
| **S1** | ⚠️ **BLOQUEADA — 2026-08-29** | Duas premissas refutadas por medição (ver bloco no topo da D6): `render.captures` perde o mapa opção→nota (dano VIVO em `dialog_wrapup_arc12_v1`), e o único chamador não tem `form_get`, logo não há `render` no `pipeline_state` dele. Reabre como escolha entre pin de versão (F0b) e renderizador consumindo o render do workflow. | — |
| **S2** | **`version` na referência** | `form_get` repassa `version`; pin × float documentado. Fecha antes a decisão em aberto #2 (rollback). | — |
| **S3** | **Resolução no promote** | O `promote` resolve as referências e grava no snapshot; `form_get` lê do snapshot em execução (D1). | S2 |
| **S4** | **Conflito otimista** | A tela de deploy declara as versões exibidas; `409` + diff quando divergirem (D3). | S3 |
| **S5** | **Afordância no editor** | O editor resolve o `form_id` e mostra prompt/opções in loco (D5). | S2 |
| **S6** | **Validação** | Contato real com wrap-up: editar o form entre claim e submit não muda a composição; promover com form alterado dá 409. | todas |

**Ordem inegociável:** S1 antes de S3 — enquanto houver duas leituras, congelar uma delas só desloca a
divergência em vez de removê-la. E **S4 junto ou depois de S5, nunca antes**: a afordância sem o conflito
promete ao autor uma garantia que o sistema ainda não dá.

**Gate:** `infra/test/probe_deploy_content_snapshot.sh`. Testemunhas negativas: publicar uma versão nova do
form **entre** o claim e o submit não pode mudar o que `segment_outcome_record` compõe; um `promote` com o form
alterado desde a exibição tem de dar **409**, não 200; e um skill com `version` pinada tem de continuar rodando
a versão pinada depois de o form ser republicado (controle positivo — sem ele o probe passa por não exercer
o pin).
