# Módulo: Configuração → Controle de Acesso

> Rota UI: `/config/access` | Roles: admin

## O que é

O módulo de Controle de Acesso gerencia usuários, roles e permissões granulares (ABAC) da plataforma. Usa o auth-api como backend. Permite criar usuários, definir roles globais e configurar permissões por módulo e por escopo (pool ou global).

## Sistema de autenticação

**JWT HS256** com dois tokens:
- **Access token** (TTL 1h) — carregado em memória no frontend, nunca em localStorage
- **Refresh token** (TTL 7 dias) — armazenado em localStorage, usado para renovação automática

Renovação automática 60 s antes da expiração. Ao montar a UI com refresh_token válido no localStorage, o token de acesso é restaurado sem mostrar o formulário de login.

## RBAC — Roles globais

| Role | Acesso base |
|---|---|
| `operator` | Monitor, Agent Assist, Analytics |
| `supervisor` | operator + Avaliação, Relatórios |
| `admin` | supervisor + Configuração, AgentFlow |
| `developer` | admin + Developer Tools |
| `business` | Home, Analytics, Business (cross-cutting: incluído em todos os grupos, mas com gates ABAC operacionais aplicados) |

## ABAC — Permissões por módulo e campo

O sistema ABAC complementa o RBAC com permissões granulares. Cada módulo registra seus campos de permissão em `infra/modules.yaml`. As permissões são armazenadas em `auth.users.module_config` (JSONB) e incluídas no JWT.

### Módulos e campos principais

| Módulo | Campos de permissão | Gate |
|---|---|---|
| `contacts` | `operacao`, `visualizar`, `exportar` | Monitor + AgentAssist; Análise |
| `workflows` | `operacao`, `visualizar`, `cancelar`, `webhooks` | Editor, Monitor, Calendar; Report |
| `skill_flows` | `operacao`, `visualizar`, `editar` | Editor, Monitor, Deploy; Report |
| `evaluation` | `contestar`, `revisar`, `relatorio`, `formularios` | Ações de contestação/revisão; Reports; Forms |
| `billing` | `visualizar`, `gerenciar` | Acesso ao módulo de Faturamento |
| `config` | `plataforma`, `recursos`, `canais`, `usuarios`, `mascaramento` | Abas de Configuração |
| `agent_assist` | `atender`, `supervisionar` | Atender contatos; entrar como supervisor |
| `campaigns` | `visualizar`, `gerenciar` | Visualização e gestão de campanhas |

### Hierarquia de acesso por campo

```
none < read_only < write_only < read_write
```

### Escopo de acesso

- `scope: []` — acesso global (todos os pools/campanhas)
- `scope: ["pool:retencao_humano", "pool:sac"]` — restrito a pools específicos

## AccessPage — CRUD de usuários

Tabela de usuários com filtros. Modal de criação/edição inclui:
1. **Campos básicos**: email, nome, senha, roles (multi-select), accessible_pools ([] = acesso total)
2. **ModulePermissionForm**: acordeões colapsáveis por módulo, `<select>` por campo (None/Read Only/Write Only/Read Write), input de escopo com chips remoníveis quando `scopable: true`

**Criação**: `POST /auth/users` → `PUT /auth/users/{id}/module-config` (dois steps).
**Edição**: `PATCH /auth/users/{id}` + `PUT /auth/users/{id}/module-config` (simultâneos via Promise.all).

## Resolução de permissão no frontend

`makePermissions(session?.moduleConfig)` retorna um `PermissionChecker` puro:

```typescript
const perms = makePermissions(session?.moduleConfig)

perms.can('contacts', 'operacao')                          // tem qualquer acesso?
perms.can('evaluation', 'revisar', 'read_write')           // tem read_write?
perms.can('evaluation', 'revisar', 'read_write', 'pool:sac') // tem acesso ao pool?
perms.access('evaluation', 'contestar')                    // 'none'|'read_only'|...
perms.scopeOf('evaluation', 'revisar')                     // [] ou ['pool:retencao']
```

**Graceful degradation**: usuários sem `moduleConfig` no JWT (contas legacy) têm visibilidade baseada apenas em `role`. O helper `makePermissions(undefined)` retorna permissões que sempre retornam `false` para campos específicos mas não quebra a UI.

## APIs envolvidas

| Endpoint | Auth | Descrição |
|---|---|---|
| `POST /auth/login` | — | Login email+senha → tokens |
| `POST /auth/refresh` | body | Renovação de tokens |
| `POST /auth/logout` | body | Invalida refresh token |
| `GET /auth/me` | Bearer | Claims do access token |
| `GET /auth/users?tenant_id=` | X-Admin-Token | Lista usuários |
| `POST /auth/users` | X-Admin-Token | Cria usuário |
| `PATCH /auth/users/{id}` | X-Admin-Token | Atualiza usuário |
| `DELETE /auth/users/{id}` | X-Admin-Token | Remove usuário |
| `PUT /auth/users/{id}/module-config` | X-Admin-Token | Salva permissões ABAC |
| `GET /auth/modules?active_only=true` | — | Lista módulos com permission_schema |

## Pacotes envolvidos

| Pacote | Responsabilidade |
|---|---|
| `auth-api` | Users CRUD, JWT HS256, refresh token rotation, module_registry seed, module_config (porta 3200) |
| `platform-ui` | `modules/access/AccessPage.tsx`, `auth/AuthContext.tsx`, `lib/permissions.ts`, `components/ModulePermissionForm.tsx` |

## Referências

- Backend: `packages/auth-api/`
- Frontend: `packages/platform-ui/src/modules/access/`, `packages/platform-ui/src/auth/`
- Tipos: `packages/platform-ui/src/types/index.ts` (Session, ModuleConfig, PermissionAccess)
- Módulos YAML: `infra/modules.yaml`
