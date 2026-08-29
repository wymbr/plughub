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
  TODO.md            ← itens genuinamente não implementados
  CHANGELOG.md       ← histórico de implementações concluídas
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
    adr/
      adr-message-masking.md  ← masking architecture decision
      adr-contextstore-allowlist.md ← ContextStore como ALLOWLIST: `default_unmatched_operator: "plain"` é **deny-nothing** (campo sem regra nasce em claro, e desde a F5 nasce DURÁVEL em claro), e a causa é estrutural — `session.*`/`journey.*` não podem ter catch-all (o seed avisa: derruba a tela de aprovação em silêncio) e é justamente ali que o `delegate.context` deposita. **TIPO é a declaração única** (formato × máscara-por-papel × classe LGPD; qualquer uma pode ser vazia) — funde as três METADES medidas: `MaskingRule`+regex tem detecção sem papel/canal, `MaskingDisplayRule` tem canal (`display_screen`×`display_voice`) sem papel, `ContextMaskingType` tem papel sem detecção/canal. **MAPA em `escopo.dominio.campo`** = a allowlist; o escopo FICA no primeiro segmento porque o prefixo hoje roteia hash+TTL em 3 casas (`sdk/context-store.ts:106-120` · `interpolate.ts:237` · `journey.ts:180`) e mover isso p/ config criaria roteamento de RETENÇÃO DE PII que degrada mudo. Legado vira `alias` no próprio nó, resolvido na BORDA (nenhuma regra escrita contra alias — seria "regra que não regra"), **contado e datado**. **QUATRO políticas, um vocabulário** (W escrita · R-agente · R-humano · P persistência) — fundi-las é erro (o portão de namespace na persistência apagaria história). **Pré-requisito inegociável: a omissão deixa de ser MUDA antes da inversão** (`continue` em `server.ts:1163`/`:1179`), senão troca vazamento de PII por quebra muda de UI. Achados que o ADR carrega: a 2ª porta — o tool `supervisor_state` devolvia o ctx **CRU** — **fechada na V1b** (política em `lib/context-masking.ts`, uma casa para as duas portas; tool entrega em **grau operator sem portão de namespace**, porque não há visualizador com PAPEL e o pool que ele tem à mão é o de ENTRADA) · ~~3~~ **SETE inventários de categoria discordavam** — a contagem herdada era por MENÇÃO e perdeu os três com produtor vivo em Python, que eram justamente os que podiam divergir sem teste de TS notar; medidos lado a lado, **nenhuma das 5 linhas era unânime** · **12 `HSET` diretos** no ctx sem choke point. Fases V0 (tela que mente) → V1 (contar, não omitir — vale sozinha) → V1b (2ª porta) → V2 (tipos) → **V2b (fechar a casa legada `rule.{category}`)** → V3 (mapa+alias+modo AUDITORIA) → **V4 inverter (não reversível)** → V5 (seletor no pool). Ordem inegociável: V1 antes de V4. **V0/V1/V1b/V2/V2b entregues (ver CHANGELOG); a V3 é a próxima.** A V2 fechou a metade aberta da V0 (catálogo único, `DEFAULT_MASKING_RULES` derivada dele, fantasmas fora, selo derivado) e derrubou dois defeitos alheios: o leitor de regras de canal apontava para rota inexistente (**inerte desde sempre**, 404 engolido por `.catch(() => {})`) e o `\(?` do regex de telefone era **ramo morto** (`\b` nunca casa antes de `(`). **A V2b é pré-requisito do `masked` TIPADO** — enquanto a dimensão CANAL tivesse casa própria, *"que máscara este campo usa?"* teria duas respostas e valeria a mais permissiva (o leitor legado **vencia** o catálogo). Três medições que regem o método: **(1)** a premissa herdada *"a tela ainda escreve o legado"* era **falsa** — as linhas citadas eram a declaração da função e duas LEITURAS; **(2)** o caso real não era nenhum dos dois ramos previstos, e sim **zero escritores + zero dados + 4 leitores**, o menor deles; **(3)** quem autorizou remover foi o **contador** que a V2 deixou publicando *"a remoção é MEDIDA por este número zerar"*, não uma decisão nova. Achado: o leitor legado **não era peso morto** — `getMaskingRule` o devolvia vencendo o catálogo e o `update()` da tela o gravava de volta NO catálogo, promovendo legado a tipo em silêncio (armadilha ARMADA, blast radius zero por ausência de dado). Gate `probe_legacy_display_rule_closed.sh` (fonte · oráculo · config viva · store inteiro), vermelho antes de verde; a **exclusão de linha de comentário é load-bearing e não higiene** — depois do conserto **3 linhas ainda casam a regex e as 3 são a prosa que documenta a remoção** — **Aceito, parcialmente implementado** (V0 metade · V1 · V1b · V2 · V2b; V4 não iniciada)
      adr-masked-typed-declaration.md ← `masked` deixa de ser BOOLEANO e passa a nomear um TIPO do catálogo. O booleano é declaração **anônima** — diz *"esconda"* e não diz **o quê** —, então máscara-por-papel, regra de canal e classe LGPD não têm onde morar e acabam decididas **por formulário**; e conformidade por formulário significa N políticas de CPF no tenant, valendo a mais permissiva. Consumidor direto do catálogo da V2; a **V2b foi pré-requisito** (enquanto a dimensão canal tivesse casa própria, a pergunta teria duas respostas). **D1: `true` é um TIPO (`opaque`, máxima restrição), não a ausência de um** — manter um ramo "mascarado porém sem tipo" reintroduziria o default permissivo como AUSÊNCIA, que é o valor mais barato de produzir e o mais difícil de contar. **D4: o tipo decide EXIBIÇÃO e CLASSE, nunca PERSISTÊNCIA** — *"masked nunca entra em `pipeline_state`"* segue absoluto, porque tipo que opta por persistir derruba a invariante **editando config**, sem revisão de deploy. **D3: duas guardas, ambas fail-closed com posturas opostas** — deploy RECUSA tipo desconhecido, runtime resolve para `opaque` e LOGA nomeando (recusar em runtime derrubaria atendimento em curso por erro de config). **D5: a DETECÇÃO fica fora** — declaração sabe ANTES do valor existir, alcança o valor inteiro e suprime na origem; detecção só sabe DEPOIS, alcança um trecho e tokeniza post-hoc; o que compartilham é o TIPO, e a detecção já o lê desde a V2. Achados que sustentam as fases: **(1)** quatro declarações em **2×2** (`skill.ts:476,503` × `dialog.ts:314,258`) mas **um** resolvedor (`masking-policy.ts`) e **um** normalizador (`form_get` achata dialog→`render.fields[].masked`) ⇒ a tipagem muda declaração+normalizador, não a cauda; **(2)** a submissão de form ENTRA na transcrição durável e o campo **não-declarado vai em CLARO** — medido: `email` em claro ao lado de `senha`/`codigo_2fa` redigidos, com o catálogo tendo `email_addr`/`lgpd=pessoal`; **(3)** `DialogFormRenderer.tsx` ignora `masked` (zero ocorrências) — armadilha ARMADA, **dano hoje 0** (1 de 10 forms declara masked, e não é dos que chegam ao Console); **(4)** parque de **6 declarações**, medido na AUTORIDADE com três vias concordando (arquivo 5 = `skills.flow` 5 = 2 de 37 slots; `flow_draft` zero). Fases T0 (contar no agent-registry, nunca no arquivo) → T1 (`opaque`) → T2 (união + resolvedor único) → T3 (redator consome o tipo) → T4 (4ª superfície) → T5 (guardas) → T6 (migrar) → **T7 fechar o ramo `boolean` (não reversível)**. Ordens inegociáveis: T1 antes de T2 · **T4 antes de a tipagem alcançar o Console** · T6 antes de T7 · **S1 do ADR de snapshot antes de T2** (as duas mexem no mesmo bloco `render`: aditiva antes de mutativa). D8: o fechamento é por **CONTADOR**, nunca por decreto — mesmo mecanismo que autorizou a V2b — proposto
      adr-mcp-interception-single-border.md ← borda única de interceptação MCP: veredicto no mcp-server (3 bordas → 1), proxy externo vira mapeador de vocabulário, `McpInterceptor` fica como caminho de portabilidade; requisito T = domain server inalcançável a partir do agente (borda é rede, não código); fases M0(medir)/B1(pool c/ health-check)/B2(mcpCall nativo)/B3(assimetrias)/T — proposto
      adr-webchat-channel.md  ← webchat channel architecture
      adr-session-replayer.md ← session replayer architecture
      adr-contact-segments.md ← Arc 5 architecture
      adr-instance-bootstrap.md
      adr-evaluation-sampling.md ← amostragem: cota por agente (virada para estado) + carimbo de versão
      adr-quality-substrate-isolation.md ← isolamento do substrato de avaliação por `origin` (híbrido; implementado ✅)
      adr-survey-form-scoring-composition.md ← composição de nota em survey (dimension+perguntas ponderadas; primitivo `scoring.ts` compartilhado c/ Quality) — proposto
      adr-dialog-conditional-skip-logic.md ← skip-logic condicional em DialogForm (guarda declarativa `ask_when`, **não** control-flow) — **Aceito + implementado 2026-07-08**, validado ao vivo no webchat *(corrigido 2026-08-17: este índice dizia "proposto" por mais de um mês)*. **Guarda LOAD-BEARING**, e a razão mudou sem enfraquecer *(2026-08-18)*: a versão anterior dizia "com o editor de fluxo local saindo (interop n8n)" — o editor **fica**, e a pressão para empurrar control-flow ao form **existe do mesmo jeito**, agora vinda do lado oposto (enquanto o editor de fluxo próprio for insuficiente, o formulário é o caminho de menor resistência). Se ceder, o editor de fluxo é reconstruído dentro do editor de formulário, com linguagem pior. Avaliador canônico `evaluateAskWhen` em `schemas/src/dialog.ts:423`, **hoje triplicado** (espelhos em `survey_web.py:386` e `DialogFormRenderer.tsx:400`). Aberta só 1 das 3 decisões do ADR (`checklist` multi-valor)
      adr-dialog-form-deletion.md ← `DELETE` de DialogForm: **arquivar** (reversível) e não apagar. A medição separou dois eixos que "soft-delete" funde: ARMAZENAMENTO (*"dá para recuperar?"*) × **LEITURA** (*"o contato em andamento cai?"*) — soft-delete com `404` na resolução quebraria igual ao hard delete. **D1: o catálogo fecha, `GET /{form_id}` continua servindo** (com `deleted_at`), porque ninguém DESCOBRE form por id: quem chama já tem vínculo. São **SEIS** leitores, todos por `?status=published`; **dois leem no FIM** do diálogo (`survey_record`, `segment_outcome_record` ⇒ a janela de risco vai até o submit, não até o `carregar_form`) e **um lê história encerrada** (`WebhookSegmentDetail`, dano sem janela). **D2: purga real só do nunca-publicado** — é a única parte DECIDÍVEL de "recusar quando há referência viva" (o `form_id` literal mora dentro do flow do snapshot do slot, então checagem cross-service seria incompleta por construção), com aviso de irreversibilidade na tela. D3 `409` em escrita sobre arquivado + `undelete` próprio (ressuscitar implícito faria slot antigo executar conteúdo novo sem ninguém tocar no deploy) · D4 recusa só de quem cria vínculo NOVO (`survey_link_create`) · D5 delete é do `form_id`, nunca da versão (despublicar é outra operação) · D6 `RECONCILE` limpa `deleted_at`, log diz *arquivado* · D7 a tela diz **arquivar**. Achado de tabela: `seed_dialog.published_version()` trata `404` como AUSENTE ⇒ com leitura fechada, **todo boot ressuscitaria o form apagado**. Emenda medida na UI: **`ever_published` não é derivável do `status` da lista** (última versão pode ser rascunho com uma publicada mais antiga), então a lista o carrega e o botão de arquivar fica DESABILITADO sem ele — supor o caso reversível é o palpite confortável e o errado num ato irreversível. Fases F1–F5 e gate `probe_dialog_form_delete.sh` (9 falhas antes do build → verde depois) — **Aceito + implementado 2026-08-28**
      adr-deploy-time-content-snapshot.md ← conteúdo referenciado (DialogForm) resolvido no **PROMOTE**, congelado no snapshot do slot — não em runtime. **Não é padrão novo: a base já decidiu assim duas vezes** (o bridge roda o `yaml_snapshot` do slot, não `skill.flow`; e `survey_link_create` já snapshota o form no token). Resolve TRÊS problemas que vinham fundidos: **(1)** a corrida das duas leituras — e resolve por **REMOÇÃO**, não sincronização: `form_get` já normaliza `captures` no `render`, então o skill passa `$.pipeline_state.dialog.render.captures` e `segment_outcome_record` para de buscar o form ⇒ **supersede a F0b/D9 do ADR da árvore**; **(2)** *"vi X, subiu Y"* — o form é editado noutra tela, por outra pessoa, noutra cadência, e **não há indicador de defasagem** para ele (há para o flow: `_isStale`); conserto = **promote OTIMISTA**, a tela declara as versões que exibiu e diverge com `409`+diff, porque aviso que ninguém lê é a família do *"using default values"*; **(3)** dispersão de autoria ⇒ o editor RESOLVE a referência e mostra in loco. **Injetar na EDIÇÃO foi recusado** (D4): troca risco visível por silencioso — a cópia envelhece sem sinal (modo de falha do `flattenBlocks`) e **velho é pior que diferente**; mais o bloat de uma taxonomia de 5 níveis dentro do YAML, que é MENOS legível. `version` opcional na referência = **pin × float** explícito por caso. **NÃO resolve** (D7): o snapshot congela o que EXECUTA, não como o histórico se LÊ — a imutabilidade do `id` segue invariante independente. Efeito colateral: **afrouxa o ADR de deleção** (arquivar deixa de poder quebrar deploy em execução). Emenda registrada: o argumento *"reuso entre canais exige arquivo separado"* **caiu** — quem serve N canais é a normalização `render`, que funcionaria inline; a referência compra (a) superfícies sem skill rodando, (b) editar texto sem re-deploy, (c) o mesmo form em N pools — **só a (c) obriga**. Fases S1 (captures no render, mata a corrida sozinha) · S2 (`version`) · S3 (resolução no promote) · S4 (conflito) · S5 (afordância) · S6. Ordem: S1 antes de S3; S4 nunca antes da S5 — proposto
      adr-skill-flow-editor-validation.md ← feedback de validação no editor de skill-flow: **AFORDÂNCIA ≠ VEREDICTO**. A proposta original ("JSON Schema no Monaco, uma dep e um passo de build") foi refutada nos DOIS eixos. **Custo:** o Monaco vem de **CDN via AMD loader** (`@monaco-editor/loader`), `vite.config.ts` não tem config nenhuma para ele, `monaco-yaml` não existe — e é ele, não o Monaco, que aplica schema a YAML (`jsonDefaults` não atua em `language="yaml"`) ⇒ é **migração de bundling**, não `npm i`. **Papel (o que decide o ADR):** `SkillSchema` é `ZodEffects` e `SkillFlowSchema` tem dois `.refine` (`skill.ts:1318-1324` — `entry` existente, tem `complete|escalate`), e **`zod-to-json-schema` não representa refinements** ⇒ o JSON Schema diria VÁLIDO para flow que o servidor recusa: verde local contradizendo o verde que importa. Logo JSON Schema serve para **afordância** (autocomplete/hover) e nunca para **veredicto** — fundir os dois dá duas respostas para *"isto é válido?"*, e a mais permissiva vence. **Decisão: veredicto pelo SERVIDOR, via dry-run `POST /v1/skills/validate`**, com `PUT` e dry-run chamando o **mesmo** `validateSkillPayload()` extraído (D3). Achados: **(1)** o erro de bloco masked é **invisível** hoje — servidor devolve `details` (plural, `skills.ts:226`) e o front lê `detail` (singular, `SkillFlowsPage.tsx:608`) ⇒ vira "HTTP 422" mudo; **(2)** o `PUT` **não** detecta ciclo não-guardado (a checagem existe em `engine.ts:270` mas só roda na execução) ⇒ flow com ciclo salva verde e explode depois; **(3)** existem DUAS `validateFlow`, e a boa para UI é a do `sdk/certify/flow.ts:91` (devolve resultado estruturado; a do engine lança string); **(4)** `onMount` descarta o argumento `monaco` (`:846`) ⇒ não há handle para `setModelMarkers`. Pré-requisito medido de D3: rodar o verificador sobre os **42 YAMLs** e contar reprovações antes de ligar. Fora de escopo e registrado: refs `$.`/`@ctx.` (exigiria inferência de `pipeline_state` por ponto do grafo) e o **desacoplamento `platform-ui × @plughub/schemas`** — que redefine `Skill`/`Pool`/`AgentType`/`Session` à mão em `types/index.ts` (~960 linhas) mais dezenas de cópias locais divergentes, **sem justificativa escrita em lugar nenhum**, e cujo risco de dual-instance de Zod já está documentado em `agent-registry/src/app.ts:58-61`. Fases F0 (handle + erro visível) · F1 (verificador único) · F2 (dry-run + painel, degradação ALTA) · F3 (markers — decide `js-yaml` × `yaml`, que tem offsets) · F4 (afordância, arco de bundling). Ordem inegociável: F1 antes de F2 — proposto
      adr-dialog-tree-options.md ← opções em ÁRVORE no DialogForm (taxonomia de wrap-up). **A recursão entra em `DialogOption`, nunca em `DialogNode`** — taxonomia é DOMÍNIO DE VALOR, não control-flow (`Financeiro > Cobrança > indevida` é UMA resposta, não decide o que vem depois), então `nodes` segue plano e as SEIS superfícies mantêm o laço linear. Pasta × arquivo com seletividade **derivada** (selecionável ⟺ sem `options`); profundidade 5; nesting só sob `list`/`checklist` (sob `button`/`form` é erro de schema, nunca render parcial); multi-seleção **dentro de uma pasta** ⇒ prefixo comum vira invariante CONFERÍVEL; resposta = caminho de **`id`s** (label nunca entra na série); obrigatoriedade derivada do nesting + folha de escape `nao_se_aplica` (que é um *arquivo na raiz*) — `required` burlado grava NULL, indistinguível de "não perguntamos"; árvore INLINE e versionada; `ask_when` ganha `prefix`, o que **fecha a decisão em aberto #3** do ADR de skip-logic. Quatro achados que sustentam as fases: **(1)** o form é lido DUAS VEZES (renderer no claim × `segment.ts:325` no submit, com `timeout_s:-1` no meio) e nada as amarra ⇒ pin de versão; **(2)** `flattenBlocks` reconstrói `capture` e perde `kind` (`dialog-blocks.ts:120,136`), que `deriveAgentEvents` exige (`segment.ts:110`) — armadilha ARMADA, blast radius zero hoje (só fixture a declara); **(3)** o ramo `multi-select ⇒ N eventos` é **CÓDIGO MORTO** — os 3 renderizadores tratam `checklist` como escalar e o bridge faz `json.dumps` na lista (`main.py:9116-9126`) ⇒ hoje sairia **uma** categoria-lixo `_a_b_`; **(4)** `AGENT_EVENT_CATEGORY_REGEX` aceita 2–5 segmentos e a profundidade decidida daria 8 ⇒ **bloqueio**, e `decomposeCategoryLevels` só extrai 4 (5º segmento hoje é gravado e silenciosamente invisível). Versionamento é **uso, não build** (PK inclui `version`, publish só promove, `?version=N` existe). Adjacentes registrados e fora de escopo: sem `GET /{form_id}/versions`, e **rollback não faz rollback**. Fases F0 (lossless) · F0b (pin) · F1 (schema) · F2 (multi de verdade) · F3 (Miller + recusa alta) · F4 (categoria de caminho) · F5 (editor) · F6. Ordem inegociável: F0 antes de F5, F2 antes de F4 — proposto
      adr-outbound-survey-as-collect-contact.md ← survey web outbound = contato via `collect` (canal survey/web), membro N1 da journey; sinal solto vira legado/anônimo (Journey J4c) — proposto
      adr-customer-360-two-surfaces.md ← Cliente 360 (Console 4 abas × Analytics): Contexto/Histórico(jornadas em aberto)/Cliente(cadastro manual+360 quality/survey)/Ações; jornadas = filtro `customer_id` no `/reports/journeys`; cadastro v1 reusa Resolvedor Fase A/B (merge=Fase C) — proposto
      adr-human-approval-workflow-step.md ← Aprovação humana = passo de workflow (collect/delegate a pool, dispatch_mode config); conteúdo=DialogForm (reuso), aprovador=agente logado (Modo A), Console/inbox responsivo, retorno→choice; omnichannel adiado (canal-agnóstico); fases A1–A6 — proposto (fechado)
      adr-wrapup-detached-pull.md ← Camada E2: wrap-up humano destacado = item de pull `assigned_to`. **Decisão: Path α, renderer-first** — o renderer é o **tratamento genérico de collect-form no Console** (não "renderer de aprovação"): renderiza o DialogForm de qualquer collect/delegate reivindicado + submit via `workflow_resume`; serve aprovação+wrap-up+survey-no-Console SEM skill por caso (§2.1). β (skill agente menu) **não viável no pull-standalone** (humano vira primário, sem IA p/ renderizar). Comuns: `assigned_to` (E2c), `acw_pending` (E2e, produtor pendente da Camada C), sessão de wrap-up fora da contagem (E2f), DialogForm (E2a). Kickoff do núcleo genérico: `docs/product/approval-renderer-kickoff.md` — proposto
      adr-work-item-requeue-and-agent-affinity.md ← devolução de item à fila, posse e afinidade (D1–D8). Achado: um F5 no Console (WS ~2 s) é tratado como abandono → `agent_done` + re-route genérico → item volta ao ZSET com a vaga ainda ocupada (**duplicação**), e o re-publish de 6 campos apaga `assigned_to`/`conference_id`/`work_item_deadline`/`auto_attend`. **D6 emendada ao implementar**: posse NÃO cabe no ledger `work_task` (`assigned_to` é reserva, vazio em item pooled) — é registro durável do ÁRBITRO (`{t}:pool:{p}:claim_record:{sid}`, TTL do prazo do item). **Fases A ✅ + B ✅ + C ✅ + D ✅ 2026-08-04** (A: posse conferida no submit contra registro durável, 4 ramos, 403 em item na fila — provado na UI; B: bridge pergunta a POSSE ao árbitro e devolve por `work_task_release` — `conference_id`/`work_item_deadline` preservados no F5 real; C: a queda devolve RESERVADO ao dono anterior via Camada B, janela por tipo em config-api ns `routing` (`-int` 300 s / demais 30 s), com o botão "Return to queue" NÃO reservando — default seguro. `first_queued_ms` saiu do escopo: é chave própria com NX, já escrita). D: a tela deriva posse do CLAIM — guarda no mcp-server sobre `pool:pending_assignment` (o replay não era do pub/sub), veredicto puro `shouldDropOnPossession` com os mesmos 4 ramos do submit e do drop; fecha a duplicação VISUAL). **E ✅ 2026-08-04**: mapas de `close_reason` separados por DOMÍNIO — `_TRANSPORT_TO_SEGMENT_CLOSE_REASON` só com os transportes em que o CONTATO não fecha (`agent_disconnect`, `agent_transfer` — e, desde 2026-08-05, `agent_release_item`, a devolução deliberada à fila: ver CHANGELOG e `conference-mechanics.md` § Mudança 32), consultado antes do de contato, e só no fim de segmento pelo lado do agente (fecha a lacuna 6: 14 de 31 segmentos humanos saíam mudos, 12 deles em pools de pull cujo item nunca foi entregue, contra 0/9 na fila interna, que carimba pelo submit). A queda publica **`agent_released`**, não `agent_done` — o routing trata igual para devolver a vaga (`keep_slot_for_wrapup` forçado a false: numa queda não há herdeiro para o hold). **Duas emendas medidas:** nada analítico lê `agent_done` (`analytics-api/models.py` o mapeia a `None` desde 2026-07-28) — logo a contaminação de contagem/AHT/bancada vem dos SEGMENTOS, não do evento; e **suprimir** o evento no ramo de item de trabalho seria regressão, porque `remove_conversation` também restaura a membership dos SETs do pool, que o `work_task_release` não faz. **F ✅ 2026-08-04 — ARCO A–F COMPLETO**: resume terminal-uma-vez. O achado que mudou a fase é que **não era corrida da fila pull, e sim da RETOMADA** — os três gatilhos (submit, supervisor, prazo) entram pela MESMA `handle_resume`, e o hash `resume_tokens` é escrito por `suspend`/`delegate`/`collect`, logo toda workflow suspensa já tem dois retomadores possíveis (o pretendido e o scanner, que roda no MESMO event loop do endpoint HTTP — a corrida não precisa de réplicas). Mecanismo: `SET NX` no topo, solto no `finally`; o `HDEL` **fica no fim** (é ele que preserva a retentabilidade e faz o 403 do A5 não consumir o item — subi-lo trocaria a corrida por item irresumível). Registro terminal `{t}:resume_terminal:{token}` gravado ANTES do consumo dá NOME à recusa: token ausente com registro → **409**, sem registro → 404 honesto. Fecha o caso `expire→submit`, que devolvia *"token não encontrado ou expirado"* ao agente cujo item o supervisor acabara de encerrar. `work_task_expire` segue idempotente de propósito (é o árbitro; o errado era ser a única defesa). **Consequência aceita:** o lock dá unicidade, não prioridade — entrega pode perder para prazo numa janela de segundos; o conserto, se preciso, é um sinal de "em preenchimento" para o scanner, não o lock. **F2 ✅ 2026-08-04**: o Console LÊ o 409 (`lib/resume-conflict.ts`, parser que DESCE por `detail` — o corpo chega aninhado DUAS vezes no caminho do supervisor: FastAPI embrulha, mcp-server repassa sob `expire_failed`). Assimetria que rege o desenho: o supervisor **nunca** vê `terminal` (resume bem-sucedido apaga o ledger `work_task` → 404 `no_work_task` antes do gateway), e o `in_flight` que sobra a ele traz `session_id`/`cause`/`closed_at` **VAZIOS** — por isso **sentença** (do consumidor: o agente perde respostas, o supervisor não perde nada) e **linha de fatos** (compartilhada, omitindo campo ausente) são separadas; concatenar daria *"encerrado por agent () em "*. No agente, `terminal` desliga o Submit; `in_flight` não
      adr-historico-unificado-duas-visoes.md ← `/analise/sessions` + `/analise/processos` colapsam num módulo: **visão 1** (contatos não relacionados, filtro de contato) × **visão 2** (processo). **Processo é PIVÔ, nunca navegação livre** — lista de processos só escopada por atributo de contato (`customer_id`, `open`), e é isso que mantém o filtro sempre no nível de contato (senão "filtrar por pool" devolve *journeys que tocaram o pool*). Processo aparece como CHIP na linha de contato (conta o processo inteiro, não a fatia filtrada — exige rótulo). **Duas classes de linha**: acesso do cliente (direção + par entrada→saída) × etapa interna (maquinaria, dobrada). Segmento é a FOLHA (sem transcript fundido cross-contato ⇒ sem ADR de masking). Com `started_at` na linha, **árvore e cronologia viram um componente com toggle de ordenação**; faixas-por-personagem = destino (faixa = IDENTIDADE, não segmento). Direção do acesso DERIVADA de `spawn_reason` (NULL=inbound · `collect`=outbound · `trigger`/`delegate`=interno). **"Recebeu a saída" nunca se infere de `visibility='all'`** — mente no parking, que existe justamente porque o cliente não está lá. **Estado 2026-08-25: F0 ✅ F1 ✅ F1b ✅ F2 ✅ F3 ✅ F4 ✅ (as duas visões na tela) — resta a F5
(`ContextStorePersister`, fase própria) e a lente C (destino registrado).** A F4 trouxe um achado
que não era dela: **a direção do acesso estava prestes a existir em duas casas** (derivada em TS
para a coluna, e em SQL para o filtro novo). Virou UMA expressão, usada como coluna e como
predicado na mesma query — divergir entre *"o que a linha diz"* e *"o que o filtro devolve"*
deixou de ser possível. Gate `infra/test/probe_f4_direction_and_classes.sh` (vermelho→verde).
**F0 antes da UI** *(feito)*: `handle_collect` não honrava `customer_resumable`/`resume_policy` (gate assimétrico vs os dois handlers de delegate; registrado em `skill_limite_entrega_v1.yaml:41-42`) — fechá-lo dá output-com-confirmação, perna-como-sessão, direção outbound e pertença por PROVENIÊNCIA, dispensando `journey_merge` para o output ativo. Achados medidos: `ani`/`dnis` vazios em 314 sessões · `sessions.pool_id` é o ÚLTIMO pool (filtro por pool já mente) · `/reports/segments` trunca em silêncio (janela sempre aplicada) · Audit LGPD documentado e AUSENTE — proposto
      adr-a2a-server-binding.md ← PlugHub como **servidor** A2A: binding de borda sobre pool+sessão, sem motor novo. `Task`=sessão (`taskId`=`session_id`, `contextId`=`root_session_id` — não criar contêiner, é o erro da `WorkflowInstance`/`Journey` pela 3ª vez); AgentCard = PROJEÇÃO do agent-registry (`version`=`set_at` do slot ⇒ contrato externo versiona junto com o deploy); A2A é **binding, não `channel`** (canal é filtro de roteamento; quem chamou é fato de CREDENCIAL) ⇒ zero diff no routing. Net-new real **não é o protocolo, é o ARTEFATO**: o caminho webhook nasceu fire-and-forget (trigger devolve só `{session_id}`) e `get_status` responde `"closed"` quando a chave não existe — "não sei" indistinguível de "terminou". Principal externo = `a2a_client` no auth-api (token por endpoint ≠ caller com N pools), `tenant_id` **nunca do corpo** (hoje vem), masking sem opção, cota `a2a_tasks`. Fases A0 descritor → A1 card read-only → **A2 principal (bloqueia A4)** → A3 artefato+status honesto → A4 JSON-RPC → A5 SSE → A6 validação. FORA: pool humano (fase 2, será `webchat`), PlugHub como CLIENTE (seria `invoke`, nunca pool — inventaria capacidade de recurso alheio) — proposto
      adr-cti-gateway-multi-driver.md ← telefonia legada como canal: serviço on-prem `cti-gateway` com N drivers (CSTA-IPO 1º) sobre **modelo canônico = perfil reduzido de CSTA** (ancestral de TSAPI/JTAPI e nativo em Unify/Alcatel/Mitel ⇒ tradução tem destino, não esperanto). **O PABX é o ÂNCORA, o CTI é o EFETUADOR, nunca o árbitro** — 3 destinos p/ a mesma chamada ancorada (perna de mídia · ramal · conferência); *estacionar é estado de MÍDIA, não lógico* (ou a central segura não-atendida, ou a perna da plataforma atendeu — nunca os dois), e é isso que acopla "quem enfileira" a "quem faz a URA". **Capability declarada por driver, recusa alto, nunca emulação muda** (`transfer` atômico × consulta+completa atrás do mesmo método = valor plausível que falha como chamada perdida); verificada no boot contra o switch. Identidade da chamada é do GATEWAY (transfer/conference trocam o id nativo) por componente conexa sob aliases — **reusa o padrão `root_session_id`+union-find**, não inventa o 3º mecanismo; `attach_call_data` (UUI) é a via preferencial quando existe. Ramal = **2 fatos com escopos diferentes**: elegibilidade→`auth.users`, alocado→hash da instância; **monitor ⟺ alocação** (é o que segura o teto de sinalização do IPO, ordem de grandeza abaixo do parque). Transferência: fila = re-publish inbound (caminho que já existe) · agente = `assigned_to` (pull direcionado) ⇒ invariante "pool é a unidade endereçável" intacto. **Nenhum driver na matriz sem TRAÇO GRAVADO** (Record/Replay vira infra do arco, não backlog). Canal PRÓPRIO `pbx`, e o `voice` (Twilio) **não** é consertado aqui — medido: `handle_inbound` chama 5 métodos inexistentes, mockados no teste, `AttributeError` em runtime real. Fases F0 (núcleo+IPO, mídia toda na central, entrega os 6 requisitos) → **F1 driver estruturalmente diferente, obrigatória antes de qualquer outro** → F2 demanda. **§0 emendado 2026-08-19 — a fronteira é ATENDIMENTO × TELEFONIA INTERNA, não controle × mídia**, e caem DOIS MODOS que não são fases um do outro: *modo CTI* (PABX ancora, mídia nunca sai da LAN, sem IA na voz, matriz de drivers) × *modo SIP* (plataforma ancora, PABX vira interno, tronco INVERTE de direção e só carrega handoff). Achado que reposicionou: no modo SIP a parte Avaya é **um tronco e um `REFER`** — o resto não tem relação com PABX nenhum, logo é arco de VOZ PRÓPRIA, não integração (classificá-lo como "integração IPO" escondia o custo *e* o valor). SIP é **mais** portável que CTI (todo PABX manda SIP ⇒ zero matriz), mas depende de plano de mídia que está em ZERO; CTI é mais complexo e não depende de nada — "mais simples" ≠ "caminho mais curto até algo de pé". Emendas: D1 vale só no modo CTI; D6 vira **capability de mídia VAZIA** em `pbx` (skill com TTS não roda, e o registry reprova); D8 escopado ao CTI; D9 desdobra "para ramal" (CTI = contato CONTINUA · SIP = contato SAI, `REFER`); F2-mídia saiu p/ o outro ADR — proposto
      adr-voice-media-plane.md ← arco de VOZ PRÓPRIA (modo SIP): terminação SIP + SFU + STT/TTS + perna do agente + gravação, **independente de PABX**. Consolida TRÊS dívidas que sozinhas não se justificam: canal `voice` que **não roda** (5 métodos inexistentes em `voice.py:236`, mockados no teste ⇒ `AttributeError` real), Arc 15 **placebo** (zero LiveKit em compose, zero env, SDK fora do `pyproject`), discador bloqueado por falta de mídia. **V1: o plano de mídia NÃO tem topologia própria — acompanha o deploy da plataforma** (⇒ elimina SFU SaaS; on-prem = sem WAN e sem SBC de graça; nuvem = SBC é do produto). **V6: `_dev_mode` SAI — sem credencial o provider RECUSA**, porque token bem-formado e falso é o valor plausível mais caro (foi o que deixou o Arc 15 parecer pronto por meses); mock é escolha declarada, nunca inferida de credencial vazia. V2 reconstrói `voice` (já está no `Literal`), Twilio rebaixado a **um** `IVoiceProvider` (tronco CPaaS); `webrtc` segue canal à parte (lá o CLIENTE é browser); perna do AGENTE nunca é canal. V4 preserva "IA é sempre texto" — bot leg é o ÚNICO ponto áudio↔texto. **V5: gravação no AttachmentStore, sem store próprio — mas retenção vira política POR CLASSE** (o ciclo atual, soft-expire horário, apagaria gravação) e há **conflito doc×doc a arbitrar: 5 anos × 30 dias LGPD**. V10: **borda SIP é superfície nova, fora da allowlist HTTP** (o probe só conhece prefixo). Fases V-F0 infra de pé (fase própria, primeira) → V-F1 perna SIP entrante → V-F2 bot leg STT/TTS (conserta o `collect` morto) → V-F3 gravação → V-F4 egress+supervisão → V-F5 validação c/ instalação limpa — proposto
      adr-relatorios-duas-superficies-e-lentes.md ← relatórios colapsam em DUAS superfícies (Contatos=demanda × Recursos=oferta) com nível (`journey>session>segment`) × lente em aba × modo. **A mesa de comparação é MODO, não página** (difere de "evoluir" em UMA dimensão: série por entidade × série pela população). **Lente vira DECLARAÇÃO com três campos** — `aggregation` · `emptiness` (vazio ≠ zero) · **`comparability`** (o campo que a mesa descobriu e resolveu UMA vez, inline, na guarda cross-form de `quality`, e que falta em `quality_criteria`); hoje são 4 campos e uma cascata de 11 `if`, com lista de exceção nomeando 5 lentes e um booleano `deployLens` porque a lente `deploy` troca o TIPO DE ENTIDADE. **Token: o produtor ESTAVA morto e foi construído** (T0→T2) — o diagnóstico original (`emit_llm_tokens` só de `POST /inference`, rota sem chamador) valia, e a T0 mediu que **42% das chamadas vêm do `sentiment_analyzer`**, que não tem rota própria: o emissor foi para o site que fala com o provider, não para o handler. Hoje os 4 caminhos vivos publicam com `source` obrigatório, e `segment_id`/conta/modelo são COLUNAS, a partir de época declarada (`usage_attribution.USAGE_ATTRIBUTION_EPOCH`). Chave de atribuição = **`segment_id`**, não `pool_id` (segmento→pool é total, a inversa não; e o pool da SESSÃO é o de ENTRADA ⇒ especialista IA seria creditado ao pool errado); conta é a **EFETIVA** com identidade dupla (`config_id` sobrevive à rotação de chave, `key_id` não) e ausência `null` nomeada. **Recursos por contato são DOIS números** (`distinct instance_id` = custo × `count(segment_id)` = trocas de mão) mais o pico simultâneo por varredura de intervalos — a sobreposição que torna `Σ duration` inválido É a métrica aqui. Inventário medido: **10 páginas órfãs na árvore** (a contagem inicial de 2 só media a área de relatórios; a F0 removeu 5 arquivos — `AnaliseComparacaoPage` + `MetricSelector`, seu único consumidor, `AgentReportsPage`, `AnaliseAgentesPage`, `ProcessosPage` — e deixou **8 declaradas como dívida que não pode crescer**, 5 delas de relatório com rota já `Navigate` desde o Arc 19 ⇒ F0b), 1 endereço duplicado, 3 rotas fora do menu — patologia já diagnosticada em `routes.tsx:41-47` e **recorrida duas vezes**, logo a lista de morte é a TABELA DE UM GATE (`probe_report_surface.sh`, molde do `probe_edge_surface.sh`), não documentação. **Estado 2026-08-29: ARCO COMPLETO — F0 ✅ F0b ✅ F1 ✅ F2 ✅ F3 ✅ F4 ✅ T0–T3 ✅.** `/analise/` saiu de 10 endereços para 6, com TRÊS superfícies de mesma gramática (filtro × nível × lente × modo): **Contatos** (demanda, `/analise/sessions`) · **Recursos** (oferta, `/analise/resources`) · **Voz do Cliente** (sinal, `/analise/customer-voice`). **A F4** absorveu `/analise/surveys` como o NÍVEL de respostas da Voz do Cliente — e a palavra "drill" da D7 teve de estreitar para *nível*: o agregado lê `session_signal` (ClickHouse) e a lista lê `survey_response` (PG), então prometer que uma linha explica um ponto seria afirmar identidade entre duas populações. **Não é defeito** (medido: os dois produtores são persist-first, e para todo sinal REAL existe a resposta — 48=48, 3=3; a divergência de 130×48 é `seed_volume_demo.sh` escrevendo `vol_%` direto no CH). Lições da F4: (a) **vocabulário hardcodado oferecia o que o backend não serve** — `pmf × segment`, um grão `workflow` inexistente, e um default `journey` que abria a página VAZIA com 130 sinais na base; tudo passou a vir do catálogo; (b) **absorver não pode rebaixar** — a lista tinha multi-pool e o agregado aceitava um só, então o endpoint passou a aceitar `?pool_id=` repetido nas DUAS metades (série + overlay de SLA; filtrar só uma compararia populações diferentes no mesmo eixo); (c) o gate `evaluation.report` da entrada de menu veio junto, e ao trazê-lo mediu-se que **ele nunca foi fronteira** — `/v1/evaluation/survey/responses` não o confere, apesar de o docstring afirmar que sim (exposição real, dano zero: os 4 de 6 que alcançam sem grant são o admin e três fixtures; dívida no `TODO.md`). A T3 entregou a lente de token da superfície A (série por SESSÃO = quanto o contato custou × breakdown por SEGMENTO = quem gastou, de qual conta, com qual modelo; trocar os dois joins não fica vermelho). **A F3 entregou a Superfície B em `/analise/resources`**: `/analise/pools` e `/analise/agents` viraram redirects — as quatro sub-abas são as lentes do modo EVOLUIR, a mesa é o modo COMPARAR (D6), e a forma do gráfico saiu da cascata de dez `if` para o campo `chart` do contrato, com `switch` exaustivo. **Lições que ficam:** (a) a metade B do token **não podia reusar o endpoint da A** — aquele faz `INNER JOIN` com as sessões filtradas, e medido são **945 de 1 991 tokens**, ou seja 47% publicados em silêncio; virou rota própria (`/reports/resources/tokens`) que **recusa `?pool_id=` com 422** em vez de ignorá-lo; (b) **partição de namespace declarada em prosa não é partição** — superfície e mesa escreviam ambas `?mode=`, e trocar de lente apagava o modo; (c) `REPORT_LENSES.filter(l => l.entity === 'contact')` **colapsa os literais em `string`**, e com isso o `assertNever` que o comentário dizia impedir lente nova não impedia nada (conserto: predicado de tipo); (d) a época de atribuição **mentiu pela terceira vez** — granularidade de DIA contra corte de INSTANTE faz as sessões de verificação da própria T1 parecerem defeito vivo; ficou DECLARADA como teto, não consertada, porque o único instante disponível seria escolhido olhando os dados. Achado que não era da fase: a mesa exibia **seis botões escritos `bench.lens.list`, `bench.lens.volume`, …** (a chave crua) desde que a F2 acrescentou as lentes de contato à declaração. A F2 entregou a Superfície A com cinco lentes sobre o MESMO predicado da lista (`_session_conditions`, uma expressão e dois consumidores) e trouxe um achado que não era dela: **o filtro de canal da lista de contatos nunca funcionou** — subconsulta correlacionada que o CH 23.8 recusa, `except` do wrapper devolvendo `data_unavailable`, endpoint respondendo 200 com zero linha. O seletor não filtrava, ele ESVAZIAVA (medido: 398 sessões `webchat`, filtro devolvia 0), e 683 testes não notavam. O contrato de lente ganhou `source` e `honors` por exigência de mecanismo, não de simetria.
```

### Como adicionar uma nova feature

1. **Feature pequena** (< 20 linhas): inline na seção H2 existente mais próxima.
2. **Feature média** (20–50 linhas): subseção `###` dentro da seção H2 mais próxima.
3. **Feature grande** (> 50 linhas): criar `docs/arcos/{nome}.md`; adicionar resumo de 15–20 linhas aqui.
4. **Fase pendente concluída**: mover do `## Pending` para `CHANGELOG.md`; atualizar `TODO.md`; **nunca deixar ✅ aqui**.

### Regra de persistência de planejamento

| Tipo de decisão | Onde registrar imediatamente |
|---|---|
| Nova tarefa planejada | Task no tracker (`TaskCreate`) |
| Decisão técnica (> 3 linhas) | Entrada em `TODO.md` com raciocínio |
| Invariante ou regra arquitetural | Seção neste arquivo |
| Implementação concluída | `CHANGELOG.md` |

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

```
plughub/
  CLAUDE.md                      ← this file
  plughub_spec_v1.docx           ← full architectural specification
  packages/
    schemas/                     ← @plughub/schemas — Zod contracts
    py-authz/                    ← plughub-authz — verificador CANÔNICO de JWT+ABAC (Python)
    sdk/                         ← @plughub/sdk — TypeScript + Python
    mcp-server-plughub/          ← Agent Runtime and BPM tools
    skill-flow-engine/           ← Skill Flow interpreter
    ai-gateway/                  ← LLM calls and context extraction (Python)
    agent-registry/              ← CRUD for AgentType, Pool, Skill, GatewayConfig
    routing-engine/              ← Agent allocation and queue management
    rules-engine/                ← Post-routing event evaluation
    channel-gateway/             ← Channel adapters and inbound normalisation
    calendar-api/                ← Calendar engine + CRUD REST (Arc 4) — port 3700
    scheduler-api/               ← Agenda/scheduler: fire a pool via webhook at a time — port 3650
    workflow-api/                ← Workflow instance lifecycle (Arc 4) — port 3800
    skill-flow-worker/           ← Kafka consumer, runs SkillFlow for workflow instances
    pricing-api/                 ← Capacity-based billing, invoice — port 3900
    auth-api/                    ← Auth, JWT, ABAC — port 3200
    evaluation-api/              ← Quality evaluation platform (Arc 6) — port 3400
    quality-ingest/              ← Pluggable contact-history reader (R13a) — port 3850
    quality-export/              ← Internal history → re-evaluation (R13d) — port 3852
    mcp-server-knowledge/        ← Vector knowledge base for RAG agents
    platform-ui/                 ← All operator-facing UI (React + Vite)
```

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
- Never allow AI agents to emit `@mention` commands — only `role: primary` or `role: human`
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
> **EXIGIR CREDENCIAL e RECORTAR LINHA são dois fatos** — e só o primeiro está fechado. As
> `query_*` que servem as doze de `reports.py` não aceitam `accessible_pools`: não é
> argumento esquecido, é filtro que não existe. Inventá-lo por rota seria escolher qual
> coluna é "o pool desta agregação", e o precedente está medido (F2: um filtro de canal que
> não filtrava, **esvaziava**). Exceção deliberada: `/sessions/active` recorta, porque lá o
> chamador **nomeia** o pool e o teste é de pertinência, não de coluna. O resto é dívida
> contada no `TODO.md`, nunca "por enquanto".

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

**@mention**: only `role: primary` or `role: human` may issue mentions. Domain closed by `mentionable_pools` pool config. `mention_commands` YAML declares actions: `set_context`, `trigger_step`, `terminate_self`.

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
> `/templates`, `/modules`, `module-config`), o **corpo** (`roles`/`accessible_pools`/`unrestricted`
> num `POST`/`PATCH /users`, cuja porta é `config.users`), o **alvo** (editar/apagar quem *detém*
> `config.permissions`) e o **escopo** (`POST`/`DELETE /v1/groups/{id}/supervisors`). A porta do alvo
> existe por causa da **senha**: resetá-la é campo de PESSOA e tem de seguir permitido, então quem
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
> razão. E **NÃO existe porta larga**, nem sequer o claim `unrestricted`:
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

## Arc 11 — Console como Superfície de Orquestração ✅

O Console é uma **superfície de orquestração**: o operador humano dirige, delega e monitora agentes AI como coparticipantes de primeira classe (AI e humanos simétricos no modelo de sessão). Funcionalidades: cartões de participantes AI em tempo real (step/status do Skill-Flow); "Adicionar Especialista" (invoca pools de `mentionable_pools` via A2A `assist`); "Delegar Tarefa" (seleção de mensagens → drawer instrução+visibilidade → card de resultado no `agent_done`); Tab de Orquestração (steps do Skill-Flow + intervenções de supervisor). **Permissões**: operar = `agent_assist.operacao`; intervir = role `supervisor` + scope ABAC.

→ See [`docs/arcos/arc11-console-orchestration.md`](docs/arcos/arc11-console-orchestration.md)

---

## Arc 6 Fase 2 — Observabilidade por Deploy ✅ (diário+markers P3 + epoch/versão R15a/R15b + cobertura 1b)

> **Status (completo, 2026-06-24):** lente `deploy` no board de Agentes (`/reports/agents/compare?lens=deploy`,
> decisão D3), **ancorada no POOL** (spec §11), com **dois modos** via `&mode=daily|epoch` (toggle Diário↔
> Por versão na UI): **diário+markers** (1º corte §6) e **epoch/versão** (§4.1/D4 — eixo X = versões). O epoch
> faz `JOIN evaluation_finalized.segment_id→segments` (carimbo `deploy_version` do R9, sem denormalizar),
> `GROUP BY pool/skill/deploy_version`, ordem `deployed_at` (fallback `first_seen`), `min_sample=30`, multi-pool
> = uma curva por pool (união por deployed_at). **Micro-fatia 1b ✅**: overlay de **nota provisória** (linha
> tracejada) + **pendentes de fechamento** por versão, da evaluation-api (`GET /v1/evaluation/reports/deploy-coverage`
> via `coverage_client`, degradação graciosa). Detalhe em `docs/arcos/arc-evaluation-metrics-methodology.md` §IV.8.

**Âncora = POOL** (par `(pool, skill)` colapsado enquanto 1 skill por pool): `skill_id` é estável (deploy não
muda o id; `version` é campo à parte; deploy é pool-centric via `PoolSkillSlot`+`SkillDeployment.pool_ids`), e
**um skill pode rodar em vários pools** → âncora-skill misturaria pools. Curva por pool; um deploy compartilhado
vira o mesmo marcador em cada curva de pool atingida.

**Implementado (P2 + P3):**
- **agent-registry**: `GET /v1/skills/:id/deployments` (P2-A) + `GET /v1/pools/:id/deployments` (P3-A — deploys
  onde pool ∈ `pool_ids`). Header `x-tenant-id`.
- **analytics-api**: config `agent_registry_url`; `deployments_client` (`fetch_skill_deployments` +
  `fetch_pool_deployments`, cache `(kind,tenant,id)` 60s, degradação → `[]`, D1). Lente `deploy` em
  `query_agents_compare` (`_COMPARE_LENSES`, domain `ai`): `_compare_deploy_lens` lê `avg(final_score)` de
  `evaluation_finalized` (Oficial, D2) **agrupado por `attr.pool_id`** (curva por pool); `_fetch_deploy_markers`
  usa a timeline do pool, cada marker com `pool_id`+`skill_id`+`version_label`; `meta.min_sample=30`.
  *(Cuidado CH: `any(attr.agent_type)`, não constante `'ai'` — alias colide com o `WHERE attr.agent_type` e a
  query falha.)*
- **platform-ui** `AgentsBenchPage`/`DeployChart`: na lente, entidades = **pools** (checkbox do pool → `pool_id`,
  cor própria; agentes desabilitados; μ oculto); `include_average=false`. Leitura honesta: eixo diário completo,
  **bolinha = dia com avaliação** + **reta** (`linear`) entre medições (sem zero/interpolação em dia sem amostra);
  **deploy = triângulo** na cor do pool sobre a curva, **versão/skill no tooltip** (`<title>` em `ReferenceDot
  shape`) + **contador** "N deploys" (não cresce com a qtd); flag N<min; estado-vazio "selecione um pool".
  Cleanup T16: `TimeseriesView`/`ComparisonView` mortas removidas (`MetricSelector` foi mantido então por causa da `AnaliseComparacaoPage`; medido em 2026-08-28, essa página estava ÓRFÃ e as duas caíram juntas na F0 do ADR de relatórios — o consumidor era
  `AnaliseComparacaoPage`).
- Demo: analytics-api ganhou `PLUGHUB_AGENT_REGISTRY_URL`. Seed `infra/test/seed_deploy_lens_demo.sh`
  (usa `flow_id == skill_id` p/ alinhar). Testes: `test_deployments_client.py` (9) + `test_deploy_lens.py` (5).

**Limitações registradas:** `ReferenceDot`/eixo categórico só rende se o dia do deploy é categoria (o front
injeta); deploy posterior à última avaliação fica no fim da curva (sem dados pós-deploy ainda).

→ See [`docs/arcos/arc6-phase2-observability.md`](docs/arcos/arc6-phase2-observability.md),
[`docs/product/arc6-phase2-deploy-observability-spec.md`](docs/product/arc6-phase2-deploy-observability-spec.md)

---

## Arc 12 — Agent Business Events ✅

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

## Arc 13 — Evaluation Review, Contestation & Calibration ✅

Dois fluxos por tipo de agente avaliado. **Humano**: revisor AI pré-publicação (gate por campanha) → contestação por dimensão → human reviewer decide (`ContestationThread` append-only; `max_rounds` via `ContestationPolicy`). **AI**: `evaluation_finalized` imediato + curadoria amostral por regras configuráveis; revisor AI gera `calibration_signal` → `CalibrationNote` no knowledge namespace → feedback ao avaliador via RAG. **Invariante**: `evaluation_finalized` é a única fonte de truth para relatórios de qualidade. Topic `calibration.events` + `GET /reports/evaluator-calibration` (Calibration Dashboard, correlaciona com deploy epochs do Arc 6 Fase 2).

→ See [`docs/arcos/arc13-review-contestation.md`](docs/arcos/arc13-review-contestation.md)

---

## Métricas de Avaliação & Metodologia ⚠️ design fechado — R1/R5/R6/R7a/R8a–R8e/R9–R12 ✅ (R8 completo); R7b/R7c fora de escopo (LGPD); R13a–c/R14/R15a–b/R16 PENDENTE

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

## Quality Ingest — leitor de histórico plugável (R13a–R13d) ✅ arco completo

Módulo anti-corrupção que faz históricos **externos** (CCaaS) e a **reavaliação interna** entrarem no
MESMO pipeline de avaliação (sampling → ReplayContext → avaliador → analytics), sem o importador tocar a
infra interna. **Interface = stream de eventos** `ingestion_event_v1` (não lote); **pool é a unidade**
(eventos carimbam `pool_id`, não `campaign_id`); tier-2 de IA indisponível p/ externo (grau-transcript).

`packages/quality-ingest/` (Python FastAPI, porta 3850, **produtor puro**) expõe `POST /v1/ingest/events`
(header `X-Tenant-ID`), roda masking net-pass, deriva `session_id`/`segment_id` determinísticos
(idempotência), e **mapeia 1:1** o stream → eventos canônicos internos que os consumers já entendem:
`conversations.events` (contact_open/message_sent/contact_closed), `conversations.participants` (campo
`type` underscore), `agent.lifecycle` `agent_done`, e `conversations.session_closed` (dispara sampling).
Toda emissão leva `source:"external_import"` (gate do consumer Y; nunca `channel_gateway`).
Schemas em `@plughub/schemas/ingestion-event.ts` (R13a-1). **Consumer Y ✅ (R13b)**:
`ImportStreamConsumer` (session-replayer) reconstrói `session_stream_events` (PG) dos eventos canônicos
gated `source=external_import`, via o `StreamPersister.insert_records`/`recompute_deltas` (mesmo escritor do
Persister vivo, sem drift) → Hydrator/Replayer dão um ReplayContext.events igual ao interno. **Mapa por
source ✅ (R13c)**: namespace `quality_ingest.source_map` (Config API); o `SourceMapClient` resolve e o
mapper traduz ext→int (pool, humano→`user_id`, IA→`skill_id`+`deploy_version`) **antes** de emitir
(pass-through se não mapeado). **Exportador interno ✅ (R13d)**: `packages/quality-export/` (ClickHouse-only,
porta 3852) lê `sessions`+`segments`+`messages` (`FINAL`) e re-emite `ingestion_event_v1` pela mesma porta
do quality-ingest (inverso do mapper) — `external_contact_id`=session_id original → novo session_id de
reavaliação. Reusa o pool original; pool dedicado sai do `source_map` (R13c) sem código novo.

→ See [`docs/arcos/quality-ingest.md`](docs/arcos/quality-ingest.md)

---

## Arc 15 — Canal WebRTC com SFU (LiveKit) ⚠️ código ✅ · SFU NÃO PROVISIONADO

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

## Dialog Primitive — Scripted-Dialog Runner (survey + OTP) — Fatia 1 + 2b ✅

Primitivo de "interação scriptada delegada" compartilhado por survey e OTP (ADR
`docs/adr/adr-otp-workflow-and-dialog-primitive.md`). **Quatro costuras inegociáveis:** conteúdo (DialogForm
JSON) × controle (skill/workflow chamador) × canal (runner Tier-3) × **segredo** (`OtpService`). O código do OTP
**nunca** passa pela mão de um agente/runner — gerar/enviar/verificar ficam no serviço confiável; o runner só
carrega o que o **cliente** digitou (vale p/ survey: resposta é do cliente, não fabricada — integridade do dado).

**DialogForm** (`@plughub/schemas/dialog.ts`): script **linear** de nodes `statement` (→ notify) e `question`
(→ menu), versionado (draft/published), i18n embutido (`LocalizedText = string | {locale: texto}`), `capture`
(binding declarativo de métrica p/ survey) e `validation` (formato). **Sem `next` condicional** — branching é do
skill, nunca no JSON (senão vira linguagem em JSON). Store canônico: **`dialog-api`** (porta 3760, schema
`dialog.forms`, CRUD + publish). Tool MCP **`form_get`** resolve o form publicado + normaliza num bloco `render`
single-turn (menu_prompt / fields / statement_after / captures).

**dialog-runner** (`skill_dialog_runner_v1`, pool `dialog_runner`, perfil `agent`): invocado via `delegate()`
(roda como conference specialist na sessão do chamador). v1 = N statements + **1 turno de coleta**, render
**nativo single-question** (§17.4): usa a **interação da pergunta** (text→input, button/list→botões — o webchat
já renderiza). **Contrato uniforme:** devolve `payload = { value: <escalar> }`; o domínio lê
`$.pipeline_state.<delegate>.value` e faz verify/record (não unificar — vira `if` gigante). Binding as-built do
`form_id` = **contexto de delegate** (`@ctx.session.dialog_form_id`).

**DOIS veículos (achado 2b):** o runner-especialista serve chamadores que **podem suspender** (OTP intake,
survey reconnect). **Hooks de `on_contact_end` NÃO podem delegar** — delegar suspende o hook agent e o bridge
trata `suspended` como hook concluído → fecha o contato antes de renderizar. Logo o NPS **ativo** (`agente_nps_v1`)
consome o primitivo de **conteúdo INLINE** (`form_get` + menu dinâmico), sem delegate/suspend. Ambos veículos
compartilham `DialogForm` + `form_get` + menu dinâmico; só divergem em suspender-ou-não.

**Engine (extensões):** §17.3 — `$.config.*` (slot config_json → runtime; **plumbing bridge→slot ✅ 2026-07-08** —
`config_params` declarativo no skill + UI de deploy + bridge injeta `PoolSkillSlot.config_json` no `/execute`; skill
parametrizado por deploy, ex. `skill_survey_multi_v1` usa `$.config.form_id`) +
`menu.options/fields` união `array | ref`. §17.4 — `menu.interaction` e `menu.visibility` união `enum|array | ref`
(`$.`/`@ctx.`), resolvidas por `resolveDynamicValue` em `menu.ts`; `form_get` expõe o render nativo. **Invariante
de build:** o `MenuStepSchema` mudou → **todo serviço TS que valida skills (`agent-registry`) + o engine
(`skill-flow-service`) + `mcp-server` devem ser rebuildados** junto, senão o `agent-registry` rejeita o ref (422).

**Consumidores:** OTP (Fatia 1) · **NPS ativo de fim-de-contato** (`agente_nps_v1`,
hook `on_contact_end`, **inline**, form `dialog_nps_buttons` botões 0-10 customer-only). Delegate de nível único
(aninhar no collector = colisão de `session.delegate_resume_token`, rejeitado). *(A "survey NPS reconnect"
delegate — `agente_survey_reconnect_v1`/`skill_survey_v1` — foi **aposentada na Camada E1**, arco detach de
hooks; a coleta assíncrona vive no J4c collect.)*

**Provisionamento ✅ (2026-08-07):** fonte declarativa `infra/dialog/*.json` aplicada no boot pelo serviço
**`dialog-seed`** (`infra/seed/seed_dialog.py`), via API oficial e **seed-if-absent** (form publicado → DB
vence; `DIALOG_SEED_RECONCILE=true` → arquivo vence). Antes disso os forms só existiam em scripts ad-hoc de
`infra/test/`, e **base nova subia sem nenhum**: NPS de fim-de-contato caía no `on_failure` do `form_get`
(contato fecha sem pesquisa) e o wrap-up abria o painel VAZIO (`404 → setForm(null)`). Editar um JSON de
`infra/dialog/` é **no-op** onde o form já está publicado — mesma pegadinha do YAML de skill.

**Editor ✅:** `/config/dialog-forms` (platform-ui, grupo Configuração) — cria/edita/publica DialogForms via
`dialog-api` (proxy `/v1/dialog`). Fecha a dívida "form = dado do tenant, UI-editável". **Multi-locale ✅**
(LocaleBar + `setLt`/`ltToStr` sobre `LocalizedText`; string pura = só o `default_locale`; indicador
"sem tradução" por nó). Refinamentos de UX/completude → `TODO.md` § "Revisão do editor de diálogos".

**Loop ✅:** step `loop` (N perguntas sequenciais em canal pobre) — `dialog_survey_multi_v1` +
`skill_survey_multi_v1` (pool `survey_multi_ia`). Item atual em path fixo (sem índice variável), contador tipo
`receive`, guardado pelo `menu` do body.

**Veículo web ✅:** página pública `GET /survey/{token}` (channel-gateway, `survey_web.py`) renderiza o **mesmo**
`DialogForm` como `<form>` e grava via `session.signals` (mesma trilha do `survey_record`). Snapshot do form no
`create` (token Redis). **Três superfícies, um conteúdo:** chat (runner) · inline (hook) · página web. Entrega
real do link (SMS/e-mail) = trilha à parte.

**Retry por formato ✅:** `MenuStep` ganhou `validation`+`retry` (união objeto|ref); o step `menu` faz reprompt
na mesma superfície em falha de FORMATO (numeric/pattern/faixa/comprimento), honra `max_attempts`, esgota→
`on_failure`. Só escalar; timeout/desconexão/@mention não são retry. Semântica (código OTP) segue no chamador.

**Fatia 2 (pendente):** `channel_policy: elect`; ~~plumbing `$.config` bridge→slot~~ (✅ 2026-07-08); timeout dinâmico do runner;
multi-locale + preview no editor; entrega real do link web (provedor SMS/e-mail). *(Vazamento de instância do
delegate→suspend do OTP resolvido 2026-07-07 — ver CHANGELOG + `docs/arcos/session-conference-lifecycle.md`.)*

→ See [`docs/product/dialog-primitive-and-runner-design.md`](docs/product/dialog-primitive-and-runner-design.md),
[`docs/adr/adr-otp-workflow-and-dialog-primitive.md`](docs/adr/adr-otp-workflow-and-dialog-primitive.md)

---

## Scheduler / Agenda — `scheduler-api` (Fase 1 ✅ · Fase 2 ✅ · Fase 3 ✅)

Serviço `scheduler-api` (porta 3650). Uma **Agenda** é um recurso **domain-agnostic** que, num *quando/modo*
(1x / recorrente daily-weekly-monthly, `times[]` no dia), **aciona um POOL via webhook** (Arc 19,
`POST /v1/channels/webhook/pool/{id}`) — nunca um skill (invariante S4). Duas camadas: **Camada 1** (Redis
sorted-set `scheduler:timers` + poller único 15s + re-hidratação no boot); **Camada 2** (Postgres schema
`scheduler`: `agendas` + `agenda_dispatches`, fonte de verdade). Invariantes: o scheduler **não** reimplementa
o "quando" — `business_day_policy` consulta o **calendar-api** (endpoints by-calendar_id `is-open-calendar`/
`next-open-slot-calendar`; o engine segue a única autoridade); **status da agenda = "acionou o pool ou não"**,
execução é da sessão (ref `session_id` no ledger, drill-through, nunca espelhada); `dispatched` = gateway criou
sessão (admissão/capacidade aparecem no ciclo da sessão); sem retry no v1 (`failed` gravado + Monitor).
Recorrência calcula só a **próxima** ocorrência e re-arma no disparo; `once`/exhausted → `completed`.
**Fase 2 ✅ (promote agendado):** o corpo do job = pool webhook `deploy_promote_ia` (skill `skill_deploy_promote_v1`,
perfil workflow) que faz `invoke pool_promote` lendo o pool-alvo do **payload da agenda** (`@ctx.target_pool`;
payload = `{ target_pool, action }`). `pool_promote` (tool em `mcp-server-plughub/tools/deploy.ts`) é o wrapper
auditado do **único** caminho de promote (`POST /v1/pools/:id/promote`); **não-2xx (409 `next` vazio / 422
capacidade) → `isError` → `on_failure`** (o 409 não some, promoção nenhuma em silêncio). Endereça **pool**, nunca
skill/versão (S4; **sem pin**). A falha do promote vive no **ciclo da sessão** (drill-through), não no
`AgendaDispatch` (a gateway devolve 201+session_id). Gate `infra/test/smoke_scheduled_promote.sh`.
**Fase 3 ✅ (UI + fire-now):** `POST /v1/agendas/{id}/fire` (`Dispatcher.fire_manual` — disparo imediato sem
consumir a recorrência; cancelada→409). platform-ui **`/config/schedules`** (autoria: CRUD + editor de rule/
validity/calendar/payload, seletor de pool só-webhook) e **Monitor › Agendas** (`/monitor/schedules`: régua de
`AgendaDispatch` + drill pra sessão + disparar/pausar/retomar/cancelar; reagendar = editar na autoria).
Proxy `/v1/agendas`→3650 (Vite+nginx). **ABAC `scheduler.{configurar,operacao}`** grant-first, **sem role
default nem bypass de admin** (D2 — só quem recebe o campo em Acesso vê as telas; seed concede ao admin demo).
Cliente/tipos em `modules/schedules/api.ts`; i18n ns `scheduler`. **Scheduler completo (Fases 1–3).**

→ See [`docs/product/scheduler-agenda-spec.md`](docs/product/scheduler-agenda-spec.md),
[`docs/adr/adr-timer-scheduler.md`](docs/adr/adr-timer-scheduler.md)

---

## Outbound — Mailing + Campaign + Delivery (Fases 1 ✅ / 2 ✅ / 2b ✅ / 3 ✅ [3a+3b] / 4 importador ✅ / 5a fan-out ✅ / 5b survey e2e ✅)

Substrato **genérico** de contato ativo (Fase 4 do arco Scheduler): `mailing` (audiência) + `campaign`
(orquestrador fino, endereça **POOL** — S4) + `campaign_delivery` (estado por-campanha). **Survey é o 1º
consumidor (S11 agendado), não o dono.** Invariantes: metadado da entrada **opaco** (contrato produtor↔consumidor);
**membership (`mailing_entries`) ≠ suppression (`campaign_deliveries`)**; entrada = `(pessoa, contexto)`.

**`mailing-api`** (Python FastAPI + asyncpg, porta **3660**, schema PG `outbound`): store canônico do domínio
(one-source). CRUD mailings/campaigns + `mailing_add` (upsert por `dedup_key`) + **drain** (`FOR UPDATE SKIP
LOCKED` + claim atômico em `campaign_deliveries`) + `POST /v1/deliveries/{id}/result`. Tools MCP grupo `outbound`
(`mailing_add`/`campaign_drain`/`campaign_delivery_result`) = wrappers finos, `isError` em não-2xx, auditados. O
skill outbound **drena via MCP** (agentes nunca tocam DB); **pacing = a agenda recorrente** (tick drena ≤
`batch_size`). Idempotência: `UNIQUE(campaign_id, mailing_entry_id)` + retry por `campaign.retry.max_attempts`.
Demo: pool webhook `outbound_demo` + `skill_outbound_demo_v1` (drena → `loop` → `campaign_delivery_result`), agenda
dispara com **diff zero** no scheduler (`@ctx.campaign_id` do payload). Gate `infra/test/smoke_outbound_fase1.sh`
✅ (2026-07-21). A validação destravou um fix de engine: `loop.ts` não limpava o sentinel `:__invoked__` → invoke
no body do loop só rodava a 1ª iteração (agora limpo, simétrico ao `:__notified__`).
**Decisão (2026-07-21):** o `contact_eligibility_check` (Fase 2) **substitui** o `survey_eligibility_check` — motor
de elegibilidade único e genérico; survey depois. UI (mailings/campaigns) = dívida da fatia 1b.

**Fase 2 — governança de contato ✅ (validada via API):** motor **agnóstico** de fadiga no schema `outbound` —
`contact_log` (fato: customer×channel×campaign×contacted_at) + `contact_policy` (regra em camadas tenant/campaign:
`frequency_caps`/`quarantine_after`/`channel_caps`; janela `24h|7d|60m|30s`|seg) + `contact_eligibility_check`
(decisão: avalia a policy efetiva — campanha sobre tenant — contra o log; `claim=true` grava o fato na mesma
transação, janela começa no envio; `reason` sempre nomeia a regra). `mailing_unsubscribe` = supressão mailing-scoped
(`entry.status='unsubscribed'`). Tools MCP `contact_eligibility_check`/`mailing_unsubscribe`; endpoints
`/v1/contact-policies`, `/v1/contact/eligibility`, `/v1/unsubscribe`. Validação **via API** (`smoke_outbound_fase2.sh`).
Opt-out global (do_not_contact no cadastro), janela de calendário e preferência soft = Fase 3. **Fase 2b ✅ E2E:**
o gate roda **dentro** do `skill_outbound_demo_v1` (loop → `verificar_elegibilidade`(claim) → `choice` →
`contacted`|`skipped_ineligible`); smoke `smoke_outbound_fase2b.sh` (fadiga cross-campanha no fluxo real).
**Deploy do skill editado num pool com slot:** republicar `skill.flow`/reconcile NÃO basta (o bridge roda o
snapshot do slot `current`) — re-snapshotar via `PUT /slots/next` → `POST /promote` (com `x-service-token`), que
publica `registry.changed(pool)` e invalida o cache do bridge.

**Fase 3 — portões (desenho fechado):** só **3a (janela/calendar)** + **3b (opt-out `do_not_contact`)** são build
novo, no `contact_eligibility_check`. **Capacidade** = routing `allocate-or-queue` + `pool.queue_config.max_wait_s`
(config, sem código — o `collect` cria o contato roteado); **canal** = reuso de `collect.channel_policy` (channels/
preferred_order/exclude) — ambos fecham na Fase 5. Pacing **por-canal**: `reactive` (sem consulta) p/ baixa latência;
`look_ahead` (consulta `pool_status_get` + taxa de conexão) p/ o **discador de voz** (Fase 5+). **Fase 3a ✅ via
API:** `db_contact_eligibility` consulta `campaign.contact_calendar_id` → calendar-api `is_open` (antes dos caps,
fora da transação); fechado → `outside_window` sem claim; erro do calendar → degrada p/ ABERTO. Smoke
`smoke_outbound_fase3a.sh`. **Fase 3b ✅ via API (opt-out global):** `do_not_contact` (`{all?, channels?}`) vive
no cadastro (`identity.customers.attributes`), lido via channel-gateway `GET …/identity/customers/{id}`; o
eligibility veta `opt_out` de **MAIOR precedência** (antes de calendar/fadiga) salvo `campaign.transactional`;
`mailing_unsubscribe scope=global` escreve o atributo. Degrada→ALLOW barulhento. Smoke `smoke_outbound_fase3b.sh` (**validado 2026-08-20**, com
testemunha e caso por-canal acrescentados na validação).

**Fase 4 — importador de arquivo ✅ API (2026-07-22):** adaptador anti-corrupção em **DUAS camadas** no
`mailing-api`, REST puro (importador não é agente): **Camada A** (`batch_ingest` + `POST /v1/mailings/{id}/
entries/batch`, público, agnóstico de formato) recebe linhas normalizadas → resolve `customer_id` (id nativo ou
`anchors`→Identity `resolve()`) → valida (sem contato nem id = `rejected`) → `db_add_entry` → relatório
`{total,added,deduped,resolved,unresolved,rejected}`; **Camada B** (`parse_file` + `POST /v1/mailings/{id}/import`,
multipart) lê o **`column_map` do mailing** (`{customer_id_column?, anchors:[{kind,column}], contacts:{canal→col},
metadata_columns?}` — config de PARSING, `metadata` segue opaco em runtime), faz parse CSV/xlsx **síncrono com
teto** (`PLUGHUB_MAILING_IMPORT_MAX_ROWS=5000`→413), remapeia rejeição→nº de linha, carimba `source=import:{id}`.
Rejeita-linha-e-continua (nunca aborta). Camada A pública = seam reusável por formatos futuros. Deps novas:
`openpyxl`+`python-multipart`. Smoke `smoke_outbound_fase4.sh`. Ver `docs/arcos/outbound.md`.

**Fase 5a — fan-out dispatcher/worker ✅ (2026-07-22):** o loop inline sequencial da Fase 1 virou **dispatcher +
worker** via `workflow_trigger` (fire-and-forget). `skill_outbound_dispatch_v1` (pool `outbound_dispatch`,
disparado pela agenda): `drenar(campaign_drain, claim) → loop{ workflow_trigger(pool=outbound_worker, customer_id,
context_json={delivery_id,customer_id,channel,campaign_id}) } → complete`, **não espera**. `skill_outbound_worker_v1`
(pool `outbound_worker`, 1 por contato em paralelo): `eligibility(claim) → choice → [elegível: contacted →
collect(lazy) → responded|failed] | [inelegível: skipped_ineligible]`. Contabilidade variante (a): dispatcher
claima + passa `delivery_id`. Paralelismo = `outbound_worker.max_concurrent` + allocate-or-queue. **Decisão B:**
usa o `collect` LAZY existente — funcionalmente = ativo p/ todo canal com engajamento **adiável** (link/mensagem);
o ativo-síncrono só é forçado na voz-com-agente (fora do corte; entra como pacing `look_ahead`, não reserva).
Smoke `smoke_outbound_fase5a.sh` (N deliveries `claimed→contacted`).

**Fase 5b — survey outbound e2e ✅ (2026-07-22):** conecta o survey ao substrato de campanha. O processo faz
`mailing_add` no `complete` (journey_complete) com `metadata={origin_session_id,grain,form_id,customer_key}`;
campanha+agenda drena; `skill_outbound_survey_dispatch_v1` (pool `outbound_survey_dispatch`) faz fan-out ao
`skill_outbound_survey_worker_v1` (pool `outbound_survey_worker`). **Veículo = link web** (`survey_link_create`,
que recebe `origin_session_id` EXPLÍCITO da metadata) e **não** o `collect` — o collect chavearia o sinal pela raiz
da sessão chamadora (a do dispatcher no fan-out), errada p/ o survey do processo. Worker: `eligibility(claim) →
survey_link_create → campaign_delivery_result(contacted, guarda token) → complete`. A submissão em
`/survey/{token}/submit` publica `session.signals` no origin/grão (mesma trilha do `survey_record`). Closure =
sinal + `contacted` (token na delivery p/ drill; `responded` por-delivery = refinamento). Dispatcher de survey
próprio (conhece o contrato de metadata; mantém o da 5a congelado). Smoke `smoke_outbound_fase5b.sh`. **Arco
Outbound completo (1–5).**

→ See [`docs/arcos/outbound.md`](docs/arcos/outbound.md),
[`docs/product/outbound-mailing-campaign-design.md`](docs/product/outbound-mailing-campaign-design.md),
[`docs/product/outbound-fase1-implementation-spec.md`](docs/product/outbound-fase1-implementation-spec.md)

---

## Pending (Next Iteration)

> ⚠️ **A triagem de 2026-08-17 NÃO é mais filtro vivo — a direção que a ancorava foi revertida em
> 2026-08-18.** Ver [`docs/product/n8n-arco-abortado-2026-08-18.md`](docs/product/n8n-arco-abortado-2026-08-18.md).
> Os baldes que aparecem entre colchetes abaixo eram relativos a um alvo que não existe mais:
>
> | Balde antigo | Estado agora |
> |---|---|
> | `Congela` | **DESCONGELADO** — prendia "até o gate da fase 3", e esse gate não existe. Volta à fila normal, **sem prioridade herdada** |
> | `Escopo reduzido` | **REEXAMINAR** — o corte era *"esta parte vira template n8n"*. Rejulgar item a item; **não** reverter em bloco (alguns cortes eram bons por mérito próprio) |
> | `Aborta` | **segue abortado**, por mérito próprio — nenhum caiu por *"o n8n cobre"* |
> | `Segue` | inalterado; onde a justificativa citava o alvo, ela foi trocada, não a prioridade |
>
> A evidência por item da triagem (arquivo:linha) continua válida; os baldes e as âncoras de fase, não.
> **No lugar do alvo:** A2A server binding ([`adr-a2a-server-binding.md`](docs/adr/adr-a2a-server-binding.md))
> e editor gráfico próprio alavancado por *execução observável*. A direção *"config + interpretador
> genérico"* sobrevive inteira e **nunca dependeu do n8n**.
>
> ⚠️ Arcos concluídos foram movidos daqui para o `CHANGELOG.md` — a regra de manutenção deste arquivo
> proíbe ✅ no `CLAUDE.md`, e item concluído dentro de uma seção chamada *Pending* volta a ser triado como
> trabalho em aberto. Limpeza restrita ao que a triagem tocou; varredura completa da seção é escopo à parte.

### Arc 15 — WebRTC (decisão em aberto) — **[Descongelado 2026-08-18]**
- **Provisionar o SFU** *(pendente de VERDADE, medido 2026-08-20)*: não há serviço LiveKit em compose
  algum, nem env `LIVEKIT_*`, nem manifesto k8s em `infra/`, nem o SDK como dependência do
  channel-gateway — o canal roda inteiro em `_dev_mode`/mock. Bloqueia qualquer medição de WebRTC.
- bridge PSTN → WebRTC via LiveKit SIP Ingress (eliminar Twilio como canal separado). Decisão, não
  implementação pendente — mas **depende do item acima**: não se decide topologia de mídia sobre um
  SFU que não existe. *(Esta linha dizia "o arco em si está concluído"; concluído é o canal, não a
  solução de mídia.)*

### Usage Metering — Channel Gateway Adapters — **[Descongelado 2026-08-18]**
- `whatsapp_conversations`, `voice_minutes`, `sms_segments`, `email_messages` *(deferred)*: functions in `usage_emitter.py` ready, adapters not yet calling them. Depende da evolução dos módulos de channel-gateway, que não andou — motivo **próprio**, e o único que sobrou depois que o gate de fase caiu. *(O achado de que `llm_tokens_*` não é emitido no `/v1/reason` continua valendo, mas é **defeito**, não item de direção.)*

### Pricing Module — **[Descongelado 2026-08-18]**
- **Integração metering × pricing** *(deferred)*: módulo que aplica planos e escreve `{tenant}:quota:limit:*`.

### Audit LGPD — Fases Pendentes — **[Segue — fosso]**
> ⚠️ A urgência extra vinha de *"parte da execução mora fora da plataforma"* (retenção de log com PII
> no motor de terceiro). Com a reversão, **a execução volta a ser toda em casa e essa urgência cai** —
> as quatro fases seguem pendentes por obrigação de LGPD, que é razão própria e independente.
- **Fase 2** *(deferred)*: `original_content` desmascarado via endpoint batch de resolução de tokens em Core.
- **Fase 3** *(deferred)*: `user_access` logs — topic Kafka `user_access.events` em auth-api + ClickHouse.
- **Fase 4** *(deferred)*: SAR/erasure pipeline — pseudonimização `sessions_stream` + anonimização ClickHouse.
- **Fase 5** *(deferred)*: `config_snapshot` — read-only do namespace `masking` do Config API para DPO.

### Quality Ingest — concerns abertos — **[Segue — fosso]**
*(O arco R13a–R13d está concluído; história no `CHANGELOG.md` e detalhe em `docs/arcos/quality-ingest.md`.)*
- **Concerns** (§9 do doc do arco): (a) `ReplayContext` `session_meta`/`participants`/`sentiment` ainda em default p/ importados (transcript completo); (b) correlação por-requisição do quality-ingest — `pool_id` degrada se um contato vier partido entre POSTs. O concern (c) foi resolvido pelo discriminador `origin` (abaixo).

### Isolamento do substrato por `origin` — Fase 2 — **[Descongelado 2026-08-18 — mas o gatilho próprio segue não disparado]**
Discriminador `origin: live|import|reeval` por-sessão nas tabelas de substrato, com **filtro default `live`** no report layer da analytics-api (`_apply_origin_scope`) e no sampling da evaluation-api (`_passes_filters`) — é o default no backend que dá a garantia, e a UI operacional espelha (sem seletor de origem: origem é contexto de qualidade, não dropdown operacional). **Invariantes:** `origin` é a verdade universal por-sessão; **não** estender `pool.agent_kind`.
**Pendente = só a Fase 2** (partição CH `PARTITION BY (…, origin)` + `pool.origin_class`), **adiada por decisão em 2026-06-25**: é governança/lifecycle, não correção. **Gatilho de reativação inalterado pela reversão** — importação externa real com obrigação de retenção/erasure própria (LGPD, `DROP PARTITION`). Sair do balde `Congela` **não** o antecipa: o item nunca dependeu do alvo abortado, e continua esperando o gatilho de negócio. → [`docs/adr/adr-quality-substrate-isolation.md`](docs/adr/adr-quality-substrate-isolation.md)

### Business in Any Media — processo channel-abstract + framework de loja *(proposta)* — **[REEXAMINADO 2026-08-26 — corte REVERTIDO]**
> O corte do nível (a), do contrato delegate-por-pool e do intake-flow tinha como razão literal
> *"autoria, que vira template n8n"* — fundamento que caiu com a reversão. **Voltam à fila**, a
> rejulgar pelo mérito sob a pergunta *"quanto disso vira config + interpretador genérico"* (tarefa
> **B1**). Veredicto e tarefas em `TODO.md` § *"Reexame dos 9 em `Escopo reduzido`"*.
> **Fica** (fronteira/governança): resolvedor de identidade nível (b), gate de identificação,
> commerce-cards com checkout mascarado + repasse ao PSP, novas `ChannelCapability`. Esta metade
> nunca dependeu do alvo e segue inalterada.
> ⚠️ **A outra metade voltou a ser questão aberta.** O nível (a) *"fluxo negocial channel-abstract"*,
> o contrato delegate-por-pool e o intake-flow tinham sido cortados com a razão *"vira template n8n,
> porque a autoria sai por completo"* — a autoria **fica**, então o fundamento do corte caiu.
> Rejulgar pelo mérito: são autoria de fluxo, e o que decide agora é o escopo do editor próprio
> (quanto mais vira config + interpretador genérico, menos precisa ser fluxo autorado).
> **Consumidor da parte que fica:** Cliente 360 / Resolvedor de Identidade Fase C.
- Reposicionamento process-centric + comércio conversacional sobre o modelo de 3 níveis (a/b/c). Specs em `docs/product/`: arquitetura-alvo (3 níveis), resolvedor de identidade/cadastro (nível b, generaliza `pending_workflow`), contrato delegate-por-pool, commerce-cards (nível c), fluxo de intake. Detalhe e fases em `TODO.md`. Base existe (workflow+canais+suspend/resume+masking); falta cadastro de identidade completo, commerce-cards e o nível (b) de primeira classe.
- **Resolvedor de Identidade (nível b) — Fase A · Slices 1–2 ✅** (2026-07-02, CHANGELOG): cadastro mínimo interno no channel-gateway (módulo `identity/`). **Slice 1 (Redis):** Lookup 1 `resolve_or_provision` (`{t}:identity:{kind}:{hash}`→`customer_id` nativo, PII hasheada com salt de env), Lookup 2 `pending_by_customer` (generaliza `pending_workflow`), endpoints `POST …/identity/resolve` + `GET …/pending/by-customer/{id}`, tools MCP `customer_resolve` + `pending_workflow_get(anchors)`, dual-write flag-gated no `delegate`. **Slice 2 (PG durável):** schema `identity` (`customers`/`secondary_keys`/`external_refs`/`merges`, raw asyncpg idempotente, reusa o pool dos attachments), promoção efêmero→PG no gatilho concreto (`write_pending`), fallback Redis→PG com reidratação (`matched_by="durable"`). **Destrava a retomada cross-canal** e é a chave estável que o histórico (arco H) precisa. **Slice 4 ✅** (2026-07-02): o bridge (`_close_contact_layer` → `_resolve_close_customer_id`) carimba o `caller.customer_id` **nativo** do ContextStore em `sessions.customer_id` no fechamento (fallback `contact_id`) → conserta o `contact_id`-como-`customer_id` e reconecta H1/H2/H3; `AgentAssistPage` chaveia a `HistoricoTab` pelo `caller.customer_id`. **Fase A completa (Slices 1–4).** **Wiring do intake ✅ (2026-07-03, CHANGELOG):** `agente_portabilidade_intake_v1` resolve o `customer_id` nativo (`customer_resolve`, âncoras `numero_atual`+`contact_identifier`) e grava `caller.customer_id` via `context_set` pré-ramificação → Slice 4 propaga o nativo. Validado no demo (2 intakes, mesmo número → mesmo `cus_…`). **Nota de deploy:** pool migrado a `PoolSkillSlot` exige `set-next`+`promote` (edição de YAML+restart republica `skill.flow` mas não re-snapshota o slot `current` que o bridge executa). **Slice 3 ✅ (2026-07-03, CHANGELOG):** campos opcionais `customer_resumable` (default `false`) + `resume_policy` (`offer|auto`, default `offer`) no step `delegate` e `collect` (`schemas/src/skill.ts`), propagados pelos call sites explícitos dos executores (`executeDelegate`/`executeCollect` — ponto de drop) → `persistDelegate`/`persistCollect` → skill-flow-service → channel-gateway. A **dual-write `pending_by_customer`** (antes incondicional) agora é **gated em `customer_resumable`** em `handle_delegate` **e** `handle_delegate_conference` (spec §6); `resume_policy` viaja no `PendingEntry.policy`. `session_resumed` ganha `resume_origin` (`same_channel|token|identity`; só `token` wirado — `same_channel`/`identity` ficam p/ o caminho de reconexão-oferta da Fase B). Guardrail de perfil = colocação no schema (o discriminated union descarta os campos de um `suspend`). Demo: `skill_portabilidade_demo_v1` (`notificar_e_confirmar`) seta `customer_resumable: true` p/ manter a retomada cross-canal sob o gate. **Reconexão-oferta por identidade ✅ (2026-07-03, Fase B slice, CHANGELOG):** o intake (`agente_portabilidade_intake_v1`) resolve pendências por **anchors[]** (Lookup 1→2 cross-canal via `pending_workflow_get(anchors)`) em vez de `contact_identifier`; `find_pending_by_customer` devolve `policy` + view achatada (compat legado) e a dual-write guarda `context_preview` **mascarado** (`operadora_destino` claro, `numero_atual`→`***4321`); novo `choice avaliar_politica_retomada` honra `policy` (`auto`→retoma direto, `offer`→menu); `resume_origin=identity` percorre intake delegate→`session.resume_origin`→confirmação→`workflow_resume` (tool tolerante a valor ausente→`token`)→endpoint→`handle_resume`→`session_resumed`. Validado no demo (oferta cross-canal com número mascarado). **Identidade progressiva + posse de canal (OTP) + gate seguro ✅ (2026-07-04, Fase B completa em 3 fases, CHANGELOG + ADR `adr-identity-channel-possession.md`):** (1) progressiva — `resolve_or_provision` anexa âncoras *miss* ao vencedor como `claimed` (email sozinho resolve depois); `verification_class` (`claimed|possessed`) no índice Redis (`{cid,vc}`, leitor tolerante) + PG; confiança = `f(kind,classe)` (possessed>claimed). (2) OTP como **serviço componível opcional** (`OtpService`: challenge/verify, código só-hash, rate-limit, entrega mockada gated por `PLUGHUB_OTP_DEV_RETURN_CODE`); `otp_verify`→`attach_anchor(possessed,durable)` é a **única** via para `possessed` (`customer_attach_key` só `claimed`; invariante possessed⟺verificado); tools `otp_challenge`/`otp_verify`/`customer_attach_key`/`customer_update_attributes`. (3) **default seguro**: retomada cross-canal de `customer_resumable` exige `possessed` — `pending_workflow_get` devolve `verification_required` (sem vazar existência) quando só `claimed`; intake oferece OTP (proativo com recusa)→verifica→re-consulta. **Plataforma = autoridade de posse de canal, não de identidade-de-registro** (emenda princípio 7/§4.4; identidade legal segue no CRM do tenant). **Falta (Fase C / quando houver CRM):** `external_refs` + merge de clientes; wiring do step CRM `resolve`; origem `resume_origin=same_channel` (continuidade intra-canal, platform-level); transporte real do OTP; `persistCollect` no skill-flow-worker legado. Plano: `docs/product/identity-resolver-fase-a-plano.md`.

### Journey (retorno) — modelo de 3 níveis — **[Segue — fosso]** *(J1–J5b concluídos; história no `CHANGELOG.md`)*
> ⚠️ **Corrigido 2026-08-17:** este cabeçalho dizia *"N3-no-drill pendente"*; o `TODO.md:3240` registra o
> item como **entregue em 2026-07-23**. Abertos de verdade: **item 2** (cache `sessions.journey_id` não
> refrescado no merge — otimização adiada por decisão) e **item 3** (guard de rota ABAC em `analise/*`,
> dívida app-wide de segurança).
- **Estado:** J1 (espinha `root_session_id` + propagação), J2 (`/reports/journeys` + Vista Processos + drill 3 níveis), J3 (`journey_merge` + `journey.merges` + `journey_aliases` + union-find + `PendingEntry.root_session_id`) **concluídos e validados** (ver `CHANGELOG.md`). **J4 reenquadrado (2026-07-22):** deixou de ser um display isolado e virou a **fatia journey** da camada genérica **Customer Voice** (lente `grain × metric` sobre `session_signal` + catálogo source-aware + overlay SLA; ver seção "Customer Voice" no CHANGELOG). O grão journey do survey é exibido na superfície "Voz do Cliente" (`/analise/customer-voice`); a exibição N3 **na própria Vista Processos** (pendurar o sinal no drill) segue pendente. Decisão as-built: cache `sessions.journey_id` **diferido** (reads por union-find, sem refresh no merge). **J5a ✅ (2026-07-22) — `@ctx.journey.*` (contexto compartilhado do processo):** a **leitura** (interpolate) + **escrita automática** (context_tags via engine) + **migração no merge** (`journey_merge` → `migrateJourneyContext`, canônica vence) já existiam; o gap era a **escrita imperativa** (`context_set` do skill-flow e `/api/inject-context` do supervisor gravavam raw no hash da sessão). Fechado com o helper único **`writeContextTag`** (`tools/journey.ts`): tag `journey.*` → hash do PROCESSO (`{t}:ctx:journey:{raiz canônica}`, TTL 30d), resolvendo a raiz pela MESMA via do bridge (proveniência `session.root_session_id` → `resolveJourneyRoot` union-find); demais tags → hash da sessão. Sem nova dependência (não importa `@plughub/sdk`; reusa os helpers que o `journey_merge` já usa). Smoke `infra/test/smoke_journey_context.sh`.
- Reintroduz o agrupamento de N contatos (N1/N2 operacionais) em torno de um processo negocial (N3) **sem** a entidade Journey do Arc 10 (removida na Fase F). Modelo **D1.5**: journey = componente conexa de sessões sob (proveniência ∪ alias), identificada pela **raiz canônica** (valorada em `session_id`, resolvida por union-find na leitura). Fonte de verdade = `root_session_id` (imutável, nunca null: param propagado do chamador ou auto-mint=`self`) + `journey_aliases`; a coluna dormente `sessions.journey_id` vira **cache** eventualmente consistente. Merge = tool MCP `journey_merge` (sempre novo→antigo ⇒ ordem total, sem ciclo) + topic **`journey.merges`** (1 tipo, ≠ `journey.events` de 9 tipos removido no Arc 19). Mantém `origin_session_id` (1 salto, `SessionTrace`) **e** `root_session_id` (raiz transitiva). Três superfícies: mostrar 3 níveis (drill `journey→[session→[segment]]`), medir/avaliar por nível (N1 QA por segmento; N3 `session_signal` grain=`journey`), exibir contatos sob a journey (proveniência ∪ merge). Fases J1–J5 (J1+J2 = journey por proveniência; J3 = merge). **Nunca** reviver entidade/lifecycle/merge-split. Detalhe/fases em `TODO.md`.
- Design: [`docs/product/journey-retorno-modelo-3-niveis-design.md`](docs/product/journey-retorno-modelo-3-niveis-design.md) · Spec: [`docs/product/journey-3-niveis-implementation-spec.md`](docs/product/journey-3-niveis-implementation-spec.md) · Diagrama: `docs/product/journey-3-cenarios-unionfind.svg`.

### Fila de trabalho humano / dispatch pull + inbox no Console — **[Segue — fosso]** *(pull genérico e renderer R0 concluídos; aprovação R1 + wrap-up E2 pendentes)*
> Dispatch de trabalho a humano de ponta a ponta — governança de contato com pessoa. Nada depende do
> editor de fluxo. O `DialogFormRenderer` (R0) é a superfície genérica que o alvo **reforça**: é o
> tratamento de collect-form no Console **sem skill por caso**, o mesmo princípio de "config +
> interpretador genérico" que o §5.3 do doc de interop extrapola.
- **Pull genérico ✅** (Frente 1 F1–F2b, ver CHANGELOG): `dispatch_mode: pull` no Routing Engine (claim atômico `ZREM`, lease+auto-release), tools `work_queue_*`, `PullInboxPanel` com preview.
- **Renderer genérico de collect-form no Console — R0 ✅** (2026-07-24, ver CHANGELOG): `DialogFormRenderer.tsx` = 4ª superfície do dialog primitive. Claim de workflow suspensa (ctx `session.dialog_form_id`+resume token) → **briefing** (transcrição de `session.briefing_session_id`) + **DialogForm inteiro** → `workflow_resume` com `payload.answers`. `ApprovalPanel` virou **wrapper fino** (decisions/edições/ABAC empilhados). Consumidores validados: aprovação (wrapper) + demo genérico (`skill_formfill_demo_v1`/`dialog_formfill_demo`, `smoke_formfill_renderer.sh`). Núcleo estável que aprovação (R1) e wrap-up-α (E2) consomem sem alterar. **Follow-up:** ingress de resume aplica `approvals.decide` a qualquer resume com JWT (parametrizar por tipo de tarefa — relevante à E2).
- **Aprovação como especialização — desenho FECHADO** (ADR `docs/adr/adr-human-approval-workflow-step.md`, 2026-07-16; **A3/R0 ✅**, R1 = anexos/masking + ABAC completos pendente): aprovação = passo transparente do workflow (`collect`/`delegate` a um pool, `dispatch_mode` config push|pull) → **conteúdo = DialogForm** (reuso do primitivo; editor/`form_get`), **aprovador = agente logado** (Modo A; reusa Agent Groups/Arc 8/ABAC `approvals`), **superfície = Console/inbox responsivo**, **retorno pelo payload do delegate → `choice`**. **Omnichannel adiado** (conteúdo/retorno canal-agnósticos; sem `channel_type: "console"`). Fases A1–A6 no ADR §6. Specs antigas (`routing-pull-dispatch`, `human-work-queue-aprovacao`, `pull-inbox-console-ui`, `frente1-…-consolidado`) **reconciliadas** pelo ADR. Liga ao gate de promoção homologação→produção.

### Detach de hooks de finalização + Pull direcionado + ACW *(desenho fechado 2026-07-23; Camadas A ✅ + B ✅ + D ✅ 2026-07-24; E1 ✅ (Forma A aposentada); E2 wrap-up ✅ (detached + wiring, 2026-07-24/27); **wrap-up UNIFICADO** ✅ Phase 0+1+3 (2026-07-27) — inline=auto-atendimento sobre a MESMA máquina detached, `acw_pending`/`acw_gate` REVERTIDOS + inline antigo (`wrapup_ia`+`wrap_up_pending`) REMOVIDO (modelo errado: bloqueavam a instância inteira; capacidade = 1 vaga pelo semáforo `claim_instance`, os dois modos); **Phase 2 ✅** (2026-07-27) hand-off da vaga: no close com wrap-up inline a vaga é TROCADA por um hold `__wrapup_hold__::{origin}::{pool_id}::{expires_at_ms}` (swap net 0, flag `keep_slot_for_wrapup` carimbado pelo bridge) que o auto-claim do wrap-up HERDA — ocupação nunca oscila, push não toma a vaga na janela; holds expirados são descartados em qualquer claim (anti-vazamento). Destravou 3 defeitos pré-existentes do ciclo pull/resume (identidade da instância humana corrompida pelo `agent_ready` do resume; vaga do claimante devolvida só por efeito colateral; estado do form grudado no Console) — ver CHANGELOG + `docs/guias/conference-mechanics.md` Mudança 27. **Camada F ✅ 2026-07-30 — ARCO A–F COMPLETO**; ver CHANGELOG)*
- **Problema:** hooks de finalização não podem suspender/collect — o bridge segura `_trigger_contact_close()` (`hook_pending`) e trata `suspended` como concluído → fecha o contato cedo. Isso força DUAS formas de coleta de survey (delegate legado `skill_survey_v1` × collect J4c). A razão de segurar é **atribuição**, que a Journey (`root_session_id`) + referência de segmento no payload resolvem **sem** segurar.
- **Alvo:** reduzir a 2 mecanismos — `inline` (síncrono, precisa do WS vivo do cliente: NPS presente) e `collect` (assíncrono, perfil workflow) — e **aposentar a Forma A (delegate)**. Fecha **G1** (AHT inflado por wrap-up) e generaliza **G7** (desacoplamento `on_human_end`).
- **Invariante (PABX):** o "ramal" (direcionar a um recurso) NÃO é alvo de roteamento — é work item que mora num **pool** (fila) com filtro de claim `assigned_to` + **fallback pro pool** por lease. Fila=pool+dispatch; ramal=pull direcionado+overflow. Embrião de transfer-to-agent, sem quebrar o invariante "pool é a unidade endereçável".
- **Camadas:** **A** `dispatch: inline|detached` no `PoolHookEntry` (schema, default inline; guard rejeita `detached` em `on_human_start`) ✅ · **B** pull direcionado ✅ **(2026-07-24, smoke 5/5)** — `assigned_to`+`fallback_to_pool_after_s`+`assigned_at_ms` no work item (`QueuedContact`/`contact_data`, sem novo Zod); gate DENTRO de `Router.work_task_claim` antes do `ZREM` (dono OU idade ≥ fallback; ausente=permanente; `reason: reserved_to_other`, logado); claimant derivado de `instance_id`=`human-{userId}` (ou explícito); inbox filtra/rotula reservado×transbordado; **sem reaper de lease** (transbordo por idade do item); smoke `infra/test/smoke_directed_pull.sh`; wrap-up como consumidor = Camada E · ~~**C** `acw_gate: none|soft|hard`~~ **REVERTIDA na Phase 0 e REMOVIDA ponta a ponta (2026-07-29)** — o gate bloqueava a instância INTEIRA (não uma vaga) e reservava no dispatch (não no claim); a Phase 0 tirou o enforcement e o marker, e a coluna/plumbing/UI saíram depois (migration `20260729000000_drop_pool_acw_gate`). Capacidade de wrap-up = 1 vaga pelo semáforo `claim_instance`, nos dois modos. **Não reviver este enum**: um gate de ACW futuro se desenha sobre a VAGA · **D** bridge honra `detached` ✅ **(2026-07-24, smoke 2/2)** — `_fire_detached_hook` (workflow webhook `POST /channels/webhook/pool/{id}`, `origin`+`journey:inherit`+ref de segmento no ctx); `_entry_will_dispatch` exclui detached do `hook_pending`; auto-close `_trigger_contact_close` na leva 100% detached (fecha G1); guardas `_has_customer_hooks` excluem detached; env `CHANNEL_GATEWAY_URL`; **conference-mechanics.md Mudança 25**. Limitações: post_human+detached, segment_wrapup fanout → Camada E · **E1** ✅ **(2026-07-24)** aposentar Forma A (pools `survey_processo_ia`/`survey_collector_ia`/`survey_reconnect_ia` + skills `skill_survey_v1`/`skill_survey_nps_v1`/`skill_survey_reconnect_v1` — estavam inertes, removidos do YAML/arquivos; DB rodando persiste inerte, purge opcional via PRUNE) · **E2** wrap-up detached — **núcleo ✅** (Path α renderer-first): renderer R0 (`DialogFormRenderer.tsx`) ✅ 2026-07-24; form `dialog_wrapup_v1` + workflow `skill_wrapup_detached_v1` (E2a) ✅; tool `segment_outcome_record` (E2b, grava outcome no segmento da origem por referência) ✅ 2026-07-24 E2E; **wiring `on_human_end` ✅ 2026-07-27** (`retencao_humano.on_human_end` → `wrapup_detached_ia`; o `dispatch` controla só a ENTREGA sobre a MESMA máquina — `inline` = auto-atendimento no Console; `detached` = item de pull manual. **Config VIVA medida 2026-08-22: `dispatch: detached`** (`GET :3300/v1/pools/retencao_humano`).
> ⚠️ **Correção da correção.** Em 2026-08-20 esta linha foi "corrigida" para dizer que o demo estava em
> `inline`, citando `infra/registry/tenant_demo.yaml:400`. O YAML diz `inline` mesmo — e não vale nada:
> registry é **seed-if-absent**, editar pool já semeado é no-op e **o DB vence**. Aquela correção leu a
> fonte DECLARATIVA e a chamou de estado — o mesmo erro que a § Configuration — Single Source
> Invariants descreve em "Seed-if-absent / DB-owned". Regra derivada: **para
> `hooks`, `deploy`, `capacity` e afins, perguntar ao agent-registry, nunca ao YAML.**; `_fire_detached_hook` injeta `origin_session_id` no ctx; `fire_pool_hooks` semeia seg_signal + surveyed_*; **E2E validado com atendimento real, sem seed**). **Falta:** ~~sessão de wrap-up fora da contagem de contato/TMA (E2f)~~ ✅ · **F** validação ✅ **2026-07-30** — F1 atribuição (provada pelo `issue_status`, não pelo `outcome`), F2 G1 no relatório (8 contatos na tela = 8 no pool de contato; 11 sessões internas fora — contaminação seria na CONTAGEM, não na média: `handle_time_ms` é NULL nas internas), F3 pull direcionado 5/5 em duas execuções (flakiness era o **drain** comendo o item de um pool sem `pool_config`, corrigido na raiz), F4 expiração (`acw_expired` com duração real; vaga devolvida pelo prazo — a **lease** não foi medida, lacuna 2 segue aberta). *(E2e — produtor do marker `acw_pending` — saiu de escopo com a remoção da Camada C.)*
- Design: [`docs/product/finalization-hooks-detach-and-directed-pull-design.md`](docs/product/finalization-hooks-detach-and-directed-pull-design.md). Detalhe/fases em `TODO.md`.
- **Triagem 2026-08-17 — [Segue — fosso].** Governança de contato com pessoa de ponta a ponta; nada aqui depende do editor de fluxo. A **Camada E2** é o próprio movimento do alvo aplicado à superfície humana (*"o renderer trata collect-form genérico, sem skill por caso"*) — alinhada, não conflitante.
- ✅ **CONFLITO DOC×DOC resolvido POR MEDIÇÃO (2026-08-20).** Este cabeçalho estava certo; o `TODO.md` § homônima (~linha 4303) estava **estagnado no plano de 2026-07-23** — chamava E2 de "pendente" e ainda listava a **E2e** como escopo, item que morreu com a reversão da Camada C. O tell de que era plano velho, não medição divergente: nenhuma das duas seções do `TODO.md` concordava entre si (a de ~2652 já marcava E2f e F ✅). Medido no código: os sete sub-itens da E2 existem (`dialog_wrapup_v1` + `skill_wrapup_detached_v1`; `segment_outcome_record` nos DOIS registros do mcp-server; `assigned_to` de webhook→routing→claim; `pools.purpose` com 2 migrations e filtros no analytics; `DialogFormRenderer.tsx`), e o `CHANGELOG.md:6542` tem a entrada da Camada F datada de 2026-07-30 com F1–F4 contra o dado. **Uma correção caiu da medição** (o `dispatch` do demo, acima). **Dois fatos seguem abertos — e são fato, não conflito:** (a) a própria F4 declara sua lacuna (a **lease** não foi medida; sem reaper); (b) **não existe gate re-executável da Camada F** — ela foi validada por medição manual instrumentada, reaproveitando os smokes de B/D/R0/I5 por override de env (`infra/test/smoke_internal_work_queue.sh:85-89`, que parametriza `DISPATCH`/`ACW_HOURS`). Um arco declarado completo sem gate versionado volta a ser lembrança, não verificação.

### Record/Replay Harness *(proposta)* — **[Segue — fase 5]**
- Generaliza o Session Replayer num harness de gravação/replay em todas as costuras (driver/mock por seam) p/ regressão determinística e gate de promoção via `ComparisonReport`. Falta captura full-fidelity MCP/AI Gateway, clock/seed injetável, gravação seletiva. Spec em `docs/product/record-replay-harness-spec.md`. Detalhe em `TODO.md`.
- **Justificativa trocada em 2026-08-18, prioridade preservada.** A razão de então era *"é o único gate capaz de pegar a avaliação tier-2 achatando sem alarme"* — ela **caiu junto com o alvo**, porque a avaliação deixa de achatar quando a execução fica em casa. Sobrevive pela razão original e própria: **gate de promoção** por regressão determinística. Também some o custo extra que a triagem lhe atribuía (não há costura nova a gravar).

### Customer Surveys — Módulo de Pesquisas de Satisfação *(spec/ADR)* — **[REEXAMINADO 2026-08-26 — parcial]**
> **S7 (editor de DialogForm) sobe, confirmado.** O **S2** segue absorvido no interpretador genérico,
> mas *se ele volta a ter dono próprio é decisão a tomar, não herdada* (tarefa **C2**) — a frente que
> o absorvia mudou de dono, não morreu. O trio de skills sem pool **segue abortado por mérito próprio**.
> ⚠️ O resíduo do `value_label` foi citado com **arquivo errado** na triagem e precisa ser remedido
> antes de entrar em plano (**C4**). Veredicto em `TODO.md` § *"Reexame dos 9 em `Escopo reduzido`"*.
> **Fica:** S5/S8/S9–S11, store per-response, resíduos do S1 (nenhum produtor CES/PMF/FCR; `value_label`
> ignorado em `CustomerVoicePage.tsx:161`), e o **S7 = editor de DialogForm** — que **ganha importância
> com a reversão**, não perde: o conteúdo conversacional continua sendo autorado em casa, e a guarda do
> `ask_when` (sem control-flow no form) segue load-bearing.
> **S2 — reavaliar o enquadramento.** Ele era *"runner genérico + DialogForm"* e tinha sido **absorvido**
> pela frente *"promover o interpretador a serviço de código"*, ancorada num pré-requisito de fase 5 que
> não existe mais. A frente sobrevive (agora como **redutor de escopo do editor próprio**), mas se o S2
> volta a ser fatia com dono próprio ou continua absorvido é decisão a tomar, não herdada.
> **Segue abortado por mérito próprio:** o trio `skill_survey_runner_v1`/`skill_survey_outbound_v1`/
> `skill_survey_trigger_v1`, que nenhum pool deploya. Não foi cortado por *"o n8n cobre"*, e a reversão
> não o ressuscita.
- Generaliza o NPS de fim-de-contato (`skill_nps_v1` + `on_contact_end` + `survey_record` → `session_signal`) num módulo de 5 instrumentos (**CSAT/NPS/CES/PMF/FCR**; Health Score = composto futuro). Princípio: separar **instrumento** (`survey_definition`, composto de perguntas reutilizáveis `survey_question` — N formulários por tipo via form-builder; editor — ADR §16×§17: **B decidida** = 1 skill interpretador genérico + **form JSON versionado** (draft/published na evaluation-api), **engine estendido em 2 peças** (`$.config` do slot no flow + `menu.options/fields` dinâmicos), binding via `interface_schema`→`PoolSkillSlot.config_json` (`form_id` + `survey_form_get`); **A alternativa** = compile-to-skill via `SurveyCompiler`) de **gatilho** (**decisão no skill**, não na plataforma) de **veículo** (runner na conferência / link web). **Gatilho (revisão 2026-06-23)**: o hook é genérico e despacha sempre; o `skill_survey_runner_v1` lê `@ctx.session.contact_outcome` e decide — "ciclo fechado" (`resolved`) é convenção customizável do runner, não invariante de plataforma. Único pré-requisito de plataforma: carimbar `contact_outcome`/`segment_outcome` no ContextStore pré-hook. Achado corrigido: o `skill_nps_v1` é slot transacional (CSAT) com instrumento NPS colado — substituído pelo runner genérico. Net new: **quarentena** anti-fadiga (tool MCP `survey_eligibility_check` + ledger PG/Redis), schema PG `survey` (question/definition/instance/response/quarantine), **interface web pública** `/survey/:token` + envio outbound, **lente `customer_voice`/view "Visão do cliente"** na bancada 360°, **navegador de respostas** (lista por tipo + verbatim + áudio/STT, LGPD — desde a F4 do ADR de relatórios é o NÍVEL de respostas de `/analise/customer-voice`, não endereço próprio) e **agente IA `agente_survey_analyst_v1`** (classifica sentiment/tema/urgência + endereça via Rules Engine/`workflow_trigger`). **Retorno outbound** (§19): contato ativo via `collect`/Arc 19, modo auto (rules) OU **caixa de ações no Console** (sessão outbound-intent parqueada na **inbox pull já existente** — `PullInboxPanel`/`dispatch_mode`/`work_queue`; novo = pool de retorno + skill pós-claim). **claim ≠ collect**: o claim só anexa + dispara briefing (`on_human_start` copilot: contexto da origem + verbatim + histórico); o agente coordena o `collect`/dial via menu `agents_only`. Associação à base de cliente via `customer_key` (forward-compatível com o cadastro dinâmico futuro). Fases S1–S10. **Cadastro de cliente e Health Score fora de escopo** (só os ganchos de dados).
- **Reconciliação de store (2026-07-07, ADR `adr-survey-form-scoring-composition.md`):** a nota original "form JSON versionado na **evaluation-api** + `survey_form_get`" é **superseded** — o `survey_definition` é um **`DialogForm`+dimensions na dialog-api** (D8; o dialog primitive as-built usa dialog-api + `form_get`). Composição de nota: camada `dimension` (instrumento) agrupa perguntas com **escala+agregação na dimension** (perguntas herdam), `weighted_mean` peso-1-default com re-normalização de NA, **dimensions paralelas** (um sinal por dimension, ≠ composite único do Quality); `survey_record` **compõe** server-side (D9) via o primitivo compartilhado `@plughub/schemas/scoring.ts` (`composeScore`). Schema escrito; runtime + editor com dimension pendentes.
- Spec: `docs/arcos/customer-surveys.md`.

### Outbound — refinamentos — **[Segue — fosso]**
*(O arco Fases 1–5 está concluído; história no `CHANGELOG.md` e detalhe em `docs/arcos/outbound.md`.
O substrato de audiência — `mailing`/`campaign`/`campaign_delivery`, com máquina de estado por
destinatário e pacing na agenda — é capacidade própria, e nenhuma parte dele esteve em jogo na direção
revertida. O `do_not_contact` da Fase 3b — que era o item mais urgente da seção, veto de contato com
pessoa rodando sem gate verde — foi **validado em 2026-08-20**; ver CHANGELOG.)*
- **Fase 3 — pipeline de portões** (cada um reuso, "aplica se configurado"): janela de contato (calendar-api
  `is_open`), recursos/pacing (`pool_status_get` + back-pressure da agenda), canal (`channel_policy`+resolver,
  possessed-only), preferência (cadastro de cliente).
- **Fase 4 — importador** anti-corrupção (CSV/xlsx → `mailing_add`, padrão quality-ingest) **✅ API** (2026-07-22)
  — duas camadas (batch ingest público + adaptador de arquivo); `column_map` no mailing; `smoke_outbound_fase4.sh`.
- **Fase 5a — fan-out ✅** (2026-07-22): dispatcher/worker via `workflow_trigger` (skills `skill_outbound_dispatch_v1`/
  `skill_outbound_worker_v1`, pools `outbound_dispatch`/`outbound_worker`); collect lazy sob decisão B; `smoke_outbound_fase5a.sh`.
- **Fase 5b — survey outbound e2e ✅** (2026-07-22): substrato de campanha → `survey_link_create` (origin explícito) →
  `/survey/submit` → `session.signals`. Skills `skill_outbound_survey_{dispatch,worker}_v1`, pools homônimos; `smoke_outbound_fase5b.sh`.
- **Refinamentos 5b (backlog):** `responded` por-delivery (submit → `campaign_delivery_result`); skill de processo que
  auto-alimenta a mailing no `complete` (journey_complete real, hoje seed direto); pertença à journey via `journey_merge` (metadata.origin_session_id).
- **UI (fatia 1b) ✅** (2026-07-22): módulo `outbound` no platform-ui (`/config/outbound`, página com abas
  Mailings/Campaigns/Deliveries + editores de `column_map` e `ordering` + import). ABAC `outbound.{configurar,operacao}`,
  proxy `/v1/(mailings|campaigns)`→3660. Fecha a invariante "UI-editable".
- Design: [`docs/product/outbound-mailing-campaign-design.md`](docs/product/outbound-mailing-campaign-design.md);
  arco: [`docs/arcos/outbound.md`](docs/arcos/outbound.md).

### Histórico de contatos do cliente — capacidade transversal *(spec — §20 de customer-surveys.md)* — **[Segue — fosso / busca `GIN` em Congela]**
- Útil a **qualquer atendimento** (não só survey). **Já existe**: lista por `customer_id` (`GET /analytics/sessions/customer/{id}`, ClickHouse `sessions.customer_id` = `customer_key`) + `HistoricoTab`/`useCustomerHistory` no Agent Assist; transcrição por sessão (`GET /analytics/v1/transcript/sessions/{id}`). **H1 drill lista→transcrição ✅** (2026-07-02, CHANGELOG): `HistoricoTab` liga ao endpoint via `useSessionTranscript` (masked-by-construction, sem `audit_access_log`); exigiu adicionar o proxy `/analytics/*` ao platform-ui (nginx+vite) — o app canônico não o tinha, então lista+transcrição eram inalcançáveis. **H2 busca (backend) ✅** (2026-07-02, CHANGELOG): `GET /sessions/customer/{id}/search` — **decisão v1 = ClickHouse `messages` JOIN `sessions`** (não `sessions_stream`; masked-by-construction, escopo `customer_id`, colocado), substring `positionCaseInsensitiveUTF8`, 1 hit/sessão + snippet mascarado + score, filtros from/to/channel/outcome/pool. **Proposta fechada (2026-07-15, `docs/adr/adr-customer-360-two-surfaces.md`): Cliente 360 em duas lentes** — Console (ao vivo) × Analytics (retrospectivo) sobre os mesmos dados (`customer_id`), com o **Journey fechado** (T1–T6). **Mapa das 4 abas do Console (D1):** **Contexto** (afirmado — ContextStore da sessão, o que persiste é config de pool), **Histórico** (reatribuído — **jornadas em aberto** re-introduzidas + contatos + busca), **Cliente** (NOVA — cadastro manual + 360), **Ações** (inalterada). Jornadas vivem **só no Histórico**; Cliente **linka**. (D2) "jornadas do cliente" = **filtro `customer_id`+`open` no `/reports/journeys`** (reuso). (D3) **cadastro manual** na aba Cliente, v1 = buscar/criar/atacar âncora (reusa Resolvedor Fase A/B; net-new = `GET /identity/customers/search`; merge/`external_refs` = Fase C). (D4) **360 agregado** por `customer_id`: **quality** (Arc 6) + **survey** (`session_signal`) + resumo contatos/jornadas. **Falta**: H3 (busca UI, backend pronto); HJ (jornadas no Histórico); H4-geral (contexto de jornada); C1a (cadastro) + C1b (360); H4-survey **bloqueado** (briefing de retorno não construído); H5 (Analytics por cliente + `GIN`). **Spec**: `docs/arcos/customer-contact-history.md` (H1–H5/HJ/C1).

