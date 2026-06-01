# Instance Bootstrap — Reconciliation-Driven Agent Instance Management

> Última atualização: 2026-05-31 · Estado: Arc 16 + Fase 3 (deploy-driven)

## Fase 3b/3a — Provisionamento deploy-driven (2026-05-31)

Além do caminho legado por `agent_type`, o bootstrap agora provisiona instâncias de IA
a partir do **deploy do flow** (`PoolSkillSlot.current`), eliminando a dependência do
`agent_type` para pools novos:

- **Fonte**: `GET /v1/pools` (agent-registry) anexa `deployed_skill_id` +
  `deployed_max_concurrent_sessions` lidos do slot `current` de cada pool.
- **Builder** `_build_desired_from_deploy`: para cada pool com slot `current`, cria N
  instâncias `{pool_id}-{n}` (N = concurrent sessions do slot) rodando a skill deployada,
  com `skill_id`/`flow_id` no payload e `source=bootstrap_deploy`. Capacidade = `N × 1`.
- **Transição segura**: pools já cobertos por um `agent_type` legado são pulados (zero
  sobreposição). `skill_id` entrou no set MANAGED do diff de reconciliação.
- **Execução (3a)**: a síntese de native agent_type é **centralizada em `get_agent_type`**
  (`orchestrator-bridge/main.py`): no 404, se o `agent_type_id` for uma skill com flow,
  `_synthesize_agent_type_from_skill` devolve um native agent_type — cobre todos os
  caminhos de ativação (routed, conferência, queue, restore) num ponto único.
- **Precedência (3c)**: deploy vence. `_build_desired_state` recebe `deployed_pool_ids` e
  remove esses pools dos `pools` de cada agent_type (1:1 no demo → agent_type ignorado).
  Migrar um pool real = configurar+promover seu slot (`PUT /slots/next` + `POST /promote`),
  sem deletar agent_type.
- **Auto-provisionamento (3c)**: `RegistrySyncer._sync_deploy_slots` cria os slots a partir
  dos agent_types IA do YAML (idempotente), **opt-in** via `REGISTRY_SYNC_DEPLOY_SLOTS`
  (default `false` enquanto a síntese não replica `mention_commands`/`role` de especialista).
- **Invariante de modelo**: `AgentInstance` (routing-engine `models.py`) **deve** declarar
  `skill_id`/`flow_id` — `mark_busy` revalida via Pydantic e descartaria campos não
  declarados ao alocar, apagando a identidade da skill na instância busy.

**Fase 3c concluída (2026-06-01)**: todos os pools IA do demo migrados (slot+promote);
`mention_commands` de especialista resolvido pela Skill via **embed no flow** —
`mention_commands` declarado em `SkillFlowSchema` (sobrevive ao `CreateSkillSchema.parse`
do Zod), aninhado no `flow` por `_sync_skills`, persistido na coluna `flow` (JSON),
devolvido por `get_skill_flow`, carregado na síntese e resolvido em runtime por
`_resolve_mention_commands` (cache do flow → agent-registry → disco fallback). `role`/
`capabilities` não replicados (não consumidos em runtime). `REGISTRY_SYNC_DEPLOY_SLOTS=true`
ligado e validado (`deploy_slots set=2 skip=14 err=0`).

**Fase 3d-parcial concluída (2026-06-01)**: slots agora vêm do bloco `deploy: { skill_id,
max_concurrent_sessions }` de cada pool (`RegistrySyncer._sync_deploy_slots_from_pools`,
sempre roda, idempotente). Os `agent_types` IA foram **aposentados do YAML** — só o agente
humano resta declarado; o prune limpa os IA órfãos do registry. O reconcile é **deploy-only**:
`_build_desired_state`/`_extract_all_pool_ids`/`_fetch_agent_types` removidos; o desired state
vem só de `_build_desired_from_deploy` (pools com `deployed_skill_id`). Hack
`_applyMaxConcurrentSessions` (pool-slots.ts) removido. Validado: boot limpo provisiona 295
instâncias só dos slots, sem agent_type IA no registry.

Pendente (Fase C): renomear `agent_type_id`→`skill_id`/`flow_id` (Redis/Kafka/ClickHouse/
routing/segments) + identidade do agente humano por `user_id` + remover tabela/CRUD `AgentType`.

---


Implemented in `packages/orchestrator-bridge/src/plughub_orchestrator_bridge/instance_bootstrap.py`.

**Principle**: Agent Registry is the single source of truth. The Bootstrap operates as a
**reconciliation controller** (Kubernetes-style): it compares *desired state* (Registry)
with *actual state* (Redis) and applies only the minimum diff to converge them.
No restart needed for any configuration change — the controller self-heals.

## Reconciliation Algorithm

```
reconcile(tenant_id):
  # Section A — Agent instances (deploy-only, Fase 3d)
  registry_pools = GET /v1/pools          ← single call, all pools
  deployed_pools = [p for p in registry_pools if p.deployed_skill_id]
  desired        = build_desired_from_deploy(deployed_pools)   # {pool_id}-{n} per slot
  actual         = scan {tenant}:instance:* from Redis

  diff:
    to_create  → write instance key + SADD pool SET
    to_delete  → status=ready: DELETE + SREM  |  status=busy: mark draining=True
    to_update  → status=ready: update payload  |  status=busy: mark pending_update=True
    to_renew   → EXPIRE only (payload identical, TTL refresh)

  sync pool:*:instances SETs

  # Section B — Pools
  for each pool in registry_pools:
    if pool_config key missing or content diverged → SET pool_config:{pool_id}
    else → EXPIRE only (renew TTL)

  for each pool_config:* key in Redis NOT in registry_pools:
    DELETE pool_config:{pool_id}
    if pool:{pool_id}:instances SET is empty → DELETE it too

  sync {tenant}:pools global SET (+adds, -removes)
```

## Trigger Points

| Trigger | Action |
|---|---|
| Bridge startup | `reconcile()` — full diff + apply; logs ReconciliationReport |
| Heartbeat every 15s | `_heartbeat_tick()` — TTL renewal + drain/pending_update processing |
| Every 5 min (periodic) | `reconcile()` — auto-healing of any drift |
| `registry.changed` (Kafka) | `reconcile()` — immediate after signal |
| `config.changed` namespace=`quota` (Kafka) | `reconcile()` — quota limits changed, may affect instance count |

## Dry-run (Audit Without Applying)

```python
report = await bootstrap.dry_run("tenant_demo")
print(report.summary())
# tenant=tenant_demo created=2 deleted=1 drained=0 updated=1 renewed=7 unchanged=0 errors=0 (45ms)
```

## ReconciliationReport Fields

**Instances:** `created`, `deleted`, `drained`, `updated`, `renewed`, `unchanged`

**Pools:** `pools_written` (created or updated), `pools_removed` (deleted from Redis), `pools_set_sync` (IDs added/removed from `{tenant}:pools` SET)

**Common:** `errors`, `duration_ms`, `dry_run`

## Rules

- Human agents are NOT managed — login is user-initiated via Agent Assist UI.
- Busy/paused instances are never hard-deleted; they receive `draining=True` or `pending_update=True` and are processed by the heartbeat after the session ends.
- Idempotent: reconciling N times produces the same result as reconciling once.
- Instance IDs: `{agent_type_id}-{n+1:03d}` (e.g. `agente_demo_ia_v1-001`).
- `channel_types` on instances = union of `channel_types` from all associated pools.

## RegistrySyncer — YAML as Single Source of Truth for PostgreSQL

Implemented in `packages/orchestrator-bridge/src/plughub_orchestrator_bridge/registry_syncer.py`.
Runs BEFORE InstanceBootstrap at bridge startup. Reads `infra/registry/*.yaml` and:

1. **Upserts** pools and agent_types via Agent Registry REST API (POST → 201 created, 409 → PATCH)
2. **Prunes** stale agent_types not declared in YAML (`REGISTRY_SYNC_PRUNE=true`, default)
   - Lists all agent_types via `GET /v1/agent-types` and DELETEs any not present in the YAML
   - DELETE publishes `registry.changed` to Kafka → InstanceBootstrap cleans up Redis automatically
   - Set `REGISTRY_SYNC_PRUNE=false` to disable (multi-tenant environments with external agent registrations)

A fresh environment is fully self-configuring from YAML alone. Stale entries from old seeds or manual API calls are removed automatically on every startup — making DROP TABLE unnecessary.

## Skill Sync — YAML → Agent Registry (PostgreSQL)

In addition to pools and agent_types, RegistrySyncer also syncs **skill definitions** from
`packages/skill-flow-engine/skills/` to the Agent Registry at bridge startup.

**`skills_dir` parameter:**
```python
syncer = RegistrySyncer(
    registry_url=AGENT_REGISTRY_URL,
    config_path=REGISTRY_CONFIG_DIR or None,
    skills_dir=SKILLS_DIR or None,        # e.g. /app/skills
)
```

**Requirements for a YAML to be synced:**
- Must have `id:` matching regex `^skill_[a-z0-9_]+_v\d+$`
- Must have `entry:` and `steps:` fields
- `name:`, `version:`, `description:`, `classification:` are optional
- `mention_commands:` at top-level is included if present

Skills are PUT (upserted) before pools and agent_types. Skill IDs not matching the regex are silently skipped.

**`SyncReport`** extended fields: `skills_upserted`, `skills_skipped`, `skills_errors`.

## Skill Hot-Reload — Three-Elo Architecture

```
Elo 1 — RegistrySyncer (startup sync)
  bridge restart → reads *.yaml from SKILLS_DIR
  → PUT /v1/skills/{skill_id} → PostgreSQL is source of truth

Elo 2 — registry.changed event (agent-registry/routes/skills.ts)
  PUT /v1/skills/{id} → publishRegistryChanged(entity_type="skill", entity_id=skill_id)
  DELETE /v1/skills/{id} → publishRegistryChanged(...)
  → Kafka topic: registry.changed

Elo 3 — cache invalidation (orchestrator-bridge/main.py)
  registry.changed received → entity_type == "skill"
  → del _skill_flow_cache[skill_id]
  → next agent activation fetches updated flow from Agent Registry
```

**Live production update (no restart required):**
```
PUT /v1/skills/skill_copilot_sac_v1  →  registry.changed  →  cache invalidated  →  immediate effect
```

`_skill_flow_cache` — in-memory dict in orchestrator-bridge `main.py` mapping `skill_id → flow dict`. Populated on first activation. Invalidated individually per `registry.changed`.

**Note:** POST (create) on `/v1/skills` does NOT publish `registry.changed` — startup only; cache miss on first activation is acceptable.

**Known issue:** `agente_avaliacao_v1.yaml` has no `complete`/`escalate` step → HTTP 422 from agent-registry. RegistrySyncer logs warning + increments `skills_errors` without blocking startup. Evaluator agent falls back to `_load_yaml_fallback()`.

## Impact on Seed

`infra/seed/seed.py` no longer writes Redis instance keys, pool instance sets, pool_config keys, or the `{tenant}:pools` SET — all handled exclusively by InstanceBootstrap. The seed only registers pools and agent types in the Agent Registry API (PostgreSQL).
