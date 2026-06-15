# Frente 1 — Dispatch Pull + Inbox + Aprovação · Plano Consolidado

> Consolidação dos três specs (`routing-pull-dispatch-spec.md`, `human-work-queue-aprovacao-spec.md`,
> `pull-inbox-console-ui-spec.md`) num **mapa de impacto por módulo + task list fatiada + esforço**, para dar
> visão de esforço antes de implementar. **Status:** planejamento. **Data:** 2026-06-15.
> **Rev. 2026-06-15:** decisões D1–D3 resolvidas (§4); nó frágil #1 refinado para "capacidade compartilhada
> push+pull no semáforo do recurso + rollback do `ZREM`" — a fila em si não tem conflito (§3, F1).

## 0. Dependências verificadas (não precisam ser construídas)

- **AttachmentStore** — existe (`channel-gateway/src/plughub_channel_gateway/attachment_store.py` + testes).
- **Retorno do `delegate`** — `skill-flow-engine/src/steps/delegate.ts` já devolve `output_as: step.id` +
  `output_value: payload` no resume → o payload do `workflow_resume` cai em `$.pipeline_state.<id_do_delegate>`.
  Logo a "decisão pelo retorno do delegate" é **reuso puro**, sem mudança de schema/engine.

## 1. Escopo (o que a frente entrega)

1. **Modo de despacho `pull`** genérico por pool (`dispatch_mode: push|pull`, default push). Em pull o Routing
   Engine parqueia o contato na fila e **não auto-aloca**; o agente lista e pede o **claim**, concedido
   atomicamente (`ZREM`). Push inalterado. Pools push e pull coexistem.
2. **Operações** no Routing Engine, expostas como operational tools: `work_queue_list`, `work_task_claim`
   (claim atômico + `mark_busy` + abre segmento + grava **lease**), `work_task_release` (re-enfileira),
   **auto-release** (lease expira via heartbeat/crash_detector). Dois relógios: SLA não-claimada (existe) +
   lease curta do claim (novo).
3. **Inbox no Console** integrado ao atendimento (3 zonas: rail de filas → lista → preview/atendimento), com
   **preview soft** (sem claim) → **Pull** → claim; estados/lock; **cor por SLA**; indicador de **capacidade**
   (push+pull combinados); atualização via **ciclo do heartbeat** (sem evento dedicado).
4. **Aprovação** (1ª especialização): work item = **sessão de workflow suspensa** num pool pull (via
   `delegate`); **pacote** (form padrão + `decisions` + `attachments` + `form_ext` opcional) no
   `delegate.context`; **decisão volta pelo retorno do delegate**; o **workflow principal** roteia (`choice`
   sobre `$.pipeline_state.<delegate>`); edições **auditadas**.
5. **Ordenação por peso** (push+pull): score da fila = peso computado (expressão por pool sobre perfil de
   agente/usuário + tags `session.queue.*` + componente de idade anti-starvation). *Realça push também.*
6. **ABAC** `approvals` (`operacao`/`decide`) + filtro por `accessible_pools`.
7. **Métricas/Monitor/Bancada**: tempo na fila, tempo de tratamento, **rework rate** por passo (qualidade do
   processo montado pela IA — liga ao Arc 6/bancada); Monitor mostra pools pull.

## 2. Mapa de impacto por módulo

| Módulo | Tipo | O que muda | Esforço |
|---|---|---|---|
| `schemas` | alterado | `PoolConfig += dispatch_mode`; registro ABAC `approvals`. Pacote/`decisions`/`attachments`/`form_ext` = **JSON no `delegate.context`** (sem Zod obrigatório; opcional validar). | **P** |
| `routing-engine` | alterado **+ novo** | `route()` branch pull (→ `_build_queued_result`, pula `_allocate`); agent-ready/dequeue **ignoram** pull; **claim `ZREM`** + `mark_busy` + segmento + **lease** (`pool:{id}:claim:{sid}` TTL); release/auto-release via `add_queued_contact` + crash_detector. *(Weight-ordering = sub-projeto, F6.)* | **G** · nó frágil |
| `mcp-server-plughub` | novo | operational tools `work_queue_list`/`work_task_claim`/`work_task_release` (wrappers finos do routing); gate `instance_has_capacity`. | **M** |
| `skill-flow-engine` (delegate) | reuso | `delegate.context` já passa contexto; retorno já existe. Só garantir o pacote (JSON) fluir. | **P** |
| `channel-gateway` | reuso / **decisão** | AttachmentStore existe. **Decisão de design:** os specs **não** criam `channel_type` "console" — usam `delegate`→pool pull + inbox lendo a fila. (Ver §4.) | **P** (ou 0) |
| `platform-ui` (Console) | novo | inbox 3-zonas (rail/lista/preview/Pull); estados/lock; **cor SLA**; capacidade; heartbeat snapshot; **renderer do pacote de aprovação** (form+editáveis+anexos+decisões); ABAC `approvals`. | **G** |
| `config-api` | alterado | namespace `routing` += `claim_lease_s`; expressão de peso por pool (F6). | **P** |
| `auth-api` | alterado | módulo ABAC `approvals` (operacao/decide) no `module_registry` + `infra/modules.yaml`. | **P** |
| `analytics-api` / Monitor / Bancada | novo | métricas de fila pull (tempo na fila/tratamento, **rework rate**); Monitor pull pools; integração na bancada. | **M** |
| Auditoria | reuso / novo | trilha append-only de **edições/decisões/visualização** (reusa `audit_access_log`/`ContestationThread`). | **P–M** |

## 3. Nós frágeis e gates

1. **Capacidade compartilhada push+pull no semáforo do RECURSO** (não é conflito de fila). A fila é limpa: o
   `ZREM` é o árbitro de "quem pega o contato" (um vencedor); `agent_ready` drena **só push**; pull **nunca
   auto-aloca** (entra na fila pelas regras de prioridade atuais); `list`/`view` leem só os não-reservados
   (reservado = `ZREM` → fora da lista). O ponto real é a **capacidade do agente** (`max_concurrent_sessions`),
   **compartilhada** entre push e pull — o claim/`reserve` do pull consome um slot do **mesmo recurso** que o
   push, logo passa pelo **mesmo semáforo do recurso** (`claim_instance`). Race só no **último slot** (TOCTOU:
   checar capacidade antes do botão Pull não basta — um push pode comer o slot entre o check e o claim).
   Solução = o claim do pull **reserva via `claim_instance`** (push+pull combinados); como são duas operações
   (`ZREM` da fila + `claim_instance` do slot), **rollback** (re-`add_queued_contact`) se a capacidade perder.
   Não é mecanismo novo — é reusar o semáforo que já existe. Gate: **E2E concorrente** (push roteado + Pull no
   **mesmo agente** disputando o último slot → 1 só vence, sem sobre-alocação, sem contato órfão).
2. **Weight-ordering muda a ordem de dequeue do PUSH** → risco de regressão no push. Gate: **paridade push**.
   Mitigação: **adiar para F6** (pull começa FIFO; weight é enhancement), isolando o risco do core.
3. **Lease/auto-release**: desconexão do Console → crash_detector re-enfileira; lease TTL = backstop "conectado
   ocioso". Gate: E2E (fechar aba → task volta claimável).
4. **Latência do piscar** = intervalo do heartbeat (aceitável p/ async; cadência mais curta com inbox aberta).

## 4. Decisões (resolvidas — 2026-06-15)

- **D1 ✅ — seguir os specs (delegate + pull-pool + inbox; SEM `channel_type` "console").** A aprovação é
  `delegate` a um pool pull + inbox lendo a fila; o "Console como mídia de dados entre passos" se realiza pelo
  `delegate.context` (pacote) + o retorno (`$.pipeline_state.<delegate>`). Reavaliar só se surgir caso que o
  pull-pool não cubra.
- **D2 ✅ — Weight-ordering ADIADO (F6).** v1 pull = FIFO + cor de SLA; weight é enhancement pós-v1.
- **D3 ✅ — `claim_lease_s` dedicado, default por pool** (namespace `routing` da Config API).

## 5. Task list fatiada (dependências + esforço + gate)

| Fatia | Entrega | Dep. | Esforço | Gate |
|---|---|---|---|---|
| **F1 — Pull core (routing)** | `dispatch_mode`; `route()`: pull→fila (sem alocar), push inalterado; `agent_ready` drena **só push**; claim = `ZREM` (1 vencedor) + reserva de slot pelo **semáforo do recurso** (`claim_instance`, push+pull) + abre segmento + lease/heartbeat; **rollback** (re-enfileira) se a capacidade perder; release/auto-release. | — | **G** (frágil) | unit + E2E: 1 vencedor; push+Pull no último slot do mesmo agente sem sobre-alocação; rollback sem órfão; auto-release no disconnect |
| **F2 — Ops tools (mcp-server)** | `work_queue_list`/`work_task_claim`/`work_task_release`; gate capacidade. | F1 | **M** | tool E2E (list/claim/release/preview) |
| **F3 — Inbox genérico (Console UI)** | 3-zonas; rail/lista/preview/Pull; estados/lock; cor SLA; capacidade; heartbeat snapshot; ABAC `approvals.operacao`. | F2 | **G** | UI E2E com pool pull genérico (ex.: e-mail) |
| **F4 — Aprovação: pacote + agente + rota** | `delegate.context` (decisions/attachments/form_ext); agente de aprovação (skill YAML nível c) que captura e devolve outcome; `choice` no workflow principal; edições auditadas. | F1 | **M** | E2E aprovação ponta-a-ponta (delegate→claim→decide→resume→rota) |
| **F5 — Aprovação: renderer no inbox (UI)** | form padrão + editáveis + anexos + botões de decisão + devolver; `approvals.decide`; masking por role. | F3,F4 | **M** | UI E2E aprovação |
| **F6 — Weight-ordering (push+pull)** | score = peso (expressão por pool + `session.queue.*` + idade). | F1 | **M** (sub-arco) | paridade push + ordenação pull |
| **F7 — Analytics/Monitor/Bancada** | métricas fila pull + rework rate; Monitor pull pools; integração bancada. | F1,F4 | **M** | reports + views |
| **F8 — Extras** | quatro-olhos (2 aprovadores); reatribuição por supervisor; notificações de SLA. | F5 | **M** | — |

## 6. Sequência recomendada e cortes

- **v1 genérico (prova o mecanismo):** F1 → F2 → F3. Entrega pull funcionando para um pool async (e-mail/
  back-office), FIFO + cor de SLA. É onde mora o **risco do routing** — fazer com gate concorrente forte.
- **v1 aprovação:** + F4 → F5. Liga ao **gate de promoção** (promover = workflow com passo de aprovação) e à
  **frente 2** (revisar processo montado por IA).
- **Pós-v1:** F6 (weight), F7 (analytics/bancada — superfície compartilhada com a frente 2), F8 (extras).

**Esforço grosso:** 2× **G** no caminho crítico (F1 routing, F3 inbox UI) + ~4× **M**. O core (F1) é o nó
frágil — convém fatiá-lo internamente (claim atômico → lease → auto-release) com gate entre sub-fatias, como
foi feito na alocação atômica do router.

## 7. Conexões com outras frentes

- **Frente 3 (config):** `dispatch_mode`, `claim_lease_s` e a expressão de peso precisam **sobreviver a
  rebuild** (DB-owned) — a frente 3 destrava testar a frente 1 sem reconfigurar.
- **Frente 2 (evaluation):** a **bancada** é superfície compartilhada (F7 aqui × integração de qualidade lá);
  o **rework rate** da aprovação alimenta a leitura de qualidade do Arc 6. Coordenar F7 com a frente 2.
- **Gate de promoção homologação→produção:** vira um workflow com passo de aprovação (F4/F5).
