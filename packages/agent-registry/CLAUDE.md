# @plughub/agent-registry — Administrative API

## What it is

Administrative REST API for registering pools, agent types, and skills.
Cross-validations that Zod schemas do not cover happen here.

## Cross-validations implemented here

- skill_id in AgentType.skills must exist in the Skill Registry (skills table)
- pool_id in AgentType.pools must exist in the Agent Registry (pools table)
- mcp_server in SkillTool must be registered for the tenant (mcp_servers table)
- evaluation_template_id must exist in the evaluation_templates table

## Routes

### Pools
- POST   /v1/pools
- GET    /v1/pools
- GET    /v1/pools/:pool_id
- PUT    /v1/pools/:pool_id

### Agent Types
- POST   /v1/agent-types
- GET    /v1/agent-types
- GET    /v1/agent-types/:agent_type_id

### Skills
- POST   /v1/skills
- GET    /v1/skills
- GET    /v1/skills/:skill_id

## Invariants

- tenant_id always inferred from JWT — never from the body
  > **Measured false until 2026-09-13 (PID-07).** Every router read the tenant from the
  > `x-tenant-id` header and the gate checked only the grant: a `tenant_outro` token with
  > `x-tenant-id: tenant_demo` wrote a tenant_demo slot (200). Now, for WRITES with Bearer,
  > `requireAbacWrite` binds the tenant to the token (divergent header → 403 `tenant_mismatch`,
  > absent header → filled from the token) and the author to its `email`/`sub` (`authorOf`,
  > one helper; routers never read `x-user-id`). Service-token callers still act on the
  > header tenant and declare the author. READS stay open and header-scoped, by decision.
  > Deploy routes (`slots`/`promote`/`rollback`) are gated by `skill_flows.operacao`, mounted
  > BEFORE `/v1/pools` — mount order is the mechanism. Gate: `probe_deploy_write_principal.sh`.
- Every mutation is recorded in the audit log (created_at, updated_at, created_by)
- skill_id and agent_type_id are immutable after creation — create a new version
- Pools cannot be deleted — only deactivated (status: inactive)

## Stack

- TypeScript + Express
- PostgreSQL + Prisma
- Zod for payload validation
- @plughub/schemas as the source of truth for contracts

## Spec reference

- 4.5 — Agent Registry (pool + agent type)
- 4.7 — Skill Registry
