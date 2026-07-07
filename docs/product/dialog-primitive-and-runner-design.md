# Desenho — Primitivo de diálogo genérico + dialog-runner (survey + OTP)

**Status:** Desenho (aprovado nas 6 bifurcações abaixo; implementação pendente do OK final).
**Data:** 2026-07-06
**Relacionado:** `docs/adr/adr-otp-workflow-and-dialog-primitive.md` (as 4 costuras + D1–D3),
`docs/arcos/customer-surveys.md` (§16/§17/§18 interpretador+editor, §19 retorno outbound),
`docs/arcos/delegate-workflow-io.md` (padrão delegate A2A), Arc 19 (unified session / collect / perfis).
**Componentes previstos:** novo `packages/dialog-api` (store fino), `packages/schemas` (`dialog.ts`),
`packages/skill-flow-engine` (2 extensões §17.3 + `skill_dialog_runner_v1`), `packages/mcp-server-plughub`
(`form_get`, thin), `packages/platform-ui` (editor — fase posterior).

> **Este doc é o desenho dos DOIS artefatos que destravam a implementação:** (1) o schema do form/dialog
> JSON genérico; (2) o contrato do dialog-runner Tier-3. Não é código. Ancorado em **dois consumidores
> reais** — prompts de OTP e perguntas de survey — para evitar generalização especulativa.

---

## 0. Decisões travadas (bifurcações fechadas 2026-07-06)

| # | Decisão | Escolha | Razão curta |
|---|---|---|---|
| D-STORE | Onde mora o store de dialog-forms | **`dialog-api` dedicado (fino)** | Dialog-form ≠ evaluation-form (script apresentado ao cliente × rubrica pontuada). Primitivo de plataforma neutro (ADR D2). Custo de infra aceito; espelha o versionamento da `EvaluationForm`. |
| D-SINK | Modelo de resultado do runner | **Devolve cru; domínio faz verify/record** | Mantém `verify` × `record` fora do runner (ADR: "não unificar à força"); preserva a costura de segredo. |
| D-I18N | Representação de i18n | **Mapa de locale embutido no JSON** | Form é dado do tenant → traduções viajam no JSON, resolvidas por `default_locale` + locale da sessão. Nada em locale files (código). |
| D-RENDER | Escopo de render v1 | **Estagiado: §17.3 (2 peças) + N statements + 1 turno de coleta.** Loop sobre N perguntas sequenciais = fatia 2 | Cobre OTP (1 pergunta) e survey transacional. Schema já é N-node; só o *executor* estagia. |
| D-RESULT-SPLIT | Signals × verbatim | **Runner devolve tudo em `answers`; domínio separa** pelo `capture.metric` do form | Runner domínio-cego. |
| D-BRANCH | Branching no diálogo | **Fora do JSON — controle é do skill.** Form é script linear | Invariante das costuras; §17 rejeita "form como programa". |

---

## 1. As quatro costuras (invariante-mãe)

Todo o desenho existe para manter estas quatro camadas separadas:

| Camada | Dono no desenho | O quê |
|---|---|---|
| **Conteúdo** | `DialogForm` JSON (via `form_get`) | texto, opções, i18n, validação de **formato** — dado versionado, linear, **sem** controle |
| **Controle** | workflow/skill negocial chamador | branching, decisão verify/record, re-delegate no retry **semântico** |
| **Canal** | dialog-runner (Tier-3, perfil `agent`) + reach do chamador | elege/alcança o canal e coleta na superfície |
| **Segredo** | `OtpService` (channel-gateway) | gera + **envia direto ao canal** + verifica + rate-limit |

**Costura inegociável do OTP:** o **código gerado nunca passa pela mão de um agente/runner.** O `OtpService`
envia direto ao canal da âncora. O runner só apresenta "digite o código" e **carrega o que o cliente digitou**
de volta ao workflow — nunca vê o código gerado. Violar isso mata o OTP. Vale a análoga p/ survey: o runner
**coleta** a resposta do cliente, **não a fabrica** (integridade do dado).

---

## 2. Artefato 1 — Schema do dialog/form JSON genérico

**Arquivo alvo:** `packages/schemas/src/dialog.ts` (Zod, exports nomeados). Store canônico: `dialog-api` (§4).

### 2.1 Princípios

- **Script linear**: `nodes[]` em ordem = fluxo. **Sem `next` condicional** (branching = controle, vive no skill).
- **Dois tipos de node**: `statement` (sem resposta → `notify`) e `question` (captura → `menu`).
- **Retry na mesma superfície**: `question.retry.reprompt` re-apresenta o **mesmo** node em falha de **formato**
  (required/numeric/pattern). Falha **semântica** (código errado, detrator) **não** está aqui — é controle.
- **i18n embutido**: `LocalizedText = string | { locale: texto }`, resolvido por `default_locale` + locale da sessão.
- **Versionado**: `status: draft|published` + `version` monotônico (espelha `EvaluationForm` + skill deploy).
- **Masking respeita o invariante**: campo `masked:true` nunca sai no retorno cru — é para uso de masked-input
  in-flow. O código OTP é **não-mascarado** por design (prova de posse efêmera, não segredo armazenado).

### 2.2 Esboço (nível Zod)

```ts
LocaleCode    = z.string().min(2)                       // "pt-BR", "en"
LocalizedText = z.union([z.string(), z.record(z.string(), z.string())])
//   string      → literal
//   {locale:tx} → tx[session_locale] ?? tx[default_locale] ?? primeiro valor

// Validação de FORMATO apenas (não semântica) — habilita o retry-na-superfície
DialogValidation = z.object({
  numeric:    z.boolean().optional(),
  pattern:    z.string().optional(),      // regex de formato
  min_length: z.number().int().optional(),
  max_length: z.number().int().optional(),
  min:        z.number().optional(),
  max:        z.number().optional(),
}).partial().optional()

// Binding declarativo p/ o domínio (survey) — DADO, não lógica
DialogCapture = z.object({
  metric: z.string().regex(/^[a-z0-9_]+$/).optional(),   // ex.: "csat", "nps"
  value:  z.union([z.number(), z.string()]).optional(),  // valor fixo da opção (button → score)
}).optional()

DialogOption = z.object({
  id:      z.string(),
  label:   LocalizedText,
  value:   z.string().optional(),   // valor de máquina devolvido (default = id)
  capture: DialogCapture,
})

DialogField = z.object({
  id:         z.string(),
  label:      LocalizedText,
  type:       z.string(),           // "text"|"number"|… (agnóstico de canal)
  required:   z.boolean().default(false),
  masked:     z.boolean().optional(),
  validation: DialogValidation,
  capture:    DialogCapture,
})

DialogVisibility = z.union([                    // espelha MenuStep.visibility
  z.enum(["all", "agents_only"]),
  z.array(z.string().min(1)).min(1),            // array pode conter @ctx refs (resolvidas pelo runner)
])

StatementNode = z.object({
  id:         z.literal-id,
  kind:       z.literal("statement"),
  text:       LocalizedText,
  visibility: DialogVisibility.optional(),
})                                              // → notify

QuestionNode = z.object({
  id:          z.string(),
  kind:        z.literal("question"),
  prompt:      LocalizedText,
  interaction: z.enum(["text","button","list","checklist","form"]).default("text"),
  options:     z.array(DialogOption).optional(),
  fields:      z.array(DialogField).optional(),
  masked:      z.boolean().optional(),
  output_key:  z.string(),                      // onde a resposta crua cai no retorno do runner
  capture:     DialogCapture,                   // metric no nível da pergunta (resposta única)
  validation:  DialogValidation,                // formato (perguntas escalares)
  retry:       z.object({
                 reprompt:     LocalizedText,
                 max_attempts: z.number().int().min(1).default(2),
               }).optional(),
  visibility:  DialogVisibility.optional(),
  timeout_s:   z.number().int().min(-1).default(300),
})                                              // → menu (dinâmico, extensão §17.3-2)

DialogNode = z.discriminatedUnion("kind", [StatementNode, QuestionNode])

DialogForm = z.object({
  form_id:        z.string().min(1),
  tenant_id:      z.string().min(1),
  name:           z.string().min(1),
  description:    z.string().optional(),
  status:         z.enum(["draft","published"]).default("draft"),
  version:        z.number().int().positive().default(1),
  default_locale: LocaleCode,
  locales:        z.array(LocaleCode).min(1),
  nodes:          z.array(DialogNode).min(1),   // LINEAR; ordem = fluxo; sem next condicional
  tags:           z.array(z.string()).default([]),  // views do editor (survey/otp) — NÃO-semântico
  created_at:     z.string().datetime(),
  updated_at:     z.string().datetime(),
})
```

### 2.3 Onde a costura conteúdo × controle é desenhada DENTRO do schema

- **Retry de formato** (`validation` falha → `retry.reprompt`, mesma superfície) = dado, no form.
- **Retry semântico** (OTP `verified:false`, resposta é detrator) = controle → o skill decide e **re-delega**.
  Nunca no JSON.
- **`capture.metric`** é *binding declarativo* (dado echoado ao domínio), **não** um branch. Skip-logic de
  survey (pular pergunta conforme resposta) = job do skill, não do form.
- **Sem `next` condicional**: o único "fluxo" no JSON é a ordem do array. Isso mantém o form como *conteúdo*.

### 2.4 Ancoragem — Consumidor 1: OTP (`form_id: dialog_otp_possession`)

Substitui os prompts hardcoded de `agente_portabilidade_intake_v1.yaml` (`pedir_codigo_otp`, L253-261).

```jsonc
{
  "form_id": "dialog_otp_possession", "default_locale": "pt-BR", "locales": ["pt-BR"],
  "nodes": [
    { "id": "aviso_envio", "kind": "statement",
      "text": { "pt-BR": "Enviamos um código de 6 dígitos ao seu número." } },
    { "id": "coletar_codigo", "kind": "question",
      "prompt": { "pt-BR": "Digite o código para confirmar:" },
      "interaction": "text", "output_key": "code",
      "validation": { "numeric": true, "min_length": 6, "max_length": 6 },
      "retry": { "reprompt": { "pt-BR": "Código inválido. Digite os 6 dígitos:" }, "max_attempts": 3 },
      "timeout_s": 180 }
  ]
}
```

- O envio real do código é do **`OtpService`** (o workflow negocial chama `otp_challenge` **antes** de delegar).
- A pergunta "Reenviar por outro canal?" = uma `question` cuja resposta o runner devolve crua; o **workflow**
  decide re-desafiar (escolha do usuário + re-dispatch — ADR D1, não escalonamento automático).
- Retry **semântico** (código errado após verify) = o workflow chama `otp_verify`, e em `verified:false`
  re-delega o runner. O `retry` do form cobre só o **formato** (não-numérico / tamanho errado).

### 2.5 Ancoragem — Consumidor 2: Survey (`form_id: dialog_csat_pos_atendimento`)

```jsonc
{
  "form_id": "dialog_csat_pos_atendimento", "default_locale": "pt-BR", "locales": ["pt-BR","en"],
  "nodes": [
    { "id": "intro", "kind": "statement",
      "text": { "pt-BR": "Sua opinião importa — 1 pergunta rápida.", "en": "1 quick question." } },
    { "id": "q_csat", "kind": "question",
      "prompt": { "pt-BR": "Como você avalia o atendimento?", "en": "How was the service?" },
      "interaction": "button", "output_key": "q_csat", "capture": { "metric": "csat" },
      "options": [
        { "id": "1", "label": {"pt-BR":"1 😠"}, "value": "1", "capture": {"value": 1} },
        { "id": "2", "label": {"pt-BR":"2"},    "value": "2", "capture": {"value": 2} },
        { "id": "3", "label": {"pt-BR":"3"},    "value": "3", "capture": {"value": 3} },
        { "id": "4", "label": {"pt-BR":"4"},    "value": "4", "capture": {"value": 4} },
        { "id": "5", "label": {"pt-BR":"5 😀"}, "value": "5", "capture": {"value": 5} }
      ] },
    { "id": "q_comentario", "kind": "question",
      "prompt": { "pt-BR": "Quer comentar? (opcional)" },
      "interaction": "text", "output_key": "q_csat_text" },     // sem capture.metric → verbatim
    { "id": "obrigado", "kind": "statement", "text": { "pt-BR": "Obrigado!" } }
  ]
}
```

- Mapeamento métrica → interação (§16.3: csat→button, nps→list, etc.) é **do editor** ao gerar o JSON; o
  runner só honra `interaction`. O fallback por canal (botão ≤3 no WhatsApp) fica **no Channel Gateway adapter**
  (invariante) — o form nunca é channel-specific.
- Pergunta **sem `capture.metric`** + `interaction:text` = verbatim (open_text) → o **workflow de survey**
  roteia ao sink LGPD (`survey_response.open_text` + `session.survey.verbatim`), não ao `survey_record`.

---

## 3. Artefato 2 — Contrato do dialog-runner (Tier-3 genérico)

**Skill alvo:** `skill_dialog_runner_v1` (perfil **`agent`** — usa `menu`/`notify`). Invocado via `delegate()`
(padrão `delegate-workflow-io`): roda como **conference specialist** dentro do `session_id` do chamador (produz
um segmento; nunca sessão própria). Devolve por `workflow_resume(resume_token, payload)`.

### 3.1 Parametrização — `(form_id, política de canal, sink)`

Entra via `Skill.interface_schema` → `PoolSkillSlot.config_json` → exposto como `$.config.*`
(extensão de engine §17.3-1).

```jsonc
// interface_schema (valores no config_json do slot)
{
  "form_id":       "dialog_otp_possession",   // qual diálogo (obrigatório)
  "form_version":  12,                         // opcional: pina a versão
  "channel_policy": "session",                 // v1: só "session" (ver 3.4). "elect" = fatia 2
  "result":        "return"                    // v1 fixo: coleta pura, devolve cru (D-SINK)
}
```

### 3.2 Retorno ao chamador (payload do `workflow_resume`)

```jsonc
{
  "answers":      { "code": "482913" },                 // por output_key, cru, SEM campos masked
  "captures":     { "code": {} },                        // echo do capture.metric/value do form (domínio-cego)
  "attempts":     { "coletar_codigo": 1 },               // tentativas por node (retry de formato)
  "channel_used": "whatsapp",
  "completed":    true
}
```

- **Campos `masked:true` nunca aparecem no retorno** (invariante). São para masked-input in-flow; se um form
  os usa, o valor fica no `ctx.maskedScope`, não no `answers`.
- **`captures`** é só o echo declarativo do form — o chamador (workflow de survey) usa p/ montar
  `survey_record(signals)` sem re-`form_get`. Mantém o runner domínio-cego (D-RESULT-SPLIT).

### 3.3 Fluxo do runner (v1 — 1 turno de coleta)

```
1. invoke form_get($.config.form_id [, form_version], locale)  → $.pipeline_state.dialog_form
2. statements iniciais  → notify (visibility do node)                [cadeia fixa curta, sem loop]
3. TURNO DE COLETA (exatamente um):
     - 1 question escalar → menu (interaction do node, options/fields DINÂMICOS do form — extensão §17.3-2)
       OU 1 question interaction=form → menu(form) com M fields dinâmicos (form inteiro num payload, §18.3)
     - retry de FORMATO na mesma superfície (validation falha → reprompt)   [v1: 1 reprompt fixo, ver 3.5]
4. statements finais    → notify
5. monta o retorno { answers, captures, attempts, channel_used, completed }
6. workflow_resume(resume_token, payload=retorno)                    [delegate-workflow-io → retoma o chamador]
```

- **v1 suporta:** qualquer nº de `statement` (cadeia fixa) + **exatamente um** turno de coleta (1 pergunta,
  ou 1 `interaction=form` multi-field). Cobre OTP (statement + 1 pergunta) e survey transacional.
- **Múltiplas `question` sequenciais** (loop sobre N perguntas em canal pobre) = **fatia 2** (precisa do
  primitivo de iteração — §3.6). O schema já é N-node; só o executor estagia.

### 3.4 Política de canal — quem alcança o cliente

Costura "canal": o **reach** é do **chamador**, o **runner** só renderiza na sessão em que foi delegado.

- **Caminho leve (v1, `channel_policy: session`)**: âncora == canal da sessão (cliente no WhatsApp, código no
  WhatsApp). O workflow chama `otp_challenge` (OtpService → mesmo canal) e delega o runner na **sessão viva**.
  O runner apresenta "digite o código" e coleta. Sem sessão-filho.
- **Caminho pesado (cross-canal)**: cliente no webchat, código no telefone. O **workflow** (perfil `workflow`)
  abre a **sessão-filho** via `collect` (Arc 19) no canal eleito, e delega o runner **nessa** sessão-filho. O
  runner segue idêntico (renderiza na "sessão atual"). O `collect` é do workflow (perfil segrega: workflow faz
  collect, agent-runner faz o turno de menu). **v1 compõe** este caminho reusando o `collect` existente.
- **`channel_policy: elect`** (o runner **ele mesmo** apresenta um menu de canais e dispara o re-dispatch) =
  **fatia 2**. Em v1 a eleição de canal, quando existir, é uma `question` no form cuja resposta o workflow lê.

### 3.5 Retry de formato — v1 vs fatia 2

O engine hoje **não tem contador/aritmética** (§17.2). Então em v1:

- **v1**: `retry` do form = **um reprompt fixo** (unroll: menu → `on_failure` → menu-reprompt → desiste). Sem
  contador. Cobre o caso comum (errou o formato uma vez).
- **Fatia 2**: `retry.max_attempts` honrado plenamente (precisa do contador que vem junto do primitivo de loop).
- **Independente disso**: o retry **semântico** (OTP código errado) é do chamador (re-delega), nunca do runner.

### 3.6 As extensões de engine que o runner exige (base reaproveitável — §17.3)

Confirmadas como lacunas no código:

1. **`$.config.*` no flow** — `interpolate.ts` (L262-276) hoje não expõe `config`; o `PoolSkillSlot.config_json`
   não chega ao runtime. Plumbing: dispatcher (bridge p/ hooks, worker p/ webhook) lê o `config_json` do slot e
   o injeta no launch → `evalContext.config`. **É por aqui que `form_id`/policy chegam ao runner.**
2. **`menu.options`/`menu.fields` dinâmicos** — `MenuStepSchema` (`skill.ts` L358-389) e `menu.ts` (L62-68) hoje
   só aceitam arrays estáticos. Vira união `array | ref`; runtime resolve o ref via `resolveInputValue`
   (índice **literal** — suficiente p/ 1 turno). **É por aqui que a pergunta é renderizada do JSON.**

**Fatia 2 (deferida): 3ª extensão — primitivo de iteração** (step `loop`/`foreach` ou contador + índice
variável em `pipeline_state`), com bookkeeping por iteração e cuidado com a guarda anti-runaway
(`engine.ts` L246-250). Habilita N perguntas sequenciais em canal pobre + `retry.max_attempts` pleno.

---

## 4. Store — `dialog-api` (dedicado, fino) — D-STORE

Novo pacote `packages/dialog-api` (Python FastAPI + asyncpg, porta a alocar). **Fino**: CRUD sobre JSON
versionado, espelhando o versionamento da `EvaluationForm` (não inventar). Reusa um Postgres existente
(schema `dialog`).

### 4.1 Modelo & endpoints (esboço)

```
Tabela dialog.forms: (tenant_id, form_id, version, status, json JSONB, created_at, updated_at)
  PK (tenant_id, form_id, version).  status ∈ {draft, published}.  1 published "corrente" por form_id.

REST (header X-Tenant-ID; auth admin p/ escrita, ABAC a definir):
  GET    /v1/dialog/forms                       → lista (metadados)
  GET    /v1/dialog/forms/:id?status=published  → JSON resolvido (default = published corrente)
  POST   /v1/dialog/forms                        → cria draft
  PUT    /v1/dialog/forms/:id                    → edita draft (published → nova versão draft)
  POST   /v1/dialog/forms/:id/publish            → snapshot imutável → published corrente
```

### 4.2 `form_get` (tool MCP genérica — substitui `survey_form_get`)

`mcp-server-plughub` (thin) → `dialog-api GET /v1/dialog/forms/:id?status=published&locale=…` → JSON do form
(nodes normalizados + metadados de capture). O runner: `invoke form_get output_as: dialog_form` → menu lê
`$.pipeline_state.dialog_form.nodes[…]`.

### 4.3 Deploy binding (reusa o slot Next/Current — §17.6)

`PoolSkillSlot.config_json = { form_id, form_version? }` no slot do `skill_dialog_runner_v1`. **Next → promote →
Current** (imutável) = deploy formal da troca de form; **Previous** = rollback; promote dispara
`registry.changed` (hot-reload). Fonte canônica do form = `dialog-api` (não duplica JSON no slot). *(Migração
de pool a slot exige `set-next`+`promote` — não basta editar YAML.)*

### 4.4 Custo assumido (do trade-off D-STORE)

Novo serviço no `docker-compose.demo.yml` + porta + migração + health check; o leitor de analítica (navegador
de respostas §10b, `agente_survey_analyst_v1` §10c) passa a cruzar a fronteira do `dialog-api` p/ rotular
respostas pelo form. Aceito pela limpeza de domínio (primitivo neutro de plataforma).

---

## 5. Reuso pelo survey — "interação scriptada delegada"

OTP e survey = duas instâncias do mesmo padrão. Ativos **compartilhados**: (a) schema/editor de diálogo
(conteúdo, §2), (b) `skill_dialog_runner_v1` (canal+coleta, §3), (c) orquestração `delegate-workflow-io`
(controle). Ativos **distintos** (não unificar — ADR): `otp_verify` × `survey_record`, e a costura de segredo.

Consequências no arco de surveys (a atualizar quando implementar): §17/§19 passam a **consumir** o dialog-runner
+ `form_get` genérico em vez de `skill_survey_runner_v1`/`survey_form_get` bespoke; o retorno outbound (§19) usa
o mesmo `collect`/especialista do caminho pesado do OTP (§3.4).

---

## 6. Faseamento

- **Fatia 0 (este doc)** — desenho dos 2 artefatos ✅.
- **Fatia 1 — primitivo v1**: `schemas/dialog.ts`; `dialog-api` fino + `form_get`; extensões de engine §17.3-1
  e §17.3-2; `skill_dialog_runner_v1` (1 turno). Consumidor de validação = **OTP** (migra o intake para delegar
  ao runner com `dialog_otp_possession`, mantendo `OtpService`/`otp_verify` no workflow — costura de segredo).
- **Fatia 2 — loop + editor**: 3ª extensão (iteração/contador) → N perguntas sequenciais + `retry.max_attempts`
  pleno; `channel_policy: elect`; editor (form-builder) no platform-ui; survey adota o runner (atualiza §17/§19).
- **Adiado (trilhas próprias, ADR "fora de escopo")**: entrega real do OTP (provedor SMS/e-mail), auditoria
  (`mcp.audit`), lockout crescente, merge/external_refs de identidade.

---

## 6.1 As-built — Fatia 1 (implementado 2026-07-06)

Entregue nesta sessão (pendente build/teste do usuário):

- **`@plughub/schemas`** — `dialog.ts` (`DialogFormSchema`, nodes, `LocalizedText`, `capture`,
  `validation`, `resolveLocalizedText`) + exports no `index.ts`.
- **Engine §17.3-1 (`$.config.*`)** — `StepContext.config`, threading `run→_execute→_buildContext`,
  `evalContext.config` em `interpolate.ts`, passthrough `config` no `skill-flow-service /execute`.
  **Não usado pelo OTP em Fatia 1** (ver binding abaixo); pronto para o deploy-por-slot do survey.
- **Engine §17.3-2 (menu dinâmico)** — `MenuStepSchema.options/fields` viram união `array | string(ref)`;
  `menu.ts` resolve o ref via `resolveInputValue` (`resolveMenuArray`) antes de montar o payload.
- **`dialog-api`** — novo pacote Python (porta 3760, schema `dialog.forms`, CRUD + publish, versionado
  draft/published); serviço no `docker-compose.demo.yml`.
- **`form_get`** — tool MCP fina (`tools/dialog.ts`) → dialog-api; normaliza o form num bloco `render`
  single-turn (`menu_prompt`, `fields`, `statement_after`, `captures`) para o runner consumir direto.
- **`skill_dialog_runner_v1`** — runner Tier-3: `form_get → menu(interaction=form, fields dinâmicos) →
  workflow_resume(payload=answers) → complete`. Pool `dialog_runner` no registry.
- **OTP (consumidor de validação)** — o intake `agente_portabilidade_intake_v1` **delega** a coleta do
  código ao runner (`coletar_codigo_dialog` → pool `dialog_runner`, `context.dialog_form_id=
  dialog_otp_possession`); lê `$.pipeline_state.coletar_codigo_dialog.code` no `otp_verify`. `OtpService`
  (challenge/verify) permanece no intake — **costura de segredo intacta**. Seed:
  `infra/test/seed_dialog_otp_form.sh`.

**Decisão as-built — binding do `form_id` ao runner = contexto de delegate (`@ctx.session.dialog_form_id`),
não `$.config`.** O caminho de delegate já escreve o context no ContextStore do especialista (padrão
`delegate-workflow-io`, provado pelo `agente_confirmacao`), então o runner lê `@ctx.session.dialog_form_id`
sem tocar o plumbing bridge→slot. A extensão `$.config.*` foi construída e o passthrough do launcher está
pronto, mas o plumbing bridge→`PoolSkillSlot.config_json` (deploy-por-slot do survey) fica para a Fatia 2.

**Deferido para Fatia 2 (schema já pronto):** retry de formato (`validation`/`retry` ficam no form, runner
v1 não os enforça — sem contador no engine); loop sobre N perguntas sequenciais; `channel_policy: elect`
(runner apresenta menu de canal); editor (form-builder) no platform-ui; adoção pelo survey + plumbing
`$.config` bridge→slot.

## 6.2 As-built — Fatia 2 (implementado 2026-07-06)

Fecha os itens antes deferidos (§6.1) e formaliza o achado dos **dois veículos**.

- **Adoção pelo survey (2º consumidor)** — `agente_survey_reconnect_v1` delega ao runner (form
  `dialog_nps_v1`); `skill_survey_v1` faz `survey_record`. **NPS ativo de fim-de-contato**
  (`agente_nps_v1`, hook `on_contact_end`) consome o primitivo **INLINE** (`form_get` + menu dinâmico),
  sem delegate — form `dialog_nps_buttons` (botões 0-10, customer-only).
- **Achado "dois veículos"** — hooks de `on_contact_end` **não podem delegar** (delegar suspende o hook
  agent → o bridge trata `suspended` como hook concluído → fecha o contato antes de renderizar). Logo:
  runner-especialista (delegate/suspend) p/ chamadores que podem suspender (OTP intake, survey reconnect);
  **INLINE** (`form_get`+menu) p/ hooks. Guard #17 (`registry_syncer._validate_teardown_hooks`) proíbe
  `suspend`/`collect`/`delegate` em skills de hook de teardown no sync.
- **Render nativo single-question + payload `{value}`** — `form_get` expõe `render` nativo (usa a
  interação da pergunta); o runner devolve `payload={value:<escalar>}` uniforme; o domínio lê
  `$.pipeline_state.<delegate>.value`.
- **Editor (form-builder)** — `/config/dialog-forms` no platform-ui (proxy `/v1/dialog`), cria/edita/publica
  DialogForms. MVP locale único, sem preview, write aberto (ABAC/multi-locale ficam p/ Fatia 2 residual).
- **3ª extensão — step `loop`** — N perguntas sequenciais em canal pobre; item atual em path fixo (sem
  índice variável), contador tipo `receive`, guardado pelo `menu` do body; `validateFlow` aceita ciclo que
  passa por `loop`. Consumidor: `skill_survey_multi_v1` (pool `survey_multi_ia`, form `dialog_survey_multi_v1`).
- **Veículo web** — `GET /survey/{token}` (channel-gateway `survey_web.py`) renderiza o **mesmo**
  `DialogForm` como `<form>` público e grava via `session.signals` (mesma trilha do `survey_record`).
  Snapshot do form no `create` (token Redis, TTL). **Três superfícies, um conteúdo:** chat (runner),
  inline (hook), página web. Validado: web-test → `session.signals` → ClickHouse (`csat`/`ces`).

- **Retry por formato ✅ (2026-07-07)** — `MenuStep.validation`+`retry` (união objeto|ref); o `menu` faz
  reprompt na mesma superfície em falha de FORMATO (numeric/pattern/faixa/comprimento), honra `max_attempts`,
  esgota→`on_failure`. Só escalar; timeout/desconexão/@mention não são retry; semântica (código OTP) segue
  no chamador. `form_get` expõe validation/retry no render; runner + loop consumer passam os refs.

**Deferido residual (Fatia 2):** entrega real do link web (provedor SMS/e-mail); timeout dinâmico do runner;
`channel_policy: elect`; plumbing `$.config` bridge→slot; multi-locale + preview + auth no editor.
Follow-up de demo-infra: vazamento de instância no `portabilidade_ia`.

## 7. Pontos a validar em runtime (quando implementar)

- Plumbing do `config_json` no launch para **hook `on_*` (bridge)** e **webhook (worker)** — os dois dispatchers.
- `resolveInputValue` resolvendo `options`/`fields` do `$.pipeline_state.dialog_form` no `menu.ts` sem quebrar
  o caminho estático (união retrocompatível).
- Conference specialist (runner) rodando dentro de sessão-filho de `collect` no caminho cross-canal (§3.4) —
  reusa o ponto a validar já registrado em `delegate-workflow-io.md` (specialist em sessão sem cliente vivo).
- Retorno cru chegando ao chamador via `workflow_resume` com o `payload` (answers/captures) intacto.
