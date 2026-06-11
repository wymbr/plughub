# Pool Config Surface — inventário de campos e plano de exposição na UI

> Objetivo: **eliminar o seed via YAML** (`infra/registry/tenant_demo.yaml`) levando TODO o config de
> pool para a tela `config/resources/pool` (registry-backed). Decisão 2026-06-11: o YAML é tratado
> como seed-a-eliminar; o provisionamento deve sair 100% do store de config (agent-registry), editável
> na UI. Este doc é o inventário-fonte: o que existe no contrato, o que a UI já expõe, e o gap.

## Contrato autoritativo

A fonte da verdade é `PoolRegistrationSchema` em `@plughub/schemas/agent-registry.ts` (o que a registry
aceita/persiste). O YAML usa um **subconjunto** por pool; a UI deve cobrir o **superconjunto** do
contrato. Há ainda o **deploy slot** (`deploy: {skill_id, max_concurrent_sessions}` no YAML), que é
sincronizado por um endpoint à parte (`PUT /v1/pools/{id}/slots`) — não é campo do `PoolRegistrationSchema`.

## Inventário de campos

Legenda **UI hoje**: ✅ exposto · ❌ ausente (gap) · ➖ legado/skip.

| Campo | Tipo | O que faz | Onde mora | UI hoje |
|---|---|---|---|---|
| `pool_id` | string | Identidade do pool | registration | ✅ |
| `agent_kind` | `human`\|`ai` | Tipagem (gates C_ai/C_human; queue⇒human) | registration | ❌ |
| `description` | string | Descrição | registration | ✅ |
| `channel_types` | Channel[] | Canais que apontam pro pool (hard filter) | registration | ✅ |
| `sla_target_ms` | int | SLA do atendimento (score factor) | registration | ✅ (timeout) |
| `max_reply_time_ms` | int? | Tempo máx por resposta do agente | registration | ✅ (timeout) |
| `webhook_skill_id` | string? | Skill "DIN" do pool webhook (Arc 19) | registration | ❌ |
| `max_concurrent_sessions` | int? | Throttle de backpressure downstream (webhook) | registration | ❌ |
| `session_reservation` | int? | Fatia reservada de sessões (admissão híbrida) | registration | ❌ |
| `routing_expression` | weights | Pesos do scoring (sla/wait/tier/churn/negócio) | registration | ✅ (pesos) |
| `evaluation` | {sampling_rate, skill_id_template} | Amostragem + template do avaliador (Arc 6) | registration | ❌ |
| `evaluation_template_id` | string? | ID explícito do template de avaliação | registration | ❌ |
| `supervisor_config.enabled` | bool | Liga monitor de supervisor IA | registration | ❌ |
| `supervisor_config.escalation_pools` | string[] | **Destinos do botão Transfer** (pool→pool) | registration | ❌ |
| `supervisor_config.intent_capability_map` | map | Capacidades sugeridas por intent (Agent Assist) | registration | ❌ |
| `supervisor_config.*` (history_window_days, insight_categories, sentiment_alert_threshold, relevance_model, proactive_delegation) | vários | Config do supervisor/copilot | registration | ❌ |
| `mentionable_pools` | map alias→pool | Aliases `@mention` (convidar IA assist) | registration | ❌ |
| `agent_groups` | string[] | Agent Groups (Arc 9) a que o pool pertence | registration | ❌ |
| `copilot_skill_id` | string? | (deprecated) skill do co-pilot | registration | ➖ |
| `hooks.on_human_start[]` | {pool, side, nps_on_disconnect} | Hooks ao humano ENTRAR (ex.: copilot) | registration | ❌ |
| `hooks.on_human_end[]` | {pool, side, nps_on_disconnect} | **Hooks ao FECHAR: wrap-up + NPS** (qual pool/skill, side, nps_on_disconnect) | registration | ❌ |
| `hooks.post_human[]` | {pool, side, nps_on_disconnect} | Hooks após os on_human_end (qualidade/resumo) | registration | ❌ |
| `queue_config.skill_id` | string? | Flow de tratamento de fila | registration | ✅ |
| `queue_config.max_wait_s` | int | Teto de espera (max_wait_exceeded) | registration | ✅ (timeout) |
| `queue_config.agent_type_id` | string | (legacy) | registration | ➖ |
| `calendar_id` | uuid? | Calendário associado (cache; fonte em calendar-api) | registration | ✅ |
| `context_visibility.operator_namespaces` | string[] | Namespaces do ContextStore visíveis ao operador | registration | ✅ |
| `deploy.skill_id` | string | **Skill que o pool IA executa** | slots endpoint | ❌ |
| `deploy.max_concurrent_sessions` | int | Concorrência (slots) do pool IA | slots endpoint | ❌ |

## Mapeamento dos itens citados pelo usuário

- **"Ativa NPS com qual script"** → `hooks.on_human_end[]` com `{pool: nps_ia, side: customer, nps_on_disconnect: skip}`. O "script" é o pool/skill do hook de NPS. ❌ não exposto.
- **"Wrap-up"** → `hooks.on_human_end[]` com `{pool: wrapup_ia, side: agent}`. ❌ não exposto.
- **"Timeouts"** → `sla_target_ms` ✅, `max_reply_time_ms` ✅, `queue_config.max_wait_s` ✅. Já cobertos.
- **Transfer** → `supervisor_config.escalation_pools` ❌ (fix de contrato aplicado 2026-06-11; falta UI).

## Gap a fechar (não lido/editável na UI hoje)

`agent_kind`, `deploy {skill_id, max_concurrent_sessions}`, `webhook_skill_id`, `max_concurrent_sessions`,
`session_reservation`, `evaluation` + `evaluation_template_id`, `supervisor_config` (incl.
`escalation_pools`), `mentionable_pools`, `agent_groups`, `hooks` (on_human_start/on_human_end/post_human).

## Plano de fases (proposto)

1. **Contrato** — garantir que cada campo do gap está no `PoolRegistrationSchema` e é aceito no
   create/update da registry (escalation_pools ✅ feito; auditar os demais — a maioria já está).
2. **UI core** — adicionar à tela resources/pool, agrupados: (a) **Hooks** (wrap-up/NPS/post — editor de
   lista {pool, side, nps_on_disconnect}); (b) **Transfer/@mention** (escalation_pools + mentionable_pools);
   (c) **Capacidade** (deploy skill+concorrência p/ IA, max_concurrent_sessions, session_reservation);
   (d) **Avaliação** (sampling_rate, template); (e) **agent_kind/agent_groups/webhook_skill_id**.
3. **Validação E2E** — configurar um pool 100% pela UI (sem YAML) e confirmar wrap-up+NPS+Transfer
   funcionando; depois remover o pool do YAML e confirmar que segue funcionando (prova de "sem seed fora").
4. **Aposentar o YAML** — quando a UI cobrir o superconjunto, migrar o `tenant_demo.yaml` para um seed
   ÚNICO no store de config (ou um bootstrap idempotente que escreve só na registry), e eliminar o
   provisionamento via arquivo. (Liga à "Fase 3 — Config + Deploy" do CLAUDE.md.)

> Itens `deprecated`/legacy (`copilot_skill_id`, `queue_config.agent_type_id`) ficam fora da UI.
