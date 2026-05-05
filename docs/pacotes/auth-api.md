# Módulo: auth-api

> **Responsabilidade:** autenticação real com JWT HS256, gestão de usuários, refresh token rotation e sistema ABAC de permissões por módulo.
> **Porta:** 3200 · **Runtime:** Python 3.11+ · **Framework:** FastAPI + asyncpg + bcrypt + python-jose

---

## Visão geral

O auth-api é o único componente do PlugHub responsável por autenticação e controle de identidade. Ele:

1. Autentica usuários (email + senha) e emite JWTs com refresh token rotation
2. Mantém o registro de usuários por tenant com roles e pools acessíveis
3. Declara e distribui permissões de módulo (ABAC) embutidas no JWT
4. Expõe operações CRUD de usuários para administração via `X-Admin-Token`

Outros serviços validam o JWT localmente (sem chamada ao auth-api no hot path). A exceção são endpoints admin que verificam o `X-Admin-Token` configurado via env var.

---

## PostgreSQL Schema (`auth`)

### `auth.users`

```sql
CREATE TABLE auth.users (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        TEXT NOT NULL,
    email            TEXT NOT NULL,
    name             TEXT NOT NULL DEFAULT '',
    password_hash    TEXT NOT NULL,           -- bcrypt rounds=12
    roles            TEXT[] NOT NULL DEFAULT '{}',
    accessible_pools TEXT[] NOT NULL DEFAULT '{}',  -- [] = todos os pools
    module_config    JSONB NOT NULL DEFAULT '{}',   -- ABAC: { module: { field: { access, scope } } }
    active           BOOL NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMPTZ DEFAULT now(),
    updated_at       TIMESTAMPTZ DEFAULT now(),
    UNIQUE (tenant_id, email)
);
```

### `auth.sessions`

```sql
CREATE TABLE auth.sessions (
    id                 UUID PRIMARY KEY,
    user_id            UUID REFERENCES auth.users(id) ON DELETE CASCADE,
    tenant_id          TEXT NOT NULL,
    refresh_token_hash TEXT NOT NULL UNIQUE,  -- SHA-256(plain_token)
    expires_at         TIMESTAMPTZ NOT NULL,
    last_used_at       TIMESTAMPTZ DEFAULT now()
);
```

### `auth.module_registry`

```sql
CREATE TABLE auth.module_registry (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    module_id  TEXT NOT NULL UNIQUE,
    label      TEXT NOT NULL,
    fields     JSONB NOT NULL DEFAULT '{}',
    -- fields = { field_id: { label: str, type: "permission", scope?: "pool" } }
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);
```

### `auth.platform_permissions`

```sql
CREATE TABLE auth.platform_permissions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    module      TEXT NOT NULL,
    action      TEXT NOT NULL,
    scope_type  TEXT NOT NULL CHECK (scope_type IN ('pool', 'global')),
    scope_id    TEXT,
    granted_by  TEXT NOT NULL DEFAULT 'system',
    template_id UUID,
    created_at  TIMESTAMPTZ DEFAULT now(),
    UNIQUE (tenant_id, user_id, module, action, scope_type, COALESCE(scope_id, ''))
);
```

### `auth.permission_templates`

```sql
CREATE TABLE auth.permission_templates (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   TEXT NOT NULL,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    permissions JSONB NOT NULL DEFAULT '[]',
    created_at  TIMESTAMPTZ DEFAULT now(),
    updated_at  TIMESTAMPTZ DEFAULT now(),
    UNIQUE (tenant_id, name)
);
```

---

## JWT — claims do access token

Token HS256, TTL 1 hora (configurável via env).

```json
{
  "sub":              "uuid-do-usuario",
  "tenant_id":        "tenant_demo",
  "email":            "user@company.com",
  "name":             "Nome do Usuário",
  "roles":            ["operator", "supervisor"],
  "accessible_pools": ["pool_sac", "pool_retencao"],
  "module_config": {
    "evaluation": {
      "formularios": { "access": "read_write", "scope": [] },
      "revisar":     { "access": "read_only",  "scope": ["pool_sac"] },
      "contestar":   { "access": "none",       "scope": [] }
    }
  },
  "exp": 1745971200,
  "iat": 1745967600
}
```

`accessible_pools: []` significa acesso a todos os pools (sem restrição de pool).

---

## Refresh token

Token opaco de 43 chars URL-safe (~258 bits de entropia). O plain token é exibido uma vez e nunca persistido — apenas `SHA-256(plain_token)` fica no banco. Rotation automática: cada `POST /auth/refresh` emite novo par e invalida o hash anterior atomicamente.

---

## Endpoints

### Autenticação

| Método | Rota | Auth | Descrição |
|---|---|---|---|
| `POST` | `/auth/login` | — | Email + senha → access_token + refresh_token |
| `POST` | `/auth/refresh` | body `refresh_token` | Rotation → novo par de tokens |
| `POST` | `/auth/logout` | body `refresh_token` | Invalida refresh_token (idempotente) |
| `GET`  | `/auth/me` | Bearer | Claims do access token atual |

### Gestão de usuários (X-Admin-Token)

| Método | Rota | Descrição |
|---|---|---|
| `GET`    | `/auth/users` | Lista usuários do tenant |
| `POST`   | `/auth/users` | Cria usuário |
| `GET`    | `/auth/users/{id}` | Detalhe do usuário |
| `PATCH`  | `/auth/users/{id}` | Atualiza (name, password, roles, accessible_pools, active) |
| `DELETE` | `/auth/users/{id}` | Remove usuário |
| `PATCH`  | `/auth/users/{id}/module-config` | Atualiza `module_config` ABAC |

### Módulos ABAC

| Método | Rota | Auth | Descrição |
|---|---|---|---|
| `GET` | `/auth/modules` | — | Lista módulos registrados do `module_registry` |

### Permissões de plataforma (platform_permissions)

| Método | Rota | Auth | Descrição |
|---|---|---|---|
| `POST`   | `/auth/permissions` | X-Admin-Token | Concede permissão (upsert idempotente) |
| `GET`    | `/auth/permissions` | X-Admin-Token | Lista com filtros (`tenant_id`, `user_id`, `module`) |
| `DELETE` | `/auth/permissions/{id}` | X-Admin-Token | Revoga permissão |
| `GET`    | `/auth/permissions/resolve` | — | Resolve se usuário tem permissão específica |

### Templates de permissão

| Método | Rota | Auth | Descrição |
|---|---|---|---|
| `POST`   | `/auth/templates` | X-Admin-Token | Cria template |
| `GET`    | `/auth/templates` | X-Admin-Token | Lista templates do tenant |
| `GET`    | `/auth/templates/{id}` | X-Admin-Token | Detalhe do template |
| `PATCH`  | `/auth/templates/{id}` | X-Admin-Token | Atualiza template |
| `DELETE` | `/auth/templates/{id}` | X-Admin-Token | Remove template |
| `POST`   | `/auth/templates/{id}/apply` | X-Admin-Token | Materializa permissões para um usuário |

---

## Arquivos

| Arquivo | Responsabilidade |
|---|---|
| `config.py` | `Settings` com prefixo `PLUGHUB_AUTH_`; todos os parâmetros configuráveis |
| `password.py` | `hash_password()`, `verify_password()` — bcrypt rounds=12 |
| `jwt_utils.py` | `create_access_token()`, `decode_access_token()`, `generate_refresh_token()`, `hash_refresh_token()` |
| `models.py` | Pydantic: `LoginRequest`, `RefreshRequest`, `LogoutRequest`, `CreateUserRequest`, `UpdateUserRequest`, `ModuleConfigPatch`, `TokenResponse`, `UserResponse`, `MeResponse`, `PermissionResponse`, `TemplateResponse` |
| `db.py` | DDL + CRUD asyncpg: users, sessions, CRUD completo |
| `permissions.py` | DDL + CRUD: `ensure_permissions_schema`, grant/revoke/list/resolve, templates CRUD + apply |
| `modules.py` | `seed_modules_from_yaml()`, `list_modules()` |
| `router.py` | Todos os endpoints FastAPI |
| `main.py` | FastAPI app + lifespan (asyncpg pool + seed) |

---

## Configuração

Todas as variáveis de ambiente usam o prefixo `PLUGHUB_AUTH_`:

| Variável | Default | Descrição |
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

---

## Seed automático

`seed_admin_if_absent()` — executado no lifespan do app. Cria o usuário admin configurado via env vars se não existir. Idempotente: sem erro em re-inicializações.

`seed_modules_from_yaml(conn, yaml_path)` — lê `infra/modules.yaml` e faz upsert em `auth.module_registry`. Executado imediatamente após o schema DDL no lifespan. Idempotente.

---

## Integração com outros módulos

### analytics-api (Arc 7c)

Valida JWT do auth-api para pool scoping: `PLUGHUB_ANALYTICS_AUTH_JWT_SECRET` deve ser igual a `PLUGHUB_AUTH_JWT_SECRET`. `optional_pool_principal` dependency extrai `accessible_pools` do JWT e injeta `WHERE pool_id IN (...)` nas queries ClickHouse.

### platform-ui (Arc 7d)

`AuthContext.tsx` — auth flow completo:
- `access_token` em memória (React state)
- `refresh_token` em `localStorage`
- Auto-refresh 60s antes da expiração
- Silent re-auth no mount via `localStorage`
- `isInitializing` previne flash do login para usuários com refresh_token válido

`session.moduleConfig` propagado para todos os componentes via `useAuth()`. `makePermissions(session.moduleConfig)` avalia permissões ABAC localmente.

---

## Segurança

- **Senhas**: bcrypt rounds=12 — ~300ms de hash intencional para resistência a brute-force
- **Refresh tokens**: opaco, 258 bits de entropia, armazenado como SHA-256 — plain token nunca persisted
- **Comparação de tokens**: `hmac.compare_digest` — proteção contra timing attacks
- **JWT**: HS256 com secret mínimo de 32 chars — nunca RS256 para evitar complexidade de PKI interna
- **`module_config` no JWT**: avaliado localmente na UI, mas o backend repete verificações em endpoints sensíveis (defesa em profundidade)

---

## Tests

`tests/test_router.py` — **58/58 testes**:

| Classe | Testes | Cobertura |
|---|---|---|
| `TestHealth` | 1 | GET /health |
| `TestLogin` | 4 | success, wrong password, inactive user, unknown email |
| `TestRefresh` | 3 | success (rotation), expired, invalid |
| `TestLogout` | 2 | success, already invalid (idempotent) |
| `TestMe` | 3 | valid token, expired, no header |
| `TestCreateUser` | 3 | success, duplicate email, validation |
| `TestListUsers` | 1 | list with tenant filter |
| `TestGetUser` | 2 | found, not found |
| `TestUpdateUser` | 2 | name change, password change |
| `TestDeleteUser` | 2 | success, not found |
| `TestSeedAdmin` | 2 | absent (creates), present (no-op) |
| `TestPasswordUtils` | 3 | hash, verify match, verify mismatch |
| `TestJwtUtils` | 3 | create/decode round-trip, expired, tampered |
| `TestHashRefreshToken` | 3 | deterministic, different inputs, length |
| `TestGrantPermission` | 3 | success, idempotent upsert, multiple |
| `TestListPermissions` | 2 | all, filtered by module |
| `TestRevokePermission` | 2 | success, not found |
| `TestResolvePermission` | 3 | global match, pool match, no match |
| `TestTemplates` | 6 | CRUD completo |
| `TestApplyTemplate` | 2 | apply with scope override, apply without |
| `TestResolvePermissionsLogic` | 6 | wildcards, scope_type pool vs global, combined |
