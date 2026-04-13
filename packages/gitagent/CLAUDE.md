# @plughub/gitagent — GitAgent Parser and Importer

## What it is

Implements the PlugHub GitAgent pattern (spec 4.9).
Reads structured Git repositories and imports agents into the Agent Registry.
Converts YAML flows → JSON validated by the Zod schema before registering.

## Responsibilities

1. Parse the GitAgent repository structure (agent.yaml, instructions.md, flows/*.yaml)
2. Convert YAML flows → SkillFlow JSON with Zod validation
3. Generate AgentTypeRegistration payload from agent.yaml
4. Import a local or remote repository via POST /v1/agent-types and /v1/skills
5. CLI: plughub-sdk import <url-or-path>

## Invariants

- YAML is the authoring format — JSON is the runtime format
- Zod validation happens during YAML→JSON conversion — rejects invalid flows before registering
- .plughub/config.yaml never goes into the Git repository
- Credentials are in env vars — never in repository files

## GitAgent repository structure

```
my-agent/
  agent.yaml          ← required
  instructions.md     ← required
  flows/main.yaml     ← required if type: orchestrator
  tools.yaml          ← optional
  schema.yaml         ← optional
  evals/criteria.yaml ← optional
  .plughub/config.yaml ← in .gitignore
```

## Spec reference

- 4.9 — Full PlugHub GitAgent pattern
