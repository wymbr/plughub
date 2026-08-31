# done.md — índice do que FECHOU, agrupado por demanda

> **Este arquivo é ÍNDICE, nunca narrativa.** Cada linha traz id, tarefa, data e a **âncora no
> `CHANGELOG.md`**. O porquê, a medição e os achados moram lá — o `CHANGELOG.md` já é o arquivo de
> raciocínio deste repositório (625 entradas narrativas), e repetir aqui criaria uma quinta casa
> afirmando o mesmo fato, com a mais nova vencendo em silêncio.
>
> Os grupos espelham os de `pending.md`, inclusive quando vazios: o lugar de pousar uma tarefa
> fechada existe antes de ela fechar.

---

## `docs/adr/adr-contextstore-allowlist.md` — ContextStore como ALLOWLIST

*(V0–V3, V1b, V2b, D6, D7-metade, D8 e a FATIA 1 da D9 fecharam antes deste ledger existir —
história no `CHANGELOG.md`. Nada registrado aqui ainda.)*

---

## `docs/adr/adr-historico-unificado-duas-visoes.md` — ler um processo num lugar só

| id | tarefa | fechada em | âncora |
|---|---|---|---|
| HIS-03 | F4 — as duas visões na tela | 2026-08-25 | `CHANGELOG.md:5559` |

---

## `docs/adr/adr-journey-session-segment-model.md` — D12: a janela de espera

| id | tarefa | fechada em | âncora |
|---|---|---|---|
| WAI-02 | Produtor de espera para o tier `max_wait_exceeded` (fatia B) | 2026-08-24 | `CHANGELOG.md:6304` |

---

## `docs/adr/adr-voice-media-plane.md` — voz própria / Arc 15 WebRTC

*(nada fechado ainda)*

---

## `docs/product/identity-resolver-fase-a-plano.md` — identidade e comércio conversacional

*(Fases A e B fecharam antes deste ledger — história no `CHANGELOG.md`.)*

---

## `docs/adr/adr-human-approval-workflow-step.md` — aprovação humana

| id | tarefa | fechada em | âncora |
|---|---|---|---|
| APR-08 | v1 da aprovação humana — A1–A5, validadas E2E | 2026-07-17 | `CHANGELOG.md:16316` |

---

## `docs/adr/adr-work-item-requeue-and-agent-affinity.md` — pull direcionado e wrap-up

| id | tarefa | fechada em | âncora |
|---|---|---|---|
| PUL-03 | Arco A–F: posse conferida no submit, devolução por queda, resume terminal-uma-vez, Console lendo o 409 | 2026-08-04 | `CHANGELOG.md` § Fase F (D7) |

---

## `docs/arcos/customer-surveys.md` — módulo de pesquisas

*(nada fechado ainda)*

---

## `docs/arcos/arc7-auth.md` — ABAC e escopo

| id | tarefa | fechada em | âncora |
|---|---|---|---|
| AUT-03 | **`accessible_pools: []` passou a significar NENHUM pool.** `LEGACY_EMPTY_MEANS_UNRESTRICTED = False`. O caminho vazio **não ficou mudo**: virou `logger.info` que nomeia a origem e diz que é config válida, porque *"não vejo nada"* é o sintoma que chega e sem a linha ele é indistinguível de tela quebrada. **Provado ao vivo, com controle positivo e negativo no mesmo teste:** `admin@` (36 pools explícitos) → 100 linhas; `probe@` (0 pools) → 0 linhas. Antes da virada os dois viam as mesmas 100 | 2026-08-31 | `CHANGELOG.md` § `accessible_pools: []` |
| AUT-06 | **A UI parou de inferir "todos" de lista vazia** — e eram **cinco** sítios, não um: `AgentAssistContext`, `PoolDomainSelect`, `FlowMonitorPage`, `AgentsPage` (×2), `AnaliseSurveysPage` e `CustomerVoicePage`. Os quatro últimos usavam a variável local `dom` e escaparam do primeiro censo, que só casava `accessiblePools` na mesma linha — quinta vez neste arco que um instrumento teve eixo cego | 2026-08-31 | `CHANGELOG.md` § `accessible_pools: []` |
| AUT-11 | **O `admin@` do demo recebeu os 36 pools explicitamente**, pela API oficial (`PATCH /auth/users/{id}`) e não por `UPDATE` — o invariante de provisionamento vale também para dado de demo. Verificado no token: 36 pools, `unrestricted` ausente. Achado no caminho: o serviço rodava o código ANTIGO porque `docker cp` não recarrega processo Python vivo — os 63 testes verdes eram de outro processo. Só depois do `restart` o token saiu correto | 2026-08-31 | `CHANGELOG.md` § `accessible_pools: []` |
| AUT-18 | **116 chamadas do browser migradas para `apiFetch`** (44 arquivos), em duas levas: 58 com rota literal e 58 com URL montada por variável — estas invisíveis ao primeiro censo, que só via literais. `fetch` cru sem credencial: **65 → 24**, e 23 dos 24 são o `registry.ts`, excluído por ser cross-origin (AUT-20). O `probe_ui_credential_coverage.sh` segue **VERDE**. Dois erros do meu script, pegos por verificação e não pelo verde: import inserido dentro de bloco multi-linha (2 arquivos) e uma substituição dentro de COMENTÁRIO (revertida) | 2026-08-31 | `CHANGELOG.md` § `accessible_pools: []` |
| AUT-17 | **O escopo deixou de degradar ABERTO sem credencial.** As duas cópias TS devolviam irrestrito em token ausente/inválido; agora devolvem **domínio vazio**, e o log separa as duas populações (sem header × token inválido), que não são o mesmo fato. Metade indispensável junto: `CampaignsPage` usava `fetch` cru e só funcionava por causa do fail-open — migrado para `apiFetch`. O consumidor foi conferido: `[]` é truthy em JS, vira `Set` vazio e filtra tudo (não repete o `if not x` do lado Python) | 2026-08-31 | `CHANGELOG.md` § `accessible_pools: []` |
| AUT-16 | **As duas cópias TypeScript do resolvedor de escopo** perderam a cascata do claim — `mcp-server/server.ts` e `agent-registry/routes/operational.ts`. Nenhum censo as contava: o `probe_authz_single_verifier` conta quem DECODIFICA JWT, e estas consomem claims já decodificados | 2026-08-31 | `CHANGELOG.md` § `accessible_pools: []` |
| AUT-08 | **O parâmetro morto saiu de `resolve_supervisor_scope`** — e ao remover descobriu-se que não era morto: `router.py:188` lia a COLUNA do banco e a função ainda concedia escopo total de supervisão no login, mesmo sem o claim no token. `role` e `unrestricted` saíram da assinatura juntos | 2026-08-31 | `CHANGELOG.md` § `accessible_pools: []` |
| AUT-13 | **`unrestricted` deixou de ser emitido e de ser lido.** O ramo `claim → irrestrito` saiu de `resolve_scope` (py-authz) e o claim saiu do token (`jwt_utils`, mais os 2 call sites do builder). Escopo de usuário passa a ser **sempre uma lista**; `None` sobrevive só para principal de SISTEMA, construído explicitamente. Inerte hoje — o ramo legado ainda resolve irrestrito para `[]`, então nada quebrou. Testes reescritos com **testemunha negativa** (com o legado desligado, `unrestricted: True` recebe `[]`, não o tenant): sem ela, reintroduzir o ramo passaria despercebido, porque com o legado ligado os dois desenhos devolvem `None` pelo mesmo caminho. Suítes: **83 + 63 verdes** | 2026-08-31 | `CHANGELOG.md` § `accessible_pools: []` |
| AUT-07 | **O seed do admin deixou de declarar escopo de pool.** `unrestricted=True` saiu de `seed_admin_if_absent` — era afirmação sobre POOLS feita antes de existir pool, e reintroduzia por SEED a porta larga que o passo 8 removeu do runtime. O bootstrap fecha pelo MÓDULO: `config.permissions` tem `role_defaults: admin: read_write` e é `scopable: false`, logo o admin semeado concede escopo — a si mesmo inclusive — assim que houver pool. Suíte auth-api: **63 verdes**. Sem efeito hoje (o ramo legado ainda resolve irrestrito) e sem efeito no `admin@` existente (seed é if-absent) | 2026-08-31 | `CHANGELOG.md` § `accessible_pools: []` |
| AUT-05 | **Os 3 sítios que fundiam `[]` com `None` foram consertados** — `query_workflow_summary` (analytics-api), `list_survey_responses` e `list_results` (evaluation-api), este último preservando a regra de que a PRÓPRIA avaliação é sempre visível (sem pool algum sobra a posse, e só ela). Censo: FUNDE 8→6, e os 6 restantes são 1 falso positivo + 5 helpers protegidos por wrapper. Suítes: **237 + 747 verdes**. Gate novo `infra/test/probe_accessible_pools_scope.sh`, duas metades (censo + testemunha dos 3 consertos), ambas provadas falseáveis por mutação | 2026-08-31 | `CHANGELOG.md` § `accessible_pools: []` |
| AUT-04 | **Auditoria dos consumidores de `accessible_pools`** — censo AST (`infra/test/_accessible_pools_census.py`): **142 funções** tocam o campo · 41 distinguem `[]` de `None` · 93 apenas repassam · **8 decidem por truthiness**, das quais 4 são protegidas pelo wrapper e 1 é falso positivo ⇒ **3 vazamentos reais**, nomeados em AUT-05. O instrumento fica versionado e re-executável | 2026-08-31 | `CHANGELOG.md` § `accessible_pools: []` |
| AUT-02 | **ABAC TOTAL — arco de 8 passos completo**: split `config.users` → `users`+`permissions` · campos `config.calendars`/`dialog_forms`/`dashboards` e `nav.channels`→`config.channels` · papel vira **preset de seed** aplicado em `create_user` · grants do supervisor · portão único **grant-first** · queda dos dois bypasses (`unrestricted` vira a única porta larga) · remoção dos 7 `roles:` do `Sidebar.tsx` · cauda de papel no backend (4 sítios) | 2026-08-27 | `TODO.md:621` + `CHANGELOG.md` |

---

## `docs/product/journey-retorno-modelo-3-niveis-design.md` — Journey

| id | tarefa | fechada em | âncora |
|---|---|---|---|
| JRN-01 | Sinal N3 no drill da Vista Processos | 2026-07-23 | `CHANGELOG.md:17051` |
| JRN-02 | Guard de rota ABAC em `analise/*` | 2026-08-27 | `CHANGELOG.md:4533` |

---

## `sem-demanda`

*(vazio)*
