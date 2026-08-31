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
| AUT-27 | **As 14 suítes Python voltaram ao verde: 2 629 testes, zero vermelhos.** Eram **10** falhas em 3 serviços, todas com uma raiz só — instrumentos escritos quando `[]` significava "todos" e quando o claim `unrestricted` existia. Cauda do arco de 2026-08-31, **não regressão**. Reescritos como **TESTEMUNHAS, nunca apagados**: apagar deixaria o caminho livre para a porta larga voltar sem nada acusar. Dois merecem registro: **(a)** `test_ramo_LEGADO_pools_vazio_ainda_libera_e_e_CONTADO` trazia no docstring *"é este teste que vai ficar vermelho para avisar, em vez de o escopo vazar mudo"* — **o instrumento funcionou exatamente como prometido**, e o que faltava era escrever a versão pós-virada; **(b)** os dois do gate de credencial do `/stream` mediam uma proposição ADJACENTE à que os nomeia — aferem se o `?token=` é LIDO e falhavam por ESCOPO, então o vermelho fiel teria publicado *"o `?token=` parou de ser aceito"*, que é falso. Hoje o veredicto é sobre o **401** (única resposta que significa "credencial não foi lida"), com teto de 5xx para não passar por acidente. Entrou junto `test_ramo_LEGADO_esta_DESLIGADO`, que guarda o interruptor: religá-lo devolve o tenant inteiro a todo token de lista vazia, **sem erro e sem log de erro** | 2026-08-31 | `CHANGELOG.md` § suítes Python |
| AUT-12 | **Papel semeado virou config — e a mudança revelou um IMPASSE DE BOOTSTRAP.** `roles` era o único campo do admin semeado fora da config (e-mail, senha e nome já eram); default passou de `admin,developer` para **`admin`** (decisão do dono), medido antes: **zero** campos do catálogo concedem a `developer` sem conceder a `admin`, e os dois gates de papel restantes na UI aceitam `admin`. A env **recusa alto** papel desconhecido ou lista vazia, porque papel fora do catálogo não casa com preset e o admin nasceria cego. ⚠️ **O achado:** `create_user` não grava `module_config` — o preset era aplicado só pelo ROUTER, e o seed chama `create_user` direto. Medido: `module_config = '{}'`, isto é, admin **sem menu** sob o grant-first e **sem poder se conceder nada**. Não mordia porque o `seed_auth.py` provisiona pela API — o ambiente funcionava por um caminho DIFERENTE do que o código afirmava. Hoje a aplicação vive em `presets.apply_role_preset`, **um caminho e dois chamadores**. Testemunhas novas (a classe só afirmava `True`/`False`; *"criou"* e *"nasceu com grants"* são dois fatos) mais a **negativa** — papel sem `role_defaults` não grava nada. Suíte **65 verdes**; mutação (seed volta a não aplicar) reprova exatamente a testemunha nova | 2026-08-31 | `CHANGELOG.md` § papel semeado |
| AUT-14 | **Pool sem vigia virou fato visível, e só ele é alarme.** Três peças, como decidido: **(a)** alarme na barra lateral **só para ÓRFÃO** (zero usuários ativos) — "preso a UM" não entra, porque seria exposição publicada como dano (D14.1) e daria 31 de 36, isto é, 86% da população; **(b)** contagem de vigias por pool como **dado** ao lado da linha, nunca emblema; **(c)** a guarda no instante em que exposição vira dano — **três** caminhos tiram vigia de circulação (modal, desativar na lista, apagar) e todos passam por `confirmaSemVigia`, um decisor só. **Avisa, nunca impede:** pool sem vigia é config válida; o que não pode é ser silenciosa. ⚠️ **Achado que teria invertido o valor do alarme:** `GET /auth/users` pagina com `limit` default **100**, e a tela pedia sem parâmetro — um censo sobre lista cortada publica **órfão FALSO**, que é exatamente como se ensina a ignorar alarme. Hoje o teto é explícito e, se a lista puder estar cortada, o censo **declara que não sabe** em vez de afirmar ausência. Módulo puro `access/pool-coverage.ts`; gate `infra/test/probe_pool_coverage.sh` (11 casos, compila o arquivo de PRODUÇÃO em `strict` + `noUncheckedIndexedAccess`), falseável por mutação: contar inativo reprova 3 casos, acusar órfão preexistente reprova 1 | 2026-08-31 | `CHANGELOG.md` § pool sem vigia |
| AUT-24 | **As suítes TS voltaram a ser instrumento de regressão: 596 verdes.** Duas causas, e a segunda não era de autorização. **(a)** `agent-registry` 6 vermelhos `expected 401 to be…` — mas o diagnóstico real era pior que "teste atrás do portão": **o veredicto dependia de ambiente não declarado** e errava dos DOIS lados (sem `PLUGHUB_JWT_SECRET` a suíte ficava VERDE **sem nunca exercer o portão**; com ele, vermelha). Hoje o teste declara o próprio env (`vi.hoisted`, que roda antes dos imports — `config.ts` lê `process.env` no import) e **limpa o service-token de propósito**, para exercer o ramo Bearer+ABAC que a UI usa; consertar pelo atalho teria apagado a cobertura. **(b)** Ao remover o 401 apareceram **três falhas de outra natureza** que ele escondia (mocks velhos: `publishRegistryChanged` e `poolSkillSlot`). **(c)** `mcp-server` 1 vermelho: o produto estava CERTO — `conversation_escalate` passou a recusar sem `tenant_id` (*"identidade não tem fallback"*) e o teste afirmava o contrato antigo. Entraram **4 testemunhas novas** que não existiam: 401 sem credencial, 403 sem `config.resources`, 403 com `read_only`, e a do fail-closed da escalação — sem elas, apagar o portão ou reintroduzir o tenant inventado deixaria tudo verde. Gate novo `infra/test/probe_ts_suites.sh` (gêmeo do Python: execução a partir da IMAGEM + cobertura NOMEADA), provado falseável por mutação | 2026-08-31 | `CHANGELOG.md` § suítes TS |
| AUT-19 | **`[]` passou a ter UM significado.** Credencial ausente/inválida devolve **401**; escopo legitimamente vazio segue **200 + `[]`** — ambiguidade **eliminada**, não rotulada. Servidor: união discriminada (`unrestricted` × `scoped` × `unauthorized`) que o compilador **obriga** a tratar, nas duas cópias TS. **Achado no caminho:** `GET /pools/:id/queue` **não tinha verificação nenhuma** — servia os `session_id` da fila de qualquer pool sem credencial, enquanto a irmã (que devolve só CONTAGENS) já escopava; era a porta destrancada do par, e agora exige credencial + escopo (403 fora do domínio). Cliente: re-auth **reativo** no `apiFetch`, com **single-flight** — que não é otimização: o refresh token é ROTATIVO, e N renovações concorrentes se invalidam, matando a sessão quando ela tenta se salvar. Uma retentativa, nunca duas. Chamador migrado junto (`smoke_admission_licensing.sh` chamava sem credencial). Gates: `probe_ts_scope_resolvers.sh` estendido (401 nas TRÊS rotas + 403 de escopo, com controle positivo) e `probe_apifetch_reauth.sh` novo (compila os arquivos de PRODUÇÃO e os exerce; 7 casos), ambos provados falseáveis por mutação | 2026-08-31 | `CHANGELOG.md` § `[]` com um significado só |
| AUT-23 | **As duas copias TS passaram a seguir a AUT-03 — e o `agent-registry` deixou de degradar ABERTO sem header.** Dois ramos: (a) `[] -> null` (legado, que o py-authz ja tinha virado — Python e TypeScript discordavam sobre o mesmo claim); (b) o early-return de "sem header Authorization" ficava **antes do `try`**, e por isso o ramo homonimo do log era **INALCANCAVEL** — foi o log impossivel que denunciou a AUT-17 registrada por mim como se cobrisse os dois casos. Medido ao vivo, com controle positivo em toda rodada: `admin@` 36 pools / 291 instancias intactos; `probe@` (claim `[]`), sem header e token invalido -> **0/0** nas duas rotas (antes: 36 pools). Gate novo `infra/test/probe_ts_scope_resolvers.sh`, em duas metades provadas NAO redundantes por mutacao: trocar `return []` por `return null` mantendo o log deixa a metade estatica **verde nas quatro** e reprova so a viva. Suites conferidas contra baseline do `HEAD`: identicas (as 7 falhas sao herdadas, AUT-24) | 2026-08-31 | `CHANGELOG.md` § copias TS do resolvedor |
| AUT-06 | **A UI parou de inferir "todos" de lista vazia** — e eram **cinco** sítios, não um: `AgentAssistContext`, `PoolDomainSelect`, `FlowMonitorPage`, `AgentsPage` (×2), `AnaliseSurveysPage` e `CustomerVoicePage`. Os quatro últimos usavam a variável local `dom` e escaparam do primeiro censo, que só casava `accessiblePools` na mesma linha — quinta vez neste arco que um instrumento teve eixo cego | 2026-08-31 | `CHANGELOG.md` § `accessible_pools: []` |
| AUT-11 | **O `admin@` do demo recebeu os 36 pools explicitamente**, pela API oficial (`PATCH /auth/users/{id}`) e não por `UPDATE` — o invariante de provisionamento vale também para dado de demo. Verificado no token: 36 pools, `unrestricted` ausente. Achado no caminho: o serviço rodava o código ANTIGO porque `docker cp` não recarrega processo Python vivo — os 63 testes verdes eram de outro processo. Só depois do `restart` o token saiu correto | 2026-08-31 | `CHANGELOG.md` § `accessible_pools: []` |
| AUT-18 | **116 chamadas do browser migradas para `apiFetch`** (44 arquivos), em duas levas: 58 com rota literal e 58 com URL montada por variável — estas invisíveis ao primeiro censo, que só via literais. `fetch` cru sem credencial: **65 → 24**, e 23 dos 24 são o `registry.ts`, excluído por ser cross-origin (AUT-20). O `probe_ui_credential_coverage.sh` segue **VERDE**. Dois erros do meu script, pegos por verificação e não pelo verde: import inserido dentro de bloco multi-linha (2 arquivos) e uma substituição dentro de COMENTÁRIO (revertida) | 2026-08-31 | `CHANGELOG.md` § `accessible_pools: []` |
| AUT-17 | **O escopo deixou de degradar ABERTO em token INVÁLIDO.** ⚠️ **Corrigido em 2026-08-31 por medição:** esta linha dizia "token ausente/inválido" nas DUAS cópias, e isso é falso para o `agent-registry`, onde `if (!auth.startsWith("Bearer ")) return null` **antecede o `try`** — o caso sem header nunca alcança o `catch` e segue irrestrito. O próprio código denuncia: o ramo `"sem header Authorization"` do log é **inalcançável**. No `mcp-server` a correção vale inteira, porque `verifyJwtPayload` levanta. Resíduo em **AUT-23**. Metade indispensável junto: `CampaignsPage` usava `fetch` cru e só funcionava por causa do fail-open — migrado para `apiFetch`. O consumidor foi conferido: `[]` é truthy em JS, vira `Set` vazio e filtra tudo (não repete o `if not x` do lado Python) | 2026-08-31 | `CHANGELOG.md` § `accessible_pools: []` |
| AUT-16 | **As duas cópias TypeScript do resolvedor de escopo** perderam a cascata do claim — `mcp-server/server.ts` e `agent-registry/routes/operational.ts`. Nenhum censo as contava: o `probe_authz_single_verifier` conta quem DECODIFICA JWT, e estas consomem claims já decodificados | 2026-08-31 | `CHANGELOG.md` § `accessible_pools: []` |
| AUT-08 | **O parâmetro morto saiu de `resolve_supervisor_scope`** — e ao remover descobriu-se que não era morto: `router.py:188` lia a COLUNA do banco e a função ainda concedia escopo total de supervisão no login, mesmo sem o claim no token. `role` e `unrestricted` saíram da assinatura juntos | 2026-08-31 | `CHANGELOG.md` § `accessible_pools: []` |
| AUT-13 | **`unrestricted` deixou de ser emitido e de ser lido.** O ramo `claim → irrestrito` saiu de `resolve_scope` (py-authz) e o claim saiu do token (`jwt_utils`, mais os 2 call sites do builder). Escopo de usuário passa a ser **sempre uma lista**; `None` sobrevive só para principal de SISTEMA, construído explicitamente. Inerte hoje — o ramo legado ainda resolve irrestrito para `[]`, então nada quebrou. Testes reescritos com **testemunha negativa** (com o legado desligado, `unrestricted: True` recebe `[]`, não o tenant): sem ela, reintroduzir o ramo passaria despercebido, porque com o legado ligado os dois desenhos devolvem `None` pelo mesmo caminho. Suítes: **83 + 63 verdes** | 2026-08-31 | `CHANGELOG.md` § `accessible_pools: []` |
| AUT-07 | **O seed do admin deixou de declarar escopo de pool.** `unrestricted=True` saiu de `seed_admin_if_absent` — era afirmação sobre POOLS feita antes de existir pool, e reintroduzia por SEED a porta larga que o passo 8 removeu do runtime. ⚠️ **Corrigido em 2026-08-31 (AUT-12) por medição:** esta linha dizia *"o bootstrap fecha pelo MÓDULO: `config.permissions` tem `role_defaults: admin: read_write`, logo o admin semeado concede escopo"* — e era **falso**. `create_user` grava `roles` e **não** grava `module_config`; quem aplicava o preset era só o router, e o seed chama `create_user` direto. Medido: o admin semeado nascia com `module_config = '{}'`, ou seja, **sem menu** sob o portão grant-first e **sem poder se corrigir**. Era promessa sem mecanismo, e load-bearing — foi com ela que este passo justificou remover o `unrestricted=True`. Hoje o seed aplica o preset (`presets.apply_role_preset`), e a afirmação passou a ser verdadeira. Suíte auth-api: **63 verdes**. Sem efeito hoje (o ramo legado ainda resolve irrestrito) e sem efeito no `admin@` existente (seed é if-absent) | 2026-08-31 | `CHANGELOG.md` § `accessible_pools: []` |
| AUT-05 | **Os 3 sítios que fundiam `[]` com `None` foram consertados** — `query_workflow_summary` (analytics-api), `list_survey_responses` e `list_results` (evaluation-api), este último preservando a regra de que a PRÓPRIA avaliação é sempre visível (sem pool algum sobra a posse, e só ela). Censo: FUNDE 8→6, e os 6 restantes são 1 falso positivo + 5 helpers protegidos por wrapper. Suítes: **237 + 747 verdes**. Gate novo `infra/test/probe_accessible_pools_scope.sh`, duas metades (censo + testemunha dos 3 consertos), ambas provadas falseáveis por mutação | 2026-08-31 | `CHANGELOG.md` § `accessible_pools: []` |
| AUT-04 | **Auditoria dos consumidores de `accessible_pools`** — censo AST (`infra/test/_accessible_pools_census.py`): **142 funções** tocam o campo · 41 distinguem `[]` de `None` · 93 apenas repassam · **8 decidem por truthiness**, das quais 4 são protegidas pelo wrapper e 1 é falso positivo ⇒ **3 vazamentos reais**, nomeados em AUT-05. O instrumento fica versionado e re-executável | 2026-08-31 | `CHANGELOG.md` § `accessible_pools: []` |
| AUT-02 | **ABAC TOTAL — arco de 8 passos completo**: split `config.users` → `users`+`permissions` · campos `config.calendars`/`dialog_forms`/`dashboards` e `nav.channels`→`config.channels` · papel vira **preset de seed** aplicado em `create_user` · grants do supervisor · portão único **grant-first** · queda dos dois bypasses (`unrestricted` vira a única porta larga) · remoção dos 7 `roles:` do `Sidebar.tsx` · cauda de papel no backend (4 sítios) | 2026-08-27 | `TODO.md:621` + `CHANGELOG.md` |

---

## `docs/adr/adr-abac-module-granularity-and-delegation.md` — granularidade e delegacao ABAC

*(nada fechado ainda)*

---

## `docs/product/journey-retorno-modelo-3-niveis-design.md` — Journey

| id | tarefa | fechada em | âncora |
|---|---|---|---|
| JRN-01 | Sinal N3 no drill da Vista Processos | 2026-07-23 | `CHANGELOG.md:17051` |
| JRN-02 | Guard de rota ABAC em `analise/*` | 2026-08-27 | `CHANGELOG.md:4533` |

---

## `sem-demanda`

*(vazio)*
