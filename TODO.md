# TODO — PlugHub Itens Pendentes

> Itens genuinamente não implementados. Histórico de implementações concluídas em `CHANGELOG.md`.
>
> **Poda:** seções fechadas saem daqui por `infra/scripts/prune_todo_closed.py` (dry-run por
> defeito) — o CHANGELOG é a casa do concluído, e duas casas para a mesma informação é o
> defeito que este projeto evita em toda parte.

---

## Interop com n8n — alvo: eliminar o editor de fluxo local *(decisão de direção 2026-08-17, ver `docs/product/n8n-interop-boundaries-and-seams.md`)*

Medição (575 arquivos de produção, `e2e-tests` **não** contado): ~12% do código é território que o
n8n cobre melhor — `workflow-api`, `skill-flow-worker`, `scheduler-api`, `mailing-api`, importador,
UI de workflows/schedules/outbound, e 7 dos 17 step types. Os 45% de fosso (sessão, conferência,
roteamento, capacidade, canais, qualidade, governança) não têm equivalente em lugar nenhum.

**Decisão de direção:** parar de competir na faixa de 12% e integrar. **Alvo declarado: substituir o
bloco `flow:` do skill pelo n8n e eliminar o editor de fluxo local** — ele não acompanha os editores
de mercado, e restaurá-lo não resolveria isso. O seguro contra dependência não é um editor de
reserva sem investimento, é a **portabilidade** que a plataforma já vende (`skill-extract` passa a
ter o JSON do n8n como alvo).

**O skill NÃO morre — vira envelope de configuração.** `config_params`, `interface_schema`, política
de masking, declaração de perfil e `mention_commands` mantêm a casa; o modelo de slot/`promote`/
`deploy_version` fica **inalterado** (muda só a carga do snapshot). Consequência: skill sem lógica
não precisa de YAML à mão — vira formulário na UI, e o que morre é a **tela de fluxo**, não a
autoria.

**Regra de fronteira:** *n8n toca sistemas; PlugHub toca pessoas.* As três fronteiras
(`journey`/`session`/`segment`) nascem da travessia do channel-gateway, não do código que decidiu
atravessar — por isso o n8n pode ser autor da lógica sem nunca ser autor da fronteira. Único modo de
quebra: workflow n8n contatando cliente por fora da borda.

**Resíduo não-movível — dois itens, e só dois:**

1. **Evidência de execução para avaliação tier-2.** `flow_definition` (esperado) e `pipeline_state`
   (real) são artefatos do engine local; sem eles a avaliação de IA degrada a **grau-transcript** —
   a mesma limitação que o quality-ingest documenta para histórico externo. Exige mapeadores. É o
   item mais caro, e é dano ao fosso, não ao editor. *(Atenua: o DialogForm já é versionado na
   dialog-api, então o conteúdo conversacional — o que a avaliação mais olha — segue versionado em
   casa.)*
2. **Hook de cliente inline** (`side=customer` + `inline`, hoje só o NPS). `_is_workflow_dispatch_entry`
   (`orchestrator-bridge/main.py:1233`) mostra que **todo o resto já migrou** para o veículo de
   workflow. Não é destacável por natureza: destacar fecha o contato e o cliente sai do WS.
   `begin_transaction`/`end_transaction` idem — não-retenção é invariante que um runtime feito de
   retenção não honra.

**Guarda que passa a ser load-bearing:** com o editor de fluxo morto, haverá pressão para empurrar
control-flow para dentro do DialogForm. `adr-dialog-conditional-skip-logic.md` (guarda declarativa
`ask_when`, *não* control-flow) precisa ser defendido — se ceder, o editor de fluxo é reconstruído
dentro do editor de formulário, com uma linguagem pior.

**Quatro costuras:** A (webhook, existe) + E (Kafka Trigger, existe) = fase 0, e **A não serve
sozinho** (sem superfície de resultado, o n8n fica cego); B (n8n como cliente MCP — compartilha o
principal externo com a fase A2 do ADR de A2A); C (n8n como domain MCP server — maior retorno,
pendura na fase B2 do ADR de borda única); D (node/template — fase 5, com sub-workflow template como
precursor barato na fase 0, porque é ali que mora a disciplina de propagação de `root_session_id`).

**Bloqueio de segurança que precede tudo:** `POST /v1/channels/webhook/pool/{pool_id}` é **anônima**.
Com n8n do outro lado vira exposição ativa. A fase 0 não sobe sem fechar.

**Gate empírico antes do ponto sem volta:** instrumentar latência de turno e contagem de travessias
no perfil `workflow` **antes** de migrar o perfil `agent`. O alvo está decidido; o gate responde
"batemos num impedimento?", não "devemos prosseguir?". A estimativa de "5–8 round-trips" é palpite,
não medição.

### Sub-item — promover `skill-flow-service` a pacote de primeira classe

Fica **aqui** e não em item separado: é onde a integração aterrissa, e a promoção e a fase 2 são a
mesma obra.

O runtime de produção dos skills conversacionais é `packages/e2e-tests/services/skill-flow-service/`
— cujo cabeçalho diz *"Thin HTTP wrapper … for E2E testing"* e que é dependência `service_healthy`
do orchestrator-bridge, do mcp-server e da evaluation-api. Complementa o achado da seção seguinte
(que já identifica esse `mcpCall` como o caminho nativo sem interceptação).

| Achado | Evidência |
|---|---|
| O `skill-flow-worker` está morto para MCP, e o código admite | `orchestrator-bridge/main.py:638-639` |
| Ele ainda consome `workflow.events` (produtor vivo em `workflow-api/kafka_emitter.py`), mas posta em `/mcp`, rota que não existe | `engine-runner.ts:131` × mcp-server só expõe `/sse` e `/messages` |
| Mapa de servidores MCP hardcoded com 2 entradas + **fallback silencioso** | `skill-flow-service/src/index.ts:35-38`, `:142-144` — servidor desconhecido vai ao mcp-server-plughub em vez de falhar |
| `agente_contexto_ia_v1.yaml:96` aponta para `mcp-server-crm`, que **não existe** | O erro que aparece é "tool desconhecida", não "servidor não configurado" |
| A tool `invoke` resolve endereço por env `MCP_SERVER_{NOME}_URL`, **não definida em lugar nenhum** | `tools/external-agent.ts:149-159` |
| `tools/list` **não é chamado em nenhum ponto do repositório** | Zero discovery; nomes de tool sempre hardcoded |

Escopo: mover para `packages/skill-flow-service/`, substituir a convenção de env pelo catálogo
`mcp_servers` no config-api, implementar `tools/list` (+ snapshot no slot como detector de drift de
contrato), remover o fallback mudo, e decidir o destino do `skill-flow-worker`.

*Ressalva: "404" e "env ausente" são conclusões estáticas (ausência de rota e de env em todos os
compose), não observadas em execução.*

### Defeitos colaterais achados no levantamento *(independentes do n8n)*

- **`llm_tokens_*` não é emitido no caminho principal.** `emit_llm_tokens` tem um único call site,
  `InferenceEngine.infer()` (`ai-gateway/inference.py:149-161`) = `POST /inference`. O **`/v1/reason`**,
  que é o step `reason` dos skill flows, **não emite**; `/v1/turn` também não. Verificar se alguma
  cota ou relatório de tokens está sendo lido como se tivesse dado.
- **`MCP_PROXY_URL` aponta para serviço inexistente.** `tools/evaluation.ts:803` faz `fetch` em
  `localhost:7422`; não há proxy em nenhum compose; o `catch` só emite `console.warn`.
- **DECR de `hook_pending` não inspeciona outcome** (`orchestrator-bridge/main.py:4952`); o
  tratamento de `suspended` é guardado por `not conference_id` (`:4663`, `:4784`). Não morde hoje,
  mas é borda desguardada.
- **Não foi localizado o guard do engine** que rejeitaria `delegate` fora de sessão webhook — o
  comportamento se apoia em comentário e wiring.
- **`inline` tem dois significados** em `PoolHookEntry` conforme o `side`: conferência quando
  `customer`, máquina destacada quando `agent`.
- **`ai-gateway` não tem shim OpenAI** (`/v1/chat/completions`), mas `providers/openai_provider.py`
  já converte o formato interno *para* Chat Completions na saída — a fachada de entrada é largamente
  reverter isso, e é o que permite ao AI Agent node do n8n usar o gateway sem perder rotação
  multi-conta e fallback.

---

## As chamadas de domínio do agente NATIVO não passam por interceptação nenhuma *(achado 2026-08-13, ao alinhar o audit do `invoke` — ver CHANGELOG)*

O invariante da plataforma diz que **nenhuma** chamada MCP chega a um domain server sem validação de
permissão, injection guard e `AuditRecord`. Estão cobertas duas bordas de três, e a que falta é a de
maior volume:

| Borda | Quem usa | Estado |
|---|---|---|
| `invoke` (mcp-server) | agente `external-mcp` | ✅ desde 2026-08-13 |
| proxy sidecar | agente externo que fala direto com o domain server | ✅ implementado — mas **só roda se o operador subir o sidecar** |
| `McpInterceptor` (SDK, em-processo) | agente nativo | ❌ **nunca instanciado** — existe em definição e em comentários |

O caminho real do agente nativo é o `mcpCall` do `skill-flow-service`
(`packages/e2e-tests/services/skill-flow-service/src/index.ts:149`, o que o orchestrator-bridge
executa) e o do `skill-flow-worker` (`engine-runner.ts:150`, legado): `fetch` JSON-RPC cru. Sem
filtro de `permissions[]`, sem guard, sem registro.

**Por que ninguém notou:** o modo de falha é a AUSÊNCIA de linhas num relatório. Nada fica vermelho,
nenhuma chamada falha; o `mcp_audit_log` simplesmente não tem o que mostrar, e "não houve chamada"
é indistinguível de "não foi auditada" para quem só olha a tela. Foi o que manteve o `invoke`
publicando num tópico órfão por meses.

**Decisão fechada em ADR** (2026-08-13, proposto): a regra mora no `mcp-server-plughub` e o
`mcp_call` nativo passa a atravessá-lo — saída **(b)**. A saída (a), instanciar o `McpInterceptor` no
skill-flow-service, foi descartada por criar uma segunda implementação VIVA do mesmo veredicto, em
outro processo e outro ciclo de deploy; a regra já está escrita três vezes e as cópias já
divergiram (curinga `server:*` e `permissions[]` vazia decidem diferente no `invoke` e no sidecar).

O ADR levanta o ponto que decide de verdade: **borda é fato de rede, não de código**. Enquanto um
domain MCP server for alcançável a partir do processo do agente, qualquer borda é evitável por
omissão — e nada no repositório garante o contrário hoje.

Fases, gates e custos: [`docs/adr/adr-mcp-interception-single-border.md`](docs/adr/adr-mcp-interception-single-border.md).
**Primeiro passo é M0 — medir** o volume por caminho (nativo × `external-mcp`): o número diz se isto
é lacuna de 5% ou de 95% do tráfego, e o argumento LGPD depende dele.

---

## Masking — 5 mecanismos distintos, config espalhada entre seed.py, Config API e YAML *(achado 2026-08-13, ao investigar por que `session.cpf` aparece aberto pro operator no pacote de aprovação de `skill_limite_processo_v1`)*

Levantamento factual (nenhum código mudado por este item). O sintoma que disparou a investigação
não é bug: `session.cpf` simplesmente não tem regra no Mecanismo 2 (nunca teve, nem quando a
âncora era `contact_identifier`/telefone) — mas expôs que "masking" no PlugHub não é UM sistema,
são cinco, cada um com config/enforcement em lugar diferente, e ao menos um (seed.py) não é
editável em produção sem redeploy de código.

**Os cinco:**

1. **Message/Stream masking** (`MaskingService`, `packages/mcp-server-plughub/src/lib/masking.ts`)
   — regex sobre texto livre do cliente, produz token `[cat:tk_xxx:partial]` no `content` da
   mensagem (stream). Config: namespace Config API `masking`, key `rule.{category}` — **editável
   na UI** (`/config/masking`, `MaskingPage.tsx`). Vault: `token-vault.ts`.
2. **ContextStore field-level masking** (`applyContextMaskingDynamic`,
   `packages/mcp-server-plughub/src/server.ts:1054`) — regras casadas por NOME EXATO/glob de tag
   (`session.numero_cartao`, `caller.*`, `*`) × role do visualizador. Config: namespace Config API
   `masking`, key `context_rules` — **também editável na UI** (`ContextRulesSection` na mesma
   `MaskingPage.tsx`), MAS as regras de `session.*` específicas de cada skill (ex. as três do
   pacote de aprovação de limite) são semeadas em `packages/config-api/src/plughub_config_api/
   seed.py:483-513` — ou seja, a INFRAESTRUTURA do mecanismo é UI-editável, mas o CONTEÚDO
   operacional (quais tags cada skill precisa mascarar) hoje só entra via seed.py, que é
   seed-if-absent: uma vez semeado, editar o arquivo é no-op sem reconcile+redeploy. Isto é
   exatamente o padrão descrito em `docs/adr/...` sobre "toda config de negócio tem que ser
   UI-editável" — este mecanismo viola o invariante na prática, mesmo tendo UI.
3. **Masked Input** (`masked: true` em step/campo de menu, namespace `@masked.*`,
   `begin_transaction`/`end_transaction`) — não é masking de EXIBIÇÃO, é ausência de persistência:
   o valor nunca chega a existir em `pipeline_state`/stream/Redis/log. Declarado em YAML de skill
   OU em campo de DialogForm JSON (`infra/dialog/*.json`, ex. `cvv` em
   `dialog_limite_solicitacao.json`). Enforçado em `packages/skill-flow-engine/src/masking-policy.ts`
   + `steps/begin-transaction.ts`/`end-transaction.ts`/`menu.ts`. Consumido pelo webchat como
   `<input type="password">`.
4. **Port Python no quality-ingest** (`packages/quality-ingest/src/plughub_quality_ingest/
   masking.py:mask_text()`) — réplica HARDCODED (não lida de Config API) do Mecanismo 1, porque
   não existe engine de masking em Python no repo. Roda como rede de segurança na importação de
   transcrições externas.
5. **Audit access log** (`packages/analytics-api/src/plughub_analytics_api/audit.py`) — não é
   masking em si, consome o resultado dos Mecanismos 1 e 3. `masked_input_fields` está no schema
   (`audit.ts`) e no guia, mas **não tem escritor** em `mcp-server-plughub` — sempre `[]` na
   prática (Fase 2 pendente, já registrada em `docs/arcos/audit-lgpd.md`).

**O que falta decidir antes de tocar em código** (por isso este item fica só como levantamento):
- Os Mecanismos 1 e 2 já são via Config API/UI — o problema real é só o CONTEÚDO do Mecanismo 2
  nascer em seed.py em vez de nascer por API/UI desde o início de cada skill novo. Precisa de um
  fluxo (no editor de skill? no editor de DialogForm? um novo painel "regras de contexto por
  skill"?) que evite todo skill novo com campo sensível em `delegate.context` precisar de uma
  entrada manual em seed.py.
- Mecanismo 3 é conceitualmente diferente dos outros (não-persistência vs. mascaramento de
  exibição) — não faz sentido "unificar" ele com 1/2, mas vale deixar isso EXPLÍCITO em algum lugar
  (hoje só está implícito no guia `docs/guias/masked-input.md`) para não ser confundido como
  concorrente dos outros dois numa unificação futura.
- Mecanismo 4 é dívida técnica pura (duplicação de regra sem fonte única) — collapse natural seria
  o quality-ingest ler as MESMAS regras via Config API em vez de hardcode, mas isso muda o
  contrato de "produtor puro, sem dependência do resto da stack" do quality-ingest (ver
  `docs/arcos/quality-ingest.md`) — precisa avaliar se vale a pena.

Levantamento completo (paths, funções, formatos exatos) disponível sob demanda — não replicado
aqui para não duplicar código-fonte em prosa.

---

## ⚠️ Erros de método que se repetem — ler antes de atacar qualquer item

Esta seção **não descreve trabalho pendente**. Descreve como se erra aqui, e ficou depois que
uma sessão inteira (2026-08-03) mostrou que o mesmo padrão custa mais que os defeitos.

### 1. Item antigo mente sobre o próprio estado — MEÇA antes de executar

Dos 5 itens atacados em 2026-08-03, **três estavam stale** e **dois eram maiores** do que o
registro dizia:

| Item | O que o TODO dizia | O que a medição achou |
|---|---|---|
| F5 (limpeza de capacidade) | 4 peças pendentes | 3 já feitas **no mesmo dia** em que a nota foi escrita; a peça real (`position_updated` lendo `available_agents`) não estava na lista |
| Pricing → quota Redis | "pricing-api não tem código Redis" | existe desde 2026-06-04; as chaves estavam vivas (`370/360/10`) |
| JWT do BFF | assinatura não verificada | **e** `exp` não verificado — token expirado valia para sempre |
| 87 segmentos órfãos | 87, "os 9 da aprovação são ruído" | eram **107**, e os da aprovação tinham subido para **17** — deixaram de ser ruído e viraram evidência de defeito aberto |

**Regra:** antes de executar, meça o que o item afirma — inclusive os NÚMEROS que ele cita. Em
dois casos a medição mudou a decisão, não só o escopo.

### 2. Hipótese plausível é a que engana — escreva-a COMO hipótese

Duas ficam registradas porque o custo de cada uma foi real e o formato foi o que salvou:

- **`PLUGHUB_AUTH_JWT_SECRET` na analytics-api.** O TODO supunha que os 2 vermelhos de
  `TestPoolPrincipalAuth` vinham da variável ausente no container, e mandava *"confirmar antes
  de tratar como defeito de código"*. A variável **estava definida**. A causa era
  `_with_internal_mirrors` (ADR author-bound D2), que passou a derivar o espelho `-int`. A
  hipótese era plausível, batia com o sintoma (JWT/pool-scoping) e teria levado a mexer no
  compose. *O que salvou foi ela estar escrita como hipótese, com o "confirmar antes" junto.*

- **Limpar antes de medir, nos órfãos.** O TODO propunha `DELETE` direto. Medindo primeiro
  apareceram 9 órfãos PÓS-fix — e a limpeza teria apagado a única evidência de que a lacuna 2
  (sem reaper de `claim_lease`) acontece de fato.

### 3. Verde acidental custa mais que vermelho

Em 4 classes de teste, o conserto do vermelho revelou irmãos VERDES pela mesma causa —
ocupando o lugar da cobertura que faltava. Ao consertar um teste, olhe a **classe inteira**.

### 4. Prever o número antes de rodar

Quatro previsões erradas em 2026-08-03, três delas produzindo resultados **plausíveis**
(`7 passed / 22 failed` por rootdir errado; `9 failed / 13 passed` de uma mutação que não
mutou nada). Sem a previsão escrita, cada uma teria virado diagnóstico em vez de erro.

### 5. O TÍTULO é o que mente para mais gente *(acrescentado 2026-08-03, 2ª sessão)*

Três seções foram achadas com **corpo mantido e cabeçalho não**, na mesma varredura:

| Seção | Título dizia | Corpo dizia |
|---|---|---|
| Capacidade | "implementação não iniciada" | F1, F2, F3a, F4a/b/c, F5, F5b ✅ |
| I5 | "resta o relatório" | relatório = fatias 1 e 2, ambas ✅ |

**Quarta ocorrência, 2026-08-07 — a variante que erra para MAIS.** O I5 dizia *"seguem abertas
2/3/4/6"* com a 3 e a 6 fechadas (a 3 na própria tabela da seção; a 6 pela Fase E), e o corpo dizia
*"Próximo: Fase E"* com E e F concluídas. As três da tabela acima subestimavam o feito e faziam
pular a seção; esta **superestimava o pendente** e faz gastar a sessão re-medindo o que já foi
medido. A regra não muda de forma: *o cabeçalho entra no mesmo commit que fecha a fatia* — inclusive
quando o que fecha é uma FASE citada num parágrafo de "próximo passo".
| Resolvedor de Identidade | "falta Slice 3 + Fase B" | Slice 3 ✅ e Fase B ✅ há um mês |

Atualizar item a item e deixar o título é o modo mais comum de a seção envelhecer, e o mais caro:
**o título é a única linha que se lê no índice**. Quem varre o TODO para planejar nunca chega ao
parágrafo que corrige. Ao fechar uma fatia, o cabeçalho da seção entra no mesmo commit.

### 6. Um portão nunca julga o ALVO quando a falha foi na PRÓPRIA montagem *(2026-08-03)*

O `smoke_approval_segment_closes.sh` v1 mandou o resume sem `tenant_id`, levou 422 — e o passo
seguinte, mesmo tendo marcado o submit como INCONCLUSIVO, **seguiu julgando** e concluiu
*"❌ DEFEITO REAL na aprovação"*. O segmento estava aberto porque ninguém submetera. O vermelho era
convincente e tinha 17 órfãos "confirmando"; levado adiante, custaria a sessão consertando código
correto. **Pré-condição falha ⇒ INCONCLUSIVO e o teste PARA.** Portão que aponta o lugar errado é
pior que portão nenhum — este manda alguém trabalhar.

*Placar do dia, que é o argumento inteiro:* **7 defeitos de instrumento × 1 achado de código real.*
Seis falharam alto e custaram minutos. O sétimo falhou plausível e quase custou a sessão.

### 7. Como um dublê mente *(destilado dos 48 vermelhos de 2026-08-03; seção de origem podada)*

Nenhum dos 48 era defeito de produção, e a causa se repetiu em famílias. As duas que não têm outra
casa permanente ficam aqui, porque valem para a **próxima** suíte que alguém escrever:

- **Dublê de store responde por CHAVE** — nunca com valor único para todas elas, nunca por ordem de
  chamada (`side_effect` posicional). Valor único faz o teste ler o dado do vizinho; ordem faz o
  teste depender de um detalhe que o código pode reorganizar sem quebrar nada. Melhor ainda:
  **derivar o dublê por introspecção** do objeto real, para ele responder à ESTRUTURA e não a uma
  serialização congelada.
- **A alavanca do teste tem de ser a fonte que o CÓDIGO lê** — mexer em `Settings` quando o adapter
  resolve pelo cache do config-api testa uma alavanca desconectada (invariante *"config-api vence"*).
  O teste fica verde porque não alcança o caminho, não porque o caminho está certo.

Detalhe completo das 5 famílias no `CHANGELOG.md` § *"Zero suítes vermelhas: 48 falhas em 6 pacotes → 0"*.

### 8. Um número que PARECE resposta *(2026-08-05 — cinco casos num dia)*

Placar do dia: **5 defeitos de instrumento × 1 achado de código real**, e os cinco tinham a mesma
forma — um número foi produzido, foi lido, e não significava o que parecia. Nenhum falhou alto.

| O que apareceu | O que significava de verdade |
|---|---|
| `238 deselected in 0.37s` | **zero** testes selecionados; `-k` não casou nada, exit 0 |
| `2 passed` (duas vezes) | container ainda com o código VELHO — o arquivo de teste mora na imagem |
| `INCONCLUSIVO` sobre saída correta | o veredicto do probe só sabia REPROVAR (ver abaixo) |
| `grep -c` = 0, duas vezes | `--since` com timestamp sem fuso: janela de log 3 h no FUTURO |
| `presence_at_reclaim = 1` | proxy lido no instante errado — o guard avalia DEPOIS |

**A regra que emergiu, e que funcionou toda vez que foi aplicada:**

> **Um contador de AUSÊNCIA precisa de um contador-TESTEMUNHA de presença ao lado.**

Sem a testemunha, `0` significa *"não aconteceu"* **ou** *"não foi exercitado"*, e não há como
distinguir. Os três pares que funcionaram: `Return to queue` ao lado de `Skipping duplicate`;
`Pool (registered|already exists)` ao lado de `HTTP 401`; roster com participante ao lado de
`ctx_participants`. **O único gate que pegou seu próprio erro de primeira** foi o que já exigia a
testemunha antes de aceitar um verde.

**A pergunta que teria evitado três erros seguidos:** *qual linha de código precisa executar para
este número mudar, e o meu experimento a executa?* Duas vezes propus um gate que media o **produtor**
quando a mudança estava no **consumidor** — e a segunda vez foi na mesma mensagem em que eu advertia
contra a primeira.

**Corolário do §3 (verde acidental), na direção oposta:** um veredicto de três estados pode estar
errado no ramo que **aprova**, e isso não chama atenção porque o estado inútil (`INCONCLUSIVO`)
parece prudência. O probe de janela contava ausências nas duas pontas e exigia "nenhum dos novos
presente" para dar verde — impossível quando a janela passa de metade da fila, porque as duas pontas
se **sobrepõem**. Ele sabia reprovar e não sabia aprovar. **Provar que o veredicto APROVA exige uma
execução esperada-verde**; só a esperada-vermelha não basta.

**Controle negativo por ritual manual não é validação.** Reverter → rebuildar → rodar → restaurar
falhou **duas vezes**, das duas por pular o rebuild. Substituído por um teste **diferencial** (lê o
mesmo dado pelas duas semânticas e afirma que DIVERGEM), que roda em todo build e ainda cobre o caso
que o ritual nunca cobriria: fixture encolhida para dentro da janela faz as duas leituras
coincidirem, os testes de sentido seguem verdes e param de discriminar — em silêncio.

### 9. O pente vale mais que o plano *(2026-08-05)*

Sete achados antigos foram auditados contra o código que executa. **Três já estavam consertados** —
resolvidos de passagem por arcos vizinhos, sem ninguém marcar. Um deles (`§1b`) estava completo
inclusive no bundle servido.

Isso põe número no §1 e no §5: **o custo não é o item ficar aberto, é o registro AFIRMAR que está
aberto**, porque quem planeja em cima dele planeja trabalho que não existe. Antes de abrir uma frente
grande, passar o pente nos achados da faixa de datas correspondente — a taxa foi de 3 em 7, e cada
verificação custou minutos.

*Corolário achado no mesmo dia:* dois itens sem nenhuma referência cruzada podem estar em cadeia. O
**§101** (401 no registry) travava a suíte e2e, que era o único caminho vivo do leitor de `role` do
**§1055**. Um defeito de autenticação a três saltos era o que impedia de validar outro. **Ao fechar
um item, perguntar o que ele DESBLOQUEIA.**

---

## Volume de sessões inexplicado — +167 contatos numa execução de e2e *(observado 2026-08-14, não medido)*

**Observação, não diagnóstico.** Duas leituras de `/analise/sessions` com **a mesma janela**
(07/08→14/08), **mesmo escopo** (`contacts`, toggle desligado) e **o mesmo build**, separadas por uma
execução de e2e:

| | `meta.total_contacts` |
|---|---|
| antes do e2e | **118** (3 páginas) |
| depois do e2e | **285** (6 páginas) |

O e2e deveria criar **uma** sessão com 5 segmentos — no máximo ~10 sessões somando as internas.
Apareceram **+167 contatos**. Ou a suíte rodou muito mais do que um cenário, ou algo produz contato
sozinho. Não há dado para escolher, e as duas explicações têm consequências opostas (uma é operação
normal, a outra contamina toda contagem, TMA e atribuição por pool do ambiente de demo).

**Primeiro corte** — quem são, por minuto de abertura e por pool/canal:

```
SELECT toStartOfMinute(opened_at) AS m, pool_id, channel, count()
FROM plughub_demo.sessions FINAL
WHERE tenant_id='tenant_demo' AND opened_at >= '2026-08-14 18:00:00'
GROUP BY m, pool_id, channel ORDER BY m DESC LIMIT 40
```

Se concentrarem num punhado de minutos e num pool de teste, é a suíte e o item morre. Se estiverem
espalhados no tempo, há produtor ativo — e aí o alvo é *quem* publica `conversations.inbound` sem
contato real. ⚠️ Conferir também `origin`: seed que escreve `origin='live'` já é achado conhecido
(`telas-design` §5) e inflaria exatamente esta contagem.

---

## Segmento que nunca fecha — `participant_left` não publicado na saída por SUPERAÇÃO *(diagnosticado 2026-08-14, não consertado)*

Sintoma na tela: um contato **encerrado** exibe segmento com `live` + `join`, e o cabeçalho diz
`1 active`. A UI está honesta — `SegmentList.tsx:96` deriva `live` de `ended_at === null`.

**Impacto, e por que não é cosmético:** `agent_time_ms` filtra `duration_ms IS NOT NULL`, então
segmento que nunca fecha fica **fora** do tempo de agente; o `join` oferece entrar numa conferência
já destruída; e o contador de ativos mente. Se a espera em fila deve contar como tempo de agente, ela
está sumindo dos agregados; se não deve, então `role='queue'` não deveria ser segmento de agente. As
duas leituras não podem estar certas ao mesmo tempo — decidir isso faz parte do conserto.

**Cadeia medida (`tenant_demo`, sessão `61dd213c…`):**

| Passo | Resultado |
|---|---|
| escopo — segmentos abertos em sessão FECHADA, por papel | `primary` 5/597 · `queue` 2/11 · `specialist` 2/68 ⇒ **9 em 676 (1,3%)** |
| `queue` no tenant inteiro | **14 fechados**, 2 abertos ⇒ o caminho normal FUNCIONA |
| os 9, nomeados | **9 sessões distintas**, `close_reason` variado (`flow_complete` ×6, `agent_hangup` ×2, `customer_abandon` ×1), 2 canais, 6 skills, 5 dias |
| `segments` **sem `FINAL`** | 1 versão por `segment_id` ⇒ **inconclusivo** (merge pode ter comido a anterior) |
| `participation_intervals` | fila com `left_at = ∅` ⇒ **o evento nunca foi publicado** |

**Descartado — não redescobrir:** (a) *"a fila nunca fecha"* — fecha em 14 de 16; (b) *"é específico
do papel `queue`"* — `primary` e `specialist` também têm casos; (c) *"corrida de ordenação entre
tópicos"* — `segments` é escrita pelo par `participant_joined`/`participant_left` do **mesmo** tópico
(`clickhouse.py:376`), não por dois; (d) *"sobrescrita RMT"* — não sustentada: se a linha nunca
recebeu o rewrite E o evento também falta em `participation_intervals`, o fato não existiu.

**Diagnóstico:** o participante de fila nasce da SAÍDA do agente anterior (`sac_ia` sai
`16:09:28.912`, fila entra `16:09:28.965`) e desaparece quando o humano assume (`16:09:41`) — some
por **superação**, não por término negociado, e esse caminho não publica `participant_left`.

**Onde mexer:** produtor do `conversations.participants` (orchestrator-bridge / routing na alocação
que tira o contato da fila). ⚠️ Toca mecânica de conferência ⇒ pelo `CLAUDE.md`, exige atualizar
`docs/guias/conference-mechanics.md` § Histórico de Problemas e Correções **antes** de considerar
concluído. Gate precisa de testemunha: os 14 que fecham têm de continuar fechando.

---

## Ler um processo = ver seus CONTATOS em sequência, num lugar só *(ADR fechado 2026-08-12; **F0 ✅ + F1 ✅ + F1b ✅ + F2 ✅ + F3 ✅** — restam F4, F5)*

> ⚠️ **Cabeçalho corrigido em 2026-08-14.** Dizia *"nada implementado"*, e F0 (`774b257`) e F1
> (`43ab761`) estavam commitadas desde 12-13/08, sem entrada no `CHANGELOG` e sem nada aqui. A sessão
> de 14/08 mediu antes de escrever: gate do `collect` em vigor no container, slot `current` do
> `limite_entrega` executando `type: collect`, gates `5/0` e `18/0`, e o merge provado por
> `journey_aliases`. As-built e os quatro achados no `CHANGELOG.md`; plano das fases restantes em
> [`docs/product/historico-unificado-plano-execucao.md`](docs/product/historico-unificado-plano-execucao.md).
>
> **Decisão aberta #1 FECHADA por ausência:** o `collect` é lazy — sem clique não nasce sessão, logo
> um `collect` expirado não conta como contato porque **não existe**. Em troca, "a perna do output
> como sessão" passou a ser **condicional ao engajamento**, o que o ADR não previa.

> **Desenho fechado em [`docs/adr/adr-historico-unificado-duas-visoes.md`](docs/adr/adr-historico-unificado-duas-visoes.md).**
> Kickoff de F0: [`docs/product/historico-unificado-kickoff.md`](docs/product/historico-unificado-kickoff.md).
> Handoff de origem: `docs/product/journey-contatos-em-sequencia-handoff.md`. O detalhe (D1–D12,
> achados medidos, o que foi rejeitado) vive no ADR; aqui fica só o que está pendente e por quê.

**O que já funciona** (medido 2026-08-12, não reinvestigar): `/reports/journeys?root_session_id=…`
devolve `session_count: 3`; `/reports/sessions?root_session_id=…` devolve as três encadeadas por
`origin_session_id`; a Vista Processos renderiza `ROOT WebChat → análise → entrega`. O agrupamento
por PROVENIÊNCIA está correto ponta a ponta — **não há defeito de journey a consertar.**

**O achado que reordenou tudo.** O pedido virou quatro perguntas — direção do acesso, prova de saída,
perna do workflow, output com confirmação — e nas quatro **o fato já está persistido sem superfície**.
Uma delas está persistida e **desligada por um gate assimétrico**: `handle_collect` não honra
`customer_resumable`/`resume_policy`, embora o schema tenha os campos e o engine os envie (registrado
em `skill_limite_entrega_v1.yaml:41-42`, que por isso parqueia com `delegate`). Fechar esse gate dá,
de uma vez: output-com-confirmação, a perna do output **como sessão**, a direção **outbound**
(`spawn_reason='collect'`) e pertença ao processo **por proveniência**.

Consequência: a pertença se reparte, e só metade precisa de merge.

| Caso | Como entra no processo |
|---|---|
| **Output ativo** — nós avisamos o cliente | `collect` → proveniência, automático (F0) |
| **Acesso espontâneo** — o cliente volta por conta (acesso 2) | `journey_merge` (F1) |

**Fases — ordem revisada em 2026-08-14 (F2 subiu na frente de F1b):**

F2 é a menor, é backend puro sem consumidor a quebrar, e é a **única** que F4 não contorna; F1b só
destrava um filtro de F3 e custa o inventário de ~40 leitores. Não há dependência entre as duas, então
trocar a ordem custa zero.

- ~~**F0 · conserto do gate do `collect`**~~ ✅ **2026-08-14** (código de 12/08). `handle_collect`
  honra `customer_resumable`/`resume_policy` (`webhook.py:1956-2010`); `parquear_resultado` é
  `collect` com `resume_policy: auto` e 168h, promovido no slot `current` do `limite_entrega`.
- ~~**F1 · `journey_merge` no intake**~~ ✅ **2026-08-14** (`skill_limite_entrada_v1.yaml:362-372`,
  step `unificar_journey` no ramo `policy == "auto"`). Provado por aresta ativa em `journey_aliases`.
  **Falta só o intake de PORTABILIDADE** (`agente_portabilidade_intake_v1.yaml`: zero ocorrências de
  `journey_merge`; vai de `avaliar_politica_retomada` direto a `retomar_processo`) — fatia pequena,
  **fora do caminho crítico**. ~~+ endereço de entrada (`endpoint_id`)~~ **REMOVIDO 2026-08-12 — ver F1b.**
- ~~**F1b** · `entrou por`: first-write-wins em `sessions.pool_id`~~ ✅ **2026-08-14**. Critério = menor
  `timestamp` (não ordem de chegada: `inbound`/`routed`/`queued` são tópicos distintos). Fonte única —
  o fallback `_pool` de `_fetch_sessions` foi removido; `_fetch_pools_queue` teve a precedência
  invertida para o segmento de fila (**já estava errado** em 6 de 15 sessões, antes desta fase). ABAC
  **precisou de conserto**, não de conferência: 52 dos 67 contatos sairiam do escopo de 2 usuários
  reais → `_session_scope_clause` (predicado único, união entrou ∪ sem-pool ∪ participou, +9/−0).
  Gates `probe_entry_pool_base.sh` + `probe_entry_pool_fww.sh` (7/0). As-built no CHANGELOG.
- ~~**F2** · `root_session_id` em `/reports/segments`, **com isenção da janela de data**~~ ✅
  **2026-08-14**. Subconsulta em `sessions` (a coluna não existe em `segments`) com o mesmo union-find
  de `/reports/journeys`; `meta.window_applied` marca o ramo isento. Gate
  `infra/test/probe_segments_journey_window.sh` (6/0, diferencial de 4 leituras com janela absurda).
  **Achado 6 medido e descartado**: 723 segmentos com pool, **0** com `pool_id` vazio — o defeito
  derivado do código não tem amostra; `_apply_pool_scope` **não** foi tocado.
  ~~**Dívida aberta junto:** `/reports/journeys` … sem o marcador `window_applied`~~ ✅ **2026-08-14**
  — e ao medir apareceu um **defeito vivo maior ao lado**: `/reports/sessions?root_session_id=`
  **não** tinha a isenção que o CHANGELOG da F2 lhe atribuía (só `origin_session_id` a tinha), então
  o drill de um processo fora da janela devolvia **0** sessões, não "menos". Os dois consertados +
  marcador nos dois endpoints. Gate `infra/test/probe_journeys_window_applied.sh` (7/0).
  **Resíduo declarado:** `session_id` + janela que o exclui segue devolvendo 0, e pela mesma lógica
  ("pedir UM não é listar") também deveria ser isento. Não foi mudado junto porque o
  `probe_segments_journey_window` da F2 usa exatamente esse comportamento como **testemunha** de que
  a janela funciona — mudá-lo derrubaria o discriminador de outro gate. Item próprio: decidir a
  isenção e trocar a testemunha daquele probe na mesma fatia, nunca só a primeira metade.
- ~~**F3** · visão 1 (contatos + chip de processo + direção)~~ ✅ **2026-08-14**. As-built no
  `CHANGELOG.md`; gates `probe_f3_contact_list_contract.sh` (4 ramos) e
  `probe_i18n_contacts_parity.sh` (692 chaves). **Três fatias de BACKEND que o kickoff não previa**
  (`entry_pool_id`; `journey_id`+`journey_session_count`; `attended_pool_ids`) — nenhuma é
  implementável no front, e as duas últimas são pós-passes justamente para não encostar na query
  principal. Saíram: ANI/DNIS (colunas, filtros, i18n, tipos), `AnaliseProcessosPage.tsx`,
  `OriginSelector.tsx`, «Processos» do menu, e o seletor Inbound/Outbound que não filtrava nada.
  **Resíduos abertos por esta fase**, cada um item próprio:
  - **filtro por direção** — o seletor removido era falso; um de verdade é parâmetro novo sobre
    `spawn_reason` em `/reports/sessions`. Não foi contrabandeado na F3.
  - **`/analise/sessions?session_id=…` não é honrado.** Três telas linkam para lá (`WorkItemsPage`,
    `DeliveriesTab`, `SchedulesMonitorPage`) e a página ignora o parâmetro — abre a lista sem o
    recorte. Defeito **anterior** à F3, agora fácil (a página já lê `useSearchParams` para o chip).
  - **medição que ficou por rodar** (não bloqueia a F3): divergência `sessions.pool_id` × pool do
    1º segmento, **partida antes/depois** do deploy da F1b em 14/08. A tela já provou que o ingest
    novo está certo (`sac ia → nps ia` numa sessão que antes exibia entrada `retencao humano`), mas
    falta o número que separa *"resíduo histórico a backfillar"* de *"a F1b tem produtor escapando"*.
    Divergência **depois** do corte > 0 seria furo. Query pronta no histórico da sessão de 14/08.
  - **mais duas páginas mortas**, no mesmo critério que matou as duas removidas: `ContactsPage.tsx`
    (nenhuma rota a monta desde que `/contacts` virou redirect) e `AnaliseContatosPage.tsx`
    (importada em `routes.tsx`, mas `analise/contatos` é `Navigate`). Não removidas junto por
    disciplina de escopo — a F3 já tinha aberto três fatias não previstas.
- **F4** · visão 2 (pivô, árvore/cronologia num componente com toggle, internas dobradas).
- **F5** · `ContextStorePersister` — fase própria, desenho fechado no ADR §3 (mascarado, estado final,
  ctx de processo a cada close, foto inteira).

**Decisões abertas** (ADR §4): ~~`collect` que expira sem engajamento conta como contato?~~ **fechada
2026-08-14, por ausência** · ~~texto do rótulo do chip~~ **fechada 2026-08-14 (F3.3)**, e o achado é
que ele é **condicional** em `meta.window_applied`, não permanente · `uniq(root_session_id)` como
métrica de cabeçalho (lacuna registrada, não fechada).

**A verificar antes de construir** (nenhum destes foi medido):

- o literal que o cliente usa em `messages.author_role` — suposto em D9.
- **`contatos` ≠ `acessos do cliente`, e nada hoje os separa.** Medido em 2026-08-14: das 4 sessões da
  journey de referência só **2** são acesso do cliente (webchat, `spawn_reason NULL`); as outras duas
  são maquinaria (webhook, `trigger`), e `aprovacao_credito` **não** é `purpose=internal`, logo
  `_apply_contact_scope` não a exclui. O cabeçalho de F4 diria *"contatos 4"* para quem nos procurou 2
  vezes. O discriminador de D4 é derivável hoje, sem dado novo — **decidir o texto antes de renderizar.**
- **O tipo de linha "acesso outbound" tem ZERO amostras.** `spawn_reason` só tem dois valores no tenant
  (re-medido 2026-08-14: `NULL` **349** · `trigger` **71**, total 420): nem `collect` nem `delegate`.
  F3/F4 construiriam uma classe de linha que nada no ambiente exercita — mesma armadilha das colunas
  ANI/DNIS. O `delegate = 0` é **não explicado** (o carimbo existe em `webhook.py:1604`); medir só
  quando F4 precisar da classe "interno".
  **F3 seguiu em frente com o ramo escrito e não verificável** — a diferença para ANI/DNIS é que aqui
  o ramo é o DOMÍNIO do campo (não uma coluna a mais na tela), e o custo de não tê-lo seria classificar
  um `collect` futuro como *inbound* em silêncio. Está registrado, não descoberto na revisão.
- **`customer_id LIKE 'sys:%'` — desempate NÃO codificado, e a medição é o motivo.** Previsão: 0.
  Medido: **1 em 420**, e é `webhook`+`trigger`+`limite_entrega`, já classificada como interna pelo
  primeiro ramo da regra. `sys:` ali é consequência de nascer de máquina, não critério independente.
  Reabrir só com população que o exercite.
- **`probe_journey_limite.sh` não pode reprovar na dimensão de F1** — conta por proveniência (disse 3)
  enquanto `/reports/journeys` conta proveniência ∪ alias (disse 4). Se ele for usado como gate de
  journey depois de F1, compra confiança sem dar nada.

**Filtros da visão 1, revisados e medidos (ADR D12b):** `período · canal · entrou por · atendido por`.
O **DNIS saiu e não volta** — endpoint→pool é **1:1** (13 webhook/13 pools, 2 webchat/2 pools), logo o pool
o substitui sem perda; e `whatsapp`/`voice`/`sms`/`email` têm **zero** linhas em `channel_endpoints`. O
**canal fica**: tirá-lo economizaria zero (já está preenchido em todas as sessões) e 4 pools do demo
declaram `[webchat, whatsapp]`, então o pool não o subsume por config — só por ausência de amostra.

---

## O adapter de whatsapp publica o `phone_number_id` DENTRO do campo `pool_id` *(achado 2026-08-14, ao desenhar o carimbo `entrou por`)*

`adapters/whatsapp.py:386-387` faz `pool_id = phone_id` com o comentário
`# pool resolved by routing engine from phone_number_id`. **A resolução prometida não existe:**
`grep -r phone_number_id packages/routing-engine` devolve **zero**. O consumidor trata `pool_id`
como pool literal, não acha, e cai no drop gracioso de `main.py:791`.

Por que só apareceu agora: até a F1b o valor era sobrescrito pelo `routed` antes de chegar a
qualquer leitor — invisível por acidente. Com o carimbo first-write-wins, ele **congelaria** um
número de telefone dentro de `sessions.pool_id`.

**Exposição medida (2026-08-14): ZERO.** `tenant_demo` tem 288 sessões `webchat` e 125 `webhook`,
**nenhuma** `whatsapp` — coerente com o M2 do ADR (zero linhas de whatsapp em `channel_endpoints`).
Por isso **nada foi feito no analytics**: pôr um `if channel == 'whatsapp'` no parser seria mascarar
defeito de produtor e enfiar conhecimento de canal numa camada que não deve tê-lo. O conserto certo
é no channel-gateway — ou o adapter resolve o pool de verdade, ou manda o phone num campo próprio e
deixa `pool_id` vazio.

⚠️ **Gatilho de reativação:** primeira sessão real de whatsapp. `probe_entry_pool_base.sh` bloco 3
conta por canal e é onde isso aparece.

---

## 15 `session_id` existem em `segments` e NÃO existem em `sessions` *(achado 2026-08-14, no contador-testemunha da F1b)*

O contador-testemunha da base acusou **422 sessões com segmento contra 413 linhas em `sessions`** —
um lado do substrato tem sessão que o outro não tem. Nomeados (contar não é identificar): os 15 são
**todos** `pool=sac_ia · role=primary · agent_type=ai`.

Não são conferência/hook — esses compartilham o `session_id` do pai e não criariam id novo. São
segmentos primários de IA sem linha de contato, ou seja: **o contato não aparece em nenhum relatório**
(toda query de sessão parte de `sessions`), enquanto o trabalho do agente aparece em `/reports/segments`.
Qualquer confronto entre as duas superfícies vai discordar em 15.

Não investigado: é anterior à F1b, ortogonal a ela, e entrar nisso teria trocado o escopo da fase.
Primeira pergunta para quem pegar: são antigos (anteriores a algum conserto de ingest) ou o
`sac_ia` produz isso hoje? `min/max(started_at)` desses 15 responde em uma query.

---

## Seeds escrevem substrato de produção sem carimbar `origin` *(achado 2026-08-12, ao medir o histórico)*

`infra/test/seed_deploy_lens_demo.sh:61` e `infra/test/seed_epoch_demo.sh:63` inserem `segments` **direto
no ClickHouse por HTTP**, fora do pipeline de eventos — daí não nascer linha em `sessions` (15 sessões
órfãs medidas). A assinatura é `started_at` em `10:00:00.000` exato, `pool_id='sac_ia'`, `channel='webchat'`.

**O defeito não é a órfã: é que a lista de colunas do INSERT não inclui `origin`**, então as linhas caem no
default e saem como `live`. O discriminador `origin: live|import|reeval` foi construído exatamente para
manter substrato não-produtivo fora dos relatórios, com filtro default `live` na camada de leitura — e o
escritor passa por fora do mecanismo que existe para ele. É o mesmo formato de *"fonte declarativa tem
aplicador separado"*: o mecanismo existe, quem escreve não o usa, e nada fica vermelho.

Efeito medido: `sac_ia` tem 85 contatos reais **mais** 15 sessões sintéticas em `segments`, indistinguíveis
por query. Para a lista de contatos não vaza (as órfãs caem fora ao juntar com `sessions`), mas vaza em tudo
que agrega `segments` sem juntar — incluindo o filtro *atendido por* de D12, se implementado como agregação
em vez de subconsulta.

Conserto barato: os seeds carimbarem `origin`. Vale também um gate que reprove INSERT em tabela de substrato
sem a coluna — senão o próximo seed repete.

---

## `voice.py` chama dois métodos que não existem, e o teste os fabrica *(achado 2026-08-12)*

`adapters/voice.py:236` e `:247` chamam `self._open_session(...)` e `self._route_inbound(...)`. **Não há
definição de nenhum dos dois em lugar nenhum de `packages/channel-gateway`** — nem em `ChannelAdapter`
(`adapters/base.py:28-73`), nem em `VoiceAdapter`. As únicas outras ocorrências estão em
`tests/test_voice_adapter.py:116,118`, que os atribui como `AsyncMock` e depois afirma
`assert_awaited_once()` (`:350-351`).

Ou seja: o teste **cria** o método que a produção não tem e então verifica que ele foi chamado. É o caso
canônico de *"um teste que não pode reprovar é pior que teste nenhum"* — só que agravado, porque não é um
verde por ausência de amostra, é um verde por o próprio teste ter suprido o que faltava.

Consequência esperada: o caminho inbound de voz levanta `AttributeError` ao ser alcançado. Não medido em
runtime — **não há uma única sessão de voz no ambiente**, o que é consistente com a hipótese e é, por si só,
o sintoma que ninguém leu. Antes de consertar, decidir se o alvo é implementar os dois métodos (a
documentação os descreve em `docs/arcos/channel-gateway-multi-channel.md:163,183`) ou reescrever o caminho
sobre os helpers que os outros adaptadores usam.

---

## Workflow trace é assimétrico na proveniência — inclui o PAI, não inclui o FILHO *(achado 2026-08-12, no E2E de tela)*

O *Workflow trace* de `/analise/sessions` para a sessão de ANÁLISE do cenário de limite
(`af64c36b-…-21a1824ad58d`) lista **8 execuções**, e a primeira é `skill limite entrada` no pool
`limite_ia` — que rodou em **outra sessão**, a de intake (`48f7cce5-…`, a raiz). Ou seja: o trace
**atravessa a fronteira de sessão para trás**.

Mas ele **para** em `skill limite processo — resolved` (13:35) e **não** inclui a sessão de entrega
(`f4db86cf-…`, pool `limite_entrega`, 13:35→13:46), que é filha da mesma análise por
`origin_session_id` e carrega a mesma raiz.

**A pergunta em aberto não é "falta a entrega"** — é *por que o escopo é assimétrico*. Se o trace
segue proveniência, deveria seguir nos dois sentidos; se é session-scoped, não deveria mostrar o
intake. Uma das duas leituras está errada, e não sei qual: pode ser que o intake apareça por outro
motivo (o `origin_session_id` da própria sessão, e não uma varredura), o que seria desenho
consistente e não assimetria.

**Medir antes de consertar:** ler a query/endpoint que alimenta o trace e responder "qual é o escopo
declarado?". Só depois decidir. Consertar para "seguir os dois sentidos" sem saber a intenção
transforma uma pergunta em duas.

Contexto: as três sessões estão corretas no dado — `/reports/journeys?root_session_id=…` devolve
`session_count: 3` e `/reports/sessions?root_session_id=…` devolve as três com `origin_session_id`
encadeado. A Vista Processos renderiza a árvore certa (ROOT WebChat → análise → entrega). **Não há
defeito de agrupamento**; isto é só sobre o escopo do trace.

---

## Subida automática falhou uma vez e a causa ficou NÃO PROVADA *(achado 2026-08-12)*

`agent-assist-ui`, `skill-flow-service`, `channel-gateway` e `orchestrator-bridge` deixaram de subir
sozinhos; só ficaram de pé iniciados na mão. Três hipóteses foram medidas e **as três caíram**:
corrida de arranque sem gate de health (o `stop`+`start` subiu tudo), container velho × compose novo
(o compose não muda desde 10/08), e crash-loop por 422 de capacidade dos pools novos do commit
`32f197f` (`RestartCount 0` em todos).

A leitura que sobrou — stack montada aos pedaços (containers de 46 h a 12 h, de `build X` + `up -d X`
serviço a serviço) somada ao botão Start do Docker Desktop, que **inicia containers e não reconcilia a
stack** — explica todas as observações sem inventar mecanismo, mas **não foi testada**: um `up -d`
completo consertou o ambiente e destruiu a evidência antes que os logs fossem coletados.

**Não atacar este item especulativamente.** O que existe hoje é instrumento, não diagnóstico:
`infra/scripts/up.sh` grava `.logs/up-*.log` em toda subida, e os cinco serviços que não tinham
política ganharam `restart: on-failure` (ver CHANGELOG 2026-08-12). **Gatilho de reabertura:** a
reincidência. Quando ocorrer, a ordem é `logs` do container que falhou **antes** de qualquer conserto
— inclusive antes do `up -d` que sabidamente resolve.

Item aberto de verdade, herdado da investigação: **nada verifica que a stack em execução corresponde ao
compose.** Um container criado há dois dias roda a config de dois dias atrás, e o único sinal disso é a
coluna `CREATED` do `ps`, que ninguém lê. Um probe que compare `docker inspect` × `docker compose
config` (env e `depends_on` por serviço) transformaria deriva silenciosa em vermelho — e é o que teria
respondido esta sessão em um comando.

---

## Tabela construída como duas grids irmãs — 2 telas abertas *(achado 2026-08-11, ao consertar `/analise/wrapup`)*

O cabeçalho é um `<div grid grid-cols-[...auto...]>` e **cada linha de dado é outro**. Grids irmãs não
compartilham trilha: `auto` dimensiona pelo conteúdo *daquela* grid, e os conteúdos são de naturezas
diferentes — palavra no título, dígito no dado. As colunas divergem **por construção**, e o `1.4fr` da
primeira coluna, absorvendo sobras diferentes em cada grid, espalha o cabeçalho e espreme os números.

Corrigido em `analise/WrapupSummaryPage.tsx` (virou `<table>`, ver CHANGELOG 2026-08-11). **Abertos:**

| arquivo | linhas | por que ainda não saltou |
|---|---|---|
| `work-items/WorkItemsPage.tsx` | :88 / :352 | `1.2fr_auto×5`; os dados são mais largos, então o desvio é pequeno — **não é ausência de defeito, é ausência de sintoma** |
| `schedules/SchedulesMonitorPage.tsx` | :101 / :108 | `1fr_1fr_auto_1.4fr`; só uma coluna `auto`, o resto é `fr` (que não depende do conteúdo) |

**Não trocar `auto` por px fixo** — conserta a foto e não a causa: o primeiro rótulo traduzido mais
longo (pt-BR costuma ser) reabre o defeito sem nada ficar vermelho. A saída é `<table>`, que é também o
padrão dominante do platform-ui (46 usos / 31 arquivos). Regra registrada em
`docs/arcos/platform-ui.md` § "What never to do".

---

## `fila_humano` está declarado `agent_kind: ai` — nome e tipo discordam *(achado 2026-08-11)*

`infra/registry/tenant_demo.yaml` :168-169. **Meça antes de tratar como erro de digitação**: se o pool
tem `queue_config`, o agent-registry recusaria `ai` (`POST/PUT /v1/pools` valida *"queue_config exige
agent_kind 'human'"*) — então ou não tem fila, ou o tipo é deliberado e o **nome** é que mente.

Por que importa agora: desde 2026-08-11 o seletor de presença do Console esconde pool `'ai'`. Hoje o
`fila_humano` não está entre os pools acessíveis do admin do demo, então nada acontece. No dia em que
alguém der acesso dele a um humano, o pool **some da lista** e a explicação não estará no nome — o
sintoma vai parecer permissão, e a causa é tipo.

---

## ~~`Pool registration returned HTTP 401` no login do agente humano~~ ✅ **2026-08-05** *(achado 2026-08-04, na validação da Fase E)*

> **Conserto:** o `POST /v1/pools` do login (`mcp-server/server.ts`) passou a mandar `x-service-token`
> a partir de `AGENT_REGISTRY_SERVICE_TOKEN` — o **mesmo padrão, no mesmo processo, da mesma env**
> que `tools/deploy.ts` já usava, e que o container já recebia (`docker-compose.demo.yml` §619).
> Omitido quando vazio, porque sem `service_token`/`jwt_secret` no destino o gate é no-op.
>
> **A pergunta que o item mandava fazer primeiro — *"o que grava, e quem lê"* — tinha resposta no
> comentário logo acima da chamada:** persiste o pool no PostgreSQL do Registry porque o reconciliador
> do `InstanceBootstrap` **apaga `pool_config` do Redis que não esteja no Registry**. Logo não era
> candidato a remoção: o fallback em Redis competia com um processo que o desfaz a cada 5 min.
>
> **Medido** (previsão escrita antes, com contador-testemunha ao lado): `returned HTTP 401` = **0** e
> `Pool (registered|already exists)` = **3** (um por pool do login). O segundo existe porque `0`
> sozinho é indistinguível de "o login não rodou".
>
> **De passagem:** o ramo de erro passou a logar o **corpo** da resposta e `token=enviado|AUSENTE`.
> Era a falta disso que mantinha o item aberto — o TODO pedia "capturar a URL e o status body" como
> primeiro passo, e um status nu não separa credencial errada de variável não entregue.
>
> **Efeito colateral útil:** este 401 é o mesmo que trava a suíte e2e contra o demo (ela morre no seed
> em `POST /v1/skills`). O conserto aqui **não** a desbloqueia — o `http-client` do e2e não tem
> suporte a service token —, mas identifica exatamente o que falta lá: uma env + o header, no
> `lib/http-client.ts`. Isso destravaria o cenário 10, único exercício vivo do leitor de `role`
> (§1055 Fatia B).
>
> **Ponta fechada em 2026-08-05** (ver CHANGELOG § "O runner e2e ganhou credencial de serviço"): o
> `RegistryClient` manda o header, a env entrou no `e2e-runner` do compose, e o gate diferencial
> rodou nos dois ramos. **A cadeia, porém, NÃO fechou:** o cenário 10 ainda não roda, barrado por um
> defeito de outra natureza logo adiante — ver § "Fixtures do e2e ainda falam AgentType". O leitor de
> `role` do §1055 segue **não-exercitado**.
>
> **Duas correções de método que este item rendeu.** (1) *O header no código é no-op sem o
> aplicador*: a env faltava no `environment:` do `e2e-runner`, e sem ela o 401 continuaria — só que
> com uma anotação bonita dizendo `AUSENTE`. Mesmo padrão de skill YAML, slot e seed do config-api.
> (2) *A receita de contorno virou receita canônica sem ninguém decidir*: rodar o runner no HOST com
> `6380/9093` era gambiarra de porta; existe serviço `e2e-runner` no compose, com URLs internas e
> `tenant_demo`. Vale a pergunta ao herdar qualquer receita de handoff — **isto é o jeito certo ou é
> o contorno de alguém?**

## Fixtures do e2e ainda falam AgentType — **seed base ✅ 2026-08-05; sobram 3 bolsões**

**Fechado nesta passada (saída (a), a que o item recomendava):** removido o bloco morto de
`createAgentType` do `seedBaseFixtures` e migradas **23 call sites** de `"agente_retencao_v1"` →
`"skill_retencao_oferta_v1"` em 11 arquivos (01, 02, 03, 04, 06×5, 07×6, 08×3, 09, 10, 11,
regressions×2), mais os 2 comentários que citavam o id antigo. Ver CHANGELOG.

**Três medições que corrigem o texto original abaixo — ler antes de reaproveitá-lo:**

1. **A contagem estava baixa.** O item dizia "~15 chamadas em 9 arquivos"; são **23 em 11**. Duas não
   são `agentLogin`: `06:116` e `07:198`/`07:295` são `agentJoinConference` (a tool **não** valida
   contra o registry — passa o id adiante como identidade do participante IA), e `08:59` é o campo
   `agent_type_id` de `outbound_contact_request`.
2. **O escopo do dano estava subestimado.** O item lia como "o cenário 10 está barrado". `runner.ts`
   §363 chama `seedBaseFixtures` para **todo** cenário com `needsRegistry` (todos fora de 13/14/16) —
   logo o 404 parava a suíte **inteira**, e o "cenário 10" era só o que estava na linha de comando.
3. **A migração não custou os `pools` porque eles já não existiam.** O bloco morto declarava
   `pools: ["retencao_humano"]` e `max_concurrent_sessions: 2`, mas o cliente HTTP de produção
   (`mcp-server/infra/registry-client.ts` §69-72) devolve `pools: []` e `max_concurrent_sessions: 1`
   **fixos** — config por-agente mudou para o slot de deploy do pool. `agent_ready`
   (`runtime.ts` §271-275) itera lista vazia e **não inscreve a instância em pool nenhum**.
   *Consequência que vale mais que o item*: qualquer cenário e2e que dependa de o **routing alocar**
   uma instância logada por essa via está quebrado — por uma causa anterior e independente desta.
   Ainda não medido quais são; o 10 não é um deles (semeia `session:meta` à mão).

**Os três bolsões que sobram** (nenhum bloqueia a suíte hoje):

- **`seedPerfFixtures` (cenário 05)** — 50 `createAgentType` na rota morta, E **a função não é chamada
  por ninguém**: `runner.ts` §90 importa só `seedBaseFixtures`. O cenário 05 monta `agent_perf_{i}_v1`
  a partir de fixtures que nunca foram semeadas em execução alguma. Remover o código morto e migrar o
  05 são a mesma decisão.
- **`15_instance_bootstrap.ts`** — `listAgentTypes()` (§78) é a **espinha** do cenário: ele deriva
  `instance_id` de `agent_type_id`+`max_concurrent_sessions` e confere pertencimento a pools a partir
  do registro. Não é renomear chamada; é reescrever o cenário sobre skills+slots. Por isso o método
  sobreviveu no `http-client`, agora com aviso de rota aposentada.
- **`fixtures/seed_demo.ts`** — script standalone (`ts-node fixtures/seed_demo.ts`), 2 `createAgentType`
  (`orquestrador_demo_v1`, `agente_suporte_humano_v1`). Morre no primeiro `await` desde que a entidade
  saiu. Ninguém reclamou, o que sugere que ninguém o roda.

**Um comentário que mentia foi corrigido** (`lib/http-client.ts` §68): dizia que "`/v1/agent-types`
NÃO é gateado — só pools e skills precisam da credencial". Verdadeiro e enganoso ao mesmo tempo: não é
gateado porque **não existe**. Descrever rota inexistente pelo que ela não exige sugere que funcione
sem credencial. Mesma família do §57 ("o TÍTULO é o que mente para mais gente").

<details><summary>Texto original do item (2026-08-05) — mantido para rastreio</summary>

Com o 401 resolvido, o seed avança e morre em `POST /v1/agent-types → 404 Cannot POST`
(`fixtures/seed.ts:94`). **Não é permissão: a rota não existe.** Zero ocorrências de `agent-types` em
todo o `agent-registry/src`; o `app.ts` §43-49 monta pools, skills, instances, channels,
channel-endpoints e operational, mais nada. A decisão está nomeada em
`registry_syncer.py` §293 — *"AgentType entity retired (Fase 3d/C)"* — e o mundo novo está em
`mcp-server/infra/registry-client.ts` §42: **a identidade de um agente É o `skill_id` deployado**, e
`agent_login` resolve por `GET /v1/skills/{id}`.

**A camada de fixtures ficou meio migrada, e dá para ver a costura.** O seed cria três skills (vivas)
e dois agent-types (mortos). Nos cenários, `09` e `11` já logam o avaliador como `skill_avaliacao_v1`
— e o comentário §54-59 do próprio `seed.ts` documenta essa migração, feita quando o gate de
`agent_role` entrou. A identidade do **executor** ficou para trás: `"agente_retencao_v1"` aparece em
~15 chamadas de `agentLogin` em 9 arquivos (`06`, `07`, `08`, `09`, `10`, `11`, `regressions`, mais
`01`–`04` em chamadas multi-linha não conferidas).

**Consequência**: remover só o bloco morto do seed não basta — o cenário 10 passaria a morrer no
login com `agent_type_not_found`, porque não existe skill chamada `agente_retencao_v1`.

**Duas saídas, e a barata é a errada.** (a) Migrar as identidades para `skill_id`
(`skill_retencao_oferta_v1`), removendo o bloco morto: é o conserto certo, coerente com o que `09`/`11`
já fizeram e com a convenção `skill_{slug}`, ao custo de ~15 edições. (b) Semear uma skill chamada
`agente_retencao_v1`: uma linha, zero churn — e grava um nome com forma de `agent_type` como
`skill_id`, consertando o sintoma e deixando a dívida **com aparência de resolvida**. Preferir (a).

**Antes de atacar, conferir contra o código que executa** (a lição de 2026-08-05, que rendeu 3 de 7):
este item nasceu de leitura de fonte, não de execução. Confirmar em especial se `01`–`04` usam mesmo
`agente_retencao_v1` nas chamadas multi-linha, e o que `regressions.ts` espera.

**O que isto desbloqueia:** o cenário 10 e, com ele, o único exercício vivo do leitor de `role`
(§1055 Fatia B) — a mesma cadeia que o §101 abriu.

Visto no log do mcp-server em **todo** pool do login do Console, seguido de sucesso:

```
[agent-ws] Pool registration returned HTTP 401: pool=formfill_demo
[agent-ws] Human agent registered: instance=human-bef14526-… pools=retencao_humano,aprovacao_deploy,formfill_demo status=ready
```

O registro **degrada e segue** — e loga o motivo, que é o comportamento certo pela postura de
engenharia. Mas um 401 é falha de **autenticação de serviço**, não estado normal: alguma chamada de
registro de pool está indo sem `x-service-token` (ou com um que o destino não aceita), e o caminho
que "conserta" a ausência é justamente o que impede de notar.

**Não investigado, e por isso não afirmado:** qual endpoint devolve o 401, o que ele registraria se
tivesse sucesso, e se alguma coisa depende disso mais tarde. O login funciona, o claim funciona e a
Fase E foi validada com ele presente — então não é bloqueante, e misturá-lo com o arco de requeue
seria confundir dois defeitos.

**Primeiro passo quando for atacado:** capturar a URL e o status body (`console.error` no ramo do
401 já basta), e responder *"o que este registro grava, e quem lê"*. Se a resposta for "nada que
alguém leia", o item vira remoção da chamada — não conserto do token.

</details>

> **Nota de arquivo (2026-08-05):** os últimos 5 parágrafos do texto original acima
> (`[agent-ws] Pool registration returned HTTP 401`) **não são deste item** — falam do registro de
> pool no login do Console, que é outro assunto e tem seção própria já fechada no topo do arquivo.
> Chegaram aqui por colagem. Não confundir com o 401 do seed do e2e, que era em `POST /v1/skills` e
> está resolvido.

## ~~`agent_ready` não inscreve instância em pool nenhum — e o SET que ele escreve não tem leitor~~ ✅ **RESOLVIDO 2026-08-05 — saída (a), vestígio removido** *(item nascido 2026-08-05, ao destravar o e2e)*

> **Fechado no mesmo dia em que nasceu, e a medição é o que decidiu.** As seis
> previsões do `infra/test/probe_pool_registration.sh` foram escritas antes de rodar e
> saíram todas certas: 37 chaves `:pool:*:instances` vivas · **0** chaves
> `:pool:*:available` · 2471 `SADD` em `:instances` numa janela de 150 s, **todos de um
> único cliente** (172.20.0.35 = orchestrator-bridge; routing-engine e mcp-server mudos)
> · **0** `SADD` e **0** comandos de qualquer tipo sobre `:available`. Removidos: o
> `sadd`, os quatro `srem`, o helper `keys.poolAvailable`, o ramo
> `if (currentSessions >= max)` do `agent_busy` (cujo corpo era só o `srem`), a asserção
> do cenário 01, o `getPoolAvailableAgents` do e2e e a entrada de
> `docs/modelos-de-dados.md` que prometia um leitor no Routing Engine. O dublê do
> `runtime.test.ts` passou a declarar a forma que o cliente real devolve
> (`pools: []`, `max_concurrent_sessions: 1`, `permissions: []`). Detalhe e o que
> **não** foi substituído (e por quê) no CHANGELOG.

<details><summary>Registro original do item (mantido: é o raciocínio que a medição testou)</summary>


**Como apareceu:** com o seed destravado, o cenário 01 rodou pela primeira vez e deu 10 verdes e
**um** vermelho, exatamente onde a previsão escrita antes dizia que daria:

```
✗ Pool retencao_humano contains instance after agent_ready   {"available":[], …}
```

**Três fatos, medidos, em ordem de gravidade crescente:**

1. **O caminho não pode popular o SET.** `agent_ready` (`tools/runtime.ts` §271-275) itera a lista
   `pools` do hash da instância, escrita no `agent_login` a partir de `agentType.pools`. O cliente
   HTTP de produção (`infra/registry-client.ts` §71) devolve `pools: []` **sem ramo algum** — não
   existe `skill_id` que produza outra coisa. Junto vêm `max_concurrent_sessions: 1` e
   `permissions: []`, igualmente fixos. Logo o laço é vazio **sempre**, para qualquer agente.
2. **O SET não tem leitor.** `{tenant}:pool:{p}:available` tem, em todo o `mcp-server-plughub`,
   **um `sadd` e quatro `srem`** — e nenhuma leitura fora de teste. As leituras de pertencimento em
   produção vão para `pool:{p}:instances` e `pool_roster:{p}`; o routing-engine sequer conhece a
   chave (zero ocorrências de `:available` em `.py`). É a resposta de *"o que grava, e quem lê"* —
   a mesma pergunta que fechou o §101 e a lacuna 4 — e ela é **ninguém**.
3. **O teste unitário é verde por causa do dublê.** `runtime.test.ts` §158-159 afirma
   `sismember == 1` em dois pools; passa porque `createStubRegistryClient` devolve
   `pools: ["retencao_humano","retencao_bot"]` — **uma forma que o cliente real não consegue
   retornar**. O stub não simplifica a produção: contradiz. Vale para `max_concurrent_sessions: 2`
   e `permissions` no mesmo objeto. É o §7 ("como um dublê mente") num teste que ninguém suspeitava.

**O que NÃO está provado, e por isso não afirmado:** que isto seja defeito de produção. A hipótese
concorrente, e mais provável, é que a inscrição em pool **deixou de ser** do `agent_ready` e passou
ao slot de deploy do pool + bootstrap (o próprio comentário do `registry-client` §44-46 diz que
"config por-agente vive no slot do pool"), e que o `sadd` + o SET + a asserção do 01 sejam **vestígio
do modelo anterior**. O demo aloca agentes normalmente, o que sustenta a hipótese. Não foi medido
qual caminho popula o pool hoje para IA.

**Por isso a decisão não é "consertar o vermelho".** São duas saídas de sentidos opostos, e escolher
exige a medição acima: (a) se é vestígio → **remover** o `sadd`, o SET e a asserção do 01, e trocar o
stub por um que respeite a forma do cliente real (o que provavelmente deixa outros testes vermelhos —
e esse vermelho é o resultado, não o obstáculo); (b) se algum consumidor real depende do SET →
o defeito é o `pools: []` fixo, e o conserto é no cliente. **Não fazer (a) sem medir**, porque
apagar o SET certo pelo motivo errado dá o mesmo diff com a dívida escondida.

**Primeiro passo:** responder *"quem inscreve uma instância de IA num pool hoje, no demo"* — com
`MONITOR` filtrado por `SADD` durante um boot do bridge, não por leitura de código (a lição de
2026-08-05, que rendeu 3 de 7).

</details>

## `source` do resume é asserido pelo CLIENTE na porta pública *(achado 2026-08-04, ao implementar a Fase F)*

`_terminal_cause` (channel-gateway `adapters/webhook.py`) decide entre `task_done`, `acw_expired` e
`acw_supervisor_closed` lendo `payload["source"]`. Os gatilhos internos escrevem esse campo
server-side (o tool marca `agent`, o scanner `timeout_scanner`, o endpoint do supervisor
`supervisor:{sub}`) — mas o `POST /v1/channels/webhook/resume/{token}` **repassa o `payload` do corpo
verbatim**. Um chamador externo pode declarar `source: "supervisor:x"` e obter o carimbo
`acw_supervisor_closed` no segmento.

**A exposição é anterior à Fase F** — a expressão inline lia o mesmo campo — mas a F a tornou
**durável**: a causa agora também vai para `{t}:resume_terminal:{token}`, que vive 25 h e é o que
nomeia a recusa do próximo. Uma causa forjada deixou de ser efêmera.

**Por que NÃO foi fechado junto:** o conserto óbvio (rebaixar `source` quando não há principal
verificado) depende de `_resolve_approver_principal`, que ainda não foi lido. No caminho genérico de
form-fill o `resume_required_abac` devolve `None` — se disso resultar `approver is None` também para
um supervisor legítimo, o downgrade cego **derrubaria o expire do supervisor**. E esse caminho
`acw_supervisor_closed` tem **0 ocorrências** no demo (medido): ele não reclamaria. Fechar no escuro
seria trocar um defeito silencioso por outro.

**Primeiro passo:** ler `_resolve_approver_principal` e responder *"um Bearer de supervisor sem ABAC
exigida produz principal?"*. Só então decidir entre rebaixar no endpoint ou exigir o header. Validar
com `INSTANCE=human-<user_id> bash infra/test/smoke_acw_expire.sh`, que é o único jeito de exercitar
o ramo reivindicado do supervisor.

## I5 — encerramento de trabalho author-bound *(**sem defeito vivo desde 2026-08-07**; núcleo A+B ✅, relatório fatias 1–2 ✅, arco de duplicação/afinidade A–F ✅, lacunas 3/4/5/6 ✅. Resíduo NÃO é defeito: fatia 3 e lacuna 2 estão **gated em evidência que manda não construir**; sobram 3 timeouts constantes + o portão de deriva do seed, ambos de consolidação de config)*

> ⚠️ **Cabeçalho corrigido em 2026-08-03.** Dizia *"resta o relatório"*, e o relatório está pronto:
> fatia 1 (Monitor › Pendências) ✅ e fatia 2 (Analítico › Histórico de Wrap-up) ✅, ambas em
> 2026-07-30. O que resta é a **fatia 3**, e ela não é "pendente" no sentido usual — está
> **explicitamente gated em medição** (*"não construir sem medir"*), com o gate já rodado uma vez:
> `unfilled_rate` 22,2%, e os 2 vencidos eram **reivindicados**, não nunca-reivindicados. Ou seja: a
> evidência disponível diz para NÃO construir. Ler "resta o relatório" no índice sugeria o oposto.
>
> **Corrigido DE NOVO no mesmo dia**, e o segundo erro é mais sutil que o primeiro: o cabeçalho
> passou a dizer *"resta **só** a fatia 3"* enquanto a tabela § Lacunas, 100 linhas abaixo, listava
> **cinco** lacunas abertas — nº 2 (reaper), 3 (TTL de fila não alcança pull), 4 (parcial), 6 (enum
> de `close_reason`). "Resta só" é uma afirmação de completude, e ela não era do autor do título
> para dar: quem varre o índice conclui que a seção está a um item de fechar. *O erro nº 5 tem uma
> forma pior que o título desatualizado — o título que resume a MAIS do que sabe.*
>
> **Terceira correção — 2026-08-07, e desta vez o título errava para o LADO OPOSTO.** Ele listava
> *"seguem abertas 2/3/4/6"*, e conferido contra o código: a **3** já estava marcada fechada na
> própria tabela desta seção (2026-08-05, sem código); a **6** foi fechada pela **Fase E**
> (`_TRANSPORT_TO_SEGMENT_CLOSE_REASON`, `orchestrator-bridge/main.py:3256`, separado do de contato
> em `:3194`, com `test_segment_close_reason_domain.py` assertando não-sobreposição); a **2** está
> meio-fechada desde 08-03 e teve a outra metade *reenquadrada pela medição*; e a **4** perdeu o
> `force-complete` em 08-05. O corpo, mais abaixo, ainda dizia **"Próximo: Fase E"** — com E e F
> concluídas em 08-04 e registradas no `CLAUDE.md`. *Título que superestima o pendente custa
> diferente do que subestima: ele não faz ninguém pular a seção, faz gastar a sessão re-medindo o
> que já foi medido. Foi o que quase aconteceu ao escolher o próximo item por esta linha.*

Fase final da ADR [`adr-internal-work-queue-author-bound`](docs/adr/adr-internal-work-queue-author-bound.md).
**Núcleo A+B entregue** (ver CHANGELOG): ledger `{t}:work_task:{session}`, `Router.work_task_expire`
+ `POST /v1/work_queue/expire`, expire em todo resume, gatilho de supervisor no BFF, TTL do JSON da
fila alinhado ao prazo, três `close_reason` distintos. Smoke `infra/test/smoke_acw_expire.sh`.

### Falta

- **Relatório de pendências por agente** — **desenho fechado 2026-07-30** (ADR § D7b); **fatia 1
  entregue 2026-07-30**, fatias 2 e 3 pendentes. A lacuna 5 deixou de bloquear: o **ledger
  `{t}:work_task:{session}` da I5 é o índice de
  pendência por construção** (nasce no despacho, morre no resume; o claim NÃO o apaga → cobre as duas
  formas com uma linha só) e carrega `assigned_to`.

  | Fatia | Entrega | Estado |
  |---|---|---|
  | 1 | **Pendências agora** — `GET /api/work_queue/pending` no BFF (`SCAN` do ledger + cruzamento ZSET/lease/`dispatch_mode`) + **Monitor › Pendências** (`/monitor/work-items`), agrupável por agente ou pool, com o encerramento pelo supervisor ligado | ✅ **2026-07-30** (smoke 11/11; ver CHANGELOG) |
  | 2 | **Histórico do caso reivindicado** — `GET /reports/wrapup-summary` + **Analítico › Histórico de Wrap-up** (`/analise/wrapup`), agregado por agente/pool com `unfilled_rate` | ✅ **2026-07-30** (sonda 7/7; ver CHANGELOG) |
  | 3 | **Histórico do nunca-reivindicado** — evento `work_item.expired` → ClickHouse. **Gated:** só se a fatia 1 mostrar volume. Nas medições da Camada F quase toda expiração foi de item reivindicado | **não construir sem medir** |

  **Primeira medição do gate (2026-07-30, sonda da fatia 2):** 9 wrap-ups no período — 7 submetidos,
  2 vencidos, `unfilled_rate` **22,2%**. Os 2 vencidos são **reivindicados** (têm segmento, senão não
  apareceriam nesta contagem), o que reforça o achado da Camada F e mantém a fatia 3 fora de escopo.
  O que ainda não se mediu é o **nunca reivindicado** — que por construção não aparece aqui; esse
  número só sai olhando o Monitor › Pendências dentro da janela de 25 h.

  **Escopo da fatia 1 — só wrap-up, e por quê.** O ledger é genérico (`_write_work_task`
  é incondicional nos DOIS handlers de delegate, e o próprio docstring assume pool push),
  então ele indexa também aprovação e delegate a especialista IA. A tela corta pelo sufixo
  `-int`, que a **D6 tornou garantia por construção** (o registry rejeita criação manual com
  ele). O critério não é arbitrário: aprovação é **pooled** e tem transbordo por
  `fallback_to_pool_after_s` — ninguém fica preso nela; wrap-up é **author-bound** e sem
  transbordo, que é a razão de a D4 pedir o relatório. `?all=1` derruba o filtro para
  diagnóstico.

  **Quatro estados, e o quarto é o achado.** `unclaimed` (no ZSET) · `claimed` (lease) ·
  `not_queued` (pool push) · **`orphaned`** — pool *pull*, fora do ZSET e **sem lease**, isto
  é: a lease venceu e nada devolveu o item à fila. É a **lacuna 2** (não há reaper de
  `claim_lease`), que a Camada F deixou sem instrumento. Colapsá-lo em `not_queued` o
  esconderia atrás de um valor plausível. Há ainda `unknown` para pool sem `pool_config` no
  cache — ausência de infra não é presumida como "push". Se `orphaned` aparecer com volume, a
  discussão do reaper passa a ter número.

  **Não criar segmento sintético** para o item nunca reivindicado: nenhum valor de `duration_ms` é
  honesto ali (`0` dilui o ACW que a E2f fez existir; a janela de pendência vira tempo de trabalho;
  `NULL` queima a assinatura que achou os 87 órfãos). Segmento = participação; pendência = item de
  trabalho, com dono/prazo/tempo parado que segmento não comporta. Discussão completa no ADR § D7b.

  **Achados da fatia 1 — dois limites que a tela declara em vez de esconder:**

  1. **O relatório é uma JANELA de ~25 h, não um acumulado.** O ledger nasce com
     `ex = timeout_hours*3600 + 3600` (`webhook.py:1012` e `:1787`) — 25 h no wrap-up default.
     No caminho normal a linha morre antes disso, no resume (o `handle_resume` apaga o ledger),
     e o buffer de +1 h existe justamente para o TTL não ganhar do timeout scanner. **Mas se o
     scanner não passar** (serviço fora, ou o intervalo de 60 s), a pendência **desaparece da
     tela sem deixar registro nenhum** — nem em `segments`, porque item nunca reivindicado não
     tem segmento. Consequência para o gate da fatia 3: "medir antes de construir" significa
     *olhar a tela e anotar*, não *deixar acumular*; nada acumula.
  2. **O nome do agente depende de um grant que o público da tela pode não ter.** `assigned_to`
     é `user_id` (derivado de `human-{uid}`, `main.py:1517`), e `/auth/users` exige ABAC
     `config.usuarios` (strict, sem bypass de admin) — que o supervisor típico não tem. A tela
     degrada para o `user_id` cru **exibindo o motivo**, em vez de mostrar UUID sem explicação.
     A alternativa Redis (`{t}:instance:human-{uid}` → `user_login`) foi **descartada**: aquela
     chave é heartbeat de 30 s e some no logout, ou seja, falharia exatamente na linha mais
     interessante — a pendência de quem já saiu. Conserto real (se incomodar): ou um endpoint
     de diretório mínimo com grant próprio, ou carimbar `user_login` no ledger no despacho
     (mudança de produtor, só vale para itens novos).
- ~~**Bloco C da sonda de prosa**~~ ✅ **2026-07-31** — exercitado (4/4, resolvido E não-resolvido).
  Ver CHANGELOG: a sonda tinha **dois defeitos que a impediam de reprovar**, corrigidos antes da
  medição.
- ~~**Cenários `claimed` e `orphaned` do relatório**~~ ✅ **2026-07-31** — rodados com
  `INSTANCE=human-<user_id>` de agente logado: **14/14**. `claimed` (fora do ZSET, com lease,
  `claimed_by` correto) e `orphaned` (lease apagada sem re-enfileirar) foram **vistos acontecer**. O
  estado `orphaned` deixa de ser instrumento não calibrado e passa a valer como medida da **lacuna 2**.
- **Validação ao vivo do gatilho de prazo.** O smoke exercita o gatilho de supervisor; o de prazo
  depende do scanner de 60 s. ✅ o prazo virou config do pool (`PoolHookEntry.context.acw_timeout_hours`
  → `@ctx.hook.acw_timeout_hours`), então encurtá-lo para medir na Camada F é edição de pool via PUT,
  sem tocar em skill nem em slot.
- **Cenário reivindicado no smoke** — só roda com `INSTANCE=human-<user_id>` de um agente logado.

### Lacunas do levantamento que seguem abertas

| # | Lacuna | Evidência |
|---|---|---|
| 2 | **Não há reaper de `claim_lease`** — ~~vaga presa~~ ✅ **2026-08-03**; janela de invisibilidade SEGUE ABERTA | nenhum poller varre `*:pool:*:claim:*`; a lease expira passivamente. Defeito da família pull inteira (aprovação também), não do wrap-up. ✅ docstring do heartbeat inexistente corrigido (2026-07-30) — e **corrigido de novo em 2026-08-03**, porque o substituto afirmava outra rede que também não existe (ver § abaixo). Instrumento: estado `orphaned` do relatório de pendências, ✅ **CALIBRADO em 2026-07-31** (smoke 14/14). **O que fechou:** a VAGA, que nunca voltava — ver § "Lacuna 2 — o que fechou e o que não" |
| 3 | ~~**O TTL de fila existente nunca alcança fila pull**~~ | ✅ **NÃO É DEFEITO — item mal especificado, fechado 2026-08-05 sem código.** Ver § "Lacuna 3" abaixo |
| 4 | **Nenhuma ação de terceiro encerra item de tarefa** — ⚠️ **ÚNICA LACUNA COM DEFEITO VIVO** | ✅ fila pull (`/api/work_queue/expire/:sessionId`). ✅ **`force-complete` — 2026-08-05**, ramificado em 200/404/501, probe 9/9 (ver § "Lacuna 4" abaixo). Resta `/v1/workflow/instances/:id/cancel` = **410 hard** — e "inerte" era descrição errada: **4 telas o chamam** e a mensagem do 410 aponta um substituto **que não existe**. Ver § "Lacuna 4b" abaixo |
| 5 | ~~**A fila pull não é consultável pelo analytics**~~ | ✅ **resolvido para a pergunta operacional (2026-07-30)**: `GET /api/work_queue/pending` varre o ledger `{t}:work_task:*` e cobre as duas formas de pendência com uma linha só (o claim não apaga o ledger). Segue sem evento/tabela espelho — o histórico do **nunca-reivindicado** continua sem fonte (fatia 3, gated) |
| 6 | ~~**`close_reason` de segmento não tem enum**~~ | ✅ **FECHADA pela Fase E (2026-08-04).** Os mapas foram separados por domínio: `_TRANSPORT_TO_SEGMENT_CLOSE_REASON` (`orchestrator-bridge/main.py:3256`, vocabulário de SEGMENTO) × `_TRANSPORT_TO_CLOSE_REASON` (`:3194`, enum de CONTATO), com `test_segment_close_reason_domain.py` assertando que os conjuntos não se cruzam. O `contact-segment.ts:83` segue `z.string()` livre **de propósito** — o domínio de segmento é aberto (`task_submitted`, `acw_expired`, `agent_release_item`, …); o que estava errado não era a ausência de enum, era o mapa compartilhado escolhendo domínio em silêncio. Registro original abaixo. ⟨histórico⟩ `contact-segment.ts:83` é `z.string()` livre; `task_submitted`/`session_teardown`/`acw_expired`/`acw_supervisor_closed` são literais no publish do bridge. O enum fechado (`CloseReasonSchema`, `common.ts:44-56`) é o de SESSÃO — domínio diferente. **O `_TRANSPORT_TO_CLOSE_REASON` do bridge serve os DOIS** (`main.py:5755` = contato; `:6401` = segmento), e por isso todo `agent_disconnect` (um F5 no Console) gera segmento SEM `close_reason`, com aviso no log. Conserto = separar os mapas, não estender o compartilhado. Ver § "um F5 no Console devolve à fila um item em trabalho" |

### Lacuna 4 — `force-complete` ✅ *(2026-08-05; resta só o `cancel` 410)*

Detalhe em `CHANGELOG.md` § "`force-complete` deixou de mentir". Aqui fica só o que serve ao
próximo item, porque **os dois achados não estavam na descrição da lacuna** e valem como método:

- **A lacuna descrevia o handler; o defeito estava no caminho.** Ela dizia *"só reescreve uma chave
  Redis"* — verdade sobre o handler, e irrelevante na prática: as duas chamadas da UI iam **sem
  `Authorization`** num endpoint que exige `supervisor|admin`, logo tomavam **401 antes de chegar
  lá**. O comportamento descrito só era alcançável por curl. *Ao auditar um endpoint, ler também
  QUEM o chama e COM O QUÊ — senão descreve-se um trecho que ninguém executa.*
- **Chave com TTL não serve de condição de existência.** O `404 session_not_found` vinha de
  `session:{sid}:meta` ausente — e o caso que motiva o botão é justamente a sessão parada há muito
  tempo, cujo `meta` já expirou. O guarda escondia exatamente o alvo.
- **Um 501 que NOMEIA a ausência vale mais que um flag falso.** Abortar pipeline em execução não
  tem mecanismo (o engine não consulta cancelamento). Inventar um campo que ninguém lê seria repetir
  o defeito com outro nome — foi o que o endpoint fazia.

**Aberto na mesma linha:** `/v1/workflow/instances/:id/cancel` segue **410 hard**. ✅ **Conferido em
2026-08-07** pelo mesmo levantamento — e rendeu de novo dois achados fora do enunciado. Ver § abaixo.

**Não coberto:** a mudança de UI foi verificada por leitura, não executada. O probe exerce o
endpoint por curl.

### Lacuna 4b — o `/cancel` 410 *(levantado 2026-08-07; sonda escrita, medição pendente)*

O enunciado dizia *"segue **inerte**"*. Inerte sugere endpoint sem chamador. Medido por leitura:

- **410 hard sem handler** — `workflow-api/router.py:462`, só `raise HTTPException(410, …)`.
- **Quatro telas o chamam**, com o corpo idêntico: `ProcessosPage.tsx:414`, `WorkflowsPage.tsx:52`,
  `WorkflowMonitorPage.tsx:69`, `MonitorTab.tsx:642` — `confirm(…confirmCancel)` → `cancelWorkflow()`
  → `catch { alert(String(e)) }`. `cancelWorkflow` (`hooks.ts:221`) lança `HTTP ${status}` e
  **descarta o corpo**, então o operador confirma um cancelamento e recebe um `alert` dizendo
  **`Error: HTTP 410`**, sem nada mais.
- **A mensagem que ele não vê aponta um substituto inexistente.** O 410 instrui *"cancel webhook
  sessions via the channel-gateway (`DELETE /v1/channels/webhook/{session_id}`)"* — e o
  channel-gateway **não tem nenhuma rota `DELETE`**: a superfície webhook é POST
  trigger/resume/pool/collect/delegate + `GET …/status`.

**Modo de falha invertido em relação ao `force-complete`.** Lá a mentira era de SINAL (`200 ok:true`
sem fazer nada). Aqui o status é honesto — 410 é o certo para deprecado — e a mentira está no
**ponteiro**: manda usar um caminho que ninguém construiu. É a mesma forma do docstring do
`_claim_lease_key`, que citava uma segunda rede inexistente: *o 410 tem cara de decisão
arquitetural tomada (Arc 19 Fase D), e por isso ninguém foi conferir se o substituto nasceu.*

**Pré-requisito NÃO verificado do conserto óbvio.** Religar as 4 telas a
`POST /api/force-complete/:sessionId` (BFF, JWT `supervisor|admin`, já ramificado 200/404/501) é o
caminho natural — é literalmente o "encerramento por terceiro" que a D4 pedia. Mas ele é endereçado
por **`session_id`**, e a linha da lista de instâncias traz `session_id?` **opcional**
(`hooks.ts:24`). Se vier vazio, o botão troca `HTTP 410` por `HTTP 404` — defeito novo com data
recente, que é o pior tipo. **Medir antes:** `infra/test/probe_workflow_cancel_callers.sh` conta a
cobertura de `session_id` nas linhas reais e ramifica o veredicto em três estados.

**Três saídas, e a medição escolhe** (não decidir antes do número):

| Cobertura de `session_id` | Conserto |
|---|---|
| ~100% | religar as 4 telas ao `force-complete`; apagar o `cancel` e a rota deprecada |
| parcial | o botão precisa **declarar por que não pode agir** naquela linha (e as linhas sem `session_id` são o próprio diagnóstico: instâncias pré-Arc 19) |
| ~0% | o botão não tem alvo sob o modelo unificado — remover das 4 telas fecha a lacuna sem backend novo |

#### Medição de 2026-08-07 — respondeu, e por outra via

`probe_workflow_cancel_callers.sh`, 2ª execução (a 1ª caiu no preflight: pingava `/health`, e o
serviço expõe `/v1/health` — caminho copiado do comentário errado em `docker-compose.demo.yml:207`).

Resultado: **`workflow.instances` tem ZERO linhas em `tenant_demo`**, com o serviço de pé e o
endpoint respondendo. Isso reprovou a P1 e, pela regra da própria seção, seria *ausência de amostra*
— o probe declarou INCONCLUSIVO, corretamente. **Mas a pergunta não precisava da amostra**, e a
evidência que a fecha é estática:

1. **A tabela tem UM único escritor**: `db_create_instance` (`db.py:252`), chamado num só lugar —
   `POST /v1/workflow/webhook/{webhook_id}` (`router.py:794`), o gatilho legado por token.
2. **Esse escritor grava `"session_id": None` HARDCODED** (`router.py:799`). Logo a cobertura de
   `session_id` nesta tabela é **0% por construção**, não por amostragem: nenhuma linha que ela
   possa vir a ter jamais terá `session_id`.
3. **O caminho canônico não escreve nada aqui.** `POST /v1/workflow/trigger` (`router.py:158`) é,
   desde o Arc 19 Fase D, um *proxy* para o channel-gateway: cria sessão e devolve `session_id`,
   sem linha em `workflow.instances`.
4. **Uma linha criada nunca muda de estado.** `persist-suspend`, `complete`, `fail`, `cancel`,
   `collect/persist` e `collect/respond` são **todos 410**. O único mutador vivo — `/v1/workflow/resume`
   no ramo legado — exige `status == 'suspended'` (`router.py:299`), e nada pode pôr uma linha em
   `suspended`. Ela nasce `active` e **congela ali para sempre**.

**Conclusão: saída (c).** O `force-complete` está descartado como conserto — ele é endereçado por
`session_id`, que esta tabela nunca tem. Não há backend a construir: as 4 telas listam uma tabela
cujo único escritor é legado e cujas linhas são imutáveis por desenho.

> *Método, e é o mesmo escorregão registrado na § Lacuna 2 com outro nome:* a sonda foi desenhada
> para **esperar o fenômeno** (contar linhas, classificar ramos) quando a resposta estava legível
> no **produtor** desde o primeiro minuto — `session_id: None` é uma constante no código, não uma
> distribuição a medir. Antes de instrumentar uma leitura, perguntar se o ESCRITOR já responde.
> A sonda não foi perdida: é ela que prova o estado *vazio* da lista, que a leitura de código
> sozinha não provaria.

#### Executado em 2026-08-07 — saída (c) aplicada ✅

- **platform-ui**: `cancelWorkflow` removida (`hooks.ts`), com o motivo medido no lugar dela; botão
  + `handleCancel` fora das 4 telas; prop `onCancel`/`canCancel` dos dois `InstanceDetail`; 4 chaves
  i18n nos **dois** locales (`instance.confirmCancel`, `instance.cancelInstance`,
  `processes.instances.confirmCancel`, `…detail.cancelButton`). `refresh` saiu do `MonitorTab` (não
  tinha outro consumidor — o polling de 10 s já mantém a lista viva).
- **workflow-api**: rota apagada (era `status_code=410`); teste virou `test_cancel_route_is_gone`
  assertando **404**, não `!= 410` — `!= 410` passaria se alguém reintroduzisse a rota com outro
  código, que é a regressão que o teste existe para pegar.
- **e2e**: Parte F do cenário 13 e `WorkflowClient.cancel()` removidas.
- **Gate novo**: `infra/test/gate_orphan_ui_callers.sh` — falha se alguma tela chamar rota declarada
  com `status_code=410|501`. Estático (sem stack, sem efeito colateral), com **contador-testemunha**
  (zero rotas duras ⇒ INCONCLUSIVO, porque o zero mediria o detector). Distinção central: 410/501
  **condicional** não conta — o 501 do ramo 2 do `force-complete` é o padrão BOM que a I5
  estabeleceu, e marcá-lo ensinaria a esconder a ausência. Previsão contada: **5 rotas duras, 0
  órfãos**.

**Validação ✅ 2026-08-07** — `test_cancel_route_is_gone` PASSED **nomeado** (`-v --no-header`) e da
imagem RECONSTRUÍDA: `workflow-api` não tem volume mount, então a 1ª rodada (`48 passed`, sem nomes)
exercitou código velho + teste velho, coerentes entre si — passaria de qualquer jeito. Gate
`gate_orphan_ui_callers.sh` verde (5 rotas duras, 0 órfãos). UI conferida **nas duas telas
alcançáveis**, com fixture `skill_probe_ui_v1` (`status=suspended`, que era exatamente a condição de
render do botão): `/flow/monitor` › Processes e `/flow/processos` › Instances abrem o painel de
detalhe **sem** o rodapé vermelho.

**Correção do próprio levantamento:** eram *quatro chamadores em código*, mas só **duas telas
alcançáveis** — `WorkflowsPage.tsx` e `WorkflowMonitorPage.tsx` não são importadas por ninguém
(`routes.tsx:106,108` mandam `/workflows` e `/workflow/monitor` para `/flow/monitor`). Arquivos
mortos, e a frase "quatro telas vivas" superestimava a gravidade. *Contar call sites não é contar
superfície: o roteador é que decide o que existe.*

**O que a validação mostrou de graça (argumento da saída removida do escopo):** o painel exibe um
processo `Suspended` que não oferece **ação nenhuma** — nem cancelar, nem retomar. Coerente com o
achado: as linhas nascem `active`/`suspended` e congelam. A listagem inteira é candidata a remoção
sob o Arc 19, mas isso é decisão de produto, não conserto de defeito. *(O chip de filtro
"Cancelled" segue na tela e sempre volta vazio — defensável, porque linhas legadas de tenants reais
podem ter esse status; anotado para não ser "descoberto" de novo.)*

#### Quatro sintomas independentes, uma decisão de produto *(2026-08-07)*

Não construir nada com isto — é o dossiê para quando a remoção da listagem for decidida. Cada linha
foi observada por uma via diferente, o que é o que dá peso ao conjunto:

1. **Nada a fazer** — o painel de detalhe não oferece ação alguma (visto na tela, após a remoção).
2. **Nada muda** — todo mutador é 410; o único vivo exige `suspended`, estado inalcançável.
3. **O campo canônico está vazio** — a tabela TEM `pool_id`, e o único escritor grava `None`
   (`router.py:801`). Por isso o filtro da tela é por **`flow_id`** (= `skill_id`, o endereço
   legado), e **está certo assim**: "corrigi-lo" para pool, obedecendo o invariante ao pé da letra,
   criaria um filtro sobre coluna sempre nula — digitar qualquer pool devolveria vazio sem erro.
   *Trocar um rótulo legado honesto por um filtro que nunca casa é a armadilha do valor plausível.*
4. **O produtor é inalcançável pelo menu** — as linhas só nascem do registro legado
   `workflow.webhooks` (`flow_id` + token `plughub_wh_…`), cujo editor é o `WebhooksTab` em
   **`/workflow/calendar`** (`routes.tsx:104`): rota existente, **sem entrada em nav nenhuma**. Só
   se chega digitando a URL.

**Duas gerações de "webhook", e é isso que confunde ao ler a tela:** o canônico é o `ChannelEndpoint`
de `/config/channels` › Webhook (**slug → pool**, ex. `crm-callback` → `retencao_humano`), que cria
sessão normal; o legado é o `workflow.webhooks` (**`flow_id`** = skill), que cria linha em
`workflow.instances`. Não são o mesmo campo em telas diferentes — são a mesma pergunta respondida
antes e depois do invariante "o pool é a unidade endereçável".

➡️ **Isto virou arco próprio:** [`docs/adr/adr-webhook-endpoint-single-registry.md`](docs/adr/adr-webhook-endpoint-single-registry.md)
(proposto, 2026-08-07) — registro único + `identifier` opaco. Ver § abaixo.

---

## Webhook — registro único e identificador opaco *(ADR aceito; **arco A–F ✅ 2026-08-07**)*

**A regra:** um webhook é UMA coisa, logo tem UM registro. `ChannelEndpoint` é esse registro
(já é o de webchat/WhatsApp/voz — **webhook é o desviante, não o caso especial**), e o `identifier`
é **opaco**: não codifica qual skill roda, e nada o interpreta.

**Não é regra nova** — é a aplicação de duas invariantes que o `CLAUDE.md` já declara ("One source
per domain", "Every config field is UI-editable") ao domínio que escapou delas.

**O que a medição mostrou** (detalhe no ADR §1-2): três superfícies acionáveis, uma visível; o
defeito **não é colisão** (`@@unique` bloqueia, e os prefixos de URL são disjuntos) e sim **ausência
de inventário** — todo pool com `webhook_skill_id` está acionável agora, sem linha em registro
nenhum. E o endereçamento por skill **não é inevitável**: é convenção do Arc 19, já rebaixada pelo
invariante do pool, com **modo de falha conhecido** (skill em N pools ⇒ `Webhook endpoint AMBÍGUO`,
regime legítimo previsto no próprio `CLAUDE.md`).

**Duas propriedades que barateiam a mudança:**

- **É backfill, não reescrita.** Identificador opaco ⇒ os chamadores internos seguem mandando a
  MESMA string; muda só quem a resolve (registro, não fallback).
- **Nada se perde.** "Qual skill roda" já tem autoridade única — o slot `current` do pool. Derivar
  skill do path sempre foi a fonte ERRADA, não só redundante: após um `promote` o path continua
  dizendo o nome antigo.

**Emenda ao enunciado original:** "sem regra de formação" = **sem semântica**, com validação
**sintática** (URL-safe). Opaco quanto ao significado, validado quanto à forma — senão trocamos
acoplamento semântico por defeito de transporte.

**Ordem é parte da decisão (D7):** semear ANTES de trocar a resolução, remover o fallback POR
ÚLTIMO. Invertido, todo disparo interno vira 404 — falha muda do lado de quem chama.

**Fases A–F no ADR.** Em aberto: se `/v1/channels/webhook/pool/{id}` também entra no registro
(inclinação: sim, inventário só vale se exaustivo) e o destino do legado por token.

**Fase A ✅ MEDIDA 2026-08-07** (`infra/test/probe_webhook_endpoint_inventory.sh`; detalhe no ADR §6).
**11 superfícies acionáveis, 1 na tela, e é a suspeita.** As 4 previsões bateram exatas — o que
também prova ausência de drift YAML↔store para estas entidades.

| | Medido |
|---|---|
| pools com canal webhook (acionáveis, sem registro) | **10 de 10** |
| `ChannelEndpoint(webhook)` | **1** (`crm-callback`) |
| endpoint registrado com pool que **não declara o canal** | **1 de 1** |
| `workflow.webhooks` (token) | **0** |

**Dois achados fora do enunciado:**

1. **O registro visível não é validado.** `POST /v1/channel-endpoints` confere só PRESENÇA de campo
   (`channel-endpoints.ts:93-95`) — não valida existência do pool nem o canal declarado. Não é erro
   de seed, é buraco sistemático, e é o **espelho** do problema de invisibilidade: *endpoint que
   aparece na tela e não serve é pior que endpoint ausente — tem aparência de conferido.* Vira
   requisito da D8.
2. **`workflow.instances` está vazia PORQUE `workflow.webhooks` está vazia** — o único escritor
   daquela exige linha desta (`router.py:794`). Os dois achados da manhã eram um só. Não decide a
   fase F (demo vazio ≠ nenhum tenant usa), mas nada no demo depende do caminho por token.

✅ **`crm-callback` MEDIDO (2026-08-07) — e o resultado REFUTOU a hipótese: o endpoint FUNCIONA.**
Disparo → 201 → fila (nenhum humano logado ainda) → recursos conectados → **contato entregue a um
agente humano, atendido e encerrado**.

**Mecanismo** (`router.py:86-92`): com `pool_id` explícito, `pools = [pool]` — **sem filtro de
canal**. O filtro por `channel_types` só existe no ramo legado de DESCOBERTA (`:94`). Logo *canal é
hard filter sobre a descoberta de pool, não sobre pool endereçado* — e `ChannelEndpoint` é
justamente um entry point que declara o pool. A configuração é **válida**.

⚠️ **Erro de método meu, registrado porque é novo:** a versão anterior desta nota dizia "hard filter
provado", a partir de um snapshot com `available: 2` lido **depois** de os recursos serem
conectados — **leitura pós-intervenção usada como controle do estado anterior**, com
contemporaneidade *inferida* de um `updated_at` isolado em vez de *provada* contra o instante do
disparo. *Um controle só vale se for CONTEMPORÂNEO do fenômeno, e contemporaneidade se prova
comparando instantes, nunca se deduz de um timestamp lido sozinho.* Versão temporal do erro que a
§ Postura já cataloga: o valor era plausível e por isso passou.

✅ **Fase B MEDIDA 2026-08-07** (detalhe no `CHANGELOG.md` e no ADR §7). `F1=10 · F2=11 · F3=0` → **21**
superfícies · `sem registro: 0` · `procedência: 0 errada(s) de 10 checada(s)` · 1 aviso · **exit 0**.
As quatro previsões bateram exatas. **Não muda comportamento (D7):** a resolução segue pelo fallback.

**A decisão da fase — declarado, não derivado.** As 10 linhas internas são **entradas escritas no
`infra/registry/tenant_demo.yaml`**, não derivadas de `pool.webhook_skill_id` pelo `RegistrySyncer`.
Derivar (a) **inverteria a D5** (a autoridade do endereço continuaria no campo que ela retira, com o
registro como projeção) e (b) tornaria o gate da D8 **um teste que não pode reprovar** — a linha
concordaria com o pool por ter sido copiada dele. A derivação sobrevive como **checador**
(`_validate_webhook_endpoints`, ERROR fail-open), nunca como escritor; ele lê o YAML, o probe lê o store.
`origin` virou **coluna** porque a D2 obriga: sem ela, "linha interna" só seria decidível interpretando o
texto do `identifier`, que é a semântica que a D2 retira.

⚠️ **Segundo erro de método meu, mesma família do anterior:** a 1ª execução do probe deu `F2=1` e parecia
"o seed não aplicou" — o log mostrava os 10 `created` segundos depois. **`up -d` retornar não é "o syncer
rodou"**: medi **antes de a intervenção terminar**. Espelho do erro acima (lá, controle *pós*-intervenção;
aqui, medição *pré*). *Entre agir e medir, prove que a ação terminou — um comando que retorna não é uma
ação concluída.* E o bloco novo `F4c` aprovou sobre **zero** amostras ("todas carimbadas internal" sem
nenhuma linha interna) até ganhar contador-testemunha.

**Pendente até a Fase D:** as 10 linhas aparecem em `/config/channels` › Webhook **editáveis**; apagar uma
lá é ressurreição silenciosa no próximo boot (seed-if-absent). Consequência da ordem da D7, não defeito.

✅ **Fase C IMPLEMENTADA 2026-08-07** (detalhe no `CHANGELOG.md` e no ADR §7.5). O gateway resolve pelo
registro e publica `pool_id`; o fallback segue **ativo**. Gate novo:
`infra/test/gate_webhook_registry_resolution.sh` — **positivo** (tem linha → registro) + **controle
NEGATIVO** (sem linha → fallback). Sem o negativo o gate não poderia reprovar, e ele ainda prova que o
fallback está vivo (exigência da D7). O gate **não** dispara os dez endereços de propósito: entre eles há
`skill_deploy_promote_v1` (promove pool de verdade) e os dispatchers de outbound (contatam gente).

⚠️ **Emenda de FATO à D5 — o carimbo de DNIS que ela manda preservar não existe.** `models.py:272` é
COMENTÁRIO; quem popula `sessions.dnis` é `analytics-api/models.py:89`
(`payload.dnis|dialed_number|to`), campos que o adapter de webhook nunca escreve. **`dnis` é NULL para
toda sessão webhook**, nas duas portas. Carimbá-lo é trabalho A FAZER (candidato à Fase D), não efeito a
preservar — e é por isso que `skill_id` continua no evento mesmo quando o registro resolve.

**Bloqueadores da Fase E (registrados agora, enquanto o contexto é fresco):**

1. **`FASE C · FALLBACK webhook` zerado** por janela representativa em produção — descontado o 1× que o
   controle negativo do gate produz de propósito a cada execução.
2. **`unavailable` precisa de tratamento próprio antes de o fallback sair.** `resolve_pool_ex` já separa
   "não existe" de "não consegui perguntar" (e parou de cachear falha), mas na Fase E `not_found` vira
   404 e `unavailable` **não pode** — seria afirmar que o endereço não existe por causa de um soluço de
   rede do agent-registry, num caminho sem rede de segurança. Sem isso, uma indisponibilidade de 30 s do
   registry vira 404 em todo disparo interno.

✅ **Fase D IMPLEMENTADA 2026-08-07** (ADR §7.6). Coluna **Origem** (`cadastrado`/`declarado`/`legado
(token)`) e `internal` **read-only** em `/config/channels` › Webhook. Read-only aqui não é permissão, é
honestidade sobre quem manda: a linha nasce do YAML e o provisionamento é seed-if-absent, então edição
feita na tela morreria no próximo boot — aceitar e perder em silêncio é pior que recusar.

**O critério "endpoint acionável ausente da tela" fechou o §7 em aberto — e contra a inclinação escrita
lá.** `/v1/channels/webhook/pool/{id}` **não** vira registro: a linha `identifier = pool_id → pool_id`
seria a função identidade do pool, incapaz de discordar da fonte e portanto de denunciar qualquer coisa.
A tela ganhou a seção "endereço implícito de cada pool webhook", derivada. Inventário exaustivo **onde a
pergunta é feita**, sem inflar a tabela.

**Reprovação nova e honesta:** o probe ganhou **F5 (cobertura da tela)** e agora falha quando `F3 > 0` —
`workflow.webhooks` é acionável, vive noutra tabela e não entra nesta tela. No demo é inócuo (F3=0); num
tenant com linhas, a Fase D está incompleta por construção e o conserto é a Fase F.

⚠️ **EMENDA À FASE B — ela mudou comportamento, e num lugar que ninguém olhou** (ADR §7.6.3, achado ao
conferir a tela). "Não muda comportamento" valia para a porta INTERNA. A porta externa
`POST /channel/webhook/{slug}` sempre resolveu pelo registro e **404 quando não achava** — ao semear os
dez identificadores, `/channel/webhook/skill_formfill_demo_v1` deixou de dar 404. **Dez endereços
internos ganharam uma segunda porta.** Não é falha de autenticação (mesmo gateway, nenhuma das rotas
exige credencial) e é coerente com a D1, mas vira relevante se o ambiente publica `/channel/webhook/*` na
borda e mantém `/v1/*` interno. **Medido: 201** (confirmado, não inferido). ✅ **RESOLVIDO — decidido (b),
filtrar:** a porta externa serve só `origin='external'` (`allowed_origins` no `resolve_pool_ex`, novo
desfecho `origin_refused`; resposta 404 e o LOG nomeia — 403 confirmaria a existência do endereço a quem
chama de fora). O argumento não foi "é mais seguro", foi a **assimetria dos erros**: aceitar e a
topologia divergir depois expõe endereço interno em silêncio; filtrar e a exposição ser uniforme custa um
alias que ninguém usa. *Quando falta a informação que decidiria, decide-se pelo custo de errar.*
Cuidado que ficou no código: **o filtro é aplicado na SAÍDA do cache** — as duas portas compartilham a
chave `(tenant, canal, identificador)`, e cachear o veredicto filtrado faria a primeira a consultar
decidir pela outra.
*Lição: "não muda comportamento" foi conferido no caminho que a fase MEXIA, não no que ela ALIMENTAVA —
semear um registro muda todo mundo que o lê, e essa lista é maior que a de arquivos tocados.*

**Dívidas abertas por esta fase:**

1. **Read-only é da TELA, não da API.** `PUT`/`DELETE` seguem aceitando linha `internal` — deliberado:
   bloquear tiraria a única forma de remover linha interna obsoleta depois que ela sai do YAML (o
   provisionamento **nunca poda**, então ela ficaria imortal). Enforcement server-side só junto de um
   caminho de poda explícito.
2. **Carimbo de DNIS não entrou.** Era candidato natural aqui (a tela passou a exibir o endereço), mas é
   mudança de backend + dado analítico, com risco próprio — ver a emenda de fato à D5 acima. Fatia
   separada.

✅ **Fase E IMPLEMENTADA 2026-08-07** (ADR §7.7). Registro é o **único** resolvedor: `not_found` → 404
nomeado, `unavailable` → **503 + Retry-After**; ramo `webhook_skill_id` do router **apagado**.

**O critério da fase foi TROCADO, e é a decisão da fase.** "Log zerado em janela representativa" não era
satisfazível (demo reiniciado; log com minutos de vida) — esperar seria nunca remover, ou aceitar "não vi
nada em três minutos" como prova, a mesma ausência-de-amostra que este arco já pegou duas vezes.
Substituído por **inventário estático de discadores** (método da Fase A): dois discadores reais
(`agente_portabilidade_intake_v1`, `smoke_journey_root.sh`), ambos em `skill_portabilidade_demo_v1`, que
tem linha. *Log mede amostra; inventário mede o espaço* — fechado porque todo produtor passa pela porta
HTTP do gateway.

**`Pool.webhook_skill_id` sobrevive como CAMPO, não como endereço** (D5): é a fonte declarativa do que
semear, cruzada pelo guard `_validate_webhook_endpoints`. Não remover sem antes reescrever o guard e o
probe (F1/F4a dependem dele).

**Dívida criada por esta fase — cenários e2e discam `flow_id` sem linha e agora tomam 404.**
Afetados: `03_resume_after_failure`, `13_workflow_automation`, `14_collect_step`, `18_workflow_worker_chain`,
`28_evaluation_workflow_cycle`, todos via o proxy `/v1/workflow/trigger` da workflow-api. **Eles já não
funcionavam**: nenhum pool declara aqueles skills, o router os rejeitava e a sessão morria enfileirada —
a mudança troca "201 + sessão que não vai a lugar nenhum" por "404". Conserto: migrar para endereço por
pool (preferível) ou semear linhas de teste. **Cuidado ao consertar:** se a suíte passou a sessão inteira
com 201 sobre sessão morta, o verde dela já não significava o que parecia; conferir o que cada cenário
realmente assere antes de só trocar a URL.

✅ **Fase F DECIDIDA 2026-08-07 (ADR §7.8) — aposentar o legado.** A pergunta da fase ("migrar ou
aposentar") estava **mal posta**, e reformulá-la foi o resultado: o legado acopla **autenticação**
(valiosa) a um **ciclo de vida de instância morto** (mutadores 410; a linha nasce `active` e nunca muda;
flow que suspenda trava). Como pacote, forçava escolher entre preservar o morto para manter a auth ou
perder a auth ao matar o morto. *Quando as duas saídas oferecidas são ruins, desconfie de que a pergunta
amarrou coisas separáveis.* **Arco A–F completo.**

⚠️ **ACHADO DE SEGURANÇA — todo endpoint webhook é ANÔNIMO.** O caminho por token era o **único
autenticado** (`X-Webhook-Token`, SHA-256 + tempo constante + `active` + log de entrega). O
`ChannelEndpoint`, **nas duas portas**, não tem autenticação alguma: todos os disparos desta sessão foram
`curl` sem credencial, contra pools que promovem deploy e contatam clientes. **É anterior a este ADR** —
não foi criado por ele —, mas estava mascarado por um caminho que ninguém usa. `F3 = 0` ⇒ aposentar não
retira proteção de ninguém hoje.

---

## Autenticação de endpoint webhook ✅ ARCO FECHADO *(1 ✅ · 2 ✅ 2026-08-07 · 3 ❌ cancelada · 4 ✅ · borda ✅ 2026-08-10 — ADR §7.9 e §7.10; aberto só o 2b, que espera número de volume)*

✅ **Fatia 1 — mecanismo + medida** (detalhe no `CHANGELOG.md`). `auth_required` opcional por endpoint,
default **false**; token portado do `workflow-api/webhooks.py`; hash só para chamador de serviço;
verificação nas **duas** portas a partir de UMA função; **fail-closed** quando não dá para verificar;
revogar desliga a exigência junto (não deixa estado impossível de satisfazer). Coluna `Auth` na tela e
seção **F6** no probe contam os anônimos — o antídoto do opt-in é a ausência medida, não o default
agressivo. Gate `infra/test/gate_webhook_endpoint_auth.sh` com **controle de não-regressão** (endpoint
anônimo tem de seguir aceitando sem header — é o que prova que o default OFF não virou ON).

**Pendências, em ordem de valor:**

1. ✅ **Invalidação de cache por `registry.changed` — FEITA** (`registry_invalidation_consumer.py`).
   Rotação/revogação passa a valer em segundos, não em 30 s. **`group_id` único por processo**: o cache é
   in-process, logo invalidação é *broadcast*, não fila — com group compartilhado só uma réplica receberia
   o evento e as outras seguiriam com o hash revogado. (O routing-engine usa group compartilhado no MESMO
   tópico e está certo, porque o cache dele vive no Redis.) Gate cobre por **rotação**, não revogação —
   revogar desliga `auth_required` e o endereço fica anônimo, o que não distingue nada.
2. ✅ **UI de token — FEITA** (fatia 2, detalhe no `CHANGELOG.md`). Gerar/rotacionar/revogar em
   `/config/channels` › Webhook; banner que **não some sozinho** (o segredo aparece uma vez e não é
   recuperável); confirmação **assimétrica** (gerar não pergunta, rotacionar sim — confirmar sempre
   treina a clicar sem ler); a confirmação de revogar diz que a autenticação é **desligada junto**.
   Botões só em linhas `external`, porque ligar auth em `internal` silencia o disparo interno até a
   fatia 3.
2b. **Janela de aceitação de credencial revogada — medida, não zero.** Com o consumidor de invalidação
   já no grupo: **0 s**. Rodando durante o join (~3 s após o boot do gateway): **3 s** — o
   `invalidate_all()` pós-join fecha aí. Se o evento se perder por qualquer motivo, volta ao TTL (**30 s**).
   Para material de credencial, staleness tem custo de segurança que `pool_id` não tem. Opções, com o
   trade-off explícito: (a) TTL curto só quando `auth_required` (ex. 5 s) — mais consultas só nos
   protegidos; (b) não cachear o `token_hash`, resolvendo o resto do cache — uma ida ao registry por
   disparo autenticado, aceitável em webhook de baixo volume, cara em alto; (c) manter como está e confiar
   na invalidação, que é o desenho atual. **Decidir com número de volume na mão**, não por preferência.
3. ❌ **CANCELADA — fatia 3 (plumbing de credencial nos chamadores internos)** *(2026-08-10, ADR §7.9)*.
   Não foi adiada: o **inventário estático dos discadores** (método das Fases A/E) mostrou que a tarefa
   **não deve ser feita**. Dos dez `origin=internal`, **nove não têm chamador algum** na porta por
   identificador — os pools deles são disparados por `/v1/channels/webhook/pool/{id}`, que não passa pelo
   registro e **não tem onde pendurar token** (§7.6.1). Só `skill_portabilidade_demo_v1` é discado por
   identificador (intake + `smoke_journey_root`); o `_fire_detached_hook`, citado no enunciado da fatia,
   é porta por pool e **está fora do escopo**. Daí o argumento, estrutural e não de risco: `/v1/*` exposto
   na borda ⇒ a porta por pool está junto ⇒ auth por identificador é **teatro**; `/v1/*` não exposto ⇒ os
   internos são inalcançáveis ⇒ auth é **redundante**. Nos dois ramos compra zero, e no primeiro ainda
   custa (silencia disparo interno). *"Dez anônimos" era dez ENDEREÇOS e um DISCADOR.* Entregue junto:
   **F6 reclassificado** (internal = "por DECISÃO", com o probe declarando que **não verificou** a borda —
   ele lê o store, exposição é infra) e **guard `INTERNAL_AUTH_REFUSAL`** (422 nomeado no create *e* no
   `POST /{id}/token`, que era o furo real: read-only da tela ≠ read-only da API, §7.6.4). ⚠️ **A decisão
   depende da porta por pool seguir anônima por construção** — registrá-la ou fechá-la reabre a pergunta.
4. ✅ **Fatia 4 — FEITA, mas NÃO como "default ON"** *(2026-08-10, ADR §7.10)*. O inventário dos
   **criadores** refutou a premissa de que existe um só: o operador pela UI **recebe** o token (o 201 é a
   única janela em que ele existe em claro), e o `RegistrySyncer` faz o mesmo POST a partir do YAML e
   **descarta o corpo**. Default ON faria instalação limpa nascer com `crm-callback` exigindo um token que
   ninguém viu — 401 permanente, dormindo até o `--wipe` porque seed-if-absent dá 409 nas linhas que já
   existem. **Decisão: sem default.** `auth_required` ausente em `channel=webhook` + `origin=external` ⇒
   **422 nomeado** — "este chamador consegue guardar um segredo?" só é sabido NO chamador, então o route
   para de adivinhar. A UI declara `true` (caixa **marcada por padrão**, que é onde a intenção do "default
   ON" legitimamente vive) e mostra o banner do token no create; o YAML declara `false`, e **só `false` é
   válido ali** (o syncer rebaixa `true` com log ERROR em vez de criar endpoint inalcançável). Junto:
   **`auth_required` recusado em canal não-webhook** (a flag só é lida nas rotas de webhook; nos demais a
   linha afirmaria proteção que ninguém aplica). Cobertura: **P11/P12** no gate, com os ids dos creates
   que devem falhar no `trap`.
5. ✅ **Requisito de borda ESCRITO** *(2026-08-10)* — era o último item do arco. Vive em dois lugares, de
   propósito: **invariante** em `CLAUDE.md` § What Never To Do (é o arquivo lido toda sessão; prosa em
   guia não é requisito) e **detalhe** em `docs/guias/webhook-patterns.md` § Exposição na borda (tabela
   dos dois prefixos, por que a porta por pool é inprotegível, o que conferir num ambiente). O argumento
   registrado é o da porta (2): `/v1/channels/webhook/pool/{id}` é anônima por construção, logo publicar
   o prefixo torna disparável todo pool webhook do tenant e **nenhum `auth_required` muda isso**.
   ⚠️ Continua **sem cobertura de teste** — ver item 6.
6. **Probe EXTERNO de borda** *(aberto 2026-08-10)* — o requisito do item 5 está escrito e não é
   verificável de dentro: todo instrumento que temos roda na rede interna, onde `/v1/*` **deve** mesmo
   responder. Um probe que rodasse ali confirmaria o oposto do que se quer provar. O teste válido é um
   `curl` **de fora** contra o host publicado, esperando: `/channel/webhook/{identifier}` responde e
   `/v1/channels/webhook/pool/{qualquer}` **não**. É trabalho de infra (precisa de um ponto de origem
   externo), não de código — e enquanto não existir, o F6 do probe de inventário declara que não mediu,
   que é o comportamento correto. **Não fabricar um substituto interno:** um teste que só pode passar
   não distingue nada, e aqui ele ainda daria a impressão de que a borda foi conferida.

**Arco de autenticação de endpoint webhook: FECHADO** (fatias 1 ✅, 2 ✅, 3 ❌ cancelada com motivo, 4 ✅,
requisito de borda ✅). Aberto só o item **2b** (acima), que espera número de volume para decidir o regime
de cache do material de credencial.

⚠️ **Os endereços por pool (`/v1/channels/webhook/pool/{id}`) seguem anônimos por construção** — não passam
pelo registro (ADR §7.6.1), logo não têm onde pendurar credencial. Se isso for inaceitável num ambiente, a
saída não é registrá-los (ver o argumento da função identidade), é restringir o prefixo `/v1/*` na borda.

---

## Prontidão de provisionamento — não há sinal de "o syncer terminou" *(aberto 2026-08-10)*

`rebuild-all.sh` termina em `up -d` e imprime *"Acompanhe a convergência"*, delegando ao olho humano; e o
`orchestrator-bridge` **não tem healthcheck**, então `docker compose ps` não consegue dizer se o
provisionamento acabou. O bridge é o ÚLTIMO a convergir (espera agent-registry + skill-flow-service
healthy, depois sincroniza skills → pools → channel_endpoints), e nada anuncia esse fim.

**Consequência medida, três vezes:** ADR §7.4 (`F2=1` lido antes do seed, que parecia "o seed não
aplicou"), `up -d <serviço>` subindo só o subgrafo, e 2026-08-10 (bateria inteira em INCONCLUSIVO logo
após um `--wipe`, com o bridge em *"Up Less than a second"*). Nos três, o número era **plausível** — e
por isso pareceu resultado, não ausência de medição.

**Conserto:** `infra/test/wait_registry_converged.sh` ✅ *(feito 2026-08-10)* — bloqueia até o registro
ficar **quiescente** (duas amostras iguais e > 0), com timeout e veredicto de 3 estados. Critério é
estabilidade, não contagem fixa: contagem fixa faria do helper um teste do tenant demo, que envelhece a
cada pool novo, e prontidão não é a mesma pergunta que inventário (essa é do probe). `EXPECT_*` permite
exigir números exatos quando o chamador os conhece.

**Falta:** chamá-lo no fim do `rebuild-all.sh`. Não foi feito junto de propósito — o script é o caminho
de instalação de todo mundo, e mudá-lo no mesmo movimento em que se cria a ferramenta mistura duas
coisas que devem falhar em separado.

---

## `sequence_index` apagado pelo `participant_left` — atribuição de agente em qualidade está em risco *(achado 2026-08-10)*

**Causa raiz, localizada e única.** O `sequence_index` é atribuído no `participant_joined` (contador Redis
`INCR session:{sid}:segment_seq`) mas **não é persistido junto com o `segment_id`**
(`orchestrator-bridge/main.py:918-922` grava só o segment_id). No `participant_left` ele é reconstruído
como `0` (`main.py:6759`) ou omitido (default `0`, `main.py:3030`) — e como `analytics.segments` é
`ReplacingMergeTree`, **a linha do left substitui a do join e apaga o índice**. Atinge **todo segmento
humano e todo especialista**; os nativos escapam por acidente (join e left no mesmo escopo léxico).

Medido: sessão `5553c72a` saiu `0, 0, 2` — o `1` do segmento humano existiu no join e foi sobrescrito.
Verificável com `SELECT sequence_index, ingested_at FROM segments WHERE session_id=… ` **sem `FINAL`**,
que mostra as duas versões.

⚠️ **A consequência mais séria não é a ordenação — é ATRIBUIÇÃO.** `reports_query.py:2183-2209`
(`_session_agent_attribution_sql`) usa **cinco** `argMax(…, sequence_index)` para decidir *qual agente é
atribuído à sessão* nas lentes **quality**, **quality_criteria**, **deploy** e **session_nps**. Com
empates em `0` o `argMax` é **não-determinístico** — e as cinco colunas podem vir de **linhas
diferentes** (agent_key de um segmento, pool_id de outro). Na sessão medida, atribuiria ao agente nativo
(seq 2) e não ao humano que efetivamente atendeu. **Nota de honestidade:** o impacto real em números de
qualidade **não foi medido**, só derivado do código.

⚠️ **Inventário de consumidores REVISADO 2026-08-10 (a v1 desta nota errava dois dos quatro).** Cada um
tem de ser julgado por *como lê*, não por *se cita o campo*:

| Consumidor | Como lê | Hoje | Depois do conserto |
|---|---|---|---|
| `_session_agent_attribution_sql` (5 `argMax`) | `segments FINAL` | empate **só quando o máximo empata** | determinístico |
| `quality-export/exporter.py:181` | `segments FINAL`, `ORDER BY seq, started_at` | correto **por acidente** | **REGRIDE** — ver abaixo |
| `mv_segment_summary` → `handoff_count` | **MV sobre INSERTs**, `maxState` | **correto** (vê a linha do join) | inalterado |
| `SegmentList.tsx:146` (badge) | exibição | badge some | badge correto |

- **`handoff_count` NÃO é afetado.** A MV (`clickhouse.py:922-940`) é gatilho de INSERT sobre `segments`,
  **sem `FINAL`**: ela agrega as DUAS versões da linha e `maxState` preserva o índice do join. O defeito
  vive só onde se lê o estado mesclado. *(A v1 listava a MV como afetada — leitura por citação do campo,
  não pelo mecanismo.)*
- **O empate do `argMax` é mais estreito do que a v1 dizia, e por isso mais fácil de reproduzir errado.**
  `argMax` só é não-determinístico quando o **máximo** empata. Na sessão medida (`0, 0, 2`) o máximo é
  único → a atribuição já sai determinística, no agente nativo. O empate real é a sessão com **dois ou
  mais primários humanos** (transferência): ambos gravam 0, o máximo empata, e as 5 colunas podem vir de
  linhas diferentes. Um teste que use uma sessão com resume nativo no fim **não pega** o empate.
- ⚠️ **O exporter REGRIDE com o conserto, e por isso saiu na mesma fatia.** `ORDER BY sequence_index ASC,
  started_at ASC`: enquanto tudo empata em 0, a cláusula degenera em cronológica e o export sai certo.
  Com o índice correto, um especialista de conferência (fora do contador ⇒ 0) entrando tarde passa a
  ordenar **antes** de um primário de handoff (1+). Consertar o produtor sem rechavear o consumidor
  **quebraria** o export — caso literal de "o conserto move o número". Rechaveado para `started_at ASC,
  segment_id ASC`.

**A cadeia a jusante já está correta — não precisa de conserto** *(verificado 2026-08-10)*. O registro
`human_seg:{pool}` é gravado **no `participant_left`** (`main.py:1613`); `_seed_segment_signal:3321` lê
`record["sequence_index"]` e `_republish_segment_from_signal:3362` lê do acumulador. Os três propagam
fielmente — **herdam o `0` do left**. Logo o conserto é num ponto só de origem, e a cadeia se corrige
sozinha. *(Isto corrige a v1 desta nota, que listava acumulador e `segment.ts` como sites a alterar.)*

**Conserto, simétrico ao que já se faz com `segment_id`:**

1. **No join** (`main.py:900-930`): `_seq_idx` é calculado em `:915` e **nunca persistido**, enquanto
   `_seg_id` é gravado em `:918-922`. Persistir o índice ao lado — chave paralela
   `session:{sid}:segment_seq_idx:{instance_id}` (mesmo TTL 14400) **ou** um campo a mais no
   `participant_meta:{instance_id}` (`:934-944`), que já se declara *"fonte por-participante para o path
   de close"*. Preferir o `participant_meta`: reusa chave existente e é `get`, não `getdel`, então
   sobrevive a um left republicado.
2. **Nos left sites** que hoje mandam `0`: recuperar junto com o `segment_id`, no mesmo bloco `getdel`
   que já existe (`main.py:2830`, `4531`, `5641`, `6165`, e os demais em `6854`/`8182`).

⚠️ **Um segundo left do MESMO segmento tem de continuar funcionando.** O `segment_id` sobrevive porque os
sites guardam fallback em escopo (`_left_seg_id = _part_seg_id`); o índice precisa do mesmo cuidado, ou o
republish volta a escrever 0 e o `ReplacingMergeTree` desfaz o conserto — exatamente o bug de novo, por
outro caminho.

**Como validar (e o e2e 23 NÃO serve — ver acima):** rodar `smoke_formfill_renderer.sh` + claim/submit no
Console e conferir que o segmento humano sai com `sequence_index = 1`, não `0`. A prova de hoje foi
aritmética (`0, ?, 2` com o meio em zero ⇒ o `1` foi consumido e perdido); depois do conserto a sequência
tem de ler `0, 1, 2`.

⚠️ **Não conserta a ordenação, e não deve prometer isso.** Mesmo corrigido, `queue`, sintéticos e
especialistas ficam fora do contador **por decisão** — o campo é *"ordem entre primários não-sintéticos"*,
nunca ordenação total. Ordenar por `started_at`.

### Decisão de fatia — tomada 2026-08-10, com o critério explícito

A pergunta era *"os consumidores que usam o índice como chave de ordem entram na mesma fatia ou viram
fatia própria?"*. O critério que decide **não é acoplamento, é neutralidade**: entra na fatia do conserto
o consumidor que o conserto **move**; vira fatia própria o que ele deixa parado.

- **Verificação pedida, feita:** `_session_agent_attribution_sql` **filtra sim** —
  `WHERE role = 'primary' AND agent_type != 'system'` (`reports_query.py:2204-2205`). E o escopo do filtro
  **coincide exatamente** com o escopo do contador: os três únicos sites de `INCR segment_seq`
  (`main.py:914` humano, `:4148` nativo não-conferência, `:8045` resume) publicam todos
  `role='primary'` com `agent_type` ∈ {`human`,`native`}; `queue` (`:5418`) e especialista (`:4136`)
  nunca incrementam e são excluídos pelo `role`. **Logo o conserto TORNA o índice único dentro do
  conjunto filtrado, e o empate desaparece** — a hipótese *"empata mesmo depois do conserto"* está
  **refutada**, e o rechaveamento da atribuição não é pré-requisito.
- **Na fatia do conserto:** o **exporter**, porque o conserto o quebra (acima). Não é zelo, é evitar
  introduzir um defeito.
- **Fatia própria:** o rechaveamento dos 5 `argMax` para `started_at`. Depois do conserto os dois
  critérios **concordam** (índice e `started_at` derivam do mesmo evento de join), então a troca é
  provadamente neutra — e é justamente por ser neutra que ela merece medição própria: se o número mudar,
  mudou por outro motivo. Continua valendo fazê-la, por dois motivos que o conserto não remove:
  o índice não é ordenação total, e o TTL de 4 h do contador o reinicia em 0 numa sessão longa
  (`started_at` não tem essa borda).

### ⚠️ Achado adjacente — a REGRA de atribuição erra em suspend/resume, e nem o conserto nem o rechave a corrigem

`_session_agent_attribution_sql` atribui a sessão ao **último** primário não-sintético. Na sessão
`5553c72a` esse é o segmento nativo de **13 ms** que apenas processou o resume — não o humano que passou
30 s preenchendo o formulário. Depois do conserto isso fica **determinísticamente errado**, que é pior de
notar do que aleatoriamente errado. Vale para toda sessão do padrão delegate→humano→resume: a lente de
qualidade credita a máquina pelo trabalho do humano.

Não é bug do índice — é a regra "último primário" encontrando um caminho de execução que ela não previu.
O conserto certo depende da **transição como primeira classe** (D4 do ADR): com a lacuna nomeada, dá para
atribuir ao segmento que fez o trabalho em vez do que fechou a porta. **Registrar como entrada do arco de
workflow, não deste conserto.**

**Dívidas adjacentes que apareceram junto:**
- **O e2e 23 não pode pegar isto** — `23_contact_segments.ts:532-552` publica join **e** left já com o
  índice correto, então nunca exercita o caminho onde o left zera. Teste que passa por não tocar o
  defeito.
- **`ConversationParticipantEventSchema` não declara `sequence_index`** (`contact-segment.ts:108-139`) —
  o campo trafega sem validação em ponto nenhum do pipeline.
- **Contradição de doc, precisa de decisão humana:** `arc5-segments.md:23,106` diz escopo **por pool com
  reset**; `adr-contact-segments.md` e `CLAUDE.md:852` dizem **por sessão sem reset**. O código segue o
  segundo.
- **TTL do contador** (`session:{sid}:segment_seq`, 14400 s) — sessão com >4 h entre dois joins reinicia
  em 0. Segunda fonte de duplicata, plausível e **não medida**.

---

## Modelo journey/session/segment — ADR fechado, spec pendente *(2026-08-10)*

Fixado em [`docs/adr/adr-journey-session-segment-model.md`](docs/adr/adr-journey-session-segment-model.md):
três definições + discriminador por **identidade de contato** (não por duração), a **regra dual** de
escopo, pertença de journey com **uma** regra (o resto é filtro), **transição como primeira classe** (D4),
`journey_id` como projeção **com reconciliação** (D5), *workflow declara / journey observa* (D6), merge
como reparo de proveniência não observável (D7), **porta externa de resume como pré-requisito** (D8) e
**definição única de duração** (D9).

**§8 todo medido** (2026-08-10, ambiente pós-`--wipe`): legado **zero** em todas as tabelas ⇒ **não há
backfill**; transição **observável** num ciclo real (suspend → delegate a humano → submit → resume →
`resolved`, 6 min 23 s de lacuna); TTL do `pipeline` ~24 h ⇒ `current_step` cobre o recente, não o
histórico; `journey_aliases = 0` ⇒ a reconciliação da D5 nasce sem dado de teste; **não há re-carimbo de
`opened_at`**.

**Achados adjacentes registrados no ADR, nenhum investigado:**

- **`sequence_index` não ordena** — medido `0, 0, 2` numa sessão e `0, 0` noutra. Ordenar por
  `started_at` é o único caminho correto hoje; código que use o índice como chave de ordem está errado.
- **Composição de segmentos varia por caminho** — item de pull não gera segmento de fila, push gera.
  Contar segmentos para inferir ciclos é frágil por natureza (reforça a D4).
- **`handle_time_ms` com dois comportamentos vivos** + um terceiro que o `CLAUDE.md` afirma e o código
  registra como adiado. Virou **D9**, dentro deste arco.

**Spec escrita 2026-08-10** — [`docs/product/workflow-arc-implementation-spec.md`](docs/product/workflow-arc-implementation-spec.md).
Fases 0→4 (declarar a borda · porta externa de resume · transição · duração · remoção), cada uma com
instrumento e número previsto. Dois achados do inventário estático mudaram a spec antes da primeira linha
de código:

- ⚠️ **A "porta externa" não é externa.** `/channel/webhook/{slug}` (`channel-gateway/main.py:1302`) e
  `/v1/channels/...` (`:1387`) são rotas do MESMO app na MESMA porta (`docker-compose.demo.yml:1185`);
  não há proxy para `/channel` em `vite.config.ts` nem no `Dockerfile`, e **não existe nginx versionado no
  repo**. A separação é o filtro `allowed_origins={"external"}` (`:1347`) — de código, não de topologia.
  O `CLAUDE.md` opõe os dois como se a borda existisse. Virou a Fase 0: **declarar** o requisito de deploy,
  não construir a borda no meio deste arco.
- ⚠️ **O TTL de `{tenant}:resume_tokens` é do HASH, não do token** (`index.ts:463`, `webhook.py:1723`,
  `:2152`): o hash é compartilhado por todo o tenant e o último escritor redefine o prazo de todos. Hoje é
  higiene; quando o prazo virar contrato com um terceiro ("seu link vale 48 h"), vira promessa não cumprida
  — um `collect` de 1 h escrito depois encurta o token de 48 h. Entra na Fase 1, junto com a chave
  `resume_meta` que a Fase 2 precisa para cumprir o invariante RMT.

E um terceiro que é de segurança: o **`source` do resume é asserido pelo chamador** (`webhook.py:131-141`)
— sem JWT dá para declarar `source:"supervisor:x"` e obter `acw_supervisor_closed` no registro terminal
durável de 25 h. Já catalogado neste TODO; **abrir a porta externa o torna explorável**, então é gate da
Fase 1, não follow-up.

---

## Porta externa de resume × posse do item de pull — decisão pendente *(achado 2026-08-10, ao escrever o gate da Fase 1)*

A conferência de posse do A5 é **gateada em `approver is not None`** (`webhook.py:1148`
— `if approver is not None and claim_instance_id:`). A porta externa
(`POST /channel/webhook/resume/{token}`, Fase 1) passa `approver=None` por construção — quem tem JWT usa
a porta interna. **Logo ela não confere posse.**

Para um token de `suspend` puro isso é correto e é o ponto da porta: o chamador está retomando a
**própria** execução suspensa. Para um token de `delegate` a uma fila de PULL, o mesmo token é a
conclusão de um **item de trabalho humano** — e aí o A5 existe justamente para impedir que uma submissão
descarte trabalho que voltou à fila ou que outro agente detém.

**Três estados, e só o primeiro é claramente aceitável:**

| Estado do item no árbitro | Hoje pela porta externa | Deveria? |
|---|---|---|
| não existe item (suspend puro) | resume passa | ✅ sim — é o caso de uso da porta |
| `found=False, in_queue=True` (na fila, sem dono) | resume passa | ⚠️ **indeciso** — submete sobre trabalho disponível a outro agente |
| `held_by=X` (humano detém) | resume passa | ❌ **não** — descarta trabalho em curso |

O terceiro caso é errado sob qualquer política e não precisa de principal para ser recusado: **é
propriedade do ITEM, não do chamador** — o mesmo ramo (2) do A5, aplicado sem JWT. O obstáculo é
mecânico, não conceitual: `_routing_work_task_holder` exige `pool_id`, que a porta externa não recebe;
derivá-lo do ledger `work_task` da sessão é o caminho.

**Não implementado de propósito nesta fase.** O gate `gate_external_resume.sh` nunca reivindica o item,
então mede sempre o segundo estado — um portão que só sabe medir o caso indeciso não pode julgar o caso
errado. Fechar isto pede um passo que **reivindique** primeiro (Console/`work_queue`), e aí a recusa vira
verificável. Ordem sugerida: junto da Fase 2, que já vai mexer no que o resume lê antes do consumo.

⚠️ **Não confundir com a Camada F.** O lock dá unicidade (só um resume vence); ele não diz *qual* dos
dois deveria vencer. Posse é a pergunta que sobra depois da unicidade.

---

## Cenário e2e 28 falha por config, não por lógica — e o achado reforça a Fase 4 *(medido 2026-08-11)*

Rodando `E2E_EXTRA_ARGS=--workflow-review`, o 28 estoura 60 s. **Não é regressão do arco de workflow** — a
evidência é que a `evaluation-api` nunca chega a tentar o resume (nenhuma linha nos logs dela; os
`webhook resume` do channel-gateway são todos da corrida do gate, horas antes). O cenário morre antes.

**Causa raiz, com a linha:** `POST /v1/workflow/trigger` devolve **502 Bad Gateway**. Essa rota é proxy
para `channel-gateway/v1/channels/webhook/{flow_id}` e **repropaga o status do gateway** — 502 é o código
reservado a *inalcançável*. E o serviço `workflow-api` **não declara `PLUGHUB_WORKFLOW_CHANNEL_GATEWAY_URL`
no `docker-compose.demo.yml`** (envs em `:1356-1363`): cai no default `http://localhost:8010`, que dentro do
próprio container é ELE MESMO. Conexão recusada, 502, sempre.

⚠️ **Mesma família do defeito que a Fase 4a evitou por um triz na `evaluation-api`** — default `localhost`
dentro de container, degradando em silêncio. Lá o env foi adicionado junto com o repointe; aqui nunca foi.

**Defeito secundário, visível no mesmo log:** o polling do 28 chama `GET /v1/workflow/instances/` (com
barra final) → FastAPI responde **307** → o cliente refaz em `/v1/workflow/instances` **sem os parâmetros**
→ **422**, em laço até o timeout. A barra final custa a query string no redirect.

**Por que isto REFORÇA a Fase 4, em vez de bloqueá-la:** o trigger legado está quebrado no demo e ninguém
percebeu, porque o cenário 28 é o **único** gatilho dele — e o motor de review por workflow que o 28
exercita já está classificado como legado inerte (`CLAUDE.md` § Arc 6). É evidência de que *nada vivo
depende dessa rota funcionar*. **Não consertar a config**: a 4d remove a rota. Consertar só faria o
inventário de chamadores voltar a incluir um caminho que estamos removendo.

**O que fica pendente de verdade:** a Fase 4a segue com **fiação provada e comportamento não provado** — o
28 não consegue exercitá-la, porque quebra antes. O gate honesto da 4a precisa ser outro: um teste que
dispare contestação→revisão sem passar pelo trigger legado.

---

## Remoção física do legado de workflow por token *(aberto pela Fase F, 2026-08-07)*

Executar a decisão do ADR §7.8.4. **Remover:** `workflow.webhooks` + CRUD + `POST /v1/workflow/webhook/{id}`;
`workflow.instances` e o resto do lifecycle 410; `WebhooksTab` + a rota órfã `/workflow/calendar`.

⚠️ **NÃO remover junto sem análise própria:** o tópico `workflow.events` e o `skill-flow-worker`. O
cenário e2e **18** depende do worker, e a **evaluation-api consome** `workflow.events` para
`suspended`/`completed` (motor de review legado, reactive-only). Matar o tópico junto seria repetir o erro
que a Fase F acabou de desfazer — tratar como pacote coisas que só estão adjacentes.

**Gate:** o `probe_webhook_endpoint_inventory.sh` já reprova se `F3 > 0`; depois da remoção, F3 passa a ser
estruturalmente 0 e a checagem vira testemunha (mesma reclassificação que os contadores de fallback
sofreram na Fase E).

📄 **Doc a corrigir junto:** `docs/guias/webhook-patterns.md` § "Padrão 1" descreve exatamente este
caminho legado (`POST /v1/workflow/webhook/{id}` + registro por `X-Admin-Token`) como se fosse o trigger
canônico. Recebeu um aviso de obsolescência no topo em 2026-08-10, mas o corpo continua ensinando o
caminho errado a integradores — reescrever para o `ChannelEndpoint` faz parte da remoção, não é
follow-up. (O guia também ainda cita `notify` como step depreciado no Arc 16 e o `skill-flow-worker`;
conferir o resto ao mexer.)

---

## Seis serviços rodam SEM logging configurado — todo `logger.info` invisível *(achado 2026-08-07)*

Descoberto pelo gate da Fase C do webhook, que reprovou por não achar uma linha INFO que o serviço
**nunca emitiu**. Causa no `channel-gateway` (já corrigida): `logging.basicConfig` morava dentro de
`run()`, o entry point de `python -m`, mas o `CMD` do Dockerfile é `uvicorn …:app` — uvicorn importa o
módulo e nunca chama `run()`. Root logger no default `WARNING` ⇒ **todo `logger.info` do pacote
descartado em silêncio**, desde sempre. `logger.warning` seguia aparecendo (handler de último recurso),
o que fazia o defeito passar por "log normal".

**Não é isolado.** O `ai-gateway` já carrega um comentário descrevendo o mesmo sintoma ("todo
`logger.info` sumia"), corrigido lá e nunca varrido. Levantamento por `CMD` × presença de `basicConfig`
em qualquer `.py` do pacote — **seis serviços `uvicorn …:app` sem NENHUMA configuração de logging**:

| Serviço | Porta |
|---|---|
| `dialog-api` | 3760 |
| `scheduler-api` | 3650 |
| `mailing-api` | 3660 |
| `analytics-api` | 3500 |
| `calendar-api` | 3700 |
| `config-api` | 3600 — tem `basicConfig` só no `seed.py`, que é job à parte |

Sadios: `evaluation-api`, `pricing-api`, `ai-gateway`, `auth-api` (config no nível do módulo) e os
serviços de console-script/`python -m`, onde a função que configura **é** o entry point
(`routing-engine`, `rules-engine`, `orchestrator-bridge`, `session-replayer`, `usage-aggregator`,
`conversation-writer`, `clickhouse-consumer`).

**Conserto:** o padrão aplicado no channel-gateway — `_configure_logging()` chamado no import, nível por
`PLUGHUB_LOG_LEVEL` (default INFO), `run()` mantido chamando (no-op se o root já tem handler).
**Cuidado ao varrer:** o efeito é aumento real de volume de log em seis serviços de uma vez; fazer com
medição de volume, não no dia em que se precisa da stack quieta. **E a lição de método:** configuração
de logging presa ao entry point ERRADO é a mesma família de "fonte declarativa tem aplicador separado" —
o código está lá, correto, e não roda; o sintoma é ausência, e ausência parece "não aconteceu".

---

## Guard de teardown-hook — falso positivo a cada boot ✅ *(achado 2026-08-07, corrigido 2026-08-10)*

`_validate_teardown_hooks` (`registry_syncer.py`) loga **ERROR** a cada boot do orchestrator-bridge:

```
CONFIG ERROR — pool 'retencao_humano' declares hook 'on_human_end' → pool 'wrapup_detached_ia'
(skill 'skill_wrapup_detached_v1'), but that skill has SUSPENDING step(s) [coletar:delegate]
```

✅ **CORRIGIDO 2026-08-10** (detalhe no `CHANGELOG.md`). Era falso positivo mesmo, mas **o diagnóstico
acima estava errado no ponto que importava** — e o erro sobreviveu porque ninguém abriu o YAML:

> *"Esse hook é `dispatch: detached`"* — **não é.** `retencao_humano.on_human_end` declara
> `dispatch: inline` (`infra/registry/tenant_demo.yaml:377`). A primeira tentativa de conserto seguiu
> esta nota ao pé da letra, isentou só `detached`, e o ERROR continuou de pé no boot seguinte.
> *Descrição de configuração não é configuração.*

A condição real é o espelho de `_is_workflow_dispatch` (`main.py:1775`), extraída em `_runs_as_workflow`:
`workflow ⇔ dispatch == "detached" OU (side == "agent" E dispatch == "inline")`. No **wrap-up unificado**
(Phase 0+1+3) `inline` não quer dizer "roda na conferência" — quer dizer "o Console AUTO-REIVINDICA o
item", sobre a MESMA máquina destacada. `dispatch` governa a ENTREGA; é o `side` que decide se há
conferência. Sobra só **`side=customer` + `inline`** (o NPS, que precisa do WS vivo) para checar.
Entrou junto um contador-testemunha: zero violações sobre zero entradas checadas se declara NÃO CHECOU.

> ⚠️ **Achado adjacente — `_entry_will_dispatch` diz reproduzir os predicados do loop e não reproduz
> mais.** Ele isenta só `detached`, então entrada `agent`+`inline` é contada no barrier `hook_pending` e
> nunca arma `hook_conf` → contador órfão. **Hoje é inócuo**: o ramo `_detached_fired and not
> _inline_dispatched` (`main.py:2025`) fecha o contato na hora e a chave expira no TTL de 4 h. Não foi
> mexido junto de propósito — consertar um contador que nada lê no mesmo movimento em que se conserta um
> guard tira a chance de saber qual mudança causou o quê. Conserto: usar `_runs_as_workflow` nos dois
> lados (hoje ele vive no `registry_syncer`; viraria utilitário compartilhado).

**Consequência na D8:** validar **existência do pool** mantém-se; validar **canal declarado** é
**rebaixado a aviso** — rejeitar quebraria configuração legítima (esta). E a justificativa "fabrica
contato abandonado" **cai**: a fila foi por ausência de agente logado, que é comportamento correto.
⚠️ A checagem **F4b da sonda é geradora de falso-positivo** e precisa virar advisory (não deve
mais puxar `exit 1`).

**Próximo:** fase B (seed idempotente das 10 linhas `internal`) + guard da D8 + conserto do dado do
`crm-callback` — nessa ordem, respeitando a D7 (o fallback sai por último).

**⚠️ Adjacência encontrada, NÃO consertada — fatia própria:** o cenário e2e **13** descreve o ciclo
pré-Arc 19 inteiro. Além da Parte F, as **Partes C e E** batem em `persist-suspend` e `complete`,
ambas 410 — ou seja, o cenário não pode passar como está, e provavelmente já não passava. Reescrevê-lo
é decidir *o que ele deve provar sob o modelo unificado*, não limpeza mecânica. Some-se ao item
já aberto "Fixtures do e2e ainda falam AgentType": há um bolsão de e2e que envelheceu com o Arc 19.

### Lacuna 3 — não era defeito, era descrição errada *(avaliada 2026-08-05, fechada sem código)*

O item dizia *"fila pull nunca tem teto de espera"* e prometia conserto de **uma linha** (mover o
`continue` de `dispatch_mode == "pull"` para depois da varredura de `max_wait_exceeded`, hoje em
`routing-engine/main.py:1240`). Movê-lo seria **regressão**, por três razões lidas no código:

1. **A varredura fecha um CONTATO, não um item.** `_emit_queue_timeout` (§495-514) emite mensagem de
   cortesia, `session.closed` ao gateway, `contact_closed` com `outcome=abandoned` e segmento
   sintético `role=queue`. Item de wrap-up/aprovação/formfill não tem cliente — seria contato
   abandonado **falso** no ledger, a mesma contaminação que a Camada F acabou de limpar.
2. **O teto que passaria a valer não é o do pool.** Sem `queue_config.max_wait_s`, `attended_wait_s`
   é 0 e o pool cai no ramo da fila muda, herdando `queue_max_wait_default_s` = **1800 s**. Itens de
   trabalho expirando em 30 min por um default que ninguém configurou para eles.
3. **Já existe autoridade de expiração, e não é esta.** `router.py:1107-1112` nomeia o caso:
   `work_task_expire` faz `ZREM` + delete do JSON no **nunca-reivindicado**, apaga a lease e devolve
   a **vaga** (pela lease ou pelo semáforo). A varredura não faz nada disso — deixaria ledger
   `work_task` e vaga para trás, e seriam duas autoridades sobre o mesmo item.

**A fila pull TEM teto**: o `timeout_hours` do delegate. O que a evidência original observou de fato
— *"o prazo do item vem do delegate, não da fila"* — está correto; o que estava errado era chamar
isso de ausência de teto. **A linha está no lugar certo.**

**O que sobra, e é pergunta de produto, não defeito:** se a fila pull deve ter um teto **próprio**,
ele é de **visibilidade/SLA** (o item está exposto na inbox há tempo demais), não de abandono de
contato — e o produtor seria o árbitro (`work_task_expire`), nunca esta varredura. Só abrir se
alguém pedir o número.

**Lição (a segunda vez na mesma semana):** o item envelheceu com um **conserto proposto** embutido, e
o conserto é que estava errado — mais perigoso que um item vago, porque parece pronto para executar.
O comentário em `main.py` §1238-1262 agora carrega o porquê, para o próximo leitor não re-derivar.

### Lacuna 2 — o que fechou e o que não *(2026-08-03)*

O item pedia *"volume antes de decidir o reaper"*. A leitura de código achou algo que não
depende de volume, e por isso não esperou por ele: **a vaga nunca voltava**.

**Sequência real, medida no código** (wrap-up default, `timeout_hours` 24 h):

| t | O que acontece |
|---|---|
| 0 | claim: `ZREM` da fila · vaga ocupada no semáforo · lease com TTL **180 s** · bridge abre o segmento |
| 180 s | a lease expira e **nada reage** — sem reaper, sem heartbeat. Item invisível a todos, vaga ocupada, segmento aberto. `work_task_holder` passa a falhar ABERTO |
| 24 h | o scanner do channel-gateway dispara `work_task_expire` → limpa ZSET/JSON/lease; o bridge fecha o segmento com `acw_expired` |

**O defeito:** o `work_task_expire` derivava o dono da vaga **da lease** — exatamente o que já
expirou no cenário que o motiva (~480× de diferença entre os dois prazos). `instance_id` saía
vazio, `release_instance` não era chamado, e cada claim abandonado subtraía uma vaga do agente
**até o SET inteiro expirar**. Não era frequência, era aritmética.

**E o docstring mentia duas vezes.** `_claim_lease_key` dizia *"com a vaga ocupada até o reap de
ocupantes órfãos passar"* — e o `reap_stale_occupants` só remove ocupante cuja sessão tenha
`session:{sid}:closed`. Num claim abandonado o delegate está **suspenso** e a sessão está
**aberta**: o reap passa ao lado e nunca a toca. Não havia "até". *A nota anterior (07-30) já
havia corrigido um heartbeat inexistente e o substituiu por uma segunda rede inexistente —
correção pela metade é mais cara que o erro, porque ganha data recente e passa por conferida.*

**✅ Fechado (fix 2a):** `work_task_expire` consulta a lease primeiro e, faltando ela, acha o
ocupante pelo semáforo (`find_occupant_instance`). O que tornou isso seguro foi a **F1**: o membro
leva o pool no 3º campo, então a busca discrimina `(sessão, pool)` e devolve `None` — com log —
quando o único ocupante daquela sessão pertence a outro pool, que era o risco usado para justificar
depender só da lease. Qual via respondeu vai no log e em `claimed_via`. `was_claimed` deixou de
reportar `False` para item que FOI reivindicado. Testes: 2 novos em `test_pull_release_snapshot.py`
(213 na suíte) + `infra/test/mutation_claim_lease_slot.sh` (M1 e M2, 6 atribuições).

**⚠️ 2b REENQUADRADA PELA MEDIÇÃO (2026-08-04) — a descrição abaixo estava errada.** O texto que
ficava aqui dizia: *"entre os 180 s e o prazo do item o trabalho está fora do ZSET e nem o próprio
dono consegue retomá-lo; um reaper que re-enfileirasse fecharia isso"*. Foram medidas as duas
metades, e **as duas caíram**:

- o dono **recupera** o formulário depois de um F5 — não por restauração de estado, mas porque o
  mcp-server **replay-a o `conversation.assigned`** na reconexão ao pool;
- o item **não fica** reivindicado durante a janela: ele **volta à fila**. O estado observado é
  `unclaimed`, não `orphaned` — e o reaper proposto faria o que o defeito já faz sozinho.

Ou seja, o gate *"medir `orphaned` antes de construir"* nunca produziria número: o estado que
aparece é outro. Ver § abaixo — o que existe é o defeito **oposto** ao descrito, e mais grave.
Probes: `infra/test/probe_invisibility_window.sh` (as duas lentes) e
`probe_console_restore_after_reload.sh` (as duas observações de F5).

> **A regra que este caso deixa:** *posse de um item de trabalho não pode morar só numa chave cujo
> TTL é menor que o prazo do próprio item.* Quando mora, o caminho de limpeza chega sempre depois
> da testemunha, e a limpeza fica incompleta **sem nada ficar vermelho**.

### ❌ ABERTO E GRAVE — um F5 no Console devolve à fila um item em trabalho *(achado 2026-08-04)*

Procurando o número da 2b, apareceu o defeito contrário. **Um reload do Console (≈2 s de WS
fechado) é interpretado como abandono do atendimento**, e o item reivindicado volta a ser
reivindicável enquanto o formulário segue na tela do primeiro agente. Medido ponta a ponta em
`formfill_demo` (probes `probe_reclaim_duplication.sh` e `probe_requeue_culprit.sh`):

| t | Evento (log real, session `dbdb1e94…`) |
|---|---|
| 10:56:30 | `work_task_claim: claimed … occ=1` — item sai do ZSET, vaga tomada, form na tela |
| 10:56:53 | `WS closed: pool=formfill_demo` — **o F5** |
| 10:56:55 | `agent_disconnect published` → `G7-decision: remaining=0 → continuation=False` → `agent_done published to lifecycle (human agent)` |
| 10:56:55 | `agent_disconnect: last human dropped — re-routing to pool=formfill_demo (contact kept alive)` |
| 10:56:55 | `Queued session=… pool=formfill_demo — no agents available` — **de volta ao ZSET** |
| 10:56:59 | `Forwarding conversation.assigned …` — o form REAPARECE na tela por replay do pub/sub |

Estado final conferido: item no ZSET **e** instância ocupando vaga
(`dbdb1e94::bfa4d4b2::formfill_demo`) **e** lease ausente. Dois donos possíveis para o mesmo
trabalho: quem puxar entra numa sessão em atendimento, e o primeiro submete um item que não detém.

**A causa não é a lease.** É `main.py:6634` — a política *"último humano caiu, devolve o contato ao
pool para não perder o cliente"*, correta para **contato de cliente**, aplicada a um **item de fila
pull**, onde não há cliente esperando: há um formulário com dono, prazo e token de resume.

**E a devolução é feita pelo caminho ERRADO.** O re-route republica em `conversations.inbound` um
evento de **seis campos** (`session_id`, `tenant_id`, `customer_id`, `channel`, `pool_id`,
`started_at`) — não pelo `work_task_release`, que é o caminho que a Frente 1 construiu para
devolver item de pull. Todo o resto é reconstruído pelos **defaults do Pydantic**, então o JSON da
fila *parece íntegro* (as chaves estão lá) com os valores perdidos:

| Campo perdido | Consequência |
|---|---|
| `assigned_to`, `fallback_to_pool_after_s`, `assigned_at_ms` | a reserva author-bound morre — **um wrap-up vira reivindicável pelo pool inteiro** (Camada B anulada) |
| `conference_id` | o claim anexa como primary solto, ocupante vira `{session}::`, o re-claim bate no dedup do bridge e nunca reanexa (o "Bug B" que o `PullInboxPanel` documenta) |
| `work_item_deadline` | TTL do JSON cai no default de 4 h contra prazo de 24 h → o membro do ZSET sobrevive ao JSON e o item passa a mentir (defeito que `models.py:92-97` já descreve como consertado no caminho normal) |
| `auto_attend` | o hand-off de vaga do wrap-up inline (Phase 2) não acontece |

**Terceiro efeito, já denunciado pelo próprio sistema:** `close_reason: transporte
'agent_disconnect' não mapeado — segmento sai SEM close_reason`. **Não é uma linha no
`_TRANSPORT_TO_CLOSE_REASON`:** o mapa serve DOIS domínios — em `main.py:5755` o valor é o
`close_reason` do CONTATO (enum fechado, `CloseReasonSchema`) e em `:6401` ele carimba o SEGMENTO
humano (vocabulário livre, já com literais próprios: `task_submitted`, `acw_expired`, …). No
`agent_disconnect` o contato **não fecha**, então estender o mapa compartilhado escolheria um
domínio em silêncio — exatamente o palpite que o docstring da função existe para impedir. É a
**lacuna 6 com produtor concreto**, e o conserto é separar os dois mapas.

**Desenho fechado em 2026-08-04:** [`docs/adr/adr-work-item-requeue-and-agent-affinity.md`](docs/adr/adr-work-item-requeue-and-agent-affinity.md)
(D1–D8, alternativas descartadas com o motivo, fases A→F). Os três pontos abaixo são o que a
discussão precisava resolver e já estão resolvidos lá — ficam como registro do caminho.

**Fase A (D6) ✅ VALIDADA 2026-08-04** (13 pytests routing + 7 channel-gateway + smoke 19 PASS; ver
CHANGELOG). Posse do item passou a ter
**registro durável no árbitro** (`{t}:pool:{p}:claim_record:{sid}`, TTL do `work_item_deadline`),
escrito no `work_task_claim` e apagado em `release`/`expire`/**re-parque**; `/v1/work_queue/holder`
responde `{found, instance_id, claimant_user_id, via, in_queue}`; o A5 do `handle_resume` virou
**quatro ramos** e recusa (403) quando ninguém detém **e** o item está no ZSET.
⚠️ **A D6 original — conferir contra o ledger `work_task` — foi EMENDADA ao implementar:** o ledger
não carrega claimant, e seu `assigned_to` é *reserva* (vazio em item pooled ⇒ fail-open intacto;
enganoso após o transbordo). Motivo completo no ADR § D6 Emenda. Gates:
`infra/test/smoke_claim_possession.sh` + `test_claim_possession_record.py` (routing) +
`test_resume_possession_check.py` (channel-gateway). **A Fase A NÃO fecha a duplicação** — ela impede
o segundo dono de *submeter*.

**Fase B (D1) ✅ VALIDADA 2026-08-04** (14 pytests bridge + 14 routing + smoke 24 PASS + e2e com F5
real no Console; ver CHANGELOG). O bridge pergunta ao ÁRBITRO se a instância que caiu detém o item
(`claim_record` da Fase A — só o caminho pull o escreve, então contato de cliente nunca casa) e, se
detém, devolve por `work_task_release` em vez do re-publish de seis campos. Medido no item real
`413b9f75…` pós-F5: `conference_id` e `work_item_deadline` **preservados** (antes vinham `null` e
`""`). ⚠️ `first_queued_ms` **saiu do escopo**: não é campo do JSON, é chave própria escrita com NX,
já preenchida na rota real (D2 emendada).

**Fase C (D3) ✅ VALIDADA 2026-08-04** (19 pytests routing + 14 bridge + smoke 30 PASS). A queda
devolve o item **reservado ao agente que caiu**, reusando `assigned_to` + `fallback_to_pool_after_s`
da Camada B (a *carência* foi descartada no ADR). A distinção decisiva é o CHAMADOR: o bridge manda
`reserve_to_previous: true` (queda); o botão "Return to queue" não manda (desistência) — e o default
é **não** reservar, senão o botão passaria a esconder itens do pool em silêncio. Nunca sobrescreve
`assigned_to` autoral. **Janela por AUTHOR-BOUND × POOLED, não por `-int` × cliente:** `-int` =
reserva **permanente** (wrap-up não transborda — aplicar transbordo a trabalho author-bound é erro
de categoria, ver migration `20260730000000_pool_internal_queue`); pooled (aprovação e afins) =
`drop_reserve_window_default_s` 30 s, config-api ns `routing`, UI-editável em Configuration ›
Platform. D2 já vinha satisfeita pela Fase B (não há contador de rodadas, e nenhum foi criado).
⚠️ **O seed do config-api é job separado** (`docker compose run --rm config-seed`) — rebuildar o
serviço NÃO insere chave nova.

**Fase D (D5) ✅ VALIDADA 2026-08-04** (14 testes + e2e no Console). A duplicação **visual** fechou:
o replay não vinha do pub/sub, vinha de `pool:pending_assignment:{pool}` (TTL 300 s, reentregue na
reconexão), onde as duas guardas existentes não pegavam o caso — workflow SUSPENSA (sem
`session:closed`) e `instance_id` batendo (mesmo agente). Guarda nova no **mcp-server** pergunta a
posse ao árbitro; veredicto em função pura `shouldDropOnPossession` (`lib/assignment-filter.ts`),
com os **mesmos 4 ramos** do submit (A) e do drop (B) — a regra existe em três lugares no mesmo
formato. E2e: F5 sobre item reivindicado deixa a tela em "Waiting for next contact…" e o item só na
inbox, com crachá de reserva.

**Aberto na Fase D:** `pool:pending_assignment` não é apagado no `work_task_release` (vive até o TTL
e é descartado na entrega) — conserto no produtor é follow-up opcional.

**Fase E (D8) ✅ 2026-08-04 — fechou a lacuna 6.** Os mapas de `close_reason` foram separados
(contato = enum fechado × segmento = vocabulário livre); a queda publica `agent_released`, não
`agent_done`. Duas emendas medidas ao implementar (nada analítico lê `agent_done`; suprimir o evento
seria regressão, porque `remove_conversation` também restaura membership) — detalhe no `CLAUDE.md`
§ ADR de requeue/afinidade e no `CHANGELOG.md`.

**Fase F ✅ 2026-08-04 — ARCO A–F COMPLETO.** Resume terminal-uma-vez (`SET NX` + registro terminal
→ 409 nomeado × 404 honesto); F2 fez o Console ler o 409. *Este parágrafo dizia "Próximo: Fase E"
até 2026-08-07 — ver a 3ª correção no topo da seção.*

**O que decidir antes de codar** (é a "política de filas pull × push" — o enquadramento certo):

1. **Reload não é abandono.** Hoje 2 s e 20 min de WS fechado recebem o mesmo tratamento. Não há
   nenhum sinal que os distinga — e é essa ausência, não a lease, que produz o defeito.
2. **Item de trabalho ≠ contato de cliente** no `agent_disconnect`. Para item de trabalho, a
   resposta certa a um WS caído é (a) **nada** (ledger, token e ctx sobrevivem; o dono volta) ou
   (b) `work_task_release` de verdade — que preserva `assigned_to`, devolve a vaga e apaga a lease.
   Nunca o re-route genérico.
3. A assimetria a **preservar** (não achatar): aprovação é *pooled* e tem transbordo; wrap-up é
   *author-bound* e não tem, de propósito.

**Medido no JSON do item, depois do re-route** (`GET {t}:queue_contact:{sid}`):

```
assigned_to: null   conference_id: null   work_item_deadline: ""
auto_attend: false  skill_id: ""          agent_type_id: null
```

`conference_id`, `work_item_deadline`, `skill_id` e `agent_type_id` **estavam preenchidos** no
enfileiramento original (o ledger da mesma sessão ainda traz `deadline 2026-08-05T10:56:17`): o
re-route os apagou. As chaves seguem presentes — os defaults do Pydantic as recriam —, e é por isso
que o JSON passa por íntegro numa inspeção de campos.

**Consequência do `work_item_deadline` vazio ✅ CONFIRMADA (2026-08-04, pelos TTLs):**
`queue_contact` = **8909 s** restantes (original ≈ 4h01m — o **default**) × `work_task` = **84471 s**
(23h27m de 25 h). O JSON morre ~**20 h antes** do ledger; nesse intervalo o membro do ZSET sobrevive
sozinho e o item fica **listado na inbox e irreivindicável** (`not_in_queue`).

> *Método, e é o terceiro escorregão do dia no mesmo lugar:* a previsão original mandava **esperar**
> a expiração (~14:56 UTC) para provar. O valor que a determina — o TTL — estava legível desde o
> primeiro minuto. Esperar o fenômeno quando o número já está na mão é a versão temporal de
> "esperar volume para decidir", que esta mesma seção critica duas telas acima.

**Não medido:** (a) se um segundo agente logado no mesmo pool consegue de fato puxar o item
duplicado — exige dois logins; (b) se o wrap-up `-int` perde o `assigned_to`. Este segue **DEDUZIDO**
do evento de seis campos: em `formfill_demo` o campo nasceria vazio de qualquer forma, então o
`null` observado não é evidência sobre item author-bound.

### Portão de deriva do seed do config-api *(proposta — 2026-08-04)*

**O achado.** Ao aplicar as chaves da Fase C, o `config-seed` reportou `inserted=2`: uma minha e
**`survey.link_delivery`, que estava em `seed.py` e nunca fora aplicada**. A fonte declarativa e o
store estavam divergentes havia tempo indeterminado, e ninguém notou — porque não há o que notar. O
modo de falha é mudo dos DOIS lados: a UI simplesmente não lista a chave, e o leitor no código cai
no seu próprio default. Lê-se como *"config com valor padrão"*, não como *"config inexistente"*.

*(Neste caso o valor semeado era igual ao default do código — `mock`/vazio — então nada mudou de
comportamento. Isso é sorte, não garantia: nada obriga os dois a coincidirem.)*

**Proposta (barata).** `infra/test/gate_config_seed_drift.sh`: varre `_SEED` e falha (vermelho) se
existir chave ausente do store, ou presente com **descrição** divergente. Transforma "descobrir por
acaso, meses depois" em CI vermelho no dia. Não deve comparar VALOR — o tenant edita legitimamente
pela tela, e um portão que exigisse valor igual ao seed brigaria com o próprio invariante
"every config field is UI-editable".

**Cuidado de implementação.** O gate precisa consultar o **store** (`GET /config/{ns}/{key}?tenant_id=`),
nunca reler `seed.py`; e `config-seed` tem **imagem própria** (`build:` separado do `config-api`),
então qualquer automação que rode o seed precisa buildá-lo antes — foi essa peça que fez a primeira
tentativa de aplicar as chaves da Fase C rodar o `seed.py` antigo em silêncio.

### Timeouts ainda constantes no caminho da I5 *(arco de consolidação de config)*

Auditoria do caminho todo (2026-07-30). `claim_lease_s` já é config (`routing`); o
`delegate.timeout_hours` é dado de autoria e agora aceita ref. Restam três, todos com casa natural
no namespace `session` (cujos seeds já dizem "currently hardcoded — migrating"):

| Onde | Valor | Chave candidata |
|---|---|---|
| `add_queued_contact(ttl=14_400)` — routing-engine `registry.py` | 4 h | `routing.queue_contact_ttl_s` |
| Buffer `+3600` no TTL do item — channel-gateway `webhook.py` **e** registry (duplicado) | 1 h | `session.work_item_ttl_buffer_s` |
| `run_timeout_scanner(interval_s=60)` — chamado sem argumento em `main.py:374` | 60 s | `session.timeout_scan_interval_s` |

O terceiro é o que mais importa: **é política, não infra** — define a granularidade de toda
expiração da plataforma, e hoje ninguém pode afrouxá-la ou apertá-la sem rebuild.

### ~~Dívida de segurança encostada nesta fatia~~ ✅ **RESOLVIDA 2026-08-03**

`PLUGHUB_JWT_SECRET` ligado no compose do `mcp-server-plughub`. O gate deixou de ser de
intenção e passou a ser de autenticação. Smoke: `infra/test/smoke_bff_jwt_verification.sh`
(4/4 — sem token 401, forjado 401, **expirado 401**, válido 200).

**O escopo era maior do que esta nota dizia.** Ela registrava a assinatura não verificada;
o decode-only **também não lê `exp`**, então o TTL de 1 h do access token era decorativo
nas rotas do BFF — token vazado valia para sempre. Registro original abaixo.

`mcp-server-plughub` **não recebe `PLUGHUB_JWT_SECRET`** no compose da demo, então
`verifyJwtPayload` cai no fallback de desenvolvimento (decodifica sem verificar assinatura) — vale
para TODAS as rotas de UI do BFF, incluindo o novo gate `supervisor|admin`. Não foi wirado aqui de
propósito: ligar o segredo muda o comportamento de autenticação do BFF inteiro e merece fatia
própria, não um efeito colateral da I5. Enquanto isso, o gate é de intenção, não de autenticação.

---

## Wrap-up unificado — resíduos após a Phase 2 ✅ *(arco fechado 2026-07-27, ver CHANGELOG)*

**Polish (não bloqueia):** latência do auto-atendimento (~2-3s do poll da inbox) → instantâneo bombando o
`refreshSignal` do `PullInboxPanel` no `conversation.assigned`. **Agora é seguro**: antes da Phase 2 o claim
instantâneo AUMENTARIA a chance de chegar antes do release (`-1` → cai na inbox); com o hold, as duas ordens
são cobertas. E: UI para a config de `dispatch` inline/detached do hook (hoje só YAML — invariante "config
UI-editável" pendente para hooks de pool).

**Camada E2 restante:** ~~**E2f**~~ ✅ (2026-07-29) · ~~**Camada F**~~ ✅ **2026-07-30** (F1 atribuição,
F2 G1 no relatório, F3 pull direcionado 5/5, F4 expiração — ver CHANGELOG). **Arco A–F completo.**
Resíduo da F: a **lease** não foi medida (a sonda observou a chave de outra sessão), e o que ficou
provado é que o **prazo** devolve a vaga. ⚠️ **Atualizado 2026-08-03** — a frase seguinte dizia
*"a lacuna 2 segue como estava"*, e não segue: o prazo **só** devolvia a vaga porque a sonda pegou
o caso de lease viva; com a lease expirada (o caso normal, 180 s × 24 h) a vaga ficava presa. Ver
§ I5 → "Lacuna 2 — o que fechou e o que não". *A medição que faltou é a que teria mostrado isso.*
*(E2e — produtor do marker `acw_pending` — **saiu de escopo** com a remoção da Camada C, 2026-07-29.)*

**Resíduo herdado da seção `close_reason` (podada em 2026-08-03; o fix está no CHANGELOG de 07-30):**
o `_TRANSPORT_TO_CLOSE_REASON` cobre **6 transportes**; qualquer outro agora produz `close_reason`
ausente **com WARNING**, em vez do `agent_hangup` inventado de antes. **Gated em evidência:** completar
o mapa só se o WARNING aparecer em produção — `infra/test/check_close_reason_persisted.sh` tem uma
asserção que o varre nos logs. *Ausência barulhenta é o estado desejado, não o pendente.*

**Cleanup:** ~~`infra/test/smoke_acw_gate.sh` órfão~~ — não existia mais (item stale) · ~~`acw_gate` como config
sem leitor~~ ✅ **removido ponta a ponta (2026-07-29, ver CHANGELOG)**: schemas, Prisma (migration
`20260729000000_drop_pool_acw_gate`), `pools.ts`, routing-engine, platform-ui (tipo, `PoolsPage`, i18n en+pt-BR)
e as 4 superfícies de doc. **Não reviver o enum** — um gate de ACW futuro se desenha sobre a VAGA.

---

## Wrap-up como fonte de dados — arco de 4 fatias *(discussão 2026-07-29, fatia 1 em curso)*

> **Origem:** a E2f começou como "tirar a sessão de wrap-up da contagem de TMA" e a discussão a
> reenquadrou. A sessão de wrap-up não é ruído a excluir — é **fonte de dados** (serviços
> executados, FCR, motivo), cruzável com Evaluation. Isso muda a ordem: garantir que o dado seja
> gravado de forma consultável vem ANTES de construir relatório, senão os primeiros meses de
> histórico se perdem.

**Achado que motiva o arco:** o `segment_outcome_record` (`tools/segment.ts:67-75`) tem contrato
**fixo de 4 campos** (`classificacao`/`resumo`/`escalation_reason`/`proximos_passos`) e tudo
desemboca em `outcome`/`issue_status`/`handoff_reason` (texto livre concatenado). O DialogForm, ao
contrário, é genérico: dá para acrescentar "serviço executado" no editor hoje — e a resposta
**some sem log**, porque o skill não passa e a tool não aceita. Formulário genérico × tool de
contrato fixo = funil que descarta em silêncio.

> **✅ A perna do descarte foi CONSERTADA em 2026-07-30** (ver CHANGELOG): `resumo` e
> `proximos_passos` agora têm colunas próprias (`segments.wrapup_summary` /
> `wrapup_next_steps`) e são gravados em TODA disposição. O que **permanece** deste arco é o
> outro lado do funil: campo NOVO acrescentado no editor do DialogForm segue sem chegar à
> tool (contrato fixo de 4 campos) — é a fatia 3.

**Evidência ao vivo (F1, 2026-07-30)** — o funil é mais estreito do que "campo novo no editor":
descartava campo que o formulário JÁ TINHA. Wrap-up submetido com `resumo="zxzxzx"` e
`proximos_passos="wwww"`; o segmento da origem gravou

```
outcome: resolved   issue_status: resolvido   handoff_reason: NULL
```

porque a tool só montava `handoff_reason` quando `outcome !== "resolved"`. Num atendimento
**resolvido** — o caso mais comum — o resumo que o atendente escreveu não ia a lugar nenhum, e a
tela não dava nenhum sinal disso. O `issue_status` (classificação crua, em português) é o campo
que prova a atribuição por referência: nada mais no sistema o escreve.

**Conserto (2026-07-30):** colunas próprias `wrapup_summary`/`wrapup_next_steps`, escritas em toda
disposição pelos DOIS produtores (destacado e inline). **`handoff_reason` ficou intacto de
propósito** — ele define `handoff_rate` (`countIf(handoff_reason != '') / count()`), e escrever o
resumo ali levaria a taxa de repasse a ~100%: trocaria perda silenciosa por métrica que muda de
sentido sem avisar. Prosa também não caberia em `agent_business_events` (D2: `value` é numérico,
nominal vive na categoria). Sonda `infra/test/check_wrapup_prose_persisted.sh`.

**~~Resíduo~~ — era STALE, medido e derrubado em 2026-07-31.** A nota dizia que o caminho
**inline** (`_apply_wrapup_to_segment`) só conhece `wrapup_resumo`, e que portanto
`wrapup_next_steps` só seria preenchido pelo destacado. A sonda mostrou o contrário: os dois
atendimentos (um `resolvido`, um `escalado`, ambos pelo hook `dispatch: inline`) gravaram
`wrapup_next_steps`. Um campo que aquela função **não recebe na assinatura** não poderia estar ali —
logo o produtor foi o `segment_outcome_record`.

**Causa da defasagem:** a Phase 3 (wrap-up unificado) aposentou o inline antigo, e o inline de hoje é
**auto-atendimento sobre a mesma máquina destacada** — mesmo `skill_wrapup_detached_v1`, mesma tool.
`_apply_wrapup_to_segment` (`main.py:3010`, acionado em `process_routed` por
`pipeline_state.results.wrapup_classificacao`) servia o especialista de conferência `wrapup_ia`, que
saiu do `tenant_demo.yaml:445`. O único emissor daquela chave é `agente_wrapup_v1.yaml`, que **nenhum
pool deploya** (`grep` em `infra/` só acha o comentário de remoção).

**Consequência a tratar:** `_apply_wrapup_to_segment` e `agente_wrapup_v1.yaml` são candidatos a
**código morto** — sem produtor vivo. Não remover sem confirmar que nenhum tenant fora do demo
deploya o skill; enquanto ficarem, ensinam um modelo que não é mais o corrente (foi exatamente o que
produziu esta nota errada).

### Fatias

| # | Entrega | Estado |
|---|---|---|
| 1 | **E2f** — atributo `purpose: contact\|internal` no pool + filtros no analytics | ✅ 2026-07-29 (resíduo: TMA por agente sobre `segments`) |
| 2 | **Arc 12 `segment_id`** em `agent_business_events` (plano A+C já decidido, seção própria) | ✅ 2026-08-03 — **coluna existe, e nunca recebeu dado real** (ver abaixo) |
| 3 | **Capture de wrap-up** — roteamento no `segment_outcome_record` | ✅ **backend 2026-08-03** (smoke 14/14) — **resta o editor** |
| 4 | **Relatório de wrap-up** — cai sobre `/reports/agent-events/*` (série/summary/categorias já existem) | **destravada** — o Arc 12 passou a ter dado real com `segment_id` |

**Medição de 2026-08-03 que reenquadra as fatias 3 e 4** (`infra/test/probe_block2.sh`):

| Onde o wrap-up grava HOJE | Medido |
|---|---|
| `segments.wrapup_summary` + `segments.wrapup_next_steps` | **13 segmentos**, o último em 2026-08-03 18:06 |
| `agent_business_events` (Arc 12) | **1 linha, de seed**, zero com `segment_id` |

Logo a fatia 3 **não acrescenta captura — ela muda o sink**, e ao fazê-lo passa a ser o
primeiro produtor vivo do Arc 12.

**D6 — os dois sinks COEXISTEM** *(decidido 2026-08-03)*. Não é uma migração: é uma divisão por
natureza do dado, coerente com a D2 desta seção e com `clickhouse.py:470` (*"prosa não cabe em
`agent_business_events`: lá `value` é numérico e o nominal vive na CATEGORIA"*).

| Dado | Sink | Por quê |
|---|---|---|
| resumo em prosa, próximos passos | `segments.wrapup_summary` / `wrapup_next_steps` | texto livre não é `value` numérico nem folha de categoria |
| FCR (pontuável), serviço/motivo (nominal) | `agent_business_events` (Arc 12) | é o que agrega, série histórica, cruza por `segment_id` |

**Corolário que a decisão obriga a escrever, senão ela não vale:** para cada pergunta do
relatório existe UMA fonte, declarada. Prosa nunca é agregada; contagem/taxa nunca sai de
`segments`. Escrever nos dois lugares sem essa regra é como nasce a divergência que não
reconcilia — o mesmo defeito que a D3 previne na folha nominal (`options[].value` como lista
controlada, senão `troca_titularidade` × `troca_de_titularidade` viram duas séries).

### Decisões fechadas na discussão

**D1 — o sink roteia por QUEM RESPONDE, não por que métrica é.** O `DialogCapture` já foi desenhado
assim (`dialog.ts:109-112`: *"echoed back to the domain… the domain routes it to its sink"*).

| Captura | Quem responde | Sink |
|---|---|---|
| CSAT/NPS/CES de survey | o **cliente** | `session_signal` → Voz do Cliente (máquina de `dimension`) |
| FCR, serviço, motivo do wrap-up | o **atendente** | `agent_business_events` (Arc 12) |

Violar isso faz a superfície "Voz do Cliente" exibir declaração de atendente como se fosse do
cliente — e contamina a série histórica, que é irreversível. *(Correção registrada: a ideia inicial
de pôr FCR no catálogo de instrumentos do editor pegava o mecanismo certo e o sink errado — o
catálogo desemboca em `session_signal`.)*

**D2 — dentro do `agent_event`, pontuável × nominal é só onde o dado mora.** `value` é
`z.number().finite()` (`agent-events.ts:92`) e o relatório **não agrupa por tag**
(`VALID_GROUP_BY = {category, skill_id, pool_id, agent_type_id}`, `reports_query.py:5684`) — tag
seria gravada e invisível. Logo:

- **pontuável** (FCR): categoria fixa + `value` numérico → `avg_value` do summary **é** a taxa.
- **nominal** (serviço, motivo): folha na **categoria** (`l4`, a regex aceita 2–5 segmentos e a
  convenção usa 3) + `value: 1` → `count` por categoria. Multi-select = N eventos.

**D3 — o roteamento mora na TOOL, não no YAML do skill.** Se o skill passar campo a campo, cada
pergunta nova no editor vira edição de skill + `set-next` + `promote`, e o formulário deixa de
dirigir — que era o ponto. Precedente: `survey_record` compõe server-side (D9 do ADR de scoring).
Corolário de governança: a folha nominal deve vir do **`options[].value` do DialogForm**, que é a
lista controlada, versionada e UI-editável. Só a tool tem como derivá-la. Sem isso a regex valida
só o formato e `troca_titularidade` × `troca_de_titularidade` viram duas séries que nunca
reconciliam.

**Brinde de D1+D2:** FCR passa a ter três fontes independentes — **declarado** (agente, wrap-up),
**percebido** (cliente, survey) e **observado** (voltou na janela? `root_session_id` da Journey, já
existe). A divergência entre elas é indicador de qualidade melhor que qualquer uma isolada, e cruza
com Evaluation pelo mesmo `segment_id`.

### ⚠️ Questão ABERTA — serviços executados por múltiplos agentes *(marcada 2026-07-29, discutir)*

Num atendimento orquestrado, **vários serviços** são executados e **especialistas** (IA ou humanos)
executam parte deles. Como consolidar isso num wrap-up?

**Posição preliminar (a validar na discussão):** não se consolida *dentro* do wrap-up. Serviço
executado é fato de **(segmento, momento)** — quem executou sabe, e sabe na hora. O humano no fim
não sabe o que o especialista de IA fez três passos atrás; pedir que re-declare é lossy por
construção e duplica um fato que já existe. É o invariante do CLAUDE.md (*nunca guardar fato de
escopo estreito em campo de escopo largo — derivar onde o escopo é conhecido*).

Consequência: cada agente emite `agent_event` **no seu próprio segmento**, e "serviços do contato"
é a **união sobre os segmentos da sessão** — uma query na leitura, não um campo de formulário. O
wrap-up fica com o que só o humano sabe no fim (disposição, FCR declarado, resumo).

Isso **eleva a fatia 2**: sem `segment_id` no `agent_business_events`, as marcações de todos os
agentes caem na mesma sessão sem dizer quem executou o quê. O item do Arc 12 deixa de ser só
"destrava o cruzamento com Evaluation" e vira pré-requisito da própria contabilização de serviços.

**Desdobramento de UI a discutir:** se os serviços já estão marcados, o formulário de wrap-up pode
**exibi-los** (o briefing já carrega contexto da origem) para o humano confirmar/complementar, em
vez de digitar do zero.

---

## Visibilidade seletiva da sessão de wrap-up em Analytics/Sessions *(proposta 2026-08-11, ver ADR §7)*

> **Origem:** operador rodou um E2E completo e não achou o wrap-up em `Analytics > Sessions` — nem como
> segmento (não é; gravação por referência, §3α D3 do ADR), nem como sessão própria (é, mas some). Causa:
> E2f (`pools.purpose = 'internal'`) exclui de forma **incondicional**, sem parâmetro de override, e isso
> vale mesmo com `accessible_pools` liberado — não é bug de permissão, é o comportamento como fechado.
> **Desenho fechado em [`docs/adr/adr-wrapup-detached-pull.md`](docs/adr/adr-wrapup-detached-pull.md) §7**
> (emenda ao ADR já dono do trade-off, não ADR novo). Este item é só o rastreio de implementação.

**Decisão (ADR §7):** visibilidade ≠ contagem. Todo agregado (TMA, "N contacts", métricas de
pool/agente) continua excluindo `purpose=internal` sem exceção — E2f não é reaberto. A **listagem**
(`/reports/sessions`) ganha parâmetro opcional `scope: contacts|all` (default `contacts`, idêntico ao
comportamento atual). Associação ao contato pai é por **`origin_session_id`** (já gravado de forma
confiável nos dois modos de dispatch — inline e detached — desde o fix de 2026-07-27), não por Journey;
Journey artificial de 1+1 para todo wrap-up foi descartada por poluir `/reports/journeys` com processos
triviais.

### Fatias

| # | Entrega | Estado |
|---|---|---|
| 1 | `scope=contacts\|all` em `GET /reports/sessions` — filtro condicional em vez de incondicional; cabeçalho de contagem sempre lê `scope=contacts` mesmo com a tabela expandida | ✅ **2026-08-11** (ver CHANGELOG) — `meta` passou a devolver `total` (paginação) + `total_contacts` (cabeçalho) + `total_internal` |
| 1b | **Marcar a linha como interna na resposta** — o veredicto `purpose=internal` é computado no backend (`_internal_pools_for`) e descartado; a UI recebe só `pool_id` e não tem como saber | ✅ **2026-08-11** — `is_internal` por linha (`_mark_internal_rows`) + `meta.internal_pools_known` (contagem, não flag de saúde). Entra no CSV de graça (`_to_csv` tira as colunas da 1ª linha) |
| 2 | Coluna/badge "Origin" na linha da sessão interna (quando `scope=all`), linkando para o `session_id` pai via `origin_session_id` | ✅ **2026-08-11** — coluna **`parent`** ("Contato de origem"), **não** `origin`: `lista.columns.origin` já é **ANI** nesta tabela (e `destination` é DNIS); reusar o nome daria duas "Origem" com sentidos diferentes na mesma linha. Só renderizada com o toggle ligado — fora dele não há linha interna, e coluna vazia prometeria vínculo inexistente |
| 3 | Toggle "Incluir sessões internas (wrap-up, dispatch)" na UI (`ListaTab.tsx`, ns i18n `contacts`), desligado por padrão; tag visual por `row.is_internal`. Cabeçalho lê `meta.total_contacts`, paginação lê `meta.total`; com `meta.internal_pools_known == 0` **não oferecer o toggle** (não há como distinguir nada — não prometer o recurso) | ✅ **2026-08-11** — medido na tela: desligado `12 contacts`, ligado `12 contacts · 9 internal` com o cabeçalho **imóvel** (é a imobilidade, não a aparição das linhas, que prova o §7.2). Toggle desabilitado durante o fetch: o `pendingRef` do `load` descarta requisição concorrente, e um clique em voo seria no-op silencioso |
| 4 | Isentar o drill-down de UMA journey já aberta (`journey → sessions → segments`) do filtro E2f — sempre mostra sessões internas associadas, independente do `scope` da listagem topo | ✅ **2026-08-11** — mesma válvula do `session_id` em `_fetch_sessions` (`if not session_id and not root_session_id`), com controle negativo no teste (a listagem sem `root_session_id` TEM de manter a exclusão) |
| 4b | **Segundo número no card da journey** — "3 contatos · 1 interna", para o card não discordar do drill que ele expande | ✅ **2026-08-11** — `internal_session_count` por pós-passe (`_attach_journey_internal_counts`), bounded à página, no padrão do `_attach_journey_signals`. **Não** entrou como agregado da query principal: para contar as internas ali elas teriam de entrar no `WHERE` e contaminariam `channels`/`pool_ids`/`open_count` e o wall-clock do processo — o G1 reaberto um nível acima |

`spawned_from_root` **não** precisa de isenção: sessão interna nasce com `journey: "inherit"`, então
nunca atravessa a fronteira e nunca aparece nessa lista.

**Limites conhecidos do `scope=all` (fatia 1, as-built):**

- Mostra sessão de **pool interno**, não "tudo que é interno": hook que roda NA CONFERÊNCIA (NPS
  inline) não tem sessão própria e é filtrado pela regra do CANAL, que `all` **não** relaxa (relaxá-la
  duplicaria sessão ativa — ver CHANGELOG). O rótulo do toggle tem de dizer isso.
- ~~**`format=csv` herda o `scope`** e exporta os dois domínios sem coluna que os separe.~~ ✅ fechado
  pela 1b: `is_internal` está na linha, e o `_to_csv` monta o cabeçalho a partir das chaves dela.
- **Escopo ABAC — premissa REFUTADA por medição (2026-08-11); sobra um caso estreito.** A redação
  original dizia que a linha da SESSÃO de wrap-up carrega `wrapup_detached_ia` (pool webhook, não
  espelho), e daí que supervisor com escopo restrito ligaria o toggle sem ver nada. Medido:
  `sessions.pool_id = 'retencao_humano-int'` — o **espelho**, que `_with_internal_mirrors` já deriva.
  `wrapup_detached_ia` é o pool que **dispara** o workflow (execution metadata do trace); o que sobra na
  linha é o do último roteamento, isto é, o pool onde o humano reivindicou o item. **Para o wrap-up
  atendido não há problema de escopo.**
  **O que resta medir:** wrap-up que nunca é reivindicado (expira por prazo, `acw_expired`) pode nunca
  ser roteado a um espelho e reter o pool webhook em `sessions.pool_id` — nesse caso a linha só aparece
  para quem tem `wrapup_detached_ia` no escopo. Repetir a query sobre uma sessão expirada; se confirmar,
  decidir entre incluir o pool webhook no escopo derivado ou logar a ausência.
  ```
  SELECT session_id, pool_id, origin_session_id FROM plughub_demo.sessions FINAL
  WHERE tenant_id='tenant_demo' AND session_id LIKE '%<sufixo>'
  ```
  (banco = `plughub_demo` no demo, `plughub` por default; **`analytics` não existe em ambiente nenhum** —
  é o nome usado nos testes e o `{db}` interpolado nas queries.)
  Se vier o espelho, apagar este bullet e corrigir a nota gêmea no CHANGELOG (fatia 4b, "Achado de
  passagem"); se vier `wrapup_detached_ia`, então há DOIS produtores de `pool_id` para a mesma sessão e
  o item deixa de ser sobre ABAC.

**Guardrails (não reabrir o que E2f fechou):** nenhum endpoint de agregado aceita/lê `scope`; `scope=all`
não se estende a `/reports/journeys` (listagem topo), só a `/reports/sessions` e ao drill-down do item 4.

**Não-objetivos:** contar sessões internas em TMA/contagem de contatos; Journey sintética para contato
sem processo multi-sessão; mostrar o conteúdo respondido do wrap-up (`wrapup_summary`/`wrapup_next_steps`,
já existente em `segments` mas sem UI nenhuma) — gap real, mas separado, ainda sem item próprio aqui.

---

## Linha do tempo única do contato — "o que esta sessão originou" ✅ *(S1–S4 concluídas 2026-08-11 — as-built no CHANGELOG)*

> **Origem:** revisão da fatia 3 acima. O toggle entrega as sessões internas como LINHAS da listagem,
> mas a leitura é ruim — ordenadas por tempo, a interna pode cair páginas longe do pai. Discutindo,
> ficou claro que **o problema não é do wrap-up**: o sistema gera sessão a partir de sessão em pelo
> menos cinco situações (`collect` → sessão-filho de contato · `workflow_trigger` → processo ·
> dispatcher→worker do outbound (fan-out) · link de survey outbound · wrap-up destacado), e nenhuma
> tem superfície que a mostre **no lugar em que aconteceu**.

**Diagnóstico:** não falta modelo, falta PROJEÇÃO. Os dois eixos já são de primeira classe —
pertença (`root_session_id` / `spawned_from_root`) e natureza (`is_internal`, fatia 1b). O que não
existe é a leitura de **1 salto**: *o que esta sessão originou*. Temos "o que houve DENTRO dela"
(segmentos) e "o processo INTEIRO" (journey, que esconde de propósito o sub-limiar — `significant_only`
+ exclusão de pool interno). O wrap-up sumiu exatamente nessa lacuna: não é insignificante, é
sub-limiar.

**Decisão 1 — a relação reconciliadora é PROVENIÊNCIA, não journey.** *(pergunta levantada: "não
descarto usar journey para conciliar as visões")* Journey continua sendo o fecho transitivo
(proveniência ∪ alias), filtrado a contatos e com limiar — a visão de PROCESSO. A timeline é a
projeção de **1 aresta** da mesma relação, sem filtro. Mesma relação, três leituras:

| Leitura | Relação | Filtro | Pergunta |
|---|---|---|---|
| Timeline "originou" | `origin_session_id`, 1 salto | nenhum | o que houve neste contato |
| Journey / Processos | fecho transitivo ∪ alias | contatos + limiar | qual foi o processo |
| Listagem + toggle (✅) | — | pool interno | o que existiu, sem saber o pai |

**Nunca** materializar journey para todo contato: o discriminador do `CLAUDE.md` é *"nasceu um contato
NOVO?"*, e a sessão de wrap-up **não é contato** (sem cliente, pool interno). Fazer tudo virar journey
mata o discriminador e transforma o `significant_only` em ruído ou em mentira. Precedente: as duas
remoções de contêiner largo desta base (`WorkflowInstance`, entidade `Journey` do Arc 10).

**Decisão 2 — timeline ÚNICA, não bloco separado.** Uma sessão originada é um **evento na vida do
contato**, e a tela de Segments já é uma timeline. Bloco no rodapé destrói a informação principal do
caso `collect`: que o atendimento ficou parado N minutos esperando a filha. A distinção
interno×contato×processo vira **tag na linha**.

**Decisão 3 — filha com `journey: new` LINKA, não expande.** Expandir a subárvore desfaria o corte que
alguém pediu ao usar `journey: new` (é a aresta T5, `spawned_from_root`). Linha "originou o processo X"
+ link para a Vista Processos.

**Divergência ACEITA (não é bug):** a timeline dirá "4 originadas" e o card da journey dirá "1 sessão".
São domínios diferentes (journey exclui interna e conta contatos). Mesma classe do par card×drill da
fatia 4b — resolve-se **rotulando os domínios, nunca unificando as contagens** (guardrail ADR §7.2 um
nível acima).

### Fatias

| # | Entrega | Nota |
|---|---|---|
| S1 | Param novo em `GET /reports/sessions` filtrando `origin_session_id = <sessão>` (filhas de 1 salto) | ✅ **2026-08-11** — 3ª isenção do filtro de contato + **ignora a janela** (filha nasce depois do pai). **Não** reusa `root_session_id`: o fecho transitivo penduraria aqui as filhas do contato IRMÃO |
| S2 | Prosa do wrap-up na linha do segmento que a gravou | ✅ **2026-08-11** — custo zero de backend: o dado já chegava de `/reports/segments` e a UI o descartava |
| S3 | Linhas "originou" intercaladas na timeline por tempo, com tag (interna \| contato \| processo), duração, status e link | ✅ **2026-08-11** — + trilha de ancestrais no breadcrumb (achado na validação: abrir a filha perdia o caminho de volta ao contato) |
| S4 | Drill da prosa → formulário + respostas, como num segmento de agente | ✅ **2026-08-11** — lista pergunta→resposta na janela de execução, com o DialogForm como **dicionário de rótulos** |

**Armadilha de versão — resolvida na S4 pela via barata, mas não fechada:** o formulário é MUTÁVEL e a
resposta é HISTÓRICA. A implementação itera pelas CHAVES DA RESPOSTA e usa o form só como dicionário
(chave desconhecida aparece crua, nunca com rótulo inventado). Isso impede o rótulo MENTIROSO, mas não
recupera o rótulo CERTO de uma pergunta que foi editada: ela mostra o texto de hoje sobre a resposta de
ontem. Fechar de verdade exige snapshot do form na gravação — é o que o link de survey web já faz no
`create`. Gatilho para reabrir: primeira edição real de um `dialog_*` que já tenha respostas gravadas.

**Não-objetivos:** materializar journey por contato; entidade "sessões relacionadas"; expandir
subárvore de `journey: new`; mexer em qualquer agregado (a timeline é leitura, não contagem).

---

## Capacidade, licenças e isolamento entre pools *(A e B ✅ 2026-08-02/03 — histórico no CHANGELOG; resta C + fatia 4)*

> **Podada em 2026-08-03: 503 → 81 linhas.** O as-built das fatias F1–F5b e P1–P3 mora no
> `CHANGELOG.md` (14 entradas, de *"fatia 1: tag de pool no membro do semáforo"* a *"pré-requisito
> da F3, F5 e o fóssil em quarentena"*) e o modelo vigente no `CLAUDE.md` § Operational Visibility
> e § Admissão de sessão. Duas casas para a mesma informação é o defeito que este projeto evita em
> toda parte — e a segunda casa já estava mentindo em dois pontos (registrados no CHANGELOG da
> poda, não aqui: contradição resolvida é histórico).
>
> · Desenho de relatório: [`docs/product/shared-capacity-pool-as-tag-design.md`](docs/product/shared-capacity-pool-as-tag-design.md)
> · ADR de licenciamento: [`docs/adr/adr-agent-licensing-and-pool-isolation.md`](docs/adr/adr-agent-licensing-and-pool-isolation.md)

| | Problema | Estado |
|---|---|---|
| **A** | relatório mente: `available` por pool ignora consumo dos irmãos; KPI soma recurso compartilhado | ✅ F1–F5b + P1–P3 (2026-08-02/03) |
| **B** | teto de licença mistura moedas (`C = ai + human`) e gateia sessão humana | ✅ fatia 3 (2026-08-02) — sobrou `kind:ai ≤ C_ai`, gate único |
| **C** | piso/teto por pool, licenças materializadas, cerimônia de deploy | **ADIADO por medição** — é capacidade NOVA, não conserto |

### O que segue aberto

**1. Fatia 4 / defeito C — adiada, e a medição que a adiou precisa ser REFEITA antes de reabrir.**
`Q1` (IA roda > 1 sessão por instância?) e `Q2` (alguém usa `session_reservation`?) saíram do banco
ERRADO: `plughub_demo.public.pools` era fóssil congelado, o agent-registry vive em
`plughub_registry`. A perna de CÓDIGO de Q1 sustenta a conclusão (`instance_bootstrap.py:1054-1072`
usa *"Concurrent sessions: N"* como número de INSTÂNCIAS, cada uma `max_concurrent=1` ⇒ para IA,
instância == sessão); a perna de DADO não. **Q2 não é re-mensurável** — a coluna foi dropada em
02/08, e as evidências que sobram (`infra/registry/*.yaml` não declara reserva em pool nenhum; o
fóssil também marcava zero) apontam para a mesma conclusão sem serem o banco vivo no momento da
decisão. *O método estava errado mesmo com o resultado provavelmente certo.* O script já aponta
para o banco certo e aborta se não for (portão Q-1, `_prisma_migrations` como discriminador):

```bash
bash infra/test/measure_capacity_licensing_baseline.sh tenant_demo
```

**2. `max_concurrent_sessions` ainda soma as moedas** — hoje só como teto de PROVISIONAMENTO
(`lib/capacity.ts`, `deployViolation`: Σ declarada nos slots ≤ C). A fatia 3 deixou o defeito C mais
VISÍVEL, não menor; trocar esse gate agora seria construir a fatia 4 no meio da 3. Anotado no
docstring do próprio arquivo, para quem chegar nele primeiro não repetir a conta.

**3. Itens independentes achados na medição de 2026-07-31 — DATADOS, não verificados desde.**

| Achado | O que se mediu | Ressalva antes de agir |
|---|---|---|
| vazamento de admissão | 3 sessões presas em `…:admission:shared` (todas `kind:ai`, pool `survey_journey_wf`) com zero instâncias ocupadas; o reconciler não as liberou | o SET `shared` **não existe mais** (fatia 3). O mecanismo sobrevive em `kind:ai`: a liberação depende do marcador `closed` + reconciler. **Re-medir antes de tratar como defeito** |
| pools fantasma | `formfill_demo`, `ramal_test`, `survey_journey_wf` — resíduo de smoke com estado vivo | limpeza, não defeito |
| tenant fantasma *(colhido na poda de 03/08)* | `smoke_gprobe_pricing`, capacidade 1, aparece no `sync_all` do boot do pricing | mesma família da linha acima; herdado da seção "Pricing → quota Redis", podada |
| **`webhook_skill_id` é um pool** | com 3 instâncias: **o nome de um campo virou id de pool** | o mais concreto dos três; bug de seed/provisionamento |
| `retencao_humano-int` fora de `public.pools` | espelho vive só em runtime ⇒ **invisível a validação em tempo de config** | é por desenho (ADR §9.1: pool interno resolve licenciamento no pai). Registro, não defeito |
| `fila_humano` com `agent_kind = ai` | pelo nome deveria ser humano; muda licenciamento e hook | dado de tenant, não código |

**4. Costura única `acquire`/`release`** — arco separado; ver a seção própria mais abaixo.

### O que a poda NÃO pode levar junto

- **Não somar linhas de pool.** `Σ available(pool)` conta o mesmo recurso uma vez por pool e **não é
  corrigível na linha do pool**: a linha está certa (aquele pool alcança mesmo N vagas), somá-la é
  que não pode, e a informação de sobreposição não está lá. Vale igual na série
  `pool_occupancy_peaks` — `__total__` e `__capacity_{kind}__` são deduplicados (F4c), a linha do
  pool **não** é, por construção. E `by_channel` é PROJEÇÃO, não partição: instância que serve 2
  canais conta nos dois, então Σ entre canais excede o total do tipo.
- **Duas descontinuidades a marcar no eixo**, se a série virar base de dimensionamento:
  (1) `peak_concurrency` trocou de fonte (`active_count` → `used_here`, 2026-08-02) — o contador
  antigo derivava para CIMA, então o histórico tende a estar **superestimado**; degrau não medido.
  (2) `admission.shared_series` morreu e `admission.ai_series` começa em 2026-08-02 — não é
  renomeação: o denominador mudou de `370 − Σ reservas` para `C_ai = 360` e o numerador deixou de
  contar sessão humana.
- **`peak_concurrency` nunca responde "ocupação média"** — o registro por minuto já é máximo, e média
  de máximos não é média de ocupação. Média exigiria soma+contagem de amostras por minuto (campo
  novo, não pedido).

### Alternativas descartadas — não reabrir sem argumento novo

Reservar vagas de sessão por pool (fragmenta o recurso — contraria o invariante *"capacidade é do
RECURSO"*); só piso sem teto (sem teto não há limite a impor); empréstimo do piso ocioso (garantia
que exige espera não é garantia); baixar o TTL do snapshot (cura por expiração); métrica única de
"degradação" (valor plausível que esconde privação, espera e atribuição); adotar `current_sessions`
em vez do SET de ocupantes (é da mesma família do contador por pool — trocar um contador por outro
não fecha a classe, só muda qual deles vai mentir depois).

---

## Dois pacotes fósseis — `clickhouse-consumer` e `conversation-writer` *(quarentena 2026-08-03)*

Vieram do resíduo acima. Estavam INCONCLUSIVOS no `report_suite_skips.sh` desde sempre, e a
leitura fácil ("a sonda não alcança o serviço") estava errada em ambos: **não há serviço**.
Nenhum dos dois é serviço do compose, nenhum tem `Dockerfile`, e os dois só existem no
`ecosystem.config.js` — a topologia PM2 anterior ao Docker. Também não constam da §
Repository Structure do `CLAUDE.md`.

| | consome | escreve | estado medido |
|---|---|---|---|
| `clickhouse-consumer` | `evaluation.results` | `evaluation_results` (CH) | tópico **não existe** no broker e nenhum produtor no repo o escreve; auto-create desligado |
| `conversation-writer` | `conversations.*` (vivos) | `transcripts`, `transcript_messages` | tabelas **não existem** em `plughub_demo`; ninguém as lê |

Ambos ganharam `README.md` de fóssil no próprio diretório, com a evidência e o que exigiria
reativação. **Não apagados** — mesmo critério da tabela `pools` fóssil: reversível, e o erro
fica visível.

**O `conversation-writer` é o que merece atenção se alguém mexer**: ele consome tópicos
**vivos** e traz `migrate()` embutido no `postgres_writer.py`. Subi-lo não daria erro —
criaria uma segunda persistência de transcrição, paralela ao `StreamPersister`, divergente e
sem leitor. Fóssil que falha barulhento é inofensivo; este falharia em silêncio.

**Falta decidir** (não urgente, e a quarentena já tira o dano): apagar os dois pacotes junto
com as entradas do `ecosystem.config.js`, ou manter. Apagar só o pacote e deixar o PM2
apontando para um script inexistente troca um fóssil silencioso por um erro de boot.

**Terceiro caso da mesma família (2026-08-03):** o trio `skill_survey_runner_v1` /
`skill_survey_outbound_v1` / `skill_survey_trigger_v1` — existe como YAML, **nenhum pool o
deploya**, e as únicas menções fora dos próprios arquivos são exemplos em docstring. Cada um
ganhou a marca de quarentena no cabeçalho. **Armadilha registrada lá:**
`skill_survey_outbound_v1` NÃO é o outbound vivo — o que roda é o par
`skill_outbound_survey_dispatch_v1`/`_worker_v1` (fase 5b), com pool. Nomes quase idênticos,
destinos opostos; quem for apagar precisa olhar duas vezes.

---

## Costura única de aquisição (`acquire`/`release`) *(arco separado, adiado — 2026-07-31)*

O **árbitro** já é único: `claim_instance`, Lua atômica, mesmo semáforo para push e pull. O que está
duplicado é o **entorno**: push faz `selecionar → pontuar → claim → mark_busy → snapshot → publish
routed`; pull faz `gate → ZREM → claim → mark_busy → lease → publish routed`. Mesma sequência, duas
implementações — e as divergências são onde moram os defeitos deste arco: o pull **não escreve
snapshot**, **não checa admissão** nem **pertencimento ao pool** (o `formfill_demo` teve item
reivindicado com `total_instances 0`), e a liberação tem três caminhos (`remove_conversation`,
`release_instance`, o release condicional do `work_task_expire`).

Alvo: um par `acquire(recurso, sessão, conferência, pool, motivo)` / `release(...)` que possua claim +
sincronia do espelho + tag + fan-out de snapshot + lease + publish, compondo os **três portões**
(licença, admissão, semáforo) com uma taxonomia de falha só. Push e pull passariam a diferir apenas em
**quem escolhe o recurso** — algoritmo de score num caso, um humano no outro. Pull é "o humano é o
scorer"; tudo depois é idêntico.

**Não unificar:** admissão responde *"este contato entra no sistema"*, alocação responde *"qual recurso
o atende"* — donos diferentes, colapsá-las é o erro simétrico. Exceções declaradas (throttle de pool
webhook, canal como hard filter) viram parâmetro explícito, não caminho paralelo.

Adiado por decisão (2026-07-31): não há defeito visível ao usuário aqui, e separar mantém a validação
de cada arco capaz de ficar vermelha sozinha. Depende das fatias 1–3 acima.

---

## Auditar `duration_ms` × `handle_time_ms` no analytics *(follow-up do fix de 2026-07-29)*

`sessions` tem `handle_time_ms`; `segments` tem `duration_ms`. O
`/reports/timeseries/handle_time` pedia `duration_ms` sobre `sessions` e falhava desde
sempre, mudo (ver CHANGELOG). **Só aquela função foi corrigida.**

Falta varrer o analytics-api atrás do mesmo engano — qualquer `duration_ms` referenciado
contra `sessions` (ou `handle_time_ms` contra `segments`). O sintoma é sempre o mesmo:
endpoint que devolve vazio com `error: "data_unavailable"` e UI que renderiza gráfico em
branco, sem erro visível.

**Como varrer com proveito:** não basta grep — a coluna certa depende da tabela no `FROM`,
que às vezes é aliasada. Um teste que rode cada query contra o schema real (ou um
`DESCRIBE` comparado com as colunas citadas) acha mais que leitura. Vale considerar
transformar o `except` genérico desses wrappers em log de ERROR com o texto da exceção:
`UNKNOWN_IDENTIFIER` teria denunciado isto no primeiro boot.

---

## `docker compose build` não pega arquivo NOVO — só `--no-cache` *(achado 2026-07-29, causa não investigada)*

Reproduzido **duas vezes na mesma sessão**, em serviços diferentes:

| Arquivo novo | Serviço | Sintoma |
|---|---|---|
| `prisma/migrations/20260729000000_drop_pool_acw_gate/` | agent-registry | boot dizia "28 migrations found" (havia 29 no disco); `migrate deploy` reportava "No pending migrations" |
| `pools_client.py` + `tests/test_pools_client.py` + migration `pool_purpose` | analytics-api, agent-registry | `pytest` → "file or directory not found"; boot → "29 migrations" |

Nos dois casos `build --no-cache <svc>` resolveu na hora. **Edição de arquivo EXISTENTE
entra normalmente** — o problema é só com arquivo/diretório novo, o que aponta para
invalidação de layer de `COPY` (`.dockerignore`, padrão fixo no Dockerfile, ou cache do
BuildKit).

**Por que investigar em vez de sempre usar `--no-cache`:** nas duas vezes o sintoma foi
barulhento por sorte — o pytest reclamou do arquivo ausente e o Prisma contou as migrations.
Um arquivo novo cuja ausência é **silenciosa** (um consumer que simplesmente não roda, um
filtro que não aplica, um cliente que degrada para vazio) não produziria mensagem nenhuma —
só um comportamento que não muda. É o padrão que a § Postura de Engenharia nomeia, na
camada de build.

**Primeiro passo:** comparar `.dockerignore` com o `COPY` do Dockerfile do agent-registry e
do analytics-api; conferir se o build usa BuildKit com cache montado.

---

## ~~Segmento humano do wrap-up NUNCA fecha~~ ✅ **RESOLVIDO DE PASSAGEM — fechado por pente de código 2026-08-05** *(achado 2026-07-29)*

> **Nenhum trabalho novo: o conserto veio junto com o arco de wrap-up unificado.** Verificado no
> código, não no registro: no `session_resumed`, com `_claimant_instance_id` preenchido, o bridge
> consome (`GETDEL`) `participant_joined_at:{inst}` e `segment:{inst}` e publica `participant_left`
> com `agent_type="human"`, `duration_ms` real e `close_reason` de `_wrapup_close_reason`
> (`orchestrator-bridge/main.py` §7971-8038) — **sem depender de `contact_closed` nem do botão
> "Encerrar"**. O `_wrapup_close_reason` (§7671-7692) já emite `task_submitted`/`acw_expired`/
> `acw_supervisor_closed`, ou seja, a fase I5 que este item listava como faltante existe
> (contraparte em `routing-engine/router.py` §1094-1205 `work_task_expire`). E o teardown varre o
> que sobrou: `_sweep_open_human_participants` (§2753-2891) fecha com `close_reason="session_teardown"`,
> idempotente por GETDEL contra o caminho principal.
>
> *Texto original preservado abaixo pelo valor de diagnóstico.*

> **Candidato forte ao "produtor faltante" da seção seguinte.** Os dois itens são
> provavelmente o mesmo defeito visto de ângulos diferentes.

Ao investigar o resíduo "TMA por agente" da E2f, a query mostrou que **toda** sessão de
wrap-up destacado tem dois segmentos, e o do humano nunca encerra:

| Segmento | `pool_id` | `ended_at` | `duration_ms` | `outcome` |
|---|---|---|---|---|
| workflow (`agent_type: native`) | `wrapup_detached_ia` | ok | 23–48 ms | ok |
| **humano** (`agent_type: human`) | `formfill_demo` | **NULL** | **NULL** | **NULL** |

Segmentos abertos desde 2026-07-28 (portanto **depois** da Phase 2, que já havia tocado o
sintoma correlato "vaga do claimante devolvida só por efeito colateral"). O humano
reivindica o item, preenche o form, submete via `workflow_resume` — e o `participant_left`
do segmento DELE nunca é publicado. O `segment_outcome_record` grava no segmento da
**origem**, por referência; o segmento do próprio wrap-up fica órfão.

**Três consequências, em ordem de importância:**

1. **O tempo de ACW não existe como número em lugar nenhum.** Nem para excluir do TMA de
   atendimento, nem para reportar como métrica própria — que era a promessa inteira da
   "segregação, não supressão" da E2f. *(Correção de registro: a afirmação "o TMA do pool
   de wrap-up É o tempo de ACW" foi feita sem verificação e é falsa neste wiring — os
   23–48 ms de `wrapup_detached_ia` são o runtime do workflow.)*
2. **A vaga fica pendurada** até o reap passar — a origem que a seção seguinte procurava.
3. **Segmentos permanentemente abertos** em `segments`. Não poluem
   `mv_agent_performance_daily` (`WHERE ended_at IS NOT NULL`), mas poluem qualquer leitura
   de "participação em aberto".

**Resíduo da E2f fica SUSPENSO por causa disto:** filtrar a lente `sessions_aht` não faz
sentido enquanto a duração é nula — filtraria zeros. Reabrir depois do fix, e aí valendo o
achado de que **filtro por `segments.pool_id` não serve**: o segmento humano carrega
`formfill_demo` (pool `contact`), não o pool interno. O filtro correto é por SESSÃO
(subquery em `sessions`), e a `mv_agent_performance_daily` — chaveada por `pool_id`, sem
`session_id` — não tem conserto por leitura.

**Decisão de configuração pendente (anterior ao código):** o claim do wrap-up deveria ter
**pool próprio** (`wrapup_claim`, `purpose: internal`) em vez de reusar o `formfill_demo`
(`skill_wrapup_detached_v1.yaml:32`, "reusa o pool pull do demo R0"). Com pool próprio, o
tempo de ACW nasce legível por pool, o filtro por `segments.pool_id` volta a servir e a MV
também. Reusar um pool de demo foi conveniência da fatia R0, não desenho.

### Mapeamento concluído (2026-07-29) — não é bug do wrap-up, é lacuna da família pull

**Não há caminho de referência a copiar: a APROVAÇÃO tem o mesmo defeito.** Aprovação,
`skill_formfill_demo_v1` e wrap-up usam o mesmo `delegate`+`pool` → `handle_delegate`
(inbound roteado, **não** `handle_delegate_conference`). Nos três o segmento humano abre e
nunca fecha.

**O produtor canônico e por que ele não é acionado.** `participant_left` de `agent_type=human`
tem só **2 produtores**, ambos em `process_contact_event` (bridge `main.py:6175` e `:5601`), e
**ambos exigem um `contact_closed` em `conversations.events`**. No atendimento normal quem o
publica é o `/api/agent_done` chamado pelo botão "Encerrar" do Console
(`AgentAssistPage.tsx:328` → `server.ts:1887`). Na UI de form-fill **esse botão não existe**
(só "Return to queue") — corretamente, porque item de tarefa não é contato. O `agent_done` que
o caminho pull publica (`main.py:7342-7387`) vai para `agent.lifecycle`, que só devolve a
**vaga**; o analytics não o consome.

**Triplo trinco no caminho A** — mesmo consertando um, os outros seguram: (a) ninguém publica
`contact_closed(agent_closed)`; (b) `_destroy_conference` (`main.py:2483-2497`) **deleta**
`human_agent`/`human_agents` sem emitir nada; (c) a única varredura de participantes existente
(`:5493-5619`) está atrás de `not _ccf_already`, e `_close_contact_layer` seta esse flag 190
linhas antes de publicar (`:2144-2149`) — o ramo "customer_side" documentado é, na prática,
inalcançável a partir dali.

**Vazamento gêmeo:** `work_task_release` (`routing/router.py:714-741`) devolve item, lease e
vaga — e também não emite `participant_left`. Idem o `on_timeout` do delegate.

> **H2 ✅ + H1 ✅ (2026-07-29, ver CHANGELOG).** A medição refinada mostrou **0 vazamentos
> no caminho canônico** (`retencao_humano`) — o produtor canônico funciona; o defeito era
> exclusivo da família pull.
>
> **Falta:**
> - **Resíduo da E2f, agora REAL** — com duração preenchida, o segmento de wrap-up entra
>   na lente `sessions_aht`. Filtrar por `segments.pool_id` **não serve** (o segmento
>   carrega `formfill_demo`, pool `contact`). Fazer o **pool próprio** abaixo torna o
>   filtro trivial e conserta a MV junto; sem ele, exige subquery por sessão.
> - ~~**Resíduo da E2f**~~ + ~~**Pool próprio para o claim**~~ ✅ **RESOLVIDOS (2026-07-30, ver
>   CHANGELOG)** pela ADR [`adr-internal-work-queue-author-bound`](docs/adr/adr-internal-work-queue-author-bound.md),
>   fases **I1–I4**. O segmento humano do wrap-up passou a nascer em `{pool}-int`
>   (`purpose: internal`) e o `_apply_contact_scope` já existente o cobre — o resíduo do TMA por
>   agente desapareceu **sem filtro novo**, e o ACW ficou legível **por pool de origem**.
>   **Falta a fase I5** (sem transbordo + supervisor pode encerrar + TTL `acw_expired` +
>   relatório de pendências por agente). Sem ela um wrap-up que ninguém preenche fica pendurado
>   para sempre — os 87 órfãos em outra roupa. *(Achado a checar junto: o segmento de wrap-up
>   SUBMETIDO fechou com `outcome = NULL`, que é o valor que a D5 reserva para "ninguém
>   preencheu" — o que os separa tem de ser o `close_reason`.)*
> - ~~**Backfill dos 87 já abertos**~~ ✅ **2026-08-03 — 107 → 27** (o número tinha crescido).
>   Instrumento: `infra/test/report_open_human_segments.sh` (corta por 2026-07-30 e
>   CLASSIFICA antes de contar). **Medir antes de limpar mudou a conclusão:** havia 9
>   órfãos PÓS-fix, e a consulta da SESSÃO (todas abertas) provou que são claim
>   abandonado — lacuna 2 — e não vazamento de teardown; logo **H1/H2 segurou**.
>   Deletados só os 80 do `formfill_demo` pré-corte.
> - ~~**Os 17 órfãos do `aprovacao_deploy` — "a aprovação segue produzindo"**~~
>   ❌ **AFIRMAÇÃO REFUTADA 2026-08-03 (2ª medição do mesmo dia).** As 17 linhas vão de
>   **2026-07-16 a 2026-07-24** — a última é SEIS DIAS anterior ao fix. Nenhuma depois do
>   corte. O que sustentava "segue produzindo" era o número ter ido de 9 (29/07) para 17
>   (03/08); mas as linhas subjacentes não são novas, então **o crescimento foi artefato de
>   contagem** (`FINAL` ou filtro diferente entre as duas leituras), não produção. É o § Erros
>   de método item 1 aplicado à medição da PRÓPRIA sessão: um número que cresce é plausível
>   como evidência de defeito ativo, e por isso não foi conferido contra as datas.
>
>   **Nem por isso estava provado o contrário:** zero aprovações desde 24/07 ⇒ zero amostra
>   pós-fix. "Nenhum órfão novo" e "nenhuma aprovação nova" são indistinguíveis na tabela.
>   Fechado com amostra própria — **`infra/test/smoke_approval_segment_closes.sh` ✅ 7/7**:
>   trigger → claim → submit deixa o segmento do aprovador com `close_reason=task_submitted`
>   e `duration_ms=428`. O H1 é genérico (dispara com qualquer `_claimant_instance_id`
>   `human-*`) e cobre aprovação e wrap-up igualmente. Os 17 viraram dívida histórica pura.
>   *(O smoke também fecha uma lacuna à parte: a aprovação não tinha smoke nenhum.)*
> - **As 3 divergências de doc** listadas abaixo.
> - ~~**Validar H1 ao vivo**~~ ✅ **(2026-07-30)** — atendimento real: o segmento de wrap-up
>   fechou com `close_reason=task_submitted` e `duration_ms=89 483`. A corrida não ocorreu, e
>   o **tempo de ACW passou a existir como número** (contato: 11 656 ms no `retencao_humano`;
>   ACW: 89 483 ms no `retencao_humano-int` — 7,7× o atendimento, a distorção que G1 nomeia).

**Conserto proposto (duas camadas, complementares):**
- **H2 (estrutural, primeiro):** varrer `session:{id}:human_agents` emitindo `participant_left`
  **antes** do delete em `_destroy_conference` (`main.py:2483-2497`), espelhando o loop de
  `:5493-5619`. Cobre submetido, devolvido e expirado. Fecha com `outcome` genérico.
- **H1 (por cima):** em `_handle_webhook_session_resumed`, ao lado do `agent_done` de lifecycle
  (`main.py:7342`), emitir o `participant_left` humano lendo o mesmo trio que o produtor
  canônico usa (`participant_joined_at:{inst}`, `segment:{inst}`, `participant_meta:{inst}`,
  todos escritos por `activate_human_agent` em `:892-933` e vivos nesse ponto). **Só o resume
  conhece o `outcome`** — é isto que produz o tempo de ACW como número.
- **H3 descartada:** fazer o Console chamar `/api/agent_done` após o submit — publicaria
  `contact_closed(agent_closed)` e dispararia `on_human_end` no pool do claim, correndo contra
  o `_close_contact_layer` do próprio resume.

**Evidência que dimensiona antes de codar:** listar segmentos humanos com `ended_at IS NULL`
agrupados por pool. Se aparecerem wrap-ups **nunca submetidos** (devolvidos/expirados), H1
sozinha é insuficiente — previsão do código é que ambos vazem.

**Divergências doc × código achadas no mapeamento (corrigir junto):**
0. ✅ **CORRIGIDA (2026-07-30)** — `CLAUDE.md` § Configuration dizia "Skills seguem upsert (são
   código, não config de tenant)". **Falso desde 2026-07-13**: skills são seed-if-absent
   (`registry_syncer.py` §46-53). Consequência que custou um ciclo inteiro de validação: editar o
   YAML de um skill já semeado é **no-op**, reiniciar o bridge não publica nada (só loga o DRIFT),
   e o modo de falha é **sucesso pelo caminho antigo**.
4. `tenant_demo.yaml:123` comentava "dispatch: detached ✅" enquanto a entrada declarava
   `inline` — corrigido em 2026-07-30 junto com a I3.
5. `PresenceSidebar.tsx` não é renderizado por ninguém — 5º órfão, além dos 4 já listados em
   § "Eventos — três superfícies para duas ideias".
1. `docs/arcos/session-conference-lifecycle.md:305-311` diz que o segmento fecha por "agent_done
   OU heartbeat TTL expirado". A perna de heartbeat (`server.ts:3371-3388`) é **gated em
   `sismember human_agents`** — SET que `_destroy_conference` já apagou. A rede não fecha.
2. `docs/adr/adr-wrapup-detached-pull.md:25,143` diz que `segment_outcome_record` "re-publica
   participant_left p/ o analytics" — verdade **só para o segmento da ORIGEM**. O ADR nunca
   menciona que a sessão de wrap-up gera um segmento humano próprio; o desenho não previu quem
   o fecharia.
3. Comentário em `main.py:2204` descreve um caminho `customer_side` que a própria função
   neutraliza (ver trinco (c)).

---

## ~~Vaga só é liberada no `agent_done`~~ ✅ **ORIGEM FECHADA — pente de código 2026-08-05** *(2026-07-28)*

> **A premissa do título deixou de valer: `release_instance` tem hoje TRÊS origens, não uma** —
> `registry.py` §2035 (via `remove_conversation`), `router.py` §933 (`work_task_release`) e
> `router.py` §1184 (`work_task_expire`). O `kafka_listener.py` §308 trata `agent_done` e
> `agent_released` com o MESMO efeito de capacidade, e o bridge publica `agent_released` nos caminhos
> de morte que não concluem trabalho (§6800-6803: `agent_disconnect`/`agent_release_item`). A vaga do
> claimante de pull deixou de ser efeito colateral: §8074-8114 publica `agent_done` explícito com o
> pool da sessão.
>
> **Resíduo que sobra (2 itens, pequenos):** (a) o docstring de `registry.py` §938 ainda repete a
> afirmação antiga *"a vaga só é liberada no `agent_done`"* — comentário obsoleto sobre desenho
> revertido, exatamente a classe que este projeto trata como mentira documentada; (b) a medição de
> frequência do `warning "reap:"` que este item pedia **nunca foi feita**, e não se responde por
> leitura — precisa de log de produção. O caso crash/restart segue coberto só pela rede, o que o
> próprio item já aceitava.
>
> *Texto original preservado abaixo pelo valor de diagnóstico.*

O reap de ocupantes órfãos está **implementado e validado** (ver CHANGELOG): ocupante cuja sessão tem
`session:{sid}:closed` sai do semáforo, nos dois sites onde a lotação pode ser mentira
(`get_ready_instances` e `claim_instance`), com cooldown de 60 s por instância.

**O que continua aberto é a origem.** `release_instance` só é chamado no `agent_done`. Todo caminho de
morte de sessão que não passa por ele segue vazando vaga até o próximo reap — o reap repara *depois*,
não impede. Assimetria que denuncia a premissa: o **hold** de wrap-up tem expiração passiva porque o
desenho previu "wrap-up que nunca chega"; o ocupante real não tem equivalente porque se presumiu que
todo claim termina em `agent_done`.

**Instrumento de decisão:** o `warning` de `reap:`. Ele existe para MEDIR, não só para consertar.

- Se aparecer **raro** (só após crash/restart do bridge) → a rede basta, não mexer.
- Se aparecer **em uso normal** → existe um produtor de `agent_done` faltando. Caçá-lo é melhor que
  seguir reparando: cada linha de `reap:` nomeia o `session_id`, e o `session:{sid}:closed` guarda o
  `reason` (7 d de TTL) — dá para agrupar por motivo de fechamento e achar qual caminho não publica.

Só depois dessa medição decidir se cabe fechar a origem (publicar `agent_done` também nos caminhos de
morte abrupta) ou aceitar a rede como suficiente.

---

## ~~`role` nunca é escrito no hash de participante~~ — **Fatia A ✅ · Fatia B ✅ 2026-08-05** *(resíduo da F5 de identidade por-pool, 2026-07-28)*

> **Fatia B entregue (2026-08-05, ver CHANGELOG § "Roster de participantes").** O bridge passou a
> escrever `session:{id}:participants` dentro de `_publish_participant_event` (funil dos 12 call
> sites), com upsert **atômico em Lua** — obrigatório, porque o bridge despacha com `create_task` e
> não preserva ordem. Os dois leitores de `role` passaram a resolvê-lo do roster
> (`resolveParticipantRole`), que é o escopo do fato, sem fallback para o hash da instância.
>
> **Medido com grupo de controle:** ~97 replay contexts do demo — as 2 sessões com roster têm
> `ReplayContext.participants` = 3 e 4; as ~95 sem roster têm 0, todas. O consumidor que mais
> importava (substrato de avaliação chegando sem participantes) está fechado ponta a ponta.
>
> **Não exercitado, e registrado como tal:** o leitor de `role` em `session_context_get`/`message_send`
> compila e está no artefato em execução, mas **nenhum caminho do demo o chama** (verificado por
> leitura: só a suíte e2e e agentes externos via SDK). A suíte não roda contra o demo — para no seed
> com o 401 do `requireResourceWrite`, que é o **TODO §101**. Fechar aquele item desbloqueia este
> exercício.
>
> **Segue aberto desta seção:** os pré-requisitos **1** (dupla identidade de `participant_id` — o
> especialista de conferência via SDK recebe um `uuid4()` efêmero nunca persistido, `main.py` §3338, e
> por isso não é resolvível pelo roster) e **2** (vocabulário de papel), ambos detalhados abaixo. O
> pré-req 3 (chave órfã) e o 4 (schema) foram fechados pela Fatia B.
>
> **Nit colhido de passagem, não consertado:** a asserção D do `10_masking.ts` é
> `evalHasOriginal || !isMasked` — passa por ausência quando masking não está configurado. Teste que
> não pode reprovar, mesma família dos instrumentos que falharam em 2026-08-05.

> **Fatia A ✅ (2026-07-28, ver CHANGELOG).** A investigação mostrou que o nome `role` cobria **dois
> fatos de escopos diferentes**, e que por isso não existia um único produtor a escrever:
>
> | | **Fato A** — propósito do agente | **Fato B** — papel de participação |
> |---|---|---|
> | Valores | `executor` / `orchestrator` / `evaluator` | `primary` / `specialist` / `supervisor` |
> | Escopo | o ARTEFATO (skill), estável | (participante, sessão) |
> | Consumidores | `evaluation_context_get`, `evaluation_submit` | `message_send`, `session_context_get` |
>
> **Fato A está fechado**: campo `agent_role` no skill (registry), carimbado pelo `agent_login` no hash
> da instância — que é o escopo CERTO para ele, porque o propósito é constante por toda a vida da
> instância. **Fato B segue aberto** e é o que resta desta entrada: NÃO cabe naquele hash (a mesma
> instância atende `max_concurrent_sessions` sessões e é `primary` numa e `specialist` noutra ao mesmo
> tempo — guardá-lo ali colapsa multi-sessão, invariante do CLAUDE.md).

Dois sites ainda LEEM `role` de `{tenant}:agent:instance:{participant_id}` — `session_context_get` e
`message_send` — e **nenhum produtor escreve o campo**. Ambos caem no default.

Consequências vivas:

- A tool MCP `message_send` **não roteia @mention nenhuma**: o gate da F5 exige leitura positiva
  (falha fechada, de propósito). Correto por ora — o Console usa o WS, que conhece o agente pela
  conexão — mas é capacidade desligada por falta de produtor, não por decisão. Fechar quando/se
  existir agente humano via SDK.
- O mesmo default decide **mascaramento** (`session.ts`: `role === "customer" || role === "primary"`
  → mascara) e carimba `author_role` no stream. Como nunca é lido de fato, toda mensagem via
  `message_send` é mascarada e sai como `primary`. Blast radius maior que o do @mention; mesmo
  produtor ausente.

**Fatia B — desenho decidido, não implementado.** Store por participante
`session:{id}:participant:{participant_id}` (hash), escrito pelo bridge no join, generalizando o
`session:{id}:ai_participant:{instance_id}` atual (que hoje cobre só IA nativa e é chaveado por
instance_id). Pré-requisitos levantados na investigação:

1. **Unificar a convenção de `participant_id`** — o bridge publica `participant_id=native_instance_id`
   no Kafka (`main.py:3622`) mas entrega `uuid4()` ao especialista de conferência (`main.py:2863`, nunca
   persistido). Duas identidades para o mesmo participante; nenhum store conserta isso antes.
2. **Produzir o vocabulário** — `_part_role = "specialist" if conference_id else "primary"`
   (`main.py:3489`) é a ÚNICA decisão de papel no sistema; os outros 11 call sites de
   `_publish_participant_event` passam literais. `supervisor` nunca é emitido por caminho nenhum.

   > **Consequência agora OBSERVÁVEL (2026-08-05, na 1ª validação do roster):** numa sessão-filha de
   > `delegate` o roster traz **DOIS participantes `primary`** — o agente nativo do workflow e o
   > humano que reivindicou o item de trabalho. Não é defeito do roster: é este pré-requisito, que
   > antes ninguém via porque nada persistia papel. O vocabulário não tem como dizer "quem executa o
   > processo" × "quem preenche o formulário", e a regra binária (`conference_id` ou não) responde
   > `primary` para os dois. Qualquer consumidor que assuma *um* primary por sessão vai errar.
3. **`session:{id}:participants` é chave órfã** — o `ParticipantSchema`
   (`schemas/src/session.ts:77-88`) já tem a forma exata, é lido por `session_context_get:182` e pelo
   replayer (`replayer.py:303`), e **não tem writer**. Hoje `session_context_get` sempre devolve
   `participants: []` e todo `ReplayContext.participants` vem vazio.
4. `e2e-tests/scenarios/10_masking.ts:235` só passa porque semeia `role` à mão no Redis — o teste
   documenta a ausência do produtor, não a presença.

Correlatos do mesmo arco (fechado — ADR
[`adr-human-agent-pool-scoped-identity`](docs/adr/adr-human-agent-pool-scoped-identity.md)):
`crash_detector.py:144` ainda usa `meta.pools[0]` (mitigado por pular `human-*` em `:98`; o docstring
de `update_instance_meta` agora avisa que o meta é cache, não constante) · **testes de estabilidade
multi-pool** seguem inexistentes, embora a F5 os previsse.

---

## Eventos — três superfícies para duas ideias *(desenho fechado 2026-07-28, não implementado)*

Levantamento do platform-ui achou **três** telas de "Eventos", duas delas cópia literal
uma da outra:

| # | Onde | Conteúdo | Fonte |
|---|---|---|---|
| 1 | Monitor › Sessões → toggle "Eventos" (`MonitorTab.tsx:780` `EventsView`) | **agregado** (categoria, count, sum, avg, first/last seen) | `/reports/agent-events/summary` → `agent_business_events` (Arc 12) |
| 2 | Monitor › Eventos (`Sidebar.tsx:70` → `/contacts/events`) | lista crua | `/reports/events` |
| 3 | Analítico › Eventos (`Sidebar.tsx:123` → `/analise/events`) | lista crua — **mesmo componente do #2** | `/reports/events` |

#2 e #3 montam o MESMO `EventsPage` (`routes.tsx:78` e `:111`); só o grant ABAC difere
(`contacts.operacao` × `contacts.visualizar`).

**Decisão (2026-07-28):** o #1 já É o dash consolidado que Monitor deveria ter — está só no
lugar errado, escondido como toggle. Rearranjo:

- **Monitor › Eventos** passa a renderizar o agregado (conteúdo do #1) — vira dash com
  entrada própria de menu.
- **O toggle dentro de Sessões sai** (`MonitorScope` volta a `sessions | processes`).
- **Analítico › Eventos** fica com a lista crua, sozinha.

Espelha o padrão do produto: Monitor = estado agregado ao vivo; Analytics = detalhe
retrospectivo — a mesma relação que Monitor › Sessões tem com Analítico › Sessões.

**Defeito a corrigir junto:** `EventsView` envia `period=24h` (`MonitorTab.tsx:794`), mas
`get_agent_events_summary` (`reports.py:1431`) só aceita `from_dt`/`to_dt` — o param é
ignorado, a janela real é o default de 7 dias, e o título i18n diz "últimas 24h". Número
que mente.

**Órfãos achados no mesmo levantamento** (não tratados): `AnaliseComparacaoPage` não tem
rota (arrasta `MetricSelector` junto); `ContactsPage` não é importado no router;
`/reports/agent-events/series` não tem nenhum chamador; chave i18n `nav.service.events`
sem item de nav.

---

## Posição na fila — resíduos após o fix do `queue.position_updated` ✅ *(2026-07-27, ver CHANGELOG)*

O evento voltou a ser publicado e `queue_position`/`estimated_wait_ms` são corretos. O que ficou:

- **Nenhum canal consome o evento.** O comentário do código promete "channel-gateway (to inform customer)", mas
  o channel-gateway só assina `collect.events` — **mostrar a posição ao cliente nunca foi implementado**. É
  feature, não regressão: exige consumidor no gateway + render por canal (webchat WS; voz = prompt falado).
- **Ruído do drain na tabela.** O drain periódico re-enfileira o mesmo contato a cada ~5 s e cada ciclo grava um
  par `queued`+`position_updated` (10 linhas para 1 contato em 45 s). Ou o publish passa a ser condicionado a
  MUDANÇA de posição, ou a série é agregada na leitura. Decidir antes que a tabela vire lixo em produção.
- **`available_agents` é enganoso**: conta instâncias no set `ready` (SCARD), não vagas livres — um agente
  lotado ainda aparece como "disponível". Renomear para `ready_instances` ou passar a contar capacidade real.
- **`queue_length` não é persistido**: o payload leva, a tabela `queue_events` não tem a coluna. Se o tamanho da
  fila no instante interessa ao relatório, é `ALTER TABLE … ADD COLUMN queue_length Nullable(Int32)` + a linha no
  `CREATE TABLE` do `clickhouse.py`.

---

## Journey (retorno) — modelo de 3 níveis *(design fechado 2026-07-08, pré-código)*

**Contexto:** o modelo de 3 níveis (N3 negocial `workflow` / N2 acesso a canais / N1 I/O — perfis `agent`) faz
voltar a necessidade de amarrar vários contatos a um processo de longa duração. A entidade `Journey` (Arc 10) foi
removida no Arc 19 Fase F (dualidade contact/workflow; "rastreabilidade via `parent_session_id`, sem entidade").
O retorno é **como lente + camada mínima de alias**, não como entidade.

**Decisão (D1.5):** journey = componente conexa de sessões sob (proveniência ∪ alias), identificada pela **raiz
canônica** valorada em `session_id`. Descartado D1 puro (não resolve cenário 2-unify nem 3-inbound — proveniência
é imutável) e D2 (entidade — reintroduz o que o Arc 19 removeu). Insight: sem merge, `journey_id=session_id` é só
`origin_session_id` replicado; o merge/alias é a única coisa que a derivação por proveniência não expressa.

**Invariantes:**
- `root_session_id` imutável, **nunca null** (param propagado no `delegate`/`collect`/`task` = do chamador; senão
  auto-mint = `self`). Propagação é de plataforma (injetada como o `origin_session_id`), não campo de fluxo.
- Fonte de verdade = `root_session_id` + `journey_aliases`; `sessions.journey_id` = **cache** eventualmente
  consistente (refresh no merge; reads não dependem dele em v1 — resolve por union-find).
- Merge sempre **novo→antigo** (ordem total por `started_at`,`session_id`) ⇒ floresta sem ciclo, sem cycle-guard.
- `journey.merges` = topic de **1 tipo**; proibido reviver entidade/lifecycle/merge-split/`journey.events` (9 tipos).
- Mantém `origin_session_id` (1 salto, desenha o `SessionTrace`) **E** `root_session_id` (raiz transitiva, agrupa).

**Fases:**

| Fase | Entrega | Depende de |
|---|---|---|
| J1 ✅ (2026-07-09, ver CHANGELOG) | `root_session_id` (schemas + CH + nascimento + propagação automática); `journey_id` cache=root no open. Cenários 1 e 2-com-journey. Persistência da raiz via **enrichment central no consumer** (lê ContextStore autoritativo — não repete root em cada evento nem toca routing-engine). Validado E2E (`infra/test/smoke_journey_root.sh`, transitividade W3 origin=W2/root=W1). | — |
| J2 ✅ (2026-07-09, ver CHANGELOG) | `/reports/journeys` (proveniência-only) + filtro `root_session_id` no `/reports/sessions` (drill) + Vista Processos (`AnaliseJourneysPage`, repurpose de `/analise/processos`) + drill 3 níveis + toggle "significativa". Só Analytics (Monitor fica p/ depois). | J1 |
| J3 ✅ (2026-07-09, ver CHANGELOG) | `journey_merge` tool + `journey.merges` + `journey_aliases` + union-find (resolução na leitura via `transform()`; cache `journey_id` **diferido**, não refresh — reads por union-find) + `PendingEntry.root_session_id`. Cenário 2-unify validado E2E; cenário 3 = pipeline pronto, falta o skill disparar a tool. | J1, J2 |
| J4a ✅ (2026-07-10, ver CHANGELOG) | Leitura N3: `session_signal` grain=`journey` + métricas de processo (`business_outcome`, `business_duration_ms`, `signal_count`, `nps_avg`/`csat_avg`/`ces_avg`) no `/reports/journeys` + colunas Outcome/NPS na Vista Processos. | J2 |
| J4b ✅ (2026-07-10, ver CHANGELOG) | Hook **genérico** `on_process_end` (dispara em desfecho terminal, carimba `session.process_outcome`; mecanismo igual aos outros hooks, survey é 1 consumidor). Agente `skill_journey_survey_v1` cria survey OUTBOUND (`survey_link_create`, form `dialog_nps_buttons`) grain=journey keyed na raiz. Validado E2E via trigger slug→pool (`/channel/webhook/{slug}`). | J4a |
| **J4c ✅** (2026-07-13, validado E2E — spec `docs/product/journey-j4c-survey-collect-spec.md`, ADR `adr-outbound-survey-as-collect-contact.md`) | **Survey outbound = contato via `collect` (Arc 19 suspend/resume), não sinal solto.** Modelo 3 camadas: **N3** (workflow de survey, **channel-agnostic**, faz `collect`+suspende) → **N2** (handler `persistCollect` = resolvedor de canal **único e cego ao processo**: alcançabilidade via Resolvedor de Identidade + `channel_policy` declarativo de N3 + consentimento/política como slots plugáveis) → **N1** (sessão-filho **roteada** a um pool de survey, herda `root`→membro da journey). **Opção A + criação LAZY (decidida 2026-07-10):** separa o assíncrono (esperar o cliente) do síncrono (o survey). **(1)** `collect` = convite: N2 **entrega o link + guarda pending, suspende — zero sessão/recurso/metering** até o clique (sem clique→timeout→nada alocado). **(2)** clique com token válido = **inbound PADRÃO** (cliente presente), roteado ao pool de survey → Routing admite (cota + `max_concurrent_sessions`) + Core metera — **limites só no engajamento real**; `dialog_runner` (agente único, DialogForm por config) renderiza **ao vivo** (síncrono → `menu` funciona, e o princípio "agente único interpreta o form" sobrevive). **(3)** fim do survey → `session_closed` + sinal grain=journey no close + `collect.responded`→resume N3 (collect resolve **no fim**). Resolve a regra de perfil (`menu`≠`suspend` no mesmo skill) e o custo de capacidade do assíncrono. "delega"≠step `delegate()` (é inbound, sessão própria). **Segmentação/billing por pool** (sem canal-classe novo, sem carve-out — capacity-based; `max_concurrent_sessions` = botão de volume). Trabalho central: **wirar `persistCollect`** (hoje só `persistDelegate`; `collect` cai em wall-clock). `survey_link_create` = legado/anônimo. **Invariantes:** N3 nunca nomeia canal (só `channel_policy`); N2 nunca ramifica por `skill_id`/`campaign_id` (guard de CI estilo `check_config_invariants.py`); escolha de canal = concern reutilizável. Fatias J4c-1..5. Demo = web+mock; SMS/e-mail/consent/policy = slots futuros por config. | J4b |
| J5a ✅ (2026-07-14, ver CHANGELOG) | `@ctx.journey.*` **vivo** (bridge resolve a raiz canônica → `journey_id` no `/execute` → `journeyId` no engine; TTL próprio de 30d) + **merge acíclico por construção** (aresta raiz→raiz via mapa de aliases no Redis; idade vem do stream canônico, não do `meta` que só o webchat escreve) + 12 testes do `journey_merge`. Validado E2E com escritor e leitor em sessões diferentes da mesma journey, com controle negativo. **J5a-2 ✅ (2026-07-22, ver CHANGELOG):** fechada a **escrita IMPERATIVA** — `context_set` (skill-flow) e `/api/inject-context` (supervisor) gravavam raw no hash da sessão; agora roteiam pelo helper único `writeContextTag` (`journey.*` → hash do processo/raiz canônica, TTL 30d; reusa `resolveJourneyRoot`, sem dep de `@plughub/sdk`). Smoke `smoke_journey_context.sh`. | J3, J4 |
| J5b ✅ (2026-07-14) | i18n dos **enums** na Vista Processos. `status`/`outcome`/`business_outcome`/`channels` chegavam crus da analytics-api e eram renderizados assim (o operador via inglês técnico em pt-BR); a moldura já passava por `t()`, faltavam os **valores**. Reusa `sessions.status.*` (já existia no namespace) e adiciona `enums.outcome.*` + `enums.channel.*` (en+pt-BR) — não duplica dicionário. `defaultValue: <valor cru>` em todos: enum novo no backend degrada para o valor cru em vez de quebrar a tela. `t` passa por **parâmetro** nos helpers (a regra proíbe `useTranslation` fora de componente). `title` guarda o valor cru para debug. | J5a |
| — (app-wide, fora do Journey) | **Guard de rota ABAC**: nenhuma página de `analise/` tem gate próprio — só o Sidebar. Deep-link contorna a UI (o dado segue filtrado por `accessible_pools` no backend). Consertar só a de Journeys seria cosmético; é um item do app. | — |

### Journey — 3 itens pendentes: natureza + mini-plano (levantamento 2026-07-23)

Cruzados contra o código. **São três naturezas distintas** — só o Item 1 é entrega de valor acionável.

**Item 1 — sinal N3 no drill da Vista Processos ✅ ENTREGUE (Fatias 1+2, 2026-07-23 — ver CHANGELOG).**
Painel **PROCESS SIGNAL** no cabeçalho do L2 (desfecho+provisório, duração, NPS/CSAT/CES, `signal_count`);
`csat_avg`/`ces_avg` agora renderizados. Fatia 1 = UI-only (`selectedJourney` no `AnaliseJourneysPage` →
prop). Fatia 2 = filtro `root_session_id` no `/reports/journeys` (resolve canônico, ignora janela+significant)
+ rebusca no `JourneySessions` para deep-link. Validado (clique + deep-link). *Limitação:* fetch direcionado
varre `sessions` por lista de roots-membros — medir se houver journeys enormes sob merge.

**Item 2 — cache `sessions.journey_id` diferido** *(otimização adiada por decisão, não é bug)*. A coluna
existe (escrita = raiz no nascimento) mas **não é refrescada no merge**; reads resolvem por union-find sobre
`journey_aliases` (`_journey_resolved_map`). "Ativar" = refrescar `journey_id` no consumer de merge para
`GROUP BY journey_id` direto. Custo atual baixo (tabela de aliases minúscula, 1 hop pré-resolvido), correção
intacta (cache nunca é lido como verdade). **Só sob pressão de latência/volume medida.**

**Item 3 — guard de rota ABAC** *(dívida app-wide, defesa-em-profundidade/UX, NÃO vazamento)*. Rotas
`analise/*` (`routes.tsx`) sem wrapper — só o `Sidebar` esconde o nav; deep-link renderiza o chrome. O dado
**segue filtrado** por `accessible_pools` no backend (`_apply_pool_scope`), então não vaza. Modelo de correção
já existe no repo: `RequireEvalAccess` (guard por-rota das telas de Avaliação, hoje hard-coded a
`module='evaluation'`) — generalizar (prop `module`) ou criar `RequireAbac` irmão e envolver `analise/*`.
**App-wide** (analise/monitor/config são todos nav-only) — melhor numa passada dedicada, não enxertado no
Journey.

### Journey — Árvore de proveniência (T1–T6) ✅ COMPLETA (2026-07-14/15)

Toda a árvore de proveniência entregue e validada — movida para `CHANGELOG.md` (entradas **"Journey T1–T5"**
e **"Journey T6"**): T1 persistir `origin_session_id` · T2 desfecho = raiz (+ provisório) · T3 `journey:
inherit|new` · T4 `spawn_reason` · T5 UI em árvore + prefixo `PRC-` · T6 rastro forense bidirecional
(`GET /reports/sessions/{id}/trace` + `TraceDrawer`). Bug colateral fechado no caminho: `/reports/sessions`
nunca rodava a query principal (alias-shadowing → fallback mudo pelo tier 3). Design/decisões e não-objetivos
na spec `docs/product/journey-provenance-tree-spec.md` (§9). ⚠️ T2 mudou números já exibidos (desfecho passou
a ser o da raiz) — correção, quebra comparação com prints anteriores.

---

## Deploy de skills — cleanup de campos órfãos *(follow-up do redesenho D1–D4, 2026-07-13)*

Depois do modelo novo de deploy ("uma definição editável + cópia imutável no slot"), ficaram órfãos:
dropar `flow_draft` e `deploy_status` do schema Prisma (agent-registry) e remover o endpoint
`POST /v1/skills/:id/deploy`. Deixados para depois de o modelo novo rodar; histórico completo do
redesenho no `CHANGELOG.md`.

---

## Analytics — revisar workarounds pré-`row_version` *(resíduo do fix de 2026-07-13)*

Com `sessions` já em `ReplacingMergeTree(row_version)`, revisar (e provavelmente remover) os workarounds
de `COALESCE` / `channel=""` no analytics-api que existiam **só** para mitigar a corrida entre tópicos.
Histórico do bug e do fix no `CHANGELOG.md`.

---

## Tópicos Kafka órfãos — achados do saneamento do doc *(2026-07-27, doc ✅ saneado)*

O saneamento de `docs/kafka-eventos.md` (✅ feito, ver CHANGELOG) reconciliou a doc contra o código e expôs
**quatro defeitos reais** — nenhum é de documentação:

> **Propósito declarado (2026-07-27, decisão do dono do produto):** estes eventos são **negociais, de
> MEDIÇÃO** — contam ocorrências nos fluxos de agentes gerados nos skills, para análise e comparação
> posterior. Não são mecanismo (a ação já acontece por outra via) e **não devem ser removidos**: estão
> incompletos, não mortos. Isso muda a pergunta de "remover ou ligar consumidor" para **"onde essa medição
> deve aterrissar"**.
>
> **Substrato que já existe (avaliar ANTES de criar consumidor/tabela novos):** o **Arc 12** faz exatamente
> isso — `agent.events` → ClickHouse `analytics.agent_business_events`, com `category` hierárquico
> (`pool_id.skill_id.metric_key`, decomposto em `category_l1..l4`), endpoints
> `/reports/agent-events/{series,summary,categories}` e integração com a lente de deploy do Arc 6 Fase 2
> (`metrics[]=agent_event:{category}` — "esta versão do skill mudou a taxa de ocorrência?"). Se a medição de
> regras entrar por aí, ganha série temporal, drill e comparação por versão **sem infra nova**.

1. **`rules.escalation.events`** — telemetria de escalação disparada (modo `active`), sem consumidor. (NÃO é a
   via da escalação — correção de um diagnóstico meu errado: `escalator.py:79` chama
   `POST /tools/conversation_escalate` e só depois publica o evento, `:91`.) Falta o destino de medição.
2. **`rules.shadow.events`** — o shadow mode existe para MEDIR o que uma regra faria antes de ativá-la; hoje o
   único registro é um `logger.info`. É o caso em que a medição É a feature.

**Opções para os dois** (mesma decisão): (a) o rules-engine passa a emitir `agent_event` com categoria
(`{pool}.{skill}.rule_escalation` / `.rule_shadow`) e os tópicos `rules.*` são aposentados — reuso máximo;
(b) consumidor dedicado no analytics com tabela própria (mais fiel ao schema atual, mais infra); (c) manter
publicando e aterrissar depois. **Correção pendente no CLAUDE.md** em qualquer caso: a tabela de tópicos lista
`rules.escalation.events` → consumidor `Routing Engine`, o que nunca foi verdade.
3. **`agent.done`** — ✅ **REMOVIDO (2026-07-27, ver CHANGELOG).** Publicação órfã + dupla no mcp-server; teste
   reescrito para cobrir as vias reais. Resíduo: `issue_status` não trafega mais em nenhum tópico (só era
   publicado no órfão; segue validado na entrada). Se o analytics precisar dele, adicionar ao `contact_closed`.
4. **`usage.cycle_reset`** — ✅ **REMOVIDO (2026-07-27, ver CHANGELOG).** Consumo morto no usage-aggregator; o
   reset segue pelo `POST /admin/cycle-reset` (mesma classe). O schema fica em `usage.ts` — se o caminho por
   evento for desejado, falta o PRODUTOR.

Também corrigido na doc (era erro de documentação, não de código): `conversations.events` — o tópico mais
movimentado da plataforma — estava listado como "nome obsoleto que não existe mais"; e cinco tópicos
documentados **não existem** (`conversations.session_opened`, `conversations.message_sent`,
`conversations.abandoned`, `rules.session_tagged`, `gateway.heartbeat` — os três primeiros confundiam evento
com tópico).

**Dívida de contrato:** `conversations.events` não tem schema Zod único, sendo o tópico central e o de maior
fan-in (5 produtores × 6 consumidores). Contraria o princípio "todo evento cross-package tem contrato
validado" registrado no próprio doc.

**Correção pendente no CLAUDE.md**: a tabela de Kafka topics lista `rules.escalation.events` → consumidor
`Routing Engine` e `agent.done` → `Rules Engine, Analytics`. Ambas falsas — atualizar junto com a decisão (1).

**Método:** cross-check contra `packages/analytics-api/src/plughub_analytics_api/clickhouse.py` (DDLs reais) e
`CLAUDE.md § Kafka Topics` (que já está correto e serve de gabarito). Baixo risco, alta clareza — chore de doc.

---

## Resolvedor de Identidade — próximos passos (Fases A ✅ e B ✅; falta a Fase C) *(2026-07-02, cabeçalho corrigido 2026-08-03)*

> ⚠️ **Terceiro cabeçalho stale achado na mesma varredura.** Dizia *"falta Slice 3 + Fase B"*. O
> `CLAUDE.md` registra o **Slice 3 ✅ (2026-07-03)** e a **Fase B completa em 3 fases (2026-07-04)** —
> identidade progressiva, posse de canal por OTP e gate seguro. O que falta é a **Fase C**
> (`external_refs` + merge de clientes, wiring do step CRM `resolve`, `resume_origin=same_channel`,
> transporte real do OTP), e ela depende de haver um CRM.
>
> Os três cabeçalhos stale (este, I5 e Capacidade) têm a mesma assinatura: **corpo mantido, título
> não.** Vale como padrão a vigiar — o título é o que sobrevive à leitura rápida, então ele mente
> para mais gente do que qualquer parágrafo interno.

**Estado:** Fase A completa e validada (ver `CHANGELOG.md` § Slices 1/2/4 e `docs/product/identity-resolver-fase-a-plano.md`). Cadastro mínimo interno sem CRM: índice Redis + durabilidade PG (`schema identity`) + retomada cross-canal + `sessions.customer_id` = nativo no fechamento (conserta `contact_id`-como-`customer_id`, reconecta H1/H2/H3).

**Próximo (recomendado — desbloqueia o valor no demo):**
- **Wiring do intake para escrever `caller.customer_id` NATIVO ✅ (2026-07-03, CHANGELOG).** `agente_portabilidade_intake_v1` chama `customer_resolve` (âncoras `numero_atual`+`contact_identifier`, kind detectado por choice `contains "@"`) e grava `caller.customer_id` via `context_set` **pré-ramificação** (não `context_tags.outputs` — `context_set` é o caminho já provado no runtime nativo do bridge e é a tag exata que `_resolve_close_customer_id` lê). Validado no demo: 2 intakes, mesmo número → mesmo `cus_…` em `sessions.customer_id`. Deploy exigiu `set-next`+`promote` (pool migrado a `PoolSkillSlot`; YAML+restart republica `skill.flow` mas não re-snapshota o `current`).
- **Slice 3** — campos `customer_resumable`/`resume_policy` no step `delegate` (schema `skills.ts` + propagação no engine até o callback `persistDelegate` — **verificar** se o engine repassa campos novos) + `session_resumed` com `resume_origin: same_channel|token|identity`. Ver plano §2 Slice 3 + spec §6/§11.
- **Fase B** — identidade progressiva (anexar âncora nova a cliente existente em match parcial — hoje retorna o existente sem indexar as novas), `external_refs` (CRM id → `external_refs`, não como chave), merge de clientes. Spec §5/§12.
- **Consolidar `caller.customer_id = nativo` no step CRM `resolve`** (`agente_contexto_ia_v1.yaml`): hoje o `buscar_crm` grava `caller.customer_id` com o id do CRM; no modelo novo o nativo é a chave e o CRM vai p/ `external_refs`. Spec §13.8-5 / §3 nota de migração.

**Candidato Fase B/C — gate de validação p/ steps sensíveis + OTP de posse de canal (proposta 2026-07-02, REVISADA 2026-07-03):** liberar sequências **sensíveis** só com validação da identidade/posse que entrou em contato. Duas classes de verificação, decisão consciente:

- **Posse de canal (NOVO — plataforma PODE ser autoridade):** OTP interno (plataforma gera+envia+valida) prova que quem está na conversa **controla o handle agora** → eleva a âncora `phone`/`email` de fraca→verificada. Isto **NÃO** é autoridade de identidade-de-registro; é autoridade de posse de canal (a plataforma é dona dos canais). Gate para ações **não-sensíveis / baixo-médio risco** (retomar carrinho, ver histórico, confirmar dado cadastral) e é o que torna `resume_policy: auto` seguro (vs foot-gun).
- **Identidade-de-registro / credencial / KYC / pagamento (INALTERADO — só retaguarda):** continua **sempre** delegada ao tenant via `identity_verify` MCP; a plataforma relaya e guarda só o veredito. Princípio 7 preservado *neste eixo*.

**Correção de posição:** a proposta original (2026-07-02) proibia OTP próprio da plataforma ("só se emitido pela retaguarda"). Revisão: permitir OTP de **posse de canal** exige **emenda explícita ao princípio 7 e §4.4** — hoje a spec reserva TODA elevação de `confidence`/`verified` ao backend (§ linha 105: "confidence reflete o veredito do backend, não um palpite nosso"). Emenda = separar as duas classes acima; **fazer a emenda antes do código**.

**Não-negociável de modelagem — classe na DADO, não só na prosa:** `confidence` escalar único colapsa semânticas de confiança não-intercambiáveis (0.95-OTP ≠ 0.95-CRM). Adicionar `verification_method`/`verification_class ∈ {channel_otp, backend_identity, none}` ao lado de `verified_at` na `customer_secondary_keys` (colunas já existem: `confidence`, `verified_at`). Consumidores gateiam pela classe certa: `auto`-resume → `channel_otp` recente; ação sensível → `backend_identity`. Veredito escopado a `(customer_id, kind, value_hash)`, nunca ao handle global.

**Precisões:** (a) OTP mata **spoof**, não a **ambiguidade de handle compartilhado** (`matched_by="ambiguous"` ainda precisa de discriminador — pessoa escolhe conta / backend desambigua); não é primitiva de merge. (b) "Nunca guardar o código" tem asterisco: o **desafio** gerado vive efêmero server-side `{t}:otp:{challenge_id}` (hasheado, TTL, uso único, bound a session+customer_id) p/ comparar; a resposta digitada do cliente é `@masked.*` (comparada e descartada); só o veredito persiste. O desafio **não** usa o namespace `@masked.*`. (c) Primitiva = **tools MCP** `otp_challenge`/`otp_verify` via `invoke` (não novo step-type). Composição: `invoke otp_challenge` → `menu masked:true` (coleta código) → `invoke otp_verify(@masked.code)` → `choice` no veredito. (d) **Degradação graciosa** obrigatória (código errado/expirado/max-tentativas → modo baixa-confiança ou escala; nunca hard-block). (e) Entrega pelos adapters de canal existentes; créditos/provedor (SMS/WA template) = integração/custo do tenant; anti-enumeração (só OTP p/ handle que o cliente forneceu no contato que ele iniciou — nunca "esse número tem conta aqui?") + consentimento no envio proativo.

**Fronteira (clarificação 2026-07-03):** OTP é **fator componível / step-up**, nunca o autenticador final. A plataforma provê a primitiva + o veredito-com-classe; **o nível de segurança é definido pelo fluxo do tenant** (regra de negócio, não modelada aqui). Não-sensível: fluxo pode aceitar `channel_otp` só. Sensível/regulado: fluxo **encadeia** OTP (posse) → `identity_verify` retaguarda (identidade-de-registro/KYC) — a plataforma nunca vira autenticador final. `resume_policy: auto` em `channel_otp` é default opt-in do fluxo, não mandato. Requisito que isso impõe: `verification_class` no dado (a primitiva é neutra; a classe dá ao fluxo o poder de compor a barra "posse E/OU identidade").

**Sequência:** o wiring de intake (gargalo) está ✅. OTP é independente do Slice 3 mas complementar — Slice 3 define o campo `resume_policy`, OTP dá a prova que deixa `auto` disparar com segurança. Config no namespace `identity` (tamanho, TTL, máx-tentativas, rate-limit). **Próximo artefato:** mini-spec de `otp_challenge`/`otp_verify` (contrato das tools, chaves Redis, config, fluxo anti-enumeração, emenda ao princípio 7/§4.4) — criticar antes de codar. Ver spec §4.4 (dois momentos), §5, §6/§8 (gate no delegate), princípio 7.

**Dívida colateral ✅ (2026-07-08):** os 2 testes pré-existentes de `test_webhook_bridge.py` (drift anterior, sem
relação com identidade) foram corrigidos — `test_resume_publishes_agent_ready_and_agent_done` usa `AsyncMock` no
`producer.send` (awaitable p/ o `create_task`); `test_process_inbound_does_not_call_resume_handler_for_customer_msg`
deixa o `process_inbound` correr contra o `mock_redis` (a função `forward_inbound_to_active_agent` não existe mais),
com `get`/`hgetall` configurados p/ pular o retry-loop e não vazar coroutine. 17/17 verdes. Ver `CHANGELOG.md`.

---

## OTP produção + primitivo de diálogo genérico (survey + OTP) — resíduos *(ADR ainda Proposto; primitivo v1 + Fatias 1/2 ✅, ver CHANGELOG)*

OTP Fase B é um **MVP tool-based** (identidade progressiva + `verification_class` + `OtpService` + gate `possessed`);
o dialog-primitive v1 (`dialog-api`, `skill_dialog_runner_v1`, `form_get`, editor `/config/dialog-forms`) está entregue
e adotado por OTP, NPS e survey multi-pergunta. ADRs: `docs/adr/adr-otp-workflow-and-dialog-primitive.md` (**Proposto**),
`docs/adr/adr-identity-channel-possession.md`; spec: `docs/product/dialog-primitive-and-runner-design.md`.
**Inegociável (invariante):** o código do OTP nunca passa pela mão de um agente — gerar/enviar/verificar ficam no `OtpService`/channel-gateway.

**OTP — produção (ADR não implementado)**
- **D1 — OTP como workflow negocial + especialista de canal** (`delegate-workflow-io`, Arc 19) segue **só desenhado**: workflow channel-abstract exposto como step-up reusável (`{verified}`) + especialista Tier-3 dono do canal. Hoje é tool-based no intake. Item 6 (OTP como step-up genérico) depende disto.
- **Item 1 — entrega real** (SMS/e-mail, envio por canal ≠ sessão = posse forte) **adiado até termos canais**; vira o `collect` do especialista.
- **Trilha B / D3 — tela de OTP em Configurations**: tuning numérico (TTL, tentativas, rate-limit, canais de posse) é **env-only**; falta namespace `identity`/`otp` no config-api + bindings (`form_id` dos prompts, `template_id` de entrega).
- **Trilha C — segurança**: auditoria de challenge/verify (Kafka/`mcp.audit`, item 5); **lockout crescente** (item 7); **testes de unidade** do adapter/endpoints (item 8).
- **Trilha A** — textos/i18n dos prompts de OTP (item 3) *(verificar: o retry na mesma superfície já saiu em 2026-07-07)*.
- **D2** — atualizar o spec de survey (§17/§19) para consumir o primitivo de diálogo *(verificar se já feito)*.

**Limitações declaradas do primitivo (aceitas, sem fix)**
- **Hooks de fim-de-contato não podem delegar** — `suspend` = hook concluído → o contato fecha antes de renderizar. Por isso o NPS ativo (`agente_nps_v1`, `on_contact_end`) roda **inline** (form_get + menu dinâmico), não via runner. Runner só serve chamadores que podem suspender.
- **Delegate de nível único** — aninhar o runner dentro do collector colide em `session.delegate_resume_token` (rejeitado).
- **`channel_policy: elect` adiado (decisão C, 2026-07-08)** — eleição de canal hoje é uma `question` do form lida pelo workflow; o `elect` de 1ª classe conflita com a segregação de perfil (reach/`collect` é exclusivo de `workflow`, runner é `agent`). Reabrir quando houver fluxo que exija o runner **ele mesmo** re-despachar cross-canal (aí decidir A escopado vs B pleno).
- **Binding do form no runner é contexto de delegate** (`@ctx.session.dialog_form_id`), não `$.config` — o hook `$.config` existe, mas a migração para deploy-por-slot só foi feita no `skill_survey_multi_v1` *(verificar se o runner/OTP ainda dependem do ctx)*.

**Config params por deploy**
- Skill parametrizado **exige deploy por slot** com `config_json.form_id` (`set-next` + `promote`); sem isso o `form_get` falha em runtime.
- **Typo de `source` não é tratado no deploy** — o lint no publish (`configParamSourceWarnings`, agent-registry) é apenas **avisador, não-bloqueante**.
- Worker legado `skill-flow-worker` fora de escopo (Arc 19 o deprecou).

**Editor de dialog-forms `/config/dialog-forms` — 2ª passada**
- Reordenar nós por **drag** (hoje setas ↑↓); **edição de locale lado-a-lado** + progresso de tradução estável; **preview** do que o cliente vê.
- **Auth no write** — hoje **aberto**, sem gate ABAC `config.*`.
- Validação client-side com mensagens (form_id slug, `output_key` único, `dimension_id` snake_case); confirmação ao descartar rascunho (dirty/blocker); `interaction=form` com múltiplos `fields`.

**Survey / scoring**
- `survey_question` **reutilizável** — fora do 1º corte, ainda pendente.
- **Entrega do link web**: falta só o **operacional** (tenant apontar `survey.link_delivery.webhook.url` pro gateway SMS/e-mail dele + `PLUGHUB_SURVEY_LINK_WEBHOOK_TOKEN`); `SmtpProvider` nativo é opção futura; **UI dedicada** para `link_delivery` é follow-up (hoje só config genérica). §9.2/§19 de customer-surveys.

**Guard de teardown-hook (Tarefa #17) — endurecer**
- O guard atual (`_validate_teardown_hooks`/`_load_skill_steps` no `registry_syncer.py`) é **read-only, fail-open**: só loga ERROR. O desenho pede **rejeitar no deploy/sync** (agent-registry/RegistrySyncer) quando o flow de um skill deployado em pool-alvo de `PoolHooks.on_contact_end/on_human_end/post_human` contiver step que suspende — reusando a varredura do `_computeFlowModel` **estendida com `delegate`** (hoje `_computeFlowModel` só olha `suspend`/`collect`). Alternativa descartada por ser menos robusta: flag declarado `classification.execution_context`.

---

## Flow — step de expressão sandboxed (NÃO eval cru) *(decisão de design, 2026-06-28)*

**Necessidade**: valores computados / lógica mais rica em flows (ex.: o loop p/ ler o form JSON de pesquisa de
satisfação; condições derivadas além de JSONPath em `choice`). **Ideia descartada**: um step que roda
**JavaScript livre (`eval`)** com acesso ao ContextStore — quebra invariantes (Redis só via routing/skill-flow,
MCP audit, masking/LGPD, isolamento de tenant) e abre RCE/exfiltração/loop infinito.

**Recomendado**: **step de expressão sandboxed, read-only**:
- avaliador de expressão **restrito** (estilo CEL/jsonlogic), **puro e determinístico**, **sem I/O nem rede**,
  com limite de CPU/tempo; lê `@ctx.*` (respeitando escopo/visibility), **não** escreve direto no Redis;
- saída tipada gravada via os mecanismos já existentes (`context_tags`/output), nunca acesso bruto ao store;
- cobre a maioria dos "flows complexos" sem o buraco de segurança do eval.
- **Casos específicos já têm caminho seguro**: pesquisa de satisfação → form JSON interpreter + menu dinâmico
  (decisão B do ADR de surveys); lógica que não cabe em expressão → step `reason` (AI Gateway + `output_schema`).
- **Código de verdade** (Turing-completo) só no **SDK/agente nativo** (runtime controlado, já auditado), nunca
  como step de flow.

Invariante a preservar: nenhum step de flow executa código arbitrário do tenant com acesso ao runtime interno.
*(discussão; sem implementação)*

---

## Agent Principal — identidade de máquina p/ agentes IA *(spec, 2026-06-28)*

Identidade de máquina (`subject_type:"agent"`) p/ agentes nativos e externos se autenticarem, distinta das
roles humanas; capability vem do `agent_type` (registry), auth-api só emite/rota credencial; audit por
`principal_id`. Nativo = auto-provisionado, **sem UI**; externo = cadastro + secret (API/CLI; UI enxuta na F3).
Fases F1–F4. **Spec**: `docs/product/agent-principal-identity-spec.md`. *(discussão; não implementado)*

---

## Dashboards — cobertura de catálogo *(spec, 2026-06-28)*

O sistema composável (estilo Grafana) **já existe** (Dashboard #35/Arc 16: DisplayTool registry, grid,
Add Card 3-passos, runtime filters, `/reports/display/*`). Fases (spec): **F1 cobertura** — expor no
`ENDPOINT_CATALOG` os relatórios ausentes (segmentos/complexidade, disponibilidade, Fila/SLA, Pools/Infra,
qualidade/calibração, surveys, performance diária) via o contrato existente; **F2 consumo no Home** — `HomePage`
renderiza o dashboard do usuário (destravar p/ todas as roles; builder segue em Config/admin); **F3 allowlist +
starter por role** (`role_catalog:{role}` no Config API: admin define componentes liberados + layout starter;
reconcile no load); **F4 picker do usuário** (escolhe/arruma dentro da allowlist; layout pessoal já existe).
Escopo de dados sempre via ABAC/`accessible_pools`/`supervised_*` no endpoint. **Decisão: NÃO** construir
datasource/query-builder genérico (dado interno); novos tools (heatmap/gauge/leaderboard) só sob demanda.
**Spec**: `docs/product/dashboard-catalog-coverage-spec.md`. *(discussão; não implementado)*

---

## Isolamento do substrato por `origin` — Fase 2 (adiada) *(arco completo 2026-06-25; histórico no CHANGELOG)*

**Fase 2 — ADIADA por decisão (2026-06-25), não enterrada.** Conteúdo: partição CH
`PARTITION BY (toYYYYMM(date), origin)` em tabelas novas/migração versionada (lifecycle/LGPD; **não**
in-place — CH não altera partition key in-place); campo `pool.origin_class: production|import|review`
(default production), **ortogonal a `agent_kind`**, como atalho/validador p/ pools dedicados + eixo de
agrupamento na UI.

**Por que adiar:** a fase 2 é **governança/lifecycle, não correção**. A separação dos dados (o problema
real) já está garantida pelo **filtro de leitura default `live`** (passo 4) + sampling (passo 5); a partição
não muda nada disso. Hoje não há importação externa real e a reavaliação é de volume mínimo → custo/benefício
não fecha.

**Gatilho que reativa (vira necessária, não opcional):** entrada de **importação externa real com obrigação
de retenção/erasure própria** (LGPD — dado de terceiro com prazo distinto, ou direito ao esquecimento que
precise expurgar **só** o `import`/`reeval`). Nesse cenário o filtro de leitura não basta: precisa da
separação **física** para `DROP PARTITION` barato/limpo (a alternativa, `ALTER … DELETE`/mutation, é pesada
e não-particionada). Enquanto esse requisito não existir, fica como backlog.

---

## G-PROBE — Auth ABAC/serviço nos endpoints do Quality (evaluation-api)

**Fase 1 ✅ (config humana, 2026-06-25):** mutações de forms/campaigns/rubric gateadas por
`_require_evaluation_field` (grant-first, deny em config vazio; forms/campaigns→`formularios`,
rubric→`gerir_rubrica`, read_write). Route guard `RequireEvalAccess` em todas as rotas de evaluation
(espelha o nav strict, sem bypass). Bearer JWT (de `session.accessToken`) nas mutações + hooks de lista
no platform-ui. Detalhe em `CHANGELOG.md`.

**Listas abertas (decisão fase 1):** `list_forms/campaigns/rubric` ficaram **sem gate** — são read
compartilhado (Avaliações/Calibração/Curadoria/Reports mapeiam id→nome com `report`/`revisar`/`curar`,
não `formularios`; gateá-las quebraria essas telas). GET-by-id/resolve/effective também abertos
(runtime: session-replayer lê `forms/{id}`, mcp-server lê `rubric-templates/effective`).

**Fase 2 — slice backend ✅ (2026-06-26); wiring + UI PENDENTES.** Decisões da sessão: gate de serviço
**strict** (sem fallback admin-token); UI usa **Bearer+ABAC** (sem segredo no frontend); slice backend-first.

- ✅ **`_require_service`** (strict `X-Service-Token`, `config.service_token` env
  `PLUGHUB_EVALUATION_SERVICE_TOKEN`, vazio = no-op/demo) em: `ingest`, `claim_instance`,
  `expire/skip/mark-error`, `dispatch_scan`, `submit_pre_review`, `submit_ai_review`,
  `publish_calibration_note`.
- ✅ **`_require_service_or_eval_write`** (serviço OU Bearer+ABAC `formularios:rw`) nas ações de ops
  disparáveis pela UI: `dispatch_campaign`, `backfill`, `seed/flush-synthetic`, `sampling-rules` CUD.
- ✅ **`_require_any_evaluation`** (any-of, degradação graciosa) nas LEITURAS de lista: forms, campaigns,
  rubric-templates, instances, contestations, calibration-notes, sampling-rules.
- ✅ Testes `tests/test_gprobe_phase2.py` (funções puras). Ver CHANGELOG.

**Slice caller-wiring ✅ (2026-06-26):**
- ✅ **Provisionado** `PLUGHUB_EVALUATION_SERVICE_TOKEN` no `docker-compose.demo.yml` (evaluation-api +
  mcp-server-plughub; valor demo `changeme_eval_service_token_demo`). Gates de serviço agora ENFORCED no demo.
- ✅ **mcp-server** `evaluation_pre_review_submit` envia `X-Service-Token` (env; `EVALUATION_API_URL` também
  provisionado p/ o container). Único caller HTTP backend de endpoint service-gated (o avaliador real publica
  por Kafka, não por HTTP `/ingest`; os scanners chamam a função direto, não o endpoint).
- ✅ **UI bridge**: `seed/flush/dispatch` da `CampaignsPage` passam o Bearer do operador (`session.accessToken`)
  → `_require_service_or_eval_write` aceita via ABAC. Input de admin-token vira vestigial (remoção = cleanup UI).
- ✅ **Smoke** `infra/test/smoke_gprobe_service_auth.sh` valida os 3 gates (service strict / dual / any-of).

**Follow-ups restantes:**
- ⏳ **Repair dos ~15 e2e legados de eval** (`test_t7a/t9*/t10*/t12/t13/t14/t15/t17/r1/r6/t7b2`): **já vermelhos
  pela Fase 1** (criam form/campanha SEM Bearer; `create_form/create_campaign` exigem `formularios:rw`) —
  precisam de (a) Bearer mintado p/ o setup E (b) `X-Service-Token` nos calls G-PROBE-gated (ingest/dispatch/
  scan/backfill/ai-review/skip/mark-error/sampling-rules). Dívida pré-existente da Fase 1; smoke dedicado cobre
  o G-PROBE no intervalo.
- ✅ **Cleanup UI** (2026-06-26): input de admin-token removido da `CampaignsPage` (state/input/props +
  i18n `campaigns.sidebar.adminTokenPlaceholder` en/pt); `saveCurationSamplingRules`/`useCurationSamplingRules`
  passam o Bearer do operador. Bearer explícito nos consumidores de lista que faltavam (`useInstances`,
  `useContestations`, `useCurationSamplingRules`); forms/campaigns/rubric/results/curations já tinham. Ver CHANGELOG.

**Pendente — admin-token boxes platform-wide → Bearer+ABAC (FORA do escopo G-PROBE, não bloqueia):**
G-PROBE cobriu só o módulo Quality (evaluation-api). O MESMO anti-padrão (caixa de texto de admin-token na UI,
em vez de autorizar pelo JWT do operador + ABAC) persiste em outras telas, cada uma gateando um serviço
diferente pelo seu admin-token. Migrar cada uma é um "mini-G-PROBE" por serviço (gatear endpoints em
Bearer+ABAC + remover a caixa). Inventário:
- ✅ **`config/access` (`AccessPage`) + `config/groups` (`GroupsPage`) → auth-api** (`config.usuarios`) — slice
  CONCLUÍDO (2026-06-26): gate strict Bearer+ABAC na auth-api (router + groups_router), seed_auth minta Bearer
  de bootstrap, UI usa session Bearer (listas carregam no login — conserta o bug reportado). Smoke
  `smoke_config_usuarios_auth.sh`. Ver CHANGELOG. *(Follow-up: `auth-api/tests/test_router.py` em X-Admin-Token
  → refresh; envs `*_AUTH_ADMIN_TOKEN` vestigiais → cleanup.)*
- ✅ `config/platform` (`ConfigPlataformaPage`) + `config/masking` (`MaskingPage`) → **config-api** — slice
  CONCLUÍDO (2026-06-26): gate DUAL (admin-token OU Bearer+ABAC mapeado por namespace; default→`plataforma`,
  masking/audit_policy→`masking`); `putConfig/deleteConfig` com Bearer opcional; caixas removidas das 2 telas.
  Smoke `smoke_config_write_auth.sh`. Demais telas de config (Channels/Billing/Dashboards) seguem em admin-token
  (dual cobre) até suas fatias. Ver CHANGELOG.
- ✅ `config/resources → Skills` (`SkillsPage`, `competencySkills`) → **config-api** (NÃO era agent-registry —
  escreve namespace `competency_skills` via `putConfig`, mapeia ao default `config.plataforma`). Slice UI-only
  CONCLUÍDA (2026-06-26): caixa removida, escritas via Bearer; backend já coberto pelo gate dual da config-api.
- ✅ **agent-registry — gate dual nas mutações de config** (2026-06-26): middleware `requireResourceWrite`
  (Express, verificação HS256 em stdlib `crypto`) nos routers **pools/skills/channels/channel-endpoints** —
  GET aberto; mutação exige **X-Service-Token** (callers internos) OU **Bearer+ABAC `config.resources`** (UI).
  Callers internos wirados: RegistrySyncer (`registry_syncer.py`) + `skill_deploy` (`deploy.ts`) mandam
  `x-service-token`. UI: `registry.ts` manda Bearer via novo `auth/token-store.ts` (holder de módulo espelhado
  pelo AuthContext) → caixa da `SkillsPage` removida. Provisionado `PLUGHUB_JWT_SECRET` +
  `AGENT_REGISTRY_SERVICE_TOKEN` (agent-registry + orchestrator-bridge + mcp-server). Smoke
  `smoke_agent_registry_write_auth.sh`. Ver CHANGELOG.
  - **Residual (fora desta fatia, FORA do gate de propósito):** `pool-slots` (promote/rollback do Fluxo→Deploy,
    cadeia via mcp-server), `instances`/`operational` (runtime: bootstrap/heartbeat). Gatear esses = fatia
    própria (wirar a cadeia de deploy + bootstrap). Ferramentas CLI de import (`sdk/cli/import.ts`,
    `gitagent/import.ts`) mutam `/v1/skills` sem token — dev/CI, não-runtime; passar `x-service-token` se forem
    usadas contra registry gateado.
- ✅ `config/channels` (`WebChatConfigPage` + `WebhookConfigPage`) → **config-api** `config.canais` — slice
  CONCLUÍDO (2026-06-26): backend já dual; add `webhook`→`canais` no mapa; caixas removidas, escritas via Bearer.
  Smoke estendido (§4). Ver CHANGELOG.
- ✅ `config/billing` (`BillingPage`) → **pricing-api** (NÃO era config-api — usa `/v1/pricing/*`) — slice
  CONCLUÍDO (2026-06-26): gate DUAL na pricing-api (admin-token OU Bearer+ABAC **`config.plataforma`** — decisão:
  reusa config.plataforma, sem campo billing novo; o módulo `billing` só tem `visualizar`/read). `jwt_secret` +
  `PLUGHUB_PRICING_JWT_SECRET`. Caixa removida; reserve activate/deactivate via Bearer. Smoke
  `smoke_pricing_write_auth.sh`. Ver CHANGELOG.
- ✅ `config/dashboards` (`DashboardsPage`) → **config-api** namespace `dashboards` (→ default `config.plataforma`)
  — slice UI-only CONCLUÍDA (2026-06-26): `dashboard-hooks` (configGet/Put/Delete/List) mandam Bearer via
  token-store; caixa de admin-token (+ localStorage `plughub_admin_token`) removida. Backend já coberto pelo gate
  dual da config-api. Ver CHANGELOG.
- ✅ `evaluation/knowledge` (`KnowledgePage`) — **fatia de wiring CONCLUÍDA (2026-06-26)**. Recon confirmou que a
  página estava **morta**: `/v1/knowledge/*` não existia em lugar nenhum (proxy ia p/ eval-api:3400 sem rotas;
  mcp-server-knowledge só tinha `/admin/*` + MCP tools). Construído o **surface REST** na mcp-server-knowledge
  (`routes/knowledge.ts`: GET `/v1/knowledge/search`, POST/DELETE `/v1/knowledge/snippets`, reusando `db.ts`),
  gate DUAL (`require-knowledge-access.ts`: X-Service-Token OU Bearer+ABAC `evaluation.gerir_rubrica`, read p/
  search / read_write p/ snippets). Proxy Vite `^/v1/knowledge` → **3401**. Publish de CalibrationNote da
  evaluation-api passa `X-Service-Token` (conserta o KB vetorial do Arc 13, que silenciava em 404). UI usa Bearer
  (token-store) e perde a caixa. Smoke `smoke_knowledge_rest_auth.sh`. Ver CHANGELOG.
- ✅ `Avaliações` filters (`AvaliacoesPage`) — caixa de admin-token removida (2026-06-26); a adjudicação Arc6
  **legada** usa o Bearer do operador (`adjudicateContestation` → `bearerHeaders`). *Resíduo:* a **retirada
  física** do endpoint/UI `adjudicate` segue junto da limpeza do motor Arc6 legado (não bloqueia).
Decisão (2026-06-26): sequenciável por serviço; auth-api foi a 1ª fatia (strict, decisão da sessão). Inventário
completo das telas com caixa de admin-token: access, groups (✅ auth-api), platform, masking (config-api),
resources/skills (agent-registry), knowledge (mcp-server-knowledge), avaliações/adjudicate (evaluation-api legado).

**Rot pré-existente (separado do G-PROBE, não bloqueia):** `evaluation-api/tests/test_router.py` tem
11 testes quebrados **independentes do gate** (classes TestInstances/Ingest/Results/Contestations):
mocks não cobrem `set_contestation_state`/`get_campaign`/`lock_result` (chamadas novas Arc 13),
`app.state.redis` ausente no app de teste, payload de review desatualizado (422), `expire_instance`
sem `x-admin-token` (container tem `admin_token` setado). Atualizar os mocks ao contrato evoluído.

---

## Webhook pools — throttle de downstream: enforcement no routing *(deferred)*

Re-validação 2026-06-04 (ver `CHANGELOG.md`): o default 500 **já não existia** no código
(schema `.optional()`, registry grava null); a premissa "nada é pré-instanciado" ficou
stale pós Arc 19 Fase C — capacidade real de webhook = slots de instância do deploy
(Bootstrap) + admissão híbrida. O `max_concurrent_sessions` pool-level era display-only
no Monitor (capacidade fictícia) — coerência aplicada: removido do YAML demo, comments
schema/registry revisados ("throttle opcional de downstream").

**Deferred**: enforcement real do throttle no routing quando configurado
(`active_count ≥ max` → enfileira; backpressure p/ downstream frágil, ex. ERP).
Implementar quando houver caso de uso real.

---

## Delegate v2 — itens restantes (pós-correção do ciclo de portabilidade)

Modelo corrigido e backend verde em [`docs/arcos/delegate-workflow-io.md`](docs/arcos/delegate-workflow-io.md)
(delegate sempre roda o alvo como segmento conference do chamador; A-new fecha como webchat;
`context_set` registrado; specialist de B adia instantâneo). Restam:

- **Fase C — heurística de canal na UI ✅** (já implementada — TODO estava
  desatualizado): `ListaTab.tsx` classifica pelo `channel_type` real (canal decide
  WorkflowTraceList vs SegmentList) e o badge "suspended" é restrito a `channel ===
  'webhook'` (webchat em delegate-wait lê live). Nota residual no código: contador
  de participantes vivos exigiria suporte de backend — channel é o proxy aceito.
- **Fase D — timeout scanner do delegate ✅** (já implementado — TODO estava
  desatualizado; ver `delegate-workflow-io.md` § Fase D): `run_timeout_scanner` em
  `channel-gateway/adapters/webhook.py` (lifespan, 60s) expira `resume_tokens`
  vencidos via `handle_resume(decision="timeout")` → `on_timeout` do step; cobre
  suspend e delegate; `pending_workflow` stale auto-limpa no próximo reconnect.
- **Fase E — Workflow Execution Trace (step-level)** ✅ (E.1/E.2/E.3 + transcript):
  step timeline já renderiza; `step_io` com `decision`/`payload`/`child_session_id` por step
  (E.1); `resumed_by` por step (E.3); duration webhook = tempo decorrido total (E.2);
  transcript do specialist via clique no nó de agente (já existia). Design em
  `docs/arcos/delegate-workflow-io.md` § Fase E.
  - **E.4 diferido (sem dado no demo)**: (a) **MCP audit** por step — `skill-flow-service`
    chama o mcp-server via cliente cru, não pelo `McpInterceptor`, então os `invoke` não
    geram `mcp.audit`; construir quando a execução passar pelo interceptor. (b)
    **agent_business_events** (Arc 12, via tool `agent_event`) — agentes de portabilidade
    não emitem. *(Não confundir com a tabela `agent_events`, descontinuada em 2026-07-28 —
    eram nomes quase idênticos para eixos diferentes.)* (c) snapshot de
    ContextStore com evolução entre suspends (hoje só o estado atual no strip Input context).
    (d) duration "corridas vs úteis" (business_hours) lado a lado.

## Relatórios analíticos — Agentes e Pools *(só o que resta aberto; histórico no CHANGELOG)*

Arco de relatórios (agentes + pools/infra) e Bancada de comparação 360° por `agent_key`. Specs:
[`analytics-reports-redesign.md`](docs/arcos/analytics-reports-redesign.md) · [`pools-infra-report.md`](docs/arcos/pools-infra-report.md) ·
[`analytics-agents-workbench.md`](docs/arcos/analytics-agents-workbench.md) · [`config-consolidation.md`](docs/arcos/config-consolidation.md) ·
[`config-http-propagation.md`](docs/arcos/config-http-propagation.md).

### Dívidas e limitações declaradas

- **`sessions.sla_target_ms` histórico**: sessões antigas permanecem NULL (valor nunca persistido,
  irrecuperável); a aba SLA só popula com contatos novos.
- **`AgentTimeline` — precisão por pool é aproximada**: atribui o intervalo inteiro a cada pool
  tocado; sub-intervalos exatos por pool = refinamento futuro.
- **`farewell_text` só renderiza no webchat**: voice/whatsapp não renderizam (voice = TTS futuro).
- **Quality ainda em fixture (F8 ⏸ adiado)**: `evaluation_dimension_scores` vem de seed de
  `evaluation_results`; `agente_avaliacao_v1` não roda no demo (test-grade, sem associação
  form/campanha). Pendências test-grade da F2: ReplayContext sem `session_meta` e sem associação
  campanha/form. Consertar o pipeline de avaliação = arco próprio.
- **`pool:pending_assignment:{poolId}` é UMA chave por pool** (last-write wins) → chave
  por-instância é melhoria futura (liga à fila pull/inbox).
- **NPS render (cosmético, diferido)**: a mensagem de `menu`/`notify` aparece no transcript como
  "structured content" em vez de texto puro (o dado do NPS grava normalmente) — revisar emit + render.
- **Cenários sem teste** (queue-attended-model): "fila muda" e "drop sem `pool_id`".
- **(verificar)** "Fase 1 — relatório de agentes" nunca foi marcada ✅ (parece absorvida por
  C1/C1b-A/C1b-B + Bancada); idem "Fase 3 · 3d-**parcial**" do provisionamento — conferir o que ficou fora.

### Trabalho futuro planejado

- **F11 — pesquisa multi-grão / surveys diferidas** (arco de evaluation, separado do G7): falta o
  **planejamento da orquestração** — quando/como cada grão (`journey | session | segment`, até 3 por
  fluxo) dispara, e surveys diferidas (`captured_at ≠ session_at`). Base parcial na F10.2b
  (`survey_collector_ia` / `survey_reconnect_ia`). Ver workbench §13/§14 e
  `g7-segment-contact-decoupling.md` §5.
  - **F11.2 (validação)** diferida: simular via curl/seed (publicar `session.signals`/`survey_record`
    com origem de `opened_at` anterior + grão `journey` e conferir `session_at = opened_at`);
    workflow agendado real (dias depois) fica futuro.
- **Catálogo canônico de dimensões de qualidade** (arco próprio): única base rigorosa p/ comparar
  dimensões entre forms. Hoje cross-agente exige mesmo form e cross-form só vale p/ um agente
  (`_compare_quality_lens` expõe `summary.form_ids`; a UI faz o guard).
- **Avaliador dirigido por calendário/campanha** (arco próprio, decisão 2026-06-07): disparar pelo
  `schedule` (JSONB de `evaluation.campaigns`) passando o `session_id`, substituindo o gatilho
  incondicional do Persister.
- **Residuais opcionais do relatório de Pools/Infra** (spec § Pendente): sub-aba Visão geral,
  heatmap hora×dia, SETs de `session_id`, overlay de capacidade licenciada v2.

### Config Consolidation / HTTP Propagation — o que falta

- [ ] **F2** migração por domínio: faltam **hooks**, **evaluation/pricing** e **defaults hardcoded**
      (pools, TTLs, masking e ABAC/users ✅).
  - [ ] **Item 6** — seeds `seed_evaluation`/`seed_pricing` → bootstrap idempotente via API.
        **Estacionado (2026-06-12)**: atacar junto da revisão dos módulos evaluation/pricing.
- [ ] **F3** bootstrap idempotente único (substitui `infra/seed/*.py` + YAML-fonte, só via APIs).
      Arquitetural, sem bug vivo, baixa urgência (`config-consolidation.md` §9).
- [ ] **F4** política de env vars (segurança) — inventário final.
- *Cleanup opcional*: remover o caminho dormente `evaluation_sampler`/`on_pool_config` do
  rules-engine (`on_pool_config` nunca é chamado) — ou religá-lo se a campanha não cobrir.
- *Dead code a varrer*: `_sync_agent_type`/`_prune_agent_types` (`registry_syncer.py`, sem chamador);
  Path A `elif framework == "human"` (main.py, inalcançável); `AgentTypeSchema` (@plughub/schemas) +
  `validators/agent-type.ts` órfão. Testes do agent-registry com agent_type foram deletados — revisar
  a suíte se reativar CI.

---

## G7 — Decoupling segment-end × contact-close *(fases entregues; restam follow-ups + 2 arcos próprios)*

Spec em [`g7-segment-contact-decoupling.md`](docs/arcos/g7-segment-contact-decoupling.md) (§10/§11) +
`conference-mechanics.md`. Fases 0/3, Slices A/B, sub-arco multi-humano (Slices 1/2′/3/4′), arco do
router (alocação atômica) e Camada 3 estão entregues e validados E2E — histórico no CHANGELOG. Resta:

### Follow-ups do modelo de hooks *(baixa prioridade)*

- **Gap (2) — survey customer-side por-segmento não chega aos peers**: `segment_wrapup` reusa a lista
  de `on_human_end` mas filtra `side=agent` (`main.py` ~938) → surveys customer-side (grão=segment,
  NPS) só saem na âncora/primário.
- **Gap (4) — binding grão↔boundary é convenção, não contrato** (skill em "contact ends" gravar
  `grain=session`); disparo com **grão=journey** não está plumbado (não há boundary de fim-de-journey) → F11.
- **Higiene opcional**: convergir `on_human_end` (último) + `segment_wrapup` (peers) num mecanismo
  único de wrap-up por-segmento.
- **Polish (Slice 3)**: atribuição-por-nome do remetente no fan-out humano↔humano.
- **UX cosmético**: sinalizar no Console "convidando, aguardando login do agente" quando o `@mention`
  vai p/ pool sem instância `ready` (não é bug — fila + drain no `agent_ready`, conclusão 2026-06-15).

### Router — alocação atômica *(arco concluído; só residuais opcionais)*

- `get_ready_instances`/snapshots poderiam ler `SCARD` direto (hoje leem o JSON sincronizado pelo
  claim/release — funciona como hint; o claim é o gate atômico). Baixa prioridade.
- Cenário "2 contatos simultâneos no mesmo pool → spread" não exercitado isoladamente.
- Hardening da chave de menu por `segmentId` julgado **desnecessário** após a alocação atômica +
  Camada 3 Fatia A — reabrir só se houver regressão.

### Unificação de contabilidade de agente (kind-agnostic) *(arco próprio — DIFERIDO)*

Anchor "último agente customer-facing" é aproximado por 4 chaves de papéis distintos: `human_agent`
(flag, ~10 sites, hot path de entrega) · `human_agents` (SET, ~10: remaining/restore/participant_left/
fan-out) · `ai_agents` (SET, ~8: restore no close) · `active_ai_specialists` (SET, ~7: defer G2).
Alvo: HASH único `session:{id}:agents → {kind, role, customer_facing, running}`.
- **Decisão (2026-06-13, reafirmada 2026-06-15)**: fazer **oportunisticamente** — só quando um bug
  concreto justificar ou encostado em feature que já toque essas chaves. Refactor puro-interno,
  gateável só por paridade, raio cross-package (mcp-server supervisor/bpm/evaluation), no path mais
  frágil (close).
- Único incremento baixo-risco se encostar no path de entrega: derivar `human_agent` de
  `SCARD(human_agents) > 0` — atenção à aresta (flag setada mesmo com `instance_id` vazio em
  `activate_human_agent`; não é 1:1).

### Detecção de queda involuntária de humano *(Slices 1/2 ✅ — verificar se o alvo está coberto)*

- **(verificar)** Slices 1 (ws.close + grace → `contact_closed(agent_disconnect)`; re-rota ao
  `_ha_pool` quando `remaining<=0`) e 2 (pong-tracking `ws.ping` + `terminate` em 30s) estão ✅ e o
  texto declara "arco heartbeat completo", mas o fechamento do sub-arco multi-humano ainda listava
  este arco como restante — conferir o alvo "posse re-estabelecida por alocação" no caso `remaining>0`.

---

## Frente 3 — Revisão de config / eliminar seeds *(em curso)*

Meta: produção sem seeds re-aplicados — DB é fonte de verdade; setup inicial de DB versionado.
- **Fase 1 ✅ (2026-06-15)** — **seed-if-absent / DB-owned** no `RegistrySyncer` (`registry_syncer.py`): no 409,
  não sobrescreve pool config nem deploy-slot (capacidade); edições de UI sobrevivem a rebuild. Env
  `REGISTRY_SYNC_RECONCILE=true` = reconcile legado (YAML vence) p/ dev. Skills seguem upsert (código). Curou o
  sintoma "Transfer/`escalation_pools` some a cada build". Ver CHANGELOG 2026-06-15 + CLAUDE.md § Configuration.
- **Fase 2 — correção ✅ / arquitetura DIFERIDA (auditoria 2026-06-15)**: a auditoria por store mostrou que
  **todos já são seed-if-absent** (pools via Fase 1; config-api `overwrite=False`; pricing/evaluation checam
  existência; users 409; catálogo ABAC e skills re-aplicam de propósito = código). Ou seja, **não há bug
  pendente** — a "config some no rebuild" está resolvida. O que sobra é só o **sonho arquitetural** (converter
  seeds/YAML em **migração versionada if-absent**, modelo `initdb/01_platform_config.sql`, aposentando
  `infra/seed/*.py` + YAML de registry, store por store) — **baixa urgência**, burn-down gradual sem retrabalho.
  Resíduo opcional: `set_module_config` do `seed_auth` if-absent (demo-users). Ver `docs/arcos/config-
  consolidation.md` §9.
- **Doc** ✅ — `docs/arcos/config-consolidation.md` existe; atualizado com a auditoria + precedência seed-if-
  absent (§9). Referências de `CLAUDE.md`/`registry_syncer.py` resolvem.

---

## Hardening de Auth — postura de sessão do Console *(proposta — não é bug)*

Hoje (Arc 7, por design): `access_token` em memória; `refresh_token` em `localStorage('plughub_refresh_token')`
→ **silent re-auth** no mount (`POST /auth/refresh`). Reabrir a URL após fechar a aba entra logado sem
credencial — esperado, mas é um trade-off UX×segurança. Levers de endurecimento (cada um é arco próprio,
escolher conforme exigência de segurança para um console que vê PII):
- **refresh_token em cookie httpOnly** (em vez de `localStorage`) → mitiga exfiltração por XSS. Maior
  mudança (auth-api seta cookie; CORS/SameSite; CSRF token).
- **Idle/inactivity timeout** — não existe hoje; sessão dura enquanto o refresh_token for válido. Adicionar
  expiração por inatividade no Console + invalidação no auth-api.
- **TTL do refresh_token** — encurtar no auth-api (hoje rotaciona indefinidamente enquanto usado).
- **"Fechar aba = deslogar"** — trocar `localStorage` por `sessionStorage` (morre com a aba); custo de
  conforto (reloga a cada nova aba).
Decisão de produto/segurança pendente: qual combinação aplicar. Sem isso, manter o comportamento atual.

---

## Customer Surveys — estado as-built das fases S1–S11 *(levantamento 2026-07-23)*

> Cruzamento do plano §12 de [`docs/arcos/customer-surveys.md`](docs/arcos/customer-surveys.md) contra o
> **código real** (o F11 abaixo dizia "nenhuma fase iniciada" em 2026-07-02 — **desatualizado**). Tabela
> as-built + evidências + próximos passos completos em **`customer-surveys.md` §12.1**. Achado central:
> várias fases estão **feitas-por-substituição** (dialog-api, `contact_eligibility_check`, `session_signal`
> genéricos cobrem o que o spec pedia como entidades dedicadas de survey).

**Feito / feito-por-substituição (não é trabalho pendente):** S2 (runner genérico + DialogForm), S3 (gatilho
lê outcome), S4 (quarentena → `contact_eligibility_check` genérico), S5 (web + link → `session.signals`).

**Pendente — eixo "fechar parciais primeiro" (decidido 2026-07-23):**

1. **S1 — ✅ FEITO (2026-07-27, ver CHANGELOG).** Catálogo único `survey_catalog.py` + roll-up por instrumento.
   **Resíduos:**
   - **Nenhum produtor emite CES/PMF/FCR** — ✅ **reconfirmado 2026-08-03**: os seeds só cobrem `nps`
     (`seed_dialog_nps_buttons_form.sh`) e `csat`+`nps` (`seed_dialog_survey_multi_form.sh`). A
     normalização está pronta e sem dado; falta um form de seed com dimensions CES/PMF/FCR para um E2E
     de verdade (e para o S6/S8 mostrarem algo além de NPS/CSAT).
   - **UI ignora `value_label`** — ✅ **reconfirmado 2026-08-03**: o ternário vazio segue literal em
     `CustomerVoicePage.tsx:161` (`data.instrument.rollup === 'avg' ? '' : ''` — os dois ramos idênticos,
     que é a assinatura de um sufixo que nunca foi escrito), e `AnaliseSurveysPage.tsx:21` declara
     `value_label` na interface sem renderizá-lo. Fatia C do S1.
   - **Rótulos mistos** — CES/PMF/FCR em inglês (spec), NPS/CSAT em pt-BR (histórico gravado). Unificar exige
     decidir migração do histórico + i18n na UI.
2. **S7 (refinos do editor `/config/dialog-forms`):** biblioteca `survey_question` reutilizável, ABAC no
   write (hoje só `X-Admin-Token`), drag reorder, locale lado-a-lado + preview.
3. **S6 (fechar):** view consolidada "Visão do cliente" (cross-cut multi-métrica + divergências §8/§10)
   sobre a base que a lente `customer_voice` já expõe (Customer Voice Fatia 1 = só grão×instrumento + SLA).
4. **Higiene S2 — enunciado CORRIGIDO (medido 2026-08-03).** Dizia *"o registry ainda roda o conjunto
   antigo"*. **Falso**: o conjunto antigo foi REMOVIDO na Camada E1 (2026-07-24) — de
   `tenant_demo.yaml` sobrou só o comentário na linha 374 explicando a remoção. O estado real é
   outro e é pior: o **trio novo existe como YAML e NENHUM pool o deploya** —
   `skill_survey_runner_v1`, `skill_survey_outbound_v1` e `skill_survey_trigger_v1` estão em
   `packages/skill-flow-engine/skills/` e não têm uma única menção em `infra/registry/`. São
   arquivos mortos da mesma família dos dois pacotes fósseis: existem, não rodam, e ensinam um
   modelo que ninguém executa. **Decidir**: deployar como pools, ou remover junto com os fósseis.
5. **Store per-response** — ✅ **FEITO E VALIDADO (2026-07-23, ver CHANGELOG).** Schema PG `survey`
   (`survey.survey_instance` + `survey.survey_response`, `db.py:632-672`), `persist_survey_response`
   (`:723`) e `list_survey_responses` (`:801`); `survey_record` persist-first; `survey_web.submit`
   captura verbatim. ADR aceito: [`docs/adr/adr-survey-response-store.md`](docs/adr/adr-survey-response-store.md).

   > **Resíduo textual removido em 2026-08-03:** este item terminava com *"**Falta só codar**"* logo
   > depois de se declarar FEITO — parágrafo da spec pré-implementação que sobreviveu à
   > implementação. Contradição dentro do mesmo item, e do tipo que engana: quem lê o fim decide
   > que há trabalho, quem lê o começo decide que não. Conferido no código antes de podar (as duas
   > tabelas, o persist e o list existem). O "endpoint de leitura de S8" que ele listava como aberto
   > também já existe — é o `list_survey_responses`.
6. **Valor novo (loop captura→leitura→ação):** **S8** ✅ **FEITO (2026-07-23).** Restante: **S9**
   (`agente_survey_analyst_v1` — classifica verbatim + áudio/transcript via `attachment_store`) →
   **S10** (retorno outbound + caixa de ações) → **S11** (NPS/PMF relacional agendado). Refino de S8:
   export CSV (opcional) + guard de rota ABAC (Item 3 app-wide).

---

## Arco de Segurança — Pool-scoping em relatórios (ABAC no DADO) *(achado 2026-07-23; Fase A preparada)*

**Problema (levantado pelo usuário, confirmado em código).** O modelo pretende que relatórios/monitores
respeitem o **domínio de pools** do usuário (Arc 7c: `accessible_pools` = filtro de linha; ABAC + grupos).
Hoje isso está **inerte** em toda a superfície de Analytics.

- **Causa raiz (app-wide):** a **platform-ui não envia `Authorization: Bearer`** nas chamadas de `/reports/*`
  e `/v1/evaluation/*` — as páginas de `/analise` usam `fetch(url)` cru; o proxy do Vite é pass-through
  (`vite.config.ts` `^/reports` e `^/v1/evaluation`, só `changeOrigin`). Sem token, o `optional_pool_principal`
  (analytics-api `pool_auth.py`) e o `_decode_jwt_optional` (evaluation-api) resolvem `accessible_pools=None`
  = **irrestrito** ("unauthenticated → all pools", documentado). Ou seja, o filtro por pool é **no-op**: qualquer
  usuário vê **todos os pools**. Vale para journeys, sessions, survey, etc. Postura de demo — mas fura o modelo.
- **Fix camada de dado:** a UI passa a anexar o `bearer()` (existe em `api/registry.ts`, lê o token em memória)
  nas chamadas de relatório — ou um gateway injeta o header. Necessário para QUALQUER scoping de Analytics
  funcionar. Distinto do **Item 3 (guard de rota ABAC)** da seção Journey: aquele protege o *chrome* da página;
  este protege o *dado*. Os dois juntos = enforcement real (rota + linha).

**Gaps ESPECÍFICOS do survey (S8) — só mordem quando o token for enviado:**
1. **`survey_instance.pool_id` não é populado na escrita.** Veículo web (`survey_web.submit`, channel-gateway):
   `pool_id` sai **sempre vazio** (o token congelado não carrega o pool da sessão pesquisada). `survey_record`
   (mcp-server): `pool_id` é input **opcional** → vazio quando omitido. **Decisão de produto**: a resposta deve
   ser atribuída ao **pool da sessão/segmento PESQUISADO** (resolver na escrita — web: do `origin_session_id`
   no `survey_link_create`/persist; record: exigir/derivar). Sem isso o scoping não tem em que se ancorar.
2. **Sem escape hatch de pool vazio** em `db.list_survey_responses` (`i.pool_id IN (...)`), ao contrário da
   analytics-api que usa `(s.pool_id IN (...) OR s.pool_id = '')` de propósito. Com o token ativo + pool vazio,
   um supervisor restrito veria **zero** respostas web (inverte "vê tudo"→"vê nada"). Decidir a política de
   pool vazio junto com o fix (1).
- **LGPD reforça a prioridade:** o verbatim é texto aberto do cliente (dado controlado); ler verbatim de pools
  fora do escopo é vazamento cross-pool, não só cosmético.
- **Referência do padrão correto:** evaluation-api `list_results` + `_compute_result_scope` (row-scope por
  role+grupo+pool, trata self-ownership) — mas **também** depende do token que a UI não manda.

**Fases:**

| Fase | Entrega | Depende |
|---|---|---|
| **A — propagar o token na UI** | ✅ **Completa (2026-07-23):** helper `apiFetch` + **8 arquivos de `analise/`** + **varredura dos demais consumidores** (18 call sites `/reports` em 15 arquivos: `contacts/*`, `contacts/tabs/*` [Monitor/Analise/Agents/AgentTimeline/Lista], `agent-reports/`, `agent-flow/*`, `service/SessionTranscript`, `billing/`, `campaigns/`, `analise/CustomerVoicePage` instruments). Único `fetch` cru remanescente a `/reports` = `api/evaluation-hooks.ts:515` (POST flush-synthetic, já anexa `bearerHeaders`). | — |
| **B — `pool_id` na escrita do survey** | 🟢 **Feito p/ web + NPS inline + J4c collect + multi (2026-07-23):** veículo web plumba `pool_id` (`survey_link_create`→token→`submit`); outbound 5b carimba `origin_pool` na metadata→dispatcher→worker; `agente_nps_v1`/`skill_survey_multi_v1` usam `@ctx.session.pool.id` (origem = self); **J4c** — `handle_collect` resolve o pool do alvo e semeia `session.survey_pool_id` no engage, `skill_survey_runner_v1` o carimba. Smokes: `smoke_outbound_fase5b.sh` + pytest `test_collect_pool_scoping.py`. **Resta 1 seam:** `skill_survey_v1` (survey_processo_ia, F10.2b delegate) grava de `@ctx.session.origin_session_id` sem passar pelo collect → semear o pool no `handle_trigger` (do `origin_session_id`). Até lá pool vazio = admin-only (decisão C). | — |
| **C — política de pool vazio** | ✅ **DECIDIDA strict (2026-07-23): pool vazio = só irrestrito/admin vê.** Sem escape hatch — respeita o domínio (resposta sem pool não pertence a nenhum domínio; over-expor a todos seria mais inseguro que sub-expor). É o comportamento ATUAL da query (`pool_id IN (domain)` já exclui vazio p/ restrito), **sem código**. O "restrito vê zero survey web" é sintoma de B (pool vazio na escrita), não de C. | — |
| **D — endpoints operacionais + `/reports/*` sem scoping** | ✅ **COMPLETA (2026-07-23):** `/v1/operational/pools` (agent-registry) + Monitor SSE `/dashboard/{operational,sentiment,pool-sla}` (token por query param) + auditoria `/reports/*`: `contact-insights` ESCOPADO (subquery a segments); demais não-escopados por decisão fundamentada (`usage`/`campaigns` não pool-atribuídos; `workflows` metadado de processo; `evaluations*` gateados por ABAC evaluation; `quality` unscoped por construção; `instruments` catálogo). Follow-up de posture: JWT em URL do SSE → cookie/ticket em prod. Ver CHANGELOG "Fase D COMPLETA". | A |
| **E — filtro de pool = combo do DOMÍNIO (não texto)** | ✅ **Completa (2026-07-23):** survey usa `PoolMultiSelect` (multi, `pool_ids[]` + reinterseção no backend); **agentes/contatos** usam o novo `PoolDomainSelect` (single) — `AnaliseAgentesPage`/`AnaliseContatosPage` trocaram o texto livre por combo do domínio (`listPools ∩ accessiblePools`). Single (não multi) por decisão: `ContactFilters.poolId` é singular e compartilhado (blast radius) e a segurança já é backend (`optional_pool_principal`). i18n `agentReports.filters.allPools`. | A |

Enforcement completo = **rota** (Item 3 do Journey — guard ABAC de `/analise/*`) + **dado** (este arco).
Ver `docs/arcos/arc7-auth.md` (ABAC/accessible_pools) e `docs/arcos/customer-surveys.md` §7.3.

### Fase A — preparada (turnkey)

**Decisão:** helper explícito `apiFetch` (consistente com o `bearer()` já existente em `api/registry.ts`), NÃO
monkey-patch do `window.fetch`. Motivo: a base já faz merge explícito de header (`bearer()`), sem interceptor
global; um patch global tem efeito colateral em chamadas que não devem levar token (auth/refresh, CDNs). O
custo do explícito (migrar call sites) é aceitável e a segurança do backend **já enforça** quando o token chega
(o gate é permissivo só na ausência) — logo A é **puramente frontend**.

1. **Novo helper** `packages/platform-ui/src/api/apiFetch.ts`:
   ```ts
   import { getAccessToken } from '@/auth/token-store'
   /** fetch que anexa Authorization: Bearer do token em memória (se houver e não já setado).
    *  Usar em TODA chamada de relatório (/reports, /v1/evaluation, /analytics). */
   export function apiFetch(input: string, init: RequestInit = {}): Promise<Response> {
     const t = getAccessToken()
     const headers = new Headers(init.headers)
     if (t && !headers.has('Authorization')) headers.set('Authorization', `Bearer ${t}`)
     return fetch(input, { ...init, headers })
   }
   ```
2. **Migrar os call sites** de `fetch(` → `apiFetch(` nas chamadas de `/reports/*` e `/v1/evaluation/*`.
   Superfície confirmada (8) em `src/modules/analise/`: `AnaliseSurveysPage`, `AnaliseJourneysPage`,
   `CustomerVoicePage`, `AnalisePoolsPage`, `AnaliseAgentesPage`, `AgentsBenchPage`, `MetricSelector`,
   `AnaliseComparacaoPage` — **+ varrer `src/modules/monitor/`** e demais consumidores de `/reports`
   (grep `fetch\(['"\`]/(reports|v1/evaluation|analytics)`). Só GET de relatório; não tocar chamadas de auth.
3. **Backend: zero mudança em A** — `optional_pool_principal` (analytics-api) e `_decode_jwt_optional`
   (evaluation-api) já leem o `Authorization` e aplicam `accessible_pools`. **Exceção:** para o survey,
   entregar a **Fase C junto** (escape hatch), senão o admin segue vendo tudo (accessible_pools vazio→None) mas
   um supervisor restrito perde as respostas web (pool vazio).
4. **Verificação:** logar com um usuário **restrito** (accessible_pools não-vazio, sem admin) → só vê linhas
   dos seus pools em `/analise/*`; admin (accessible_pools vazio→None) → vê tudo. Cobre journeys + survey.
   Guard futuro (opcional): lint/grep que falha em `fetch('/reports`|`fetch('/v1/evaluation` cru (fora do
   `apiFetch`), p/ não reintroduzir call site sem token.

**Consequência aceita (decisão C strict):** com o token fluindo (A) + a decisão strict (C), um usuário
**restrito** vê **zero** respostas web hoje (todas com pool vazio) — é correto (não pertencem ao domínio dele),
não um bug. **Admin não é afetado** (domínio vazio→None→vê tudo). A completude vem de **B** (carimbar o pool
da sessão pesquisada na escrita), que faz as respostas web aparecerem para o supervisor do pool certo.
**Próximo passo natural do arco: B.** (Validação E2E do A/C/E ✅ 2026-07-23 — admin restrito a 2 pools passou a
ver só o pool do domínio; ver CHANGELOG.)

### Fase B — 🟢 web + NPS inline feitos (2026-07-23); falta J4c runner/workflow

**Entregue (ver CHANGELOG § "Segurança — Pool-scoping: Fase B"):** veículo web plumba `pool_id`
(`survey_link_create`→`create` congela no token→`submit` carimba persist + `session.signals`); outbound 5b
carimba `origin_pool` na metadata→dispatcher (`session.survey_origin_pool`)→worker; `agente_nps_v1` passa
`@ctx.session.pool.id`. Smoke `smoke_outbound_fase5b.sh` prova pool não-vazio + controle negativo.

**J4c collect-based ✅ (2026-07-23):** `handle_collect` resolve o pool do alvo (`signal_target_id`) do ctx
(`session.pool.id`), congela em `pending.signal_pool_id`; `handle_collect_engage` semeia `session.survey_pool_id`;
`skill_survey_runner_v1` passa `pool_id: "@ctx.session.survey_pool_id"`. `skill_survey_multi_v1` pesquisa a
própria sessão → `@ctx.session.pool.id`. Pytest `test_collect_pool_scoping.py`.

**Resta `skill_survey_v1` (F10.2b delegate, survey_processo_ia):** grava via `survey_record` de
`@ctx.session.origin_session_id`, mas NÃO passa pelo `handle_collect` (é delegate, não collect). Para carimbar o
pool: no `handle_trigger` (webhook.py), quando `origin_session_id` vier no `workflow_trigger`, ler o
`session.pool.id` do ctx da origem e semear `session.survey_pool_id` na sessão do workflow → `skill_survey_v1`
passa `pool_id: "@ctx.session.survey_pool_id"`. Mudança genérica no trigger (afeta todo trigger com origin) —
avaliar custo/benefício. Até lá, pool vazio = admin-only (decisão C), correto e sem crash.

**Objetivo (histórico):** `survey_instance.pool_id` deixa de nascer vazio — carimbar o **pool da sessão/segmento
PESQUISADO**, para a resposta ter domínio e o supervisor do pool certo a ver.

**Decisão de produto:** o pool da resposta = o pool da **sessão de origem** (`origin_session_id`), não o pool
do dispatcher/runner de survey. É o atendimento que gerou a pesquisa que define o domínio.

**Dois veículos (investigar a origem do pool em cada um):**
1. **Web** (`survey_web`, channel-gateway): o token (`survey_web:token`) tem `origin_session_id`+`grain` mas
   **não** o pool. Duas opções a decidir: **(a)** `survey_link_create` (mcp-server `tools/survey.ts`) passa o
   `pool_id` do contexto do chamador (o hook/skill que cria o link roda numa sessão COM pool — `session.pool.id`
   no ContextStore) → congela no token → persiste; **(b)** resolver no persist a partir do `origin_session_id`
   (lookup do pool da sessão — analytics-api `sessions.pool_id` OU ContextStore `session.pool.id`). (a) é mais
   barato (sem lookup) e o pool já está no contexto de quem dispara; preferir (a), (b) como fallback.
2. **Conferência/inline** (`survey_record`, mcp-server): `pool_id` é input **opcional**. O runner/inline
   (`agente_nps_v1`, `skill_survey_runner_v1`) roda na sessão pesquisada → tem `session.pool.id` no contexto →
   passar via `$.pipeline_state`/`@ctx`. Verificar se o skill já resolve o pool e só não o passa.

**Escopo mínimo:** carimbar o pool na escrita (web + record) + demo/smoke que prova a resposta nascendo com o
pool real (não vazio) e o usuário restrito daquele pool passando a vê-la. **Não** precisa migração de dado
antigo (pool vazio legado = admin-only, decisão C). **Entry points:** `channel-gateway/survey_web.py` (create/
submit + token record), `mcp-server/tools/survey.ts` (`survey_link_create`/`survey_record`), ContextStore
`session.pool.id` (escrito pela Routing Engine no `_write_pool_context`). Ver ADR `adr-survey-response-store.md`
(o `pool_id` já existe no schema; falta a origem na escrita) e `customer-surveys.md` §7.3.

### Fase E — filtro de pool = combo do domínio ✅ (2026-07-23)

**Concluída:** survey → `PoolMultiSelect` (multi, `pool_ids[]`); agentes/contatos → `PoolDomainSelect` (single,
`components/ui/PoolDomainSelect.tsx`) em `AnaliseAgentesPage`/`AnaliseContatosPage`. Single por decisão
(`ContactFilters.poolId` singular/compartilhado; segurança já no backend). Ver CHANGELOG "Fase E (combo do
domínio em agentes/contatos)". Notas de design abaixo (mantidas p/ referência).

**Confirmado (2026-07-23):** o domínio do usuário = bloco **"Accessible Pools"** em Configuration > Access
(`AccessPage.tsx` → `user.accessible_pools` na auth-api → claim `accessible_pools` no JWT; **vazio = todos**).
A sessão **já expõe** isso no client: `useAuth().session.accessiblePools` (`AuthContext`, `[]` = todos).

**Problema:** o filtro de pool nas telas de Analytics é **caixa de texto** — `AnaliseSurveysPage.tsx:233` (a
nova), `AnaliseAgentesPage.tsx:376`, `AnaliseContatosPage.tsx:107`. Deveria ser um **combo multi-select do
domínio**. (`AnaliseJourneysPage`/`CustomerVoicePage` não têm filtro de pool.)

**Design:**
1. **Fonte das opções (client):** `registryApi.listPools(tenantId)` (`api/registry.ts`, já normaliza `items`)
   **∩ `session.accessiblePools`** — se `accessiblePools` vazio (admin) → lista cheia. Assim o combo mostra
   só o que o usuário pode ver (o filtro nunca oferece pool fora do domínio). Referência de `<select>`
   populado por `listPools`: `AnaliseProcessosPage.tsx` (fetch L104-108 + select L151-157) — copiar, mas
   **multi-select** (checkbox-list, como o de `AccessPage.tsx` L430-478, o único multi-select do app; não há
   componente compartilhado — extrair um `PoolMultiSelect` reusável é oportuno).
2. **Backend aceita lista:** `GET /v1/evaluation/survey/responses` troca `pool_id: str` por `pool_ids`
   (repetido ou CSV); `db.list_survey_responses` já filtra `i.pool_id IN (...)` — passar a lista do filtro
   **interseccionada com `accessible_pools`** (o filtro é subconjunto do domínio; a fronteira dura continua no
   scoping da Fase A/C). Vazio no filtro = todo o domínio (não todos os pools).
3. **Invariante:** filtro (subconjunto escolhido) ≠ scoping (domínio permitido). O combo só oferece o domínio;
   o backend **sempre** reintersecta com `accessible_pools` (nunca confia só na UI).
4. Aplicar o mesmo `PoolMultiSelect` às outras telas de texto (agentes, contatos) na varredura.

---

## Detach de hooks de finalização + Pull direcionado + ACW *(desenho fechado 2026-07-23; Camada A iniciada)*

Unifica a coleta de finalização (survey/wrap-up) e aposenta a **Forma A (delegate `skill_survey_v1`)**. Hooks de
finalização não podem suspender/collect (o bridge trata `suspended` como concluído → fecha o contato cedo). A
razão de segurar o contato é **atribuição** — que a Journey (`root_session_id`) + referência de segmento no
payload resolvem sem segurar. Reduz de 3 mecanismos (inline/delegate/collect) para 2 (inline síncrono / collect
assíncrono). Fecha **G1** (AHT inflado por wrap-up) e generaliza **G7** (desacoplamento de `on_human_end`).

**Invariante preservado (PABX):** o "ramal" (direcionar a um recurso) NÃO vira alvo de roteamento — é um work
item que mora num **pool** (fila) com filtro de claim `assigned_to` + **fallback pro pool** por lease. Fila =
pool+dispatch; ramal = pull item direcionado + overflow. Embrião de transfer-to-agent, sem quebrar o invariante.

**Camadas:**
- **A — fundação ✅ (iniciada):** `dispatch: inline|detached` no `PoolHookEntry` (`@plughub/schemas`), default
  `inline`; guard de parse rejeita `detached` em `on_human_start` (não-finalização). Rebuild: agent-registry +
  skill-flow-service + mcp-server (validam skills/pools).
- **B — pull direcionado ✅ (2026-07-24, smoke 5/5):** `assigned_to` + `fallback_to_pool_after_s` +
  `assigned_at_ms` no work item + claim-eligibility em `Router.work_task_claim` (reusa `dispatch_mode: pull`/
  `work_queue`/`PullInboxPanel`). Wrap-up como consumidor = Camada E (não wirado aqui). Smoke
  `infra/test/smoke_directed_pull.sh`.
- **~~C — ACW~~ REVERTIDA (Phase 0) e REMOVIDA (2026-07-29):** entregue em 2026-07-24 (`acw_gate: none|soft|hard`
  + marker `:acw_pending` + regra em `get_ready_instances` + UI + smoke 3/3) e desfeita por operar na **unidade
  errada** — bloqueava a instância inteira (não a vaga) e reservava no dispatch (não no claim). A Phase 0 tirou
  enforcement/marker/smoke; a coluna e todo o plumbing saíram em 2026-07-29 (migration
  `20260729000000_drop_pool_acw_gate`). Capacidade de wrap-up = 1 vaga pelo `claim_instance`, nos dois modos.
  **E2e (produtor do marker) sai de escopo junto.**
- **D — bridge ✅ (2026-07-24, smoke 2/2):** `_fire_detached_hook` (workflow webhook fire-and-forget
  `POST {CHANNEL_GATEWAY_URL}/v1/channels/webhook/pool/{id}`, `origin_session_id`+`journey:inherit`+ref de segmento
  no `context`); `_entry_will_dispatch` exclui detached do barrier (`hook_pending`/`posatt`); auto-close
  `_trigger_contact_close` na leva 100% detached de finalização (fecha G1); guardas `_has_customer_hooks` (IA-primário
  + humano) excluem detached; env `CHANNEL_GATEWAY_URL`. **conference-mechanics.md § Histórico → Mudança 25 ✅.**
  Limitações registradas: `post_human`+detached e `segment_wrapup` fanout detached → Camada E. Smoke
  `infra/test/smoke_detached_hook.sh`.
- **E1 — Forma A aposentada ✅ (2026-07-24):** pools `survey_processo_ia`/`survey_collector_ia`/`survey_reconnect_ia`
  + skills `skill_survey_v1`/`skill_survey_nps_v1`/`skill_survey_reconnect_v1` estavam **inertes** (sem hook/trigger
  vivo); removidos do YAML + arquivos. Coleta de survey = NPS inline + J4c collect. *(DB rodando persiste inerte;
  purge opcional via PRUNE — sem DELETE de pool na API.)*
- **Renderer R0 ✅ (2026-07-24, pré-requisito do Path α):** `DialogFormRenderer.tsx` (núcleo genérico) entregue e
  validado — ver CHANGELOG "Renderer genérico de collect-form no Console — R0". Superfície estável que a E2
  consome: claim de workflow suspensa (`session.dialog_form_id`+resume token) → briefing (`session.briefing_session_id`)
  + DialogForm → `workflow_resume` com `payload.answers`. Falta só o conteúdo/plumbing da E2 (abaixo).
- **E2 — wrap-up humano → `detached` (pendente):** `agente_wrapup_v1`/`wrapup_ia` (inline hoje) vira item de pull
  inbox `assigned_to` o humano (fecha G1 do humano). Plumbar `assigned_to` webhook trigger→routing; `wrapup_ia`→
  `dispatch_mode: pull`; skill de wrap-up como workflow pull (DialogForm no claim); gravação do outcome por
  referência (`surveyed_segment_id`); **produtor do marker `acw_pending`** (setar no dispatch detached de pool
  `hard`, limpar na resolução); briefing. NPS síncrono presente fica `inline`. Fecha as limitações da Camada D
  (post_human+detached, segment_wrapup fanout). **Desenho FECHADO** → ADR
  [`docs/adr/adr-wrapup-detached-pull.md`](docs/adr/adr-wrapup-detached-pull.md). **Decisão (2026-07-24): Path α,
  renderer-first** — o renderer é o **tratamento genérico de collect-form no Console** (não "renderer de
  aprovação"; reenquadramento 2026-07-24, ADR §2.1): renderiza o DialogForm de qualquer `collect`/`delegate`
  reivindicado no inbox pull + submit via `workflow_resume`; serve aprovação + wrap-up + survey-no-Console **sem
  skill por caso** (o wrap-up deixa de ter skill próprio). Construir ANTES (arco/sessão dedicado; kickoff do
  núcleo R0 em `docs/product/approval-renderer-kickoff.md`); wrap-up-α por cima. Path β (skill agente menu) **NÃO
  viável no pull-standalone** (humano reivindica → vira primário, sem IA p/ renderizar; só o Console renderiza).
  Comuns aos dois
  (não se perdem na troca): **E2a** (DialogForm
  `dialog_wrapup_v1` + skill) · **E2b** (tool `segment_outcome_record`) · **E2c** (plumbing `assigned_to` no
  `ConversationInboundEvent`) · **E2d** (dispatch pull sintético no bridge) · **E2e** (`acw_pending` set/clear) ·
  **E2f** (analytics: sessão de wrap-up fora da contagem de contato/TMA — **ponto de atenção**) · **E2g** (config
  `wrapup_ia`→pull + smoke E2E).
- **F — validação:** G1 (AHT), atribuição de segmento no relatório, smoke wrap-up na pull inbox (claim direcionado
  + fallback), pool-scoping do survey sem delegate.

Design fechado: [`docs/product/finalization-hooks-detach-and-directed-pull-design.md`](docs/product/finalization-hooks-detach-and-directed-pull-design.md).

### Camada B — pull direcionado ("ramal") — ✅ (2026-07-24, smoke 5/5; ver CHANGELOG)

> **As-built (2026-07-24):** entregue conforme o kickoff abaixo. Toques do que ficou:
> - **Item = dict `contact_data`** (JSON em `{t}:queue_contact:{sid}`) — sem novo schema Zod; campos `assigned_to`/
>   `fallback_to_pool_after_s`/`assigned_at_ms` tipados em `QueuedContact` (routing `models.py`) e na interface
>   `QueueContact` (TS: `lib/work-queue.ts` + `PullInboxPanel`).
> - **Âncora da janela = `assigned_at_ms`**, auto-carimbada no 1º `add_queued_contact` (registry) e **preservada
>   no re-enqueue** (contact_data re-passado verbatim) — a janela conta desde a atribuição, não reinicia a cada
>   requeue. Fallback p/ `queued_at_ms` se ausente.
> - **Gate em `Router.work_task_claim`** (antes do `ZREM`): reservado só é claimable pelo dono OU após transbordo
>   (idade ≥ `fallback_to_pool_after_s`; ausente = permanente). `reason: reserved_to_other`, **logado** (degradação
>   nunca silenciosa). Sem I/O extra — âncora já no pacote lido no passo 2.
> - **Claimant** = `claimant_user_id` explícito (opcional, plumbado em http_api/tools/server) OU derivado de
>   `instance_id` (`human-{userId}`). Retrocompat: sem `assigned_to` = fila compartilhada (comportamento atual).
> - **Inbox:** `PullInboxPanel` esconde reservados-a-outro (até transbordo), rotula "reservado a você"/"transbordado",
>   ordena reservados-a-mim primeiro; i18n `pullInbox.{reservedToYou,overflow}` + `claimReason.reserved_to_other`.
> - **Sem reaper de lease** (o transbordo é por idade do item, não expiração de lease — o kickoff antecipava lease;
>   o modelo real dispensa). Smoke `infra/test/smoke_directed_pull.sh` (userB barrado na janela; dono sempre;
>   userB após transbordo; reserva permanente nunca transborda).
> - **Validado (2026-07-24):** build dos 3 serviços OK + smoke `smoke_directed_pull.sh` 5/5. **Não wirado:**
>   wrap-up como consumidor = Camada E.

**Objetivo:** um work item da fila pull pode ser **reservado** a um recurso específico (`assigned_to`), com
**transbordo pro pool** por lease. Fila = pool; ramal = item direcionado + overflow. Invariante: `assigned_to` é
elegibilidade de claim sobre trabalho *pooled* — **nunca** alvo de roteamento que bypassa o pool.

**Pré-investigação (abrir a sessão lendo isto):** onde vive o work item e o claim hoje —
- Routing Engine: `dispatch_mode: pull` (claim atômico `ZREM`, lease+auto-release). Achar a estrutura do item na
  fila e o handler de claim (`work_queue_claim`?). Ver `packages/routing-engine`.
- Tools MCP `work_queue_*` (mcp-server-plughub) — o preview/claim que a UI consome.
- `PullInboxPanel` (platform-ui) — como lista/filtra os itens.
- ADR `docs/adr/adr-human-approval-workflow-step.md` (a aprovação já é o 1º uso do pull; reusar o mesmo item).

**Sub-etapas:**
1. **Schema do work item:** `assigned_to?: string` (user_id preferido) + `fallback_to_pool_after_s?: number`
   (default: sem reserva). Onde o item é modelado (schemas / routing). Retrocompat: ausência = fila compartilhada
   (comportamento atual).
2. **Claim-eligibility no Routing Engine:** ao reivindicar, um item com `assigned_to` só é elegível se
   `claimant.user_id == assigned_to` **OU** a idade do item ≥ `fallback_to_pool_after_s` (aí vira claimable por
   qualquer um do pool/grupo). Sem `assigned_to` = elegível a todos (hoje). Cuidar do hot path (barato, sem
   query extra — a idade já está no ZSET score).
3. **Fallback por lease:** o transbordo é do **direcionamento**, não do item (o item continua na fila; só deixa
   de ser exclusivo). Nada de mover de fila.
4. **Tools MCP `work_queue_*`:** expor `assigned_to`/estado ("reservado a você" × "transbordado") no preview.
5. **`PullInboxPanel`:** mostrar itens reservados ao usuário + rótulo de transbordo; ordenar reservados primeiro.
6. **Smoke:** enfileira item com `assigned_to=userA` + `fallback` curto → userB NÃO vê antes do fallback; após,
   userB vê; userA vê sempre. `infra/test/smoke_directed_pull.sh`.

**Não fazer nesta camada:** o wrap-up ainda não é wirado como consumidor (isso é a Camada E, depois de a B e a D
existirem); aqui só o primitivo genérico de pull direcionado. E **nunca** transformar `assigned_to` em alvo de
roteamento (bypass do pool) — é filtro de claim com fallback.

---

## Histórico de contatos do cliente — backlog pós-H5

> O arco Customer History está **completo no v1** (H1–H5 + C1a/C1b ✅ — ver `CHANGELOG.md` e
> `docs/arcos/customer-contact-history.md` §9). Resta:
- **Busca full-text `GIN(tsvector)` (escala)** *(adiado no H5)* — a busca de mensagens (H2) usa hoje
  ClickHouse substring (`positionCaseInsensitiveUTF8`), suficiente no volume atual. Para escala, migrar
  para full-text tokenizado real (índice `GIN(tsvector)` no Postgres `session_stream_events`, ou skip-index
  ClickHouse). É otimização, não correção — a busca funciona. Gatilho: latência/volume medidos.
- **H4-survey** *(bloqueado)* — origem+resultado do survey no **briefing de retorno** (`customer-surveys.md`
  §19), que ainda não existe.

---

## Scheduler / Outbound — resíduos *(arco Scheduler 1–3 ✅ e arco Outbound 1–5 ✅; histórico no CHANGELOG)*

- **Fase 3b do Outbound — ⚠️ a validar:** opt-out global `do_not_contact` no cadastro (identity), veto de
  maior precedência no eligibility salvo `transactional`; `mailing_unsubscribe scope=global` escreve o
  atributo. O smoke `infra/test/smoke_outbound_fase3b.sh` está escrito mas **não foi validado**.
- **Refinamentos do Outbound 5b (backlog):** `responded` por-delivery (submit → `campaign_delivery_result`);
  skill de processo que **auto-alimenta a mailing** no `complete` (journey_complete real — hoje é seed direto).

### Migração dos timers legados *(follow-up — antigo "Scheduler central de timers")*

Consolidar os timers espalhados (timeout de suspend/delegate no channel-gateway,
`_hook_timeout_guard` no bridge, timeout de `collect`) no substrato do scheduler-api:
sorted-set de deadlines (`ZADD`/`ZRANGEBYSCORE`) + poller único + evento `timer.fired`
com os donos reagindo; calendar-api permanece o engine de prazo (calcula o *quando*, não
dispara). Primeiro corte funcional já existe (`run_timeout_scanner` no channel-gateway).
Decisão e mecanismo em [`docs/adr/adr-timer-scheduler.md`](docs/adr/adr-timer-scheduler.md).

---

## Agent-registry — unificar binding skill↔pool (2→1) *(proposta — concern do registry)*

Origem: discussão do doc de avaliação (`docs/arcos/arc-evaluation-metrics-methodology.md` §IV.3),
scoped-out de lá por ser refactor do agent-registry, não de avaliação.

**Achado (revisado 2026-07-20):** a associação skill↔pool aparecia em **três** lugares no `schema.prisma`, mas
o `SkillVersionSlot.pool_ids` (3-slot POR skill) **já foi aposentado** (Skill Versioning Fase E, 2026-06-24 — o
modelo virou pool-cêntrico; `db push` dropou `skill_version_slots`). Hoje sobram **dois**: `PoolSkillSlot`
(slot do pool — binding vivo, autoritativo) e `SkillDeployment.pool_ids` (histórico de deploy). Risco de
divergência entre eles.

**Alvo**: `PoolSkillSlot` como relação **autoritativa** do binding atual + o histórico como **append-log** das
mudanças de slot (o `SkillDeployment` deixaria de precisar do próprio `pool_ids`, derivável do contexto).
**Pré-trabalho**: auditar os readers de `pool_ids` (routing/alocação no caminho quente, RegistrySyncer, lente
deploy do Arc 6 Fase 2, `GET /v1/pools/:id/deployments`) antes de dropar o campo. Escopo menor do que o "3→1"
original sugeria.

---

## Skill hot-reload via YAML em disco sem restart *(deferred — dev/demo only)*

**Fluxo editor → deploy já funciona**: `POST /v1/skills/:id/deploy` → `publishRegistryChanged` → bridge invalida `_skill_flow_cache` → próxima execução busca conteúdo atualizado do agent-registry. Nenhuma mudança necessária para este caminho.

**Gap**: edição direta de arquivo YAML em disco (dev/demo) ainda requer `restart orchestrator-bridge` para o RegistrySyncer re-ler e fazer PUT para o agent-registry. A solução correta é um endpoint `POST /admin/skills/sync` (ou handler de `registry.changed` com `source: disk`) no bridge — chama `RegistrySyncer._sync_skills()` → PUT → `registry.changed` → cache invalidado. Deve ser acionado pelo processo de deploy YAML (CI/CD, script), não pelo editor.

---

## Arc 19 — cleanup residual de infra *(arco concluído 2026-05-28; histórico no CHANGELOG)*

Remover o tópico `workflow.events` do Kafka e arquivar o package `skill-flow-worker`.

---

## Usage Metering — Channel Gateway Adapters *(deferred)*

Funções em `usage_emitter.py` implementadas, mas os adapters de canal ainda não as chamam. Será wired quando cada adapter for criado:

- `whatsapp_conversations` — adapter WhatsApp
- `voice_minutes` — adapter WebRTC/Voice
- `sms_segments` — adapter SMS
- `email_messages` — adapter Email

---

## Pricing Module — Integração metering × pricing *(deferred)*

Módulo que lê contadores de `usage.events` no Redis/ClickHouse, aplica planos configurados no Config API e escreve `{tenant}:quota:limit:*` no Redis. Metering registra mas pricing não consome ainda.

---

## Masking — Bloco 3: Channel Gateway TTS *(deferred até implementação de voz)*

Quando qualquer adapter de voz/TTS for criado, deve consultar `rule.{category}.display_voice` no namespace `masking` do Config API antes de passar texto ao sintetizador. Comportamentos: `silence` (pula o valor), `beep` (tom de beep), `speak_placeholder` (fala "valor mascarado"). Não implementar antes de definir qual engine TTS será usada.

---

## Audit LGPD — Fases Pendentes

Fase 1 concluída — ver CHANGELOG 2026-05-14 e `docs/arcos/audit-lgpd.md`.

- **Fase 2** — `original_content` desmascarado: endpoint de resolução de tokens em Core → analytics-api expõe conteúdo original ao DPO. Requer endpoint batch de resolução de tokens no Core.
- **Fase 3** — `user_access` logs: topic Kafka `user_access.events` em auth-api + tabela ClickHouse + tab ativo em AuditPage.
- **Fase 4** — SAR/Erasure pipeline: CRUD de Subject Access Requests + pseudonimização em `sessions_stream` + anonimização ClickHouse (TTL/partition replacement).
- **Fase 5** — `config_snapshot`: leitura read-only do namespace `masking` do Config API para verificação DPO.

---

## Business in Any Media — processo channel-abstract + framework de loja *(proposta — não implementado)*

Reposicionamento process-centric ("nunca perca um negócio por causa de canal") + framework de comércio conversacional sobre o modelo de 3 níveis (a = fluxo negocial channel-abstract; b = acesso a canais; c = agente de I/O). Especificações em `docs/product/`:

- **Arquitetura-alvo (3 níveis)** — [`docs/product/business-in-any-media-arquitetura-alvo.md`](docs/product/business-in-any-media-arquitetura-alvo.md) + diagrama `business-in-any-media-3-niveis.svg`. Define as 3 camadas, contratos, e o que falta construir no nível (b).
- **Resolvedor de identidade + cadastro (nível b)** — [`docs/product/identity-resolver-nivel-b-spec.md`](docs/product/identity-resolver-nivel-b-spec.md) + sequência `identity-resolver-sequencia.mermaid`. Generaliza o `pending_workflow` existente: cadastro nativo (`customer_id` canônico, dois andares Redis/PG), índice multi-âncora hasheado, retomada cross-canal. Governança: plataforma não é autoridade de identidade/pagamento; só chaves mascaradas; uso interno.
- **Contrato delegate por pool (a→b)** — [`docs/product/delegate-contrato-por-pool-spec.md`](docs/product/delegate-contrato-por-pool-spec.md). Delegação por pool (não skill); decidido alinhar `task.target` a pool; 1 skill publicada por pool; gate de identificação como lógica de fluxo (não campo de schema).
- **Commerce-cards (nível c)** — [`docs/product/commerce-cards-nivel-c-spec.md`](docs/product/commerce-cards-nivel-c-spec.md). `component` tipado em `notify`/`menu` (product_card/carousel/cart/checkout/order_status), render nativo por canal; checkout com masked input + repasse ao PSP; novas ChannelCapability `rich_card`/`carousel`.
- **Fluxo de intake (nível c)** — [`docs/product/intake-flow-nivel-c-spec.md`](docs/product/intake-flow-nivel-c-spec.md). Generaliza o `agente_portabilidade_intake_v1`: resolve identidade (origem do canal) → checa pendência → oferta de retomada → roteia intenção; gate de identificação flow-wired.

Descritivo técnico-funcional consolidado (com a seção de roadmap §20.7): [`docs/product/plughub-descritivo-tecnico-funcional.md`](docs/product/plughub-descritivo-tecnico-funcional.md) (+ `.html` print-ready) — **manter atualizado conforme cada item for implementado**.

**Base que já existe** (não confundir com o gap): workflow + canais + suspend/resume + retomada via `pending_workflow` + masking. **A construir**: cadastro de identidade completo, commerce-cards, gate, e o nível (b) como camada de primeira classe.

---

## Fila de trabalho humano / dispatch pull + inbox no Console — resíduos pós-v1 *(v1 concluído 2026-07-17; histórico no CHANGELOG)*

**Resta (A6 — pós-v1, ADR §6 `adr-human-approval-workflow-step.md`):** quatro-olhos (2 aprovadores);
reatribuição por supervisor (= conferência padrão); notificações/SLA na inbox; **rework rate**
(Bancada/Arc 6); **auto-aprovação** (pool IA). **Não-objetivos v1 (adiados por decisão):** omnichannel/
Modo B (D6); weight-ordering (F6); **promote real** (invoke de deploy no `efetuar_promocao`, hoje
`complete`). **Follow-ups menores (CHANGELOG A5):** Context/History trazendo a journey do workflow por
`root_session_id` (aprovação raramente tem `customer_id`); gate de servibilidade do pool de aprovação
pelo ABAC `approvals` (fechar o claim genérico); refresh imediato do inbox pós-release.

**Diferido desde a F1.3** (spec "sem sweep dedicado"): renovação da lease de claim por heartbeat +
sweeper de "conectado-mas-ocioso". Hoje o auto-release do pull é emergente (desconexão → bridge
re-roteia → `route()` parqueia e limpa a lease); a inbox sinaliza melhor que um sweep.

### Achados de 2026-08-04 (na validação do F2) — três, em ordem de gravidade

1. ~~**A recusa de claim é EFÊMERA demais**~~ ✅ **2026-08-04 — PREMISSA FALSA, medida na tela.**
   Esta é a **terceira** redação do item, e as duas primeiras erraram pelo mesmo motivo: descreviam
   a tela a partir do que se esperava do código. O que a leitura dos handlers + o experimento
   acharam:

   · **A inbox não tem botão de claim.** A linha do item é um `<button>` único que abre o preview
     (`PullInboxPanel.tsx` §342). O `handlePull` — o do `setError` que as redações anteriores
     chamavam de "erro fixo no painel" — tem **um único chamador**: o efeito de auto-atendimento do
     wrap-up (§254). O operador nunca o dispara clicando.
   · **E esse erro se apaga sozinho**: `setError(motivo)` na §224, `await refresh()` na §225, e
     `refresh()` faz `setError(null)` na §130. Vive o tempo de um fetch. *(Contraria "degradação
     NUNCA é silenciosa", mas só no caminho do auto-atendimento — item próprio, abaixo.)*
   · **O claim manual é só o do preview, e lá o botão é `disabled={atCapacity}`**
     (`AgentAssistPage.tsx` §681). Com as vagas cheias **não há clique, logo não há toast**.

   **Experimento** (`infra/test/probe_claim_capacity_sources.sh` + tela, agente `human-bef14526…`,
   `max_concurrent` 3), previsto × medido — todos bateram:

   | | previsto | medido |
   |---|---|---|
   | cartões × ocupantes do semáforo | iguais | **iguais** (3 e 3, mesmos ids) |
   | botão do preview com vagas cheias | cinza | **cinza** |
   | toast de `no_capacity` | não dispara | **não disparou** |
   | teto cliente (JWT) × árbitro (registro) | 3 × 3 | **3 × 3** |

   **O gap real, que sobrou e foi consertado:** o teto nunca era exibido. O crachá dizia
   "Serving 3", não "3/3", e a única explicação do botão cinza era um `title` — hover apenas,
   inexistente no toque. Um controle desabilitado sem causa legível é lido como tela quebrada, e foi
   essa leitura ("reivindiquei e veio vazio") que disparou a investigação errada. Fix: crachá vira
   fração e ganha cor de lotação; o preview mostra "Todas as vagas em uso (3/3)" ao lado do botão.

   **Não fazer:** tornar o toast persistente. Ele não dispara neste caminho — seria conserto
   especulativo sobre um defeito não observado.

1b. ~~**`handlePull` apaga o próprio motivo de falha**~~ ✅ **JÁ ESTAVA CONSERTADO — item obsoleto,
   fechado por LEITURA + medição em 2026-08-05.** Nenhuma linha de código foi escrita para fechá-lo;
   o trabalho foi descobrir que ele já não descrevia o código. O que `PullInboxPanel.tsx` tem hoje:

   · **estado `claimError` próprio**, separado do `error` de listagem (§115) — e o `refresh()` roda
     ANTES do `setClaimError` (§241), com comentário explicando que a ordem é o conserto;
   · **faixa persistente** com botão de dispensa (§335-353);
   · **invalidação pelo fato certo**: a recusa some quando o ITEM sai da fila (§257), não quando a
     lista recarrega — recarregar não torna a recusa falsa;
   · props `claimDisabled`/`claimDisabledReason` **removidas** (§51-55, com lápide);
   · i18n completo nos DOIS locales, incluindo os 6 `pullInbox.claimReason.*`.

   **Verificado onde importa, não só no repo:** `grep claimFailedDismiss` dentro do bundle servido
   (`/usr/share/nginx/html/assets/index-*.js`) devolve o arquivo — o conserto está na TELA, não só no
   fonte. Num pacote sem volume mount, essa é a única prova que vale; o fonte estar certo é
   compatível com o Console rodar o bundle de ontem.

   **Lição de método:** este item descrevia como "aberto" um caminho (`setError` §224 → `refresh()`
   §225) que o código já não tinha. *Entrada de TODO é comentário como qualquer outro — envelhece, e
   envelhece em silêncio.* A releitura custou minutos; implementar por cima do registro teria custado
   um conserto duplicado sobre código já correto. **Antes de abrir um item antigo, conferir contra o
   código que executa** — a mesma regra que vale para docstring, e pelo mesmo motivo.

1c. **Item reivindicado depois de sair do ZSET** *(observação, causa não determinada)*. Na montagem
   deste experimento, `509d5441…` foi removido da fila (`ZREM` → `1`) e o probe seguinte mostrou o
   semáforo **vazio**; ainda assim o item foi reivindicado com sucesso minutos depois e virou
   cartão. O `work_task_claim` §739 exige o `ZREM` vencedor (`atomic_claim_dequeue`), logo ele
   **estava** no ZSET no momento do claim — algo o repôs. Pode ser um reconciliador fazendo o certo
   (o ZSET é projeção; o ledger `work_task` é a verdade) ou um requeue perdido. **O `ZREM` foi
   mutação sintética minha** — não teorizar sobre rastro próprio; reproduzir sem ele antes de
   chamar de defeito.

2. ~~**Vaga ocupada por sessão que nunca virou cartão.**~~ ✅ **CONSERTADO 2026-08-05** — transporte
   `agent_release_item`: o mcp-server anuncia `contact_closed` depois do `released:true` do árbitro e
   o **bridge** faz o desmonte pelo caminho da queda. Escopo maior que o mínimo por medição: publicar
   `agent_released` também restaura a membership do `ready_set`, que o `work_task_release` não toca
   (sem isso o agente sumia do push a cada devolução). Validado na tela, 3 cartões no re-claim contra
   0 antes, `Skipping duplicate` = 0. Detalhe em `CHANGELOG.md` e `docs/guias/conference-mechanics.md`
   § Mudança 32; instrumento em `infra/test/probe_release_presence.sh`.

   **Lição de método, guardada porque muda como se monta reprodução:** os marcadores expiram com o
   TTL da SESSÃO. O lixo deixado como "reprodução viva" já não reproduzia nada 14 h depois — as vagas
   seguiam presas, os marcadores não existiam mais. **O rastro sobrevive ao mecanismo**, e medir o
   rastro não mede a causa. Reproduções que dependem de estado com TTL precisam ser refeitas do zero
   na sessão que as usa.

   <details><summary>Diagnóstico original (2026-08-04) — mantido pelo valor de método</summary>

   **Receita de reprodução (3/3, determinística):** reivindicar N itens → **"Return to queue"** em
   todos → reivindicar de novo. Os re-claims sobem a ocupação (1→2→3) e **nenhum cartão aparece**.

   **Cadeia, medida:**
   1. MONITOR do Redis: os três `ZREM` de claim em `…921`, `…928`, `…932` (deltas 7s e 4s), cada um
      com o `EVAL` de ocupação subindo. O árbitro concedeu as três vagas.
   2. `orchestrator-bridge`: três `Skipping duplicate routing for already-served session …
      skill_running=False human_active=True` em **22:18:41, 22:18:48, 22:18:52** — deltas **7s e 4s**.
      Mesmos eventos, casados pelo relógio.
   3. `mcp-server-plughub`: **nenhum** `Forwarding conversation.assigned` nesses instantes (os três que
      aparecem no log são da 1ª rodada de claims, 22:08–22:09). O evento nunca chegou ao frontend —
      o que também **exclui o bundle de UI** como causa.
   4. `EXISTS {t}:session:{sid}:closed` = **0** nos três: sessões VIVAS. Vaga gasta, trabalho vivo,
      tela vazia.

   **Causa:** o guard de dedup (`main.py` §3509-3519) descarta `conversations.routed` sem
   `conference_id` quando existe `session:{sid}:human_agent`. Esse marcador é escrito por
   `activate_human_agent` (§808, `SETEX` no TTL da sessão) e apagado **só** em caminhos de
   encerramento/queda: close de contato (§2696, §6347), último humano derrubado (§6810) e
   `agent_done` (§7284). **O `work_task_release` ("Return to queue") não está entre eles** — libera a
   vaga do árbitro e deixa a presença no bridge. O re-claim vira, para o guard, uma re-emissão do
   drain periódico (que é contra o que o guard foi escrito) e é indistinguível dela.

   Mesma assimetria já registrada no CLAUDE.md sobre este caminho — *"`remove_conversation` também
   restaura a membership dos SETs do pool, que o `work_task_release` não faz"*. Mais um fato que ele
   não restaura.

   **Forma do conserto (não implementado):** limpar `session:{sid}:human_agent` + o membro
   correspondente de `session:{sid}:human_agents` na devolução à fila, simétrico ao ramo de queda do
   §6810 — o marcador significa "há humano anexado", e depois do release não há. **Preferir isso a
   afrouxar o guard**: exceção no guard trocaria um caso mudo por outro (o spam de
   `participant_joined` que ele existe para impedir), enquanto corrigir o estado torna o guard
   verdadeiro. Cuidado ao escrever o teste: o TTL do marcador é o da sessão, então "some sozinho"
   é uma janela de horas, não um conserto.

   **Tentativa de reprodução em 2026-08-04 (achado 1): NÃO reproduziu.** Três claims seguidos,
   ocupantes do semáforo == cartões na tela, mesmos ids, nenhum hold. Isso **não** absolve o
   achado — só diz que o caminho feliz não o produz. Ficou o instrumento:
   `infra/test/probe_claim_capacity_sources.sh` **identifica** cada ocupante (`SMEMBERS`, não
   `SCARD` — contar não diz QUAL) e separa hold de sessão, então da próxima vez que a vaga sumir
   basta rodá-lo para ter o id do ocupante sem cartão. Vale rodar logo após um wrap-up inline: a
   janela do hold é curta e é a única ocupação legítima que nunca vira cartão.

   </details>

3. ~~**Rótulo "Reserva expirada"**~~ ✅ **2026-08-04** — os crachás de `reservedToMeExpired` e
   `overflowed` foram **removidos**, não reescritos. Primeira tentativa trocou os dois por "Aberto
   a todos"; o dono do produto apontou o erro: **a ausência de crachá já é a notação de "qualquer
   um pode pegar"** (é como `shared` é exibido), então o selo anunciava o default e competia com o
   único que carrega informação, "Reservado a você". Os nomes antigos descreviam o que acontecera
   com a RESERVA e eram lidos como "o item morreu". O estado segue no `reservationOf` — filtro e
   ordenação intactos; só a marca visual saiu.

   **E a ordenação foi junto**, pela regra geral que o dono do produto formulou: *item devolvido à
   fila preserva o timestamp original, logo é ordenado normalmente pela espera*. `first_queued_ms`
   já existe exatamente para isso. Então `rank` 0 ficou só com `reservedToMe` (dentro da janela —
   exclusivo, ninguém mais pode pegar, o topo não fura fila); expirado e transbordado voltaram à
   ordem por espera. **Achado colhido na mesma passada:** o `sort` usava `queued_at_ms` (que RESETA
   na devolução) enquanto a tela exibia idade de `first_queued_ms` (que não reseta) — a lista
   parecia ordenada pelo número mostrado e não estava. Agora desempata pelo próprio `ageOf`, o que
   torna a ordem conferível a olho.

4. ~~**Requeue carimba score NOVO no ZSET**~~ ✅ **CONSERTADO 2026-08-05 — e a descrição do item
   estava errada.** Não era "o item perde o lugar". São **duas fontes de tempo**: o aging e o
   `max_wait_exceeded` leem o JSON `contact.queued_at_ms` (que já era preservado), enquanto a posição
   publicada ao cliente (`get_queue_rank`) e a urgência de SLA do pool (`get_oldest_queue_wait_ms`)
   leem o **score do ZSET** (que reiniciava). Quem decidia o atendimento estava certo; quem o cliente
   via, não. Conserto: score = `queued_at_ms` original, que é o que o rollback do `work_task_claim`
   já fazia. Medido no mesmo item, mesma chamada: score **voltou** de `…355601` para `…433275`
   (== `first_queued`), com os outros dois itens da fila como controle na mesma saída. Detalhe em
   `CHANGELOG.md` e `docs/adr/adr-work-item-requeue-and-agent-affinity.md` § **D2b**.

   **Lição:** o discriminador `score > first_queued` do `probe_release_presence.sh` existia **por
   causa do defeito** e morreu com ele — todo item devolvido passaria a parecer virgem, e o gate do
   achado 2 viraria verde vazio sem nada ficar vermelho. Trocado por `session:{sid}:segment_seq`.
   *Instrumento que depende de um defeito precisa ser revisto no mesmo commit que o conserta.*

   *(A pendência sobre `get_queued_contacts`/ZREVRANGE que este item deixou aberta foi fechada no
   mesmo dia — ver item 6.)*

6. ~~**`get_queued_contacts` lê ZREVRANGE**~~ ✅ **CONSERTADO 2026-08-05 — e a pergunta de produto
   já estava respondida pelo código.** O item mandava decidir antes *"o score é timestamp ou
   prioridade?"*. Não era escolha: há **um único escritor** do score (`add_queued_contact`) e ele
   grava `queued_at_ms`; e prioridade **não pode** ser um score armazenado, porque
   `score_contact_in_queue` depende de `now_ms` (aging e breach crescem com a espera) — gravá-la seria
   gravar um valor que nasce velho. O `"queue_scorer may override with priority"` do docstring
   descrevia um caminho inexistente, e foi ele que autorizou a leitura invertida.

   **Eram DOIS leitores** (este item registrava um): `get_queued_contacts` (janela 10 → `Router.dequeue`,
   só pool push, consequência = **atendimento**) e `listQueue` (janela 20 → inbox pull do Console,
   consequência = **visibilidade**, e invisível de propósito: o Console ordena por idade o que
   RECEBEU, então a lista parecia ordenada e estava sem o começo). Medido em fila de 25 com previsão
   escrita antes: antes `06..25` e `16..25`; depois `01..20` e `01..10`. Detalhe em `CHANGELOG.md`;
   instrumento em `infra/test/probe_queue_window_order.sh`.

   **Fica em aberto, e é da JANELA, não do sentido:** contato de tier alto que chegue além do corte
   também não é pontuado. Ordenar pela espera é estritamente melhor (o aging é monótono no tempo de
   espera), mas não zera o efeito para `base_priority`. Não consertado de propósito — leitura integral
   tornaria o drain O(fila). Se virar requisito, o desenho é uma segunda passada por tier, não a
   remoção do limite.

   **Lição de método, guardada porque mudou como esta suíte valida:** o controle negativo por fora
   (reverter → rebuildar → rodar → restaurar) falhou **duas vezes**, das duas por pular o rebuild, e
   das duas o resultado pareceu resposta — `238 deselected` (nenhum teste selecionado, exit 0) e
   depois `2 passed` contra um container ainda consertado. Substituído por um teste **diferencial**
   que lê a mesma fila pelas duas semânticas e exige que divirjam. Ele ainda cobre o que o ritual
   manual nunca cobriria: fixture encolhida para dentro da janela faz as duas leituras coincidirem,
   os testes de sentido seguem verdes e param de discriminar — agora isso fica vermelho.

5. ~~**Janela de ordenação entre `conversations.events` e `conversations.routed`**~~ ✅ **MEDIDO
   2026-08-05 — risco caracterizado, sem conserto; VIGIAR.** Três coisas mudaram na descrição.

   **(a) A causa é maior do que "dois tópicos sem ordem entre si".** O bridge tem UM consumidor para
   os seis tópicos e despacha com `asyncio.create_task(_dispatch(...))`, sem `await` (`main.py` §9021).
   Isso **descarta a ordenação do Kafka inteira** — inclusive dentro de uma partição do mesmo tópico.
   Não há ordem entre dois eventos quaisquer, e nunca houve.

   **(b) `auto_attend` NÃO é o candidato rápido que o item afirmava.** Acelerador e cruzamento são
   mutuamente exclusivos: na MESMA aba o `refreshSignal` refaz a lista na hora após o release, mas o
   `autoAttendedRef` já tem o id (a aba não re-reivindica o que devolveu); em OUTRA aba o ref está
   vazio, mas o gatilho é o poll de 4 s. Reproduzir "pelo uso" devolveria NÃO-REPRODUZIU explicado
   pelos 4 s — inconclusivo por construção.

   **(c) O que foi medido, então, foi a JANELA** (`infra/test/probe_release_reclaim_race.sh`, 5
   rodadas, release→re-claim back-to-back num único processo para o gap ser um round-trip HTTP e não
   o custo do `docker exec`): desmonte da presença em **~30 ms** a partir do release; re-claim
   disparado a **~15 ms**; **0 engolidas** em 5/5, com 10 `Return to queue` no log como testemunha de
   que o transporte rodou. Guard **em jogo**, não isento — o claim manda `conference_id=""` e
   `work_task_claim` monta o routed com `conference_id or None` (`router.py` §816), então a condição
   `if not conference_id` (§3517) foi avaliada.

   **A margem é incidental, e não é (window − gap).** Os dois eventos atravessam o Kafka; o que
   protege é o `contact_closed` ser publicado ANTES (no início do release) enquanto o routed depende
   de todo o resto (release responder + claim ir e voltar). A margem é esse **offset de publicação**
   menos a diferença de latência dos dois handlers. Sob carga, o `create_task` pode atrasar o handler
   de `contact_closed` enquanto o de routed corre — **é aí que o risco mora, e o probe não mede isso**
   (rodada ociosa ≠ rodada sob carga).

   **Gatilhos que reabrem:** auto-claim server-side (sem round-trip de UI no meio), `pollMs` menor no
   inbox, um ref que não guarde o id, ou qualquer coisa que engorde o prólogo do handler de
   `contact_closed` até o `DEL session:{sid}:human_agent` (§6841). O conserto, se preciso, **não** é
   afrouxar o guard (trocaria um caso mudo por outro, o spam de `participant_joined` do drain): é dar
   ao routed de CLAIM um discriminador que o drain não tem.

   **Lição de instrumento:** `presence_at_reclaim` é medido quando o re-claim é DISPARADO, e o guard
   avalia quando o bridge PROCESSA o routed. O proxy **superestima** o acerto — na 1ª leitura eu
   afirmei "caiu dentro da janela 5/5" com base nele, e estava errado. Quem decide é a contagem no
   log. *Proxy medido no instante errado é um número certo respondendo outra pergunta.*

*(Medido junto, e por isso NÃO é item: no encerramento real por supervisor o árbitro devolve a vaga
e o Console derruba o cartão sozinho — `SMEMBERS` da instância vai a vazio em ≤6 s. Ver CHANGELOG
§ F2.)*

## Frente 2 — Avaliação campaign-driven — resíduos *(pipeline S1/S2.1/S2.Q1/S2.2 ✅ e lente `deploy` P2+P3 ✅; histórico no CHANGELOG)*

Avaliação é **sempre dirigida por campanha** (janela = `evaluation_calendar_id`, throttle = `avaliacao_ia.max_concurrent_sessions`).
Pipeline validado E2E com avaliador real (2026-06-17) e lente `deploy` ancorada no pool (2026-06-20).
Specs: `docs/product/arc6-phase2-deploy-observability-spec.md`, `docs/product/calendar-consolidation-and-trigger.md`.

**Diferidos por decisão do usuário (reabrir só se observabilidade por deploy/versão virar requisito)**
- **P4 (núcleo §4.1/D4)** — série por **epoch/versão**: eixo X = versões do pool (`[deploy N, deploy N+1)`), ponto = qualidade média da versão, N por versão. Hoje o eixo é tempo + `deploy_markers` (leitura de "v1 vs v2" ainda manual). Seed: `infra/test/seed_deploy_lens_demo.sh`.
- **Ruído herdado do board na lente `deploy` (§4.5/D3)** — média/multi-seleção fazem pouco sentido numa lente de versões; avaliar remover/ocultar e focar single-skill quando o epoch entrar.
- **Markers exigem `flow_id == skill_id` (§8)** — no demo `sac_ia` (agent_type_id) ≠ `skill_atendimento_sac_v1`; só alinha quando o `flow_id` carrega o skill_id real *(verificar se o re-ancoramento por pool do P3 já tornou isso irrelevante)*.
- **Capacidades perdidas com a remoção das abas Trend/Comparison** (não existem no bench): significância estatística (N<30), comparação de **períodos arbitrários A vs B**, overlay multi-métrica. Se voltarem, entram como modo "comparar fatias/deploy" no bench.
- `TimeseriesView`/`ComparisonView` continuam no repo como **código morto** (não removidos no cleanup).

**Nits do bench (diferidos, não fechados)**
- **Quality score geral diluído** — KPI "Quality score 0.00 (N evals)" do drill-down e a curva da lente `quality` saem baixos/zero enquanto o radar de dimensões está correto. Hipótese original (zero-fill por sessão) **refutada** por leitura de `analytics-api/reports_query.py`. Achado real não confirmado como causa: `_compare_quality_lens` filtra a janela pelo `timestamp` da avaliação, enquanto `_fetch_agents_cross` filtra por `attr.session_started_at` — mas a mesma divergência existe em `_compare_quality_criteria_lens` (que está correto). **Requer reprodução ao vivo com dado real** (range + Quality/N evals/Sessions do drill-down vs. a linha do mesmo agente na tabela) antes de qualquer fix.
- **Janela/período** — confirmar se KPI, lente e tabela de dimensão usam períodos diferentes no mesmo request (não confirmado); considerar default próprio do bench (hoje reusa `DEFAULT_FILTERS` de `contacts/types.ts`, 7 dias, alinhado com `_default_from`/`_default_to`).
- **NPS por agente parece alto** (pequeno).

**Contrato de avaliação / robustez**
- **Unificação do contrato prompt×schema (desenhada, não implementada)** — prompt `evaluation_rubric_v3` é fixo e deveria derivar do `EvaluationForm`; `_format_schema` do ai-gateway é **lossy** (descarta `items`/`properties`/`description`/`nullable`; `OutputFieldSchema` nem os modela); alvo = YAML `output_schema` ≡ Zod do `evaluation_submit`, permitindo **remover os shims de compat**. *(O nit específico da perda de `justification`/evidência foi fechado no T9-C.fix2.)*
- **Sessão sem dados** — avaliar sessão "magra" ainda falha duro no `evaluation_submit` (`overall_score=null` × `composite_score: number` obrigatório). Contrato escolhido: avaliador detecta sessão sem conteúdo e marca a instance `skipped`/`error` com motivo, **sem** chamar submit; pode exigir `skipped` no enum (hoje só `error`).

**Pipeline / superfícies faltantes**
- **S2.3** — dispatcher automático drenando instances `scheduled` das campanhas com `evaluation_calendar_id` aberto (calendar-api `is_open`), respeitando a capacidade do pool avaliador *(verificar sobreposição com o dispatcher windowed T15 já existente)*.
- **Surface de instances `scheduled`** — hoje Avaliações mostra só resultados; operador não tem visão da fila agendada.
- **CampaignsPage** — sem editar/deletar campanha (só create + pause/resume), embora a API já tenha `CampaignUpdate`/PUT.
- **i18n** — chaves `campaigns.seedSynthetic*` (en/pt-BR) nunca adicionadas; e rebuild do `platform-ui` para as chaves Arc 13 (`contest.*`/`review.*`) entrarem em produção *(verificar se já rebuildado)*.
- **Curation/Calibration (Arc 13 Fase H)** — telas existem mas nunca validadas com dado real; exercitar o **Fluxo 2** (curadoria → `calibration_signal` → CalibrationNote → KB), que só rodou via seeder.
- **Fila de revisão do supervisor** ("Awaiting my action", depende de `available_actions`) — confirmar se existe.

**Auth / limpeza**
- **G-PROBE, perna agente/sistema** — `submit_pre_review`, `seed/flush-synthetic`, `create/update/delete_sampling_rule`, `publish_calibration_note` seguem **header-only** (`X-Tenant-ID`/`X-User-ID`). Decisão 2026-07-01: **não** usar credencial de serviço ad-hoc; gatear por `principal_id` do **Agent Principal** (F1–F4) quando existir. Perna humana `curar` ✅ resolvida. Ver seção `## G-PROBE` própria neste arquivo.
- **G-S2.4 aposentado (decisão 2026-06-25)** — resta o *follow-up opcional* de **remoção física da cola morta**: consumer reativo `workflow.events` na evaluation-api, coluna/seletor `review_workflow_skill_id`, skills `skill_revisao_*`/`agente_revisor_v1` e o cenário e2e 28. Slice próprio (raio de teste no 28).

**Achados pré-existentes (não causados pela F1.0)**
- **A — specialist-return (pré-requisito/núcleo da F4)**: conference specialist que termina com `escalate` re-roteia o CONTATO em vez de **voltar ao chamador** (ex.: `agente_auth_form_v1.yaml` → `retencao_humano` → fila, com mensagem de fila espúria). Fix preferido: **engine** — flow em modo conference specialist trata `escalate`/`complete` como retorno-ao-chamador devolvendo outcome. Sub-arco próprio.
- **B — multi-sessão humana no push**: humano servindo entra `state="busy"` e `get_ready_instances` exige `state=="ready"` → mesmo sob capacidade (`max_concurrent=3`, vindo da URL do WS do Console — `mcp-server` server.ts:2147 — não do `auth`) não recebe 2º contato via push. Pull (F1) endereça; decisão pendente: o push também deveria manter `ready` enquanto sob capacidade? Medir ao vivo antes de atacar.

---

## Record/Replay Harness — gravação/replay em todas as costuras *(proposta — não implementado)*

Visão + spec em [`docs/product/record-replay-harness-spec.md`](docs/product/record-replay-harness-spec.md). Generaliza o Session Replayer (que hoje replaya só o stream da sessão, para avaliação) num harness "VCR" em todas as costuras (channel-gateway, AI Gateway, MCP, Kafka) — cada costura como **driver** (injeta inputs gravados) ou **mock** (devolve outputs gravados), com timings.

**Base que já existe**: `session-replayer` (persister/hydrator/replayer/comparator), `ComparisonReport` (Jaccard + deltas), `delta_ms`/`speed_factor`, Kafka como log, harness `e2e-tests`. **A construir**: captura full-fidelity de payload em MCP/AI Gateway (hoje `mcp.audit` é só metadado), clock/seed injetável (determinismo), harness multi-costura, gravação seletiva (golden/amostrada/on-demand) com masking, e o **gate de promoção** consumindo o `ComparisonReport` como critério objetivo. Aplicações: regressão determinística, repro de bug, simulação de carga, datasets de avaliação.

---

---

