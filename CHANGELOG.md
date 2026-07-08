# CHANGELOG — PlugHub Implementações Concluídas

---

## EvaluationForm — composição alinhada ao primitivo scoring.ts (#23, 2026-07-08)

Unifica a **semântica** de composição de nota entre survey (`scoring.ts`, TS) e Quality
(`evaluation-api/scoring.py`, Python). Como são linguagens diferentes, não há reuso literal de módulo — a
unificação é "um spec, duas implementações fiéis" (decisão A), com teste de paridade garantindo que concordam.

**Achado (bug latente):** o `scoring.py` **ignorava** `dimension.aggregation` (`weighted_average | min_score`) e
`form.scoring_method` (`weighted_average | simple_average`) — sempre fazia média ponderada. Quem escolhia
`min_score`/`simple_average` na UI não via efeito.

**Fix:** `aggregate_scores` reescrito com um kernel `_compose(items, method)` que **espelha o `composeScore`** do
`scoring.ts` (sobre itens já normalizados a 0..10: `weighted_mean` = Σv·w/Σw, `min` = pior membro ignorando peso,
Σw==0 → 0, vazio → None). Agora honra `aggregation` (critérios→dimensão) e `scoring_method` (dimensões→composite,
`simple_average` = pesos iguais). O caso default (weighted/weighted) é **idêntico** à implementação anterior
(lock de regressão no teste). A mudança de comportamento é **corretiva** (passa a respeitar a config que a UI já
oferecia). `test_scoring.py`: 13 testes (kernel de paridade + form-driven + regressão). NA/text seguem excluídos
com re-normalização de peso.

---

## Dev hygiene — typecheck do agent-registry + testes do injection_guard (2026-07-08)

Dívida pré-existente sem relação com features. **agent-registry**: os 4 erros de `tsc` (`redis.ts` "Cannot find
module 'ioredis'" + `operational.ts` `poolId`/`sid` sem tipo) eram **puramente `ioredis` não instalado no
working-copy** (declarado em `package.json`, ausente no `node_modules`); `npm install` restaura os tipos e os 4
somem em cascata — sem mudança de código. **mcp-server** `injection_guard.test.ts`: os 2 testes l33tspeak/unicode
casavam `.toThrow(/INJECTION_DETECTED/)` contra a **mensagem** (que é o texto descritivo), mas `INJECTION_DETECTED`
é o **`.code`** do erro — realinhados a checar `err.code` (convenção do teste vizinho). 25/25 verdes.
**orchestrator-bridge** `test_webhook_bridge.py` (2 falhas antigas): `test_resume_publishes_agent_ready_and_agent_done`
mockava `producer.send` com `MagicMock(return_value=None)`, mas o publish é `asyncio.create_task(producer.send(...))`
→ precisa de awaitable (`AsyncMock`); `test_process_inbound_does_not_call_resume_handler_for_customer_msg`
patcheava `forward_inbound_to_active_agent` (removida) → substituído por deixar o `process_inbound` correr contra o
`mock_redis` (entrega inline via pub/sub/LPUSH/stream), com `get`/`hgetall` configurados p/ pular o retry-loop de
3s e não vazar coroutine de mock. 17/17 verdes, 0.24s, sem warnings. Nenhuma mudança de código de produção.

---

## Config params — lint de `source` no publish + timeout dinâmico do dialog-runner (2026-07-08)

Dois hardenings pequenos na frente de config-params/dialog.

**Lint de `source` (agent-registry):** `configParamSourceWarnings` avisa — **não-bloqueante** — quando um
`config_param` declara um `source` fora do conjunto conhecido (`dialogforms`/`pools`/`skills`), no PUT/POST de
`/v1/skills` (o momento de authoring). Emite no log + campo `config_param_warnings` na resposta. Pega o typo
(`dialogfroms`) sem fechar o schema — `source` segue `z.string()` aberto, então uma UI defasada não vira erro
(forward-compat). Constante espelha o `CONFIG_PARAM_SOURCES` do platform-ui.

**Timeout dinâmico do dialog-runner:** o `skill_dialog_runner_v1` usava `timeout_s: 180` estático, ignorando o
timeout do form. O `DialogForm` já tinha `timeout_s` por pergunta (default 300) — agora: `form_get` expõe
`render.timeout_s`; `MenuStep.timeout_s` virou união `number | ref` ($./@ctx.); o engine (`menu.ts`) resolve o
ref → número (fallback 300) via `resolveDynamicValue`, mesmo padrão do §17.4; o runner lê
`$.pipeline_state.dialog.render.timeout_s`. Único leitor de `menu.timeout_s` é o `menu.ts` (blast radius contido).
Rebuild: schemas → mcp-server + skill-flow-engine + agent-registry (o `MenuStepSchema` mudou). schemas 165 ✓,
mcp-server typecheck ✓, skill-flow-engine typecheck + 145 testes ✓.

---

## Survey web-link — camada de providers plugável (webhook vendor-neutro, 2026-07-08)

Evolui a entrega do link de survey de um mock único para uma **camada de providers** production-ready,
testável sem canal real. Fecha o resíduo "provider real" da frente de survey (só resta o tenant apontar o
gateway dele).

**survey_web.py:** `SurveyLinkDelivery` deixou de ser monolítico e virou um **roteador** sobre
`LinkDeliveryProvider` (Protocol). Providers: `MockProvider` (log dev, default/fallback) e `WebhookProvider`
**vendor-neutro** — `POST {kind,address,url,tenant_id}` a um endpoint HTTP que o tenant configura (o gateway
SMS/e-mail dele fica atrás; nenhum SDK de vendor hardcoded = no-lock-in). O roteador resolve o config
`survey.link_delivery` por tenant (config-api HTTP, cacheado) e escolhe o provider por `kind`
(`routes[kind]` → `default_provider` → `mock`); provider desconhecido ou `webhook.url` ausente → mock (a
entrega nunca quebra o fluxo). `deliver()`/`create()` passam `tenant_id`.

**Split config/segredo (invariantes):** o não-secreto (`default_provider`, `routes`, `webhook.url`) vem do
config-api (namespace `survey`, key `link_delivery` — seed default adicionado); o segredo (token de auth do
webhook) fica **só em env** (`PLUGHUB_SURVEY_LINK_WEBHOOK_TOKEN`).

**main.py:** constrói o delivery configurado (`config_api_url`) e invalida o cache no `config.changed(survey)`
(namespace novo no consumer, ao lado de `webchat`).

**Testes:** `test_survey_link_delivery.py` (10) — `WebhookProvider` (corpo do POST + header de auth + 2xx/
http_error/transport_error) e o roteador (routing por kind, default_provider, fallback sem url, cache +
invalidação, mock dev-log off). Tudo offline (httpx fake + config injetado).

**Pendente (sem código):** o tenant setar `webhook.url` + token. UI dedicada p/ `link_delivery` e um
`SmtpProvider` nativo de e-mail = follow-ups.

---

## Skill deploy — plumbing runtime do config_json → `$.config.*` (fatia 1, 2026-07-08)

Fecha a cadeia `interface_schema → PoolSkillSlot.config_json → $.config.* → runtime`: agora o `config_json` do
slot chega ao flow em execução. Com a fatia 2 (declaração + UI) já entregue, isto torna **um skill parametrizado
por deploy** possível — o survey deixa de exigir um skill-flow por DialogForm.

**orchestrator-bridge:** `get_pool_current_flow` (que já lia o slot `current` para o snapshot do flow) passa a
capturar o `config_json` do MESMO slot num cache por pool (`_pool_config_cache`), invalidado em lockstep com
`_pool_flow_cache` no `registry.changed(pool)`. `activate_native_agent` injeta esse config no payload do
`/execute` como `config` (só quando não-vazio; fallback skill.flow/YAML = sem config). Os call sites de fila e
webhook-resume passaram a encaminhar `pool_id` para o config valer também nesses caminhos.

**skill-flow-service:** nenhuma mudança — o `/execute` já aceitava `config` e o repassava a `engine.run` →
`interpolate` expõe `$.config.*` (§17.3-1, dialog primitive; já estava pronto = "Peça 1b").

**survey:** `skill_survey_multi_v1.yaml` trocou os 2 literais `form_id: "dialog_survey_multi_v1"` por
`$.config.form_id` (steps `carregar_form` e `gravar`). **Passa a exigir deploy por slot** com
`config_json.form_id` preenchido (set-next → promote na UI de Flow › Deploy) — sem isso o `form_get` falha, que é
o contrato correto de um skill parametrizado.

**Testes:** `test_config_params_plumbing.py` (bridge) — captura do config_json no slot (2) + injeção no payload
do `/execute` (2, incl. omissão quando vazio).

---

## Skill deploy — parâmetros de config declarativos + combos de sistema na UI (fatia 2, 2026-07-08)

Destrava "um skill parametrizado por deploy" em vez de "um skill por variação". A parametrização já existia
meio-caminho (`PoolSkillSlot.config_json` + `$.config.*` no engine); faltava o skill **declarar** quais campos
precisa e a UI de deploy montar o formulário a partir disso. Esta fatia entrega a declaração + a UI; o plumbing
runtime (bridge/worker lerem o slot e injetarem `config` no `/execute`) é a fatia 1, ainda pendente.

**Contrato (`@plughub/schemas`):** novo `SkillConfigParamSchema` (`key` [ident. inglês] `/type` [string|number|
boolean] `/label/description/required/default/source/options/min/max`) + campo `config_params?: SkillConfigParam[]`
no `SkillSchema`. Decisão: `source` é **`z.string()` aberto** — o schema não valida contra um conjunto fechado;
adicionar origem nova é mudança só de UI (forward-compat). Export + 5 testes (`skill-config-params.test.ts`).

**agent-registry:** coluna `config_params Json?` no `Skill` (Prisma; `bootstrap-db` aplica aditivo no restart),
passthrough no create/update de `skills.ts` (`CreateSkillSchema = SkillSchema` valida sozinho; `_formatSkill`
devolve via `...rest`). RegistrySyncer encaminha `config_params` do YAML (whitelist, ao lado de `delegation_input`).

**platform-ui (`AgentFlowDeployPage`):** `ConfigForm` ganhou o caminho canônico `config_params` (novo
`ConfigParamsForm`) mantendo o legado `interface.properties` como fallback. Para cada param: `source` conhecido +
opções carregadas → **combo**; `options` estáticos → select; senão input por `type`; **source desconhecido degrada
p/ texto** (silencioso, por decisão — tratar seria falso-positivo com UI defasada). O parent busca dialogforms e
monta `sourceOptions` (`dialogforms`/`pools`/`skills`) — o **único** lugar que interpreta `source`; o engine só vê
`$.config.<key>` literal. Defaults e "copy from current" passam a usar as chaves de `config_params`.

**Demo:** `skill_survey_multi_v1.yaml` ganhou `config_params: [form_id, source=dialogforms]` **declaração-only**
(o combo aparece no deploy e grava em `config_json`, mas os steps seguem com `form_id` literal até a fatia 1).

Typecheck: schemas 165 ✓, platform-ui ✓, agent-registry ✓ (após `prisma generate`; 4 erros restantes são
pré-existentes em `redis.ts`/`operational.ts`, fora do escopo). Fatia 1 (plumbing bridge→slot) registrada em TODO.

---

## Survey — resíduos: ask_when por-bloco, composite/health score, entrega plugável (2026-07-08)

Fecha três resíduos da frente de survey.

**ask_when — guarda por-bloco + validação na UI:** `AskWhenRow` refatorado p/ `(guard, onSet)`, reusado num
controle **por-bloco** no `BlockCard` ("only ask this block if…") que **fan-out** a guarda a todos os nós do
bloco (deriva a guarda comum na carga; some quando os nós divergem) — o caso "seção de follow-up inteira
condicional" sem repetir por pergunta. `save()` valida **forward-reference** (`field` deve ser `output_key`
anterior) e bloqueia com mensagem. Corrigida a chave i18n `err.instrumentType` que faltava no `en`.

**Composite / health score:** `DialogForm.composite? { metric }` (`@plughub/schemas` + tipo local +
`FormUpsert` da dialog-api). `composeSurveySignals` faz, após os sinais por dimension, um **roll-up ponderado**
das dimensions (cada uma normalizada pela sua escala, peso = `DialogDimension.weight`) num sinal extra na escala
**0–100**. Editor: toggle "Health score" + métrica no metadata + campo de peso por dimension no cabeçalho do
instrumento (quando ligado). Opcional/aditivo — ausente = dimensions puramente paralelas.

**Entrega do link web — camada plugável:** `SurveyLinkDelivery` (channel-gateway) com impl **mock** que loga o
link (`[SURVEY-LINK-DEV]`, gated por `PLUGHUB_SURVEY_LINK_DEV_LOG`) e um ponto de extensão pro provider real
(SMS/e-mail — trilha à parte, mesmo item 1 do OTP). `SurveyWebService.deliver(token, kind, address)` +
`base_url` p/ o link absoluto; `create()` entrega opcionalmente (`deliver_kind`/`deliver_address`), wirado no
endpoint `/v1/survey/web/create`. Desacoplado do create → o gatilho outbound (§19) reenvia.

---

## Skip-logic condicional em DialogForm — guarda `ask_when` (2026-07-08)

Perguntas de follow-up condicionais sem quebrar o invariante "form = conteúdo linear, controle é do skill" — ver
`docs/adr/adr-dialog-conditional-skip-logic.md`. **Guarda declarativa** `ask_when` num nó (não comando imperativo
"test on"): o runner permanece linear e apenas **pula** o nó cuja guarda for falsa. **Validado ao vivo** no
webchat: `q_motivo` (`ask_when: atendimento < 3`) é perguntado quando atendimento=2 e **pulado** quando
atendimento=5; pulada = ausente em `answers` = NA na composição (`signals=2` nos dois casos).

**Schema (`@plughub/schemas/dialog.ts`):** `AskWhen { field, op(lt/lte/gt/gte/eq/ne/in), value }`; `ask_when?`
em statement/question. `evaluateAskWhen(guard, answers)` puro (ausente ⇒ skip) — fonte única da semântica.
`askWhenForwardRefErrors(form)` valida referência **só-para-trás** por `output_key`. Testes vitest (9).

**Runtime (chat):** `buildRender` (mcp-server) inclui `ask_when` em `render.questions`; o step `loop`
(skill-flow-engine) avalia `evaluateAskWhen` contra o acumulador e **avança sobre itens falsos** (não expõe nem
coleta). Item pulado nunca vira sinal.

**Veículo web (`survey_web.py`):** a página `/survey/{token}` embute um avaliador JS que **espelha** o
`evaluateAskWhen` e mostra/esconde perguntas reativamente ao responder; pergunta escondida tem a resposta
limpa (NA no submit).

**Editor (`DialogFormsPage`):** linha "only ask if [pergunta anterior ▾] [op ▾] [valor]" por nó, oferecendo só
`output_keys` **anteriores** (backward-only); `in` aceita lista separada por vírgula. i18n en/pt-BR.

**A linha:** guarda no form = *qual conteúdo mostrar*; agir (delegar/escalar/tool) segue no `choice`/`escalate`
do skill-flow. Retrocompatível: `ask_when` ausente = comportamento atual.

---

## Editor de dialog-forms por blocos — reforma completa (2026-07-07)

Reforma do `/config/dialog-forms` (`DialogFormsPage.tsx`) para o modelo de **blocos**, fechando a UI de
dimension + a completude de campos (o item "Revisão do editor de diálogos" + a peça que faltava da composição
de survey). Editor + config; o runtime não muda.

**Modelo de blocos:** o form é uma sequência de blocos; cada bloco é um **instrumento pontuado**
(CSAT/NPS/CES/PMF/FCR ou personalizado) ou um **Diálogo sem nota** (falas, verbatim, OTP). A associação
pergunta→instrumento é o **aninhamento** (não há linha de pontuação por pergunta). Projeção pura
`dialog-blocks.ts` (`buildBlocks`/`flattenBlocks`): agrupa `nodes[]` contíguos por `dimension_id` na carga,
achata na gravação; contiguidade por instrumento; runtime idêntico (composição é form-wide por `dimension_id`).

**Instrumento (cabeçalho do bloco):** tipo, label i18n, escala, agregação, **interaction** e **rótulos de
âncora** por ponto da escala. As perguntas do bloco viram **só-prompt** (formato herdado); na gravação o
`interaction`+`options` são **materializados** da escala+âncoras em cada nó (o `buildBlocks` infere o
`interaction` de forms antigos). Peso **implícito-igual** com "ajustar pesos" revelando os %.

**Catálogo de instrumentos em config-api** (namespace `survey`, key `instruments`; seed CSAT/NPS/CES/PMF/FCR +
escala/agregação); o editor lê via `useNamespace` com **fallback** nos built-in; editável em
**Config → Plataforma → Surveys** (aba genérica de namespace).

**Completude (blocos de Diálogo):** `masked`, `timeout_s`, validação `pattern`/`min_length`/`max_length` (além
de numeric/min/max), `retry` (reprompt + max_attempts), `value` por opção; + `description` do form e
`default_locale` como select. Nós **colapsáveis** (chevron → editor).

**Schema:** `DialogDimension` ganhou `interaction`/`anchors` (`@plughub/schemas` + tipo local do platform-ui);
`DialogInteractionSchema` movido para antes do uso. i18n en/pt-BR (`dialogForms` + `configPlataforma`).

---

## Survey — wiring E2E da composição (loop → compose) + form composto (2026-07-07)

Fecha o encanamento da composição de nota (segue a entrega schema+runtime abaixo) e **valida ao vivo** no
webchat. O `skill_survey_multi_v1` (step `loop`) passou a gravar via compose e um form CSAT composto prova a
média ponderada ponta a ponta.

- **`answers` aceita o array do loop:** `SurveyRecordInput.answers` virou union `record | array<{output_key,
  value}>` — o formato que o step `loop` já acumula (`[{value, output_key, metric?}]`). O `survey_record`
  normaliza array→mapa (`answersToMap`, last-wins) antes de compor. 3 testes novos.
- **`skill_survey_multi_v1` grava via compose:** o step `gravar` passa `form_id` + `answers=$.pipeline_state.
  respostas` em vez de `signals[]`. Retrocompat: form sem dimensions → mesmos sinais legado.
- **`dialog-api` persiste `dimensions`:** `FormUpsert` (Pydantic) ganhou `dimensions` (opaco ao store, servido
  as-is) — sem isso o campo era descartado no upsert.
- **Seed do form composto** (`seed_dialog_survey_multi_form.sh`): `dialog_survey_multi_v1` = dimension `csat`
  (escala 1–5, `weighted_mean`) com atendimento (peso 2) + resolução (peso 1) + `nps` standalone.
- **Validação ao vivo:** atendimento=5, resolução=3, nps=10 → `survey_record` publicou **2 sinais** (`csat`≈4.33
  ponderado + `nps`=10). Deploy: rebuild mcp-server/dialog-api + re-seed + `set-next`/`promote` do pool
  `survey_multi_ia`.

**Pendente:** editor de dialog-forms com a UI de dimension (o form composto hoje entra via seed/JSON).

---

## Survey — composição de nota multi-pergunta (dimensions) — schema + runtime (2026-07-07)

Instrumentos de survey (CSAT/NPS…) passam a poder ser **compostos por várias perguntas** com média ponderada,
em vez do `capture.metric` por-pergunta. Desenho: `docs/adr/adr-survey-form-scoring-composition.md`. Esta entrega
cobre **schema + runtime** (editor com dimension e wiring dos skills de survey ficam pendentes).

**Primitivo compartilhado (`@plughub/schemas/scoring.ts`):** `ScoreScale`, `ScoreAggregation`
(`weighted_mean`|`min`) e `composeScore()` determinístico — normaliza cada item por escala, média ponderada com
**re-normalização de NA**, remapeia para a escala do grupo. Fonte única da matemática (survey_record + futuro
EvaluationForm). 7 testes vitest.

**`DialogForm` estendido (aditivo, retrocompat):** `DialogDimension` (instrumento: `dimension_id`, `scale`,
`aggregation`, `weight?` reservado p/ composite futuro); `DialogCapture` ganhou `dimension_id?` + `weight?`
(mantém `metric?` legado e `value?`); `DialogForm.dimensions[]` (default `[]`). `capture.metric` legado = dimension
1-item.

**Runtime — `survey_record` compõe (ADR §D9):** `SurveyRecordInput` aceita `form_id` + `answers` (respostas
cruas por `output_key`) além de `signals[]`; o tool busca o `DialogForm` na dialog-api e compõe server-side via
`composeSurveySignals` (função pura, mcp-server): por dimension agrupa perguntas (`capture.dimension_id`), mapeia
resposta→score (`option.capture.value` ou numérico cru), `composeScore` → **um sinal por dimension** paralelo;
legado `capture.metric` → sinal single. Runner burro, YAML declarativo, analytics inalterado. 10 testes vitest.

**Docs reconciliados:** `customer-surveys.md` §17 e `CLAUDE.md` — o `survey_definition` é `DialogForm`+dimensions
na **dialog-api** (supersede a nota "evaluation-api"/`survey_form_get`).

---

## Vazamento de instância no delegate→suspend — corrigido (2026-07-07)

Fecha o follow-up de demo-infra "vazamento de instância no `portabilidade_ia`" (e generaliza p/ qualquer
`delegate`/`suspend` de agente nativo). **Sintoma:** o intake (`agente_portabilidade_intake_v1`) fazia
`delegate` ao runner OTP e suspendia; o snapshot do runner (`dialog_runner-001`) ficava
`status:busy, current_sessions:1` com **SCARD real = 0** (fantasma) e o routing enfileirava "no agents
available" no próximo `delegate`, travando o fluxo OTP no webchat. Validado ao vivo: pós-fix o
`Routed → dialog_runner-001` aloca (sem `Queued`) e o release fecha limpo (`current_sessions=0→0 state=ready`).

**Causa-raiz (duas contabilidades + dois furos de espelho):** routing-engine conta ocupância ABSOLUTA
(`SCARD({tenant}:instance:{iid}:sessions)`, fonte de verdade); o bridge mantinha um espelho RELATIVO
(`max(0, snapshot-1)`) que divergia. (A) **Gap A** — o restore do espelho era gated em `native_snapshot`
presente; quando a chave `{tenant}:instance:{iid}` (TTL 30s) expirava durante o delegate→OTP→suspend, o
restore era pulado mas o `agent_done` disparava mesmo assim (SREM → SCARD 0), deixando o espelho preso em
`busy/1`. (B) O **self-heal do bootstrap** comparava `pool active_count` (que vaza quando a conferência
compartilha `session_id`) e, pior, era **engolido pela guarda de `_write_instance`** (recusava busy→ready) e
**blindado pelo short-circuit de `pending_update`** no heartbeat — o fantasma nunca era curado. (C) O heartbeat
**reescrevia `busy` a partir do estado desejado em memória** (`_registered`), envenenado por um fantasma
pré-fix, clobberando o `ready` recém-escrito.

**Entregue (`orchestrator-bridge`):**
- **`main.py`** — helper `_release_native_instance_snapshot()` sempre aterrissa o espelho em `ready`
  (reconstrói snapshot mínimo quando a chave expirou); usado nos 3 restore blocks que liam o snapshot de TTL
  curto (`process_routed` nativo + YAML-fallback, `_handle_webhook_session_resumed`). Fecha o Gap A.
- **`instance_bootstrap.py`** — guarda de `_write_instance` **SCARD-aware** (só preserva `busy` via
  `pending_update` se `SCARD>0`; `busy` com `SCARD 0` = fantasma → sobrescreve ready); self-heal do heartbeat
  ancorado em `SCARD==0` e **movido para antes** dos short-circuits de `draining`/`pending_update`; heartbeat
  **nunca empurra `busy` do estado desejado** (normaliza ready/0 no restore-de-expirada e no caminho normal,
  corrigindo a cópia em memória → corta a oscilação heal↔clobber). `paused` nunca é auto-curado.
- **Testes** — `TestHeartbeatSelfHeal` (cura do ghost com `pending_update`; preserva busy com sessão viva) +
  `test_write_instance_overwrites_stale_busy_ghost`; ajuste do teste de guarda p/ a semântica SCARD.

**Invariante reforçada:** `SCARD({tenant}:instance:{iid}:sessions)` é a única fonte de verdade de ocupância;
o espelho do bridge e o estado desejado do bootstrap **convergem para ela**, nunca a sobrescrevem com `busy`.
**Limitação conhecida:** o self-heal libera a instância p/ roteamento NOVO mas não republica `agent_ready` no
Kafka — uma fila já enfileirada não é redrenada no instante da cura (item aberto se necessário). Doc:
[`docs/arcos/session-conference-lifecycle.md`](docs/arcos/session-conference-lifecycle.md) § delegate→suspend.

---

## Primitivo de diálogo — editor multi-locale (`/config/dialog-forms`) (2026-07-07)

O editor de DialogForms passou de single-locale (texto editado como string pura) para **multi-locale**: um
form pode carregar traduções embutidas (`LocalizedText = {locale: texto}`) editáveis pela UI. Fecha a lacuna
"forms multi-idioma só via seed/JSON". Validado: `nodes[].prompt` grava `{"pt-BR":…,"en":…}` e `locales`
carrega os dois; trocar de idioma preserva os demais.

**Entregue (`DialogFormsPage.tsx` + i18n):**
- **LocaleBar** — chips por idioma (do `locales[]`), clique seleciona o **idioma em edição**, `+ Idioma`
  adiciona, `×` remove (menos o `default_locale`, que fica marcado).
- **`setLt`** — grava o valor no idioma em edição preservando os demais; converte string↔mapa e **colapsa**
  para string pura quando só o `default_locale` resta (mantém os seeds single-locale limpos).
- **`ltToStr(t, locale, defaultLocale)`** — uma **string pura pertence só ao `default_locale`**; editando outro
  idioma o campo aparece vazio (untranslated). Corrige o bug de o texto default "reaparecer" ao limpar o campo
  de outro idioma (o colapso para string era exibido em qualquer idioma).
- **Indicador "sem tradução"** — ponto âmbar + código do idioma no cabeçalho do nó quando o texto principal
  falta no idioma em edição; save garante `default_locale ∈ locales[]`. Aplica a `text`/`prompt`/`labels`.

**Follow-up (item dedicado no TODO — "Revisão do editor de diálogos"):** passada de UX (nós colapsáveis,
agrupar validação/retry/opções, edição lado-a-lado, preview, progresso de tradução estável), campo
`retry.reprompt` no editor, e auth ABAC no write.

---

## Primitivo de diálogo — retry por formato no menu (`validation`+`retry`) (2026-07-07)

Fecha o item de Fatia 2 "`retry.max_attempts` pleno por pergunta". O reprompt acontece na **mesma
superfície** (o step `menu`), só para falha de **FORMATO** (numeric/pattern/comprimento/faixa) — nunca
semântica (código OTP correto, regra de negócio), que segue sendo controle do chamador. Validado no demo:
`abc` → reprompt; `200` (fora de 0–99) → reprompt; `15` → aceito.

**Entregue:**
- **`schemas/skill.ts`** — `MenuStep` ganhou `validation` (numeric/pattern/min/max/min_length/max_length) +
  `retry` (reprompt + `max_attempts`), cada um união **objeto | ref** (`$.`/`@ctx.`).
- **`mcp-server/tools/dialog.ts`** — `form_get`/`buildRender` expõe `validation`+`retry` na view
  single-question e em cada `questions[]` (loop); `flattenRetry` resolve o reprompt i18n → string.
- **`skill-flow-engine/steps/menu.ts`** — o BLPOP virou **loop de retry**: valida o escalar após a resposta;
  formato inválido + tentativas restantes → reenvia o `reprompt` e re-bloqueia (renova o lock a cada volta);
  esgotou `max_attempts` → `on_failure`. Só escalar (`interaction !== "form"`); timeout/desconexão/@mention
  saem direto (não são retry). `resolveObjectRef` resolve validation/retry ref|literal; `validateFormat`
  é o gate determinístico.
- **`skill_dialog_runner_v1.yaml`** + **`skill_survey_multi_v1.yaml`** — o `coletar` passa
  `validation`/`retry` do render (runner) / da pergunta atual `q_atual` (loop). Seed do survey_multi ganhou
  uma 3ª pergunta de **texto** com validação numérica 0–99 + retry (prova o reprompt).

**Nota de deploy (aprendida na verificação):** mudar `MenuStepSchema` exige rebuild de **schemas +
agent-registry + mcp-server-plughub + skill-flow-service** (`--no-cache` se a layer do schemas estiver
cacheada — o Zod do agent-registry **descarta** campos desconhecidos silenciosamente, então schema velho
= `validation:null`). Pool slotado (`PoolSkillSlot`) executa o **snapshot do slot `current`**, não o
`skill.flow` — após editar a skill é preciso **`PUT /v1/pools/:id/slots/next` + `POST …/promote`** (auth por
header **`x-service-token`**+`x-user-id`, não `Authorization: Bearer`) para re-snapshotar o que o bridge roda.

---

## Primitivo de diálogo — veículo web (`/survey/:token`) (2026-07-06)

Segundo veículo do primitivo (§9.2/§19): um link tokenizado leva a uma **página pública** que renderiza o
**mesmo `DialogForm`** (buscado do dialog-api) como `<form>` web e grava pela **mesma trilha confiável**
(`session.signals`, idêntico ao `survey_record`). Prova a tese "conteúdo agnóstico de veículo": o `DialogForm`
serve chat (runner conversacional), inline (hook), **e** página web — todos escrevendo o mesmo sinal. Validado
no demo (form CSAT+CES → página → submit → `session.signals`).

**Entregue (channel-gateway):**
- **`survey_web.py`** — `SurveyWebService` (`create`/`get`/`submit`) + `SURVEY_PAGE_HTML` (página self-contained,
  sem build). `create` congela o form publicado (snapshot, pina a versão) num token Redis; `submit` monta os
  `signals` das perguntas com `capture.metric` + valor numérico e publica `session.signals`.
- **Endpoints** (`main.py`): `POST /v1/survey/web/create`, `GET /v1/survey/web/{token}`,
  `POST /v1/survey/web/{token}/submit`, `GET /survey/{token}` (HTML). `config.py`: `dialog_api_url` +
  `kafka_topic_signals` + `survey_web_ttl_s`. Compose: `PLUGHUB_DIALOG_API_URL` no channel-gateway.

**Fora de escopo (trilha própria):** a **entrega real** do link (provedor SMS/e-mail) — o `create` devolve o
`path`; no demo abre-se manual. Verbatim (open_text) não vira signal (só numéricas).

---

## Primitivo de diálogo — step `loop` (N perguntas sequenciais) (2026-07-06)

3ª extensão de engine da Fatia 2: um step `loop` que caminha uma sub-flow (body) sobre um array, uma iteração
por elemento — habilita survey multi-pergunta **sequencial** em canal pobre (uma pergunta por turno), que o
`interaction=form` (multi-field num payload) não cobre. Validado no demo: 2 perguntas (CSAT + CES) caminhadas
em sequência → `survey_record` com os 2 sinais (`csat`, `ces`).

**Entregue:**
- **Schema** (`FlowStepSchema`): step `loop` (`over`/`item_as`/`index_as`/`body`/`collect`/`results_as`/
  `on_complete`/`max_iterations`) + tipo `LoopStep`.
- **Engine** (`steps/loop.ts`): modelado no padrão cíclico do `receive` (contador em `pipeline_state`
  `_loop_idx_{id}`). Expõe o elemento atual num **path FIXO** (`item_as`) a cada iteração — **sem índice
  variável em ref** (contorna a limitação do jsonpath-plus). Acumula `{value, output_key?, metric?}` (pareado
  do item) em `results_as`; ao esgotar o array → `on_complete`. Limpa sentinels `:__notified__` a cada volta.
- **Engine** (`engine.ts`): `_getSuccessors` reconhece `body`/`on_complete`; `validateFlow` aceita `loop` como
  ciclo guardado (o `menu` do body também guarda — bloqueia por input a cada iteração).
- **`form_get`**: `render.questions` (array por-pergunta: prompt/interaction/options/output_key/capture/
  visibility) para o loop iterar.
- **Consumidor real**: `dialog_survey_multi_v1` (seed, 2 perguntas numéricas) + `skill_survey_multi_v1`
  (`form_get → loop → survey_record`) + pool `survey_multi_ia` (webchat direto) + opção no `webchat-test.html`.

**Nota:** o loop paga a limitação do §17.2 (sem contador/índice variável) reusando o contador do `receive` e
expondo o item num path fixo — sem tocar o resolver de refs. Cobre o caso sequencial; `retry.max_attempts`
pleno por pergunta (contador de tentativas) segue como refinamento sobre o mesmo mecanismo.

---

## Primitivo de diálogo — Editor (form-builder) no platform-ui (2026-07-06)

Fecha a dívida do repo "todo campo de config é UI-editável; YAML/arquivo-only é dívida" — os DialogForms
deixam de nascer só por script de seed. Página em `/config/dialog-forms` (grupo Configuração, ABAC
`config.platform`), consumindo o `dialog-api` via proxy `/v1/dialog`. Validado no demo: lista os forms
semeados, cria/edita nós (statements + questions com prompt/interaction/options/output_key/capture/validation/
visibilidade), publica, e o form editado é servido pelo `form_get`/runner.

**Entregue (platform-ui):**
- **Proxy `/v1/dialog` → dialog-api:3760** no `Dockerfile` (nginx, antes do catch-all `^/v1`) + `vite.config.ts` (dev).
- **`src/api/dialog-hooks.ts`** — `useDialogForms`/`getDialogForm`/`createDialogForm`/`updateDialogForm`/
  `publishDialogForm` (fetch custom, header `X-Tenant-ID`); tipos `DialogForm` locais (decoplado de `@plughub/schemas`).
- **`src/modules/dialog-forms/DialogFormsPage.tsx`** — lista + editor de nós + Salvar rascunho/Publicar.
- Rota `config/dialog-forms`; nav em Configuração (ícone MessageSquare); namespace i18n `dialogForms` (en+pt-BR) +
  `nav.dialogForms` no shell.

**Escopo MVP (deliberado):** edição em **locale único** (`LocalizedText` como string; multi-locale = follow-up);
sem preview embutido; visibilidade via dropdown (Todos / Só o cliente / Só agentes). Writes abertos no demo
(dialog-api `admin_token` vazio); auth/ABAC no write da API = follow-up.

---

## Primitivo de diálogo — Fatia 2b: NPS por botões + interação/visibilidade dinâmicas (2026-07-06)

Estende o primitivo para o NPS **ativo** (hook `on_contact_end`): botões 0-10 customer-only. Validado no demo
(NPS de fim-de-contato + survey reconnect + OTP por simetria).

**Entregue:**
- **Engine §17.4 — interação/visibilidade dinâmicas:** `MenuStepSchema.interaction` e `.visibility` viram
  união `enum|array | ref` (`$.`/`@ctx.`); `menu.ts` resolve o ref (`resolveDynamicValue`) antes de renderizar.
  Só strings com prefixo `$.`/`@ctx.` são ref; literais passam direto.
- **form_get render nativo (single-question):** `buildRender` expõe `interaction`/`prompt`/`options`/
  `output_key`/`visibility` da 1ª pergunta (statements dobrados no prompt), além do render `form`/`fields`
  legado. O runner passa a usar a **interação NATIVA** da pergunta (text→input, button/list→botões — o webchat
  já renderiza), alinhado ao §17.4 ("1 pergunta = 1 menu com options dinâmicas").
- **Contrato uniforme `{value}`:** o runner devolve `payload = { value: <escalar coletado> }` (antes era o mapa
  `{output_key: valor}` de `interaction=form`). Consumidores leem `$.pipeline_state.<delegate>.value` — OTP
  (`agente_portabilidade_intake_v1`) e survey (`agente_survey_reconnect_v1`) atualizados de `.code`/`.nps` → `.value`.
- **`dialog_nps_buttons`** (seed) — NPS 0-10 `interaction=list`, `visibility: [@ctx.session.customer_participant_id]`.

**Achado de arquitetura (importante):** **hooks de `on_contact_end` NÃO podem delegar ao runner.** Delegar
suspende o hook agent, e o bridge trata `suspended` como hook concluído → **fecha o contato** antes de o runner
renderizar (o sinal de session-closing desbloqueia o menu do runner na hora). Validado nos logs. Portanto o
NPS ativo (`agente_nps_v1`) consome o primitivo de **conteúdo INLINE** (`form_get` + menu dinâmico com
interação/visibilidade do form), **sem** delegate/suspend — bloqueia e segura o posatt como o NPS antigo. O
**runner-especialista** serve chamadores que **podem suspender** (OTP intake, survey reconnect); hooks usam inline.
Ambos compartilham o `DialogForm` + `form_get` + o menu dinâmico.

**Follow-ups registrados (demo-infra + refinamentos, NÃO o primitivo):**
- **Vazamento de instância** no `portabilidade_ia`: o delegate-wait do OTP deixa uma sessão fantasma no tracking
  do bridge → a instância nasce/persiste `busy/current_sessions=1` mesmo com ocupância (SCARD) 0; resets no Redis
  não seguram (o bridge reescreve o snapshot). Precisa de reset do estado in-memory do bridge / fix do sync
  reconcile×routing-registry. Bloqueou o teste e2e do OTP (validado por simetria via survey).
- **Timeout dinâmico:** o runner usa `timeout_s` estático (180); o `timeout_s` do form (ex.: NPS 30) não é lido.
  Deferido (mesma extensão-padrão de `$.`/`@ctx.` ref).

---

## Primitivo de diálogo — Fatia 2 (parcial): adoção pelo survey (2º consumidor) (2026-07-06)

O survey passa a ser o **2º consumidor** do primitivo de diálogo (o OTP foi o 1º), validando a generalidade:
o mesmo `dialog_runner` + `form_get` + `DialogForm` que servem o OTP agora servem a coleta de NPS. Validado
no demo (veículo conversacional): reconexão → runner renderiza o form de NPS → cliente responde → o survey
workflow grava via `survey_record`.

**Entregue:**
- **`dialog_nps_v1`** (DialogForm, seed `infra/test/seed_dialog_nps_form.sh`) — agradecimento (statement) +
  pergunta NPS (`interaction: text`, `output_key: nps`, `capture.metric: nps`, `validation {numeric,0..10}`).
- **`agente_survey_reconnect_v1`** — `retomar_survey` passa a delegar ao **`dialog_runner`** (context
  `dialog_form_id: dialog_nps_v1`) em vez do collector bespoke `survey_collector_ia`; novo step `resumir_survey`
  retoma o survey workflow (`skill_survey_v1`) com `{nps}` cru → `gravar_pesquisa` faz o `survey_record`
  (domínio). O collector segue **só** para o *defer* (cria o pending), inalterado.

**Decisão de arquitetura:** delegate de **nível único** (reconnect→runner). Aninhar o runner dentro do collector
foi **rejeitado** — colidiria o `session.delegate_resume_token` (campo session-scoped) com o duplo-resume do
collector (anti-padrão "single-source" do CLAUDE.md). O reconnect faz o resume do survey workflow explicitamente
(`workflow_resume` com o `pendencia.resume_token`), então o runner só resume seu delegador imediato.

**Escopo v1 (deliberado):** a pergunta de NPS usa **campo de texto** (o runner força `interaction=form`). NPS
por **botões/lista** (choice) exigiria campos `choice` com opções no render+adapter — deferido. O NPS **ativo**
do demo (`skill_nps_v1`, hook `on_contact_end`, in-conference, customer-only, botões) **não** foi migrado: exige
**visibilidade dinâmica** (customer-only) + **campos choice** no runner — frente maior, registrada como pendente.

**Veículo web (link SMS/e-mail → `/survey/:token`):** confirmado como **frente à parte** (o "veículo link web"
do §9.2 + outbound do §19) — o mesmo `DialogForm` seria renderizado como página pública em vez de chat (conteúdo
agnóstico de veículo). Não construído.

**Deferido (Fatia 2 restante):** loop no engine (N perguntas sequenciais + retry pleno); `channel_policy: elect`;
editor (form-builder); plumbing `$.config` bridge→slot; NPS por choice/visibilidade dinâmica; veículo web.

---

## Primitivo de diálogo genérico + dialog-runner — Fatia 1 (2026-07-06)

Primeira fatia do primitivo de diálogo scriptado compartilhado por survey + OTP (ADR
`docs/adr/adr-otp-workflow-and-dialog-primitive.md`; desenho `docs/product/dialog-primitive-and-runner-design.md`).
Validado ponta-a-ponta no demo: o intake de portabilidade delega a coleta do código de OTP a um dialog-runner
Tier-3 que renderiza um `DialogForm` versionado e devolve o input cru — o código **gerado** nunca passa pelo
runner (costura de segredo intacta; `OtpService`/`otp_verify` seguem no intake).

**Entregue:**
- **`@plughub/schemas/dialog.ts`** — `DialogFormSchema` (script linear de nodes `statement`/`question`,
  versionado draft/published, i18n `LocalizedText` embutido, `capture`/`validation` declarativos,
  `resolveLocalizedText`). Sem controle no JSON (branching é do skill — invariante das 4 costuras).
- **Engine §17.3-1 (`$.config.*`)** — `StepContext.config` exposto no `evalContext` (`interpolate.ts`),
  threading `run→_execute→_buildContext`, passthrough `config` no `skill-flow-service /execute`.
- **Engine §17.3-2 (menu dinâmico)** — `MenuStepSchema.options/fields` viram união `array | string(ref)`;
  `menu.ts` resolve o ref via `resolveInputValue` (`resolveMenuArray`) antes de montar o payload.
- **`packages/dialog-api`** (novo, porta 3760, schema `dialog.forms`) — store fino versionado (CRUD +
  publish), espelhando o versionamento da `EvaluationForm`. Serviço no `docker-compose.demo.yml`.
- **`form_get`** (tool MCP fina, `mcp-server-plughub/tools/dialog.ts`) — resolve o form no dialog-api e
  normaliza num bloco `render` single-turn (`menu_prompt` das leading statements, `fields` por pergunta,
  `statement_after`, `captures` domínio-cego) que o runner consome direto.
- **`skill_dialog_runner_v1`** (pool `dialog_runner`) — Tier-3 genérico: `form_get → menu(interaction=form,
  fields dinâmicos) → workflow_resume(payload=answers) → complete`. Devolve cru; nunca verifica/registra.
- **OTP (consumidor de validação)** — `agente_portabilidade_intake_v1` substitui o menu inline de código por
  `delegate` ao `dialog_runner` (`context.dialog_form_id=dialog_otp_possession`); lê
  `$.pipeline_state.coletar_codigo_dialog.code` no `otp_verify`. Seed `infra/test/seed_dialog_otp_form.sh`.

**Decisão as-built:** binding do `form_id` ao runner = **contexto de delegate** (`@ctx.session.dialog_form_id`,
padrão `delegate-workflow-io`), não `$.config`. O hook `$.config.*` foi construído e o passthrough do launcher
está pronto, mas o plumbing bridge→`PoolSkillSlot.config_json` (deploy-por-slot do survey) fica p/ Fatia 2.
**Gotcha operacional:** o `MenuStepSchema` virou união → o `agent-registry` (valida o skill no PUT do
RegistrySyncer) precisou de rebuild junto com `mcp-server-plughub`/`skill-flow-service`; sem isso o
`skill_dialog_runner_v1` era rejeitado (422 `fields: expected array`). Pool migrado a slot exige `set-next`+`promote`.

**Deferido (Fatia 2):** retry de formato (contador no engine) + loop sobre N perguntas; `channel_policy: elect`;
editor no platform-ui; adoção pelo survey. Ver `docs/product/dialog-primitive-and-runner-design.md` §6.1.

---

## Identity Resolver (nível b) — Fase B: identidade progressiva + posse de canal (OTP) + gate seguro (2026-07-04)

Capacidade de identificação em três fases (commits separados). Fecha o cross-canal do Thread A com
segurança: âncoras se acumulam progressivamente, o OTP prova posse do canal, e a retomada cross-canal
sensível exige posse provada. **Merge e external_refs seguem adiados (Fase C / quando houver CRM).**
ADR: `docs/adr/adr-identity-channel-possession.md` (plataforma = autoridade de posse de canal, emenda ao
princípio 7/§4.4).

**Fase 1 — progressiva + `verification_class`:** `resolve_or_provision`, num hit não-ambíguo, anexa as
âncoras que eram *miss* ao vencedor como `claimed` → reconectar com phone+email indexa o email; depois o
email sozinho resolve o mesmo cliente. `verification_class` (`claimed|possessed`) no índice Redis (valor
JSON `{cid,vc}`, leitor tolerante — string legada = claimed) e no PG (`customer_secondary_keys`, coluna
via `ALTER IF NOT EXISTS`). Confiança de desambiguação = `f(kind, classe)` (`anchor_rank_score`: qualquer
possessed supera qualquer claimed). `attach_anchor` primitivo (nunca rebaixa possessed); `CustomerRef`/
`resolve_customer` expõem a classe.

**Fase 2 — OTP como serviço + enriquecimento:** `identity/otp.py` `OtpService` (agnóstico de identidade —
prova posse de `(kind,value)`): `challenge` (código 6 díg só-hash, TTL 300s, rate-limit anti-enumeração) +
`verify` (tentativas, one-shot). Entrega **mockada** gated por `PLUGHUB_OTP_DEV_RETURN_CODE` (código no log
WARNING + `dev_code`; **nunca** em produção). `WebhookAdapter.otp_verify` OK → `attach_anchor(possessed,
durable)` — **única via para possessed**. `customer_attach_key` só escreve `claimed` (invariante
possessed⟺verificado). `IdentityIndex.update_attributes` (merge JSONB em `customers.attributes`). Tools MCP
`otp_challenge`/`otp_verify`/`customer_attach_key`/`customer_update_attributes` + 4 endpoints.

**Fase 3 — gate seguro + demo:** **default de plataforma** — retomada cross-canal de `customer_resumable`
exige âncora `possessed`. `pending_workflow_get` (anchors) não devolve `resume_token`/contexto quando a
resolução é só `claimed`; devolve `verification_required` **sem revelar se há pendência** (anti-enumeração).
Intake (`agente_portabilidade_intake_v1`): `avaliar_resolucao` → `oferecer_verificacao` (menu Sim/Não,
proativo com recusa, wording neutro) → `otp_challenge` → `pedir_codigo` → `otp_verify` → re-consulta
`pending_workflow_get` (agora possessed) → `avaliar_pendencia`. Recusa/falha → atendimento novo.

**Testes:** `test_identity_index.py` (+progressiva, classe, tolerante, no-downgrade, update_attributes),
`test_otp.py` (challenge/verify/tentativas/rate-limit/dev-gate/código-nunca-claro + verify→possessed). Suíte
channel-gateway 38 verde. **Deploy:** channel-gateway + mcp-server-plughub, restart orchestrator-bridge
(re-sync intake YAML), re-promote `portabilidade_ia`. **Mudança de UX:** o cross-canal do Thread A agora
inclui o passo de OTP (o ponto da feature).

---

## Identity Resolver (nível b) — Fase B slice: reconexão-oferta por identidade (cross-canal) (2026-07-03)

Primeira fatia da Fase B: a retomada deixa de depender do `contact_identifier` exato (intra-canal de fato)
e passa a resolver a pendência pelo **customer_id nativo** via anchors — reconecta por qualquer âncora que
resolva ao mesmo cliente. Consome a `pending_by_customer` que o Slice 3 já cria (gated em `customer_resumable`).

- **channel-gateway `find_pending_by_customer`** — inclui `policy` por pendência e **achata a primeira**
  pendência no topo (`found`/`resume_token`/`pool`/`context`/`policy`) → shape compatível com o legado
  `get_pending_workflow`, então o intake lê `pendencia.resume_token`/`.context.*`/`.policy` sem indexar
  array em JSONPath. `pendings[]` + `customer_id` seguem para multi-pendência futura.
- **Dual-write `context_preview` mascarado** (`handle_delegate_conference` + `handle_delegate`) — helper
  `_pending_context_preview`: `operadora_destino` em claro (não-secreto), `numero_atual` mascarado nos
  últimos 4 (`***4321`). É o que a oferta cross-canal exibe (spec §10 — preview mascarado).
- **Intake `agente_portabilidade_intake_v1`** — `verificar_pendencia` usa `anchors:[{phone: numero_atual}]`
  (cross-canal). Novo `choice avaliar_politica_retomada`: `policy=auto`→`retomar_processo` direto;
  `offer` (default anti-enumeração)→`menu_continuidade`. `retomar_processo`/`cancelar_processo` levam
  `resume_origin: "identity"`.
- **`resume_origin=identity` fim-a-fim** — tool MCP `workflow_resume` ganha `resume_origin` (loose+validado:
  só `same_channel|token|identity` viaja; ausente/inválido→omitido→`token`, para não quebrar o caminho normal
  de confirmação). `WebhookResumeRequest`/endpoint `webhook_resume` repassam ao `handle_resume` (param do
  Slice 3). O intake grava `session.resume_origin` no specialist (via delegate context); o
  `agente_confirmacao_portabilidade_v1` lê `@ctx.session.resume_origin` em `confirmar_e_resumir`/
  `retornar_sessao_pai`. `same_channel` (continuidade intra-canal, platform-level) fica p/ depois.
- **Testes** — `test_webhook_adapter.py` (+6: `_pending_context_preview` mascara/omite; `find_pending_by_customer`
  achata+policy+context / vazio sem flatten; `handle_resume` origin=identity). Suíte channel-gateway verde.
- **Validado no demo** — reconexão via webchat (número + contato) acha a pendência cross-canal, mostra a
  oferta com número mascarado (`***6666`) e o menu (offer). Deploy: rebuild channel-gateway + mcp-server-plughub,
  restart orchestrator-bridge (re-sync YAML), promote `portabilidade_ia` + `portabilidade_confirmacao`.
- **Fora do slice (resto da Fase B):** identidade progressiva + `external_refs` + merge + wiring do step CRM
  `resolve`; `resume_origin=same_channel` (resolvedor de inbound platform-level); `persistCollect` no
  skill-flow-worker legado.

---

## Identity Resolver (nível b) — Fase A · Slice 3: `customer_resumable`/`resume_policy` + `resume_origin` (2026-07-03)

Declara a política de retomada channel-abstract na **delegação** (spec §6) e gata a indexação cross-canal.

- **`schemas/src/skill.ts`** — campos opcionais `customer_resumable` (`z.boolean().default(false)`) e
  `resume_policy` (`z.enum(["offer","auto"]).default("offer")`) no step **`delegate`** (inline no
  `FlowStepSchema`) e no **`CollectStepSchema`**. Defaults preservam 100% do comportamento legado.
- **Propagação (risco "engine dropa campos" tratado):** os executores montam o objeto do callback
  **explicitamente**, então os campos foram adicionados em (a) `steps/delegate.ts` e `steps/collect.ts`
  (call sites — o ponto de drop), (b) tipos `persistDelegate`/`persistCollect` em `executor.ts` + `engine.ts`,
  (c) `persistDelegateFn` do `e2e-tests/services/skill-flow-service`. O wiring genérico do engine já faz
  spread `...params` tipado por `StepContext` — não dropa por si.
- **channel-gateway** — `WebhookDelegate{,Conference}Request` + rotas passam os campos;
  `handle_delegate` e `handle_delegate_conference` agora **gatam a dual-write `pending_by_customer`
  (+`promote_to_durable`) em `customer_resumable`** (antes incondicional quando identity on); `resume_policy`
  viaja no `PendingEntry.policy`. `handle_delegate_conference` não tinha dual-write — adicionada, gated.
- **`session_resumed` ganha `resume_origin`** (`same_channel|token|identity`) no payload do stream e no evento
  `conversations.inbound`. `handle_resume` recebe `resume_origin: str = "token"`; **só `token` é wirado**
  (endpoint de resume + timeout scanner). `same_channel`/`identity` ficam para o caminho de reconexão-oferta
  da Fase B (§7). Routing-engine `ConversationInboundEvent` usa `extra="ignore"` → campo novo é seguro.
- **Guardrail de perfil** = colocação no schema: os campos só existem em `delegate`/`collect`; o
  `discriminatedUnion` do Zod descarta se colados num `suspend` (spec §1.2).
- **Demo** — `skill_portabilidade_demo_v1` (`notificar_e_confirmar`, o delegate que suspende aguardando o
  retorno do cliente) seta `customer_resumable: true` + `resume_policy: offer` p/ manter a retomada
  cross-canal sob o gate.
- **Testes (verdes):** `schemas/src/skill.slice3.test.ts` (6 — defaults, enum inválido, guardrail suspend);
  `skill-flow-engine/src/steps/delegate.slice3.test.ts` (2 — propagação ao callback);
  `test_webhook_adapter.py` (+4 — `resume_origin` default/explícito, dual-write gated skip/carry-policy).
  Também corrigidas 2 asserts stale pré-existentes (`test_handle_resume_*` esperavam igualdade exata do
  payload, quebrada desde que a Fase E.3 injeta `source:"external"`). Suítes: 26 / 144 / 145 passando.
- **Fora do slice:** wiring `persistCollect` no `skill-flow-worker` legado (collect cross-canal não está no
  demo — plumbing schema/engine pronto); `task.target {skill_id}→{pool}` (delegate-spec §7, Fase B).

---

## Identity Resolver (nível b) — Fase B slice: wiring do intake escreve `caller.customer_id` nativo (2026-07-03)

Fecha o gargalo prático da Fase A: o `agente_portabilidade_intake_v1` agora **resolve/provisiona o
`customer_id` nativo** e o grava em `caller.customer_id`, dando ao bridge (Slice 4) o que propagar para
`sessions.customer_id`. Sem isto, o Slice 4 só tinha o fallback `contact_id` no demo (não havia CRM que
escrevesse o nativo).

- **`skill-flow-engine/skills/agente_portabilidade_intake_v1.yaml`** — após `coletar_contato` e **antes**
  de qualquer ramificação (`verificar_pendencia`), inseridos: `detectar_kind_contato` (choice `contains
  "@"` → email/phone), `resolver_identidade_email|phone` (invoke `customer_resolve`, âncoras
  `numero_atual:phone` + `contact_identifier:phone|email`, `provision:true`, `output_as: identidade`) e
  `escrever_caller_id` (invoke `context_set` `caller.customer_id = $.pipeline_state.identidade.customer_id`,
  confidence 1.0, agents_only). Ambas as branches de resolve caem para `verificar_pendencia` no `on_failure`
  → resolver nunca bloqueia o intake. Posição pré-ramificação garante o carimbo em todos os caminhos
  (inclusive continuar/cancelar pendência).
- **Mecânica reusada:** `context_set` grava a ContextEntry `{value,confidence,source,visibility,updated_at}`
  em `{tenant}:ctx:{session}` — exatamente o que `_resolve_close_customer_id` (bridge) lê no fechamento.
  `customer_resolve` já existia na imagem do mcp-server (Slice 1); `resolveInputValue` resolve o array
  `anchors` aninhado; o engine desembrulha o envelope MCP em objeto (`identidade.customer_id`).
- **Deploy (nota operacional):** editar YAML de skill + restart **não** basta para pool migrado a
  `PoolSkillSlot` — o bridge executa o snapshot do slot `current`. Publicação exigiu `set-next` +
  `promote` (`/v1/pools/portabilidade_ia/slots/{next}` + `/promote`, header `x-service-token`). O
  RegistrySyncer publicou `skill.flow` mas não re-snapshotou o slot.

**Validação (demo, webchat):** dois intakes com o mesmo `numero_atual` (`11999999999`) fecharam ambos sob
o **mesmo** `cus_f384c8f1e99b495cb1a0b7ce` em `sessions.customer_id` (antes: `cliente-demo-…`) — prova o
carimbo nativo (Slice 4 e2e) e a unificação cross-contato (base de H1/H2/H3) num só passo.

Docs: `docs/product/identity-resolver-fase-a-plano.md` (§2 Slice 4 + Falta), `identity-resolver-nivel-b-spec.md`.

---

## Bugfix — sessão presa em `active` no customer-disconnect com NPS `nps_on_disconnect=skip` (2026-07-03)

Corrige assimetria entre os caminhos `agent_done` e `client_disconnect` no fechamento da **camada de
contato**. Quando o **cliente** encerrava e o único hook de cliente do pool era o NPS com
`nps_on_disconnect=skip`, a entrada era pulada dentro de `fire_pool_hooks`, `posatt:customer_active`
nunca era incrementado e **`_close_contact_layer()` nunca era chamado** → a sessão ficava `active` por
até 180s, até o `_hook_timeout_guard` force-close (degradado) — o contador `hook_pending:on_contact_end`
havia sido armado de forma órfã por `len(hook_list)`. O caminho `agent_done` já fechava correto (guard
`_has_customer_hooks`). Segmentos fechavam (via `_destroy_conference`), só a camada de contato não.

- **`orchestrator-bridge/main.py` · `fire_pool_hooks`** — `close_origin` lido **uma vez**
  (`_close_origin_val`); `hook_pending:{hook_type}` passa a ser dimensionado pelas entradas que
  **realmente serão disparadas** (`_entry_will_dispatch`), e não é armado quando esse total é 0. Skip
  por-entrada usa o mesmo valor (sem contador órfão). Byte-equivalente no `agent_done`.
- **`orchestrator-bridge/main.py` · `process_contact_event` (client_disconnect)** — computa
  `_cs_customer_will_run`; se nenhum hook de cliente vai rodar, fecha a camada de contato
  **imediatamente** (`_close_contact_layer`, ou `_trigger_contact_close` quando também não há segmento
  agent-side) — espelha o guard do `agent_done`. Disparo de `on_contact_end` + guard passam a ser gated
  por `_cs_customer_will_run`. Decisão de **transporte**, não de negócio; idempotência preservada (NX).

**Segunda causa (analytics-api) — reabertura pela linha de `sessions` do routing do hook.** Após o fix do
bridge, os logs mostraram o `_close_contact_layer` fechando certo, mas a sessão voltava a `active`. A tabela
`analytics.sessions` é `ReplacingMergeTree()` sem coluna de versão (last-inserted-wins). `parse_routed`/
`parse_queued` escreviam uma linha de `sessions` (pool + `closed_at=NULL`) para **todo** routing, inclusive o
do agente de hook (wrap-up). Com o fechamento agora imediato, o `contact_closed` sai ANTES do wrap-up rotear
→ a linha `routed(wrapup_ia, closed_at=NULL)` entra depois do close → reabre a sessão (active, pool=hook).

- **`analytics-api/models.py`** — `parse_routed` e `parse_queued` **pulam a linha de `sessions`** quando
  `result.conference_id` está presente. Routing de hook/especialista é fato de segmento (rastreado em
  `conversations.participants → segments`), não do contato; só o primário (sem `conference_id`) escreve a
  linha de `sessions`. Corrige a reabertura e o pool exibido errado. Testes: `test_consumer.py`
  (`test_conference_routing_skips_sessions_row`, `test_primary_routing_still_writes_sessions_row`,
  `test_conference_queued_skips_sessions_row`).

Doc: `docs/guias/conference-mechanics.md` § Histórico — Mudança 24 (bridge + analytics-api).

---

## Resolvedor de Identidade — Fase A · Slice 4 (ponte ao histórico) + Fase A completa (2026-07-02)

Conserta na raiz o erro `contact_id`-como-`customer_id`: o `sessions.customer_id` (analytics) passa a
refletir o **customer_id nativo** resolvido, reconectando o arco H (H1/H2/H3) à identidade real. Fecha a
**Fase A** do Resolvedor de Identidade (Slices 1–4).

- **`orchestrator-bridge/main.py`** — helper `_resolve_close_customer_id(redis, tenant, session, fallback)`:
  lê `caller.customer_id` (ContextEntry) do ctx hash `{t}:ctx:{session}` e devolve o nativo, ou o `fallback`
  (contact_id efêmero) quando ausente/inválido. `_close_contact_layer` chama-o para **sobrescrever**
  `_customer_id_close` — a linha de fechamento é a autoritativa no ReplacingMergeTree do analytics (o bridge
  é o único escritor da close row, `models.py` `contact_closed`), então `sessions.customer_id` vira o nativo.
  Best-effort: qualquer erro → fallback (nunca bloqueia o close).
- **`platform-ui/AgentAssistPage.tsx`** — a `HistoricoTab` passa a chavear pelo `caller.customer_id` do
  snapshot do ContextStore (`supervisorState.customer_context.context_snapshot`), com fallback `contactId` —
  a lista/busca ao vivo resolvem para o cliente real.

**Validação** — 6 unit tests `tests/test_close_customer_id.py` (nativo / fallback ausente / valor vazio /
JSON malformado / bytes+valor cru / sem tenant), verdes. *(Nota: 2 testes pré-existentes de
`test_webhook_bridge.py` — `test_resume_publishes_agent_ready_and_agent_done` e
`test_process_inbound_does_not_call_resume_handler_for_customer_msg` — falham por dívida anterior não
relacionada: mock de producer que retorna None em `asyncio.create_task`, e referência à função removida
`forward_inbound_to_active_agent`. Não tocam `_close_contact_layer`.)*

**Escopo (honesto):** a plataforma **propaga** o `customer_id` nativo quando ele já está resolvido no
ContextStore. **Quem escreve** `caller.customer_id` nativo (intake chamando `customer_resolve`, ou o step CRM
`resolve` gravando o nativo em vez do id de CRM) é **wiring de fluxo — Fase B**. Sem CRM no demo, a validação
end-to-end no browser depende desse wiring.

---

## Resolvedor de Identidade — Fase A · Slice 2 (durabilidade PG) (2026-07-02)

Torna a identidade **durável** (sobrevive ao TTL do Redis) — o cadastro mínimo interno passa a ter cofre em
Postgres, sem virar CRM. Reusa o pool asyncpg que o channel-gateway já cria para os attachments (sem
dependência nova). Ver `docs/product/identity-resolver-fase-a-plano.md` §2/§3.

- **`identity/index.py`** — `IdentityIndex` ganhou `db_pool` opcional (None → comportamento Redis-only do
  Slice 1): `ensure_schema` (cria `CREATE SCHEMA IF NOT EXISTS identity` + tabelas `customers`,
  `customer_secondary_keys`, `customer_external_refs`, `customer_merges` — idempotente, **raw asyncpg, não
  Prisma**, respeitando o invariante `db push`); `promote_to_durable` (upsert em `customers` +
  `customer_secondary_keys` reusando o `customer_id` nativo, chaves **hasheadas**); e **fallback Redis→PG** em
  `resolve_or_provision` — miss no índice Redis → `_pg_resolve` por `(kind, value_hash)` → **reidrata** o
  índice Redis e devolve `matched_by="durable"`.
- **`adapters/webhook.py`** — adapter recebe `db_pool`; `ensure_identity_schema()` chamado no startup;
  **promoção no gatilho concreto** (§5 da spec): logo após `write_pending` no `_open_child_session` (uma
  pendência registrada precisa sobreviver à janela efêmera).
- **`main.py`** — passa o pool asyncpg existente ao `WebhookAdapter` e chama `ensure_identity_schema` no
  startup.

**LGPD:** o PG guarda **só hashes** (`value_hash`), nunca a PII crua; teste inspeciona que o telefone não
aparece em `value_hash`. Schema `identity` separado de `auth` (cliente final ≠ usuário de plataforma).

**Validação** — 3 unit tests novos (`TestDurability`: promoção + fallback com fake PG pool + reidratação;
sem-pool sem-fallback; promote/ensure no-op sem pool) → 19 no total em `tests/test_identity_index.py`; smoke
`infra/test/test_identity_resolver_slice2.sh` (delegate dispara promoção → `identity.customers` +
`secondary_keys` populados → apaga o índice Redis → resolve por email e phone ainda acha via PG
`matched_by="durable"` + reidrata; LGPD sem PII no PG). Tudo verde.

**Limitação registrada:** `external_refs`/`merges` têm DDL mas só são populados na Fase B (identidade
progressiva + merge de clientes).

---

## Resolvedor de Identidade — Fase A · Slice 1 (Redis-only) (2026-07-02)

Primeira fatia do **cadastro mínimo interno de cliente** (sem CRM) que renasce o papel identidade↔fluxo-
pendente da Journey (removida no Arc 19 Fase F) — ver `docs/product/identity-resolver-nivel-b-spec.md` e o
plano `docs/product/identity-resolver-fase-a-plano.md`. Motivação de fundo: `contact_id`-como-`customer_id`
é um erro; sem identidade estável não há histórico unificado nem retomada cross-canal. Slice 1 entrega o
mecanismo (Redis-only, sem PG ainda) e a **retomada cross-canal demoável**.

- **`packages/channel-gateway/.../identity/`** (módulo coeso, co-localizado por reuso do prior art
  `pending_workflow`): `normalize.py` (normalização por tipo phone/email/cpf/princ + `hash_anchor` =
  `sha256(salt+normalizado)`) e `index.py` (`IdentityIndex`): **Lookup 1** `resolve_or_provision`
  (`{t}:identity:{kind}:{hash}`→`customer_id`; provisiona prospect efêmero `cus_…` quando ausente;
  desambiguação por confiança princ>cpf>email>phone; `ambiguous` em colisão de confiança igual) e **Lookup 2**
  `write/find/consume_pending` (`{t}:pending_by_customer:{customer_id}` HASH; `find` poda pendências cujo
  `resume_token` saiu de `{t}:resume_tokens`).
- **`adapters/webhook.py`**: `_open_child_session` faz **dual-write** (chave legada `pending_workflow:{contact_id}`
  + `pending_by_customer:{customer_id}`), flag-gated (`PLUGHUB_IDENTITY_RESOLVER_ENABLED`), best-effort;
  `_anchors_from_context` deriva âncoras do context; métodos `resolve_customer`/`find_pending_by_customer`.
- **`main.py`**: `POST /v1/channels/webhook/identity/resolve` + `GET /v1/channels/webhook/pending/by-customer/{id}`
  (declarados antes das rotas greedy `/{skill_id}` e `/pending/{contact_identifier}`).
- **`mcp-server-plughub/src/tools/workflow.ts`**: tool nova `customer_resolve`; `pending_workflow_get` estendida
  com `anchors[]` (Lookup 1→Lookup 2 cross-canal), mantendo `contact_identifier` legado. `workflow_resume`
  inalterada.
- **Config/env**: namespace `identity` no config-api (`prospect_ttl_s`, `resolution_index_ttl_s`, `system_trust`);
  **salt em env** `PLUGHUB_IDENTITY_SALT` (correção de invariante: salt é segredo, não config-api). Env no
  compose demo do channel-gateway.

**LGPD:** índice de resolução **nunca guarda PII em claro** (âncoras normalizadas + hasheadas com salt por
tenant); tenant isolation nas chaves; teste inspeciona que phone/email não aparecem em `{t}:identity:*`.

**Validação** — 16 unit tests (`tests/test_identity_index.py`, stub Redis in-memory: normalize/hash
determinístico+salt, provision, cross-âncora, tenant isolation, ambiguous, confiança, pendências + stale) +
smoke `infra/test/test_identity_resolver_slice1.sh` (resolve/provision → cross-canal por email e phone →
pendência por cliente → retomada por outro canal → stale → sem PII no índice). Tudo verde.

**Limitação registrada:** identidade progressiva (anexar âncora nova a cliente existente em match parcial) e
durabilidade PG ficam para Slice 2 / Fase B.

---

## Customer History H2 — busca no histórico do cliente (backend) (2026-07-02)

Fecha a fase **H2** de `docs/arcos/customer-contact-history.md`: endpoint de busca por termo dentro dos
atendimentos passados de um cliente ("o cliente já reclamou de cobrança antes?"). Backend apenas — a UI
(caixa de busca + filtros na `HistoricoTab`) é a H3.

- **`packages/analytics-api/src/plughub_analytics_api/sessions.py`** — `GET /sessions/customer/{id}/search`
  (`customer_history_search` + `_search_customer_history` + helpers `_iso`/`_make_snippet`). Query:
  ClickHouse `messages` JOIN `(sessions FINAL)`, escopo `tenant_id + customer_id`, só sessões fechadas,
  `positionCaseInsensitiveUTF8(m.content, {q})` (substring case-insensitive), filtros estruturados
  `from`/`to` (via `parseDateTimeBestEffort`), `channel`/`outcome`/`pool`; colapsa em **1 hit por sessão**
  (ordem `opened_at` DESC, consistente com a lista) → `{ session_id, opened_at, channel, outcome, pool_id,
  snippet, score }`; `score` = nº de mensagens que casaram; `limit`/`offset` paginam **sessões**. Graceful
  em falha (200 `[]`).

**Decisão de arquitetura (diverge do doc §5 original, registrada):** buscar sobre **ClickHouse**
(`messages`+`sessions`) em vez do Postgres `session_stream_events`. Motivos: (a) **LGPD por construção** —
`analytics.messages` não tem coluna `original_content` (o Postgres a tem ao lado do texto mascarado → risco);
(b) `sessions.customer_id` dá escopo por cliente sem join cross-store; (c) colocado com H1/transcrição e já
alcançável pelo proxy. Custo: sem stemming (substring). Postgres+`GIN(tsvector)` fica p/ escala (H5).

**LGPD:** indexa e devolve **só conteúdo MASKED** (`messages.content`); `original_content` não existe na
tabela — nunca lido nem exposto; sem `audit_access_log` (mesma postura do H1). Teste unitário faz assert de
que a string `original_content` **não** aparece no SQL.

**Validação** — 13 unit tests novos em `tests/test_sessions.py` (`TestMakeSnippet`, `TestSearchCustomerHistory`,
`TestSearchEndpoint`: snippet/janela, collapse por sessão, paginação, filtros→params, 422 sem `q`/tenant,
graceful, no-shadow da rota da lista) + smoke `infra/test/test_h2_customer_history_search.sh` (seed 2 sessões
+ mensagens; valida score por sessão, ordem `opened_at` DESC, case-insensitive, filtros channel/outcome,
termo inexistente=0, e o proxy `/analytics` no :5174). Tudo verde.

---

## Customer History H1 — drill lista → transcrição na HistoricoTab (2026-07-02)

Fecha a fase **H1** de `docs/arcos/customer-contact-history.md`: no Agent Assist (platform-ui), expandir
um contato na aba **Histórico** agora carrega **inline** a transcrição MASKED daquele atendimento anterior
(clicar na linha já abre; fetch lazy na primeira expansão). Resolve "ver o atendimento que originou a
pesquisa/contato" reusando o endpoint de transcrição que já existia.

Recon revelou que o "só falta wiring" tinha um pré-requisito de infra: o **platform-ui (:5174) nunca teve
proxy `/analytics/*`** (só o legado `agent-assist-ui` :5173 tinha). Por isso, no app canônico, tanto a lista
de contatos quanto a transcrição eram **inalcançáveis** (a lista degradava silenciosamente para vazio). O fix
adiciona o proxy — que conserta a lista pré-existente **e** habilita o drill.

- **`packages/platform-ui/Dockerfile`** (nginx) + **`vite.config.ts`** — nova rota `/analytics/*` →
  `analytics-api:3500` com strip do prefixo (espelha o legado). Alcança `/sessions/customer/{id}` e
  `/v1/transcript/sessions/{id}` sem colidir com o catch-all `/v1` → agent-registry.
- **`src/modules/agent-assist/hooks/useSessionTranscript.ts`** (novo) — fetch lazy de
  `GET /analytics/v1/transcript/sessions/{id}?scope=contact`, degradação graciosa, cancelamento.
- **`src/modules/agent-assist/types.ts`** — `TranscriptMessage` + `SessionTranscriptResult`.
- **`src/modules/agent-assist/components/tabs/HistoricoTab.tsx`** — `TranscriptView`/`TranscriptBubble`
  inline na linha expandida (bolhas compactas, `max-h-64` rolável, estados loading/erro/vazio, estilo de
  nota interna `agents_only`, aviso "conteúdo mascarado (LGPD)").
- **i18n** `agentAssist.json` (en + pt-BR) — 5 chaves `historico.*` (transcriptTitle, loadingTranscript,
  transcriptError, noMessages, maskedNote).

**LGPD:** masked-by-construction — `analytics.messages` não tem coluna `original_content`, então o drill
não expõe original e **não** requer `audit_access_log` (postura §8 do doc). O endpoint impõe só tenant
isolation; conteúdo mascarado = baixa sensibilidade (D3).

**Correção de doc:** o prefixo real do router é `/v1/transcript` (o doc dizia `/transcript`); path do drill =
`/analytics/v1/transcript/sessions/{id}`.

**Validação** — `infra/test/test_h1_customer_history_drill.sh` (novo), 7/7 verde: (A) transcrição MASKED
direto no :3500 (3 msgs semeadas, token e `agents_only` preservados); (B) **novo proxy** `/analytics/*` no
:5174 (transcrição + lista chegam ao analytics-api, não à SPA); (C) drill com dado real descoberto no demo
(customer real → lista traz a sessão → transcrição MASKED com 11 msgs). Render visual no browser pendente de
sessão com contato identificado cujo id resolva ao `sessions.customer_id` (limitação de identidade, §7 do doc
— fora do escopo de H1).

---

## G-PROBE — perna humana `curar` (curadoria/calibração) fechada: seed + smoke (2026-07-02)

Fecha o item "G-PROBE (perna humana `curar`)" do TODO. Recon encontrou o desenho fechado em 2026-06-25
já implementado quase por completo por uma sessão anterior, sem ter sido registrado como concluído:
catálogo `curar` (`none|read_only|read_write`, `scopable: pool`) já em `infra/modules.yaml`;
`packages/evaluation-api/src/plughub_evaluation_api/contestation_router.py` já gateava os 5 endpoints de
curadoria (`list_curations`, `resolve_curation`, `get_blind_context`, `blind_rescore`, `blind_resolve`)
via `_require_curar` (exige Bearer JWT) + `_check_abac_permission('curar', pool_id, min_access=...)` —
leitura exige `read_only`, escrita exige `read_write`, escopo por pool resolvido via
`_curation_pool_id` (join review→instance→campaign); `CuradoriaPage.tsx` (platform-ui) já mandava o
Bearer do operador (`useAuth().session.accessToken`), sem caixa de admin-token. Testes unitários
`test_curar_*` (`test_available_actions.py`) já cobriam `_check_abac_permission` isoladamente.

Faltava só o que impedia validar/usar a feature de ponta a ponta:

- **`infra/seed/seed_auth.py`** — `supervisor@plughub.local` ganhou `evaluation.curar = read_write`
  (`scope: []`). Sem isso nenhum usuário demo tinha o grant e `CuradoriaPage` ficava 403 por padrão.
- **`infra/test/smoke_gprobe_curar_auth.sh`** (novo) — mesmo padrão de `smoke_gprobe_service_auth.sh`
  (mint de JWT HS256 dentro do container evaluation-api, mesmo `jwt_secret` que a API valida). Valida:
  `GET /curations` sem Bearer → 401; sem grant `curar` → 403; com `curar=read_only` → 200;
  `POST /curations/{id}/resolve` com `read_only` (insuficiente p/ escrita) → 403; com `read_write` e
  review inexistente → 404 (prova que o ABAC passou antes do lookup no banco, já que o gate roda primeiro
  em ambos os endpoints); mesma prova de ordem para `GET /curations/{id}/blind-context`.

**Perna agente/sistema deste item** (pre-review/ai-review/seed-flush) permanece re-roteada para depender
do **Agent Principal** (identidade de máquina, F1–F4, não implementado) — decisão de 2026-07-01, ver TODO.md.

---

## evaluation-api — bug self-view: pool administrativo da campanha divergente bloqueava o próprio avaliado (2026-07-02)

Resolve o "BUG REAL encontrado ao vivo (2026-07-01)" do TODO: quando `campaign.pool_id` (administrativo,
usado só para escopo/relatório) diverge do pool operacional real onde o agente atendeu, o filtro de
visibilidade de `list_results` (T10-C, interseção AND cega de `evaluated_user_id ∈ supervised_user_ids`
com `campaign.pool_id ∈ accessible_pools`) bloqueava o próprio avaliado de ver (e portanto contestar) o
seu resultado — a posse do resultado (`evaluated_user_id == sub`) nunca deveria depender de acesso
administrativo de pool.

- **`packages/evaluation-api/src/plughub_evaluation_api/router.py`** — `_compute_result_scope` passa a
  retornar uma 3-tupla `(evaluated_user_ids, accessible_pools, self_user_id)`; `self_user_id` = `sub` do
  JWT para não-admin, `None` para admin/sem-token (admin já não tem restrição de posse). `list_results`
  (GET handler) repassa `self_user_id` para `_db.list_results`. `get_result` (item único) não precisou de
  mudança — não tinha filtro de linha algum (só `result_id` imprevisível + ABAC via `available_actions`).
- **`packages/evaluation-api/src/plughub_evaluation_api/db.py`** — `list_results` ganhou parâmetro
  `self_user_id: str | None = None`. Quando setado junto com `accessible_pools`, o filtro SQL vira
  `evaluated_user_id = self_user_id OR (evaluated_user_id = ANY(supervised) AND c.pool_id = ANY(accessible))`
  — a posse bypassa o filtro de pool; a visibilidade de outras pessoas supervisionadas continua exigindo a
  interseção AND normal (Arc 9 Group × Arc 7 accessible_pools, ver `docs/arcos/arc9-agent-groups.md` § Escopo
  de visibilidade). `use_join` já garantia o alias `c.` sempre que `accessible_pools` é truthy — sem mudança
  necessária ali.
- **Testes**: `test_available_actions.py` (seção T10-C) atualizados para a 3-tupla + novo teste de regressão
  `test_scope_self_view_bypasses_pool_mismatch`. `test_router.py::test_list_results` mocka `_db.list_results`
  inteiro — sem impacto (parâmetro novo é opcional).
- **Hygiene de dado (opcional, não bloqueante para o fix)**: a campanha demo específica que expôs o bug
  (`evcampaign_8ce82c4110d24d5a903d270649f7519f`, `pool_id` administrativo divergente de
  `retencao_humano`) pode ainda valer a pena corrigir via `UPDATE evaluation.campaigns SET
  pool_id='retencao_humano' WHERE id='evcampaign_8ce82c4110d24d5a903d270649f7519f'` — o fix de código torna
  isso não-bloqueante para self-view, mas `pool_id` também é usado por outros escopos (supervisor/admin
  não-self), então alinhar o dado continua sendo boa higiene.

---

## agent-registry — bootstrap seguro (substitui `db push --accept-data-loss` no boot) (2026-07-02)

Resolve o risco vivo documentado no TODO ("agent-registry `db push` clobbera tabelas") — já causou perda
real de `pools`/`skills` duas vezes (rebuild com `db push --accept-data-loss` recriando o que divergia do
`schema.prisma`). Achado: o projeto já tinha 25 migrações versionadas em `prisma/migrations/` (voltando a
`20260408121021_init`), nunca aplicadas via `prisma migrate deploy` — só `db push` rodava no boot,
ignorando esse histórico.

- **`packages/agent-registry/scripts/bootstrap-db.js`** (novo) — substitui o `db push` no `CMD` do
  Dockerfile. Auto-detecta o estado do banco via `to_regclass` (sem depender de flag manual): (1)
  `_prisma_migrations` existe → `migrate deploy` normal (só pendentes); (2) não existe mas a tabela `pools`
  existe (banco legado, criado por `db push`) → baseline uma vez (`migrate resolve --applied` para cada
  migração já refletida no schema, sem executar DDL) + `migrate deploy`; (3) banco vazio (instalação nova
  de verdade) → `migrate deploy` aplica tudo do zero, sem risco. Nunca dropa tabela existente por
  divergência de schema em nenhum dos três caminhos.
- **Caminho destrutivo explícito preservado** — `FRESH_INSTALL=true` no env do container roda o antigo
  `db push --accept-data-loss` de propósito (mesmo script, branch isolado), para reset de dev/instalação
  intencional. **`infra/scripts/fresh-install.sh`** (novo) — wrapper de confirmação via
  `docker compose run -e FRESH_INSTALL=true`, reusando o mesmo código (sem duplicar o caminho destrutivo).
- **`package.json`**: novo script `db:bootstrap` (`node scripts/bootstrap-db.js`).
- Resolve a decisão registrada no TODO (2026-07-01) "separar fresh install de restart/reconcile normal" —
  a auto-detecção elimina a necessidade de um operador lembrar de setar a flag corretamente no caso comum
  (boot normal), já que o script deduz o estado certo sozinho.
- **Fora de escopo desta fatia** (item F3 separado, sem bug vivo, ver TODO.md § Frente 3): consolidar
  `infra/seed/*.py` numa orquestração única via API — isso é sobre a camada de **seed data**, já
  seed-if-absent em todos os stores; este fix é só sobre **schema DDL** do agent-registry, que era o único
  serviço com um mecanismo destrutivo no boot.

**Incidente ao vivo pós-deploy (2026-07-02) — a v1 do baseline tinha um bug real, corrigido no mesmo dia.**
A primeira versão do script baseava o caminho "banco legado" **sem verificar** — confiava que, se a tabela
`pools` existisse, todas as migrações já estavam refletidas. Essa suposição era falsa neste caso: a migração
`pool_llm_account_ids` (criada mais cedo no mesmo dia) tinha sido adicionada ao `schema.prisma`, mas o `db
push` antigo não tinha efetivamente aplicado a coluna no banco antes do container trocar pro script novo. O
baseline marcou `--applied` mesmo assim (bookkeeping puro, sem DDL) → `migrate deploy` acreditou que estava
tudo em dia → toda query em `pools` (`GET`/`POST /v1/pools`, `operational.js`) passou a falhar em runtime com
`column "llm_account_ids" does not exist` → o `RegistrySyncer` (orchestrator-bridge) tentou re-semear os pools
no restart seguinte e todo `POST` retornou 500 → pools zerados de verdade (skills sobreviveram, pois não
tinham divergência de schema). **Recuperação**: `prisma db execute --file <migration>/migration.sql` aplicou
o `ALTER TABLE` real (a tentativa inicial de `migrate resolve --rolled-back` falhou com `P3012` — esse flag só
vale para migração marcada como falha, não como aplicada-mas-incorreta); `migrate deploy` confirmou limpo
depois; restart do `agent-registry` + `orchestrator-bridge` re-semeou os 20 pools do YAML.
**Fix (mesmo dia)**: `hasSchemaDrift()` — `prisma migrate diff --from-url $DATABASE_URL --to-schema-datamodel
schema.prisma --exit-code` — roda **antes** de confiar no baseline; se houver drift real, fecha o gap com um
`db push` guardado (mesmo mecanismo que este banco sempre usou, agora só uma vez, verificado, não a cada
boot) antes de baselinar. **Checagem final de sanidade** (mesmo `hasSchemaDrift()`) roda incondicionalmente
antes do script terminar com sucesso, em qualquer um dos 4 caminhos — se o schema ainda divergir por qualquer
motivo, o script falha alto (`exit 1`, container não sobe) em vez de servir 500 silenciosamente.

---

## Groups — remoção das abas Agents/Shifts (Arc 9) (2026-07-01)

Removidas por decisão arquitetural: Agents duplicava Members (dupla fonte de verdade com `Pool.agent_kind`,
que já é o campo canônico humano/AI); Shifts deveria ser modelado como Groups distintos, não como sub-recurso.

- **auth-api**: removidas tabelas `agent_group_members`/`agent_group_shifts` + CRUD; `resolve_supervisor_scope()`
  simplificado de 3-tupla para 2-tupla (sem shift-gating/expansão por agent_type); `supervised_agent_types`
  removido do JWT (claim permanentemente ausente — `accessible_pools` já escopa os mesmos endpoints por pool,
  independente).
- **platform-ui**: `GroupsPage.tsx` — drawer reduzido a 3 abas (Info/Members/Owners); i18n órfão removido.
- **Consequência aceita**: `PoolPrincipal.supervised_agent_types`/`_apply_agent_scope` em analytics-api
  tornam-se no-op permanente (tratam ausente como "sem restrição") — não removido do código, documentado como
  tal.
- Docs: `docs/arcos/arc9-agent-groups.md` (banner de remoção + novo estado); `CLAUDE.md` § Arc 9 reescrita.

---

## AI Gateway — LLM Accounts Catalog (contas configuráveis via Configuration) (2026-07-01)

Suporte a múltiplas contas de LLM geridas pelo Configuration (não só por env var fixa), com pools associáveis
a contas preferidas e fallback. Segue a Single Source Invariant: só a API key é segredo (env var); o resto
fica no config-api.

- **config-api**: novo namespace `llm_accounts` (genérico, sem código de backend dedicado) — `provider`,
  `display_name`, `rpm_limit`, `tpm_limit`, `active` por id de conta.
- **ai-gateway**: `llm_accounts_catalog.py` (novo) — `load_llm_accounts_catalog()` busca o namespace no boot,
  resolve a API key via convenção `PLUGHUB_LLM_ACCOUNT_<ID>_API_KEY`, substitui a construção de
  `providers`/`accounts` quando o catálogo retorna resultados; degrada graciosamente para
  `PLUGHUB_ANTHROPIC_API_KEYS`/`PLUGHUB_OPENAI_API_KEYS` se config-api estiver fora. **Fix concomitante**:
  `ReasonEngine` (`/v1/reason`) não tinha suporte a múltiplas contas (diferente de `/v1/inference`) — agora
  aceita `providers`/`account_selector` e seleciona conta em `process()`/`_process_tool_use()`.
- **agent-registry**: `Pool.llm_account_ids: string[]` (`PoolRegistrationSchema`) — contas preferidas em
  ordem de preferência; migration `20260702000000_pool_llm_account_ids`.
- **routing-engine**: `_write_pool_context()` escreve `session.pool.llm_account_ids[]` no ContextStore após
  cada roteamento.
- **skill-flow-engine**: step `reason` resolve `session.pool.llm_account_ids` via `resolvePreferredConfigIds()`
  e repassa como `preferred_config_ids` no `InferenceRequest`/`ReasonRequest`.
- **platform-ui**: `LlmAccountsPage.tsx` (nova aba Resources → LLM Accounts) — CRUD do catálogo, mostra o
  nome esperado da env var; `PoolsPage.tsx` — seção "LLM Accounts preferidas" no drawer de edição do pool
  (lista ordenável reusando `PoolListEditor`).
- Docs: `docs/arcos/ai-gateway.md` § LLM Accounts Catalog (nova); `CLAUDE.md` § AI Gateway — Multi-Account
  Rotation atualizada.

---

## Dashboards F1 — cards Fila/SLA + Volume por Canal + i18n de título/colunas (2026-06-28)

- **+2 cards** (molde F1): `fmt_pools_queue` (by_pool → TableData: pool/contatos/fila/abandono/espera/SLA) via
  `GET /reports/display/pools-queue`; `fmt_volume_by_channel` (by_channel → DonutData) via
  `GET /reports/display/volume-by-channel`. Entradas `pools-queue`/`volume-by-channel` no `ENDPOINT_CATALOG`
  + `catalog.*.label` (en+pt). Reusa `query_pools_queue`/`query_pools_volume` (scope aplicado no endpoint).
- **Fix i18n do card inserido**: o título ficava "assado" no idioma de criação e as colunas da table vinham do
  backend em idioma misto. Agora: `resolveCardTitle(card, t)` (em `catalog.ts`) re-traduz o label do catálogo em
  tempo de render (detecta o baked em EN/pt-BR, preserva título custom) — usado em `DashboardView` e
  `DashboardsPage`; `TableTool` traduz cabeçalhos por `col.key` via dicionário `displayCols` (en+pt) com
  fallback ao label do backend. Catálogo agora com 19 cards.

---

## Dashboards F1 — cobertura de catálogo: card "Disponibilidade de Agentes" (2026-06-28)

Primeiro card da cobertura incremental (molde da F1). Reusa o relatório existente do Arc 8.

- **analytics-api**: `fmt_agent_availability` (`display_formatters.py`) reusa `query_agent_availability` e
  formata como **TableData** (agente/pool/dia/pausas/pausado-min); rota `GET /reports/display/agent-availability`
  (`display.py`, mesmo padrão + `optional_pool_principal` p/ scope).
- **platform-ui**: entrada `agent-availability` no `ENDPOINT_CATALOG` (tool `table`); i18n
  `catalog.agent-availability.label` (en+pt). Aparece no Add card / Role defaults / Home como qualquer card.
- **Molde F1** validado: novo relatório = formatter + endpoint + entrada no catálogo + i18n label. Próximos
  (Fila/SLA, Pools/Infra, qualidade/calibração, performance diária, surveys) seguem o mesmo.

---

## Infra fix — agent-registry em banco próprio (para de dropar config no build) (2026-06-28)

**Bug (reproduzido):** o container `agent-registry` roda `prisma db push --accept-data-loss` no boot;
como compartilhava o `plughub_demo` com os demais serviços, o push **DROPava** tabelas do schema `public`
fora do Prisma dele — em especial `platform_config` (config-api). Como o Docker **bake** rebuilda/recria
todos os serviços a cada `up --build`, **todo build dropava a config** (templates de dashboard, role_catalog,
etc.). Confirmado: recriar só o agent-registry → `relation "public.platform_config" does not exist`.

**Fix:** `agent-registry.DATABASE_URL` → **`plughub_registry`** (banco dedicado). O `db push` agora só afeta o
banco dele; `plughub_demo` (config, auth, calendar, …) fica intacto. Init script
`infra/demo/initdb/00_create_registry_db.sql` cria o DB em volumes novos; volume existente cria manualmente
(`CREATE DATABASE plughub_registry`). Nenhum outro serviço lê tabelas do agent-registry direto do banco (usam
a API 3300/Kafka), então a migração é segura. Bônus: pools/skills editados na UI agora são duráveis (antes
eram dropados a cada build). **Workaround até aplicar:** `up -d --build --no-deps platform-ui` (não recria o
agent-registry).

## Dashboards F4 — personalização no Home (allowlist + layout pessoal) (2026-06-28)

- **`DashboardView`** vira personalizável: modo **Customize** (todas as roles) — drag/resize, **×** remove card,
  **Add component** (dropdown restrito à allowlist da role), **Reset to default** (volta ao starter), **Done**
  salva o layout pessoal (`layout:{tenant}:{user}` no config-api).
- **Modelo de resolução**: layout pessoal (se houver) → starter da role → default do módulo → 1º template;
  `reconcileCards` filtra cards fora da allowlist. Load/save do layout pessoal via config-api (com Bearer).
- i18n `home.customize/done/saving/reset/addComponent/removeCard` (en+pt). Validado admin/operator/supervisor.

---

## Dashboards F3 — allowlist + starter por role (2026-06-28)

- **Storage** (`dashboard-hooks`): `role_catalog:{role}` = `{ allowed: string[] (catalog ids), starter_template_id }`
  no Config API namespace `dashboards`; `load/saveRoleCatalog`.
- **DashboardView**: resolução passa a ser **starter da role → default do `module_config` → 1º template**;
  **reconcile** filtra cards fora da allowlist (vazia/ausente = sem restrição; cards legados/desconhecidos
  mantidos).
- **Admin** (Config → Dashboards): botão **"Padrões por papel"** → `RoleDefaultsModal` p/ definir, por role,
  o template starter + a allowlist (checklist do `ENDPOINT_CATALOG`). Salva no `role_catalog`.
- **Fixes pegados no teste**: (a) **remoção de card** no builder — o `×` era engolido pelo drag do
  react-grid-layout; `onMouseDown stopPropagation` no botão resolve. (b) **labels do catálogo em PT no EN** —
  as chaves `catalog.{id}.label` (já referenciadas pelo `AddCardModal` com fallback) nunca existiam; adicionadas
  (en+pt) → `AddCardModal` e `RoleDefaultsModal` agora respeitam o idioma.

---

## Dashboards F2 — consumo no Home (view-only, todas as roles) (2026-06-28)

Primeira fatia do modelo de consumo (spec `docs/product/dashboard-catalog-coverage-spec.md`).

- **`DashboardView.tsx`** (novo): renderer **view-only** do dashboard — reusa o Display Tool registry
  (`CardRenderer`) + `FilterBar` + react-grid-layout, **sem** chrome de builder (sidebar/edit/add). Resolve o
  template via `module_config.dashboard.default_template_id` → fallback 1º template; aplica layout pessoal se
  existir; `isDraggable/isResizable=false` (personalização é F3/F4). Estado vazio + loading com i18n
  (`dashboards.home.*`).
- **`HomePage`**: deixa de ser landing estática e passa a renderizar `<DashboardView/>` (welcome sensível à
  role + dashboard). Visível a **todas as roles** — leituras de config (`GET /config/...`) são abertas (só
  writes são gated), e o escopo dos dados é aplicado nos endpoints `/reports/display/*`. O builder segue em
  Config → Dashboards (admin).
- Sem mudança de backend. Próximas fatias: F3 (allowlist + starter por role) e F4 (picker do usuário).

---

## Access ↔ Groups — associação pela tela do usuário + grupo genérico de usuários (2026-06-28)

Generaliza o grupo (Arc 9) para "grupo de usuários" com **member/owner**, e permite associar pelos dois lados.

- **AccessPage** (`/config/access`): nova seção **Grupos** no modal do usuário — colunas **Membro** (→
  `agent_group_users`) e **Owner** (→ `agent_group_supervisors`). Diff aplicado no Save; como não há rota
  reversa "grupos do usuário", o modal sonda o detalhe de cada grupo (N+1, ok p/ poucos grupos). Hint avisa
  que o escopo de owner vale no próximo login (denormalizado no JWT).
- **GroupsPage** (`/config/groups`): abas reorganizadas **Info · Members · Owners · Agents · Shifts**.
  `Members` agora = **usuários** (`agent_group_users`) via **checklist com busca**; `Owners` (era
  "Supervisors") = checklist de usuários; `Agents` (era "Members") mantém os `agent_type_ids` (escopo de IA
  preservado — `supervised_agent_types`). Componente genérico `GroupUserChecklist` (kind users|supervisors).
  Renames só em i18n (en+pt); código `supervisor`/`members`/rotas estável.
- **i18n fixes (mesma área)**: chaves cruas do modal New/Edit User resolvidas — `users.fullName`,
  `emailPlaceholder`, `newPassword`, `passwordBlank`, `passwordMin8`, `poolsNone`, `selectAll`,
  `deselectAll`, `noPoolsConfigured`, `poolsDescription`, `permissionsDescription`, `saveChanges`,
  `createUser`, e `form.status`/`saving`/`applying` (en+pt-BR).

---

## BUG fix — Access (ABAC): permissões agora carregam na UI + i18n (2026-06-28)

- **Load**: o modal de edição de usuário (`AccessPage`/`UserModal`) abria com **todas as permissões em
  "Sem acesso"**. Causa: `UserResponse` (GET `/auth/users` e `/users/{id}`) **não inclui `module_config`** →
  o front recebia `undefined` → form vazio. Fix cirúrgico no front: o modal busca
  `GET /auth/users/{id}/module-config` na edição (`useEffect`) e hidrata o `ModulePermissionForm`. Sem mexer
  no backend nem na forma do JWT.
- **i18n**: `ModulePermissionForm` tinha labels **fixos em PT** (`ACCESS_LABELS`, "Escopo por…",
  "permissões", "ativo(s)", empty-state). Migrados para `t()` no namespace `access` (`permForm.*`, en+pt-BR).

---

## Calendários — Dias Especiais (horário em feriado) + flush nas Exceções (2026-06-28)

Follow-ups do lote de calendários (specs: `docs/product/special-days-with-hours-spec.md`).

- **#1 — flush nas Exceções:** `ExceptionsEditor` virou forwardRef com `flushPending()`; o submit do calendário
  commita o add-row de exceção digitado antes de salvar (mesmo fix do holiday editor — digitar+Save sem "+").
- **#2 — Dias Especiais (Special Days):** o conjunto de feriados agora permite **horário custom** por dia, não
  só "fechado o dia todo". **Achado:** schema (`HolidaySchema.override_slots`), engine (`_resolve_date` já
  aplica override) e persistência (JSONB) **já suportavam** — só faltava a UI. `HolidaysEditor` ganhou o toggle
  **fechado o dia todo / horário custom** + editor de intervalos (reusa o padrão do `ExceptionsEditor`);
  `override_slots` na interface local + `pendingEntry/flushPending`; linha mostra horário ou "fechado".
  Rótulos visíveis renomeados para **"Dias Especiais / Special Days"** + subtítulo mencionando feriados
  (código `holiday*`/schema/rotas/engine **inalterados** — contrato estável). `override_slots: null` ≡ feriado
  fechado (retrocompat, sem migração). i18n en+pt-BR (`tabs.holidaySets`, `holidaySet.*`, `calendar.holidaySets`,
  `addInterval`, `noHolidaySets`, `goToHolidaySets`); strings hardcoded PT do form convertidas para `t()`.

---

## Calendários — consolidação de UI + disparo de avaliação por calendário (2026-06-28)

Fecha os dois itens de calendário (spec: `docs/product/calendar-consolidation-and-trigger.md`).

- **Item 1 — disparo por calendário (combo da campanha):** `useCalendarOptions` (`CampaignsPage`) chamava
  `GET /v1/calendars?tenant_id=…` **sem `organization_id`** → 422 → `.catch` engolia → combo vazio. Agora passa
  `organization_id` (de `VITE_CALENDAR_ORG_ID ?? 'org-default'`) **+** `tenant_id`, e loga o erro em vez de
  engolir. `evaluation_calendar_id` volta a ser selecionável; backend (`compute_expires_at` + dispatcher
  windowed T15) já respeitava a janela.
- **Item 2 — consolidação de UI:** removida por completo a seção de calendários do `configurations/platform`
  (`CalendarManager` — sub-abas Calendars + Holiday List — era CRUD redundante e mal-escopado, usava `tenant_id`
  no slot de `organization_id`). Fonte única passa a ser `/config/calendars` (`CalendarsPage`: Calendários +
  Feriados + Associações). `CalendarManager.tsx` + `config-plataforma/api/calendar-hooks.ts` ficaram órfãos
  (a remover via `git rm`).
- **Clone-from-existing:** no modal de Novo Calendário, seletor "começar a partir de [calendário ▾]" copia
  `weekly_schedule` + `exceptions` como **snapshot** e `holiday_set_ids` como **referências vivas** aos holiday
  sets compartilhados (sem backend novo). i18n `calendar.clone*` (en + pt-BR).
- **Modelo confirmado:** escopo é `installation_id → organization_id → tenant_id`; `tenant_id` segue universal,
  `organization_id` é o dono acima do tenant (não trocar um pelo outro), site/cluster = `installation_id`.
  Holiday set já era referência viva (engine resolve por id na avaliação); `weekly_schedule` é inline por calendário.
- **Holiday editor (CalendarsPage) — 3 fixes:** (1) **perda de feriado no Save** — o add-row (data+nome) só
  entrava na lista ao clicar `+`; clicar Save direto descartava o digitado. Agora `HolidaysEditor` expõe
  `flushPending()` (forwardRef/useImperativeHandle) e o submit commita o campo pendente antes de salvar.
  (2) **recorrente** — badge clicável por linha alterna `one-off` (YYYY-MM-DD) ↔ `↺ todo ano` (MM-DD), exibindo
  recorrentes sem ano. (3) **lista cortava em ~3** — removido o scroll interno (`max-h`); o corpo do modal rola,
  nenhuma linha some; coluna de data `w-24 whitespace-nowrap`. i18n `holidaySet.everyYear/oneOff/toggleRecurring`.

---

## G-PROBE platform-wide — Dashboards + Avaliações (2026-06-26): últimas caixas de admin-token

Fecha as caixas de admin-token restantes da frente (UI-only — gates de backend já existiam).

- **Dashboards** (`DashboardsPage` + `dashboard-hooks.ts`): os templates são config-api namespace `dashboards`
  (→ default `config.plataforma`, já coberto pelo gate dual). `configGet/Put/Delete/List` passam a mandar
  `Authorization: Bearer` (token-store) em vez de `X-Admin-Token`; caixa de admin-token + `localStorage
  plughub_admin_token` removidos.
- **Avaliações** (`AvaliacoesPage` + `evaluation-hooks.ts`): removida a caixa de admin-token do filtro; a
  adjudicação Arc6 **legada** (`adjudicateContestation`) usa o Bearer do operador (`bearerHeaders`). `adminHeaders`
  ficou sem uso e foi removido de `evaluation-hooks`. *(Retirada física do `adjudicate` segue com a limpeza do
  motor Arc6 legado.)*
- **Verificação Frente 2 / dashboard de quality (não era pendência):** recon confirmou que a observabilidade de
  qualidade já foi **consolidada** no bench (Analytics→Agents) + Quality Summary (abas Trend/Comparison removidas
  em 2026-06-16; lente `deploy` P2+P3 ✅). Só restam itens **diferidos por decisão** (P4 eixo epoch/versão; disparo
  do avaliador real por calendário; nits do bench). TODO §Frente 2 ganhou banner de STATUS.

---

## Knowledge Base — surface REST construído + gate ABAC (2026-06-26)

A `KnowledgePage` (`/evaluation/knowledge`) estava **morta**: chamava `/v1/knowledge/{search,snippets}` que
**não existia em lugar nenhum** (proxy Vite ia p/ eval-api:3400, que não define essas rotas; a
mcp-server-knowledge só servia `/admin/*` + MCP tools). A caixa de admin-token alimentava um caminho 404.
Esta fatia **constrói o surface REST** que faltava, gateado, e conserta de quebra o publish do KB vetorial.

- **mcp-server-knowledge** (`routes/knowledge.ts`): novo REST `GET /v1/knowledge/search`,
  `POST/DELETE /v1/knowledge/snippets`, reusando os helpers de `db.ts` (mesma lógica das MCP tools;
  `embedText` exportado de `tools.ts`). Gate DUAL (`middleware/require-knowledge-access.ts`, HS256 stdlib
  `crypto`): `X-Service-Token` (caller interno) OU Bearer + ABAC `evaluation.gerir_rubrica` (read p/ search,
  read_write p/ snippets). No-op quando `KNOWLEDGE_SERVICE_TOKEN` e `PLUGHUB_JWT_SECRET` vazios.
- **Proxy** (`vite.config.ts`): `^/v1/knowledge` corrigido de `3400` → **`3401`** (mcp-server-knowledge).
- **evaluation-api**: o publish de CalibrationNote no KB (`contestation_router`, 2 pontos) passa a mandar
  `X-Service-Token` (`_kb_headers()` + `config.knowledge_service_token`) — **conserta o KB vetorial do Arc 13**,
  que vinha falhando silenciosamente em 404 (a entrega primária ao avaliador via Postgres já funcionava).
- **UI**: `searchKnowledge`/`upsertSnippet`/`deleteSnippet` mandam `Authorization: Bearer` via `auth/token-store`;
  `KnowledgePage` perde a caixa de admin-token. A página passa a funcionar (search/add/delete reais).
- **compose**: `PLUGHUB_JWT_SECRET` + `KNOWLEDGE_SERVICE_TOKEN` na mcp-server-knowledge;
  `PLUGHUB_EVALUATION_KNOWLEDGE_SERVICE_TOKEN` na evaluation-api.
- **Smoke** `smoke_knowledge_rest_auth.sh`: search 401(sem)/200(ro)/403(sem grant); snippets POST 401/403(ro)/
  pass(rw)/pass(service-token); cleanup.

---

## G-PROBE platform-wide — Skills + agent-registry (2026-06-26)

Duas fatias. **(1) SkillsPage** (config/resources → Skills): NÃO era agent-registry — escreve config-api
namespace `competency_skills` (→ default `config.plataforma`, já coberto pelo gate dual da config-api). UI-only:
caixa removida, escritas via Bearer. **(2) agent-registry** — fecha o gap real: as mutações de config estavam
**sem auth** (`registry.ts` mandava só `x-tenant-id`).

- **agent-registry** (`middleware/require-resource-write.ts`): gate DUAL nos routers de config **pools/skills/
  channels/channel-endpoints** (montados com o middleware em `app.ts`). GET/HEAD/OPTIONS abertos; mutação exige
  `X-Service-Token` (callers internos) OU Bearer + ABAC `config.resources` (read_write). Verificação HS256 em
  **stdlib `crypto`** (sem dep). No-op quando `service_token` e `jwt_secret` vazios (postura atual preservada).
  `config.ts` ganha `service_token`. **Fora do gate de propósito** (cadeia maior): `pool-slots` (deploy),
  `instances`, `operational`.
- **Callers internos wirados**: `registry_syncer.py` (RegistrySyncer — pools/skills/channel-endpoints/slots do
  YAML no bootstrap) e `deploy.ts` (`skill_deploy`, POST /v1/skills/:id/deploy) mandam `x-service-token` (env,
  omitido se vazio).
- **UI**: novo `auth/token-store.ts` (holder de módulo do access token, espelhado pelo AuthContext via
  `useEffect`); `api/registry.ts` anexa `Authorization: Bearer` (do store) em todas as chamadas — destrava
  Bearer fora de hook. `SkillsPage` (config-recursos) perde a caixa de admin-token.
- **compose**: `PLUGHUB_JWT_SECRET` (= segredo auth-api, valida o Bearer da UI) + `AGENT_REGISTRY_SERVICE_TOKEN`
  na agent-registry; `AGENT_REGISTRY_SERVICE_TOKEN` na orchestrator-bridge e mcp-server-plughub.
- **Smoke** `smoke_agent_registry_write_auth.sh`: DELETE skill — sem cred 401, Bearer read_only/sem-grant 403,
  X-Service-Token e Bearer resources:rw passam o gate (404 not-found), GET lista aberto 200.
- **Residual**: ferramentas CLI de import (`sdk/cli/import.ts`, `gitagent/import.ts`) mutam `/v1/skills` sem
  token (dev/CI) — passar `x-service-token` se usadas contra registry gateado.

---

## G-PROBE platform-wide — Billing/pricing-api (2026-06-26): admin-token → Bearer+ABAC `config.plataforma`

Quarta fatia. A `BillingPage` NÃO usava config-api — escreve na **pricing-api** (`/v1/pricing/*`). Migrada para
gate DUAL. Como o módulo ABAC `billing` só tem `visualizar` (leitura), as mutações reusam **`config.plataforma`**
(read_write) — billing tratado como config de plataforma (decisão da sessão, sem campo billing novo nem re-seed).

- **pricing-api** (`router.py`): `require_admin` reescrito p/ DUAL — admin-token (`seed_pricing`/sistema) OU
  Bearer + ABAC `config.plataforma` (read_write); verificação HS256 em stdlib + `config.jwt_secret`
  (`PLUGHUB_PRICING_JWT_SECRET`). Gateia upsert/delete de resources + activate/deactivate de reservas. Postura
  original preservada (`admin_token` vazio = auth desabilitada).
- **compose**: `PLUGHUB_PRICING_JWT_SECRET=changeme_auth_jwt_secret_demo_32c` na pricing-api.
- **platform-ui** `BillingPage`: caixa de admin-token removida (state + input do `ResourceSidebar` + props); o
  toggle de reserva manda `Authorization: Bearer` (session token) em vez de `X-Admin-Token`.
- **Smoke** `smoke_pricing_write_auth.sh`: POST resources — sem credencial 403, Bearer read_only 403,
  X-Admin-Token 200, Bearer `config.plataforma:rw` 200 (tenant descartável, limpa no fim).

---

## G-PROBE platform-wide — Channels (2026-06-26): WebChat + Webhook → Bearer+ABAC `config.canais`

Terceira fatia (UI-only — o gate dual da config-api já existia). As duas telas de Channels que escrevem config
(`WebChatConfigPage` namespace `webchat`, `WebhookConfigPage` namespace `webhook`) saem do X-Admin-Token.

- **config-api** (`router.py`): `webhook` adicionado ao mapa de namespaces de canais (→ `config.canais`;
  `webchat` já estava).
- **platform-ui** `WebChatConfigPage` + `WebhookConfigPage`: caixa de admin-token removida (substituída por
  uma barra de status/reload); `putConfig` passa o `session.accessToken` na posição Bearer. Edição autorizada
  pelo login + ABAC `config.canais`.
- **Smoke** `smoke_config_write_auth.sh` §4: Bearer canais:rw → 200 em webchat/webhook; Bearer plataforma
  (campo errado) → 403.

---

## G-PROBE platform-wide — slice config-api (2026-06-26): Platform + Masking → Bearer+ABAC

Segunda fatia da migração platform-wide. O endpoint de ESCRITA da config-api
(`PUT/DELETE /config/{namespace}/{key}`) é **genérico e compartilhado** por ~7 telas (cada uma num
namespace). Gate **DUAL** (decisão da sessão, por ser endpoint compartilhado): aceita `X-Admin-Token`
(telas ainda não migradas) **OU** Bearer + ABAC `config.{campo}` mapeado por namespace.

- **config-api** (`router.py`): `_require_config_write(namespace)` substitui `_require_admin` no PUT/DELETE.
  Mapa namespace→campo: `masking`/`audit_policy`→`config.masking`; `webchat`/`sms`/`whatsapp`/`voice`/`webrtc`
  →`config.canais`; **default→`config.plataforma`** (Platform é o editor catch-all). Verificação HS256 em
  **stdlib** (`hmac`/`hashlib`, sem dep nova) com `config.jwt_secret` (env `PLUGHUB_CONFIG_JWT_SECRET`, =
  segredo da auth-api). Preserva a postura original (`admin_token` vazio = auth desabilitada).
- **compose**: `PLUGHUB_CONFIG_JWT_SECRET=changeme_auth_jwt_secret_demo_32c` na config-api.
- **platform-ui** `config-hooks.ts`: `putConfig`/`deleteConfig` ganham `accessToken?` opcional → mandam
  `Authorization: Bearer` quando presente, senão `X-Admin-Token` (não-quebra os outros ~5 consumidores).
- **Platform** (`ConfigPlataformaPage` + `NamespaceEditor`/`SentimentBandsEditor`/`RoutingSkillsManager`) e
  **Masking** (`MaskingPage`): caixa de admin-token removida; escritas passam o `session.accessToken` na
  posição Bearer. Demais telas de config (Channels, Billing, Dashboards, Skills) seguem em admin-token até
  suas fatias (dual cobre).
- **Smoke** `infra/test/smoke_config_write_auth.sh`: admin-token 200/401; Bearer plataforma 200 em namespace
  default e 403 em masking (campo errado); Bearer masking 200 em masking/audit_policy e 403 em default; sem
  grant 403; sem credencial 401. Limpa as chaves de teste.

---

## G-PROBE platform-wide — slice auth-api (2026-06-26): admin-token → Bearer+ABAC `config.usuarios`

Primeira fatia da migração platform-wide do anti-padrão "caixa de admin-token na UI" para autorização pelo
JWT do operador + ABAC. **auth-api** (gestão de usuários/permissões/grupos) deixa de aceitar `X-Admin-Token`
e passa a exigir **Bearer + ABAC `config.usuarios`** (STRICT, sem fallback de admin-token; decisão da sessão).

- **auth-api** (`router.py` + `groups_router.py`): novo gate `_require_config_usuarios(write)` (decode do
  Bearer via `_bearer_claims` + `_check_config_field` em `module_config.config.usuarios`); GET → `read_only`,
  mutações → `read_write` (grant-first, ausência = 403). Todos os `Depends(_require_admin)` substituídos;
  `_require_admin` (e o caminho X-Admin-Token) **removido** dos dois routers. `groups_router` importa o gate de
  `router`.
- **seed_auth.py**: bootstrap não usa mais `X-Admin-Token` — **minta um Bearer HS256 de bootstrap** (stdlib
  `hmac`/`hashlib`, sem pyjwt) com `config.usuarios:read_write`, assinado com `AUTH_JWT_SECRET` (default =
  `changeme_auth_jwt_secret_demo_32c`, bate com o `PLUGHUB_AUTH_JWT_SECRET` da auth-api). Resolve o
  chicken-and-egg do bootstrap sob strict.
- **platform-ui** `AccessPage` + `GroupsPage`: removida a caixa de admin-token; `authHeaders` envia
  `Authorization: Bearer` (token = `session.accessToken`); as listas (users/groups) **passam a carregar no
  login** (antes só após digitar o token — bug reportado). `Check` mantido (usado noutro ponto).
- **Smoke** `infra/test/smoke_config_usuarios_auth.sh`: minta Bearer rw/ro/sem-grant no container; assере
  GET 401(sem)/200(ro,rw)/403(sem grant)/401(X-Admin-Token sem fallback); POST 403(ro)/201(rw); grupos idem.
- **Follow-ups**: (a) `auth-api/tests/test_router.py` usa X-Admin-Token → quebrado pelo strict (refresh = TODO,
  análogo ao test_router do evaluation-api); (b) `PLUGHUB_AUTH_ADMIN_TOKEN`/`AUTH_ADMIN_TOKEN` no compose viram
  vestigiais (cleanup); (c) demais telas (config-api Platform, agent-registry Skills, Avaliações/adjudicate)
  seguem no TODO platform-wide.

---

## G-PROBE fase 2 (cleanup UI, 2026-06-26) — admin-token removido + Bearer nas listas

Fecha o cleanup de UI do G-PROBE: a `CampaignsPage` não tem mais o input de admin-token (todas as ações de
ops já usam o Bearer do operador via `_require_service_or_eval_write`), e os consumidores de lista que ainda
não mandavam Bearer passam a mandar.

- **`CampaignsPage.tsx`**: removidos `adminToken` state, o `<input type=password>` do sidebar e a prop
  `adminToken` de `CreateModal`/`CurationSamplingRulesDetailPanel`. As curation rules
  (`saveCurationSamplingRules`) agora usam `accessToken` (Bearer). i18n `campaigns.sidebar.adminTokenPlaceholder`
  removido de en + pt-BR (a chave `filters.adminTokenPlaceholder`, ainda usada na AvaliaçõesPage, permanece).
- **`evaluation-hooks.ts`**: `saveCurationSamplingRules` → `bearerHeaders`; `useCurationSamplingRules`,
  `useInstances`, `useContestations` ganham `accessToken?` opcional e mandam `Authorization: Bearer` quando
  presente (forms/campaigns/rubric/results/curations já mandavam). Degradação graciosa do gate any-of cobre
  chamadas sem token.
- **`AvaliacoesPage.tsx`**: `useContestations(TENANT, result_id, session?.accessToken)`.
- **Nota**: `saveCurationSamplingRules` faz `PUT /campaigns/{id}/sampling-rules` (coleção), que NÃO existe no
  backend (rotas são POST + PUT/DELETE por `{rule_id}`) → 405 pré-existente, fora do escopo do G-PROBE; o
  header trocou p/ Bearer por consistência.

---

## G-PROBE fase 2 (slice caller-wiring, 2026-06-26) — credencial de serviço ENFORCED no demo

Ativa a credencial de serviço do slice backend, wirando os callers e provisionando o token no demo.

- **Provisionamento**: `PLUGHUB_EVALUATION_SERVICE_TOKEN=changeme_eval_service_token_demo` no
  `docker-compose.demo.yml` para `evaluation-api` e `mcp-server-plughub` (+ `EVALUATION_API_URL` no
  mcp-server, que faltava). Com o token setado, `_require_service`/`_require_service_or_eval_write` passam a
  ENFORÇAR no demo.
- **Backend caller**: `mcp-server-plughub` `evaluation_pre_review_submit` envia `X-Service-Token` (lido de
  `PLUGHUB_EVALUATION_SERVICE_TOKEN`, header omitido quando vazio) + fallback `EVALUATION_API_URL` p/ o base.
  É o ÚNICO caller HTTP backend de endpoint service-gated — o avaliador real publica `evaluation.completed`
  por **Kafka** (não HTTP `/ingest`), e os scanners de dispatch chamam a função internamente (não o endpoint).
- **UI bridge** (`platform-ui/evaluation-hooks.ts` + `CampaignsPage.tsx`): `seedSyntheticEvaluations`,
  `flushSyntheticEvaluations`, `dispatchCampaign` passam a enviar o **Bearer do operador**
  (`session.accessToken`) em vez de `X-Admin-Token` → `_require_service_or_eval_write` aceita via ABAC
  `formularios:rw` (sem segredo no frontend). O input de admin-token da `CampaignsPage` fica vestigial
  (remoção = cleanup UI, follow-up).
- **Smoke**: `infra/test/smoke_gprobe_service_auth.sh` — login admin (Bearer) + service token; assере os 3
  gates: `/dispatch/scan` (401 sem token / 200 com X-Service-Token / 401 com X-Admin-Token = sem fallback);
  `/campaigns/{id}/dispatch` (401 sem cred / 200 service / 200 Bearer); `/forms` (200 anônimo / 200 Bearer /
  403 Bearer sem grant evaluation, JWT mintado no container).
- **Achado registrado (TODO §G-PROBE)**: os ~15 e2e legados de eval já estavam **vermelhos pela Fase 1**
  (criam form/campanha sem Bearer; `create_form/create_campaign` exigem `formularios:rw`) — repair (Bearer no
  setup + `X-Service-Token` nos calls gated) fica como follow-up; o smoke dedicado cobre o G-PROBE no intervalo.

---

## G-PROBE fase 2 (slice backend, 2026-06-26) — credencial de serviço + leituras de lista fechadas

Fecha o gap de auth dos endpoints de **sistema/agente** da evaluation-api (header-only `X-Tenant-ID`/
`X-User-ID` ou `_require_admin`, sem JWT\ABAC) com uma **credencial de serviço** strict, e fecha as
**leituras de lista** compartilhadas com gate any-of. Decisões da sessão: gate de serviço **strict** (sem
fallback p/ admin-token); UI usa **Bearer+ABAC** (sem segredo no frontend); **slice backend-first** (só
evaluation-api — wiring dos callers + migração da UI ficam p/ slices seguintes).

- **`config.service_token`** (env `PLUGHUB_EVALUATION_SERVICE_TOKEN`, default vazio = no-op/demo aberto,
  espelha `admin_token`).
- **`_require_service`** (strict `X-Service-Token`) nos endpoints puramente sistema/agente:
  `ingest`, `instances/claim`, `instances/{id}/expire|skip|mark-error`, `dispatch/scan` (router);
  `instances/{id}/pre-review`, `instances/{id}/ai-review`, `calibration-notes/{id}/publish`
  (contestation_router). Os que estavam em `_require_admin` migraram p/ serviço.
- **`_require_service_or_eval_write`** (serviço OU Bearer+ABAC `evaluation.formularios` read_write) nas ações
  de **ops disparáveis pela UI**: `campaigns/{id}/dispatch`, `campaigns/{id}/backfill`, `admin/seed-synthetic`,
  `admin/flush-synthetic` (router); `campaigns/{id}/sampling-rules` create/update/delete (contestation_router).
- **`_require_any_evaluation`** (any-of dos campos `evaluation`, Bearer opcional, degradação graciosa:
  sem token/legado/admin → permite; JWT com `module_config` sem nenhum grant evaluation → 403) nas
  **leituras de lista** compartilhadas: `list_forms`, `list_campaigns`, `list_rubric_templates`,
  `list_instances`, `list_contestations` (router); `list_calibration_notes`, `list_sampling_rules`
  (contestation_router). `_can_view_transcript` refatorado p/ reusar `_has_any_evaluation_access` (mesma semântica).
- **Testes**: `tests/test_gprobe_phase2.py` (funções puras, sem DB) — `_require_service` (strict, no-op quando
  vazio, sem fallback de admin), `_require_service_or_eval_write` (serviço × Bearer rw × read_only/outro campo),
  `_has_any_evaluation_access`/`_require_any_evaluation` (any-of + degradação).
- **Postura demo**: `service_token` fica **não-provisionado** neste slice → gates de serviço no-op (demo segue
  funcional, sem quebra de UI). Provisionar o token + wiring (orchestrator-bridge/agentes/workers/seed/e2e) +
  migrar CampaignsPage p/ Bearer (remover input de admin-token) = follow-up.
- **`contestation_router._require_admin` removido** (sem uso após a migração de `ai-review` p/ serviço).

---

## S2.4 — motor de review por workflow APOSENTADO (decisão 2026-06-25) — só docs/anotações

Decisão de arquitetura, não bug. O contest→review→finalize **canônico já existe** no **Arc 13 REST**
(`contestation_router`: `file_contestation` → `submit_review` → `finalize_evaluation`, que emite
`evaluation_finalized` — fonte única dos relatórios de qualidade). O "motor por workflow" do Arc 6
(`campaign.review_workflow_skill_id` / `skill_revisao_treplica_v1` + `agente_revisor_v1`) é **paralelo e
inerte em produção**: nada no backend o dispara (config morta lida só pela UI; único trigger = e2e cenário
28, opt-in `--workflow-review`, fora da suíte default 01–18), e o consumer reativo de `workflow.events`
termina em `lock_result` (NÃO finaliza). Decisão: **Arc 13 é o contrato único; o motor por workflow é
legado/superseded.**

- **Distinção preservada:** isto aposenta só o **uso** que o módulo de avaliação fazia do workflow como motor
  de review — NÃO o motor genérico (skill-flow-engine + workflow-api + `workflow.events`), que segue como infra
  do PlugHub usada por outras frentes.
- **Anotações de legado:** CLAUDE.md (§ Arc 6 "Workflow as review motor"), `docs/arcos/arc6-evaluation.md`
  (banner na seção), `evaluation-api/main.py` (`_on_workflow_event` docstring), e o cabeçalho do cenário e2e 28.
- **Validado:** cenário 28 não está deprecated, mas é opt-in e fora da suíte default → aposentar não quebra a
  regressão padrão.
- **Follow-up opcional (remoção física):** consumer reativo, coluna/seletor `review_workflow_skill_id`, skills
  `skill_revisao_*`/`agente_revisor_v1`, cenário 28 — slice próprio (raio de teste no 28).

---

## Recon + re-validação G-FIN/G-TIMEOUT (2026-06-25) — só validação/docs

Recon do shakedown do Arc 13 (TODO §"Shakedown pós-submit") contra o código atual: **G-FIN e G-TIMEOUT já
estavam resolvidos** pelos T1–T11 (2026-06-19), depois das notas do shakedown (2026-06-17) — caso recorrente
de "TODO atrás do código". `finalize_evaluation` é o ponto único (idempotente) que grava `final_score` +
emite `evaluation_finalized`, chamado por todos os caminhos terminais (ingest IA, ai_review, `submit_review`
humano, deadline scanner); `_run_deadline_scanner` (60s) wired no startup.

- **Validação E2E**: novo smoke `infra/test/smoke_eval_finalize_timeout.sh` — reabre um resultado finalizado
  como vencido (`open`, `deadline_at` no passado) e confirma que o scanner o re-finaliza
  (`timeout_contestation`/`uncontested`, `final_score`+`finalized_at` gravados) + evento `evaluation_finalized`
  no ClickHouse. Verde (finaliza em ~40s). Prova G-TIMEOUT e o núcleo de finalização compartilhado com G-FIN.
- **Docs**: TODO §Shakedown atualizado — G-FIN/G-TIMEOUT marcados ✅ RESOLVIDOS (ref. T3/T4/T11); nota
  "Confirmado ao vivo 2026-06-17" marcada obsoleta. **Gaps remanescentes (abertos)**: G-S2.4 (motor de review
  por workflow finaliza, hoje só `lock_result`), G-PROBE (auth ABAC nos endpoints de escrita), G-UI (grants
  ABAC `evaluation.revisar`/`contestar` p/ as ações surgirem na tela).

---

## Isolamento do substrato de avaliação por `origin` — ARCO COMPLETO (2026-06-25)

Resolve o concern §9(c) do Quality Ingest (reavaliação/importação misturavam com produção no substrato
compartilhado). Direção e racional em [`docs/adr/adr-quality-substrate-isolation.md`](docs/adr/adr-quality-substrate-isolation.md)
(Status: **Aceito — implementado**). Discriminador de procedência **por-sessão** (`origin`: `live|import|reeval`,
default `live`), sem duplicar o substrato vivo nem segundo banco. Validado E2E (`infra/test/smoke_origin_reeval.sh`):
reavaliação re-emitida surge isolada como `reeval`; produção (`live`) intacta.

- **Passo 1 — coluna `origin` (DDL aditivo)**: `origin String DEFAULT 'live'` nas tabelas CH
  `analytics.sessions/segments/messages` (migrations idempotentes em `clickhouse.py`) e PG `session_stream_events`
  (`stream_persister.py`). Sem backfill (default cobre legado/vivo). Distinta de `origin_session_id` (Arc 19).
- **Passo 2 — analytics-api consumer**: `models.origin_from_source(source)` (`external_import`→import,
  `internal:reeval`→reeval, demais→live) carimba `origin` nas linhas de sessions/segments/messages; row-builders
  + `_*_COLS` em `clickhouse.py`.
- **Passo 3 — session-replayer consumer Y**: `insert_records` grava `origin` (default `live` no Persister vivo);
  `ImportStreamConsumer` deriva o `origin` do source e reconstrói o stream de **ambos** import e reeval (gate
  `_REBUILD_SOURCES`).
- **Passo 4 + 4b — report layer**: helper único `_apply_origin_scope(conditions, origin='live')` em
  `reports_query.py`; filtro **default `live`** (garantia de correção / defense-in-depth) nas funções de
  substrato (sessions, segments, agent performance/daily, session complexity, pools volume/queue) **e na bancada**
  (compare/cross + atribuição `_session_agent_attribution_sql` + todas as lentes que leem segments). Override
  programático via kwarg `origin`.
- **Passo 5 — evaluation-api sampling**: `_allowed_origins` (filtro **opcional**, default `{'live'}`) no
  `_passes_filters` (cobre `should_sample` + `should_sample_quota`); `_sample_on_close` carimba o `origin` do
  evento no `meta`. Campanha de produção mira `live`; reavaliação seta `origin:"reeval"` em `sampling_rules`
  (JSONB, sem schema novo) → fim do cross-fire.
- **Passo 6 — endpoints (+ UI reservada)**: `origin` como query-param validado (`^(live|import|reeval)$`,
  default `live`) em 9 endpoints `/reports/*`. **Decisão de UX (2026-06-25):** o seletor de origem **NÃO** é
  exibido nas telas de Analytics operacionais (Sessions/Pools/Agents) — origem é contexto de qualidade, não
  dropdown operacional, e a re-emissão é detalhe de implementação. Toda a UI operacional mostra **produção**
  (default do backend). O componente `OriginSelector` + i18n `origin.*` (en/pt-BR) e o campo opcional
  `ContactFilters.origin` ficam **reservados** para uma futura superfície de qualidade contextual (onde a
  origem é o contexto em que o usuário já está, não uma escolha ad hoc).
- **Fix de rótulo (mapper quality-ingest + gate consumer Y)**: o mapper achatava todo source no marker global
  (reeval virava import). Corrigido: preserva `internal:reeval`, normaliza sources externos (ex. `ccaas:genesys`)
  ao marker de importação; consumer Y reconstrói stream de ambos.
- **Testes**: `_apply_origin_scope`/derivação/stamping no report layer (test_reports + test_consumer),
  sampling origin (test_sampling), consumer Y reeval (test_import_stream_consumer), mapper (test_mapper).
- **Não-objetivos (fase 2, backlog)**: partição CH `PARTITION BY (toYYYYMM(date), origin)` (exige migração
  versionada, não in-place) + campo `pool.origin_class` (ortogonal a `agent_kind`).

---

## Quality Ingest · R13d — exportador interno (reavaliação) — ARCO COMPLETO (2026-06-25)

Fecha a reavaliação interna: lê o histórico da própria plataforma e o re-emite pela MESMA porta
do importador externo, sem código de avaliação divergente. Spec: `docs/arcos/quality-ingest.md` §7.

- **`packages/quality-export/`** (FastAPI:3852, deps só `httpx`, **ClickHouse-only**): a tabela `messages`
  já tem o transcript mascarado (`original_content` nunca sai); `segments`/`sessions` têm os metadados.
  `POST /v1/export/sessions {tenant_id, session_ids, source?}` lê `sessions`+`segments`+`messages` (`FINAL`),
  reconstrói `ingestion_event_v1` (**inverso do mapper**: sessions→contact.opened/closed, segments→
  participant.joined/left filtrando primary/specialist, messages→message.sent com author_role revertido) e
  faz POST no quality-ingest. `external_contact_id`=session_id original → novo session_id de reavaliação
  (sem colisão). É **cliente do contrato** (lê histórico, não toca infra de eventos interna).
- **Pool**: reusa o original (`source="internal:reeval"`, sem source_map → pass-through). Pool de revisão
  dedicado sai **de graça do R13c** (cadastrar source_map p/ `internal:reeval`). Compose: serviço
  `quality-export` (CH + quality-ingest deps).
- **Testes**: 9 unit do builder puro + round-trip pelo mapper do quality-ingest; smoke
  `smoke_quality_export.sh` (import → export → re-eval com pool/transcript originais + sampling).

**Arco Quality Ingest COMPLETO (R13a–R13d):** contrato `ingestion_event_v1` → módulo produtor →
consumer Y (stream durável) → mapa por source → exportador interno. Externos (CCaaS) e o próprio histórico
entram no MESMO pipeline de avaliação. Concern aberto (§9): mistura tráfego-vivo × reavaliação no pool
original (mitigável via source_map → pool dedicado).

---

## Quality Ingest · R13c — mapa identidade/pool/versão por `source` (2026-06-25)

Traduz ids EXTERNOS (pool/agente) → INTERNOS antes de emitir os eventos canônicos, para que
analytics, sampling e consumer Y só vejam identidades internas. Spec: `docs/arcos/quality-ingest.md` §6.

- **config-api**: namespace `quality_ingest`, key `source_map` (seed-if-absent, default `{}`). Um JSON
  keyed por `source`: `{pools: {ext→int}, agents: {ext_id→{kind, user_id | skill_id+deploy_version}}}`.
- **quality-ingest**: `SourceMapClient` (`GET /config/quality_ingest/source_map?tenant_id=`, cache TTL 60s/tenant,
  degradação graciosa → `{}` = pass-through). O `mapper` aplica a tradução **antes da emissão**: `pool_id`
  traduzido (carimbado também no close via segmento primário), humano → `user_id`, IA → `flow_id`+`deploy_version`;
  `agent_type_id` deixa de carregar o `external_agent_id` (sem vazamento). Pass-through quando não mapeado.
  Wiring: setting `config_api_url` + dep `httpx`; compose `PLUGHUB_QUALITY_INGEST_CONFIG_API_URL` + depends_on config-api.
- **Testes**: 4 unit de tradução (pool/humano/IA/pass-through) no `test_mapper.py`; smoke
  `smoke_quality_ingest_sourcemap.sh` (PUT do map em tenant fresco → ids externos viram internos em
  `analytics.segments` + sampling dispara sob campanha que mira o pool interno).

---

## Quality Ingest · R13b — consumer Y (stream durável p/ importados) (2026-06-25)

Fecha o ReplayContext p/ contatos importados: reconstrói o stream durável
`session_stream_events` (PG) a partir dos eventos canônicos, sem depender do stream Redis
(inexistente p/ importados). Spec: `docs/arcos/quality-ingest.md` §4.

- **session-replayer / `StreamPersister` refatorado**: extraídos `insert_records()` (ÚNICO ponto de
  escrita da tabela, idempotente por `event_id`) + `recompute_deltas()` (janela `LAG` por timestamp,
  ordem-independente). `persist()` (Persister vivo) passou a usá-los → mesmo escritor, **zero drift**.
- **`ImportStreamConsumer` (consumer Y)** (`import_stream_consumer.py`, grupo `session-replayer-import`,
  `earliest`): consome `conversations.events` + `conversations.participants`, **gated `source=external_import`**
  (tráfego vivo ignorado), mapeia 1:1 ao vocabulário de stream interno (`contact_open→session_opened`,
  `message_sent→message`, `contact_closed→session_closed`, `participant_*`) e grava via `insert_records`.
  `delta_ms` finalizado no fechamento por `recompute_deltas`; `original_content=null` (cego por construção);
  `author.role` `customer` preservado, `agent→primary`. Wired como 3º task no `SessionReplayerConsumer`.
  Convive com o Persister vivo (que vira no-op p/ importados — Redis vazio). Hydrator→Replayer produzem
  ReplayContext.events idêntico ao interno.
- **Testes**: 10 unit (mapper + handler, standalone/pytest); smoke `smoke_quality_ingest.sh` §4b
  (`session_stream_events` message/open/close rows p/ a sessão importada + `original_content` NULL).

> **Residual registrado** (§9): `session_meta`/`participants`/`sentiment` do ReplayContext ainda caem em
> default p/ importados (Replayer os lê do Redis, ausente); o transcript — núcleo da avaliação — fica completo.
> Correlação por-requisição do quality-ingest segue aberta (R13b não a muda; consumer Y é ordem-independente).

---

## Quality Ingest · R13a-1 + R13a-2 — leitor de histórico plugável (2026-06-24)

Interface aberta de eventos (anti-corrupção) que faz históricos **externos** (CCaaS) e a
**reavaliação interna** entrarem no MESMO pipeline de avaliação (sampling → ReplayContext →
avaliador → analytics), sem o importador tocar a infra interna. Spec: `docs/arcos/quality-ingest.md`.

- **R13a-1 — schemas `ingestion_event_v1`** (`@plughub/schemas`): família discriminada (`contact.opened`,
  `participant.joined`, `message.sent`, `participant.left`, `contact.closed`) com mandatório/opcional do §3,
  vocabulário próprio do módulo (decoupled dos enums internos), `deriveIngestionEventId` p/ idempotência.
  Exportada no `index.ts`. 39 testes (vitest) + typecheck OK.
- **R13a-2 — `packages/quality-ingest/`** (Python FastAPI, porta 3850, produtor puro): endpoint aberto
  `POST /v1/ingest/events` (header `X-Tenant-ID`); **masking net-pass** (port de `DEFAULT_MASKING_RULES`);
  `session_id`/`segment_id` determinísticos (idempotentes); **mapper** correlaciona o stream por
  `external_contact_id` e emite os eventos canônicos internos que os consumers já entendem —
  `conversations.events` (contact_open/message_sent/contact_closed), `conversations.participants`
  (campo `type` underscore), `agent.lifecycle` `agent_done`, e `conversations.session_closed`
  (dispara sampling). Carimba `pool_id` do segmento primário no close e `source:"external_import"`
  (gate do consumer Y / R13b). 23 testes (pytest) + smoke `infra/test/smoke_quality_ingest.sh`
  (contato externo → ClickHouse sessions/messages/segments + evaluation.instance agendada sob campanha).

> **Decisões fechadas:** interface = stream de eventos (não lote); **pool é a unidade** (eventos carimbam
> `pool_id`, não `campaign_id`); tier-2 de IA indisponível p/ externo. **Limitação registrada:** correlação
> por contato é por-requisição (durabilidade cross-request = reconstrução de stream do R13b).

---

## Skill Versioning · Fase E — cleanup (2026-06-24)

Higiene final do arco — sem mudança de comportamento.

- **`SkillVersionSlot` aposentado**: o ciclo de 3 slots POR SKILL (Task #31) era duplicação do
  `PoolSkillSlot` (autoritativo, por-pool) e estava órfão (nenhuma UI/serviço usava — confirmado via
  grep TS+Python). Removido: rota `skillSlotsRouter` desmontada (`app.ts`), model + relação
  `Skill.version_slots` removidos do schema (`db push` dropa `skill_version_slots`), `skill-slots.ts`
  vira stub. O `SkillSlot` enum permanece (usado por `PoolSkillSlot`). Deploy segue pool-centric
  (`/v1/pools/:id/slots` + `SkillDeployment`). Verificado: per-skill slots → 404; pool slots → 200.
- **`version_policy`/`exact_version` depreciados** (`SkillRefSchema`): vestigiais (não há resolução por
  versão em runtime). Mantidos por retrocompat (SDK/agent_type declaram), marcados `@deprecated`.

**Arco Skill Versioning & Deploy COMPLETO** (A→D→B→C→E): `skill_id` slug estável + `version` rótulo;
editor "Novo skill"/Save; anti-vazamento (editor=rascunho, produção=slot `current` do pool, P1);
versão = deploy (`set_at`, promote grava `SkillDeployment`); cleanup. Spec:
`docs/product/skill-versioning-deploy-spec.md`.

> **Achado pré-existente (fora do arco):** o `prisma db push --accept-data-loss` do agent-registry roda
> no boot no schema `public` do `plughub_demo` e clobbera tabelas de OUTROS serviços ali (ex.:
> `session_stream_events`, stream durável/R5). Footgun de DB compartilhado — a cada restart do
> agent-registry. Não afeta este arco; registrar como concern de infra.

---

## Skill Versioning · Fase C — versão = deploy (C-full) (2026-06-24)

A identidade de versão (analytics/epoch) deixa de ser o `skill.version` manual e passa a ser o
**deploy** — automático, por-pool, por-evento. Escopo C-full (o promote unifica slot + append-log).
Spec: `docs/product/skill-versioning-deploy-spec.md` §4.

- **agent-registry** (`pool-slots.ts` promote): o promote **É o deploy** → grava um `SkillDeployment`
  (`deployed_at = set_at` do slot, `version` = rótulo `skill.version`, `pool_ids=[pool]`,
  `yaml_snapshot`, `notes:"promote"`) além de setar o slot `current`. Dá ao epoch o rótulo + markers e
  unifica os dois mecanismos. Validado: promote em `demo_ia` criou o `SkillDeployment` (deployed_at = momento).
- **orchestrator-bridge**: carimba `segments.deploy_version = set_at` do slot `current` (cache
  `_pool_deploy_version_cache` por pool, invalidado no `registry.changed(pool)`); fallback `skill.version`
  (pools não migrados). A identidade casa com `SkillDeployment.deployed_at`.
- **analytics-api** (`_attach_epoch_deploy_order`): casa o ponto-versão por **`deployed_at`** (Fase C:
  `deploy_version`=set_at) **e** por rótulo (legado `skill.version`), expõe `version_label` p/ display.
  `_norm_ts` (precisão segundos). +1 teste (`test_epoch_fase_c_timestamp_version_maps_to_label`). **11/11.**
- **platform-ui** (`DeployEpochChart`): eixo usa `version_label` (rótulo); timestamp no tooltip. Dois
  promotes do mesmo rótulo = dois pontos, desambiguados pela data.

Transição: dados legados (deploy_version = "1.0"/"2.0") seguem renderizando por rótulo; deploys novos
(via promote) usam o timestamp. `SkillVersionSlot` + `version_policy` vestigiais → Fase E.

---

## Skill Versioning · Fase B — anti-vazamento edição→produção (P1) (2026-06-24)

Corta o vazamento "salvar = produção": editar no editor não pode mais alterar o que roda em produção.
Decisão **revisada para P1** ao descobrir que o deploy real é **por pool** (slots `next→current→previous`),
o que tornava o P2 (`skill.flow` global) incoerente (promover num pool vazaria para todos os pools que
compartilham o skill). Spec: `docs/product/skill-versioning-deploy-spec.md` §4.

- **agent-registry**: `Skill.flow_draft` (rascunho, via `prisma db push`). PUT do **editor** escreve
  `flow_draft` (NUNCA `flow`/`flow_model`); canal **sync/deploy** (`x-skill-publish:true`) escreve
  produção (`flow`+`flow_model`, limpa draft). `POST :id/deploy` promove `flow_draft→flow`. Pool-slots
  `_fetchSkillSnapshot` captura `flow_draft ?? flow` (o set-next pega a edição). `_formatSkill` expõe
  `unpublished_draft`.
- **orchestrator-bridge**: **produção = snapshot do slot `current` do POOL**. `get_pool_current_flow`
  (`GET /v1/pools/:id/slots`→`current.yaml_snapshot`, cache por pool); `resolve_flow_for_agent` prefere o
  slot do pool, **fallback** `skill.flow` (retrocompat — pools não migrados seguem rodando); `pool_id`
  propagado na ativação; cache invalidado no `registry.changed(pool)` (promote/rollback). `registry_syncer`
  publica via `x-skill-publish`.
- **platform-ui**: editor carrega o rascunho (`flow_draft ?? flow`) + badge "rascunho não publicado".

Verificado: editor cria skill com **produção vazia** (`flow=null`, `flow_draft` set, `deploy_status=draft`)
→ não vaza; syncer publica produção dos skills existentes (sem regressão); skill IA `skill_wrapup_v1`
executa normal (`outcome=resolved`) via fallback. Versão do segmento segue resolvida do skill (fallback
analytics) — vira id-do-deploy na Fase C.

---

## Skill Versioning · Fase D — affordances do editor (Novo skill + Save) (2026-06-24)

Fecha o R14(a/b) dentro do arco Skill Versioning. Criação de skill agora é descobrível e o Save
deixa de "parecer travado".

- **SkillFlowsPage**: botão **"+ Novo skill"** (limpa seleção, carrega template, marca `isNew`); estado
  `isNew` + `canSave = !saving && (isModified || isNew)` habilita o Save no template em branco (antes
  `savedValue==editorValue` → Save parecia desabilitado). Hint "Edite para habilitar o Salvar" quando
  desabilitado (skill existente sem alteração). `isNew` resetado em select/save/delete. `BLANK_TEMPLATE`
  atualizado: `skill_id: skill_novo` (slug estável), **sem `version`** (opcional desde a Fase A).
- **i18n** `agentFlow` en+pt-BR: `editor.new`, `editor.newSkill` (badge "não salvo"), `editor.saveHintView`.

Verificado visualmente: botão + badge + Save habilitado no modo novo; Save desabilitado+hint ao só
visualizar. Não toca runtime/analytics.

---

## Skill Versioning · Fase A — skill_id estável + version opcional (2026-06-24)

Primeira fase do arco Skill Versioning & Deploy (`docs/product/skill-versioning-deploy-spec.md`):
desacopla a identidade do skill da versão. `skill_id` passa a ser um **slug estável** (`^skill_[a-z0-9_]+$`,
sem exigir `_v\d+` — legados seguem válidos por casarem o slug); o campo `version` vira **opcional**
(rótulo livre, nunca identidade — versão é do DEPLOY do pool).

- **schemas** (`skill.ts`): regex `skill_id` relaxada; `version` `z.string().optional()` (era required
  `^\d+\.\d+$`). Testes `skill.test.ts` atualizados (id sem `_v` aceito + retrocompat `_v` + version
  opcional). **99/99 verde.**
- **workflow-api** + **orchestrator-bridge/registry_syncer**: `_SKILL_ID_RE` relaxada idem.
- **agent-registry** (`skills.ts`): 409 reescrito ("edite/atualize ou outro nome — versões nascem no
  deploy"); create/PUT default `version ?? ""` (coluna NOT NULL). Validado: PUT `skill_teste_estavel`
  (sem `_v`, sem `version`) passa a validação de id/version.
- **CLAUDE.md**: convenção de `skill_id` atualizada (slug estável).

Não toca runtime/analytics (Fases B/C). `version_policy`/`exact_version` seguem vestigiais.

---

## Arc 6 Fase 2 · micro-fatia 1b — Cobertura do epoch: provisória + pendentes (Opção II) (2026-06-24)

Fecha a pendência do núcleo epoch (R15a/R15b): o ponto de cada versão mostrava só a média
**finalizada**, sem dizer se o número estava assentado. Decisão (Opção II): exibir, por versão, a
nota **provisória** (só avaliações já pontuadas) + o **backlog** (instâncias amostradas não
finalizadas) — convergência = sinal de confiança. Fonte exata por versão = `evaluation.instances`
(Postgres, `deploy_version` do R9d), que o ClickHouse não tem.

**Fatia A — backend.** evaluation-api: `db.deploy_coverage()` + `GET /v1/evaluation/reports/deploy-coverage`
→ por `(pool, deploy_version)`: `pending_n` (status ∈ scheduled/assigned/in_progress/under_review/
contested), `provisional_n`/`provisional_avg` (`results.normalized_score` 0–1, **só pontuadas**),
filtro janela (`instances.created_at`, parse→datetime UTC; asyncpg exige datetime em timestamptz) +
pool. Pool = `COALESCE(c.evaluation_pool_id, c.pool_id)`. analytics-api: `coverage_client` (config
`evaluation_api_url`, cache 60s, degradação→`[]`) + `_attach_epoch_coverage` anexa
`provisional_avg/_n` + `pending_n` a cada ponto-versão do epoch (match por `(pool, versão)`). compose:
`PLUGHUB_EVALUATION_API_URL` no analytics-api. Testes `test_deploy_lens.py` +2 (overlay anexa; url
vazia→sem overlay). **10/10 verde**; endpoint validado com dado real (sac_ia v1.0: provisional 0.656).

**Fatia B — UI.** `DeployEpochChart`: linha **tracejada provisória** por pool (mesma cor, sobreposta à
finalizada sólida) + selo **"Pendentes de fechamento · <pool> v<versão> +N"** sob o gráfico; tooltip
enriquecido (oficial + provisória + pendentes + data de deploy). i18n `bench.deploy.{tipFinal,
tipProvisional,tipPending,pending}` + legenda en+pt-BR. Build (tsc) verde; QA via seed (8×v2.0
`in_progress` → "+8 pending"; v1.0 com provisória 0.66 sob a finalizada 0.73).

**Seed:** `infra/test/seed_epoch_demo.sh` estendido — além das finalizadas (ClickHouse), insere
instâncias pendentes da v2.0 no Postgres (reusa campanha existente do pool, idempotente). **Arc 6 Fase 2
completo** (sem pendências).

---

## R15a + R15b — Núcleo epoch/versão do Arc 6 Fase 2 (lente deploy `mode=epoch`) (2026-06-24)

Destravado pelo R9 (carimbo `deploy_version` no segmento). A lente `deploy` ganha um **modo
epoch**: série de qualidade OFICIAL bucketizada por **`deploy_version`** (eixo X = versões), em vez
de por dia. Decisões fechadas em `arc-evaluation-metrics-methodology.md` §IV.8.

**R15a — query** (`analytics-api`): novo parâmetro `mode=daily|epoch` em `query_agents_compare` +
rota `GET /reports/agents/compare` (epoch só na lente deploy; demais ignoram). `_compare_deploy_epoch_lens`:
JOIN **exato** `evaluation_finalized.segment_id → segments.segment_id` (sem inferência por timeline —
o R9 carimbou `deploy_version` no segmento), `GROUP BY pool_id, flow_id, deploy_version`,
`avg(final_score)` (Oficial) + `n` + `min(timestamp)` (`first_seen`, proxy de ordenação). Camada async
`_attach_epoch_deploy_order` resolve `deployed_at` por `(skill_id, version_label)` do agent-registry
(`fetch_pool_deployments`) e **reordena a série por deployed_at** (fallback `first_seen` em degradação).
Domain ai (`agent_type != 'human'`), só segmentos com versão carimbada (`deploy_version != ''`).
`meta.mode=epoch`, `min_sample=30`. Sem média da frota no epoch (forçado). Testes: `test_deploy_lens.py`
+3 (epoch: agrupamento por versão + ordem por deployed_at + meta; multi-pool união uma-curva-por-pool;
degradação registry → fallback first_seen). **8/8 verde.**

**R15b — UI** (`platform-ui` `AgentsBenchPage`): toggle **Diário ↔ Por versão** (só na lente deploy;
estado `deployMode` + sync URL `mode=epoch`). `DeployEpochChart`: eixo X = versões (chave `skill|versão`,
rótulo = versão), uma curva por pool, **multi-pool = união ordenada por deployed_at** (pools que
compartilham skill alinham na mesma versão; skills distintas ocupam pontos próprios). Tooltip custom
(versão + valor + `n` + data de deploy). Esconde a média da frota; flag N<min_sample reaproveitada.
`useCompare` propaga `mode`. i18n `bench.deploy.{modeLabel,modeDaily,modeEpoch,epochLegend,tipN}` en+pt-BR.
Modo diário+markers permanece como visão alternativa (1º corte).

**Verificação:** R15a `test_deploy_lens.py` 8/8 verde; build platform-ui (tsc) verde; QA visual via
`infra/test/seed_epoch_demo.sh` (6×v1.0 + 6×v2.0 de `skill_atendimento_sac_v1`/`sac_ia` com
`deploy_version` carimbado) — o epoch renderiza dois pontos de versão, ordenados pelo `deployed_at`
real do agent-registry (confirmado via API: v2.0 deployed 06-17 < v1.0 deployed 06-20 → v2.0 à
esquerda, priorizando `deployed_at` sobre o fallback `first_seen`), tooltip n+data, aviso N<min_sample.

**Decisão adiada (micro-fatia 1b):** indicador "N pendentes de fechamento" (lag de finalização) — a
série epoch entrega avg+n por versão; o contador de pendentes entra depois (fonte a definir).

---

## R8c — Curadoria cega-primeiro · Slice 5: UI modo cego (CuradoriaPage) — R8c COMPLETO (2026-06-23)

Quinta e última fatia: a superfície do curador. **R8c completo.**

**CuradoriaPage** (`modules/evaluation/CuradoriaPage.tsx`): reviews `mode='blind'` ganham um badge
**Cega** (EyeOff) + um único CTA "Pontuar às cegas" (em vez de Approve/Recalibrate/Bias). O
`BlindScoreDrawer` é **dois painéis**: esquerda = **conversa mascarada** (reusa `useResultTranscript`,
toggle Segmento/Contato, 🔒); direita = **formulário a pontuar** (dimensions→criteria; inputs por tipo
score/boolean/choice + N/A) com a **nota da IA escondida**. "Revelar nota da IA" → `blind-rescore` →
tabela de diff por dimensão (IA × humano, % diff, badge de divergência) + nota geral; "Resolver" →
`blind-resolve` (severity/flag_bias/notas). Re-abrir review já resolvida mostra o reveal (read-only).

**API** (`api/evaluation-hooks.ts`): `getBlindContext`/`blindRescore`/`blindResolve` + tipos
(`BlindContext`/`BlindResult`/`BlindDimensionDiff`/…); `CurationReview` ganha `mode/deadline_at/
expired_at/skill_version`. i18n `curation.blind.*` en+pt-BR (invariante i18n).

**Verificação:** build platform-ui (tsc) verde; QA visual via `infra/test/seed_r8c_blind_review.sh`
(badge Cega + drawer 2-painéis renderizam; conversa + form + reveal/diff). Limitação herdada: o painel
de conversa depende de mensagens persistidas em `analytics.messages` (instâncias demo antigas podem
não ter → "sem mensagens"; alternar Contato).

---

## R8c — Curadoria cega-primeiro · Slice 4: diff → CalibrationNote + calibration.events (2026-06-23)

Quarta fatia: a resolução transforma o desacordo por dimensão em sinal de calibração.

**Endpoint** (`contestation_router.py`): `POST /v1/evaluation/curations/{id}/blind-resolve` — para cada
dimensão em `disagree`, cria uma `CalibrationNote` (texto auto-composto IA vs humano, nota humana como
referência), publica no namespace de conhecimento (`origin=blind_curation`, RAG) e emite
`calibration_note_published`; resolve a review e emite **sempre** `calibration_reviewed`
(`calibration.events`) — alimenta a divergência R8b por `skill_version`. Status via
`scoring.blind_resolution_status`: sem desacordo→`approved` (sem nota), com→`recalibrated` (ou
`bias_flagged` se `flag_bias`). Reusa `create_calibration_note`/`mark_calibration_note_published`/
`resolve_curation_review`/emitters do Arc 13. Guarda: review não-`pending`→409. **Nunca** toca o
resultado imutável.

**Teste:** `tests/test_blind_diff.py::TestBlindResolutionStatus` (4 — puro) → 68 passed. Smoke
`infra/test/test_r8c_blind_resolve.sh`: rescore (3 desacordos) → resolve → `status=recalibrated` + 3
`CalibrationNote`s persistidas → 409 idempotente → `R8C_RESOLVE_SMOKE_OK`.

---

## R8c — Curadoria cega-primeiro · Slice 3: endpoint re-score + reveal/diff (2026-06-23)

Terceira fatia: o curador re-pontua o form às cegas e o sistema revela a nota da IA + diff.

**Endpoints** (`contestation_router.py`): `GET /v1/evaluation/curations/{id}/blind-context` (form a
pontuar + ponteiros de transcript, **sem** as notas da IA; se já re-pontuado, devolve o reveal) e
`POST /v1/evaluation/curations/{id}/blind-rescore` (valida via `validate_criterion_responses`, agrega
humano E IA pela **mesma** `scoring.aggregate_scores` — form = fonte única → diff apples-to-apples,
calcula `compute_dimension_diffs` com `severity_min` da campanha, persiste o artefato, devolve reveal).
Headers: `X-Tenant-ID` + `X-User-ID`. Idempotente (`uq_blind_per_review` → 409).

**Diff puro** (`scoring.py`): `compute_dimension_diffs(ai_by_dim, human_by_dim, severity_min)` →
`[{dimension_id, ai_score, human_score, diff (|ai-h|/10), disagree (diff>severity)}]`; dimensão presente
de um só lado = `na` (sem discordância). **DB** (`db.py`): `get_curation_review`, `create_blind_result`,
`get_blind_result`. A nota cega **nunca** toca o resultado imutável.

**Teste:** `tests/test_blind_diff.py` (8 — puro) → 64 passed. Smoke
`infra/test/test_r8c_blind_rescore.sh`: context (sem nota IA) → rescore (humano = IA com todos os scores
−5) → reveal com 3/3 dimensões `disagree=true` → 409 idempotente → `R8C_RESCORE_SMOKE_OK`.

> 11 falhas pré-existentes em `test_router.py` (mock `AsyncMock` incompleto + `app.state.redis` ausente +
> `422` Pydantic-v2) **confirmadas idênticas no baseline** (`git stash` → 11 failed/35 passed) — alheias ao R8c.

---

## R8c — Curadoria cega-primeiro · Slice 2: amostragem 2-estratos + SLA soft (2026-06-23)

Segunda fatia: o item entra na fila cega no `evaluation_finalized`, **após** o Stage-1.

**Amostragem 2-estratos** (`sampling.py`, puro): `blind_decide(flagged, cfg, instance_id)` escolhe o
`%` do estrato (sinalizado × não-sinalizado) e aplica o gate determinístico por hash do `instance_id`
(reproduzível/idempotente). `sampling_engine.py`: `run_curation_sampling` agora **retorna `flagged`**;
novo `run_blind_curation_sampling` (carrega campanha→`blind_stage_config`, decide, idempotência via
`count_blind_reviews_for_instance`, `skill_version` de `instances.deploy_version`, cria review
`mode='blind'` + `deadline_at` SOFT + `trigger=blind_stage:{stratum}`); `run_curation_and_blind_sampling`
encadeia Stage-1→cego num só task de fundo, fiado no `_finalize_and_emit` (router).

**SLA soft** (`db.py` `expire_overdue_blind_reviews` + `main.py`): o `_run_deadline_scanner` (60s) marca
reviews cegas pendentes vencidas com `expired_at` — **informativo, status segue `pending`**, sem efeito
na avaliação (≠ timeout do revisor de contestação). Idempotente.

**Teste:** `tests/test_sampling.py::TestBlindDecide` (7 — puro) → 56 passed. Smoke
`infra/test/test_r8c_blind_sampling.sh` (host→container): cria 1 review cega (unflagged), idempotente,
soft-expira mantendo `status=pending` → `R8C_BLIND_SMOKE_OK`.

---

## R8c — Curadoria cega-primeiro · Slice 1: schema + config (2026-06-23)

Primeira fatia do Estágio 2 (curadoria cega, §III.4). **Decisões de escopo fechadas** nesta
sessão (atualizam o design original): (1) a nota cega é um **artefato de calibração** — NUNCA
altera `evaluation.results.final_score` nem re-emite `evaluation_finalized`; corrige avaliações
**futuras** via `CalibrationNote`→KB→RAG + divergência (R8b), não esta. (2) Amostragem em **dois
estratos** no `evaluation_finalized`, após o Stage-1: `%` sobre itens sinalizados +
`%` sobre não-sinalizados (o estrato não-sinalizado pega o viés de KB compartilhado). (3) **Mesma
fila do curador** (`CuradoriaPage`); SLA **soft** (expira = higiene de fila, sem consequência para
a avaliação — distinto do timeout do revisor de contestação).

**Schema** (`db.py`, DDL idempotente): `evaluation.curation_reviews` ganha `mode`
(`'standard'|'blind'`, CHECK), `deadline_at`, `expired_at`, `skill_version` + índice parcial
`idx_evcuration_blind_open`. Nova tabela `evaluation.curation_result_blinds` (1:1 com a review via
`uq_blind_per_review`): re-pontuação cega do humano (`blind_criterion_responses`,
`blind_overall_score`, `blind_by_dimension`), snapshot da IA no reveal (`ai_overall_score`,
`ai_by_dimension`) e `per_dimension_diffs` — **nunca toca o resultado imutável**.

**Config** (`sampling.py`, puro): `blind_stage_config(campaign)` lê de `campaign.contestation_policy`
(JSONB, sem migração): `blind_stage_enabled`, `blind_stage_sample_pct_flagged`,
`blind_stage_sample_pct_unflagged`, `blind_stage_sla_hours` (default 48), `blind_stage_severity_min`
(default 0.20, limiar de desacordo por dimensão). `enabled` exige pelo menos um `%`>0.

**Teste:** `tests/test_sampling.py::TestBlindStageConfig` (7 — puro). Smoke docker-demo: rebuild
evaluation-api, schema aplicada (tabela + 4 colunas + índices/FK), 49 passed.

> **Dev loop (registrado):** `evaluation-api` **bakeia o source** (sem bind mount) → `build` + `up -d`,
> não `restart`. `pytest` não está na imagem runtime → `pip install -q pytest pytest-asyncio` ad-hoc.

---

## R8d — Revisor heterogêneo (modelo do revisor ≠ avaliador) (2026-06-23)

Reduz viés de MODELO descorrelacionando o modelo do revisor AI do avaliador. Achado: o
`model_profile` do reason step **não era repassado** — avaliador e revisor caíam no default
`balanced` (mesmo modelo). Agora é fiado ponta a ponta.

**Plumbing** (reusável): `ReasonStep.model_profile?` (`@plughub/schemas`); `reason.ts`
`resolveModelProfile()` aceita estático ("evaluation") **ou** referência `$.pipeline_state.*`
(resolvida em runtime → o revisor lê o perfil da config da campanha) e o inclui no payload do
`aiGatewayCall`; tipos atualizados (`executor.ts`/`engine.ts`/skill-flow-service); o
skill-flow-service já forwarda via `JSON.stringify(payload)` ao `/v1/reason`. O `ai-gateway` já
aceita `model_profile` (`ReasonRequest`, default `balanced`; profiles `fast`/`balanced`/`powerful`/
`evaluation`).

**Heterogeneidade**: o avaliador (`agente_avaliacao_v1`) passa a fixar `model_profile: evaluation`
(modelo isolado, explícito). Config de campanha `ContestationPolicy.reviewer_model_profile`
(`fast|balanced|powerful|evaluation`, ≠ avaliador) + UI na `CampaignsPage` (dropdown + hint) +
i18n en/pt-BR. **Caveat documentado**: descorrelaciona viés de MODELO, não de DADO (KB
compartilhada) — não substitui o check humano cego (R8c).

**Teste:** `skill-flow-engine/src/steps/reason.model-profile.test.ts` (vitest) — estático, `$.`-ref,
não-resolve→undefined, ausente→undefined.

---

## R8a + R8b + R8e — Calibração: controles de viés + gatilho de divergência + UI (2026-06-23)

Primeira fatia do R8 (calibração avaliador×humano, sobre Arc 13). Fecha a "peça faltante"
que o doc nomeia: a métrica de divergência como gatilho de recalibração. Defere R8c (curadoria
cega) e R8d (revisor heterogêneo).

**R8a — controles de viés na rubrica** (`prompt_composer.py`): `BIAS_CONTROLS` (verbosity,
self-enhancement, surface-fluency, authority/emotional, consistência/posição) + `with_bias_controls()`
idempotente. Anexado ao body EFETIVO no endpoint `rubric-templates/effective` (runtime, o avaliador
sempre os recebe mesmo sob rubrica sobreposta do tenant) e ao `compose_rubric_prompt` (preview).

**R8b — gatilho Estágio 1 (divergência)**: `apply_divergence_flags()` (analytics-api `reports_query.py`,
pura/testável) anota por linha `divergence = 1 − calibration_score/100` e `recalibration_recommended`
(= divergence > limiar ∧ total ≥ N mínimo) + `recalibration_recommended_count` no summary. **Sinal, não
auto-mutação.** O endpoint `/reports/evaluator-calibration` lê limiar/N do config-api (namespace
`evaluation`, via novo `config_client.py`; degrada p/ defaults 0.25/30). Dashboard (`CalibrationDashboard.tsx`)
ganha coluna "Recalibração recomendada" (badge + tooltip de divergência); i18n en+pt-BR.

**R8e — UI de config**: aba **Avaliação** na `ConfigPlataformaPage` expõe o namespace `evaluation`
(editor genérico) — limiar de divergência, N mínimo + demais chaves; fecha de quebra o invariante
"todo campo de config é editável na UI". Seed config-api: `calibration_divergence_threshold` (0.25),
`calibration_min_sample_n` (30). Compose demo: `PLUGHUB_CONFIG_API_URL` no analytics-api.

**Testes (puros, sem infra):** `evaluation-api/.../tests/test_prompt_composer_r8a.py` (anexo idempotente,
preview) + `analytics-api/.../tests/test_calibration_divergence_r8b.py` (flag por limiar/N, score=null→na,
coerção de config).

---

## R7a — Masking do output_snapshot na auditoria MCP (fix de vazamento) (2026-06-23)

Fecha um vazamento latente: o `McpInterceptor` gravava o `output_snapshot` **cru**
(`capture_output ? result : undefined`) — ligar `capture_output` numa tool que retorna PII
escrevia PII bruta no `mcp.audit`/ClickHouse, independente de avaliação. Agora o output é
mascarado por padrão, simétrico ao input (que já redigia campos `@masked`).

**sdk** (`mcp-interceptor.ts`): nova `maskOutputForAudit()` — varre o retorno recursivamente e
mascara PII em folhas string por **padrão** (regex das `DEFAULT_MASKING_RULES`, substituindo pelo
`replacement` estático, sem dígitos reais); **preserva conteúdo não-PII** (habilita
faithfulness-vs-ferramenta sobre fatos não sensíveis). Registra `masked_output_fields` (paths) e
**une** as categorias detectadas a `data_categories` (flagra PII que a tool não declarou).
`capture_output` segue **opt-in por tool** (default false).

**schemas** (`audit.ts`): `AuditRecordSchema.masked_output_fields?: string[]` (simétrico a
`masked_input_fields`).

**Teste:** `packages/sdk/src/__tests__/output-masking.test.ts` (vitest) — CPF/email/cartão/telefone
mascarados, paths/categorias corretos, não-PII preservado, primitivos/null intactos.

**Deferido (R7b/c + Audit LGPD Fase 2):** faithfulness sobre o **valor** PII (vault + reveal
campo-mínimo transiente, R7c); enforcement de `retention_days` por tool acompanha o dual-write do
`mcp_audit_log` (Fase 2). Argument correctness e faithfulness-não-PII já ficam habilitados ao ligar
`capture_input`/`capture_output` na tool.

---

## R5 + R6 — Tier-2 de IA: evidência de execução no avaliador (2026-06-23)

Habilita o avaliador a julgar o que a IA **fez** (não só o que disse): tool correctness, policy
adherence e faithfulness-vs-KB. Escopo limpo, sem depender do R7 (sem input/output snapshot).

**R5 — fiação da evidência ao `ReplayContext`/`evaluation_context_get`:**
- **analytics-api** (`audit.py`): `GET /v1/audit/mcp-calls` ganha filtro **`session_id`** (ordem
  cronológica ASC quando escopado) — base do `tool_trace`. Compat mantida (sem `session_id` =
  escopo tenant, DESC).
- **mcp-server-plughub** (`tools/evaluation.ts` + `server.ts`): `evaluation_context_get` passa a
  devolver dois campos irmãos — **`tool_trace`** (mcp.tool_call da sessão via analytics-api) e
  **`flow_definition`** (trajetória ESPERADA via agent-registry `GET /v1/skills/:flow_id`, resolvido
  do `pipeline_state.flow_id`). `EvaluationDeps` ganha `analyticsApiUrl`/`agentRegistryUrl`
  (`ANALYTICS_API_URL`=3500, `AGENT_REGISTRY_URL`=3300). Degrada para `[]`/`null` em erro.

**R5/B — trajetória REAL durável (policy adherence):** o `pipeline_state` (com `transitions[]`) só
vivia no Redis (TTL 24h) e não vai ao stream → some no eval tardio/backfill. Novo
**`PipelineStatePersister`** (session-replayer) faz **snapshot no `session_closed`** para a tabela
PG **`session_pipeline_state`** (upsert idempotente; substrato durável reaproveitável pelo R4).
`ReplayContext.pipeline_state` lê do PG (fallback Redis vivo); ausente → critério vira `na`
(decisão D). A ESPERADA vem do agent-registry; o avaliador compara esperado × real.

**R6 — critérios de IA como 1ª classe:** são critérios `type=score` (fluem para o output-schema do
avaliador via `buildEvaluationOutputSchema`, sem cirurgia de schema). `agente_avaliacao_v1.yaml`
passa `tool_trace`/`flow_definition`/`actual_trajectory` ao step `reason` e instrui o uso (com `na`
quando a evidência falta). Form-semente **"Avaliação de IA (tier-2)"** provisionado via API oficial:
`infra/test/seed_ai_eval_form.sh` (3 critérios com `scoring_guidance` apontando a evidência).

**Verificação (docker-demo, tudo verde):** unit `packages/session-replayer/tests/test_pipeline_persister.py`
(persist/upsert, fallback PG→Redis, ausência→None); round-trip real R5/B (Redis→persist→PG→fetch
`source=postgres`); smoke `infra/test/test_r5_tier2_smoke.sh` (filtro `session_id` discrimina + ordem
ASC, compat DESC); **e2e `infra/test/test_r6_ai_eval_e2e.sh`** — avaliador real pontuou `tool_correctness=10`
e `policy_adherence=4` (na=false) usando `tool_trace`+`actual_trajectory`×`flow_definition`, `faithfulness_kb=na`
(sem KB). Log diagnóstico `evaluation_context_get evidence:` confirma a entrega. **Nota operacional:** o flow do
avaliador é cacheado em memória pela `routing-engine`/`skill-flow-service` — editar o YAML exige `restart`
desses serviços (ou hot-reload via `registry.changed`); rebuild de imagem não basta.

**Fora de escopo (R7):** argument correctness (`input_snapshot`) e faithfulness-vs-ferramenta
(`output_snapshot`) seguem pendentes — exigem `capture_input/output` + fix de masking de output.

---

## R11 — UI de % por-agente (cota) no editor de campanha (2026-06-22)

Fecha o trio de amostragem (R10/R11/R12). O backend de `%` por-agente já existia (consumido pelo
R10); R11 adiciona a superfície editável.

**platform-ui** (`modules/evaluation/CampaignsPage.tsx`): o seletor de modo de sampling ganha a
opção **"Cota por agente"** (`quota`). Quando selecionado, aparecem dois inputs — **% Humano** e
**% IA** (0–1) — gravados em `sampling_rules.quota_rate_human`/`quota_rate_ai`; o input de taxa
único (percentage/fixed) é ocultado no modo quota. O painel de detalhe da campanha exibe as duas
cotas. Hint explícito de que o `%` é **por-agente** e que o 1º contato elegível de cada agente é
sempre amostrado (IA tipicamente menor, opera 24×7).

**Tipos/contrato:** `SamplingRules` em `types/index.ts` e o Zod `SamplingRulesSchema`
(`@plughub/schemas`) ganham `mode:"quota"` + `quota_rate_human`/`quota_rate_ai` (0–1, opcionais).
**i18n:** `samplingMode.quota`, `quotaRateHuman`, `quotaRateAi`, `quotaHint` (modal + detail) em
en e pt-BR.

---

## R12 — Backfill ordenado + quota-aware na evaluation-api (2026-06-22)

Fecha o ciclo de corretude do R10 no caminho de backfill (`backfill.py::run_campaign_backfill`).
A seleção quota é **dependente de ordem**; o backfill agora processa os segmentos em ordem
cronológica de fechamento para reproduzir a mesma seleção que o forward teria feito.

**Ordenação** (`_close_order_key`): `/reports/segments` não expõe `closed_at` por segmento → o
fechamento é o `ended_at` (fallback `started_at` → `sequence_index` → `segment_id`, desempate
estável). `segments.sort(key=_close_order_key)` antes do laço — seleção independente da ordem de
fetch/paginação.

**Quota-aware**: ramo `mode=="quota"` chama `should_sample_quota` com o **mesmo** `redis_client`
do forward (passado pelo `router.py` via `_redis(request)`) — o contador é cumulativo entre
backfill e tempo-real, e o set `:seen:` torna o re-run idempotente (não re-amostra nem infla o
denominador). Modos `percentage`/`fixed`/`all` seguem por `should_sample` (stateless), inalterados.

**Limitação herdada (pendência R9 do backfill):** `/reports/segments` ainda não expõe
`user_id`/`flow_id`/`deploy_version`, então a chave IA no backfill usa `agent_type_id` como skill
e cai no bucket `_nover` (sem versão). Fecha quando o endpoint expuser esses campos — alinhado com
a denormalização já feita no segmento (R9 a–c).

**Teste:** `test_backfill.py` — ordenação determinística (`_close_order_key`), déficit reproduzível
sob fetch embaralhado (10 contatos @30% → seleção em `ended_at` t=1,4,7), e idempotência de re-run
(contador não infla).

---

## R10 — Amostragem por cota de agente (déficit cumulativo) na evaluation-api (2026-06-22)

Troca a amostragem stateless por **cota por agente cumulativa por déficit** (ADR
`adr-evaluation-sampling.md`; metodologia §IV.2). Novo modo `sampling_rules.mode="quota"` — não
toca `percentage`/`fixed`/`all` (caminho stateless via `should_sample` intacto).

**Mecanismo** (`sampling.py::should_sample_quota`, async/stateful): piso (1º contato elegível de
cada agente é **sempre** amostrado) + déficit (a cada contato recomputa `sampled/total`; amostra se
`< x%`). Cumulativo, não diário. Contador = hash Redis `{tenant}:eval:quota:{campaign}:{agent_key}`
com `HINCRBY total/sampled` atômico (race-safe entre contatos paralelos do mesmo agente). Chave:
humano `h:{user_id}`; IA `ai:{pool}:{skill}:{deploy_version}` — versão não resolvível cai no
sentinela `_nover` = bucket `(campaign,pool,skill)` (ADR borda). **Denominador = só elegíveis**:
`_passes_filters` (extraído de `should_sample`, compartilhado) roda antes de tocar o contador, então
um contato filtrado não infla `total`. Idempotência: set `:seen:` por `target_id` (segment_id|
session_id) — redelivery Kafka (offset latest, at-least-once) não dobra contagem nem re-amostra.
Degradação best-effort: Redis indisponível → cai no hash determinístico na mesma `%` por-agente
(cobertura nunca some em silêncio).

**Config `%` por agente (R11 backend):** `quota_rate_human` / `quota_rate_ai` no `SamplingRules`
JSONB, lidas por `quota_rate()` (fallback legado `rate` → default humano 10% / IA 5%). UI na
CampaignsPage segue pendente (R11).

**Wiring** (`main.py`): `_sample_one_target` ganha `redis_client`+`skill_id`; ramo `mode=="quota"`
chama `should_sample_quota`; `_sample_on_close` passa `flow_id`(=skill_id) e o `app.state.redis`.

**Trade-off aceito (ADR):** perde idempotência/determinismo do hash → seleção **dependente de
ordem** (backfill R12 deve ordenar por `closed_at`); o piso infla a taxa efetiva acima de `x%` em
baixo volume (cobertura > teto de `%`).

**Teste determinístico:** `test_sampling.py::TestShouldSampleQuota` — fake async Redis, 100 contatos
de um agente IA @10% → seleção exata em t=1,11,21,…,91 (10 amostrados, coverage 10%), + separação
humano/IA, fallback `_nover`, filtro fora do denominador, idempotência de redelivery, fallback
Redis-None. Pure-math em `TestQuotaDecide`.

**Demo:** flipar a campanha "Demo SAC — Avaliação Contínua" de `mode:all` para
`{"mode":"quota","quota_rate_ai":0.3,"min_duration_s":30,"outcome_filter":["resolved"]}` (rodar a
sessão DEPOIS do rebuild — consumers em offset latest).

---

## Amostragem sac_ia desbloqueada + NPS de fim-de-contato como hook genérico (2026-06-22)

Duas correções no fluxo SAC IA da bancada de avaliação.

**(1) Bug de amostragem (dado).** A campanha "Demo SAC" estava semeada com `pool_id="sac"` (typo de
`sac_ia`); como `evaluation_pool_id` é vazio, o `_sample_one_target` caía no `pool_id` legado (campo
de escopo ABAC) e descartava **todo** segmento sac_ia em silêncio (`"sac_ia" != "sac"` → `continue`,
sem log) — nenhuma `evaluation.instance` gerada apesar de segmento acumulado, `session_closed`
publicado e `deploy_version` resolvido. Fix: seed corrigido (`pool_id`+`evaluation_pool_id`=`sac_ia`);
**filtro de amostragem endurecido** para usar SÓ `evaluation_pool_id` (sem fallback ao `pool_id`
legado) em `main.py`/`backfill.py` (alinha com o avaliador, `router.py:2202`); backfill mirror
`pool_id→evaluation_pool_id` para rows legadas. Desbloqueou e **validou R9d-1** (`deploy_version=1.0`
em `evaluation.instances`).

**(2) NPS de fim-de-contato como hook genérico (reenquadramento da §14.2).** O `disparar_survey` no
caminho `resolvido` do `skill_atendimento_sac_v1` disparava `skill_survey_v1` (survey OUTBOUND,
delegate inbound_only). Sem identidade/religação no sac_ia (cliente anônimo de webchat), a survey
ficava suspensa por `timeout_hours:1` → vazava 1 contato `in_progress` por contato resolvido; e, no
caso comum (cliente presente que resolveu), nunca coletava NPS — assimétrico com o humano, que coleta
ao vivo na conferência. **Reenquadramento (§14.2, revisão 2026-06-22):** o hook `on_contact_end` é o
mecanismo **genérico** de NPS de fim-de-contato (segura a sessão via `posatt:customer_active` + roda o
skill do pool na conferência), válido para **qualquer** pool — humano OU IA. A survey OUTBOUND vira
**customização de SKILL** para casos especiais (multi-humano), iniciada pelo skill, não pela
plataforma. Mudanças:
- **bridge** (`process_routed`, conclusão do primário IA): completude do mecanismo — quando
  `outcome=resolved` e o pool declara `hooks.on_contact_end`, dispara `fire_pool_hooks("on_contact_end")`
  + `_hook_timeout_guard` em vez de `_trigger_contact_close` direto. Antes o hook só disparava no
  caminho com humano. Não é lógica de survey; é o hook genérico passando a cobrir o fim de contato IA.
- **config** (`tenant_demo.yaml`): pool `sac_ia` ganha `hooks.on_contact_end: [{pool: nps_ia,
  side: customer, nps_on_disconnect: skip}]` — **reusa o pool `nps_ia`** do humano (já bootstrapado),
  sem pool novo.
- **skill** `agente_nps_v1` (skill_nps_v1) ganha step `escolher_grao`: se há
  `@ctx.session.surveyed_segment_id` (humano) → grão **segment** (como hoje); senão (IA) → grão
  **session** (`survey_record(grain=session)`). Um pool serve humano e IA. Caminho humano inalterado.
- **skill** `skill_atendimento_sac_v1`: `disparar_survey` removido; `resolvido` → `finalizar_resolvido`.
- **mantido como caso especial**: `skill_survey_v1`/`survey_processo_ia`/`survey_collector_ia` (outbound
  multi-humano), com o gate `verificar_identidade` adicionado (sem `contact_identifier` não suspende —
  guarda contra vazamento se acionado anonimamente).
- **docs**: §14.2 revisada (analytics-agents-workbench) + `conference-mechanics.md` § Mudança 23.

**Gotcha de aplicação:** `sac_ia` já existe no DB (seed-if-absent/DB-owned) → a hook nova **não**
auto-aplica no rebuild; aplicar via `PUT /v1/pools/sac_ia` ou `REGISTRY_SYNC_RECONCILE=true`. `nps_ia`
já tem instância (reuso, sem pool novo).

---

## R9 a–c — Carimbo de `deploy_version`/`channel` no segmento (2026-06-22)

Carimba a versão do skill (deploy) no segmento → `analytics.segments`, insumo da cota por versão
(ADR amostragem), do núcleo epoch (Arc 6 Fase 2) e do condicionamento por canal no backfill.
`flow_id` já existia (= skill_id deployado).

- **schemas** (`contact-segment.ts`): `deploy_version` no `ContactSegmentSchema` e no
  `ConversationParticipantEventSchema`; `channel` no segmento (já estava no evento).
- **orchestrator-bridge**: `_skill_version_cache` populado no `get_skill_flow` (versão do corpo do
  skill = corrente = a que rodou); `_publish_participant_event` resolve `deploy_version` do cache
  via `flow_id` (sem tocar call-sites) e aceita `channel`.
- **analytics-api**: `parse_participant_event` mapeia `deploy_version`/`channel` na linha `segments`;
  CREATE + `ALTER ... ADD COLUMN IF NOT EXISTS` (runner de migração no boot). Colunas validadas via
  `DESCRIBE plughub_demo.segments`.
- **analytics-api (fallback robusto)**: `deployments_client.fetch_skill_version` (GET
  `/v1/skills/{id}.version`, cache) + enriquecimento no consumer (`conversations.participants` →
  preenche `deploy_version` do segmento quando o evento traz `flow_id` sem versão). Bridge = exato
  no início; analytics = versão corrente quando o bridge não envia.
- **Fix de causa-raiz**: o INSERT de `segments` usa lista fixa (`_SEGMENT_COLS` + `_segment_row`)
  que não tinha `deploy_version`/`channel` → o valor era descartado na escrita. Adicionadas as
  colunas na lista e no builder.
- **Validado**: sessão de IA nova → `analytics.segments` com `deploy_version=1.0`
  (`skill_atendimento_sac_v1`, `skill_survey_v1`, …).
- **R9d-1**: `deploy_version` propagado à **evaluation instance**. `_on_participant_event` capta
  `deploy_version` do evento (já captava `flow_id`); `_sample_one_target`/`_sample_on_close`/backfill
  repassam; `create_instance` + coluna `evaluation.instances.deploy_version` (migração
  `ADD COLUMN IF NOT EXISTS`). Insumo do R10 (cota por versão). Para o R15a (epoch), `deploy_version`
  já está em `analytics.segments` (R9c) → JOIN por `segment_id`; denormalizar em `evaluation_finalized`
  é opcional.
- **Pendente**: popular `channel` nos call-sites (~10; param já existe); `deploy_version` no
  `/reports/segments` (backfill).

---

## R1 — SessionMetricsExtractor fiado no ingest (`session_metric.*` + `auto_computed` na nota) (2026-06-22)

Primeiro item de implementação do arco de Métricas de Avaliação
(`docs/arcos/arc-evaluation-metrics-methodology.md`). O extractor era **código órfão** (nunca
chamado) → critérios `auto_computed` eram no-op que distorcia pesos.

- **evaluation-api `_ingest_core`**: antes da agregação, chama `SessionMetricsExtractor.extract()`
  (lazy) → `set_instance_session_metrics()` (persiste `evaluation.instances.session_metrics`) →
  `fill_auto_computed_criteria()` (injeta os critérios `auto_computed` em `criterion_responses` para
  entrarem na nota). Best-effort: falha de extração não derruba o ingest.
- **`session_metrics_extractor.py`**: SQL reescrito do schema imaginado (`stream_events` flat) para o
  real `session_stream_events` (JSONB): `author->>'role'`, `payload->>'content'`,
  `visibility='"all"'::jsonb`, coluna `"timestamp"`. **Cliente = `author->>'role'` NULL** (não é
  participante nomeado). Duração com **fallback** MIN/MAX de evento (o stream do demo não persiste
  `session_opened/closed`). Outcome/close_reason do payload de `session_closed`. Guard em
  `usage_events` (pode não existir no demo). Média de tamanho do agente ponderada entre roles.
- **Escopo-contato** (segment-scope = R4, pois `session_stream_events` não tem `segment_id`).
- Mesmo banco `plughub` → extractor reusa o pool da evaluation-api.
- Teste: `infra/test/test_r1_session_metrics.sh` (semeia stream + form com `auto_computed` + ingest;
  valida `session_metrics` 4/2/2 e `overall=(LLM 6 + auto 10)/2=8`). Verde.

---

## P3 — Arc 6 Fase 2: lente `deploy` RE-ANCORADA no POOL (spec §11) (2026-06-20)

Após walkthrough com o usuário, a unidade da lente `deploy` passou de skill (`flow_id`) para **pool**
(spec §11). Motivo: `skill_id` é estável (deploy não muda o id; `version` é campo à parte; deploy é
pool-centric via `PoolSkillSlot`+`SkillDeployment.pool_ids`) e **um skill pode rodar em vários pools** →
âncora-skill misturava pools. Validado no browser: curva por pool, ponto de deploy colorido por pool,
sem média, flag N<30. Núcleo epoch/versão (§4.1) segue diferido (P4).

- **P3-A (agent-registry)** — `GET /v1/pools/:id/deployments`: deploys onde o pool ∈ `pool_ids`
  (`SkillDeployment`, `pool_ids: { has }`), desc por `deployed_at`, header `x-tenant-id`. Validado por curl.
- **P3-B (analytics-api)** — `_compare_deploy_lens` agora **agrupa por `attr.pool_id`** (curva por pool);
  `_fetch_deploy_markers` consome a timeline do **pool** (novo `fetch_pool_deployments`) e cada marker carrega
  `pool_id`+`skill_id`+`version_label` (deploy compartilhado vira marker em cada curva). `deployments_client`
  fatorado (`_fetch_deployments` + variantes skill/pool, cache `(kind,tenant,id)`). Test `test_deploy_lens.py`
  reescrito (14 ✓ no total c/ `test_deployments_client`). **Fix de causa-raiz:** o `'ai' AS agent_type` constante
  colidia com `WHERE attr.agent_type != 'human'` (alias visível no WHERE do ClickHouse) → query falhava →
  "No data"; trocado por `any(attr.agent_type)` (padrão da lente `quality`).
- **P3-C (platform-ui)** — na lente `deploy` a seleção é por **pool** (checkbox do pool → `pool_id` na cor do
  pool; agentes viram referência desabilitada; pino μ oculto); `include_average=false`; estado-vazio
  "selecione um pool" (`bench.chart.selectForDeploy`); i18n `bench.domain.ai` reescrito p/ pools. en + pt-BR.
- **P3-C — refinamentos visuais (iteração com usuário, 2026-06-20):** decisões de leitura honesta da curva
  e do marcador, validadas no browser:
  - **Eixo diário completo** (cada dia = bucket). Dia sem avaliação fica **vazio**, nunca **zero** (zero
    significaria "avaliado=0"; vazio = "sem amostra" — avaliação é amostral). Sem interpolação/fabricação.
  - **Bolinhas = dias COM avaliação** (pontos de medição reais); **reta** (`type="linear"`, não `monotone`)
    liga as medições — sem suavização que insinue dado entre pontos.
  - **Deploy = triângulo** na cor do pool, SOBRE a curva (no valor do dia, ou último valor medido se o deploy
    caiu em dia sem amostra). Distingue do ponto redondo de medição. Deploy e avaliação são eixos independentes
    (um deploy pode cair em dia com OU sem avaliação).
  - **Versão/skill fora do gráfico** → no **tooltip nativo** do triângulo (`<title>` em `ReferenceDot shape`)
    + **contador** "N deploy(s)" (`bench.deploy.count`) no lugar da régua de chips → o espaço não cresce com a
    quantidade de deploys. **Legenda** (`bench.deploy.legend`) explica símbolos + "sem ponto = sem avaliação".

---

## P2 — Arc 6 Fase 2: lente `deploy` no board de Agentes (1º corte §6) (2026-06-20)

Observabilidade por deploy entregue como **lente no bench** (decisão D3), no **formato do 1º corte da §6**
(série DIÁRIA + `deploy_markers`). O núcleo §4.1/D4 (bucket por **epoch/versão**) fica **pendente** por
decisão do usuário (reavaliar depois) — "comparar versão N vs N+1" hoje é leitura manual via markers, não
eixo de versões. Registrado como **incompleto** no CLAUDE.md (não é ✅) para não repetir o ✅ falso do T16.

- **P2-A (analytics-api)** — `config.agent_registry_url` (env `PLUGHUB_AGENT_REGISTRY_URL`, default
  `http://localhost:3300`) + `deployments_client.fetch_skill_deployments(base_url, tenant, skill_id)`:
  httpx `GET /v1/skills/:id/deployments`, header `x-tenant-id`, cache `(tenant,skill)` 60s, degradação
  graciosa → `[]` (nunca 500). Decisão D1: sem tabela `analytics.deploy_events`, sem consumer, sem evento.
  Test `test_deployments_client.py` (9 ✓).
- **P2-B (analytics-api)** — lente `deploy` em `query_agents_compare` (`_COMPARE_LENSES`, domain `ai`):
  `_compare_deploy_lens` lê `avg(final_score)` de `evaluation_finalized` (Oficial, D2) com a atribuição
  por `flow_id` da lente `quality`; série diária + `deploy_markers` (top-level, via `_fetch_deploy_markers`
  async, filtrados à janela e ordenados) + `meta.min_sample=30`. Filtro domain `ai` (`attr.agent_type != 'human'`).
  Test `test_deploy_lens.py` (5 ✓). Também corrigidos 2 testes obsoletos do T11 (`TestQueryQualityReport`)
  que ainda chamavam a assinatura pré-T11 (`pool_id`/`category`/`score`).
- **P2-C (platform-ui)** — `AgentsBenchPage`: `LENSES += deploy` (domain `ai`), tipos `LensId`/`Domain`,
  `CompareResp.deploy_markers`/`meta.min_sample`, `isDisabled` p/ `ai`, `DeployChart` (linha `avg_score` 0–1
  + `ReferenceLine` por dia de deploy, com injeção do dia do marker como categoria p/ o eixo categórico
  renderizar a vertical + chip-legenda dos deploys + flag N<min). i18n `bench.lens.deploy`/`bench.domain.ai`/
  `bench.deploy.*` (en + pt-BR). **Cleanup**: `TimeseriesView`/`ComparisonView` mortas removidas de
  `AnaliseQualidadePage` (apontavam p/ `quality-timeseries`/`quality-comparison` inexistentes) + imports/helpers
  órfãos; `MetricSelector` mantido (ainda usado por `AnaliseComparacaoPage`).
- **Demo/infra** — `docker-compose.demo.yml`: analytics-api ganhou `PLUGHUB_AGENT_REGISTRY_URL` (markers).
  Seed de validação `infra/test/seed_deploy_lens_demo.sh` (segments AI + `evaluation_finalized` + deploy via
  API, com `flow_id == skill_id` p/ alinhar markers — §8). Validado no browser: série, markers, flag N<30 e
  domain-gate corretos.
- **Limitações conhecidas registradas:** (1) markers só alinham com `flow_id == skill_id` (§8); (2) núcleo
  epoch/versão (§4.1) não entregue; (3) média/multi-seleção herdadas do board são ruído nesta lente — a
  reavaliar junto com o epoch.

---

## T16 — Correção de verdade nas docs (✅ falsos, gap Arc 6 Fase 2) (2026-06-19)

Spec §19. **Só docs.** Auditoria de ✅ doc×código após T1–T11.

- **Arc 6 Fase 2 (gap confirmado)**: `arc6-phase2-observability.md` afirmava "Status: implementado ✅"
  (tabela `analytics.deploy_events`, endpoints `deploy-timeline`/`quality-comparison`/`quality-timeseries`,
  UI), mas **nada existe** no código (grep zero em `analytics-api`; UI Trend/Comparison desativada em
  `AnaliseQualidadePage`, `TAB_IDS=['summary']`). Corrigido: doc reescrito p/ "PROPOSTA / NÃO IMPLEMENTADO"
  (banner + fases A–D rotuladas *não implementado*); resumo do `CLAUDE.md` (§ Arc 6 Fase 2) corrigido +
  gap registrado no `## Pending`; nota em `TODO.md`.
- **Demais ✅ da spec §19 deixaram de ser falsos**: o que a spec listava como aspiracional
  (`finalize_evaluation`, `result_state`, `evaluation_finalized` como fonte de verdade, contestação por
  dimensão, contrato form-driven) **foi implementado** nos T1–T11 — logo os ✅ de arc6/arc13 agora têm
  código que os sustenta. T16 não os remove; apenas corrige o que permanece sem lastro (Arc 6 Fase 2).

---

## T11-C — UI do relatório de qualidade Oficial × Operacional (2026-06-19)

Fecha o T11. **Só platform-ui.** Validado no browser. O relatório de qualidade discoverable
(`/analise/quality` → aba Resumo / `AnaliseQualidadePage.SummaryView`) passa a ser o relatório
Oficial × Operacional, substituindo a fonte legada (`/reports/evaluations/summary`, modelo
`eval_status` pré-T3).

- `SummaryView` reescrito p/ consumir `GET /reports/evaluations/quality` (hook `useQualityReport`):
  toggle **Oficial** (default; só finalizadas — invariante) × **Operacional** (inclui provisório,
  rotulado), com banner; `group_by` campanha/motivo/segmento/versão/tipo de agente/data; KPIs
  Finalizadas (+ Provisórias no Operacional) + nota média; chips de `finalize_reason`; tabela com
  distribuição (alta/média/baixa) + colunas Finalizadas/Provisórias no Operacional; CSV.
- `useQualityReport` hook + i18n (`contacts` namespace: `quality.modes/modeBanner/finalizeReasons/
  kpi.finalized|provisional/table.total/groupByOptions.*`, en + pt-BR). Fix `group_by=date` (cast
  `toString(date)` no SQL — `Date` do ClickHouse não serializava em JSON → 500). A aba órfã que
  havia sido posta em `/evaluation/reports` (fora do nav) foi removida. **T11 COMPLETO** (A+B+C).

---

## T11-A+B — Relatório de qualidade Oficial × Operacional (§17.3) (2026-06-19)

Backend dos relatórios de qualidade em dois modos nunca blendados. **evaluation-api + analytics-api.**
Validado por API (Kafka e2e + query). Antes: o invariante `evaluation_finalized` **não chegava ao
ClickHouse** (o consumer descartava o evento por falta de `result_id`; a tabela não tinha os campos).

- **Ingest (A)** — `emit_evaluation_finalized` passa a incluir `result_id`; nova tabela ClickHouse
  `evaluation_finalized` (ReplacingMergeTree por `tenant_id, instance_id` — chave estável presente no
  completed e no finalized). O consumer ganha branch dedicado em `parse_evaluation_event` p/
  `event_type='evaluation_finalized'` → grava `result_state` implícito + `finalize_reason`/`segment_id`/
  `form_version`/`evaluated_agent_type`/`final_score` (0–1), sem poluir `evaluation_results` (evita a
  colisão `result_id=evaluation_id` do completed). Roteamento no consumer + DDL/cols/row builder/insert.
- **Query (B)** — `GET /reports/evaluations/quality`: `mode=oficial` (default; só `evaluation_finalized`
  — o invariante) × `mode=operacional` (finalized ∪ provisório de `evaluation_results` ainda não
  finalizados, rotulado por `provisional`/`finalized_n`/`provisional_n`), nunca blendados. Fatiável por
  `finalize_reason`/`segment_id`/`form_version`/`campaign_id` + `group_by`
  (campaign_id|finalize_reason|segment_id|form_version|evaluated_agent_type|date); distribuição por
  `finalize_reason`. Test `test_t11_quality_report.sh` (publica finalized no Kafka → consumer → tabela;
  seed direto → valida oficial=3/operacional=4/fatiamento). **Pendente**: T11-C (UI toggle na ReportsPage).

---

## T10-D — ações do nível 3 na rota dedicada + Arc 13 acionável + threads agrupadas (2026-06-19)

Conclui o T10. Traz revisar/contestar para a rota dedicada e, no caminho, fecha dois gaps
pré-existentes do Arc 13 que estavam mascarados. evaluation-api + platform-ui. Validado por API + browser.

- **UI (platform-ui)** — `EvaluationDetailPage`: barra de ações ✓ Revisar / ⚑ Contestar dirigida por
  `result.available_actions` (server-side, T10-A); painel ativo (Arc 13 `HumanReviewPanel`/
  `DimensionContestPanel13`, fallback Arc 6) na coluna esquerda, transcript à direita; `onDone`
  recarrega result + threads. Painéis exportados de `AvaliacoesPage`. Sem campo de ação → read-only.
- **Fix tenant (evaluation-api)** — `_get_tenant` (contestation_router) cai no claim `tenant_id` do
  Bearer JWT quando o header `X-Tenant-ID` falta. Os hooks Arc 13 da UI mandam só o Bearer → antes
  `GET /threads` e os submits `/contest`·`/review` davam **400** → a UI caía no Arc 6 legado (mexia em
  `eval_status`, não em `result_state` → lista não mudava, deixava recontestar). Test
  `test_t10d_arc13_tenant_fallback.sh` (contest só com Bearer → 200; `result_state` open→under_review).
- **Read agrupado (evaluation-api)** — `db.get_instance_threads_grouped`: o `GET /instances/{id}/threads`
  agora devolve UMA thread por dimensão (a UI espera assim; o storage é plano). Reconstrói `entries[]`
  (timeline por round), `current_state` (máquina: evaluator→neutral, human_agent→contested, reviewer
  revised/upheld), `original/current_score` normalizados 0–1 (do `criterion_responses`) e
  `dimension_label` (da versão fixada do form). Test `test_t10d2_threads_grouped.sh`
  (contest→contested, review→upheld, label/score/entries). Guard de shape no hook `fetchContestationThreads`.
- **T10 COMPLETO** (A lógica de ação · B provisionamento já existente · C visibilidade self-scope ·
  D ações na rota + Arc 13 acionável + threads agrupadas).

---

## T10-C — visibilidade self-scope em list_results (2026-06-19)

Fronteira dura de visibilidade da tela de Avaliações (spec §17.2): role+Grupo+pool = quem vê o quê;
ABAC = ação (nunca amplia). **Só evaluation-api.** Validado por unit test + API.

- `_compute_result_scope(jwt)` (helper puro, `router.py`): sem token → sem filtro (posture aberta
  por tenant); admin → tudo (+ `accessible_pools` se setado); não-admin → `evaluated_user_ids =
  supervised_user_ids ∪ {sub}` (atendente sem Grupo = só os próprios; supervisor = pessoas do(s)
  Grupo(s) Arc 9 + próprios). `accessible_pools` (Arc 7) como filtro de linha adicional.
- `db.list_results` += `evaluated_user_ids`/`accessible_pools` (filtro `evaluated_user_id = ANY(...)`
  + `campaign.pool_id = ANY(...)`; join na campanha quando há filtro de pool). Wired no endpoint
  `GET /v1/evaluation/results`.
- `InstanceCreate` passa a expor `evaluated_user_id` (paridade com o backfill/T2 + habilita o escopo
  por posse em criação direta).
- Testes: casos de `_compute_result_scope` em `tests/test_available_actions.py` (22/22) +
  `infra/test/test_t10c_visibility_scope.sh` (escopo SQL real: operator vê só os próprios; admin tudo).
- **Diferido (documentado)**: escopo por `supervised_agent_types` (avaliações de AGENTES AI por tipo) —
  o result não carrega `agent_type_id` (exigiria join/enriquecimento). A posse humana é o escopo novo
  da spec. **Pendente**: T10-D (superfície de ações revisar/contestar na rota dedicada do nível 3).

---

## T10-A — available_actions por result_state + round + posse (2026-06-19)

Núcleo do T10 (spec §17.2): `available_actions` deixa de depender de `action_required` (workflow)
e passa a derivar de **estado + round corrente + posse**. **Só evaluation-api.** Validado por unit
test (pytest, 16/16).

- `_compute_available_actions` reescrito (`router.py`): `open(R)` ∧ caller é o avaliado (dono,
  `jwt.sub == result.evaluated_user_id`) ∧ campo de contestação do round R → `["contest"]`;
  `under_review(R)` ∧ caller ≠ avaliado ∧ campo de revisão do round R → `["review"]`; senão `[]`.
  Campo ABAC casado por round (`contestar`/`_replica`/`_treplica`; idem `revisar`),
  via `_CONTEST_FIELD_BY_ROUND`/`_REVIEW_FIELD_BY_ROUND` + `_round_field` (clamp 1..3). Guardas:
  locked/finalized/sem-token → `[]`; não-dono não contesta; ninguém se revisa.
- **Fix do gate de leitura** (`_can_view_transcript`, regressão do T9-C2): generalizado p/ "qualquer
  campo do módulo `evaluation` com acesso ≠ none". Antes checava `visualizar`, **inexistente** no
  módulo (a leitura é `report`) → observador report-only tomava 403 no transcript.
- Test `tests/test_available_actions.py` (função pura, sem DB): matriz estados×papéis×posse×round +
  o gate de leitura. **Provisionamento (B) já existe** (`seed_auth.py`: supervisor `revisar`+`report`,
  operator `contestar`). **Pendente**: T10-C (visibilidade self-scope em `list_results`) e T10-D
  (superfície de ações na rota dedicada do nível 3).

---

## T9-C.fix2 — alinhamento do schema do evaluation_submit (caminho do avaliador real) (2026-06-19)

Fecha a ponta que o T9-C.fix deixou aberta: o caminho do **avaliador IA real** perdia a
justificativa por critério no boundary do `evaluation_submit` (mcp-server), antes de chegar ao
ingest. **Só mcp-server-plughub (TS).** Validado por unit test (vitest).

- **Causa**: a saída form-driven do LLM (`buildEvaluationOutputSchema`) emite por critério
  `justification` + `evidence[]{stream_entry_id, excerpt, relevance_note}`. Mas o
  `EvaluationCriterionResponseInputSchema` tinha campo `notes` (não `justification`) e
  `EvidenceRefInputSchema` exigia o shape legado `{event_id, turn_index}` → o Zod **descartava**
  `justification` e **rejeitava/perdia** a evidência form-driven antes do publish.
- **Fix** (`tools/evaluation.ts`): `EvaluationCriterionResponseInputSchema += justification` (opcional);
  `EvidenceRefInputSchema` passa a aceitar `stream_entry_id`/`excerpt`/`relevance_note` (form-driven)
  mantendo `event_id`/`turn_index`/`quote`/`category` opcionais (compat). O handler já encaminha
  `criterion_responses` como veio do Zod; o ingest (T9-C.fix) faz `notes||justification` e
  `evidence||evidence_entries`. Cadeia real fechada: LLM → evaluation_submit → evento → ingest → UI.
- Test `src/__tests__/evaluation.test.ts` (novo caso): `justification` e `evidence.stream_entry_id`
  sobrevivem ao parse e ao evento publicado (16/16 passam). **Rebuild**: mcp-server-plughub.

---

## T9-C.fix — ingest persiste justificativa + evidência por critério (2026-06-19)

Fecha um gap de mapeamento (anterior ao T9-C) que deixava o nível 3 **sem** a justificativa por
critério e **sem** chips de evidência clicáveis. **Só evaluation-api.** Validado no browser.

- **Causa**: `create_criterion_responses` gravava só `r.get("notes")`/`r.get("evidence")`, mas a saída
  form-driven do avaliador (`evaluation.ts buildEvaluationOutputSchema`) emite a fundamentação como
  **`justification`** e a evidência às vezes como **`evidence_entries`**. A justificativa sumia (campo
  errado) e os chips ficavam vazios → `CriterionDetail` (T9-B) e o clique-evidência (T9-C3) não tinham
  dado para mostrar.
- **Fix** (`db.create_criterion_responses`): `notes ← notes || justification` e
  `evidence ← evidence || evidence_entries`. `_parse_jsonb` já devolve `evidence` como array → a UI
  recebe `{stream_entry_id, excerpt, relevance_note}` e renderiza/torna clicável.
- Test `infra/test/test_t9cfix_criterion_evidence.sh` (ingest direto com `justification` + `evidence`
  em c1 e `evidence_entries` em c2; valida persistência e `stream_entry_id`; imprime URL do nível 3).
- **Pendência registrada (caminho do avaliador REAL, e2e-blocked)**: o `EvaluationCriterionResponseInputSchema`
  do mcp-server (`evaluation_submit`) tem campo `notes`, divergente do `justification` do schema de
  saída → o Zod pode descartar `justification` antes do ingest. Alinhar TS (aceitar `justification`) faz
  parte da dívida "form-driven prompt revision" (ver `TODO.md`/handoff). Este fix cobre seeder + ingest direto.

---

## T9-C3 — platform-ui: rota dedicada do nível 3 + transcript com evidência (2026-06-19)

Fecha o T9-C (blueprint §C, D1). **Só platform-ui.** Validado no browser (sessão real
`e8f75639`). Nível 3 do drill-down vira **rota dedicada** (tela cheia) com formulário preenchido
e transcript mascarado lado a lado.

- `EvaluationDetailPage.tsx` (novo): rota `evaluation/evaluations/:campaignId/:resultId` (D1).
  Split: esquerda = critérios (reusa `CriterionDetail` do T9-B, join form fixado ∪ respostas);
  direita = `TranscriptPanel` (mascarado, toggle **Segmento/Contato**, bolhas por papel). Clicar o
  id de evidência num critério **rola e destaca** a mensagem no transcript (C.3, via
  `t9c-msg-{stream_entry_id}`). Breadcrumb ← volta ao nível 2 da campanha.
- `evaluation-hooks.ts`: `useResult` + `useResultTranscript` (+ tipos `TranscriptMessage`/
  `ResultTranscript`) consumindo o endpoint do T9-C2.
- `AvaliacoesPage.tsx`: `CriterionDetail` e `ScorePill` exportados; chip de evidência clicável via
  prop `onEvidenceClick` (compat: vira `<span>` sem o handler); botão **⤢** no header do
  `DetailPanel` abre a rota dedicada (painel inline + ações de revisar/contestar intactos).
- `routes.tsx` + i18n `transcript.*`/`evalDetail.*`/`detail.openFullscreen` (en+pt-BR). **Rebuild**:
  platform-ui. **T9-C completo** (C1 analytics-api + C2 evaluation-api + C3 UI). As ações no nível 3
  (3 papéis via `available_actions`) seguem como **T10**.

---

## T9-C2 — evaluation-api: GET /results/{id}/transcript (orquestra + delega) (2026-06-19)

Segundo elo do T9-C (blueprint §C, D2/D3). **Só evaluation-api.** O nível 3 ganha a porta que
a UI consome: resolve `result → session_id+segment_id`, gateia por **papel de avaliação** e
**delega** a leitura mascarada ao analytics-api (T9-C1).

- `router.py`: `GET /v1/evaluation/results/{result_id}/transcript` (`?scope=segment|contact`).
  Busca o result (`get_result`), resolve `pool_id` da campanha p/ escopo ABAC, e chama via `httpx`
  o `analytics-api` (`settings.analytics_api_url` + `/v1/transcript/sessions/{session_id}`,
  `segment_id` quando `scope=segment`). Devolve `{result_id, session_id, segment_id, scope, window,
  masked:true, messages}`. Erros: 404 (result), 409 (sem session_id), 502 (delegação).
- `_can_view_transcript(jwt, pool_id)` (novo): gate por `module_config.evaluation`
  (`visualizar`/`revisar`/`contestar` no pool) — **não** `audit.sessions`. Nível 3 = "mesma tela
  p/ todos" (read-only p/ observador). Graceful degradation: token legado/anônimo → permitido
  (endpoints de avaliação são abertos por tenant; conteúdo mascarado, baixa sensibilidade).
- `_ingest_core` já propaga `instance.segment_id → result` (T2), então o recorte por segmento
  funciona fim-a-fim. Test `infra/test/test_t9c2_transcript.sh` (seed CH + form/campanha/instance/
  ingest → valida janela=2/contact=4/404/mascarado/ids alinhados). Próximo: T9-C3 (rota dedicada +
  UI com evidência clicável).

---

## T9-C1 — analytics-api: leitura de transcript mascarado por segmento (2026-06-19)

Backend do T9-C (blueprint `t9-evaluations-ia.md` §C, decisões D2/D3). **Só analytics-api.**
Primeiro elo da entrega do transcript do nível 3: a **porta limpa** que o evaluation-api delega
(D2), sobre `analytics.messages` (persistido, **mascarado por construção** — não há coluna
`original_content`, então D3/revisão cega é garantido no storage).

- `transcript.py` (novo): `GET /v1/transcript/sessions/{session_id}` com `?segment_id=&scope=segment|contact`.
  Resolve a janela `started_at/ended_at` de `analytics.segments FINAL` e janela `analytics.messages`
  por `session_id`+timestamp (C.4). Segmento desconhecido/aberto → fallback p/ `contact` (flag no
  `scope` devolvido). Cada msg carrega `stream_entry_id` (== `message_id` == `event_id` do stream
  canônico → alinha a evidência clicável, C.3) + `content` mascarado; `masked: true`.
- Router separado de `/v1/audit` (semântica de avaliação, não LGPD): o gate de **papel de avaliação**
  fica no evaluation-api (T9-C2); aqui só isolamento por tenant, espelhando `audit.py`.
- Registrado em `main.py` (`include_router(transcript_router)`).
- Test `infra/test/test_t9c1_transcript_window.sh` (seed direto no ClickHouse: 4 msgs + 1 segmento;
  valida janela=2, contact=4, fallback, mascaramento e alinhamento de `stream_entry_id`).
  Próximo: T9-C2 (evaluation-api orquestra `result → session_id+segment_id`, gate ABAC, delega aqui).

---

## T9-B.2 — Timeline por critério + provisória/final Δ (2026-06-19)

Fecha o T9-B (blueprint §B). **Só platform-ui.** Validar por browser.

- `CriterionDetail`: badge de **estado** por critério (`contested`/`upheld`/`revised`/`timeout` —
  oculto p/ `neutral`) + **provisória → final (Δ)** quando houve override (`thread.original_score`
  → `current_score`), materializando "a nota real consolida só no finalized" (§14.1/B.4).
- `DetailPanel`: threads agrupadas por `dimension_id` (= `criterion_id`, §15.5) e passadas a cada
  `CriterionDetail`; seção de histórico relabelada "Dimension threads" → **"Histórico por critério"**
  (a timeline round-a-round já vem do `DimensionThreadCard`, keyed por critério).
- i18n `dimensionStates.*` + `detail.criterionHistory` (en+pt-BR). **Rebuild**: platform-ui.
  **T9-B completo** (B.1 render tipado + B.2 timeline/Δ). Próximo: T9-C (transcript + evidência,
  rota dedicada).

---

## T9-B.1 — Drill-down: render tipado por critério (2026-06-19)

Nível 3 do drill-down (blueprint `t9-evaluations-ia.md` §B). **Só platform-ui.** O detalhe da
avaliação passa a renderizar os critérios **por tipo**, contra a **versão fixada do form** —
fechando o gap G-UI (o painel antes não tinha fonte de dado para os critérios). Validar por browser.

- `CriterionDetail` (novo): render por tipo (`score`→nota, `boolean`→Sim/Não, `choice`→opção,
  `text`→qualitativo, `auto_computed`→cinza, não-contestável) + label/pergunta/`scoring_guidance` +
  justificativa + **evidência como chips** (`stream_entry_id`).
- `DetailPanel`: busca `useResultCriteria` (GET `/results/{id}/criteria`) + `useFormVersion`
  (snapshot da `form_version` pinada, B.1 do blueprint) e faz o **join form∪respostas** por
  `criterion_id` (iterando a definição do form → mostra todos os critérios, inclusive os sem
  resposta); fallback genérico quando não há snapshot. Removido o `CriterionRow` antigo
  (tratava tudo como score, sem dado).
- Tipos: `EvaluationCriterion` += `type/scoring_guidance/...` (T6a); `CriterionResponseRow`
  (shape real do backend: score/boolean/choice/text/notes/evidence); `EvaluationResult += form_version`.
- i18n `detail.autoComputed/notContestable/yes/no/noCriteria` (en+pt-BR).
- **Fix (bug exposto no teste): seleção de linha.** A API de results devolvia `id` (PK), não
  `result_id`; o front comparava `undefined===undefined` → toda linha "selecionada" e o clique não
  abria o painel. Add `_expose_result_id` (id→result_id) em `list_results`/`get_result` (espelha
  `_expose_form_id`/`_expose_campaign_id`).
- **Fix: "Awaiting my action" zerava a lista.** O filtro mandava `action_required=any` ao backend
  (retornava [] p/ admin) e não voltava; passou a ser **client-side** sobre `available_actions`.
- **Rebuild**: evaluation-api (result_id) + platform-ui. Próximo: T9-B.2 (timeline por critério + Δ).

---

## T9-A2.2 — Lista de Avaliações em dois níveis (UI) (2026-06-19)

Frontend do nível 1 + escopo do nível 2 (blueprint `t9-evaluations-ia.md`). **Só platform-ui.**
Validar por browser.

- `AvaliacoesPage`: sem `?campaign=` → **nível 1** (`CampaignsLevel` — cards de campanha com total,
  chips por `result_state`, tempo médio, split humano/IA, badge SLA, período/pool); clicar no card →
  `?campaign=X` → **nível 2** (a tabela escopada à campanha, com **breadcrumb** "← Campanhas / nome"
  e a **coluna de campanha removida**, agora redundante). Drill por query param (deep-linkable).
- hook `useCampaignSummaries` (GET `/reports/campaign-summary`) + tipo `CampaignSummary`.
- i18n `campaignsLevel.*` (en+pt-BR). **Rebuild**: platform-ui. Próximo: T9-B (drill-down nível 3).

---

## T9-A2.1 — Sumário por campanha (backend, nível 1) (2026-06-19)

Backend do nível 1 da lista de Avaliações (blueprint `t9-evaluations-ia.md`). **Code-only
evaluation-api.** Validado via `infra/test/test_t9a2_campaign_summary.sh`.

- `db.campaign_summaries(tenant_id, campaign_ids?)`: GROUP BY tenant-wide — instances por status;
  results por `result_state`; `finalize_reason` (finalized); `evaluated_agent_type` (humano/IA);
  AVG(`process_duration_ms`); SLA vencido (`deadline_at<now` em open/under_review). Consolidado
  **global por campanha**.
- `GET /v1/evaluation/reports/campaign-summary?tenant_id=&campaign_id=` (aberto). O frontend (A2.2)
  mescla com nome/período/pool da campanha. **Rebuild**: evaluation-api. Próximo: A2.2 (UI nível 1
  + escopo nível 2 + remoção da coluna de campanha).

---

## T9-A1 — Colunas canônicas da lista de Avaliações (nível 2) (2026-06-19)

Primeiro chunk do T9 (blueprint `docs/product/t9-evaluations-ia.md`; spec §7.1). A lista deixa de
mostrar `eval_status` cru ("Submitted") e passa ao modelo canônico. **Só platform-ui** (o
`list_results` já devolve os campos via `SELECT *`). Validar por browser.

- `AvaliacoesPage`: badge `ResultStateBadge` (`result_state` + round + `finalize_reason`; fallback
  `eval_status` p/ linhas legadas); coluna 1 → **"Agente avaliado (segmento)"** (`evaluated_user_id`/
  `segment_id` + ícone humano/IA, sessão como contexto); coluna Data → `finalized_at` /
  `deadline_at` (prazo, quando há ação) / `created_at` + **elapsed** (tempo no estado, de `updated_at`).
- `types/index.ts`: `EvaluationResult += result_state/finalize_reason/finalized_at/segment_id/
  evaluated_user_id/evaluated_agent_type/current_round/deadline_at`.
- i18n `resultStates.*` + `finalizeReasons.*` + `table.agent/deadline` + `detail.elapsed` (en+pt-BR).
- **Follow-up**: o filtro de Status ainda usa o vocabulário antigo (eval_status) — alinhar ao
  `result_state` quando o backend filtrar por ele. **Rebuild**: platform-ui.

---

## T17-UI — Janela de período na CampaignsPage (2026-06-18)

Fecha o gap de UI do T17 (o backend — period_start/period_end + filtro forward + backfill — já
existia, mas a tela de campanha não expunha os campos, furando "every config field is UI-editable").
**Só platform-ui.** Validar por browser.

- `CampaignsPage`: dois inputs `type=date` (período início/fim) no form de criar/editar (start→
  `T00:00:00Z`, end→`T23:59:59Z`); fiação em create/update; exibição no painel de detalhe
  (`período → ∞` quando aberto). `EvaluationCampaign` += `period_start/period_end`. i18n
  `campaigns.modal.period*` + `campaigns.detail.period/noPeriod` (en+pt-BR). **Rebuild**: platform-ui.

---

## T8-D — ABAC gerir_rubrica + epochs por versão (2026-06-18) — T8 completo

Último chunk do T8 (spec §16.3). **modules.yaml + Sidebar.** Validado via
`infra/test/test_t8d_abac_rubrica.sh`.

- ABAC: campo `gerir_rubrica` no módulo `evaluation` (modules.yaml; upsert no boot do auth-api →
  `up -d --force-recreate auth-api`). Gate da Rubrica/Prompt repointado `formularios` →
  `gerir_rubrica` (separação de deveres; admin vê via bypass de role).
- Deploy epochs: os snapshots `rubric_template_versions` (published_at via `/versions`) já são os
  epochs da rubrica — nada novo a emitir.
- **Gap registrado**: Arc 6 Fase 2 (deploy_events + endpoints deploy-timeline/quality-comparison/
  quality-timeseries) é doc-✅ mas ausente no código da analytics-api — a comparação ancorada em
  epochs depende de construir essa infra (fora do T8). **Rebuild**: auth-api + platform-ui.
- **T8 completo** (A storage/versões; B1 composição/preview; B2 runtime; C UI; D ABAC/epochs).

---

## T8-C — UI Rubrica/Prompt (2026-06-18)

Chunk C do T8 (spec §16.3): página de edição da rubrica-template + preview + publish/versões.
**Só platform-ui** (backend pronto T8-A/B). Validação por browser (sem build Node no WSL).

- `RubricPage.tsx` (`/evaluation/rubric`): escopo default-tenant ↔ override-campanha, editor
  name/body, badge deploy_status/version, Salvar/Publicar/Preview (composed_prompt + source) +
  histórico de versões.
- hooks `evaluation-hooks.ts`: `useRubricTemplates`, `create/update/publishRubricTemplate`,
  `useRubricVersions`, `previewRubric`.
- nav no grupo Quality (`nav.eval.rubric`, ABAC `formularios`), rota `/evaluation/rubric`, i18n
  `rubric.*` (en+pt-BR). **Rebuild**: platform-ui. Próximo: T8-D (ABAC `gerir_rubrica` + deploy epoch).

---

## T8-B2 — Fiação de runtime da rubrica (2026-06-18)

Chunk B2 do T8 (spec §16.2): o avaliador real passa a receber a rubrica-template efetiva.
evaluation-api validado via `infra/test/test_t8b2_effective_rubric.sh`; mcp-server/skill
inspecionados (runtime e2e-blocked).

- evaluation-api: `GET /rubric-templates/effective` (body efetivo + fallback built-in; nunca null).
- mcp-server `evaluation_context_get`: fetch effective + expõe `rubric_instructions`/`rubric_source`.
  **Rebuild mcp-server.**
- skill `agente_avaliacao_v1.yaml`: `reason.input.rubric_instructions` ←
  `eval_context.rubric_instructions`; `prompt_id` renomeado `evaluation_rubric_v3` →
  `evaluation_form_driven_v1` (vestigial; `prompt_id` é obrigatório no schema, ai-gateway ignora).
- **Rebuild**: evaluation-api + mcp-server-plughub. Próximo: T8-C (UI), T8-D (ABAC + epoch).

---

## T8-B1 — Composição + preview do prompt (2026-06-18)

Chunk B1 do T8 (spec §5.1/§16.3): camada de composição do prompt do avaliador na evaluation-api
(upstream; ai-gateway stateless). **Code-only evaluation-api.** Validado via
`infra/test/test_t8b_rubric_preview.sh` (draft/built-in/resolvida + critérios + scoring_guidance +
auto_computed pulado).

- `prompt_composer.py` (novo): `DEFAULT_RUBRIC_BODY` (built-in) + `compose_rubric_prompt`
  (instruções gerais + critérios c/ scoring_guidance, pula auto_computed + notas de calibração por
  criterion_id + transcript placeholder). Sem I/O.
- router: `POST /rubric-templates/preview` (precedência: rubric_body → rubric_id → resolve_rubric →
  builtin_default; busca form + notas publicadas; compõe). **Rebuild**: evaluation-api.
- Próximo: T8-B2 (runtime: evaluation_context_get/skill + remove evaluation_rubric_v3), T8-C (UI),
  T8-D (ABAC + deploy epoch).

---

## T8-A — Rubrica-template: fundação backend (2026-06-18)

Chunk A do T8 (spec §16.3): storage + versionamento da rubrica-template (default por tenant +
override por campanha), espelhando forms (T6b). **Code-only evaluation-api.** Validado via
`infra/test/test_t8a_rubric_template.sh` (CRUD + versionamento imutável + resolução
default/override). Descoberta: `evaluation_rubric_v3` é vestigial (ai-gateway `reason` genérico,
não resolve `prompt_id`) — não há rubrica-template hoje; o T8 a cria.

- db: tabelas `rubric_templates` + `rubric_template_versions` (snapshot imutável); índices únicos
  parciais (1 default/tenant, 1 override/campanha); funções CRUD + `publish_rubric_template` +
  versions + `resolve_rubric` (override publicado da campanha → default publicado do tenant → null;
  lê do snapshot). Editar publicada bifurca draft + bumpa versão.
- router: `rubric-templates` CRUD + `/resolve` (efetiva) + `/publish` + `/versions`. Abertos
  (tenant_id), como forms. **Rebuild**: evaluation-api.
- Próximos chunks: B (composição/preview + remove evaluation_rubric_v3), C (UI), D (ABAC + epoch).

---

## T14 (c) — criterion_id na CalibrationNote (2026-06-18)

Laço mole de calibração ancorado no **critério** (spec §6/§18.3): a `CalibrationNote` ganha
`criterion_id` p/ o RAG injetar a orientação no bloco do critério certo. **Code-only
evaluation-api + UI** (mcp-server pass-through). Validado via
`infra/test/test_t14_calibration_criterion.sh` (round-trip resolve→note→list; cobre (b)
`resolve_curation` sem NameError).

- db: `calibration_notes += criterion_id TEXT` (nullable); `create_calibration_note` aceita o
  campo; `list_calibration_notes` (`SELECT *`) já retorna.
- contestation_router: `CurationResolveBody += criterion_id`; `resolve_curation` passa ao create
  + `metadata.criterion_id` no snippet do `mcp-server-knowledge`.
- mcp-server `evaluation_context_get`: `calibration_notes` pass-through → `criterion_id` flui ao
  contexto sem mudança TS.
- platform-ui `CuradoriaPage`: campo "Critério" (opcional) + `CurationResolvePayload.criterion_id`
  + i18n `curation.drawer.criterion*` (en+pt-BR).
- Fora desta leva: composição do prompt por critério + validação de scoring (a, e2e-blocked);
  laço estrutural (d, dep. T8). **Rebuild**: evaluation-api + platform-ui.

---

## T17-backfill — Reprocesso da janela de dados por segmento (2026-06-18)

Backfill do passado (spec §18.5): enumera os segmentos fechados na janela
`[period_start, period_end]` da campanha via `analytics.segments` (REST `GET
/reports/segments`) e cria instances por segmento (mesma amostragem do forward; idempotente
por `(campaign_id, segment_id)`). **Code-only na evaluation-api.** Validado via
`infra/test/test_t17_backfill.sh` (contrato 400/summary + idempotência). Instances nascem
`scheduled` → despachadas pelo T15; o backfill não despacha.

- `backfill.py` (novo): `fetch_closed_segments` (pagina `/reports/segments`, role ∈
  {primary,specialist}, best-effort) + `run_campaign_backfill` (reusa sampling/dedup/
  create_instance; `evaluated_user_id` do segmento; `form_version` pinado).
- `POST /v1/evaluation/campaigns/{id}/backfill` (admin): exige `period_start` (400 senão);
  `period_end` nulo → now(). Retorna `{scanned, created, skipped_pool/sample/dup}`.
- Config: `analytics_api_url` (env `PLUGHUB_EVALUATION_ANALYTICS_API_URL` =
  `http://analytics-api:3500` no compose), `backfill_page_size`, `backfill_max_segments`.
- Janela filtra por `started_at` (aproxima closed_at); `channel` ausente no segmento →
  regras por canal não se aplicam no backfill. **Rebuild**: evaluation-api (`--force-recreate`
  p/ a env nova).

---

## T15 — Dispatcher por janela de calendário (2026-06-18)

Tarefa de fundo que despacha as instances `scheduled` de cada campanha ativa **na janela de
calendário** da campanha, emitindo `evaluation.requested` (spec §18.4). **Code-only na
evaluation-api.** Validado via `infra/test/test_t15_dispatcher.sh` (default-open; idempotência por
cooldown; gating fechado/aberto via associação de calendário). Complementa o `POST
/campaigns/{id}/dispatch` manual ("Rodar agora"), que permanece para disparo sob demanda.

- DDL: `evaluation.instances += dispatched_at TIMESTAMPTZ` (idempotência do scanner; o dispatch
  manual não mexe nele).
- `db.claim_dispatchable_instances`: claim atômico (`FOR UPDATE SKIP LOCKED`) das scheduled
  não-expiradas fora do cooldown, carimbando `dispatched_at=now()` no mesmo UPDATE (race-safe);
  `db.list_active_campaigns` (cross-tenant).
- `sampling.campaign_dispatch_open`: janela via `calendar-api /v1/engine/is-open` para a entidade
  `evaluation_campaign:{id}`; sem associação / calendar-api down → aberto (best-effort).
- `router.dispatch_campaign_scheduled` (core compartilhado) + `POST /v1/evaluation/dispatch/scan`
  (admin; uma passada sob demanda) + `main._run_dispatch_scanner` (loop ~60s, gated por
  `dispatch_scanner_enabled`).
- Knobs (`config.py`): `dispatch_scanner_enabled`/`_interval_s`/`dispatch_redispatch_cooldown_s`/
  `dispatch_batch_limit`. **Rebuild**: evaluation-api.

---

## T17 (core) — Janela de dados da campanha + filtro forward (2026-06-18)

Janela de dados explícita por `closed_at` (spec §18.5), ortogonal ao `schedule`. **Schema novo**
na evaluation-api. Validado via `infra/test/test_t17_period_window.sh` (create/update/get round-trip).

- DDL: `evaluation.campaigns += period_start, period_end` (TIMESTAMPTZ; NULL=aberto).
- `create_campaign`/`update_campaign` + `CampaignCreate`/`CampaignUpdate` aceitam os campos
  (`_parse_ts`: ISO→datetime, pois asyncpg exige datetime p/ TIMESTAMPTZ).
- Filtro forward no sampling: `_sample_one_target` → `_within_campaign_window` descarta sessões
  com `closed_at` fora de `[period_start, period_end]` (NULL=aberto), nos dois caminhos do
  `_sample_on_close`.

Modos: forward (atual); bounded (`end` set); backfill (`start` no passado) = **T17-backfill**
(job batch sobre segmentos persistidos, follow-up). **Rebuild**: evaluation-api.

---

## T12 — Gate ai_review (sinalizados) (2026-06-18)

Gate de qualidade antes de publicar (spec §18.1). **Code-only na evaluation-api.** Validado via
`infra/test/test_t12_ai_review.sh` (flag→ai_review→ai-review→finalized; em-faixa→finaliza direto;
guard 409). Inclui fix do bug latente §2.2.

- **`_is_flagged` no `_ingest_core`**: score fora de faixa (regra `score_extremes`, params min/max)
  ∨ sem nota → result `ai_review` (via `pre_review_pending`) antes de publicar (AI e humano);
  não-sinalizado → comportamento atual.
- **`POST /instances/{id}/ai-review`** (admin): ajuste opcional + calibration_signal opcional →
  publica (IA→finalize auto_ai; humano→contestation_open+deadline); 409 fora de ai_review.
- **Fix §2.2 (latente)**: `results_contestation_state_check` recriado permissivo —
  aceitava só `closed_upheld/closed_revised/...` mas o código grava `auto_finalized`
  (ingest IA) e `closed_max_rounds` → CheckViolation. contestation_state é espelho deprecado
  (T1 decisão A); verdade é `result_state`/`chk_result_state`.

**Rebuild**: evaluation-api. **Fronteira**: timeout técnico do gate; ajuste por critério;
erro irrecuperável→error_rejected (liga T13).

---

## T13 (core) — Degradação thin-session/erro (2026-06-18)

Degradação da camada de trabalho (spec §8/§18.2). **Code-only na evaluation-api, sem migração**
(enums `skipped`/`error_rejected` já da T1). Validado via `infra/test/test_t13_degradation.sh`
(thin→skipped, erro→error, guard 409, fora dos relatórios).

- `POST /v1/evaluation/instances/{id}/skip` → instance `skipped` (thin-session, sem submit/result);
- `POST /v1/evaluation/instances/{id}/mark-error` → instance `error` (falha do avaliador);
- guardados (409 se já terminal); reusam `update_instance_status`;
- `skipped`/`error`/`error_rejected` fora dos relatórios de qualidade por construção
  (filtram `evaluation_finalized`).

**Fronteira (T13-skill, e2e-blocked)**: ramo thin no `agente_avaliacao_v1.yaml` (choice vs
`thin_min_turns` por campanha → tool `evaluation_skip`) + `on_failure→evaluation_mark_error`;
classificação recuperável/irrecuperável→`error_rejected` é a T12 (ai_review). **Rebuild**: evaluation-api.

---

## T7b-3 — Remoção dos shims do evaluation_submit (2026-06-18) — T7 completo

Com a saída form-driven via tool-use (T7b-2), os shims de normalização do `evaluation_submit`
(`mcp-server-plughub/tools/evaluation.ts`) viraram dead-weight e foram removidos:

- removido o `z.preprocess` do `DimensionThreadInputSchema` (`observation→justification` +
  default de `evidence_entries`); `dimension_threads` fica como entrada opcional **deprecada**
  (a saída usa `criterion_responses`; threads round-1 nascem por critério no ingest — T7a);
- removida a coerção objeto→string de `compliance_flags` (agora `z.array(z.string())`);
- `score` nullable de `criterion_responses` não era shim (contrato: null em `na`) — comentário corrigido;
- `evaluation_rubric_v3`: confirmado **vestigial** (não há prompt fixo no ai-gateway; o `reason`
  ignora `prompt_id`) — composição de rubrica é T8. Nada a remover em código.

Rede de segurança: validação recursiva + retry do ai-gateway (T7b-1) + validação do ingest
contra o form (T7a). **Rebuild**: mcp-server-plughub.

**T7 completo** (T7a + T7b-1/2/3): contrato de saída e nota são form-driven (JSON Schema do
form via tool-use; agregação determinística no ingest; sem shims).

---

## T7b-2 — Composição do JSON Schema do form + skill form-driven (2026-06-18)

Liga o avaliador ao conveyance do T7b-1 (spec §5.4/§16.2). **2a** (skill-flow-engine) + **2b**
(mcp-server + skill). Validado: substância do conveyance via proxy
(`infra/test/test_t7b2_schema_conveyance.sh` — envelope do form → /v1/reason tool-use →
criterion_responses conforme, incl. score nullable). E2e completo do avaliador bloqueado pela
infra de replay/alocação do demo (replayer curto-circuita alocação no cache-hit; sessões antigas
não re-hidratam) — confirma em run real fresco.

**T7b-2a (skill-flow-engine)**: `ReasonStep` += `json_schema` (inline) e `json_schema_ref`
(JSONPath do pipeline_state); `reason.ts` resolve, repassa `json_schema` ao ai-gateway e **pula
a validação estática local** quando presente; tipos de `aiGatewayCall` (executor+engine) +
runners (`skill-flow-worker`, `skill-flow-service`) forwardam. 3 unit tests.

**T7b-2b (mcp-server + skill)**: `buildEvaluationOutputSchema(form)` deriva o JSON Schema
(`criterion_responses[]` com `criterion_id` enum não-auto, `score` 0..max nullable, `na`,
`justification`, `evidence`) e o `evaluation_context_get` expõe `evaluation_output_schema`;
o skill `agente_avaliacao_v1.yaml` referencia via `json_schema_ref`. `composite_score` do
`evaluation_submit` virou opcional (nota recomputada no ingest — T7a); removidos os mapeamentos
mortos `composite_score`/`dimension_threads` no submit do skill.

**Rebuild**: mcp-server-plughub, skill-flow-service (inclui engine); restart orchestrator-bridge.
**Fronteira (T7b-3)**: remover shims do `evaluation_submit` + `evaluation_rubric_v3` fixo.

---

## T7b-1 — ai-gateway: reason aceita JSON Schema via tool-use nativo (2026-06-18)

Primeiro sub-chunk do conveyance form-driven (spec §5.4). **Code-only no ai-gateway.** Validado
verde ao vivo (`infra/test/test_t7b1_reason_toolschema.sh`, Claude real devolveu saída conforme)
+ 17 unit tests (`tests/test_reason.py`, validador recursivo).

**`ReasonRequest.json_schema`** opcional: presente → `reason` usa **tool-use nativo** (uma tool
cujo `input_schema` é o JSON Schema montado UPSTREAM do form; o ai-gateway não monta nada, só
repassa) com `tool_choice` forçado; ausente → caminho flat (compat).

**`LLMProvider.call(..., force_tool=None)`** (base + anthropic + openai): mapeia o tool_choice
forçado por provedor (Anthropic `{"type":"tool","name":...}`, OpenAI `{"type":"function",...}`).

**`reason._process_tool_use`**: lê `tool_calls[0].input`, valida com `_validate_json_schema`
(recursivo lite: object/array/number/string/boolean, required=presença de chave, enum, min/max,
nullable) e re-tenta até 3× com correção — a rede de segurança do §5.4.

**Fronteira (T7b-2/3)**: montar o JSON Schema do form no `evaluation_context_get` + skill usar
schema dinâmico (T7b-2); remover shims + `evaluation_rubric_v3` (T7b-3). **Rebuild**: ai-gateway.

---

## T7a — Agregação determinística form-driven + validação no ingest (2026-06-18)

Primeiro chunk da T7 (spec `evaluation-reconciliation-spec.md` §5.2/§16.2 — form como fonte
única da NOTA). **Code-only, sem migração.** Validado verde via `infra/test/test_t7a_aggregation.sh`
(overall do LLM descartado → recomputa 7.0 de (8+6)/2; threads round-1 por critério; 3 casos
de validação → 422).

**`scoring.py` (novo, lógica pura)**: `aggregate_scores(form, criterion_responses)` recomputa
bottom-up pelos pesos/tipos do form (`na`/`text` fora; pesos re-normalizados; score/auto→score,
boolean→true/false_score, choice→choice_scores, normalizado 0–10) → `(overall, by_dimension[])`.
`validate_criterion_responses` → violações (criterion inexistente, regra de `na`, faixa).

**`router._ingest_core`**: carrega o snapshot pinado (`get_form_version` pela `form_version` da
instance; fallback ao form vivo) e **descarta a `overall_score` recebida**, usando a recomputada.
Valida: `strict_validation=True` na rota HTTP `/ingest` → 422; consumer real (`_ingest_from_completed_event`)
e seeder sintético passam `False` (logam e seguem — endurecer é T7b). Threads round-1 nascem **por
critério** de `criterion_responses` (author `evaluator_ai`; fallback `dimension_threads`). Resposta
do ingest passa a expor `overall_score` + `final_scores_by_dimension`.

**Fronteira (T7b)**: conveyance tool-use nativo (JSON Schema do form ao `reason`), `output_schema`
dinâmico no skill e remoção dos shims. **Rebuild**: evaluation-api.

---

## T6b — Deploy lifecycle do form + snapshots imutáveis de versão (2026-06-18)

Segundo chunk da T6 (spec `evaluation-reconciliation-spec.md` §16.1), versionamento alinhado
ao Skill Deploy Lifecycle. **Tem schema novo** (aplicado por `ensure_schema` no boot — sem
migração manual). Validado verde via `infra/test/test_t6b_form_versioning.sh`
(create draft v1 → publish v1 → edit→draft v2 → publish v2 → snapshots imutáveis → republish idempotente).

**Schema (`db.py`)**: `evaluation.forms.deploy_status` (`draft|published`, CHECK idempotente;
ortogonal ao `status` legado) + tabela imutável `evaluation.form_versions` (PK `form_id,version`,
snapshot da definição).

**db helpers**: `publish_form` (snapshot `ON CONFLICT DO NOTHING` + marca published; idempotente),
`get_form_version` (snapshot c/ fallback ao form vivo), `list_form_versions`,
`latest_published_version`. `update_form` **bifurca novo draft** (`version+1`, `deploy_status=draft`)
ao editar form publicado; drafts editam in-place (snapshot intacto).

**router**: `POST /forms/{id}/publish`, `GET /forms/{id}/versions`, `GET /forms/{id}/versions/{version}`.

**Sampling**: `_sample_one_target` pina a versão **publicada** na instance
(`form_version = latest_published_version ?? versão viva`), substituindo o stub `=1` da T2.

**Fronteira (não-bug)**: o avaliador ler o snapshot pinado é a T7 (reconstrói o caminho do
avaliador). **Rebuild**: evaluation-api. Pendente T6: T6c (UI FormsPage).

---

## T6a — Modelo do critério enriquecido + normalização-na-leitura (2026-06-18)

Primeiro chunk da T6 (form como fonte única — spec `evaluation-reconciliation-spec.md`
§5.3/§16.1). **Add-only, retrocompatível, sem migração** (DB/router tratam `dimensions`
como JSONB opaco → novos campos round-trip). Validado verde via
`infra/test/test_t6a_form_model.sh` (legado→defaults; auto_computed→não-contestável/sem-evidência;
text→contestável; override explícito preservado).

**`@plughub/schemas` (`EvaluationCriterionSchema`)**: campos opcionais novos — `question`
(canônico, cai pra `description`), `scoring_guidance`, `min_score`, `choice_scores`,
`true_score`/`false_score`, `na_guidance`, `applies_when`, `evidence_required`,
`contestable`. Helpers exportados `deriveContestable(type)` (auto_computed→false) e
`deriveEvidenceRequired(type)` (score/boolean→true) — fonte única da derivação para backend,
UI e agregação (T7).

**evaluation-api (`db.py`)**: `normalize_form()` preenche derivados/default por critério **na
leitura** (`get_form`/`list_forms`/`create_form`/`update_form`) — migração-sem-reescrita do
§16.1, não-destrutiva (JSONB armazenado intacto; campos explícitos nunca sobrescritos).

Isolado a `@plughub/schemas` + evaluation-api (platform-ui tem `EvaluationCriterion` próprio
em `types/index.ts` → T6c). **Rebuild**: evaluation-api. Pendente T6: T6b (versionamento), T6c (UI).

---

## T5 chunk 5c — Contestação em lote por critério + gate "tratar todas" (2026-06-18)

Reconciliação Arc 6 + Arc 13 (spec `docs/product/evaluation-reconciliation-spec.md`, §4/§15).
Unifica o contrato de contestação no nível de **critério** sob o envelope de round/estado do
resultado. Code-only na `evaluation-api` (**sem migração** — usa colunas já existentes de
T1/T2). Validado verde ponta-a-ponta na demo via `infra/test/test_5c_contestation.sh` (4 steps:
contest em lote → gate 409 → guarda 403 → review completo finaliza).

**`POST /instances/{id}/contest` em lote** (`contestation_router.py`): aceita
`{dimension_ids[], reasons{criterion_id→texto}, evidence?, round?}`; cria uma
`ContestationThread` (`author_type=human_agent`) por critério e move `contestation_open →
under_review` **uma única vez**. `round` opcional faz anti-replay. Forma single legada aceita.

**`POST /instances/{id}/review` em lote + gate (§15.3)**: aceita `dimension_decisions[]`. O
**gate server-side "tratar todas"** exige decisão para o conjunto **exato** de critérios
contestados no round corrente → faltando algum, **`409 pending_contestations`**
(`missing`/`contested`/`round` no detail); critério não-contestado → `400`. Cria uma thread
`human_reviewer` por decisão e aplica a transição do round **uma vez**: reabre `round+1`
enquanto há round restante ou **finaliza no último** via o emissor único `finalize_evaluation`
(T3) — reason `revised` se houve qualquer override, senão `upheld`.

**Helper novo** (`db.py`): `list_contested_criteria_for_round` (distinct `dimension_id` com
`author_type='human_agent'` no round) — base do gate. **ABAC/posse (5a) preservados**:
contest exige posse + `contestar*` do round; review exige `revisar*` do round + guarda
revisor≠avaliado.

**Fronteira (não-bug):** a consolidação do `score_override` na nota final pelos pesos do form
é a **T7**; no 5c o `finalize()` usa a `overall_score` corrente como placeholder, então
`final_score` ainda não reflete overrides.

**Rebuild**: evaluation-api (`docker compose -f docker-compose.demo.yml up -d --build evaluation-api`).

---

## S2.2 — Avaliação real VERDE ponta-a-ponta com sessão de conversa real (2026-06-17)

Fechado o gate da S2.2 com uma sessão webchat **real** (retenção: agente humano João reverte o
cancelamento de TV da Maria com oferta; cliente aceita; NPS=10; wrap-up "Resolvido"; 26 eventos no
stream). Cadeia inteira verde com avaliação real do Claude: sessão real → `POST /v1/evaluation/instances`
→ `dispatch` → session-replayer (ReplayContext + form + transcript) → routing → `agente_avaliacao_v1`
(login → get_context → evaluate) → `evaluation_submit` → Kafka `evaluation.completed` → **consumer de
ingest novo** → `EvaluationResult` no Postgres (`overall_score=7.8`) + instance `completed`. Resultado
visível em Avaliação → Avaliações (`contestation_state=contestation_open`, fluxo humano).

Três causas-raiz corrigidas (todas "docs diziam completo, código não"):

**1. Transcript nunca chegava ao avaliador (descasamento de campo, latente).** O step `reason` do
`agente_avaliacao_v1.yaml` lia `$.pipeline_state.eval_context.context.replay_events`, mas o
`ReplayContext` (session-replayer `models.py`) serializa a lista de eventos como **`events`** (sem alias).
Resultado: `replay_events=0` → o LLM corretamente devolvia "sem dados de transcrição" (score=null) **mesmo
em sessão rica**. Estava mascarado porque os gates anteriores usavam sessões vazias (0 eventos de qualquer
forma). Fix: JSONPath → `.context.events` (mantida a chave `replay_events` que o prompt espera).

**2. Drift de contrato avaliador↔submit (shim defensivo).** O prompt `evaluation_rubric_v3` é **fixo** e o
ai-gateway `_format_schema` transmite o `output_schema` de forma **lossy** (só campos top-level —
`OutputFieldSchema` não modela `items`/`properties`/`description`/`nullable`), então o LLM **inventa** o
shape: dimensão com `observation`/`max_score`/`weighted_score` (não `justification`/`evidence_entries`),
`compliance_flags` como objetos, `score=null` em critérios N/A. O `evaluation_submit` (Zod estrito) recusava.
Shim de compatibilidade no `evaluation_submit` (`mcp-server-plughub/tools/evaluation.ts`): `dimension_threads`
normaliza `observation→justification` e default `evidence_entries=[]` com `score` nullable; `criterion.score`
nullable (N/A); `compliance_flags` coage objeto→string. Marcado como compat pendente da revisão form-driven
(Task #5 / TODO) — a avaliação já é real (8.2/7.8 com justificativas), só faltava persistir.

**3. Elo de persistência do avaliador real faltando (Arc 13).** `evaluation_submit` publica
`evaluation.completed`/`eval.instance.submitted` em `evaluation.events`, mas **nenhum consumer** ligava isso
ao `POST /v1/evaluation/ingest` — só analytics-api/clickhouse-consumer consumiam → o resultado ia pro
ClickHouse mas **nunca pro Postgres da evaluation-api**, e a instance ficava `scheduled` (o flow nunca dá
`claim`). Novo consumer `evaluation-api-ingest-consumer` (`main.py`) filtra `event_type=evaluation.completed`,
mapeia → `IngestBody` e chama o núcleo reusável `_ingest_core` (refatorado de `ingest_result`). Idempotente
(pula instance já `completed`). `_ingest_from_completed_event` no `router.py`.

**Rebuilds**: mcp-server-plughub, evaluation-api; **restart**: routing-engine (recarrega o flow do disco).

**Achados colaterais (não corrigidos — registrados)**: (a) ai-gateway sentiment pipeline quebra
(`name '_classify' is not defined` + `UnknownTopicOrPartitionError` no `sentiment.updated`) — fire-and-forget,
não derruba o `reason`; (b) `criterion.justification` é stripado pelo Zod do submit (perda de texto — a ser
resolvido pela unificação de contrato da revisão); (c) `session_meta.closed_at/outcome/duration_ms` nulos
para a sessão avaliada (o LLM nota "sessão aparenta aberta", mas avalia pelo transcript).

---

## Fix — avaliador real travava por validação UUID em IDs opacos (2026-06-16)

Descoberto no gate E2E da S2.2. O skill `agente_avaliacao_v1` passa `participant_id`/`evaluation_id` =
`evaluation_id` (formato prefixado `evinstance_<hex>`, **não** UUID canônico; instance-ids de agente AI
também são tipo `teste_demo-009`). As tools `evaluation_context_get` e `evaluation_submit`
(`mcp-server-plughub/tools/evaluation.ts`) validavam esses campos com `z.string().uuid()` → **toda**
avaliação morria no `get_context` (`MCP error -32602 Invalid uuid → participant_id`), caindo em
`complete_error`. Relaxado para `z.string().min(1)` (são identificadores opacos, só humanos recebem UUID).
**Rebuild**: mcp-server-plughub.

Segundo achado no mesmo gate: com o `get_context` destravado, o passo `reason` chamou o AI Gateway e o
LLM **gerou** a rubrica, mas a saída era truncada (`ai-gateway` HTTP 422 `invalid JSON — Unterminated
string`). Causa: `ReasonEngine` (`ai-gateway/reason.py`) tinha `max_tokens=1024` **hardcoded** no path
`/v1/reason` — baixo demais para o JSON de uma rubrica com N critérios × observações. Tornado
parametrizável (`ReasonEngine(max_tokens=...)`) e ligado a `settings.inference_max_tokens`, elevado para
**4096** (é um teto: o modelo para quando termina, então não encarece respostas curtas do realtime).
**Rebuild**: ai-gateway.

---

## Frente 2 — S2.2: avaliação real ponta-a-ponta (dispatcher + form no ReplayContext) (2026-06-16)

Liga o **avaliador real** (decoplado do seeder sintético). Quatro fatias:

**Slice A — `evaluator_pool` por campanha** (pool do AGENTE avaliador, ≠ `evaluation_pool_id` que é o pool
avaliado). Coluna `evaluator_pool TEXT` (null/'' = default global); `create_campaign`/`update_campaign`; campo
**SELECT** de pools na `CampaignsPage` (setar e **limpar** para "Padrão global" — envia `''`, não `undefined`).

**Slice B — Replayer carrega o form no ReplayContext** (`session-replayer/consumer.py`): o
`_handle_evaluation_requested` passa `form_id/campaign_id/instance_id` ao `prepare()` e busca o
`evaluation_form` via `GET /v1/evaluation/forms/{id}` (helper `_fetch_evaluation_form`, env
`EVALUATION_API_URL`). Sem o form, o agente avaliador não tinha critérios.

**Slice C — dispatcher** (`evaluation-api`): `POST /v1/evaluation/campaigns/{id}/dispatch` emite
`evaluation.requested` (tópico `evaluation.events`) para cada instance `scheduled` — `evaluator_pool` da
campanha (fallback `settings.default_evaluator_pool=avaliacao_ia`), `form_id`, `campaign_id`, `instance_id`.
Emitter `emit_evaluation_requested` (shape = `EvaluationRequest`). Instances ficam `scheduled` p/ o avaliador
reivindicar (claim → assigned).

**Slice D — "Rodar agora"** na `CampaignsPage`: botão primário + `dispatchCampaign` em `evaluation-hooks.ts`;
i18n `campaigns.dispatch`/`dispatchHint` (en + pt-BR). **Rebuild**: evaluation-api, session-replayer, platform-ui.

---

## Avaliação — Editar/Excluir campanha na UI (2026-06-16)

Fecha o gap de CRUD de campanha (o `CampaignsPage` só tinha create + pause/resume). **Excluir**: nova rota
`DELETE /v1/evaluation/campaigns/{id}` + `db.delete_campaign` (hard delete em transação, cascata de
instances/results/criterion/threads/curation); botão "Excluir" (com confirm) no detalhe. **Editar**: o
`CreateModal` ganhou modo edição (`editing` prop) — prefill dos campos escalares (name/description/form/
evaluation_pool/calendar/sampling/reviewer/workflow/contestation) e submit via `PUT` (`updateCampaign`) em vez de
`createCampaign`; botão "Editar" no detalhe; título/CTA do modal cientes do modo. Clients
`updateCampaign`/`deleteCampaign` em `evaluation-hooks.ts`. **Rebuild**: evaluation-api, platform-ui.

---

## Frente 2 — Avaliação campaign-driven: S1 (create) + S2.1 (trigger por campanha) (2026-06-16)

**Shakedown E2E do módulo de avaliação** — bugs reais encontrados rodando o fluxo (docs diziam "completo").

**S1 — destravar create de campanha/form** (`evaluation-api/router.py`):
- `CampaignCreate.pool_id` virou opcional e **espelha** `evaluation_pool_id` (o UI tem um seletor só
  "Evaluation Pool"; antes 422 `pool_id required`). Validator `_mirror_pools`.
- Endpoints de forms expõem **`form_id`** (= `id`) na resposta (`_expose_form_id`). Sem isso o `<select>` caía no
  fallback do HTML e enviava o NOME do form → 400 `form not found`.

**S2.1 — avaliação dirigida por CAMPANHA, não pelo fechamento** (decisão de arquitetura): o gatilho inline
`session_closed → avaliação` era o modelo antigo e foi **removido como gatilho**. Avaliação agora é amostrada por
campanha e despachada depois (janela do calendário — vale de atendimento), evitando concorrer com o atendimento
ao vivo. Quem quiser avaliar no fim usa o mecanismo **genérico** de pool hooks (on_segment_end/on_contact_end).
- `session-replayer/consumer.py`: o Persister **não publica mais** `evaluation.requested` no fechamento (segue
  persistindo o stream p/ replay posterior).
- `orchestrator-bridge/main.py`: o evento `conversations.session_closed` foi **enriquecido** com `pool_id`,
  `channel`, `started_at` (o `should_sample` filtra por pool/canal/duração).
- `evaluation-api`: novo consumer de `conversations.session_closed` (`_run_session_closed_consumer` +
  `_sample_on_close`) → casa com campanhas ATIVAS (`should_sample` + hard filter `evaluation_pool_id`) → cria
  `EvaluationInstance(status=scheduled)`. **Não roda o avaliador** (barato — só registra o candidato).
  Idempotente via `instance_exists_for_session`. **Rebuild**: evaluation-api, orchestrator-bridge,
  session-replayer.

**S2.Q1b — seeder enriquecido (datas + atribuição de agente)** p/ validar Trend/Comparison + lentes
quality/quality_criteria do bench de Agents: (1) `evaluated_at` propagado seeder→`IngestBody`→`emit_instance_completed`
→ timestamp do `evaluation_results` (séries temporais espalhadas em `days_back` dias, default 30); (2) o seeder
emite um **segment sintético** por sessão (`conversations.participants` → `analytics.segments`) com `agent_type_id`
+ `user_id`/`flow_id`, dando ao bench o AGENTE avaliado (join por session_id; `agent_key`=user_id|flow_id);
NPS alinhado ao mesmo `agent_key`. Flush do ClickHouse estendido p/ `segments`+`participation_intervals`.
**Também corrigidos no shakedown**: avg_score 0–10→0–1 no consumer (Bug B), `ScorePill` coage string (Bug A),
`campaign_id`/`form_id` expostos nas respostas (id vs entity_id), botões Gerar/Limpar na CampaignsPage.

**S2.Q1 — avaliador FAKE (seeder sintético) p/ validar o módulo Quality em VOLUME** (decisão: validar o módulo
desacoplado do agente LLM, que precisa de massa real). `POST /v1/evaluation/admin/seed-synthetic` (evaluation-api)
gera N avaliações sintéticas para uma campanha pelo **mesmo caminho real** (`create_instance` + `ingest_result`
→ result + criterion_responses + dimension_threads + finalização/contestação + Kafka `evaluation.events` →
ClickHouse), mix humano/IA, + sinais de **NPS** sintéticos (`session.signals`, grão session). UI: botão "Gerar
avaliações de teste" na CampaignsPage (`seedSyntheticEvaluations`). **Bug corrigido de caminho:** `ingest_result`
para `evaluated_agent_type="ai_agent"` referenciava `initial_state` indefinido (`UnboundLocalError`) — nunca fora
exercitado. **Rebuild**: evaluation-api, platform-ui. **Próximo**: validar Avaliações/Reports/Curadoria/NPS em
volume; depois S2.2 (avaliador real) quando houver massa.

---

## Frente 1 — Pull F2b-2b-1: preview/triagem read-only antes do claim (2026-06-16)

Triagem da fila pull: o operador **percorre os contatos em espera e vê contexto+histórico** antes de escolher
qual atender — sem virar participante até o claim. **Puro UI** (backend já coberto por endpoints read-only
existentes, keyed só por `sessionId`):

- **Mensagens (centro)**: reusa `GET /api/conversation_history/:sessionId` (carrega tudo — D2).
- **Contexto/SLA (abas direitas)**: reusa `GET /api/supervisor_state/:sessionId` via `useSupervisorState`.
- **Fluxo**: clicar na **linha** da inbox (`PullInboxPanel`) → `previewSessionId` → centro renderiza a conversa
  **read-only** (banner + `ChatArea` sem input) + abas Context/History, exatamente como um atendimento; action
  bar troca para **"Atender (Pull)"** + **Fechar**. Linha previewada destacada; trocar de linha troca o preview;
  poll 4s (D3). **Sem cache** (D2): ao trocar/fechar descarta o anterior. **Atender** → claim existente → WS
  `conversation.assigned` anexa o contato real e seleciona. Botão **Pull** por-linha mantido como claim rápido.
- Arquivos: `AgentAssistPage.tsx` (estado/preview/claim + branch de render centro/header/direita ciente de
  preview), `PullInboxPanel.tsx` (linha clicável + highlight). Sem mudança de backend.

**E2E**: fila → clica a linha → preview read-only (system+cliente) → "Atender (Pull)" → atende (input/Serving) →
Close encerra no webchat. **Rebuild**: platform-ui.

**F2b-2b-2 (polish, 2026-06-16)**: cor por SLA nas linhas (idade÷`sla_target` do pool: verde<0.6 / amarelo≥0.6 /
vermelho≥1.0); idade ao vivo (tick 1s a partir de `queued_at_ms`); **gating de capacidade**
(`contacts.size ≥ maxConcurrentSessions` do JWT desabilita Pull/Atender + hint — reforça o rollback server-side
`no_capacity` da F1.2 com feedback antecipado); auto-clear do preview quando o contato previewado sai da fila
(claim de outro agente / timeout). Só UI (`PullInboxPanel.tsx` + `AgentAssistPage.tsx`). **Frente 1 / Pull
completa (F1+F2).**

**Ajustes de UX (2026-06-16)**: (1) a coluna esquerda agora **divide em duas metades** (contatos atendidos ×
fila pull, ambas roláveis) quando há pool pull ativo — antes a inbox ficava espremida no rodapé; sem pool pull,
os contatos ocupam tudo. (2) **i18n**: as strings da inbox (`pullInbox.*` + `common.close`) estavam só como
`defaultValue` (PT fixo) → agora registradas em `en/` e `pt-BR/` (namespace `agentAssist`), respeitando o idioma.

**F2b-2b-3 — agrupamento por fila (2026-06-16)**: a inbox agora agrupa os contatos **por pool**, com cabeçalho
recolhível (nome + contagem + chevron) por fila; dentro de cada fila, ordem **mais-antigo-primeiro** (FIFO/triagem;
a cor por SLA reforça). O `pool_id` saiu da linha (vira o cabeçalho) → linhas mais limpas. Resolve a localização
quando há contatos de várias filas misturados. Só UI (`PullInboxPanel.tsx`).

**F2b-2b-4 — divisória ajustável (2026-06-16)**: a fronteira entre a área de contatos e a fila pull virou uma
**divisória arrastável** (`flexBasis` da área de contatos, clamp 15–85%), com a proporção persistida em
`localStorage` (`plughub_pull_split_pct`). Substitui o split 50/50 fixo. Só UI (`AgentAssistPage.tsx` + chave
i18n `pullInbox.resizeHint`).

**Limpeza**: removido o botão **Pull** por-linha da inbox — o claim passa a ser exclusivamente pelo fluxo de
preview ("Atender (Pull)" na action bar). A linha permanece clicável (abre o preview). **Frente 1 / Pull
encerrada (F1 + F2 + polish).**

---

## Frente 1 — Pull F2 (API + tools + inbox no Console) + fix de propagação de pool_config (2026-06-16)

Pull usável ponta-a-ponta: o operador vê a fila pull no Console e puxa o contato.

- **F2a-1** — API HTTP no Routing Engine (`http_api.py`, aiohttp, porta `ROUTING_HTTP_PORT`=3550):
  `POST /v1/work_queue/claim|release` → `Router.work_task_claim/release`; produtor Kafka injetado
  (`router._producer`) após `producer.start()`.
- **F2a-2** — tools mcp-server (`tools/work_queue.ts`) + lib compartilhada (`lib/work-queue.ts`):
  `work_queue_list` (Redis-direto `zrevrange` em `pool:{}:queue`), `work_queue_claim|release` (HTTP ao routing —
  o engine continua o único árbitro).
- **F2b-1** — rotas Express `/api/work_queue/{list,claim/:sid,release/:sid}` no mcp-server (consumidas pela
  inbox humana; clientes humanos não falam MCP).
- **F2b-2a** — `PullInboxPanel` no Console (`AgentAssistPage`, rodapé da coluna de contatos): poll 4s de
  `/api/work_queue/list?pools=`, botão **Pull** → `claim/:sid` → contato vira atendimento normal (Serving).
  `dispatch_mode` propagado em `PoolInfo`/`fetchPools`. Layout: coluna esquerda em flex-column (contatos
  atendidos `flex-1` roláveis; inbox pull ancorada no rodapé `max-h-50%`).

**Fix de propagação de `pool_config`** (destravou o gate de login humano): mudar `agent_kind` ai→human na UI
não propagava — o `orchestrator-bridge` reescrevia `pool_config` no Redis a partir do **cache em memória velho**
(heartbeat 15s), e `pools.ts` só publicava `pool.updated` (tópico do routing), **nunca** `registry.changed`
(tópico que o bridge consome p/ reconciliar). 
- `pools.ts`: POST/PUT agora também chamam `publishRegistryChanged("pool", id, op)` → bridge reconcilia na hora.
- `instance_bootstrap.py` `_pool_config_diverged`: `MANAGED` ganhou `agent_kind, dispatch_mode,
  session_reservation, max_concurrent_sessions, queue_config, webhook_skill_id, hooks, supervisor_config,
  calendar_id, context_visibility, agent_groups` — o reconcile passa a reescrever o Redis quando esses campos
  mudam. A **mesma classe de bug** atingia `dispatch_mode` (toggle push↔pull era revertido por ~5min).

**E2E validado**: `teste_demo` (human+pull) → webchat parqueia na fila muda → "Filas (pull)" lista o contato →
**Pull** → atende (mensagens bidirecionais; webchat mostra "Agente entrou no atendimento"). **Rebuild**:
agent-registry, orchestrator-bridge, mcp-server-plughub, routing-engine, platform-ui. **Próximo (F2b-2b)**:
preview/triagem — ver contexto+histórico de cada contato da fila ANTES do claim.

---

## Frente 1 — Pull core (dispatch pull no Routing Engine, F1.0–F1.3) (2026-06-15)

Fundação do **dispatch pull** (operador puxa da fila) — modo genérico por pool, coexistindo com push. Spec:
`docs/product/frente1-dispatch-pull-aprovacao-plano-consolidado.md`. **Routing-side completo; UI/tools = F2.**

- **F1.0** — campo `dispatch_mode: push|pull` (default push) ponta a ponta: `@plughub/schemas`
  `PoolRegistrationSchema`, agent-registry (coluna Prisma + migração + POST/PUT), routing `PoolConfig` +
  `kafka_listener`, **UI select** na PoolsPage (+ i18n). Aditivo.
- **F1.1** — `route()`: pool pull → **parqueia na fila** (pula `_allocate`, reusa caminho queued); o drain por
  `agent_ready` e o drain periódico **ignoram pools pull**. Push byte-parity.
- **F1.2** — claim atômico no `Router`: `work_task_claim` (`ZREM` 1-vencedor + `claim_instance` no semáforo do
  RECURSO push+pull + **rollback** se sem capacidade + `mark_busy` + lease + publica `conversations.routed` →
  reusa bridge/Console) e `work_task_release` (lease off + `release_instance` + re-enfileira). Registry:
  `atomic_claim_dequeue`, `write/delete_claim_lease`.
- **F1.3** — `claim_lease_s` (config-api ns `routing`, 180) lido via `routing_config`; branch pull deleta a
  claim lease no re-parque. **Auto-release de pull é emergente**: o crash_detector pula humanos → a desconexão
  do humano (mcp-server WS lifecycle) re-roteia → `route()` parqueia → contato volta claimável + vaga liberada.
  Renovação de lease por heartbeat + sweeper "ocioso conectado" diferidos (spec "sem sweep dedicado").

**Invariante preservada**: Routing Engine é o único árbitro — `ZREM`/`claim`/`mark_busy`/lease/`routed`
acontecem DENTRO dele. **Testes**: `test_work_queue_claim.py` 6/6 (1-vencedor, happy-path+routed, already_claimed,
no_capacity+rollback, release, route-pull-parqueia+limpa-lease) + suíte routing 96 verde. **Rebuild**:
agent-registry, routing-engine, config-api, platform-ui. **Falta (F2)**: tools mcp-server + API HTTP no routing
+ inbox no Console → pull usável ponta-a-ponta.

---

## Config — RegistrySyncer seed-if-absent (DB-owned, para o clobber de pools no rebuild) (2026-06-15)

Frente 3 Fase 1. **Sintoma**: a lista de pools do Transfer (`supervisor_config.escalation_pools`) — e qualquer
config de pool editada na UI — **sumia a cada rebuild**. **Causa** (não era perda de dado): o
`RegistrySyncer._sync_pool` fazia, no boot do `orchestrator-bridge`, um `PUT /v1/pools/{id}` com o corpo do
YAML sempre que o pool já existia (409), **sobrescrevendo** a edição de UI (que estava salva no Postgres) com os
valores do `infra/registry/tenant_demo.yaml`. O mesmo vetor existia no `_ensure_deploy_slot` (capacidade).

**Fix** (`registry_syncer.py`): provisioning **seed-if-absent / DB-owned** por padrão — no 409, o syncer
**não sobrescreve** (pool/deploy-slot DB-owned; o YAML só semeia DB vazio no 201). Helper `_reconcile_enabled()`
+ env `REGISTRY_SYNC_RECONCILE=true` restaura o reconcile legado (YAML vence) p/ dev/GitOps. **Skills seguem
upsert** (código, não config de tenant). Invariante registrada em `CLAUDE.md` § Configuration.

**Validado**: rebuild com DB existente → `Registry sync: pools(created=0 skip=20) deploy_slots(set=0 skip=19)`,
**zero** `pool ... updated (config drift)` (antes apareceriam ~20); + teste manual: edição de `escalation_pools`
na UI **sobrevive** ao rebuild. Sanidade: DB zerado ainda semeia tudo do YAML no 1º boot. **Rebuild**:
`orchestrator-bridge`. **Fase 2** (TODO): YAML→migração versionada if-absent, store por store.

---

## G7 Hook-pool por segmento — on_human_end/on_contact_end usam o pool de quem fecha (2026-06-15)

Fecha o gap de atribuição de pool nos hooks de fim-de-segmento/contato (o "follow-up `_cs_pool_id`" + o gêmeo
no `agent_closed`). Antes, o pool que resolvia a config de `on_human_end` (wrap-up do último segmento) e
`on_contact_end` (NPS de contato) vinha do `session:meta` (last-writer = último humano **ativado**), não do
segmento que fecha. Com pools humanos divergentes, wrap-up/NPS rodavam a config do pool errado.

Modelo (confirmado com o usuário): wrap-up é **por-segmento** (cada fim de segmento, pool próprio); NPS
(`on_contact_end`) é grão-**contact**, dispara **uma vez** na âncora (último segmento a se desligar) com o pool
**desse** segmento; a granularidade vive no skill-flow do agente NPS — `survey_record(grain)` já suporta os 4
grãos. Wrap-up/NPS são agent-skills configuráveis (UI Pools: "When the human segment ends" / "When the contact
ends — fires once per contact"), não core.

Fix (`orchestrator-bridge`, 2 sites de cómputo): resolve o pool de `participant_meta:{instância que fecha}` com
**fallback** ao `session:meta` (paridade se faltar):
- `agent_closed` (`_pool_id_hooks`, `instance_id` do `agent_done`) — cobre também o disparo **deferred** (o
  stash `pending_on_human_end` copia `_pool_id_hooks`).
- `customer_disconnect` (`_cs_pool_id`, `_last_human_instance_id` da âncora). Os **peers** já resolviam por
  `participant_meta`; isto alinha a âncora/último a eles.

**Validado E2E** (sequencial: humanoxxx encerra 1º, admin `retencao_humano` por último): `segment_wrapup
origin_pool=humanoxxx` (peer); `on_human_end`+`on_contact_end origin_pool=retencao_humano` (admin, âncora) —
**pré-fix saía `humanoxxx`** (meta). Paridade mantida (`target_pool` `wrapup_ia`/`nps_ia`, 2 wrap-ups
`pushed=true`). **Rebuild**: `orchestrator-bridge`. **Gaps remanescentes** (follow-ups, ver TODO): (2) survey
customer-side por-segmento não dispara p/ peers (`segment_wrapup` filtra `side=agent`); (4) binding
grão↔boundary é convenção; disparo grão=journey = F11.

---

## G7 Camada 3 — isolamento de pipeline por conferência + dedup de hook (Fatias A/A2) (2026-06-15)

Fecha o E2E do **G7 Item 2** (os DOIS humanos recebem wrap-up no customer-disconnect, de forma
**determinística**). A investigação reabriu o diagnóstico da Mudança 20: o isolamento de `pipeline_state`/lock
por conferência **já estava na fonte** (o bridge sufixa `pipeline_session_id` por `--seg--{segment_id}` desde
antes; o `5ea8dfae` que motivou a Mudança 20 era **build stale**). Restavam dois pontos reais, ambos no
`orchestrator-bridge`:

- **Fatia A — hardening da chave de pipeline** (`activate_native_agent`): a derivação virou regra única —
  para **qualquer** `conference_id`, isolar por `segment_id or instance_id or uuid`, **nunca** `session_id`
  cru. Byte-parity no caminho native comum (`segment_id` sempre presente → `--seg--{segment_id[:8]}`,
  idêntico). Fecha dois gaps latentes: o branch sem-segmento (era `--conf--{conf}`, colidia se 2 agentes
  dividissem `conference_id`) e o **YAML-fallback** (`process_routed`, registry 404) que ativava sem
  `conference_id`/`segment_id` → chaveava em `session_id` puro. O fallback passa a propagar `conference_id`.
- **Fatia A2 — isenção de hooks no dedup de specialist por `pool_id`** (`process_routed`): no fan-out
  multi-humano a âncora (`on_human_end`) **e** o peer (`segment_wrapup`) miram ambos `wrapup_ia` — dois agentes
  **legítimos do mesmo pool** servindo humanos diferentes. O dedup `conference:specialist:{pool_id}` (anti
  repeat-@mention) colapsava o segundo numa **corrida** (marcador escrito depois do check) → 1 humano ficava
  sem wrap-up, **intermitente**. Agora hooks (identificados por `hook_conf:{conference_id}`, já gravado pelo
  `fire_pool_hooks`) são **isentos** desse dedup; repeat-@mention de specialist (sem `hook_conf`) segue
  protegido.

**Rebuild**: `orchestrator-bridge`. **Validado E2E** (2 humanos — admin `retencao_humano` + operator
`humanoxxx` via @mention — reiniciar cliente, responder nos 2 consoles): 2 runs verdes, `wrapup_ia-001`+`-002`
em `menu:waiting`, `pipeline=` distintos (`--seg--`), ambos `pushed=true`, **zero** `Skipping duplicate
conference invite: specialist pool=wrapup_ia`. **G7 sub-arco multi-humano Item 2 concluído.**
**Follow-up aberto** (TODO): (1) âncora do fan-out usa `_cs_pool_id` do `meta` (last-writer) em vez do
`participant_meta` da âncora — invisível enquanto os pools convergem para `wrapup_ia` (classe Slice-1b);
(2) latência do `@mention` de humano (ver TODO § Camada 3).

---

## Router · Alocação atômica · Fatia B — claim/release no decide + release simétrico (2026-06-14)

Liga as primitivas da Fatia A ao caminho de alocação, eliminando de fato a corrida de sobre-alocação
(seleção otimista + claim atômico + re-seleção do perdedor). **Arco do router concluído.**

- **`router.py route()`**: em vez de escolher só o `best`, coleta **todos** os candidatos pontuados, ordena
  por score e tenta `claim_instance` em cascata — o 1º que vence aloca; quem recebe `−1` (perdeu a corrida /
  instância lotada) **re-seleciona** o próximo best; nenhum candidato → fila (caminho atual). `_try_affinity()`
  faz claim da instância de afinidade (`−1` → roteamento normal). occupant = `"{session_id}::{conference_id}"`.
- **`registry.py`**: occupant composto + **release por prefixo de sessão** (`release_instance(session_id)` →
  `"{session_id}::*"`), pois o `agent_done` só carrega `session_id` (simetria claim↔release). `mark_busy`
  **sincroniza** `current_sessions` do `SCARD` (não incrementa mais); `remove_conversation` usa
  `release_instance` (não decrementa mais). Elimina o read-modify-write não-atômico dos dois lados.
- **Testes**: `test_router.py` +2 (re-seleção em claim perdido; fila quando todos perdem) + mock
  `claim_instance`; `test_decide.py` mock `claim_instance`; `test_instance_semaphore.py` ampliado
  (release-por-prefixo, "release só afeta a própria sessão", confs concorrentes da mesma sessão). Corrigidos 2
  **drifts pré-existentes** (só teste): `test_routing_config` (`params={tenant_id}`), `test_crash_detector`
  (`scan_iter` MagicMock).

**Rebuild**: `routing-engine`. **Validado**: suíte 102 verde + 6 do semáforo; **E2E** mostrou
`router.claim ... claim=-1 — re-selecting` e os dois wrap-ups concorrentes em **instâncias distintas**
(`wrapup_ia-002` / `-018`), zero sobre-alocação. **Follow-ups abertos** (sub-arcos, ver TODO): (1) isolamento
de `pipeline_state`/lock do skill-flow por conferência (hoje por `session_id` → 2 wrap-ups concorrentes da
mesma sessão colidem no lock; o não-vencedor aborta sem renderizar — última camada do E2E do G7 Item 2);
(2) latência do `@mention` de humano (leak descartado, origem a localizar, baixa prioridade).

---

## Router · Alocação atômica · Fatia A — semáforo por-instância (claim/release) (2026-06-14)

Primeira fatia do conserto da **corrida de sobre-alocação** do router (causa-raiz real do bloqueio E2E da
Fatia 2b/3 do G7, e bug geral sob concorrência — ver `conference-mechanics.md` § Mudança 19). A alocação
atual faz `get_ready_instances` (leitura) → score → `mark_busy` (`current_sessions += 1`, read-modify-write)
**sem atomicidade**, e o consumer processa inbound **concorrente** (`asyncio.create_task` por msg) → dois
inbound paralelos pegam a mesma instância single-occupancy (lost update).

**Fatia A (aditiva — nada chama ainda, zero mudança de comportamento)**: primitivas atômicas em `registry.py`:
- `_instance_sessions_key` = SET de occupant_ids por instância (`{tenant}:instance:{id}:sessions`); `SCARD` =
  ocupação real (fonte de verdade).
- Lua `_CLAIM_INSTANCE_LUA` (atômico: `SADD` se `SCARD < max`, idempotente via `SISMEMBER`, com TTL) +
  `_RELEASE_INSTANCE_LUA` (`SREM` idempotente).
- `claim_instance()→int` (nova ocupação ≥1, ou −1 se lotado), `release_instance()→int` (ocupação restante),
  `instance_session_count()→int` (SCARD).

**Modelo escolhido (semáforo de contagem por-instância)** sobre as alternativas: idempotente (cobre
redelivery de `agent_done`), atômico de verdade (Lua single-threaded), sem fragilidade de TTL de mutex
distribuído, e fino (só serializa colisões reais; o select+score caro continua paralelo).

**Validado E2E**: `tests/test_instance_semaphore.py` (Redis real) — 5/5: 25 claims concorrentes em max=1 →
1 vencedor/24 `−1`/ocupação=1 (sem lost update); idempotência claim+release; teto multi-capacidade;
claim×release sem corromper contagem. **Rebuild**: nenhum p/ rodar o teste (aditivo). Próximo: Fatia B
(wiring no `decide()` com re-seleção + `mark_busy` usando claim). Ver TODO § Router.

---

## G7 Item 1 (Slice 4′) · Fatia 2b/3 — fan-out do wrap-up no customer-disconnect (entrega ✅, E2E bloqueado por gap-2) (2026-06-14)

Fan-out do wrap-up por peer no customer-disconnect multi-humano. **Lado bridge entregue e correto**;
a conclusão E2E concorrente está **bloqueada por um gap pré-existente de plumbing de menu** (gap-2 do §7),
agora com root-cause cravado.

- **`fire_pool_hooks(arm_contact_close)`** — no fan-out, cada `segment_wrapup` de peer faz `INCR
  session:{id}:contact_close_pending` + marcador `close_arming:{conference_id}` (segment_wrapup não toca
  posatt:active).
- **`_destroy_conference`** — guarda nova: adia enquanto `contact_close_pending > 0` (espelha o guard de
  posatt:active). **`process_routed`** (conclusão de `segment_wrapup`): `GETDEL close_arming` → `DECR
  contact_close_pending`; ao zerar → `_close_contact_layer` (idempotente) + `_destroy_conference`.
- **customer_disconnect** (`process_contact_event`) — âncora (`_last_human_instance_id`) inalterada
  (on_human_end + on_contact_end); **cada peer** dispara `segment_wrapup` do seu pool (resolvido por
  `participant_meta`, `arm_contact_close=True`). Single-humano → sem peers → byte-parity.
- **`human_seg` no customer_side** — o loop de `human_members` passou a escrever `human_seg:{instance}`
  (dual-write) por humano (o branch agent_closed já fazia; sem isto o fan-out caía no pid global). Corrige
  a 1ª regressão observada (operator recebia 2 / admin 0).
- **`_contact_close_timeout_guard`** (180s) — rede de segurança (segment_wrapup não usa hook_pending).

**Validado**: entrega/atribuição corretas — 2 `human_seg WRITE`, READs `fallback=False`, cada wrap-up ao
seu console (visibility por-segmento). **NÃO fecha E2E** — e a investigação revelou que a causa **não** é
plumbing de menu, e sim uma **corrida de sobre-alocação no router** (diagnóstico corrigido 2026-06-14):
instâncias de `wrapup_ia` são **single-occupancy** (`max_concurrent=1`), o consumer do routing é
**concorrente** (`asyncio.create_task` por inbound) e `get_ready_instances`→`mark_busy` é **não-atômico**
→ os dois inbound de wrap-up (paralelos) leem a `-019` com `current_sessions=0`, ambos a escolhem (lost
update) → 2 segmentos da MESMA sessão na MESMA instância → chave de menu `{sid}:{instanceId}` colide. **A
corrida afeta qualquer pool** sob concorrência (latente: p/ sessões distintas só desbalanceia carga). **Fix
primário = alocação atômica no router** (claim que rejeita sobre-capacidade); menu-por-`segmentId` vira
hardening opcional. Ver `conference-mechanics.md` § Mudança 19 + `g7…` §11. **Rebuild**: `orchestrator-bridge`.

---

## G7 Item 1 (Slice 4′) · Fatia 2a — idempotência do `agent_done` (fim do double-processing) (2026-06-14)

Fix do achado da Fatia 1: o `agent_done`/`contact_closed` do humano podia chegar **2×** (double-submit do
Console ou redelivery Kafka — o consumer despacha cada msg como task, sem serialização). O 2º passe
recriava o segmento (`participant_joined_at` já `getdel`'d → duração 0 → **segmento fantasma**) e
re-disparava `segment_wrapup` (conferência redundante).

**Mecanismo (sem chave nova, race-safe)**: o branch de close do agente (`process_contact_event`) ganha um
**gate de idempotência** logo após o `is_human`, antes do restore e dos side-effects: `SREM human_agents
instance_id` é atômico e retorna quantos removeu — se **0**, o instance já saiu (duplicado, ou já encerrado
por outro caminho, ex. heartbeat drop) → `return` no-op (log `Duplicate/late agent close ignored`). O
duplicado do **último** humano já era pego pelo `is_human` (flag deletada no `remaining<=0`); o gate cobre
o **não-último** (flag segue viva sob o outro humano). O `SREM` redundante mais abaixo foi removido.

**Rebuild**: `orchestrator-bridge`. **Validado E2E**: multi-humano agent_done pós-fix — um só `Peer wrap-up
dispatched` por close, **sem segmento fantasma**, ambos os wrap-ups + NPS corretos (não-regressão). A
captura explícita do guard (`Duplicate/late agent close ignored`) é oportunística (dup é intermitente);
correção é correta por construção (SREM atômico). Doc: `conference-mechanics.md` § Mudança 18.

---

## G7 Item 1 (Slice 4′) · Fatia 1 — `human_seg` por-instância (fundação do fan-out) (2026-06-14)

Fundação parity-preserving para o wrap-up por peer no customer-disconnect multi-humano (Item 1). O
registro do segmento humano `session:{id}:human_seg:{pool}` passa a ser keyed por **instância**
(`human_seg:{instance_id}`), com **dual-write** do espelho por-pool como fallback de back-compat.
Razão real (decisão 2026-06-14): a colisão "2 humanos no mesmo pool" é operacionalmente inexistente
(agentes de um pool são equivalentes — 1 por pool basta); o valor da fatia é ser o substrato correto
para o fan-out da Fatia 3, que precisa endereçar o segmento de **cada** humano (pools distintos) por
`instance_id` ao iterar `human_agents`.

- **`fire_pool_hooks`** ganha param `human_instance_id` (default `""`); o reader prefere
  `human_seg:{instance_id}` e cai no espelho `human_seg:{pool}` só se a chave por-instância faltar
  (sessão in-flight durante deploy / caller não migrado).
- **Writer** (`participant_left`) faz dual-write: `human_seg:{instance_id}` (canônica) +
  `human_seg:{pool}` (espelho legado).
- **Threading** nos 10 call-sites que leem `human_seg` (on_human_end / on_contact_end / segment_wrapup):
  defer nativo (`_npd_h_inst`), defer-Kafka (`_pd_h_inst`), customer_disconnect (`_last_human_instance_id`),
  transfer / no_continuation / other_human_active (`instance_id`).
- **Observabilidade**: `logger.info` em WRITE (instance/pool/seg) e READ (qual chave casou + `fallback`).

**Rebuild**: `orchestrator-bridge`. **Validado E2E**: (1) single-humano byte-parity (NPS=10 → segmento
humano; READ `fallback=False` na chave por-instância); (3) **multi-humano pools distintos** (admin
`retencao_humano` sai não-último → `segment_wrapup` lê `human_seg:human-{admin}`, `escalated`;
operator `humanoxxx` sai último → `on_human_end`+`on_contact_end` leem `human_seg:human-{operator}`,
NPS=10 no segmento do operator) — duas chaves por-instância distintas, zero cross-attribution.
Cenário "mesmo pool" descartado por não ter sentido operacional. Doc: `conference-mechanics.md`
§ Mudança 17 + `g7-segment-contact-decoupling.md` §11.

**Achado registrado (pré-existente, não-regressão — ver TODO)**: o `agent_done` do humano não-último
foi processado **2×** (double-dispatch de `segment_wrapup` + segmento fantasma de duração zero). É
idempotência ausente em `process_contact_event`, upstream da Fatia 1; atribuição permanece correta.

---

## Heartbeat · Slice 2 — pong-tracking (drop sujo) (2026-06-13)

Hardening da Slice 1: `ws.close` nem sempre dispara numa meia-conexão (sleep, partição de rede). O
agent-WS passa a usar **ping de protocolo** (`ws.ping`, auto-respondido pelo browser via RFC 6455):
evento `pong` reseta `isAlive`; um ciclo de 30s sem pong → `ws.terminate()` → dispara `ws.on('close')` →
grace → `agent_disconnect` (Slice 1) → re-rota. Falso positivo é auto-curável (Console reconecta dentro
do grace de 2.5s → cancela). O `{type:"ping"}` app-level é mantido. **Sem mudança no Console** (browser
responde o pong de protocolo automaticamente). **Rebuild**: `mcp-server-plughub`. **Arco heartbeat
completo** (Slices 1+2). Doc: `conference-mechanics.md` § Mudança 16 (adendo).

---

## Heartbeat / queda involuntária de humano · Slice 1 — detecção de drop + re-rota (2026-06-13)

Gap G4: humano que cai mid-contato (WS drop, não `agent_done`) não disparava nada → o contato ficava
**órfão** (sem re-rota nem close) até o cliente sair ou o watchdog. Agora o drop vira fim-de-segmento +
re-rota, mantendo o cliente atendido.

- **mcp-server** (`server.ts`, grace-timer do `ws.close`): no drop genuíno (sem reconnect dentro do grace
  de 2.5s), para cada sessão inscrita onde o humano AINDA está em `human_agents` (`sismember` dedup vs.
  quem já saiu por `agent_done`), publica `contact_closed(reason="agent_disconnect", instance_id)`.
- **bridge** `process_contact_event`: `agent_disconnect` flui pelo branch `agent_closed` (segment-end:
  restore + `participant_left` + lifecycle DECR + SREM `human_agents`). Diferenças: (a) `other_human_active`
  **não** dispara peer wrap-up (humano sumiu, não preenche menu); (b) `remaining<=0` → **re-rota**
  (`conversations.inbound` ao `_ha_pool` do humano que caiu, espelhando o transfer) em vez de close —
  posse re-estabelecida por alocação; cliente presente é implícito (se tivesse saído viria por
  `customer_side`). Sem `session:closed` (contato continua).

**Rebuild**: `mcp-server-plughub` + `orchestrator-bridge`. **Gate**: humano cai mid-contato (fechar a aba,
não Close) → log `agent_disconnect published` (mcp) + `re-routing to pool=… (contact kept alive)` (bridge);
contato re-aloca/enfileira no pool e segue; com outro humano na conferência, segue sob ele sem wrap-up do
que caiu. **Limitação/hardening (Slice 2)**: detecção de meia-conexão por pong-tracking (hoje depende do
WS close; ping de 30s não rastreia pong). Doc: `conference-mechanics.md` § Mudança 16, TODO.

---

## G7 Sub-arco multi-humano · Slice 4′ Item 1 — marcador session:closed em other_human_active (2026-06-13)

O mcp-server seta `session:{id}:closed` **incondicional** no `/api/agent_done` (server.ts ~1475, p/
ganhar a corrida com `pending_assignment` no reconnect single-humano). Em **continuação** (outro agente
customer-facing ativo) esse marcador vazava → o Routing Engine descartaria re-rotas/reconexões da sessão
ainda viva (§4).

- **bridge** `process_contact_event`, branch `other_human_active`: passa a **desfazer**
  (`delete session:{id}:closed`) o marcador, já que o contato continua. O mcp-server segue setando síncrono
  (preserva a proteção de corrida no single-humano); o bridge só desfaz quando detecta continuação —
  opção (a) do §4.

**Rebuild**: `orchestrator-bridge`. **Limitação documentada (Item 2, não feito):** no path
**customer-disconnect** com N humanos, só o pool do `meta` (last-writer) recebe wrap-up; dar
`segment_wrapup` por humano ali exigiria reintroduzir o gating `arm_close` num path frágil que serve o
caso comum single-humano — adiado (edge raro, baixo impacto). Doc: g7 §11. **Sub-arco multi-humano:
Slices 1/2′/3/4′-Item1 ✅.**

---

## G7 Sub-arco multi-humano · Slice 3 — fan-out humano↔humano (2026-06-13)

Gap (1) do §7: numa conferência com 2+ humanos, a mensagem normal de um humano ia só ao cliente
(outbound) + stream + analytics, **não** a `agent:events:{session}` → os outros humanos não viam.
(Os ramos `@mention` e resposta-a-hook já publicavam em `agent:events`; só o ramo normal não.)

- **mcp-server** `server.ts` (agent-WS): (1) o ramo normal customer-facing passa a publicar a msg em
  `agent:events:{session}` (`author.instance_id`, `visibility:"all"`) — fan-out aos outros humanos;
  (2) o filtro de forward ganha **self-skip**: `message.text` cujo `author.instance_id == self` não é
  reenviado ao próprio remetente (ele já tem o echo otimista local; ids diferentes → o dedup-por-id do
  Console não pegaria → evita render duplo).
- **Console: sem mudança** — já renderiza `message.text` de `agent:events`; o self-skip server-side
  evita o double. Cliente não assina `agent:events`; agentes IA leem o stream → sem duplicação.

**Rebuild**: `mcp-server-plughub`. **Gate**: admin manda texto → aparece no Console do operator + cliente;
operator manda → aparece no admin + cliente; nenhum vê a própria msg 2×. **Escopo**: a msg aparece como
`agent_human` genérico (atribuição-por-nome admin/operator = polish, fora desta fatia). Doc:
`conference-mechanics.md` § Mudança 15, g7 §11.

---

## G7 Sub-arco multi-humano · Slice 2′ — wrap-up por peer humano (2026-06-13)

Modelo **peer/Teams-like** (invariante revisada g7 §10/§11): humanos numa conferência são peers; o
contato fecha quando o último agente customer-facing sai. Esta fatia dá wrap-up ao humano **não-último**.

- **bridge** `process_contact_event`, branch `other_human_active` (`remaining>0`): em vez de só logar,
  dispara `fire_pool_hooks(hook_type="segment_wrapup", pool_id=_ha_pool)` para o humano que sai — igual
  ao branch `agent_transfer`. `_ha_pool`/`_ha_tenant` são por-instance (Slice 1b); `human_seg:{pool}`
  (escrito na saída deste humano) atribui ao segmento dele. `segment_wrapup` **não** arma
  `posatt`/`hook_pending` → não fecha o contato (segue sob os outros). A conclusão aplica a disposição ao
  `seg_signal` → re-publish do segmento.
- O **último** humano (`no_continuation`) segue inalterado: `on_human_end`(wrap-up) + `on_contact_end`(NPS).

**Rebuild**: `orchestrator-bridge`. **Gate**: conferência admin+operator → admin (não-último) fecha →
`Peer wrap-up (segment_wrapup) dispatched … pool=retencao_humano`, Console do admin coleta o wrap-up,
segmento re-publicado; contato continua sob operator; operator fecha → wrap-up+NPS+fecha. **Limitação
conhecida** (pré-existente): `human_seg` é keyed por pool → 2 humanos no MESMO pool colidem; wrap-up por
peer no path customer-disconnect é Slice 4′. Doc: `conference-mechanics.md` § Mudança 14, g7 §11.

---

## G7 Sub-arco multi-humano · Slice 1 (+1b) — identidade por-participante no close (2026-06-13)

Raiz do §8.1 (segmento do não-último primário travado "live" + contato não fecha): o close lia a
identidade do humano de um campo de SESSÃO (`session:{id}:meta.instance_id`), que `activate_human_agent`
sobrescreve last-writer-wins a cada humano ativado. Com 2 humanos, o `agent_done` de ambos era atribuído
ao último ativado → o segmento do outro nunca encerrava. Mesmo princípio da Slice A / ADR de identidade
única (identidade de participante não pode viver em campo de escopo-sessão).

- **platform-ui** (`agent-assist`): `ContactSession.instanceId` (novo); `conversation.assigned` passa a
  capturar `instance_id` por-contato (antes descartado); `handleClose` envia `instance_id` no corpo do
  `POST /api/agent_done` (todas as rotas de close passam por ele, incl. auto-close).
- **mcp-server** (`/api/agent_done`): usa `body.instance_id` quando presente; fallback `meta.instance_id`
  (compat). Assim o `contact_closed` carrega o instance do humano que realmente fechou.
- **Slice 1b** (bridge): `pool_id`/`agent_type_id`/`tenant`/`user_login` do humano que fecha passam a vir
  de `session:{id}:participant_meta:{instance_id}` (gravado por `activate_human_agent`), não do `meta` de
  sessão (last-writer). Sem isso, o close do não-último saía com o pool do último (`agent_done` lifecycle
  DECRementava o pool errado + segmento atribuído ao pool errado em Analytics). Corrigido nos **dois**
  paths (agent_closed + customer_disconnect), com fallback no `meta`.
- **Efeito**: cada humano encerra o SEU segmento, com o SEU pool; `remaining` chega a 0 quando todos saem
  → contato fecha; contadores de pool DECRementam corretamente.

**Rebuild**: `platform-ui` (`--no-cache`) + `mcp-server-plughub`. **Gate**: 2-humanos (admin + convidado
por @mention), cada Close encerra o segmento do humano certo (instance correto no log do bridge), nenhum
segmento fica "live", contato fecha quando ambos saem. **Follow-up conhecido** (Slice 2/4): o mcp-server
ainda seta `session:closed` incondicionalmente (server.ts ~1475) — em multi-humano deve ser condicionado
à não-continuação (§4). Docs: `g7-segment-contact-decoupling.md` § 10.

---

## G7 Fase 3b-ii — editor de `on_contact_end` na UI de Pools (2026-06-13)

Fecha o invariante "every config field is UI-editable" para `on_contact_end` e o caveat da 3b-i
(salvar um pool pela tela antiga dropava o campo). Só `platform-ui`.

- **`src/types/index.ts`**: `on_contact_end: PoolHookEntry[]` em `PoolHooks`.
- **`PoolsPage.tsx`**: `EMPTY_HOOKS`, load (`formData.hooks`), save (`cleanHooks`+`hasHooks`+`hadHooks`)
  e 4ª seção no editor de hooks (entre on_human_end e post_human). `HookListEditor` ganhou prop
  `defaultSide` — entries novas de `on_contact_end` nascem `side=customer` (NPS).
- **i18n** (`configRecursos`, en + pt-BR): chaves `pools.hooks.onContactEnd` + `onContactEndHint`;
  `onHumanEnd`/`onHumanEndHint` reescritas (fim-de-segmento / wrap-up, sem NPS). Reusa
  `side`/`npsOnDisconnect`.

**Rebuild**: `platform-ui` (`--no-cache`). **Gate**: abrir pool migrado → ver `on_contact_end` com
`nps_ia` → editar/salvar → `GET /v1/pools/{id}` confirma que `on_contact_end` **persiste** (não é mais
dropado). **G7 Fase 3 completa** (3a + 3b-i + 3b-ii). Docs: `g7-segment-contact-decoupling.md` § 10.

---

## G7 Fase 3b-i — NPS como hook de fim-de-CONTATO (`on_contact_end`) (2026-06-13)

NPS deixa de pegar carona no `on_human_end` (fim-de-SEGMENTO) e vira hook de
fim-de-CONTATO de 1ª classe. Cutover limpo (sem dual-read): wrap-up fica em
`on_human_end` (side=agent), NPS migra para `on_contact_end` (side=customer).

- **schema** (`packages/schemas/src/agent-registry.ts`): novo campo
  `on_contact_end: PoolHookEntry[]` em `PoolHooksSchema` + doc no campo `hooks`.
  `hooks` é coluna `Json?` no Prisma → sem migração de DB.
- **bridge `fire_pool_hooks`**: `on_contact_end` adicionado aos 4 conjuntos existentes
  (set `hook_pending`, stash `human_seg`→`surveyed_segment_id` para o NPS gravar
  `survey_record grain=segment`, escrita de `hook_conf`, INCR `posatt:active`
  +`posatt:customer_active`). Completion handler em `process_routed` é genérico
  (por `_hook_side` + contador por tipo) → **sem mudança**. `post_human` continua
  gatilhado por `on_human_end` (dispara após o wrap-up; `post_human=[]` no demo).
- **bridge `process_contact_event`**: nos 4 sites de dispatch (no_continuation,
  customer_disconnect, e os 2 caminhos de defer-por-specialist) passa a disparar
  `on_human_end` (wrap-up) **e** `on_contact_end` (NPS) separadamente. A decisão
  "mantém WS aberto" passa a olhar `on_contact_end` (com fallback defensivo p/
  entries side=customer em `on_human_end` de pools ainda não migrados).
- **cutover** `infra/registry/tenant_demo.yaml` (`retencao_humano`): entry `nps_ia`
  movida de `on_human_end`→`on_contact_end`. Migração dos pools de DB (criados via
  UI, ex.: `humanoxxx`) via `infra/migrations/g7_nps_to_on_contact_end.py` (API
  oficial, idempotente, dry-run por default).

**Rebuild**: `schemas` + `agent-registry` + `orchestrator-bridge`. **Pendente 3b-ii**:
editor de `on_contact_end` no platform-ui + i18n. **Gate E2E**: single-humano
byte-parity (wrap-up no Console + NPS ao cliente, fecha 1×); pool sem NPS fecha após
wrap-up sem teardown prematuro; transfer inalterado. Docs: `conference-mechanics.md`
§ Mudança 13, `g7-segment-contact-decoupling.md` § 10.

---

## G7 Fase 3a — close governado por `_has_continuation` + marcador condicional (2026-06-13)

Primeiro passo do decoupling de close (escopo single-humano + transfer; parity-preserving). A decisão de
fim-de-contato passa a ser governada pelo classificador `_has_continuation` (antes read-only) e o marcador
`session:closed` deixa de ser escrito incondicionalmente.

- **bridge** `process_contact_event`: (1) o literal `if reason=="agent_transfer"` no branch
  `remaining<=0` vira `if _g7_cont and _g7_motive=="transfer"` (com default por `reason` antes do
  `try`, preservando o transfer se o classificador falhar); (2) a escrita do marcador `session:closed`
  saiu do topo do handler (incondicional, exceto transfer) e foi **fatiada**: `customer_side` escreve no
  topo (cliente saiu = sempre fim-de-contato); `agent_closed` escreve só no path `no_continuation`/defer
  por specialist (dentro de `remaining<=0`, após o transfer retornar); o branch `other_human_active`
  (`remaining>0`) **não** escreve — antes vazava o marcador em multi-humano e fazia o Routing Engine
  descartar re-rotas legítimas (§4).
- **Efeito**: single-humano e transfer **inalterados** (marcador escrito/omitido como antes); a única
  mudança de comportamento é não-escrever o marcador em `other_human_active` — fundação para o sub-arco
  multi-humano, cujo close completo continua fora de escopo.

**Rebuild**: `orchestrator-bridge` (Python). **Gate E2E**: single-humano fecha com wrap-up+NPS 1× e
marcador setado; transfer A→B sem marcador prematuro, NPS só em B. Docs: `conference-mechanics.md`
§ Mudança 12, `g7-segment-contact-decoupling.md` § 10.

---

## G7 Slice B — wrap-up no transfer (fim-de-segmento, sem close) (2026-06-13)

Hook type novo **`segment_wrapup`**: dispara só o wrap-up `side=agent` para o segmento do humano que
transferiu, **sem** armar contadores de close (`hook_pending`/`posatt:active`) e **sem** NPS. Coleta a
disposição (motivo da escalação/transfer) e atribui ao segmento da origem (`seg_signal`→re-publish),
enquanto o contato segue pelo destino.

- **bridge** `fire_pool_hooks`: aceita `hook_type="segment_wrapup"` (reusa lista `on_human_end`
  filtrando `side=agent`); grava `hook_conf`+`hook_served_human`+`wrap_up_pending` mas **não** INCR
  `posatt:active`/`hook_pending`. Branch `agent_transfer` em `process_contact_event` troca o `return`
  seco por `fire_pool_hooks(segment_wrapup, pool_id=origin_pool)`. Conclusão em `process_routed`
  (`completed_hook_type=="segment_wrapup"`): aplica `_apply_wrapup_to_segment`, limpa `wrap_up_pending`,
  publica `posatt_segment_complete`, e **pula** DECR de close + `_destroy_conference`.
- **platform-ui** `AgentAssistContext`: `session.closed{agent_transfer}` deixa de remover o contato da
  origem → entra em **modo wrap-up** (`sessionClosed=true`, mantém inscrito), recebe o `menu.render`
  (visibility `[origin_pid]`, isolado pela Slice A). Removido no `posatt_segment_complete`.

Reusa a identidade por-segmento da Slice A (sem ela o wrap-up da origem vazaria/colidiria com o destino
ativo). **Rebuild**: `orchestrator-bridge` + `platform-ui`. Doc: `conference-mechanics.md` § Mudança 11.
**Aberto**: contabilidade de pool destino+wrap-up concorrentes (`session:pool` slot único — validação E2E).

---

## G7 Slice A — wrap-up multi-humano: identidade de participante por-segmento (2026-06-12)

**Report:** o wrap-up (`agente_wrapup_v1`, hook `on_human_end` side=agent) só funcionava quando o
humano era o **segmento final** do contato; em multi-humano (humano convidado como specialist ou
origem+destino de transfer) endereçava/roteava errado. **Causa-raiz:** o isolamento dependia de um
único campo de **sessão** `session.human_agent_participant_id`, lido por 4 componentes e sobrescrito
a cada humano que sai → colapsa com ≥2 humanos. Ver
[`docs/adr/adr-participant-identity-single-source.md`](docs/adr/adr-participant-identity-single-source.md).

**(a) Endereçamento por-segmento** — `orchestrator-bridge`: `fire_pool_hooks` monta o `_fixed_pid`
(side=agent) a partir do `instance_id` do humano DESTE segmento (`human_seg:{pool}`) e guarda
`session:{id}:hook_served_human:{conference_id}`; no join do wrap-up (`process_routed`, antes do
`activate_native_agent`) grava `segment.{wrapupSegId}.served_human_participant_id` no ContextStore
(espelha `inviter_participant_id`). `agente_wrapup_v1.yaml`: 8× visibility →
`@segment.served_human_participant_id` (auto-prefixa via `resolveSegmentRef`; `@ctx.segment.*` NÃO
auto-prefixa). Linchpin verificado: `activate_native_agent` envia `segment_id=_part_seg_id` →
`ctx.segmentId` = segmento onde o campo é gravado.

**(b) Entrega isolada** — `mcp-server` `subscriber.on("message")`: `forward()` fazia `ws.send`
incondicional (menu.render broadcast a todos os Consoles). Adicionado filtro: descarta evento de
array-visibility que não inclui a identidade da conexão (`expectedInstanceId`/`agentInstanceId`);
`"all"`/`"agents_only"` passam; identidade desconhecida → encaminha conservador.

**(c) Entrada por remetente real** — `mcp-server`: (c1) handler de texto WS resolve `agentPid` pela
identidade da conexão (`expectedInstanceId`), com fallback no campo global só sem conexão; (c2)
`menu_submit` aceita `agent_key` e roteia direto ao `menu:result:{sid}:{agent_key}` (fallback no scan
por visibility). `bpm.ts` menu.render expõe `source_instance = authorId` (= `ctx.instanceId` =
chave do `menu:waiting` = sufixo do BLPOP — alinhamento verificado). platform-ui (`types.ts`,
`AgentAssistContext`, `AgentAssistPage`) ecoa `source_instance` como `agent_key`.

`session.human_agent_participant_id` mantido como fallback single-humano (não aposentado ainda).
**Rebuild:** `orchestrator-bridge` (Python; re-sync da YAML no boot) + `mcp-server-plughub` +
`platform-ui`. Doc: `docs/guias/conference-mechanics.md` § Mudança 10; invariante no CLAUDE.md.
**Falta no G7:** Slice B (wrap-up no transfer = fim-de-segmento, sem armar close) + Fase 3 (close por
continuação + NPS de contato).

---

## G7 Fase 0 (classificador read-only) + convite de humano como specialist (2026-06-12)

**G7 Fase 0 — `_has_continuation` (read-only):** função em `orchestrator-bridge/main.py` que classifica se um
fim de segmento humano é continuação (`transfer` / `other_human_active` / `specialist_active`) ou fim de
contato (`no_continuation`), + log `G7-decision` em `process_contact_event`. Sem mudar comportamento.
**Validada E2E nos 3 casos** (`no_continuation`, `transfer`, e `other_human_active` confirmado em log com 2
humanos: `remaining=1 → continuation=True (other_human_active)`).

**Convite de humano como specialist:** o endpoint `GET /v1/pools/:poolId/mentionable-agents` (agent-registry
`routes/pools.ts`) passou a incluir pools `agent_kind=human` (placeholder `agent_type_id="human_agent"`),
além dos IA (deploy skill). O dispatch do @mention (mcp-server) já era agnóstico a kind (roteia ao pool com
`conference_id`). + alias `humanoxxx: humanoxxx` no `mentionable_pools` do `retencao_humano` no YAML demo.
**Validado E2E**: `@humanoxxx` → `Human agent notified: pool=humanoxxx` → 2 humanos simultâneos numa
conferência (1ª vez possível). Rebuild: `agent-registry` + restart `orchestrator-bridge`.

**Gaps multi-humano expostos** (pré-existentes; ver `docs/arcos/g7-segment-contact-decoupling.md` §7): sem
fan-out msg humano↔humano; roteamento do menu do wrap-up vai pra conferência em vez do `menu:result`; NPS não
dispara em multi-humano. **Reordenação do G7**: Fase 1 (wrap-up do não-último) adiada (depende de conferência
multi-humano); prioriza-se Fase 2/3 (close+NPS-de-contato para single/transfer).

---

## Console Transfer funcional + G7 (decoupling segment-end × contact-close) (2026-06-12)

O "Transfer" do Console era um **stub** (`handleTransferTo` → toast `transferComingSoon`) e o mecanismo de
transfer do `session_escalate` tinha bug latente: publicava `conversations.inbound` sem `started_at` → o
Routing Engine descartava como **"Unrecognised inbound event"** (a re-rota nunca acontecia).

**Implementado** (branch cirúrgico G7):

- **mcp-server** `POST /api/session_transfer/:sessionId` (auth JWT, `participant_id=human-{userId}`):
  `participant_left` (stream) → `session.closed{reason:agent_transfer}` em `agent:events` (origem larga o
  contato no Console) → `contact_closed{reason:agent_transfer, instance_id:origem, outcome:transferred}`
  (limpeza da origem no bridge) → `conversations.inbound` **válido** (`customer_id`/`channel` literal/
  `started_at`/`pool_id=target`) que re-roteia (router migra o bucket).
- **platform-ui** `handleTransferTo` → `fetch` na rota (toasts `transferDone`/`transferFailed`, i18n en+pt-BR).
- **orchestrator-bridge** `process_contact_event`: (A) não seta `session:{id}:closed` quando
  `reason==agent_transfer` (senão o `is_closing` guard descarta a re-rota); (B) no `remaining<=0`, se
  `reason==agent_transfer` → return após a limpeza da origem (restore + `participant_left` `outcome=transferred`
  + `agent_done` lifecycle DECR + SREM `human_agents`), **sem** `_mark_contact_ended`/`on_human_end`/close. O
  SREM libera `human_active` → cai o guard "Skipping duplicate routing / already-served" que bloqueava o destino.

**Validado E2E**: 1 contato com 2 segmentos humanos primários distintos (`operator@…` `transferred` em
`retencao_humano` → `admin@…` `resolved` em `humanoxxx`), `on_human_end` (wrap-up + NPS) disparando só no
fechamento do humano **final**, e o sinal `session_signal` NPS=10 corretamente chaveado ao `segment_id`/
`agent_key` do segmento final (atribuição per-segmento — F5). **Config**: o pool humano de destino precisa ter
os hooks `on_human_end` (wrapup+nps) ou o fechamento final fecha sem wrap-up/NPS.

**Rebuild**: `mcp-server-plughub` + `platform-ui` + `orchestrator-bridge`. Doc: `docs/guias/conference-mechanics.md`
§ Mudança 9; gap **G7** registrado no CLAUDE.md. **Dívida aberta (G7)**: decoupling segment-end×contact-close só
para o caso transfer; wrap-up transfer-aware do segmento que sai = Estágio 3 opcional.

---

## Bancada — F7 validado E2E real + F5 achado estrutural (NPS once-per-contact) (2026-06-12)

**F7 (motivo de escalação) — validado E2E com DADO REAL** (antes só fixture). Contato real conduzido via
webchat (:5173) + Console (:5174) no navegador: `sac_ia` → "Falar com especialista" →
`escalate(reason=specialist_needed)` → `retencao_humano` (operador) → Close → wrap-up classificado
**Escalado** + motivo **Retenção/insatisfação**. `plughub_demo.segments` da sessão:

- IA: `flow_id=skill_atendimento_sac_v1`, `outcome=escalated_human`, `escalation_reason=specialist_needed`.
- Humano: `agent_type=human`, `outcome=escalated`, `close_reason=agent_hangup`, `escalation_reason=retention`.

Confirma o wiring ponta a ponta da lente `escalation_reason` (IA via `pipeline_state.results.escalation_reason`
lido pelo bridge na conclusão; humano via menu do wrap-up → acumulador `seg_signal` → bridge). Nota de
execução: o menu `interaction: list`/`button` exige **eventos de mouse completos** para submeter a seleção
(um `.click()` JS puro não dispara o handler). Pré-requisito: rebuild de `orchestrator-bridge` +
`skill-flow-service` + `analytics-api` para a imagem carregar o código F7.

**F5 (NPS/wrap-up multi-humano) — achado estrutural, PAUSADO.** "2 NPS num mesmo contato" é **impossível**:
o NPS é customer-facing e dispara **uma vez por contato, no segmento humano final** (`on_human_end` não
dispara em transfer/handoff intermediário — confirmado E2E: após transfer o cliente não recebe NPS, só no
fechamento). Com **um operador**, transfer→re-escala **reusa o mesmo segmento humano** (sessão com
`humans=1`), então nem por construção saem 2 segmentos humanos. Validar "1 sinal por agente" requer **2
contatos com fechamento humano distinto** (idealmente 2 operadores p/ `agent_key` distinto, ou 2º pool
humano com `on_human_end`). **Caminho de escrita do NPS confirmado saudável**: `survey_record(grain=segment)`
publica `session.signals` com `segment_id`/`agent_key`/`value` correto quando o cliente responde de verdade —
o "não gravava" observado em automação foi artefato de `.click()` JS no webchat (não dispara o handler real),
**não regressão**. Diferença cosmética notada (deferida): o `menu`/`notify` do NPS renderiza como "structured
content" no transcript em vez de texto puro; **dado intacto**.

**Pendente F5**: criar 2º operador/pool humano → rodar 2 contatos → conferir 2 linhas
`session_signal (grain=segment, metric=nps)` com `segment_id` distintos.

---

## Config Consolidation — F2 item 5: ABAC/users (seed_auth × modules.yaml) (2026-06-12)

Fonte única do domínio auth. `infra/modules.yaml` é o catálogo ABAC (carregado pelo auth-api no startup →
`module_registry`); `infra/seed/seed_auth.py` provisiona os demo users **via a API HTTP** do auth-api
(POST /auth/users + PUT module-config) — já estava limpo nisso.

**Bug + drift corrigido**: o `module_config` hardcoded no seed_auth **divergia** do catálogo —
referenciava o módulo **`analytics`** (inexistente), `evaluation.relatorio` (no catálogo é `report`),
`evaluation.permissoes` (inexistente) e `billing.view`/`manage_*` (no catálogo é `visualizar`/`gerenciar`).
Como o PUT module-config valida contra o `module_registry` e rejeita **todo** o config com **422** se houver
qualquer campo desconhecido, os demo users (supervisor/operator) ficavam **sem module_config** (ABAC vazio).

**Fix (item 5)**: `seed_auth.py` realinhado ao `modules.yaml` (campos reais): supervisor →
`evaluation.revisar`/`report`, `contacts.visualizar`/`exportar`, `billing.visualizar`; operator →
`evaluation.contestar`, `contacts.operacao`/`visualizar`. `set_module_config` agora **falha (die) em 422**
(drift de schema é erro de config, não ruído) — pega divergência futura entre seed e catálogo.

Sem build (seed/yaml). **Validação (usuário)**: rodar `seed_auth.py` (auth-api no ar) → sem 422; logar como
`supervisor@plughub.local` e confirmar que vê Avaliação/Revisar + relatórios; `operator@plughub.local` vê
Console/Contatos. **Item 5 (ABAC/users) fechado.** Restam na Fase 2: item 6 (seeds → bootstrap idempotente).

---

## Config Consolidation — F2 item 7: defaults/env cat. C (2026-06-12)

Fecha a categoria C do §6 (config de negócio que vazou pra env).

**7a — VITE_DEFAULT_POOL**: era **env morto** — não é lido em lugar nenhum (nem platform-ui nem
agent-assist-ui, que só usa VITE_MCP_WS_URL/VITE_ANALYTICS_URL/VITE_TENANT_ID). Removido do compose
(estava no bloco agent-assist-ui).

**7b — EVALUATOR_POOL + REPLAY_SPEED_FACTOR** (session-replayer): movidos para o config-api namespace
`evaluation` (`evaluator_pool`=avaliacao_ia, `replay_speed_factor`=10.0). Achados latentes corrigidos de
passagem: (1) `CONFIG_API_URL` não era setado no compose do session-replayer e o default era
`http://localhost:3500` (porta do analytics!) → o fetch de config (incl. os TTLs `replayer_hydration_ttl_s`/
`replay_context_ttl_s`) **já falhava** e caía no default; (2) o fetcher não passava `?tenant_id=` (exigido
desde 2026-06-05); (3) o default de código de EVALUATOR_POOL era `avaliador_qualidade` (pool inexistente).
(4) o session-replayer não tinha `config-api` no `depends_on` → subia antes do config-api estar pronto e o
fetch de startup dava Connection refused. Fix: fetcher genérico `_fetch_config_value(namespace, key,
tenant_id, default)` (str/int/float), URL default 3500→3600, `CONFIG_API_URL`+`PLUGHUB_TENANT_ID` no
compose, `depends_on: config-api (service_healthy)`, seed `evaluation.evaluator_pool`/`replay_speed_factor`,
consumer lê via config-api no startup, envs removidos.

Builds: `config-api` (+ rodar `config-seed` p/ as 2 chaves novas) + `session-replayer`. Guard 0/0.
**Validação (usuário)**: `curl .../config/evaluation?tenant_id=tenant_demo` lista `evaluator_pool` +
`replay_speed_factor`; logs do session-replayer mostram `Config API evaluation.evaluator_pool=avaliacao_ia`
e `Config API session.replayer_hydration_ttl_s=...` (agora os TTLs também pegam, antes caíam no default).
**Categoria C do §6 fechada.**

---

## Config HTTP Propagation — Fase 3: authorized_roles + guard lint (arco completo) (2026-06-12)

Fecha o arco `docs/arcos/config-http-propagation.md`.

**3b — authorized_roles (mcp-server stream masking)**: mesmo bug do context_rules. `loadAccessPolicy` lia
`{tenant}:masking:access_policy` (legacy, nunca escrito — `saveAccessPolicy` sem chamador) e os tiers
`plughub:cfg:...` (cache TTL), caindo sempre no hardcoded `["evaluator","reviewer"]`. Agora lê
`GET /config/masking → authorized_roles` (HTTP) com cache TTL 60s in-process; `saveAccessPolicy` removido;
caller em `session.ts` passa `CONFIG_API_URL`. authorized_roles vira de fato configurável.

**3c — secrets exemptos**: as credenciais `{tenant}:config:sms|whatsapp|voice:*` (access_token,
account_sid, auth_token) e `{tenant}:config:webchat:jwt_secret` são **secrets** (cat. A) — não vão pro
config-api por invariante. O override por tenant no Redis é capacidade **não implementada** (sem writer); a
fonte é o env. Não migradas (documentado no arc doc).

**3a — guard lint**: `infra/check_config_invariants.py` ganhou `config_cache_direct_read` — falha se algum
serviço fora do config-api ler `plughub:cfg:*` direto (`.get/.mget/.hget`). Trava a regressão do padrão
furado. **0 ofensores** após as Fases 1–3b.

Builds: `mcp-server-plughub`. Guard: rodar — deve seguir **0/0** (incl. o novo lint). **Arco completo
(Fases 1–3)** — F1.2, F2-TTL e masking (context_rules + authorized_roles) agora consomem config-api de
verdade via HTTP; secrets ficam em env por design.

---

## Bugfix — platform-ui nginx: PUT /config/masking/{key} dava 405 (2026-06-12)

Surfaceado ao validar a Fase 2 (salvar `context_rules` pela Masking page → 405). O nginx da platform-ui
(build de produção) tinha a location de rotas SPA `^/config/(access|billing|platform|masking|recursos)(/|$)`
**antes** do proxy do config-api. O `(/|$)` capturava `/config/masking/context_rules` (a escrita do
config-api) junto com `/config/masking` (a página) — `masking` é nome de página E de namespace do
config-api. O PUT caía no `try_files` (estático) → **405**. (webchat não colidia: não é nome de página.)

**Fix**: regex da location SPA trocada de `(/|$)` para `/?$` (casa só a página exata; escritas de 2
segmentos `/config/{ns}/{key}` caem no proxy do config-api) + incluídas as páginas que faltavam
(`resources|channels|canais|groups|calendars`) — antes quebravam em hard-refresh. Build: `platform-ui`.

---

## Config HTTP Propagation — Fase 2: mcp-server masking (2026-06-12)

Conserta o `context_rules` (masking de campo do ContextStore) e fecha o item "masking" da Fase 2 da
config-consolidation (§8 item 4). Mesmo bug das Fases anteriores: `masking.ts::loadContextMaskingConfig`
lia `plughub:cfg:...:masking:context_rules` direto do Redis (cache TTL transitória; default global nunca
escrito de forma durável — `saveContextMaskingConfig` era dead-code sem chamador), então quase sempre caía
no `DEFAULT_CONTEXT_MASKING_CONFIG` independentemente do valor configurado.

**Correção**:
- `mcp-server lib/masking.ts`: `loadContextMaskingConfig(configApiUrl, tenantId)` busca via
  `GET /config/masking?tenant_id=` (HTTP, resolve tenant→global), valida com `ContextMaskingConfigSchema`,
  fallback `DEFAULT_CONTEXT_MASKING_CONFIG`. Removido o `saveContextMaskingConfig` dead-code. O cache TTL
  60s in-process do `server.ts` (`getContextMaskingConfig`) foi mantido; `redis` saiu da assinatura de
  `getContextMaskingConfig`/`applyContextMaskingDynamic`.
- `config-api seed.py`: novo `masking.context_rules` global (= conteúdo do JSON órfão) — agora
  `GET /config/masking` retorna o default editável.
- `infra/config-seed/masking-context-rules.json` **aposentado** (órfão — nada o carregava; `git rm`).
- `CONFIG_API_URL` no serviço mcp-server-plughub (compose). Comentário de `@plughub/schemas/audit.ts`
  corrigido (descrevia leitura direta do Redis).

Sem env novo de negócio → guard 0/0. Builds: `config-api` (rodar seed) + `mcp-server-plughub` +
`@plughub/schemas` (se buildado à parte). **Validação (usuário)**: rodar o seed do config-api (cria
`masking.context_rules`); alterar `context_rules` na Masking page → `supervisor_state` reflete o novo
mascaramento (cache TTL ≤ 60s). **Próximo**: Fase 3 (varredura: `authorized_roles`,
`{tenant}:config:sms|whatsapp:*`; + guard lint opcional).

---

## Config HTTP Propagation — Fase 1: channel-gateway (conserta F1.2 + F2-TTL) (2026-06-12)

Abre o arco `docs/arcos/config-http-propagation.md`. **Diagnóstico**: o padrão "config-api vence via
leitura direta do Redis" (usado na F1.2 e na F2-TTL) **nunca funcionou** — as chaves
`{tenant}:config:webchat:*` que os resolvers liam **não são escritas por ninguém** (o config-api só
mantém a cache TTL `plughub:cfg:...`, e o channel-gateway não consumia `config.changed`). Resultado: os
adapters sempre caíam no default do código; mudar o valor no config-api não tinha efeito. (Os testes
unitários passavam porque mockavam o Redis retornando valor — testavam a lógica do resolver, não a
populção da chave.)

**Correção (padrão canônico, espelha `SessionConfigCache`/`RoutingConfigCache`)**:
- `webchat_config.py`: novo `WebchatConfigCache` — busca `GET /config/webchat?tenant_id=` via HTTP,
  cacheia in-process, defaults no código (auth_timeout_s=30, attachment_expiry_days=30).
- `main.py`: reload no startup + consumer `config.changed` (recarrega quando `namespace == "webchat"`).
- `attachment_store.py`: `resolve_attachment_expiry_days`/`resolve_ws_auth_timeout_s` passam a ler do
  `WebchatConfigCache` (não do Redis). Assinaturas mantidas — call-sites (4 adapters + webchat/webrtc)
  inalterados. Comentários "config-api vence via Redis" corrigidos.
- `config.py` + compose: setting `config_api_url` + `PLUGHUB_CONFIG_API_URL=http://config-api:3600`.
- Testes reescritos: `test_webchat_config.py` (cache) + `test_{attachment_expiry,ws_auth_timeout}_resolver.py`
  agora testam a leitura via cache (não Redis).

Sem env novo de config de negócio → guard 0/0 inalterado. Build: `channel-gateway`. **Validação
(usuário)**: alterar `auth_timeout_s` na WebChatConfigPage → o handshake WS passa a respeitar o novo valor
(agora de verdade); idem `attachment_expiry_days`. **Próximo**: Fase 2 (mcp-server masking) + Fase 3
(varredura + guard lint).

---

## Config Consolidation — F2-TTL: ws_auth_timeout (config-api vence) (2026-06-12)

Fatia §8 item 2 (TTLs/timeouts — matar duplicação env×config). Após a F1.2 (instance_ttl +
attachment_expiry), o timeout do handshake WS (`ws_auth_timeout_s`) ainda vinha do env
`PLUGHUB_WS_AUTH_TIMEOUT_S` no webchat, **ignorando** o `webchat.auth_timeout_s` que o config-api já tem
semeado e editável na WebChatConfigPage — mesma duplicação do attachment_expiry. O webrtc usava uma
constante hardcoded `_AUTH_TIMEOUT_S` (mesmo conceito, sem fonte de config).

**Mudança (config-api vence, espelha F1.2)**:
- `attachment_store.py`: novo `resolve_ws_auth_timeout_s(redis, tenant_id, default)` — lê
  `{tenant}:config:webchat:auth_timeout_s` do Redis (config-api), fallback ao default.
- `adapters/webchat.py`: o auth-wait resolve o timeout do config-api (`settings.ws_auth_timeout_s` vira só
  fallback). Tenant vem de `settings.tenant_id` (wiring), conhecido pré-auth.
- `adapters/webrtc.py`: **foldado** — passa a ler a mesma chave; `_AUTH_TIMEOUT_S` (30) vira só o default.
- `docker-compose.demo.yml`: `PLUGHUB_WS_AUTH_TIMEOUT_S` removido (comentário explica, como na F1.2).
- `check_config_invariants.py`: nova detecção `env_dup_ws_auth_timeout` — o guard passa a pegar
  reintrodução do env (antes nem olhava). Como o env já saiu, segue **0/0**.
- +5 testes (`test_ws_auth_timeout_resolver.py`).

Build: `channel-gateway`. **Validação (usuário)**: rodar o guard (0/0); alterar `auth_timeout_s` na
WebChatConfigPage e confirmar que o handshake WS respeita o novo valor (sem o env); webrtc idem.
**Próximo (§8)**: masking (`audit_policy`) → ABAC/users → seeds evaluation/pricing → defaults hardcoded
restantes (incl. `EVALUATOR_POOL`, `VITE_DEFAULT_POOL`, `REPLAY_SPEED_FACTOR` da cat. C).

---

## Config Consolidation — F2-pool: fechamento (D dissolvida, E resolvida) (2026-06-12)

Fecha a UI de pool da Fase 2. F2.A–C expuseram hooks, Transfer/@mention, agent_kind e session_reservation.
As duas últimas fatias se resolveram **sem código de UI**, por aplicação do invariante de fonte única:

- **F2.D dissolvida**: `evaluation`/`evaluation_template_id` são donos de **Quality → Campaigns**
  (evaluation-api); o `pool.evaluation` consumido por `rules-engine/evaluation_sampler` é caminho
  legado/dormente (`on_pool_config` nunca é chamado). `agent_groups` é dono do **módulo Groups + JWT**
  (`supervised_groups[]`, Arc 9). Expor qualquer um no pool duplicaria a fonte → fora do drawer por design.
- **F2.E resolvida (nada no pool)**: investigação confirmou que o deploy é consumido **ponta a ponta** —
  `promote` (next→current) publica `registry.changed` → orchestrator-bridge `bootstrap.request_refresh()`
  → `_build_desired_from_deploy` lê `deployed_skill_id`/`deployed_max_concurrent_sessions` do
  `GET /v1/pools` e provisiona as instâncias IA. Dono = tela **Fluxo → Deploy**; por fonte única, não se
  duplica no drawer.

Resultado: todo o gap de `pool-config-surface.md` está resolvido — exposto na UI (A–C), com dono em outro
módulo (D, E) ou deixado fora por decisão (`max_concurrent_sessions`, `webhook_skill_id`). Só docs nesta
fatia (sem código). Guard inalterado. **Próximo na Fase 2 (config-consolidation §8): TTLs/timeouts
(env×config) → masking → ABAC/users → evaluation/pricing seeds → defaults hardcoded.**

---

## Config Consolidation — F2.C: Tipo & Capacidade na UI de pool (2026-06-12)

Terceira fatia da Fase 2. Expõe `agent_kind` (human/ai) e `session_reservation` na tela
`config/resources/pool`. `max_concurrent_sessions` e `webhook_skill_id` ficaram **fora** por decisão
(enforcement deferred / config de canal — ver `pool-config-surface.md` § Decisões).

**Mudança (platform-ui apenas — backend já persiste/valida ambos)**:
- `PoolsPage.tsx`: seção "Tipo & Capacidade" (Select agent_kind inferido/human/ai + Input
  session_reservation). `agent_kind` só vai no payload quando explicitamente setado (`''` deixa o backfill
  do registry inferir); `session_reservation` envia número ou `null` para limpar. Aviso inline quando
  `agent_kind=ai` + fila configurada (espelha o guard backend queue⇒human). Coluna **Tipo** (badge
  Humano/IA) adicionada à lista de pools — visível sem abrir o drawer.
- `api/registry.ts`: `createPool`/`updatePool` agora propagam a mensagem de erro do registry (helper
  `poolError`) — o **422 de `Σ session_reservation > C`** aparece no banner do drawer em vez do genérico.
- i18n `configRecursos.pools.typeCapacity.*` (en + pt-BR).

Sem mudança de backend → guard não afetado. Build: `platform-ui`. **Validação (usuário)**: (1) editar
`retencao_humano` → tipo já vem **Humano**; pools IA (`sac_ia`, …) vêm **IA**. (2) Setar `agent_kind=ai`
num pool com fila → aviso inline. (3) Pôr `session_reservation` alto que estoure C → salvar deve mostrar
o 422 do registry no banner. **Próximo**: F2.D (coluna `evaluation` no backend + sampling_rate +
agent_groups).

---

## Config Consolidation — F2.B: Transfer + @mention na UI de pool (2026-06-12)

Segunda fatia da Fase 2. Expõe `supervisor_config.escalation_pools` (destinos do botão Transfer do
Console) e `mentionable_pools` (especialistas @mention) na tela `config/resources/pool` — antes só no YAML.

**Modelagem**: Transfer e @mention são **listas distintas** (Transfer = escalate/transfere o contato;
@mention = convida especialista em assist/conferência, não transfere). Ambas referenciam **pool_id**
(estável a versões de skill). `escalation_pools` é gravado **merge-safe** dentro de `supervisor_config`
(preserva `enabled`, `intent_capability_map`, etc.; `enabled` mantém valor existente / default `false` —
não exposto na UI por decisão: ativação de copilot/assist é coberta por hook `on_human_start`).
`mentionable_pools` é editado como lista alias→pool; o alias (`@handle`) é auto-sugerido do pool_id.

**Mudança (platform-ui apenas — backend já persiste/retorna ambos)**:
- `PoolsPage.tsx`: componentes `PoolListEditor` (Transfer) e `MentionListEditor` (@mention) + duas seções
  no drawer; submit faz merge-safe de supervisor_config e converte a lista de menções em Record.
- i18n `configRecursos.pools.transfer.*` e `pools.mention.*` (en + pt-BR).

Sem mudança de backend → guard não afetado. Build: `platform-ui`. **Validação (usuário)**: editar
`retencao_humano` → Transfer deve listar `sac_ia`, `reembolso_ia`, `portabilidade_ia`; @mention deve
listar `@copilot→copilot_sac`, `@auth→auth_ia`, `@auth_form→auth_form_ia` (carregados do YAML). Adicionar/
remover, salvar, confirmar via `GET /v1/pools` (supervisor_config.escalation_pools + mentionable_pools).
Confirmar no Console que o combo Transfer e o @mention refletem. **Próximo**: F2.C (agent_kind +
session_reservation).

---

## Config Consolidation — F2.A: Hooks de ciclo de vida na UI de pool (2026-06-12)

Primeira fatia da Fase 2 (UI de pool, `pool-config-surface.md`). Expõe `hooks` (on_human_start /
on_human_end / post_human) na tela `config/resources/pool` — antes só editável via YAML. Destrava
wrap-up + NPS configuráveis pela UI.

**Decisões de modelagem (com o usuário)**: os combos referenciam **pool_id**, não skill_id — o pool é
estável a mudanças de versão da skill-flow (referência por skill forçaria reescrever todas as referências
a cada deploy). O label do combo mostra a skill deployada (`deployed_skill_id`) só como dica visual.
`side` (agent/customer) é mantido porque distingue wrap-up de NPS; `nps_on_disconnect` só aparece quando
`side=customer`.

**Mudança (platform-ui apenas — backend já persiste/retorna `hooks` via `PoolRegistrationSchema`)**:
- `types/index.ts`: `Pool`/`CreatePoolInput`/`UpdatePoolInput` estendidos com os campos do gap (hooks,
  supervisor_config, mentionable_pools, agent_kind, session_reservation, evaluation, agent_groups,
  webhook_skill_id, deployed_skill_id read-only) + tipos `PoolHooks`/`PoolHookEntry`.
- `PoolsPage.tsx`: componente `HookListEditor` (lista por slot) + 3 listas no drawer; payload envia
  `hooks` quando presente, slots vazios para limpar, ou omite. Combo de pool rotulado pela skill.
- i18n `configRecursos.pools.hooks.*` (en + pt-BR).

Sem mudança de backend → guard não afetado. Build: `platform-ui`. **Validação (usuário)**: editar
`retencao_humano` → ver wrap-up (wrapup_ia, agent) + NPS (nps_ia, customer, skip) já carregados; salvar e
confirmar persistência via `GET /v1/pools`. **Próximo**: F2.B (Transfer `escalation_pools` +
@mention `mentionable_pools`, listas separadas).

---

## Config Consolidation — F1.1b: seed.py aposentado, pools com fonte única (2026-06-11)

Fecha a Fase 1 da consolidação. O `infra/seed/seed.py` (serviço `demo-seed`) ainda definia pools (lista
hardcoded duplicando o YAML), agent_types e channel_endpoints — a violação `pools_double_source`.

**Achados**: (1) os `agent_types` do seed são **mortos** — a entidade AgentType foi removida (Fase 3
C2/C3/C4); não há rota `/v1/agent-types` na registry. (2) Os `channel_endpoints` do seed **já falhavam**:
mandavam `label`, mas a rota `POST /v1/channel-endpoints` exige `display_name` (400). (3) Pools e Redis já
têm fonte única (RegistrySyncer + routing-engine, F1.1a).

**Mudança**:
- **channel_endpoints migrados** para `infra/registry/tenant_demo.yaml` (`channel_endpoints:`, com
  `display_name` correto) + `RegistrySyncer._sync_channel_endpoints` (POST idempotente, após os pools).
  Fonte única = YAML; e corrige o bug do `label`→`display_name`.
- **seed.py aposentado** (stub de migração; pode ser `git rm`) e **serviço `demo-seed` removido** do
  compose. Nada dependia dele.
- Guard: `pools_double_source` sai do allowlist → **`KNOWN` vazio**. Qualquer violação futura é NOVA
  (exit 1).

**Fase 1 da config-consolidation COMPLETA** (F0 contrato+guard · F1.1a Redis · F1.2 env×config · F1.1b
pools/seed). Guard: **0 violações conhecidas**. Provisionamento do demo agora sai 100% da config
(RegistrySyncer ← YAML; config-api; auth/eval/pricing seeds via API). Build: `orchestrator-bridge`
(RegistrySyncer). **Validação (usuário)**: restart bridge → `GET /v1/channel-endpoints` lista os 3;
guard 0/0; demo sobe sem `demo-seed`. **Próximo (Fase 2)**: expor campos de pool na UI
(`pool-config-surface.md`); migrar os demais seeds para bootstrap idempotente (Fase 3).

---

## Config Consolidation — F1.2: precedência env×config (config-api vence) (2026-06-11)

Triagem do perigo "config de negócio em env duplicando o config-api" (invariante "env only for
secrets/wiring"). Duas violações no guard.

**`PLUGHUB_ATTACHMENT_EXPIRY_DAYS` (channel-gateway) — dup real**: o config-api já tem
`webchat.attachment_expiry_days` (escrito no Redis como `{tenant}:config:webchat:attachment_expiry_days`),
mas os adapters liam `settings.attachment_expiry_days` (do env), **ignorando o config-api**. Fix
(config-api vence): novo helper `resolve_attachment_expiry_days(redis, tenant_id, default)` em
`attachment_store.py` — lê a chave do config-api no Redis, fallback ao default. Aplicado nos 4 adapters
(webchat/whatsapp/email/webrtc), espelhando o padrão já existente do `jwt_secret` per-tenant. Env
removido do compose. +5 testes (`test_attachment_expiry_resolver.py`).

**`PLUGHUB_INSTANCE_TTL_SECONDS` (routing-engine) — tuning em env**: sobrescrevia
`instance_ttl_seconds` (30→3600); sem chave equivalente no config-api e contra a spec ("TTL 30s"). Fix:
env removido — o routing-engine usa o default do código (30s, conforme spec; instâncias são renovadas
pelo heartbeat de 15s do bridge). Se um dia precisar ser configurável, vira chave no config-api (não env).

Guard: detecção de env passou a casar **assignment ativo** (`^\s*NAME:\s*valor`, ignora comentários);
`env_dup_instance_ttl` e `env_dup_attachment_expiry` saem do allowlist (3→1 violação conhecida — resta só
`pools_double_source` da F1.1b). Build: `channel-gateway` + `restart routing-engine` (env). **Validação
(usuário)**: guard 1/0; upload no webchat respeita o `attachment_expiry_days` do config-api; instâncias
AI seguem vivas (heartbeat).

---

## Bugfix — Console Transfer 8.3: rota REST era um stub vazio (2026-06-11)

Terceira e **última** camada do bug do Transfer. Após corrigir o contrato (8.1 — `escalation_pools` no
`SupervisorConfigSchema`) e a config (8.2 — seed no YAML), o combo Transfer **continuava vazio**.
Diagnóstico via runtime: a registry persistia `escalation_pools` corretamente
(`["sac_ia","reembolso_ia","portabilidade_ia"]`), mas a rota REST que o frontend chama —
`GET /api/supervisor_capabilities/:sessionId` (server.ts) — era um **stub hardcoded** que sempre
devolvia `{suggested_agents: [], escalations: []}`. A lógica real (ler `supervisor_config.escalation_pools`)
existia só no **tool MCP** `supervisor.ts`, nunca ligada à rota REST. Por isso o Transfer nunca funcionou.

**Fix**: a rota passa a resolver `tenant_id`/`pool_id` do `session:{id}:meta` e ler
`supervisor_config.escalation_pools` da agent-registry (`GET /v1/pools/{poolId}`, header `x-tenant-id`),
mapeando para `escalations: [{pool_id}]` — mesma fonte do tool. Fallback de URL corrigido p/
`agent-registry:3300`. `suggested_agents` segue `[]` (vinha do stub; sem regressão — os agentes
mentionáveis usam outro endpoint). Build: `mcp-server-plughub`.

Com as 3 camadas (contrato + config + endpoint), o Transfer lista os destinos configurados em
`supervisor_config.escalation_pools` do pool. Editar esses destinos na UI é a fatia F2-pools
(`pool-config-surface.md`).

---

## Config Consolidation — F1.1a: seed.py deixa de escrever Redis (2026-06-11)

Primeira fatia da triagem de perigos ativos. `infra/seed/seed.py` escrevia direto no Redis
(`{tenant}:pool_config:{id}` + `{tenant}:pools`) — violação do invariante "provisioning only via API".

**Achado (validado no código)**: é **redundante**. Ao registrar um pool na agent-registry, a registry
publica `registry.changed`; o routing-engine consome (`kafka_listener._handle_pool_event`) e o
`save_pool_config` (registry.py:1255) escreve **ambos** `pool_config:{id}` **e** `{tenant}:pools` — com
**mais** campos do que o seed escrevia. O boot normal já produz as chaves.

**Mudança**: removidos `seed_redis()`, o helper `RedisConn` (RESP cru) e a chamada no `main()` + a env
`REDIS_URL` (agora sem uso no seed). O guard `check_config_invariants.py` teve `seed_redis_write` retirado
do allowlist (burn-down: 4→3 violações conhecidas).

**Validação (usuário)**: após `restart` do demo, conferir que `{tenant_demo}:pool_config:retencao_humano`
e `{tenant_demo}:pools` continuam populados (pelo routing-engine, via `registry.changed`) — e o guard
roda sem violação nova. **Pendente F1.1b**: aposentar as DEFINIÇÕES de pool do seed.py (lista hardcoded
duplica o YAML) — migrar channel_endpoints p/ YAML/RegistrySyncer e deletar seed.py + serviço demo-seed.

---

## Config Consolidation — Fase 0: contrato + guard-rail (2026-06-11)

Fundação da consolidação de config (estratégia híbrida — `docs/arcos/config-consolidation.md` §8).

**F0.1 — Contrato**: seção **permanente** "Configuration — Single Source Invariants" no CLAUDE.md
(4 invariantes: fonte única por domínio; provisão só via API; todo campo editável na UI; env só
secret/wiring, config-api vence em duplicação). O burn-down das violações herdadas (F1–F4) é rastreado
no escopo "Config Consolidation" do TODO + guard.

**F0.2 — Guard-rail**: `infra/check_config_invariants.py` — script dependency-free (roda no host:
`python infra/check_config_invariants.py`) com allowlist de **4 violações conhecidas** (seed.py escreve
Redis direto; pools em fonte dupla YAML+seed.py; `PLUGHUB_INSTANCE_TTL_SECONDS` e
`PLUGHUB_ATTACHMENT_EXPIRY_DAYS` duplicando o config-api). **Falha (exit 1) se surgir violação NOVA**;
avisa quando uma conhecida é corrigida (para sair do allowlist). Burn-down objetivo da migração.

Sem build (script + docs). Próximo: F1.1 (de-duplicar pools — eliminar lista hardcoded + escrita Redis
do `seed.py`) e F1.2 (precedência env×config).

---

## Bugfix — Console Transfer "No destinations available" (contrato + config) (2026-06-11)

**Sintoma**: o botão Transfer do Console mostrava sempre "No destinations available".

**Causa-raiz (duas camadas)**:
1. **Contrato dessincronizado**: o Transfer lê `capabilities.escalations` ← `supervisor_capabilities`
   (mcp-server) ← `GET {agent-registry}/v1/pools/{id}` → `pool.supervisor_config.escalation_pools`. Mas
   o `SupervisorConfigSchema` (`@plughub/schemas`) **não declarava `escalation_pools`** — e a registry
   valida o body com `CreatePoolSchema.parse`/`UpdatePoolSchema.parse`, então o Zod **descartava** o
   campo no write. O dado nunca persistia, mesmo se semeado.
2. **Dado ausente na config**: nenhum pool definia `escalation_pools`.

**Fix (na origem, dado 100% na config — a registry é a fonte única de pool config)**:
- **Contrato**: `escalation_pools: z.array(z.string()).default([])` adicionado ao
  `SupervisorConfigSchema`. Sincroniza contrato↔consumidor; a registry passa a aceitar e persistir o
  campo (flui por `PoolRegistrationSchema` → Create/Update). +teste no `agent-registry.test.ts`.
- **Config**: `infra/registry/tenant_demo.yaml` — `retencao_humano.supervisor_config.escalation_pools =
  [sac_ia, reembolso_ia, portabilidade_ia]` (destinos customer-facing). O RegistrySyncer faz UPSERT na
  registry no restart do bridge → o dado vive na config; o mcp-server (sem mudança) passa a lê-lo.

Fix interino: o dado entra na registry via o YAML (RegistrySyncer); o mcp-server lê da registry. Build:
`@plughub/schemas` + `agent-registry`; restart `orchestrator-bridge` (re-sync do YAML mount).
**Direção (decidida 2026-06-11)**: o YAML passa a ser tratado como seed-a-eliminar — `escalation_pools`
e os demais campos de pool hoje só setáveis via YAML (hooks NPS/wrap-up, timeouts, queue, mentionable,
etc.) devem ser **expostos na tela `config/resources/pool`**, para o provisionamento sair 100% da
config. Inventário de campos + plano de UI: ver `docs/arcos/pool-config-surface.md` (a criar) e `TODO.md`.

---

## Bugfix — Console: contato vazava para todos os agentes do mesmo pool (2026-06-11)

**Sintoma**: com dois humanos logados no mesmo pool (ex.: admin + operator em `retencao_humano`), um
contato alocado a UM agente aparecia no Console do OUTRO — os dois "servindo" o mesmo contato.

**Causa-raiz**: o modelo de identidade por usuário existe (`registerHumanAgent` → `instanceId =
"human-{userId}"`, server.ts) e a Routing Engine aloca o contato a UMA instância, publicando
`conversation.assigned` com o `instance_id` alvo (bridge `_notify_human_agent_assigned`). **Mas** toda
conexão WS assina o canal do POOL `pool:events:{poolId}` e o handler aceitava QUALQUER
`conversation.assigned` daquele canal **sem filtrar pelo `instance_id`** → fan-out pro pool inteiro.
Regressão: o canal por pool é legado (1 humano por pool); o modelo por-usuário (C1) entrou sem filtrar.

**Fix**: a conexão calcula `expectedInstanceId = "human-${userId}"` no connect e descarta
`conversation.assigned` cujo `instance_id` aponta para outro agente. Aplicado nos **dois** caminhos de
entrega: o pub/sub ao vivo (`subscriber.on("message")`, antes do `forward()` que faz `ws.send`) e a
reentrega do `pool:pending_assignment` (reconexão). Lógica extraída para `lib/assignment-filter.ts`
(`shouldDropAssignment`, pura/testável). **Backward-compatible**: `userId` vazio (cliente legado) ou
`instance_id` vazio no evento → não filtra (nunca over-filtra).

**Testes**: `__tests__/assignment-filter.test.ts` (alvo diferente → drop; alvo próprio → keep; legado/
defensivo → keep; não-assignment → keep). Build: `mcp-server-plughub`.

**Limitação remanescente (notada)**: `pool:pending_assignment:{poolId}` é UMA chave por pool (last-write
wins) — se dois agentes têm pending simultâneo, a reconexão pode perder um. O filtro impede a entrega
ERRADA, mas a chave por-instância (`pool:pending_assignment:{poolId}:{instanceId}`) fica como melhoria
futura. Relaciona à fila pull/inbox (proposta). O "Transfer: No destinations available" (handoff
humano→humano) segue em aberto — investigar junto da fila.

→ Ver `docs/guias/conference-mechanics.md` § Histórico.

---

## Bancada — item 6: débitos de teste pré-existentes corrigidos (2026-06-11)

Fecha os follow-ups A. Ambos os débitos eram **drift teste×implementação** (a impl evoluiu, os testes
ficaram no contrato antigo) — a produção estava correta; só os testes desatualizados.

**6a — `TestQueryAgentAvailabilityReport` (6 falhas)**: dois níveis de drift. (1) Assinatura:
`query_agent_availability` virou `(client, database, tenant_id, ...)` — a produção (`reports.py`) já
chamava assim, mas o teste passava `(store, tenant_id)` → `TypeError`. (2) **Modelo de mock obsoleto**:
`_fetch_agent_availability` foi reescrita na Fase 1b — agora faz **4 queries** (login_intervals,
pause_intervals, reason breakdown, segments busy) agrupadas por `instance_id`, não o antigo
`count/agg/reason` (3 queries). O mock fornecia só 3 resultados → na 4ª chamada o `side_effect`
esgotava e o `StopIteration` dentro do `asyncio.to_thread` **travava o pytest** (não falhava — pendurava).
Tests reescritos: helper fornece os 4 resultados com as colunas novas; happy-path valida o merge
(logged/paused/busy/available + reason_breakdown por identidade); `call_count == 4`; IN clause nas 4.

**6b — `resolve.test.ts` (3 falhas)**: o `resolve` migrou para o modelo **multi-instância** (igual ao
menu): result key `menu:result:{sid}:{instanceId}` e flag de espera como HASH (`hdel(menu:waiting:{sid},
field)`), mas os testes usavam o modelo antigo de chave plana. Corrigidos: (1)+(2) os mocks de `blpop`
dos interrupts `@mention` (`_mention_trigger_step`/`_mention_terminate`) passam a devolver a key com
`instanceId` (senão o branch de mention não casa e cai em on_success); (3) o teste de limpeza do
`waitingKey` passa a checar `hdel` (não `del`).

Nenhuma mudança de código de produção — só testes. Build: `analytics-api` e `skill-flow-service`
(os testes são assados na imagem; rebuild p/ rodar a versão nova). **Follow-ups A (itens 1–6) completos.**

---

## Bancada — item 5: DROP `segments.nps_score` (session_signal como fonte única) (2026-06-11)

Aposenta a coluna vestigial `segments.nps_score`. Desde o cutover F10.3b, o NPS de segmento é
gravado/lido via `session_signal` (grain=segment, metric=nps); a coluna seguia no schema sem ser
escrita. **Havia um leitor vivo esquecido**: `query_agents_cross` (F6) ainda lia `segments.nps_score`
— migrado também.

**5a — leitor migrado** (`reports_query.py`): `query_agents_cross` deixa de ler `segments.nps_score`;
o NPS por agente passa a vir de `session_signal` (grain=segment, metric=nps) ⋈ `segments` por
`segment_id`, agregado por `agent_key` e mesclado no Python (como o agregado de qualidade). Espelha o
`_compare_nps_lens`. Depois disto, **nada** lê a coluna.

**5b — ingest + bridge limpos**: removida `nps_score` do CREATE TABLE `segments`, da migração ADD
(substituída por `_DDL_SEGMENTS_DROP_NPS` — `DROP COLUMN IF EXISTS` idempotente na lista de
migrações), do `_SEGMENT_COLS`, do `_segment_row` e do `parse_participant_event` (analytics). No
bridge (`orchestrator-bridge`), removido o param `nps_score` de `_publish_participant_event` e a
leitura vestigial no `_republish_segment_from_signal` (o acumulador `seg_signal` já não populava NPS
desde a F10.3b). `ConversationParticipantEventSchema` nunca declarou o campo — remoção limpa.

**5c — DROP automático**: a migração `_DDL_SEGMENTS_DROP_NPS` roda no startup do `analytics-api`
(idempotente) — sem passo manual; instalações existentes têm a coluna removida no próximo boot.

**Testes**: `TestQueryAgentsCross` atualizado (query passa de 2→3: seg → nps → eval; valida que o NPS
lê `session_signal`, não `nps_score`). Comentários stale atualizados (`survey.ts`, `agente_nps_v1.yaml`,
`reports_query.py`, `clickhouse.py`, `models.py`).

Build: `analytics-api` + `orchestrator-bridge` (Python). `@plughub/schemas` rebuild (comentários).
`agente_nps_v1.yaml` é mount → restart orchestrator-bridge.

---

## Bancada — item 4 (F7): escalação real destravada + F8 adiado (2026-06-11)

Item 4 dos follow-ups A — substituir fixtures sintéticos por dado E2E real. **F7 primeiro**; **F8
adiado** (decisão: o pipeline de avaliação `agente_avaliacao_v1` não roda no demo — test-grade, sem
associação form/campanha; consertá-lo é arco próprio, não cabe nesta bancada).

**Gap F7 corrigido (YAML)**: o `escalate` do `skill_atendimento_sac_v1` não declarava `reason` — só
`error_reason` (que é a causa de erro do close, não o motivo de escalação F7). Sem `reason`, o
`executeEscalate` não persiste `pipeline_state.results.escalation_reason` e o segmento da IA fica sem
motivo. Adicionado `reason: specialist_needed` (estático — Phase-1 sempre escala p/ especialista
humano; ids espelham `agent_activity/escalation_reasons`). Skills são mount → **restart do
orchestrator-bridge re-sincroniza**, sem rebuild.

Com isso, um único fluxo real (webchat → `sac_ia` escala → `retencao_humano` humano → wrap-up) produz
**ambos** os motivos reais: segmento IA (`specialist_needed`, do `escalate`) e segmento humano (motivo
escolhido no menu do `agente_wrapup_v1` quando classifica "escalado"). Substitui o fixture sintético
(`segments.escalation_reason` via `ALTER UPDATE` em 55 segmentos — F7 original).

**Caminho humano** já estava cabeado (`agente_wrapup_v1`: choice `escalado` → menu de 8 motivos →
`seg_signal` → bridge). Nada a mudar ali.

**Validação E2E + limpeza de fixture** (rodadas do usuário): (1) limpar o sintético —
`ALTER TABLE segments UPDATE escalation_reason='' WHERE escalation_reason!=''` (hoje tudo é sintético);
(2) rodar o fluxo real; (3) conferir `segments.escalation_reason` com 1 linha IA + 1 humana reais.
F5 multi-humano (NPS por 2 handoffs sequenciais) valida na sequência. **F8 permanece com fixture
documentado** até o arco do avaliador.

---

## Bancada — item 3: regra de comparabilidade por formulário (qualidade) (2026-06-11)

Fecha o item 3 dos follow-ups A **redefinindo o escopo** após discussão de validade metodológica. A
proposta original ("alinhar dimensões equivalentes entre formulários por dimension_id/label") foi
**descartada**: fundir dimensões de forms diferentes inventa uma equivalência que não existe (rubricas,
pesos, escala e calibração diferem por form) — produziria um heatmap que *parece* comparável e não é.

**Decisão (regra de comparabilidade)**: a validade depende do que se mantém constante. Comparar
**entre agentes** exige o **mesmo formulário**; comparar **entre formulários** só é legítimo para um
**único agente** (a campanha/régua é o eixo da comparação, não um disfarce). Foi descoberto que a lente
`quality` (média final) **já comparava cross-agente + cross-form silenciosamente** (`avg(overall_score)`
por agente+data, sem olhar `form_id`) — aplicar a regra **corrige** esse ponto cego.

**Backend** (`_compare_quality_lens`): a lente passa a expor `summary.form_ids` (união distinta dos
formulários que avaliaram o agente no período) via `groupUniqArray(er.form_id)`. Sem mudança de grão,
média ou atribuição.

**UI** (`AgentsBenchPage`, render da lente `quality`): banner de comparabilidade —
(a) multi-agente **e** forms misturados → **guard** (bloqueia o gráfico, "comparação entre agentes
exige o mesmo formulário" + lista os forms); (b) **um único agente** em vários forms → gráfico + **ressalva**
("médias de formulários/campanhas diferentes do mesmo agente — contexto, não régua única"); (c) mesmo
form / sem seleção → inalterado. A lente `quality_criteria` (dimensões) mantém o guard same-form atual —
**não se mexeu no mérito das dimensões** (escolha do usuário: só a média final).

**Testes**: `test_reports.py::test_quality_exposes_form_ids_union`. i18n `bench.quality.*` (en + pt-BR).

Build: `analytics-api` (Python) + `platform-ui` (rebuild). Sem migração.

---

## Bancada F11.1 — enrichment de `session_at` para surveys diferidas (2026-06-11)

Implementa a espinha da F11 (item 2 dos follow-ups A): **bucketização correta de sinais de survey
diferida**. O parser `parse_session_signal_event` cravava `session_at = captured_at` — correto só no
no-ato (mesmo dia). Numa survey **diferida** (collect/workflow dias depois), `captured_at` é a chegada,
não a data da sessão original — quebrando a **regra de ouro** (§7): a quali deve bucketizar pela data
da sessão **original**, alinhada com o quanti.

**Mecanismo (consumer-side, sem migração)**: o sinal é chaveado por `origin_session_id`. Após o parse,
para o tópico `session.signals`, o consumer resolve o `opened_at` da sessão original e **sobrescreve
`session_at`**; `date` (partição) e TTL seguem do row builder (já derivam de `session_at`). Fallback
seguro = `captured_at` (origem ainda não ingerida / erro de lookup) — nenhum evento é descartado.

- `AnalyticsStore.lookup_session_opened_at(tenant, session_id)` — `SELECT opened_at FROM sessions FINAL …`
  (ISO8601; molde dos lookups Arc 5; `asyncio.to_thread`).
- `consumer._enrich_signal_session_at(rows, store)` — cache FIFO bounded (`_session_opened_cache`,
  espelha `_channel_cache`); chamado em `_process_message` no ramo `session.signals`.
- Parser inalterado no contrato (default `session_at=captured_at`); docstring atualizada.

**Testes** (`test_consumer.py::TestSignalSessionAtEnrichment`): diferido → `session_at=opened_at`
(captured_at intacto); origem ausente → fallback captured_at; erro de lookup → fallback; cache evita
lookup repetido.

**Grão `journey`** já é aceito pelo parser/schema (`_SESSION_SIGNAL_GRAINS`) — sem trabalho de schema.

**F11.2 (validação E2E — diferido simulado, decisão do usuário)**: publicar `session.signals` (ou chamar
`survey_record`) com `origin_session_id` de uma sessão cujo `opened_at` é dias anterior + grão `journey`,
e conferir `session_at = opened_at` no ClickHouse. Sem agendador (escolha "simular via curl/seed").

Build: `analytics-api` (Python) exige rebuild. Sem migração de schema.

---

## Bancada — Fechamento (follow-ups A) item 1: built-in `$.segment_id` no interpolate (2026-06-11)

Quick win do plano "fechar a bancada". O `survey_record(grain=segment)` precisa do `segment_id` do
próprio agente para atribuir o sinal. O `ctx.segmentId` já chegava ao `StepContext` (bridge passa via
`/execute`, já usado em `@segment.*` e escritas `scope: segment`), mas **não estava exposto como
built-in JSONPath** — a YAML só alcançava `$.session_id`/`$.tenant_id`/`$.customer_id`/`$.instance_id`.

**Mudança (1 linha)**: `resolveJsonPathRef` (`interpolate.ts`) ganha `segment_id: ctx.segmentId` no
`evalContext`. Skills passam a ler `$.segment_id` e entregá-lo a `survey_record` — sinal de segmento
"sobre si mesmo" genérico via skill, **sem o bridge injetar nada** (o caso NPS-sobre-o-humano no
`on_human_end`, com segment_id de OUTRO agente, segue dependendo do bridge — F10.3b, inalterado).

**Teste**: novo caso em `invoke.test.ts` ("resolve o built-in $.segment_id do próprio agente") cobre o
caminho real `resolveInputValue`→JSONPath num step invoke (`survey_record`).

Build: `skill-flow-engine` (TS) exige rebuild. Sem migração, sem mudança de conferência.

---

## Bancada de agentes F10.3b — cutover F5: NPS de segmento unificado em session_signal (2026-06-10)

Unifica o NPS por agente (grão segmento) no mesmo store/fluxo dos demais grãos (Opção 2): **um caminho
de escrita** (`survey_record`) + tratamento diferente por grão **na leitura** da bancada. Decisão do
usuário: evitar os dois fluxos de NPS que a alternativa "bridge emite" traria.

**Write (caminho B, unificado)**: o `agente_nps_v1` (hook `on_human_end` side=customer) passa a chamar
`survey_record(grain=segment, segment_id, agent_key, signals=[{nps}])` — mesmo fluxo de session/journey.
A atribuição vem do `@ctx`: o **bridge** (`fire_pool_hooks`, on_human_end) escreve
`session.surveyed_segment_id` + `session.surveyed_agent_key` (segment_id do humano via `hook_conf`;
agent_key = user_id derivado do `instance_id` do `human_seg`). `survey_record` é tenant-explícito
(sem token), então o hook chama direto.

**Read (lente migrada)**: `_compare_nps_lens` lê `session_signal` (grain='segment', metric='nps'),
`INNER JOIN segments` por `segment_id` para `agent_type`/`label` (ledger do segmento). As lentes `nps`
(por agente) e `session_nps` (contexto) passam a ler **a mesma tabela** — **acaba a duplicação** de
plumbing NPS/CSAT entre `segments` e `session_signal`.

**Legado removido (cutover final)**: validado o write do hook real, o bridge **deixou de escrever**
`segments.nps_score` — `_apply_nps_to_segment` + a leitura de `nps_resposta` no `process_routed` foram
removidos. A coluna `segments.nps_score` fica **vestigial** (histórico congelado, sem write nem read);
um `DROP COLUMN` é polish opcional.

**Validação E2E**: (1) testes `test_nps_lens_reads_session_signal` + `session_nps` passam; (2) seed →
lente `nps` lê de `session_signal`; (3) **fluxo humano real** (sac → escala `retencao_humano` → humano
resolve → cliente responde NPS) → `[survey_record] invoked grain:segment, segment_id, agent_key, nps=8`
+ `published`. **Cutover F5 completo e finalizado.** **Fatia F10 inteira concluída** (resta só F11:
survey diferida + grão journey ponta-a-ponta, arco futuro).

---

## Bancada de agentes F10.3a — exposição do NPS de sessão na bancada (2026-06-10)

Lente `session_nps` no `/reports/agents/compare`: `session_signal` (grain='session', metric='nps') ⋈
atribuição por `session_id` (último primary não-sintético, F2) → NPS de **sessão** dos contatos que o
agente atendeu. É o cruzamento §8 (NPS do agente × NPS da sessão) — **contexto não-atribuível** a um
agente (o sinal de sessão não tem agente; aqui o agente vem da sessão atendida). Bucketiza por
`session_at` (regra de ouro §7); N sempre visível.

**UI**: seção "Voz do cliente" no detalhe type-aware (`AgentDetail`) com 2 tiles — **NPS · agente
(segmento, F5)** × **NPS · sessão (contexto, F10.3a)** —, cada um com N. i18n en+pt-BR.
`session_nps` adicionada a `_COMPARE_LENSES` + `LensId`.

**Validação**: teste `test_session_nps_lens_reads_session_signal` (lê session_signal, grain='session',
avg/NPS corretos) — passa. Endpoint vivo: `lens=session_nps` → 200 (`entities:[]` quando não há sinal
de sessão atribuível; popula ao chavear sinal a contato real com segmento).

**Não toca a F5**: o NPS por agente (grão segmento) segue lendo `segments.nps_score`. A unificação
total (Opção 2) — segment NPS via `survey_record`, lente migra para `session_signal`, aposenta
`segments.nps_score` — é a **F10.3b (futura)**, onde a duplicação NPS/CSAT entre `segments` e
`session_signal` some de vez.

---

## Bancada de agentes F10.2b.2 — coleta real de NPS via delegate (inbound_only) (2026-06-10)

Fecha a F10.2b: a survey de sessão coleta o **NPS real do cliente** (não mais valor semeado) e grava o
sinal contra a sessão original. Mecanismo = **`delegate`** (proven Arc 19; `collect` é legado Arc 4).

**Fluxo (inbound_only, espelha portabilidade)**: `skill_survey_v1` ganha step `delegate →
survey_collector_ia` passando `contact_identifier` (sem `customer_present`) — `handle_delegate` grava
`pending_workflow:{contact}=resume_token` e o coletor vai a `aguardar_inbound` (survey fica pendente).
O cliente **reconecta via webchat** no pool de entrada `survey_reconnect_ia` (`agente_survey_reconnect_v1`):
informa o `contact_identifier`, `pending_workflow_get` acha a pendência e delega ao coletor
(`agente_survey_nps_v1`) com `customer_present=true` + o `resume_token` da survey. O coletor faz
notify + menu NPS 0–10 → `workflow_resume(payload={nps})` → a survey retoma o `delegate`
(`pipeline_state.coletar_nps.nps`) → `survey_record(grain=session, value=<nps real>)` → `session_signal`.

**Novos componentes**: skills `agente_survey_nps_v1` (coletor) + `agente_survey_reconnect_v1` (intake de
reconexão); pools `survey_collector_ia` + `survey_reconnect_ia`; `survey_reconnect_ia` no dropdown do
`webchat-test.html`.

**Fix de plataforma — recursão de arrays no `interpolate.ts`**: `resolveInputValue` recursava só objetos
planos, **não arrays** — refs dentro de arrays (ex.: `signals: [{value: "$.pipeline_state.coletar_nps.nps"}]`)
ficavam como string literal e nunca resolviam (o `survey_record` não recebia o valor). Agora recursa
arrays também. Geral: qualquer `invoke` com refs dentro de arrays resolve (complementa o fix do schema
de array da F10.2b.1).

**Validação E2E real**: trigger → survey pendente → reconexão webchat (`11888888888`) → cliente responde
NPS=8 no menu → `survey_record invoked value:8` + `published` → `session_signal(grain=session, nps=8,
neutro, origin=sess-real-2)`. **Toda a fatia F10.2 (session_signal grão session/workflow/journey) completa.**

---

## Bancada de agentes F10.2b.1 — survey disparada por workflow + 4 fixes de plataforma (2026-06-10)

Esqueleto trigger→record da pesquisa de sessão: o **passo final do fluxo primário** delega a um
sub-workflow de pesquisa (perfil webhook) que religa à sessão original e grava o sinal via
`survey_record`. Validado E2E (trigger direto da survey → `session_signal(grain=session)` chaveado ao
`origin_session_id`). A fatia expôs e corrigiu **4 bugs de plataforma**:

**Implementação F10.2b.1**: `survey_record` passa a aceitar **`tenant_id` explícito** (como
`workflow_trigger`/`context_set` — workflows não têm `session_token`); + logging. `skill_survey_v1`
(perfil workflow, pool webhook `survey_processo_ia`) lê `@ctx.session.origin_session_id` e chama
`survey_record(grain=session, signals=[{nps}])`. `skill_atendimento_sac_v1` ganha step `disparar_survey`
(`invoke workflow_trigger`, `origin_session_id=$.session_id`) no caminho resolvido.

**Fix 1 — input array no schema de skill** (`@plughub/schemas` `skill.ts`): `StepInputValueSchema` era
`string|number|boolean|record(escalares)` — rejeitava **arrays e objetos aninhados** (422 no upsert da
skill). Ampliado para JSON recursivo (arrays + objetos). Geral: qualquer `invoke` agora passa
parâmetros estruturados a tools (ex.: `signals: [{...}]`). O engine já resolvia arrays em runtime.

**Fix 2 — resolução webhook `skill_id`→pool** (`routing-engine`): a resolução **nunca existira** —
`get_candidate_pools` filtra só por canal; `webhook_skill_id` era armazenado mas **nunca casado**.
Funcionava por acaso com 1 pool webhook (único candidato). Com o 2º pool (survey), triggers caíam no
fallback (portabilidade). `router.route()` agora resolve por `webhook_skill_id == event.skill_id`
quando `pool_id` é None + canal webhook (fallback ao scan p/ retrocompat). `webhook_skill_id` setado
nos YAMLs dos 2 pools webhook (o campo não era derivado do `skill_id` do topo, que o zod stripa).

**Fix 3 — `skill_id` no evento inbound** (`routing-engine` `models.py`): `ConversationInboundEvent`
**não declarava `skill_id`** — o pydantic descartava o campo do evento webhook. Adicionado
(`skill_id: str = ""`), sem o qual o Fix 2 não tinha como casar.

**Fix 4 — auth da tool** (já em Implementação): `survey_record` tenant-explícito remove o atrito de
`session_token` em workflows.

**Infra demo**: a governança de capacidade (`Σ deploys ≤ C`) exige teto; o pool de survey precisou
`INCRBY tenant_demo:quota:max_concurrent_sessions` (C estava cheio com os 16 pools). Documentado no YAML.

**Validação E2E**: trigger → `pool=survey_processo_ia` → `survey_record invoked/published` →
`session_signal(grain=session, nps=9, promotor, origin=sess-test-8)`.

**Pendente**: F10.2b.2 (I/O real do cliente via `collect`, substitui o valor semeado). A fiação
`sac → workflow_trigger` está in-place; valida-se rodando um contato sac que resolve.

---

## Bancada de agentes F10.2a — `survey_record` + `session.signals` (store unificado) (2026-06-10)

Pivota o ingest da `session_signal` do dual-write sobre `agent_event` (F10.1) para uma **tool MCP
dedicada `survey_record`** + tópico `session.signals`. Decisão: a gravação de pesquisa é explícita
(um `invoke` no skill-flow), não um efeito colateral de `agent_event` — `origin_session_id`, `grain`
e as métricas são parâmetros estruturados de 1ª classe, sem a checagem de namespace `category[0]==pool`
do Arc 12 nem convenção de sufixo.

**Modelo (decisões 2026-06-10)**: a pesquisa de contato/processo roda numa **survey OUTBOUND** (sessão
própria) que religa à sessão original e grava o sinal **contra ela** (`session_signal.session_id =
origin_session_id`). O **disparo** é responsabilidade do **fluxo primário no passo final** (delega a um
sub-workflow de pesquisa passando o `session_id`) — hook de fechamento de pool fica como fallback;
mecânica fica para a F10.2b. **Store unificado (opção 2)**: TODOS os grãos
(`segment|session|workflow|journey`) moram em `session_signal`, gravados explicitamente; `segment`
carrega `segment_id`+`agent_key` (atribuição). Vocabulário: `journey` é rótulo de grão (relacionamento
multi-sessão), **não** a entidade Journey eliminada; timing (no ato × diferido) = `captured_at` ×
`session_at`, não grão.

**Implementação**: `@plughub/schemas` `survey.ts` (`SignalGrainSchema` 4 grãos, `SESSION_SIGNAL_GRAINS`,
`SurveySignalSchema`, `SurveyRecordInputSchema`, `SessionSignalEventSchema`). mcp-server `tools/survey.ts`
(`survey_record` → Kafka `session.signals`), registrada no `server.ts`. analytics-api:
`parse_session_signal_event` (1 linha/métrica, chaveado por `origin_session_id`, N métricas,
normalização nps/csat, `segment` exige `segment_id`); `session_signal` ganha `segment_id` (na chave de
dedup `(tenant, session, grain, segment_id, metric)`) + `agent_key`. Tópico `session.signals` no compose
+ tabelas Kafka/Zod no CLAUDE.md. **Revertidas** as edições transitórias no `agent_event` (contrato
Arc 12 intacto). Dual-write da F10.1 removido.

**Validação E2E**: 118 testes. Smoke real no demo — `session.signals` (grãos session + segment) →
linhas chaveadas à sessão original; `segment` com `segment_id`/`agent_key`; coerção `"9"→9`;
normalização promotor/satisfeito/neutro.

**Pendente (F10.3, cutover F5)**: a `segments.nps_score` (F5) segue como fonte da lente por agente até a
bancada migrar para ler tudo de `session_signal`; nesse cutover o hook de NPS de segmento passa a chamar
`survey_record` (com `segment_id`/`agent_key` via `@ctx`) e o `seg_signal`/`nps_score` é aposentado.
Resolve de uma vez a duplicação de plumbing NPS/CSAT entre `segments` e `session_signal`.

---

## Bancada de agentes F10.1 — camada de dados `session_signal` (2026-06-10)

Primeira sub-fase da F10 (item deferred mais estrutural do §7): voz do cliente/agente no grão
**contato/jornada**, não atrelado a um segmento. **Não toca o mecanismo de conferência.**

**Recon (ETAPA 0)** validada no código: Journey eliminada (Arc 19 Fase F) → "contato" **é** a
`session_id`; religação multi-sessão via `origin_session_id` (não `journey_id`, vestigial). Hooks
NPS/wrap-up não emitem `agent_event` hoje (gravam ContextStore + `seg_signal`). Decisões travadas:
captura via **Arc 12 `agent_event` + normalizador** (sem pipeline novo); religação por
**`origin_session_id`** (coluna `journey_id` mantida por compat); grão contato/jornada **não**
atribuível a agente (`agent_key=''`). Desenho completo em `docs/arcos/analytics-agents-workbench.md` §14.

**Implementação** (`packages/analytics-api`): tabela `analytics.session_signal` (ReplacingMergeTree,
dedup por `tenant+session+grain+metric`, TTL 2a em `session_at`); `_session_signal_row` +
`_SESSION_SIGNAL_COLS` + `insert_session_signal`; dispatch no `_write_row`. Parser
`parse_agent_business_event` agora faz **dual-write**: quando o *leaf* da category casa
`_SIGNAL_METRIC_MAP` (`nps_contact`/`csat_contact`/`*_journey`), retorna `[business_event, session_signal]`.
Normalização `_normalize_signal_value`: NPS 0–10 → promotor(≥9)/neutro(7–8)/detrator(≤6); CSAT 1–5 →
satisfeito/neutro/insatisfeito. Bucketização por `session_at` (regra de ouro §7).

**Validação E2E**: 110 testes (10 novos: normalização + dual-write + grão jornada + dispatch). Smoke
real no demo — `agent.events` NPS-contato → 1 linha em `session_signal` (`grain=contact`,
`value_label=promotor`, `agent_key` vazio) + cru preservado em `agent_business_events`.

**Próximo**: F10.2 (captura de produção via `post_human` + `agente_nps_contato_v1` — toca conferência);
F10.3 (endpoint + bancada). F11 (futura): survey diferida grão jornada (`captured_at ≠ session_at`).

---

## Bancada de agentes F7 — motivo de escalação normalizado (2026-06-09)

Taxonomia configurável de motivos de escalação + lente na bancada. Captura **humano + IA**,
decisão de seed de 8 motivos. `handoff_reason` segue como nota livre; o motivo normalizado é
uma dimensão agregável nova.

**F7.1 fundação**: config `agent_activity/escalation_reasons` (8 itens `{id,label,requires_note}`,
espelha `pause_reasons`, override por pool via `escalation_reasons:{pool_id}`). `ContactSegment`/
`ConversationParticipantEvent` + `escalation_reason`; `EscalateStep` + `reason`. ClickHouse
`segments.escalation_reason Nullable(String)` (migração idempotente) + cols/row + parser.

**F7.2 captura humano**: `agente_wrapup_v1` ganha menu de motivo (interaction list, 8 opções) via um
`choice` que dispara só quando `wrapup_classificacao == escalado`. Bridge: `_apply_wrapup_to_segment`
grava `escalation_reason` no acumulador `seg_signal` (só p/ outcome `escalated`); `_publish_participant_event`
+ `_republish_segment_from_signal` propagam o campo.

**F7.3 captura IA**: `escalate` step `reason` → `executeEscalate` persiste via `output_as` em
`pipeline_state.results.escalation_reason`; bridge lê na conclusão do agente IA (`participant_left`
nativo) e estampa no segmento. `conversation_escalate` repassa ao Rules Engine (`process_context`).

**F7.4 lente**: `_compare_escalation_reason_lens` — distribuição por agente (`summary.reasons[]`, só
família escalate com motivo), espelha `pause_reason`. Sai de pending. Teste.

**F7.5 UI**: lente "Motivo de escalação" (universal) reusa `StackedReasonBars` parametrizado
(`valueMode='count'` + `reasonLabels`); hook `useEscalationLabels` busca o config e remapeia
id→label (PT). i18n en+pt-BR.

**Validação**: testes parser + lente; E2E com fixture (`segments.escalation_reason` semeado via
`ALTER UPDATE` em 55 segmentos escalados — demo sem fluxo de escalação humana E2E), legenda com
labels do config. **Bancada Arc workbench: F1–F9 completas.**

→ Ver `docs/guias/conference-mechanics.md` § Histórico (escalation_reason no acumulador).

---

## Bancada de agentes F8 — lente quality_criteria (qualidade por dimensão) (2026-06-09)

Decompõe a nota de qualidade em **dimensões** do `EvaluationForm` — heatmap agente×dimensão na
bancada + radar de perfil no detalhe. Decisões: eixo = **dimensão** (critério cru vira drill-down);
comparável **só dentro do mesmo formulário** (guard na UI).

**F8.1 — ingest** (`clickhouse.py`, `models.py`, `consumer.py`): nova tabela
`analytics.evaluation_dimension_scores` (ReplacingMergeTree(ingested_at), ORDER BY
tenant/result/dimension). `parse_evaluation_event` emite 1 linha por dimensão a partir de
`dimensions[]` (Arc 6) **ou**, como fallback, de `dimension_threads[]` (caminho real do
`agente_avaliacao_v1`/Arc 13 — {dimension_id, score}). Atribuição ao agente AVALIADO é query-time
via `session_id` (como a lente quality, F2). Dispatch + 5 testes de parser/dispatch.

**F8.2 — query** (`reports_query.py`): `_compare_quality_criteria_lens` — nota média por
(agente, dimensão) via join de atribuição, grão snapshot do período em `summary.dimensions[]`
(como wrapup em `summary.dispositions[]`) + `summary.form_id` para o guard. `quality_criteria`
sai de `_COMPARE_LENSES_PENDING`. Teste `test_quality_criteria_lens_dimensions_in_summary`.

**F8.3 + F8.4 — UI** (`AgentsBenchPage.tsx`): lente "Qualidade por dimensão" (universal);
**heatmap** agente(linhas)×dimensão(colunas), célula colorida 0–10 (`scoreColor`), coluna n,
legenda, "Form: …" e **guard de comparabilidade** (avisa quando os selecionados misturam forms);
**radar** das dimensões no detalhe do agente (cor estável). i18n en+pt-BR (`bench.lens.quality_criteria`,
`bench.criteria.*`, `bench.chart.selectForQuality`, `bench.detail.qualityProfile`).

**Validação**: 47 testes (Evaluation/Dimension/Compare/Cross). E2E com **fixture** (`evaluation_dimension_scores`
semeada dos `evaluation_results` reais — o demo não tem pipeline de formulário/avaliador): heatmap
admin(n=6)×sac_ia(n=1) + radares por tipo. Em produção o `agente_avaliacao_v1` popula via
`dimension_threads`. **Bancada Arc workbench: F1–F6, F8, F9 completas.**

---

## Bancada de agentes F9 — pool-average como pseudo-entidade `pool:` (2026-06-09)

Refinamento: permite **fixar a média de um pool** como linha no gráfico da bancada e comparar
médias de 2+ pools lado a lado — sem dependência de ingest (só query + UI).

**compare** (`reports_query.py`): `entities` aceita `pool:<pool_id>`. Helpers fatorados da F3 —
`_per_agent_for_lens` (computa per_agent+metric_keys p/ lente/escopo), `_mean_series` (média
aritmética por bucket, gap≠0 — decisão fechada), `_aggregate_pool_summary` (escalares→média,
`reasons`/`dispositions`→soma por id, `total`→soma). Para cada `pool:<id>` o per_agent é recomputado
escopado ao pool e devolvido como entidade `{agent_key:"pool:<id>", agent_type:"__pool__",
label:"média · <id>", pool_id, n, series, summary}` (mesma semântica da média global). Teste
`test_pool_pseudo_entity_aggregates_pool_average`.

**platform-ui** (`AgentsBenchPage`): botão **μ** no cabeçalho de cada pool fixa/desafixa
`pool:<id>` em `selected` (cor estável via `colorFor`, persiste em `sel=pool:`); no `MetricLine` a
linha do pool sai **tracejada** (`strokeDasharray`) para distinguir de agentes. i18n `bench.list.pinPoolAvg`.

**Validado**: 13 testes (Compare+Cross); curl `entities=pool:sac_ia` → série de média escopada +
`summary` 57 sess / res 0.14 / esc 0.35 (consistente com o endpoint cross).

---

## Bancada de agentes F6 — Cruzamentos das vantagens (§8) (2026-06-09)

Fecha a bancada (Arc workbench): a view **Cross-cut** põe as 3 vantagens (resolução, qualidade, NPS)
lado a lado por agente e destaca **onde elas discordam** — o payoff de gestão do §8.

**Endpoint** `GET /reports/agents/cross` (`query_agents_cross`/`_fetch_agents_cross` em
`reports_query.py`; rota em `reports.py`): `seg_agg` (sessions, resolved, escalated, NPS n/sum,
promotores/detratores por `agent_key` — segments primary não-sintético) **`LEFT JOIN`** `eval_agg`
(n_evals, avg_score via `_session_agent_attribution_sql` filtrado por `attr.session_started_at` no
período) on `agent_key`. Retorna por agente: `sessions`, `resolution_rate`, `escalation_rate`,
`quality_score`(0–1)/`quality_n`, `nps`(−100..100)/`avg_nps`/`nps_n`. Agente sem avaliação/NPS →
campos null (não zero). Testes `TestQueryAgentsCross` em `test_reports.py`.

**platform-ui** (`AgentsBenchPage`): toggle **Lentes ↔ Cross-cut** (persistido na URL `view=cross`).
View Cross-cut = tabela de concordância (linha por agente, ordenada por sessões; "sem aval."/"sem NPS"
quando null) + **3 flags de divergência** (decisão F6.1: só flag, sem score combinado): ★ destaque
(entrega+qualidade+NPS altos), ⚠ lacuna de percepção (entrega alta, NPS baixo), ◑ divergência de
disposição (marca resolvido, avaliação baixa). Quadrante resolução(X)×qualidade(Y), bolha=sessões,
cor=NPS, linhas-guia 70/70 (plota só quem tem qualidade avaliada). Linha clicável reusa o detalhe
type-aware (F4.4). **Export CSV sensível à view**: Cross-cut → `bancada_cruzamento_*.csv` (uma linha
por agente + coluna `signals`); Lentes → série da lente como antes. i18n en+pt-BR (`bench.view.*`,
`bench.cross.*`).

**Validado E2E** (tenant_demo, 7 agentes): humano `res 0.64 · qual 0 (n=6) · NPS 100 (n=2)`;
`skill_atendimento_sac_v1` `res 0.14 · esc 0.35` (candidato a coaching); 2 estrelas (`skill_triagem_v2`
res 0.72, `skill_atendimento_auth_v1` res 1.0 — sem qualidade/NPS que contradiga). Quadrante mostra
humano + sac_ia no piso (qual 0).

→ **Bancada completa (F1–F6).** Calibração do avaliador (Arc 13, IA×NPS) fica para o Calibration
Dashboard; refinamentos abertos: pool-average agregado, `session_signal` (grãos contato/jornada),
F7 (motivo de escalação normalizado).

---

## Bancada de agentes F5 — NPS + wrap-up por segmento (grão segmento) (2026-06-09)

Liga as lentes **NPS** e **Wrap-up disposition** da bancada, atribuindo os sinais ao **segmento
humano correto** (o do pool cujo `on_human_end` os disparou) — suportando N humanos/pools por
contato (handoff sequencial). Decisão de grão (§7 do spec): grão **segmento** mora em `segments`;
grãos **contato/jornada** ficam para a `session_signal` futura.

**Refator per-segmento (corrige simplificações de demo da F1.3/F1.4):**
- `analytics.segments` ganha `nps_score Nullable(Int32)` (DDL + migração idempotente; parser +
  `_SEGMENT_COLS`/`_segment_row`). `_publish_participant_event` aceita `nps_score`.
- **bridge**: no `participant_left`, grava `session:{id}:human_seg:{pool}` (registro do segmento) e
  semeia o acumulador `seg_signal:{segment_id}` com o outcome placeholder. `fire_pool_hooks` (recebe
  o pool do humano) lê o registro, deriva `close_reason` da iniciativa e **carimba o `segment_id` no
  `hook_conf`** (5º campo). Na conclusão de cada hook (`process_routed`), a disposição/NPS vêm do
  **`agent_result.pipeline_state.results`** do próprio agente (`wrapup_classificacao`/`wrapup_resumo`;
  `nps_resposta`) — não do ContextStore — e são **acumulados** no `seg_signal` e re-publicados no
  segmento (acumulador evita que wrap-up e NPS se anulem no `ReplacingMergeTree`).
- Removidos `_republish_human_primary_segment`/`_finalize_human_outcome_from_wrapup`/
  `primary_human_segment` (F1.4, single-segment). Tags `session.wrapup.*` voltaram a `scope: segment`.
  Doc: `conference-mechanics.md` § Mudança 7.

**compare**: lentes `nps` (segments.nps_score → avg + índice NPS = %promotores−%detratores por
agente/tempo, N visível) e `wrapup` (distribuição de `outcome`/`issue_status` por agente, em
`summary.dispositions`) saem de `_COMPARE_LENSES_PENDING`. Testes em `test_reports.py`.

**platform-ui**: 7 lentes (add NPS = índice −100..100 + nota média 0–10; Wrap-up disposition =
barras empilhadas por disposição com cores semânticas). i18n en+pt-BR.

**Validado E2E** (tenant_demo, single-humano): wrap-up "Escalado" + NPS 9 no MESMO segmento humano
(`escalated·escalado·nps=9`); lente NPS índice 100 / nota 9.0; wrap-up resolvido 11 · escalado 2.
**Multi-humano: correto por construção, sem E2E** (demo tem só um pool humano).

→ **Bancada F1–F5 completas.** Resta F6 (cruzamentos §8) + refinamentos (pool-average agregado,
session_signal contato/jornada, F7 motivo de escalação).

---

## Bancada de agentes F4 — UI da bancada de comparação 360° (2026-06-09)

Reescreve a aba Analytics/Agents como **bancada de comparação** (lista pools→agentes +
seletor de lente + gráfico com a "média dos agentes" + detalhe type-aware). Novo
`AgentsBenchPage.tsx` na rota `/analise/agents`; o `AnaliseAgentesPage` antigo fica acessível em
`/analise/agents-legacy` (fallback temporário). Fontes: `/reports/agents/performance` (lista) +
`/reports/agents/compare` (séries por lente, F3).

- **F4.1** — shell: filtro período + seletor de 5 lentes (com caption de domínio; IA desabilitada
  nas lentes humanas) + lista + wiring dos dois fetches.
- **F4.2** — gráfico por lente: resolution/escalation = dois mini-gráficos %; sessions_aht = contagem
  × tempo (unidades distintas); availability = barras agrupadas (ocupação/pausa); pause_reason =
  barra empilhada por motivo; quality = curva da nota. Legenda por `label`, média = linha preta
  tracejada, cor estável por entidade (hash determinístico do agent_key), gap (null) = quebra.
- **F4.3** — lista interativa: árvore pools→agentes (chevron), checkbox do pool faz **bulk** dos
  agentes elegíveis (estado indeterminado p/ parcial), checkbox por agente, clique no nome abre
  detalhe. Domínio respeitado no agente e no bulk.
- **F4.4** — detalhe type-aware (pop-up): consolidado das lentes por agente (busca o compare
  individual, `include_average=false`) — humano: sessões/resolução/escalação/TMA/qualidade +
  ocupação + donut disponível×pausa; IA: tiles aplicáveis sem ocupação/donut.
- **F4.5** — polish: combo de pool (populado pelos pools do período; lista sempre busca todos e o
  filtro é client-side p/ a árvore + server-side p/ o compare); persistência de lente/pool/período/
  seleção na **URL** (`?from&to&pool&lens&sel`, replace — link compartilhável); **export CSV** do
  conjunto comparado (formato longo entity,date,métricas). i18n en+pt-BR em todas as etapas.

**Lentes pendentes na UI** (já tratadas como 400 no backend): nps/wrap-up (F5 — `session_signal`) e
quality_criteria (critérios por item não chegam ao CH). **Refinamentos anotados**: "média do pool"
como série agregada única exigiria pseudo-entidade `pool:` no endpoint compare (hoje o checkbox do
pool faz bulk dos agentes); pause-reason multilíngue seria taxonomia i18n por tenant (rótulos hoje
são dado do tenant). **Validado com dado real** (tenant_demo): série do humano reflete o efeito F1
(resolução 0→0.8), n=8 agentes em escopo, quality n=5, donut e barras coerentes.

→ Próxima fase: **F5 — camada `session_signal`** (NPS obrigatório + wrap-up), que habilita as lentes
nps/wrap-up e os cruzamentos do §8.

---

## Bancada de agentes F3 — endpoint `/reports/agents/compare` (2026-06-07)

Backend da bancada de comparação (`analytics-agents-workbench.md` §11): uma chamada devolve as
séries diárias de todas as entidades pedidas + a referência **"média dos agentes"**.

**analytics-api**: `query_agents_compare` + rota `GET /reports/agents/compare`
(`?lens&pool_id&entities=k1,k2&include_average`). Semânticas do §10: média = **aritmética dos
agentes por bucket** (N visível por ponto; agente sem dado no dia = **gap**, fora do denominador —
nunca zero); `entities` vazio = só a média do escopo; média sempre computada sobre TODOS os agentes
do escopo (é a referência), independente da seleção. **Lentes v1**: `resolution`
(resolution+escalation de segments, com folding `{escalated, escalated_human, escalated_ai,
transferred}→escalated` na leitura), `sessions_aht`, `availability`/`pause_reason` (domínio humano —
intervals Arc 8 + busy de segments; denominadores fixos §5: pause%=paused/logged,
occupancy%=busy/(logged−paused)), `quality` (join de atribuição F2, **bucketizada por
`session_started_at`** — regra de ouro §7: a nota cai na data da SESSÃO avaliada; N amostral em
cada ponto e no summary). **Pendentes** (400 + `pending_lenses`): `nps`/`wrapup` (F5 —
session_signal) e `quality_criteria` (critérios por item não chegam ao ClickHouse). Filtros
sintéticos (`agent_type != 'system'`, `role='primary'`); ABAC via
`accessible_pools`/`supervised_agent_types`; atribuição F2 estendida com `session_started_at`
(aditivo). 7 testes novos (média aritmética com gap, folding, lente pendente, quality por
session_at, entidade ausente).

**Validado com dado real** (tenant_demo): n=8 agentes em escopo (humano + flows IA); a série do
humano mostra o efeito F1 (rates 0.0 nos dias pré-F1 com placeholder NULL → 0.5/0.8/0.76 pós-F1,
escalation 0.0588 do teste "Escalado"); quality n=5; availability com gaps honestos
(occupancy=null onde logged=0).

---

## Bancada de agentes F2 — qualidade atribuída ao agente avaliado + religação do pipeline de avaliação (2026-06-07)

A nota de qualidade (Arc 6) passa a ser atribuível ao **agente avaliado** (`agent_key`+`pool_id`),
e o pipeline de avaliação Arc 3/6 — que estava **inteiramente dormente** (`evaluation_results`
vazio desde sempre) — foi religado elo a elo durante a validação.

**analytics-api (a F2 propriamente)**: helper `_session_agent_attribution_sql` (atribuição por
sessão = último segmento `primary` não-sintético, `argMax` por `sequence_index` — mesmo padrão do
agent_performance); `/reports/evaluations` devolve cada avaliação com
`agent_key`/`agent_type`/`pool_id`/`user_login` (LEFT JOIN em query-time — retroativo, sem mudança
de ingest); `/reports/evaluations/summary` ganha `group_by=agent_key|pool_id`. Pegadinha CH
corrigida: alias de SELECT é visível no WHERE (ILLEGAL_AGGREGATION) → filtro em subquery interno.
Testes novos em `test_reports.py`.

**Pipeline religado — 7 elos (todos descobertos porque a validação exigiu dado real):**
1. **bridge** publica `conversations.session_closed` no `_close_contact_layer` — o tópico NUNCA teve
   produtor (a doc atribuía ao "Core", que não existe como serviço); sem ele o Persister jamais roda.
2. **session-replayer**: `ensure_schema()` self-healing antes de cada persist (reset de banco com o
   serviço de pé deixava `session_stream_events` inexistente até o próximo restart).
3. **compose**: `EVALUATOR_POOL` corrigido `avaliador_qualidade`→`avaliacao_ia` (pool inexistente).
4. **routing-engine** `EvaluationConsumer`: filtrava `payload.event`, mas o produtor publica
   `event_type` → 100% das mensagens descartadas em silêncio. Aceita ambos.
5. **compose**: `PLUGHUB_SKILL_FLOW_SERVICE_URL` faltava (default `localhost:3400` quebrava o load
   do flow e o `POST /execute` — mesmo padrão do bug do CONFIG_API_URL de 2026-06-05).
6. **compose**: mount ro de `packages/skill-flow-engine/skills` no routing-engine (o load primário
   do flow é de disco e a imagem só copia o próprio pacote).
7. **analytics parser** aceita `evaluation.completed` publicado DIRETO pelo avaliador
   (`result_id:=evaluation_id`, `overall_score:=composite_score/10`) — o caminho desenhado no Arc 13
   (`eval.instance.submitted` → ingest na evaluation-api → re-publish com `result_id`) tem o
   consumer **inexistente** na evaluation-api.

**agente_avaliacao_v1 (opção A test-grade)**: o avaliador rodava **sem identidade** (o YAML esperava
`session_token`/`participant_id` "injetados pelo orchestrator" — mecanismo nunca implementado).
Ganhou step inicial `agent_login` (contrato Spec 4.5): token próprio; `participant_id`=`evaluation_id`
(UUID); `agent_type_id`=`skill_avaliacao_v1` (pós-C2 a identidade é o skill deployado).

**Validado E2E** (tenant_demo): 5 avaliações no ClickHouse, todas com `agent_key`/`agent_type=human`/
`pool_id=retencao_humano`/`user_login` corretos; summary agrupado por agente.

**Limitações conhecidas (test-grade — pertencem ao arco da visão final registrada no TODO:
avaliador disparado pelo calendário da campanha, recebendo session_id):** o ReplayContext não
popula `session_meta` (outcome/close_reason/duration/participants vazios → LLM devolve score 0 com
compliance_flags justas) e não associa campanha/form (`campaign=none` — o Replayer não consulta
campanhas; `form_id` vazio nos resultados). O gatilho atual é incondicional por sessão fechada
(sem amostragem da campanha).

**Incidente operacional durante a validação**: recriações de containers (postgres −3h,
agent-registry −54min, hot path −1h) deixaram o **agent-registry vazio** (recriado DEPOIS do
RegistrySyncer do boot do bridge) e o operador deslogado → webchat sem roteamento. Recuperação:
`restart orchestrator-bridge` (re-sync do registry) + re-login no Console. Lição: o RegistrySyncer
só roda no boot do bridge — se o registry for recriado depois, restart no bridge restaura.

---

## Bancada de agentes F1 — outcome real do segmento primário humano (2026-06-07)

Espinha da reformulação Analytics/Agents (`docs/arcos/analytics-agents-workbench.md` §13): o segmento
`human/primary` deixa de carregar outcome placeholder (Console hardcodava `resolved`/`abandoned`) e
passa a receber a **disposição real do wrap-up**, normalizada — resolution/escalation por agente
humano passa a ter sentido.

1. **schemas**: `CompleteStepSchema.outcome` ampliado p/ subset consistente do cânone
   `SegmentOutcomeSchema` (+`escalated`/`suspended`/`abandoned`; `transferred_agent` mantido — é
   load-bearing no SDK/adapter; folding na leitura/F3); `outcome_from` p/ outcome dinâmico do
   `pipeline_state` (F1.2); `scope` no `context_tags` do notify — era declarado nos YAMLs e
   **descartado pelo Zod** (schema inline sem o campo).
2. **skill-flow-engine**: `complete` resolve `outcome_from` (síncrono, valida contra o cânone,
   fallback no literal). **Causa-raiz da validação**: `executeNotify` **nunca implementou
   `context_tags`** — wrap-up E NPS gravavam no vácuo (nem `session.wrapup.*` nem `session.nps_score`
   chegavam ao ContextStore). Extração implementada (outputObj = `pipeline_state.results`,
   fire-and-forget, espelha o invoke) — destrava também o NPS p/ a F5. Testes: `complete.test.ts` (8),
   `notify.test.ts` (4).
3. **agente_wrapup_v1.yaml**: `classificacao`/`resumo` com `scope: session` (bridge lê sem conhecer o
   segment do wrap-up); **ids crus mantidos** (taxonomia do pool → `issue_status`).
4. **orchestrator-bridge (B1′)**: registro `session:{id}:primary_human_segment` no primeiro
   `participant_left`; `_finalize_human_outcome_from_wrapup` com **2 gatilhos idempotentes** (NX) —
   conclusão do hook `on_human_end` side=agent (caminho normal; `_close_contact_layer` dispara ANTES
   do wrap-up terminar) e `_close_contact_layer` (wrap-up termina antes → corrige também o outcome de
   sessão via `last_outcome`). Normaliza cru→outcome (`resolvido→resolved, pendente→suspended,
   escalado→escalated, cancelado→abandoned` — decisão `pending≡suspended`/`transfer≡escalate`),
   re-publica o segmento (mesmo `segment_id`, ReplacingMergeTree dedupica) com `issue_status`=cru,
   `handoff_reason`=resumo (quando ≠resolved) e `close_reason` da **iniciativa** via
   `session.close_origin` (pre-hook) — o marcador `:closed` é sobrescrito pelo teardown do WS do
   cliente pós-NPS e corrompia a iniciativa.
5. **Validado E2E** (tenant_demo): `escalated·escalado·<resumo>` e `resolved·resolvido·agent_hangup·NULL`.
   Limitações documentadas: pool sem wrap-up / timeout / pulado → placeholder permanece (nunca inventa
   `resolved`); fechamento antes do wrap-up → outcome de SESSÃO mantém placeholder (o **segmento** é a
   fonte da verdade). Check de segurança: nenhum consumidor lê `segments.outcome='suspended'` como
   "vai resumir" (semântica de resume keia em session status / agent_result).

Docs: `conference-mechanics.md` § Mudança 5 · spec §13 (F1 ✅). Próxima fase: F2 (join
`evaluation_results → segments`).

---

## Limpeza de ajustes menores (2026-06-05)

Três itens registrados nas validações da semana:

1. **i18n do PoolCombo (Console)**: cabeçalho do dropdown exibia o literal `POOLS ({{POOLS}})` — a chave `header.comboPools` interpola `{{pools}}` mas o call site passava `{ count }`. Fix em `Header.tsx`: passa `pools: "ativo/total"` (mesmo formato do botão do combo).
2. **`PLUGHUB_CONFIG_API_URL` do routing no compose**: faltava — o `RoutingConfigCache` tentava `localhost:3600` e operava só com defaults. **E o fix expôs um bug mais antigo**: o `GET /config/{namespace}` exige `?tenant_id=` (422 sem ele) — o reload do cache **nunca funcionou** (sempre mascarado pelo erro de conexão). Fix: `routing_config` ganha `configure_tenant` (settings `tenant_id`, default `__global__` = defaults da instalação; demo usa `tenant_demo` via env) e envia o query param; o mesmo bug existia no `_maxQueueTotal` do agent-registry (item 7a — caía silenciosamente no default 100) → corrigido com tenant_id + cache por tenant + parse do shape flat de `entries`.
   **Descobertas em cascata na validação**: (a) a tabela `platform_config` havia sumido e o health check do config-api a recriou **vazia** (500 transiente; defaults hard-coded seguraram tudo — a disciplina de nunca depender só do Config API pagou); (b) re-seed revelou que a **imagem do `config-seed` estava stale** — o serviço builda imagem própria do mesmo Dockerfile do config-api, e os rebuilds de config-api não a atualizam. **Lição operacional: rebuild de config-api deve sempre incluir config-seed** (`up -d --build config-api config-seed`). Após rebuild+re-seed: namespace routing completo (13 chaves) e ciclo Config API → routing saudável pela primeira vez.
3. **Linha "—" no relatório de fila**: sessões com pool vazio E sem segmento de fila (nunca roteadas nem enfileiradas — webchat que conecta e não engaja, artefatos de teste) não têm comportamento de fila a reportar → filtradas do `/reports/pools/queue` (`WHERE pool_id != ''` sobre o per-session, justificado em comentário). O volume delas permanece visível no Volume report.

---

## Capacity-governance item 7b — Analytics espelha o Monitor (ARCO CONCLUÍDO) (2026-06-05)

Fecha o item 7 e o arco: a organização reservado × compartilhado × fila gratuita que o Monitor mostra ao vivo (7a) ganha histórico no Analytics — donut = foto, área empilhada = filme.

**routing-engine** (occupancy sampler): cada tick (5s) amostra também a admissão **nas mesmas chaves que o Monitor lê** — reservas usadas por pool (`SCARD reserved:*`), shared por pool (HASH `shared_pools` do 7a), buffer (`SCARD unadmitted`) — e rastreia picos por minuto. O flush emite: `admitted_peak` em toda linha por pool (sessões debitando C atribuídas ao pool) e três linhas agregadas no padrão do `__total__`: `__reserved__` (Σ usadas vs Σ reservas configuradas), `__shared__` (usado vs limite = C − Σ reservas), `__buffer__` (fila gratuita vs `queue_max_total`).

**analytics-api**: coluna `admitted_peak` no `pool_occupancy_peaks` (ALTER ... IF NOT EXISTS idempotente no boot) + parser/row builder; `/reports/pools/occupancy` exclui as linhas agregadas das séries por pool e devolve o bloco **`admission`** (`reserved_series`/`shared_series`/`buffer_series`, used vs limit por bucket; só p/ callers sem escopo).

**platform-ui** (aba Capacidade): dois gráficos novos — **"Admissão no tempo"** (áreas empilhadas Reservado+Compartilhado vs linha vermelha do C contratado, `extendDomain`) e **"Sala de espera gratuita no tempo"** (área de espera muda — isenta de C — vs teto do buffer). i18n en + pt-BR.

**ARCO CAPACITY-GOVERNANCE CONCLUÍDO**: contratado como fonte única governando configuração (3a/3b), criação (gates item 2), runtime (quota item 1), visibilidade comercial (Billing item 4), demo (item 6) e operação tempo-real + histórica (item 7), com a fila de sistema (arco system-queue) integrada ao modelo.

---

## Capacity-governance item 7a — Monitor com físico × admissível, regimes e donuts (2026-06-05)

Fecha a parte tempo-real do item 7: o Monitor passa a contar a verdade da admissão (reserved × shared × fila gratuita), não só a física.

**routing-engine** (`admission.py`): novo HASH `{t}:admission:shared_pools` {sid→pool} — **atribuição exata** do consumo do shared por pool (o SET continua sendo O limite; HSET/HDEL nos mesmos pontos do member key: admit/idempotente/migração/release/reconciler) + higiene no reconciler (entradas órfãs ⇒ HDEL; Σ fatias == SCARD por construção).

**agent-registry** (`operational.ts`): `GET /v1/operational/pools` enriquecido — por pool: `admission_scope` (reserved/shared), `reservation`, `admitted` (debitando C), `active_sessions`, `queue_mute`/`queue_attended` (split via `unadmitted`), `queue_tier`, **`admissible`** (fatia restante, ou shared restante, e pools IA limitados também por C_ai; `admissible_shared` marca o ⊕) — tudo read-only do Redis, sem tocar o hot path. Novo bloco `summary`: C/admitted/headroom, em atendimento, fila at/muda, `shared.by_pool` (fatias exatas do HASH), reservas usadas, buffer usado/teto (teto via Config API `queue_max_total`, cache 60s, default 100). Env `CONFIG_API_URL` no compose.

**platform-ui**: **Monitor/Pools** reorganizado — tiles do pipeline (Contratado usado/C + folga · Em atendimento · Em fila at/grátis · Sala de espera gratuita usado/teto); **donuts** "total e como está sendo consumido" (Compartilhado com fatias por pool + disponível; mini-donuts por pool reservado; Sala de espera gratuita); tabela em **seções por regime** (Reservados = fatia própria / Compartilhado = sem teto por pool) com colunas novas: Atend., Fila (at/grátis), **Disp (fís/adm⊕)** — dois números, vermelho quando admissível 0 (agente livre + contrato cheio fica visível). **Monitor/Sessions** ganha os tiles Contratado e Sala de espera (mesmo summary). i18n en + pt-BR (`pools.admission.*`).

---

## Fila de sistema — Fase B: UI (arco concluído) (2026-06-05)

Fase B enxuta (a decisão dos segmentos sintéticos eliminou qualquer mudança no analytics): **causa `queue_full`** na demanda reprimida ("Fila de espera cheia" / "Waiting queue full") e **tier da fila por pool** na aba Fila do Analytics→Pools — badge Atendida (IA) / Sistema (grátis) / "—", derivado da config do registry no client (`queue_config` ⇒ atendida; pool humano sem ⇒ sistema; pool IA ⇒ sem fila). i18n en + pt-BR. **Arco system-queue concluído** (spec → implementado); item 7 do capacity-governance destravado.

---

## Fila de sistema (tier gratuito) — Fase A: routing (2026-06-05)

Implementa o tier gratuito de fila ([`system-queue.md`](docs/arcos/system-queue.md)): espera muda não consome capacidade contratada; rejeição na porta só quando a FILA lota, não quando os atendentes lotam.

**Isenção de C** (`_persist_queued_contact` + novo `mute_queue.py`): enqueue em fila MUDA (pool sem `queue_config`, ou overflow) libera os slots de admissão (`AdmissionController.release` — buckets + kind + member keys) e marca em `{t}:queue:unadmitted` (SCARD = ocupação do buffer grátis). `first_queued` NX vira o score do ZSET: re-enfileiramentos (re-admissão negada no drain) não resetam posição nem relógio de espera. Fila ATENDIDA segue debitando C (é IA licenciada).

**Overflow** (`_try_overflow_enqueue`): admissão rejeitada (shared_full/quota/reservation_full) em pool `agent_kind=human` → contato cai na fila muda gratuita (sempre muda; tratamento atendido só após re-admissão pelo drain) enquanto o buffer tiver vaga; buffer cheio → outage causa NOVA **`queue_full`** com `msg_queue_full`; canal sem fila muda ou pool IA → outage com a causa original.

**Proteções operacionais** (Config API namespace `routing` + defaults hard-coded no `routing_config` — nunca ilimitado com Config fora): `queue_max_total` (100), `queue_max_wait_by_channel` (voice/webrtc 300s, webchat 1800s, whatsapp 4h; **0 = canal não aceita fila muda** → close gracioso imediato, nunca dead air). Sweep de timeout do drain periódico agora é **channel-aware** para filas mudas (atendida mantém `queue_config.max_wait_s`). Drain com orçamento confirmado estrutural no recon (1 contato/pool/ciclo + checagem de capacidade) — sem mudança.

**Ledger analítico SEM tópicos novos** (decisão 3 do spec superada na implementação): toda saída de fila muda emite **segmento sintético `role=queue`** (`mute_queue.resolve_mute_exit`) — `handoff` na transição unadmitted→admitida (hook no consume loop, no-op barato para quem nunca enfileirou), `abandoned` na desistência detectada pelos drains; o caminho de max_wait mantém o segmento que já emitia (só limpa estado). O `/reports/pools/queue` (Fase D, segments) passa a contar fila muda com **zero mudança no analytics** — a Fase B encolhe para i18n da causa `queue_full` + tier da fila por pool.

Reconciler da admissão ganha backstop do `unadmitted` (sessões fechadas; TTL 7d nas chaves).

**Fix da validação (handoff lento pós-fechamento)**: o slot de admissão era liberado só pelo reconciler (~60s — aceitável quando admissão era gauge), mas o drain da fila muda depende do headroom → cliente esperava até 60s por vaga já livre. Agora o `SessionClosedEventHandler` (contact_closed) faz **release imediato** da admissão (event-driven); reconciler vira backstop. Latência do handoff pós-fechamento: ≤ ciclo do drain periódico (5s).

**Fixes da validação (churn de drain + spam de aviso)**: com agente pronto e contrato cheio, os drains re-publicavam a sessão não-admitida a cada ciclo (5s) → admissão rejeitava → overflow re-enfileirava → novo aviso "Aguardando..." ao cliente (loop). (1) Novo `AdmissionController.has_headroom` (read-only, espelha o admit, fail-open): ambos os drains (periódico e agent-ready) só re-publicam sessão `unadmitted` com vaga no contrato — sem vaga, segue esperando em silêncio; (2) aviso de espera deduplicado pela chave `first_queued` — re-enfileiramento nunca re-avisa. Validado: overflow funcionando (`overflow → mute queue`, isenção + espera real preservada), fila muda com handoff de 17min medido no segmento sintético.

---

## Capacity-governance item 2 / Etapa 2 — gates por tipo armados (2026-06-05)

Fecha o item 2 do arco: C_ai e C_human deixam de ser display e passam a **negar criação de recurso**.

**Gate humano** (`mcp-server/server.ts::registerHumanAgent`): dois checks antes do registro — (a) **kind do pool**: login humano só em `agent_kind: human` (lê `{t}:pool_config:{pool}` cacheado pelo routing; ausente → fail-open); (b) **logins concorrentes** ≤ C_human (`{t}:quota:capacity:human_agent` vs contagem de `{t}:instance:human-*`; re-login do mesmo usuário nunca bloqueia — instância existente é merge). Recusa lança `HumanLoginDenied` → o WS handler envia `{type:"login_denied", reason, limit, current}` e fecha a conexão; **Console** (`AgentAssistContext`) exibe toast persistente de erro com a causa. Pool auto-criado no login agora declara `agent_kind: "human"`. Qualquer falha de Redis → fail-open (gate nunca derruba login por infra).

**Gate IA** (`routing-engine/admission.py`): sessões entrando em pool `agent_kind: ai` respeitam C_ai (`{t}:quota:capacity:ai_agent`) — novo SET `{t}:admission:kind:ai` + `{t}:admission:kind_member:{sid}` com a mesma mecânica idempotente dos buckets da Fase B: SADD/SCARD com rollback, re-publish idempotente, migração ai↔human atualiza o tracking, **mid-session fail-open** mantém a atribuição de origem (nunca derruba sessão ativa), reconciler libera membros fechados (sets de kind incluídos no scan). Rejeição na porta → outage **cause `quota`**, que a demanda reprimida já renderiza como "Teto contratado" (zero front novo).

**Recurso × kind** (pool misto proibido): deploy de skill em pool `human` → 422 no `PUT slots/next` (pool-slots.ts); login humano em pool `ai` → `login_denied`.

**Ajuste pós-validação (loop de toasts)**: o auto-reconnect do WS re-tentava o login negado a cada 3s, empilhando um toast persistente por tentativa. Fix em duas camadas: `useMultiPoolWebSocket` marca `loginDenied` na conexão ao receber o evento e **suprime o reconnect** daquele pool (status "disconnected"; desmarcar/marcar o pool re-tenta quando vagar assento); `AgentAssistContext` deduplica o toast por pool (substitui em vez de empilhar). Validado: 2º usuário com C_human=1 → um único toast, sem loop.

**Ajuste pós-validação (header mentia "Connected/Ready")**: o socket abre antes do gate negar (deny+close vem ms depois), e o header tratava socket-aberto como logado — exibia "Connected", "Ready in 1 pool" e Pause habilitado durante o ciclo de retry. Fix (`Header.tsx`): o subtítulo "Ready in N pool" conta **conexões aceitas** (`status === "connected"` por pool ativo), não pools selecionados — login negado mostra "Offline"; botão Pause desabilitado (`disabled` + opacity) quando nenhum pool conectado. Resta um flash de "Connected" de <1s entre o open e o deny — cosmético, aceito.

---

## Capacity-governance item 2 / Etapa 1 — agent_kind no pool + quotas por tipo (2026-06-05)

Fundação dos gates por tipo. Decisões fechadas com o usuário (registradas no spec § Tipagem de pool): canal nunca é tipado — o **pool declara** `agent_kind: human|ai`; `queue_config ⇒ human` (fila atendida só para recurso escasso/lento — para IA, o slot da fila instanciaria o próprio agente); fila atendida é `ai` **cobrável** (o tier gratuito é a fila de sistema — arco futuro registrado no TODO); pool misto proibido (validação de registro na Etapa 2).

**@plughub/schemas**: `PoolRegistrationSchema.agent_kind` (`enum human|ai`, opcional). **agent-registry**: coluna `Pool.agent_kind` (nullable) + **backfill por inferência no boot** (deploy slot `current` ⇒ `ai`; senão `human` — roda uma vez por pool, declaração explícita daí em diante); POST/PUT persistem o campo; validação `queue_config ⇒ human` por **estado resultante** (422). **routing-engine**: `PoolConfig.agent_kind` populado via `pool.registered/updated` (base do gate de admissão da Etapa 2). **pricing-api** (`quota_sync.py`): além do total, grava `{t}:quota:capacity:ai_agent` e `{t}:quota:capacity:human_agent` (C_ai/C_human, mesmo recompute idempotente, DEL quando 0). **tenant_demo.yaml**: `agent_kind` explícito nos 17 pools (16 `ai` + `retencao_humano` `human`).

Etapa 2 (pendente): gate de login humano concorrente (`registerHumanAgent` ≤ C_human, erro claro no Console), gate de sessões IA na admissão (≤ C_ai, causa `quota`), validação tipo-do-recurso × kind-do-pool no registro.

---

## Capacity-governance itens 6 + 5 — demo coerente + aba Capacidade contratado-cêntrica (2026-06-04)

**Item 6 — `pricing-seed`** (`infra/seed/seed_pricing.py` + serviço no `docker-compose.demo.yml`): recursos contratados do demo coerentes com os deploys do `tenant_demo.yaml` — `ai_agent×300` (Σ declarada do YAML = 280 + margem p/ pools de teste) + `human_agent×10` → **C=310**; quota de admissão gravada pelo quota sync na subida. **Não-destrutivo**: se o tenant já tem qualquer resource, o seed pula (experimentos do operador — ex. testes de gate com C baixo — sobrevivem a re-`up`; para re-semear, delete os resources). Elimina o estado "295 provisionados vs 25 contratados" do demo fresco.

**Item 5 — aba Capacidade (Analytics→Pools) contratado-cêntrica**: o teto único (gráfico/headroom/utilização = C) já valia desde o fechamento da Fase 2; agora o **Alocado (provisionada)** entra na tira de KPIs como diagnóstico — vermelho + "acima do contrato" quando > C — e os hints foram reescritos para fixar a semântica: valores por pool = alocação física (instâncias × max); teto do tenant = capacidade contratada. i18n en + pt-BR.

---

## Capacity-governance item 3b — Σ declarada nos deploys ≤ C (2026-06-04)

Fecha a validação de configuração do arco: depois das reservas (3a), a **declaração de deploy** também passa a respeitar o contratado.

**agent-registry**: novo `lib/capacity.ts` (helpers compartilhados — `contractedCapacity` movida do pools.ts, `slotDeclared`, `declaredTotalOthers`, `deployViolation`); `routes/pools.ts` refatorado para o helper. `routes/pool-slots.ts` valida `Σ declarada ≤ C` em **dois pontos**: `PUT /v1/pools/:id/slots/next` (feedback na declaração) e `POST /promote` (quando vira efetiva — revalida contra o C vigente). Declarada por pool = `config_json.max_concurrent_sessions` do slot `current` (default 1; é o N de instâncias que o bootstrap provisiona). Regras idênticas ao 3a: sem C / Redis fora → fail-open; **reduções/iguais sempre passam** (re-sync idempotente do RegistrySyncer não quebra; demo legado com Σ≈245 > C=25 continua bootando — só não pode *aumentar*); aumentos que estourem C → **422** (`contracted`, `declared_others`, `requested`, `balance_would_be`). **Rollback é isento** — operação de emergência nunca bloqueia. Comparação contra o C total do tenant; por `resource_type` entra com os gates do item 2.

---

## Capacity-governance item 4 — Billing/Capacidade: contratado × alocado × saldo (2026-06-04)

Torna o modelo visível ao operador — é o que dá sentido ao "redução sempre aceita + alerta" (não-conformidade nunca é silenciosa).

**platform-ui** (`BillingPage`): terceira aba **Capacidade** (`/config/billing`) — KPIs: **Contratado (C)** do `GET /v1/pricing/capacity` (agentes IA+humano, base + reservas ativas), **Alocado** = provisionada corrente (último bucket de `total_series` do `/reports/pools/occupancy`; fallback agregado), **Saldo (C − alocado)** verde/vermelho ("contratado e ainda não utilizado" — o papel do provisionado no modelo), **Reservado (Σ pools)** e **Shared (C − reservado)** do `GET /v1/pools/capacity/conformance` (item 3a). Alertas: 🔴 `conform=false` (reservas excedem C — shared negativo), 🟠 alocado > C (deploy acima do contrato: admissão corta em C, excedente é custo ocioso), ℹ️ sem contrato configurado (sem teto). Tabelas: capacidade por `resource_type` (base/reserva ativa/total) e pools com reserva. i18n en + pt-BR (`billing.capacity.*`). Fontes via proxies já existentes (`/v1/pricing` → 3900, `/v1` → registry 3300 com `x-tenant-id`, `/reports` → analytics 3500).

---

## Capacity-governance item 3a — Σ reservas ≤ C validado na config de pool (2026-06-04)

Fecha o furo original do arco: a config aceitava `Σ session_reservation > C`, deixando o shared da admissão híbrida negativo (um pool podia "reservar" capacidade que o contrato não tem).

**agent-registry** (`routes/pools.ts`): POST/PUT de pool validam `Σ session_reservation ≤ C` — C lido de `{t}:quota:max_concurrent_sessions` (produzida pelo quota sync do item 1, via Redis já existente no registry). Regras: sem C / Redis fora → **fail-open** (sem pricing configurado não há o que validar; runtime segue protegido pela admissão); **reduções e re-PUTs com valor igual sempre passam** (heal gradual de estado legado não-conforme — o RegistrySyncer não quebra no boot); só **aumentos** que estourem C retornam **422** com detalhe (`contracted`, `reserved_others`, `requested`, `shared_would_be`). Novo `GET /v1/pools/capacity/conformance`: conformidade **derivada** (não persistida — C relido a cada chamada, mudança de contrato revalida implicitamente) com `contracted/reserved_total/shared/conform/pools`; o alerta visual na UI fica com o item 4 do arco.

Escopo: 3b (Σ declarada nos deploys ≤ C) permanece pendente no spec.

---

## Capacity-governance item 1 — quota sync: pricing arma o gate de admissão (2026-06-04)

Primeiro item do arco [`capacity-governance.md`](docs/arcos/capacity-governance.md). O gate já existia dos dois lados — `AdmissionController._shared_limit` (routing, admissão híbrida: `shared = C − Σ session_reservation`) e `checkConcurrentSessions` (mcp-server) leem `{t}:quota:max_concurrent_sessions` — mas **ninguém gravava a chave** (a "integração" era só documentação).

**pricing-api**: novo `quota_sync.py` — `sync_tenant` recalcula C (capacidade contratada de agentes: `ai_agent` + `human_agent`, base + reservas comerciais **ativas**, agregado de todas as instalações via `get_capacity(installation_id=None)`) e grava a chave (`DEL` quando C=0 → sem resources, sem limite — comportamento anterior preservado); `sync_all` no startup re-deriva todos os tenants com resources (auto-cura pós Redis flush). Hooks nas 4 mutações: upsert, delete, activate/deactivate de reserva (ativar reserva sobe C na hora; desativar reduz — redução sempre aceita, conforme modelo). `config.py` ganha `redis_url` (`PLUGHUB_PRICING_REDIS_URL`; vazia = sync off; Redis fora = warning, billing nunca quebra); dependência `redis[hiredis]`; compose com env + depends_on redis.

**Granularidade decidida**: uma chave por tenant (a única com leitores hoje). Chaves por `resource_type` (gate de instância IA, login humano concorrente) entram com os respectivos gates (itens 2 do arco).

**Bug pré-existente descoberto na validação** (a quota somou "errado" → 27): a constraint `uq_installation_resource` incluía `reserve_pool_id` **NULL** e em Postgres NULL ≠ NULL → o `ON CONFLICT` do upsert **nunca disparava para recursos base** — cada POST inseria linha duplicada (20+5+2=27). Fix: constraint recriada com `UNIQUE NULLS NOT DISTINCT` (PG15+; demo é pg16) + migração idempotente no `ensure_schema` (detecta via `pg_index.indnullsnotdistinct`, deduplica mantendo a linha mais recente por chave lógica — a última escrita do operador — e recria a constraint). O `sync_all` do boot roda após a migração → quota corrige sozinha no restart.

**docs**: `pricing.md` § Quota Side Effects reescrito (descrevia chaves inexistentes que nem batiam com os leitores); spec do arco e TODO marcam item 1 ✅.

---

## Relatórios Fase 2 — Pools/Infra: fechamento de capacidade (2026-06-04)

Fecha o item "Pools/Infra restante" do TODO. Recon inicial confirmou (TODO atrás do código, 4ª vez na semana) que os itens 1, 2 e 4 do § Pendente do spec **já estavam implementados**: occupancy sampler no routing-engine (`_occupancy_sampler` — amostra `active_count` por pool a cada 5s + total instantâneo do tenant, pico/minuto, carry-over implícito → Kafka `pool.occupancy`), consumer + `pool_occupancy_peaks` + `/reports/pools/occupancy` no analytics-api, e a aba Analytics→Pools. Decisões de fechamento: **(a)** sampler basta — contadores event-driven (SET de session_id) descartados, ficam como evolução se deriva do `active_count` aparecer; **(b)** teto do **total** = capacidade **configurada no pricing**; per-pool segue a provisionada (pricing não tem granularidade por pool de routing); **(c)** time-series de capacidade na UI (Arc 19 "Pools (time-series capacity)").

**pricing-api** (`db.py`, `router.py`): novo `GET /v1/pricing/capacity/{tenant_id}` — agrega `installation_resources` ativos (base + reservas ativas) por `resource_type`; `agent_capacity_total` = ai_agent + human_agent (denominador do total).

**analytics-api**: novo `pricing_client.py` (httpx, cache TTL 60s, degradação graciosa — pricing fora/sem recurso → mantém provisionada); `config.py` ganha `pricing_api_url` (`PLUGHUB_PRICING_API_URL`); `_fetch_pools_occupancy` retorna `total_series` (linha `__total__` por bucket, só p/ callers sem escopo); a rota `/reports/pools/occupancy` sobrescreve `total.capacity` com a configurada quando disponível (recalcula headroom/utilização) e expõe `capacity_source` (`pricing`|`provisioned`) + `provisioned_capacity` preservada.

**platform-ui** (`AnalisePoolsPage`): aba Capacidade ganha gráfico **Concorrência no tempo** — área = pico de concorrência (total do tenant, ou o pool quando filtrado), linha tracejada = capacidade provisionada por bucket, `ReferenceLine` = capacidade configurada (quando `capacity_source=pricing`); KPI "Capacidade total" indica a fonte; bucket hour ≤48h senão day (convenção do spec). i18n en + pt-BR (`pools.capacity.*`).

**docker-compose.demo.yml**: `PLUGHUB_PRICING_API_URL: http://pricing-api:3900` no analytics-api.

Spec atualizado (`docs/arcos/pools-infra-report.md`): estado → implementado; § "Como foi implementado — occupancy sampler"; § Fonte de capacidade com o refinamento 2026-06-04; § Pendente→Concluído com residuais opcionais (sub-aba Visão geral, heatmap hora×dia, SETs de session_id, overlay licenciada v2).

Validação (usuário, demo): `GET /v1/pricing/capacity/tenant_demo` → 25 (5 human + 20 ai); occupancy `total.capacity=25 / capacity_source="pricing" / provisioned_capacity=298` preservada; UI com KPIs + fonte + gráfico. Ajuste pós-validação: na visão do **total** com teto do pricing, a linha da provisionada sai do gráfico (provisionada ~295 — Σ instâncias×max_concurrent, igual ao Total do Monitor — esmagava o eixo contra pico 5 / teto 25; `ReferenceLine` com `ifOverflow="extendDomain"`). **Dívida descoberta**: integração pricing→quota Redis (`{t}:quota:*` / `assertQuota`) documentada em `pricing.md`/CLAUDE.md mas inexistente no pricing-api — teto contratado é só analítico; gate de admissão não arma (registrada no TODO).

---

## validateFlow — adjacência fechada + política de guarda de ciclo (2026-06-04)

Ponto cego descoberto no fix do copilot: `_getSuccessors` (engine.ts) não percorria `conditions[].next`/`default` do choice (formato real dos YAMLs), `strategies[]` do catch (lia o singular legado), nem campos-objeto `{next}` de collect/suspend (`on_response`/`on_resume`/`on_reject`/`on_timeout`) — ciclos por esses caminhos escapavam da validação (ex.: loop do `agente_fila_v1` nunca foi detectado).

**Política de guarda consolidada**: ciclo é controlado quando passa por step **bloqueante** — `receive` com `max_iterations` (freio por contagem), qualquer `menu` (bloqueia em I/O externo; inclui standby de @mention), `suspend`/`collect` (bloqueiam por sinal externo; teto = timeout scanner do gateway). Runaway real = ciclos só de reason/notify/invoke/choice (queimam LLM sem freio) — esses continuam rejeitados.

Auditoria pré-mudança dos 23 YAMLs: 6 flows com ciclo (auth_ia, echo, revisao_treplica, reembolso_demo, fila, copilot) — **todos** passam por guarda bloqueante; fechamento da adjacência não quebra nenhum flow existente.

TODO correlato: Fase D do delegate (timeout scanner) constatada **já implementada** (`run_timeout_scanner` no gateway, `delegate-workflow-io.md` § Fase D ✅) — entrada do TODO estava desatualizada.

---

## Registry: limpar campos de pool via PUT null (2026-06-04)

Gap recorrente (mordeu 3× na semana do queue-attended-model): campos opcionais de pool não podiam ser limpos via PUT — Zod rejeitava `null` e a única via era SQL + republish. Resolvido:

**schemas (`PoolRegistrationSchema`)**: `.nullable()` em `queue_config`, `session_reservation`, `max_concurrent_sessions`, `max_reply_time_ms`, `calendar_id` — PUT com `null` limpa o campo; ausente continua significando "não mexer" (RegistrySyncer/YAML sem o campo NÃO apaga config feita via UI).

**agent-registry (`pools.ts`)**: update mapeia `queue_config: null → Prisma.DbNull` (JSONB); escalares aceitam null direto. Consumidores a jusante já tratavam (`or None` no kafka_listener; bridge trata queue_config null como ausente → default do tenant).

**platform-ui**: desmarcar a skill de fila num pool que tinha `queue_config` envia `null` (limpa de verdade); fix colateral no clear de `calendar_id` (enviava `undefined`, que o `JSON.stringify` remove — nunca limpava); `CreatePoolInput`/`UpdatePoolInput` aceitam null.

Validado: `session_reservation` 2→null e `queue_config`→null via curl, cache do routing acompanhando (`pool.updated`); restart do bridge restaura a fila do YAML. Resolve o residual (b) do queue-attended-model.

---

## Webhook pools — capacidade fictícia eliminada (coerência) (2026-06-04)

Re-validação do item do TODO (escrito na discussão da admissão híbrida): (1) o **default 500 já não existia** — `PoolRegistrationSchema.max_concurrent_sessions` é `.optional()` e o registry grava null; (2) a premissa "nada é pré-instanciado" ficou stale pós **Arc 19 Fase C** — webhook pools têm slots de instância criados pelo Bootstrap a partir do `deploy:`, e a capacidade real = slots + admissão híbrida (Fase B); (3) o campo pool-level era **display-only** no snapshot do Monitor (nunca gateia alocação) — essa era a capacidade fictícia restante.

Aplicado (escopo coerência, sem mudança de comportamento de alocação): `max_concurrent_sessions: 20` pool-level removido do webhook pool do demo (`tenant_demo.yaml` — o `deploy:` mantém a concorrência real); comments revisados em `agent-registry.ts` (schema) e `registry.py` (snapshot) — campo redefinido como **throttle opcional de downstream** (backpressure p/ sistemas frágeis; Monitor exibe `max − busy` quando setado; ausente = capacidade real por instâncias). Enforcement do throttle no routing = deferred (TODO).

Nota operacional: limpar o valor no Postgres exige SQL (PUT parcial não aceita null — mesmo gap da `session_reservation`).

---

## Copilot @mention standby — três causas, fix completo (2026-06-04)

Sintoma: copilot morria na entrada (segmento 0s, sem anúncio), re-convite a cada @mention, comando nunca despachado. **Três causas empilhadas**:

1. **Validador de ciclos do engine (killer imediato)**: `validateFlow` rejeitava o ciclo `aguardar → analisar → sugerir → aguardar` ("unguarded cycles") e o `/execute` falhava antes do primeiro step. Fix: menu com **`standby: true`** conta como guarda de ciclo — avança só por interrupt externo (humano), sem runaway; equivalente a `receive` com `max_iterations`.
2. **Roteadores entregavam mensagem comum ao standby**: handler WS de texto + `menu_submit` (mcp-server) roteiam mensagem de agente para qualquer waiter `agents_only` — o texto do humano (inclusive o próprio `@copilot ...`) estourava o BLPOP. Fix: campo `standby` no `MenuStepSchema` → `menu.ts` grava no hash `menu:waiting` → roteadores pulam entradas standby.
3. **Dispatch na chave errada**: `dispatch_mention_command` (bridge) e `mention_command_dispatch` (bpm.ts) empurravam interrupts para `menu:result:{sid}` session-scoped, mas o specialist BLPOPa na instance-scoped (`{sid}:{iid}`) — o comando nunca chegava. Fix: ambos miram a chave instance-scoped (instance_id do `specialist_key`).

Validado: anúncio + standby armado, `@copilot ativa`/`pausa` (ack)/`para`, re-invite via @mention e via botão Trigger, mensagens humano↔cliente fluindo sem acordar o standby. Descoberto e registrado em TODO: ponto cego do `validateFlow` (`conditions[].next` de choice fora da adjacência — ciclo do agente de fila escapa por acidente). Guia: `docs/guias/mention-protocol.md` § Standby.

---

## Render v2 — Mensagens de Sistema no WebChat (2026-06-04)

Webchat entregava mensagens só pelo stream canônico — mensagens de sistema do routing (`message.text` via outbound) eram no-op: rejeição outage, timeout de fila muda e aviso de espera fechavam o WS em silêncio (pendência da Fase B do queue-attended-model).

**channel-gateway (`webchat_channel.py`):** `deliver_text` com `author.type=system` → frame `msg.text` direto via WS (label "Sistema"; sem duplicação — sistema não tem contraparte no stream); `deliver_session_closed` renderiza novo campo `farewell_text` antes do frame de close — mensagens acopladas ao fechamento viajam no próprio `session.closed`, eliminando a corrida message×close por construção.

**routing-engine:** `_emit_outage` ganhou a mensagem de rejeição via `farewell_text`; timeout de fila muda e `_emit_no_resource_drop` migrados de message.text+close para close+farewell; aviso "Aguardando agente disponível..." suprimido quando o pool tem `queue_config` (fila atendida — saudação do flow cobre).

**Configurabilidade/idioma**: as 4 mensagens de sistema viraram chaves do Config API namespace `routing` (`msg_queue_waiting`, `msg_outage_rejection`, `msg_queue_timeout`, `msg_no_resource`) — tenant edita no idioma desejado, hot-reload via `config.changed` já existente (`RoutingConfigCache`); defaults pt-BR em `routing_config.py`; seeds com descrição no config-api. Mensagens da fila atendida seguem no skill-flow YAML (conteúdo do tenant).

Validado: 2ª sessão rejeitada por `reservation_full` no sac_ia renderizou "Não há atendentes disponíveis no momento..." (label Sistema) antes do "Atendimento encerrado". Aprendizado operacional do teste: PUT de `session_reservation` leva segundos até o cache do routing (`pool.updated`) — sessões abertas na janela caem no bucket vigente. Pendência: voice/whatsapp não renderizam `farewell_text` (voice = TTS futuro). Spec: `docs/arcos/queue-attended-model.md` § Render v2.

---

## sessions.sla_target_ms — dívida de origem resolvida (2026-06-04)

Aba SLA de Analytics/Pools estava sem dado (`sla_eligible=0`) porque `sla_target_ms` nunca chegava ao ClickHouse. **Causa raiz dupla**: (1) `_SESSION_COLS`/`_session_row` (clickhouse.py) nunca incluíram a coluna — o INSERT descartava a chave que o `parse_routed` já mandava desde sempre; (2) mesmo persistindo, a linha de close substituiria com NULL (ReplacingMergeTree: última escrita vence a linha inteira — mesma classe do problema documentado do `channel=""`).

**Fix nas três pontas**: analytics-api — `sla_target_ms` em `_SESSION_COLS`/`_session_row` e na linha de close do `parse_contact_closed`; orchestrator-bridge — `_close_contact_layer` lê `{t}:pool_config:{p}` do Redis e denormaliza `sla_target_ms` no `contact_closed`; routing-engine — helper `_pool_sla_target` + campo nos closes autoritativos (`_emit_outage`, `_emit_queue_timeout`).

Validado: sessão nova no sac_ia gravou 480000 → relatório Fila/SLA com `sla_eligible=1`, `sla_attainment=1.0`. Histórico permanece NULL (valor nunca foi persistido). Dívida correlata `sessions.wait_time_ms` dispensada — segments `role='queue'` são a fonte de espera desde a Fase D.

---

## Queue-Attended-Model Fase E — Fechar-Sempre / Cadeia de Fallback (2026-06-03)

Fecha o modelo (A–E completas): nenhum contato fica em fila eterna. Cadeia: `catch` do flow → fila atendida → **`max_wait_exceeded`** como teto de retenção.

**routing-engine:** sweep de timeout no `_periodic_queue_drain` — lê `queue_config.max_wait_s` do cache de pool (passthrough novo: `PoolConfig.queue_config` + kafka_listener) com fallback `queue_max_wait_default_s` (1800, limita filas mudas também); ZREM-first contra corrida. `_emit_queue_timeout` (espelho do `_emit_outage`; routing = escritor autoritativo de sessão nunca roteada, fecha o gap de tenant da Fase A): marcadores closed+close_fired → fila atendida: sinal `__queue_timeout__` via `menu:result` + `session.closed` adiado por `queue_timeout_close_grace_s` (4s); fila muda: segmento sintético `role=queue` (system, duration=espera, abandoned/max_wait_exceeded) + message.text best-effort → `contact_closed` autoritativo (`max_wait_exceeded`+`abandoned`). `_emit_no_resource_drop`: caminho sem pool_id fecha gracioso (`no_resource`) em vez de sessão muda eterna.

**skill-flow (agente_fila_v1.yaml):** branch `__queue_timeout__` no `verificar_sinal` → `avisar_timeout` (notify via stream — webchat não implementa `deliver_text`, o flow é quem fala) → `finalizar_timeout` (outcome `failed`; plataforma sobrescreve para `abandoned`).

**orchestrator-bridge:** cases defensivos `max_wait_exceeded`/`no_resource` no mapeamento de close_reason do `_close_contact_layer`.

**platform-ui:** seção "Tratamento de Fila" no form de pool (`/config/resources`) — **skill-first**: dropdown do catálogo de skill-flows + espera máx (s); decisão: fila NÃO vai no Flow/Deploy (deploy = skill que atende o pool, com slots/instâncias; fila = política do pool, ativação por sessão sem slot). `QueueConfigSchema.agent_type_id` legado (default "") — bridge resolve o flow direto pela skill (`process_queued` aceita queue_config só com `skill_id`); `QueueConfig` em types; i18n configRecursos en+pt-BR. `tenant_demo.yaml`: `max_wait_s: 1800` no retencao_humano (validado com 30s — RegistrySyncer re-PUTa o YAML a cada start do bridge; curl/UI são sobrescritos, YAML é a fonte de verdade).

Validado end-to-end (fila atendida): espera 30s → mensagem de timeout renderizada no widget → WS fechado ~4s → relatório Fila/SLA contabilizou abandono com espera coerente. Pendências: render v2 webchat para message.text de sistema (fila muda/outage silenciosos); cenários fila muda e drop sem pool não exercitados; limpar queue_config via UI (Zod null). Spec: `docs/arcos/queue-attended-model.md`.

---

## Queue-Attended-Model Fase D — Relatório de Fila/SLA sobre Segments + Demanda Reprimida (2026-06-03)

O ledger de fila passa a ser consumido na ponta: `/reports/pools/queue` reescrito sobre os segments `role='queue'` (Fase C) e o Volume ganha o KPI de demanda reprimida (segmentos sintéticos da Fase B). O interim "gap até o primeiro primary" foi removido.

**analytics-api (`reports_query.py`):** `_fetch_pools_queue` — por sessão (excluindo `outcome='outage'`), LEFT JOIN com agregado de segments: `queued` = tem segmento de fila; **espera = `duration_ms` do segmento de fila** (fila ao vivo fora das stats, dentro de queued); `abandoned` = `q_outcome='abandoned'`; novo campo `handoff` = fila não-abandonada + primary real. **`abandon_rate` agora é abandonados/enfileirados** (antes /contatos). SLA: não-enfileirado espera 0; pool de sessão nunca roteada vem do segmento de fila (cobre o gap "sessão sem meta" da Fase A). `queue_events` permanece suplementar (max_queue_len/disponíveis). `_fetch_pools_volume` — bloco `rejected`: série bucket×pool×canal das sessões outage, `by_cause` (pool × `reservation_full|shared_full|quota` via join com segmentos system) e `totals.rejected`; `totals.contacts` segue sendo demanda total.

**platform-ui (`AnalisePoolsPage.tsx`):** card "Demanda reprimida" no Volume (total, % da demanda, tabela pool×causa) + KPI `rejected` no header; coluna "Pós-fila" (handoff) e hint de semântica na aba Fila. i18n en+pt-BR (`agentReports.json`).

Validado com os dados das Fases B/C: `retencao_humano` queued=3/handoff=1/abandoned=1, espera média ~110s, p95 ~277s; `rejected.total=2` com causas `shared_full`+`reservation_full` no `sac_ia`; sessões outage ausentes da aba Fila. Pendências: `sessions.sla_target_ms` NULL na origem (aba SLA sem dado — dívida routing→analytics em `pools-infra-report.md`); fila ao vivo conta como dentro do SLA até fechar. Spec: `docs/arcos/queue-attended-model.md`.

---

## Queue-Attended-Model Fase C — Fila Atendida com Segmento Próprio (2026-06-03)

**Decisão**: segmento do agente de fila marcado com **`role: queue`** em vez de pool separado (`pool_kind`/`queue_pool_id` dispensados no MVP). O `queue_config` existente (descoberta B0) já ativa o agente de fila no próprio pool-alvo — segmento com `pool_id` = alvo é a dimensão exata do relatório Fila/SLA da Fase D, e as queries de agente (`primary`/`specialist`) excluem fila por construção. Invariante analítico: **"atendido" = primeiro segmento `primary`**.

**schemas:** `queue` adicionado aos enums de role em `contact-segment.ts` (ContactSegmentSchema + ConversationParticipantEventSchema).

**orchestrator-bridge:** `process_queued` emite `participant_joined` (role=queue, `participant_id=queue-{session_id}`, instance_id="") antes de ativar o agente de fila e `participant_left` (duration = janela de espera, outcome do flow — escalated_human/abandoned/timeout —, flow_id) na conclusão. Não toca `segment_seq`/`primary_segment` nem `session:{id}:last_outcome` (só primary dirige outcome de sessão). Fallback de tenant: pool sem `queue_config` usa `queue_default_agent_type_id`/`queue_default_skill_id` do namespace `session` (Config API); vazio = espera muda (comportamento original).

**routing-engine:** `_write_queue_context` escreve `session.queue.position` (1-based, tamanho da fila pós-inserção) e `session.queue.eta_ms` (posição × sla_target_ms × 0.7, espelha `_publish_queue_position`) no ContextStore a cada tentativa de enqueue — drain re-attempts refrescam a posição. Skill-flow de fila pode referenciar `@ctx.session.queue.*`.

**config-api:** seeds `session.queue_default_agent_type_id` / `session.queue_default_skill_id` (default ""); espelhados em `session_config.py` do bridge (hot-reload via `config.changed` namespace session já cobre).

**analytics-api:** nenhuma mudança necessária — parser de participants é passthrough de role (coluna String); filtros existentes (`role IN ('primary','specialist')` na performance, `role='primary'` na espera interim) excluem queue automaticamente.

**Fixes da validação:** (a) abort do agente de fila no disconnect — bridge soma 1 push de `session:closed` quando `queue:agent_active:{sid}` existe (agente de fila roda com `instance_id=""` → `menu.ts` não cria activity key → contagem genérica o ignorava → BLPOP eterno, segmento nunca fechava); (b) override de outcome no fechamento do segmento de fila: `session:{id}:closed` presente → `abandoned` (plataforma detecta; o complete do YAML reporta `escalated_human` mesmo via `on_disconnect`, e o contrato Fase A proíbe o flow de declarar abandono). `queue_config` adicionado ao `retencao_humano` em `infra/registry/tenant_demo.yaml` (agente_fila_v1/skill_fila_v1).

Validado: handoff (`escalated_human`, 21s, primary humano `sequence_index=0` intacto, ContextStore position/eta_ms corretos) e abandono (`abandoned`, 5.6s). Pendente: timeout de fila (`max_wait_s` não enforced — Fase E); posição não re-escrita entre drains; `close_reason` NULL no segmento de fila; i18n do role `queue` na UI. Spec: `docs/arcos/queue-attended-model.md`.

---

## Queue-Attended-Model Fase B — Admissão Híbrida + Outage na Porta (2026-06-03)

Modelo *trunk reservation*: `session_reservation` (pool, opt-in) = fatia dedicada (teto+garantia) subtraída do total; pools sem reserva disputam o shared coletivo (`total − Σ reservas`). Billing só sobre o total. Rejeição na porta = **outage** registrado (demanda reprimida).

**routing-engine:** novo `admission.py` — buckets como SETs de session_id (idempotente em re-publish; escalação = migração de bucket; migração rejeitada = fail-open mantendo origem — fechar sessão viva é da cadeia de fallback Fase E); reconciler 60s libera slots de sessões com `session:{id}:closed`; check em `_process_message` antes do `route()` (conference events isentos); `_emit_outage` publica contact_closed autoritativo (`close_reason=no_resource`, `outcome=outage`, `outage_cause`) + segmento sintético (`agent_type=system`, duração 0, pool que faltou) + outbound close, com guards `closed`/`contact_close_fired` bloqueando re-close do bridge.

**agent-registry/schemas:** `session_reservation` em `PoolRegistrationSchema`, coluna Prisma + migration, CRUD create/update; `_formatPool` e RegistrySyncer são passthrough → YAML e PUT propagam sem código extra.

**analytics-api:** `agent_type != 'system'` na performance de agente e na derivação de espera (segmentos sintéticos nunca contam como atendimento).

Validado: teto 2 + 3 webchats → 3º rejeitado `shared_full`; sessão `no_resource+outage`; segmento system com causa. Decisão registrada (TODO): webhook `max_concurrent_sessions` default 500 é capacidade fictícia → remover default, campo vira throttle opcional de downstream (re-validar ao retomar). Spec: `docs/arcos/queue-attended-model.md`.

---

## Queue-Attended-Model Fase A — Padronização outcome/close_reason (2026-06-03)

Antes: `sessions.outcome` NULL em 100%; `close_reason` com valores de transporte fora do domínio (`client_disconnect` 71×, `agent_done` 43×). Causa: dois escritores do `contact_closed` vazando o contrato de transporte pro ClickHouse, e o outcome nunca propagado.

**orchestrator-bridge:** marcador `session:{id}:last_outcome` ({outcome, agent_kind}) escrito no agent_done IA primary, no contact_closed humano (Console agora propaga) e no abandono por disconnect; `_close_contact_layer` deriva `close_reason` de negócio (tabela transporte→domínio) + `outcome` e os inclui no evento analítico — `reason` de transporte permanece intacto (re-entrada/`customer_side` dependem dele). `participant_left` humano ganha `outcome` (Console ou `abandoned`).

**mcp-server-plughub:** `/api/agent_done` inclui `outcome` no `contact_closed`.

**channel-gateway:** `_close` do webchat idempotente (publicava 2× no fechamento por plataforma); `ContactClosedEvent` ganha `close_reason` + `source: channel_gateway`.

**analytics-api:** **bridge é o escritor único** da linha de fechamento — eventos `source=channel_gateway` não fazem upsert de sessions (eliminava corrida no ReplacingMergeTree: o teardown do WS pelo widget chegava depois do evento enriquecido e sobrescrevia com NULL); prioridade `close_reason` > `reason` no parse.

**schemas:** `SegmentOutcomeSchema`/`SessionOutcomeSchema` estendidos pro domínio completo do ledger (incl. `escalated_human/ai`, `suspended`, `outage`); contrato do agente (`OutcomeSchema`) intencionalmente intocado — plataforma detecta `abandoned/timeout/outage`, agente não declara.

Validado: `flow_complete+resolved` (IA), `agent_hangup+resolved` (humano), `customer_disconnect+abandoned` (F5). Spec: `docs/arcos/queue-attended-model.md`.

---

## Ocupação do Agente (busy ÷ disponível) (2026-06-02)

Fecha a visão de produtividade do agente no relatório de disponibilidade.

**analytics-api** (`_fetch_agent_availability`): nova query de **busy** a partir de `segments FINAL` (soma `duration_ms` dos roles `primary`/`specialist`), agrupada por `(instance_id, pool_id, date)` — junta na mesma chave de identidade do relatório (segments já têm `instance_id`). Novo campo `busy_ms` por linha.

**platform-ui** (`AgentsTab` → Disponibilidade): agrega `busy` por identidade e adiciona colunas **Ocupado** (tempo em atendimento) e **Ocupação** = `busy ÷ disponível` (padrão de contact center: % do tempo logado-menos-pausa gasto atendendo). i18n `busy`/`occupancy` (en + pt-BR). Ocupação pode passar de 100% para agentes multi-sessão (concorrência) — exibida como valor real.

**Pausas — gestão de motivos**: decidido manter lista **global** (a pausa é do agente, remove de todos os pools → associação por pool é incorreta); Config UI descartada (overkill). Único ajuste: i18n dos motivos default + textos do `PauseReasonModal` (namespace `agentAssist.pause`, en + pt-BR); labels do Config API permanecem como configurados.

---

## Pausa — Persistência através de Reconnect do Console (2026-06-02)

O estado de pausa não sobrevivia a trocar de tela: ao sair do Console, após o grace o `unregisterHumanAgent` deleta a instância (logout); ao voltar, `registerHumanAgent` recriava como `ready` e o `isPaused` (estado UI-local) voltava a false.

**Descoberta-chave:** a alocação no routing exige `inst.state == "ready"` (`registry.py` linhas 161/652) — um agente `paused` é excluído **pelo estado**, sem depender de set membership. Então basta o `status="paused"` fluir.

**mcp-server-plughub:**
- **Key durável** `{tenant}:agent_paused:{instanceId}` (reason + paused_at, TTL 16h) — escrita no `/api/agent-pause`, deletada no `/api/agent-resume`. Sobrevive à deleção da instância no logout.
- **`registerHumanAgent`**: lê a key; se presente, registra com `status="paused"` (instância + evento `agent_ready`) → o `_upsert_instance` do routing mantém `state="paused"` e o `_drain_queue_for_agent` não dreneia (checa `state != "ready"`).
- **Heartbeat do WS** (`pong → agent_heartbeat`): passa a enviar `status` lido da key durável (antes hardcoded `"ready"`) — senão o heartbeat a cada ~15s ressuscitava o agente como ready. Agentes não-pausados: inalterado.
- **Novo `GET /api/agent-state`** (auth): retorna `{paused, reason_id, reason_label}` da key durável.

**platform-ui:** `AgentAssistPage` lê `/api/agent-state` ao montar (uma vez, quando há sessão) e seta `isPaused` → o botão reflete a realidade após reconnect.

Comportamento novo só afeta agente **pausado** (caminho ready normal intacto = baixo risco). Não foi preciso tratar o mismatch `agent_pause`/`agent_paused` do routing (a exclusão vem do estado).

**Pausa órfã resolvida:** distinguir KEY durável (marcador de restauração) do INTERVALO de pausa no analytics. No `agent_logout`, o consumer fecha a pausa **aberta** somente quando a key durável está **ausente** (= logout explícito, que o `clear-pause` apaga antes do grace); na navegação/queda a key persiste → a pausa fica aberta e contínua, fechada depois pelo resume. Sem órfã no logout real, sem cortar a pausa na navegação.

**Semântica de expiração (b+c):**
- **TTL por motivo**: a key durável usa `max_minutes` do motivo (Config API `pause_reasons`) + 30min de tolerância (default 4h, teto 16h). O modal repassa `max_minutes` → endpoint. Uma pausa esquecida expira sozinha e o login seguinte começa `ready` (não arrasta para o dia seguinte). Queda/navegação dentro da janela preservam.
- **Logout explícito limpa**: novo `POST /api/agent-clear-pause` chamado pelo `logout` central do `AuthContext` (cobre todos os caminhos; no-op se não-pausado). Encerrar o turno começa limpo; navegação/queda não passam por aqui.

---

## Fix — Pausa de Agente Humano chegava ao Analytics (2026-06-02)

A pausa pelo Console nunca registrava no analytics (relatórios de pausa/donut vazios). Dois bugs pré-existentes:

1. **Auth**: o `fetch` de `/api/agent-pause` e `/api/agent-resume` (AgentAssistPage) não enviava `Authorization` — não há interceptor global, o access token vive no `AuthContext`. `requireJwtRole` → 401 silencioso. Corrigido enviando `Bearer ${session.accessToken}`.
2. **Redis key/formato**: os endpoints liam a instância com `hget`/`hset` em `keys.agentInstance` (`${t}:agent:instance:…`, hash), mas o agente humano é gravado como **string JSON** em `${t}:instance:…` por `registerHumanAgent`. Lookup falhava (404 silencioso) → `agent_pause` nunca publicado. Reescritos pause/resume para ler/gravar a string JSON no key correto, manipular `poolInstances`/`poolAvailable`, e o **resume publicar `agent_ready` com identidade completa** (user_id/user_login/execution_model/pools) para não disparar o wipe da instância no routing.

Também: availability e pauses agora incluem intervalos **em aberto** (login e pausa em andamento contam até `now()` via `toUnixTimestamp64Milli`), para visão ao vivo; e o limite superior de data usa `_ch_fmt(to_dt, upper=True)` (antes a meia-noite excluía o dia corrente).

**Pendente** (tarefa isolada): preservar o estado de pausa através de reconnect do Console (o reconnect re-registra como `ready`). Ver `TODO.md`.

---

## Timeline do Agente — Presença por Pool (2026-06-02)

Visualização em **swimlanes** por agente: faixa "Total" (tempo logado) + uma faixa por pool, no mesmo eixo de tempo, com blocos de presença e overlay de pausas. Resolve a pergunta "quanto tempo logado no total **e** quanto em cada pool" sem somar (as duas visões respondem perguntas diferentes; tempos por pool não são aditivos).

**Decisão:** humano é uma instância (`human-{userId}`) com **um** relógio. Tempo logado = métrica da pessoa. Presença por pool é uma atribuição (replicada, não dividida) derivada do `pools[]` que viaja nos eventos `agent_ready`.

**analytics-api:**
- `clickhouse.py`: tabela `agent_pool_intervals` (entered_at/left_at/duration por pool, `login_interval_id` ligando ao intervalo de login) + `upsert_agent_pool_interval` + `_agent_pool_interval_row`.
- `consumer.py`: `_handle_login_interval` ganhou diff de pools — a cada `agent_ready` com `pools[]` autoritativo (register/logout-parcial levam a lista completa; resume não leva → ignorado), abre sub-intervalo para pool que entrou e fecha o que saiu; `agent_logout` fecha todos. Estado `pools_open` no mesmo Redis key de login.
- `reports_query.py` + `reports.py`: novo endpoint `GET /reports/agent-timeline?instance_id&from_dt&to_dt` → `{login_intervals, pause_intervals, pool_intervals}` (timestamps ISO, intervalos abertos com fim null). Pool scope aplicado às presenças.

**platform-ui:**
- `AgentTimeline.tsx`: modal de swimlanes (faixa Total + uma por pool; barras posicionadas por % no eixo de tempo; pausas overlaid em todas as faixas; legenda + eixo de horas).
- `AgentsTab.tsx`: linhas da tabela de Disponibilidade viram clicáveis (drill-down) → abre a timeline do agente no período. i18n `timeline.*` em en + pt-BR.

→ Ver `docs/arcos/arc8-agent-availability.md` § Timeline.

---

## Fase 1b — Tempo Logado / Disponibilidade (2026-06-02)

Habilita rastreamento de **tempo logado** e **disponibilidade** do agente (antes só havia tempo de pausa). Nova tabela ClickHouse `agent_login_intervals` espelhando o padrão `agent_pause_intervals` (Arc 8).

**Decisão de design (sem mudança no producer):** em vez de criar um novo evento de login (risco de o routing engine reconstruir a instância de forma incompleta — classe do bug de wipe), o consumer **reusa eventos existentes**: o primeiro `agent_ready` (humano — já carrega `user_id`/`user_login`) ou `agent_login` (nativo) **abre** o intervalo; `agent_logout` **fecha**. Zero mudança em mcp-server, schemas ou routing.

**analytics-api:**
- `clickhouse.py`: DDL `agent_login_intervals` (ReplacingMergeTree, ORDER BY tenant/instance/logged_in_at) com `user_id`/`user_login`; `upsert_agent_login_interval` + `_agent_login_interval_row` + cols; registrada na criação de tabelas.
- `consumer.py`: máquina de estados de login independente do fluxo de parse/pause — `_handle_login_interval` com namespace Redis `{tenant}:login:{instance}` (separado de `:pause:`); abre só se não houver intervalo aberto (resume/pool-refresh apenas renova TTL 24h); `agent_logout` calcula `duration_ms`. Injetada antes do early-return do parser (que continua pulando login/logout).
- `reports_query.py`: `_fetch_agent_availability` reescrito — agrupa por **instance_id** (por pessoa, deixa de colapsar humanos em `human_agent_{pool}`), faz merge de login + pause + reason_breakdown em Python; devolve `logged_ms`, `total_logins`, `available_ms = logged − pausas`, `user_login`/`user_id`/`instance_id`. Compatível: campos antigos (`agent_type_id`, `total_pause_ms`, `reason_breakdown`) preservados.

**platform-ui (`AgentsTab.tsx`):** sub-aba Disponibilidade vira tabela por identidade (Agente=user_login | Pool | Logado | Pausado | Disponível | Disp.%) + **donut de motivos** (Recharts PieChart sobre `reason_breakdown`). Sub-aba Pausas rotula por `user_login`. i18n: chaves `logged`/`paused`/`available`/`availPct`/`reasonsTitle`/`noReasons` em en + pt-BR.

**Limitações conhecidas:** intervalo de login com TTL 24h — turno >24h sem evento gera intervalo órfão (aceito). Ocupação (busy ÷ logado, a partir de segments) ficou para passo seguinte (decisão 2a).

→ Ver `docs/arcos/arc8-agent-availability.md`.

---

## C1b-B — Daily Trend por Identidade (2026-06-02)

Analytics/**Agents** → "Daily Trend" agora reflete a identidade por aba (humano por `user_id`, IA por `flow_id`), em vez de colapsar todo humano em `human_agent_{pool}`.

**Backend (`reports_query.py`):** `_fetch_agent_performance_daily` reescrito para ler `segments FINAL` direto (subquery computa `period_date`, `is_human` e `agent_key`), abandonando a MV `mv_agent_performance_daily` (keyed por `agent_type_id`, que colapsa humano). Subquery seleciona só as colunas necessárias (sem `SELECT *`) para reduzir I/O. Devolve `agent_type`/`user_login`/`flow_id` para filtragem client-side.

**UI (`AnaliseAgentesPage.tsx`):** `PerformanceDailyRow` ganhou `agent_type?`; `tabDailyRows` filtra `dailyRows` por `agent_type` (`=== 'human'` na aba Human, `!== 'human'` na AI); `<TrendChart rows={tabDailyRows}>`.

**Bug pré-existente corrigido (chart):** o `TrendChart` usava `stroke="var(--color-green|warning|border)"` — CSS custom properties **inexistentes** no projeto (cores são tokens Tailwind, não `--color-*`), então as linhas e o grid ficavam invisíveis (eixo e tooltip seguiam funcionando, o que mascarava o defeito). Estava oculto porque o endpoint daily antes não trazia dado. Trocado por hex dos tokens (`#059669`/`#D97706`/`#E5E7EB`), padrão já usado em `TimeseriesChart/constants.ts`. Validado: curva de Resolution desenha (65→47→62→37% em 05‑29→06‑01), Escalation em 0%.

**Erros encontrados no caminho:** (1) `ILLEGAL_AGGREGATION` — alias `any(agent_type) AS agent_type` sombreava a coluna em `countIf(agent_type='human')`; resolvido computando `is_human` na subquery. (2) `date` não serializável em JSON — `period_date.isoformat()` no retorno.

Pendente derivado → **Fase 1b**: humano vem com availability/pauses vazio e `outcome`/resolution = 0% (segments humanos não marcam `outcome='resolved'`) — revisão de semântica de outcome humano + `agent_login_intervals`.

---

## Fase C/C2-C4 — Entidade `AgentType` REMOVIDA (2026-06-01)

Remoção física completa da entidade `AgentType` — a IA já era deploy-driven (síntese via skill) e o humano login-driven, então o `AgentType` era vestigial. Descoberta-chave: as UIs de CRUD de AgentType (`AgentTypesPage`, `HumanAgentsPage`) eram **código morto** (não roteadas — `/monitor` redireciona para `/flow/monitor`), então não houve migração para o auth — só deleção.

**UI (Fase 1 — código morto):** deletadas `AgentTypesPage.tsx`, `HumanAgentsPage.tsx`, `InstancesPage.tsx`, `MonitorPage.tsx` (service); funções AgentType removidas de `api/registry.ts`.

**Sync/YAML (Fase 2):** `RegistrySyncer` parou de sincronizar/prune agent_types; seção `agent_types` removida do `tenant_demo.yaml`. Validado: humano segue ativado via Path B (`execution_model=stateful` no Redis), independente do registry.

**Backend (Fase 3):**
- `pools.ts` `mentionable-agents`: fonte = `PoolSkillSlot.current` (skill_id), não mais `agentType.findMany` — restaura a lista de especialistas da Console.
- Novo `GET /v1/skills/:id/delegation-schema`; `useDelegationSchema` repontado p/ a skill.
- `instances.ts`/`pools.ts`: removida a relação Prisma `agent_type` (include/where) — tabela `agent_instances` é legada/vazia no deploy-driven (instâncias vivem no Redis).
- `mcp-server/registry-client.ts`: `agent_login` da IA valida contra `/v1/skills/:id` (identidade = skill_id; permissions vazias = sem filtro, o padrão deploy-driven).
- `agent-types.ts` + `import.ts` deletados; rotas removidas do `app.ts`; log de startup do `index.ts` atualizado.

**Prisma (Fase 4):** removidos `model AgentType`, enum `AgentTypeStatus`, `model AgentTypePool`, a back-ref `Pool.agent_types` e a relação `AgentInstance.agent_type` (FK → coluna simples). `prisma db push --accept-data-loss` (startup) **dropou** `agent_types` + `agent_type_pools`. Validado: tabelas inexistentes, agent-registry saudável, demo (humano/`@auth_form`/lista de especialistas) 100%.

**Decisão registrada**: rename de `agent_type_id`→`skill_id`/`flow_id` permanece **descartado** — o campo segue como carrier (skill_id p/ IA, placeholder p/ humano).

**Cleanup residual (inofensivo, dead code)**: funções `_sync_agent_type`/`_prune_agent_types` no `registry_syncer.py` (sem chamador); `elif framework == "human"` Path A no `main.py` (inalcançável — humano usa Path B); `AgentTypeSchema` em `@plughub/schemas` + `validators/agent-type.ts` (órfão). Removíveis numa varredura futura.

---

## Fase C/C1 — Identidade do agente humano por user_id/user_login nos segments (2026-06-01)

O agente humano passa a ser identificado no analytics pelo **login** (user_id estável + email para exibição), em vez do placeholder sintético `agent_type_id = human_agent_{pool}` — espelhando o que a IA já tem com `flow_id`. (Decisão da Fase C: **rename em massa de `agent_type_id` descartado** — 1198 ocorrências/136 arquivos, semanticamente errado p/ humano; o campo permanece como carrier. C1 entrega só a identidade humana.)

**Threading (platform-ui → segment ClickHouse)**:
- `platform-ui` (`useMultiPoolWebSocket.ts`, `AgentAssistContext.tsx`): query do WS leva `user_login` (= `session.email`).
- `mcp-server` (`server.ts`): `registerHumanAgent` grava `user_id`/`user_login` na instância **e** nos eventos `agent_ready` **e `agent_heartbeat`**.
- `routing-engine` (`models.py`, `kafka_listener.py`): `AgentInstance` declara `user_id`/`user_login` (sobrevive ao Pydantic) e `_upsert_instance` os propaga do evento.
- `orchestrator-bridge` (`main.py`): `activate_human_agent` lê o `user_login` da instância → grava no session meta → passa ao `_publish_participant_event` (join + left). O `user_id` é derivado do `participant_id` (`human-{userId}`) dentro do publish.
- `analytics-api` (`clickhouse.py`, `models.py`): colunas `user_id`/`user_login` no `segments` (+ migrações `ADD COLUMN IF NOT EXISTS`) + consumer.
- `schemas` (`contact-segment.ts`): `user_id`/`user_login`/`flow_id` nos schemas.

**Bug-chave (parecia "cache")**: o `agent_heartbeat` (a cada ~15s) passa pelo `_upsert_instance`, que **reconstrói** o `AgentInstance` a partir do evento — apagando `user_id`/`user_login` se eles não viajarem no heartbeat (mesma classe do `skill_id` da Fase 3b). Corrigido adicionando os campos ao `agent_ready` **e** ao `agent_heartbeat`, e declarando-os no modelo.

**Exibição (C1b parcial)**: `/reports/segments` (`reports_query.py`) passa a **selecionar** `user_login`/`flow_id`/`user_id`; `SegmentList.tsx` e `SessionTranscript.tsx` mostram o `user_login` (email) para segmentos humanos (IA mantém o label da skill). `ContactSegment` (service `types.ts`) ganhou os campos.

**Decisão — apelido voltado ao cliente NÃO incluído**: mostrar login/email ao cliente seria vazamento; o `name` já existe se um dia quiser nome amigável. Fora do escopo do C1.

**Validado**: instância persiste `user_login: admin@plughub.local` (heartbeat não apaga mais); segment no ClickHouse com `user_login` preenchido; Analytics/Sessions (lista + detalhe) exibindo o email no segmento humano.

**C1b-A — relatório de agentes (Analytics/Agents)**: `_fetch_agent_performance` (`reports_query.py`) deixa de colapsar humanos em `human_agent_{pool}` — agrupa por identidade via subquery: humano por `user_id` (display `user_login`), IA por `flow_id`/skill (fallback `agent_type_id` p/ histórico); retorna `user_login`/`flow_id`/`user_id`/`agent_type`. `AnaliseAgentesPage.tsx`: divide `perfRows` por `agent_type` → aba **Human Agents** ganha tabela de performance por agente (linhas por `user_login`) + KPIs só de humanos; aba **AI Agents** só IA. Bug colateral corrigido: endpoint `agent-performance/daily` quebrava com `TypeError: date is not JSON serializable` (pré-existente, disparou ao haver dados na MV) — `period_date` agora stringificado.

**Pendente Fase C**: C1b-B (MV `mv_agent_performance_daily` ainda keyed por `agent_type_id` → daily trend colapsa humano; + revisar dado de availability/pauses vazio no humano) — parte do redesign de analytics; C2 (tirar humano da entidade `AgentType`) + C3 (remover tabela/CRUD `AgentType`).

---

## Fase 3d (parcial) — Slots por pool.deploy, agent_types YAML aposentados, reconcile deploy-only (2026-06-01)

Aposenta os `agent_types` IA como fonte de provisionamento. O pool passa a ser dono do seu deploy; o bootstrap reconcilia exclusivamente a partir dos slots.

**Fonte de slots = `pool.deploy`** (decisão 1a): cada pool IA em `infra/registry/tenant_demo.yaml` ganhou `deploy: { skill_id, max_concurrent_sessions }`. `RegistrySyncer._sync_deploy_slots_from_pools` cria/promove o slot a partir do pool (substitui a antiga `_sync_deploy_slots` baseada em agent_types; sempre roda, idempotente). Env `REGISTRY_SYNC_DEPLOY_SLOTS` removido (morto).

**agent_types IA aposentados do YAML**: a seção `agent_types` foi reduzida ao único agente humano (`agente_retencao_humano_v1`, login-driven). O prune do RegistrySyncer remove os 16 agent_types IA órfãos do registry (`agent_types deleted=16`), restando só `['agente_retencao_humano_v1']`.

**reconcile deploy-only** (`instance_bootstrap.py`): removidos `_build_desired_state`, `_extract_all_pool_ids`, `_fetch_agent_types` e o param morto `active_pool_ids` de `_reconcile_pool_configs`. O reconcile agora monta o desired state só via `_build_desired_from_deploy` (pools com `deployed_skill_id`). O bridge sintetiza um agent_type native a partir da skill na ativação (404 → síntese).

**Hack removido** (`agent-registry/routes/pool-slots.ts`): `_applyMaxConcurrentSessions` (propagava max_concurrent do slot para agent_types do pool) + as 2 chamadas em promote/rollback. Dead após a aposentadoria (a capacidade vive no slot).

**Validado** (boot limpo, Redis vazio): `deploy_slots(set=0 skip=16 err=0)`, `agent_types deleted=16` → só o human; `Reconciliation created=295 errors=0` — 295 instâncias IA provisionadas exclusivamente pelos slots de deploy, sem nenhum agent_type IA no registry. Smoke-test `sac_ia` ok.

**Decisão 2a — campo `agent_type_id` mantido como carrier de `skill_id`** (proxy 1:1 em `_build_desired_from_deploy`): segue load-bearing no routing (restrição em `router.py`, chaves `agent_perf:{agent_type_id}`, crash recovery). A renomeação `agent_type_id`→`skill_id`/`flow_id` e a remoção da tabela/CRUD `AgentType` (bloqueada pela identidade do agente humano, que ainda vive como agent_type) ficam para a **Fase C**.

---

## Fase 3c — Migração completa para deploy-driven + mention_commands via flow (2026-06-01)

Conclui a Fase 3c: todos os pools IA do demo migrados para deploy-driven (slot+promote), `mention_commands` de especialista resolvido pela Skill (round-trip via agent-registry), e auto-provisionamento de slots ligado e validado.

**mention_commands via embed no flow** (`schemas/src/skill.ts` + `orchestrator-bridge`):
- Causa do gap: o `mention_commands` nunca round-trippava pela agent-registry — o modelo Prisma `Skill` não tem coluna dedicada, e o `CreateSkillSchema.parse` (Zod) **descartava** a chave quando aninhada em `flow`. No modelo legado funcionava só por leitura de disco por filename (`agente_copilot_v1.yaml`), que quebra no deploy-driven (instância carrega `skill_id`, não o nome do arquivo).
- Fix: `mention_commands` declarado em `SkillFlowSchema` (sobrevive ao parse) → `RegistrySyncer._sync_skills` aninha o `mention_commands` do top-level do YAML **dentro do flow** → persiste na coluna `flow` (JSON) → `get_skill_flow` devolve. `_synthesize_agent_type_from_skill` carrega o `mention_commands` do flow na síntese; `process_mention_routing` resolve via novo `_resolve_mention_commands` (cache do flow → agent-registry → disco como fallback dev), eliminando o acoplamento ao filename.
- `role`/`capabilities` avaliados e **não replicados**: confirmado que não são consumidos em runtime (routing-engine e bridge não leem). Isolamento de pools evaluator vem da topologia de roteamento, não do `role`. Síntese defaulta `role="executor"` só por completude cosmética.

**Migração dos pools** (slot+promote via `scripts/migrate_entry_pools.sh` + `migrate_conference_pools.sh`): entrada/jornada (`sac_ia`, `portabilidade_ia`, `reembolso_ia`, `auth_sac_ia`, `auth_ia`, `auth_form_ia`, `contexto_ia`) e conferência/hook (`copilot_sac`, `nps_ia`, `wrapup_ia`, `portabilidade_processo_ia`, `portabilidade_confirmacao`, `evaluador_echo_ia`). Validado: `nps`/`wrapup` (hook `on_human_end`), portabilidade webhook+delegate (intake → suspend → delegate → confirmação → resolved).

**Auto-provisionamento ligado** (`docker-compose.demo.yml`): `REGISTRY_SYNC_DEPLOY_SLOTS=true`. `_sync_deploy_slots` derivou os slots restantes dos agent_types IA do YAML — validado `deploy_slots(set=2 skip=14 err=0)` (set: `fila_humano` + `avaliacao_ia`; skip: os 14 já migrados manualmente; idempotente, pula `human`). `avaliacao_ia` registrou sem 422 nesta rodada (`skills upserted=23 err=0`).

**Issues separados (não bloqueiam)**: (1) copilot `@mention` standby fecha em 0s — bug pré-existente de conferência (corrida na chave `menu:result` session-scoped), documentado no TODO, independente do deploy-driven. (2) `evaluador_echo_ia` provisionado mas não exercitado no demo (hook `on_human_start: []` desativado por design).

**Pendente 3c/3d**: aposentar `infra/registry/*.yaml` (agent_types) requer fonte de slots para boot limpo (hoje `_sync_deploy_slots` deriva dos agent_types); 3d remove `agent_type` de schema/routing/bootstrap/segments + hack `_applyMaxConcurrentSessions`.

---

## Fase 3b + 3a — Provisionamento deploy-driven (pool + deploy, sem agent_type) (2026-05-31)

Migração do provisionamento de instâncias de IA do `agent_type` (legado) para o **deploy do flow** (`PoolSkillSlot.current`). Fonte de verdade passa a ser o slot de deploy do pool: skill + capacidade ("Concurrent sessions").

**Causa do gap** (confirmada empiricamente): pool criado via Config + skill deployada → `resources=0`. O `_applyMaxConcurrentSessions` (pool-slots.ts) só propagava capacidade para agent_types **pré-existentes** no pool; e o `instance_bootstrap` só criava instâncias a partir de `agent_types`. Pool sem agent_type = zero instâncias.

**Fase 3b — bootstrap por pool+deploy** (`instance_bootstrap.py` + `agent-registry/routes/pools.ts`):
- `GET /v1/pools` enriquecido com `deployed_skill_id` + `deployed_max_concurrent_sessions` (lidos do `PoolSkillSlot.current`).
- Novo builder `_build_desired_from_deploy`: para cada pool com slot `current`, cria N instâncias `{pool_id}-{n}` (N = concurrent sessions do slot) rodando a skill deployada, com `skill_id`/`flow_id` no payload e `source=bootstrap_deploy`. Aditivo: pools já cobertos por agent_type legado são pulados (zero sobreposição na transição). `skill_id` adicionado ao set MANAGED do diff.

**Fase 3a — bridge resolve a skill pela deploy** (`orchestrator-bridge/main.py`):
- Em `process_routed`, quando `get_agent_type` retorna None e a instância carrega `skill_id` (deploy-driven), sintetiza um `agent_type` native (`skills=[{skill_id}]`) → caminho plughub-native existente resolve o flow via `get_skill_flow`, sem depender de `agent_type.skills`.

**Bug corrigido** (`routing-engine/models.py`): `AgentInstance` não declarava `skill_id`/`flow_id`; o `mark_busy` revalidava via Pydantic e **descartava** esses campos ao alocar a instância, apagando o `skill_id` da instância busy. Campos declarados no modelo para sobreviver ao round-trip `model_validate → model_dump`.

**Fase 3c (fundação)** — migração de pools reais + precedência:
- **Precedência invertida (deploy vence)** (`instance_bootstrap.py`): `_build_desired_state` recebe `deployed_pool_ids` e remove esses pools dos `pools` de cada agent_type (se nenhum restar, ignora o agent_type). Pool com slot `current` é provisionado exclusivamente pelo builder deploy-driven. Migrar um pool = só configurar+promover seu slot, sem deletar agent_type.
- **Síntese centralizada** (`main.py get_agent_type`): no 404, se o `agent_type_id` for uma skill com flow, sintetiza um native agent_type (`_synthesize_agent_type_from_skill`). Cobre **todos** os caminhos de ativação (routed, conferência, queue, restore) num ponto único — removido o bloco ad-hoc do `process_routed`.
- **RegistrySyncer** (`registry_syncer.py`): `_sync_deploy_slots` provisiona `PoolSkillSlot.current` a partir dos agent_types IA do YAML (idempotente). **Opt-in** via `REGISTRY_SYNC_DEPLOY_SLOTS` (default `false`) — a síntese ainda não replica config de especialista (`mention_commands`/`role`), então auto-migração em massa de pools @mention/conferência fica gated.

**Resultado verificado**: (1) pool `teste_demo` 100% Config+Deploy (skill `skill_triagem_v2`, 15 concurrent), **sem agent_type** → 15 instâncias → triagem IVR end-to-end. (2) pool **real** `demo_ia` migrado por deploy (slot promovido) → instâncias `agente_demo_ia_v1-*` substituídas por `demo_ia-*` → flow roda via síntese centralizada, sem regressão.

**Pendente 3c/3d**: migrar demais pools de entrada/jornada; enriquecer síntese p/ especialistas (mention_commands/role) antes de migrar @mention/conferência; aposentar `infra/registry/*.yaml`; 3d remove `agent_type` do schema/routing/segments + hack `_applyMaxConcurrentSessions`.

---

## Arc 19 — Session lifecycle + analytics status fix (2026-05-29)

Quatro bugs corrigidos que impediam o status de sessões webhook de fechar corretamente no Analytics:

**Bug 1 — Watchdog fechava sessões webhook suspensas** (`main.py _sweep_orphaned_sessions`):
O `session_watchdog` varria `session:*:meta` e fechava qualquer sessão sem `ws_alive`. Sessões webhook nunca têm WebSocket, então todas caíam no sweep — incluindo sessões legitimamente suspensas aguardando resume_token. Fix: skip completo de `channel_type: webhook` no watchdog (o lifecycle delas é gerenciado por suspend/resume/complete, não por WebSocket keepalive).

**Bug 2 — Resume não limpava os dois NX guards** (`main.py _handle_webhook_session_resumed`):
`_close_contact_layer` usa `contact_close_fired` como guard NX; `_destroy_conference` usa `close_fired`. O fix anterior deletava só `close_fired`, deixando `contact_close_fired` setado pelo watchdog. Quando o workflow completava, `_close_contact_layer` encontrava o guard setado e pulava o publish do `contact_closed` → analytics ficava `active`. Fix: deletar ambos os guards (`contact_close_fired` + `close_fired` + `closed`) no resume.

**Bug 3 — collect step não escrevia em resume_tokens** (workaround):
O step `collect` em modo webhook não grava automaticamente o token em `{tenant}:resume_tokens` como o step `suspend` faz. Para o demo, o token é injetado manualmente via `redis-cli HSET`. Fix permanente pendente no TODO.

**Bug 4 — skill YAML volumes não montados** (`docker-compose.demo.yml`):
Mudanças em `skill_*.yaml` e `agente_*.yaml` não tinham efeito sem rebuild de imagem porque o `SKILLS_DIR` do bridge e do skill-flow-service apontavam para paths baked na imagem. Fix: volume mounts adicionados para ambos os serviços; agora `restart orchestrator-bridge` é suficiente.

**Resultado verificado**: ciclo completo portabilidade — intake → webhook trigger → suspend (aguarda operadora) → resume approved → collect (aguarda cliente) → resume input → `encerrar_sucesso` → `__complete__` → session status `closed` em Analytics/Sessions.

---

## Arc 19 Demo — Webhook workflow end-to-end fix (2026-05-29)

Três bugs corrigidos que impediam o fluxo de portabilidade Arc 19 de rodar end-to-end na demo:

**Bug 1 — `started_at` ausente no evento do WebhookAdapter** (`channel-gateway/adapters/webhook.py`):
O `WebhookAdapter.handle_trigger()` publicava `conversations.inbound` sem o campo `started_at`, que é obrigatório em `ConversationInboundEvent` do routing-engine. O `model_validate()` lançava `ValidationError` silencioso e o evento era descartado com WARNING "Unrecognised inbound event". O routing engine nunca roteava a sessão webhook.
Fix: adicionado `"started_at": now_str` ao payload do trigger. Idem no `handle_resume()` para o path de retomada.

**Bug 2 — Pool webhook ausente no `tenant_demo.yaml`** (`infra/registry/tenant_demo.yaml`):
Não havia pool com `channel_types: [webhook]` e `skill_id: skill_portabilidade_demo_v1`, então o routing engine não encontrava candidato para a sessão criada pelo trigger. Fix: adicionado pool `portabilidade_processo_ia` e agente `agente_portabilidade_processo_v1` com 20 instâncias.

**Bug 3 — YAML da skill lendo `@ctx.session.review_decision` em vez de usar `on_reject`** (`skill-flow-engine/skills/skill_portabilidade_demo_v1.yaml`):
O step `solicitar_operadora` (suspend) mandava para um `choice` que lia `@ctx.session.review_decision` do ContextStore — que nunca era escrito pelo fluxo normal. Fix: removido o choice step redundante; o suspend agora usa `on_resume/on_reject` nativos para rotear approved → `notificar_aprovado` e rejected → `notificar_rejeitado`.

**Observabilidade** (`orchestrator-bridge/main.py`): adicionado `logger.info` no path WITH-instance-id do `agent_done` para human agents, que antes publicava silenciosamente sem log.

**Verificação**: resume_token criado após intake (suspend executou); curl de resume com `decision=approved` completou o workflow até `closed` conforme visível em Analytics/Sessions.

---

## Arc 19 Fase F — Journey entity elimination (#341–#353) (2026-05-28)

Complete removal of the Journey entity from the entire PlugHub platform. Journey was architecturally redundant — it was conceptually just a Tier-1 Workflow that invokes other workflows via the `task` step. The session trace (Arc 19 unified model) provides sufficient hierarchy visibility without a separate entity.

**@plughub/schemas**: Removed `JourneySchema`, `JourneyTypeSchema`, `JourneyEventSchema`, all `journey_*` fields from `WorkflowEventSchema`/`CollectEventSchema`/`PoolRegistrationSchema`, and `creates_journey`/`journey_type_id` from skill YAML types.

**agent-registry**: Dropped `journey_types` Prisma table; removed `authorized_journey_types` and `mentionable_journeys` columns from Pool; dropped `/v1/journey-types` CRUD REST; removed mentionable-processes endpoint; removed `mentionable_journeys` from pool registration schema.

**workflow-api**: Removed `journeys` table and all `/v1/journeys` endpoints (create/get/list/resume/link/merge/split). Removed `journey_id` FK from `workflow_instances`.

**mcp-server-plughub**: Removed 8 MCP tools: `journey_start`, `journey_link_session`, `journey_merge`, `journey_split`, `journey_list_suspended`, `journey_resume`, `journey_check_pending`, and the mentionable-processes tool used by `agent_delegate`.

**routing-engine**: Removed `session.authorized_journey_types` write to ContextStore after pool allocation; removed `mentionable_journeys` from `PoolConfig`; removed `mentionable_journeys` from pool context enrichment writes.

**skill-flow-engine + skill-flow-worker**: Removed `creates_journey` flag processing; removed `journey_id` propagation to child sessions in collect steps; removed `journey_session_linked` Kafka emit in `respond_collect`; removed `journey_id` from `WorkflowContext`.

**analytics-api**: Removed Kafka consumer for `journey.events`; dropped `journey_events` ClickHouse table; removed `/reports/journeys` endpoint and `query_journeys` function; removed `journey_type_id`/`pool_id` journey filters.

**channel-gateway**: Removed `inbound_journey_resume` check from inbound normalisation (Arc 16 Fase E); removed journey lookup by `customer_id` before session creation.

**infra YAMLs**: Removed `journey_type_id` from all skill YAMLs; removed `journey_types` and `authorized_journey_types` from all registry pool/agent YAML files.

**platform-ui** (tasks #350–#352): Removed Journey Types tab from Config/Resources; removed `authorized_journey_types` and `mentionable_journeys` fields from Pool form; replaced `MonitorJourneysPage` with redirect to `/monitor`; replaced `AnalyticsJourneysPage` (and ProcessosPage JourneysTab) with redirect to `/analise/sessions`; replaced `JourneyPanel` center-column component with null stub; removed `useMentionableProcesses` hook; removed AcoesTab "Processos" mode (`ProcessItemRow`, toggle, `mentionableProcesses`/`onStartProcess` props); removed `handleIniciarProcesso` callback from `AgentAssistPage`; removed `centralTab` Atual|Journey switcher; removed `mentionable_journeys` from `PoolInfo` type and `AgentAssistContext` pool mapping; removed `MentionableProcess` interface from `types.ts`; removed all journey/process i18n keys from both `en/agentAssist.json` and `pt-BR/agentAssist.json`.

**Arcs retired to CHANGELOG**: Arc 10 (Journey multi-session), Arc 16 (Three-Tier Business Process Orchestration), Arc 17 (JourneyType Governance). Their docs remain in `docs/arcos/` for historical reference.

---

## Arc 19 Fase E — Monitor e Analytics unificados (#366–#373) (2026-05-28)

Extends Monitor and Analytics/Sessions to fully reflect Arc 19's unified session model — webhook sessions with `suspended` status are visible alongside regular contacts.

**analytics-api** (`clickhouse.py`, `models.py`, `reports_query.py`, `reports.py`):
- Added `status TEXT DEFAULT ''` column to `analytics.sessions` via `_DDL_SESSIONS_MIGRATE_STATUS` migration (executes at startup if column absent).
- `parse_inbound` sets `status: "active"` on every new inbound session row.
- `parse_conversations_event` handles two new event types: `session_suspended` → writes `status: "suspended"`; `contact_closed` → writes `status: "closed"`.
- `report_sessions` endpoint gains optional `status` query param with special handling: `status=closed` matches rows where `status = 'closed' OR status IS NULL` (backward compat for pre-Arc-19 rows with no status).

**orchestrator-bridge** (`main.py`):
- When skill-flow writes `session:{id}:status = suspended` to Redis, bridge now also publishes `session_suspended` event to `conversations.events` Kafka topic so analytics-api indexes it.

**platform-ui — MonitorTab** (`tabs/MonitorTab.tsx`):
- `MonitorScope` extended from `'sessions' | 'processes'` to `'sessions' | 'processes' | 'events'`.
- `CHANNEL_COLORS` gains `webhook: '#6366F1'` (indigo).
- `PoolRow` shows a `webhook` badge (indigo) when `pool.channel_types?.includes('webhook')`.
- `SessionList`/`SessionRow` (service module) displays a yellow `suspended` badge when `sess.status === 'suspended'`.
- New `EventsView` component: calls `GET /reports/agent-events/summary?period=24h` with optional `category_regex` filter, polls every 30 s, renders table of category/pool/count/avg/last-seen.
- Scope toggle bar gains an **Events** button (`BarChart2` icon) wired to `EventsView`.

**platform-ui — Analytics/Sessions** (`contacts/SessionsPage.tsx`, `contacts/tabs/ListaTab.tsx`, `contacts/types.ts`):
- `webhook` added to the channel dropdown.
- `suspended` added to the status dropdown (between `active` and `closed`).
- `status` field added to `ContactFilters` and `ContactRow` types.
- `status` wired end-to-end from `SessionsPage` filter state → `contactFilters` object → `ListaTab` query params → API `?status=`.

**i18n** (`en/contacts.json`, `pt-BR/contacts.json`):
- `monitor.scope.events` — Events tab label.
- `monitor.events.*` — title, filter placeholder, refresh, loading, empty, and 5 column headers.
- `sessions.status.suspended` — Suspended / Suspenso.

---

## Arc 19 Fase D — workflow-api: proxy trigger/resume → channel-gateway, deprecate lifecycle endpoints (#364, #365) (2026-05-28)

Redirects external workflow entry points to the Arc 19 unified session model and marks all PostgreSQL-backed lifecycle endpoints as deprecated (410 Gone).

**workflow-api** (`router.py`, `config.py`):
- `Settings` gains `channel_gateway_url: str = "http://localhost:8010"` (env: `PLUGHUB_WORKFLOW_CHANNEL_GATEWAY_URL`).
- `POST /v1/workflow/trigger` now proxies to `POST {channel_gateway_url}/v1/channels/webhook/{flow_id}`. Normalises `trigger_type="manual"` → `"api"`. Forwards `tenant_id`, `customer_id`, `trigger_type`, and a merged `metadata` dict (includes `context`, `origin_session_id`, `pool_id`, `journey_id` when present). Returns the channel-gateway response body directly. Returns 502 if the gateway is unreachable or returns a non-2xx.
- `POST /v1/workflow/resume` gains a dual-path: when `tenant_id` is present (Arc 19 webhook session), proxies to `POST {channel_gateway_url}/v1/channels/webhook/resume/{token}` with `{ tenant_id, payload: { **payload, decision } }`. When `tenant_id` is absent, falls through to the existing PostgreSQL-backed legacy path (backward compat for pre-Arc19 instances).
- `ResumeRequest` adds optional field `tenant_id: str | None = None`.
- `POST /v1/workflow/instances/{id}/persist-suspend` → 410 Gone (deprecated — suspend handled by orchestrator-bridge via `persistSuspendWebhook` Redis callback).
- `POST /v1/workflow/complete`, `POST /v1/workflow/fail`, `POST /v1/workflow/instances/{id}/cancel` → 410 Gone.
- `POST /v1/workflow/collect/persist`, `POST /v1/workflow/collect/respond` → 410 Gone.
- GET endpoints (`/v1/workflow/instances`, `/v1/workflow/instances/{id}`) kept read-only as-is for observability.

**skill-flow-engine** (`executor.ts`, `steps/suspend.ts`):
- `StepContext.persistSuspendWebhook` params extended with `business_hours?: boolean` and `calendar_id?: string` — forwarded from the `suspend` step config so the callback can compute business-hours-aware deadlines.
- `executeSuspend()` spreads `business_hours` and `calendar_id` into the `persistSuspendWebhook` call when present.

**skill-flow-service** (`packages/e2e-tests/services/skill-flow-service/src/index.ts`):
- `CALENDAR_API_URL` constant added (env: `CALENDAR_API_URL`, default `http://localhost:3700`).
- `persistSuspendWebhookFn` extended: when `params.business_hours === true && params.calendar_id` is set, calls `POST {CALENDAR_API_URL}/v1/engine/add-business-duration` with `{ tenant_id, entity_type: "calendar", entity_id: calendar_id, from_dt: now, hours: timeout_hours }`. Uses the returned `deadline` as `resume_expires_at`. Falls back to wall-clock on calendar-api error or non-2xx response. TTL for Redis EXPIRE recomputed from actual deadline (`(deadline - now) / 1000 + 3600`).

**Tests** (`packages/workflow-api/src/plughub_workflow_api/tests/test_router.py`):
- Full rewrite to reflect Arc 19 Fase D behavior.
- `TestTrigger` (6 tests): proxy URL formed correctly, `"manual"` normalised to `"api"`, non-manual trigger_type preserved, metadata fields (`context`, `origin_session_id`, `pool_id`, `journey_id`) forwarded, 422 for missing required fields, 502 on gateway error.
- `TestPersistSuspend` (2 tests): both assert 410.
- `TestResume` (7 tests): webhook path (3 tests — proxy URL, payload with decision, 502 on error) and legacy path (4 tests — existing PostgreSQL logic, exercised when `tenant_id` is absent).
- `TestDeprecated` (5 tests): complete, fail, cancel, collect/persist, collect/respond all assert 410.

---

## Arc 19 Fase C — orchestrator-bridge: skill-flow instances as webhook pool native agents (#362, #363) (2026-05-28)

Wires the orchestrator-bridge and skill-flow-service so that webhook pool sessions are started and resumed directly through the bridge's native-agent activation path, eliminating the separate workflow-api lifecycle for webhook channels.

**skill-flow-service** (`packages/e2e-tests/services/skill-flow-service/src/index.ts`):
- `/execute` endpoint now accepts two new optional body fields: `webhook_pool: boolean` and `resume_context: { step_id, decision, payload }`.
- When `webhook_pool=true`, a `persistSuspendWebhook` callback is wired into `SkillFlowEngine` config. The callback: extends TTL of all four session Redis keys (`stream`, `ctx`, `pipeline`, `status`) by `ceil(timeout_hours × 3600) + 3600s`; writes the resume token to `{tenant}:resume_tokens` hash with the same TTL.
- When `resume_context` is provided, it is passed as `resumeContext` to `engine.run()` so the suspended `suspend` step follows its `on_resume` / `on_reject` / `on_timeout` path.
- `dedicatedRedis` is declared before the `persistSuspendWebhook` closure so the callback captures a stable reference while `finally { dedicatedRedis.disconnect() }` still runs correctly.

**orchestrator-bridge** (`packages/orchestrator-bridge/src/plughub_orchestrator_bridge/main.py`):
- `activate_native_agent()` gains two keyword parameters: `webhook_pool: bool = False` and `resume_context: dict | None = None`. Both are forwarded in the skill-flow-service POST payload when set.
- `process_routed()` detects webhook pools by reading `{tenant_id}:pool_config:{pool_id}` from Redis and checking `channel_types` contains `"webhook"`. On detection and successful instance allocation, writes session meta (`session:{session_id}:meta`) with `NX` guard and TTL (`_stl()`) — contains `agent_type_id`, `pool_id`, `customer_id`, `instance_id` for use by the resume handler. Passes `webhook_pool=True` to `activate_native_agent`.
- New `_handle_webhook_session_resumed()` helper: reads `session:{session_id}:meta`, fetches agent skills from Agent Registry, calls `activate_native_agent` with `webhook_pool=True` and `resume_context`. Terminal outcome (anything except `"suspended"`) triggers `_mark_contact_ended` + `_trigger_contact_close`. After execution (terminal or suspended), restores the instance snapshot to `{tenant}:instance:{instance_id}` (status=ready, current_sessions decremented) and re-adds it to the pool set. Publishes `agent_ready` + `agent_done` lifecycle events on `TOPIC_LIFECYCLE` for routing-engine capacity tracking.
- `process_inbound()` gains a third parameter `http: aiohttp.ClientSession | None = None`. A new early-return branch at the top detects `event_type == "session_resumed"` and dispatches to `_handle_webhook_session_resumed`. If `http` is None a WARNING is logged and the event is skipped. The function docstring is updated to document four (not three) event types.
- `_dispatch_once()` updated to pass `http=http` to `process_inbound`.

**Tests** (`packages/orchestrator-bridge/src/plughub_orchestrator_bridge/tests/test_webhook_bridge.py`):
- 17 pytest-asyncio tests covering `_handle_webhook_session_resumed` guard paths (missing session_id, tenant_id, no meta, no agent_type_id), happy path (activate called with correct webhook_pool + resume_context), terminal vs suspended outcome (contact close gated), instance restore (snapshot written + pool sadd), Kafka lifecycle events (agent_ready + agent_done published, skipped when producer is None), and `process_inbound` routing (session_resumed → handler, session_resumed + http=None → warning, customer msg → no handler, mention_routing → process_mention_routing, no-author event → skipped).

---

## Arc 19 Fase B — stream events session_suspended / session_resumed (#359, #360, #361) (2026-05-28)

Implements the canonical stream events for webhook session lifecycle transitions. After this phase, consumers (analytics-api, Monitor) can observe suspension and resumption transitions directly from the session stream, independently of Kafka routing events.

**@plughub/schemas** (`stream.ts`):
- `StreamEventTypeSchema` extended with `"session_suspended"` and `"session_resumed"`.
- `SessionSuspendedPayloadSchema` — `{ step_id, resume_token, resume_expires_at }`. Published when skill-flow engine returns `outcome: "suspended"` for a webhook pool session.
- `SessionResumedPayloadSchema` — `{ step_id, resume_token, payload? }`. Published when a valid resume token arrives at the webhook adapter.
- Both schemas exported in `StreamPayloads` map for typed deserialization by consumers.

**skill-flow-engine** (`executor.ts`, `steps/suspend.ts`):
- `StepContext` gains optional `persistSuspendWebhook` callback — Redis-only TTL extension path for webhook sessions (no PostgreSQL). Signature: `(params: { step_id, resume_token, timeout_hours }) => Promise<{ resume_expires_at: string }>`.
- `executeSuspend()` in `suspend.ts` implements the Arc 19 Redis-only path: when `ctx.persistSuspendWebhook` is set (and `ctx.persistSuspend` is not), calls it to extend all session Redis key TTLs and register the resume token in `{tenant}:resume_tokens` hash.
- Two-phase idempotency sentinel (`"suspending"` → `"suspended"`) prevents token regeneration on crash + retry. Token is written to `pipeline_state.results` under `{step.id}:__resume_token__` before the persist call so a crashed process reuses the same token.
- Resume path: detects `ctx.resumeContext.step_id === step.id`, follows `on_resume` / `on_reject` / `on_timeout` depending on `decision`. Resume decision persisted in `results[decisionKey]` for replay safety.

**orchestrator-bridge** (`main.py`):
- `"suspended"` added to `_escalation_outcomes` tuple — prevents `_trigger_contact_close()` from firing when the skill-flow engine returns `outcome: "suspended"`. The session must remain alive (TTL extended in Redis) awaiting a resume signal.
- `session_suspended` stream event written to `session:{session_id}:stream` via `redis_client.xadd` when `_ai_outcome == "suspended" and not conference_id`. Fields: `event_id`, `type: session_suspended`, `author_id/role`, `visibility: agents_only`, `segment_id`, `payload { step_id, resume_token, resume_expires_at }`.
- `resume_token` and `resume_expires_at` extracted from `pipeline_state.results` by scanning for keys ending in `:__resume_token__` and `:__expires_at__` — the engine writes these keys in `suspend.ts`.
- Status key `{tenant_id}:session:{session_id}:status` set to `"suspended"` via `setex` so `WebhookAdapter.get_status()` reflects the transition immediately.
- Stream write failure is non-fatal — logged as WARNING, resume flow unblocked.

**channel-gateway** (`adapters/webhook.py`):
- `handle_resume()` writes `session_resumed` to `session:{session_id}:stream` **before** publishing `conversations.inbound` to Kafka — ensures analytics-api and Monitor consumers see the transition before re-allocation fires.
- Status key reset to `"active"` via `redis.set(..., keepttl=True)` — preserves the existing TTL set by `persistSuspendWebhook` without resetting it.
- Stream write failure and status key failure are non-fatal — logged as WARNING, Kafka event and token deletion proceed normally.
- `event.timestamp` unified: `now_iso` computed once before the stream write and reused in the Kafka event for temporal consistency.

**Tests** (`packages/channel-gateway/src/plughub_channel_gateway/tests/test_webhook_adapter.py`):
- Full unit test suite for `WebhookAdapter` covering Fase A (trigger, resume token resolution, status query, no-op outbound) and Fase B (stream write, status key reset, non-fatal failure paths, ordering guarantee xadd-before-kafka).

→ [`docs/arcos/arc19-unified-session-model.md`](docs/arcos/arc19-unified-session-model.md)

---

## Arc 19 Fase A — Canal webhook + adapter (#354, #355, #356, #357, #358) (2026-05-28)

Foundation layer for the unified session model. Introduces `channel_type: webhook` as a first-class channel so that workflow-style pools are routed by the same path as any other contact channel.

**@plughub/schemas** (`common.ts`, `agent-registry.ts`):
- `ChannelSchema` enum extended with `"webhook"`.
- `SessionStatusSchema` extended with `"suspended"` (needed by Fase B suspend executor; added here to keep schema aligned with spec from the start).
- `PoolRegistrationSchema` gains `webhook_skill_id: string | null` (the skill endpoint / "DIN" of the pool — required when `channel_types` includes `"webhook"`) and `max_concurrent_sessions: number | null` (capacity ceiling for webhook pools; informational for human/AI pools).

**channel-gateway** (`adapters/webhook.py`, `main.py`):
- New `WebhookAdapter(ChannelAdapter)` following the same pattern as `WebchatAdapter`, `WhatsAppAdapter`, etc.
- `handle_trigger(skill_id, body)` — validates tenant, looks up pool config by `webhook_skill_id`, builds and publishes `conversations.inbound` event, returns `session_id`.
- `handle_resume(resume_token, body)` — resolves `{tenant}:resume_tokens` hash (token → `session_id:step_id:expires_at`), wakes the suspended session, returns `session_id`. Resume token logic is wired for Fase B executor.
- `get_status(session_id)` — returns current session status (`active | suspended | closed`) from Redis.
- No-op outbound methods (`deliver_outbound`, `send_typing`) — webhook sessions never receive messages directly; they use `notify` steps targeting child sessions via `collect`.
- Three HTTP endpoints registered in `main.py`: `POST /v1/channels/webhook/{skill_id}`, `POST /v1/channels/webhook/resume/{resume_token}`, `GET /v1/channels/webhook/{session_id}/status`.

**routing-engine** (`models.py`, `kafka_listener.py`, `registry.py`, `router.py`):
- `ConversationInboundEvent.channel` Literal extended with `"webhook"` — events produced by the WebhookAdapter now validate correctly.
- `PoolConfig` gains `webhook_skill_id: str | None` and `max_concurrent_sessions: int | None` — populated from `pool.registered` / `pool.updated` Kafka events via `kafka_listener._handle_pool_event()`.
- `InstanceRegistry.write_pool_snapshot()` updated with new optional parameters. For webhook pools (`"webhook" in channel_types`), when `max_concurrent_sessions` is set, `available` and `total_instances` in the snapshot are derived from that ceiling (not from logged-in agent instances — there are none at Fase A). Active count (`busy`) still drives `available = max(0, max_concurrent_sessions - busy)`.
- `webhook_skill_id` and `max_concurrent_sessions` written to the pool snapshot dict so the Monitor can display configured capacity for webhook pools.
- `refresh_pool_snapshot()` preserves webhook fields from the existing snapshot when refreshing on heartbeat.
- All three `write_pool_snapshot()` call sites updated: `router._write_snapshot()`, `kafka_listener._refresh_pool_snapshots()`, and `registry.refresh_pool_snapshot()`.

**agent-registry** (Prisma, `pools.ts`):
- Migration `20260528000000_arc19_webhook_pool_fields`: `ALTER TABLE "pools" ADD COLUMN "webhook_skill_id" TEXT, ADD COLUMN "max_concurrent_sessions" INTEGER`.
- `model Pool` in `schema.prisma` updated with both new nullable columns.
- `POST /v1/pools` and `PUT /v1/pools/:pool_id` persist `webhook_skill_id` and `max_concurrent_sessions`.
- `pool.registered` / `pool.updated` Kafka events already carry these fields via `_formatPool()` — no changes needed to the event publisher.

→ [`docs/arcos/arc19-unified-session-model.md`](docs/arcos/arc19-unified-session-model.md)

---

## max_concurrent_sessions — full stack (#274, #276, #277, #278, #281) (2026-05-26)

Complete implementation of the `max_concurrent_sessions` limit across all layers. The feature was already partially scaffolded (#279 DB column, #280 mcp-server JWT read) — this entry documents that all remaining layers were verified complete and the tracker closed.

**auth-api** (`router.py`):
- `GET /me` returns `max_concurrent_sessions` from JWT claims (set at login from `auth.users` column). Live DB access available via admin `GET /users/{user_id}`. No separate profile endpoint needed — `GET /me` satisfies #274.

**orchestrator-bridge** (`main.py`, lines 2304–2320):
- `agent_ready` Kafka event includes `max_concurrent_sessions` read from instance snapshot key in Redis (#276).

**mcp-server-plughub** (`tools/runtime.ts`, lines 267–281):
- `agent_ready` Kafka event for human agents reads `max_concurrent_sessions` from Redis instance hash and publishes it in the lifecycle event (#277, #278).

**platform-ui** (`modules/access/AccessPage.tsx`, lines 243–272):
- User edit form has `maxConcurrentSessions` number input, wired to both `CreateUserInput` and `UpdateUserInput` payloads (#278, #281).

---

## Arc 17 — JourneyType Governance — Backend completo (#298, #299, #300) (2026-05-26)

All three remaining Arc 17 backend tasks were verified as already fully implemented (tracker was stale).

**#300 — workflow-api** (`db.py`, `journey_router.py`):
- `journey_type_id TEXT` column added via idempotent `ALTER TABLE` migration.
- `db_create_journey` and `db_create_journey_for_instance` accept and insert `journey_type_id`.
- `_row_to_journey` serializes it; all GET endpoints return it.
- `JourneyCreateRequest` and `JourneyFromInstanceRequest` Pydantic models include `journey_type_id: str | None`.

**#298 — routing-engine** (`main.py` `_write_pool_context()`, `models.py` `PoolConfig`):
- `PoolConfig.authorized_journey_types: list[str]` field populated from `pool.registered` / `pool.updated` Kafka events.
- `_write_pool_context()` writes `session.authorized_journey_types` to ContextStore (always, even as `[]`; confidence 1.0, visibility `agents_only`).

**#299 — mcp-server-plughub** (`tools/journey.ts` `journey_start`):
- Reads `session.authorized_journey_types` from ContextStore via Redis.
- If the list is present and `journey_type_id` is not in it → returns `JOURNEY_TYPE_NOT_AUTHORIZED` error with instructions to configure the pool.
- If the key is absent entirely → returns same error (pool not configured).
- Redis errors are logged and swallowed (fail-open to avoid blocking legitimate calls when ContextStore is temporarily unavailable).

---

## Arc 17 — Skill YAML validator: creates_journey requires journey_type_id (#301) (2026-05-26)

Three-layer defense ensuring a skill YAML with `creates_journey: true` always carries a non-empty `journey_type_id`. Also fixes a latent bug where `creates_journey` / `journey_type_id` were invisible to the engine-runner because `flow_definition` only stored the nested `flow` sub-object.

**agent-registry** (`validators/skill.ts`):
- New `validateJourneyType(rawBody: unknown): string[]` — operates on raw `req.body` before Zod parsing (Zod strips unknown root-level fields like `creates_journey`). Returns a descriptive 422 error when `creates_journey: true` without a valid `journey_type_id`.
- Both POST and PUT handlers in `routes/skills.ts` now invoke this validator before `CreateSkillSchema.parse()`, returning `{ error: "invalid_journey_type", details: [...] }` on failure.

**workflow-api** (`router.py`, `_resolve_flow_definition`):
- Bug fix: previously only stored `skill["flow"]` (the `SkillFlow` sub-object) as `flow_definition`. Root-level flags `creates_journey` and `journey_type_id` were never visible to the engine-runner, silently breaking Arc 10 Phase B auto-journey creation.
- Fix: merges `creates_journey` and `journey_type_id` from the skill root into the `flow_def` dict before storing as `flow_definition` in instance metadata.

**skill-flow-worker** (`engine-runner.ts`):
- New runtime guard: if `flowDefinition['creates_journey'] === true` and `flowDefinition['journey_type_id']` is absent, calls `workflowClient.fail(instance.id, ...)` immediately before any execution begins. Prevents a journey with `type: none` from being silently created.

---

## Arc 17 — Monitor heatmap: journey counts per pool (#314) (2026-05-26)

**analytics-api**:
- `query.py`: new `get_pool_journey_counts(client, database, tenant_id)` — queries `journey_events` using `argMax(status, event_time)` to reconstruct current journey state per pool; returns `{ pool_id, active_journeys, suspended_journeys }` for pools with at least one active or suspended journey.
- `dashboard.py`: new `GET /dashboard/pool-journeys?tenant_id=...` endpoint — same auth pattern as `/dashboard/pool-sla`; returns empty list when ClickHouse unavailable.

**platform-ui**:
- `types.ts`: new `PoolJourneyEntry` interface; `PoolView` extended with `active_journeys: number` and `suspended_journeys: number` (default 0).
- `api/hooks.ts`: new `usePoolJourneys(tenantId, intervalMs=30_000)` hook; `usePoolViews` now calls it and merges journey counts into each `PoolView` via `journeyMap`.
- `components/PoolTile.tsx`: purple `◈ N proc` badge in the top-left corner when `active_journeys + suspended_journeys > 0`; tooltip shows breakdown.

---

## Arc 17 — Dashboard journey cards: journey_type_id + pool_id filters (2026-05-26)

**analytics-api** — 4 journey display formatters (`fmt_journey_active_count`, `fmt_journey_resolution_rate`, `fmt_journey_funnel`, `fmt_journey_median_duration`) and their 4 `_fetch_*_sync` helpers now accept `journey_type_id` and `pool_id` as optional filters applied as WHERE conditions. All 4 endpoints in `display.py` expose these as Query params.

**platform-ui** — `catalog.ts`: all 4 journey display cards (`journey-active-count`, `journey-resolution-rate`, `journey-funnel`, `journey-median-duration`) now declare `journey_type_id` and `pool_id` as `configurable_params`. `en/dashboards.json` and `pt-BR/dashboards.json`: added `journey_type_id` param label/placeholder to the shared `params` section.

---

## Arc 17 — JourneyType Governance — UI Layer (2026-05-26)

Full implementation of Arc 17 — schemas, agent-registry, analytics-api, and platform-UI. Backend tasks #298–301 (routing-engine, mcp-server, workflow-api, skill YAML) deferred to next iteration.

**@plughub/schemas**: `JourneyTypeSchema` + `CreateJourneyTypeInputSchema` + `UpdateJourneyTypeInputSchema` added to `packages/schemas/src/journey-type.ts`.

**agent-registry**: Prisma migration + CRUD REST (`GET/POST/PATCH/DELETE /v1/journey-types`). `authorized_journey_types: String[]` field added to Pool schema — create and update routes persist it.

**analytics-api** (#302):
- `journey_events` ClickHouse table extended with `journey_type_id Nullable(String)` + `pool_id Nullable(String)` columns; `_DDL_JOURNEY_EVENTS_MIGRATE_ARC17` migration applied at startup
- `parse_journey_event()` in `models.py` extracts `journey_type_id` + `pool_id` from Kafka payload
- `_journey_event_row()` in `clickhouse.py` writes both fields per event
- `_fetch_journeys()` in `reports_query.py` applies `journey_type_id` and `pool_id` as `base_where` conditions on both the journey list query **and** the KPI aggregation subquery — filters are fully scoped end-to-end

**platform-ui**:
- `JourneyType`, `CreateJourneyTypeInput`, `UpdateJourneyTypeInput` in `src/types/index.ts`; `authorized_journey_types` added to `Pool`, `CreatePoolInput`, `UpdatePoolInput`
- `listJourneyTypes`, `createJourneyType`, `updateJourneyType`, `deleteJourneyType` in `src/api/registry.ts`
- Config/Resources: new "Journey Types" tab with inline CRUD table (key, description, SLA, edit/delete)
- Config/Resources: Pool drawer — `authorized_journey_types` multi-select checkbox list, loads registered types on open
- `hooks.ts`: `Journey.journey_type_id` + `Journey.pool_id` fields; `useJourneys` extended with `journeyTypeId` (5th param) + `poolId` (6th param) mapped to `/reports/journeys` query params
- ProcessosPage JourneysTab: L1 journey-type chip row (All + one per registered type), pool dropdown filter, `journey_type_id` badge in list rows and detail panel, `pool_id` in detail panel
- i18n: `journeyTypes.*` + `pools.authorizedJourneyTypes.*` + `tabs.journeyTypes` in `configRecursos.json` (en + pt-BR); `processes.journeys.filters.journeyType/pool/allPools`, `allTypes`, `detail.journeyType/pool` in `contacts.json` (en + pt-BR)

→ [`docs/arcos/arc17-journey-types.md`](docs/arcos/arc17-journey-types.md)

---

## Fix: primeira pergunta do wrap-up pulada após desconexão do cliente (2026-05-25)

**Sintoma**: no cenário webchat F5 (cliente desconecta), o agente humano era notificado e o wrap-up era acionado; o agente via a primeira pergunta (`menu text`) mas não conseguia respondê-la — o fluxo avançava automaticamente para a segunda pergunta. Todas as perguntas seguintes se comportavam normalmente.

**Root cause** — `packages/orchestrator-bridge/src/plughub_orchestrator_bridge/main.py`, `process_contact_event()`:

Quando o cliente desconectava e **nenhum agente estava num `menu` step** (situação normal — o agente humano estava apenas atendendo), a função `hgetall("menu:waiting:{session_id}")` retornava vazio, ativando o fallback legado:

```python
if _no_menu_entries:
    # No menu:waiting hash at all — push 1 for legacy agents
    n_waiting = 1
```

Esse bloco empurrava 1 valor para `session:closed:{sessionId}` com TTL de 300s. Como nenhum BLPOP estava ativo no momento, o valor permanecia na lista. O agente de wrap-up (hook `on_human_end`) era disparado 5–15s depois; quando seu primeiro step `menu` executava `BLPOP([menu:result:{sid}:{iid}, session:closed:{sid}])`, consumia imediatamente o valor pendente em `session:closed`, retornando pelo branch `on_disconnect` → `on_failure`. Como o YAML do wrap-up tem `on_failure` do primeiro step apontando para o segundo step, o fluxo "pulava" a primeira pergunta.

**Fix**: removido o bloco legado (linhas 3371–3374):
```python
# REMOVIDO:
if _no_menu_entries:
    # No menu:waiting hash at all — push 1 for legacy agents
    # that use the list without the hash registration.
    n_waiting = 1
```

Quando não há `menu:waiting` hash (nenhum agente bloqueado em BLPOP), `n_waiting` permanece 0 e nenhum valor é enviado para `session:closed`. O primeiro step do wrap-up agora aguarda normalmente pela resposta do agente.

**Impacto zero em outros cenários**: agentes em BLPOP ativo continuam recebendo o sinal via a lógica de hash `menu:waiting` + verificação de `active_instance` key, que foi implementada exatamente para substituir o fallback legado.

---

## Fix: customer responses to AI menu steps missing from Analytics/Sessions transcript (2026-05-21)

**Root cause**: `process_inbound()` in orchestrator-bridge wrote customer menu/form submissions to the canonical stream (`session:{id}:stream`) only when a human agent was active. For native AI-agent sessions (NPS, auth_form hooks, Skill Flow menu steps), responses were routed to `menu:result:*` LPUSH keys and to Kafka analytics, but never written to the stream. The analytics-api session stream SSE endpoint reads from Redis, so the customer's reply was invisible in the Analytics/Sessions segment transcript.

**Fix** — `packages/orchestrator-bridge/src/plughub_orchestrator_bridge/main.py`:
- Inside `if waiting_hash:` block (after routing to AI agents via `menu:result` LPUSH), added `if not is_human:` stream write that mirrors the human branch's masking logic:
  - `any_masked = True` → `visibility: "agents_only"`, content `"[entrada mascarada]"`
  - `all_masked_fields` (field-level) → `visibility: "all"`, content `"[Formulário: {redacted JSON}]"`
  - else → `visibility: "all"`, content `"[Seleção: <reply_text>]"` (for button/list) or raw text (for text type)
- The `xadd` writes `event_id`, `type: "message"`, `author_id`, `author_role: "customer"`, `visibility`, `content` (JSON `{"text": ...}`) — all fields expected by `_parse_entry()` in analytics-api.
- Double-write guard: skipped when `is_human` (human branch already wrote).

**Result**: NPS scores, form submissions, and menu responses now appear in the segment transcript for AI-only sessions. The `entryBelongsToSpecialist` filter in `SessionTranscript.tsx` correctly includes them because `segmentTalksToCustomers()` finds the NPS/auth_form agent's outbound entries and returns `true`.

---

## Arc 16 Fases D + E — Channel Capability Negotiation + Inbound Journey Resume (2026-05-21)

Implementação das Fases D e E do Arc 16 — Three-Tier Business Process Orchestration.

### Fase D — Channel Capability Negotiation

O step `collect` passa a aceitar `requires: [text|audio|video|file_upload|masked_input|rich_menu]` em vez de `channel` explícito. O Channel Gateway seleciona o canal outbound baseado na matriz de capacidades e no contexto de Journey.

**`packages/channel-gateway/src/plughub_channel_gateway/`**
- `channel_capability_registry.py` (NOVO) — módulo central de Arc 16 Phase D:
  - `CHANNEL_CAPABILITIES` — matriz estática de capacidades por canal (whatsapp, sms, email, voice, webchat, webrtc)
  - `_CHANNEL_PRIORITY` — ordem de preferência quando múltiplos canais satisfazem os requisitos
  - `channel_satisfies(channel, requires)` — verifica se um canal suporta todos os capabilities solicitados
  - `select_channel(available, requires, preferred)` — algoritmo 2-step: honra preferência, depois escolhe por prioridade
  - `read_journey_channel_context(redis, tenant_id, journey_id)` — lê `journey.available_channels` + `journey.canal_preferido` do ContextStore de Journey
  - `write_journey_channel_context(redis, tenant_id, journey_id, channel, contact_id)` — escreve `journey.available_channels`, `journey.canal_preferido`, `journey.{channel}_contact_id`; limpa `journey.pending_collect_info` quando resposta chega; TTL 30 dias NX
  - `get_journey_contact_id(redis, tenant_id, journey_id, channel)` — recupera o contact_id do cliente para um canal na journey
  - `write_journey_pending_collect(redis, tenant_id, journey_id, requires, channel, contact_id)` — grava `journey.pending_collect_info` para que `journey_check_pending` MCP tool consiga descobrir journeys com collect pendente
- `main.py` — consumer `collect.events` estendido de voice-only para todos os canais:
  - `_collect_events_consumer()` usa group_id `-collect` e filtra apenas `collect.requested`
  - `_dispatch_collect_event()` — routing em 2 passos: (1) canal explícito → adapter direto; (2) sem canal → `read_journey_channel_context` + `select_channel` + `get_journey_contact_id`; grava `write_journey_pending_collect` após despacho
- `adapters/whatsapp.py` — `handle_collect_event()` envia prompt via provider + grava `pending_collect` Redis (TTL 30 min); inbound verifica `pending_collect`, chama `write_journey_channel_context`, enriquece evento com `collect_token`/`journey_id`/`response_text`
- `adapters/sms.py` — idem WhatsApp (via `_send_text_to_contact`)
- `adapters/email.py` — idem para email (via `deliver_text`); inbound chama `write_journey_channel_context` antes de publicar

**`packages/schemas/src/`**
- `skill.ts` — `CollectStepSchema`: `channel` agora `z.string().optional()`, campo `requires: z.array(ChannelCapabilitySchema).optional()` adicionado; `ChannelCapabilitySchema` (`"text"|"audio"|"video"|"file_upload"|"masked_input"|"rich_menu"`) exportado

### Fase E — Inbound Journey Resume via Agente de Pool (agent-side opt-in)

Abordagem: Channel Gateway e Routing Engine não são modificados. O agente AI do pool detecta e oferece a retomada como parte natural da conversa.

**`packages/mcp-server-plughub/src/tools/journey.ts`**
- `journey_check_pending(customer_id, channel?, limit?)` — nova MCP tool (Arc 16 Phase E):
  - Consulta `GET /v1/journeys?customer_id=...&status=active` no workflow-api
  - Para cada journey, lê `journey.pending_collect_info` do Redis (ContextStore de Journey)
  - Filtra por capacidade de canal se `channel` fornecido (via `_channelSatisfies` — mirror de `CHANNEL_CAPABILITIES`)
  - Retorna `[{ journey_id, skill_id, pool_id, pending_channel, pending_contact_id, requires[], dispatched_at }]`
  - Auditada via McpInterceptor; permissão ABAC `journey.read`
- `_CHANNEL_CAPABILITIES` + `_channelSatisfies()` — helper local em sync com `channel_capability_registry.py`
- `JourneyCheckPendingInputSchema` — schema de validação Zod

**`packages/agent-registry/`**
- `prisma/schema.prisma` — `inbound_journey_resume Boolean @default(false)` adicionado ao model `Pool`
- `prisma/migrations/20260521000000_add_pool_inbound_journey_resume/migration.sql` — migration correspondente
- `src/routes/pools.ts` — create e update incluem `inbound_journey_resume`

**`packages/schemas/src/agent-registry.ts`**
- `PoolRegistrationSchema` — `inbound_journey_resume: z.boolean().default(false).optional()` (doc explica que é flag informacional para o skill author; routing engine não o lê)

**`packages/platform-ui/`**
- `modules/config-recursos/PoolsPage.tsx` — toggle "Enable Inbound Journey Resume" no formulário de pool (checkbox com label + hint); estado `formData.inbound_journey_resume`; incluído no payload de criação/edição
- `i18n/locales/en/configRecursos.json` — `pools.inboundJourneyResume.label/hint`
- `i18n/locales/pt-BR/configRecursos.json` — `pools.inboundJourneyResume.label/hint`

---

## Arc 16 Fases B + C — Journey Public API + MCP Tools Tier 1 Poller (2026-05-21)

Implementação das Fases B e C do Arc 16.

### Fase B — Journey Public API Surface

**`packages/workflow-api/`** — `journey_router.py`: `GET /v1/journeys` aceita `pool_id` como filtro (Arc 16 Tier 1 poller); `POST /v1/journeys/{id}/resume` encapsula `resume_token` interno — callers só passam `context` + `decision`; `db.py`: `db_list_journeys` suporta `pool_id`; migration `20260521000001_add_journey_pool_id` adiciona coluna `pool_id` em `workflow.journeys` com índice `(tenant_id, pool_id, status)`

**`packages/schemas/src/journey.ts`** — `JourneySchema` com `pool_id: z.string().nullable()`; novos schemas `JourneyListSuspendedInputSchema`, `JourneyListSuspendedOutputSchema`, `JourneyResumeInputSchema`, `JourneyResumeOutputSchema`

### Fase C — MCP Tools Tier 1 Poller

**`packages/mcp-server-plughub/src/tools/journey.ts`** — tools `journey_list_suspended(pool_id, skill_id?, limit?)` e `journey_resume(journey_id, context?, decision)` registrados em `registerJourneyTools`

**`packages/auth-api/`** — módulo `workflows` no `modules.yaml` com campos `journey.resume` e `journey.read`; `PermissionChecker` valida em `journey_list_suspended` e `journey_resume`

---

## Arc 16 Fase A — Journey ContextStore Namespace (2026-05-21)

Implementação da Fase A do Arc 16 — Three-Tier Business Process Orchestration. Introduz o namespace `journey.*` no ContextStore: um Redis hash compartilhado entre todas as sessões de uma mesma Journey, resolvendo o problema de dados coletados em sessões `collect` serem invisíveis para o Business Workflow do Tier 1.

### Contrato do namespace
Redis key: `{tenantId}:ctx:journey:{journeyId}`. Cada field = tag name → JSON `{value, confidence, source, visibility, updated_at}`. TTL 30 dias, renovado em toda escrita. `@ctx.journey.*` lê deste hash; `context_tags.outputs` com prefixo `journey.*` redireciona escrita para o hash journey em vez do hash de sessão.

### Arquivos modificados

**@plughub/schemas** (`packages/schemas/src/`)
- `workflow.ts` — `journey_id: z.string().uuid().optional()` adicionado a `WorkflowTimedOutSchema`, `WorkflowFailedSchema`, `WorkflowCancelledSchema` (os 4 anteriores já estavam)
- `journey.ts` — `pool_id: z.string().nullable()` + `failure_reason: z.string().nullable()` em `JourneySchema`; novos Arc 16 schemas: `JourneyListSuspendedInputSchema`, `JourneyListSuspendedOutputSchema`, `JourneyResumeInputSchema`, `JourneyResumeOutputSchema`, `JourneyCheckPendingInputSchema`, `JourneyCheckPendingOutputSchema`
- `index.ts` — exporta todos os novos schemas de Journey + `JourneySplitInputSchema`/`OutputSchema` que faltavam

**workflow-api** (`packages/workflow-api/src/plughub_workflow_api/`)
- `kafka_emitter.py` — todos os 7 `emit_*` aceitam `journey_id: str | None = None` e o incluem no payload condicionalmente
- `router.py` — todos os call sites de `emit_*` passam `journey_id=instance.get("journey_id")`
- `timeout_job.py` — `emit_timed_out` e `emit_resumed` (collect timeout) passam `journey_id=instance.get("journey_id")`

**skill-flow-engine** (`packages/skill-flow-engine/src/`)
- `executor.ts` — `journeyId?: string` adicionado a `StepContext` com doc Arc 16
- `engine.ts` — `journeyId?: string` em `SkillFlowEngineConfig` e parâmetros `run()`/`_execute()`/`_buildContext()`; propagado ao `StepContext`
- `interpolate.ts` — `resolveCtxRef()` detecta `tag.startsWith("journey.")` e redireciona para `contextStore.getValue("journey:" + ctx.journeyId, tag, customerId)`
- `context-accumulator-util.ts` — `extractOutputsToCtx()` aceita `journeyId?: string`; escreve em `"journey:" + journeyId` quando tag começa com `journey.`
- `steps/invoke.ts` — passa `ctx.journeyId` no call site de `extractOutputsToCtx`
- `steps/reason.ts` — passa `ctx.journeyId` no call site de `extractOutputsToCtx`
- `steps/resolve.ts` — passa `ctx.journeyId` no call site de `extractOutputsToCtx`

**skill-flow-worker** (`packages/skill-flow-worker/src/`)
- `engine-runner.ts` — `engine.run()` passa `journeyId: instance.journey_id` quando disponível

**ai-gateway** (`packages/ai-gateway/src/plughub_ai_gateway/`)
- `models.py` — `journey_id: str | None = None` em `InferenceRequest`
- `inference.py` — `_build_journey_context_block()` lê `{tenant}:ctx:journey:{id}` do Redis e filtra confidence < 0.3; `_prepend_journey_context()` injeta bloco no system message; `infer()` chama ambos quando `req.journey_id` está presente

**mcp-server-plughub** (`packages/mcp-server-plughub/src/`)
- `tools/journey.ts` — `JourneyDeps` recebe `redis: Redis`; novos schemas `JourneyContextGetInputSchema`, `JourneyContextSetInputSchema`; novos tools `journey_context_get` (lê hash journey do Redis com filtro de tags opcional) e `journey_context_set` (escreve tag com prefixo `journey.*` obrigatório, TTL 30d)
- `server.ts` — `journeyDeps` passa `redis` para `registerJourneyTools`

---

## Arc 15 Fase F — WebRTC: Widget do Cliente (2026-05-20)

Widget standalone single-file para o cliente no browser. Conecta ao WS `/ws/webrtc/{pool_id}`, negocia medium, solicita `getUserMedia()`, conecta à sala LiveKit e publica tracks conforme medium. Fallback gracioso para text quando câmera/mic negados. Renegociação de medium sem reiniciar a sessão. Arc 15 completo.

### Arquivos criados
- `packages/agent-assist-ui/webrtc-widget.html` — widget completo: JWT HS256 via Web Crypto, handshake WS, `webrtc.ready` (LiveKit connect + getUserMedia + publish tracks), `webrtc.renegotiate` (upgrade/downgrade sem recriar room), medium indicator bar (📹/🎤/💬), video grid 2-up (remote full + local PiP espelhado), waveform 20 barras CSS animadas, media controls (mic/cam/hangup), interaction cards com opções e text input fallback, `getUserMedia` permission denial banner → fall back to text

---

## Arc 15 Fase E — WebRTC: Console Platform-UI Overlay (2026-05-20)

Implementação da Fase E do canal WebRTC: overlay de mídia no Console do agente para sessões `channel=webrtc`. Adapta o visual ao medium negociado (video grid 2-up, waveform animado, ou nenhum overlay para text). Inclui hook de conexão LiveKit, controles de mídia, view somente-leitura para supervisores, e namespace i18n `webrtc` completo (en + pt-BR).

### Arquivos criados
- `packages/platform-ui/src/modules/agent-assist/hooks/useWebRTCSession.ts` — fetch token, `Room` LiveKit, publish local tracks, controles mic/câmera, cleanup automático; medium=text sem conexão de mídia
- `packages/platform-ui/src/modules/agent-assist/components/VideoGrid.tsx` — grid 2-up com `track.attach()` nativo; PiP local espelhado; placeholder quando sem track remoto
- `packages/platform-ui/src/modules/agent-assist/components/MediaControls.tsx` — mic/câmera/desconectar; câmera oculta em voice; badge de medium; i18n
- `packages/platform-ui/src/modules/agent-assist/components/WebRTCOverlay.tsx` — container condicional por medium; AnimatedWaveform 20 barras CSS; status bar com timer; estados connecting/error
- `packages/platform-ui/src/modules/agent-assist/components/WebRTCSupervisorView.tsx` — connect como observer sem publicar tracks; compact VideoGrid ou indicador voz
- `packages/platform-ui/src/i18n/locales/en/webrtc.json` — namespace webrtc inglês
- `packages/platform-ui/src/i18n/locales/pt-BR/webrtc.json` — namespace webrtc português

### Arquivos modificados
- `packages/platform-ui/package.json` — deps: `@livekit/components-react@^2.6.0`, `livekit-client@^2.5.0`
- `packages/platform-ui/src/i18n/index.ts` — import + registro namespace `webrtc` (en + pt-BR)
- `packages/platform-ui/src/modules/agent-assist/AgentAssistPage.tsx` — import `WebRTCOverlay`; render condicional antes do `ParticipantFilterBar` quando `selected.channel === "webrtc"`

---

## Arc 15 Fase D — WebRTC: Egress Recording (2026-05-20)

Implementação da Fase D do canal WebRTC: gravação de segmentos via LiveKit Egress API, aviso LGPD antes de iniciar, download do arquivo → AttachmentStore → evento `recording.completed` no session stream. Guard de duplo início via Redis. Suite de testes com 30+ casos.

### Arquivos modificados
- `packages/channel-gateway/src/plughub_channel_gateway/config.py` — 3 novas variáveis WebRTC: `webrtc_recording_notice`, `webrtc_egress_output_dir`, `webrtc_egress_wait_s`
- `packages/channel-gateway/src/plughub_channel_gateway/adapters/webrtc_provider.py` — `LiveKitProvider.start_egress()` (StartRoomCompositeEgressRequest + EncodedFileOutput) e `stop_egress()` (StopEgressRequest) implementados; dev_mode e ImportError com graceful fallback
- `packages/channel-gateway/src/plughub_channel_gateway/adapters/webrtc.py` — `attachment_store` injetável; `_session_egress` dict; `_start_egress()` (Redis guard + LGPD notice TTS/text + provider call); `_stop_all_egress()` (idempotente); `_stop_egress_and_store()` (stop → sleep → read → commit → XADD stream → unlink); integração em `_on_routing_assigned`, `_close_session`, `deliver_session_closed`

### Arquivos criados
- `packages/channel-gateway/src/plughub_channel_gateway/tests/test_webrtc_egress.py` — 30+ testes: start guard, double-start, opt-out, stop+store (file → AttachmentStore → stream event), no-file graceful, idempotência, provider dev mode

### Documentação
- `docs/arcos/arc15-webrtc.md` — v1.4; Fase D marcada ✅; CLAUDE.md Pending atualizado

---

## Arc 15 Fase C — WebRTC: STT/TTS Pipeline + DataChannel (2026-05-20)

Implementação da Fase C do canal WebRTC: pipeline completo de STT/TTS server-side usando LiveKit Python SDK, com resampler PCM 48kHz → 8kHz μ-law, injeção de TTS via LocalAudioTrack, normalização de DataChannel text/menu reply para Kafka/Redis.

### Arquivos criados

- **`packages/channel-gateway/src/plughub_channel_gateway/adapters/webrtc_room_client.py`**: Módulo de participação server-side em rooms LiveKit para o pipeline STT/TTS.
  - `resample_pcm_48_to_8(pcm_48, num_channels)`: Downsampler 48kHz PCM → 8kHz μ-law usando audioop (Python ≤ 3.12) com fallback struct-based para Python 3.13+
  - `mp3_to_pcm(mp3_bytes, target_sample_rate)`: Decode MP3 → PCM via pydub; graceful degradation (retorna `b""`) quando pydub/ffmpeg não está disponível
  - `IWebRTCRoomClient` Protocol: `connect()`, `subscribe_customer_audio()`, `publish_audio()`, `disconnect()`
  - `LiveKitRoomClient`: implementação produção usando `livekit.rtc.Room`; graceful degradation quando SDK não instalado; queue de 500 frames para backpressure; `LocalAudioTrack` publicado no primeiro `publish_audio()` call
  - `MockRoomClient`: stub in-memory para testes com `inject_audio()` / `end_audio()` helpers

- **`packages/channel-gateway/src/plughub_channel_gateway/tests/test_webrtc_stt_tts.py`**: 30+ testes cobrindo: resampler + μ-law helper, `MockRoomClient` compliance, STT pipeline → Kafka (`audio_transcript`), interim results not published, cancelled gracefully, audio resampled before STT, TTS inject calls `publish_audio`, TTS skipped on none/empty/mp3-failure, `deliver_text` triggers TTS on voice medium, DataChannel text → Kafka, interaction_reply → Redis, STT disabled, teardown via `_close_session` + `deliver_session_closed`, provider factories.

### Arquivos modificados

- **`packages/channel-gateway/src/plughub_channel_gateway/adapters/webrtc.py`**: WebRTCAdapter estendido com Phase C:
  - Docstring atualizada: protocolo WS expandido com `webrtc.message`, `webrtc.interaction_reply`, `webrtc.renegotiate`; seção Phase C explicando pipeline STT/TTS e DataChannel
  - Imports: `FallbackSTTProvider`, `FallbackTTSProvider`, `ISTTProvider`, `ITTSProvider`, `DeepgramSTTProvider`, `ElevenLabsTTSProvider`, `MockSTTProvider`, `MockTTSProvider` de `voice_provider`; `IWebRTCRoomClient`, `LiveKitRoomClient`, `mp3_to_pcm`, `resample_pcm_48_to_8` de `webrtc_room_client`
  - `__init__`: parâmetros `stt_provider` e `tts_provider` + `_room_clients: dict[str, IWebRTCRoomClient]` + `_stt_tasks: dict[str, asyncio.Task]`
  - `_build_stt_provider()`: Deepgram quando `voice_deepgram_api_key` + `webrtc_stt_enabled`; fallback `MockSTTProvider`
  - `_build_tts_provider()`: ElevenLabs quando `voice_elevenlabs_api_key`; fallback `MockTTSProvider(synthesize_returns_none=True)`
  - `deliver_text()`: dispara `_tts_inject()` quando `medium in ("voice","video")` e `webrtc_tts_injection_enabled=True`
  - `_on_routing_assigned()`: inicia `_start_stt_pipeline()` quando `medium in ("voice","video")`
  - `_receive_loop()`: `webrtc.message` → `_publish_inbound(content_type="text")`; `webrtc.interaction_reply` → `redis.lpush("menu:result:{session_id}", ...)`
  - `_close_session()`: cancela `_stt_tasks[session_id]`; desconecta `_room_clients[session_id]`
  - `deliver_session_closed()`: mesmo teardown de Phase C
  - `_start_stt_pipeline()`: gera bot token (`hidden=True`); instancia `LiveKitRoomClient`; conecta; lança task `_stt_pipeline()`
  - `_stt_pipeline()`: async generator interno `_audio_chunks()` que itera `subscribe_customer_audio()` → `resample_pcm_48_to_8()`; passa para `FallbackSTTProvider.stream()`; publica `is_final=True` via `_publish_transcript()`
  - `_publish_transcript()`: Kafka `conversations.inbound` com `content_type="audio_transcript"`, `confidence`, `start_ms`, `end_ms`
  - `_tts_inject()`: `FallbackTTSProvider.synthesize()` → `mp3_to_pcm()` → `room_client.publish_audio(24000 Hz)`

- **`docs/arcos/arc15-webrtc.md`**: versão 1.2 → 1.3; status atualizado; Fase C marcada ✅ com todos os detalhes de implementação.

---

## Arc 15 Fase B — WebRTC: Media Capabilities + Re-negociação (2026-05-20)

Implementação da Fase B do canal WebRTC: `media_capabilities` propagada do schema Zod → banco de dados → CRUD → stream de sessão → adaptador WebRTC. Inclui re-negociação de medium mid-session quando um novo agente assume a sessão.

### Arquivos criados

- **`packages/agent-registry/prisma/migrations/20260520200000_add_agent_media_capabilities/migration.sql`**: `ALTER TABLE "agent_types" ADD COLUMN "media_capabilities" TEXT[] DEFAULT ARRAY[]::TEXT[]`

### Arquivos modificados

- **`packages/schemas/src/agent-registry.ts`**: `media_capabilities: z.array(z.enum(["video","voice","text"])).default([])` adicionado ao `AgentTypeRegistrationSchema` (após `capabilities`, antes de `agent_classification`). Documentação inline: ordem implica preferência, vazio = text-only.

- **`packages/schemas/src/agent-registry.test.ts`**: 4 novos casos de teste — defaults to empty array, validates all media types, validates single medium, rejects invalid medium ("audio").

- **`packages/agent-registry/prisma/schema.prisma`**: campo `media_capabilities String[] @default([])` com comentário Arc 15 no modelo `AgentType`.

- **`packages/agent-registry/src/routes/agent-types.ts`**: POST handler cria com `media_capabilities: body.media_capabilities ?? []`; PATCH handler propaga `media_capabilities` quando presente no body.

- **`packages/orchestrator-bridge/src/plughub_orchestrator_bridge/main.py`**: `_write_routing_assigned_to_stream()` helper — escreve evento `routing.assigned` no stream `session:{id}:stream` com `agent_type.media_capabilities` e `pool` JSON. Chamado em todas as vias de ativação: native (`plughub-native`, antes de `activate_native_agent`), human (`framework == "human"`, antes de `activate_human_agent`), e external-mcp (`framework == "external-mcp"`, antes de `activate_external_mcp_agent`). Falhas são silenciosas (warning no log) para nunca bloquear o fluxo de roteamento.

- **`packages/channel-gateway/src/plughub_channel_gateway/adapters/webrtc.py`**: `_stream_watcher` atualizado — primeiro `routing.assigned` → `_on_routing_assigned()` (setup completo: room + token + webrtc.ready); subsequentes → `_on_routing_renegotiate()` (re-negocia medium; envia `webrtc.renegotiate` apenas se medium mudou; reutiliza room existente). Novo método `_on_routing_renegotiate()`: extrai capabilities do evento, chama `negotiate_medium()`, compara com `self._mediums[session_id]`, atualiza Redis `channel:webrtc:{id}:medium`, envia `{"type":"webrtc.renegotiate","negotiated_medium":...,"room_name":...}`.

---

## Arc 15 Fase A — Canal WebRTC: Infra + Signaling (2026-05-20)

Implementação da Fase A do canal WebRTC: provider abstraction (LiveKit SFU), WebSocket de signaling, negociação de medium, emissão de tokens LiveKit, e endpoint HTTP para agentes/supervisores entrarem na sala.

### Arquivos criados

- **`adapters/webrtc_provider.py`**: Data types (`RoomInfo`, `ParticipantInfo`, `TokenGrants`), Protocol `IWebRTCProvider` (runtime_checkable: `generate_token`, `create_room`, `delete_room`, `get_room`, `list_participants`, `start_egress`, `stop_egress`). `LiveKitProvider`: integração via `livekit-api` SDK, dev mode automático quando sem api_key/secret (tokens placeholder sem I/O de rede). `MockWebRTCProvider`: stub in-memory com gravação de calls para assertions (rooms_created, rooms_deleted, tokens_generated, egresses_started, egresses_stopped). Helpers: `build_room_name(session_id)` → `plughub-{session_id}`; `negotiate_medium(agent_capabilities, fallback_order)` → `"video"|"voice"|"text"`.

- **`adapters/webrtc.py`**: `WebRTCAdapter(ChannelAdapter)` — singleton de signaling e entrega WebRTC. **Outbound delivery**: `deliver_text` → `webrtc.message`; `deliver_menu` → `webrtc.interaction`; `deliver_typing` → `webrtc.typing`; `deliver_session_closed` → `webrtc.session_closed` + WS close. Registra `_connections: dict[session_id → WebSocket]` para entrega via OutboundConsumer. **WS lifecycle** (`handle_ws`): accept → conn.ready → conn.hello → conn.authenticate (JWT HS256 com override Redis) → conn.authenticated; 3 tasks concorrentes — `_stream_watcher` (XREAD routing.assigned → negotiate_medium → create_room → generate_token → webrtc.ready; também detecta session.closed), `_receive_loop` (webrtc.hangup → contact_close Kafka; conn.ping → conn.pong), `_keepalive` (renova TTL Redis a cada 20s). **Token endpoint** (`get_token`): role=agent (can_publish=True, hidden=False) ou supervisor (can_publish=False, hidden=True); retorna {token, livekit_url, room_name, negotiated_medium}. Layer 2 pool resolution via `endpoint_resolver`. Classe interna `_AuthError` para erros estruturados no handshake.

- **`tests/test_webrtc_adapter.py`**: Suite completa com 42+ testes. Cobre: `negotiate_medium` (7 casos: video preferred, voice fallback, text fallback, empty capabilities, pool override, no intersection, pool order), `build_room_name`, `TokenGrants` (customer/supervisor defaults), `MockWebRTCProvider` (token generation, room CRUD, list_participants, egress start/stop, counter), `LiveKitProvider` dev mode (token, create_room, get/delete/list, egress raises NotImplementedError), `WebRTCAdapter.deliver_text/menu/typing/session_closed` (com e sem conexão ativa), `get_token` (room not ready, room ready, agent/supervisor grants, identity), `_on_routing_assigned` (webrtc.ready content, medium negotiation, redis persistence, room creation), `_auth_handshake` (success, conn.authenticated sent, invalid token, publishes contact_open + routing.request, redis storage), `_close_session` (contact_close payload), `IWebRTCProvider` protocol compliance.

### Arquivos modificados

- **`config.py`**: +8 campos WebRTC — `webrtc_livekit_url`, `webrtc_livekit_api_key`, `webrtc_livekit_api_secret`, `webrtc_token_ttl_s`, `webrtc_default_pool_id`, `webrtc_default_medium_order`, `webrtc_stt_enabled`, `webrtc_tts_injection_enabled`.
- **`main.py`**: `WebRTCAdapter` importado e instanciado no lifespan; registrado em `_channel_adapters["webrtc"]`. Endpoints: `WS /ws/webrtc/{pool_id}` (signaling lifecycle), `GET /webrtc/token/{session_id}?role=agent|supervisor&identity=<id>` (LiveKit token para agente/supervisor).

---

## Channel Gateway — Voice: ElevenLabs TTS + Fallback Providers (2026-05-20)

Adicionado suporte a ElevenLabs como provedor TTS primário de alta qualidade, e encadeamento automático de fallback para STT e TTS. Twilio permanece exclusivamente como tronco de voz PSTN — nunca produz TTS.

### Arquivos modificados

- **`adapters/voice_provider.py`**: +3 classes — `ElevenLabsTTSProvider` (REST API `eleven_multilingual_v2`, retorna MP3 bytes; `None` em erro habilita fallback automático), `FallbackSTTProvider` (tenta providers em ordem; preserva buffer de áudio para replay ao próximo provider em caso de exceção ou zero results), `FallbackTTSProvider` (tenta providers em ordem; avança no primeiro `None` ou exceção; TwilioSay como último recurso sempre disponível). Módulo docstring atualizado com diagrama de seleção.
- **`config.py`**: +4 campos — `PLUGHUB_VOICE_ELEVENLABS_API_KEY`, `PLUGHUB_VOICE_ELEVENLABS_VOICE_ID` (default: Adam multilingual `pNInz6obpgDQGcFmaJgB`), `PLUGHUB_VOICE_TTS_FALLBACK_PROVIDER`, `PLUGHUB_VOICE_STT_FALLBACK_PROVIDER`.
- **`adapters/voice.py`**: imports + `_build_tts_provider()` refatorado: ElevenLabs primário (quando api_key) → FallbackTTSProvider([ElevenLabs, TwilioSay]); Deepgram Aura quando explicitamente selecionado → FallbackTTSProvider([DeepgramAura, TwilioSay]); padrão = TwilioSay direto. `_build_stt_provider()` refatorado: Deepgram com chave → FallbackSTTProvider([Deepgram, MockSTT]); sem chave → MockSTT diretamente.
- **`tests/test_voice_adapter.py`**: +~100 linhas — `TestElevenLabsTTSProvider` (5 testes: sem key, HTTP success, HTTP error, voice_id override, default voice), `TestFallbackSTTProvider` (4 testes: yields from primary, advances on exception, last-provider exception, empty-providers error), `TestFallbackTTSProvider` (5 testes: returns first non-None, advances on None, advances on exception, all-None chain, empty-providers error), `TestVoiceAdapterProviderFactories` (6 testes: factory logic para cada combinação de env var), `TestFakeSettingsElevenLabsFields` (smoke test). `_fake_settings()` estendido com 4 novos campos.
- **`docs/arcos/channel-gateway-multi-channel.md`**: seção 9.7 reescrita — tabela por interface, seleção TTS/STT, diagrama de encadeamento de fallback.

---

## Channel Gateway — Voice Adapter (2026-05-20)

Implementação completa do canal de Voz via Twilio CPaaS, com abstração de provider para STT e TTS. Cobre inbound PSTN, Media Streams WebSocket (STT), conferências, TTS na conference, transferência AI→humano, gravação por segmento (Arc 13 compliance), chamadas outbound via `collect.events`, e modos de input DTMF/STT.

### Arquivos criados

- **`adapters/voice_provider.py`**: três Protocol interfaces independentes — `IVoiceProvider` (CPaaS: controle de chamada, conferência, gravação), `ISTTProvider` (streaming STT), `ITTSProvider` (TTS com retorno bytes ou None para CPaaS nativo). Implementações concretas: `TwilioVoiceProvider` (HMAC-SHA1 webhook verify + TwiML generation + REST API calls via httpx: answer, conference, add_participant, announce_tts, start_recording, stop_recording, create_call), `DeepgramSTTProvider` (WebSocket streaming via `websockets` lib: μ-law 8kHz → transcripts parciais e finais), `TwilioSayTTSProvider` (delegação ao `<Say>` TwiML nativo — sem API externa), `DeepgramAuraTTSProvider` (REST API Deepgram Aura → bytes MP3). Mocks: `MockVoiceProvider` (calls_created, hung_up, recordings_started, tts_announced), `MockSTTProvider` (results configuráveis, chunks_received), `MockTTSProvider` (none mode ou bytes mode).
- **`adapters/voice.py`**: `VoiceAdapter(ChannelAdapter)`. Dois vínculos simultâneos por chamada ativa. **Webhook inbound** (`handle_inbound`): HMAC-SHA1 verify → pending_collect lookup (outbound collect correlation) → `_resolve_pool` (Layer 2 via agent-registry por DID) → `_open_session` → `_route_inbound` → TwiML (`<Start><Stream>` + `<Dial><Conference>`). **Webhook status** (`handle_status`): detect hangup/no-answer/failed → `_close_session` com close_reason mapeado; `in-progress` → store conference_sid. **Webhook recording** (`handle_recording_complete`): lookup segment_id por recording SID no Redis → background download via httpx → AttachmentStore → `recording.completed` event no stream. **TTS** (`_deliver_tts`): TwilioSay path (texto → Redis TTL 60s → `conference.announce_url → /voice/tts/{tts_id}`) ou Deepgram path (bytes → Redis → `/voice/tts-audio/{tts_id}`). **Media WS** (`handle_media_ws`): 4 loops concorrentes — `_receive_loop` (Twilio WS events: start, media, dtmf, stop), `_stt_loop` (audio_queue → DeepgramSTT → publish `audio_transcript`), `_keepalive` (TTL renewal 15s), `_stream_watcher` (XREAD → `routing.assigned` → segment recording). **Collect state machine**: `_handle_dtmf`, `_handle_stt_result`, `_process_collect_input` (option matching, multi-field advance, re-prompt on invalid). **Segment recording** (§13): `_on_routing_assigned`, `_announce_and_start_recording` (notice TTS + 1.2s pause + CPaaS start_recording), `_stop_all_recordings`. Guards: opt-out, double-announcement, missing conference_sid. **Outbound collect** (`handle_collect_event`): consume `collect.events` (channel: voice) → `create_call` → `pending_collect:{call_sid}` Redis key (TTL 5min). Helpers: `_normalize_e164`, `_match_option` (DTMF index + label case-insensitive), `_build_voice_prompt`.
- **`tests/test_voice_adapter.py`**: 38+ testes. Cobre: TwilioVoiceProvider (signature válida/inválida/dev-mode/params-vazios, TwiML inbound com/sem wait_url, TTS TwiML com escape de XML), MockVoiceProvider/STT/TTS (verify, create_call, hangup, start_recording, announce_tts, synthesize), handle_inbound (nova sessão, pending_collect, pool default, invalid signature), handle_status (completed→hangup, no-answer, failed, sem sessão, conference_sid store), _deliver_tts (TwilioSay path, DeepgramAura path, sem conference_sid, Redis set key), get_tts_twiml/audio (found/not found), deliver_outbound (notify, message.text, session.closed, typing noop, interaction.request), DTMF collect (válido, inválido re-prompt, sem estado, voice-only mode, multi-field), segment recording (start, skip-false, opt-out, double-announcement, stop-all, notice TTS), handle_collect_event (outbound call, pending Redis, sem target, journey_id), utilities (_normalize_e164, _match_option digit/label/no-match/empty, _build_voice_prompt), integração completa (inbound→status→close).

### Arquivos modificados

- **`config.py`**: adicionados campos `voice_account_sid`, `voice_auth_token`, `voice_from_number`, `voice_provider`, `voice_default_pool_id`, `voice_stt_provider`, `voice_deepgram_api_key`, `voice_stt_language`, `voice_tts_provider`, `voice_tts_voice_id`, `voice_webhook_host`, `voice_conference_wait_url`, `voice_agent_stt_enabled`, `voice_default_recording_notice`.
- **`main.py`**: `VoiceAdapter` importado e instanciado no lifespan; registrado em `_channel_adapters["voice"]`; `_collect_events_consumer()` task (Kafka `collect.events`, filtra `channel: voice`). Endpoints: `POST /webhooks/voice/inbound` (TwiML XML response), `POST /webhooks/voice/status`, `POST /webhooks/voice/recording`, `GET /voice/tts/{tts_id}` (TwiML), `GET /voice/tts-audio/{tts_id}` (audio/mpeg), `WS /voice/media`.
- **`docs/arcos/channel-gateway-multi-channel.md`**: seções 9.9 (DTMF vs STT input_mode), 9.10 (outbound voice via collect.events), 9.11 (Twilio protocol details: TwiML, TTS via conference announce, assinatura, Redis keys) adicionadas. Status atualizado: todos os quatro canais de texto + Voice implementados.

---

## Channel Gateway — Email Adapter (2026-05-20)

Implementação completa do canal Email via Mailgun, com abstração de provider para suporte futuro a SendGrid, AWS SES, Microsoft Graph API (Exchange/O365) e Gmail API.

### Arquivos criados

- **`adapters/email_provider.py`**: `IEmailProvider` Protocol + `ParsedEmail` / `EmailAttachment` dataclasses + `MailgunProvider` (HMAC-SHA256 webhook verify + multipart/form-data parse + send via Mailgun API v3) + `MockEmailProvider` (testes sem I/O). Helpers `_extract_email`, `_extract_attachments_from_mime`.
- **`adapters/email.py`**: `EmailAdapter(ChannelAdapter)` singleton. Inbound: `process_inbound` (Mailgun HMAC-SHA256 + background task), `_resolve_session` (3 tiers: Reply-To address → In-Reply-To Message-ID → contact email hash → nova sessão), `_strip_quoted_text` (heurísticas: `>`, On/Em wrote, Outlook headers), `_store_attachments` (AttachmentStore). Outbound: `deliver_text` (MIME multipart text/plain + text/html via `mistune`, assinatura do agente, headers Reply-To + In-Reply-To + References), `deliver_menu` (lista numerada + coleta sequencial), `deliver_typing` (no-op), `deliver_session_closed` (cleanup Redis). `send_template` para MCP tool `email_send_template`.
- **`tests/test_email_adapter.py`**: 35+ testes com `MockEmailProvider`. Cobre `_strip_quoted_text` (5 padrões de citação), `_markdown_to_html`, key helpers, MailgunProvider signature (válida, inválida, dev mode), MockEmailProvider, process_inbound, resolve_session (3 tiers + nova), deliver_text (subject Re:, reply-to, in_reply_to, assinatura, HTML), deliver_menu, deliver_typing, deliver_session_closed, fluxo completo de inbound (nova sessão, reply via Reply-To, strip de quoted text, armazenamento de Message-ID).

### Arquivos modificados

- **`config.py`**: adicionado `email_api_key`, `email_domain`, `email_signing_key`, `email_from_address`, `email_reply_domain`, `email_default_pool_id`, `email_provider`.
- **`main.py`**: endpoint `POST /webhooks/email` (Mailgun multipart). `EmailAdapter` instanciado no lifespan e registrado em `_channel_adapters["email"]`.
- **`docs/arcos/channel-gateway-multi-channel.md`**: seção 8.4 expandida de stub para 11 subseções.

### Decisões de arquitetura

- Provider inicial: Mailgun. Novos providers implementam `IEmailProvider` (Exchange/Gmail marcados como fase futura).
- Mailbox config em Configuration/Channels (ChannelEndpoint), não env vars — múltiplas caixas simultâneas via Layer 2.
- Session: Reply-To `reply+{session_id}@{domain}` como mecanismo primário; In-Reply-To e hash de endereço como fallback.
- TTL da sessão: segue ciclo de vida do Core (`session.closed`) — sem TTL de inatividade no gateway.
- Quoted text: strip heurístico antes de publicar no stream; `original_text` armazenado em `content.payload` para auditoria.
- Histórico de thread: acumulado no stream PlugHub via `email_get_thread` MCP tool — não no quoted text do email.
- Outbound: MIME multipart `text/plain` + `text/html` (Markdown → HTML via `mistune`), assinatura por `AgentType.email_signature`.
- `deliver_typing`: no-op.
- `deliver_session_closed`: cleanup Redis sem email ao cliente.
- Lógica de triagem (protocolo, classificação, escalação): responsabilidade do agente Skill Flow, não do gateway.
- Forward de email: não implementado — escalação via routing engine (`task` step).

### Spec

→ [`docs/arcos/channel-gateway-multi-channel.md`](docs/arcos/channel-gateway-multi-channel.md) § 8.4

---

## Channel Gateway — SMS Adapter (2026-05-20)

Implementação completa do canal SMS via Twilio, com abstração de provider para suporte futuro a Telnyx, Vonage e AWS SNS.

### Arquivos criados

- **`adapters/sms_provider.py`**: `ISMSProvider` Protocol + `TwilioProvider` (HMAC-SHA1 webhook verify, REST API, split_sms automático) + `MockSMSProvider` (testes sem I/O). Helper `split_sms()` divide textos longos em segmentos ≤153 chars com sufixo `(N/T)`, limite de 10 segmentos.
- **`adapters/sms.py`**: `SMSAdapter(ChannelAdapter)` singleton. Inbound: `process_inbound` (Twilio HMAC-SHA1 + background task), `_accumulate_parts` (buffer Redis de fragmentos SMS por `SmsMessageSid`, TTL 5min, idempotente contra retries Twilio), `_resolve_session` (lookup `channel:sms:{contact_id}:session` TTL 24h). Outbound: `deliver_text` (auto-split multi-segmento), `deliver_menu` (sequential collect — único modo SMS), `deliver_typing` (no-op), `deliver_session_closed` (cleanup Redis). Coleta sequencial com validação de opção numérica e reprompt automático.
- **`tests/test_sms_adapter.py`**: 35+ testes com `MockSMSProvider`. Cobre `split_sms`, TwilioProvider signature, MockSMSProvider, process_inbound, accumulate_parts (ordem, duplicatas), session resolution, deliver_text/menu/typing/closed, sequential collect completo (texto livre, seleção numérica, opção inválida, último campo → menu_result, campo masked).

### Arquivos modificados

- **`config.py`**: adicionado `sms_account_sid`, `sms_auth_token`, `sms_from_number`, `sms_provider`, `sms_default_pool_id`.
- **`main.py`**: endpoint `POST /webhooks/sms` (Twilio form-encoded, resposta TwiML `<Response/>`). `SMSAdapter` instanciado no lifespan e registrado em `_channel_adapters["sms"]`.
- **`docs/arcos/channel-gateway-multi-channel.md`**: seção 8.3 expandida de stub para 10 subseções (provider abstraction, credenciais, sessão, inbound flow, concatenação, outbound split, coleta sequencial, Redis keys, verificação Twilio, testes).

### Decisões de arquitetura

- Provider inicial: Twilio. Novos providers implementam `ISMSProvider` sem tocar no adapter.
- Credenciais: env var padrão por instalação + Redis override por tenant.
- Session: enquanto `session_id` ativo no Redis para o número (E.164), envia para ele. Session encerrada = próximo contato é novo. TTL 24h renovável.
- Concatenação inbound: acumula fragmentos por `SmsMessageSid`, publica quando completo. TTL 5min no buffer.
- Outbound: divide em segmentos ≤153 chars com sufixo `(1/N)` quando necessário (máx 10 segmentos).
- `deliver_typing`: no-op (SMS não tem typing indicator).
- `deliver_session_closed`: limpeza Redis sem mensagem ao cliente.
- Coleta sequencial: único modo de interação SMS. Validação de seleção numérica com reprompt automático para opções inválidas.

### Spec

→ [`docs/arcos/channel-gateway-multi-channel.md`](docs/arcos/channel-gateway-multi-channel.md) § 8.3

---

## Channel Gateway — WhatsApp Adapter (2026-05-20)

Implementação completa do canal WhatsApp via Meta Cloud API, com abstração de provider para suporte futuro a BSPs.

### Arquivos criados

- **`adapters/whatsapp_provider.py`**: `IWhatsAppProvider` Protocol + `MetaCloudProvider` (httpx, Graph API v19.0) + `MockWhatsAppProvider` (testes sem I/O de rede). Provider cobre: `send_text`, `send_interactive_buttons`, `send_interactive_list`, `send_media`, `get_media_url`, `download_media`.
- **`adapters/whatsapp.py`**: `WhatsAppAdapter(ChannelAdapter)` singleton. Inbound: `verify_signature` HMAC-SHA256, `handle_inbound` (HTTP 200 imediato + background task), `_resolve_session` (lookup `channel:whatsapp:{contact_id}:session` TTL 24h), normalização de text/media/interactive/location. Outbound: `deliver_text`, `deliver_menu` (botões ≤3, list 4-10, sequential collect >10 ou form), `deliver_typing` (no-op), `deliver_session_closed` (cleanup Redis). Coleta sequencial de formulário com estado Redis TTL 30min.
- **`tests/test_whatsapp_adapter.py`**: 30+ testes com `MockWhatsAppProvider`. Cobre signature, sessão, inbound text/media/interactive/location, outbound text/menu/typing/closed, coleta sequencial completa, payloads MetaCloudProvider.

### Arquivos modificados

- **`config.py`**: adicionado `whatsapp_access_token`, `whatsapp_phone_number_id`, `whatsapp_app_secret`, `whatsapp_verify_token`, `whatsapp_graph_api_url`.
- **`main.py`**: endpoints `GET /webhooks/whatsapp` (challenge Meta) + `POST /webhooks/whatsapp` (inbound). `WhatsAppAdapter` instanciado no lifespan e registrado em `_channel_adapters["whatsapp"]`.

### Decisões de arquitetura

- Provider inicial: Meta Cloud API direta. BSPs adicionados implementando `IWhatsAppProvider`.
- Credenciais: env var padrão por instalação + Redis override por tenant (mesmo padrão webchat JWT).
- Session: `contact_id` = número E.164 do cliente, TTL 24h renovado por mensagem.
- Media inbound: HTTP 200 imediato, download em background task.
- `deliver_typing`: no-op (WhatsApp não tem API de typing indicator).
- `deliver_session_closed`: limpeza Redis sem mensagem ao cliente.

### Spec

→ [`docs/arcos/channel-gateway-multi-channel.md`](docs/arcos/channel-gateway-multi-channel.md) § 8.2

---

## Channel Gateway — Phase 1 Refactoring Multi-Canal (2026-05-20)

Refactoring de base para suporte a múltiplos canais no `channel-gateway`. Sem breaking changes — comportamento do webchat inalterado.

### Arquivos criados

- **`adapters/__init__.py`**: pacote adapters.
- **`adapters/base.py`**: `ChannelAdapter` ABC com 4 métodos abstratos (`deliver_text`, `deliver_menu`, `deliver_typing`, `deliver_session_closed`). `channel: ClassVar[str]` identifica o canal. Um singleton por canal registrado no `OutboundConsumer`.
- **`adapters/webchat_channel.py`**: `WebchatChannelAdapter(ChannelAdapter)` — singleton de entrega para webchat. Extrai lógica de dispatch do `OutboundConsumer` anterior. `deliver_text` persiste histórico (sem WS send — hybrid stream model). `deliver_menu` registra `masked_fields` no `SessionRegistry`. `deliver_typing` e `deliver_session_closed` enviam via WebSocket.

### Arquivos modificados

- **`models.py`**: `channel: Literal["webchat"]` → `channel: str = "webchat"` em `NormalizedInboundEvent`, `ContactOpenEvent`, `ContactClosedEvent`. Adicionado `content_type: Literal["text", "audio_transcript", "image", "document", "video"] = "text"` em `NormalizedInboundEvent`. Adicionado `channel_session_id: str | None = None` em `ContactOpenEvent` (wamid, CallSid, Message-ID etc.).
- **`outbound_consumer.py`**: reescrito com `_adapters: dict[str, ChannelAdapter]`. Elimina `if channel != "webchat"` hardcoded — delega por `self._adapters.get(channel)`. Novos canais adicionados registrando o adapter no `main.py`, sem tocar o consumer.
- **`main.py`**: instancia `WebchatChannelAdapter` e passa `adapters={"webchat": ...}` para `OutboundConsumer`.
- **`tests/test_outbound_consumer.py`**: atualizado para nova API. Testa routing do consumer, `WebchatChannelAdapter` por tipo de mensagem, e tratamento de erros.

### Spec

→ [`docs/arcos/channel-gateway-multi-channel.md`](docs/arcos/channel-gateway-multi-channel.md)

---

## Retry + Dead-Letter Queue nos consumers Kafka críticos (2026-05-20)

Implementado padrão de retry com backoff exponencial + DLQ (`events.dead_letter`) nos três consumers Kafka críticos da plataforma, eliminando perda silenciosa de eventos em caso de falha transiente.

### skill-flow-worker (`packages/skill-flow-worker/`)

- **`config.ts`**: adicionado `kafkaDlqTopic: string` em `WorkerSettings` (env `KAFKA_DLQ_TOPIC`, default `events.dead_letter`).
- **`worker.ts`**: reescrito com `DLQ producer` KafkaJS, método `_handleWithRetry()` (3 tentativas, backoff 500 ms → 1 000 ms) e `_publishDlq()`. Fire-and-forget de `runInstance()` preservado via `_inflight` Set — apenas o dispatch layer é retried. Erros de JSON skip imediato (sem retry). `DlqPayload`: `event_id`, `source_topic`, `consumer_group`, `service`, `error`, `attempt_count`, `payload_raw`, `failed_at`.

### analytics-api (`packages/analytics-api/`)

- **`config.py`**: adicionado `kafka_dlq_topic: str = "events.dead_letter"`.
- **`consumer.py`**: adicionado `AIOKafkaProducer` (DLQ producer), constantes `MAX_ATTEMPTS=3`/`BACKOFF_BASE_MS=500`, funções `_process_with_retry()` e `_publish_dlq_analytics()`. `_process_message()` agora propaga exceções (sem `except Exception` externo); `_write_row()` já propagava. JSON malformado: skip imediato. Commit de offset após batch completo — mensagens DLQ-roteadas também commitadas.

### orchestrator-bridge (`packages/orchestrator-bridge/`)

- **`main.py`**: adicionado `KAFKA_DLQ_TOPIC` (env var), `_MAX_DISPATCH_ATTEMPTS=3`/`_DISPATCH_BACKOFF_BASE_MS=500`, função `_publish_dlq_bridge()` (usa `_kafka_producer` global já existente). `_dispatch()` reescrito como wrapper de retry; lógica movida para `_dispatch_once()`. Fire-and-forget via `asyncio.create_task` preservado — retries ocorrem dentro da task, sem bloquear o consumer loop.

### Contrato DLQ uniforme

Todos os três consumers publicam o mesmo formato `DlqPayload` no tópico `events.dead_letter`:
`event_id`, `source_topic`, `consumer_group`, `service`, `error`, `attempt_count`, `payload_raw`, `failed_at`.

---

## Channel Endpoints Layer 2 + Analytics Agents expandido (2026-05-20)

### Channel Endpoints — Layer 2: channel-gateway endpoint resolver

Implementado o lookup de channel endpoints no hot-path do WebSocket, completando a Layer 2 da pilha de roteamento do canal.

**agent-registry** (`packages/agent-registry/src/routes/channel-endpoints.ts`):
- `GET /v1/channel-endpoints` agora aceita o query param `identifier` como filtro; sem ele, retorna todos os endpoints do tenant/channel — backward compatible.

**channel-gateway** (`packages/channel-gateway/`):
- `pyproject.toml`: `httpx>=0.27.0` movido de `[dev]` para dependências principais.
- `config.py`: dois novos settings com prefixo `PLUGHUB_`:
  - `agent_registry_url` (default `http://localhost:3000`)
  - `endpoint_cache_ttl_s` (default `30`)
- Novo módulo `endpoint_resolver.py`:
  - Cache em memória `dict[(tenant_id, channel, identifier), (pool_id, expires_at)]` com TTL configurável; evita round-trips no connect path.
  - Double-check lock (asyncio.Lock) para prevenir cache stampede em conexões concorrentes.
  - Negative caching: `None` também é armazenado com TTL para evitar bombardear o registry com slugs desconhecidos.
  - `resolve_pool(channel, identifier, tenant_id, agent_registry_url, cache_ttl_s, http_timeout_s)` — retorna `pool_id | None`; erros de rede são logados e retornam `None` (nunca quebram o WebSocket accept).
  - `invalidate(tenant_id, channel?)` — exposta para futura integração com consumer `registry.changed`.
- `main.py` — `websocket_endpoint`:
  - Tenta resolver via `resolve_pool('webchat', pool_id_param, …)`.
  - Se retornar `None` (sem registro ativo ou registry indisponível), cai no fallback: `pool_id_param or settings.entry_point_pool_id` — 100% backward compatible.

**Pendente (operacional)**: executar `prisma migrate dev --name add_channel_endpoint` no agent-registry para criar a tabela `channel_endpoints` em produção.

### Analytics Agents — página expandida com abas Humanos / IA

`AnaliseAgentesPage.tsx` reescrita com duas abas de primeiro nível:

- **Humanos**: KPI strip (Sessões, TMO, Resolução, Escalação) + gráfico de tendência diária (Recharts LineChart) + seção de disponibilidade/pausas existente (Arc 8).
- **IA**: KPI strip + gráfico de tendência + tabela de performance por `agent_type_id × pool` com badges coloridos de resolution/escalation.
- Hooks: `useAgentPerformance` (`GET /analytics/reports/agents/performance`) + `useAgentPerformanceDaily` (`GET /analytics/reports/agent-performance/daily`).
- Namespace i18n `agentReports` — locale files `en/agentReports.json` + `pt-BR/agentReports.json` completos.

---

## platform-ui: i18n Console + remoção do modal de wrap-up manual (2026-05-19)

Cobertura i18n completa do módulo agent-assist e remoção do fluxo manual de wrap-up
que foi substituído pelos hook agents do Arc 14.

### i18n — namespace `agentAssist` completo

Todos os componentes do Console agora usam `useTranslation('agentAssist')` + `t(key)`.
Nenhuma string hardcoded de PT-BR ou EN permanece no módulo:

- `AgentAssistPage.tsx` — toasts, labels de tab central (Atual/Journey)
- `AgentInput.tsx` — botão "Enviar"
- `CannedPhrasesPalette.tsx` — empty state de busca
- `Header.tsx` — fix interpolação `count` (reservado i18next) → `pools`
- `AgentesTab.tsx` — title "Opções"
- `ContextoTab.tsx` — refactoring completo: helpers `confidenceLabel`, `sourceLabel`,
  `tagLabel`, `groupByNamespace` passam `t` como parâmetro; `FieldRowProps.t` e
  `CtxFieldRowProps.t` tipados como `(key, opts?) => string`
- `HistoricoTab.tsx` — locale de `formatDate` hardcode PT-BR → `undefined` (browser locale)
- Locale files `en/agentAssist.json` + `pt-BR/agentAssist.json`:
  - Adicionadas seções: `message.*`, `agentes.*`, `contexto.*`, `centerTab.*`, `canned.*`
  - Fix: `comboPools` interpolation `{{count}}` → `{{pools}}`

### Remoção do CloseModal — wrap-up delegado ao Arc 14

- `CloseModal` removido de `AgentAssistPage.tsx` (import + state + JSX)
- `onEncerrar` agora chama `handleClose` diretamente com defaults
  `{ issue_status: "closed", outcome: "resolved" }` — sem modal
- `pendingCloseModal` (cliente desconectou antes do agente receber o contato):
  `useEffect` auto-dispara `agent_done` com `{ issue_status: t("clientDisconnected"), outcome: "abandoned" }`
- Pós-atendimento (wrap-up, NPS, formulários) tratado exclusivamente pelos
  hook agents do Arc 14 (`side: agent | customer`)

---

## Microcopy Review — PT-BR (2026-05-18)

Revisão sistemática de copy UX em todas as superfícies principais. Critérios: idioma PT-BR consistente, tokens semânticos, clareza de CTAs, mensagens de erro empáticas e úteis, ellipsis correto.

### `auth/LoginPage.tsx`
- Toda a string de erro em inglês → PT-BR: `"E-mail ou senha incorretos."`, `"Sua conta está inativa. Entre em contato com o administrador."`, etc.
- Labels "Email" → "E-mail", "Password" → "Senha"
- Placeholder `"you@example.com"` → `"voce@empresa.com.br"`
- CTA `"Sign In"` → `"Entrar"`, `"Signing in…"` → `"Entrando…"`
- Subtítulo `"Enterprise Orchestration Platform"` → `"Plataforma de Orquestração Empresarial"`
- Erro alert: tokens `bg-red-50 border-red-400 text-red-700` → `bg-red-light border-red text-red-text`
- Removido hint de credencial de dev (segurança)

### `components/ui/EmptyState.tsx`
- `text-gray` → `text-muted` (token semântico correto)

### `modules/agent-assist/components/ToastContainer.tsx`
- `bg-indigo-600` → `bg-info` / `bg-warning` / `bg-red` (tokens semânticos)
- `z-40` → `z-toast` (token de z-index)
- `shadow-lg` → `shadow-toast`
- Dismiss button: `tabIndex={-1}` removido; `aria-label="Fechar notificação"` adicionado
- Adicionado `role="region" aria-label="Notificações" aria-live="polite"`

### `modules/agent-assist/components/CannedPhrasesPalette.tsx`
- `text-indigo-600` / `bg-indigo-100` / `bg-indigo-50` / `text-indigo-500` → tokens `primary`
- Label seção "Especialistas (@mention)" → "Especialistas · via @menção" (menos técnico)
- `border-gray-100` → `border-border`

### `modules/agent-assist/components/CloseModal.tsx`
- `focus:ring-indigo-500` → `focus:ring-primary`
- Outcome buttons: `bg-indigo-600 border-indigo-600` → `bg-primary border-primary`; `hover:border-indigo-400` → `hover:border-primary`
- CTA "Confirmar encerramento" → "Encerrar atendimento" — alinha com o título do modal e segue o padrão verbo+substantivo
- `bg-red-600 hover:bg-red-700` → `bg-red hover:bg-red-text`

### `modules/agent-assist/components/DelegarTarefaDrawer.tsx`
- Visibilidade "🔒 Interno (agents_only)" → "🔒 Somente equipe" (remove ID técnico)
- Visibilidade "🌐 Visível ao cliente (all)" → "🌐 Visível ao cliente"
- Shortcut hint "⌘↵ para delegar" → "Ctrl + Enter para delegar" (Windows-compatible)
- `text-gray-400` → `text-muted`

### `modules/_placeholder/PlaceholderPage.tsx`
- `"Back to Home"` → `"Ir para o início"` (PT-BR)

### `i18n/locales/pt-BR/common.json`
- `"Carregando..."` → `"Carregando…"` (ellipsis Unicode correto em ambas ocorrências)
- `"Salvando..."` → `"Salvando…"`

---

## Accessibility Audit — WCAG 2.1 AA (2026-05-18)

Auditoria completa e correções críticas/major. 9 Critical + 14 Major + 10 Minor identificados. Resolvidos nesta sessão: todos os Critical e a maioria dos Major.

### `index.html`
- `lang="en"` → `lang="pt-BR"` — corrige WCAG 3.1.1 (Language of Page)

### `index.css`
- `*:focus-visible` com `ring-2 ring-primary ring-offset-1` — focus ring visível para teclado (WCAG 2.4.7)
- `@media (prefers-reduced-motion: reduce)` — zera todas animações/transições (WCAG 2.3.3)

### `shell/Sidebar.tsx`
- `<nav aria-label="Navegação principal">` em ambos modos collapsed/expanded (WCAG 1.3.1)
- `aria-expanded` + `aria-controls` nos botões de grupo — estado comunicado ao SR (WCAG 4.1.2)
- `<span aria-hidden="true">` em todos os emojis decorativos — elimina anúncios duplos (WCAG 1.3.1)
- `aria-current="page"` no item ativo — localização comunicada (WCAG 2.4.8)
- `aria-label` em vez de `title` nos botões Expandir/Recolher menu
- Painel de filhos usa `hidden` nativo em vez de renderização condicional — semanticamente correto

### `shell/TopBar.tsx`
- `<div>` → `<header>` — landmark `banner` ARIA (WCAG 1.3.1)
- `border-lightGray` → `border-border` / `text-gray` → `text-muted` — tokens corretos
- Skip-navigation link: "Ir para o conteúdo principal" → `#main-content` (WCAG 2.4.1)
- `aria-label` descritivo no botão de idioma (WCAG 4.1.2)
- `aria-label` no bloco de info do usuário

### `shell/Shell.tsx`
- `<main id="main-content" tabIndex={-1}>` — alvo do skip-link, recebe foco programático (WCAG 2.4.1)

### `components/ui/Table.tsx`
- Linhas clicáveis: `tabIndex={0}`, `onKeyDown` (Enter/Space), `focus-visible:ring-2` (WCAG 2.1.1)
- `scope="col"` nos cabeçalhos (WCAG 1.3.1)
- `role="grid"` na tabela com linhas interativas (WCAG 4.1.2)
- `aria-label` por linha com `rowActionLabel` configurável
- `aria-busy="true"` no skeleton state
- `motion-safe:animate-pulse` no skeleton — respeita prefers-reduced-motion
- "No data available" → "Nenhum dado disponível" (idioma correto)

### `modules/agent-assist/components/AgentInput.tsx` *(já aplicado em sessão anterior)*
- `<label>` com `sr-only` + `htmlFor` na textarea (WCAG 1.3.1, 3.3.2)
- `aria-label` descritivo no botão "/" (WCAG 4.1.2)
- `aria-expanded` + `aria-controls` no botão de paleta (WCAG 4.1.2)
- Remoção do soft focus-trap em `handleBlur` (WCAG 2.1.2)
- Touch targets 44×44px no botão "/" e Enviar (WCAG 2.5.5)

### `modules/agent-assist/components/ChatArea.tsx` *(já aplicado)*
- `role="log"` + `aria-live="polite"` na lista de mensagens (WCAG 4.1.3)
- `role="status"` no strip de sentimento com `aria-label` completo (WCAG 4.1.3)
- `aria-label` na seta de tendência (WCAG 1.1.1)
- `aria-hidden="true"` em spans decorativos
- `motion-safe:animate-bounce` nos dots do typing indicator (WCAG 2.3.3)
- Tokens: `bg-red-light border-red/20`, `bg-ai-light text-ai` (sem hardcoded indigo)

### `modules/agent-assist/components/MessageBubble.tsx` *(já aplicado)*
- `text-[10px]` → `text-xs` no rótulo de autor — corrige falha de tamanho de fonte (WCAG 1.4.4)
- Checkbox de seleção: `w-11 h-11` touch target, `aria-pressed`, `aria-label`, `focus-visible:ring` (WCAG 2.5.5, 4.1.2)
- Sempre focusável via teclado (não `opacity-0` para keyboard users)
- SVG checkmark em vez de ✓ texto literal

### Documentação
- Criado `docs/arcos/accessibility-audit.md` — 33 findings com WCAG criterion, severidade, recomendação
- Criado `docs/arcos/design-system-audit.md` — score 30/100 com sprint sequence

---

## Design System — Sprint 2: Missing Components (2026-05-18)

Componentes compartilhados que substituem ~25 implementações duplicadas espalhadas pelo código.

### Novo: `packages/platform-ui/src/components/ui/Drawer.tsx` (Task #108)

Right slide-over reutilizável. Substitui implementações independentes em DelegarTarefaDrawer, GroupsPage, CurationReview drawer, CalibrationDashboard e outros.
- Props: `isOpen`, `onClose`, `title`, `children`, `footer?`, `size` (sm/md/lg/xl), `disableBackdropClose?`, `description?`
- Body scroll lock via `useEffect`
- Escape key handler via `useEffect`
- Focus trap completo (Tab / Shift+Tab ficam dentro do painel)
- `role="dialog"`, `aria-modal`, `aria-labelledby`, `aria-describedby`
- `z-overlay` (token de z-index = 30)
- Backdrop com `aria-hidden="true"`
- Botão de fechar com `aria-label="Fechar painel"`
- Footer sticky opcional com `border-t border-border`

### Novo: `packages/platform-ui/src/components/ui/Tabs.tsx` (Task #109)

Tab bar horizontal acessível. Substitui implementações independentes em AgentAssistPage (5 tabs), ContactsPage, AuditPage, EvaluationForms e outros.
- Props: `tabs: TabItem[]`, `activeTab`, `onChange`, `variant` (underline | pill), `panels?`, `aria-label?`
- `TabItem`: `key`, `label`, `count?` (badge numérico), `disabled?`
- ARIA completo: `role="tablist"`, `role="tab"`, `role="tabpanel"`, `aria-selected`, `aria-controls`
- Navegação por teclado: ArrowLeft / ArrowRight / Home / End — foco DOM sincronizado
- `tabIndex={0}` apenas na tab ativa (roving tabindex pattern)
- Variante `underline`: underline animado com `::after`, `text-primary` na ativa
- Variante `pill`: background `bg-surface` com sombra `shadow-card` na ativa
- Badge de contagem (`count`) com estilo contextual (ativo vs inativo)
- Exporta tipo `TabItem`

### Novo: `packages/platform-ui/src/components/ui/Textarea.tsx` (Task #110)

Primitive de formulário para texto multi-linha. Espelha API do Input.
- Props: `label?`, `error?`, `hint?`, `maxLength?`, `showCount?` + todos os attrs nativos
- `id` automático via `useId()` para associação label↔input sem props extra
- Character counter com `aria-live="polite"` e `aria-atomic`; vira `text-warning font-semibold` nos últimos 10%
- `aria-invalid` e `aria-describedby` para erro/hint
- `resize-y` por padrão; `rows` padrão 3
- `role="alert"` no error
- Border `border-red bg-red-light/20` no estado de erro

### Novo: `packages/platform-ui/src/components/ui/Checkbox.tsx` (Task #110)

Input de seleção acessível. Substitui `<input type="checkbox">` com styling ad-hoc em AccessPage, ModulePermissionForm, FormsPage e outros.
- Props: `label?`, `error?`, `indeterminate?` + todos os attrs nativos (exceto `type`)
- `indeterminate` aplicado imperativamente via ref callback (não suportado por HTML attribute)
- `focus-visible:ring-2` com offset
- `group-has-[:disabled]` para opacidade do label quando input está disabled
- `aria-invalid` e `aria-describedby` para erro

### Novo: `packages/platform-ui/src/components/ui/Switch.tsx` (Task #110)

Toggle acessível com label e description. Substitui implementações ad-hoc em config pages.
- Props: `checked`, `onChange`, `label?`, `description?`, `disabled?`
- `role="switch"`, `aria-checked`, `aria-describedby`
- Transição `translate-x-5` / `translate-x-0` via CSS transition
- `focus-visible:ring-2` com `ring-offset-2`
- `htmlFor` no label apontando para o botão via id

### Atualizado: `packages/platform-ui/src/components/ui/index.ts`

Barrel agora exporta 17 componentes + 2 tipos (`BadgeVariant`, `TabItem`). Todos os novos componentes incluídos: `Checkbox`, `Drawer`, `Switch`, `Tabs`, `Textarea`.

---

## Design System — Sprint 1: Token Hygiene + Component Foundation (2026-05-18)

Primeira fase do trabalho de design system identificado no audit (score 30/100).
Ver auditoria completa em `docs/arcos/design-system-audit.md`.

### `packages/platform-ui/tailwind.config.ts` — Extensão do sistema de tokens (Task #104)

- Adicionados **tokens de hover**: `primary-dark` (#163F70), `secondary-dark` (#2484BE)
- Adicionado **token de superfície principal**: `surface-muted` (#F9FAFB) — substitui `bg-gray-50` disperso
- Adicionados **tokens de superfície**: `surface`, `surface-alt`, `border`, `border-strong`
- Adicionados **aliases sem camelCase**: `light-gray` (alias de `lightGray`), `muted` (alias de `gray`), `muted-light`, `border`
- Adicionadas **variantes de escala semântica**:
  - `green-light` / `green-text` — backgrounds e texto success
  - `warning-light` / `warning-text` — backgrounds e texto warning
  - `red-light` / `red-text` — backgrounds e texto error
  - `info` / `info-light` / `info-text` — cor info (mapeada ao secondary)
  - `primary-light` — tint azul para seleções e badges
- Adicionados **tokens de domínio** (previamente off-palette):
  - `contested` / `contested-light` / `contested-text` — laranja, estado de contestação
  - `revised` / `revised-light` / `revised-text` — teal, decisão de revisão com ajuste
  - `ai` / `ai-light` / `ai-text` — indigo, indicadores de agente AI
- Adicionada **escala de z-index**: `dropdown(10)`, `sticky(20)`, `overlay(30)`, `modal(40)`, `toast(50)`, `tooltip(60)`
- Adicionada **escala de sombras**: `card`, `panel`, `modal`, `toast`

### `packages/platform-ui/src/components/ui/Button.tsx` — Hover tokens + loading state (Task #105)

- Substituídos `hover:bg-blue-900` / `hover:bg-blue-500` / `hover:bg-gray-200` por tokens: `hover:bg-primary-dark`, `hover:bg-secondary-dark`, `hover:bg-surface-alt`
- Adicionado estado `loading` com spinner inline e `aria-busy`
- Adicionados slots `leftIcon` / `rightIcon` com `aria-hidden`
- `disabled` agora herda de `loading` (ambos bloqueiam interação)
- `opacity-60` no lugar de `disabled:bg-gray-400` para não sobrescrever a cor do variant
- `inline-flex items-center` adicionado para correta exibição de ícones

### `packages/platform-ui/src/components/ui/Badge.tsx` — Cobertura de domínio (Task #106)

- Exportado tipo `BadgeVariant` — 18 variantes total (era 4)
- **Novas variantes genéricas**: `pending`, `processing`, `completed`, `cancelled`, `success`, `warning`, `error`, `info`
- **Novas variantes de avaliação**: `approved`, `contested`, `rejected`, `revised`
- **Novas variantes de tipo de agente**: `ai`, `human`
- Prop `dot?: boolean` — prepend indicador colorido antes do label
- Todas as variantes usam tokens semânticos (`bg-green-light`, `text-green-text`, etc.)

### `packages/platform-ui/src/shell/Shell.tsx` + `Modal.tsx` (Task #107)

- `Shell.tsx`: `bg-gray-50` → `bg-surface-muted` (único token no lugar de classe Tailwind direta)
- `Modal.tsx` reescrito:
  - Prop `size?: 'sm' | 'md' | 'lg' | 'xl'` (antes fixo em `max-w-md`)
  - Prop `loading?: boolean` — overlay de loading sobre o body
  - Prop `disableBackdropClose?: boolean`
  - Escape key handler via `useEffect` + `keydown` listener
  - Body scroll lock (`overflow: hidden`) via `useEffect`
  - Focus management: `dialogRef.focus()` no open via `requestAnimationFrame`
  - `role="dialog"`, `aria-modal="true"`, `aria-labelledby="modal-title"` no container
  - Close button com `aria-label="Fechar"` e `aria-hidden="true"` no SVG
  - Backdrop com `aria-hidden="true"`
  - `z-modal` usando token de z-index em vez de `z-50` hardcoded
  - Sombra `shadow-modal` em vez de `shadow-xl`
  - Bordas usando token `border-border` em vez de `border-lightGray`

### `packages/platform-ui/src/components/ui/PageHeader.tsx`

- `<a href>` → `<Link to>` do react-router-dom (evita reload de página)
- `<nav>` com `aria-label="Breadcrumb"`
- `aria-current="page"` no crumb ativo
- `aria-hidden="true"` no separador `/`
- `text-gray` → `text-muted` (token)

### Novo: `packages/platform-ui/src/components/ui/Alert.tsx`

Componente compartilhado de feedback inline — substitui o padrão `div bg-red-50 border border-red-100` presente em 15+ arquivos.
- 4 variantes: `info`, `success`, `warning`, `error`
- Props: `title?`, `onDismiss?` (botão X), `className`
- `role="alert"` para leitores de tela
- Ícones SVG semânticos por variante
- Usa exclusivamente tokens semânticos do design system

### Novo: `packages/platform-ui/src/components/ui/index.ts`

Barrel de exports para `@/components/ui` — 12 componentes exportados com nomes explícitos (sem `export *`). Inclui export de tipo `BadgeVariant`.

---

## Security & Bug Fixes — Code Review Round 2 (2026-05-18)

Correções críticas identificadas em code review completo do projeto.

### `packages/mcp-server-plughub/src/server.ts` — JWT auth em endpoints UI

- **Task #98**: Adicionado `requireJwtRole()` com verificação de assinatura JWT via `jsonwebtoken.verify()` em todos os endpoints de UI:
  - `GET /api/supervisor_state/:sessionId` → roles: operator, supervisor, admin, developer
  - `POST /api/inject-context/:sessionId` → roles: operator, supervisor, admin, developer
  - `POST /api/force-complete/:sessionId` → roles: supervisor, admin (mais restritivo)
  - `PUT /api/agent-pause` → roles: operator, supervisor, admin
  - `PUT /api/agent-resume` → roles: operator, supervisor, admin
- `extractJwtRole()` (decode sem verificação de assinatura) removido de `supervisor_state` — substituído por `payload["role"]` do JWT verificado.

### `packages/evaluation-api` — Arc 13 bugs

**Task #99** — `contestation_state` errado na finalização de agente AI:
- `router.py`: path `ai_agent` (Fluxo 2) usava `contestation_state="contestation_open"` em `finalize_result` e `emit_evaluation_finalized`. Corrigido para `"auto_finalized"`. Removido campo `initial_state` que nunca era usado.

**Task #100** — `calibration_reviewed` não emitido para decisão `"approved"`:
- `contestation_router.py` `resolve_curation`: `emit_calibration_reviewed` estava dentro de `if calibration_note:` — que é `None` para `approved`. Refatorado para buscar `campaign_id` e `evaluation_instance_id` do DB incondicionalmente; `emit_calibration_reviewed` emitido para todos os status (`approved`, `recalibrated`, `bias_flagged`). Calibration dashboard agora registra aprovações corretamente.

**Task #101** — `max_rounds` nunca verificado, `current_round` nunca incrementado:
- `db.py` `set_contestation_state`: adicionado parâmetro `current_round: int | None = None` com UPDATE condicional.
- `contestation_router.py` `file_contestation`: chama `set_contestation_state` com `current_round=current_round` para persistir o round após cada contestação.
- `contestation_router.py` `submit_review`: busca `contestation_policy.max_rounds` da campanha; verifica `cycles_completed = (review_round - 1) // 2 >= max_rounds`; se excedido → `next_state = "closed_max_rounds"` em vez de `"contestation_open"`; persiste `current_round=review_round`. Loops infinitos de contestação não são mais possíveis.

### `packages/orchestrator-bridge` — Arc 14 Fase C wrap_up_pending

**Task #102** — `wrap_up_pending` nunca bloqueava routing-engine:
- `fire_pool_hooks`: key de `wrap_up_pending` agora usa `f"human-{pool_id}"` diretamente (pattern deterministico idêntico ao que routing-engine usa para indexar pool instances), em vez de `_fixed_pid` do ContextStore (que podia ser `None` se `_write_pre_hook_context` tivesse falhado).
- Formato de `hook_conf` estendido de `"{hook_type}:{target_pool}:{side}"` para `"{hook_type}:{target_pool}:{side}:{origin_pool}"`. Parser atualizado para `split(":", 3)`.
- Cleanup em `process_routed`: usa `_hook_origin_pool` (4° campo do `hook_conf`) para derivar `human-{origin_pool}` — não depende mais de ContextStore. Condição adicionada: `completed_hook_type == "on_human_end"` antes de limpar o flag.

---

## Arc 13 — Evaluation Review, Contestation & Calibration — Fase H: Feedback Loop RAG / Curation Module (2026-05-18)

Fecha o loop completo de melhoria contínua do avaliador AI: `sampling_engine.py` faz curadoria amostral pós-`evaluation_finalized` para Fluxo 2 (AI avaliado), `CuradoriaPage` expõe a fila de curadoria ao curador humano, e `CalibrationNote` é publicada no knowledge namespace do `mcp-server-knowledge` para fechar o RAG feedback loop ao `agente_avaliacao_v1`.

### `packages/evaluation-api` — sampling engine

**`sampling_engine.py`** (novo):
- `run_curation_sampling(pool, *, instance_id, tenant_id, campaign_id, normalized_score)` — entry point async chamado em background task após `evaluation_finalized` para Fluxo 2.
- Helpers inline sem circular import: `_get_campaign_score_stats`, `_count_finalized_instances`, `_count_na_criteria`.
- Avalia 5 regras (pula `reviewer_signal` explicitamente para Fluxo 2): `score_extremes`, `random_baseline` (hash determinístico do `instance_id`), `deploy_baseline`, `score_outlier` (min 5 amostras), `na_excess`.
- Trigger composto: regras ativas concatenadas como string, ex. `"score_extremes,random_baseline"`.
- Cria um único `CurationReview` por instância (insert-or-skip se já existe).

**`router.py`** — após `emit_evaluation_finalized` no path `ai_agent`:
- Importou `asyncio` e `run_curation_sampling`.
- `asyncio.create_task(run_curation_sampling(...), name=f"curation-sampling-{body.instance_id}")` — não bloqueia resposta HTTP.

**`config.py`**: campo `knowledge_api_url: str = "http://localhost:3401"` adicionado.

### `packages/evaluation-api` — contestation_router (CalibrationNote → KB)

**`contestation_router.py`** — `resolve_curation` endpoint:
- Após criar/resolver `CurationReview`, se `status == "recalibrated"` ou `status == "bias_flagged"` com `calibration_note_text`:
  - `httpx.AsyncClient.post(knowledge_api_url + "/v1/knowledge/snippets")` com namespace `evaluation:calibration:{campaign_id}`, conteúdo + metadados de dimensão/severidade.
  - On success (200/201): `mark_calibration_note_published(note_id, kb_source_ref)` + emite `calibration_note_published` ao `evaluation.events`.
  - Erros de I/O capturados — `kb_published=False` retornado sem falhar a ação do curador.
- Emite `calibration_reviewed` ao topic `calibration.events` em todos os casos.
- Retorna `{ ..., "kb_published": bool }`.

**`db.py`** — `list_curation_reviews` reescrita:
- Sempre faz JOIN com `evaluation.instances` para retornar `campaign_id`.
- Correlated subquery retorna o `calibration_signal` mais recente do `pre_reviewer_ai` nos threads de contestação.
- Parse defensivo: converte `calibration_signal` de JSON string para dict quando necessário (asyncpg JSONB variante).

### `packages/platform-ui` — hooks + CuradoriaPage

**`evaluation-hooks.ts`** — novos exports:
- Interfaces: `CurationReview`, `CurationResolvePayload`.
- `useCurationQueue(tenantId, opts, pollMs)` — fetch `GET /evaluation/curation-reviews`, polling configurável (padrão 15s), retorna `{ reviews, total, loading, error, reload }`.
- `resolveCuration(reviewId, tenantId, userId, payload)` — `POST /evaluation/curation-reviews/{id}/resolve`.

**`CuradoriaPage.tsx`** (novo — `/evaluation/curadoria`):
- KPI strip: Aguardando, Total, label do filtro ativo.
- Filtros: status (pending/approved/recalibrated/bias_flagged), campanha.
- `CurationCard`: trigger badges coloridos, preview do `calibration_signal` do revisor AI, 3 botões (Aprovar / Recalibrar / Viés).
- `RecalibrateDrawer`: pré-preenche observação do sinal AI; campos `noteText`, `curatorNotes`, `dimensionId`, `evaluatorId`, `skillVersion`, `severity`. Submit → `resolveCuration` com status `recalibrated` ou `bias_flagged`.
- Polling 15s.

**`routes.tsx`**: `{ path: 'evaluation/curadoria', element: <CuradoriaPage /> }`.

**`Sidebar.tsx`**: nav item `{ label: t('nav.eval.curadoria'), href: '/evaluation/curadoria', icon: '🔍', roles: ['supervisor', 'admin'] }`.

**i18n** (`shell.json` en + pt-BR): chave `nav.eval.curadoria` → `"Curation"` / `"Curadoria"`.

→ Ver [`docs/arcos/arc13-review-contestation.md`](docs/arcos/arc13-review-contestation.md)

---

## Arc 13 — Evaluation Review, Contestation & Calibration — Fase G: Calibration Dashboard (2026-05-18)

Implementa o pipeline completo de dados e a tela de Calibration Dashboard: Kafka consumer para `calibration.events`, ClickHouse DDL, endpoint `GET /reports/evaluator-calibration` na analytics-api, hook `useEvaluatorCalibration` e página `CalibrationDashboard` na platform-ui.

### `packages/analytics-api` — ClickHouse DDL + consumer

**`clickhouse.py`**:
- `_DDL_CALIBRATION_EVENTS` — nova tabela `calibration_events` (ReplacingMergeTree por `(tenant_id, event_id)`): `event_id`, `tenant_id`, `campaign_id`, `evaluator_id`, `skill_version`, `decision` (LowCardinality: approved/recalibrated/bias_flagged), `dimension_id`, `severity`, `curator_id?`, `note_id?`, `event_time`, `date`.
- `_calibration_event_row(d)` — row builder para inserção.
- `AnalyticsStore.insert_calibration_event()` + `_CALIBRATION_EVENT_COLS`.
- Adicionada à lista `_ALL_DDL`.

**`models.py`**:
- `parse_calibration_event(payload)` — parser do topic `calibration.events`. Aceita `calibration_reviewed` (retorna row para ClickHouse); ignora `calibration_note_published` (informacional).

**`consumer.py`**:
- Topic `calibration.events` adicionado a `_TOPICS` e `_PARSERS`.
- `_write_row`: routing `calibration_events` → `store.insert_calibration_event`.
- Docstring atualizado.

### `packages/analytics-api` — endpoint

**`reports_query.py`** — `query_evaluator_calibration` (async wrapper + `_fetch_evaluator_calibration` sync):
- Parâmetros: `campaign_id`, `evaluator_id`, `skill_version`, `granularity` (day/week).
- Query time-series: `calibration_score = countIf(decision='approved') * 100 / count()` por `(period, skill_version, evaluator_id)`.
- Query summary: agregação total do período.
- Retorna: `{ data, summary, meta }`.

**`reports.py`** — `GET /reports/evaluator-calibration`:
- Query params: `tenant_id`, `from_dt`, `to_dt`, `campaign_id`, `evaluator_id`, `skill_version`, `granularity`.
- Delega a `query_evaluator_calibration`.

### `packages/platform-ui` — hook + página

**`evaluation-hooks.ts`** — `useEvaluatorCalibration`:
- Interfaces exportadas: `CalibrationPoint`, `CalibrationSummary`, `CalibrationResult`.
- Hook com `fetch` a `${ANALYTICS_BASE}/evaluator-calibration`, estado `{ data, summary, meta, loading, error, reload }`, polling opcional.

**`CalibrationDashboard.tsx`** (novo — `/evaluation/calibration`):
- Filtros: campanha (select), evaluator_id (input), granularidade (day/week).
- KPI strip: Calibration Score geral, Aprovadas %, Recalibradas %, Viés %.
- LineChart (Recharts): `calibration_score (%)` × tempo, uma série por `skill_version`, paleta rotativa de 7 cores. ReferenceLine em 90% (verde tracejada).
- Tabela de dados brutos com score colorido (verde ≥ 90%, amarelo ≥ 75%, vermelho abaixo).
- Estado vazio, loading e erro tratados.

**`routes.tsx`**: rota `evaluation/calibration` → `<CalibrationDashboard />`.

**`Sidebar.tsx`**: nav item "Calibração" (📐) em `/evaluation/calibration`, roles `supervisor|admin`.

**i18n** `shell.json` (en + pt-BR): chave `nav.eval.calibration`.

---

## Arc 13 — Evaluation Review, Contestation & Calibration — Fase F: Campaign Config UI + Curation Sampling Rules (2026-05-18)

Implementa a interface de configuração de campanha para Arc 13 na `CampaignsPage` da platform-ui: novos campos na `ContestationPolicy`, editor de regras de curadoria amostral (`CurationSamplingRule`) e painel de detalhe de campanha atualizado.

### `packages/platform-ui/src/types/index.ts`

Novos campos opcionais em `ContestationPolicy` (Arc 13):
- `reviewer_type?: 'ai' | 'human' | 'ai_then_human'` — roteamento de resolução de contestação.
- `contest_deadline_hours?: number` — prazo em horas para o avaliado contestar.
- `use_business_hours?: boolean` — usa horário comercial (calendar-api) nos deadlines.
- `pre_review_enabled?: boolean` — habilita gate de qualidade por AI pré-publicação.
- `pre_review_agent_pool?: string | null` — pool do agente pré-revisor.

Novos tipos de regras de curadoria amostral:
- `CurationRuleType` — union `'score_extremes' | 'deploy_baseline' | 'score_outlier' | 'na_excess' | 'random_baseline' | 'reviewer_signal'`.
- `CurationRuleParams` — campos opcionais: `threshold_low/high`, `sample_pct`, `sample_n`, `std_devs`, `na_threshold_pct`, `rate`.
- `CurationSamplingRule` — entidade completa com `rule_id?`, `campaign_id`, `rule_type`, `enabled`, `priority`, `params`.

### `packages/platform-ui/src/api/evaluation-hooks.ts`

Novos hooks Arc 13 Fase F:
- `useCurationSamplingRules(campaignId)` — `GET /v1/evaluation/campaigns/{id}/sampling-rules`; retorna `{ rules, loading, error, reload }`.
- `saveCurationSamplingRules(campaignId, rules, token?)` — `PUT /v1/evaluation/campaigns/{id}/sampling-rules`; retorna a lista salva.

### `packages/platform-ui/src/modules/evaluation/CampaignsPage.tsx`

**Constantes novas:**
- `REVIEWER_TYPE_OPTIONS` — 3 opções: `human`, `ai`, `ai_then_human` com labels descritivos.
- `DEFAULT_CURATION_RULES` — 6 regras pré-configuradas (score_extremes e reviewer_signal habilitadas por padrão; score_outlier e deploy_baseline habilitadas; na_excess e random_baseline desabilitadas).

**Novos componentes:**
- `CurationRuleRow` — editor de uma regra: toggle enabled, campo priority, inputs condicionais por `rule_type` (threshold_low/high para score_extremes, sample_n para deploy_baseline, std_devs+sample_pct para score_outlier, na_threshold_pct para na_excess, rate para random_baseline — reviewer_signal sem params).
- `CurationSamplingRulesEditor` — lista de `CurationRuleRow` em ordem de priority com drag-free reordering via flechas.
- `CurationSamplingRulesDetailPanel` — painel de detalhe de campanha: modo leitura (bullet list por regra com resumo de params) + modo edição (chama `saveCurationSamplingRules`). Exibido apenas para campanhas com `review_workflow_skill_id === 'skill_revisao_treplica_v1'`.

**`CreateModal` estendido (Arc 13 fields):**
- Novos campos de estado: `contestDeadlineHours` (default '72'), `reviewerType` ('ai_then_human'), `useBusinessHours`, `preReviewEnabled`, `preReviewPool`, `curationRules` (DEFAULT_CURATION_RULES), `showCurationRules`.
- `isArc13Skill = workflowSkillId === 'skill_revisao_treplica_v1'` — gatea seções Arc 13 no modal.
- Quando `isArc13Skill`: exibe select `reviewer_type`, campos de deadline com toggle `use_business_hours`, toggle `pre_review_enabled` + input do pool, acordeão "Regras de curadoria amostral" com `CurationSamplingRulesEditor`.
- `submit()`: passa novos campos em `contestation_policy`; após criar campanha, chama `saveCurationSamplingRules` quando `isArc13Skill && curationRules.length > 0`.

**Painel de detalhe atualizado:**
- Card de contestation policy exibe Arc 13 fields como grid de badges coloridos: reviewer_type (azul), pre_review (verde/cinza), use_business_hours (teal), auto_lock (âmbar).
- `<CurationSamplingRulesDetailPanel>` renderizado abaixo do card de política para campanhas Arc 13.

---

## Arc 13 — Evaluation Review, Contestation & Calibration — Fase E: Human Review UX (2026-05-18)

Implementa a interface de revisão humana com threads por dimensão na `AvaliacoesPage` da platform-ui. Suporte completo a Arc 13 (dimension threads) com fallback transparente para Arc 6 (criterion list).

### `packages/platform-ui/src/types/index.ts`

Novos tipos Arc 13:
- `DimensionState` — union type dos estados visuais por dimensão (`neutral|pre_reviewed|contested|upheld|revised|timeout`).
- `EvidenceEntry` — evidência individual em um thread entry (`stream_entry_id`, `excerpt`, `relevance_note`).
- `ContestationThreadEntry` — entrada append-only de um thread: `round`, `author_role`, `action?`, `score`, `justification`, `evidence_entries[]`, `submitted_at`.
- `ContestationThread` — thread completo de uma dimensão: `dimension_id`, `dimension_label?`, `current_state`, `original_score`, `current_score`, `entries[]`.
- `InstanceThreads` — resposta de `GET /v1/evaluation/instances/{id}/threads`.
- `HumanDimensionDecision` — payload de decisão por dimensão para o revisor humano.
- `HumanReviewResponse` — resposta de `POST /v1/evaluation/instances/{id}/review`.
- `DimensionContestationPayload` / `DimensionContestationResponse` — payload/resposta de `POST /v1/evaluation/instances/{id}/contest`.

### `packages/platform-ui/src/api/evaluation-hooks.ts`

Novos hooks e funções Arc 13:
- `fetchContestationThreads(instanceId, accessToken?)` — `GET /v1/evaluation/instances/{id}/threads`, normaliza resposta.
- `useContestationThreads(instanceId, accessToken?, pollMs?)` — React hook com polling opcional; retorna `{ data, loading, error, reload }`.
- `submitHumanReview(instanceId, body, jwtToken)` — `POST /v1/evaluation/instances/{id}/review` para o revisor humano.
- `submitDimensionContestation(instanceId, body, jwtToken)` — `POST /v1/evaluation/instances/{id}/contest` para o agente avaliado.

### `packages/platform-ui/src/modules/evaluation/AvaliacoesPage.tsx`

**Novos componentes Arc 13:**
- `DimensionStateIndicator` — dot colorido + label por estado (`DIM_STATE_META` map).
- `DimensionThreadCard` — card expansível por dimensão: header com estado/score, entradas por round com `ROUND_ROLE_LABELS`, evidências formatadas. Expandido por padrão quando `contested` ou `revised`.
- `HumanReviewPanel` — revisor humano decide por dimensão contestada: upheld/revised radio, `score_override` (apenas revised, 0–10), `justification` (≥ 20 palavras, contador em tempo real). Chama `submitHumanReview`.
- `DimensionContestPanel13` — agente avaliado contesta por dimensão (apenas `neutral` ou `pre_reviewed`): checkbox por dimensão, justification (≥ 10 palavras). Chama `submitDimensionContestation` com anti-replay `round`.

**`DetailPanel` atualizado:**
- Chama `useContestationThreads(result.instance_id)` em cada abertura.
- Detecta `isArc13 = threads.length > 0` — ativo automaticamente quando a instância tem threads.
- Modo Arc 13: exibe `DimensionThreadCard` list + `HumanReviewPanel` (review) ou `DimensionContestPanel13` (contest).
- Modo Arc 6 (fallback): mantém `CriterionRow` list + `ReviewPanel`/`ContestPanel` sem alteração.
- Badge "Arc 13" no header quando threads disponíveis.

**Invariante de compatibilidade**: instâncias criadas antes do Arc 13 (sem threads) continuam funcionando exatamente como antes — sem alteração de comportamento.

---

## Arc 13 — Evaluation Review, Contestation & Calibration — Fase D: Revisor Pós-Contestação + Workflow (2026-05-18)

Implementa o árbitro AI pós-contestação (`agente_revisor_v1`) e o motor de estado de revisão (`skill_revisao_treplica_v1` v2.0) que roteia para AI ou humano com base em `reviewer_type` da campanha.

### `mcp-server-plughub` — `src/tools/evaluation.ts`

**`evaluation_review_submit`** (novo):
- Input: `session_token`, `instance_id`, `dimension_decisions[]`, `reviewer_id?`.
- `DimensionDecisionSchema`: `dimension_id`, `decision` ("upheld"|"revised"), `score_override?` (obrigatório se revised), `evidence_entries[]?` (obrigatório se revised), `justification` (obrigatório).
- Chama `POST /v1/evaluation/instances/{id}/review` na evaluation-api.
- Retorna: `dimensions_upheld`, `dimensions_revised`, `contestation_state`, `current_round`, `finalized`.

### `skill-flow-engine` — `skills/agente_revisor_v1.yaml` (novo — v1.0)

Árbitro AI pós-contestação. Fluxo de 5 steps: `get_context` → `get_threads` → `filter_contested` → `review` (reason, prompt `post_contestation_rubric_v1`) → `submit_review`.

- Só arbitra dimensões com `round=2` (contestadas pelo humano avaliado). Não toca dimensões não contestadas.
- `output_schema.dimension_decisions[]`: `decision` (upheld|revised) + `score_override?` + `evidence_entries[]?` + `justification`.
- `decision=revised` obriga `score_override` diferente do original + `evidence_entries` (mínimo 1).
- `decision=upheld` requer apenas `justification` explicando por que a contestação não é procedente.
- Lê `calibration_notes` antes de decidir (RAG de calibração).
- Não emite `calibration_signal` — o revisor árbitro não é calibrador.
- `complete_skip` quando sem dimensões contestadas (edge case).

### `skill-flow-engine` — `skills/skill_revisao_treplica_v1.yaml` (v1.0 → v2.0)

Motor de estado do ciclo de revisão. Atualizado com roteamento Arc 13.

**Novo fluxo v2.0** (ciclo por round):
1. `aguardar_contestacao` (suspend, `contest_deadline_hours`) — aguarda o agente humano contestar; timeout → `congelar_resultado`.
2. `verificar_contestacao` (choice) — se `review_decision="contested"` → incrementa round; se não → `encerrar_aprovado`.
3. `verificar_limite_rounds` (choice) — `current_round > max_rounds` → `congelar_resultado`.
4. `rotear_revisor` (choice) — `reviewer_type="ai"` ou `"ai_then_human"` → `dispatch_revisor_ai`; default → `aguardar_revisao_humana`.
5. `dispatch_revisor_ai` (task, `skill_revisao_v1`) — A2A para `agente_revisor_v1`; `on_failure` → `fallback_para_humano`.
6. `fallback_para_humano` (choice) — só suspende para humano se `reviewer_type="ai_then_human"`; caso contrário `congelar_resultado`.
7. `aguardar_revisao_humana` (suspend, `review_deadline_hours`) — deadline para revisor humano; timeout → `congelar_resultado`.
8. `aguardar_proxima_contestacao` (suspend) — resultado publicado; aguarda próxima contestação do avaliado; timeout → `congelar_resultado`; recomeça o ciclo.

Contexto esperado no ContextStore (escrito pela evaluation-api antes do trigger): `instance_id`, `result_id`, `reviewer_type`, `max_rounds`, `contest_deadline_hours`, `review_deadline_hours`, `use_business_hours`, `current_round` (init=0).

---

## Arc 13 — Evaluation Review, Contestation & Calibration — Fase C: Revisor AI Pré-Publicação (2026-05-18)

Implementa o `agente_pre_revisor_v1` — gate de qualidade que atua antes do resultado ser publicado ao agente humano avaliado. Verifica evidências, calibração de notas e emite sinais de calibração para padrões sistemáticos do avaliador.

### `mcp-server-plughub` — `src/tools/evaluation.ts`

**`evaluation_threads_get`** (novo):
- Input: `session_token`, `instance_id`.
- Chama `GET /v1/evaluation/instances/{id}/threads` na evaluation-api.
- Retorna `threads[]` — ContestationThreads round=1 do avaliador por dimensão.
- Usado pelo `agente_pre_revisor_v1` para ler o que o avaliador produziu antes de revisar.

**`evaluation_pre_review_submit`** (novo):
- Input: `session_token`, `instance_id`, `dimension_reviews[]`, `calibration_signal?`.
- `DimensionReviewSchema`: `dimension_id`, `action` ("approve"|"adjust"), `score_override?`, `revised_evidence[]?`, `justification` (obrigatório).
- `CalibrationSignalPreReviewSchema`: `severity` ("low"|"medium"|"high"), `dimension_id`, `observation`.
- Chama `POST /v1/evaluation/instances/{id}/pre-review` na evaluation-api.
- `calibration_signal` presente → `curation_review_created: true` na resposta (assíncrono).
- Retorna: `dimensions_adjusted`, `dimensions_approved`, `contestation_state`, `curation_review_created`.

### `skill-flow-engine` — `skills/agente_pre_revisor_v1.yaml` (novo — v1.0)

Fluxo de 5 steps: `get_context` → `get_threads` → `check_has_threads` → `review` (reason) → `submit_pre_review`.

- `get_context`: `evaluation_context_get` — ReplayContext + form + calibration_notes (RAG).
- `get_threads`: `evaluation_threads_get` — ContestationThreads round=1 do avaliador.
- `check_has_threads`: choice — se sem threads, avança para `complete_skip` (avaliação legacy sem dimension_threads).
- `review` (`pre_review_rubric_v1`): LLM revisa cada dimensão:
  - Lê `evaluator_threads`, `replay_events`, `evaluation_form`, `knowledge_snippets`, `calibration_notes`.
  - `output_schema.dimension_reviews[]`: `action` + `score_override?` + `revised_evidence[]?` + `justification`.
  - `output_schema.calibration_signal?`: emitido apenas para padrões sistemáticos do avaliador (não discordância pontual).
  - `action=adjust` obriga `score_override` + `revised_evidence[]` (mínimo 1 entry).
- `submit_pre_review`: `evaluation_pre_review_submit` — persiste threads + avança estado da instância.

**Invariantes da skill:**
- Nunca abre contestação — é gate pré-publicação, não árbitro.
- `calibration_signal` emitido apenas para padrões sistemáticos (severidade low/medium/high).
- `complete_skip` quando threads vazios — não bloqueia o fluxo para avaliações sem dimension_threads.

---

## Arc 13 — Evaluation Review, Contestation & Calibration — Fase B: Session Metrics + Evidence Threads (2026-05-18)

Completa o pipeline de avaliação com extração automática de métricas de sessão, threads de evidência estruturadas por dimensão, e integração do agente avaliador com notas de calibração do curador.

### `evaluation-api`

**`db.py`**:
- `evaluation.instances`: novo campo `session_metrics JSONB` — armazena métricas extraídas pelo `SessionMetricsExtractor` (tempo, mensagens, escalação, custo LLM).
- `set_instance_session_metrics()`: nova função CRUD para persistir métricas após `session_closed`.

**`session_metrics_extractor.py`** (novo):
- `SessionMetricsExtractor`: computa `session_metric.*` para uma `EvaluationInstance` após `session_closed`.
  - `_compute_time_metrics`: `first_response_time_s`, `total_session_duration_s` — lê `stream_events` no PostgreSQL.
  - `_compute_message_metrics`: `total_messages`, `agent_messages`, `customer_messages`, percentuais, `avg_agent_message_length`, `turns_to_resolution`.
  - `_compute_outcome_metrics`: `escalated` (bool), `escalation_reason` — lê evento `agent_done` com `handoff_reason`.
  - `_compute_llm_metrics`: `llm_calls_total`, `tokens_input_total`, `tokens_output_total` — lê `usage_events`.
  - Todas as métricas são best-effort: falha individual não aborta as demais.
- `compute_auto_criterion_score()`: interpolação linear entre `threshold_pass` e `threshold_fail` para critérios `auto_computed`.
- `fill_auto_computed_criteria()`: preenche `criterion_responses` com scores auto-calculados antes do submit, eliminando a necessidade de o LLM avaliar esses critérios.

**`router.py`**:
- `IngestBody`: novos campos `dimension_threads: list[dict]` e `evaluated_agent_type: str`.
- `ingest_result()`:
  - Cria `ContestationThread` round=1 para cada dimensão em `dimension_threads` (com `evidence_entries[]`).
  - Fluxo 2 (ai_agent): chama `finalize_result()` + emite `evaluation_finalized` imediatamente.
  - Fluxo 1 (human_agent): verifica `pre_review_enabled` → define estado `pre_review_pending` ou `contestation_open`.
  - Retorna `contestation_threads_created`, `contestation_state`, `evaluated_agent_type`.

### `mcp-server-plughub`

**`src/tools/evaluation.ts`**:
- `EvidenceEntryInputSchema` (novo): `stream_entry_id`, `excerpt`, `relevance_note`.
- `DimensionThreadInputSchema` (novo): `dimension_id`, `score`, `justification`, `evidence_entries[]` (mínimo 1).
- `EvaluationSubmitInputSchema`: novos campos `dimension_threads[]` e `evaluated_agent_type`.
- Handler `evaluation_submit`: repassa `dimension_threads` e `evaluated_agent_type` no payload enviado ao ingest.
- Handler `evaluation_context_get`: busca `CalibrationNotes` publicadas da evaluation-api (`/v1/evaluation/calibration-notes?published_to_kb=true`) e retorna em `calibration_notes[]` — consumidas pelo agente avaliador via RAG.

### `skill-flow-engine`

**`skills/agente_avaliacao_v1.yaml`** (v2.0 → v3.0):
- `evaluate` step (`evaluation_rubric_v2` → `evaluation_rubric_v3`):
  - Nova instrução: ignorar critérios com `type=auto_computed` (preenchidos pelo `SessionMetricsExtractor`).
  - Novo input: `calibration_notes` do `eval_context` — guia o LLM com feedback anterior do curador.
  - Novo campo `output_schema.dimension_threads[]`: thread por dimensão com `dimension_id`, `score`, `justification` e `evidence_entries[]` (stream_entry_id + excerpt + relevance_note, mínimo 1 por dimensão).
- `submit_result` step: passa `dimension_threads` e `evaluated_agent_type` para `evaluation_submit`.
- Comentários atualizados para refletir dois fluxos (ai_agent → finalização imediata; human_agent → contestação).

---

## Arc 13 — Evaluation Review, Contestation & Calibration — Fase A: Data Model (2026-05-18)

Implementa o modelo de dados e endpoints base para o ciclo completo de qualidade: contestação estruturada por dimensão para agentes humanos, curadoria amostral para agentes AI, e infraestrutura de calibração contínua.

### `@plughub/schemas` — `src/evaluation.ts`

- `EvaluationCriterionTypeSchema`: novo valor `"auto_computed"` — critérios computados automaticamente de `session_metric.*` sem LLM.
- `EvaluationCriterionSchema`: novos campos `dimension_label`, `computation_source`, `threshold_pass`, `threshold_fail`, `comparison` (suporte ao tipo `auto_computed`).
- `ContestationPolicySchema` (novo): `max_rounds` (padrão 3, máx 5), `contest_deadline_hours`, `use_business_hours`, `reviewer_type` ("ai"|"human"|"ai_then_human"), `pre_review_enabled`, `pre_review_agent_pool`, `review_deadline_hours`.
- `ContestationStateSchema` (novo): state machine de contestação — 7 estados: `pre_review_pending`, `contestation_open`, `under_review`, `timeout_contestation`, `timeout_review`, `closed_upheld`, `closed_revised`.
- `EvidenceEntrySchema` (novo): `stream_entry_id`, `excerpt`, `relevance_note` — formato Arc 13, diferente do `EvidenceRefSchema` Arc 6.
- `CalibrationSignalSchema` (novo): `severity`, `dimension_id`, `observation`, `evaluator_id`, `skill_version`.
- `ContestationThreadSchema` (novo): registro append-only por dimensão com `round`, `author_type`, `decision`, `score_override`, `evidence_entries`, `calibration_signal`.
- `CurationReviewSchema` + `CurationReviewStatusSchema` (novos): fila de curadoria com `trigger`, `curator_id`, `status`, `calibration_note_id`.
- `CalibrationNoteSchema` (novo): nota de calibração do avaliador com `published_to_kb`.
- `CurationSamplingRuleSchema` + `CurationSamplingRuleTypeSchema` (novos): 6 regras configuráveis por campanha.
- Novos Kafka events: `EvalFinalizedSchema` (`evaluation_finalized`), `CalibrationReviewedSchema` (`calibration_reviewed`), `CalibrationNotePublishedSchema` (`calibration_note_published`).

### `evaluation-api`

**`db.py`** — migrations DDL (idempotentes):
- `evaluation.campaigns`: `pre_review_enabled BOOLEAN`, `pre_review_agent_pool TEXT`.
- `evaluation.results`: `contestation_state TEXT`, `pre_review_complete BOOLEAN`, `evaluated_agent_type TEXT`, `finalized_at TIMESTAMPTZ`, `final_score NUMERIC`, `process_duration_ms BIGINT`.
- Nova tabela `evaluation.contestation_threads` (append-only, indexed por instance+dimension+round).
- Nova tabela `evaluation.curation_reviews` (fila de curadoria, indexed por tenant+status).
- Nova tabela `evaluation.calibration_notes` (notas do curador, indexed por campaign+evaluator).
- Nova tabela `evaluation.curation_sampling_rules` (regras por campanha, indexed por campaign+priority).

**`db.py`** — funções CRUD:
- `create_contestation_thread`, `list_contestation_threads`
- `create_curation_review`, `resolve_curation_review`, `list_curation_reviews`
- `create_calibration_note`, `mark_calibration_note_published`, `list_calibration_notes`
- `create_sampling_rule`, `list_sampling_rules`, `update_sampling_rule`, `delete_sampling_rule`
- `finalize_result`, `set_contestation_state`

**`contestation_router.py`** (novo — `contestation_router` registrado em `main.py`):
- `GET  /v1/evaluation/instances/{id}/threads` — lista threads por dimensão
- `POST /v1/evaluation/instances/{id}/contest` — agente humano contesta dimensão
- `POST /v1/evaluation/instances/{id}/review` — revisor submete decisão upheld/revised
- `POST /v1/evaluation/instances/{id}/pre-review` — revisor AI pré-publicação; se `calibration_signal` → cria `CurationReview` automaticamente
- `GET  /v1/evaluation/curations` — fila de curadoria (filtros: campaign_id, status)
- `POST /v1/evaluation/curations/{id}/resolve` — curador aprova/recalibra/flag viés; cria `CalibrationNote` e emite `calibration_reviewed`
- `GET  /v1/evaluation/calibration-notes` — histórico de notas de calibração
- `POST /v1/evaluation/calibration-notes/{id}/publish` — marca nota como publicada no KB; emite `calibration_note_published`
- `GET  /v1/evaluation/campaigns/{id}/sampling-rules` — lista regras de curadoria
- `POST /v1/evaluation/campaigns/{id}/sampling-rules` — cria regra
- `PUT  /v1/evaluation/campaigns/{id}/sampling-rules/{rid}` — atualiza regra
- `DELETE /v1/evaluation/campaigns/{id}/sampling-rules/{rid}` — remove regra

**`kafka_emitter.py`** — novos emitters:
- `emit_calibration_reviewed` → topic `calibration.events`
- `emit_calibration_note_published` → topic `evaluation.events`
- `emit_evaluation_finalized` → topic `evaluation.events`

**`main.py`**: `contestation_router` registrado no app FastAPI.

---

## Arc 10 — Journey — Fase F: Split de Jornadas (2026-05-18)

Permite extrair sessões collect de uma journey para uma nova journey independente. Caso de uso: durante a execução de um processo, descobre-se que algumas sessões pertencem a um processo diferente.

### `@plughub/schemas` — `src/journey.ts`

- `JourneySchema`: novo campo `split_from_journey_id: UUID nullable` rastreia proveniência de journeys derivadas de split.
- `JourneyEventTypeSchema`: `journey_split` removido o comentário `(future)` — agora ativo.
- `JourneyEventSchema`: novos campos `source_journey_id`, `new_journey_id`, `session_ids[]`, `session_count`, `split_from_journey_id`.
- Novos schemas de tool: `JourneySplitInputSchema` / `JourneySplitOutputSchema`.

### `workflow-api`

**`db.py`**:
- Migration idempotente: `ALTER TABLE workflow.journeys ADD COLUMN IF NOT EXISTS split_from_journey_id UUID`.
- `_row_to_journey`: serializa o novo campo.
- `db_split_journey()`: cria nova journey com `split_from_journey_id`, re-linka `collect_instances` via JOIN em `instances.session_id`.

**`kafka_emitter.py`**: `emit_journey_split()` publica `journey_split` no topic `journey.events` com `source_journey_id`, `new_journey_id`, `session_ids[]`, `session_count`.

**`journey_router.py`**:
- `GET /v1/journeys/{id}/collect-sessions`: retorna session IDs das collect_instances da journey para o picker do Monitor.
- `POST /v1/journeys/{id}/split`: valida restrições (origin protegida, somente collect sessions, merged read-only), cria nova journey, re-linka sessões, opcionalmente dispara novo workflow, publica `journey_split` no Kafka.

### `mcp-server-plughub` — `src/tools/journey.ts`

Nova tool `journey_split(journey_id, session_ids[], skill_id?, metadata?)`. Chama `POST /v1/journeys/{id}/split`. Interceptada pelo McpInterceptor (auditoria automática).

### `analytics-api`

- `models.py`: `_JOURNEY_STATUS_MAP` inclui `journey_split: None` (audit event sem transição de status). `parse_journey_event` retorna `split_from_journey_id`, `new_journey_id`, `session_count`.
- `clickhouse.py`: DDL de `journey_events` com 3 novas colunas (`split_from_journey_id`, `new_journey_id`, `session_count`). `_JOURNEY_EVENT_COLS` e `_journey_event_row` atualizados.

### `platform-ui` — `ProcessosPage.tsx`

- Novo componente `SplitDrawer`: modal com checklist de sessões collect (carregadas via `GET /v1/journeys/{id}/collect-sessions`), campo opcional de `skill_id`, validação client-side (origin bloqueada). Após split: fecha drawer, atualiza lista e seleciona a nova journey automaticamente.
- Botão "✂️ Separar em nova jornada" no footer do painel de detalhe (visível para journeys `active`/`suspended`).

### Invariants

- `journey_split` é irreversível — use `journey_merge` para reagrupar.
- `origin_session_id` da journey origem é protegido — retorna `400 origin_session_cannot_be_split` se incluído.
- Somente sessões collect (`collect_instances.journey_id = source_journey_id`) são elegíveis.
- Journey com `status: merged` retorna `409`.

---

## workflow-api — Fix: timeout scanner rodava antes do schema ser aplicado (2026-05-17)

### Problema
Em `main.py`, a chamada `ensure_schema(pool)` estava dentro de um `try/except` que silenciava a exceção com `logger.warning(...)` e continuava normalmente. Se o PostgreSQL ainda não estivesse pronto para aceitar DDL no momento em que o pool conectava (race condition frequente em docker-compose), o schema nunca era criado. Após `timeout_scan_interval_s` segundos (default 60), o scanner rodava `db_timeout_expired_instances()` e recebia `relation "workflow.instances" does not exist`.

### Fix — `packages/workflow-api/src/plughub_workflow_api/main.py`
Substituído o `try/except` silencioso por um retry loop com backoff exponencial (até 7 tentativas, 2s × attempt entre cada uma, total ~56s). Se todas as tentativas falharem, o lifespan levanta `RuntimeError` — o container falha e o Docker policy de restart reinicia até o PG estar pronto. O scanner só é criado **após** `ensure_schema()` ter sido confirmado com sucesso.

---

## Arc 14 — Pós-Atendimento: Segmentos Independentes — Fase C: Bloqueio do Agente Humano (2026-05-17)

Impede que o routing-engine aloque um novo contato ao agente humano enquanto o segmento de wrap-up ainda está ativo.

### `routing-engine` — `src/plughub_routing/registry.py`

- Documentação do novo Redis key `{tenant_id}:instance:{instance_id}:wrap_up_pending` adicionada ao header.
- **`get_ready_instances()`**: antes de incluir um candidato com `state=ready`, verifica a existência de `{tenant_id}:instance:{instance_id}:wrap_up_pending`. Se presente → `continue` (agente excluído do pool de candidatos). Falha no check é não-fatal (agente incluído como fallback).

### `orchestrator-bridge` — `main.py`

**`fire_pool_hooks()`** — quando `hook_type=on_human_end` e `hook_side=agent`:
  - Após registrar o `_fixed_pid` no posatt participants SET (Fase B), escreve `{tenant_id}:instance:{human_instance_id}:wrap_up_pending = session_id` com TTL = `_HOOK_TIMEOUT_S + 300` (auto-expira como safety net em caso de crash da bridge).

**`process_routed()` — hook completion** — quando `_hook_side == "agent"`:
  - Após o publish do targeted `session.closed` (Fase B), lê `session.human_agent_participant_id` do ContextStore e deleta `{tenant_id}:instance:{human_instance_id}:wrap_up_pending`.
  - Na próxima avaliação de candidatos, `get_ready_instances()` incluirá o agente normalmente.

**Mecanismo**: não usa `agent_paused` event (que seria sobrescrito por heartbeats). Em vez disso, a flag Redis é verificada diretamente pelo routing-engine em tempo de roteamento — zero interferência com o ciclo de vida do agente e sem modificação no kafka_listener.

---

## Arc 14 — Pós-Atendimento: Segmentos Independentes — Fase D: nps_on_disconnect (2026-05-17)

Implementa o comportamento configurável do segmento NPS quando o cliente se desconecta antes de ser atendido.

### `@plughub/schemas` — `src/agent-registry.ts`

- Campo `nps_on_disconnect: z.enum(["skip", "timeout"]).default("timeout")` adicionado a `PoolHookEntrySchema`.
  - `"skip"` → hook customer-side não é despachado quando `close_origin == "customer_disconnect"`. Segmento é pulado silenciosamente (sem INCR `posatt:active`, sem Kafka).
  - `"timeout"` → despachado normalmente; skill YAML pode encerrar via branch `@ctx.session.close_origin` ou aguardar `_HOOK_TIMEOUT_S`. Default backward-compat.

### `orchestrator-bridge` — `main.py`

**`fire_pool_hooks()`**: antes de criar cada `conference_id` para uma entrada `side=customer`, verifica:
  1. `entry.nps_on_disconnect == "skip"`.
  2. Lê `session.close_origin` do ContextStore (`{tenant}:ctx:{session_id}` hash).
  3. Se `close_origin == "customer_disconnect"` → `continue` (skip da entrada, sem dispatch, sem INCR `posatt:active`).
  
Leitura `nps_on_disconnect` via `entry.get("nps_on_disconnect", "timeout")` — backward compat para hooks sem o campo.

---

## Arc 14 — Pós-Atendimento: Segmentos Independentes — Fase B: session.closed Targeted (2026-05-17)

Implementa o fechamento direcionado por segmento: cada posatt segment publica `session.closed` apenas para os participantes que fazem parte daquele segmento, em vez de broadcast para todos.

### `@plughub/schemas` — `src/stream.ts`

- Campo `recipients?: z.array(z.string()).nullable().optional()` adicionado a `SessionClosedPayloadSchema`.
  - `null` / ausente → broadcast (comportamento anterior preservado).
  - `string[]` → teardown restrito aos `participant_id`s listados.

### `orchestrator-bridge` — `main.py`

**`fire_pool_hooks()`**: Ao disparar cada hook `on_human_end` / `post_human`, registra o participante do lado fixo no SET `session:{id}:posatt:{conf_id}:participants` (TTL 4h):
  - `side=customer` → lê `session:{id}:customer_participant_id` (STRING Redis).
  - `side=agent` → lê `session.human_agent_participant_id` do ContextStore (`{tenant}:ctx:{session_id}` hash).

**`process_routed()` — hook agent join**: Quando um hook agent entra na conferência (`_is_hook_agent = True`), adiciona seu `native_instance_id` ao SET `posatt:{conf_id}:participants`.

**`process_routed()` — hook completion**: Ao detectar conclusão de um segmento posatt:
  1. Lê SMEMBERS `session:{id}:posatt:{conf_id}:participants`.
  2. Publica `session.closed` com `reason=posatt_segment_complete` e `recipients=[...]` em `agent:events:{session_id}`.
  3. Deleta o SET (cleanup).
  O broadcast final (`reason=conference_destroyed`) de `_destroy_conference()` continua como sinal global de teardown quando todos os segmentos terminam.

### `mcp-server-plughub` — `src/server.ts`

Handler `session.closed` refatorado com lógica de três paths:
  - `posatt_segment_complete` + `recipients` → teardown apenas se `agentInstanceId` estiver em `recipients`. NPS completion não afeta o agente humano; wrap-up completion encerra a view do agente humano.
  - `conference_destroyed` → teardown incondicional (broadcast final).
  - `agent_done` → teardown incondicional (path legado / backward compat).
  - Qualquer outro reason → mantém canal aberto (hooks ainda podem estar em execução).

---

## Arc 14 — Pós-Atendimento: Segmentos Independentes — Fase A: Core Split (2026-05-17)

Implementa a separação das camadas de encerramento de contato (Layer 1 × Layer 3) e o rastreamento de segmentos posatt independentes via `posatt:active` counter.

### `@plughub/schemas` — `src/agent-registry.ts`

- Campo `side: z.enum(["agent", "customer"]).default("agent")` adicionado a `PoolHookEntrySchema`.
  - `"agent"` → hook interage com agente humano (wrap-up, resumo automático). Default backward-compat.
  - `"customer"` → hook interage com cliente (NPS, pesquisa de satisfação).

### `orchestrator-bridge` — `main.py`

**Novas funções:**

- **`_close_contact_layer(redis_client, session_id)`**: Layer 1 close — fecha o WebSocket do cliente imediatamente quando o contato encerra. Publica `conversations.outbound session.closed` + `conversations.events contact_closed` (analytics com `ended_at` do `_mark_contact_ended()`). Guard: `contact_close_fired` NX.

- **`_destroy_conference(redis_client, session_id)`**: Layer 3 destroy — limpa infraestrutura da conferência quando o último segmento posatt terminar. Deleta `human_agent`/`human_agents` keys (que DEVEM permanecer vivas durante os hooks para roteamento de mensagens). Publica `session.closed` broadcast em `agent:events:{session_id}` (Arc 14 Fase B tornará isso targeted). Guard: `close_fired` NX (reutiliza a chave existente).

- **`_trigger_contact_close(redis_client, session_id)`**: mantida como wrapper backward-compat que chama `_close_contact_layer()` + `_destroy_conference()` sequencialmente. Usado no no-hook path, watchdog, e paths de AI primary.

**`fire_pool_hooks()` changes:**
- Lê `side` de cada hook entry (`entry.get("side", "agent")`, default `"agent"`).
- `INCR session:{id}:posatt:active` (TTL 4h) para cada hook `on_human_end` ou `post_human` disparado. Decrementado no completion em `process_routed()`.
- Valor de `hook_conf` estendido de `"{hook_type}:{pool}"` para `"{hook_type}:{pool}:{side}"` — backward compat: parse usa `split(":", 2)` e missing side defaults para `"agent"`.
- Log de `hook_type`, `side`, `origin_pool`, `target_pool` por hook disparado.

**Hook path (human agent_done com on_human_end hooks):**
- Chama `_close_contact_layer()` imediatamente após `_mark_contact_ended()`, ANTES de `fire_pool_hooks()`.
- Antes (P2): cliente aguardava WS aberto até TODOS os hooks terminarem.
- Depois (Arc 14 A): WS do cliente fecha imediatamente; hooks rodam em paralelo.
- NPS e wrap-up descobrem que o cliente saiu via `@ctx.session.close_origin` e agem conforme YAML (decisão D2 do spec).

**`process_routed()` hook completion (Arc 14 algorithm):**
- Parseia `side` do valor estendido de `hook_conf` (`_hl_parts[2]`, default `"agent"`).
- Dispatch post_human (se aplicável) ANTES de DECR `posatt:active` — garante que os INCRs de post_human precedam o DECR deste segmento (evita transição espúria a 0).
- `DECR session:{id}:posatt:active` para cada hook completion, independente do tipo.
- Quando `posatt:active == 0` e nenhum novo segmento foi despachado: `_destroy_conference()`.
- Substitui as chamadas a `_trigger_contact_close()` no completion block (que era Layer 1 + Layer 3 juntos).

**Comportamento resultante (P1 e P2 resolvidos):**
- NPS e wrap-up são posatt segments independentes — cada um tem seu `posatt:active` contribution.
- Cliente vê seu WS fechar imediatamente ao fim do contato (não mais aguarda wrap-up).
- Wrap-up e NPS rodam em paralelo sem esperar um pelo outro.
- Console do agente permanece aberto até o último posatt segment terminar (`_destroy_conference()`).
- P3 (bloqueio do agente para próximo contato) e Fase B (targeted session.closed) permanecem como Arc 14 Fases C e B respectivamente — ver TODO.md.

---

## Sistema Dinâmico de Mascaramento ContextStore — Fase C: UI (2026-05-17)

Seção 6 "Regras de Context Store" adicionada à `MaskingPage.tsx` — interface completa para configurar `ContextMaskingConfig` via Config API.

### `platform-ui` — `src/modules/masking/MaskingPage.tsx`

**Tipos inline** (sem import de schemas para isolar dependência):
- `ContextMaskingType` union dos 9 valores visuais
- `ContextMaskingRule` interface `{ pattern, role, type, label? }`
- `ContextMaskingConfig` interface `{ rules[], default_unmatched_operator }`

**Constante `MASKING_TYPE_INFO`**: mapa de cada `ContextMaskingType` para `{ label, sample }` — usado em selects e preview na tabela.

**Em `MaskingPage()`**:
- Lê `maskingEntries['context_rules']` do namespace `masking` (já carregado por `useNamespace`)
- `saveContextRules(config)` → `putConfig('masking', 'context_rules', config, tenantId, adminToken)` → Redis `plughub:cfg:{tenantId}:masking:context_rules`

**`ContextRulesSection` sub-component**:
- Tabela com colunas: Padrão, Role, Tipo de máscara, Prévia (preview do sample), Label, Ações
- Edição inline por linha (click ✏️ abre row edit in-place, ✓/✕ confirma/cancela)
- Exclusão por linha (✕ vermelho)
- Linha "adicionar nova regra" expansível (botão "+ Nova regra")
- Select de `default_unmatched_operator` com preview inline
- Botão "Salvar Regras" com badge "⚠ Alterações não salvas" quando há mudanças locais não persistidas
- Hint de prioridade de regras: exact > glob > wildcard; role exato > `*`
- Sincroniza state com `config` prop via `useEffect` (suporta reload após save)

**Persistência**: `putConfig('masking', 'context_rules', { rules, default_unmatched_operator }, tenantId, adminToken)` escreve no Config API (port 3600) que propaga para Redis. `MaskingService.loadContextMaskingConfig()` em `mcp-server-plughub` lê a chave no próximo request (TTL cache 60s).

---

## Sistema Dinâmico de Mascaramento ContextStore — Fase B: Backend Dinâmico (2026-05-17)

Substitui o `TAG_PII_CATEGORY` hardcoded em `server.ts` pelo algoritmo dinâmico de resolução de regras baseado em `ContextMaskingConfig` carregado do Config API.

### `mcp-server-plughub` — `src/server.ts`

**Removido**: `TAG_PII_CATEGORY` (mapa estático tag → categoria) e `maskPiiValue()` (switch por categoria).

**Adicionado**:

- **Cache em memória**: `contextMaskingConfigCache: Map<string, CachedMaskingConfig>` com TTL de 60s por tenant. `getContextMaskingConfig(redis, tenantId)` — retorna cache hit se válido, senão chama `MaskingService.loadContextMaskingConfig()` e armazena. `invalidateContextMaskingCache(tenantId)` — disponível para eventos futuros de `config.changed`.

- **Algoritmo de especificidade** (`ruleSpecificity()`): pontua cada regra candidata:
  - Pattern: exact = 20 pontos; glob (`caller.*`) = 10; wildcard (`*`) = 0; sem match = `null`
  - Role: match exato da categoria do caller = +2; `*` = +0; categoria diferente = `null`

- **`resolveContextMaskingRule(tag, callerRole, config)`**: varre todas as regras, seleciona a de maior pontuação. Traduz roles em duas categorias: `operator` (default) e `supervisor` (supervisor/admin/evaluator/reviewer). Retorna `null` quando nenhuma regra casa.

- **`applyMaskingTypeToValue(raw, type)`**: aplica os 9 tipos visuais:
  - `plain` → valor intacto; `hidden` → `""` (sinal de omissão); `full` → `"***"`
  - `last_2` / `last_4` → preserva últimos N dígitos; `first_1` → preserva primeiro caractere; `first_word` → preserva primeira palavra
  - `email_domain` → `X***@domain.com`; `financial` → `"R$ ****,**"`

- **`applyContextMaskingDynamic(rawHash, role, allowedNs, redis, tenantId)`** (async): substitui o antigo `applyContextMasking()` síncrono. Fluxo:
  1. Filtra `agent.*` (sempre omitido)
  2. Filtra namespaces fora de `allowedNs` para operator
  3. Chama `resolveContextMaskingRule()` → obtém `maskType`
  4. Fallback: `config.default_unmatched_operator` para operator; `"plain"` para supervisor
  5. Aplica: `hidden` → omite campo; `plain` → campo intacto; outros → aplica `applyMaskingTypeToValue()` e anota `{ pii: true, masked: true, category: maskType }`

- **Handler `GET /api/supervisor_state`**: chamada atualizada para `await applyContextMaskingDynamic(hash, viewerRole, operatorNamespaces, redis, tenantId)`.

**Import adicionado** no topo: `MaskingService` de `"./lib/masking"` e `ContextMaskingConfig` de `"@plughub/schemas"`.

---

## Sistema Dinâmico de Mascaramento ContextStore — Fase A: Schemas e Bootstrap (2026-05-17)

Implementa a infraestrutura de tipos para o mecanismo 3D de mascaramento (tag × role × tipo) conforme especificado em `docs/guias/context-masking-rules.md`.

### `@plughub/schemas` — `src/audit.ts`

- `ContextMaskingTypeSchema` (z.enum): 9 tipos visuais de mascaramento — `plain`, `hidden`, `full`, `last_2`, `last_4`, `first_1`, `first_word`, `email_domain`, `financial`. Puramente semântica visual, sem vínculo a tipo de dado.
- `ContextMaskingRuleSchema`: mapa `{ pattern, role, type, label? }`. `pattern` aceita nome exato ou glob com `*`. `role` = `"operator" | "supervisor" | "*"`. Resolução por especificidade: exact > glob > `*`; role específico > `*`.
- `ContextMaskingConfigSchema`: `{ rules[], default_unmatched_operator }` com default `"plain"` (permissivo — maioria dos tags do ContextStore não são PII).
- `DEFAULT_CONTEXT_MASKING_CONFIG`: fallback hardcoded que converte exatamente o `TAG_PII_CATEGORY` anterior, incluindo `account.limite_credito → hidden` (campo oculto para operator). Inclui catch-alls `caller.* → last_4` e `account.* → financial`.

### `@plughub/schemas` — `src/index.ts`

- Exports adicionados: `ContextMaskingTypeSchema`, `ContextMaskingRuleSchema`, `ContextMaskingConfigSchema`, `DEFAULT_CONTEXT_MASKING_CONFIG` (valor) + tipos `ContextMaskingType`, `ContextMaskingRule`, `ContextMaskingConfig`.

### `mcp-server-plughub` — `src/lib/masking.ts`

- `MaskingService.loadContextMaskingConfig(redis, tenantId)`: lookup chain em 3 tiers — `plughub:cfg:{tenantId}:masking:context_rules` → `plughub:cfg:__global__:masking:context_rules` → `DEFAULT_CONTEXT_MASKING_CONFIG`. Valida com `ContextMaskingConfigSchema.safeParse()` — schema inválido cai para o próximo tier.
- `MaskingService.saveContextMaskingConfig(redis, scope, config)`: persiste configuração. `scope = "global"` escreve em `__global__`; string de tenant escreve override por-tenant.

### `infra/config-seed/masking-context-rules.json`

- Arquivo de seed criado com as regras globais padrão. Pode ser carregado por `saveContextMaskingConfig(redis, "global", ...)` durante bootstrap de novos ambientes.

---

## ContextStore Taxonomy — Mascaramento PII e Visibilidade por Role (2026-05-17)

Implementa controle formal de acesso e mascaramento de dados PII na aba Contexto do Console, conforme `docs/guias/context-store-taxonomy.md`.

### Taxonomia (documento)
- **`docs/guias/context-store-taxonomy.md`** (criado): 7 namespaces (`caller`, `account`, `service`, `journey`, `session`, `agent`, `history`) com catálogo de tags por namespace, categorias PII por tag, matriz de visibilidade por role (operator/supervisor/admin/evaluator), `context_visibility.operator_namespaces` configurável por pool, `TAG_PII_CATEGORY` mapping, e plano de 4 fases.

### Backend — Fase 1: mascaramento em supervisor_state

**`mcp-server-plughub` `src/server.ts`**
- Funções helper adicionadas antes de `startServer`: `extractJwtRole()` (decodifica role do Bearer JWT sem verificação — auth middleware já validou), `TAG_PII_CATEGORY` (mapa tag → categoria PII), `maskPiiValue()` (padrões por categoria: cpf, cnpj, phone, email_addr, financial), `DEFAULT_OPERATOR_NAMESPACES = ["service","journey","session"]`, `applyContextMasking()` (filtra namespaces por role + mascara PII para operator).
- `GET /api/supervisor_state/:sessionId`: extrai `viewerRole` do header `Authorization`; lê `poolId` do session meta; busca `context_visibility.operator_namespaces` do pool_config Redis; lê ContextStore hash e aplica `applyContextMasking()`; resposta inclui `context_snapshot` filtrado/mascarado (entries PII têm `masked:true`, `pii:true`, `category`); `contact_context` (legacy) suprimido quando `context_snapshot` presente.

### Backend — Fase 2: validação de namespace no inject-context

**`mcp-server-plughub` `src/server.ts`**
- `POST /api/inject-context/:sessionId`: extrai role do JWT; valida namespace da `key` — `operator` pode escrever apenas em `agent.*` e `service.*`; outros namespaces retornam `HTTP 403 forbidden_namespace`.

### Schemas + Agent Registry + Routing Engine — Fase 3

**`@plughub/schemas` `agent-registry.ts`**
- `PoolRegistrationSchema`: novo campo `context_visibility?: { operator_namespaces: string[] }` com JSDoc explicando defaults e comportamento PII.

**`agent-registry`**
- `prisma/schema.prisma`: `context_visibility Json?` no modelo `Pool`
- `prisma/migrations/20260517000000_add_pool_context_visibility/migration.sql`: `ALTER TABLE pools ADD COLUMN context_visibility JSONB`
- `routes/pools.ts`: `create` inclui `context_visibility ?? DbNull`; `update` spread condicional. `_formatPool` já é dinâmico — propagado automaticamente no Kafka `pool.registered`.

**`routing-engine`**
- `models.py` `PoolConfig`: campo `context_visibility: dict | None = None`
- `kafka_listener.py`: propaga `pool_data.get("context_visibility")` para `PoolConfig`
- `save_pool_config()` usa `model_dump()` — `context_visibility` serializado automaticamente no Redis

### Frontend — Fase 4

**`platform-ui`**
- `types/index.ts` `Pool`: campo `context_visibility?: { operator_namespaces: string[] } | null`
- `modules/agent-assist/types.ts` `ContextEntry`: campos `pii?`, `masked?`, `category?` — set pelo backend quando valor mascarado
- `ContextoTab.tsx`:
  - `CtxFieldRow`: badge "🔒 PII" em amber para entradas mascaradas; valor exibido em `font-mono text-gray-400`; confidence badge suprimido quando mascarado
  - `groupByNamespace()`: ordem canônica atualizada para `caller → account → service → journey → session → agent → history` + labels para novos namespaces
  - `ManualTagForm`: aceita `viewerRole` prop; datalist filtrado — operator vê `agent.`, `service.`; supervisor+ vê todos; erro 403 exibe mensagem do backend em vez de "HTTP 403"
  - `ContextoTab`: prop `viewerRole?: string` (default "operator"); propagado para `ContextSnapshotCard` e `ManualTagForm`
- `RightPanel.tsx`: importa `useAuth`; lê `currentUser.role`; passa `viewerRole` para `ContextoTab`
- `config-recursos/PoolsPage.tsx`: campo "Visibilidade do Context Store" no formulário de pool — input de texto com namespaces separados por vírgula; lido/gravado como `context_visibility.operator_namespaces`

---

## max_reply_time_ms — SLA de resposta por mensagem (2026-05-16)

Campo opcional `max_reply_time_ms` adicionado ao Pool para definir o tempo máximo de resposta do agente a cada mensagem do cliente, independente do SLA total de sessão (`sla_target_ms`).

### Backend

**`@plughub/schemas` `agent-registry.ts`**
- `PoolRegistrationSchema`: novo campo `max_reply_time_ms: z.number().int().positive().optional()`

**`agent-registry` Prisma + routes**
- `prisma/schema.prisma`: `max_reply_time_ms Int?` no modelo `Pool`
- `prisma/migrations/20260516000000_add_pool_max_reply_time_ms/migration.sql`: `ALTER TABLE pools ADD COLUMN max_reply_time_ms INTEGER`
- `routes/pools.ts`: incluído no `create` (`?? null`) e no `update` (spread condicional). `_formatPool` já é dinâmico — sem alteração necessária.

**`routing-engine`**
- `models.py` `PoolConfig`: campo `max_reply_time_ms: int | None = None`
- `registry.py` `write_pool_snapshot()`: parâmetro `max_reply_time_ms` opcional; escrito no snapshot Redis apenas quando não-nulo
- `kafka_listener.py` `_handle_pool_event()`: propaga `pool_data.get("max_reply_time_ms")` para `PoolConfig`
- `main.py` `_write_pool_context()`: lê `max_reply_time_ms` do pool_config Redis e escreve `session.pool.max_reply_time_ms` no ContextStore quando não-nulo

**`mcp-server-plughub` `tools/supervisor.ts`**
- `supervisor_state`: lê `pool_id` do `session:meta`, busca `{tenantId}:pool_config:{poolId}` no Redis, extrai `sla_target_ms` e `max_reply_time_ms`
- Resposta `sla`: inclui `max_reply_time_ms` (null quando não configurado)

### Frontend

**`platform-ui`**
- `types/index.ts` `Pool`: campo `max_reply_time_ms?: number | null`
- `modules/agent-assist/types.ts` `PoolInfo`: campo `max_reply_time_ms: number | null`
- `modules/agent-assist/types.ts` `ContactSession`: campo `maxReplyTimeMs: number | null`
- `AgentAssistContext.tsx`: `fetchPools` mapeia `max_reply_time_ms`; `makeContact` inicializa `maxReplyTimeMs: null`; contact criado na chegada recebe `maxReplyTimeMs` do `poolInfo`
- `ContactList.tsx` `waitLevel()`: agora aceita `maxReplyTimeMs` como segundo parâmetro. Com limite: 50% = atenção, 100% = urgente. Sem limite: fallback hardcoded 60s/180s.
- `ActionBar.tsx`: novo componente `ReplySlaChip` — mostra `💬 {elapsed}` colorido (verde/âmbar/vermelho pulsante) quando sessão aberta + cliente aguardando + pool tem `maxReplyTimeMs` configurado. Limiar: 70% = warning, 100% = breach.
- `config-recursos/PoolsPage.tsx`: SLA e tempo máx. de resposta agora exibidos lado a lado no formulário de pool. Campo `max_reply_time_ms` opcional, `placeholder="Sem limite"`.

---

## Arc 11 Fase 2 — Fase E: Área central abas Atual + Journey (2026-05-16)

### Mudanças

**`components/JourneyPanel.tsx`** (novo)
- Painel do journey para a aba central. Layout: strip superior com dados do journey + dois painéis (lista de sessões | transcript read-only).
- `useCustomerJourneys(tenantId, customerId)`: busca journeys do cliente via `GET /analytics/reports/journeys?customer_id=X`. Prioridade: journey com `origin_session_id === currentSessionId` → journey ativo/suspenso mais recente → primeiro da lista.
- `useCustomerSessions(customerId)`: busca histórico via `GET /analytics/sessions/customer/:id` (mesmo endpoint do `useCustomerHistory`). Filtra sessões abertas após `journey.created_at - 1min`.
- `useTranscript(sessionId)`: busca transcript via `GET /api/conversation_history/:sessionId` (endpoint existente).
- `SessionListItem`: item da lista de sessões com ícone de outcome, badge "atual" / "origem", data e duração.
- `TranscriptViewer`: exibe mensagens read-only com banner "🔒 somente leitura", auto-scroll to bottom, bubble colors por author.
- Estados: sem customer → "Cliente não identificado"; sem journey → "Contato standalone" (mensagem explicativa); com journey → view completa.
- Auto-seleciona a sessão atual ao abrir o painel.

**`AgentAssistPage.tsx`**
- Import `JourneyPanel`
- Estado `centralTab: "current" | "journey"` (default `"current"`, reset em `selectedSessionId` change)
- Tab switcher "Atual · Journey" renderizado como barra compacta acima do conteúdo central (quando `selected` existe)
- `centralTab === "journey"` → `<JourneyPanel>` (hidei ChatArea + CopilotBanner + AgentInput + ParticipantFilterBar)
- `centralTab === "current"` → comportamento idêntico ao anterior

---

## Arc 11 Fase 2 — Fase D: Aba Contexto enriquecida (2026-05-16)

### Mudanças

**`components/tabs/ContextoTab.tsx`** — reescrita parcial
- Props adicionadas: `sessionId?: string | null`, `supervisorState?: SupervisorState | null`
- **`IntentFlagsCard`** (novo): card violeta exibindo `intent.current` (com % de confiança) + `flags[]` como chips laranja. Migrado do EstadoTab (removido na Fase C). Aparece apenas quando `supervisorState` fornecido.
- **`ManualTagForm`** (novo): form inline com campo chave (datalist com `caller.` / `account.` / `session.`), textarea de valor, select de confiança (0.5–1.0). Submit → `POST /api/inject-context/:sessionId` com `{ key, value, confidence }`. Feedback visual: "Salvando…" / "✓ Tag salva" com auto-limpa após 1.5s. ESC cancela. ⌘↵ submete.
- **`ContextSnapshotCard`**: botão "+" no header abre/fecha `ManualTagForm` inline. Exibe mesmo quando snapshot vazio. Props: `sessionId`, `onTagSaved`.
- `sourceLabel()` ampliado: `human_agent` → "agente", `routing_engine` → "roteamento".
- Quando `context_snapshot` ausente mas `sessionId` presente: exibe card teal com form aberto (sempre visível).
- Ordem de renderização: IntentFlagsCard → ContextSnapshotCard+form → legacy ContactContextCard → Insights histórico → Insights conversa.

**`components/RightPanel.tsx`**
- Prop adicionada: `sessionId?: string | null`
- `ContextoTab` recebe `sessionId` e `supervisorState`
- Docstring atualizada para Fase D

**`AgentAssistPage.tsx`**
- `RightPanel`: prop `sessionId={selected?.sessionId ?? null}` adicionada

---

## Arc 11 Fase 2 — Fase C: Aba Agentes (substitui State) (2026-05-16)

### Mudanças

**`types.ts`** — `ActiveTab`: `"estado"` → `"agentes"`

**`components/tabs/AgentesTab.tsx`** (novo)
- **Seção A — Ativos na Sessão**: `HumanAgentCard` (blue, primary, "● Atendendo", `···` → Substituir inline menu) + `AiParticipantCard` existente por AI participant; texto "Nenhum agente AI" quando vazio
- **Seção B — Adicionar Agente**: `AgentInviteRow` por agente mentionável com estado ⚪→🔄→🟢→✅ derivado de `ai_participants`; convite expande textarea inline; pending aliases rastreados em state local e limpos quando agente entra em `ai_participants`; botão "📤 Delegar Tarefa" abre drawer existente
- **Seção C — Pós-Atendimento**: oculto (Arc 14 pendente)
- Prop `substitutionMode` + `onToggleSubstitutionMode` migradas de ActionBar para `HumanAgentCard.···` menu

**`components/RightPanel.tsx`**
- Import `AgentesTab` (substitui `EstadoTab` no render)
- Interface ampliada: `agentName`, `substitutionMode`, `onToggleSubstitutionMode`, `mentionableAgents`, `onAddSpecialist`, `onDelegar`, `sessionClosed`

**`AgentAssistPage.tsx`**
- `activeTab` default: `"estado"` → `"agentes"`
- `handleSelectContact`: `setActiveTab("agentes")`
- `rightTabLabels`: chave `estado` → `agentes`; defaultValue `"Agentes"`
- Tab array: `["estado","contexto","historico"]` → `["agentes","contexto","historico"]`
- `RightPanel`: 7 novas props passadas

---

## Arc 11 Fase 2 — Fase B: Barra superior simplificada + sentimento compacto (2026-05-16)

### Mudanças

**`ActionBar.tsx`**
- Removida a seção de identidade do contato (channel icon, displayId, pool badge) — migrada definitivamente para a lista esquerda (Fase A)
- Removida a `SlaBar` e seu import de `SlaState` — SLA já está na lista esquerda
- Removido o botão "Substituir" da barra principal — moverá para Aba Agentes (Fase C); props `substitutionMode`/`onToggleSubstitutionMode` mantidas na interface para não quebrar o pai
- Adicionado `SentimentChip`: indicador compacto com emoji + label colorido derivado de `contact.supervisorState?.sentiment.current`; escala: 😊 Satisfeito (≥0.3) · 😐 Neutro (-0.3–0.3) · 😤 Frustrado (-0.6– -0.3) · 😡 Irritado (<-0.6); usa tokens Tailwind green-700/orange-600/red-600/gray-500; só aparece quando sessão aberta e dado disponível
- Banner "⚠️ Sessão encerrada" simplificado (antes dependia de `!sla`)

**`Header.tsx`**
- Removida sub-linha com `poolId + sessionId` — só exibe estado ready/offline do agente
- Removido bloco de timer de sessão (`sessionStartedAt` + `handleMs` state + `useEffect`)
- Removido bloco de SLA (`sla`, `slaPercent`, `slaColor`)
- Removido `formatElapsed()` (ficou órfão após remoção do timer)
- Removido `SlaState` do import de tipos
- Props `poolId?`, `sessionId?`, `sla?`, `sessionStartedAt?` marcadas como `@deprecated` e mantidas na interface para não quebrar o pai
- Row 1 final: avatar + nome + estado ready/offline | badge contatos + botão pausa + WS status

---

## Fix: Mensagens do wrap-up não apareciam no Console após F5 do cliente (2026-05-15)

### Causa raiz
`fire_pool_hooks()` no bridge publica `conversations.inbound` via Kafka para os agentes de hook (wrap-up, NPS) **depois** que `session:{id}:closed` já foi setado (linha 2802 do bridge, antes de `fire_pool_hooks()` na linha 3129). O routing engine, ao receber esses eventos, verificava `session:{id}:closed` no guard de `_process_message()` → retornava imediatamente → `conversations.routed` nunca publicado → `activate_native_agent()` nunca chamado → wrap-up nunca iniciava → nenhuma mensagem aparecia no Console.

O banner "Wrap-up em andamento" aparecia porque é gerado pelo `hook_pending` key, independente do routing. O Agente Wrapup V1 aparecia no painel Estado mas com status inicial sem avançar.

### Fix
`packages/routing-engine/src/plughub_routing/main.py` — `_process_message()`:
- O guard `is_closing` agora é condicionado a `not event.conference_id`
- Eventos de conferência (`conference_id` setado por `fire_pool_hooks()` e routing de `@mention`) são isentos do guard — são ativações legítimas em sessões já encerrando
- Seguro: o router já passa `session_id=None` para `mark_busy()` quando `conference_id` está presente, então nunca incrementa `active_count` nem atualiza o serving-pool key

### Containers afetados
- `routing-engine` (único rebuild necessário)

## Fix: active_count stuck após reconexão webchat (F5) + guard janela de hooks (2026-05-15)

### Causa raiz — Bug 1 (active_count stuck)
Ao recarregar a página (F5) durante uma sessão ativa, o webchat cliente publicava novo `conversations.inbound`. O serving-pool key `{tenant}:session:pool:{session_id}` havia sido deletado por `remove_conversation()` no fechamento do WS anterior, então `mark_busy()` Guard 0 não detectava a sessão como ativa → `INCR active_count` → contador travado em 1 permanentemente.

### Fix — Bug 1
`packages/channel-gateway/src/plughub_channel_gateway/adapters/webchat.py`:
- Guard expandido para 5 chaves: `{tenant}:session:pool:{session_id}`, `session:{id}:closed`, `session:{id}:close_fired`, `session:{id}:hook_pending:on_human_end`, `session:{id}:hook_pending:post_human`
- Cobre a janela de reconexão durante hooks ativos (até 180s)

### Containers afetados
- `channel-gateway`

## Pool Context Enrichment — Campos agent_groups e mentionable_journeys (2026-05-14)

Completa o Pool Context Enrichment adicionando os dois campos que estavam bloqueados por falta de suporte no schema.

### packages/schemas/src/agent-registry.ts
- `PoolRegistrationSchema`: adicionados `mentionable_journeys: z.record(z.string()).optional()` e `agent_groups: z.array(z.string()).optional()`

### packages/agent-registry/prisma/schema.prisma
- Model `Pool`: adicionados `mentionable_journeys Json?` e `agent_groups String[] @default([])`
- Requer `prisma migrate dev --name add_pool_groups_journeys` ao conectar

### packages/agent-registry/src/routes/pools.ts
- `POST /v1/pools`: persiste `mentionable_journeys` e `agent_groups` no create
- `PUT /v1/pools/:pool_id`: atualiza `mentionable_journeys` e `agent_groups` no update
- Ambos incluídos no payload do evento `pool.registered`/`pool.updated` via `_formatPool`

### packages/routing-engine/src/plughub_routing/models.py
- `PoolConfig`: adicionados `mentionable_journeys: dict[str, str] | None = None` e `agent_groups: list[str] = Field(default_factory=list)`

### packages/routing-engine/src/plughub_routing/kafka_listener.py
- `_handle_pool_event`: lê `mentionable_journeys` e `agent_groups` do payload e popula o `PoolConfig`
- Log atualizado para incluir `agent_groups`

### packages/routing-engine/src/plughub_routing/main.py
- `_write_pool_context`: extrai `mentionable_journeys` e `agent_groups` do Redis cache do pool
- Escreve `session.pool.mentionable_journeys` (condicional, quando presente) e `session.pool.agent_groups` (condicional, quando não vazio) no ContextStore
- Skill-flows podem agora acessar `@ctx.session.pool.mentionable_journeys` e `@ctx.session.pool.agent_groups`

---

## Arc 12 Fase E — Integração do MetricSelector com Análise de Qualidade (2026-05-14)

Fecha o ciclo de observabilidade do Arc 12: `agent_event` KPIs agora sobreponíveis em todas as telas de análise de qualidade, com seletor dinâmico de categorias.

### analytics-api — reports_query.py
- `_fetch_agent_event_slice(category, from_dt, to_dt, ...)`: query de AVG(value) na `agent_business_events` para uma categoria; retorna valor ou null
- `_fetch_agent_event_timeseries(category, from_dt, to_dt, granularity, ...)`: série temporal de AVG(value) por período
- `query_quality_comparison()`: aceita `metrics: list[str]` — para cada `agent_event:{category}`, chama `_fetch_agent_event_slice()` em paralelo e injeta no dict de métricas de cada fatia
- `query_quality_timeseries()`: aceita `metrics: list[str]` — para cada `agent_event:{category}`, chama `_fetch_agent_event_timeseries()` e mescla na série
- `query_quality_metrics()`: single-slice, mesma expansão de `metrics[]`
- `_compute_delta()`: refatorado para ser key-agnostic — itera sobre todas as chaves presentes, não apenas as 4 base

### analytics-api — reports.py
- `GET /reports/quality-comparison`: parâmetro `metrics: list[str] = Query(default=[])` — aceita `metrics[]=agent_event:{category}` repetível
- `GET /reports/quality-timeseries`: idem
- `GET /reports/quality-metrics`: idem
- `GET /reports/agent-events/categories`: novo endpoint — retorna lista de categorias distintas com contagem; usado pelo AddCardModal e MetricSelector

### platform-ui — catalog.ts
- `agent-event-timeseries`: endpoint `/reports/display/agent-event-timeseries`; `compatible_tools: ['line_chart', 'bar_chart']`; `configurable_params.category` com `options_from: '/reports/agent-events/categories'`
- `agent-event-summary`: endpoint `/reports/display/agent-event-summary`; `compatible_tools: ['metric_card', 'table']`; mesmo `options_from`
- `ConfigurableParam.options_from?: string` — nova propriedade opcional

### platform-ui — AddCardModal.tsx
- `StepConfigure`: prop `tenantId: string` adicionada
- `paramOptions: Record<string, string[]>` via `useState`
- `useEffect` por `endpoint.id + tenantId`: loop sobre params com `options_from`, fetch assíncrono, popula `paramOptions`
- Render condicional: `paramOptions[param.key]` presente → `<select>` com categorias; ausente → `<input type="text">` original
- `<select>` desabilitado com "Carregando categorias…" enquanto array está vazio; opção vazia para params opcionais

### platform-ui — MetricSelector.tsx (novo arquivo)

Componente compartilhado em `src/modules/analise/MetricSelector.tsx`:

- `MetricDef`: `{ key: string, label, format, higherIsBetter, color? }` — `key` é `string` (aceita `agent_event:*`)
- `BASE_METRIC_DEFS`: 4 defs (evaluation_score #1B4F8A, resolution_rate #059669, escalation_rate #DC2626, aht_ms #D97706)
- `BASE_METRIC_KEYS`: `BASE_METRIC_DEFS.map(d => d.key)`
- `makeAgentEventDef(category, idx)`: cria def com cor de `AGENT_EVENT_COLORS` (paleta de 6 cores violet/cyan/pink/green/amber/purple)
- `buildMetricDefs(selectedMetrics)`: filtra base + map agent_event entries → `MetricDef[]`
- `MetricSelector`: pills toggle para 4 base + pills removíveis para agent_event ativos + picker "+ Evento" com lazy-fetch de `/reports/agent-events/categories`; fechamento por clique externo via `useRef`

### platform-ui — AnaliseComparacaoPage.tsx
- Removidas definições inline de `MetricDef`, `BASE_METRIC_DEFS`, `buildMetricDefs`; importadas de `./MetricSelector`
- `SliceMetrics.metrics: Record<string, number | null>` (era struct tipada com 4 chaves fixas)
- Estado `selectedMetrics: string[]` + derivado `metricDefs = buildMetricDefs(selectedMetrics)`
- `fetchSlice()`: append `&metrics[]={k}` para cada `agent_event:*` selecionado
- `MetricSelector` adicionado na área de filtros globais
- `GroupedBarChart` e `MetricTable` recebem `metricDefs: MetricDef[]` e iteram dinamicamente
- `metricToChartValue(key, raw)`: `agent_event:*` → raw; `aht_ms` → `/60000`; outros → `×100`

### platform-ui — AnaliseQualidadePage.tsx

**TimeseriesView**:
- `selectedMetrics` (default `['evaluation_score']`), `metricDefs = buildMetricDefs(selectedMetrics)`
- `load()` append `metrics[]` para agent_event; `selectedMetrics` em deps
- `chartData`: `score` (evaluation_score ×100) + `agent_event:*` keys raw
- `YAxis` sem `domain` fixo — auto-escala
- `<Legend>` adicionado; `<Line>` para evaluation_score + `{metricDefs.filter(agent_event).map(d => <Line dataKey={d.key}>)}` dinâmico
- Tooltip dinâmico: label da MetricDef; `%` apenas para `score`
- `MetricSelector` abaixo do filtro de datas

**ComparisonView**:
- `selectedMetrics` (default `BASE_METRIC_KEYS`), `metricDefs = buildMetricDefs(selectedMetrics)`
- `run()` append `metrics[]`; `selectedMetrics` em `useCallback` deps
- `metricDefs.map(def => <MetricComparisonRow>)` substitui 4 chamadas hardcoded
- `MetricSelector` adicionado após os SliceForms

**Doc:** `docs/arcos/arc12-agent-business-events.md`

---

## Arc 6 Fase 2 — Observabilidade de Mudanças e Comparação por Deploy (2026-05-14)

Todas as fases A–D implementadas. Spec: [`docs/arcos/arc6-phase2-observability.md`](docs/arcos/arc6-phase2-observability.md).

**Fase A — Infraestrutura Deploy Events**
- `analytics.deploy_events` ClickHouse table (ReplacingMergeTree por `deploy_id`) em `clickhouse.py`.
- `agent-registry/src/infra/kafka.ts`: `publishSkillDeployed()` chamado em `POST /v1/skills/:id/deploy`.
- `analytics-api/consumer.py`: consumer `registry.changed` com `event_type: "skill_deployed"` → INSERT `deploy_events`.
- `GET /reports/deploy-timeline` endpoint em `reports.py`.

**Fase B — Quality Comparison**
- `GET /reports/quality-comparison`: dual-slice com `evaluation_score`, `resolution_rate`, `escalation_rate`, `aht_ms`, delta + `statistical_significance` (warn N < 30).
- `AnaliseQualidadePage.tsx`: tab "Comparação" com `SliceForm` + `MetricComparisonRow` (deltas coloridos ▲▼→).

**Fase C — Timeseries com Deploy Markers**
- `GET /reports/quality-timeseries`: retorna pontos diários/semanais + `deploy_markers[]`.
- `AnaliseQualidadePage.tsx`: tab "Tendência", Recharts `LineChart` + `ReferenceLine` por deploy (linhas laranja tracejadas, hover com versão).

**Fase D — Painel de Grupos de Comparação**
- `GET /reports/quality-metrics`: slice única — usado em parallel fetch pelo frontend.
- `AnaliseComparacaoPage.tsx`: rota `/analise/comparison`, `ComparisonGroupBuilder` (até 4 slices), `GroupedBarChart` (barras agrupadas por KPI), `MetricTable` com N < 30 badges.

---

## Skill-flow DAG Validation + Worker Non-blocking Receive (2026-05-14)

Completa os dois itens pendentes do step `receive` (pontos 3 e 4 do TODO).

**`packages/skill-flow-engine/src/engine.ts`**
- `_getSuccessors(step)` (novo): extrai todos os IDs de próximo step de qualquer step type — cobre todos os campos de transição (`on_success`, `on_failure`, `on_message`, `on_timeout`, `on_disconnect`, `on_max_iterations`, `branches[].next`, `strategy.on_success/on_failure`). Filtra sentinelas do engine (`__complete__`, `__suspended__`, etc.).
- `validateFlow(flow)` (novo, exportado): DFS three-colour (white/gray/black) sobre o grafo de steps. Detecta back-edges (ciclos). Para cada ciclo encontrado, verifica se algum node do ciclo é um `receive` step com `max_iterations` definido. Lança erro descritivo se ciclo não guarded for encontrado. Chamado em `run()` antes do lock, uma vez por execução (O(V+E) — negligível vs BLPOP).

**`packages/skill-flow-engine/src/index.ts`**
- `validateFlow` adicionado ao export público — permite que skill-registry e testes externos validem flows sem instanciar o engine.

**`packages/skill-flow-worker/src/worker.ts`**
- `_inflight: Set<Promise<void>>` adicionado a `SkillFlowWorker` — rastreia todas as execuções em andamento para graceful drain no shutdown.
- `eachMessage`: substituído `await this.handleMessage()` por fire-and-forget com `.catch()` + `.finally(() => _inflight.delete(p))`. Retorna imediatamente — Kafka commita o offset e processa a próxima mensagem sem esperar o BLPOP do `receive` (que pode durar até 4h).
- `gracefulShutdown` (SIGTERM/SIGINT): agora drena `_inflight` via `Promise.allSettled([..._inflight])` antes de desconectar o Redis. Sessions com `receive` ativo recebem o sentinela `session:closed` do orchestrator-bridge, desbloqueando o BLPOP rapidamente.

---

## Pool Context Enrichment — Routing Engine → ContextStore (2026-05-14)

Routing Engine agora escreve contexto do pool alocado no ContextStore após cada roteamento bem-sucedido, tornando `@ctx.session.pool.*` disponível em skill-flows sem consulta ao agent-registry.

**`packages/routing-engine/src/plughub_routing/models.py`**
- `PoolConfig`: novo campo `mentionable_pools: dict[str, str] | None = None` — alias→pool_id map proveniente do agent-registry. Compatível com `extra="ignore"` existente (campo explícito agora).

**`packages/routing-engine/src/plughub_routing/kafka_listener.py`**
- `_handle_pool_event()`: passa `mentionable_pools=pool_data.get("mentionable_pools") or None` ao construir `PoolConfig`. Log atualizado para incluir aliases de pools mencionáveis.

**`packages/routing-engine/src/plughub_routing/main.py`**
- `_write_pool_context()` (novo): lê `{tenant_id}:pool_config:{pool_id}` do Redis (cache próprio do routing engine, sem I/O extra), escreve 2–3 entradas no hash ContextStore `{tenant_id}:ctx:{session_id}`:
  - `session.pool.id` → pool_id (string, confidence 1.0)
  - `session.pool.channels` → channel_types (list, confidence 1.0)
  - `session.pool.mentionable_pools` → dict alias→pool_id (omitido se vazio)
  - visibility: `agents_only`; TTL 24h com NX (não sobrescreve TTL existente)
- `_process_message()`: chama `_write_pool_context()` via `asyncio.create_task()` após `result.allocated == True` (fire-and-forget, nunca falha o roteamento).

---

## Audit Profile — LGPD Compliance Role (2026-05-14)

Perfil de auditoria LGPD implementado como módulo ABAC `audit`, sem criar role fixa. Docs: [`docs/arcos/audit-lgpd.md`](docs/arcos/audit-lgpd.md).

**`infra/modules.yaml`** — Módulo `audit` com 5 campos: `sessions`, `mcp_calls`, `user_access`, `data_requests`, `config_snapshot`. Seedado em `auth.module_registry`.

**`packages/analytics-api/src/plughub_analytics_api/clickhouse.py`**
- DDL `_DDL_MCP_AUDIT_LOG`: `ReplacingMergeTree`, colunas `event_id/tenant_id/session_id/instance_id/server_name/tool_name/allowed/injection_detected/duration_ms/source/masked_input_fields/timestamp/date`. Idempotente para replay de consumer.
- DDL `_DDL_AUDIT_ACCESS_LOG`: `MergeTree` (não-deduplicado por design — todo acesso DPO é registro permanente). Colunas: `access_id/tenant_id/accessed_by/resource/resource_id/accessed_at/date`.
- Ambos adicionados a `_ALL_DDL`.
- Métodos: `insert_mcp_audit_log()`, `insert_audit_access_log()`, `query_mcp_audit_calls()` (filtros: tenant_id, session_id, from_dt, to_dt, masked_only, limit).

**`packages/analytics-api/src/plughub_analytics_api/models.py`**
- `parse_mcp_audit_event()` agora retorna `list[dict] | None` (dual-write pattern). Primeiro item: `session_timeline` row (existente). Segundo item: novo `mcp_audit_log` row com `masked_input_fields` completo. Compatível com `_process_message` que já normaliza para lista.

**`packages/analytics-api/src/plughub_analytics_api/consumer.py`**
- `_write_row()`: dois novos branches `mcp_audit_log` e `audit_access_log`.

**`packages/analytics-api/src/plughub_analytics_api/audit_router.py`** (novo)
- Router FastAPI prefix `/v1/audit`.
- `_require_audit_access(field, credentials)`: decodifica JWT, verifica `module_config.audit.{field} >= read_only`, impõe tenant isolation. Raises 401/403/503.
- `GET /v1/audit/sessions/{session_id}/messages`: requer `audit.sessions`. Lê `analytics.messages` (conteúdo mascarado). Side-effect: insere linha imutável em `audit_access_log` (fire-and-forget).
- `GET /v1/audit/mcp-calls`: requer `audit.mcp_calls`. Tenant isolation JWT vs query param. Parâmetros: `session_id`, `from_dt`, `to_dt`, `masked_only=true`, `limit=200`.

**`packages/analytics-api/src/plughub_analytics_api/main.py`**
- Import e registro de `audit_router`.

**`packages/platform-ui/src/modules/audit/AuditPage.tsx`** (novo)
- 5 tabs: Sessions (ativo), MCP Calls (ativo), User Access / Data Requests / Config Snapshot (stubs "Em desenvolvimento").
- Auth via `getAccessToken()` (JWT in-memory). Base URL `VITE_ANALYTICS_URL`.
- Warning banner vermelho em todos os tabs: "Todo acesso a esta área é registrado em log de auditoria".
- SessionsTab: busca por session_id, renderiza timeline com role color chips e tokens mascarados.
- McpCallsTab: filtros (session_id, masked_only checkbox, from/to datetime), tabela com `masked_input_fields` como badges amarelos, badge de injection_detected.

**`packages/platform-ui/src/shell/Sidebar.tsx`**
- Item standalone "Auditoria LGPD" (ícone 🔍) entre Analytics e Configuração. Roles: admin, supervisor. ABAC gate: `audit.sessions`.

**`packages/platform-ui/src/app/routes.tsx`**
- Import `AuditPage` + rota `{ path: 'audit', element: <AuditPage /> }`.

**`packages/platform-ui/src/i18n/locales/en/shell.json`** — `nav.audit = "Audit"`
**`packages/platform-ui/src/i18n/locales/pt-BR/shell.json`** — `nav.audit = "Auditoria LGPD"`

---

## Bugfix — G2: on_human_end hooks não disparavam para especialistas plughub-native (2026-05-13)

**`packages/orchestrator-bridge/src/plughub_orchestrator_bridge/main.py`**

**Causa raiz**: quando o agente humano clicava "Desligar" enquanto um especialista `plughub-native` (ex: `skill_auth_form_v1`) ainda estava em execução, o bridge corretamente armazenava `pending_on_human_end` no Redis e adiava o disparo dos hooks `on_human_end`. No entanto, o bloco responsável por processar esse key deferido — SREM de `active_ai_specialists` + GETDEL `pending_on_human_end` — existia **apenas** no handler Kafka de `conference_agent_completed` (linha ~2500), que é publicado pela ferramenta MCP `agent_done` do `runtime.ts`. Para agentes `plughub-native`, o skill flow executa **inline** no bridge via `activate_native_agent` (await bloqueante na linha 1867), sem nunca chamar a ferramenta MCP — portanto o evento Kafka nunca era publicado, e o `pending_on_human_end` ficava preso no Redis até expirar (300s). Resultado: wrapup e NPS nunca chegavam ao Console nem ao webchat quando havia especialista nativo ativo no momento do "Desligar".

**Fix** (`main.py`):
1. Inicializa `_is_hook_agent = False` antes do bloco `if conference_id and native_instance_id:` (linha 1734) para garantir que a variável esteja sempre definida no escopo.
2. Adicionado bloco **G2 inline** ao fim da seção `if conference_id:` no caminho de conclusão nativo (após Fase B/C, antes de `elif framework == "human":`). Quando `activate_native_agent` retorna e o especialista não é hook agent (`not _is_hook_agent`):
   - SREM `native_instance_id` de `session:{id}:active_ai_specialists`
   - SCARD para verificar se restam outros especialistas
   - Se 0: GETDEL `pending_on_human_end` → se existir, chama `_write_pre_hook_context` + `fire_pool_hooks(on_human_end)` + `_hook_timeout_guard`
   - Fallbacks: sem hooks no pool → `_trigger_contact_close`; sem http/pool/tenant → `_trigger_contact_close`

**Log de diagnóstico adicionado**: `"All native specialists done — dispatching deferred on_human_end: session=... pool=..."` + `"on_human_end hooks dispatched (native deferred): session=... pool=... count=..."`.

**Verificado em produção**: NPS aparece no webchat com pergunta de 0-10 (botões de lista); wrapup aparece no Console com pergunta de classificação e campo de resumo. Ambos os agentes listados em "AGENTES AI NA SESSÃO" no painel direito do Console.

---

## Bugfix — NPS buttons no webchat + masked_fields em menu.render (2026-05-13)

**`packages/mcp-server-plughub/src/tools/bpm.ts`**

Dois bugs na entrega de mensagens pós-`agent_done` via array visibility:

**Bug 1 — NPS buttons não apareciam no webchat**
Para `isArrayVis && hasMenu`, o stream recebia `type: "message"` em vez de `type: "interaction_request"`. O `StreamSubscriber` do channel-gateway converte `interaction_request` → WS `interaction.request` (com botões clicáveis); `message` → WS `msg.text` (texto plano, sem botões). Resultado: o cliente via webchat recebia a pergunta NPS como texto em vez de ver os botões de resposta.
- Fix: quando `isArrayVis && hasMenu`, escreve `type: "interaction_request"` no stream com payload completo (`menu_id`, `interaction`, `prompt`, `options`, `fields`, `masked_fields`).
- Mensagens de texto simples (`isArrayVis && !hasMenu`) continuam usando `type: "message"` sem alteração.

**Bug 2 — `masked_fields` ausente no `menu.render`**
O evento `menu.render` publicado em `agent:events:{sessionId}` (para o Console) não incluía `masked_fields`. O handler `menu.render` do `AgentAssistContext` já suportava o campo; apenas não o recebia.
- Fix: `menu.render` agora inclui `masked_fields: parsed.menu!.masked_fields ?? undefined`.

---

## Bugfix — NPS customer reply visível ao agente após sessionClosed (2026-05-13)

**`packages/platform-ui/src/modules/agent-assist/AgentAssistContext.tsx`**

No handler `message.text`, após `sessionClosed === true` mensagens com `author === "customer"` eram adicionadas ao array de mensagens do contato. Isso fazia a resposta do cliente ao NPS (ex: "1") aparecer na view do agente humano, que já havia encerrado seu atendimento.

Fix: ao processar `message.text`, se `c.sessionClosed && msg.author === "customer"` o evento é silenciosamente ignorado. Mensagens de hook agents (wrapup, NPS) ainda chegam normalmente pois têm `author === "agent_ai"`.

---

## Bugfix — Pool Lifecycle Hooks (wrapup + NPS) não chegavam ao Console (2026-05-13)

Dois bugs introduzidos durante Arc 11 Fases C+D impediam que wrapup e NPS funcionassem.

**Bug 1 — `packages/mcp-server-plughub/src/server.ts`**
O WS server desfazia a subscription de `agent:events:{sessionId}` ao receber qualquer `session.closed`, inclusive o `reason=client_disconnect` publicado pelo bridge ANTES de disparar os hooks `on_human_end`. Com isso, todas as mensagens de wrapup/NPS eram perdidas quando o cliente desconectava.
- Fix: só cancela subscription quando `reason === "agent_done"` (publicado por `_trigger_contact_close()` após todos os hooks terminarem).

**Bug 2 — `packages/orchestrator-bridge/src/plughub_orchestrator_bridge/main.py` linha 878**
O guarda G2 (`active_ai_specialists`) excluía hooks do set via chave `hook_conf` apenas para `on_human_end` e `post_human`. Agentes de hook `on_human_start` (como o Echo evaluator) não recebiam a chave e entravam no set. Como rodam em loop `receive→notify→receive` indefinido, nunca saíam — quando o agente humano clicava Desligar, G2 detectava count=1 e adiava os hooks para sempre.
- Fix: incluir `on_human_start` no conjunto de tipos que gravam a chave `hook_conf`.

**`packages/platform-ui/src/modules/agent-assist/AgentAssistContext.tsx`**
- `session.closed reason=client_disconnect`: mantém o contato ativo (`sessionClosed=true`, `pendingCloseModal=false`) em vez de removê-lo — hooks ainda precisam interagir com o agente humano.
- `session.closed reason=agent_done`: remove o contato (todos os hooks completaram).

**`packages/platform-ui/src/modules/agent-assist/AgentAssistPage.tsx`**
- `handleSend`: quando `sessionClosed=true` força `visibility=agents_only` para que respostas do agente após o encerramento vão para o BLPOP do hook (não para o cliente).

---

## Arc 11 — Console Orchestration: Fases C + D — Delegar Tarefa + OrchestrationTab (2026-05-13)

Spec completa: [`docs/arcos/arc11-console-orchestration.md`](docs/arcos/arc11-console-orchestration.md)

### Fase C (F3) — Delegar Tarefa: seleção de mensagens + drawer

**`packages/platform-ui/src/modules/agent-assist/types.ts`** (Task #100)
- `ActiveTab` expanded: `"estado" | "capacidades" | "contexto" | "orquestracao"`.
- `AiState`, `AiParticipantInfo`, `PipelineTransition` interfaces added (synced from WSL path).
- `SupervisorState.ai_participants?: AiParticipantInfo[]` + `pipeline_transitions?: PipelineTransition[]` added.

**`packages/platform-ui/src/modules/agent-assist/components/MessageBubble.tsx`** (Task #100)
- New props: `isSelected?: boolean`, `onToggleSelection?: () => void`.
- When `onToggleSelection` provided: wraps bubble in `group` flex row with hover-only checkbox button.
- Selected state: `ring-1 ring-orange-400 rounded-2xl` on bubble wrapper; checkbox shows orange fill + `✓`.

**`packages/platform-ui/src/modules/agent-assist/components/ChatArea.tsx`** (Task #100)
- New props: `selectedMessageIds?: Set<string>`, `onToggleSelection?: (messageId: string) => void`.
- Orange selection toolbar shown above scroll area when `selectedMessageIds.size > 0`.
- Each `MessageBubble` receives `isSelected` + `onToggleSelection` forwarded.

**`packages/platform-ui/src/modules/agent-assist/components/ActionBar.tsx`** (Task #100)
- `DelegarButton` sub-component: orange accent, count badge overlay (max "9+") when `selectedCount > 0`.
- New `ActionBarProps`: `selectedCount?: number`, `onDelegar?: () => void`.
- Button shown when `onDelegar` is provided and `mentionableAgents.length > 0`.

**`packages/platform-ui/src/modules/agent-assist/components/DelegarTarefaDrawer.tsx`** (Task #100, new file)
- Slide-in right drawer (fixed, `w-80`); 3-section form: agent picker + instruction textarea + visibility radio.
- Pre-fills instruction from `prefilledContext` prop (concatenation of selected message texts).
- Escape closes; ⌘↵ submits. Orange accent throughout.

**`packages/platform-ui/src/modules/agent-assist/AgentAssistPage.tsx`** (Task #100)
- State: `selectedMessageIds: Set<string>`, `showDelegarDrawer`, `delegatedAgents: Set<string>`.
- `handleToggleMessageSelection(messageId)` — toggles set membership.
- `prefilledContext` — joins selected message texts with `\n---\n` separator.
- `handleDelegate(agentTypeId, instruction, visibility)` — calls `handleSend(@{id} {instr})`, adds toast, clears selection, closes drawer.
- `useEffect` resets selection on contact change.
- Fixed TDZ bug: `selected` declaration moved before hook calls.

### Fase D (F4) — OrchestrationTab: supervisor view + interventions

**`packages/mcp-server-plughub/src/server.ts`** (Task #101)
- `GET /api/supervisor_state/:sessionId`: extended with `ai_participants` (array from `session:{id}:ai_agents` SET + per-instance metadata + pipeline-state derivation) and `pipeline_transitions` (from `{tenant}:pipeline:{sessionId}.transitions[]`). Same logic as MCP tool `supervisor.ts`.
- `POST /api/inject-context/:sessionId`: writes ContextStore entry `{key: ContextEntry}` to `{tenantId}:ctx:{sessionId}` Redis hash. Body: `{ key, value, confidence?, source? }`. Reads `tenantId` from `session:{id}:meta`.
- `POST /api/force-complete/:sessionId`: updates `{tenantId}:pipeline:{sessionId}` status → `"completed"` with `force_complete_reason`, `force_complete_outcome`, `force_complete_at`. Body: `{ reason?, outcome? }`.

**`packages/platform-ui/src/modules/agent-assist/hooks/useSupervisorState.ts`** (Task #102)
- Return type changed from `SupervisorState | null` to `{ state: SupervisorState | null; refresh: () => void }`.
- `refresh` exposes the internal `fetchState` callback for on-demand re-fetch (used by OrchestrationTab after supervisor interventions).

**`packages/platform-ui/src/modules/agent-assist/components/AiParticipantCard.tsx`** (Task #102, synced to Windows path)
- Copied from WSL path to Windows development path (the two are physically distinct directories).

**`packages/platform-ui/src/modules/agent-assist/components/tabs/OrchestrationTab.tsx`** (Task #102, new file)
- Three sections: (1) AI agents list (`AiParticipantCard` per participant); (2) pipeline transition timeline — reverse-chronological, step-type icons, formatted timestamps; (3) Supervisor interventions: `InjectContextForm` (key + value + confidence, POST to `/api/inject-context`) + `ForceCompleteConfirm` (2-step confirm, POST to `/api/force-complete`).
- Both intervention forms call `onRefresh()` on success to trigger immediate state re-poll.

**`packages/platform-ui/src/modules/agent-assist/components/RightPanel.tsx`** (Task #102)
- New props: `mcpBase?: string`, `onRefreshState?: () => void`.
- `OrchestrationTab` imported and rendered when `activeTab === "orquestracao"`.

**`packages/platform-ui/src/modules/agent-assist/AgentAssistPage.tsx`** (Task #102)
- `useSupervisorState` destructured as `{ state: supervisorState, refresh: refreshSupervisorState }`.
- Tab bar: "Orq." tab added; gated on `session.role === "supervisor" || "admin"`.
- `RightPanel` receives `onRefreshState={refreshSupervisorState}`.

---

## Arc 11 — Console Orchestration: Fase A — Cartões de Participantes AI (2026-05-13)

Spec completa: [`docs/arcos/arc11-console-orchestration.md`](docs/arcos/arc11-console-orchestration.md)

### Backend — `supervisor_state` extended with `ai_participants[]`

**`packages/orchestrator-bridge/src/plughub_orchestrator_bridge/main.py`** (Task #94)
- Native agent activation path: after `segment_id` write, sets `session:{session_id}:ai_participant:{instance_id}` (TTL 4h) with `{ role, agent_type_id, pool_id, segment_id, joined_at }`.
- YAML fallback path: same write after `sadd ai_agents`, `role: "primary"`.

**`packages/mcp-server-plughub/src/tools/supervisor.ts`** (Task #94)
- Section 8 added: reads `session:{id}:ai_agents` SET, pipeline_state (`{tenant_id}:pipeline:{session_id}`), `menu:waiting:{session_id}` hash, `receive:waiting:{session_id}` hash.
- Per instance: reads `session:{id}:ai_participant:{instance_id}` for metadata; derives `ai_state { current_step, step_type, step_status, waiting_for, since_ms }`.
- Step derivation rules: menu:waiting hit → `step_status=waiting, step_type=menu`; receive:waiting hit → `waiting, receive`; pipeline.status=`suspended` → `waiting, suspend`; `completed` → `done`; `failed` → `error`; otherwise → `running`, type inferred from step_id naming.
- `ai_participants: AiParticipant[]` added to supervisor_state JSON response.

### Frontend — `AiParticipantCard` + polling + drawer

**`packages/platform-ui/src/modules/agent-assist/types.ts`** (Task #95)
- `AiState` interface: `current_step`, `step_type`, `step_status`, `waiting_for`, `since_ms`.
- `AiParticipantInfo` interface: `instance_id`, `agent_type_id`, `pool_id`, `role`, `segment_id`, `joined_at`, `ai_state`.
- `SupervisorState.ai_participants?: AiParticipantInfo[]` added.

**`packages/platform-ui/src/modules/agent-assist/hooks/useSupervisorState.ts`** (Task #95)
- Added 3 s periodic `setInterval` poll alongside the existing event-driven refresh. Keeps AI participant step state live even when no WS events arrive.

**`packages/platform-ui/src/modules/agent-assist/components/AiParticipantCard.tsx`** (Task #95, new file)
- Card shows: `🤖` icon, formatted agent_type_id, role badge (primary/specialist/supervisor), status chip (running/waiting/done/error with CSS animation for running), step ID formatted as `type: label`, elapsed time, waiting_for pill when applicable.
- Click opens drawer: agent metadata + step detail + last 5 messages from this agent in the session + "Encerrar segmento" button (disabled when status=done).
- "Encerrar segmento" calls `onTerminateSegment(instance_id)` which sends `@{instance_id} terminate_self` via WS and closes the drawer.

**`packages/platform-ui/src/modules/agent-assist/components/tabs/EstadoTab.tsx`** (Task #95)
- New props: `sessionMessages?: ChatMessage[]`, `onTerminateSegment?: (instanceId: string) => void`.
- "Agentes AI na Sessão" section added at top of tab (only shown when `ai_participants.length > 0`), renders one `AiParticipantCard` per participant.

**`packages/platform-ui/src/modules/agent-assist/components/RightPanel.tsx`** (Task #95)
- New props: `sessionMessages?: ChatMessage[]`, `onTerminateSegment?` threaded down to `EstadoTab`.

**`packages/platform-ui/src/modules/agent-assist/AgentAssistPage.tsx`** (Task #95)
- `handleTerminateSegment(instanceId)` → calls `handleSend(\`@${instanceId} terminate_self\`)`.
- `RightPanel` call updated with `sessionMessages={selected?.messages ?? []}` and `onTerminateSegment={handleTerminateSegment}`.

---

## Arc 11 — Console Orchestration: Fase B — Adicionar Especialista (2026-05-13)

Spec completa: [`docs/arcos/arc11-console-orchestration.md`](docs/arcos/arc11-console-orchestration.md)

### Backend — `GET /v1/pools/:poolId/mentionable-agents` (Task #97)

**`packages/agent-registry/src/routes/pools.ts`** (Task #97)
- New route `GET /v1/pools/:pool_id/mentionable-agents` inserted before the generic `GET /v1/pools/:pool_id` (critical ordering — prevents Express matching sub-path as `:pool_id`).
- Reads `pool.mentionable_pools` (JSON array of pool_id strings); returns `{agents:[]}` when empty.
- `prisma.agentType.findMany` WHERE `tenant_id` + `status: active` + `pools.some.pool.pool_id IN mentionablePoolIds`; includes pools JOIN to resolve `pool_id` per agent.
- Response shape: `{ agents: [{ agent_type_id, pool_id, description, capabilities }] }`.

### Frontend — `AdicionarEspecialistaButton` + `useMentionableAgents` (Task #98)

**`packages/platform-ui/src/modules/agent-assist/types.ts`** (Task #98)
- `MentionableAgent` interface: `{ agent_type_id, pool_id: string|null, description: string|null, capabilities: Record<string, unknown> }`.
- `PoolInfo.mentionable_pools?: string[]` added.

**`packages/platform-ui/src/modules/agent-assist/hooks/useMentionableAgents.ts`** (Task #98, new file)
- Fetches `GET /v1/pools/${poolId}/mentionable-agents` on `poolId` change.
- Returns `MentionableAgent[]`; defaults to `[]` on error or when `poolId` is null.
- Cleanup cancellation pattern (`cancelled` flag) prevents stale state on rapid poolId changes.

**`packages/platform-ui/src/modules/agent-assist/components/ActionBar.tsx`** (Task #98)
- `AdicionarEspecialistaButton` sub-component with 2-step inline flow:
  - Step 1: scrollable dropdown list of agents — name, description, `agent_type_id`, `pool_id`.
  - Step 2: back chevron + context `<textarea>` (⌘↵ submits, Esc closes) + "Convidar" button.
  - Purple accent (`text-purple-700 bg-purple-50 border-purple-200`); hidden when `mentionableAgents` is empty.
- New `ActionBarProps`: `mentionableAgents?: MentionableAgent[]`, `onAddSpecialist?: (agentTypeId: string, context: string) => void`.
- Button rendered before `<IniciarProcessoButton>` in the action row.

**`packages/platform-ui/src/modules/agent-assist/AgentAssistPage.tsx`** (Task #98)
- `useMentionableAgents(currentPoolId)` — `currentPoolId = selected?.poolId ?? null`.
- `handleAddSpecialist(agentTypeId, context)` → `handleSend(`@${agentTypeId} ${context}`)` + info toast.
- `ActionBar` call updated: `mentionableAgents={mentionableAgents}` + `onAddSpecialist={handleAddSpecialist}`.

---

## Arc 12 — Agent Business Events: Fases C + D (2026-05-13)

Spec completa: [`docs/arcos/arc12-agent-business-events.md`](docs/arcos/arc12-agent-business-events.md)

### Fase C — 3 analytics endpoints

**`packages/analytics-api/src/plughub_analytics_api/reports_query.py`** (Task #92)
- `query_agent_events_series()` / `_fetch_agent_events_series()` — time-series by period × category; granularity `hour|day|week`; `startsWith(category, ...)` prefix filter
- `query_agent_events_summary()` / `_fetch_agent_events_summary()` — aggregation by `group_by` (`category|skill_id|pool_id|agent_type_id`); pagination; validated against `VALID_GROUP_BY` set
- `query_agent_events_categories()` / `_fetch_agent_events_categories()` — catalogue of active category prefixes (max 500); pool/skill filter

**`packages/analytics-api/src/plughub_analytics_api/reports.py`** (Task #92)
- `GET /reports/agent-events/series` — params: `tenant_id`, `from`, `to`, `category`, `pool_id`, `skill_id`, `granularity`, `format`
- `GET /reports/agent-events/summary` — params: `tenant_id`, `from`, `to`, `category`, `pool_id`, `group_by`, `page`, `page_size`, `format`
- `GET /reports/agent-events/categories` — params: `tenant_id`, `from`, `to`, `pool_id`, `skill_id` (JSON only)

### Fase D — Dashboard cards (2 cards)

**`packages/analytics-api/src/plughub_analytics_api/display_formatters.py`** (Task #93)
- `_fetch_agent_event_timeseries_sync()` — daily aggregation (count + avg value) for a category prefix
- `_fetch_agent_event_summary_sync()` — grouped aggregation (top 20 groups by event count)
- `fmt_agent_event_timeseries()` — async; returns `LineChartData` with series `['Total', 'Média']`
- `fmt_agent_event_summary()` — async; returns `BarChartData`; `group_by` validated against allowed set

**`packages/analytics-api/src/plughub_analytics_api/display.py`** (Task #93)
- `GET /reports/display/agent-event-timeseries` — params: `tenant_id`, `from`, `to`, `category`, `pool_id`
- `GET /reports/display/agent-event-summary` — params: `tenant_id`, `from`, `to`, `category`, `pool_id`, `group_by`
- Route count updated from 14 → 16 in module docstring

**`packages/platform-ui/src/dashboard/catalog.ts`** (Task #93)
- `agent-event-timeseries` — `LineChartData`; configurable params: `category`, `pool_id`
- `agent-event-summary` — `BarChartData`; configurable params: `category`, `pool_id`, `group_by`; `group_by` placeholder lists all valid values for discoverability

---

## Arc 12 — Agent Business Events: Fases A + B (2026-05-13)

Spec completa: [`docs/arcos/arc12-agent-business-events.md`](docs/arcos/arc12-agent-business-events.md)

### Fase A — Schema + ClickHouse DDL + analytics-api consumer

**`packages/schemas/src/agent-events.ts`** (Task #88)
- `AGENT_EVENT_CATEGORY_REGEX` — `^[a-z0-9_]+(\.[a-z0-9_]+){1,4}$` (2–5 dot-separated segments)
- `AGENT_EVENT_PII_TAG_KEYS` — set of 20 blocked PII keywords (`cpf`, `cnpj`, `phone`, `email`, `token`, `api_key`, …)
- `AgentBusinessEventSchema` — full event schema with `event_id`, `tenant_id`, `session_id`, `journey_id` (nullable), `agent_type_id`, `skill_id`, `pool_id`, `category`, `category_l1..l4` (pre-decomposed), `value`, `tags`, `emitted_at`
- `AgentEventInputSchema` — agent-facing input with max 10 tags, 64-char limits
- `decomposeCategoryLevels()` — splits category into `{l1, l2, l3, l4}` with empty-string defaults
- Exported from `packages/schemas/src/index.ts`

**`packages/analytics-api/src/plughub_analytics_api/clickhouse.py`** (Task #89)
- `_DDL_AGENT_BUSINESS_EVENTS` — `MergeTree()` (immutable events, no dedup), partitioned by month, ORDER BY `(tenant_id, category_l1, category_l2, category_l3, emitted_at)`, TTL 2 years
- `_AGENT_BUSINESS_EVENT_COLS` + `insert_agent_business_event()` method on `AnalyticsStore`
- `_agent_business_event_row()` row builder with fallback category decomposition and tag coercion to `dict[str, str]`

**`packages/analytics-api/src/plughub_analytics_api/models.py`** (Task #89)
- `parse_agent_business_event()` — validates required fields, coerces `value` to float, decomposes category levels, sanitises tags

**`packages/analytics-api/src/plughub_analytics_api/consumer.py`** (Task #89)
- Topic `agent.events` added to `_TOPICS`
- Parser `parse_agent_business_event` added to `_PARSERS`
- `agent_business_events` table dispatch added to `_write_row()`

### Fase B — MCP Tool `agent_event`

**`packages/mcp-server-plughub/src/tools/agent-events.ts`** (Task #90)
- `AgentEventToolInputSchema` — extends `AgentEventInputSchema` with `session_token` + `session_id`
- Full validation chain:
  - JWT decode (`verifySessionToken`) → resolves `tenant_id`, `agent_type_id`, `instance_id`
  - PII tag key check (case-insensitive against `AGENT_EVENT_PII_TAG_KEYS`)
  - Namespace isolation: `category_l1 === pool_id` from `session:{id}:meta` (enforced when pool_id resolvable)
  - Rate limit: `{tenant_id}:agent_event_count:{session_id}` Redis INCR, max 50 (env `AGENT_EVENT_RATE_LIMIT`)
- Context enrichment from Redis `session:{id}:meta`: `pool_id`, `skill_id`, `journey_id` (best-effort, non-fatal)
- Publishes to Kafka topic `agent.events` with full `AgentBusinessEvent` payload (category_l1..l4 pre-decomposed)
- Returns `{ event_id, category, value, emitted_at, session_event_count }`

**`packages/mcp-server-plughub/src/server.ts`** (Task #90)
- Import `registerAgentEventTools` + `AgentEventDeps`
- `agentEventDeps = { redis, kafka }` wired
- `registerAgentEventTools(server, agentEventDeps)` registered

---

## AI Gateway — AccountSelector: preferred_config_ids[] por InferenceRequest (2026-05-13)

### `packages/ai-gateway/src/plughub_ai_gateway/account_selector.py` (Task #76)
- `LLMAccount`: novo campo `config_id: str = ""` — GatewayConfig ID do agent-registry; identifica a qual configuração o API key pertence
- `AccountSelector.pick()`: novo parâmetro `preferred_config_ids: list[str] | None`. Quando não-vazio: primeira tentativa restrita a accounts cujo `config_id` está na lista; se nenhum disponível (throttled/RPM), cai graciosamente para o pool completo (degradação graciosa — nunca falha por causa do filtro)
- Método privado `_pick_least_loaded()` extraído para evitar duplicação entre preferred pass e full-pool pass

### `packages/ai-gateway/src/plughub_ai_gateway/models.py` (Task #76)
- `InferenceRequest`: novo campo `preferred_config_ids: list[str] = []` — lista de GatewayConfig IDs a preferir para esta chamada; populado a partir de `EvaluationCampaign.gateway_config_ids`

### `packages/ai-gateway/src/plughub_ai_gateway/inference.py` (Task #76)
- `InferenceEngine._call_with_fallback()`: aceita `preferred_config_ids` e passa para `account_selector.pick()` — tanto na chamada primária quanto na retry após throttle
- `InferenceEngine.infer()`: extrai `req.preferred_config_ids` e passa para `_call_with_fallback()`

### `packages/ai-gateway/src/plughub_ai_gateway/config.py` (Task #76)
- `Settings`: dois novos campos `anthropic_config_ids: str = ""` e `openai_config_ids: str = ""` (env `PLUGHUB_ANTHROPIC_CONFIG_IDS`, `PLUGHUB_OPENAI_CONFIG_IDS`)
- `get_anthropic_config_ids()` e `get_openai_config_ids()`: parseia CSV, retorna lista paralela às API keys; sempre padded com `""` para manter alinhamento index-a-index

### `packages/ai-gateway/src/plughub_ai_gateway/main.py` (Task #76)
- Passa `config_id=anthropic_config_ids[idx]` e `config_id=openai_config_ids[idx]` ao construir cada `LLMAccount`; backward compat total (sem config_id = `""` = sem preferência)

### `packages/ai-gateway/.../tests/test_account_selector.py` (Task #76)
- 3 novos testes: `preferred_config_ids` seleciona account correto, fallback quando preferido throttled, lista vazia = comportamento normal
- 3 novos testes: `get_anthropic_config_ids()` parsing, padding, vazio

**Uso típico — isolamento de avaliação:**
```
PLUGHUB_ANTHROPIC_API_KEYS=sk-realtime,sk-evaluation
PLUGHUB_ANTHROPIC_CONFIG_IDS=gcfg_realtime,gcfg_evaluation
```
Então `EvaluationCampaign.gateway_config_ids=["gcfg_evaluation"]` → `InferenceRequest.preferred_config_ids=["gcfg_evaluation"]` → avaliação usa `sk-evaluation` sem competir com agentes realtime.

---

## Arc 6 — EvaluationCampaign: evaluation_pool_id + evaluation_calendar_id + gateway_config_ids (2026-05-13)

### `packages/schemas/src/evaluation.ts` (Task #74)
- `EvaluationCampaignSchema` extended with 3 new optional fields:
  - `evaluation_pool_id?: z.string()` — pool under evaluation; used as hard filter in `check_sample`
  - `evaluation_calendar_id?: z.string()` — calendar for business-hours SLA deadline calculation in `compute_expires_at`; takes precedence over legacy `schedule.calendar_id`
  - `gateway_config_ids?: z.array(z.string()).default([])` — GatewayConfig IDs allowed for the evaluation's AI agents (reserved for AI Gateway routing)

### `packages/evaluation-api/src/plughub_evaluation_api/db.py` (Task #75)
- Idempotent DDL migration via `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` for all 3 columns in `evaluation.campaigns`
- `create_campaign()`: accepts and inserts `evaluation_pool_id`, `evaluation_calendar_id`, `gateway_config_ids`
- `update_campaign()`: allowed-set extended with the 3 new fields
- `list_campaigns()`: optional `evaluation_pool_id` query filter added

### `packages/evaluation-api/src/plughub_evaluation_api/router.py` (Task #75)
- `CampaignCreate` and `CampaignUpdate` Pydantic models: added `evaluation_pool_id: str | None`, `evaluation_calendar_id: str | None`, `gateway_config_ids: list[str] = []`
- `GET /v1/evaluation/campaigns`: `evaluation_pool_id` query param forwarded to db filter
- `POST /v1/evaluation/campaigns/check-sample`: hard filter — if campaign has `evaluation_pool_id` and `session_meta.pool_id` doesn't match, returns `should_sample: false` immediately (before statistical sampling)

### `packages/evaluation-api/src/plughub_evaluation_api/sampling.py` (Task #75)
- `compute_expires_at()`: uses `campaign.evaluation_calendar_id` with fallback to legacy `schedule.calendar_id`

### `packages/platform-ui/src/types/index.ts` (Task #77)
- `EvaluationCampaign` interface: added `evaluation_pool_id?: string`, `evaluation_calendar_id?: string`, `gateway_config_ids?: string[]`

### `packages/platform-ui/src/modules/evaluation/CampaignsPage.tsx` (Task #77)
- `CreateModal`: added `usePoolOptions` hook (fetches `/v1/operational/pools`) and `useCalendarOptions` hook (fetches `/v1/calendars?tenant_id=`)
- Two new selectors in modal form: **Pool avaliado** (hard filter for sampling) + **Calendário de SLA** (business-hours deadline)
- New values passed to `createCampaign()` as `evaluation_pool_id` and `evaluation_calendar_id`
- Detail panel: two new info cards showing pool and calendar for selected campaign

### `packages/platform-ui/src/i18n/locales/{en,pt-BR}/evaluation.json` (Task #77)
- Added keys under `campaigns.modal`: `evaluationPoolLabel`, `selectPool`, `evaluationCalendarLabel`, `selectCalendar`
- Added keys under `campaigns.detail`: `evaluationPool`, `evaluationCalendar`, `noPool`, `noCalendar`

---

## Receive Step — Full Implementation + Bug Fixes (2026-05-12)

### `packages/skill-flow-engine/src/steps/receive.ts`
- **Sentinel clearing fix**: ao retornar com sucesso do BLPOP, limpa todas as chaves `*:__notified__` de `ctx.state.results` antes de retornar o payload. Sem essa limpeza, em flows cíclicos (receive → notify → receive) o sentinel `ecoar_mensagem:__notified__` = `"completed"` permanecia após a primeira iteração e bloqueava silenciosamente todas as execuções subsequentes do `notify`.
- **Activity flag (CrashDetector)**: seta `receive:active:{tenant}:{session}:{instance}` com TTL 30s e renova via `setInterval(15s)` durante o BLPOP — evita re-enfileiramento falso por expiração de heartbeat.
- **Lock renewal**: chama `ctx.renewLock(timeoutSec + 60)` antes do BLPOP; aborta graciosamente se o lock foi assumido por outra instância (crash recovery).
- **Inline notify (pre-block)**: envia `step.notify.message` com `visibility: agents_only` (padrão) antes de registrar no HASH e bloquear — sinaliza prontidão ao agente convidante.
- **Ciclos declarativos**: contador `_receive_iterations_{stepId}` em `pipeline_state.results`; ao atingir `max_iterations` zera o contador e retorna `on_max_iterations`.
- **BLPOP duplo**: monitora `receive:result:{sid}:{iid}` (evento) e `session:closed:{sid}` (sessão fechada) simultaneamente; retorna `on_disconnect` quando a sessão fecha durante espera.

### `packages/orchestrator-bridge/src/plughub_orchestrator_bridge/main.py`
- **Fix double re-arm para mensagens de customer**: em `process_contact_event`, o branch `if event_type == "message_sent"` adicionou um guard `if _ms_author_role == "customer": return` — mensagens de customer já são roteadas para `receive:waiting` por `process_inbound`; sem o guard, o evento analytics publicado ao Kafka (`conversations.events`) era consumido e roteava novamente, causando dois `BLPOP` wakes por mensagem de customer e dois bubbles `[Echo] aguardando...`.
- **`_route_to_receive_waiting`**: função helper que roteia eventos stream para instâncias bloqueadas em receive; filtra por `event_types`, `author_role`, `visibility`; echo suppression via `instance_id_of_author`; LPUSH em `receive:result:{sid}:{iid}`.
- **Bridge contract**: em cada `message_sent` de agente (publicado por `notification_send` ou `message_send` via `conversations.events` Kafka), o bridge roteia para os waiting receivers que passam no filtro; customer messages chegam diretamente de `process_inbound`.

### `packages/platform-ui/Dockerfile`
- Adicionado `resolver 127.0.0.11 valid=10s ipv6=off;` no bloco `server {}` do nginx — previne caching de IPs de containers após rebuilds (Docker DNS resolver interno).

### `packages/skill-flow-engine/skills/agente_evaluador_echo_v1.yaml`
- Skill de teste para o mecanismo receive → notify → receive (cyclic DAG).
- Ecoa cada mensagem de `primary` ou `customer` como nota `agents_only`.
- Valida: registro em `receive:waiting`, LPUSH do bridge, echo suppression, `inviter_participant_id` no ContextStore.
- Ativação via pool hook `on_human_start` de `retencao_humano`.
- `max_iterations: 100`, `timeout_s: 600`.

### `packages/schemas/src/skill.ts` (Task #79)
- `ReceiveStepFilterSchema`: `author_role?`, `visibility?`, `event_types?`
- `ReceiveStepNotifySchema`: `message`, `visibility?`
- `ReceiveStepSchema`: `type: "receive"`, `filter?`, `notify?`, `timeout_s?`, `max_iterations?`, `on_max_iterations?`, `output_as?`, `on_message`, `on_timeout?`, `on_disconnect?`, `on_failure`
- `StepSchema` union atualizado para incluir `ReceiveStepSchema`

### `packages/skill-flow-engine/src/redis-keys.ts` (Task #80)
- `receiveResult(sessionId, instanceId)` → `receive:result:{sid}:{iid}`
- `receiveWaiting(sessionId)` → `receive:waiting:{sid}`
- `sessionClosed(sessionId)` → `session:closed:{sid}`
- `activeInstance(tenantId, sessionId, instanceId)` → `receive:active:{tid}:{sid}:{iid}`

---

## Arc 10 Phase E — Dashboard Journey Cards (2026-05-11)

### `packages/analytics-api/src/plughub_analytics_api/display_formatters.py`
- `fmt_journey_active_count()`: MetricCardData — contagem de jornadas ativas com tendência vs período anterior (query `argMax(status)` no `journey_events` FINAL)
- `fmt_journey_resolution_rate()`: BarChartData — taxa de resolução (%) por `skill_id`; denominador = apenas jornadas terminais (`completed/failed/cancelled`)
- `fmt_journey_funnel()`: DonutData — distribuição de jornadas por status mais recente
- `fmt_journey_median_duration()`: BarChartData — duração mediana (p50) em minutos por `skill_id`; calcula `max(event_time) - min(event_time)` por jornada; filtra jornadas terminais
- Todos os formatters aceitam `skill_id: str | None` como filtro opcional
- Helpers privados `_fetch_journey_*_sync()` com queries ClickHouse `argMax` sobre `journey_events FINAL`

### `packages/analytics-api/src/plughub_analytics_api/display.py`
- 4 novos endpoints adicionados: `GET /reports/display/journey-active-count`, `journey-resolution-rate`, `journey-funnel`, `journey-median-duration`
- Docstring do módulo atualizada (14 rotas total)
- Import dos 4 novos formatters

### `packages/platform-ui/src/dashboard/catalog.ts`
- 4 novas entradas em `ENDPOINT_CATALOG`:
  - `journey-active-count` → `metric_card` (3×2) — param fixo `skill_id`
  - `journey-resolution-rate` → `bar_chart` (6×4) — param fixo `skill_id`
  - `journey-funnel` → `donut` (4×3) — param fixo `skill_id`
  - `journey-median-duration` → `bar_chart` (6×4) — param fixo `skill_id`

---

## Arc 10 Phase D.5 — Journey Spec Refinements (2026-05-11)

### `packages/schemas/src/journey.ts`
- `JourneyEventSchema`: campos opcionais `current_step`, `session_outcome`, `session_started_at`, `session_ended_at` adicionados ao tipo `journey_session_linked`

### `packages/workflow-api/src/plughub_workflow_api/kafka_emitter.py`
- `emit_journey_session_linked()`: aceita `current_step`, `session_outcome`, `session_started_at`, `session_ended_at` (todos opcionais); inclui no payload quando presentes

### `packages/workflow-api/src/plughub_workflow_api/router.py`
- `respond_collect`: passa `current_step = instance.get("current_step")` para `emit_journey_session_linked` — captura o passo do workflow no momento em que a sessão de collect é vinculada

### `packages/workflow-api/src/plughub_workflow_api/journey_router.py`
- `JourneyLinkSessionRequest`: campos opcionais `current_step`, `session_outcome`, `session_started_at`, `session_ended_at`
- `link_session` endpoint: repassa todos os campos para `emit_journey_session_linked`

### `packages/analytics-api/src/plughub_analytics_api/clickhouse.py`
- `_DDL_JOURNEY_EVENTS`: 4 colunas novas — `current_step Nullable(String)`, `session_outcome Nullable(String)`, `session_started_at Nullable(DateTime64(3))`, `session_ended_at Nullable(DateTime64(3))`
- `_JOURNEY_EVENT_COLS`: lista de colunas atualizada
- `_journey_event_row()`: popula as 4 novas colunas do dict de entrada

### `packages/analytics-api/src/plughub_analytics_api/models.py`
- `parse_journey_event()`: extrai `current_step`, `session_outcome`, `session_started_at`, `session_ended_at` do payload Kafka

**Regra de interseção confirmada:** `db_create_journey` e MCP `journey_start` nunca tocam `sessions.journey_id` — verificado. Sessions só recebem `journey_id` via `collect.events` processado pelo Core (collect sessions exclusivamente). Múltiplas journeys podem compartilhar o mesmo `origin_session_id` sem conflito.

---

## Arc 10 Phase D — Console Journey Integration (2026-05-11)

### `packages/platform-ui/src/modules/agent-assist/types.ts`
- `PoolInfo`: campo `mentionable_journeys?: string[]` adicionado

### `packages/platform-ui/src/modules/agent-assist/AgentAssistContext.tsx`
- `fetchPools`: extrai `mentionable_journeys` do response da agent-registry e povoa `PoolInfo`

### `packages/platform-ui/src/modules/agent-assist/components/ActionBar.tsx`
- `IniciarProcessoButton`: dropdown de skill_ids de `mentionable_journeys` com label humanizado (remove prefixo `skill_` e sufixo `_vN`)
- `ActionBarProps`: props `mentionableJourneys?` e `onIniciarProcesso?` adicionadas
- Botão "🗺️ Processo ▾" renderizado na barra de ações quando `onIniciarProcesso` e `mentionableJourneys.length > 0`

### `packages/platform-ui/src/modules/agent-assist/components/tabs/HistoricoTab.tsx`
- `HistoricoTabProps`: campo `tenantId?: string | null` adicionado
- `useCustomerJourneys(tenantId, customerId)`: hook local que consulta `GET /analytics/reports/journeys` filtrando por `customer_id`; retorna apenas jornadas `active` e `suspended`
- `OpenJourneys`: seção "Processos em aberto" com badges coloridos por status, renderizada antes de "Contatos anteriores"

### `packages/platform-ui/src/modules/agent-assist/AgentAssistPage.tsx`
- `mentionableJourneys`: estado derivado de `availablePools` filtrado pelo `poolId` do contato selecionado
- `handleIniciarProcesso(skillId)`: `POST /v1/journeys` com `{ tenant_id, skill_id, session_id }` + toast feedback
- `ActionBar`: props `mentionableJourneys` e `onIniciarProcesso` conectadas
- `HistoricoTab`: prop `tenantId` conectada

### `packages/platform-ui/src/modules/agent-flow/ProcessosPage.tsx`
- `MergeButton`: componente dropdown "⛓ Unir jornadas" adicionado ao painel de detalhes de jornadas no tab Jornadas (visível apenas para jornadas `active`/`suspended`; chama `POST /v1/journeys/merge`; remove itens sem candidatos possíveis)

---

## Arc 10 Phase C — Journey Monitor (ProcessosPage) (2026-05-11)

### `packages/analytics-api/src/plughub_analytics_api/reports.py`
- Import `query_journeys_report` adicionado
- Novo endpoint `GET /reports/journeys`: filtra por `skill_id`, `status`, `customer_id`, `from_dt/to_dt`, paginação; retorna `data` (journey summaries), `kpis` (por skill_id), `meta`; status filter via HAVING sobre estado agregado

### `packages/platform-ui/src/modules/workflows/api/hooks.ts`
- Tipos `JourneyStatus`, `Journey`, `JourneyKpi` adicionados
- `useJourneys(tenantId, skillId?, status?)`: polling 15s para `GET /analytics/reports/journeys`; retorna `{ journeys, kpis, loading, refresh }`
- `useJourney(journeyId)`: fetch sob demanda de `GET /v1/journeys/{id}` (workflow-api)

### `packages/platform-ui/src/modules/agent-flow/ProcessosPage.tsx`
- Reestruturado com dois tabs: **Jornadas** (tab padrão) e **Instâncias**
- `JourneysTab`: KPI strip por skill_id (total, taxa resolução, duração p50) + lista de jornadas com filtro de status + painel de detalhes (timeline, session_count, sessão de origem, workflow_instance_id, customer_id)
- `InstancesTab`: visão existente de instâncias de workflow preservada sem alterações funcionais
- Ambos os tabs mantêm refresh manual acessível

---

## Arc 10 Phase B — Journey Automatic Session Linking (2026-05-11)

### `packages/workflow-api/src/plughub_workflow_api/db.py`
- Migration `ALTER TABLE workflow.collect_instances ADD COLUMN IF NOT EXISTS journey_id UUID` — propaga journey_id do instance para o collect
- `_row_to_instance()`: expõe `journey_id` no dict serializado
- `_row_to_collect()`: expõe `journey_id` no dict serializado
- `db_create_collect()`: aceita `journey_id: str | None = None` e persiste na tabela
- `db_create_journey_for_instance()` (NEW): cria Journey vinculada a instance já existente; idempotente (retorna journey existente se instance já tem `journey_id`); transacional com `SELECT FOR UPDATE`; deriva `skill_id = flow_id`, back-links `instances.journey_id`

### `packages/workflow-api/src/plughub_workflow_api/config.py`
- Adicionado `journey_topic: str = "journey.events"` a `Settings`

### `packages/workflow-api/src/plughub_workflow_api/kafka_emitter.py`
- `emit_collect_requested()`: aceita `journey_id: str | None = None`; inclui no payload quando presente

### `packages/workflow-api/src/plughub_workflow_api/router.py`
- `persist_collect`: lê `journey_id` do instance e repassa para `db_create_collect()` e `emit_collect_requested()`
- `respond_collect`: após `emit_collect_responded`, se `collect.journey_id` e `body.session_id`, emite `journey_session_linked` via `emit_journey_session_linked()` (falha não bloqueia resposta)
- Imports: `db_create_journey_for_instance`, `emit_journey_session_linked` adicionados

### `packages/workflow-api/src/plughub_workflow_api/journey_router.py`
- Novo endpoint `POST /v1/journeys/from-instance/{instance_id}`: chamado pelo skill-flow-worker quando `creates_journey:true`; idempotente; emite `journey_started` apenas para journeys recém-criadas
- Import `db_create_journey_for_instance` adicionado

### `packages/skill-flow-worker/src/workflow-client.ts`
- `WorkflowInstance`: campo `journey_id?: string` adicionado
- `WorkflowClient.createJourneyForInstance(instanceId, tenantId)` (NEW): `POST /v1/journeys/from-instance/{id}` com headers `x-tenant-id` + `x-internal:1`

### `packages/skill-flow-worker/src/engine-runner.ts`
- `EngineRunner.runInstance()`: antes de `engine.run()`, verifica `flowDefinition.creates_journey === true && !instance.journey_id`; se verdadeiro, chama `workflowClient.createJourneyForInstance()`; falha é não-fatal (log + continua sem journey)

---

## Arc 10 Phase A — Journey Backend Foundation (2026-05-11)

### `packages/schemas/src/journey.ts` (NEW)
- `JourneyStatusSchema`, `JourneySchema` — entidade Journey com todos os campos
- `JourneyEventTypeSchema`, `JourneyEventSchema` — 9 tipos de eventos Kafka
- `JourneyStartInputSchema/OutputSchema`, `JourneyLinkSessionInputSchema`, `JourneyMergeInputSchema` — contratos MCP tool
- Exportações adicionadas a `src/index.ts`

### `packages/workflow-api` — Journey CRUD + Router
- **`db.py`**: DDL `workflow.journeys` (self-referential FK `merged_into_journey_id`); migration `workflow.instances.journey_id`; `_row_to_journey()`; CRUD: `db_create_journey`, `db_get_journey`, `db_list_journeys`, `db_set_journey_workflow_instance`, `db_update_journey_status`, `db_merge_journeys` (transactional, protege contra re-merge)
- **`kafka_emitter.py`**: `emit_journey_started`, `emit_journey_session_linked`, `emit_journey_status_changed`, `emit_journey_merged`
- **`journey_router.py`** (NEW): `APIRouter(prefix="/v1/journeys")` — 5 endpoints: POST (create+trigger), GET /{id}, GET (list), POST /{id}/link-session, POST /{id}/merge, PATCH /{id}/status; helper `_trigger_workflow()` chama `/v1/trigger`
- **`main.py`**: `journey_router` registrado

### `packages/mcp-server-plughub/src/tools/journey.ts` (NEW)
- `journey_start` — cria Journey + dispara workflow; retorna `journey_id` + `workflow_instance_id`
- `journey_link_session` — vincula sessão adicional à journey
- `journey_merge` — absorve secondary no primary (irreversível); Phase D
- Registrado em `server.ts` com `JourneyDeps` (`WORKFLOW_API_URL`)

### `packages/analytics-api` — Kafka consumer + ClickHouse
- **`models.py`**: `parse_journey_event()` — mapeia 8 event types para `journey_events` table; status derivado de `_JOURNEY_STATUS_MAP`
- **`clickhouse.py`**: `_DDL_JOURNEY_EVENTS` (ReplacingMergeTree ORDER BY tenant_id, event_id); `_journey_event_row()`; `AnalyticsStore.insert_journey_event()`; DDL adicionado a `_ALL_DDL`
- **`consumer.py`**: tópico `journey.events` adicionado a `_TOPICS`, `_PARSERS`; `journey_events` adicionado a `_write_row`

---

## Arc 10 — Journey Merge/Split Spec (2026-05-11)

**`docs/modules/arc10-journey.md`**
- Status `merged` adicionado ao enum; campo `merged_into_journey_id` na entidade
- Kafka event types: `journey_merged` + `journey_split` (futuro — Phase F)
- Phase D: `journey_merge(journey_id_primary, journey_id_secondary)` — operação irreversível, secondary vira read-only
- Phase F: `journey_split` com 3 open decisions documentadas
- Invariantes: merged journey é read-only; merge é irreversível

**`TODO.md`**: Phase D e Phase F atualizados

---

## Skill Flow Editor — Folder Tree Sidebar (2026-05-11)

**`packages/platform-ui/src/modules/skill-flows/SkillFlowsPage.tsx`**
- Campo `folder?: string` no YAML lido como metadado de view-only (path 2-níveis, ex: `"clientes/retencao"`)
- `buildFolderTree(skills)` separa skills por `classification.type` em raízes Agentes/Workflows; constrói árvore de pastas
- Sidebar expandível: raízes + pastas com `paddingLeft` por profundidade; busca colapsa para lista plana
- Estado: `expandedRoots: Set<string>`, `expandedFolders: Set<string>` (ambos abertos por padrão)

---

## AgentFlowDeployPage — Skill Selector Grouping (2026-05-11)

**`packages/platform-ui/src/modules/agent-flow/AgentFlowDeployPage.tsx`**
- `groupSkillsForSelect(skills)` → `<optgroup>` HTML nativo com labels "Agentes", "Agentes / pasta", "Workflows / pasta"
- Skills separadas por `classification.type === 'orchestrator'` antes do agrupamento por `folder`

---

## AgentFlowDeployPage — Role-Based Access Control (2026-05-11)

**`packages/platform-ui/src/modules/agent-flow/AgentFlowDeployPage.tsx`**
- `hasEditRole(roles)` — `developer | admin` → pode configurar slot Próxima (Edit button)
- `hasOperateRole(roles)` — `operator | supervisor | admin` → pode promover e reverter
- Editar slot Próxima: `onEdit` prop do `SlotCard` só passada quando `canEdit`
- Botões Promover/Rollback: `disabled={!canOperate}` + `title` explicativo no hover
- Banner de permissão: exibido quando o usuário tem apenas um dos dois papéis, explicando o que pode e o que não pode fazer (dev vê azul, operator/supervisor vê verde; admin não vê banner)
- Lógica usa `session.roles[]` (array multi-role) — usuário com ambos os papéis tem acesso completo

---

## Arc 9 — Monitor + Console Scope Filtering (2026-05-11)

Transparently restricts what a supervisor sees in the live Monitor and Console based on JWT claims from Arc 9. Admin (empty arrays) is unrestricted.

**`packages/platform-ui/src/types/index.ts`**
- `Session.supervisedAgentTypes: string[]` adicionado — `[]` = irrestrito (admin); não-vazio = escopo Arc 9

**`packages/platform-ui/src/auth/AuthContext.tsx`**
- `CurrentUser.supervisedAgentTypes: string[]` adicionado
- `buildSession()` params: `supervised_agent_types?: string[]` opcional (backwards-compat); fallback `[]` para JWTs legados
- `currentUser` memo inclui `supervisedAgentTypes`

**`packages/platform-ui/src/modules/service/MonitorPage.tsx`**
- Heatmap: `pools` filtrado por `session.accessiblePools` antes de passar para `HeatmapGrid` — supervisores com escopo só vêem seus pools

**`packages/platform-ui/src/modules/config-recursos/HumanAgentsPage.tsx`**
- `LiveTab`: instâncias filtradas por `session.supervisedAgentTypes` após load — supervisores vêem só agentes do seu escopo

**`packages/platform-ui/src/modules/config-recursos/InstancesPage.tsx`**
- Instâncias AI filtradas por `session.supervisedAgentTypes` após load

*Console (AgentAssistPage)*: já filtrado via `accessiblePools` em `AgentAssistContext.fetchPools` — sem mudança necessária.

---

## Masking Bloco 2b — AgentAssistPage Token Rendering (2026-05-11)

**`packages/platform-ui/src/modules/agent-assist/components/ChatArea.tsx`**
- `useMaskingDisplayRules()` chamado uma vez no componente; `maskingRules` passado para cada `MessageBubble`

**`packages/platform-ui/src/modules/agent-assist/components/MessageBubble.tsx`**
- Prop `maskingRules?: MaskingRulesMap` adicionada
- `{message.text}` substituído por `{renderWithTokens(message.text, maskingRules)}` — tokens aparecem como chips no console do operador

---

## Masking Bloco 2 — Channel-Aware Display Architecture (2026-05-11)

**`packages/platform-ui/src/components/MaskedToken.tsx`** *(novo)*
- `TOKEN_RE`, `parseToken()` — regex e parser para formato `[category:tk_xxx:display]`
- `MaskedToken` component — chip estilizado com ícone de categoria, label e valor parcial; suporta `display_screen: display_partial | full_mask | hidden`
- `renderWithTokens(text, rules?)` — split de texto com substituição de tokens por `<MaskedToken>` components; retorna `React.ReactNode[]`
- `useMaskingDisplayRules()` — hook que lê namespace `masking` do Config API e retorna `MaskingRulesMap` per-category; defaults aplicados quando ausente
- `DEFAULT_DISPLAY_RULE` — `display_partial / silence / echo_to_customer=false / echo_to_operator=true`

**`packages/platform-ui/src/modules/service/components/SessionTranscript.tsx`**
- `EntryRow` migrado: usa `renderWithTokens(text, maskingRules)` ao invés de `extractText` raw — tokens aparecem como chips ao invés de strings brutas
- Removida `maskSensitiveContent()` (regex keyword crude) — substituída por token-aware rendering
- `useMaskingDisplayRules()` chamado no componente raiz; `maskingRules` passado para todos os `EntryRow`

**`packages/platform-ui/src/modules/masking/MaskingPage.tsx`**
- Section 5 "Display Rules by Category": grid de cards por categoria com 4 controles cada — `display_screen` (select), `display_voice` (select), `echo_to_customer` (toggle), `echo_to_operator` (toggle)
- Salva em namespace `masking`, chave `rule.{category}` no Config API
- `MiniToggle` component, `selectStyle`, `getMaskingRule()` helper, `saveMaskingRule()`

---

## Masking Bloco 1 — Security Fixes (2026-05-11)

Correções de segurança identificadas no mapeamento de vetores de vazamento. Nenhuma mudança de API ou schema — apenas remoção de exposição de dados sensíveis em logs.

**`packages/mcp-server-plughub/src/server.ts`**
- `menu_submit` handler: removido `resultText` dos três `console.log` (linhas 844, 854, 928). Valor coletado no menu (pode ser senha, CPF, etc.) não aparece mais em logs de servidor. Contexto de debug preservado (sessionId, resultKey, menu_id, pushed).

**`packages/sdk/src/mcp-interceptor.ts`**
- Fallback `AUDIT_WRITE_FAILED`: `input_snapshot` e `output_snapshot` agora são redactados como `"[REDACTED]"` antes de ir para `console.error`. Metadados de auditoria (tenant, session, tool, duration, allowed) são preservados para rastreabilidade LGPD.
- `_collectTokenPaths(value, path)`: novo método síncrono — varre os args originais (antes de resolução) e retorna caminhos de campo que contêm tokens mascarados `[category:tk_xxx:display]`.
- `_sanitizeSnapshotForAudit(snapshot, maskedPaths)`: substitui valores de campos mascarados por `"[MASKED]"` no snapshot de auditoria.
- `_audit()`: aceita `masked_input_fields?: string[]`; quando presente, o `input_snapshot` armazenado no `AuditRecord` tem os campos mascarados substituídos — os valores originais não chegam ao Kafka/ClickHouse. `masked_input_fields` lista quais campos eram sensíveis sem expor seus valores.
- Call site pré-resolution: `maskedInputFields` coletado dos args originais antes de `_resolveArgsTokens()`.

---

## Arc 9 — Agent Groups & Supervisor Scope (2026-05-11)

Implementação completa da entidade `AgentGroup` como camada de gestão de pessoas ortogonal a Pool.

**auth-api** (`packages/auth-api/src/plughub_auth_api/`)
- `db.py`: 5 novas tabelas DDL em schema `auth` (`agent_groups`, `agent_group_members`, `agent_group_users`, `agent_group_supervisors`, `agent_group_shifts`); funções CRUD completas para cada entidade; `resolve_supervisor_scope(pool, user_id, role)` — resolução de shifts com conversão de DOW (spec 0=Sun ↔ Python weekday 0=Mon via `(dow+1)%7`); sentinela `["__no_active_shift__"]` para grupos ativos sem membros configurados
- `jwt_utils.py`: `create_access_token()` estendido com `supervised_groups`, `supervised_agent_types`, `supervised_user_ids`; todos emitidos como `[]` (não `null`) para compatibilidade JSON
- `router.py`: `_make_token_response` convertida para `async`; chama `resolve_supervisor_scope` no login e no refresh; claims denormalizados no JWT
- `groups_router.py` (novo): `APIRouter(prefix="/v1/groups")`; CRUD de grupos + sub-recursos `/members`, `/users`, `/supervisors`, `/shifts`; todos os endpoints autenticados por `X-Admin-Token`
- `main.py`: `groups_router` registrado

**analytics-api** (`packages/analytics-api/src/plughub_analytics_api/`)
- `pool_auth.py`: `PoolPrincipal` estendido com `supervised_agent_types: list[str] | None`; decodificação do claim JWT no `optional_pool_principal()`
- `reports_query.py`: `_apply_agent_scope()` para filtro direto (segments, performance, availability); `_agent_scope_session_join()` para sessões via LEFT JOIN em `segments FINAL`; os 5 pares de funções `query_*/fetch_*` atualizados com parâmetro `supervised_agent_types`
- `reports.py`: os 5 endpoints de relatório passam `supervised_agent_types = pool_principal.supervised_agent_types`

**platform-ui** (`packages/platform-ui/src/`)
- `modules/groups/GroupsPage.tsx` (novo): lista de grupos com drawer lateral; 4 tabs — Info (edição de nome/descrição), Members (agent_type_ids + is_human), Supervisors (users), Shifts (dias da semana + janela horária + timezone + toggle ativo)
- `i18n/locales/en/groups.json` + `pt-BR/groups.json` (novos): namespace `groups` completo
- `i18n/locales/*/shell.json`: chave `nav.groups` adicionada
- `i18n/index.ts`: namespace `groups` registrado
- `shell/Sidebar.tsx`: entrada "Groups" adicionada ao grupo Configuração (ABAC `config.users`)
- `app/routes.tsx`: rota `config/groups` mapeada para `GroupsPage`

**CLAUDE.md**: seção Arc 9 adicionada; item removido do `## Pending`

---

## Backend-dependent Pages — AgentsPage Relatório + EventsPage + AnaliseProcessosPage (2026-05-09)

Implementação completa das últimas três páginas que dependiam de endpoints backend não existentes:

**AgentsPage sub-tab Relatório** (`packages/platform-ui/src/modules/contacts/AgentsPage.tsx`)
- Nova tab "Relatório" ao lado de "Monitor" com query a `GET /reports/agent-performance/daily` (endpoint já existia)
- Filtros: range de data, pool (dropdown dinâmico), agente (dropdown populado pelos dados)
- KPI strip: total sessões, resolução média ponderada, escalonamento médio ponderado, tempo médio
- Tabela sortável com mini-bars para resolução/escalonamento/transferência/humano; export CSV

**EventsPage backend** (`packages/analytics-api/`)
- `query_events` em `reports_query.py`: UNION ALL de 7 branches (session_opened, session_closed, message_sent, agent_done/routed, agent_pause, agent_ready, workflow events) com `CAST(NULL AS Nullable(String))` para type safety no ClickHouse
- Endpoint `GET /reports/events` com filtros: `session_id`, `pool_id`, `channel`, `event_type`; pool_scope RBAC via `accessible_pools`; paginação padrão
- Otimização: quando `event_type` é especificado, apenas os branches relevantes são incluídos no UNION
- Frontend `EventsPage.tsx` já existia e estava aguardando este endpoint

**AnaliseProcessosPage** (full stack)
- `query_workflow_summary` em `reports_query.py`: agrega `workflow_events` por `flow_id` ou `campaign_id` usando `countDistinctIf`; retorna `completion_rate`, `failure_rate`, `avg_duration_ms` (calculados em Python pós-query para evitar divisão por zero no SQL)
- Endpoint `GET /reports/workflow-summary?group_by=flow_id|campaign_id`
- `AnaliseProcessosPage.tsx`: substituído placeholder por página completa com filter bar (data + group_by), 5 KPI cards, tabela sortável com `OutcomeBar` (distribuição de outcomes) + `RateBar` (conclusão/falha), export CSV

---

## Dashboard #35 — Part 4: Analytics-API Display Endpoints (2026-05-09)

10 endpoints `GET /reports/display/*` no `analytics-api`, com formatters centralizados em `display_formatters.py`. Cada endpoint retorna o shape exato do DisplayTool correspondente — os cards new-format passam a mostrar dados reais de ClickHouse/Redis.

**Arquivos criados:**

| Arquivo | Descrição |
|---|---|
| `packages/analytics-api/src/plughub_analytics_api/display_formatters.py` | Funções de query + formatação para cada endpoint; `_fmt_dt()` aceita ISO8601 e `YYYY-MM-DD`; `_prev_period()` calcula período anterior para KPI trend; `_buckets_to_chart()` converte timeseries buckets para `BarChartData`/`LineChartData` shape |
| `packages/analytics-api/src/plughub_analytics_api/display.py` | FastAPI router `prefix="/reports/display"` com 10 rotas; parâmetros `from`/`to` (alias para datas do FilterBar); `pool_id` opcional; auth via `optional_pool_principal` |

**Arquivos modificados:**

| Arquivo | Alteração |
|---|---|
| `packages/analytics-api/src/plughub_analytics_api/main.py` | `from .display import router as display_router` + `app.include_router(display_router)` |

**Endpoints implementados:**

| Endpoint | Tool compatível | Shape retornado |
|---|---|---|
| `GET /reports/display/session-volume` | bar_chart, line_chart | `BarChartData` (`x_labels` + `series`) |
| `GET /reports/display/handle-time` | line_chart, bar_chart | `BarChartData` (`x_labels` + `series`) |
| `GET /reports/display/evaluation-score` | line_chart, bar_chart | `BarChartData` (`x_labels` + `series`) |
| `GET /reports/display/sessions-by-pool` | bar_chart | `BarChartData` (pool_id como categorias) |
| `GET /reports/display/outcome-distribution` | donut | `DonutData` (`labels` + `values`) |
| `GET /reports/display/pool-status` | table | `TableData` (Redis snapshots, live) |
| `GET /reports/display/agent-performance` | table | `TableData` (ClickHouse segments) |
| `GET /reports/display/kpi-sessions` | metric_card | `MetricCardData` com trend vs período anterior |
| `GET /reports/display/kpi-resolution` | metric_card | `MetricCardData` (ratio 0-1, format: percent) |
| `GET /reports/display/kpi-score` | metric_card | `MetricCardData` com trend vs período anterior |

**Reutilização:** timeseries endpoints delegam para `query_volume_timeseries`, `query_handle_time_timeseries`, `query_score_timeseries` existentes; agent-performance delega para `query_agent_performance_report`; pool-status reutiliza `get_pool_snapshots` do Redis.

---

## Dashboard #35 — Part 3: Runtime Filters (FilterBar + GlobalFilters) (2026-05-09)

Sistema de filtros globais do dashboard: admin configura quais filtros o template expõe; usuário controla valores em runtime via `FilterBar`; cada card `NewDashboardCard` lê os valores via `buildQueryUrl()` na hora do fetch.

**Arquivos criados:**

| Arquivo | Descrição |
|---|---|
| `src/dashboard/FilterBar.tsx` | `FilterBar` component: renderiza controles de `date`, `select`, `multi_select`; botão ↺ Limpar quando há filtros ativos; `FILTER_PRESETS` com Período e Pool |
| `src/dashboard/FilterConfigPanel.tsx` | Painel admin (edit mode, sidebar): adicionar/remover presets de filtros; editor de opções para filtros select |

**Arquivos modificados:**

| Arquivo | Alteração |
|---|---|
| `src/modules/dashboards/DashboardsPage.tsx` | `globalFilters` state (carregado do template); `runtimeFilters` state (seed dos defaults); `setRuntimeFilter` / `resetRuntimeFilters`; `updateGlobalFilters` (sets dirty); `handleSave` persiste `global_filters`; `<FilterBar>` entre TopBar e grid; `<FilterConfigPanel>` no sidebar em edit mode; `runtimeFilters` passado para `<CardRenderer>` |

**Fluxo completo:** admin adiciona preset "Período" em edit mode → salva template com `global_filters: [{date_from}, {date_to}]` → usuário vê FilterBar com pickers → ao alterar datas, todos os cards new-format re-buscam via `buildQueryUrl()` com os novos valores. Cards legados (TimeseriesChart) ignoram `runtimeFilters` (mantêm seus próprios controles internos).

---

## Dashboard #35 — Part 2: AddCardModal 3-step + NewDashboardCard (2026-05-09)

Modal de criação de cards substituído por fluxo de 3 passos. Novos cards criados diretamente como `NewDashboardCard` (tool_id + query + tool_config).

**Arquivos criados:**

| Arquivo | Descrição |
|---|---|
| `src/dashboard/catalog.ts` | `ENDPOINT_CATALOG` — 10 endpoints `/reports/display/*` com `compatible_tools`, `configurable_params`, defaults de dimensão |
| `src/dashboard/AddCardModal.tsx` | Modal 3-step: (1) escolher métrica, (2) escolher visualização, (3) configurar título + params fixos; `onAdd(NewDashboardCard)` |

**Arquivos modificados:**

| Arquivo | Alteração |
|---|---|
| `src/modules/dashboards/DashboardsPage.tsx` | `CARD_PRESETS` / `DISPLAY_OPTIONS` / antigo `AddCardModal` removidos; `addCard(card: NewDashboardCard)` direto; imports limpos |

**Comportamento do modal:** `tenant_id` sempre fixo com o valor do tenant atual; `from`/`to` sempre runtime (filter_key `date_from`/`date_to`); `pool_id` fixo se preenchido pelo usuário, runtime caso contrário. Navegação Passo 1 → 2 → 3 com Voltar; indicador de progresso com dots.

---

## Dashboard #35 — Part 1: Display Tool Registry (2026-05-09)

Implementada a infraestrutura de Display Tools no `platform-ui` (sem dependência de backend). Cards antigos continuam funcionando sem alteração.

**Arquivos criados:**

| Arquivo | Descrição |
|---|---|
| `src/dashboard/tools/types.ts` | Contratos TypeScript: `DisplayTool`, `NewDashboardCard`, `CardQuery`, `QueryParam`, `GlobalFilter`, `buildQueryUrl()` + 5 data shapes |
| `src/dashboard/tools/MetricCardTool.tsx` | KPI com número grande, label e trend arrow (↑↓) |
| `src/dashboard/tools/BarChartTool.tsx` | Barras verticais via recharts, stacked opcional |
| `src/dashboard/tools/LineChartTool.tsx` | Linhas com pontos, eixo X com labels |
| `src/dashboard/tools/DonutTool.tsx` | Donut chart com legenda lateral e tooltip de % |
| `src/dashboard/tools/TableTool.tsx` | Tabela MxN com header fixo, scroll interno, ordenação client-side |
| `src/dashboard/tools/registry.ts` | Mapa `toolId → DisplayTool` + `normalizeCard()` com migration map dos 6 tipos antigos |
| `src/dashboard/CardRenderer.tsx` | Dispatcher: detecta `tool_id` (new path) vs `type` (legacy path); faz fetch + polling para new-format cards |

**Arquivos modificados:**

| Arquivo | Alteração |
|---|---|
| `src/types/index.ts` | `DashboardTemplate.cards` → `(DashboardCard | NewDashboardCard)[]`; `global_filters?`; re-exports de `NewDashboardCard`, `CardQuery`, `QueryParam`, `GlobalFilter` |
| `src/api/dashboard-hooks.ts` | `savePersonalLayout` e `loadPersonalLayout` aceitam `AnyDashboardCard[]` |
| `src/modules/dashboards/DashboardsPage.tsx` | `CardContent` removido; substituído por `<CardRenderer>`; `cards` state: `(DashboardCard | NewDashboardCard)[]`; card header title: suporte a ambos os formatos |

**Invariants mantidos:** cards antigos continuam sendo renderizados via `LegacyCardContent` → `TimeseriesChart`. `normalizeCard()` existe mas não é chamado ainda (Part 2). Nenhum endpoint `/reports/display/*` é requerido em Part 1.

---

## CLAUDE.md Otimização Fase 2 — docs/modules/ completo (2026-05-09)

Criados 5 novos arquivos em `docs/modules/` para seções que não tinham referência completa fora do CLAUDE.md:

| Arquivo criado | Conteúdo |
|---|---|
| `docs/modules/arc5-segments.md` | ContactSegment data model, ClickHouse schema, endpoints |
| `docs/modules/ai-gateway.md` | Multi-account rotation, AccountSelector, model profiles, sentiment emission |
| `docs/modules/arc8-agent-availability.md` | Pause tracking, Kafka events, ClickHouse schema, analytics endpoint |
| `docs/modules/usage-metering.md` | Dimensions, Redis counters, assertQuota pattern, cycle reset |
| `docs/modules/pricing.md` | Billing model, reserve pools, endpoints, quota side effects |
| `docs/modules/session-replayer.md` | Pipeline, Hydrator pattern, ReplayContext, timing replay, Comparison Mode |

CLAUDE.md atualizado: `→ See [docs/modules/xxx.md]` adicionado em cada seção que agora tem um arquivo dedicado. Seção "Pending — CLAUDE.md Otimização Fase 2" atualizada para Fase 3.

TODO.md: Fase 2 marcada como concluída; item Fase 3 mantido.

---

## Language Cleanup Phase 2 — ABAC field names (2026-05-09)

Renomeados os 3 identificadores ABAC em português que violavam a Language Rule (English in code):

| Antigo | Novo | Módulo |
|---|---|---|
| `relatorio` | `report` | `evaluation` |
| `recursos` | `resources` | `config` |
| `mascaramento` | `masking` | `config` |

**Arquivos alterados:**

- `infra/modules.yaml` — campos renomeados nas seções `evaluation` e `config`.
- `packages/auth-api/src/plughub_auth_api/db.py` — 3 migrations idempotentes adicionadas a `ensure_schema()`: renomeiam as chaves no JSONB `auth.users.module_config` (condição `? 'old_key'` garante no-op se já migrado).
- `packages/platform-ui/src/shell/Sidebar.tsx` — já usava nomes ingleses (`report`, `resources`, `masking`) desde a Language Cleanup Phase 1.
- `packages/platform-ui/src/i18n/locales/en/contacts.json` e `pt-BR/contacts.json` — chave `"relatorio"` → `"report"` (chave órfã, sem usages em código).
- `packages/platform-ui/src/i18n/locales/en/home.json` e `pt-BR/home.json` — chaves `"recursos"` → `"resources"` e `"recursosDesc"` → `"resourcesDesc"`.
- `packages/platform-ui/src/modules/home/HomePage.tsx` — `t('quickLinks.recursos')` → `t('quickLinks.resources')`, `t('quickLinks.recursosDesc')` → `t('quickLinks.resourcesDesc')`, `to="/config/recursos"` → `to="/config/resources"`.

**Comportamento no restart:** `_register_platform_modules()` já faz upsert do módulo inteiro com `ON CONFLICT DO UPDATE SET schema = EXCLUDED.schema`, então `auth.module_registry` é atualizado automaticamente. As migrations de `module_config` garantem que permissões existentes de usuários não são perdidas.

TODO.md: seção "Language Cleanup — Fase 2" removida.

---

## Task #173 — writeStreamEntry centralizado no mcp-server-plughub (2026-05-09)

Eliminados 4 pontos de `redis.xadd()` direto em `server.ts` que usavam formatos inconsistentes (campos `author` como JSON object sem campos flat `author_id`/`author_role`, ausência de `segment_id`). Todos migrados para `writeStreamEntry()` que garante validação Zod em runtime e layout canônico no stream.

Pontos migrados em `server.ts`:
- `writeParticipantEvent` — `participant_joined`/`participant_left` com `visibility: "all"`
- Mention dispatcher — `message` com `visibility: "agents_only"`
- Hook agent response path — `message` com `visibility: streamVis` (dinâmico)
- Normal message path — `message` com `visibility: "all"`

Resultado: `server.ts` não contém nenhum `xadd` direto. Todo entry no stream canônico passa por `writeStreamEntry()` com `author_id`/`author_role` flat fields obrigatórios — elimina fallbacks em `_parse_entry` do analytics-api.

---

## Quota namespace removido do Config API seed (2026-05-09)

As três entradas do namespace `quota` (`max_concurrent_sessions`, `llm_tokens_daily`, `messages_daily`) foram removidas do seed.py por serem código morto:

- Nenhuma é lida em runtime por nenhum componente via Config API
- `max_concurrent_sessions`: `checkConcurrentSessions()` lê diretamente `{tenant}:quota:max_concurrent_sessions` no Redis, que é escrito pelo pricing-api (não pelo Config API)
- `llm_tokens_daily` / `messages_daily`: conceitualmente incorretos no modelo multi-conta — limites por conta API não cabem como valor único global por tenant
- Quando o pricing for integrado, escreverá `{tenant}:quota:*` diretamente no Redis por plano ativado

Nota adicionada ao cabeçalho do seed.py documentando o fluxo correto.
TODO.md: seção "Quotas — refatoração" removida.

---

## Pool Registry routing_expression — verificado como já implementado (2026-05-09)

Code review confirmou que `routing_expression` está completamente suportado:
- `PoolRegistrationSchema` (schemas) — campo `routing_expression: RoutingExpressionSchema.optional()`
- Prisma schema — `routing_expression Json?`
- `validators/pool.ts` — `CreatePoolSchema`/`UpdatePoolSchema` herdam via extend/partial
- `routes/pools.ts` — create e update ambos persistem o campo corretamente

TODO.md: seção "Pool Registry — routing_expression field" removida.

---

## Task #41 — Remover category do AI Gateway + schemas (2026-05-09)

O AI Gateway é producer puro de dados de sentimento — não deve classificar scores em categorias. As faixas (satisfied/neutral/frustrated/angry) são configuráveis por tenant via Config API; qualquer classificação feita com valores hardcoded seria incorreta após mudança de configuração. A responsabilidade de classificar pertence ao consumer (analytics-api), que tem acesso às faixas corretas.

**Alterações:**

`packages/schemas/src/platform-events.ts`:
- `SentimentUpdatedEventSchema` — removido campo `category`. Comentário documenta explicitamente que a classificação é responsabilidade do consumer.

`packages/ai-gateway/src/plughub_ai_gateway/`:
- `sentiment_config.py` — deletado (classificação dinâmica não pertence ao AI Gateway).
- `sentiment_emitter.py` — removidos `_classify()`, import `sentiment_config`, campo `category` do payload Kafka, contagens por categoria (`satisfied/neutral/frustrated/angry`) do hash `sentiment_live`. Hash agora contém apenas `avg_score`, `score_total`, `count`, `last_session_id`, `updated_at`.
- `config.py` — removido `config_api_url` (não mais necessário).
- `main.py` — removidos import `sentiment_config`, `reload()` no startup, `refresh_loop()` background task e cancel no teardown.

TODO.md: seção "Sentimento — _classify() dinâmico" já havia sido removida na sessão anterior.

---

## Scheduled Deploy gap — verificado como já implementado (2026-05-09)

Code review revelou que o TODO "Skill Flow — Scheduled Deploy gap" já estava resolvido. Items confirmados como presentes:

- `_resolve_flow_definition()` em `workflow-api/router.py` (linhas 125–153): busca o skill pelo `flow_id` no agent-registry quando `metadata` não contém `flow_definition`, e injeta automaticamente antes de salvar a instância.
- Chamado em `trigger_workflow` via `resolved_metadata = await _resolve_flow_definition(...)`.
- `agent_registry_url` configurado em `workflow-api/config.py`.
- `skill_scheduled_deploy_v1.yaml` presente em `skill-flow-engine/skills/`.
- `scheduleSkillDeploy()` removido da UI no Task #33 (seção de agendamento foi retirada do `AgentFlowDeployPage`).

TODO.md: seção "Skill Flow — Scheduled Deploy gap" removida.

---

## Session TTLs dinâmicos via Config API (2026-05-09)

Eliminados ~25 literais `14400` hardcoded de 3 componentes. Todos os TTLs de sessão Redis agora lidos do namespace `session` do Config API no startup, com fallback silencioso aos valores default (14400s / 3600s).

**Arquivos modificados:**

`packages/orchestrator-bridge/`:
- `session_config.py` (novo) — `SessionConfigCache`, mesmo padrão do `RoutingConfigCache`. Singleton `session_config`. Defaults espelham `seed.py`.
- `main.py` — importa `session_config`; adiciona `CONFIG_API_URL` env var; função `_stl()` retorna TTL dinamicamente; substitui todos os literais `14400` por `_stl()`; carrega Config API no startup; `_handle_config_changed` agora aceita `http` e invalida + recarrega ao receber `namespace=session`.

`packages/conversation-writer/`:
- `config.py` — adiciona `config_api_url: str = "http://localhost:3500"`.
- `main.py` — função `_fetch_session_ttl()` busca `transcript_ttl_s` via `urllib.request` no startup; `RedisBuffer` criado com TTL dinâmico.

`packages/session-replayer/`:
- `stream_hydrator.py` — `StreamHydrator.__init__` recebe `ttl: int = HYDRATION_TTL_SECONDS`; usa `self._ttl` internamente.
- `replayer.py` — `Replayer.__init__` recebe `context_ttl: int = REPLAY_CONTEXT_TTL`; usa `self._context_ttl` no `redis.set(ex=...)`.
- `consumer.py` — `SessionReplayerConsumer` adiciona `CONFIG_API_URL` env var; `start()` busca `replayer_hydration_ttl_s` e `replay_context_ttl_s` do Config API antes de iniciar consumers; passa TTLs ao construir `StreamHydrator` e `Replayer`.

TODO.md: item "orchestrator-bridge TTLs dinâmicos" removido.

---

## Arc 8 — Disponibilidade e Pausas de Agentes — backend completo (verificado 2026-05-09)

Verificação de code review revelou que todo o backend Arc 8 já estava implementado. TODO.md estava desatualizado. Itens confirmados como presentes:

- `AgentPauseEventSchema` em `platform-events.ts` — campos `reason_id`, `reason_label`, `note` presentes.
- Config API seed `agent_activity.pause_reasons` — já presente em `seed.py`.
- `PUT /api/agent-pause` e `PUT /api/agent-resume` em `mcp-server-plughub/src/server.ts` (linhas 944–1050) — publicam `agent_pause`/`agent_ready` em `agent.lifecycle` com `reason_id`/`reason_label`.
- `parse_agent_lifecycle` em `analytics-api/models.py` — processa `agent_pause` (action=open) e `agent_ready` (action=close_check).
- Tabela `agent_pause_intervals` (ClickHouse `ReplacingMergeTree`) em `clickhouse.py`.
- `upsert_agent_pause_interval` e Redis state machine no consumer (`consumer.py`).
- `GET /reports/agent-availability` em `reports.py` + `query_agent_availability` em `reports_query.py`.
- Testes unitários em `tests/test_reports.py`.

TODO.md: seção Arc 8 removida (todos os itens concluídos).

---

## Config API Seed — novos namespaces e chaves de sessão (2026-05-09)

**Arquivo:** `packages/config-api/src/plughub_config_api/seed.py`

**O que mudou:**

Namespace `session` — 6 chaves TTL adicionadas para centralizar todos os TTLs de Redis em um único namespace, eliminando literais hardcoded nos componentes:
- `orchestrator_session_ttl_s` (14400) — bridge session state
- `transcript_ttl_s` (14400) — conversation-writer
- `replayer_hydration_ttl_s` (3600) — Hydrator (session_replayer)
- `replay_context_ttl_s` (3600) — ReplayContext hash
- `pool_config_ttl_s` (3600) — PoolConfigCache no orchestrator-bridge
- `sentiment_live_ttl_s` (300) — sentiment_live hash (duplicado de `sentiment.live_ttl_s` para o bridge ler tudo de um namespace)

Namespace `audit_policy` — substitui `masking` como namespace primário para políticas de mascaramento/auditoria (LGPD). Chaves: `authorized_roles`, `default_retention_days`, `capture_input_default`, `capture_output_default`. Entradas `masking.*` mantidas como aliases deprecados com prefixo `[DEPRECATED]` na descrição.

Namespace `analytics_consumer` — substitui `consumer` como namespace primário para configuração do consumidor Kafka da analytics-api. Entradas `consumer.*` mantidas como aliases deprecados.

Docstring do arquivo atualizado para listar namespaces ativos vs aliases deprecados.

---

## Bugfix — Routing Engine: sessões órfãs na fila ao desconectar cliente (2026-05-08)

**Problema:** Quando o cliente WebSocket desconectava (refresh, fechar aba, queda de rede), a sessão permanecia no ZSET `{tenant}:pool:{pool_id}:queue` indefinidamente — exibida na tela operacional de pools como sessão ativa, gerando falso-positivo de SLA excedido.

**Root cause:** O Channel Gateway já publicava `ContactClosedEvent` em `conversations.events` corretamente. O Routing Engine não consumia esse tópico e nunca gravava a chave `session:{id}:closed` no Redis — que é o marcador que o `_drain_queue_for_agent` usa para pular sessões encerradas.

**Solução (apenas routing-engine):**

- ✅ `config.py` — adicionado `kafka_topic_events: str = "conversations.events"`
- ✅ `kafka_listener.py` — novo `SessionClosedEventHandler`:
  - Recebe `event_type="contact_closed"`, grava `session:{id}:closed = reason` (TTL 7d)
  - Lê `{tenant}:queue_contact:{session_id}` para descobrir `pool_id`, remove do ZSET e deleta o JSON
- ✅ `kafka_listener.py` — `run_listeners()` e `_dispatch()` atualizados para aceitar e rotear `kafka_topic_events`
- ✅ `main.py` — `run_listeners` chamado com `kafka_topic_events = settings.kafka_topic_events`
- ✅ `main.py` — `_periodic_queue_drain` agora verifica `session:{id}:closed` antes de re-rotear (defesa em profundidade)

**Componentes alterados:** `routing-engine` apenas. Channel Gateway, Core e orchestrator-bridge sem alteração.

---

## Task #38 — Calendar Arc: modelo dois níveis + feriados recorrentes + detecção de conflitos (2026-05-08)

**Problema:** O calendário era um template compartilhado por referência, o que significava que exceções de um pool (manutenção, etc.) vazavam para outros pools que usassem o mesmo calendário. Feriados não podiam ser marcados como recorrentes anuais. Datas sobrepostas entre conjuntos de feriados eram silenciosamente resolvidas pelo último registro.

**Solução:** Modelo dois níveis onde (1) o calendário define a política compartilhada e (2) a associação `pool ↔ calendário` carrega exceções específicas do pool com prioridade 1 no motor.

**calendar-api — db.py:**
- ✅ `_DDL_ASSOCIATIONS` — coluna `exceptions JSONB NOT NULL DEFAULT '[]'` na tabela `calendar_associations`
- ✅ `_DDL_ASSOC_ADD_EXCEPTIONS` — migration idempotente `ADD COLUMN IF NOT EXISTS`
- ✅ `ensure_schema` — executa a migration idempotente
- ✅ `_row_to_assoc` — inclui `exceptions` no retorno
- ✅ `db_create_association` — persiste `exceptions`
- ✅ `db_update_association` — PATCH de `exceptions` de uma associação existente
- ✅ `db_upsert_pool_association` — upsert `ON CONFLICT DO UPDATE SET exceptions = EXCLUDED.exceptions`
- ✅ `db_delete_associations_for_entity` — remove todas as associações de uma entidade
- ✅ `db_get_associations_for_engine` — merge de exceções: `assoc.exceptions` (prioridade) + `cal.exceptions` (deduplica por date)

**calendar-api — router.py:**
- ✅ `AssociationCreate` — aceita `exceptions: list[dict] = []`
- ✅ `AssociationUpdate` — Pydantic model para PATCH
- ✅ `AssociationUpsert` — Pydantic model para PUT /upsert
- ✅ `PATCH /v1/associations/{id}` — atualiza exceções
- ✅ `PUT /v1/associations/upsert` — idempotent upsert (limpa associações antigas, cria nova com exceções)
- ✅ `DELETE /v1/associations/entity` — remove todas associações de uma entidade

**agent-registry:**
- ✅ `prisma/schema.prisma` — `calendar_id String?` no model Pool
- ✅ `prisma/migrations/20260508220000_add_pool_calendar_id/migration.sql`
- ✅ `src/routes/pools.ts` — persiste `calendar_id` no create + update
- ✅ `packages/schemas/src/agent-registry.ts` — `calendar_id: z.string().uuid().optional()` em `PoolRegistrationSchema`

**platform-ui — CalendarsPage.tsx:**
- ✅ `HolidaysEditor` — checkbox "Repete todo ano": salva `MM-DD` em vez de `YYYY-MM-DD`; badge ↺ na lista de feriados recorrentes
- ✅ `conflictDates` — `useMemo` detecta datas sobrepostas entre conjuntos selecionados
- ✅ Warning ⚠️ inline no seletor de conjuntos quando há datas duplicadas

**platform-ui — PoolsPage.tsx:**
- ✅ `PoolExceptionsEditor` — componente inline para gerenciar exceções do pool (fecha o dia todo ou horário especial)
- ✅ `calExceptions` state — carregado assincronamente da associação existente ao abrir o drawer
- ✅ `loadPoolAssociation` — busca associações do pool em `GET /v1/associations`
- ✅ `handleSubmit` — após salvar o pool no agent-registry, faz PUT `/v1/associations/upsert` (com calendar_id + exceptions) ou DELETE `/v1/associations/entity` (se sem calendário)
- ✅ Seção "Exceções deste Pool" aparece no drawer somente quando `calendar_id` está definido

**i18n:**
- ✅ `en/calendars.json` — `holidaySet.recurringLabel`, `calendar.holidayConflict`, `calendar.holidayConflictHint`
- ✅ `pt-BR/calendars.json` — traduções correspondentes

---

## Task #37 — Config/Channels: hierarquia Conta → Endpoints (2026-05-08)

**Problema:** GatewayConfig (credenciais) e ChannelEndpoint (endereços → pools) eram entidades paralelas sem relação explícita, listadas em sub-tabs separadas.

**Solução:** FK nullable `gateway_config_id` no ChannelEndpoint + UI hierárquica onde cada conta (GatewayConfig) é um card pai com seus endpoints filhos abaixo.

**Backend — agent-registry:**
- ✅ `prisma/schema.prisma` — `gateway_config_id String?` em ChannelEndpoint + relação bidirecional com GatewayConfig
- ✅ `prisma/migrations/20260508200000_channel_endpoint_gateway_config_fk/migration.sql` — ADD COLUMN + índice
- ✅ `src/types/channel-endpoint.ts` — `gateway_config_id: string | null` no type shim + `updateMany` adicionado
- ✅ `src/routes/channel-endpoints.ts` — aceita `gateway_config_id` em POST + PUT; filtro em GET; webhook adicionado a VALID_CHANNELS

**Frontend — platform-ui:**
- ✅ `src/types/index.ts` — `gateway_config_id: string | null` em ChannelEndpoint; `gateway_config_id?` em Create/UpdateChannelEndpointInput
- ✅ `src/api/registry.ts` — `listChannelEndpoints` aceita filtro opcional `gatewayConfigId`
- ✅ `modules/config-channels/ChannelAccountCard.tsx` — novo componente card pai: mostra credenciais + tabela de endpoints filhos; formulários inline para criar/editar/deletar endpoints dentro do card
- ✅ `modules/config-channels/index.tsx` — reestruturado: ChannelPanel usa ChannelAccountCard como hierarquia principal; webhook mantém ChannelEndpointList standalone; webchat mantém sub-tab "Runtime Settings"

---

## Task #36 — Config/Channels: consolidação GatewayConfig (2026-05-08)

**Problema resolvido:** GatewayConfig (credenciais de API) estava em Resources/Channels enquanto ChannelEndpoints (roteamento) estava em Config/Channels — duas telas separadas para configurar o mesmo canal.

**Solução:** Config/Channels virou o ponto único de configuração de canal. Resources ficou apenas com Pools + Skills.

**Frontend — platform-ui:**
- ✅ `modules/config-channels/channel-meta.ts` — `CHANNEL_META` extraído para arquivo compartilhado (8 canais: whatsapp, webchat, voice, email, sms, instagram, telegram, webrtc, webhook)
- ✅ `modules/config-channels/GatewayConfigPanel.tsx` — CRUD de GatewayConfig escopado por canal; sub-tabs expandíveis inline; suporte a create/edit/delete com masking de credenciais
- ✅ `modules/config-channels/index.tsx` — adicionada sub-tab "Credentials" para todos os canais que possuem campos de API; sub-tabs: Endpoints | Credentials | Settings (Settings apenas webchat/webhook)
- ✅ `modules/config-recursos/index.tsx` — aba "Channels" removida; Resources agora tem apenas Pools + Skills
- ✅ `modules/config-recursos/ChannelsPage.tsx` — marcado `@deprecated`; arquivo mantido para referência

**Estrutura final de Config/Channels por canal:**
- Endpoints: mapeamento identifier → pool (ChannelEndpointList, existente)
- Credentials: API keys/tokens/secrets (GatewayConfigPanel, novo)
- Settings: configuração de comportamento runtime via Config API (webchat: auth_timeout, attachments; webhook: path)

---

## Task #34 — Service/Pools: monitoração operacional de pools (2026-05-08)

**Backend — agent-registry:**
- ✅ `src/infra/redis.ts` — Redis client (ioredis) com opKeys: poolSnapshot, poolQueue, poolInstances
- ✅ `src/routes/operational.ts` — `GET /v1/operational/pools` + `GET /v1/operational/pools/:id/queue`
  - Fallback automático: snapshot Redis (Routing Engine) → live counts (:instances SCARD + :queue ZCARD)
  - Pool config (DB) + estado operacional (Redis) combinados por pool_id
  - Queue drill-down: posição, session_id, aguardando, SLA excedido, espera estimada restante
- ✅ `src/app.ts` — operationalRouter montado em `/v1/operational`
- ✅ `package.json` — ioredis adicionado
- ✅ `config.ts` — redis_url adicionado
- ✅ `docker-compose.demo.yml` — REDIS_URL + depends_on redis adicionados ao agent-registry

**Frontend — platform-ui:**
- ✅ `modules/contacts/PoolsPage.tsx` — nova página `/contacts/pools`:
  - Summary pills: agentes disponíveis, contatos em fila, pools com fila, total
  - Tabela de pools: status, disponíveis, fila, espera est., SLA, canais, snapshot age
  - Drill-down inline por pool: lista de sessões em fila com posição + wait + SLA excedido
  - Auto-refresh 15s, filtros por pool (select dinâmico) e status
  - `PoolStatusCard` exportado para reutilização em dashboard (Task #35)
- ✅ `modules/contacts/AgentsPage.tsx` — redesign: grid de cards → tabela compacta; tab "Lista" removida
- ✅ `shell/Sidebar.tsx` — item "Pools" adicionado em Service
- ✅ `app/routes.tsx` — rota `/contacts/pools` adicionada
- ✅ i18n pt-BR + en — chave `nav.service.pools` adicionada

---

## Task #33 — Deploy Pool-Centric: redesign completo (2026-05-08)

**Conceito corrigido:** deploy é uma operação de *pool*, não de skill. O usuário escolhe um pool e atribui um skill-flow a cada slot.

**Backend — agent-registry:**
- ✅ `prisma/migrations/20260508120000_add_pool_skill_slots/migration.sql` — tabela `pool_skill_slots` (pool_id + tenant_id + slot como chave; FK para pools com CASCADE; índice pool+tenant)
- ✅ `prisma/schema.prisma` — modelo `PoolSkillSlot` + relação `skill_slots` em `Pool`
- ✅ `src/routes/pool-slots.ts` — 4 endpoints montados em `/v1/pools/:pool_id`:
  - `GET /slots` — retorna todos os 3 slots do pool
  - `PUT /slots/next` — único slot editável; 403 para previous/current; auto-fetches yaml_snapshot
  - `POST /promote` — next→current, current→previous, next cleared (transaction)
  - `POST /rollback` — previous→current, previous cleared (transaction)
- ✅ `src/app.ts` — `poolSlotsRouter` montado em `/v1/pools/:pool_id` com `mergeParams: true`

**Frontend — platform-ui:**
- ✅ `AgentFlowDeployPage.tsx` — reescrita pool-centric:
  - Lista de pools no painel esquerdo com filtro
  - 3 cards por pool: Anterior (cinza) / Corrente (verde) / Próxima (azul)
  - Somente "Próxima" tem botão Editar; Corrente/Anterior mostram "🔒 somente leitura"
  - `NextSlotEditor`: skill dropdown + `ConfigForm` derivado de `skill.interface_schema`
  - "Copiar do Corrente": intersection merge — campos que existem em ambos são copiados; novos campos (só no novo schema) recebem badge "novo"; campos removidos são ignorados
  - Rollback usa `config_json` salvo sem revalidação (operação de emergência)
  - `ConfirmModal` para Promover e Rollback
  - Headers `x-tenant-id` em todos os fetches

---

## Full-Stack — 3-Slot Deploy Lifecycle (2026-05-08)

### Task #31 — Flow/Deploy: modelo de 3 slots (anterior / corrente / próxima)

**Backend — agent-registry:**
- ✅ `prisma/migrations/20260508000000_add_skill_version_slots/migration.sql` — tabela `skill_version_slots` + enum `SkillSlot` (previous/current/next)
- ✅ `prisma/schema.prisma` — modelo `SkillVersionSlot` + enum `SkillSlot` + relação `version_slots` em `Skill`
- ✅ `src/routes/skill-slots.ts` — 4 endpoints: `GET /slots`, `PUT /slots/:slot`, `POST /promote`, `POST /rollback`
- ✅ `src/app.ts` — `skillSlotsRouter` montado em `/v1/skills/:skill_id` com `mergeParams: true`

**Backend — workflow-api:**
- ✅ `config.py` — adicionado `agent_registry_url` (default `http://localhost:3300`)
- ✅ `router.py` — `_resolve_flow_definition()`: injeta `flow_definition` de agent-registry no metadata quando omitido em `POST /v1/workflow/trigger` para skill_*_v{n}
- ✅ `docker-compose.demo.yml`, `docker-compose.full.yml`, `docker-compose.arc4.yml` — `PLUGHUB_WORKFLOW_AGENT_REGISTRY_URL` adicionado

**Frontend — platform-ui:**
- ✅ `AgentFlowDeployPage.tsx` — reescrita completa; substituiu histórico-as-rollback por modelo de slots:
  - Painel de 3 colunas: Anterior / Corrente / Próxima
  - Desenvolvedor: botão "Editar" por slot (yaml_snapshot + config_json + pool_ids)
  - Operador: "Promover" (Próxima→Corrente, Corrente→Anterior) e "Rollback" (Anterior→Corrente) com confirmação
  - Após promote/rollback: chama `POST /deploy` para registrar em `skill_deployments`
  - Histórico de deploys colapsável
  - Deploys agendados colapsável (agendamento via workflow-api + cancelamento)
  - Monitor de handoff gradual colapsável (polling 15s)

---

## platform-ui — Contacts & Nav Restructure (2026-05-08)

### Task #30 — Grupos Atendimento + Análise + Flow expandido

**Novas páginas criadas:**
- ✅ `SessionsPage.tsx` (`/contacts/sessions`) — lista unificada de sessões (inbound + outbound), com filtros revisados: tipo (inbound/outbound), status (active/closed/abandoned), session_id, canal, pool, agent
- ✅ `AgentsPage.tsx` (`/contacts/agents`) — sub-abas Monitor (instâncias ao vivo, polling 15s) e Lista (placeholder Arc 8)
- ✅ `EventsPage.tsx` (`/contacts/events`) — stream plano de eventos com filtros: período, session_id (busca exata), canal, pool, tipo de evento
- ✅ `FlowMonitorPage.tsx` (`/flow/monitor`) — pool cards em tempo real (extrai scope "sessões" do MonitorTab)
- ✅ `ProcessosPage.tsx` (`/flow/processos`) — workflow instances com filtro de status, painel de detalhe, link para sessão de origem em `/contacts/sessions`
- ✅ `AnaliseContatosPage.tsx` (`/analise/contatos`) — seletor de período (Dia/Semana/Mês/Ano) + custom range; wraps AnaliseTab
- ✅ `AnaliseAgentesPage.tsx` (`/analise/agentes`) — filtros próprios; wraps AgentsTab (heatmap de disponibilidade + pausas)
- ✅ `AnaliseProcessosPage.tsx` (`/analise/processos`) — placeholder (backend analytics pendente)
- ✅ `AnaliseQualidadePage.tsx` (`/analise/qualidade`) — placeholder (backend evaluation summary pendente)

**Sidebar.tsx atualizado:**
- ✅ Grupo `Atendimento` (📞): Sessions · Agents · Events · Agent Assist
- ✅ Grupo `Fluxo` (🔄): Editor · Deploy · Monitor · Processos (2 novos sub-itens)
- ✅ Grupo `Análise` (📊) novo: Contatos · Agentes · Processos · Qualidade
- ✅ ABAC bypass para admin/supervisor/developer aplicado também nos itens de grupo

**routes.tsx atualizado:**
- ✅ Novas rotas: `/contacts/sessions`, `/contacts/agents`, `/contacts/events`, `/flow/monitor`, `/flow/processos`, `/analise/*`
- ✅ Redirects legados: `/contacts` → `/contacts/sessions`, `/monitor` → `/flow/monitor`, `agent-flow/monitor` → `/flow/monitor`, `config/agent-reports` → `/analise/agentes`, `reports` → `/analise/contatos`
- ✅ ContactsPage (`/contacts`) substituída — redirecionada para `/contacts/sessions`

**i18n:**
- ✅ `shell.json` (pt-BR + en): chaves `nav.service.*`, `nav.flow.monitor`, `nav.flow.processos`, `nav.analise.*` adicionadas

**Documentação:**
- ✅ `docs/modules/task-30-contacts-restructure.md` — especificação completa do redesign com decisões de design, filtros, colunas, ABAC e componentes

> Histórico de itens implementados removidos do `## Pending` do CLAUDE.md para reduzir noise no contexto.
> Itens pendentes: ver `TODO.md`.

---

## platform-ui — UI fixes + Skills/Pools/Config refactor (2026-05-07)

### Contacts (#23)
- ✅ Tab bar separada do título (border-b não mais aparecia sob "Contatos")
- ✅ Normalização de URL stale (`?tab=lista` → `?tab=report` via useEffect)
- ✅ Tab "Report" renomeada para "List" (`tabs.list`); i18n já tinha a chave
- ✅ Admin/supervisor/developer bypassam ABAC — veem todas as abas (List · Monitor · Analysis · Agents)

### Calendars (#24)
- ✅ Botão "New" adicionado nas seções Calendar e Holiday Sets
- ✅ Aba "Associations" removida (TabBar + AssociationsTab + código relacionado removidos)
- ✅ Tab state tipado com union `CalTab = 'calendars' | 'holiday-sets'`

### Flow/Editor (#25)
- ✅ Botão "New" removido (skills vêm do registry YAML, não são criadas pela UI)
- ✅ `NewSkillForm`, `AgentTypeConfig`, `FRAMEWORKS`, `ROLES`, `handleNewConfirm` removidos
- ✅ `isNew`, `pendingAgentType` removidos do estado

### Config/Platform — Masking + Namespaces (#26, #27)
- ✅ `MaskingPage.tsx`: namespace `masking` → `audit_policy`; chaves `capture_input`, `capture_output`, `token_retention_days`
- ✅ `NamespaceEditor`: namespaces `masking` e `audit_policy` removidos (têm UI própria)
- ✅ Namespaces `routing` + `session` unificados em grupo "Roteamento & Timeouts" com section headers
- ✅ Namespace `expurgo` adicionado (`voice_recording_days`, `attachment_days`)
- ✅ `NamespacePanel` sub-componente extraído; suporte a grupos (`namespaceIds[]`)
- ✅ `useMultiNamespace` hook adicionado em `config-hooks.ts`

### Resources/Skills (#28)
- ✅ `SkillsPage.tsx` reescrito: CRUD de competency skills no namespace `competency_skills` da Config API
- ✅ Cada entry: `key` (snake_case) + `value.domain` (0-9)
- ✅ Visual domain bar (10 pips) + slider inline para add/edit
- ✅ Admin token field para operações de escrita

### Resources/Pools (#29)
- ✅ `PoolsPage.tsx` reescrito: form de Modal para Drawer (slide-in direita, Escape para fechar)
- ✅ `routing_weights` substitui `routing_expression` na UI: Fixos (per-skill 0-9) + Dinâmicos (5 fatores 0-9)
- ✅ Competency skills carregadas de `/config/competency_skills` para o seletor de Fixos
- ✅ `routing_skills[]` derivado automaticamente dos Fixos com peso > 0 no save
- ✅ `types/index.ts`: `RoutingWeights`, `RoutingWeightsDinamicos`, `ROUTING_WEIGHTS_DEFAULTS` adicionados

### Bugfix
- ✅ `SkillFlowsPage.tsx` linha 408: referência residual a `isNew` removida

---

## platform-ui — Language cleanup + ChannelEndpoint (2026-05-07)

### Language rule — English identifiers in code

- ✅ **CLAUDE.md**: added "Language Rule — English in code, Portuguese only in display" to Naming Conventions section with examples.
- ✅ **routes.tsx**: `/config/canais` → `/config/channels`, `/config/recursos` → `/config/resources`, `/evaluation/avaliacoes` → `/evaluation/evaluations`. Legacy routes kept as `<Navigate>` redirects. Tab query params: `?tab=relatorio`→`?tab=report`, `?tab=analise`→`?tab=analysis`.
- ✅ **Sidebar.tsx**: all navKeys renamed to English (`service`, `flow`, `quality`, `config`); all hrefs updated to English paths; all i18n key calls updated (`t('nav.service')`, `t('nav.channels')`, `t('nav.resources')`, etc.); ABAC field references updated: `mascaramento`→`masking`, `relatorio`→`report`, `recursos`→`resources`.
- ✅ **shell.json (pt-BR + en)**: all i18n key names renamed to English while preserving translated values.
- ✅ **i18n/index.ts**: namespace `atendimento` → `service`; import variables renamed accordingly.
- ✅ **ContactsPage.tsx**: `ContactTab` type `'relatorio'|'analise'` → `'report'|'analysis'`; all tab references updated.
- ✅ **agent-assist types.ts**: `ultima_analise` → `last_analysis`.
- ✅ **useCopilotState.ts + CapacidadesTab.tsx**: updated to use `last_analysis`.
- ✅ **atendimento/MonitorPage.tsx**: `useTranslation('atendimento')` → `useTranslation('service')`.
- ✅ **config-channels/**: new module at English path; `index.tsx` + `WebChatConfigPage.tsx` created (logic same as config-canais version).

### Channel Endpoints — ChannelEndpoint entity

- ✅ **`@plughub/schemas`**: `ChannelEndpointChannelSchema`, `ChannelEndpointSchema`, `CreateChannelEndpointSchema`, `UpdateChannelEndpointSchema`, `ChannelEndpointQuerySchema` added to `packages/schemas/src/channel-endpoint.ts` and exported from `index.ts`.
- ✅ **`agent-registry` Prisma schema**: `ChannelEndpoint` model added with `@@unique([tenant_id, channel, identifier])`.
- ✅ **`agent-registry` routes**: `src/routes/channel-endpoints.ts` — full CRUD (GET list with `?channel=` filter, GET/:id, POST, PUT/:id with channel+identifier immutable, DELETE/:id). 409 on duplicate identifier. `publishRegistryChanged` on every write. Registered at `/v1/channel-endpoints` in `app.ts`.
- ✅ **`agent-registry` type shim**: `src/types/channel-endpoint.ts` — `ChannelEndpointRow` + `ChannelEndpointDelegate` for pre-generate Prisma workaround.
- ✅ **`platform-ui` types**: `ChannelEndpoint`, `ChannelEndpointChannel`, `CreateChannelEndpointInput`, `UpdateChannelEndpointInput` added to `types/index.ts`.
- ✅ **`platform-ui` api/registry.ts**: `listChannelEndpoints`, `createChannelEndpoint`, `updateChannelEndpoint`, `deleteChannelEndpoint` added.
- ✅ **`platform-ui` ChannelEndpointList.tsx**: new component — inline create/edit form, pool dropdown, delete with confirm; `IDENTIFIER_HINT` and `IDENTIFIER_PLACEHOLDER` maps per channel type.
- ✅ **`platform-ui` config-channels/index.tsx**: restructured with two sub-tabs per channel — "Endpoints" (`ChannelEndpointList`) + "General Settings" (`WebChatConfigPage` for webchat, coming soon for others).

**Manual steps required (WSL terminal):**
- `cd packages/agent-registry && npx prisma migrate dev --name add_channel_endpoint`
- `git mv packages/platform-ui/src/modules/config-canais packages/platform-ui/src/modules/config-channels` (then delete if config-channels already works)
- `git mv packages/platform-ui/src/modules/atendimento packages/platform-ui/src/modules/service` + update all imports referencing `@/modules/atendimento/`

---

## platform-ui — Config/Recursos + Config/Plataforma + Config/Canais (2026-05-06)

### Configuração / Recursos

- ✅ **Pool form — routing_expression weights**: 5 pesos dinâmicos de prioridade (`weight_sla=1.0`, `weight_wait=0.8`, `weight_tier=0.6`, `weight_churn=0.9`, `weight_business=0.4`) adicionados ao form de pool. Grid 2×3, inputs numéricos com step=0.1, botão "Restaurar padrões". Payload só envia `routing_expression` quando difere dos defaults. Coluna "Prioridade" na tabela mostra SLA+Churn com tooltip completo.
- ✅ **Pool form — calendar_id + routing_skills**: dropdown de template de calendário (calendar-api) e checkboxes de competency skills (Config API namespace `routing`) integrados ao form.
- ✅ **Types**: `RoutingExpression`, `ROUTING_EXPRESSION_DEFAULTS` adicionados a `types/index.ts`; `Pool`, `CreatePoolInput`, `UpdatePoolInput` atualizados.
- ✅ **Remover tab AgentTypes**: tab AgentTypes removida de `config-recursos/index.tsx` (Pools · Skills · Channels apenas). AgentType é criado pelo editor de SkillFlow ao criar novo skill.
- ✅ **SkillFlowsPage — inline AgentType**: `NewSkillForm` substitui `NewSkillPrompt`; ao criar skill, formulário completo de AgentType (framework, execution_model, role, max_concurrent, pools) é preenchido inline; `agent_type_id = skill_id`; POST /v1/agent-types disparado após skill salvo.

### Configuração / Plataforma

- ✅ **NamespaceEditor reestruturado**: 8 namespaces → 5 ativos. Removidos: `sentiment` (UI dedicada), `dashboard` (migrado ao módulo Dashboards), `webchat` (migrado a Configuração/Canais). Renomeados labels: `consumer` → "Consumer Analytics", `masking` mantido como legado com label "Mascaramento (legado)" + novo `audit_policy`. Entrada `routing` com descrição atualizada (sem score_weights — agora por pool).
- ✅ **SentimentBandsEditor**: novo componente dedicado para edição de faixas de sentimento com níveis numéricos (1=pior, N=melhor). Validação de contiguidade e cobertura [-1.0, 1.0]. Barra visual de cor vermelho→verde. Suporte a 2–6 faixas configuráveis. Salva `sentiment.bands` no Config API sem texto (i18n resolve labels). Nova tab "💬 Sentimento" no ConfigPlataformaPage.
- ✅ **RoutingSkillsManager**: CRUD de competency skills (key + domain range) no namespace `routing`. Tab "🎯 Roteamento" no ConfigPlataformaPage.
- ✅ **ConfigPlataformaPage tabs**: ⚙️ Configuração | 💬 Sentimento | 📅 Calendários | 🎯 Roteamento.

### Configuração / Canais (novo módulo)

- ✅ **config-canais/index.tsx**: seção `/config/canais` com tabs por canal. WebChat ativo; WhatsApp/Voice/Email/SMS marcados "em breve".
- ✅ **WebChatConfigPage.tsx**: configuração do canal WebChat em 3 grupos — autenticação WS (`auth_timeout_s`), política de attachments (`attachment_expiry_days`, `upload_limits_mb` por MIME type), info sobre JWT secret. Lê/escreve namespace `webchat` no Config API. Admin token inline.
- ✅ **Rota + Sidebar**: `/config/canais` registrado em `routes.tsx`; item "📡 Canais" na sidebar entre Plataforma e Calendários. Chave i18n `nav.canais` em pt-BR e en.

---

## platform-ui — Sidebar / Navigation refactor (2026-05-06)

- ✅ **Fix eco no histórico de segmento (Atendimento/Contatos)**: `mcp-server-plughub/src/server.ts` — substituído `redis.xadd()` direto por `writeStreamEntry()` no handler `menu_submit` (respeita invariante arquitetural). O `xadd` direto não populava os campos flat `author_id`/`author_role` que `_parse_entry()` do analytics-api exige, causando eco/duplicação na exibição das conversas do segmento no histórico do contato. Adicionado `import { writeStreamEntry }` do `lib/write-stream-entry`. Verificado e funcionando na docker-demo.
- ✅ **Scripts linux**: permissões de execução corrigidas em `check-infra.sh`, `seed-demo.sh`, `set-env.sh`, `setup.sh`.

---

## Context-Aware / ContextStore

- ✅ **Fase 2 — Co-pilot**: `copilot_emitter.py` (AI Gateway) — `analyze_for_copilot()` fire-and-forget; endpoint `POST /v1/copilot/analyze`; `GET /copilot_state/:sessionId` no mcp-server-plughub; `useCopilotState` hook; `CapacidadesTab.tsx` com `CopilotSection`; 28/28 testes.
- ✅ **Fase 3 — Step `resolve` nativo**: Executor completo de 5 fases (gap check → CRM → LLM question → BLPOP → LLM extract); schemas em `@plughub/schemas`; 15 unit tests.

---

## Arc 5 — ContactSegment (v2)

- ✅ **Enrichment post-hoc de `segment_id`**: `SegmentEnricher` em `analytics-api/segment_enricher.py` — lookup chain LRU → Redis → ClickHouse FINAL; `_ENRICHED_TOPICS = {"sentiment.updated", "mcp.audit"}`; 27/27 testes em `test_segment_enricher.py`. Total analytics-api: 226/226.
- ✅ **Materialized views ClickHouse**: `mv_agent_performance_daily` (AggregatingMergeTree, POPULATE) + `v_agent_performance` — `GET /reports/agent-performance/daily`; `mv_segment_summary` + `v_segment_summary` — `GET /reports/sessions/complexity`. Pool scoping em ambos.

---

## Skill Deploy Lifecycle (Phase 2)

- ✅ **Deploy agendado**: `skill_scheduled_deploy_v1.yaml` com `reason: timer` suspend; MCP tool `skill_deploy` em `mcp-server-plughub`; `scheduled_at` em `PersistSuspendRequest`; `GET /v1/skills/:id/deployments/scheduled`; UI `AgentFlowDeployPage.tsx` com agendamento e cancelamento.
- ✅ **Graceful handoff monitor**: `GET /v1/skills/:id/handoff-status` — detecta deploy recente, consulta analytics-api por sessões ativas antes de `deployed_at`. UI com polling a cada 10s.
- ✅ **Automated rollback button**: botão "↩ Rollback" no histórico; two-step (PUT yaml_snapshot + POST re-deploy); `RollbackConfirmModal`; badge "rollback" em entradas originadas por rollback.

---

## Config API / Routing Engine

- ✅ **Consumo de `config.changed` namespace `routing`**: `RoutingConfigCache` em `routing_config.py` — fetch no startup, invalida/recarrega via `ConfigChangedHandler`. `performance_score_weight` dinâmico com fallback para env var. 15/15 testes em `test_routing_config.py`.

---

## Arc 8 — Frontend

- ✅ **platform-ui — AgentReportsPage**: `/config/agent-reports` (supervisor, admin) — duas sub-abas: "Disponibilidade" (pivot agent × data, células âmbar por intensidade) e "Pausas" (tabela flat de reason_breakdown + exportação CSV). `useAvailability` hook.
- ✅ **platform-ui — PauseReasonModal**: modal intercepta "Pausar"; busca motivos do Config API com fallback para DEFAULT_REASONS; `requires_note: true` exibe textarea obrigatória; chama best-effort `PUT /api/agent-pause`.

---

## Arc 2 — Fechamento

- ✅ E2E scenario 12: webchat auth flow + media upload end-to-end
- ✅ Usage Metering no Channel Gateway (voice_minutes, whatsapp_conversations, sms_segments)
- ✅ WebChat reconexão fase 2: stream TTL expirado + jwt_secret por tenant
- ✅ AttachmentStore fase 2: S3/MinIO
- ✅ Magic bytes validation no upload
- ✅ Pricing Module v1: planos, tarifas, ciclo de billing

---

## Arc 3 — Analytics, Dashboard Operacional e Relatórios

Todos os 12 itens implementados:

1. ✅ AI Gateway — `sentiment_emitter.py`: `emit_sentiment_updated` (Kafka) + `update_sentiment_live` (Redis). 41 assertions.
2. ✅ analytics-api — consumer + ClickHouse (6 tabelas ReplacingMergeTree, 8 tópicos, commit manual). 30 assertions.
3. ✅ analytics-api — dashboard endpoints: `GET /dashboard/operational` (SSE), `/metrics`, `/sentiment`. 18 assertions.
4. ✅ analytics-api — reports + BI export: sessions/agents/quality/usage com paginação e CSV. 26 assertions.
5. ✅ analytics-api — camada admin consolidada: `/admin/consolidated`, RBAC JWT, cross-tenant. 21 assertions.
6. ✅ operator-console fase 1 — heatmap + métricas realtime: SSE, PoolTile, MetricsPanel. 157 kB JS gzip 50 kB.
7. ✅ operator-console fase 2 — drill-down: sessions ativas → transcrição ao vivo (SSE + ClickHouse fallback). 61 assertions.
8. ✅ operator-console fase 3 — intervenção ativa: supervisor join/message/leave via REST. 173 kB JS gzip 54 kB.
9. ✅ Metabase setup: `docker-compose.infra.yml`, driver ClickHouse, Row Policies por tenant, 5 questions + dashboard.
10. ✅ Config Management Module: `packages/config-api/`, PostgreSQL, Redis cache 60s, 9 namespaces seedados. 27 assertions.
11. ✅ Timeseries endpoints: `/reports/timeseries/volume`, `/handle_time`, `/score`. Bucketing dinâmico. Pool scoping.
12. ✅ Dashboard drag-and-drop + templates: `DashboardsPage.tsx`, `TimeseriesChart`, react-grid-layout, Config API namespace `dashboards`.

---

## Arc 5 — ContactSegment v1

- ✅ Schemas em `@plughub/schemas/src/contact-segment.ts`: `ContactSegmentSchema`, `ConversationParticipantEventSchema`
- ✅ orchestrator-bridge: `_publish_participant_event` com `segment_id`, `sequence_index`, `parent_segment_id`, `outcome`
- ✅ analytics-api: tabelas `segments` + `session_timeline` (ClickHouse); endpoints `/reports/segments`, `/reports/agents/performance`, `/reports/agent-performance/daily`, `/reports/sessions/complexity`
- ✅ E2E scenario 23: Parts A/B/C — 11 assertions (`--segments` flag)

---

## AI Gateway — Multi-account Rotation

- ✅ `AccountSelector` em `account_selector.py`: Redis-backed, stateless, scoring por (rpm_used/rpm_limit × 0.7) + (tpm_used/tpm_limit × 0.3)
- ✅ Configuração multi-chave: `PLUGHUB_ANTHROPIC_KEYS` (vírgula) + `PLUGHUB_OPENAI_KEYS` como fallback
- ✅ `_call_with_fallback`: rotação automática em 429/529 → `mark_throttled` → próxima conta → cross-provider fallback
- ✅ Isolamento de workloads: profiles `realtime` (Sonnet → gpt-4o), `balanced` (Haiku → gpt-4o-mini), `evaluation` (Haiku isolado)
- ✅ `OpenAIProvider` com graceful degradation se SDK ausente; tool format conversion; role mapping
- ✅ Config API namespace `ai_gateway`: `account_rotation_enabled`, `throttle_retry_after_s`, `utilization_rpm_weight`, `evaluation_model`, `evaluation_max_tokens`
- ✅ 29 assertions em `test_account_selector.py`
- ✅ E2E scenario 26: throttle → fallback → recovery (`--fallback` flag)

---

## Arc 6 v2 — Permissões 2D + Workflow Motor

- ✅ `evaluation_permissions` substituído por ABAC (`module_config.evaluation.revisar` / `contestar`)
- ✅ `_check_abac_permission` em `router.py`; graceful degradation para tokens legacy
- ✅ Campos de workflow em `evaluation_results`: `workflow_instance_id`, `resume_token`, `action_required`, `current_round`, `deadline_at`, `lock_reason`
- ✅ Anti-replay de `round` em review/contestation endpoints
- ✅ Consumer `workflow.events` no evaluation-api
- ✅ `skill_revisao_simples_v1.yaml` e `skill_revisao_treplica_v1.yaml`
- ✅ MCP tool `evaluation_lock` (idempotente)
- ✅ E2E scenarios 27/28 (11 + 11 assertions)

---

## Calendar API — Fase 4: Múltiplos Intervalos de Tempo por Dia

- ✅ **Engine**: já suportava múltiplos slots desde a implementação inicial (estrutura `{day, open, slots: [{open, close}]}`); nenhuma mudança necessária no backend
- ✅ **5 novos testes de engine** (`test_engine.py` — total: 46/46):
  - `test_between_slots_returns_start_of_next_slot` — 12:30 durante pausa → retorna 13:00 mesmo dia
  - `test_within_first_slot_returns_current_time` — 10:00 dentro do primeiro slot → já aberto
  - `test_after_all_slots_returns_next_day_open` — 17:30 após último slot → 08:00 dia seguinte
  - `test_is_open_false_during_gap` — gap entre slots retorna `False`; início de slot retorna `True`
  - `test_crosses_gap_between_slots` — `add_business_duration` 11:00 + 3h cruza pausa (12–13) → 15:00
- ✅ **CalendarsPage.tsx** — corrigido bug de formato de dados (UI enviava `{day_of_week, start_time, end_time}` mas o engine lê `{day, open, slots: [{open, close}]}`); tipos `TimeInterval` + `WeeklyDaySchedule` agora corretos
- ✅ **WeeklyEditor** — reescrito para suportar N intervalos por dia: cada slot é uma linha; botão `× ` remove um intervalo; botão `+ intervalo` adiciona (máx 4); fallback para `08:00–18:00` ao remover último slot
- ✅ **`scheduleLabel`** — exibe `Segunda(2)` quando o dia tem múltiplos slots
