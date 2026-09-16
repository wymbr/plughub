---
name: platform-ui-change
description: Como mudar o platform-ui do PlugHub (React + Vite + Tailwind em packages/platform-ui) sem quebrar i18n, ABAC do menu, tokens de cor ou a rota servida pelo nginx — e como ver a mudança no navegador. Use ao criar ou editar página, módulo (src/modules), rota (app/routes.tsx), item de menu (shell/Sidebar.tsx), guarda RequireAbac/RequireEvalAccess, texto visível, chave de locale (i18n/locales en e pt-BR), namespace i18n, cor, gráfico recharts, chamada de API (apiFetch, x-tenant-id), proxy do vite ou nginx do Dockerfile, e ao validar uma tela no browser (login, Console).
---

# platform-ui-change — a tela, o menu, a rota e o backend dão a MESMA resposta

O platform-ui é **um app só** (nunca `packages/<outra>-ui/`), servido no demo por **nginx sobre a
imagem buildada** — não há HMR nem bind-mount. Fatos com `arquivo:linha` em
[`references/fatos-do-codigo.md`](references/fatos-do-codigo.md).

⚠️ **Os dois docs de frontend estão atrás do código** (`docs/arcos/platform-ui.md`,
`docs/standards/frontend-architecture.md`): ainda ensinam `roles` no item de menu, o claim
`unrestricted`, locale padrão `pt-BR`, `nav.*` em `common` e um namespace `modules`. **Nada disso
existe.** Onde divergirem, **vale o código** — e o doc precisa ser corrigido na mesma entrega.

## 1. Página nova

1. **Módulo:** `src/modules/<feature>/` (nome em inglês). Componentes compartilhados em
   `src/components/`.
2. **Rota:** entrada em `src/app/routes.tsx`, filha de `/` (dentro de `ProtectedRoute` + `Shell`),
   **envolvida pela guarda** com o MESMO par do menu:
   `<RequireAbac module="…" field="…">` ou `<RequireEvalAccess field|anyOf>`.
3. **Menu:** item em `navItems` (`src/shell/Sidebar.tsx`) com `label: t('nav.<x>')`, `href`, `icon`
   e `abac: { module, field }` ou `{ module, anyOf: [...] }`.
   - **Grant-first:** sem grant o item some; não há bypass de papel nem de `module_config` vazio.
   - ⚠️ Item **sem** `abac`, ou com `module` sem `field`/`anyOf`, fica **sempre visível**.
   - ⚠️ O gate de concordância menu × rota **só lê `href` e `abac` na mesma linha e só `field:`**
     (`AUT-57`): escreva a entrada com os dois na mesma linha até o gate ser consertado, e confira
     a guarda da rota **à mão**.
4. **Campo ABAC novo?** Declare em `infra/modules.yaml` (+ rótulo nos dois locales de `access`) e
   recrie o auth-api com `--force-recreate` (skill `deployment` § 4). O campo do menu tem de ser o
   que o backend exige.
5. **Rota sob `/config/*`:** o nginx do `Dockerfile` só devolve o SPA para uma **allowlist**
   (`access|billing|platform|masking|…|outbound`); o resto vai ao config-api, e link direto/F5 dá
   JSON de erro (`ROT-01`). Acrescente o segmento à lista, ou a tela nova quebra no F5.

## 2. Texto visível

- **Todo texto por `t()`**, com `useTranslation('<namespace>')` no componente; helper fora de
  componente **recebe `t` por parâmetro**.
- **Chave nos DOIS locales**: `src/i18n/locales/en/<ns>.json` e `pt-BR/<ns>.json`. Identificador
  (chave, namespace, rota, variável) em **inglês**; português só no valor pt-BR.
- **Namespace novo** se registra em **três lugares** de `src/i18n/index.ts`: import, `resources` e a
  lista `ns`. ⚠️ O namespace `service` é carregado de `atendimento.json` (nome de arquivo ≠ namespace).
- `nav.*` mora no namespace **`shell`**. Padrão e fallback: `en`.
- **Nunca repetir chave no mesmo objeto** (a última apaga a anterior, e paridade não detecta):
  `probe_i18n_duplicate_keys.sh`.
- **Nunca gravar resultado de `t()` em store** (título de cartão, rótulo): grava-se o id, traduz-se
  no render.
- ⚠️ **Não existe gate de paridade EN × pt-BR geral** — só para `contacts`. Confira as duas árvores
  de chaves do seu namespace à mão.

## 3. Cor e gráfico

- **Tokens Tailwind**, nunca hex inline em JSX (`tailwind.config.ts`: `primary`, `secondary`,
  `accent`, `green`, `warning`, `red` e variantes).
- **Gráfico (recharts)** recebe cor como STRING, onde classe não se aplica: use
  `SERIES_COLORS` de `src/components/TimeseriesChart/constants.ts`, **não** uma paleta nova inline.
  Há três paletas duplicadas no código e 520+ hex antigos — dívida, não modelo (`PUI-01`).
- O scanner `plughub-review` acusa `inline-hex` nas linhas adicionadas.

## 4. Chamar backend

- **`apiFetch`** (`src/api/apiFetch.ts`): anexa o Bearer do token em memória e renova UMA vez no
  401. `fetch` cru perde os dois — `probe_ui_credential_coverage.sh` mede as chamadas sem Bearer.
- **Tenant não é global**: cada chamada passa `x-tenant-id` com o `tenantId` do `useAuth()`.
- **Sessão:** access token só em memória; refresh token em `localStorage` (`plughub_refresh_token`);
  `module_config`, `perms` e `accessiblePools` vêm do `useAuth()`.
- **Dev:** proxy em `vite.config.ts` (rotas específicas ANTES do catch-all `/v1` → 3300). **Imagem:**
  nginx inline no `Dockerfile`. Rota nova de backend precisa entrar **nos dois**.
- ⚠️ Usuário recém-criado tem `accessible_pools = []` = **nenhum pool**: tela vazia não é bug até
  alguém lhe dar escopo.
- **Nunca oferecer na UI uma opção que o backend recusa** — a tela lê o mesmo catálogo/validador.

## 5. Build e verificação

**Typecheck** — o WSL não tem `node` Linux (só o `npm` do Windows em `/mnt/c`), então rode num
container, pelo `tsc` LOCAL, e confira que ele leu o projeto (medido 2026-09-16: 275 arquivos de
`src/`, ~15 s). `npx tsc` pode baixar um `tsc` avulso e sair `0` sem compilar nada:

```bash
docker run --rm -v /home/a1/projects/plughub:/repo -w /repo/packages/platform-ui node:20-alpine sh -c 'node_modules/.bin/tsc --noEmit -p . --listFiles | grep -c /packages/platform-ui/src/; echo rc=$?'
```

Chame por script em arquivo (skill `deployment` § 6) — o `$?` não sobrevive ao `wsl.exe -- bash -c`.

**Ver no demo:**

```bash
docker compose -f docker-compose.demo.yml build platform-ui
docker compose -f docker-compose.demo.yml up -d platform-ui
```

- **Não há suíte de teste nem lint** no pacote (sem vitest/jest, sem `*.test.*`): o `typecheck` e os
  gates de `infra/test/` são o que existe. Não afirme "testado" por typecheck verde.
- **Sem rebuild da imagem, o browser mostra a versão antiga** (nginx serve `dist` da imagem).
- Gates que tocam a UI: `probe_nav_grant_first.sh` · `probe_nav_route_guard_agreement.sh` ·
  `probe_nav_backend_field_agreement.sh` · `probe_analise_route_guard.sh` · `probe_apifetch_reauth.sh`
  · `probe_ui_credential_coverage.sh` · `gate_orphan_ui_callers.sh` · `probe_config_route_collision.sh`
  · `probe_dashboard_card_title.sh` · `probe_i18n_duplicate_keys.sh` · `probe_abac_field_labels_i18n.sh`.

## 6. Ver no navegador

Em `http://localhost:5174`, o login **completa** com esta receita (medida):
1. **clique real** no e-mail e `type`;
2. **`Tab`** para a senha — clicar no segundo campo não move o foco;
3. `type` da senha e **clique no botão** — `Enter` não submete, e `form_input` não dispara os
   eventos do React.

Teste a tela com **dois usuários**: um COM o grant (vê e opera) e um SEM (não vê o item, e a URL
direta recusa). Só o primeiro é o controle positivo que parece suficiente.
