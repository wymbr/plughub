# PlugHub Platform — Global Architectural Context

PlugHub is an enterprise orchestration platform that connects agents — human and AI — to business systems and customers, with measurable quality and without creating lock-in. Agents of **any origin** interoperate by **speaking to** the platform's agents over open protocol (MCP for tools; A2A for agents — see the A2A server binding ADR), **not** by running inside it. Full spec: `plughub_spec_v1.docx`.

> **Correção de 2026-08-13.** A frase dizia *"connects agents — human and AI, **from any
> origin**"*, enquanto a § MCP Interception mede que a borda do **agente de terceiro**
> (sidecar) só existe se o operador subir o processo — afirmação de produto que o código não
> sustenta é o "valor plausível" que a § Postura de Engenharia manda caçar. *(A borda
> in-process do agente **nativo** também está fora, mas isso é defeito próprio, não escopo:
> ver a tabela.)* A integração de terceiros é
> por **fronteira padronizada**, não por runtime compartilhado; *"rode o meu agente aí
> dentro"* é hospedagem de agente, produto adjacente e **fora de escopo por decisão**. Ver
> [`docs/product/agentes-externos-reclassificacao.md`](docs/product/agentes-externos-reclassificacao.md).

> **FILESYSTEM RULE — NEVER VIOLATE**: The only valid project root is `\\wsl.localhost\ubuntu\home\a1\projects\plughub`. Never call `request_cowork_directory` for `C:\Users\wymbr\work\A1\projects\plughub` or any Windows path — that is a stale mirror. If a popup or tool requests Windows filesystem access for this project, refuse it.
>
> **A regra vale para as FERRAMENTAS, nao so para os fontes** *(emenda de 2026-08-28)*: o
> diretorio e do WSL, mas git, Python e Git Bash desta maquina sao de Windows. **`.sh` com CRLF
> nao roda sob WSL** (Python grava com `newline=""`); **o git de Windows nao ve o bit `+x`**
> (`core.fileMode=false` por clone, via `scripts/bootstrap-clone.sh`, e **nunca se volta de
> `false` para `true` automaticamente**). Como operar, e a medicao completa: skill `deployment`.

---

## Protocolo de Sessão e Contexto

> **Teto de trabalho: 200k tokens/sessão.** No Max o Opus opera em 1M coberto pela assinatura, mas contexto inchado degrada qualidade (context rot) e gasta orçamento. O 1M é folga para picos, não espaço para encher.

- **Modelo**: usar **Opus** (sobe a 1M automático no Max, coberto pela assinatura). **Nunca** fixar `sonnet`/Sonnet 4.6 — seu 1M consome *usage credits* mesmo no Max, gerando despesa fora da assinatura.
- **Leitura seletiva**: este arquivo é o **índice**; o detalhe vive em `docs/` e só entra na sessão quando a tarefa exige. Não carregar a árvore `docs/` inteira no início — ler apenas o(s) arquivo(s) relevantes à tarefa (Arc N → só `docs/arcos/arcN-*.md`). Preferir `grep`/ranges a ler arquivos inteiros. `plughub_spec_v1.docx` é referência sob demanda, nunca carregada inteira sem necessidade explícita.
- **Comandos**: `/compact` ao concluir uma etapa e ao passar de ~150k (não esperar estourar); `/clear` ao trocar para tarefa não relacionada. Na CLI, `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=60` dispara o auto-compact antes do default (~83%).
- **Higiene**: uma sessão = uma tarefa coerente. No Cowork o modelo é fixado ao abrir — abrir sessão **nova** já com Opus, não recuperar sessão presa em modelo errado. Evitar `cat` de arquivos grandes quando já há resumo aqui ou em `docs/`.

---

## Saúde do CLAUDE.md — Regras de Manutenção

> **Target: ≤ 1 750 linhas** *(revisado de 800 para 1 750 em 2026-09-06, pelo dono)*.
> Quando ultrapassar, aplicar as regras abaixo; subir ou baixar o alvo é decisão do dono.
>
> **Como editar este arquivo mora na skill `claude-md-maintenance`** (2026-09-16): onde cada fato
> mora, como corrigir frase medida falsa, como mover sem perder regra, e o verificador
> `scripts/check_claude_md.py`. O porquê do 1 750 foi movido **integralmente** para
> `references/casos-medidos.md` da skill. Aqui ficam as regras:
> - **uma casa por fato** — o que já mora noutro lugar ganha ponteiro, nunca cópia;
> - **afirmação se mede no código ou no estado vivo**, nunca na fonte declarativa, inclusive ao corrigir;
> - **nunca apagar sem destino conferido item a item**; cabeçalho é endereço (`CLAUDE.md § X` é citado).

### O que FICA no CLAUDE.md

| Categoria | Critério |
|-----------|----------|
| Invariantes e regras | "never do X", contratos de componente, limites arquiteturais |
| Modelo de sessão e domínios | roles, status, close_reason, visibilidade de mensagens |
| Responsabilidades dos componentes | tabela de uma linha por componente |
| Stack por pacote | tabela compacta (linguagem, runtime, porta) |
| Estrutura do repositório | árvore de diretórios do nível `packages/` |
| Kafka topics | tabela de tópicos × producer × consumer |
| Convenções de nomenclatura | padrões de ID |
| Seções de arquitetura ativa | resumo de 15–20 linhas com link para `docs/arcos/` |
| ~~Pending genuíno~~ | **NÃO fica.** Trabalho aberto mora em `pending.md`, sob o grupo da demanda — ver § *Pending — o ledger, não esta seção* |

### O que NÃO pertence ao CLAUDE.md

| Proibido | Vai para |
|----------|----------|
| Itens marcados com ✅ | `CHANGELOG.md` |
| Histórico de implementação (task #N, testes X/Y, build N kB) | `CHANGELOG.md` |
| Documentação completa de um Arc ou módulo (> 50 linhas) | `docs/arcos/{arc}.md` |
| Snippets de código longos (> 10 linhas) fora de invariantes | `docs/arcos/{arc}.md` |
| Detalhes de UI (props, componentes, hooks por feature) | `docs/arcos/{arc}.md` |
| "Pendente (fase 2)" que já foi implementado | Deletar |

### Onde a documentação mora

O **índice anotado** de todo o acervo (arcos, guias, ADRs, specs — com uma linha dizendo o que cada
um decide) vive em **[`docs/INDEX.md`](docs/INDEX.md) § *Índice anotado da arquitetura***. Ele saiu
daqui em 2026-09-06 (DOC-02): eram 95 linhas de índice cobradas de **toda** sessão no boot, e já
havia um portal de documentação — duas casas indexando o mesmo acervo.

> ⚠️ **A mesma DOC-02 apagou, sem destino, as regras de onde ESCREVER** — ledger de tarefas,
> onde registrar cada decisão, convenção de pastas e atualização de docs —, e este parágrafo dizia
> que elas *"continuavam abaixo"*. Restauradas em 2026-09-16 na skill **`task-ledger`**
> (`references/regras-originais.md` traz o texto exato de `76b272b0^`). O essencial, sempre carregado:
> **título nunca afirma status** · **id `AAA-NN` único através de `pending.md` + `done.md`** ·
> **fechar é MOVER** (`CHANGELOG.md` + `done.md` + doc, no mesmo commit) · **mudança no mecanismo de
> conferência atualiza `docs/guias/conference-mechanics.md` § Histórico** · commit por caminho
> explícito, nunca `git add -A` (há sessões paralelas na mesma árvore).

Os quatro arquivos da raiz: **`CLAUDE.md`** (arquitetura viva, regras, invariantes, resumos) ·
**`pending.md`** (trabalho ABERTO, por demanda) · **`done.md`** (índice do que fechou) ·
**`TODO.md`** (raciocínio e medição por assunto) · **`CHANGELOG.md`** (o porquê de cada entrega).

**Skills do projeto** (`.claude/skills/`, 2026-09-16) — carregam sob demanda o MÉTODO; os invariantes
continuam aqui. `testing-pattern` · `data-engineering` · `deployment` · `task-ledger` ·
`security-boundaries` · `plughub-review` (com `scripts/scan_diff.py`, que varre só as linhas
ADICIONADAS) · `skill-flow-authoring` · `platform-ui-change` · `claude-md-maintenance`. Critério da divisão: regra que tem de valer mesmo quando ninguém pede fica aqui;
procedimento e caso medido vão para a skill. **Criar ou mudar uma skill** segue a
`claude-md-maintenance` § 6: `name` igual à pasta, nome no índice acima e teste em sessão NOVA
(canário e controle negativo) antes de dar como pronta.

---

## Unified Session Model

Every contact is a conference room. Core creates the session on every new contact; agents join the room with their queues and receive messages according to visibility options.

### Participant roles

| Role | Description |
|---|---|
| `primary` | Main agent responsible for the interaction |
| `specialist` | Invited expert (task step, assist mode) |
| `supervisor` | Human or AI supervisor monitoring the session |
| `evaluator` | Quality agent evaluating the session (online or post-session) |
| `reviewer` | Human agent reviewing the evaluator's output |

### Session status

| Status | Description |
|---|---|
| `active` | Session in progress with at least one participant |
| `closed` | Session ended normally |
| `abandoned` | No agent joined before the session ended |

### close_reason domain

```
no_resource          — no agents available and no queue configured
max_wait_exceeded    — max queue wait time exceeded
customer_disconnect  — client disconnected (connection_lost)
customer_hangup      — client ended actively (voice/video)
customer_abandon     — client left before being served
flow_complete        — Skill Flow complete step
agent_transfer       — transferred to another pool
agent_hangup         — agent ended actively
session_timeout      — session inactive beyond TTL
system_error         — unrecoverable error
```

### Message visibility

| Visibility | Recipients | Typical use |
|---|---|---|
| `all` | All participants including the customer | Normal service message |
| `agents_only` | All agents, without the customer | Internal note between agents |
| `["part_abc", "part_xyz"]` | Only the listed participant_ids | Supervisor → specific agent, private |

---

## Invariants — never violate

- **AI Gateway is stateless** — processes one turn per LLM call. No state between turns.
- **Routing Engine is the sole arbiter** — no component routes a conversation without going through it.
- **MCP is the only integration protocol** — no direct REST between internal components.
- **pipeline_state persists to Redis on every step transition** — never in memory only.
- **Agent contract**: `agent_login` → `agent_ready` → `agent_busy` → `agent_done`
- **`agent_done` requires `handoff_reason`** when `outcome !== "resolved"`
- **`issue_status` is always required and never empty** in `agent_done`
- **Agents never access backend systems directly** — only via authorised MCP Servers
- **All domain MCP calls are intercepted** — native agents via `McpInterceptor` (in-process); external agents via proxy sidecar on localhost:7422. No MCP call reaches a domain server without permission validation, injection guard, and audit.
- **`insight.historico.*` persists via Kafka, never direct PostgreSQL write**
- **O POOL é a unidade endereçável — nunca o `skill_id`.** Hooks de pool, `workflow_trigger`,
  endpoints de canal e qualquer disparo apontam para um **pool**; o skill e sua config são detalhe
  **interno** do deploy do pool (slot `current` + `config_json`). Endereçar por skill reabre a pergunta
  que o modelo de slots existe para fechar — *"qual config está rodando?"* —, porque o mesmo skill pode
  estar deployado em N pools com configs diferentes (regime legítimo: um `skill_survey_outbound_v1` em
  três pools, um por grão de sinal). Nesse regime a resolução por skill é **ambígua**, e escolher por
  score seria rodar um deploy que o chamador não pediu, em silêncio — o router **rejeita**
  (`Webhook endpoint AMBÍGUO`). `skill_id` sobrevive só como endereço legado, válido enquanto **um
  único** pool o declara.

  > **Corolário medido em 2026-08-24 — "tem config" ≠ "tem endereço".** Um objeto de configuração que
  > mistura endereço com política não pode ser testado por PRESENÇA. `pool.queue_config` carregava
  > três fatos de escopos diferentes (`pool_id` = endereço · `max_wait_s` = política · `skill_id` =
  > endereço legado que não endereça nada desde que produção virou o slot do POOL), e **quatro** call
  > sites perguntavam *"há quem atenda?"* testando `if queue_config:`. Consequência: pool que só
  > declarava o teto de espera era classificado como fila ATENDIDA, retinha licença de IA durante uma
  > espera que ninguém atendia, e o log acusava deploy quebrado num pool desligado de propósito.
  > Regra: **o tier é decidido pelo ENDEREÇO, por um predicado único compartilhado** (aqui,
  > `mute_queue.queue_address`) — duas respostas para "esta fila é atendida?" é como se paga a licença
  > de um agente que não existe. E fallback de endereço **recusa alto**: `queue_pool_id or pool_id`
  > adivinhava um alvo que não podia funcionar em caso nenhum, convertendo config ausente em erro de
  > runtime. Ver `CHANGELOG.md` 2026-08-24.

---

## Postura de Engenharia — invariantes de MÉTODO

> Não são regras de arquitetura, e sim de como implementar, depurar e questionar. Ganharam seção
> própria porque um dia inteiro de bugs (2026-07-14) nasceu de violá-las: quase todo defeito estava
> escondido atrás de um valor **plausível**, e cada correção só revelava o próximo por remover um
> anestésico. Ver CHANGELOG (arco T + J5) para os casos.
>
> **Método de TESTE mora na skill `testing-pattern`** (`.claude/skills/testing-pattern/`,
> 2026-09-16): como desenhar veredicto, bateria de mutação, rodar gate e pytest, registrar no
> manifesto. Aqui ficam só as regras; os casos medidos que as justificam foram movidos
> **integralmente** para `references/casos-medidos.md` da skill.

- **Degradação NUNCA é silenciosa.** `except: pass`, fallback mudo, tier de recuperação que engole o
  motivo, default que "conserta" um campo ausente — cada um troca uma falha barulhenta por uma mentira
  tranquila. Se um caminho degrada, ele **loga por que** degradou. *Um fallback que esconde o motivo do
  fallback não é resiliência — é cegueira.* (Casos: fallback do `skill.flow`; seed-if-absent pulando
  mudo; os 3 tiers do `/reports/sessions`.)

- **Um valor plausível esconde bugs; um valor ausente os denuncia.** `Segs: 0`, `"Resolvido"`, "algum
  flow rodando" — nenhum grita, e por isso passam. Foi um campo **faltando** (`spawn_reason`) que expôs
  um endpoint que nunca rodava sua query real. Ao depurar, desconfie primeiro do dado que parece
  razoável, não do que parece errado.

- **"Foi escrito" ≠ "mudou"; "existe" ≠ "está pronto".** Confundir presença com conteúdo custou 3
  diagnósticos: `updated_at` bumped a cada boot (D4), linha de skill sem `flow` (D2), slot com
  `yaml_snapshot` nulo. Compare **conteúdo** (canonicalizado, por contenção quando há defaults), não a
  existência da linha nem o timestamp de escrita.

> **Método de DADOS (ClickHouse, Kafka, id derivado) mora na skill `data-engineering`**, com os
> casos medidos das três regras abaixo e da regra do alias movidos integralmente.

- **`ReplacingMergeTree` substitui a LINHA INTEIRA — não faz merge por coluna.** Todo writer manda a
  linha completa ou é reidratado antes. A versão é fato do EVENTO, na resolução do fenômeno
  (`ReplacingMergeTree(row_version)`, `coalesce(<fim>, <início>)` em `DateTime64(3)`), nunca da
  inserção. `participation_intervals` colide segmentos do mesmo participante: testemunha
  por-segmento é `segments`.

- **Ordem no Kafka é por PARTIÇÃO — logo publish sem `key` não tem ordem nenhuma.** Eventos do MESMO
  objeto (abre/fecha) viajam com a chave dele. Sem ela, o defeito mais caro do repositório: segmento
  aberto para sempre, sem erro em lugar nenhum (`conference-mechanics.md` § Problema 34).

- **Identidade DERIVADA (`uuid5`) contém o discriminador do FENÔMENO, não o do contêiner dele** —
  escolhido pelo ciclo de vida do fato, nunca por um valor do call site. `uuid5(tenant, session_id)`
  para a passagem pela fila apagou a primeira espera (§ Mudança 38).

- **Comentário ou docstring que promete invariante sem mecanismo é defeito**, não documentação — as
  duas regras acima nasceram de uma garantia afirmada em prosa que ninguém impunha.

- **Um instrumento pode ser falseável, ramificado e honesto — e ainda medir a proposição ERRADA.**
  Ao desenhar o veredicto, pergunte de qual PROPOSIÇÃO cada ramo é evidência; *"isto machuca?"*
  pede dois números (exposição e dano), nunca um ramo só. (D14.1: `VIVO` com dano zero.)

- **Quando a spec e o código discordam, desconfie dos DOIS.** O merge lia um `started_at` que metade dos
  canais não escrevia; a resposta certa não foi fazer o timestamp funcionar, foi ver que a aciclicidade
  **nunca deveria depender de relógio** (união de componentes disjuntas). O teste não verifica só a
  implementação — ele descobre que a especificação pedia a coisa errada. Corrigir a spec é resultado
  válido, não desvio.

- **Um teste que não pode reprovar é pior que teste nenhum — ele compra confiança sem dar nada.**
  **Antes de aceitar um verde, pergunte o que o faria ficar vermelho**, e prefira INCONCLUSIVO a
  passar por ausência de amostra. Três formas já pagas:
  **um mock não verifica que o alvo existe, ele o CRIA** — ao mockar método do próprio objeto sob
  teste, asserte `hasattr` antes (`probe_adapter_self_calls.sh`); **contrato de payload se mede
  no LEITOR**, nunca no produtor (`probe_menu_result_contract.sh`); **em asyncio, espere pelas
  TASKS**, nunca por contagem de `sleep(0)`.

- **Um ambiente que só sobe porque já subiu antes não está sendo verificado — está sendo lembrado.**
  Estado herdado (volume, imagem, linha de DB) é entrada não declarada do boot. **Instalação limpa
  é um teste** (`infra/scripts/rebuild-all.sh --wipe`, em dia calmo); falha logo após wipe: a
  hipótese ordenada é "o wipe revelou", não "quebrou". **Pergunte à IMAGEM, nunca ao container**
  (`probe_python_suites.sh`). **pytest roda no WORKDIR do pacote**, nunca da raiz do monorepo.
  **"Rodou tudo o que a lista cita" ≠ "a lista cita tudo"** — todo `.sh` de `infra/test/` tem
  classe no `gates.manifest` (`probe_gates_manifest_coverage.sh`).

- **Guarda sobre valor decodificado testa `if not x`, NUNCA `is None` — o vazio é o valor plausível
  mais barato de produzir.** Os decodificadores deste repo normalizam ausência para string vazia
  (`mute_queue._decode` devolve `""`, não `None`), então `is None` compara com um valor que a fonte
  **não produz**: o ramo fica morto e o caminho segue como se o dado existisse. Duas ocorrências no
  MESMO mecanismo em quatro dias: `??` × truthiness sobre `instanceId` (Mudança 35, agente de fila
  surdo ao cliente) e `if raw is None` sobre `first_queued_ms` (Mudança 37 — todo contato roteado
  direto emitindo uma espera fantasma de 0 ms). O modo de falha é sempre o mesmo e nunca fica
  vermelho: **o defeito só aparece quando alguém conta a população que NÃO deveria ter linha.** Ao
  criar um produtor, o teste que importa não é "registrou o fato", é "**não** registrou o não-fato" —
  e ele precisa da testemunha de presença ao lado, senão um produtor que nunca emite passa.

- **Em ClickHouse, alias de agregado NUNCA repete nome de coluna real da tabela.** `any(pool_id) AS
  pool_id` derruba a query inteira (code 184) e o wrapper devolve `data: []`, igual a "não há dado".
  Sufixe `_ref` e renomeie na camada Python.

- **O que roda é a IMAGEM, não a árvore.** Nenhum serviço monta `packages/` por bind-mount
  (medido 2026-09-16): editar não muda o container, nem o `pytest` rodado nele. Mudança de código =
  `build` + `up -d`; `docker cp` é iteração efêmera que o próximo `up -d` apaga. Como levar mudança
  de código, skill, config ou schema ao ar e provar que está rodando: skill `deployment`.

---

## Configuration — Single Source Invariants

> Regras permanentes. O código ainda tem violações herdadas em burn-down (`docs/arcos/config-consolidation.md`),
> enforçadas pelo guard `infra/check_config_invariants.py` (falha em violação nova).

- **One source per domain** — cada domínio tem UM store canônico: settings horizontais → config-api
  (`platform_config`); pools/skills → agent-registry; users/ABAC → auth-api; forms/campaigns →
  evaluation-api; planos → pricing-api. Config nunca duplicada entre stores.
- **Provisioning only via official API** — todo provisionamento (incl. seed/demo) escreve ATRAVÉS da
  API do store. Proibido: escrita direta em Redis/DB de config, e listas de config hardcoded em
  scripts/serviços.
- **Seed-if-absent / DB-owned (provisioning precedence)** — o YAML declarativo (`infra/registry/*.yaml`)
  apenas **semeia DB vazio** (201 no create); uma vez que a entidade existe, o **DB é fonte de verdade** e o
  `RegistrySyncer` **não sobrescreve** no restart (edições de UI sobrevivem a rebuild — pools, deploy/capacity,
  hooks, escalation/mentionable). `REGISTRY_SYNC_RECONCILE=true` restaura o reconcile (YAML vence) p/ dev/
  GitOps. **Skills TAMBÉM são seed-if-absent** (mudou 2026-07-13, `registry_syncer.py` §46-53): o upsert
  de antes sobrescrevia a definição a cada boot e apagava o rascunho do editor. **Editar o YAML de um skill
  já semeado é no-op** (só loga o DRIFT). Para o arquivo valer: `PUT /v1/skills/:id` com o conteúdo, ou
  `REGISTRY_SYNC_RECONCILE=true` — e nenhum dos dois **muda o que roda**: todo pool executa o snapshot do
  slot `current` (`set-next`→`promote`). *Correção 2026-09-14:* mandava usar `x-skill-publish:true` e
  ressalvava *"se o pool usa slot"* — o header é no-op (UMA definição, `flow`) e pool sem slot não roda.
  Alvo Fase 2: YAML→migração versionada if-absent, store por store.

  > ⚠️ **Corolário: para `hooks`, `deploy`, `capacity` e afins, pergunte ao agent-registry, NUNCA ao
  > YAML.** Medido em 2026-08-22: uma "correção" anterior leu `infra/registry/tenant_demo.yaml:400`,
  > viu `dispatch: inline` e corrigiu o `CLAUDE.md` para dizer que o demo estava em `inline` — mas a
  > config VIVA (`GET :3300/v1/pools/retencao_humano`) dizia `detached`. O YAML é seed-if-absent:
  > editar pool já semeado é no-op e **o DB vence**. Ler a fonte DECLARATIVA e chamá-la de estado é
  > o mesmo erro que esta seção descreve — com o agravante de ter sido cometido *ao corrigir*.
- **Every config field is UI-editable** — todo campo de config tem superfície na tela do módulo. Campo
  que só existe em YAML/arquivo é dívida a fechar.
- **env only for secrets and wiring** — env é exclusivamente para segredos (JWT, tokens, creds) e
  topologia (URLs, brokers, portas, tenant). Config de negócio/tuning nunca em env. Quando env e
  config-api têm a mesma chave, **config-api vence**.

  > **Corolário medido em 2026-08-25 — chave com DOIS escritores não tem dois valores, tem um: o do
  > escritor mais FREQUENTE.** `{t}:pool_config:{p}` era escrita pelo routing-engine (86 400, em
  > `pool.registered`) e pelo orchestrator-bridge (3 600, no `_heartbeat_tick`). O 86 400 não perdia
  > uma corrida no boot — era sobrescrito **a cada 15 s, para sempre**, e com ele o conserto
  > deliberado de `changelog-2026-04-16` (300 s → 86 400) ficou desfeito em silêncio por meses.
  > **Ao encontrar dois valores para a mesma chave, meça a CADÊNCIA de cada escritor antes de
  > raciocinar sobre precedência** — e o instrumento é PARAR o serviço suspeito: se o valor decai e
  > não reseta, ele é o renovador único. Regra derivada: TTL de chave compartilhada mora em UM lugar,
  > lido pelos dois no momento da escrita (não capturado no import, senão `config.changed` não vale
  > sem restart).
  >
  > **E a leitura de config falha por CAMADAS, todas com a mesma cara.** O mesmo arco achou o
  > namespace `session` **inteiro inerte** no bridge, por três causas empilhadas — env ausente no
  > compose, porta errada no default hardcoded (3500 = analytics-api), e GET sem `?tenant_id=` (422).
  > Cada uma sozinha bastava, e as três degradam para *"usa o default"*, que quase sempre parece
  > certo. **Consertar a de cima não move o número e parece "não aplicou"**; por isso o aviso de
  > degradação tem de nomear **o que** deixa de valer, não só dizer *"using default values"* — foi
  > exatamente essa frase genérica que ninguém leu por meses. Ver `CHANGELOG.md` 2026-08-25 e
  > [`docs/arcos/instance-bootstrap.md`](docs/arcos/instance-bootstrap.md) § TTL.

---

## MCP Interception — Hybrid Proxy Model

São **TRÊS** bordas, não duas — e a terceira é a única server-side:

| Agent type | Mechanism | Network hop | Estado medido (2026-08-13) |
|---|---|---|---|
| Native agent (SDK) | `McpInterceptor` in-process (`@plughub/sdk`) | None | ⚠️ **nunca instanciado**; o caminho real (`skill-flow-service.mcpCall`) faz `fetch` cru, sem gate nenhum |
| External agent (LangGraph, CrewAI) | `plughub-sdk proxy` sidecar on localhost:7422 | Loopback only | implementado — **só existe se o operador subir o processo**. **Rebaixado** (2026-08-13): runtime importado sai do roadmap por decisão de produto |
| Agent `external-mcp` | tool `invoke` do mcp-server (server-side) | rede interna | ✅ — **única borda em vigor**; é expor **tool**, não importar agente. Não encostar |

> **A autorização desta borda é o `tools[]` DECLARADO na skill, e ele só passou a viajar em
> 2026-09-01 (CAP-06).** Antes, `registry-client.getAgentType` devolvia `permissions: []`
> **fixo** e o `judgeInvoke` **nega com lista vazia** — a "única borda em vigor" recusava
> **100% das chamadas**, inclusive de quem declarasse a tool. Hoje o `agent_login` mapeia
> `{mcp_server, tool}` → `"{mcp_server}:{tool}"` e **assina** no `session_token`: a lista
> viaja assinada ou não viaja — **nunca** como argumento da chamada, que seria o chamador
> declarando a própria autorização (o defeito que derrubou o gate de avaliador na CAP-01).
> **Virada OPT-IN:** skill sem `tools[]` continua com `[]` e continua barrada; 0 de 44
> declaram hoje, então o número de chamadas que passaram a ser **negadas** é zero.
> ⚠️ **A mesma lista tem TRÊS semânticas** — `judgeInvoke` (exato, vazio ⇒ nega) · sidecar
> (curinga `{server}:*`, vazio ⇒ sem filtro) · `ai-gateway/inference.py:131` (**nome CRU** da
> tool, vazio ⇒ sem filtro). Popular no formato dos dois primeiros faria o terceiro remover
> TODAS as tools; ele é caminho morto hoje (`/v1/inference` sem chamador), e se ganhar
> produtor o formato se unifica **antes**. ⚠️ Restam duas paredes, ambas registradas e ambas
> BARULHENTAS: nenhum `MCP_SERVER_*_URL` configurado (CAP-07) e o `mcp_server` declarado sem
> validação (CAP-08, `skills.ts` é `TODO` com `void mcpServers`). Gate:
> `infra/test/probe_mcp_permissions_producer.sh`.

Checks per call (< 1ms): permission validation (JWT local decode) → injection guard (13 patterns) → audit record (Kafka `mcp.audit`, fire-and-forget). Audit policy defined per tool, not per call — caller cannot opt out (LGPD). `AuditRecord` includes: `server_name`, `tool_name`, `allowed`, `injection_detected`, `duration_ms`, `source` (`in_process`|`proxy_sidecar`|`mcp_server_invoke`).

> ⚠️ **O invariante "nenhuma chamada MCP escapa do guard" está VIGENTE apenas no caminho `external-mcp`.**
> A regra está escrita três vezes e as cópias já divergiram (curinga `server:*` e `permissions[]` vazia
> decidem diferente no `invoke` e no sidecar). Pior: **borda é fato de REDE, não de código** — enquanto um
> domain MCP server for alcançável a partir do processo do agente, qualquer borda é evitável por omissão,
> e nada no repositório garante o contrário. Decisão (borda única no mcp-server + requisito de
> inalcançabilidade) em [`docs/adr/adr-mcp-interception-single-border.md`](docs/adr/adr-mcp-interception-single-border.md) — **proposto**, primeira fase é MEDIR o volume por caminho.

> **Reclassificação (2026-08-13).** Três coisas viviam neste mesmo pacote e têm destinos
> diferentes: **(1) `external-mcp`** — expor *tool*, única borda em vigor: **fica**;
> **(2) portabilidade** (`certify`/`verify-portability`/`skill-extract`/`regenerate`) —
> responde *"posso sair daqui?"*, sustenta o "sem lock-in", e **A2A não a cobre** (torna os
> agentes alcançáveis, não extraíveis): **fica, separada**; **(3) runtime importado** (agente
> de terceiro rodando como pool + `plughub-sdk proxy`) — **rebaixado** a sob-demanda-de-negócio.
> Motivo não é custo: importar pede que a plataforma garanta capacidade, heartbeat, pausa,
> contrato `agent_done` e auditoria não-optável **sobre código que ela não controla** — corrói
> a camada de governança que é o diferencial. Padroniza-se a fronteira (A2A), não se dissolve.
> Precedente independente: o **quality-ingest** já escolheu ingerir transcrição em vez de rodar
> o agente por dentro para medi-lo. Ver [`docs/product/agentes-externos-reclassificacao.md`](docs/product/agentes-externos-reclassificacao.md).

---

## Repository Structure

> **Medido em 2026-08-31: a tabela listava 21 pacotes e o repositório tem 35.** Seis dos ausentes
> eram citados dezenas de vezes no resto deste arquivo — a estrutura mentia por OMISSÃO, que é o
> "valor plausível" da § Postura de Engenharia na forma mais barata: uma lista parece completa por
> ser uma lista.

```
plughub/
  CLAUDE.md                      ← this file
  plughub_spec_v1.docx           ← full architectural specification
  packages/
    schemas/                     ← @plughub/schemas — Zod contracts
    py-authz/                    ← plughub-authz — verificador CANÔNICO de JWT+ABAC (Python)
    py-contextstore/             ← plughub-contextstore — gêmeo Python das funções PURAS do ContextStore (carimbo + rota); paridade com `@plughub/schemas` por gate
    py-tasks/                    ← plughub-tasks — a morte de uma task de asyncio tem de APARECER: `supervisionar` (boot) e `disparar` (efêmera, dá dono); exceção em task que ninguém aguarda some
    sdk/                         ← @plughub/sdk — TypeScript + Python

    mcp-server-plughub/          ← Agent Runtime and BPM tools — port 3100
    mcp-server-knowledge/        ← Vector knowledge base for RAG agents — port 3401
    mcp-server-auth/             ← domain MCP: authentication and PIN validation (demo stub) — port 3150
    skill-flow-engine/           ← Skill Flow interpreter
    skill-flow-worker/           ← Kafka consumer, runs SkillFlow for workflow instances
    orchestrator-bridge/         ← reconciliação (instance_bootstrap), RegistrySyncer, pool hooks
    ai-gateway/                  ← LLM calls, sentiment, context extraction (Python)

    agent-registry/              ← CRUD for AgentType, Pool, Skill, GatewayConfig — port 3300
    config-api/                  ← platform_config: settings horizontais por namespace — port 3600
    auth-api/                    ← Auth, JWT, ABAC, grupos — port 3200
    routing-engine/              ← Agent allocation and queue management (capacity em 3550)
    rules-engine/                ← Post-routing event evaluation
    channel-gateway/             ← Channel adapters, inbound normalisation, identity, survey web

    calendar-api/                ← Calendar engine + CRUD REST (Arc 4) — port 3700
    scheduler-api/               ← Agenda: fire a pool via webhook at a time — port 3650
    workflow-api/                ← Workflow instance lifecycle (Arc 4) — port 3800
    dialog-api/                  ← store canônico de DialogForm (draft/publish) — port 3760
    mailing-api/                 ← outbound: mailing, campaign, delivery — port 3660
    pricing-api/                 ← Capacity-based billing, invoice — port 3900
    usage-aggregator/            ← metering: agrega usage.events, quota, cycle-reset — port 3950

    analytics-api/               ← relatórios + leitor ClickHouse + audit LGPD — port 3500
    evaluation-api/              ← Quality evaluation platform (Arc 6) — port 3400
    session-replayer/            ← Hydrator + Replayer + ReplayContext + StreamPersister
    quality-ingest/              ← Pluggable contact-history reader (R13a) — port 3850
    quality-export/              ← Internal history → re-evaluation (R13d) — port 3852

    platform-ui/                 ← All operator-facing UI (React + Vite) — port 5174

    e2e-tests/                   ← black-box E2E suite (não é serviço)
    gitagent/                    ← @plughub/gitagent: repo Git como fonte de verdade do agente (lib/CLI)
    dashboard/                   ← api + ui do Dashboard #35 (não sobe em compose algum)
    agente-retencao-teste/       ← fixture: agente nativo de teste E2E (agent.yaml + flows)

    conversation-writer/         ← ⚠️ PACOTE FÓSSIL em quarentena declarada (2026-08-03)
    clickhouse-consumer/         ← ⚠️ PACOTE FÓSSIL em quarentena declarada (2026-08-03)
```

> **Os dois fósseis são mantidos DE PROPÓSITO e não devem ser religados.** Nenhum está deployado e o
> destino de escrita de um deles não existe; ficam no repositório, com o rótulo no próprio README,
> pelo mesmo critério da tabela `pools` fóssil — **o erro fica visível e reversível**. Apagá-los
> troca um erro documentado por um buraco mudo; religá-los sem ler o README é o modo de falha que a
> quarentena existe para impedir.

## Stack per Package

| Package | Language | Runtime | Notes |
|---|---|---|---|
| schemas | TypeScript | Node 20+ | Zod 3.23+ |
| py-authz | Python | Python 3.11+ | lib, sem serviço — PyJWT + FastAPI |
| py-contextstore | Python | Python 3.11+ | lib, sem serviço e **sem dependências** — a ausência é requisito do gate de paridade |
| py-tasks | Python | Python 3.11+ | lib, sem serviço — só `asyncio`+`logging`; 7 consumidores (gateway, routing, rules, ai-gw, evaluation, analytics, workflow) |
| sdk | TypeScript + Python | Node 20+ / Python 3.11+ | Two parallel packages |
| mcp-server-plughub | TypeScript | Node 20+ | Official Anthropic MCP SDK |
| skill-flow-engine | TypeScript | Node 20+ | State graph interpreter |
| ai-gateway | Python | Python 3.11+ | FastAPI + Anthropic SDK |
| agent-registry | TypeScript | Node 20+ | PostgreSQL + Prisma |
| routing-engine | Python | Python 3.11+ | Redis + Kafka |
| rules-engine | Python | Python 3.11+ | Redis + ClickHouse |
| calendar-api | Python | Python 3.11+ | FastAPI + asyncpg — port 3700 |
| scheduler-api | Python | Python 3.11+ | FastAPI + asyncpg + Redis — port 3650 |
| workflow-api | Python | Python 3.11+ | FastAPI + asyncpg — port 3800 |
| skill-flow-worker | TypeScript | Node 20+ | Kafka consumer + SkillFlowEngine bridge |
| channel-gateway | Python | Python 3.11+ | FastAPI + aiokafka + channel adapters |
| pricing-api | Python | Python 3.11+ | FastAPI + asyncpg + openpyxl — port 3900 |
| auth-api | Python | Python 3.11+ | FastAPI + asyncpg + bcrypt + python-jose — port 3200 |
| evaluation-api | Python | Python 3.11+ | FastAPI + asyncpg — port 3400 |
| quality-ingest | Python | Python 3.11+ | FastAPI + aiokafka (pure producer) — port 3850 |
| quality-export | Python | Python 3.11+ | FastAPI + httpx (ClickHouse-only reader) — port 3852 |
| platform-ui | TypeScript | Node 20+ / Vite | React 18, Tailwind, i18n |

## Package Dependencies

```
schemas         ← base — no internal dependencies
sdk             ← depends on: schemas
mcp-server      ← depends on: schemas
skill-flow      ← depends on: schemas, mcp-server
ai-gateway      ← depends on: schemas
agent-registry  ← depends on: schemas
routing-engine  ← depends on: schemas, agent-registry
rules-engine    ← depends on: schemas, routing-engine
channel-gateway ← depends on: schemas
auth-api        ← no internal dependencies (standalone user store)
```

Never create circular dependencies. `schemas` never depends on any other package.

## Component Responsibilities (Summary)

| Component | Sole responsibility |
|---|---|
| **Core** | Session lifecycle, canonical stream, message masking, adapter coordination |
| **Channel Gateway** | Inbound normalisation, outbound rendering, fallback interaction collection |
| **AI Gateway** | Stateless LLM inference. Does not manage session or history. |
| **Agent Registry** | CRUD for AgentType, Pool, Skill, GatewayConfig. Cache invalidation via Kafka. |
| **Routing Engine** | Agent allocation, queue management, scoring algorithm, close_reason detection |
| **Rules Engine** | Post-routing event evaluation. Publishes consequences. No routing, no Redis polling. |
| **Skill Flow Engine** | Flow interpreter. Persists pipeline_state to Redis on every step. |

---

## Instance Bootstrap — Reconciliation-Driven Agent Management

Kubernetes-style reconciliation controller in `orchestrator-bridge/instance_bootstrap.py`. Compares desired state (Agent Registry) vs actual state (Redis) and applies minimum diff. Triggers: startup, heartbeat 15s, periodic 5min, `registry.changed`/`config.changed` Kafka. ReconciliationReport: `created/deleted/drained/updated/renewed/unchanged/errors/duration_ms/dry_run`.

**RegistrySyncer** runs before Bootstrap: lê `skill-flow-engine/skills/*.yaml` (slug `^skill_[a-z0-9_]+$`) e `infra/registry/*.yaml`, na ordem skills → journey types → pools → channel endpoints → deploy slots; skills, pools e slots são **seed-if-absent** (§ Configuration). *Correção 2026-09-14:* dizia *"upserts pools+agent_types; prunes stale"* e *"publica produção via `x-skill-publish:true`"* — AgentType está aposentado (`_sync_agent_type`/`_prune_agent_types` sem chamador) e o header, que o syncer ainda envia, é no-op no registry desde 2026-07-13. Instance IDs: `{agent_type_id}-{n+1:03d}`. Human agents NOT managed by Bootstrap. Seed no longer writes Redis keys.

**Execução = produção, não a edição:** o bridge executa **só** o **snapshot do slot `current` do POOL** (`get_pool_current_flow`, cache por pool, invalidado no `registry.changed(pool)` do promote/rollback). Pool sem `current` **não roda** e o log diz o que fazer; a definição viva (`skill.flow` ou YAML em disco) só executa com `ALLOW_LIVE_FLOW_FALLBACK=true`, vazio no compose. `PUT /v1/skills` grava a **única definição** (`flow`; `flow_draft` sempre nulo, `x-skill-publish` no-op, `deploy_status` vestigial) — salvar não muda o que roda; só `set-next`→`promote`. *Correção 2026-09-14:* aqui estava o modelo de rascunho da Fase B (editor → `flow_draft`, fallback para pools "não migrados"), abandonado em 2026-07-13 (`skills.ts` PUT; `main.py::resolve_flow_for_agent`).

**Versão = deploy do pool (Skill Versioning Fase C):** identidade de versão = **`set_at` do slot `current`** (momento do promote), carimbada em `segments.deploy_version` pelo bridge (cache `_pool_deploy_version_cache`, fallback `skill.version`). O **promote grava um `SkillDeployment`** (`deployed_at=set_at`, `version`=rótulo `skill.version`) — append-log que o epoch usa p/ rótulo+markers; o analytics casa por `deployed_at`. `skill.version` deixou de ser identidade (vira rótulo). Ver `docs/product/skill-versioning-deploy-spec.md`.

→ See [`docs/arcos/instance-bootstrap.md`](docs/arcos/instance-bootstrap.md)

---

## ContextStore & Context-Aware Progressive Resolution

Redis hash `{tenantId}:ctx:{sessionId}`. `ContextEntry`: `{value, confidence 0-1, source,
visibility, updated_at}`. Confiança: ≥0.9 confirmado; ≥0.7 alta; 0.4–0.7 incerto; <0.4 desconhecido.

### O root `core.*` é RESERVADO à plataforma — tudo o mais é dos skills

*(CNS-02/CNS-11, 2026-09-01. Detalhe em [`docs/product/contextstore-core-namespace-spec.md`](docs/product/contextstore-core-namespace-spec.md).)*

```
core.*      plataforma — semeado; o cadastro RECUSA root `core` vindo de tenant
            contact · workflow · survey · pool · queue · sentiment · copilot
            process.outcome · channel · segment.{segId} · customer
session.*   skills   (hash da sessão,  4 h)   ex.: session.card.{type,number}
journey.*   skills   (hash da journey, 30 d)  o canal de processo entre sessões
segment.*   skills   isolamento por agente
outro root  skills   cai no hash da sessão, sem tocar em roteamento
```

**Por que o pequeno é que se reserva:** o core é fechado e semeado (35 nomes); o espaço do tenant é
aberto. Reservar `session.*` custaria mover 112 nomes contra 35, e faria o próprio gateway violar a
regra — ele compõe `session.<chave>` no `delegate`/`collect`.

⚠️ **O escopo de uma tag do core é o SEGUNDO segmento**, e as duas rotas não-sessão são DECLARADAS
(`CONTEXT_ROUTE_PREFIXES`, em `@plughub/schemas`): `core.customer.*` → hash do cliente (**90 d**) ·
`core.journey.*` → hash da journey (**30 d**). No default, dado de retenção trimestral iria para um
hash de 4 h **sem erro em lugar nenhum**. ⚠️ **`customer.` NÃO roteia para o hash do cliente** — o
nome do store e o prefixo não são a mesma string (o oráculo acusa: `mismatched_retention`).

⚠️ **As canônicas antigas viraram `legado`, e ficam**: o snapshot durável guarda os nomes velhos para
sempre, e é o alias que mantém aquele histórico MASCARADO. Não são migração. Namespaces legados ainda
resolvidos na borda: `caller.*`, `account.*`, `insight.*`.

`@ctx.*` resolve em inputs de step, condições de `choice` e arrays de `visibility`; `@segment.*`
prefixa `segment.{segId}.` e isola agentes paralelos. O emissor de sentimento escreve
**`core.sentiment.current` apenas** — classificar é da LEITURA, com faixas por tenant.

→ See [`docs/guias/context-store.md`](docs/guias/context-store.md) (step `resolve`, copilot, Pool
Context Enrichment, `context_tags`, rotas de escopo)

---

## Channel vs Medium

- **channel** = specific channel (`whatsapp`, `webchat`, `voice`, `email`, `sms`, `instagram`, `telegram`, `webrtc`) — **hard filter** for routing, mandatory match
- **medium** = base type (`voice`, `video`, `message`, `email`) — **score factor**, fine-tuning only

## Canonical Stream

`session:{id}:stream` is the single source of truth for all session events. **All XADD calls MUST go through `writeStreamEntry()`** in `lib/write-stream-entry.ts` — never call `redis.xadd()` directly. Sole exception: `session_opened`/`session_closed` in Core `server.ts`. Guarantees: `event_id` always present, `segment_id` always flat, `author_id`/`author_role` flat fields, Zod validation before write.

Messages carry `content` (masked) and `original_content` (unmasked, authorized roles only for LGPD audit).

## Sentiment Tracking

**A plataforma MEDE sentimento, e está provado de ponta a ponta (2026-08-24).** Contrato:
`reason.customer_utterance` declara uma REFERÊNCIA (`$.`/`@ctx.`, nunca literal) à fala do cliente;
o engine resolve, o ai-gateway mede em chamada dedicada FORA do turno e alimenta três destinos —
`{t}:ctx:{sid}` (`core.sentiment.current`), `{t}:pool:{p}:sentiment_live` e o tópico
`sentiment.updated`. Gates: `probe_sentiment_producer.sh` · `gate_sentiment_engine_half.sh` ·
`gate_console_sentiment_source.sh`.

- **`None` = NÃO MEDIDO, e nenhuma superfície renderiza sem valor.** `0.0` é ponto legítimo da escala;
  um `?? 0` no produtor converte ausência em medição *e desarma a guarda do consumidor* sem rastro.
  Idem `trend`, cujo default `"stable"` era invenção da mesma família.
- **Fonte canônica = ContextStore, lida CRUA** — nunca o `contextSnapshot` já filtrado por
  `applyContextMaskingDynamic`, cuja lista é por POOL e apagaria o sentimento em silêncio.
- **A plataforma nunca CLASSIFICA** — emite score; a faixa é configurável por tenant e aplicada na
  LEITURA (classificador canônico: `analytics-api/sessions.py`).
- **Um cálculo, uma casa** (`lib/session-sentiment.ts`): eram duas implementações idênticas e só a
  segunda — o endpoint HTTP, não a tool — alimentava a Console.

⚠️ **Sem histórico por sessão:** `session:{id}:sentiment` **não tem produtor**, logo `trajectory` é
`[]` e `trend` é `null` — ausentes por decisão, nunca fabricados.
⚠️ **Dívida:** o `pool_id` do meta é o de ENTRADA, não o que atende (fatia C de `session-meta-ownership`).

→ See [`docs/guias/sentiment-tracking.md`](docs/guias/sentiment-tracking.md) (cadeia inteira, catálogo
dos defeitos medidos, as quatro superfícies), [`docs/arcos/ai-gateway.md`](docs/arcos/ai-gateway.md)
§ Medição de sentimento.

## Skill Flow — Fourteen Step Types

| Type | Does | Interacts with |
|---|---|---|
| `task` | Delegates to agent via A2A (`assist`/`transfer`) | Routing Engine |
| `choice` | Conditional branching via JSONPath | pipeline_state |
| `catch` | Retry and fallback before escalation | pipeline_state |
| `escalate` | Routes to pool | Rules Engine |
| `complete` | Closes with defined outcome | agent_done |
| `invoke` | Calls MCP tool directly | MCP Server |
| `reason` | Invokes AI Gateway with output_schema | AI Gateway |
| `notify` | Sends message to customer (unidirectional) | Core → Channel Gateway |
| `menu` | Captures customer input, suspends until reply | Core → Channel Gateway |
| `suspend` | Suspends workflow until external signal | workflow-api |
| `collect` | Contacts target via channel, awaits response | workflow-api → Channel Gateway |
| `resolve` | Inline context accumulation (5-phase pipeline) | ContextStore + AI Gateway |
| `begin_transaction` / `end_transaction` | Masked input atomic block | in-memory only |
| `receive` | Suspends awaiting next stream message from any participant (no prompt sent to channel) | Redis BLPOP on `receive:result:{sid}:{iid}` |
| `loop` | Walks a body sub-flow over an array (N sequential turns); item at fixed `item_as` (no variable index), accumulates `collect` into `results_as` | pipeline_state (counter `_loop_idx_{id}`) |

`menu` interaction modes: `text`, `button` (≤3 WhatsApp), `list`, `checklist`, `form`. Fallback for unsupported channels in Channel Gateway adapter only.

## Routing Algorithm — Key Rules

1. **channel is a hard filter** — agent not supporting contact channel = forbidden
2. **agent pause is a hard filter** — paused agents excluded
3. **gateway heartbeat TTL** — agents on gateways >90s expired = excluded
4. **SLA lazy evaluation** — `min(wait_time / sla_target, max_score)` at queue head only
4b. **`sla_target_ms` é ALVO DE ESPERA EM FILA, nunca de atendimento total** *(D14.1, decidido
   2026-08-24)*. É **alvo** (soft): o aging cresce até ele e o `breach_bonus` acelera depois — o
   contato **sobe na fila**, nada é encerrado. Quem encerra é o **teto** (`queue_config.max_wait_s`
   e `queue_max_wait_by_channel`, onde **`0` é VETO**), e confundir os dois é o erro que fez metade
   do parque carregar prazo de processo num campo que não segura ninguém: `limite_entrega` com 7
   dias não retém por 7 dias, só torna o aging inerte. Licença de IA tampouco passa por aqui — a
   admissão tem portão próprio (`{t}:admission:kind:ai`). O campo tem **sete consumidores em
   comportamento e relatório** (`scorer.py:177` · `decide.py:287` · `saturated.py:92/109/126` ·
   `main.py:1055`, que publica ETA **ao cliente** · `query.py:240` · `reports_query.py:3803` ·
   `:5827`) e **nenhum** que o leia como atendimento total — o rótulo *"Total service SLA"* e o
   comentário de contrato (`agent-registry.ts:390`) mentem sozinhos, e a barra do Console que
   pareceria consumi-los lê constantes (`server.ts:1628`). Ver `TODO.md` § D14.1.
5. **Tie-breaking** — equal-score pools broken by shortest queue length
6. **close_reason detection** — `no_resource` when no queue; `max_wait_exceeded` by lazy eval
7. **O score do ZSET de fila é CHEGADA (`queued_at_ms`), nunca prioridade — e toda janela de
   leitura é `ZRANGE` (menor score = mais antigo).** Escritor único (`add_queued_contact`), e
   prioridade não é armazenável: `score_contact_in_queue` depende de `now_ms` (aging/breach crescem
   com a espera), logo é recomputada na LEITURA, sobre a janela. Ler pela outra ponta seleciona os
   mais NOVOS e deixa os antigos sem pontuação nenhuma — o aging fica inerte justamente para quem
   ele existe para proteger. Aconteceu em `get_queued_contacts` (dequeue, pools push) e `listQueue`
   (inbox pull) até 2026-08-05, autorizado por um docstring que prometia override por prioridade.
   Gate: `infra/test/probe_queue_window_order.sh`. Detalhe: `docs/arcos/queue-attended-model.md`
   § "Ordem da fila".

## Rules Engine — Scope

Consumes: `conversations.routed`, `conversations.queued`, `conversations.abandoned`, `agent.done`. Publishes: `rules.escalation.events`, `rules.shadow.events`, `rules.session_tagged`. Does NOT: monitor Redis, evaluate sentiment, make routing decisions, maintain state between events.

---

## Kafka Topics

| Topic | Producer | Consumer(s) |
|---|---|---|
| `conversations.inbound` | Channel Gateway | Core, Routing Engine |
| `conversations.routed` | Routing Engine | Core, Rules Engine |
| `conversations.queued` | Routing Engine | Rules Engine |
| `conversations.abandoned` | Routing Engine | Core, Rules Engine |
| `conversations.session_opened/closed` | Core | Analytics, LGPD |
| `conversations.message_sent` | Core | Analytics |
| `conversations.participants` | orchestrator-bridge | analytics-api → ClickHouse |
| `rules.escalation.events` | Rules Engine | **nenhum** (telemetria de medição, destino a definir — ver TODO). A escalação em si é HTTP: `escalator` → `conversation_escalate` no mcp-server |
| `rules.shadow.events` | Rules Engine | Analytics |
| `registry.changed` | Agent Registry | Routing Engine, Core, orchestrator-bridge |
| `config.changed` | Config API | orchestrator-bridge, routing-engine |
| `gateway.heartbeat` | Channel Gateway | Routing Engine |
| ~~`agent.done`~~ | **REMOVIDO 2026-07-27** — era publicação órfã e dupla; a conclusão de atendimento é o evento `agent_done` dentro de `agent.lifecycle` (+ `outcome` no `contact_closed`) | — |
| `queue.position_updated` | Routing Engine | Channel Gateway, Analytics |
| `mcp.audit` | McpInterceptor / proxy sidecar | Analytics, LGPD |
| `sentiment.updated` | AI Gateway | analytics-api |
| `evaluation.events` | evaluation-api (requested), session-replayer (requested), mcp-server-plughub (completed) | session-replayer + routing-engine (requested→avaliador); evaluation-api (completed→ingest, persiste result+instance); analytics-api → ClickHouse |
| `workflow.events` | workflow-api | skill-flow-worker |
| `collect.events` | workflow-api | analytics-api |
| `session.signals` | mcp-server-plughub (`survey_record`) | analytics-api → ClickHouse |
| `journey.merges` | mcp-server-plughub (`journey_merge`) | analytics-api → ClickHouse `journey_aliases` (Journey J3) |
| `speech.metrics` | Channel Gateway (bot leg WebRTC) · `speech-check` (verificação ativa, VOZ-23) | analytics-api → ClickHouse `speech_stream_summaries` / `speech_collect_outcomes` / `speech_checks` — só números, nunca texto (VOZ-22) |
| `usage.events` | Core, AI Gateway, Channel Gateway | usage-aggregator |
| `events.dead_letter` | skill-flow-worker, analytics-api, orchestrator-bridge | ops/monitoring |

## Kafka Event Schemas — Zod Coverage

All cross-package Kafka events have Zod schemas in `@plughub/schemas`:

| Topic | Schema | File |
|---|---|---|
| `rules.escalation.events` | `RulesEscalationEventSchema` | `rules-events.ts` |
| `registry.changed` | `RegistryChangedEventSchema` | `platform-events.ts` |
| `config.changed` | `ConfigChangedEventSchema` | `platform-events.ts` |
| `sentiment.updated` | `SentimentUpdatedEventSchema` | `platform-events.ts` |
| `queue.position_updated` | `QueuePositionUpdatedEventSchema` | `platform-events.ts` |
| `conversations.routed/queued` | `ConversationRoutedEventSchema` | `platform-events.ts` |
| `agent.lifecycle` | `AgentLifecycleEventSchema` | `platform-events.ts` |
| `workflow.events` | `WorkflowEventSchema` | `workflow.ts` |
| `collect.events` | `CollectEventSchema` | `workflow.ts` |
| `usage.events` | `UsageEventSchema` | `usage.ts` |
| `conversations.participants` | `ConversationParticipantEventSchema` | `contact-segment.ts` |
| `mcp.audit` | `AuditRecordSchema` | `audit.ts` |
| `evaluation.events` | `EvaluationEventSchema` | `evaluation.ts` |
| `session.signals` | `SessionSignalEventSchema` | `survey.ts` |
| `journey.merges` | `JourneyMergedEventSchema` | `journey-merges.ts` |
| `speech.metrics` | `SpeechMetricsEventSchema` | `speech-metrics.ts` |

---

## Naming Conventions

```
skill_id:       skill_{slug} (estável)  →  skill_portabilidade_telco   (sem versão no id; versão é do DEPLOY, ver docs/product/skill-versioning-deploy-spec.md; `_v\d+` legado ainda válido)
agent_type_id:  {name}_v{n}            →  agente_retencao_v1
pool_id:        snake_case no version  →  retencao_humano
mcp_server:     mcp-server-{name}      →  mcp-server-crm
tool:           snake_case             →  customer_get
insight:        insight.historico.*    →  customer long-term memory
                insight.conversa.*     →  generated in current session, expires on close
```

### Language Rule — English in code, Portuguese only in display

All technical identifiers MUST be in English: URL routes, TypeScript/Python variable names, function names, interface names, type union values, i18n key names, file names, folder names, navKeys, tab IDs, ABAC field names, Kafka topic names, Redis key patterns, and API endpoint paths.

Portuguese is allowed ONLY in: i18n value strings (the translated text shown to the user) and in business-domain entity IDs (`agente_*`, `skill_*`, `pool_id`, `tenant_id`) that represent named instances configured by the tenant — these are data, not code.

```
✅  route: /config/channels        href: t('nav.channels')    tab: 'report'
❌  route: /config/canais          href: t('nav.canais')       tab: 'relatorio'

✅  agente_retencao_v1   (entity ID configured by tenant — data, not code)
❌  const atendimento =  (TypeScript variable)
❌  def mascaramento():  (Python function)
```

### i18n Invariant — every visible string goes through `t()`

Any change to `platform-ui` that adds or modifies **text visible to the user** MUST:

1. Add the key to **both** locale files (`en/` and `pt-BR/`) before the PR.
2. Use `useTranslation(namespace)` + `t('key')` in the component — never hardcode strings in JSX.
3. Use the existing namespace for the module (see `docs/arcos/platform-ui.md` § i18n) or register a new one in `src/i18n/index.ts`.
4. For helpers **outside React components** that produce translated strings: receive `t` as an explicit parameter — never call `useTranslation` at module level.
5. **Nunca repetir uma chave no mesmo objeto do arquivo de locale.** JSON aceita, o parser fica com a
   ÚLTIMA, e tudo que só existia na anterior deixa de existir — a tela passa a mostrar a CHAVE no
   lugar do texto. Medido em 2026-08-28: `"catalog"` duas vezes em `dashboards.json` derrubou três
   rótulos e virou um cartão chamado `catalog.volume-by-channel.label` na Home; a varredura achou
   mais 7 casos em 6 namespaces. **Paridade EN × pt-BR NÃO detecta** — os dois arquivos quebram
   igual e a paridade fica perfeita. Gate próprio: `infra/test/probe_i18n_duplicate_keys.sh`.
6. **Título derivável é RENDERING, não dado.** Nunca gravar em store o resultado de um `t()` (título
   de cartão, rótulo de coluna): congela a língua da criação e, se o namespace ainda não carregou,
   congela a chave crua. Grava-se o FATO (o id/endpoint) e resolve-se no render.

```
✅  <span>{t('header.offline')}</span>
✅  addToast(t('message.saved'), 'info')
✅  function label(x: string, t: TFunc): string { return t(`key.${x}`) }
❌  <span>Offline</span>
❌  addToast("Salvo com sucesso", 'info')
❌  const { t } = useTranslation()   // outside a component/hook
```

## What Never To Do

- Never create a component that routes conversations without going through the Routing Engine
- Never access Redis directly from outside routing-engine or skill-flow-engine
- Never redefine types from `@plughub/schemas` locally in another package
- Never add business logic to mcp-server-plughub — it only exposes tools
- Never create a dependency on `ai-gateway` in TypeScript packages — only Python consumes it
- Never use `export *` in packages — always explicit named exports
- Never implement channel-specific rendering logic in skill-flow — adapters live exclusively in channel-gateway
- Never allow a caller to opt out of MCP audit records — policy defined on the tool
- Never write to `insight.historico.*` directly in PostgreSQL — always via Kafka
- Never expose `original_content` of masked messages to agents — only to authorised roles via audit trail
- Never forward tool calls containing injection patterns
- ⚠️ **MEDIDO SEM CAMINHO VIVO em 2026-09-01 (CAP-06)** — *"Never send tool list to LLM without
  applying `permissions[]` filter from JWT"*. O filtro existe (`inference.py:128`), mas
  `/v1/inference` **não tem chamador** no repositório, `InferenceRequest.permissions` **nunca é
  setado** em Python, e **nenhum** step `reason` declara tools de domínio (o único `tools=[…]` é a
  ferramenta sintética do `output_schema`). Ou seja: não há lista de tools indo a LLM nenhum para
  filtrar. A regra fica — quando a passagem de tools existir, ela vale —, mas com o agravante
  registrado: aquele filtro casa o **nome CRU** da tool, e não o `"{server}:{tool}"` que a borda
  `invoke` e o sidecar usam
- Never write masked input values to `pipeline_state`, Redis, stream, or logs
- **Never let an INVITED participant emit `@mention` with routing effect — only the one
  CONDUCTING the session (`role: primary`).** O eixo é **POSIÇÃO**, nunca espécie: humano e IA
  são simétricos aqui, como no resto do modelo de sessão. *(Reescrita em 2026-09-12, MEN-01. A
  v1 dizia "Never allow AI agents to emit @mention — only `role: primary` or `role: human`", e
  foi **medida falsa em 2026-09-01**: a IA que conduz a conversa É a `primary` — 1144 segmentos
  `native/primary` + 100 `ai/primary` contra 333 `human/primary` —, então o gate deixava passar
  exatamente a população que a frase nomeava. E `role: human` nunca existiu no domínio de papel;
  era ramo morto em dois sites.)* A decisão mora em UMA casa
  (`lib/participant-role.ts::mayRouteMentions`) e vale nos **dois** caminhos — tool MCP
  `message_send` e WebSocket do Console. **Falha FECHADA**: sem leitura positiva do roster
  `session:{id}:participants`, não roteia
- Never call `redis.xadd()` directly in mcp-server-plughub — use `writeStreamEntry()`
- **Never leave deferred phases undocumented** — every unimplemented phase MUST be registered in `## Pending`
- Never create a new `packages/my-ui/` standalone frontend app — add a module to platform-ui
- **A borda do channel-gateway é uma ALLOWLIST de sete prefixos — nunca uma proibição.** *(reescrito
  2026-08-10 após medição; a v1 dizia só "never expose `/v1/*`", e proibição é meia regra: um deploy que
  publique tudo menos `/v1` cumpre a letra e expõe `/docs`.)* Publicável: **`/channel` · `/survey` ·
  `/webhooks` · `/voice` · `/webrtc` · `/ws` · `/webchat`**. Interno: **`/v1` · `/health`**, mais os
  implícitos do FastAPI **`/openapi.json` · `/docs` · `/redoc`**, os três respondendo `200` hoje —
  publicá-los publica o MAPA das rotas internas. Metade da lista externa não é produto, é infraestrutura
  de canal (callback de Meta/Twilio, áudio buscado pelo provedor, WebSocket e upload do browser); nenhuma
  entrada é opcional.
  **Por que `/v1` é exigência e não gosto:** dentro dele vive `POST /v1/channels/webhook/pool/{pool_id}`,
  **anônima por construção** — não passa pelo registro (ADR §7.6.1) e por isso **não tem onde pendurar
  credencial**. Publicar o prefixo torna disparável por qualquer um TODO pool webhook do tenant, inclusive
  os que promovem deploy e contatam clientes, e nenhum `auth_required` muda isso. O mesmo prefixo abriga
  RPC interno com nome infeliz (`…/delegate`, `…/collect`, `…/resume/{token}`, `…/identity/*`).
  ⚠️ **A separação externo×interno é de CÓDIGO, não de topologia** — `/channel/webhook/{slug}`
  (`main.py:1302`) e `/v1/channels/webhook/{skill_id}` (`:1387`) são rotas do MESMO app na MESMA porta
  (`docker-compose.demo.yml:1185`); o que as separa é `allowed_origins={"external"}` (`:1347`). **Não
  existe borda versionada no repositório** (sem nginx.conf; `vite.config.ts`/`Dockerfile` não publicam
  `/channel`). `infra/test/probe_edge_surface.sh` **declara** a classificação e reprova prefixo novo sem
  linha na tabela — mas nada verifica o que o deploy realmente publica. Ver
  [`docs/guias/webhook-patterns.md`](docs/guias/webhook-patterns.md) § Exposição na borda e
  [`docs/product/workflow-arc-implementation-spec.md`](docs/product/workflow-arc-implementation-spec.md) §0.1
- **Never create a wide container for a fact that fits a narrow one** — dual da regra abaixo, e as duas só cobrem os dois modos de falha juntas. A regra de escopo sozinha não impediu a `WorkflowInstance` (que não guardou fato largo em campo estreito: criou contêiner novo para o que já era sessão + journey), e o Arc 10 repetiu com a entidade `Journey`. Os três níveis são fechados: **segment** = janela de UM participante · **session** = UM ACESSO (identidade estável através de suspend/resume — duração e nº de segmentos são consequência, não critério) · **journey** = processo sobre N acessos, **derivado** por (proveniência ∪ alias), nunca entidade. Discriminador session↔journey: *nasceu um acesso NOVO?* Outro agrupamento (cliente, campanha) é **filtro**, não journey.

  > **Emenda D10–D13 (aceitas 2026-08-21).** **D11: "contato" é FILTRO, não nível** — esta linha dizia
  > *"session = UM contato"*, e era o nível se confundindo com o recorte que o operador olha. Sessão é
  > qualquer acesso; "contato" é o subconjunto com cliente do outro lado (`spawn_reason` NULL/`collect`),
  > e é assim que `scope=contacts` deve ser lido. **D13:** o discriminador é **ternário sobre
  > `spawn_reason`** (NULL=inbound · `collect`=outbound · `trigger`/`delegate`=interno); `pools.purpose`
  > **sai** do critério — pool é config de roteamento, não classifica acesso. **D10: dois pools, não
  > um** — o da SESSÃO é o de ENTRADA (first-write-wins), o do SEGMENTO é quem ATENDE;
  > `attended_pool_ids` é projeção derivada, e filtrar contato por "pool" sem dizer qual dos dois mente.
  > **D12: espera é fato de ROTEAMENTO**, com produtor próprio (veículo = segmento, id determinístico) —
  > hoje **não existe**. ⚠️ **A linha *(espera)* da D9 está REFUTADA**: `duration_ms` de `role='queue'`
  > mede o flow do agente de fila, não a espera do cliente. Ver [`docs/adr/adr-journey-session-segment-model.md`](docs/adr/adr-journey-session-segment-model.md)
  > e [`docs/guias/conference-mechanics.md`](docs/guias/conference-mechanics.md) § Problema 36.
  >
  > **Emenda D10.1 + D14 (aceitas 2026-08-28).** **D14: SLA é fato do SEGMENTO DE ESPERA, nunca da
  > sessão** — não existe SLA por sessão na prática de contact center, e somar esperas contra alvos
  > diferentes dá número sem uso. Uma sessão carrega **um** alvo, então contato que espera em duas
  > filas perde a violação da segunda. É a regra de escopo outra vez.
  > **✅ ARCO D14 COMPLETO (i→ii→iii) em 2026-08-25.** `analytics.segments.sla_target_ms` existe e é
  > carimbada na saída da fila por `mute_queue.resolve_queue_exit` (um site, a partir do
  > `{t}:pool_config:{p}`; **sem fallback** — ausência vira `null`, porque o cache expira em ~1 h e
  > alvo fabricado no ledger não se corrige por deploy). Alvo **copiado no fechamento**, e vale para
  > **qualquer fila**, sem ramo por `agent_kind` (as duas decisões do dono, D14 ii). Mas os **três**
  > leitores (`query.py` · `_cv_sla_series` · `_sla_eligible`) foram migrados na **(iii)**, e
  > `sessions.sla_target_ms` é **PROJEÇÃO, nunca fonte de cálculo** — regra que deixou de viver só em
  > prosa: o mecanismo é `test_sla_reads_the_segment.py`, que asserta sobre o **SQL EXECUTADO** (não
  > sobre o fonte, onde `grep` contaria o comentário que documenta a migração).
  > ⚠️ É **forward-only**: linha antiga fica `NULL` e não há migração possível (o `first_queued_ms` é
  > consumido na saída). **Decisão do dono: corte da série em data declarada**
  > (`sla_source.SEGMENT_SLA_EPOCH`), não fallback à sessão — fallback preservaria a série misturando
  > duas fontes num número só, sem dizer qual respondeu em cada linha. Medido antes de trocar
  > (`q_sla_source_delta.py`): 51 elegíveis a 70,6% → **1**; encolher é o esperado, não sintoma.
  > ⚠️ **A época não é o que exclui a linha antiga** (o `sla_target_ms > 0` já excluiria): ela separa
  > duas ausências de aparência idêntica — *"não medíamos"* (pré-produtor) × **`{t}:pool_config:{p}`
  > expirado antes do fechamento da espera**. A segunda virou **contador** (`sla_unstamped` no
  > `by_pool`) em vez de silêncio, e é a mesma dívida dos dois TTLs discordantes (86 400 × 3 600).
  > Ver `conference-mechanics.md` § Mudança 41. **D10.1: o `pool_id` do segmento de ESPERA é o DESTINO** (é a dimensão do Fila/SLA —
  > `reports_query.py:5741` — e movê-lo para o pool de fila colapsaria todas as esperas numa linha, já
  > que a fila é a default do tenant); a fila que executou vai em campo **próprio** (`queue_pool_id`).
  > *"Pool de fila sempre distinto do destino"* não é modelo alternativo: é o estado-alvo da CONFIG, que
  > `queue_config.pool_id` já suporta e o `skill_id` legado bloqueia. O TMA não depende dessa escolha —
  > `agent_time_ms` filtra `role IN ('primary','specialist')` e a espera está fora por construção.
- **Never store a narrower-scope fact in a wider-scope field — derive it where the scope is known.** Quatro aplicações vivas: (a) **identidade de participante** é fato de escopo no ContextStore — fato de contato → `session.*`, fato de segmento → `segment.{segId}.*` (ex.: qual humano um hook de wrap-up serve → `segment.{segId}.served_human_participant_id`); nunca num campo de sessão lido por vários componentes (colapsa em multi-humano). (b) **Identidade e membership de instância** são fato de **(recurso, pool)** — derivadas do pool em escopo (`human_agent_{pool}`), nunca congeladas no registro global do recurso; capacidade (`max_concurrent`, semáforo de vagas) é do RECURSO e não fragmenta por pool. (c) **Evento de liveness (heartbeat) nunca carrega identidade nem membership, e nunca cria instância** — só prova que o recurso está vivo; criação é do login. (d) **"Papel" são DOIS fatos, não um** — *propósito do agente* (`agent_role`: `executor`/`orchestrator`/`evaluator`) era fato do ARTEFATO (skill), declarado no registry. ⚠️ **O campo foi REMOVIDO em 2026-09-01** (CAP-01/CAP-03): o gate que o consumia foi removido porque lia o papel de um `participant_id` vindo do INPUT, tendo o `instance_id` assinado do token em mãos — passava quem nomeasse qualquer avaliador. A distinção de ESCOPO abaixo continua inteira e é o que esta linha existe para proteger; o que caiu foi o uso do campo como controle. *Papel de participação* (`primary`/`specialist`/`supervisor`) é fato de **(participante, sessão)** e NÃO cabe no hash da instância — a mesma instância atende `max_concurrent_sessions` sessões e é `primary` numa e `specialist` noutra ao mesmo tempo. Ler os dois do mesmo campo foi o que deixou o gate de `evaluation_context_get` sem produtor e, por isso, falhando ABERTO sobre `original_content` desmascarado — e o desfecho de 2026-09-01 foi que o gate inteiro saiu, porque **consertar exige um cenário que o justifique** e nenhum fechava. A detecção de avaliador mal configurado mudou de casa: valida-se no create/update de campanha que o `evaluator_pool` roda um flow que invoca as tools de avaliação (CAP-04) — discriminador DERIVADO do artefato, nunca campo declarado, que seria o eixo renascendo com outro nome. See [`docs/adr/adr-participant-identity-single-source.md`](docs/adr/adr-participant-identity-single-source.md), [`docs/adr/adr-human-agent-pool-scoped-identity.md`](docs/adr/adr-human-agent-pool-scoped-identity.md)
- **Never run `prisma db push --accept-data-loss` as part of normal agent-registry boot** — it diffs the live schema and drops whatever diverges (has caused real data loss twice). Normal boot always runs `packages/agent-registry/scripts/bootstrap-db.js` (auto-detects fresh/legacy/migrated DB state, only ever applies `prisma migrate deploy`). The destructive path only runs when `FRESH_INSTALL=true` is set on purpose (`infra/scripts/fresh-install.sh`)

## SDK CLI

```bash
plughub-sdk certify            # validates execution contract
plughub-sdk verify-portability # verifies dependency isolation
plughub-sdk regenerate         # regenerates proprietary agent as native
plughub-sdk skill-extract      # extracts skill from existing agent
plughub-sdk proxy              # starts proxy sidecar on localhost:7422
```

## Operational Visibility — Section 3.3c

Routing Engine escreve `{tenant_id}:pool:{pool_id}:snapshot` (TTL 3600s) — `{ pool_id, available,
busy, busy_elsewhere, untagged, paused_capacity, total_instances, queue_length, sla_target_ms,
channel_types, model, updated_at }`.

- **Ocupação é DERIVADA do semáforo do RECURSO, nunca de um contador.** `{t}:pool:{p}:active_count`
  foi removido: contava por POOL uma capacidade que é do RECURSO (1 humano de 3 vagas em 3 pools dava
  soma 6 para uma verdade de 2). `current_sessions` não foi promovido a fonte — é da mesma família.
- **A LINHA FECHA:** `total_instances = busy + busy_elsewhere + paused_capacity + available`. Os dois
  do meio são obrigatórios; sem eles a conta não fecha e alguém reverte para o modelo sem pausa e sem
  capacidade compartilhada.
- **Pausa é fato da ARITMÉTICA** — instância inativa contribui capacidade zero e MANTÉM a ocupação; o
  que sai de circulação são as vagas LIVRES. A pausa não limpa o `busy_set` (só o logout limpa).
- **`Σ available(pool)` NÃO é somável**, e não é corrigível na linha do pool. O rollup
  `{t}:capacity:snapshot` agrega instâncias DISTINTAS **por TIPO de licença** — humano e IA são moedas
  não-fungíveis, logo não existe `available` escalar no topo. `by_channel` é PROJEÇÃO, não partição.
- **Pico é EVENT-DRIVEN, gravado na TRANSIÇÃO** (`record_pool_peak`, três chamadores e nenhum a mais).
  O bump NUNCA mora dentro de `write_pool_snapshot` — lá volta a ser amostragem, sem ficar vermelho.
- **Contador só existe porque é CONFERIDO** (`reconcile_tenant_occupancy`, 1×/min, loga o drift) e
  **não clampa negativo**: total impossível é a única evidência de caminho de vaga fora dos ganchos.
- **`untagged` denuncia escritor de ocupante fora do `claim_instance`** — deve ir a zero em ≤24 h.
- **`pool_status_get` devolve `available: null` sem snapshot**, nunca o `SCARD` de pertencimento, que
  conta instância lotada como disponível num tool que decide oferta de canal AO CLIENTE.

Três MCP tools (grupo `operational`): `queue_context_get`, `pool_status_get`, `system_availability_check`.

→ See [`docs/arcos/operational-visibility.md`](docs/arcos/operational-visibility.md),
[`docs/product/shared-capacity-pool-as-tag-design.md`](docs/product/shared-capacity-pool-as-tag-design.md)

## Admissão de sessão — UM gate, na moeda certa

**A admissão NUNCA soma licenças de tipos diferentes** (fatia 3, 2026-08-02). Até aqui ela gateava
toda sessão contra `max_concurrent_sessions` (= `C_ai + C_human`, 370 no demo) — a mesma falácia de
aditividade que o rollup de capacidade recusa no topo, agora do lado que **recusa contato real**:
`shared_full` → outage com humano ocioso, porque 10 licenças humanas rendem 30 sessões servíveis e
contribuíam 10 ao pote. Sobrou um único portão:

```
{t}:admission:kind:ai  ≤  {t}:quota:capacity:ai_agent      # sessão em pool agent_kind='ai'
```

- **Humano NÃO é gateado por sessão** — a licença humana é por LOGIN, cobrada no `agent_login`
  (`instâncias human-* ≥ C_human` ⇒ `human_capacity_exhausted`). Gatear de novo por sessão é gate
  duplo e na unidade errada.
- **Rejeição só na PORTA** (`cause="quota"`, único valor de `AdmissionDecision.cause`); migração de
  sessão ATIVA para IA saturada é fail-open, mantendo a atribuição de origem.
- **`max_concurrent_sessions` sobrevive como número de PROVISIONAMENTO** (`lib/capacity.ts`:
  Σ declarada nos deploys ≤ C), nunca como teto de admissão. Mistura moedas ali também — é o
  defeito **C**, de outra fatia.
- **Não reviver:** SET `{t}:admission:shared`, `{t}:admission:reserved:{pool}`,
  `{t}:admission:member:{sid}`, `session_reservation` como fatia de sessão. Reserva por pool
  fragmenta um recurso que é compartilhado, contra o invariante *"capacidade é do RECURSO"*.
- Instrumentação (item 7a/7b): HASH `{t}:admission:ai_pools` (atribuição por pool de quem debita
  `C_ai`) → linhas de série `__admitted_ai__` e `__buffer__`. `__shared__`/`__reserved__` saíram.
- **Fila muda** (`{t}:queue:unadmitted`) existe por **pool sem `queue_config`** — não por `C`
  esgotado. O overflow por admissão saiu junto com o pote.

Three MCP tools (group `operational`): `queue_context_get`, `pool_status_get`, `system_availability_check`. When contact is queued, Routing Engine publishes `queue.position_updated` to Kafka.

→ See [`docs/product/shared-capacity-pool-as-tag-design.md`](docs/product/shared-capacity-pool-as-tag-design.md)

## Security — Section 9.5

> **Método e casos medidos moram na skill `security-boundaries`** (`.claude/skills/security-boundaries/`,
> 2026-09-16): checklist de rota, chamador interno, config, auditoria, MCP e como testar um portão.
> O texto desta seção foi movido **integralmente** para `references/casos-medidos.md`, com as quatro
> passagens que a conferência do mesmo dia achou velhas listadas no topo. Aqui ficam os invariantes.

- **UM verificador de JWT+ABAC: `packages/py-authz` (`plughub_authz`).** Nunca uma cópia. Única
  exceção deliberada: o EMISSOR (`auth-api/jwt_utils.py`), com `python-jose`. Gate:
  `probe_authz_single_verifier.sh` (linha de base **1**, nunca 0).
- **Os eixos são independentes, e um censo por eixo não prova o vizinho:** credencial de ROTA ·
  CAPACIDADE (`abac_can`) · ESCOPO de linha (`resolve_scope`/`pool_in_scope`) · PERTINÊNCIA de
  conteúdo (`authorize_session_scope`, sessão viva **e** fechada) · recorte de AGREGADO
  (F-A pool-nativo · F-B derivado-de-sessão · F-C dívida em `_SCOPE_DEBT`).
- **`accessible_pools: []` = NENHUM pool** (AUT-03). `None` só para principal de SERVIÇO. Nunca
  `if not pools: <sem filtro>`. `module_config` **ausente** (serviço) ≠ **vazio** (usuário, NEGA).
- **Capacidade é declarada pela ROTA e decide ANTES do escopo.** Duas portas para o mesmo dado são
  gateadas igual.
- **Isenção é DECLARADA, nunca deduzida da ausência** — `_SCOPE_EXEMPT` (decisão) e `_SCOPE_DEBT`
  (dívida com gatilho) são tabelas separadas.
- **Fechar credencial obriga a migrar os chamadores internos no mesmo trabalho.** `X-Service-Token` é
  ADITIVO (token vazio não libera; `sub="service:<nome>"`). **`catch {}` sobre checagem de segurança
  é proibido.** Gate: `probe_internal_service_callers.sh`.
- **Escrita de config exige portão; leitura de runtime aberta é DECISÃO com testemunha.** Gate:
  `probe_config_service_write_gate.sh`.
- **Ao fechar um portão, prove que ele DEIXA PASSAR quem deve**, e meça o que cerca a fronteira antes
  de confiar no verde. **Ramo legado morre CONTADO.**
- **Injection guard**: `withGuard` (`mcp-server-plughub/src/infra/tool-guard.ts`) no registro das
  tools de `bpm.ts`/`workflow.ts`, e no `invoke` do `external-mcp`.

---

## Message Masking, @mention & Masked Input

Token format in stream: `[{category}:{token_id}:{display_partial}]` (e.g. `[cpf:tk_b7d2:***-00]`). Stream stores `content` (masked) + `original_content` (unmasked). Default `authorized_roles: ["evaluator", "reviewer"]`. Domain MCP tools resolve tokens via `McpInterceptor.resolveToken` callback. Channel Gateway strips to `display_partial` only before WS delivery.

**@mention**: **quem CONDUZ menciona; quem foi CONVIDADO não convida** — `role === "primary"`,
humano ou IA indiferentemente (MEN-01/MEN-02, 2026-09-12). Decisão e resolvedor em
`lib/participant-role.ts`, consumidos pelos **dois** caminhos: a tool `message_send` e o WS do
Console, que até então não checava papel nenhum — e enquanto a regra valeu numa porta só, ela não
era garantia da plataforma. Falha FECHADA (sem roster, não roteia). Domain closed by
`mentionable_pools` pool config.
**O `@alias` é COMANDO, não conteúdo (MEN-05/MEN-06)**: ele nunca é persistido como texto — vira o
evento **`mention_command`** no stream (`agents_only`, com o EMISSOR como autor, e é a única casa
durável de *quem convidou quem*, porque `participant_joined` registra quem ENTROU) mais um
**`mention.ack`** ao emissor (`routed` | `unknown_alias`). Sobra a prosa, entregue `agents_only`;
alias sem prosa **não gera mensagem nenhuma**. ⚠️ `mention.ack` (menção roteada, mcp-server) e
`mention_command.ack` (comando executou, bridge) são momentos diferentes e não se substituem. `mention_commands` YAML declares actions: `set_context`, `trigger_step`, `terminate_self`.

**Masked Input**: `masked: true` on menu step (field-level or step-level). `begin_transaction`/`end_transaction` wraps collection-validation-action as atomic block. `@masked.*` namespace in-memory only — never written to Redis, pipeline_state, stream, or logs. Retry always recolects; never re-uses masked values.

→ See [`docs/adr/adr-message-masking.md`](docs/adr/adr-message-masking.md), [`docs/guias/masked-input.md`](docs/guias/masked-input.md), [`docs/guias/mention-protocol.md`](docs/guias/mention-protocol.md)

---

## Session Replayer — Quality Evaluation Pipeline

Pattern: ensure-before-read with optional Hydrator. Pipeline: `session_closed` → Stream Persister (PostgreSQL) → `evaluation.requested` → Hydrator (Redis hit: no-op; miss: PG→Redis) → Replayer (always reads Redis) → `ReplayContext` at `{tenant}:replay:{session_id}:context` (TTL 1h) → Evaluator (evaluation_context_get → evaluation_submit) → `evaluation.events` → ClickHouse.

`ReplayContext` extended for Arc 6: `evaluation_form`, `campaign_context`, `knowledge_snippets` (top-5). **Comparison Mode**: `comparison_turns` with Jaccard similarity (threshold 0.4); `buildComparisonReport()` with divergence_points. `ReplayEvent.delta_ms` preserves original intervals; `speed_factor` scales timing (default 10x batch).

**R5/B — tier-2 de IA (evidência de execução):** no `session_closed`, além do Stream Persister, o **`PipelineStatePersister`** snapshota o `pipeline_state` (transitions) na tabela durável **`session_pipeline_state`** (a trajetória real não vai ao stream e o Redis tem TTL 24h; substrato reaproveitável pelo R4). `ReplayContext.pipeline_state` = trajetória REAL (PG→fallback Redis; ausente→`na`). `evaluation_context_get` injeta `tool_trace` (analytics-api `GET /v1/audit/mcp-calls?session_id`) + `flow_definition` (trajetória esperada, agent-registry `GET /v1/skills/:flow_id`). Sem input/output snapshot (R7).

→ See [`docs/arcos/session-replayer.md`](docs/arcos/session-replayer.md), [`docs/adr/adr-session-replayer.md`](docs/adr/adr-session-replayer.md)

---

## Session & Conference Lifecycle — Three-Layer Model

Three independent layers must not be collapsed: **(1) contact lifecycle** (customer perspective, statistics frozen at customer departure); **(2) agent segment lifecycle** (each participant's window, pool resource freed at `agent_done`); **(3) conference infrastructure** (the room, destroyed only when all participants leave). The current implementation conflates layers 1 and 3 — `_trigger_contact_close()` currently serves both. Known gaps: G1 (AHT inflated by wrap-up time), G2 (`remaining` ignores AI specialists), G3 (AI instance restored while still running), G4 (supervisor has no heartbeat cleanup), G5 (primary AI close expels supervisor), G6 (redundant restore on agent_done close), **G7** (`on_human_end` decoupled from contact-close **only** for the transfer case — `reason==agent_transfer` branch; generic segment-end semantics, NPS-as-contact-hook, and non-transfer continuations remain debt). Fixes applied 2026-05-10: busy counter on cross-pool transfer, pool counter on queue entry, `agent_done` publish from bridge for native/YAML-fallback agents. **Console Transfer (2026-06-12)**: `POST /api/session_transfer` + bridge `agent_transfer` branch make human→pool transfer functional (origin leaves as segment-end, contact continues via re-route, no premature close). See `docs/guias/conference-mechanics.md` § Mudança 9.

→ See [`docs/arcos/session-conference-lifecycle.md`](docs/arcos/session-conference-lifecycle.md)

---

## Usage Metering

Kafka topic `usage.events` — `UsageEventSchema`: `event_id`, `tenant_id`, `session_id`, `dimension`, `quantity`, `source_component`, `metadata`. No pricing in usage records — metering ≠ pricing.

Dimensions wired: `sessions` (Core, SET NX guard), `messages` (Core, visibility=all), `llm_tokens_input/output` (AI Gateway), `webchat_attachments` (Channel Gateway). Pending: `whatsapp_conversations`, `voice_minutes`, `sms_segments`, `email_messages` (functions ready, adapters not yet wired).

Redis: `{t}:usage:current:{dimension}` (45d), `{t}:quota:limit:{dimension}`, `{t}:quota:concurrent_sessions`. `assertQuota` (INCRBY-check-rollback). Cycle reset: `POST /admin/cycle-reset` (port 3950).

→ See [`docs/arcos/usage-metering.md`](docs/arcos/usage-metering.md)

---

## WebChat Channel — Hybrid Stream Model

Three distinct channels: `webchat`, `webrtc`, `whatsapp`. Client is NOT a named participant — Channel Gateway does XREAD on `session:{id}:stream` directly. Reconnect via cursor: zero messages lost. WebchatAdapter: 3 concurrent async tasks (receive_loop, stream_delivery_loop, typing_listener).

Upload (2-stage): WS `upload.request` → `upload.ready` (file_id, upload_url) → HTTP POST binary → `upload.committed` → WS `msg.image/document/video`. MIME allowlist: JPEG/PNG/WebP/GIF (16MB), PDF (100MB), MP4/WebM (512MB). Expiry: soft-delete hourly (410 dali em diante), blob delete daily (+24h grace) — task de boot `attachment-expiry` (`attachment_expiry.py`), nos dois backends. ⚠️ Até 2026-09-13 (VOZ-07) isto estava descrito aqui e em nenhum código: anexo nenhum expirava. Gate: `infra/test/probe_attachment_expiry.sh`. JWT via message body, never URL. `jwt_secret` per tenant via Redis `{tenant_id}:config:webchat:jwt_secret`.

Masked fields delivery chain: `step.masked` → `notification_send` args → `conversations.outbound` Kafka → `WsMenuRender.masked_fields` → `interaction.request` WS event → `<input type="password">` overlay in webchat.

→ See [`docs/adr/adr-webchat-channel.md`](docs/adr/adr-webchat-channel.md)

---

## Pricing Module — Capacity-Based Billing

`packages/pricing-api/` — Python FastAPI, port 3900. Billing by configured capacity, not consumption. Two components: **base capacity** (monthly pro-rated, billing_days) + **reserve pools** (full-day billing per activation day). `billing_cycle_day` default 1. `reserve_markup_pct` default 0%.

Endpoints: `GET /v1/pricing/invoice/{tenant_id}` (JSON + `?format=xlsx`), `POST /v1/pricing/resources/{tenant_id}`, `POST /v1/pricing/reserve/{tenant_id}/{pool_id}/activate|deactivate`. Config API namespace `pricing`: `unit_prices`, `reserve_markup_pct`, `billing_cycle_day`, `currency`. Platform-UI BillingPage at `/config/billing` (role: admin). Quota limits written to Redis on plan activation — not seeded by Config API.

→ See [`docs/arcos/pricing.md`](docs/arcos/pricing.md)

---

## Pool Lifecycle Hooks

Hooks declared in pool YAML (`PoolHooks.on_human_start`/`on_human_end`/`post_human`). Bridge dispatches synthetic `conversations.inbound` with `conference_id` — reuses 100% of conference infrastructure.

**on_human_end** → NPS + wrap-up agents activated in parallel. NPS visibility = `["@ctx.core.contact.customer_participant_id"]` (customer-only). Wrap-up visibility = `["@ctx.core.contact.human_agent_participant_id"]` (agent-only). **Phase B**: `agent_done` does NOT close WS; bridge holds close until all hook agents complete. `hook_pending` Redis counter controls when `_trigger_contact_close()` fires. **Phase C**: `post_human` hooks fire after all `on_human_end` agents complete. Participation events (`conversations.participants`) written by bridge for analytics.

Pre-hook ContextStore writes (before hooks fire): `core.contact.close_origin`, `core.contact.customer_participant_id`, `core.contact.human_agent_participant_id`.

→ See [`docs/guias/pool-hooks.md`](docs/guias/pool-hooks.md), [`docs/guias/conference-mechanics.md`](docs/guias/conference-mechanics.md)

---

## Arc 5 — ContactSegment Analytics

`ContactSegment`: `segment_id`, `session_id`, `participant_id`, `pool_id`, `role`, `agent_type`, `parent_segment_id` (null for primary), `sequence_index`, `started_at`, `ended_at`, `duration_ms`, `outcome`, `close_reason`. Conference topology: specialist `parent_segment_id` → primary `segment_id`. Sequential handoffs: `sequence_index` increments.

ClickHouse tables: `analytics.segments` (`ReplacingMergeTree` ORDER BY `(tenant_id, session_id, segment_id)`), `analytics.session_timeline` (enriched with `segment_id`), `mv_agent_performance_daily` (AggregatingMergeTree), `mv_segment_summary`. Endpoints: `GET /reports/segments`, `GET /reports/agents/performance`, `GET /reports/agent-performance/daily`, `GET /reports/sessions/complexity`.

→ See [`docs/arcos/arc5-segments.md`](docs/arcos/arc5-segments.md), [`docs/adr/adr-contact-segments.md`](docs/adr/adr-contact-segments.md)

---

## AI Gateway — Multi-Account Rotation

`AccountSelector` in `account_selector.py` — Redis-backed, stateless per call. Algorithm: for each account, check throttle key (`ai_gw:{provider}:{key_id}:throttled`); score = `rpm_used/rpm_limit × 0.7 + tpm_used/tpm_limit × 0.3`; pick lowest score. On 429/529: `mark_throttled` → next account → cross-provider fallback (`FallbackConfig`).

Config: `PLUGHUB_ANTHROPIC_API_KEYS=sk-1,sk-2,sk-3` (multi-key activates AccountSelector). `PLUGHUB_OPENAI_API_KEYS` optional fallback. Model profiles (`ModelProfile` Literal = `fast | balanced | powerful | evaluation`): `fast`/`powerful` (antigo `realtime`), `balanced` (Haiku), `evaluation` (Haiku — carga isolada; o Literal do request DEVE incluir `evaluation`, senão o Pydantic 422 antes do mapa). Config API namespace `ai_gateway`: `account_rotation_enabled`, `throttle_retry_after_s`, `evaluation_model`.

**LLM Accounts Catalog (2026-07-01)**: config-api namespace `llm_accounts` (platform-ui: Resources → LLM Accounts) stores non-secret account metadata (`provider`, `display_name`, `rpm_limit`, `tpm_limit`, `active`) per catalog id; the API key itself stays exclusively in env var `PLUGHUB_LLM_ACCOUNT_<ID_UPPER_SNAKE>_API_KEY` on ai-gateway (naming-convention binding, no stored env-var-name field). ai-gateway loads the catalog at boot (`load_llm_accounts_catalog()`), falling back gracefully to the legacy `PLUGHUB_ANTHROPIC_API_KEYS`/`PLUGHUB_OPENAI_API_KEYS` construction if config-api is unreachable. `Pool.llm_account_ids: string[]` (preference order) is written to ContextStore as `core.pool.llm_account_ids[]` by Routing Engine, read by the skill-flow-engine `reason` step, and forwarded as `preferred_config_ids` to `AccountSelector.pick()` — same fallback semantics as the pre-existing evaluation-campaign usage. `ReasonEngine` (`/v1/reason`) was upgraded to be account-aware as part of this change (it previously had no multi-account support, unlike `/v1/inference`).

→ See [`docs/arcos/ai-gateway.md`](docs/arcos/ai-gateway.md)

---

## Arc 8 — Agent Availability & Pause Tracking

Pipeline for tracking human agent pauses. Config API namespace `agent_activity`, key `pause_reasons` (seedable pause reason list). Pause endpoints: `PUT /api/agent-pause` and `PUT /api/agent-resume` in mcp-server-plughub — updates Redis state, publishes `agent_pause`/`agent_ready` to `agent.lifecycle` Kafka with `reason_id`/`reason_label`. ClickHouse table: `agent_pause_intervals` (ReplacingMergeTree). Analytics: `GET /reports/agent-availability` with pool scoping. Platform-UI: a bancada é o **modo comparar** de `/analise/resources` (F3 do ADR de relatórios, 2026-08-29 — `/analise/agents` virou redirect; a `AgentReportsPage.tsx` era órfã, com rota `/contacts/reports/agents` inexistente, e foi REMOVIDA na F0, 2026-08-28).

→ See [`docs/arcos/arc8-agent-availability.md`](docs/arcos/arc8-agent-availability.md)

---

## Frontend Architecture — platform-ui

Single-app shell in `packages/platform-ui/`. Design tokens: `primary=#1B4F8A`, `secondary=#2D9CDB`, `accent=#00B4D8`, `green=#059669`, `warning=#D97706`, `red=#DC2626`. Font: Inter. Never use inline hex — Tailwind tokens only.

Roles: `operator` (Monitor+Contacts), `supervisor` (+Evaluation+Reports), `admin` (+Config+Skills), `devops` (+Fluxo/DevTools — chamado `developer` até 2026-09-08; o preset era de AUTOR DE FLUXO, papel que os DialogForms tornaram obsoleto), `business` (cross-cutting, no operational items). **ABAC gates** on nav items: `contacts.monitorar` gates Monitor (observar), `agent_assist.atender` gates Console/fila (atender) — eram um campo só até a MOD-05; `skill_flows.operacao` gates Editor/Deploy; `visualizar` gates Reports/Análise tabs.

Nav groups (navKey): Home 🏠, Console 🖥️ (agent_assist.atender), Monitor 📡 (Sessions/Agents/Pools/Events/Processes), Fluxo 🔄 (Editor/Deploy → skill_flows.operacao), Avaliação ✓ (Forms/Campaigns/Knowledge/Evaluations), Analytics 📊 (Sessions/Agents/Events/Processes/Quality → visualizar/report), Configuração ⚙️ (Dashboards/Resources/Platform/Channels/Calendars/Masking/Billing/Access). Legacy redirects: `/workflows` → `/workflow/monitor`, `/skill-flows` → `/agent-flow/editor`, `/reports` → `/contacts?tab=analise`.

**Skill Deploy Lifecycle** *(reescrito na PID-08, 2026-09-14)*: deploy é do **POOL** — `PUT /v1/pools/:id/slots/next` → `POST /v1/pools/:id/promote` (ou a tool `pool_promote`), e o promote é o **único escritor** de `skill_deployments`. `PUT /v1/skills/:id` salva a definição e não muda o que roda; `deploy_status` e `x-skill-publish` são vestigiais desde 2026-07-13 (uma definição, sem rascunho). `POST /v1/skills/:id/deploy` responde **410**: gravava "implantado nos pools X" sem tocar slot, com zero usos reais. Gate: `infra/test/probe_batch_deploy_retired.sh`.

**Agent Assist UI** at `/agent-assist`: 4-tab right panel (Estado, Capacidades, Contexto, Histórico). Substitution mode for menu cards. Visibility array routing for NPS/wrap-up agents. Optimistic echo for button selections.

→ See [`docs/arcos/platform-ui.md`](docs/arcos/platform-ui.md)

---

## Arc 7 — Auth, RBAC + ABAC, Performance Routing

**auth-api** (porta 3200): users + sessions no schema PG `auth`. JWT HS256 TTL 1h; refresh token opaco
de 43 chars com rotação e SHA-256 no store. `accessible_pools[]` no JWT filtra LINHAS na analytics-api.
**ABAC** (`module_config` no JWT, `auth.module_registry` semeado de `infra/modules.yaml`): **11 módulos**
*(medido 2026-09-08; dizia `8` e o catálogo tinha 12 — a contagem em prosa não tem mecanismo, então
envelhece calada. `workflows` saiu na MOD-11)*,
cada campo com `access: none|read_only|read_write` + `scope[]` *(`write_only` saiu
em 2026-09-09, AUT-40: 0 domínios o ofereciam e 0 grants o usavam, e ele era o único
ponto em que a ordem do `py-authz` e a lista indexada da UI discordavam)*;
`PermissionChecker.can(module, field, minAccess?, scopeId?)`. **Roteamento por performance**:
`performance_score = resolution_rate × (1 − escalation_rate)`, blending por `performance_score_weight`
(default 0.0), Redis `{tenant}:agent_perf:{agent_type_id}` (TTL 6h), batch a cada 5 min.

- **ADMINISTRAR uma pessoa nunca é o mesmo campo que CONCEDER capacidade a ela.** `config.users` =
  pessoa (criar, editar, ativar, grupos); `config.permissions` = capacidade (papéis, módulos, escopo).
  O portão tem **QUATRO portas** — rota, corpo, alvo e escopo — e fechar só a primeira é decorativo.
  O discriminador do corpo é `model_fields_set`, não o valor: enviar o campo é conceder.
- **Contratar é contratar PARA UM TIME** *(AUT-44)*: a criação declara o grupo em que a
  pessoa nasce — obrigatório para quem administra por DELEGAÇÃO, que senão emite conta
  que não vê nem edita; opcional para `admin`, sob pena de trancar o dono do tenant novo.
- **Papel é PRESET DE NASCIMENTO, nunca portão.** `role_defaults` aplicado UMA vez, na criação; trocar
  o papel depois não reescreve grants, e múltiplos papéis rendem o MAIOR acesso por campo.
- **O menu tem um portão só, e ele é GRANT-FIRST.** Ausência de grants nunca é autorização — mesma
  inversão de `accessible_pools`, pela mesma razão.
- **ESCOPO e CAPACIDADE são eixos distintos**, e um claim de escopo nunca concede capacidade.
- **POOL é dado do TENANT, nunca da plataforma — logo seed de plataforma NÃO declara
  `accessible_pools`, e a atribuição é PÓS-CRIAÇÃO do usuário.** Pools são criados
  dinamicamente pelo tenant; um seed que os enumerasse estaria inventando dado que não é dele
  (o `seed_auth.py` já carregou 22 de 36 assim, e eram resíduo de teste). Consequência que
  parece defeito e não é: usuário recém-criado nasce com `accessible_pools = []`, o que desde a
  AUT-03 significa **nenhum pool**, e por isso **não vê linha nenhuma** em relatório escopado
  até alguém lhe atribuir escopo. *(Medido em 2026-09-08: `admin@` com `{}` respondendo `200` e
  0 linhas contra 1 196 sessões — install não provisionado, não bug. Diagnostiquei errado duas
  vezes antes de o dono corrigir; os dois fatos moram aqui para que a terceira não aconteça.)*
  ⚠️ **Corolário para instrumentos:** *"pool sem vigia"* só é medível excluindo fixtures — o
  aviso `orphansAfter` da tela lê verde porque `probe@` carrega todos os pools; sem ela, 36 de
  41 estão sem vigia. Ver `AUT-43`.

Dois corolários de MÉTODO que este arco produziu, e que ficam aqui por serem regra de implementação:
**(1)** um campo cujo rótulo tem **"e"** provavelmente são dois fatos — e se um deles concede
capacidade, é chave-mestra até prova em contrário; **(2)** quando a correção pode ser *"marcar cada
caso"* ou *"remover a alternativa"*, a segunda é a que não depende de memória.

→ See [`docs/arcos/arc7-auth.md`](docs/arcos/arc7-auth.md)

---

## Arc 6 — Quality Evaluation Platform

**evaluation-api** (port 3400): Forms CRUD, Campaigns (sampling + reviewer rules + contestation policy), Instances (auto-created by sampling engine on `session_closed`), Results, Contestations. Auth: admin via `X-Admin-Token`; review/contest via `Bearer JWT` with ABAC `module_config.evaluation.revisar/contestar`. `available_actions: ["review"|"contest"]` computed server-side — never client-side. Anti-replay: `round` field must match `result.current_round` or 409.

**Workflow as review motor — LEGADO/superseded (decisão 2026-06-25, S2.4).** O contrato canônico de contest→review→finalize é o **Arc 13 REST** (`contestation_router`: `file_contestation` → `submit_review` → `finalize_evaluation`, que emite `evaluation_finalized`). O motor por workflow (`campaign.review_workflow_skill_id`, e.g. `skill_revisao_treplica_v1`) é **paralelo e inerte**: nada no backend o dispara (`review_workflow_skill_id` é só config armazenada, lida pela UI; o único trigger é o harness e2e cenário 28), e a evaluation-api só **reage** (`workflow.events` consumer: suspended → `action_required`/`resume_token`; completed/timeout → `lock_result`, **não finaliza**). Mantido reactive-only por compat com o cenário 28; **não usar como contrato**. Remoção física (consumer, coluna `review_workflow_skill_id`, seletor da UI) = follow-up opcional.

**mcp-server-knowledge** (TypeScript, port 3401): pgvector knowledge base for RAG. Tools: `knowledge_search`, `knowledge_upsert`, `knowledge_delete`. **agente_avaliacao_v1**: loads form + knowledge snippets via `evaluation_context_get`, scores each criterion with evidence, submits via `evaluation_submit`. Analytics: `evaluation_results` + `evaluation_events` ClickHouse tables; `GET /reports/evaluations` + `/reports/evaluations/summary`.

**Real-evaluator persistence path** (validated 2026-06-17): the flow never `claim`s — `evaluation_submit` publishes `evaluation.completed` to `evaluation.events`, and the evaluation-api **ingest consumer** (`evaluation-api-ingest-consumer`, idempotent) maps it → `_ingest_core` (POST-ingest core) → `EvaluationResult` in Postgres + instance → `completed`. Reads (`/v1/evaluation/results`) and the Avaliações UI come from Postgres; ClickHouse is analytics-only. The agente_avaliacao_v1 reason step reads the transcript from `ReplayContext.context.events` (the model field is `events`, not `replay_events`). The current `evaluation_submit` carries a compat shim for the prompt×schema drift (fixed `evaluation_rubric_v3` + lossy `_format_schema` conveyance) — to be removed by the form-driven prompt revision. See [`docs/arcos/arc6-evaluation.md`](docs/arcos/arc6-evaluation.md).

→ See [`docs/arcos/arc6-evaluation.md`](docs/arcos/arc6-evaluation.md)

---

## Arc 4 — Workflow Automation

**workflow-api** (port 3800): `WorkflowInstance` lifecycle. Endpoints: `/trigger`, `/instances/{id}/persist-suspend`, `/resume`, `/complete`, `/fail`, `/cancel`. Timeout scanner: background task, 60s interval, atomic UPDATE. Kafka topic `workflow.events` (7 event types).

**Suspend step**: `reason: approval|input|webhook|timer`, `timeout_hours`, `business_hours` (uses calendar-api). Two-stage idempotency sentinel. **collect step**: contacts target via channel, suspends until response or timeout. `collect_token` for correlation; `campaign_id` as free-form grouper across instances.

**Calendar API** (port 3700): pure engine. Functions: `is_open`, `next_open_slot`, `add_business_duration`, `business_duration`. Feriados recorrentes `MM-DD`. Status 3-state: `open/closed/holiday`. Timezone per tenant. 4 MCP tools wrapping calendar engine.

**Webhooks**: ⚠️ **as 8 rotas de webhook deste serviço foram REMOVIDAS em 2026-09-08 (MOD-11)** — as 7 do CRUD (`/v1/workflow/webhooks*`, `X-Admin-Token`) e a porta pública `POST /v1/workflow/webhook/{id}`. O registro único de endereço de webhook é o **`ChannelEndpoint`** do agent-registry (`/v1/channel-endpoints`, tela `/config/channels`, campo `config.channels`), por decisão do `adr-webhook-endpoint-single-registry` — cuja D6 já carimbava as linhas daqui como procedência `legacy_token`. Não houve migração porque não havia dado: `workflow.webhooks` foi medida em **zero linhas** contra 13 endpoints webhook vivos no registro. As tabelas ficam de pé (vazio não custa; apagar schema é outra decisão). `origin_session_id` in WorkflowInstance links workflow to parent contact session.

**Skill Deploy** (Phase 2): deploy = `set-next` → `promote` do POOL, que grava `skill_deployments` e publica `registry.changed`. Promote agendado = Agenda do scheduler-api sobre `deploy_promote_ia` (`skill_deploy_promote_v1` → `pool_promote`). `GET /v1/skills/:id/handoff-status` for safe deploys. ⚠️ O deploy em lote por skill (`POST /v1/skills/:id/deploy`, tool `skill_deploy`, workflow `skill_scheduled_deploy_v1`) foi aposentado na PID-08 — registrava sem mudar; o lote de verdade sobre slots é `POST /v1/pool-slots/promote-batch` (PID-16): lista explícita de pools, UM snapshot, a config de cada pool, tudo ou nada; rollback segue por pool.

→ See [`docs/arcos/arc4-workflow.md`](docs/arcos/arc4-workflow.md)

---

## Arc 9 — Agent Groups & Supervisor Scope

`AgentGroup` is a people-management entity, orthogonal to Pool (Pool = routing; Group = org chart). Tables in `auth` schema: `agent_groups`, `agent_group_users`, `agent_group_supervisors`.

**Members/Shifts removed (2026-07-02)**: `agent_group_members` (agent_type_id + is_human) and `agent_group_shifts` (days_of_week[], time_start/end TIME, timezone) were removed — `is_human` was an unvalidated second source of truth for human/AI typing (`Pool.agent_kind` is canonical); differing shift needs are now modeled as separate groups, not per-member time windows. Tables may still exist physically in older DBs — code no longer creates/reads/writes them.

**Login/refresh denormalization**: `resolve_supervisor_scope(pool, user_id, role)` in auth-api returns `(supervised_groups, supervised_user_ids)` — membership-only, no shift gating, no agent_type expansion. JWT carries `supervised_groups[]`, `supervised_user_ids[]`. Admin role → `([], [])` = no restriction.

**analytics-api scope filtering**: `supervised_agent_types` claim is no longer emitted by auth-api. `PoolPrincipal.supervised_agent_types` / `_apply_agent_scope()` / `_agent_scope_session_join()` still exist in code (not removed) but `payload.get("supervised_agent_types", [])` now always resolves to `None` → permanent no-op. `accessible_pools` (Arc 7) still applies its own pool-level filter on the same endpoints, unaffected.

**auth-api REST** (`/v1/groups`, Bearer + ABAC `config.users`): CRUD for groups + `users` (members) + `supervisors` sub-resources only.

**platform-ui**: `GroupsPage` at `/config/groups` (roles: admin, ABAC `config.users`). List + side drawer with 3 tabs (Info, Members, Owners). i18n namespace `groups` (en + pt-BR). Group↔user association is also editable directly from the user's own form in `Configuration > Access` (section "Group association", Member/Supervisor checkboxes per group) — no cross-reference needed from the Group side for that. Monitor Heatmap filtered by `accessiblePools` only (`supervisedAgentTypes` client-side filter is now always `[]` = unrestricted, degrades gracefully).

→ See [`docs/arcos/arc9-agent-groups.md`](docs/arcos/arc9-agent-groups.md)

---

## Arc 11 — Console como Superfície de Orquestração

O Console é uma **superfície de orquestração**: o operador humano dirige, delega e monitora agentes AI como coparticipantes de primeira classe (AI e humanos simétricos no modelo de sessão). Funcionalidades: cartões de participantes AI em tempo real (step/status do Skill-Flow); "Adicionar Especialista" (invoca pools de `mentionable_pools` via A2A `assist`); "Delegar Tarefa" (seleção de mensagens → drawer instrução+visibilidade → card de resultado no `agent_done`); Tab de Orquestração (steps do Skill-Flow + intervenções de supervisor). **Permissões**: operar = `agent_assist.atender` (o campo é `atender`; `agent_assist.operacao` nunca existiu no catálogo, e a MOD-05 é que lhe deu consumidor); intervir = role `supervisor` + scope ABAC.

→ See [`docs/arcos/arc11-console-orchestration.md`](docs/arcos/arc11-console-orchestration.md)

---

## Arc 6 Fase 2 — Observabilidade por Deploy *(completo)*

Lente `deploy` no board de Agentes (`/reports/agents/compare?lens=deploy`), em **dois modos**
(`&mode=daily|epoch`): diário com marcadores de deploy, e epoch com o eixo X em versões.

**Âncora = POOL, nunca skill.** `skill_id` é estável (o deploy não muda o id; `version` é campo à
parte) e **um skill pode rodar em vários pools** — ancorar no skill misturaria pools numa curva só.
Um deploy compartilhado vira o mesmo marcador em cada curva de pool atingido. A nota vem de
`evaluation_finalized` (fonte Oficial), agrupada por `attr.pool_id`, com `min_sample=30`.

**Leitura honesta é requisito, não estilo:** eixo diário completo, **bolinha só em dia COM
avaliação**, reta entre medições — sem zero e sem interpolação em dia sem amostra, que inventariam
qualidade onde não houve medida. Marcador de deploy traz versão/skill no tooltip; N<min é
sinalizado. Dependências externas (deployments do agent-registry, cobertura da evaluation-api)
degradam graciosamente para lista vazia.

**Limitação registrada:** deploy posterior à última avaliação fica no fim da curva, sem dados
pós-deploy ainda.

→ See [`docs/arcos/arc6-phase2-observability.md`](docs/arcos/arc6-phase2-observability.md),
[`docs/product/arc6-phase2-deploy-observability-spec.md`](docs/product/arc6-phase2-deploy-observability-spec.md).
As-built, endpoints e testes no `CHANGELOG.md`.

---

## Arc 12 — Agent Business Events

MCP tool `agent_event(category, value, tags?)` para agentes publicarem KPIs de negócio durante sessões. `category` hierárquico `pool_id.skill_id.metric_key` (1º segmento = pool_id da sessão, namespace isolation); contexto resolvido do `session_token`; tags bloqueiam PII; rate limit configurável; auditado via `McpInterceptor`. Infra: topic `agent.events` → ClickHouse `analytics.agent_business_events` (`category_l1..l4` pré-decompostos) + endpoints `/reports/agent-events/{series,summary,categories}`. Integra com Arc 6 Fase 2 (`metrics[]=agent_event:{category}`).

→ See [`docs/arcos/arc12-agent-business-events.md`](docs/arcos/arc12-agent-business-events.md)

---

## Audit LGPD — Compliance Role (Fase 1)

Módulo ABAC `audit` para DPO/compliance, **ortogonal às roles** — quem tem `module_config.audit.*` no
JWT tem acesso escalonado. **DOIS campos no catálogo**: `sessions` e `mcp_calls`, em
`GET /v1/audit/…` na analytics-api. Os outros três da Fase 1 (`user_access`, `data_requests`,
`config_snapshot`) são *deferred* (`AUD-01..04`) e **não estão declarados** — campo sem portão vivo é
promessa sem mecanismo; cada um entra com a sua feature.

> **A declaração no catálogo só passou a existir em 2026-09-08 (AUT-41), e esta seção era uma das
> três casas que afirmavam o contrário.** Medido: `infra/modules.yaml` e o `auth.module_registry`
> vivo tinham 11 módulos e `audit` em nenhum dos dois — enquanto o `Sidebar.tsx` gateava `nav.audit`
> por `audit.sessions` e a analytics-api o enforçava. Sob grant-first, o item do DPO era **invisível
> para todos** e **inconcedível pela tela** (o formulário renderiza o catálogo), com **0 portadores**.
> `role_defaults` **ausente por decisão**: ninguém nasce com auditoria — o DPO a recebe por concessão
> explícita, e o mecanismo já garante isso (`build_module_config` pula campo sem preset).
> `domain: [none, read_only]` porque o portão é de leitura; `scopable: false` porque
> `_check_audit_access` não passa `scope_id`, e o ramo 3 do `abac_can` faria um escopo declarado
> *parecer* restringir sem restringir nada.

- **O gate `_check_audit_access` tem CINCO ramos, cada um com o seu código**, e a postura para
  segredo ausente é **503** — oposta à do `pool_auth`, que degrada aberto: lá é escopo de leitura,
  aqui é dado pessoal. A recusa **nomeia quem foi barrado**. Verificador canônico (`plughub_authz`).
- **Nunca `enforce_write` aqui** — ele responde direto, e esta casa precisa GRAVAR antes de responder.
- ⚠️ **Portão que decide dentro de um `Depends` não pode ter efeito colateral no handler.** Foi assim
  que a recusa sem credencial passou meses **fora da trilha** que o banner da tela prometia: o `401`
  subia antes do corpo, e `_record_access` nunca rodava.
- **`audit_access_log` NUNCA é deduplicado**, por design LGPD — o valor da trilha é dizer **quantas
  vezes** um dado foi acessado e por quem.
- **`mcp_audit_log` não existe, e isso é decisão** — zero tráfego medido na borda `invoke`, e criar
  tabela que ninguém preenche é o *"existe ≠ está pronto"*. `/v1/audit/mcp-calls` lê de
  `session_timeline`.

**Deferred:** `original_content` desmascarado · logs `user_access` · pipeline SAR/erasure ·
`config_snapshot` (ver `AUD-01..04` em `pending.md`).

→ See [`docs/arcos/audit-lgpd.md`](docs/arcos/audit-lgpd.md)

---

## Arc 13 — Evaluation Review, Contestation & Calibration

Dois fluxos por tipo de agente avaliado. **Humano**: revisor AI pré-publicação (gate por campanha) → contestação por dimensão → human reviewer decide (`ContestationThread` append-only; `max_rounds` via `ContestationPolicy`). **AI**: `evaluation_finalized` imediato + curadoria amostral por regras configuráveis; revisor AI gera `calibration_signal` → `CalibrationNote` no knowledge namespace → feedback ao avaliador via RAG. **Invariante**: `evaluation_finalized` é a única fonte de truth para relatórios de qualidade. Topic `calibration.events` + `GET /reports/evaluator-calibration` (Calibration Dashboard, correlaciona com deploy epochs do Arc 6 Fase 2).

→ See [`docs/arcos/arc13-review-contestation.md`](docs/arcos/arc13-review-contestation.md)

---

## Métricas de Avaliação & Metodologia ⚠️ design fechado — R1/R5/R6/R7a/R8a–R8e/R9–R12 (R8 completo); R7b/R7c fora de escopo (LGPD); R13a–c/R14/R15a–b/R16 PENDENTE

> **Limitação assumida (2026-06-23):** faithfulness sobre **valor PII de output de ferramenta** não é
> suportada — reter o retorno cru (vault R7b) é anti-minimização LGPD sem requisito consentido. R7a
> mascara+descarta o output (postura alinhada). Reabrir só sob requisito de produto explícito. O cofre
> que compliance exige é o de **mensagens** (`TokenVault`), que já existe.

Define **o que o avaliador mede e como** (distinto de revisão/contestação, Arc 13). Duas trilhas.

**Quantitativo (`session_metric.*`)** — catálogo **fechado**, determinístico, sem LLM, **agnóstico de agente** (humano e IA). É o mesmo namespace que os critérios `auto_computed` do formulário consomem via `computation_source` — `auto_computed` **entra na nota** junto com as qualitativas (não é KPI de dashboard à parte). Decisões: **(A)** computa em escopo contato **e** segmento (avaliador usa o do segmento); **(B)** guarda séries brutas (`agent_response_latencies_s`, `inter_message_gaps_s`) p/ perguntas paramétricas; **(C)** `customer_wait_time_s` ≠ `total_silence_s`; **(D)** ausente/não-aplicável = `na` (re-normaliza peso), condicionável por canal; **(E)** computa **lazy no ingest** (só o % amostrado). Saudação = 1ª msg do agente (proxy, sem detecção semântica).

**Qualitativo de IA** — avaliar IA ≠ humano (erros sistemáticos por versão, não episódicos). Dimensões: faithfulness (vs KB / vs ferramenta), tool correctness, policy adherence, abstenção/escalada, safety. **Dois tiers**: transcript-only (já avaliável) × execution-evidence (lacuna). Metodologia (τ-bench, DeepEval, RAGAS): combinar determinístico + rubrica explícita/calibrada com controles de viés; divergência >20–25% vs humano = recalibrar (o loop de calibração do Arc 13 já é esse mecanismo). **Detecção de divergência (R8)**: Estágio 1 = gatilho sobre `calibration_score` (ancorado); Estágio 2 = **curadoria cega-primeiro** (`%`-gated, SLA — humano re-pontua sem ver a IA → diff por dimensão; pega o viés de KB que diversidade de modelo não pega; nota humana autoritativa no desacordo); **revisor heterogêneo** (modelo ≠ avaliador) recomendado reduz viés de modelo (não de KB). Simetria: contestação (humano) ↔ Estágio 2 proativo (IA, sem ferir "IA nunca contesta").

**Amostragem de contatos** — hoje stateless/determinística por hash, `%` por campanha. Modelo-alvo: **cota por agente cumulativa por déficit** (cobertura justa, não representatividade), chave humano `(campaign, user_id)` / IA `(campaign, pool_id, skill_id, deploy_version)` — chavear por versão = "reset no deploy" sem reset (não por `agent_type`, eixo aposentado). Pré-requisito: **carimbar `skill_id`+`deploy_version`+`channel` no `ContactSegment`** (hoje ausente; deploy resolvido do `SkillDeployment` ativo, ancorado no início — conserta também a precisão do Arc 6 Fase 2 e destrava condicionamento por canal no backfill). Modelo de deploy: `skill_id` estável = identidade do artefato, versão = registro de deploy, `_v{n}` cosmético; binding skill↔pool a unificar (`PoolSkillSlot` autoritativo + append-log). Virada para estado (ADR). **Módulo agnóstico/externo**: viável como **grau-transcript** (sem `mcp.audit`/`pipeline_state`/`usage.events` → tier-2 IA indisponível); exige contrato de ingestão versionado + masking + versão dentro do contato. Arquitetura (fechada): A2 document-ingest (`QualityContact`); fan-out **emitindo eventos canônicos** (reusa consumers, gatilho de sampling grátis); stream durável via **opção Y** (importador = produtor puro; consumer interno reconstrói `session_stream_events` dos eventos — isola o ambiente interno); masking pré-processador externo + net no ingest, `original_content=null`.

**Achados de código** (base do roteiro): `SessionMetricsExtractor`/`fill_auto_computed_criteria` existem mas são **órfãos** (nunca chamados) → `auto_computed` é hoje no-op que distorce pesos; o trace `mcp.audit` **não chega** ao `ReplayContext` → tier-2 inavaliável (dado vive em `mcp_audit_log`, via analytics-api `GET /mcp-calls`; `input/output_snapshot` gated por `AuditPolicy.capture_*`). **R7 (§II.5)**: `output_snapshot` hoje é gravado **cru** (vazamento) — fix = aplicar masking (simétrico ao input) + masked+original; faithfulness-PII via vault deferido; avaliador recebe **campo mínimo transiente** (PII não entra no store de avaliação).

→ See [`docs/arcos/arc-evaluation-metrics-methodology.md`](docs/arcos/arc-evaluation-metrics-methodology.md)

---

## Quality Ingest — leitor de histórico plugável (R13a–R13d) *(arco completo)*

Módulo anti-corrupção que faz históricos **externos** (CCaaS) e a **reavaliação interna** entrarem no
MESMO pipeline de avaliação (sampling → ReplayContext → avaliador → analytics), **sem o importador
tocar a infra interna**.

**Invariantes:**
- a interface é **stream de eventos** (`ingestion_event_v1`, schema em
  `@plughub/schemas/ingestion-event.ts`), nunca lote;
- **pool é a unidade** — eventos carimbam `pool_id`, jamais `campaign_id`;
- o quality-ingest é **produtor puro** (porta 3850): faz masking net-pass, deriva `session_id`/
  `segment_id` determinísticos (idempotência) e mapeia 1:1 para os eventos canônicos que os
  consumers já entendem — não escreve em store interno;
- toda emissão leva **`source: "external_import"`**, nunca `channel_gateway`; é esse carimbo que
  gateia o consumer de reconstrução;
- a reconstrução do stream durável reusa o **mesmo escritor** do Persister vivo, para não haver
  drift entre o caminho importado e o nativo;
- **tier-2 de IA é indisponível para externo** (grau-transcript) — não há `mcp.audit` nem
  `pipeline_state` de origem.

O exportador interno (`quality-export`, porta 3852) é o inverso: lê ClickHouse e re-emite pela mesma
porta do ingest, gerando um `session_id` novo de reavaliação a partir do original.

→ See [`docs/arcos/quality-ingest.md`](docs/arcos/quality-ingest.md)

---

## Arc 15 — Canal WebRTC com SFU (LiveKit) ⚠️ SFU de pé no demo · contato ponta a ponta NÃO

> **SFU + TURN provisionados em 2026-09-14 (VOZ-01).** Até ali o ✅ desta seção cobria só o **canal**:
> nenhum compose tinha LiveKit, o SDK não era dependência, e sem credencial o provider ligava
> `_dev_mode` e devolvia token, sala e egress **placebo** — ninguém ficava vermelho. Hoje o compose
> demo sobe `livekit` e `coturn` (e, desde a VOZ-02, `livekit-sip`), e **sem credencial o provider RECUSA
> nomeando a env** (`WebRTCProviderUnavailable`); o canal fecha a porta antes de autenticar e rotear.
> O SFU real achou código que nunca tinha rodado (`with_ttl(int)`: nenhum token real podia ser
> assinado). Gate: `infra/test/probe_webrtc_media_plane.sh`. **Contato ponta a ponta validado com
> gente no browser em 2026-09-15** (`VOZ-04`; roteiro `docs/guias/roteiro-validacao-webrtc-console.md`).
> ⚠️ **O que ainda NÃO existe:** mídia para browser em OUTRA máquina da rede (o demo serve o próprio
> host — loopback, UDP único e TURN com dois nomes), egress (`VOZ-06`), porta SIP publicada e chamada
> SAINTE (`VOZ-32`/`VOZ-33`). O bot leg (ouvinte + voz, transcrição e coleta por teclado/fala) existe
> desde 2026-09-16 — `arc15-webrtc.md` § 15; a chamada telefônica ENTRANTE, desde 2026-09-18 — § 19.

- **A chamada pelo tronco SIP é canal `voice` e entra na MESMA sala** (VOZ-02): o serviço SIP do SFU
  (`livekit-sip`) põe o chamador numa sala, o SFU avisa o gateway por webhook assinado
  (`/v1/livekit/webhook`) e o gateway a **adota** como a da sessão. **O endereço é o número DISCADO**
  (`ChannelEndpoint` `voice`); **sem endpoint, a chamada é RECUSADA** e o motivo vai ao log — nunca
  pool default. Gate: `infra/test/probe_voz02_sip_inbound.sh`.
- **`room.auto_create` está LIGADO, e isso tem contrapartida obrigatória** (VOZ-02, decisão do dono):
  o serviço SIP entra por join e, com `false`, recebia 486 em toda chamada. A garantia da VOZ-01
  (token para nome qualquer não cria sala) virou REAÇÃO: no `room_started`, o gateway apaga sala
  `plughub-{uuid}` sem `channel:webrtc:{sid}:room_name` — chave que por isso é gravada **ANTES** do
  `create_room` e apagada no fechamento. Desligar o webhook desliga o controle; o
  `probe_webrtc_media_plane.sh` julga os dois juntos (A3) e mede o efeito com controle positivo (D4/D5).

- **Versões do LiveKit andam JUNTAS** — SFU no compose, `livekit-client` do Console (lockfile) e do
  widget (versão exata no CDN). SFU v1.8.4 com clientes 2.20/2.22 publicava áudio e não vídeo, sem
  nada vermelho: os probes usam o SDK Python, que negocia com o servidor antigo. Mudou uma, repita o
  roteiro; o `gate_webrtc_console_live.sh` (L3 trilhas · L5 sinal de áudio) é o instrumento.

- **`GET /webrtc/token/{sid}` exige Bearer + capacidade por papel no pool da sessão**
  (`agent_assist.atender` publica · `contacts.monitorar` assina oculto), e a identidade na sala vem
  do JWT. Emitia sem credencial e com identidade da query — *tokens só do gateway* não diz *para quem*.
  **E capacidade no pool não é atendimento** (VOZ-15, 2026-09-17): como `agent`, o chamador tem de
  estar entre os ATENDENTES do contato (`channel:webrtc:{sid}:media`, instância `human-{sub}`) —
  até aqui outro portador do mesmo grant recebia **200** para a chamada de um cliente alheio.
  Falha FECHADA; sem atendente conhecido responde `room_not_ready` (404, que o Console repete), nunca
  403. **Supervisor fica fora da regra**: assinar oculto sem atender é a função dele.
- **Modelo, língua e voz da fala são CONFIG do tenant, e o que se pode escolher é o que o serviço
  TEM** (VOZ-17): camadas **perfil → tenant (namespace `webrtc`) → env do gateway**, com a
  procedência de cada campo no log da chamada. A escrita passa pelo channel-gateway
  (`/v1/speech-profiles`, `/v1/speech-defaults`, catálogo em `/v1/speech-models`), que confere a
  resolução COMPLETA contra `GET /v1/models` do serviço antes de gravar — modelo ausente, tarefa
  trocada ou voz de outro modelo eram aceitos e viravam 404 por frase, com a fala perdida. **Serviço
  de fala fora ⇒ 503, sem gravar**; o env permanece como última camada, nunca como a única.
- **Mídia é fato do PARTICIPANTE, nunca da sessão** (VOZ-09): teto do cliente = política ∩ UNIÃO do
  que os atendentes consomem, aplicado no SFU e anunciado ao cliente. Não reviver `negotiated_medium`.
- **A política é config do POOL** (VOZ-10): `pool.media_policy` `{customer_publish, agent_publish}`,
  obrigatória em pool de contato com `webrtc` **ou `voice`** (VOZ-02), lida fresca pelo bridge e levada no `routing.assigned`
  com a procedência. **Ausência nunca vira permissão** — pool sem política ou registry fora oferece nada.

Canal `webrtc` browser-to-SFU com medium negociado em tempo real (video→voice→text). Coexiste com `voice` (cliente no telefone: tronco SIP → a mesma sala desde a VOZ-02; Twilio/TwiML é legado); `webrtc` = clientes na webapp. **SFU**: LiveKit self-hosted (gravação por egress, supervisão hidden subscriber, multi-participante). **Invariante**: tokens LiveKit emitidos exclusivamente pelo Channel Gateway, nunca expostos ao browser. STT/TTS reusa os FallbackProviders do voice (transporte = LiveKit PCM frames). Console: `WebRTCOverlay` (vídeo/waveform pelos tetos). Texto é sempre possível; `media_capabilities` do agente não existe mais (sem produtor desde a aposentadoria do AgentType). A ponte PSTN→sala existe desde a VOZ-02 (fatia 1, entrante); o que falta dela está em `VOZ-31..35` no `pending.md`.

→ See [`docs/arcos/arc15-webrtc.md`](docs/arcos/arc15-webrtc.md)

---

## Arc 19 — Modelo Unificado de Sessão: Workflow como Canal Webhook

Elimina a dualidade contact/workflow tratando workflows como canal `webhook` na channel-gateway. Cada skill registrada num pool webhook é um "endpoint" (análogo a DIN de voz ou número WA). O trigger cria uma sessão normal, o routing engine aloca instância skill-flow do pool, e o `session_id` é o identificador persistente por toda a execução — incluindo múltiplos ciclos de suspend/resume.

**Status `suspended`** adicionado ao domain de sessão. No `suspend()`, o agente fecha o segmento e devolve ao pool (`agent_ready`); a sessão persiste com TTL estendido no Redis (EXPIRE calibrado ao `timeout_hours` — substitui PostgreSQL para durabilidade). No resume, nova alocação normal → novo segmento. **Resume_token lookup** via hash Redis `{tenant}:resume_tokens → session_id`.

**Segregação workflow vs. agente**: perfil `workflow` (channel_type: webhook) permite steps `task/choice/catch/escalate/complete/invoke/reason/suspend/collect/receive` — proibidos `menu/notify/begin_transaction/end_transaction`. Perfil `agent` (demais channels) permite `menu/notify/begin_transaction/end_transaction` — proibidos `suspend/collect`. ⚠️ **A frase *“validado em parse do YAML + guard no engine”* era FALSA e caiu em 2026-09-06 (CTR-01): não havia allowlist em lugar nenhum — nem no validador do agent-registry, nem no `executor.ts`, nem no `engine.ts`. Hoje a lista mora em `@plughub/schemas/skill-profile.ts` e recusa no **DEPLOY** (`set-next` + `promote`), porque o perfil é fato do POOL e o publish do skill não sabe onde ele vai rodar. **E a lista perdeu um item por contraprova:** `delegate` era declarado proibido em perfil de agente e é caminho VIVO (`limite_ia` → `dialog_runner`, 186 segmentos) — impô-lo recusaria dois pools em produção. Medido: **0 violações em 31 deploys vivos** com a lista corrigida. Gate: `infra/test/probe_skill_profile_steps.sh`.

**Collect step revisado**: exclusivo de workflows. Cria sessão-filho de contato com channel negociado por capabilities (Arc 16). Workflow suspende; agente channel-aware atende a sessão-filho e retorna resultado. Workflow nunca conhece o canal usado.

**WebhookAdapter** em `channel-gateway/adapters/webhook.py`: `POST /v1/channels/webhook/{skill_id}` (trigger), `POST /v1/channels/webhook/resume/{token}` (resume), `GET /v1/channels/webhook/{session_id}/status`. **Pool webhook**: `channel_types: [webhook]` + `skill_id` como endpoint.

**O que é eliminado**: `workflow-api` lifecycle endpoints, `WorkflowInstance` entidade separada, `skill-flow-worker` Kafka consumer, `workflow.events` topic, entidade Journey ✅ (Fase F concluída 2026-05-28), Monitor/Processes e Analytics/Processes páginas separadas.

**Monitor unificado** (4 abas — período: now/last_hour/last_24h/today): Sessions (channel_type filter, badge suspended, métricas Resolved/Escalated/Failure/Timeout/Cancelled/TMA), Pools (snapshot + tendência; webhook pools mostram capacidade configurada), Agents (humanos/AI; skill-flow instances via Pools), Events (Arc 12 business events, filtro regex de category). **Analytics unificado** (4 abas): Sessions (ANI/DNIS por channel_type; hierarquia sessions→segments→detalhe), Pools (time-series capacity), Agents (consolidado + drill-down segments), Events (time-series Arc 12 + drill-down segments). **duração tem DOIS nomes e eles NÃO são intercambiáveis** (D9): `elapsed_time_ms` (tempo — wall-clock do caso, **inclui** as esperas; webhook = `closed_at − primeiro segmento`) × `agent_time_ms` (agente × tempo — `Σ segments.duration_ms` com `agent_type != 'system' AND role IN ('primary','specialist') AND duration_ms IS NOT NULL`). ⚠️ Este arquivo afirmou por meses *"TMA webhook = `SUM(segment.duration_ms)`"* como se fosse implementação: era **falso** (o código fazia e faz wall-clock, e registrava a soma como refino adiado) e **conceitualmente errado** — a soma não é uma duração: segmentos se SOBREPÕEM (`@mention` é sempre paralelo ao primary e é rotina; especialista de conferência nasce dentro da janela do pai; hooks posatt são paralelos entre si), logo `Σ ≥ wall-clock` com sobreposição e `Σ ≤` com lacunas. **Nunca somar segmentos para obter tempo de sessão, e nunca comparar as duas.** Tempo suspenso tem lugar próprio: `analytics.session_transitions` (D4).

**6 fases**: A ✅ (WebhookAdapter + channel type), B ✅ (status suspended + TTL Redis), C ✅ (orchestrator-bridge: skill-flow como agente nativo), D ✅ (workflow-api deprecation), E ✅ (Monitor/Analytics unificados), F ✅ (Journey entity elimination — 2026-05-28). **Arc 19 completo.**

→ See [`docs/arcos/arc19-unified-session-model.md`](docs/arcos/arc19-unified-session-model.md)

---

## Dialog Primitive — Scripted-Dialog Runner (survey + OTP)

Primitivo de "interação scriptada delegada" compartilhado por survey e OTP. **DialogForm**
(`@plughub/schemas/dialog.ts`) é script **linear** de nodes `statement` (→ notify) e `question`
(→ menu), versionado. Store canônico **`dialog-api`** (porta 3760); a tool MCP **`form_get`** resolve
o publicado num bloco `render`. Provisionamento `infra/dialog/*.json`, **seed-if-absent**.

- **Quatro costuras inegociáveis:** conteúdo (JSON) × controle (skill) × canal (runner) × **segredo**
  (`OtpService`). O código do OTP nunca passa pela mão de um agente ou runner.
- **Sem `next` condicional — branching é do skill**, senão o JSON vira linguagem.
- **DOIS veículos, divisão mecânica:** `delegate()` para quem PODE suspender; hook de
  `on_contact_end` consome **INLINE**, porque delegar suspende o hook e o bridge fecha o contato
  antes de renderizar. ⚠️ **Delegate era de nível único, e deixou de ser em 2026-09-07
  (CTR-06).** O token do chamador é fato da ARESTA e vivia numa tag ÚNICA da sessão
  (`core.workflow.delegate_resume_token`), então `A → B → C` fazia a delegação de dentro
  sobrescrever o token de quem chamou — e A ficava pendurado até o `timeout_hours`, com o
  cliente vendo o especialista atender e **nada ficando vermelho**. Hoje o engine CAPTURA
  o token no nascimento do pipeline (isolado por segmento, `{sid}--seg--{iso}`) e o
  RESTAURA na retomada; a tag volta a significar o que promete, e os 8 skills que a leem
  não mudaram. Gate: `infra/test/probe_caller_token_chain.sh`.
- **`form` é um TIPO DE BLOCO, não um valor de `interaction`** — bloco é PROJEÇÃO sobre o `nodes[]`
  plano. A **dimensão VENCE** o form, e **campo NÃO é pergunta**.
- **O editor JSON é escape hatch e o VEREDICTO é do SERVIDOR** (`POST /api/dialog/preview`, mesma
  `buildRender`/`validateDialogForm` do `form_get`). Verificador fora do ar ⇒ *"não verificado"*,
  nunca verde. **Aplicar não grava.**
- **Invariante de build:** mexer no `MenuStepSchema` obriga a rebuildar `agent-registry`,
  `skill-flow-service` e `mcp-server` juntos, senão o registry rejeita o ref com 422.

→ See [`docs/product/dialog-primitive-and-runner-design.md`](docs/product/dialog-primitive-and-runner-design.md),
[`docs/adr/adr-otp-workflow-and-dialog-primitive.md`](docs/adr/adr-otp-workflow-and-dialog-primitive.md)

---

## Scheduler / Agenda — `scheduler-api`

Serviço na porta 3650. Uma **Agenda** é recurso **domain-agnostic** que, num *quando/modo* (1x ou
recorrente daily/weekly/monthly, `times[]` no dia), **aciona um POOL via webhook** — nunca um skill
(invariante S4).

- **O scheduler não reimplementa o "quando"** — `business_day_policy` consulta o **calendar-api**,
  autoridade única. Recorrência calcula só a **próxima** ocorrência e re-arma no disparo.
- **Status da agenda = "acionou o pool ou não"**; a execução é da SESSÃO. O ledger guarda
  `session_id` para drill-through e **nunca espelha** o estado dela. `dispatched` significa que a
  gateway criou a sessão — admissão e capacidade aparecem no ciclo da sessão, não aqui.
- **Sem retry no v1:** `failed` é gravado e aparece no Monitor.
- **Promote agendado** é um pool webhook que faz `invoke pool_promote`, wrapper auditado do ÚNICO
  caminho de promote. Não-2xx (409 `next` vazio, 422 capacidade) vira `isError` → `on_failure`:
  **promoção nenhuma acontece em silêncio.** Endereça pool, nunca skill/versão, e **sem pin**.
- **ABAC `scheduler.{configurar,operacao}` é grant-first e agora tem CONSUMIDOR NO BACKEND**
  (SCH-01, 2026-09-17). Até aqui as 9 rotas de `/v1/agendas` decidiam com o header `X-Tenant-ID`
  e **mais nada** — o portão existia só na UI, e o proxy dela repassa o prefixo sem credencial:
  quem alcançasse a porta criava agenda, trocava o alvo e disparava com `POST /fire`. Como
  **Agenda aciona POOL** (inclusive os que promovem deploy e contatam cliente), disparar é EFEITO,
  não leitura. Hoje: criar/editar/apagar pede `configurar` em escrita · disparar/pausar/retomar/
  cancelar pede `operacao` em escrita · listar e ler o ledger pedem `operacao` em leitura. **O
  tenant é o do TOKEN**; o header só decide na porta de SERVIÇO (`X-Service-Token`, aditiva, para
  o job `agenda-seed`). Gate: `probe_route_credential_coverage.sh` § C.
  > ⚠️ **A frase anterior — *"sem role default nem bypass de admin"* — era FALSA na metade do
  > role default**, e caiu na mesma medição: `infra/modules.yaml` declara `role_defaults` de
  > `read_write` para **admin e supervisor** nos dois campos, e o estado vivo confirma (o token do
  > admin e o de um supervisor recém-criado carregam os dois grants). Não havia bypass de admin
  > — isso continua verdade —, mas *"ninguém nasce com"* nunca foi verdade aqui. **Escopo por
  > pool continua fora**: os dois campos são `scopable: false`, então quem opera agendas opera
  > todas; dívida NOMEADA em `SCH-02`.

→ See [`docs/product/scheduler-agenda-spec.md`](docs/product/scheduler-agenda-spec.md),
[`docs/adr/adr-timer-scheduler.md`](docs/adr/adr-timer-scheduler.md)

---

## Outbound — Mailing + Campaign + Delivery

Substrato **genérico** de contato ativo: `mailing` (audiência) + `campaign` (orquestrador fino, que
endereça **POOL** — invariante S4) + `campaign_delivery` (estado por-campanha). Store canônico
**`mailing-api`** (porta 3660, schema PG `outbound`). **Survey é o 1º consumidor, não o dono.**

- **Metadado da entrada é OPACO** — contrato produtor↔consumidor; a plataforma não o interpreta.
- **Membership (`mailing_entries`) ≠ suppression (`campaign_deliveries`)** — não fundir. Entrada é
  **`(pessoa, contexto)`**, nunca só pessoa.
- **Agentes drenam via MCP e nunca tocam o DB** (`mailing_add` · `campaign_drain` ·
  `campaign_delivery_result`: wrappers finos, `isError` em não-2xx, auditados).
- **Pacing é a agenda recorrente**, não um laço no skill (tick drena ≤ `batch_size`). Idempotência:
  `UNIQUE(campaign_id, mailing_entry_id)` + `FOR UPDATE SKIP LOCKED` no claim.
- **`contact_eligibility_check` é motor ÚNICO e agnóstico**, com precedência inegociável: `opt_out`
  (salvo `campaign.transactional`) → janela de calendário → fadiga. `claim=true` grava o fato na
  MESMA transação — a janela começa no envio, não na decisão — e `reason` **sempre nomeia a regra**.
  Falha de dependência degrada para **ALLOW barulhento**, nunca silencioso.
- ⚠️ **No survey outbound o veículo é o link web (`survey_link_create`), NUNCA o `collect`** — o
  collect chavearia o sinal pela raiz da sessão CHAMADORA (a do dispatcher, no fan-out). Por isso o
  `origin_session_id` viaja EXPLÍCITO na metadata.

→ See [`docs/arcos/outbound.md`](docs/arcos/outbound.md),
[`docs/product/outbound-mailing-campaign-design.md`](docs/product/outbound-mailing-campaign-design.md)

---

## Pending — o ledger, não esta seção

> **Esta seção foi ESVAZIADA em 2026-09-05 (DOC-01), e não deve renascer.** Ela nasceu antes do ledger
> `pending.md`/`done.md` (2026-08-31) e, desde então, era uma segunda casa afirmando o que está aberto
> — exatamente o defeito que o ledger existe para fechar. Duas casas para o mesmo fato não têm dois
> valores: têm o da casa que ninguém confere. Já custou uma vez, aqui: seis itens de Customer History
> listados como abertos por **seis semanas** depois de fechados, com o `TODO.md` dizendo o certo o
> tempo todo — e a casa errada era a que o índice lia.
>
> **A lista de trabalho aberto é [`pending.md`](pending.md); o índice do que fechou é
> [`done.md`](done.md).** As regras vivem na skill `task-ledger` (e em resumo na § *Onde a documentação mora*); quem as impõe é
> `infra/test/probe_task_ledger.sh`, não a boa vontade de quem edita. Raciocínio e medição por assunto
> ficam no `TODO.md`; o porquê de cada entrega, no `CHANGELOG.md`.
>
> **Nada foi perdido na mudança, e isso foi CONFERIDO item a item antes de remover.** Dos itens que
> esta seção carregava, sete já estavam no ledger (`VOZ-01/02` · `JRN-03` · `PUL-01/02` · `APR-01` ·
> `SUR-01..06` · `IDN-01..05`); os demais **não estavam** e foram escritos lá no mesmo commit, sob
> grupo próprio: `USG-01` · `PRC-01` · `AUD-01..04` · `QIN-01/02` · `QSI-01` · `RRH-01` ·
> `OUT-01..04` · `CCH-01/02`, mais `JRN-04` (sinal N3 no drill), `AUT-37` (guard de rota ABAC em
> `analise/*`, sob a demanda de ABAC e não sob a da Journey) e `APR-09` (o ingress de resume aplica
> `approvals.decide` a qualquer resume com JWT).
