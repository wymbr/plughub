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
> **A regra vale para as FERRAMENTAS, nao so para os fontes** *(emenda medida em 2026-08-28)*.
> A copia Windows continua intocada — o que estava misturado era a toolchain: o diretorio e o
> do WSL, mas os binarios que o operam sao de Windows (`git 2.47.1.windows.1`, Python com
> `os.linesep == '\r\n'`). Dois danos, ambos silenciosos ate serem fatais:
> **(1)** `core.autocrlf=true` vem do gitconfig de SISTEMA do Git for Windows, e **um `.sh` com
> CRLF nao roda sob WSL** — falha com `syntax error`, *depois* de ter rodado no Git Bash, que
> tolera CRLF; **(2)** o git de Windows **nao enxerga o bit `+x`** neste mount (medido: 33
> mudancas `755→644` pendentes, nenhuma no sentido inverso — e o `ls` da MESMA sessao mostra
> `-rwxr-xr-x`, ou seja, `ls` e `git` discordam).
>
> **Duas metades, e so uma viaja no commit.** `.gitattributes` e conteudo e o git le sozinho —
> mecanismo. `core.fileMode` e `safe.directory` sao config **por clone**, e nenhum arquivo as
> carrega: vivem em **`scripts/bootstrap-clone.sh`** (rodar apos `git clone`; o
> `scripts/linux/setup.sh` delega a ele). Isso e promessa, nao mecanismo, e esta declarado como
> tal no cabecalho do script.
>
> **A decisao do `fileMode` e ASSIMETRICA, e a versao "mede e aplica" esta errada** — o mesmo
> clone mede `100755` de dentro do WSL e `100644` pelo `\wsl.localhost`, entao uma execucao so
> observa o proprio lado. `false` vence sempre; **nunca se volta de `false` para `true`
> automaticamente**, porque quem roda nao sabe se outro lado toca o clone. Mesma forma do
> `resolve_scope`: o restritivo vence, porque o permissivo degrada mudo.
>
> Ao escrever arquivo com ferramenta Windows, **`newline=""` em Python** — modo texto grava CRLF.
> O `.gitattributes` conserta no commit, mas o `.sh` ja quebrou antes disso.

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
> Quando ultrapassar, aplicar as regras abaixo.
>
> **Por que 800 caiu, e é aritmética, não gosto.** O arquivo estava em 1 883; as três maiores seções
> somam 567 linhas (*Saúde* · *Security* · *Postura*), então **apagar as três inteiras daria 1 316** —
> ainda 116 acima de 800. Um alvo abaixo do piso que as próprias regras protegem é promessa que o
> arquivo não pode cumprir, e a próxima sessão que o lesse tentaria cortar o catálogo para alcançá-lo.
> As outras 51 seções somavam 1 175 linhas, média de **23** — já no formato de resumo que a tabela
> abaixo pede. O 800 foi fixado antes de *Postura* e *Security* crescerem para carregar o catálogo
> medido; ele descrevia um arquivo que não existe mais.
>
> **1 750 nasce cumprido, com folga estreita de propósito:** os dois movimentos que o alcançaram
> (índice de docs → `docs/INDEX.md`; cinco seções de arco apertadas ao formato) esgotaram o que havia
> a mover. A folga é o orçamento de crescimento — seção de arco nova que passe de 20 linhas o estoura,
> que é exatamente o que ele deve cobrar.

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
havia um portal de documentação — duas casas indexando o mesmo acervo. A convenção de qual pasta
recebe o quê continua abaixo, porque é regra de onde ESCREVER, não catálogo do que existe.

Os quatro arquivos da raiz: **`CLAUDE.md`** (arquitetura viva, regras, invariantes, resumos) ·
**`pending.md`** (trabalho ABERTO, por demanda) · **`done.md`** (índice do que fechou) ·
**`TODO.md`** (raciocínio e medição por assunto) · **`CHANGELOG.md`** (o porquê de cada entrega).

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

- **`ReplacingMergeTree` substitui a LINHA INTEIRA — não faz merge por coluna.** Todo writer de
  `sessions` ou manda a linha completa, ou é reidratado antes da escrita (cache de identidade no
  consumer + carimbo no close, que é a linha sobrevivente). Três bugs de `sessions` num dia só vieram
  disto. Vale para qualquer tabela RMT nova. **Regra derivada (2026-08-18): versão de RMT é fato do
  EVENTO, nunca da inserção, e precisa da RESOLUÇÃO do fenômeno.** `segments` e
  `participation_intervals` foram migradas para `ReplacingMergeTree(row_version)` com
  `coalesce(<fim>, <início>)` em `DateTime64(3)`, como `sessions` e `session_transitions` — as duas
  versões anteriores (`ingested_at` em segundo; e nenhuma coluna) perdiam o fechamento de segmento.
  **Resíduo que a migração NÃO cobre:** `participation_intervals` continua
  `ORDER BY (tenant, session, participant)`, então dois segmentos do mesmo participante na mesma sessão
  (caso do resume) colidem numa linha só — ela **não** serve de testemunha por-segmento, e agora vence
  o de evento mais recente em vez do último inserido. Use `segments`.

- **Ordem no Kafka é por PARTIÇÃO — logo publish sem `key` não tem ordem nenhuma.** Qualquer par de
  eventos que descreva o MESMO objeto (abre/fecha, cria/atualiza) tem de viajar com chave que os
  coloque na mesma partição; sem ela o particionador espalha e o segundo evento pode ser consumido
  antes do primeiro. Custou o defeito mais caro deste repositório até hoje: `conversations.participants`
  publicava sem chave em tópico de 3 partições, o `participant_joined` vencia o `participant_left` na
  dedup, e o segmento ficava aberto **para sempre, sem erro em lugar nenhum** — cinco rodadas de
  investigação em três hipóteses erradas (transporte, controle, GC de task). Pior: o DDL de
  `participation_intervals` **afirmava em prosa** a garantia que ninguém impunha (*"the 'left' event is
  always inserted after 'joined' (Kafka ordering)"*). Comentário que promete invariante sem produtor é
  a mesma família de "valor plausível". Ver `CHANGELOG.md` 2026-08-18 e
  `docs/guias/conference-mechanics.md` § Problema 34.

- **Identidade DERIVADA tem de conter o discriminador do FENÔMENO, não o do contêiner dele.** Id
  determinístico (`uuid5`) é a forma correta de tornar emissão repetida inócua — mas só se a chave
  descrever a coisa que se quer contar. `queue_wait_segment_id` era `uuid5(tenant, session_id)`:
  identificava a SESSÃO, enquanto o fato registrado é a PASSAGEM pela fila. Medido em 2026-08-24 num
  contato real — espera de 24 118 ms num pool, transferência, espera de 85 009 ms noutro, **duas
  emissões, uma linha**, e a primeira espera **deixou de existir** (o `ReplacingMergeTree` não funde,
  substitui). Não é defeito de exibição: o carimbo da passagem perdida é apagado na saída, logo
  nenhuma migração a alcança depois. **Escolha do discriminador é escolha de escopo**: o
  `first_queued_ms` serviu porque seu ciclo de vida (NX na entrada, DELETE na saída) *já significa* uma
  passagem; o `pool_id` foi recusado porque é fato do CALL SITE (o emissor passa `event.pool_id or ""`)
  e daria dois ids para uma passagem. **Agravante que é a lição de método:** a premissa falsa
  (*"uma sessão tem UMA passagem pela fila"*) vivia no **docstring da própria função** — comentário que
  promete invariante sem mecanismo que a imponha, exatamente como o DDL de `participation_intervals`.
  Ver `CHANGELOG.md` 2026-08-24 e `conference-mechanics.md` § Mudança 38.

- **Um instrumento pode ser falseável, ramificado e honesto — e ainda medir a proposição ERRADA.**
  Não é o teste que não pode reprovar (essa família já está abaixo); é o teste que reprova
  corretamente **uma pergunta adjacente à que se fez**. Medido em 2026-08-24 na D14.1: o probe do
  aging inerte tinha três ramos (`VIVO`/`LATENTE`/`INCONCLUSIVO`) e testemunha de presença ao lado,
  e mesmo assim não sabia responder o que importava — porque *"contato esperou neste pool"* e *"a
  espera foi longa o bastante para o alvo importar"* são **dois fatos**, e só o segundo é dano. A
  medição saiu `VIVO` (16 de 63 esperas em pools de alvo absurdo) enquanto o dano era **zero** (as
  esperas ali são de 5 a 14 segundos, e quem espera 8 s não precisa de aging). Um relatório fiel ao
  ramo teria publicado um defeito que não existe. **Ao desenhar o veredicto, pergunte de qual
  PROPOSIÇÃO cada ramo é evidência** — e quando a pergunta tem a forma *"isto machuca?"*, exposição
  e dano são grandezas separadas, que precisam de dois números, nunca de um ramo só. *(Irmão de
  `exposicao-latente-e-hipotese`, na direção inversa: lá faltou contar quem sofre antes de declarar
  inócuo; aqui contou-se quem foi exposto e chamou-se de sofrimento.)*

- **Quando a spec e o código discordam, desconfie dos DOIS.** O merge lia um `started_at` que metade dos
  canais não escrevia; a resposta certa não foi fazer o timestamp funcionar, foi ver que a aciclicidade
  **nunca deveria depender de relógio** (união de componentes disjuntas). O teste não verifica só a
  implementação — ele descobre que a especificação pedia a coisa errada. Corrigir a spec é resultado
  válido, não desvio.

- **Um teste que não pode reprovar é pior que teste nenhum — ele compra confiança sem dar nada.**
  O modo de falha é sempre o mesmo: a asserção nunca alcança a condição que deveria julgar, e o
  resultado (verde, ou `skipped`) parece resposta. Catálogo do que já aconteceu: `skipped` por ler
  `REDIS_URL` quando o serviço define `PLUGHUB_REDIS_URL` (9 testes do claim pull, **nunca** rodaram
  no container) · `MagicMock` devolvendo truthy para `analytics_open_access` (14 testes de RBAC
  trocaram de caminho) · `set -e` + `VAR=$(curl …)` matando o script sem imprimir quando o serviço
  ainda sobe · `jq '.campo // empty'` tratando `false` como ausente · janela por `started_at`
  cobrando dado gravado antes do deploy (o corte certo é `ingested_at`). **Antes de aceitar um
  verde, pergunte o que o faria ficar vermelho** — e prefira que o teste se declare INCONCLUSIVO a
  passar por ausência de amostra.

  > **Corolário medido em 2026-09-07 (VOZ-03) — um mock não verifica que o alvo existe; ele o
  > CRIA.** `voice.py` chamava **seis** métodos que não existiam em lugar nenhum do MRO, e a
  > suíte os mockava sob o comentário *"Mock inherited base methods"*: o comentário afirmava
  > herança e a atribuição da linha seguinte tornava a afirmação verdadeira **dentro do teste**.
  > O teste prova a CHAMADA e esconde a AUSÊNCIA. No produto o `AttributeError` caía num
  > `except Exception` largo que o reportava como fim NORMAL do laço, em `debug` — o canal de voz
  > nunca publicou nada e nada ficou vermelho. Regra: **ao mockar um método do próprio objeto sob
  > teste, asserte `hasattr` antes**; e o censo que vale é AST sobre a população (`hasattr`
  > responde por uma classe de cada vez e exige a imagem de pé). Gate:
  > `infra/test/probe_adapter_self_calls.sh`.
  >
  > **E o irmão de CONTRATO, do mesmo dia:** `sms.py` publicava `payload["answers"]` e o bridge
  > lê `payload["result"]` — ninguém lia `answers`, e o teste do SMS afirmava `answers`. Produtor
  > e teste olhando um para o outro, **nenhum dos dois para o consumidor**. Um contrato de payload
  > não mora em nenhum dos lados: mora ENTRE eles, e por isso nenhum `grep` num arquivo o alcança.
  > O gate que o fecha (`probe_menu_result_contract.sh`) **mede a chave no LEITOR** — escrevê-la
  > como constante mediria a concordância dos produtores com o gate, e trocar a chave no bridge
  > deixaria os quatro verdes contra um leitor que mudou.

  > **Corolário de assincronia, medido em 2026-08-30 — esperar por CONTAGEM DE YIELDS é
  > adivinhar a estrutura interna da corrotina.** `await asyncio.sleep(0)` depois de um
  > `ensure_future` não espera a task: espera **um** turno do loop. Medido no emissor de tokens,
  > `sources()` só enche a partir de **2** yields e os dois eventos a partir de **5** — dois
  > testes vermelhos com o produto CERTO, e a leitura óbvia (*"a emissão não acontece"*)
  > apontando para uma regressão inexistente. Pior, o gêmeo deles **passava por acidente**: o
  > caminho dele tinha um `await` a mais DEPOIS do agendamento, e era ele que dava os turnos —
  > mesmo produto, veredictos opostos, decididos por uma linha alheia à proposição. **Espere
  > pelas TASKS**, e mantenha o conjunto delas no PRODUTO, não no teste: um helper que use
  > `asyncio.all_tasks` varre também as tasks de quem chamou. Aqui o conjunto já precisava
  > existir por outra razão — `ensure_future` sem guardar o retorno deixa o loop como único dono
  > e o CPython avisa que a task pode ser coletada no meio da execução; num produtor de CUSTO
  > isso é fail-silent com a evidência na FATURA. Ver `CHANGELOG.md` 2026-08-30.

- **Um ambiente que só sobe porque já subiu antes não está sendo verificado — está sendo lembrado.**
  Estado herdado (volume, imagem, linha de DB, coluna criada por `db push`) é entrada não declarada
  do boot: enquanto ele existir, o aplicador pode estar quebrado sem que nada fique vermelho. Três
  defeitos ANTIGOS caíram juntos no primeiro `down -v` (2026-08-05): `migrations` do agent-registry
  atrás do `schema.prisma` (o `db push` aplicava o schema direto, então batia sempre); a ordem do
  DDL do ClickHouse (a MV já existia de instalações passadas); e o `eval-seed` sem credencial desde
  o G-PROBE fase 2 (o `GET` achava o formulário e retornava **antes** de exercer o gate, saindo 0).
  Nenhum foi causado pelo wipe — o wipe foi o instrumento. **Instalação limpa é um teste, e teste
  que nunca roda não é cobertura**: rode `infra/scripts/rebuild-all.sh --wipe` de propósito e em dia
  calmo, não no dia em que você precisa da stack de pé. Corolário para diagnóstico: quando um
  serviço falha logo após um wipe, a hipótese ordenada não é "o wipe quebrou", é "o wipe revelou".

  > **Corolário medido em 2026-08-30 — pergunte à IMAGEM, nunca ao container.** O `TODO.md`
  > dizia que **quatro** Dockerfiles Python não instalavam pytest. Medido: **nenhuma das 14
  > imagens** tinha, e os quatro containers em que a suíte "rodava" tinham o pytest instalado
  > **à mão** — estado que um `up -d` apaga. `docker exec` no container e `docker run` sobre a
  > imagem respondem perguntas DIFERENTES, e só a segunda é reprodutível. Quando o defeito é
  > *"isto só funciona aqui"*, o instrumento tem de ser o artefato, não a instância dele. Com o
  > pytest na imagem apareceram **15 falhas reais em 3 serviços** — nenhum deles o que o TODO
  > apontava, e 12 eram testes que ficaram **para trás de um portão de autorização** (a sexta
  > ocorrência do padrão da § Security). Gate: `infra/test/probe_python_suites.sh`, que separa
  > **declaração** (o Dockerfile pede `.[dev]`) de **imagem** (o pytest está lá) de **execução**
  > — a primeira sem a segunda é promessa sem mecanismo; a segunda sem a primeira fica verde por
  > container herdado.
  >
  > ⚠️ **E o runner óbvio nasce permanentemente vermelho.** Rodar `pytest` da raiz do monorepo
  > (`cd /app`) troca o **rootdir**, e com ele o `[tool.pytest.ini_options]` de cada pacote
  > (`asyncio_mode = "auto"`) deixa de ser lido: **476 falsos vermelhos** contra 15 reais. Um
  > gate assim ensina todo mundo a ignorá-lo, que é pior que gate nenhum. Rode no WORKDIR do
  > pacote. O que denunciou foi comparar com uma medição anterior do MESMO serviço
  > (`channel-gateway`: 699/0 antes, 594/187 depois, **mesmo código**) — um número sozinho não
  > diz de qual proposição ele é evidência.
  >
  > **Corolário medido em 2026-09-04 (GAT-01) — "rodou tudo o que a lista cita" e "a lista cita
  > tudo" são DOIS fatos, e só o primeiro tinha mecanismo.** O `run_gates.sh` executava fielmente
  > o `gates.manifest`; o manifesto declarava **44 de 281** scripts de `infra/test/`. Rodando os
  > 144 não declarados com cara de gate, **100 saíram VERDES** — cobertura que já funcionava e
  > ninguém colhia. É a mesma família do teste que não pode reprovar, uma casa acima: *uma lista
  > parece completa por ser uma lista*, e o que falta não aparece em contagem nenhuma. Hoje o
  > manifesto presta contas de TODO `.sh` em quatro classes (AUTO · `!`assistido · `=`isento com
  > motivo · `?`não-triado NOMEADO), e `probe_gates_manifest_coverage.sh` reprova script que não
  > caia em nenhuma. ⚠️ **A população é "tudo" por medição, não por zelo**: um critério textual
  > para *"quem precisa ser declarado"* foi refutado duas vezes, nas duas por falso NEGATIVO
  > (`exit "$FAIL"` fora do padrão; os 35 `test_*` que julgam com `✅`/`❌`) — e critério que
  > decide quem é COBRADO pode esconder arquivo, que é justamente o defeito a fechar. O critério
  > sobrevive rebaixado a INFORMAÇÃO, ordenando a fila de triagem.

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
  pool_id` faz o alias sombrear a coluna que o `WHERE` usa, e a query inteira falha
  (`ILLEGAL_AGGREGATION`, code 184) — não a coluna, a query. Já aconteceu duas vezes: `any(attr.agent_type)`
  na lente `deploy` e `any(pool_id)`/`any(user_id)` no `wrapup-summary`. Sufixe o alias (`_ref`) e renomeie
  na camada Python, onde o contrato da API é definido. O modo de falha agrava a regra: o wrapper devolve
  `data_unavailable` com `data: []`, indistinguível de "não há dado" para quem só olha a tela — só se
  diagnostica se o `except` logar o texto da exceção.

- **`docker cp` sobrevive a `restart`, não a `up -d`.** `up -d` recria o container a partir da imagem.
  Mudança em código de serviço = `build`, nunca `cp` (que é só atalho de iteração efêmera). Um `up -d`
  no meio de uma validação faz o serviço voltar à imagem antiga e os testes "regridem" sem motivo.

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
  incondicional levava `x-skill-publish:true`, que grava `{flow, flow_draft:DbNull}` — todo boot sobrescrevia
  produção **e apagava o rascunho do editor**. Consequência operacional que morde: **editar o YAML de um skill
  já semeado é no-op** — reiniciar o bridge não publica nada (ele só loga o DRIFT). Para o arquivo valer:
  `PUT /v1/skills/:id` com `x-skill-publish:true`, ou `REGISTRY_SYNC_RECONCILE=true`. E, se o pool usa slot,
  publicar ainda **não basta** — o bridge executa o snapshot do slot `current` (`set-next`→`promote`).
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
    py-tasks/                    ← plughub-tasks — dono e alarme de task efêmera de asyncio: `create_task` solto perde a exceção e pode ser coletado em execução
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
| py-tasks | Python | Python 3.11+ | lib, sem serviço — só `asyncio`+`logging`; seis consumidores (gateway, routing, rules, ai-gw, evaluation) |
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

**RegistrySyncer** runs before Bootstrap: upserts pools+agent_types from `infra/registry/*.yaml`; prunes stale (`REGISTRY_SYNC_PRUNE=true`). Skill sync: PUTs `skill-flow-engine/skills/*.yaml` before pools (slug `^skill_[a-z0-9_]+$`, **publica produção via `x-skill-publish:true`** — Skill Versioning Fase B). Instance IDs: `{agent_type_id}-{n+1:03d}`. Human agents NOT managed by Bootstrap. Seed no longer writes Redis keys.

**Execução = produção, não a edição (Skill Versioning Fase B/P1):** o bridge executa o **snapshot do slot `current` do POOL** (`get_pool_current_flow`, cache por pool, invalidado no `registry.changed(pool)` do promote/rollback), com **fallback** para `skill.flow` (pools não migrados). O editor (`PUT /v1/skills`) escreve **`skill.flow_draft`** (rascunho) — **não vaza para produção**; só o deploy (set-next→promote, ou `x-skill-publish`) preenche o que roda.

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
- ⚠️ **MEDIDO FALSO em 2026-09-01, aguardando decisão (MEN-01)** — *"Never allow AI agents to
  emit `@mention` commands — only `role: primary` or `role: human`"*. As duas metades da frase
  **não são a mesma coisa**: `primary` é POSIÇÃO na sessão, não espécie do participante, e a IA
  que conduz a conversa É a `primary` (medido: 1144 segmentos `native/primary` + 100
  `ai/primary` × 333 `human/primary`). O gate de `message_send` implementa a segunda metade e
  por isso **deixa passar a população que a primeira nomeia**. Nada foi mudado no código ainda
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

## Message Masking, @mention & Masked Input

Token format in stream: `[{category}:{token_id}:{display_partial}]` (e.g. `[cpf:tk_b7d2:***-00]`). Stream stores `content` (masked) + `original_content` (unmasked). Default `authorized_roles: ["evaluator", "reviewer"]`. Domain MCP tools resolve tokens via `McpInterceptor.resolveToken` callback. Channel Gateway strips to `display_partial` only before WS delivery.

**@mention**: ⚠️ *o gate testa `role ∈ {primary, human}` — e isso **não** exclui agentes de IA,
que são `primary`; ver MEN-01, medido falso em 2026-09-01. A aplicação é ainda ASSIMÉTRICA: o
caminho WS do Console (`server.ts:3638`) não checa papel nenhum, por desenho declarado.* Domain closed by `mentionable_pools` pool config. `mention_commands` YAML declares actions: `set_context`, `trigger_step`, `terminate_self`.

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

Upload (2-stage): WS `upload.request` → `upload.ready` (file_id, upload_url) → HTTP POST binary → `upload.committed` → WS `msg.image/document/video`. MIME allowlist: JPEG/PNG/WebP/GIF (16MB), PDF (100MB), MP4/WebM (512MB). Expiry: soft-delete hourly, physical delete daily (+24h grace). JWT via message body, never URL. `jwt_secret` per tenant via Redis `{tenant_id}:config:webchat:jwt_secret`.

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

**Skill Deploy Lifecycle**: `deploy_status` (draft/published) + `skill_deployments` table. `PUT /v1/skills` always sets `deploy_status=draft` on new skills, NEVER modifies it on updates. `POST /v1/skills/:id/deploy` — only action that sets published.

**Agent Assist UI** at `/agent-assist`: 4-tab right panel (Estado, Capacidades, Contexto, Histórico). Substitution mode for menu cards. Visibility array routing for NPS/wrap-up agents. Optimistic echo for button selections.

→ See [`docs/arcos/platform-ui.md`](docs/arcos/platform-ui.md)

---

## Arc 7 — Auth, RBAC + ABAC, Performance Routing

**auth-api** (porta 3200): users + sessions no schema PG `auth`. JWT HS256 TTL 1h; refresh token opaco
de 43 chars com rotação e SHA-256 no store. `accessible_pools[]` no JWT filtra LINHAS na analytics-api.
**ABAC** (`module_config` no JWT, `auth.module_registry` semeado de `infra/modules.yaml`): **11 módulos**
*(medido 2026-09-08; dizia `8` e o catálogo tinha 12 — a contagem em prosa não tem mecanismo, então
envelhece calada. `workflows` saiu na MOD-11)*,
cada campo com `access: none|read_only|write_only|read_write` + `scope[]`;
`PermissionChecker.can(module, field, minAccess?, scopeId?)`. **Roteamento por performance**:
`performance_score = resolution_rate × (1 − escalation_rate)`, blending por `performance_score_weight`
(default 0.0), Redis `{tenant}:agent_perf:{agent_type_id}` (TTL 6h), batch a cada 5 min.

- **ADMINISTRAR uma pessoa nunca é o mesmo campo que CONCEDER capacidade a ela.** `config.users` =
  pessoa (criar, editar, ativar, grupos); `config.permissions` = capacidade (papéis, módulos, escopo).
  O portão tem **QUATRO portas** — rota, corpo, alvo e escopo — e fechar só a primeira é decorativo.
  O discriminador do corpo é `model_fields_set`, não o valor: enviar o campo é conceder.
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

**Skill Deploy** (Phase 2): `POST /v1/skills/:id/deploy` → `skill_deployments` table → `publishRegistryChanged`. Scheduled deploy via `skill_scheduled_deploy_v1` workflow YAML. `GET /v1/skills/:id/handoff-status` for safe deploys.

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

## Arc 15 — Canal WebRTC com SFU (LiveKit) ⚠️ código · SFU NÃO PROVISIONADO

> **Corrigido 2026-08-20 por medição.** O ✅ desta seção cobria o **canal**, e foi lido por meses como
> se cobrisse a solução de mídia. Medido: **não há serviço LiveKit em compose nenhum** (`grep livekit
> **/*.yml` → zero), **nenhuma env `LIVEKIT_*`/`WEBRTC_*`** em `.env*`/compose/scripts, e o SDK **não é
> dependência** do pacote (`packages/channel-gateway/pyproject.toml:6-23`) — logo a imagem construída
> não o tem e os imports caem no ramo de degradação (`webrtc_room_client.py:217-220`,
> `webrtc_provider.py:183-184`). Com `api_key`/`api_secret` vazios (`config.py:228-232`) o provider liga
> `_dev_mode` (`webrtc_provider.py:167`) e devolve token, room e egress **placebo**. O plano de
> SINALIZAÇÃO existe e roda (WS `main.py:729`, `GET /webrtc/token/{session_id}` `main.py:754`, cliente
> real no platform-ui `package.json:16`); o plano de **MÍDIA** não está de pé em ambiente algum do
> repositório. O `arc15-webrtc.md:81-89` prescreve topologia Kubernetes (livekit-server, egress, redis,
> coturn) e **não há manifesto correspondente** em `infra/` — ou seja, o doc nunca prometeu o SFU no
> compose; foi o ✅ do cabeçalho que passou a valer por ele. É a família *"'existe' ≠ 'está pronto'"*,
> agravada por `_dev_mode` ser exatamente um **valor plausível**: devolve token bem-formado e ninguém
> fica vermelho. **Antes de qualquer trabalho de WebRTC, provisionar o SFU é pré-requisito, não detalhe
> de deploy.**

Canal `webrtc` browser-to-SFU com medium negociado em tempo real (video→voice→text). Coexiste com `voice` (PSTN/Twilio = tronco externo); `webrtc` = clientes na webapp. **SFU**: LiveKit self-hosted (gravação por egress, supervisão hidden subscriber, multi-participante). **Invariante**: tokens LiveKit emitidos exclusivamente pelo Channel Gateway, nunca expostos ao browser. STT/TTS reusa os FallbackProviders do voice (transporte = LiveKit PCM frames). Console: `WebRTCOverlay` (vídeo/waveform por medium). `media_capabilities: [video,voice,text]` no agente; text = fallback universal. *Futuro*: bridge PSTN→WebRTC via LiveKit SIP Ingress (`VOZ-02` em `pending.md`, bloqueado por `VOZ-01`).

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
- **ABAC `scheduler.{configurar,operacao}` é grant-first**, sem role default nem bypass de admin.

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
> [`done.md`](done.md).** As regras vivem em § *Ledger de tarefas* acima; quem as impõe é
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
