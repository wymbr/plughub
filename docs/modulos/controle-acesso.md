# Módulo: Configuração → Controle de Acesso

> Última atualização: 2026-05-25 · Estado: Arc 16

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

São 9 módulos ABAC registrados em `infra/modules.yaml`:

| Módulo | Campos de permissão | Gate |
|---|---|---|
| `contacts` | `operacao`, `visualizar`, `exportar` | Monitor + AgentAssist; Análise |
| `workflows` | `operacao`, `visualizar`, `cancelar`, `webhooks`, `journey.read`, `journey.resume` | Editor, Monitor, Calendar; Report; Journey API (Arc 16) |
| `skill_flows` | `operacao`, `visualizar`, `editar` | Editor, Monitor, Deploy; Report |
| `evaluation` | `contestar`, `revisar`, `relatorio`, `formularios` | Ações de contestação/revisão; Reports; Forms |
| `billing` | `visualizar`, `gerenciar` | Acesso ao módulo de Faturamento |
| `config` | `plataforma`, `recursos`, `canais`, `usuarios`, `mascaramento` | Abas de Configuração |
| `agent_assist` | `atender`, `supervisionar`, `operacao` | Atender contatos; entrar como supervisor; orquestração (Arc 11) |
| `campaigns` | `visualizar`, `gerenciar` | Visualização e gestão de campanhas |
| `audit` | `sessions`, `mcp_calls`, `user_access`, `data_requests`, `config_snapshot` | Audit LGPD — acesso DPO/compliance (`sessions` e `mcp_calls` ativos) |

Os campos `workflows.journey.read` e `workflows.journey.resume` (Arc 16) governam as MCP tools `journey_list_suspended`, `journey_resume` e `journey_check_pending`.

### Hierarquia de acesso por campo

```
none < read_only < write_only < read_write
```

### Escopo de acesso

- `scope: []` — acesso global (todos os pools/campanhas)
- `scope: ["pool:retencao_humano", "pool:sac"]` — restrito a pools específicos

### Claims de supervisor scope no JWT (Arc 9)

Além dos campos ABAC, o JWT carrega três claims de escopo de supervisão, denormalizados no momento do login/refresh via `resolve_supervisor_scope()` (resolução de turnos dos Grupos de Agentes):

| Claim | Significado |
|---|---|
| `supervised_groups[]` | IDs dos grupos sob supervisão no turno ativo |
| `supervised_agent_types[]` | agent_type_ids dos membros dos grupos supervisionados |
| `supervised_user_ids[]` | user_ids dos usuários nos grupos supervisionados |

`admin` → `([], [], [])` (sem restrição). Supervisor com grupos mas sem turno ativo → sentinel `["__no_active_shift__"]` para impedir interpretação de array vazio como "sem restrição". O analytics-api usa `supervised_agent_types` para filtrar todos os relatórios de agentes. Ver módulo Grupos de Agentes.

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
