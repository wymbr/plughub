# Kickoff — Renderer genérico de DialogForm no inbox pull (base de aprovação + wrap-up)

> **STATUS: R0 ENTREGUE ✅ (2026-07-24).** Núcleo genérico `DialogFormRenderer.tsx` construído e validado (UI +
> smoke) — ver CHANGELOG "Renderer genérico de collect-form no Console — R0". O `ApprovalPanel` virou wrapper fino
> sobre o núcleo. Consumidores: aprovação (wrapper, validado) e demo genérico (`skill_formfill_demo_v1` +
> `dialog_formfill_demo`, `smoke_formfill_renderer.sh`). Wrap-up-α (E2) consome o núcleo sem alterá-lo.
> **Falta:** R1 (anexos/masking-por-role + ABAC `approvals` completos) e a E2 do wrap-up.

> **Para:** a sessão/arco dedicado do **renderer de aprovação** (ADR `docs/adr/adr-human-approval-workflow-step.md`,
> fases A1/A2/A3). **Por que este kickoff:** o **wrap-up-α** (Camada E2 do arco de detach de hooks,
> ADR `docs/adr/adr-wrapup-detached-pull.md`) decidiu (2026-07-24) esperar este renderer em vez de construir o
> Path β descartável. Este doc recorta o **núcleo genérico** que o wrap-up-α precisa e o separa do que é
> **aprovação-específico**, para o renderer nascer reusável e não pedir retrabalho depois.
> **Relacionados:** Camada B (pull `assigned_to`/`PullInboxPanel`), Dialog Primitive (`form_get`/DialogForm),
> `packages/channel-gateway/src/plughub_channel_gateway/survey_web.py` (renderiza QUALQUER DialogForm — molde de reuso).

---

## 1. Objetivo

> **Enquadramento (2026-07-24):** isto **não é** um "renderer de aprovação" — é o **tratamento genérico de
> collect-form no Console**. Aprovação, wrap-up e qualquer *form-fill de agente* são o **mesmo** cenário: um
> workflow que `collect`/`delegate` um **DialogForm** para o **agente responsável preencher**. O mecanismo de
> passar o form já existe (`collect`/`delegate` carregam `dialog_form_id`; o J4c usa para o cliente). O que falta
> é a **superfície de renderização para o agente que reivindica, no Console** — que é este renderer. É a **quarta
> superfície** do dialog primitive (chat-runner · página web · hook inline · **Console inbox**). *(Por que não um
> skill que renderiza menu ao agente: num item de pull standalone o humano reivindica e vira o primário — não há
> IA para renderizar; a apresentação do form tem que ser do Console. Ver ADR wrap-up §2.1.)*

Uma superfície canônica: **reivindicar uma workflow suspensa no pull → renderizar seu DialogForm no Console →
submeter via `workflow_resume`**. Um mecanismo serve **N** consumidores: **aprovação** (decisões), **wrap-up**
(disposição do atendente), **survey no inbox** (futuro) — **sem skill customizado por caso**. O wrap-up só precisa
do **núcleo (R0)**; aprovação empilha extras por cima. Construir o núcleo **genérico** desde o dia 1 é o ponto.

## 2. Núcleo genérico (SHARED) — o que o wrap-up-α exige

**Entrada:** um item de pull reivindicado que é uma **workflow suspensa** carregando no contexto um **`form_id`**
(DialogForm publicado) + o **`resume_token`**. (Hoje o item pull já carrega contexto; o `form_id` as-built vem
do `@ctx.session.dialog_form_id`, mesmo binding do dialog-runner.)

A superfície tem **DUAS partes**: um **painel de briefing read-only** (o contexto que o agente lê para
preencher) + o **form** (DialogForm). O briefing **é núcleo**, não opcional: sem ele o wrap-up é impreenchível (o
agente reivindica depois e precisa **lembrar o que aconteceu**); a aprovação idem (mostra o pacote/contexto).

**Renderer (Console, no `PullInboxPanel`/preview):**
1. Reivindica (Camada B, claim atômico) — reuso total, **nada novo aqui**.
2. **Briefing (read-only):** alimentado por (a) **`briefing_session_id`** (do contexto do item) → renderiza a
   **transcrição** daquela sessão — **reusa o `/api/conversation_history/{id}` que o preview do pull já usa**
   (wrap-up → `origin_session_id`); e/ou (b) **contexto inline / refs de anexo** (aprovação → pacote montado pelo
   workflow). O que varia é só a **fonte** (dado/config no `collect`); o painel é genérico.
3. Busca o **DialogForm publicado** por `form_id` (proxy p/ `form_get`/dialog-api — o mesmo form congelado que o
   veículo web já consome).
4. **Renderiza o form inteiro:** nós `statement` (read-only) + nós `question` (inputs/botões conforme
   `interaction`: text→input, button/list→botões). **Reusar a lógica de `survey_web.py`** (`render(form)`,
   linhas ~344-485) portada para React — ela já renderiza QUALQUER DialogForm e valida formato.
5. Coleta as respostas e **submete via `workflow_resume`**: `POST /v1/channels/webhook/resume/{token}` com o
   `payload` = respostas capturadas. O ingress de resume **já é auth-aware** (A5: JWT do aprovador + `caller ==
   claimant`) — reuso.
6. O **workflow lê o `payload`** e ramifica/grava (no wrap-up: grava o outcome do segmento por referência; na
   aprovação: `choice` sobre a decisão). **Fronteira invariante:** roteamento vive no workflow, nunca no JSON do
   form.

**Requisito de generalidade (não-negociável):** o renderer renderiza um DialogForm **arbitrário por `form_id`**,
sem assumir o pacote approve/reject. Um **form de disposição** (wrap-up: um `question` de classificação com
botões + `question`s de texto para resumo/próximos passos) tem que renderizar **sem nenhum código
aprovação-específico**. Se o renderer hardcodar `decisions[]`/anexos/edições, o wrap-up-α quebra — daí este doc.

## 3. Aprovação-específico (empilha por cima — NÃO bloqueia wrap-up)

Estes são enriquecimentos do caso aprovação; o wrap-up-α **não** depende deles:
- **`decisions[]` como botões terminais** mapeados ao `choice` do workflow. *(Nuance: a classificação do wrap-up
  é um `question` de botões que também termina + retorna valor — então "botão que finaliza e devolve um escalar"
  é **shared**; o que é aprovação-específico é a semântica `decisions[]`+`choice`, não o widget.)*
- **Campos pré-preenchidos editáveis + auditoria da edição** (antes/depois) — A5.
- **Painel de anexos** (refs do AttachmentStore, masking por role) — A5.
- **Gate ABAC `approvals`** (`operacao`/`decide`) — A4. *(Wrap-up usa o gate do próprio inbox/claim + um ABAC
  de wrap-up ou `agent_assist.operacao`; a definir na E2, não aqui.)*

## 4. Reuso concreto (não reinventar)

| Peça | Fonte a reusar |
|---|---|
| Claim + item direcionado + preview | `PullInboxPanel` + `/api/work_queue/*` (Camada B — pronto) |
| Buscar o DialogForm publicado | tool `form_get` / dialog-api (proxy `/v1/dialog`) |
| Renderizar o form inteiro + validar formato | `survey_web.py` `render(form)` (portar p/ React) |
| Submeter | ingress `POST …/webhook/resume/{token}` (auth-aware, A5) |
| Voltar à fila | `work_task_release` (Camada B) |

## 5. Fatiamento sugerido (para a sessão do renderer)

- **R0 — núcleo genérico (desbloqueia wrap-up-α):** no claim de um item pull cuja workflow suspensa carrega
  `form_id` + `resume_token` (+ opcional `briefing_session_id` / contexto inline), renderizar **briefing
  read-only** (transcrição por `briefing_session_id` — reuso do `/api/conversation_history/{id}` — e/ou contexto
  inline) **+ o DialogForm publicado** (statements + questions incl. botões terminais), coletar respostas,
  submeter via `workflow_resume`; devolver à fila. **Gate:** um DialogForm de teste (statements + 1 question de
  botões + 1 de texto) renderiza no Console **ao lado da transcrição de uma sessão referenciada**, e o resume
  chega ao workflow com o payload. Layout responsivo/mobile (aprovação é tap-first; wrap-up idem).
- **R1 — extras de aprovação (por cima):** `decisions[]`→`choice`, pré-preenchido editável + auditoria de edição,
  anexos com masking, ABAC `approvals`. (Fases A1/A2/A4/A5 da ADR de aprovação.)

**A superfície estável que o wrap-up-α vai consumir = R0** (render DialogForm por `form_id` + submit via resume).
Manter esse contrato genérico é o único requisito de acoplamento entre os dois arcos.

## 6. O que o wrap-up-α adiciona depois (fora deste kickoff — E2 no ADR de wrap-up)

Peças que **não** são do renderer e ficam na E2: DialogForm `dialog_wrapup_v1` (conteúdo); dispatch do bridge
que cria o item pull `assigned_to` o humano (E2c/E2d); gravação do outcome no `workflow_resume` por referência
de segmento; lifecycle do marker `acw_pending` (E2e); isenção da sessão de wrap-up nas métricas de contato (E2f).
O renderer só precisa entregar o **R0**.
