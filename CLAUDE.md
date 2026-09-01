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

> **Target: ≤ 800 linhas.** Quando ultrapassar, aplicar as regras abaixo.

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
| Pending genuíno | máx 50 linhas — apenas itens não implementados |

### O que NÃO pertence ao CLAUDE.md

| Proibido | Vai para |
|----------|----------|
| Itens marcados com ✅ | `CHANGELOG.md` |
| Histórico de implementação (task #N, testes X/Y, build N kB) | `CHANGELOG.md` |
| Documentação completa de um Arc ou módulo (> 50 linhas) | `docs/arcos/{arc}.md` |
| Snippets de código longos (> 10 linhas) fora de invariantes | `docs/arcos/{arc}.md` |
| Detalhes de UI (props, componentes, hooks por feature) | `docs/arcos/{arc}.md` |
| "Pendente (fase 2)" que já foi implementado | Deletar |

### Estrutura de arquivos de referência

```
plughub/
  CLAUDE.md          ← arquitetura viva, regras, invariantes, resumos (≤ 800 linhas)
  pending.md         ← trabalho ABERTO, agrupado por demanda (ADR/spec). É a lista de tarefas
  done.md            ← ÍNDICE do que fechou, com os MESMOS grupos. Nunca narrativa
  TODO.md            ← raciocínio e medição por assunto. NÃO é lista de tarefas (medido: 78% é prosa)
  CHANGELOG.md       ← histórico de implementações concluídas, com o porquê
  docs/
    modulos/                  ← docs de páginas/features da UI (uma por rota)
    arcos/                    ← docs de implementação por Arc (detalhe técnico)
      arc4-workflow.md        ← Arc 4 completo (workflow, calendar, collect, webhooks)
      delegate-workflow-io.md ← Padrão delegate: workflow delega I/O a agente via suspend/resume
      arc5-segments.md        ← Arc 5 ContactSegment analytics
      arc6-evaluation.md      ← Arc 6 Evaluation platform completo
      arc-evaluation-metrics-methodology.md ← métricas de avaliação (session_metric.*) + dimensões qualitativas IA + metodologia + roteiro
      arc7-auth.md            ← Arc 7 Auth + ABAC completo
      arc8-agent-availability.md ← Arc 8 disponibilidade e pausas
      arc9-agent-groups.md    ← Arc 9 Agent Groups + Supervisor Scope
      arc10-journey.md        ← Arc 10 Journey multi-session
      instance-bootstrap.md   ← reconciliação, RegistrySyncer, hot-reload
      platform-ui.md          ← Frontend Architecture + Agent Assist UI
      ai-gateway.md           ← AI Gateway multi-account, copilot, stateless
      usage-metering.md       ← metering por dimensão, Redis, quota
      pricing.md              ← faturamento por capacidade, billing API
      session-replayer.md     ← Session Replayer, Hydrator, ReplayContext
      session-conference-lifecycle.md ← modelo de 3 camadas, gaps conhecidos
      dashboard.md            ← Dashboard #35, DisplayTool registry, catalog
      queue-attended-model.md ← fila sempre atendida: admissão híbrida, outage, role queue, relatório Fila/SLA, max_wait (A–E ✅)
      pools-infra-report.md   ← relatório Pools/Infra: volume, fila, capacidade, SLA
      customer-surveys.md     ← spec/ADR módulo de pesquisas de satisfação (CSAT/NPS/CES/PMF/FCR)
      customer-contact-history.md ← histórico de contatos do cliente (lista/transcrição/busca) — transversal
    guias/
      context-store.md        ← ContextStore, @ctx.*, segment-scoped
      masked-input.md         ← Masked Input, begin_transaction
      mention-protocol.md     ← @mention protocol
      pool-hooks.md           ← Pool lifecycle hooks
      orchestrator-working-memory.md ← Working memory pattern para orquestradores em loop
      conference-mechanics.md ← Mecanismo de conferência: Redis keys, eventos, posatt, teardown
      session-meta-ownership.md ← `session:{id}:meta`: partição de propriedade (porta × bridge),
                                  helper `session_meta_merge` (3 modos, EVAL único), regra do MAIOR
                                  TTL (-1/-2 DEFINEM). Fatia A ✅; B (recusar campo alheio) e C
                                  (`entry_pool_id` × `pool_id`) abertas
      abac-permission-system.md ← ABAC: guia de implementação (módulos, campos, access levels)
      context-store-taxonomy.md ← ContextStore: taxonomia de namespaces e controle de visibilidade
      context-masking-rules.md ← ContextStore: mascaramento dinâmico por variável × role
      timeouts-e-deteccao-de-falhas.md ← timeouts e detecção de falhas por camada
      gitagent.md             ← GitAgent: ciclo de vida completo (repo Git como fonte de verdade do agente)
      conferencia-agente-ia-mapeamento.md ← ⚠️ OBSOLETO por auto-declaração: mapeava gaps de conferência multi-agente/@mention já implementados. Indexado para não ser redescoberto como pendente
      changelog-2026-04-{15,16,16b,29}.md ← fragmentos datados de changelog (histórico; o canônico é `CHANGELOG.md`)
    adr/
      adr-message-masking.md  ← masking architecture decision
      adr-contextstore-allowlist.md  ← ContextStore como ALLOWLIST: **TIPO é a declaração única** (formato × máscara-por-papel × classe LGPD), MAPA em `escopo.dominio.campo`, legado vira `alias` contado e datado. Pré-requisito: a omissão deixa de ser MUDA antes da inversão. Fases V0→V5 + D6–D9 — **V0–V3, D6–D8 e a FATIA 1 da D9 entregues; a V4 (inverter o default) é a próxima e NÃO é reversível** — Aceito, parcialmente implementado
      adr-masked-typed-declaration.md ← `masked` deixa de ser BOOLEANO e passa a nomear um TIPO do catálogo (`true` = `opaque`, não a ausência de tipo); o tipo decide EXIBIÇÃO e CLASSE, **nunca PERSISTÊNCIA**; detecção fica fora. Fases T0–T7 — **T1–T6 e T7-A entregues; T7-B (tolerância do runtime) BLOQUEADA** — proposto
      adr-mcp-interception-single-border.md ← borda única de interceptação MCP: veredicto no mcp-server (3 bordas → 1), proxy externo vira mapeador de vocabulário, `McpInterceptor` fica como caminho de portabilidade; requisito T = domain server inalcançável a partir do agente (borda é rede, não código); fases M0(medir)/B1(pool c/ health-check)/B2(mcpCall nativo)/B3(assimetrias)/T — proposto
      adr-webchat-channel.md  ← webchat channel architecture
      adr-session-replayer.md ← session replayer architecture
      adr-contact-segments.md ← Arc 5 architecture
      adr-instance-bootstrap.md
      adr-evaluation-sampling.md ← amostragem: cota por agente (virada para estado) + carimbo de versão
      adr-quality-substrate-isolation.md ← isolamento do substrato de avaliação por `origin` (híbrido; implementado ✅)
      adr-survey-form-scoring-composition.md ← composição de nota em survey (dimension+perguntas ponderadas; primitivo `scoring.ts` compartilhado c/ Quality) — proposto
      adr-dialog-conditional-skip-logic.md ← skip-logic em DialogForm: guarda declarativa `ask_when`, **não** control-flow — guarda LOAD-BEARING (ceder reconstrói o editor de fluxo dentro do editor de formulário). Avaliador canônico `evaluateAskWhen`, hoje **triplicado** — **Aceito + implementado 2026-07-08**; 1 das 3 decisões segue aberta
      adr-dialog-form-deletion.md    ← `DELETE` de DialogForm é **arquivar** (reversível), não apagar — separa ARMAZENAMENTO de LEITURA; o catálogo fecha mas `GET /{form_id}` continua servindo; purga real só do nunca-publicado — **Aceito + implementado 2026-08-28**
      adr-deploy-time-content-snapshot.md ← conteúdo referenciado (DialogForm) resolvido no **PROMOTE** e congelado no snapshot do slot, nunca em runtime; promote OTIMISTA com `409`+diff. **A S1 original foi REFUTADA por medição; decisão do dono = pin de versão gravado pelo SERVIDOR** — proposto
      adr-skill-flow-editor-validation.md ← validação no editor de skill-flow: **AFORDÂNCIA ≠ VEREDICTO** — `zod-to-json-schema` não representa refinements, então JSON Schema serve para autocomplete e o veredicto vem do SERVIDOR (dry-run `POST /v1/skills/validate`, mesmo `validateSkillPayload()` do `PUT`). Fases F0–F4; F1 antes de F2 — proposto
      adr-dialog-tree-options.md     ← opções em ÁRVORE no DialogForm: a recursão entra em `DialogOption`, **nunca em `DialogNode`** — taxonomia é DOMÍNIO DE VALOR, não control-flow, então `nodes` segue plano e as seis superfícies mantêm o laço linear. Fases F0–F6; F0 antes de F5, F2 antes de F4 — proposto
      adr-outbound-survey-as-collect-contact.md ← survey web outbound = contato via `collect` (canal survey/web), membro N1 da journey; sinal solto vira legado/anônimo (Journey J4c) — proposto
      adr-customer-360-two-surfaces.md ← Cliente 360 (Console 4 abas × Analytics): Contexto/Histórico(jornadas em aberto)/Cliente(cadastro manual+360 quality/survey)/Ações; jornadas = filtro `customer_id` no `/reports/journeys`; cadastro v1 reusa Resolvedor Fase A/B (merge=Fase C) — proposto
      adr-human-approval-workflow-step.md ← Aprovação humana = passo de workflow (collect/delegate a pool, dispatch_mode config); conteúdo=DialogForm (reuso), aprovador=agente logado (Modo A), Console/inbox responsivo, retorno→choice; omnichannel adiado (canal-agnóstico); fases A1–A6 — proposto (fechado)
      adr-wrapup-detached-pull.md    ← Camada E2: wrap-up humano destacado = item de pull `assigned_to`. **Path α, renderer-first** — o renderer é o tratamento genérico de collect-form no Console, servindo aprovação+wrap-up+survey **sem skill por caso** — proposto
      adr-work-item-requeue-and-agent-affinity.md ← devolução de item à fila, posse e afinidade (D1–D8): posse é registro durável do **ÁRBITRO**, não do ledger `work_task`; resume terminal-uma-vez com `SET NX`. **Arco A–F completo + F2 (o Console lê o 409)** — implementado
      adr-historico-unificado-duas-visoes.md ← `/analise/sessions` + `/analise/processos` colapsam num módulo (contatos × processo): **processo é PIVÔ, nunca navegação livre**; segmento é a FOLHA. **F0–F4 entregues (as duas visões na tela); resta a F5** — proposto
      adr-a2a-server-binding.md      ← PlugHub como **servidor** A2A: binding de borda sobre pool+sessão (`Task`=sessão), sem motor nem contêiner novo; AgentCard = PROJEÇÃO do agent-registry; A2A é binding, não `channel` ⇒ zero diff no routing. Fases A0–A6 — proposto
      adr-cti-gateway-multi-driver.md ← telefonia legada como canal: `cti-gateway` on-prem com N drivers sobre **perfil reduzido de CSTA**; o PABX é o ÂNCORA e o CTI é o EFETUADOR, nunca o árbitro; capability por driver, recusa alto, nunca emulação muda. A fronteira é **modo CTI × modo SIP**, que não são fases um do outro. Fases F0–F2 — proposto
      adr-voice-media-plane.md       ← arco de VOZ PRÓPRIA (modo SIP): terminação SIP + SFU + STT/TTS + perna do agente + gravação, **independente de PABX**; o plano de mídia acompanha o deploy da plataforma e `_dev_mode` SAI (sem credencial o provider RECUSA). Fases V-F0→V-F5 — proposto
      adr-relatorios-duas-superficies-e-lentes.md ← relatórios colapsam em DUAS superfícies (Contatos=demanda × Recursos=oferta) com nível × lente × modo; a mesa de comparação é MODO, não página; lente vira DECLARAÇÃO (`aggregation`/`emptiness`/`comparability`). **ARCO COMPLETO — F0–F4 + T0–T3** — implementado
      adr-agent-licensing-and-pool-isolation.md ← licenciamento de agentes e isolamento entre pools (D9 partição por pool, D10 licenças materializadas; D6 revogada) — proposto
      adr-pool-capacity-reserved-shared.md ← capacidade de IA por pool: `reserved` × `shared`, no provisionamento e na admissão de pools `agent_kind: ai` — proposto
      adr-pool-no-resource-policy.md ← desfecho do roteamento quando o pool não tem recurso: enfileirar ou recusar — proposto
      adr-ai-gateway-separation.md   ← separação do AI Gateway entre carga OPERACIONAL e AVALIAÇÃO (perfil `evaluation` isolado) — Aceito, implementado
      adr-identity-channel-possession.md ← plataforma é autoridade de POSSE DE CANAL (OTP), nunca de identidade-de-registro; `verification_class` (`claimed` × `possessed`) e `otp_verify` como única via para `possessed` — Aceito, implementado
      adr-internal-work-queue-author-bound.md ← fila interna por pool: trabalho **author-bound** não é trabalho pooled — Aceito; I1–I4 e o núcleo da I5 implementados, relatório de pendências em aberto
      adr-webhook-endpoint-single-registry.md ← webhook com registro ÚNICO de endpoint e identificador opaco — Aceito, arco A–F completo; remoção do legado e auth saem como arcos próprios
      adr-survey-response-store.md   ← store operacional por-resposta de survey: schema PG dedicado × estender a dialog-api — Aceito, pré-implementação (gate antes de codar o S8)
```

### Como adicionar uma nova feature

1. **Feature pequena** (< 20 linhas): inline na seção H2 existente mais próxima.
2. **Feature média** (20–50 linhas): subseção `###` dentro da seção H2 mais próxima.
3. **Feature grande** (> 50 linhas): criar `docs/arcos/{nome}.md`; adicionar resumo de 15–20 linhas aqui.
4. **Fase pendente concluída**: mover do `## Pending` para `CHANGELOG.md`; atualizar `TODO.md`; **nunca deixar ✅ aqui**.

### Regra de persistência de planejamento

| Tipo de decisão | Onde registrar imediatamente |
|---|---|
| Nova tarefa planejada | Linha em `pending.md`, **sob o grupo da demanda** |
| Decisão técnica (> 3 linhas) | Entrada em `TODO.md` com raciocínio |
| Invariante ou regra arquitetural | Seção neste arquivo |
| Implementação concluída | `CHANGELOG.md` (o porquê) **+** linha em `done.md` (o índice) |

### Ledger de tarefas — `pending.md` / `done.md`

> **Nasceu em 2026-08-31.** O `TODO.md` acumulou 127 seções e nenhuma citava um ADR no título; a
> vinculação entre tarefa, demanda e histórico só existia em prosa. Medido no mesmo dia: **nove
> marcadores desatualizados**, e em todos o **corpo estava certo e o título velho** — porque quem
> lista pendências lê título. O tracker que esta tabela mandava usar (`TaskCreate`) **nunca recebeu
> uma linha**: destino sem mecanismo não se cumpre, e é por isso que a regra abaixo vem com portão.

1. **Toda tarefa nasce sob um grupo**, e o grupo titula um documento que existe (ADR, spec ou arco).
   Sem documento, vai para o balde **`sem-demanda`**, que é **contado** — se cresce, está entrando
   trabalho sem decisão por trás.
2. **Todo id é `AAA-NN`, único através dos DOIS arquivos.** É a chave de junção que não existia.
3. **Título nunca afirma status** — status é coluna. Isso remove a possibilidade do defeito em vez
   de exigir vigilância, que já falhou nove vezes.
4. **Três estados abertos:** `aberto` · `bloqueado` (impedimento nomeado) · `adiado` (decidido não
   agora, com **gatilho** declarado). `adiado` existe para que decisão tomada não volte à mesa.
5. **`done.md` é índice, nunca narrativa** — id, tarefa, data e âncora no `CHANGELOG.md`. O porquê
   mora lá; repetir aqui criaria mais uma casa afirmando o mesmo fato.
6. **Fechar é MOVER**, e a mudança é conferida: nenhum id nos dois arquivos, nenhum id sumido.

Portão: **`infra/test/probe_task_ledger.sh`** (6 ramos; A/B/C/E/F provados falseáveis por mutação).
Ele existe porque o modo de falha do desenho de dois arquivos — **tarefa perdida na mudança** — é
mais silencioso que o status velho que ele substitui.

### Convenção de pastas de documentação

| Pasta | Conteúdo | Quando criar arquivo aqui |
|---|---|---|
| `docs/modulos/` | Docs de páginas e features da UI | Nova rota/módulo de interface |
| `docs/arcos/` | Docs de implementação por Arc | Arc novo ou refactoring de backend significativo |
| `docs/guias/` | Padrões transversais a múltiplos pacotes | Novo padrão (mascaramento, @mention, hooks, etc.) |
| `docs/adr/` | Decisões arquiteturais com trade-offs | Toda decisão estrutural relevante |
| `docs/pacotes/` | Contratos públicos de cada pacote | Novo pacote no monorepo |

### Regra de atualização de documentação

> Toda entrada em `CHANGELOG.md` deve ter um doc correspondente **criado ou atualizado** antes de ser considerada concluída. Se a feature afeta uma rota de UI → atualizar `docs/modulos/`. Se é um Arc ou backend significativo → atualizar ou criar `docs/arcos/`. Se é um padrão transversal → atualizar `docs/guias/`.

> **Conference mechanics**: qualquer mudança no mecanismo de conferência (lifecycle, Redis keys, eventos Kafka/pub-sub, lógica de posatt, filtros no mcp-server, regras de teardown no platform-ui) **deve atualizar `docs/guias/conference-mechanics.md` e adicionar uma entrada em § Histórico de Problemas e Correções** antes de ser considerada concluída.

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
> (`analytics-api`, `config-api`, `orchestrator-bridge`, `dialog-api`, `mailing-api`,
> `session-replayer`) são citados dezenas de vezes no resto deste arquivo — a estrutura mentia por
> OMISSÃO, que é o "valor plausível" da § Postura de Engenharia na forma mais barata: uma lista
> parece completa por ser uma lista.

```
plughub/
  CLAUDE.md                      ← this file
  plughub_spec_v1.docx           ← full architectural specification
  packages/
    schemas/                     ← @plughub/schemas — Zod contracts
    py-authz/                    ← plughub-authz — verificador CANÔNICO de JWT+ABAC (Python)
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

Redis hash `{tenantId}:ctx:{sessionId}`. `ContextEntry`: `{value, confidence 0-1, source, visibility, updated_at}`. Tag namespaces: `caller.*` (customer data), `session.*` (session state), `account.*` (account data), `segment.{segId}.*` (per-agent isolated). Confidence: ≥0.9 confirmed; ≥0.7 high certainty; 0.4-0.7 uncertain; <0.4 unknown.

`@ctx.*` resolves in step inputs, choice conditions (`exists`/`confidence_gte`/`eq`/etc.), and visibility arrays. `@segment.*` prefixed with `segment.{segId}.` isolates parallel agents. `context_tags` on reason/invoke/notify: `inputs` (pre-call) + `outputs` (post-call, fire-and-forget, confidence + merge strategy). Sentiment emitter writes **`session.sentimento.current` apenas** (score, confidence 0.80, TTL 4h) — `…categoria` NÃO é escrita: classificar usa faixas configuráveis por tenant e é feito na LEITURA, pelo consumidor (ver § Sentiment Tracking; classificador canônico em `analytics-api/sessions.py`). *Corrigido 2026-08-02: o emitter chamava um `_classify` já removido, fora do `try`, e o `NameError` matava as DUAS escritas — o `copilot_emitter`, que lia `categoria`, degradava sem log.*

**Step `resolve`**: 5-phase inline accumulation (gap check → CRM → LLM question → BLPOP → LLM extract). **agente_contexto_ia_v1**: 0 LLM when CRM resolves; max 2 when collecting. **Copilot**: fire-and-forget analysis per client message → `session.copilot.*` tags. `supervisor_state` returns `context_snapshot` from ContextStore.

**Pool Context Enrichment** (Routing Engine): after every successful allocation, `_write_pool_context()` writes `session.pool.id`, `session.pool.channels`, and (when set) `session.pool.mentionable_pools` to ContextStore (source: `routing_engine`, confidence: 1.0, visibility: `agents_only`, TTL 24h NX). Reads from routing engine's own Redis cache — no extra I/O. `PoolConfig.mentionable_pools: dict[str, str]` populated from `pool.registered` events.

→ See [`docs/guias/context-store.md`](docs/guias/context-store.md)

---

## Channel vs Medium

- **channel** = specific channel (`whatsapp`, `webchat`, `voice`, `email`, `sms`, `instagram`, `telegram`, `webrtc`) — **hard filter** for routing, mandatory match
- **medium** = base type (`voice`, `video`, `message`, `email`) — **score factor**, fine-tuning only

## Canonical Stream

`session:{id}:stream` is the single source of truth for all session events. **All XADD calls MUST go through `writeStreamEntry()`** in `lib/write-stream-entry.ts` — never call `redis.xadd()` directly. Sole exception: `session_opened`/`session_closed` in Core `server.ts`. Guarantees: `event_id` always present, `segment_id` always flat, `author_id`/`author_role` flat fields, Zod validation before write.

Messages carry `content` (masked) and `original_content` (unmasked, authorized roles only for LGPD audit).

## Sentiment Tracking

> ✅ **A plataforma MEDE sentimento, e isso está provado de ponta a ponta (2026-08-24).**
> Duas metades, dois gates: `probe_sentiment_producer.sh` (contrato → analisador → três emissores,
> com **testemunha negativa**: chamada sem `customer_utterance` não pode escrever nada) e
> `gate_sentiment_engine_half.sh` (contato REAL: referência resolvida pelo engine → skill-flow-service
> → gateway → ctx + `sentiment_live`). Medição de referência: score `-0.50`, pool `sac_ia`.
>
> ⚠️ **O bloqueio de credencial que dominava esta seção CAIU** — a chave do demo foi reposta e o
> `/v1/health` responde 200/`ok`. O diagnóstico de 08-22 (*"124 `status_401`, todo step `reason` de
> todo skill caindo no `on_failure`"*) está **encerrado**, mas a causa dele merece registro: o
> `docker-compose.demo.yml` não tinha `env_file`, então o `.env.demo` **nunca era lido** e a chave
> vinha exportada da shell de quem subiu a stack. Estado de shell não é entrada declarada.
>
> **Contrato de 2026-08-23 — a plataforma passou a MEDIR.** O diagnóstico anterior desta
> seção descrevia o defeito e apontava `/inference` como o caminho a resgatar. A medição refutou a
> premissa: `/inference` isola a fala, mas entrega a `extract_context_from_response`
> (`context.py:53-64`), que é **contagem de palavras-chave em português** — e a rota não tem chamador
> algum. Os dois caminhos pareciam medir e nenhum media (`/v1/reason` lia `sentiment_score` do
> `output_schema`, que nenhum skill declara ⇒ sempre `0.0`).
>
> Desenho vigente, em três peças: **(1)** `ReasonStepSchema.customer_utterance` — referência
> (`$.` / `@ctx.`, **nunca literal**) ao texto do cliente, resolvida pelo engine e enviada nomeada em
> `ReasonRequest`; nomear é declarar ENTRADA, não pedir que o modelo dê a própria nota. **(2)**
> `sentiment_analyzer.py` — chamada dedicada (haiku) fora do turno, alimentando os três emissores que
> já existiam. **(3)** `sentiment_score: float | None`, onde **`None` = não medido** e o pipeline é
> pulado; publicar `0.0` faria toda sessão parecer medida-e-neutra. `tenant_id` passou a viajar nos
> dois chamadores (`engine-runner.ts`, `skill-flow-service`), injetado onde o tenant é conhecido — sem
> ele as chaves nasciam sem prefixo. O analisador **recusa** tenant vazio.
>
> **Declarado (2026-08-24):** `agente_fila_v1.responder_cliente` traz
> `customer_utterance: "$.pipeline_state.ultima_mensagem"` — **o único** step `reason` sobre fala de
> cliente no repositório (`skill_atendimento_sac_v1`, apesar da descrição *"via LLM"*, é todo
> menu/choice/notify). Enquanto for o único, sentimento só existe para contato que passou pela FILA.
>
> **Três defeitos que esta trilha revelou, e que não são de sentimento** (detalhe no `CHANGELOG.md`
> de 2026-08-24) — todos da família *valor plausível*, cada um mascarado pelo anterior:
> · a medição **nunca rodara**: o provider era buscado em `inference_engine.providers`, atributo
>   inexistente (é `_providers`), e o `getattr(..., {})` fazia defeito de fiação sair pela porta de
>   "ambiente sem chave". Hoje: `app.state.llm_providers` + `main.sentiment_provider()`, que separa
>   os dois motivos;
> · **ordem dos emissores**: Kafka vinha antes das escritas locais e `producer.send` BLOQUEIA (não
>   levanta) com broker inalcançável — o score ficava ilegível por 40 s. Hoje: Redis primeiro, Kafka
>   por último sob `wait_for` de 5 s;
> · `session:{id}:meta` é **String (JSON)**, e o ai-gateway a lia com `HGET` em duas cópias ⇒
>   `WRONGTYPE` ⇒ toda medição de contato real agregada sob `unknown`. Hoje: helper único
>   `sentiment_emitter.resolve_session_pool_id`, com quatro ramos de saída nomeados.
>
> ✅ **MEDIR não é EXIBIR — a leitura foi consertada (2026-08-25), e o achado mudou o alvo.**
> A passagem apontava `tools/supervisor.ts:118`; medindo, o cálculo tinha **duas implementações
> independentes e idênticas**, e a que desenha a tela é a OUTRA — o endpoint HTTP
> `GET /api/supervisor_state/:sessionId` (`server.ts`), que a Console consome. Consertar só a tool
> teria deixado a barra dizendo "Neutral" com o commit no lugar. Hoje as duas chamam
> **`lib/session-sentiment.ts`**, fonte única.
>
> **Fonte canônica = ContextStore** (`{tenant}:ctx:{sid}` → `session.sentimento.current`), e isso é
> medição, não gosto: todo caminho que produz score passa por `update_partial_params` →
> `write_context_store_sentiment`, inclusive o auto-reporte do `output_schema`. O ctx é
> **superconjunto estrito** de `partial_params`; ler as duas fontes seria redundante *e* perderia dado.
> Lê-se sempre o hash **CRU**, nunca o `contextSnapshot` já filtrado por `applyContextMaskingDynamic`
> — o filtro é por namespace de operador, configurável POR POOL, e um pool que estreitasse a lista
> apagaria o sentimento em silêncio.
>
> **`current: null` = NÃO MEDIDO**, e nenhuma superfície renderiza sem valor. O `?? 0` convertia
> ausência num ponto legítimo da escala; pior, **desarmava a guarda que a UI já tinha** (`ActionBar`
> só renderiza com valor não-nulo) antes que ela pudesse agir. *Um default no produtor derruba a
> guarda do consumidor sem deixar rastro.* Idem `trend`, cujo default era `"stable"` — invenção da
> mesma família.
>
> **Quatro superfícies, três graus de proteção** — inventário que a passagem não tinha: `ActionBar` e
> `ContactList` guardavam por `!== null` (desarmadas); `ChatArea` inventara `!== 0`, que protegia por
> acidente **e escondia um `0.0` medido de verdade**; `EstadoTab` não tinha guarda nenhuma e
> anunciava "0% neutral" em toda sessão. *(O `packages/agent-assist-ui/` renderizava a mesma tela e caiu no mesmo conserto — app legado,
> **APOSENTADO em 2026-08-27**; a porta 5173 hoje serve só os ativos estáticos de `infra/demo/web/`.)*
>
> Gate: `infra/test/gate_console_sentiment_source.sh` (re-executável, sem contato real; testemunha
> negativa = ctx presente com outra tag e sentimento ausente ⇒ tem de vir `null`, nunca `0`).
>
> ⚠️ **Sem histórico**: o ctx guarda só o valor corrente. `trajectory` é `[]` e `trend` é `null` —
> `consolidated_turns` não serve de substituto (o `float(… or 0.0)` já achatou lá dentro, tornando um
> `0.0` medido indistinguível de turno sem medição). O array `session:{id}:sentiment` documentado
> abaixo **não tem produtor**; enquanto não tiver, gráfico e seta ficam ausentes em vez de fabricados.
>
> ⚠️ **Dívida nomeada:** o `pool_id` do meta é o pool de **ENTRADA**, não o que atende — sentimento
> medido pelo agente de fila agrega sob o pool onde o contato começou. É a fatia C de
> `session:{id}:meta` (`entry_pool_id` × `pool_id`), ver `docs/guias/session-meta-ownership.md`.
>
> Gates: `infra/test/probe_sentiment_producer.sh` (metade gateway) +
> `infra/test/gate_sentiment_engine_half.sh` (metade engine, reprodução manual com contato que
> ENFILEIRE). Detalhe em [`docs/arcos/ai-gateway.md`](docs/arcos/ai-gateway.md) § Medição de
> sentimento.
>
> **A recusa deixou de ser invisível (2026-08-23).** O `/v1/health` do ai-gateway decidia
> `anthropic: "ok"` pela PRESENÇA da string da chave — nada contatava o provedor —, então as 124
> recusas conviveram com verde no `docker ps`. Agora o estado é medido: desfecho gravado no funil
> único de erro + sonda de boot, `credentials` por conta, e **503 quando a chave está configurada e
> é recusada** (ausente ≠ recusada: só a segunda reprova). `unknown` nunca vira `ok` e `rate_limit`
> nunca vira `invalid`. Gate `infra/test/probe_llm_credential_health.sh`; detalhe em
> [`docs/arcos/ai-gateway.md`](docs/arcos/ai-gateway.md) § Health de credencial.

Score-only array in Redis during session. Labels calculated at read time using tenant-configurable ranges. Persisted to PostgreSQL (`sentiment_timeline JSONB`) on session close. Never published to canonical stream.

```
session:{id}:sentiment → [{ score: 0.40, timestamp: "..." }, ...]
TTL: same as session TTL
Ranges: [ 0.3, 1.0] → satisfied | [-0.3, 0.3] → neutral | [-0.6,-0.3] → frustrated | [-1.0,-0.6] → angry
```

> ⚠️ **`session:{id}:sentiment` NÃO TEM PRODUTOR** (medido 2026-08-25: nenhum componente escreve a
> chave). É promessa sem produtor — a mesma família de `participation_intervals`, cujo DDL *afirmava
> em prosa* a ordenação que ninguém impunha. O emitter grava três destinos e nenhum é este:
> `{tenant}:ctx:{sid}` (valor corrente, sobrescrito), `{tenant}:pool:{p}:sentiment_live` (agregado por
> pool) e o tópico `sentiment.updated`. Consequência viva: **não existe histórico por sessão**, logo
> trajetória e tendência são ausentes por decisão, não fabricadas. Ver `TODO.md`.

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
- Never send tool list to LLM without applying `permissions[]` filter from JWT
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
- **Never store a narrower-scope fact in a wider-scope field — derive it where the scope is known.** Quatro aplicações vivas: (a) **identidade de participante** é fato de escopo no ContextStore — fato de contato → `session.*`, fato de segmento → `segment.{segId}.*` (ex.: qual humano um hook de wrap-up serve → `segment.{segId}.served_human_participant_id`); nunca num campo de sessão lido por vários componentes (colapsa em multi-humano). (b) **Identidade e membership de instância** são fato de **(recurso, pool)** — derivadas do pool em escopo (`human_agent_{pool}`), nunca congeladas no registro global do recurso; capacidade (`max_concurrent`, semáforo de vagas) é do RECURSO e não fragmenta por pool. (c) **Evento de liveness (heartbeat) nunca carrega identidade nem membership, e nunca cria instância** — só prova que o recurso está vivo; criação é do login. (d) **"Papel" são DOIS fatos, não um** — *propósito do agente* (`agent_role`: `executor`/`orchestrator`/`evaluator`) é fato do ARTEFATO (skill), estável, declarado no registry, e é entrada de AUTORIZAÇÃO (lido do registry pelo `agent_login`, nunca do input do agente); *papel de participação* (`primary`/`specialist`/`supervisor`) é fato de **(participante, sessão)** e NÃO cabe no hash da instância — a mesma instância atende `max_concurrent_sessions` sessões e é `primary` numa e `specialist` noutra ao mesmo tempo. Ler os dois do mesmo campo foi o que deixou o gate de `evaluation_context_get` sem produtor e, por isso, falhando ABERTO sobre `original_content` desmascarado. See [`docs/adr/adr-participant-identity-single-source.md`](docs/adr/adr-participant-identity-single-source.md), [`docs/adr/adr-human-agent-pool-scoped-identity.md`](docs/adr/adr-human-agent-pool-scoped-identity.md)
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

Routing Engine writes pool snapshot to Redis: `{tenant_id}:pool:{pool_id}:snapshot`
(**TTL 3600s**) — `{ pool_id, available, busy, busy_elsewhere, untagged, paused_capacity,
total_instances, queue_length, sla_target_ms, channel_types, model, updated_at }`.

**A PAUSA é fato da ARITMÉTICA, não só do roteamento** (2026-08-21). O recompute lê `status` da
instância (`_INACTIVE_STATES` = `paused|logged_out|logout|draining`, **fonte única** — o trecho Lua é
gerado do conjunto Python). Instância inativa contribui **capacidade zero** e mantém a ocupação:
pausar **não** interrompe a sessão em curso, e o que sai de circulação são as vagas **livres** —
`paused_capacity = Σ max(0, max_concurrent − ocupação)` **por instância**. Com `max_concurrent=3` e 1
sessão viva, saem **2**, não 3. **INVARIANTE: a linha FECHA** —
`total_instances = busy + busy_elsewhere + paused_capacity + available` (salvo sobre-alocação, em que
`available` clampa em zero). `paused_capacity` é obrigatório na linha pelo mesmo motivo de
`busy_elsewhere`: sem ele `available < total − busy` fica inexplicável e alguém reverte para o modelo
sem pausa. **A pausa NÃO limpa o `busy_set`** (só o logout limpa) — limpar zeraria o `busy` com sessão
em andamento e deflacionaria o `busy_elsewhere` dos pools irmãos do mesmo recurso. Gate:
`infra/test/gate_pause_capacity.sh`.

**A ocupação é DERIVADA do semáforo do RECURSO, nunca de um contador** (fatia 2 da capacidade
compartilhada, 2026-08-02). Um recompute em Lua (`_RECOMPUTE_POOL_OCCUPANCY_LUA`) sobre
`ready_set ∪ busy_set` do pool:

```
total_capacity = Σ max_concurrent(i)                    available      = max(0, total_capacity − used_global)
used_global    = Σ SCARD({t}:instance:{i}:sessions)     busy           = used_here
used_here      = Σ #{ m : occupant_pool(m) = P }        busy_elsewhere = used_global − used_here
```

`{t}:pool:{p}:active_count` foi **removido** (contava por POOL uma capacidade que é do RECURSO
— 1 humano de 3 vagas em 3 pools dava três linhas `available 3`, soma 6, verdade 2), e com ele o
INCR/DECR e o patch `available += 1` (com o teto/chão que o remendo exigia). `current_sessions`
**não** foi promovido a fonte: é da mesma família, e trocar um contador por outro só muda qual
mente depois. **`busy_elsewhere` é obrigatório na linha** — sem ele `available = total − busy` não
fecha e o modelo compartilhado parece bug. **`untagged` denuncia escritor de ocupante fora do
`claim_instance`**: deve ir a zero em ≤24 h (TTL do SET); persistente é bug, não ruído.

Gatilhos: `route()` (pool roteado) + **fan-out sobre `pools(instance)`** em `mark_busy`,
`remove_conversation`, `release_session_from_pool` e — desde a **F3a** (2026-08-02) —
`work_task_release`/`work_task_expire` (`refresh_snapshots_for_instance`; só reescreve
pool que já tem snapshot — inventar `sla_target_ms`/`channel_types` seria publicar config falsa).
`work_task_claim` entra de carona no `mark_busy`. O bootstrap (`instance_bootstrap._refresh_pool_snapshots`, NX, TTL 60 s) é
uma segunda implementação: publica `model: "bootstrap_placeholder"` com `available`/`total_instances`
derivados do SCARD e **omite `busy`/`busy_elsewhere`/`untagged`** — ausência é honesta, zero não
seria.

**Defeito C — `Σ available(pool)` conta o mesmo recurso uma vez por pool** e **não é corrigível na
linha do pool**: a linha está certa (aquele pool alcança mesmo N vagas); somá-la é que não pode, e a
informação de sobreposição não está lá. Segunda superfície, **F4a ✅ 2026-08-02**: rollup
`{t}:capacity:snapshot` (`compute_tenant_capacity`, throttle 5 s, TTL 1 h) agregando `max(0,
max_concurrent − SCARD)` sobre instâncias **DISTINTAS**, **por TIPO de licença** — humano e IA são
moedas não-fungíveis, então **não existe `available` escalar no topo** (somá-las seria a falácia de
aditividade um nível acima). Tipo vem de `Pool.agent_kind` (autoridade canônica, nunca de
`source`/`agent_type_id`); pool sem `agent_kind` ou instância em pools de tipos DIFERENTES cai no
balde **`unknown`**, publicado como tipo próprio e logado — dobrar em `human` seria escolher a moeda
cara em silêncio. `pools_available` sobrevive como contagem aditiva ("há por onde entrar?"), mas
chaveada por **(tipo, canal)**: contá-la só por canal fazia `human/whatsapp` publicar 19 num tenant
com 2 pools humanos. **`by_channel` é PROJEÇÃO, não partição** — instância que serve 2 canais conta
nos dois, então `Σ by_channel` excede o total do tipo (628 p/ 353 instâncias no demo); não existe
soma válida entre canais. Gatilhos: fan-out (`refresh_snapshots_for_instance`) + flusher (cobre
tenant ocioso). `system_availability_check` devolve `available_by_kind` do rollup; rollup ausente →
`null` + `capacity_unknown`, **nunca** voltando a somar as linhas (a soma é o defeito, não o fallback
dele). **F4b ✅:** `/v1/operational/pools` repassa em `summary.capacity` e `MonitorTab`/`PoolsPage`
mostram um cartão por tipo. **Escopo (`accessible_pools`) exige RECOMPUTE, não recorte** — a dedução
não projeta sobre subconjunto: `compute_tenant_capacity(only_pools=…)` via `GET /v1/capacity?pools=`
(porta 3550), chamado pelo agent-registry com cache 5 s. Recurso logado dentro E fora do domínio
conta INTEIRO (escopo = "quanto os MEUS pools alcançam"); `only_pools=[]` ≠ `None`.
**F4c ✅:** na série `pool_occupancy_peaks`, `__total__.provisioned_capacity` passou à capacidade
deduplicada e entraram linhas `__capacity_{kind}__` (a linha do pool **não** mudou — está certa e é
não-aditiva). Janela de arranque (1–2 min pós-restart, sem rollup) publica o `Σ` inflado com log
**e marcador na própria série**: minuto sem linhas `__capacity_*` ⇒ `__total__` não confiável.
Ocupação por tipo segue AMOSTRADA (`max` de somas — P2).

**F5b ✅ 2026-08-02:** o *live fallback* de `pool_status_get` devolvia `SCARD(pool:instances)` —
PERTENCIMENTO, não capacidade (conta instância lotada como disponível, ignora vaga gasta em pool
irmão, não filtra pausa/wrap-up), num tool que o Skill Flow usa para decidir oferta de canal **ao
cliente**. Agora devolve `available: null`, `status: "unknown"` e o motivo; a fila segue respondida
(é fato do pool). **Mudança de contrato**: fluxo que compare `available` numericamente recebe `null`
no caso sem snapshot.

**Pico de ocupação é EVENT-DRIVEN, não amostrado** (P1, 2026-08-02). Pico é o máximo de uma função
escada: qualquer intervalo de amostra pode cair inteiro entre duas subidas, e encurtar o intervalo
só estreita a classe de falha. O valor é gravado na TRANSIÇÃO — watermark `{t}:pool:{p}:peak:{minuto}`
(+ `:peakcap:` com a capacidade **do instante do pico**, TTL 2 h), por `record_pool_peak`, com três
chamadores e nenhum a mais: **(1) alocação** (`mark_busy`, sobre o `used_here` que o recompute já
devolveu — único que faz o pico SUBIR), **(2) virada do bucket** no flusher (carga carregada:
`max(novo) := ocupação corrente`), **(3) liberação** (`release_instance`, com o valor de ANTES — o
mesmo seed da virada, disparado por evento, para o pico que sobe e desce entre duas passadas do
flusher). **INVARIANTE: o bump NUNCA mora dentro de `write_pool_snapshot`** — lá ele faria a F3a
bumpar em liberações e o pico voltaria a ser *amostrado nos instantes de escrita de snapshot*, sem
nada ficar vermelho; `write_pool_snapshot` apenas **devolve** o recompute. `_occupancy_sampler` virou
**flusher** (mesmo tópico `pool.occupancy`, mesma tabela `pool_occupancy_peaks`, mesmo endpoint,
mesma UI); segue amostrando só os agregados de admissão (item 7b).

**P2 ✅ — o `__total__` do tenant também é event-driven.** Não é derivável dos watermarks por pool
(`max` de SOMAS ≠ soma de `max`: quatro pools com pico 1 no mesmo minuto e total real 2). Fonte =
ZSET `{t}:occupancy` (`instance → ocupação`, `ZREM` em zero ⇒ cardinalidade O(ocupadas)); atalho O(1)
= contador `{t}:occupancy:total`, ambos escritos num Lua que tira o delta de `ZSCORE` antes/depois.
Ganchos DENTRO de `claim_instance`/`release_instance`/`swap_to_hold` (nunca nos call sites), FORA do
Lua da vaga (que é single-key/cluster-safe por decisão). **INVARIANTE: o contador só existe porque é
CONFERIDO** — `reconcile_tenant_occupancy` roda 1×/min no flusher, corrige para a fonte e LOGA o
drift; sem ela este contador é o `active_count` que o arco removeu, e deve sair junto. Não clampa
negativo: total impossível é a única evidência de caminho de vaga fora dos ganchos. **Descontinuidade na série:**
a fonte mudou de `active_count` (derivava para cima) para `used_here`, e agora o método mudou —
marcar a data no eixo se a série virar base de dimensionamento.

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
> **Conteúdo ✅** — as quatro rotas que servem UM contato (`/v1/transcript/sessions/{id}`,
> `/sessions/{id}/stream`, `/workflow-trace`, `/pipeline-state`) recortam por pool desde
> 2026-08-30. Ali a pergunta é de **PERTINÊNCIA** (*esta sessão é dos meus pools?*), não de
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

**on_human_end** → NPS + wrap-up agents activated in parallel. NPS visibility = `["@ctx.session.customer_participant_id"]` (customer-only). Wrap-up visibility = `["@ctx.session.human_agent_participant_id"]` (agent-only). **Phase B**: `agent_done` does NOT close WS; bridge holds close until all hook agents complete. `hook_pending` Redis counter controls when `_trigger_contact_close()` fires. **Phase C**: `post_human` hooks fire after all `on_human_end` agents complete. Participation events (`conversations.participants`) written by bridge for analytics.

Pre-hook ContextStore writes (before hooks fire): `session.close_origin`, `session.customer_participant_id`, `session.human_agent_participant_id`.

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

**LLM Accounts Catalog (2026-07-01)**: config-api namespace `llm_accounts` (platform-ui: Resources → LLM Accounts) stores non-secret account metadata (`provider`, `display_name`, `rpm_limit`, `tpm_limit`, `active`) per catalog id; the API key itself stays exclusively in env var `PLUGHUB_LLM_ACCOUNT_<ID_UPPER_SNAKE>_API_KEY` on ai-gateway (naming-convention binding, no stored env-var-name field). ai-gateway loads the catalog at boot (`load_llm_accounts_catalog()`), falling back gracefully to the legacy `PLUGHUB_ANTHROPIC_API_KEYS`/`PLUGHUB_OPENAI_API_KEYS` construction if config-api is unreachable. `Pool.llm_account_ids: string[]` (preference order) is written to ContextStore as `session.pool.llm_account_ids[]` by Routing Engine, read by the skill-flow-engine `reason` step, and forwarded as `preferred_config_ids` to `AccountSelector.pick()` — same fallback semantics as the pre-existing evaluation-campaign usage. `ReasonEngine` (`/v1/reason`) was upgraded to be account-aware as part of this change (it previously had no multi-account support, unlike `/v1/inference`).

→ See [`docs/arcos/ai-gateway.md`](docs/arcos/ai-gateway.md)

---

## Arc 8 — Agent Availability & Pause Tracking

Pipeline for tracking human agent pauses. Config API namespace `agent_activity`, key `pause_reasons` (seedable pause reason list). Pause endpoints: `PUT /api/agent-pause` and `PUT /api/agent-resume` in mcp-server-plughub — updates Redis state, publishes `agent_pause`/`agent_ready` to `agent.lifecycle` Kafka with `reason_id`/`reason_label`. ClickHouse table: `agent_pause_intervals` (ReplacingMergeTree). Analytics: `GET /reports/agent-availability` with pool scoping. Platform-UI: a bancada é o **modo comparar** de `/analise/resources` (F3 do ADR de relatórios, 2026-08-29 — `/analise/agents` virou redirect; a `AgentReportsPage.tsx` era órfã, com rota `/contacts/reports/agents` inexistente, e foi REMOVIDA na F0, 2026-08-28).

→ See [`docs/arcos/arc8-agent-availability.md`](docs/arcos/arc8-agent-availability.md)

---

## Frontend Architecture — platform-ui

Single-app shell in `packages/platform-ui/`. Design tokens: `primary=#1B4F8A`, `secondary=#2D9CDB`, `accent=#00B4D8`, `green=#059669`, `warning=#D97706`, `red=#DC2626`. Font: Inter. Never use inline hex — Tailwind tokens only.

Roles: `operator` (Monitor+Contacts), `supervisor` (+Evaluation+Reports), `admin` (+Config+Skills), `developer` (+DevTools), `business` (cross-cutting, no operational items). **ABAC gates** on nav items: `operacao` field gates Monitor/Editor/Calendar/Deploy/AgentAssist; `visualizar` gates Reports/Análise tabs.

Nav groups (navKey): Home 🏠, Console 🖥️ (contacts.operacao), Monitor 📡 (Sessions/Agents/Pools/Events/Processes), Fluxo 🔄 (Editor/Deploy → skill_flows.operacao), Avaliação ✓ (Forms/Campaigns/Knowledge/Evaluations), Analytics 📊 (Sessions/Agents/Events/Processes/Quality → visualizar/report), Configuração ⚙️ (Dashboards/Resources/Platform/Channels/Calendars/Masking/Billing/Access). Legacy redirects: `/workflows` → `/workflow/monitor`, `/skill-flows` → `/agent-flow/editor`, `/reports` → `/contacts?tab=analise`.

**Skill Deploy Lifecycle**: `deploy_status` (draft/published) + `skill_deployments` table. `PUT /v1/skills` always sets `deploy_status=draft` on new skills, NEVER modifies it on updates. `POST /v1/skills/:id/deploy` — only action that sets published.

**Agent Assist UI** at `/agent-assist`: 4-tab right panel (Estado, Capacidades, Contexto, Histórico). Substitution mode for menu cards. Visibility array routing for NPS/wrap-up agents. Optimistic echo for button selections.

→ See [`docs/arcos/platform-ui.md`](docs/arcos/platform-ui.md)

---

## Arc 7 — Auth, RBAC + ABAC, Performance Routing

**auth-api** (port 3200): users + sessions in PostgreSQL schema `auth`. JWT HS256 TTL 1h; refresh token rotation (43-char opaque, SHA-256 stored). Silent re-auth from `localStorage('plughub_refresh_token')`. `accessible_pools[]` in JWT: empty = all pools; non-empty = row-level filter in analytics-api.

> ⚠️ **ADMINISTRAR uma pessoa nunca é o mesmo campo que CONCEDER capacidade a ela**
> *(split de 2026-08-27)*. `config.users` era a chave-mestra do tenant: cobria criar/editar usuário
> **e** conceder papel, módulo e escopo de pool, então toda fronteira ABAC do produto colapsava em
> *"tem `config.users`"* — quem o recebesse para gerir a operação podia marcar qualquer módulo em si
> mesmo, virar `admin`, ligar `unrestricted`, ou redefinir a senha do admin e entrar como ele.
> Hoje: **`config.users`** = pessoa (criar, editar dados, ativar/desativar, grupos) · **`config.permissions`**
> = capacidade (papéis, módulos/campos, escopo de pools).
>
> **O portão tem QUATRO portas, e fechar só a primeira é decorativo:** a **rota** (`/permissions`,
> `/templates`, `/modules`, `module-config`), o **corpo** (`roles`/`accessible_pools`
> num `POST`/`PATCH /users`, cuja porta é `config.users`), o **alvo** (editar/apagar quem
> *detém* `config.permissions`) e o **escopo** (`POST`/`DELETE /v1/groups/{id}/supervisors`).
>
> *`unrestricted` saiu do conjunto do corpo em 2026-08-31 porque saiu do MODELO (AUT-15).
> Campo que ninguém pode mandar não precisa de portão — mas precisa **não ser aceito em
> silêncio**: pydantic ignora chave desconhecida, então a rota o recusa com 422 nomeando.*
>
> A porta do alvo existe por causa da **senha**: resetá-la é campo de PESSOA e tem de seguir permitido, então quem
> barra o *"reseto a senha do admin e entro como admin"* é a proteção do alvo, nunca a guarda de
> corpo. A do escopo existe porque `resolve_supervisor_scope` deriva `supervised_user_ids` de quem a
> pessoa SUPERVISIONA, e a evaluation-api consome esse claim para decidir de quem ela vê avaliações —
> auto-nomear-se supervisor de um grupo é conceder. **Membership fica** em `config.users`: alargar por
> ali só alcança grupo que já se supervisiona, que é a definição do escopo, não uma extensão dele.
>
> **O discriminador do corpo é `model_fields_set`, não o valor** — omitir `roles` aceita o default;
> enviá-lo é conceder, ainda que o valor coincida. Comparar valores deixaria passar *"mandei o mesmo
> papel de novo"*, e a tela manda o formulário inteiro. **Corolário de modelagem:** um campo cujo
> rótulo tem **"e"** provavelmente são dois fatos — e se um deles concede capacidade, é chave-mestra
> até prova em contrário. Gate: `infra/test/probe_config_permissions_split.sh`.

> **PAPEL É PRESET DE NASCIMENTO, NUNCA PORTÃO** *(passo 3, 2026-08-27)*. Cada campo do
> catálogo declara `role_defaults`; `create_user` aplica o preset dos papéis **uma vez**, na
> criação. Antes disso o `INSERT` não gravava `module_config` e **todo usuário criado pela tela
> nascia com config vazio** — dentro da degradação graciosa. O menu funcionava porque o buraco o
> sustentava, e inverter a degradação sem preset faria cada usuário novo **nascer cego**.
>
> Consequências aceitas: **editar o preset não muda quem já existe** (mesma semântica de
> seed-if-absent do resto da casa — política se aplica por edição, não por decreto), e **trocar o
> papel depois não reescreve grants** (rebaixar é ato deliberado; deduzi-lo da troca apagaria em
> silêncio o que foi dado à mão). Múltiplos papéis rendem o **maior** acesso por campo, nunca a
> interseção. Gate: `infra/test/probe_role_preset_on_create.sh`.

> **O MENU TEM UM PORTÃO SÓ, E ELE É GRANT-FIRST** *(passo 5, 2026-08-27)*. Eram três
> mecanismos empilhados, e dois invisíveis para quem lia só o `Sidebar.tsx`: o `roles:` por
> item/grupo, o papel `admin`/`supervisor` liberando dentro de `passesAbacRule`, e — o mais
> silencioso — **`module_config` vazio liberando**, de modo que bastava um usuário sem grants
> para ver a plataforma inteira com o menu parecendo normal.
>
> **Os `roles:` não eram "um passo depois": eram o que tornava o grant INERTE.** O cabeçalho do
> grupo decidia antes da ABAC, então conceder o campo do filho não mudava o que a pessoa via —
> medido, 11 grants do supervisor que ele não alcançava. Regra derivada: **dois portões sobre a
> mesma decisão significam que o mais grosseiro é o único que vale**, e conceder no fino vira
> no-op silencioso.
>
> **O ramo saiu INTEIRO, não virou flag por regra.** Marcar cada regra com `strict: true`
> deixaria a porta aberta para a próxima entrada escrita sem a flag; sem o ramo não há flag a
> esquecer. Corolário: quando a correção pode ser *"marcar cada caso"* ou *"remover a alternativa"*,
> a segunda é a que não depende de memória.
>
> **Ausência de grants nunca é autorização** — mesma inversão de `accessible_pools`, pela mesma
> razão. E **NÃO existe porta larga**, nem sequer o claim `unrestricted` — que desde
> 2026-08-31 (AUT-12/13/15) não é cunhado, não é lido, e nem existe mais como campo:
>
> > **ESCOPO e CAPACIDADE são eixos distintos, e um claim de escopo nunca concede capacidade**
> > *(corrigido em 2026-08-27, no mesmo dia em que foi introduzido)*. `unrestricted` responde
> > *"quais linhas/pools/pessoas eu alcanço"*; `module_config` responde *"quais funções eu posso
> > exercer"*. A primeira versão do portão grant-first deixou o claim liberar o menu, e a
> > evidência de que isso é defeito é concreta: `probe@` (unrestricted, **zero grants**) passou a
> > ver `nav.audit` — o módulo de **Auditoria LGPD**, que existe para ser concedido
> > individualmente ao DPO. A alternativa (manter o atalho e excluir os módulos de concessão
> > individual) seria lista de exceção, que envelhece. Não falta a ninguém: o admin tem os grants.
>
> Gate: `infra/test/probe_nav_grant_first.sh` (o S6 guarda exatamente essa regressão, que é a mais
> tentadora do arco — o claim está à mão e parece atalho razoável até alguém contar o que abre).

**ABAC** (`module_config` in JWT): `auth.module_registry` seeded from `infra/modules.yaml`. 8 modules: `evaluation`, `contacts`, `billing`, `config`, `skill_flows`, `workflows`, `agent_assist`, `campaigns`. Each field has `access: none|read_only|write_only|read_write` + `scope[]`. `PermissionChecker.can(module, field, minAccess?, scopeId?)`. Graceful degradation for legacy accounts without `module_config`.

**Performance routing** (Arc 7d): `performance_score = resolution_rate × (1 − escalation_rate)`. Blending: `(1-w) × competency + w × performance`; `w = performance_score_weight` (default 0.0, env `PLUGHUB_PERFORMANCE_SCORE_WEIGHT`). Redis key `{tenant}:agent_perf:{agent_type_id}` (TTL 6h). Batch job in analytics-api runs every 5min, lookback 7 days, min 5 sessions for statistical significance.

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

**Webhooks**: `plughub_wh_{43-char}` token, SHA-256 stored. CRUD (X-Admin-Token) + public `POST /v1/workflow/webhook/{id}` (X-Webhook-Token). Delivery log with timing and status. `origin_session_id` in WorkflowInstance links workflow to parent contact session.

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

O Console é uma **superfície de orquestração**: o operador humano dirige, delega e monitora agentes AI como coparticipantes de primeira classe (AI e humanos simétricos no modelo de sessão). Funcionalidades: cartões de participantes AI em tempo real (step/status do Skill-Flow); "Adicionar Especialista" (invoca pools de `mentionable_pools` via A2A `assist`); "Delegar Tarefa" (seleção de mensagens → drawer instrução+visibilidade → card de resultado no `agent_done`); Tab de Orquestração (steps do Skill-Flow + intervenções de supervisor). **Permissões**: operar = `agent_assist.operacao`; intervir = role `supervisor` + scope ABAC.

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

Módulo ABAC `audit` para DPO/compliance — ortogonal às roles existentes. Qualquer usuário com `module_config.audit.*` no JWT tem acesso escalonado. Cinco campos: `sessions`, `mcp_calls`, `user_access`, `data_requests`, `config_snapshot` — os dois primeiros ativos.

**analytics-api** tem dois endpoints em `/v1/audit`: `GET /sessions/{id}/messages` e `GET /mcp-calls`.
Gate `_check_audit_access(request, field)` (`audit.py`) — **cinco** ramos declarados, cada um com o seu
código: `analytics_open_access` LIBERA nomeando o ator como `open_access`; **sem `auth_jwt_secret` → 503**
(falha do SERVIÇO — postura oposta à do `pool_auth`, que degrada aberto: lá é escopo de leitura, aqui é
dado pessoal); credencial ausente ou não verificável → **401**; `module_config.audit.{sessions|mcp_calls}`
≥ `read_only` LIBERA; senão **403**, e a recusa **nomeia quem foi barrado**. O verificador é o CANÔNICO
(`plughub_authz`) desde 2026-08-28 — a lista indexada local, onde `write_only` era maior que `read_only`,
saiu com ele. **Nunca `enforce_write` aqui:** ele responde direto, e esta casa precisa GRAVAR antes de
responder.

> **A trilha só vale se a recusa também for gravada — e a sem credencial não era** *(fechado
> 2026-08-28)*. As duas rotas carregavam `optional_pool_principal` só pelo `tenant_id` (o
> `accessible_pools` nunca foi lido: auditoria é ortogonal a pool). Sendo `Depends`, o `401` dela era
> levantado **antes do corpo do handler**, então `_record_access` nunca rodava — e o banner da tela
> prometia que todo acesso fica registrado. **Regra derivada: portão que decide dentro de um `Depends`
> não pode ter efeito colateral no handler**; se a recusa precisa gravar, ela decide onde grava. Hoje a
> identidade sai do próprio portão. Gate: `infra/test/probe_audit_surface.sh` (P4) +
> `tests/test_audit_handler_trail.py` — este último nasceu porque uma mutação (`status_code=denied.status`
> → `403`) sobreviveu a 23 testes verdes: eles cobriam o VEREDICTO, e nada atravessava a rota.

> ⚠️ **Corrigido 2026-08-22 por medição.** Esta seção afirmava `_require_audit_access()` e o dual-write
> `[timeline_row, mcp_audit_log_row]` como entregues (CHANGELOG de 2026-05-14). **Nada disso existia na
> árvore**: nenhum gate no handler — só `optional_pool_principal`, que confere ASSINATURA e não
> autorização, então qualquer token válido do tenant lia dado pessoal —, nenhum `INSERT`, e nenhuma das
> duas tabelas em `_ALL_DDL` (`probe_audit_surface.sh`: 0 de 2, com `session_timeline` de testemunha).
> O `401` que o token malformado devolve é o que fazia o buraco parecer coberto.

**ClickHouse**: `audit_access_log` (`MergeTree` — **nunca** deduplicado por design LGPD: o valor da trilha
é dizer quantas vezes um dado foi acessado e por quem). **`mcp_audit_log` NÃO existe e não foi criado de
propósito** — medido zero tráfego na borda `invoke` neste ambiente (`session_timeline` recebe linha de um
único parser, o de `mcp.audit`, e está vazia), e criar tabela que ninguém preenche é o "existe ≠ está
pronto" de novo. Dívida dormente registrada no `TODO.md`. `parse_mcp_audit_event()` grava **uma** linha,
em `session_timeline`, que é de onde `/v1/audit/mcp-calls` lê.

**platform-ui**: `AuditPage` em `/audit` (5 tabs: Sessions + MCP Calls ativos; 3 stubs). Nav entry standalone "Auditoria LGPD" (🔍) com ABAC gate `audit.sessions`. Warning banner: todo acesso registrado em log.

**Deferred**: `original_content` desmascarado (requer endpoint batch em Core), `user_access` logs, SAR/erasure pipeline, `config_snapshot`.

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

Canal `webrtc` browser-to-SFU com medium negociado em tempo real (video→voice→text). Coexiste com `voice` (PSTN/Twilio = tronco externo); `webrtc` = clientes na webapp. **SFU**: LiveKit self-hosted (gravação por egress, supervisão hidden subscriber, multi-participante). **Invariante**: tokens LiveKit emitidos exclusivamente pelo Channel Gateway, nunca expostos ao browser. STT/TTS reusa os FallbackProviders do voice (transporte = LiveKit PCM frames). Console: `WebRTCOverlay` (vídeo/waveform por medium). `media_capabilities: [video,voice,text]` no agente; text = fallback universal. *Futuro*: bridge PSTN→WebRTC via LiveKit SIP Ingress (ver § Pending).

→ See [`docs/arcos/arc15-webrtc.md`](docs/arcos/arc15-webrtc.md)

---

## Arc 19 — Modelo Unificado de Sessão: Workflow como Canal Webhook

Elimina a dualidade contact/workflow tratando workflows como canal `webhook` na channel-gateway. Cada skill registrada num pool webhook é um "endpoint" (análogo a DIN de voz ou número WA). O trigger cria uma sessão normal, o routing engine aloca instância skill-flow do pool, e o `session_id` é o identificador persistente por toda a execução — incluindo múltiplos ciclos de suspend/resume.

**Status `suspended`** adicionado ao domain de sessão. No `suspend()`, o agente fecha o segmento e devolve ao pool (`agent_ready`); a sessão persiste com TTL estendido no Redis (EXPIRE calibrado ao `timeout_hours` — substitui PostgreSQL para durabilidade). No resume, nova alocação normal → novo segmento. **Resume_token lookup** via hash Redis `{tenant}:resume_tokens → session_id`.

**Segregação workflow vs. agente**: perfil `workflow` (channel_type: webhook) permite steps `task/choice/catch/escalate/complete/invoke/reason/suspend/collect/receive` — proibidos `menu/notify/begin_transaction/end_transaction`. Perfil `agent` (demais channels) permite `menu/notify/begin_transaction/end_transaction` — proibidos `suspend/collect`. Validado em parse do YAML + guard no engine.

**Collect step revisado**: exclusivo de workflows. Cria sessão-filho de contato com channel negociado por capabilities (Arc 16). Workflow suspende; agente channel-aware atende a sessão-filho e retorna resultado. Workflow nunca conhece o canal usado.

**WebhookAdapter** em `channel-gateway/adapters/webhook.py`: `POST /v1/channels/webhook/{skill_id}` (trigger), `POST /v1/channels/webhook/resume/{token}` (resume), `GET /v1/channels/webhook/{session_id}/status`. **Pool webhook**: `channel_types: [webhook]` + `skill_id` como endpoint.

**O que é eliminado**: `workflow-api` lifecycle endpoints, `WorkflowInstance` entidade separada, `skill-flow-worker` Kafka consumer, `workflow.events` topic, entidade Journey ✅ (Fase F concluída 2026-05-28), Monitor/Processes e Analytics/Processes páginas separadas.

**Monitor unificado** (4 abas — período: now/last_hour/last_24h/today): Sessions (channel_type filter, badge suspended, métricas Resolved/Escalated/Failure/Timeout/Cancelled/TMA), Pools (snapshot + tendência; webhook pools mostram capacidade configurada), Agents (humanos/AI; skill-flow instances via Pools), Events (Arc 12 business events, filtro regex de category). **Analytics unificado** (4 abas): Sessions (ANI/DNIS por channel_type; hierarquia sessions→segments→detalhe), Pools (time-series capacity), Agents (consolidado + drill-down segments), Events (time-series Arc 12 + drill-down segments). **duração tem DOIS nomes e eles NÃO são intercambiáveis** (D9): `elapsed_time_ms` (tempo — wall-clock do caso, **inclui** as esperas; webhook = `closed_at − primeiro segmento`) × `agent_time_ms` (agente × tempo — `Σ segments.duration_ms` com `agent_type != 'system' AND role IN ('primary','specialist') AND duration_ms IS NOT NULL`). ⚠️ Este arquivo afirmou por meses *"TMA webhook = `SUM(segment.duration_ms)`"* como se fosse implementação: era **falso** (o código fazia e faz wall-clock, e registrava a soma como refino adiado) e **conceitualmente errado** — a soma não é uma duração: segmentos se SOBREPÕEM (`@mention` é sempre paralelo ao primary e é rotina; especialista de conferência nasce dentro da janela do pai; hooks posatt são paralelos entre si), logo `Σ ≥ wall-clock` com sobreposição e `Σ ≤` com lacunas. **Nunca somar segmentos para obter tempo de sessão, e nunca comparar as duas.** Tempo suspenso tem lugar próprio: `analytics.session_transitions` (D4).

**6 fases**: A ✅ (WebhookAdapter + channel type), B ✅ (status suspended + TTL Redis), C ✅ (orchestrator-bridge: skill-flow como agente nativo), D ✅ (workflow-api deprecation), E ✅ (Monitor/Analytics unificados), F ✅ (Journey entity elimination — 2026-05-28). **Arc 19 completo.**

→ See [`docs/arcos/arc19-unified-session-model.md`](docs/arcos/arc19-unified-session-model.md)

---

## Dialog Primitive — Scripted-Dialog Runner (survey + OTP)

Primitivo de "interação scriptada delegada" compartilhado por survey e OTP
(ADR `docs/adr/adr-otp-workflow-and-dialog-primitive.md`).

**Quatro costuras inegociáveis:** conteúdo (DialogForm JSON) × controle (skill/workflow chamador) ×
canal (runner) × **segredo** (`OtpService`). O código do OTP **nunca** passa pela mão de um
agente/runner — gerar/enviar/verificar ficam no serviço confiável; o runner só carrega o que o
**cliente** digitou. Vale igual para survey: resposta é do cliente, nunca fabricada.

**DialogForm** (`@plughub/schemas/dialog.ts`): script **linear** de nodes `statement` (→ notify) e
`question` (→ menu), versionado (draft/published), i18n embutido, `capture` (binding de métrica) e
`validation` (formato). **Sem `next` condicional — branching é do skill, nunca no JSON**, senão vira
linguagem em JSON. Store canônico **`dialog-api`** (porta 3760); a tool MCP **`form_get`** resolve o
form publicado e normaliza num bloco `render` single-turn. **Contrato uniforme:** o runner devolve
`payload = { value: <escalar> }` e o domínio faz verify/record — não unificar, vira `if` gigante.

⚠️ **DOIS veículos, e a divisão é mecânica, não estética.** O runner-especialista (via `delegate()`)
serve chamadores que **podem suspender**. **Hooks de `on_contact_end` NÃO podem delegar** — delegar
suspende o hook agent, o bridge trata `suspended` como hook concluído e **fecha o contato antes de
renderizar**; por isso o NPS ativo consome o primitivo **INLINE** (`form_get` + menu dinâmico). Os
dois compartilham `DialogForm` + `form_get` + menu dinâmico; só divergem em suspender-ou-não.
Delegate é de **nível único** — aninhar no collector colide em `session.delegate_resume_token`.

**Três superfícies, um conteúdo:** chat (runner) · inline (hook) · página web pública
`GET /survey/{token}`. Entrega real do link (SMS/e-mail) é trilha à parte, ainda não construída.

**Invariante de build:** mexer no `MenuStepSchema` obriga a **rebuildar junto** todo serviço TS que
valida skills (`agent-registry`), o engine (`skill-flow-service`) e o `mcp-server` — senão o
agent-registry rejeita o ref com 422.

**Provisionamento:** `infra/dialog/*.json` aplicado no boot pelo `dialog-seed`, via API oficial e
**seed-if-absent**. Editar um JSON onde o form já está publicado é **no-op** — mesma pegadinha do
YAML de skill (`DIALOG_SEED_RECONCILE=true` inverte).

**Pendente (Fatia 2):** `channel_policy: elect`; timeout dinâmico do runner; preview no editor;
entrega real do link web.

→ See [`docs/product/dialog-primitive-and-runner-design.md`](docs/product/dialog-primitive-and-runner-design.md),
[`docs/adr/adr-otp-workflow-and-dialog-primitive.md`](docs/adr/adr-otp-workflow-and-dialog-primitive.md).
História (editor, loop, retry, multi-locale, datas) no `CHANGELOG.md`.

---

## Scheduler / Agenda — `scheduler-api` *(completo, Fases 1–3)*

Serviço na porta 3650. Uma **Agenda** é recurso **domain-agnostic** que, num *quando/modo* (1x ou
recorrente daily/weekly/monthly, `times[]` no dia), **aciona um POOL via webhook** — nunca um skill
(invariante S4). Duas camadas: Redis (sorted-set `scheduler:timers` + poller 15 s + re-hidratação no
boot) sobre Postgres (schema `scheduler`, fonte de verdade).

**Invariantes:**
- o scheduler **não reimplementa o "quando"** — `business_day_policy` consulta o **calendar-api**,
  que é a autoridade única;
- **status da agenda = "acionou o pool ou não"**; a execução é da SESSÃO (ledger guarda `session_id`
  para drill-through e **nunca espelha** o estado dela);
- `dispatched` significa que a gateway criou a sessão — admissão e capacidade aparecem no ciclo da
  sessão, não aqui;
- **sem retry no v1**: `failed` é gravado e aparece no Monitor;
- recorrência calcula só a **próxima** ocorrência e re-arma no disparo.

**Promote agendado:** o corpo do job é um pool webhook que faz `invoke pool_promote` — wrapper
auditado do **único** caminho de promote. Não-2xx (409 `next` vazio, 422 capacidade) vira `isError`
→ `on_failure`; **promoção nenhuma acontece em silêncio**. Endereça pool, nunca skill/versão, e
**sem pin**.

**ABAC `scheduler.{configurar,operacao}` é grant-first, sem role default nem bypass de admin** — só
quem recebe o campo em Acesso vê as telas. Autoria em `/config/schedules`; operação em
Monitor › Agendas (disparo imediato não consome a recorrência).

→ See [`docs/product/scheduler-agenda-spec.md`](docs/product/scheduler-agenda-spec.md),
[`docs/adr/adr-timer-scheduler.md`](docs/adr/adr-timer-scheduler.md)

---

## Outbound — Mailing + Campaign + Delivery *(arco completo, Fases 1–5)*

Substrato **genérico** de contato ativo: `mailing` (audiência) + `campaign` (orquestrador fino, que
endereça **POOL** — invariante S4) + `campaign_delivery` (estado por-campanha). Store canônico
**`mailing-api`** (porta 3660, schema PG `outbound`). **Survey é o 1º consumidor, não o dono.**

**Invariantes:**
- metadado da entrada é **opaco** — contrato produtor↔consumidor, a plataforma não o interpreta;
- **membership (`mailing_entries`) ≠ suppression (`campaign_deliveries`)** — não fundir;
- entrada = **`(pessoa, contexto)`**, nunca só pessoa;
- **agentes drenam via MCP e nunca tocam o DB** (`mailing_add`/`campaign_drain`/
  `campaign_delivery_result`: wrappers finos, `isError` em não-2xx, auditados);
- **pacing é a agenda recorrente**, não um laço no skill (tick drena ≤ `batch_size`);
- idempotência = `UNIQUE(campaign_id, mailing_entry_id)` + `FOR UPDATE SKIP LOCKED` no claim.

**Elegibilidade (`contact_eligibility_check`) é motor único e agnóstico** — substituiu o
`survey_eligibility_check`. Precedência **inegociável**: `opt_out` (cadastro `do_not_contact`, salvo
`campaign.transactional`) → janela de calendário → fadiga (`frequency_caps`/`quarantine_after`/
`channel_caps`). `claim=true` grava o fato na MESMA transação — a janela começa no envio, não na
decisão — e `reason` **sempre nomeia a regra**. Falha de dependência degrada para **ALLOW
barulhento**, nunca silencioso.

**Fan-out = dispatcher + worker** via `workflow_trigger` fire-and-forget; paralelismo pelo
`max_concurrent` do pool + allocate-or-queue. O contato usa o **`collect` LAZY** — ativo-síncrono só
é exigido na voz-com-agente, fora do corte.

⚠️ **No survey outbound o veículo é o link web (`survey_link_create`), NUNCA o `collect`** — o
collect chavearia o sinal pela raiz da sessão CHAMADORA (a do dispatcher, no fan-out), errada para o
survey do processo. Por isso o `origin_session_id` viaja EXPLÍCITO na metadata.

**Importador** é anti-corrupção em duas camadas: ingest normalizado público (agnóstico de formato,
seam reusável) × adaptador de arquivo que lê o `column_map` do mailing. Parse síncrono com teto
(`PLUGHUB_MAILING_IMPORT_MAX_ROWS`, 413 acima dele). Rejeita-linha-e-continua, nunca aborta o lote.

→ See [`docs/arcos/outbound.md`](docs/arcos/outbound.md),
[`docs/product/outbound-mailing-campaign-design.md`](docs/product/outbound-mailing-campaign-design.md).
História fase-a-fase, gates e datas no `CHANGELOG.md`.

---

## Pending (Next Iteration)

> **Só itens NÃO implementados.** Arco concluído sai daqui para o `CHANGELOG.md` — item pronto dentro
> de uma seção chamada *Pending* volta a ser triado como trabalho em aberto, que é o custo real de
> deixá-lo. Detalhe, fases e evidência por item vivem no `TODO.md` e nos `docs/`; aqui fica **o que
> está aberto e por que importa**.
>
> **Os baldes da triagem de 2026-08-17 foram REMOVIDOS em 2026-08-31** — a direção que os ancorava
> caiu em 2026-08-18 ([`n8n-arco-abortado-2026-08-18.md`](docs/product/n8n-arco-abortado-2026-08-18.md)),
> e por treze dias eles custaram contexto em toda sessão para dizer que não valiam. A evidência por
> item da triagem continua válida; os baldes e as âncoras de fase, não. **No lugar do alvo:** A2A
> server binding e editor gráfico próprio alavancado por *execução observável* — a direção
> *"config + interpretador genérico"* sobrevive inteira e **nunca dependeu do n8n**.

### Arc 15 — WebRTC
**Provisionar o SFU** é pré-requisito, não detalhe de deploy: não há LiveKit em compose algum, nem env
`LIVEKIT_*`, nem manifesto k8s, nem o SDK como dependência — o canal roda inteiro em `_dev_mode`, que
devolve token bem-formado e falso. Bloqueia qualquer medição de WebRTC e a decisão do bridge
PSTN→WebRTC via SIP Ingress.

### Usage Metering — adaptadores de canal
`whatsapp_conversations`, `voice_minutes`, `sms_segments`, `email_messages`: as funções existem em
`usage_emitter.py`, os adaptadores não as chamam. *(Separado: `llm_tokens_*` não emitido no
`/v1/reason` é **defeito**, não item de direção.)*

### Pricing — integração metering × pricing
Módulo que aplica planos e escreve `{tenant}:quota:limit:*`.

### Audit LGPD — Fases 2–5
`original_content` desmascarado (exige endpoint batch em Core) · logs `user_access` · pipeline
SAR/erasure · `config_snapshot` para o DPO. Pendentes por obrigação legal, razão própria e
independente de qualquer direção de produto.

### Quality Ingest — concerns abertos
(a) `ReplayContext` entrega `session_meta`/`participants`/`sentiment` em default para importados;
(b) correlação por-requisição: `pool_id` degrada se um contato vier partido entre POSTs.

### Isolamento do substrato por `origin` — Fase 2
Partição ClickHouse `PARTITION BY (…, origin)` + `pool.origin_class`. É governança/lifecycle, não
correção — **adiada por decisão**, aguardando gatilho próprio: importação externa real com obrigação
de retenção/erasure (`DROP PARTITION`).

### Business in Any Media — processo channel-abstract + comércio
**Fica:** resolvedor de identidade Fase C (`external_refs` + merge de clientes), gate de
identificação, commerce-cards com checkout mascarado e repasse ao PSP, novas `ChannelCapability`.
**A rejulgar (tarefa B1 do `TODO.md`):** o nível (a) *"fluxo negocial channel-abstract"*, o contrato
delegate-por-pool e o intake-flow — cortados com a razão *"vira template n8n"*, fundamento que caiu.
O critério agora é *quanto disso vira config + interpretador genérico*.

### Journey — modelo de 3 níveis
Abertos: cache `sessions.journey_id` não refrescado no merge (**otimização adiada por decisão**;
leituras vão por union-find) · guard de rota ABAC em `analise/*` (dívida app-wide de segurança) ·
exibição do sinal N3 no drill da própria Vista Processos.
→ See [`docs/product/journey-retorno-modelo-3-niveis-design.md`](docs/product/journey-retorno-modelo-3-niveis-design.md),
[`docs/product/journey-3-niveis-implementation-spec.md`](docs/product/journey-3-niveis-implementation-spec.md). Diagrama: `docs/product/journey-3-cenarios-unionfind.svg`.

### Fila de trabalho humano — aprovação R1
O pull genérico e o renderer genérico de collect-form (R0) estão entregues, e o wrap-up destacado
também. Aberto: **R1** — anexos, masking e ABAC completos na aprovação. Follow-up medido: o ingress
de resume aplica `approvals.decide` a **qualquer** resume com JWT; parametrizar por tipo de tarefa.

### Detach de hooks / pull direcionado — dívida de verificação
O arco A–F está completo, mas **duas lacunas são fato, não conflito**: a própria F4 declara que a
**lease não foi medida** (não há reaper), e **não existe gate re-executável da Camada F** — ela foi
validada por medição manual instrumentada. *Arco declarado completo sem gate versionado volta a ser
lembrança, não verificação.*

### Record/Replay Harness *(proposta)*
Harness de gravação/replay em todas as costuras, para regressão determinística e **gate de
promoção**. Falta captura full-fidelity MCP/AI Gateway, clock/seed injetável, gravação seletiva.

### Customer Surveys — módulo de pesquisas
**Fica:** S5/S8/S9–S11, store per-response e o **S7 (editor de DialogForm)**, que *ganha* importância
com a reversão — o conteúdo conversacional segue autorado em casa e a guarda do `ask_when` (sem
control-flow no form) segue load-bearing. **Resíduos do S1:** nenhum produtor de CES/PMF/FCR;
`value_label` ignorado em `CustomerVoicePage.tsx:161`. **A decidir:** se o S2 (runner genérico) volta
a ter dono próprio (tarefa C2); e o resíduo do `value_label` foi citado com **arquivo errado** na
triagem, a remedir antes de entrar em plano (C4).

### Outbound — refinamentos
`responded` por-delivery (submit → `campaign_delivery_result`) · skill de processo que auto-alimenta
a mailing no `complete` (hoje seed direto) · pertença à journey via `journey_merge` · pacing
`look_ahead` para o discador de voz, que depende do plano de mídia.

### Histórico de contatos do cliente / Cliente 360
⚠️ **Corrigido em 2026-08-31 por validação contra o CHANGELOG.** Esta entrada listava H3, HJ,
H4-geral, C1a, C1b e H5 como abertos; `CHANGELOG.md:17339` (2026-07-16) declara *"Fecha o Customer
History no v1: H1 · H2 · H3 · HJ · H4-geral · C1a · C1b · H5"* — **os seis estavam fechados havia
seis semanas**. O `TODO.md:6778` já dizia o certo; eram duas casas afirmando e a errada era a que
o índice lia. Abertos de verdade, ambos por gatilho e não por esforço:
- **busca full-text `GIN(tsvector)`** — a busca de mensagens usa substring no ClickHouse, suficiente
  no volume atual; é **otimização, não correção**. Gatilho: latência/volume medidos.
- **H4-survey** — origem+resultado do survey no briefing de retorno, **BLOQUEADO** porque o briefing
  ainda não existe.
