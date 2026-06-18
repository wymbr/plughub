# Handoff — Reconciliação do módulo Evaluation (PlugHub)

> Cole o bloco abaixo como prompt inicial da nova sessão (Opus). Atualize "PRÓXIMA TAREFA"
> conforme o progresso.

---

Continuação da implementação do módulo de Evaluation do PlugHub (WSL Ubuntu,
`\\wsl.localhost\ubuntu\home\a1\projects\plughub`). Leia `CLAUDE.md` e a spec-alvo
`docs/product/evaluation-reconciliation-spec.md` (fonte de verdade; decisões DECIDIDO/RESOLVIDO
2026-06-17). Inglês no código, PT em i18n/spec/dados de tenant.

**IMPORTANTE — modo de trabalho:** o sandbox bash NÃO alcança o WSL → **você EDITA, EU rodo**
(rebuild/migrações/testes). Chunks pequenos e revisáveis; cada chunk: implementar → script de
teste em `infra/test/test_*.sh` → eu rodo → você atualiza docs (`docs/arcos/arc6-evaluation.md`
e/ou `arc13-review-contestation.md` + `CHANGELOG.md`) → me dá o comando de `git add/commit`.
Use AskUserQuestion pra decidir escopo/sub-chunking antes de codar tarefas grandes.

**Demo:** `docker compose -f docker-compose.demo.yml`. Portas/serviços:
evaluation-api `:3400`; auth-api `:3202` (login `POST /auth/login {email,password,tenant_id}`,
retorna `access_token`); ai-gateway `:3200` (`POST /v1/reason`; tem chave Anthropic real);
postgres serviço `postgres` (user/pass `plughub`, db `plughub_demo`, host `5433`); redis host
`6380` (interno 6379); skill-flow-service `:3460` (runtime do skill, chamado pela
orchestrator-bridge); tenant `tenant_demo`. Admin token eval: `changeme_eval_admin_token_demo`.
Usuários: operator@plughub.local/changeme_operator (ABAC contestar), supervisor@plughub.local/
changeme_supervisor (ABAC revisar), admin@plughub.local/changeme_admin. JWT secret alinhado
auth↔evaluation. curls precisam `-H Content-Type:application/json`.

**Rebuild:** Python code-only → `docker compose -f docker-compose.demo.yml up -d --build <svc>`
(schema aplica via `ensure_schema` no boot — **sem migração manual**). TS (mcp-server-plughub,
skill-flow-service, ai-gateway, @plughub/schemas) → rebuild da imagem do serviço. `infra/modules.yaml`
→ `up -d --force-recreate auth-api`. Forms endpoints são abertos (tenant_id em query/body);
`/threads` e `/campaigns/{id}/sampling-rules` usam header `X-Tenant-ID`; `dispatch`/`skip`/
`mark-error`/`ai-review` usam `X-Admin-Token` + `tenant_id` query.

**JÁ IMPLEMENTADO E VALIDADO (commitado) nesta leva** (spec §):
- **5c** — contest/review EM LOTE por critério + gate "tratar todas" (409 `pending_contestations`).
  `contestation_router.py`; `db.list_contested_criteria_for_round`.
- **T6a** — modelo do critério enriquecido (`@plughub/schemas/evaluation.ts`: `question`,
  `scoring_guidance`, `min_score`, `choice_scores`, `true/false_score`, `na_guidance`,
  `applies_when`, `evidence_required`, `contestable` + helpers `deriveContestable`/
  `deriveEvidenceRequired`) + `db.normalize_form()` (migração-sem-reescrita na leitura).
- **T6b** — deploy lifecycle: `forms.deploy_status` (draft|published) + tabela imutável
  `form_versions` + `publish_form`/`get_form_version`/`list_form_versions`/`latest_published_version`
  + `POST /forms/{id}/publish`, `GET /forms/{id}/versions(/{v})`; sampling pina versão publicada;
  `update_form` bifurca draft ao editar publicado.
- **T7a** — `scoring.py` (`aggregate_scores` recomputa nota do form; `validate_criterion_responses`);
  `_ingest_core` descarta nota do LLM e recomputa, valida (strict HTTP / lenient consumer+seeder),
  threads round-1 por critério.
- **T7b-1** — `reason` do ai-gateway aceita `ReasonRequest.json_schema` via **tool-use nativo**
  (`force_tool` nos providers; `_process_tool_use` + `_validate_json_schema` recursivo + retry).
- **T7b-2** — `buildEvaluationOutputSchema(form)` no `evaluation_context_get` (mcp-server) expõe
  `evaluation_output_schema`; skill `agente_avaliacao_v1.yaml` usa `json_schema_ref`;
  `ReasonStep` += `json_schema`/`json_schema_ref` (skill-flow-engine), runners forwardam;
  `composite_score` do `evaluation_submit` virou opcional.
- **T7b-3** — removidos shims do `evaluation_submit` (DimensionThread preprocess, coerção
  compliance_flags). `evaluation_rubric_v3` confirmado vestigial. **T7 completo.**
- **T13-core** — `POST /instances/{id}/skip` (→skipped) + `/mark-error` (→error), guardados.
- **T12** — `_is_flagged` (regra `score_extremes`) → gate `ai_review` no ingest;
  `POST /instances/{id}/ai-review` (IA→finalize auto_ai; humano→contestation_open).
  **+ fix bug latente §2.2**: `results_contestation_state_check` recriado permissivo
  (não aceitava `auto_finalized`/`closed_max_rounds`).
- **T17-core** — `campaigns.period_start/period_end` + filtro forward no sampling
  (`_within_campaign_window`). *(confirme que o commit do T17-core foi feito.)*

**GOTCHAS aprendidos (não repetir):**
1. **e2e do avaliador real está BLOQUEADO** pela infra do demo: o session-replayer faz
   ensure-before-read e **curto-circuita a alocação do avaliador no cache-hit**; sessões antigas
   não re-hidratam (stream expira ~1h). Semear ReplayContext no Redis NÃO resolve (impede a
   alocação). → As "fronteiras" de skill (fiação T7b-2/T13 no YAML, timeout do ai_review,
   T17-backfill) ficam **e2e-blocked**; validamos por **proxy** (`/v1/reason` direto), **unit**
   e **API**. Pra e2e real precisaria de sessão webchat fresca.
2. **asyncpg + TIMESTAMPTZ**: exige `datetime`, não string; `$n::timestamptz` NÃO converte string
   de entrada → parsear ISO em Python antes (`db._parse_ts`).
3. **jq 1.7 preserva `7.0`** (não vira `7`) → asserts numéricas via `awk`.
4. **CHECKs legados restritivos**: `action_required` só `review|contestation`; `evaluated_agent_type`
   só `human_agent|ai_agent`; `contestation_state` é espelho deprecado (verdade = `result_state`/
   `chk_result_state`). Ao gravar estados, cuidado com CHECK violation.
5. **Modelo do form** cabeado = aninhado `dimensions[].criteria[]` (DB JSONB opaco; platform-ui
   tem `EvaluationCriterion` próprio em `types/index.ts`). `@plughub/schemas` tem um paralelo flat.
6. Sem ambiente Node/build no WSL → unit tests TS rodam só dentro do container (ou pular). UI
   (platform-ui) é difícil de validar; preferir backends testáveis por API.
7. Estados: `result_state` ∈ {ai_review, open, under_review, finalized, error_rejected} (T1, canônico);
   instance.status inclui `skipped`/`error`/`error_rejected`/`expired`. `finalize_evaluation` é o
   ÚNICO emissor de `evaluation_finalized` (relatórios filtram isso).

- **T15** — dispatcher por janela de calendário (§18.4). Tarefa de fundo
  (`main._run_dispatch_scanner`, loop ~60s, gated `dispatch_scanner_enabled`) + endpoint
  `POST /v1/evaluation/dispatch/scan` (admin, uma passada). Core `router.dispatch_campaign_scheduled`.
  Idempotência via `instances.dispatched_at` + cooldown (`db.claim_dispatchable_instances`, claim
  atômico). Janela = `calendar-api /v1/engine/is-open` na entidade `evaluation_campaign:{id}`
  (`sampling.campaign_dispatch_open`; sem associação/down → aberto best-effort). Manual
  `/campaigns/{id}/dispatch` permanece (força). Test `test_t15_dispatcher.sh`. *(O scanner real
  emitindo p/ avaliador é e2e-blocked pela infra do demo — gotcha 1; validado por API.)*

**PRÓXIMA TAREFA: T17-backfill — job batch sobre segmentos persistidos (§18.5).** Enumera os
segmentos já fechados na janela `[period_start, period_end]` da campanha a partir do store
persistido (`analytics.segments` / Session Replayer), cria as instances por segmento e as despacha
(encaixa no dispatcher T15); o avaliador reconstrói o transcript via Hydrator/ReplayContext.
Liga à validação de prompt (T8 + deploy epochs) rodando uma campanha sobre janela histórica.
Dep. T2 (chave por segmento). Avaliar fonte dos segmentos (analytics-api vs replayer) e sub-chunking.

**Depois:** T14 (calibração com dado real
+ bug `resolve_curation` já corrigido no 5d? confirmar), T8 (rubrica-template UI Quality),
T9–T11 (UI: drill-down modo Forms, 3 papéis ABAC, relatórios Oficial×Operacional), T16 (corrigir
docs arc6/arc13/CLAUDE.md/TODO), T4b (notificar avaliado no enter-`open`), + fronteiras e2e-blocked.
