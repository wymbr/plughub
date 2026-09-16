---
name: security-boundaries
description: Como abrir, fechar e testar uma fronteira de autorização no PlugHub — JWT e ABAC via plughub_authz (verify_user_jwt, abac_can, resolve_scope, pool_in_scope, enforce_write), escopo de pool (accessible_pools), credencial de rota, X-Service-Token de chamador interno, auditoria LGPD, borda do channel-gateway, permissão e injection guard de tool MCP. Use ao criar ou alterar rota HTTP, endpoint de relatório ou query_* da analytics-api, portão de escrita de config, campo ou módulo ABAC (module_config, infra/modules.yaml), grant de usuário ou grupo, chamada entre serviços, tool MCP, exposição de rota na borda, e ao revisar ou testar qualquer checagem de permissão, 401/403 ou vazamento de dado entre pools.
---

# security-boundaries — uma pergunta de acesso tem UMA resposta

Quase todo vazamento medido neste repositório foi **duas respostas para a mesma pergunta**: uma
cópia do verificador que decidia diferente, uma porta irmã sem tranca, um eixo que ninguém contava.
A regra operacional é: **use a casa única, declare o eixo, prove os dois lados.** O porquê de cada
regra, com medição, está em [`references/casos-medidos.md`](references/casos-medidos.md) — leia as
**correções de 2026-09-16** no topo dele antes de citar um número de lá.

## 1. Os eixos são independentes — e cada um tem o seu censo

| Eixo | Pergunta | Casa única | Gate |
|---|---|---|---|
| **Credencial de rota** | a rota exige que alguém decida? | `Depends` de principal | `probe_route_credential_coverage.sh` (AST + ao vivo) |
| **Capacidade (ABAC)** | posso exercer esta FUNÇÃO? | `plughub_authz.abac_can(module, field, min_access, scope_id)` | `probe_authz_single_verifier.sh` |
| **Escopo de linha** | alcanço estes POOLS/LINHAS? | `resolve_scope` · `pool_in_scope` | C4 do mesmo probe · `probe_ts_scope_resolvers.sh` |
| **Pertinência de conteúdo** | ESTA sessão é dos meus pools? | `pool_auth.authorize_session_scope` (viva + fechada) | `probe_session_content_scope.sh` |
| **Recorte de agregado** | este número mistura pools que não vejo? | F-A `_apply_pool_scope` · F-B `_session_derived_scope_clause` · F-C dívida | `probe_report_row_scope.sh` |

**Um censo desenhado para um eixo não prova nada sobre o vizinho.** Verde num não autoriza
afirmar cobertura noutro.

## 2. Rota nova ou alterada — checklist

1. **Exige principal** (`Depends`). Isenção só **nomeada** (ex.: `/v1/health`), nunca por omissão.
2. **Capacidade declarada PELA ROTA** (campo ABAC explícito, como `requireAbacWrite` /
   `_NS_FIELD_OVERRIDES`), e decidida **antes** do escopo — recusa não paga Redis nem ClickHouse.
3. **Escopo de pool depois**, com a semântica atual:
   - `resolve_scope` devolve **lista** para usuário; **`[]` = NENHUM pool** (AUT-03).
   - `None` (irrestrito) **só** para principal de SERVIÇO construído explicitamente.
   - ⚠️ **Nunca `if not pools: <sem filtro>`** — transforma restrição geral em liberação geral.
4. **`module_config` ausente ≠ vazio**: ausente é principal de serviço; vazio é usuário sem grants,
   e **NEGA**. Ausência de grant nunca é autorização (grant-first).
5. **Duas portas para o mesmo dado**: procure a rota irmã que serve a mesma coisa e gateie as
   duas igual. A trancada dá a impressão de que o dado está protegido.
6. **Conteúdo de UM contato**: resolva sessão viva **e** fechada (gatear só a viva cria buraco
   intermitente); indeterminável **recusa** e **loga nomeando** (`session_scope_undeterminable`).
7. **Agregado novo (`query_*`)**: classifique a linha — **F-A** carrega o pool como fato próprio;
   **F-B** o pool é da sessão que ela referencia (delegue a `_session_scope_clause`, nunca
   `pool_id IN (…)` escrito à mão); **F-C** indecidível → linha em `_SCOPE_DEBT` com gatilho.
   Isenção decidida vai em `_SCOPE_EXEMPT`. **Nunca deduzir isenção da ausência.**

## 3. Verificador: nunca uma cópia

- Todo portão sobre JWT do auth-api usa **`plughub_authz`** (`verify_user_jwt` · `abac_can` ·
  `bearer_from_header` · `enforce_write` · `resolve_scope`/`pool_in_scope`). Sétima cópia reprova.
- **Exceção deliberada:** o EMISSOR (`auth-api/jwt_utils.py`) fica com `python-jose` — quem assina
  e quem confere são lados diferentes. A linha de base do gate é **1**, nunca 0.
- Cópia TypeScript do resolvedor de escopo tem de concordar com o Python (AUT-23); mudou um,
  rode `probe_ts_scope_resolvers.sh`.
- Docstring que promete *"este é o ponto compartilhado"* sem mecanismo é como nasceram as seis
  cópias. Corrija o comentário ou crie o mecanismo.

## 4. Chamador interno (serviço → serviço)

- **Fechar credencial numa API obriga a MIGRAR os chamadores internos no MESMO trabalho** — eles não
  têm usuário e degradam para um **zero plausível** (`scanned=0`, `active_sessions: 0`).
  Antes de fechar: `grep` de quem chama a rota em todo `packages/`.
- **`X-Service-Token` é ADITIVO**: acrescenta uma porta, **nunca** remove a exigência. Token vazio
  no serviço **não libera**. O principal é irrestrito e por isso é **identidade**:
  `sub="service:<nome>"` vai para log e trilha.
- **`catch {}` sobre checagem de segurança é proibido.** Todo caminho que degrada num número diz
  por que degradou.
- Auditoria LGPD fica **fora** do principal de serviço (`_check_audit_access` não é furado).

## 5. Config

- **Escrita de config exige portão** (`config.<campo>`, `read_write`).
- **Leitura de runtime pode ficar aberta — por DECISÃO, com testemunha** (ex.: `/v1/engine/*` do
  calendar-api, `GET` do dialog-api). Um portão que feche a leitura passa no teste de segurança e
  quebra o produto calado. Gate: `probe_config_service_write_gate.sh`.
- **Administrar pessoa ≠ conceder capacidade**: `config.users` × `config.permissions`, com quatro
  portas (rota, corpo por `model_fields_set`, alvo, escopo). Papel é preset de nascimento, não
  portão. Detalhe: `CLAUDE.md` § Arc 7.

## 6. Auditoria LGPD

- Portão `_check_audit_access` (analytics-api): cinco ramos, **503** para segredo ausente (dado
  pessoal não degrada aberto), recusa **nomeia** quem foi barrado.
- **Grave a trilha ANTES de responder**, inclusive na recusa — portão dentro de `Depends` que
  levanta antes do handler deixa a recusa fora da trilha. Nunca `enforce_write` ali.
- `audit_access_log` **nunca é deduplicado**.

## 7. MCP e borda

- **Permissão de tool viaja ASSINADA no `session_token`** (`"{mcp_server}:{tool}"`), nunca como
  argumento da chamada — seria o chamador declarando a própria autorização.
- **Injection guard**: tool nova com entrada de texto livre é registrada com
  `withGuard("<tool>", handler)` (`mcp-server-plughub/src/infra/tool-guard.ts`).
- **Borda do channel-gateway é ALLOWLIST** de sete prefixos (`/channel` `/survey` `/webhooks`
  `/voice` `/webrtc` `/ws` `/webchat`); prefixo novo exige linha em `probe_edge_surface.sh`.
  `/v1`, `/health`, `/docs`, `/redoc`, `/openapi.json` são internos.

## 8. Testar um portão

1. **Meça o que cerca a fronteira antes de confiar no verde** — em 5 de 7 passos do arco de
   consolidação os testes ao redor estavam para trás.
2. **Escreva o caso que prova que o portão DEIXA PASSAR** quem deve. O negativo sozinho passa pelo
   motivo errado; o vermelho de um controle positivo parece proteção.
3. **Bateria de mutação** para gate de autorização (skill `testing-pattern` § 3). Em três passos,
   foi ela — não a suíte — que achou o defeito.
4. **Gate decorativo é pior que nenhum**: conceda o grant ERRADO ao vivo (ex.: `contacts.monitorar`
   sem `contacts.visualizar`) e confirme a recusa.
5. **Ramo legado morre CONTADO**: antes de fechar, conte os portadores; se houver usuário ativo,
   backfill (`presets.build_module_config`), nunca manter a porta.
6. **Política contra população zero é erro**: recusa por escopo que barra o admin real para
   defender zero linha é pior que a dívida declarada.
7. **Ao medir ao vivo**, usuário recém-criado tem `accessible_pools = []` e vê zero linhas por
   design — não é defeito. Fixture de escopo total é EFÊMERA (`mk_unrestricted_principal.sh
   --revogar` no `trap`).
