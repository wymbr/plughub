# PlugHub — Portal de Conhecimento

> Última atualização: 2026-05-25 · Estado da plataforma: Arc 16

Ponto de entrada único de toda a documentação do PlugHub. Cada seção mapeia um público e um nível de detalhe.

> **Revisão de documentação:** a avaliação completa do acervo (123 arquivos) está em [`revisao-documentacao-2026-05.md`](revisao-documentacao-2026-05.md). Documentos com drift conhecido estão marcados abaixo.

---

## Navegação rápida

| Quero entender... | Vá para |
|---|---|
| Visão técnica completa da plataforma | [visao-geral.md](visao-geral.md) |
| O que é o PlugHub e para quem serve | [product/overview.md](product/overview.md) |
| Como a plataforma compete no mercado | [product/competitive-analysis.md](product/competitive-analysis.md) |
| O que cada tela da UI faz | [Módulos funcionais](#módulos-funcionais-modulos) |
| Como um Arc/feature funciona internamente | [Arcos de implementação](#arcos-de-implementação-arcos) |
| Como um pacote funciona internamente | [Pacotes técnicos](#pacotes-técnicos-pacotes) |
| Como implementar um padrão (masking, mention, etc.) | [Guias temáticos](#guias-temáticos-guias) |
| Por que uma decisão arquitetural foi tomada | [ADRs](#adrs-adr) |
| Eventos Kafka e schemas | [kafka-eventos.md](kafka-eventos.md) |
| Modelos de dados e persistência | [modelos-de-dados.md](modelos-de-dados.md) |
| A arquitetura em camadas conceitual | [Camadas arquiteturais](#camadas-arquiteturais-layers) |

---

## Índice anotado da arquitetura *(movido do `CLAUDE.md` em 2026-09-06 — DOC-02)*

> **Onde esta seção e o resto deste arquivo discordarem, esta vence.** Ela veio do `CLAUDE.md`,
> onde era mantida a cada arco; o resto do portal está datado de 2026-05-25. A mudança não é
> cosmética: eram **duas casas indexando o mesmo acervo**, e a mais nova vivia no arquivo que toda
> sessão carrega no boot — 95 linhas de índice cobradas de toda sessão para dizer onde ficam os
> documentos que a maioria delas não abre. É o mesmo defeito que a § *Pending* teve (`DOC-01`).
>
> O `CLAUDE.md` guarda um ponteiro de sete linhas com a convenção de pastas; a lista anotada é aqui.


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
      operational-visibility.md ← snapshot de pool, ocupação derivada do semáforo, picos event-driven, rollup por tipo de licença (movido do CLAUDE.md em 2026-09-05)
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
      sentiment-tracking.md   ← cadeia inteira do sentimento: engine → gateway → ctx → Console (movido do CLAUDE.md em 2026-09-05)
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
      adr-contextstore-allowlist.md  ← ContextStore como ALLOWLIST: **TIPO é a declaração única** (formato × máscara-por-papel × classe LGPD), MAPA em `escopo.dominio.campo`, legado vira `alias` contado e datado. Pré-requisito: a omissão deixa de ser MUDA antes da inversão. Fases V0→V5 + D6–D9 — **V0–V4 e D6–D9 entregues; resta a V5 (fechar aliases) e a ALW-08 (afordância no editor)** — Aceito, parcialmente implementado. ⚠️ *Corrigido aqui em 2026-09-04: esta linha dizia **"a V4 (inverter o default) é a próxima e NÃO é reversível"**, e as duas metades eram falsas — a **D9 REDEFINIU** a V4 (deixou de ser inverter o default de leitura e passou a ser **ligar o portão de PUBLISH sobre o cadastro**, com runtime que nunca rejeita), e nela foi **ENTREGUE em 2026-09-02** como ALW-01. O ADR já registra que este mesmo erro aconteceu antes (§ correção de 2026-08-30): quem lista pendência lê o ÍNDICE, então índice velho é a casa em que o erro renasce*
      adr-context-read-audience-policy.md ← leitura de contexto com política por PLATEIA: as três peças de *"pegar o dado já filtrado"* existem em duas casas (`context_map` tipa a tag · `masking.types.*.mascara.by_role` declara a máscara por papel) e a terceira — **o leitor — JÁ EXISTE e é canônico**: `resolve_mask_for_audience` + `apply_masking_type_to_value`, usados por `_build_pending_preview` desde 2026-09-02. ⚠️ **O ADR nasceu apontando o eixo errado** (`display.echo_to_*`, que é ADVISORY e trata do eco da ENTRADA mascarada) e foi corrigido no mesmo dia (§D9). O que faltava mesmo era derivar a PLATEIA do sítio, e o gêmeo TS do resolvedor. A plateia é derivada do **SÍTIO** (`visibility` do notify/menu · argumento de invoke · prompt de reason), nunca da tag: dos 20 acertos, **10 são token em link legítimo** e 4 estão num skill que não fala com cliente. UM leitor que SUBSTITUI (segunda porta é o achado do `/sessions/{id}/stream`). ⚠️ NÃO decide o default de tag desconhecida (é a V4 da allowlist), NÃO absorve a detecção, e `$.pipeline_state.*` (225 pontos) é fase 2 via carimbo de proveniência. **A exceção vem ANTES da aplicação** — inverter quebra survey link e OTP. Fases F0–F5 — proposto
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
      adr-dialog-input-format-catalog.md ← campo de coleta: `format` NOMEIA uma entrada de catálogo (forma do `adr-masked-typed-declaration`) e **`pattern` SAI** — 0 usuários medidos, e removê-lo apaga por construção um fail-open (regex inválida libera tudo, mudo) além de tirar regex de tenant do event loop. O catálogo é **DADO** e cada superfície tem um interpretador (política em código vira o `evaluateAskWhen` triplicado, que é TOPOLOGIA e não desleixo); entrada de PII **referencia** `masking.types` em vez de repetir a máscara. ⚠️ `detect_pattern` é *finder*, **nunca** validador; e veredicto tem DOIS níveis — regex não recusa `31/02/2026`. Fases F0–F5, **F2 antes de F3** — proposto
      adr-deploy-time-content-snapshot.md ← conteúdo referenciado (DialogForm) resolvido no **PROMOTE** e congelado no snapshot do slot, nunca em runtime; promote OTIMISTA com `409`+diff. **A S1 original foi REFUTADA por medição; decisão do dono = pin de versão gravado pelo SERVIDOR** — proposto
      adr-skill-flow-editor-validation.md ← validação no editor de skill-flow: **AFORDÂNCIA ≠ VEREDICTO** — `zod-to-json-schema` não representa refinements, então JSON Schema serve para autocomplete e o veredicto vem do SERVIDOR (dry-run `POST /v1/skills/validate`, mesmo `validateSkillPayload()` do `PUT`). Fases F0–F4; F1 antes de F2 — proposto
      adr-dialog-tree-options.md     ← opções em ÁRVORE no DialogForm: a recursão entra em `DialogOption`, **nunca em `DialogNode`** — taxonomia é DOMÍNIO DE VALOR, não control-flow, então `nodes` segue plano e as seis superfícies mantêm o laço linear. Fases F0–F6; F0 antes de F5, F2 antes de F4 — proposto
      adr-orchestrator-tree-navigation.md ← navegação de ORQUESTRADOR como árvore de `DialogForm`: pasta navega, folha endereça SERVIÇO, e o runner genérico do dialog faz roteamento sem LLM e sem virar linguagem — o laço `menu → choice → menu` é ciclo que o engine JÁ sanciona (`menu` bloqueia em I/O). ⚠️ **A folha carrega o CAMINHO, NUNCA o pool**: form é congelado no promote × pool é DB-owned, então `pool_id` no snapshot seria segunda fonte de verdade de roteamento invisível de quem administra pools; o mapa mora na config do pool (precedente `mentionable_pools`). Medido em 2026-09-05: `agente_triagem_v2` gasta **15 steps** para uma tabela de 5 entradas; os menus são de DUAS espécies (navegação × coleta) e só a primeira sobe; e **o eixo de DEMANDA não tem produtor — 0 `agent_event` nos cinco skills de atendimento**, ou seja toda resposta de menu do cliente é hoje descartada. A árvore é o contrato COMUM ao orquestrador determinístico e ao com LLM (o LLM navega livre mas aterrissa em folha declarada ⇒ mesma série, escape leaf = *"não sei"* contável, roteador avaliável). Fases F0–F5, **F1 entrega o eixo de demanda sozinha, sem tocar no engine** — proposto
      adr-orchestrator-specialist-contract.md ← o orquestrador é DONO do contato, o especialista é EXECUTOR, e o contrato de contexto entre eles. Tese: o orquestrador **humano** já mantém `primary` enquanto especialistas entram como `specialist` e saem; o **IA** perde a titularidade no primeiro `escalate` — e é essa ASSIMETRIA, não a frequência de re-roteamento, que justifica o modelo. ⚠️ A frequência foi medida e PROÍBE o argumento fácil: 2+ pools de IA em **5 de 1 151** contatos — mas o `escalate` é terminal, então seria medir demanda por uma capacidade com dados gerados pela ausência dela. Medido: nenhum dos 5 especialistas é executor puro; **3 menus de DEMANDA dentro de folhas** (um ainda não observado); o contrato de contexto tem a ENTRADA pronta e ociosa (`1/41` declara `required_context`, `0/41` lê `@ctx.__gaps__` que o engine já computa) e a **SAÍDA inexistente**; `task` retorna mas endereça skill, contorna o slot de deploy e não acha 3 dos 5 especialistas; e a regra que proíbe `delegate` em agente **não tem mecanismo**. Decisões D1–D8, fases G0–G5 — **G1 (dar mecanismo à regra de perfil) vale sozinha e vem antes** — proposto
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
      adr-remove-agent-role-axis.md ← REMOÇÃO do terceiro eixo de papel: o gate de evaluator não autentica o chamador (o `session_token` carrega `instance_id` e as tools o DESCARTAM, consultando o papel do `participant_id` do INPUT) e o cenário de PII não fecha (o ReplayContext exige sessão fechada ∧ amostrada ∧ dentro do TTL de 1 h). O que ele separa é avaliador MAL CONFIGURADO — erro de deploy, não fronteira. `agent_role` sai junto, com `orchestrator` (zero portadores). ⚠️ Contém a alternativa REFUTADA (trocar o eixo) e a tabela dos DOIS gates de papel que autorizam pela string do input — **Aceito e IMPLEMENTADO em 2026-09-01 — R1, R2, R3 e a mitigação CAP-04**
      adr-agent-flow-single-authored-level.md ← o tenant autora **UM** nível (o processo); o que o modelo de 3 níveis chamava de N1/N2 vai para **três** destinos — mecânica → canal (adapter; cliente só onde existe, logo não em voz/WhatsApp/SMS) · roteiro → `DialogForm` · o resto **já era primitivo de plataforma** (`customer_resolve`, `pending_workflow_get`, `workflow_resume`, `otp_*`, `select_channel`). "Um nível" é um nível **autorado**: a segregação de perfil proíbe workflow de falar com o cliente, então do outro lado do `collect` fica o runner genérico da plataforma. ⚠️ **NÃO confundir com o modelo de escopo `segment`/`session`/`journey`** — "três níveis" nomeia dois modelos aqui, e este só dissolve o de FLUXO DE AGENTE. Fatias F1–F4 (`NIV-01..04`), nenhuma implementada — proposto
```


---

## Documentos transversais (raiz)

| Arquivo | Conteúdo |
|---|---|
| [visao-geral.md](visao-geral.md) | **Visão técnica consolidada** — percorre toda a plataforma com links para os docs detalhados |
| [modelos-de-dados.md](modelos-de-dados.md) | Schemas por camada de persistência + matriz de acesso por módulo |
| [kafka-eventos.md](kafka-eventos.md) | Tópicos Kafka, schemas de eventos, produtores e consumidores |
| [plughub_analise_competitiva_2026.md](plughub_analise_competitiva_2026.md) | Análise competitiva detalhada (abr/2026) |
| [revisao-documentacao-2026-05.md](revisao-documentacao-2026-05.md) | Relatório de avaliação do acervo de documentação |

---

## Produto (`product/`)

Documentação voltada ao público comercial, de gestão e a novos usuários.

| Arquivo | Conteúdo |
|---|---|
| [product/overview.md](product/overview.md) | O que é o PlugHub, proposta de valor, arquitetura em uma página |
| [product/target-audience.md](product/target-audience.md) | Perfis de público-alvo: gestores, supervisores, integradores, desenvolvedores |
| [product/value-proposition.md](product/value-proposition.md) | Diferenciais por papel, benefícios mensuráveis, casos de uso |
| [product/competitive-analysis.md](product/competitive-analysis.md) | Comparação com Gemini Enterprise, Agentforce, Genesys, NICE/Cognigy, Five9, Talkdesk, LangGraph, CrewAI, n8n |
| [product/plughub-descritivo-tecnico-funcional.md](product/plughub-descritivo-tecnico-funcional.md) | Descritivo técnico-funcional consolidado p/ avaliador técnico (+ `.html` print-ready). Inclui roadmap §20 |

### Propostas / roadmap em `product/` *(não implementado — ver `TODO.md`)*

| Arquivo | Conteúdo |
|---|---|
| [product/business-in-any-media-arquitetura-alvo.md](product/business-in-any-media-arquitetura-alvo.md) | Modelo de 3 níveis (a/b/c) + framework de loja (+ diagrama `business-in-any-media-3-niveis.svg`) |
| [product/identity-resolver-nivel-b-spec.md](product/identity-resolver-nivel-b-spec.md) | Resolvedor de identidade + cadastro de cliente (nível b) (+ `identity-resolver-sequencia.mermaid`) |
| [product/delegate-contrato-por-pool-spec.md](product/delegate-contrato-por-pool-spec.md) | Contrato de delegação por pool (a→b) |
| [product/commerce-cards-nivel-c-spec.md](product/commerce-cards-nivel-c-spec.md) | Vocabulário de commerce-cards renderizado por canal (nível c) |
| [product/intake-flow-nivel-c-spec.md](product/intake-flow-nivel-c-spec.md) | Fluxo de intake (nível c) |
| [product/routing-pull-dispatch-spec.md](product/routing-pull-dispatch-spec.md) | Dispatch pull genérico no Routing Engine |
| [product/human-work-queue-aprovacao-spec.md](product/human-work-queue-aprovacao-spec.md) | Fila de trabalho humano / aprovação no Console |
| [product/pull-inbox-console-ui-spec.md](product/pull-inbox-console-ui-spec.md) | UI da inbox de pull no Console |
| [product/record-replay-harness-spec.md](product/record-replay-harness-spec.md) | Record/Replay Harness (regressão determinística + gate de promoção) |

---

## Módulos funcionais (`modulos/`)

Um arquivo por módulo da UI. Cobre o que cada módulo faz, suas abas, gates de acesso ABAC, APIs envolvidas e os pacotes de backend que o sustentam.

### Atendimento

| Arquivo | Rota UI | Roles | Descrição |
|---|---|---|---|
| [modulos/contatos.md](modulos/contatos.md) | `/contacts` | operator+ | Lista de contatos, aba Agentes, Monitor em tempo real, Análise |
| [modulos/agent-assist.md](modulos/agent-assist.md) | `/agent-assist` | operator+ | Console do agente humano: chat, RightPanel, orquestração de agentes IA |

### Automação

| Arquivo | Rota UI | Roles | Descrição |
|---|---|---|---|
| [modulos/workflow.md](modulos/workflow.md) | `/workflow/*` | operator+ | Editor de workflows, Monitor de instâncias, Calendar, Report |
| [modulos/agentflow.md](modulos/agentflow.md) | `/agent-flow/*` | admin+ | Editor YAML de SkillFlows, Monitor, Deploy lifecycle, @mention, Pool Hooks |
| [modulos/processos.md](modulos/processos.md) | ~~removida 2026-08-28~~ | — | Jornadas (Arc 10/16) e Instâncias de workflow — monitoramento multi-sessão |

### Qualidade

| Arquivo | Rota UI | Roles | Descrição |
|---|---|---|---|
| [modulos/avaliacao.md](modulos/avaliacao.md) | `/evaluation/*` | operator+ | Formulários, campanhas, avaliação IA + RAG, contestação/revisão, calibração, curadoria |

### Configuração

| Arquivo | Rota UI | Roles | Descrição |
|---|---|---|---|
| [modulos/configuracao-recursos.md](modulos/configuracao-recursos.md) | `/config/resources` | admin | Pools, Agent Types, Skills, Instâncias, Canais, Agentes Humanos |
| [modulos/configuracao-plataforma.md](modulos/configuracao-plataforma.md) | `/config/platform` | admin | Namespaces de configuração via Config API |
| [modulos/mascaramento.md](modulos/mascaramento.md) | `/config/masking` | admin | Regras de mascaramento de dados sensíveis, audit capture, retenção |
| [modulos/controle-acesso.md](modulos/controle-acesso.md) | `/config/access` | admin | Usuários RBAC + ABAC, JWT, module_config |
| [modulos/grupos.md](modulos/grupos.md) | `/config/groups` | admin | Agent Groups, supervisores por turno, escopo de supervisor no JWT (Arc 9) |
| [modulos/dashboards.md](modulos/dashboards.md) | `/dashboards` | admin | DisplayTool registry, tipos de card, ENDPOINT_CATALOG, FilterBar |
| [modulos/faturamento.md](modulos/faturamento.md) | `/config/billing` | admin, business | Faturamento por capacidade: base + reserve pools |
| [modulos/relatorios-agentes.md](modulos/relatorios-agentes.md) | ~~removida 2026-08-28~~ | — | Disponibilidade e pausas de agentes humanos |

---

## Arcos de implementação (`arcos/`)

Documentação técnica detalhada por Arc ou componente: implementação, contratos internos, schemas de banco, eventos Kafka e decisões de design.

### Arcos de feature

| Arquivo | Conteúdo |
|---|---|
| [arcos/arc4-workflow.md](arcos/arc4-workflow.md) | Workflow, Calendar, Collect, Webhooks, Skill Deploy lifecycle |
| [arcos/arc5-segments.md](arcos/arc5-segments.md) | ContactSegment analytics, ClickHouse tables, endpoints de relatório |
| [arcos/arc6-evaluation.md](arcos/arc6-evaluation.md) | Quality Evaluation Platform (Forms, Campaigns, Contestação, RAG) |
| [arcos/arc6-phase2-observability.md](arcos/arc6-phase2-observability.md) | Observabilidade de mudanças e comparação por deploy epoch |
| [arcos/arc7-auth.md](arcos/arc7-auth.md) | Auth, RBAC, ABAC, performance routing, JWT |
| [arcos/arc8-agent-availability.md](arcos/arc8-agent-availability.md) | Disponibilidade e pausas de agentes humanos, pipeline ClickHouse |
| [arcos/arc9-agent-groups.md](arcos/arc9-agent-groups.md) | Agent Groups, Supervisor Scope, shift resolution, JWT claims |
| [arcos/arc10-journey.md](arcos/arc10-journey.md) | Journey multi-sessão, fases A–F, Kafka journey.events |
| [arcos/arc11-console-orchestration.md](arcos/arc11-console-orchestration.md) | Console como superfície de orquestração humana (fases A–D) |
| [arcos/arc11-phase2-console-redesign.md](arcos/arc11-phase2-console-redesign.md) | Redesign do Console (fases A–E) |
| [arcos/arc12-agent-business-events.md](arcos/arc12-agent-business-events.md) | Agent Business Events — tool `agent_event`, KPIs de negócio |
| [arcos/arc13-review-contestation.md](arcos/arc13-review-contestation.md) | Evaluation Review, Contestation & Calibration (fases A–H) |
| [arcos/arc14-posatt-independent-segments.md](arcos/arc14-posatt-independent-segments.md) | Segmentos independentes de pós-atendimento |
| [arcos/arc15-webrtc.md](arcos/arc15-webrtc.md) | Canal WebRTC (fases A–F) — ⚠️ **só a sinalização roda**; o SFU nunca foi provisionado (medido 2026-08-19). Reconstrução em [adr/adr-voice-media-plane.md](adr/adr-voice-media-plane.md) |
| [arcos/arc16-flow-orchestration.md](arcos/arc16-flow-orchestration.md) | Orquestração de processos em três camadas, channel capability negotiation |
| [arcos/audit-lgpd.md](arcos/audit-lgpd.md) | Audit LGPD — módulo ABAC `audit`, acesso DPO/compliance |

### Componentes e subsistemas

| Arquivo | Conteúdo |
|---|---|
| [arcos/instance-bootstrap.md](arcos/instance-bootstrap.md) | Reconciliação Kubernetes-style, RegistrySyncer, hot-reload |
| [arcos/platform-ui.md](arcos/platform-ui.md) | Frontend Architecture — design system, nav groups, ABAC, i18n |
| [arcos/ai-gateway.md](arcos/ai-gateway.md) | AI Gateway — multi-account rotation, AccountSelector, copilot |
| [arcos/usage-metering.md](arcos/usage-metering.md) | Usage Metering — dimensões, Redis quota, cycle reset |
| [arcos/pricing.md](arcos/pricing.md) | Pricing — faturamento por capacidade, base + reserve pools |
| [arcos/session-replayer.md](arcos/session-replayer.md) | Session Replayer — ensure-before-read, Hydrator, ReplayContext |
| [arcos/session-conference-lifecycle.md](arcos/session-conference-lifecycle.md) | Ciclo de vida de conferência — modelo de 3 camadas |
| [arcos/dashboard.md](arcos/dashboard.md) | Dashboard — DisplayTool registry, ENDPOINT_CATALOG, cards |
| [arcos/channel-gateway-multi-channel.md](arcos/channel-gateway-multi-channel.md) | Channel Gateway multi-canal — WhatsApp, SMS, Email implementados; **Voice é especificação, não roda** (medido 2026-08-19: `AttributeError` em runtime real). Reconstrução em [adr/adr-voice-media-plane.md](adr/adr-voice-media-plane.md) |
| [arcos/evaluation-agents.md](arcos/evaluation-agents.md) | Agentes de avaliação — design de fluxos |

### Propostas e relatórios pontuais

| Arquivo | Conteúdo |
|---|---|
| [arcos/dialer-compliance-invariants.md](arcos/dialer-compliance-invariants.md) | **Proposta** — invariantes do compliance guard de discador (não implementado) |
| [arcos/journey-analytics.md](arcos/journey-analytics.md) | **Obsoleto** — proposta analítica superada pelo modelo real do Arc 10 |
| [arcos/task-30-contacts-restructure.md](arcos/task-30-contacts-restructure.md) | **Histórico** — design de reestruturação de nav (Task #30) |
| [arcos/design-system-audit.md](arcos/design-system-audit.md) | Relatório pontual — auditoria do design system (2026-05-18) |
| [arcos/accessibility-audit.md](arcos/accessibility-audit.md) | Relatório pontual — auditoria de acessibilidade WCAG 2.1 AA (2026-05-18) |

---

## Pacotes técnicos (`pacotes/`)

Um arquivo por pacote do monorepo: funcionamento interno, contratos, persistência e eventos.

| Arquivo | Pacote | Runtime |
|---|---|---|
| [pacotes/schemas.md](pacotes/schemas.md) | `@plughub/schemas` | Node 20+ |
| [pacotes/sdk.md](pacotes/sdk.md) | `@plughub/sdk` + `plughub-sdk` (Python) | Node / Python |
| [pacotes/mcp-server-plughub.md](pacotes/mcp-server-plughub.md) | `mcp-server-plughub` | Node 20+ |
| [pacotes/skill-flow-engine.md](pacotes/skill-flow-engine.md) | `@plughub/skill-flow` | Node 20+ |
| [pacotes/ai-gateway.md](pacotes/ai-gateway.md) | `ai-gateway` | Python 3.11+ |
| [pacotes/agent-registry.md](pacotes/agent-registry.md) | `agent-registry` | Node 20+ |
| [pacotes/routing-engine.md](pacotes/routing-engine.md) | `routing-engine` | Python 3.11+ |
| [pacotes/rules-engine.md](pacotes/rules-engine.md) | `rules-engine` | Python 3.11+ |
| [pacotes/channel-gateway.md](pacotes/channel-gateway.md) | `channel-gateway` | Python 3.11+ |
| [pacotes/channel-gateway-webchat.md](pacotes/channel-gateway-webchat.md) | `channel-gateway` (WebChat) | Python 3.11+ |
| [pacotes/auth-api.md](pacotes/auth-api.md) | `auth-api` | Python 3.11+ |
| [pacotes/platform-ui.md](pacotes/platform-ui.md) | `platform-ui` | React 18 + TypeScript |
| [pacotes/evaluation-agent.md](pacotes/evaluation-agent.md) | `evaluation-agent` | Python 3.11+ |
| [pacotes/clickhouse-consumer.md](pacotes/clickhouse-consumer.md) | `analytics-api` consumer | Python 3.11+ |
| [pacotes/conversation-writer.md](pacotes/conversation-writer.md) | stream persister | Python 3.11+ |

> **Pacotes sem doc própria em `pacotes/`:** `calendar-api`, `workflow-api`, `skill-flow-worker`, `pricing-api`, `evaluation-api`, `mcp-server-knowledge`, `analytics-api`, `orchestrator-bridge`. Cobertos parcialmente pelos docs de Arc correspondentes (`arcos/arc4-workflow.md`, `arcos/arc6-evaluation.md`, `arcos/pricing.md`). Criar entradas formais é uma ação de remediação pendente.

---

## Guias temáticos (`guias/`)

Padrões e workflows que cruzam múltiplos pacotes.

| Arquivo | Assunto |
|---|---|
| [guias/context-store.md](guias/context-store.md) | ContextStore — Redis hash por sessão, `@ctx.*`, `context_tags`, namespaces |
| [guias/context-store-taxonomy.md](guias/context-store-taxonomy.md) | Taxonomia de namespaces de contexto |
| [guias/context-masking-rules.md](guias/context-masking-rules.md) | Mascaramento dinâmico do ContextStore (`ContextMaskingRule`) |
| [guias/masked-input.md](guias/masked-input.md) | Masked Input — captura segura: `begin_transaction`, `@masked.*` |
| [guias/mention-protocol.md](guias/mention-protocol.md) | @mention — endereçamento de participantes, `mentionable_pools` |
| [guias/conference-mechanics.md](guias/conference-mechanics.md) | Mecanismo de conferência — Redis keys, eventos, posatt, teardown |
| [guias/pool-hooks.md](guias/pool-hooks.md) | Pool Lifecycle Hooks — `on_human_start`, `on_human_end`, `post_human` |
| [guias/orchestrator-working-memory.md](guias/orchestrator-working-memory.md) | Working memory para orquestradores em loop |
| [guias/abac-permission-system.md](guias/abac-permission-system.md) | Sistema ABAC — `makePermissions()`, `modules.yaml`, scope por pool |
| [guias/gitagent.md](guias/gitagent.md) | GitAgent — artefatos, certificação, regeneração, deploy |
| [guias/webhook-patterns.md](guias/webhook-patterns.md) | Webhooks — padrões trigger e resume; comportamento do step `collect` |
| [guias/timeouts-e-deteccao-de-falhas.md](guias/timeouts-e-deteccao-de-falhas.md) | Timeouts, CrashDetector, heartbeat, TTLs por componente |
| [guias/conferencia-agente-ia-mapeamento.md](guias/conferencia-agente-ia-mapeamento.md) | **Obsoleto** — mapeamento de gaps já implementados |

---

## ADRs (`adr/`)

Decisões arquiteturais com contexto, opções consideradas e consequências.

| Arquivo | Decisão |
|---|---|
| [adr/adr-ai-gateway-separation.md](adr/adr-ai-gateway-separation.md) | Separação do AI Gateway como serviço stateless |
| [adr/adr-contact-segments.md](adr/adr-contact-segments.md) | ContactSegment como entidade analítica de participação |
| [adr/adr-instance-bootstrap.md](adr/adr-instance-bootstrap.md) | Instance Bootstrap — reconciliação controlada (Kubernetes-style) |
| [adr/adr-message-masking.md](adr/adr-message-masking.md) | Mascaramento de mensagens com tokenização e partial display |
| [adr/adr-session-replayer.md](adr/adr-session-replayer.md) | Session Replayer — ensure-before-read com Hydrator opcional |
| [adr/adr-webchat-channel.md](adr/adr-webchat-channel.md) | WebChat — hybrid stream model, WebSocket tipado, upload dois estágios |
| [adr/adr-cti-gateway-multi-driver.md](adr/adr-cti-gateway-multi-driver.md) | Telefonia legada por CTI — `cti-gateway` on-prem, N drivers sobre perfil reduzido de CSTA; PABX ancora, mídia nunca sai da LAN |
| [adr/adr-voice-media-plane.md](adr/adr-voice-media-plane.md) | Voz própria — terminação SIP, SFU, STT/TTS e gravação; a mídia acompanha o deploy da plataforma. Reconstrói o canal `voice`, que **não roda** |

---

## Camadas arquiteturais (`layers/`)

Mapeamento conceitual das 9 camadas da plataforma para os pacotes do monorepo.

| Arquivo | Camada | Pacotes |
|---|---|---|
| [layers/01-channel-layer.md](layers/01-channel-layer.md) | Channel Layer | `channel-gateway` |
| [layers/02-gateway-layer.md](layers/02-gateway-layer.md) | Gateway Layer | `channel-gateway`, `ai-gateway` |
| [layers/03-message-bus.md](layers/03-message-bus.md) | Message Bus | Kafka |
| [layers/04-orchestration-layer.md](layers/04-orchestration-layer.md) | Orchestration Layer | `routing-engine`, `rules-engine`, `skill-flow-engine` |
| [layers/05-agent-layer.md](layers/05-agent-layer.md) | Agent Layer | `sdk`, agentes externos |
| [layers/06-mcp-layer.md](layers/06-mcp-layer.md) | MCP Layer | `mcp-server-plughub`, `mcp-server-knowledge`, domain MCP Servers |
| [layers/07-data-layer.md](layers/07-data-layer.md) | Data Layer | Redis, PostgreSQL, ClickHouse, Object Storage |
| [layers/08-mlops-layer.md](layers/08-mlops-layer.md) | MLOps Layer | fora do repositório (Horizonte 1) |
| [layers/09-observability-layer.md](layers/09-observability-layer.md) | Observability Layer | ferramentas externas |

---

## Padrões de desenvolvimento (`standards/`)

| Arquivo | Conteúdo |
|---|---|
| [standards/frontend-architecture.md](standards/frontend-architecture.md) | Design system, módulos, componentes, anti-padrões, i18n, autenticação |

---

## Referência histórica (`sections/`)

Seções extraídas da especificação técnica v24.0 original. Mantidas apenas para consulta — a documentação viva está nas seções acima.

| Arquivo | Conteúdo |
|---|---|
| [sections/spec_completa.md](sections/spec_completa.md) | Spec técnica v24.0 completa em markdown |
| [sections/INDEX.md](sections/INDEX.md) | Índice das seções extraídas |
| [sections/conferencia-e-historico.md](sections/conferencia-e-historico.md) | Rascunho v25.0 — conferência unificada (superado por `guias/conference-mechanics.md`) |
| [sections/3.2-rules-engine.md](sections/3.2-rules-engine.md) | Seção 3.2 — Rules Engine |
| [sections/3.3-routing-engine.md](sections/3.3-routing-engine.md) | Seção 3.3 — Routing Engine |
| [sections/3.4-context-package.md](sections/3.4-context-package.md) | Seção 3.4 — Context Package |
| [sections/4.2-contrato-execucao.md](sections/4.2-contrato-execucao.md) | Seção 4.2 — Contrato de Execução |
| [sections/4.5-agent-registry.md](sections/4.5-agent-registry.md) | Seção 4.5 — Agent Registry |
| [sections/4.6-sdk.md](sections/4.6-sdk.md) | Seção 4.6 — SDK |
| [sections/4.7-skill-registry.md](sections/4.7-skill-registry.md) | Seção 4.7 — Skill Registry |
| [sections/9.4-agent-runtime-tools.md](sections/9.4-agent-runtime-tools.md) | Seção 9.4 — Agent Runtime Tools |
| [sections/9.5-a2a-protocol.md](sections/9.5-a2a-protocol.md) | Seção 9.5 — A2A Protocol |
| [sections/10-evaluation.md](sections/10-evaluation.md) | Seção 10 — Evaluation |
| [sections/14-multi-tenant.md](sections/14-multi-tenant.md) | Seção 14 — Multi-tenant |

---

## Deprecated (`deprecated/`)

Arquivos supersedidos mantidos apenas como referência histórica. Não consulte para implementação.

| Arquivo | Motivo de deprecação |
|---|---|
| [deprecated/modulos/agent-assist-piloto.md](deprecated/modulos/agent-assist-piloto.md) | Design do piloto — substituído pelo Agent Assist atual |
| [deprecated/modulos/dashboard-piloto.md](deprecated/modulos/dashboard-piloto.md) | Dashboard do piloto — substituído pelo módulo Dashboards atual |
| [deprecated/modulos/evaluation.md](deprecated/modulos/evaluation.md) | Stub inicial — substituído por [modulos/avaliacao.md](modulos/avaliacao.md) |
| [deprecated/standards/operator-console-migration.md](deprecated/standards/operator-console-migration.md) | Plano de migração — `operator-console` removido (migração concluída) |
| [deprecated/sections/visao_negocial.md](deprecated/sections/visao_negocial.md) | Substituído por [product/overview.md](product/overview.md) |
| [deprecated/sections/visao_negocial_v24.md](deprecated/sections/visao_negocial_v24.md) | Substituído por [product/](product/overview.md) |
| [pacotes/notification-agent.md](pacotes/notification-agent.md) | Pacote nunca implementado — `notify` depreciado no Arc 16 |
| [guias/changelog-2026-04-15.md](guias/changelog-2026-04-15.md) | Changelog histórico pré-`CHANGELOG.md` |
| [guias/changelog-2026-04-16.md](guias/changelog-2026-04-16.md) | Changelog histórico pré-`CHANGELOG.md` |
| [guias/changelog-2026-04-16b.md](guias/changelog-2026-04-16b.md) | Changelog histórico pré-`CHANGELOG.md` |
| [guias/changelog-2026-04-29.md](guias/changelog-2026-04-29.md) | Changelog histórico pré-`CHANGELOG.md` |
