# Sistema ABAC — Guia de Implementação

> Complementa o RBAC (role-based access control) com permissões declarativas por módulo.
> Avaliado localmente na UI via `makePermissions()` — zero latência extra no hot path.

---

## Conceitos

### Por que ABAC além de RBAC?

O RBAC do PlugHub define papéis (`operator`, `supervisor`, `admin`, etc.) e cada papel tem acesso a um conjunto fixo de páginas. Isso é suficiente para separar quem entra em qual módulo.

Dentro de um módulo, porém, usuários com o mesmo papel podem ter responsabilidades diferentes. Um supervisor pode precisar visualizar avaliações mas não aprová-las. Um coordenador de qualidade pode aprovar mas não gerir formulários. O ABAC permite essas distinções sem criar novos papéis ou lógica condicional espalhada pela UI.

### Modelo de dados

Cada usuário carrega um campo `module_config` (JSONB no PostgreSQL, embedded no JWT). Estrutura:

```json
{
  "evaluation": {
    "formularios": { "access": "read_write", "scope": [] },
    "revisar":     { "access": "read_only",  "scope": ["pool_sac"] },
    "contestar":   { "access": "none",       "scope": [] },
    "relatorio":   { "access": "read_only",  "scope": [] }
  },
  "analytics": {
    "dashboards":  { "access": "read_write", "scope": [] },
    "admin":       { "access": "none",       "scope": [] }
  }
}
```

### Hierarquia de acesso

```
none < read_only < write_only < read_write
```

| Nível | Significado |
|---|---|
| `none` | Sem acesso — botões ocultados, sidebar item oculto |
| `read_only` | Leitura apenas — pode ver, não pode agir |
| `write_only` | Escrita sem leitura (raro — para ingestão cega) |
| `read_write` | Acesso completo |

### Escopo por pool

`scope: []` significa acesso global (todos os pools). `scope: ["pool_sac", "pool_retencao"]` restringe a esses pools específicos. Útil para supervisores que gerenciam apenas parte da operação.

### Graceful degradation

Usuários sem `module_config` (accounts criados antes do ABAC, admins que não foram configurados) **nunca são bloqueados pelo ABAC**. O RBAC (roles) continua sendo o único filtro para eles. Isso garante continuidade operacional durante a adoção gradual.

---

## Declaração de módulos

Todos os módulos e campos são declarados em `infra/modules.yaml`:

```yaml
modules:
  evaluation:
    label: "Avaliação"
    fields:
      formularios:
        label: "Formulários e Campanhas"
        type: permission
      revisar:
        label: "Revisar avaliações"
        type: permission
      contestar:
        label: "Contestar avaliações"
        type: permission
      relatorio:
        label: "Relatórios de avaliação"
        type: permission
      permissoes:
        label: "Gerenciar permissões"
        type: permission
```

`type: permission` com `scope: pool` indica que o campo suporta restrição por pool_id.

O auth-api lê este YAML no startup e faz upsert em `auth.module_registry` (idempotente). A UI carrega os módulos de `GET /auth/modules` para construir o `ModulePermissionForm` dinamicamente.

---

## API de permissões

### `makePermissions(moduleConfig?)`

Importação:
```typescript
import { makePermissions } from '@/lib/permissions'
import type { ModuleConfig } from '@/types'
```

Retorna objeto `Permissions` com métodos:

```typescript
// Retorna true quando access !== 'none' (e pool no scope, se informado)
perms.can('evaluation', 'revisar')
perms.can('evaluation', 'revisar', 'pool_sac')

// Retorna o nível de acesso: 'none' | 'read_only' | 'write_only' | 'read_write'
perms.access('evaluation', 'contestar')

// Retorna o scope: [] = global; [...] = pools específicos
perms.scopeOf('analytics', 'admin')

// true quando scope === []
perms.isGlobal('evaluation', 'formularios')

// Retorna o ModuleFieldConfig completo ou undefined
perms.fieldConfig('evaluation', 'revisar')
```

### Padrão de uso em componentes

```typescript
import { useAuth } from '@/auth/useAuth'
import { makePermissions } from '@/lib/permissions'

export default function MinhaPagina() {
  const { session } = useAuth()
  const perms = makePermissions(session?.moduleConfig)

  // Graceful degradation: quando sem moduleConfig, perms.can() sempre retorna true
  const hasModuleConfig = !!(session?.moduleConfig
    && Object.keys(session.moduleConfig).length > 0)

  // Booleano simples para botões
  const canContest = !hasModuleConfig || perms.can('evaluation', 'contestar')

  // Três estados para áreas com leitura parcial
  const reviewAccess = (!hasModuleConfig
    ? 'read_write'
    : perms.access('evaluation', 'revisar')) as 'none' | 'read_only' | 'read_write'

  return (
    <>
      {/* Botão oculto quando sem permissão */}
      {canContest && <button>Contestar</button>}

      {/* Área com estados de acesso diferenciados */}
      {reviewAccess === 'read_write' && <ReviewForm />}
      {reviewAccess === 'read_only'  && <p className="text-blue-600">Somente leitura</p>}
      {reviewAccess === 'none'       && <p className="text-amber-600">Solicite permissão</p>}
    </>
  )
}
```

---

## Sidebar — ABAC gates

Para ocultar um item de menu baseado em ABAC, adicione o campo `abac` ao `NavItem` em `Sidebar.tsx`:

```typescript
const navItems: NavItem[] = [
  {
    label: t('nav.avaliacao'),
    href: '#',
    icon: '✓',
    roles: ['operator', 'supervisor', 'admin'],
    children: [
      {
        label: t('nav.eval.review'),
        href: '/evaluation/review',
        icon: '🔍',
        roles: ['supervisor', 'admin'],
        abac: { module: 'evaluation', field: 'revisar' }  // ← gate ABAC
      },
    ]
  },
]
```

O helper `passesAbac()` no Sidebar avalia o gate:
- Se `item.abac` ausente → passa (sem restrição ABAC)
- Se usuário sem `moduleConfig` → passa (graceful degradation)
- Caso contrário → `perms.can(module, field)`

**Regra:** ABAC gate e roles filter são ANDados — o item só aparece quando ambos passam.

---

## Adicionando um novo módulo

### 1. Declare em `infra/modules.yaml`

```yaml
modules:
  meu_modulo:
    label: "Meu Módulo"
    fields:
      visualizar:
        label: "Visualizar dados"
        type: permission
      editar:
        label: "Editar configurações"
        type: permission
```

### 2. Adicione o gate no Sidebar

```typescript
{
  label: t('nav.meuModulo'),
  href: '/meu-modulo',
  icon: '🔧',
  roles: ['supervisor', 'admin'],
  abac: { module: 'meu_modulo', field: 'visualizar' }
}
```

### 3. Use `makePermissions()` na página

```typescript
const perms = makePermissions(session?.moduleConfig)
const canEdit = !hasModuleConfig || perms.can('meu_modulo', 'editar')
```

### 4. Não é necessário mais nada

O auth-api lê o YAML no startup e registra o módulo automaticamente. O `ModulePermissionForm` carrega os módulos de `GET /auth/modules` dinamicamente. Não há listas hardcoded para atualizar.

---

## Gestão de permissões

### Via API (admin)

```bash
# Ver module_config atual de um usuário
GET /auth/users/{user_id}

# Atualizar module_config
PATCH /auth/users/{user_id}/module-config
X-Admin-Token: <token>
Content-Type: application/json

{
  "evaluation": {
    "formularios": { "access": "read_write", "scope": [] },
    "revisar":     { "access": "read_only",  "scope": ["pool_sac"] },
    "contestar":   { "access": "none",       "scope": [] }
  }
}
```

### Via UI

Rota `/config/access` (role: `admin`). Interface com lista de usuários e `ModulePermissionForm` com selects por campo e multi-select de pools.

---

## Invariantes

1. **ABAC nunca substitui RBAC** — é uma restrição adicional. Usuário precisa ter o role correto antes que o ABAC seja avaliado.
2. **Graceful degradation é obrigatória** — código sem `moduleConfig` (ou vazio) nunca deve bloquear acesso. Use o padrão `!hasModuleConfig || perms.can(...)`.
3. **Avaliação é sempre local** — nunca faça chamada de rede para verificar permissão ABAC. Os dados vêm do JWT.
4. **`makePermissions()` é puro** — sem estado, sem I/O. Pode ser chamado em qualquer ponto do ciclo de render.
5. **Defesa em profundidade** — a UI oculta botões para UX, mas o backend repete a verificação antes de executar ações sensíveis. Nunca confie apenas no frontend.
6. **`infra/modules.yaml` é a fonte de verdade** — nunca hardcode nomes de módulos ou campos fora desse arquivo e do código que o processa.
7. **Scope `[]` significa global** — acesso irrestrito a todos os pools. Scope `["pool_a"]` significa acesso restrito àquele pool.

---

## Tabelas PostgreSQL

```sql
-- Registro de módulos (populado do YAML no startup do auth-api)
CREATE TABLE auth.module_registry (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    module_id  TEXT NOT NULL UNIQUE,
    label      TEXT NOT NULL,
    fields     JSONB NOT NULL DEFAULT '{}',  -- { field_id: { label, type, scope? } }
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Permissões por usuário (campo na tabela auth.users)
-- auth.users.module_config JSONB NOT NULL DEFAULT '{}'
-- Estrutura: { module_id: { field_id: { access, scope: [] } } }
```

---

## Endpoints auth-api

| Método | Rota | Auth | Descrição |
|---|---|---|---|
| `GET` | `/auth/modules` | — | Lista módulos registrados (do YAML) |
| `PATCH` | `/auth/users/{id}/module-config` | X-Admin-Token | Atualiza module_config |
| `GET` | `/auth/me` | Bearer | Claims incluem module_config atual |

---

## Fluxo de dados

```
infra/modules.yaml
  → auth-api startup: seed_modules_from_yaml()
  → auth.module_registry (PostgreSQL)

PATCH /auth/users/{id}/module-config
  → auth.users.module_config (JSONB)

POST /auth/login
  → access_token JWT: { ..., module_config: { ... } }

platform-ui: AuthContext
  → session.moduleConfig

makePermissions(session.moduleConfig)
  → perms.can() / perms.access() / perms.scopeOf()
  → Sidebar: passesAbac()
  → Componentes: botões, estados de acesso
```
