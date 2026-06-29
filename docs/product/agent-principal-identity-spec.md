# Agent Principal — identidade de máquina para agentes IA (internos e externos)

> Spec fechada (2026-06-28). Ideia (b): dar aos agentes IA — nativos (SDK) e externos (LangGraph/CrewAI) —
> uma **identidade de máquina de primeira classe** para se autenticar, com escopo por capacidade, **distinta
> das roles humanas** (operator/supervisor/admin/developer/business). Não é "mais uma role"; é um tipo de
> principal diferente.

## 1. Estado atual (o que já existe)

- **auth-api** (`jwt_utils.create_access_token`) emite JWT HS256 **para usuários humanos**: claims `sub`
  (user UUID), `roles`, `module_config` (ABAC), `accessible_pools`, `supervised_*`. Não há conceito de
  principal de agente.
- **Agentes hoje** autenticam chamadas MCP com um **`session_token` (JWT) por sessão**, provido pelo
  Routing Engine, decodificado **localmente** (sem rede) pelo `McpInterceptor` (nativo, in-process) ou pelo
  **proxy sidecar** (externo, `localhost:7422`). O token carrega `permissions[]` (`server:tool`) usado para
  filtrar a tool list e validar cada chamada.
- **Capability já é declarada por `agent_type`** no agent-registry (`agent.yaml`): `permissions: [server:tool]`
  + `pools: [...]`. É a fonte de verdade do que o agente pode chamar.
- **Service-token** (G-PROBE, recente) = autenticação **inter-serviço** (segredo compartilhado HS256), não
  por-agente. Ortogonal a esta spec.

**Lacuna**: não existe (1) uma **identidade persistente** do agente (um `principal` auditável e credenciável),
nem (2) um caminho de **autenticação para agentes externos** que se conectam de fora (hoje o token é
sempre cunhado pela plataforma por sessão; um agente externo self-hosted não tem como obter credencial).

## 2. Decisão: principal de máquina, não role humana

Agente é um **principal de serviço** (machine identity), não um papel RBAC humano. Razões:
- escopo de agente é por **capacidade** (quais MCP tools, quais pools), não por módulo de UI;
- auditoria LGPD deve atribuir ações à **identidade do próprio agente** (o `mcp.audit` já carrega `source`);
- não poluir o enum de roles humanas nem o `module_config` (que é superfície de UI, irrelevante para agente).

Introduz-se um discriminador de **tipo de sujeito**: `subject_type: "human" | "agent" | "service"`. Humanos
seguem idênticos; agentes ganham um ramo próprio; `service` cobre o service-token já existente (rótulo, sem
mudança de comportamento).

## 3. Modelo

### 3.1 Identidade (auth-api, schema `auth`)
Nova tabela `agent_principals`:
- `agent_principal_id` (UUID, `sub` do JWT), `tenant_id`,
- `agent_type_id` (FK lógica ao agent-registry — a capability vive lá, não duplicar),
- `origin: native | external` (nativo = cunhado pela plataforma; external = self-hosted via client-credentials),
- `display_name`, `active`, `created_at`, `last_authenticated_at`,
- **para `external`**: `credential_hash` (SHA-256 de um secret opaco — mesmo padrão do refresh token),
  `credential_rotated_at`. Nativos **não** têm secret (são cunhados server-side).

> Capability (`permissions[]`, `pools[]`) **não** é copiada para cá — é resolvida do `agent_type` no registry
> no momento da emissão (denormalizada no token, como o login humano já faz com `supervised_*`).

### 3.2 Claims do JWT de agente
`create_agent_token(...)` (novo, espelha `create_access_token`):
```
sub            — agent_principal_id
subject_type   — "agent"
tenant_id      — tenant
agent_type_id  — tipo (rótulo + origem da capability)
origin         — "native" | "external"
permissions    — [server:tool] resolvidas do agent_type (MESMO campo que o proxy/interceptor já lê)
accessible_pools — pools[] do agent_type (reusa o filtro já existente)
session_id     — (opcional) presente quando cunhado por sessão (nativo)
iat / exp      — TTL curto (≤ 15 min nativo; ≤ 60 min external), HS256 jwt_secret
```
**Sem `roles`, sem `module_config`** (não se aplicam a agente). O `McpInterceptor`/proxy **não mudam** — já
leem `permissions[]`; ganham só o registro de `sub`/`agent_type_id` no `AuditRecord`.

### 3.3 Dois fluxos de emissão
- **Nativo / interno** (SDK, spawned pela plataforma): o **orchestrator-bridge/Routing Engine** cunha o token
  por sessão (como hoje), mas agora **referenciando o `agent_principal`** (cria/resolve o principal por
  `agent_type_id`+tenant na primeira vez) e incluindo `subject_type:"agent"` + `agent_type_id`. Transparente
  para o agente.
- **Externo / self-hosted** (LangGraph/CrewAI rodando fora): **client-credentials** —
  `POST /v1/agent/token` com `agent_principal_id` + `secret` → JWT curto com a capability do `agent_type`.
  O proxy sidecar usa esse token. Rotação de secret via `POST /v1/agent/{id}/rotate-credential` (admin).

### 3.4 Auditoria (LGPD)
`AuditRecord` (`mcp.audit`) ganha `principal_id` (= `sub`) e `subject_type` além do `source` já existente.
Toda chamada MCP passa a ser atribuível à identidade do agente, não só ao componente. (O audit já é
inescapável por política de tool — sem opt-out.)

### 3.5 Provisionamento (pré-cadastro) e superfície de UI

**Pré-cadastro exigido por tipo:**

| Tipo | Cadastro prévio | Âncora de confiança | Quem emite o token | UI nova exigida |
|---|---|---|---|---|
| Humano | sim, explícito (já existe) | usuário no DB `auth` | auth-api (login) | — (já existe) |
| Agente **nativo** | **não** — auto-provisionado (seed-if-absent) a partir do `agent_type` registrado | `agent_type` no registry + plataforma spawna | plataforma cunha por sessão | **nenhuma** |
| Agente **externo** | **sim** — `agent_principal` + secret emitido por admin | secret provisionado | auth-api (client-credentials) | pequena UI de credencial (ou API/CLI no F2) |

- **Nativo**: o bridge cria o principal no primeiro uso (`seed-if-absent`, coerente com a precedência de
  provisionamento do `CLAUDE.md`); nenhuma ação manual. O token é server-side.
- **Externo**: precisa existir um `agent_principal(origin=external)` **com secret** antes do
  `POST /v1/agent/token` — não se autentica identidade externa desconhecida. O cadastro dá **identidade +
  credencial**, nunca capability nova (capability é sempre do `agent_type`).

**Superfície de UI — escopo mínimo vs. opcional:**
- **Mínimo (F1/F2)**: nativo não exige UI; externo pode ser provisionado por **API/CLI de admin** primeiro
  (`POST /v1/agent`, `rotate-credential`). Nada de UI no caminho crítico.
- **F3 (UI enxuta)**: aba "Agentes" em `Config/Access` — lista de principals, **criar agente externo** +
  **emitir/rotacionar secret**, e **capability read-only** (vinda do registry). É só identidade/credencial.
- **Gap pré-existente e ORTOGONAL**: hoje **não há UI para `permissions[]` (MCP tools)** — só pool tem tela;
  tools são YAML no `agent_type`. Este mecanismo **não depende** disso (lê do registry). Um **editor de tools
  por agente** é melhoria **opcional** (fecha a dívida do invariante "todo campo de config é editável na UI"),
  pode acoplar à aba "Agentes" ou à de Recursos, mas **fora do escopo mínimo** desta spec.

## 4. Fronteira MCP — o que NÃO muda
- `McpInterceptor` (in-process) e proxy sidecar continuam validando `permissions[]` **localmente** (<1ms),
  sem chamar a auth-api por requisição.
- Invariantes preservados: agentes nunca acessam backend direto; toda chamada MCP é interceptada
  (permission + injection guard + audit); tool list nunca vai ao LLM sem o filtro de `permissions[]`.
- A auth-api entra **só na emissão/rotação** de credencial (fora do hot-path), não na validação por chamada.

## 5. Fases
- **F1 — Principal + claims (nativo)**: tabela `agent_principals`, `create_agent_token`, `subject_type`,
  bridge resolve/cunha o principal por sessão. Audit ganha `principal_id`/`subject_type`. Sem fluxo externo.
- **F2 — Client-credentials (externo)**: `POST /v1/agent/token` + secret + rotação; proxy sidecar usa.
- **F3 — UI**: listagem de agent principals em `Config/Access` (aba separada "Agentes"), read-only de
  capability (vem do registry) + emissão/rotação de secret para externos. ABAC `config.agents` (novo campo).
- **F4 — Revogação**: `active=false` derruba emissão; TTL curto faz o token expirar (sem revogação online no
  hot-path, por design — coerente com a validação local).

## 6. Invariantes
- Agente é `subject_type:"agent"` — **nunca** entra no enum de roles humanas nem recebe `module_config`.
- Capability (`permissions[]`/`pools[]`) é **fonte única no agent-registry** (`agent_type`); a auth-api
  **resolve e denormaliza** no token, não duplica.
- Validação por chamada permanece **local** no interceptor/proxy; auth-api só emite/rota.
- `mcp.audit` atribui toda ação à identidade do agente (`principal_id`); sem opt-out.
- Inglês no código (`agent_principal`, `subject_type`, `origin`); PT só em i18n.

## 7. Fora de escopo
- Mudar o modelo de capability (continua declarativo no `agent_type`/registry).
- OAuth/OIDC externo completo (client-credentials HS256 interno basta; federar é arco futuro).
- Identidade humana (inalterada).
- Service-token inter-serviço (só ganha o rótulo `subject_type:"service"`, sem mudança).
