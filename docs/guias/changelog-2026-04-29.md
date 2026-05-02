# Changelog — 2026-04-29

> Módulos afetados: `auth-api`, `platform-ui`, `analytics-api`
> Tasks concluídas: #17 (ABAC auth-api), #18 (platform-ui types), #19 (makePermissions), #20 (AccessPage), #21 (Sidebar ABAC), #22 (evaluation permissions), #23 (evaluation ABAC propagation), #15 (contact_insights tests)

---

## Contexto geral

Esta sessão implementou o **sistema ABAC (Attribute-Based Access Control)** do PlugHub — um modelo de permissões declarativas por módulo que complementa o controle por role (RBAC) existente.

O ABAC permite configurar, por usuário, acesso granular a funcionalidades dentro de cada módulo da plataforma (`evaluation`, `analytics`, `billing`, `config`, etc.) com níveis de acesso (`none`, `read_only`, `write_only`, `read_write`) e escopo por pool. As permissões são declaradas em `infra/modules.yaml`, armazenadas na coluna `module_config` dos usuários, embutidas no JWT e avaliadas localmente na UI via `makePermissions()` — zero latência extra no hot path.

A sessão também completou os testes do recurso `contact_insights` que havia sido implementado em sessão anterior mas estava sem cobertura de testes.

---

## 1. auth-api — extensão ABAC (Tasks #17–#18)

### Novos elementos

**`infra/modules.yaml`** — declaração canônica dos módulos e seus campos de permissão:

```yaml
modules:
  evaluation:
    label: "Avaliação"
    fields:
      formularios:  { label: "Formulários e Campanhas", type: permission }
      revisar:      { label: "Revisar avaliações",       type: permission }
      contestar:    { label: "Contestar avaliações",     type: permission }
      relatorio:    { label: "Relatórios de avaliação",  type: permission }
      permissoes:   { label: "Gerenciar permissões",     type: permission }
  analytics:
    label: "Analytics"
    fields:
      dashboards:   { label: "Dashboards",              type: permission }
      relatorios:   { label: "Relatórios",              type: permission }
      admin:        { label: "Visão consolidada",        type: permission, scope: pool }
  billing:
    label: "Faturamento"
    fields:
      visualizar:   { label: "Visualizar fatura",       type: permission }
      exportar:     { label: "Exportar XLSX",           type: permission }
      admin:        { label: "Gerenciar recursos",      type: permission }
  config:
    label: "Configuração"
    fields:
      visualizar:   { label: "Visualizar configurações", type: permission }
      editar:       { label: "Editar configurações",     type: permission }
  registry:
    label: "Registry"
    fields:
      pools:        { label: "Pools",                   type: permission }
      agent_types:  { label: "Tipos de agente",         type: permission }
      skills:       { label: "Skills",                  type: permission }
  skill_flows:
    label: "Skill Flows"
    fields:
      visualizar:   { label: "Visualizar flows",        type: permission }
      editar:       { label: "Editar flows",            type: permission }
  campaigns:
    label: "Campanhas"
    fields:
      visualizar:   { label: "Visualizar campanhas",    type: permission }
      admin:        { label: "Gerenciar campanhas",     type: permission }
  workflows:
    label: "Workflows"
    fields:
      visualizar:   { label: "Visualizar instâncias",   type: permission }
      admin:        { label: "Gerenciar workflows",     type: permission }
```

**`auth.module_registry` (PostgreSQL):**
```sql
CREATE TABLE auth.module_registry (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    module_id  TEXT NOT NULL UNIQUE,
    label      TEXT NOT NULL,
    fields     JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);
```

**`module_config` em `auth.users`:**
```sql
ALTER TABLE auth.users
  ADD COLUMN module_config JSONB NOT NULL DEFAULT '{}';
```

Estrutura de `module_config`:
```json
{
  "evaluation": {
    "formularios": { "access": "read_write", "scope": [] },
    "revisar":     { "access": "read_only",  "scope": ["pool_sac"] },
    "contestar":   { "access": "none",       "scope": [] }
  }
}
```

**Novos endpoints:**

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/auth/modules` | Lista módulos e campos do `module_registry` |
| `PATCH` | `/auth/users/{id}/module-config` | Atualiza `module_config` de um usuário (admin) |

**`seed_modules_from_yaml(conn, yaml_path)`** — função chamada no lifespan do auth-api. Lê `infra/modules.yaml`, faz upsert em `auth.module_registry`. Idempotente.

**JWT — campo `module_config`:** embutido no access token junto com os demais claims. Propagado para `Session.moduleConfig` na platform-ui.

---

## 2. platform-ui — tipos e helpers ABAC (Tasks #18–#19)

### `src/types/index.ts` — novos tipos

```typescript
export type PermissionAccess = 'none' | 'read_only' | 'write_only' | 'read_write'

export interface ModuleFieldConfig {
  access: PermissionAccess
  scope:  string[]      // [] = acesso global; [...pool_ids] = acesso restrito a esses pools
}

export type ModuleConfig = Record<string, Record<string, ModuleFieldConfig>>
```

`Session` estendido com `moduleConfig?: ModuleConfig`.

### `src/lib/permissions.ts` — makePermissions()

```typescript
export interface Permissions {
  can(module: string, field: string, poolId?: string): boolean
  access(module: string, field: string): PermissionAccess
  scopeOf(module: string, field: string): string[]
  isGlobal(module: string, field: string): boolean
  fieldConfig(module: string, field: string): ModuleFieldConfig | undefined
}

export function makePermissions(moduleConfig?: ModuleConfig): Permissions
```

Hierarquia de `PermissionAccess`: `none < read_only < write_only < read_write`.

`can()` retorna `true` quando `access !== 'none'` e (scope vazio OU poolId está no scope).

`access()` retorna o nível de acesso do campo, ou `'none'` quando campo ausente.

**Graceful degradation:** quando `moduleConfig` é `undefined` ou `{}`, `makePermissions()` ainda retorna um objeto `Permissions` válido, mas:
- `can()` → sempre `true`
- `access()` → sempre `'read_write'`
- `scopeOf()` → sempre `[]`
- `isGlobal()` → sempre `true`

Isso garante que usuários sem `module_config` (accounts legados, admins que não foram configurados) passem por todas as verificações ABAC sem bloqueio, dependendo apenas do RBAC (roles) para filtrar acesso.

---

## 3. platform-ui — AccessPage e ModulePermissionForm (Task #20)

### `src/modules/config/access/AccessPage.tsx`

Rota: `/config/access` (role: `admin`). Interface de gestão de permissões ABAC por usuário.

**Funcionalidades:**
- Lista de usuários do tenant com pesquisa por email/nome
- Painel lateral: visualização do `module_config` atual do usuário selecionado
- `ModulePermissionForm` para edição
- Salvar via `PATCH /auth/users/{id}/module-config`

### `src/components/ModulePermissionForm.tsx`

Formulário reativo para edição do `module_config`. Para cada módulo + campo:
- Select de `access`: `none | read_only | write_only | read_write`
- Multi-select de pools (quando `type: permission, scope: pool` no YAML)
- Preview JSON do `module_config` resultante

O formulário carrega a lista de módulos de `GET /auth/modules` e constrói os controles dinamicamente — sem listas hardcoded de módulos na UI.

---

## 4. platform-ui — Sidebar ABAC (Task #21)

### `src/shell/Sidebar.tsx`

Adicionada interface `NavItem.abac?: { module: string; field: string }` e helper `passesAbac()`:

```typescript
function passesAbac(item: NavItem): boolean {
  if (!item.abac) return true
  if (!session?.moduleConfig || Object.keys(session.moduleConfig).length === 0) return true
  return perms.can(item.abac.module, item.abac.field)
}
```

**Graceful degradation:** usuários sem `moduleConfig` passam automaticamente (`passesAbac → true`) — RBAC continua sendo o único filtro para esses usuários.

**Evaluation nav — 6 gates adicionados:**

| Item de menu | ABAC gate |
|---|---|
| Formulários e Campanhas | `evaluation.formularios` |
| Campanhas | `evaluation.formularios` |
| Revisar | `evaluation.revisar` |
| Minhas Avaliações | `evaluation.contestar` |
| Relatórios | `evaluation.relatorio` |
| Permissões | `evaluation.permissoes` |

O filtro é aplicado em dois pontos: `filteredItems` (para itens de nível raiz) e o mapeamento interno de `item.children` dentro de `renderNavItem`.

---

## 5. platform-ui — Evaluation ABAC (Tasks #22–#23)

### `EvaluationPermissionsPage.tsx` (Task #22)

Página existente atualizada para usar `makePermissions()` internamente, garantindo que ações de gestão de permissões só sejam possíveis quando o usuário tem `evaluation.permissoes` com `access !== 'none'`.

### `MyEvaluationsPage.tsx` (Task #23)

`canContestAbac` computado a partir do `moduleConfig` da sessão:

```typescript
const perms = makePermissions(session?.moduleConfig)
const hasModuleConfig = !!(session?.moduleConfig && Object.keys(session.moduleConfig).length > 0)
const canContestAbac = !hasModuleConfig || perms.can('evaluation', 'contestar')
```

`ResultCard` recebe `canContestAbac` e ANDa com as condições de estado:
```typescript
const canContest = canContestAbac && !result.locked
  && result.eval_status !== 'contested'
  && result.eval_status !== 'rejected'
```

### `ReviewPage.tsx` (Task #23)

`reviewAccess` computado com três estados possíveis:

```typescript
const reviewAccess = (!hasModuleConfig ? 'read_write'
  : perms.access('evaluation', 'revisar')) as 'none' | 'read_only' | 'read_write'
```

`ReviewModal` renderiza footer diferente por estado:
- `read_write` — formulário completo de aprovação/rejeição
- `read_only` — aviso azul "pode visualizar mas não tem permissão para aprovar"
- `none` — aviso amarelo "solicite permissão ao administrador"

---

## 6. analytics-api — testes contact_insights (Task #15)

A tabela `contact_insights`, o consumer, o query helper e o endpoint `GET /reports/contact-insights` já haviam sido implementados em sessão anterior. Esta sessão adicionou a cobertura de testes que estava faltando.

### Novos testes em `test_consumer.py`

Classe `TestParseConversationsEventInsight` (12 métodos):

| Teste | O que valida |
|---|---|
| `test_returns_contact_insights_row` | Nome da tabela de destino |
| `test_insight_id_preserved` | `insight_id` mantido quando fornecido |
| `test_insight_type_equals_event_type` | Ex: `"insight.historico.cancelamento"` |
| `test_category_and_value_mapped` | Payload flat corretamente mapeado |
| `test_tags_propagated` | Lista de tags preservada |
| `test_agent_id_from_agent_id_field` | Campo `agent_id` direto |
| `test_agent_id_from_instance_id_fallback` | Fallback para `instance_id` |
| `test_category_from_nested_data` | Payload aninhado em `data: {}` |
| `test_insight_id_generated_when_absent` | UUID gerado automaticamente |
| `test_unknown_event_type_returns_none` | Eventos não-insight ignorados |
| `test_missing_session_id_returns_none` | Validação de campos obrigatórios |
| `test_missing_tenant_id_returns_none` | Validação de campos obrigatórios |

Classe `TestWriteRowDispatchContactInsight` (2 métodos):

| Teste | O que valida |
|---|---|
| `test_contact_insight_dispatched` | Roteia para `store.insert_contact_insight` |
| `test_contact_insight_does_not_touch_other_stores` | Sem side effects em outras tabelas |

### Novos testes em `test_reports.py`

Classe `TestQueryContactInsightsReport` (4 métodos):

| Teste | O que valida |
|---|---|
| `test_returns_data_rows` | Count + data rows retornados corretamente |
| `test_category_filter_appends_condition` | Filtro `category` injeta cláusula SQL |
| `test_tags_filter_appends_has_condition` | Filtro `tags` injeta `has()` por tag |
| `test_error_returns_empty_with_error_key` | Falha CH retorna `{"data": [], "error": "..."}` |

**Total analytics-api após Task #15:** 143/143 testes passando.

---

## Resumo de impacto

| Área | Antes | Depois |
|---|---|---|
| Controle de acesso | RBAC (role) apenas | RBAC + ABAC (role + module_config) |
| Granularidade | Por página | Por funcionalidade dentro da página |
| Configuração | Hardcoded em roles | Declarativa em `infra/modules.yaml` |
| Propagação | Apenas roles no JWT | roles + module_config no JWT |
| Avaliação | Sem filtro de ação | Botões de contestação/revisão filtrados |
| Sidebar | Filtrado por role | Filtrado por role + ABAC gate |
| Testes analytics | contact_insights sem testes | 14 novos testes (12 consumer + 2 reports) |
