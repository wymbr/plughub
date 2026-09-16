# Fatos do código — base da skill `platform-ui-change`

> Levantados em 2026-09-16 (`pui/` = `packages/platform-ui/`). **(conferido)** = relido no fonte nesta
> data; os demais vieram de levantamento com citação de linha. Linhas mudam — abra antes de citar.

## Rotas, menu e ABAC

| Fato | Onde |
|---|---|
| Rotas; `/` com `ProtectedRoute` + `Shell` | `pui/src/app/routes.tsx:87-93`; router em `pui/src/app/App.tsx:7` |
| `console`, `evaluation/reports` e `audit` sem guarda de rota **(conferido)** | `routes.tsx` (entradas `path: 'console'`, `'evaluation/reports'`, `'audit'`) |
| `RequireAbac` / `RequireEvalAccess` | `pui/src/auth/RequireEvalAccess.tsx:30`, `:80` |
| `navItems` e filtro do menu | `pui/src/shell/Sidebar.tsx:44`, `:238-251` |
| `passesAbacRule`: sem regra ⇒ visível; `module` sem `field` ⇒ visível **(conferido)** | `pui/src/lib/permissions.ts:202-212` |
| Gate lê `href`+`abac` na mesma linha; 35 entradas, 31 vistas **(conferido, AUT-57)** | `infra/test/probe_nav_route_guard_agreement.sh:71-75` |

## i18n

| Fato | Onde |
|---|---|
| Registro em três lugares; `lng`/`fallbackLng` `en` **(conferido)** | `pui/src/i18n/index.ts:5-58`, `:60-118`, `:119-128` |
| Namespace `service` ← `atendimento.json` **(conferido)** | `index.ts:17`, `:45` |
| `nav.*` no namespace `shell` | `Sidebar.tsx:33` |
| Paridade só de `contacts`; duplicatas e rótulos ABAC | `probe_i18n_contacts_parity.sh` · `probe_i18n_duplicate_keys.sh` + `_i18n_dupes.py` · `probe_abac_field_labels_i18n.sh` |

## Cor

| Fato | Onde |
|---|---|
| Tokens | `pui/tailwind.config.ts:12-48` |
| `SERIES_COLORS` **(conferido)** | `pui/src/components/TimeseriesChart/constants.ts:7` |
| Paletas inline duplicadas | `modules/evaluation/CalibrationDashboard.tsx:30`, `modules/contacts/ContactLensChart.tsx:45`, `modules/analise/AgentsBenchPage.tsx:118` |

## API e sessão

| Fato | Onde |
|---|---|
| `apiFetch`: Bearer + reauth único no 401 **(conferido)** | `pui/src/api/apiFetch.ts:29-51` |
| Token em memória; single-flight | `pui/src/auth/token-store.ts:8-16`, `:50-59` |
| Refresh em `localStorage`, `buildSession`, `perms`, `accessiblePools` | `pui/src/auth/AuthContext.tsx:6-8`, `:141-167`, `:193-228`, `:288-378` |
| Proxy dev | `pui/vite.config.ts:14-134` (`/config` com bypass de `text/html` em `:87-95`) |
| Allowlist nginx de `/config/*` **(conferido)** | `pui/Dockerfile` (location `^/config/(access|…|outbound)/?$`) |

## Build

| Fato | Onde |
|---|---|
| Scripts `dev`/`build`/`preview`/`typecheck`, sem test/lint **(conferido)** | `pui/package.json:4-9` |
| Imagem node → nginx; compose sem volumes, porta 5174 | `pui/Dockerfile`; `docker-compose.demo.yml` serviço `platform-ui` |

## Docs atrás do código

`docs/arcos/platform-ui.md` e `docs/standards/frontend-architecture.md` ainda descrevem `roles` no
NavItem, claim `unrestricted`, locale padrão `pt-BR`, `nav` em `common`, namespace `modules` e gating
por papel. Todos removidos ou diferentes no código em 2026-09-16.
