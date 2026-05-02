# @plughub/sdk

SDK de Integração da **PlugHub Platform** — conecta qualquer agente ao pool sem reescrita.
Implementa as 9 responsabilidades da spec seção 4.6a.

## Instalação

```bash
npm install @plughub/sdk
```

## Uso básico

```typescript
import { definePlugHubAgent, PlugHubAdapter } from "@plughub/sdk"

// 1. Declarar o mapeamento de contexto
const adapter = new PlugHubAdapter({
  context_map: {
    "customer_data.tier":     "cliente.tier",
    "customer_data.churn_risk": "cliente.churn_score",
    "conversation_history":   "historico",
  },
  result_map: {
    "outcome":      "status_resolucao",
    "issue_status": "issues",
  },
  outcome_map: {
    "resolvido":  "resolved",
    "escalar":    "escalated_human",
  },
})

// 2. Definir o agente
const agente = definePlugHubAgent({
  agent_type_id: "agente_retencao_v1",
  pools:         ["retencao_humano"],
  server_url:    process.env.PLUGHUB_SERVER_URL!,
  adapter,
  handler: async ({ context, session_id }) => {
    // context está no schema do agente — não no formato interno da plataforma
    const tier = context.cliente?.tier
    // ... lógica do agente ...
    return {
      result: { status_resolucao: "resolvido" },
      issues: [{ issue_id: "1", description: "Retenção concluída", status: "resolved" }],
    }
  },
})

// 3. Iniciar
await agente.start()
```

## CLI

```bash
# Certificar agente antes do deploy
plughub-sdk certify --agent ./meu-agente.ts --pools retencao_humano

# Verificar portabilidade
plughub-sdk verify-portability --source ./meu-agente.ts

# Regenerar agente proprietário como nativo
plughub-sdk regenerate --source ./copilot-export/

# Extrair skill de agente existente
plughub-sdk skill-extract ./meu-agente.ts
```

## Drivers disponíveis

| Driver | Uso |
|---|---|
| `GenericMCPDriver` | Qualquer sistema MCP (padrão) |
| `BedrockDriver` | AWS Bedrock Agents |
| `AgentBuilderDriver` | Google Agent Builder (Vertex AI) |
| `CopilotDriver` | Microsoft Copilot Studio (Direct Line) |

## Spec de referência

- 4.6a — visão geral das 9 responsabilidades
- 4.6d — PlugHubAdapter
- 4.6e — certificação
- 4.6h — portabilidade nativa
