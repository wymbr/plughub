# Módulo: sdk (@plughub/sdk)

> Pacote: `sdk` (biblioteca + CLI)
> Runtime: TypeScript / Node 20+ (TypeScript) | Python 3.11+ (pacote paralelo)
> Spec de referência: seções 4.6a–4.6k

## O que é

O `@plughub/sdk` é o kit de integração que qualquer agente usa para participar de um pool do PlugHub. Ele implementa as 9 responsabilidades da spec seção 4.6a:

1. **Ciclo de vida** — `agent_login → agent_ready → agent_busy → agent_done`
2. **Adaptação de contexto** — `PlugHubAdapter.fromPlatform()` / `.toPlatform()`
3. **Drivers de ambiente** — tradução para LLMs e orquestradores específicos
4. **Interceptação MCP** — PlugHubAdapter in-process (nativo) ou proxy sidecar (externo)
5. **Certificação** — `plughub-sdk certify` valida o contrato sem depender do ambiente
6. **Observabilidade** — propagação de `plughub.session_id` como trace ID raiz
7. **Portabilidade** — `plughub-sdk verify-portability` verifica que o agente funciona fora da plataforma
8. **Regeneração** — `plughub-sdk regenerate` converte agente proprietário em artefatos nativos
9. **Proxy sidecar** — `plughub-sdk proxy` intercepta chamadas MCP de agentes externos

---

## O que NÃO é

- Não é um framework de agente — não define como o agente raciocina
- Não é um MCP Server — não expõe tools ao BPM
- Não contém lógica de negócio — apenas o contrato de execução

---

## Estrutura do Pacote

```
sdk/src/
  index.ts            ← API pública — exports nomeados explícitos
  agent.ts            ← definePlugHubAgent() — wrapper principal
  adapter.ts          ← PlugHubAdapter, drivers (GenericMCPDriver, BedrockDriver, etc.)
  lifecycle.ts        ← LifecycleManager — login/ready/busy/done via MCP
  certify.ts          ← certifyAgent() — certificação do contrato
  portability.ts      ← verifyPortability() — verificação de dependências implícitas
  observability.ts    ← ObservabilityManager — propagação de trace
  proxy/
    server.ts         ← createProxySidecar() — HTTP proxy sidecar
    config.ts         ← carrega e valida proxy_config.yaml
    circuit-breaker.ts← timeout + fallback mode error_clear
  regenerate/
    convert.ts        ← convertGitAgent() — converte GitAgent → artefatos nativos
    reader.ts         ← lê artefatos de diretório GitAgent
  certify/
    dir.ts, flow.ts, lifecycle.ts, manifest.ts, yaml.ts ← checks individuais
  cli/
    index.ts          ← entrypoint CLI (plughub-sdk)
    certify.ts        ← plughub-sdk certify
    regenerate.ts     ← plughub-sdk regenerate
    verify.ts         ← plughub-sdk verify-portability
    proxy.ts          ← plughub-sdk proxy
    skill-extract.ts  ← plughub-sdk skill-extract
  drivers/
    generic-mcp.ts
    bedrock.ts
    copilot.ts
    agent-builder.ts
```

---

## Dois caminhos de integração

| Tipo de agente | Integração | Interceptação MCP |
|---|---|---|
| **Nativo** (usa SDK) | `definePlugHubAgent()` | PlugHubAdapter in-process — sem hop de rede |
| **Externo** (LangGraph, CrewAI, etc.) | `plughub-sdk proxy` como sidecar | Proxy sidecar em localhost:7422 — loopback only |
| **GitAgent** (output de `regenerate`) | Código gerado com SDK embutido | PlugHubAdapter in-process |

> **GitAgents** são agentes cujo código-fonte vive em um repositório Git e seguem uma convenção de artefatos (`agent.yaml`, `SOUL.md`, `DUTIES.md`, `flow.yaml`) que o SDK sabe ler e converter. O ciclo de vida completo — da estrutura do repositório ao deploy — está documentado em [`docs/guias/gitagent.md`](../guias/gitagent.md).

---

## Criando um Agente Nativo

Um agente nativo é escrito com o SDK. O ciclo de vida, adaptação e interceptação MCP são gerenciados automaticamente.

### Passo 1 — Instalar o SDK

```bash
npm install @plughub/sdk
```

### Passo 2 — Criar o `PlugHubAdapter`

O adapter define o mapeamento entre o `context_package` da plataforma e o schema interno do agente. Deve ser instanciado uma vez e reutilizado.

```typescript
import { PlugHubAdapter } from "@plughub/sdk"

const adapter = new PlugHubAdapter({
  // context_map: chave = campo no context_package, valor = campo no agente
  context_map: {
    "customer_data.customer_id": "cliente.id",
    "customer_data.tier":        "cliente.segmento",
    "conversation_history":      "historico",
  },

  // result_map: chave = campo no agent_done, valor = campo no resultado do agente
  // "outcome" e "issue_status" são OBRIGATÓRIOS — validados no construtor
  result_map: {
    "outcome":      "status_resolucao",
    "issue_status": "problemas_tratados",
  },

  // outcome_map: traduz outcomes semânticos do agente para valores da plataforma
  outcome_map: {
    "resolvido":   "resolved",
    "escalado":    "escalated_human",
    "transferido": "transferred",
  },
})
```

> O construtor do `PlugHubAdapter` valida imediatamente que `outcome` e `issue_status` estão presentes no `result_map`. Erros de configuração falham na inicialização — nunca em tempo de execução.

### Passo 3 — Implementar o Handler

O handler recebe apenas o contexto já mapeado pelo adapter — nunca o `context_package` interno da plataforma diretamente.

```typescript
import type { AgentHandler } from "@plughub/sdk"

const handler: AgentHandler = async ({ context, session_id, turn_number }) => {
  // context já está no schema do agente (resultado de adapter.fromPlatform)
  const cliente = context["cliente"] as { id: string; segmento: string }

  // Lógica do agente aqui — acessa domain MCP Servers via tools autorizados
  const resultado = await resolverProblema(cliente)

  return {
    result: {
      status_resolucao: "resolvido",
      problemas_tratados: resultado.issues,
    },
    issues: resultado.issues,
    // handoff_reason obrigatório apenas quando outcome !== "resolved"
  }
}
```

### Passo 4 — Definir e iniciar o agente

```typescript
import { definePlugHubAgent } from "@plughub/sdk"

const agente = definePlugHubAgent({
  agent_type_id: "agente_suporte_v1",
  pools:         ["suporte_standard"],
  server_url:    process.env.MCP_SERVER_URL!,
  adapter,
  handler,
  on_error: (error, session_id) => {
    console.error(`Erro na sessão ${session_id}:`, error)
  },
})

await agente.start()
// O agente está registrado e na fila (login → ready)
// Quando o Routing Engine aloca uma conversa, handleConversation() é chamado automaticamente
```

### O que o SDK gerencia automaticamente

```
agente.start()
  ├── lifecycle.login()       → agent_login no mcp-server → session_token + instance_id
  └── lifecycle.ready()       → agent_ready → entra na fila do Routing Engine

handleConversation(rawContextPackage)   [chamado pela plataforma]
  ├── ContextPackageSchema.parse()     → valida context_package recebido
  ├── observability.startTurn()        → inicia span de tracing
  ├── lifecycle.busy()                 → agent_busy (session_id + customer_id)
  ├── adapter.fromPlatform(pkg)        → mapeia para schema do agente
  ├── handler({ context, session_id, turn_number })
  ├── adapter.toPlatform(result)       → mapeia resultado para contrato
  ├── AgentDoneSchema.parse()          → valida agent_done
  ├── lifecycle.done()                 → agent_done
  └── observability.endTurn()          → encerra span

  [em caso de erro no handler]
  └── lifecycle.done({ outcome: "escalated_human", handoff_reason: "sdk_handler_error" })
       → best-effort, erro não bloqueia o shutdown

agente.stop()
  └── lifecycle.logout()              → graceful shutdown (SIGTERM hook registrado no start)
```

O **token JWT** é renovado automaticamente 1 minuto antes de expirar via re-login silencioso.

---

## Portando um Agente Externo

Agentes externos (LangGraph, CrewAI, frameworks proprietários) não usam o SDK diretamente. O mecanismo de integração é o **proxy sidecar** — um processo separado que intercepta todas as chamadas a domain MCP Servers sem exigir modificação no código do agente.

### Por que o proxy é necessário

Agentes externos chamam domain MCP Servers diretamente, bypassando validação de permissões e audit log. O proxy sidecar resolve isso garantindo que nenhuma chamada MCP chegue a um domain server sem validação e registro de auditoria.

### Passo 1 — Preparar o manifesto `agent.yaml`

```yaml
agent_type_id: agente_suporte_langgraph_v1
framework: langgraph          # ou: crewai, langchain, proprietary, etc.
execution_model: stateless
pools:
  - suporte_standard
permissions:
  - mcp-server-crm:customer_get
  - mcp-server-crm:ticket_create
  - mcp-server-telco:contract_get
version: "1.0.0"
description: Agente de suporte usando LangGraph
```

Arquivos opcionais no mesmo diretório:

| Arquivo | Descrição |
|---|---|
| `SOUL.md` | Identidade e persona do agente (Camada 1 do prompt gerado) |
| `DUTIES.md` | Políticas e limites de atuação (Camada 2 do prompt gerado) |
| `flow.yaml` | Skill Flow declarativo — se o agente orquestra um fluxo |

### Passo 2 — Executar `plughub-sdk regenerate`

```bash
plughub-sdk regenerate --dir ./meu-agente --output ./output
```

**Artefatos gerados em `./output/`:**

| Arquivo | Quando gerado | Conteúdo |
|---|---|---|
| `agent-type.json` | Sempre | Registro do tipo de agente na plataforma |
| `prompt.md` | Sempre | Prompt em 2 camadas (SOUL + DUTIES) |
| `skill-ref.json` | Quando `SKILL.md` existir | Referência ao Skill Flow |
| `flow.json` | Quando `flow.yaml` existir e for válido | Skill Flow convertido para formato nativo |
| `proxy_config.yaml` | Quando `permissions[]` declarar MCP Servers | Configuração do proxy sidecar |

> Se `flow.yaml` existir mas for inválido, o `regenerate` **aborta** com mensagem de erro indicando o step problemático — nunca gera artefatos parciais.

### Passo 3 — Configurar e iniciar o proxy sidecar

O `proxy_config.yaml` é gerado pelo `regenerate` automaticamente:

```yaml
port: 7422
session_token_env: PLUGHUB_SESSION_TOKEN
audit_buffer_size: 1000
audit_flush_interval_ms: 500
circuit_breaker:
  timeout_ms: 50
  mode_on_failure: error_clear   # retorna erro limpo; nunca passa silenciosamente
routes:
  mcp-server-crm:   ${MCP_CRM_URL}
  mcp-server-telco: ${MCP_TELCO_URL}
```

As variáveis de ambiente `MCP_*_URL` são derivadas dos nomes dos MCP Servers:
- `mcp-server-crm` → `MCP_CRM_URL`
- `mcp-server-telco` → `MCP_TELCO_URL`

```bash
# Iniciar proxy sidecar
export PLUGHUB_SESSION_TOKEN=<jwt_da_sessao>
export MCP_CRM_URL=http://mcp-server-crm:3200
export MCP_TELCO_URL=http://mcp-server-telco:3201

plughub-sdk proxy --config ./output/proxy_config.yaml
# [plughub-sdk proxy] listening on localhost:7422
# [plughub-sdk proxy] routes: mcp-server-crm, mcp-server-telco
```

### Passo 4 — Configurar o agente externo para usar o proxy

O agente externo aponta suas chamadas MCP para `localhost:7422/{nome-do-server}/{path}`:

```
# Antes (direto para o servidor):
http://mcp-server-crm:3200/tools/customer_get

# Depois (via proxy):
http://localhost:7422/mcp-server-crm/tools/customer_get
```

A mudança é apenas de URL base — o formato das chamadas MCP permanece idêntico.

### Como o proxy funciona por chamada MCP

```
Agente externo → POST localhost:7422/mcp-server-crm/tools/customer_get
  │
  ├─ 1. Lê JWT do env PLUGHUB_SESSION_TOKEN
  ├─ 2. Decodifica permissions[] do payload JWT — local, ~0.1ms, zero rede
  ├─ 3. Verifica se "mcp-server-crm:*" está em permissions[]
  │     └─ Não permitido → 403 { error: "permission_denied" } + AuditEvent → fim
  ├─ 4. Encaminha para routes["mcp-server-crm"]/tools/customer_get
  ├─ 5. Push AuditEvent no buffer in-memory (~0ms, não bloqueia)
  │     └─ Background timer drena → Kafka (flush a cada 500ms, buffer até 1000 eventos)
  └─ 6. Retorna resposta do upstream ao agente
  Overhead total: < 1ms
```

**Circuit breaker:** se o upstream não responde em `timeout_ms` (padrão 50ms), o proxy retorna `502 { error: "circuit_breaker_open" }`. O modo `error_clear` garante que a falha é sempre visível ao agente — nunca passa silenciosamente.

---

## `PlugHubAdapter` — Mapeamento Bidirecional (spec 4.6d)

O adapter opera nas duas direções com o mesmo objeto de configuração:

| Direção | Método | Quando é chamado |
|---|---|---|
| Entrada (plataforma → agente) | `adapter.fromPlatform(pkg)` | Antes de passar contexto ao handler |
| Saída (agente → plataforma) | `adapter.toPlatform(result)` | Após o handler retornar |

**Resolução JSONPath**: valores com prefixo `$.` são resolvidos como JSONPath simples sobre o objeto fonte. Valores literais são passados diretamente.

**Campos obrigatórios no `result_map`**: `outcome` e `issue_status`. Ausência causa `Error` no construtor — nunca em runtime.

### Drivers disponíveis

| Driver | Destino |
|---|---|
| `GenericMCPDriver` | MCP Servers genéricos |
| `BedrockDriver` | Amazon Bedrock |
| `CopilotDriver` | GitHub Copilot / Azure OpenAI |
| `AgentBuilderDriver` | Agent Builder environments |

---

## Certificação — `plughub-sdk certify` (spec 4.6e)

Gate obrigatório no pipeline CI/CD. Valida o contrato de execução **sem** precisar do ambiente da plataforma.

```bash
plughub-sdk certify --dir ./meu-agente
```

| Check | O que valida |
|---|---|
| `adapter.required_fields` | `outcome` e `issue_status` mapeados no `result_map` |
| `handler.executes_without_error` | Handler executa com `context_package` mínimo sintético |
| `handler.produces_valid_agent_done` | Resultado produz `AgentDone` válido conforme spec 4.2 |
| `registration.pools_declared` | Pelo menos um pool declarado |
| `contract.issue_status_not_empty` | `issue_status` presente e não vazio |

Qualquer check `failed` → exit code ≠ 0 → bloqueia deploy no CI.

---

## Verificação de Portabilidade — `plughub-sdk verify-portability` (spec 4.6h)

Análise estática para detectar dependências implícitas da plataforma.

```bash
plughub-sdk verify-portability --dir ./meu-agente
```

| Check | O que detecta |
|---|---|
| `no_direct_platform_schema_import` | Imports de `@plughub/schemas`, `context_package`, `pipeline_state` |
| `no_internal_url_hardcoded` | URLs internas como `plughub.internal`, `.plughub.svc` |
| `adapter_declared` | Presença de `PlugHubAdapter` ou `definePlugHubAgent` (warning se ausente) |
| `no_direct_infra_access` | Acesso direto a Redis (`ioredis`) ou Kafka (`kafkajs`) |
| `no_runtime_lock_dependencies` | Dependência de `plughub-runtime` ou `@plughub/core` |

> **Complementaridade com `certify`:**
> - `certify` → verifica se o agente funciona **dentro** da plataforma
> - `verify-portability` → verifica se funciona **fora** dela

---

## Observabilidade (spec 4.6f)

Propaga `plughub.session_id` como trace ID raiz para sistemas de observabilidade do agente (OpenTelemetry, LangSmith, etc.):

```typescript
import { observability } from "@plughub/sdk"
import { trace } from "@opentelemetry/api"

observability.useBackend({
  startSpan: (name, parentTraceId) =>
    trace.getTracer("meu-agente").startSpan(name, {
      root: true,
      attributes: { "trace.id": parentTraceId }
    })
})
```

Atributos propagados automaticamente (prefixo `plughub.*`):

```
plughub.session_id          ← correlação principal com a plataforma
plughub.tenant_id
plughub.agent_type_id
plughub.pool
plughub.turn_number
plughub.parent_session_id   (presente em delegações A2A)
```

O SDK também expõe `observability.toHeaders()` para propagar contexto em chamadas downstream via headers HTTP (`x-plughub-session-id`, `x-plughub-tenant-id`, `x-plughub-agent-type-id`).

Sem backend configurado, o SDK opera normalmente — a observabilidade é opt-in.

---

## CLI `plughub-sdk` — Referência de Comandos

```bash
plughub-sdk certify            # valida contrato de execução (gate CI/CD)
plughub-sdk verify-portability # verifica dependências implícitas da plataforma
plughub-sdk regenerate         # converte agente proprietário em artefatos nativos
plughub-sdk skill-extract      # extrai skill de agente existente
plughub-sdk proxy              # inicia proxy sidecar para agentes externos
```

---

## Invariantes

- `PlugHubAdapter` é a única interface de adaptação — nunca criar adaptação inline
- Drivers são separados do adapter — o adapter é agnóstico de ambiente
- O ciclo de vida (login/ready/busy/done) é gerenciado automaticamente — o agente nunca chama `agent_done` diretamente
- `session_id` e `tenant_id` nunca são expostos diretamente ao código do agente
- Agentes externos **obrigatoriamente** roteiam chamadas MCP via proxy sidecar — nunca diretamente
- Validação de permissões usa apenas a assinatura local do JWT — zero chamadas de rede por MCP call
- Audit writes são sempre assíncronas — o proxy nunca bloqueia uma chamada MCP aguardando ack de audit

---

## Dependências

```
@plughub/sdk
  ├── @plughub/schemas            ← source of truth para contracts
  ├── @modelcontextprotocol/sdk   ← comunicação MCP com mcp-server-plughub
  ├── zod                         ← validação de schemas
  └── commander                   ← CLI
```

---

## Relação com Outros Módulos

```
sdk
  ├── chama → mcp-server-plughub  (lifecycle tools: agent_login/ready/busy/done)
  ├── intercepta → domain MCP Servers  (via PlugHubAdapter in-process ou proxy sidecar)
  ├── lê → @plughub/schemas       (ContextPackageSchema, AgentDoneSchema, OutcomeSchema)
  └── é usado por → qualquer agente nativo ou externo que participa de um pool PlugHub
```
