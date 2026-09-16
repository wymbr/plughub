# Fatos do código — base da skill `skill-flow-authoring`

> Levantados em 2026-09-16. Os marcados **(conferido)** foram lidos diretamente no fonte nesta data;
> os demais vieram de levantamento com citação de linha e não foram relidos um a um. Linhas mudam:
> antes de citar uma como estado atual, abra o arquivo.

## Esquema e validação

| Fato | Onde |
|---|---|
| `SkillSchema`, `SkillFlowSchema` (refine: `entry` existe; há `complete` ou `escalate`), união `FlowStepSchema` | `packages/schemas/src/skill.ts:1426`, `:1382`, `:1409-1415`, `:1152` |
| 17 tipos de step; despacho 1:1 com executores | `packages/skill-flow-engine/src/executor.ts:330-346`, `src/steps/` |
| Comentário *"Os 8 tipos de step"* desatualizado | `packages/schemas/src/skill.ts:212` |
| Allowlist por perfil; `webhook` ⇒ `workflow`, ausência ⇒ `agent` **(conferido)** | `packages/schemas/src/skill-profile.ts:65-83` |
| Perfil aplicado só no deploy: `judgeSlotCandidate` no set-next e no promote | `agent-registry/src/lib/slot-candidate.ts:73`, `routes/pool-slots.ts:160`, `:249` |
| `validateSkillPayload` e códigos de erro **(conferido)** | `agent-registry/src/validators/skill.ts:447`, `:458-503` |
| Config-api fora ⇒ checagem de cadastro passa com `console.warn` | `validators/skill.ts:349-360` |
| Ciclos só no motor (`validateFlow`), não no save | `skill-flow-engine/src/engine.ts:274`, `:438` |
| `id` `^skill_[a-z0-9_]+$`; docstring ainda cita `_v\d+$` | `orchestrator-bridge/.../registry_syncer.py:79`, `:636` |

## Referências

| Fato | Onde |
|---|---|
| `resolveRef` despacha `@masked.` · `@segment.` · `@ctx.` · `$.` | `skill-flow-engine/src/interpolate.ts:40`, `:261`, `:275`, `:320`, `:327` |
| Referência pura não resolvida é OMITIDA do `input` (CTR-09, 2026-09-15) **(conferido)** | `interpolate.ts:200-222` |
| Template `{{…}}` ausente ⇒ `""`; `@masked` ausente ⇒ `""` | `interpolate.ts:137`, `:316-324` |

## ContextStore

| Fato | Onde |
|---|---|
| `collectContextTagWrites`; comentário diz 5 superfícies, o tipo tem 7 | `packages/schemas/src/context-map.ts:940`, `:905`, `:879-886` |
| Nome de tag dinâmico recusado | `agent-registry/src/validators/skill.ts:321` |
| `core.*` reservado; override de `context_map` por tenant recusado | `context-map.ts:230`; `config-api/.../router.py:281`, `:284` |

## Steps

| Fato | Onde |
|---|---|
| `reason`: campos e `customer_utterance` | `skill.ts:340-392`, `:345-361`; `steps/reason.ts:204-219` |
| `prompt_id` não é resolvido: prompt de sistema fixo + schema + `input` **(conferido)** | `ai-gateway/.../reason.py:28-32`, `:158-164` (único uso de `prompt_id` é o docstring `:6`; descrição do campo em `models.py:108`) |
| Padrão real de instrução no `input` **(conferido)** | `skill-flow-engine/skills/agente_contexto_ia_v1.yaml:211-226` |
| `invoke` de flow chama `ctx.mcpCall` sem `judgeInvoke`; servidor padrão | `steps/invoke.ts:122`, `:69`; `judgeInvoke` em `mcp-server-plughub/src/lib/invoke-audit.ts:48` |
| `tools[]` não copiado pelo syncer | `registry_syncer.py:628-730` |
| `menu.timeout_s` e `standby` | `skill.ts:466-478`, `:516-525` |
| `delegate` pool não resolvido; `timeout_hours` padrão | `steps/delegate.ts:178-196`, `:205-222` |
| `loop.body` e `over` não-array | `skill.ts:1323-1331`; `steps/loop.ts:49-50` |
| `complete.outcome_from` inválido cai no literal sem log **(conferido)** | `steps/complete.ts:27-33` |

## Execução e deploy

| Fato | Onde |
|---|---|
| Só o slot `current`; sem slot ⇒ ERROR e não roda | `orchestrator-bridge/.../main.py:1066`, `:1160`, `:1175-1186` |
| `ALLOW_LIVE_FLOW_FALLBACK` padrão falso | `main.py:795-806` |
| Skill seed-if-absent | `registry_syncer.py:739-790` |
| `deploy_skill_to_slot.sh <skill_yaml> <pool_id> [âncora]` **(conferido)** | `infra/scripts/deploy_skill_to_slot.sh` |
