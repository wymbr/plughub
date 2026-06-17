# Evaluation — Spec Reconciliadora (Arc 6 + Arc 13)

> **Status:** proposta de arquitetura-alvo (decide o alvo, não só descreve).
> **Baseline congelado:** 2026-06-17, commit `eval-baseline`.
> **Escopo:** módulo de Evaluation — finalização, contestação, contrato de saída do
> avaliador, UI, fluxos humano × IA, degradação thin-session, dispatcher por calendário.
> **Método:** confirmado contra o código (leitura dirigida); este doc registra
> **estado-atual × estado-alvo** e re-deriva os gaps + plano de tarefas.
> **Regra de linguagem:** identificadores em inglês; português só em strings de UI e
> dados de tenant (CLAUDE.md § Language Rule).

---

## 0. Contexto e descoberta-chave

O avaliador **real campaign-driven** foi validado verde ponta-a-ponta (sessão real
`e8f75639` → instance → campanha → form → `overall_score` 7.8 persistido). Porém o
módulo só havia sido exercitado **pelo ramo `ai_agent` / `auto_finalized`** (seeder
sintético via `/ingest` direto), o que mascarou **todo o fluxo humano**: avaliação de
agente humano **nunca finaliza**, logo nunca entra nos relatórios (que filtram
`type = 'evaluation_finalized'`).

**Contradição "docs dizem completo × código":** `docs/arcos/arc6-evaluation.md` e
`docs/arcos/arc13-review-contestation.md` marcam finalização e contestação como ✅
concluídas — `arc13` chega a declarar o invariante "uma avaliação não finalizada não
existe para fins analíticos". Apenas o `TODO.md` (§ Shakedown pós-submit Arc 13)
descreve a realidade. **Esta spec corrige esses dois docs, não apenas acrescenta.**

---

## 1. Princípios reconciliadores (invariantes do alvo)

1. **Terminal único.** Todo caminho termina em **`finalized`** (avaliação válida) ou em
   **`error_rejected`** (não virou avaliação). `evaluation_finalized` é emitido em
   **todos** os caminhos de `finalized` — é a única fonte de verdade dos relatórios.
2. **A chave canônica da avaliação é o `segment_id`** (o trecho do contato atendido por
   **um** agente — ContactSegment, Arc 5), nunca o `session_id`.
3. **Um único contrato de contestação:** threads **por critério** (evidência append-only)
   sob um **envelope de round/estado no nível do resultado**, com ABAC.
4. **O formulário é fonte única** de: prompt do LLM, schema de saída, validação do submit
   e **agregação da nota**. O LLM nunca devolve nota de dimensão ou geral.
5. **AI Gateway é stateless** (CLAUDE.md): recebe o prompt já composto; não guarda
   template. Prompt/rubrica são config **editável na UI** do módulo Quality, versionados.
6. **Inação respeita prazo em horário comercial** (calendar-api): prazo de revisão avança
   o estado (default `mantida`); prazo de contestação leva a `finalized`.

---

## 2. Modelo de estados — a máquina única

Duas **camadas** que não se misturam, costuradas pelo `segment_id`/`session_id`:

### 2.1 Camada da Instance (item de trabalho, por segmento)

`status`: `scheduled` (agendado) → `dispatched` → `in_progress` → `completed`
(avaliador submeteu) | `error` | `skipped`.

- `scheduled` — criada no fechamento da sessão (sampling), **fixa `form_version`**.
- dispatch ocorre na **janela do calendário** da campanha (§9).
- `skipped`/`error` — thin-session e falha de avaliação (§8).

> **Mudança de enum:** a `evaluation.instances.status` ganha `skipped`. (Hoje:
> `scheduled, assigned, in_progress, completed, under_review, reviewed, contested,
> locked, expired, error` — sem `skipped`.)

### 2.2 Camada do Result (desfecho da avaliação)

Campo canônico **`result_state`** + atributos `round` (1..max) e `finalize_reason`:

| `result_state` | Rótulo UI (pt) | Significado |
|---|---|---|
| `ai_review` | Revisão IA | gate condicional dos sinalizados (fora de faixa ou erro) |
| `open` | Disponível para contestação (round N) | janela de contestação correndo — só avaliado humano |
| `under_review` | Contestado / Revisado (round N) | contestação aberta aguardando revisor humano |
| `finalized` | Finalizado | terminal — emite `evaluation_finalized` |
| `error_rejected` | Rejeitado por erro | terminal-irmão — não é avaliação válida |

`finalize_reason` ∈ `auto_ai | uncontested | upheld | revised | max_rounds |
contest_timeout | review_timeout`.

> **Colapso do enum atual.** Hoje `evaluation.results.contestation_state` tem CHECK com
> `pre_review_pending, contestation_open, under_review, timeout_contestation,
> timeout_review, closed_upheld, closed_revised` — **mas o código grava
> `auto_finalized` (ingest ai_agent) e `closed_max_rounds` (submit_review), nenhum no
> CHECK** (inconsistência latente). O alvo substitui esses 9 valores por
> `{ai_review, open, under_review, finalized, error_rejected}` + `round` +
> `finalize_reason`, fazendo schema e código concordarem por construção.

### 2.3 Transições

- **avaliação IA (instance `completed`)** →
  - sinalizado (`score > config_max` ∨ `score < config_min` ∨ erro) → `ai_review`;
  - avaliado IA, não sinalizado → `finalized(auto_ai)`;
  - avaliado humano, não sinalizado → `open(round 1)`.
- **`ai_review`** →
  - ajuste de nota → `open` (humano) | `finalized(auto_ai)` (IA);
  - erro recuperável → re-tentativa (instance volta a `dispatched`/delegate);
  - erro irrecuperável → `error_rejected`.
- **`open(round N)`** →
  - humano contesta (ativo na UI) → `under_review(round N)`;
  - prazo expira sem contestar → `finalized(uncontested | max_rounds)`.
- **`under_review(round N)`** →
  - revisor humano decide **mantida** → `finalized(upheld)`;
  - **revisada(+override)** e há round restante → `open(round N+1)`;
  - **revisada** e `N == max_rounds` → `finalized(revised | max_rounds)`;
  - prazo de revisão expira → default **mantida** → avança (não finaliza
    diretamente; cai na regra acima).

**Quem finaliza / motor de review (S2.4 — DECIDIDO 2026-06-17, opção A):** a
**evaluation-api é dona da máquina** e emite `evaluation_finalized` diretamente nas
transições terminais **e** via um **scanner de deadline** (§ G-TIMEOUT). A revisão humana
é **ação direta com ABAC** (POST), não mediada por workflow. O workflow
`skill_revisao_treplica_v1` / `review_workflow_skill_id` (Arc 6 v2) e o acoplamento via
`action_required`/`resume_token`/consumer de `workflow.events` são **removidos** do ciclo
de contestação (eliminam o split-brain api↔workflow que produziu o G-FIN/G-S2.4).

**Design do scanner:** o `deadline_at` é computado **uma vez na entrada do estado** via
calendar-api (horário comercial da campanha); o scanner periódico só compara
`now ≥ deadline_at` e aplica a transição com **UPDATE atômico guardado por estado**
(idempotente, no molde do `finalize_result WHERE eval_status != 'locked'`). Sem
recomputar horário comercial no laço de varredura.

---

## 3. Chave canônica: segmento, não sessão

**Alvo:** a avaliação é por **`segment_id`** (ContactSegment do Arc 5). Um contato com
transferência/especialista tem N segmentos → até N avaliações, uma por agente.
`evaluated_agent_type`, `pool_id`, `agent_type_id` vêm do **segmento**.

**Estado atual:** `evaluation.instances` já tem a coluna `segment_id`, mas o sampling
campaign-driven (`_sample_on_close`) **não a popula** — cria a instance só com
`session_id` e deduplica por sessão (`instance_exists_for_session`). Um contato
multi-agente geraria no máximo **uma** avaliação, possivelmente do agente errado.

**Mudança:** o consumer de `conversations.session_closed` faz **fan-out sobre os
segmentos** da sessão; dedup e unicidade por `(campaign_id, segment_id)`; `session_id`
permanece como contexto.

---

## 4. Contrato único de contestação

**Decisão:** modelo **em camadas** — threads do Arc 13 (dado) sob o envelope de
round/estado (Arc 6). Deprecam-se os endpoints duplicados.

### 4.1 Formato (funcional, fechado)

- **Unidade = critério** (`criterion_id`). Sem limite de quantos critérios contestar.
- **Round no resultado** (1 = contestação, 2 = réplica, 3 = tréplica; `max_rounds` da
  campanha). O histórico de justificativas/decisões vive **por critério** (thread
  append-only); o **estado e o round vivem no resultado**.
- **Regra de UI:** "Salvar revisão" só é liberado quando **todas** as contestações do
  round têm decisão — fecha o round sem contador por item e garante que o revisor não
  deixe critério para trás.
- **Revisor = humano** com permissão ABAC `revisar` (avaliado humano). Avaliado IA →
  só revisor IA (`ai_review`, §2).
- **Decisão = `mantida` | `revisada(+override)`** (mata o vocabulário `aprovado/rejeitado`
  do Arc 6). Override é **tipado por critério** (§5.3).
- **Override vale na hora** (nota provisória); a **nota real** consolida só em
  `finalized` (§7.4).
- **Evidência ancorada no transcript** por `stream_entry_id` (§7.3).

### 4.2 Deprecações

| Depreciar (Arc 6) | Em favor de (Arc 13 + envelope) |
|---|---|
| `POST /v1/evaluation/contestations` (+ `/{id}`, `/adjudicate`) | threads por critério |
| `POST /v1/evaluation/results/{id}/review` (approved/rejected, anti-replay round) | `/instances/{id}/review` (mantida/revisada) com ABAC |
| `action_required`/`resume_token`/workflow motor | scanner + ação direta (§2.3) |
| adjudicação por `X-Admin-Token` | revisão humana por ABAC |

### 4.3 Estado atual (a reconciliar)

- **Dois contratos paralelos**, ambos chamados pela UI:
  - Arc 6 (`router.py`): `/contestations`, `/results/{id}/review` — nível-resultado,
    JWT+ABAC, motor de workflow.
  - Arc 13 (`contestation_router.py`): `/instances/{id}/contest|review|threads` —
    nível-dimensão, **auth só por header** (`X-User-ID`, `X-Author-Type`), **sem ABAC**.
- `submit_review` (Arc 13) atinge `closed_upheld`/`closed_max_rounds` mas **nunca emite
  `evaluation_finalized` nem grava `final_score`**.
- Bug latente em `resolve_curation`: referência a `row["campaign_id"]` (variável
  inexistente; o correto é `_cr_row`) → `NameError` em `recalibrated`/`bias_flagged`.

---

## 5. Contrato de saída unificado — formulário como fonte única

### 5.1 Composição do prompt

```
prompt = instruções gerais (rubrica-template)      ← humano, versionado, UI Quality
       + critérios do formulário (cada um com sua orientação)   ← form (fonte única)
       + RAG (calibration_notes por criterion + knowledge)
       + transcript do segmento avaliado
```

- A **rubrica-template** (como pontuar, citar evidência, N/A, anti-viés) é **uma default
  por tenant + override por campanha** — raramente muda, **não** é por formulário nem por
  critério.
- A **orientação específica do critério** (`scoring_guidance`: o que é 0/5/10) vive **no
  critério, no formulário**.
- O prompt composto é **derivado e previewável** na UI; ninguém o edita à mão.

### 5.2 As quatro derivações do formulário

A mesma definição de Forms gera: **(1)** o prompt, **(2)** o schema de saída tipado,
**(3)** a validação do submit, **(4)** a agregação da nota (pesos bottom-up). A nota de
dimensão/geral é **recomputada deterministicamente** dos valores por critério — o
`overall_score` do LLM é descartado.

### 5.3 Modelo-alvo do critério

| Campo | Papel |
|---|---|
| `id`, `label` | identidade / exibição |
| `type` (`score`\|`boolean`\|`choice`\|`text`\|`auto_computed`) | dirige schema, render e **tipo do override** |
| `question` | o que avaliar (o `description` atual) |
| `scoring_guidance` | ancoragem da escala (opcional; vazio = comportamento atual) |
| `scale` | score: min/max; choice: opções+mapa; boolean: true/false→nota |
| `weight` | contribuição na dimensão (agregação) |
| `allow_na` + `na_guidance` | quando N/A; o `na` é **contestável** |
| `applies_when` | aplicabilidade condicional |
| `auto_source` | `auto_computed`: qual métrica do SessionMetricsExtractor |
| `evidence_required` | exige citar evidência (default true em score/boolean) |
| `contestable` (derivado) | `auto_computed → false`; demais `true` |

- **`auto_computed`** é fato determinístico (SessionMetricsExtractor) → **não
  contestável**.
- **`text`** é qualitativo → peso 0, **fora do agregado numérico**.
- **Versionamento:** instance/result **fixam `form_version`**; contestação/revisão/
  finalização usam a versão sob a qual a avaliação nasceu (liga a *deploy epochs*,
  Arc 6 Fase 2).

### 5.4 Conveyance do schema (DECIDIDO 2026-06-17, opção 1)

Hoje o `OutputFieldSchema` do ai-gateway é **flat** (`type/enum/min/max/required`) e o
`_format_schema` é **lossy** (descarta `items/properties/nullable/description`) → o LLM
adivinha o shape e os **shims** no `evaluation_submit` normalizam (`observation→
justification`, `evidence_entries` default, `compliance_flags` objeto→string, `value` do
YAML vs `score` do Zod).

**Alvo:** **structured-output / tool-use nativo** — um tool cujo `input_schema` é o
**JSON Schema derivado do formulário**, com `tool_choice` forçado; o modelo retorna JSON
conforme por construção (tipagem por critério imposta pelo provedor). Pontos de design:

- O **JSON Schema é montado upstream a partir do form** (camada de composição na
  evaluation-api / skill do avaliador) e **passado** ao `reason`. O **ai-gateway não monta
  nada** — segue stateless e genérico; o contrato do `reason` é estendido para aceitar um
  JSON Schema (o caminho flat atual permanece como compat para outros skills).
- **Fallback (opção 2):** se o cross-provider (Anthropic tool-use × gpt-4o
  function-calling) pesar, cai-se para `OutputFieldSchema` recursivo + validação recursiva
  + retry (`ReasonRequest.attempt`). A validação+retry fica como rede mesmo no caminho 1.

**Remover todos os shims** e o `evaluation_rubric_v3` fixo.

---

## 6. Calibração — dois laços distintos

1. **Runtime (mole):** revisor/curador emite `calibration_signal` → fila de curadoria →
   `CalibrationNote` (recalibrated/bias_flagged) → publicada no `mcp-server-knowledge`
   (`evaluation:calibration:{campanha}`) → injetada via RAG nas **próximas** avaliações.
   Ajusta o avaliador, **não** a nota passada. *(Existe no código; nunca rodou com dado
   real — só pelo seeder.)* **Melhoria:** `CalibrationNote.criterion_id` para o RAG
   injetar a nota no bloco do critério certo.
2. **Estrutural (dura):** quando as notas revelam padrão, o curador/designer **edita a
   rubrica-template** (ou o `scoring_guidance` do critério) — versionado, muda o contrato
   das **futuras**. A visibilidade do prompt na UI fecha esse laço; ancorar em *deploy
   epochs* para comparar qualidade antes/depois.

---

## 7. UI

### 7.1 Lista (Evaluations) — corrigir

- **Status** = `result_state` + `round` + `finalize_reason` (hoje mostra `eval_status`
  cru "Submitted").
- **Coluna "Session" → "Agente avaliado (segmento)"**; contato como contexto.
- **Actions** vêm de `available_actions` (server-side, ABAC) — hoje vazio para todos,
  inclusive Admin (G-UI/G-PROBE).
- **Date** = `deadline`/`finalized_at`; em linhas com ação, mostrar o **prazo correndo**
  (horário comercial).
- Remover campo "Admin token (adjudication)".
- **Filtro de relatório/visão "Awaiting my action"** = fila de revisão (já existe, morto).

### 7.2 Drill-down = modo "preenchido + ações" do render do Forms

O detalhe é o **mesmo componente do Forms**, renderizado com os valores do avaliador e
afordância por critério (não é tela nova). Pipeline: Forms (template) → Campanha →
Instance (por segmento, pin form vN) → Avaliador IA preenche → Result → **Drill-down**.
**Hoje o passo Result → Drill-down não existe** (Evaluations é lista plana).

Por critério: nota + justificativa + evidência do avaliador; estado (contestado/revisada
+Δ); para o revisor, controle **Manter / Alterar(+nota tipada)** + fundamentação na
timeline do critério. Rastreador "X de Y contestações tratadas" + **Salvar bloqueado**
até tratar todas.

### 7.3 Transcript + evidência

Visão dividida (form ↔ transcript). Clicar numa evidência **rola e destaca a mensagem no
contexto** (via `stream_entry_id`). **Mascaramento por papel** (revisor vê
`original_content`; avaliado vê mascarado — CLAUDE.md § masking). **Escopo no segmento**
avaliado, com expandir para o contato.

### 7.4 Três superfícies por papel (ABAC)

- **Auto-visão do avaliado** — só as próprias avaliações (seus segmentos); contesta na
  janela; conteúdo mascarado. *(Não existe hoje.)*
- **Fila de revisão** — revisor humano vê o que aguarda decisão dele; `manter/alterar`.
- **Supervisão/Admin** — vê tudo, sem ação de parte (ou override de supervisor).

Mesma tela de detalhe; botões conforme papel — é o que destrava `available_actions`.

### 7.5 Relatórios — nota provisória vs final

Dois modos **explícitos e rotulados**, nunca blendados: **Oficial** (só `finalized` — o
invariante) e **Operacional** (inclui provisório, com aviso "em andamento").

---

## 8. Degradação thin-session / erro

Sessão sem dados / erro do avaliador **não chama `evaluation_submit`** (hoje falha duro).
A instance vai a `error`; o result entra em `ai_review` para **classificar**: recuperável
→ nova tentativa (muda status / delegate ao avaliador) vs irrecuperável →
**`error_rejected`** (terminal, auditável, fora dos relatórios de qualidade). Instance
`skipped` quando descartada sem ser falha nem avaliação.

---

## 9. Dispatcher por calendário (S2.3)

**Alvo:** tarefa agendada que despacha as instances `scheduled` de cada campanha **na
janela do calendário** da campanha (calendar-api), emitindo `evaluation.requested`.
**Estado atual:** só existe o `POST /campaigns/{id}/dispatch` manual ("Rodar agora",
admin-token); o comentário no código admite "(later) the windowed dispatcher".

---

## 10. Estado-atual × alvo (resumo)

| Eixo | Atual | Alvo |
|---|---|---|
| Finalização humana | nunca emite `evaluation_finalized` | terminal único `finalized` em todo caminho |
| Chave | `session_id` (sampling não popula `segment_id`) | `segment_id` (fan-out por segmento) |
| Estados | 9 valores; CHECK ≠ código (`auto_finalized`/`closed_max_rounds`) | `{ai_review, open, under_review, finalized, error_rejected}` + round + reason |
| Contestação | 2 contratos paralelos (Arc 6 resultado + Arc 13 dimensão) | 1 — threads por critério sob envelope de round |
| Auth ação | headers (`X-User-ID`/`X-Author-Type`), sem ABAC | JWT + ABAC em todas as ações |
| Saída do avaliador | 3 vocabulários + shims + prompt fixo | form como fonte única; schema tipado; sem shims |
| Prompt | `evaluation_rubric_v3` fixo no ai-gateway | rubrica-template na UI Quality, previewável, versionada |
| Timeout | inexistente | scanner em horário comercial |
| Thin-session | falha duro no submit | `ai_review` → `error_rejected`/retry, sem submit |
| Dispatcher | manual ("Rodar agora") | agendado por janela de calendário |
| UI | lista plana, actions "—" | drill-down (modo Forms) + 3 papéis ABAC |
| Docs | arc6/arc13 marcam ✅ | corrigir para refletir o alvo |

---

## 11. Gaps re-derivados contra o alvo

| ID | Gap | Eixo |
|---|---|---|
| **G-FIN** | fluxo humano nunca emite `evaluation_finalized` | §2 |
| **G-SEGMENT** | avaliação por sessão, não por segmento | §3 |
| **G-STATE** | enum de estado fragmentado; CHECK ≠ código | §2.2 |
| **G-TIMEOUT** | sem scanner de deadline | §2.3 |
| **G-CONTRACT-DUP** | dois contratos de contestação | §4 |
| **G-PROBE** | endpoints contest/review sem JWT/ABAC | §4.2 |
| **G-OUTPUT-DRIFT** | 3 vocabulários + shims + prompt fixo | §5 |
| **G-FORMVER** | formulário não versionado/fixado na avaliação | §5.3 |
| **G-PROMPT** | rubrica fixa no ai-gateway, invisível à curadoria | §5.1 |
| **G-CALIB** | calibração só rodou via seeder; laço estrutural não amarrado | §6 |
| **G-UI** | sem drill-down/self-view/fila; `available_actions` vazio; mostra `eval_status` | §7 |
| **G-THIN** | thin-session falha duro no submit | §8 |
| **G-S2.3** | sem dispatcher por janela de calendário | §9 |
| **G-DOCS** | arc6/arc13 marcam concluído o que não está | §0 |

---

## 12. Plano de tarefas com dependências

**Âncora:** `T1` (modelo de estado) + `T2` (segmento) destravam tudo; **G-FIN (`T3`)**
destrava os relatórios.

### Fase 0 — Fundação
- **T1 — Modelo de estado canônico** (`result_state` + `round` + `finalize_reason`;
  instance `status` + `skipped`; migração que concilia o CHECK). *Resolve G-STATE; base
  de quase tudo.*
- **T2 — Segmento como chave** (sampling fan-out por segmento de agente — exclui
  supervisor/evaluator/reviewer; unicidade parcial `(campaign_id, segment_id)`;
  `form_version` fixado). *Resolve G-SEGMENT/G-FORMVER (parte).* → detalhe §13.2
  - **T2a (evaluation-api)** — consumer de `conversations.participants` + acumulador de
    segmentos por sessão (sem mudança no Core; usa o evento existente). *Bloqueia o fan-out.*

### Fase 1 — Núcleo de finalização  *(dep. T1)*
- **T3 — Emitir `evaluation_finalized` em todas as transições terminais.** *Resolve
  G-FIN. Destrava relatórios.*
- **T4 — Scanner de deadline** (`deadline_at` computado na entrada do estado via
  calendar-api; scanner compara `now ≥ deadline_at` e aplica UPDATE atômico guardado por
  estado; revisão→avança/default `mantida`, contestação→finaliza). **Remover o consumer
  `workflow.events` do ciclo de contestação.** *Resolve G-TIMEOUT; decisão A.* *(dep. T1, T3)*

### Fase 2 — Contrato de contestação  *(dep. T1)*
- **T5 — Unificar contrato:** threads por critério + round no resultado + gate "tratar
  todas"; revisor humano via ABAC; depreciar endpoints Arc 6; corrigir bug
  `resolve_curation`. *Resolve G-CONTRACT-DUP, G-PROBE.*

### Fase 3 — Contrato de saída  *(form como fonte única)*
- **T6 — Enriquecer modelo do critério/form** (`type`, `scoring_guidance`, `auto_source`,
  `contestable`, `na_guidance`; versionamento). *Base do form-driven; resolve G-FORMVER.*
- **T7 — Saída form-driven** (validação + agregação derivadas do form; **JSON Schema
  montado upstream do form e passado ao `reason` via tool-use nativo** — ai-gateway
  stateless/genérico, caminho flat como compat; validação recursiva + retry como rede;
  **remover shims**). *Resolve G-OUTPUT-DRIFT; decisão 1.* *(dep. T6)*
- **T8 — Rubrica-template na UI Quality** + preview do prompt composto; remover
  `evaluation_rubric_v3` fixo do ai-gateway. *Resolve G-PROMPT.* *(dep. T6/T7)*

### Fase 4 — UI  *(dep. T5)*
- **T9 — Drill-down** como modo "preenchido + ações" do render do Forms (form + transcript
  com evidência clicável + rastreador). *Resolve G-UI (parte).*
- **T10 — Provisionamento ABAC + 3 papéis** (auto-visão/fila/supervisão); wiring de
  `available_actions`. *Resolve G-UI/G-PROBE (UI).* *(dep. T5 + roles auth)*
- **T11 — Relatórios Oficial vs Operacional** (default `finalized`; toggle provisório).
  *(dep. T3)*

### Fase 5 — Fluxo IA, calibração, degradação, dispatcher
- **T12 — Gate `ai_review`** (fora-de-faixa + erro; ajuste de nota antes de publicar).
  *(dep. T1, T5)*
- **T13 — Thin-session/erro → `error_rejected`** sem submit; semântica `skipped`.
  *Resolve G-THIN.* *(dep. T1)*
- **T14 — Validar calibração com dado real** + laço estrutural (edição de rubrica) ligado
  a *deploy epochs*. *Resolve G-CALIB.* *(dep. T8)*
- **T15 — Dispatcher por janela de calendário** (S2.3). *Resolve G-S2.3.* *(dep. T2)*
- **T17 — Janela de dados da campanha (período) + backfill** (`period_start/period_end`;
  filtro de janela no sampling forward; job batch de backfill histórico por segmento).
  *Detalhe §18.5.* *(dep. T2)*

### Fase 6 — Documentação
- **T16 — Corrigir `arc6-evaluation.md` e `arc13-review-contestation.md`** para refletir
  o alvo; mover ✅ falsos; atualizar `CLAUDE.md` (§ Arc 6 / Arc 13) e `TODO.md`.
  *Resolve G-DOCS.*

---

## 13. Detalhamento — T1 (modelo de estado) e T2 (chave por segmento)

> Fundação que destrava o resto. Decisões 2026-06-17: **T1 → eval_status como espelho
> depreciado (A); T2 → enriquecer `conversations.session_closed` com `segments[]` (A);
> fan-out exclui `supervisor`/`evaluator`/`reviewer`.**

### 13.1 T1 — Modelo de estado canônico

**`evaluation.results` — alvo:**

| Campo | Definição |
|---|---|
| `result_state` | `ai_review \| open \| under_review \| finalized \| error_rejected` (NOT NULL) |
| `round` | `SMALLINT NOT NULL DEFAULT 1` (1..max_rounds) — assume o papel de `current_round` |
| `finalize_reason` | `auto_ai \| uncontested \| upheld \| revised \| max_rounds \| contest_timeout \| review_timeout` (NULL até finalizar) |
| `final_score`, `finalized_at`, `deadline_at`, `evaluated_agent_type` | mantêm |

**Depreciar:** `contestation_state` (→ `result_state`), `action_required`, `resume_token`,
`pre_review_complete` (virou estado `ai_review`).

**`eval_status` (decisão A):** mantido como **espelho depreciado**, atualizado em lockstep
(`finalized → eval_status='locked'`) para não quebrar relatórios/UI/`lock_result` durante
a transição. Removido num passo posterior, com a migração dos consumidores para
`result_state`.

**`evaluation.instances.status` — alvo:** `scheduled → assigned → in_progress →
completed`, mais `skipped`, `error`, `expired`. **Remover** `under_review/reviewed/
contested/locked` (concerns do resultado que vazavam para a camada de trabalho).

**Migração (DECIDIDO 2026-06-17, opção b — sem framework novo).** O projeto não tem
framework de migração; o schema é aplicado por `ensure_schema` (`CREATE/ALTER … IF NOT
EXISTS` no `_DDL`). O problema do padrão atual é que `ADD COLUMN IF NOT EXISTS … CHECK`
**não** corrige CHECK já existente (foi o que deixou driftar). T1 entra como **DDL
explícito idempotente dentro do `ensure_schema`**, guardado por existência: (1) add colunas
novas (`result_state`, `finalize_reason`, `round`); (2) **backfill** `contestation_state →
result_state/finalize_reason` (roda uma vez, guardado por "result_state ainda nulo");
(3) **drop do CHECK antigo** de `contestation_state` + add CHECK de `result_state`;
(4) guard `infra/check_config_invariants.py` para barrar regressão. *(Framework de migração
versionada = melhoria futura, fora do escopo de T1.)* Backfill:

| `contestation_state` antigo (+ `eval_status`/`evaluated_agent_type`) | `result_state` / `finalize_reason` |
|---|---|
| `auto_finalized` | `finalized` / `auto_ai` |
| `closed_upheld` | `finalized` / `upheld` |
| `closed_revised` | `finalized` / `revised` |
| `closed_max_rounds` | `finalized` / `max_rounds` |
| `timeout_contestation` | `finalized` / `contest_timeout` |
| `timeout_review` | `finalized` / `review_timeout` |
| `pre_review_pending` | `ai_review` |
| `contestation_open` | `open` |
| `under_review` | `under_review` |
| null + ai_agent finalizado | `finalized` / `auto_ai` |
| null + humano | `open` |

**`finalize_reason` por transição** (set no momento de finalizar):

| Transição | reason |
|---|---|
| avaliação IA, IA não sinalizado | `auto_ai` |
| `open` → prazo sem contestação | `uncontested` (ou `max_rounds` se já no último round) |
| `under_review` → revisor mantém | `upheld` |
| `under_review` → revisada, último round | `revised` / `max_rounds` |
| `open`/`under_review` → prazo de contestação/revisão vencido | `contest_timeout` / `review_timeout` |

Todas as transições terminais usam **UPDATE atômico guardado** (`WHERE result_state IN
(...não-terminal)`), idempotente para redelivery/scanner.

### 13.2 T2 — Segmento como chave

**Fan-out no fechamento.** `_sample_on_close` itera os **segmentos de agente** da sessão
(`role ∈ {primary, specialist}`; **exclui `supervisor`/`evaluator`/`reviewer`**). Por
segmento × campanha ativa:

1. `segment_meta` (pool_id, channel, outcome, agent_type_id, `evaluated_agent_type`,
   duração) vem **do segmento**;
2. hard filter `campaign.evaluation_pool_id == segment.pool_id`;
3. `should_sample(segment_id, segment_meta, rules, counter)`;
4. dedup por **`(campaign_id, segment_id)`** (substitui `instance_exists_for_session`);
5. `create_instance(segment_id, session_id, form_version)`; `evaluated_agent_type`
   resolvido do segmento (humano → fluxo de contestação; IA → `ai_review`/auto).

**Fonte dos segmentos (REVISTO 2026-06-17 — sem mudança no Core).** A investigação
mostrou que o bridge **não** monta uma lista de segmentos no `session_closed` (só um
acumulador parcial `seg_signal`, humano/hook) — enriquecer o evento ali seria custoso e
arriscado. Porém o bridge **já publica** `conversations.participants` por segmento, com
**tudo** que o sampling precisa: `segment_id`, `role` (primary/specialist), `agent_type`
(human/native/external), **`user_id`** (humano, de `human-{userId}` — destrava a posse do
5a), `pool_id`, `agent_type_id`, `flow_id`, `outcome`, `duration_ms`.

**Mecanismo T2a (na evaluation-api, não no Core):** um consumer de
`conversations.participants` **acumula** os segmentos por sessão (Redis
`{tenant}:eval:segs:{session_id}`); no `session_closed`, o `_sample_on_close` lê o
acumulado e faz o **fan-out** sobre os segmentos de agente (primary/specialist). Vantagens:
zero mudança no Core, usa evento existente, traz `user_id` do avaliado.

> **Subtarefa T2a (evaluation-api):** consumer de `conversations.participants` +
> acumulador por sessão. Sem dependência no Core/bridge.

**Schema.** Índice **único parcial** `(campaign_id, segment_id) WHERE segment_id IS NOT
NULL` (linhas legadas têm `segment_id` null → histórico intacto). `session_id` mantido
como contexto; uma sessão passa a ter N instances (uma por segmento avaliável).

**`form_version` pin.** Coluna `form_version` adicionada já em T2 na instance/result,
populada com a versão corrente do form (default `1` até T6 dar semântica de versão ao
formulário). A fixação existe desde já; T6 só a preenche.

**Alinhamento com relatórios.** Como os relatórios de performance por agente (Arc 5) já
chaveiam por segmento, a avaliação passa a casar 1:1 com `analytics.segments` — junção
limpa, sem o descasamento sessão↔agente de hoje.

---

## 14. Detalhamento — Fase 1: T3 (finalização) e T4 (scanner)

> Depende de T1 (estado canônico). T4 depende de T3 (roteia pela finalização única).

### 14.1 T3 — Emissão única de `evaluation_finalized`

**Princípio: uma única função `finalize(result, reason)` é o único ponto que emite
`evaluation_finalized`.** Todos os caminhos terminais roteiam por ela — mata o G-FIN, que
existia porque a emissão estava só no ramo `ai_agent` do `_ingest_core`.

Chamadores que passam a usar `finalize()`:

| Caminho | reason |
|---|---|
| `_ingest_core` (IA não sinalizado) | `auto_ai` |
| `ai_review` → ajuste e publica (IA) | `auto_ai` |
| `submit_review` **no último round**, `mantida` | `upheld` |
| `submit_review` **no último round**, `revisada` | `revised` |
| scanner — `open` expirado | `uncontested` |
| scanner — `under_review` expirado (default mantida) | `review_timeout` |

**`finalize()` faz, atomicamente e idempotente:**

1. **Consolidação da nota** — para cada critério, `effective_value` = valor do avaliador,
   **substituído pelo `score_override` da última revisão `revisada`** quando houver;
   agrega pelos pesos do **formulário** → `final_score` + `final_scores_by_dimension`.
   (Materializa "override vale na hora como provisória; a nota real consolida só no
   `finalized`".)
2. **UPDATE guardado** — `SET result_state='finalized', finalize_reason=…,
   final_score=…, finalized_at=now(), process_duration_ms=… WHERE id=$ AND
   result_state <> 'finalized'`. Se já finalizado → no-op, **sem evento duplicado**
   (seguro para race scanner × ação humana × redelivery).
3. **Emite `evaluation_finalized`** com payload estendido:
   `+ finalize_reason, segment_id, round, evaluated_agent_type, form_version,
   final_scores_by_dimension, process_duration_ms`. (Hoje carrega só `final_score`/
   `contestation_state`/`process_duration_ms`.) Relatórios passam a fatiar por reason,
   segmento e versão de formulário.

`process_duration_ms` = tempo de `produced` a `finalized` (duração do ciclo de
contestação/revisão).

### 14.2 T4 — Scanner de deadline

**`deadline_at` é computado na ENTRADA do estado** via calendar-api (horário comercial),
não no laço:

- entra em `open(round N)` → `deadline_at = add_business_duration(now,
  campaign.contest_deadline_hours, evaluation_calendar_id)`;
- entra em `under_review(round N)` → `deadline_at = add_business_duration(now,
  campaign.review_deadline_hours, evaluation_calendar_id)`.

(Campos `contest_deadline_hours`/`review_deadline_hours`/`use_business_hours` já existem na
`ContestationPolicy`; `evaluation_calendar_id` na campanha.)

**Scanner** = tarefa de fundo periódica (~60s, no molde dos consumers do `main.py`):
`SELECT … WHERE result_state IN ('open','under_review') AND deadline_at <= now()`. Para
cada linha, aplica a transição de timeout **roteando por `finalize()`**:

- `open` expirado → `finalize(uncontested)` (o humano teve a janela e não contestou — nada
  a revisar; vale em qualquer round);
- `under_review` expirado → default **mantida** em todos os critérios pendentes →
  `finalize(review_timeout)` (distingue, em relatório, "revisor agiu e manteve" de "revisor
  deixou vencer").

UPDATE atômico guardado por estado → concorrência segura com a ação humana. O scanner
**nunca recomputa horário comercial**: só compara `now() >= deadline_at`.

> **Limpeza de enum:** `contest_timeout` (§13.1) é subsumido por `uncontested` — removível.
> O `ai_review` (gate IA) usa timeout **técnico** (não horário comercial); em stall →
> `error_rejected` ou prossegue conforme política (detalhe em T12).

### 14.3 Dependência de UX — notificar o avaliado ao entrar em `open`

Para a inação ser uma escolha real, o avaliado precisa **saber** que há uma avaliação
aberta e até quando contestar. Ao entrar em `open`, disparar **notificação ao humano
avaliado** (com `deadline_at`), ligada à auto-visão (§7.4). Sem isso, "não contestou"
vira "não soube".

**Item configurável (DECIDIDO 2026-06-17):** campo de campanha
`notify_evaluated_on_open` (default **ligado**, **desligável por campanha**); opcionais
de canal e antecedência de reforço antes do `deadline_at`. Subtarefa **T4b** — gatilho de
notificação no enter-`open` + superfície na auto-visão (dep. T9/T10).

---

## 15. Detalhamento — Fase 2: T5 (contrato único de contestação)

> Depende de T1. Unifica os dois contratos: **threads por critério** (dado) sob
> **envelope de round/estado no resultado** (Arc 6), com ABAC. Mata G-CONTRACT-DUP e
> G-PROBE.

### 15.1 Endpoints canônicos e deprecações

| Canônico | Quem (ABAC) | Ação |
|---|---|---|
| `GET /v1/evaluation/instances/{id}/threads` | papel autorizado | histórico **por critério** |
| `POST /v1/evaluation/instances/{id}/contest` | avaliado (`contestar`, dono do segmento) | contesta um **conjunto** de critérios no round N |
| `POST /v1/evaluation/instances/{id}/review` | revisor humano (`revisar`, scope pool) | decide **todos** os critérios do round N |
| `POST /v1/evaluation/instances/{id}/ai-review` | sistema (avaliador/revisor IA) | gate dos sinalizados (T12) |

| Depreciar/remover | Em favor de |
|---|---|
| `/v1/evaluation/contestations` (+ `/{id}`, `/adjudicate`) | threads por critério |
| `/v1/evaluation/results/{id}/review` (approved/rejected) | `/instances/{id}/review` (mantida/revisada) |
| `X-Admin-Token` (adjudicação) + header auth `X-User-ID`/`X-Author-Type` | JWT + ABAC |

### 15.2 Semântica de round (contestação / réplica / tréplica)

`round` é o **contador de ciclo humano** (1 = contestação, 2 = réplica, 3 = tréplica;
`max_rounds` da campanha). O **avaliado dirige** o avanço:

- `open(N)` + contesta conjunto → `under_review(N)`.
- `review(N)` (todos decididos) → **se N < max_rounds → `open(N+1)`** (janela de apelação
  do avaliado, novo `deadline_at`); **se N == max_rounds → `finalize()`**.
- `open(N)` sem contestar até o prazo → `finalize(uncontested)` (scanner).
- `under_review(N)` sem revisar até o prazo → default **mantida** → `finalize(review_timeout)` (scanner).

> **A reabertura acontece por rounds restantes — independente de `upheld`/`revised`.** Um
> `upheld` em round < max também reabre, dando ao avaliado a apelação seguinte; o avaliado
> escolhe re-contestar (avança) ou aceitar (deixa o prazo finalizar em `uncontested`). A
> revisão **só finaliza no último round**. A nota provisória do round N (com overrides)
> vale como base do round N+1; a nota real consolida em `finalized` (§14.1).

> **Assimetria intencional do `mantida`:** um **upheld ativo** (revisor agiu e manteve)
> **reabre** se há round restante — o avaliado ainda tem a apelação seguinte. Mas
> **revisor que não age** dentro do prazo encerra em `review_timeout` (§14.2). Decisão do
> revisor = o ciclo continua; ausência do revisor = encerra.

### 15.3 Gate "tratar todas" (server-side, não só UI)

`POST …/review` exige decisão para **o conjunto exato** de critérios contestados no round
corrente; faltando algum → **409 `pending_contestations`**. É a aplicação no backend da
trava da UI ("Salvar revisão" bloqueado), garantindo que nenhum critério contestado fique
sem decisão.

### 15.4 ABAC e guardas de identidade (mata G-PROBE)

Substitui `_get_user`/`X-Author-Type` por `_decode_jwt` + `_check_abac_permission`
(helpers já existem em `router.py`). **Permissões por usuário (`module_config`, perfil),
orthogonais ao role** — 6 campos por round, cada um com `scope[]`:

| Lado avaliado (na própria avaliação) | Lado revisor (≠ avaliado) |
|---|---|
| `contestar` — contestação (round 1) | `revisar` — round 1 |
| `contestar_replica` — réplica (round 2) | `revisar_replica` — round 2 |
| `contestar_treplica` — tréplica (round 3) | `revisar_treplica` — round 3 |

- **`contest`** — JWT + campo de contestação **do round corrente** + **caller é o dono do
  segmento avaliado**.
- **`review`** — JWT + campo de revisão **do round corrente** + `scope` no pool +
  **guarda: revisor ≠ avaliado**.
- **Sem nenhum campo → read-only** (supervisão/observador; não contesta nem revisa). Sem
  necessidade de regra à parte — é o default natural do ABAC.
- O ABAC limita a **profundidade de apelação**: faltando o campo do round seguinte, a
  janela expira em `uncontested`. Compõe com `max_rounds` da campanha.
- Nenhum endpoint de ação aceita mais auth só por header nem `X-Admin-Token`.

Além destes 6 campos de **ação**, o módulo tem campos de **gestão** (formulário, rubrica/
prompt, campanha, curadoria) — definidos em §16.3 (T8).

### 15.5 Threads (modelo de dado)

`evaluation.contestation_threads` por `(instance, criterion_id, round, author_type)`,
append-only. Entry: `text` (justificativa), `decision` (`upheld|revised`),
`score_override`, `evidence_entries[]`, **`target` (`value|na`** — o que se contesta:
nota ou aplicabilidade). `author_type`: `evaluator_ai` (round 1, no ingest),
`human_agent` (contest), `human_reviewer` (review). O campo `dimension_id` atual passa a
guardar `criterion_id` (o comentário do schema já prevê o fallback).

### 15.6 `finalize_reason` — produzido vs disponível

O runtime produz ativamente **`{auto_ai, uncontested, upheld, revised, review_timeout}`**;
a **profundidade** do ciclo está no campo `round` (não precisa de reason `max_rounds`).
`max_rounds`/`contest_timeout` permanecem no enum apenas para **fidelidade do backfill**
de dados legados (§13.1), não são emitidos pelo fluxo novo.

---

## 16. Detalhamento — Fase 3: T6 (modelo do critério) + T7 (saída form-driven) + T8 (rubrica na UI)

> O formulário vira **fonte única** de prompt, schema, validação e agregação. Mata
> G-OUTPUT-DRIFT, G-FORMVER, G-PROMPT.

### 16.1 T6 — Modelo do critério/form + versionamento

**Campos por critério** (alvo, §5.3): `type` (`score|boolean|choice|text|auto_computed`),
`question`, `scoring_guidance`, `scale` (score→min/max; choice→opções+mapa; boolean→
true/false→nota), `weight`, `allow_na`+`na_guidance`, `applies_when`, `auto_source`
(auto_computed), `evidence_required`, `contestable` (derivado: auto→false).

**Migração dos forms existentes** (sem reescrita): critério atual → `type=score`,
`question=description`, `scale=0..max_score`, `scoring_guidance=null`,
`evidence_required=true`, `contestable=true`. Os tipos `boolean/choice/text/auto_computed`
são opt-in.

**Versionamento (alinha ao Skill Deploy Lifecycle):** form com `deploy_status`
`draft|published` + `version`. Publicar **snapshot imutável** da definição (`form_version`);
edição de form já usado → **nova versão** (drafts editam livre). Instances/results
**pinam o `version`** (T2); avaliações em curso mantêm a versão sob a qual nasceram. Liga
a *deploy epochs* (Arc 6 Fase 2) para comparar qualidade antes/depois de mudar o form.

**UI (FormsPage):** por critério — seletor de `type`, `scoring_guidance`, editor de opções
(choice), rótulos true/false (boolean), `auto_source` (auto_computed), toggle
`evidence_required`, `na_guidance`; no form — controles de versão/publish.

### 16.2 T7 — Saída form-driven (schema + validação + agregação; remover shims)

**Schema construído do form** (camada de composição **upstream**; ai-gateway não monta):
por critério não-auto → propriedade tipada (`score`→number[min,max,nullable se `allow_na`];
`boolean`→boolean; `choice`→enum; `text`→string) + `na` + `justification` + `evidence[]`
(`{stream_entry_id, excerpt, relevance_note}`). Saída = **`criterion_responses[]`** +
narrativa opcional (`overall_observation/highlights/improvement_points/compliance_flags`).

- **`overall_score` NÃO é saída do LLM** — recomputado dos valores por critério pelos
  pesos do form (bottom-up: critério → dimensão → geral). O número do LLM é descartado.
- **`dimension_threads` deixa de ser saída separada** — os threads round-1 nascem **por
  critério** das `criterion_responses` (autor `evaluator_ai`), eliminando o
  descasamento `value/evidence_refs` (YAML) × `score/evidence` (Zod).

**Conveyance:** tool-use nativo, `input_schema` = o schema do form, `tool_choice` forçado
(§5.4). O contrato do `reason` aceita JSON Schema; caminho flat fica como compat.

**Validação no submit:** contra a definição do form (criterion_id existe, tipo bate, regra
de `na`, faixa). **Remover todos os shims** (`observation→justification`, default de
`evidence_entries`, coerção de `compliance_flags`, `value`×`score`) e o `output_schema`
estático do `agente_avaliacao_v1.yaml` (o schema passa a ser resolvido do
`eval_context.evaluation_form` em runtime). Validação recursiva + retry como rede.

**Agregação:** `calculateScores` alinhado ao modelo do critério (tipos + pesos); é a fonte
do `final_score`/`final_scores_by_dimension` (consolidação em §14.1).

### 16.3 T8 — Rubrica-template na UI Quality + preview do prompt

- **Template como config** (evaluation-api, pois forms/campanhas são single-source ali):
  **default por tenant + override por campanha**; **versionada** (ancora em *deploy
  epochs*). Editável na UI Quality (curador/designer, ABAC).
- **Composição** = template + critérios do form (com `scoring_guidance`) + RAG
  (`CalibrationNote` por `criterion_id` + knowledge) + transcript do segmento. Roda na
  camada de composição; ai-gateway segue stateless.
- **Preview do prompt composto** na UI (template + critérios de um form exemplo + amostra
  de calibração/knowledge) — a curadoria vê exatamente o que o avaliador recebe.
- **Remover** o `evaluation_rubric_v3` fixo do ai-gateway/prompt registry.
- **Calibração estrutural (§6):** Curation/Calibration liga `CalibrationNote → rubrica/
  critério implicado → editar` (com permissão); edições versionadas, comparáveis por
  *deploy epochs*.

**Superfície "Rubrica / Prompt" (nova, no grupo Quality** — ao lado de Forms, Campaigns,
Knowledge, Calibration, Curation). Edita a rubrica-template (default tenant + override
campanha) e mostra o **preview do prompt composto**. Hoje **não existe** — o prompt é o
`evaluation_rubric_v3` fixo no ai-gateway.

**Fluxo de alteração (espelha o Skill Deploy Lifecycle / versão de form):**

1. mantenedor edita em **rascunho** (edita livre);
2. **preview** do prompt composto contra um form escolhido (valida);
3. **publicar** → **versão imutável** da template + registra **deploy epoch**;
4. avaliações **novas** usam a versão nova; **em curso** ficam intactas (pinam a versão de
   template, como `form_version` em §16.1);
5. **comparação antes/depois** via *deploy epochs* (Arc 6 Fase 2);
6. entrada de **calibração estrutural**: curador navega `CalibrationNote → rubrica/critério
   → editar → publicar` nova versão.

Drafts editam à vontade; **publish é a ação gated e versionada**. Rollback = republicar
uma versão anterior.

**ABAC — campos de GESTÃO do módulo `evaluation`** (por usuário/perfil, orthogonais ao
role; complementam os campos de **ação** da §15.4):

| Campo (code) | Label (UI) | Concede |
|---|---|---|
| `gerir_formulario` | Formulários | CRUD de form + `scoring_guidance` por critério (FormsPage) |
| `gerir_rubrica` | Rubrica/Prompt | editar/publicar a rubrica-template + preview (perfil "mantenedor de prompt") |
| `gerir_campanha` | Campanhas | CRUD de campanha (CampaignsPage) |
| `curar` | Curadoria | fila de curadoria + resolver `CalibrationNote` (laço mole) |

Um perfil combina o que precisar: "mantenedor de prompt puro" = só `gerir_rubrica`;
"designer de qualidade" = `gerir_formulario` + `gerir_rubrica` + `gerir_campanha` + `curar`.
**Sem campo → read-only** (default natural, §15.4).

---

## 17. Detalhamento — Fase 4: UI (T9 drill-down + T10 ABAC/papéis + T11 relatórios)

> Depende de T5 (contrato) e T3 (finalização). Mata G-UI e a parte de UI do G-PROBE.

### 17.1 T9 — Drill-down como modo "preenchido + ações" do render do Forms

**Reusa o componente do Forms** renderizado com os valores do avaliador (não é tela nova).
Nova rota `Result → Drill-down` (inexistente hoje). Por critério:

- **Render tipado:** `score`→número; `boolean`→true/false; `choice`→opção; `text`→
  qualitativo (sem nota); `auto_computed`→métrica, **cinza, não contestável**.
- valor + justificativa + **evidência clicável** (chip → rola e **destaca** a mensagem no
  transcript via `stream_entry_id`);
- badge de estado por critério (contestado / revisada +Δ) + **timeline append-only** do
  critério (avaliador → contestação → revisão, por round).

**Transcript** em visão dividida; **mascaramento por papel** (revisor vê `original_content`;
avaliado vê mascarado); **escopo no segmento** (janela do segmento, expansível ao contato).

**Trava server-side** (§15.3): "Salvar revisão" bloqueado na UI **e** 409
`pending_contestations` no backend. Rastreador "X de Y contestações tratadas".

**Lista (Evaluations) corrigida** (§7.1): Status = `result_state`+`round`+`finalize_reason`;
coluna **"Agente avaliado (segmento)"**; `available_actions` (ABAC); Date = `deadline_at`/
`finalized_at`.

### 17.2 T10 — Provisionamento ABAC + três papéis

**`available_actions` deriva de `result_state` + ABAC + posse** (reescreve
`_compute_available_actions`, que hoje depende de `action_required` do workflow):

`available_actions` casa o **round corrente** com o campo ABAC certo (§15.4):

| Condição (round R) | Ação |
|---|---|
| `open(R)` ∧ **dono do segmento** ∧ campo de contestação do round R | `contest` |
| `under_review(R)` ∧ caller **≠ avaliado** ∧ campo de revisão do round R | `review` |
| caso contrário (incl. nenhum campo) | `[]` (read-only) |

(R=1→`contestar`/`revisar`; R=2→`contestar_replica`/`revisar_replica`;
R=3→`contestar_treplica`/`revisar_treplica`.)

**Provisionamento (a lacuna do G-UI):** semear `auth.module_registry` com os 6 campos do
módulo `evaluation` e atribuí-los **ao `module_config` do usuário (perfil)**, não ao role —
hoje vêm vazios até para Admin, por isso Actions "—".

**Uma tela só, escopada pelo viewer (DECIDIDO 2026-06-17).** Não há "Minhas Avaliações"
como tela separada — é a **mesma tela de Evaluations**, escopada pela identidade do viewer.
Separação dura: **role + Grupo + pool = visibilidade de dados; ABAC = ação.** A **ABAC
nunca amplia visibilidade** — só governa o que se faz nas linhas já visíveis.

**Visibilidade (fronteira dura por role):**

| Role | Vê |
|---|---|
| atendente | **só os próprios** (segmentos com `agent_user_id == caller`) — estar num Grupo **não** dá visão dos colegas |
| supervisor | as **pessoas do(s) seu(s) Grupo(s)** — `supervised_groups/agent_types/user_ids` (Arc 9), limitado ao Grupo |
| admin | tudo |

`accessible_pools` (Arc 7) entra como filtro adicional de linha. **Sem união por ação:**
quem precisa revisar fora do próprio Grupo recebe um **perfil de revisão com Grupo
ampliado** (Arc 9), não uma exceção de visibilidade.

- **Fila de revisão** = linhas **dentro do escopo** com `available_actions` ≠ [] (filtro,
  não ampliação).
- **Componente único** (lista + drill-down); muda o filtro de escopo e os botões por linha.
  Supervisão/observador sem campos de ação = read-only.
- **Gate de nav:** a tela passa a ser alcançável por **qualquer campo do módulo**, não só
  supervisor+; o escopo auto-estreita pelo role. Operador pode ter atalho "Minhas
  Avaliações" no Console → a **mesma** tela, escopo self.
- Reusa Arc 7 (pools) e Arc 9 (Grupos); só o escopo **self do atendente** (filtro por agente
  do segmento, via T2) é novo.

### 17.3 T11 — Relatórios Oficial vs Operacional

Dois modos **explícitos, nunca blendados**:

- **Oficial** (default) — só `result_state='finalized'` (lê `evaluation_finalized`); é o
  invariante de qualidade. Com T3, as avaliações **humanas finalmente aparecem**.
- **Operacional** — inclui provisório (em andamento), rotulado.

Fatiamento por `finalize_reason`, `segment_id`, `form_version` (payload estendido §14.1).
ClickHouse/analytics passam a agrupar por reason/segmento/versão.

### 17.4 Supervisão — resolvido pelo ABAC

**RESOLVIDO 2026-06-17:** "supervisor read-only" não é regra à parte — é simplesmente um
**perfil sem campos de ação** (§15.4). Um eventual override (forçar `finalize`/ajustar nota
fora do ciclo) fica **fora de escopo**; se um dia houver demanda, entra como capacidade
explícita e auditável, não como atributo implícito do role — para não furar o ciclo de
contestação.

---

## 18. Detalhamento — Fase 5: T12 (ai_review) + T13 (thin/erro) + T14 (calibração) + T15 (dispatcher)

### 18.1 T12 — Gate `ai_review` (sinalizados)

Após o avaliador IA submeter, o resultado é **sinalizado** se `score > config_max` ∨
`score < config_min` ∨ **erro** de avaliação. Sinalizado → `ai_review` (gate **antes** de
publicar); não-sinalizado → direto a `open` (humano) ou `finalized(auto_ai)` (IA).

- **Limiares** `config_min`/`config_max`: por campanha, via `CurationSamplingRules`
  (rule_type de faixa — reusa a infra existente).
- **Revisor IA** (≠ avaliador) revisa o sinalizado e **pode ajustar a nota** (override por
  critério) antes de publicar; recomputa `final_score` → `open`/`finalized`. Reusa o
  `pre_reviewer_ai`/endpoint `ai-review` (§15.1), com gatilho concreto (faixa+erro).
- Pode emitir `calibration_signal` → fila de curadoria (laço mole, §6/T14).
- **Timeout técnico** (não horário comercial): em stall → **publica sem ajuste** + log
  (a avaliação é válida, só não revisada) — não vira `error_rejected`. *(default recomendado.)*
- Para avaliado **IA**, `ai_review` é a **única** revisão (sem contestação humana — §1
  pressuposto 2).

### 18.2 T13 — Degradação thin-session / erro

| Situação | Caminho |
|---|---|
| **Thin-session** (sem dados suficientes) | avaliador detecta no `evaluation_context_get`, marca a instance **`skipped`**, **não chama submit** (evita o hard-fail atual) |
| **Erro de avaliação** | instance **`error`** → `ai_review` **classifica**: recuperável → retry (re-dispatch / mudar status / **delegate** ao avaliador) vs irrecuperável → **`error_rejected`** (terminal, auditável) |

- **Limiar de "thin"** (mín. de turnos/conteúdo) **configurável por campanha**. *(knob.)*
- `skipped` (não avaliável, sem culpa do avaliador) ≠ `error` (avaliador falhou) ≠
  `error_rejected` (erro classificado como irrecuperável).
- Relatórios de qualidade excluem `skipped` e `error_rejected`.
- Skill `agente_avaliacao_v1`: ramo thin-session → terminal que marca `skipped` sem submit;
  `on_failure` → `error`.

### 18.3 T14 — Validar calibração com dado real (dois laços)

A maquinaria existe mas **só rodou no seeder**. T14 = validar ponta-a-ponta + corrigir +
enriquecer:

- **Laço mole (RAG):** `calibration_signal` → fila de curadoria → curador resolve
  (`approved/recalibrated/bias_flagged`) → `CalibrationNote` → publica no
  `mcp-server-knowledge` → RAG na próxima avaliação → **verificar que o scoring desloca**.
- **Corrigir o bug** em `resolve_curation` (referência a `row` inexistente — deveria ser
  `_cr_row`; `NameError` em `recalibrated`/`bias_flagged`).
- **Enriquecer:** `CalibrationNote.criterion_id` para o RAG injetar a nota **no bloco do
  critério certo** (§6).
- **Laço estrutural:** curador edita rubrica-template/`scoring_guidance` (T8) → versionado
  → *deploy epoch* → comparação de qualidade antes/depois. Validar essa ligação.

### 18.4 T15 — Dispatcher por janela de calendário (S2.3)

Tarefa agendada que despacha as instances `scheduled` de **cada campanha na sua janela**:
lê `campaign.schedule` (já existe em `evaluation.campaigns`) + `evaluation_calendar_id`
(calendar-api); quando dentro da janela, emite `evaluation.requested` para as `scheduled`
(respeitando lote/rate). **Idempotente** — não re-despacha `assigned`/`in_progress`.
Substitui o `POST /campaigns/{id}/dispatch` manual ("Rodar agora"), que permanece para
disparo sob demanda. *(dep. T2 — instances já por segmento.)*

### 18.5 T17 — Janela de dados da campanha (período) + backfill histórico

**Lacuna:** a campanha hoje é **forward-only** (amostra no `session_closed`, da ativação em
diante, sem fim). Falta uma **janela de dados explícita** para reprocessar passado, olhar a
mesma massa sob outro formulário, ou validar uma nova versão de prompt.

**Eixo novo, ortogonal ao `schedule`:**

| Campo | É | Não confundir com |
|---|---|---|
| `period_start`/`period_end` (novos) | **quais sessões** entram (por `closed_at`) | — |
| `schedule` (existe) | **quando** o avaliador roda (timing do dispatch) | o período de dados |

`evaluation.campaigns += period_start TIMESTAMPTZ (default ativação), period_end
TIMESTAMPTZ NULL`. Escopo = `closed_at ∈ [period_start, period_end]` (fim nulo = aberto).
**Três modos sem flag extra:**

- `start=agora, end=null` → streaming forward (comportamento atual);
- `start=passado, end=set` → histórico/bounded (reprocessa janela fechada);
- `start=passado, end=null` → backfill do passado **+** continua forward.

**Dois mecanismos (DECIDIDO 2026-06-17):**

- **Forward** (`start ≥ agora`): `_sample_on_close` ganha **filtro de janela** (descarta
  sessões fora de `[start, end]`).
- **Backfill** (`start` no passado): **job batch** enumera os **segmentos já fechados** na
  janela a partir do store persistido (Stream Persister / Session Replayer; `analytics.
  segments`) e cria as instances; o avaliador reconstrói o transcript via Hydrator/
  ReplayContext. Encaixa junto ao dispatcher (§18.4). *(dep. T2.)*

**Liga à validação de prompt (T8 + deploy epochs):** rodar uma campanha (ou rubrica nova)
sobre a **mesma janela histórica** e comparar; a chave `(campaign_id, segment_id)` permite
múltiplas avaliações sobre o mesmo segmento (perspectivas distintas sem colisão).

---

## 19. Detalhamento — Fase 6: T16 (correção da documentação)

A correção **não espera** a implementação — é correção de **verdade** (docs afirmam ✅ o
que o código não faz):

- **`docs/arcos/arc6-evaluation.md`** — corrigir o "§ Caminho do avaliador real" e os ✅ de
  Arc 6 v2: o consumer de `workflow.events` **não finaliza** (só `lock`); o ramo humano
  **não emite** `evaluation_finalized`. Apontar para esta spec como alvo.
- **`docs/arcos/arc13-review-contestation.md`** — o invariante "`evaluation_finalized` é a
  única fonte de verdade" é **alvo**, não realidade atual; corrigir os ✅ de finalização/
  contestação por dimensão (dois contratos paralelos); o enum `contestation_state`.
- **`CLAUDE.md`** — atualizar os resumos § Arc 6 / Arc 13 e a "Real-evaluator persistence
  path" para o modelo novo (`result_state`, chave por **segmento**, contrato único,
  form-driven); referenciar `docs/product/evaluation-reconciliation-spec.md`.
- **`TODO.md`** — ligar os itens do § Shakedown pós-submit Arc 13 às tarefas T1–T16.

> **Regra de manutenção (CLAUDE.md):** quando cada tarefa for implementada, o doc de Arc
> correspondente é atualizado e a entrada vai ao `CHANGELOG.md`; nenhum ✅ permanece aqui
> ou nos arcos sem código que o sustente.

---

## 20. Fechamento — status e sequência

**Status:** spec-alvo completa, decisões fechadas (ver marcadores *DECIDIDO/RESOLVIDO
2026-06-17*). Nenhuma decisão de produto em aberto.

**Decisões consolidadas:** terminal único `finalized` + `finalize_reason`; chave por
**segmento**; contrato único (threads por critério + envelope de round); ABAC de 6 campos
por round (read-only = sem campos); form como fonte única (prompt/schema/validação/
agregação) via tool-use; finalização na evaluation-api + scanner (workflow removido do
ciclo); rubrica-template versionada na UI Quality; relatórios Oficial × Operacional.

**Sequência de execução** (§12, detalhe §13–§19): fundação **T1 + T2 (+T2a Core)** →
finalização **T3 + T4** → contrato **T5** → saída **T6 → T7 → T8** → UI **T9/T10/T11** →
fluxo IA/degradação/dispatcher **T12–T15** → docs **T16**. Âncoras: T1/T2 destravam tudo;
**T3 (G-FIN) destrava os relatórios**.

---

## Apêndice — arquivos-chave (leitura dirigida)

- `evaluation-api/src/plughub_evaluation_api/`: `router.py` (`_ingest_core`/`dispatch_campaign`/
  results+contestations), `main.py` (consumers workflow/sampling/ingest; `_sample_on_close`),
  `contestation_router.py` (contest/review/pre-review/curation), `db.py` (enums/`finalize_result`).
- `mcp-server-plughub/src/tools/evaluation.ts` (`evaluation_submit` + shims, `evaluation_context_get`).
- `skill-flow-engine/skills/agente_avaliacao_v1.yaml` (output_schema; `.context.events`).
- `ai-gateway/src/plughub_ai_gateway/`: `reason.py` (`_format_schema`), `models.py` (`OutputFieldSchema`).
- `platform-ui/src/modules/evaluation/` (AvaliacoesPage, CampaignsPage, FormsPage) + `api/evaluation-hooks.ts`.
- Docs a corrigir: `docs/arcos/arc6-evaluation.md`, `docs/arcos/arc13-review-contestation.md`.
