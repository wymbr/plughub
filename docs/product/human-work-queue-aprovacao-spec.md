# Fila de Trabalho Humano / Aprovação no Console · Especificação

> **Contexto:** *Business in Any Media*. Novo módulo do **Console**: uma **fila de trabalho (task inbox)** onde um humano revisa, **edita** e **roteia** um processo montado por um agente/workflow num passo anterior. Aprovação é o primeiro instanciamento; o mecanismo é genérico (revisão de reembolso, liberação de crédito, conferência de pedido, revisão de resposta da IA antes do envio).
> **Modelo:** **pull**. O Routing Engine entrega as tasks numa **fila do pool**; a interface de trabalho lista as tasks e **solicita a retirada (claim) ao Routing Engine** (que continua o único árbitro); o humano trata, **devolve** à fila, ou **encaminha a um dos vários próximos passos** definidos pelo workflow.
> **Status:** especificação. Fiel a `packages/schemas/src/skill.ts` e ao modelo de sessão/routing. **Data:** Junho 2026.

---

## 1. Princípios

1. **O work item não é entidade nova** — é a **sessão do workflow suspensa** num pool de aprovação. A "fila" é um pool; o "pacote" é o contexto (`@ctx.*`) + anexos (AttachmentStore) montados num passo anterior; a "decisão" é um `workflow_resume` com payload.
2. **Pull, com o Routing Engine como árbitro.** O routing entrega a task à fila do pool e **concede o claim** atomicamente (um único vencedor) — não fura a invariante "Routing Engine é o único árbitro de alocação". `claim` = alocação; `release` = desalocação.
3. **O workflow principal orquestra tudo; o agente de aprovação só devolve um outcome.** O agente humano foi invocado por `delegate` — ele apenas captura a escolha do humano (+ edições) e **retorna pelo retorno do delegate** (`workflow_resume` payload). O **workflow principal** é quem declara os próximos passos possíveis e **roteia** (`choice` sobre o outcome capturado). Decisão multi-via é lógica do fluxo principal, não do engine nem do agente.
4. **Composição sobre primitivas existentes** — `delegate` por pool, ContextStore, AttachmentStore, masking, auditoria. O que se constrói é a **superfície de inbox**, o **modo pull no routing** e o **contrato do pacote**.

---

## 2. O work item — sessão suspensa num pool de aprovação

O workflow (a) monta o pacote num passo anterior (escreve contexto via `context_set`, anexa via AttachmentStore) e então **delega ao pool de aprovação** (pull):

```yaml
# ── passo anterior: monta o pacote (exemplo) ──
- id: montar_pacote
  type: invoke
  tool: context_set
  input: { session_id: "$.session_id", tenant_id: "$.tenant_id",
           tag: "approval.resumo", value: "Reembolso R$ 1.240 — cliente X", confidence: 1.0 }
  on_success: aprovar

# ── delega ao pool de aprovação (pull) ──
- id: aprovar
  type: delegate
  pool: aprovacao_reembolso          # pool em dispatch_mode: pull
  context:                            # o "pacote" channel-abstract entregue ao Console
    title:    "@ctx.approval.resumo"
    summary:  "@ctx.approval.detalhe"
    fields:   "@ctx.approval.fields_json"     # campos read-only + editáveis (JSON)
    attachments: "@ctx.approval.attachments_json"
    decisions:   "@ctx.approval.decisions_json"  # próximos passos possíveis (JSON)
  timeout_hours: 24
  business_hours: true
  on_resume:  { next: rotear_decisao }   # humano resolveu → workflow ramifica
  on_timeout: { next: escalar_prazo }    # SLA estourou
```

- A skill publicada em `aprovacao_reembolso` é o **agente de aprovação** (nível c): roda no Console, renderiza o pacote, captura decisão+edições e chama `workflow_resume`.
- O `delegate` já gera `workflow_resume_token` e suspende a sessão — o work item "fica na fila".

---

## 3. Routing Engine — modo pull (mecanismo genérico)

A fila de aprovação roda sobre o **dispatch pull genérico** do Routing Engine — `dispatch_mode: pull` no pool: o routing entrega a task à fila e **concede o claim** atomicamente (`ZREM`, um único vencedor); a interface só pede `work_queue_list`/`work_task_claim`/`work_task_release`; claim = alocação (abre segmento); a **lease** do claim (TTL + heartbeat) re-enfileira no auto-release; o release re-enfileira **pelos critérios do routing** (não preserva posição). Dois relógios: espera não-claimada = SLA existente; claim ocioso = lease curta. **Especificação completa em `routing-pull-dispatch-spec.md`.**

O que é **específico de aprovação** (resto deste doc): o item é uma **sessão de workflow suspensa** (delegate ao pool pull), com **pacote** (form padrão + `decisions`) e **decisão pelo retorno do delegate**. A fila/claim em si é o **mecanismo genérico** — serve para e-mail, back-office, qualquer contato.

> Não confundir com `queue_config` (Queue-Attended-Model): aquilo é **cliente** esperando atendimento (push, agente de fila entretém); `dispatch_mode: pull` é **operador** puxando trabalho. Eixos distintos.

---

## 4. Contrato do pacote — form padrão + extensão

**Por default, um form padrão de aprovação** (sem contrato bespoke): o Console renderiza o **contexto read-only** (mascarado por role) + os **anexos** + uma **nota** + os **botões de decisão**. O caso comum não declara campos editáveis.

**Extensão só quando faltar:** se o passo precisa que o humano altere algo, o workflow declara um **delta de form** (campos editáveis adicionais) no `delegate.context`. Standard cobre o comum; estende-se pontualmente.

```jsonc
// decisions — próximos passos POSSÍVEIS, definidos pelo WORKFLOW PRINCIPAL (bounded)
[ {"id":"aprovar","label":"✅ Aprovar e pagar"},
  {"id":"revisar","label":"↩ Voltar para revisão"},
  {"id":"recusar","label":"❌ Recusar"} ]

// attachments — refs do AttachmentStore (NÃO o binário)
[ {"id":"att_1","name":"nota.pdf","mime":"application/pdf","masked":false} ]

// form_ext (OPCIONAL) — só os campos editáveis extras além do padrão
{ "editable": [ {"id":"valor_aprovado","label":"Valor aprovado","type":"money","value":"1240.00"} ] }
```

- **`decisions`** é a lista de saídas — **definida pelo workflow principal** (o humano não salta para qualquer passo). 
- **Devolver à fila** é ação universal do Console (não precisa estar em `decisions`) → `work_task_release`.
- O conjunto de `type` de campo do form padrão (text/money/select/date/bool) é fixo; extensões usam os mesmos tipos.

---

## 5. Decisão volta pelo RETORNO do delegate; o workflow principal roteia

O agente de aprovação **só devolve um outcome** (a escolha do humano + edições) pelo retorno do delegate; o **workflow principal captura e ramifica**:

**Sem mudança de schema:** o executor do `delegate` (`steps/delegate.ts`) já devolve `output_as: step.id` + `output_value: payload` no resume. Logo, o `payload` do `workflow_resume` **já cai em `$.pipeline_state.<id_do_delegate>`** — o "campo complementar que volta com o outcome". O fluxo principal só testa isso num `choice`.

```yaml
# (agente de aprovação, nível c) — captura e RETORNA, não decide rota
- id: concluir
  type: invoke
  tool: workflow_resume
  input:
    resume_token: "@ctx.session.workflow_resume_token"
    decision: "input"              # → on_resume (input/approved); rejected → on_reject; timeout → on_timeout
    payload:                       # OUTCOME — vira $.pipeline_state.<id do delegate>
      choice: "$.pipeline_state.escolha"        # qual decisão o humano escolheu
      edits:  "$.pipeline_state.edicoes_json"   # campos editados (não sensíveis)

# (workflow principal (a)) — o delegate já captura o payload em $.pipeline_state.aprovar
- id: aprovar
  type: delegate
  pool: aprovacao_reembolso
  context: { title: "@ctx.approval.resumo", decisions: "@ctx.approval.decisions_json" }
  on_resume:  { next: rotear_decisao }
  on_timeout: { next: escalar_prazo }

- id: rotear_decisao
  type: choice                     # (ou um reason, se a escolha precisar de interpretação)
  conditions:
    - { field: "$.pipeline_state.aprovar.choice", operator: eq, value: aprovar, next: efetuar_pagamento }
    - { field: "$.pipeline_state.aprovar.choice", operator: eq, value: revisar, next: passo_revisao_anterior }
    - { field: "$.pipeline_state.aprovar.choice", operator: eq, value: recusar, next: encerrar_recusado }
  default: efetuar_pagamento
```

- **O workflow principal é dono do roteamento:** o `choice` mapeia `payload.choice` → `next` (incluindo um **passo anterior** para revisão/rework). O agente não conhece os passos — só devolve a escolha.
- **Encaminhar a um dos vários próximos passos** = esse `choice`, com os alvos que o **workflow principal** declarou em `decisions`.
- **Devolver à fila** = `work_task_release` (não resume; a task volta claimável).
- Edições não sensíveis viajam no `payload.edits` (lidas em `$.pipeline_state.aprovar.edits.*`); campos sensíveis seguem masked input.

---

## 6. Console — superfície de inbox

Nova superfície "Aprovações / Tarefas" (distinta do atendimento ao vivo, porque é async/pull):

- **Lista da fila** (`work_queue_list`): por pool acessível, com título, resumo, idade/SLA, estado (`claimável` | `claimada por mim` | `claimada por outro`). Filtros (pool, prazo).
- **Abrir/pré-visualizar:** vê resumo + metadados sem claimar.
- **Retirar (claim):** `work_task_claim` → trava para o usuário; abre a visão completa.
- **Visão completa:** contexto (read-only, mascarado por role) + **campos editáveis** (form) + **anexos** (visualização respeitando masking) + **botões de decisão** (do `decisions[]`) + **Devolver à fila** + nota.
- **Decidir:** escolhe um próximo passo → grava edições + decisão → `workflow_resume`. A task sai da fila.
- **Estados/locks:** claimada por outro = read-only; auto-release por timeout/heartbeat; badge de SLA.

ABAC: superfície gated por `approvals.operacao` (ver §9); decisões podem exigir `approvals.decide`.

---

## 7. Edição e auditoria

- **Campos editáveis** → as edições voltam no **outcome** do delegate (`payload.edits`); cada alteração é **auditada** (quem, quando, valor antes/depois) — trilha append-only no espírito do `ContestationThread`/`audit_access_log`. Campos sensíveis usam masked input (não vazam para stream/log).
- **Visualização de anexo/contexto** → registrada (como os endpoints de Auditoria LGPD já fazem para sessões/MCP).
- **Decisão** → registrada com `task_id`, `session_id`, decisor, próximo passo escolhido, timestamp.
- Edição de binário de anexo: **fora de escopo** (só anotar/adicionar novo anexo, não alterar o original).

---

## 8. Masking e visibilidade

- O pacote respeita **masking por role**: campos sensíveis chegam como `display_partial`; `original_content` só a `authorized_roles`. Anexos com PII seguem a mesma regra.
- Edição de um campo mascarado segue o padrão de masked input quando aplicável (valor sensível não vaza para stream/log).

---

## 9. Permissões (ABAC)

- Novo módulo/campo ABAC `approvals` (ortogonal): `approvals.operacao` (ver/claimar a fila), `approvals.decide` (encaminhar/recusar), com `scope[]` por pool. Reusa o `PermissionChecker.can(...)` e o escopo por `accessible_pools`.
- Escopo de supervisor (grupo+turno) já vale para os pools de aprovação.

---

## 10. Fases

| Fase | Entrega |
|---|---|
| **A — pull routing** | `dispatch_mode: pull` no PoolConfig; `work_queue_list`/`work_task_claim`/`work_task_release` no routing (claim atômico + segmento + auto-release por heartbeat). |
| **B — pacote + retorno do delegate** | form padrão + extensão (`form_ext`); `decisions`/`attachments` via `delegate.context`; agente de aprovação (skill nível c) genérico que só captura e devolve outcome via `workflow_resume` payload; roteamento no workflow principal (`choice` sobre `$.pipeline_state.<delegate>`, já capturado pelo executor); edição auditada via `payload.edits`. **Sem mudança de schema.** |
| **C — Console inbox** | Superfície "Aprovações/Tarefas": lista, claim/release, visão completa (contexto+anexos+editáveis), botões de decisão + devolver; estados/lock/SLA; ABAC `approvals`. |
| **D — extras** | Quatro-olhos (2 aprovadores); reatribuição por supervisor; notificações de SLA; métricas (tempo na fila, tempo de tratamento, taxa de rework por passo). |

---

## 11. Decisões (fechadas) e pendência

1. ~~`delegate` vs `suspend(approval)`~~ **Resolvido:** o agente humano é invocado por **`delegate`** e **devolve um outcome** pelo retorno (§5); o workflow principal roteia. **Nenhuma primitiva nova** — o executor do `delegate` já expõe o `payload` em `$.pipeline_state.<id_do_delegate>` (`output_as: step.id`).
2. ~~`decisions` no contexto vs. schema~~ **Resolvido:** no `delegate.context`, **definidas pelo workflow principal** (§4/§5).
3. ~~Reatribuição/roubo de claim~~ **Resolvido:** é **conferência padrão** — supervisor (e até especialistas) entram/atuam como em qualquer sessão; sem mecanismo especial de "roubo".
4. ~~Tipos de campo editável~~ **Resolvido:** **form padrão por default + extensão** quando faltar (§4); conjunto fixo de `type` (text/money/select/date/bool).
5. ~~Decisões definidas onde~~ **Resolvido:** pelo **workflow principal** que delega aos demais.

**Pendência (feature futura):** métrica de **rework** — quantas vezes uma task volta a um passo anterior (qualidade do processo montado pela IA) — integra com a Bancada/Arc 6.
