# @plughub/gitagent

Implementação do padrão **GitAgent PlugHub** — repositório Git como fonte de verdade do agente.

## Instalar

```bash
npm install @plughub/gitagent
```

## Usar

```typescript
import { GitAgentParser, GitAgentImporter } from "@plughub/gitagent"

// Parsear repositório local
const parser = new GitAgentParser()
const parsed = parser.parse("./my-agent")
console.log(parsed.manifest.agent_type_id)
console.log(parsed.flows["main"].steps.length)

// Importar para o Agent Registry
const importer = new GitAgentImporter()
const result   = await importer.import({
  localPath:   "./my-agent",
  tenantId:    "tenant_telco",
  registryUrl: "http://localhost:3300",
  apiKey:      process.env.PLUGHUB_API_KEY!,
})
console.log(result.certification_status) // "passed"
```

## CLI

```bash
# Importar repositório local
PLUGHUB_TENANT_ID=tenant_telco \
PLUGHUB_REGISTRY_URL=http://localhost:3300 \
plughub-sdk import ./my-agent

# Importar repositório remoto
plughub-sdk import https://github.com/empresa/agente-retencao
```

## Fixtures

O diretório `fixtures/my-agent/` contém um repositório GitAgent completo de exemplo,
com agent.yaml, instructions.md, flows/main.yaml e .plughub/config.yaml.

## Testes

```bash
npm test
```
