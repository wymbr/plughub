# ContextStore — o root `core.*`: reserva, padronização e limpeza

**Status:** decidido — 2026-09-01 (CNS-02). **Origem:** reformulação do dono, feita
*deliberadamente sem a linguagem do* [`adr-contextstore-allowlist.md`](../adr/adr-contextstore-allowlist.md),
para separar desenho de arqueologia. Convergiu com o ADR em quatro pontos e acrescentou três.

**Componentes:** `packages/schemas` (`context-map.ts`) · `packages/sdk` (`context-store.ts`) ·
`packages/config-api` (seed) · `packages/orchestrator-bridge` · `packages/channel-gateway` ·
`packages/routing-engine` · `packages/ai-gateway` · `packages/skill-flow-engine` ·
`packages/platform-ui`

---

## 0. A decisão, em uma frase

> **O que é da plataforma vive sob `core.*`. Todo o resto do ContextStore é dos skills.**

Quatro consequências:

1. **`core.*` é reservado e semeado**; o cadastro recusa root `core` vindo de tenant.
2. **`session.*`, `journey.*` e `segment.*` ficam LIVRES** para os skills — inclusive o canal
   de processo de 30 dias, que hoje só eles usam.
3. **O namespace do core vai para inglês**, pela regra de idioma que a plataforma já tem.
4. **A rota de `core.` é DECLARADA**, nunca herdada do default.

### Por que reservar o core, e não o `session.*`

A primeira versão desta spec reservava `session.*`. O dono inverteu, e a inversão é melhor por
uma razão que se mede: **o core é pequeno, fechado e semeado (36 nomes); o espaço do tenant é
aberto.** Reservar o conjunto pequeno e liberar o aberto custa um terço do trabalho e não obriga
ninguém a enumerar a coisa grande.

| proposta | nomes que teriam de mudar |
|---|---|
| reservar `session.*` | 36 do core **+ 76 de skill** = 112 |
| **reservar `core.*`** | **36**, só os do core |

E resolve de graça uma pergunta que a versão anterior deixava aberta: o `delegate`/`collect`
compõe `session.<chave>` **no código do gateway** (`webhook.py:1693`). Sob a reserva de
`session.*` aquele código passaria a violar a própria regra a cada delegação; sob `core.*`,
`session.` é espaço de tenant e **o gateway não muda**.

---

## 1. Medições que fundamentam (2026-09-01)

Instrumento: `infra/test/censo_contextstore_cadastro.py`, importado como módulo para atribuir
escrita por arquivo; `infra/registry/tenant_demo.yaml` para o vínculo pool↔skill.

⚠️ **O vínculo saiu da DECLARAÇÃO, não do estado.** `infra/registry/*.yaml` é seed-if-absent; a
autoridade é o slot `current` de cada pool no agent-registry vivo (`CLAUDE.md`: *"para deploy,
pergunte ao agent-registry, NUNCA ao YAML"*). Os números de "em pool" são um primeiro corte
defensável, não veredicto.

### 1.1 Quem manipula o ContextStore

| | arquivos |
|---|---|
| escrevem | **18** |
| só leem | **16** |
| não tocam | **8** |
| **total** | **42** |

94 escritas; **os 5 maiores concentram 58 (62%)**: `skill_limite_processo_v1` (16),
`skill_limite_entrada_v1` (13), `agente_contexto_ia_v1` (12),
`skill_outbound_survey_dispatch_v1` (9), `agente_portabilidade_intake_v1` (8).

Os dois maiores skills de atendimento (`skill_atendimento_sac_v1`, `skill_atendimento_auth_v1`)
e o `agente_fila_v1` **não tocam** o ContextStore.

### 1.2 Inventário de skills

**42 arquivos · 33 declarados em algum pool · 9 órfãos.** Dos 9 órfãos, **7 sem citação nenhuma**
em `infra/test`, `packages/e2e-tests` ou `infra/scripts`. As duas exceções:
`skill_reembolso_demo_v1` (usado por `infra/test/gate_external_resume.sh`) e
`skill_survey_outbound_v1` (só num README).

**3 pools apontam para skill sem arquivo**: `skill_survey_v1`, `skill_survey_nps_v1`,
`skill_survey_reconnect_v1`.

### 1.3 Quem escreve o quê

| origem | nomes | grafia |
|---|---|---|
| **plataforma** | **36** | 13 já canônicos · 23 planos/legados |
| **tenant** (skills) | 61 | 48 legados · resto canônico |

### 1.4 Idioma do core

Em **português**: as pastas `contato`, `processo`, `sentimento` e quatro folhas do `copilot`.
Todo o resto já está em inglês. Caso ilustrativo: **`session.sentimento.current`** — pasta em
português, folha em inglês, no mesmo nome.

### 1.5 Como o dado atravessa sessões de um mesmo processo

Medido, porque a decisão sobre `journey.*` depende disto. **Três caminhos, e só um é canal de
dado de negócio:**

| # | mecanismo | natureza | TTL |
|---|---|---|---|
| **1** | **`journey.*`** — hash separado `{t}:ctx:journey:{raiz}` | canal **compartilhado** de processo | **30 d** |
| **2** | `delegate.context` / `collect.context` | **cópia** no nascimento da sessão filha, prefixada com `session.` no gateway | `timeout_hours + 1 h` |
| **3** | `root_session_id` / `origin_session_id` | **ponteiro** de identidade, nunca dado | — |

O caminho 1 fecha o laço na prática: `skill_limite_processo_v1` escreve
`journey.{limite_aprovado, numero_cartao, parecer, resultado}` e `skill_limite_entrada_v1` lê os
mesmos quatro, em **outra sessão** do mesmo processo.

**É por isso que `journey.*` NÃO é reservado:** o core não escreve nada lá, é o único store de
30 dias, e um root de tenant cai no hash da sessão (4 h). Reservá-lo mataria o mecanismo sem
substituto — o dado do processo evaporaria entre dois contatos.

---

## 2. D-A — o root `core.*` é reservado, com rota DECLARADA

### 2.1 A reserva

```
RESERVADO — plataforma, semeado; o cadastro recusa root `core` vindo de tenant
  core.contact.*     core.pool.*        core.channel.*
  core.workflow.*    core.queue.*       core.process.outcome
  core.survey.*      core.sentiment.*   core.copilot.*
  core.segment.{segId}.*
  core.customer.*        ← absorve `insight.historico.*` e `pricing.*`

LIVRE — skills
  session.*   (4 h)    ex.: session.card.{type,number,valid_thru,cvv}
  journey.*   (30 d)   o canal de processo, preservado inteiro
  segment.*            isolamento por agente, preservado
  qualquer outro root  (cai no hash da sessão)
```

**Mecanismo, não promessa:** o cadastro recusa `core` como root de tenant. É um predicado de uma
linha (`root == "core"`), não uma lista de pastas — e por isso não envelhece. Foi esse o ganho
que decidiu entre as duas propostas: reserva parcial exige **consulta**; reserva de root inteiro
é **regra**, e um nome se explica sozinho.

### 2.2 A rota tem de ser DECLARADA — e esta é a parte que pode falhar em silêncio

O roteamento do SDK (`context-store.ts:50-118`) é uma allowlist de prefixos com **default**:

```
insight.historico, pricing  → hash do cliente   (90 d)
journey.                    → hash da journey   (30 d)
qualquer outro prefixo      → hash da SESSÃO    ( 4 h)   ← o default
```

`core.` cairia no default e ganharia o hash da sessão — **que é exatamente onde os 36 já vivem
hoje**, logo nada muda agora. O risco é futuro e é silencioso: no dia em que o core precisar de
um fato de processo, `core.journey.x` **não** começa com `journey.`, cai no default e recebe
4 h em vez de 30 dias, sem erro em lugar nenhum.

**Por isso `core.` entra explicitamente na tabela**, apontando para o hash da sessão, em vez de
herdar o default. Uma linha em três casas. Assim quem precisar de `core` com outro escopo bate
numa tabela declarada e tem de decidir.

> É a família de defeito que o `CLAUDE.md` cataloga em *"Degradação NUNCA é silenciosa"*: um
> default que "conserta" a ausência troca falha barulhenta por mentira tranquila.

### 2.3 O eixo que a decisão sacrifica, declarado

Hoje o primeiro segmento carrega **escopo** (retenção). Com `core.*` ele passa a carregar
**propriedade**, e o escopo sai dos nomes do core. Aceito porque **os 36 nomes do core são todos
de sessão** — medido, não suposto. A guarda da §2.2 é o que impede isso de virar dívida muda.

### 2.4 `core.customer.*` — o de maior risco, e estava fora de todas as listas

`insight.historico.*` e `pricing.*` roteiam para o hash do **cliente, 90 dias**, e hoje estão
livres: um skill que escreva `insight.historico.qualquer_coisa` ganha PII com retenção
trimestral e nada impede. Onde o risco de retenção é maior, a reserva é mais urgente.

Absorvidos em `core.customer.*`, o root do core fica sendo uma frase só.

---

## 3. D-B — o namespace do core vai para inglês

Regra de idioma já existente (`CLAUDE.md` § *Language Rule*): identificador técnico é inglês;
português só em display e em **IDs de negócio nomeados pelo tenant**, que são dado. Um nome de
tag escrito por código de plataforma é código.

| atual | vira |
|---|---|
| `session.contato.*` | `core.contact.*` |
| `session.processo.outcome` | `core.process.outcome` |
| `session.sentimento.current` | `core.sentiment.current` |
| `session.copilot.acoes_recomendadas` | `core.copilot.recommended_actions` |
| `session.copilot.flags_risco` | `core.copilot.risk_flags` |
| `session.copilot.sugestao_resposta` | `core.copilot.suggested_reply` |
| `session.copilot.ultima_analise` | `core.copilot.last_analysis` |

⚠️ **O idioma do TENANT não é assunto da plataforma.** `session.cliente.*`, `session.cartao.*`,
`session.portabilidade.*` ficam como estão — são vocabulário de negócio, que é **dado**. A
versão anterior desta spec propunha traduzi-los e **retirar `portabilidade`/`reembolso` da
estrutura**; sob `core.*` isso deixa de ser decisão da plataforma. *(A recomendação de modelagem
sobrevive como conselho: `session.card.type = "credito"` envelhece melhor que
`session.cartao_credito.*`. Conselho, não regra.)*

---

## 4. D-D — o core deixa de escrever a grafia plana

23 grafias legadas morrem aqui, sem tocar em vocabulário de skill nenhum.

| família | o core grava hoje | passa a gravar |
|---|---|---|
| **contact** (7) | `session.close_origin` · `customer_participant_id` · `human_agent_participant_id` · `last_primary_agent_key` · `last_primary_segment_id` · `root_session_id` · `spawn_reason` | `core.contact.*` |
| **workflow** (8) | `delegate_resume_token` · `dialog_form_id` · `origin_session_id` · `review_decision` · `round_echoed` · `workflow_resume_token` · `current_round` · `reviewer_id` | `core.workflow.*` |
| **survey** (7) | `survey_{grain,target_id,segment_id,pool_id,agent_key}` · **o par duplicado `surveyed_*`** | `core.survey.*` |
| **process** (1) | `session.process_outcome` | `core.process.outcome` |

Ganho de lambuja: o par **`survey_*` × `surveyed_*`** — duas grafias para o mesmo fato, escritas
em serviços diferentes (bridge no fim do atendimento × gateway na pesquisa) — colapsa numa só.

E `session.reviewer_id`, hoje **não declarado**, entra como `core.workflow.reviewer_id`.
*(A classe LGPD dele continua aberta — nenhuma das cinco foi pensada para identidade de usuário
da plataforma; decisão #5 do ADR.)*

### 4.1 ⚠️ O `legado[]` NÃO cobre leitura — medido em 2026-09-01

> **Correção de uma afirmação que esta spec fez.** Ela dizia *"aliases cobrem a transição"*.
> **Falso.** `resolveContextTag` tem **um único chamador em runtime** — `classifyContextTags`,
> dentro de `observeContextTags`, que é o caminho de **AUDITORIA**, `void`-chamado e cujo retorno
> o ramo E do `probe_context_map_audit.sh` proíbe consumir. O caminho real de leitura de skill
> (`interpolate.ts:241`, `resolveCtxRef`) faz `contextStore.getValue(sessionId, tag)` — **`HGET`
> literal, sem passar pelo mapa**.
>
> O ADR não errou: a D3 diz que o alias é *"resolvido na BORDA, nas duas portas humanas"*, e é
> isso que ele faz. O alias dá continuidade de **política de máscara**, nunca de **leitura**.

**Consequência: o rename é BREAKING.** Alcance medido dos 36 nomes: **65 arquivos** — 39 de
código, **14 skills** (que os **leem**, ex. `@ctx.session.pool.id`) e 12 de teste.

**Mecanismo escolhido (CNS-11, decidido 2026-09-01): BIG BANG num commit atômico.** Sem escrita
dupla e sem alias na leitura. O que torna isso seguro é uma propriedade medida — **zero nomes
dinâmicos** —, então um `grep` dos 36 nomes é verificação **completa**, não amostra. Não há dado
em voo a proteger: TTL de 4 h, sem produção, sessão velha morre com o nome velho.

⚠️ *"Migrar leitores antes, escritores depois"* foi **recusado**: leitor novo + escritor velho =
campo inexistente = `undefined` silencioso, o mesmo buraco mais cedo.

**Os aliases FICAM no mapa** — não como migração, mas porque o snapshot durável do fechamento de
sessão guarda os nomes antigos para sempre, e é o `legado[]` que mantém esse histórico
corretamente **mascarado**.

---

## 5. Achados de superfície que este trabalho expôs

1. **`masking.context_map` não tem superfície de configuração nenhuma.** A tela
   `/config/masking` grava três chaves e o mapa não é uma delas; a única rota é
   `GET /visibility-options` (leitura); o único escritor é o `seed.py`, que é seed-if-absent —
   **editar o arquivo numa base já semeada é no-op.** Viola a invariante que a própria
   plataforma declara (*"Every config field is UI-editable"*). → **CNS-08**
2. **A tela de tipos editava uma das quatro dimensões** — `mascara.by_role` não tinha
   superfície. ✅ **Fechado em 2026-09-01 (CNS-07)**, ver `CHANGELOG.md`.
3. **`i18n/index.ts` fixa `lng: 'en'`**, sem detector e sem seletor. O pt-BR é empacotado e
   **nunca selecionado** — a metade pt-BR de toda mudança de i18n é, por construção, não
   exercitada.

---

## 6. Ordem de execução

| # | passo | id | depende de |
|---|---|---|---|
| 1 | Confirmar os skills válidos contra os slots `current` vivos; apagar os 7 órfãos; resolver os 3 pools apontando para o vazio | CNS-01 | stack de pé |
| 2 | Declarar a rota de `core.` na tabela do SDK (§2.2) e acrescentar `core.segment.` a `dynamic_prefixes` | CNS-03 | — |
| 3 | Mapa aceita roots de tenant (hoje o topo é o enum `session\|journey\|customer`; o oráculo acusaria `unknown_scope`) | CNS-04 | — |
| 4 | Reserva no cadastro: recusar root `core` de tenant | CNS-05 | §2 |
| 5 | **O commit único** — 36 renames + 65 arquivos | CNS-11 | 2, 3 |
| 6 | Superfície de configuração do mapa | CNS-08 | — |

---

## 7. Como isto pode ficar vermelho

- **D-A** — testemunha obrigatória é a **colisão**: cadastro de tenant declarando root `core`
  tem de ser **recusado nomeando**. Um teste que só verifique que `core.*` existe passa com a
  reserva desligada.
- **§2.2** — a rota declarada precisa de teste que prove que ela **não** é o default: remover a
  linha de `core.` tem de mudar algum comportamento observável, senão ela é decorativa.
- **D-B / D-D** — cada rename precisa do **par**: a grafia nova sendo escrita **e** a antiga
  parando de aparecer. Só o primeiro não distingue *"migrou"* de *"escreve as duas"*.
- **CNS-11** — a verificação é `grep` dos 36 nomes antigos em `packages/` dando **zero**, mais
  um smoke com o contador de auditoria. O `grep` só é completo porque **0 nomes são dinâmicos** —
  se essa propriedade cair, a rede cai junto.
- **Transversal** — o censo (`censo_contextstore_cadastro.py`) e o oráculo do mapa
  (`verifyContextMap`) têm de concordar no total após cada passo. Foi comparar instrumento com
  oráculo que pegou a sub-contagem de aliases em 2026-08-30.

---

## 8. Relação com o ADR da allowlist

**Comum** — quatro pontos, mesma coisa com outro nome: catálogo de tipos (D1) · estrutura em
árvore com tipo por folha (D2) · área configurada em vez de livre (D9) · Console consumindo a
estrutura (D6/D9.7).

**Que esta spec acrescenta:**

1. **A reserva do core** (D-A) — o ADR tem "duas origens" (D9.3) mas nunca disse o que impede o
   tenant de escrever onde o core escreve.
2. **O idioma** (D-B) — o ADR não trata; a regra da plataforma já obrigava.
3. **A padronização do core como trabalho PRÓPRIO** (D-D) — o ADR trata alias como migração de
   tenant; **23 dos 69 são do core**, e morrem sem tocar em vocabulário de skill.

**Que o ADR tem e esta spec assume sem repetir:** a postura publish × runtime (recusar em runtime
derruba atendimento em curso); a terceira origem de escrita (corpo HTTP do webhook, que nenhuma
análise estática alcança); e que **85% das folhas são `texto`** — "configurar o ContextStore" é
sobretudo declarar que o campo existe, e a proteção de PII é a fatia menor.

**O que esta spec RESOLVE do ADR:** a decisão aberta **#3** (lista de domínios) deixa de existir
como problema da plataforma — o vocabulário de negócio é do tenant, e o único domínio que a
plataforma fecha é o seu próprio, sob `core.`.
