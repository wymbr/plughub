# Arc 7 — Autenticação Real, Permissões e Roteamento por Performance

## Arc 7a — auth-api (✅ implementado)

Usuários reais, JWT HS256, session lifecycle com refresh token rotation.
Substitui o modelo de `x-tenant-id`/`x-user-id` como headers livres.

**Pacote:** `packages/auth-api/` — Python FastAPI, porta 3200.

### PostgreSQL schema (schema `auth`)

```sql
CREATE TABLE auth.users (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        TEXT NOT NULL,
    email            TEXT NOT NULL,
    name             TEXT NOT NULL DEFAULT '',
    password_hash    TEXT NOT NULL,   -- bcrypt rounds=12
    roles            TEXT[] NOT NULL DEFAULT '{}',
    accessible_pools TEXT[] NOT NULL DEFAULT '{}',  -- [] = todos os pools
    active           BOOL NOT NULL DEFAULT TRUE,
    created_at, updated_at TIMESTAMPTZ,
    UNIQUE (tenant_id, email)
);

CREATE TABLE auth.sessions (
    id                 UUID PRIMARY KEY,
    user_id            UUID REFERENCES auth.users(id) ON DELETE CASCADE,
    tenant_id          TEXT NOT NULL,
    refresh_token_hash TEXT NOT NULL UNIQUE,  -- SHA-256(plain_token)
    expires_at         TIMESTAMPTZ NOT NULL,
    last_used_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### JWT claims (access token — HS256, TTL 1h)

```json
{
  "sub":              "user-uuid",
  "tenant_id":        "tenant_demo",
  "email":            "user@example.com",
  "name":             "User Name",
  "roles":            ["operator", "supervisor"],
  "accessible_pools": ["retencao_humano", "sac"],
  "module_config":    {
    "evaluation": {
      "contestar":   { "access": "none", "scope": [] },
      "revisar":     { "access": "read_write", "scope": ["pool:retencao_humano"] }
    }
  },
  "exp": ..., "iat": ...
}
```

`accessible_pools: []` significa acesso a todos os pools (usuário admin/developer).

### Refresh token

Token opaco de 43 chars URL-safe (~258 bits de entropia). Armazenado como SHA-256 em `auth.sessions` — plain token nunca persisted. Rotation automática em cada `POST /auth/refresh` (novo par emitido, hash antigo substituído atomicamente).

### Endpoints

| Método | Rota | Auth | Descrição |
|---|---|---|---|
| `POST` | `/auth/login` | — | Login email+senha → access_token + refresh_token |
| `POST` | `/auth/refresh` | body refresh_token | Rotation → novo par |
| `POST` | `/auth/logout` | body refresh_token | Invalida refresh_token (idempotente) |
| `GET` | `/auth/me` | Bearer | Claims do access token |
| `GET` | `/auth/users` | X-Admin-Token | Lista usuários do tenant |
| `POST` | `/auth/users` | X-Admin-Token | Cria usuário |
| `GET` | `/auth/users/{id}` | X-Admin-Token | Detalhe do usuário |
| `PATCH` | `/auth/users/{id}` | X-Admin-Token | Atualiza usuário (name, password, roles, accessible_pools, active) |
| `DELETE` | `/auth/users/{id}` | X-Admin-Token | Remove usuário |
| `GET` | `/health` | — | Healthcheck |

### Seed automático

Ao iniciar, `seed_admin_if_absent()` cria o usuário admin configurado via env vars se não existir. Idempotente — sem erro em re-inicializações.

### Variáveis de ambiente (prefixo `PLUGHUB_AUTH_`)

| Var | Default | Descrição |
|---|---|---|
| `DATABASE_URL` | `postgresql://plughub:plughub@postgres:5432/plughub` | DSN PostgreSQL |
| `JWT_SECRET` | `changeme_auth_jwt_secret_at_least_32_chars` | Segredo HS256 |
| `JWT_ALGORITHM` | `HS256` | Algoritmo JWT |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | TTL do access token |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `7` | TTL do refresh token |
| `ADMIN_TOKEN` | `""` | Token admin (vazio = sem auth em dev) |
| `SEED_ADMIN_EMAIL` | `admin@plughub.local` | Email do admin seed |
| `SEED_ADMIN_PASSWORD` | `changeme_admin` | Senha do admin seed |
| `SEED_TENANT_ID` | `tenant_demo` | Tenant do admin seed |

### Arquivos

| Arquivo | Responsabilidade |
|---|---|
| `config.py` | Settings com prefixo `PLUGHUB_AUTH_` |
| `password.py` | `hash_password()`, `verify_password()` — bcrypt rounds=12 |
| `jwt_utils.py` | `create_access_token()`, `decode_access_token()`, `generate_refresh_token()`, `hash_refresh_token()` |
| `models.py` | Pydantic: LoginRequest, RefreshRequest, LogoutRequest, CreateUserRequest, UpdateUserRequest, TokenResponse, UserResponse, MeResponse |
| `db.py` | DDL + CRUD asyncpg: `ensure_schema`, `create_user`, `get_user_by_email`, `get_user_by_id`, `list_users`, `update_user`, `delete_user`, `create_session`, `get_session_by_token_hash`, `rotate_session`, `delete_session`, `seed_admin_if_absent` |
| `router.py` | FastAPI routes — login/refresh/logout/me + CRUD admin |
| `main.py` | FastAPI app + lifespan asyncpg pool + seed |
| `tests/test_router.py` | **58/58 testes** — TestHealth, TestLogin (4), TestRefresh (3), TestLogout (2), TestMe (3), TestCreateUser (3), TestListUsers (1), TestGetUser (2), TestUpdateUser (2), TestDeleteUser (2), TestSeedAdmin (2), TestPasswordUtils (3), TestJwtUtils (3), TestHashRefreshToken (3), TestGrantPermission (3), TestListPermissions (2), TestRevokePermission (2), TestResolvePermission (3), TestTemplates (6), TestApplyTemplate (2), TestResolvePermissionsLogic (6) |

## Arc 7b — platform_permissions (✅ implementado)

Generaliza `evaluation_permissions` para todo o sistema. Implementado em `packages/auth-api/`.

### PostgreSQL schema (schema `auth`)

```sql
-- Permissão explícita: uma linha por (user_id, module, action, scope_type, scope_id)
CREATE TABLE auth.platform_permissions (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   TEXT        NOT NULL,
    user_id     TEXT        NOT NULL,
    module      TEXT        NOT NULL,   -- analytics | evaluation | billing | config | registry | skill_flows | campaigns | workflows | *
    action      TEXT        NOT NULL,   -- view | edit | admin | *
    scope_type  TEXT        NOT NULL CHECK (scope_type IN ('pool', 'global')),
    scope_id    TEXT,                   -- pool_id for scope_type='pool'; NULL for global
    granted_by  TEXT        NOT NULL DEFAULT 'system',
    template_id UUID,                   -- FK para permission_templates (auditoria)
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, user_id, module, action, scope_type, COALESCE(scope_id, ''))
);

-- Template nomeado de permissões (conjunto reutilizável)
CREATE TABLE auth.permission_templates (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   TEXT        NOT NULL,
    name        TEXT        NOT NULL,
    description TEXT        NOT NULL DEFAULT '',
    permissions JSONB       NOT NULL DEFAULT '[]',   -- list[{module, action, scope_type, scope_id}]
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, name)
);
```

### Domínios válidos

| Campo | Valores |
|---|---|
| `module` | `analytics`, `evaluation`, `billing`, `config`, `registry`, `skill_flows`, `campaigns`, `workflows`, `*` |
| `action` | `view`, `edit`, `admin`, `*` |
| `scope_type` | `pool` (scope_id = pool_id), `global` (scope_id = NULL) |

Curingas: `module='*'` ou `action='*'` batem em qualquer valor pedido.

### Endpoints (X-Admin-Token)

| Método | Rota | Descrição |
|---|---|---|
| `POST` | `/auth/permissions` | Concede permissão (upsert idempotente) |
| `GET` | `/auth/permissions?tenant_id=&user_id=&module=` | Lista permissões com filtros |
| `DELETE` | `/auth/permissions/{id}` | Revoga permissão |
| `GET` | `/auth/permissions/resolve?tenant_id=&user_id=&module=&action=&pool_id=` | Resolve se usuário tem permissão (sem admin token) |
| `POST` | `/auth/templates` | Cria template |
| `GET` | `/auth/templates?tenant_id=` | Lista templates |
| `GET` | `/auth/templates/{id}` | Detalhe do template |
| `PATCH` | `/auth/templates/{id}` | Atualiza template |
| `DELETE` | `/auth/templates/{id}` | Remove template |
| `POST` | `/auth/templates/{id}/apply` | Materializa permissões do template para um usuário |

### Funções principais (`permissions.py`)

```python
grant_permission(...)       → dict   # ON CONFLICT DO UPDATE (idempotente)
revoke_permission(...)      → bool
list_permissions(...)       → list[dict]   # filtros: tenant_id, user_id, module
resolve_permissions(...)    → bool   # global scope primeiro, depois pool scope
get_accessible_pools_for_module(...)  → list[str] | None
# None = acesso global (todos os pools); [] = sem acesso; [...] = pools específicos

apply_template(pool, template_id, tenant_id, user_id, granted_by, scope_override=None)
# Materializa template → platform_permissions (sem lookup em cadeia no runtime)
# scope_override: {"scope_type": "pool", "scope_id": "pool_sac"} para restringir ao bind
```

### Resolução de permissão

```
resolve_permissions(tenant_id, user_id, module, action, pool_id=None):
  1. Busca linhas WHERE (module=$m OR module='*') AND (action=$a OR action='*')
  2. scope_type='global'                     → True
  3. scope_type='pool' AND scope_id=$pool_id → True (se pool_id fornecido)
  4. Nenhuma match                           → False
```

### Arquivos

| Arquivo | Responsabilidade |
|---|---|
| `permissions.py` | DDL + CRUD: `ensure_permissions_schema`, grant/revoke/list/resolve, templates CRUD + apply |
| `router.py` | Endpoints de permissão e template adicionados ao router existente |
| `models.py` | `GrantPermissionRequest`, `PermissionResponse`, `PermissionEntry`, `CreateTemplateRequest`, `UpdateTemplateRequest`, `TemplateResponse`, `ApplyTemplateRequest`, `ResolvePermissionResponse` |

## Arc 7c — visibilidade por pool em analytics (✅ implementado)

JWT carrega `accessible_pools[]`. analytics-api injeta `WHERE pool_id IN (...)` nas queries ClickHouse. Row-level security sem subselects — whitelist de pool_ids vem diretamente do JWT.

### Arquivos novos / modificados

| Arquivo | Alteração |
|---|---|
| `analytics-api/config.py` | Campo `auth_jwt_secret: str = ""` — segredo HS256 do auth-api (deve coincidir com `PLUGHUB_AUTH_JWT_SECRET`) |
| `analytics-api/pool_auth.py` | **NOVO** — `PoolPrincipal` + `optional_pool_principal` FastAPI dependency |
| `analytics-api/reports_query.py` | `_apply_pool_scope()` helper; parâmetro `accessible_pools: list[str] \| None` em 6 funções: sessions, agents, quality, participation, segments, agent_performance |
| `analytics-api/reports.py` | `Depends(optional_pool_principal)` em 6 endpoints; `accessible_pools` propagado para query helpers |

### PoolPrincipal — semântica de acessível

```python
accessible_pools = None    # acesso irrestrito — todos os pools
accessible_pools = [...]   # restrito a esses pool_ids (JWT com lista de pools)
```

### optional_pool_principal — comportamento por cenário

| Cenário | Resultado |
|---|---|
| `analytics_open_access=True` OU `auth_jwt_secret=""` | `accessible_pools=None` (sem restrição) |
| Sem header Authorization | `accessible_pools=None` (backward-compatible) |
| JWT válido, `accessible_pools=[]` | `accessible_pools=None` (convenção auth-api: `[]` = todos os pools) |
| JWT válido, `accessible_pools=["sac","retencao"]` | `accessible_pools=["sac","retencao"]` |
| JWT inválido/expirado | HTTP 401 |

### `_apply_pool_scope` — aplicado nas queries

Quando `accessible_pools` é uma lista não-vazia, injeta:
```sql
AND pool_id IN ('pool_sac', 'pool_retencao')
```
Quando `accessible_pools=[]` (lista vazia), o caller retorna `{"data": [], "meta": {total: 0}}` sem chamar o ClickHouse (short-circuit).

### Env var

```
PLUGHUB_ANALYTICS_AUTH_JWT_SECRET=<mesmo valor que PLUGHUB_AUTH_JWT_SECRET>
```

Quando vazia (default), pool scoping é desabilitado (todos os pools visíveis).

### Endpoints com pool scoping

`GET /reports/sessions`, `/reports/agents`, `/reports/quality`, `/reports/participation`, `/reports/segments`, `/reports/agents/performance`

Os endpoints que não têm dimensão `pool_id` (`/reports/usage`, `/reports/workflows`, `/reports/campaigns`, `/reports/evaluations`) não foram modificados.

### Testes

`analytics-api/tests/test_reports.py` — 63/63 passando. Classes novas Arc 7c:
- `TestApplyPoolScope` (4) — helper puro: None noop, lista vazia retorna False, IN clause gerado corretamente
- `TestPoolScopedSessionsReport` (3) — None passa, vazia short-circuits, lista injeta IN clause no SQL
- `TestPoolScopedAgentsReport` (2) — idem para agent_events
- `TestPoolPrincipalAuth` (9) — open_access, sem secret, sem token, JWT []→None, JWT lista→restrito, JWT inválido→401

## Arc 7d — roteamento por performance (✅ implementado)

Batch job lê `mv_agent_performance_daily` (ClickHouse) e escreve scores normalizados em Redis.
`score_resource()` no routing-engine blenda competência com performance histórica com peso configurável.

### Score formula

```
performance_score = resolution_rate × (1 − min(escalation_rate, 1.0))
```

Resultado em [0.0, 1.0]. Recompensa alta taxa de resolução, penaliza escalação.

### Blending no score_resource()

```
final = (1 − w) × competency_score + w × performance_score

w = performance_score_weight (0.0–1.0)
  0.0 = puro competency (padrão — backward-compatible, sem Redis reads)
  0.3 = 70% competência + 30% performance histórica (recomendado em produção)
```

Hard filter (-1.0) é preservado independente do performance_score.
Quando sem dados (novo agent, primeiros 7 dias), `performance_score = 0.5` (neutro — sem viés).

### Redis key pattern

```
{tenant_id}:agent_perf:{agent_type_id}
  Value: str(float) in [0.0, 1.0]
  TTL:   21600s (6h) — renovado a cada sync (5 min)
```

### Configuração

```
PLUGHUB_PERFORMANCE_SCORE_WEIGHT=0.3    # env var no routing-engine
```

Ou via Config API namespace `routing` key `performance_score_weight` (editável por tenant no Operator Console).

### Componentes implementados

| Arquivo | Responsabilidade |
|---|---|
| `analytics-api/performance_job.py` | `compute_performance_score()`, `run_performance_sync()`, `run_performance_job_loop()` — batch job query + Redis write |
| `analytics-api/main.py` | Inicializa `perf_task` background em lifespan; `POST /admin/performance-sync` para trigger manual |
| `routing-engine/registry.py` | `_agent_perf_key()` helper; `InstanceRegistry.get_agent_performance_score()` — lê Redis com fallback 0.5 |
| `routing-engine/scorer.py` | `score_resource()` estendida com `performance_score` + `performance_score_weight` params |
| `routing-engine/router.py` | `_allocate()` lê `perf_weight` de `routing_config.get()` com fallback para env var |
| `routing-engine/config.py` | `performance_score_weight: float = 0.0` (env `PLUGHUB_PERFORMANCE_SCORE_WEIGHT` — fallback quando Config API indisponível) |
| `routing-engine/routing_config.py` | **NOVO** — `RoutingConfigCache`: busca namespace `routing` do Config API no startup; invalidado por `ConfigChangedHandler` via `config.changed` Kafka; defaults built-in garantem operação offline |
| `routing-engine/kafka_listener.py` | `ConfigChangedHandler` — invalida e recarrega `RoutingConfigCache` em background quando `namespace=routing` |
| `config-api/seed.py` | Seed entry `routing.performance_score_weight = 0.0` com descrição |

### Parâmetros do batch job

| Constante | Valor | Descrição |
|---|---|---|
| `PERF_KEY_TTL` | 21600s (6h) | TTL das chaves Redis de performance |
| `LOOKBACK_DAYS` | 7 | Janela de lookback no ClickHouse |
| `MIN_SESSIONS` | 5 | Mínimo de sessões para significância estatística |
| Intervalo do loop | 300s (5 min) | Frequência de sync performance → Redis |

### Tests

- `analytics-api/tests/test_performance_job.py` — 12 assertions: `TestComputePerformanceScore` (6 — fórmula, edge cases), `TestRunPerformanceSync` (6 — Redis write, key format, TTL, CH error, Redis error)
- `routing-engine/tests/test_scorer.py`: `TestResourceScorerPerformanceBlending` (6 assertions — zero weight backward-compat, high perf boost, low perf penalty, hard filter preserved, neutral default no-bias, no-requirements pool blending)

## Platform-UI — integração real com auth-api (✅ implementado)

A platform-ui foi integrada ao auth-api real (porta 3200), substituindo o formulário mock por autenticação JWT completa.

### Token storage strategy

| Token | Localização | Motivo |
|---|---|---|
| `access_token` | Memória (React state) | Não persiste entre reloads — re-obtido via refresh silencioso |
| `refresh_token` | `localStorage('plughub_refresh_token')` | Sobrevive reload — base para silent re-auth |
| Metadados (name, role, tenant) | `localStorage('plughub_session_meta')` | Persiste sem expor token |

### Arquivos modificados/criados

| Arquivo | Descrição |
|---|---|
| `src/api/auth.ts` (NOVO) | `apiLogin`, `apiRefresh`, `apiLogout` — client HTTP para auth-api; `AuthApiError` com status HTTP |
| `src/auth/AuthContext.tsx` | Reescrito: JWT flow real, auto-refresh (60s antes da expiração), silent re-auth no mount |
| `src/auth/LoginPage.tsx` | Reescrito: email + password reais, tratamento de erros por status HTTP (401/403/5xx) |
| `src/auth/ProtectedRoute.tsx` | Atualizado: spinner durante `isInitializing`; preserva URL de destino em `location.state` |
| `src/auth/useAuth.ts` | Inalterado (expõe novo `isInitializing` e `getAccessToken` via context) |
| `src/types/index.ts` | `Session` extendido com `email`, `roles[]`, `accessiblePools[]`, `accessToken`, `refreshToken`, `expiresAt` |
| `src/shell/TopBar.tsx` | `handleLogout` tornou-se async; exibe `session.email` em vez de `session.userId` |
| `vite.config.ts` | Proxy `'^/auth'` → `http://localhost:3200` adicionado |

### Fluxo de autenticação

```
Login:
  LoginPage → apiLogin(email, password) → TokenResponse
  → buildSession() → setState + localStorage + scheduleRefresh()

Auto-refresh:
  setTimeout (60s antes de expiresAt) → apiRefresh(refreshToken)
  → novo TokenResponse → re-agendamento

Silent re-auth no mount:
  localStorage tem refresh_token → apiRefresh()
    → sucesso: session restaurada, isInitializing=false
    → falha: clearStorage(), isInitializing=false (→ login page)

Logout:
  clearTimeout, setSession(null), clearStorage()
  → apiLogout(refreshToken, accessToken) — best-effort
```

### `getAccessToken()` — para API clients

Método disponível em `useAuth()`:
```typescript
const token = await getAccessToken()   // null se não autenticado
// Verifica expiração, faz refresh se necessário, deduplica chamadas concorrentes
```

### `isInitializing` — evita flash do login

`ProtectedRoute` mostra spinner enquanto `isInitializing=true`, evitando que usuários com refresh_token válido vejam o formulário de login por 100–500ms antes do redirect.

---

## Sistema ABAC — Permissões Declarativas por Módulo

O sistema ABAC (Attribute-Based Access Control) complementa o RBAC existente (roles: operator/supervisor/admin/developer/business) com permissões granulares declaradas por módulo. Cada módulo registra seus próprios campos de permissão em `infra/modules.yaml`. As permissões são armazenadas em `auth.users.module_config` (JSONB), carregadas no JWT e avaliadas localmente no frontend — sem round-trip ao servidor.

### auth-api — extensões

#### Nova tabela: `auth.module_registry`

```sql
CREATE TABLE auth.module_registry (
    module_id          TEXT PRIMARY KEY,
    label              TEXT NOT NULL,
    icon               TEXT NOT NULL DEFAULT '',
    nav_path           TEXT NOT NULL DEFAULT '',
    active             BOOL NOT NULL DEFAULT TRUE,
    permission_schema  JSONB NOT NULL DEFAULT '{}',
    created_at         TIMESTAMPTZ DEFAULT now(),
    updated_at         TIMESTAMPTZ DEFAULT now()
);
```

#### Nova coluna em `auth.users`

```sql
ALTER TABLE auth.users
  ADD COLUMN IF NOT EXISTS module_config JSONB NOT NULL DEFAULT '{}';
```

#### Novos endpoints

| Método | Rota | Auth | Descrição |
|---|---|---|---|
| `GET` | `/auth/modules?active_only=true` | — | Lista módulos com permission_schema |
| `PUT` | `/auth/users/{id}/module-config` | X-Admin-Token | Salva module_config do usuário |

#### Seed automático

Função `seed_modules_from_yaml()` lê `infra/modules.yaml` e faz upsert em `auth.module_registry` ao iniciar o auth-api. Idempotente — sem erro em re-inicializações.

### `infra/modules.yaml` — registro central de módulos

Arquivo YAML com 8 módulos registrados. Cada módulo define:
- `module_id`, `label`, `icon`, `nav_path`, `active`
- `permission_schema`: mapa de campos, onde cada campo tem `label`, `domain` (lista de `PermissionAccess` permitidos), `scopable` (bool), `scope_type` (pool|campaign|global), `default`

**Módulos registrados:**

| Módulo | Campos de permissão | Descrição |
|---|---|---|
| `evaluation` | `contestar`, `revisar`, `relatorio`, `formularios` | Avaliação de qualidade |
| `contacts` | **`operacao`**, `visualizar`, `exportar` | Contatos — `operacao` gatea Monitor + AgentAssist |
| `billing` | `visualizar`, `gerenciar` | Faturamento e preços |
| `config` | `plataforma`, `recursos`, `canais`, `usuarios`, `mascaramento` | Configuração de plataforma |
| `skill_flows` | **`operacao`**, `visualizar`, `editar` | AgentFlow — `operacao` gatea Editor, Monitor, Deploy |
| `workflows` | **`operacao`**, `visualizar`, `cancelar`, `webhooks` | Automação — `operacao` gatea Editor, Monitor, Calendar |
| `agent_assist` | `atender`, `supervisionar` | Atendimento humano |
| `campaigns` | `visualizar`, `gerenciar` | Campanhas de coleta |

**Nota:** o módulo `analytics` foi removido do `infra/modules.yaml`. Seus dados estão disponíveis nas abas MonitorTab e AnaliseTab do ContactsPage, não como módulo ABAC separado.

### JWT — `module_config` incluído

O access token agora inclui o campo `module_config` extraído de `auth.users`:

```json
{
  "sub": "user-uuid",
  "tenant_id": "tenant_demo",
  "email": "user@example.com",
  "roles": ["supervisor"],
  "accessible_pools": [],
  "module_config": {
    "evaluation": {
      "contestar": { "access": "none", "scope": [] },
      "revisar":   { "access": "read_write", "scope": ["pool:retencao_humano"] }
    },
    "analytics": {
      "view": { "access": "read_only", "scope": [] }
    }
  }
}
```

### platform-ui — tipos e helper

#### Tipos em `src/types/index.ts`

```typescript
export type PermissionAccess = 'none' | 'read_only' | 'write_only' | 'read_write'

export interface ModuleFieldConfig {
  access: PermissionAccess
  scope: string[]           // [] = acesso global; ['pool:sac', 'pool:retencao'] = restrito
}

export type ModuleConfig = Record<string, Record<string, ModuleFieldConfig>>

// Integrado em Session
export interface Session {
  // ...campos existentes...
  moduleConfig: ModuleConfig
}
```

#### Helper puro: `src/lib/permissions.ts`

```typescript
export class PermissionChecker {
  constructor(private moduleConfig: ModuleConfig | undefined) {}

  // Verifica se usuário tem acesso a um campo (qualquer nível acima de 'none')
  can(module: string, field: string): boolean
  can(module: string, field: string, minAccess: PermissionAccess): boolean
  can(module: string, field: string, minAccess: PermissionAccess, scopeId: string): boolean

  // Retorna o nível de acesso (sem verificar scope)
  access(module: string, field: string): PermissionAccess

  // Retorna a lista de escopos para um campo
  scopeOf(module: string, field: string): string[]
}

export function makePermissions(moduleConfig: ModuleConfig | undefined): PermissionChecker
```

**Exemplos de uso:**

```typescript
const perms = makePermissions(session?.moduleConfig)

perms.can('evaluation', 'revisar')                               // tem qualquer acesso?
perms.can('evaluation', 'revisar', 'read_write')                 // tem read_write?
perms.can('evaluation', 'revisar', 'read_write', 'pool:sac')     // tem acesso ao pool sac?
perms.access('evaluation', 'contestar')                          // 'none'|'read_only'|'write_only'|'read_write'
perms.scopeOf('evaluation', 'revisar')                           // [] = global; ['pool:x', 'pool:y']
```

**Hierarquia de acesso:** `none < read_only < write_only < read_write`. Quando `scope: []`, acesso é global (qualquer pool/campaign passa na validação de escopo).

#### Componente: `src/components/ModulePermissionForm.tsx`

Formulário dinâmico de permissões ABAC para uso na gestão de usuários:

```typescript
<ModulePermissionForm
  modules={modules}              // retorno de GET /auth/modules?active_only=true
  value={userModuleConfig}       // estado atual
  onChange={(newConfig) => {...}}
  readOnly={false}
  adminToken={adminToken}
/>
```

**Comportamento:**
- Renderiza acordeões colapsáveis por módulo
- Cada campo do `permission_schema` vira um `<select>` com opções: None, Read Only, Write Only, Read Write
- Quando `access != none` e `scopable: true`, renderiza input de escopo com chips remoníveis (pool_id ou campaign_id)
- Suporta modo `readOnly` (desabilita inputs, buttons)
- Validação inline: rejeita duplicatas de escopo, detecta escopos inválidos

### Integração no gerenciamento de usuários

#### `src/modules/access/AccessPage.tsx` — CRUD de usuários com ABAC

O modal de criação/edição de usuário agora inclui `ModulePermissionForm`:

- Hook `useModules(adminToken)` → `GET /auth/modules?active_only=true`
- Ao criar usuário: `POST /auth/users` → depois `PUT /auth/users/{id}/module-config` com config
- Ao editar: `PATCH /auth/users/{id}` + `PUT /auth/users/{id}/module-config` (ambos simultâneos via Promise.all)
- Modal ampliado de `max-w-lg` para `max-w-2xl` para acomodar formulário de permissões
- Estados de salvamento e erro propagados para `ModulePermissionForm`

### Sidebar — filtragem ABAC de itens de nav

#### `src/shell/Sidebar.tsx` — gates ABAC opcionais em `NavItem`

```typescript
export interface NavItem {
  label: string
  href: string
  icon: string
  roles?: string[]              // filtro RBAC
  abac?: {                       // novo — filtro ABAC
    module: string
    field: string
  }
}
```

#### Função `passesAbac(item, moduleConfig)`

- Sem campo `abac` → sempre visível
- Sem `moduleConfig` no session (conta legacy) → graceful degradation, mostra o item (backward-compatible)
- Com `moduleConfig` → avalia `perms.can(item.abac.module, item.abac.field)` → visível se true

**`operacao` gates — contacts, workflows, skill_flows (added in sidebar refactor):**

```typescript
// Operational items hidden for users with operacao: none (e.g. business role)
{ href: '/contacts?tab=monitor', abac: { module: 'contacts',    field: 'operacao' } },
{ href: '/agent-assist',         abac: { module: 'contacts',    field: 'operacao' } },
{ href: '/workflow/editor',      abac: { module: 'workflows',   field: 'operacao' } },
{ href: '/workflow/monitor',     abac: { module: 'workflows',   field: 'operacao' } },
{ href: '/workflow/calendar',    abac: { module: 'workflows',   field: 'operacao' } },
{ href: '/agent-flow/editor',    abac: { module: 'skill_flows', field: 'operacao' } },
{ href: '/agent-flow/monitor',   abac: { module: 'skill_flows', field: 'operacao' } },
{ href: '/agent-flow/deploy',    abac: { module: 'skill_flows', field: 'operacao' } },
// Report pages have NO gate — visible to all roles that reach the group
```

**Items de avaliação com gates ABAC definidos:**

```typescript
// Em navItems array
{
  label: t('nav.evaluation.forms'),
  href: '/evaluation/forms',
  icon: '📋',
  roles: ['admin'],
  abac: { module: 'evaluation', field: 'formularios' }
},
{
  label: t('nav.evaluation.campaigns'),
  href: '/evaluation/campaigns',
  icon: '📢',
  roles: ['supervisor', 'admin'],
  abac: { module: 'evaluation', field: 'formularios' }
},
{
  label: t('nav.eval.avaliacoes'),
  href: '/evaluation/avaliacoes',
  icon: '🗂️',
  roles: ['operator', 'supervisor', 'admin'],
  // No ABAC gate — page is visible to all; available_actions are computed server-side per row
},
{
  label: t('nav.evaluation.reports'),
  href: '/evaluation/reports',
  icon: '📊',
  roles: ['supervisor', 'admin'],
  abac: { module: 'evaluation', field: 'relatorio' }
}
```

### Integração no módulo de avaliação

#### `AvaliacoesPage.tsx` — tabela unificada (substitui ReviewPage + MyEvaluationsPage)

Visão única de todas as avaliações com filtros completos e drill-down lateral.
`available_actions` é computado server-side com Bearer JWT — nunca localmente:

```
GET /v1/evaluation/results?tenant_id=...&action_required=any     → Aguardando minha ação
GET /v1/evaluation/results?tenant_id=...&eval_status=submitted   → filtro por status
GET /v1/evaluation/results?tenant_id=...&campaign_id=...         → filtro por campanha
```

Por row, o servidor retorna `available_actions: ["review" | "contest"]` baseado no ABAC do JWT do caller.
A UI desabilita/oculta botões com base nesse campo — nunca computa permissão localmente.

#### Contestação por critério — `ContestPanel`

Operadores contestam avaliações critério a critério em vez de escrever uma razão genérica.

**Componentes:**
- `CriterionContestRow` — exibe `criterion_id`, score atribuído e justificativa do avaliador IA como contexto; checkbox para selecionar o critério para contestação; textarea de discordância (≥ 30 chars) com contador de caracteres e borda verde/vermelha
- `ContestPanel` — lista todos os critérios não-NA via `CriterionContestRow`; estado `crState: Record<criterion_id, { checked, justification }>`; botão de envio desabilitado até que ao menos um critério esteja selecionado e todas as justificativas tenham ≥ 30 chars

**Formato estruturado do campo `reason`** (compilado por `buildReason()`, armazenado no backend como string):
```
[criterion_id] Nota atribuída: 7.0/10
Avaliação do sistema: O agente seguiu o protocolo...
Discordância: Na verdade o agente não...

---

[criterion_id2] Nota atribuída: 4.0/10
Avaliação do sistema: ...
Discordância: ...
```

Blocos separados por `\n\n---\n\n`. Backward-compatible — contestações legadas (texto livre) são tratadas como fallback.

**`parseContestationReason(reason: string): ParsedCriterionContestation[]`** — helper puro que faz split em `\n\n---\n\n` e extrai `criterion_id`, `score_label`, `system_evaluation`, `disagreement` por bloco. Fallback gracioso para texto não estruturado.

#### Revisão por critério — `ReviewPanel`

Revisores (IA e humanos) respondem por critério, no mesmo formato estruturado.

- Contestações abertas renderizadas via `parseContestationReason()` — cada critério contestado em card próprio mostrando score original, avaliação do sistema e discordância do contestante
- Textareas de nota por critério abaixo de cada card (opcional por linha)
- Nota geral (obrigatória apenas para `adjusted_approved` ou `rejected`)
- `buildReviewNote()` compila `[criterion_id] nota\n\n---\n\n[geral] nota` no mesmo formato estruturado
- Estado `crNotes: Record<criterion_id, string>` + `generalNote: string`

**Invariante:** o mesmo parser `parseContestationReason` é usado tanto para exibir a contestação ao revisor quanto para exibir a resposta do revisor ao contestante — formato é simétrico.

#### `CampaignsPage.tsx` — campos Arc 6 v2 no CreateModal e painel de detalhe

**CreateModal** (expandido para 620 px, layout 2 colunas):
- Dropdown "Skill de revisão" → `review_workflow_skill_id` (opções: `skill_revisao_simples_v1`, `skill_revisao_treplica_v1`)
- Seção "Política de contestação" opt-in via checkbox:
  - Máximo de rounds, prazo de revisão (horas), alçada (`supervisor` / `manager` / `director`), auto-lock por timeout
  - Informação contextual: "sem política = contestação desabilitada"

**Painel de detalhe** — 4 cards: Sampling, Reviewer IA, Skill de revisão (id monoespaçado + descrição), Política de contestação (max rounds / prazo / auto-lock / breakdown por round)

### Invariantes (nunca violar)

- **`module_config` é avaliado localmente no frontend** — nunca fazer round-trip ao servidor para verificar permissão ABAC em roteamento de UI
- **Graceful degradation:** usuários sem `moduleConfig` recebem acesso baseado apenas em `role` (backward-compatible com contas legacy)
- **O `module_registry` é seed automático** ao iniciar auth-api — nunca inserir manualmente via SQL
- **`makePermissions` é puro** (sem efeitos colaterais) — seguro chamar em render sem memoização
- **Permissão ABAC é um filtro adicional** ao filtro de roles — roles continuam sendo a primeira barreira. Ambos devem passar (`AND` lógico)
- **Nunca persistir `PermissionAccess` como strings hardcoded** — sempre usar tipos TypeScript para evitar typos
- **Escopo sempre em formato `{type}:{id}`** — ex: `pool:retencao_humano`, `campaign:camp_abc123`, nunca valores soltos
