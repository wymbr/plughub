# ADR — Wrap-up de fim-de-segmento como item de pull inbox destacado (Camada E2)

> **Status:** Proposto (2026-07-24). Fecha o fork de desenho da **Camada E2** do arco "detach de hooks de
> finalização + pull direcionado + ACW". **Decisão de sequenciamento (2026-07-24): Path α, renderer-first** —
> construir o renderer de aprovação (arco próprio) antes; wrap-up-α por cima. β retido como alternativa (§2).
> **Contexto de origem:** A/B/C/D ✅ + E1 ✅ (Forma A aposentada). Falta o miolo: o **wrap-up humano** ainda é
> **inline** (segura a conferência → infla AHT/G1; bloqueia o humano via `wrap_up_pending`). Este ADR resolve
> COMO o wrap-up vira destacado sem perder a coleta do form nem a atribuição ao segmento.
> **Relacionados:** [`docs/product/finalization-hooks-detach-and-directed-pull-design.md`](../product/finalization-hooks-detach-and-directed-pull-design.md)
> (arco), [`docs/guias/conference-mechanics.md`](../guias/conference-mechanics.md) (Mudança 25 = Camada D),
> [`docs/guias/pool-hooks.md`](../guias/pool-hooks.md) (`dispatch: inline|detached`),
> [`docs/adr/adr-human-approval-workflow-step.md`](adr-human-approval-workflow-step.md) (pull inbox + DialogForm),
> [`docs/product/dialog-primitive-and-runner-design.md`](../product/dialog-primitive-and-runner-design.md)
> (DialogForm + `form_get` + menu dinâmico), Camadas B (`assigned_to`), ~~C (`acw_gate`/`acw_pending`)~~ *(revertida
> na Phase 0, removida em 2026-07-29 — ver D4)*, D (detached).
> **Emenda (2026-08-11, §7 — proposta):** visibilidade seletiva da sessão de wrap-up em Analytics/Sessions
> (`scope=contacts|all`), sem reabrir a contaminação de TMA fechada em E2f.

---

## 1. Contexto e a tensão central

O wrap-up (`retencao_humano.on_human_end → wrapup_ia`, `agente_wrapup_v1`) hoje é um **especialista de
conferência inline**: coleta um form do **agente humano** via menus (resumo / classificação / motivo de
escalação / próximos passos), com `visibility` agent-only. O bridge lê `pipeline_state.results` na conclusão do
hook e chama `_apply_wrapup_to_segment` → `_republish_segment_from_signal` (grava o **outcome do segmento
humano** em `session:{origin}:seg_signal:{seg}` e re-publica `participant_left` p/ o analytics).

Ser inline é o que **segura o contato/conferência** (AHT inflado = **G1**) e bloqueia o humano
(`wrap_up_pending`). A Camada D já destaca hooks de finalização — mas a Camada D implementou `detached` como
**workflow webhook fire-and-forget** (`_fire_detached_hook`). O wrap-up **não cabe** nesse molde: o humano já
saiu (`agent_done`), e o wrap-up **precisa de input do humano** — então não pode ser fire-and-forget nem um
workflow que auto-completa.

**A tensão:** o desenho do arco diz que o wrap-up destacado vira "**item de pull inbox `assigned_to` o
humano** → claim → **DialogForm de disposição** → submete → gravado contra o segmento". Mas:
- Um **pool webhook** (perfil workflow, onde o `_fire_detached_hook` aterrissa) **não pode `menu`/`notify`**
  (invariante Arc 19) — não renderiza form interativo.
- Renderizar um DialogForm a um humano que reivindicou uma **workflow suspensa** é exatamente o **renderer de
  aprovação** (ADR aprovação, A3) — que **ainda não existe** (proposto).

Ou seja: reusar o webhook + renderer de aprovação (Path α) **acopla a E2 a trabalho não construído**. Precisamos
de um caminho que feche o G1 **agora**, self-contained.

## 2. Os dois caminhos

### Path α — webhook-workflow + DialogForm no Console (renderer de aprovação)
O bridge dispara o wrap-up como **workflow webhook** a um pool pull; a workflow **suspende** apresentando um
DialogForm; o humano reivindica no inbox e o **Console renderiza o DialogForm** (reuso do renderer de aprovação,
A3), submetendo via `workflow_resume`; a workflow grava e completa.
- **Prós:** canal-agnóstico; DialogForm versionado/editável; unifica wrap-up + aprovação + survey-collect sob
  "claim de workflow suspensa → DialogForm → resume".
- **Contras:** **depende do renderer de aprovação (A3), não construído**; é o caminho maior.

> **Correção/achado (2026-07-24):** o β abaixo pressupõe um *skill agente* renderizando menus ao humano — mas
> isso é o **modelo inline de conferência** (especialista IA renderiza agent-only ao humano co-presente). Num
> item de pull **standalone** o humano reivindica e **vira o primário** — não há IA para renderizar menu. Logo,
> para "humano reivindica item destacado e preenche form", **só o α (Console renderiza o DialogForm) é coerente**;
> o β não se aplica sem reintroduzir a conferência inline. O β fica registrado por completude, mas **não é uma
> alternativa viável** para o caso pull-standalone — ver §2.1.

### Path β — pull-contact + skill agente que renderiza o form ao vivo (NÃO viável no pull-standalone — ver acima)
O bridge cria um **contato pull sintético** (sessão própria, **sem** `conference_id`) no pool `wrapup_ia`
(`dispatch_mode: pull`) com `assigned_to = user_id` do humano + referência de segmento + briefing no contexto, e
**fecha o contato de origem na hora** (Camada D). O item aparece no `PullInboxPanel` **só do humano**
(filtro `assigned_to`, Camada B). Ao **reivindicar**, o humano **se anexa como primário** da sessão de wrap-up e
um **skill perfil `agent`** (evolução do `agente_wrapup_v1`) renderiza o form **ao vivo** — via **`form_get` +
menu dinâmico** (o mesmo padrão inline do `agente_nps_v1`, que já usa DialogForm sem depender de renderer de
Console). O skill grava o outcome do segmento por **referência** (tool nova) e completa.
- **Prós:** **self-contained** (não depende do renderer de aprovação); **reusa o `agente_wrapup_v1`** (o flow de
  menus quase inalterado) + o **DialogForm/`form_get`** (honra "DialogForm de disposição" do desenho) + Camadas
  B/C/D; fecha o **G1** já.
- **Contras:** o form é entregue como **interação de agente ao vivo** (o humano precisa estar "dentro" da sessão
  de wrap-up — natural, ele está logado no Console); é **canal-bound** (Console/webchat) no v1; introduz uma
  **sessão de wrap-up própria** (fato de journey — ver §4 Consequências).

### 2.1 — O renderer é o **tratamento genérico de collect-form no Console** (não "renderer de aprovação")

Reenquadramento (2026-07-24): aprovação, wrap-up e qualquer *form-fill de agente* são **o mesmo cenário** — um
workflow que `collect`/`delegate` um **DialogForm** para o **agente responsável preencher**. O mecanismo já
existe (`collect` carrega `dialog_form_id`; o J4c usa para o cliente). O que falta é a **superfície de
renderização do form para o agente que reivindica, no Console** — que é **exatamente** o α. Então o α **não é**
"renderer de aprovação": é a **superfície genérica** "renderiza o DialogForm de qualquer collect/delegate
reivindicado no inbox pull + submete via `workflow_resume`", servindo aprovação + wrap-up + survey-no-Console,
**sem skill customizado por caso** (o wrap-up deixa de ter skill próprio: vira `collect(form=dialog_wrapup_v1)` +
o renderer). É a **quarta superfície** do dialog primitive (chat-runner · página web · hook inline · **Console
inbox**). Aprovação empilha afordâncias (decisions/edições/anexos/ABAC) por cima do mesmo núcleo.

**Decisão (revisada 2026-07-24): Path α, renderer-first.** Como **não há urgência nem legado de wrap-up a
administrar**, o β **não é viável no pull-standalone** (§2.1/nota do β), e o α **é a generalização que você quer**
(collect-form genérico no Console), adotamos **α** e **construímos o renderer ANTES** (arco próprio — `adr-human-approval-workflow-step.md`, fase A3 + A1/A2 conforme escopo). Racional: (a) o
β seria entrega descartável; (b) o renderer é **independentemente valioso** (destrava o recurso de aprovação
inteiro — gate de promoção, reembolso, revisão-de-IA); (c) resulta em **um mecanismo canônico** ("claim de
workflow suspensa no pull → DialogForm no Console → `workflow_resume`") para wrap-up + aprovação + survey-collect.
**A maior parte da E2 é comum aos dois caminhos** e não se perde: o plumbing de `assigned_to` (E2c), o lifecycle
de `acw_pending` (E2e), a isenção da sessão de wrap-up nas métricas (E2f) e o **DialogForm de wrap-up** (E2a) valem
para α e β; **só a entrega muda** (menu-agente ao vivo × renderer-no-Console + resume). **Requisito de projeto do
renderer:** nascer **genérico** — renderizar um DialogForm de *disposição* (wrap-up) e não só o pacote
approve/reject da aprovação; senão o wrap-up-α pede ajuste no renderer. **Path β fica documentado como
alternativa** para o caso "urgente / sem renderer" (fecha G1 sem dependência).

**Sequenciamento:** (1) renderer de aprovação (sessão/arco dedicado, ADR aprovação) → (2) wrap-up Path α por cima
(peças compartilhadas E2a/E2c/E2e/E2f + gravação do outcome no `workflow_resume` + config `wrapup_ia`). O recorte
do renderer que o wrap-up-α depende (núcleo genérico **R0**) está em
[`docs/product/approval-renderer-kickoff.md`](../product/approval-renderer-kickoff.md).

---

## 3α. Decisões sob Path α (ADOTADO)

> A **superfície é "form + briefing"**, não só o form: o agente reivindica o item **depois** (tratou outros
> contatos no meio) e precisa **rever o que aconteceu** para preencher a disposição. Isso NÃO torna o tratamento
> wrap-up-específico — a aprovação também mostra "contexto read-only + form". Genérico = **form (DialogForm) +
> painel de briefing read-only**; o que varia é a **fonte** do briefing (dado/config no `collect`, não código).

- **D1α — Veículo = workflow webhook que `collect`a o form (reusa `_fire_detached_hook`).** O bridge dispara o
  `on_human_end side=agent detached` pelo **mesmo** `_fire_detached_hook` da Camada D (`POST …/webhook/pool/
  {wrapup_pool}`, `origin_session_id`+`journey:inherit`+`surveyed_segment_id`/`surveyed_agent_key` no context) →
  cria a **sessão de workflow** de wrap-up. Esse workflow faz **`delegate`** (NÃO `collect` — ver correção
  abaixo) carregando `context.dialog_form_id` do wrap-up + `context.briefing_session_id =
  @ctx.session.origin_session_id` + **`assigned_to = @ctx.session.surveyed_agent_key`** (campo de 1ª classe do
  step) → o convite de especialista **parqueia como item de pull direcionado** ao agente no pool `dispatch_mode:
  pull`. O agente reivindica no inbox → o **Console renderiza (form + briefing da origem)** → submete via
  `workflow_resume` → o workflow **grava o outcome** (D3) e completa. O contato de origem fecha na hora (Camada D).
  **Correção (2026-07-24): o veículo é `delegate`, não `collect`.** O `collect` é lazy e cliente-oriented (link →
  webchat, para alcançar o CLIENTE); o wrap-up é **agente-facing** (o humano reivindica do inbox) — exatamente o
  que o demo R0 validado (`skill_formfill_demo_v1`) faz com `delegate` a um pool pull. A cadeia real é
  `delegate` → `persistDelegate` → `handle_delegate_conference` (o especialista entra na sessão do workflow) →
  inbound com `conference_id` parqueia no pull.
- **D2α — `assigned_to` é campo de 1ª classe do step `delegate`** (✅ implementado 2026-07-24). A Camada B fez o
  lado-routing (`QueuedContact.assigned_to` + gate do claim); o plumbing produtor foi ligado: `DelegateStep.
  assigned_to`/`fallback_to_pool_after_s` (schema) → executor resolve o ref (`@ctx.session.surveyed_agent_key`) →
  `persistDelegate` → forwarder → `handle_delegate_conference` injeta no evento inbound → declarado em
  `ConversationInboundEvent` (senão `model_dump` descarta) → `contact_data` → `work_task_claim` honra. Assim o item
  cai no inbox **daquele** agente (filtro da Camada B); transbordo pro pool por lease se ele não pegar. Smoke
  `infra/test/smoke_directed_delegate.sh`.
- **D5α — Briefing = slot genérico (parte do núcleo, não enriquecimento).** A superfície de collect-form no
  Console tem um **painel de briefing read-only** ao lado do form, alimentado por: (a) **`briefing_session_id`**
  → o Console renderiza a **transcrição** daquela sessão (wrap-up = `origin_session_id`; reuso do
  `/api/conversation_history/{id}` que o preview do pull já usa) e/ou (b) **contexto inline / refs de anexo**
  (aprovação = pacote). Sem esse painel o wrap-up é impreenchível (o agente não lembra) — por isso é **núcleo**,
  não opcional. Mantém-se genérico: wrap-up preenche `briefing_session_id`, aprovação preenche o pacote.
- **D3 ✅ (2026-07-24, E2E validado) — gravação por referência via tool `segment_outcome_record`** (mcp-server): no
  `on_resume` do delegate, o workflow chama a tool que replica `_apply_wrapup_to_segment`/`_republish_segment_from_signal`
  (acumula no `seg_signal` + re-publica a linha COMPLETA `participant_left` → `analytics.segments`; no-op se os
  estáticos não foram semeados, p/ não corromper o RMT). **D4/D6/D7 valem sob α** (`acw_pending` e config do pool).
  Ver §3 (β) para o detalhe, comum aos dois caminhos. **E2E fechado no Console** (submit → `seg_signal.outcome=resolved`
  + linha em `analytics.segments` com `pool_id` preservado). **Gotcha registrado (CHANGELOG):** a tool teve de ser
  registrada no bloco do **`startServer`** do `mcp-server` (não só no `createServer`) — é o `startServer` que o
  `index.ts` sobe; toda tool nova vai nos DOIS blocos.
- **Dauth — o wrap-up submete como form-fill GENÉRICO (não aprovação).** O ingress de resume gateia por **tipo de
  tarefa** (`resume_required_abac`, server-side do ctx — ✅ 2026-07-24): aprovação (`session.decisions`/`session.
  resume_abac`) exige a capacidade; **form-fill genérico depende só do binding do claim** (`instance==human-{sub}`
  + `caller==claimant`). Logo o `collect` do wrap-up **NÃO deve setar `decisions`** (senão seria gated como
  aprovação); se quiser um ABAC próprio de wrap-up, declara `session.resume_abac`, senão o claim já autoriza o
  operador comum. Foi o pré-requisito de backend que destravou o R0 p/ o wrap-up.

**Núcleo genérico que o renderer precisa entregar (R0):** claim → **form (DialogForm por `form_id`) + painel de
briefing (transcrição por `briefing_session_id` + contexto inline)** → submit via `workflow_resume` → devolver à
fila. Ver [`docs/product/approval-renderer-kickoff.md`](../product/approval-renderer-kickoff.md).

---

## 3. Decisões (Path β — alternativa documentada, NÃO adotada)

### D1 — Veículo = **contato pull sintético `assigned_to`** (não webhook, não conferência)
Para `on_human_end` com `side: agent` + `dispatch: detached`, o bridge **não** usa `_fire_detached_hook`
(webhook); usa um **novo caminho** que publica um `conversations.inbound` sintético **sem `conference_id`**,
`pool_id = wrapup_ia`, carregando no contato: `assigned_to` = user_id do humano (de `_surveyed_agent_key` /
`_hook_human_instance_id`), `fallback_to_pool_after_s` (transbordo team-claimable, Camada B), `origin_session_id`
+ `journey: inherit` (pertença à journey), `surveyed_segment_id`/`surveyed_agent_key` (atribuição), e o
**briefing** (D5). O contato cai no ramo **pull** do `route()` (Camada B) e **parqueia** com `assigned_to`. O
contato de origem fecha na hora (Camada D, all-detached → `_trigger_contact_close`). Os demais detached
(`on_process_end` survey) seguem no `_fire_detached_hook` webhook — **wrap-up é o único que usa o caminho pull**.

### D2 — Plumbing do `assigned_to` até o `contact_data`
A Camada B lê `assigned_to` do **dict `contact_data`** (`{t}:queue_contact:{sid}`). O caminho de enqueue
(`route()` → `main.py` → `add_queued_contact`) monta `contact_data = event.model_dump()`. Como `model_dump()`
**descarta campos não declarados**, o `assigned_to`/`fallback_to_pool_after_s`/`assigned_at_ms` precisam ser
**declarados no `ConversationInboundEvent`** (schema) **ou** injetados no `contact_data` no ponto de enqueue a
partir de um campo do evento sintético. Decisão: **declarar os 3 campos opcionais no evento inbound** (fonte
única, fluem por `model_dump`), default ausente = fila compartilhada (retrocompat total). `assigned_at_ms` é
auto-carimbado no 1º `add_queued_contact` (já implementado na Camada B).

### D3 — Gravação do outcome = **tool `segment_outcome_record`** (nova), por referência
O wrap-up destacado roda em **sessão própria** → o bridge **não** dispara `_apply_wrapup_to_segment` para ela
(aquele caminho é da conclusão do hook de conferência). Em vez disso, o **skill de wrap-up** chama uma tool MCP
nova **`segment_outcome_record(origin_session_id, segment_id, classificacao, resumo, escalation_reason?,
proximos_passos?)`** (grupo `operational`, auditada) que **replica** `_apply_wrapup_to_segment`: normaliza a
classificação crua via o mesmo mapa, escreve `session:{origin}:seg_signal:{seg}` + `last_outcome`, e
re-publica o segmento (`participant_left` com outcome/issue_status/handoff_reason/escalation_reason) p/ o
analytics. `origin_session_id`+`segment_id` vêm do contexto (`@ctx.session.surveyed_segment_id` + raiz da
journey). **Invariante:** a atribuição é por **referência carregada**, nunca por o wrap-up ser fisicamente um
segmento da conferência.

### ~~D4 — Produtor/consumidor do marker `acw_pending`~~ — CADUCA (2026-07-29)

> A Camada C que este D4 fechava foi **revertida na Phase 0** (o gate operava sobre a instância, não sobre a
> vaga, e reservava no dispatch, não no claim) e **removida ponta a ponta em 2026-07-29** (migration
> `20260729000000_drop_pool_acw_gate`). Não há mais `acw_gate` para ler, nem marker para produzir. O wrap-up
> ocupa **uma vaga** pelo `claim_instance`, nos dois modos, com hand-off por hold (Phase 2). Texto abaixo
> mantido como registro.

- **Setar:** no D1, quando o pool do humano (`retencao_humano`) tem `acw_gate == hard`, o bridge **seta**
  `{t}:instance:human-{userId}:acw_pending` (TTL = SLA do wrap-up + margem) **ao criar o item pull**. É o que a
  Camada C lê em `get_ready_instances` p/ **não rotear** novo contato ao humano.
- **Limpar:** na **resolução** do item — o `segment_outcome_record` (submissão) **ou** o transbordo/timeout —
  **deleta** o marker. Cobre também o caso "sem claim no lease → transborda" (o marker expira por TTL; a
  resolução por qualquer atendente do pool limpa). Sem órfão (TTL é a rede).
- `soft`/`none`: bridge **não** seta o marker (não bloqueia). Assim o `hard` da Camada C passa a **bloquear de
  fato** só quando E2 liga o produtor.

### D5 — Briefing no item
O contexto do item carrega um **briefing** para o humano decidir rápido no claim: `origin_session_id` (drill),
resumo/verbatim da origem e sinais de copilot (reuso do que o `on_human_start` copilot já monta). No v1,
**mínimo viável** = `origin_session_id` + `surveyed_segment_id` + o `close_origin`; verbatim/transcrição e
copilot entram como enriquecimento (o Console já sabe puxar transcrição por `session_id`).

### D6 — Skill de wrap-up = **DialogForm via `form_get` + menu dinâmico** (perfil `agent`)
O `agente_wrapup_v1` evolui para: `carregar_form` (`invoke form_get`, form `dialog_wrapup_v1` — novo DialogForm
com os campos resumo/classificação/escalação/próximos passos) → menus dinâmicos (interaction/options do form,
padrão `agente_nps_v1`) → `gravar` (`invoke segment_outcome_record`) → `complete`. Fecha a dívida "form = dado
do tenant, UI-editável" (editor `/config/dialog-forms`). Mantém `visibility` agent-only (o form é do atendente).

### D7 — Config do pool `wrapup_ia`
`dispatch_mode: pull` + `fallback_to_pool_after_s` (transbordo). O hook `on_human_end → wrapup_ia` ganha
`dispatch: detached`. *(O `acw_gate` que constava aqui foi revertido/removido — ver D4.)*

---

## 4. Invariantes preservadas · Consequências

**Invariantes.** O **pool é a unidade endereçável** (`assigned_to` é elegibilidade de claim sobre trabalho
*pooled* + fallback, nunca alvo de roteamento — Camada B). **Identidade de participante single-source** (a
atribuição viaja por referência de segmento, não duplicada em campo session-global). **Degradação nunca
silenciosa** (marker por TTL; recusa de claim logada). **Routing Engine é o árbitro único** (claim via Camada B).

**Consequências positivas.** Fecha **G1** do caminho humano (AHT congela na saída do cliente; o que "prende" o
humano vira o backlog dele — a vaga que o wrap-up ocupa, não o gate por instância que foi revertido). Fecha as duas limitações registradas na Camada D
(`post_human`+detached e `segment_wrapup` fanout deixam de ser bloqueio: o wrap-up destacado não arma barrier).
Reduz a coleta a **2 mecanismos** de fato (inline síncrono / assíncrono). Reusa B/C/D + dialog primitive.

**Custos/Riscos.** (a) **Sessão de wrap-up própria** = novo fato de sessão no analytics (na journey da origem,
mas linha distinta) — precisa ser **filtrada da contagem de contatos/TMA** (como os segmentos de hook já são;
ver a 2ª causa da Mudança 24: `parse_routed` não escreve `sessions` p/ `conference_id`; aqui **não há**
`conference_id`, então é preciso um discriminador — ex.: marcar a sessão de wrap-up com `spawn_reason=wrapup`
e excluí-la das métricas de contato). **Era o principal ponto de atenção da E2 — fechado em E2f (✅
2026-07-29) via `pools.purpose`, e a visibilidade seletiva (custo residual) tem desenho em §7.** (b) v1
canal-bound (Console); omnichannel = convergência futura a Path α. (c) `segment_outcome_record` duplica lógica
de `_apply_wrapup_to_segment` — extrair um helper compartilhado evita drift.

---

## 5. Alternativas consideradas

- **Path α (webhook + renderer de aprovação) no v1** — adiado: depende do renderer de aprovação (A3, não
  construído). É a **convergência** futura, não o v1.
- **Wrap-up auto/IA (sem input humano)** — rejeitado: descaracteriza o wrap-up (a disposição é do humano que
  atendeu; sem ele não há conteúdo).
- **Manter inline e só não inflar AHT** — rejeitado: o inline segura a conferência por construção; sem destacar,
  o G1 não fecha.

---

## 6. Fases (plano fatiado da E2)

> **Nota (decisão Path α, renderer-first):** **pré-requisito = renderer de aprovação** (ADR aprovação, A3 +
> A1/A2), em arco/sessão dedicado. **A tabela abaixo foi escrita para o β; as diferenças sob α (ADOTADO):**
> - **E2a** — em vez de "skill agent que renderiza menu", o wrap-up é um **workflow** (perfil workflow) que faz
>   **`collect(form=dialog_wrapup_v1, assigned_to=@ctx.session.surveyed_agent_key,
>   briefing_session_id=@ctx.session.origin_session_id)`**; a renderização é do **renderer no Console** (R0), não
>   de um skill. Continua precisando do form `dialog_wrapup_v1`.
> - **E2b** — a gravação por referência acontece no **`workflow_resume`** do collect (o workflow chama a tool /
>   grava o outcome no retorno), não numa tool chamada por um skill agent ao vivo. A tool/helper de gravação
>   (replica `_apply_wrapup_to_segment`) permanece.
> - **E2c** — `assigned_to` (+ `fallback_to_pool_after_s`) vive no **step `collect`** (o executor propaga ao
>   `contact_data` do child), **não** no `ConversationInboundEvent` do webhook trigger.
> - **E2d** — não há "dispatch pull sintético no bridge": reusa o **`_fire_detached_hook`** da Camada D (webhook)
>   → o workflow de wrap-up é quem cria o item pull (via o `collect` do E2a).
> - **Briefing (D5α)** é **parte do R0** (form + painel de transcrição por `briefing_session_id`), não fase à parte.
> - **E2e/E2f/E2g** valem iguais. **Pré-requisito de todo o α: o renderer R0** (kickoff dedicado).
>
> A tabela β segue como referência da alternativa não adotada.

| Fase | Entrega | Dep. | Gate |
|---|---|---|---|
| **E2a — DialogForm + skill** | form `dialog_wrapup_v1` (dialog-api) + `agente_wrapup_v1` reescrito (perfil agent: `form_get` + menu dinâmico + `segment_outcome_record` + complete). | dialog primitive | form criável no editor; skill valida |
| **E2b — tool `segment_outcome_record`** | tool MCP (mcp-server) que replica `_apply_wrapup_to_segment` por referência (origin+segment do contexto) — extrair helper compartilhado. | — | grava seg_signal + republish; segmento aparece no analytics |
| **E2c — plumbing `assigned_to`** | declarar `assigned_to`/`fallback_to_pool_after_s`/`assigned_at_ms` no `ConversationInboundEvent`; fluem por `model_dump` → `contact_data`. | Camada B | item pull nasce com `assigned_to` |
| **E2d — dispatch pull no bridge** | novo caminho p/ `on_human_end side=agent detached`: `conversations.inbound` sintético (sem conf) → `wrapup_ia` pull + `assigned_to` + contexto/briefing; fecha origem na hora. | E2c, Camada D | wrap-up parqueia no inbox do humano; origem fecha (G1) |
| ~~**E2e — `acw_pending` lifecycle**~~ | **FORA DE ESCOPO (2026-07-29)** — a Camada C foi revertida (Phase 0) e removida ponta a ponta; não há `acw_gate` a ler nem marker a produzir. | — | — |
| **E2f — analytics: sessão de wrap-up fora da contagem** | discriminador (`spawn_reason=wrapup`) + exclusão das métricas de contato/TMA (espelha a isenção de hook). | E2d | TMA/open_count não contam a sessão de wrap-up |
| **E2g — config + validação** | `wrapup_ia` → `dispatch_mode: pull`; hook `detached`; smoke E2E (claim direcionado → form → grava outcome no segmento → fallback; G1). | todas | smoke verde |

**Sequência:** E2a+E2b (conteúdo+gravação) → E2c+E2d (item pull) → E2f (métricas) → E2g (config+validação).
**Não-objetivos v1:** omnichannel (Path α); briefing rico (verbatim/copilot além do mínimo); wrap-up de IA.

---

## 7. Emenda (2026-08-11, ACEITA — fatia 1 implementada) — Visibilidade seletiva da sessão de wrap-up em Analytics/Sessions

> **Estado:** fatia 1 (`scope` + contagem por domínio no `meta`) ✅ 2026-08-11 (CHANGELOG). Fatias 2–4 +
> a **1b** (marcar a linha como interna na resposta — descoberta na implementação: o veredicto é
> computado no backend e descartado, e sem ele a UI não tem o que pintar) seguem em `TODO.md`.
>
> **Correção de registro a §7.1:** a associação por `origin_session_id` foi **verificada no código**, não
> só na doc. Inline e detached passam pelo MESMO `_fire_detached_hook` — o predicado
> `_is_workflow_dispatch_entry` inclui `side == "agent" AND dispatch == "inline"` —, e o campo é gravado
> no top-level do body (`main.py:1300`) **e** como tag de contexto (`:1248`), com `journey: "inherit"`.

> **Gatilho:** operador rodou um E2E completo (webchat → IA → fila → humano → especialista → NPS do cliente +
> wrap-up do humano) e não encontrou o wrap-up em `Analytics > Sessions` — nem como segmento da sessão principal
> (não é; §3α D3, gravação por referência), nem como sessão própria (é, mas fica invisível). A causa é o próprio
> E2f (§4): a exclusão de `pools.purpose = 'internal'` em `/reports/sessions`/`/reports/journeys` é
> **incondicional**, sem parâmetro de override — resolveu a contaminação de TMA/contagem de contatos, mas também
> apagou a visibilidade retrospectiva da sessão de wrap-up, mesmo com `accessible_pools` liberado.

### 7.1 — Decisão

**Visibilidade ≠ contagem.** A exclusão de E2f deve continuar protegendo todo **agregado** (TMA, "N contacts",
métricas de pool/agente) — isso não muda. O que muda é que a **listagem bruta** de `/reports/sessions` ganha um
parâmetro opcional `scope: contacts | all` (default `contacts` = comportamento atual, bit-a-bit idêntico ao que
E2f fechou). Com `scope=all`, sessões de `purpose=internal` (wrap-up, dispatch, etc.) aparecem como linhas
extras, mas o cabeçalho de contagem **nunca** lê `scope=all` — continua somando só `purpose≠internal` mesmo
quando a listagem está expandida (ex.: "5 contacts · 2 internal", nunca "7 contacts").

**Associação por `origin_session_id`, não por Journey.** Cogitou-se resolver a visibilidade só no drill-down de
Journey — descartado: nem todo contato tem journey de N sessões, e forçar isso criaria uma journey artificial de
1+1 para todo wrap-up, poluindo `/reports/journeys` com processos triviais que não são processos. A ligação
correta é direta, 1:1, via `origin_session_id` — campo já gravado de forma confiável nos dois modos de dispatch
(`inline` e `detached`; ambos passam por `_fire_detached_hook`, que grava `origin_session_id` tanto no
top-level do body quanto em `context["session.origin_session_id"]` — gap histórico de só gravar no top-level,
fechado em 2026-07-27, "Wrap-up-α — wiring do hook `on_human_end` `detached`", CHANGELOG). Com `scope=all`, a
linha da sessão de wrap-up ganha uma coluna/badge "Origin" apontando para o `session_id` pai (clicável, navega
direto — sem depender de Journey).

**Drill-down de uma Journey já aberta é exceção à regra do default.** Diferente da listagem topo (que é
agregação e deve ficar limpa por padrão), o drill de UMA journey específica (`journey → sessions → segments`) já
está fora de qualquer contagem — o operador abriu aquele processo, não está somando nada. Esse endpoint fica
isento do filtro de E2f (sempre mostra sessões internas associadas), independentemente do `scope` da listagem
topo.

### 7.2 — Guardrails (para não reabrir o que E2f fechou)

1. `scope=all` só afeta a listagem (`/reports/sessions`); nenhum endpoint de agregado (TMA, ocupação, contagem
   de contatos, `/reports/agents/*`, `/reports/pools/*`) aceita ou lê esse parâmetro.
2. O cabeçalho de contagem da tela sempre computa a partir do scope `contacts`, mesmo quando a tabela abaixo
   está expandida por `scope=all` — nunca um único número que misture os dois domínios.
3. Na UI, o toggle "Incluir sessões internas (wrap-up, dispatch)" nasce **desligado por padrão** — o operador
   tem que optar ativamente por ver o ruído operacional.
4. Linhas de sessão interna carregam uma tag visual distinta (não "contact") e, quando aplicável, a coluna
   "Origin" — nunca ficam visualmente indistinguíveis de um contato real.

### 7.3 — Por que emenda e não ADR novo

A tensão "sessão de wrap-up própria custa visibilidade em analytics" já estava registrada como risco aceito
neste ADR (§4, item a) antes mesmo de E2f existir. §7 é a continuação natural dessa mesma decisão — não é um
domínio novo, é o ajuste fino do trade-off que este documento já era dono. Criar um ADR separado fragmentaria o
histórico de uma única decisão em dois lugares.

### 7.4 — Não-objetivos

- Não reabre a contagem de contatos/TMA para incluir sessões internas — E2f continua valendo para todo agregado.
- Não introduz Journey artificial para contatos sem processo multi-sessão.
- Não estende `scope=all` a `/reports/journeys` (listagem topo) — só ao drill-down de uma journey já aberta e a
  `/reports/sessions`.
