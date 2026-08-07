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
| `agent_kind` | `human`\|`ai` | Tipagem (gates C_ai/C_human; queue⇒human) | registration | ✅ (F2.C) |
| `description` | string | Descrição | registration | ✅ |
| `channel_types` | Channel[] | Canais que apontam pro pool (hard filter) | registration | ✅ |
| `sla_target_ms` | int | SLA do atendimento (score factor) | registration | ✅ (timeout) |
| `max_reply_time_ms` | int? | Tempo máx por resposta do agente | registration | ✅ (timeout) |
| `webhook_skill_id` | string? | Skill "DIN" do pool webhook (Arc 19) | registration | ❌ |
| `max_concurrent_sessions` | int? | Throttle de backpressure downstream (webhook) | registration | ❌ |
| `session_reservation` | int? | Fatia reservada de sessões (admissão híbrida) | registration | ✅ (F2.C) |
| `routing_expression` | weights | Pesos do scoring (sla/wait/tier/churn/negócio) | registration | ✅ (pesos) |
| `evaluation` | {sampling_rate, skill_id_template} | Amostragem + template do avaliador (Arc 6) | registration | ➖ (dono: Quality/Campaigns) |
| `evaluation_template_id` | string? | ID explícito do template de avaliação | registration | ➖ (dono: Quality/Campaigns) |
| `supervisor_config.enabled` | bool | Liga monitor de supervisor IA | registration | ❌ |
| `supervisor_config.escalation_pools` | string[] | **Destinos do botão Transfer** (pool→pool) | registration | ✅ (F2.B, merge-safe) |
| `supervisor_config.intent_capability_map` | map | Capacidades sugeridas por intent (Agent Assist) | registration | ❌ |
| `supervisor_config.*` (history_window_days, insight_categories, sentiment_alert_threshold, relevance_model, proactive_delegation) | vários | Config do supervisor/copilot | registration | ❌ |
| `mentionable_pools` | map alias→pool | Aliases `@mention` (convidar IA assist) | registration | ✅ (F2.B) |
| `agent_groups` | string[] | Agent Groups (Arc 9) a que o pool pertence | registration | ➖ (dono: módulo Groups + JWT) |
| `copilot_skill_id` | string? | (deprecated) skill do co-pilot | registration | ➖ |
| `hooks.on_human_start[]` | {pool, side, nps_on_disconnect} | Hooks ao humano ENTRAR (ex.: copilot) | registration | ✅ (F2.A) |
| `hooks.on_human_end[]` | {pool, side, nps_on_disconnect} | **Hooks ao FECHAR: wrap-up + NPS** (qual pool/skill, side, nps_on_disconnect) | registration | ✅ (F2.A) |
| `hooks.post_human[]` | {pool, side, nps_on_disconnect} | Hooks após os on_human_end (qualidade/resumo) | registration | ✅ (F2.A) |
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

## Decisões de modelagem (F2, 2026-06-12, com o usuário)

- **Combos referenciam pool, não skill_id**: hooks, `escalation_pools` (Transfer) e `mentionable_pools`
  (@mention) guardam **pool_id**. Motivo: o pool é estável a mudanças de versão da skill-flow — referência
  por skill forçaria reescrever todas as referências a cada deploy. O label do combo exibe a
  `deployed_skill_id` corrente só como dica visual.
- **Transfer ≠ @mention** (são listas distintas): `escalation_pools` = destinos de escalate (transfere o
  contato para outra fila); `mentionable_pools` = lista de pools de especialistas acionáveis pelo agente
  humano via `@alias` (assist/conferência, NÃO transfere). Seções separadas na UI.
- **`max_concurrent_sessions` fora do editável**: enforcement no routing é *deferred* e na prática se
  sobrepõe a `session_reservation` (que vira a única alavanca de capacidade na UI).
- **`webhook_skill_id` fora do drawer de pool**: webhook é um canal — ao ser acionado pede um pool ao Core
  como qualquer canal, e o vínculo endpoint→pool já vive em `Configurations/Channels` (channel endpoint).
  *(Consolidação futura: rota de webhook 100% via channel endpoints; `webhook_skill_id` é candidato a
  cleanup.)*
- **`supervisor_config.enabled` não exposto**: a ativação de copilot/assist é coberta por hook
  `on_human_start`. A UI só persiste `escalation_pools` dentro de `supervisor_config` (merge-safe,
  preservando `intent_capability_map` etc.); `enabled` mantém o valor existente (default `false`).
- **`evaluation` / `evaluation_template_id` NÃO expostos no pool** (fonte única): a amostragem e a
  configuração de avaliação são donas do módulo **Quality → Campaigns** (evaluation-api: sampling +
  reviewer rules + scheduling; instances criadas na `session_closed`). O `pool.evaluation`
  (`sampling_rate`/`skill_id_template`, consumido por `rules-engine/evaluation_sampler`) é o caminho
  **legado/dormente** — `on_pool_config` nunca é chamado, o sampler sempre usa o default. Expor no pool
  duplicaria a fonte → fica fora do drawer por design. *(Cleanup futuro: remover o caminho dormente do
  rules-engine ou religá-lo só se a campanha não cobrir.)*
- **`agent_groups` NÃO exposto no pool** (dono = módulo Groups): grupo é definido no **perfil do
  usuário** (auth-api `agent_groups`/GroupsPage) e a permissão de ver dados do grupo vem no **JWT**
  (`supervised_groups[]`, Arc 9). `Pool.agent_groups` é denormalização escrita no ContextStore — não é
  editado à mão no pool. Fica fora do drawer.

## Gap a fechar — estado (2026-06-12)

Fechados na UI: `hooks` ✅ (F2.A), `supervisor_config.escalation_pools` ✅ (F2.B), `mentionable_pools`
✅ (F2.B), `agent_kind` ✅ (F2.C), `session_reservation` ✅ (F2.C).

Conscientemente FORA do drawer de pool (ver § Decisões): `max_concurrent_sessions` (deferred/overlap),
`webhook_skill_id` (config de canal), `evaluation` + `evaluation_template_id` (dono: Quality/Campaigns),
`agent_groups` (dono: módulo Groups + JWT), `supervisor_config.*` restantes/`intent_capability_map`
(preservados merge-safe, não editados).

**`deploy {skill_id, max_concurrent_sessions}`** — RESOLVIDO (F2.E, decisão: nada no pool). O ciclo de
deploy tem dono próprio na tela **Fluxo → Deploy** (`/agent-flow/deploy`) e é **consumido ponta a ponta**:
`PUT /v1/pools/:id/slots/next` (staging) → `POST /promote` (next→current) publica `registry.changed` →
orchestrator-bridge `bootstrap.request_refresh()` → `_build_desired_from_deploy` lê `deployed_skill_id`
+ `deployed_max_concurrent_sessions` do `GET /v1/pools` e provisiona as instâncias IA. Pelo princípio de
fonte única, deploy NÃO é editável no drawer de pool (sem duplicar o ciclo).

**F2-pool COMPLETA**: todo o gap de campos do pool ou foi exposto na UI (A–C) ou tem dono consciente em
outro módulo/tela (D, E) ou foi deixado fora por decisão (max_concurrent_sessions, webhook_skill_id).

## F2.F — entrega do hook e config do hook (2026-08-07)

O editor de hooks da F2.A cobria três campos (`pool`, `side`, `nps_on_disconnect`); os campos que a Camada A
do arco de detach e o ADR internal-work-queue acrescentaram DEPOIS ficaram fora, e com eles a decisão mais
visível do wrap-up. Fechado agora:

| Campo | Onde | Nota |
|---|---|---|
| `hooks[].dispatch` (`inline`\|`detached`) | por entrada, **só em slots de finalização** | `on_human_start` não recebe o seletor — o parse do backend **rejeita** `detached` lá, e oferecer o que não passa é pior que omitir |
| `hooks[].context.dialog_form_id` | por entrada | combo dos DialogForms da dialog-api; marca `(rascunho)` quem não está publicado e mostra o id quando o form referenciado sumiu do editor |
| `hooks[].context.acw_timeout_hours` | por entrada | vazio = default de 24 h do engine |
| `internal_queue_enabled` | seção própria do drawer | pré-requisito de `detached`+`side: agent` |

Duas decisões de desenho:

- **O 422 do registry virou aviso no formulário.** `detached` + `side: agent` sem fila interna é recusado
  pelo `detachedHookViolation`; a tela mostra o aviso na hora de marcar, porque o custo do erro é um
  wrap-up que só quebra no próximo atendimento real.
- **Desligar a fila interna pede confirmação e manda `?force_disable=true`.** O registry recusa por
  default — ele não enxerga a fila (é do routing-engine) e trata "não consigo verificar pendência" como
  pendência. A confirmação é do operador; `forceDisable` NÃO é default de conveniência no cliente.

Chaves de `context` que a tela não conhece são **preservadas** no round-trip (o editor faz merge por
chave, não substitui o objeto) — o campo é `Record<string,string>` aberto de propósito.

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
