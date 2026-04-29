# GitAgent — Ciclo de vida completo

> Guia transversal: envolve `sdk`, `schemas`, `skill-flow-engine` e `agent-registry`
> Spec de referência: seções 4.6e, 4.6h, 4.6i, 4.6k

## O que é um GitAgent

Um **GitAgent** é um agente cujo código-fonte vive em um repositório Git, desenvolvido com qualquer framework (proprietário, LangGraph, CrewAI, etc.) ou nenhum. Ele segue uma convenção de arquivos que permite ao SDK ler, certificar, e converter seus artefatos para o formato nativo da plataforma.

A relação com o SDK é direta: o comando `plughub-sdk regenerate` é o elo entre o repositório Git do agente e a plataforma — ele lê os artefatos do repositório e produz os artefatos de registro e execução necessários para que o agente participe de um pool.

---

## Estrutura de um repositório GitAgent

```
meu-agente/
  agent.yaml          ← OBRIGATÓRIO — manifesto do agente
  agent.py            ← código do agente (qualquer linguagem/framework)
  SOUL.md             ← opcional — identidade e persona (Camada 1 do prompt)
  DUTIES.md           ← opcional — políticas e limites (Camada 2 do prompt)
  SKILL.md            ← opcional — skill associada ao agente
  flow.yaml           ← opcional — Skill Flow declarativo
  tools/              ← opcional — declaração das tools MCP usadas
    customer_get.yaml
    ticket_create.yaml
```

### `agent.yaml` — manifesto obrigatório

```yaml
agent_type_id: agente_retencao_v1   # formato: {nome}_v{n} — snake_case, sem maiúsculas
framework: proprietary              # ou: langgraph, crewai, langchain, native
execution_model: stateless          # ou: stateful
pools:
  - retencao_humano                 # pelo menos um pool obrigatório
permissions:
  - mcp-server-crm:customer_get
  - mcp-server-crm:ticket_create
  - mcp-server-telco:plan_get
  - mcp-server-telco:plan_upgrade
version: "1.0.0"                    # opcional — fallback: git tag ou branch
description: Agente de retenção
```

**Validações do manifesto** (aplicadas pelo `certify` e pelo `regenerate`):

| Campo | Regra |
|---|---|
| `agent_type_id` | Regex `/^[a-z][a-z0-9_]+_v\d+$/` — ex: `agente_retencao_v1` |
| `framework` | String não vazia |
| `execution_model` | Deve ser `stateless` ou `stateful` |
| `pools` | Array com ao menos 1 elemento |
| `permissions` | Array de strings no formato `{mcp-server}:{tool}` |
| `version` | Opcional — inferido do git tag ou branch se ausente |

O manifesto aceita os nomes `agent.yaml`, `agent.yml`, `manifest.yaml` ou `manifest.yml`.

### `SOUL.md` e `DUTIES.md` — camadas do prompt

O prompt do agente é construído em duas camadas, preservando a separação entre identidade e responsabilidades:

```
SOUL.md   → Camada 1 — Identity & Persona
              "Você é um especialista em retenção de clientes..."
              "Seu tom é empático, direto e orientado a solução..."

DUTIES.md → Camada 2 — Políticas e Limites
              "Nunca ofereça descontos acima de 20% sem aprovação..."
              "Sempre registre o motivo do churn no ticket..."
```

Se um dos arquivos estiver ausente, o `regenerate` gera um placeholder no `prompt.md` de saída e emite um warning — não aborta.

### `flow.yaml` — Skill Flow declarativo

Quando o agente orquestra um fluxo de trabalho, ele pode declará-lo em `flow.yaml` usando o subconjunto de tipos de step do GitAgent (equivalentes ao Skill Flow nativo):

```yaml
entry: verificar_cliente
steps:
  - id: verificar_cliente
    type: task
    agent_pool: suporte_standard
    on_success: concluir
    on_failure: escalar

  - id: escalar
    type: escalate
    target:
      pool: supervisor
    error_reason: falha no atendimento inicial

  - id: concluir
    type: complete
    outcome: resolved
```

> **Diferença de sintaxe**: no `flow.yaml` do GitAgent, steps de `task` usam `agent_pool` (nome do pool). No `flow.json` nativo gerado pelo `regenerate`, o campo é convertido para `target.skill_id`. A conversão é feita por `regenerate/convert.ts`.

Se `flow.yaml` existir mas for inválido, o `regenerate` **aborta com erro** — nunca gera artefatos parciais.

### `tools/` — declaração de tools MCP

Cada arquivo em `tools/` representa uma tool MCP usada pelo agente. As permissões são extraídas e mescladas com `permissions[]` do manifesto:

```yaml
# tools/customer_get.yaml
permission: mcp-server-crm:customer_get
```

---

## Ciclo de vida completo

```
┌─────────────────────────────────────────────────────────────┐
│ 1. DESENVOLVIMENTO                                          │
│    Escrever agent.yaml, SOUL.md, DUTIES.md, flow.yaml       │
│    Implementar lógica em qualquer linguagem/framework        │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. CERTIFICAÇÃO  (plughub-sdk certify --dir .)              │
│    Valida contrato de execução sem ambiente da plataforma    │
│    Gate obrigatório no pipeline CI/CD                        │
│    Retorna exit ≠ 0 se qualquer check falhar                 │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. REGENERAÇÃO  (plughub-sdk regenerate --dir . --output .) │
│    Lê artefatos do repositório                               │
│    Gera: agent-type.json, prompt.md, flow.json (se houver), │
│           skill-ref.json (se houver), proxy_config.yaml      │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│ 4. REGISTRO  (agent-registry API)                           │
│    POST /agent-types com agent-type.json                     │
│    Vincula agente a pools configurados                       │
│    A plataforma passa a conhecer o tipo de agente            │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│ 5. DEPLOY + RUNTIME                                         │
│    Se framework != native: iniciar proxy sidecar             │
│      plughub-sdk proxy --config ./output/proxy_config.yaml  │
│    Iniciar o processo do agente                              │
│    O agente entra no ciclo: login → ready → busy → done     │
└─────────────────────────────────────────────────────────────┘
```

---

## Artefatos de saída do `regenerate`

### `agent-type.json`

Registro do tipo de agente para a plataforma. Consumido pela `agent-registry API`.

```json
{
  "agent_type_id":   "agente_retencao_v1",
  "framework":       "proprietary",
  "execution_model": "stateless",
  "pools":           ["retencao_humano"],
  "permissions":     [
    "mcp-server-crm:customer_get",
    "mcp-server-crm:ticket_create",
    "mcp-server-telco:plan_get",
    "mcp-server-telco:plan_upgrade"
  ],
  "version":         "1.0.0",
  "description":     "Agente de retenção com dois MCP Servers",
  "_generated_from": "plughub-sdk regenerate",
  "_generated_at":   "2026-03-31T..."
}
```

### `prompt.md`

Prompt consolidado em duas camadas, gerado a partir de `SOUL.md` e `DUTIES.md`:

```markdown
# Prompt do Agente
<!-- Gerado por plughub-sdk regenerate — revise antes do deploy -->

## Camada 1 — Identity & Persona
[conteúdo de SOUL.md]

## Camada 2 — Políticas e Limites
[conteúdo de DUTIES.md]
```

Se `SOUL.md` ou `DUTIES.md` estiver ausente, o `regenerate` insere um placeholder com `<!-- TODO: ... -->` e emite warning. O arquivo `prompt.md` deve ser **revisado antes do deploy** em produção.

### `flow.json`

Skill Flow convertido de `flow.yaml` para o formato nativo da plataforma. Diferença principal: `agent_pool` → `target.skill_id`.

```json
{
  "entry": "verificar_cliente",
  "steps": [
    {
      "id":             "verificar_cliente",
      "type":           "task",
      "target":         { "skill_id": "skill_suporte_standard_v1" },
      "_agent_pool":    "suporte_standard",
      "execution_mode": "sync",
      "on_success":     "concluir",
      "on_failure":     "escalar"
    },
    {
      "id":           "escalar",
      "type":         "escalate",
      "target":       { "pool": "supervisor" },
      "context":      "pipeline_state",
      "error_reason": "falha no atendimento inicial"
    },
    {
      "id":      "concluir",
      "type":    "complete",
      "outcome": "resolved"
    }
  ]
}
```

### `proxy_config.yaml`

Gerado automaticamente quando `permissions[]` declara MCP Servers. Os nomes das variáveis de ambiente são derivados dos nomes dos servers:

```yaml
port: 7422
session_token_env: PLUGHUB_SESSION_TOKEN
audit_buffer_size: 1000
audit_flush_interval_ms: 500
circuit_breaker:
  timeout_ms: 50
  mode_on_failure: error_clear
routes:
  mcp-server-crm:   ${MCP_CRM_URL}
  mcp-server-telco: ${MCP_TELCO_URL}
```

### `skill-ref.json`

Gerado quando `SKILL.md` existe. Associa o agente a um Skill Flow existente no registry.

```json
{
  "skill_id":       "skill_retencao_telco_v1",
  "version_policy": "stable",
  "_source":        "SKILL.md",
  "_generated_at":  "2026-03-31T..."
}
```

---

## Inferência de versão

O `regenerate` infere a versão do agente na seguinte ordem de prioridade:

1. Campo `version` no `agent.yaml`
2. Git tag (`git describe --tags --abbrev=0`) — prefixo `v` removido automaticamente
3. Git branch (`git rev-parse --abbrev-ref HEAD`) — gera `0.0.0-{branch}`
4. Fallback: `"1.0.0"`

---

## Relação com o proxy sidecar

Todo GitAgent com `framework != native` e `permissions[]` declarando MCP Servers **precisa** do proxy sidecar em execução. O sidecar é o que garante que nenhuma chamada a um domain MCP Server escape da validação de permissões e do audit log.

```
GitAgent (framework: proprietary)
  │
  └─ Chama MCP Servers via → localhost:7422/{server}/{path}
       │
       proxy sidecar (plughub-sdk proxy)
         ├─ Valida permissions[] do JWT — local, ~0.1ms
         ├─ Encaminha ao domain MCP Server real
         └─ Registra AuditEvent no Kafka (async)
```

Um GitAgent com `framework: native` (código gerado com o SDK embutido) usa `PlugHubAdapter` in-process — não precisa do sidecar.

---

## Relacionamento com outros módulos

```
GitAgent (repositório Git)
  │
  ├── sdk
  │     ├─ plughub-sdk certify     → valida o manifesto e o contrato
  │     ├─ plughub-sdk regenerate  → lê artefatos e gera agent-type.json + flow.json + proxy_config.yaml
  │     └─ plughub-sdk proxy       → intercepta MCP calls em runtime
  │
  ├── schemas
  │     └─ AgentManifestSchema     → valida agent.yaml; AgentTypeSchema → agent-type.json
  │
  ├── skill-flow-engine
  │     └─ flow.json               → consumido como Skill Flow pela engine
  │
  └── agent-registry
        └─ POST /agent-types       → registra agent-type.json na plataforma
```

---

## Erros comuns e como resolver

| Erro | Causa | Solução |
|---|---|---|
| `agent.yaml não encontrado` | Manifesto ausente ou com nome diferente | Criar `agent.yaml` com os campos obrigatórios |
| `agent_type_id` — formato inválido | Nome não segue regex `{nome}_v{n}` | Renomear para ex: `agente_retencao_v1` |
| `pelo menos um pool é obrigatório` | `pools: []` ou campo ausente | Declarar ao menos um pool existente |
| `flow.yaml inválido — regenerate abortado` | Step com campos inválidos ou tipo desconhecido | Corrigir `flow.yaml` antes de rodar `regenerate` |
| `SOUL.md não encontrado — placeholder inserido` | Warning (não erro) | Revisar `prompt.md` gerado antes do deploy |
| Agente chamando MCP diretamente sem proxy | `framework != native`, proxy não iniciado | Iniciar `plughub-sdk proxy` antes do agente |
