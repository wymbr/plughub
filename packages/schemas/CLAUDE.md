# PlugHub Platform — Schemas v24.0

## Architectural invariants — never violate

- AI Gateway is stateless — one turn per LLM call
- Routing Engine is the sole arbiter — no component routes without it
- MCP is the only integration protocol between components
- pipeline_state persists to Redis on every step transition
- Agent contract: agent_login → agent_ready → agent_busy → agent_done
- agent_done requires handoff_reason when outcome !== "resolved"
- issue_status is always mandatory and never empty

## This package

Zod schemas that are the source of truth for all platform contracts.
Any component that produces or consumes these objects must import from here —
never redefine locally.

## Files

- `context-package.ts` — ContextPackage, AgentDone, SessionItem
  PlugHub spec v24.0 sections 3.4, 3.4a, 4.2

- `skill.ts` — Skill, SkillFlow, FlowStep (9 types: task, choice, catch, escalate, complete, invoke, reason, notify, menu), MenuPayload, MenuSubmitEvent
  PlugHub spec v24.0 sections 4.7, 4.7m

- `agent-registry.ts` — PoolRegistration, AgentTypeRegistration, PipelineState
  PlugHub spec v24.0 section 4.5

## Naming conventions

- skill_id:      skill_{name}_v{n}           e.g. skill_portabilidade_telco_v2
- agent_type_id: {name}_v{n}                e.g. agente_retencao_v1
- pool_id:       snake_case without version  e.g. retencao_humano
- Insight categories:  insight.historico.* or insight.conversa.*
- Outbound categories: outbound.*

## Stack

- Product: PlugHub Platform
- Package: @plughub/schemas
- Runtime: TypeScript 5.4+
- Validation: Zod 3.23+
- Tests: Vitest

## CLI (next steps)

- `plughub-sdk certify` — validates the execution contract
- `plughub-sdk verify-portability` — verifies isolation
- `plughub-sdk regenerate` — regenerates a proprietary agent as native
- `plughub-sdk skill-extract` — extracts skill from an existing agent

## MCP Servers

- `mcp-server-plughub` — Agent Runtime and BPM tools

## Runtime validations NOT covered by schemas

Responsibility of the administrative API — not of the Zod schemas:
- skill_id in AgentTypeRegistration.skills must exist in the Skill Registry
- pool_id in AgentTypeRegistration.pools must exist in the Agent Registry
- mcp_server in SkillTool must be registered for the tenant
- evaluation_template_id must exist in the template store
