# Casos medidos — o porquê de cada regra da skill `security-boundaries`

> Texto movido **integralmente** de `CLAUDE.md` § *Security — Section 9.5* em 2026-09-16,
> extraído por script (sem redigitar). O `SKILL.md` traz a forma operacional; aqui fica a medição.
>
> ## ⚠️ Correções de 2026-09-16 — leia antes do texto abaixo
>
> A seção foi conferida contra o código ao ser movida, e quatro passagens estavam velhas. O texto
> original ficou intacto abaixo, porque é o registro do raciocínio de cada data; **onde divergir,
> vale esta lista**:
>
> 1. **`LEGACY_EMPTY_MEANS_UNRESTRICTED` já é `False`** (AUT-03, 2026-08-31, `py-authz/__init__.py`).
>    O parágrafo *"São DOIS verificadores"* descreve a inversão como futura (*"hoje '[]' = todos"*,
>    *"o que o passo 3 ainda terá de auditar"*). Ela aconteceu e foi provada ao vivo com controle
>    positivo e negativo (`admin@` 36 pools → 100 linhas; `probe@` `[]` → 0), e as cópias TypeScript
>    seguiram na AUT-23 (gate `probe_ts_scope_resolvers.sh`). Hoje **`[]` = NENHUM pool**.
> 2. **O guard de injeção não está só em `notification_send` e `conversation_escalate`.** Mora em
>    `mcp-server-plughub/src/infra/injection_guard.ts`, e `withGuard` (`infra/tool-guard.ts`) envolve
>    **15 tools** no registro (`tools/bpm.ts` e `tools/workflow.ts`), além do `invoke` do
>    `external-mcp` (`lib/invoke-audit.ts`).
> 3. **`probe_internal_service_callers.sh` está como `?` NÃO TRIADO** no `gates.manifest`: existe,
>    mas o `run_gates.sh` não o executa.
> 4. **"Tool permission filtering … Empty = no filtering"** está correto sobre o código
>    (`ai-gateway/inference.py:128`), mas o `CLAUDE.md` § *What Never To Do* mede que
>    `/v1/inference` **não tem chamador** e `permissions` nunca é setado: é filtro sem caminho vivo.
>
> Os números datados (19 de 73 rotas, 35·2·2 escopadas/isentas/dívidas, 10 de 947) são do dia em
> que foram medidos; re-meça antes de citá-los como estado atual.

---

**UM verificador de JWT+ABAC, e ele é `packages/py-authz`.** *(2026-08-27; a migração dos seis
foi concluída em 2026-08-28.)* Todo portão que responde *"este chamador pode?"* a partir de um JWT
do auth-api usa `plughub_authz` (`verify_user_jwt` · `abac_can` · `bearer_from_header` ·
`enforce_write` · `resolve_scope`/`pool_in_scope`) — nunca uma cópia. **`abac_can` também decide o
recorte de CAPACIDADE por pool** (parâmetro `scope_id`, com o alias `pool:x` × `x` normalizado numa
casa só): é eixo distinto do `resolve_scope`, que recorta LINHAS de relatório. A regra é medida, não
estética: quando ela foi escrita já existiam **seis** implementações independentes, e elas
**divergiam em seis pontos** (biblioteca; ordem de acesso, onde `analytics-api/audit.py` trata
`write_only` como maior que `read_only` e os outros os colapsam; `module_config` vazio, que a
`evaluation-api` LIBERA no ramo legado; `min_access` desconhecido, que em três serviços vira rank
0 e deixa **qualquer** grant passar; 401 × 403 para credencial ausente; e quatro posturas
distintas para segredo ausente). Tabela completa no cabeçalho do pacote.
**O agravante que dá o nome à regra:** `channel-gateway/auth.py` já *prometia no docstring*
ser o ponto compartilhado, e cinco serviços reimplementaram — promessa sem mecanismo, a mesma
família do DDL de `participation_intervals`. Gate `infra/test/probe_authz_single_verifier.sh`
(reprova a sétima cópia; a migração dos seis é dívida registrada no `TODO.md`, **não** exigida
pelo gate). *Aquele arquivo migrou no passo 3 (2026-08-28) e hoje é camada fina — deixou de ser
cópia depois de deixar de mentir, nessa ordem; a inversa teria sido cosmética.* **Linha de base do
gate: 7 arquivos em 6 serviços → 1**, e esse 1 é o EMISSOR (`auth-api/jwt_utils.py`), que fica com
`python-jose` por decisão — quem assina e quem confere têm de ser cada um o seu lado. **A linha de
base não deve ir a zero**: se for, alguém migrou o emissor sem decidir isso.

> **Ao mover uma fronteira de autorização, MEÇA o que a cerca antes de confiar no verde.** Em
> **cinco dos sete passos** deste arco os testes ao redor da fronteira estavam para trás — campo ABAC
> em português que nunca existiu (pricing/config), portão de resume sem teste nenhum
> (channel-gateway), nada atravessando a rota (analytics), 17 vermelhos herdados de um split
> anterior (auth-api), a porta de autenticação descoberta (evaluation). Em **três** deles quem
> revelou foi a **bateria de mutação**, não a suíte. E o modo de falha é sempre o mesmo: *o vermelho
> de um controle POSITIVO parece proteção*, que é justamente o que se queria ver. Corolário: ao
> fechar um portão, escreva o caso que prova que ele **deixa alguém passar** — o negativo sozinho
> passa pelo motivo errado.

> **Ramo legado de autorização morre CONTADO, nunca por decreto nem por inércia** *(passo 6)*. A
> `evaluation-api` liberava revisão e contestação a token com `module_config` vazio, e o que tornou
> isso insustentável não foi a política — foi a **contradição interna**: o mesmo serviço já negava o
> transcript ao mesmo token desde 2026-08-27, então ele *não podia LER* a conversa e *podia DECIDIR*
> sobre ela. **Duas respostas para a mesma pergunta dentro do mesmo arquivo significam que a mais
> permissiva é a que vale.** Antes de fechar, contou-se a população: **um** portador na instalação, a
> fixture do probe grant-first. Onde houver usuário ativo sem grants, o caminho é **backfill** com
> `presets.build_module_config`, nunca manter a porta.*

> **São DOIS verificadores, e o segundo passou meses sem mecanismo** *(consolidado 2026-08-28)*.
> `abac_can` responde *"quais FUNÇÕES posso exercer"*; o resolvedor de **escopo de pool**
> (`resolve_scope` · `pool_in_scope`) responde *"quais LINHAS/POOLS eu alcanço"*. Eixos
> independentes — confundi-los é o defeito que fez o claim `unrestricted` liberar o menu, corrigido
> no mesmo dia em que nasceu. O de escopo tinha **três** cópias (`analytics-api/pool_auth.py`,
> `channel-gateway/auth.py`, `evaluation-api/router.py`), todas com o marcador
> `LEGADO_POOLS_VAZIO`, e **o probe não contava nenhuma**: ele conta quem DECODIFICA JWT, e essas
> três só consomem claims já decodificados. **Regra derivada: um censo desenhado para um eixo não
> prova nada sobre o eixo vizinho** — a cobertura tem de ser afirmada por eixo, nunca herdada.
>
> A urgência era o **passo 3** do plano de `accessible_pools`, que inverte o significado de `[]`
> (hoje "todos", depois "nenhum"): inversão aplicada a duas das três cópias é vazamento de escopo
> que degrada **mudo**. Hoje o interruptor é único (`LEGACY_EMPTY_MEANS_UNRESTRICTED`), com a
> tabela-verdade dos **dois** estados escrita. ⚠️ O que o passo 3 ainda terá de auditar por call
> site: depois da inversão `resolve_scope` devolve `[]`, e todo consumidor que fizer
> `if not pools: <sem filtro>` transforma restrição geral em **liberação** geral.
> Gate: C4 do mesmo probe, via `infra/test/_scope_resolver_census.py` — que é AST, não `grep`,
> porque `grep` acusava os sete produtores do auth-api (o emissor **escreve** os campos; escrever
> não é decidir o que a ausência significa).

> **E há um TERCEIRO eixo: COBERTURA DE ROTA** — *descoberto 2026-08-28 na T3 do ADR de
> relatórios, com censo próprio e fechado em 2026-08-29*. Os dois censos acima contam
> **quem decide**: quem decodifica JWT (C1), quem resolve escopo de pool (C4). Nenhum
> conta **quais rotas exigem que alguém decida** — e uma rota sem dependência nenhuma não
> tem decisor para contar, então atravessa os dois intacta. É a regra da seção acima pela
> terceira vez (*"um censo desenhado para um eixo não prova nada sobre o eixo vizinho"*),
> e desta vez o eixo é o mais grosseiro dos três: a rota simplesmente não pede nada.
>
> **O recorte do achado não era o do eixo.** O achado falava de `/reports/*` porque veio
> de um arco de relatórios; o censo AST mediu **19 rotas descobertas em 73**, e as sete
> fora daquele prefixo incluíam a pior de todas — `GET /sessions/{id}/stream`, que servia
> a **transcrição inteira do contato** a quem chamasse, medida ao vivo. O agravante é a
> forma: a rota IRMÃ que existe para servir esse mesmo dado (`/v1/transcript/sessions/{id}`)
> já exigia credencial. **Duas portas para o mesmo dado e só uma trancada** — e a trancada
> é o que dá a impressão de que o dado está protegido.
>
> Hoje: **18 gateadas, 1 isenta NOMEADA** (`/v1/health`, liveness do compose — exigir
> credencial ali acopla o boot da stack ao boot do emissor de token). Gate:
> `infra/test/probe_route_credential_coverage.sh`, em duas metades que não se substituem —
> **(A)** censo AST (`_route_principal_census.py`) e **(B)** medição ao vivo, porque um
> `Depends` declarado num router que ninguém inclui não gateia nada.
>
> **EXIGIR CREDENCIAL e RECORTAR LINHA são dois fatos.** O primeiro fechou em 2026-08-29;
> o segundo fechou **para o CONTEÚDO** em 2026-08-30 e segue aberto para os AGREGADOS —
> e a linha divisória não é de esforço, é de natureza da pergunta.
>
> **Conteúdo ✅ — e são DOIS eixos, sendo que o segundo só chegou em 2026-09-08 (MOD-07).**
> As quatro rotas que servem UM contato (`/v1/transcript/sessions/{id}`,
> `/sessions/{id}/stream`, `/workflow-trace`, `/pipeline-state`) recortam por pool desde
> 2026-08-30, e agora também exigem CAPACIDADE: `contacts.transcricao` para o diálogo
> verbatim, `contacts.visualizar` para os traços de execução — o campo é declarado pela
> ROTA, como no `requireAbacWrite` e no `_NS_FIELD_OVERRIDES`. Até ali a analytics-api
> tinha **um eixo só**, e o efeito foi medido ao vivo: token com `contacts.monitorar` e
> **sem** `contacts.visualizar` lia a transcrição inteira (**200**). *Gate decorativo é
> pior que gate nenhum — quem concede acredita ter negado.* A capacidade decide ANTES do
> escopo (recusa não paga Redis + ClickHouse), e `module_config` **ausente** (serviço)
> difere de **vazio** (usuário sem grants, que NEGA). Ali a pergunta é de **PERTINÊNCIA** (*esta sessão é dos meus pools?*), não de
> coluna, e por isso é decidível — mesma razão pela qual `/sessions/active` sempre recortou.
> O decisor é **único**: `pool_auth.authorize_session_scope`, para onde o
> `_authorize_live_session` do supervisor passou a DELEGAR. **Duas metades, e a segunda era
> o bloqueio:** o resolvedor existente só decidia sessão VIVA (Redis), e metade do tráfego é
> sessão fechada do ClickHouse — gatear só a metade viva trocaria um buraco por um buraco
> INTERMITENTE. O irmão fechado (`resolve_closed_session_pools`) usa a MESMA união do
> predicado de lista (`_session_scope_clause`: entrou por pool meu **ou** um pool meu
> atendeu), e faz **duas consultas, nunca um `JOIN`** — há `session_id` em `segments` sem
> linha em `sessions`, e num `JOIN` a ausência de linha viraria recusa disfarçada de escopo.
> **Onde as duas DIVERGEM, e é decisão:** a lista trata `pool_id = ''` como VISÍVEL (para o
> contato aparecer desde a chegada); o conteúdo **RECUSA** o indeterminável, porque uma
> sessão ainda não roteada está VIVA e a metade viva a resolve — indeterminável *no
> ClickHouse* é sessão que fechou sem nunca ser atribuída. Medido antes de escolher: **10 de
> 947** (1,06%), todas detritos de teste. A recusa **loga nomeando**
> (`session_scope_undeterminable`), que é o que transforma 1% numa lista se virar 10%.
>
> **Agregados ✅ — fechado em 2026-08-31 (AUT-01), e a chave foi não decidir por rota.**
> As `query_*` não aceitavam `accessible_pools` — filtro que não existia, não argumento
> esquecido. Vazamento medido ao vivo, com controle positivo na MESMA rodada: `admin@`
> (36 pools) e um chamador escopado a UM pool liam números IDÊNTICOS em `/usage`,
> `/evaluations`, `/evaluations/{summary,quality}`, `/agent-events/{summary,categories}` e
> `/customers/{id}/360`, enquanto `/sessions` movia 386→323.
>
> **A pergunta não é "qual coluna é o pool desta agregação?"** — essa forma pede 13
> escolhas, e 13 escolhas é errar ao menos uma em silêncio (o precedente é a F2: um filtro
> de canal que não filtrava, **esvaziava**). A pergunta é **"a linha carrega o pool como
> fato PRÓPRIO, ou o pool é fato de OUTRA coisa que ela referencia?"**, que tem três
> respostas e **um predicado cada**: **F-A** pool-nativo (`_apply_pool_scope`) · **F-B**
> derivado-de-sessão (`_session_derived_scope_clause`, que DELEGA ao `_session_scope_clause`
> da F1b — escrever ali um `pool_id IN (…)` recriaria a cópia que autoriza pelo pool de
> ENTRADA) · **F-C** indecidível, que vira **dívida DECLARADA**.
>
> **Isenção é DECLARADA, nunca deduzida da ausência** — e são DUAS tabelas, porque
> *"decidimos não recortar"* (`_SCOPE_EXEMPT`, sem gatilho) e *"ainda não sabemos
> recortar"* (`_SCOPE_DEBT`, com gatilho) são fatos diferentes; juntá-las faria a dívida
> herdar a tranquilidade da decisão. Estado: **35 escopadas · 2 isentas · 2 dívidas**.
>
> ⚠️ **A recusa por escopo NÃO é viável hoje, e isso é medição.** A primeira versão da F-C
> devolvia 403 ao chamador escopado; `admin@plughub.local` carrega uma LISTA de 36 pools
> (`accessible_pools is None` só acontece para principal de SERVIÇO), então ela recusava o
> administrador de verdade para defender ZERO linha — a D14.1 ao contrário.
>
> ⚠️ *Correção de algumas horas depois:* esta passagem dizia *"volta a ser opção quando
> o admin for `unrestricted`"*, e o campo `unrestricted` foi **REMOVIDO** no mesmo dia
> (AUT-15) — a dependência estava invertida. Com escopo sempre ENUMERADO, o
> discriminador teria de ser *"este escopo cobre o universo de pools do registry?"*,
> dependência nova e com caminho de degradação próprio. Ver AUT-29.
>
> Gates: `infra/test/probe_report_row_scope.sh` (A: censo AST · B: ao vivo, com controle
> positivo obrigatório e `SEM AMOSTRA` em vez de verde por ausência) e
> `infra/test/probe_session_content_scope.sh` (conteúdo, 6 ramos).

> **Fechar credencial numa API obriga a MIGRAR os chamadores internos, e eles não têm
> usuário** *(medido 2026-08-30)*. O fechamento de 08-29 gateou 18 rotas e não tocou em
> chamador nenhum: **quatro** falavam com a analytics-api sem header, e o modo de falha é o
> do catálogo — três degradavam para um **zero plausível** (`scanned=0` no backfill de
> campanha · `active_sessions: 0` no `handoff-status`, que existe para decidir se um deploy é
> SEGURO · `toolTrace=[]` na evidência do tier-2), e só um dava erro visível (502 na tela de
> Qualidade). Hoje eles apresentam `X-Service-Token`, e a **postura é aditiva**: o header
> ACRESCENTA uma porta e **nunca remove a exigência** — token vazio no serviço **não libera**
> (é o oposto de `_require_service` da evaluation-api, onde vazio é no-op por herança de demo
> aberto; replicar aquilo reintroduziria o *ABAC opt-in do chamador* que 08-27 fechou). O
> principal de serviço é **irrestrito e por isso é uma IDENTIDADE**: `sub="service:<nome>"`
> viaja para log e trilha, porque um chamador interno legítimo precisa alcançar pools que não
> são "dele" (o backfill enumera a campanha inteira) — o que não pode é o alcance ser
> **anônimo**. **A auditoria LGPD fica FORA**: o serviço não fura `_check_audit_access`,
> decisão tomada contra a medição de que a fonte daquele leitor está VAZIA (política contra
> população zero é o erro que a decisão #4 desta semana firmou). Gate:
> `infra/test/probe_internal_service_callers.sh` (7 ramos).
>
> ⚠️ **`catch {}` sobre uma checagem de SEGURANÇA é pior que a falha que ele esconde.** O
> `handoff-status` não estava só sem credencial: `ANALYTICS_API_URL` **nunca foi setada** no
> agent-registry deste compose, então a chamada dava `fetch failed` e o `catch` a convertia em
> *"0 sessões ativas"* — uma promoção com **24** sessões vivas parecia segura, e assim foi
> desde sempre. **Todo caminho que degrada num número diz por que degradou.**
>
> ⚠️ **E o achado NÃO era novo — ele estava escrito desde 2026-08-27**, com o mesmo
> diagnóstico e **o mesmo conserto prescrito**, no `TODO.md`. Foi re-derivado do zero em
> 08-30 porque ninguém procurou. Com o `TODO.md` em ~7 900 linhas, **achado que não é
> procurado é achado que se paga duas vezes** — e a segunda vez pode contradizer a primeira
> sem ninguém notar. Regra barata: **antes de registrar uma descoberta, `grep` do sintoma no
> `TODO.md` e no `CHANGELOG.md`.**

**Escrita de config exige portão; LEITURA de config nem sempre — e isso é decidido, não
omitido.** `calendar-api` e `dialog-api` gateiam escrita (`config.calendars` / `config.dialog_forms`,
`read_write`) e mantêm abertas as rotas que chamadores de **runtime sem credencial** consomem:
`/v1/engine/*` (workflow-api, scheduler-api, mailing-api decidem a janela de contato) e os `GET`
do dialog (`form_get` do mcp-server, survey web). Um portão que feche a leitura **passa** no teste
de segurança e quebra o produto em silêncio — por isso o gate carrega testemunhas dos dois lados.
Gate: `infra/test/probe_config_service_write_gate.sh`.

**Tool permission filtering**: `InferenceRequest.permissions` from JWT → `InferenceEngine.infer()` filters tool list. Empty = no filtering (backward-compatible).

**Injection guard** (`injection_guard.ts`): 13+ heuristic regex patterns. Applied in `notification_send` (message) and `conversation_escalate` (pipeline_state). Future: apply at proxy sidecar level for all domain tool calls.

---
