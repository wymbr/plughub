# ADR: Composição de nota em surveys — dimension + perguntas ponderadas, primitivo de pontuação compartilhado

**Status:** Aceito + implementado (2026-07-07). Decisões #1/#2 resolvidas; **schema** (`scoring.ts` +
`DialogForm.dimensions`), **runtime** (`survey_record` compõe via `composeScore`) e **wiring E2E**
(`skill_survey_multi_v1` loop → compose; form CSAT composto) prontos e **validados ao vivo** no webchat
(atendimento=5, resolução=3 → `csat`≈4.33 + `nps`). Pendente: **editor** de dialog-forms com UI de dimension.
**Data:** 2026-07-07 · **Atualizado:** 2026-07-07 (schema + runtime + wiring E2E)
**Componentes:** `packages/schemas` (novo `scoring.ts` compartilhado + extensão de `dialog.ts`), `packages/dialog-api`
(store canônico do `survey_definition` = `DialogForm`+dimensions — D8), `mcp-server-plughub` (`survey_record` compõe —
D9), `packages/analytics-api` (roll-up populacional — inalterado), `packages/platform-ui` (editor de forms com dimension).
**Relacionado:** `docs/adr/adr-otp-workflow-and-dialog-primitive.md` (primitivo de diálogo, D2/D3),
`docs/arcos/customer-surveys.md` (§16/§17 interpretador+editor; `survey_definition` composto de `survey_question`),
`packages/schemas/src/dialog.ts` (`DialogForm`/`DialogCapture`), `packages/schemas/src/evaluation.ts`
(`EvaluationForm`/`EvaluationDimensionDef`/`EvaluationCriterion` — modelo de referência),
`packages/schemas/src/survey.ts` (`survey_record`/`session_signal`).

---

## Contexto

O `DialogForm` as-built (dialog-api) liga cada `question` a **uma** métrica via `capture.metric` (1 pergunta =
1 métrica). Isso não cobre instrumentos **compostos por várias perguntas** — CSAT transacional com sub-itens de
satisfação, ou uma dimension que agrega mais de um driver. Hoje isso vira N sinais soltos, sem noção de "estas
perguntas compõem o CSAT".

O módulo de Quality (`EvaluationForm`) já tem um modelo de **3 níveis** provado: `dimensions[]`
(peso, agregação) → `criteria[]` (peso, `max_score`, mapa valor→score) → `scoring_method` no form. A proposta é
trazer essa estrutura de **composição** para o survey, sem fundir os dois tipos de form (renderização a canal vs.
rubrica de avaliador são costuras diferentes). A discussão convergiu no modelo abaixo.

## Decisões

### D1 — Camada `dimension` (instrumento) entre form e pergunta

Promover o `metric` de hoje a **`dimension`**, que representa um **instrumento** (csat, nps, ces, …), agrupa
perguntas e carrega a política de pontuação. Cada `question` passa a declarar a qual dimension pertence
(`dimension_id` no `capture`) + o mapa de valor por opção/campo + um `weight`. A dimension agrega os itens num
**valor per-respondente**, que vira o sinal.

### D2 — Escala e método de agregação vivem na dimension; perguntas herdam

A **escala** (ex.: CSAT 1–5) e o **método de agregação** são propriedades da **dimension**; as perguntas
**herdam** a escala. Por pergunta fica apenas o **mapa de valor** (opção "😀" → 5), validado contra o range
herdado. Instrumentos de survey são homogêneos (todas as sub-perguntas de um CSAT usam a mesma escala), então
subir a escala para a dimension elimina o caso de escalas mistas e simplifica o editor.

Divergência consciente vs. Quality: lá `max_score` é **por-critério** (uma dimension mistura boolean + 0–10
legitimamente). No survey a escala é **por-dimension**. → o primitivo compartilhado (D6) deve permitir a escala
em **qualquer um dos níveis**. Escape hatch YAGNI: se surgir dimension survey heterogênea, modelar como duas
dimensions (preferido) ou um override de escala por-pergunta (adiado até haver necessidade real).

### D3 — Média ponderada com peso default 1 (agregação única `weighted_mean`)

Um único método `weighted_mean` cobre média aritmética (todos os pesos iguais — default `weight: 1`) e
ponderada (pesos ajustados). Pesos são **relativos**, normalizados pela soma no cômputo (o autor não precisa
somar 1). **Re-normalização em NA/pulada:** pergunta não respondida (skip, canal não suporta, timeout) sai do
denominador e os pesos das demais re-normalizam — igual ao tratamento de `na` do Quality e à regra "ausente/na
re-normaliza peso" da metodologia de métricas. Sem isso uma pergunta pulada derruba a nota artificialmente.

### D4 — Dimensions **paralelas**, não um composite único

Num survey as dimensions são **paralelas**: um form pode carregar um bloco CSAT + uma pergunta NPS + um
verbatim, e **cada dimension emite seu próprio sinal** — não uma nota só. Isto é o oposto do Quality, onde as
dimensions **sobem para um `composite_score` único**. Um composite de form (ex.: "health score") é opcional e
fica como roll-up por cima, **adiado** — o default é paralelo.

### D5 — Per-respondente no form; populacional no analytics (inalterado)

A dimension produz só o **valor per-respondente** (ex.: CSAT = `weighted_mean` das sub-perguntas = 4.3 → sinal
`metric=csat, value=4.3`). Tudo **populacional** — %NPS (promotores−detratores), CSAT top-2-box, média entre
respondentes — continua sendo **agregação read-time no analytics**, como já é hoje (`session_signal`). O form
**não** embute as fórmulas canônicas de NPS/CSAT; compõe o valor per-respondente e o analytics rola cada métrica
pelo nome. Consequência: o método de agregação da dimension é pequeno e per-respondente (`weighted_mean`); NPS =
dimension de 1 pergunta 0–10 (peso irrelevante) + n perguntas de verbatim sem métrica.

### D6 — Primitivo de pontuação compartilhado (`scoring.ts`), envelopes separados

Extrair a subestrutura **comum** de pontuação — "grupo pontuado" (dimension: peso, escala opcional, método de
agregação) → item ponderado (peso, escala opcional, mapa valor→score) — para um módulo compartilhado em
`@plughub/schemas` (`scoring.ts`), importado **tanto** pelo survey/`DialogForm` **quanto** pelo `EvaluationForm`.
Parametrizável: escala no nível da dimension (survey) ou do item (Quality); roll-up paralelo (survey) ou composite
(Quality) fica no **envelope**, não no primitivo.

**Não fundir os envelopes de form.** `DialogForm` renderiza a canal ao vivo (`interaction`, `LocalizedText`
embutido, `masked`, `visibility`, `retry`, `timeout_s`) e é conteúdo para o **cliente**; `EvaluationForm` é
rubrica de **avaliador** (`type` score/boolean/choice/text/auto_computed, `scoring_guidance`, exemplos de
calibração, `evidence_required`, `contestable`, `computation_source`). Respondente, rendering e ciclo de vida são
diferentes; casá-los num só schema seria casamento forçado. Compartilha-se **a composição**, não o envelope.

### D7 — Compatibilidade retroativa

O `capture.metric` de hoje = o caso degenerado "dimension com 1 item, `weighted_mean` peso 1". Forms atuais
mapeiam sem migração destrutiva; a derivação implícita (uma dimension por métrica distinta, item único) cobre o
legado.

## Invariantes preservadas

- **4 costuras do primitivo de diálogo** intactas: dimension/escala/peso/método são **conteúdo declarativo**
  (dado no form), sem branching e sem control. O **runner continua burro** (renderiza linear, devolve respostas
  cruas); quem **aplica** a receita de agregação é o **domínio survey** (skill ou `survey_record`), exatamente
  como o `capture` já é declarado no form e aplicado pelo domínio.
- **Single-source**: o roll-up populacional permanece só no analytics; o valor per-respondente é derivado uma vez
  no domínio survey. Sem duplicação de fórmula.

## Decisões resolvidas (2026-07-07)

### D8 — Store canônico = estender o `DialogForm` na dialog-api

Resolvida a favor da **Opção A**. O `survey_definition` **é** um `DialogForm` com a camada de composição
(dimensions) acoplada — conteúdo voltado ao cliente, renderizado pelo runner via `form_get`. Reusa runner,
`form_get` e o editor; evita segundo store + passo de compile. Não viola o single-source "forms→evaluation-api":
esse invariante rege as **forms de qualidade** (rubrica de avaliador); conteúdo de diálogo é outro domínio, já
da dialog-api. A unificação com o Quality é o primitivo `scoring.ts` (D6), não o store. Custo: diverge da letra
da spec de surveys (§16/§17 dizia evaluation-api, anterior ao dialog primitive as-built) → reconciliar a
`customer-surveys.md`.

### D9 — Agregação per-respondente roda no `survey_record` (server-side)

O `survey_record` passa a aceitar `form_id` + respostas cruas e **compõe** os sinais per-respondente server-side
(no mcp-server), aplicando o `composeScore` determinístico de `scoring.ts`. Mantém o runner burro, o YAML
declarativo (nada de matemática em step de flow — respeita "sem eval em step") e o analytics inalterado (roll-up
populacional read-time). O contrato atual (`signals[]` explícito) permanece válido para o caminho legado.

## Decisões em aberto

1. **Composite de form opcional** (health score) — roll-up por cima das dimensions paralelas; adiado até haver
   requisito. (`DialogDimension.weight` já reservado para isso.)
2. **`survey_question` reutilizável** (biblioteca compartilhada entre definitions, prevista na spec de surveys) —
   ortogonal ao modelo de composição; fora do 1º corte.
3. **Adoção do `scoring.ts` pelo `EvaluationForm`** — o primitivo é compartilhável (D6), mas retrofit do Quality
   é mudança separada e mais arriscada (evaluation-api em produção); feito depois, mapeando `weighted_average`→
   `weighted_mean` e `min_score`→`min`.

## Consequências

- Surveys ganham instrumentos compostos (CSAT/NPS multi-pergunta) com média ponderada e re-normalização de NA,
  sem embutir fórmulas populacionais no form.
- `EvaluationForm` e `DialogForm` passam a compartilhar o submodelo de pontuação sem fusão de envelope — reuso com
  baixo acoplamento.
- Nada quebra no legado (`capture.metric` vira o caso 1-item).
- Pendências físicas (store, local da agregação) ficam explícitas antes do schema.
