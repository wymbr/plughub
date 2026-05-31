# Delegate — A2A entre Agentes (Modelo Corrigido v2)

> Atualizado: 2026-05-30 · **v2 supersede a v1.** A v1 (em git) modelava o delegate
> como suspend de webhook + criação de sessão independente. Isso provou-se errado:
> gerava sessão fantasma e contaminava sessões webchat como "webhook". A v2 abaixo
> é o modelo vigente.

## Princípio

`delegate()` é **A2A genérico**: um chamador (workflow OU agente) invoca outro **agente**.
O agente-alvo roda como **conference specialist dentro do `session_id` do chamador**
— produz um **segmento** (`segment_id` sob o `session_id` do chamador), **nunca** uma
sessão própria nem um workflow filho.

Isso se apoia na premissa da unificação Arc 19: **toda sessão é uma conferência**. O
specialist entra na conferência do chamador, seja ele webchat (agente) ou webhook
(workflow). *(A validar: rodar specialist dentro de sessão webhook sem cliente vivo.)*

## `task()` vs `delegate()`

Ambos criam **segmento** no `session_id` do chamador (mesmo mecanismo de conferência).
A diferença aparece no contexto de **workflow**:

| Função | Alvo | Cria workflow filho? | Mecanismo de espera |
|---|---|---|---|
| `task()` | agente **ou** workflow | sim, quando o alvo é workflow | polling (`agent_delegate` / `agent_delegate_status`) |
| `delegate()` | **agente** (A2A) | **nunca** | suspend + `workflow_resume` (resume_token) |

`delegate` usa suspend/resume (não polling) porque o agente delegado faz **I/O com o
cliente** e pode demorar (minutos). Mas o retorno é, no caso comum, rápido — é um step
normal que cede controle ao agente e retoma quando ele conclui.

## Mecanismo do `delegate`

```
CHAMADOR (B ou A-new)                         SPECIALIST (segmento do chamador)

delegate(pool)
  → gera resume_token
  → grava em {tenant}:resume_tokens
  → despacha specialist NO MESMO session_id    agente alocado como conference
     (handle_delegate_conference):             specialist (conference_id set)
       conference_id, channel = do chamador     parent_segment_id = segmento primário
       context + delegate_resume_token          do chamador
  → suspende o FLUXO do chamador (espera A2A)   faz I/O (notify/menu) — ou deferred
  → NÃO troca canal, NÃO marca webhook-         se canal sem outbound
     suspended (salvo chamador webhook)         conclui → workflow_resume(resume_token)
                                                 → retoma o fluxo do chamador
```

**Regra única (decisão 2026-05-30):** o **fluxo** do chamador entra em `suspended`
durante a espera A2A — seja o chamador agente ou workflow. Ambos registram "em espera";
muda só a duração (agente retorna rápido, workflow pode demorar). O que a v1 errava e
**não** deve acontecer: trocar o `channel` do chamador para `webhook`. O canal é
**preservado** — A-new continua `webchat`, B continua `webhook`.

O **badge de status da sessão na lista** é derivado dos **participantes vivos**, não do
estado interno do fluxo: enquanto o specialist está ativo (A-new com o cliente
conversando), a sessão lê `active`; sem participante vivo (B depois que C adiou), lê
`suspended`. Assim a regra de flow é única, mas a UI não mostra "suspended" para uma
sessão em que o cliente está ativamente conversando.

## Fluxo B → C (workflow → agente; canal inbound-only)

1. B (webhook) resume da aprovação da operadora → `delegate(portabilidade_confirmacao)`.
2. C (agente_confirmacao) roda como **segmento de B** (specialist). É agente pleno —
   poderia fazer outbound (WhatsApp/SMS/e-mail) se o canal permitisse.
3. Canal é `inbound_only` (webchat não tem outbound) → C **não alcança o cliente agora,
   então adia**: encerra o segmento **sem chamar `workflow_resume`**. Adiar ≠ desistir —
   ainda pode haver retentativa/aguardo do reconnect.
4. B permanece **suspended**, marcado `awaiting_customer_inbound` — **mesma semântica do
   suspend "aguardando autorização da operadora"**. `pending_workflow` key válida.
5. O `workflow_resume` **com erro/timeout** só é chamado no **timeout final** da espera
   (delegate timeout / timeout scanner) → B `on_timeout`. Um reconnect bem-sucedido
   (A-new) resume **antes** disso, com sucesso.

Resultado nos relatórios: **nenhuma sessão C standalone**. B mostra um segmento da
tentativa de confirmação (adiada) e fica suspenso aguardando inbound, até o reconnect
(sucesso) ou o timeout final (falha).

## Fluxo A-new → C (agente → agente; reconnect)

1. Cliente reconecta (webchat) → A-new intake detecta `pending_workflow` → menu → confirma
   → `delegate(portabilidade_confirmacao)`.
2. C roda como **segmento de A-new** (`segment_id` sob o `session_id` de A-new, sem sessão
   própria). A-new **permanece `active`/webchat**.
3. C faz o I/O real (notify→menu→confirma) → `workflow_resume` no token de **B** (B fecha)
   → depois `workflow_resume` no `delegate_resume_token` de **A-new**.
4. Fluxo do intake de A-new retoma → `finalizar` → A-new **fecha como webchat normal,
   com 2 segmentos** (intake primário + confirmação specialist).

## Mudanças por componente

1. **skill-flow-service `persistDelegateFn`**: sempre `handle_delegate_conference`
   (specialist no `session_id` do chamador). Remover o ramo `webhook_pool →
   handle_delegate` independente.
2. **channel-gateway**: aposentar `handle_delegate` (criação de sessão independente) —
   ou restringi-lo a canais outbound reais e, nesse caso, gravar `session:{child}:meta`.
   Resume do delegate preserva o `channel` do chamador (sem re-carimbar webhook).
3. **orchestrator-bridge**: publicar `session_suspended` (status=suspended) **apenas**
   para chamador de pool webhook. Chamador webchat/agente em delegate-wait permanece
   `active`. Garantir que A-new feche quando o intake chega a `finalizar` após resume.
   Validar suporte a conference specialist em sessão webhook (B).
4. **analytics-api**: C deixa de existir; B/A-new têm `meta` real → `contact_closed`
   sempre com `tenant_id`. Não regravar `channel` no resume. Segmentos vêm dos eventos
   de participante (specialist → `parent_segment_id`).
5. **platform-ui `ListaTab`**: classificar por `channel_type` real, não por presença de
   step `delegate`/`suspend`. *(Fase C — pendente.)*
6. **YAML `agente_confirmacao` `verificar_canal`**: **default = `aguardar_inbound` (adiar)**.
   Só notifica proativamente quando `customer_present == "true"` (flag literal passado pelo
   reconnect de A-new) OU canal outbound explícito (`email`, etc.). Não usar
   `delegate_resume_token` (existe nos dois casos no v2) nem depender de
   `confirmation_channel` (é `@ctx`-ref e pode não propagar — ver Pendências). `intake`
   `retomar_processo` passa `customer_present: "true"`.
7. **`handle_delegate_conference`**: grava o `resume_token` com o **step_id real** do pai
   (passado pelo skill-flow-service), não o literal `"delegate_conference"` (que quebrava
   o resume). Escreve a `pending_workflow` key quando há `contact_identifier` (B→C). Usa o
   **canal real do pai** no inbound do specialist.
8. **`handle_resume`** e **`parse_inbound`**: resume preserva o canal real (lê do meta);
   `parse_inbound` ignora convites com `conference_id` (specialist ≠ contato novo).

## Como cada problema observado se resolve

| Problema | Resolução |
|---|---|
| Session C aparece standalone | delegate nunca cria sessão → C é segmento do chamador |
| A-new marcada como webhook | delegate-wait de chamador webchat não publica webhook-suspend nem regrava canal |
| A-new presa em `active`, não finaliza | resume conference-native retoma o intake → `finalizar` → fecha |
| C rodou 10min / `meta` ausente | C não é mais sessão; segmento de confirmação resolve rápido |

## Fases de implementação

- **Fase A** — Parar de materializar C: `persistDelegateFn` sempre conference; B→C
  inbound_only marca B como `awaiting_customer_inbound`. Ganho visível: C some da lista.
- **Fase B** — delegate-wait não-webhook: bridge não marca chamador webchat como
  suspended/webhook; resume preserva canal e fecha A-new no `finalizar`.
- **Fase C** — UI: heurística de classificação por `channel_type` real; badge de status
  derivado de participantes vivos (não do estado interno do fluxo).
- **Fase D** ✅ — Timeout scanner do delegate. Implementado em
  `channel-gateway/adapters/webhook.py` (`run_timeout_scanner`, background task no
  `lifespan`, intervalo 60s): varre `*:resume_tokens`, e para cada token com `expires_at`
  (3º campo do valor) vencido dispara `handle_resume(decision="timeout")` → bridge
  `_handle_webhook_session_resumed` → engine `on_timeout` do step. `handle_resume` consome
  o token (HDEL), então cada expiração dispara uma vez. Cobre **suspend** (aprovação da
  operadora, 48h — antes sem timeout pós-deprecação do workflow-api) **e delegate**
  (confirmação inbound_only). `pending_workflow` key fica stale e é auto-limpa por
  `get_pending_workflow` no próximo reconnect. **Nota arquitetural:** este scanner é o
  primeiro corte; a consolidação num scheduler central (sorted-set + `timer.fired`) está
  registrada em [`docs/adr/adr-timer-scheduler.md`](../adr/adr-timer-scheduler.md).
- **Fase E** — Workflow Execution Trace (step-level). Ver seção dedicada abaixo.

## Fase E — Workflow Execution Trace (step-level)

> Conceito que sobrevive do Arc 18 (`ProcessStepTimeline`), reaproveitado como o trace
> de detalhe de uma sessão webhook em Analytics/Sessions. Hoje o trace mostra só
> participação; falta a timeline de steps.

### Problema

O `_build_workflow_trace` (`analytics-api/sessions.py`) é **baseado em segmentos**
(`analytics.segments`): produz nós `input_origin` (intake), `webhook_exec` e
`specialist_output`. Mostra **quem participou**, não **quais steps o fluxo executou** —
por isso a sessão B aparece com só 2 nós (exec + output) em vez de toda a sequência
(`solicitar_operadora` → `determinar_canal_confirmacao` → `setar_canal_*` →
`notificar_e_confirmar` → `encerrar_sucesso`).

### Duas camadas a unir no relatório

- **Camada A — participação** (já existe): intake (input) → execução webhook → specialist
  de confirmação (output). Vínculo do intake via `origin_session_id` (topologia estrela).
- **Camada B — timeline de steps** (a implementar): cada step do fluxo com
  `entered_at → left_at`, tipo do step e `transition_reason`.

### Fontes de dados (sem infra nova)

- **Sessões ativas/suspensas**: `pipeline_state.transitions[]` + `pipeline_state.results`
  no Redis (`{tenant}:pipeline:{session}`). O engine já registra cada transição.
- **Sessões fechadas**: stream canônico persistido (`session:{id}:stream`) — eventos
  `session_suspended` (`step_id`, `resume_token`) e `session_resumed` (`step_id`, `payload`).

### "Antes do suspend / depois do resume" (recurso da v1)

**E.1 implementado (2026-05-31):** o endpoint `GET /sessions/{id}/pipeline-state` agora
expõe `step_io` (extraído de `pipeline_state.results`): por step, `decision` +
`payload` (recebidos no resume) + `child_session_id` (do delegate). O
`WebhookSegmentDetail` renderiza isso sob cada step no timeline. O "enviado antes do
suspend" continua visível no strip de Input Context (as tags `session.*` que o delegate
gravou).

**E.3 implementado (2026-05-31):** `resumed_by` por step — derivado do `source` do payload
de resume. Cada caminho carimba a fonte: tool `workflow_resume` → `agent` (um agente
retomou), curl/operador direto → `external` (default no `handle_resume`), timeout scanner →
`timeout_scanner`, intake cancelar → `customer_reconnect`. O `step_io` expõe `resumed_by`
e o `WebhookSegmentDetail` mostra "retomado por: …". Pendente: timestamps
`suspended_at`/`resumed_at`.

Para cada step de `suspend`/`delegate`, anexar:

- **`payload_in` (enviado antes do suspend)**: output do step + tags `session.*` gravadas
  no ContextStore naquele ponto (ex.: `confirmation_channel`, `numero_atual`). De
  `pipeline_state.results[step]` + snapshot do ctx.
- **`payload_out` (recebido após o resume)**: `decision` + `payload`. Do evento
  `session_resumed` (stream) e de `pipeline_state.results[{step}:__resume_payload__ /
  __resume_decision__]`.
- **`resumed_by`**: `session_resumed.author_id` / `payload.source` — distingue operador
  (curl approve), cliente (reconnect via A-new) ou o timeout scanner (Fase D).
- **`suspended_at` / `resumed_at`** + a razão do suspend.

### Outras informações relevantes a incluir

- **Snapshot do ContextStore por suspend** — as tags `session.*` no momento (a decisão tomada).
- **Duration webhook ✅ (E.2 implementado)** — `reports_query.py` `_fetch_sessions`:
  para canal `webhook`, `handle_time_ms` = **tempo decorrido total** do processo
  (`closed_at − min(segment.started_at)`), via JOIN `_segdur`. Usa o início do primeiro
  segmento porque o `opened_at` é re-carimbado a cada resume. Decisão (2026-05-31): o
  decorrido total é mais útil como "duração" que a soma das durações de segmento (que dava
  ~ms — tempo de engine, pouco informativo). Inclui as esperas (suspends) — é a duração
  real do caso. *Futuro:* "corridas vs úteis" (business_hours) lado a lado; soma de
  segmentos (trabalho ativo) como métrica secundária no trace.
- **MCP audit** (`mcp.audit` → ClickHouse) — tools chamadas pelos steps `invoke`
  (`context_set`, `workflow_resume`, …) com allowed/injection/duração ("o que o workflow
  fez nos bastidores").
- **Agent business events** (Arc 12 `agent_event`) emitidos durante o fluxo — KPIs de negócio.
- **Transcript do specialist** (interação de confirmação com o cliente) linkado no nó de output.
- **`close_reason`/`outcome` por ramo** (resolved/failed/timeout) e retries de step `catch`.
- **Dados capturados no intake** (número/operadora/contato) no nó `input_origin`, com link
  pro transcript de A.

### Esboço de implementação

Estender `GET /sessions/{id}/workflow-trace`: em cada nó de execução, anexar um array
`steps[]` (de `pipeline_state.transitions` para ativas/suspensas; do stream persistido para
fechadas), com os campos `payload_in`/`payload_out`/`resume_decision`/`resumed_by`/
`suspended_at`/`resumed_at` nos steps de suspend/delegate. A UI expande o nó `webhook_exec`
nessa timeline (substitui a antiga aba Trace do Arc 18).

## `context_set` — tool de escrita no ContextStore (corrigido)

**Causa-raiz encontrada:** `context_set` era referenciado em YAMLs (`setar_canal_*`,
`escrever_*`) mas **não existia como tool registrado** — o step `invoke` chamava
`mcpCall("context_set")`, falhava, caía no `on_failure` e a tag nunca era escrita. Por
isso `confirmation_channel` (e qualquer escrita via context_set) chegava `nil` no ctx.

**Fix:** registrado o tool `context_set` em `mcp-server-plughub` (`session.ts`):
grava `{tenant}:ctx:{session}` campo `tag` = `{value, confidence, source, visibility,
updated_at}`. Recebe `session_id` + `tenant_id` no input (o `mcpCall` do skill-flow não
injeta contexto) — os YAMLs passam `$.session_id` / `$.tenant_id`. Permissão
`context_set` já constava no registry dos agentes intake e processo.

## Pontos a validar em runtime

- Conference specialist dentro de sessão **webhook** B (sem cliente vivo) — o routing/
  bridge ativam e produzem segmento corretamente?
- `confirmation_channel` chega como `inbound_only` no contexto do specialist (na v1 o C
  caía no menu de 10min, indício de que o contexto não propagou) — confirmar propagação.
